"""Audit KÖK 18: the two paths that destroyed user data without a word.

Both share a shape. A question about the filesystem ("is this row's file
still there?", "is there a live database here?") was answered by a call that
cannot tell "no" from "could not look", and the destructive branch ran on the
optimistic reading. These tests drive the ambiguous state directly, because
neither bug is reachable from the happy path - which is exactly why both
shipped green.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlcipher3 import dbapi2 as sqlite3

import config
import database
import legacy_migration
import vault_state


# ---------------------------------------------------------------------------
# 1. reconcile must not read "cannot list the uploads dir" as "no files"
# ---------------------------------------------------------------------------

TEST_VAULT_KEY = bytes(range(32))


def _break_iterdir(monkeypatch, target: str):
    """Make listing exactly one directory fail the way a locked one does.

    A genuinely unreadable directory is not portable to create - Windows ACLs
    and POSIX modes disagree, and a suite running as root ignores the mode
    entirely - so the failure is injected at the single call the fix now
    depends on. Returns the original for restoring mid-test.
    """
    real = Path.iterdir

    def _boom(self):
        if str(self) == target:
            raise PermissionError(13, "denied")
        return real(self)

    monkeypatch.setattr(Path, "iterdir", _boom)
    return real


@pytest.fixture
def unlocked_db(tmp_path, monkeypatch, request):
    """A keyed, schema-built DB without the HTTP stack.

    conftest's `client` fixture would do, but these tests drive the migration
    functions directly and one of them breaks Path.iterdir process-wide -
    something a live TestClient has no reason to survive.
    """
    db_path = str(tmp_path / "app.db")
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(database, "DB_PATH", db_path)
    request.addfinalizer(vault_state.clear_key)
    vault_state.set_key(TEST_VAULT_KEY)
    database.init_db()
    return db_path


@pytest.fixture
def _uploads(tmp_path, monkeypatch):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr(config, "UPLOADS_DIR", str(uploads), raising=False)
    return uploads


def test_an_unlistable_uploads_dir_raises_instead_of_looking_empty(
    _uploads, monkeypatch,
):
    _break_iterdir(monkeypatch, config.UPLOADS_DIR)
    with pytest.raises(legacy_migration.UploadsUnreadable):
        legacy_migration.migrate_upload_files_to_blobs()


def test_a_missing_uploads_dir_is_still_an_ordinary_clean_pass(
    _uploads, monkeypatch,
):
    """The state migrate_upload_files_to_blobs itself creates when it finishes:
    it rmdir's the directory once empty. That must stay quiet."""
    _uploads.rmdir()
    migrated, failed, removed = legacy_migration.migrate_upload_files_to_blobs()
    assert (migrated, failed, removed) == (0, set(), 0)


def test_reconcile_refuses_to_delete_rows_it_could_not_check(
    _uploads, unlocked_db, monkeypatch,
):
    """The core of the finding.

    A row whose blob has not landed yet is protected by its file being on
    disk. When the directory cannot be listed, the old per-sha is_file()
    answered False for every one of them and the whole table went.
    """
    sha = "a" * 64
    (_uploads / f"{sha}.png").write_bytes(b"not really a png")
    with database.get_db() as con:
        con.execute(
            "INSERT INTO attachments (message_id, sha256, mime, width, "
            "height, byte_size) VALUES (NULL, ?, 'image/png', 1, 1, 16)",
            (sha,),
        )

    real_iterdir = _break_iterdir(monkeypatch, config.UPLOADS_DIR)
    with pytest.raises(legacy_migration.UploadsUnreadable):
        legacy_migration.reconcile_attachments_without_blobs(set())

    monkeypatch.setattr(Path, "iterdir", real_iterdir)
    with database.get_db() as con:
        still_there = con.execute(
            "SELECT count(*) c FROM attachments WHERE sha256 = ?", (sha,)
        ).fetchone()["c"]
    assert still_there == 1, "the row was deleted on a directory we could not read"


def test_reconcile_still_drops_rows_when_the_dir_is_genuinely_gone(
    _uploads, unlocked_db,
):
    """The other half: an absent directory really does mean unrecoverable,
    and the guard must not turn reconcile into a no-op."""
    sha = "b" * 64
    with database.get_db() as con:
        con.execute(
            "INSERT INTO attachments (message_id, sha256, mime, width, "
            "height, byte_size) VALUES (NULL, ?, 'image/png', 1, 1, 16)",
            (sha,),
        )
    _uploads.rmdir()

    assert legacy_migration.reconcile_attachments_without_blobs(set()) == 1
    with database.get_db() as con:
        assert con.execute(
            "SELECT count(*) c FROM attachments WHERE sha256 = ?", (sha,)
        ).fetchone()["c"] == 0


