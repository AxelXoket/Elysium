"""Does a sent message actually reach the notebook?

Nothing tested this. Every worker test called `_handle(chat_id)` by hand, so
all four `_offer_to_notebook` call sites could have been deleted with the whole
suite green and the feature simply never running again - which is the failure
its own docstring describes having already happened once, when only the
streaming path was wired and the plain routes silently stopped extracting.

The assertion is on the queue, not on a mock: the point is that the real send
path puts a real chat id somewhere the real worker will find it.
"""
from __future__ import annotations

import asyncio

import pytest

import notebook_worker

from tests.conftest import make_character, make_chat


@pytest.fixture
def streaming(monkeypatch):
    """A fake SSE provider. Without one the stream errors before the turn is
    persisted, and the offer legitimately never happens - the test would be
    asserting on a path that never ran."""
    import routers.completions as cr

    def fake_stream(*a, **kw):
        async def gen():
            for piece in ("hel", "lo"):
                yield piece
        return gen()

    monkeypatch.setattr(cr, "complete_stream", fake_stream)


@pytest.fixture
def watching(monkeypatch):
    """A live queue on the module-level worker, and nothing else changed."""
    seen: list[int] = []
    w = notebook_worker.worker
    w.queue = asyncio.Queue(maxsize=8)

    real = w.offer

    def spy(chat_id: int) -> None:
        seen.append(chat_id)
        real(chat_id)

    monkeypatch.setattr(w, "offer", spy)
    yield seen
    w.queue = None


class TestEverySendPathTellsTheNotebook:
    def test_streaming_send(self, client, streaming, watching) -> None:
        chat_id = make_chat(client, make_character(client))
        resp = client.post(f"/api/v1/chats/{chat_id}/complete/stream",
                           json={"message": "hello",
                                 "model_id": "vendor/model"})
        assert resp.status_code == 200, resp.text
        resp.read()          # drain, so the exchange completes
        assert watching == [chat_id]

    def test_plain_send(self, client, provider, watching) -> None:
        """The path that was missed. A feature wired only into streaming
        works in the live UI and is dead for any client using these."""
        chat_id = make_chat(client, make_character(client))
        resp = client.post(f"/api/v1/chats/{chat_id}/complete",
                           json={"message": "hello",
                                 "model_id": "vendor/model"})
        assert resp.status_code == 200, resp.text
        assert watching == [chat_id]

    def test_regenerate(self, client, provider, watching) -> None:
        chat_id = make_chat(client, make_character(client))
        client.post(f"/api/v1/chats/{chat_id}/complete",
                    json={"message": "hello", "model_id": "vendor/model"})
        watching.clear()

        msgs = client.get(f"/api/v1/chats/{chat_id}/messages").json()
        last = [m for m in msgs if m["role"] == "assistant"][-1]
        resp = client.post(
            f"/api/v1/chats/{chat_id}/messages/{last['id']}/regenerate",
            json={"model_id": "vendor/model"})
        assert resp.status_code == 200, resp.text
        assert watching == [chat_id]

    def test_edit(self, client, provider, watching) -> None:
        chat_id = make_chat(client, make_character(client))
        client.post(f"/api/v1/chats/{chat_id}/complete",
                    json={"message": "hello", "model_id": "vendor/model"})
        watching.clear()

        msgs = client.get(f"/api/v1/chats/{chat_id}/messages").json()
        first_user = [m for m in msgs if m["role"] == "user"][0]
        resp = client.post(
            f"/api/v1/chats/{chat_id}/messages/{first_user['id']}/edit",
            json={"message": "hello again", "model_id": "vendor/model"})
        assert resp.status_code == 200, resp.text
        assert watching == [chat_id]


class TestTheOfferCannotBreakTheSend:
    def test_a_worker_that_is_not_running_does_not_fail_the_turn(
            self, client, provider) -> None:
        """The whole reason `offer` swallows everything. A background feature
        that can make a message fail to send is not a feature."""
        notebook_worker.worker.queue = None
        chat_id = make_chat(client, make_character(client))
        resp = client.post(f"/api/v1/chats/{chat_id}/complete",
                           json={"message": "hello",
                                 "model_id": "vendor/model"})
        assert resp.status_code == 200, resp.text

    def test_an_offer_that_raises_does_not_fail_the_turn(
            self, client, provider, monkeypatch) -> None:
        def boom(chat_id: int) -> None:
            raise RuntimeError("the queue exploded")

        monkeypatch.setattr(notebook_worker.worker, "offer", boom)
        chat_id = make_chat(client, make_character(client))
        resp = client.post(f"/api/v1/chats/{chat_id}/complete",
                           json={"message": "hello",
                                 "model_id": "vendor/model"})
        assert resp.status_code == 200, resp.text
