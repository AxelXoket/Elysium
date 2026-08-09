"""Client-disconnect / abort tests for the streaming completion generator.

These drive the StreamingResponse body_iterator directly so we can throw
GeneratorExit at a precise suspension point - something TestClient can't do -
covering the two disconnect paths the audit flagged as untested:

- disconnect exactly at the `done` yield must NOT double-insert the assistant
  message (regression for the persisted-guard fix),
- disconnect mid-stream with a partial must persist that partial exactly once.
"""

import asyncio
import json

from routers.completions import complete_chat_stream, CompleteRequest

from conftest import make_character, make_chat, get_messages


def _run(coro):
    return asyncio.run(coro)


def _install_stream(monkeypatch, deltas):
    import routers.completions as cr

    def fake_stream(messages, model_id, gen_params, provider, **kwargs):
        async def gen():
            try:
                for d in deltas:
                    yield d
            except GeneratorExit:
                return  # graceful close when the outer generator is aborted
        return gen()

    monkeypatch.setattr(cr, "complete_stream", fake_stream)


async def _drive(chat_id, message, model_id, stop_after_type):
    """Drive the generator until an event of stop_after_type, then aclose()."""
    body = CompleteRequest(message=message, model_id=model_id)
    resp = await complete_chat_stream(chat_id, body)
    agen = resp.body_iterator
    seen = []
    async for chunk in agen:
        data = chunk[len("data: "):].strip()
        evt = json.loads(data)
        seen.append(evt)
        if evt["type"] == stop_after_type:
            break
    # Simulate the client dropping the connection at this suspension point.
    await agen.aclose()
    return seen


def test_disconnect_at_done_does_not_double_insert(client, monkeypatch):
    _install_stream(monkeypatch, ["Hello ", "world."])
    char_id = make_character(client)
    chat_id = make_chat(client, char_id)

    events = _run(_drive(chat_id, "hi", "test/model-1", stop_after_type="done"))
    assert events[-1]["type"] == "done"

    # Exactly one assistant turn - the abort handler must not re-insert.
    msgs = get_messages(client, chat_id)
    assistant = [m for m in msgs if m["role"] == "assistant"]
    # first_mes + the one completion assistant = 2; never 3.
    assert len(assistant) == 2
    assert assistant[-1]["content"] == "Hello world."


def test_disconnect_midstream_persists_partial_once(client, monkeypatch):
    _install_stream(monkeypatch, ["Partial ", "answer ", "here."])
    char_id = make_character(client)
    chat_id = make_chat(client, char_id)

    # Stop right after the first delta → a non-empty partial, no done.
    events = _run(_drive(chat_id, "hi", "test/model-1", stop_after_type="delta"))
    assert events[0]["type"] == "user_message"
    assert events[-1]["type"] == "delta"

    msgs = get_messages(client, chat_id)
    # user message kept, partial persisted exactly once as assistant.
    assert [m["role"] for m in msgs] == ["assistant", "user", "assistant"]
    assert msgs[-1]["content"] == "Partial "  # only the delta streamed before abort


def test_disconnect_before_any_delta_rolls_back_user(client, monkeypatch):
    _install_stream(monkeypatch, ["late"])  # we abort before consuming it
    char_id = make_character(client)
    chat_id = make_chat(client, char_id)

    # Stop at user_message (before any delta) → empty partial → user removed.
    events = _run(_drive(chat_id, "hi", "test/model-1", stop_after_type="user_message"))
    assert events[-1]["type"] == "user_message"

    msgs = get_messages(client, chat_id)
    assert [m["role"] for m in msgs] == ["assistant"]  # only first_mes remains


# ── v1.1 H12/I9: finalization must not resurrect a cleared chat ──────────────

async def _drive_all(chat_id, message, model_id):
    """Consume the whole stream (no early close); return all events."""
    body = CompleteRequest(message=message, model_id=model_id)
    resp = await complete_chat_stream(chat_id, body)
    seen = []
    async for chunk in resp.body_iterator:
        seen.append(json.loads(chunk[len("data: "):].strip()))
    return seen


