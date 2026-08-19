"""routers/notebook.py -- the per-chat notebook and the boundaries (FAZ 1).

Routes:
    GET    /notebook/{chat_id}                 - notes for one chat
    POST   /notebook/{chat_id}                 - add a note
    PATCH  /notebook/entries/{id}              - edit a note
    DELETE /notebook/entries/{id}              - remove a note
    POST   /notebook/{chat_id}/reorder         - set the order
    GET    /notebook/{chat_id}/boundaries      - limits in force for this chat
    GET    /notebook/boundaries                - the global set
    POST   /notebook/boundaries                - add a limit (global or chat)
    DELETE /notebook/boundaries/{id}           - remove a limit
    POST   /notebook/{chat_id}/use-global      - follow the global set, or not

Privacy invariants:
    - Note and boundary TEXT is never logged, at any level. Ids and counts
      only. A shipped memory feature elsewhere leaked health and relationship
      data through eighteen INFO-level statements; the notebook is the most
      distilled text this app holds, so the rule here is absolute rather than
      case-by-case.
    - The CRUD half of this module imports no network code at all: no httpx,
      requests, urllib.request or keyring, anywhere.
    - The extraction half (the four /extract routes) does import `openrouter`
      and `proxy_health`, because a route that reaches OpenRouter must pass
      the same gate as every other outbound path. The imports are function-
      local so the boundary is visible at the two call sites rather than at
      the top of a file whose other twenty routes never leave the machine.
      This paragraph used to claim the module imported neither - written when
      that was true, left standing when it stopped being true, which is the
      kind of line a later reader trusts instead of re-checking.

Every handler hops off the event loop before touching the database (audit
KÖK 8). The reads are small, but "small" is a property of today's data and the
loop being stalled at a fixed cadence is how the last one of these was found.
"""

import logging
import re

import anyio.to_thread
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import config
import notebook_store as notebook

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notebook", tags=["notebook"])

#: Large enough that the fraction never binds, so the gauge reports what the
#: notebook actually costs rather than what one particular model would allow.
_GAUGE_AVAILABLE = 10_000_000


class EntryBody(BaseModel):
    text: str = Field(min_length=1)
    kind: str = "fact"
    durability: str = "permanent"
    importance: int = Field(default=2, ge=1, le=3)
    pinned: bool = False


class EntryPatch(BaseModel):
    text: str | None = Field(default=None, min_length=1)
    kind: str | None = None
    durability: str | None = None
    importance: int | None = Field(default=None, ge=1, le=3)
    pinned: bool | None = None
    # `provenance` is absent and stays absent. Accepting it here - even to
    # ignore it - would put the idea in the schema that it is a thing an edit
    # can carry, and the next reader would wire it up.


class AutoAcceptBody(BaseModel):
    enabled: bool


class ReorderBody(BaseModel):
    ordered_ids: list[int]


class BoundaryBody(BaseModel):
    label: str = Field(min_length=1)
    phrasing: str = Field(min_length=1)
    severity: str
    chat_id: int | None = None
    polarity: str = "avoid"
    on_violation: str = "pause"
    rating_ceiling: str | None = None
    # `source` is absent: a limit created through this route is one a person
    # typed, so it is `explicit` by construction. The database refuses an
    # inferred hard limit, and this route cannot produce an inferred one at all.
    #
    # `max_length` is absent too, and that is the same choice EntryBody makes
    # about notes. The ceiling is notebook_store.BOUNDARY_MAX_CHARS and it is
    # enforced in the domain function, so no route can be added that skips it.
    # Declaring it here as well would only change WHICH refusal arrives first:
    # pydantic would answer 422 with a validation structure instead of the 400
    # carrying `boundary_too_long`, and the reader would get "Something went
    # wrong" in place of the sentence written for exactly this case.


class UseGlobalBody(BaseModel):
    use_global: bool


def _refuse(exc: notebook.NotebookError):
    raise HTTPException(400, exc.code)


