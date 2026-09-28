"""Repros for a message read in parts (68 a), each kept as a test.

-     The ordinary reader never touches a chat a reading job holds, and a
      long message's parts are never stepped over: every part is read, or
      stays named `message_part_pending` and is read on a later turn.
-     No paid call leaves after a vault lock began, including one whose
      later parts were being named when the lock landed.
-     One read takes at most PARTS_PER_READ later parts of a message and at
      most a FOLLOW_ON_SHARE of the day's remaining calls; other chats keep
      theirs, and the rest continues on later turns.
-     The Notes tab's long-message line never shows during a healthy read;
      the pending-parts write is checked; a job's calls are never counted
      as the ordinary reader's.

Drives the real `Worker`; only the provider and the timing hooks named in
each test are fakes. Constants are imported.
"""
from __future__ import annotations

import asyncio
import json

import pytest

import config
import notebook_extract
import notebook_worker
from database import get_db

from tests.test_notebook_long_messages import (
    TestALongMessageReadIsWhole as Long, _long_chat, _read)
from tests.test_notebook_worker import fact


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _part_rows(chat_id: int) -> list[tuple[str, str, str | None]]:
    with get_db() as con:
        return sorted((r[0].rsplit(":", 1)[1], r[1], r[2]) for r in con.execute(
            "SELECT work_key, status, skip_reason FROM notebook_extractions "
            "WHERE chat_id = ? AND work_key LIKE '%:part%'",
            (chat_id,)).fetchall())


def _job_state(chat_id: int) -> str:
    with get_db() as con:
        return con.execute(
            "SELECT state FROM notebook_backfill_jobs WHERE chat_id = ? "
            "ORDER BY id DESC LIMIT 1", (chat_id,)).fetchone()[0]


def _stub(monkeypatch, on_call=None) -> list[str]:
    import openrouter

    sent: list[str] = []

    async def reply(*a, **kw):
        sent.append(a[0][1]["content"])
        if on_call is not None:
            await on_call(len(sent))
        return {"id": f"gen-{len(sent)}", "choices": [{
            "finish_reason": "stop",
            "message": {"content": json.dumps({"facts": []})}}],
            "usage": {"tokens_in": 10, "tokens_out": 5, "cost": 0.0001}}

    monkeypatch.setattr(openrouter, "complete", reply)
    return sent


