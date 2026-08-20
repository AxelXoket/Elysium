"""routers/vault.py - passphrase vault lifecycle (Part K, full-DB encryption).

Routes (all under /api/v1/vault, reachable while LOCKED - everything else is
gated with HTTP 423 by the middleware in main.py):
    GET  /vault/status            → {initialized, unlocked}
    POST /vault/init              → first-time setup (+ plaintext migration)
    POST /vault/unlock            → passphrase → key; 401 wrong_passphrase
    POST /vault/lock              → drop the key from RAM
    POST /vault/change-passphrase → crash-safe rekey (file backup first)
    POST /vault/discard-plaintext-backup → shred the pre-vault copies
    POST /vault/discard-orphaned-copy → shred a stranded encrypted copy
    POST /vault/discard-empty-stub → remove the 0-byte stub a recovery moved aside
    POST /vault/discard-premigrate-backup → shred a stale pre-migration snapshot
    POST /vault/reset → wipe every artefact of this vault (locked-state only, typed confirmation)

Privacy rules:
    - Passphrases are NEVER logged (mirrors keyring_service's no-log rule).
    - Responses never echo the passphrase or any key material.
    - scrypt runs in a worker thread - it is deliberately slow (~100ms+) and
      must not stall live SSE streams on the event loop.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import sys
import time
from functools import partial
from pathlib import Path

import anyio.to_thread
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import config
import database
import launch_token
import passphrase_strength
import secure_delete
import vault_state
import crypto
from crypto import KeyVault

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/vault", tags=["vault"])

# Length bounds. min enforced in-handler; max NOT on the pydantic model - a
# model-level max_length echoes the rejected passphrase back in the 422 body.
# Kept as names here because callers and tests import them from this module;
# the values and the reasoning live in passphrase_strength.py.
MIN_PASSPHRASE_LEN = passphrase_strength.MIN_PASSPHRASE_LEN
MAX_PASSPHRASE_LEN = passphrase_strength.MAX_PASSPHRASE_LEN

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
    """Refuse a passphrase that an offline attack would walk through.

    Applied when a passphrase is SET, never when one is used: raising the bar
    must not lock out somebody who already has a vault. They meet the new one
    the next time they change it.
    """
    reason = passphrase_strength.assess(passphrase)
    # Spelled out rather than `raise HTTPException(422, reason)`. The error
    # vocabulary guard scans this codebase for LITERAL codes and checks each
    # one has a sentence in errorMessages.ts; a computed detail is invisible
    # to it, so the concise version would have quietly removed every
    # passphrase code from the only thing that notices an unmapped one.
    if reason == "passphrase_too_short":
        raise HTTPException(422, "passphrase_too_short")
    if reason == "passphrase_too_long":
        raise HTTPException(422, "passphrase_too_long")
    if reason == "passphrase_too_common":
        raise HTTPException(422, "passphrase_too_common")
    if reason == "passphrase_too_simple":
        raise HTTPException(422, "passphrase_too_simple")
    if reason is not None:                               # pragma: no cover
        # A new code in passphrase_strength with no branch here. Refusing is
        # right; the generic detail is the signal that this list is stale.
        raise HTTPException(422, "passphrase_invalid")


#: How long a wrong passphrase waits before it is told so.
#:
#: K-30, and the shape is the owner's decision after the measurement came in.
#: A graduated lockout was the original plan and it was dropped, for a reason
#: worth keeping written down: no counter this app can persist is out of reach
#: of somebody who has the data folder, so a ladder does nothing at all
#: against the attacker the threat model actually names - the one who copies
#: the folder and guesses at home, where this code never runs. It would only
#: have slowed the person sitting at this keyboard, which is what a flat delay
#: does too, without ever locking an honest user out of their own vault and
#: without needing a clock that survives a reboot.
#:
#: So README's and SECURITY.md's "there is no rate limit behind this vault"
#: stays true and stays unedited. It was always a statement about the offline
#: attack, and it still is.
#:
#: Two seconds because scrypt already charges about a quarter of one, and the
#: two together put a hand-typed guess near three seconds - slow enough to
#: matter to a person, short enough that a real typo is not a punishment.
WRONG_PASSPHRASE_DELAY_S = 2.0


async def _refuse_passphrase(where: str) -> None:
    """Wait, then refuse. Never called while holding _vault_lock.

    The wait is outside the lock deliberately. Sleeping inside it would hold
    every other vault route - including /vault/status, which the lock screen
    polls and which main.py keeps out of the idle clock precisely so it can
    answer at any time - hostage to somebody else's typo.
    """
    logger.info("Vault %s rejected (wrong passphrase)", where)
    await asyncio.sleep(WRONG_PASSPHRASE_DELAY_S)
    raise HTTPException(401, "wrong_passphrase")


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

        cache = _Path(_config.TTS_CACHE_DIR)
        # is_dir() first: is_redirected fails closed on ENOENT, and this
        # runs on every unlock - including on installs where voice has
        # never been used and the folder has never been made. Without it,
        # those users get an alarming line in the log at every launch.
        if cache.is_dir() and secure_delete.is_redirected(cache):
            # Widest reach of the three sweeps over this directory: no name
            # prefix and no age cutoff, so every .wav in the junction target
            # went, however new and whoever recorded it. And it runs
            # unattended on every unlock, so a user who moved their cache to
            # another drive lost files by opening the app.
            #
            # tts/host.py refuses the identical directory in wipe_audio_cache.
            # This one and the per-sentence trim did not.
            logger.warning(
                "voice-cache purge: the cache path is a redirected name - "
                "not swept. Nothing was deleted.")
            return

        for wav in cache.glob("*.wav"):
            try:
                if not secure_delete.shred(wav):
                    raise OSError("not removed")
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


#: Kept in the vault, not in an env var and not in browser storage: a switch
#: that protects the screen is not one somebody can read and flip without the
#: passphrase. Off by default - the owner said they take screenshots.
SETTING_SCREEN_PRIVACY = "screen_privacy_enabled"


def screen_privacy_enabled() -> bool:
    """Fails CLOSED on any exception, including a locked vault.

    Same shape as image_output_enabled. A protection setting that defaults to
    "on" when it cannot be read would black out the window of somebody who
    never asked for it, with no way to see why.
    """
    try:
        raw = (database.get_setting(SETTING_SCREEN_PRIVACY) or "").lower()
    except Exception:
        return False
    return raw in ("1", "true")


def _apply_screen_privacy(unlocked: bool) -> None:
    """Put the window's capture exclusion where the vault says it belongs.

    A STATE TRANSITION, not a one-time setting. While the vault is locked there
    is no conversation on screen - only a passphrase box, and that is masked -
    so the protection comes off. It goes back on at unlock. Both directions
    matter: leaving it on while locked would black out a window with nothing
    to hide, and leaving it off after unlock would be the setting silently not
    applying.

    Never raises into a route. On a build with no window this is a no-op.
    """
    try:
        import win_hardening
        win_hardening.set_screen_privacy(unlocked and screen_privacy_enabled())
    except Exception:
        logger.warning("screen-capture setting could not be applied")


def _key_opens_the_database(key: bytes) -> bool:
    """Whether this key actually opens the database that is on disk.

    Only an ENCRYPTED file has an opinion about keys. A missing app.db (first
    unlock after setup), an empty one, and a still-plaintext one awaiting
    migration all answer True here, because refusing on any of them would
    close the very paths `_bootstrap_unlocked` exists to walk.

    WHAT THIS CANNOT DO, and the limit is worth naming: it only speaks when
    the passphrase was already accepted. If someone types the OLD passphrase
    against a database from that same era, the verifier rejects it before this
    is reached and the answer is still "wrong passphrase" - which is a true
    sentence about the identity files and a misleading one about the disk.
    Telling those apart would mean deriving keys nobody handed us.
    """
    if database.classify_db_file() != database.DB_ENCRYPTED:
        return True
    return database.check_key(key, database.DB_PATH)


def _sweep_crashed_rotation_backups(key: bytes) -> list[str]:
    """Remove the full vault copies a crashed rotation left behind.

    K-44. Both rotations - the passphrase change and the KDF upgrade - copy
    the whole database to `app.db.rekey.bak-<ts>` before touching it, and
    remove it after. Kill the process in that window and the copy stays: it
    is in none of the remnant families /vault/status reports, no discard route
    removes it, and on the KDF path there is no HTTP answer to carry the news
    either. A complete copy of every chat, persona, secret and image, that
    nothing would ever mention again.

    Removed here rather than given a route of its own, and the reason is what
    the two cases look like:

    - it opens under the current key: this vault can read it, it is a
      redundant duplicate of the live database, and there is nothing for a
      user to decide about it. Swept.
    - it does not open: it is a copy from before a rotation that DID finish,
      readable only with the revoked passphrase. Left alone and logged loudly.
      Deleting something this app cannot read is the one thing every other
      discard path in this file refuses to do, and a route offering to do it
      would be a button that destroys data nobody could check first.

    Returns the names it left behind, so /vault/status can say they are there.
    """
    left: list[str] = []
    for path in database.rotation_backup_paths():
        if not database.check_key(key, str(path)):
            left.append(path.name)
            logger.error(
                "%s is a complete copy of the vault left by an interrupted "
                "rotation, and it does not open with the current key - so it "
                "is readable with the previous passphrase. It was NOT removed.",
                path.name)
            continue
        if secure_delete.discard(path):
            logger.info("Removed %s, left by an interrupted rotation.",
                        path.name)
        else:
            left.append(path.name)
            logger.warning("%s could not be removed and is still on disk.",
                           path.name)
    return left


def _bootstrap_unlocked() -> str | None:
    """Deferred, SELF-HEALING startup work that needs the key. Idempotent and
    run on every init/unlock, so a state left half-finished by a crashed or
    file-locked migration is completed on the next unlock instead of bricking.
    Canonical order:
      0) sweep the full copies a crashed rotation left behind;
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
    _sweep_crashed_rotation_backups(key)
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


