"""Audit KÖK 8: regenerate and edit looked up their target on the loop.

Four handlers - regenerate, regenerate/stream, edit, edit/stream - each opened
the vault twice before doing anything else: once to validate the target row and
once to load that row's linked images. Both on the event loop, so every live
SSE stream in the process froze for the duration.

The audit named only the attachment load. The validator sitting one line above
it is the heavier of the two: a chat lookup, a message lookup, an active-anchor
scan and a MAX(id) scan, each paying its own SQLCipher open. Fixing only the
named line would have made the diff look complete while leaving the bigger
blocker in place, so both moved together, into ONE hop rather than two.

One hop, not two, is deliberate: wrapping them separately would open a new
await point between the guard and the read it guards, and that window does not
exist today.

Discriminator as established in test_chat_read_loop_blocking.py: a real
wall-clock stall injected into the module's own reference to the blocking call,
and a heartbeat coroutine counting how often the loop got control meanwhile.
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

import database
import routers.completions as cr
from tests.conftest import make_character, make_chat

from tests.loop_guard import (
    MAX_FREEZE_S,
    MIN_TICKS,
    STALL_S as _STALL_S,
    longest_freeze as _longest_freeze,
    ticks_during as _ticks_during,
)



@pytest.fixture()
def slow_db(monkeypatch):
    """Stall the connection the target lookup opens.

    Patched on routers.completions' own reference so the seeding below still
    runs at full speed, and so the stall fires whether or not the handler
    hands the work to a thread.
    """
    real = cr.get_db

    def slow():
        time.sleep(_STALL_S)
        return real()

    monkeypatch.setattr(cr, "get_db", slow)



def _seed(client) -> tuple[int, int]:
    """A chat ending in [user, assistant], the shape both flows need."""
    char_id = make_character(client)
    chat_id = make_chat(client, char_id)
    with database.get_db() as con:
        user_id = con.execute(
            "INSERT INTO messages (chat_id, role, content) "
            "VALUES (?, 'user', 'original question')", (chat_id,),
        ).lastrowid
        con.execute(
            "INSERT INTO messages (chat_id, role, content) "
            "VALUES (?, 'assistant', 'old reply')", (chat_id,),
        )
    return chat_id, user_id


# ── the loop keeps running ───────────────────────────────────────────────────

@pytest.fixture()
def fake_provider(monkeypatch):
    """A provider that answers instantly, so the stall is the only wall clock."""
    async def fake_complete(*a, **kw):
        return {"choices": [{"message": {"content": "new reply"}}]}

    monkeypatch.setattr(cr, "complete", fake_complete)


@pytest.mark.anyio
async def test_regenerating_does_not_freeze_the_loop(
    anyio_backend, client, fake_provider, slow_db
):
    """The real handler, end to end, not the worker body in isolation.

    Driving the extracted _sync helper directly would prove nothing: the test
    would be handing it to a thread itself, so it would pass whether or not the
    handler does.
    """
    chat_id, user_id = _seed(client)
    body = cr.RegenerateRequest(model_id="test/model-1")
    freeze, out = await _longest_freeze(
        cr.regenerate_message(chat_id, user_id + 1, body)
    )
    assert out["assistant_message"]["content"] == "new reply"
    assert freeze < MAX_FREEZE_S, (
        f"the loop was frozen for {freeze:.3f}s during a regenerate"
    )


#: How long a gated database open waits for the loop to free it. Only a
#: FAILING run ever waits this long: a call made off the loop is freed on
#: the loop's next turn, and a call made on the loop can never be, because
#: the coroutine that would free it cannot run. No threshold sits between
#: those two outcomes, so load cannot move the verdict.
GATE_TIMEOUT_S = 5.0


@pytest.fixture()
def gated_db(monkeypatch):
    """Every connection the handler opens waits until the LOOP frees it.

    A counted fact instead of a wall-clock gap (this test
    flaked at 0.060 s against the 0.05 s ceiling under a full-suite load).
    Each open parks on its own event and a coroutine on the loop sets every
    parked event it sees. An open made on the loop thread blocks the one
    thread that could set its event, so it times out and is recorded as not
    freed; an open made on a worker thread is freed on the loop's next turn.
    """
    real = cr.get_db
    parked: list[threading.Event] = []
    freed: list[bool] = []

    def gated():
        gate = threading.Event()
        parked.append(gate)
        freed.append(gate.wait(timeout=GATE_TIMEOUT_S))
        return real()

    monkeypatch.setattr(cr, "get_db", gated)
    return parked, freed


async def _run_with_the_loop_freeing(coro, parked):
    async def free():
        while True:
            for gate in list(parked):
                gate.set()
            await asyncio.sleep(0)

    freer = asyncio.ensure_future(free())
    try:
        return await coro
    finally:
        freer.cancel()


@pytest.mark.anyio
async def test_editing_does_not_freeze_the_loop(
    anyio_backend, client, fake_provider, gated_db
):
    parked, freed = gated_db
    chat_id, user_id = _seed(client)
    body = cr.EditRequest(model_id="test/model-1", message="a different question")
    out = await _run_with_the_loop_freeing(
        cr.edit_message(chat_id, user_id, body), parked)
    assert out["user_message"]["content"] == "a different question"
    # GROUND: the handler really opened the vault through the gate.
    assert freed, "the edit opened no connection through routers.completions"
    assert all(freed), (
        f"{freed.count(False)} of {len(freed)} database opens ran on the "
        "event loop during an edit: the loop could not free them")


# ── and the flows still behave ───────────────────────────────────────────────

def test_regenerate_still_answers_and_keeps_the_old_variant(client, monkeypatch):
    """Behaviour guard: the moved lookup must not change what is returned."""
    chat_id, user_id = _seed(client)

    async def fake_complete(*a, **kw):
        return {"choices": [{"message": {"content": "new reply"}}]}

    monkeypatch.setattr(cr, "complete", fake_complete)
    asst_id = user_id + 1
    r = client.post(
        f"/api/v1/chats/{chat_id}/messages/{asst_id}/regenerate",
        json={"model_id": "test/model-1"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["user_message"]["content"] == "original question"
    assert body["assistant_message"]["content"] == "new reply"
    assert body["assistant_message"]["variant_count"] == 2


def test_edit_still_rewrites_the_turn(client, monkeypatch):
    chat_id, user_id = _seed(client)

    async def fake_complete(*a, **kw):
        return {"choices": [{"message": {"content": "answer to the new text"}}]}

    monkeypatch.setattr(cr, "complete", fake_complete)
    r = client.post(
        f"/api/v1/chats/{chat_id}/messages/{user_id}/edit",
        json={"model_id": "test/model-1", "message": "a different question"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["user_message"]["content"] == "a different question"
    assert body["assistant_message"]["content"] == "answer to the new text"


def test_a_missing_target_is_still_a_clean_error(client):
    """HTTPException has to survive the thread hop."""
    chat_id, _ = _seed(client)
    assert client.post(
        f"/api/v1/chats/{chat_id}/messages/99999/regenerate",
        json={"model_id": "test/model-1"},
    ).status_code == 404
    assert client.post(
        f"/api/v1/chats/{chat_id}/messages/99999/edit",
        json={"model_id": "test/model-1", "message": "x"},
    ).status_code == 404
