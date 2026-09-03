"""Five claims about what `commit_extraction` and the spend counters owe.

Each class below is one defect that was reproduced before it was patched, and
each carries the controls that make its red mean something: a ground control
proving the call reached the code under test, and - where the claim is that
something is ABSENT - a positive control proving the same sweep sees that
thing when it IS there.

  1. The "this chat has been read" flag was lit by a `failed` and by a
     `running` row. One network drop then moved a long chat out of the
     start-at-the-present branch permanently.
  2. The migration that INSTALLS that flag lit it from any extraction row at
     all, so the defect above arrived pre-baked on every database that
     upgraded into the column while holding one dropped call - and nothing in
     this app ever clears the flag again.
  3. `spend_today` re-derived its own day, so no test could pin the day
     boundary the ceiling is supposed to reset on.
  4. `commit_extraction` INSERTed into a chat that had been deleted during
     the provider call. The foreign key fired, the whole transaction rolled
     back, and the spend row written moments earlier went with it.
  5. The UPDATE branch overwrote `cost` and the token counts instead of
     adding to them, so a range that took three attempts remembered only the
     last one's price.
"""
from __future__ import annotations

import json

import pytest

import config
import database
import notebook_store as notebook
import notebook_worker
import openrouter
from database import get_db, set_setting

from tests.conftest import make_character, make_chat


@pytest.fixture
def anyio_backend():
    return "asyncio"


def seed(client, count: int = 4) -> int:
    """A chat with `count` real messages, so the foreign keys have targets."""
    chat_id = make_chat(client, make_character(client))
    with get_db() as con:
        for i in range(count):
            con.execute(
                "INSERT INTO messages (chat_id, role, content, active) "
                "VALUES (?,?,?,1)",
                (chat_id, "user" if i % 2 == 0 else "assistant",
                 f"line {i}: her brother owns the mill"))
    return chat_id


def message_ids(chat_id: int) -> list[int]:
    with get_db() as con:
        return [r[0] for r in con.execute(
            "SELECT id FROM messages WHERE chat_id = ? ORDER BY id",
            (chat_id,)).fetchall()]


def settle(chat_id: int, **kw) -> dict:
    """One `commit_extraction` in its own transaction, the way the worker
    calls it - the caller opens the transaction, this function does not."""
    with get_db() as con:
        con.execute("BEGIN IMMEDIATE")
        return notebook.commit_extraction(con, chat_id=chat_id, **kw)


def extraction_row(work_key: str) -> tuple:
    with get_db() as con:
        return con.execute(
            "SELECT status, cost, tokens_in, tokens_out "
            "FROM notebook_extractions WHERE work_key = ?",
            (work_key,)).fetchone()


def ever_flag(chat_id: int) -> int:
    with get_db() as con:
        return con.execute(
            "SELECT notebook_extracted_ever FROM chats WHERE id = ?",
            (chat_id,)).fetchone()[0]