def _vault_status_sync() -> dict:
    """Every disk answer /vault/status needs, in ONE worker-thread hop.

    This ran on the event loop. Each line below opens a file and some of them
    hand SQLCipher a key to try, which decrypts pages on the calling thread -
    and the frontend polls this route on a timer whether or not anything is
    happening, so the loop was being stalled at a fixed cadence for the life of
    the app. The same reasoning, and the same fix, as _load_completion_context
    in the completions router.
    """
    import legacy_migration

    vault = _vault()
    kind = database.classify_db_file()
    # "initialized" = the unlock screen is the right UI: identity files exist,
    # or an encrypted DB + salt survive with a lost verifier (recoverable at
    # unlock via DB-validated recovery). Only a genuinely ENCRYPTED file counts
    # for that second branch; see classify_db_file for what an empty one used
    # to do here.
    # can_recover, not can_derive, and this half of K-05 is the half that
    # matters. The unlock route's gate was the visible symptom; THIS is why
    # nobody could reach it. VaultGate branches on `initialized` before it
    # branches on `unlocked`, so a vault whose salt.bin was shelved by a
    # half-finished rotation was answered "not initialized", shown the SET UP
    # A PASSPHRASE screen, and never offered the unlock box at all - while
    # /vault/init refused with encrypted_db_without_identity because the
    # database is encrypted. Widening only the unlock gate would have fixed a
    # door nobody could walk to.
    initialized = vault.is_initialized() or (
        kind == database.DB_ENCRYPTED and vault.can_recover()
    )
    # One read of the key decides BOTH answers below, and that is the point.
    # Asking is_unlocked() here and get_key() a few lines later is two reads
    # with file I/O between them, and the idle watchdog takes the key away on
    # its own schedule - so a lock landing in that gap made get_key() raise and
    # turned the one route that must answer while locked into a 423. get_key()
    # already returns a snapshot under its own lock, so taking it once and
    # deriving "unlocked" from it closes the window instead of narrowing it.
    try:
        key = vault_state.get_key()
    except vault_state.VaultLockedError:
        key = None
    unlocked = key is not None
    return {
        "initialized": initialized,
        "unlocked": unlocked,
        # A stranded .enc-tmp is a full, readable copy of the vault. It is
        # never deleted automatically, so without a field here the only trace
        # is a log line - which is how one sat unnoticed beside a freshly
        # created empty vault while the user assumed their data was gone.
        "orphaned_copy": database.orphaned_enc_tmp_present(),
        # Whether that copy opens under the key we currently hold. It decides
        # what the user can safely do with it: a copy this vault can read is a
        # redundant duplicate, while one it cannot may be a vault under a
        # DIFFERENT passphrase - the only copy of something, not clutter.
        # null while locked, because the question needs the key to answer.
        "orphaned_copy_readable": (
            database.orphaned_enc_tmp_opens_with(key)
            if key is not None and database.orphaned_enc_tmp_present()
            else None
        ),
        # Same reasoning as orphaned_copy, and a worse file: this one is not
        # even encrypted. Migration keeps the pre-vault app.db on purpose, in
        # case the verification was wrong - but it then stayed forever, with
        # one banner on one launch as its only trace. A field makes it a
        # STATE the UI can show and act on instead of a log line.
        "plaintext_backups": [b.name for b in database.plaintext_backups()],
        # Same reasoning as the two fields above, one step smaller: the stub is
        # provably 0 bytes, so this is not about data at rest. It is about an
        # unexplained file appearing beside the vault of an app whose whole
        # pitch is that you can see what it keeps.
        "empty_stub": database.empty_stub_present(),
        # K-44. A rotation killed mid-flight leaves app.db.rekey.bak-<ts>: a
        # complete copy of the vault, in none of the three families above and
        # removed by no route. The unlock sweep takes every one of them this
        # vault can read, so a file still here while unlocked is the other
        # case - openable only with the passphrase that was rotated away - and
        # that is precisely the file the user has to know about, because it is
        # the one Elysium will not touch.
        "rotation_backups": [b.name for b in database.rotation_backup_paths()],
        # A stale copy of the whole vault, kept by legacy_migration whenever
        # an uploads-migration pass could not finish cleanly - and NOT gated
        # on whatever made a pass dirty still being true, so it can outlive
        # that condition indefinitely (see legacy_migration.discard_
        # premigrate_backup's own comment). Encrypted under the same key as
        # the live vault, so this is not a plaintext leak - the problem is
        # that it is a STALE copy: a message deleted after it was written
        # still lives inside it, which is what keeps "delete" from being
        # complete while the file is there. No route reported this or
        # removed it before now.
        "premigrate_backup": legacy_migration.premigrate_backup_present(),
        # Same shape as orphaned_copy_readable, and the same reason: null
        # while locked, because the question needs the key to answer.
        "premigrate_backup_readable": (
            database.check_key(
                key, str(legacy_migration.premigrate_backup_path()))
            if key is not None and legacy_migration.premigrate_backup_present()
            else None
        ),
    }


