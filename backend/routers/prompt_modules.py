"""routers/prompt_modules.py -- Prompt modules and the prompt settings.

Routes (prefix /prompt-modules):
    GET    ""                        list every row, the imports, the enabled total
    POST   "/import"                 raw-body file upload (never multipart)
    GET    "/settings"               prompt_layer_enabled, reply_length_words
    PUT    "/settings"               both, replaced together
    POST   "/enable-preset-defaults" switch on what SillyTavern had on, all or none
    PATCH  "/{id}"                   enabled / sort_order / name
    DELETE "/{id}"

Privacy invariants:
    - The uploaded file is read into memory and nowhere else: no multipart
      parser (Starlette spools large parts to a temp file), no temp file, no
      disk path. The byte cap is checked from Content-Length before a byte is
      read and again while streaming.
    - The kept preset fields and each entry's raw JSON are never returned.
    - Nothing here logs file text, module names or content; only ids and
      counts.
    - This module makes no network call.
"""

from __future__ import annotations

import logging
from urllib.parse import unquote

import anyio.to_thread
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from database import get_db
from prompt_modules import budget, parsers, store
from prompt_modules import errors as E
from prompt_modules.limits import FILENAME_HEADER, REPLY_LENGTH_WORDS_ALLOWED

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/prompt-modules", tags=["prompt-modules"])

#: Body types that go through a form parser. Refused before the body is read,
#: so the multipart path (and its spool-to-disk) can never be taken here.
_FORM_TYPES = ("multipart/form-data", "application/x-www-form-urlencoded")


def _refusal(exc: E.PromptModuleError) -> JSONResponse:
    return JSONResponse({"detail": exc.code, **exc.extra},
                        status_code=exc.status)


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

async def _read_capped(request: Request, cap: int) -> bytes:
    """In memory, never more than `cap` bytes held.

    A declared length is required: a chunked body has none, and the browser's
    fetch with a Blob or ArrayBuffer body always declares one, so a missing
    length is a sender this route has no reason to serve. The running count
    stays anyway, for a sender that declares less than it sends.
    """
    declared = request.headers.get("content-length")
    if declared is None:
        raise E.PromptModuleError(E.LENGTH_REQUIRED, 411)
    try:
        n = int(declared)
    except ValueError:
        raise E.PromptModuleError(E.REQUEST_INVALID) from None
    if n > cap:
        raise E.PromptModuleError(E.FILE_TOO_LARGE, 413, max_bytes=cap)
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > cap:
            raise E.PromptModuleError(E.FILE_TOO_LARGE, 413, max_bytes=cap)
        chunks.append(chunk)
    return b"".join(chunks)


def _parse(filename: str, raw: bytes) -> parsers.ImportResult:
    """The parser, with no way to turn a hostile file into a 500.

    An unexpected exception would reach the server log as a traceback, and a
    traceback can quote the value that broke it. So anything that is not a
    named refusal becomes one, and only the exception's TYPE is logged.
    """
    try:
        return parsers.parse_upload(filename, raw)
    except E.PromptModuleError:
        raise
    except Exception as exc:  # noqa: BLE001 - see the docstring
        logger.warning("Prompt module parse failed: %s", type(exc).__name__)
        raise E.PromptModuleError(E.JSON_INVALID,
                                  problems=["the file could not be read"]) from None


def _import_sync(filename: str, raw: bytes) -> dict:
    result = _parse(filename, raw)
    with get_db() as con:
        import_id = store.insert_import(con, result, filename)
        rows = [store.row_to_public(r)
                for r in store.rows_for_import(con, import_id)]
    logger.info("Prompt modules imported: import_id=%d rows=%d",
                import_id, len(rows))
    return {"import_id": import_id, "modules": rows, "report": result.report}


@router.post("/import", status_code=201)
async def import_file(request: Request):
    try:
        ctype = (request.headers.get("content-type") or "").lower()
        if ctype.startswith(_FORM_TYPES):
            raise E.PromptModuleError(E.UPLOAD_RAW_ONLY, 415)
        filename = unquote(request.headers.get(FILENAME_HEADER) or "").strip()
        cap = parsers.byte_cap_for(filename)
        raw = await _read_capped(request, cap)
        return await anyio.to_thread.run_sync(_import_sync, filename, raw)
    except E.PromptModuleError as exc:
        return _refusal(exc)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

#: The settings page is in no chat, so `{{char}}` and `{{user}}` stand for
#: themselves, exactly as the page's own counter renders them.
_NAME_STAND_IN = {"name": "{{char}}", "user_name": "{{user}}"}


def _prepared_chars(modules: list[dict]) -> int:
    """What these modules add to a message, prepared as a request prepares
    them (macros, then the sanitizer) plus one separator each: the number the
    page's counter shows (warning 2). A text past its ceiling cannot come out
    of the upload caps; if one ever does, the raw text is counted rather than
    failing the whole list."""
    import prompt_layer

    try:
        texts = prompt_layer.module_texts(modules, **_NAME_STAND_IN)
    except Exception:  # noqa: BLE001 - an estimate beats a failed list
        texts = [m["content"] for m in modules]
    return budget.module_addition_chars(texts)


