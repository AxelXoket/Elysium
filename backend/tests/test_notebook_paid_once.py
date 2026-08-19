"""Two ways the extraction ledger paid for the same words twice.

Both bugs live in the same crack: the worker decides what to read BEFORE the
call and settles the outcome AFTER it, and for the couple of minutes in
between the world can move. Neither was theoretical - each was reproduced
against the real code with a counted provider before it was fixed.

  1. A call that was made and never settled leaves a `running` row. The cursor
     counted only `done`, so that row neither blocked the range nor let it
     past: the identical stretch was re-planned, re-claimed and RE-SENT on
     every later cycle. The app dying with its window is the ordinary case
     here, not the exotic one - it has no shutdown path and says so.

  2. Editing a message deletes the extraction row on purpose, so the rewritten
     stretch gets read again. A reply already in flight then arrived and
     recreated that row as `done`, from the wording the user had just taken
     back - pushing the cursor past the edit forever and writing notes about
     a sentence that no longer exists.

The tests are therefore about a NUMBER: how many times one range is charged.
"""
from __future__ import annotations

import notebook_store as notebook
import notebook_worker
from database import get_db

from tests.test_notebook_worker import fact, seed

MODEL = "vendor/cheap"


def _cursor(chat_id: int) -> int:
    """Where the planner would start reading. Read through the planner rather
    than restated here: a test with its own copy of the cursor rule passes
    while the rule the app uses is broken."""
    plan = notebook_worker._plan_work(chat_id, MODEL, "en", 1, 20)
    return -1 if plan is None else plan["from_id"]


def _trace(chat_id: int, *, status: str, to_id: int, key: str = "k") -> None:
    """The row a paid call leaves behind, in whatever state we are testing."""
    with get_db() as con:
        con.execute("BEGIN IMMEDIATE")
        con.execute(
            "INSERT INTO notebook_extractions "
            "(work_key, chat_id, from_message_id, to_message_id, status) "
            "VALUES (?,?,?,?,?)", (key, chat_id, 1, to_id, status))


def _last_message(chat_id: int) -> int:
    with get_db() as con:
        return con.execute(
            "SELECT MAX(id) FROM messages WHERE chat_id = ?",
            (chat_id,)).fetchone()[0]


class TestACallMadeAndNeverSettled:
    """Bug 1. The app is killed, or the vault locks, mid-request."""

    def test_the_orphaned_row_is_closed_out(self, client) -> None:
        chat_id = seed(client, 8)
        _trace(chat_id, status="running", to_id=_last_message(chat_id))

        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            settled = notebook.settle_orphaned_running(con, chat_id)
            row = con.execute(
                "SELECT status, error_type FROM notebook_extractions "
                "WHERE chat_id = ?", (chat_id,)).fetchone()

        assert settled == 1
        assert row[0] == "failed", "a lost reply must not be counted an answer"
        assert row[1] == notebook.ABANDONED_IN_FLIGHT

    def test_a_settled_run_is_left_alone(self, client) -> None:
        """Ground. A sweep that also closed finished rows would rewrite the
        ledger every cycle and make the counter meaningless."""
        chat_id = seed(client, 8)
        _trace(chat_id, status="done", to_id=_last_message(chat_id))

        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            assert notebook.settle_orphaned_running(con, chat_id) == 0
            assert con.execute(
                "SELECT status FROM notebook_extractions WHERE chat_id = ?",
                (chat_id,)).fetchone()[0] == "done"

    def test_only_this_chat_is_swept(self, client) -> None:
        """The last defect in this file's neighbourhood was chat-blind and
        rolled back every chat's cursor from one chat's edit."""
        mine = seed(client, 8)
        theirs = seed(client, 8)
        _trace(mine, status="running", to_id=_last_message(mine), key="a")
        _trace(theirs, status="running", to_id=_last_message(theirs), key="b")

        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            assert notebook.settle_orphaned_running(con, mine) == 1
            assert con.execute(
                "SELECT status FROM notebook_extractions WHERE work_key = 'b'"
            ).fetchone()[0] == "running", "another chat's row was touched"

    def test_the_range_is_not_sent_a_second_time(self, client) -> None:
        """The money question, and the reason the sweep exists at all."""
        chat_id = seed(client, 8)
        _trace(chat_id, status="running", to_id=_last_message(chat_id))

        assert notebook_worker._plan_work(chat_id, MODEL, "en", 1, 20) is None, (
            "the planner re-read a range that was already paid for")

    def test_the_chat_does_not_freeze_instead(self, client) -> None:
        """The other half, and the reason the fix is not simply "refuse it".

        Refusing the range without moving the cursor stops the notebook for
        that chat forever: the planner reads a fixed window forward from the
        cursor, so the same stretch yields the same work key on every cycle
        and every one of them is refused.
        """
        chat_id = seed(client, 8)
        _trace(chat_id, status="running", to_id=_last_message(chat_id))
        _cursor(chat_id)                       # the sweep runs in here

        with get_db() as con:
            for i in range(4):
                con.execute(
                    "INSERT INTO messages (chat_id, role, content, active) "
                    "VALUES (?,'user',?,1)", (chat_id, f"after {i}"))

        plan = notebook_worker._plan_work(chat_id, MODEL, "en", 1, 20)
        assert plan is not None, "the chat stopped extracting altogether"
        assert plan["new"], "a plan with nothing in it is a frozen notebook"

    def test_a_chat_with_no_history_still_plans(self, client) -> None:
        """Positive control: the sweep must not be what stops a fresh chat."""
        chat_id = seed(client, 8)
        assert notebook_worker._plan_work(chat_id, MODEL, "en", 1, 20)

    def test_it_is_counted_where_the_owner_can_see_it(self, client) -> None:
        """A run that vanished and a quiet week are otherwise one screen."""
        chat_id = seed(client, 8)
        with get_db() as con:
            assert notebook.extraction_stats(con, chat_id)["abandoned"] == 0

        _trace(chat_id, status="running", to_id=_last_message(chat_id))
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.settle_orphaned_running(con, chat_id)
        with get_db() as con:
            assert notebook.extraction_stats(con, chat_id)["abandoned"] == 1


