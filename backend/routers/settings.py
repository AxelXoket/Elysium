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

import anyio.to_thread
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

import auto_lock
import keyring_service
from config import SECRET_API_KEY, SECRET_PROXY_URL
from database import get_db, set_setting
from generated_images import set_image_output_enabled
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

def _get_settings_sync() -> dict:
    """Worker-thread body (audit KÖK 8): own connection, one read snapshot.

    A read is not free here. Opening the SQLCipher database pays the KDF, and
    the connection can still queue behind a writer holding the lock for the
    full busy_timeout. On the event loop that freezes every live SSE stream,
    so the settings panel loading could stall a reply mid-sentence. The query
    below is unchanged; only the thread it runs on is.
    """
    # Settings rows AND secret presence read on ONE connection = one snapshot
    # (the previous code opened two, contradicting its own comment). (v1.1 FB11.)
    with get_db() as con:
        rows = {
            r["key"]: r["value"]
            for r in con.execute(
                "SELECT key, value FROM settings "
                "WHERE key IN ('proxy_required', 'proxy_alias', "
                "'selected_persona_id', 'stop_sequences', "
                "'image_output_enabled', 'auto_lock_minutes')"
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
        # Off unless the row says otherwise, which is how every existing vault
        # and every fresh install both read.
        "image_output_enabled": rows.get("image_output_enabled") in ("1", "true"),
        # Minutes of inactivity before the vault locks itself; 0 means never.
        # An unlocked vault is a decrypted vault for as long as the window is
        # open, and windows stay open for days.
        "auto_lock_minutes": _read_auto_lock(rows.get("auto_lock_minutes")),
    }


@router.get("")
async def get_settings() -> dict:
    """Return current configuration state. No secrets are included."""
    return await anyio.to_thread.run_sync(_get_settings_sync)


def _read_auto_lock(raw: str | None) -> int:
    """What the screen shows, from the same function the watchdog obeys.

    This used to be a second copy of auto_lock's parsing. Two copies agreeing
    that "absent means off" was harmless; the moment a default existed they
    would have disagreed about a vault nobody has configured, and the settings
    panel would have read "never" while the vault locked itself every five
    minutes.
    """
    return auto_lock.minutes_from_raw(raw)


# ---------------------------------------------------------------------------
# POST /settings/api-key
# ---------------------------------------------------------------------------

def _store_api_key_sync(api_key: str) -> None:
    """Worker-thread body (audit KÖK 8): the vault write plus the legacy
    keyring delete, which is a blocking call into the Windows credential store
    and not a fast one.

    Only the storage moves. enforce_proxy_gate() and validate_api_key() stay on
    the loop above because they are coroutines - a worker thread has no loop to
    await them on - and because their ORDER is the protection: the gate has to
    refuse before the key is put on the wire.
    """
    set_secret(SECRET_API_KEY, api_key)
    # Saving through the app is the user's resolution path for any stale
    # or conflicting LEGACY keyring copy: best-effort delete it now (the
    # unlock migration warns about conflicts but never auto-deletes).
    keyring_service.delete_legacy(SECRET_API_KEY)


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
        await anyio.to_thread.run_sync(_store_api_key_sync, body.api_key)
        # Stays on the loop: _model_cache is a bare module dict with no lock,
        # safe today only because every mutation happens on the single loop
        # thread. Moving it into the worker pool would make two concurrent
        # settings writes race for real, and nothing here is blocking anyway.
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

def _delete_api_key_sync() -> None:
    """Worker-thread body (audit KÖK 8): vault delete plus legacy keyring
    delete, both blocking. See _store_api_key_sync."""
    delete_secret(SECRET_API_KEY)
    keyring_service.delete_legacy(SECRET_API_KEY)


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
    await anyio.to_thread.run_sync(_delete_api_key_sync)
    invalidate_model_cache()  # loop-only, see save_api_key
    logger.info("API key deleted.")
    return {"ok": True}


# ---------------------------------------------------------------------------
# POST /settings/proxy
# ---------------------------------------------------------------------------

def _save_proxy_sync(proxy_url: str, proxy_required: bool,
                     proxy_alias: str) -> None:
    """Worker-thread body (audit KÖK 8): the write transaction and the legacy
    keyring delete. The URL is already validated - that has to fail on the loop
    before anything opens the database, exactly as it did before."""
    with get_db() as con:
        set_secret(SECRET_PROXY_URL, proxy_url, conn=con)
        _set_setting_on(con, "proxy_required", "1" if proxy_required else "0")
        _set_setting_on(con, "proxy_alias", proxy_alias)
    # Same resolution path as the API key: clear any stale legacy copy. Kept
    # OUTSIDE the transaction, where it has always been: it is a different
    # store with its own failure mode, and holding the writer lock across a
    # Windows credential-store call would widen the stall this whole change
    # exists to remove.
    keyring_service.delete_legacy(SECRET_PROXY_URL)


@router.post("/proxy")
async def save_proxy(body: ProxyBody) -> dict:
    """Store proxy config atomically: secret + flags in ONE transaction (E5 -
    everything is DB rows now, so the old two-store split is gone)."""
    _validate_proxy_url(body.proxy_url)

    await anyio.to_thread.run_sync(
        _save_proxy_sync,
        body.proxy_url.strip(),
        body.proxy_required,
        (body.proxy_alias or "").strip(),
    )

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

    # The cleaning above is pure Python and stays on the loop; only the write
    # is worth a thread hop (audit KÖK 8).
    await anyio.to_thread.run_sync(
        set_setting, "stop_sequences", json.dumps(cleaned, ensure_ascii=False)
    )
    logger.info("Stop sequences saved (%d).", len(cleaned))
    return {"ok": True, "stop_sequences": cleaned}


# ---------------------------------------------------------------------------
# POST /settings/proxy/required
# ---------------------------------------------------------------------------

def _set_proxy_required_sync(required: bool) -> None:
    """Worker-thread body (audit KÖK 8), and the guard read moves INTO the
    write transaction while it is being moved off the loop.

    The two statements used to run in autocommit on two separate connections,
    so a DELETE /settings/proxy landing between them armed the kill-switch
    against a proxy that no longer existed - and the screen that could disarm
    it is the one that just told the user it worked, while every completion
    started refusing with proxy_missing. BEGIN IMMEDIATE serializes this
    against delete_proxy's own transaction (v1.1 FB3, same shape as
    _rename_chat_sync).
    """
    with get_db() as con:
        con.execute("BEGIN IMMEDIATE")
        if required and not get_secret(SECRET_PROXY_URL, conn=con):
            raise HTTPException(400, "proxy_url_required")
        _set_setting_on(con, "proxy_required", "1" if required else "0")


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
    await anyio.to_thread.run_sync(_set_proxy_required_sync, body.proxy_required)
    # The gate reads the flag live, but a cached "healthy" verdict from before
    # the switch was armed would still be served for up to the TTL. In-memory,
    # so it stays on the loop; only blocking work is worth a thread hop.
    invalidate_health_cache()
    logger.info("Proxy required set to %s.", body.proxy_required)
    return {"ok": True}


# ---------------------------------------------------------------------------
# POST /settings/auto-lock
# ---------------------------------------------------------------------------

class AutoLockBody(BaseModel):
    # 0 is off. The upper bound is a day: past that the setting is a promise
    # nobody is relying on, and a typo like 100000 should read as a mistake
    # rather than as "effectively never" wearing the label of a timeout.
    auto_lock_minutes: int = Field(ge=0, le=1440)


@router.post("/auto-lock")
async def set_auto_lock(body: AutoLockBody) -> dict:
    """Lock the vault after this many minutes of doing nothing. 0 disables it.

    Stored in the vault rather than in browser storage, like every other
    setting that is about protection rather than appearance: browser storage
    is readable without the passphrase, and a security setting somebody else
    can read and change is not one.

    One minute is a legitimate setting for somebody who wants it, so the only
    values refused are the ones that are not timeouts at all: negative, and
    beyond a day. Past a day the number is a promise nobody is relying on, and
    a typo like 100000 should read as a mistake rather than as "effectively
    never" wearing the label of a timeout.
    """
    minutes = body.auto_lock_minutes
    await anyio.to_thread.run_sync(set_setting, auto_lock.SETTING, str(minutes))
    logger.info("Auto-lock set to %d minute(s).", minutes)
    return {"ok": True, "auto_lock_minutes": minutes}


# ---------------------------------------------------------------------------
# POST /settings/image-output
# ---------------------------------------------------------------------------

class ImageOutputBody(BaseModel):
    image_output_enabled: bool


@router.post("/image-output")
async def set_image_output(body: ImageOutputBody) -> dict:
    """Allow (or stop allowing) a model to answer with a picture.

    Off by default and stored in the vault, not in browser storage: it changes
    what is sent to the provider, so it belongs with the other request-shaping
    settings rather than with the appearance preferences.

    There is deliberately no capability check here. Whether a given model can
    draw is decided per request, from the cached catalogue, in exactly one place
    (_model_emits_images) - and refusing to store the preference because the
    model selected RIGHT NOW cannot draw would make the switch mean something
    different from what it says.
    """
    await anyio.to_thread.run_sync(
        set_image_output_enabled, body.image_output_enabled
    )
    logger.info("Image output set to %s.", body.image_output_enabled)
    return {"ok": True, "image_output_enabled": body.image_output_enabled}


# ---------------------------------------------------------------------------
# POST /settings/proxy/alias
# ---------------------------------------------------------------------------

def _set_proxy_alias_sync(alias: str) -> None:
    """Worker-thread body (audit KÖK 8). Guard and write in one transaction,
    for the same reason as _set_proxy_required_sync: a proxy delete landing
    between an autocommit guard read and the write would leave a label naming
    a proxy that no longer exists."""
    with get_db() as con:
        con.execute("BEGIN IMMEDIATE")
        if not get_secret(SECRET_PROXY_URL, conn=con):
            raise HTTPException(400, "proxy_url_required")
        _set_setting_on(con, "proxy_alias", alias)


@router.post("/proxy/alias")
async def set_proxy_alias(body: ProxyAliasBody) -> dict:
    """Rename the configured proxy WITHOUT rewriting its URL.

    Refused when no proxy is configured: a label for a thing that does not
    exist would show up in the panel as a proxy that is not there.
    """
    alias = (body.proxy_alias or "").strip()
    await anyio.to_thread.run_sync(_set_proxy_alias_sync, alias)
    logger.info("Proxy alias %s.", "set" if alias else "cleared")
    return {"ok": True, "proxy_alias": alias or None}


# ---------------------------------------------------------------------------
# DELETE /settings/proxy
# ---------------------------------------------------------------------------

def _delete_proxy_sync() -> None:
    """Worker-thread body (audit KÖK 8): the delete transaction and the legacy
    keyring delete. See _save_proxy_sync."""
    with get_db() as con:
        delete_secret(SECRET_PROXY_URL, conn=con)
        _set_setting_on(con, "proxy_required", "0")
        _set_setting_on(con, "proxy_alias", "")
    # Mirrors save_proxy, for the same reason delete_api_key does: a legacy
    # copy left behind here is re-migrated into the vault on the next unlock,
    # so "deleted" would mean "back tomorrow".
    keyring_service.delete_legacy(SECRET_PROXY_URL)


@router.delete("/proxy")
async def delete_proxy() -> dict:
    """Remove proxy config atomically (secret + flags, one transaction)."""
    await anyio.to_thread.run_sync(_delete_proxy_sync)

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
