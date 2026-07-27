"""v1.1 C3: POST /chats/{id}/messages/{mid}/edit(+/stream).

Provider-first + one atomic swap (regenerate's data-protection law): nothing
is written until the new reply fully exists, and the I6 optimistic-concurrency
snapshot (user content + updated_at + chat tail id) must still hold at swap
time or the endpoint refuses with edit_conflict and the chat stays
byte-identical.
"""

import asyncio
import json

import pytest
from fastapi import HTTPException

import database
from routers.completions import (
    EditConflictError,
    EditRequest,
    _finalize_edit,
    _validate_edit_target,
    edit_message_stream,
)

from conftest import make_character, make_chat, get_messages


def _run(coro):
    return asyncio.run(coro)


def _install_stream(monkeypatch, deltas, mid_stream=None):
    """Fake provider stream; optionally run `mid_stream()` after the first
    delta - the window where concurrent mutations race the edit."""
    import routers.completions as cr

    def fake_stream(messages, model_id, gen_params, provider):
        async def gen():
            try:
                for i, d in enumerate(deltas):
                    yield d
                    if i == 0 and mid_stream is not None:
                        mid_stream()
            except GeneratorExit:
                return
        return gen()

    monkeypatch.setattr(cr, "complete_stream", fake_stream)


def _seed_conversation(client) -> tuple[int, int, int]:
    """chat with [first_mes, user 'original question', assistant 'old reply'].
    Returns (chat_id, user_id, old_assistant_id)."""
    char_id = make_character(client)
    chat_id = make_chat(client, char_id)
    with database.get_db() as con:
        user_id = con.execute(
            "INSERT INTO messages (chat_id, role, content) "
            "VALUES (?, 'user', 'original question')", (chat_id,),
        ).lastrowid
        old_asst_id = con.execute(
            "INSERT INTO messages (chat_id, role, content) "
            "VALUES (?, 'assistant', 'old reply')", (chat_id,),
        ).lastrowid
    return chat_id, user_id, old_asst_id


async def _drive_edit(chat_id, message_id, new_text, stop_after_type=None):
    """Run the edit stream; consume fully, or aclose() after an event type."""
    body = EditRequest(message=new_text, model_id="test/model-1")
    resp = await edit_message_stream(chat_id, message_id, body)
    agen = resp.body_iterator
    seen = []
    async for chunk in agen:
        evt = json.loads(chunk[len("data: "):].strip())
        seen.append(evt)
        if stop_after_type is not None and evt["type"] == stop_after_type:
            break
    if stop_after_type is not None:
        await agen.aclose()
    return seen


# ── Happy path ───────────────────────────────────────────────────────────────

def test_edit_stream_happy_path(client, monkeypatch):
    _install_stream(monkeypatch, ["New ", "answer."])
    chat_id, user_id, old_asst_id = _seed_conversation(client)

    events = _run(_drive_edit(chat_id, user_id, "edited question"))

    # user_message first: SAME id, NEW content (preview before persistence).
    assert events[0]["type"] == "user_message"
    assert events[0]["message"]["id"] == user_id
    assert events[0]["message"]["content"] == "edited question"
    assert events[-1]["type"] == "done"
    assert events[-1]["user_message"]["content"] == "edited question"
    assert events[-1]["assistant_message"]["content"] == "New answer."

    msgs = get_messages(client, chat_id)
    # first_mes + edited user + NEW assistant; old reply swept.
    assert [m["role"] for m in msgs] == ["assistant", "user", "assistant"]
    assert msgs[1]["id"] == user_id and msgs[1]["content"] == "edited question"
    assert msgs[2]["content"] == "New answer."
    assert all(m["id"] != old_asst_id for m in msgs)


def test_edit_sweeps_whole_variant_group(client, monkeypatch):
    _install_stream(monkeypatch, ["Fresh."])
    chat_id, user_id, old_asst_id = _seed_conversation(client)
    # Grow the old reply into a 3-variant group (2 inactive + 1 active).
    with database.get_db() as con:
        con.execute(
            "UPDATE messages SET variant_group = ?, active = 0 WHERE id = ?",
            (old_asst_id, old_asst_id),
        )
        for i in range(2):
            con.execute(
                "INSERT INTO messages (chat_id, role, content, variant_group, "
                "active) VALUES (?, 'assistant', ?, ?, ?)",
                (chat_id, f"variant {i}", old_asst_id, 1 if i == 1 else 0),
            )

    events = _run(_drive_edit(chat_id, user_id, "edited"))
    assert events[-1]["type"] == "done"

    with database.get_db() as con:
        left = con.execute(
            "SELECT COUNT(*) FROM messages WHERE chat_id = ? AND id > ? "
            "AND id != ?",
            (chat_id, user_id, events[-1]["assistant_message"]["id"]),
        ).fetchone()[0]
    assert left == 0  # the entire 3-row group is gone


# ── Validation ───────────────────────────────────────────────────────────────

def test_edit_validation_stable_codes(client):
    chat_id, user_id, old_asst_id = _seed_conversation(client)

    with pytest.raises(HTTPException) as exc:
        _validate_edit_target(chat_id + 999, user_id)
    assert exc.value.detail == "chat_not_found"

    with pytest.raises(HTTPException) as exc:
        _validate_edit_target(chat_id, 99999)
    assert exc.value.detail == "message_not_found"

    with pytest.raises(HTTPException) as exc:
        _validate_edit_target(chat_id, old_asst_id)  # role=assistant
    assert exc.value.status_code == 422
    assert exc.value.detail == "not_editable"