class TestTheJobsChatIsTheJobs:
    @pytest.mark.anyio
    async def test_an_offer_for_the_jobs_chat_between_units(
            self, client, monkeypatch) -> None:
        """Repro: a reply in the chat a job is reading, handled
        between two job units, used to drop the job's pending part; the job
        then closed `done` with parts 2 and 3 never read."""
        from tests.test_notebook_backfill import choose

        chat_id, unread, sent = Long._huge_chat(client, monkeypatch)
        choose(chat_id)
        w = notebook_worker.Worker()
        w.queue = asyncio.Queue(maxsize=8)
        real_cap = notebook_worker._backfill_cap
        offers: list[asyncio.Task] = []

        def cap():
            if len(sent) == 1 and not offers:  # part 1 read, part 2 next
                offers.append(asyncio.get_running_loop().create_task(
                    w._read_offer(chat_id)))
            return real_cap()

        monkeypatch.setattr(notebook_worker, "_backfill_cap", cap)
        assert (await w.backfill(chat_id, "all"))["started"] is True
        await w.wait_for_backfill()
        results = [await t for t in offers]

        assert offers, "ground: an offer came while the job held the chat"
        assert results == [None], "the ordinary reader ran in the job's chat"
        assert Long._parts(chat_id) == {"done": 3}, _part_rows(chat_id)
        assert _job_state(chat_id) == "done"

    @pytest.mark.anyio
    async def test_a_job_pressed_during_an_ordinary_read_loses_no_part(
            self, client, monkeypatch) -> None:
        """The other direction: the job starts while the ordinary reader is
        between two parts. The ordinary read yields; the parts it did not
        reach stay named and are read, by the job or by the next reply."""
        from tests.test_notebook_backfill import choose

        chat_id, unread, sent = Long._huge_chat(client, monkeypatch)
        choose(chat_id)
        w = notebook_worker.Worker()
        w.queue = asyncio.Queue(maxsize=8)
        started: list[dict] = []
        pressed: list[asyncio.Task] = []

        async def press():
            # The moment part 1 has settled and before part 2 is claimed:
            # the chat is not in flight, so the button is not refused.
            while chat_id in w._active_chats:
                await asyncio.sleep(0)
            started.append(await w.backfill(chat_id, "all"))

        async def on_call(n):
            if n == 1 and not pressed:
                pressed.append(asyncio.get_running_loop().create_task(press()))

        _stub(monkeypatch, on_call)
        await w._read_offer(chat_id)
        await pressed[0]
        await w.wait_for_backfill()
        await w._read_offer(chat_id)            # the next reply

        assert started and started[0]["started"] is True, (
            "ground: the job started during the ordinary read")
        assert Long._parts(chat_id) == {"done": 3}, _part_rows(chat_id)
        assert _read(chat_id, unread) == unread

    @pytest.mark.anyio
    async def test_the_ordinary_reader_does_nothing_in_a_held_chat(
            self, client, monkeypatch) -> None:
        sent = _stub(monkeypatch)
        chat_id, _ = _long_chat(client, 100)
        w = notebook_worker.Worker()
        monkeypatch.setattr(w, "_job_holds", lambda cid: True)
        asked: list[int] = []
        real_pending = notebook_worker._next_pending_part

        def pending(*a, **kw):
            asked.append(1)
            return real_pending(*a, **kw)

        monkeypatch.setattr(notebook_worker, "_next_pending_part", pending)

        assert await w._read_offer(chat_id) is None
        assert sent == []
        assert asked == [], "the reader looked into a chat the job holds"
        # POSITIVE: the same chat, not held, is read.
        monkeypatch.setattr(w, "_job_holds", lambda cid: False)
        await w._read_offer(chat_id)
        assert len(sent) == 1


class TestNoPaidCallAfterTheLock:
    @pytest.mark.anyio
    async def test_follow_on_part_one_sent_after_stand_down(
            self, client, monkeypatch) -> None:
        """Repro: a follow-on call whose message is read in
        parts passed the stand-down check at its claim, then named its
        later parts (an await) and sent anyway after the lock began."""
        chat_id, unread = _long_chat(client, 100)
        with get_db() as con:
            con.execute(
                "UPDATE messages SET content = ? WHERE id = ?",
                ("<m05>" + "y" * int(notebook_extract.TURNS_MAX_CHARS * 2.5),
                 unread[5]))
        w = notebook_worker.Worker()
        after_lock: list[bool] = []

        async def on_call(n):
            after_lock.append(w.task is None)

        sent = _stub(monkeypatch, on_call)
        real = notebook_worker._record_pending_parts

        def naming(*a, **kw):
            w.task = None                     # the lock lands here
            return real(*a, **kw)

        monkeypatch.setattr(notebook_worker, "_record_pending_parts", naming)
        w.task = asyncio.current_task()
        with get_db() as con:
            spent = notebook_worker._calls_today()

        await w._read_offer(chat_id)

        assert after_lock == [False], (
            f"a paid request left after the stand-down began: {after_lock}")
        assert len(sent) == 1, "ground: the first call went out"
        assert notebook_worker._calls_today() == spent + 1, (
            "the claim of the call that did not leave was not given back")
        assert unread[5] not in _read(chat_id, unread), (
            "the message whose call never left was counted read")
        with get_db() as con:
            assert con.execute(
                "SELECT COUNT(*) FROM notebook_extractions WHERE chat_id = ? "
                "AND status = 'running'", (chat_id,)).fetchone()[0] == 0

        # And nothing is stepped over afterwards: the later parts named
        # before the lock wait for part 1, which the next read takes first.
        # That read comes at the threshold as usual: the rest of the
        # stretch stayed unread above the cursor, so a few more replies.
        from tests.test_notebook_planner_budget import add_messages

        monkeypatch.setattr(notebook_worker, "_record_pending_parts", real)
        w.task = asyncio.current_task()
        with get_db() as con:
            cursor = con.execute(
                "SELECT MAX(to_message_id) FROM notebook_extractions "
                "WHERE chat_id = ? AND status = 'done'", (chat_id,)).fetchone()[0]
        above = sum(1 for i in unread if i > cursor)
        add_messages(chat_id, ["one more reply"] * (
            config.NOTEBOOK_EXTRACT_EVERY_TURNS - above))
        for _ in range(3):
            await w._read_offer(chat_id)
        parts = {k: st for k, st, _r in _part_rows(chat_id)}
        assert parts == {"part1of3": "done", "part2of3": "done",
                         "part3of3": "done"}, parts
        assert _read(chat_id, unread) == unread


