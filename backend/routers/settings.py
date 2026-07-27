"""routers/settings.py -- Settings endpoints (Phase 2).

Routes:
    GET    /settings              - current config state (no secrets)
    POST   /settings/api-key      - store API key in keyring
    DELETE /settings/api-key      - remove API key from keyring
    POST   /settings/proxy        - store proxy config
    DELETE /settings/proxy        - remove proxy config
    GET    /settings/proxy/health - proxy health probe result

Privacy invariants:
    - API key is NEVER logged, returned, or stored in SQLite.
    - Proxy URL is NEVER logged, returned, or stored in SQLite.
    - This module does NOT import or instantiate httpx.AsyncClient.
    - This module does NOT call OpenRouter or fetch models.
"""

import json
import logging
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

import keyring_service
from config import SECRET_API_KEY, SECRET_PROXY_URL
from database import get_db, get_setting, set_setting
from secrets_service import get_secret, set_secret, delete_secret
from network_client import reset_client
from proxy_health import (
    check_proxy_health,
    enforce_proxy_gate,
    invalidate_health_cache,
)
from openrouter import invalidate_model_cache

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])


def _set_setting_on(con, key: str, value: str) -> None:
    """set_setting joined to the caller's transaction (same upsert SQL)."""
    con.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )

# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class ApiKeyBody(BaseModel):
    api_key: str

    @field_validator("api_key")
    @classmethod
    def must_be_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("api_key must not be empty.")
        return v


class ProxyBody(BaseModel):
    proxy_url: str
    proxy_required: bool
    proxy_alias: str | None = None


class ProxyRequiredBody(BaseModel):
    proxy_required: bool


class ProxyAliasBody(BaseModel):
    """The display label, on its own.

    Same reasoning as ProxyRequiredBody: the alias could only ride along with
    POST /settings/proxy, which rejects an empty proxy_url - and the URL is
    write-only, cleared after every save and never shown again. So once a proxy
    existed, naming it meant retyping a URL nobody could see. It was not a
    hard path; it was an impossible one.
    """

    proxy_alias: str | None = None


#: Mirrors MAX_STOP_SEQUENCES / STOP_SEQUENCE_MAX_LENGTH in the dialog. Clamped
#: rather than rejected: a stale UI must not be able to 422 a settings save.
MAX_STOP_SEQUENCES = 4
MAX_STOP_SEQUENCE_CHARS = 100


class StopSequencesBody(BaseModel):
    stop_sequences: list[str]


def _read_stop_sequences(raw: str | None) -> list[str]:
    """Stored as a JSON array. A corrupted value reports as "none set" rather
    than breaking GET /settings - the same rule selected_persona_id follows."""
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("Ignoring malformed stop_sequences setting.")
        return []
    if not isinstance(value, list):
        return []
    return [str(v) for v in value if isinstance(v, str) and v][
        :MAX_STOP_SEQUENCES
    ]


# ---------------------------------------------------------------------------
# Proxy URL validation
# ---------------------------------------------------------------------------

_ALLOWED_SCHEMES = {"http", "https", "socks5", "socks5h"}


def _validate_proxy_url(url: str) -> None:
    """Raise HTTPException if the proxy URL is invalid.

    Error codes:
        proxy_url_required   - empty or whitespace-only
        invalid_proxy_scheme - scheme not in allowed set
        proxy_url_invalid    - valid scheme but missing host
    """
    if not url.strip():
        raise HTTPException(400, "proxy_url_required")
    parsed = urlparse(url.strip())
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise HTTPException(400, "invalid_proxy_scheme")
    if not parsed.hostname:
        raise HTTPException(400, "proxy_url_invalid")
    # Accessing `.port` is what PARSES it, and it raises on a non-numeric one.
    # Without this the bad URL was committed to the vault and only failed
    # afterwards, inside the httpx client build - as an opaque 500, with every
    # outbound request already broken and the settings page showing the value
    # as successfully saved.
    try:
        parsed.port
    except ValueError:
        raise HTTPException(400, "proxy_url_invalid") from None


# ---------------------------------------------------------------------------
# GET /settings
# ---------------------------------------------------------------------------

