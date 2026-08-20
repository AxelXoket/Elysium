"""routers/settings.py -- Settings endpoints (Phase 2).

Routes:
    GET    /settings              - current config state (no secrets)
    POST   /settings/api-key      - store API key in keyring
    DELETE /settings/api-key      - remove API key from keyring
    POST   /settings/api-key/check - is the STORED key still accepted
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
from pydantic import BaseModel, ConfigDict, Field, field_validator

import auto_lock
import keyring_service
from config import (
    SECRET_API_KEY,
    SECRET_PROXY_URL,
    SELECTED_MODEL_ID_MAX_CHARS,
    SETTING_SELECTED_MODEL_ID,
)
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

    `forbid` for the same reason as ModelSelectionBody, which is the only
    other body in this file with NO required field: absence means "clear",
    so an unknown field name is not a harmless typo. Under the default
    `ignore` a request carrying `proxy_allias` reached the route as
    `proxy_alias=None`, the route wrote "", the stored label was DELETED, and
    the answer was `{"ok": true}`. Every other body here has at least one
    required field and so already 422s on a misspelling; these two did not.
    """

    model_config = ConfigDict(extra="forbid")

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
                "'image_output_enabled', 'auto_lock_minutes', "
                "'screen_privacy_enabled', 'selected_model_id')"
            ).fetchall()
        }
        api_key_set = get_secret(SECRET_API_KEY, conn=con) is not None
        proxy_configured = get_secret(SECRET_PROXY_URL, conn=con) is not None

    proxy_alias_raw = rows.get("proxy_alias", "").strip()
    selected_model_id_raw = (rows.get("selected_model_id") or "").strip()
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
        # Off unless explicitly on, matching the reader in routers/vault.py.
        # Two places parse the same row, so both fail closed the same way.
        "screen_privacy_enabled":
            rows.get("screen_privacy_enabled") in ("1", "true"),
        # v1.2: the currently-selected model id. A NAME a person reads on
        # screen, not a number, so it lives here rather than in localStorage -
        # see config.SETTING_SELECTED_MODEL_ID.
        "selected_model_id": selected_model_id_raw if selected_model_id_raw else None,
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

def _revoke_legacy(name: str) -> bool:
    """Delete a legacy keyring copy, and remember it if that fails.

    Returns True when a copy is still out there.

    THE RETURN VALUE WAS THE WHOLE DEFECT. `delete_legacy` computes a boolean
    and, at every one of its six call sites, that boolean was dropped on the
    floor. The credential store is a different machine-global store with its
    own failure mode - a broken backend, a roaming profile, a locked store -
    and it answers False rather than raising, so a failure looked exactly like
    a success from here.

    Worse than a leak, on the delete path. The vault row goes; the keyring
    entry stays; and the next unlock's migration sees "vault empty, keyring
    set", which is its signal to IMPORT. So the key the user revoked was
    copied back into the vault and used for the next request. The tombstone is
    what tells those two states apart: "never imported" and "deliberately
    removed" are otherwise the same absence.
    """
    if keyring_service.delete_legacy(name):
        # Clean state: no copy, so no tombstone to keep. Leaving one behind
        # would block a genuine future import for no reason.
        set_setting(keyring_service.revoked_key(name), "")
        return False
    set_setting(keyring_service.revoked_key(name), "1")
    logger.warning(
        "settings: the legacy credential-store copy of %s could not be "
        "removed and is still readable on this machine.", name)
    return True


def _store_api_key_sync(api_key: str) -> bool:
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
    # or conflicting LEGACY keyring copy: delete it now. The migration warns
    # about conflicts and never auto-deletes, so this is the only exit from
    # that state - which is why a failure here is recorded rather than
    # dropped.
    return _revoke_legacy(SECRET_API_KEY)


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

def _delete_api_key_sync() -> bool:
    """Worker-thread body (audit KÖK 8): vault delete plus legacy keyring
    delete, both blocking. See _store_api_key_sync."""
    delete_secret(SECRET_API_KEY)
    return _revoke_legacy(SECRET_API_KEY)


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
    left_behind = await anyio.to_thread.run_sync(_delete_api_key_sync)
    invalidate_model_cache()  # loop-only, see save_api_key
    logger.info("API key deleted.")
    # Not a flat ok. The vault row is gone either way, but a legacy copy the
    # credential store would not release is still readable by anything running
    # as this user, and the person who pressed this button pressed it because
    # a key leaked. Saying "done" to that is the failure this field exists to
    # end - the same shape /vault/lock reports with audio_left.
    return {"ok": True, "legacy_copy_left": left_behind}