def _list_sync() -> dict:
    with get_db() as con:
        rows = [store.row_to_public(r) for r in store.list_rows(con)]
        imports = store.list_imports(con)
        on_send = store.list_enabled(con, generation="send")
        on_any = store.list_enabled(con)
    added = _prepared_chars(on_send)
    return {"modules": rows, "imports": imports,
            # "N modules, about X tokens on every message" (A4): an ordinary
            # send. `enabled_any` also counts regenerate-only entries, which
            # still decide that the layer is not sent.
            "enabled_count": len(on_send), "enabled_any": len(on_any),
            "enabled_chars": added, "enabled_tokens": budget.tokens_for(added)}


@router.get("")
async def list_modules() -> dict:
    return await anyio.to_thread.run_sync(_list_sync)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def _settings_sync() -> dict:
    with get_db() as con:
        return {"prompt_layer_enabled": store.layer_enabled(con),
                "reply_length_words": store.reply_length_words(con)}


@router.get("/settings")
async def get_settings() -> dict:
    return await anyio.to_thread.run_sync(_settings_sync)


def _put_settings_sync(layer: bool, words: int | None) -> dict:
    with get_db() as con:
        store.save_settings(con, layer=layer, words=words)
    return _settings_sync()


@router.put("/settings")
async def put_settings(request: Request):
    try:
        body = await _json_body(request)
        if set(body) != {"prompt_layer_enabled", "reply_length_words"}:
            raise E.PromptModuleError(E.SETTINGS_INVALID)
        layer = body["prompt_layer_enabled"]
        words = body["reply_length_words"]
        if not isinstance(layer, bool):
            raise E.PromptModuleError(E.SETTINGS_INVALID)
        if words is not None and (isinstance(words, bool)
                                  or words not in REPLY_LENGTH_WORDS_ALLOWED):
            raise E.PromptModuleError(E.SETTINGS_INVALID)
        return await anyio.to_thread.run_sync(_put_settings_sync, layer, words)
    except E.PromptModuleError as exc:
        return _refusal(exc)


# ---------------------------------------------------------------------------
# Toggle-time H4
# ---------------------------------------------------------------------------

_BUDGET_KEYS = frozenset({"chat_id", "context_budget_tokens", "max_tokens",
                          "model_id"})


def _int_or_none(v, key: str) -> int | None:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
        raise E.PromptModuleError(E.REQUEST_INVALID, field=key)
    return v


def _budget_inputs(body: dict) -> dict:
    """The window the UI is using right now. The server cannot know it: the
    context window and reply length live in the client's generation settings
    and arrive with every completion, so they arrive with a toggle too."""
    out = {"chat_id": _int_or_none(body.get("chat_id"), "chat_id"),
           "context_budget_tokens": _int_or_none(
               body.get("context_budget_tokens"), "context_budget_tokens"),
           "max_tokens": _int_or_none(body.get("max_tokens"), "max_tokens"),
           "model_id": body.get("model_id")}
    if out["model_id"] is not None and not isinstance(out["model_id"], str):
        raise E.PromptModuleError(E.REQUEST_INVALID, field="model_id")
    return out


def _available_chars(b: dict) -> int:
    """Request-time arithmetic with the same defaults the completion path
    uses when the model's metadata is not cached."""
    from openrouter import get_cached_model_metadata
    from routers.completions import _DEFAULT_CONTEXT_LEN, _DEFAULT_MAX_TOKENS

    meta = get_cached_model_metadata(b["model_id"]) if b["model_id"] else None
    model_ctx = (meta or {}).get("context_length") or _DEFAULT_CONTEXT_LEN
    meta_max = (meta or {}).get("max_completion_tokens") or _DEFAULT_MAX_TOKENS
    return budget.history_budget_chars(
        context_budget_tokens=b["context_budget_tokens"],
        model_context_tokens=model_ctx,
        max_tokens=b["max_tokens"] or meta_max)


def _check_budget(con, enabled_after: list[dict], b: dict) -> None:
    """H4 at toggle time through the request path's own counter (A5).

    `routers.completions.module_budget_check` prepares each module the way a
    request does (macros, then the sanitizer), builds the same system message
    and tail around it, and applies `budget.check`. One counting path: the
    toggle cannot pass what the next message would refuse, or the reverse.
    Its refusals are all named: over budget and context_too_large as they
    come, a field that grows past its ceiling as prompt_field_too_large.
    """
    import card_macros
    import prompt_sanitizer
    from routers.completions import module_budget_check

    available = _available_chars(b)
    try:
        module_budget_check(con, b["chat_id"], enabled_after, available)
    except (card_macros.MacroOutputTooLarge, prompt_sanitizer.PromptFieldTooLarge):
        raise HTTPException(400, "prompt_field_too_large") from None


