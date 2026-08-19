"""The background extractor: one supervised worker, one bounded queue.

This is the part of the notebook that runs when nobody is watching, on the
user's own API key, and every decision in it is shaped by that.

WHAT IT DOES
    After a turn is persisted, `offer(chat_id)` drops a chat id in a queue and
    returns immediately - the send path never waits for it and never fails
    because of it. The worker wakes, counts how many messages have arrived
    since the last extraction, and if that reaches the threshold it reads the
    delta plus two messages of disambiguation window, asks the chosen model
    for at most six facts, filters them in code, and writes the survivors.

WHAT IT REFUSES TO DO
    *Retry.* `network_client.py:68-79` documents why the whole app refuses:
    httpx reads the body inside `send()`, so "the request never left" and "it
    was accepted, generated, BILLED and then cut off" raise the same
    exception. Re-sending the second pays for the answer twice. A failed
    extraction is skipped and the next threshold tries again under a NEW work
    key - which is a fresh question, not a retry of the old one.

    *Spend without a ceiling.* The daily cap is claimed BEFORE the request and
    is a block, not an alert. The largest documented runaway in this space was
    not a loop; it was a context that grew every call while a budget alarm
    dutifully fired.

    *Treat a locked vault as a failure.* Auto-lock can fire mid-extraction -
    background work deliberately does not feed the idle timer. The worker
    accepts that silently: it is a cancellation, and counting it as a failure
    would walk the circuit breaker towards "stop" every time somebody left
    their desk.

    *Outlive its own crash.* The packaged exe never runs a shutdown path -
    uvicorn is a daemon thread and the process simply dies with the window.
    So cancellation must be survivable at any point, and an extraction in
    flight when the window closes is lost. That is accepted and written down
    rather than defended against.

THE CIRCUIT BREAKER
    Five consecutive failures open it for a cooldown; twenty stop it until
    somebody says otherwise. The batch halves on the way down. The half-open
    probe is a SINGLE call, and a too-fast Open->Half-Open cycle is what makes
    a breaker flap, so the cooldown grows with each trip.
"""
from __future__ import annotations

import asyncio
import functools
import logging

import anyio.to_thread

import config
import notebook_extract
import notebook_store as notebook

logger = logging.getLogger(__name__)

#: A bounded queue, and the bound is written down. `asyncio.Queue(maxsize=0)`
#: is INFINITE - the default that looks like "no queueing" and is the opposite.
#: When it fills, the OLDEST offer is dropped: the newest turn is the one whose
#: context is worth reading, and a queue that drops the newest would starve
#: exactly the chat somebody is using.
QUEUE_MAXSIZE = 32

#: Every reason a run can be refused. It is a USER-FACING vocabulary - the
#: status route hands these to the panel, which turns each into a sentence -
#: so it is declared here and checked against the panel's map, exactly like
#: the error catalogue is checked against errorMessages.ts. A reason added
#: without a sentence reaches a reader as a snake_case token.
SKIP_REASONS: frozenset[str] = frozenset({
    "notebook_daily_cap_reached",
    "proxy_gate",
})

#: Consecutive failures. Five is a cooldown, twenty is a stop.
TRIP_AFTER = 5
STOP_AFTER = 20

#: Seconds. Doubles per trip, because a breaker that returns to Half-Open too
#: quickly does not protect anything - it flaps, and each flap is another
#: billed call into a provider that is still broken.
COOLDOWN_BASE_S = 60.0
COOLDOWN_MAX_S = 3600.0


