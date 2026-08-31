"""Two ways into one range, and one day key for one call.

The background loop drains a queue; the Sweep button runs `_handle` from the
HTTP request task. They are the same `Worker`, on the same loop, and until
this file existed neither could see the other:

  * `already_done` answers for `status='done'` only, so a range the other
    path had claimed thirty seconds ago and was still generating against
    passed the gate and was claimed and BILLED a second time for one answer;
  * `_plan_work` opens by calling `settle_orphaned_running`, whose written
    safety argument was "the worker is ONE task draining one chat at a time,
    so a `running` row cannot belong to a call still in flight". `sweep()`
    made that false, so the sweep's first act was to mark the loop's live,
    paid row `failed` and move the cursor past it.

And the first fix for that had a failure mode worse than the defect: a work
key left in `_active` is a work key in `keep` forever, which freezes that
chat permanently while the panel reports a healthy worker. So half of this
file is about the gate and half is about it letting go.

The day key is here for the same reason: the claim, the refund and the cost
are three writes about ONE call, and each used to derive the date for
itself.
"""
from __future__ import annotations

import asyncio

import pytest

import config
import database
import notebook_store as notebook
import notebook_worker


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _seed(client, count: int = 30) -> int:
    from tests.conftest import make_character, make_chat

    chat_id = make_chat(client, make_character(client))
    with database.get_db() as con:
        for i in range(count):
            con.execute(
                "INSERT INTO messages (chat_id, role, content, active) "
                "VALUES (?,?,?,1)",
                (chat_id, "user" if i % 2 == 0 else "assistant",
                 f"line {i}: the mill changed hands"))
    return chat_id


def _cursor(chat_id: int) -> int:
    with database.get_db() as con:
        return con.execute(
            "SELECT COALESCE(MAX(to_message_id), 0) FROM notebook_extractions "
            "WHERE chat_id = ? AND status IN ('done','failed')",
            (chat_id,)).fetchone()[0]


class TestTheGateLetsGo:
    """The regression the gate introduced, and it was the worse of the two.

    `_active` was given back at four hand-written return sites. `_claim_one`
    raises `VaultLockedError` and `sqlite3.OperationalError`, neither of
    which is a `NotebookError`, so the commonest failure on that line
    escaped all four - and nothing in the process ever clears that set.
    """

    @pytest.mark.anyio
    async def test_a_locked_vault_during_the_claim_does_not_strand_the_range(
            self, client, monkeypatch) -> None:
        import vault_state

        chat_id = _seed(client)
        database.set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")

        # GROUND CONTROL on its OWN chat, because a preamble consumes the
        # range it reads: running it against `chat_id` would move the cursor
        # and the injected failure below would never reach the claim at all.
        other = _seed(client)
        ground = notebook_worker.Worker()
        ground.queue = asyncio.Queue(maxsize=8)
        assert await ground._prepare(other) is not None
        assert len(ground._active) == 1, (
            "ground: a successful preamble reaches the claim and holds its "
            "key, so the line broken below is one this code really runs")

        w = notebook_worker.Worker()
        w.queue = asyncio.Queue(maxsize=8)

        def locked() -> str:
            raise vault_state.VaultLockedError("vault_locked")

        monkeypatch.setattr(notebook_worker, "_claim_one", locked)

        with pytest.raises(vault_state.VaultLockedError):
            await w._prepare(chat_id)

        assert w._active == set(), (
            "the key was kept after a failure that is not a NotebookError; "
            "it is now in `keep` for the life of the process, so the running "
            "row can never be closed out and this chat never reads again")

    @pytest.mark.anyio
    async def test_a_stranded_key_would_freeze_the_chat(
            self, client) -> None:
        """POSITIVE CONTROL for the sentence above, and the reason it is
        phrased as a stall rather than as untidiness.

        This plants the leak by hand and measures the consequence, so the
        test above is asserting something with a cost attached rather than a
        housekeeping preference.
        """
        chat_id = _seed(client)
        database.set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")

        w = notebook_worker.Worker()
        w.queue = asyncio.Queue(maxsize=8)
        ready = await w._prepare(chat_id)
        assert ready is not None
        key = ready[0]["work_key"]

        # The turn is abandoned WITHOUT `_handle` ever running, which is what
        # a leak looks like from the outside: a `running` row and a key still
        # held.
        with database.get_db() as con:
            assert con.execute(
                "SELECT COUNT(*) FROM notebook_extractions "
                "WHERE work_key = ? AND status = 'running'",
                (key,)).fetchone()[0] == 1, "ground: the trace is there"

        for _ in range(3):
            assert await w._prepare(chat_id) is None, (
                "the range is refused while this process holds it")
        assert _cursor(chat_id) == 0, "and the cursor does not move"

        # Letting go IS the whole of the recovery - no restart, no
        # migration. Measured on the sweep itself rather than on a second
        # `_prepare`, because by now the cursor has nothing new to read and
        # a `None` from `_prepare` would mean two different things.
        with database.get_db() as con:
            assert notebook.settle_orphaned_running(
                con, chat_id, keep=frozenset(w._active)) == 0, (
                "ground: while the key is held the row is protected")
        w._active.discard(key)
        with database.get_db() as con:
            assert notebook.settle_orphaned_running(
                con, chat_id, keep=frozenset(w._active)) == 1, (
                "once the key is back the row is an ordinary orphan again "
                "and the cursor can move past it")