class TestOnePasteDoesNotTakeTheDay:
    @pytest.mark.anyio
    async def test_one_long_paste_leaves_the_other_chats_their_calls(
            self, client, monkeypatch) -> None:
        """Repro: an ~840k paste made 60 calls in one offer and
        starved every other chat until midnight."""
        sent = _stub(monkeypatch)
        chat_id, unread = _long_chat(client, 100)
        with get_db() as con:
            con.execute(
                "UPDATE messages SET content = ? WHERE id = ?",
                ("<m00>" + "q" * (notebook_extract.TURNS_MAX_CHARS * 70),
                 unread[0]))
        w = notebook_worker.Worker()

        await w._read_offer(chat_id)

        first_read = len(sent)
        # Part 1, PARTS_PER_READ later parts, and one call for the rest of
        # the stretch (the nineteen short messages after the paste).
        assert first_read == 1 + notebook_worker.PARTS_PER_READ + 1, first_read
        assert first_read <= config.NOTEBOOK_DAILY_CALL_CAP \
            // notebook_worker.FOLLOW_ON_SHARE
        with get_db() as con:
            block = w.long_message_block(con, chat_id)
        assert block["pending"] > 0, "the rest was dropped, not kept"

        # Another chat still gets its call today.
        other, _ = _long_chat(client, 100)
        await w._read_offer(other)
        assert len(sent) == first_read + 1

        # And the paste goes on at the next reply, a few parts at a time.
        await w._read_offer(chat_id)
        with get_db() as con:
            later = w.long_message_block(con, chat_id)
        assert later["pending"] == block["pending"] \
            - notebook_worker.PARTS_PER_READ

    @pytest.mark.anyio
    async def test_a_read_takes_at_most_its_share_of_the_day(
            self, client, monkeypatch) -> None:
        sent = _stub(monkeypatch)
        chat_id, _ = _long_chat(client, 2000)       # needs 5 calls
        for _ in range(config.NOTEBOOK_DAILY_CALL_CAP - 6):
            notebook_worker._claim_one()             # 6 left: 3 allowed

        await notebook_worker.Worker()._read_offer(chat_id)

        assert len(sent) == 6 // notebook_worker.FOLLOW_ON_SHARE


