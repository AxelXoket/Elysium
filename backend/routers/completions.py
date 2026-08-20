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

import anyio.to_thread

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from config import (
    SECRET_API_KEY,
    PROVIDER_POLICY,
    CONTEXT_SAFETY_MARGIN,
    CHARS_PER_TOKEN_ESTIMATE,
    IMAGE_TOKEN_ESTIMATE,
    MAX_ATTACHMENTS_PER_MESSAGE,
)
import notebook_store
import voice_tags
from database import get_db
from typing import Literal

from vault_state import VaultLockedError
from tts import stream_hook
from routers import tts_runtime
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
from openrouter import (
    NOTICE_FRAME_DROPPED,
    NOTICE_STREAM_UNFINISHED,
    TRUNCATED_FINISH_REASONS,
    OpenRouterError,
    MODALITIES_WITH_IMAGE,
    finish_reasons,
    image_urls_from,
    validate_and_filter_gen_params,
    get_cached_model_metadata,
    complete,
    complete_stream,
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
    stop: str | list[str] | None = None

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
    # They are NOT accepted from the frontend (extra="ignore" drops them).


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

def _build_system_block(char_row) -> str:
    """Build the system-role message from character fields.

    Sections with empty (stripped) values are omitted.
    Non-empty sections are separated by double newlines.
    """
    sections = []
    # K-31. The character's NAME never reached the model at all - five fields
    # went out and the one word naming who is speaking was not among them. The
    # user's persona has carried its name since the beginning
    # (_build_persona_block below), so the model was told who it was talking
    # TO and not who it was playing.
    #
    # A LABEL, not an instruction, and the persona header's exact twin. "You
    # are {name}." was the alternative and it competes with the card: an
    # author who has already set the voice in system_prompt now has the app
    # talking over them. A header states the fact and leaves the framing to
    # whoever wrote it.
    name = (char_row["name"] or "").strip()
    if name:
        sections.append(f"[Character: {name}]")
    for label, field in [
        ("System Prompt", "system_prompt"),
        ("Description", "description"),
        ("Personality", "personality"),
        ("Scenario", "scenario"),
        ("Example Dialogue", "mes_example"),
    ]:
        value = (char_row[field] or "").strip()
        if value:
            sections.append(f"[{label}]\n{value}")
    return "\n\n".join(sections)


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
    # FAZ 2. Three blocks, three different authorities, and they default to
    # empty so every existing caller keeps its exact behaviour:
    #   boundary_block      - standing rules; counted, never dropped
    #   notebook_user_block - what the person wrote; system channel, by persona
    #   notebook_model_block- what the model wrote; tail, by the PHI
    boundary_block: str = "",
    notebook_user_block: str = "",
    notebook_model_block: str = "",
    # Out-param, same shape as omitted_images: the trim loop counts what it
    # drops so the turn can say so instead of dropping history in silence.
    trimmed_out: list[int] | None = None,
) -> list[dict]:
    """Build the final messages list with context budget truncation.

    history entries may carry an "attachments" list (user messages only);
    pending_attachments belong to the current user_message. Each image costs
    a flat _IMAGE_CHAR_COST in the trim math.

    Raises HTTPException(400, "context_too_large") if even the system block
    plus persona plus the current user message exceeds the available budget.
    """
    # post_history_instruction is appended unconditionally after the trim, so
    # its length must be reserved up front - otherwise a large PHI silently
    # pushes the real payload past the model context. (The frontend estimator
    # already charges PHI to fixed cost; this keeps backend and gauge aligned.)
    phi_chars = len(post_history_instruction.strip()) if post_history_instruction else 0

    available = context_budget_chars - max_tokens_chars
    # The voice-delivery block (V4) is reserved like the PHI: injected
    # unconditionally when voice is on, so unbudgeted it would silently push
    # the payload past the model context on long chats.
    # BEFORE the trim, and this ordering is the whole of it. `system_chars` is
    # what the loop below subtracts from `available` to decide how much history
    # survives; a block added after this sum would be sent anyway and the trim
    # would over-drop by exactly its size, with no counter noticing.
    #
    # Boundaries are counted here too but are NOT in the droppable set - see
    # notebook_store.BoundariesDoNotFit. They are the one block that refuses
    # rather than shrinks.
    notebook_chars = (len(notebook_user_block) + len(notebook_model_block)
                      + len(boundary_block))
    system_chars = (len(system_block) + len(persona_block) + phi_chars
                    + len(voice_block) + notebook_chars)
    user_msg_chars = _entry_chars(user_message, pending_attachments,
                                  include_images)
    min_required = system_chars + user_msg_chars

    if min_required > available:
        # The limits are the one block that refuses instead of shrinking. If
        # they do not fit, generating anyway would mean speaking WITHOUT rules
        # the user believes are in force - and nothing would say so. That is
        # the documented lorebook failure: the budget runs out and entries stop
        # activating while their keywords are right there in the prompt.
        if boundary_block and len(boundary_block) + user_msg_chars > available:
            raise HTTPException(400, "boundaries_do_not_fit")
        raise HTTPException(400, "context_too_large")

    # Trim history from oldest end until it fits
    remaining = available - system_chars - user_msg_chars
    history_chars = sum(
        _entry_chars(m["content"], m.get("attachments"), include_images,
                     role=m["role"])
        for m in history
    )
    # The count exists because the silence did. History has always been trimmed
    # oldest-first with nothing recording it - so a conversation could lose ten
    # turns and the only trace was the frontend gauge. Shipping a notice for the
    # notebook's own truncation while leaving this silent would teach the user
    # that dropped context gets announced, which would then be false.
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

    # Build final list
    blobs = image_blobs or {}
    messages: list[dict] = []

    if system_block:
        messages.append({"role": "system", "content": system_block})

    if persona_block:
        messages.append({"role": "system", "content": persona_block})

    # V4: HOW to speak - injected at call level, invisible to the user, never
    # stored on the character. After the persona (stable identity first),
    # before the history (so the examples read as instruction, not dialogue).
    # Limits before anything the story can argue with. They are standing rules
    # set by the person, not content produced inside the fiction.
    if boundary_block:
        messages.append({"role": "system", "content": boundary_block})

    # What the USER wrote about this chat. Same channel as the persona because
    # it is the same trust class - a person typing into their own app - and the
    # primacy end of the prompt is where a stable fact belongs.
    if notebook_user_block:
        messages.append({"role": "system", "content": notebook_user_block})

    if voice_block:
        messages.append({"role": "system", "content": voice_block})

    for msg in history:
        messages.append({
            "role": msg["role"],
            "content": _content_for(
                msg["content"], msg.get("attachments"), include_images, blobs,
                omitted_images, role=msg["role"],
            ),
        })

    messages.append({
        "role": "user",
        "content": _content_for(
            user_message.strip(), pending_attachments, include_images, blobs,
            omitted_images,
        ),
    })

    # What the MODEL wrote, at the tail beside the post-history instruction
    # rather than up in the system channel with the persona. Same table, same
    # ceiling, lower authority - and the split only means anything because
    # nothing can relabel a row's provenance on the way here.
    #
    # This was budgeted and never appended for one commit: charged for, not
    # sent. A read-only review caught it mid-flight, which is exactly the
    # class of defect that ships looking correct.
    if notebook_model_block:
        messages.append({"role": "system", "content": notebook_model_block})

    phi = post_history_instruction.strip() if post_history_instruction else ""
    if phi:
        messages.append({"role": "system", "content": phi})

    return messages


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


