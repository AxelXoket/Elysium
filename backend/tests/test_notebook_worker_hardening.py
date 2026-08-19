"""FAZ 5 audit round - eight defects, and the tests that were missing.

Every fix in here came from a read-only audit, was reproduced before it was
patched, and then survived a mutation that broke nothing - which indicts the
TEST, not the code. This file is that debt paid: one behavioural test per
defect, each able to fail if the fix is undone.
"""
from __future__ import annotations

import asyncio
import json

import pytest

import config
import notebook_store as notebook
import notebook_worker
from database import get_db

from tests.conftest import make_character, make_chat


@pytest.fixture
def anyio_backend():
    return "asyncio"


def seed(client, count: int = 30) -> int:
    chat_id = make_chat(client, make_character(client))
    with get_db() as con:
        for i in range(count):
            con.execute(
                "INSERT INTO messages (chat_id, role, content, active) "
                "VALUES (?,?,?,1)",
                (chat_id, "user" if i % 2 == 0 else "assistant",
                 f"line {i}: her brother owns the mill"))
    return chat_id


def fact(**over):
    base = {"text": "Her brother owns the mill.",
            "evidence": "her brother owns the mill",
            "kind": "fact", "durability": "permanent",
            "importance": 2, "supersedes": None}
    base.update(over)
    return base


def worker() -> notebook_worker.Worker:
    w = notebook_worker.Worker()
    w.queue = asyncio.Queue(maxsize=8)
    return w


def replying(monkeypatch, facts, *, finish="stop", usage=None):
    import openrouter

    async def _reply(*a, **kw):
        return {"id": "gen", "choices": [{
            "finish_reason": finish,
            "message": {"content": json.dumps({"facts": facts})}}],
            "usage": usage or {}}

    monkeypatch.setattr(openrouter, "complete", _reply)


class TestAFailedRangeCanBeRetried:
    """The worst one. `already_done` matched ANY row, so a range that failed
    or was skipped read as answered - and because the cursor only advances
    past `done`, the retry was planned, claimed against the daily cap, sent
    and BILLED, and only then had its answer thrown away as a duplicate. The
    range stayed unread forever at that boundary.
    """

    def test_a_failed_attempt_is_not_an_answer(self, client) -> None:
        chat_id = seed(client, 4)
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.commit_extraction(
                con, work_key="k", chat_id=chat_id, from_id=1, to_id=4,
                status="failed", error_type="timeout")
            assert notebook.already_done(con, "k") is False

    def test_a_completed_one_is(self, client) -> None:
        chat_id = seed(client, 4)
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.commit_extraction(
                con, work_key="k", chat_id=chat_id, from_id=1, to_id=4,
                proposals=[])
            assert notebook.already_done(con, "k") is True

    def test_the_retry_of_a_failed_range_KEEPS_its_notes(self, client) -> None:
        """Not "does not crash" - keeps them. The duplicate path discarded a
        paid-for extraction and left the row saying `failed`."""
        chat_id = seed(client, 4)
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.commit_extraction(
                con, work_key="same", chat_id=chat_id, from_id=1, to_id=4,
                status="failed", error_type="timeout")
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            out = notebook.commit_extraction(
                con, work_key="same", chat_id=chat_id, from_id=1, to_id=4,
                proposals=[fact()], usage={"cost": 0.0001})
        assert out["duplicate"] is False and out["written"] == 1
        assert len(notebook.list_entries(chat_id)) == 1
        with get_db() as con:
            assert notebook.extraction_stats(con, chat_id)["done"] == 1
            assert notebook.extraction_stats(con, chat_id)["failed"] == 0

    def test_what_a_duplicate_COST_is_still_recorded(self, client) -> None:
        """It was sent, generated and billed. Skipping the spend write on that
        path made the counter under-report exactly the calls worth seeing."""
        chat_id = seed(client, 4)
        for _ in range(2):
            with get_db() as con:
                con.execute("BEGIN IMMEDIATE")
                notebook.commit_extraction(
                    con, work_key="dup", chat_id=chat_id, from_id=1, to_id=4,
                    proposals=[fact()], usage={"cost": 0.0002})
        with get_db() as con:
            assert notebook.spend_today(con)["cost"] == pytest.approx(0.0004)


