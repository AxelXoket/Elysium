"""Audit KÖK 8, read half: the three chat reads still ran on the event loop.

Every WRITE handler in routers/chats.py was moved to a worker thread because
taking SQLite's writer lock on the loop freezes every live SSE stream in the
process. The reads were left behind on the theory that a read is cheap. It is
not: SQLCipher decrypts page by page on the calling thread, so the cost scales
with the bytes touched and no lock is involved either way.

`GET /chats/{id}/messages` is the one that actually hurt. The client refetches
it unconditionally at the end of every exchange, so an entire transcript was
being decrypted on the loop at the exact moment the streaming generator for
that same chat was trying to ship its next sentence of audio.

The discriminator below is the one test_voice_loop_blocking.py already uses: a
heartbeat coroutine counts ticks while the handler runs. Off the loop, the loop
keeps ticking. On the loop, it does not.
"""
from __future__ import annotations

import asyncio
import time

import pytest

import routers.chats as chats
from database import get_db
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
    """Make every connection this module opens cost real wall-clock time.

    The sleep stands in for page decryption. It is deliberately placed in
    routers.chats' own reference to get_db so that only the handlers under test
    are slowed - the fixture setup below still runs at full speed.
    """
    real = chats.get_db

    def slow():
        time.sleep(_STALL_S)
        return real()

    monkeypatch.setattr(chats, "get_db", slow)



def _seed(client) -> int:
    char = make_character(client, first_mes="Hello there.")
    chat = make_chat(client, char)
    with get_db() as con:
        for i in range(20):
            con.execute(
                "INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)",
                (chat, "user" if i % 2 == 0 else "assistant", f"line {i}"),
            )
    return chat


# ── the loop keeps running ───────────────────────────────────────────────────

@pytest.mark.anyio
async def test_listing_chats_does_not_freeze_the_loop(anyio_backend, client, slow_db):
    _seed(client)
    ticks, out = await _ticks_during(chats.list_chats())
    assert len(out) == 1
    assert ticks >= MIN_TICKS, "the loop was frozen while the chat list was read"


@pytest.mark.anyio
async def test_reading_one_chat_does_not_freeze_the_loop(anyio_backend, client, slow_db):
    chat = _seed(client)
    ticks, out = await _ticks_during(chats.get_chat(chat))
    assert out["id"] == chat
    assert ticks >= MIN_TICKS, "the loop was frozen while one chat was read"


@pytest.mark.anyio
async def test_reading_a_transcript_does_not_freeze_the_loop(anyio_backend, client, slow_db):
    """The heaviest read, and the one that runs while audio is in flight."""
    chat = _seed(client)
    ticks, out = await _ticks_during(chats.list_messages(chat))
    assert len(out) == 21                      # greeting + 20 seeded rows
    assert ticks >= MIN_TICKS, "the loop was frozen while a transcript was decrypted"


# ── and moving them did not change what they return ──────────────────────────

def test_a_missing_chat_is_still_a_clean_404(client):
    """HTTPException has to survive the thread hop, or a 404 becomes a 500."""
    assert client.get("/api/v1/chats/99999").status_code == 404
    assert client.get("/api/v1/chats/99999/messages").status_code == 404


def test_the_transcript_still_carries_variants_and_attachments_keys(client):
    chat = _seed(client)
    body = client.get(f"/api/v1/chats/{chat}/messages").json()
    assert [m["role"] for m in body][:3] == ["assistant", "user", "assistant"]
    for m in body:
        assert m["attachments"] == []
        assert m["variant_index"] == 0 and m["variant_count"] == 1


def test_the_chat_list_still_counts_only_active_messages(client):
    chat = _seed(client)
    body = client.get("/api/v1/chats").json()
    assert len(body) == 1
    assert body[0]["id"] == chat
    assert body[0]["message_count"] == 21