# LITERAL PATHS FIRST. FastAPI matches in registration order, so with
# `/{chat_id}` declared above them a request for `/notebook/boundaries` binds
# chat_id="boundaries" and 422s - the route exists and is unreachable.
@router.get("/boundaries")
async def list_global_boundaries() -> dict:
    rows = await anyio.to_thread.run_sync(notebook.list_boundaries, None)
    return {"boundaries": rows}


@router.post("/boundaries")
async def create_boundary(body: BoundaryBody) -> dict:
    try:
        row = await anyio.to_thread.run_sync(
            lambda: notebook.create_boundary(
                body.label, body.phrasing, body.severity,
                chat_id=body.chat_id, polarity=body.polarity,
                on_violation=body.on_violation,
                rating_ceiling=body.rating_ceiling))
    except notebook.NotebookError as exc:
        _refuse(exc)
    logger.info("Boundary added: scope=%s id=%d", row["scope"], row["id"])
    return row


@router.delete("/boundaries/{boundary_id}")
async def delete_boundary(boundary_id: int) -> dict:
    removed = await anyio.to_thread.run_sync(
        notebook.delete_boundary, boundary_id)
    if not removed:
        raise HTTPException(404, "boundary_not_found")
    logger.info("Boundary removed: id=%d", boundary_id)
    return {"ok": True}



# ── FAZ 5: what the background worker did, and whether it may keep going ────

@router.get("/worker")
async def worker_status() -> dict:
    """Counters, not silence.

    A skipped extraction that leaves no trace makes "the notebook has proposed
    nothing this week" and "the notebook has refused sixty times for a reason
    nobody can see" the same screen. Every refusal carries its reason here.
    """
    import notebook_worker

    def _read() -> dict:
        from database import get_db
        with get_db() as con:
            return {
                "stats": notebook.extraction_stats(con),
                "spend": notebook.spend_today(con),
                # Same connection, same poll - not a second round trip and
                # not a new one. See notebook.spend_lifetime for why a plain
                # SUM over the whole table is the cheap answer here, not the
                # expensive one.
                "spend_lifetime": notebook.spend_lifetime(con),
            }

    body = await anyio.to_thread.run_sync(_read)
    body["worker"] = notebook_worker.worker.status()
    body["daily_cap"] = config.NOTEBOOK_DAILY_CALL_CAP
    return body


@router.post("/worker/reset")
async def worker_reset() -> dict:
    """The hand on the breaker.

    Without it, recovering from a stopped breaker means restarting the whole
    application after fixing whatever broke - which is a breaker plus an
    insult. The counters stay; only the refusal is lifted.
    """
    import notebook_worker

    notebook_worker.worker.breaker.reset()
    logger.info("Notebook extraction breaker reset by the user.")
    return {"ok": True, "worker": notebook_worker.worker.status()}


class SafewordBody(BaseModel):
    word: str = Field(default="", max_length=64)


@router.get("/safeword")
async def get_safeword() -> dict:
    return {"word": await anyio.to_thread.run_sync(notebook.safeword)}


@router.post("/safeword")
async def set_safeword(body: SafewordBody) -> dict:
    """The one thing here that is not a request to a model.

    Every other limit is a paragraph in a prompt, and a paragraph in a prompt
    is something a model may decline to honour. This is matched in code before
    the provider is called, and when it matches the turn does not happen: no
    request, no storage, no reply. Set it to an empty string to turn it off.
    """
    try:
        await anyio.to_thread.run_sync(notebook.set_safeword, body.word)
    except notebook.NotebookError as exc:
        _refuse(exc)
    # The WORD is never logged. It is a phrase the user chose for the worst
    # moment they expect to have, and it belongs in the vault and nowhere else.
    logger.info("Safeword %s.", "set" if body.word.strip() else "cleared")
    return {"ok": True}


@router.get("/auto-accept")
async def get_auto_accept() -> dict:
    def _read() -> dict:
        from database import get_setting
        raw = get_setting(config.SETTING_NOTEBOOK_AUTO_ACCEPT)
        # Unset IS the default, and the default is on. Reading an unset key as
        # "off" would make a fresh install silently do nothing and look like a
        # broken worker rather than a setting.
        return {"enabled": raw != "0"}
    return await anyio.to_thread.run_sync(_read)