class TestWhatTheNotesTabSays:
    @pytest.mark.anyio
    async def test_the_line_does_not_show_during_a_healthy_read(
            self, client, monkeypatch) -> None:
        """The repro recorded the pending count per call as
        [2, 1, 0]; the card read "not read" for the whole live read."""
        w = notebook_worker.Worker()
        seen: list[dict] = []

        async def on_call(n):
            with get_db() as con:
                seen.append(w.long_message_block(con, chat_id))

        chat_id, unread, _sent = Long._huge_chat(client, monkeypatch)
        _stub(monkeypatch, on_call)

        await w._read_offer(chat_id)

        # Three parts and one call for the short messages after them.
        assert len(seen) == 4, "ground: every call of the read was watched"
        assert all(b == {"pending": 0, "failed": 0, "unread": 0} for b in seen), seen

    @pytest.mark.anyio
    async def test_POSITIVE_parts_left_for_later_are_said_once_the_read_ends(
            self, client, monkeypatch) -> None:
        chat_id, _unread, _sent = Long._huge_chat(client, monkeypatch)
        _stub(monkeypatch)
        monkeypatch.setattr(notebook_worker, "PARTS_PER_READ", 1)
        w = notebook_worker.Worker()

        await w._read_offer(chat_id)

        with get_db() as con:
            assert w.long_message_block(con, chat_id) == {
                "pending": 1, "failed": 0, "unread": 0}

    @pytest.mark.anyio
    async def test_no_part_is_sent_if_the_later_parts_cannot_be_named(
            self, client, monkeypatch) -> None:
        chat_id, unread, sent = Long._huge_chat(client, monkeypatch)
        monkeypatch.setattr(notebook_worker, "_record_pending_parts",
                            lambda *a, **kw: False)
        spent = notebook_worker._calls_today()

        await notebook_worker.Worker()._read_offer(chat_id)

        assert sent == [], "a part left with its later parts unnamed"
        assert notebook_worker._calls_today() == spent
        assert _read(chat_id, unread) == []

    @pytest.mark.anyio
    async def test_a_jobs_calls_are_not_the_ordinary_readers(
            self, client, monkeypatch) -> None:
        from tests.test_notebook_backfill import choose

        sent = _stub(monkeypatch)
        chat_id, _ = _long_chat(client, 2000)
        choose(chat_id)
        w = notebook_worker.Worker()
        w.queue = asyncio.Queue(maxsize=8)
        assert (await w.backfill(chat_id, "all"))["started"] is True
        await w.wait_for_backfill()

        assert len(sent) > 1, "ground: the job made calls"
        assert w._reached == {}
        assert w.status(now=0.0, chat_id=chat_id)["read_calls"] is None

    def test_the_route_serves_the_block(self, client) -> None:
        with_chat = client.get("/api/v1/notebook/worker?chat_id=1").json()
        without = client.get("/api/v1/notebook/worker").json()
        assert with_chat["long_message"] == {"pending": 0, "failed": 0, "unread": 0}
        assert without["long_message"] is None


class TestAFailedPartIsReadAgain:
    @pytest.mark.anyio
    async def test_a_later_part_that_times_out_is_read_on_a_later_read(
            self, client, monkeypatch) -> None:
        """The h23t repro: part 2 of 3 times out at the provider. It used to
        be lost (the cursor had passed the message with part 1). It stays
        a failed part the card names, and the next read reads it."""
        from tests.test_notebook_planner_budget import add_messages

        chat_id, unread, sent = Long._huge_chat(client, monkeypatch)
        import openrouter
        real = openrouter.complete

        async def flaky(*a, **kw):
            if len(sent) == 1:                # part 2 is the second call
                sent.append(a[0][1]["content"])
                raise RuntimeError("read timeout")
            return await real(*a, **kw)

        monkeypatch.setattr(openrouter, "complete", flaky)
        w = notebook_worker.Worker()
        await w._read_offer(chat_id)

        parts = {k: st for k, st, _r in _part_rows(chat_id)}
        assert parts == {"part1of3": "done", "part2of3": "failed",
                         "part3of3": "skipped"}, parts
        with get_db() as con:
            assert w.long_message_block(con, chat_id) == {
                "pending": 1, "failed": 1, "unread": 0}

        add_messages(chat_id, ["one more reply"])  # the next reply
        await w._read_offer(chat_id)

        assert Long._parts(chat_id) == {"done": 3}, _part_rows(chat_id)
        with get_db() as con:
            assert w.long_message_block(con, chat_id) == {
                "pending": 0, "failed": 0, "unread": 0}