@router.get("/status")
async def vault_status() -> dict:
    return await anyio.to_thread.run_sync(_vault_status_sync)


@router.post("/discard-plaintext-backup")
async def discard_plaintext_backup() -> dict:
    """Delete the pre-vault plaintext copies of the database.

    Deliberately not automatic. The backup exists because a migration that
    verified wrong would otherwise have destroyed the only copy of everything
    the user ever wrote, so throwing it away is the user's call to make once
    they trust the vault.

    Needs no unlock: this removes a file that is readable WITHOUT the
    passphrase, so requiring the passphrase to remove it would protect
    nothing and strand it for anyone who forgot theirs.
    """
    # Under the same lock as init/unlock/change-passphrase, because the
    # migration those run holds this exact file mid-swap: between renaming
    # the plaintext database to .plain.bak- and moving the encrypted copy
    # into place, the backup already matches the glob below.
    async with _vault_lock:
        # Off the event loop (audit KÖK 8): this shreds a plaintext copy of the
        # WHOLE database, overwriting every byte before unlinking it, so the
        # cost scales with the size of everything the user ever wrote. Its two
        # siblings below have always run in a thread; this one was the outlier.
        # The lock stays on the loop side, wrapping the hop, exactly as they do.
        removed, left, shared = await anyio.to_thread.run_sync(
            database.discard_plaintext_backups)
    # `shared` separately from `left`: one means try again, the other means
    # the file cannot be removed from in here at all and deleting it by hand
    # will not remove the data either. Told as one list, the second reads as
    # the first and sends people off closing programs.
    return {"removed": removed, "left": left, "shared": shared}


@router.post("/discard-empty-stub")
async def discard_empty_stub() -> dict:
    """Remove the 0-byte stub an earlier recovery moved aside.

    Needs no unlock, and for a stronger reason than the plaintext backup does:
    that file is readable without the passphrase, this one has nothing in it to
    read. The size is re-checked at removal time rather than assumed - see
    database.discard_empty_stub.
    """
    async with _vault_lock:
        removed, reason = await anyio.to_thread.run_sync(
            database.discard_empty_stub)
    return {"removed": removed, "reason": reason}


@router.post("/discard-orphaned-copy")
async def discard_orphaned_copy() -> dict:
    """Delete the encrypted copy stranded by an interrupted migration.

    Unlike the plaintext backup, this one REQUIRES an unlocked vault, and the
    reason is not ceremony. The file is encrypted, so "can this user read it"
    is a real question with a real answer, and the answer decides whether
    deleting it is tidying or destroying: adoption only leaves it behind when
    the live database is healthy (redundant) or when it does not open under
    this key (possibly a vault under another passphrase). Without the key we
    cannot tell those apart, so we do not act.
    """
    if not vault_state.is_unlocked():
        raise HTTPException(423, "vault_locked")
    async with _vault_lock:
        removed, reason = await anyio.to_thread.run_sync(
            partial(database.discard_orphaned_enc_tmp, vault_state.get_key()))
    return {"removed": removed, "reason": reason}


@router.post("/discard-premigrate-backup")
async def discard_premigrate_backup() -> dict:
    """Delete a stale pre-migration snapshot of the whole vault.

    Same shape as discard-orphaned-copy above, and for the same reason it
    requires an unlocked vault: the file is encrypted, so "can this user read
    it" is a real question, and the answer decides whether deleting it is
    tidying or destroying. /vault/status's own comment on the
    `premigrate_backup` field spells out why this can survive indefinitely on
    a machine where an uploads-migration pass never comes back clean.
    """
    if not vault_state.is_unlocked():
        raise HTTPException(423, "vault_locked")
    async with _vault_lock:
        import legacy_migration
        removed, reason = await anyio.to_thread.run_sync(
            partial(legacy_migration.discard_premigrate_backup_now,
                   vault_state.get_key()))
    return {"removed": removed, "reason": reason}


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
        #
        # ENCRYPTED, exactly. This used to read `exists() and not
        # is_plaintext_db()`, which also says yes to a 0-byte file, so the one
        # state where setup is both safe and the only way forward was the state
        # that refused it. See database.classify_db_file.
        kind = await anyio.to_thread.run_sync(database.classify_db_file)
        if kind == database.DB_ENCRYPTED:
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


def _upgrade_kdf_if_needed(vault: KeyVault, passphrase: str,
                           old_key: bytes) -> bool:
    """Re-derive this vault's key under the current KDF parameters.

    Only possible HERE. Strengthening the derivation changes the key, so the
    database has to be re-keyed, and that needs the PASSPHRASE - which this
    app deliberately never stores. Unlock is the one moment it exists in
    memory, so an upgrade either happens on this path or asks the user to
    perform a passphrase change for no reason they can see.

    Every failure is non-fatal and leaves the vault exactly as it was. The
    user has already unlocked successfully; turning "your cost parameters are
    a generation old" into "you cannot get in" would be a far worse trade than
    the one this function exists to make. The next unlock tries again.
    """
    if not vault.needs_kdf_upgrade():
        return False
    db_path = Path(config.DB_PATH)
    backup = db_path.with_name(db_path.name + f".rekey.bak-{int(time.time())}")
    try:
        if db_path.exists():
            database.backup_encrypted(str(backup), key=old_key)
        new_key = vault.change_passphrase(
            passphrase,
            partial(database.rekey_db, current_key=old_key),
            database.check_key,
        )
    except Exception:
        logger.warning("KDF upgrade did not take; the vault is unchanged",
                       exc_info=True)
        if not secure_delete.discard(backup):
            # Its twin twelve lines down checks this and its sibling route
            # reports it; here the answer was dropped. The upgrade failed, so
            # the file is under the key that is STILL current - not a
            # revocation hole, but a complete second copy of the vault sitting
            # in the data folder that nothing would ever mention again. The
            # unlock sweep collects it; this line is what makes it findable in
            # the meantime.
            logger.warning(
                "KDF upgrade failed AND its backup %s could not be removed; "
                "it is a full copy of the vault under the current key.",
                backup.name)
        return False
    vault_state.set_key(new_key)
    # The same revocation the passphrase route performs. The passphrase has
    # not changed, but the KEY has, so every snapshot beside the database is
    # still readable under the old one.
    unrevoked = _rekey_sidecars(db_path, backup, old_key, new_key)
    if unrevoked:
        logger.warning("KDF upgrade left %d sidecar(s) under the old key: %s",
                       len(unrevoked), ", ".join(unrevoked))
    if not secure_delete.discard(backup):
        logger.warning("KDF upgrade could not remove %s", backup.name)
    logger.info("Vault KDF upgraded to n=%d", crypto.KDF_CURRENT["n"])
    return True