class TestDeletingAMessageRollsBackTheReadingRecord:
    """`to_message_id` is a high-water mark and it outlived its own messages.
    A turn deleted or REWRITTEN below the mark could never be read again - and
    the edit case is the sharp one, because notes distilled from the old
    wording stay accepted while the new wording is never extracted."""

    def test_the_record_covering_a_deleted_message_goes(self, client) -> None:
        chat_id = seed(client, 6)
        with get_db() as con:
            ids = [r[0] for r in con.execute(
                "SELECT id FROM messages WHERE chat_id = ? ORDER BY id",
                (chat_id,)).fetchall()]
            con.execute("BEGIN IMMEDIATE")
            notebook.commit_extraction(
                con, work_key="w", chat_id=chat_id, from_id=ids[0],
                to_id=ids[-1], proposals=[])

        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.forget_proposals_from_messages(con, [ids[2]])
            left = con.execute(
                "SELECT COUNT(*) FROM notebook_extractions").fetchone()[0]
        assert left == 0, "the mark outlived the messages it covered"

    def test_a_record_BELOW_the_deleted_message_survives(self, client) -> None:
        """Ground. Rolling the whole history back on every delete would make
        the worker re-read - and re-pay for - the entire chat."""
        chat_id = seed(client, 6)
        with get_db() as con:
            ids = [r[0] for r in con.execute(
                "SELECT id FROM messages WHERE chat_id = ? ORDER BY id",
                (chat_id,)).fetchall()]
            con.execute("BEGIN IMMEDIATE")
            notebook.commit_extraction(
                con, work_key="early", chat_id=chat_id, from_id=ids[0],
                to_id=ids[1], proposals=[])

        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.forget_proposals_from_messages(con, [ids[4]])
            left = con.execute(
                "SELECT COUNT(*) FROM notebook_extractions").fetchone()[0]
        assert left == 1

    @pytest.mark.anyio
    async def test_the_worker_re_reads_the_rolled_back_stretch(
            self, client, monkeypatch) -> None:
        import database

        chat_id = seed(client, 30)
        database.set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")
        replying(monkeypatch, [])

        w = worker()
        await w._handle(chat_id)
        after_run = notebook_worker._plan_work(
            chat_id, "vendor/cheap", "en", 1, 20)
        assert after_run is not None
        moved_to = after_run["from_id"]

        # A message from INSIDE the stretch just read is deleted.
        with get_db() as con:
            covered = con.execute(
                "SELECT MIN(id) FROM messages WHERE chat_id = ?",
                (chat_id,)).fetchone()[0]
            con.execute("BEGIN IMMEDIATE")
            notebook.forget_proposals_from_messages(con, [covered + 3])

        again = notebook_worker._plan_work(chat_id, "vendor/cheap", "en", 1, 20)
        assert again is not None
        assert again["from_id"] < moved_to, (
            "the reading mark did not roll back, so the messages around the "
            "deleted one can never be read again")


class TestTheBreakerIsToldTheRightTime:
    @pytest.mark.anyio
    async def test_the_cooldown_starts_when_the_call_FAILED(
            self, client, monkeypatch) -> None:
        """The captured `now` was taken before a request that can burn the
        full 120-second timeout, so `opened_at` was stamped two minutes in the
        past against a 60-second cooldown: the breaker opened and was already
        half-open, and every following turn got another billed call through a
        breaker reporting "open"."""
        import database
        import openrouter

        chat_id = seed(client, 30)
        database.set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")

        async def slow_failure(*a, **kw):
            # Long enough that a stale timestamp would already have expired
            # the cooldown by the time it is written.
            await asyncio.sleep(0.05)
            raise openrouter.OpenRouterError("openrouter_error")

        monkeypatch.setattr(openrouter, "complete", slow_failure)
        monkeypatch.setattr(notebook_worker, "COOLDOWN_BASE_S", 0.04)
        monkeypatch.setattr(notebook_worker, "TRIP_AFTER", 1)

        w = worker()
        loop = asyncio.get_running_loop()
        before = loop.time()
        await w._handle(chat_id)

        assert w.breaker.state == "open"
        assert w.breaker.opened_at >= before + 0.05, (
            "the breaker was told a time from before the call")