class TestTheRealJobHoldsPredicate:
    @pytest.mark.anyio
    async def test_a_running_job_keeps_the_ordinary_reader_out(
            self, client, monkeypatch) -> None:
        """No stub: a real job is between two units when a reply's offer is
        handled. The real `_job_holds` answers, and the reader stays out."""
        from tests.test_notebook_backfill import choose

        chat_id, _unread = _long_chat(client, 2000)
        choose(chat_id)
        w = notebook_worker.Worker()
        w.queue = asyncio.Queue(maxsize=8)
        doors: list[str] = []
        real_handle = w._handle
        offered: list[object] = []

        async def handle(cid, **kw):
            doors.append("job" if kw.get("job_id") is not None else "ordinary")
            if kw.get("job_id") is not None and len(doors) == 1:
                offered.append(await w._read_offer(cid))   # between units
            return await real_handle(cid, **kw)

        monkeypatch.setattr(w, "_handle", handle)
        _stub(monkeypatch)
        assert (await w.backfill(chat_id, "all"))["started"] is True
        await w.wait_for_backfill()

        assert offered == [None], "the reader ran in a chat a job held"
        assert "ordinary" not in doors, doors
        assert doors.count("job") >= 2, "ground: the job read in several units"


def _paste(client, times: float, marker: str = "<m00>"):
    chat_id, unread = _long_chat(client, 100)
    with get_db() as con:
        con.execute("UPDATE messages SET content = ? WHERE id = ?",
                    (marker + "q" * int(notebook_extract.TURNS_MAX_CHARS * times),
                     unread[0]))
    return chat_id, unread


class TestAnAbandonedPartIsReadAgain:
    @pytest.mark.anyio
    async def test_a_part_out_when_the_app_died_is_read_on_the_next_start(
            self, client, monkeypatch) -> None:
        """Part 2 is at the provider when the process dies; the
        next cycle closes it `abandoned_in_flight`. It was paid and never
        read, so it is read again, and the card's "tried again" is true."""
        import notebook_store as notebook

        chat_id, unread, sent = Long._huge_chat(client, monkeypatch)
        monkeypatch.setattr(notebook_worker, "PARTS_PER_READ", 0)
        await notebook_worker.Worker()._read_offer(chat_id)
        with get_db() as con:
            con.execute(
                "UPDATE notebook_extractions SET status = 'running', "
                "skip_reason = NULL WHERE chat_id = ? "
                "AND work_key LIKE '%:part2of%'", (chat_id,))
            assert notebook.settle_orphaned_running(con, chat_id) == 1
        monkeypatch.setattr(notebook_worker, "PARTS_PER_READ", 4)
        w = notebook_worker.Worker()                 # the next start
        with get_db() as con:
            assert w.long_message_block(con, chat_id)["failed"] == 1, (
                "ground: the abandoned part is on the card")

        await w._read_offer(chat_id)

        assert Long._parts(chat_id) == {"done": 3}, _part_rows(chat_id)
        with get_db() as con:
            assert w.long_message_block(con, chat_id) == {
                "pending": 0, "failed": 0, "unread": 0}


class TestAPartThatNoLongerPlansDoesNotBlock:
    @pytest.mark.anyio
    async def test_newer_messages_are_read_when_a_part_no_longer_plans(
            self, client, monkeypatch) -> None:
        """The ceiling is raised across a restart after part 1 of
        4, so parts 3 and 4 no longer exist in the new split. They are
        named not read and the chat's new messages are read."""
        from tests.test_notebook_planner_budget import add_messages

        chat_id, _unread = _paste(client, 3.5)
        sent = _stub(monkeypatch)
        monkeypatch.setattr(notebook_worker, "PARTS_PER_READ", 0)
        w = notebook_worker.Worker()
        await w._read_offer(chat_id)
        assert Long._parts(chat_id) == {"done": 1, "skipped": 3}, "ground"
        monkeypatch.setattr(notebook_extract, "TURNS_MAX_CHARS",
                            notebook_extract.TURNS_MAX_CHARS * 10)
        monkeypatch.setattr(notebook_worker, "PARTS_PER_READ", 4)
        with get_db() as con:
            last = con.execute("SELECT MAX(id) FROM messages WHERE chat_id = ?",
                               (chat_id,)).fetchone()[0]
        add_messages(chat_id, [f"<new{i:02d}> the ferry leaves at nine"
                               for i in range(25)])
        with get_db() as con:
            new_ids = [r[0] for r in con.execute(
                "SELECT id FROM messages WHERE chat_id = ? AND id > ?",
                (chat_id, last)).fetchall()]
        n0 = len(sent)

        await w._read_offer(chat_id)

        assert len(sent) > n0 and _read(chat_id, new_ids), (
            "the chat was wedged by a part that no longer plans")
        with get_db() as con:
            block = w.long_message_block(con, chat_id)
        assert block["pending"] == 0 and block["unread"] >= 1, block


