"""routers/completions.py -- OpenRouter completion endpoints.

Routes:
    POST /chats/{chat_id}/complete                      - non-streaming send
    POST /chats/{chat_id}/complete/stream               - SSE send
    POST /chats/{chat_id}/messages/{id}/regenerate/stream - SSE regenerate
    POST /chats/{chat_id}/messages/{id}/edit/stream     - SSE edit + rewrite

The three SSE endpoints carry all of the abort, stale-exchange and voice
handling; the non-streaming one is the simple case. A change that touches only
`complete_chat` has almost certainly missed the part that matters - this header
used to say the opposite ("Text-only, non-streaming ... No streaming"), which
is what the next change would have been built on.

Privacy invariants:
    - API key is read via secrets_service.get_secret() (sealed in the
      encrypted vault DB, E5); never stored in a variable beyond the call site.
    - User message content, assistant response, prompt payload, and raw OpenRouter
      response are NEVER logged.
    - Only chat_id, model_id, message IDs, and gen_param keys are logged.
    - This module does NOT import httpx, requests, urllib.request, or the
      keyring package. It uses secrets_service as the approved abstraction.
    - Raw OpenRouter error bodies are never forwarded to the client.

Scope:
    - Text, plus `image_url` parts for models that accept image input
      (_model_accepts_images is the ONE rule; the attachment gate and payload
      assembly both read it, which is what stops them disagreeing).
    - No tools, tool_choice, response_format, file, reasoning.
    - No local models.
"""

import asyncio
import hashlib
import json
import logging
import threading

import uuid

import anyio.to_thread

import audio_sweep

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import (
    BaseModel, ConfigDict, Field, StrictBool, StrictStr, field_validator,
)

from config import (
    SECRET_API_KEY,
    PROVIDER_POLICY,
    CONTEXT_SAFETY_MARGIN,
    CHARS_PER_TOKEN_ESTIMATE,
    IMAGE_TOKEN_ESTIMATE,
    MAX_ATTACHMENTS_PER_MESSAGE,
)
import card_macros
import notebook_store
import prompt_layer
import prompt_sanitizer
import voice_tags
from prompt_modules import budget as pm_budget
from prompt_modules import errors as pm_errors
from prompt_modules import store as pm_store
from prompt_modules import placement as pm_place
from prompt_modules import st_regex as pm_regex
from prompt_modules import st_build as pm_st_build
from prompt_modules import variables as pm_variables
from prompt_modules.parsers import ST_SOURCES
from database import get_db
from typing import Annotated, Literal

from vault_state import VaultLockedError
from tts import stream_hook
from routers import tts_runtime
from routers.settings import MAX_STOP_SEQUENCES_READ
from secrets_service import get_secret
from attachments_service import (
    AttachmentError,
    validate_staged,
    link_attachments,
    load_for_messages,
    delete_for_messages,
    build_image_part,
    normalise_image,
    prefetch_blobs,
    store_generated_image,
)
from generated_images import (
    NOTICE_IMAGE_REJECTED,
    NOTICE_IMAGE_REMOTE_URL,
    RemoteImageURL,
    decode_data_url,
    image_output_enabled,
)
import openrouter
import safeword_filter
from openrouter import (
    NOTICE_FRAME_DROPPED,
    NOTICE_STREAM_UNFINISHED,
    TRUNCATED_FINISH_REASONS,
    OpenRouterError,
    MODALITIES_WITH_IMAGE,
    finish_reasons,
    image_urls_from,
    plan_private_params,
    validate_and_filter_gen_params,
    get_cached_model_metadata,
    complete,
    complete_stream,
    model_id_blocked,
    reasoning_param,
    THINKING_DEFAULT,
)
from proxy_health import enforce_proxy_gate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chats", tags=["chats"])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CHAR_PER_TOKEN = CHARS_PER_TOKEN_ESTIMATE  # see config.py rationale
_DEFAULT_CONTEXT_LEN = 32000
_DEFAULT_MAX_TOKENS = 2048
# Flat per-image budget cost, expressed in "estimate chars" so the existing
# char-based trim math keeps working unchanged.
_IMAGE_CHAR_COST = IMAGE_TOKEN_ESTIMATE * _CHAR_PER_TOKEN
# v1.1 audit L6: the stream-abort cleanup writes run SYNCHRONOUSLY on the event
# loop (awaiting inside GeneratorExit handling is fragile). A short busy_timeout
# bounds how long a contended write lock can stall the loop; past it the write
# fails fast, the partial is dropped, and the abort handler logs and re-raises.
_ABORT_DB_BUSY_TIMEOUT_MS = 800
#: The ordinary ceiling, matching database.get_db's own default. Named here so
#: the salvage path can pick between the two by which caller it has, instead of
#: repeating the number.
_DB_BUSY_TIMEOUT_MS = 15000


# ---------------------------------------------------------------------------
# Pydantic request models
# ---------------------------------------------------------------------------

class GenerationParams(BaseModel):
    model_config = ConfigDict(extra="ignore")  # silently drop unknown fields

    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    min_p: float | None = None
    top_a: float | None = None
    frequency_penalty: float | None = None
    presence_penalty: float | None = None
    repetition_penalty: float | None = None
    max_tokens: int | None = None
    seed: int | None = None
    # The only list on this body, and the last one on the live streaming path
    # whose entries are WALKED: `validate_and_filter_gen_params` visits every
    # one, and `_prepare_completion` calls it straight on the event loop with
    # no thread hop, from six routes. So an oversized list does not merely cost
    # the request that sent it. It holds the loop and stalls every reply
    # already streaming in this process. Measured through the route, with a
    # 25 MB body of five million entries, as the longest stretch the loop went
    # without control: 0.139s, against 0.016s for the same request carrying no
    # stop list. With this ceiling the body answers 422 and that falls to
    # 0.086s, all of it reading and JSON-parsing the body before any schema
    # runs. That remainder is the BYTE dimension, which no entry ceiling
    # removes and which a body-size limit elsewhere would. (The settings route
    # quotes a larger figure for a walk of the same length and does not
    # contradict this: its loop trims and de-duplicates every entry, this one
    # compares two things per entry.)
    #
    # REFUSED here, not clamped, which is the opposite of what the settings
    # route does with the same list, because the two protect different things.
    # There the clamp protects a SAVE: a stale dialog sending a few entries too
    # many must not lose the whole settings write to a 422. Here nothing is
    # stored, so a refusal costs one retry and no state; there is no clamp on
    # this path to preserve, the list being forwarded as sent; and dropping
    # entries quietly would change where the model stops writing, which is a
    # wrong answer nobody is told about instead of a refusal somebody can see.
    # The refusal lands before the stream opens, because the body is validated
    # before the handler runs, so it is a plain 422 and never a broken stream.
    #
    # The ceiling is the settings route's own READ ceiling rather than a new
    # number, because this list IS that list: the panel merges the saved stop
    # sequences into the request. Nothing any version of it sends comes near.
    #
    # `Annotated` on the list arm rather than `Field(max_length=...)` on the
    # field: a bare max_length would cap the STRING arm too, in CHARACTERS,
    # and one stop sequence is allowed a hundred of those.
    #
    # The walk itself is not moved off the loop, because at this ceiling it is
    # microseconds and a thread hop would cost more than it saves.
    stop: str | Annotated[
        list[str], Field(max_length=MAX_STOP_SEQUENCES_READ)
    ] | None = None
    # D3, the "Thinking" switch. Not a sampler and never forwarded under this
    # name: validate_and_filter_gen_params drops it (it is not on the numeric
    # allow-list), and _prepare_completion reads it only to choose the
    # `reasoning` field. Strict, so "true" as a string is a 422, not a yes.
    thinking: StrictBool = False
    # The thinking choice for the selected model: "off", "default" or an
    # effort level (openrouter.THINKING_CHOICES). Like `thinking`, never
    # forwarded under its own name; reasoning_param checks it against the
    # model's listed levels, and a word it does not know, or a level the
    # model does not list, gives the request it got before this field
    # existed. Absent means exactly that too.
    thinking_effort: StrictStr | None = Field(default=None, max_length=16)

    @field_validator(
        "temperature", "top_p", "top_k", "min_p", "top_a",
        "frequency_penalty", "presence_penalty", "repetition_penalty",
        "max_tokens", "seed",
        mode="before",
    )
    @classmethod
    def _reject_non_numeric(cls, v):
        """Reject string and bool values for numeric fields."""
        if v is None:
            return v
        if isinstance(v, bool) or isinstance(v, str):
            raise ValueError("must be a number, not string or bool.")
        return v

    @field_validator("top_k", "max_tokens", "seed", mode="before")
    @classmethod
    def _reject_fractional_float(cls, v):
        """Reject fractional floats for integer params (e.g. 1.9 -> 422)."""
        if v is None:
            return v
        if isinstance(v, float) and not v.is_integer():
            raise ValueError("must be a real integer, not a fractional float.")
        return v


class ProviderPolicy(BaseModel):
    model_config = ConfigDict(extra="ignore")  # silently drop unknown fields (D1)

    require_parameters: bool | None = None
    # zdr, data_collection, allow_fallbacks are locked in PROVIDER_POLICY.
    # They are NOT accepted from the frontend (extra="ignore" drops them), in
    # either direction: a body that asks for less privacy and a body that asks
    # for no failover are both ignored.


class SpeakOptions(BaseModel):
    """Whether this reply should be SPOKEN while it is written, and how fast.

    Off by default and on every request type: the user's rule was that voice
    changes nothing until they ask for it. `speak_rate` is clamped server-side
    (tts/speed.py) - a client is not trusted to keep the dial inside the range
    where the time-stretch still sounds like speech.
    """
    model_config = ConfigDict(extra="ignore")

    speak: bool = False
    speak_rate: float | None = None
    # How *asterisk narration* is voiced: same tone, a narrator tone, or not
    # read at all. Validated here rather than trusted: an unknown value would
    # reach speech_prep and raise inside an SSE generator, costing the reply.
    speak_narrative: Literal["same", "narrator", "skip"] = "same"


class CompleteRequest(SpeakOptions):
    model_config = ConfigDict(extra="ignore")

    message: str
    model_id: str
    generation_params: GenerationParams = Field(default_factory=GenerationParams)
    provider: ProviderPolicy = Field(default_factory=ProviderPolicy)
    persona_id: int | None = Field(
        default=None,
        description="Optional persona override. Must exist in personas table. "
                    "If null, uses selected_persona_id from settings."
    )
    attachments: list[int] = Field(
        default_factory=list,
        description="Staged upload ids to attach to this user message. "
                    "Validated in the handler (max count, existence, staging, "
                    "model image support) so errors surface as stable string "
                    "codes rather than 422 validation arrays.",
    )
    context_budget_tokens: int | None = Field(
        default=None,
        ge=512,
        le=2_000_000,
        description="App-level context budget in tokens. "
                    "Effective budget = min(this, model.context_length). "
                    "Output + safety margin reserved before history trim. "
                    "NOT forwarded to OpenRouter under any name."
    )

    @field_validator("message")
    @classmethod
    def message_must_not_be_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("message_required")
        return v  # preserve original; stripped only for OpenRouter payload

    @field_validator("model_id")
    @classmethod
    def model_id_must_not_be_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("model_id_required")
        return v.strip()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _col(row, name: str):
    """One column of a sqlite Row or a plain dict; None when it is absent."""
    try:
        return row[name]
    except (KeyError, IndexError, TypeError):
        return None


class _CardRow(dict):
    """A character row with every card column present (None when absent)."""

    _KEYS = ("name", "system_prompt", "description", "personality",
             "scenario", "mes_example", "post_history_instruction")

    def __init__(self, row) -> None:
        super().__init__({k: _col(row, k) for k in self._KEYS})


def _build_system_block(char_row, user_name: str = card_macros.DEFAULT_USER_NAME) -> str:
    """The labelled card block (modes a and c), without the final note.

    K-31. The character's NAME reaches the model as a LABEL, the persona
    header's twin, not as "You are {name}.": a header states the fact and
    leaves the framing to whoever wrote the card. A blank name gives no
    header at all, so a hand-edited vault never produces "[Character: ]".

    The text is what is SENT: macros rendered with the sanitized card name,
    then the sanitizer (prompt_sanitizer), then trimmed. The stored card is
    untouched.
    """
    card = prompt_layer.card_text(_CardRow(char_row), user_name)
    card.final_note = ""
    return prompt_layer.labelled_card_block(card)


def _build_persona_block(persona_row) -> str:
    """Render the user-persona system block (v1.1 KUME D).

    Same `[Label]\\n{value}` convention as the character block so the model
    sees one consistent sectioning scheme. A name-only persona still injects
    the header - the name alone is meaningful, and this was the reported bug
    (the persona NAME never reached the model before). Frontend parity:
    lib/context/estimateContextUsage.ts buildPersonaBlock must match this
    char-for-char or the context gauge undercounts by 16 + len(name).

    Privacy: the description is never logged (personas.py invariant).
    """
    name = (persona_row["display_name"] or "").strip()
    desc = (persona_row["description"] or "").strip()
    if not name:
        # The schema forbids blank names; this defensive branch only protects
        # hand-edited DBs. Never emit a nameless header.
        return desc
    if desc:
        return f"[User Persona: {name}]\n{desc}"
    return f"[User Persona: {name}]"


def _model_accepts_images(meta: dict | None) -> bool:
    """Whether a payload for this model may carry image parts.

    ONE rule, shared by the request gate (_validate_request_attachments) and
    payload assembly (_prepare_completion). Refuse only when cached metadata
    POSITIVELY says the model has no image input; unknown or empty metadata is
    allowed through, because the provider is the final arbiter.

    Deriving this twice is what made the attachment gate accept an image that
    assembly then silently dropped: with an empty _model_cache (fresh start, or
    any settings change calling invalidate_model_cache) the gate said "let the
    provider decide" while assembly read the same absent metadata as "no image
    support" and stripped every image from the payload - no error, no SSE
    event, no log, and the user billed for a reply about an image the model
    never received.
    """
    mods = (meta or {}).get("input_modalities") or []
    return not (meta is not None and mods and "image" not in mods)


def _model_emits_images(meta: dict | None) -> bool:
    """Whether this model can RETURN a picture, so we may ask it to.

    The mirror of _model_accepts_images, and it follows the same two rules for
    the same reasons: derived in exactly one place, and permissive when the
    metadata is unknown because the provider is the final arbiter. The
    postmortem above is about image INPUT, but nothing in it is specific to
    direction - a gate that says yes while assembly says no costs the user a
    request either way.

    Gated on output_modalities, which openrouter.py already parses and caches
    for every model, and NOT on supported_parameters: no model on OpenRouter
    lists `modalities` there, so reading that would disable the feature for
    everything.
    """
    mods = (meta or {}).get("output_modalities") or []
    return not (meta is not None and mods and "image" not in mods)


#: A generated picture is shown to the reader and never replayed to the model.
#:
#: This is a rule about WHOSE images may become provider content parts, and it
#: has to exist because nothing else can enforce it: `attachments` has no role
#: column and no constraint (database.py), `load_for_messages` contains no
#: mention of role, and `_content_for` has no role parameter. The docstring at
#: _content_for asserts "user messages only" as if that were enforced; it was a
#: comment. So the moment an assistant row owned an attachment, the very next
#: turn would have shipped {"role":"assistant","content":[text, image_url]}.
#:
#: Two costs, one of them silent. A provider that rejects an assistant image
#: part answers 400, which this app maps to a 502 one turn LATE with the body
#: unlogged - nothing in the logs would point at images. A provider that accepts
#: it charges for re-uploading the same picture on every subsequent turn, and
#: `_entry_chars` charges 3300 budget chars for it, so the context trim starts
#: evicting REAL history to make room.
#:
#: This is also what every comparable app converged on independently: SillyTavern
#: hides generated images from the prompt by default, and LibreChat shipped the
#: bug and fixed it by displaying without re-sending. An opt-in "let the
#: character see what it drew" is a separate feature with a real token cost, and
#: it would have to gate on input_modalities, not output_modalities.
_IMAGE_REPLAY_ROLES = frozenset({"user"})


def _replays_images(role: str) -> bool:
    """Whose attachments may become provider image parts. See _IMAGE_REPLAY_ROLES."""
    return role in _IMAGE_REPLAY_ROLES


def _entry_chars(text: str, attachments: list[dict] | None,
                 include_images: bool = True, role: str = "user") -> int:
    """Budget length of one message: text chars + flat per-image cost.

    include_images and role both mirror payload assembly: images the payload
    will not carry cost nothing, or the trim would drop real history to make
    room for bytes that are never sent.
    """
    if not include_images or not _replays_images(role):
        return len(text)
    return len(text) + len(attachments or []) * _IMAGE_CHAR_COST


def _content_for(text: str, attachments: list[dict] | None,
                 include_images: bool,
                 image_blobs: dict[str, bytes],
                 omitted: list[int] | None = None,
                 role: str = "user") -> str | list[dict]:
    """Plain string content, or OpenRouter content parts when images ride along.

    Image parts are emitted only for a role whose images are replayable (see
    _IMAGE_REPLAY_ROLES - in practice the user's own uploads, never a picture
    the model produced) AND only when the model accepts image input; for
    text-only models the images are silently omitted (documented in the
    contract) so old multimodal history never breaks a text model.

    image_blobs is the PREFETCHED sha->bytes map (built off the event loop in
    _prepare_completion); assembly itself never touches the DB.
    """
    if not include_images or not attachments or not _replays_images(role):
        return text
    parts: list[dict] = [{"type": "text", "text": text}]
    for row in attachments:
        part = build_image_part(row, image_blobs, omitted)
        if part is not None:
            parts.append(part)
    return parts if len(parts) > 1 else text