class TestAReplyThatArrivesAfterItsQuestionWasWithdrawn:
    """Bug 2. The user edits a message while the call is in flight."""

    def test_the_notes_are_not_written(self, client) -> None:
        chat_id = seed(client, 8)
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            out = notebook.commit_extraction(
                con, work_key="gone", chat_id=chat_id, from_id=1, to_id=4,
                proposals=[fact()], usage={"cost": 0.0002},
                require_trace=True)

        assert out["written"] == 0
        assert notebook.list_entries(chat_id) == [], (
            "notes were written about wording the user took back")

    def test_the_range_stays_unread(self, client) -> None:
        """The point of the rollback the edit performed. Marking it done here
        pushed the cursor past the edit, so the new wording was never read by
        any later run."""
        chat_id = seed(client, 8)
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.commit_extraction(
                con, work_key="gone", chat_id=chat_id, from_id=1, to_id=4,
                proposals=[fact()], require_trace=True)

        assert _cursor(chat_id) == 1, "the cursor jumped past the edited text"

    def test_the_money_is_still_recorded(self, client) -> None:
        """It was sent, generated and billed. A spend counter that hides the
        calls whose answers were thrown away is the one that misleads most."""
        chat_id = seed(client, 8)
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.commit_extraction(
                con, work_key="gone", chat_id=chat_id, from_id=1, to_id=4,
                proposals=[fact()], usage={"cost": 0.0002},
                require_trace=True)
            assert notebook.spend_today(con)["cost"] > 0

    def test_it_says_why_rather_than_reporting_success(self, client) -> None:
        chat_id = seed(client, 8)
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.commit_extraction(
                con, work_key="gone", chat_id=chat_id, from_id=1, to_id=4,
                proposals=[fact()], require_trace=True)
            reasons = notebook.extraction_stats(con, chat_id)["skip_reasons"]

        assert reasons.get("plan_invalidated") == 1

    def test_a_run_whose_trace_survived_writes_normally(self, client) -> None:
        """The positive control, and the one that matters most: a rule this
        strict, applied one step too widely, silently turns the whole feature
        off while every other test stays green."""
        chat_id = seed(client, 8)
        _trace(chat_id, status="running", to_id=4, key="kept")
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            out = notebook.commit_extraction(
                con, work_key="kept", chat_id=chat_id, from_id=1, to_id=4,
                proposals=[fact()], require_trace=True)

        assert out["written"] == 1
        assert len(notebook.list_entries(chat_id)) == 1

    def test_callers_that_never_left_a_trace_are_unaffected(self, client):
        """Ground. `require_trace` is opt-in on purpose: only the worker
        promises to have written one first."""
        chat_id = seed(client, 8)
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            out = notebook.commit_extraction(
                con, work_key="plain", chat_id=chat_id, from_id=1, to_id=4,
                proposals=[fact()])

        assert out["written"] == 1
