"""A 0-byte app.db must not be described as an encrypted vault.

/vault/status and /vault/init both asked the same question the same way:
`Path(DB_PATH).exists() and not is_plaintext_db()`. Read that as "is there an
encrypted database here" and it is wrong, because is_plaintext_db() answers
False three different ways - encrypted, gone, and zero bytes.

The zero-byte case is reachable in one step (check_key's own docstring
describes it: identity files land, init_db creates the file, the process dies
before the schema does). What the app then said about it:

  - /vault/status: initialized=true, so the UI shows the unlock screen,
  - /vault/unlock: every passphrase comes back wrong, because a missing
    verifier sends it to DB-validated recovery and check_key refuses 0-byte
    files by design,
  - /vault/init: 409 encrypted_db_without_identity.

Three routes agreeing that data is present and merely locked, about a file with
nothing in it, with no way back to setup. That is the failure these tests pin.

The second half of the file is about where this work runs. /vault/status opens
files and hands SQLCipher keys to try, and the frontend polls it on a timer -
so it stalled the event loop at a fixed cadence forever.
"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

import config
import database
import routers.vault as vault_router


# ---------------------------------------------------------------------------
# classify_db_file: one question, four answers
# ---------------------------------------------------------------------------

def test_a_missing_file_is_absent(tmp_path: Path):
    assert database.classify_db_file(str(tmp_path / "nope.db")) == database.DB_ABSENT


def test_a_zero_byte_file_is_empty_not_encrypted(tmp_path: Path):
    target = tmp_path / "app.db"
    target.write_bytes(b"")
    assert database.classify_db_file(str(target)) == database.DB_EMPTY


def test_a_readable_sqlite_file_is_plaintext(tmp_path: Path):
    from sqlcipher3 import dbapi2 as sqlite3

    target = tmp_path / "app.db"
    con = sqlite3.connect(str(target))
    con.execute("CREATE TABLE t (a)")
    con.commit()
    con.close()
    assert database.classify_db_file(str(target)) == database.DB_PLAINTEXT


def test_bytes_that_are_not_a_readable_database_are_encrypted(tmp_path: Path):
    """"Encrypted" here means "present, non-empty, and not openable without a
    key" - which is exactly what a real vault file looks like from outside."""
    target = tmp_path / "app.db"
    target.write_bytes(b"\x91\x2b\xd4" * 64)
    assert database.classify_db_file(str(target)) == database.DB_ENCRYPTED


def test_the_file_cannot_be_swapped_while_it_is_being_classified(
    tmp_path: Path, monkeypatch,
):
    """The handle is held across the probe on purpose, and this is the claim
    made testable: on Windows a file opened this way cannot be renamed or
    deleted until it closes, so the file the probe reads is provably the one
    whose size was just measured.

    A mutation that narrowed the `with` block to the fstat alone survived the
    first round of these tests, because a guarantee about what ANOTHER caller
    cannot do is invisible to a test that only calls this one. So the attempt
    is made from inside, at exactly the moment the window would be open.
    """
    if os.name != "nt":
        pytest.skip("the lock this asserts is Windows file-sharing semantics")

    target = tmp_path / "app.db"
    target.write_bytes(b"\x91\x2b\xd4" * 64)
    attempted: list[BaseException | None] = []

    real = database.is_plaintext_db

    def probing(path):
        try:
            os.unlink(target)
        except OSError as exc:
            attempted.append(exc)
        else:
            attempted.append(None)
        return real(path)

    monkeypatch.setattr(database, "is_plaintext_db", probing)

    assert database.classify_db_file(str(target)) == database.DB_ENCRYPTED
    assert attempted and isinstance(attempted[0], OSError), (
        "the file was deletable mid-classification, so the handle was not held"
    )
    assert target.exists()


def test_asking_about_a_missing_file_does_not_create_one(tmp_path: Path):
    """sqlite3.connect() on a missing path makes a fresh empty database and
    then reports it as readable plaintext. A probe that leaves a file behind
    turns a lost app.db into a migration candidate."""
    target = tmp_path / "gone.db"
    database.classify_db_file(str(target))
    assert not target.exists()


# ---------------------------------------------------------------------------
# What the two routes say about an empty file
# ---------------------------------------------------------------------------

#: Not a real vault key, and it does not have to be: can_derive() only asks
#: whether salt.bin is on disk. Every test below is about that question and the
#: shape of the database file, never about deriving anything.
_SALT = b"\x17" * 16

#: Long enough for _check_length, and not on the common-passphrase list.
_PASSPHRASE = "quilt harbor lantern mosaic"


def _strand(db_bytes: bytes) -> Path:
    """The state this is all about: salt kept, verifier lost, DB is `db_bytes`.

    A lost verifier is what sends /vault/status down the "or an encrypted DB
    still opens" branch, so it is the only state where the file's real nature
    decides what the user is offered.
    """
    db_path = Path(config.DB_PATH)
    vault = vault_router._vault()
    vault.salt_path.write_bytes(_SALT)
    vault.verifier_path.unlink(missing_ok=True)
    db_path.write_bytes(db_bytes)
    return db_path


