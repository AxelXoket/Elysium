"""The model's reply may stop because the token ceiling hit, not because it
finished a sentence - and until now the half sentence was stored and shown
exactly like a complete reply. This file proves the fix end to end: the
guarded migration that adds messages.truncated, the shared vocabulary both
notebook_extract.py and this persistence path read finish reasons through,
the streaming and non-streaming write paths that populate the column, and the
read paths (GET /messages, activate) that expose it under the agreed name
`truncated`.

Decision, spelled out once here rather than scattered across call sites: a
stream the user ABORTS and a stream that DIES mid-flight both count as
truncated, unconditionally, when a partial is kept. Neither one is the model
choosing to stop - the text is cut wherever the connection happened to be
when it broke, which is a strictly worse case than a token-ceiling cutoff
(that one at least finishes the sentence it was mid-word on more often than
not). See the comment at routers/completions.py's rescue() for the same
reasoning next to the code it governs.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest

import openrouter
from database import _migrate, _SCHEMA
from messages_common import msg_to_dict
from openrouter import TRUNCATED_FINISH_REASONS, finish_reasons
from routers.completions import complete_chat_stream, CompleteRequest

from conftest import make_character, make_chat, get_messages


# ---------------------------------------------------------------------------
# Migration: the guarded ALTER, same idiom as variant_group/active/updated_at
# ---------------------------------------------------------------------------

# The messages table as it shipped before this column existed - no different
# from the v1.0 fixture test_phase2_migration.py already uses, kept local so
# this file does not depend on another test module's private schema string.
_PRE_TRUNCATED_SCHEMA = """
CREATE TABLE characters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE chats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id INTEGER NOT NULL REFERENCES characters(id),
    title TEXT,
    model_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL REFERENCES chats(id),
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER REFERENCES messages(id),
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _old_db() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(_PRE_TRUNCATED_SCHEMA)
    con.execute("INSERT INTO characters (name) VALUES ('C')")
    con.execute("INSERT INTO chats (character_id) VALUES (1)")
    con.execute(
        "INSERT INTO messages (chat_id, role, content) VALUES (1, 'assistant', 'old row')"
    )
    con.commit()
    return con


def test_migrate_adds_truncated_column_defaulting_to_zero():
    """GROUND: a row written before this build shipped backfills to 0, not
    to some inferred guess - nothing about its truncation status survives."""
    con = _old_db()
    _migrate(con)

    cols = {r[1] for r in con.execute("PRAGMA table_info(messages)").fetchall()}
    assert "truncated" in cols
    row = con.execute("SELECT truncated FROM messages WHERE id = 1").fetchone()
    assert row["truncated"] == 0


def test_migrate_truncated_column_is_idempotent():
    con = _old_db()
    _migrate(con)
    _migrate(con)  # second boot on an already-migrated DB must not raise
    cols = {r[1] for r in con.execute("PRAGMA table_info(messages)").fetchall()}
    assert list(cols).count("truncated") == 1  # PRAGMA table_info can't lie
    # twice, but the point under test is that ALTER did not fire twice either -
    # a second unconditional ALTER on an existing column raises OperationalError,
    # and this call would already have raised above if it had.


def test_fresh_schema_gets_truncated_column():
    """variant_group and active also live only in the migration, not in
    _SCHEMA's CREATE TABLE - truncated follows the same, already-proven path."""
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(_SCHEMA)
    _migrate(con)
    con.execute("INSERT INTO characters (name) VALUES ('C')")
    con.execute("INSERT INTO chats (character_id) VALUES (1)")
    con.execute("INSERT INTO messages (chat_id, role, content) VALUES (1, 'assistant', 'x')")
    row = con.execute("SELECT truncated FROM messages WHERE id = 1").fetchone()
    assert row["truncated"] == 0


# ---------------------------------------------------------------------------
# The shared vocabulary (openrouter.TRUNCATED_FINISH_REASONS / finish_reasons)
# ---------------------------------------------------------------------------