class TestWhatTheCardSaysAboutParts:
    @pytest.mark.anyio
    async def test_no_pending_line_while_a_job_reads_the_chat(
            self, client, monkeypatch) -> None:
        """Sampled between job units the block said pending 2,
        then 1."""
        from tests.test_notebook_backfill import choose

        chat_id, unread, sent = Long._huge_chat(client, monkeypatch)
        choose(chat_id)
        w = notebook_worker.Worker()
        w.queue = asyncio.Queue(maxsize=8)
        real_cap = notebook_worker._backfill_cap
        seen: list[dict] = []

        def cap():
            with get_db() as con:
                seen.append(w.long_message_block(con, chat_id))
            return real_cap()

        monkeypatch.setattr(notebook_worker, "_backfill_cap", cap)
        assert (await w.backfill(chat_id, "all"))["started"] is True
        await w.wait_for_backfill()

        assert len(seen) >= 3, "ground: sampled between several units"
        assert all(b == {"pending": 0, "failed": 0, "unread": 0}
                   for b in seen), seen
        assert Long._parts(chat_id) == {"done": 3}

    @pytest.mark.anyio
    async def test_parts_of_a_message_regenerated_away_are_not_on_the_card(
            self, client, monkeypatch) -> None:
        chat_id, unread = _paste(client, 3.5)
        _stub(monkeypatch)
        monkeypatch.setattr(notebook_worker, "PARTS_PER_READ", 1)
        w = notebook_worker.Worker()
        await w._read_offer(chat_id)
        with get_db() as con:
            assert w.long_message_block(con, chat_id)["pending"] > 0, "ground"
            con.execute("UPDATE messages SET active = 0 WHERE id = ?",
                        (unread[0],))
            assert w.long_message_block(con, chat_id) == {
                "pending": 0, "failed": 0, "unread": 0}

    @pytest.mark.anyio
    async def test_a_part_that_always_fails_is_given_up_and_said(
            self, client, monkeypatch) -> None:
        """Retried on later reads PART_RETRIES_MAX times in all, then named
        `message_part_unread`: the card says it, and the reader goes on
        with the rest of the chat instead of stopping at that part."""
        chat_id, unread, sent = Long._huge_chat(client, monkeypatch)
        import openrouter
        real = openrouter.complete

        async def part_two_fails(*a, **kw):
            text = a[0][1]["content"]
            if "yyyy" in text and "zzzz" in text:        # only part 2
                sent.append(text)
                raise RuntimeError("read timeout")
            return await real(*a, **kw)

        monkeypatch.setattr(openrouter, "complete", part_two_fails)
        w = notebook_worker.Worker()
        tries = 0
        for _ in range(notebook_worker.PART_RETRIES_MAX + 2):
            before = sum("yyyy" in s and "zzzz" in s for s in sent)
            await w._read_offer(chat_id)
            tries += sum("yyyy" in s and "zzzz" in s for s in sent) - before
            w.chat_breakers.clear()

        assert tries == notebook_worker.PART_RETRIES_MAX, tries
        parts = {k: (st, r) for k, st, r in _part_rows(chat_id)}
        assert parts["part2of3"] == ("skipped", "message_part_unread"), parts
        assert parts["part3of3"][0] == "done", "the read did not go on"
        with get_db() as con:
            assert w.long_message_block(con, chat_id)["unread"] == 1