@router.get("")
async def get_settings() -> dict:
    """Return current configuration state. No secrets are included."""
    # Settings rows AND secret presence read on ONE connection = one snapshot
    # (the previous code opened two, contradicting its own comment). (v1.1 FB11.)
    with get_db() as con:
        rows = {
            r["key"]: r["value"]
            for r in con.execute(
                "SELECT key, value FROM settings "
                "WHERE key IN ('proxy_required', 'proxy_alias', "
                "'selected_persona_id', 'stop_sequences')"
            ).fetchall()
        }
        api_key_set = get_secret(SECRET_API_KEY, conn=con) is not None
        proxy_configured = get_secret(SECRET_PROXY_URL, conn=con) is not None

    proxy_alias_raw = rows.get("proxy_alias", "").strip()
    persona_id_raw = rows.get("selected_persona_id")
    try:
        selected_persona_id = int(persona_id_raw) if persona_id_raw else None
    except ValueError:
        # Corrupted setting must not break GET /settings; report as unset.
        logger.warning("Ignoring non-integer selected_persona_id setting.")
        selected_persona_id = None

    return {
        "api_key_set": api_key_set,
        "stop_sequences": _read_stop_sequences(rows.get("stop_sequences")),
        "proxy_required": rows.get("proxy_required") == "1",
        "proxy_configured": proxy_configured,
        "proxy_alias": proxy_alias_raw if proxy_alias_raw else None,
        "selected_persona_id": selected_persona_id,
    }


# ---------------------------------------------------------------------------
# POST /settings/api-key
# ---------------------------------------------------------------------------

@router.post("/api-key")
async def save_api_key(body: ApiKeyBody) -> dict:
    """Validate candidate API key via /api/v1/key, then store if valid.

    200 from /key → key stored, {ok: true, key_status: "valid"}.
    401/403 from /key → key NOT stored, HTTP 422.
    Network/timeout → key NOT stored, {ok: false, key_status: "validation_unavailable"}.
    503 when the proxy kill-switch is armed and the proxy is not usable.
    """
    from openrouter import validate_api_key

    # Validation is a LIVE outbound request carrying the key itself - it goes
    # through the same gate as completions and /models. Without it, a user with
    # proxy_required=1 and no usable proxy had their key and real IP sent in
    # the clear by the very screen where they type the key.
    await enforce_proxy_gate()
    status = await validate_api_key(body.api_key)

    if status == "valid":
        set_secret(SECRET_API_KEY, body.api_key)
        # Saving through the app is the user's resolution path for any stale
        # or conflicting LEGACY keyring copy: best-effort delete it now (the
        # unlock migration warns about conflicts but never auto-deletes).
        keyring_service.delete_legacy(SECRET_API_KEY)
        invalidate_model_cache()
        logger.info("API key validated and saved.")
        return {"ok": True, "key_status": "valid"}

    if status == "invalid":
        raise HTTPException(422, "api_key_invalid")

    # validation_unavailable - do NOT store
    logger.info("API key validation unavailable; key not stored.")
    return {"ok": False, "key_status": "validation_unavailable"}


# ---------------------------------------------------------------------------
# DELETE /settings/api-key
# ---------------------------------------------------------------------------

@router.delete("/api-key")
async def delete_api_key() -> dict:
    """Remove the API key from the vault AND from any legacy keyring copy.

    The legacy delete is not housekeeping, it is the whole point: save_api_key
    clears the OS-keyring copy and this path did not, so the unlock-time
    migration copied the stale legacy secret straight back into the vault on
    the next unlock. The user revoked a key, the app told them it was gone,
    and it came back - silently, and specifically for the one action people
    take when a key has leaked.
    """
    delete_secret(SECRET_API_KEY)
    keyring_service.delete_legacy(SECRET_API_KEY)
    invalidate_model_cache()
    logger.info("API key deleted.")
    return {"ok": True}


# ---------------------------------------------------------------------------
# POST /settings/proxy
# ---------------------------------------------------------------------------

@router.post("/proxy")
async def save_proxy(body: ProxyBody) -> dict:
    """Store proxy config atomically: secret + flags in ONE transaction (E5 -
    everything is DB rows now, so the old two-store split is gone)."""
    _validate_proxy_url(body.proxy_url)

    with get_db() as con:
        set_secret(SECRET_PROXY_URL, body.proxy_url.strip(), conn=con)
        _set_setting_on(con, "proxy_required", "1" if body.proxy_required else "0")
        _set_setting_on(con, "proxy_alias", (body.proxy_alias or "").strip())
    # Same resolution path as the API key: clear any stale legacy copy.
    keyring_service.delete_legacy(SECRET_PROXY_URL)

    # Side effects only after the commit above.
    await reset_client()
    invalidate_health_cache()
    invalidate_model_cache()

    logger.info("Proxy config saved.")
    return {"ok": True}


