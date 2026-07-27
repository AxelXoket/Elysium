"""routers/vault.py - passphrase vault lifecycle (Part K, full-DB encryption).

Routes (all under /api/v1/vault, reachable while LOCKED - everything else is
gated with HTTP 423 by the middleware in main.py):
    GET  /vault/status            → {initialized, unlocked}
    POST /vault/init              → first-time setup (+ plaintext migration)
    POST /vault/unlock            → passphrase → key; 401 wrong_passphrase
    POST /vault/lock              → drop the key from RAM
    POST /vault/change-passphrase → crash-safe rekey (file backup first)

Privacy rules:
    - Passphrases are NEVER logged (mirrors keyring_service's no-log rule).
    - Responses never echo the passphrase or any key material.
    - scrypt runs in a worker thread - it is deliberately slow (~100ms+) and
      must not stall live SSE streams on the event loop.
"""

from __future__ import annotations

import asyncio
import logging
import time
from functools import partial
from pathlib import Path

import anyio.to_thread
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import config
import database
import vault_state
from crypto import KeyVault

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/vault", tags=["vault"])

# Length bounds. min enforced in-handler; max NOT on the pydantic model - a
# model-level max_length echoes the rejected passphrase back in the 422 body.
MIN_PASSPHRASE_LEN = 8
MAX_PASSPHRASE_LEN = 1024

# Serializes state-changing vault operations. init/unlock/change race each
# other on the process-global key and the identity files otherwise (two /init
# calls could interleave a DB keyed by A with identity files describing B).
_vault_lock = asyncio.Lock()


def _vault() -> KeyVault:
    return KeyVault(Path(config.DB_PATH).resolve().parent)


class PassphraseBody(BaseModel):
    model_config = {"extra": "forbid"}
    passphrase: str = Field(min_length=1)


class ChangePassphraseBody(BaseModel):
    model_config = {"extra": "forbid"}
    old_passphrase: str = Field(min_length=1)
    new_passphrase: str = Field(min_length=1)


def _check_length(passphrase: str) -> None:
    if len(passphrase) < MIN_PASSPHRASE_LEN:
        raise HTTPException(422, "passphrase_too_short")
    if len(passphrase) > MAX_PASSPHRASE_LEN:
        raise HTTPException(422, "passphrase_too_long")


def _lock_down_voice_sync() -> list[str]:
    """Everything voice-related that must not survive a lock.

    Returns the names of audio files still readable on disk afterwards.

    Isolated and non-raising, like the staged-purge step above - a worker that
    will not die is not a reason to turn a re-lock into a 500. A running
    ENGINE INSTALL is deliberately left alone: it writes packages, not user
    data, and killing a 3 GB download because the user locked the screen would
    punish exactly the cautious behaviour we want.

    Non-raising is not the same as non-reporting, which is where this went
    wrong: a teardown that blew up, or a wav Windows would not let go of, left
    the user's spoken conversation in the clear and /vault/lock still said
    {"ok": true}.
    """
    try:
        from tts.host import get_host
        return get_host().on_vault_locked()
    except Exception:
        logger.warning("vault: voice teardown failed during lock", exc_info=True)
        # The teardown did not finish, so we cannot claim the cache is clean.
        # A sentinel rather than [] - "unknown" and "none" must not look alike.
        return ["<teardown failed>"]


async def _lock_down_voice() -> list[str]:
    """Run the teardown OFF the event loop.

    It ends a subprocess: up to a couple of seconds of blocking waits. All four
    call sites hold _vault_lock inside async handlers, so doing that inline
    would freeze every request in the app for the duration."""
    return await anyio.to_thread.run_sync(_lock_down_voice_sync)


