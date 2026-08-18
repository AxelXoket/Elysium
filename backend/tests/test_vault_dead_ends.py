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

import json
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