def test_finish_reasons_reads_both_fields_lowercased():
    got = finish_reasons({"finish_reason": "Length", "native_finish_reason": None})
    assert got == {"length", ""}


def test_a_natural_stop_is_not_a_truncated_reason():
    """GROUND for the vocabulary itself: "stop" must never trip the set, or
    every ordinary reply would be flagged."""
    assert not ({"stop"} & TRUNCATED_FINISH_REASONS)


def test_length_is_a_truncated_reason():
    """POSITIVE CONTROL: the one spelling OpenRouter itself normalises to."""
    assert "length" in TRUNCATED_FINISH_REASONS


def test_native_finish_reason_spelling_is_recognized():
    """The upstream word, not OpenRouter's normalised one - native_finish_reason
    relays it unchanged, so the vocabulary has to know it under its own name."""
    reasons = finish_reasons({"finish_reason": None,
                              "native_finish_reason": "MAX_TOKENS"})
    assert reasons & TRUNCATED_FINISH_REASONS


# ---------------------------------------------------------------------------
# messages_common.msg_to_dict: defensive read, real value when present
# ---------------------------------------------------------------------------

def test_msg_to_dict_truncated_defaults_false_when_column_missing():
    """GROUND: an older SELECT (no truncated column) must not crash and must
    not claim truncation it never asked about."""
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(_SCHEMA)
    _migrate(con)
    con.execute("INSERT INTO characters (name) VALUES ('C')")
    con.execute("INSERT INTO chats (character_id) VALUES (1)")
    con.execute(
        "INSERT INTO messages (chat_id, role, content, truncated) "
        "VALUES (1, 'assistant', 'x', 1)"
    )
    narrow = con.execute(
        "SELECT id, chat_id, role, content, created_at FROM messages WHERE id = 1"
    ).fetchone()
    assert msg_to_dict(narrow)["truncated"] is False


def test_msg_to_dict_reads_the_truncated_column():
    """POSITIVE CONTROL (True) plus its own ground (False) in the same table,
    same SELECT shape - the only variable is the stored value."""
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(_SCHEMA)
    _migrate(con)
    con.execute("INSERT INTO characters (name) VALUES ('C')")
    con.execute("INSERT INTO chats (character_id) VALUES (1)")
    con.execute(
        "INSERT INTO messages (chat_id, role, content, truncated) "
        "VALUES (1, 'assistant', 'cut off mid', 1)"
    )
    con.execute(
        "INSERT INTO messages (chat_id, role, content, truncated) "
        "VALUES (1, 'assistant', 'finished fine', 0)"
    )
    rows = con.execute(
        "SELECT id, chat_id, role, content, created_at, truncated "
        "FROM messages ORDER BY id"
    ).fetchall()
    assert msg_to_dict(rows[0])["truncated"] is True
    assert msg_to_dict(rows[1])["truncated"] is False


# ---------------------------------------------------------------------------
# openrouter.complete_stream: the on_finish verdict
# ---------------------------------------------------------------------------
# Same fake-transport shape as test_stream_deadlines.py, duplicated locally
# rather than imported - these fakes are test fixtures, not public API, and
# every existing streaming test file that needs one writes its own.

class _FakeResponse:
    def __init__(self, lines):
        self.status_code = 200
        self._lines = lines

    async def aiter_bytes(self):
        for line in self._lines:
            yield line.encode("utf-8") + b"\n"


class _FakeStreamCtx:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *exc):
        return False


class _FakeClient:
    def __init__(self, lines):
        self._lines = lines

    def stream(self, *a, **kw):
        return _FakeStreamCtx(_FakeResponse(self._lines))


