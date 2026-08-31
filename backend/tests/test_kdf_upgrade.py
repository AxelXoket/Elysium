"""The cost of guessing a passphrase, and the fact that it was frozen.

scrypt N=2^15 was chosen once and became unchangeable, because raising it
changes every derived key: the moment the constant moved, every existing vault
would have stopped opening and reported itself as a wrong passphrase. A cost
parameter nobody can raise is a cost parameter frozen at whatever seemed
enough the year it was written, and this one was four times under OWASP's
current floor.

So the parameters are recorded per vault now, in kdf.json beside the salt, and
the upgrade happens at unlock - the one moment the passphrase exists in memory
and a re-key is possible at all.

Every test here is about the two things that could go wrong with that: a vault
that stops opening, and an upgrade that half-happens.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import crypto


def _legacy_vault(tmp_path: Path, passphrase: str = "a-real-passphrase-here"
                  ) -> tuple[crypto.KeyVault, bytes]:
    """A vault as it existed before kdf.json: no parameters file at all."""
    vault = crypto.KeyVault(tmp_path)
    salt = crypto.new_salt()
    key = crypto.derive_key(passphrase, salt, crypto.KDF_V1)
    vault.salt_path.write_bytes(salt)
    vault.verifier_path.write_bytes(crypto.make_verifier(key))
    return vault, key


def _legacy_install(tmp_path: Path, monkeypatch,
                    passphrase: str = "a-real-passphrase-here"):
    """A legacy vault WITH its database, at a path of its own.

    _legacy_vault above writes an identity and nothing else, which is all the
    direct-call tests need. Driving /vault/unlock needs the other half: the
    route unlocks, runs the migration bootstrap against a real database, and
    only then upgrades. Pointed at the suite's own vault it would be
    unlocking one install's database with another install's key.
    """
    import config
    import database
    import vault_state

    db_path = tmp_path / "app.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(database, "DB_PATH", str(db_path))
    vault, key = _legacy_vault(tmp_path, passphrase)
    vault_state.set_key(key)
    try:
        database.init_db()
    finally:
        vault_state.clear_key()
    return vault, key


class TestTheParametersAreRecorded:
    def test_a_new_vault_records_the_current_ones(self, tmp_path) -> None:
        vault = crypto.KeyVault(tmp_path)
        vault.initialize("a-real-passphrase-here")
        assert vault.read_params() == crypto.KDF_CURRENT
        assert json.loads(vault.kdf_path.read_text())["n"] == 2 ** 17

    def test_a_vault_with_no_file_reads_as_legacy(self, tmp_path) -> None:
        # The default has to be the OLD parameters. Defaulting to the current
        # ones would derive a key that opens nothing and report it to the user
        # as a wrong passphrase - a lockout dressed as a typo.
        vault, _ = _legacy_vault(tmp_path)
        assert vault.read_params() == crypto.KDF_LEGACY

    @pytest.mark.parametrize("content", ["", "not json", "[]", '{"n": "x"}'])
    def test_an_unreadable_file_reads_as_legacy_too(
        self, tmp_path, content: str
    ) -> None:
        vault, _ = _legacy_vault(tmp_path)
        vault.kdf_path.write_text(content, encoding="utf-8")
        assert vault.read_params() == crypto.KDF_LEGACY

    def test_the_current_parameters_meet_the_owasp_floor(self) -> None:
        # The number this whole file exists to make changeable. If it is ever
        # lowered, that should be a deliberate act with a failing test in
        # front of it.
        assert crypto.KDF_CURRENT["n"] >= 2 ** 17
        assert crypto.KDF_CURRENT["r"] >= 8
        assert crypto.KDF_CURRENT["p"] >= 1


class TestTheOldVaultStillOpens:
    def test_a_legacy_vault_unlocks_with_its_own_parameters(
        self, tmp_path
    ) -> None:
        # THE compatibility test. Everything else here is worthless if this
        # one fails, because it means an existing user cannot get in.
        vault, key = _legacy_vault(tmp_path)
        assert vault.unlock("a-real-passphrase-here") == key

    def test_a_wrong_passphrase_is_still_wrong(self, tmp_path) -> None:
        vault, _ = _legacy_vault(tmp_path)
        assert vault.unlock("not-the-passphrase-at-all") is None

    def test_a_vault_that_needs_the_upgrade_says_so(self, tmp_path) -> None:
        vault, _ = _legacy_vault(tmp_path)
        assert vault.needs_kdf_upgrade() is True

    def test_a_current_vault_does_not(self, tmp_path) -> None:
        vault = crypto.KeyVault(tmp_path)
        vault.initialize("a-real-passphrase-here")
        assert vault.needs_kdf_upgrade() is False

    def test_stronger_than_current_is_not_downgraded(self, tmp_path) -> None:
        # Somebody who raised their own parameters must not have them pulled
        # back down to whatever this version happens to ship.
        vault = crypto.KeyVault(tmp_path)
        vault.initialize("a-real-passphrase-here")
        vault.write_params({**crypto.KDF_CURRENT, "n": 2 ** 18})
        assert vault.needs_kdf_upgrade() is False


class TestChangingThePassphraseCarriesTheParameters:
    def test_a_rotation_lands_on_the_current_parameters(self, tmp_path) -> None:
        vault, _ = _legacy_vault(tmp_path)
        vault.change_passphrase("a-different-passphrase-here",
                                rekey_fn=lambda key: None,
                                verify_fn=lambda key: True)
        assert vault.read_params() == crypto.KDF_CURRENT

    def test_the_new_key_matches_the_recorded_parameters(self, tmp_path
                                                         ) -> None:
        # The failure this ordering exists to prevent: a salt described by the
        # wrong parameters derives a key that opens nothing.
        vault, _ = _legacy_vault(tmp_path)
        key = vault.change_passphrase("a-different-passphrase-here",
                                      rekey_fn=lambda k: None,
                                      verify_fn=lambda k: True)
        assert vault.unlock("a-different-passphrase-here") == key

    def test_a_failed_rekey_leaves_the_parameters_alone(self, tmp_path
                                                        ) -> None:
        vault, key = _legacy_vault(tmp_path)

        def refuse(new_key):
            raise RuntimeError("rekey did not take")

        with pytest.raises(RuntimeError):
            vault.change_passphrase("a-different-passphrase-here",
                                    rekey_fn=refuse, verify_fn=lambda k: True)

        assert vault.read_params() == crypto.KDF_LEGACY
        assert vault.unlock("a-real-passphrase-here") == key
        assert not vault.kdf_path.with_name("kdf.json.new").exists()


class TestRecoveryKnowsWhichParametersMadeWhichSalt:
    def test_a_half_finished_change_is_completed_under_its_own_parameters(
        self, tmp_path
    ) -> None:
        # A crash between "the new key works" and "the identity files are
        # swapped" leaves salt.bin.new and kdf.json.new. Deriving that staged
        # salt under the LIVE parameters produces a key that opens nothing,
        # which recover_with_db would read as a wrong passphrase - the exact
        # lockout the pairing prevents.
        vault, _ = _legacy_vault(tmp_path)
        staged_salt = crypto.new_salt()
        staged_key = crypto.derive_key("a-real-passphrase-here", staged_salt,
                                       crypto.KDF_CURRENT)
        vault.salt_path.with_name("salt.bin.new").write_bytes(staged_salt)
        vault.write_params(crypto.KDF_CURRENT,
                           vault.kdf_path.with_name("kdf.json.new"))

        recovered = vault.recover_with_db("a-real-passphrase-here",
                                          lambda k: k == staged_key)

        assert recovered == staged_key
        assert vault.salt_path.read_bytes() == staged_salt
        assert vault.read_params() == crypto.KDF_CURRENT

    def test_a_vault_whose_parameters_file_was_lost_still_recovers(
        self, tmp_path
    ) -> None:
        # kdf.json is not secret and not backed up. Losing it must cost a few
        # extra derivations, not the database.
        vault = crypto.KeyVault(tmp_path)
        key = vault.initialize("a-real-passphrase-here")
        vault.kdf_path.unlink()

        assert vault.recover_with_db("a-real-passphrase-here",
                                     lambda k: k == key) == key
        assert vault.read_params() == crypto.KDF_CURRENT


class TestTheUpgradeHappensAtUnlockAndOnlyThere:
    def test_unlocking_a_legacy_vault_upgrades_it(
        self, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import config
        import vault_state
        from routers.vault import _upgrade_kdf_if_needed

        vault_dir = Path(config.DB_PATH).parent
        vault, key = _legacy_vault(vault_dir)
        try:
            rekeyed: list[bytes] = []
            monkeypatch.setattr("database.rekey_db",
                                lambda new_key, current_key: rekeyed.append(new_key))
            monkeypatch.setattr("database.check_key", lambda k, *a, **kw: True)
            monkeypatch.setattr("database.backup_encrypted",
                                lambda path, key=None: Path(path).write_bytes(b"x"))

            upgraded = _upgrade_kdf_if_needed(
                vault, "a-real-passphrase-here", key)

            # `.upgraded`, not the value itself: the function now returns
            # what it could NOT revoke alongside whether it ran, because
            # the unlock route had no way to report the first half.
            assert upgraded.upgraded is True
            assert upgraded.unrevoked == []
            assert vault.read_params() == crypto.KDF_CURRENT
            assert rekeyed, "the database was never re-keyed"
            assert vault_state.get_key() == rekeyed[0]
        finally:
            from tests.conftest import TEST_VAULT_KEY
            vault_state.set_key(TEST_VAULT_KEY)

    def test_unlock_reports_a_sidecar_left_under_the_old_key(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The unlock that re-keys the vault has to say what it could not.

        A KDF upgrade changes the key without the user asking for anything,
        and every snapshot beside the database is still readable under the
        old one until this function moves it. When it cannot, the name went
        to a log line and stopped there: the response carried `kdf_upgraded`
        and nothing else, so the user was told their key had been
        strengthened and not told that a complete copy of the vault still
        opens with the superseded one.
        """
        import vault_state

        vault, key = _legacy_install(tmp_path, monkeypatch)
        vault_state.clear_key()

        # The snapshot the upgrade is supposed to revoke, and cannot.
        #
        # A rotation backup rather than the premigrate one: the unlock
        # bootstrap runs BEFORE the upgrade and discards a premigrate copy of
        # its own accord once a migration pass comes back clean, so a
        # premigrate name would be gone before the code under test ran and
        # the test would measure the sweep instead of the revocation.
        sidecar = tmp_path / "app.db.rekey.bak-1700000000"
        sidecar.write_bytes(b"a full copy of the vault")

        monkeypatch.setattr("database.rekey_db", lambda *a, **kw: None)
        # True for the database, FALSE for the sidecar. A blanket True would
        # have the unlock sweep delete it before the upgrade ran: that sweep
        # removes exactly the copies this vault can open, on the grounds that
        # they are duplicates of the live file. The one this test is about is
        # the other kind - the copy that answers to a key nobody holds any
        # more - so it has to read as unopenable, which is also the truth
        # about the twenty-four bytes written above.
        monkeypatch.setattr(
            "database.check_key",
            lambda k, db_path=None, **kw: sidecar.name not in str(db_path))
        monkeypatch.setattr("database.backup_encrypted",
                            lambda path, key=None: Path(path).write_bytes(b"x"))

        def _held_open(path, new_key, old_key):
            raise OSError(13, "held open by another process")

        monkeypatch.setattr("database.rekey_file", _held_open)

        r = client.post("/api/v1/vault/unlock",
                        json={"passphrase": "a-real-passphrase-here"})

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True, "a failed revocation must not lock anyone out"
        assert body["kdf_upgraded"] is True
        assert sidecar.name in body["unrevoked"]

    def test_a_clean_unlock_reports_nothing_unrevoked(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ground control. An `unrevoked` hardcoded to a name would satisfy
        the test above and mean nothing; and a vault with no upgrade to do
        must still answer the field rather than omit it."""
        import config
        import database
        import vault_state

        db_path = tmp_path / "app.db"
        monkeypatch.setattr(config, "DB_PATH", str(db_path))
        monkeypatch.setattr(database, "DB_PATH", str(db_path))
        vault = crypto.KeyVault(tmp_path)
        key = vault.initialize("a-real-passphrase-here")
        vault_state.set_key(key)
        try:
            database.init_db()
        finally:
            vault_state.clear_key()

        r = client.post("/api/v1/vault/unlock",
                        json={"passphrase": "a-real-passphrase-here"})

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["kdf_upgraded"] is False
        assert body["unrevoked"] == []

    def test_unlock_also_reports_what_the_vault_could_not_clean_up(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Positive control on the SECOND source.

        `unrevoked` has always carried two different things on the
        change-passphrase route: the sidecars that would not re-key, and the
        half-written identity files a rotation could not remove
        (KeyVault.left_behind). Carrying only the first here would give the
        same field two meanings depending on which route produced it.
        """
        import vault_state

        vault, key = _legacy_install(tmp_path, monkeypatch)
        vault_state.clear_key()

        monkeypatch.setattr("database.rekey_db", lambda *a, **kw: None)
        monkeypatch.setattr("database.check_key", lambda k, *a, **kw: True)
        monkeypatch.setattr("database.backup_encrypted",
                            lambda path, key=None: Path(path).write_bytes(b"x"))

        real_change = crypto.KeyVault.change_passphrase

        def leaves_something(self, *a, **kw):
            out = real_change(self, *a, **kw)
            self.left_behind.append("salt.bin.new")
            return out

        monkeypatch.setattr(crypto.KeyVault, "change_passphrase",
                            leaves_something)

        r = client.post("/api/v1/vault/unlock",
                        json={"passphrase": "a-real-passphrase-here"})

        assert r.status_code == 200, r.text
        assert r.json()["unrevoked"] == ["salt.bin.new"]

    def test_a_vault_already_current_is_left_completely_alone(
        self, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import config
        from routers.vault import _upgrade_kdf_if_needed

        vault = crypto.KeyVault(Path(config.DB_PATH).parent)
        key = vault.initialize("a-real-passphrase-here")
        monkeypatch.setattr("database.rekey_db", lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("re-keyed a vault that did not need it")))

        assert _upgrade_kdf_if_needed(vault, "a-real-passphrase-here",
                                      key).upgraded is False

    def test_a_failed_upgrade_leaves_the_vault_openable(
        self, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The whole risk of doing this on the unlock path. The user is already
        # in; a failure here must cost them nothing.
        import config
        from routers.vault import _upgrade_kdf_if_needed

        vault_dir = Path(config.DB_PATH).parent
        vault, key = _legacy_vault(vault_dir)
        monkeypatch.setattr("database.backup_encrypted",
                            lambda path, key=None: Path(path).write_bytes(b"x"))
        monkeypatch.setattr("database.rekey_db", lambda *a, **kw: None)
        monkeypatch.setattr("database.check_key", lambda k, *a, **kw: False)

        assert _upgrade_kdf_if_needed(vault, "a-real-passphrase-here",
                                      key).upgraded is False
        assert vault.read_params() == crypto.KDF_LEGACY
        assert vault.unlock("a-real-passphrase-here") == key

    def test_a_failed_upgrade_does_not_leave_its_backup_behind(
        self, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # That backup is a complete copy of the vault under the old key.
        import config
        from routers.vault import _upgrade_kdf_if_needed

        db_path = Path(config.DB_PATH)
        vault, key = _legacy_vault(db_path.parent)
        monkeypatch.setattr("database.backup_encrypted",
                            lambda path, key=None: Path(path).write_bytes(b"x"))
        monkeypatch.setattr("database.rekey_db", lambda *a, **kw: None)
        monkeypatch.setattr("database.check_key", lambda k, *a, **kw: False)

        _upgrade_kdf_if_needed(vault, "a-real-passphrase-here", key)

        leftovers = list(db_path.parent.glob(db_path.name + ".rekey.bak-*"))
        assert leftovers == []