def test_edit_empty_message_422(client):
    chat_id, user_id, _ = _seed_conversation(client)
    r = client.post(
        f"/api/v1/chats/{chat_id}/messages/{user_id}/edit/stream",
        json={"message": "   ", "model_id": "test/model-1"},
    )
    assert r.status_code == 422


# ── I6 optimistic concurrency ────────────────────────────────────────────────

def test_edit_conflict_when_user_row_changed_mid_stream(client, monkeypatch):
    chat_id, user_id, old_asst_id = _seed_conversation(client)

    def concurrent_change():
        with database.get_db() as con:
            con.execute(
                "UPDATE messages SET content = 'changed elsewhere', "
                "updated_at = datetime('now', '+1 hour') WHERE id = ?",
                (user_id,),
            )

    _install_stream(monkeypatch, ["A ", "reply."], mid_stream=concurrent_change)
    events = _run(_drive_edit(chat_id, user_id, "my edit"))

    assert events[-1] == {"type": "error", "status": 409, "code": "edit_conflict"}
    msgs = get_messages(client, chat_id)
    # NOTHING written: concurrent content stands, old reply intact.
    assert [m["id"] for m in msgs if m["role"] != "assistant" or m["id"] == old_asst_id]
    assert any(m["id"] == old_asst_id and m["content"] == "old reply" for m in msgs)
    assert any(m["id"] == user_id and m["content"] == "changed elsewhere" for m in msgs)


def test_edit_conflict_when_downstream_grew_mid_stream(client, monkeypatch):
    chat_id, user_id, old_asst_id = _seed_conversation(client)

    def concurrent_append():
        with database.get_db() as con:
            con.execute(
                "INSERT INTO messages (chat_id, role, content) "
                "VALUES (?, 'user', 'raced in')", (chat_id,),
            )

    _install_stream(monkeypatch, ["A ", "reply."], mid_stream=concurrent_append)
    events = _run(_drive_edit(chat_id, user_id, "my edit"))

    assert events[-1] == {"type": "error", "status": 409, "code": "edit_conflict"}
    msgs = get_messages(client, chat_id)
    # The raced-in row and the old reply both survive; user text unchanged.
    assert any(m["content"] == "raced in" for m in msgs)
    assert any(m["id"] == old_asst_id and m["content"] == "old reply" for m in msgs)
    assert any(m["id"] == user_id and m["content"] == "original question" for m in msgs)


def test_finalize_edit_guard_unit(client):
    chat_id, user_id, _ = _seed_conversation(client)
    with database.get_db() as con:
        row = con.execute(
            "SELECT content, COALESCE(updated_at, created_at) AS updated_at "
            "FROM messages WHERE id = ?", (user_id,),
        ).fetchone()
        tail = con.execute(
            "SELECT MAX(id) AS m FROM messages WHERE chat_id = ?", (chat_id,),
        ).fetchone()["m"]

    with pytest.raises(EditConflictError):
        _finalize_edit(chat_id, user_id, "x", "y", "m",
                       row["updated_at"], "WRONG CONTENT", tail)
    with pytest.raises(EditConflictError):
        _finalize_edit(chat_id, user_id, "x", "y", "m",
                       row["updated_at"], row["content"], tail + 5)
    # Matching snapshot succeeds.
    result = _finalize_edit(chat_id, user_id, "new text", "new reply", "m",
                            row["updated_at"], row["content"], tail)
    assert result["user_message"]["content"] == "new text"
    assert result["assistant_message"]["content"] == "new reply"


# ── Abort: DB byte-identical ─────────────────────────────────────────────────

def test_edit_abort_leaves_chat_untouched(client, monkeypatch):
    _install_stream(monkeypatch, ["Partial ", "text."])
    chat_id, user_id, old_asst_id = _seed_conversation(client)
    before = [(m["id"], m["content"]) for m in get_messages(client, chat_id)]

    events = _run(
        _drive_edit(chat_id, user_id, "abandoned edit", stop_after_type="delta")
    )
    assert events[-1]["type"] == "delta"

    after = [(m["id"], m["content"]) for m in get_messages(client, chat_id)]
    assert after == before  # partial discarded, old content + tail intact


# ── Non-stream twin ──────────────────────────────────────────────────────────

def test_edit_non_stream_happy_path(client, monkeypatch):
    import routers.completions as cr

    async def fake_complete(messages, model_id, gen_params, provider):
        return {"choices": [{"message": {"role": "assistant",
                                         "content": "Sync reply."}}]}

    monkeypatch.setattr(cr, "complete", fake_complete)
    chat_id, user_id, old_asst_id = _seed_conversation(client)

    r = client.post(
        f"/api/v1/chats/{chat_id}/messages/{user_id}/edit",
        json={"message": "edited sync", "model_id": "test/model-1"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user_message"]["id"] == user_id
    assert body["user_message"]["content"] == "edited sync"
    assert body["assistant_message"]["content"] == "Sync reply."

    msgs = get_messages(client, chat_id)
    assert all(m["id"] != old_asst_id for m in msgs)