class TestOneRangeIsClaimedOnce:
    @pytest.mark.anyio
    async def test_the_second_entry_point_is_refused_rather_than_billed(
            self, client) -> None:
        chat_id = _seed(client)
        database.set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")

        with database.get_db() as con:
            before = notebook.spend_today(con)["calls"]

        w = notebook_worker.Worker()
        w.queue = asyncio.Queue(maxsize=8)

        first = await w._prepare(chat_id)
        assert first is not None
        with database.get_db() as con:
            assert notebook.spend_today(con)["calls"] == before + 1, (
                "ground: the first entry point really did claim")

        second = await w._prepare(chat_id, min_new=1, after_id=0)
        assert second is None, "the same range was planned and claimed twice"
        with database.get_db() as con:
            assert notebook.spend_today(con)["calls"] == before + 1, (
                "the day was charged twice for one answer")
            rows = con.execute(
                "SELECT status FROM notebook_extractions WHERE chat_id = ?",
                (chat_id,)).fetchall()
        assert [r[0] for r in rows] == ["running"], (
            "the refusal wrote a row over the live trace of a call that is "
            "still out - the work key is the same, so a `skipped` row would "
            "replace the evidence the first attempt was ever made")
        assert w.refused_in_flight == 1, (
            "the collision left no trace at all; the counter is the only "
            "thing that says the two paths are reaching for one range")

    @pytest.mark.anyio
    async def test_the_button_says_so_instead_of_going_quiet(
            self, client) -> None:
        """Where the reason actually reaches a person.

        The gate inside `_prepare` cannot write a row - `commit_extraction`
        is keyed on the work key and the live `running` row already holds it
        - so a reason recorded there is a reason dropped on the floor. The
        reader who needs it is the one pressing Sweep, and that route
        already answers with a reason the panel renders.
        """
        chat_id = _seed(client)
        database.set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")

        w = notebook_worker.Worker()
        w.queue = asyncio.Queue(maxsize=8)

        # GROUND CONTROL: with nothing in flight the button is answered on
        # its merits, not by this gate.
        assert (await w.sweep(chat_id))["started"] is True

        held = await w._prepare(chat_id)
        assert held is not None, "ground: the loop is now inside this chat"

        out = await w.sweep(chat_id)
        assert out == {"started": False, "reason": "already_in_flight"}, (
            "the button went quiet while the loop was reading the same "
            "range, or worse, paid for it again")

    @pytest.mark.anyio
    async def test_a_live_row_is_not_closed_out_as_an_orphan(
            self, client) -> None:
        """The half that costs an ANSWER rather than a call.

        `settle_orphaned_running` marks every `running` row for the chat
        `failed` and lets the cursor past it. Run from the second entry
        point while the first is still generating, that discards a reply
        that has already been paid for.
        """
        chat_id = _seed(client)
        database.set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")

        w = notebook_worker.Worker()
        w.queue = asyncio.Queue(maxsize=8)
        ready = await w._prepare(chat_id)
        assert ready is not None
        key = ready[0]["work_key"]

        with database.get_db() as con:
            settled = notebook.settle_orphaned_running(
                con, chat_id, keep=frozenset(w._active))
            status = con.execute(
                "SELECT status FROM notebook_extractions WHERE work_key = ?",
                (key,)).fetchone()[0]
        assert settled == 0
        assert status == "running", (
            "the other entry point failed a call that was still in flight")

    def test_a_row_nobody_holds_is_still_an_orphan(self, db) -> None:
        """GROUND CONTROL. The keep-set must not turn the orphan sweep off.

        A row left by a crash is exactly what that sweep exists for, and a
        fix that simply stopped sweeping would look identical from the test
        above.
        """
        with database.get_db() as con:
            con.execute("INSERT INTO characters (name) VALUES ('C')")
            cid = con.execute("SELECT MAX(id) FROM characters").fetchone()[0]
            con.execute(
                "INSERT INTO chats (character_id, title) VALUES (?,'t')",
                (cid,))
            chat_id = con.execute("SELECT MAX(id) FROM chats").fetchone()[0]
            notebook.commit_extraction(
                con, work_key="abandoned-by-a-crash", chat_id=chat_id,
                from_id=1, to_id=9, status="running")

            assert notebook.settle_orphaned_running(
                con, chat_id, keep=frozenset({"something-else"})) == 1
            assert con.execute(
                "SELECT status FROM notebook_extractions "
                "WHERE work_key = 'abandoned-by-a-crash'").fetchone()[0] \
                == "failed"

    def test_keep_refuses_the_two_shapes_that_lie(self, db) -> None:
        """A bare string and a None are both silent wrong answers.

        `tuple("abc")` is three one-character keys - protecting nothing AND
        failing the row it was handed - and `NOT IN (NULL)` is never true,
        which turns the sweep off for every chat with a healthy-looking
        rowcount. Both are shapes the `keep=()` default invites.
        """
        with database.get_db() as con:
            with pytest.raises(TypeError):
                notebook.settle_orphaned_running(con, 1, keep="a-work-key")
            with pytest.raises(ValueError):
                notebook.settle_orphaned_running(con, 1, keep=(None,))
            # POSITIVE CONTROL: the shapes that are right still work.
            assert notebook.settle_orphaned_running(con, 1, keep=()) == 0
            assert notebook.settle_orphaned_running(
                con, 1, keep=frozenset({"k"})) == 0