@router.post("/auto-accept")
async def set_auto_accept(body: AutoAcceptBody) -> dict:
    def _write() -> None:
        from database import set_setting
        set_setting(config.SETTING_NOTEBOOK_AUTO_ACCEPT,
                    "1" if body.enabled else "0")
    await anyio.to_thread.run_sync(_write)
    logger.info("Notebook auto-accept set to %s.", body.enabled)
    return {"ok": True}


@router.post("/entries/{entry_id}/accept")
async def accept_entry(entry_id: int) -> dict:
    """Promote a proposal. The ONLY thing this changes is `status`.

    `provenance` stays `model` forever - promotion is the classic bypass, and
    a route that could rewrite it would leave `provenance='model'` with no
    live rows, so its guard would pass by describing an empty set.
    """
    def _accept() -> dict:
        # Read first, and read WHAT it is. Without this the route promoted any
        # id in the database and RETURNED THE ROW - so it answered "what does
        # note 412 say" for a note in a chat the caller never opened. The same
        # gap exists on patch and delete, but only this one hands back text.
        from database import get_db
        with get_db() as con:
            row = con.execute(
                "SELECT status, retired_at FROM notebook_entries WHERE id = ?",
                (entry_id,)).fetchone()
        if row is None:
            raise notebook.NotebookError("notebook_entry_not_found")
        if row["retired_at"] is not None:
            # Accepting a replaced note would show it as kept in the panel
            # while the payload still, correctly, leaves it out.
            raise notebook.NotebookError("notebook_entry_not_found")
        return notebook.update_entry(entry_id,
                                     status=notebook.STATUS_ACCEPTED)

    try:
        entry = await anyio.to_thread.run_sync(_accept)
    except notebook.NotebookError as exc:
        _refuse(exc)
    logger.info("Notebook proposal accepted: id=%d", entry_id)
    return entry


@router.get("/{chat_id}")
async def list_entries(chat_id: int) -> dict:
    """The notes, and what they COST - measured here, not re-derived there.

    `notebook_chars` crosses the wire the way `/tts/voice-mode` sends
    `prompt_chars`: the frontend charges an opaque number instead of rebuilding
    the block. The voice block is the one part of the fixed cost the estimator
    does not duplicate, and it is the one part that cannot drift. The character
    header drifted once already because two languages built the same string;
    this is that lesson applied before it can happen again.
    """
    def _read() -> dict:
        entries = notebook.list_entries(chat_id)
        blocks = notebook.build_notebook_blocks(chat_id, _GAUGE_AVAILABLE)
        return {
            "entries": entries,
            # Gauge figure only: the real ceiling depends on the model chosen
            # for the turn, which this route does not know. Sized against a
            # generous budget so it reports the full cost rather than a
            # truncated one, and the per-turn truth arrives in the done frame.
            "notebook_chars": (len(blocks["user_block"])
                               + len(blocks["model_block"])
                               + len(blocks["boundary_block"])),
        }
    return await anyio.to_thread.run_sync(_read)


@router.post("/{chat_id}")
async def create_entry(chat_id: int, body: EntryBody) -> dict:
    try:
        entry = await anyio.to_thread.run_sync(
            lambda: notebook.create_entry(
                chat_id, body.text, kind=body.kind,
                durability=body.durability, importance=body.importance,
                pinned=body.pinned))
    except notebook.NotebookError as exc:
        _refuse(exc)
    logger.info("Notebook entry added: chat=%d id=%d", chat_id, entry["id"])
    return entry


@router.patch("/entries/{entry_id}")
async def patch_entry(entry_id: int, body: EntryPatch) -> dict:
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(400, "notebook_entry_invalid")
    try:
        entry = await anyio.to_thread.run_sync(
            lambda: notebook.update_entry(entry_id, **fields))
    except notebook.NotebookError as exc:
        _refuse(exc)
    return entry


