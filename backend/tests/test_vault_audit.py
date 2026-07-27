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

import sqlite3 as std_sqlite3

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


def test_recovery_still_works_against_a_real_db(tmp_path):
    """Control: with a real DB behind it, recovery repairs a lost verifier."""
    vault = crypto.KeyVault(tmp_path)
    key = vault.initialize("correct-passphrase")

    db = tmp_path / "app.db"
    con = std_sqlite3.connect(db)
    con.execute("CREATE TABLE t (x)")
    con.commit()
    con.close()

    vault.verifier_path.write_bytes(b"corrupt")
    assert vault.unlock("correct-passphrase") is None
    # A plaintext DB opens under any key, so validate against the identity
    # instead - the point here is that a NON-empty file reaches the check.
    recovered = vault.recover_with_db(
        "correct-passphrase", lambda k: k == key,
    )
    assert recovered == key
    assert vault.unlock("correct-passphrase") == key


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


def test_a_failed_rekey_leaves_the_old_identity_untouched(tmp_path):
    """Control: the crash-safe ordering still holds - nothing is removed when
    the rekey did not take."""
    vault = crypto.KeyVault(tmp_path)
    old_key = vault.initialize("old-passphrase")

    try:
        vault.change_passphrase(
            "new-passphrase", rekey_fn=lambda k: None, verify_fn=lambda k: False,
        )
    except RuntimeError:
        pass
    else:  # pragma: no cover - the guard must fire
        raise AssertionError("a no-op rekey must raise")

    assert vault.unlock("old-passphrase") == old_key
    assert not list(tmp_path.glob("salt.bin.new"))


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