class TestOnlyAReadRangeSaysTheChatHasBeenRead:
    """`notebook_extracted_ever` is what tells the planner a chat is not new.

    The cursor it sits beside counts only `done` rows and the one `failed`
    row that means the call was made and never settled. So a flag lit by an
    ordinary `failed` - a network drop - put the two out of step: the cursor
    still said "nothing read", the flag said "read before", and the branch
    that starts a long chat at its newest messages stopped firing for that
    chat forever. The chat then ground through hundreds of old messages
    twenty at a time, every batch a billed call.
    """

    # `skipped` is deliberately not in this list. It was already excluded
    # before this change and `test_notebook_sweep.py` owns that case; adding
    # it here would be a parameter that cannot go red.
    @pytest.mark.parametrize("status", ["failed", "running"])
    def test_an_attempt_that_read_nothing_leaves_the_flag_alone(
            self, client, status) -> None:
        chat_id = seed(client)
        ids = message_ids(chat_id)
        settle(chat_id, work_key=f"wk-{status}", from_id=ids[0],
               to_id=ids[-1], status=status,
               error_type="ReadTimeout" if status == "failed" else None)
        # GROUND CONTROL: the call really reached commit_extraction and wrote
        # its row. Without this the assertion below would also pass if
        # nothing had happened at all.
        assert extraction_row(f"wk-{status}")[0] == status
        assert ever_flag(chat_id) == 0, (
            f"a {status!r} attempt read nothing and moved no cursor, and it "
            "still told the planner this chat had been read before")

    def test_a_real_extraction_does_light_the_flag(self, client) -> None:
        """POSITIVE CONTROL for the absence asserted above. The sweep can see
        the flag when it is genuinely there, so a zero means the write did
        not happen rather than that nothing can ever set it."""
        chat_id = seed(client)
        ids = message_ids(chat_id)
        settle(chat_id, work_key="wk-done", from_id=ids[0], to_id=ids[-1])
        assert extraction_row("wk-done")[0] == "done"
        assert ever_flag(chat_id) == 1

    def test_a_dropped_first_call_does_not_condemn_a_long_chat(
            self, client) -> None:
        """THE OUTCOME, not the column. The flag is only interesting because
        the planner reads it: a long chat nobody has read yet starts at its
        NEWEST messages, and the flag lit by a dropped connection took that
        branch away for good. Measured here where the user feels it - the
        range the next call would pay for.
        """
        chat_id = seed(client, 200)
        ids = message_ids(chat_id)
        settle(chat_id, work_key="wk-drop", from_id=ids[0], to_id=ids[19],
               status="failed", error_type="ReadTimeout")

        plan = notebook_worker._plan_work(chat_id, "vendor/cheap", "en", 4, 20)

        assert plan is not None
        assert plan["to_id"] == ids[-1], (
            "one dropped connection sent a chat that has never been read "
            "back to its oldest message, twenty at a time")
        assert plan["from_id"] == ids[-20]

    def test_a_chat_that_really_was_read_still_reads_forward(
            self, client) -> None:
        """POSITIVE CONTROL for the flag that stayed. An edit deletes the
        extraction rows on purpose, so the cursor rolls back to nothing - and
        the flag is the only thing left saying this chat HAS been read. If the
        fix had simply stopped setting the flag, this chat would jump to the
        present and silently abandon everything before it.
        """
        chat_id = seed(client, 200)
        ids = message_ids(chat_id)
        settle(chat_id, work_key="wk-real", from_id=ids[0], to_id=ids[19])
        assert ever_flag(chat_id) == 1
        with get_db() as con:
            con.execute("DELETE FROM notebook_extractions WHERE chat_id = ?",
                        (chat_id,))

        plan = notebook_worker._plan_work(chat_id, "vendor/cheap", "en", 4, 20)

        assert plan is not None
        assert plan["from_id"] == ids[0], (
            "a chat that had been read was treated as brand new and jumped "
            "to the present, abandoning every message before it")

    def test_an_abandoned_call_moves_the_cursor_without_the_flag(
            self, client) -> None:
        """The mirror, recorded rather than assumed. A call that was made and
        never settled is counted by the cursor - the money is gone either way
        and re-reading that range would pay for it twice - while the flag
        stays 0 because nothing was actually read. The two disagree here on
        purpose, and it is harmless precisely because the cursor has moved:
        the branch that consults the flag is only reachable when it has not.
        """
        chat_id = seed(client, 200)
        ids = message_ids(chat_id)
        settle(chat_id, work_key="wk-abandoned", from_id=ids[0],
               to_id=ids[19], status="failed",
               error_type=notebook.ABANDONED_IN_FLIGHT)

        assert ever_flag(chat_id) == 0
        plan = notebook_worker._plan_work(chat_id, "vendor/cheap", "en", 4, 20)
        assert plan is not None
        assert plan["from_id"] > ids[19], (
            "the range whose call was already paid for was planned again")


def unmigrate_the_ever_column() -> None:
    """Put this database back in the shape that upgrades INTO the flag.

    `ALTER TABLE ... DROP COLUMN` rather than a hand-written pre-migration
    schema, because the thing under test is the REAL `chats` table this build
    creates minus exactly one column. A copied CREATE statement would drift
    away from it silently and the test would go on passing against a table
    that no longer exists anywhere.

    The assertion is the ground control for every test that calls this: if
    the column were still there, `_migrate`'s guard would skip the ALTER and
    the backfill both, and the flags read afterwards would just be whatever
    `commit_extraction` had already written.
    """
    with get_db() as con:
        con.execute("ALTER TABLE chats DROP COLUMN notebook_extracted_ever")
        cols = {r[1] for r in
                con.execute("PRAGMA table_info(chats)").fetchall()}
    assert "notebook_extracted_ever" not in cols, (
        "the column survived the drop, so the migration under test never ran")