#: What a vault file looks like from outside: present, and not openable by
#: SQLite without a key.
_ENCRYPTED_LOOKING = b"\x91\x2b\xd4" * 64


def test_an_empty_database_is_not_reported_as_an_initialized_vault(client):
    _strand(b"")

    body = client.get("/api/v1/vault/status").json()

    assert body["initialized"] is False, (
        "the unlock screen was offered for a vault with nothing in it, and "
        "unlock can never succeed there"
    )


def test_setup_is_allowed_over_an_empty_database(client):
    """The other half. Answering initialized=false and then refusing /init
    would only move the dead end one route along."""
    _strand(b"")

    resp = client.post("/api/v1/vault/init", json={"passphrase": _PASSPHRASE})

    assert resp.status_code == 200, resp.json()


def test_setup_is_still_refused_over_a_real_encrypted_database(client):
    """The guard that must survive: a fresh salt can never open old data, so
    minting a new identity over an encrypted file destroys access to it."""
    _strand(_ENCRYPTED_LOOKING)

    resp = client.post("/api/v1/vault/init", json={"passphrase": _PASSPHRASE})

    assert resp.status_code == 409
    assert resp.json()["detail"] == "encrypted_db_without_identity"


def test_an_encrypted_database_with_no_salt_left_is_not_called_initialized(
    client,
):
    """The other half of that same condition. An encrypted file with the salt
    ALSO gone cannot be opened by any passphrase, so offering the unlock screen
    would be an invitation to type one forever.

    Its own test because a mutation that deleted `and vault.can_derive()`
    survived: every other case here has a salt on disk, so the term was always
    true and never load-bearing.
    """
    db_path = Path(config.DB_PATH)
    vault = vault_router._vault()
    vault.verifier_path.unlink(missing_ok=True)
    vault.salt_path.unlink(missing_ok=True)
    db_path.write_bytes(_ENCRYPTED_LOOKING)

    body = client.get("/api/v1/vault/status").json()

    assert body["initialized"] is False


def test_an_encrypted_database_with_a_lost_verifier_still_offers_unlock(client):
    """The branch this whole expression exists for. Narrowing it to DB_ENCRYPTED
    must not have narrowed it away."""
    _strand(_ENCRYPTED_LOOKING)

    body = client.get("/api/v1/vault/status").json()

    assert body["initialized"] is True


# ---------------------------------------------------------------------------
# And it does not run on the event loop
# ---------------------------------------------------------------------------

#: Long enough that a blocked loop is unambiguous, short enough to stay cheap.
_STALL_S = 0.12


@pytest.fixture()
def slow_classification(monkeypatch):
    """Stand in for the page decryption /vault/status pays for on a real vault.

    Patched on the vault router's own reference to `database`, so only the
    handler under test is slowed.
    """
    real = database.classify_db_file

    def slow(*a, **kw):
        time.sleep(_STALL_S)
        return real(*a, **kw)

    monkeypatch.setattr(vault_router.database, "classify_db_file", slow)


async def _ticks_during(coro) -> tuple[int, object]:
    """Run `coro`, counting how many times the loop got control meanwhile."""
    ticks = 0

    async def heartbeat():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.005)
            ticks += 1

    beat = asyncio.ensure_future(heartbeat())
    try:
        await asyncio.sleep(0.02)          # let the heartbeat settle
        before = ticks
        result = await coro
        return ticks - before, result
    finally:
        beat.cancel()


def test_a_lock_landing_mid_status_is_answered_not_raised(client, monkeypatch):
    """/vault/status is the one route that must answer while locked, and the
    idle watchdog takes the key away on its own schedule.

    The handler used to ask is_unlocked() and then, several file reads later,
    get_key(). A lock landing in that gap made get_key() raise, and the route
    whose whole job is to say "you are locked" answered 423 instead of saying
    it. Reading the key ONCE and deriving both answers from that snapshot is
    what closes the gap rather than narrowing it - so this asserts the payload
    describes one instant, not two.
    """
    import vault_state

    orphan = Path(config.DB_PATH)
    orphan = orphan.with_name(orphan.name + ".enc-tmp")
    orphan.write_bytes(_ENCRYPTED_LOOKING)

    real = database.orphaned_enc_tmp_present

    def locking_probe():
        # Stands in for the watchdog firing part-way through the handler. This
        # call sits between the key read and its use, which is exactly where
        # the old gap was.
        vault_state.clear_key()
        return real()

    monkeypatch.setattr(vault_router.database, "orphaned_enc_tmp_present",
                        locking_probe)

    resp = client.get("/api/v1/vault/status")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # The coherence that matters: "unlocked" and "we could answer the readable
    # question" have to agree. Either answer is fine; disagreeing is not, and
    # neither is refusing to answer at all.
    assert body["orphaned_copy"] is True
    assert (body["orphaned_copy_readable"] is not None) is body["unlocked"]


@pytest.mark.anyio
async def test_reading_vault_status_does_not_freeze_the_loop(
    anyio_backend, client, slow_classification,
):
    ticks, body = await _ticks_during(vault_router.vault_status())
    assert "initialized" in body
    assert ticks > 1, "the loop was frozen while /vault/status touched the disk"
