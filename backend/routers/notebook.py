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


#: Codes that are a MISSING THING rather than a bad request.
#:
#: `chat_not_found` already reached the wire as 404 from `list_chat_boundaries`
#: and as 400 from here, for the same code, and the shared catalogue declares
#: it as 404. One code, two statuses, is a contract the client cannot write
#: against - and the catalogue gate is what says which of the two was right.
#: `boundary_not_found` joined them when the chat-scope guard started
#: raising it: `:160` has always answered 404 for that code directly,
#: so leaving it out here would have given ONE code two statuses again -
#: the exact contract this set exists to close.
_NOT_FOUND_CODES = frozenset({"chat_not_found", "notebook_entry_not_found",
                              "boundary_not_found"})


def _refuse(exc: notebook.NotebookError):
    raise HTTPException(404 if exc.code in _NOT_FOUND_CODES else 400, exc.code)


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
async def delete_boundary(boundary_id: int, chat_id: int | None = None) -> dict:
    """Remove a limit. Permanent, by design.

    `chat_id` is OPTIONAL here and required on the note routes, and the
    difference is the data: a GLOBAL limit belongs to no chat, so demanding
    one would mean inventing a scope for a row that has none.

    Optional is not unscoped. Omitting it means "this is a global limit",
    and a chat-scoped one is refused; it does not mean "delete whatever this
    id is". The store enforces that, not this route - a check written here
    would be one caller's promise about a column that needs the next writer
    to remember it.
    """
    try:
        removed = await anyio.to_thread.run_sync(
            notebook.delete_boundary, boundary_id, chat_id)
    except notebook.NotebookError as exc:
        _refuse(exc)
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
    # No max_length here, and the reason is the same one BoundaryBody gives
    # itself a hundred lines above: declaring the ceiling twice only changes
    # WHICH refusal arrives first, and pydantic's arrives worse.
    #
    # Worse in a way that matters for this field in particular. There is no
    # RequestValidationError handler in this app, so FastAPI's default runs,
    # and pydantic v2's errors() puts the REJECTED VALUE in an `input` field
    # that goes back over the wire. For a safeword - a phrase somebody chose
    # for the worst moment they expect to have - that is the one string in
    # this application that must never come back out.
    #
    # The ceiling lives in notebook_store.set_safeword, which raises
    # safeword_too_long, which _refuse turns into a 400 carrying a code and
    # nothing else. That path also measures the word AFTER collapsing runs of
    # whitespace, so a long-looking phrase that is short once typed out is
    # accepted rather than refused on a technicality.
    word: str = Field(default="")


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
async def get_auto_accept(chat_id: int | None = None) -> dict:
    """What the switch should show. With a chat, what will actually happen.

    The panel asked this route and the extractor asked
    `notebook.auto_accept_for`, and the two answered differently for exactly
    the chats the difference matters in: one opened from an imported card
    carries a per-chat override that forces review, and the switch went on
    showing the global setting as though it applied.

    `chat_id` is optional because the same route answers the global question
    when no chat is open. `effective` is the chat's real answer; `enabled`
    stays the global setting, unchanged, because that is what the POST
    beneath this writes and the switch has to reflect what it will write.
    """
    def _read() -> dict:
        from database import get_setting
        raw = get_setting(config.SETTING_NOTEBOOK_AUTO_ACCEPT)
        if chat_id is not None:
            from database import get_db
            with get_db() as con:
                effective = notebook.auto_accept_for(con, chat_id)
                forced = con.execute(
                    "SELECT notebook_auto_accept_override FROM chats "
                    "WHERE id = ?", (chat_id,)).fetchone()
            return {
                "enabled": raw != "0",
                "effective": effective,
                # Why they differ, when they do. Without this the panel can
                # say "off" and not say that the chat is what turned it off.
                "overridden": forced is not None and forced[0] is not None,
            }
        # Unset IS the default, and the default is on. Reading an unset key as
        # "off" would make a fresh install silently do nothing and look like a
        # broken worker rather than a setting.
        return {"enabled": raw != "0", "effective": raw != "0",
                "overridden": False}
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
async def accept_entry(entry_id: int, chat_id: int) -> dict:
    """Promote a proposal. The ONLY thing this changes is `status`.

    `provenance` stays `model` forever - promotion is the classic bypass, and
    a route that could rewrite it would leave `provenance='model'` with no
    live rows, so its guard would pass by describing an empty set.

    `chat_id` is REQUIRED, and required rather than optional on purpose: an
    optional scope is a scope every existing caller silently opts out of.
    A query parameter rather than a path segment because the shape of these
    three routes is already load-bearing for four frontend hooks, and moving
    them buys nothing this does not - what matters is that the caller must
    name the chat it is acting from and be refused if the note is not in it.
    """
    def _accept() -> dict:
        # Read first, and read WHAT it is. Without this the route promoted
        # any id in the database and RETURNED THE ROW - so it answered "what
        # does note 412 say" for a note in a chat the caller never opened.
        #
        # The old version of this comment said the same gap existed on patch
        # and delete "but only this one hands back text". That was wrong:
        # `update_entry` ends with `SELECT *` and `patch_entry` returns what
        # it gets, so patch handed back text too. All three take a scope now.
        from database import get_db
        with get_db() as con:
            row = con.execute(
                "SELECT status, retired_at, chat_id, supersedes_id "
                "FROM notebook_entries WHERE id = ? AND chat_id = ?",
                (entry_id, chat_id)).fetchone()
        if row is None:
            # Not found, or not THIS chat's - answered the same way on
            # purpose. Telling a caller "that note exists, just not here"
            # is itself the answer they should not be getting.
            raise notebook.NotebookError("notebook_entry_not_found")
        if row["retired_at"] is not None:
            # Accepting a replaced note would show it as kept in the panel
            # while the payload still, correctly, leaves it out.
            raise notebook.NotebookError("notebook_entry_not_found")
        entry = notebook.update_entry(entry_id,
                                      status=notebook.STATUS_ACCEPTED)
        # The intent the proposal was carrying, applied at the moment it
        # becomes true. In automatic mode commit_extraction retires the
        # replaced note inside the same transaction; in REVIEW mode it
        # deliberately does not - retiring on behalf of a suggestion nobody
        # has read removes a note the reader approved. So the intent waited
        # on the row, and this is the moment somebody read it and said yes.
        #
        # Same guard as the automatic path, from the same predicate: a
        # model's word may only retire the model's own accepted, unpinned
        # notes. Refusal is silent and normal.
        if row["supersedes_id"] is not None:
            with get_db() as con:
                notebook.retire_superseded(
                    con, row["chat_id"], row["supersedes_id"], entry_id)
        return entry

    try:
        entry = await anyio.to_thread.run_sync(_accept)
    except notebook.NotebookError as exc:
        _refuse(exc)
    logger.info("Notebook proposal accepted: id=%d", entry_id)
    return entry


