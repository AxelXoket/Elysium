"""routers/completions.py -- Text-only OpenRouter completion endpoint (Phase 5B).

Route:
    POST /chats/{chat_id}/complete  - send a user message, get assistant response.

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
    - Text-only, non-streaming.
    - No tools, tool_choice, response_format, image_url, file, reasoning.
    - No streaming (stream: true).
    - No local models.
"""

import asyncio
import json
import logging

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
from database import get_db, get_setting
from vault_state import VaultLockedError
from secrets_service import get_secret
from attachments_service import (
    AttachmentError,
    validate_staged,
    link_attachments,
    load_for_messages,
    build_image_part,
    prefetch_blobs,
    to_api as attachment_to_api,
)
from openrouter import (
    OpenRouterError,
    validate_and_filter_gen_params,
    get_cached_model_metadata,
    complete,
    complete_stream,
)
from proxy_health import check_proxy_health

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


class CompleteRequest(BaseModel):
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


def _entry_chars(text: str, attachments: list[dict] | None) -> int:
    """Budget length of one message: text chars + flat per-image cost."""
    return len(text) + len(attachments or []) * _IMAGE_CHAR_COST


def _content_for(text: str, attachments: list[dict] | None,
                 include_images: bool,
                 image_blobs: dict[str, bytes]) -> str | list[dict]:
    """Plain string content, or OpenRouter content parts when images ride along.

    Image parts are emitted only when the model accepts image input; for
    text-only models the images are silently omitted (documented in the
    contract) so old multimodal history never breaks a text model.

    image_blobs is the PREFETCHED sha->bytes map (built off the event loop in
    _prepare_completion); assembly itself never touches the DB.
    """
    if not include_images or not attachments:
        return text
    parts: list[dict] = [{"type": "text", "text": text}]
    for row in attachments:
        part = build_image_part(row, image_blobs)
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
    system_chars = len(system_block) + len(persona_block) + phi_chars
    user_msg_chars = _entry_chars(user_message, pending_attachments)
    min_required = system_chars + user_msg_chars

    if min_required > available:
        raise HTTPException(400, "context_too_large")

    # Trim history from oldest end until it fits
    remaining = available - system_chars - user_msg_chars
    history_chars = sum(
        _entry_chars(m["content"], m.get("attachments")) for m in history
    )
    while history_chars > remaining and history:
        dropped = history.pop(0)
        history_chars -= _entry_chars(dropped["content"], dropped.get("attachments"))

    # Build final list
    blobs = image_blobs or {}
    messages: list[dict] = []

    if system_block:
        messages.append({"role": "system", "content": system_block})

    if persona_block:
        messages.append({"role": "system", "content": persona_block})

    for msg in history:
        messages.append({
            "role": msg["role"],
            "content": _content_for(
                msg["content"], msg.get("attachments"), include_images, blobs,
            ),
        })

    messages.append({
        "role": "user",
        "content": _content_for(
            user_message.strip(), pending_attachments, include_images, blobs,
        ),
    })

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


def _msg_to_dict(
    row,
    attachments: list[dict] | None = None,
    variant_index: int | None = None,
    variant_count: int | None = None,
) -> dict:
    """Convert a message DB row to the API response shape.

    variant_group/active are read defensively (older SELECTs may not include
    them); variant_index/variant_count are attached only when the caller
    computed them - the frontend schema defaults the rest.
    """
    keys = row.keys() if hasattr(row, "keys") else []
    d = {
        "id":         row["id"],
        "chat_id":    row["chat_id"],
        "role":       row["role"],
        "content":    row["content"],
        "created_at": row["created_at"],
        "attachments": [attachment_to_api(a) for a in (attachments or [])],
        "variant_group": row["variant_group"] if "variant_group" in keys else None,
        "active": bool(row["active"]) if "active" in keys else True,
    }
    if variant_index is not None:
        d["variant_index"] = variant_index
    if variant_count is not None:
        d["variant_count"] = variant_count
    return d


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
    mods = (meta or {}).get("input_modalities") or []
    if meta is not None and mods and "image" not in mods:
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


