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
import secrets

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
#: so it is checked against the panel's map, exactly like the error catalogue
#: is checked against errorMessages.ts. A reason added without a sentence
#: reaches a reader as a snake_case token.
#:
#: Declared in notebook_store.py, not here, and reused under the same name
#: rather than copied. `plan_invalidated` is written from THAT module -
#: `commit_extraction`'s `require_trace` branch, not this worker - and a
#: second frozenset here would leave that writer checking itself against a
#: vocabulary that never heard of its own reason, which is exactly how it
#: leaked before this alias replaced the copy.
SKIP_REASONS: frozenset[str] = notebook.SKIP_REASONS

#: Consecutive failures. Five is a cooldown, twenty is a stop.
TRIP_AFTER = 5
STOP_AFTER = 20

#: Seconds. Doubles per trip, because a breaker that returns to Half-Open too
#: quickly does not protect anything - it flaps, and each flap is another
#: billed call into a provider that is still broken.
COOLDOWN_BASE_S = 60.0
COOLDOWN_MAX_S = 3600.0

#: How long `quiesce()`/`stop()` will wait for an ALREADY-PAID extraction to
#: finish settling before locking anyway. What it is waiting for is one
#: SQLite transaction (see Worker._settle) - this is generous headroom for a
#: slow disk, not a promise that every write completes. See Defect 1: a bare
#: `asyncio.Task.cancel()` unwinds the awaiting coroutine at once even through
#: `anyio.to_thread.run_sync(abandon_on_cancel=False)` - measured, it does NOT
#: protect the await from an external cancel - so the only reliable way to
#: let a paid write finish is to run it as its OWN task, shielded, and have
#: the canceller wait for THAT task specifically.
SETTLE_GRACE_S = 5.0


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
        # Two different events, counted apart. Both used to land in
        # `dropped_offers`, and only ONE of them means what the status panel
        # says it means: a queue that overflowed is a worker falling behind,
        # while an offer arriving with no queue at all is the ordinary state
        # before startup and during every vault lock. Auto-lock fires all day,
        # `quiesce()` nulls the queue each time, and every turn sent while
        # locked added to the number labelled "turns went unqueued while it was
        # behind" - so the one counter that was supposed to reveal a backlog
        # was dominated by the most routine event in the application.
        self.queue_overflows = 0
        self.offers_while_down = 0
        self.refused_by_breaker = 0
        self.died: str | None = None
        self.unhandled = 0
        self.last_error: str | None = None
        self.runs = 0
        self.task: asyncio.Task | None = None
        # The tail of ONE extraction - parse a paid reply, write it or record
        # its failure - running as its OWN task, shielded from the loop
        # task's cancellation. Set the instant that task is created, cleared
        # by the task itself when it finishes; `quiesce()`/`stop()` read this
        # to wait for a paid call to actually settle before the vault key is
        # cleared out from under it. See _handle, _settle, Defect 1.
        self.settling: asyncio.Task | None = None

    @property
    def dropped_offers(self) -> int:
        """Every offer that never reached the queue, whatever the reason.

        Derived rather than stored, because the two causes above are now
        counted separately and this name is a WIRE field: `status()` publishes
        it, the client schema requires it, and the panel renders it. A counter
        that is split in the backend and silently dropped from the response is
        a status screen that goes blank, which is the one thing a status screen
        may not do. So the total stays, exact, and the two causes travel beside
        it for whoever wants to tell them apart.
        """
        return self.queue_overflows + self.offers_while_down

    @dropped_offers.setter
    def dropped_offers(self, value: int) -> None:
        # Only a reset is meaningful on a derived counter - the test suite
        # zeroes this between tests, since the worker is process-wide state.
        # Splitting an arbitrary total back into two causes would be a guess,
        # and a guess written into a counter is worse than no counter.
        if value:
            raise ValueError(
                "dropped_offers is derived; set queue_overflows or "
                "offers_while_down")
        self.queue_overflows = 0
        self.offers_while_down = 0

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
            #
            # Its OWN counter, because this is not a backlog. The vault locks
            # on an idle timer many times a day and `quiesce()` nulls the queue
            # every time, so counting these as dropped-while-behind buried the
            # rare, real overflow under the most ordinary event there is.
            self.offers_while_down += 1
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
            # This one really is "behind": the worker is up, the queue is at
            # its bound, and a turn was lost because the drain could not keep
            # pace. It is the number the panel's sentence describes.
            self.queue_overflows += 1
            logger.info("Notebook queue full; oldest offer dropped.")
        except Exception:                                     # pragma: no cover
            # A live queue that refused an offer for some other reason is
            # still a turn lost while the worker was running, so it belongs
            # with the overflows rather than with the locks.
            self.queue_overflows += 1
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
        # The identity of THIS attempt, not of the range - work_key names the
        # range and is deterministic, so a re-plan of the identical range
        # reuses the same key. Carried through every write this attempt makes
        # (_record_running, _record_failure, _write) so a stale settle from an
        # abandoned attempt can be told apart from the retry that reclaimed
        # the row. See commit_extraction's ownership check, Defect 2.
        plan["attempt_token"] = secrets.token_hex(16)

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

        # The claim commits in its OWN transaction, BEFORE the request, so from
        # this line on the day's budget is spent whatever happens next. That
        # left one hole nothing covered: a cancellation in flight - the vault
        # locking while the provider is generating, which this module expects
        # rather than defends against - unwinds through `raise` and recorded
        # NOTHING. `calls` was +1, the cost was never attributed to it, and
        # there was no extraction row of ANY status, so the identical work key
        # was re-planned and re-billed at the next threshold.
        #
        # The schema has carried `status = 'running'` and `started_at` for
        # exactly this since the table was written - "so a crash leaves a
        # trace" - and nothing in the repository ever wrote them. This is that
        # row. It is not a lock and it does not refuse anything: it is the
        # evidence that a paid call left, and the success and failure paths
        # below settle it into `done` or `failed` through the same
        # `commit_extraction`, which updates a prior non-`done` row in place.
        if not await _record_running(chat_id, plan):
            # No trace could be written, so a call made now would be exactly
            # the unaccountable spend the row exists to prevent - and since the
            # bookkeeping is what just failed, the OUTCOME could not be
            # recorded either. The claim is already gone; one wasted claim is
            # much cheaper than an invisible billed call, so this stops here
            # and says so on the status screen rather than in a log line.
            self.unhandled += 1
            self.last_error = "running_row_unwritable"
            logger.warning(
                "Notebook extraction abandoned before the call for "
                "chat_id=%d: its trace could not be written.", chat_id)
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

        # Defect 1. From here the call has been sent, generated and BILLED -
        # `reply` is the proof. Everything left to do (parse it, write it or
        # record its failure) must not be allowed to vanish if the vault
        # locks in the next few lines, and a bare `asyncio.Task.cancel()`
        # reaches here even through `anyio.to_thread.run_sync
        # (abandon_on_cancel=False)`: measured, that flag does NOT protect
        # the awaiting coroutine from an external cancel - the coroutine
        # unwinds at once and the worker thread keeps running detached,
        # racing `vault_state.clear_key()` for a connection to open.
        #
        # So the rest of this turn runs as its OWN task, SHIELDED from our
        # own cancellation. `quiesce()`/`stop()` do not just cancel this loop
        # and walk away - they wait for `self.settling` specifically, so a
        # commit that has already been paid for is allowed to finish (or to
        # record its own failure, with the cost attached) before the caller's
        # next line clears the key out from under it.
        settle_task = asyncio.create_task(
            self._settle(chat_id, plan, reply), name="notebook-settle")
        self.settling = settle_task

        def _clear_settling(t: asyncio.Task, w: "Worker" = self) -> None:
            if w.settling is t:
                w.settling = None
        settle_task.add_done_callback(_clear_settling)

        await asyncio.shield(settle_task)

    async def _settle(self, chat_id: int, plan: dict, reply: dict) -> None:
        """Parse a PAID reply and land it. Runs as its own task - see the
        shield in `_handle` above, which is the whole reason this is a
        separate method rather than the tail of that one: everything in here
        must keep running to completion even if `_handle`'s own await gets
        cancelled out from under it.
        """
        loop = asyncio.get_running_loop()
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

        try:
            await anyio.to_thread.run_sync(_write, chat_id, plan, proposals,
                                           usage)
        except Exception as exc:
            # COUNTED, not merely recorded. A row was written and nothing else
            # was: the breaker stayed closed, `unhandled` stayed at zero and
            # `runs` never moved, so a chat that fails to commit every time -
            # a vault that locked between the reply and the commit, a chat
            # deleted mid-call so the foreign key fires, a disk error - burned
            # one billed call per threshold, forever, while the panel said
            # "Running. 0 runs." Only the daily cap ever stopped it, and a cap
            # is a ceiling on the damage, not a report of it.
            #
            # A locked vault is deliberately NOT a failure elsewhere in this
            # file (see `run`), and this is the considered exception rather
            # than an oversight: that rule exists so cancellations which cost
            # NOTHING cannot walk the breaker towards "stopped" every time
            # somebody steps away. By this line the call has been sent,
            # generated and paid for, and the work is lost. Five of those in a
            # row is precisely what the breaker is for.
            self.breaker.failed(loop.time())
            self.unhandled += 1
            error_type = "write_" + type(exc).__name__
            if not await _record_failure(chat_id, plan, error_type,
                                         usage=usage):
                # `_record` swallows its own failure to keep the loop alive,
                # which is right, and it did so SILENTLY, which is not: when
                # the vault is still locked the row is lost as well as the
                # write, and then the database also says nothing happened. The
                # in-memory counter is the last witness, so it names it.
                error_type += "_unrecorded"
            self.last_error = error_type
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
            # The total, kept because the client requires this key. The two
            # keys after it are the same number taken apart: an overflow is
            # the worker falling behind, an offer while it was down is an
            # ordinary vault lock, and reporting only the sum let the second
            # answer for the first.
            "dropped_offers": self.dropped_offers,
            "queue_overflows": self.queue_overflows,
            "offers_while_down": self.offers_while_down,
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


