"""Audit KÖK 8: eight persona and character handlers were still on the loop.

Both routers had converted only their patch/delete handlers in an earlier pass.
Everything else - listing, creating, importing, fetching, deleting, selecting -
still opened the vault on the event loop, so browsing your character list while
a reply streamed could stall the reply.

Two of them were also wrong in a way that had nothing to do with threading, and
both are fixed here because the fix is the same transaction:

  select_persona  ran its "does this persona exist" guard in one connection,
                  let it commit and close, then opened a SECOND connection to
                  write the setting. A delete landing in that gap left
                  selected_persona_id naming a row that no longer existed, with
                  no error anywhere. That one has a race test at the bottom.

  delete_persona  ran its guard in autocommit, so two concurrent deletes of the
                  same id both passed it and the loser reported {"ok": true}
                  for a persona it did not delete, where every sibling answers
                  404.

Discriminator: a real wall-clock stall injected into each module's own get_db
reference, and the longest stretch the loop went without control while the
handler ran. A tick COUNT would not do here - some of these handlers open two
connections, and once one of them is off the loop the loop ticks either way.
The longest gap is what actually distinguishes blocked from not.
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest
from starlette.requests import Request

import routers.characters as characters
import routers.personas as personas
from database import get_db

from tests.loop_guard import (
    MAX_FREEZE_S,
    MIN_TICKS,
    STALL_S as _STALL_S,
    longest_freeze as _longest_freeze,
    ticks_during as _ticks_during,
)



def _stall_get_db(monkeypatch, module):
    real = module.get_db

    def slow():
        time.sleep(_STALL_S)
        return real()

    monkeypatch.setattr(module, "get_db", slow)


@pytest.fixture()
def slow_personas(monkeypatch):
    _stall_get_db(monkeypatch, personas)


@pytest.fixture()
def slow_characters(monkeypatch):
    _stall_get_db(monkeypatch, characters)



def _make_persona(client, name="Tester") -> int:
    r = client.post("/api/v1/personas",
                    json={"display_name": name, "description": "d"})
    assert r.status_code == 201
    return r.json()["id"]


def _make_character(client, name="Char") -> int:
    r = client.post("/api/v1/characters", json={"name": name})
    assert r.status_code == 201
    return r.json()["id"]


# ── personas: the loop keeps running ─────────────────────────────────────────

@pytest.mark.anyio
async def test_listing_personas_does_not_freeze_the_loop(
    anyio_backend, client, slow_personas
):
    freeze, out = await _longest_freeze(personas.list_personas())
    assert isinstance(out, list)
    assert freeze < MAX_FREEZE_S, f"loop frozen {freeze:.3f}s listing personas"


@pytest.mark.anyio
async def test_creating_a_persona_does_not_freeze_the_loop(
    anyio_backend, client, slow_personas
):
    body = personas.PersonaCreate(display_name="Ayse", description="d")
    freeze, out = await _longest_freeze(personas.create_persona(body))
    assert out["display_name"] == "Ayse"
    assert freeze < MAX_FREEZE_S, f"loop frozen {freeze:.3f}s creating a persona"


@pytest.mark.anyio
async def test_selecting_a_persona_does_not_freeze_the_loop(
    anyio_backend, client, slow_personas
):
    pid = _make_persona(client)
    freeze, out = await _longest_freeze(personas.select_persona(pid))
    assert out["selected_persona_id"] == pid
    assert freeze < MAX_FREEZE_S, f"loop frozen {freeze:.3f}s selecting a persona"


@pytest.mark.anyio
async def test_deleting_a_persona_does_not_freeze_the_loop(
    anyio_backend, client, slow_personas
):
    pid = _make_persona(client)
    freeze, out = await _longest_freeze(personas.delete_persona(pid))
    assert out == {"ok": True}
    assert freeze < MAX_FREEZE_S, f"loop frozen {freeze:.3f}s deleting a persona"


# ── characters: the loop keeps running ───────────────────────────────────────

@pytest.mark.anyio
async def test_listing_characters_does_not_freeze_the_loop(
    anyio_backend, client, slow_characters
):
    freeze, out = await _longest_freeze(characters.list_characters())
    assert isinstance(out, list)
    assert freeze < MAX_FREEZE_S, f"loop frozen {freeze:.3f}s listing characters"


@pytest.mark.anyio
async def test_creating_a_character_does_not_freeze_the_loop(
    anyio_backend, client, slow_characters
):
    body = characters.CharacterCreate(name="Nihal")
    freeze, out = await _longest_freeze(characters.create_character(body))
    assert out["name"] == "Nihal"
    assert freeze < MAX_FREEZE_S, f"loop frozen {freeze:.3f}s creating a character"


@pytest.mark.anyio
async def test_fetching_a_character_does_not_freeze_the_loop(
    anyio_backend, client, slow_characters
):
    cid = _make_character(client)
    freeze, out = await _longest_freeze(characters.get_character(cid))
    assert out["id"] == cid
    assert freeze < MAX_FREEZE_S, f"loop frozen {freeze:.3f}s fetching a character"


@pytest.mark.anyio
async def test_importing_a_character_does_not_freeze_the_loop(
    anyio_backend, client, slow_characters
):
    """The awkward one: the body read stays on the loop, the rest moved.

    The REAL handler is driven here, not the extracted worker body. Handing
    _import_character_sync to a thread from inside the test would prove only
    that anyio works - it would pass whether or not the handler ever threads
    anything. So a minimal ASGI request is built by hand and fed to the
    handler, which is also the only way to exercise the loop-bound body read
    and the threaded tail together.
    """
    freeze, out = await _longest_freeze(
        characters.import_character(_fake_request(b'{"name": "Imported"}'))
    )
    assert out["name"] == "Imported"
    assert freeze < MAX_FREEZE_S, f"loop frozen {freeze:.3f}s importing a character"


def _fake_request(body: bytes) -> Request:
    """The narrowest ASGI request _read_capped_body will accept."""
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/characters/import",
        "headers": [(b"content-length", str(len(body)).encode())],
    }
    return Request(scope, receive)


# ── behaviour is unchanged ───────────────────────────────────────────────────

def test_the_persona_round_trip_still_works(client):
    pid = _make_persona(client, "Round")
    assert client.post(f"/api/v1/personas/{pid}/select").status_code == 200
    listed = client.get("/api/v1/personas").json()
    assert [p["is_active"] for p in listed if p["id"] == pid] == [True]
    assert client.delete(f"/api/v1/personas/{pid}").status_code == 200
    assert client.delete(f"/api/v1/personas/{pid}").status_code == 404


def test_selecting_a_missing_persona_is_still_404(client):
    """HTTPException has to survive the thread hop."""
    assert client.post("/api/v1/personas/99999/select").status_code == 404
    assert client.get("/api/v1/characters/99999").status_code == 404


def test_importing_still_validates(client):
    assert client.post("/api/v1/characters/import",
                       content=b"not json").status_code == 400
    assert client.post("/api/v1/characters/import",
                       json={"name": "   "}).status_code == 400
    r = client.post("/api/v1/characters/import",
                    json={"data": {"name": "V2 Card", "tags": ["a", 3]}})
    assert r.status_code == 201
    assert r.json()["name"] == "V2 Card"
    assert r.json()["tags"] == ["a"]


# ── the dangling reference select_persona used to leave behind ───────────────

def test_selecting_cannot_commit_against_a_persona_that_was_just_deleted(
    client, monkeypatch
):
    """The guard read and the settings write are one transaction now.

    They used to be two, on two connections. A delete landing in between left
    selected_persona_id naming a deleted persona, silently. The window is
    widened here by stalling inside the transaction, right after the guard, so
    the delete gets every chance to land.

    The assertion is the invariant, not the winner: whichever commits second,
    the setting must never point at a persona that is gone.
    """
    pid = _make_persona(client, "Doomed")

    # Stall the SECOND connection - the one the old code opened to write the
    # setting after its guard had already committed and closed. That gap, and
    # only that gap, is the bug. Stalling the guard's own connection instead
    # would put the delete BEFORE the guard read, where both the old and the
    # new code correctly answer 404, and the test would pass either way.
    import database

    real_set_setting = database.set_setting
    inside = threading.Event()

    def stalling_set_setting(*args, **kwargs):
        inside.set()
        time.sleep(0.3)
        return real_set_setting(*args, **kwargs)

    monkeypatch.setattr(database, "set_setting", stalling_set_setting)

    def select():
        try:
            personas._select_persona_sync(pid)
        except Exception:        # a 404 here is a legitimate outcome
            pass

    t = threading.Thread(target=select)
    t.start()
    # The fixed version never calls set_setting at all - the write is inlined
    # in the guard's transaction - so the event does not fire and this simply
    # waits for the select to finish. Either way the delete below lands after
    # the guard read, which is the interleaving under test.
    if not inside.wait(2):
        t.join(5)
    personas._delete_persona_sync(pid)
    t.join(20)
    assert not t.is_alive(), "the select transaction never finished"

    with get_db() as con:
        row = con.execute(
            "SELECT value FROM settings WHERE key = 'selected_persona_id'"
        ).fetchone()
        selected = row["value"] if row else None
        still_there = con.execute(
            "SELECT 1 FROM personas WHERE id = ?", (pid,)
        ).fetchone() is not None

    assert not (selected == str(pid) and not still_there), (
        "selected_persona_id points at a deleted persona: the guard and the "
        "write did not share a transaction"
    )