def _purge_voice_cache() -> None:
    """Remove generated speech left by a crash or a hard kill.

    The lock path and the shutdown path both wipe, but neither runs when the
    process is terminated outright - and the cache is the user's conversation
    in audible form, in the clear, next to a database that is encrypted.
    Unlock is the first moment we are alive again; clean up here. Isolated and
    non-fatal like every other bootstrap step.
    """
    left: list[str] = []
    try:
        import config as _config
        from pathlib import Path as _Path

        for wav in _Path(_config.TTS_CACHE_DIR).glob("*.wav"):
            try:
                wav.unlink()
            except OSError:
                # `pass` here made this the only cleanup path in the app that
                # could not report its own failure, while its sibling
                # host.wipe_cache names every file it had to leave. This is
                # the ONLY sweep that runs after a hard kill, so a file it
                # skips silently can outlive the session by days.
                left.append(wav.name)
    except Exception:
        logger.warning("voice-cache purge failed; will retry next unlock.")
    if left:
        logger.warning(
            "voice-cache purge: %d file(s) could not be deleted and are still "
            "readable on disk: %s", len(left), ", ".join(left[:5]),
        )


def _bootstrap_unlocked() -> str | None:
    """Deferred, SELF-HEALING startup work that needs the key. Idempotent and
    run on every init/unlock, so a state left half-finished by a crashed or
    file-locked migration is completed on the next unlock instead of bricking.
    Canonical order:
      1) adopt an encrypted copy orphaned mid-migration-swap;
      2) migrate a still-plaintext app.db into the vault (backup kept);
      3) build the schema (fatal on failure - the only fatal step);
      4) E5: legacy keyring secrets -> vault (isolated);
      5) E6: legacy upload files -> blobs, then reconcile (isolated; a
         premigrate snapshot guards the first row-deleting pass);
      6) purge stale staged uploads.
    Steps 4-5 log full tracebacks on failure (never values) and retry on the
    next unlock; they can never make unlock itself fail.
    Returns the plaintext backup path if a migration ran this call, else None.
    """
    _purge_voice_cache()
    import legacy_migration

    key = vault_state.get_key()
    database.adopt_orphaned_enc_tmp(key)
    backup: str | None = None
    if database.is_plaintext_db():
        backup = database.migrate_plaintext_to_encrypted(key)
    database.init_db()

    try:
        legacy_migration.migrate_legacy_secrets()
    except Exception:
        logger.exception(
            "legacy-migration: secrets step failed; will retry next unlock."
        )

    try:
        pending = legacy_migration.uploads_migration_pending()
        if pending:
            legacy_migration.ensure_premigrate_backup()
        _migrated, failed_shas, _removed = (
            legacy_migration.migrate_upload_files_to_blobs()
        )
        legacy_migration.reconcile_attachments_without_blobs(failed_shas)
        # The snapshot outlives every pass with ANY failure; a fully clean
        # pass discards it (watch-point 2).
        #
        # NOT gated on `pending`. That flag is computed BEFORE the migration
        # runs, so once the condition that made a pass dirty disappeared - the
        # user deletes the stuck uploads/<sha>.png by hand - every later pass
        # was clean AND pending=False, and the snapshot the dirty pass left
        # behind could never be reached: a permanently orphaned, full-size
        # encrypted copy of the whole DB in %LOCALAPPDATA%, mentioned in no UI
        # and no README. discard_premigrate_backup() is a no-op when there is
        # nothing to remove.
        if not failed_shas:
            legacy_migration.discard_premigrate_backup()
    except legacy_migration.UploadsUnreadable:
        # Called out separately from the generic handler because it is the
        # one failure that USED to look like success. is_dir() answered False
        # for a directory it could not read, the migration returned an empty
        # failed-set, reconcile read that as "no file on disk" for every row
        # and deleted them all, and `not failed_shas` then discarded the
        # snapshot that could have undone it - both halves of the recovery
        # gone in one pass, silently. Now it lands here: nothing deleted,
        # snapshot kept, retried on the next unlock.
        logger.exception(
            "uploads-migration: uploads directory present but unlistable; "
            "NOTHING deleted, premigrate snapshot KEPT, retry next unlock."
        )
    except Exception:
        # Reconcile is skipped automatically when the migration raises
        # (exception jumps here first) - rows stay, files stay, retry next
        # unlock. Traceback logged; no values.
        logger.exception(
            "uploads-migration: failed; reconcile skipped; will retry next unlock."
        )

    # Isolated like the migration steps: a busy write lock here (concurrent
    # data write in the already-open gate window) must NOT fail an otherwise
    # correct unlock. Schema init above stays fatal; this cleanup does not.
    # (v1.1 FB4.)
    try:
        from attachments_service import purge_stale_staged
        purge_stale_staged()
    except Exception:
        logger.exception(
            "staged-purge: failed during unlock bootstrap; will retry next unlock."
        )

    _preload_voice_model()
    return backup