@router.post("/unlock")
async def vault_unlock(body: PassphraseBody) -> dict:
    """See the module docstring's own K-30 note for WHY the two phases below
    used to be two separate `async with _vault_lock:` blocks, and why that
    was one too many.

    The delay for a WRONG passphrase must not run with the lock held -
    sleeping inside it would hold /vault/status, the one route the lock
    screen polls and the one main.py deliberately keeps out of the idle
    clock, hostage to somebody else's typo. Releasing the lock and taking it
    again a few lines later looked like the obvious way to buy that, and it
    is what this route did. The gap that opened between those two blocks had
    nothing scheduled to fill it on the correct-passphrase path - no sleep,
    no I/O of its own - but a gap does not need something scheduled in it to
    be a gap: /vault/reset queued on the SAME lock during the scrypt
    derivation above resumes exactly there, sees is_unlocked() still False
    (set_key had not run yet) and wipes. Its own bootstrap then rebuilt
    app.db and rewrote salt.bin/verifier.bin/kdf.json from the very
    passphrase this request was about to accept, so the request answered
    200, the data was gone, and the old passphrase kept working on a vault
    that had been silently replaced under it. Confirmed at every artificial
    delay tried between the two blocks, because the window did not depend on
    the delay - it depended on the split existing at all.

    The fix keeps ONE critical section for the whole successful path, ended
    only in `finally`, and releases early in exactly the one place that still
    needs it: right before the deliberate sleep on a refusal, which always
    raises and never returns to reacquire anything.
    """
    refused = False
    key: bytes | None = None
    await _vault_lock.acquire()
    held = True
    try:
        vault = _vault()
        if vault_state.is_unlocked():
            # VERIFY ANYWAY, and this was a hole. The branch used to return
            # ok:true without looking at body.passphrase at all, so the app's
            # one "prove you know the passphrase" primitive answered yes to
            # anything whenever the vault happened to be open - and told the
            # caller ok about a passphrase that was wrong.
            #
            # change-passphrase closed the identical hole on its own route and
            # test_vault.py:666 records why: anything that can reach these
            # routes while the vault is open may use them. Nothing built on
            # unlock as a re-authentication step - confirm before shredding
            # the plaintext copy, before revealing a secret - could have
            # worked while this branch existed.
            #
            # Off the loop, like every other derivation here: scrypt is a
            # quarter of a second and this route holds _vault_lock.
            if await anyio.to_thread.run_sync(vault.unlock,
                                              body.passphrase) is None:
                refused = True
            if not refused:
                # Same shape as the real path below. One route that sometimes
                # carries `migrated`/`backup` and sometimes does not is how a
                # consumer learns to stop looking for them.
                return {"ok": True, "migrated": False, "backup": None}
        # can_recover, not can_derive. K-05: the narrower gate asked only
        # whether salt.bin exists, while recover_with_db below accepts
        # salt.bin OR salt.bin.new - so the route refused, with "this vault
        # was never set up", the exact state recovery was written to repair.
        if not vault.can_recover():
            raise HTTPException(409, "vault_not_initialized")

        key = await anyio.to_thread.run_sync(vault.unlock, body.passphrase)
        if key is None:
            # Verifier said no (or is missing). The DB itself is the final
            # authority - a lost/corrupt verifier must not lock the user out.
            key = await anyio.to_thread.run_sync(
                vault.recover_with_db, body.passphrase, database.check_key
            )
        elif not await anyio.to_thread.run_sync(_key_opens_the_database, key):
            # K-52. "The database is the final authority" was only ever true in
            # ONE direction: recovery ran when the verifier said no, and never
            # when it said yes. So a database restored from before a passphrase
            # change - a backup, a synced folder, a copy put back by hand - met
            # identity files belonging to a later vault, and the CORRECT
            # current passphrase produced a 500 forever while /vault/status
            # reported every honesty field clean. Three answers, none of them
            # the true sentence.
            key = await anyio.to_thread.run_sync(
                vault.recover_with_db, body.passphrase, database.check_key
            )
            if key is None:
                # Deliberately not 401: the passphrase was right. What is wrong
                # is that these two things on disk are not the same vault, and
                # only the user knows which one they meant to keep.
                logger.error(
                    "Unlock refused: the passphrase matches the identity "
                    "files, but the database on disk does not open with the "
                    "key they produce. They are not the same vault.")
                raise HTTPException(409, "vault_identity_mismatch")
        if key is None:
            refused = True
        if refused:
            # The ONE early release in this whole handler, and it happens
            # here rather than in `finally` because what follows is a sleep
            # that must not hold the lock - not because the critical section
            # is over. _refuse_passphrase always raises, so control never
            # returns here to reacquire anything; `held = False` tells the
            # `finally` below not to release a lock that is already released.
            _vault_lock.release()
            held = False
            await _refuse_passphrase("unlock")

        # STILL the same lock acquired at the top of this function - no
        # release, no reacquire, no window for a concurrent /vault/reset to
        # see is_unlocked() == False between "the passphrase was accepted"
        # and "the key is installed". That gap is exactly what the docstring
        # above describes and exactly what used to sit here as a second
        # `async with _vault_lock:` block.
        vault_state.set_key(key)
        # A fresh session starts idle at zero, not at however long the app sat
        # locked on the passphrase screen.
        vault_state.reset_idle_clock()
        try:
            migrated_backup = await anyio.to_thread.run_sync(_bootstrap_unlocked)
        except Exception:
            vault_state.clear_key()
            await _lock_down_voice()
            logger.exception("Vault unlock bootstrap failed")
            raise HTTPException(500, "vault_unlock_failed")
        # AFTER the bootstrap, so a vault that could not finish migrating is
        # not also re-keyed in the same breath.
        upgraded = await anyio.to_thread.run_sync(
            partial(_upgrade_kdf_if_needed, vault, body.passphrase, key),
        )
        _apply_screen_privacy(True)
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
            # Reported rather than silent: this unlock re-encrypted the whole
            # database, which is worth being able to see in a log or a test.
            "kdf_upgraded": upgraded,
        }
    finally:
        # Covers every exit that did NOT already release: the two
        # HTTPException raises above (vault_not_initialized,
        # vault_identity_mismatch), the vault_unlock_failed raise, and the
        # ordinary return. The refusal branch already released and set
        # `held = False`, so it is skipped here rather than double-released.
        if held:
            _vault_lock.release()


