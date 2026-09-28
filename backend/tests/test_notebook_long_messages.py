"""Decision 68 a: a read of long messages reads all of them.

Each call sends at most `TURNS_MAX_CHARS` of conversation. The ordinary
reader used to keep the NEWEST lines that fit and mark the whole stretch
read, so on twenty messages of about a thousand characters nine were read
and eleven were stepped over for good. Now it keeps the OLDEST lines that
fit, and the same offer makes further calls, each an ordinary call counted
against the day's ceiling and halved under pressure, until the stretch is
read. What it cannot reach stays unread; nothing is marked read unless it
was sent.

Every test drives the real `Worker._read_offer` -> `_handle` -> `_plan_work`
path with a provider stub that records every request. Sizes and ceilings
come from the modules, never retyped.
"""
from __future__ import annotations

import json
import math

import pytest

import config
import database
import notebook_extract
import notebook_store as notebook
import notebook_worker
from database import get_db

from tests.test_notebook_planner_budget import add_messages, mark_done
from tests.test_notebook_worker import fact, seed

TWENTY = config.NOTEBOOK_EXTRACT_EVERY_TURNS


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _provider(monkeypatch) -> list[str]:
    """Records the text of every request sent."""
    import openrouter

    sent: list[str] = []

    async def reply(*a, **kw):
        sent.append(a[0][1]["content"])
        return {"id": f"gen-{len(sent)}", "choices": [{
            "finish_reason": "stop",
            "message": {"content": json.dumps({"facts": []})}}],
            "usage": {"tokens_in": 10, "tokens_out": 5, "cost": 0.0001}}

    monkeypatch.setattr(openrouter, "complete", reply)
    return sent


def _long_chat(client, size: int, count: int = TWENTY) -> tuple[int, list[int]]:
    """Two messages already read, then `count` unread ones of `size`
    characters, each opening with its own marker so a request can be
    searched for it. Returns the chat and the unread ids."""
    chat_id = seed(client, 2, greeting=False)
    with get_db() as con:
        ids = [r[0] for r in con.execute(
            "SELECT id FROM messages WHERE chat_id = ? ORDER BY id",
            (chat_id,)).fetchall()]
    mark_done(chat_id, ids[0], ids[-1], f"covered-{chat_id}")
    bodies = [f"<m{i:02d}>" + "x" * (size - 5) for i in range(count)]
    add_messages(chat_id, bodies)
    with get_db() as con:
        unread = [r[0] for r in con.execute(
            "SELECT id FROM messages WHERE chat_id = ? AND id > ? "
            "ORDER BY id", (chat_id, ids[-1])).fetchall()]
    database.set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")
    return chat_id, unread


def _done_ranges(chat_id: int) -> list[tuple[int, int]]:
    with get_db() as con:
        return [(r[0], r[1]) for r in con.execute(
            "SELECT from_message_id, to_message_id FROM notebook_extractions "
            "WHERE chat_id = ? AND status = 'done' ORDER BY rowid",
            (chat_id,)).fetchall()]


def _read(chat_id: int, ids: list[int]) -> list[int]:
    spans = _done_ranges(chat_id)
    return [i for i in ids if any(lo <= i <= hi for lo, hi in spans)]


def _expected_calls(chat_id: int, stretch: int = TWENTY) -> int:
    """The oracle, worked out from the chat itself: calls needed to read the
    first `stretch` unread messages when each call keeps whole lines from
    the oldest, under a ceiling that loses what the two messages before the
    call cost (at most `RECENT_RESERVE_CHARS`). Independent of the planner:
    it reads the rows and the two constants, nothing else."""
    with get_db() as con:
        rows = [(r[0], f"{r[1]}: {r[2]}") for r in con.execute(
            "SELECT id, role, content FROM messages WHERE chat_id = ? "
            "AND active = 1 ORDER BY id", (chat_id,)).fetchall()]
    cursor = len(_done_ranges(chat_id)) and max(
        i for i, (mid, _) in enumerate(rows)
        if any(lo <= mid <= hi for lo, hi in _done_ranges(chat_id))) + 1
    end = cursor + stretch
    calls = 0
    while cursor < end:
        recent = rows[max(0, cursor - 2):cursor]
        reserve = min(notebook_worker.RECENT_RESERVE_CHARS,
                      sum(len(line) + 1 for _, line in recent))
        room = notebook_extract.TURNS_MAX_CHARS - reserve
        spent, took = 0, 0
        while cursor + took < end:
            cost = len(rows[cursor + took][1]) + 1
            if spent + cost > room:
                break
            spent += cost
            took += 1
        assert took, "oracle: a line longer than the ceiling"
        cursor += took
        calls += 1
    return calls


