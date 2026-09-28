"""Decision 29 a: in a chat that opens with the character's greeting, the
first read is the greeting and twenty messages, in one call.

The greeting is a message, so in such a chat the reader's own message is
the twentieth and the reply that crossed the threshold is the twenty-first.
A plain LIMIT of twenty read one to twenty and left that reply for the next
read, and the same one-message shift came back on every read after it.

Every test drives the real `Worker._handle` and the real `_plan_work`; the
only fake is the provider, which counts itself. Nothing here reads source
text. The threshold is imported, never retyped.
"""
from __future__ import annotations

import json

import pytest

import config
import database
import notebook_worker
from database import get_db

from tests.test_notebook_worker import fact, seed

TWENTY = config.NOTEBOOK_EXTRACT_EVERY_TURNS


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _provider(monkeypatch) -> list[int]:
    import openrouter

    calls: list[int] = []

    async def reply(*a, **kw):
        calls.append(1)
        return {"id": f"gen-{len(calls)}", "choices": [{
            "finish_reason": "stop",
            "message": {"content": json.dumps({"facts": [fact()]})}}],
            "usage": {"tokens_in": 10, "tokens_out": 5, "cost": 0.0001}}

    monkeypatch.setattr(openrouter, "complete", reply)
    return calls


def _messages(chat_id: int) -> list[tuple[int, str]]:
    with get_db() as con:
        return [(r[0], r[1]) for r in con.execute(
            "SELECT id, role FROM messages WHERE chat_id = ? AND active = 1 "
            "ORDER BY id", (chat_id,)).fetchall()]


def _done(chat_id: int) -> list[tuple[int, int]]:
    with get_db() as con:
        return [(r[0], r[1]) for r in con.execute(
            "SELECT from_message_id, to_message_id FROM notebook_extractions "
            "WHERE chat_id = ? AND status = 'done' ORDER BY rowid",
            (chat_id,)).fetchall()]


def _covered(chat_id: int, span: tuple[int, int]) -> list[tuple[int, str]]:
    lo, hi = span
    return [m for m in _messages(chat_id) if lo <= m[0] <= hi]


def _add_turns(chat_id: int, count: int) -> None:
    """`count` more messages, alternating on from the chat's last role."""
    role = "assistant" if _messages(chat_id)[-1][1] == "user" else "user"
    with get_db() as con:
        for i in range(count):
            con.execute(
                "INSERT INTO messages (chat_id, role, content, active) "
                "VALUES (?,?,?,1)",
                (chat_id, role, f"later {i}: her brother owns the mill"))
            role = "user" if role == "assistant" else "assistant"


def _worker() -> notebook_worker.Worker:
    database.set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")
    return notebook_worker.Worker()


class TestTheGreetingChunk:
    @pytest.mark.anyio
    async def test_a_greeting_chat_reads_twenty_one_in_one_call(
            self, client, monkeypatch) -> None:
        calls = _provider(monkeypatch)
        chat_id = seed(client, TWENTY)
        msgs = _messages(chat_id)
        assert len(msgs) == TWENTY + 1, "ground: greeting plus twenty"
        assert msgs[0][1] == "assistant", "ground: the chat opens on a greeting"
        assert msgs[-1][1] == "assistant", "ground: the last one is a reply"

        await _worker()._handle(chat_id)

        assert len(calls) == 1, "one call, no extra call for the reply"
        assert _done(chat_id) == [(msgs[0][0], msgs[-1][0])], (
            "the read did not cover the greeting through the reply")
        assert len(_covered(chat_id, _done(chat_id)[0])) == TWENTY + 1

    @pytest.mark.anyio
    async def test_the_next_read_after_the_greeting_chunk_is_twenty_and_ends_on_a_reply(  # noqa: E501
            self, client, monkeypatch) -> None:
        calls = _provider(monkeypatch)
        chat_id = seed(client, TWENTY)
        w = _worker()
        await w._handle(chat_id)
        assert len(calls) == 1, "ground: the greeting chunk was read"

        # GROUND: nineteen more is below the threshold, with the greeting
        # already read the rule is the plain twenty again.
        _add_turns(chat_id, TWENTY - 1)
        await w._handle(chat_id)
        assert len(calls) == 1, "nineteen new messages started a read"

        _add_turns(chat_id, 1)
        await w._handle(chat_id)
        assert len(calls) == 2, "the twentieth new message did not read"
        second = _covered(chat_id, _done(chat_id)[-1])
        assert len(second) == TWENTY
        assert second[-1][1] == "assistant", "the read ended on the reader"
        assert second[0][0] == _done(chat_id)[0][1] + 1, (
            "the second read did not start right after the greeting chunk")

    @pytest.mark.anyio
    async def test_ZEMIN_a_chat_without_a_greeting_reads_twenty(
            self, client, monkeypatch) -> None:
        calls = _provider(monkeypatch)
        chat_id = seed(client, TWENTY, greeting=False)
        msgs = _messages(chat_id)
        assert len(msgs) == TWENTY and msgs[0][1] == "user", (
            "ground: twenty messages, the reader's first")

        await _worker()._handle(chat_id)

        assert len(calls) == 1
        assert _done(chat_id) == [(msgs[0][0], msgs[-1][0])]
        assert msgs[-1][1] == "assistant", "a plain chat also ends on a reply"

    @pytest.mark.anyio
    async def test_a_greeting_chat_at_twenty_messages_does_not_read(
            self, client, monkeypatch) -> None:
        """POSITIVE CONTROL for the wait: greeting plus nineteen, the last
        one the reader's, is not yet a read. Without this the first test
        passes on a planner that reads at twenty and happens to be handed
        twenty-one."""
        calls = _provider(monkeypatch)
        chat_id = seed(client, TWENTY - 1)
        msgs = _messages(chat_id)
        assert len(msgs) == TWENTY and msgs[-1][1] == "user"

        await _worker()._handle(chat_id)

        assert calls == [] and _done(chat_id) == [], (
            "the greeting chat was read on its twentieth message, the "
            "reader's own, leaving the reply for the next read")

    @pytest.mark.anyio
    async def test_a_deleted_greeting_is_not_counted(
            self, client, monkeypatch) -> None:
        """The greeting is the chat's oldest ACTIVE row. Once it is
        deleted the chat opens on the reader and reads at twenty."""
        calls = _provider(monkeypatch)
        chat_id = seed(client, TWENTY)
        greeting = _messages(chat_id)[0][0]
        with get_db() as con:
            con.execute("UPDATE messages SET active = 0 WHERE id = ?",
                        (greeting,))
        msgs = _messages(chat_id)
        assert len(msgs) == TWENTY and msgs[0][1] == "user"

        await _worker()._handle(chat_id)

        assert len(calls) == 1
        assert _done(chat_id) == [(msgs[0][0], msgs[-1][0])]


# -- The two doors take turns on one chat ------------------------------------
#
# Since 29 a the ordinary reader plans the greeting and twenty while a job
# unit plans twenty, so the in-flight net can no longer be keyed on the
# range alone: it refuses a second read of the same CHAT while one is out
# (`test_notebook_two_entry_points.py::TestOneRangeIsClaimedOnce` pins the
# double pay). That makes which door yields a real question, and these two
# pin the answer from each side.


def _spent() -> int:
    import notebook_store as notebook
    with get_db() as con:
        return notebook.spend_today(con)["calls"]


def _job_state(chat_id: int) -> tuple[str, str | None]:
    with get_db() as con:
        row = con.execute(
            "SELECT state, last_error FROM notebook_backfill_jobs "
            "WHERE chat_id = ? ORDER BY id DESC LIMIT 1", (chat_id,)).fetchone()
    return (row[0], row[1])


class TestTheTwoDoorsTakeTurns:
    @pytest.mark.anyio
    async def test_the_ordinary_reader_yields_to_a_job_that_started_meanwhile(
            self, client, monkeypatch) -> None:
        """The ordinary reader asks whether a job holds the chat before its
        awaits; a job pressed during them owns the chat by the time of the
        claim (3.12). It is asked again at the claim, so the reader backs
        off rather than claiming and leaving the job's unit refused."""
        chat_id = seed(client, TWENTY)
        w = _worker()
        answers = iter([False])            # free at the top, held at the claim
        monkeypatch.setattr(w, "_job_holds",
                            lambda cid: next(answers, True))
        before = _spent()

        assert await w._prepare(chat_id) is None
        assert _spent() == before, "the reader claimed a chat a job holds"
        assert chat_id not in w._active_chats

    @pytest.mark.anyio
    async def test_POSITIVE_a_chat_no_job_holds_is_claimed(
            self, client, monkeypatch) -> None:
        chat_id = seed(client, TWENTY)
        w = _worker()
        monkeypatch.setattr(w, "_job_holds", lambda cid: False)
        before = _spent()

        assert await w._prepare(chat_id) is not None
        assert _spent() == before + 1, "ground: the same chat is claimable"

    @pytest.mark.anyio
    async def test_a_job_pressed_while_an_ordinary_read_is_out_waits_for_it(
            self, client, monkeypatch) -> None:
        """The other side of the window: the ordinary read claimed while the
        job's row was still being written. The job's unit used to reach the
        claim, be refused, and close the job `failed` having read nothing."""
        import asyncio

        import openrouter
        from tests.test_notebook_backfill import choose

        chat_id = seed(client, TWENTY)
        choose(chat_id)
        hold, entered = asyncio.Event(), asyncio.Event()
        calls: list[int] = []

        async def reply(*a, **kw):
            calls.append(1)
            if len(calls) == 1:
                entered.set()
                await hold.wait()
            return {"id": f"gen-{len(calls)}", "choices": [{
                "finish_reason": "stop",
                "message": {"content": json.dumps({"facts": [fact()]})}}],
                "usage": {"tokens_in": 10, "tokens_out": 5, "cost": 0.0001}}

        monkeypatch.setattr(openrouter, "complete", reply)
        w = notebook_worker.Worker()
        w.queue = asyncio.Queue(maxsize=8)
        gate, in_preview = asyncio.Event(), asyncio.Event()
        real_preview = w.plan_backfill

        async def slow_preview(*a, **kw):
            out = await real_preview(*a, **kw)
            in_preview.set()
            await gate.wait()
            return out

        monkeypatch.setattr(w, "plan_backfill", slow_preview)

        job = asyncio.create_task(w.backfill(chat_id, "next"))
        await in_preview.wait()
        read = asyncio.create_task(w._handle(chat_id))
        w._reading = read                      # what `run()` does
        await entered.wait()
        assert chat_id in w._active_chats, "ground: the ordinary read is out"

        gate.set()
        assert (await job)["started"] is True
        # Give the job's loop every chance to reach its unit while the read
        # is still out. Bounded: a refused unit ends the loop at once.
        for _ in range(50):
            if w._backfill_task is None or w._backfill_task.done():
                break
            await asyncio.sleep(0.01)
        hold.set()
        assert await read == "settled"
        await w.wait_for_backfill()

        state, why = _job_state(chat_id)
        assert state == "done", (
            f"the job closed {state} ({why}) instead of waiting for the "
            "ordinary read that was already out")
        assert len(calls) == 1, "the stretch the ordinary read took was paid twice"