class TestTheResetLiftsTheRefusalAndKeepsTheHISTORY:
    def test_the_lifetime_count_survives_a_reset(self) -> None:
        """`reset()` was `self.__init__()`, which zeroed the lifetime total -
        and since the permanent stop counted THAT, one press of the button
        made the twenty-failure ceiling unreachable forever. A reset that
        quietly disabled the strongest guard in the class."""
        b = notebook_worker.Breaker()
        for i in range(3):
            b.failed(float(i))
        b.reset()
        assert b.total_failures == 3
        assert b.state == "closed"

    def test_the_stop_ceiling_is_still_reachable_after_a_reset(self) -> None:
        b = notebook_worker.Breaker()
        for i in range(notebook_worker.STOP_AFTER):
            b.failed(float(i))
        b.reset()
        for i in range(notebook_worker.STOP_AFTER):
            b.failed(float(i))
        assert b.state == "stopped"

    def test_a_success_forgives_the_run_towards_the_stop(self) -> None:
        """Ground: twenty transient failures spread over a week, each
        followed by successes, must not permanently kill the feature."""
        b = notebook_worker.Breaker()
        for i in range(15):
            b.failed(float(i))
            b.succeeded()
        for i in range(15):
            b.failed(float(i))
            b.succeeded()
        assert b.state == "closed"


class TestAFailedWriteIsNotASuccess:
    @pytest.mark.anyio
    async def test_a_write_that_raises_is_recorded_as_a_failure(
            self, client, monkeypatch) -> None:
        """Declared successful first, a vault that locked between the reply
        and the write left a call that was billed, facts discarded, NO row of
        any status, a healthy breaker and an incremented run counter."""
        import database

        chat_id = seed(client, 30)
        database.set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")
        replying(monkeypatch, [fact()], usage={"cost": 0.0001})

        def boom(*a, **kw):
            raise RuntimeError("vault locked")

        monkeypatch.setattr(notebook_worker, "_write", boom)

        w = worker()
        await w._handle(chat_id)

        assert w.runs == 0, "a run that wrote nothing was counted"
        with get_db() as con:
            stats = notebook.extraction_stats(con, chat_id)
        assert stats["failed"] == 1 and stats["done"] == 0


class TestTheReadWindowIsBounded:
    def test_a_long_backlog_is_taken_in_pieces(self, client) -> None:
        """Unbounded, the planner selected all five hundred pending messages,
        named the last of them as the range, let the prompt builder keep the
        newest twelve thousand characters, and marked the WHOLE range done -
        so the other four hundred and eighty were silently never extracted."""
        chat_id = seed(client, 200)
        plan = notebook_worker._plan_work(chat_id, "vendor/cheap", "en", 4, 20)
        assert plan is not None
        assert len(plan["new"]) == 20
        with get_db() as con:
            ids = [r[0] for r in con.execute(
                "SELECT id FROM messages WHERE chat_id = ? ORDER BY id",
                (chat_id,)).fetchall()]
        # A chat this app has never read starts at the PRESENT, so the range
        # is the newest twenty rather than the oldest - see
        # TestAnExistingChatStartsAtThePRESENT for why.
        assert plan["to_id"] == ids[-1], "the range must name what was read"
        assert plan["from_id"] == ids[-20]

    def test_the_chunk_is_what_was_actually_sent(self, client) -> None:
        """The grounding check runs against this. A chunk larger than the
        prompt would validate a quote against text the model never saw."""
        chat_id = seed(client, 200)
        plan = notebook_worker._plan_work(chat_id, "vendor/cheap", "en", 4, 20)
        assert plan["chunk"].count("\n") == len(plan["new"]) - 1

    def test_a_short_backlog_is_taken_whole(self, client) -> None:
        chat_id = seed(client, 6)
        with get_db() as con:
            pending = con.execute(
                "SELECT COUNT(*) FROM messages WHERE chat_id = ? AND active = 1",
                (chat_id,)).fetchone()[0]
        plan = notebook_worker._plan_work(chat_id, "vendor/cheap", "en", 4, 20)
        assert len(plan["new"]) == pending

    @pytest.mark.anyio
    async def test_the_rest_of_the_backlog_is_not_marked_read(
            self, client, monkeypatch) -> None:
        import database

        chat_id = seed(client, 60)
        database.set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")
        replying(monkeypatch, [])

        w = worker()
        await w._handle(chat_id)

        left = notebook_worker._plan_work(chat_id, "vendor/cheap", "en", 4, 20)
        # Nothing NEW has arrived since, and the older stretch is behind the
        # mark by design - a chat this app met for the first time is read from
        # the present, not from its beginning.
        assert left is None


