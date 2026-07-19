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
        # The snapshot outlives every pass with ANY failure; only a fully
        # clean pass may discard it (watch-point 2).
        if pending and not failed_shas:
            legacy_migration.discard_premigrate_backup()
    except Exception:
        # Reconcile is skipped automatically when the migration raises
        # (exception jumps here first) - rows stay, files stay, retry next
        # unlock. Traceback logged; no values.
        logger.exception(
            "uploads-migration: failed; reconcile skipped; will retry next unlock."
        )

    from attachments_service import purge_stale_staged
    purge_stale_staged()
    return backup


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
            return {"ok": True}
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
            await anyio.to_thread.run_sync(_bootstrap_unlocked)
        except Exception:
            vault_state.clear_key()
            logger.exception("Vault unlock bootstrap failed")
            raise HTTPException(500, "vault_unlock_failed")
        logger.info("Vault unlocked")
        return {"ok": True}


@router.post("/lock")
async def vault_lock() -> dict:
    # Serialized with init/unlock/change: clearing the key mid-bootstrap
    # would make the in-flight unlock fail with a spurious 500 (self-healing,
    # but avoidable by simply waiting our turn).
    async with _vault_lock:
        vault_state.clear_key()
        # Drop the HTTP client too: it snapshots the proxy URL (a secret) at
        # build time and would otherwise keep it in RAM while locked. The
        # next unlocked request lazily rebuilds from fresh vault values.
        from network_client import close_client
        await close_client()
    logger.info("Vault locked")
    return {"ok": True}


@router.post("/change-passphrase")
async def vault_change_passphrase(body: ChangePassphraseBody) -> dict:
    _check_length(body.new_passphrase)
    async with _vault_lock:
        vault = _vault()
        was_unlocked = vault_state.is_unlocked()
        old_key = await anyio.to_thread.run_sync(vault.unlock, body.old_passphrase)
        if old_key is None:
            raise HTTPException(401, "wrong_passphrase")
        vault_state.set_key(old_key)  # rekey_db reads the current key from state

        # Online-backup safety net before the (non-atomic) rekey. change_
        # passphrase VERIFIES the new key actually took before swapping
        # identity files (a rekey under a write lock can silently no-op), so
        # this backup is only ever needed for a hard crash mid-rekey.
        db_path = Path(config.DB_PATH)
        backup = db_path.with_name(db_path.name + f".rekey.bak-{int(time.time())}")
        if db_path.exists():
            await anyio.to_thread.run_sync(database.backup_encrypted, str(backup))
        try:
            new_key = await anyio.to_thread.run_sync(
                vault.change_passphrase,
                body.new_passphrase,
                database.rekey_db,
                database.check_key,
            )
        except Exception:
            # Rekey did not take (or failed): the DB + old identity are intact.
            # Restore the pre-call lock state; keep the backup for forensics.
            if not was_unlocked:
                vault_state.clear_key()
            logger.exception("Passphrase change failed; DB backup kept at %s", backup.name)
            raise HTTPException(500, "change_passphrase_failed")
        vault_state.set_key(new_key)
        if not was_unlocked:
            # Locked→unlocked transition via a successful change: run the
            # deferred schema/purge bootstrap the unlock path would have.
            try:
                await anyio.to_thread.run_sync(_bootstrap_unlocked)
            except Exception:
                logger.exception("Post-change bootstrap failed")
        backup.unlink(missing_ok=True)
        logger.info("Vault passphrase changed")
        return {"ok": True}