def _preload_voice_model() -> None:
    """Start bringing the selected voice model up, in the background.

    Loading is SLOW - a cold Fish S2 pays a torch.compile that the worker's own
    progress line calls "first compile is slow", and TTS_LOAD_TIMEOUT_S is 180.
    Left lazy, the whole of that landed on the first press of Speak: the button
    appeared to do nothing for up to three minutes, on the one interaction that
    is supposed to be instant.

    Unlock is the right moment, not app start: resolving the model reads the
    model's saved parameters out of the vault, which does not exist until the
    passphrase is in.

    A DAEMON THREAD, and never fatal. Unlock must not wait for a GPU, and a
    voice that will not come up must cost the audio and nothing else - the same
    rule the streaming hook follows. host.load() publishes state="loading"
    before it blocks, so /tts/active can report the progress while this runs.
    """
    import threading

    def work() -> None:
        try:
            from routers import tts_runtime
            from tts.host import get_host

            uid = database.get_setting(tts_runtime.SETTING_ACTIVE_UID)
            if not (uid or "").strip():
                return                      # nothing chosen - nothing to warm
            host = get_host()
            snap = host.snapshot()
            if snap["state"] in ("loading", "loaded") and snap["uid"] == uid:
                return
            model = tts_runtime._resolve(uid)
            logger.info("Preloading the voice model chosen for this vault.")
            host.load(model, tts_runtime._values_for(model))
            logger.info("Voice model ready.")
        except Exception:
            # Every reason lands here on purpose: no engine installed, no GPU,
            # a renamed model folder, a worker that will not start. The UI
            # reports all of them from /tts/active when somebody looks; none of
            # them is a reason to disturb an unlock that otherwise worked.
            logger.info("Voice preload did not complete.", exc_info=True)

    threading.Thread(target=work, name="tts-preload", daemon=True).start()


@router.get("/status")
async def vault_status() -> dict:
    vault = _vault()
    db_exists = Path(config.DB_PATH).exists()
    encrypted_db = db_exists and not database.is_plaintext_db()
    # "initialized" = the unlock screen is the right UI: identity files exist,
    # or an encrypted DB + salt survive with a lost verifier (recoverable at
    # unlock via DB-validated recovery).
    initialized = vault.is_initialized() or (encrypted_db and vault.can_derive())
    return {
        "initialized": initialized,
        "unlocked": vault_state.is_unlocked(),
        # A stranded .enc-tmp is a full, readable copy of the vault. It is
        # never deleted automatically, so without a field here the only trace
        # is a log line - which is how one sat unnoticed beside a freshly
        # created empty vault while the user assumed their data was gone.
        "orphaned_copy": database.orphaned_enc_tmp_present(),
    }


@router.post("/init")
async def vault_init(body: PassphraseBody) -> dict:
    """First-time setup: create identity files, key the vault, migrate any
    pre-vault plaintext DB (backed up first), build the schema."""
    _check_length(body.passphrase)
    async with _vault_lock:
        vault = _vault()
        if vault.is_initialized():
            raise HTTPException(409, "vault_already_initialized")
        # Refuse to mint a NEW identity over an existing ENCRYPTED database -
        # that combination means identity files were lost; recovery, not init,
        # is the correct path (a fresh salt can never open the old data).
        if Path(config.DB_PATH).exists() and not database.is_plaintext_db():
            raise HTTPException(409, "encrypted_db_without_identity")

        key = await anyio.to_thread.run_sync(vault.initialize, body.passphrase)
        vault_state.set_key(key)
        try:
            backup = await anyio.to_thread.run_sync(_bootstrap_unlocked)
        except Exception:
            # Leave no half-open state: a failed bootstrap relocks the vault.
            vault_state.clear_key()
            await _lock_down_voice()
            logger.exception("Vault init bootstrap failed")
            raise HTTPException(500, "vault_init_failed")
        logger.info("Vault initialized%s", " (plaintext DB migrated)" if backup else "")
        return {
            "ok": True,
            "migrated": backup is not None,
            "backup": Path(backup).name if backup else None,
        }