class TestAnImportedCardNeverAutoAccepts:
    """The named defence against a salami attack. The column existed, was read
    on every extraction, was described in two docstrings and the API contract,
    and was written by NOTHING - so every model-written fact distilled from
    somebody else's card went into the prompt unreviewed."""

    def _imported_character(self, client) -> int:
        resp = client.post("/api/v1/characters/import", json={
            "name": "Imported One", "description": "from a card",
            "first_mes": "hello",
        })
        assert resp.status_code == 201, resp.text
        return resp.json()["id"]

    def test_a_chat_from_an_imported_card_waits_for_review(
            self, client) -> None:
        chat_id = make_chat(client, self._imported_character(client))
        with get_db() as con:
            assert notebook.auto_accept_for(con, chat_id) is False

    def test_a_hand_written_character_follows_the_global_setting(
            self, client) -> None:
        """Ground: without it the assertion above is satisfied by a build that
        never auto-accepts anything."""
        chat_id = make_chat(client, make_character(client))
        with get_db() as con:
            assert notebook.auto_accept_for(con, chat_id) is True

    def test_the_notes_really_do_wait(self, client) -> None:
        chat_id = make_chat(client, self._imported_character(client))
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.commit_extraction(
                con, work_key="imp", chat_id=chat_id, from_id=1, to_id=1,
                proposals=[fact()])
        assert notebook.list_entries(chat_id)[0]["status"] == "proposed"


class TestTheModelCannotOutrankTheUser:
    def test_a_self_rated_importance_is_clamped(self, client) -> None:
        """The eviction order is importance-ascending, so a model rating its
        own suggestion a 3 pushed the USER's own notes out of the prompt
        first while its own survived."""
        chat_id = seed(client, 4)
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.commit_extraction(
                con, work_key="imp2", chat_id=chat_id, from_id=1, to_id=4,
                proposals=[fact(importance=3)])
        assert notebook.list_entries(chat_id)[0]["importance"] == 2

    def test_a_users_own_note_may_still_be_a_three(self, client) -> None:
        chat_id = seed(client, 4)
        entry = notebook.create_entry(chat_id, "Mine.", importance=3)
        assert entry["importance"] == 3


