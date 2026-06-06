"""routers/completions.py -- Text-only OpenRouter completion endpoint (Phase 5B).

Route:
    POST /chats/{chat_id}/complete  — send a user message, get assistant response.

Privacy invariants:
    - API key is read via keyring_service.get_secret(); never stored in a variable
      beyond the call site.
    - User message content, assistant response, prompt payload, and raw OpenRouter
      response are NEVER logged.
    - Only chat_id, model_id, message IDs, and gen_param keys are logged.
    - Raw OpenRouter error bodies are never forwarded to the client.
    - This module does NOT import httpx, requests, urllib.request,
      or the keyring package directly (import keyring / from keyring import ...).
      It uses keyring_service as the approved abstraction.

Scope:
    - Text-only, non-streaming.
    - No tools, tool_choice, response_format, image_url, file, reasoning.
    - No streaming (stream: true).
    - No local models.
"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from config import KEYRING_API_KEY, PROVIDER_POLICY, CONTEXT_SAFETY_MARGIN
from database import get_db
from keyring_service import get_secret
from openrouter import (
    OpenRouterError,
    validate_and_filter_gen_params,
    get_cached_model_metadata,
    complete,
)
from proxy_health import check_proxy_health

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chats", tags=["chats"])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CHAR_PER_TOKEN = 4  # conservative token estimation: 1 token ≈ 4 chars
_DEFAULT_CONTEXT_LEN = 32000
_DEFAULT_MAX_TOKENS = 2048


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


def _assemble_messages(
    system_block: str,
    persona_block: str,
    history: list[dict],
    user_message: str,
    post_history_instruction: str,
    context_budget_chars: int,
    max_tokens_chars: int,
) -> list[dict]:
    """Build the final messages list with context budget truncation.

    Raises HTTPException(400, "context_too_large") if even the system block
    plus persona plus the current user message exceeds the available budget.
    """
    available = context_budget_chars - max_tokens_chars
    system_chars = len(system_block) + len(persona_block)
    user_msg_chars = len(user_message)
    min_required = system_chars + user_msg_chars

    if min_required > available:
        raise HTTPException(400, "context_too_large")

    # Trim history from oldest end until it fits
    remaining = available - system_chars - user_msg_chars
    history_chars = sum(len(m["content"]) for m in history)
    while history_chars > remaining and history:
        dropped = history.pop(0)
        history_chars -= len(dropped["content"])

    # Build final list
    messages: list[dict] = []

    if system_block:
        messages.append({"role": "system", "content": system_block})

    if persona_block:
        messages.append({"role": "system", "content": persona_block})

    for msg in history:
        messages.append({"role": msg["role"], "content": msg["content"]})

    messages.append({"role": "user", "content": user_message.strip()})

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


def _msg_to_dict(row) -> dict:
    """Convert a message DB row to the API response shape."""
    return {
        "id":         row["id"],
        "chat_id":    row["chat_id"],
        "role":       row["role"],
        "content":    row["content"],
        "created_at": row["created_at"],
    }


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

async def _call_provider_for_chat(
    chat_id: int,
    model_id: str,
    user_message_text: str,
    generation_params: GenerationParams,
    provider: ProviderPolicy,
    persona_id: int | None,
    context_budget_tokens: int | None,
) -> str:
    """Shared logic: build payload, call OpenRouter, return assistant text.

    This function handles:
      - fetch chat + character + history from DB
      - check API key
      - proxy health gate
      - persona resolution & injection
      - context budget computation & history trimming
      - generation parameter validation & filtering
      - provider privacy policy hardcoding
      - OpenRouter API call
      - response parsing

    Returns the assistant text string on success.
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

        history_rows = con.execute(
            "SELECT id, role, content FROM messages "
            "WHERE chat_id = ? ORDER BY id ASC",
            (chat_id,),
        ).fetchall()

    history = [{"role": r["role"], "content": r["content"]} for r in history_rows]

    # ── Check API key ─────────────────────────────────────────────────────
    api_key = get_secret(KEYRING_API_KEY)
    if not api_key:
        raise HTTPException(401, "api_key_missing")

    # ── Proxy health check ────────────────────────────────────────────────
    from database import get_setting
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
    from database import get_setting as _get_setting_fn
    persona_block = ""
    resolved_persona_id = persona_id
    if resolved_persona_id is None:
        sel = _get_setting_fn("selected_persona_id")
        if sel:
            resolved_persona_id = int(sel)

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

    messages = _assemble_messages(
        system_block,
        persona_block,
        history,
        user_message_text,
        phi,
        context_budget_chars,
        max_tokens_chars,
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

    # ── Build provider dict ───────────────────────────────────────────────
    provider_dict = _build_provider_dict(provider)

    # ── Call OpenRouter ───────────────────────────────────────────────────
    logger.info("Completion request: chat_id=%d model=%s", chat_id, model_id_stripped)

    try:
        raw = await complete(messages, model_id_stripped, filtered_gen_params, provider_dict)
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

    assistant_text = await _call_provider_for_chat(
        chat_id=chat_id,
        model_id=body.model_id,
        user_message_text=body.message,
        generation_params=body.generation_params,
        provider=body.provider,
        persona_id=body.persona_id,
        context_budget_tokens=body.context_budget_tokens,
    )

    # ── DB transaction: insert user + assistant ───────────────────────────
    user_message_stripped = body.message.strip()
    model_id_stripped = body.model_id

    try:
        with get_db() as con:
            cur = con.execute(
                "INSERT INTO messages (chat_id, role, content) VALUES (?, 'user', ?)",
                (chat_id, user_message_stripped),
            )
            user_msg_id = cur.lastrowid

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
        "user_message": _msg_to_dict(user_row),
        "assistant_message": _msg_to_dict(asst_row),
    }


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


