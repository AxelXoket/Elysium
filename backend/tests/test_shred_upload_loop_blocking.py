"""Audit KÖK 8, the heavy-I/O pair: a shred and an upload ran on the loop.

Two handlers, both doing real disk work synchronously on the event loop:

  POST /vault/discard-plaintext-backup   shreds the pre-vault PLAINTEXT copy of
      the whole database - every byte overwritten before the unlink, so the
      cost scales with everything the user ever wrote. Its two siblings in the
      same file have always run in a thread; this one was simply missed.

  POST /tts/voices/{id}                  makes a directory, writes the clip,
      shreds the PREVIOUS clip and writes two metadata files. A user can do
      this in the middle of a conversation, and on the loop all of it froze
      whatever reply was streaming.

Measured as the longest stretch the loop went without control while the handler
ran, with a real stall injected into the blocking call. A tick count would not
separate these: both handlers await something else as well, so the loop ticks
either way.
"""
from __future__ import annotations

import asyncio
import time
import wave
from io import BytesIO

import pytest
from starlette.datastructures import UploadFile as StarletteUploadFile

import database
import routers.tts_runtime as tts_runtime
import routers.vault as vault_router
from tts import refs

from tests.loop_guard import (
    MAX_FREEZE_S,
    MIN_TICKS,
    STALL_S as _STALL_S,
    longest_freeze as _longest_freeze,
    ticks_during as _ticks_during,
)




def _wav_bytes(seconds: float = 3.0, rate: int = 22050) -> bytes:
    buf = BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


# ── the vault backup shred ───────────────────────────────────────────────────

@pytest.fixture()
def a_plaintext_backup(tmp_path, monkeypatch):
    """One pre-vault backup sitting next to the live database."""
    live = tmp_path / "app.db"
    live.write_bytes(b"encrypted-enough-for-this-test")
    (tmp_path / "app.db.plain.bak-20260101120000").write_bytes(b"x" * 4096)
    monkeypatch.setattr(database, "DB_PATH", str(live))
    return live


@pytest.fixture()
def slow_shred(monkeypatch):
    real = database.discard_plaintext_backups

    def slow():
        time.sleep(_STALL_S)
        return real()

    monkeypatch.setattr(database, "discard_plaintext_backups", slow)


@pytest.mark.anyio
async def test_discarding_a_backup_does_not_freeze_the_loop(
    anyio_backend, client, a_plaintext_backup, slow_shred
):
    freeze, out = await _longest_freeze(vault_router.discard_plaintext_backup())
    assert out["removed"] == 1
    assert out["left"] == []
    assert freeze < MAX_FREEZE_S, f"loop frozen {freeze:.3f}s shredding a backup"


def test_discarding_a_backup_still_removes_the_file(client, a_plaintext_backup):
    """Behaviour guard: the thread hop must not change what happens on disk."""
    backup = a_plaintext_backup.with_name(
        a_plaintext_backup.name + ".plain.bak-20260101120000")
    assert backup.exists()
    r = client.post("/api/v1/vault/discard-plaintext-backup")
    assert r.status_code == 200
    assert r.json() == {"removed": 1, "left": []}
    assert not backup.exists()
    assert a_plaintext_backup.exists(), "the live database must be untouched"


# ── the voice upload ─────────────────────────────────────────────────────────

@pytest.fixture()
def refs_root(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "TTS_REFS_DIR", str(tmp_path / "refs"))
    return tmp_path


@pytest.fixture()
def slow_save_upload(monkeypatch):
    real = refs.save_upload

    def slow(*args, **kwargs):
        time.sleep(_STALL_S)
        return real(*args, **kwargs)

    monkeypatch.setattr(refs, "save_upload", slow)


def _upload(data: bytes) -> StarletteUploadFile:
    return StarletteUploadFile(filename="clip.wav", file=BytesIO(data))


@pytest.mark.anyio
async def test_uploading_a_voice_does_not_freeze_the_loop(
    anyio_backend, client, refs_root, slow_save_upload
):
    """A voice clone uploaded mid-conversation must not stall the reply."""
    freeze, out = await _longest_freeze(
        tts_runtime.upload_voice("ayse", file=_upload(_wav_bytes()),
                                 label="Ayse", transcript="merhaba")
    )
    assert out["voice_id"] == "ayse"
    assert out["transcript"] == "merhaba"
    assert freeze < MAX_FREEZE_S, f"loop frozen {freeze:.3f}s saving a voice clip"


@pytest.mark.anyio
async def test_a_too_short_clip_is_still_refused(
    anyio_backend, client, refs_root
):
    """RefError has to survive the thread hop and still become an HTTP error."""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        await tts_runtime.upload_voice("kisa", file=_upload(_wav_bytes(0.2)),
                                       label="", transcript="")
    assert excinfo.value.status_code in (400, 422)