class TestTheMigrationInstallsTheFlagTheWriterWouldHave:
    """The same claim as the class above, one layer down and one-way.

    `commit_extraction` decides this flag for every chat from here on, and it
    can be corrected by shipping a new build. The migration decides it ONCE,
    for the whole history already on disk, and nothing in this app clears the
    flag afterwards - so a migration that is more generous than the writer
    does not merely disagree with it, it hands the disagreement to that
    database permanently. The writer's rule is therefore the reference, and
    "any extraction row at all" was a second, wider rule wearing its name.
    """

    # The abandoned-in-flight row is in this list on purpose and it is the
    # interesting one: the CURSOR counts it, the writer does not, and the
    # migration has to pick a side. It picks the writer's, because the branch
    # that reads this flag is only reachable when the cursor has not moved.
    @pytest.mark.parametrize("status,error_type", [
        ("done", None),
        ("failed", "ReadTimeout"),
        ("failed", notebook.ABANDONED_IN_FLIGHT),
        ("running", None),
        ("skipped", None),
    ])
    def test_a_migrated_chat_and_a_written_one_read_the_same(
            self, client, status, error_type) -> None:
        """One database, two ways of arriving at the same flag.

        The row is settled through the real writer first and its answer
        recorded; the column is then removed and put back by the real
        migration, which re-derives the flag from that same row. A migrated
        install and a fresh install must not be able to disagree about
        whether a chat has been read.
        """
        chat_id = seed(client)
        ids = message_ids(chat_id)
        key = f"mig-{status}-{error_type}"
        settle(chat_id, work_key=key, from_id=ids[0], to_id=ids[-1],
               status=status, error_type=error_type,
               skip_reason="proxy_gate" if status == "skipped" else None)
        written = ever_flag(chat_id)

        unmigrate_the_ever_column()
        database.init_db()

        # GROUND CONTROL: `_migrate` sweeps orphaned extraction rows, so a
        # backfill that found nothing would agree with a writer that wrote
        # nothing, vacuously, for every parameter at once.
        assert extraction_row(key)[0] == status, (
            "the row the backfill was supposed to read is not there")
        assert ever_flag(chat_id) == written, (
            f"a {status!r}/{error_type!r} row reads as "
            f"{ever_flag(chat_id)} after the migration and as {written} "
            "after the writer; the same history means two things depending "
            "on which build first opened the database")

    def test_the_migration_can_light_the_flag_at_all(self, client) -> None:
        """POSITIVE CONTROL for the parity above, which four of its five
        parameters satisfy with 0 == 0. A backfill deleted outright would
        pass every one of them."""
        chat_id = seed(client)
        ids = message_ids(chat_id)
        settle(chat_id, work_key="mig-lights", from_id=ids[0], to_id=ids[-1])

        unmigrate_the_ever_column()
        database.init_db()

        assert ever_flag(chat_id) == 1

    def test_a_dropped_call_found_on_disk_is_not_a_reading(
            self, client) -> None:
        """The defect itself, stated as the flag. A database carrying one
        `failed` row from a dropped connection used to come out of this
        migration claiming the chat had been read."""
        chat_id = seed(client)
        ids = message_ids(chat_id)
        settle(chat_id, work_key="mig-drop", from_id=ids[0], to_id=ids[-1],
               status="failed", error_type="ReadTimeout")

        unmigrate_the_ever_column()
        database.init_db()

        assert ever_flag(chat_id) == 0

    def test_a_long_chat_survives_the_upgrade_that_found_a_dropped_call(
            self, client) -> None:
        """THE OUTCOME, not the column, and it is the whole reason this line
        of SQL is worth a test.

        Two hundred messages, one dropped connection on disk, and the build
        that adds this column opens the database. What the reader pays for
        next is either the newest twenty messages, once, or the oldest
        twenty and then the next twenty and then the next - about ten billed
        calls to reach the present, on a chat nothing has ever read.
        """
        chat_id = seed(client, 200)
        ids = message_ids(chat_id)
        settle(chat_id, work_key="mig-long", from_id=ids[0], to_id=ids[19],
               status="failed", error_type="ReadTimeout")

        unmigrate_the_ever_column()
        database.init_db()

        plan = notebook_worker._plan_work(chat_id, "vendor/cheap", "en", 4, 20)

        assert plan is not None
        assert plan["to_id"] == ids[-1], (
            "the upgrade read a dropped call as a reading and sent a chat "
            "nothing has ever read back to its oldest message, twenty at a "
            "time")
        assert plan["from_id"] == ids[-20]

    def test_a_chat_that_really_was_read_still_survives_the_upgrade(
            self, client) -> None:
        """POSITIVE CONTROL for the outcome above. The narrower predicate
        must not have been narrowed to nothing: a chat with a completed
        extraction on disk still comes out of the migration knowing it, and
        still reads forward from its cursor rather than jumping to the end.
        """
        chat_id = seed(client, 200)
        ids = message_ids(chat_id)
        settle(chat_id, work_key="mig-read", from_id=ids[0], to_id=ids[19])

        unmigrate_the_ever_column()
        database.init_db()
        assert ever_flag(chat_id) == 1
        # The cursor's own evidence removed, the way an edit removes it, so
        # the flag is the only thing left answering the question.
        with get_db() as con:
            con.execute("DELETE FROM notebook_extractions WHERE chat_id = ?",
                        (chat_id,))

        plan = notebook_worker._plan_work(chat_id, "vendor/cheap", "en", 4, 20)

        assert plan is not None
        assert plan["from_id"] == ids[0], (
            "a chat that had been read came out of the migration looking "
            "brand new and abandoned every message before the present")