async def _drain_with_finish(monkeypatch, lines):
    """Run complete_stream to exhaustion; return (text, [on_finish calls])."""
    monkeypatch.setattr(openrouter, "get_client", lambda: _FakeClient(lines))
    monkeypatch.setattr(openrouter, "get_secret", lambda name: "sk-test")
    calls = []
    out = []
    async for delta in openrouter.complete_stream(
        [{"role": "user", "content": "hi"}], "test/model", {}, None,
        on_finish=calls.append,
    ):
        out.append(delta)
    return "".join(out), calls


@pytest.mark.anyio
async def test_on_finish_reports_false_for_a_clean_stop(monkeypatch):
    """GROUND: an ordinary reply that says "stop" and sends [DONE]."""
    lines = [
        'data: {"choices":[{"delta":{"content":"Once "},"finish_reason":null}]}',
        'data: {"choices":[{"delta":{"content":"upon a time."},'
        '"finish_reason":"stop"}]}',
        "data: [DONE]",
    ]
    text, calls = await _drain_with_finish(monkeypatch, lines)
    assert text == "Once upon a time."
    assert calls == [False]


@pytest.mark.anyio
async def test_on_finish_reports_true_for_length_finish_reason(monkeypatch):
    """POSITIVE CONTROL: the token ceiling, OpenRouter's normalised spelling."""
    lines = [
        'data: {"choices":[{"delta":{"content":"cut off mid"},'
        '"finish_reason":"length"}]}',
        "data: [DONE]",
    ]
    text, calls = await _drain_with_finish(monkeypatch, lines)
    assert text == "cut off mid"
    assert calls == [True]


@pytest.mark.anyio
async def test_on_finish_reports_true_for_native_finish_reason_spelling(monkeypatch):
    """The upstream word alone, with OpenRouter's own field empty - proves both
    fields are actually read, not just finish_reason."""
    lines = [
        'data: {"choices":[{"delta":{"content":"cut"},'
        '"native_finish_reason":"MAX_TOKENS"}]}',
        "data: [DONE]",
    ]
    text, calls = await _drain_with_finish(monkeypatch, lines)
    assert text == "cut"
    assert calls == [True]


@pytest.mark.anyio
async def test_on_finish_reports_true_when_the_stream_dies_without_saying_so(
    monkeypatch,
):
    """K-15: the connection just closes - no [DONE], no finish_reason at all.
    That silence is not evidence of a clean stop and must count as cut off,
    exactly like NOTICE_STREAM_UNFINISHED already treats it for the reader."""
    lines = [
        'data: {"choices":[{"delta":{"content":"a mid-sentence "}}]}',
        'data: {"choices":[{"delta":{"content":"trail"}}]}',
        # provider hangs up here - no [DONE], no finish_reason ever arrives
    ]
    text, calls = await _drain_with_finish(monkeypatch, lines)
    assert text == "a mid-sentence trail"
    assert calls == [True]


@pytest.mark.anyio
async def test_on_finish_is_not_called_when_the_stream_raises(monkeypatch):
    """A mid-stream provider error is the caller's job (rescue/error handling
    in _stream_exchange), not this callback's - it must not fire on a path
    that never reached a normal end."""
    lines = [
        'data: {"error": {"code": 429}}',
    ]
    monkeypatch.setattr(openrouter, "get_client", lambda: _FakeClient(lines))
    monkeypatch.setattr(openrouter, "get_secret", lambda name: "sk-test")
    calls = []
    with pytest.raises(openrouter.OpenRouterError):
        async for _ in openrouter.complete_stream(
            [{"role": "user", "content": "hi"}], "test/model", {}, None,
            on_finish=calls.append,
        ):
            pass
    assert calls == []


# ---------------------------------------------------------------------------
# Non-streaming routes: /complete, /regenerate, /edit
# ---------------------------------------------------------------------------

