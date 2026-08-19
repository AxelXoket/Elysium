"""/vault/status's `premigrate_backup` field and POST /vault/discard-
premigrate-backup - the two things closing the last unreported, unremovable
vault remnant.

app.db.premigrate.bak is written by legacy_migration.ensure_premigrate_backup
before the first uploads-migration pass that could delete a row, and removed
automatically only after a pass comes back with zero failures. Until now
nothing else reported it and nothing else removed it, so an unlucky machine
that never gets a clean pass carries a full encrypted copy of every chat,
persona, secret and image indefinitely - and a message deleted after the
backup was written still lives inside it, which is what keeps "delete" from
being complete while the file is there.

Same shape as discard-orphaned-copy (requires an unlocked vault, refuses a
copy that does not open under the current key), tested the same way: real
KeyVault, real encrypted files, nothing mocked.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import config
import crypto
import database
import legacy_migration
import vault_state

PASSPHRASE = "correct horse battery staple premigrate"


def _real_vault(tmp_path: Path, monkeypatch) -> tuple[crypto.KeyVault, bytes]:
    db_path = tmp_path / "app.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(database, "DB_PATH", str(db_path))
    vault = crypto.KeyVault(tmp_path)
    key = vault.initialize(PASSPHRASE)
    vault_state.set_key(key)
    try:
        database.init_db()
    finally:
        vault_state.clear_key()
    return vault, key


def _write_encrypted_file(path: Path, key: bytes) -> None:
    """A minimal, real SQLCipher file under an arbitrary key - stands in for
    a premigrate snapshot from an era whose passphrase this vault does not
    hold."""
    from sqlcipher3 import dbapi2 as sqlite3

    con = sqlite3.connect(str(path))
    try:
        database._key_pragma(con, key)
        con.execute("CREATE TABLE t (v)")
        con.commit()
    finally:
        con.close()


class TestStatusReportsPresence:
    def test_absent_by_default(self, client) -> None:
        body = client.get("/api/v1/vault/status").json()
        assert body["premigrate_backup"] is False
        assert body["premigrate_backup_readable"] is None

    def test_present_but_unreadable_while_locked(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        vault, key = _real_vault(tmp_path, monkeypatch)
        database.backup_encrypted(
            str(legacy_migration.premigrate_backup_path()), key=key)
        vault_state.clear_key()

        body = client.get("/api/v1/vault/status").json()

        assert body["premigrate_backup"] is True
        # null, not True or False: the question needs the key to answer.
        assert body["premigrate_backup_readable"] is None

    def test_readable_once_unlocked_with_a_matching_key(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        vault, key = _real_vault(tmp_path, monkeypatch)
        database.backup_encrypted(
            str(legacy_migration.premigrate_backup_path()), key=key)
        # Direct state, not through /vault/unlock: the real unlock route
        # runs the uploads-migration bootstrap, which discards a premigrate
        # backup automatically once a pass comes back clean - exactly the
        # thing this file is testing on its own terms, so going through the
        # HTTP route here would erase the fixture before the test runs.
        vault_state.set_key(key)

        body = client.get("/api/v1/vault/status").json()

        assert body["premigrate_backup"] is True
        assert body["premigrate_backup_readable"] is True

    def test_unreadable_under_a_different_key_even_while_unlocked(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The discriminating half: readable must not default to True just
        because the vault is open."""
        vault, key = _real_vault(tmp_path, monkeypatch)
        other_key = crypto.derive_key(
            "a different era entirely", crypto.new_salt())
        _write_encrypted_file(
            legacy_migration.premigrate_backup_path(), other_key)
        # Direct state, not through /vault/unlock: the real unlock route
        # runs the uploads-migration bootstrap, which discards a premigrate
        # backup automatically once a pass comes back clean - exactly the
        # thing this file is testing on its own terms, so going through the
        # HTTP route here would erase the fixture before the test runs.
        vault_state.set_key(key)

        body = client.get("/api/v1/vault/status").json()

        assert body["premigrate_backup"] is True
        assert body["premigrate_backup_readable"] is False


class TestDiscardRoute:
    def test_refuses_while_locked(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        vault, key = _real_vault(tmp_path, monkeypatch)
        path = legacy_migration.premigrate_backup_path()
        database.backup_encrypted(str(path), key=key)
        vault_state.clear_key()

        response = client.post("/api/v1/vault/discard-premigrate-backup")

        assert response.status_code == 423
        assert response.json()["detail"] == "vault_locked"
        assert path.exists()

    def test_removes_a_snapshot_that_opens_with_the_current_key(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        vault, key = _real_vault(tmp_path, monkeypatch)
        path = legacy_migration.premigrate_backup_path()
        database.backup_encrypted(str(path), key=key)
        # Direct state, not through /vault/unlock: the real unlock route
        # runs the uploads-migration bootstrap, which discards a premigrate
        # backup automatically once a pass comes back clean - exactly the
        # thing this file is testing on its own terms, so going through the
        # HTTP route here would erase the fixture before the test runs.
        vault_state.set_key(key)

        response = client.post("/api/v1/vault/discard-premigrate-backup")

        assert response.status_code == 200, response.text
        assert response.json() == {"removed": True, "reason": ""}
        assert not path.exists()
        assert client.get(
            "/api/v1/vault/status").json()["premigrate_backup"] is False

    def test_refuses_a_snapshot_under_a_different_key(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        vault, key = _real_vault(tmp_path, monkeypatch)
        path = legacy_migration.premigrate_backup_path()
        other_key = crypto.derive_key(
            "a different era entirely", crypto.new_salt())
        _write_encrypted_file(path, other_key)
        # Direct state, not through /vault/unlock: the real unlock route
        # runs the uploads-migration bootstrap, which discards a premigrate
        # backup automatically once a pass comes back clean - exactly the
        # thing this file is testing on its own terms, so going through the
        # HTTP route here would erase the fixture before the test runs.
        vault_state.set_key(key)

        response = client.post("/api/v1/vault/discard-premigrate-backup")

        assert response.status_code == 200, response.text
        assert response.json() == {"removed": False, "reason": "different_key"}
        assert path.exists(), "a snapshot nobody can prove is redundant was deleted"

    def test_reports_not_present(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        vault, key = _real_vault(tmp_path, monkeypatch)
        # Direct state, not through /vault/unlock: the real unlock route
        # runs the uploads-migration bootstrap, which discards a premigrate
        # backup automatically once a pass comes back clean - exactly the
        # thing this file is testing on its own terms, so going through the
        # HTTP route here would erase the fixture before the test runs.
        vault_state.set_key(key)

        response = client.post("/api/v1/vault/discard-premigrate-backup")

        assert response.status_code == 200
        assert response.json() == {"removed": False, "reason": "not_present"}