class TestRetriesCountOnlyThePartsOwnFailures:
    @staticmethod
    def _account_down(monkeypatch, exc_factory):
        import openrouter

        state = {"down": False, "calls": 0}
        sent = _stub(monkeypatch)
        real = openrouter.complete

        async def maybe_down(*a, **kw):
            if state["down"]:
                state["calls"] += 1
                raise exc_factory()
            return await real(*a, **kw)

        monkeypatch.setattr(openrouter, "complete", maybe_down)
        return state, sent

    @pytest.mark.anyio
    async def test_credit_runs_out_for_three_replies_then_the_part_is_read(
            self, client, monkeypatch) -> None:
        """402 on three replies in a row (breakers left alone,
        TRIP_AFTER not reached), then a top-up. The part was given up for
        good; the account's failures are not the part's."""
        import openrouter

        chat_id, _unread = _paste(client, 3.5)
        state, _sent = self._account_down(monkeypatch, lambda: (
            openrouter.OpenRouterError("openrouter_insufficient_credits")))
        monkeypatch.setattr(notebook_worker, "PARTS_PER_READ", 0)
        w = notebook_worker.Worker()
        await w._read_offer(chat_id)
        monkeypatch.setattr(notebook_worker, "PARTS_PER_READ", 4)

        state["down"] = True
        for _ in range(notebook_worker.PART_RETRIES_MAX):
            await w._read_offer(chat_id)
        assert state["calls"] == notebook_worker.PART_RETRIES_MAX, (
            "ground: each reply tried the part and the account refused")
        state["down"] = False                   # topped up
        await w._read_offer(chat_id)
        await w._read_offer(chat_id)

        assert Long._parts(chat_id) == {"done": 4}, _part_rows(chat_id)
        with get_db() as con:
            assert w.long_message_block(con, chat_id)["unread"] == 0

    @pytest.mark.anyio
    async def test_POSITIVE_a_failure_of_the_part_itself_still_counts(
            self, client, monkeypatch) -> None:
        """The same three replies with a failure that is the part's own
        (an unclassified error, "unknown" scope): it is given up."""
        chat_id, _unread = _paste(client, 3.5)
        state, _sent = self._account_down(
            monkeypatch, lambda: RuntimeError("the reply made no sense"))
        monkeypatch.setattr(notebook_worker, "PARTS_PER_READ", 0)
        w = notebook_worker.Worker()
        await w._read_offer(chat_id)
        monkeypatch.setattr(notebook_worker, "PARTS_PER_READ", 4)
        state["down"] = True
        for _ in range(notebook_worker.PART_RETRIES_MAX):
            await w._read_offer(chat_id)
            w.chat_breakers.clear()
        with get_db() as con:
            assert w.long_message_block(con, chat_id)["unread"] == 1

    @pytest.mark.anyio
    async def test_one_offer_makes_no_back_to_back_failing_calls(
            self, client, monkeypatch) -> None:
        """Three later parts, each at its last try, and every call
        fails. One offer used to make three failing paid calls in a row;
        after a give-up that followed a failed call the offer stops."""
        chat_id, _unread = _paste(client, 3.5)
        state, _sent = self._account_down(
            monkeypatch, lambda: RuntimeError("the reply made no sense"))
        monkeypatch.setattr(notebook_worker, "PARTS_PER_READ", 0)
        w = notebook_worker.Worker()
        await w._read_offer(chat_id)
        monkeypatch.setattr(notebook_worker, "PARTS_PER_READ", 4)
        with get_db() as con:
            keys = [r[0] for r in con.execute(
                "SELECT work_key FROM notebook_extractions WHERE chat_id = ? "
                "AND work_key LIKE '%:part%of%' "
                "AND work_key NOT LIKE '%:part1of%'", (chat_id,)).fetchall()]
        assert len(keys) == 3, "ground: three later parts"
        for key in keys:
            w._part_failures[key] = notebook_worker.PART_RETRIES_MAX - 1
        state["down"] = True

        await w._read_offer(chat_id)

        assert state["calls"] == 1, (
            f"one offer made {state['calls']} failing calls in a row")
        with get_db() as con:
            assert w.long_message_block(con, chat_id)["unread"] == 1