def _fake_complete(text="reply", finish_reason=None, native_finish_reason=None):
    """A minimal openrouter.complete stand-in that can report a finish reason -
    the shared `provider` fixture in conftest.py cannot, by design (it exists
    to record payloads, not to script truncation), so these routes get their
    own small fake rather than growing that fixture a knob only this file uses.
    """
    choice: dict = {"message": {"content": text}}
    if finish_reason is not None:
        choice["finish_reason"] = finish_reason
    if native_finish_reason is not None:
        choice["native_finish_reason"] = native_finish_reason

    async def _complete(messages, model_id, gen_params, provider, **kwargs):
        return {"choices": [choice]}

    return _complete


def _seed(client, monkeypatch, *, finish_reason=None, text="first reply"):
    import routers.completions as cr

    char_id = make_character(client)
    chat_id = make_chat(client, char_id)
    monkeypatch.setattr(cr, "complete", _fake_complete(text, finish_reason))
    resp = client.post(f"/api/v1/chats/{chat_id}/complete",
                       json={"message": "hi", "model_id": "test/model-1"})
    assert resp.status_code == 200, resp.text
    return chat_id, resp.json()


def test_complete_marks_truncated_true_on_length_finish_reason(client, monkeypatch):
    chat_id, data = _seed(client, monkeypatch, finish_reason="length")
    assert data["assistant_message"]["truncated"] is True


def test_complete_marks_truncated_false_on_stop_finish_reason(client, monkeypatch):
    """GROUND, at the route level: a normal reply is NOT marked."""
    chat_id, data = _seed(client, monkeypatch, finish_reason="stop")
    assert data["assistant_message"]["truncated"] is False


def test_complete_marks_truncated_false_when_finish_reason_is_absent(client, monkeypatch):
    """GROUND: some providers send nothing at all on an ordinary reply."""
    chat_id, data = _seed(client, monkeypatch, finish_reason=None)
    assert data["assistant_message"]["truncated"] is False