class TestAnAnsweredRangeIsNotPaidForAgain:
    """The work key was computed and then never consulted: `already_done` had
    no caller anywhere in the repository. The duplicate was noticed only after
    the call had been claimed against the daily cap, sent and BILLED - at
    which point the answer was thrown away."""

    @pytest.mark.anyio
    async def test_no_request_is_made_for_a_range_already_answered(
            self, client, monkeypatch) -> None:
        import database
        import openrouter

        chat_id = seed(client, 30)
        database.set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")

        plan = notebook_worker._plan_work(
            chat_id, "vendor/cheap", "en",
            config.NOTEBOOK_EXTRACT_EVERY_TURNS,
            config.NOTEBOOK_EXTRACT_EVERY_TURNS)
        assert plan is not None
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.commit_extraction(
                con, work_key=plan["work_key"], chat_id=chat_id,
                from_id=plan["from_id"], to_id=plan["to_id"], proposals=[])
            # The cursor is deliberately left where it was, so the planner
            # produces this same range again - which is exactly the state the
            # check exists for.
            con.execute("UPDATE notebook_extractions SET to_message_id = 0 "
                        "WHERE work_key = ?", (plan["work_key"],))

        sent = []

        async def spy(*a, **kw):
            sent.append(1)
            return {}

        monkeypatch.setattr(openrouter, "complete", spy)

        w = worker()
        await w._handle(chat_id)
        assert sent == [], "an answered range was sent to the provider again"
        with get_db() as con:
            assert notebook.spend_today(con)["calls"] == 0

    @pytest.mark.anyio
    async def test_an_unanswered_range_IS_sent(self, client, monkeypatch):
        """Ground: without it the assertion above is satisfied by a worker
        that never calls anything."""
        import database

        chat_id = seed(client, 30)
        database.set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")
        replying(monkeypatch, [])

        w = worker()
        await w._handle(chat_id)
        with get_db() as con:
            assert notebook.spend_today(con)["calls"] == 1


class TestPinnedNotesCannotBreakTheChat:
    """A pin means "never dropped to make room for another NOTE". It was
    reading as "allowed to break the conversation": pinned rows were exempt
    from eviction but not from arithmetic, so enough of them pushed the
    notebook past its ceiling, those characters entered the context budget,
    and every send in that chat failed with `context_too_large` - an error
    about the window, naming nothing about the notebook, with the only fix
    (unpin) never suggested anywhere.
    """

    def test_pins_alone_cannot_exceed_the_ceiling(self, client) -> None:
        chat_id = make_chat(client, make_character(client))
        for i in range(40):
            notebook.create_entry(chat_id, f"{i:03d} " + "x" * 200,
                                  pinned=True)
        blocks = notebook.build_notebook_blocks(chat_id, 32000)
        assert len(blocks["user_block"]) <= notebook.NOTEBOOK_MAX_CHARS

    def test_the_pins_that_did_not_fit_are_named(self, client) -> None:
        """They must not vanish quietly - the owner's rule is that a note
        never disappears, and this is the one case where the app overrode a
        pin the user set on purpose."""
        chat_id = make_chat(client, make_character(client))
        for i in range(40):
            notebook.create_entry(chat_id, f"{i:03d} " + "x" * 200,
                                  pinned=True)
        blocks = notebook.build_notebook_blocks(chat_id, 32000)
        assert any(reason == "pinned_over_ceiling"
                   for _id, reason in blocks["excluded"])

    def test_a_pin_still_outranks_an_unpinned_note(self, client) -> None:
        """Ground: the ceiling must not turn the pin into nothing."""
        chat_id = make_chat(client, make_character(client))
        pinned = notebook.create_entry(chat_id, "PINNED " + "x" * 200,
                                       pinned=True)
        for i in range(40):
            notebook.create_entry(chat_id, f"filler {i} " + "x" * 200)
        blocks = notebook.build_notebook_blocks(chat_id, 32000)
        dropped = {e[0] for e in blocks["excluded"]}
        assert pinned["id"] not in dropped

    def test_a_notebook_of_pins_that_FITS_loses_none(self, client) -> None:
        chat_id = make_chat(client, make_character(client))
        ids = [notebook.create_entry(chat_id, f"note {i}", pinned=True)["id"]
               for i in range(5)]
        blocks = notebook.build_notebook_blocks(chat_id, 32000)
        assert blocks["excluded"] == []
        assert blocks["sent"] == len(ids)