async def _await_settle() -> None:
    """Wait for an already-PAID extraction to actually finish, bounded.

    Shared by `quiesce()` and `stop()`. `asyncio.shield` here for the same
    reason `_handle` shields the task in the first place: if the CALLER of
    this function is itself cancelled (a lock request torn down mid-flight,
    a shutdown that does not wait), that must not re-open the exact race this
    exists to close by cancelling the shielded settle task out from under us
    a second time.
    """
    settle = worker.settling
    if settle is None or settle.done():
        return
    try:
        await asyncio.wait_for(asyncio.shield(settle), timeout=SETTLE_GRACE_S)
    except asyncio.TimeoutError:
        logger.warning(
            "Notebook: a paid extraction did not settle within %.0fs; "
            "proceeding without it.", SETTLE_GRACE_S)
    except BaseException:
        logger.warning("Notebook: the in-flight extraction did not settle "
                       "cleanly.")


async def quiesce() -> None:
    """Stand down for a vault lock: cancel what is in flight, drop the queue.

    Called from `lock_vault_now`, which is the single funnel both the button
    and the idle watchdog come through, IMMEDIATELY before the vault key is
    cleared. The loop is restarted before returning, so this is a pause
    rather than a shutdown - what it must NOT leave behind is an in-flight
    request carrying decrypted text, a backlog of offers that will each wake
    into a locked vault and be counted as an error, or (Defect 1) a reply
    that was already paid for and received, discarded mid-write because the
    key was cleared out from under the thread writing it.
    """
    task, worker.task = worker.task, None
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except BaseException:
            pass

    # Defect 1. The loop task above is cancelled and gone, but `_handle` may
    # have handed the tail of a PAID call to `self.settling` as its own
    # shielded task before it went - see `_handle`. That task is still
    # running, or about to be, entirely independent of the cancellation just
    # awaited. Waiting for it HERE, before the queue is torn down and before
    # this function returns to `lock_vault_now` (which clears the key on its
    # very next line), is what lets that write actually land instead of
    # racing a cleared key in a detached thread.
    await _await_settle()

    # The queue is nulled and drained only AFTER the cancellation above has
    # fully unwound - not before. Nulling it first left `run()`'s own
    # `finally: self.queue.task_done()` reading `self.queue` as None while
    # the CancelledError it was supposed to let through was still in flight,
    # replacing that CancelledError with an AttributeError and reporting the
    # loop as died rather than cleanly cancelled. See `stop()`'s docstring,
    # which already carries this fix; this function had not caught up to it.
    queue, worker.queue = worker.queue, None
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
    # Defect 1, same fix as `quiesce()`: give an already-paid call the chance
    # to actually land before this function returns. Best-effort like the
    # rest of this path - the packaged exe never reaches here at all - but a
    # graceful dev-mode shutdown should not needlessly throw away a call that
    # was already billed and had already come back.
    await _await_settle()
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
        # Before anything is planned, close out the trace of a call that was
        # made and never settled - the app killed with the window, or the
        # vault locked mid-request. See notebook.settle_orphaned_running: the
        # money for that range is already spent, and a cursor that will not
        # move past it re-sends and re-bills the identical range on every
        # later cycle.
        abandoned = notebook.settle_orphaned_running(con, chat_id)
        last = con.execute(
            "SELECT COALESCE(MAX(to_message_id), 0) FROM notebook_extractions "
            "WHERE chat_id = ? AND (status = 'done' "
            "     OR (status = 'failed' AND error_type = ?))",
            (chat_id, notebook.ABANDONED_IN_FLIGHT)).fetchone()[0]
        # Whether this chat has EVER been read, which `last == 0` alone does
        # not answer. `forget_proposals_from_messages` deletes extraction rows
        # above an edited message ON PURPOSE, to roll the cursor back - and
        # that can empty the table for a chat that has in fact been read many
        # times over. Without this flag, Defect 3: a 61-message chat with
        # three completed ranges, an edit to message 5, and `last` collapses
        # to 0 exactly like a chat that has never been looked at - so the
        # branch below reads it as the upgrading-user case and jumps to the
        # PRESENT, abandoning messages 1..41 (the edited one included) for
        # good. The flag survives the delete that emptied `last`.
        ever_extracted = con.execute(
            "SELECT notebook_extracted_ever FROM chats WHERE id = ?",
            (chat_id,)).fetchone()
        ever_extracted = bool(ever_extracted[0]) if ever_extracted else False
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
        if last == 0 and pending > limit * 2 and not ever_extracted:
            # NOTHING has EVER been read in this chat (not merely "since the
            # cursor last moved") and there is a backlog. That is the
            # upgrading user: an existing conversation meeting this feature
            # for the first time.
            #
            # Reading from the OLDEST end would have the notebook describing
            # the opening of a story that has since moved four hundred
            # messages on, and injecting those notes into the live prompt for
            # the twenty-odd turns it takes to catch up - each one a paid
            # call. A notebook that lags a session behind is worse than an
            # empty one, because the model trusts it.
            #
            # So the first read of an existing chat starts at the PRESENT.
            # The older history is not extracted at all, and that is the
            # honest trade: it was never read, it is not pretended to be.
            rows = con.execute(
                "SELECT id, role, content FROM messages "
                "WHERE chat_id = ? AND active = 1 "
                "ORDER BY id DESC LIMIT ?",
                (chat_id, max(1, limit))).fetchall()
            rows = list(reversed(rows))
        else:
            rows = con.execute(
                "SELECT id, role, content FROM messages "
                "WHERE chat_id = ? AND active = 1 AND id > ? ORDER BY id "
                "LIMIT ?",
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
    # Bounded HERE, where the ids and the text are still side by side, so the
    # numbered list the model sees and the list its answers are resolved
    # against are the same list. Budgeting one and not the other shifted every
    # index and retired the wrong note.
    budget = 0
    trimmed: list[dict] = []
    for entry in reversed(live):          # newest first, keep what fits
        cost = len(entry["text"]) + 8     # the "12. " prefix and a newline
        if budget + cost > notebook_extract.EXISTING_MAX_CHARS:
            break
        trimmed.append(entry)
        budget += cost
    live = list(reversed(trimmed))
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
            # This path ALWAYS wrote a `running` row first. If it is gone,
            # an edit or a delete rolled the cursor back under us on purpose,
            # and these notes describe wording the user has since taken back.
            usage=usage, status="done", require_trace=True,
            # Whose attempt this is. If the row has since been reclaimed by a
            # NEWER attempt at the identical range (same work_key), this call
            # is stale and must not overwrite it - see commit_extraction's
            # ownership check, Defect 2.
            attempt_token=plan.get("attempt_token"))