class TestTheDayIsOneSourceBothSidesCanBeGiven:
    """The ceiling resets at a local midnight, and until the read side took a
    day it was not possible to write a test that crossed one.

    `claim_call` and `release_call` already took the claim's day so a pair
    hours apart could not disagree; `spend_today` still asked SQLite what day
    it was. A test could move the write side and then had no way to look at
    the row it had written.
    """

    def test_the_read_side_answers_about_the_day_it_is_given(
            self, db) -> None:
        with get_db() as con:
            notebook.claim_call(con, config.NOTEBOOK_DAILY_CALL_CAP, day="2026-08-30")
            notebook.claim_call(con, config.NOTEBOOK_DAILY_CALL_CAP, day="2026-08-30")
            assert notebook.spend_today(con, day="2026-08-30")["calls"] == 2
            # A different day is a different row, not the same counter seen
            # again.
            assert notebook.spend_today(con, day="2026-08-31")["calls"] == 0

    def test_the_ceiling_starts_over_on_the_next_day(self, db) -> None:
        with get_db() as con:
            notebook.claim_call(con, 1, day="2026-08-30")
            with pytest.raises(notebook.NotebookError) as exc:
                notebook.claim_call(con, 1, day="2026-08-30")
            assert exc.value.code == "notebook_daily_cap_reached"
            # Same cap, next day, and the budget is whole again.
            assert notebook.claim_call(con, 1, day="2026-08-31") == 1

    def test_the_production_path_still_reads_the_real_day(self, db) -> None:
        """GROUND CONTROL. Nothing above patches the clock, so this proves
        the seam did not change what an unparameterised call answers."""
        with get_db() as con:
            notebook.claim_call(con, config.NOTEBOOK_DAILY_CALL_CAP)
            assert notebook.spend_today(con)["calls"] == 1

    def test_both_sides_agree_on_the_key_format(self, db) -> None:
        """The `day` column is `ON CONFLICT` keyed. A write whose key is
        shaped differently from the read's opens a new row per call and the
        ceiling stops bounding anything, so the two are measured against each
        other rather than against a hard-coded string."""
        with get_db() as con:
            today = notebook.spend_day(con)
            notebook.claim_call(con, config.NOTEBOOK_DAILY_CALL_CAP)
            notebook.claim_call(con, config.NOTEBOOK_DAILY_CALL_CAP)
            assert notebook.spend_today(con, day=today)["calls"] == 2
            rows = con.execute(
                "SELECT COUNT(*) FROM notebook_spend").fetchone()[0]
            assert rows == 1, "two claims on one day opened two rows"

    def test_the_lifetime_total_is_still_unfiltered(self, db) -> None:
        """GROUND CONTROL for the one function the seam must not reach.
        `spend_lifetime` sums every day on purpose; a day filter leaking into
        it would hide spend that really happened."""
        with get_db() as con:
            notebook.claim_call(con, config.NOTEBOOK_DAILY_CALL_CAP, day="2026-08-30")
            notebook.claim_call(con, config.NOTEBOOK_DAILY_CALL_CAP, day="2026-08-31")
            assert notebook.spend_lifetime(con)["calls"] == 2