@router.delete("/entries/{entry_id}")
async def delete_entry(entry_id: int) -> dict:
    removed = await anyio.to_thread.run_sync(notebook.delete_entry, entry_id)
    if not removed:
        raise HTTPException(404, "notebook_entry_not_found")
    logger.info("Notebook entry removed: id=%d", entry_id)
    return {"ok": True}


@router.post("/{chat_id}/reorder")
async def reorder(chat_id: int, body: ReorderBody) -> dict:
    await anyio.to_thread.run_sync(
        lambda: notebook.reorder(chat_id, body.ordered_ids))
    return {"ok": True}


@router.get("/{chat_id}/boundaries")
async def list_chat_boundaries(chat_id: int) -> dict:
    """What is actually in force here - global plus this chat's, or this
    chat's alone when it has been told to stand on its own."""
    def _read() -> dict:
        rows = notebook.list_boundaries(chat_id)
        # The flag comes back with the rows, because the screen has to show the
        # switch in the position it is actually in. Left to a local default it
        # reads "on" after every remount - which is precisely the "you believe
        # it is in force and it is not" failure this panel warns about.
        return {"boundaries": rows,
                "use_global": notebook.uses_global_boundaries(chat_id)}
    try:
        return await anyio.to_thread.run_sync(_read)
    except notebook.NotebookError as exc:
        raise HTTPException(404, exc.code) from None


@router.post("/{chat_id}/use-global")
async def set_use_global(chat_id: int, body: UseGlobalBody) -> dict:
    await anyio.to_thread.run_sync(
        lambda: notebook.set_use_global_boundaries(chat_id, body.use_global))
    return {"ok": True, "use_global": body.use_global}


# ---------------------------------------------------------------------------
# FAZ 4 - choosing an extractor, and trying it before trusting it
# ---------------------------------------------------------------------------

class ExtractSettingsBody(BaseModel):
    model_id: str | None = None
    prompt_language: str | None = None



def _relay(exc) -> tuple[int, str]:
    """An OpenRouter failure, translated into something a reader has a
    sentence for.

    `raise HTTPException(502, str(exc))` looked harmless and was not: the
    string is the RAW reason (`openrouter_auth_failed`, `api_key_not_set`,
    `openrouter_unreachable`), and none of those are in the catalogue. An
    expired key on the chat path reads "your API key was rejected"; the same
    key, on this panel, read "Something went wrong. Please try again."

    The elif chain relays `reason` rather than literals for the same reason
    models_router does, so RELAY_DETAILS below is what enumerates it.
    """
    reason = getattr(exc, "reason", None) or str(exc)
    if reason in ("api_key_invalid", "api_key_not_set",
                  "openrouter_auth_failed", "api_key_required_by_openrouter"):
        return 401, reason
    if reason == "proxy_auth_failed":
        # The user's OWN proxy refused the tunnel. Never blamed on the
        # provider, so the UI can point at the proxy settings.
        return 502, reason
    if reason == "openrouter_timeout":
        return 504, reason
    if reason == "openrouter_unreachable":
        return 502, reason
    return 502, "notebook_extract_failed"


#: Every detail the two provider-facing notebook routes can put in front of a
#: reader. Five of the six are relayed variables rather than literals, so a
#: reader of the raise sites sees `reason` and nothing about what it holds -
#: which is exactly how the raw-reason leak survived review in the first
#: place. `tests/error_enumeration.py` reads this.
RELAY_DETAILS: frozenset[str] = frozenset({
    "api_key_invalid",
    "api_key_not_set",
    "openrouter_auth_failed",
    "api_key_required_by_openrouter",
    "proxy_auth_failed",
    "openrouter_timeout",
    "openrouter_unreachable",
    "notebook_extract_failed",
})