@router.post("/{chat_id}/messages/{message_id}/regenerate")
async def regenerate_message(chat_id: int, message_id: int,
                             body: RegenerateRequest) -> dict:
    """Regenerate the latest assistant message.

    1. Validate the target is the last message AND role == assistant
    2. Find the preceding user message (unchanged)
    3. Delete ONLY the target assistant message
    4. Call provider with existing history + existing user message
    5. Insert ONLY the new assistant message
    6. Return existing user_message row + new assistant_message row
    """
    with get_db() as con:
        # Verify chat
        chat_row = con.execute(
            "SELECT id, character_id FROM chats WHERE id = ?", (chat_id,)
        ).fetchone()
        if chat_row is None:
            raise HTTPException(404, "chat_not_found")

        # Verify message exists in this chat
        msg_row = con.execute(
            "SELECT id, role, chat_id FROM messages WHERE id = ? AND chat_id = ?",
            (message_id, chat_id),
        ).fetchone()
        if msg_row is None:
            raise HTTPException(404, "message_not_found")

        # Must be assistant role
        if msg_row["role"] != "assistant":
            raise HTTPException(422, "not_last_assistant_message")

        # Must be the last message in the chat
        last_msg = con.execute(
            "SELECT id FROM messages WHERE chat_id = ? ORDER BY id DESC LIMIT 1",
            (chat_id,),
        ).fetchone()
        if last_msg is None or last_msg["id"] != message_id:
            raise HTTPException(422, "not_last_assistant_message")

        # Find the preceding user message
        user_msg = con.execute(
            "SELECT id, chat_id, role, content, created_at FROM messages "
            "WHERE chat_id = ? AND role = 'user' AND id < ? "
            "ORDER BY id DESC LIMIT 1",
            (chat_id, message_id),
        ).fetchone()
        if user_msg is None:
            raise HTTPException(422, "no_preceding_user_message")

        existing_user_row = dict(user_msg)
        user_text = user_msg["content"]

        # Delete ONLY the target assistant message
        con.execute(
            "DELETE FROM messages WHERE id = ? AND chat_id = ?",
            (message_id, chat_id),
        )

    # Call provider with existing history (assistant is now deleted,
    # so history will contain everything up to and including the user message)
    assistant_text = await _call_provider_for_chat(
        chat_id=chat_id,
        model_id=body.model_id,
        user_message_text=user_text,
        generation_params=body.generation_params,
        provider=body.provider,
        persona_id=body.persona_id,
        context_budget_tokens=body.context_budget_tokens,
    )

    # Insert ONLY the new assistant message
    model_id_stripped = body.model_id
    try:
        with get_db() as con:
            cur = con.execute(
                "INSERT INTO messages (chat_id, role, content) VALUES (?, 'assistant', ?)",
                (chat_id, assistant_text),
            )
            asst_msg_id = cur.lastrowid

            con.execute(
                "UPDATE chats SET model_id = ?, updated_at = datetime('now') WHERE id = ?",
                (model_id_stripped, chat_id),
            )

            asst_row = con.execute(
                "SELECT id, chat_id, role, content, created_at FROM messages WHERE id = ?",
                (asst_msg_id,),
            ).fetchone()
    except Exception:
        logger.warning("DB write failed after successful regeneration: chat_id=%d", chat_id)
        raise

    logger.info(
        "Regenerate success: chat_id=%d existing_user=%d new_asst=%d",
        chat_id, existing_user_row["id"], asst_msg_id,
    )

    return {
        "chat_id": chat_id,
        "model_id": model_id_stripped,
        "user_message": _msg_to_dict(existing_user_row),
        "assistant_message": _msg_to_dict(asst_row),
    }

