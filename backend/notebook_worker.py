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
from functools import partial
import functools
import logging
import secrets

import anyio.to_thread

import config
import notebook_extract
import voice_tags
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
        # The last time anything asked this breaker a question. `state` is a
        # property with no clock of its own, and "the cooldown has elapsed"
        # is a question about now - so the answer comes from the same clock
        # `allows()` uses rather than from a second one invented here.
        self._now: float | None = None

    @property
    def state(self) -> str:
        """closed | open | half_open | stopped.

        `half_open` was missing and the omission reached the screen. Once the
        cooldown has elapsed `allows()` lets exactly one call through, and it
        deliberately does NOT clear `opened_at` - a success resets, a failure
        re-opens with a longer wait, and that rule is load-bearing. But the
        state read from the same field, so the panel went on saying "Paused
        after repeated failures. It will try again by itself." while a real,
        billed request was going out.

        Reported, not changed: `allows()` is untouched.

        NO ARGUMENT, so it answers from `self._now` - the clock of whoever
        last called `allows()`. That is stale exactly when it matters: a
        tripped breaker on an idle machine gets no queue items (the vault
        locks, `offer()` returns early), so nothing calls `allows()`, so
        `_now` never advances and this reports "open" long after the
        cooldown has passed. Prefer `state_at(now)` from anywhere that has
        a clock; this remains for the callers that do not.
        """
        return self.state_at(self._now)

    def state_at(self, now: float | None) -> str:
        """The same question, asked at a stated time.

        The clock is the caller's, like `allows(now)` and `failed(now)`. The
        class holds no clock of its own on purpose: every decision it makes
        is comparable to every other only if they share one, and the loop's
        `loop.time()` is the one the failures were stamped with.

        `None` means "no clock available", and then the honest answer is the
        conservative one - `open` - because a breaker that guesses `closed`
        would advertise a call it might refuse.
        """
        if self.stopped:
            return "stopped"
        if self.opened_at is None:
            return "closed"
        if now is not None and now - self.opened_at >= self.cooldown:
            return "half_open"
        return "open"

    def allows(self, now: float) -> bool:
        self._now = now
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
        #: Turns the claim gate refused because the other entry point was
        #: already inside that range. Counted rather than logged: this file
        #: is on log_leak_scan's must-stay-clean list, and the number is what
        #: says whether the two paths are colliding often enough to matter.
        self.refused_in_flight = 0
        self.died: str | None = None
        self.unhandled = 0
        self.last_error: str | None = None
        #: Preambles whose turn was cancelled before it could send anything.
        #: Counted because the undo is best-effort and a reader deserves to
        #: know it happened rather than to find a call missing from the day.
        self.abandoned_preambles = 0
        self.runs = 0
        # A call that was paid for, parsed, and landed NOTHING.
        #
        # `runs` and `breaker.succeeded()` used to fire whenever `_write`
        # simply failed to raise - and `commit_extraction` reports several
        # failures by RETURNING them: a duplicate work key, a settle whose
        # attempt has been reclaimed, a range whose messages are gone. Each
        # of those was counted as a success, and because `succeeded()` also
        # clears `since_reset`, each one ERASED the real failures that came
        # before it. A worker failing every single turn could show a healthy
        # breaker and a rising run count forever.
        self.settled_empty = 0
        # One sweep at a time. A reader pressing the button three times has
        # asked for one thing, and three concurrent claims against the daily
        # cap is not what they asked for.
        self._sweeping = False
        #: Work keys this process has CLAIMED and not yet settled.
        #
        # Two coroutines can be inside `_handle` at once - the loop's and the
        # sweep button's - and neither could see the other. `already_done`
        # answers only for `status='done'`, so the same range was planned
        # twice, claimed twice and billed twice for one answer; and
        # `_plan_work`'s opening `settle_orphaned_running` marked the other
        # one's live, paid row `failed` on the way past.
        #
        # In-process and deliberately so. There is one backend process, and a
        # database flag would need its own crash recovery - the exact
        # `running`-row problem this set exists to stop being confused with.
        self._active: set[str] = set()
        #: The chats those keys belong to, for the button.
        #
        # `sweep()` is asked about a CHAT and cannot know the work key until
        # after it has planned - by which point the claim gate below has
        # already refused it, silently, with nobody to tell. This is the same
        # fact indexed the way the route needs to ask it.
        self._active_chats: set[int] = set()
        # What nobody has read, counted once at unlock. Never a trigger: see
        # notebook.unread_backlog for why this is an offer on a screen and
        # not a background job.
        self.backlog: dict[str, int] = {"chats": 0, "messages": 0}
        # EVERY in-flight task that touches the database, not just the last
        # one. `self.settling` is a single pointer and stays one - the tests
        # and `_clear_settling`'s `is t` guard both depend on that - but a
        # single pointer cannot answer "is anything still running": a second
        # extraction overwrites it while the first is still writing, and
        # `quiesce()` then waits for the wrong task and clears the vault key
        # out from under the right one. A set answers it and cannot be
        # overwritten.
        self._inflight: set[asyncio.Task] = set()
        # What `parse_reply` refused, by reason. It came back from every
        # single call and was assigned to `_dropped` and never read - so a
        # model whose every suggestion failed the grounding check produced
        # "3 runs, 0 notes" and no way at all to find out why.
        self.dropped: dict[str, int] = {}
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

    async def _prepare(self, chat_id: int, *, min_new: int | None = None,
                       after_id: int | None = None):
        """Everything up to and including the running row, or None.

        Split out of `_handle` so it can be waited for. Returns the three
        things the call itself needs - the plan, the model and the language -
        or None when this turn is not going to make a call at all.
        """
        loop = asyncio.get_running_loop()

        # Before ANY database work. Planning decrypts every pending message,
        # and a stopped breaker that still did that would pay a
        # multi-hundred-kilobyte decrypt on every turn, forever, plus one
        # skipped row per turn with no ceiling. "Stopped" has to mean stop.
        # The reason is not lost: it is in-memory and on the status screen,
        # which is where a user looks when nothing is happening.
        if not self.breaker.allows(loop.time()):
            self.refused_by_breaker += 1
            return None

        model_id = await anyio.to_thread.run_sync(notebook_extract.extract_model)
        if not model_id:
            # A46: no model, no extraction. Not an error and not worth a row
            # every turn - the panel already says suggestions are off.
            return None

        language = await anyio.to_thread.run_sync(_language)
        plan = await anyio.to_thread.run_sync(
            partial(_plan_work, chat_id, model_id, language,
                    config.NOTEBOOK_EXTRACT_EVERY_TURNS, self.batch_size,
                    min_new=min_new, after_id=after_id,
                    # What the OTHER entry point is holding. `_plan_work`
                    # opens by closing out `running` rows as orphans, and
                    # without this the sweep button's first act was to fail
                    # the loop's live call and move the cursor past it.
                    keep=frozenset(self._active)))
        if plan is None:
            return None
        # The identity of THIS attempt, not of the range - work_key names the
        # range and is deterministic, so a re-plan of the identical range
        # reuses the same key. Carried through every write this attempt makes
        # (_record_running, _record_failure, _write) so a stale settle from an
        # abandoned attempt can be told apart from the retry that reclaimed
        # the row. See commit_extraction's ownership check, Defect 2.
        plan["attempt_token"] = secrets.token_hex(16)
        # ON THE PLAN, because the plan is the only thing the done-callback
        # that undoes an abandoned preamble is given. It had no way to say
        # which chat it was giving back.
        plan["chat_id"] = chat_id

        # Before the gate, before the claim, before the money. The key was
        # computed and then never consulted: `already_done` had no caller in
        # the repository at all, and the duplicate was only noticed after the
        # call had been sent and billed - at which point the answer was thrown
        # away and the range stayed unread.
        if await anyio.to_thread.run_sync(_already_done, plan["work_key"]):
            return None

        # AND the same question about right now, which `already_done` cannot
        # answer. It reads `status='done'`; a range the other entry point
        # claimed thirty seconds ago and is still generating against has a
        # `running` row, passes that gate, and gets claimed and billed a
        # second time for one answer.
        #
        # Checked and taken in the same breath - no `await` between them -
        # so the two coroutines cannot both pass.
        work_key = plan["work_key"]
        if work_key in self._active:
            # SILENT, and the silence is correct here.
            #
            # This tried to write a `skipped` row and could not: the row for
            # this work key already exists as `running`, written by whoever
            # got here first, and `commit_extraction` is keyed on that. A
            # `skipped` row would have overwritten the live trace of a call
            # that is still out - so the reason was declared, given a
            # sentence, and dropped on the floor every time.
            #
            # The person who needs to hear it is the one pressing Sweep, and
            # `sweep()` refuses them by name before it gets this far. On the
            # loop's side nobody is waiting for an answer, so this is a net
            # under a second claim and nothing else.
            self.refused_in_flight += 1
            return None
        self._active.add(work_key)
        self._active_chats.add(chat_id)

        # GIVEN BACK IN ONE PLACE, and the first version of this gave it
        # back in four.
        #
        # Four hand-written `discard`s cover the exits somebody thought of.
        # `_claim_one` below raises `VaultLockedError` and
        # `sqlite3.OperationalError`, and neither is a `NotebookError`, so
        # the single likeliest failure on this path escaped all four. The key
        # then stayed in the set for the life of the process - nothing clears
        # it - and a key in the set is a key in `keep`, so the `running` row
        # could never be closed out, the cursor never advanced, the identical
        # range was re-planned and refused every turn, and the panel went on
        # reporting a healthy worker. A permanent silent stall for that chat,
        # which is worse than the double bill this gate exists to prevent.
        #
        # `handed_over` rather than a bare `finally`: on the ONE path that
        # succeeds the key belongs to `_handle` from here, and `_handle`'s
        # own `finally` is what gives it back.
        handed_over = False
        try:
            try:
                from proxy_health import enforce_proxy_gate
                await enforce_proxy_gate()
            except Exception:
                await _record_skip(chat_id, "proxy_gate", plan)
                return None

            # BEFORE the claim, because the claim is the moment the day's budget
            # is spent and this is the question of whether anything can be sent
            # at all. `openrouter.complete`'s first statement reads this same
            # secret and raises - so with no key the old order burned a slot per
            # turn on a request that never left the machine, twenty of them, and
            # then stopped the worker with an unnamed failure.
            #
            # Not folded into the provider: a pre-flight inside `complete` would
            # still be reached AFTER the claim, and the claim is the thing that
            # needs to not happen.
            if not await anyio.to_thread.run_sync(_have_api_key):
                await _record_skip(chat_id, "api_key_not_set", plan)
                return None

            try:
                # The day the claim is STAMPED WITH, carried on the plan.
                #
                # The refund used to re-derive it hours later, so a claim made at
                # 23:59 and abandoned at 00:00 decremented a row it had never
                # touched: yesterday's phantom claim stayed, today's counter went
                # down for a call today never made, and the ceiling passed one
                # extra billed call.
                plan["claim_day"] = await anyio.to_thread.run_sync(_claim_one)
            except notebook.NotebookError as exc:
                await _record_skip(chat_id, exc.code, plan)
                return None

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
                # recorded either. One wasted call is much cheaper than an
                # invisible billed one, so this stops here and says so on the
                # status screen rather than in a log line.
                #
                # The claim goes BACK. Nothing has been written to the socket at
                # this line - `openrouter.complete` is a hundred lines below - so
                # "the day's budget is spent whatever happens next" is not true
                # yet, and charging the day for a request that never left is a
                # second failure on top of the first. Sixty calls a day, and a
                # chat that hits this path repeatedly could spend the whole
                # allowance without ever reaching a provider.
                await anyio.to_thread.run_sync(
                    _release_one, plan["claim_day"])
                self.unhandled += 1
                self.last_error = "running_row_unwritable"
                logger.warning(
                    "Notebook extraction abandoned before the call for "
                    "chat_id=%d: its trace could not be written.", chat_id)
                return None
            handed_over = True
            return plan, model_id, language
        finally:
            if not handed_over:
                self._active.discard(work_key)
                self._active_chats.discard(chat_id)

    async def _handle(self, chat_id: int, *, min_new: int | None = None,
                      after_id: int | None = None) -> None:
        # THE PREAMBLE IS A TRACKED, SHIELDED TASK.
        #
        # Everything before the provider call touches the database from a
        # worker thread, and two of those touches WRITE: the daily claim
        # commits in its own transaction, and the running row opens a
        # `BEGIN IMMEDIATE`. None of it was reachable from `quiesce()`.
        #
        # The chain that made that a real hazard: `quiesce()` cancels the
        # loop task and then waits for `self.settling` - which is set AFTER
        # the provider answers, so during the whole preamble there is nothing
        # registered to wait for. `_await_inflight` returned instantly,
        # `lock_vault_now` cleared the vault key on its very next line, and a
        # thread was still holding an open keyed connection and a write lock.
        # Cancelling the loop task does not stop that thread: a thread
        # already inside `run_sync` runs to completion whatever the awaiting
        # coroutine does.
        #
        # So the preamble runs as its own task, registered before it starts
        # and shielded from the caller's cancellation, exactly as the settle
        # tail already was. The grace period applies to it too.
        prep = asyncio.create_task(
            self._prepare(chat_id, min_new=min_new, after_id=after_id),
            name="notebook-prepare")
        self._track(prep)
        try:
            ready = await asyncio.shield(prep)
        except asyncio.CancelledError:
            # THE SHIELD WORKED, AND THAT IS THE PROBLEM TO HANDLE.
            #
            # `prep` keeps running and will finish its claim and its
            # `running` row, then hand a plan to this coroutine, which is
            # gone. Nothing sends the request. Left alone, the row becomes
            # `abandoned_in_flight` on the next cycle and the cursor counts
            # that range as read - so a vault lock landing here cost one of
            # sixty daily calls and one stretch of messages, permanently,
            # for a request that never left the machine.
            #
            # The undo is attached rather than awaited: this coroutine is
            # being torn down and may not await anything. `prep` is still in
            # `_inflight`, so `_await_settle` holds the grace period open
            # for it, and the callback runs on the same loop before that
            # window closes.
            prep.add_done_callback(self._undo_abandoned_preamble)
            raise
        if ready is None:
            return
        loop = asyncio.get_running_loop()
        plan, model_id, language = ready
        try:
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
            # Same window as the one above, same reason: the prompt is built
                # BEFORE the request, so this failure means nothing was sent.
                # SUBSCRIPT, not `.get`. This line is only reachable after
                # the claim succeeded, so the key is always there - and the
                # fallback a `.get` would take is `day=None`, which is the
                # re-derive this whole change removed. A refactor that broke
                # the invariant would silently restore the bug.
                await anyio.to_thread.run_sync(
                    _release_one, plan["claim_day"])
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
                # The class name alone said "OpenRouterError" for every single
                # provider failure - a timeout, a refused proxy, a rate limit, a
                # missing key - and threw away the only part a reader could act
                # on. `openrouter` raises one class and puts the separated reason
                # in the message, which is why the settle path two hundred lines
                # down already records `str(exc)`.
                # RELEASED ONLY WHEN NOTHING WAS BILLED.
                #
                # This except catches both sides of the only line that
                # matters: a request that never reached a socket
                # (`api_key_not_set`, a refused proxy, a destination the
                # egress gate would not open, a connect timeout) and one that
                # was sent, generated, billed and then failed. Keeping the
                # slot for both was the conservative half, and it cost a
                # sixtieth of the day every time a proxy was misconfigured.
                #
                # NOT a `sent_at` column, which is what this was waiting for.
                # A stamp we write after the fact cannot tell a connect
                # timeout from a read timeout - both are one
                # `TimeoutException` by the time anyone could write it, and
                # one of them has been paid for. The exception knows, because
                # it is raised at the point where the answer is a fact. See
                # `OpenRouterError.reached_provider`.
                #
                # `is False`, not `not`: an exception from somewhere else
                # carries no such attribute, and `getattr(..., True)` keeps
                # the old behaviour for it. Unknown still means billed.
                if getattr(exc, "reached_provider", True) is False:
                    await anyio.to_thread.run_sync(
                        _release_one, plan["claim_day"])
                await _record_failure(chat_id, plan, _failure_type(exc))
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
            # In the set as well as in the pointer. The pointer is what the
            # done-callback below guards and what the tests read; the set is what
            # survives a second extraction starting while this one still runs -
            # which `quiesce()` makes possible by restarting the loop after its
            # own timeout.
            self._track(settle_task)

            def _clear_settling(t: asyncio.Task, w: "Worker" = self) -> None:
                if w.settling is t:
                    w.settling = None
            settle_task.add_done_callback(_clear_settling)

            await asyncio.shield(settle_task)
        finally:
            # THE RANGE IS FREE AGAIN, whatever happened to it.
            #
            # One place, not eight: every path out of this turn - sent and
            # settled, refused by the provider, cancelled mid-generation -
            # ends here, and a key left behind would refuse the range
            # forever with `already_in_flight` and no way back. The
            # cancellation path is the exception that proves it: `_handle`
            # never reaches this line then, so `_undo_abandoned_preamble`
            # discards the key itself.
            self._active.discard(plan["work_key"])
            self._active_chats.discard(chat_id)

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
            proposals, dropped = notebook_extract.parse_reply(
                reply, plan["chunk"], plan["existing"],
                spans=plan.get("spans"))
            # Counted, not discarded. These are the only evidence of WHY a
            # paid call produced no notes; without them "0 notes this week"
            # and "every suggestion failed the grounding check" are the same
            # screen. Kept in memory and on the status route rather than in
            # the schema: the reasons are a fixed vocabulary produced by our
            # own parser, so a counter answers the question a column would.
            # COUNTED, and only counted. Not logged: this file is in
            # log_leak_scan's "must stay clean" list, and both of these are
            # derived from `parse_reply(reply, ...)` - the scanner cannot
            # know that its keys are a fixed vocabulary of our own, and it is
            # right not to guess. The counter reaches the status route, which
            # is where a reader looks anyway; a log line would add nothing
            # the panel does not already say.
            for reason, count in (dropped or {}).items():
                if count:
                    self.dropped[reason] = self.dropped.get(reason, 0) + count
        except notebook_extract.ExtractionFailed as exc:
            # A reply that cannot be trusted is a FAILURE, and the range stays
            # unread. Recorded as done-with-nothing it would be skipped
            # forever, which is the shape this whole design is built against.
            self.breaker.failed(loop.time())
            await _record_failure(chat_id, plan, str(exc), usage=usage)
            return

        try:
            outcome = await anyio.to_thread.run_sync(
                _write, chat_id, plan, proposals, usage)
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

        # NOT RAISING IS NOT SUCCEEDING.
        #
        # commit_extraction reports three of its failures by returning them:
        # `duplicate` (this work key was already answered), `stale_attempt`
        # (the row has since been reclaimed by a retry), and `written == 0`
        # with a skip reason (the range was cleared or rewritten while the
        # reply was out). Every one of those was read as a success, `runs`
        # went up, and `succeeded()` wiped the breaker's memory of the real
        # failures that came before - so the panel showed a healthy worker
        # writing nothing, indefinitely.
        #
        # `written == 0` with no proposals at all is a different thing and is
        # NOT counted here: a reply that honestly found nothing worth noting
        # is a completed run. The empty case is about a call whose result
        # could not be landed, not about a quiet chat.
        landed = bool(outcome) and (
            not outcome.get("duplicate")
            and not outcome.get("stale_attempt")
            and (outcome.get("written", 0) > 0 or not proposals))
        if not landed:
            self.settled_empty += 1
            # On the status screen, not in the log. Same reason as the
            # dropped counters above: this is derived from what `_write`
            # returned for this reply, and nothing in this file may put a
            # value derived from a reply into elysium.log.
            self.last_error = _empty_reason(outcome)
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

    def _undo_abandoned_preamble(self, task: "asyncio.Task") -> None:
        """Give back what a preamble claimed for a turn that never happened.

        Runs as a done-callback on `_prepare` when the coroutine that would
        have used its plan was cancelled. Two things go back: the daily call
        slot, and the `running` row - because a row left behind is read by
        `settle_orphaned_running` as a paid attempt and moves the cursor past
        a range nothing has read.

        Best effort and silent about the vault, deliberately: the usual
        reason for landing here IS the vault locking, and by now the key may
        be gone. What it must never do is raise out of a done-callback,
        where asyncio would log it as an unretrieved exception.
        """
        if task.cancelled():
            return
        if task.exception() is not None:
            # It failed before claiming anything; `_prepare`'s own handlers
            # already released what they took.
            return
        ready = task.result()
        if ready is None:
            return
        plan, _model_id, _language = ready
        self.abandoned_preambles += 1
        # `_handle` was torn down before its `finally` could run, so the key
        # is given back HERE or not at all - and not at all means the range
        # is refused for the life of the process.
        self._active.discard(plan["work_key"])
        self._active_chats.discard(plan["chat_id"])

        def _undo() -> None:
            # TWO REPAIRS, AND NEITHER MAY EAT THE OTHER.
            #
            # These were one unguarded block. `get_db` raises
            # `VaultLockedError` - which is the usual reason this callback
            # exists at all - and that took the row deletion down with the
            # refund, leaving behind the very `running` row this method's
            # docstring says moves the cursor past a range nothing read. The
            # same change taught `_release_one` to expect that exception and
            # left its twin here with nothing.
            from database import get_db

            _release_one(plan["claim_day"])
            try:
                with get_db() as con:
                    con.execute(
                        "DELETE FROM notebook_extractions "
                        "WHERE work_key = ? AND status = 'running'",
                        (plan["work_key"],))
            except vault_state.VaultLockedError:
                logger.info(
                    "Notebook: the trace of an abandoned preamble could not "
                    "be removed because the vault locked first; the next "
                    "cycle closes it out instead.")
            except Exception:
                logger.warning(
                    "Notebook: the trace of an abandoned preamble could not "
                    "be removed.")

        try:
            # TRACKED, like every other write this module starts.
            #
            # A bare `create_task` here was not waited for by
            # `_await_settle` and was referenced only weakly by the loop, so
            # the undo's `BEGIN IMMEDIATE` could land AFTER the stand-down
            # had returned and told the vault route it was safe to rekey.
            # The fix for an abandoned preamble was reopening, in miniature,
            # the race the tracking exists to close.
            self._track(asyncio.get_running_loop().create_task(
                anyio.to_thread.run_sync(_undo)))
        except Exception:                                 # pragma: no cover
            pass

    def _track(self, task: "asyncio.Task") -> None:
        """Register a task `quiesce()` and `stop()` must wait for.

        Registered BEFORE it can do anything, which is the whole point: the
        window this closes is the one between "a thread has started touching
        the database" and "somebody has recorded that fact".
        """
        self._inflight.add(task)
        task.add_done_callback(self._inflight.discard)

    def count_backlog(self) -> dict:
        """Measure what has not been read. Spends nothing, starts nothing.

        Called from the unlock bootstrap, on the bootstrap's own thread. It
        touches no asyncio object - a plain attribute, not the queue - so it
        is safe there; `offer()` would not be, and offering here is the thing
        this design refuses anyway.

        Never raises. A count that fails must not take an unlock down with
        it, and a stale zero reads as "nothing to report", which is the
        harmless direction.
        """
        from database import get_db

        try:
            with get_db() as con:
                self.backlog = notebook.unread_backlog(con)
        except Exception as exc:                              # noqa: BLE001
            # The CLASS, not the traceback. This file is in the "must stay
            # clean" list: an exception's own message can carry a filesystem
            # path, and a path carries the Windows account name.
            logger.warning("Notebook: could not count the unread backlog (%s).",
                           type(exc).__name__)
        return dict(self.backlog)

    async def sweep(self, chat_id: int) -> dict:
        """Read this chat's unread history, because somebody asked.

        ONE work unit, through the SAME `_handle` the turn path uses - the
        same claim against the daily cap, the same running row, the same
        settle. A second entry point into the planner would mean two cursors
        and two ways to bill for the same range, which is the failure this
        module is mostly built against.

        Refuses to run twice at once. Not a lock on the chat: a lock on this
        button. A reader who presses it three times has asked for one thing.
        """
        if self._sweeping:
            return {"started": False, "reason": "already_running"}
        # AND the other entry point counts too.
        #
        # `already_running` is a lock on this button. The loop reads the same
        # chats from the other side, and a sweep that started while it was
        # mid-call used to plan the identical range, pass `already_done` -
        # which only answers for `status='done'` - and be billed a second
        # time for one answer. Refused here rather than three hundred lines
        # down, because here there is somebody to tell: the panel renders
        # this as "It is already reading. Give it a moment."
        if chat_id in self._active_chats:
            return {"started": False, "reason": "already_in_flight"}
        from database import get_db

        def _where() -> int | None:
            with get_db() as con:
                return notebook.first_unread_message(con, chat_id)

        after_id = await anyio.to_thread.run_sync(_where)
        if after_id is None:
            return {"started": False, "reason": "nothing_unread"}

        self._sweeping = True
        # A TRACKED TASK, not an inline await.
        #
        # This runs on the HTTP request task, which `quiesce()` neither
        # cancels nor waits for - it cancels `worker.task` and waits on
        # `_inflight`, and an inline sweep was in neither. So a vault lock
        # landing mid-sweep returned at once while the request went on
        # carrying decrypted chat text to the provider, which is the one
        # thing the quiesce exists to stop. The paid reply was then lost on
        # a cleared key, and `_settle`'s write handler called
        # `breaker.failed()` - so locking the vault walked the breaker
        # toward `stopped`.
        #
        # Registered before it can do anything, exactly as `_track`'s own
        # docstring requires, and awaited here so the route still answers
        # only when the work is done.
        task = asyncio.create_task(
            # min_new=1: the reader has already decided this is worth a call,
            # and it is also what keeps the planner's jump-to-the-present
            # branch out of the way.
            self._handle(chat_id, min_new=1, after_id=after_id))
        self._track(task)
        try:
            await asyncio.shield(task)
        finally:
            self._sweeping = False
        return {"started": True, "after_id": after_id}

    def status(self, now: float | None = None) -> dict:
        """What the panel reads.

        `now` so the breaker is asked about the present rather than about
        whenever the loop last dequeued something. Defaulted from the running
        loop when there is one - the status route is async, so there always
        is in production - and left as None only where no loop exists, which
        is a handful of synchronous tests.
        """
        if now is None:
            try:
                now = asyncio.get_running_loop().time()
            except RuntimeError:
                now = None
        return {
            "state": self.breaker.state_at(now),
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
            "abandoned_preambles": self.abandoned_preambles,
            "last_error": self.last_error,
            "runs": self.runs,
            # Runs that produced NOTHING, kept apart from `runs` rather than
            # folded into it: "it ran" and "it ran and landed something" are
            # different claims and only one of them is what a reader means by
            # a working notebook.
            "settled_empty": self.settled_empty,
            # So the panel can leave the button disabled while it runs
            # rather than discovering the refusal by pressing it.
            "sweeping": self._sweeping,
            "refused_in_flight": self.refused_in_flight,
            # Reported, not acted on. A chat whose history was never read is
            # invisible otherwise: the worker only moves forward and says
            # nothing about what it stepped over.
            "backlog": dict(self.backlog),
            # Why suggestions were refused, by reason. Empty is the healthy
            # answer, and an empty dict says that rather than saying nothing.
            "dropped": dict(self.dropped),
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
    #
    # And anything left over from a PREVIOUS loop goes with it. The queue was
    # already rebuilt for this reason; `_inflight` and `settling` are the same
    # kind of state and were not, so a task pending when the last loop closed
    # stayed in the set and poisoned the next stand-down's `gather`.
    here = asyncio.get_event_loop()
    worker._inflight = {t for t in worker._inflight if t.get_loop() is here}
    if worker.settling is not None and worker.settling.get_loop() is not here:
        worker.settling = None
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
    """Wait for every in-flight database step to finish, bounded.

    It used to wait for ONE thing: the tail that lands an already-paid reply.
    That tail is registered only after the provider answers, so for the whole
    preamble - planning, the daily claim, the running row, two of which WRITE
    - there was nothing registered and this returned instantly. The caller's
    next line clears the vault key.

    Shared by `quiesce()` and `stop()`. `asyncio.shield` here for the same
    reason `_handle` shields the task in the first place: if the CALLER of
    this function is itself cancelled (a lock request torn down mid-flight,
    a shutdown that does not wait), that must not re-open the exact race this
    exists to close by cancelling the shielded settle task out from under us
    a second time.
    """
    # THIS LOOP'S tasks only, and the stale ones are dropped on the way past.
    #
    # `worker` is a module singleton; a task still pending when its loop
    # closed stays in the set forever, and one such member makes
    # `asyncio.gather` raise synchronously below. The broad handler then
    # logged one line and returned - so a real, already-paid settle in the
    # same snapshot was never waited for. The message said the settle failed;
    # nobody had waited.
    #
    # Filtering rather than clearing: a live task from this loop must not be
    # dropped just because a dead one is keeping it company.
    here = asyncio.get_running_loop()
    stale = {t for t in worker._inflight if t.get_loop() is not here}
    worker._inflight -= stale
    if stale:
        logger.debug("Notebook: dropped %d task(s) from a closed loop.",
                     len(stale))
    pending = {t for t in worker._inflight if not t.done()}
    settle = worker.settling
    if settle is not None and settle.get_loop() is not here:
        settle = None
    if settle is not None and not settle.done():
        # Belt and braces: the pointer is what every existing test reads, and
        # the set is what cannot be overwritten. Waiting for the union costs
        # nothing when they are the same task, which is the ordinary case.
        pending.add(settle)
    if not pending:
        # NOTHING is in flight, so this returns AT ONCE. Not a formality: a
        # version that always waited out the grace period would put five
        # seconds on every vault lock, including the idle timer's, and the
        # test that pins `elapsed < 1.0` exists because that is a tempting
        # and wrong way to write this.
        return
    try:
        await asyncio.wait_for(
            asyncio.shield(asyncio.gather(*pending, return_exceptions=True)),
            timeout=SETTLE_GRACE_S)
    except asyncio.TimeoutError:
        logger.warning(
            "Notebook: %d in-flight extraction step(s) did not finish within "
            "%.0fs; proceeding without them.", len(pending), SETTLE_GRACE_S)
    except asyncio.CancelledError:
        # OURS, and it travels. The `shield` above already protects the
        # gather - those tasks keep running whatever happens here - so
        # honouring a cancellation aimed at this coroutine costs the settle
        # nothing and is what the caller asked for. Swallowing it made every
        # caller of this function unbounded.
        raise
    except Exception:
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
        # `asyncio.wait`, not `await task`.
        #
        # `await task` raises the task's own `CancelledError`, which has to
        # be ignored - it is the cancellation just requested. A bare
        # `except BaseException` around it also ignores a cancellation aimed
        # at THIS coroutine, and that made this function uncancellable: it
        # carried on to `start()` below and built a fresh worker task on a
        # loop that might be closing. Anything bounding a vault lock with
        # `asyncio.wait_for` would then wait forever, because `wait_for`
        # cancels the inner coroutine and waits for it to finish.
        #
        # `wait` returns when the task settles, however it settled, and
        # raises only when the waiter itself is cancelled. That is exactly
        # the distinction the `except` could not make.
        #
        # OUR LOOP ONLY, and this line is why the previous spelling survived.
        # `await task` raises RuntimeError at once when the task belongs to
        # another loop, and the `except BaseException` around it swallowed
        # that - so the lock carried on, by accident. `asyncio.wait` has no
        # such guard: it attaches a callback that resolves a future owned by
        # THIS loop from the OTHER loop's thread, and this loop is never
        # woken. Measured: the worker task reaches CANCELLED and the wait has
        # still not returned seventy-five seconds later. A vault lock that
        # never returns is worse than either thing the change was about.
        #
        # A foreign task has already been cancelled by the line above, and
        # its own loop will run that cancellation. There is nothing this loop
        # can usefully wait for, and `_await_settle` below filters foreign
        # tasks out of the grace period for the same reason.
        if task.get_loop() is asyncio.get_running_loop():
            await asyncio.wait({task})

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
        # Same reasoning as `quiesce()`, including the loop check: waiting
        # on a task another loop owns deadlocks this one outright. Fixed
        # here in the same change rather than left for the day a test
        # happens to call `stop()` across loops - which is the only reason
        # this site was not red too.
        if task.get_loop() is asyncio.get_running_loop():
            await asyncio.wait({task})
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


def _claim_one() -> str:
    """Reserve one call and RETURN THE DAY it was reserved against.

    The day travels with the plan so the refund can name the same row. See
    `notebook_store.release_call`: deriving it twice, hours apart, is how a
    claim at 23:59 was refunded out of the first of January.
    """
    from database import get_db
    with get_db() as con:
        day = notebook.spend_day(con)
        notebook.claim_call(con, config.NOTEBOOK_DAILY_CALL_CAP, day=day)
        return day


def _release_one(day: str | None = None) -> None:
    """Hand the claimed slot back. Never raises into the caller.

    Both callers are already on an abandon path, and the whole point of the
    slot is that the day's budget stays honest - failing to give it back is
    the cheaper of the two mistakes, and it is the one that used to happen
    every time.

    `day` is the day the claim was stamped with, carried on the plan. A
    refund that re-derives it lands on the wrong row after midnight.

    AND IT SAYS SO WHEN IT FAILS. The bare `except: pass` was swallowing the
    single most likely outcome on this path: the usual reason a refund is
    needed at all is that the vault locked, and at that moment `get_db`
    raises `VaultLockedError` before a statement runs. So the commonest
    failure of the repair was the one nothing recorded - no counter, no log,
    the slot simply gone out of sixty. Still never raises, because both
    callers are unwinding an abandoned turn and neither can act on it; but
    a reader looking for where their day went now has a line to find.
    """
    from database import get_db

    try:
        with get_db() as con:
            notebook.release_call(con, day=day)
    except vault_state.VaultLockedError:
        # EXPECTED, and named rather than counted as a fault: the lock is
        # what abandoned the turn in the first place. No values, no day
        # string - this module is on log_leak_scan's must-stay-clean list.
        logger.info(
            "Notebook: a claimed call could not be given back because the "
            "vault locked first. Today's allowance is one call smaller than "
            "it should be.")
    except Exception:
        logger.warning(
            "Notebook: a claimed call could not be given back. Today's "
            "allowance is one call smaller than it should be.")


def _empty_reason(outcome: dict | None) -> str:
    """Why a paid reply landed nothing, in one word for the status screen."""
    if not outcome:
        return "settled_nothing"
    if outcome.get("stale_attempt"):
        return "settled_stale_attempt"
    if outcome.get("duplicate"):
        return "settled_duplicate"
    return "settled_empty"


def _failure_type(exc: BaseException) -> str:
    """What to write in `error_type` for a failed call.

    The class name, plus this app's own short reason code when the exception
    carries one - as an attribute, or as the whole of its message.

    `openrouter` raises ONE class for everything and separates the cause into
    a short code: `openrouter_timeout`, `proxy_auth_failed`, `api_key_not_set`
    and the rest of the `_status_to_reason` vocabulary. Recording only the
    class meant every provider failure in the table read `OpenRouterError`,
    and "it broke" and "you have not set an API key" became the same row.

    Both sources are checked against `isidentifier()`, and that is the guard
    rather than a formality: a code from our own vocabulary is one word with
    no spaces, while an arbitrary exception MESSAGE can carry a path, a URL,
    or something a person typed. Anything that does not look like one of our
    codes is refused and the class name stands.
    """
    name = type(exc).__name__
    for candidate in (getattr(exc, "reason", None), str(exc)):
        if isinstance(candidate, str) and candidate.isidentifier():
            return f"{name}:{candidate}"
    return name


def _have_api_key() -> bool:
    """Whether a request could leave this machine at all.

    Imported inside the function for the same reason `openrouter` is: this
    module is imported by the send path, and the secrets layer pulls in the
    vault. Failure is treated as "no key" rather than raised - a background
    feature that cannot read a secret must not take the send path down with
    it, and answering "no" costs a skip row, which is exactly the visible,
    bounded outcome this branch exists to produce.
    """
    try:
        # The same two names openrouter.complete itself reads, from the same
        # module. Reading the secret by any other route would be a second
        # answer to a question that already has one, and the two could
        # disagree.
        from openrouter import SECRET_API_KEY, get_secret
        return bool(get_secret(SECRET_API_KEY))
    except Exception as exc:                              # noqa: BLE001
        # As above: the class name only.
        logger.warning("Notebook: could not read the API key (%s).",
                       type(exc).__name__)
        return False


def _existing_line(entry: dict) -> str:
    """One note as the model sees it, with what KIND of note it is.

    Three states that look identical in a plain list and mean different
    things: something the reader wrote, something the model suggested and the
    reader kept, and something the model suggested that nobody has looked at
    yet. The last of those only became visible to the model at all when the
    list widened to stop it re-proposing its own observations - and a
    suggestion it cannot tell from an accepted fact is a suggestion it will
    treat as settled.
    """
    if entry["status"] == notebook.STATUS_PROPOSED:
        return f"(unreviewed suggestion) {entry['text']}"
    if entry["provenance"] == notebook.PROV_USER:
        return f"(written by the reader) {entry['text']}"
    return entry["text"]


def _plan_work(chat_id: int, model_id: str, language: str,
               threshold: int, limit: int, *,
               min_new: int | None = None,
               after_id: int | None = None,
               keep=()) -> dict | None:
    """Decide whether there is enough new material, and gather it.

    A25: the delta is what gets extracted, plus a two-message disambiguation
    window that is shown as CONTEXT and is explicitly not extracted from.
    Graphiti's default is the same two, with the same rule.

    `threshold` is how much new material is worth a call; `limit` is how much
    one call may read. They are different numbers and conflating them was a
    ten-fold spending bug - see Worker.batch_size.

    TWO CALLERS, ONE DOOR. The ordinary turn-driven path passes neither
    keyword and behaves exactly as before. The sweep - a reader asking for a
    chat's unread history to be read - passes both, and they are what make it
    a different question rather than a different function:

      * `min_new` says "one message is enough", because a person asking for
        this has already decided it is worth a call. It also CLOSES the
        upgrading-user branch below: that branch exists to spare somebody the
        cost of reading four hundred old messages they did not ask for, and
        it must not fire for somebody who did ask.
      * `after_id` names where to read from, so a sweep can fill a hole the
        ordinary cursor has already passed. The cursor is a MAX and can only
        move forward; a range that was skipped is below it forever.

    Opening a second entry point into the worker is exactly the thing this
    module refuses elsewhere, so this is a keyword pair on the existing
    planner rather than a second planner - one cursor, one claim, one
    accounting.
    """
    from database import get_db

    with get_db() as con:
        # Before anything is planned, close out the trace of a call that was
        # made and never settled - the app killed with the window, or the
        # vault locked mid-request. See notebook.settle_orphaned_running: the
        # money for that range is already spent, and a cursor that will not
        # move past it re-sends and re-bills the identical range on every
        # later cycle.
        abandoned = notebook.settle_orphaned_running(con, chat_id, keep)
        if abandoned:
            # Read, at last. This was assigned and never used, so the one
            # event it names - a paid call that was never settled, now closed
            # out - left no trace anywhere a person could see it.
            logger.info("Notebook: closed %d abandoned extraction(s) for a "
                        "chat before planning.", abandoned)
        last = con.execute(
            "SELECT COALESCE(MAX(to_message_id), 0) FROM notebook_extractions "
            "WHERE chat_id = ? AND (status = 'done' "
            "     OR (status = 'failed' AND error_type = ?))",
            (chat_id, notebook.ABANDONED_IN_FLIGHT)).fetchone()[0]
        if after_id is not None:
            # The sweep reads from where it was TOLD to, not from the high
            # water mark. `last` is a MAX and only moves forward, so a range
            # the ordinary path stepped over - a budget that dropped the
            # oldest lines of a batch, an upgrading chat that started at the
            # present - sits below it and is unreachable for good. This is
            # the only way back to it.
            last = after_id
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
        # A person who asked for this has already decided it is worth a
        # call, so the turn-count threshold is theirs to override - but only
        # downward, and only for their own request.
        if pending < (min_new if min_new is not None else threshold):
            return None

        # LIMIT, and the range is what was actually READ. Without it a long
        # backlog was selected whole, `to_id` named the last of five hundred
        # messages, the prompt builder kept the newest twelve thousand
        # characters of it, and the transaction marked the entire range done -
        # so the other four hundred and eighty were silently never extracted.
        # The one silent-loss shape this whole module exists to prevent,
        # rebuilt at the other end of the same function.
        if (min_new is None and last == 0 and pending > limit * 2
                and not ever_extracted):
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
            #
            # And it is now a trade the reader can UNDO. This branch used to
            # be the end of the story: the cursor sat at the newest message
            # and the `AND id > ?` branch below could never look under it
            # again, so the whole history was unreachable by any route. The
            # sweep passes `min_new`, which is what keeps it out of here.
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
        # On THIS connection. `list_entries` opens its own, and doing that
        # from inside an open `get_db()` block works today only because the
        # database is in WAL mode - the moment the outer block becomes a
        # `BEGIN IMMEDIATE`, the inner connection waits out the busy timeout
        # and fails. Reading it here costs one query and removes the trap.
        entries = [dict(r) for r in con.execute(
            "SELECT id, text, status, provenance FROM notebook_entries "
            "WHERE chat_id = ? AND retired_at IS NULL ORDER BY position, id",
            (chat_id,)).fetchall()]

    # WHAT ENTERS THE PROMPT IS WHAT GETS MARKED READ.
    #
    # `build_user_message` budgets these lines and drops whole ones from the
    # OLD end when they do not fit. The planner then recorded `from_id` as
    # the first row of the SQL range - including the lines the budget threw
    # away - so a batch larger than the character ceiling marked messages
    # read that no model ever saw. The cursor is a MAX, so they were never
    # coming back.
    #
    # Budgeting HERE, where the ids and the text are still side by side, is
    # the same rule the existing-notes list a few lines down already follows,
    # and for the same reason. `notebook_extract._budget` stays exactly as it
    # is (its `keep="tail"` is load-bearing and locked): given a list that
    # already fits, it returns it unchanged.
    #
    # The tags come off first, because a stripped line is shorter and the
    # budget must be spent on what the model will actually read.
    # The role and the body, kept APART as well as joined.
    #
    # The joined form is what the model reads; the split form is what the
    # grounding gate reads. Rebuilding the second from the first is where a
    # message body that contains a line starting `user: ` became a message
    # of its own - the model's words attributed to the person, and the
    # panel's "from the model's own reply" mark silenced on the one note
    # class it exists for.
    parts = {r["id"]: (r["role"],
                       voice_tags.strip_for_display(r["content"], r["role"]))
             for r in rows}
    lines = [(r["id"], f"{r['role']}: "
              f"{voice_tags.strip_for_display(r['content'], r['role'])}")
             for r in rows]
    kept = notebook_extract.budget_pairs(lines, notebook_extract.TURNS_MAX_CHARS)
    if not kept:
        # Even one line does not fit. Reading nothing is right; marking the
        # range read would be the silent loss this whole block is about.
        return None
    rows = [r for r in rows if r["id"] in {i for i, _ in kept}]

    new = [text for _, text in kept]
    # The same stripping for the context window. Not cosmetic: the grounding
    # check in parse_reply compares a quote against one MESSAGE of `chunk`,
    # and `chunk` is built from these same lines - so stripping one side and
    # not the other would make every quote ungrounded and silently empty the
    # feature.
    recent = [f"{r['role']}: "
              f"{voice_tags.strip_for_display(r['content'], r['role'])}"
              for r in reversed(window)]
    # ACCEPTED notes and the model's OWN pending suggestions.
    #
    # The model could not see what it had already proposed, so on the next
    # window it proposed the same fact again, and again - a review queue that
    # grew a fresh copy of every observation per turn, which is the fastest
    # way to make a reader stop reading it. It sees them now.
    #
    # A pending note the USER wrote is a state that does not exist (the panel
    # writes accepted rows), so `provenance='model'` costs nothing here and
    # keeps the widening from ever reaching the reader's own writing.
    #
    # This is the half that goes in SECOND. Being visible means being
    # nameable in `supersedes`, and a model naming its own unreviewed
    # suggestion would have retired a note nobody had looked at yet -
    # retire_superseded refuses that, and it refuses it because of the
    # `status = accepted` condition that landed with this same change.
    live = [e for e in entries
            if e["status"] == notebook.STATUS_ACCEPTED
            or (e["status"] == notebook.STATUS_PROPOSED
                and e["provenance"] == notebook.PROV_MODEL)]
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
        # The same messages, still in pieces. `parse_reply` grounds against
        # THESE rather than re-parsing the string above, so nothing a message
        # body contains can invent a boundary.
        "spans": [parts[i] for i, _ in kept],
        "card": (card_row["description"] if card_row else "") or "",
        # MARKED, not bare. The model was shown a numbered list with no way
        # to tell its own unreviewed suggestions from notes the reader wrote
        # and kept - so it re-proposed its own pending observations turn
        # after turn, and a `supersedes` naming one of the reader's notes
        # looked exactly like naming one of its own.
        #
        # The order and the LENGTH are untouched, and that is not a style
        # choice: `parse_reply` bounds-checks against `len(existing)` and
        # `commit_extraction` resolves `existing_ids[idx]`, so the two lists
        # have to stay index-for-index aligned. Adding a prefix is safe;
        # dropping, sorting or trimming an entry is not.
        "existing": [_existing_line(e) for e in live],
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
            attempt_token=plan.get("attempt_token"),
            claim_day=plan.get("claim_day"))


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
    if reason not in SKIP_REASONS:
        # NOT an `assert`. A gate written as an assertion is a gate with a
        # command-line switch on it: `python -O` removes it and the
        # undeclared reason is written silently, which is the one outcome
        # this check exists to prevent.
        #
        # The two spec files carry no `optimize=` today and `PYTHONOPTIMIZE`
        # appears nowhere in the repo, so nothing SHIPS with them stripped -
        # this closes a latent hole rather than a live one. Closing it here
        # rather than pinning `optimize=0` in the build is the stronger of
        # the two: it holds however the process is started, including one
        # somebody starts by hand.
        raise ValueError(f"undeclared skip reason: {reason}")
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
                attempt_token=plan.get("attempt_token"),
                claim_day=plan.get("claim_day"))
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