@router.get("/extract/models")
async def list_extraction_models() -> dict:
    """Models a background extraction may use. Filtered, not ranked by taste.

    Two conditions and both are promises rather than preferences: the endpoint
    keeps no data, and it honours a strict JSON schema. Everything else is
    hidden - a model that cannot do the job has no business being pickable and
    then failing at request time.
    """
    import openrouter
    from proxy_health import enforce_proxy_gate

    # THE gate every outbound path must pass. Skipping it here would be the
    # exact failure its docstring records: in the proxy_required + no-proxy
    # state that every other path refuses, this one would still have gone out
    # unproxied, carrying the user's real IP and their key.
    await enforce_proxy_gate()
    try:
        models = await openrouter.fetch_extraction_models()
    except openrouter.OpenRouterError as exc:
        # Unpacked into named locals rather than starred into the call: the
        # error census reads raise sites syntactically, and `HTTPException(
        # *_relay(exc))` is invisible to it - the route would ship codes the
        # gate believed nobody could produce.
        status, detail = _relay(exc)
        raise HTTPException(status, detail) from None
    return {"models": models}


@router.get("/extract/settings")
async def get_extract_settings() -> dict:
    def _read() -> dict:
        from database import get_setting
        return {
            # None means extraction never runs. There is deliberately no
            # default: a background job spending somebody's credits on a model
            # they never chose is not a convenience.
            "model_id": get_setting(config.SETTING_NOTEBOOK_MODEL) or None,
            "prompt_language":
                get_setting(config.SETTING_NOTEBOOK_PROMPT_LANG) or "en",
        }
    return await anyio.to_thread.run_sync(_read)


#: An OpenRouter model id is `author/slug`, optionally `:variant`. The check
#: is a SHAPE check, not a membership check: membership would need a network
#: call on every save, and the wire guarantee does not depend on it anyway -
#: PROVIDER_POLICY pins zdr/deny/no-fallbacks on the request itself, so a
#: model whose endpoint policy changed gets no qualifying endpoint rather than
#: a downgraded one. What this stops is an unbounded string reaching the
#: settings table and, from there, the payload.
_MODEL_ID = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._:-]+$")
MODEL_ID_MAX_CHARS = 128


@router.post("/extract/settings")
async def set_extract_settings(body: ExtractSettingsBody) -> dict:
    if body.prompt_language is not None and body.prompt_language not in ("en", "tr"):
        raise HTTPException(400, "notebook_language_unknown")
    if body.model_id:
        if len(body.model_id) > MODEL_ID_MAX_CHARS:
            raise HTTPException(400, "notebook_model_id_too_long")
        if not _MODEL_ID.match(body.model_id):
            raise HTTPException(400, "notebook_model_id_invalid")

    def _write() -> None:
        from database import set_setting
        if body.model_id is not None:
            set_setting(config.SETTING_NOTEBOOK_MODEL, body.model_id)
        if body.prompt_language is not None:
            set_setting(config.SETTING_NOTEBOOK_PROMPT_LANG,
                        body.prompt_language)
    await anyio.to_thread.run_sync(_write)
    # The id is not logged as a secret but it is not interesting either; what
    # matters in the log is that the choice changed.
    logger.info("Notebook extraction settings updated.")
    return {"ok": True}


