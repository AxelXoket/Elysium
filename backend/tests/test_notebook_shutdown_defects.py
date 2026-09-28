"""Four defects from the FAZ 5 shutdown-path audit, plus the mutant that
`extraction_stats` let through.

  1. `quiesce()` cancelled the loop task and moved on. Measured: a bare
     `asyncio.Task.cancel()` unwinds the awaiting coroutine at once even
     through `anyio.to_thread.run_sync(abandon_on_cancel=False)` - that flag
     does NOT protect the await from an external cancel - so a reply that had
     already been sent, generated and BILLED could still be mid-write when
     `routers/vault.py` cleared the vault key on the very next line, and the
     write died with `VaultLockedError` in a thread nobody was watching.

  2. `work_key` names a RANGE, not an ATTEMPT, and is deterministic - a
     re-plan of the identical range reuses the same key. A stale reply from
     an abandoned attempt could therefore find a RETRY's row and overwrite it
     as `done`, and the retry's own freshly billed reply then arrived and was
     discarded as a duplicate.

  3. `forget_proposals_from_messages` deletes extraction rows above an edited
     message ON PURPOSE, to roll the cursor back. That can empty the table
     for a chat that has in fact been read many times, and `_plan_work`'s
     upgrading-user branch could not tell that apart from a chat that had
     genuinely never been looked at - so it jumped to the present and
     abandoned everything before it, forever.

  4. `commit_extraction`'s require_trace branch wrote `plan_invalidated` for
     any reply that arrived after its running row vanished, whether that was
     an edit (the range still needs reading) or a cleared chat (there is
     nothing left to read).

Defect 1 is driven end to end: the REAL worker loop, a fake provider, a real
`asyncio.Task.cancel()` through the REAL `quiesce()`, provider calls counted,
cost asserted. It uses the `db` fixture rather than `client`: TestClient runs
its app's lifespan (which starts the worker) on a separate portal thread, and
driving that same task's cancellation from a different event loop fails
outright - so this file builds its own chat with plain SQL and starts the
worker on the SAME loop the test runs on.

Defects 2-4 are driven at the layer whose contract they broke -
`commit_extraction` and `_plan_work` - which is where each one actually
lives, plus one end-to-end check that the real worker wires what the guard
needs.
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

import config
import notebook_store as notebook
import notebook_worker
import vault_state
from database import get_db

from tests.test_notebook_worker import fact

TEST_VAULT_KEY = bytes(range(32))
#: How early in its grace a read still open may be cut. The stand-down takes
#: its unwind headroom out of the grace, so the cut lands a little before
#: the deadline; half a second is room for that headroom and a loaded
#: machine, and far short of a read cut a second into its wait.
CUT_NO_EARLIER_THAN_S = 0.5


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _seed(count: int = 30) -> int:
    """A chat with `count` messages, built with no HTTP app in the loop."""
    with get_db() as con:
        con.execute("INSERT INTO characters (name) VALUES ('t')")
        char_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        con.execute("INSERT INTO chats (character_id) VALUES (?)", (char_id,))
        chat_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        for i in range(count):
            con.execute(
                "INSERT INTO messages (chat_id, role, content, active) "
                "VALUES (?,?,?,1)",
                (chat_id, "user" if i % 2 == 0 else "assistant",
                 f"line {i}: her brother owns the mill"))
    return chat_id


def worker() -> notebook_worker.Worker:
    w = notebook_worker.Worker()
    w.queue = asyncio.Queue(maxsize=8)
    return w


class TestDefect1APaidReplySurvivesQuiesce:
    """`quiesce()` must let an already-billed reply settle, or record its own
    failure with the cost attached, before its caller's next line (in
    routers/vault.py: `vault_state.clear_key()`) can race it."""

    @pytest.mark.anyio
    async def test_the_reply_settles_and_its_cost_is_not_lost(
            self, db, monkeypatch) -> None:
        import database
        import openrouter

        chat_id = _seed(30)
        database.set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")

        provider_calls: list[int] = []
        reply_sent = asyncio.Event()

        async def slow_reply(*a, **kw):
            provider_calls.append(1)
            await asyncio.sleep(0.02)
            reply_sent.set()
            return {"id": "gen-defect1", "choices": [{
                "finish_reason": "stop",
                "message": {"content": json.dumps({"facts": [fact()]})}}],
                "usage": {"tokens_in": 10, "tokens_out": 5, "cost": 0.0009}}

        monkeypatch.setattr(openrouter, "complete", slow_reply)

        # Widens the window between "the reply arrived" and "the write
        # lands", so the test reliably cancels while `_write` is mid-flight
        # instead of racing real disk speed.
        real_write = notebook_worker._write

        def slow_write(*a, **kw):
            time.sleep(0.1)
            return real_write(*a, **kw)

        monkeypatch.setattr(notebook_worker, "_write", slow_write)

        assert notebook_worker.worker.task is None, (
            "ground: nothing left running from a previous test")
        notebook_worker.start()
        try:
            notebook_worker.worker.offer(chat_id)
            await reply_sent.wait()
            await asyncio.sleep(0.01)   # let the write thread actually start

            # Exactly what routers/vault.py's lock_vault_now does: quiesce()
            # the worker, THEN clear the key.
            await notebook_worker.quiesce()
            vault_state.clear_key()
            vault_state.set_key(TEST_VAULT_KEY)

            assert provider_calls == [1], "the provider must be billed once"
            with get_db() as con:
                spend = notebook.spend_today(con)
                rows = con.execute(
                    "SELECT status, cost FROM notebook_extractions "
                    "WHERE chat_id = ?", (chat_id,)).fetchall()
            assert spend["calls"] == 1
            assert spend["cost"] == pytest.approx(0.0009), (
                "the paid reply's cost never landed - Defect 1")
            assert [r[0] for r in rows] == ["done"], (
                "a reply already billed and received was thrown away")
            assert notebook.list_entries(chat_id), (
                "the notes from an already-billed reply were discarded")
        finally:
            await notebook_worker.stop()

    @pytest.mark.anyio
    async def test_quiescing_with_nothing_settling_is_a_no_op(
            self, db, monkeypatch) -> None:
        """Positive control: when nothing is mid-write, quiesce() must not
        block waiting for a settle task that does not exist."""
        import database
        import openrouter

        chat_id = _seed(30)
        database.set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")

        async def good(*a, **kw):
            return {"id": "gen", "choices": [{
                "finish_reason": "stop",
                "message": {"content": json.dumps({"facts": []})}}],
                "usage": {}}

        monkeypatch.setattr(openrouter, "complete", good)

        notebook_worker.start()
        try:
            notebook_worker.worker.offer(chat_id)
            # WAITS FOR THE RUN, and says so if it never comes.
            #
            # This loop used to break on the `done` row alone and then assert
            # that nothing was settling - and it passed for the wrong reason:
            # with no API key seeded in the `db` fixture the worker never
            # reached the provider at all, so `done` never appeared, the loop
            # ran its half second out, and "nothing is settling" was true
            # because nothing had ever started. The positive control was
            # measuring an idle worker.
            #
            # Now the fixture seeds the key, the run really happens, and the
            # wait is for the settle TASK rather than for the row it writes
            # (the row lands inside that task, so the row can exist while the
            # task is still finishing). The `else` is the vacuity guard: if
            # the run never completes, that is a failure, not a pass.
            for _ in range(200):
                if (notebook_worker.worker.settling is None
                        or notebook_worker.worker.settling.done()):
                    with get_db() as con:
                        if notebook.extraction_stats(con, chat_id)["done"]:
                            break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("the run never finished")
            assert (notebook_worker.worker.settling is None
                    or notebook_worker.worker.settling.done()), (
                "ground: the run finished, nothing should still be settling")

            started = time.monotonic()
            await notebook_worker.quiesce()
            elapsed = time.monotonic() - started
            assert elapsed < 1.0, "quiesce() blocked with nothing to settle"
        finally:
            await notebook_worker.stop()

    @pytest.mark.anyio
    async def test_quiesce_does_not_wait_past_its_own_grace_period(
            self, db, monkeypatch) -> None:
        """A stuck write must not hang the vault lock forever - SETTLE_GRACE_S
        is a bound, not a promise every write completes."""
        import database
        import openrouter

        chat_id = _seed(30)
        database.set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")
        monkeypatch.setattr(notebook_worker, "SETTLE_GRACE_S", 0.05)

        reply_sent = asyncio.Event()

        async def slow_reply(*a, **kw):
            await asyncio.sleep(0.01)
            reply_sent.set()
            return {"id": "gen", "choices": [{
                "finish_reason": "stop",
                "message": {"content": json.dumps({"facts": []})}}],
                "usage": {}}

        monkeypatch.setattr(openrouter, "complete", slow_reply)

        real_write = notebook_worker._write

        def stuck_then_write(*a, **kw):
            time.sleep(0.3)
            return real_write(*a, **kw)

        monkeypatch.setattr(notebook_worker, "_write", stuck_then_write)

        notebook_worker.start()
        try:
            notebook_worker.worker.offer(chat_id)
            await reply_sent.wait()
            await asyncio.sleep(0.01)

            started = time.monotonic()
            await notebook_worker.quiesce()
            elapsed = time.monotonic() - started
            assert elapsed < 1.0, (
                "quiesce() waited past its own grace period for a stuck "
                "write")
        finally:
            # Let the stuck write actually land before the DB is torn down,
            # so the test process is not racing its own teardown.
            await asyncio.sleep(0.5)
            await notebook_worker.stop()

    @pytest.mark.anyio
    async def test_quiesce_does_not_report_the_loop_as_died(
            self, db, monkeypatch) -> None:
        """A related latent bug fixed alongside Defect 1: `quiesce()` used to
        null `worker.queue` BEFORE awaiting the cancelled loop task, so
        `run()`'s own `finally: self.queue.task_done()` read it as None and
        raised AttributeError - which REPLACES the CancelledError, so a clean
        cancellation was reported to `_note_death` as a crashed loop."""
        import database
        import openrouter

        chat_id = _seed(30)
        database.set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")

        async def hangs(*a, **kw):
            await asyncio.sleep(10)

        monkeypatch.setattr(openrouter, "complete", hangs)

        notebook_worker.start()
        try:
            notebook_worker.worker.offer(chat_id)
            for _ in range(50):
                await asyncio.sleep(0.01)
                if notebook_worker.worker.queue is not None and (
                        notebook_worker.worker.queue.empty()):
                    break
            await notebook_worker.quiesce()
            assert notebook_worker.worker.died is None, (
                "a clean cancellation was reported as a crashed loop")
        finally:
            await notebook_worker.stop()


class TestDecision34aANormalReadGetsUpToFiveSeconds:
    """Decision 34 a.

    A lock used to cancel the ordinary reader's loop and, with it, a read
    whose request was already out: the range was counted read, paid for,
    and never produced a note. A reading job's unit had up to five seconds;
    this reader had none. Now the ordinary read runs as its own shielded
    task, the lock waits for it for what is left of the same up to five
    seconds (the read is cancelled `READ_CANCEL_UNWIND_S` before the end,
    so the lock still returns inside them), and a read still open then is
    CANCELLED by name - its request closes, the range is closed out as
    abandoned, and it is not bought twice.

    Driven through the real loop and the real `quiesce()`, on the test's
    own event loop, like Defect 1 above. The grace is the shipped
    `SETTLE_GRACE_S`, imported, not a shortened copy.
    """

    @staticmethod
    def _provider(monkeypatch, *, answer_after: float | None):
        """`answer_after` seconds after the call arrives the reply comes
        back; None means never. Records the call and whether it was
        cancelled while open."""
        import openrouter

        state = {"calls": 0, "cancelled": False,
                 "entered": asyncio.Event()}

        async def reply(*a, **kw):
            state["calls"] += 1
            state["entered"].set()
            try:
                if answer_after is None:
                    await asyncio.Event().wait()
                await asyncio.sleep(answer_after)
            except asyncio.CancelledError:
                state["cancelled"] = True
                raise
            return {"id": "gen-34a", "choices": [{
                "finish_reason": "stop",
                "message": {"content": json.dumps({"facts": [fact()]})}}],
                "usage": {"tokens_in": 10, "tokens_out": 5, "cost": 0.0001}}

        monkeypatch.setattr(openrouter, "complete", reply)
        return state

    @staticmethod
    def _rows(chat_id: int) -> list[tuple]:
        with get_db() as con:
            return [tuple(r) for r in con.execute(
                "SELECT status, error_type FROM notebook_extractions "
                "WHERE chat_id = ?", (chat_id,)).fetchall()]

    @pytest.mark.anyio
    async def test_GROUND_the_same_read_without_a_lock_is_written(
            self, db, monkeypatch) -> None:
        import database

        chat_id = _seed(30)
        database.set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")
        state = self._provider(monkeypatch, answer_after=1.0)

        notebook_worker.start()
        try:
            notebook_worker.worker.offer(chat_id)
            for _ in range(400):
                if [r[0] for r in self._rows(chat_id)] == ["done"]:
                    break
                await asyncio.sleep(0.01)
            assert [r[0] for r in self._rows(chat_id)] == ["done"]
            assert notebook.list_entries(chat_id)
            assert state["calls"] == 1 and not state["cancelled"]
        finally:
            await notebook_worker.stop()

    @pytest.mark.anyio
    async def test_a_normal_read_that_answers_inside_the_locks_grace_is_written(
            self, db, monkeypatch) -> None:
        import database

        chat_id = _seed(30)
        database.set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")
        state = self._provider(monkeypatch, answer_after=1.0)

        notebook_worker.start()
        try:
            notebook_worker.worker.offer(chat_id)
            await asyncio.wait_for(state["entered"].wait(), 5)

            started = time.monotonic()
            await notebook_worker.quiesce()
            elapsed = time.monotonic() - started

            assert [r[0] for r in self._rows(chat_id)] == ["done"], (
                "a read that answered one second into the lock was not "
                "written")
            assert notebook.list_entries(chat_id), "and its note is missing"
            assert state["calls"] == 1 and not state["cancelled"]
            assert elapsed < notebook_worker.SETTLE_GRACE_S, (
                f"the lock waited {elapsed:.2f}s for a read that answered "
                "in one")
        finally:
            await notebook_worker.stop()

    @pytest.mark.anyio
    async def test_a_normal_read_still_open_when_the_grace_runs_out_is_cancelled_and_not_paid_twice(
            self, db, monkeypatch) -> None:
        import database

        chat_id = _seed(30)
        database.set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")
        state = self._provider(monkeypatch, answer_after=None)

        notebook_worker.start()
        try:
            notebook_worker.worker.offer(chat_id)
            await asyncio.wait_for(state["entered"].wait(), 5)
            unit = notebook_worker.worker._reading
            assert unit is not None and not unit.done(), (
                "ground: the read is open as the reader's own task")

            started = time.monotonic()
            await notebook_worker.quiesce()
            elapsed = time.monotonic() - started

            # INSIDE the one grace, not after it: the read is cancelled
            # `READ_CANCEL_UNWIND_S` before the deadline precisely so its
            # unwind does not push the lock past the up to five seconds the
            # documents state. Cancelling at the deadline itself would
            # return just after it, and this bound goes red.
            #
            # AND NOT EARLY: the read gets (up to) the grace, not a slice of
            # it. The lower bound is on the grace itself, not on the grace
            # less READ_CANCEL_UNWIND_S, so a headroom grown large enough to
            # cut a read a second into its wait goes red here.
            grace = notebook_worker.SETTLE_GRACE_S
            assert grace - CUT_NO_EARLIER_THAN_S <= elapsed < grace, (
                f"the lock returned after {elapsed:.2f}s, not inside the "
                f"last {CUT_NO_EARLIER_THAN_S}s of its grace")
            assert unit.cancelled(), (
                "the open read was left running after the grace ran out")
            assert state["cancelled"], "and its request was never closed"
            assert notebook.list_entries(chat_id) == []

            # NOT PAID TWICE: the next offer closes the range out as
            # abandoned and does not send it again. Ten messages are left
            # after the abandoned twenty, under the threshold, so no call.
            notebook_worker.worker.offer(chat_id)
            for _ in range(200):
                if notebook_worker.worker.queue.empty() and (
                        notebook_worker.worker._reading is None):
                    break
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.05)
            assert state["calls"] == 1, "the abandoned range was sent again"
            assert self._rows(chat_id) == [
                ("failed", notebook.ABANDONED_IN_FLIGHT)], (
                "POSITIVE CONTROL: the range is recorded as abandoned in "
                "flight, which is what keeps it from being bought twice")
        finally:
            await notebook_worker.stop()

    @pytest.mark.anyio
    async def test_a_lock_torn_down_mid_grace_leaves_the_reader_running(
            self, db, monkeypatch) -> None:
        """A lock request cancelled while it waits out the grace (the
        request torn down mid-flight) used to leave the notebook dead:
        `quiesce()` had already taken the loop task and cancelled it, and
        the line that starts it again was never reached. The queue stayed
        live with nobody draining it and the open read stayed on the wire.
        The cancel still travels to the caller; the loop is started again
        and the read is cancelled as the end of the grace would have."""
        import database

        chat_id = _seed(30)
        database.set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")
        state = self._provider(monkeypatch, answer_after=None)

        notebook_worker.start()
        try:
            notebook_worker.worker.offer(chat_id)
            await asyncio.wait_for(state["entered"].wait(), 5)
            lock = asyncio.create_task(notebook_worker.quiesce())
            await asyncio.sleep(1.0)
            assert not lock.done(), "ground: the lock is inside its grace"

            lock.cancel()
            (outcome,) = await asyncio.gather(lock, return_exceptions=True)
            assert isinstance(outcome, asyncio.CancelledError), (
                "the cancel aimed at the lock was swallowed")

            w = notebook_worker.worker
            assert w.task is not None and not w.task.done(), (
                "the torn-down lock left the reader loop dead")
            await asyncio.sleep(0.05)
            assert state["cancelled"], "and the open read's request open"

            # POSITIVE CONTROL: the restarted loop really drains its queue.
            w.offer(chat_id)
            for _ in range(200):
                if w.queue.empty() and w._reading is None:
                    break
                await asyncio.sleep(0.01)
            assert w.queue.empty(), "the restarted loop took nothing"
        finally:
            await notebook_worker.stop()

    @pytest.mark.anyio
    async def test_the_cancel_reaches_the_normal_read_and_nothing_else(
            self, db) -> None:
        """The control decision 34 a needs. A reading job's unit and a paid
        reply's settle, open at the same moment, are separate tasks that
        the stand-down neither waits on nor cancels here; only the reader's
        own unit is cancelled when its grace runs out."""
        loop = asyncio.get_running_loop()
        w = notebook_worker.worker
        hung_read = loop.create_task(asyncio.Event().wait())
        job_unit = loop.create_task(asyncio.Event().wait())
        settle = loop.create_task(asyncio.Event().wait())
        w._reading = hung_read
        w._track(job_unit)
        w.settling = settle
        try:
            await notebook_worker._stand_down_reading(loop.time() + 0.5)
            await asyncio.sleep(0)
            assert hung_read.cancelled(), "ground: the read was cancelled"
            assert not job_unit.done(), "a job's unit was cancelled with it"
            assert not settle.done(), "a paid settle was cancelled with it"
        finally:
            for t in (hung_read, job_unit, settle):
                t.cancel()
            await asyncio.gather(hung_read, job_unit, settle,
                                 return_exceptions=True)
            w._reading = None
            w.settling = None

    @pytest.mark.anyio
    async def test_closing_the_window_still_cuts_the_read_at_once(
            self, db, monkeypatch) -> None:
        """`stop()` is unchanged by 34 a: the up to five seconds are the lock's,
        and closing the app cuts an open read immediately, as before."""
        import database

        chat_id = _seed(30)
        database.set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")
        state = self._provider(monkeypatch, answer_after=None)

        notebook_worker.start()
        notebook_worker.worker.offer(chat_id)
        await asyncio.wait_for(state["entered"].wait(), 5)
        unit = notebook_worker.worker._reading

        started = time.monotonic()
        await notebook_worker.stop()
        elapsed = time.monotonic() - started

        assert unit.cancelled() and state["cancelled"]
        assert elapsed < 1.0, f"stop() waited {elapsed:.2f}s for an open read"


class TestDefect2AttemptOwnership:
    """work_key names the RANGE, not the ATTEMPT - a re-plan of the identical
    range reuses the same key. A settle is only allowed to write a FINAL
    outcome (done/failed/skipped) if it still owns the row; a fresh claim
    (status='running') is always allowed to reclaim it - that is the retry
    mechanism working as intended."""

    def test_a_stale_settle_cannot_overwrite_the_retrys_row(self, db) -> None:
        chat_id = _seed(4)
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            # Attempt A claims the row.
            notebook.commit_extraction(
                con, work_key="k", chat_id=chat_id, from_id=1, to_id=4,
                status="running", attempt_token="A")
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            # A is abandoned; the SAME range is re-planned as attempt B,
            # which reclaims the row - a claim always may.
            notebook.commit_extraction(
                con, work_key="k", chat_id=chat_id, from_id=1, to_id=4,
                status="running", attempt_token="B")
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            # A's reply finally arrives, stale, and tries to settle.
            out = notebook.commit_extraction(
                con, work_key="k", chat_id=chat_id, from_id=1, to_id=4,
                proposals=[fact(text="A's stale claim.")],
                usage={"cost": 0.0001}, attempt_token="A")

        assert out["written"] == 0, "a stale attempt overwrote the retry's row"
        with get_db() as con:
            status = con.execute(
                "SELECT status FROM notebook_extractions WHERE work_key = 'k'"
            ).fetchone()[0]
        assert status == "running", "B's row was clobbered by A's stale reply"
        assert notebook.list_entries(chat_id) == [], (
            "notes from a stale, superseded attempt were written")

    def test_the_owning_attempt_settles_normally(self, db) -> None:
        """Positive control: the guard above must not block B's OWN, genuine
        settle of the row it actually claimed."""
        chat_id = _seed(4)
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.commit_extraction(
                con, work_key="k2", chat_id=chat_id, from_id=1, to_id=4,
                status="running", attempt_token="B")
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            out = notebook.commit_extraction(
                con, work_key="k2", chat_id=chat_id, from_id=1, to_id=4,
                proposals=[fact()], usage={"cost": 0.0001}, attempt_token="B")

        assert out["written"] == 1
        with get_db() as con:
            assert con.execute(
                "SELECT status FROM notebook_extractions WHERE work_key = 'k2'"
            ).fetchone()[0] == "done"

    def test_a_stale_settles_money_is_still_recorded(self, db) -> None:
        """It was still sent, generated and billed - only the WORDS are
        refused, never the spend entry."""
        chat_id = _seed(4)
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.commit_extraction(
                con, work_key="k3", chat_id=chat_id, from_id=1, to_id=4,
                status="running", attempt_token="A")
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.commit_extraction(
                con, work_key="k3", chat_id=chat_id, from_id=1, to_id=4,
                status="running", attempt_token="B")
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.commit_extraction(
                con, work_key="k3", chat_id=chat_id, from_id=1, to_id=4,
                proposals=[fact()], usage={"cost": 0.0005}, attempt_token="A")

        with get_db() as con:
            assert notebook.spend_today(con)["cost"] == pytest.approx(0.0005)

    def test_callers_with_no_token_are_unaffected(self, db) -> None:
        """Ground: every non-worker caller never passes a token, and the
        ownership check must stay dormant for them - this is the same retry-
        keeps-its-notes behaviour test_notebook_worker_hardening.py already
        relies on."""
        chat_id = _seed(4)
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.commit_extraction(
                con, work_key="k4", chat_id=chat_id, from_id=1, to_id=4,
                status="failed", error_type="timeout")
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            out = notebook.commit_extraction(
                con, work_key="k4", chat_id=chat_id, from_id=1, to_id=4,
                proposals=[fact()], usage={"cost": 0.0001})

        assert out["duplicate"] is False and out["written"] == 1

    @pytest.mark.anyio
    async def test_the_real_worker_wires_a_fresh_token_per_attempt(
            self, db, monkeypatch) -> None:
        """Ground for the WIRING, not just the guard: `_handle` must
        generate and carry its own token through every write it makes, or
        the guard above would have nothing to check against."""
        import database
        import openrouter

        chat_id = _seed(30)
        database.set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")

        async def good(*a, **kw):
            return {"id": "gen", "choices": [{
                "finish_reason": "stop",
                "message": {"content": json.dumps({"facts": []})}}],
                "usage": {}}

        monkeypatch.setattr(openrouter, "complete", good)

        # THE SAME QUESTION THE WORKER WILL ASK, and asked THROUGH the worker
        # rather than by retyping its arguments. The threshold decides
        # whether an unread chat is read from its oldest message or from the
        # present, so a different one here names a different range, and a
        # different range is a different work key. This used to pass on a
        # coincidence: the branch was keyed on `limit` rather than on the
        # threshold, so the two calls agreed by accident. Then the threshold
        # itself started shrinking under the breaker while the first-read
        # edge deliberately did not, and a hard-coded pair here would have
        # gone on agreeing with a worker that had stopped asking it.
        w = worker()
        plan = notebook_worker._plan_work(
            chat_id, "vendor/cheap", "en",
            w.trigger_threshold_for(chat_id), w.batch_size_for(chat_id),
            first_read_edge=config.NOTEBOOK_EXTRACT_EVERY_TURNS * 2)
        await w._handle(chat_id)

        with get_db() as con:
            token = con.execute(
                "SELECT attempt_token FROM notebook_extractions "
                "WHERE work_key = ?", (plan["work_key"],)).fetchone()[0]
        assert token, "the worker's own call left no attempt identity behind"


class TestDefect3RolledBackCursorIsNotReadAsFresh:
    """`forget_proposals_from_messages` deletes extraction rows above an
    edited message ON PURPOSE, to roll the cursor back - and that can empty
    the table for a chat that has in fact been read many times.
    `_plan_work`'s upgrading-user branch must not mistake that silence for
    "this chat has never been read"."""

    def test_a_rolled_back_chat_resumes_from_the_start_not_the_present(
            self, db) -> None:
        chat_id = _seed(61)
        with get_db() as con:
            ids = [r[0] for r in con.execute(
                "SELECT id FROM messages WHERE chat_id = ? ORDER BY id",
                (chat_id,)).fetchall()]
        # Three completed ranges, as the defect's own narrative describes.
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            for i, to_id in enumerate((ids[19], ids[39], ids[59])):
                notebook.commit_extraction(
                    con, work_key=f"r{i}", chat_id=chat_id,
                    from_id=1, to_id=to_id, proposals=[])
        # The user edits message 5: the app rolls the cursor back on purpose.
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.forget_proposals_from_messages(con, [ids[4]])
        with get_db() as con:
            last = con.execute(
                "SELECT COALESCE(MAX(to_message_id), 0) FROM "
                "notebook_extractions WHERE chat_id = ?", (chat_id,)
            ).fetchone()[0]
        assert last == 0, "ground: the rollback really did empty the table"

        plan = notebook_worker._plan_work(chat_id, "vendor/cheap", "en", 1, 20)

        assert plan is not None
        assert plan["from_id"] == ids[0], (
            "the rolled-back chat was read as a fresh one and jumped to the "
            "present, abandoning messages 1..41 including the edited one")

    def test_a_chat_that_was_genuinely_never_read_still_jumps_to_the_present(
            self, db) -> None:
        """Positive control: the branch this defect protects must still fire
        for the case it exists for - the upgrading user with a real backlog
        and no extraction history at all."""
        chat_id = _seed(61)

        plan = notebook_worker._plan_work(chat_id, "vendor/cheap", "en", 1, 20)

        assert plan is not None
        with get_db() as con:
            newest = con.execute(
                "SELECT MAX(id) FROM messages WHERE chat_id = ?",
                (chat_id,)).fetchone()[0]
        assert plan["to_id"] == newest, (
            "a chat with no extraction history at all did not jump to the "
            "present")

    def test_the_flag_is_set_by_a_REAL_extraction_and_survives_a_rollback(
            self, db) -> None:
        """NARROWED. It used to say "by any extraction", and that promise was
        wrong in the one direction that mattered: a SKIPPED attempt set the
        flag too, having read nothing and moved no cursor - which then told
        the planner this chat had been read before and sent it back to its
        oldest message. What this test measures, and always measured, is a
        real extraction. The skipped case is in test_notebook_sweep.py.
        """
        chat_id = _seed(4)
        with get_db() as con:
            flag = con.execute(
                "SELECT notebook_extracted_ever FROM chats WHERE id = ?",
                (chat_id,)).fetchone()[0]
        assert flag == 0, "ground: a fresh chat starts unmarked"

        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.commit_extraction(
                con, work_key="f1", chat_id=chat_id, from_id=1, to_id=4,
                proposals=[])
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.forget_proposals_from_messages(con, [1])
            flag = con.execute(
                "SELECT notebook_extracted_ever FROM chats WHERE id = ?",
                (chat_id,)).fetchone()[0]
        assert flag == 1, "the flag was cleared by the rollback"


class TestDefect4ClearedRangeIsNotPlanInvalidated:
    """`plan_invalidated`'s own wording says the range genuinely still needs
    reading. A CLEARED chat has no range left to read at all - the two are
    told apart by whether `to_message_id` still names a row in `messages`:
    an edit leaves it there, `clear_chat` deletes every message first."""

    def test_an_edit_is_still_plan_invalidated(self, db) -> None:
        chat_id = _seed(4)
        # The message still exists - only the running row is gone, exactly
        # as an edit's rollback leaves things.
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            out = notebook.commit_extraction(
                con, work_key="e1", chat_id=chat_id, from_id=1, to_id=4,
                proposals=[fact()], require_trace=True)
            reasons = notebook.extraction_stats(con, chat_id)["skip_reasons"]

        assert out["written"] == 0
        assert reasons.get("plan_invalidated") == 1
        assert "range_cleared" not in reasons

    def test_a_cleared_chat_is_range_cleared_not_plan_invalidated(
            self, db) -> None:
        chat_id = _seed(4)
        with get_db() as con:
            highest = con.execute(
                "SELECT MAX(id) FROM messages WHERE chat_id = ?",
                (chat_id,)).fetchone()[0]
            # Simulates clear_chat: every message in the chat is gone before
            # the reply lands.
            con.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            out = notebook.commit_extraction(
                con, work_key="c1", chat_id=chat_id, from_id=1, to_id=highest,
                proposals=[fact()], require_trace=True)
            reasons = notebook.extraction_stats(con, chat_id)["skip_reasons"]

        assert out["written"] == 0
        assert reasons.get("range_cleared") == 1
        assert "plan_invalidated" not in reasons

    def test_range_cleared_is_declared_like_every_other_reason(self) -> None:
        """Ground: the new reason has to go through the same gate as the
        others - an undeclared skip_reason must never reach the database."""
        assert "range_cleared" in notebook.SKIP_REASONS


class TestExtractionStatsAbandonedIsNotJustFailed:
    """The surviving mutant this audit named: replacing `abandoned`'s COUNT
    with the ordinary failure count leaves every OTHER test in this suite
    green, because none of them puts a non-abandoned failure in the same chat
    as an abandoned one."""

    def test_an_ordinary_failure_does_not_count_as_abandoned(self, db) -> None:
        chat_id = _seed(4)
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.commit_extraction(
                con, work_key="ord", chat_id=chat_id, from_id=1, to_id=4,
                status="failed", error_type="openrouter_error")

        with get_db() as con:
            stats = notebook.extraction_stats(con, chat_id)
        assert stats["failed"] == 1
        assert stats["abandoned"] == 0, (
            "an ordinary provider failure was counted as abandoned")

    def test_an_abandoned_call_counts_as_both(self, db) -> None:
        """Positive control: an abandoned row IS a failed row too - the two
        counters are not exclusive, `abandoned` is a subset of `failed`."""
        chat_id = _seed(4)
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                "INSERT INTO notebook_extractions "
                "(work_key, chat_id, from_message_id, to_message_id, status) "
                "VALUES (?,?,?,?,?)", ("ab", chat_id, 1, 4, "running"))
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.settle_orphaned_running(con, chat_id)

        with get_db() as con:
            stats = notebook.extraction_stats(con, chat_id)
        assert stats["failed"] == 1
        assert stats["abandoned"] == 1

    def test_both_kinds_of_failure_together_are_told_apart(self, db) -> None:
        """The direct kill: two failed rows in the SAME chat, only one of
        them abandoned. `failed` must count both; `abandoned` must count
        only its own - a mutant that aliases the two cannot pass this."""
        chat_id = _seed(4)
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.commit_extraction(
                con, work_key="two-a", chat_id=chat_id, from_id=1, to_id=4,
                status="failed", error_type="openrouter_error")
            con.execute(
                "INSERT INTO notebook_extractions "
                "(work_key, chat_id, from_message_id, to_message_id, status) "
                "VALUES (?,?,?,?,?)", ("two-b", chat_id, 5, 8, "running"))
            notebook.settle_orphaned_running(con, chat_id)

        with get_db() as con:
            stats = notebook.extraction_stats(con, chat_id)
        assert stats["failed"] == 2
        assert stats["abandoned"] == 1
