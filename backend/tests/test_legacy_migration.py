"""legacy_migration tests (E5 keyring->vault + E6 files->blobs + reconcile).

The autouse _no_real_keyring fixture (conftest) stubs the legacy helpers, so
NOTHING here can touch the developer's real Credential Manager; tests that
exercise the migration monkeypatch their own fakes over the stubs.
"""

import hashlib
import io
import os
from pathlib import Path

import pytest
from PIL import Image

import config
import database
import legacy_migration
import secrets_service


def make_png_bytes(color=(10, 20, 30)) -> bytes:
    img = Image.new("RGB", (24, 24), color)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def _plant_legacy_file(data: bytes, with_row: bool = True) -> str:
    """Write a correctly-named legacy plaintext file (+ optional staged row).
    Returns the sha."""
    sha = hashlib.sha256(data).hexdigest()
    uploads = Path(config.UPLOADS_DIR)
    uploads.mkdir(parents=True, exist_ok=True)
    (uploads / f"{sha}.png").write_bytes(data)
    if with_row:
        with database.get_db() as con:
            con.execute(
                "INSERT INTO attachments (message_id, sha256, mime, width, height, byte_size) "
                "VALUES (NULL, ?, 'image/png', 24, 24, ?)",
                (sha, len(data)),
            )
    return sha


def _blob_exists(sha: str) -> bool:
    with database.get_db() as con:
        return con.execute(
            "SELECT 1 FROM attachment_blobs WHERE sha256 = ?", (sha,)
        ).fetchone() is not None


def _rows_for(sha: str) -> int:
    with database.get_db() as con:
        return con.execute(
            "SELECT COUNT(*) AS n FROM attachments WHERE sha256 = ?", (sha,)
        ).fetchone()["n"]


# ---------------------------------------------------------------------------
# E6 - file -> blob
# ---------------------------------------------------------------------------

def test_migration_happy_path_moves_file_into_blob(client):
    data = make_png_bytes((1, 2, 3))
    sha = _plant_legacy_file(data)

    migrated, failed, removed = legacy_migration.migrate_upload_files_to_blobs()
    assert migrated == 1 and not failed
    assert _blob_exists(sha)
    assert not (Path(config.UPLOADS_DIR) / f"{sha}.png").exists()
    # Second pass: clean no-op (idempotent).
    migrated2, failed2, _ = legacy_migration.migrate_upload_files_to_blobs()
    assert migrated2 == 0 and not failed2


def test_migration_hash_mismatch_deletes_file_writes_nothing(client):
    uploads = Path(config.UPLOADS_DIR)
    uploads.mkdir(parents=True, exist_ok=True)
    fake_sha = "a" * 64
    (uploads / f"{fake_sha}.png").write_bytes(b"not the content of that hash")

    _, failed, removed = legacy_migration.migrate_upload_files_to_blobs()
    assert removed == 1 and not failed
    assert not (uploads / f"{fake_sha}.png").exists()
    assert not _blob_exists(fake_sha)


def test_migration_unreferenced_file_is_policy_deleted(client):
    data = make_png_bytes((9, 9, 9))
    sha = _plant_legacy_file(data, with_row=False)

    migrated, failed, removed = legacy_migration.migrate_upload_files_to_blobs()
    assert removed == 1 and migrated == 0 and not failed
    assert not _blob_exists(sha)  # nothing referenced it - no blob written


def test_migration_never_touches_foreign_or_tmp_names(client):
    uploads = Path(config.UPLOADS_DIR)
    uploads.mkdir(parents=True, exist_ok=True)
    (uploads / "notes.txt").write_bytes(b"user file")
    (uploads / ("b" * 64 + ".png.123.tmp")).write_bytes(b"crash litter")

    _, failed, removed = legacy_migration.migrate_upload_files_to_blobs()
    assert (uploads / "notes.txt").exists()      # foreign name: untouched
    assert removed == 1                          # tmp litter: removed
    assert not failed