# ---------------------------------------------------------------------------
# 2. crash recovery must not be blocked by a 0-byte stub
# ---------------------------------------------------------------------------

def _make_encrypted_db(path: Path, key: bytes, marker: str) -> None:
    con = sqlite3.connect(path)
    database._key_pragma(con, key)
    con.execute("CREATE TABLE probe (v TEXT)")
    con.execute("INSERT INTO probe VALUES (?)", (marker,))
    con.commit()
    con.close()


def _read_marker(path: Path, key: bytes) -> str | None:
    con = sqlite3.connect(path)
    try:
        database._key_pragma(con, key)
        return con.execute("SELECT v FROM probe").fetchone()[0]
    except sqlite3.DatabaseError:
        return None
    finally:
        con.close()


@pytest.fixture
def _db_paths(tmp_path, monkeypatch):
    live = tmp_path / "app.db"
    monkeypatch.setattr(database, "DB_PATH", str(live))
    monkeypatch.setattr(config, "DB_PATH", str(live), raising=False)
    return live, live.with_name("app.db.enc-tmp")


def test_a_zero_byte_stub_no_longer_blocks_adoption(_db_paths):
    """The reported failure, start to finish.

    /vault/init writes the identity files, init_db() creates app.db, the
    process dies before the schema lands - check_key's own docstring records
    that window. Adoption saw a file, declined, and init_db() then built a
    fresh empty vault next to the user's real data.
    """
    live, enc_tmp = _db_paths
    key = bytes(range(32))
    _make_encrypted_db(enc_tmp, key, "the user's real data")
    live.touch()
    assert live.stat().st_size == 0

    assert database.adopt_orphaned_enc_tmp(key) is True
    assert _read_marker(live, key) == "the user's real data"
    assert not enc_tmp.exists()
    # Never unlinked, even at zero bytes.
    assert live.with_name("app.db.empty-stub-bak").exists()


def test_adoption_still_works_when_the_live_file_is_absent(_db_paths):
    live, enc_tmp = _db_paths
    key = bytes(range(32))
    _make_encrypted_db(enc_tmp, key, "recovered")
    assert database.adopt_orphaned_enc_tmp(key) is True
    assert _read_marker(live, key) == "recovered"


def test_a_real_live_database_is_never_replaced(_db_paths, caplog):
    """The guard on the other side: two usable databases is an ambiguous
    state, not a crashed swap, so neither is touched and it is reported."""
    live, enc_tmp = _db_paths
    key = bytes(range(32))
    _make_encrypted_db(live, key, "live")
    _make_encrypted_db(enc_tmp, key, "orphan")

    assert database.adopt_orphaned_enc_tmp(key) is False
    assert _read_marker(live, key) == "live"
    assert enc_tmp.exists(), "a full copy of the vault was removed"
    assert any("NOT adopted" in r.message for r in caplog.records)


def test_an_orphan_that_does_not_open_is_left_alone(_db_paths, caplog):
    live, enc_tmp = _db_paths
    key = bytes(range(32))
    other = bytes(range(1, 33))
    _make_encrypted_db(enc_tmp, other, "someone else's")

    assert database.adopt_orphaned_enc_tmp(key) is False
    assert enc_tmp.exists()
    assert any("does not open" in r.message for r in caplog.records)


def test_a_stranded_orphan_is_visible_not_just_logged(_db_paths):
    live, enc_tmp = _db_paths
    assert database.orphaned_enc_tmp_present() is False
    enc_tmp.write_bytes(b"x")
    assert database.orphaned_enc_tmp_present() is True


# ---------------------------------------------------------------------------
# 3. get_db must not leak a handle when its own setup fails
# ---------------------------------------------------------------------------

def test_a_failing_pragma_does_not_leave_the_connection_open(
    unlocked_db, monkeypatch, tmp_path,
):
    """On Windows an open handle blocks the very renames adopt_orphaned_enc_tmp
    performs, so a leak in the read path becomes a failure in the recovery
    path. Proven by closing over the connection the generator built.
    """
    made: list[sqlite3.Connection] = []
    real_connect = sqlite3.connect

    def _tracking_connect(*a, **kw):
        con = real_connect(*a, **kw)
        made.append(con)
        return con

    monkeypatch.setattr(sqlite3, "connect", _tracking_connect)
    monkeypatch.setattr(
        database, "_key_pragma",
        lambda con, key: (_ for _ in ()).throw(sqlite3.DatabaseError("bad key")),
    )

    with pytest.raises(sqlite3.DatabaseError):
        with database.get_db():
            pass

    assert made, "the test did not observe a connection"
    for con in made:
        with pytest.raises(sqlite3.ProgrammingError):
            con.execute("SELECT 1")