class TestALongMessageReadIsWhole:
    @staticmethod
    def _huge_chat(client, monkeypatch):
        """One unread message about two and a half ceilings long, with the
        quote a note will cite sitting in its SECOND part, then nineteen
        short ones. The provider proposes that note only when it sees the
        quote."""
        import openrouter

        chat_id, unread = _long_chat(client, 100)
        body = ("<m00>" + "y" * 15_000 + " her brother owns the mill "
                + "z" * 10_000)
        assert len(body) > 2 * notebook_extract.TURNS_MAX_CHARS
        with get_db() as con:
            con.execute("UPDATE messages SET content = ? WHERE id = ?",
                        (body, unread[0]))
        sent: list[str] = []

        async def reply(*a, **kw):
            text = a[0][1]["content"]
            sent.append(text)
            facts = [fact()] if "her brother owns the mill" in text else []
            return {"id": f"gen-{len(sent)}", "choices": [{
                "finish_reason": "stop",
                "message": {"content": json.dumps({"facts": facts})}}],
                "usage": {"tokens_in": 10, "tokens_out": 5, "cost": 0.0001}}

        monkeypatch.setattr(openrouter, "complete", reply)
        return chat_id, unread, sent

    @staticmethod
    def _parts(chat_id: int) -> dict[str, int]:
        with get_db() as con:
            rows = con.execute(
                "SELECT status, COUNT(*) FROM notebook_extractions "
                "WHERE chat_id = ? AND work_key LIKE '%:part%' "
                "GROUP BY status", (chat_id,)).fetchall()
        return {r[0]: r[1] for r in rows}

    @pytest.mark.anyio
    async def test_twenty_long_messages_are_all_read_in_one_reading(
            self, client, monkeypatch) -> None:
        sent = _provider(monkeypatch)
        chat_id, unread = _long_chat(client, 999)
        expected = _expected_calls(chat_id)

        await notebook_worker.Worker()._read_offer(chat_id)

        skipped = [i for i in unread if i not in _read(chat_id, unread)]
        assert skipped == [], f"{len(skipped)} of {TWENTY} were never read"
        assert len(sent) == expected > 1, (
            "ground: these messages really do need more than one call")

    @pytest.mark.anyio
    async def test_POSITIVE_two_thousand_characters_take_five_calls(
            self, client, monkeypatch) -> None:
        sent = _provider(monkeypatch)
        chat_id, unread = _long_chat(client, 2000)
        expected = _expected_calls(chat_id)

        w = notebook_worker.Worker()
        await w._read_offer(chat_id)

        assert len(sent) == expected
        assert expected == 5, (
            "the documented figure (decision 68: 1 call becomes 5) moved")
        assert _read(chat_id, unread) == unread
        assert w.status(now=0.0, chat_id=chat_id)["read_calls"] == 5

    @pytest.mark.anyio
    async def test_ZEMIN_short_messages_take_one_call(
            self, client, monkeypatch) -> None:
        sent = _provider(monkeypatch)
        chat_id, unread = _long_chat(client, 100)

        w = notebook_worker.Worker()
        await w._read_offer(chat_id)

        assert len(sent) == 1
        assert _read(chat_id, unread) == unread
        assert w.status(now=0.0, chat_id=chat_id)["read_calls"] == 1

    @pytest.mark.anyio
    async def test_no_message_is_marked_read_unless_it_was_sent(
            self, client, monkeypatch) -> None:
        sent = _provider(monkeypatch)
        chat_id, unread = _long_chat(client, 999)

        await notebook_worker.Worker()._read_offer(chat_id)

        read = _read(chat_id, unread)
        assert read, "ground: something was read"
        for n, mid in enumerate(unread):
            if mid in read:
                assert any(f"<m{n:02d}>" in body for body in sent), (
                    f"message {mid} was marked read and never sent")

    @pytest.mark.anyio
    async def test_follow_on_calls_stop_at_the_days_ceiling(
            self, client, monkeypatch) -> None:
        sent = _provider(monkeypatch)
        chat_id, unread = _long_chat(client, 2000)
        # The day's ceiling alone: the half-of-what-is-left rule is
        # switched off here and has its own test below.
        monkeypatch.setattr(notebook_worker, "FOLLOW_ON_SHARE", 1)
        for _ in range(config.NOTEBOOK_DAILY_CALL_CAP - 2):
            notebook_worker._claim_one()

        await notebook_worker.Worker()._read_offer(chat_id)

        assert len(sent) == 2, "the read did not stop at the day's ceiling"
        read = _read(chat_id, unread)
        assert read == unread[:len(read)] and 0 < len(read) < len(unread)
        with get_db() as con:
            cursor = con.execute(
                "SELECT COALESCE(MAX(to_message_id), 0) FROM "
                "notebook_extractions WHERE chat_id = ? AND status = 'done'",
                (chat_id,)).fetchone()[0]
        assert all(i > cursor for i in unread if i not in read), (
            "a message the ceiling stopped was left under the cursor, "
            "counted read without being sent")

    @pytest.mark.anyio
    async def test_every_follow_on_call_is_halved_under_pressure(
            self, client, monkeypatch) -> None:
        """28 a: under pressure the batch halves, the threshold does not.
        The read's stretch is the halved batch, and its follow-on calls stay
        inside it."""
        sent = _provider(monkeypatch)
        chat_id, unread = _long_chat(client, 2000)
        w = notebook_worker.Worker()
        w.breaker.failures = 1
        halved = w.batch_size_for(chat_id)
        assert halved < TWENTY, "ground: one failure halves the batch"
        expected = _expected_calls(chat_id, halved)

        await w._read_offer(chat_id)

        assert _read(chat_id, unread) == unread[:halved]
        assert len(sent) == expected > 1

    @pytest.mark.anyio
    async def test_one_message_longer_than_the_ceiling_is_read_in_parts(
            self, client, monkeypatch) -> None:
        """The clean path: the message is split into
        ceiling-sized parts, one call each, in the same turn; nothing of it
        is left unread and a note from its second part cites it."""
        chat_id, unread, sent = self._huge_chat(client, monkeypatch)

        await notebook_worker.Worker()._read_offer(chat_id)

        assert _read(chat_id, unread) == unread
        assert sum("<m00>" in b or "yyyy" in b or "zzzz" in b
                   for b in sent) >= 3, "ground: the message took 3 calls"
        assert self._parts(chat_id) == {"done": 3}
        with get_db() as con:
            cited = con.execute(
                "SELECT source_message_id FROM notebook_entries "
                "WHERE chat_id = ? AND text = ?",
                (chat_id, fact()["text"])).fetchone()
        assert cited is not None and cited[0] == unread[0], (
            "the note from the second part does not cite the message")

    @pytest.mark.anyio
    async def test_a_part_the_ceiling_stopped_is_named_and_read_later(
            self, client, monkeypatch) -> None:
        """The fallback path: two calls left in the day, so parts 1 and 2
        are read and part 3 is not. It stays named `message_part_pending`,
        not dropped under a cursor that has moved on, and the next day's
        next reply reads it."""
        chat_id, unread, sent = self._huge_chat(client, monkeypatch)
        monkeypatch.setattr(notebook_worker, "FOLLOW_ON_SHARE", 1)
        for _ in range(config.NOTEBOOK_DAILY_CALL_CAP - 2):
            notebook_worker._claim_one()

        w = notebook_worker.Worker()
        await w._read_offer(chat_id)

        assert len(sent) == 2
        assert self._parts(chat_id) == {"done": 2, "skipped": 1}
        with get_db() as con:
            block = w.long_message_block(con, chat_id)
        assert block == {"pending": 1, "failed": 0, "unread": 0}

        with get_db() as con:                  # a new day
            con.execute("DELETE FROM notebook_spend")
        await w._read_offer(chat_id)
        assert self._parts(chat_id) == {"done": 3}, "the pending part was lost"


    @pytest.mark.anyio
    async def test_no_follow_on_call_starts_once_the_loop_stands_down(
            self, client, monkeypatch) -> None:
        """A vault lock grants the call already out five seconds; it must
        not start the next one. `_stand_down` takes `worker.task` first,
        which is what the stub does at the moment the first call is out."""
        import asyncio

        import openrouter

        chat_id, unread = _long_chat(client, 2000)
        w = notebook_worker.Worker()
        sent: list[int] = []

        async def reply(*a, **kw):
            sent.append(1)
            w.task = None                     # what `_stand_down` does first
            return {"id": "gen", "choices": [{
                "finish_reason": "stop",
                "message": {"content": json.dumps({"facts": []})}}],
                "usage": {"tokens_in": 10, "tokens_out": 5, "cost": 0.0001}}

        monkeypatch.setattr(openrouter, "complete", reply)
        w.task = asyncio.current_task()       # the loop that dispatched it
        # And no further planning at all: the loop-top check stops the read
        # before it asks for more work (the claim-time check, tested below,
        # would only refuse it after planning had decrypted the stretch).
        planned: list[int] = []
        real_pending = notebook_worker._next_pending_part

        def pending(*a, **kw):
            planned.append(1)
            return real_pending(*a, **kw)

        monkeypatch.setattr(notebook_worker, "_next_pending_part", pending)

        await w._read_offer(chat_id)

        assert len(sent) == 1, "a new paid call started during a stand-down"
        assert len(planned) == 1, "the read planned more after the stand-down"
        read = _read(chat_id, unread)
        assert 0 < len(read) < len(unread), "ground: the first call landed"

    @pytest.mark.anyio
    async def test_a_stand_down_during_the_follow_on_plan_claims_nothing(
            self, client, monkeypatch) -> None:
        """The same rule one step later: the lock lands while the follow-on
        call is still being planned, after the loop's own check. The claim
        re-asks, so nothing is paid and the rest stays above the cursor."""
        import asyncio

        sent = _provider(monkeypatch)
        chat_id, unread = _long_chat(client, 2000)
        w = notebook_worker.Worker()
        real_learn = notebook_worker._learn_about
        learned: list[int] = []

        async def learn(*a, **kw):
            learned.append(1)
            if len(learned) == 2:
                w.task = None                 # the lock, mid-plan
            return await real_learn(*a, **kw)

        monkeypatch.setattr(notebook_worker, "_learn_about", learn)
        w.task = asyncio.current_task()
        with get_db() as con:
            spent = con.execute(
                "SELECT COUNT(*) FROM notebook_spend_calls").fetchone()[0]

        await w._read_offer(chat_id)

        assert len(learned) == 2, "ground: the follow-on was being planned"
        assert len(sent) == 1, "a follow-on call was paid inside a stand-down"
        with get_db() as con:
            assert con.execute(
                "SELECT COUNT(*) FROM notebook_spend_calls").fetchone()[0] \
                == spent + 1, "the follow-on claimed a call it never sent"
            assert con.execute(
                "SELECT COUNT(*) FROM notebook_extractions WHERE chat_id = ? "
                "AND status IN ('running', 'failed')",
                (chat_id,)).fetchone()[0] == 0
        read = _read(chat_id, unread)
        assert 0 < len(read) < len(unread)