@router.post("/{chat_id}/extract/dry-run")
async def dry_run(chat_id: int) -> dict:
    """Run the extractor once and SHOW the result. Store nothing.

    This exists because of the one thing that could not be measured for the
    owner: whether a small, cheap model reads THEIR Turkish well enough. The
    literature says structured output degrades on non-English input and that
    the commonest failure is the model answering schema fields in the user's
    language - but nobody has measured it for Turkish, and no amount of
    reasoning here substitutes for one look at real output.

    So: real transcript, real prompt, real schema, nothing written. The reply
    comes back beside the source text so the six failure shapes are visible
    rather than described - fields answered in the wrong language, quotes
    translated instead of copied, diacritics flattened, the wrong speaker
    credited, a schema violation, or a hypothetical recorded as a fact.
    """
    import notebook_extract
    import openrouter
    from proxy_health import enforce_proxy_gate

    model_id = await anyio.to_thread.run_sync(notebook_extract.extract_model)
    if not model_id:
        raise HTTPException(400, "notebook_model_not_chosen")
    # Same gate as every other outbound path, and before the transcript is
    # read: refusing after loading messages would have done the work anyway.
    await enforce_proxy_gate()

    def _load() -> tuple[list[str], list[str], str, list[str]]:
        from database import get_db
        with get_db() as con:
            rows = con.execute(
                "SELECT role, content FROM messages "
                "WHERE chat_id = ? AND active = 1 ORDER BY id DESC LIMIT 12",
                (chat_id,)).fetchall()
            card_row = con.execute(
                "SELECT c.description FROM chats ch "
                "JOIN characters c ON c.id = ch.character_id WHERE ch.id = ?",
                (chat_id,)).fetchone()
        lines = [f"{r['role']}: {r['content']}" for r in reversed(rows)]
        existing = [e["text"] for e in
                    notebook.list_entries(chat_id, include_retired=False)]
        return lines[:-4], lines[-4:], (card_row[0] if card_row else ""), existing

    recent, new, card, existing = await anyio.to_thread.run_sync(_load)
    if not new:
        raise HTTPException(400, "notebook_nothing_to_read")

    lang = await anyio.to_thread.run_sync(
        lambda: (__import__("database").get_setting(
            config.SETTING_NOTEBOOK_PROMPT_LANG) or "en"))
    messages = [
        {"role": "system",
         "content": notebook_extract.system_prompt(lang)},
        {"role": "user",
         "content": notebook_extract.build_user_message(
             card=card, existing=existing, recent=recent, new=new)},
    ]

    # The block, before the request and not after it. Reserved rather than
    # recorded: a failed call is billed too, and a counter that only counts
    # successes bounds nothing.
    def _claim() -> int:
        from database import get_db
        with get_db() as con:
            return notebook.claim_call(con, config.NOTEBOOK_DAILY_CALL_CAP)

    try:
        await anyio.to_thread.run_sync(_claim)
    except notebook.NotebookError as exc:
        raise HTTPException(429, exc.code) from None

    try:
        reply = await openrouter.complete(
            messages, model_id,
            {"max_tokens": notebook_extract.MAX_TOKENS, "temperature": 0},
            dict(config.PROVIDER_POLICY),
            response_format=notebook_extract.RESPONSE_FORMAT,
        )
    except openrouter.OpenRouterError as exc:
        # Unpacked into named locals rather than starred into the call: the
        # error census reads raise sites syntactically, and `HTTPException(
        # *_relay(exc))` is invisible to it - the route would ship codes the
        # gate believed nobody could produce.
        status, detail = _relay(exc)
        raise HTTPException(status, detail) from None

    chunk = chr(10).join(new)
    usage = notebook_extract.usage_of(reply)

    def _record() -> None:
        from database import get_db
        with get_db() as con:
            notebook.record_usage(con, usage)

    await anyio.to_thread.run_sync(_record)
    try:
        proposals, dropped = notebook_extract.parse_reply(reply, chunk, existing)
        failure = None
    except notebook_extract.ExtractionFailed as exc:
        proposals, dropped, failure = [], {}, str(exc)

    raw = ((reply.get("choices") or [{}])[0].get("message") or {}).get("content")
    return {
        "model_id": model_id,
        "prompt_language": lang,
        # The source beside the answer: a dry run whose output cannot be
        # compared against what it read is a number, not evidence.
        "source": chunk,
        "raw": raw,
        "proposals": proposals,
        # Everything the model returned MINUS what survived the code filter.
        # The gap is the interesting part - but broken out by REASON, because
        # one integer cannot tell "a quote was invented" (the defence working)
        # from "a Turkish quote failed a byte comparison" (the defence eating
        # a true fact), and those call for opposite responses.
        #
        # It is counted by the parser rather than by re-parsing `raw` here:
        # that version called .get() on whatever JSON came back, so an array
        # reply turned a billed call into an AttributeError.
        "dropped": sum(dropped.values()),
        "dropped_by_reason": dropped,
        "failure": failure,
        "usage": usage,
    }