def test_regenerate_marks_truncated_true_on_length(client, monkeypatch):
    import routers.completions as cr

    chat_id, seeded = _seed(client, monkeypatch)
    target_id = seeded["assistant_message"]["id"]

    monkeypatch.setattr(cr, "complete", _fake_complete("regenerated", "length"))
    resp = client.post(
        f"/api/v1/chats/{chat_id}/messages/{target_id}/regenerate",
        json={"model_id": "test/model-1"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["assistant_message"]["truncated"] is True


def test_regenerate_marks_truncated_false_on_stop(client, monkeypatch):
    """GROUND for the same route."""
    import routers.completions as cr

    chat_id, seeded = _seed(client, monkeypatch)
    target_id = seeded["assistant_message"]["id"]

    monkeypatch.setattr(cr, "complete", _fake_complete("regenerated", "stop"))
    resp = client.post(
        f"/api/v1/chats/{chat_id}/messages/{target_id}/regenerate",
        json={"model_id": "test/model-1"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["assistant_message"]["truncated"] is False


def test_edit_marks_truncated_true_on_length(client, monkeypatch):
    import routers.completions as cr

    chat_id, seeded = _seed(client, monkeypatch)
    user_msg_id = seeded["user_message"]["id"]

    monkeypatch.setattr(cr, "complete", _fake_complete("edited reply", "length"))
    resp = client.post(
        f"/api/v1/chats/{chat_id}/messages/{user_msg_id}/edit",
        json={"message": "edited question", "model_id": "test/model-1"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["assistant_message"]["truncated"] is True


def test_edit_marks_truncated_false_on_stop(client, monkeypatch):
    """GROUND for the same route."""
    import routers.completions as cr

    chat_id, seeded = _seed(client, monkeypatch)
    user_msg_id = seeded["user_message"]["id"]

    monkeypatch.setattr(cr, "complete", _fake_complete("edited reply", "stop"))
    resp = client.post(
        f"/api/v1/chats/{chat_id}/messages/{user_msg_id}/edit",
        json={"message": "edited question", "model_id": "test/model-1"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["assistant_message"]["truncated"] is False


# ---------------------------------------------------------------------------
# GET /chats/{id}/messages and /activate: the read paths
# ---------------------------------------------------------------------------

def test_list_messages_reports_truncated_per_row(client, monkeypatch):
    """The main listing route (chats.py::_list_messages_sync) has its own
    SELECT, separate from every write path above - this proves that read
    wiring independently, not just the write."""
    chat_id, data = _seed(client, monkeypatch, finish_reason="length", text="cut short")

    msgs = get_messages(client, chat_id)
    by_role = {m["role"]: m for m in msgs}
    assert by_role["assistant"]["truncated"] is True
    # The character's greeting was inserted directly (first_mes), never went
    # through a completion, and must never look cut off.
    # (There are two assistant rows once the completion lands - find the
    # greeting by its known content instead of by role alone.)
    greeting = next(m for m in msgs if m["content"] == "Hello there!")
    assert greeting["truncated"] is False
    assert by_role["user"]["truncated"] is False


def test_activate_variant_reports_truncated_of_the_activated_row(client, monkeypatch):
    """chats.py::_activate_variant_sync's own SELECT (the `fresh` read) must
    carry the column too - proven by two variants that disagree on it."""
    import routers.completions as cr

    chat_id, seeded = _seed(client, monkeypatch, finish_reason="stop")
    v0 = seeded["assistant_message"]["id"]

    monkeypatch.setattr(cr, "complete", _fake_complete("variant one", "length"))
    resp = client.post(
        f"/api/v1/chats/{chat_id}/messages/{v0}/regenerate",
        json={"model_id": "test/model-1"},
    )
    assert resp.status_code == 200, resp.text
    v1 = resp.json()["assistant_message"]["id"]
    assert resp.json()["assistant_message"]["truncated"] is True

    back = client.post(f"/api/v1/chats/{chat_id}/messages/{v0}/activate")
    assert back.status_code == 200, back.text
    assert back.json()["message"]["truncated"] is False

    forward = client.post(f"/api/v1/chats/{chat_id}/messages/{v1}/activate")
    assert forward.status_code == 200, forward.text
    assert forward.json()["message"]["truncated"] is True


# ---------------------------------------------------------------------------
# Streaming routes: /complete/stream, /regenerate/stream, /edit/stream
# ---------------------------------------------------------------------------

def _fake_stream_reporting(deltas, truncated):
    """A complete_stream stand-in that plays complete_stream's own contract:
    yield text, then call on_finish once, exactly like the real generator
    does after a clean end. Everything else (SSE framing, finish_reason
    parsing) is already covered by the openrouter-level tests above; this one
    only has to prove _stream_exchange reads the verdict and threads it
    through to the write."""
    def fake_stream(messages, model_id, gen_params, provider, **kwargs):
        on_finish = kwargs.get("on_finish")

        async def gen():
            for d in deltas:
                yield d
            if on_finish is not None:
                on_finish(truncated)

        return gen()
    return fake_stream


def _read_sse(resp) -> list[dict]:
    return [json.loads(line[len("data:"):].strip())
            for line in resp.iter_lines() if line.strip().startswith("data:")]


def test_stream_complete_marks_truncated_true(client, monkeypatch):
    import routers.completions as cr

    char_id = make_character(client)
    chat_id = make_chat(client, char_id)
    monkeypatch.setattr(cr, "complete_stream",
                        _fake_stream_reporting(["cut ", "off"], True))

    with client.stream(
        "POST", f"/api/v1/chats/{chat_id}/complete/stream",
        json={"message": "hi", "model_id": "test/model-1"},
    ) as resp:
        events = _read_sse(resp)
    done = events[-1]
    assert done["type"] == "done"
    assert done["assistant_message"]["truncated"] is True


def test_stream_complete_marks_truncated_false(client, monkeypatch):
    """GROUND for the streaming /complete path."""
    import routers.completions as cr

    char_id = make_character(client)
    chat_id = make_chat(client, char_id)
    monkeypatch.setattr(cr, "complete_stream",
                        _fake_stream_reporting(["finished ", "fine"], False))

    with client.stream(
        "POST", f"/api/v1/chats/{chat_id}/complete/stream",
        json={"message": "hi", "model_id": "test/model-1"},
    ) as resp:
        events = _read_sse(resp)
    done = events[-1]
    assert done["type"] == "done"
    assert done["assistant_message"]["truncated"] is False


def test_stream_regenerate_marks_truncated_true(client, monkeypatch):
    import routers.completions as cr

    char_id = make_character(client)
    chat_id = make_chat(client, char_id)
    monkeypatch.setattr(cr, "complete", _fake_complete("seed reply", "stop"))
    seed = client.post(f"/api/v1/chats/{chat_id}/complete",
                       json={"message": "hi", "model_id": "test/model-1"})
    assert seed.status_code == 200, seed.text
    v0 = seed.json()["assistant_message"]["id"]

    monkeypatch.setattr(cr, "complete_stream",
                        _fake_stream_reporting(["variant "], True))
    with client.stream(
        "POST", f"/api/v1/chats/{chat_id}/messages/{v0}/regenerate/stream",
        json={"model_id": "test/model-1"},
    ) as resp:
        events = _read_sse(resp)
    done = events[-1]
    assert done["type"] == "done"
    assert done["assistant_message"]["truncated"] is True


def test_stream_edit_marks_truncated_true(client, monkeypatch):
    import routers.completions as cr

    char_id = make_character(client)
    chat_id = make_chat(client, char_id)
    monkeypatch.setattr(cr, "complete", _fake_complete("seed reply", "stop"))
    seed = client.post(f"/api/v1/chats/{chat_id}/complete",
                       json={"message": "hi", "model_id": "test/model-1"})
    assert seed.status_code == 200, seed.text
    user_id = seed.json()["user_message"]["id"]

    monkeypatch.setattr(cr, "complete_stream",
                        _fake_stream_reporting(["edited "], True))
    with client.stream(
        "POST", f"/api/v1/chats/{chat_id}/messages/{user_id}/edit/stream",
        json={"message": "edited question", "model_id": "test/model-1"},
    ) as resp:
        events = _read_sse(resp)
    done = events[-1]
    assert done["type"] == "done"
    assert done["assistant_message"]["truncated"] is True


# ---------------------------------------------------------------------------
# Aborted / dead streams: decision under test - a KEPT partial is ALWAYS
# truncated, independent of whatever complete_stream's own on_finish verdict
# would have been (it never gets asked: the generator is torn down mid-flight).
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.run(coro)


def test_aborted_stream_partial_is_marked_truncated(client, monkeypatch):
    """Client-abort path (rescue()): a partial reply kept after a disconnect
    must be marked truncated even though nothing ever reported a
    finish_reason - the abort itself is the reason."""
    import routers.completions as cr

    def fake_stream(messages, model_id, gen_params, provider, **kwargs):
        async def gen():
            try:
                yield "Partial "
                yield "answer."
            except GeneratorExit:
                return
        return gen()

    monkeypatch.setattr(cr, "complete_stream", fake_stream)
    char_id = make_character(client)
    chat_id = make_chat(client, char_id)

    async def drive():
        body = CompleteRequest(message="hi", model_id="test/model-1")
        resp = await complete_chat_stream(chat_id, body)
        agen = resp.body_iterator
        async for chunk in agen:
            evt = json.loads(chunk[len("data: "):].strip())
            if evt["type"] == "delta":
                break  # non-empty partial accumulated, before `done`
        await agen.aclose()

    _run(drive())

    msgs = get_messages(client, chat_id)
    partial = next(m for m in msgs if m["content"] == "Partial ")
    assert partial["truncated"] is True
