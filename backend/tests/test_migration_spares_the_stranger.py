"""Migration must not delete the file the same unlock just promised to keep.

`_bootstrap_unlocked` runs two steps back to back. Step one,
adopt_orphaned_enc_tmp, looks at app.db.enc-tmp and, when it declines, logs
that the file "will not be removed automatically. Inspect both before deleting
either." Step two, migrate_plaintext_to_encrypted, opened with:

    if enc_tmp.exists():
        enc_tmp.unlink()

The promise and its breach were in the same request. And the unlink asked
nothing first: discard_orphaned_enc_tmp REFUSES a copy that does not open under
the current key, on the grounds that an encrypted file nobody here can read is
not junk but data whose passphrase we do not have - the exact file this line
removed without looking, with a plain unlink rather than a shred.

The fix is that migration moves it ASIDE instead of adjudicating it. Two other
candidates were tried and both are traps:

  * Refusing to migrate. The refusal propagates out of _bootstrap_unlocked,
    /vault/unlock turns it into a 500 and re-locks, and the only route that can
    remove the blocking file needs an unlocked vault.
  * Leaving it where it is and exporting to a different scratch name. That name
    then falls outside every net built around this family - crash recovery
    watches app.db.enc-tmp, and _rekey_sidecars re-keys what it can find, so a
    crash would hand recovery the stranger while a complete vault stayed
    readable under the passphrase the user was rotating away from.

migrate_plaintext_to_encrypted had no direct test of any kind before this file.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlcipher3 import dbapi2 as sqlite3

import config
import database

KEY = bytes(range(32))
OTHER_KEY = bytes(range(1, 33))


@pytest.fixture()
def paths(tmp_path, monkeypatch):
    live = tmp_path / "app.db"
    monkeypatch.setattr(database, "DB_PATH", str(live))
    monkeypatch.setattr(config, "DB_PATH", str(live), raising=False)
    return live, live.with_name("app.db.enc-tmp")


def _make_plaintext_db(path: Path, marker: str) -> None:
    """A pre-vault app.db: an ordinary unkeyed SQLite file with content."""
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE probe (v TEXT)")
    con.execute("INSERT INTO probe VALUES (?)", (marker,))
    con.commit()
    con.close()


def _make_encrypted_db(path: Path, key: bytes, marker: str) -> None:
    con = sqlite3.connect(str(path))
    database._key_pragma(con, key)
    con.execute("CREATE TABLE probe (v TEXT)")
    con.execute("INSERT INTO probe VALUES (?)", (marker,))
    con.commit()
    con.close()


def _read_marker(path: Path, key: bytes | None) -> str | None:
    con = sqlite3.connect(str(path))
    try:
        if key is not None:
            database._key_pragma(con, key)
        return con.execute("SELECT v FROM probe").fetchone()[0]
    except sqlite3.DatabaseError:
        return None
    finally:
        con.close()


# ---------------------------------------------------------------------------
# The stranded copy survives
# ---------------------------------------------------------------------------

def _moved_aside(live: Path) -> list[Path]:
    return sorted(live.parent.glob("app.db.enc-tmp.orphan.bak-*"))


def test_a_copy_under_a_different_passphrase_survives_migration(paths):
    """The worst case, and the one the old unlink handled worst: an encrypted
    file this vault cannot read may be the only copy of something."""
    live, enc_tmp = paths
    _make_plaintext_db(live, "pre-vault words")
    _make_encrypted_db(enc_tmp, OTHER_KEY, "someone else's vault")
    before = enc_tmp.read_bytes()

    database.migrate_plaintext_to_encrypted(KEY)

    aside = _moved_aside(live)
    assert len(aside) == 1, "a copy nobody here can read was deleted"
    assert aside[0].read_bytes() == before
    assert _read_marker(aside[0], OTHER_KEY) == "someone else's vault"


def test_a_readable_stranded_copy_survives_too(paths):
    """Nothing is deleted either way. Whether the copy opens under this key
    decides what the USER may do with it, through
    /vault/discard-orphaned-copy - it is not a licence for migration to act."""
    live, enc_tmp = paths
    _make_plaintext_db(live, "pre-vault words")
    _make_encrypted_db(enc_tmp, KEY, "an earlier export")

    database.migrate_plaintext_to_encrypted(KEY)

    aside = _moved_aside(live)
    assert len(aside) == 1
    assert _read_marker(aside[0], KEY) == "an earlier export"


def test_the_moved_copy_stays_visible_in_the_vault_status(paths):
    """Moving it must not move it out of sight. A copy of the vault that the
    status route cannot see and the discard route cannot remove is the exact
    shape of bug those fields were added to end."""
    live, enc_tmp = paths
    _make_plaintext_db(live, "pre-vault words")
    _make_encrypted_db(enc_tmp, OTHER_KEY, "someone else's vault")

    database.migrate_plaintext_to_encrypted(KEY)

    assert database.orphaned_enc_tmp_present() is True
    assert _moved_aside(live) == database.orphaned_enc_tmp_paths()


def test_the_moved_copy_is_still_reachable_by_the_discard_route(paths):
    """The other half of visible: reported AND removable, by the one route
    that is allowed to remove it."""
    live, enc_tmp = paths
    _make_plaintext_db(live, "pre-vault words")
    _make_encrypted_db(enc_tmp, KEY, "an earlier export")
    database.migrate_plaintext_to_encrypted(KEY)

    assert database.orphaned_enc_tmp_opens_with(KEY) is True
    removed, reason = database.discard_orphaned_enc_tmp(KEY)

    assert (removed, reason) == (True, "")
    assert _moved_aside(live) == []


def test_a_moved_copy_under_another_key_is_still_refused(paths):
    """And the safety property survives the move: a copy nobody here can read
    is not junk to be tidied away."""
    live, enc_tmp = paths
    _make_plaintext_db(live, "pre-vault words")
    _make_encrypted_db(enc_tmp, OTHER_KEY, "someone else's vault")
    database.migrate_plaintext_to_encrypted(KEY)

    assert database.orphaned_enc_tmp_opens_with(KEY) is False
    removed, reason = database.discard_orphaned_enc_tmp(KEY)

    assert (removed, reason) == (False, "different_key")
    assert len(_moved_aside(live)) == 1


def test_the_canonical_recovery_name_is_left_free(paths):
    """Crash recovery watches app.db.enc-tmp. If the stranger kept that name
    while the fresh export went somewhere else, a crash in the two-rename
    window below would hand recovery the wrong database."""
    live, enc_tmp = paths
    _make_plaintext_db(live, "pre-vault words")
    _make_encrypted_db(enc_tmp, OTHER_KEY, "someone else's vault")

    database.migrate_plaintext_to_encrypted(KEY)

    assert not enc_tmp.exists()


# ---------------------------------------------------------------------------
# And the migration still happens
# ---------------------------------------------------------------------------

def test_migration_still_completes_around_the_stranded_copy(paths):
    live, enc_tmp = paths
    _make_plaintext_db(live, "pre-vault words")
    _make_encrypted_db(enc_tmp, OTHER_KEY, "someone else's vault")

    backup = database.migrate_plaintext_to_encrypted(KEY)

    assert _read_marker(live, KEY) == "pre-vault words", "the vault is not keyed"
    assert _read_marker(live, None) is None, "the live file is still readable"
    assert Path(backup).exists(), "the pre-vault copy was not kept"


def test_a_rotation_would_still_find_the_moved_copy(paths):
    """The net it must not fall out of. _rekey_sidecars re-encrypts every
    copy beside the vault under the new passphrase, and a name it does not
    know is a complete vault that stays readable with the passphrase the user
    was rotating AWAY from - the precise failure that function exists to
    prevent, and the one a first draft of this fix walked straight into by
    inventing a name outside both of its globs.
    """
    live, enc_tmp = paths
    _make_plaintext_db(live, "pre-vault words")
    _make_encrypted_db(enc_tmp, KEY, "an earlier export")
    database.migrate_plaintext_to_encrypted(KEY)

    aside = _moved_aside(live)
    assert len(aside) == 1
    # Both nets, spelled out: the ".bak" glob _rekey_sidecars walks, and the
    # orphan family it now also asks database for.
    assert aside[0] in live.parent.glob(live.name + "*.bak*")
    assert aside[0] in database.orphaned_enc_tmp_paths()


def test_an_ordinary_migration_leaves_no_scratch_file(paths):
    """The uncontested path, unchanged."""
    live, enc_tmp = paths
    _make_plaintext_db(live, "pre-vault words")

    database.migrate_plaintext_to_encrypted(KEY)

    assert _read_marker(live, KEY) == "pre-vault words"
    assert not enc_tmp.exists()


# ---------------------------------------------------------------------------
# Its OWN scratch file is destroyed, not just unlinked
# ---------------------------------------------------------------------------

def test_a_failed_export_is_overwritten_before_it_is_removed(paths, monkeypatch):
    """The file migration does own is a partial encrypted copy of the whole
    database. `unlink` hands those blocks back with the contents intact.

    os.unlink is stubbed so the corpse can be read back - "is the file gone" is
    the question that let eight of these through before.
    """
    live, enc_tmp = paths
    _make_plaintext_db(live, "pre-vault words")

    # Fail verification so the failure path runs, without corrupting anything.
    monkeypatch.setattr(database, "check_key", lambda *a, **kw: False)
    monkeypatch.setattr(os, "unlink", lambda *a, **kw: None)

    with pytest.raises(RuntimeError, match="migration_verify_failed"):
        database.migrate_plaintext_to_encrypted(KEY)

    survivor = enc_tmp.read_bytes()
    assert survivor, "the stub should have kept the file for inspection"
    assert b"pre-vault words" not in survivor
    assert _read_marker(enc_tmp, KEY) is None, "it is still a readable database"


def test_a_failed_export_never_touches_the_plaintext_source(paths, monkeypatch):
    """Verification failing must cost the copy, never the original."""
    live, _ = paths
    _make_plaintext_db(live, "pre-vault words")

    monkeypatch.setattr(database, "check_key", lambda *a, **kw: False)

    with pytest.raises(RuntimeError, match="migration_verify_failed"):
        database.migrate_plaintext_to_encrypted(KEY)

    assert _read_marker(live, None) == "pre-vault words"


# ---------------------------------------------------------------------------
# The log line stops claiming more than it measured
# ---------------------------------------------------------------------------

def test_the_decline_does_not_assert_what_it_never_opened(paths, caplog):
    """adopt_orphaned_enc_tmp declines on two file sizes alone, then said "It
    is a full copy of the vault". A still-plaintext app.db awaiting migration
    reaches that branch too, where the stranded file is far more likely to be a
    previous migration's own scratch copy."""
    live, enc_tmp = paths
    _make_plaintext_db(live, "pre-vault words")
    _make_encrypted_db(enc_tmp, KEY, "an earlier export")

    assert database.adopt_orphaned_enc_tmp(KEY) is False

    said = " ".join(r.getMessage() for r in caplog.records)
    assert "NOT adopted" in said, "the stranded file must still be reported"
    assert "full copy of the vault" not in said