def _enabled_worst_case(con) -> list[dict]:
    return store.list_enabled(con, generation=None)


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - any unreadable body is the same refusal
        raise E.PromptModuleError(E.REQUEST_INVALID) from None
    if not isinstance(body, dict):
        raise E.PromptModuleError(E.REQUEST_INVALID)
    return body


_PATCH_KEYS = frozenset({"enabled", "sort_order", "name"}) | _BUDGET_KEYS


def _patch_sync(module_id: int, body: dict) -> dict:
    b = _budget_inputs(body)
    with get_db() as con:
        con.execute("BEGIN IMMEDIATE")
        row = store.get_row(con, module_id)
        if "name" in body:
            store.rename(con, module_id, body["name"])
        if "sort_order" in body:
            pos = body["sort_order"]
            if isinstance(pos, bool) or not isinstance(pos, int):
                raise E.PromptModuleError(E.REQUEST_INVALID, field="sort_order")
            store.move(con, module_id, pos)
        if "enabled" in body:
            want = body["enabled"]
            if not isinstance(want, bool):
                raise E.PromptModuleError(E.REQUEST_INVALID, field="enabled")
            if want and not row["enabled"]:
                reason = store.block_reason(row)
                if reason is not None:
                    raise E.PromptModuleError(E.CANNOT_ENABLE, 409,
                                              reason=reason)
                after = _enabled_worst_case(con) + [
                    {"id": row["id"], "name": row["name"],
                     "content": row["content"]}]
                _check_budget(con, after, b)
            if want != bool(row["enabled"]):
                store.set_enabled(con, [module_id], want)
        out = store.row_to_public(store.get_row(con, module_id))
    logger.info("Prompt module updated: id=%d", module_id)
    return out


@router.patch("/{module_id}")
async def patch_module(module_id: int, request: Request):
    try:
        body = await _json_body(request)
        if not body or set(body) - _PATCH_KEYS \
                or not set(body) & {"enabled", "sort_order", "name"}:
            raise E.PromptModuleError(E.REQUEST_INVALID)
        return await anyio.to_thread.run_sync(_patch_sync, module_id, body)
    except E.PromptModuleError as exc:
        return _refusal(exc)


def _enable_defaults_sync(import_id: int, body: dict) -> dict:
    """H3's one button: switch on every entry SillyTavern had on, as a whole.

    Entries that cannot fire here (or cannot be enabled at all) are not part
    of the set and are listed. If the whole set does not pass H4 together
    with what is already on, nothing is switched on."""
    b = _budget_inputs(body)
    with get_db() as con:
        con.execute("BEGIN IMMEDIATE")
        if con.execute("SELECT 1 FROM prompt_module_imports WHERE id = ?",
                       (import_id,)).fetchone() is None:
            raise E.PromptModuleError(E.IMPORT_NOT_FOUND, 404)
        rows = con.execute(
            "SELECT * FROM prompt_modules WHERE import_id = ? AND st_enabled = 1 "
            "ORDER BY sort_order, id", (import_id,)).fetchall()
        take, left_out = [], []
        for r in rows:
            reason = store.block_reason(r)
            if reason is not None:
                if r["kind"] == parsers.KIND_MODULE:
                    left_out.append({"id": r["id"], "reason": reason})
                continue
            if not r["enabled"]:
                take.append(r)
        if take:
            after = _enabled_worst_case(con) + [
                {"id": r["id"], "name": r["name"], "content": r["content"]}
                for r in take]
            _check_budget(con, after, b)
            store.set_enabled(con, [r["id"] for r in take], True)
    logger.info("Prompt module preset defaults enabled: import_id=%d n=%d",
                import_id, len(take))
    return {"enabled_ids": [r["id"] for r in take], "left_out": left_out}


@router.post("/enable-preset-defaults")
async def enable_preset_defaults(request: Request):
    try:
        body = await _json_body(request)
        imp = body.get("import_id")
        if set(body) - (_BUDGET_KEYS | {"import_id"}) \
                or isinstance(imp, bool) or not isinstance(imp, int):
            raise E.PromptModuleError(E.REQUEST_INVALID)
        return await anyio.to_thread.run_sync(_enable_defaults_sync, imp, body)
    except E.PromptModuleError as exc:
        return _refusal(exc)


def _delete_sync(module_id: int) -> None:
    with get_db() as con:
        con.execute("BEGIN IMMEDIATE")
        store.delete(con, module_id)
    logger.info("Prompt module deleted: id=%d", module_id)


@router.delete("/{module_id}")
async def delete_module(module_id: int):
    try:
        await anyio.to_thread.run_sync(_delete_sync, module_id)
        return {"ok": True}
    except E.PromptModuleError as exc:
        return _refusal(exc)
