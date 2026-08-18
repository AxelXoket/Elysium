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