# ABOVE `/{chat_id}`, and it has to stay there. FastAPI matches in
# declaration order, so a single-segment literal declared BELOW a
# single-segment parameter never matches: the parameter wins and answers 422
# for a path that is not a number. Every literal route in this file sits
# above that line for the same reason.
@router.post("/sweep/{chat_id}")
async def sweep_chat(chat_id: int) -> dict:
    """Read the part of this chat nobody has read.

    The ordinary worker only ever moves forward. A chat that met this feature
    with a long history behind it deliberately starts at the PRESENT - a
    notebook describing a conversation four hundred messages ago is worse
    than an empty one - and until now that decision was permanent, because
    the cursor is a maximum and could never look under itself again.

    This is the way back. One work unit per press, through the worker's own
    door, claimed against the same daily cap and recorded in the same table.
    """
    import notebook_worker

    result = await notebook_worker.worker.sweep(chat_id)
    logger.info("Notebook sweep for a chat: %s",
                "started" if result.get("started") else result.get("reason"))
    return result


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
    try:
        return await anyio.to_thread.run_sync(_read)
    except notebook.NotebookError as exc:
        # `list_entries` answers `[]` for a chat that is not there;
        # `build_notebook_blocks` reaches `list_boundaries`, which raises
        # `chat_not_found`. Uncaught, that left the route as the one place
        # in this file that answered 500 with Starlette's plain-text body -
        # no JSON, no code, and the generic toast at the other end. Reached
        # by an ordinary race: a chat deleted in one window while the panel
        # refreshes in another. Its sibling `/{chat_id}/boundaries` has
        # caught the identical exception all along.
        _refuse(exc)


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
async def patch_entry(entry_id: int, chat_id: int, body: EntryPatch) -> dict:
    """Edit a note. Scoped, and it returns the row - which is why.

    This route hands the note's text back, so an unscoped edit was also an
    unscoped read: any id in the vault, from any chat.
    """
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(400, "notebook_entry_invalid")
    try:
        entry = await anyio.to_thread.run_sync(
            lambda: notebook.update_entry(entry_id, chat_id, **fields))
    except notebook.NotebookError as exc:
        _refuse(exc)
    return entry


