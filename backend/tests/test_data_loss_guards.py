"""Audit KÖK 18: the two paths that destroyed user data without a word.

Both share a shape. A question about the filesystem ("is this row's file
still there?", "is there a live database here?") was answered by a call that
cannot tell "no" from "could not look", and the destructive branch ran on the
optimistic reading. These tests drive the ambiguous state directly, because
neither bug is reachable from the happy path - which is exactly why both
shipped green.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest
from sqlcipher3 import dbapi2 as sqlite3

import config
import database
import legacy_migration
import secure_delete


# ---------------------------------------------------------------------------
# 1. reconcile must not read "cannot list the uploads dir" as "no files"
# ---------------------------------------------------------------------------


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
    _uploads, db, monkeypatch,
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
    _uploads, db,
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
    db, monkeypatch, tmp_path,
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


# ---------------------------------------------------------------------------
# K-14: the plaintext journal files a migration leaves behind
# ---------------------------------------------------------------------------

#: What a -wal file holds after a checkpoint that could not finish: committed
#: pages of the database, in the clear. Distinctive so a test can find it.
SIDECAR_PAGES = b"the user's own conversation, in the clear" * 40


def _make_plaintext_db(path: Path, marker: str) -> None:
    """A pre-vault app.db: an ordinary unkeyed SQLite file with content."""
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE probe (v TEXT)")
    con.execute("INSERT INTO probe VALUES (?)", (marker,))
    con.commit()
    con.close()


def _plant_sidecars(live: Path) -> list[Path]:
    planted = []
    for suffix in ("-wal", "-shm", "-journal"):
        path = live.with_name(live.name + suffix)
        path.write_bytes(SIDECAR_PAGES)
        planted.append(path)
    return planted


class TestThePlaintextJournalFilesDoNotSurvive:
    """The remnant nothing could see, delete, or even report.

    `wal_checkpoint(TRUNCATE)` before the export does not raise when it cannot
    finish - a reader still holding the file makes it return without
    truncating - so committed chat pages stay in `app.db-wal`. That file is
    plaintext, it has no status field, no discard route and no log line, and
    it sits beside an `app.db` the app calls encrypted.

    Two windows, and the ledger only had the first:

      * a crash between the two renames, where the next unlock adopts the
        encrypted copy and never looks at the sidecars;
      * a crash AFTER the second rename, where adoption cannot run at all
        because the file it looks for became the live database.

    Both close for the same reason: the discard now happens while DB_PATH is
    empty, which is the one instant at which a file called `app.db-wal` is
    provably an orphan.
    """

    def test_a_migration_destroys_them_BEFORE_the_vault_takes_the_name(
        self, tmp_path, monkeypatch
    ):
        """Not that they go - WHEN they go. That is the whole fix.

        Removing them after the second rename passes on a run that finishes,
        and that is exactly how the defect survived: a crash in the window
        between the two leaves plaintext pages beside a database the app calls
        encrypted, and nothing ever looks again. adopt_orphaned_enc_tmp cannot
        help there either - the file it looks for became the live database.

        So the assertion is on the ordering: at the instant the encrypted copy
        takes the live name, the sidecars must already be gone. Written this
        way after the first version passed with the block back in its old
        place.

        They are planted at the first rename rather than up front because
        SQLite deletes -wal and -shm itself on a clean close, long before any
        of this. The remnant exists only when it could not, which is the same
        condition that makes wal_checkpoint(TRUNCATE) return without
        truncating.
        """
        live = tmp_path / "app.db"
        monkeypatch.setattr(database, "DB_PATH", str(live))
        monkeypatch.setattr(config, "DB_PATH", str(live), raising=False)
        _make_plaintext_db(live, "the user's real data")

        planted: list[Path] = []
        alive_at_swap: list[str] = []
        real_rename = database._rename_with_retry

        def watching_rename(src, dest):
            if planted:
                # The second rename: the encrypted copy is about to become
                # app.db. Look before it does.
                alive_at_swap.extend(q.name for q in planted if q.exists())
            real_rename(src, dest)
            if not planted:
                planted.extend(_plant_sidecars(Path(live)))

        monkeypatch.setattr(database, "_rename_with_retry", watching_rename)

        backup = database.migrate_plaintext_to_encrypted(bytes(range(32)))

        assert backup and planted, "the setup did not happen"
        assert alive_at_swap == [], (
            f"still there when the vault took the name: {alive_at_swap}. A "
            f"crash one instant later leaves them for good.")
        assert _read_marker(live, bytes(range(32))) == "the user's real data"

    def test_the_recovery_destroys_them_before_the_swap_as_well(
        self, _db_paths, monkeypatch
    ):
        # The other window, and the same ordering question. Here the crash has
        # already happened - DB_PATH is absent, the encrypted copy is waiting -
        # so this is the last chance anything has to tell an orphan from a
        # healthy vault's own journal.
        live, enc_tmp = _db_paths
        key = bytes(range(32))
        _make_encrypted_db(enc_tmp, key, "the user's real data")
        planted = _plant_sidecars(Path(live))

        alive_at_swap: list[str] = []
        real_replace = Path.replace

        def watching_replace(self, target):
            if Path(target) == Path(live):
                alive_at_swap.extend(q.name for q in planted if q.exists())
            return real_replace(self, target)

        monkeypatch.setattr(Path, "replace", watching_replace)

        assert database.adopt_orphaned_enc_tmp(key) is True

        assert alive_at_swap == [], (
            f"still there when the vault took the name: {alive_at_swap}")
        assert _read_marker(live, key) == "the user's real data"

    def test_they_are_overwritten_rather_than_unlinked(self, tmp_path,
                                                       monkeypatch):
        # A plain unlink returns the blocks with the pages intact. This is the
        # difference between "gone from the listing" and "gone".
        live = tmp_path / "app.db"
        wal = live.with_name("app.db-wal")
        wal.write_bytes(SIDECAR_PAGES)
        monkeypatch.setattr(os, "unlink", lambda *a, **kw: None)

        assert database.discard_plaintext_sidecars(live) == []

        survivor = wal.read_bytes()
        assert len(survivor) == len(SIDECAR_PAGES), "the length changed"
        assert survivor != SIDECAR_PAGES, "unlinked without overwriting"

    def test_a_sidecar_that_could_not_be_destroyed_is_named(self, tmp_path,
                                                            monkeypatch):
        """The silent half, which needs no crash at all.

        discard() returns False and, for an ordinary OSError, logs nothing -
        an antivirus scan holding the file is exactly that. So a full
        plaintext WAL could survive a completely successful migration with not
        one line anywhere to say so. Returning the names is what makes the
        caller able to.
        """
        live = tmp_path / "app.db"
        for suffix in ("-wal", "-shm"):
            live.with_name(live.name + suffix).write_bytes(SIDECAR_PAGES)
        monkeypatch.setattr(secure_delete, "discard",
                            lambda p: Path(p).name != "app.db-wal")

        assert database.discard_plaintext_sidecars(live) == ["app.db-wal"]

    def test_it_never_lets_an_exception_out(self, tmp_path, monkeypatch):
        """It runs where DB_PATH does not exist. Nothing may escape.

        An exception here leaves the live database path EMPTY, which is a
        worse state than the leak this closes. MemoryError is the realistic
        one: shred allocates os.urandom(st_size) in one go, and a -wal gets
        large in exactly the case this function exists for.
        """
        live = tmp_path / "app.db"
        live.with_name("app.db-wal").write_bytes(SIDECAR_PAGES)

        def explode(path):
            raise MemoryError("a WAL larger than the free heap")

        monkeypatch.setattr(secure_delete, "discard", explode)

        assert database.discard_plaintext_sidecars(live) == ["app.db-wal"]

    def test_a_name_that_is_not_there_is_not_reported_as_stuck(self, tmp_path):
        # The floor. Without the lexists gate every migration would report
        # three survivors, every time, and the warning would stop being read.
        live = tmp_path / "app.db"
        assert database.discard_plaintext_sidecars(live) == []

    def test_the_key_free_discard_route_still_cannot_reach_them(
        self, tmp_path, monkeypatch
    ):
        """The discriminating half, and the reason the fix sits where it does.

        The obvious fix was to report these through plaintext_backups(), so
        the existing discard route would sweep them up. That route needs no
        passphrase - deliberately, because it removes what is readable without
        one - and it would then have been able to shred a LIVE encrypted WAL
        holding committed pages that had not reached the main file yet. The
        fix would have been worse than the defect.

        So: a healthy vault's own journal must remain invisible to that route,
        and this is what says so.
        """
        live = tmp_path / "app.db"
        monkeypatch.setattr(database, "DB_PATH", str(live))
        monkeypatch.setattr(config, "DB_PATH", str(live), raising=False)
        live.write_bytes(b"an encrypted database, very much alive")
        wal = live.with_name("app.db-wal")
        pages = b"its own uncheckpointed pages, still encrypted" * 20
        wal.write_bytes(pages)
        # A real one, so the route has something to find and cannot pass by
        # finding nothing at all.
        genuine = live.with_name("app.db.plain.bak-1700000000")
        genuine.write_bytes(b"the pre-vault copy")

        assert [p.name for p in database.plaintext_backups()] == [genuine.name]
        database.discard_plaintext_backups()

        assert wal.read_bytes() == pages, "the key-free route reached the WAL"
        assert not genuine.exists(), "it stopped doing its own job"