def _assemble_messages(
    system_block: str,
    persona_block: str,
    history: list[dict],
    user_message: str,
    post_history_instruction: str,
    context_budget_chars: int,
    max_tokens_chars: int,
    include_images: bool = False,
    pending_attachments: list[dict] | None = None,
    image_blobs: dict[str, bytes] | None = None,
    voice_block: str = "",
    omitted_images: list[int] | None = None,
    # Three blocks, three different authorities, and they default to empty:
    #   boundary_block      - standing rules; counted, never dropped
    #   notebook_user_block - what the person wrote; system message, by persona
    #   notebook_model_block- what the model wrote; the tail (or the end of
    #                         the system message, prompt_layer switch)
    boundary_block: str = "",
    notebook_user_block: str = "",
    notebook_model_block: str = "",
    # Out-param, same shape as omitted_images: the trim loop counts what it
    # drops so the turn can say so instead of dropping history in silence.
    trimmed_out: list[int] | None = None,
    *,
    # Mode b's <reminder>: the last thing in the tail.
    reminder_block: str = "",
    # Every ENABLED module as dicts of id, name, content (the text as sent)
    # and `sent` (whether its text is inside `system_block` this turn). H4
    # counts them all, worst case (dF 5.1); the trim counts the sent ones.
    modules: list[dict] | None = None,
    # A SillyTavern preset's layout (prompt_modules.placement); None keeps
    # the one system message below, byte for byte.
    layout: pm_place.Layout | None = None,
    # The model refuses a trailing assistant message (a prefill).
    refuses_prefill: bool = False,
) -> list[dict]:
    """ONE system message, the history, and the user message with the tail.

    `system_block` is the head of the system message (salt, modules or the
    layer, the card block); `post_history_instruction` is the card's final
    note, placed right after it under its label (empty in mode b, where it
    sits in the layer's author slot). Then persona, Limits, the user's notes
    and voice. Parts are separated by one blank line.

    The tail (model notes, then mode b's <reminder>) is folded into the LAST
    user message after the user's own text, which is never altered; a message
    carrying images gets it as its own final text part. Several system
    messages broke some chat templates (Qwen 3.5+, pre-V7 Mistral) and one
    (gpt-oss) silently dropped every system message after the first, Limits
    included (PK-04).

    Every part is counted BEFORE the trim. Refusals, in order:
      boundaries_do_not_fit       the limits alone cannot fit
      context_too_large           everything but the modules cannot fit
      prompt_modules_over_budget  H4: the modules would take more than half
                                  of what the rest leaves for history
    """
    if layout is not None:
        return _assemble_st_messages(
            layout, persona_block, history, user_message,
            post_history_instruction, context_budget_chars, max_tokens_chars,
            include_images=include_images,
            pending_attachments=pending_attachments, image_blobs=image_blobs,
            voice_block=voice_block, omitted_images=omitted_images,
            boundary_block=boundary_block,
            notebook_user_block=notebook_user_block,
            notebook_model_block=notebook_model_block,
            trimmed_out=trimmed_out, modules=modules,
            refuses_prefill=refuses_prefill)
    system_text, tail = _system_and_tail(
        system_block, post_history_instruction, persona_block, boundary_block,
        notebook_user_block, voice_block, notebook_model_block, reminder_block)

    available = context_budget_chars - max_tokens_chars
    # BEFORE the trim, and this ordering is the whole of it: `fixed_chars` is
    # what the loop below subtracts from `available` to decide how much
    # history survives. A part added after this sum would be sent anyway and
    # the trim would keep history there is no room for.
    user_msg_chars = (_entry_chars(user_message, pending_attachments,
                                   include_images)
                      + prompt_layer.tail_chars(tail))
    fixed_chars = len(system_text) + user_msg_chars
    module_chars = pm_budget.module_addition_chars(
        [m["content"] for m in modules or [] if m.get("sent", True)])

    if fixed_chars - module_chars > available:
        # The limits are the one block that refuses instead of shrinking. If
        # they do not fit, generating anyway would mean speaking WITHOUT rules
        # the user believes are in force - and nothing would say so.
        if boundary_block and len(boundary_block) + user_msg_chars > available:
            raise HTTPException(400, "boundaries_do_not_fit")
        # The card alone overflowing keeps its old name: a module is never
        # blamed for a card that does not fit without any module.
        raise HTTPException(400, "context_too_large")
    if modules:
        _check_module_budget(modules, available, fixed_chars - module_chars)
    if fixed_chars > available:
        raise HTTPException(400, "context_too_large")

    # Trim history from oldest end until it fits
    remaining = available - fixed_chars
    history_chars = sum(
        _entry_chars(m["content"], m.get("attachments"), include_images,
                     role=m["role"])
        for m in history
    )
    # The count exists because the silence did: history has always been
    # trimmed oldest-first, and a notice for the notebook alone would teach
    # that dropped context gets announced.
    trimmed = 0
    while history_chars > remaining and history:
        dropped = history.pop(0)
        trimmed += 1
        history_chars -= _entry_chars(
            dropped["content"], dropped.get("attachments"), include_images,
            role=dropped["role"],
        )

    if trimmed_out is not None:
        trimmed_out.append(trimmed)

    blobs = image_blobs or {}
    messages: list[dict] = []
    if system_text:
        messages.append({"role": "system", "content": system_text})

    for msg in history:
        messages.append({
            "role": msg["role"],
            "content": _content_for(
                msg["content"], msg.get("attachments"), include_images, blobs,
                omitted_images, role=msg["role"],
            ),
        })

    # The user's own text first and untouched; the tail after it.
    messages.append({
        "role": "user",
        "content": prompt_layer.fold_tail(_content_for(
            user_message.strip(), pending_attachments, include_images, blobs,
            omitted_images,
        ), tail),
    })
    return messages


def _system_and_tail(system_block: str, post_history_instruction: str,
                     persona_block: str, boundary_block: str,
                     notebook_user_block: str, voice_block: str,
                     notebook_model_block: str,
                     reminder_block: str) -> tuple[str, list[str]]:
    """The one system message and the tail, from their parts. The ONE place
    the request is shaped: the assembly and the toggle-time H4 check
    (module_budget_check) both measure what this returns.

    The model's notes ride in the user channel, after the user's text, with
    their own "unverified notes" header (C3 a; owner, 26 Sep 2026, which
    replaces KARAR-V2-13's system-role placement). In the system message
    they would come back every turn with the authority of the app.
    """
    final_note = prompt_layer.final_note_part(
        prompt_layer.strip_space(post_history_instruction or ""))
    system_text = prompt_layer.PART_SEP.join(p for p in (
        system_block, final_note, persona_block, boundary_block,
        notebook_user_block, voice_block) if p)
    tail = [t for t in (notebook_model_block, reminder_block) if t]
    return system_text, tail


def _st_parts(layout: pm_place.Layout, persona_block: str,
              boundary_block: str, notebook_user_block: str, voice_block: str,
              notebook_model_block: str):
    """_system_and_tail for a SillyTavern layout: (head, post, tail).

    The app's own parts go at the end of the head: the persona (unless the
    preset has a persona placeholder), Limits, the user's notes and voice.
    The card's post-history text is not among them: it takes the preset's
    Post-History slot or, as in SillyTavern, is not sent. The model's notes
    stay in the tail of the user's message. Consecutive system parts are
    joined the way SillyTavern squashes them. The assembly and the
    toggle-time budget check both measure what this returns."""
    app: list[str] = []
    if persona_block and not layout.persona_placed:
        app.append(persona_block)
    app += [p for p in (boundary_block, notebook_user_block, voice_block) if p]
    head = pm_place.merge_system(
        [*layout.head, *(pm_place.Part(pm_place.ROLE_SYSTEM, t, pm_place.SOURCE_APP)
                         for t in app)])
    post = pm_place.merge_system(layout.post)
    tail = [notebook_model_block] if notebook_model_block else []
    return head, post, tail


def _st_fixed_chars(head: list, post: list, layout: pm_place.Layout) -> int:
    """Everything a SillyTavern layout sends besides the chat. An in-chat
    message counts one separator more than its text: it can end up joined to
    a system part next to it, and a count may be high but never low."""
    sep = len(pm_place.SYSTEM_JOIN)
    inj = sum(len(p.text) + sep
              for _depth, msgs in pm_place.injection_groups(layout.injections)
              for p in msgs)
    new_chat = len(layout.new_chat.text) if layout.new_chat is not None else 0
    return (sum(len(p.text) for p in head) + sum(len(p.text) for p in post)
            + inj + new_chat)


def _assemble_st_messages(
    layout: pm_place.Layout,
    persona_block: str,
    history: list[dict],
    user_message: str,
    post_history_instruction: str,
    context_budget_chars: int,
    max_tokens_chars: int,
    *,
    include_images: bool,
    pending_attachments: list[dict] | None,
    image_blobs: dict[str, bytes] | None,
    voice_block: str,
    omitted_images: list[int] | None,
    boundary_block: str,
    notebook_user_block: str,
    notebook_model_block: str,
    trimmed_out: list[int] | None,
    modules: list[dict] | None,
    refuses_prefill: bool,
) -> list[dict]:
    """_assemble_messages for a SillyTavern layout (prompt_modules.placement).

    The same budget, the same refusals in the same order, the same trim. What
    differs is the shape: the head as its own messages, the chat with the
    in-chat parts inside it, then the parts listed after the chat. In-chat
    parts are counted as fixed and never trimmed; the trim drops chat
    messages only, oldest first, and a part deeper than what is left lands
    before the oldest message kept.
    """
    head, post, tail = _st_parts(
        layout, persona_block, boundary_block, notebook_user_block,
        voice_block, notebook_model_block)
    available = context_budget_chars - max_tokens_chars
    user_msg_chars = (_entry_chars(user_message, pending_attachments,
                                   include_images)
                      + prompt_layer.tail_chars(tail))
    fixed_chars = _st_fixed_chars(head, post, layout) + user_msg_chars
    module_chars = pm_budget.module_addition_chars(
        [m["content"] for m in modules or [] if m.get("sent", True)])

    if fixed_chars - module_chars > available:
        if boundary_block and len(boundary_block) + user_msg_chars > available:
            raise HTTPException(400, "boundaries_do_not_fit")
        raise HTTPException(400, "context_too_large")
    if modules:
        _check_module_budget(modules, available, fixed_chars - module_chars,
                             chat_variables=layout.has_variables)
    if fixed_chars > available:
        raise HTTPException(400, "context_too_large")

    remaining = available - fixed_chars
    history_chars = sum(
        _entry_chars(m["content"], m.get("attachments"), include_images,
                     role=m["role"])
        for m in history
    )
    trimmed = 0
    while history_chars > remaining and history:
        dropped = history.pop(0)
        trimmed += 1
        history_chars -= _entry_chars(
            dropped["content"], dropped.get("attachments"), include_images,
            role=dropped["role"],
        )
    if trimmed_out is not None:
        trimmed_out.append(trimmed)

    blobs = image_blobs or {}
    chat = [pm_place.Part(msg["role"], _content_for(
                msg["content"], msg.get("attachments"), include_images, blobs,
                omitted_images, role=msg["role"]), pm_place.SOURCE_HISTORY)
            for msg in history]
    chat.append(pm_place.Part("user", prompt_layer.fold_tail(_content_for(
        user_message.strip(), pending_attachments, include_images, blobs,
        omitted_images,
    ), tail), pm_place.SOURCE_USER))
    parts = [*head, *([layout.new_chat] if layout.new_chat else []),
             *pm_place.place_in_chat(chat, layout.injections), *post]
    last = parts[-1]
    if (refuses_prefill and last.role == pm_place.ROLE_ASSISTANT
            and last.source not in (pm_place.SOURCE_HISTORY, pm_place.SOURCE_USER)):
        # A trailing assistant part is a prefill, which this model refuses.
        parts[-1] = pm_place.Part(pm_place.ROLE_USER, last.text, last.source,
                                  last.module_id)
    parts = pm_place.merge_system(parts)
    return [{"role": p.role, "content": p.text,
             **({"name": p.name} if p.name else {})} for p in parts
            if p.text or p.source in (pm_place.SOURCE_HISTORY,
                                      pm_place.SOURCE_USER)]


def _check_module_budget(modules: list[dict], available: int,
                         fixed_chars: int, *, chat_variables: bool = False) -> None:
    """H4 with prompt_modules.budget, the rule's one definition. A refusal is
    prompt_modules.errors.PromptModuleError(OVER_BUDGET) carrying each
    module's id, name and token cost; main.py answers it with the same JSON
    body the prompt-modules routes use, so one client parser reads both."""
    try:
        pm_budget.check(modules, available_chars=available,
                        fixed_chars=fixed_chars)
    except pm_errors.PromptModuleError as exc:
        if exc.code != pm_errors.OVER_BUDGET:
            # The card alone not fitting is checked before this call and
            # keeps its own name; nothing else is raised by the rule.
            raise HTTPException(400, "context_too_large") from None
        if chat_variables:
            # Macro variables can grow a module turn after turn; clearing
            # them is the other way out, and the refusal says so.
            exc.extra["chat_variables"] = True
        raise


def module_budget_check(con, chat_id: int | None, modules: list[dict],
                        available_chars: int, *,
                        user_message_chars: int = 0) -> None:
    """H4 at TOGGLE time, counted exactly as a request counts it (A5).

    `modules` are the modules that would be on (dicts with id, name and the
    STORED content). They are prepared the way the request prepares them
    (macros with the chat's card and persona names, then the sanitizer), and
    the non-module cost is the same single system message and tail the
    request builds: salt, card block and final note, persona, Limits, notes,
    voice. Without a chat, a card of REFERENCE_CARD_CHARS characters stands
    in (dF 4). The user's next message is not known yet and counts as
    `user_message_chars`.

    Raises PromptModuleError (over budget), HTTPException 404 for a missing
    chat or card, and prompt_sanitizer.PromptFieldTooLarge /
    card_macros.MacroOutputTooLarge (both code "prompt_field_too_large").
    """
    from prompt_modules.limits import REFERENCE_CARD_CHARS

    char_row: dict
    if chat_id is not None:
        chat = con.execute("SELECT character_id FROM chats WHERE id = ?",
                           (chat_id,)).fetchone()
        if chat is None:
            raise HTTPException(404, "chat_not_found")
        row = con.execute(
            "SELECT name, system_prompt, description, personality, scenario, "
            "mes_example, post_history_instruction FROM characters WHERE id = ?",
            (chat["character_id"],)).fetchone()
        if row is None:
            raise HTTPException(404, "character_not_found")
        char_row = _CardRow(row)
    else:
        char_row = _CardRow({"name": "", "description": "x" * REFERENCE_CARD_CHARS})

    persona_row = None
    sel = con.execute("SELECT value FROM settings WHERE key = ?",
                      ("selected_persona_id",)).fetchone()
    if sel and (sel["value"] or "").strip().isdigit():
        persona_row = con.execute(
            "SELECT id, display_name, description FROM personas WHERE id = ?",
            (int(sel["value"]),)).fetchone()
    user_name = card_macros.DEFAULT_USER_NAME
    if persona_row is not None:
        user_name = (persona_row["display_name"] or "").strip() or user_name

    card = prompt_layer.card_text(char_row, user_name)
    if any(m.get("source_type") in ST_SOURCES for m in modules):
        _st_budget_check(con, chat_id, modules, available_chars, char_row,
                         card, user_name, persona_row, user_message_chars)
        return
    prepared = []
    for m in modules:
        texts = prompt_layer.module_texts([m], card.name, user_name)
        if texts:
            prepared.append({"id": m.get("id"), "name": m["name"],
                             "content": texts[0], "sent": True})
    head = prompt_layer.build_head(
        chat_id=chat_id or 0, card=card, user_name=user_name,
        modules_enabled=bool(modules), modules=[p["content"] for p in prepared],
        layer_enabled=False, length_words=None)
    if chat_id is not None:
        nb = notebook_store.build_notebook_blocks(chat_id, available_chars)
    else:
        nb = {"boundary_block": "", "user_block": "", "model_block": ""}
    system_text, tail = _system_and_tail(
        head.text, head.final_note,
        _build_persona_block(persona_row) if persona_row is not None else "",
        nb["boundary_block"], nb["user_block"], voice_tags.voice_block(),
        nb["model_block"], head.reminder)
    fixed = (len(system_text) + prompt_layer.tail_chars(tail)
             + user_message_chars - head.module_chars)
    if fixed > available_chars:
        raise HTTPException(400, "context_too_large")
    if prepared:
        _check_module_budget(prepared, available_chars, fixed)


def _st_budget_check(con, chat_id: int | None, modules: list[dict],
                     available_chars: int, char_row, card, user_name: str,
                     persona_row, user_message_chars: int) -> None:
    """module_budget_check for a SillyTavern preset: the layout a request
    would build (prompt_modules.st_build), measured by the same two functions
    (_st_parts, _st_fixed_chars). Every module counts as firing (worst
    case). Macros run on a copy of the chat's variables, so a toggle never
    changes them."""
    import_ids = {m.get("import_id") for m in modules if m.get("import_id") is not None}
    built = pm_st_build.build(
        salt=prompt_layer.chat_salt(chat_id or 0), char_row=char_row,
        persona_row=persona_row, user_name=user_name,
        modules_enabled=modules, modules_sent=modules,
        markers=pm_store.list_markers(con, import_ids),
        variables=(pm_variables.pre_turn(con, chat_id, None)
                   if chat_id is not None else {}),
        preset_texts=pm_store.preset_texts(con, _preset_import(modules)))
    layout = built.layout
    persona_block = (_build_persona_block(persona_row)
                     if persona_row is not None else "")
    if chat_id is not None:
        nb = notebook_store.build_notebook_blocks(chat_id, available_chars)
    else:
        nb = {"boundary_block": "", "user_block": "", "model_block": ""}
    head, post, tail = _st_parts(
        layout, persona_block, nb["boundary_block"], nb["user_block"],
        voice_tags.voice_block(), nb["model_block"])
    fixed = (_st_fixed_chars(head, post, layout) + prompt_layer.tail_chars(tail)
             + user_message_chars
             - pm_budget.module_addition_chars([m["content"] for m in built.modules]))
    if fixed > available_chars:
        raise HTTPException(400, "context_too_large")
    if built.modules:
        _check_module_budget(built.modules, available_chars, fixed,
                             chat_variables=layout.has_variables)


def _preset_import(modules: list[dict]) -> int | None:
    """The import whose preset texts (new-chat lines, formats) are sent: the
    first SillyTavern module's, in stored order."""
    return next((m.get("import_id") for m in modules
                 if m.get("source_type") in ST_SOURCES), None)


def _reasoning_is_optional(model_id: str) -> bool:
    """Whether the Thinking switch applies to this model (D3): its reasoning
    can be turned off, which is exactly when reasoning_param answers the
    disable. One definition, the same one the request uses."""
    return reasoning_param(model_id) == {"enabled": False}


async def _private_endpoints_for(model_id: str) -> list[dict] | None:
    """This model's zero-data-retention endpoints, [] for none, None unknown.

    Through the module attribute, so the one lookup is the one a test can
    stand in for.
    """
    index = await openrouter.zdr_index()
    if index is None:
        return None
    return openrouter.zdr_endpoints_in(index, model_id)


def _build_provider_dict(req_provider: ProviderPolicy) -> dict:
    """Build the provider dict from config defaults + request overrides.

    zdr, data_collection, and allow_fallbacks are locked to PROVIDER_POLICY
    values and cannot be overridden by the client.
    Only require_parameters may be overridden.
    """
    provider_dict = dict(PROVIDER_POLICY)  # copy, not reference
    if req_provider.require_parameters is not None:
        provider_dict["require_parameters"] = req_provider.require_parameters
    return provider_dict


