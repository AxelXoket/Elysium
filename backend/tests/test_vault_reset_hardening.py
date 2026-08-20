"""Two CONFIRMED defects an adversarial audit reproduced against a real
server and a real throwaway vault, plus the smaller ones found alongside
them.

Defect 1 (CRITICAL): a failed database wipe used to still destroy the
identity files, bricking a vault that was otherwise perfectly intact.
Defect 2 (HIGH): /vault/reset could acquire _vault_lock in the gap that used
to sit between /vault/unlock verifying a passphrase and installing the key,
see is_unlocked() still False, and wipe the vault - which /vault/unlock's own
bootstrap then silently rebuilt from the very passphrase it was accepting.

Every test here builds real files at the real paths this app writes to (a
real KeyVault, a real encrypted database), the same discipline
test_vault_reset.py already follows, because the question in both defects is
whether the route survives what Windows actually does to a file it cannot
remove - not a fixture invented for the test.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

import config
import crypto
import database
import secure_delete
import vault_state
from routers.vault import (
    RESET_CONFIRM_MAX_LEN,
    RESET_CONFIRMATION_PHRASE,
)

from test_vault_reset import (  # noqa: F401  (reused fixtures/helpers)
    PASSPHRASE as REAL_LOCKED_VAULT_PASSPHRASE,
    _populate_every_artefact,
    _real_locked_vault,
    legacy_keyring,
)

# _real_locked_vault (imported above) always initializes under
# test_vault_reset's OWN passphrase constant, not this one - every test that
# uses it authenticates with REAL_LOCKED_VAULT_PASSPHRASE. This one is only
# for the tests below that build their own vault from scratch.
PASSPHRASE = "correct horse battery staple hardening"


# ---------------------------------------------------------------------------
# Defect 1: a failed database wipe must not touch the identity files
# ---------------------------------------------------------------------------

class TestAFailedDatabaseWipeDoesNotBrickTheVault:

    def test_ground_an_ordinary_reset_still_removes_everything(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """GROUND. Nothing is blocking anything here - this is what a clean
        run looks like, so the hardlink test below is provably testing a
        DIFFERENT path through the route, not a route that never wipes."""
        db_path, _key = _real_locked_vault(tmp_path, monkeypatch)
        salt_path = tmp_path / "salt.bin"

        response = client.post("/api/v1/vault/reset",
                               json={"confirm": RESET_CONFIRMATION_PHRASE})

        assert response.status_code == 200, response.text
        assert response.json() == {"ok": True, "left": []}
        assert not db_path.exists()
        assert not salt_path.exists()

    def test_a_hardlinked_database_stops_the_sweep_before_identity_files(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The repro. A hardlink makes secure_delete.is_shared() refuse to
        overwrite app.db (it would be overwriting the victim file's bytes
        too), so the database survives - exactly the ordinary situation
        antivirus, a sync client or a second running instance can cause
        without any hardlink at all. Before the fix: the sweep did not
        notice and destroyed salt.bin/verifier.bin/kdf.json anyway, so the
        surviving database could never be opened again by any passphrase.
        """
        db_path, key = _real_locked_vault(tmp_path, monkeypatch)
        salt_path = tmp_path / "salt.bin"
        verifier_path = tmp_path / "verifier.bin"
        kdf_path = tmp_path / "kdf.json"
        assert salt_path.exists() and verifier_path.exists()
        assert kdf_path.exists()
        db_bytes_before = db_path.read_bytes()

        # A SECOND name for app.db's own inode, so shred() must refuse to
        # overwrite app.db (it would be overwriting this alias's bytes too) -
        # while the database's REAL content is what is being protected, not
        # some unrelated decoy. is_shared() cannot tell "somebody made a
        # hardlink on purpose" from "antivirus/a sync client/a second
        # instance is holding this open"; either way app.db must survive.
        alias = tmp_path / "someones_notes.txt"
        os.link(db_path, alias)

        try:
            response = client.post(
                "/api/v1/vault/reset",
                json={"confirm": RESET_CONFIRMATION_PHRASE})

            assert response.status_code == 200, response.text
            body = response.json()
            assert body["ok"] is False
            assert db_path.name in body["left"]

            # The database really is untouched - not merely present, but
            # byte-identical, proving shred() refused rather than partially
            # overwriting before giving up.
            assert db_path.exists()
            assert db_path.read_bytes() == db_bytes_before

            # THE ACTUAL DEFECT: identity files must survive alongside it.
            assert salt_path.exists(), "salt.bin was destroyed with app.db still on disk"
            assert verifier_path.exists(), "verifier.bin was destroyed with app.db still on disk"
            assert kdf_path.exists(), "kdf.json was destroyed with app.db still on disk"

            # And the vault must still be OPENABLE with the original
            # passphrase - not just "files exist", but a working vault.
            reopened = crypto.KeyVault(tmp_path).unlock(REAL_LOCKED_VAULT_PASSPHRASE)
            assert reopened is not None, "the original passphrase no longer opens the vault"
            assert database.check_key(reopened, str(db_path))
        finally:
            alias.unlink(missing_ok=True)

    #: The labels _populate_every_artefact uses for the identity family
    #: itself (staged .new files a crashed rotation left, and superseded
    #: .bak- copies a completed one shelved). These are the ONLY artefacts
    #: this route holds back when app.db survives - everything else in
    #: `ground` is unrelated to app.db's key and must still go.
    _IDENTITY_FAMILY_LABELS = frozenset({
        "salt.bin.new", "salt.bin.bak",
        "verifier.bin.new", "verifier.bin.bak",
        "kdf.json.new", "kdf.json.bak",
    })

    def test_other_families_are_still_swept_when_the_database_survives(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        legacy_keyring,
    ) -> None:
        """The second half of the decision: only the identity files are held
        back. Everything else - none of it is the recipe for app.db's key -
        keeps going, because leaving it standing protects nobody."""
        db_path, _key = _real_locked_vault(tmp_path, monkeypatch)
        ground = _populate_every_artefact(tmp_path, db_path, legacy_keyring)

        alias = tmp_path / "someones_notes.txt"
        os.link(db_path, alias)

        try:
            response = client.post(
                "/api/v1/vault/reset",
                json={"confirm": RESET_CONFIRMATION_PHRASE})
            assert response.status_code == 200, response.text

            for label, path in ground.items():
                if label in self._IDENTITY_FAMILY_LABELS:
                    assert path.exists(), (
                        f"{label} was destroyed even though app.db survived")
                else:
                    assert not path.exists(), (
                        f"{label} survived even though it is unrelated to "
                        "app.db's key")
            assert legacy_keyring == {}
        finally:
            alias.unlink(missing_ok=True)

    def test_a_stuck_sidecar_alone_does_not_hold_back_identity_files(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The discriminating half of the db_path.exists() check: a WAL
        sidecar that cannot be removed, with the MAIN file gone, is not the
        coupling the guard exists to break - a dangling -wal with no app.db
        behind it opens nothing, so there is nothing left for the identity
        files to be the only way back into."""
        db_path, _key = _real_locked_vault(tmp_path, monkeypatch)
        wal = db_path.with_name(db_path.name + "-wal")
        wal.write_bytes(b"a page nobody checkpointed")
        # The SIDECAR is hardlinked, not app.db itself - a second name on
        # the WAL's own inode, so shred() refuses the wal but has nothing to
        # say about app.db.
        alias = tmp_path / "someones_notes.txt"
        os.link(wal, alias)

        try:
            response = client.post(
                "/api/v1/vault/reset",
                json={"confirm": RESET_CONFIRMATION_PHRASE})

            assert response.status_code == 200, response.text
            body = response.json()
            assert body["ok"] is False
            assert wal.name in body["left"]
            assert not db_path.exists(), "the main database should still be gone"
            assert not (tmp_path / "salt.bin").exists(), (
                "identity files were held back even though app.db is gone")
        finally:
            alias.unlink(missing_ok=True)
            wal.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Defect 2: a reset must not be able to interleave with an unlock in flight
# ---------------------------------------------------------------------------

class TestResetCannotInterleaveWithAnUnlockInFlight:

    def _vault_with_real_data(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> tuple[Path, bytes]:
        db_path = tmp_path / "app.db"
        monkeypatch.setattr(config, "DB_PATH", str(db_path))
        monkeypatch.setattr(database, "DB_PATH", str(db_path))
        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        monkeypatch.setattr(config, "UPLOADS_DIR", str(tmp_path / "uploads"))
        monkeypatch.setattr(config, "TTS_CACHE_DIR", str(tmp_path / "voice_cache"))
        monkeypatch.setattr(config, "TTS_REFS_DIR", str(tmp_path / "voice_refs"))
        vault = crypto.KeyVault(tmp_path)
        key = vault.initialize(PASSPHRASE)
        vault_state.set_key(key)
        try:
            database.init_db()
            with database.get_db() as con:
                con.execute(
                    "INSERT INTO characters (name, description, first_mes) "
                    "VALUES (?, ?, ?)",
                    ("Survivor", "must still be here afterward", "hi"),
                )
                con.commit()
        finally:
            vault_state.clear_key()
        return db_path, key

    @pytest.mark.anyio
    async def test_a_reset_arriving_mid_unlock_does_not_resurrect_the_vault(
        self, anyio_backend, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The repro. KeyVault.unlock is slowed down so the request is
        guaranteed to still hold _vault_lock (inside the anyio.to_thread hop)
        when the reset request arrives and queues behind it - the exact
        moment the audit's real-server timing measurements land in. Before
        the fix, releasing the lock between verifying the passphrase and
        installing the key let the queued reset run in that gap, see
        is_unlocked() == False, and wipe everything; unlock's own bootstrap
        then rebuilt an EMPTY vault under the same passphrase, so the request
        answered 200 and the data was gone without any error anywhere.
        """
        db_path, _key = self._vault_with_real_data(tmp_path, monkeypatch)
        salt_before = (tmp_path / "salt.bin").read_bytes()
        db_before = db_path.read_bytes()

        real_unlock = crypto.KeyVault.unlock

        def slow_unlock(self, passphrase):
            # Runs OFF the event loop (anyio.to_thread.run_sync), so this
            # does not freeze the loop - it just guarantees the unlock
            # request is still inside its critical section when reset shows
            # up, the same way real scrypt cost gave the audit its window.
            time.sleep(0.2)
            return real_unlock(self, passphrase)

        monkeypatch.setattr(crypto.KeyVault, "unlock", slow_unlock)

        import httpx
        import main
        transport = httpx.ASGITransport(app=main.app)
        try:
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver",
            ) as ac:
                unlock_task = asyncio.create_task(ac.post(
                    "/api/v1/vault/unlock", json={"passphrase": PASSPHRASE}))
                # Let the unlock request acquire _vault_lock and enter the
                # slowed-down unlock() call before reset is sent.
                await asyncio.sleep(0.05)
                reset_task = asyncio.create_task(ac.post(
                    "/api/v1/vault/reset",
                    json={"confirm": RESET_CONFIRMATION_PHRASE}))
                # Give reset time to reach `await _vault_lock.acquire()` and
                # register as a queued waiter while unlock still holds it.
                await asyncio.sleep(0.05)

                unlock_resp = await unlock_task
                reset_resp = await reset_task

            assert unlock_resp.status_code == 200, unlock_resp.text
            # Reset must see the vault already unlocked and refuse outright -
            # not race its way into a locked-looking gap.
            assert reset_resp.status_code == 409, reset_resp.text
            assert reset_resp.json()["detail"] == "vault_unlocked"

            # The decisive check: nothing was destroyed. A resurrection
            # would have left DIFFERENT bytes (a freshly initialized empty
            # vault) even though both requests "succeeded" from their own
            # point of view.
            assert (tmp_path / "salt.bin").read_bytes() == salt_before
            assert db_path.read_bytes() == db_before

            key = crypto.KeyVault(tmp_path).unlock(PASSPHRASE)
            assert key is not None

            # The vault is still unlocked - the successful unlock call set
            # vault_state's process-global key - so read straight through
            # THAT session rather than deriving a fresh one, proving the
            # data written before the race is still reachable through it.
            with database.get_db() as con:
                rows = con.execute(
                    "SELECT name FROM characters WHERE name = 'Survivor'"
                ).fetchall()
            assert len(rows) == 1, "the character written before the race is gone"
        finally:
            vault_state.clear_key()

    @pytest.mark.anyio
    async def test_the_wrong_passphrase_delay_still_runs_outside_the_lock(
        self, anyio_backend, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The property the two-block split existed to protect, now that it
        is one block: /vault/status must still answer instantly while a
        wrong-passphrase request is sleeping out its K-30 delay."""
        import routers.vault as vault_router

        self._vault_with_real_data(tmp_path, monkeypatch)
        monkeypatch.setattr(vault_router, "WRONG_PASSPHRASE_DELAY_S", 1.0)

        import httpx
        import main
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver",
        ) as ac:
            refusal = asyncio.create_task(ac.post(
                "/api/v1/vault/unlock",
                json={"passphrase": "not the passphrase 123"}))
            await asyncio.sleep(0.3)
            started = time.monotonic()
            status = await ac.get("/api/v1/vault/status")
            answered_in = time.monotonic() - started
            assert (await refusal).status_code == 401

        assert status.status_code == 200
        assert answered_in < 0.5, (
            f"status waited {answered_in:.2f}s behind a typo's sleep")


# ---------------------------------------------------------------------------
# Smaller: the confirmation phrase must actually be strict
# ---------------------------------------------------------------------------

class TestTheConfirmationPhraseForgivesOnlyPlainWhitespace:

    @pytest.mark.parametrize("padding,label", [
        (" ", "NBSP"),
        ("　", "ideographic space"),
        ("\x0b", "vertical tab"),
        ("\x0c", "form feed"),
    ])
    def test_unicode_or_control_padding_is_refused(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        padding: str, label: str,
    ) -> None:
        db_path, _key = _real_locked_vault(tmp_path, monkeypatch)

        response = client.post(
            "/api/v1/vault/reset",
            json={"confirm": f"{padding}{RESET_CONFIRMATION_PHRASE}{padding}"})

        assert response.status_code == 422, f"{label} padding was forgiven"
        assert response.json()["detail"] == "reset_confirmation_mismatch"
        assert db_path.exists()

    def test_plain_whitespace_is_still_forgiven(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The discriminating half: tightening the check must not also
        break the one leniency the route is supposed to keep."""
        db_path, _key = _real_locked_vault(tmp_path, monkeypatch)

        response = client.post(
            "/api/v1/vault/reset",
            json={"confirm": f" \t {RESET_CONFIRMATION_PHRASE}\r\n"})

        assert response.status_code == 200, response.text
        assert not db_path.exists()

    def test_a_huge_body_is_rejected_before_it_is_even_stripped(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        db_path, _key = _real_locked_vault(tmp_path, monkeypatch)
        huge = " " * (RESET_CONFIRM_MAX_LEN + 1) + RESET_CONFIRMATION_PHRASE

        response = client.post("/api/v1/vault/reset", json={"confirm": huge})

        assert response.status_code == 422, response.text
        assert db_path.exists()


# ---------------------------------------------------------------------------
# Smaller: the orphan glob's own journal sidecars must not survive either
# ---------------------------------------------------------------------------

class TestOrphanedEncTmpSidecarsAreSwept:

    def test_enc_tmp_wal_and_shm_are_removed(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """ATTACH can leave app.db.enc-tmp-wal/-shm behind. orphaned_enc_tmp_
        paths() filters them out on purpose FOR READERS THAT NEED A KEY to
        decide what is safe - reset has no key and needs no such filter, so
        it must sweep them directly instead of inheriting a filter built for
        someone else's safety property."""
        db_path, _key = _real_locked_vault(tmp_path, monkeypatch)
        wal = db_path.with_name(db_path.name + ".enc-tmp-wal")
        shm = db_path.with_name(db_path.name + ".enc-tmp-shm")
        wal.write_bytes(b"a journal page ATTACH left behind")
        shm.write_bytes(b"shared memory index")

        response = client.post("/api/v1/vault/reset",
                               json={"confirm": RESET_CONFIRMATION_PHRASE})

        assert response.status_code == 200, response.text
        assert response.json() == {"ok": True, "left": []}
        assert not wal.exists(), "app.db.enc-tmp-wal survived the reset"
        assert not shm.exists(), "app.db.enc-tmp-shm survived the reset"


# ---------------------------------------------------------------------------
# Smaller: elysium.log / elysium.log.1 / port - the docstring's own promise
# ---------------------------------------------------------------------------

class TestRuntimeFilesLeaveNoTrace:

    def test_log_and_port_files_are_removed(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """/vault/reset's own docstring says DATA_DIR is left as if Elysium
        had never run. elysium.log records chat ids, model ids and session
        timestamps; `port` is a stray but the same claim covers it."""
        db_path, _key = _real_locked_vault(tmp_path, monkeypatch)
        log = tmp_path / "elysium.log"
        log_rotated = tmp_path / "elysium.log.1"
        port = tmp_path / "port"
        log.write_text("a session that used model X on chat Y", encoding="utf-8")
        log_rotated.write_text("an earlier session", encoding="utf-8")
        port.write_text("54321", encoding="utf-8")

        response = client.post("/api/v1/vault/reset",
                               json={"confirm": RESET_CONFIRMATION_PHRASE})

        assert response.status_code == 200, response.text
        assert response.json() == {"ok": True, "left": []}
        assert not log.exists()
        assert not log_rotated.exists()
        assert not port.exists()


class TestTheLogIsShreddableWhileThisProcessHoldsItOpen:
    """The condition the shipped build is always in, and no test was in.

    `elysium.log` exists only on a frozen build (`run_app._setup_frozen_logging`
    returns early otherwise), so the ONLY configuration a user ever runs is the
    one where this process has a RotatingFileHandler open on the file the reset
    is about to shred. Every test wrote the file with `write_text` and closed
    it, which is the one configuration where the problem cannot happen.

    Measured before the fix: `secure_delete.discard` overwrote the contents and
    then failed to unlink, because CPython opens without FILE_SHARE_DELETE. So
    every reset in the shipped app answered `left: ["elysium.log"]` while the
    screen, the README and SECURITY.md all said the log was destroyed.
    """

    def _attach(self, path: Path):
        import logging
        from logging.handlers import RotatingFileHandler

        handler = RotatingFileHandler(str(path), maxBytes=512_000,
                                      backupCount=1, encoding="utf-8")
        logger = logging.getLogger("elysium.test.reset")
        logger.addHandler(handler)
        logger.warning("chat_id=%d", 7)
        handler.flush()
        return logger, handler

    def test_a_held_open_log_is_still_removed(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        db_path, _key = _real_locked_vault(tmp_path, monkeypatch)
        log = tmp_path / "elysium.log"
        logger, handler = self._attach(log)
        try:
            # GROUND: the handle is real and the shred genuinely cannot
            # unlink through it. Without this the test could pass against a
            # platform where the whole problem does not exist.
            assert log.exists()
            assert secure_delete.discard(log) is False, (
                "an open handler no longer blocks unlink, so this test is "
                "measuring nothing - check the platform before deleting it")
            logger.warning("chat_id=%d", 7)
            handler.flush()

            resp = client.post("/api/v1/vault/reset",
                               json={"confirm": RESET_CONFIRMATION_PHRASE})

            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "elysium.log" not in body["left"], (
                "the reset reported the log as left behind; the screen, the "
                "README and SECURITY.md all promise it is destroyed")
            assert not log.exists(), "the log survived a reset"
        finally:
            logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass

    def test_logging_still_works_afterwards(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Positive control. Detaching handlers to win the unlink must not
        leave the app unable to log, or a reset trades one silent failure for
        another."""
        import logging

        db_path, _key = _real_locked_vault(tmp_path, monkeypatch)
        log = tmp_path / "elysium.log"
        logger, handler = self._attach(log)
        try:
            client.post("/api/v1/vault/reset",
                        json={"confirm": RESET_CONFIRMATION_PHRASE})
            logging.getLogger("elysium.test.reset").warning("still alive")
        finally:
            logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass
