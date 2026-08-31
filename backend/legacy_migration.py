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
  ENFORCED IN keyring_service, not here: this module is one of several callers
  and the guard has to cover the ones that never think about migration - the
  Settings routes that save or clear a key reach the same machine-wide store.
  The early return below is only a shortcut, never the protection.
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
import time
from pathlib import Path

import config
import database
import keyring_service
import secrets_service
import secure_delete
from database import get_db, iter_chunks

logger = logging.getLogger(__name__)

#: Re-exported, not re-declared: two spellings of an environment variable name
#: is the kind of drift that leaves a switch half-wired.
SKIP_ENV = keyring_service.SKIP_ENV
_LEGACY_SECRET_NAMES = (config.SECRET_API_KEY, config.SECRET_PROXY_URL)
_UPLOAD_NAME_RE = re.compile(r"^[0-9a-f]{64}\.(png|jpg|webp)$")
_PREMIGRATE_BAK = "app.db.premigrate.bak"


class UploadsUnreadable(OSError):
    """The uploads directory exists but could not be listed.

    Load-bearing distinction, not a detail. Both halves of E6 answer the
    question "is this row's file still on disk?" by looking, and a directory
    that cannot be listed answers NO for every file it holds. That is the
    same answer an empty directory gives, and the two lead to opposite
    actions: an absent directory means those rows really are unrecoverable
    and reconcile should drop them; an unreadable one means the files are
    probably right there and dropping the rows destroys the only reference
    to them - while a clean-looking pass then discards the snapshot that
    could have undone it.

    Raised rather than returned so it lands in _bootstrap_unlocked's
    isolation wrapper, which already skips reconcile and keeps the snapshot
    whenever this step raises. Retried on the next unlock.
    """


def _list_uploads(uploads: Path) -> set[str] | None:
    """Names in the uploads dir, or None when it is genuinely absent.

    One syscall instead of one per sha, and - the point - it cannot quietly
    answer "not there" for a directory it merely failed to open.
    """
    try:
        return {entry.name for entry in uploads.iterdir()}
    except FileNotFoundError:
        # The normal terminal state: migrate_upload_files_to_blobs() rmdir's
        # the directory once it has emptied it.
        return None
    except OSError as exc:
        raise UploadsUnreadable(
            f"uploads directory present but unlistable: {exc.__class__.__name__}"
        ) from exc


def _keyring_disabled() -> bool:
    return keyring_service.disabled()


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
        if legacy is not None and database.get_setting(
                keyring_service.revoked_key(name)) == "1":
            # The user deleted this through Settings and the credential store
            # would not let go of its copy. Importing it back is what the
            # delete route exists to prevent, and "vault empty, keyring set"
            # is the same shape as "never imported" - the tombstone is the
            # only thing that tells them apart.
            if keyring_service.delete_legacy(name):
                database.set_setting(keyring_service.revoked_key(name), "")
                logger.info(
                    "legacy-migration: the revoked %s is finally gone from "
                    "the credential store.", name)
            else:
                logger.warning(
                    "legacy-migration: %s was revoked here but its credential "
                    "store copy is still readable on this machine.", name)
            continue
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
    # is_dir() first: is_redirected fails closed on ENOENT, and an absent
    # uploads folder is the NORMAL terminal state - _list_uploads below
    # returns None for it and the migration exits clean.
    if uploads.is_dir() and secure_delete.is_redirected(uploads):
        # Found by sweeping for the shape rather than from a report, and it is
        # the worst-placed of the family: it runs on the unlock bootstrap and
        # it shreds PICTURES - the user's own uploads, in the clear.
        #
        # The `entry.is_symlink()` check below is not this check. A junction
        # is a reparse point that islink() calls False, which is the whole
        # reason secure_delete.is_redirected exists, and it looks at the
        # entries rather than at the directory holding them. A file reached
        # through a junction has an ordinary path and passes every per-file
        # guard there is.
        #
        # Nothing is migrated either, deliberately: reading somebody else's
        # pictures into the vault is the same mistake facing the other way.
        logger.warning(
            "uploads migration: the uploads path is a redirected name - "
            "skipped. Nothing was migrated and nothing was deleted.")
        return (0, failed, 0)

    # `is_dir()` collapsed "absent" and "cannot be read" into one False, and
    # the caller read the resulting empty failed-set as a clean pass. See
    # UploadsUnreadable: this raises on the second case instead.
    names = _list_uploads(uploads)
    if names is None:
        return (0, failed, 0)

    for entry in sorted(uploads / name for name in names):
        sha = entry.name[:64] if _UPLOAD_NAME_RE.match(entry.name) else None
        try:
            if entry.is_symlink() or not entry.is_file():
                continue  # irregular entry: never touched
            if entry.name.endswith(".tmp"):
                # A half-written upload is still a piece of the user's image.
                if secure_delete.discard(entry):
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
                if secure_delete.discard(entry):
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
                # Counting a file that is still on disk as removed made the
                # summary line a lie, and left a readable orphan nobody would
                # look for again.
                if secure_delete.discard(entry):
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
                # The plaintext original of a picture now sealed in the vault.
                # A plain unlink left it recoverable, so the migration that
                # exists to get these bytes INTO the vault left a readable copy
                # of every one of them outside it.
                if not secure_delete.shred(entry):
                    raise OSError("not removed")
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
    # Read the directory ONCE, up front, and let an unreadable one raise.
    # The per-sha `is_file()` this replaces was the second, independent way
    # into the same data loss: even with the migration step guarded, a
    # directory that became unreadable between the two calls would answer
    # "no file on disk" for every row here, and every row would be doomed.
    # An absent directory (None) is the honest case where that verdict is
    # true, so it stays a delete.
    present = _list_uploads(uploads) or set()
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
            if any(f"{sha}.{ext}" in present for ext in exts):
                continue
            doomed.append(sha)
        deleted = 0
        # v1.1 audit L2: chunk the IN(...) list. A legacy upgrade with more
        # distinct orphan shas than SQLite's bound-parameter ceiling (32766)
        # would otherwise raise "too many SQL variables", roll the whole
        # reconcile pass back, and retry fruitlessly on every unlock. All
        # chunks share this one BEGIN IMMEDIATE txn, so the delete stays atomic.
        for chunk in iter_chunks(doomed):
            placeholders = ",".join("?" * len(chunk))
            deleted += con.execute(
                f"DELETE FROM attachments WHERE sha256 IN ({placeholders})",
                chunk,
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
    for name in _list_uploads(uploads) or set():
        entry = uploads / name
        if _UPLOAD_NAME_RE.match(name) and entry.is_file() \
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
    the most valuable copy.

    K-46. That rule was being applied to a file nobody had looked at.
    `exists()` was the entire test, and `backup_encrypted` wrote straight to
    the final name - so a crash or a full disk mid-write left a truncated file
    wearing the name of a snapshot, and every later unlock said "already
    present; kept" and walked on into `reconcile_attachments_without_blobs`,
    which DELETES ROWS. The never-overwrite rule then blocked the repair too:
    the half file could not be replaced by a good one, ever.

    Two changes, and the first makes the second rare rather than routine:

    - the snapshot is built under `.partial` and renamed into place, so a file
      at the final name is one that finished being written;
    - a file already at that name is opened before it is trusted, and one that
      does not open is moved aside rather than deleted - it may be a snapshot
      of an era whose passphrase this vault no longer holds, which is somebody
      else's decision to make, not this function's.

    RAISES rather than returning quietly when it cannot leave a usable
    snapshot. Its caller runs the row-deleting pass on the next line inside a
    guarded block, so a raise here skips the destructive work until the next
    unlock - the direction this whole file is supposed to fail in.
    """
    path = premigrate_backup_path()
    partial = path.with_name(path.name + ".partial")
    key = database.get_key()

    if path.exists():
        if database.check_key(key, str(path)):
            logger.info(
                "uploads-migration: premigrate backup already present; kept.")
            return
        aside = path.with_name(f"{path.name}.unreadable-{int(time.time())}")
        os.replace(path, aside)
        logger.error(
            "uploads-migration: the premigrate backup did not open with this "
            "vault's key, so it was not a usable safety net. It was moved to "
            "%s - nothing was deleted - and a fresh snapshot is being taken.",
            aside.name)

    # Whatever is here is a crashed attempt, never a snapshot: the rename is
    # the only thing that ever promotes a file to that name.
    if os.path.lexists(partial):
        secure_delete.discard(partial)

    database.backup_encrypted(str(partial))
    if not database.check_key(key, str(partial)):
        # Written, and unopenable. Saying "backup written" here is exactly the
        # sentence that made the old defect invisible.
        secure_delete.discard(partial)
        raise OSError("the premigrate backup could not be read back")
    os.replace(partial, path)
    logger.info("uploads-migration: premigrate backup written.")


def discard_premigrate_backup() -> None:
    """Remove the snapshot - call ONLY after a fully clean pass (migration
    completed with zero failures and reconcile ran)."""
    try:
        # A pre-migration snapshot of the whole attachments state.
        secure_delete.discard(premigrate_backup_path())
    except OSError:
        logger.warning("uploads-migration: premigrate backup delete failed.")


def premigrate_backup_paths() -> list[Path]:
    """Every full-vault pre-migration copy on disk, canonical name first.

    The canonical name was the only one anybody asked about, and it is not
    the only one that gets written. When a pass cannot OPEN the snapshot it
    does not delete it - it renames it to `<name>.unreadable-<ts>` and moves
    on (see the write path above). Every failed pass leaves another one, each
    a complete copy of the vault, and `exists()` on the canonical name
    reported False for all of them: the reset route already swept the whole
    family, while status could not see any of it.
    """
    base = premigrate_backup_path()
    found = [base] if base.exists() else []
    found += sorted(base.parent.glob(base.name + ".unreadable-*"))
    return found


def premigrate_backup_present() -> bool:
    """True while a stale pre-migration snapshot of the whole vault sits on
    disk, under ANY of the names one can have.

    ensure_premigrate_backup() writes it before the first uploads-migration
    pass that could delete a row, and it is NOT gated on the `pending` flag
    computed before that pass runs (see the caller's own comment) - so it
    survives long after whatever made a pass dirty is gone, on any machine
    where a pass never comes back fully clean. It is a complete copy of every
    chat, persona, secret and image, encrypted under the same key as the live
    vault, and until now no route reported it and no route removed it - the
    only trace was a log line from the one unlock that wrote it.
    """
    return bool(premigrate_backup_paths())


def discard_premigrate_backup_now(key: bytes) -> tuple[bool, str]:
    """Shred the premigrate backup on the user's own word, from a route.

    Distinct from discard_premigrate_backup() above, which the unlock
    bootstrap calls automatically once a pass comes back with zero failures -
    at that point ensure_premigrate_backup already proved the file opens with
    the current key, in the same unlock, so that path never has to ask again.

    This one is reachable at any time a pass has not come back clean, so it
    makes no such assumption: the key is checked here first, the same way
    database.discard_orphaned_enc_tmp checks an orphaned copy before touching
    it, and refused for the same reason - an encrypted file that does not
    open under the key this vault currently holds is not a stale duplicate to
    tidy away, it may be the only copy of something from an era this
    passphrase does not reach. Same reason vocabulary as that function
    ("not_present", "different_key", "in_use"), so a caller only has to learn
    it once.
    """
    path = premigrate_backup_path()
    if not path.exists():
        return False, "not_present"
    if not database.check_key(key, str(path)):
        logger.warning(
            "Refusing to delete %s: it does not open with the current key, "
            "so it may be a snapshot from a passphrase this vault no longer "
            "holds.", path.name)
        return False, "different_key"
    if not secure_delete.shred(path):
        logger.warning("%s could not be deleted and is still on disk.",
                       path.name)
        return False, "in_use"
    logger.info("Discarded the premigrate backup on the user's word.")
    return True, ""
