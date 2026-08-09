"""The non-streaming write was the last unguarded success-path write.

`POST /chats/{id}/complete` calls the provider FIRST and writes both rows
afterwards, so there is a window seconds long in which the chat can be deleted.
The write had no existence guard, so that ordinary race reached the assistant
INSERT, tripped the chat_id foreign key, and surfaced as a 500 - in a module
whose own chat-creation path already refuses to let an FK become a 500.

It also ran on the event loop, alone among the success-path writes in the file,
which means it froze every live SSE stream (and therefore somebody else's audio)
for the duration of a two-insert transaction.
"""
from __future__ import annotations

import asyncio
import threading

import pytest

import database
import routers.completions as completions
from tests.conftest import get_messages, make_character, make_chat

BODY = {"message": "How are you?", "model_id": "test/model-1"}


# ── the deleted-chat race ────────────────────────────────────────────────────

def _vanishing_provider(monkeypatch, chat: int) -> None:
    """Stand in for "the user deleted this chat while the model was typing"."""

    async def _vanish(messages, model_id, gen_params, provider_policy, **kwargs):
        with database.get_db() as con:
            con.execute("DELETE FROM messages WHERE chat_id = ?", (chat,))
            con.execute("DELETE FROM chats WHERE id = ?", (chat,))
        return {"choices": [{"message": {"content": "a reply nobody asked for"}}]}

    monkeypatch.setattr(completions, "complete", _vanish)


def test_a_chat_deleted_while_the_provider_answered_is_a_clean_404(client, monkeypatch):
    char = make_character(client, first_mes="Hi.")
    chat = make_chat(client, char)
    _vanishing_provider(monkeypatch, chat)

    resp = client.post(f"/api/v1/chats/{chat}/complete", json=BODY)
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "chat_not_found"


def test_the_vanished_chat_is_not_resurrected_by_the_reply(client, monkeypatch):
    char = make_character(client, first_mes="Hi.")
    chat = make_chat(client, char)
    _vanishing_provider(monkeypatch, chat)

    client.post(f"/api/v1/chats/{chat}/complete", json=BODY)

    with database.get_db() as con:
        chats_left = con.execute("SELECT COUNT(*) c FROM chats").fetchone()["c"]
        msgs_left = con.execute(
            "SELECT COUNT(*) c FROM messages WHERE chat_id = ?", (chat,)
        ).fetchone()["c"]
    assert chats_left == 0
    assert msgs_left == 0, "an orphan user/assistant pair survived the delete"


def test_neither_row_lands_when_the_guard_refuses(client, monkeypatch):
    """Guard and inserts share one transaction, so a refusal writes nothing -
    not even the user's message."""
    char = make_character(client, first_mes="Hi.")
    chat = make_chat(client, char)
    _vanishing_provider(monkeypatch, chat)

    client.post(f"/api/v1/chats/{chat}/complete", json=BODY)

    with database.get_db() as con:
        rows = con.execute(
            "SELECT role FROM messages WHERE content IN (?, ?)",
            ("How are you?", "a reply nobody asked for"),
        ).fetchall()
    assert rows == []


# ── off the event loop ───────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_the_write_does_not_run_on_the_event_loop(anyio_backend, client,
                                                        provider, monkeypatch):
    """Proof by starvation, not by inspection.

    The write is made to wait on an Event that only ANOTHER asyncio task can
    set. If the write runs on the loop it holds the only thread there is, that
    task never gets to run, and the wait times out. If it runs on a worker
    thread the loop stays free and the gate opens.
    """
    char = make_character(client, first_mes="Hi.")
    chat = make_chat(client, char)

    real = completions._persist_exchange_sync
    gate = threading.Event()
    opened_by_the_loop = []

    def blocking(*args):
        assert gate.wait(3.0), "the event loop was frozen by the write"
        return real(*args)

    monkeypatch.setattr(completions, "_persist_exchange_sync", blocking)

    async def open_the_gate():
        await asyncio.sleep(0.05)
        opened_by_the_loop.append(True)
        gate.set()

    body = completions.CompleteRequest(**BODY)
    opener = asyncio.ensure_future(open_the_gate())
    try:
        out = await completions.complete_chat(chat, body)
    finally:
        opener.cancel()

    assert opened_by_the_loop == [True]
    assert out["assistant_message"]["content"] == "fake assistant reply"


# ── and the happy path is unchanged ──────────────────────────────────────────

def test_the_response_shape_is_unchanged(client, provider):
    char = make_character(client, first_mes="Hi.")
    chat = make_chat(client, char)

    data = client.post(f"/api/v1/chats/{chat}/complete", json=BODY).json()
    assert data["chat_id"] == chat
    assert data["model_id"] == "test/model-1"
    assert data["user_message"]["content"] == "How are you?"
    assert data["user_message"]["attachments"] == []
    assert data["assistant_message"]["content"] == "fake assistant reply"
    assert data["notices"] == []

    assert [m["role"] for m in get_messages(client, chat)] == [
        "assistant", "user", "assistant",
    ]


def test_the_chat_still_records_the_model_it_used(client, provider):
    char = make_character(client, first_mes="Hi.")
    chat = make_chat(client, char)
    client.post(f"/api/v1/chats/{chat}/complete", json=BODY)
    assert client.get(f"/api/v1/chats/{chat}").json()["model_id"] == "test/model-1"