def test_transient_read_error_preserves_row_file_and_blocks_reconcile(
    client, monkeypatch,
):
    data = make_png_bytes((4, 4, 4))
    sha = _plant_legacy_file(data)
    path = Path(config.UPLOADS_DIR) / f"{sha}.png"

    real_read_bytes = Path.read_bytes

    def flaky_read(self):
        if self.name.startswith(sha):
            raise OSError("transient AV lock")
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", flaky_read)
    migrated, failed, _ = legacy_migration.migrate_upload_files_to_blobs()
    assert sha in failed and migrated == 0
    assert path.exists() and _rows_for(sha) == 1

    # Reconcile must spare the failed sha even though it has no blob.
    deleted = legacy_migration.reconcile_attachments_without_blobs(failed)
    assert deleted == 0 and _rows_for(sha) == 1

    # Next unlock (error gone): the same file migrates cleanly.
    monkeypatch.setattr(Path, "read_bytes", real_read_bytes)
    migrated, failed, _ = legacy_migration.migrate_upload_files_to_blobs()
    assert migrated == 1 and not failed
    assert _blob_exists(sha) and not path.exists()


def test_unlink_failure_keeps_blob_and_retries_next_pass(client, monkeypatch):
    data = make_png_bytes((6, 6, 6))
    sha = _plant_legacy_file(data)
    path = Path(config.UPLOADS_DIR) / f"{sha}.png"

    real_unlink = Path.unlink

    def stubborn_unlink(self, missing_ok=False):
        if self.name.startswith(sha):
            raise OSError("file locked by scanner")
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", stubborn_unlink)
    migrated, failed, _ = legacy_migration.migrate_upload_files_to_blobs()
    assert migrated == 1 and not failed  # blob verified; only unlink failed
    assert _blob_exists(sha) and path.exists()

    monkeypatch.setattr(Path, "unlink", real_unlink)
    legacy_migration.migrate_upload_files_to_blobs()
    assert not path.exists()  # retried and completed


def test_reconcile_drops_only_truly_unrecoverable_rows(client):
    # Row without blob and without file: unrecoverable -> dropped.
    with database.get_db() as con:
        con.execute(
            "INSERT INTO attachments (message_id, sha256, mime, width, height, byte_size) "
            "VALUES (NULL, ?, 'image/png', 1, 1, 1)",
            ("c" * 64,),
        )
    # Row without blob but WITH a file on disk: must survive (stateless rule).
    data = make_png_bytes((5, 5, 5))
    sha_on_disk = _plant_legacy_file(data)

    deleted = legacy_migration.reconcile_attachments_without_blobs(set())
    assert deleted == 1
    assert _rows_for("c" * 64) == 0
    assert _rows_for(sha_on_disk) == 1


def test_save_transaction_atomicity_blob_rolls_back(client):
    """A failure after the blob INSERT rolls the blob back too (user test #6)."""
    sha = "d" * 64
    with pytest.raises(RuntimeError):
        with database.get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                "INSERT OR IGNORE INTO attachment_blobs (sha256, data) VALUES (?, ?)",
                (sha, b"payload"),
            )
            raise RuntimeError("simulated attachments-insert failure")
    assert not _blob_exists(sha)


# ---------------------------------------------------------------------------
# Premigrate snapshot lifecycle (watch-point 2)
# ---------------------------------------------------------------------------

def test_premigrate_backup_created_kept_and_never_overwritten(client):
    data = make_png_bytes((8, 8, 8))
    _plant_legacy_file(data)
    assert legacy_migration.uploads_migration_pending()

    legacy_migration.ensure_premigrate_backup()
    bak = legacy_migration.premigrate_backup_path()
    assert bak.exists()
    first_bytes = bak.read_bytes()

    # A later call must NOT overwrite the existing snapshot.
    with database.get_db() as con:
        con.execute(
            "INSERT INTO attachments (message_id, sha256, mime, width, height, byte_size) "
            "VALUES (NULL, ?, 'image/png', 1, 1, 1)",
            ("e" * 64,),
        )
    legacy_migration.ensure_premigrate_backup()
    assert bak.read_bytes() == first_bytes

    legacy_migration.discard_premigrate_backup()
    assert not bak.exists()