# Shared with chats.py since v1.1 FB6 - one response shape, one guard.
from messages_common import msg_to_dict as _msg_to_dict, last_active_anchor


def _validate_request_attachments(ids: list[int], model_id: str) -> list[dict]:
    """Validate staged attachment ids for a send. Returns their rows.

    Stable error codes: too_many_attachments, attachment_not_found,
    attachment_unavailable, model_no_image_input. The model gate only fires
    when metadata is cached AND lists input_modalities without "image" -
    unknown metadata is allowed through (the provider is the final arbiter).
    """
    if not ids:
        return []
    if len(ids) > MAX_ATTACHMENTS_PER_MESSAGE:
        raise HTTPException(400, "too_many_attachments")
    if len(set(ids)) != len(ids):
        raise HTTPException(400, "attachment_unavailable")

    meta = get_cached_model_metadata(model_id)
    if not _model_accepts_images(meta):
        raise HTTPException(400, "model_no_image_input")

    try:
        return validate_staged(ids)
    except AttachmentError as exc:
        status = 404 if exc.reason == "attachment_not_found" else 400
        raise HTTPException(status, exc.reason)


# ---------------------------------------------------------------------------
# Error code mapping (OpenRouterError.reason -> HTTP status + detail)
# ---------------------------------------------------------------------------

_ERROR_MAP: dict[str, tuple[int, str]] = {
    "openrouter_auth_failed":                (401, "auth_failed"),
    # 403 rather than 401: the key is fine, the message is not. Sharing 401 with
    # auth_failed is exactly the confusion this code was split out to end.
    "openrouter_moderation_blocked":         (403, "openrouter_moderation_blocked"),
    "api_key_not_set":                       (401, "api_key_missing"),
    "openrouter_insufficient_credits":       (402, "openrouter_insufficient_credits"),
    "openrouter_rate_limited":               (429, "openrouter_rate_limited"),
    "openrouter_no_provider_meets_privacy":  (503, "openrouter_no_provider_meets_privacy"),
    "proxy_auth_failed":                     (502, "proxy_auth_failed"),
    "network_error":                         (502, "network_error"),
    "openrouter_server_error":               (502, "openrouter_completion_error"),
    "openrouter_error":                      (502, "openrouter_completion_error"),
    "openrouter_timeout":                    (504, "openrouter_timeout"),
    # The passing kind (429 aside, which keeps its own sentence): a provider
    # that was down, overloaded or timed out at the gateway, or a connection
    # that dropped. Chosen by `_provider_failure` from the exception's
    # `retryable` flag, never from the provider's prose.
    "openrouter_temporarily_unavailable":    (502, "openrouter_temporarily_unavailable"),
    # A reply that came back empty with finish_reason "length": the budget
    # went on thinking. Retrying does not help; a larger budget does.
    "reasoning_used_reply_budget":           (502, "reasoning_used_reply_budget"),
    # A reply that came back empty for no stated reason. Retried once.
    "openrouter_empty_reply":                (502, "openrouter_completion_error"),
    "model_web_search_blocked":              (400, "model_web_search_blocked"),
    "model_id_carries_api_key":              (400, "model_id_carries_api_key"),
}

#: Every detail this module can relay from a provider failure, as a set.
#:
#: Derived from the map above rather than typed out again, so the two cannot
#: disagree. `openrouter_completion_error` is added by hand because it is also
#: the fallback for a reason the map has never heard of, and a fallback that
#: appears in no alphabet is exactly the code nobody writes a sentence for.
#:
#: This exists so `tests/error_enumeration.py` can read the vocabulary of the
#: two sites that build a detail from a variable (the raise below and the SSE
#: error event in the streaming generator). Before it, twelve relayed codes
#: across this file and models_router.py had never been counted by anything.
RELAY_DETAILS: frozenset[str] = (
    frozenset(detail for _, detail in _ERROR_MAP.values())
    | {"openrouter_completion_error"}
)

#: Refused before sending: the model has no zero-data-retention endpoint, or
#: none that takes the keys every chat request must carry (Q-121).
MODEL_NO_PRIVATE_ENDPOINT = "model_no_private_endpoint"

#: Reasons raised in THIS module for a reply that arrived empty.
REASONING_BUDGET_REASON = "reasoning_used_reply_budget"
EMPTY_REPLY_REASON = "openrouter_empty_reply"

# ---------------------------------------------------------------------------
# One automatic retry, before anything reached the reader
# ---------------------------------------------------------------------------
#
# OpenRouter already fails over between providers before the first token, so
# a failure that reaches this app has usually been tried more than once over
# there. A second request from here covers the passing burst (a 429 with a
# short Retry-After, a provider that restarted, a connection that dropped
# before any output) and nothing else. A timeout is never retried: the reader
# has already waited it out once, and a second attempt could double the wait. Two attempts in total: every attempt is a separately billed
# generation, and there is no idempotency key. The frontend and the HTTP client
# do not retry (httpx transports default to zero retries and none is set), so
# this is the only layer that does.

#: Attempts per send, the first included.
MAX_PROVIDER_ATTEMPTS = 2
#: A Retry-After longer than this is not waited out: the reader is told to wait
#: instead of watching a spinner. Also the ceiling on any single wait.
RETRY_AFTER_CAP_S = 4.0
#: The wait when the provider named none.
RETRY_BACKOFF_S = 0.75


async def _retry_sleep(seconds: float) -> None:
    """The wait between attempts. A seam for tests, nothing more."""
    await asyncio.sleep(seconds)


def _may_retry(exc: OpenRouterError, attempt: int,
               private_endpoints: int | None) -> bool:
    """Whether attempt number `attempt` may be followed by one more.

    The CALLER owns the other half of the rule: nothing may have reached the
    reader yet. This judges only the failure.
    - the cap: MAX_PROVIDER_ATTEMPTS in total;
    - only failures the raise site marked `retryable` (RETRYABLE_STATUSES, a
      dropped connection, an empty reply). A 404 "no endpoints", a
      400/401/402/403/413, a context overflow, any timeout (the first-token
      one included) and an empty reply cut off by "length" are never marked;
    - a 503 only when the preflight counted at least one private endpoint that
      takes this request: with none it is the same answer every time;
    - a Retry-After above RETRY_AFTER_CAP_S is honoured by NOT retrying early.
    """
    if attempt >= MAX_PROVIDER_ATTEMPTS:
        return False
    if not getattr(exc, "retryable", False):
        return False
    if getattr(exc, "status", None) == 503 and not private_endpoints:
        return False
    retry_after = getattr(exc, "retry_after", None)
    if retry_after is not None and retry_after > RETRY_AFTER_CAP_S:
        return False
    return True


def _retry_delay(exc: OpenRouterError) -> float:
    """Seconds to wait before the next attempt."""
    retry_after = getattr(exc, "retry_after", None)
    if retry_after is None:
        return RETRY_BACKOFF_S
    return min(max(0.0, retry_after), RETRY_AFTER_CAP_S)


def _provider_failure(exc: OpenRouterError) -> tuple[int, str]:
    """The (status, code) a provider failure reports, from its facts.

    Two kinds the reader has to tell apart (owner, item 5):
    - NEVER for this model as it stands: an HTTP 404 from the completion
      endpoint is OpenRouter's empty candidate pool (no endpoint passed the
      privacy and parameter filters), the same answer every time. It gets the
      privacy sentence, which says to pick another model.
    - TEMPORARY: anything the raise site marked retryable, once the one retry
      is spent or was not allowed. 429 and timeouts keep their own sentences.
    Everything else reads the reason table as before.
    """
    reason = exc.reason
    if getattr(exc, "status", None) == 404:
        reason = "openrouter_no_provider_meets_privacy"
    elif (getattr(exc, "retryable", False)
          and reason not in ("openrouter_rate_limited", "openrouter_timeout",
                             EMPTY_REPLY_REASON)):
        reason = "openrouter_temporarily_unavailable"
    return _ERROR_MAP.get(reason, (502, "openrouter_completion_error"))


# ---------------------------------------------------------------------------
# Internal: shared provider-call logic (used by complete and regenerate)
# ---------------------------------------------------------------------------

def _load_completion_context(
    chat_id: int, history_before_id: int | None, persona_id: int | None,
    generation: str = "send",
) -> dict:
    """Every DB read one completion needs, in ONE worker-thread hop.

    These ran one at a time ON THE EVENT LOOP - three separate connections,
    each able to queue behind a held write lock for the full busy_timeout. A
    send that arrives while a chat delete is committing therefore stalled every
    OTHER live SSE stream in the process, because none of them could be resumed
    until this coroutine yielded. The work is unchanged; only the thread it runs
    on is.

    404s that already preceded the async proxy gate are raised here so their
    precedence is preserved. The persona 404 is RETURNED instead: it fires
    AFTER the gate today, and moving it earlier would change which error a
    request with both problems reports.
    """
    with get_db() as con:
        chat_row = con.execute(
            "SELECT id, character_id, model_id FROM chats WHERE id = ?",
            (chat_id,),
        ).fetchone()
        if chat_row is None:
            raise HTTPException(404, "chat_not_found")

        char_row = con.execute(
            "SELECT id, name, system_prompt, description, personality, "
            "scenario, first_mes, mes_example, post_history_instruction "
            "FROM characters WHERE id = ?",
            (chat_row["character_id"],),
        ).fetchone()
        if char_row is None:
            raise HTTPException(404, "character_not_found")

        # active = 1 only: inactive variant siblings must never reach the
        # provider payload nor eat the context budget in the trim loop.
        if history_before_id is None:
            history_rows = con.execute(
                "SELECT id, role, content FROM messages "
                "WHERE chat_id = ? AND active = 1 ORDER BY id ASC",
                (chat_id,),
            ).fetchall()
        else:
            history_rows = con.execute(
                "SELECT id, role, content FROM messages "
                "WHERE chat_id = ? AND id < ? AND active = 1 ORDER BY id ASC",
                (chat_id, history_before_id),
            ).fetchall()

        # The greeting is the chat's oldest row when it is the assistant's
        # (chats.py seeds it first). It is card text and goes out sanitized.
        first = con.execute(
            "SELECT id, role FROM messages WHERE chat_id = ? ORDER BY id ASC LIMIT 1",
            (chat_id,),
        ).fetchone()
        greeting_id = first["id"] if first and first["role"] == "assistant" else None

        # Prompt modules and the layer settings, on the same connection.
        # `modules_enabled` is every module that is on (the mode and H4 count
        # them all, worst case); `modules_sent` is what fires for this kind of
        # generation.
        modules_enabled = pm_store.list_enabled(con)
        modules_sent = pm_store.list_enabled(con, generation)
        layer_enabled = pm_store.layer_enabled(con)
        reply_length_words = pm_store.reply_length_words(con)
        # A SillyTavern preset switched on builds the prompt the way
        # SillyTavern does (_build_prompt). Its placeholders, the chat's
        # macro variables as they stood before this turn, and the history
        # cleanup scripts are read here, on the same connection.
        st_active = any(m["source_type"] in ST_SOURCES for m in modules_enabled)
        markers = pm_store.list_markers(con) if st_active else []
        module_vars = (pm_variables.pre_turn(con, chat_id, history_before_id)
                       if st_active else None)
        cleanup_scripts = pm_store.cleanup_scripts(con) if st_active else []
        preset_texts = (pm_store.preset_texts(con, _preset_import(modules_enabled))
                        if st_active else {})

    history_att = load_for_messages([r["id"] for r in history_rows])
    history = [
        {
            "id": r["id"],
            "role": r["role"],
            "content": r["content"],
            "attachments": history_att.get(r["id"], []),
        }
        for r in history_rows
    ]

    api_key_present = bool(get_secret(SECRET_API_KEY))

    # FB7: the setting read AND the persona SELECT run on ONE connection, so a
    # concurrent delete_persona (which clears the setting and deletes the row
    # in its own txn) cannot be half-observed across two connections.
    resolved_persona_id = persona_id
    from_settings = False
    persona_row = None
    with get_db() as con:
        if resolved_persona_id is None:
            sel_row = con.execute(
                "SELECT value FROM settings WHERE key = ?",
                ("selected_persona_id",),
            ).fetchone()
            sel = sel_row["value"] if sel_row else None
            if sel:
                try:
                    resolved_persona_id = int(sel)
                    from_settings = True
                except ValueError:
                    # Corrupted setting must not 500 the request; treat as unset.
                    logger.warning(
                        "Ignoring non-integer selected_persona_id setting.",
                    )
                    resolved_persona_id = None
        if resolved_persona_id is not None:
            persona_row = con.execute(
                "SELECT id, display_name, description FROM personas WHERE id = ?",
                (resolved_persona_id,),
            ).fetchone()

    return {
        "char_row": char_row,
        "history": history,
        "api_key_present": api_key_present,
        "persona_row": persona_row,
        "resolved_persona_id": resolved_persona_id,
        "persona_from_settings": from_settings,
        "greeting_id": greeting_id,
        "modules_enabled": modules_enabled,
        "modules_sent": modules_sent,
        "layer_enabled": layer_enabled,
        "reply_length_words": reply_length_words,
        "st_active": st_active,
        "markers": markers,
        "module_vars": module_vars,
        "cleanup_scripts": cleanup_scripts,
        "preset_texts": preset_texts,
    }


def _build_prompt(chat_id: int, ctx: dict, persona_row,
                  user_text: str | None = None) -> dict:
    """Card, modules and greeting as SENT, and the head of the system message.

    Runs in a worker thread: macros and the sanitizer are linear but a 1 MiB
    field still costs hundreds of milliseconds (synthesis 4.4), and every live
    SSE stream would wait for it on the loop. Macros first, sanitizer second:
    a macro can assemble a marker ("[Lim{{//x}}its") that the sanitizer must
    then see. The stored rows are never changed.
    """
    user_name = card_macros.DEFAULT_USER_NAME
    if persona_row is not None:
        user_name = (persona_row["display_name"] or "").strip() or user_name
    if ctx.get("st_active"):
        return _build_st_prompt(chat_id, ctx, persona_row, user_name, user_text)
    try:
        card = prompt_layer.card_text(_CardRow(ctx["char_row"]), user_name)
        sent_ids = {m["id"] for m in ctx["modules_sent"]}
        modules = []
        for m in ctx["modules_enabled"]:
            texts = prompt_layer.module_texts([m], card.name, user_name)
            if texts:
                modules.append({"id": m["id"], "name": m["name"],
                                "content": texts[0], "sent": m["id"] in sent_ids})
        for msg in ctx["history"]:
            if msg.get("id") == ctx["greeting_id"] and msg["role"] == "assistant":
                msg["content"] = prompt_sanitizer.sanitize_field(msg["content"])
    except (card_macros.MacroOutputTooLarge, prompt_sanitizer.PromptFieldTooLarge):
        raise HTTPException(400, "prompt_field_too_large") from None
    head = prompt_layer.build_head(
        chat_id=chat_id, card=card, user_name=user_name,
        modules_enabled=bool(ctx["modules_enabled"]),
        modules=[m["content"] for m in modules if m["sent"]],
        layer_enabled=ctx["layer_enabled"],
        length_words=ctx["reply_length_words"],
    )
    return {"head": head, "modules": modules, "layout": None,
            "user_text": user_text, "variables": None}


def _build_st_prompt(chat_id: int, ctx: dict, persona_row, user_name: str,
                     user_text: str | None) -> dict:
    """_build_prompt for a SillyTavern preset: the layout instead of one
    system message (prompt_modules.st_build), the macro variables this turn
    leaves behind, and the history cleanup on the outgoing copy of the
    chat."""
    try:
        built = pm_st_build.build(
            salt=prompt_layer.chat_salt(chat_id), char_row=_CardRow(ctx["char_row"]),
            persona_row=persona_row, user_name=user_name,
            modules_enabled=ctx["modules_enabled"],
            modules_sent=ctx["modules_sent"], markers=ctx["markers"],
            variables=ctx["module_vars"], preset_texts=ctx["preset_texts"])
        for msg in ctx["history"]:
            if msg.get("id") == ctx["greeting_id"] and msg["role"] == "assistant":
                msg["content"] = prompt_sanitizer.sanitize_field(msg["content"])
    except (card_macros.MacroOutputTooLarge, prompt_sanitizer.PromptFieldTooLarge):
        raise HTTPException(400, "prompt_field_too_large") from None
    scripts = pm_regex.prepare(ctx["cleanup_scripts"])
    if scripts and user_text is not None:
        # The history dicts are this request's own copies; the stored rows
        # are never written from here.
        user_text = pm_regex.clean_chat(ctx["history"], user_text, scripts,
                                        char=built.card_name, user=user_name)
    head = prompt_layer.Head(prompt_layer.MODE_MODULES, "", "", "", 0)
    return {"head": head, "modules": built.modules, "layout": built.layout,
            "user_text": user_text, "variables": built.variables}


