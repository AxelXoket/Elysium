"""routers/settings.py -- Settings endpoints (Phase 2).

Routes:
    GET    /settings              — current config state (no secrets)
    POST   /settings/api-key      — store API key in keyring
    DELETE /settings/api-key      — remove API key from keyring
    POST   /settings/proxy        — store proxy config
    DELETE /settings/proxy        — remove proxy config
    GET    /settings/proxy/health — proxy health probe result

Privacy invariants:
    - API key is NEVER logged, returned, or stored in SQLite.
    - Proxy URL is NEVER logged, returned, or stored in SQLite.
    - This module does NOT import or instantiate httpx.AsyncClient.
    - This module does NOT call OpenRouter or fetch models.
"""

import logging
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from config import KEYRING_API_KEY, KEYRING_PROXY_URL
from database import get_db, get_setting, set_setting
from keyring_service import get_secret, set_secret, delete_secret
from network_client import reset_client
from proxy_health import check_proxy_health, invalidate_health_cache
from openrouter import invalidate_model_cache

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])

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


# ---------------------------------------------------------------------------
# Proxy URL validation
# ---------------------------------------------------------------------------

_ALLOWED_SCHEMES = {"http", "https", "socks5", "socks5h"}


def _validate_proxy_url(url: str) -> None:
    """Raise HTTPException if the proxy URL is invalid.

    Error codes:
        proxy_url_required   — empty or whitespace-only
        invalid_proxy_scheme — scheme not in allowed set
        proxy_url_invalid    — valid scheme but missing host
    """
    if not url.strip():
        raise HTTPException(400, "proxy_url_required")
    parsed = urlparse(url.strip())
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise HTTPException(400, "invalid_proxy_scheme")
    if not parsed.hostname:
        raise HTTPException(400, "proxy_url_invalid")


# ---------------------------------------------------------------------------
# GET /settings
# ---------------------------------------------------------------------------

@router.get("")
async def get_settings() -> dict:
    """Return current configuration state. No secrets are included."""
    with get_db() as con:
        rows = {
            r["key"]: r["value"]
            for r in con.execute(
                "SELECT key, value FROM settings "
                "WHERE key IN ('proxy_required', 'proxy_alias', 'selected_persona_id')"
            ).fetchall()
        }

    proxy_alias_raw = rows.get("proxy_alias", "").strip()
    persona_id_raw = rows.get("selected_persona_id")
    selected_persona_id = int(persona_id_raw) if persona_id_raw else None

    return {
        "api_key_set": get_secret(KEYRING_API_KEY) is not None,
        "proxy_required": rows.get("proxy_required") == "1",
        "proxy_configured": get_secret(KEYRING_PROXY_URL) is not None,
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
    """
    from openrouter import validate_api_key

    status = await validate_api_key(body.api_key)

    if status == "valid":
        set_secret(KEYRING_API_KEY, body.api_key)
        invalidate_model_cache()
        logger.info("API key validated and saved.")
        return {"ok": True, "key_status": "valid"}

    if status == "invalid":
        raise HTTPException(422, "api_key_invalid")

    # validation_unavailable — do NOT store
    logger.info("API key validation unavailable; key not stored.")
    return {"ok": False, "key_status": "validation_unavailable"}


# ---------------------------------------------------------------------------
# DELETE /settings/api-key
# ---------------------------------------------------------------------------

@router.delete("/api-key")
async def delete_api_key() -> dict:
    """Remove API key from keyring. Silent if already absent."""
    delete_secret(KEYRING_API_KEY)
    invalidate_model_cache()
    logger.info("API key deleted.")
    return {"ok": True}


# ---------------------------------------------------------------------------
# POST /settings/proxy
# ---------------------------------------------------------------------------

@router.post("/proxy")
async def save_proxy(body: ProxyBody) -> dict:
    """Store proxy config. URL goes to keyring; flags go to SQLite."""
    _validate_proxy_url(body.proxy_url)

    # 1. Keyring
    set_secret(KEYRING_PROXY_URL, body.proxy_url.strip())

    # 2. SQLite
    set_setting("proxy_required", "1" if body.proxy_required else "0")
    set_setting("proxy_alias", (body.proxy_alias or "").strip())

    # 3. Side effects
    await reset_client()
    invalidate_health_cache()
    invalidate_model_cache()

    logger.info("Proxy config saved.")
    return {"ok": True}


# ---------------------------------------------------------------------------
# DELETE /settings/proxy
# ---------------------------------------------------------------------------

@router.delete("/proxy")
async def delete_proxy() -> dict:
    """Remove proxy config. URL from keyring; flags reset in SQLite."""
    # 1. Keyring
    delete_secret(KEYRING_PROXY_URL)

    # 2. SQLite
    set_setting("proxy_required", "0")
    set_setting("proxy_alias", "")

    # 3. Side effects
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
