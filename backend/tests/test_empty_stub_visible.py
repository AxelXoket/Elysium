"""The file recovery leaves behind must be sayable and removable.

adopt_orphaned_enc_tmp renames a 0-byte live app.db to .empty-stub-bak instead
of unlinking it, and the comment explains why: "the recovery path deleted a
file" is a sentence no one should have to read while diagnosing one. Right.

But that was where it stopped. The name reached no route, no response field and
no screen, and there was nothing in the app that could remove it. An
unexplained file beside the vault, permanently, whose only mention was a log
line from the launch that produced it - in an app whose pitch is that you can
see everything it keeps.

Not a data-at-rest problem: the file is provably empty, and these tests pin
that too, because the removal's whole safety argument rests on it.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from sqlcipher3 import dbapi2 as sqlite3

import config
import database
import routers.vault as vault_router

KEY = bytes(range(32))


@pytest.fixture()
def paths(tmp_path, monkeypatch):
    live = tmp_path / "app.db"
    monkeypatch.setattr(database, "DB_PATH", str(live))
    monkeypatch.setattr(config, "DB_PATH", str(live), raising=False)
    return live, live.with_name("app.db.empty-stub-bak")


def _make_encrypted_db(path: Path, key: bytes, marker: str) -> None:
    con = sqlite3.connect(str(path))
    database._key_pragma(con, key)
    con.execute("CREATE TABLE probe (v TEXT)")
    con.execute("INSERT INTO probe VALUES (?)", (marker,))
    con.commit()
    con.close()


def test_the_stub_a_recovery_leaves_behind_is_reported(paths):
    """Through the recovery that creates it, not by planting the file: the
    point is that a real adoption produces something visible."""
    live, stub = paths
    _make_encrypted_db(live.with_name("app.db.enc-tmp"), KEY, "real data")
    live.touch()

    assert database.empty_stub_present() is False
    assert database.adopt_orphaned_enc_tmp(KEY) is True
    assert stub.exists()
    assert database.empty_stub_present() is True


def test_the_stub_can_be_removed(paths):
    live, stub = paths
    stub.write_bytes(b"")

    removed, reason = database.discard_empty_stub()

    assert removed is True and reason == ""
    assert not stub.exists()
    assert database.empty_stub_present() is False


def test_removing_a_stub_that_is_not_there_says_so(paths):
    removed, reason = database.discard_empty_stub()
    assert removed is False
    assert reason == "not_present"


def test_a_stub_with_bytes_in_it_is_refused(paths, caplog):
    """The removal's entire safety argument is "there is provably nothing in
    it". Adoption only ever creates this name from a file it measured at zero,
    so a non-empty one means something else wrote there - and this is the one
    deletion in the app that never asks the user first."""
    live, stub = paths
    stub.write_bytes(b"someone put something here")

    removed, reason = database.discard_empty_stub()

    assert removed is False
    assert reason == "not_empty"
    assert stub.read_bytes() == b"someone put something here"
    assert any("NOT removed" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# Through the routes
# ---------------------------------------------------------------------------

def test_the_status_route_carries_the_field(client):
    body = client.get("/api/v1/vault/status").json()
    assert body["empty_stub"] is False

    Path(config.DB_PATH).with_name(
        Path(config.DB_PATH).name + ".empty-stub-bak").write_bytes(b"")

    assert client.get("/api/v1/vault/status").json()["empty_stub"] is True


def test_the_discard_route_removes_it_without_a_passphrase(client):
    """No unlock required, and for a stronger reason than the plaintext backup
    has: that file is readable without the passphrase, this one has nothing in
    it to read."""
    stub = Path(config.DB_PATH).with_name(
        Path(config.DB_PATH).name + ".empty-stub-bak")
    stub.write_bytes(b"")

    client.post("/api/v1/vault/lock")
    resp = client.post("/api/v1/vault/discard-empty-stub")

    assert resp.status_code == 200
    assert resp.json()["removed"] is True
    assert not stub.exists()


def test_a_removal_that_did_not_happen_is_not_reported_as_one(paths, monkeypatch):
    """secure_delete refuses names it will not touch and fails on locked ones,
    and it says so by returning False. Dropping that check and calling unlink
    anyway survived a mutation round: nothing here tried a shred that fails.

    The whole family of vault-discard routes exists to keep one promise - that
    "removed" means removed - so a swallowed refusal is the exact failure.
    """
    live, stub = paths
    stub.write_bytes(b"")
    monkeypatch.setattr(database.secure_delete, "shred", lambda p: False)

    removed, reason = database.discard_empty_stub()

    assert removed is False
    assert reason == "not_removed"
    assert stub.exists(), "the file was removed by something other than shred"


def test_a_stranded_copy_that_would_not_shred_is_reported_too(paths, monkeypatch):
    """The same gap, in the older sibling this one was modelled on."""
    live, _ = paths
    enc_tmp = live.with_name("app.db.enc-tmp")
    enc_tmp.write_bytes(b"anything")
    monkeypatch.setattr(database, "check_key", lambda *a, **kw: True)
    monkeypatch.setattr(database.secure_delete, "shred", lambda p: False)

    removed, reason = database.discard_orphaned_enc_tmp(KEY)

    assert removed is False
    assert reason == "in_use"
    assert enc_tmp.exists()


@pytest.mark.anyio
async def test_removing_the_stub_does_not_freeze_the_loop(
    anyio_backend, client, monkeypatch,
):
    """/vault/status got this test and the discard route beside it did not,
    which a mutation round noticed: stripping the thread hop here changed
    nothing that anything measured. Deletion touches the same disk."""
    stub = Path(config.DB_PATH).with_name(
        Path(config.DB_PATH).name + ".empty-stub-bak")
    stub.write_bytes(b"")

    real = database.discard_empty_stub

    def slow():
        time.sleep(0.12)
        return real()

    monkeypatch.setattr(vault_router.database, "discard_empty_stub", slow)

    ticks = 0

    async def heartbeat():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.005)
            ticks += 1

    beat = asyncio.ensure_future(heartbeat())
    try:
        await asyncio.sleep(0.02)
        before = ticks
        body = await vault_router.discard_empty_stub()
    finally:
        beat.cancel()

    assert body["removed"] is True
    assert ticks - before > 1, "the loop was frozen while the stub was removed"


def test_the_discard_route_refuses_a_non_empty_file(client):
    stub = Path(config.DB_PATH).with_name(
        Path(config.DB_PATH).name + ".empty-stub-bak")
    stub.write_bytes(b"not the stub")

    resp = client.post("/api/v1/vault/discard-empty-stub")

    assert resp.status_code == 200
    assert resp.json() == {"removed": False, "reason": "not_empty"}
    assert stub.read_bytes() == b"not the stub"