@router.post("/unlock")
async def vault_unlock(body: PassphraseBody) -> dict:
    async with _vault_lock:
        vault = _vault()
        if vault_state.is_unlocked():
            # Same shape as the real path below. One route that sometimes
            # carries `migrated`/`backup` and sometimes does not is how a
            # consumer learns to stop looking for them.
            return {"ok": True, "migrated": False, "backup": None}
        if not vault.can_derive():
            raise HTTPException(409, "vault_not_initialized")

        key = await anyio.to_thread.run_sync(vault.unlock, body.passphrase)
        if key is None:
            # Verifier said no (or is missing). The DB itself is the final
            # authority - a lost/corrupt verifier must not lock the user out.
            key = await anyio.to_thread.run_sync(
                vault.recover_with_db, body.passphrase, database.check_key
            )
        if key is None:
            logger.info("Vault unlock rejected (wrong passphrase)")
            raise HTTPException(401, "wrong_passphrase")

        vault_state.set_key(key)
        try:
            migrated_backup = await anyio.to_thread.run_sync(_bootstrap_unlocked)
        except Exception:
            vault_state.clear_key()
            await _lock_down_voice()
            logger.exception("Vault unlock bootstrap failed")
            raise HTTPException(500, "vault_unlock_failed")
        logger.info("Vault unlocked")
        # _bootstrap_unlocked has ALWAYS returned this path and this route has
        # always thrown it away. A plaintext pre-vault app.db can be migrated
        # on the unlock path just as easily as on the init path, and when it
        # is, `app.db.plain.bak-<ts>` stays on disk holding every message,
        # persona, system prompt, API key and image in the clear. /vault/init
        # reports it; unlock answered {"ok": true} and left the user with no
        # way to learn the file exists. Same field, same schema, same banner.
        return {
            "ok": True,
            "migrated": migrated_backup is not None,
            "backup": Path(migrated_backup).name if migrated_backup else None,
        }


@router.post("/lock")
async def vault_lock() -> dict:
    # Serialized with init/unlock/change: clearing the key mid-bootstrap
    # would make the in-flight unlock fail with a spurious 500 (self-healing,
    # but avoidable by simply waiting our turn).
    async with _vault_lock:
        vault_state.clear_key()
        audio_left = await _lock_down_voice()
        # Drop the HTTP client too: it snapshots the proxy URL (a secret) at
        # build time and would otherwise keep it in RAM while locked. The
        # next unlocked request lazily rebuilds from fresh vault values.
        from network_client import close_client
        await close_client()
    logger.info("Vault locked")
    # The key is gone either way, so this is still ok: true. But "locked" is a
    # promise about what is readable, and generated speech is the user's
    # conversation in audible form sitting in the clear. When some of it
    # survived the wipe, the screen that says "locked" is the screen that has
    # to say so - the same rule the plaintext-backup banner already follows.
    return {"ok": True, "audio_left": audio_left}


def _rekey_sidecars(db_path: Path, skip: Path, old_key: bytes,
                    new_key: bytes) -> list[str]:
    """Re-encrypt every encrypted sidecar copy of the DB under the new key.

    Returns the names of the files it could NOT re-key.

    Best effort by design: a snapshot that cannot be re-keyed (held open, or
    written by a build whose format we do not recognise) must not fail a
    passphrase change that already succeeded - but it MUST be reported,
    because it is exactly the file the rotation failed to revoke. That
    "reported" was a logger.warning and nothing else, while the route
    answered a flat {"ok": True}: the user rotating a leaked passphrase was
    told the rotation succeeded, and app.db.premigrate.bak - a complete copy
    of every chat, persona, secret and image - stayed openable with the old
    one. Naming the files here is what lets the route say so.
    """
    unrevoked: list[str] = []
    for path in sorted(db_path.parent.glob(db_path.name + "*.bak*")):
        if path == skip or not path.is_file():
            continue
        if ".plain.bak" in path.name:
            # A PLAINTEXT pre-vault copy - no key to rotate. It is reported to
            # the user on its own path (see /vault/init's `backup` field).
            continue
        try:
            database.rekey_file(str(path), new_key, old_key)
        except Exception:
            unrevoked.append(path.name)
            logger.warning(
                "Passphrase changed, but %s could not be re-keyed - it is "
                "still readable with the OLD passphrase.", path.name,
            )
    return unrevoked