# ---------------------------------------------------------------------------
# E5 - keyring -> vault
# ---------------------------------------------------------------------------

def _fake_keyring(monkeypatch, store: dict, fail_delete: set | None = None):
    import keyring_service

    def read(name):
        return store.get(name)

    def delete(name):
        if fail_delete and name in fail_delete:
            return False
        store.pop(name, None)
        return True

    monkeypatch.setattr(keyring_service, "read_legacy", read)
    monkeypatch.setattr(keyring_service, "delete_legacy", delete)
    return store


def test_secret_migration_copies_verifies_then_deletes(client, monkeypatch):
    store = _fake_keyring(monkeypatch, {config.SECRET_API_KEY: "sk-legacy-123"})
    # conftest seeds the API key in the DB - clear it to simulate first run.
    secrets_service.delete_secret(config.SECRET_API_KEY)

    legacy_migration.migrate_legacy_secrets()
    assert secrets_service.get_secret(config.SECRET_API_KEY) == "sk-legacy-123"
    assert config.SECRET_API_KEY not in store  # keyring entry deleted

    legacy_migration.migrate_legacy_secrets()  # idempotent no-op
    assert secrets_service.get_secret(config.SECRET_API_KEY) == "sk-legacy-123"


def test_secret_migration_retries_failed_delete(client, monkeypatch):
    """DB already has the value; keyring copy lingers from a failed delete -
    the next pass finishes the delete (user test #4)."""
    store = _fake_keyring(
        monkeypatch, {config.SECRET_API_KEY: "sk-test-key"},
    )
    # conftest already seeded DB with the SAME value ("sk-test-key").
    legacy_migration.migrate_legacy_secrets()
    assert config.SECRET_API_KEY not in store
    assert secrets_service.get_secret(config.SECRET_API_KEY) == "sk-test-key"


def test_secret_conflict_touches_nothing(client, monkeypatch):
    """Different values in DB and keyring: warn, never auto-delete or
    overwrite (user test #5)."""
    store = _fake_keyring(monkeypatch, {config.SECRET_API_KEY: "sk-DIFFERENT"})
    legacy_migration.migrate_legacy_secrets()
    assert store[config.SECRET_API_KEY] == "sk-DIFFERENT"  # untouched
    assert secrets_service.get_secret(config.SECRET_API_KEY) == "sk-test-key"


def test_kill_switch_disables_keyring_access(client, monkeypatch):
    calls = {"read": 0}
    import keyring_service

    def counting_read(name):
        calls["read"] += 1
        return None

    monkeypatch.setattr(keyring_service, "read_legacy", counting_read)
    monkeypatch.setenv(legacy_migration.SKIP_ENV, "1")
    legacy_migration.migrate_legacy_secrets()
    assert calls["read"] == 0  # switch on: keyring never touched


# ---------------------------------------------------------------------------
# Bootstrap isolation (unlock never fails because of migration code)
# ---------------------------------------------------------------------------

def test_bootstrap_survives_migration_crash_and_skips_reconcile(
    client, monkeypatch,
):
    import routers.vault as vault_router

    data = make_png_bytes((7, 7, 7))
    sha = _plant_legacy_file(data)

    def exploding_migrate():
        raise RuntimeError("simulated migration bug")

    reconcile_calls = {"n": 0}

    def counting_reconcile(failed):
        reconcile_calls["n"] += 1
        return 0

    monkeypatch.setattr(
        legacy_migration, "migrate_upload_files_to_blobs", exploding_migrate
    )
    monkeypatch.setattr(
        legacy_migration,
        "reconcile_attachments_without_blobs",
        counting_reconcile,
    )
    # Must NOT raise: unlock can never be blocked by migration code.
    vault_router._bootstrap_unlocked()
    assert reconcile_calls["n"] == 0  # crash skipped reconcile entirely
    assert _rows_for(sha) == 1  # nothing was destroyed
    assert (Path(config.UPLOADS_DIR) / f"{sha}.png").exists()
