"""POST /vault/reset - the owner's "forgot your passphrase" answer.

There is no recovery in this vault by design (crypto.py: the key is never
stored, forgetting the passphrase means the data is unrecoverable). What was
missing was the OTHER side of that promise: a way to stop being locked out
forever and start over. This route destroys every artefact of the vault, and
because it must work WITHOUT the passphrase - the whole reason it exists - the
tests here are the entire safety argument for a route that cannot ask "prove
you know the secret" the way every other vault route does.

Every test builds REAL files at REAL paths this app actually writes to (a
real KeyVault, a real encrypted database, real sidecar names), the same way
test_vault_dead_ends.py does, because the question is whether the route
destroys what the app actually leaves lying around - not a fixture invented
for the test.
"""

from __future__ import annotations

from pathlib import Path

import sys

import pytest

import config
import crypto
import database
import keyring_service
import legacy_migration
import vault_state
from routers import vault as vault_router
from routers.vault import RESET_CONFIRMATION_PHRASE

PASSPHRASE = "correct horse battery staple reset"



@pytest.fixture(autouse=True)
def _reset_door_armed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Open the reset door for the tests in this file.

    The route is unreachable unless the process is frozen AND the launch-token
    gate is armed, which is true of the shipped app and false of pytest. Every
    test here exercises what the route DOES, so each one has to stand where the
    lock screen stands.

    The predicate itself is replaced rather than its two ingredients, and that
    is deliberate. Monkeypatching sys.frozen reaches three subsystems that have
    nothing to do with the vault and would make these tests measure a different
    program. Replacing the seam keeps the blast radius at one function, and the
    cost - that these tests no longer prove the door is consulted at all - is
    paid off by TestTheDoorIsShutOnEveryOtherBuild, which asserts exactly that
    and would go red if the guard were deleted.
    """
    monkeypatch.setattr(vault_router, "_reset_door_is_open", lambda: True)

def _real_locked_vault(tmp_path: Path, monkeypatch) -> tuple[Path, bytes]:
    """A real, fully identified vault with a real schema, then locked.

    Same construction as test_vault_dead_ends.py's _vault_with_db: nothing
    mocked, because the whole question this file asks is whether the route
    destroys the REAL files this app writes.
    """
    db_path = tmp_path / "app.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(database, "DB_PATH", str(db_path))
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    # Every directory the reset route touches, pinned under tmp_path so a
    # test can never reach this machine's own voice folders - no autouse
    # fixture redirects TTS_REFS_DIR, and fs_guard.py is what caught it
    # while this file was being written.
    monkeypatch.setattr(config, "UPLOADS_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(config, "TTS_CACHE_DIR", str(tmp_path / "voice_cache"))
    monkeypatch.setattr(config, "TTS_REFS_DIR", str(tmp_path / "voice_refs"))
    vault = crypto.KeyVault(tmp_path)
    key = vault.initialize(PASSPHRASE)
    vault_state.set_key(key)
    try:
        database.init_db()
    finally:
        vault_state.clear_key()
    return db_path, key


@pytest.fixture()
def legacy_keyring(monkeypatch):
    """A fake credential store, so the route's keyring sweep is provable.

    Ground truth: a name present in this dict is "a legacy entry exists on
    this machine". The route calling keyring_service.delete_legacy pops it;
    a route that skipped the keyring category would leave it standing.
    """
    store: dict[str, str] = {}
    monkeypatch.setattr(keyring_service, "delete_legacy",
                        lambda name: (store.pop(name, None), True)[1])
    return store


def _populate_every_artefact(tmp_path: Path, db_path: Path,
                             legacy_keyring: dict) -> dict[str, Path]:
    """One real file per artefact class the route must destroy.

    Returns a name -> path map so a test can assert on each class BY NAME
    rather than with one blanket "the directory is empty" check - the house
    rule this whole file exists to satisfy.
    """
    paths: dict[str, Path] = {}

    for suffix in database._SIDECAR_SUFFIXES:
        p = db_path.with_name(db_path.name + suffix)
        p.write_bytes(b"a WAL or journal page holding committed chat rows")
        paths[f"db_sidecar{suffix}"] = p

    vault_dir = db_path.parent
    for name in ("salt.bin", "verifier.bin", "kdf.json", "vault.recovery"):
        staged = vault_dir / f"{name}.new"
        staged.write_bytes(b"an identity a crashed rotation staged")
        paths[f"{name}.new"] = staged
        shelved = vault_dir / f"{name}.bak-1700000000"
        shelved.write_bytes(b"a superseded identity shelved by a rotation")
        paths[f"{name}.bak"] = shelved

    plain = db_path.with_name(db_path.name + ".plain.bak-1700000000")
    plain.write_bytes(b"a plaintext pre-vault copy of the whole database")
    paths["plaintext_backup"] = plain

    orphan = db_path.with_name(db_path.name + ".enc-tmp")
    orphan.write_bytes(b"an encrypted copy stranded mid-migration")
    paths["orphaned_copy"] = orphan

    rotation = db_path.with_name(db_path.name + ".rekey.bak-1700000000")
    rotation.write_bytes(b"a full vault copy a rotation left behind")
    paths["rotation_backup"] = rotation

    stub = db_path.with_name(db_path.name + ".empty-stub-bak")
    stub.write_bytes(b"")
    paths["empty_stub"] = stub

    premigrate = legacy_migration.premigrate_backup_path()
    premigrate.write_bytes(b"a stale pre-migration snapshot of the vault")
    paths["premigrate_backup"] = premigrate
    partial = premigrate.with_name(premigrate.name + ".partial")
    partial.write_bytes(b"a half-written premigrate snapshot")
    paths["premigrate_partial"] = partial
    unreadable = premigrate.with_name(
        premigrate.name + ".unreadable-1700000000")
    unreadable.write_bytes(b"a premigrate snapshot from an era with no key")
    paths["premigrate_unreadable"] = unreadable

    # Never migrated: this vault has never been unlocked in this test, and
    # migration only runs on unlock - exactly the state a forgotten-passphrase
    # user is actually in. UPLOADS_DIR/TTS_CACHE_DIR/TTS_REFS_DIR are already
    # pinned under tmp_path by _real_locked_vault.
    uploads = Path(config.UPLOADS_DIR)
    uploads.mkdir(parents=True, exist_ok=True)
    upload_file = uploads / ("a" * 64 + ".png")
    upload_file.write_bytes(b"\x89PNG" + b"a picture nobody migrated yet")
    paths["upload"] = upload_file

    cache = Path(config.TTS_CACHE_DIR)
    cache.mkdir(parents=True, exist_ok=True)
    spoken = cache / "speak-1-1700000000000-1.wav"
    spoken.write_bytes(b"RIFF" + b"a reply read aloud")
    paths["voice_cache"] = spoken

    refs = Path(config.TTS_REFS_DIR)
    (refs / "voice1").mkdir(parents=True, exist_ok=True)
    ref_clip = refs / "voice1" / "ref.wav"
    ref_clip.write_bytes(b"RIFF" + b"the user's own recorded voice")
    paths["voice_reference"] = ref_clip

    webview = tmp_path / "webview" / "EBWebView" / "Default" / "Local Storage"
    webview.mkdir(parents=True)
    profile_file = webview / "leveldb.log"
    profile_file.write_bytes(b"the last-open chat id and the wallpaper")
    paths["webview_profile"] = profile_file

    legacy_keyring[config.SECRET_API_KEY] = "sk-a-leaked-key"
    legacy_keyring[config.SECRET_PROXY_URL] = "http://a-leaked-proxy"

    return paths


class TestRefusesWhileUnlocked:
    """The central design problem: whoever can unlock does not need this
    door, and answering it anyway would be a much more dangerous route."""

    def test_refuses_and_destroys_nothing(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        legacy_keyring,
    ) -> None:
        db_path, key = _real_locked_vault(tmp_path, monkeypatch)
        ground = _populate_every_artefact(
            tmp_path, db_path, legacy_keyring)
        vault_state.set_key(key)  # unlocked, the state this must refuse

        response = client.post("/api/v1/vault/reset",
                               json={"confirm": RESET_CONFIRMATION_PHRASE})

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "vault_unlocked"
        assert db_path.exists(), "the database was destroyed while unlocked"
        for label, path in ground.items():
            assert path.exists(), f"{label} was destroyed while unlocked"

    def test_the_check_runs_before_the_confirmation_phrase_is_even_read(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Order is load-bearing. If confirmation were checked first, a
        WRONG phrase while unlocked would answer 422 (a fact about the body)
        instead of 409 (a fact about the state) - and a caller reading only
        the status code could believe a locked-only endpoint had considered
        their request at all."""
        _real_locked_vault(tmp_path, monkeypatch)
        key = crypto.KeyVault(tmp_path).unlock(PASSPHRASE)
        assert key is not None
        vault_state.set_key(key)

        response = client.post("/api/v1/vault/reset",
                               json={"confirm": "not even close"})

        assert response.status_code == 409
        assert response.json()["detail"] == "vault_unlocked"