class Breaker:
    """Closed -> Open -> Half-Open, with a hand on the switch (A49)."""

    def __init__(self) -> None:
        self.failures = 0           # consecutive, resets on any success
        self.since_reset = 0        # what the STOP threshold counts
        self.total_failures = 0     # lifetime, for the counter on screen
        self.opened_at: float | None = None
        self.cooldown = COOLDOWN_BASE_S
        self.stopped = False
        self.trips = 0

    @property
    def state(self) -> str:
        if self.stopped:
            return "stopped"
        if self.opened_at is None:
            return "closed"
        return "open"

    def allows(self, now: float) -> bool:
        if self.stopped:
            return False
        if self.opened_at is None:
            return True
        if now - self.opened_at >= self.cooldown:
            # Half-open: exactly one call is allowed through. It is not reset
            # here - a success resets, a failure re-opens with a longer wait.
            return True
        return False

    def succeeded(self) -> None:
        self.failures = 0
        # And the run towards the permanent stop. STOP_AFTER means twenty
        # failures with NO success in between; counting them cumulatively let
        # twenty transient network failures spread over a week kill the
        # feature for good, with one log line.
        self.since_reset = 0
        self.opened_at = None
        self.cooldown = COOLDOWN_BASE_S

    def failed(self, now: float) -> None:
        self.failures += 1
        self.since_reset += 1
        self.total_failures += 1
        if self.since_reset >= STOP_AFTER:
            self.stopped = True
            logger.warning(
                "Notebook extraction stopped after %d failures.",
                self.since_reset)
            return
        if self.failures >= TRIP_AFTER or self.opened_at is not None:
            self.trips += 1
            self.opened_at = now
            self.cooldown = min(COOLDOWN_MAX_S, COOLDOWN_BASE_S * (2 ** min(self.trips, 6)))
            logger.info("Notebook extraction paused for %.0fs.", self.cooldown)

    def reset(self) -> None:
        """The hand on the switch. A breaker with no manual reset makes the
        user restart the whole application to retry after fixing whatever
        broke - which is the same as no breaker plus an insult.

        It lifts the REFUSAL and nothing else. `self.__init__()` also zeroed
        `total_failures`, and since the permanent stop counted that, one press
        of this button made the twenty-failure ceiling unreachable forever -
        a reset that quietly disabled the strongest guard in the class.
        """
        self.failures = 0
        self.since_reset = 0
        self.opened_at = None
        self.cooldown = COOLDOWN_BASE_S
        self.stopped = False
        self.trips = 0