def test_clear_mid_stream_makes_done_finalize_stale(client, monkeypatch):
    """Chat cleared while the provider streams: the `done` insert must be
    refused (409 exchange_stale event) and NO orphan assistant row written -
    otherwise the emptied chat is silently resurrected. (H12/I9.)"""
    import routers.completions as cr

    char_id = make_character(client)
    chat_id = make_chat(client, char_id)

    def fake_stream(messages, model_id, gen_params, provider, **kwargs):
        async def gen():
            yield "Hello "
            # The user clears the chat while text is still streaming.
            r = client.post(f"/api/v1/chats/{chat_id}/clear")
            assert r.status_code == 200, r.text
            yield "world."
        return gen()

    monkeypatch.setattr(cr, "complete_stream", fake_stream)

    events = _run(_drive_all(chat_id, "hi", "test/model-1"))
    assert events[-1] == {"type": "error", "status": 409, "code": "exchange_stale"}
    assert get_messages(client, chat_id) == []  # stays empty - no orphan


def test_clear_mid_stream_abort_discards_partial(client, monkeypatch):
    """Same race on the ABORT path: clear lands, then the client disconnects
    with a partial - the partial must be discarded, not inserted as an
    orphan. (H12.)"""
    _install_stream(monkeypatch, ["Partial ", "answer ", "here."])
    char_id = make_character(client)
    chat_id = make_chat(client, char_id)

    async def drive():
        body = CompleteRequest(message="hi", model_id="test/model-1")
        resp = await complete_chat_stream(chat_id, body)
        agen = resp.body_iterator
        async for chunk in agen:
            evt = json.loads(chunk[len("data: "):].strip())
            if evt["type"] == "delta":
                break  # non-empty partial accumulated
        # Clear BEFORE the disconnect propagates - the exact H12 window.
        r = client.post(f"/api/v1/chats/{chat_id}/clear")
        assert r.status_code == 200, r.text
        await agen.aclose()

    _run(drive())
    assert get_messages(client, chat_id) == []  # no orphan partial


def test_insert_assistant_message_guard_unit(client):
    """Direct guard contract: valid user id inserts; vanished id raises."""
    import pytest
    from routers.completions import _insert_assistant_message, StaleExchangeError

    import database

    char_id = make_character(client)
    chat_id = make_chat(client, char_id)
    with database.get_db() as con:
        user_id = con.execute(
            "INSERT INTO messages (chat_id, role, content) VALUES (?, 'user', 'seed')",
            (chat_id,),
        ).lastrowid

    row = _insert_assistant_message(chat_id, "test/model-1", "extra", user_id)
    assert row["role"] == "assistant" and row["content"] == "extra"

    client.post(f"/api/v1/chats/{chat_id}/clear")
    with pytest.raises(StaleExchangeError):
        _insert_assistant_message(chat_id, "test/model-1", "orphan", user_id)


# ── v1.1 audit L6: abort-path writes must fail fast, never freeze the loop ────

def test_get_db_busy_timeout_is_parameterized(client):
    """The abort path opens with a SHORT busy_timeout; the default stays 15s."""
    import database
    with database.get_db() as con:
        assert con.execute("PRAGMA busy_timeout").fetchone()[0] == 15000
    with database.get_db(busy_timeout_ms=250) as con:
        assert con.execute("PRAGMA busy_timeout").fetchone()[0] == 250


def test_abort_insert_fails_fast_under_contended_lock(client):
    """With the writer lock held elsewhere, an abort-path insert carrying the
    short busy_timeout must raise SQLITE_BUSY in well under the 15s default
    instead of stalling the event loop - proving _ABORT_DB_BUSY_TIMEOUT_MS
    actually reaches the connection."""
    import time
    import pytest
    import database
    from routers.completions import _insert_assistant_message

    char_id = make_character(client)
    chat_id = make_chat(client, char_id)
    with database.get_db() as con:
        user_id = con.execute(
            "INSERT INTO messages (chat_id, role, content) VALUES (?, 'user', 'seed')",
            (chat_id,),
        ).lastrowid

    # Hold the writer lock on a separate connection (RESERVED via IMMEDIATE).
    holder = database.get_db()
    hold_con = holder.__enter__()
    hold_con.execute("BEGIN IMMEDIATE")
    try:
        start = time.monotonic()
        with pytest.raises(database.sqlite3.OperationalError):
            _insert_assistant_message(
                chat_id, "test/model-1", "partial", user_id, busy_timeout_ms=200,
            )
        elapsed = time.monotonic() - start
        assert elapsed < 5.0  # nowhere near the 15s default -> short timeout won
    finally:
        hold_con.execute("ROLLBACK")
        holder.__exit__(None, None, None)

    # The lock is released and nothing partial was written under it.
    assert [m["role"] for m in get_messages(client, chat_id)] == ["assistant", "user"]
