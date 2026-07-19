"""legacy_migration.py - one-time unlock-time migrations (E5 + E6).

Runs from routers/vault.py:_bootstrap_unlocked, ALWAYS behind isolation
wrappers there: a failure in any step logs (with traceback, never values)
and is retried on the next unlock - unlock itself can never be blocked by
migration code.

E5 - migrate_legacy_secrets: copy each legacy OS-keyring secret into the
vault DB with commit-then-fresh-readback verification, THEN delete the
keyring entry. Handles the crash-between-copy-and-delete window (retry
delete when values match) and value conflicts (warn, touch nothing).

E6 - migrate_upload_files_to_blobs + reconcile_attachments_without_blobs:
sweep legacy plaintext image files into attachment_blobs one at a time
(commit -> fresh-connection sha256 readback -> only then unlink), then drop
attachment rows that have neither a blob nor a file left. The reconcile
predicate is STATELESS on top of the failed-set exclusion: a row whose file
still exists on disk is never deleted, whatever happened this pass.

Safety switches:
- ELYSIUM_SKIP_LEGACY_MIGRATION=1 disables ONLY the keyring reads/deletes
  (the OS keyring is machine-global; throwaway test/E2E instances must never
  touch the real entries). DB/file work is per-data-dir and stays active.
- app.db.premigrate.bak: encrypted snapshot taken before the first pass that
  could delete rows; an EXISTING backup is never overwritten (it is the
  earliest pre-damage state), and it is discarded only after a fully clean
  pass (no failures anywhere).
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path

import config
import database
import keyring_service
import secrets_service
from database import get_db

logger = logging.getLogger(__name__)

SKIP_ENV = "ELYSIUM_SKIP_LEGACY_MIGRATION"
_LEGACY_SECRET_NAMES = (config.SECRET_API_KEY, config.SECRET_PROXY_URL)
_UPLOAD_NAME_RE = re.compile(r"^[0-9a-f]{64}\.(png|jpg|webp)$")
_PREMIGRATE_BAK = "app.db.premigrate.bak"


def _keyring_disabled() -> bool:
    return os.environ.get(SKIP_ENV) == "1"


# ---------------------------------------------------------------------------
# E5 - secrets
# ---------------------------------------------------------------------------

def migrate_legacy_secrets() -> None:
    """Idempotent keyring->vault move. Per secret name:
    - vault empty + keyring set: copy -> COMMIT -> fresh readback verify ->
      only then delete the keyring entry.
    - vault set + keyring still set: equal values -> retry the delete (an
      earlier delete failed); different values -> warn (no values logged),
      touch NOTHING (re-saving in Settings is the resolution path).
    """
    if _keyring_disabled():
        return
    for name in _LEGACY_SECRET_NAMES:
        legacy = keyring_service.read_legacy(name)
        current = secrets_service.get_secret(name)
        if current is None:
            if legacy is None:
                continue
            secrets_service.set_secret(name, legacy)  # own txn; commits on exit
            readback = secrets_service.get_secret(name)  # FRESH connection
            if readback != legacy:
                logger.error(
                    "legacy-migration: post-commit verify failed for %s; "
                    "keyring entry kept for retry.", name,
                )
                continue
            keyring_service.delete_legacy(name)
            logger.info("legacy-migration: %s moved into the vault.", name)
        elif legacy is not None:
            if legacy == current:
                # Copy landed earlier but the delete failed - finish the job.
                keyring_service.delete_legacy(name)
            else:
                logger.warning(
                    "legacy-migration: %s exists in BOTH the vault and the OS "
                    "keyring with DIFFERENT values; leaving both untouched. "
                    "Re-saving it in Settings resolves the conflict.", name,
                )


# ---------------------------------------------------------------------------
# E6 - upload files -> blobs
# ---------------------------------------------------------------------------

def migrate_upload_files_to_blobs() -> tuple[int, set[str], int]:
    """Sweep legacy plaintext files into attachment_blobs, one at a time.

    Returns (migrated_count, failed_shas, removed_file_count). Per file:
    read -> content-hash check -> row-existence + blob INSERT in ONE txn ->
    COMMIT -> fresh-connection sha256 readback -> only then unlink. Any I/O
    or verify failure puts the sha in failed_shas and PRESERVES both the file
    and its rows for the next unlock. Only regular, exactly-named files are
    touched (no symlinks, no foreign names); *.tmp litter is removed.
    """
    uploads = Path(config.UPLOADS_DIR)  # dynamic read - tests repoint config
    migrated = 0
    removed = 0
    failed: set[str] = set()
    if not uploads.is_dir():
        return (0, failed, 0)

    for entry in sorted(uploads.iterdir()):
        sha = entry.name[:64] if _UPLOAD_NAME_RE.match(entry.name) else None
        try:
            if entry.is_symlink() or not entry.is_file():
                continue  # irregular entry: never touched
            if entry.name.endswith(".tmp"):
                entry.unlink(missing_ok=True)
                removed += 1
                continue
            if sha is None:
                continue  # foreign filename: never touched

            try:
                data = entry.read_bytes()
            except OSError:
                failed.add(sha)
                logger.warning(
                    "uploads-migration: read failed for %s...; file and rows "
                    "kept for retry.", sha[:12],
                )
                continue

            if hashlib.sha256(data).hexdigest() != sha:
                # Content does not match its content-address: corrupt or
                # tampered - controlled delete, nothing written to the DB.
                entry.unlink(missing_ok=True)
                removed += 1
                logger.warning(
                    "uploads-migration: content hash mismatch for %s...; "
                    "file removed.", sha[:12],
                )
                continue

            # Row-existence check INSIDE the same txn as the blob write, so a
            # concurrent chat-delete cannot strand a rowless blob (F7).
            with get_db() as con:
                con.execute("BEGIN IMMEDIATE")
                referenced = con.execute(
                    "SELECT 1 FROM attachments WHERE sha256 = ? LIMIT 1",
                    (sha,),
                ).fetchone() is not None
                if referenced:
                    con.execute(
                        "INSERT OR IGNORE INTO attachment_blobs (sha256, data) "
                        "VALUES (?, ?)",
                        (sha, data),
                    )
            # get_db context exit above == COMMIT.

            if not referenced:
                entry.unlink(missing_ok=True)  # orphan plaintext: policy delete
                removed += 1
                continue

            # Durability proof on a FRESH connection: recompute the full
            # sha256 of what the DB now returns (length alone proves nothing).
            with get_db() as con:
                row = con.execute(
                    "SELECT data FROM attachment_blobs WHERE sha256 = ?",
                    (sha,),
                ).fetchone()
            if row is None or hashlib.sha256(bytes(row["data"])).hexdigest() != sha:
                failed.add(sha)
                with get_db() as con:
                    con.execute(
                        "DELETE FROM attachment_blobs WHERE sha256 = ?", (sha,)
                    )
                logger.error(
                    "uploads-migration: readback verify failed for %s...; "
                    "file kept for retry.", sha[:12],
                )
                continue

            # Only now is the plaintext removable. An unlink failure is fine:
            # the verified blob stays, the next unlock retries the delete
            # (INSERT OR IGNORE no-ops, verify passes, unlink runs again).
            try:
                entry.unlink()
            except OSError:
                logger.warning(
                    "uploads-migration: unlink failed for %s...; will retry "
                    "next unlock.", sha[:12],
                )
            migrated += 1
        except Exception:
            # Watch-point 1: full traceback for programming errors, but never
            # file contents or secret values (nothing is interpolated here).
            if sha is not None:
                failed.add(sha)
            logger.exception(
                "uploads-migration: unexpected error on one file; continuing."
            )

    try:
        if uploads.is_dir() and not any(uploads.iterdir()):
            uploads.rmdir()
    except OSError:
        pass
    if migrated or removed or failed:
        logger.info(
            "uploads-migration: migrated=%d removed=%d failed=%d",
            migrated, removed, len(failed),
        )
    return (migrated, failed, removed)


def reconcile_attachments_without_blobs(failed_shas: set[str]) -> int:
    """Drop attachment rows that are unrecoverable: no blob, not in this
    pass's failed set, and no file left on disk either (stateless third
    layer - a row whose file still exists is NEVER deleted; the next unlock
    migrates the file first). Also sweeps rowless blobs (F8). Returns the
    number of rows removed."""
    uploads = Path(config.UPLOADS_DIR)
    exts = set(config.ALLOWED_IMAGE_MIMES.values())
    with get_db() as con:
        con.execute("BEGIN IMMEDIATE")
        rows = con.execute(
            "SELECT DISTINCT a.sha256 FROM attachments a "
            "WHERE NOT EXISTS (SELECT 1 FROM attachment_blobs b "
            "                  WHERE b.sha256 = a.sha256)"
        ).fetchall()
        doomed: list[str] = []
        for r in rows:
            sha = r["sha256"]
            if sha in failed_shas:
                continue
            if any((uploads / f"{sha}.{ext}").is_file() for ext in exts):
                continue
            doomed.append(sha)
        deleted = 0
        if doomed:
            placeholders = ",".join("?" * len(doomed))
            deleted = con.execute(
                f"DELETE FROM attachments WHERE sha256 IN ({placeholders})",
                doomed,
            ).rowcount
        # F8: rowless blobs (crash/race leftovers) - encrypted, but unbounded
        # invisible space; same clean-state gate as this whole function.
        con.execute(
            "DELETE FROM attachment_blobs WHERE NOT EXISTS ("
            "SELECT 1 FROM attachments a "
            "WHERE a.sha256 = attachment_blobs.sha256)"
        )
    if deleted:
        logger.info(
            "uploads-migration: reconciled %d attachment rows without blobs.",
            deleted,
        )
    return deleted


# ---------------------------------------------------------------------------
# Premigration snapshot (F6 + watch-point 2)
# ---------------------------------------------------------------------------

def premigrate_backup_path() -> Path:
    return Path(config.DB_PATH).parent / _PREMIGRATE_BAK


def uploads_migration_pending() -> bool:
    """True when this unlock could mutate attachment state: candidate files
    on disk, or rows without blobs in the DB."""
    uploads = Path(config.UPLOADS_DIR)
    if uploads.is_dir():
        for entry in uploads.iterdir():
            if _UPLOAD_NAME_RE.match(entry.name) and entry.is_file() \
                    and not entry.is_symlink():
                return True
    with get_db() as con:
        row = con.execute(
            "SELECT 1 FROM attachments a WHERE NOT EXISTS ("
            "SELECT 1 FROM attachment_blobs b WHERE b.sha256 = a.sha256) "
            "LIMIT 1"
        ).fetchone()
    return row is not None


def ensure_premigrate_backup() -> None:
    """Encrypted snapshot before the first mutating pass. An existing backup
    is NEVER overwritten - it is the earliest pre-damage state and therefore
    the most valuable copy."""
    path = premigrate_backup_path()
    if path.exists():
        logger.info("uploads-migration: premigrate backup already present; kept.")
        return
    database.backup_encrypted(str(path))
    logger.info("uploads-migration: premigrate backup written.")


def discard_premigrate_backup() -> None:
    """Remove the snapshot - call ONLY after a fully clean pass (migration
    completed with zero failures and reconcile ran)."""
    try:
        premigrate_backup_path().unlink(missing_ok=True)
    except OSError:
        logger.warning("uploads-migration: premigrate backup delete failed.")