@router.delete("/entries/{entry_id}")
async def delete_entry(entry_id: int, chat_id: int) -> dict:
    try:
        removed = await anyio.to_thread.run_sync(
            notebook.delete_entry, entry_id, chat_id)
    except notebook.NotebookError as exc:
        _refuse(exc)
    if not removed:
        raise HTTPException(404, "notebook_entry_not_found")
    logger.info("Notebook entry removed: id=%d", entry_id)
    return {"ok": True}


class ChatAutoAcceptBody(BaseModel):
    """`None` clears the override and returns this chat to the global switch,
    which is what a NULL in the column has always meant."""

    enabled: bool | None = None


@router.post("/{chat_id}/auto-accept")
async def set_chat_auto_accept(chat_id: int, body: ChatAutoAcceptBody) -> dict:
    """Decide for THIS chat, or hand the decision back to the global switch.

    The column this writes has been read on every extraction since it was
    added and written by exactly one statement - the chat INSERT. There was
    no way to change it afterwards, so a chat that was wrongly trusted stayed
    trusted, and the escape hatch the design assumed did not exist.

    It matters most for the case the import signal cannot see: somebody who
    pastes a downloaded card's fields into the form by hand leaves no record
    that the text came from outside. The app cannot know that. The reader
    can, and this is where they say so.
    """
    try:
        await anyio.to_thread.run_sync(
            lambda: notebook.set_auto_accept_override(chat_id, body.enabled))
    except notebook.NotebookError as exc:
        _refuse(exc)
    logger.info("Notebook auto-accept override set for a chat: %s",
                "cleared" if body.enabled is None else body.enabled)
    return {"ok": True, "enabled": body.enabled}


@router.post("/{chat_id}/reorder")
async def reorder(chat_id: int, body: ReorderBody) -> dict:
    """Put the notes in a new order.

    `notebook.reorder` refuses a partial list - reordering half a notebook
    would silently drop the other half - and that refusal reached the wire as
    an uncaught 500. A user action must not produce one.
    """
    try:
        await anyio.to_thread.run_sync(
            lambda: notebook.reorder(chat_id, body.ordered_ids))
    except notebook.NotebookError as exc:
        _refuse(exc)
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
    """Turn the global limits on or off for this chat.

    The UPDATE behind this matched nothing for a chat that does not exist and
    said `ok: true` anyway - so a caller could be told a safety setting had
    been applied to a conversation that was not there.
    """
    try:
        await anyio.to_thread.run_sync(
            lambda: notebook.set_use_global_boundaries(chat_id,
                                                       body.use_global))
    except notebook.NotebookError as exc:
        _refuse(exc)
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