class TestOneCallIsOneDay:
    """The claim, the refund and the cost are three writes about one call.

    Each used to derive `date('now','localtime')` for itself, hours apart.
    Across a midnight that is three different rows.
    """

    def test_a_refund_finds_the_day_the_claim_was_made_on(self, db) -> None:
        with database.get_db() as con:
            yesterday = "2026-08-30"
            notebook.claim_call(con, 60, day=yesterday)
            notebook.claim_call(con, 60, day=yesterday)
            today = notebook.spend_day(con)
            assert today != yesterday, "ground: two distinct day keys"
            notebook.claim_call(con, 60, day=today)

            left = notebook.release_call(con, day=yesterday)

            assert left == 1, "the refund landed on the wrong row"
            assert con.execute(
                "SELECT calls FROM notebook_spend WHERE day = ?",
                (today,)).fetchone()[0] == 1, (
                "today was decremented for a call today never made, which "
                "lets one extra billed call through the ceiling")

    def test_the_cost_lands_on_the_row_that_counted_the_call(self, db) -> None:
        with database.get_db() as con:
            yesterday = "2026-08-30"
            notebook.claim_call(con, 60, day=yesterday)
            notebook.record_usage(
                con, {"request_id": "req-A", "tokens_in": 900,
                      "tokens_out": 40, "cost": 0.0004},
                day=yesterday)
            rows = dict(con.execute(
                "SELECT day, calls FROM notebook_spend").fetchall())
            cost = dict(con.execute(
                "SELECT day, cost FROM notebook_spend").fetchall())

        assert list(rows) == [yesterday], (
            "the cost opened a second row: the panel then reads a charge "
            "with no call today and a call with no charge yesterday")
        assert rows[yesterday] == 1
        assert cost[yesterday] == pytest.approx(0.0004)

    def test_an_ordinary_same_day_call_is_unchanged(self, db) -> None:
        """GROUND CONTROL. Every one of these takes the default path in
        production; a fix that only worked when a day is passed explicitly
        would be no fix at all."""
        with database.get_db() as con:
            day = notebook.spend_day(con)
            assert notebook.claim_call(con, 60) == 1
            notebook.record_usage(
                con, {"request_id": "req-B", "cost": 0.001})
            assert notebook.spend_today(con)["calls"] == 1
            assert notebook.release_call(con) == 0
            assert con.execute(
                "SELECT COUNT(*) FROM notebook_spend WHERE day = ?",
                (day,)).fetchone()[0] == 1