# ---------------------------------------------------------------------------
# POST /settings/api-key/check
# ---------------------------------------------------------------------------

def _read_api_key_sync() -> str | None:
    """Worker-thread body (audit KOK 8): the stored-key read.

    A vault read is a SQLite open, a reader lock and a decrypt, exactly the
    work _get_settings_sync was moved off the loop for. The settings panel is
    the screen people open mid-reply, and this button is the slowest thing on
    it, so a stall here would freeze every live stream.
    """
    return get_secret(SECRET_API_KEY)


@router.post("/api-key/check")
async def check_api_key() -> dict:
    """Ask OpenRouter whether the key ALREADY STORED is still accepted.

    save_api_key answers this question for a key being typed, which is the one
    moment nobody needs to ask it. The key that quietly stops working is the
    one saved last month: revoked on the provider's dashboard, expired, or out
    of credit. Until this route existed the only way to find that out was to
    send a message and read the failure, or to retype the whole key into the
    save box - and retyping a key to test it is not a test, it is a save.

    POST rather than GET, and not as a matter of style. This handler puts the
    stored key on the wire every time it runs, so it must fire exactly when a
    person presses the button: never from a prefetch, a browser retry, or the
    parameterless-GET sweep in tests/test_privacy_promises.py that calls every
    such route the app serves. GET /settings/proxy/health is a GET because it
    is cached and safe to repeat. This is neither.

    Four answers, and the middle two are the point:
        {"key_status": "valid"}                  - OpenRouter accepted it.
        {"key_status": "invalid"}                - OpenRouter rejected it.
        {"key_status": "validation_unavailable"} - no answer ever arrived.
        {"key_status": "not_set"}                - nothing stored to check.
    "invalid" and "validation_unavailable" are OPPOSITE facts. One says replace
    the key; the other says the key was never even asked about. Collapsing them
    into a single failure is how a proxy outage gets read as a dead key and a
    perfectly good key gets thrown away.

    A rejection is not an HTTP error here, and that differs from save_api_key
    on purpose. There, 422 is right because the request - store this key -
    failed. Here the request is "check it", and a check that comes back
    "rejected" SUCCEEDED. Making it a 4xx would also route it through the
    frontend's parseApiError, where it would arrive wearing the same generic
    sentence as an unreachable provider: the exact collapse this route exists
    to prevent.

    The key never leaves this function. Not in the response, not in the log
    line - only the verdict does.
    """
    from openrouter import validate_api_key

    # The same gate as every other outbound path, in the same order and for the
    # reason written on enforce_proxy_gate itself: this is a LIVE request
    # carrying the key. It runs BEFORE the vault is read, so an armed
    # kill-switch with no usable proxy refuses without the secret ever being
    # loaded into this process's memory.
    await enforce_proxy_gate()

    stored = await anyio.to_thread.run_sync(_read_api_key_sync)
    if stored is None:
        # Answered, not raised. "There is nothing stored to check" is a state
        # of the app, not a failure of the request, and the UI hides the button
        # in that state anyway - this is what the race answers with when the key
        # was removed in another window between the render and the click.
        return {"key_status": "not_set"}

    status = await validate_api_key(stored)
    # Three fixed words, never the key and never the provider's body.
    logger.info("Stored API key checked; verdict: %s.", status)
    return {"key_status": status}


# ---------------------------------------------------------------------------
# POST /settings/proxy
# ---------------------------------------------------------------------------

def _save_proxy_sync(proxy_url: str, proxy_required: bool,
                     proxy_alias: str) -> bool:
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
    return _revoke_legacy(SECRET_PROXY_URL)


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
# POST /settings/model-selection
# ---------------------------------------------------------------------------

class ModelSelectionBody(BaseModel):
    # `forbid`, not the `ignore` the completions bodies use, because the two
    # failure modes are not the same shape. Here the field is OPTIONAL and its
    # absence is meaningful: a missing `selected_model_id` means "clear the
    # selection". So under `ignore`, a caller that sends the wrong field name
    # gets the value dropped, the model set to None, the stored selection
    # DELETED, and `{"ok": true}` back - a silent destructive write reported
    # as a success. docs/frontend_contract.md spelled the field `model_id`
    # until 2026-08-20, so the public contract described exactly that request.
    # An unknown field is now a 422 and the stored value is left alone.
    model_config = ConfigDict(extra="forbid")

    selected_model_id: str | None = None


