"""U-29 - the vault key must not be cleared out from under a writing thread.

`quiesce()` is called by `lock_vault_now` IMMEDIATELY before the vault key is
cleared, and its job is to make sure nothing is left holding decrypted state.
It waited for exactly one thing: the tail that lands an already-paid reply.

That tail is registered AFTER the provider answers. Everything before it -
planning (which decrypts every pending message), the daily claim (its own
transaction), the running row (a `BEGIN IMMEDIATE`) - ran with nothing
registered at all, so the wait returned instantly and the key was cleared
while a thread still held an open keyed connection and a write lock.

Cancelling the loop task does not help: a thread already inside
`anyio.to_thread.run_sync` runs to completion whatever the awaiting coroutine
does. Only waiting for it does.

The three things this file has to prove together, because any one of them
alone can be satisfied by a wrong fix:

  * the preamble IS waited for;
  * with nothing in flight the wait still returns AT ONCE (a version that
    always sleeps out the grace period would put five seconds on every idle
    lock);
  * a STUCK preamble does not hold the lock past the grace period.
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

import notebook_worker


def seed(client, count: int = 30) -> int:
    """A chat with a backlog the planner will want to read."""
    from database import get_db

    from tests.conftest import make_character, make_chat

    chat_id = make_chat(client, make_character(client))
    with get_db() as con:
        for i in range(count):
            con.execute(
                "INSERT INTO messages (chat_id, role, content, active) "
                "VALUES (?,?,?,1)",
                (chat_id, "user" if i % 2 == 0 else "assistant",
                 f"line {i}: her brother owns the mill"))
    return chat_id


def replying(monkeypatch, facts):
    import json

    import openrouter

    async def _reply(*a, **kw):
        return {"id": "gen",
                "choices": [{"finish_reason": "stop",
                             "message": {"content": json.dumps(
                                 {"facts": facts})}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                          "cost": 0.0001}}

    monkeypatch.setattr(openrouter, "complete", _reply)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def clean_worker():
    w = notebook_worker.worker
    w._inflight = set()
    w.settling = None
    yield w
    for task in list(w._inflight):
        task.cancel()
    w._inflight = set()
    w.settling = None


class TestWhatTheLockWaitsFor:
    @pytest.mark.anyio
    async def test_a_step_in_flight_is_waited_for(self, clean_worker) -> None:
        w = clean_worker
        landed: list[str] = []
        release = asyncio.Event()

        async def pretend_preamble() -> None:
            await release.wait()
            landed.append("written")

        task = asyncio.create_task(pretend_preamble())
        w._track(task)
        await asyncio.sleep(0)

        async def let_go() -> None:
            await asyncio.sleep(0.05)
            release.set()

        asyncio.create_task(let_go())
        await notebook_worker._await_settle()

        assert landed == ["written"], (
            "the vault key would have been cleared with a write in flight")

    @pytest.mark.anyio
    async def test_with_nothing_in_flight_it_returns_at_once(
            self, clean_worker) -> None:
        """GROUND CONTROL, and it is the one that catches the tempting wrong
        fix. The vault locks on an idle timer many times a day; five seconds
        on every one of those is not a bug the user would report, it is a bug
        they would live with."""
        started = time.monotonic()
        await notebook_worker._await_settle()
        assert time.monotonic() - started < 1.0

    @pytest.mark.anyio
    async def test_a_stuck_step_does_not_hold_the_lock_forever(
            self, clean_worker, monkeypatch) -> None:
        """POSITIVE CONTROL on the bound. SETTLE_GRACE_S is a limit, not a
        promise that every write completes - a fix without this can hang a
        vault lock indefinitely, which is worse than the defect."""
        monkeypatch.setattr(notebook_worker, "SETTLE_GRACE_S", 0.2)
        w = clean_worker

        async def never() -> None:
            await asyncio.sleep(30)

        task = asyncio.create_task(never())
        w._track(task)
        await asyncio.sleep(0)

        started = time.monotonic()
        await notebook_worker._await_settle()
        elapsed = time.monotonic() - started

        assert 0.1 < elapsed < 3.0, elapsed
        task.cancel()

    @pytest.mark.anyio
    async def test_a_second_extraction_cannot_hide_the_first(
            self, clean_worker) -> None:
        """E7, and the reason a single pointer was not enough.

        `quiesce()` restarts the loop after its own timeout, so a new
        extraction can begin while an old settle is still running - and the
        new one overwrites `self.settling`. The pointer then names the wrong
        task and the old one is waited for by nobody.
        """
        w = clean_worker
        finished: list[str] = []

        # The first one is deliberately the SLOWER of the two. Equal
        # durations would let the second's wait cover the first by accident,
        # and the test would pass against an implementation that never looked
        # at the first task at all.
        async def slow(name: str, seconds: float) -> None:
            await asyncio.sleep(seconds)
            finished.append(name)

        first = asyncio.create_task(slow("first", 0.30))
        w.settling = first
        w._track(first)
        second = asyncio.create_task(slow("second", 0.02))
        w.settling = second           # exactly what a restarted loop does
        w._track(second)
        await asyncio.sleep(0)

        await notebook_worker._await_settle()

        assert sorted(finished) == ["first", "second"], (
            "the overwritten task was left running past the vault lock")


class TestTheRealPreambleIsRegistered:
    @pytest.mark.anyio
    async def test_the_preamble_task_is_tracked_before_it_touches_anything(
            self, client, monkeypatch) -> None:
        """Through the real `_handle`, with the real preamble.

        The plan step is held open on an event; the assertion is that at that
        moment - a thread inside the planner - there IS something registered
        for `quiesce()` to wait for. Before this fix there was nothing.
        """
        import config
        from database import set_setting
        from tests.test_notebook_worker import seed

        chat_id = seed(client, count=20)
        set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")
        w = notebook_worker.worker
        w.breaker.reset()
        w._inflight = set()
        # threading primitives, not asyncio ones: the thing being held open
        # is a WORKER THREAD, and asking it to wait on the event loop it is
        # blocking would deadlock rather than reproduce anything.
        release = threading.Event()
        inside = threading.Event()

        real_plan = notebook_worker._plan_work

        def slow_plan(*a, **kw):
            inside.set()
            release.wait(5)
            return real_plan(*a, **kw)

        monkeypatch.setattr(notebook_worker, "_plan_work", slow_plan)

        handle = asyncio.create_task(w._handle(chat_id))
        for _ in range(200):
            if inside.is_set():
                break
            await asyncio.sleep(0.01)
        assert inside.is_set(), "the planner was never reached"

        # THE assertion: something is registered while a thread is in there.
        assert any(not t.done() for t in w._inflight), (
            "nothing was registered while the planner held a connection")

        release.set()
        try:
            await asyncio.wait_for(handle, timeout=10)
        except Exception:                                     # noqa: BLE001
            pass


class TestAnAbandonedPreambleGivesEverythingBack:
    """The shield works, and that is what has to be handled.

    `_prepare` is shielded so a cancellation cannot leave a thread half-way
    through a keyed write. So when the vault locks mid-preamble it runs to
    completion - claiming a daily call and writing the `running` row - and
    hands its plan to a coroutine that no longer exists. Nothing is sent.

    Left alone the row becomes `abandoned_in_flight` on the next cycle and
    the planner's cursor counts that range as READ. One of sixty daily calls
    and one stretch of messages, gone permanently, for a request that never
    left the machine.
    """

    @pytest.mark.anyio
    async def test_the_slot_and_the_range_come_back(
            self, client, monkeypatch) -> None:
        import config
        import database
        import notebook_store as notebook

        chat_id = seed(client, 30)
        database.set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")

        sent: list[object] = []

        async def never_called(*a, **kw):
            sent.append(1)
            raise AssertionError("the request must never leave")

        import openrouter
        monkeypatch.setattr(openrouter, "complete", never_called)

        with database.get_db() as con:
            before = notebook.spend_today(con)["calls"]

        w = notebook_worker.Worker()
        w.queue = asyncio.Queue(maxsize=8)
        handle = asyncio.create_task(w._handle(chat_id))
        await asyncio.sleep(0)
        handle.cancel()
        try:
            await handle
        except asyncio.CancelledError:
            pass

        # The shielded preamble finishes on its own; give it and the undo a
        # turn of the loop each.
        for _ in range(50):
            await asyncio.sleep(0.01)
            if w.abandoned_preambles:
                break

        assert sent == [], "ground: nothing was ever sent"
        assert w.abandoned_preambles == 1, (
            "the preamble was not recognised as abandoned")

        for _ in range(50):
            await asyncio.sleep(0.01)
            with database.get_db() as con:
                if notebook.spend_today(con)["calls"] == before:
                    break

        with database.get_db() as con:
            assert notebook.spend_today(con)["calls"] == before, (
                "the day was charged for a call that never left")
            left = con.execute(
                "SELECT COUNT(*) FROM notebook_extractions "
                "WHERE chat_id = ? AND status = 'running'",
                (chat_id,)).fetchone()[0]
        assert left == 0, "a running row was left to move the cursor"

    @pytest.mark.anyio
    async def test_an_ordinary_turn_still_pays_for_itself(
            self, client, monkeypatch) -> None:
        """GROUND CONTROL. An undo that fired on every turn would hand the
        day's budget back after every real call - the opposite defect, and
        the one that reads as generosity."""
        import config
        import database
        import notebook_store as notebook

        chat_id = seed(client, 30)
        database.set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")
        replying(monkeypatch, [])

        with database.get_db() as con:
            before = notebook.spend_today(con)["calls"]

        w = notebook_worker.Worker()
        w.queue = asyncio.Queue(maxsize=8)
        await w._handle(chat_id)

        assert w.abandoned_preambles == 0
        with database.get_db() as con:
            assert notebook.spend_today(con)["calls"] == before + 1


@pytest.fixture(autouse=True)
def _leave_the_singleton_as_we_found_it():
    """`quiesce()` ends by RESTARTING the worker, so any test that drives it
    leaves a live task on the module-level singleton. The next test's ground
    control - "nothing left running from a previous test" - then fails for a
    reason that has nothing to do with it."""
    yield
    w = notebook_worker.worker
    if w.task is not None:
        w.task.cancel()
        w.task = None
    w._inflight.clear()
    w.settling = None


class TestTheStandDownCanItselfBeCancelled:
    """A wait that eats its own cancellation is a hang waiting to happen.

    `quiesce()` cancels the loop task and waits for it. The awaited task
    raises `CancelledError` because it was just cancelled - expected, and
    ignored. A bare `except BaseException` around that also ignores a
    cancellation aimed at `quiesce()` ITSELF, so the function carried on to
    `start()` and built a fresh worker task on a loop that might be closing.

    Nothing bounds a vault lock with a timeout today, which is why this was
    latent rather than live. It is a trap laid for whoever tries: `wait_for`
    cancels the inner coroutine and then waits for it, and the cancel was
    being eaten.
    """

    @pytest.mark.anyio
    async def test_a_cancelled_quiesce_stops(self) -> None:
        # The MODULE-LEVEL worker:  is a module function and
        # stands down the singleton, not a local instance.
        w = notebook_worker.worker
        w.queue = asyncio.Queue(maxsize=8)

        async def forever() -> None:
            await asyncio.sleep(3600)

        w.task = asyncio.create_task(forever())
        await asyncio.sleep(0)

        # A caller that gives up while the stand-down is in flight.
        caller = asyncio.create_task(notebook_worker.quiesce())
        await asyncio.sleep(0)
        caller.cancel()

        with pytest.raises(asyncio.CancelledError):
            await caller

    @pytest.mark.anyio
    async def test_bounding_it_with_a_timeout_does_not_hang(self) -> None:
        """The shape the swallow actually traps somebody in."""
        # The MODULE-LEVEL worker:  is a module function and
        # stands down the singleton, not a local instance.
        w = notebook_worker.worker
        w.queue = asyncio.Queue(maxsize=8)

        async def forever() -> None:
            await asyncio.sleep(3600)

        w.task = asyncio.create_task(forever())
        await asyncio.sleep(0)

        await asyncio.wait_for(notebook_worker.quiesce(), timeout=5)

    @pytest.mark.anyio
    async def test_an_ordinary_stand_down_still_finishes(self) -> None:
        """GROUND CONTROL. The cancellation the function REQUESTS must still
        be swallowed - a version that let that one through would make every
        ordinary vault lock raise."""
        # The MODULE-LEVEL worker:  is a module function and
        # stands down the singleton, not a local instance.
        w = notebook_worker.worker
        w.queue = asyncio.Queue(maxsize=8)

        async def forever() -> None:
            await asyncio.sleep(3600)

        w.task = asyncio.create_task(forever())
        await asyncio.sleep(0)

        await notebook_worker.quiesce()



class TestTheUndoIsWaitedForToo:
    """The fix for an abandoned preamble reopened, in miniature, the race
    the tracking exists to close.

    `_undo_abandoned_preamble` gives back the daily slot and deletes the
    `running` row - two writes, both keyed, both on a thread. It scheduled
    them with a bare `create_task`: not registered, so `_await_settle` did
    not wait for it, and referenced only weakly by the loop. So the undo's
    `BEGIN IMMEDIATE` could land AFTER `quiesce()` had returned to
    `lock_vault_now`, whose very next line clears the key.

    That is the same shape as the defect this whole file is about, on the
    error path of its own repair.
    """

    @pytest.mark.anyio
    async def test_the_stand_down_does_not_return_before_the_undo_lands(
            self, client, monkeypatch) -> None:
        import config
        import database
        import notebook_store as notebook

        chat_id = seed(client, 30)
        database.set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")

        async def never_called(*a, **kw):
            raise AssertionError("the request must never leave")

        import openrouter
        monkeypatch.setattr(openrouter, "complete", never_called)

        # The undo made slow ON PURPOSE, and only measurably so. DERIVED
        # from the grace period rather than written next to a comment about
        # it: a hand-typed 0.3 is silently coupled to a constant nothing
        # pins, and 0.4 against a grace period somebody lowered to 0.5 is a
        # flaky test that looks like a real failure.
        slow = notebook_worker.SETTLE_GRACE_S / 16
        assert slow < notebook_worker.SETTLE_GRACE_S / 4, (
            "the injected delay has to sit well inside the grace period, or "
            "this measures the timeout instead of the wait")
        real_release = notebook.release_call

        def slow_release(con, day=None):
            # The SAME signature as the real one, forwarded verbatim. A
            # double that accepts less than the function it stands in for
            # turns a signature change into a red test that looks like a
            # behaviour failure - which is exactly what happened when
            # `release_call` gained its `day`.
            time.sleep(slow)
            return real_release(con, day=day)

        monkeypatch.setattr(notebook, "release_call", slow_release)

        with database.get_db() as con:
            before = notebook.spend_today(con)["calls"]

        w = notebook_worker.Worker()
        w.queue = asyncio.Queue(maxsize=8)
        # The stand-down reads the module singleton, so the worker under test
        # has to BE the singleton for this to measure anything.
        monkeypatch.setattr(notebook_worker, "worker", w)

        handle = asyncio.create_task(w._handle(chat_id))
        await asyncio.sleep(0)
        handle.cancel()
        try:
            await handle
        except asyncio.CancelledError:
            pass

        # `abandoned_preambles` is incremented inside the done-callback,
        # BEFORE it schedules the undo - so this loop leaves us at the moment
        # the undo has just started and has not yet released anything.
        for _ in range(400):
            await asyncio.sleep(0.005)
            if w.abandoned_preambles:
                break
        assert w.abandoned_preambles == 1, "the preamble was not abandoned"

        with database.get_db() as con:
            # GROUND CONTROL for the row half, and it was missing.
            #
            # The assertion at the end counts `running` rows by CHAT, while
            # the DELETE it is measuring keys on WORK KEY. With the running
            # row never written at all, the final `left == 0` passed - a
            # write-side defect the test was blind to. This says the row is
            # there first, so `left == 0` afterwards can only mean it was
            # removed.
            assert con.execute(
                "SELECT COUNT(*) FROM notebook_extractions "
                "WHERE chat_id = ? AND status = 'running'",
                (chat_id,)).fetchone()[0] == 1, (
                "ground: the preamble wrote the trace this undo removes")

        with database.get_db() as con:
            assert notebook.spend_today(con)["calls"] == before + 1, (
                "ground: the preamble really did claim a call, and the undo "
                "has not given it back yet - so there IS something to wait "
                "for")

        await notebook_worker._await_settle()

        with database.get_db() as con:
            assert notebook.spend_today(con)["calls"] == before, (
                "the stand-down returned while the undo was still writing; "
                "the next line of lock_vault_now clears the key")
            left = con.execute(
                "SELECT COUNT(*) FROM notebook_extractions "
                "WHERE chat_id = ? AND status = 'running'",
                (chat_id,)).fetchone()[0]
        assert left == 0, "and the running row is gone by then too"

    @pytest.mark.anyio
    async def test_with_no_undo_in_flight_the_stand_down_is_still_immediate(
            self) -> None:
        """GROUND CONTROL. Tracking one more task must not turn an idle lock
        into a five-second one."""
        notebook_worker.worker._inflight.clear()
        notebook_worker.worker.settling = None
        started = time.monotonic()
        await notebook_worker._await_settle()
        assert time.monotonic() - started < 0.5


class TestATaskFromADeadLoopCannotSilenceALiveOne:
    """One leftover from a closed loop made the whole grace period a no-op.

    `worker` is a module-level singleton and `_inflight` is a plain set.
    `_track` discards on done - success, failure and cancellation alike - so
    within one live loop the set cannot accumulate. A task still PENDING when
    its loop closes is never discarded, and it stays in the set for the life
    of the process.

    `asyncio.gather` refuses a future from another loop, synchronously. The
    broad handler around the wait turned that into one log line reading "the
    in-flight extraction did not settle cleanly" - and returned. So a real,
    already-paid write sitting in the same snapshot was never waited for at
    all. The sentence blamed the settle; nobody had waited for it.

    Every process that locks the vault, restarts the worker and locks again
    is a candidate, and TestClient-per-test is one loop per test.
    """

    @staticmethod
    def _a_task_from_a_loop_that_is_gone() -> "asyncio.Task":
        dead = asyncio.new_event_loop()

        async def never_finishes():
            await asyncio.sleep(3600)

        coro = never_finishes()
        task = dead.create_task(coro)
        # Cancelled but NOT awaited, and the difference is the whole test.
        # The cancel only requests; the task stays pending because its loop
        # will never run again, and `_await_settle` filters on
        # `not t.done()`. Awaiting the cancellation here - the obvious
        # tidy-up - would make it done, drop it at that filter, and quietly
        # void all three of these tests.
        task.cancel()
        dead.close()
        coro.close()
        assert not task.done(), (
            "the leftover has to still be PENDING; a done task is filtered "
            "out before it can reach the defect these tests reproduce")
        return task

    @pytest.mark.anyio
    async def test_a_live_write_is_still_waited_for(self, clean_worker) -> None:
        w = clean_worker
        landed: list[str] = []

        async def a_paid_write():
            await asyncio.sleep(0.2)
            landed.append("written")

        stale = self._a_task_from_a_loop_that_is_gone()
        w._inflight.add(stale)
        assert stale in w._inflight, "ground: the leftover is in the snapshot"

        live = asyncio.get_running_loop().create_task(a_paid_write())
        w._track(live)

        await notebook_worker._await_settle()

        assert landed == ["written"], (
            "a task from a closed loop made gather raise, the broad handler "
            "logged it, and the live write was abandoned mid-flight with the "
            "key about to be cleared")
        assert stale not in w._inflight, (
            "and the leftover is dropped on the way past, so it cannot do "
            "this again on the next lock")
        await live

    @pytest.mark.anyio
    async def test_the_settle_pointer_from_a_dead_loop_is_ignored_too(
            self, clean_worker) -> None:
        """`settling` is the same kind of state and is read the same way.

        `_await_settle` adds it to the snapshot separately from the set, so
        filtering only the set would leave the identical hole one line down.
        """
        w = clean_worker
        landed: list[str] = []

        async def a_paid_write():
            await asyncio.sleep(0.2)
            landed.append("written")

        w.settling = self._a_task_from_a_loop_that_is_gone()
        live = asyncio.get_running_loop().create_task(a_paid_write())
        w._track(live)

        await notebook_worker._await_settle()

        assert landed == ["written"], (
            "a settle pointer left by a closed loop made gather raise, and "
            "the live write in the same snapshot was abandoned")
        await live

    @pytest.mark.anyio
    async def test_start_clears_what_the_last_loop_left(self) -> None:
        """POSITIVE CONTROL for the second half of the fix.

        The queue was already rebuilt in `start()` for exactly this reason.
        `_inflight` and `settling` are the same kind of loop-bound state and
        were not, so the leftover survived until something happened to call
        the stand-down - which on an idle machine may be never.
        """
        w = notebook_worker.worker
        stale = self._a_task_from_a_loop_that_is_gone()
        w._inflight = {stale}
        w.settling = stale
        w.task = None

        task = notebook_worker.start()
        try:
            assert stale not in w._inflight, (
                "start() rebuilds the queue for this reason and left _inflight "
                "holding a task from the loop that just closed")
            assert w.settling is None, (
                "the settle POINTER is the same kind of loop-bound state and "
                "is read one line apart from the set")
        finally:
            task.cancel()
            await asyncio.wait({task})
            w.task = None


class TestTheStandDownDoesNotWaitOnAnotherLoop:
    """A vault lock that never returns, from the fix for one that swallowed
    its own cancellation.

    `quiesce()` used to be `await task` inside `except BaseException: pass`.
    That swallowed a cancellation aimed at the caller, which is why it was
    replaced with `asyncio.wait({task})` - and the replacement removed a
    guard nobody knew was load-bearing. `await task` raises RuntimeError
    AT ONCE when the task belongs to a different loop, and the broad handler
    swallowed that, so the lock carried on. `asyncio.wait` has no such
    guard: it resolves a future owned by THIS loop from the other loop's
    thread, and this loop is never woken.

    Measured on the real code: the worker task reaches CANCELLED and the
    wait has still not returned seventy-five seconds later. `lock_vault_now`
    is the single funnel both the Lock button and the idle watchdog come
    through, so a stand-down that never returns is a vault that never locks.
    """

    @staticmethod
    def _a_task_on_another_loop() -> "asyncio.Task":
        """A live task, on a live loop, in another thread.

        Not a closed loop - that is the other defect. This loop is running
        and its task is real, which is the arrangement `lock_vault_now`
        finds whenever the worker was started on a different loop from the
        one the lock is requested on.
        """
        import threading

        box: dict = {}
        ready = threading.Event()

        def run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            async def forever():
                await asyncio.sleep(3600)

            async def main():
                box["task"] = loop.create_task(forever())
                ready.set()
                try:
                    await box["task"]
                except asyncio.CancelledError:
                    pass

            loop.run_until_complete(main())
            loop.close()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        ready.wait(5)
        box["thread"] = thread
        return box

    @pytest.mark.anyio
    async def test_it_returns_instead_of_hanging(self) -> None:
        w = notebook_worker.worker
        box = self._a_task_on_another_loop()
        w.task = box["task"]
        assert w.task.get_loop() is not asyncio.get_running_loop(), (
            "ground: the task really does belong to another loop")

        # Two seconds against a hang that was measured past seventy-five.
        started = time.monotonic()
        await asyncio.wait_for(notebook_worker.quiesce(), timeout=5)
        assert time.monotonic() - started < 2, (
            "the stand-down waited on a task this loop cannot be woken by")
        box["thread"].join(timeout=5)

    @pytest.mark.anyio
    async def test_stop_returns_too(self) -> None:
        """The identical line, and it escaped only because no test called
        `stop()` across loops. Fixed in the same change rather than left for
        the day one does."""
        w = notebook_worker.worker
        box = self._a_task_on_another_loop()
        w.task = box["task"]

        await asyncio.wait_for(notebook_worker.stop(), timeout=5)
        box["thread"].join(timeout=5)

    @pytest.mark.anyio
    async def test_a_task_on_OUR_loop_is_still_waited_for(self) -> None:
        """GROUND CONTROL, and it is the whole point.

        The guard must not turn the wait off. A stand-down that stopped
        waiting for its own loop's worker would pass the two tests above and
        re-open Defect 1 - a paid reply discarded mid-write because the key
        was cleared out from under the thread writing it.
        """
        w = notebook_worker.worker
        landed: list[str] = []

        async def a_worker_that_cleans_up():
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                landed.append("cleaned up")
                raise

        w.task = asyncio.get_running_loop().create_task(
            a_worker_that_cleans_up())
        await asyncio.sleep(0)

        await asyncio.wait_for(notebook_worker.quiesce(), timeout=5)

        assert landed == ["cleaned up"], (
            "the stand-down returned before its own worker had unwound")