async def _record_running(chat_id: int, plan: dict) -> bool:
    """The trace a paid call leaves before it is made. True if it landed.

    Written between the claim and the request, so that a cancellation in
    flight - or the process dying with the window, which this module accepts
    as unavoidable - leaves something behind instead of a billed call with no
    row of any status. `commit_extraction` settles this same row into `done`
    or `failed` afterwards; it is a row updated in place, never a second one.
    """
    return await anyio.to_thread.run_sync(
        functools.partial(_record, chat_id, plan, "running"))


async def _record_skip(chat_id: int, reason: str,
                       plan: dict | None = None) -> bool:
    """A47: a skipped extraction is not silent.

    Without a row, "the notebook has not proposed anything for a week" and
    "the notebook has refused sixty times for a reason nobody can see" are the
    same screen.
    """
    if plan is None:
        return False
    # Declared or it does not ship. These reach a reader as sentences, so an
    # undeclared one is a snake_case token in prose - the same failure the
    # error catalogue exists to prevent, in a vocabulary the catalogue does
    # not cover.
    assert reason in SKIP_REASONS, reason
    return await anyio.to_thread.run_sync(
        functools.partial(_record, chat_id, plan, "skipped",
                          skip_reason=reason))


async def _record_failure(chat_id: int, plan: dict, error_type: str,
                          usage: dict | None = None) -> bool:
    return await anyio.to_thread.run_sync(
        functools.partial(_record, chat_id, plan, "failed",
                          error_type=error_type, usage=usage))