class TestAJobReadsTheLongMessageInPartsToo:
    @pytest.mark.anyio
    async def test_a_reading_job_reads_every_part(
            self, client, monkeypatch) -> None:
        import asyncio

        from tests.test_notebook_backfill import choose

        chat_id, unread, sent = TestALongMessageReadIsWhole._huge_chat(
            client, monkeypatch)
        choose(chat_id)
        w = notebook_worker.Worker()
        w.queue = asyncio.Queue(maxsize=8)

        assert (await w.backfill(chat_id, "all"))["started"] is True
        await w.wait_for_backfill()

        assert TestALongMessageReadIsWhole._parts(chat_id) == {"done": 3}
        assert _read(chat_id, unread) == unread
        with get_db() as con:
            state = con.execute(
                "SELECT state FROM notebook_backfill_jobs WHERE chat_id = ?",
                (chat_id,)).fetchone()[0]
        assert state == "done"


class TestReadCallsCountsOnlyCallsThatLeft:
    """The panel's "the last read took N calls" counts
    requests that reached the provider. A failure before sending is not a
    call."""

    @staticmethod
    def _failing_provider(monkeypatch, exc: Exception) -> list[int]:
        import openrouter

        tried: list[int] = []

        async def reply(*a, **kw):
            tried.append(1)
            raise exc

        monkeypatch.setattr(openrouter, "complete", reply)
        return tried

    @pytest.mark.anyio
    async def test_a_prompt_that_could_not_be_built_is_not_a_call(
            self, client, monkeypatch) -> None:
        tried = self._failing_provider(monkeypatch, RuntimeError("unused"))
        chat_id, _ = _long_chat(client, 100)

        def broken(**kw):
            raise ValueError("card could not be read")

        monkeypatch.setattr(notebook_extract, "build_user_message", broken)
        w = notebook_worker.Worker()

        assert await w._read_offer(chat_id) == "failed"
        assert tried == [], "ground: nothing reached the provider"
        assert w.status(now=0.0, chat_id=chat_id)["read_calls"] is None

    @pytest.mark.anyio
    async def test_a_request_refused_before_it_left_is_not_a_call(
            self, client, monkeypatch) -> None:
        import openrouter

        exc = openrouter.OpenRouterError("proxy refused")
        exc.reached_provider = False
        self._failing_provider(monkeypatch, exc)
        chat_id, _ = _long_chat(client, 100)
        w = notebook_worker.Worker()

        assert await w._read_offer(chat_id) == "failed"
        assert w.status(now=0.0, chat_id=chat_id)["read_calls"] is None

    @pytest.mark.anyio
    async def test_POSITIVE_a_request_that_left_and_failed_is_a_call(
            self, client, monkeypatch) -> None:
        self._failing_provider(monkeypatch, RuntimeError("read timeout"))
        chat_id, _ = _long_chat(client, 100)
        w = notebook_worker.Worker()

        assert await w._read_offer(chat_id) == "failed"
        assert w.status(now=0.0, chat_id=chat_id)["read_calls"] == 1