async def _prepare_completion(
    chat_id: int,
    model_id: str,
    user_message_text: str,
    generation_params: GenerationParams,
    provider: ProviderPolicy,
    persona_id: int | None,
    context_budget_tokens: int | None,
    history_before_id: int | None = None,
    pending_attachments: list[dict] | None = None,
    generation: str = "send",
) -> tuple[list[dict], dict, dict, list[dict]]:
    """Build everything needed for a provider call, without calling it.

    Handles:
      - fetch chat + character + history from DB
      - check API key
      - proxy health gate
      - persona resolution & injection
      - context budget computation & history trimming
      - generation parameter validation & filtering
      - provider privacy policy hardcoding

    history_before_id: when set, only messages with id < history_before_id are
    used as history. The regenerate flow passes the preceding user message's id
    so that neither that user message (re-appended as user_message_text) nor
    the assistant message being regenerated leaks into the history - otherwise
    the user turn would appear twice in the payload.

    Returns (messages, filtered_gen_params, provider_dict, notices).

    `notices` are SSE-shaped events for things the caller must not hide: today
    the images that were left out of the payload. Empty on the ordinary path.
    Raises HTTPException on any failure.
    """
    # ── Every DB read, once, OFF the event loop ───────────────────────────
    ctx = await anyio.to_thread.run_sync(
        _load_completion_context, chat_id, history_before_id, persona_id,
        generation,
    )
    history = ctx["history"]
    persona_row = ctx["persona_row"]
    resolved_persona_id = ctx["resolved_persona_id"]
    from_settings = ctx["persona_from_settings"]

    # ── The safeword ──────────────────────────────────────────────────────
    #
    # Before the key check, before the proxy gate, before anything is
    # assembled. It is the only control in this feature that IS a control:
    # every limit the notebook carries is a paragraph in a prompt, which is a
    # request, and this is a match in code that stops the request from
    # existing. Nothing is sent - not the message, not the notebook, not the
    # limits - and nothing is stored.
    if await anyio.to_thread.run_sync(
            notebook_store.safeword_in, user_message_text):
        raise HTTPException(400, "safeword_triggered")

    # C9, at the same point and for the same reason: a typed message holding
    # the stored API key is refused whole, before it is assembled, sent or
    # stored (the stream route writes the user turn after this returns). The
    # key everywhere else in the body is cut by the scrub in openrouter.
    if await anyio.to_thread.run_sync(safeword_filter.api_key_in,
                                      user_message_text):
        raise HTTPException(400, "api_key_in_message")

    # The model id is checked for the key too. No log line names a model id
    # any more, but the id still goes to the provider as the model name, where
    # the scrub does not reach, so it is refused here, before the stream
    # routes store the user turn. Not for the safeword: a model id is a
    # protocol value, and "exit" in `vendor/exit-model` is not a person
    # saying stop (test_safeword.py, test_the_model_id_is_never_cut).
    if await anyio.to_thread.run_sync(safeword_filter.api_key_in, model_id):
        raise HTTPException(400, "model_id_carries_api_key")

    # ── Check API key ─────────────────────────────────────────────────────
    if not ctx["api_key_present"]:
        raise HTTPException(401, "api_key_missing")

    # C4: a model id that makes the provider search the web is refused here,
    # before the stream route stores the user turn. openrouter.complete and
    # complete_stream refuse it too, for every other sender.
    if model_id_blocked(model_id):
        raise HTTPException(400, "model_web_search_blocked")

    # ── Proxy health check ────────────────────────────────────────────────
    await enforce_proxy_gate()

    # ── Q-121: the model's PRIVATE endpoints ─────────────────────────────
    #
    # After the proxy gate, because it may be an outbound GET, and before
    # anything is assembled or written, because a model no private endpoint
    # serves is refused here. None means the list is unknown (never fetched
    # and the fetch failed): the send then goes out filtered the old way.
    private_endpoints = await _private_endpoints_for(model_id)

    # ── Assemble messages ─────────────────────────────────────────────────
    model_id_stripped = model_id  # already stripped by validator

    # ── Resolve persona ───────────────────────────────────────────────────
    persona_block = ""
    if resolved_persona_id is not None and persona_row is None:
        if from_settings:
            # FB7: a selection pointing at a deleted persona must not fail
            # every completion - run without a persona instead. Only the
            # numeric id is logged (no PII).
            logger.warning(
                "selected_persona_id %d no longer exists; "
                "continuing without persona.",
                resolved_persona_id,
            )
        else:
            # An explicit body.persona_id that does not exist is a real error.
            raise HTTPException(404, "persona_not_found")

    if persona_row is not None:
        persona_block = _build_persona_block(persona_row)

    # ── Context budget from model metadata ────────────────────────────────
    meta = get_cached_model_metadata(model_id_stripped)
    model_ctx = _DEFAULT_CONTEXT_LEN
    meta_max_tokens = _DEFAULT_MAX_TOKENS
    if meta:
        if meta.get("context_length"):
            model_ctx = meta["context_length"]
        if meta.get("max_completion_tokens"):
            meta_max_tokens = meta["max_completion_tokens"]

    user_budget = context_budget_tokens
    if user_budget is not None:
        effective_tokens = min(user_budget, model_ctx) if model_ctx > 0 else user_budget
    else:
        effective_tokens = model_ctx or _DEFAULT_CONTEXT_LEN

    # ── Validate and filter gen_params ─────────────────────────────────────
    #
    # BEFORE the arithmetic below, which is the whole of this half. The check
    # used to sit eighty lines further down, so an out-of-range `max_tokens`
    # was multiplied into a character budget first: `-5000` gives
    # `max_tokens_chars = -15000`, the comparison two lines on is then false,
    # `available` GROWS by fifteen thousand characters, the notebook ceiling
    # inflates with it, and exclusion reasons computed against a ceiling that
    # never existed were written to the vault. Then the request was refused.
    # The turn never happened and it left a trace anyway.
    #
    # The bounds are `openrouter`'s (`max_tokens: (int, 1, 131072)`) and are
    # read from there rather than repeated here. Pydantic `Field(ge=, le=)` on
    # the request model would also have caught it, and is NOT used: this app
    # returns its own error CODE rather than FastAPI's validation array, which
    # the model's own comment a few hundred lines up explains, and a `Field`
    # constraint would have changed `invalid_gen_params` into a shape three
    # frontend tests, the error catalogue and the frontend contract all
    # describe. Moving the existing check is the same guarantee with no
    # contract change at all.
    #
    # The one `Field` constraint that model does carry, the stop-list ceiling,
    # is not an exception to that. It refuses a shape no client sends, before
    # this call runs at all, so there is no `invalid_gen_params` answer of its
    # own for a validation array to replace.
    try:
        filtered_gen_params = validate_and_filter_gen_params(
            generation_params.model_dump(exclude_none=True)
        )
    except ValueError:
        raise HTTPException(422, "invalid_gen_params")

    # Refused before the arithmetic below and before the notebook exclusions
    # are written: a turn that cannot be sent must leave no trace. Only the
    # REQUIRED keys decide this (optional ones are dropped further down).
    if private_endpoints is not None and plan_private_params(
            private_endpoints, filtered_gen_params)[2] == 0:
        raise HTTPException(400, MODEL_NO_PRIVATE_ENDPOINT)

    req_max_tokens = generation_params.max_tokens
    max_tokens_val = req_max_tokens if req_max_tokens else meta_max_tokens
    safety = min(CONTEXT_SAFETY_MARGIN, effective_tokens // 8)
    context_budget_chars = max(0, effective_tokens - safety) * _CHAR_PER_TOKEN
    max_tokens_chars = max_tokens_val * _CHAR_PER_TOKEN
    if max_tokens_chars > context_budget_chars:
        max_tokens_chars = max(0, context_budget_chars // 2)

    # SAME rule the attachment gate applied - see _model_accepts_images.
    include_images = _model_accepts_images(meta)

    # E6: prefetch every needed blob in ONE query OFF the event loop -
    # per-image DB reads during assembly would stall live SSE streams.
    # Newest-first order so the RAM cap keeps the most recent images when a
    # pathological history exceeds IMAGE_PAYLOAD_MAX_TOTAL_BYTES.
    image_blobs: dict[str, bytes] = {}
    if include_images:
        shas_newest_first = [a["sha256"] for a in (pending_attachments or [])]
        for msg in reversed(history):
            # Only the roles whose images are actually replayed. Decrypting a
            # generated picture here would spend ~2.3ms/MB of AES on bytes the
            # role gate in _content_for is about to discard.
            if not _replays_images(msg["role"]):
                continue
            for att in msg.get("attachments") or []:
                shas_newest_first.append(att["sha256"])
        if shas_newest_first:
            image_blobs = await anyio.to_thread.run_sync(
                prefetch_blobs, shas_newest_first
            )

    # P4: an image the model never saw is not a detail. build_image_part has
    # collected these all along and the list went no further than a log line,
    # so a completion answered from a payload with a picture missing looked
    # exactly like one that had it. This is the wire it was missing.
    omitted_images: list[int] = []
    trimmed_out: list[int] = []

    # FAZ 2. Off the loop, one hop, like every other disk read here. `available`
    # is what the ceiling is a fraction of, so it is computed the same way the
    # assembler does - a block sized against a different number than the one it
    # is budgeted into is the shape of a silent overrun.
    notebook = await anyio.to_thread.run_sync(
        lambda: notebook_store.build_notebook_blocks(
            chat_id, context_budget_chars - max_tokens_chars))
    # Off the event loop: a cold capability cache walks the models folder,
    # and one slow disk must not stall every in-flight request (audit-2).
    voice_block = await anyio.to_thread.run_sync(voice_tags.voice_block)

    # D3: thinking off where the model allows it, the lowest effort and no
    # returned text where it does not; nothing when the catalogue says
    # nothing. The Thinking switch only reaches models whose reasoning is
    # optional (the ones that would otherwise get the disable). A mandatory or
    # unknown model keeps its default above whatever the switch says.
    # The per-model choice, when the request carries one, decides; without
    # it the old switch reaches optional reasoning only, as before.
    effort = generation_params.thinking_effort
    if (effort is None and generation_params.thinking
            and _reasoning_is_optional(model_id_stripped)):
        effort = THINKING_DEFAULT
    reasoning = reasoning_param(model_id_stripped, effort=effort)
    # Whether a trailing assistant part may go out as a prefill. Thinking
    # counts as on when this request asks for it.
    refuses_prefill = pm_place.model_refuses_prefill(
        model_id_stripped,
        thinking=bool(reasoning) and reasoning.get("enabled") is not False)
    turn_vars: list = []

    def _assemble() -> list[dict]:
        prompt = _build_prompt(chat_id, ctx, persona_row, user_message_text)
        head = prompt["head"]
        turn_vars.append(prompt["variables"])
        return _assemble_messages(
            head.text,
            persona_block,
            history,
            prompt["user_text"],
            head.final_note,
            context_budget_chars,
            max_tokens_chars,
            include_images=include_images,
            pending_attachments=pending_attachments,
            image_blobs=image_blobs,
            voice_block=voice_block,
            omitted_images=omitted_images,
            boundary_block=notebook["boundary_block"],
            notebook_user_block=notebook["user_block"],
            notebook_model_block=notebook["model_block"],
            trimmed_out=trimmed_out,
            reminder_block=head.reminder,
            modules=prompt["modules"],
            layout=prompt["layout"],
            refuses_prefill=refuses_prefill,
        )

    # The whole assembly, sanitizer included, in one worker-thread hop.
    messages = await anyio.to_thread.run_sync(_assemble)

    # AFTER the assembly, because the assembly can still refuse the turn -
    # `boundaries_do_not_fit` and `context_too_large` both raise out of it.
    # Written before, the reasons for a turn that never happened were left in
    # the vault, and the panel showed notes as "not sent this turn" for a turn
    # nobody had.
    #
    # Still UNCONDITIONAL. Guarded on `excluded` being non-empty, the CLEARING
    # half never ran on a turn where nothing was excluded - so once the
    # pressure stopped, rows kept a reason from an earlier turn forever and the
    # panel showed them as "not sent" while they were being sent every single
    # time. The badge inverted its own meaning, which is worse than not having
    # it. Moving the call must not become adding a guard to it.
    await anyio.to_thread.run_sync(
        lambda: notebook_store.record_exclusions(
            chat_id, notebook["excluded"]))

    # Both numbers, together, and the second one is the older debt. History has
    # always been trimmed oldest-first with nothing recording it; shipping a
    # count for the notebook alone would teach that dropped context gets
    # announced, which would then be false for the bigger case.
    context_notes = {
        "notebook_sent": notebook["sent"],
        "notebook_total": notebook["total"],
        "history_trimmed": trimmed_out[0] if trimmed_out else 0,
    }

    stop_dropped = False
    reasoning_dropped = False
    eligible: int | None = None
    if private_endpoints is not None:
        # Q-121: every key, `reasoning` included (D3: "only when the model's
        # ZDR endpoints list it"), is judged against the endpoints the request
        # can actually land on. What is kept is accepted TOGETHER by at least
        # one of them, so require_parameters cannot empty the pool.
        if reasoning is not None:
            filtered_gen_params["reasoning"] = reasoning
        filtered_gen_params, dropped, eligible = plan_private_params(
            private_endpoints, filtered_gen_params)
        stop_dropped = "stop" in dropped
        reasoning_dropped = "reasoning" in dropped
    else:
        # The list is unknown. Today's rule, unchanged: the model-level list,
        # `stop` always kept, reasoning added after the samplers.
        logger.info("Private endpoint list unknown; parameters filtered by "
                    "the model-level list.")
        if meta and meta.get("supported_parameters"):
            supported = set(meta["supported_parameters"])
            filtered_gen_params = {
                k: v for k, v in filtered_gen_params.items()
                if k in supported or k == "stop"
            }
        if reasoning is not None:
            filtered_gen_params["reasoning"] = reasoning

    # Keep the outgoing max_tokens consistent with the (possibly reduced)
    # output reservation, so the provider cannot generate past the space
    # the history trim actually left for it.
    if "max_tokens" in filtered_gen_params:
        reserved_tokens = max(1, max_tokens_chars // _CHAR_PER_TOKEN)
        if filtered_gen_params["max_tokens"] > reserved_tokens:
            filtered_gen_params["max_tokens"] = reserved_tokens

    # ── Build provider dict ───────────────────────────────────────────────
    provider_dict = _build_provider_dict(provider)

    notices: list[dict] = []
    if stop_dropped:
        # Never silent: a reply that runs past where the user's stop sequence
        # would have cut it looks like the model ignoring them.
        notices.append({"type": "notice", "code": "stop_not_sent_privately"})
    if reasoning_dropped:
        # Same rule: without the field the provider picks the thinking, so
        # "Off" or a chosen level did not apply to this reply.
        notices.append({"type": "notice",
                        "code": "reasoning_not_sent_privately"})
    if omitted_images:
        logger.warning(
            "Completion payload omitted %d image(s): chat_id=%d",
            len(omitted_images), chat_id,
        )
        notices.append({
            "type": "notice",
            "code": "images_omitted",
            "count": len(omitted_images),
        })

    # ── May the model answer with a picture? ───────────────────────────────
    # Two independent yeses, and the setting is off until somebody turns it on.
    # `_model_emits_images` is permissive when the catalogue is not cached, so
    # the setting is what actually decides for a fresh vault - which is the
    # correct way round: an unknown model should not silently opt a user in.
    modalities = (
        MODALITIES_WITH_IMAGE
        if await anyio.to_thread.run_sync(image_output_enabled)
        and _model_emits_images(meta)
        else None
    )

    # `module_vars`: the macro variables this turn leaves behind, as the
    # snapshot text the reply's insert stores with it (None: nothing to keep).
    return (messages, filtered_gen_params, provider_dict, notices,
            modalities, context_notes,
            {"private_endpoints": eligible,
             "module_vars": pm_variables.dump(turn_vars[0] if turn_vars else None)})


def _with_refusals(notices: list[dict], refused: int) -> list[dict]:
    """Append the "a picture was dropped" notice, if any were.

    Same reasoning as images_omitted: a reply answered from a picture that never
    made it must not look identical to one that had it.
    """
    if refused:
        notices = [*notices, {"type": "notice", "code": NOTICE_IMAGE_REJECTED,
                              "count": refused}]
    return notices


def _store_generated(con, images: list[tuple[bytes, str, int, int]] | None,
                     message_id: int, chat_id: int) -> tuple[list[dict], int]:
    """Write generated images onto `message_id`, on the CALLER'S transaction.

    Shared by all three finalizers so a picture cannot be committed one way on
    one path and another way on another. A guard doing its job (bomb, oversize,
    unreadable, per-message cap) is counted and reported, never raised: it must
    not cost the reader the text of a reply that already arrived.
    """
    rows: list[dict] = []
    refused = 0
    for data in (images or []):
        try:
            rows.append(store_generated_image(con, data, message_id))
        except AttachmentError as exc:
            refused += 1
            logger.warning("Generated image not stored: chat_id=%d reason=%s",
                           chat_id, exc.reason)
    return rows, refused


def _decode_generated_images(
    urls: list[str], chat_id: int,
) -> tuple[list[tuple[bytes, str, int, int]], list[dict]]:
    """`data:` URLs to NORMALISED images. A refusal is a notice, never an error.

    Runs on a worker thread, and does all of the expensive work there: the
    base64 decode, the Pillow decode, the downscale and the re-encode. The
    transaction that stores the result then only writes bytes it was handed,
    which is what keeps Pillow out from under the SQLite writer lock - the shape
    save_upload has always had.

    Stops at MAX_ATTACHMENTS_PER_MESSAGE. Nothing upstream bounds how many
    pictures a provider may return, and the surplus used to be decoded in full -
    LANCZOS and all - before a COUNT rejected it. Twenty solid 5000x5000 PNGs
    are 84 KB each on the wire and cost seconds of pure CPU, so the cap has to
    bite before the work, not after.

    Losing a picture must not lose the words that came with it, so nothing in
    here can fail the turn. The refusals are counted separately because they mean
    different things to the person reading: "that model hands us links and we do
    not follow links" is a fact about the model they chose; "those bytes were not
    a usable image" is a fact about one reply; "there were more than we keep" is
    a fact about neither.

    Nothing is fetched. An https:// URL is a second egress host, and this app has
    exactly one - so the answer is no, permanently, not "no unless configured".
    """
    out: list[tuple[bytes, str, int, int]] = []
    notices: list[dict] = []
    #: sha256 of the FINAL bytes of everything kept. openrouter deduplicates the
    #: urls it sees, which handles the provider's documented shape - but the sink
    #: is a plain list append, so the only way to make "one picture, one row" a
    #: property of the RESULT rather than of one caller's discipline is to check
    #: it here as well, on content rather than on url text. Two different urls
    #: that decode to the same picture are also one picture.
    seen: set[str] = set()
    remote = rejected = surplus = 0
    for url in urls:
        if len(out) >= MAX_ATTACHMENTS_PER_MESSAGE:
            surplus += 1
            continue
        try:
            prepared = normalise_image(decode_data_url(url))
            digest = hashlib.sha256(prepared[0]).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            out.append(prepared)
        except RemoteImageURL as exc:
            remote += 1
            # NOT exc: RemoteImageURL carries the scheme text the model put
            # before the first colon of its own "url", so exc IS a slice of
            # the reply - the exact "looks harmless" shape the vault-lock
            # promise exists to close. The type name says a link was refused
            # without repeating anything the model wrote.
            logger.warning(
                "Refused a remotely hosted generated image (%s): chat_id=%d",
                type(exc).__name__, chat_id,
            )
        except ValueError as exc:
            rejected += 1
            # NOT exc, for the same reason as above: decode_data_url's
            # messages are length/media-type/decode failures TODAY, but they
            # are ordinary exception text, not a contract, and nothing here
            # would notice the day one of them starts quoting the payload.
            # The type name is the fact worth a log line: something about
            # this data: URL did not decode. If which-of-four-reasons turns
            # out to matter later, decode_data_url should hand back a
            # sanitized reason code the way AttachmentError already does,
            # not a message this call site has to trust blind.
            logger.warning("Rejected a generated image: chat_id=%d reason=%s",
                           chat_id, type(exc).__name__)
        except AttachmentError as exc:
            rejected += 1
            logger.warning("Generated image failed validation: chat_id=%d reason=%s",
                           chat_id, exc.reason)
    if remote:
        notices.append({"type": "notice", "code": NOTICE_IMAGE_REMOTE_URL,
                        "count": remote})
    if rejected or surplus:
        notices.append({"type": "notice", "code": NOTICE_IMAGE_REJECTED,
                        "count": rejected + surplus})
    if surplus:
        logger.warning("Dropped %d generated image(s) past the cap: chat_id=%d",
                       surplus, chat_id)
    return out, notices


async def _call_provider_for_chat(
    chat_id: int,
    model_id: str,
    user_message_text: str,
    generation_params: GenerationParams,
    provider: ProviderPolicy,
    persona_id: int | None,
    context_budget_tokens: int | None,
    history_before_id: int | None = None,
    pending_attachments: list[dict] | None = None,
    generation: str = "send",
) -> tuple[str, list[dict], list[tuple[bytes, str, int, int]], bool, int,
           str | None]:
    """Non-streaming provider call: prepare, call OpenRouter, parse.

    Returns (assistant_text, notices, generated_image_bytes, truncated,
    retried, module_vars) on success; `module_vars` is the snapshot the
    reply's insert stores (see _prepare_completion); `retried` is how many automatic retries it took
    (see `_may_retry`). The notices ride back out to the caller for the same reason the
    SSE path emits them: this endpoint has a response body, so "the model
    never saw your picture" has somewhere to go here too, and the two paths
    must not disagree about that (P4). `truncated` is read from THIS choice's
    finish_reason/native_finish_reason - the streaming path has to accumulate
    that across chunks, but a non-streaming reply arrives as one object with
    the field already sitting on it.
    Raises HTTPException on any failure.
    """
    (messages, filtered_gen_params, provider_dict, notices,
     modalities, context_notes, routing) = await _prepare_completion(
        chat_id=chat_id,
        model_id=model_id,
        user_message_text=user_message_text,
        generation_params=generation_params,
        provider=provider,
        persona_id=persona_id,
        context_budget_tokens=context_budget_tokens,
        history_before_id=history_before_id,
        pending_attachments=pending_attachments,
        generation=generation,
    )

    # ── Call OpenRouter ───────────────────────────────────────────────────
    logger.info("Completion request: chat_id=%d", chat_id)

    # At most MAX_PROVIDER_ATTEMPTS. Nothing is persisted until this returns,
    # so a retry can never leave a second row behind: the caller writes once,
    # with the reply that finally arrived.
    attempt = 0
    retried = 0
    while True:
        attempt += 1
        try:
            raw = await complete(messages, model_id, filtered_gen_params,
                                 provider_dict, modalities=modalities)

            # ── Parse response ────────────────────────────────────────────
            choices = raw.get("choices")
            if not isinstance(choices, list) or not choices:
                raise HTTPException(502, "invalid_openrouter_completion_response")

            choice = choices[0] if isinstance(choices[0], dict) else {}
            message = choice.get("message")
            if not isinstance(message, dict):
                raise HTTPException(502, "invalid_openrouter_completion_response")
            # Read from the CHOICE, not the message - finish_reason/
            # native_finish_reason are siblings of "message" in the OpenRouter
            # shape, never inside it.
            truncated = bool(finish_reasons(choice) & TRUNCATED_FINISH_REASONS)

            # Images first, because whether the reply is empty depends on the
            # answer. Nothing is fetched here and nothing base64 survives past
            # this call.
            generated: list[tuple[bytes, str, int, int]] = []
            image_notices: list[dict] = []
            if modalities:
                # Off the loop: this includes the Pillow decode and re-encode.
                generated, image_notices = await anyio.to_thread.run_sync(
                    _decode_generated_images, image_urls_from(message), chat_id,
                )

            content = message.get("content")
            text = content if isinstance(content, str) else ""
            # ONE judgement, where there used to be two consecutive refusals. A
            # reply that is only delivery tags renders as a permanently empty
            # bubble once they are stripped (audit-2), so "visible" is the
            # right measure - but a reply that is a PICTURE and no words is not
            # empty, it is a picture. The four places that judge this must
            # agree on the same bytes; see _visible_view.
            visible = _visible_view(text).strip()
            if not visible and not generated:
                if not text and truncated:
                    # Cut off by "length" before a single word: the budget went
                    # on thinking. The same request would do the same again.
                    raise OpenRouterError(REASONING_BUDGET_REASON)
                if not text:
                    raise OpenRouterError(EMPTY_REPLY_REASON, retryable=True)
                raise HTTPException(502, "invalid_openrouter_completion_response")
        except OpenRouterError as exc:
            if _may_retry(exc, attempt, routing["private_endpoints"]):
                delay = _retry_delay(exc)
                logger.info("Provider attempt %d failed: status=%d; retrying.",
                            attempt, int(exc.status or 0))
                await _retry_sleep(delay)
                retried += 1
                continue
            if exc.reason == EMPTY_REPLY_REASON:
                # Unchanged wording for this path once the retry is spent.
                raise HTTPException(502, "invalid_openrouter_completion_response")
            status, detail = _provider_failure(exc)
            raise HTTPException(status, detail)
        break

    notices.extend(image_notices)
    logger.info("Gen params keys: %s", list(filtered_gen_params.keys()))
    return text, notices, generated, truncated, retried, routing["module_vars"]


# ---------------------------------------------------------------------------
# POST /{chat_id}/complete
# ---------------------------------------------------------------------------

def _persist_exchange_sync(
    chat_id: int, model_id: str, user_text: str, assistant_text: str,
    attachment_ids: list[int],
    generated_images: list[tuple[bytes, str, int, int]] | None = None,
    truncated: bool = False,
    turn_vars: str | None = None,
) -> tuple[dict, dict, int, int, int]:
    """Worker-thread body for the non-streaming write. Both rows, one txn.

    This was the only SUCCESS-path write in this module still running on the
    event loop; every sibling (`_persist_user_turn`, `_insert_assistant_message`,
    `_append_variant`, `_finalize_edit`) is threaded, for the reason spelled out
    at `_persist_user_turn`. The abort rescue is on the loop deliberately, with a
    short busy_timeout to bound it; this one had no such excuse.

    BEGIN IMMEDIATE takes the writer lock before the guard reads, which is what
    makes the guard mean anything: the provider call above takes seconds, and the
    chat can be deleted in that window. Without the guard the assistant INSERT
    trips the chat_id foreign key and a perfectly ordinary race surfaces as a
    500 - the same failure `_create_chat_sync` already refuses to allow ("report
    a 404, never a 500").

    Deliberately NOT a copy of `_insert_assistant_message`'s tail guard. That
    one exists because the streaming path persists the user row BEFORE it
    streams, so the row can be deleted or overtaken while the reply arrives.
    Here both rows are written together after the provider answers, so they
    always land as an adjacent pair at the tail and there is no ordering to
    protect. The asymmetry is the correct one; please do not "fix" it.
    """
    with get_db() as con:
        con.execute("BEGIN IMMEDIATE")
        if con.execute(
            "SELECT 1 FROM chats WHERE id = ?", (chat_id,)
        ).fetchone() is None:
            raise HTTPException(404, "chat_not_found")

        cur = con.execute(
            "INSERT INTO messages (chat_id, role, content) VALUES (?, 'user', ?)",
            (chat_id, user_text),
        )
        user_msg_id = cur.lastrowid

        linked_rows: list[dict] = []
        if attachment_ids:
            linked_rows = link_attachments(con, attachment_ids, user_msg_id)

        cur = con.execute(
            "INSERT INTO messages (chat_id, role, content, truncated) "
            "VALUES (?, 'assistant', ?, ?)",
            (chat_id, assistant_text, int(truncated)),
        )
        asst_msg_id = cur.lastrowid
        # The macro variables this turn left, with the reply they belong to.
        pm_variables.save(con, chat_id, asst_msg_id, turn_vars)

        # In THIS transaction, with the row that owns them. A picture without
        # its reply is unreachable; a reply that claims a picture it does not
        # have is a broken thumbnail forever. One commit, both or neither.
        generated_rows, refused = _store_generated(con, generated_images,
                                                   asst_msg_id, chat_id)

        con.execute(
            "UPDATE chats SET model_id = ?, updated_at = datetime('now') WHERE id = ?",
            (model_id, chat_id),
        )

        user_row = con.execute(
            "SELECT id, chat_id, role, content, created_at, truncated "
            "FROM messages WHERE id = ?",
            (user_msg_id,),
        ).fetchone()
        asst_row = con.execute(
            "SELECT id, chat_id, role, content, created_at, truncated "
            "FROM messages WHERE id = ?",
            (asst_msg_id,),
        ).fetchone()
    return (_msg_to_dict(user_row, linked_rows),
            _msg_to_dict(asst_row, generated_rows),
            user_msg_id, asst_msg_id, refused)


@router.post("/{chat_id}/complete")
async def complete_chat(chat_id: int, body: CompleteRequest) -> dict:
    """Send a user message and receive an assistant response via OpenRouter."""

    pending_rows = _validate_request_attachments(body.attachments, body.model_id)

    (assistant_text, notices, generated, truncated,
     retried, turn_vars) = await _call_provider_for_chat(
        chat_id=chat_id,
        model_id=body.model_id,
        user_message_text=body.message,
        generation_params=body.generation_params,
        provider=body.provider,
        persona_id=body.persona_id,
        context_budget_tokens=body.context_budget_tokens,
        pending_attachments=pending_rows,
    )

    # ── DB transaction: insert user + assistant, link attachments ─────────
    # The notify rides INSIDE the worker thread, not after this await: a bare
    # asyncio.Task.cancel() (application forced-shutdown, not a client
    # disconnect - see openrouter.py's complete()) unwinds this coroutine
    # immediately while the thread keeps writing, detached. Without this the
    # write still lands but the notebook is never told - the silent-skip this
    # function's own docstring already names, just from a different cause.
    loop = asyncio.get_running_loop()

    def _persist_and_notify():
        result = _persist_exchange_sync(
            chat_id, body.model_id, body.message.strip(), assistant_text,
            body.attachments, generated, truncated, turn_vars,
        )
        _notify_notebook_from_thread(loop, chat_id)
        return result

    try:
        user_msg, asst_msg, user_msg_id, asst_msg_id, refused = (
            await anyio.to_thread.run_sync(_persist_and_notify)
        )
    except HTTPException:
        # A deleted chat is a race, not a malfunction. Logging it as a failed
        # write buries the real ones.
        raise
    except Exception:
        logger.warning("DB write failed after successful completion: chat_id=%d", chat_id)
        raise

    notices = _with_refusals(notices, refused)

    logger.info(
        "Completion success: chat_id=%d user_msg_id=%d asst_msg_id=%d",
        chat_id, user_msg_id, asst_msg_id,
    )

    return {
        "chat_id": chat_id,
        "model_id": body.model_id,
        "user_message": user_msg,
        "assistant_message": asst_msg,
        "notices": notices,
        **({"retried": retried} if retried else {}),
    }


# ---------------------------------------------------------------------------
# Streaming (SSE) helpers
# ---------------------------------------------------------------------------

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _sse_event(obj: dict) -> str:
    """Encode one data-only SSE event. The event type lives inside the JSON."""
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def _provider_error_status(reason: str) -> tuple[int, str]:
    """The (status, code) pair a provider failure reports on the wire."""
    return _ERROR_MAP.get(reason, (502, "openrouter_completion_error"))


def _delete_message_row(
    chat_id: int, message_id: int, busy_timeout_ms: int = 15000,
) -> None:
    """Best-effort cleanup of a single message row (sync, abort-safe).

    Linked attachments are UNLINKED first (back to staged) - both because
    foreign_keys=ON would otherwise block the delete, and because the client
    keeps the staged ids for a retry after a failed send.

    busy_timeout_ms (L6): the abort path passes a short value so a held lock
    cannot freeze the event loop; a failure here is already best-effort.
    """
    try:
        with get_db(busy_timeout_ms=busy_timeout_ms) as con:
            con.execute(
                "UPDATE attachments SET message_id = NULL WHERE message_id = ?",
                (message_id,),
            )
            # The fifth delete path, and it was missed. This one swallows its
            # exception and only logs - so an aborted foreign key would leave
            # the orphan message row behind silently, degrading a cleanup that
            # used to be reliable.
            notebook_store.forget_proposals_from_messages(con, [message_id])
            con.execute(
                "DELETE FROM messages WHERE id = ? AND chat_id = ?",
                (message_id, chat_id),
            )
    except Exception:
        logger.warning(
            "Cleanup delete failed: chat_id=%d message_id=%d", chat_id, message_id,
        )
    else:
        # After the commit, and only if it committed. An abandoned turn had
        # already written its row and, if the reply was spoken, its audio;
        # this path removed the row and left the recording.
        audio_sweep.forget_spoken_audio([message_id])


class StaleExchangeError(Exception):
    """The exchange's user row (or the chat itself) vanished mid-stream -
    the user cleared/deleted while text streamed. Finalizing would insert an
    orphan assistant row and "resurrect" the emptied chat. (v1.1 H12/I9.)"""


def _insert_assistant_message(
    chat_id: int, model_id: str, text: str, user_msg_id: int,
    busy_timeout_ms: int = 15000,
    generated_images: list[tuple[bytes, str, int, int]] | None = None,
    truncated: bool = False,
    turn_vars: str | None = None,
) -> dict:
    """Insert an assistant message + bump the chat; return the API row dict.

    Guard + insert are ONE write transaction (BEGIN IMMEDIATE, same TOCTOU
    rationale as the regenerate swap): the exchange's user row must still
    exist AND still be the chat's tail at commit time, else StaleExchangeError.
    This covers BOTH callers - the normal `done` insert and the abort-partial
    insert - against a clear/delete landing while the provider streamed.

    The tail half was missing while both sibling swaps had it (`_append_variant`
    checks last_active_anchor, `_finalize_edit` checks MAX(id)). Existence alone
    lets a reply land BEHIND a turn that arrived while it streamed, so the chat
    reloads as user -> user -> assistant with the answer attached to the wrong
    question. Hard to reach - the writer has to win a race against a stream that
    is already finishing - but the asymmetry was real and the guard is one query.

    busy_timeout_ms (L6): the abort-partial caller passes a short value so a
    held lock cannot freeze the event loop; the partial is dropped instead.
    """
    with get_db(busy_timeout_ms=busy_timeout_ms) as con:
        con.execute("BEGIN IMMEDIATE")
        user_still_there = con.execute(
            "SELECT 1 FROM messages WHERE id = ? AND chat_id = ? AND role = 'user'",
            (user_msg_id, chat_id),
        ).fetchone()
        if user_still_there is None:
            raise StaleExchangeError()
        tail = con.execute(
            "SELECT MAX(id) AS m FROM messages WHERE chat_id = ?", (chat_id,)
        ).fetchone()["m"]
        if tail != user_msg_id:
            raise StaleExchangeError()
        cur = con.execute(
            "INSERT INTO messages (chat_id, role, content, truncated) "
            "VALUES (?, 'assistant', ?, ?)",
            (chat_id, text, int(truncated)),
        )
        asst_msg_id = cur.lastrowid
        pm_variables.save(con, chat_id, asst_msg_id, turn_vars)
        generated_rows, refused = _store_generated(con, generated_images,
                                                   asst_msg_id, chat_id)
        con.execute(
            "UPDATE chats SET model_id = ?, updated_at = datetime('now') WHERE id = ?",
            (model_id, chat_id),
        )
        row = con.execute(
            "SELECT id, chat_id, role, content, created_at, truncated "
            "FROM messages WHERE id = ?",
            (asst_msg_id,),
        ).fetchone()
    if refused:
        logger.warning("Stream dropped %d generated image(s): chat_id=%d",
                       refused, chat_id)
    return _msg_to_dict(row, generated_rows)


class RegenerateConflictError(Exception):
    """The target group is no longer the chat's last active group at swap time
    - nothing was written. (v1.1 FB2b: shared by both regenerate handlers.)"""


class EditConflictError(Exception):
    """The edit target changed while the provider ran (row edited/deleted,
    downstream grew or shrank, chat cleared). NOTHING was written. (I6.)

    Declared beside its two siblings rather than next to _finalize_edit: the
    shared streaming body maps all three to one 409 branch, so the map has to
    be able to name them all."""



def _offer_to_notebook(chat_id: int) -> None:
    """Tell the extractor a turn landed. Never blocks, never raises.

    Called from EVERY path that persists a turn - the streaming generator and
    the three non-streaming routes. A version wired only into the streaming
    one worked in the live UI and silently stopped extracting for any client
    that used the plain routes, with the status screen reporting a perfectly
    healthy idle worker.

    EVENT-LOOP THREAD ONLY. notebook_worker.worker.offer() puts onto an
    asyncio.Queue, which - like every asyncio object - is not safe to touch
    from any other thread. Call from a worker thread through
    `_notify_notebook_from_thread` instead, never directly.
    """
    try:
        import notebook_worker
        notebook_worker.worker.offer(chat_id)
    except Exception:                              # pragma: no cover - belt
        logger.info("Notebook worker could not be notified.")


def _notify_notebook_from_thread(
    loop: asyncio.AbstractEventLoop, chat_id: int,
) -> None:
    """The `_offer_to_notebook` a WORKER THREAD may call.

    Every persist here runs off the event loop (audit KOK 8), and a bare
    asyncio.Task.cancel() - unlike a client disconnect, see openrouter.py's
    complete() - does not stop that worker thread: `anyio.to_thread.run_sync`
    already returned CancelledError to the coroutine while the thread was
    still running (measured). So the write can succeed with nobody left on
    the event loop to call `_offer_to_notebook` afterwards.

    Calling it directly FROM the thread would touch notebook_worker's
    asyncio.Queue from a non-owning thread - not merely undocumented but
    actively unsafe, since Queue wakes waiters through the loop's own Future
    machinery. loop.call_soon_threadsafe is the documented crossing: it
    schedules the call to run ON the loop, whether or not the coroutine that
    started this write is still around to see it.
    """
    try:
        loop.call_soon_threadsafe(_offer_to_notebook, chat_id)
    except Exception:                              # pragma: no cover - belt
        logger.info("Notebook worker could not be notified (loop gone).")


def _append_variant(
    chat_id: int, anchor: int, text: str, model_id: str, fallback_active_id: int,
    generated_images: list[tuple[bytes, str, int, int]] | None = None,
    truncated: bool = False,
    turn_vars: str | None = None,
) -> dict:
    """Atomic variant append (v1.1 FB2b/I7): run in a WORKER THREAD, opening
    its own connection here. Guard + deactivate + insert + chat bump all live
    in ONE BEGIN IMMEDIATE txn - unchanged semantics/SQL from the two inline
    swap blocks it replaces. Nothing is deleted; the old variant is
    deactivated in place. Raises RegenerateConflictError when the target group
    is no longer the last active group.

    Returns a plain dict (not a Row) so the result is safe to hand back across
    the thread boundary.
    """
    with get_db() as con:
        con.execute("BEGIN IMMEDIATE")
        # Guard scope (deliberate): the target's GROUP must still be the last
        # ACTIVE group. A concurrent regenerate of the SAME group passes and
        # appends another sibling by design - that is intended, not a lost
        # update.
        if last_active_anchor(con, chat_id) != anchor:
            raise RegenerateConflictError()

        # Re-resolve the active sibling AT SWAP TIME: an activate (or a racing
        # regenerate) may have changed it while the provider ran; reporting the
        # pre-call id would desync the client's cache.
        cur_active = con.execute(
            "SELECT id FROM messages WHERE chat_id = ? "
            "AND COALESCE(variant_group, id) = ? AND active = 1",
            (chat_id, anchor),
        ).fetchone()
        deactivated_id = cur_active["id"] if cur_active else fallback_active_id

        # Deactivate BEFORE insert - idx_one_active_per_group allows only one
        # active row per group. Also stamps the anchor's variant_group.
        con.execute(
            "UPDATE messages SET variant_group = ?, active = 0 "
            "WHERE chat_id = ? AND COALESCE(variant_group, id) = ? "
            "AND active = 1",
            (anchor, chat_id, anchor),
        )
        cur = con.execute(
            "INSERT INTO messages "
            "(chat_id, role, content, variant_group, active, truncated) "
            "VALUES (?, 'assistant', ?, ?, 1, ?)",
            (chat_id, text, anchor, int(truncated)),
        )
        asst_msg_id = cur.lastrowid
        pm_variables.save(con, chat_id, asst_msg_id, turn_vars)
        generated_rows, refused = _store_generated(con, generated_images,
                                                   asst_msg_id, chat_id)
        variant_count = con.execute(
            "SELECT COUNT(*) AS n FROM messages "
            "WHERE chat_id = ? AND COALESCE(variant_group, id) = ?",
            (chat_id, anchor),
        ).fetchone()["n"]
        con.execute(
            "UPDATE chats SET model_id = ?, updated_at = datetime('now') "
            "WHERE id = ?",
            (model_id, chat_id),
        )
        asst_row = con.execute(
            "SELECT id, chat_id, role, content, created_at, "
            "variant_group, active, truncated FROM messages WHERE id = ?",
            (asst_msg_id,),
        ).fetchone()
    return {
        "asst_row": dict(asst_row),
        "variant_count": variant_count,
        "deactivated_id": deactivated_id,
        "generated_rows": generated_rows,
        "refused_images": refused,
    }


def _persist_user_turn(
    chat_id: int, content: str, model_id: str, attachment_ids: list[int],
) -> tuple[dict, int]:
    """Worker-thread body (v1.1 FB2a/I7): open the connection here, never on
    the event loop - an image-linking insert must not stall live SSE streams.
    Returns (user_message_dict, user_msg_id)."""
    with get_db() as con:
        cur = con.execute(
            "INSERT INTO messages (chat_id, role, content) VALUES (?, 'user', ?)",
            (chat_id, content),
        )
        user_msg_id = cur.lastrowid
        linked_rows: list[dict] = []
        if attachment_ids:
            linked_rows = link_attachments(con, attachment_ids, user_msg_id)
        con.execute(
            "UPDATE chats SET model_id = ?, updated_at = datetime('now') WHERE id = ?",
            (model_id, chat_id),
        )
        user_row = con.execute(
            "SELECT id, chat_id, role, content, created_at FROM messages WHERE id = ?",
            (user_msg_id,),
        ).fetchone()
    return _msg_to_dict(user_row, linked_rows), user_msg_id


# ---------------------------------------------------------------------------
# The shared streaming body
# ---------------------------------------------------------------------------
#
# Three endpoints - send, regenerate, edit - ran ~60 byte-identical lines each:
# open the speaker, stream the deltas past the stripper, flush the tail, judge
# the display view, finalize, drain the audio. Three copies meant every voice
# and abort fix had to be made three times, and the audit found places where it
# had been made twice.
#
# What actually differs is small and now explicit: the first event, how the
# finished text is committed, and what an interrupted exchange leaves behind.

#: Finalizers raise these when the chat moved under them. All three mean the
#: same thing to a client - "your view is stale, resync" - and all three were
#: already answered with 409; only the code differed per endpoint.
_CONFLICT_CODES: dict[type, str] = {
    StaleExchangeError: "exchange_stale",
    RegenerateConflictError: "regenerate_conflict",
    EditConflictError: "edit_conflict",
}

#: The same three codes as a set, for `tests/error_enumeration.py`. The SSE
#: error event below looks its code up from the dict by exception type, so a
#: reader of the source sees the name `code` and never the three values behind
#: it. Derived, not retyped.
CONFLICT_DETAILS: frozenset[str] = frozenset(_CONFLICT_CODES.values())


def _visible_view(raw: str) -> str:
    """What the bubble will actually SHOW for this raw reply.

    One definition, used by every gate that asks "is there anything here?".
    The success path judged on this view and the abort path judged on the raw
    text, so a reply that was nothing but delivery tags was refused when it
    finished and stored when it was stopped - the same bytes, opposite answers,
    and the stored one rendered as a permanently empty bubble forever.
    """
    return voice_tags.strip_tags(raw) if voice_tags.stripping_active() else raw


def _keepable_partial(parts: list[str]) -> str:
    """The RAW text worth storing from an interrupted reply, or "".

    Two steps, in this order. trim_broken_tail drops an in-progress tag the
    stripper was still withholding - text the user never saw, which would
    reload as a broken bracket. Then the survivor is judged on its display
    view, so a partial with no visible content is not stored at all.

    Raw is what gets STORED (the tags are what make a replay worth hearing);
    the display view only decides whether storing is worth it.
    """
    partial = "".join(parts)
    if voice_tags.stripping_active():
        partial = voice_tags.trim_broken_tail(partial)
    return partial if _visible_view(partial).strip() else ""


async def _stream_exchange(
    *,
    chat_id: int,
    model_id: str,
    label: str,
    body: "SpeakOptions",
    messages: list[dict],
    gen_params: dict,
    provider_dict: dict,
    first_event: dict,
    notices: list[dict],
    finalize,
    rescue=None,
    modalities: tuple[str, ...] | list[str] | None = None,
    # FAZ 2. What the assembler decided about THIS turn, carried to the `done`
    # frame. Not a toast: errorStore merges by code and chat, so a per-turn
    # count would speak once and then go quiet exactly when the ceiling starts
    # biting every turn. One frame per turn, no merging, nothing persisted.
    context_notes: dict | None = None,
    # Q-121: how many private endpoints take this exact request, from the
    # preflight; None when the list was unknown. Only `_may_retry` reads it.
    private_endpoints: int | None = None,
):
    """Stream one provider reply and commit it. The body all three share.

    finalize(full_text, generated_images, truncated) -> dict: awaited once the
        reply is complete; the dict is merged into the `done` event. May raise
        any key of _CONFLICT_CODES. generated_images are raw bytes, already
        decoded and already judged - the finalizer stores them in the SAME
        transaction as the assistant row, so a picture and the reply that owns
        it commit together. `truncated` is complete_stream's own verdict (see
        its on_finish docstring): the model's ceiling, or a connection that
        died without ever saying it was done - never true for a natural stop.
    rescue(partial, persisted, urgent) -> bool: what an exchange that ended
        early leaves behind, True when it kept the partial. None for the two
        endpoints that write nothing until finalize, so there is nothing to
        undo. `urgent` is set only inside GeneratorExit handling, where
        awaiting is not an option. A KEPT partial is ALWAYS truncated - it is
        text the model never finished saying, whether the reason was a client
        abort or a provider error mid-reply - so rescue's own implementation
        marks it True unconditionally rather than asking complete_stream,
        which never got the chance to render a verdict on a stream that did
        not end cleanly.
    """
    parts: list[str] = []
    #: `data:` URLs collected off the image channel. Strings only, and never
    #: mixed into `parts`: everything downstream of parts assumes str-that-is-
    #: text, so a URL in there would be stored in messages.content, painted in
    #: the bubble and read aloud.
    image_urls: list[str] = []
    # complete_stream calls this once, after the loop ends without raising,
    # with its own verdict on whether the reply was cut off (finish_reason in
    # TRUNCATED_FINISH_REASONS, or the connection closing with no [DONE] and
    # no finish_reason at all - see its docstring). A one-element list rather
    # than a bare bool so the closure below can WRITE it; `finalize(full_text,
    # generated, finish_state[0])` reads it back after the loop ends.
    finish_state: list[bool] = [False]

    def _on_finish(truncated: bool) -> None:
        finish_state[0] = truncated
    persisted = False  # guards against a double-insert if the client
    # disconnects exactly at the `done` yield (GeneratorExit lands in the
    # abort handler after the assistant row is already written).
    finalizing = False  # True from just before `await finalize(...)` until it
    # either returns or raises. Guards the OTHER double-insert: a bare
    # asyncio.Task.cancel() - not a client disconnect, see openrouter.py's
    # complete() for why those are different - lands inside finalize()'s own
    # `anyio.to_thread.run_sync` write and is delivered to THIS coroutine
    # immediately, while the write's worker thread keeps running underneath,
    # detached, and reliably goes on to commit (measured: tests/
    # test_stream_finalize_cancel.py's finalize-cancel case). Without this flag the
    # except(GeneratorExit, CancelledError) handler below raced that detached
    # write with its own "urgent" rescue insert - both targeting the same
    # tail-guarded row, so exactly one committed and it was a coin flip which:
    # sometimes the correct, fully-labelled reply (with its generated images),
    # sometimes rescue's copy, which is unconditionally truncated=True even
    # when the reply was complete and never carries generated images at all
    # (rescue() does not forward them). See the `finalizing` check inside
    # `_run_rescue` just below.
    aborting = False   # the `finally` cannot await once we are being cancelled
    # Voice rides ALONGSIDE the reply and may never cost it: a silent hook
    # is returned for "off", "not configured" and "engine broke", so no
    # branch below has to know which. The rate is bound in here because it
    # belongs to the engine set-up, not to the stream.
    # OFF THE LOOP (audit: the largest avoidable cost on the send path). With
    # continuous voice on, this call BUILDS the engine - SpeakHook.__init__ ->
    # enable() -> make_stream_synth: an uncached models-folder walk plus about
    # five vault opens, "hundreds of milliseconds" by stream_hook.enable's own
    # docstring. It ran here, synchronously, in front of the first SSE event,
    # the provider request, the first token AND the first audio, and it froze
    # every OTHER live stream for the duration. The two sibling endpoints
    # already knew better: /tts/speak_live is a plain `def` so FastAPI runs the
    # identical work in a threadpool, and /tts/speak_stream wraps it in
    # run_sync with the reason written out. Only this path paid.
    #
    # A client disconnecting DURING the build is safe: this Starlette build
    # only cancels through anyio's own CancelScope machinery for an HTTP
    # disconnect (see openrouter.py's complete()), and abandon_on_cancel=False
    # genuinely shields that - measured. What is NOT safe is a bare
    # asyncio.Task.cancel(), the kind uvicorn's forced-shutdown path uses:
    # measured, it is delivered to this await immediately, while the build
    # keeps running in its thread. The `except BaseException` below only ever
    # closed hooks that had ALREADY been appended to `_built` by the time it
    # ran - a hook the thread finishes building AFTER that (the common case:
    # the build takes "hundreds of milliseconds", the cancel usually lands
    # first) was still dropped, held VRAM and everything, exactly as this
    # comment used to worry about while believing the code below already
    # prevented it. `_build_state` below is what actually closes that one.
    _built: list = []
    _build_lock = threading.Lock()
    _build_state = {"abandoned": False}

    # One token per stream, and it goes into the name of every wav this
    # reply produces. Two concurrent streams must not share a pattern: the
    # old `speak-0-` was shared by all of them, so a bulk rename onto one
    # message id would have taken the other stream's audio with it.
    stream_token = uuid.uuid4().hex[:12]

    def _open_speaker():
        hook = stream_hook.open_speaker(
            bool(getattr(body, "speak", False)),
            # Armable even when speaking was not asked for: during a stream the
            # assistant row does not exist yet, so the Speak button has no id to
            # send. Buffering the raw text here is what keeps that button
            # meaningful mid-reply - and lets it speak FROM THE START rather
            # than joining three sentences in. Gated on the sticky "voice was
            # ever enabled" flag, already read and cached for the stripper, so
            # a user who has never touched voice allocates nothing.
            #
            # OR a voice model is selected: SpeakLiveButton renders on model
            # readiness, not on the sticky flag, so gating only on the flag made
            # the button answer 404 tts_nothing_streaming for a reply that was
            # still streaming - "a button that can only produce an error toast
            # is a broken promise". Both predicates are settings reads, and they
            # are now on this thread too.
            armable=(voice_tags.stripping_active()
                     or tts_runtime.a_voice_model_is_selected()),
            narrative=getattr(body, "speak_narrative", "same"),
            make_synth=lambda: tts_runtime.make_stream_synth(
                rate=getattr(body, "speak_rate", None),
                stream_token=stream_token),
            # The FUNCTION, not the table: reading it is a vault call, and a
            # stream that only arms a dormant speaker (most of them) must not
            # pay for one up front. The hook resolves it when it speaks.
            pronunciations=tts_runtime.stored_pronunciations,
        )
        # Whichever side loses the race closes the hook; whichever side wins
        # is the only one that ever touches it again. Without the lock, the
        # awaiting coroutine could read `_built` between this thread deciding
        # to append and actually appending, and both sides would walk away
        # thinking the other owns it - closed by neither.
        with _build_lock:
            if _build_state["abandoned"]:
                try:
                    hook.close()
                except Exception:                        # noqa: BLE001
                    logger.warning(
                        "orphaned speaker would not close: chat_id=%d",
                        chat_id, exc_info=True)
                return None
            _built.append(hook)
        return hook

    try:
        voice = await anyio.to_thread.run_sync(_open_speaker)
    except BaseException:
        # Close whatever is ALREADY built, and tell a build still in flight
        # (a bare-cancelled one) to close itself the moment it finishes -
        # see `_build_state` above.
        with _build_lock:
            _build_state["abandoned"] = True
            pending = list(_built)
            _built.clear()
        for hook in pending:
            try:
                hook.close()
            except Exception:                            # noqa: BLE001
                logger.warning("orphaned speaker would not close: chat_id=%d",
                               chat_id, exc_info=True)
        raise
    stream_hook.register_live(chat_id, voice)

    async def _run_rescue(*, urgent: bool) -> bool:
        """True when a partial was kept. Never raises."""
        if rescue is None:
            return False
        if finalizing:
            # finalize()'s own write is in flight in a detached worker thread
            # (measured: a bare Task.cancel() does not stop it, see the
            # `finalizing` comment above) and it reliably goes on to commit.
            # Racing it with a SECOND insert here does not protect anything -
            # it only decides, by coin flip, whether that correct write or
            # this worse one (unconditionally truncated=True, no generated
            # images) is the one the tail guard keeps. Do nothing and let the
            # original write be the only writer.
            logger.info(
                "Streaming %s cancelled during finalize; leaving its write to "
                "finish on its own: chat_id=%d", label, chat_id,
            )
            return False
        partial = _keepable_partial(parts)
        try:
            if urgent:
                # Inside GeneratorExit/CancelledError: awaiting here is
                # fragile, and WAL + synchronous=NORMAL keeps the commit cheap.
                return bool(rescue(partial, persisted=persisted, urgent=True))
            return bool(await anyio.to_thread.run_sync(
                lambda: rescue(partial, persisted=persisted, urgent=False),
            ))
        except Exception:
            logger.warning(
                "Streaming %s cleanup failed: chat_id=%d", label, chat_id,
                exc_info=True,
            )
            return False

    try:
        yield _sse_event(first_event)
        # Before the first delta ON PURPOSE: a picture the model never saw
        # changes how the answer should be read, so it must arrive before the
        # answer does, not as a footnote after it.
        for notice in notices:
            yield _sse_event(notice)

        # Things the provider layer has to say once the stream is under way:
        # a frame it could not read (K-20), and a stream that stopped without
        # saying so (K-15). Both used to be counted and dropped on the floor.
        late_notices: list[dict] = []

        def _note(reason: str, count: int) -> None:
            """Two literal codes, spelled out at their own emission sites.

            The error census reads `{"type": "notice", "code": ...}` dicts and
            resolves module constants; it cannot resolve a code that arrives
            as a function argument. A forwarded variable would have made both
            of these invisible to the one thing that checks every user-facing
            code has a sentence.
            """
            if reason == NOTICE_FRAME_DROPPED:
                late_notices.append({"type": "notice",
                                     "code": NOTICE_FRAME_DROPPED,
                                     "count": count})
            else:
                late_notices.append({"type": "notice",
                                     "code": NOTICE_STREAM_UNFINISHED})

        # None when voice was never enabled: zero stripping work, and the raw
        # text IS the display text (audit-2: no unconditional stripping).
        stripper = voice_tags.StreamStripper() if voice_tags.stripping_active() else None
        # The image channel. The sink is called from inside the SSE parse loop,
        # so it does exactly one cheap thing - append a string - and nothing
        # else: no decode, no DB, no yield. Decoding happens after the stream,
        # off the first-token and first-audio path entirely.
        #
        # `if modalities else None` is the whole gate, and it was missing. The
        # sink was passed unconditionally while openrouter only checks whether a
        # sink EXISTS, so a picture the provider volunteered was decoded and
        # committed to the vault for somebody who had never turned the feature
        # on - and the byte-identical reply through /complete stored nothing,
        # because that path gates on `modalities`. Off has to mean off on the
        # path the app actually uses.
        # At most MAX_PROVIDER_ATTEMPTS, and a second one only while NOTHING
        # has reached the reader: no delta, no picture. The barrier is `parts`
        # and `image_urls` themselves, the two things every delivery goes
        # through, so no path can deliver and still retry. OpenRouter stops
        # its own failover at the same point. The user row was written once,
        # before this generator started; the assistant row is written once,
        # by finalize, after the loop. A retry adds neither.
        attempt = 0
        retried = 0
        while True:
            attempt += 1
            finish_state[0] = False
            try:
                async for delta in complete_stream(
                    messages, model_id, gen_params, provider_dict,
                    modalities=modalities,
                    on_image=image_urls.append if modalities else None,
                    # A SEPARATE queue, not `notices`: those were already
                    # emitted above, before the first delta. These arrive
                    # during the stream and are drained below.
                    on_notice=_note,
                    on_finish=_on_finish,
                ):
                    parts.append(delta)                      # RAW - storage
                    voice.feed(delta)                        # RAW - the tags
                    # are what make the delivery worth hearing, and only the
                    # raw text has them; the client never sees this view.
                    shown = stripper.feed(delta) if stripper else delta
                    if shown:
                        yield _sse_event({"type": "delta", "content": shown})
                    while late_notices:
                        yield _sse_event(late_notices.pop(0))
                    for event in voice.events():             # never waits
                        yield _sse_event(event)
                if not parts and not image_urls:
                    # Not one byte of reply. `finish_state` is complete_stream's
                    # "cut off" verdict, which also covers a stream that ended
                    # without saying why; that second case is the one that
                    # queued NOTICE_STREAM_UNFINISHED, and only the first is a
                    # budget spent on thinking.
                    unfinished = any(n.get("code") == NOTICE_STREAM_UNFINISHED
                                     for n in late_notices)
                    if finish_state[0] and not unfinished:
                        raise OpenRouterError(REASONING_BUDGET_REASON)
                    raise OpenRouterError(EMPTY_REPLY_REASON, retryable=True)
            except OpenRouterError as exc:
                if (parts or image_urls
                        or not _may_retry(exc, attempt, private_endpoints)):
                    raise
                # What the failed attempt queued describes a stream nobody
                # will see.
                late_notices.clear()
                delay = _retry_delay(exc)
                logger.info("Provider attempt %d failed: status=%d; retrying.",
                            attempt, int(exc.status or 0))
                await _retry_sleep(delay)
                retried += 1
                continue
            break
        # A tag still held at stream end was never a tag - show it now,
        # or the visible tail of the message would simply vanish.
        tail = stripper.flush() if stripper else ""
        if tail:
            yield _sse_event({"type": "delta", "content": tail})
        # NOT voice.feed(tail): the stripper's tail is text it WITHHELD
        # from the display, and the speaker already received it as a raw
        # delta. Feeding it again would say that clause twice.
        voice.finish()

        full_text = "".join(parts)

        # Decode here: after the last delta, before the commit. Never inside
        # the loop - a multi-MB base64 decode there would sit in front of the
        # next delta and in front of voice.events(), which is the poll that
        # ships audio.
        generated: list[tuple[bytes, str, int, int]] = []
        # Belt over the sink gate above: two conditions for one decision is how
        # the image-INPUT gate went wrong once already, so this one is checked
        # again at the only other place that could act on the list.
        if modalities and image_urls:
            generated, image_notices = await anyio.to_thread.run_sync(
                _decode_generated_images, image_urls, chat_id,
            )
            for notice in image_notices:
                yield _sse_event(notice)

        # Judged on the DISPLAY view: a reply that is only delivery tags
        # would render as a permanently empty bubble - the one silence R3
        # bans. (Raw is still what gets stored when the gate passes.)
        #
        # A reply that is a PICTURE and no words is not empty. Widened here and
        # in _keepable_partial together, because the whole point of _visible_view
        # is that the four places judging the same bytes cannot disagree - and
        # only after the bytes have been decoded, never on the promise of them.
        if not _visible_view(full_text).strip() and not generated:
            raise OpenRouterError("openrouter_error")

        finalizing = True
        try:
            done = await finalize(full_text, generated, finish_state[0])
        except tuple(_CONFLICT_CODES) as exc:
            # A clean, fully-resolved outcome: finalize() itself returned
            # control to us (raising a named conflict, not a cancellation),
            # so its write is over and `finalizing` must drop before we do
            # anything else - unlike the CancelledError/GeneratorExit case
            # below, where it must stay True into the outer handler. See the
            # `finalizing` comment above.
            finalizing = False
            code = _CONFLICT_CODES[type(exc)]
            logger.warning(
                "Streaming %s conflict: chat_id=%d code=%s", label, chat_id, code,
            )
            yield _sse_event({"type": "error", "status": 409, "code": code})
            return
        except (GeneratorExit, asyncio.CancelledError):
            # Do NOT reset `finalizing` here. run_sync only raises this
            # because a bare Task.cancel() interrupted the AWAIT - not
            # because the worker thread finished - so the write is very
            # likely still running, detached. The outer
            # except(GeneratorExit, CancelledError) handler below reads
            # `finalizing` (via `_run_rescue`) to decide whether writing
            # again is safe. See the `finalizing` comment above.
            raise
        except BaseException:
            # Any OTHER failure: to_thread.run_sync does not return or raise
            # until its worker thread is actually done, so nothing is still
            # running here - safe to fall back to the normal rescue path
            # exactly as before this fix.
            finalizing = False
            raise
        finalizing = False
        persisted = True
        spoken_id = done.get("assistant_message", {}).get("id")
        logger.info(
            "Streaming %s success: chat_id=%d asst_msg_id=%s",
            label, chat_id, spoken_id,
        )
        # The row exists now, so the audio this stream made can finally carry
        # its id. Until this landed, a live reply's wav was called
        # `speak-0-...` and forget_message_audio - the whole machine for
        # destroying the spoken form of a deleted message - globs
        # `speak-<mid>-*` and never matched it. Deleting the message removed
        # the row and left the recording of it in the clear.
        #
        # Off the loop: it is a handful of renames, and a rename on Windows
        # can block on a file that is still playing.
        if isinstance(spoken_id, int) and spoken_id > 0:
            try:
                from tts.host import get_host
                await anyio.to_thread.run_sync(
                    get_host().adopt_stream_audio, stream_token, spoken_id)
            except Exception:                            # noqa: BLE001
                # Never fail a delivered reply over bookkeeping. The files
                # are still swept by age, by the lock and at launch.
                logger.warning("tts: could not adopt streamed audio onto its "
                               "message", exc_info=True)
        # The notify already happened INSIDE finalize()'s own worker thread,
        # right after its write committed (see `_notify_notebook_from_thread`)
        # - not here, because a bare Task.cancel() can keep that write running
        # after this coroutine has already been abandoned, with nobody left to
        # reach this line. Calling `_offer_to_notebook` again here on the
        # normal path would double-queue the same chat id.
        #
        # Before `done`, always. A reader that learns a sentence went missing
        # AFTER being told the reply is complete has been told two things in
        # the wrong order.
        while late_notices:
            yield _sse_event(late_notices.pop(0))
        yield _sse_event({
            "type": "done",
            "chat_id": chat_id,
            "model_id": model_id,
            **(context_notes or {}),
            **done,
            # The only trace a retry leaves for the reader: how many there
            # were. Absent on the ordinary path, so that frame is unchanged.
            **({"retried": retried} if retried else {}),
        })

        # The text is done; the audio is not. Reading must never wait on
        # speaking, so `done` has already gone out and the remaining
        # sentences arrive behind it until `voice_done`. A client that
        # ignores voice events sees exactly the stream it saw before.
        async for event in stream_hook.drain_events(voice):
            yield _sse_event(event)

    except OpenRouterError as exc:
        # KÖK 16: the provider can fail AFTER most of the reply has already
        # been emitted (a chunk carrying finish_reason == "error"). Treating
        # that as a failure before the first byte threw away text the user had
        # already read - while the abort branch just below KEPT the same amount
        # of text. Two policies for one situation, and the one who lost was the
        # user. Same salvage now, and the error event still goes out so they
        # also learn why it stopped.
        kept = await _run_rescue(urgent=False)
        logger.warning(
            "Streaming %s failed: chat_id=%d reason=%s partial_kept=%s",
            label, chat_id, exc.reason, kept,
        )
        status, detail = _provider_failure(exc)
        event = {"type": "error", "status": status, "code": detail}
        if kept:
            # The client keeps what it read instead of rolling it back; the
            # rows are committed already, so its refetch answers with them.
            event["partial_saved"] = True
        yield _sse_event(event)

    except (GeneratorExit, asyncio.CancelledError):
        aborting = True
        await _run_rescue(urgent=True)
        logger.info("Streaming %s aborted: chat_id=%d", label, chat_id)
        raise

    except VaultLockedError:
        # Vault locked mid-stream (deliberate user action). The reply is
        # lost and nothing can be cleaned up while locked; report it
        # honestly instead of a generic 500.
        logger.info(
            "Streaming %s interrupted by vault lock: chat_id=%d", label, chat_id,
        )
        yield _sse_event({"type": "error", "status": 423, "code": "vault_locked"})

    except Exception:
        await _run_rescue(urgent=False)
        logger.warning(
            "Streaming %s internal error: chat_id=%d", label, chat_id,
            exc_info=True,
        )
        yield _sse_event({"type": "error", "status": 500, "code": "internal_error"})

    finally:
        # Every exit path, including the GeneratorExit the abort handler
        # re-raises: a surviving speaker keeps synthesising a reply nobody
        # is listening to any more, and a stale registry entry would point
        # Speak at a reply that finished minutes ago.
        stream_hook.unregister_live(chat_id, voice)
        if aborting:
            # Blocking close ON PURPOSE here. A fresh await inside
            # GeneratorExit/CancelledError handling can re-raise immediately,
            # which would both replace the exception this generator owes its
            # caller AND leak the speaker we came here to close. cancel() runs
            # first inside close(), so the worker is already told to stop and
            # the join is short - and the connection it would have delayed is
            # gone anyway. Every other path takes the thread hop.
            voice.close()
        else:
            await stream_hook.aclose(voice)


# ---------------------------------------------------------------------------
# POST /chats/{chat_id}/complete/stream
# ---------------------------------------------------------------------------

@router.post("/{chat_id}/complete/stream")
async def complete_chat_stream(chat_id: int, body: CompleteRequest) -> StreamingResponse:
    """Streaming variant of /complete (SSE).

    Event sequence (each as `data: {json}` with a "type" field):
      user_message → delta* → done          on success
      user_message → delta* → error         on provider failure

    Persistence semantics:
      - The user message is persisted BEFORE the provider call and the row is
        sent as the first event.
      - Provider failure (or empty output): the just-inserted user message is
        deleted again, so a failed exchange leaves no half-turn behind; the
        frontend restores the draft.
      - Client abort: if any partial text was received it is persisted as the
        assistant message (the user keeps what they saw); with no partial
        text the user message is removed, as with a failure.

    Validation problems (404s, missing key, proxy gate, budget) surface as
    normal HTTP errors before the stream starts.
    """
    pending_rows = _validate_request_attachments(body.attachments, body.model_id)

    (messages, filtered_gen_params, provider_dict, notices,
     modalities, context_notes, routing) = await _prepare_completion(
        chat_id=chat_id,
        model_id=body.model_id,
        user_message_text=body.message,
        generation_params=body.generation_params,
        provider=body.provider,
        persona_id=body.persona_id,
        context_budget_tokens=body.context_budget_tokens,
        pending_attachments=pending_rows,
    )

    user_message_stripped = body.message.strip()
    model_id_stripped = body.model_id

    # FB2a: the user-insert txn (insert + link_attachments + chat bump) runs
    # off the event loop so an image-heavy link cannot stall other live SSE
    # streams before this one even starts yielding.
    user_message, user_msg_id = await anyio.to_thread.run_sync(
        _persist_user_turn,
        chat_id, user_message_stripped, model_id_stripped, list(body.attachments),
    )

    logger.info(
        "Streaming completion start: chat_id=%d user_msg_id=%d",
        chat_id, user_msg_id,
    )

    finalize_loop = asyncio.get_running_loop()

    async def finalize(
        full_text: str, generated: list[tuple[bytes, str, int, int]],
        truncated: bool,
    ) -> dict:
        # Worker thread: the commit between SSE events must not block the
        # loop (other live streams stall for its duration).
        #
        # Notify from INSIDE the thread, not from `_stream_exchange` after
        # `persisted = True`: a bare Task.cancel() lands here and this
        # coroutine never returns to set that flag, but the write below still
        # goes on to commit (see `finalizing` in `_stream_exchange`). Without
        # this the notebook would never learn the turn landed.
        def _insert_and_notify():
            row = _insert_assistant_message(
                chat_id, model_id_stripped, full_text, user_msg_id, 15000,
                generated, truncated, turn_vars=routing["module_vars"],
            )
            _notify_notebook_from_thread(finalize_loop, chat_id)
            return row

        assistant_message = await anyio.to_thread.run_sync(_insert_and_notify)
        return {
            "user_message": user_message,
            "assistant_message": assistant_message,
        }

    def rescue(partial: str, *, persisted: bool, urgent: bool) -> bool:
        """What a half-finished send leaves behind. Returns True if kept.

        ONE policy for all three ways a send can end early - client abort,
        provider failure mid-stream, internal error - because from the user's
        chair they are the same event: text appeared, and then it stopped.
        Keeping the partial for a Stop press and discarding it for a dropped
        connection made the reply's survival depend on which side let go
        first, which is not something anyone can predict or learn.
        """
        if persisted:
            # Success already committed the assistant row; the disconnect
            # happened at or after the `done` yield.
            logger.info(
                "Streaming completion disconnected after done: chat_id=%d", chat_id,
            )
            return False
        # L6: the urgent (GeneratorExit) caller cannot afford to queue on a
        # held write lock - it would freeze the event loop - so the partial is
        # dropped instead of waited for.
        timeout = _ABORT_DB_BUSY_TIMEOUT_MS if urgent else _DB_BUSY_TIMEOUT_MS
        if partial:
            try:
                # Always truncated=True: a kept partial is by definition text
                # the model never finished saying - whether the reader hit
                # Stop or the provider dropped the connection, the sentence
                # is cut, and complete_stream never ran its own finish_reason
                # check on a stream that did not end cleanly enough to ask.
                _insert_assistant_message(
                    chat_id, model_id_stripped, partial, user_msg_id,
                    busy_timeout_ms=timeout, truncated=True,
                    turn_vars=routing["module_vars"],
                )
                logger.info(
                    "Streaming completion ended early; partial persisted: "
                    "chat_id=%d", chat_id,
                )
                return True
            except StaleExchangeError:
                # Chat cleared/deleted while streaming - the partial has no
                # user turn to attach to; discarding it IS the correct
                # outcome, and there is no user row left to remove either.
                logger.info(
                    "Streaming completion ended early; exchange stale, partial "
                    "discarded: chat_id=%d", chat_id,
                )
                return False
        _delete_message_row(chat_id, user_msg_id, busy_timeout_ms=timeout)
        logger.info(
            "Streaming completion ended early; no partial: chat_id=%d", chat_id,
        )
        return False

    return StreamingResponse(
        _stream_exchange(
            context_notes=context_notes,
            private_endpoints=routing["private_endpoints"],
            chat_id=chat_id,
            model_id=model_id_stripped,
            label="completion",
            body=body,
            messages=messages,
            gen_params=filtered_gen_params,
            provider_dict=provider_dict,
            first_event={"type": "user_message", "message": user_message},
            notices=notices,
            finalize=finalize,
            modalities=modalities,
            rescue=rescue,
        ),
        media_type="text/event-stream", headers=_SSE_HEADERS,
    )


# ---------------------------------------------------------------------------
# POST /chats/{chat_id}/messages/{message_id}/regenerate
# ---------------------------------------------------------------------------

class RegenerateRequest(SpeakOptions):
    model_config = ConfigDict(extra="ignore")

    model_id: str
    generation_params: GenerationParams = Field(default_factory=GenerationParams)
    provider: ProviderPolicy = Field(default_factory=ProviderPolicy)
    persona_id: int | None = None
    context_budget_tokens: int | None = Field(default=None, ge=512, le=2_000_000)

    @field_validator("model_id")
    @classmethod
    def model_id_must_not_be_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("model_id_required")
        return v.strip()


def _validate_regenerate_target(
    chat_id: int, message_id: int,
) -> tuple[dict, int, int]:
    """Validate the regenerate target; return (user_row, anchor, active_id).

    Variant-aware: the target is valid when its GROUP is the chat's last
    ACTIVE group - comparing raw MAX(id) would break the moment one inactive
    sibling exists (the newest id may be a deactivated variant).

    anchor    - COALESCE(variant_group, id) of the target (group key)
    active_id - the group's currently-active row id
    Raises HTTPException(404/422) with the original stable codes.
    """
    with get_db() as con:
        chat_row = con.execute(
            "SELECT id, character_id FROM chats WHERE id = ?", (chat_id,)
        ).fetchone()
        if chat_row is None:
            raise HTTPException(404, "chat_not_found")

        msg_row = con.execute(
            "SELECT id, role, chat_id, variant_group "
            "FROM messages WHERE id = ? AND chat_id = ?",
            (message_id, chat_id),
        ).fetchone()
        if msg_row is None:
            raise HTTPException(404, "message_not_found")

        if msg_row["role"] != "assistant":
            raise HTTPException(422, "not_last_assistant_message")

        anchor = msg_row["variant_group"] or msg_row["id"]

        if last_active_anchor(con, chat_id) != anchor:
            raise HTTPException(422, "not_last_assistant_message")

        active_row = con.execute(
            "SELECT id FROM messages "
            "WHERE chat_id = ? AND COALESCE(variant_group, id) = ? AND active = 1",
            (chat_id, anchor),
        ).fetchone()
        active_id = active_row["id"] if active_row else message_id

        # The anchor is the group's smallest id, so `id < anchor` excludes the
        # whole group while keeping everything before it.
        user_msg = con.execute(
            "SELECT id, chat_id, role, content, created_at, variant_group, active "
            "FROM messages "
            "WHERE chat_id = ? AND role = 'user' AND id < ? AND active = 1 "
            "ORDER BY id DESC LIMIT 1",
            (chat_id, anchor),
        ).fetchone()
        if user_msg is None:
            raise HTTPException(422, "no_preceding_user_message")

    return dict(user_msg), anchor, active_id


def _regenerate_target_sync(chat_id: int, message_id: int) -> tuple:
    """Worker-thread body (audit KÖK 8): the target lookup AND its images.

    Both regenerate handlers ran these two back to back on the event loop, and
    the validator is the heavier of the pair - a chat lookup, a message lookup,
    an active-anchor scan and a MAX(id) scan, each paying its own SQLCipher
    open. load_for_messages was the line the audit named, but fixing only that
    one would have left the bigger blocker sitting directly above it.

    They are folded into ONE hop rather than wrapped separately on purpose:
    two hops would open a new await point between the guard and the read it
    guards, which is a window that does not exist today.

    The user turn is excluded from history (history_before_id) and re-sent as
    the current turn, so its linked images must ride along again, exactly like
    a fresh send - otherwise a regenerate answers without ever seeing them.
    """
    row, anchor, prev_active_id = _validate_regenerate_target(chat_id, message_id)
    atts = load_for_messages([row["id"]]).get(row["id"], [])
    return row, anchor, prev_active_id, atts


@router.post("/{chat_id}/messages/{message_id}/regenerate")
async def regenerate_message(chat_id: int, message_id: int,
                             body: RegenerateRequest) -> dict:
    """Regenerate the latest assistant message.

    1. Validate the target is the last message AND role == assistant
    2. Find the preceding user message (unchanged)
    3. Call provider with history BEFORE that user message + the user message
       re-appended (history_before_id prevents a duplicated user turn)
    4. Only after the provider succeeds: atomically delete the old assistant
       message and insert the new one. A provider failure therefore never
       loses the existing assistant message.
    5. Return existing user_message row + new assistant_message row
    """
    (existing_user_row, anchor, prev_active_id,
     user_atts) = await anyio.to_thread.run_sync(
        _regenerate_target_sync, chat_id, message_id,
    )
    user_text = existing_user_row["content"]

    # Call provider first - the old assistant variants stay untouched until
    # the new one exists. history_before_id excludes the user message (it is
    # re-appended as the current turn) and the whole target variant group.
    (assistant_text, notices, generated, truncated,
     retried, turn_vars) = await _call_provider_for_chat(
        chat_id=chat_id,
        model_id=body.model_id,
        user_message_text=user_text,
        generation_params=body.generation_params,
        provider=body.provider,
        persona_id=body.persona_id,
        context_budget_tokens=body.context_budget_tokens,
        history_before_id=existing_user_row["id"],
        pending_attachments=user_atts,
        generation="regenerate",
    )

    # Atomic variant append on a worker thread (v1.1 FB2b): the swap txn must
    # not block the event loop while other SSE streams are live. Nothing is
    # ever deleted - old variants stay navigable.
    model_id_stripped = body.model_id
    loop = asyncio.get_running_loop()

    def _append_and_notify():
        result = _append_variant(
            chat_id, anchor, assistant_text, model_id_stripped, prev_active_id,
            generated, truncated, turn_vars=turn_vars,
        )
        _notify_notebook_from_thread(loop, chat_id)
        return result

    try:
        # Notify from inside the thread - see the comment on
        # `_notify_notebook_from_thread` for why a bare cancel can otherwise
        # leave the write committed with the notebook never told.
        result = await anyio.to_thread.run_sync(_append_and_notify)
    except RegenerateConflictError:
        raise HTTPException(409, "regenerate_conflict")
    except Exception:
        logger.warning("DB write failed after successful regeneration: chat_id=%d", chat_id)
        raise

    asst_row = result["asst_row"]
    variant_count = result["variant_count"]
    logger.info(
        "Regenerate success: chat_id=%d existing_user=%d new_variant=%d group=%d",
        chat_id, existing_user_row["id"], asst_row["id"], anchor,
    )

    return {
        "chat_id": chat_id,
        "model_id": model_id_stripped,
        "user_message": _msg_to_dict(existing_user_row, user_atts),
        "assistant_message": _msg_to_dict(
            asst_row, result["generated_rows"],
            variant_index=variant_count - 1, variant_count=variant_count,
        ),
        "deactivated_message_id": result["deactivated_id"],
        "notices": _with_refusals(notices, result["refused_images"]),
        **({"retried": retried} if retried else {}),
    }


# ---------------------------------------------------------------------------
# POST /chats/{chat_id}/messages/{message_id}/regenerate/stream
# ---------------------------------------------------------------------------

@router.post("/{chat_id}/messages/{message_id}/regenerate/stream")
async def regenerate_message_stream(chat_id: int, message_id: int,
                                    body: RegenerateRequest) -> StreamingResponse:
    """Streaming variant of /regenerate (SSE).

    Event sequence mirrors /complete/stream: user_message (the EXISTING
    preceding user row) → delta* → done | error.

    Persistence semantics protect existing content:
      - The old assistant message is only removed in the atomic swap after the
        new text fully streamed. Provider failure → error event, old message
        intact.
      - Client abort discards the partial and keeps the old message (unlike
        /complete/stream, where the partial is kept - here keeping the partial
        would destroy a complete existing reply).
      - If the chat changed while streaming (target no longer last), a
        regenerate_conflict error event is emitted and nothing is modified.
    """
    (existing_user_row, anchor, prev_active_id,
     user_atts) = await anyio.to_thread.run_sync(
        _regenerate_target_sync, chat_id, message_id,
    )
    user_text = existing_user_row["content"]
    model_id_stripped = body.model_id

    (messages, filtered_gen_params, provider_dict, notices,
     modalities, context_notes, routing) = await _prepare_completion(
        chat_id=chat_id,
        model_id=body.model_id,
        user_message_text=user_text,
        generation_params=body.generation_params,
        provider=body.provider,
        persona_id=body.persona_id,
        context_budget_tokens=body.context_budget_tokens,
        history_before_id=existing_user_row["id"],
        pending_attachments=user_atts,
        generation="regenerate",
    )

    logger.info(
        "Streaming regenerate start: chat_id=%d target_msg_id=%d",
        chat_id, message_id,
    )

    finalize_loop = asyncio.get_running_loop()

    async def finalize(
        full_text: str, generated: list[tuple[bytes, str, int, int]],
        truncated: bool,
    ) -> dict:
        # Atomic variant append on a worker thread (v1.1 FB2b): the swap txn
        # runs off the loop so other live SSE streams do not stall. The await
        # lands BETWEEN yields (holding no connection), preserving the "SSE
        # yields happen outside the connection context" invariant.
        #
        # Notify from inside the thread: see the comment in
        # `_stream_exchange` on `finalizing` for why a bare Task.cancel() can
        # leave this commit going on to succeed with nobody left to tell the
        # notebook.
        def _append_and_notify():
            row = _append_variant(
                chat_id, anchor, full_text, model_id_stripped, prev_active_id,
                generated, truncated, turn_vars=routing["module_vars"],
            )
            _notify_notebook_from_thread(finalize_loop, chat_id)
            return row

        result = await anyio.to_thread.run_sync(_append_and_notify)
        variant_count = result["variant_count"]
        return {
            "user_message": _msg_to_dict(existing_user_row, user_atts),
            "assistant_message": _msg_to_dict(
                result["asst_row"], result["generated_rows"],
                variant_index=variant_count - 1,
                variant_count=variant_count,
            ),
            "deactivated_message_id": result["deactivated_id"],
        }

    return StreamingResponse(
        _stream_exchange(
            context_notes=context_notes,
            private_endpoints=routing["private_endpoints"],
            chat_id=chat_id,
            model_id=model_id_stripped,
            label="regenerate",
            body=body,
            messages=messages,
            gen_params=filtered_gen_params,
            provider_dict=provider_dict,
            first_event={
                "type": "user_message",
                "message": _msg_to_dict(existing_user_row, user_atts),
            },
            notices=notices,
            finalize=finalize,
            modalities=modalities,
            # No rescue: nothing is written until the swap, so an interrupted
            # regenerate has nothing to undo - and keeping ITS partial would
            # destroy the complete existing reply it was meant to replace.
        ),
        media_type="text/event-stream", headers=_SSE_HEADERS,
    )


# ---------------------------------------------------------------------------
# POST /chats/{chat_id}/messages/{message_id}/edit (+ /edit/stream) - v1.1 C3
# ---------------------------------------------------------------------------

class EditRequest(RegenerateRequest):
    """Regenerate's payload plus the replacement user text."""

    message: str

    @field_validator("message")
    @classmethod
    def message_must_not_be_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("message_required")
        return v  # preserve original; stripped only where persisted/sent


def _validate_edit_target(chat_id: int, message_id: int) -> tuple[dict, int]:
    """Validate the edit target and capture the I6 concurrency snapshot.

    Any USER row is editable (no last-group restriction - editing an older
    turn deliberately rewrites everything after it). Returns
    (user_row_dict incl. updated_at, chat_tail_id) - the snapshot the final
    swap must still observe, or it refuses with edit_conflict.

    Stable error codes: chat_not_found, message_not_found, not_editable.
    """
    with get_db() as con:
        chat_row = con.execute(
            "SELECT id FROM chats WHERE id = ?", (chat_id,)
        ).fetchone()
        if chat_row is None:
            raise HTTPException(404, "chat_not_found")

        row = con.execute(
            "SELECT id, chat_id, role, content, created_at, "
            "COALESCE(updated_at, created_at) AS updated_at, "
            "variant_group, active "
            "FROM messages WHERE id = ? AND chat_id = ?",
            (message_id, chat_id),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "message_not_found")
        if row["role"] != "user":
            raise HTTPException(422, "not_editable")

        tail_id = con.execute(
            "SELECT MAX(id) AS m FROM messages WHERE chat_id = ?", (chat_id,)
        ).fetchone()["m"]

    return dict(row), tail_id


def _finalize_edit(
    chat_id: int,
    message_id: int,
    new_content: str,
    assistant_text: str,
    model_id: str,
    expected_updated_at: str,
    expected_content: str,
    expected_tail_id: int,
    generated_images: list[tuple[bytes, str, int, int]] | None = None,
    truncated: bool = False,
    turn_vars: str | None = None,
) -> dict:
    """Atomic edit swap. Runs in a WORKER THREAD and opens its own
    connection there (I7: a connection never crosses threads; guard + sweep +
    update + insert + commit all live in this one BEGIN IMMEDIATE txn).

    Optimistic concurrency (I6): the user row must be byte-identical to the
    validate-time snapshot AND the chat's tail unchanged - a concurrent edit,
    delete, send or clear means the provider answered a stale question, so
    NOTHING is written and EditConflictError raises (context manager rolls
    the txn back). Attachments of swept rows are removed in the same txn
    (E6); the edited row's own attachments are untouched - a content UPDATE
    does not break the message_id link.
    """
    with get_db() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT content, COALESCE(updated_at, created_at) AS updated_at "
            "FROM messages WHERE id = ? AND chat_id = ? AND role = 'user'",
            (message_id, chat_id),
        ).fetchone()
        if (
            row is None
            or row["content"] != expected_content
            or row["updated_at"] != expected_updated_at
        ):
            raise EditConflictError()
        tail = con.execute(
            "SELECT MAX(id) AS m FROM messages WHERE chat_id = ?", (chat_id,)
        ).fetchone()["m"]
        if tail != expected_tail_id:
            raise EditConflictError()

        # Sweep everything after the edited turn. Plain `id >` is safe: user
        # rows have no variant siblings, so nothing earlier can belong to a
        # swept group.
        swept = [r["id"] for r in con.execute(
            "SELECT id FROM messages WHERE chat_id = ? AND id > ?",
            (chat_id, message_id),
        ).fetchall()]
        delete_for_messages(con, swept)  # rows + orphan blobs, same txn (E6)
        spoken_swept = list(swept)
        # The sixth. Editing a message discards everything after it, which
        # is the same shape as deleting a turn: every note taken from those
        # messages goes, accepted or not, because `evidence` holds the
        # message's own words verbatim and the point of replacing the wording
        # is that the old wording stops existing.
        # The edited message ITSELF goes in the list, not only what came
        # after it. Its text is about to be rewritten, and the reading record
        # covering it must roll back or the new wording is never extracted
        # while notes distilled from the old wording stay accepted.
        notebook_store.forget_proposals_from_messages(
            con, [*swept, message_id])
        deleted = con.execute(
            "DELETE FROM messages WHERE chat_id = ? AND id > ?",
            (chat_id, message_id),
        ).rowcount

        con.execute(
            "UPDATE messages SET content = ?, updated_at = datetime('now') "
            "WHERE id = ?",
            (new_content, message_id),
        )
        cur = con.execute(
            "INSERT INTO messages (chat_id, role, content, truncated) "
            "VALUES (?, 'assistant', ?, ?)",
            (chat_id, assistant_text, int(truncated)),
        )
        asst_msg_id = cur.lastrowid
        pm_variables.save(con, chat_id, asst_msg_id, turn_vars)
        generated_rows, refused = _store_generated(con, generated_images,
                                                   asst_msg_id, chat_id)
        con.execute(
            "UPDATE chats SET model_id = ?, updated_at = datetime('now') "
            "WHERE id = ?",
            (model_id, chat_id),
        )

        user_row = con.execute(
            "SELECT id, chat_id, role, content, created_at, variant_group, "
            "active, truncated FROM messages WHERE id = ?",
            (message_id,),
        ).fetchone()
        asst_row = con.execute(
            "SELECT id, chat_id, role, content, created_at, variant_group, "
            "active, truncated FROM messages WHERE id = ?",
            (asst_msg_id,),
        ).fetchone()

    user_atts = load_for_messages([message_id]).get(message_id, [])
    # After the `with`, so the swap has committed. An edit sweeps every
    # message after the one it replaces, and this path removed those rows and
    # left their recordings in the audio cache.
    audio_sweep.forget_spoken_audio(spoken_swept)
    return {
        "user_message": _msg_to_dict(user_row, user_atts),
        "assistant_message": _msg_to_dict(asst_row, generated_rows),
        "deleted_count": deleted,
        "refused_images": refused,
    }


def _edit_target_sync(chat_id: int, message_id: int) -> tuple:
    """Worker-thread body (audit KÖK 8). See _regenerate_target_sync: same
    pairing, same reason for folding the two calls into one hop.

    The edited turn is re-sent as the current turn, so its linked images ride
    along again.
    """
    user_row, tail_id = _validate_edit_target(chat_id, message_id)
    atts = load_for_messages([message_id]).get(message_id, [])
    return user_row, tail_id, atts


@router.post("/{chat_id}/messages/{message_id}/edit")
async def edit_message(chat_id: int, message_id: int, body: EditRequest) -> dict:
    """Edit a user message: provider-first, then one atomic swap.

    The tail after the edited turn is only removed once the NEW reply exists
    (regenerate's data-protection law, not send's persist-first): provider
    failure or conflict leaves every existing row and attachment intact.
    """
    user_row, tail_id, user_atts = await anyio.to_thread.run_sync(
        _edit_target_sync, chat_id, message_id,
    )
    new_content = body.message.strip()

    (assistant_text, notices, generated, truncated,
     retried, turn_vars) = await _call_provider_for_chat(
        chat_id=chat_id,
        model_id=body.model_id,
        user_message_text=new_content,
        generation_params=body.generation_params,
        provider=body.provider,
        persona_id=body.persona_id,
        context_budget_tokens=body.context_budget_tokens,
        history_before_id=message_id,
        pending_attachments=user_atts,
    )

    loop = asyncio.get_running_loop()

    def _finalize_and_notify():
        row = _finalize_edit(
            chat_id, message_id, new_content, assistant_text, body.model_id,
            user_row["updated_at"], user_row["content"], tail_id, generated,
            truncated, turn_vars=turn_vars,
        )
        _notify_notebook_from_thread(loop, chat_id)
        return row

    try:
        # Notify from inside the thread - see `_notify_notebook_from_thread`
        # for why a bare cancel can otherwise leave the write committed with
        # the notebook never told.
        result = await anyio.to_thread.run_sync(_finalize_and_notify)
    except EditConflictError:
        raise HTTPException(409, "edit_conflict")

    logger.info(
        "Edit success: chat_id=%d user_msg_id=%d new_asst_id=%d swept=%d",
        chat_id, message_id, result["assistant_message"]["id"],
        result["deleted_count"],
    )
    return {
        "chat_id": chat_id,
        "model_id": body.model_id,
        "user_message": result["user_message"],
        "assistant_message": result["assistant_message"],
        "notices": _with_refusals(notices, result["refused_images"]),
        **({"retried": retried} if retried else {}),
    }


@router.post("/{chat_id}/messages/{message_id}/edit/stream")
async def edit_message_stream(chat_id: int, message_id: int,
                              body: EditRequest) -> StreamingResponse:
    """Streaming variant of /edit (SSE).

    Event sequence mirrors /complete/stream: user_message (the edited row -
    same id, NEW content, its attachments) → delta* → done | error.

    Persistence semantics protect existing content (regenerate's law):
      - Nothing is written until the atomic swap after the full stream. The
        old tail and the pre-edit content survive provider failures intact.
      - Client abort discards the partial - the chat is byte-identical to
        before the edit attempt.
      - If the chat changed while streaming (concurrent edit/send/delete/
        clear), an edit_conflict error event is emitted and nothing changes.
    """
    user_row, tail_id, user_atts = await anyio.to_thread.run_sync(
        _edit_target_sync, chat_id, message_id,
    )
    new_content = body.message.strip()
    model_id_stripped = body.model_id

    (messages, filtered_gen_params, provider_dict, notices,
     modalities, context_notes, routing) = await _prepare_completion(
        chat_id=chat_id,
        model_id=body.model_id,
        user_message_text=new_content,
        generation_params=body.generation_params,
        provider=body.provider,
        persona_id=body.persona_id,
        context_budget_tokens=body.context_budget_tokens,
        history_before_id=message_id,
        pending_attachments=user_atts,
    )

    logger.info(
        "Streaming edit start: chat_id=%d user_msg_id=%d",
        chat_id, message_id,
    )

    finalize_loop = asyncio.get_running_loop()

    async def finalize(
        full_text: str, generated: list[tuple[bytes, str, int, int]],
        truncated: bool,
    ) -> dict:
        # Notify from inside the thread: see the `finalizing` comment in
        # `_stream_exchange` for why a bare Task.cancel() can leave this
        # commit going on to succeed with nobody left to tell the notebook.
        def _finalize_and_notify():
            row = _finalize_edit(
                chat_id, message_id, new_content, full_text, model_id_stripped,
                user_row["updated_at"], user_row["content"], tail_id, generated,
                truncated, turn_vars=routing["module_vars"],
            )
            _notify_notebook_from_thread(finalize_loop, chat_id)
            return row

        result = await anyio.to_thread.run_sync(_finalize_and_notify)
        logger.info(
            "Streaming edit swept %d row(s): chat_id=%d user_msg_id=%d",
            result["deleted_count"], chat_id, message_id,
        )
        return {
            "user_message": result["user_message"],
            "assistant_message": result["assistant_message"],
        }

    return StreamingResponse(
        _stream_exchange(
            context_notes=context_notes,
            private_endpoints=routing["private_endpoints"],
            chat_id=chat_id,
            model_id=model_id_stripped,
            label="edit",
            body=body,
            messages=messages,
            gen_params=filtered_gen_params,
            provider_dict=provider_dict,
            first_event={
                "type": "user_message",
                # Preview of the edited row: same id, replacement content. The
                # DB still holds the OLD content - final truth lands at done.
                "message": {**_msg_to_dict(user_row, user_atts),
                            "content": new_content},
            },
            notices=notices,
            finalize=finalize,
            modalities=modalities,
            # No rescue: the edit writes nothing until the atomic swap, so an
            # interrupted attempt leaves the chat byte-identical to before it.
        ),
        media_type="text/event-stream", headers=_SSE_HEADERS,
    )