def _record(chat_id: int, plan: dict, status: str, *,
            skip_reason: str | None = None, error_type: str | None = None,
            usage: dict | None = None) -> bool:
    """Write one outcome row. Returns whether it was actually written.

    The return value is the point. This function must swallow its own failure
    or a locked vault takes the loop down with it, and swallowing it silently
    meant the caller believed a row existed when none did - so the failure
    that lost the work ALSO lost the only record of the work, and every screen
    agreed that nothing had happened.
    """
    from database import get_db
    try:
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.commit_extraction(
                con, work_key=plan["work_key"], chat_id=chat_id,
                from_id=plan["from_id"], to_id=plan["to_id"],
                usage=usage, status=status, skip_reason=skip_reason,
                error_type=error_type,
                attempt_token=plan.get("attempt_token"))
            if status == "running":
                # In the same transaction as the row it stamps.
                # `commit_extraction` does not set `started_at`, and it is the
                # only field that says WHEN the call left - which is what
                # separates a row abandoned by a crash hours ago from one that
                # is genuinely still in flight. `created_at` cannot answer
                # that, because the settled statuses share it.
                con.execute(
                    "UPDATE notebook_extractions SET started_at = "
                    "datetime('now') WHERE work_key = ?", (plan["work_key"],))
        return True
    except Exception:
        # The vault may have locked between the attempt and the bookkeeping.
        # Losing the row is regrettable; losing the loop is not acceptable.
        logger.info("Notebook outcome could not be recorded.")
        return False