# ---------------------------------------------------------------------------
# Internal: shared provider-call logic (used by complete and regenerate)
# ---------------------------------------------------------------------------

def _load_completion_context(
    chat_id: int, history_before_id: int | None, persona_id: int | None,
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

    history_att = load_for_messages([r["id"] for r in history_rows])
    history = [
        {
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
    }


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
    )
    char_row = ctx["char_row"]
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

    # ── Check API key ─────────────────────────────────────────────────────
    if not ctx["api_key_present"]:
        raise HTTPException(401, "api_key_missing")

    # ── Proxy health check ────────────────────────────────────────────────
    await enforce_proxy_gate()

    # ── Assemble messages ─────────────────────────────────────────────────
    model_id_stripped = model_id  # already stripped by validator
    system_block = _build_system_block(char_row)
    phi = (char_row["post_history_instruction"] or "").strip()

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
    # Unconditional. Guarded on `excluded` being non-empty, the CLEARING half
    # never ran on a turn where nothing was excluded - so once the pressure
    # stopped, rows kept a reason from an earlier turn forever and the panel
    # showed them as "not sent" while they were being sent every single time.
    # The badge inverted its own meaning, which is worse than not having it.
    await anyio.to_thread.run_sync(
        lambda: notebook_store.record_exclusions(
            chat_id, notebook["excluded"]))

    messages = _assemble_messages(
        system_block,
        persona_block,
        history,
        user_message_text,
        phi,
        context_budget_chars,
        max_tokens_chars,
        include_images=include_images,
        pending_attachments=pending_attachments,
        image_blobs=image_blobs,
        # Off the event loop: a cold capability cache walks the models folder,
        # and one slow disk must not stall every in-flight request (audit-2).
        voice_block=await anyio.to_thread.run_sync(voice_tags.voice_block),
        omitted_images=omitted_images,
        boundary_block=notebook["boundary_block"],
        notebook_user_block=notebook["user_block"],
        notebook_model_block=notebook["model_block"],
        trimmed_out=trimmed_out,
    )

    # Both numbers, together, and the second one is the older debt. History has
    # always been trimmed oldest-first with nothing recording it; shipping a
    # count for the notebook alone would teach that dropped context gets
    # announced, which would then be false for the bigger case.
    context_notes = {
        "notebook_sent": notebook["sent"],
        "notebook_total": notebook["total"],
        "history_trimmed": trimmed_out[0] if trimmed_out else 0,
    }

    # ── Validate and filter gen_params ─────────────────────────────────────
    try:
        filtered_gen_params = validate_and_filter_gen_params(
            generation_params.model_dump(exclude_none=True)
        )
    except ValueError:
        raise HTTPException(422, "invalid_gen_params")

    if meta and meta.get("supported_parameters"):
        supported = set(meta["supported_parameters"])
        filtered_gen_params = {
            k: v for k, v in filtered_gen_params.items()
            if k in supported or k == "stop"
        }

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

    return (messages, filtered_gen_params, provider_dict, notices,
            modalities, context_notes)


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
            logger.warning(
                "Refused a remotely hosted generated image (scheme=%s): chat_id=%d",
                exc, chat_id,
            )
        except ValueError as exc:
            rejected += 1
            # The reason is ours (a length, a media type, a decode failure) and
            # carries no model output, so it is safe to log.
            logger.warning("Rejected a generated image: chat_id=%d reason=%s",
                           chat_id, exc)
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
) -> tuple[str, list[dict], list[tuple[bytes, str, int, int]], bool]:
    """Non-streaming provider call: prepare, call OpenRouter, parse.

    Returns (assistant_text, notices, generated_image_bytes, truncated) on
    success. The notices ride back out to the caller for the same reason the
    SSE path emits them: this endpoint has a response body, so "the model
    never saw your picture" has somewhere to go here too, and the two paths
    must not disagree about that (P4). `truncated` is read from THIS choice's
    finish_reason/native_finish_reason - the streaming path has to accumulate
    that across chunks, but a non-streaming reply arrives as one object with
    the field already sitting on it.
    Raises HTTPException on any failure.
    """
    (messages, filtered_gen_params, provider_dict, notices,
     modalities, context_notes) = await _prepare_completion(
        chat_id=chat_id,
        model_id=model_id,
        user_message_text=user_message_text,
        generation_params=generation_params,
        provider=provider,
        persona_id=persona_id,
        context_budget_tokens=context_budget_tokens,
        history_before_id=history_before_id,
        pending_attachments=pending_attachments,
    )

    # ── Call OpenRouter ───────────────────────────────────────────────────
    logger.info("Completion request: chat_id=%d model=%s", chat_id, model_id)

    try:
        raw = await complete(messages, model_id, filtered_gen_params,
                             provider_dict, modalities=modalities)
    except OpenRouterError as exc:
        status, detail = _ERROR_MAP.get(exc.reason, (502, "openrouter_completion_error"))
        raise HTTPException(status, detail)

    # ── Parse response ────────────────────────────────────────────────────
    choices = raw.get("choices")
    if not isinstance(choices, list) or not choices:
        raise HTTPException(502, "invalid_openrouter_completion_response")

    choice = choices[0] if isinstance(choices[0], dict) else {}
    message = choice.get("message")
    if not isinstance(message, dict):
        raise HTTPException(502, "invalid_openrouter_completion_response")
    # Read from the CHOICE, not the message - finish_reason/native_finish_reason
    # are siblings of "message" in the OpenRouter shape, never inside it.
    truncated = bool(finish_reasons(choice) & TRUNCATED_FINISH_REASONS)

    # Images first, because whether the reply is empty depends on the answer.
    # Nothing is fetched here and nothing base64 survives past this call.
    generated: list[tuple[bytes, str, int, int]] = []
    if modalities:
        # Off the loop: this now includes the Pillow decode and re-encode too.
        generated, image_notices = await anyio.to_thread.run_sync(
            _decode_generated_images, image_urls_from(message), chat_id,
        )
        notices.extend(image_notices)

    content = message.get("content")
    text = content if isinstance(content, str) else ""
    # ONE judgement, where there used to be two consecutive refusals. A reply
    # that is only delivery tags renders as a permanently empty bubble once they
    # are stripped (audit-2), so "visible" is the right measure - but a reply
    # that is a PICTURE and no words is not empty, it is a picture. The four
    # places that judge this must agree on the same bytes; see _visible_view.
    visible = _visible_view(text).strip()
    if not visible and not generated:
        raise HTTPException(502, "invalid_openrouter_completion_response")

    logger.info("Gen params keys: %s", list(filtered_gen_params.keys()))
    return text, notices, generated, truncated


