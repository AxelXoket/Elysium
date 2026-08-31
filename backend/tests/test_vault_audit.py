"""Audit regressions for the vault: what a wrong key opens, and what a
passphrase rotation actually revokes.

Both defects are silent and unrecoverable from the UI:
  * a 0-byte app.db validated EVERY key, so DB-validated recovery promoted a
    typo and destroyed the real verifier - the user's own passphrase then
    returned wrong_passphrase forever;
  * a passphrase change re-keyed only the live DB while full encrypted
    snapshots stayed under the old key, with the superseded salt shelved right
    beside them.
"""

import pytest

import crypto
import database


# ── check_key must not validate an empty file ──────────────────────────────


def test_a_zero_byte_db_validates_no_key(tmp_path):
    empty = tmp_path / "app.db"
    empty.touch()
    assert empty.stat().st_size == 0

    for passphrase in ("correct-passphrase", "a typo", "anything at all"):
        key = crypto.derive_key(passphrase, crypto.new_salt())
        assert database.check_key(key, str(empty)) is False


def test_a_zero_byte_db_is_not_reported_as_plaintext(tmp_path):
    empty = tmp_path / "app.db"
    empty.touch()
    assert database.is_plaintext_db(str(empty)) is False


def test_a_missing_db_still_validates_no_key(tmp_path):
    assert database.check_key(b"\x00" * 32, str(tmp_path / "nope.db")) is False


def test_recovery_refuses_a_wrong_passphrase_on_an_empty_db(tmp_path):
    """The whole failure, end to end: the verifier is intact, the user typos
    once, and DB-validated recovery must NOT accept it and rewrite verifier.bin.
    """
    empty_db = tmp_path / "app.db"
    empty_db.touch()

    vault = crypto.KeyVault(tmp_path)
    real_key = vault.initialize("correct-passphrase")
    verifier_before = vault.verifier_path.read_bytes()

    assert vault.unlock("a typo") is None
    recovered = vault.recover_with_db(
        "a typo", lambda key: database.check_key(key, str(empty_db)),
    )
    assert recovered is None, "an empty DB must not validate a typo"
    assert vault.verifier_path.read_bytes() == verifier_before
    assert vault.unlock("correct-passphrase") == real_key


# Removed 2026-08-10: test_recovery_still_works_against_a_real_db.
#
# It read as the success-path control for the test above, but it was
# test_vault.py::test_recover_with_db_uses_db_as_authority with a dead fixture
# bolted on. Its comment claimed "the point here is that a NON-empty file
# reaches the check" while its validator was `lambda k: k == key`, which never
# opens the file at all - so the DB it built was never consulted and the
# sentence was not true of the code beneath it.
#
# The real "a non-empty file reaches database.check_key" case is covered, just
# not here: test_vault.py::test_recover_through_endpoint_with_corrupt_verifier
# and ::test_fb5a_change_recovers_from_corrupt_verifier both write a character
# row, corrupt the verifier, and go through the endpoint, which wires the
# UNMOCKED database.check_key into recover_with_db against a real encrypted,
# non-empty database. (An earlier note here claimed that was a gap. It was not;
# corrected 2026-08-10 after the endpoint tests were read and run.)
# Same reasoning as the removal below: a second test proving a subset is not a
# second opinion.


# ── a rotation must revoke the old passphrase everywhere ───────────────────


def test_changing_the_passphrase_removes_the_shelved_old_identity(tmp_path):
    """The shelved salt is a working recipe for the OLD key. Left on disk next
    to a snapshot still under that key, the rotation revokes nothing."""
    vault = crypto.KeyVault(tmp_path)
    vault.initialize("old-passphrase")

    new_key = vault.change_passphrase(
        "new-passphrase", rekey_fn=lambda k: None, verify_fn=lambda k: True,
    )

    shelved = list(tmp_path.glob("salt.bin.bak-*")) + list(
        tmp_path.glob("verifier.bin.bak-*")
    )
    assert shelved == [], f"superseded identity left on disk: {shelved}"
    assert vault.unlock("new-passphrase") == new_key
    assert vault.unlock("old-passphrase") is None


#: The control for a failed rekey lives in test_vault.py, not here.
#:
#: `test_change_passphrase_aborts_when_rekey_did_not_take` builds the same
#: scenario and asserts strictly more: it matches the exception message rather
#: than swallowing any RuntimeError, it checks the salt bytes are unchanged,
#: it checks the NEW passphrase does not open the vault, and it globs `*.new`
#: rather than only `salt.bin.new`. A second test that proves a subset of that
#: is not a second opinion, it is the same opinion said twice, and it costs a
#: reader the time to work out which of the two is authoritative.
#:
#: Removed 2026-08-10 with nothing moved: the design story it rested on is
#: written where it belongs, in `crypto.change_passphrase`.


def test_rekey_file_moves_a_snapshot_to_the_new_key(tmp_path):
    """The primitive the route uses on app.db.premigrate.bak and friends."""
    old_key = crypto.derive_key("old", crypto.new_salt())
    new_key = crypto.derive_key("new", crypto.new_salt())

    snapshot = tmp_path / "app.db.premigrate.bak"
    con = database.sqlite3.connect(str(snapshot))
    try:
        database._key_pragma(con, old_key)
        con.execute("CREATE TABLE secrets (v)")
        con.execute("INSERT INTO secrets VALUES ('api key')")
        con.commit()
    finally:
        con.close()

    assert database.check_key(old_key, str(snapshot)) is True

    database.rekey_file(str(snapshot), new_key, old_key)

    assert database.check_key(new_key, str(snapshot)) is True
    assert database.check_key(old_key, str(snapshot)) is False


def test_rekey_file_raises_when_the_pragma_silently_no_ops(tmp_path,
                                                           monkeypatch):
    """The case that had no test: PRAGMA rekey returning without doing it.

    Under a concurrent write lock SQLCipher can accept the pragma and change
    nothing, with no error. The sibling function two doors down has said so
    in its docstring since it was written, and the main database enforces it
    - but the sidecar path did not, and the sidecars are full copies of the
    vault. So a passphrase change reported success while a complete copy of
    everything was still open to the OLD passphrase.

    The no-op is simulated at the one place it can happen, the rekey branch
    of _key_pragma. Everything else runs for real: the file, the old key, the
    fresh connection that reads it back.
    """
    old_key = crypto.derive_key("old", crypto.new_salt())
    new_key = crypto.derive_key("new", crypto.new_salt())

    snapshot = tmp_path / "app.db.premigrate.bak"
    con = database.sqlite3.connect(str(snapshot))
    try:
        database._key_pragma(con, old_key)
        con.execute("CREATE TABLE secrets (v)")
        con.commit()
    finally:
        con.close()

    real = database._key_pragma

    def deaf(connection, key, *, rekey=False):
        if rekey:
            return                      # accepted, and nothing happened
        real(connection, key)

    monkeypatch.setattr(database, "_key_pragma", deaf)

    with pytest.raises(RuntimeError):
        database.rekey_file(str(snapshot), new_key, old_key)

    # And the point of the raise: the file really is still on the old key, so
    # the caller that catches this is telling the user the truth.
    monkeypatch.undo()
    assert database.check_key(old_key, str(snapshot)) is True
    assert database.check_key(new_key, str(snapshot)) is False
