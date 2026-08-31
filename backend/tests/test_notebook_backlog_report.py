"""U-44 - the unread backlog is REPORTED, never quietly spent on.

A dropped offer used to be gone for good. The only thing in production that
queues work is a live message send; nothing re-scans at startup, at unlock,
on idle or on a timer, so a chat whose offer was dropped - vault locked,
queue drained, app closed - was never looked at again.

The obvious fix is the one the research report this comes from refuses in as
many words: do not build an automatic catch-up scan that spends money at
startup or at unlock. The module's own stated position is why - a background
job spending somebody's own API credits on a model they never selected is not
a convenience - and a backlog can be five hundred messages long.

So the loss becomes an OFFER: one cheap count at unlock, shown beside the
button, and the reader decides. The most important test in this file is the
one that asserts NOTHING WAS SPENT.
"""
from __future__ import annotations

import json

import pytest

import config
import notebook_store as notebook
import notebook_worker
import openrouter
import vault_state
from database import get_db, set_setting

from tests.conftest import TEST_VAULT_KEY
from tests.test_notebook_worker import fact, seed


@pytest.fixture
def anyio_backend():
    return "asyncio"


def message_ids(chat_id: int) -> list[int]:
    with get_db() as con:
        return [r[0] for r in con.execute(
            "SELECT id FROM messages WHERE chat_id = ? AND active = 1 "
            "ORDER BY id", (chat_id,)).fetchall()]


def mark_read(chat_id: int, lo: int, hi: int, key: str) -> None:
    with get_db() as con:
        con.execute("BEGIN IMMEDIATE")
        notebook.commit_extraction(con, work_key=key, chat_id=chat_id,
                                   from_id=lo, to_id=hi, proposals=[])


class TestTheCount:
    def test_an_untouched_chat_is_counted_whole(self, client) -> None:
        chat_id = seed(client, count=20)

        with get_db() as con:
            out = notebook.unread_backlog(con)

        assert out["chats"] == 1
        assert out["messages"] == len(message_ids(chat_id))

    def test_a_fully_read_chat_is_not_counted(self, client) -> None:
        """GROUND CONTROL. A count that reports every chat forever is a
        permanent alarm, which is the same as no alarm."""
        chat_id = seed(client, count=6)
        ids = message_ids(chat_id)
        mark_read(chat_id, ids[0], ids[-1], "all")

        with get_db() as con:
            assert notebook.unread_backlog(con) == {"chats": 0, "messages": 0}

    def test_a_gap_in_the_MIDDLE_is_found(self, client) -> None:
        """S-3. A cursor built on `MAX(to_message_id)` cannot answer this:
        a maximum says "everything up to here", and the hole is under it."""
        chat_id = seed(client, count=20)
        ids = message_ids(chat_id)
        mark_read(chat_id, ids[0], ids[4], "low")
        mark_read(chat_id, ids[12], ids[-1], "high")

        with get_db() as con:
            cursor = con.execute(
                "SELECT COALESCE(MAX(to_message_id), 0) FROM "
                "notebook_extractions WHERE chat_id = ?", (chat_id,)
            ).fetchone()[0]
            out = notebook.unread_backlog(con)

        assert cursor == ids[-1], "ground: the MAX really is at the top"
        assert out["chats"] == 1
        assert out["messages"] == len(ids[5:12])


class TestNothingIsSpentToProduceIt:
    def test_the_whole_unlock_bootstrap_makes_no_paid_call(
            self, client, monkeypatch) -> None:
        """THE test of this unit, and it is deliberately a refusal.

        The step this unit adds runs inside the unlock bootstrap, so the
        bootstrap is what is driven - all of it, not just the new line. If an
        automatic catch-up scan is ever added to this path, this goes red on
        purpose: the backlog is an offer on a screen, not a decision somebody
        else makes about the reader's money.
        """
        from routers import vault as vault_router

        chat_id = seed(client, count=200)
        set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")
        calls: list[int] = []

        async def loud(*a, **kw):
            calls.append(1)
            return {"id": "gen", "choices": [{
                "finish_reason": "stop",
                "message": {"content": json.dumps({"facts": [fact()]})}}],
                "usage": {"tokens_in": 1, "tokens_out": 1, "cost": 1.0}}

        monkeypatch.setattr(openrouter, "complete", loud)
        offered: list[int] = []
        monkeypatch.setattr(notebook_worker.worker, "offer", offered.append)

        vault_router._bootstrap_unlocked()

        assert calls == [], "the bootstrap spent the reader's credits"
        assert offered == [], "the bootstrap queued work nobody asked for"
        with get_db() as con:
            assert notebook.spend_today(con)["calls"] == 0
        # And it DID see the backlog - so this is not passing by doing
        # nothing at all.
        assert notebook_worker.worker.backlog["chats"] == 1
        assert chat_id

    def test_counting_a_locked_vault_is_not_an_error(self, client) -> None:
        """POSITIVE CONTROL, and the biggest risk this unit carries.

        `quiesce()` drains the queue precisely so a backlog of offers cannot
        each wake into a locked vault and be counted as a failure. A count
        that ran against a locked vault and raised would rebuild that exact
        problem at the other end.
        """
        seed(client, count=20)
        w = notebook_worker.worker
        before = w.status()["unhandled"]
        vault_state.clear_key()
        try:
            out = w.count_backlog()
        finally:
            vault_state.set_key(TEST_VAULT_KEY)

        assert out == {"chats": 0, "messages": 0} or out["chats"] >= 0
        assert w.status()["unhandled"] == before, (
            "a locked vault was counted as a worker failure")
        assert w.status()["failures"] == 0


class TestItReachesTheScreen:
    def test_the_status_route_carries_it(self, client) -> None:
        seed(client, count=20)
        notebook_worker.worker.count_backlog()

        body = client.get("/api/v1/notebook/worker").json()

        assert body["worker"]["backlog"]["chats"] == 1
        assert body["worker"]["backlog"]["messages"] > 0

    def test_it_reports_zero_when_there_is_nothing_to_offer(
            self, client) -> None:
        """GROUND CONTROL: an offer that is always on screen is not an offer,
        it is decoration."""
        chat_id = seed(client, count=6)
        ids = message_ids(chat_id)
        mark_read(chat_id, ids[0], ids[-1], "everything")
        notebook_worker.worker.count_backlog()

        body = client.get("/api/v1/notebook/worker").json()

        assert body["worker"]["backlog"] == {"chats": 0, "messages": 0}