# ---------------------------------------------------------------------------
# Internal: shared provider-call logic (used by complete and regenerate)
# ---------------------------------------------------------------------------

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
) -> tuple[list[dict], dict, dict]:
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

    Returns (messages, filtered_gen_params, provider_dict).
    Raises HTTPException on any failure.
    """
    # ── Fetch chat + character from DB ────────────────────────────────────
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

    # ── Check API key ─────────────────────────────────────────────────────
    api_key = get_secret(SECRET_API_KEY)
    if not api_key:
        raise HTTPException(401, "api_key_missing")

    # ── Proxy health check ────────────────────────────────────────────────
    proxy_required = get_setting("proxy_required", "0") == "1"
    if proxy_required:
        health = await check_proxy_health()
        if not health["healthy"]:
            reason = health.get("reason", "proxy_unhealthy")
            raise HTTPException(503, reason)

    # ── Assemble messages ─────────────────────────────────────────────────
    model_id_stripped = model_id  # already stripped by validator
    system_block = _build_system_block(char_row)
    phi = (char_row["post_history_instruction"] or "").strip()

    # ── Resolve persona ───────────────────────────────────────────────────
    persona_block = ""
    resolved_persona_id = persona_id
    if resolved_persona_id is None:
        sel = get_setting("selected_persona_id")
        if sel:
            try:
                resolved_persona_id = int(sel)
            except ValueError:
                # Corrupted setting must not 500 the request; treat as unset.
                logger.warning("Ignoring non-integer selected_persona_id setting.")
                resolved_persona_id = None

    if resolved_persona_id is not None:
        with get_db() as con:
            persona_row = con.execute(
                "SELECT id, description FROM personas WHERE id = ?",
                (resolved_persona_id,),
            ).fetchone()
        if persona_row is None:
            raise HTTPException(404, "persona_not_found")
        desc = (persona_row["description"] or "").strip()
        if desc:
            persona_block = desc

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

    input_modalities = (meta or {}).get("input_modalities") or []
    include_images = "image" in input_modalities

    # E6: prefetch every needed blob in ONE query OFF the event loop -
    # per-image DB reads during assembly would stall live SSE streams.
    # Newest-first order so the RAM cap keeps the most recent images when a
    # pathological history exceeds IMAGE_PAYLOAD_MAX_TOTAL_BYTES.
    image_blobs: dict[str, bytes] = {}
    if include_images:
        shas_newest_first = [a["sha256"] for a in (pending_attachments or [])]
        for msg in reversed(history):
            for att in msg.get("attachments") or []:
                shas_newest_first.append(att["sha256"])
        if shas_newest_first:
            image_blobs = await anyio.to_thread.run_sync(
                prefetch_blobs, shas_newest_first
            )

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
    )

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

    return messages, filtered_gen_params, provider_dict


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
) -> str:
    """Non-streaming provider call: prepare, call OpenRouter, parse.

    Returns the assistant text string on success.
    Raises HTTPException on any failure.
    """
    messages, filtered_gen_params, provider_dict = await _prepare_completion(
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
        raw = await complete(messages, model_id, filtered_gen_params, provider_dict)
    except OpenRouterError as exc:
        status, detail = _ERROR_MAP.get(exc.reason, (502, "openrouter_completion_error"))
        raise HTTPException(status, detail)

    # ── Parse response ────────────────────────────────────────────────────
    choices = raw.get("choices")
    if not isinstance(choices, list) or not choices:
        raise HTTPException(502, "invalid_openrouter_completion_response")

    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        raise HTTPException(502, "invalid_openrouter_completion_response")

    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise HTTPException(502, "invalid_openrouter_completion_response")

    logger.info("Gen params keys: %s", list(filtered_gen_params.keys()))
    return content  # assistant text


# ---------------------------------------------------------------------------
# POST /{chat_id}/complete
# ---------------------------------------------------------------------------

@router.post("/{chat_id}/complete")
async def complete_chat(chat_id: int, body: CompleteRequest) -> dict:
    """Send a user message and receive an assistant response via OpenRouter."""

    pending_rows = _validate_request_attachments(body.attachments, body.model_id)

    assistant_text = await _call_provider_for_chat(
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
    user_message_stripped = body.message.strip()
    model_id_stripped = body.model_id

    try:
        with get_db() as con:
            cur = con.execute(
                "INSERT INTO messages (chat_id, role, content) VALUES (?, 'user', ?)",
                (chat_id, user_message_stripped),
            )
            user_msg_id = cur.lastrowid

            linked_rows: list[dict] = []
            if body.attachments:
                linked_rows = link_attachments(con, body.attachments, user_msg_id)

            cur = con.execute(
                "INSERT INTO messages (chat_id, role, content) VALUES (?, 'assistant', ?)",
                (chat_id, assistant_text),
            )
            asst_msg_id = cur.lastrowid

            con.execute(
                "UPDATE chats SET model_id = ?, updated_at = datetime('now') WHERE id = ?",
                (model_id_stripped, chat_id),
            )

            user_row = con.execute(
                "SELECT id, chat_id, role, content, created_at FROM messages WHERE id = ?",
                (user_msg_id,),
            ).fetchone()
            asst_row = con.execute(
                "SELECT id, chat_id, role, content, created_at FROM messages WHERE id = ?",
                (asst_msg_id,),
            ).fetchone()
    except Exception:
        logger.warning("DB write failed after successful completion: chat_id=%d", chat_id)
        raise

    logger.info(
        "Completion success: chat_id=%d user_msg_id=%d asst_msg_id=%d",
        chat_id, user_msg_id, asst_msg_id,
    )

    return {
        "chat_id": chat_id,
        "model_id": model_id_stripped,
        "user_message": _msg_to_dict(user_row, linked_rows),
        "assistant_message": _msg_to_dict(asst_row),
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


def _error_event(reason: str) -> str:
    status, detail = _ERROR_MAP.get(reason, (502, "openrouter_completion_error"))
    return _sse_event({"type": "error", "status": status, "code": detail})


def _delete_message_row(chat_id: int, message_id: int) -> None:
    """Best-effort cleanup of a single message row (sync, abort-safe).

    Linked attachments are UNLINKED first (back to staged) - both because
    foreign_keys=ON would otherwise block the delete, and because the client
    keeps the staged ids for a retry after a failed send.
    """
    try:
        with get_db() as con:
            con.execute(
                "UPDATE attachments SET message_id = NULL WHERE message_id = ?",
                (message_id,),
            )
            con.execute(
                "DELETE FROM messages WHERE id = ? AND chat_id = ?",
                (message_id, chat_id),
            )
    except Exception:
        logger.warning(
            "Cleanup delete failed: chat_id=%d message_id=%d", chat_id, message_id,
        )


def _insert_assistant_message(chat_id: int, model_id: str, text: str) -> dict:
    """Insert an assistant message + bump the chat; return the API row dict."""
    with get_db() as con:
        cur = con.execute(
            "INSERT INTO messages (chat_id, role, content) VALUES (?, 'assistant', ?)",
            (chat_id, text),
        )
        asst_msg_id = cur.lastrowid
        con.execute(
            "UPDATE chats SET model_id = ?, updated_at = datetime('now') WHERE id = ?",
            (model_id, chat_id),
        )
        row = con.execute(
            "SELECT id, chat_id, role, content, created_at FROM messages WHERE id = ?",
            (asst_msg_id,),
        ).fetchone()
    return _msg_to_dict(row)


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

    messages, filtered_gen_params, provider_dict = await _prepare_completion(
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

    with get_db() as con:
        cur = con.execute(
            "INSERT INTO messages (chat_id, role, content) VALUES (?, 'user', ?)",
            (chat_id, user_message_stripped),
        )
        user_msg_id = cur.lastrowid
        linked_rows: list[dict] = []
        if body.attachments:
            linked_rows = link_attachments(con, body.attachments, user_msg_id)
        con.execute(
            "UPDATE chats SET model_id = ?, updated_at = datetime('now') WHERE id = ?",
            (model_id_stripped, chat_id),
        )
        user_row = con.execute(
            "SELECT id, chat_id, role, content, created_at FROM messages WHERE id = ?",
            (user_msg_id,),
        ).fetchone()
    user_message = _msg_to_dict(user_row, linked_rows)

    logger.info(
        "Streaming completion start: chat_id=%d model=%s user_msg_id=%d",
        chat_id, model_id_stripped, user_msg_id,
    )

    async def event_source():
        parts: list[str] = []
        persisted = False  # guards against a double-insert if the client
        # disconnects exactly at the `done` yield (GeneratorExit lands in the
        # abort handler after the assistant row is already written).
        try:
            yield _sse_event({"type": "user_message", "message": user_message})

            async for delta in complete_stream(
                messages, model_id_stripped, filtered_gen_params, provider_dict,
            ):
                parts.append(delta)
                yield _sse_event({"type": "delta", "content": delta})

            full_text = "".join(parts)
            if not full_text.strip():
                raise OpenRouterError("openrouter_error")

            # Worker thread: the commit between SSE events must not block the
            # loop (other live streams stall for its duration). The abort
            # paths below stay synchronous ON PURPOSE - awaiting inside
            # GeneratorExit/CancelledError handling is fragile, and WAL +
            # synchronous=NORMAL keeps those commits cheap.
            assistant_message = await anyio.to_thread.run_sync(
                _insert_assistant_message, chat_id, model_id_stripped, full_text,
            )
            persisted = True
            logger.info(
                "Streaming completion success: chat_id=%d asst_msg_id=%d",
                chat_id, assistant_message["id"],
            )
            yield _sse_event({
                "type": "done",
                "chat_id": chat_id,
                "model_id": model_id_stripped,
                "user_message": user_message,
                "assistant_message": assistant_message,
            })

        except OpenRouterError as exc:
            await anyio.to_thread.run_sync(_delete_message_row, chat_id, user_msg_id)
            logger.warning(
                "Streaming completion failed: chat_id=%d reason=%s",
                chat_id, exc.reason,
            )
            yield _error_event(exc.reason)

        except (GeneratorExit, asyncio.CancelledError):
            partial = "".join(parts)
            # Cleanup DB writes are wrapped: a vault lock (or any DB error)
            # mid-abort must NOT replace the GeneratorExit we have to re-raise,
            # or it escapes into ASGI finalization as an ugly logged error.
            try:
                if persisted:
                    # Success already committed the assistant row; the
                    # disconnect happened at/after the `done` yield.
                    logger.info(
                        "Streaming completion disconnected after done: chat_id=%d", chat_id,
                    )
                elif partial.strip():
                    _insert_assistant_message(chat_id, model_id_stripped, partial)
                    logger.info(
                        "Streaming completion aborted; partial persisted: chat_id=%d",
                        chat_id,
                    )
                else:
                    _delete_message_row(chat_id, user_msg_id)
                    logger.info(
                        "Streaming completion aborted; no partial: chat_id=%d", chat_id,
                    )
            except Exception:
                logger.warning(
                    "Streaming completion abort cleanup failed: chat_id=%d", chat_id,
                )
            raise

        except VaultLockedError:
            # Vault locked mid-stream (deliberate user action). The reply is
            # lost and the user row cannot be cleaned up while locked; report
            # it honestly instead of a generic 500.
            logger.info("Streaming completion interrupted by vault lock: chat_id=%d", chat_id)
            yield _sse_event({"type": "error", "status": 423, "code": "vault_locked"})

        except Exception:
            try:
                _delete_message_row(chat_id, user_msg_id)
            except Exception:
                pass
            logger.warning("Streaming completion internal error: chat_id=%d", chat_id)
            yield _sse_event({"type": "error", "status": 500, "code": "internal_error"})

    return StreamingResponse(
        event_source(), media_type="text/event-stream", headers=_SSE_HEADERS,
    )


# ---------------------------------------------------------------------------
# POST /chats/{chat_id}/messages/{message_id}/regenerate
# ---------------------------------------------------------------------------

class RegenerateRequest(BaseModel):
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

        last_active = con.execute(
            "SELECT id, variant_group FROM messages "
            "WHERE chat_id = ? AND active = 1 ORDER BY id DESC LIMIT 1",
            (chat_id,),
        ).fetchone()
        if (
            last_active is None
            or (last_active["variant_group"] or last_active["id"]) != anchor
        ):
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
    existing_user_row, anchor, prev_active_id = _validate_regenerate_target(
        chat_id, message_id,
    )
    user_text = existing_user_row["content"]
    # The user turn is excluded from history (history_before_id) and re-sent
    # as the current turn - its linked images must ride along again, exactly
    # like a fresh send, or a regenerate answers without ever seeing them.
    user_atts = load_for_messages([existing_user_row["id"]]).get(
        existing_user_row["id"], [],
    )

    # Call provider first - the old assistant variants stay untouched until
    # the new one exists. history_before_id excludes the user message (it is
    # re-appended as the current turn) and the whole target variant group.
    assistant_text = await _call_provider_for_chat(
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

    # Atomic variant append: deactivate the group, insert the new active row.
    # Nothing is ever deleted - old variants stay navigable.
    model_id_stripped = body.model_id
    try:
        with get_db() as con:
            # Guard + mutate must be ONE write transaction: sqlite3 opens the
            # implicit txn only at the first DML, so a bare SELECT guard runs
            # in autocommit and another connection could delete/append between
            # guard and UPDATE (TOCTOU). BEGIN IMMEDIATE takes the write lock
            # up front, making the guard's snapshot the one the writes see.
            con.execute("BEGIN IMMEDIATE")
            # Guard scope (deliberate): the target's GROUP must still be the
            # last ACTIVE group. A concurrent regenerate of the SAME group
            # passes and simply appends another variant - that is the intended
            # "append a sibling" semantics, not a lost update.
            last_active = con.execute(
                "SELECT id, variant_group FROM messages "
                "WHERE chat_id = ? AND active = 1 ORDER BY id DESC LIMIT 1",
                (chat_id,),
            ).fetchone()
            if (
                last_active is None
                or (last_active["variant_group"] or last_active["id"]) != anchor
            ):
                raise HTTPException(409, "regenerate_conflict")

            # Re-resolve the active sibling AT SWAP TIME: an activate (or a
            # racing regenerate) may have changed it while the provider ran -
            # reporting the pre-call id would desync the client's cache.
            cur_active = con.execute(
                "SELECT id FROM messages WHERE chat_id = ? "
                "AND COALESCE(variant_group, id) = ? AND active = 1",
                (chat_id, anchor),
            ).fetchone()
            deactivated_id = cur_active["id"] if cur_active else prev_active_id

            # Deactivate BEFORE insert - idx_one_active_per_group allows only
            # one active row per group. Also stamps the anchor's variant_group.
            con.execute(
                "UPDATE messages SET variant_group = ?, active = 0 "
                "WHERE chat_id = ? AND COALESCE(variant_group, id) = ? "
                "AND active = 1",
                (anchor, chat_id, anchor),
            )
            cur = con.execute(
                "INSERT INTO messages "
                "(chat_id, role, content, variant_group, active) "
                "VALUES (?, 'assistant', ?, ?, 1)",
                (chat_id, assistant_text, anchor),
            )
            asst_msg_id = cur.lastrowid
            variant_count = con.execute(
                "SELECT COUNT(*) AS n FROM messages "
                "WHERE chat_id = ? AND COALESCE(variant_group, id) = ?",
                (chat_id, anchor),
            ).fetchone()["n"]

            con.execute(
                "UPDATE chats SET model_id = ?, updated_at = datetime('now') WHERE id = ?",
                (model_id_stripped, chat_id),
            )

            asst_row = con.execute(
                "SELECT id, chat_id, role, content, created_at, "
                "variant_group, active FROM messages WHERE id = ?",
                (asst_msg_id,),
            ).fetchone()
    except HTTPException:
        raise
    except Exception:
        logger.warning("DB write failed after successful regeneration: chat_id=%d", chat_id)
        raise

    logger.info(
        "Regenerate success: chat_id=%d existing_user=%d new_variant=%d group=%d",
        chat_id, existing_user_row["id"], asst_msg_id, anchor,
    )

    return {
        "chat_id": chat_id,
        "model_id": model_id_stripped,
        "user_message": _msg_to_dict(existing_user_row, user_atts),
        "assistant_message": _msg_to_dict(
            asst_row, variant_index=variant_count - 1, variant_count=variant_count,
        ),
        "deactivated_message_id": deactivated_id,
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
    existing_user_row, anchor, prev_active_id = _validate_regenerate_target(
        chat_id, message_id,
    )
    user_text = existing_user_row["content"]
    model_id_stripped = body.model_id
    # Same as the non-streaming path: the excluded-then-re-sent user turn
    # must carry its linked images again.
    user_atts = load_for_messages([existing_user_row["id"]]).get(
        existing_user_row["id"], [],
    )

    messages, filtered_gen_params, provider_dict = await _prepare_completion(
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

    async def event_source():
        parts: list[str] = []
        try:
            yield _sse_event({
                "type": "user_message",
                "message": _msg_to_dict(existing_user_row, user_atts),
            })

            async for delta in complete_stream(
                messages, model_id_stripped, filtered_gen_params, provider_dict,
            ):
                parts.append(delta)
                yield _sse_event({"type": "delta", "content": delta})

            full_text = "".join(parts)
            if not full_text.strip():
                raise OpenRouterError("openrouter_error")

            # Atomic variant append, guarded against concurrent changes.
            # Nothing is deleted - the old variant is deactivated in place.
            # SSE yields happen OUTSIDE the connection context: yielding
            # suspends the generator, and holding a connection open across a
            # network write is a leak waiting for a disconnect.
            conflict = False
            deactivated_id = prev_active_id
            asst_row = None
            variant_count = 0
            with get_db() as con:
                # One write txn for guard + mutate (see the non-streaming
                # regenerate swap for the TOCTOU rationale).
                con.execute("BEGIN IMMEDIATE")
                # Guard scope (deliberate): the group must still be the last
                # ACTIVE group; a concurrent regenerate of the SAME group
                # appends another sibling by design.
                last_active = con.execute(
                    "SELECT id, variant_group FROM messages "
                    "WHERE chat_id = ? AND active = 1 ORDER BY id DESC LIMIT 1",
                    (chat_id,),
                ).fetchone()
                if (
                    last_active is None
                    or (last_active["variant_group"] or last_active["id"]) != anchor
                ):
                    conflict = True
                else:
                    # Re-resolve the active sibling AT SWAP TIME (an activate
                    # may have changed it while the provider streamed).
                    cur_active = con.execute(
                        "SELECT id FROM messages WHERE chat_id = ? "
                        "AND COALESCE(variant_group, id) = ? AND active = 1",
                        (chat_id, anchor),
                    ).fetchone()
                    if cur_active:
                        deactivated_id = cur_active["id"]

                    # Deactivate BEFORE insert (one-active-per-group index).
                    con.execute(
                        "UPDATE messages SET variant_group = ?, active = 0 "
                        "WHERE chat_id = ? AND COALESCE(variant_group, id) = ? "
                        "AND active = 1",
                        (anchor, chat_id, anchor),
                    )
                    cur = con.execute(
                        "INSERT INTO messages "
                        "(chat_id, role, content, variant_group, active) "
                        "VALUES (?, 'assistant', ?, ?, 1)",
                        (chat_id, full_text, anchor),
                    )
                    asst_msg_id = cur.lastrowid
                    variant_count = con.execute(
                        "SELECT COUNT(*) AS n FROM messages "
                        "WHERE chat_id = ? AND COALESCE(variant_group, id) = ?",
                        (chat_id, anchor),
                    ).fetchone()["n"]
                    con.execute(
                        "UPDATE chats SET model_id = ?, "
                        "updated_at = datetime('now') WHERE id = ?",
                        (model_id_stripped, chat_id),
                    )
                    asst_row = con.execute(
                        "SELECT id, chat_id, role, content, created_at, "
                        "variant_group, active FROM messages WHERE id = ?",
                        (asst_msg_id,),
                    ).fetchone()

            if conflict or asst_row is None:
                logger.warning(
                    "Streaming regenerate conflict: chat_id=%d", chat_id,
                )
                yield _sse_event({
                    "type": "error", "status": 409, "code": "regenerate_conflict",
                })
                return

            logger.info(
                "Streaming regenerate success: chat_id=%d new_variant=%d group=%d",
                chat_id, asst_row["id"], anchor,
            )
            yield _sse_event({
                "type": "done",
                "chat_id": chat_id,
                "model_id": model_id_stripped,
                "user_message": _msg_to_dict(existing_user_row, user_atts),
                "assistant_message": _msg_to_dict(
                    asst_row,
                    variant_index=variant_count - 1,
                    variant_count=variant_count,
                ),
                "deactivated_message_id": deactivated_id,
            })

        except OpenRouterError as exc:
            # Old assistant message untouched - nothing to clean up.
            logger.warning(
                "Streaming regenerate failed: chat_id=%d reason=%s",
                chat_id, exc.reason,
            )
            yield _error_event(exc.reason)

        except (GeneratorExit, asyncio.CancelledError):
            logger.info(
                "Streaming regenerate aborted; old message kept: chat_id=%d",
                chat_id,
            )
            raise

        except VaultLockedError:
            logger.info("Streaming regenerate interrupted by vault lock: chat_id=%d", chat_id)
            yield _sse_event({"type": "error", "status": 423, "code": "vault_locked"})

        except Exception:
            logger.warning("Streaming regenerate internal error: chat_id=%d", chat_id)
            yield _sse_event({"type": "error", "status": 500, "code": "internal_error"})

    return StreamingResponse(
        event_source(), media_type="text/event-stream", headers=_SSE_HEADERS,
    )