class TestAFailureThatNeverLeftIsNotThePartsFault:
    """Offline (httpx.ConnectError) and an egress-gate refusal
    both raise `openrouter_error` with reached_provider False; the code is
    "unknown" scope, but nothing left the machine."""

    async def _offline_three_replies_then_online(self, client, monkeypatch,
                                                 exc_factory):
        chat_id, _unread = _paste(client, 3.5)
        state, _sent = TestRetriesCountOnlyThePartsOwnFailures._account_down(
            monkeypatch, exc_factory)
        monkeypatch.setattr(notebook_worker, "PARTS_PER_READ", 0)
        w = notebook_worker.Worker()
        await w._read_offer(chat_id)
        monkeypatch.setattr(notebook_worker, "PARTS_PER_READ", 4)
        state["down"] = True
        for _ in range(notebook_worker.PART_RETRIES_MAX):
            await w._read_offer(chat_id)
            w.chat_breakers.clear()
        assert state["calls"] == notebook_worker.PART_RETRIES_MAX, "ground"
        state["down"] = False
        for _ in range(2):
            w.breaker.failures = 0
            await w._read_offer(chat_id)
        return chat_id, w

    @pytest.mark.anyio
    async def test_offline_for_three_replies_then_the_part_is_read(
            self, client, monkeypatch) -> None:
        import openrouter

        chat_id, w = await self._offline_three_replies_then_online(
            client, monkeypatch, lambda: openrouter.OpenRouterError(
                "openrouter_error", reached_provider=False, retryable=True))
        assert Long._parts(chat_id) == {"done": 4}, _part_rows(chat_id)
        with get_db() as con:
            assert w.long_message_block(con, chat_id)["unread"] == 0

    @pytest.mark.anyio
    async def test_egress_refused_for_three_replies_then_the_part_is_read(
            self, client, monkeypatch) -> None:
        import openrouter

        chat_id, w = await self._offline_three_replies_then_online(
            client, monkeypatch, lambda: openrouter.OpenRouterError(
                "openrouter_error", reached_provider=False))
        assert Long._parts(chat_id) == {"done": 4}, _part_rows(chat_id)

    @pytest.mark.anyio
    async def test_POSITIVE_the_same_code_after_it_left_counts(
            self, client, monkeypatch) -> None:
        """`openrouter_error` that DID reach the provider (a 400 this text
        will not fit) is the part's own and gives it up."""
        import openrouter

        chat_id, w = await self._offline_three_replies_then_online(
            client, monkeypatch, lambda: openrouter.OpenRouterError(
                "openrouter_error"))
        with get_db() as con:
            assert w.long_message_block(con, chat_id)["unread"] == 1

    @pytest.mark.anyio
    async def test_a_row_that_cannot_be_read_does_not_count(
            self, client, monkeypatch) -> None:
        """The failure's row could not be looked up; an unknown is
        not held against the part."""
        chat_id, _unread = _paste(client, 3.5)
        state, _sent = TestRetriesCountOnlyThePartsOwnFailures._account_down(
            monkeypatch, lambda: RuntimeError("the reply made no sense"))
        monkeypatch.setattr(notebook_worker, "PARTS_PER_READ", 0)
        w = notebook_worker.Worker()
        await w._read_offer(chat_id)
        monkeypatch.setattr(notebook_worker, "PARTS_PER_READ", 4)
        monkeypatch.setattr(notebook_worker, "_part_error_type",
                            lambda key: (False, None))
        state["down"] = True
        for _ in range(notebook_worker.PART_RETRIES_MAX + 1):
            await w._read_offer(chat_id)
            w.chat_breakers.clear()
        assert state["calls"] == notebook_worker.PART_RETRIES_MAX + 1, "ground"
        with get_db() as con:
            assert w.long_message_block(con, chat_id)["unread"] == 0