class TestRefusesTheWrongConfirmationPhrase:
    """The phrase is the ONLY thing standing between an accidental POST and
    an irreversible wipe while the vault is (correctly) locked."""

    @pytest.mark.parametrize("confirm", [
        "delete everything",                       # wrong case
        RESET_CONFIRMATION_PHRASE.lower(),          # derived near-miss
        RESET_CONFIRMATION_PHRASE + "!",            # trailing character
        RESET_CONFIRMATION_PHRASE[:-1],             # missing last character
        RESET_CONFIRMATION_PHRASE.replace(" ", ""), # missing the space
        "yes",
    ])
    def test_refuses_and_destroys_nothing(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        legacy_keyring, confirm: str,
    ) -> None:
        db_path, _key = _real_locked_vault(tmp_path, monkeypatch)
        ground = _populate_every_artefact(
            tmp_path, db_path, legacy_keyring)

        response = client.post("/api/v1/vault/reset", json={"confirm": confirm})

        assert response.status_code == 422, response.text
        assert response.json()["detail"] == "reset_confirmation_mismatch"
        assert db_path.exists()
        for label, path in ground.items():
            assert path.exists(), f"{label} was destroyed by a wrong phrase"

    def test_refuses_an_empty_confirmation(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A bare POST with nothing typed. Caught by the request body's own
        min_length before this route's own check ever runs - a different
        layer, still a 422, still nothing destroyed."""
        db_path, _key = _real_locked_vault(tmp_path, monkeypatch)

        response = client.post("/api/v1/vault/reset", json={"confirm": ""})

        assert response.status_code == 422, response.text
        assert db_path.exists()

    def test_surrounding_whitespace_is_forgiven(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The discriminating half: stripping whitespace is the one and only
        leniency, so a genuine copy-paste with a trailing newline still
        works. Anything else wrong must still refuse."""
        db_path, _key = _real_locked_vault(tmp_path, monkeypatch)

        response = client.post(
            "/api/v1/vault/reset",
            json={"confirm": f"  {RESET_CONFIRMATION_PHRASE}\n"})

        assert response.status_code == 200, response.text
        assert not db_path.exists()


class TestTheFullWipe:
    """Every artefact class, checked by name, plus the setup that proves a
    green run is not simply an empty directory."""

    def test_every_ground_truth_artefact_is_destroyed(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        legacy_keyring,
    ) -> None:
        db_path, _key = _real_locked_vault(tmp_path, monkeypatch)
        ground = _populate_every_artefact(
            tmp_path, db_path, legacy_keyring)
        salt_path = tmp_path / "salt.bin"
        verifier_path = tmp_path / "verifier.bin"
        kdf_path = tmp_path / "kdf.json"

        # GROUND: every artefact this test is about to check for destruction
        # genuinely exists before the call - a green run below is not passing
        # against an empty directory.
        assert db_path.exists()
        assert salt_path.exists() and verifier_path.exists()
        assert kdf_path.exists()
        for label, path in ground.items():
            assert path.exists(), f"setup failed to create {label}"
        assert legacy_keyring == {
            config.SECRET_API_KEY: "sk-a-leaked-key",
            config.SECRET_PROXY_URL: "http://a-leaked-proxy",
        }

        response = client.post("/api/v1/vault/reset",
                               json={"confirm": RESET_CONFIRMATION_PHRASE})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body == {"ok": True, "left": []}, body

        # Asserted PER ARTEFACT, not as one blanket "the folder is empty".
        assert not db_path.exists(), "the database survived"
        assert not salt_path.exists(), "the salt survived"
        assert not verifier_path.exists(), "the verifier survived"
        assert not kdf_path.exists(), "the KDF parameters survived"
        for label, path in ground.items():
            assert not path.exists(), f"{label} survived the reset"
        assert legacy_keyring == {}, "a legacy keyring entry survived"

        # The three directories, not just the files inside them.
        assert not (tmp_path / "uploads").exists()
        assert not (tmp_path / "voice_cache").exists()
        assert not (tmp_path / "voice_refs").exists()
        assert not (tmp_path / "webview").exists()

    def test_absent_categories_do_not_fail_the_route(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The positive control for the empty-directory case: a vault that
        was set up and never used at all (no uploads, no voice, no
        backups) must still reset cleanly rather than erroring on a
        directory that was never created."""
        db_path, _key = _real_locked_vault(tmp_path, monkeypatch)
        monkeypatch.setattr(config, "UPLOADS_DIR",
                            str(tmp_path / "never-created-uploads"))
        monkeypatch.setattr(config, "TTS_CACHE_DIR",
                            str(tmp_path / "never-created-cache"))
        monkeypatch.setattr(config, "TTS_REFS_DIR",
                            str(tmp_path / "never-created-refs"))

        response = client.post("/api/v1/vault/reset",
                               json={"confirm": RESET_CONFIRMATION_PHRASE})

        assert response.status_code == 200, response.text
        assert response.json() == {"ok": True, "left": []}
        assert not db_path.exists()

    def test_status_reports_a_clean_slate_afterward(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The half that makes a reset actually USABLE: the frontend decides
        which screen to show from /vault/status, so this has to answer
        "never set up" for the next launch to land on first-run setup."""
        _real_locked_vault(tmp_path, monkeypatch)

        assert client.post(
            "/api/v1/vault/reset",
            json={"confirm": RESET_CONFIRMATION_PHRASE}).status_code == 200

        status = client.get("/api/v1/vault/status").json()
        assert status["initialized"] is False
        assert status["unlocked"] is False
        assert status["premigrate_backup"] is False


class TestCanBeSetUpAgainAfterward:
    """The clean-slate claim, proved end to end: init, write data, unlock."""

    def test_a_new_passphrase_sets_up_a_working_vault(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _real_locked_vault(tmp_path, monkeypatch)

        assert client.post(
            "/api/v1/vault/reset",
            json={"confirm": RESET_CONFIRMATION_PHRASE}).status_code == 200

        new_passphrase = "an entirely different sentence, freshly chosen"
        init = client.post("/api/v1/vault/init",
                           json={"passphrase": new_passphrase})
        assert init.status_code == 200, init.text
        assert init.json()["migrated"] is False

        char = client.post("/api/v1/characters", json={
            "name": "AfterReset", "description": "d", "first_mes": "hi",
        })
        assert char.status_code == 201

        client.post("/api/v1/vault/lock")
        assert client.get("/api/v1/characters").status_code == 423
        unlock = client.post("/api/v1/vault/unlock",
                             json={"passphrase": new_passphrase})
        assert unlock.status_code == 200
        names = [c["name"] for c in client.get("/api/v1/characters").json()]
        assert names == ["AfterReset"]


class TestTheSurvivorsAreExactlyWhatTheScreenPromises:
    """The tripwire for a drift nobody could see.

    `VaultGate.tsx`'s reset panel names what survives - the downloaded voice
    runtime and models - and that sentence is a promise made immediately
    before an irreversible click. The route and the sentence live in two
    files with nothing between them, and they have already drifted once: the
    commit that started shredding `elysium.log` also shipped a screen saying
    the log was left alone on purpose, and a test pinned that sentence in
    place so correcting it would have looked like a regression.

    Nothing can honestly assert "the screen names every category the route
    destroys" - the route speaks in paths and the screen in human categories,
    and a mapping table between them would be a third place to drift. What IS
    assertable is the half that matters: the exact set that SURVIVES. That
    set is the sentence. So this test fails the moment the sweep grows or
    shrinks, in the file the person editing the sweep is already in.
    """

    def _voice_tree(self, tmp_path: Path, monkeypatch) -> dict:
        """One marker file in each directory the sweep must NOT touch."""
        made = {}
        for setting in ("TTS_MODELS_DIR", "TTS_BIN_DIR", "TTS_ENVS_DIR",
                        "TTS_PY_DIR", "TTS_UV_CACHE_DIR"):
            d = tmp_path / "voice" / setting.lower()
            d.mkdir(parents=True, exist_ok=True)
            monkeypatch.setattr(config, setting, str(d))
            marker = d / "marker.bin"
            marker.write_bytes(b"engine software, not the user's content")
            made[setting] = marker
        return made

    def test_the_engine_survives_and_nothing_else_does(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        legacy_keyring,
    ) -> None:
        db_path, _key = _real_locked_vault(tmp_path, monkeypatch)
        doomed = _populate_every_artefact(tmp_path, db_path, legacy_keyring)
        survivors = self._voice_tree(tmp_path, monkeypatch)

        # GROUND, both directions: everything exists first, so neither half
        # of the assertion below can pass against an empty tree.
        for label, path in doomed.items():
            assert path.exists(), f"setup failed to create {label}"
        for label, path in survivors.items():
            assert path.exists(), f"setup failed to create {label}"

        resp = client.post("/api/v1/vault/reset",
                           json={"confirm": RESET_CONFIRMATION_PHRASE})
        assert resp.status_code == 200, resp.text
        assert resp.json()["ok"] is True, resp.text

        for label, path in survivors.items():
            assert path.exists(), (
                f"{label} was destroyed. The reset panel tells the user the "
                f"downloaded voice engine survives, and that sentence is now "
                f"false. Change the copy in VaultGate.tsx and its tests in "
                f"the same commit, or put this family back.")
        for label, path in doomed.items():
            assert not path.exists(), (
                f"{label} survived. If that is deliberate, the reset panel "
                f"must say so - it currently promises the engine is the only "
                f"thing left standing.")


class TestTheDoorIsShutOnEveryOtherBuild:
    """The half the autouse fixture above deliberately gives away.

    Every other test in this file opens the door for itself so it can measure
    what the route does. These four measure whether the door exists, using the
    real predicate, and they are the reason replacing that seam elsewhere is
    honest rather than convenient: delete the guard in routers/vault.py and
    the first test here goes red.
    """

    def test_a_development_tree_has_no_reset_door(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        legacy_keyring,
    ) -> None:
        # pytest is not frozen, so this is the real answer for `uvicorn
        # main:app` and for `python run_app.py` out of a checkout. Nothing is
        # patched here except the fixture being taken back.
        monkeypatch.undo()
        db_path, _key = _real_locked_vault(tmp_path, monkeypatch)
        ground = _populate_every_artefact(tmp_path, db_path, legacy_keyring)

        response = client.post("/api/v1/vault/reset",
                               json={"confirm": RESET_CONFIRMATION_PHRASE})

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "vault_reset_unavailable"
        assert db_path.exists(), "a closed door still destroyed the database"
        for name, artefact in ground.items():
            assert artefact.exists(), f"a closed door destroyed {name}"

    def test_a_closed_door_answers_before_the_unlocked_check(
        self, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Ordering, and it is not cosmetic. The 409 branch lives inside
        # _vault_lock; the guard sits above it so a door that does not exist
        # in this build never queues behind an unlock in flight. If the guard
        # slipped below the lock this would come back 409.
        monkeypatch.undo()
        _db_path, key = _real_locked_vault(tmp_path, monkeypatch)
        vault_state.set_key(key)          # unlocked: the 409 state

        response = client.post("/api/v1/vault/reset",
                               json={"confirm": RESET_CONFIRMATION_PHRASE})

        assert response.status_code == 404, response.text

    def test_being_the_packaged_build_is_not_on_its_own_enough(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # sys.frozen describes THIS PROCESS, not the caller. A curl at the
        # packaged exe is exactly as frozen as the window is.
        monkeypatch.undo()      # take back the armed door above
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(vault_router.launch_token, "configured",
                            lambda: None)
        assert vault_router._reset_door_is_open() is False

    def test_an_armed_token_gate_is_not_on_its_own_enough(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # run_app issues a token on the development path too, and configured()
        # falls back to the environment as a developer seam. Armed alone would
        # hand the door straight back to the checkout.
        monkeypatch.undo()      # take back the armed door above
        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.setattr(vault_router.launch_token, "configured",
                            lambda: "a-token")
        assert vault_router._reset_door_is_open() is False

    def test_both_together_open_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # GROUND. Without this the three refusals above would also pass for a
        # predicate that returned False unconditionally, which would ship an
        # app whose lock screen cannot reset anything.
        monkeypatch.undo()      # take back the armed door above
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(vault_router.launch_token, "configured",
                            lambda: "a-token")
        assert vault_router._reset_door_is_open() is True