@router.post("/change-passphrase")
async def vault_change_passphrase(body: ChangePassphraseBody) -> dict:
    _check_length(body.new_passphrase)
    async with _vault_lock:
        vault = _vault()
        was_unlocked = vault_state.is_unlocked()
        old_key = await anyio.to_thread.run_sync(vault.unlock, body.old_passphrase)
        if old_key is None:
            # FB5a: parity with the unlock path - the DB is the final
            # authority, so a corrupt verifier must not reject a CORRECT old
            # passphrase. Fall back to a DB-validated recovery.
            old_key = await anyio.to_thread.run_sync(
                vault.recover_with_db, body.old_passphrase, database.check_key,
            )
        if old_key is None:
            raise HTTPException(401, "wrong_passphrase")

        # FB5b: NO set_key(old_key) here. Reading the current key from
        # vault_state would force a LOCKED vault open (the 423 gate would let
        # every mutating request through) for the whole rekey window. The key
        # rides as an explicit argument instead, so a locked vault stays locked
        # until the change fully succeeds.

        # Online-backup safety net before the (non-atomic) rekey. change_
        # passphrase VERIFIES the new key actually took before swapping
        # identity files (a rekey under a write lock can silently no-op), so
        # this backup is only ever needed for a hard crash mid-rekey.
        db_path = Path(config.DB_PATH)
        backup = db_path.with_name(db_path.name + f".rekey.bak-{int(time.time())}")
        if db_path.exists():
            await anyio.to_thread.run_sync(
                partial(database.backup_encrypted, str(backup), key=old_key),
            )
        try:
            new_key = await anyio.to_thread.run_sync(partial(
                vault.change_passphrase,
                body.new_passphrase,
                partial(database.rekey_db, current_key=old_key),
                database.check_key,
            ))
        except Exception:
            # Rekey did not take (or failed): the DB + old identity are intact,
            # and lock state was never touched. Keep the backup for forensics.
            logger.exception("Passphrase change failed; DB backup kept at %s", backup.name)
            raise HTTPException(500, "change_passphrase_failed")
        vault_state.set_key(new_key)
        # A rotation must REVOKE the old passphrase. A sidecar snapshot is a
        # complete copy of the vault - every chat, message, persona, secret and
        # image - and app.db.premigrate.bak is kept on purpose whenever an
        # uploads migration could not finish, so it can outlive any number of
        # passphrase changes. Left under the old key, somebody who knew the old
        # passphrase could still open it.
        unrevoked = await anyio.to_thread.run_sync(
            partial(_rekey_sidecars, db_path, backup, old_key, new_key),
        )
        migrated_backup: str | None = None
        if not was_unlocked:
            # Locked→unlocked transition via a successful change: run the
            # deferred schema/purge bootstrap the unlock path would have.
            try:
                migrated_backup = await anyio.to_thread.run_sync(_bootstrap_unlocked)
            except Exception:
                # FB5c: the change SUCCEEDED but the bootstrap did not. Re-lock
                # so a failed bootstrap never leaves the key resident; the next
                # unlock self-heals (bootstrap is idempotent).
                vault_state.clear_key()
                await _lock_down_voice()
                logger.exception("Post-change bootstrap failed; vault re-locked")
        backup.unlink(missing_ok=True)
        logger.info("Vault passphrase changed")
        return {
            "ok": True,
            # The files this rotation did NOT revoke. Empty on the normal
            # path; when it is not, the user is the only one who can decide
            # whether to delete them, and they cannot decide about a list
            # they were never shown.
            "unrevoked": unrevoked,
            "migrated": migrated_backup is not None,
            "backup": Path(migrated_backup).name if migrated_backup else None,
        }