class TestTheCursorWipeIsScopedToItsOwnChat:
    """`messages.id` is a GLOBAL autoincrement. "Every reading record above
    id N" therefore spanned every conversation in the vault: deleting one
    message in one chat wiped every other chat's cursor, and the worker
    re-read - and re-paid for - all of their history, on every delete and
    every edit. Written in the previous audit round, found in this one.
    """

    def test_another_chats_record_survives(self, client) -> None:
        a = seed(client, 6)
        b = seed(client, 6)
        with get_db() as con:
            a_ids = [r[0] for r in con.execute(
                "SELECT id FROM messages WHERE chat_id = ? ORDER BY id",
                (a,)).fetchall()]
            b_ids = [r[0] for r in con.execute(
                "SELECT id FROM messages WHERE chat_id = ? ORDER BY id",
                (b,)).fetchall()]
            assert b_ids[0] > a_ids[0], "chat B must come later in id order"
            con.execute("BEGIN IMMEDIATE")
            notebook.commit_extraction(
                con, work_key="wa", chat_id=a, from_id=a_ids[0],
                to_id=a_ids[-1], proposals=[])
            notebook.commit_extraction(
                con, work_key="wb", chat_id=b, from_id=b_ids[0],
                to_id=b_ids[-1], proposals=[])

        # A message deleted in chat A, with an id BELOW chat B's whole range.
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.forget_proposals_from_messages(con, [a_ids[1]])

        with get_db() as con:
            left = {r[0] for r in con.execute(
                "SELECT work_key FROM notebook_extractions").fetchall()}
        assert "wb" in left, "an unrelated chat's reading record was wiped"
        assert "wa" not in left, "the affected chat's record should go"


class TestAnExistingChatStartsAtThePRESENT:
    """The upgrading user. Every test in this suite starts from a clean vault,
    so nobody had looked at what happens to a conversation that already has
    four hundred messages when this feature meets it for the first time.

    Reading from the oldest end, the notebook would spend twenty-odd paid
    turns describing the opening of a story that has moved on - and injecting
    those notes into the live prompt the whole time. A notebook that lags a
    session behind is worse than an empty one, because the model trusts it.
    """

    def test_the_first_read_of_a_long_chat_takes_the_NEWEST(self, client):
        chat_id = seed(client, 200)
        plan = notebook_worker._plan_work(chat_id, "vendor/cheap", "en", 4, 20)
        with get_db() as con:
            ids = [r[0] for r in con.execute(
                "SELECT id FROM messages WHERE chat_id = ? ORDER BY id",
                (chat_id,)).fetchall()]
        assert plan["to_id"] == ids[-1], "it started at the beginning"
        assert plan["from_id"] == ids[-20]

    def test_a_SHORT_existing_chat_is_read_whole(self, client) -> None:
        """Ground: the jump-to-the-end rule must not skip a conversation
        small enough to read entirely."""
        chat_id = seed(client, 6)
        plan = notebook_worker._plan_work(chat_id, "vendor/cheap", "en", 4, 20)
        with get_db() as con:
            first = con.execute(
                "SELECT MIN(id) FROM messages WHERE chat_id = ?",
                (chat_id,)).fetchone()[0]
        assert plan["from_id"] == first

    @pytest.mark.anyio
    async def test_after_the_first_read_it_moves_forward_normally(
            self, client, monkeypatch) -> None:
        """And never jumps again: once a mark exists, the delta is the delta.
        A rule that kept skipping to the end would silently drop every
        stretch the user wrote between runs."""
        import database

        chat_id = seed(client, 200)
        database.set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")
        replying(monkeypatch, [])

        w = worker()
        await w._handle(chat_id)

        with get_db() as con:
            mark = con.execute(
                "SELECT MAX(to_message_id) FROM notebook_extractions "
                "WHERE chat_id = ? AND status = 'done'", (chat_id,)).fetchone()[0]
            for i in range(30):
                con.execute(
                    "INSERT INTO messages (chat_id, role, content, active) "
                    "VALUES (?,'user',?,1)", (chat_id, f"new line {i}"))

        plan = notebook_worker._plan_work(chat_id, "vendor/cheap", "en", 4, 20)
        assert plan["from_id"] > mark, "it jumped to the end a second time"