async def lock_vault_now(reason: str = "request") -> list[str]:
    """Everything a lock is, callable from somewhere other than the route.

    The idle watchdog has to perform exactly the same lock as the button - the
    key cleared, the voice worker torn down, the HTTP client dropped so the
    proxy URL it snapshotted does not stay in RAM. A second, slightly
    different lock would be a second, slightly weaker one.
    """
    async with _vault_lock:
        # BEFORE the key is cleared, and before the HTTP client is dropped.
        #
        # An extraction already past its planning step is holding DECRYPTED
        # chat text and is awaiting the provider. Nothing here touched it, so
        # locking the vault - by the button or by the idle watchdog - did not
        # stop that text from leaving the machine afterwards. The lock is
        # supposed to mean the conversation is closed; it did not mean it for
        # the one path that runs while nobody is watching.
        #
        # It also drains the queue: every offer still waiting would otherwise
        # wake into a locked vault and be counted as an unhandled error, so a
        # normal lock/unlock cycle left the status panel reporting failures
        # that were nothing of the kind.
        try:
            import notebook_worker
            await notebook_worker.quiesce()
        except Exception:
            logger.warning("Notebook worker did not stand down cleanly.")

        vault_state.clear_key()
        # The single funnel: the idle watchdog comes through here too, so the
        # window stops being protected the moment the conversation stops being
        # on screen - and only then.
        _apply_screen_privacy(False)
        audio_left = await _lock_down_voice()
        # Drop the HTTP client too: it snapshots the proxy URL (a secret) at
        # build time and would otherwise keep it in RAM while locked. The
        # next unlocked request lazily rebuilds from fresh vault values.
        from network_client import close_client
        await close_client()
    logger.info("Vault locked (%s)", reason)
    return audio_left


@router.post("/lock")
async def vault_lock() -> dict:
    # Serialized with init/unlock/change: clearing the key mid-bootstrap
    # would make the in-flight unlock fail with a spurious 500 (self-healing,
    # but avoidable by simply waiting our turn).
    audio_left = await lock_vault_now()
    # The key is gone either way, so this is still ok: true. But "locked" is a
    # promise about what is readable, and generated speech is the user's
    # conversation in audible form sitting in the clear. When some of it
    # survived the wipe, the screen that says "locked" is the screen that has
    # to say so - the same rule the plaintext-backup banner already follows.
    return {"ok": True, "audio_left": audio_left}