# ---------------------------------------------------------------------------
# POST /{chat_id}/complete
# ---------------------------------------------------------------------------

def _persist_exchange_sync(
    chat_id: int, model_id: str, user_text: str, assistant_text: str,
    attachment_ids: list[int],
    generated_images: list[tuple[bytes, str, int, int]] | None = None,
    truncated: bool = False,
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

    assistant_text, notices, generated, truncated = await _call_provider_for_chat(
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
            body.attachments, generated, truncated,
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


class StaleExchangeError(Exception):
    """The exchange's user row (or the chat itself) vanished mid-stream -
    the user cleared/deleted while text streamed. Finalizing would insert an
    orphan assistant row and "resurrect" the emptied chat. (v1.1 H12/I9.)"""


def _insert_assistant_message(
    chat_id: int, model_id: str, text: str, user_msg_id: int,
    busy_timeout_ms: int = 15000,
    generated_images: list[tuple[bytes, str, int, int]] | None = None,
    truncated: bool = False,
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
                rate=getattr(body, "speak_rate", None)),
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
        async for delta in complete_stream(
            messages, model_id, gen_params, provider_dict,
            modalities=modalities,
            on_image=image_urls.append if modalities else None,
            # A SEPARATE queue, not `notices`: those were already emitted
            # above, before the first delta. These arrive during the stream
            # and are drained below.
            on_notice=_note,
            on_finish=_on_finish,
        ):
            parts.append(delta)                      # RAW - storage
            voice.feed(delta)                        # RAW - the tags are
            # what make the delivery worth hearing, and only the raw text
            # has them; the client never sees this view.
            shown = stripper.feed(delta) if stripper else delta
            if shown:
                yield _sse_event({"type": "delta", "content": shown})
            while late_notices:
                yield _sse_event(late_notices.pop(0))
            for event in voice.events():             # never waits
                yield _sse_event(event)

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
        logger.info(
            "Streaming %s success: chat_id=%d asst_msg_id=%s",
            label, chat_id, done.get("assistant_message", {}).get("id"),
        )
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
        status, detail = _provider_error_status(exc.reason)
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
     modalities, context_notes) = await _prepare_completion(
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
        "Streaming completion start: chat_id=%d model=%s user_msg_id=%d",
        chat_id, model_id_stripped, user_msg_id,
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
                generated, truncated,
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
    assistant_text, notices, generated, truncated = await _call_provider_for_chat(
        chat_id=chat_id,
        model_id=body.model_id,
        user_message_text=user_text,
        generation_params=body.generation_params,
        provider=body.provider,
        persona_id=body.persona_id,
        context_budget_tokens=body.context_budget_tokens,
        history_before_id=existing_user_row["id"],
        pending_attachments=user_atts,
    )

    # Atomic variant append on a worker thread (v1.1 FB2b): the swap txn must
    # not block the event loop while other SSE streams are live. Nothing is
    # ever deleted - old variants stay navigable.
    model_id_stripped = body.model_id
    loop = asyncio.get_running_loop()

    def _append_and_notify():
        result = _append_variant(
            chat_id, anchor, assistant_text, model_id_stripped, prev_active_id,
            generated, truncated,
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
     modalities, context_notes) = await _prepare_completion(
        chat_id=chat_id,
        model_id=body.model_id,
        user_message_text=user_text,
        generation_params=body.generation_params,
        provider=body.provider,
        persona_id=body.persona_id,
        context_budget_tokens=body.context_budget_tokens,
        history_before_id=existing_user_row["id"],
        pending_attachments=user_atts,
    )

    logger.info(
        "Streaming regenerate start: chat_id=%d model=%s target_msg_id=%d",
        chat_id, model_id_stripped, message_id,
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
                generated, truncated,
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
        # The sixth. Editing a message discards everything after it, which is
        # the same shape as deleting a turn: accepted notes stay, unaccepted
        # suggestions from the discarded turns go, and every survivor lets go
        # of its reference so the delete can proceed.
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

    assistant_text, notices, generated, truncated = await _call_provider_for_chat(
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
            truncated,
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
     modalities, context_notes) = await _prepare_completion(
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
        "Streaming edit start: chat_id=%d model=%s user_msg_id=%d",
        chat_id, model_id_stripped, message_id,
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
                truncated,
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

