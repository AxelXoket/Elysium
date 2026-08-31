"""Audit KÖK 2: five paths that reported success they had not achieved.

The shape repeats. A cleanup runs best-effort - correctly, because a held
file must not turn a re-lock into a 500 - and then the route answers a flat
{"ok": True} regardless. The user is told the thing is gone. It is not, and
the only trace is a log line no desktop user opens.

These tests assert the route CARRIES the failure, not that the failure stops
happening: best-effort is the right design, silence was the bug.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import config
import database
import keyring_service
import secrets_service
from routers import vault as vault_router


# ---------------------------------------------------------------------------
# 1. a revoked API key must not come back on the next unlock
# ---------------------------------------------------------------------------

def test_deleting_the_api_key_also_clears_the_legacy_keyring_copy(
    client, monkeypatch,
):
    """save_api_key cleared the legacy copy and delete_api_key did not, so
    migrate_legacy_secrets copied the stale secret back into the vault on the
    next unlock. Revocation - the one action people take after a leak - was
    silently undone."""
    legacy: dict[str, str] = {config.SECRET_API_KEY: "sk-leaked-key"}
    monkeypatch.setattr(keyring_service, "read_legacy", legacy.get)
    monkeypatch.setattr(
        keyring_service, "delete_legacy", lambda name: legacy.pop(name, None),
    )

    assert client.delete("/api/v1/settings/api-key").status_code == 200
    assert legacy == {}, "the legacy keyring copy survived the delete"

    # The resurrection path itself: run it and prove nothing comes back.
    import legacy_migration
    legacy_migration.migrate_legacy_secrets()
    assert secrets_service.get_secret(config.SECRET_API_KEY) is None


def test_deleting_the_proxy_also_clears_its_legacy_copy(client, monkeypatch):
    legacy: dict[str, str] = {config.SECRET_PROXY_URL: "http://leaked:8080"}
    monkeypatch.setattr(keyring_service, "read_legacy", legacy.get)
    monkeypatch.setattr(
        keyring_service, "delete_legacy", lambda name: legacy.pop(name, None),
    )
    assert client.delete("/api/v1/settings/proxy").status_code == 200
    assert legacy == {}


# ---------------------------------------------------------------------------
# 2. a rotation must name what it failed to revoke
# ---------------------------------------------------------------------------

def test_rekey_sidecars_returns_the_files_it_could_not_revoke(tmp_path, monkeypatch):
    db = tmp_path / "app.db"
    db.write_bytes(b"live")
    stuck = tmp_path / "app.db.premigrate.bak"
    stuck.write_bytes(b"a full copy of the vault")
    skip = tmp_path / "app.db.rekey.bak-1"
    skip.write_bytes(b"this call's own backup")

    def _always_fails(path, new_key, old_key):
        raise OSError(13, "held open")

    monkeypatch.setattr(database, "rekey_file", _always_fails)
    left = vault_router._rekey_sidecars(db, skip, b"old", b"new")
    assert left == ["app.db.premigrate.bak"]


def test_a_clean_rotation_reports_nothing_unrevoked(tmp_path, monkeypatch):
    db = tmp_path / "app.db"
    db.write_bytes(b"live")
    (tmp_path / "app.db.premigrate.bak").write_bytes(b"copy")
    skip = tmp_path / "app.db.rekey.bak-1"
    skip.write_bytes(b"own")
    # WHAT THIS STUB MEANS, now that it is no longer obvious. Returning None
    # is how the REAL rekey_file reports success, and since 2026-08-31 that
    # promise has teeth: it verifies the file opens under the new key from a
    # fresh connection and raises rekey_did_not_take when it does not. So the
    # stub still stands for "the rekey worked". What it no longer does is
    # exercise that verification - these files hold four bytes of ASCII, not
    # a database - which is why the silent-no-op case is tested for real in
    # test_vault_audit.py instead of here.
    monkeypatch.setattr(database, "rekey_file", lambda *a, **kw: None)
    assert vault_router._rekey_sidecars(db, skip, b"old", b"new") == []


def test_change_passphrase_puts_unrevoked_on_the_wire(
    client, tmp_path, monkeypatch,
):
    """The end the user actually sees, wired end to end.

    A real vault with a real passphrase, a real sidecar snapshot beside it,
    and a rekey_file that fails the way a held-open file does. Rotating after
    a leak and being told 'done' while a full copy still opens with the old
    passphrase is the whole failure; this is the field that makes it sayable.
    """
    import vault_state

    vdir = tmp_path / "rotate"
    vdir.mkdir()
    db_path = str(vdir / "app.db")
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(database, "DB_PATH", db_path)
    vault_state.clear_key()

    assert client.post("/api/v1/vault/init", json={
        "passphrase": "seaside-orchid-9",
    }).status_code == 200

    # The snapshot the rotation is supposed to revoke, and cannot.
    (vdir / "app.db.premigrate.bak").write_bytes(b"a full copy of the vault")

    def _held_open(path, new_key, old_key):
        raise OSError(13, "held open by another process")

    monkeypatch.setattr(database, "rekey_file", _held_open)

    r = client.post("/api/v1/vault/change-passphrase", json={
        "old_passphrase": "seaside-orchid-9",
        "new_passphrase": "harbour-lantern-4",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True, "the rotation itself must still succeed"
    assert body["unrevoked"] == ["app.db.premigrate.bak"]


def test_a_sidecar_whose_rekey_did_not_take_lands_in_unrevoked(
    client, tmp_path, monkeypatch,
):
    """The silent failure, at the end the user reads.

    The test above uses a rekey that RAISES, which was always reported
    correctly. This one uses a rekey that returns quietly having done
    nothing, which is what PRAGMA rekey actually does under a concurrent
    write lock - and which the route reported as a clean rotation with
    `unrevoked: []` while a full copy of the vault still opened with the old
    passphrase.

    Nothing is stubbed out at the route: the sidecar is a real encrypted
    database under the real old key, and the real rekey_file runs on it. The
    only thing replaced is the pragma itself, and only for the duration of
    that one call, so the main database's own rotation is untouched.
    """
    import vault_state

    vdir = tmp_path / "rotate"
    vdir.mkdir()
    db_path = str(vdir / "app.db")
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(database, "DB_PATH", db_path)
    vault_state.clear_key()

    assert client.post("/api/v1/vault/init", json={
        "passphrase": "seaside-orchid-9",
    }).status_code == 200

    old_key = vault_state.get_key()
    sidecar = vdir / "app.db.premigrate.bak"
    con = database.sqlite3.connect(str(sidecar))
    try:
        database._key_pragma(con, old_key)
        con.execute("CREATE TABLE secrets (v)")
        con.execute("INSERT INTO secrets VALUES ('a full copy of the vault')")
        con.commit()
    finally:
        con.close()

    real_pragma = database._key_pragma
    real_rekey = database.rekey_file

    def deaf(connection, key, *, rekey=False):
        if rekey:
            return                      # accepted, and nothing happened
        real_pragma(connection, key)

    def rekey_that_does_not_take(path, new_key, current_key):
        monkeypatch.setattr(database, "_key_pragma", deaf)
        try:
            return real_rekey(path, new_key, current_key)
        finally:
            monkeypatch.setattr(database, "_key_pragma", real_pragma)

    monkeypatch.setattr(database, "rekey_file", rekey_that_does_not_take)

    r = client.post("/api/v1/vault/change-passphrase", json={
        "old_passphrase": "seaside-orchid-9",
        "new_passphrase": "harbour-lantern-4",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True, "the rotation itself must still succeed"
    assert body["unrevoked"] == ["app.db.premigrate.bak"]

    # And the reason the name has to be on that list: it is true.
    assert database.check_key(old_key, str(sidecar)) is True


# ---------------------------------------------------------------------------
# 3. unlock must not swallow the plaintext-backup filename
# ---------------------------------------------------------------------------

def test_unlock_reports_a_migration_that_ran_on_its_own_path(client, monkeypatch):
    """_bootstrap_unlocked has always returned this path; /vault/init reported
    it and /vault/unlock threw it away. Same migration, same unencrypted copy
    of every message on disk, no notice."""
    monkeypatch.setattr(
        vault_router, "_bootstrap_unlocked",
        lambda: str(Path(config.DB_PATH).with_name("app.db.plain.bak-123")),
    )
    monkeypatch.setattr(
        vault_router, "_vault",
        lambda: _FakeVault(),
    )
    # The fixture pre-unlocks; the branch under test is the one that actually
    # runs a bootstrap, so start from locked.
    import vault_state
    vault_state.clear_key()
    r = client.post("/api/v1/vault/unlock", json={"passphrase": "anything-at-all"})
    assert r.status_code == 200
    body = r.json()
    assert body["migrated"] is True
    assert body["backup"] == "app.db.plain.bak-123"


class _FakeVault:
    """A vault that is initialized and accepts any passphrase.

    The real unlock path is covered by test_vault.py; this isolates the one
    thing under test - what the route does with the bootstrap's return value.
    """

    def is_initialized(self) -> bool:
        return True

    def can_derive(self) -> bool:
        return True

    def can_recover(self) -> bool:
        # The gate the unlock route reads. It used to read can_derive, and
        # that difference is K-05: recovery accepts a staged salt and the
        # route did not. A fake has to carry the real interface or it stops
        # being a stand-in and becomes a way to miss the change.
        return True

    def unlock(self, passphrase: str) -> bytes:
        return bytes(range(32))

    def needs_kdf_upgrade(self) -> bool:
        # Already current. The KDF upgrade runs on this same path and would
        # re-key the database; this test is about the bootstrap's return
        # value, and a fake that re-keys nothing keeps it about that.
        return False


# ---------------------------------------------------------------------------
# 4. lock must admit audio it could not delete
# ---------------------------------------------------------------------------

def test_lock_reports_audio_that_survived_the_wipe(client, monkeypatch):
    monkeypatch.setattr(
        vault_router, "_lock_down_voice_sync", lambda: ["speak-0007.wav"],
    )
    r = client.post("/api/v1/vault/lock")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "audio_left": ["speak-0007.wav"]}


def test_a_clean_lock_still_reports_an_empty_list(client, monkeypatch):
    monkeypatch.setattr(vault_router, "_lock_down_voice_sync", lambda: [])
    assert client.post("/api/v1/vault/lock").json() == {
        "ok": True, "audio_left": [],
    }


def test_a_teardown_that_blew_up_is_not_reported_as_a_clean_wipe(monkeypatch):
    """`unknown` and `none` must not look alike: an exception used to be
    swallowed into a warning and the route answered exactly as it would have
    on a successful wipe."""
    def _boom():
        raise RuntimeError("worker will not die")

    monkeypatch.setattr("tts.host.get_host", _boom)
    assert vault_router._lock_down_voice_sync() == ["<teardown failed>"]


# ---------------------------------------------------------------------------
# 5. the post-crash sweep must be able to report its own failure
# ---------------------------------------------------------------------------

def test_purge_voice_cache_names_the_files_it_could_not_delete(
    tmp_path, monkeypatch, caplog,
):
    """The only sweep that runs after a hard kill, and the only one that could
    not say it had failed - its sibling host.wipe_cache names every file."""
    cache = tmp_path / "voice"
    cache.mkdir()
    (cache / "speak-0001.wav").write_bytes(b"audible conversation")
    monkeypatch.setattr(config, "TTS_CACHE_DIR", str(cache))

    real_unlink = Path.unlink

    def _held(self, *a, **kw):
        if self.suffix == ".wav":
            raise PermissionError(13, "in use")
        return real_unlink(self, *a, **kw)

    monkeypatch.setattr(Path, "unlink", _held)
    vault_router._purge_voice_cache()

    assert "speak-0001.wav" in caplog.text, \
        "the file left in the clear was never named"


# ---------------------------------------------------------------------------
# 6. voice-side deletes: the same "removed" claim, made honestly
# ---------------------------------------------------------------------------

def test_deleting_a_reference_voice_reports_a_delete_that_did_not_happen(
    tmp_path, monkeypatch,
):
    """refs.delete() returned None and the route answered a hardcoded True,
    so a clip the worker still had open reappeared on the next refetch with
    no explanation."""
    from tts import refs

    folder = tmp_path / "stuck-voice"
    folder.mkdir()
    (folder / "ref.wav").write_bytes(b"audio")
    monkeypatch.setattr(refs, "_voice_dir", lambda vid: folder)
    monkeypatch.setattr(refs.shutil, "rmtree", lambda *a, **kw: None)

    assert refs.delete("stuck-voice") is False


def test_deleting_a_reference_voice_that_worked_reports_true(tmp_path, monkeypatch):
    from tts import refs

    folder = tmp_path / "ok-voice"
    folder.mkdir()
    monkeypatch.setattr(refs, "_voice_dir", lambda vid: folder)
    assert refs.delete("ok-voice") is True
    assert not folder.exists()


def test_deleting_a_voice_that_was_never_there_is_success(tmp_path, monkeypatch):
    from tts import refs

    monkeypatch.setattr(refs, "_voice_dir", lambda vid: tmp_path / "absent")
    assert refs.delete("absent") is True


def test_a_failed_uninstall_leaves_the_engine_registered_for_retry(
    tmp_path, monkeypatch,
):
    """Unregistering after a failed rmtree removed the engine from the list,
    which removed the Remove button, which removed the only way to retry -
    while the gigabytes stayed on disk."""
    from tts import provision

    target = tmp_path / "fish_s2"
    target.mkdir()
    (target / "python.exe").write_bytes(b"held open")

    monkeypatch.setattr(provision, "env_dir", lambda eid: target)
    monkeypatch.setattr(provision.shutil, "rmtree", lambda *a, **kw: None)
    unregistered: list[str] = []
    monkeypatch.setattr(provision.runtimes, "unregister",
                        lambda eid: unregistered.append(eid))

    result = provision.uninstall("fish_s2")
    assert result["removed"] is False
    assert unregistered == [], "the engine was unregistered despite still being there"


# ── standing the writer down before the key moves ──────────────────────────

class TestTheWorkerIsStoodDownBeforeARotation:
    """The lock path stops the notebook worker; the rotation path did not.

    PRAGMA rekey can silently no-op under a concurrent write lock, and the
    notebook worker is the one thing in this process that writes to the
    database with no request behind it. The verification added to
    crypto.change_passphrase means a no-op is caught and reported as a 500
    rather than believed - so this was never corruption. It was the condition
    that PRODUCED the failure, left in place in one of the two routes that
    change the key, in the same file where the other one stands the worker
    down four hundred lines up.
    """

    @staticmethod
    def _spy(monkeypatch, trace, *, boom=False):
        """Record every await of quiesce into a shared ordering list."""
        import notebook_worker

        async def quiesce():
            trace.append("quiesce")
            if boom:
                raise RuntimeError("the worker will not stand down")

        monkeypatch.setattr(notebook_worker, "quiesce", quiesce)
        return trace

    @staticmethod
    def _rotating_vault(tmp_path, monkeypatch, client):
        import vault_state

        vdir = tmp_path / "rotate"
        vdir.mkdir()
        db_path = str(vdir / "app.db")
        monkeypatch.setattr(config, "DB_PATH", db_path)
        monkeypatch.setattr(database, "DB_PATH", db_path)
        vault_state.clear_key()
        assert client.post("/api/v1/vault/init", json={
            "passphrase": "seaside-orchid-9",
        }).status_code == 200
        return vdir

    def test_the_lock_route_stands_it_down(self, client, monkeypatch):
        """Ground control for the spy itself.

        The lock path has stood the worker down since it was written. If this
        does not record a call, the spy is not wired to the thing the route
        actually calls, and the two assertions below would pass on an empty
        list forever.
        """
        trace: list[str] = []
        self._spy(monkeypatch, trace)

        assert client.post("/api/v1/vault/lock").status_code == 200

        assert trace == ["quiesce"]

    def test_change_passphrase_stands_the_notebook_worker_down(
        self, client, tmp_path, monkeypatch,
    ):
        trace: list[str] = []
        self._spy(monkeypatch, trace)
        self._rotating_vault(tmp_path, monkeypatch, client)

        r = client.post("/api/v1/vault/change-passphrase", json={
            "old_passphrase": "seaside-orchid-9",
            "new_passphrase": "harbour-lantern-4",
        })

        assert r.status_code == 200, r.text
        assert "quiesce" in trace

    def test_the_worker_is_stood_down_before_the_rekey(
        self, client, tmp_path, monkeypatch,
    ):
        """Order, not presence. Standing the writer down AFTER the rekey is
        the same race with a tidier call graph."""
        trace: list[str] = []
        self._spy(monkeypatch, trace)
        self._rotating_vault(tmp_path, monkeypatch, client)

        real_rekey = database.rekey_db

        def watched(*a, **kw):
            trace.append("rekey")
            return real_rekey(*a, **kw)

        monkeypatch.setattr(database, "rekey_db", watched)

        r = client.post("/api/v1/vault/change-passphrase", json={
            "old_passphrase": "seaside-orchid-9",
            "new_passphrase": "harbour-lantern-4",
        })

        assert r.status_code == 200, r.text
        assert "rekey" in trace, "the database was never re-keyed"
        assert trace.index("quiesce") < trace.index("rekey")

    def test_change_passphrase_survives_a_worker_that_will_not_stand_down(
        self, client, tmp_path, monkeypatch,
    ):
        """Positive control on the swallow.

        A worker that refuses to stop is a reason to log and carry on, not a
        reason to refuse a passphrase change - the rotation verifies itself,
        and a user rotating away from a leaked passphrase must not be blocked
        by a background writer.
        """
        trace: list[str] = []
        self._spy(monkeypatch, trace, boom=True)
        self._rotating_vault(tmp_path, monkeypatch, client)

        r = client.post("/api/v1/vault/change-passphrase", json={
            "old_passphrase": "seaside-orchid-9",
            "new_passphrase": "harbour-lantern-4",
        })

        assert trace == ["quiesce"], "it has to have been tried"
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True


# ── the recipe a half-finished rotation leaves on the floor ────────────────

class TestACrashInsideARotation:
    def test_a_crash_between_shelving_and_shredding_leaves_no_recipe_for_the_old_key(
        self, client, tmp_path, monkeypatch,
    ):
        """Forty-two lines wide, and nothing swept it.

        change_passphrase renames the old salt, verifier and mirror to
        `<name>.bak-<ts>`, does its work, and shreds them at the end. Killed
        in between - which is a power cut, not a rare race - it leaves all
        three behind: together they are a working recipe for the key the
        rotation was replacing, in the clear, beside snapshots that key still
        opens. The launch sweep looked only at `app.db.rekey.bak-*`. The one
        place in the tree that knew these names was /vault/reset, which is
        not a route anybody takes to recover from a crash.
        """
        import crypto
        import vault_state
        from routers import vault as vault_router

        vdir = tmp_path / "crashed"
        vdir.mkdir()
        db_path = str(vdir / "app.db")
        monkeypatch.setattr(config, "DB_PATH", db_path)
        monkeypatch.setattr(database, "DB_PATH", db_path)
        vault_state.clear_key()

        assert client.post("/api/v1/vault/init", json={
            "passphrase": "seaside-orchid-9",
        }).status_code == 200
        key = vault_state.get_key()

        # The window, reproduced by hand rather than by killing a process:
        # the shelved names are what the rotation leaves on the floor.
        shelved = []
        # crypto.IDENTITY_NAMES, not a list retyped here. It has FOUR
        # entries and every hand-written copy in this file had three:
        # `kdf.json` was missing, production shreds `kdf.json.bak-<ts>`,
        # and /vault/init writes a live one - so the omission was a real
        # gap, not a style point. Looping over the constant also makes the
        # set comparison below sensitive in both directions.
        for name in crypto.IDENTITY_NAMES:
            live = vdir / name
            if not live.exists():
                live.write_bytes(b"identity material")
            aside = vdir / f"{name}.bak-1700000000"
            aside.write_bytes(live.read_bytes())
            shelved.append(aside)

        # Positive control: without this the test could pass over an empty
        # directory and prove nothing at all.
        assert all(p.exists() for p in shelved)
        # A set: the helper walks the identity NAMES in order and sorts
        # within each name, which is a stable order but not an alphabetical
        # one across the whole family.
        assert set(crypto.shelved_identity_paths(vdir)) == set(shelved)

        vault_router._sweep_crashed_rotation_backups(key)

        assert [p.name for p in shelved if p.exists()] == []
        # And the live identity is untouched: the sweep removes what was
        # superseded, not what the vault is currently using.
        assert (vdir / "salt.bin").exists()
        assert (vdir / "verifier.bin").exists()


class TestTheSweepKeepsTheRecipeForWhatItKeeps:
    """The salt is half of the recovery, and it was being shredded first.

    The database loop deliberately KEEPS an `app.db.rekey.bak-*` that does
    not open with the current key, and tells the user - at ERROR level -
    that it is still readable with their previous passphrase. What turns
    that passphrase into that key is the superseded salt and cost
    parameters, and the identity loop used to remove those before the
    database loop had decided anything.

    The result was worse than either outcome on its own: the copy opened for
    NOBODY, the message was false, and `/vault/status` names the file
    forever because the discard route refuses to act while a match is
    unreadable. A recoverable leak turned into unrecoverable garbage plus a
    permanent false alarm.
    """

    def test_the_identity_survives_while_a_backup_it_unlocks_does(
            self, client, tmp_path, monkeypatch) -> None:
        import config
        import crypto
        import database
        import vault_state
        from routers import vault as vault_router

        vdir = tmp_path / "stranded"
        vdir.mkdir()
        db_path = str(vdir / "app.db")
        monkeypatch.setattr(config, "DB_PATH", db_path)
        monkeypatch.setattr(database, "DB_PATH", db_path)
        vault_state.clear_key()

        assert client.post("/api/v1/vault/init", json={
            "passphrase": "seaside-orchid-9",
        }).status_code == 200
        key = vault_state.get_key()

        # A rotation backup that does NOT open with the current key - the
        # exact state the database loop is written to keep.
        stranded = vdir / "app.db.rekey.bak-1700000000"
        stranded.write_bytes(b"SQLite format 3" + bytes(201))

        shelved = []
        # crypto.IDENTITY_NAMES, not a list retyped here. It has FOUR
        # entries and every hand-written copy in this file had three:
        # `kdf.json` was missing, production shreds `kdf.json.bak-<ts>`,
        # and /vault/init writes a live one - so the omission was a real
        # gap, not a style point. Looping over the constant also makes the
        # set comparison below sensitive in both directions.
        for name in crypto.IDENTITY_NAMES:
            live = vdir / name
            if not live.exists():
                live.write_bytes(b"identity material")
            aside = vdir / f"{name}.bak-1700000000"
            aside.write_bytes(live.read_bytes())
            shelved.append(aside)
        assert all(p.exists() for p in shelved), "ground: they are on disk"

        left = vault_router._sweep_crashed_rotation_backups(key)

        assert stranded.name in left, (
            "ground: the sweep really did keep the database copy")
        assert [p.name for p in shelved if not p.exists()] == [], (
            "the sweep shredded the salt for a backup it kept, so the copy "
            "it promised was readable now opens for nobody")

    def test_with_nothing_left_behind_the_identity_still_goes(
            self, client, tmp_path, monkeypatch) -> None:
        """GROUND CONTROL, and the reason this is an ORDERING and not a
        removal. Superseded key material must still be swept when there is
        nothing left for it to open - otherwise the fix would simply stop
        cleaning up, which is the failure that looks like caution."""
        import config
        import crypto
        import database
        import vault_state
        from routers import vault as vault_router

        vdir = tmp_path / "clean"
        vdir.mkdir()
        db_path = str(vdir / "app.db")
        monkeypatch.setattr(config, "DB_PATH", db_path)
        monkeypatch.setattr(database, "DB_PATH", db_path)
        vault_state.clear_key()

        assert client.post("/api/v1/vault/init", json={
            "passphrase": "seaside-orchid-9",
        }).status_code == 200
        key = vault_state.get_key()

        shelved = []
        # crypto.IDENTITY_NAMES, not a list retyped here. It has FOUR
        # entries and every hand-written copy in this file had three:
        # `kdf.json` was missing, production shreds `kdf.json.bak-<ts>`,
        # and /vault/init writes a live one - so the omission was a real
        # gap, not a style point. Looping over the constant also makes the
        # set comparison below sensitive in both directions.
        for name in crypto.IDENTITY_NAMES:
            live = vdir / name
            if not live.exists():
                live.write_bytes(b"identity material")
            aside = vdir / f"{name}.bak-1700000000"
            aside.write_bytes(live.read_bytes())
            shelved.append(aside)
        assert all(p.exists() for p in shelved)

        left = vault_router._sweep_crashed_rotation_backups(key)

        assert left == [], "ground: no database copy was kept"
        assert [p.name for p in shelved if p.exists()] == []
        assert (vdir / "salt.bin").exists(), "the LIVE identity is untouched"



class TestAnEmptyBackupIsNotACopyOfAnything:
    """The ordering fix turned one leftover into a permanent one.

    `backup_encrypted` creates its destination before it copies a single
    page, so a rotation killed early leaves a ZERO-BYTE
    `app.db.rekey.bak-<ts>` - the likeliest crash artefact on that path,
    not an exotic one. `check_key` refuses a zero-byte file outright, and
    correctly: there is no header to read. But the database loop treats
    "did not open" as "readable with the previous passphrase", so it kept
    the empty file, reported it, and - since the reordering above makes a
    kept copy suppress the identity sweep - pinned the superseded salt and
    verifier beside the live vault on every unlock, for good.

    Before the reorder those identity files went away. So the fix that
    made a real backup recoverable made an empty one leak forever, which
    is the whole reason a `check_key` False is not enough on its own to
    decide the file is worth keeping.
    """

    def test_a_zero_byte_backup_is_removed_and_does_not_pin_the_identity(
            self, client, tmp_path, monkeypatch) -> None:
        import config
        import database
        import vault_state
        from routers import vault as vault_router

        vdir = tmp_path / "emptied"
        vdir.mkdir()
        db_path = str(vdir / "app.db")
        monkeypatch.setattr(config, "DB_PATH", db_path)
        monkeypatch.setattr(database, "DB_PATH", db_path)
        vault_state.clear_key()

        assert client.post("/api/v1/vault/init", json={
            "passphrase": "seaside-orchid-9",
        }).status_code == 200
        key = vault_state.get_key()

        stub = vdir / "app.db.rekey.bak-1700000000"
        stub.write_bytes(b"")
        assert stub.stat().st_size == 0, "ground: it really is empty"
        # GROUND CONTROL for the mechanism, not the outcome: this test only
        # says something if the file lands in the not-readable branch. If
        # check_key ever started accepting an empty file the sweep would
        # remove it as a redundant duplicate and this test would pass for a
        # reason it never intended to measure.
        assert not database.check_key(key, str(stub)), (
            "ground: an empty file does not open, so it reaches the branch "
            "that used to keep it")

        import crypto

        shelved = []
        # crypto.IDENTITY_NAMES, not a list retyped here. It has FOUR
        # entries and every hand-written copy in this file had three:
        # `kdf.json` was missing, production shreds `kdf.json.bak-<ts>`,
        # and /vault/init writes a live one - so the omission was a real
        # gap, not a style point. Looping over the constant also makes the
        # set comparison below sensitive in both directions.
        for name in crypto.IDENTITY_NAMES:
            live = vdir / name
            if not live.exists():
                live.write_bytes(b"identity material")
            aside = vdir / f"{name}.bak-1700000000"
            aside.write_bytes(live.read_bytes())
            shelved.append(aside)
        assert all(p.exists() for p in shelved), "ground: they are on disk"

        left = vault_router._sweep_crashed_rotation_backups(key)

        assert not stub.exists(), (
            "an empty file is not a copy of anything and there is nothing "
            "for a user to decide about it")
        assert left == [], (
            "nothing worth keeping was left behind, so /vault/status must "
            "not name a file forever")
        assert [p.name for p in shelved if p.exists()] == [], (
            "the empty stub pinned the superseded key material beside the "
            "live vault, on every unlock, for good")
        assert (vdir / "salt.bin").exists(), "the LIVE identity is untouched"

    def test_a_backup_with_bytes_in_it_is_still_kept(
            self, client, tmp_path, monkeypatch) -> None:
        """POSITIVE CONTROL, and the boundary is one byte wide.

        The rule being added is "empty", not "unreadable". A one-byte file
        does not open either, and it must still be kept and reported - the
        sweep does not get to delete what this app cannot read just because
        it is small.
        """
        import config
        import database
        import vault_state
        from routers import vault as vault_router

        vdir = tmp_path / "onebyte"
        vdir.mkdir()
        db_path = str(vdir / "app.db")
        monkeypatch.setattr(config, "DB_PATH", db_path)
        monkeypatch.setattr(database, "DB_PATH", db_path)
        vault_state.clear_key()

        assert client.post("/api/v1/vault/init", json={
            "passphrase": "seaside-orchid-9",
        }).status_code == 200
        key = vault_state.get_key()

        nearly = vdir / "app.db.rekey.bak-1700000001"
        nearly.write_bytes(b"S")
        assert not database.check_key(key, str(nearly))

        shelved = vdir / "salt.bin.bak-1700000001"
        shelved.write_bytes(b"identity material")

        left = vault_router._sweep_crashed_rotation_backups(key)

        assert nearly.exists(), "one byte is still a file this app cannot read"
        assert left == [nearly.name]
        assert shelved.exists(), (
            "and while it is kept, the recipe for it is kept too")


class TestTheIdentitySweepAsksAboutEveryShelf:
    """`left` names one family of vault copies. The folder can hold three.

    The ordering fix - databases first, identity only if nothing was kept -
    was right, and its gate was measured against `app.db.rekey.bak-*` alone.
    Two more families of COMPLETE copies live beside the vault and this app
    reports both on /vault/status: `app.db.premigrate.bak` (with its
    `.unreadable-<ts>` siblings) and `app.db.enc-tmp*`.

    No crash is needed to reach it. Lose `salt.bin` to a sync client or a
    partial restore, leave a zero-byte `app.db` with an orphaned encrypted
    copy beside it, and press the button /vault/init was widened to allow:
    `initialize()` shelves the surviving recovery mirror and cost file, and
    this function shredded them in the same request. The copy is then
    unopenable for good, and the app tells the user it "may be a vault under
    a different passphrase" - about a passphrase whose salt it destroyed.
    """

    def _a_vault(self, client, tmp_path, monkeypatch, name: str):
        import config
        import database
        import vault_state

        vdir = tmp_path / name
        vdir.mkdir()
        db_path = str(vdir / "app.db")
        monkeypatch.setattr(config, "DB_PATH", db_path)
        monkeypatch.setattr(database, "DB_PATH", db_path)
        vault_state.clear_key()
        assert client.post("/api/v1/vault/init", json={
            "passphrase": "seaside-orchid-9",
        }).status_code == 200
        return vdir, vault_state.get_key()

    def _shelved(self, vdir):
        import crypto

        out = []
        for name in crypto.IDENTITY_NAMES:
            live = vdir / name
            if not live.exists():
                live.write_bytes(b"identity material")
            aside = vdir / f"{name}.bak-1700000000"
            aside.write_bytes(live.read_bytes())
            out.append(aside)
        return out

    def test_an_unreadable_premigrate_copy_keeps_its_recipe(
            self, client, tmp_path, monkeypatch) -> None:
        from routers import vault as vault_router

        vdir, key = self._a_vault(client, tmp_path, monkeypatch, "premig")
        stranded = vdir / "app.db.premigrate.bak"
        stranded.write_bytes(b"SQLite format 3" + bytes(4096))
        shelved = self._shelved(vdir)
        assert all(p.exists() for p in shelved), "ground: on disk"

        left = vault_router._sweep_crashed_rotation_backups(key)

        assert left == [], (
            "ground: no ROTATION backup was kept, so the old gate would "
            "have shredded here")
        assert [p.name for p in shelved if not p.exists()] == [], (
            "the only key to a complete vault copy this app is keeping was "
            "destroyed in the same call that kept it")

    def test_an_unreadable_orphaned_copy_keeps_its_recipe(
            self, client, tmp_path, monkeypatch) -> None:
        from routers import vault as vault_router

        vdir, key = self._a_vault(client, tmp_path, monkeypatch, "orphan")
        stranded = vdir / "app.db.enc-tmp"
        stranded.write_bytes(b"SQLite format 3" + bytes(4096))
        shelved = self._shelved(vdir)

        left = vault_router._sweep_crashed_rotation_backups(key)

        assert left == []
        assert [p.name for p in shelved if not p.exists()] == []

    def test_a_copy_this_key_CAN_open_does_not_keep_anything(
            self, client, tmp_path, monkeypatch) -> None:
        """POSITIVE CONTROL, and it is what stops this becoming "never
        sweep".

        A copy that opens under the LIVE key needs no superseded identity to
        read it. If any copy at all were enough to keep the old salt, the
        premigrate snapshot - which is kept on purpose after a clean
        migration - would pin superseded key material beside the vault for
        as long as it sits there.
        """
        import database
        from routers import vault as vault_router

        vdir, key = self._a_vault(client, tmp_path, monkeypatch, "readable")
        twin = vdir / "app.db.premigrate.bak"
        database.backup_encrypted(str(twin), key)
        assert database.check_key(key, str(twin)), (
            "ground: this copy really does open with the live key")
        shelved = self._shelved(vdir)

        left = vault_router._sweep_crashed_rotation_backups(key)

        assert left == []
        assert [p.name for p in shelved if p.exists()] == [], (
            "a copy the live key opens needs no old salt, so keeping one "
            "would mean never sweeping at all")