# ---------------------------------------------------------------------------
# POST /settings/stop-sequences
# ---------------------------------------------------------------------------

@router.post("/stop-sequences")
async def save_stop_sequences(body: StopSequencesBody) -> dict:
    """Persist the stop sequences IN THE VAULT, not in browser storage.

    They are the one generation setting that is user CONTENT - stop sequences
    are character names ("Human:", "Anna:") - which is why they were kept
    in-memory and are banned from localStorage by the S-09b privacy test. The
    consequence was that they had to be retyped every session, and were lost on
    every vault lock.

    The encrypted settings table is where content-bearing preferences already
    live (the selected persona, the API key). Saving them there keeps the
    privacy rule AND stops the retyping.
    """
    cleaned: list[str] = []
    for raw in body.stop_sequences:
        value = str(raw)[:MAX_STOP_SEQUENCE_CHARS]
        if value and value not in cleaned:
            cleaned.append(value)
        if len(cleaned) >= MAX_STOP_SEQUENCES:
            break

    set_setting("stop_sequences", json.dumps(cleaned, ensure_ascii=False))
    logger.info("Stop sequences saved (%d).", len(cleaned))
    return {"ok": True, "stop_sequences": cleaned}


# ---------------------------------------------------------------------------
# POST /settings/proxy/required
# ---------------------------------------------------------------------------

@router.post("/proxy/required")
async def set_proxy_required(body: ProxyRequiredBody) -> dict:
    """Arm or disarm the proxy kill-switch WITHOUT rewriting the proxy URL.

    proxy_required had no write path of its own: it could only ride along with
    POST /settings/proxy, which rejects an empty proxy_url - and the URL field
    is write-only (cleared after every save, never displayed). Changing one
    boolean therefore meant retyping the whole proxy URL from memory, so users
    who flipped the switch watched it move while nothing was written and
    completions kept going out on an unhealthy proxy.

    Arming with no proxy configured is refused: it would put every completion
    behind "proxy_missing", and the screen that could undo it is this one.
    """
    if body.proxy_required and not get_secret(SECRET_PROXY_URL):
        raise HTTPException(400, "proxy_url_required")

    set_setting("proxy_required", "1" if body.proxy_required else "0")
    # The gate reads the flag live, but a cached "healthy" verdict from before
    # the switch was armed would still be served for up to the TTL.
    invalidate_health_cache()
    logger.info("Proxy required set to %s.", body.proxy_required)
    return {"ok": True}


# ---------------------------------------------------------------------------
# POST /settings/proxy/alias
# ---------------------------------------------------------------------------

@router.post("/proxy/alias")
async def set_proxy_alias(body: ProxyAliasBody) -> dict:
    """Rename the configured proxy WITHOUT rewriting its URL.

    Refused when no proxy is configured: a label for a thing that does not
    exist would show up in the panel as a proxy that is not there.
    """
    if not get_secret(SECRET_PROXY_URL):
        raise HTTPException(400, "proxy_url_required")
    alias = (body.proxy_alias or "").strip()
    set_setting("proxy_alias", alias)
    logger.info("Proxy alias %s.", "set" if alias else "cleared")
    return {"ok": True, "proxy_alias": alias or None}


# ---------------------------------------------------------------------------
# DELETE /settings/proxy
# ---------------------------------------------------------------------------

@router.delete("/proxy")
async def delete_proxy() -> dict:
    """Remove proxy config atomically (secret + flags, one transaction)."""
    with get_db() as con:
        delete_secret(SECRET_PROXY_URL, conn=con)
        _set_setting_on(con, "proxy_required", "0")
        _set_setting_on(con, "proxy_alias", "")
    # Mirrors save_proxy, for the same reason delete_api_key does: a legacy
    # copy left behind here is re-migrated into the vault on the next unlock,
    # so "deleted" would mean "back tomorrow".
    keyring_service.delete_legacy(SECRET_PROXY_URL)

    # Side effects only after the commit above.
    await reset_client()
    invalidate_health_cache()
    invalidate_model_cache()

    logger.info("Proxy config deleted.")
    return {"ok": True}


# ---------------------------------------------------------------------------
# GET /settings/proxy/health
# ---------------------------------------------------------------------------

@router.get("/proxy/health")
async def proxy_health() -> dict:
    """Return proxy health status. No extra network logic in this handler."""
    return await check_proxy_health()