def _rekey_sidecars(db_path: Path, skip: Path, old_key: bytes,
                    new_key: bytes) -> list[str]:
    """Re-encrypt every encrypted copy of the DB beside it under the new key.

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
    # The .bak glob was the whole list, and it missed the one copy that is not
    # named like a backup: app.db.enc-tmp, left by a migration interrupted
    # between its two renames. It is a COMPLETE vault, so a rotation that
    # skipped it answered {"unrevoked": []} while every chat stayed readable
    # under the passphrase the user was rotating away from - which is the
    # precise failure this function's docstring says it exists to prevent.
    candidates = list(db_path.parent.glob(db_path.name + "*.bak*"))
    # The whole orphan family, from database, rather than the one canonical
    # name spelled out here. Migration can move a stranded copy aside under a
    # different suffix, and a name this glob does not know is a complete vault
    # the rotation silently fails to revoke - which is the failure the
    # paragraph above is about.
    candidates += database.orphaned_enc_tmp_paths()

    for path in sorted(set(candidates)):
        try:
            interesting = path != skip and path.is_file()
        except OSError:
            # The glob and the stat are separate moments, and a cleanup or an
            # antivirus pass can remove a snapshot between them. Raising here
            # would turn a rotation that ALREADY SUCCEEDED into a 500, after
            # which the user retries with a passphrase that no longer works.
            continue
        if not interesting:
            continue
        if path.stat().st_size == 0:
            # app.db.empty-stub-bak: the 0-byte live file adoption moves
            # aside. There is no ciphertext to re-key, and reporting it as
            # unrevoked would raise an alarm about a file holding nothing.
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
    # This route's delay is INDEPENDENT of the unlock route's, by decision.
    # A shared one would have meant that mistyping your current passphrase in
    # Settings - with the vault already open, on your own machine - made you
    # wait at the next launch. Two doors, two waits; skipping one by using the
    # other gains nothing, because both wait.
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
        refused = old_key is None
    if refused:
        await _refuse_passphrase("passphrase change")

    async with _vault_lock:

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
        # A COMPLETE database still encrypted under the passphrase being
        # rotated away from - deliberately excluded from the sidecar re-key so
        # it stays readable across the change. Unlinking it left every chat
        # recoverable under the revoked passphrase, which is the one outcome a
        # rotation exists to prevent.
        if not secure_delete.discard(backup):
            # Every other file this rotation touches is checked and reported.
            # This one - a COMPLETE vault under the passphrase being revoked -
            # was the one whose failure returned {"unrevoked": []}: a clean
            # rotation, reported honestly, that had revoked nothing about it.
            unrevoked.append(backup.name)
            logger.warning("Vault rotation could not remove %s", backup.name)
        # K-07: and the identity files the vault itself could not destroy. The
        # shelved salt and verifier ARE the recipe for the revoked key, so a
        # rotation that left one behind revoked nothing for anybody holding
        # the old passphrase - and this list is the only place the user could
        # ever learn that.
        unrevoked.extend(vault.left_behind)
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


# ---------------------------------------------------------------------------
# Reset: "forgot your passphrase" - wipe every artefact, no recovery exists
# ---------------------------------------------------------------------------

#: What the request body must type, verbatim (surrounding whitespace
#: stripped, nothing else forgiven - no case-folding, no synonyms). The
#: backend is the one place this is decided: a frontend field can always be
#: relaxed to a checkbox by a later edit nobody meant as a weakening, so the
#: true value has to live where nothing downstream can soften it.
#:
#: Chosen to read like a sentence describing what happens rather than like a
#: password somebody might reuse: nobody fat-fingers this by accident, and
#: unlike a single checkbox it cannot be satisfied by a client that forgot to
#: ask the user anything at all.
RESET_CONFIRMATION_PHRASE = "DELETE EVERYTHING"

#: What "surrounding whitespace" actually means here, spelled out as an
#: explicit set rather than left to str.strip()'s default. The default
#: forgives anything str.isspace() calls whitespace, and that table is far
#: wider than "surrounding whitespace" as a human reads it: NBSP (U+00A0),
#: the ideographic space (U+3000), form feed, vertical tab. None of those
#: comes out of an ordinary keystroke or a copy-paste of this exact phrase,
#: so a bare .strip() was forgiving near-misses this comment claimed it did
#: not. Tab, CR and LF stay in the set - a genuine copy-paste routinely
#: carries a trailing newline or an indenting tab, and the whole point of
#: forgiving anything is to let that through without forgiving a look-alike.
_CONFIRM_STRIP_CHARS = " \t\r\n"

#: Bounds the request body before the check above ever runs. Without this,
#: nothing stood between an empty POST and a client sending a megabyte of
#: leading spaces - str.strip() would have peeled all of it off just the
#: same, but not before the body was parsed and held in memory for a route
#: whose entire job is destructive. The slack past the phrase's own length
#: is for exactly the whitespace this route already forgives, not for
#: anything else.
RESET_CONFIRM_MAX_LEN = len(RESET_CONFIRMATION_PHRASE) + 64


def _reset_identity_files(vault_dir: Path) -> list[str]:
    """Every salt/verifier/kdf file this vault could have on disk, live or
    shelved.

    initialize() and change_passphrase() both shelve a superseded identity as
    `<name>.bak-<ts>` rather than deleting it (crypto.py), and a rotation
    killed between its two renames can leave `<name>.new` staged. None of
    that matters once app.db is gone too - but the promise this route makes
    is that NOTHING of this vault is left, not that what is left is harmless.
    """
    left: list[str] = []
    # vault.recovery is identity, not user data, and belongs in THIS family
    # rather than one of the independent ones. If app.db survives the wipe -
    # a hardlink, a lock, a read-only bit - this whole family is held back so
    # the surviving database is not bricked worse than it already is, and the
    # mirror is the last copy of the recipe for it.
    for name in ("salt.bin", "verifier.bin", "kdf.json", "vault.recovery"):
        candidates = [vault_dir / name, vault_dir / f"{name}.new"]
        candidates += sorted(vault_dir.glob(f"{name}.bak-*"))
        for path in candidates:
            if not secure_delete.discard(path):
                left.append(path.name)
    return left


def _reset_database(db_path: Path) -> list[str]:
    """The live database and its journal/WAL siblings."""
    left: list[str] = []
    if not secure_delete.discard(db_path):
        left.append(db_path.name)
    for suffix in database._SIDECAR_SUFFIXES:
        sidecar = db_path.with_name(db_path.name + suffix)
        if not secure_delete.discard(sidecar):
            left.append(sidecar.name)
    return left


def _every_orphaned_enc_tmp_path(db_path: Path) -> list[Path]:
    """Every name under the orphan glob, sidecars included.

    database.orphaned_enc_tmp_paths() deliberately drops names ending in
    -wal/-shm: every OTHER reader of that list insists each entry it is
    handed opens under a key before acting on it, and a stray journal ATTACH
    left behind never does - see that function's own comment for the bug a
    single unopenable sidecar caused once that filter did not exist. That
    safety property belongs to readers who hold a key and have to decide
    whether a file is safe to trust. Reset holds no key and asks no such
    question; it destroys NAMES, not content whose ownership it has to
    judge. So the filter built to keep those other routes honest just left
    app.db.enc-tmp-wal and app.db.enc-tmp-shm on disk here, silently, behind
    a {"ok": true, "left": []} answer. Built from the same glob those readers
    use, unfiltered.
    """
    try:
        return sorted(db_path.parent.glob(db_path.name + database.ORPHAN_GLOB))
    except OSError:
        return []


def _reset_backup_families(db_path: Path) -> list[str]:
    """Every sidecar and backup copy /vault/status already tracks by name:
    plaintext pre-vault copies, encrypted copies orphaned by an interrupted
    migration (and their journal sidecars), full snapshots a rotation left
    behind, and the 0-byte stub a recovery moved aside. Reset destroys the
    live database; leaving any of these standing would turn "gone" into
    "moved to a name with .bak in it".
    """
    left: list[str] = []
    for path in (database.plaintext_backups()
                + _every_orphaned_enc_tmp_path(db_path)
                + database.rotation_backup_paths()):
        if not secure_delete.discard(path):
            left.append(path.name)
    stub = db_path.with_name(db_path.name + ".empty-stub-bak")
    if not secure_delete.discard(stub):
        left.append(stub.name)
    return left


def _reset_premigrate_family() -> list[str]:
    """The premigrate snapshot the two routes above already track, plus the
    two names its own write path can leave behind mid-write:
    ensure_premigrate_backup's `.partial` scratch file, and the
    `.unreadable-<ts>` name an unopenable snapshot is moved aside to rather
    than deleted."""
    import legacy_migration

    left: list[str] = []
    base = legacy_migration.premigrate_backup_path()
    candidates = [base, base.with_name(base.name + ".partial")]
    candidates += sorted(base.parent.glob(base.name + ".unreadable-*"))
    for path in candidates:
        if not secure_delete.discard(path):
            left.append(path.name)
    return left


def _reset_directory_tree(path: Path, label: str) -> list[str]:
    """Shred every file under a directory, then remove the directory itself.

    Shared by the three plain-file trees this route destroys: legacy
    uploads, the voice cache and voice references, and the WebView2 browser
    profile. shred_tree refuses to follow a redirected (junction/symlink)
    name rather than descending into it - the same guard every other sweep in
    this app relies on, because a junction pointed at the user's documents
    needs no privilege to create.

    When shred_tree had to prune a redirected name, rmtree is skipped rather
    than run over what remains: rmtree would walk straight into the very
    thing that was just refused. A few empty directories left behind is the
    cheap half of that trade; the other half is not this app's to take.
    """
    if not path.is_dir():
        return []
    _removed, left, pruned = secure_delete.shred_tree(path)
    if pruned:
        logger.warning(
            "vault reset: a redirected name under %s was not swept.", label)
        left = left + [f"{label}: contains a redirected name, not fully swept"]
    else:
        shutil.rmtree(path, ignore_errors=True)
    return left


def _reset_legacy_keyring() -> list[str]:
    """The OS-keyring entries the one-time migration reads (E5).

    Nothing else in this route touches the credential store, but a reset that
    left a legacy entry standing would hand the NEXT vault's first unlock a
    secret this one never asked it to keep: migrate_legacy_secrets copies
    whatever is there into a fresh vault automatically, with no user action
    at all.
    """
    import keyring_service

    left: list[str] = []
    for name in (config.SECRET_API_KEY, config.SECRET_PROXY_URL):
        if not keyring_service.delete_legacy(name):
            left.append(f"keyring:{name}")
    return left


def _reset_runtime_files(vault_dir: Path) -> list[str]:
    """elysium.log (plus the one rotated twin backupCount=1 allows) and the
    remembered `port`.

    Neither is part of the vault's cryptographic identity, but this route's
    own docstring promises something wider: DATA_DIR left as if Elysium had
    never been run at all. elysium.log only exists on a frozen build
    (run_app.py: _setup_frozen_logging) and it records chat ids, model ids
    and session timestamps - not passphrases, not message text, but exactly
    the kind of trail someone telling this app "I forgot my passphrase,
    start over" is asking it to erase. `port` is lower stakes - a localhost
    TCP port number and nothing else - but it is still a file this run wrote
    that a clean install would not have, and the docstring made no exception
    for it.

    THIS PROCESS IS THE ONE HOLDING THE LOG OPEN, and that had to be dealt
    with rather than reasoned around. An earlier version of this comment
    claimed Python's file handles do not exclude other openers of the same
    name, so the shred could unlink it out from under the live handle. Half
    true and the wrong half: the OVERWRITE succeeds, the UNLINK does not,
    because CPython opens without FILE_SHARE_DELETE. Measured - discard()
    returns False and the name survives with its contents destroyed.

    The consequence was not cosmetic. `elysium.log` exists only on a frozen
    build, which is the only build a user ever runs, so every reset in the
    shipped app would have answered {"ok": false, "left": ["elysium.log"]}
    while three documents and this docstring promised it was destroyed. The
    test that vouched for the sweep passed because it runs UNFROZEN, where
    no handler is attached and the file is an ordinary closed file.

    So the handlers are detached and closed first. Logging survives it: the
    root logger keeps whatever other handlers it has, and a frozen build
    that wants a log again gets a fresh file on the next write, which is
    correct - the old trail is exactly what the user asked to erase.
    """
    _close_log_handlers(vault_dir / "elysium.log")
    left: list[str] = []
    for name in ("elysium.log", "elysium.log.1", "port"):
        if not secure_delete.discard(vault_dir / name):
            left.append(name)
    return left


def _close_log_handlers(log_path: Path) -> None:
    """Detach every logging handler writing to this file, and close it.

    Scoped by resolved path rather than by handler class: a caller that
    attached its own handler to the same file is holding the same lock, and
    a class check would miss it. Anything that cannot be closed is left
    attached - a half-detached logger that then raises on the next log line
    would turn a reset into a crash.
    """
    import logging

    try:
        target = log_path.resolve()
    except OSError:
        target = log_path
    for logger in [logging.getLogger()] + [
        logging.getLogger(name) for name in
        list(logging.Logger.manager.loggerDict)
    ]:
        for handler in list(getattr(logger, "handlers", [])):
            stream_name = getattr(handler, "baseFilename", None)
            if not stream_name:
                continue
            try:
                same = Path(stream_name).resolve() == target
            except OSError:
                same = str(stream_name) == str(log_path)
            if not same:
                continue
            try:
                logger.removeHandler(handler)
                handler.close()
            except Exception:
                logger.addHandler(handler)


def _reset_vault_sync() -> dict:
    """The whole wipe, off the event loop and with no key at all - the route
    below already refuses an unlocked vault before this ever runs, so nothing
    here decrypts anything. It only shreds files whose names this app already
    knows, by the same primitive every other deletion in this codebase uses.

    Every category below runs independently and a stuck file in one must not
    skip the rest: a wipe that stops at the first held-open file is a worse
    outcome than one that gets as far as it can and says exactly what did
    not go, which is the same reasoning every discard route above already
    follows for a single file at a time.

    NOT reset here, and deliberately: TTS_MODELS_DIR, the engine runtimes,
    and the uv/python install caches. Those are downloaded ENGINE software,
    multi-gigabyte and hours to reprovision (see TTS_INSTALL_TIMEOUT_S), not
    user data - re-fetching them on every forgotten passphrase would turn a
    privacy feature into a bandwidth and time tax nobody asked for. The user's
    own recorded voice references and the spoken-audio cache, which DO carry
    their conversation, are wiped below.

    Generated images are not swept as a separate directory: attachments_
    service stores every image - uploaded or model-generated - as a blob
    inside app.db itself (E6), so destroying the database already destroys
    them; there is no second copy on disk to find.

    THE ONE ORDERING THAT IS NOT INDEPENDENT

    Every family above is described as running on its own, and all of them
    do except one pair. salt.bin/verifier.bin/kdf.json are not user data;
    they are the RECIPE for the key that opens app.db. secure_delete.shred
    fails CLOSED - a hardlink to app.db (is_shared), another process holding
    a byte-range lock (antivirus, a sync client, an indexer, a second
    instance), a read-only attribute - and on an ordinary Windows machine
    every one of those is a plausible reason the database survives this
    call. If the identity files were destroyed anyway, the surviving
    database would be bricked WORSE than before this route ever ran: the
    original passphrase cannot get back in because the verifier is gone, and
    a fresh /vault/init cannot either, because it refuses to mint a new
    identity over an encrypted database it did not create
    (encrypted_db_without_identity). So the sweep below checks whether
    app.db is actually gone before it goes anywhere near its identity, and
    holds the identity files back entirely when it is not - locked, intact,
    retryable, which is strictly better than a database nobody can ever
    open again.

    Every OTHER family still runs even when app.db survives, and that is a
    second decision, not a consequence of the first. None of them is the
    recipe for app.db's key, so none of them can strand a surviving
    database the way the identity files can: the plaintext backups need no
    key to read at all; the orphaned-copy, rotation-backup and premigrate
    families are encrypted under that SAME identity, so if the identity was
    held back the live vault stays exactly as recoverable with them gone as
    with them present; and uploads, the voice cache, voice references, the
    browser profile, the legacy keyring and the runtime files
    (_reset_runtime_files: elysium.log, the one rotated twin backupCount=1
    allows, and the remembered `port`) do not depend on app.db or its
    identity in any way. The runtime files belong in that list and not in
    the paragraph above for the same reason as the rest: a log line and a
    TCP port number are not the recipe for anything, so sweeping them can
    leave a surviving database no less openable than it already was.
    Stopping the whole route at the first held-open file would leave more
    readable on disk than getting as far as it safely can and saying
    exactly what did not go - the same reasoning this
    function's own docstring already gives for a single stuck file, applied
    now to the one pair where the ORDER of "as far as it safely can" matters.
    """
    db_path = Path(config.DB_PATH)
    vault_dir = db_path.parent

    db_left = _reset_database(db_path)
    # db_path.exists(), not "db_left is non-empty": _reset_database's own
    # list also carries -wal/-shm sidecar names, and a sidecar surviving
    # alone - with the main file gone - opens nothing on its own. It is not
    # the coupling this guard exists to break; only the main file is.
    db_survives = db_path.exists()
    left: list[str] = list(db_left)
    if db_survives:
        logger.error(
            "Vault reset could not remove %s; the database is still on "
            "disk. Identity files were NOT touched, so the vault is exactly "
            "as it was before this call - locked, intact, and ready to "
            "retry once whatever is holding %s open lets go.",
            db_path.name, db_path.name)
    else:
        left += _reset_identity_files(vault_dir)

    left += _reset_backup_families(db_path)
    left += _reset_premigrate_family()
    left += _reset_directory_tree(Path(config.UPLOADS_DIR), "uploads")
    left += _reset_directory_tree(Path(config.TTS_CACHE_DIR), "voice cache")
    left += _reset_directory_tree(Path(config.TTS_REFS_DIR),
                                  "voice references")
    left += _reset_directory_tree(Path(config.DATA_DIR) / "webview",
                                  "browser profile")
    left += _reset_legacy_keyring()
    left += _reset_runtime_files(vault_dir)

    if db_survives:
        logger.warning(
            "Vault reset stopped short of a clean slate: %d artefact(s) "
            "could not be removed and the database survived, so the "
            "identity files were deliberately held back: %s",
            len(left), ", ".join(left))
    elif left:
        logger.warning(
            "Vault reset ran but %d artefact(s) could not be removed: %s",
            len(left), ", ".join(left))
    else:
        logger.info("Vault reset: every known artefact was removed.")
    return {"ok": not left, "left": left}


class VaultResetBody(BaseModel):
    model_config = {"extra": "forbid"}
    # max_length is safe on THIS field where it is not on PassphraseBody's:
    # `confirm` is a fixed public phrase, never a secret, so echoing a
    # rejected one back in a 422 body leaks nothing.
    confirm: str = Field(min_length=1, max_length=RESET_CONFIRM_MAX_LEN)


def _reset_door_is_open() -> bool:
    """Whether this build offers the reset door at all. TWO conditions.

    Neither is sufficient alone, and saying why is the whole value of this
    function, because "only from the app window" is what was asked for and
    neither half delivers it.

    sys.frozen is a property of THIS PROCESS, not of the caller. A curl at the
    packaged exe is exactly as frozen as the window is. All it establishes is
    "this is the shipped app rather than a development tree", which is the
    half about the build and not the half about who is asking.

    An ARMED launch-token gate is the half that speaks about the caller. When
    launch_token.configured() is not None, main.py's launch_token_gate has
    already refused every /api/v1 request that did not carry this launch's own
    secret before any handler ran, and its one exemption is GET-only and bound
    by regex to two other paths. So reaching this line with the gate armed
    means the token was presented and compared with hmac.compare_digest. This
    function deliberately does NOT re-read that header: checking a second time
    would add a second place for the comparison to be got wrong.

    Armed alone is not enough either. run_app.py issues a token on the
    development path too, and configured() falls back to ELYSIUM_LAUNCH_TOKEN
    out of the environment as a deliberate developer seam. Requiring BOTH is
    what leaves `uvicorn main:app` - not frozen, no token - with no reset door
    at all, which is the point.

    What this does NOT claim, stated here rather than left for a reader to
    assume: it does not distinguish the window from another local process
    holding this launch's token. That residual is the one the route's own
    docstring already states, and nothing short of an out-of-band channel
    closes it. This narrows the door from "any build at all" to "the shipped
    build with an armed gate". It does not narrow it to the renderer.
    """
    if not getattr(sys, "frozen", False):
        return False
    return launch_token.configured() is not None


@router.post("/reset")
async def vault_reset(body: VaultResetBody) -> dict:
    """Wipe every artefact of this vault and leave DATA_DIR as if Elysium had
    never been run. The "forgot your passphrase" answer the owner asked for:
    there is no recovery, so the only honest response to a lost passphrase is
    starting over.

    THE CENTRAL PROBLEM, NAMED RATHER THAN BURIED

    This has to work from the LOCKED state - a route that needed the
    passphrase would be no use to the one person it exists for - which makes
    it a destructive action reachable without proving who is asking. Three
    things stand between "reachable" and "reachable by accident, or by
    whoever merely got to this machine first":

      * the launch-token gate in main.py (see launch_token.py), armed on
        every packaged build. It refuses any request that does not carry
        THIS launch's own secret, which stops a DIFFERENT PROCESS on the
        same machine from curling this route blind. It does NOT stop
        something that can read the token out of this app's own window - a
        malicious browser extension, devtools access to the renderer, or the
        WebView2 session-restore residue launch_token.py's own docstring
        names as a real, accepted gap - and it is simply absent in dev,
        where nothing issues a token and the gate lets everything through.
      * the cross-origin write shield in main.py, which refuses a mutating
        request whose Origin is not this app's own. That stops a hostile WEB
        PAGE from reaching this route even from an open tab. It does nothing
        against a bare local process: curl sends no Origin and no
        Sec-Fetch-Site header at all, and the shield's own fallback treats an
        absent header as non-browser tooling and lets it through - which is
        exactly the gap the launch token exists to close instead.
      * `body.confirm` below, checked against a phrase only the backend
        decides the true value of, so a frontend bug - a button wired wrong,
        a stale default in a form - cannot fire this with an empty or a
        near-miss string.

    Residual risk, stated rather than implied: anyone able to present the
    current launch token can wipe this vault with no passphrase at all. That
    is the SAME trust boundary vault_state.py already accepts for the
    unlocked vault - "any code running as this user" - extended to the locked
    one. It is real, and it is not new: someone with that access does not
    need this route either, since they could already delete every file it
    deletes by hand.

    WHY THIS REFUSES OUTRIGHT WHILE UNLOCKED

    A vault that CAN be unlocked does not need this door. Answering it anyway
    would make it a different and far more dangerous route - a way to destroy
    a conversation somebody is reading right now - and a confirmation phrase
    only guards against an ACCIDENT, never against someone reaching over a
    shoulder. So the unlocked check runs first, before the confirmation
    phrase is even read.

    WHAT THIS CANNOT UNDO

    Every file here goes through secure_delete.shred: bytes overwritten
    before the name is removed, which defeats an undelete tool and anything
    reading freed blocks through the same filesystem view. It is NOT a
    guarantee against the OS page file having held a copy of something once
    decrypted, a filesystem shadow copy taken before this ran, or an SSD's
    wear levelling leaving the original physical blocks readable to
    firmware-level recovery after the logical overwrite. Full-disk encryption
    is the only answer to that class, and it is the user's to enable, not
    this app's to fake.
    """
    # Before the lock, not inside it: a door that does not exist in this
    # build should not queue behind an unlock in flight.
    if not _reset_door_is_open():
        raise HTTPException(404, "vault_reset_unavailable")
    async with _vault_lock:
        # Checked FIRST, before the confirmation phrase is even read - see
        # the docstring above for why the order is load-bearing and not
        # cosmetic. Same lock as init/unlock/change-passphrase, so a reset
        # cannot interleave with one of them starting or finishing mid-way.
        if vault_state.is_unlocked():
            raise HTTPException(409, "vault_unlocked")
        if body.confirm.strip(_CONFIRM_STRIP_CHARS) != RESET_CONFIRMATION_PHRASE:
            raise HTTPException(422, "reset_confirmation_mismatch")
        result = await anyio.to_thread.run_sync(_reset_vault_sync)
    return result
