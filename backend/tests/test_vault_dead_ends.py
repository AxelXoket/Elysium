"""The states a vault could enter and never leave, with the data intact.

Three of them, all found in the same round, all the same shape: something
went wrong at a moment nobody planned for, the repair that would have fixed it
already existed, and the app refused to reach it. In each case the user's data
was fine, their passphrase was right, and every door said no.

  * K-06 - kdf.json holds a number scrypt will not take, so unlock RAISES.
    The route's next line calls recover_with_db, which survives that input and
    heals the file in one pass. It never runs, because a raise is not a None.
  * K-05 - a crash while rotating the passphrase leaves salt.bin.new and no
    salt.bin. recover_with_db reads either. The route gated on salt.bin alone
    and answered "this vault was never set up".
  * The write order in initialize. kdf.json was written after the two files
    that make is_initialized() true, so a crash between them left a vault that
    reports itself set up and derives a key that opens nothing.

Every test here builds the damaged state on disk and asks whether the app can
still get in. None of them mocks the crypto: the passphrase is real, the
derivation is real, and the database is a real encrypted one, because the
whole question is whether the REAL repair path is reachable.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

import config
import crypto
import database

PASSPHRASE = "correct horse battery staple"


def _vault_with_db(tmp_path: Path, monkeypatch) -> tuple[crypto.KeyVault, Path]:
    """A real vault, a real encrypted database, and the routes pointed at it."""
    db_path = tmp_path / "app.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(database, "DB_PATH", str(db_path))
    vault = crypto.KeyVault(tmp_path)
    key = vault.initialize(PASSPHRASE)
    import vault_state
    vault_state.set_key(key)
    try:
        database.init_db()
    finally:
        vault_state.clear_key()
    return vault, db_path


class TestParametersScryptWillNotTake:
    """K-06. One digit in a plain text file, and the vault is bricked."""

    #: 2**17 is what the app writes. 2**21 is one keystroke away, and it is
    #: over the ceiling scrypt can be called with on this platform - measured:
    #: the OverflowError is raised before a single byte is allocated, so this
    #: is not a memory attack, it is an unhandled exception.
    OVERSIZED = {"kdf": "scrypt", "v": 2, "n": 2**21, "r": 8, "p": 1}

    @pytest.mark.parametrize("params", [
        {"n": 2**21, "r": 8, "p": 1},     # over the platform ceiling
        {"n": 3, "r": 8, "p": 1},         # not a power of two
        {"n": 0, "r": 8, "p": 1},         # zero
        {"n": 2**17, "r": -1, "p": 1},    # negative
        {"n": 2**17, "r": 8, "p": 2**40},  # p alone blows the limit
    ])
    def test_a_file_scrypt_would_refuse_reads_as_the_legacy_parameters(
        self, tmp_path: Path, params: dict
    ) -> None:
        # Read as legacy rather than raising, because read_params promises
        # never to raise and half the recovery path leans on that promise.
        # Legacy is the same answer a missing file gives, and the vault
        # already survives a missing file.
        vault = crypto.KeyVault(tmp_path)
        vault.kdf_path.write_text(json.dumps(params), encoding="utf-8")
        assert vault.read_params() == crypto.KDF_LEGACY

    def test_a_usable_file_is_still_honoured(self, tmp_path: Path) -> None:
        # The discriminating half. A bounds check that refused everything
        # would satisfy the test above and quietly downgrade every vault in
        # the world to the legacy cost.
        vault = crypto.KeyVault(tmp_path)
        vault.write_params(dict(crypto.KDF_CURRENT))
        assert vault.read_params()["n"] == crypto.KDF_CURRENT["n"]

    def test_unlock_returns_none_rather_than_raising(
        self, tmp_path: Path
    ) -> None:
        """The contract the whole recovery path rests on.

        Measured before the fix: hashlib.scrypt raised OverflowError, it
        escaped unlock(), and the route turned it into a bare 500 - one line
        above the call that would have repaired the vault.
        """
        vault = crypto.KeyVault(tmp_path)
        vault.initialize(PASSPHRASE)
        vault.kdf_path.write_text(json.dumps(self.OVERSIZED), encoding="utf-8")

        assert vault.unlock(PASSPHRASE) is None

    def test_an_unreadable_identity_file_is_not_a_500_either(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The other half of the same contract, and the half still reachable.

        The bounds check above means bad parameters no longer reach scrypt, so
        the guard inside unlock() would be unprovable through that door - and
        an unprovable fix is one nobody can tell is still working. This is the
        door that stays open: salt.bin and verifier.bin are read straight from
        disk, and a file the OS will not hand over raises OSError with nothing
        to catch it.

        Recovery survives that too (its own read sits in except Exception), so
        the answer is the same: return None and let it try.
        """
        vault = crypto.KeyVault(tmp_path)
        vault.initialize(PASSPHRASE)
        real_read = Path.read_bytes

        def refuse(self):
            if Path(self).name == "verifier.bin":
                raise OSError(13, "permission denied")
            return real_read(self)

        monkeypatch.setattr(Path, "read_bytes", refuse)

        assert vault.unlock(PASSPHRASE) is None

    def test_the_route_heals_the_file_instead_of_answering_500(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import vault_state

        vault, _ = _vault_with_db(tmp_path, monkeypatch)
        vault.kdf_path.write_text(json.dumps(self.OVERSIZED), encoding="utf-8")
        vault_state.clear_key()

        response = client.post("/api/v1/vault/unlock",
                               json={"passphrase": PASSPHRASE})

        assert response.status_code == 200, response.text
        # Repaired, not merely tolerated. unlock() is the verifier path only -
        # it does not consult recovery - so it answering with a key proves the
        # working parameters were written back to disk.
        assert vault.unlock(PASSPHRASE) is not None, (
            "the vault opened but nothing wrote the working parameters back")


class TestARotationThatDiedBetweenTwoRenames:
    """K-05. The state recovery was written for, and the route refused."""

    def _half_rotated(self, vault: crypto.KeyVault) -> None:
        """What change_passphrase leaves when it dies at the wrong instant.

        It shelves salt.bin BEFORE verifier.bin, so a crash between those two
        renames leaves no salt.bin at all - and the staged trio, which is the
        correct identity for a database that has already been re-keyed.
        """
        assert vault.salt_path.with_name("salt.bin.new").exists()
        vault.salt_path.unlink()

    def _stage_a_rotation(self, vault: crypto.KeyVault, new_passphrase: str,
                          key: bytes) -> None:
        salt = crypto.new_salt()
        params = dict(crypto.KDF_CURRENT)
        new_key = crypto.derive_key(new_passphrase, salt, params)
        vault.salt_path.with_name("salt.bin.new").write_bytes(salt)
        vault.verifier_path.with_name("verifier.bin.new").write_bytes(
            crypto.make_verifier(new_key))
        vault.kdf_path.with_name("kdf.json.new").write_text(
            json.dumps(params), encoding="utf-8")
        database.rekey_db(new_key, key)

    def test_the_route_no_longer_calls_it_uninitialised(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import vault_state

        vault, _ = _vault_with_db(tmp_path, monkeypatch)
        key = vault.unlock(PASSPHRASE)
        assert key is not None
        vault_state.set_key(key)
        try:
            self._stage_a_rotation(vault, "a whole new sentence entirely", key)
        finally:
            vault_state.clear_key()
        self._half_rotated(vault)

        response = client.post(
            "/api/v1/vault/unlock",
            json={"passphrase": "a whole new sentence entirely"})

        assert response.status_code == 200, (
            f"{response.status_code}: the vault is intact, the passphrase is "
            f"right, and recovery reads salt.bin.new - but the gate asked "
            f"only about salt.bin. {response.text}")

    def test_the_gate_still_refuses_a_vault_with_no_salt_at_all(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The discriminating half. Widening the gate must not turn it into no
        # gate: with neither salt on disk there is genuinely nothing to derive
        # from, and 409 is the honest answer.
        import vault_state

        vault, _ = _vault_with_db(tmp_path, monkeypatch)
        vault_state.clear_key()
        vault.salt_path.unlink()

        response = client.post("/api/v1/vault/unlock",
                               json={"passphrase": PASSPHRASE})

        assert response.status_code == 409
        assert response.json()["detail"] == "vault_not_initialized"


    def test_status_offers_the_unlock_screen_rather_than_setup(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The half that made the other half unreachable.

        VaultGate branches on `initialized` before `unlocked`, so a vault
        answering "not initialized" is shown SET UP A PASSPHRASE and is never
        offered the unlock box - while /vault/init refuses it, because the
        database is encrypted. Widening the unlock gate alone would have fixed
        a door with no corridor leading to it.
        """
        import vault_state

        vault, _ = _vault_with_db(tmp_path, monkeypatch)
        key = vault.unlock(PASSPHRASE)
        assert key is not None
        vault_state.set_key(key)
        try:
            self._stage_a_rotation(vault, "a whole new sentence entirely", key)
        finally:
            vault_state.clear_key()
        self._half_rotated(vault)

        body = client.get("/api/v1/vault/status").json()

        assert body["initialized"] is True, body
        assert body["unlocked"] is False, body

    def test_can_recover_sees_the_staged_salt_and_can_derive_does_not(
        self, tmp_path: Path
    ) -> None:
        # The two predicates, side by side, so the difference between them is
        # written down rather than implied by a route.
        vault = crypto.KeyVault(tmp_path)
        vault.salt_path.with_name("salt.bin.new").write_bytes(b"x" * 16)

        assert vault.can_derive() is False
        assert vault.can_recover() is True


class TestTheOrderInWhichAVaultIsBornModelled:
    """The write order in initialize, which no test could see.

    Measured: reordering those three writes moved not one test in the suite.
    So this is written as an ordering assertion rather than an end-state one -
    the end state is identical either way, and the end state was never the
    problem.
    """

    def test_the_parameters_land_before_the_vault_calls_itself_initialised(
        self, tmp_path: Path
    ) -> None:
        """is_initialized() is salt AND verifier. Both must come after kdf.

        With kdf.json written last there was a window in which the vault WAS
        initialised and its cost parameters were not recorded: read_params
        falls back to legacy, unlock derives a key the verifier rejects, and
        /vault/init refuses because the vault already exists. Recovery cannot
        help either - there is no database yet for it to validate against.
        A dead end, from a crash between two adjacent lines.
        """
        vault = crypto.KeyVault(tmp_path)
        order: list[str] = []
        real_write_bytes = Path.write_bytes
        real_write_text = Path.write_text

        def note_bytes(self, data):
            order.append(Path(self).name)
            return real_write_bytes(self, data)

        def note_text(self, data, *args, **kwargs):
            order.append(Path(self).name)
            return real_write_text(self, data, *args, **kwargs)

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(Path, "write_bytes", note_bytes)
            patch.setattr(Path, "write_text", note_text)
            vault.initialize(PASSPHRASE)

        assert order.index("kdf.json") < order.index("salt.bin"), order
        assert order.index("kdf.json") < order.index("verifier.bin"), order

    def test_a_crash_before_the_identity_lands_leaves_a_re_runnable_vault(
        self, tmp_path: Path
    ) -> None:
        # The property that ordering buys, stated as behaviour: parameters
        # alone are inert, so the vault does not yet claim to exist and
        # /vault/init can simply run again.
        vault = crypto.KeyVault(tmp_path)
        vault.write_params(dict(crypto.KDF_CURRENT))

        assert vault.is_initialized() is False
        key = vault.initialize(PASSPHRASE)
        assert vault.unlock(PASSPHRASE) == key

    def test_re_initialising_shelves_the_parameters_with_the_salt(
        self, tmp_path: Path
    ) -> None:
        # kdf.json describes the salt beside it. Shelving one without the
        # other left a backup nobody could derive from.
        vault = crypto.KeyVault(tmp_path)
        vault.initialize(PASSPHRASE)
        vault.initialize("an entirely different sentence")

        shelved = sorted(p.name for p in tmp_path.glob("kdf.json.bak-*"))
        assert shelved, sorted(p.name for p in tmp_path.iterdir())


class TestTheOneWayToProveYouKnowThePassphrase:
    """Found by an adversarial pass, reproduced live, recorded nowhere.

    /vault/unlock short-circuited on `is_unlocked()` and returned ok:true
    without reading the passphrase at all. So the app's only "prove you know
    it" primitive answered yes to anything, whenever the vault happened to be
    open. Its sibling route had already closed the identical hole - and this
    suite even routes AROUND the branch in one place (test_vault_honesty.py
    clears the key first) rather than asserting on it.
    """

    def test_a_wrong_passphrase_is_refused_even_while_the_vault_is_open(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import vault_state

        vault, _ = _vault_with_db(tmp_path, monkeypatch)
        key = vault.unlock(PASSPHRASE)
        assert key is not None
        vault_state.set_key(key)
        assert vault_state.is_unlocked()

        response = client.post("/api/v1/vault/unlock",
                               json={"passphrase": "not the passphrase 123"})

        assert response.status_code == 401, response.text
        assert response.json()["detail"] == "wrong_passphrase"
        # And it did not lock anybody out by refusing: the vault is still open.
        assert vault_state.is_unlocked()

    def test_the_right_passphrase_still_answers_ok_while_open(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The discriminating half. A branch that refused everything would
        # satisfy the test above and break the idempotent unlock the frontend
        # relies on after a reconnect.
        import vault_state

        vault, _ = _vault_with_db(tmp_path, monkeypatch)
        key = vault.unlock(PASSPHRASE)
        assert key is not None
        vault_state.set_key(key)

        response = client.post("/api/v1/vault/unlock",
                               json={"passphrase": PASSPHRASE})

        assert response.status_code == 200, response.text
        assert response.json() == {"ok": True, "migrated": False,
                                   "backup": None}


class TestAWrongPassphraseIsMadeToWait:
    """K-30, in the shape the measurement argued for.

    A graduated lockout was the plan and was dropped. The reason is worth
    keeping: no counter this app can persist is out of reach of somebody
    holding the data folder, so a ladder does nothing against the attacker the
    threat model names - the one who copies the folder and guesses somewhere
    this code never runs. It would only have slowed the person at this
    keyboard, which a flat delay does too, without ever locking an honest user
    out of their own vault and without needing a clock that survives a reboot.

    So: the delay is real, it is on both doors, and it must not hold the rest
    of the app while it runs.
    """

    def test_the_refusal_is_not_instant(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import vault_state
        import routers.vault as vault_router

        _vault_with_db(tmp_path, monkeypatch)
        vault_state.clear_key()
        monkeypatch.setattr(vault_router, "WRONG_PASSPHRASE_DELAY_S", 0.4)

        started = time.monotonic()
        response = client.post("/api/v1/vault/unlock",
                               json={"passphrase": "not the passphrase 123"})
        waited = time.monotonic() - started

        assert response.status_code == 401
        assert waited >= 0.4, f"answered in {waited:.2f}s - no delay at all"

    def test_the_right_passphrase_is_not_delayed(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The discriminating half. A delay on the success path would be a tax
        # on every launch, paid by the only person it cannot protect against.
        import vault_state
        import routers.vault as vault_router

        _vault_with_db(tmp_path, monkeypatch)
        vault_state.clear_key()
        monkeypatch.setattr(vault_router, "WRONG_PASSPHRASE_DELAY_S", 5.0)

        started = time.monotonic()
        response = client.post("/api/v1/vault/unlock",
                               json={"passphrase": PASSPHRASE})
        waited = time.monotonic() - started

        assert response.status_code == 200, response.text
        assert waited < 5.0, f"the correct passphrase waited {waited:.2f}s"

    @pytest.mark.anyio
    async def test_the_wait_does_not_hold_the_rest_of_the_vault(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The reason the delay sits outside _vault_lock.

        Sleeping inside it would hold every other vault route - including
        /vault/status, which is the one route the lock screen polls and the
        one main.py deliberately keeps out of the idle clock so that it can
        always answer. A typo would have frozen the lock screen.
        """
        import vault_state
        import routers.vault as vault_router

        _vault_with_db(tmp_path, monkeypatch)
        vault_state.clear_key()
        monkeypatch.setattr(vault_router, "WRONG_PASSPHRASE_DELAY_S", 1.0)

        import httpx
        import main
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://testserver") as ac:
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
            f"status waited {answered_in:.2f}s behind somebody else's typo")


class TestTheDatabaseIsOnlyHalfTheVault:
    """K-52. `crypto.py` says the encrypted database is the final authority on
    whether a key is right and the verifier is only a convenience. That was
    true in exactly one direction: recovery ran when the verifier said NO and
    never when it said YES.

    So the identity files and the database could come from different moments -
    restore a backup of app.db without the salt and verifier beside it, or let
    a synced folder put back an older copy - and the CORRECT current passphrase
    got a 500 forever, the old one got a 401, and /vault/status reported every
    honesty field clean. Three answers and not one of them said the true
    thing, which is that these two files are not the same vault.

    Unlike K-05 and K-06, this is not a door in FRONT of recovery. It is the
    success branch itself.
    """

    NEW_PASSPHRASE = "a different correct horse battery staple"

    def _database_from_before_the_change(
        self, client, tmp_path: Path, monkeypatch
    ) -> tuple[Path, bytes]:
        vault, db_path = _vault_with_db(tmp_path, monkeypatch)
        import vault_state
        vault_state.clear_key()

        assert client.post("/api/v1/vault/unlock",
                           json={"passphrase": PASSPHRASE}).status_code == 200
        era_one = db_path.read_bytes()

        assert client.post("/api/v1/vault/change-passphrase", json={
            "old_passphrase": PASSPHRASE,
            "new_passphrase": self.NEW_PASSPHRASE,
        }).status_code == 200
        client.post("/api/v1/vault/lock")

        # The restore. Nothing exotic: a file put back from a backup taken
        # before the passphrase was changed.
        db_path.write_bytes(era_one)
        return db_path, era_one

    def test_the_right_passphrase_is_told_what_is_actually_wrong(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db_path, _ = self._database_from_before_the_change(
            client, tmp_path, monkeypatch)

        response = client.post("/api/v1/vault/unlock",
                               json={"passphrase": self.NEW_PASSPHRASE})

        # NOT 500 (which said the disk was full or the file was busy) and NOT
        # 401 (which said the user had typed it wrong). Both sent people off
        # to fix something that was not broken.
        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "vault_identity_mismatch"

    def test_the_refusal_destroys_nothing(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The restored database is the user's only copy of that era. A route
        that diagnosed the mismatch and then tidied one side away would be a
        worse defect than the silence it replaced."""
        db_path, era_one = self._database_from_before_the_change(
            client, tmp_path, monkeypatch)

        client.post("/api/v1/vault/unlock",
                    json={"passphrase": self.NEW_PASSPHRASE})

        assert db_path.read_bytes() == era_one
        assert (tmp_path / "verifier.bin").is_file()
        assert (tmp_path / "salt.bin").is_file()

    def test_a_matching_pair_still_opens_normally(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Positive control. The new check runs on EVERY unlock, so an ordinary
        one has to be proved still ordinary - otherwise the guard above could
        be a guard that never lets anybody in."""
        _vault_with_db(tmp_path, monkeypatch)
        import vault_state
        vault_state.clear_key()

        response = client.post("/api/v1/vault/unlock",
                               json={"passphrase": PASSPHRASE})
        assert response.status_code == 200, response.text

    def _identity_only(self, tmp_path: Path, monkeypatch) -> Path:
        """Identity files, no database yet - the state right after setup."""
        import vault_state

        db_path = tmp_path / "app.db"
        monkeypatch.setattr(config, "DB_PATH", str(db_path))
        monkeypatch.setattr(database, "DB_PATH", str(db_path))
        crypto.KeyVault(tmp_path).initialize(PASSPHRASE)
        vault_state.clear_key()
        return db_path

    def test_a_database_that_is_not_there_yet_has_no_opinion_about_keys(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Only an ENCRYPTED file can answer "is this the right key". Asking an
        absent one turns the very first unlock after setup into a refusal."""
        db_path = self._identity_only(tmp_path, monkeypatch)
        assert not db_path.exists()

        response = client.post("/api/v1/vault/unlock",
                               json={"passphrase": PASSPHRASE})
        assert response.status_code == 200, response.text

    def test_a_still_plaintext_database_has_no_opinion_either(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The pre-vault migration path. A plaintext app.db opens under NO
        key, so a check that did not ask what kind of file this is would refuse
        the one unlock whose whole job is to encrypt it."""
        import sqlite3

        db_path = self._identity_only(tmp_path, monkeypatch)
        con = sqlite3.connect(str(db_path))
        con.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
        con.commit()
        con.close()
        assert database.classify_db_file() == database.DB_PLAINTEXT

        response = client.post("/api/v1/vault/unlock",
                               json={"passphrase": PASSPHRASE})
        assert response.status_code == 200, response.text
        assert response.json()["migrated"] is True


class TestTheCopyARotationLeavesWhenItIsKilled:
    """K-44. Both rotations copy the whole database to `app.db.rekey.bak-<ts>`
    before touching it and remove it afterwards. Kill the process in that
    window and the copy stays - a complete vault, in none of the three remnant
    families /vault/status reported, removed by no route, and on the KDF path
    with no HTTP answer to carry the news either.

    Two shapes, and they deserve opposite treatment. One this vault can open
    is a redundant duplicate of the live database and there is nothing to
    decide about it. One it cannot open was taken before a rotation that DID
    finish, so it is readable only with the passphrase that was revoked - and
    deleting a file this app cannot read is the single thing every other
    discard path here refuses to do.
    """

    def _rotation_leftover(self, tmp_path: Path, monkeypatch, readable: bool):
        vault, db_path = _vault_with_db(tmp_path, monkeypatch)
        leftover = db_path.with_name(db_path.name + ".rekey.bak-1700000000")
        leftover.write_bytes(
            db_path.read_bytes() if readable
            else b"SQLite format 3" + bytes(4096))
        import vault_state
        vault_state.clear_key()
        return leftover

    def test_one_this_vault_can_read_is_swept_at_unlock(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        leftover = self._rotation_leftover(tmp_path, monkeypatch, readable=True)

        assert client.post("/api/v1/vault/unlock",
                           json={"passphrase": PASSPHRASE}).status_code == 200

        assert not leftover.exists()
        assert client.get(
            "/api/v1/vault/status").json()["rotation_backups"] == []

    def test_one_it_cannot_read_is_kept_and_named(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        leftover = self._rotation_leftover(tmp_path, monkeypatch, readable=False)

        assert client.post("/api/v1/vault/unlock",
                           json={"passphrase": PASSPHRASE}).status_code == 200

        # Still there - it may be the only copy of chats this vault cannot
        # show, and nothing here gets to guess about that.
        assert leftover.exists()
        # And SAYABLE. Surviving silently is the whole defect: before this
        # field the only trace of a full second vault was a log line.
        assert client.get("/api/v1/vault/status").json()[
            "rotation_backups"] == [leftover.name]

    def test_the_sweep_does_not_touch_the_live_database(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """It works by name, next to the file it must never take."""
        leftover = self._rotation_leftover(tmp_path, monkeypatch, readable=True)
        db_path = Path(config.DB_PATH)
        before = db_path.read_bytes()

        client.post("/api/v1/vault/unlock", json={"passphrase": PASSPHRASE})

        assert db_path.read_bytes() == before
        assert not leftover.exists()
