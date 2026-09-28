"""Decision 58 a, the server half: `worker.reading` on GET /notebook/worker.

The Notes tab polls it fast after a reply and refreshes the note list once
it turns false. So it must be true for the whole time a read of that chat is
waiting or out - from the offer in the queue, through the moment the loop
takes it off the queue and the awaits before the claim, to the settle - and
false once it is over. A false while the read is still coming shows the old
list and stops asking; a true that never ends asks until the cap.

Every test drives the real `Worker.run` loop and the real `status()`; the
only fakes are the provider and one gate that holds the loop still.
"""
from __future__ import annotations

import asyncio
import json

import pytest

import config
import database
import notebook_worker
from tests.test_notebook_worker import fact, seed

TWENTY = config.NOTEBOOK_EXTRACT_EVERY_TURNS


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _provider(monkeypatch, hold: asyncio.Event, entered: asyncio.Event):
    import openrouter

    async def reply(*a, **kw):
        entered.set()
        await hold.wait()
        return {"id": "gen-1", "choices": [{
            "finish_reason": "stop",
            "message": {"content": json.dumps({"facts": [fact()]})}}],
            "usage": {"tokens_in": 10, "tokens_out": 5, "cost": 0.0001}}

    monkeypatch.setattr(openrouter, "complete", reply)


async def _until(predicate, *, steps: int = 200) -> None:
    for _ in range(steps):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the condition never came true")


class TestTheReadingFlag:
    def test_no_chat_asked_is_null(self) -> None:
        w = notebook_worker.Worker()
        assert w.status(now=0.0)["reading"] is None
        # POSITIVE: the same worker answers a bool once a chat is named.
        assert w.status(now=0.0, chat_id=1)["reading"] is False

    @pytest.mark.anyio
    async def test_a_queued_offer_reads_true_for_its_chat_only(self) -> None:
        w = notebook_worker.Worker()
        w.queue = asyncio.Queue(maxsize=8)
        assert w.status(chat_id=5)["reading"] is False, "ground: idle"

        w.offer(5)

        assert w.status(chat_id=5)["reading"] is True
        assert w.status(chat_id=6)["reading"] is False

    @pytest.mark.anyio
    async def test_it_stays_true_from_the_queue_to_the_settle(
            self, client, monkeypatch) -> None:
        chat_id = seed(client, TWENTY, greeting=False)
        database.set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")
        hold, entered = asyncio.Event(), asyncio.Event()
        _provider(monkeypatch, hold, entered)

        # Hold the loop between the dequeue and the claim: the stretch where
        # the chat is neither in the queue nor in `_active_chats`.
        gate, at_gate = asyncio.Event(), asyncio.Event()
        real_learn = notebook_worker._learn_about

        async def held_learn(*a, **kw):
            at_gate.set()
            await gate.wait()
            return await real_learn(*a, **kw)

        monkeypatch.setattr(notebook_worker, "_learn_about", held_learn)

        w = notebook_worker.Worker()
        w.queue = asyncio.Queue(maxsize=8)
        loop_task = asyncio.create_task(w.run())
        try:
            w.offer(chat_id)
            await asyncio.wait_for(at_gate.wait(), 5)
            assert w.queue.qsize() == 0, "ground: it is off the queue"
            assert chat_id not in w._active_chats, "ground: not claimed yet"
            assert w.status(chat_id=chat_id)["reading"] is True, (
                "the chat read as not being read between the queue and "
                "the claim")

            gate.set()
            await asyncio.wait_for(entered.wait(), 5)
            assert w.status(chat_id=chat_id)["reading"] is True, (
                "the chat read as not being read with its call out")

            hold.set()
            await _until(lambda: w._reading is None)
            assert w.status(chat_id=chat_id)["reading"] is False, (
                "the read ended and the flag stayed up")
        finally:
            loop_task.cancel()
            gate.set()
            hold.set()
            await asyncio.gather(loop_task, return_exceptions=True)
            if w._reading is not None:
                await asyncio.gather(w._reading, return_exceptions=True)

    def test_the_route_serves_it(self, client) -> None:
        with_chat = client.get("/api/v1/notebook/worker?chat_id=1").json()
        without = client.get("/api/v1/notebook/worker").json()
        assert with_chat["worker"]["reading"] is False
        assert without["worker"]["reading"] is None

    @pytest.mark.anyio
    async def test_a_reading_job_unit_out_reads_true(
            self, client, monkeypatch) -> None:
        """A job's notes land in the same list, and its unit never passes
        through the queue or `_reading`: its claim is what says so."""
        from tests.test_notebook_backfill import choose

        chat_id = seed(client, TWENTY, greeting=False)
        choose(chat_id)
        hold, entered = asyncio.Event(), asyncio.Event()
        _provider(monkeypatch, hold, entered)
        w = notebook_worker.Worker()
        w.queue = asyncio.Queue(maxsize=8)
        try:
            assert (await w.backfill(chat_id, "next"))["started"] is True
            await asyncio.wait_for(entered.wait(), 5)
            assert w._reading is None, "ground: the ordinary reader is idle"
            assert w.status(chat_id=chat_id)["reading"] is True
        finally:
            hold.set()
            await w.wait_for_backfill()
        assert w.status(chat_id=chat_id)["reading"] is False