def _clean_model_id(raw: str | None) -> str | None:
    """Clamp defensively rather than reject. This is UI state, not a security
    boundary - a stale or oversized value from an old client must not 422 a
    routine selection write, the same choice save_stop_sequences makes for
    its own list."""
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None
    return value[:SELECTED_MODEL_ID_MAX_CHARS]


@router.post("/model-selection")
async def set_model_selection(body: ModelSelectionBody) -> dict:
    """Persist which model is selected, IN THE VAULT rather than the browser.

    An OpenRouter model id ("anthropic/claude-3.5-sonnet") is a NAME a person
    reads on screen - exactly the shape the S-09b privacy rule, and the
    owner's own rule, ban from ever sitting outside the vault. It used to
    live in uiStore's `elysium-ui-state` blob in localStorage, in the clear;
    it lives here now, next to the API key and the stop sequences.

    uiStore.ts's version-3 `migrate` strips the old plaintext copy out of
    every install that already has one - this endpoint alone would only stop
    the leak from growing, not clean up what is already on disk.
    """
    value = _clean_model_id(body.selected_model_id)
    await anyio.to_thread.run_sync(
        set_setting, SETTING_SELECTED_MODEL_ID, value or ""
    )
    logger.info("Selected model %s.", "set" if value else "cleared")
    return {"ok": True, "selected_model_id": value}


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

def _delete_proxy_sync() -> bool:
    """Worker-thread body (audit KÖK 8): the delete transaction and the legacy
    keyring delete. See _save_proxy_sync."""
    with get_db() as con:
        delete_secret(SECRET_PROXY_URL, conn=con)
        _set_setting_on(con, "proxy_required", "0")
        _set_setting_on(con, "proxy_alias", "")
    # Mirrors save_proxy, for the same reason delete_api_key does: a legacy
    # copy left behind here is re-migrated into the vault on the next unlock,
    # so "deleted" would mean "back tomorrow".
    return _revoke_legacy(SECRET_PROXY_URL)


@router.delete("/proxy")
async def delete_proxy() -> dict:
    """Remove proxy config atomically (secret + flags, one transaction)."""
    left_behind = await anyio.to_thread.run_sync(_delete_proxy_sync)

    # Side effects only after the commit above.
    await reset_client()
    invalidate_health_cache()
    invalidate_model_cache()

    logger.info("Proxy config deleted.")
    return {"ok": True, "legacy_copy_left": left_behind}


# ---------------------------------------------------------------------------
# GET /settings/proxy/health
# ---------------------------------------------------------------------------

@router.get("/proxy/health")
async def proxy_health() -> dict:
    """Return proxy health status. No extra network logic in this handler."""
    return await check_proxy_health()


class ScreenPrivacyBody(BaseModel):
    screen_privacy_enabled: bool


@router.post("/screen-privacy")
async def set_screen_privacy(body: ScreenPrivacyBody) -> dict:
    """Hide this window from screen capture and screen sharing.

    Stored in the vault rather than in browser storage, for the same reason
    the auto-lock delay is: a protection setting somebody can read and change
    without the passphrase is not one.

    OFF by default. The owner takes screenshots of this app, and a default
    that blacks out their captures until they find the switch would be the app
    deciding for them. Applied on vault transitions, never at launch - the
    setting lives inside the vault and the window exists before it is open.

    What it cannot do, said plainly: macOS's newer capture API can still read
    the window, and a window opened after the flag is set (a file picker, a
    permission prompt) is not covered. A layer, not a guarantee.
    """
    from routers import vault as vault_router

    await anyio.to_thread.run_sync(
        set_setting, vault_router.SETTING_SCREEN_PRIVACY,
        "1" if body.screen_privacy_enabled else "0")
    # Immediately, not on the next unlock: a switch that takes effect later is
    # one the user cannot tell worked.
    await anyio.to_thread.run_sync(
        vault_router._apply_screen_privacy, True)
    logger.info("Screen privacy set to %s.", body.screen_privacy_enabled)
    return {"ok": True, "screen_privacy_enabled": body.screen_privacy_enabled}