class TestAPaidCallOutlivesTheChatItWasAbout:
    """The chat can be deleted while the provider is still answering.

    `chat_id` on `notebook_extractions` is a foreign key, so the INSERT threw
    and took the whole transaction with it - including the spend row written
    at the top of the same function, deliberately first, precisely so that a
    call that could not be landed is still counted. The money left the
    account and every screen said nothing had happened.
    """

    def test_the_spend_survives_a_chat_deleted_mid_call(self, client) -> None:
        chat_id = seed(client)
        ids = message_ids(chat_id)
        assert client.delete(f"/api/v1/chats/{chat_id}").status_code in (200, 204)

        with get_db() as con:
            before = notebook.spend_today(con)["cost"]
        out = settle(chat_id, work_key="wk-gone", from_id=ids[0],
                     to_id=ids[-1],
                     usage={"cost": 0.01, "tokens_in": 100, "tokens_out": 10,
                            "request_id": "gone-1"})
        with get_db() as con:
            after = notebook.spend_today(con)["cost"]

        assert after == pytest.approx(before + 0.01), (
            "the chat was gone, so the INSERT rolled the transaction back "
            "and the paid call was un-recorded with it")
        assert out["chat_gone"] is True
        # The two halves of "recorded but not written", read from the
        # database rather than from the return value. The per-call ledger
        # `record_usage` writes FIRST has the row; the extraction table,
        # which is the thing the foreign key protects, has none.
        with get_db() as con:
            paid = con.execute(
                "SELECT tokens_in, tokens_out, cost FROM notebook_spend_calls "
                "WHERE call_id = ?", ("gone-1",)).fetchone()
        assert paid is not None, "the per-call ledger lost the billed call"
        assert tuple(paid) == (100, 10, pytest.approx(0.01))
        assert extraction_row("wk-gone") is None, (
            "there is no chat to hang it on, so no extraction row may exist")
        # The bucket, not a claim about duplication. This is the flag the
        # settling caller reads as "this reply could not be landed, and the
        # provider is not at fault" - see the test below for what it stops.
        assert out["duplicate"] is True

    def test_a_settle_with_nothing_paid_still_reports_it_did_not_write(
            self, client) -> None:
        """The gate is for money that has already left, and only that.

        The trace row is written BEFORE the request, with no usage at all,
        and its caller refuses to make the call when that write fails. If
        this returned quietly there too, the worker would believe a row
        existed, send the request and pay for an answer about a chat that no
        longer exists - the opposite of what the gate is for.
        """
        chat_id = seed(client)
        ids = message_ids(chat_id)
        assert client.delete(f"/api/v1/chats/{chat_id}").status_code in (
            200, 204)
        with pytest.raises(Exception) as exc:
            settle(chat_id, work_key="wk-trace", from_id=ids[0],
                   to_id=ids[-1], status="running")
        assert "FOREIGN KEY" in str(exc.value).upper()

    def test_the_same_call_lands_normally_on_a_living_chat(
            self, client) -> None:
        """GROUND CONTROL. The identical arguments against a chat that still
        exists spend the money AND write the row, so the test above cannot be
        passing because the fixture never reached the function."""
        chat_id = seed(client)
        ids = message_ids(chat_id)
        with get_db() as con:
            before = notebook.spend_today(con)["cost"]
        out = settle(chat_id, work_key="wk-alive", from_id=ids[0],
                     to_id=ids[-1],
                     usage={"cost": 0.01, "tokens_in": 100, "tokens_out": 10,
                            "request_id": "alive-1"})
        with get_db() as con:
            assert notebook.spend_today(con)["cost"] == pytest.approx(
                before + 0.01)
        assert out.get("chat_gone") is None
        assert extraction_row("wk-alive")[0] == "done"

    @pytest.mark.anyio
    async def test_a_dead_chat_does_not_clear_the_failure_breaker(
            self, client, monkeypatch) -> None:
        """END TO END, and the reason the return says `duplicate`.

        The breaker stops the worker after a run of consecutive failures.
        A reply that lands nothing must not clear that memory, and a reply
        about a deleted chat lands nothing by definition. The dangerous
        shape is the quiet one: the model proposes no facts, so the settle
        looks like a completed run with an empty result, and the counter
        that was one call short of stopping goes back to zero.
        """
        chat_id = seed(client, 40)
        set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")
        worker = notebook_worker.worker
        worker.breaker.reset()

        async def answers_after_the_chat_is_gone(*a, **kw):
            # Deleted WHILE the provider is answering, which is the only way
            # to reach this path: the plan was built against a live chat.
            with get_db() as con:
                # The order the delete route uses. The notebook rows go
                # first because the trace row written before this call
                # holds a foreign key to the chat.
                notebook.delete_for_chats(con, [chat_id])
                con.execute("DELETE FROM messages WHERE chat_id = ?",
                            (chat_id,))
                con.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
            # No facts. This is the shape that used to read as a healthy run.
            return {"id": "gone-e2e", "choices": [{
                "finish_reason": "stop",
                "message": {"content": json.dumps({"facts": []})}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10,
                          "cost": 0.01}}

        monkeypatch.setattr(openrouter, "complete",
                            answers_after_the_chat_is_gone)

        with get_db() as con:
            before = notebook.spend_today(con)["cost"]
        # Real failures on the record, one short of the cooldown threshold,
        # so a wrongful reset is visible as a number. Taken from the module
        # rather than typed, because a test that hard-codes the threshold
        # stops measuring the moment the threshold moves.
        armed = notebook_worker.TRIP_AFTER - 1
        for _ in range(armed):
            worker.breaker.failed(0.0)
        assert worker.breaker.failures == armed
        runs_before = worker.runs
        empty_before = worker.settled_empty

        await worker._handle(chat_id)

        with get_db() as con:
            assert notebook.spend_today(con)["cost"] == pytest.approx(
                before + 0.01), "the billed call went unrecorded"
        assert worker.breaker.failures == armed, (
            "a reply about a deleted chat cleared the breaker's memory of "
            "every real provider failure before it")
        assert worker.runs == runs_before, (
            "nothing was written, so this was not a completed run")
        # The counter the panel actually shows. A settle that landed nothing
        # belongs here and nowhere else: not a run, and not a provider fault
        # the breaker should be counting against the next chat.
        assert worker.settled_empty == empty_before + 1

    def test_a_gate_on_a_dead_chat_is_a_shape_this_file_already_has(
            self, client) -> None:
        """POSITIVE CONTROL for the absence claimed above. `create_entry`
        checks the same table for the same reason and refuses. So the deleted
        id in these tests really is unreachable, and the missing piece was
        only the second writer's gate."""
        chat_id = seed(client)
        assert client.delete(f"/api/v1/chats/{chat_id}").status_code in (200, 204)
        with pytest.raises(notebook.NotebookError) as exc:
            notebook.create_entry(chat_id, "Her brother owns the mill.")
        assert exc.value.code == "chat_not_found"


class TestTheRowRemembersEveryAttemptItWasBilledFor:
    """One `work_key` can be settled several times: `running`, then a
    `failed` that was paid for, then a retry that succeeded. The daily
    counter adds all of them, because `record_usage` upserts additively. The
    per-range row assigned instead, so the audit trail and the day total
    told two different stories about the same money.
    """

    def test_two_billed_attempts_add_up_on_the_row(self, client) -> None:
        chat_id = seed(client)
        ids = message_ids(chat_id)
        # TWO DIFFERENT request ids on purpose. `record_usage` de-duplicates
        # on the provider's id, so a second settle reusing the first's id is
        # swallowed and the day total would prove nothing.
        settle(chat_id, work_key="acc", from_id=ids[0], to_id=ids[-1],
               status="failed", error_type="ReadTimeout",
               usage={"cost": 0.01, "tokens_in": 100, "tokens_out": 10,
                      "request_id": "acc-1"})
        settle(chat_id, work_key="acc", from_id=ids[0], to_id=ids[-1],
               status="done",
               usage={"cost": 0.02, "tokens_in": 200, "tokens_out": 20,
                      "request_id": "acc-2"})

        status, cost, tin, tout = extraction_row("acc")
        # GROUND CONTROL: `status` is a state, not a total. Seeing the LAST
        # value here proves both settles ran and that the second one took the
        # UPDATE branch - if everything were being summed, this would be
        # wrong too.
        assert status == "done"
        assert cost == pytest.approx(0.03)
        assert (tin, tout) == (300, 30)
        # GROUND CONTROL 2: the fields that must NOT accumulate. They say
        # where this range is now, so each carries the LAST attempt's value
        # and the failed attempt's `ReadTimeout` is gone rather than merged.
        with get_db() as con:
            state = con.execute(
                "SELECT request_id, error_type FROM notebook_extractions "
                "WHERE work_key = ?", ("acc",)).fetchone()
        assert tuple(state) == ("acc-2", None)

    def test_the_day_total_is_unchanged_by_that(self, client) -> None:
        """GROUND CONTROL 2. `notebook_spend` was already right. If the row
        fix made this move, the money would be counted twice."""
        chat_id = seed(client)
        ids = message_ids(chat_id)
        with get_db() as con:
            before = notebook.spend_today(con)["cost"]
        for i, (cost, status) in enumerate(
                [(0.01, "failed"), (0.02, "done")]):
            settle(chat_id, work_key="acc2", from_id=ids[0], to_id=ids[-1],
                   status=status, error_type="ReadTimeout" if i == 0 else None,
                   usage={"cost": cost, "request_id": f"acc2-{i}"})
        with get_db() as con:
            assert notebook.spend_today(con)["cost"] == pytest.approx(
                before + 0.03)

    def test_a_running_row_has_no_cost_and_the_settle_still_lands(
            self, client) -> None:
        """The column starts NULL. `cost = cost + ?` on a NULL column is
        NULL, so the first real price would erase itself; this is what the
        COALESCE on the COLUMN side is for."""
        chat_id = seed(client)
        ids = message_ids(chat_id)
        settle(chat_id, work_key="nullcol", from_id=ids[0], to_id=ids[-1],
               status="running")
        assert extraction_row("nullcol")[1] is None
        settle(chat_id, work_key="nullcol", from_id=ids[0], to_id=ids[-1],
               status="failed", error_type="ReadTimeout",
               usage={"cost": 0.02, "tokens_in": 200, "tokens_out": 20,
                      "request_id": "nullcol-1"})
        status, cost, tin, tout = extraction_row("nullcol")
        assert status == "failed"
        assert cost == pytest.approx(0.02), (
            "the first real price was added to a NULL and became NULL")
        assert (tin, tout) == (200, 20)
        # A THIRD settle with a second price, so this test also fails if the
        # accumulation is reverted to a plain assignment. Without it the
        # NULL-then-0.02 sequence reads 0.02 either way and the only thing
        # measured is the COALESCE. `done` goes last on purpose: a settled
        # `done` row is an answer, and a fourth call would be refused as a
        # duplicate before it reached this branch at all.
        settle(chat_id, work_key="nullcol", from_id=ids[0], to_id=ids[-1],
               status="done",
               usage={"cost": 0.04, "tokens_in": 400, "tokens_out": 40,
                      "request_id": "nullcol-2"})
        status, cost, tin, tout = extraction_row("nullcol")
        assert status == "done"
        assert cost == pytest.approx(0.06)
        assert (tin, tout) == (600, 60)

    def test_a_re_run_with_no_usage_does_not_erase_what_was_paid(
            self, client) -> None:
        """The other half: a `running` re-claim carries no usage at all, so
        the bound parameter is NULL. Without the COALESCE on the PARAMETER
        side that turns an existing price into NULL - a paid call reported as
        unpriced."""
        chat_id = seed(client)
        ids = message_ids(chat_id)
        settle(chat_id, work_key="nullparam", from_id=ids[0], to_id=ids[-1],
               status="failed", error_type="ReadTimeout",
               usage={"cost": 0.01, "tokens_in": 100, "tokens_out": 10,
                      "request_id": "nullparam-1"})
        settle(chat_id, work_key="nullparam", from_id=ids[0], to_id=ids[-1],
               status="running")
        status, cost, tin, tout = extraction_row("nullparam")
        assert status == "running"
        assert cost == pytest.approx(0.01)
        assert (tin, tout) == (100, 10)