class Worker:
    """Owns the queue, the breaker and the batch size."""

    def __init__(self) -> None:
        # Created in start(), NOT here. An asyncio.Queue binds to the loop it
        # is first used on, and this object is module-level: built at import
        # time it belongs to whichever loop happened to exist then, and every
        # later loop - a second run, a test client, a restart after a crash -
        # gets "bound to a different event loop" from a queue that looks
        # perfectly healthy.
        self.queue: asyncio.Queue[int] | None = None
        self.breaker = Breaker()
        self.dropped_offers = 0
        self.refused_by_breaker = 0
        self.died: str | None = None
        self.unhandled = 0
        self.last_error: str | None = None
        self.runs = 0
        self.task: asyncio.Task | None = None

    # ── the send path's only entry point ────────────────────────────────
    def offer(self, chat_id: int) -> None:
        """Note that a chat has moved. NEVER blocks, NEVER raises.

        Called from the completion path right after the turn is persisted. A
        background feature that can make a message fail to send is not a
        feature, so every failure mode here ends in a log line.
        """
        queue = self.queue
        if queue is None:
            # Offered before the worker started, or after the loop it belonged
            # to went away. Counted rather than raised: the send path must not
            # care whether the notebook is running.
            self.dropped_offers += 1
            return
        try:
            queue.put_nowait(chat_id)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
                queue.task_done()
                queue.put_nowait(chat_id)
            except (asyncio.QueueEmpty, asyncio.QueueFull):   # pragma: no cover
                pass
            self.dropped_offers += 1
            logger.info("Notebook queue full; oldest offer dropped.")
        except Exception:                                     # pragma: no cover
            self.dropped_offers += 1
            logger.warning("Notebook offer could not be queued.")

    # ── the loop ────────────────────────────────────────────────────────
    async def run(self) -> None:
        """Drain the queue forever. Cancellation is the normal exit."""
        assert self.queue is not None
        while True:
            chat_id = await self.queue.get()
            try:
                await self._handle(chat_id)
            except asyncio.CancelledError:
                # The vault locked, or the process is going down. Neither is a
                # failure of the model or of this code, and counting it as one
                # would walk the breaker to "stopped" every time the user
                # stepped away from their desk.
                raise
            except Exception as exc:
                import vault_state
                if isinstance(exc, vault_state.VaultLockedError):
                    # The vault closed under us. Not a failure of the model,
                    # of this code, or of anything the user should read about:
                    # background work deliberately does not feed the idle
                    # timer, so an ordinary lock/unlock cycle used to leave up
                    # to a queueful of "unhandled errors" on the status panel.
                    continue
                # A supervised worker that dies on one bad chat is not
                # supervised. The type name and the chat id - never the
                # message, which can carry the text that caused it. The
                # version without them logged the same sentence on every turn
                # for a chat that failed deterministically, with nothing to
                # tell which chat or what went wrong.
                self.unhandled += 1
                self.last_error = type(exc).__name__
                logger.warning(
                    "Notebook extraction raised (%s) for chat_id=%d; "
                    "loop continues.", type(exc).__name__, chat_id)
            finally:
                self.queue.task_done()

    async def _handle(self, chat_id: int) -> None:
        loop = asyncio.get_running_loop()

        # Before ANY database work. Planning decrypts every pending message,
        # and a stopped breaker that still did that would pay a
        # multi-hundred-kilobyte decrypt on every turn, forever, plus one
        # skipped row per turn with no ceiling. "Stopped" has to mean stop.
        # The reason is not lost: it is in-memory and on the status screen,
        # which is where a user looks when nothing is happening.
        if not self.breaker.allows(loop.time()):
            self.refused_by_breaker += 1
            return

        model_id = await anyio.to_thread.run_sync(notebook_extract.extract_model)
        if not model_id:
            # A46: no model, no extraction. Not an error and not worth a row
            # every turn - the panel already says suggestions are off.
            return

        language = await anyio.to_thread.run_sync(_language)
        plan = await anyio.to_thread.run_sync(
            _plan_work, chat_id, model_id, language,
            config.NOTEBOOK_EXTRACT_EVERY_TURNS, self.batch_size)
        if plan is None:
            return

        # Before the gate, before the claim, before the money. The key was
        # computed and then never consulted: `already_done` had no caller in
        # the repository at all, and the duplicate was only noticed after the
        # call had been sent and billed - at which point the answer was thrown
        # away and the range stayed unread.
        if await anyio.to_thread.run_sync(_already_done, plan["work_key"]):
            return

        try:
            from proxy_health import enforce_proxy_gate
            await enforce_proxy_gate()
        except Exception:
            await _record_skip(chat_id, "proxy_gate", plan)
            return

        try:
            await anyio.to_thread.run_sync(_claim_one)
        except notebook.NotebookError as exc:
            await _record_skip(chat_id, exc.code, plan)
            return

        import openrouter
        try:
            # Everything from here on is INSIDE the claim. A failure between
            # the claim and the request used to land in the loop's generic
            # handler: a sixtieth of the day's budget gone, no row in any
            # counter, and `calls+1` against zero cost.
            messages = [
                {"role": "system",
                 "content": notebook_extract.system_prompt(language)},
                {"role": "user",
                 "content": notebook_extract.build_user_message(
                     card=plan["card"], existing=plan["existing"],
                     recent=plan["recent"], new=plan["new"])},
            ]
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.breaker.failed(loop.time())
            await _record_failure(chat_id, plan, "prompt_" + type(exc).__name__)
            return

        try:
            reply = await openrouter.complete(
                messages,
                model_id,
                {"max_tokens": notebook_extract.MAX_TOKENS, "temperature": 0},
                dict(config.PROVIDER_POLICY),
                response_format=notebook_extract.RESPONSE_FORMAT,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # loop.time() NOW, not the `now` captured before the request.
            # A provider that hangs burns COMPLETION_TIMEOUT (120s) per call,
            # so `opened_at` would be stamped two minutes in the past against
            # a 60s cooldown: the breaker opens and is already half-open, and
            # every following turn gets another full billed call through a
            # breaker reporting "open".
            self.breaker.failed(loop.time())
            await _record_failure(chat_id, plan, type(exc).__name__)
            return

        usage = notebook_extract.usage_of(reply)
        try:
            proposals, _dropped = notebook_extract.parse_reply(
                reply, plan["chunk"], plan["existing"])
        except notebook_extract.ExtractionFailed as exc:
            # A reply that cannot be trusted is a FAILURE, and the range stays
            # unread. Recorded as done-with-nothing it would be skipped
            # forever, which is the shape this whole design is built against.
            self.breaker.failed(loop.time())
            await _record_failure(chat_id, plan, str(exc), usage=usage)
            return

        # AFTER the write, not before. Declared first, a vault that locked
        # between the reply and the write - the module's own documented
        # scenario - left a call that was billed, facts discarded, NO row of
        # any status, a healthy breaker and an incremented run counter.
        try:
            await anyio.to_thread.run_sync(_write, chat_id, plan, proposals,
                                           usage)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await _record_failure(chat_id, plan, "write_" + type(exc).__name__,
                                  usage=usage)
            return
        self.breaker.succeeded()
        self.runs += 1

    @property
    def batch_size(self) -> int:
        """How many messages ONE extraction may read. Halves on failure.

        This is the amount of WORK per call, and it was very nearly the
        opposite: halving the trigger THRESHOLD instead. That version fired
        ten times per twenty messages instead of once, and since the fixed
        part of the prompt - the card, the existing notes, the instructions,
        about ten kilobytes - is paid in full on every call regardless of how
        much transcript rides along, it multiplied the bill by ten at exactly
        the moment the provider was failing. A safety feature that is an
        amplifier.

        Halving what is READ is the real thing: fewer tokens, a smaller
        window, the same number of calls. The rest of the backlog is not
        lost - it stays unread and the next run picks it up.
        """
        size = config.NOTEBOOK_EXTRACT_EVERY_TURNS
        for _ in range(min(self.breaker.failures, 3)):
            size = max(2, size // 2)
        return size

    def status(self) -> dict:
        return {
            "state": self.breaker.state,
            "failures": self.breaker.failures,
            "total_failures": self.breaker.total_failures,
            "queued": self.queue.qsize() if self.queue is not None else 0,
            "dropped_offers": self.dropped_offers,
            "refused_by_breaker": self.refused_by_breaker,
            # 3: a dead loop used to report "closed" with a growing queue
            # forever. The screen and the truth diverging is the one failure
            # a status screen must not have.
            "alive": self.task is not None and not self.task.done(),
            "died": self.died,
            # Recoverable but INVISIBLE was the shape here: every one of these
            # left no row, no breaker failure and no counter, so a notebook
            # that threw on every single turn reported "Running. 0 runs".
            "unhandled": self.unhandled,
            "last_error": self.last_error,
            "runs": self.runs,
            "batch_size": self.batch_size,
        }


#: The one instance. Module-level so the completion path can reach it without
#: threading a reference through six call sites.
worker = Worker()


def start() -> asyncio.Task:
    """Start the loop and KEEP the reference.

    The event loop holds only a WEAK reference to a task. A worker started and
    forgotten is collected at an arbitrary moment, which in practice means it
    works in development and stops in production - and the failure looks
    exactly like the feature was never wired up at all.
    """
    if worker.task is not None and not worker.task.done():
        # Started twice, the first task is referenced only weakly by the loop
        # and is collected mid-extraction; worse, its `finally` calls
        # task_done() on the NEW queue for an item taken from the old one.
        return worker.task
    # The queue is built HERE so it binds to the loop that will drain it.
    worker.queue = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
    worker.task = asyncio.create_task(worker.run(), name="notebook-worker")
    worker.task.add_done_callback(_note_death)
    return worker.task


def _note_death(task: asyncio.Task) -> None:
    """A loop that ends is the one thing the status screen must not hide.

    Without this, a BaseException that is not CancelledError - a task group's
    ExceptionGroup, SystemExit - ended the loop permanently while `status()`
    went on reporting a healthy worker with a growing queue. The screen and
    the truth diverging is the failure a status screen exists to prevent.
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        worker.died = type(exc).__name__
        logger.warning("Notebook worker stopped: %s", worker.died)


async def quiesce() -> None:
    """Stand down for a vault lock: cancel what is in flight, drop the queue.

    Called from `lock_vault_now`, which is the single funnel both the button
    and the idle watchdog come through. The loop is restarted immediately, so
    this is a pause rather than a shutdown - what it must NOT leave behind is
    an in-flight request carrying decrypted text, or a backlog of offers that
    will each wake into a locked vault and be counted as an error.
    """
    task, worker.task = worker.task, None
    queue, worker.queue = worker.queue, None
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except BaseException:
            pass
    if queue is not None:
        while True:
            try:
                queue.get_nowait()
                queue.task_done()
            except asyncio.QueueEmpty:
                break
    # Back on its feet for the next unlock. A worker that stayed down after
    # the first auto-lock would look exactly like a feature that stopped
    # working for no reason.
    start()


async def stop() -> None:
    """Cancel the loop. Best effort by design: in the packaged exe this never
    runs at all - uvicorn is a daemon thread and the process dies with the
    window - so nothing may DEPEND on it having run."""
    task = worker.task
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except BaseException:
            pass
    # Cleared AFTER the await, not before. Nulled first, the CancelledError
    # unwound into run()'s `finally: self.queue.task_done()` and raised
    # AttributeError from inside the finally - which REPLACES the
    # CancelledError, so the task ended as failed rather than cancelled and
    # the swallow below hid that too.
    worker.task = None
    worker.queue = None


# ── database-side helpers, all off the event loop ──────────────────────────

def _language() -> str:
    from database import get_setting
    return get_setting(config.SETTING_NOTEBOOK_PROMPT_LANG) or "en"


def _already_done(work_key: str) -> bool:
    from database import get_db
    with get_db() as con:
        return notebook.already_done(con, work_key)


def _claim_one() -> None:
    from database import get_db
    with get_db() as con:
        notebook.claim_call(con, config.NOTEBOOK_DAILY_CALL_CAP)


def _plan_work(chat_id: int, model_id: str, language: str,
               threshold: int, limit: int) -> dict | None:
    """Decide whether there is enough new material, and gather it.

    A25: the delta is what gets extracted, plus a two-message disambiguation
    window that is shown as CONTEXT and is explicitly not extracted from.
    Graphiti's default is the same two, with the same rule.

    `threshold` is how much new material is worth a call; `limit` is how much
    one call may read. They are different numbers and conflating them was a
    ten-fold spending bug - see Worker.batch_size.
    """
    from database import get_db

    with get_db() as con:
        last = con.execute(
            "SELECT COALESCE(MAX(to_message_id), 0) FROM notebook_extractions "
            "WHERE chat_id = ? AND status = 'done'", (chat_id,)).fetchone()[0]
        # COUNT before SELECT. Nineteen offers in twenty return here, and the
        # version that fetched the rows first decrypted every pending message
        # body to do it - a hundred and ninety message bodies per cycle, to
        # answer a question a counter answers.
        pending = con.execute(
            "SELECT COUNT(*) FROM messages "
            "WHERE chat_id = ? AND active = 1 AND id > ?",
            (chat_id, last)).fetchone()[0]
        if pending < threshold:
            return None

        # LIMIT, and the range is what was actually READ. Without it a long
        # backlog was selected whole, `to_id` named the last of five hundred
        # messages, the prompt builder kept the newest twelve thousand
        # characters of it, and the transaction marked the entire range done -
        # so the other four hundred and eighty were silently never extracted.
        # The one silent-loss shape this whole module exists to prevent,
        # rebuilt at the other end of the same function.
        rows = con.execute(
            "SELECT id, role, content FROM messages "
            "WHERE chat_id = ? AND active = 1 AND id > ? ORDER BY id LIMIT ?",
            (chat_id, last, max(1, limit))).fetchall()

        window = con.execute(
            "SELECT role, content FROM messages "
            "WHERE chat_id = ? AND active = 1 AND id <= ? "
            "ORDER BY id DESC LIMIT 2", (chat_id, last)).fetchall()
        card_row = con.execute(
            "SELECT c.description FROM chats ch "
            "JOIN characters c ON c.id = ch.character_id WHERE ch.id = ?",
            (chat_id,)).fetchone()
        entries = notebook.list_entries(chat_id, include_retired=False)

    new = [f"{r['role']}: {r['content']}" for r in rows]
    recent = [f"{r['role']}: {r['content']}" for r in reversed(window)]
    live = [e for e in entries if e["status"] == notebook.STATUS_ACCEPTED]
    return {
        "from_id": rows[0]["id"],
        "to_id": rows[-1]["id"],
        "new": new,
        "recent": recent,
        "chunk": "\n".join(new),
        "card": (card_row["description"] if card_row else "") or "",
        "existing": [e["text"] for e in live],
        "existing_ids": [e["id"] for e in live],
        "work_key": notebook_extract.work_key(
            chat_id, rows[0]["id"], rows[-1]["id"], model_id, language),
    }


def _write(chat_id: int, plan: dict, proposals: list[dict],
           usage: dict) -> dict:
    from database import get_db
    with get_db() as con:
        con.execute("BEGIN IMMEDIATE")
        return notebook.commit_extraction(
            con, work_key=plan["work_key"], chat_id=chat_id,
            from_id=plan["from_id"], to_id=plan["to_id"],
            proposals=proposals, existing_ids=plan["existing_ids"],
            usage=usage, status="done")


async def _record_skip(chat_id: int, reason: str,
                       plan: dict | None = None) -> None:
    """A47: a skipped extraction is not silent.

    Without a row, "the notebook has not proposed anything for a week" and
    "the notebook has refused sixty times for a reason nobody can see" are the
    same screen.
    """
    if plan is None:
        return
    # Declared or it does not ship. These reach a reader as sentences, so an
    # undeclared one is a snake_case token in prose - the same failure the
    # error catalogue exists to prevent, in a vocabulary the catalogue does
    # not cover.
    assert reason in SKIP_REASONS, reason
    await anyio.to_thread.run_sync(
        functools.partial(_record, chat_id, plan, "skipped",
                          skip_reason=reason))


async def _record_failure(chat_id: int, plan: dict, error_type: str,
                          usage: dict | None = None) -> None:
    await anyio.to_thread.run_sync(
        functools.partial(_record, chat_id, plan, "failed",
                          error_type=error_type, usage=usage))


def _record(chat_id: int, plan: dict, status: str, *,
            skip_reason: str | None = None, error_type: str | None = None,
            usage: dict | None = None) -> None:
    from database import get_db
    try:
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.commit_extraction(
                con, work_key=plan["work_key"], chat_id=chat_id,
                from_id=plan["from_id"], to_id=plan["to_id"],
                usage=usage, status=status, skip_reason=skip_reason,
                error_type=error_type)
    except Exception:
        # The vault may have locked between the attempt and the bookkeeping.
        # Losing the row is regrettable; losing the loop is not acceptable.
        logger.info("Notebook outcome could not be recorded.")
