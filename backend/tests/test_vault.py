"""Vault tests: crypto units, endpoint lifecycle, 423 gate, migration, rekey.

The crypto unit tests use tiny scrypt cost via monkeypatch where speed
matters is NOT done - scrypt at production params runs in ~100ms, and these
tests call it a handful of times; keeping the real params also guards the
byte-stability contract.
"""

import sqlite3 as std_sqlite3  # stdlib on purpose: builds PLAINTEXT fixtures
from pathlib import Path

import pytest

import crypto
from tests.conftest import TEST_VAULT_KEY


# ---------------------------------------------------------------------------
# crypto.py units
# ---------------------------------------------------------------------------

def test_derive_key_is_deterministic_and_salted():
    salt = crypto.new_salt()
    k1 = crypto.derive_key("correct horse", salt)
    k2 = crypto.derive_key("correct horse", salt)
    k3 = crypto.derive_key("correct horse", crypto.new_salt())
    assert k1 == k2
    assert len(k1) == 32
    assert k1 != k3  # different salt → different key


def test_verifier_roundtrip_and_rejection():
    key = crypto.derive_key("pw", crypto.new_salt())
    other = crypto.derive_key("pw2", crypto.new_salt())
    ver = crypto.make_verifier(key)
    assert crypto.check_verifier(key, ver)
    assert not crypto.check_verifier(other, ver)


def test_keyvault_initialize_unlock_cycle(tmp_path):
    vault = crypto.KeyVault(tmp_path)
    assert not vault.is_initialized()
    key = vault.initialize("hunter2hunter2")
    assert vault.is_initialized()
    assert vault.unlock("hunter2hunter2") == key
    assert vault.unlock("wrong") is None


def test_keyvault_initialize_shelves_existing_identity(tmp_path):
    vault = crypto.KeyVault(tmp_path)
    vault.initialize("first-pass-1")
    old_salt = vault.salt_path.read_bytes()
    vault.initialize("second-pass-2")
    # Old identity is shelved (bak file), never deleted.
    baks = list(tmp_path.glob("salt.bin.bak-*"))
    assert baks and baks[0].read_bytes() == old_salt


def test_recover_with_db_uses_db_as_authority(tmp_path):
    vault = crypto.KeyVault(tmp_path)
    key = vault.initialize("recover-me-123")
    # Simulate verifier corruption.
    vault.verifier_path.write_bytes(b"\x00" * 32)
    assert vault.unlock("recover-me-123") is None
    recovered = vault.recover_with_db("recover-me-123", lambda k: k == key)
    assert recovered == key
    # Identity healed: normal unlock works again.
    assert vault.unlock("recover-me-123") == key


def test_change_passphrase_writes_new_files_and_rekeys(tmp_path):
    vault = crypto.KeyVault(tmp_path)
    vault.initialize("old-pass-111")
    seen: list[bytes] = []
    new_key = vault.change_passphrase(
        "new-pass-222", seen.append, verify_fn=lambda k: True
    )
    assert seen == [new_key]
    assert vault.unlock("new-pass-222") == new_key
    assert vault.unlock("old-pass-111") is None
    # No .new leftovers after a clean change.
    assert not list(tmp_path.glob("*.new"))


def test_change_passphrase_aborts_when_rekey_did_not_take(tmp_path):
    """The CRITICAL guard: a silently-no-op rekey (verify_fn False) must NOT
    swap identity files - the old passphrase must still open the vault, and
    no .new files may linger."""
    vault = crypto.KeyVault(tmp_path)
    old_key = vault.initialize("old-pass-111")
    old_salt = vault.salt_path.read_bytes()
    with pytest.raises(RuntimeError, match="rekey_did_not_take"):
        vault.change_passphrase(
            "new-pass-222", rekey_fn=lambda k: None, verify_fn=lambda k: False
        )
    # Old identity untouched → old passphrase still derives the same key.
    assert vault.salt_path.read_bytes() == old_salt
    assert vault.unlock("old-pass-111") == old_key
    assert vault.unlock("new-pass-222") is None
    assert not list(tmp_path.glob("*.new"))


# ---------------------------------------------------------------------------
# Endpoint lifecycle + gate (uses the pre-unlocked `client` fixture, then
# manipulates lock state through the API itself)
# ---------------------------------------------------------------------------

def test_status_reports_unlocked_with_fixture(client):
    resp = client.get("/api/v1/vault/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["unlocked"] is True


def test_lock_engages_the_423_gate(client):
    assert client.post("/api/v1/vault/lock").status_code == 200
    resp = client.get("/api/v1/characters")
    assert resp.status_code == 423
    assert resp.json()["detail"] == "vault_locked"
    # /vault/status stays reachable while locked.
    assert client.get("/api/v1/vault/status").status_code == 200


def test_healthz_bypasses_the_gate(client):
    client.post("/api/v1/vault/lock")
    assert client.get("/healthz").status_code == 200


def test_full_passphrase_lifecycle_on_fresh_vault(client, tmp_path, monkeypatch):
    """init → data write → lock → wrong unlock 401 → right unlock → data read."""
    import config
    import database
    import vault_state

    # A fresh, never-initialized vault in its own directory.
    vdir = tmp_path / "fresh"
    vdir.mkdir()
    db_path = str(vdir / "app.db")
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(database, "DB_PATH", db_path)
    vault_state.clear_key()

    assert client.get("/api/v1/vault/status").json() == {
        # orphaned_copy: a stranded app.db.enc-tmp is a full readable copy of
        # the vault that is never removed automatically, so status reports it
        # rather than leaving a log line as the only trace (audit KÖK 18).
        "initialized": False, "unlocked": False, "orphaned_copy": False,
        # A pre-vault plaintext copy is a full unencrypted database. It has
        # to be a reported STATE, not a one-off banner, or it stays
        # invisible forever - which is what happened.
        "plaintext_backups": [],
        # null, not False: while locked we cannot open the copy to
        # find out, and "unknown" must not look like "unreadable".
        "orphaned_copy_readable": None,
        # The 0-byte file crash recovery moves aside. It appeared in no route,
        # no field and no screen, and nothing in the app could remove it -
        # reported here for the same reason as the two above, one size smaller.
        "empty_stub": False,
    }
    r = client.post("/api/v1/vault/init", json={"passphrase": "seaside-orchid-9"})
    assert r.status_code == 200 and r.json()["migrated"] is False

    char = client.post("/api/v1/characters", json={
        "name": "VaultChar", "description": "d", "first_mes": "hi",
    })
    assert char.status_code == 201

    client.post("/api/v1/vault/lock")
    assert client.get("/api/v1/characters").status_code == 423
    bad = client.post("/api/v1/vault/unlock", json={"passphrase": "nope-nope-1"})
    assert bad.status_code == 401 and bad.json()["detail"] == "wrong_passphrase"
    ok = client.post("/api/v1/vault/unlock", json={"passphrase": "seaside-orchid-9"})
    assert ok.status_code == 200
    names = [c["name"] for c in client.get("/api/v1/characters").json()]
    assert "VaultChar" in names

    # On-disk bytes are NOT a readable SQLite database without the key.
    header = Path(db_path).read_bytes()[:16]
    assert header != b"SQLite format 3\x00"


def test_init_rejects_short_passphrase(client, tmp_path, monkeypatch):
    import config
    import database
    import vault_state
    vdir = tmp_path / "short"
    vdir.mkdir()
    monkeypatch.setattr(config, "DB_PATH", str(vdir / "app.db"))
    monkeypatch.setattr(database, "DB_PATH", str(vdir / "app.db"))
    vault_state.clear_key()
    r = client.post("/api/v1/vault/init", json={"passphrase": "short"})
    assert r.status_code == 422


def test_init_migrates_plaintext_db_with_backup(client, tmp_path, monkeypatch):
    import config
    import database
    import vault_state

    vdir = tmp_path / "migrate"
    vdir.mkdir()
    db_path = vdir / "app.db"
    # Build a PLAINTEXT pre-vault database with real rows (stdlib sqlite3).
    con = std_sqlite3.connect(str(db_path))
    con.executescript(
        "CREATE TABLE characters (id INTEGER PRIMARY KEY, name TEXT NOT NULL,"
        " description TEXT NOT NULL DEFAULT '', personality TEXT NOT NULL DEFAULT '',"
        " scenario TEXT NOT NULL DEFAULT '', first_mes TEXT NOT NULL DEFAULT '',"
        " mes_example TEXT NOT NULL DEFAULT '', system_prompt TEXT NOT NULL DEFAULT '',"
        " post_history_instruction TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '[]',"
        " raw_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL DEFAULT (datetime('now')));"
        "INSERT INTO characters (name) VALUES ('LegacyChar');"
    )
    con.commit()
    con.close()

    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(database, "DB_PATH", str(db_path))
    vault_state.clear_key()

    r = client.post("/api/v1/vault/init", json={"passphrase": "migrate-me-77"})
    assert r.status_code == 200 and r.json()["migrated"] is True
    # Data survived into the encrypted DB…
    names = [c["name"] for c in client.get("/api/v1/characters").json()]
    assert "LegacyChar" in names
    # …the plaintext original is preserved as a backup…
    baks = list(vdir.glob("app.db.plain.bak-*"))
    assert len(baks) == 1
    # …and the live file is no longer plaintext.
    assert Path(db_path).read_bytes()[:16] != b"SQLite format 3\x00"


def test_change_passphrase_endpoint_rekeys(client, tmp_path, monkeypatch):
    import config
    import database
    import vault_state
    vdir = tmp_path / "rekey"
    vdir.mkdir()
    monkeypatch.setattr(config, "DB_PATH", str(vdir / "app.db"))
    monkeypatch.setattr(database, "DB_PATH", str(vdir / "app.db"))
    vault_state.clear_key()

    client.post("/api/v1/vault/init", json={"passphrase": "first-pass-000"})
    client.post("/api/v1/characters", json={
        "name": "RekeyChar", "description": "d", "first_mes": "hi",
    })
    r = client.post("/api/v1/vault/change-passphrase", json={
        "old_passphrase": "first-pass-000", "new_passphrase": "second-pass-999",
    })
    assert r.status_code == 200
    # Old passphrase dead, new one opens, data intact, no backup leftover.
    client.post("/api/v1/vault/lock")
    assert client.post(
        "/api/v1/vault/unlock", json={"passphrase": "first-pass-000"}
    ).status_code == 401
    assert client.post(
        "/api/v1/vault/unlock", json={"passphrase": "second-pass-999"}
    ).status_code == 200
    names = [c["name"] for c in client.get("/api/v1/characters").json()]
    assert "RekeyChar" in names
    assert not list(vdir.glob("app.db.rekey.bak-*"))


# ---------------------------------------------------------------------------
# database.py vault plumbing units
# ---------------------------------------------------------------------------

def test_check_key_and_wrong_key(client):
    import database
    assert database.check_key(TEST_VAULT_KEY)
    assert not database.check_key(bytes(32))


def test_get_db_raises_while_locked(client):
    import database
    import vault_state
    vault_state.clear_key()
    with pytest.raises(vault_state.VaultLockedError):
        with database.get_db():
            pass


def _fresh_vault(client, tmp_path, monkeypatch, name):
    """Point config/database at a fresh, never-initialized vault dir."""
    import config
    import database
    import vault_state
    vdir = tmp_path / name
    vdir.mkdir()
    monkeypatch.setattr(config, "DB_PATH", str(vdir / "app.db"))
    monkeypatch.setattr(database, "DB_PATH", str(vdir / "app.db"))
    vault_state.clear_key()
    return vdir


def test_recover_through_endpoint_with_corrupt_verifier(client, tmp_path, monkeypatch):
    """End-to-end DB-validated recovery: a corrupt verifier must NOT lock the
    user out - unlock falls back to opening the real encrypted DB."""
    import crypto
    import config
    vdir = _fresh_vault(client, tmp_path, monkeypatch, "recover")
    client.post("/api/v1/vault/init", json={"passphrase": "recover-me-123"})
    client.post("/api/v1/characters", json={
        "name": "RecoverChar", "description": "d", "first_mes": "hi",
    })
    client.post("/api/v1/vault/lock")

    # Corrupt the verifier on disk.
    vault = crypto.KeyVault(vdir)
    vault.verifier_path.write_bytes(b"\x00" * 32)

    r = client.post("/api/v1/vault/unlock", json={"passphrase": "recover-me-123"})
    assert r.status_code == 200
    names = [c["name"] for c in client.get("/api/v1/characters").json()]
    assert "RecoverChar" in names
    # Verifier healed: a normal unlock works again next time.
    client.post("/api/v1/vault/lock")
    assert client.post(
        "/api/v1/vault/unlock", json={"passphrase": "recover-me-123"}
    ).status_code == 200


def test_double_init_returns_409(client, tmp_path, monkeypatch):
    _fresh_vault(client, tmp_path, monkeypatch, "double")
    assert client.post(
        "/api/v1/vault/init", json={"passphrase": "first-init-000"}
    ).status_code == 200
    assert client.post(
        "/api/v1/vault/init", json={"passphrase": "second-init-111"}
    ).status_code == 409


def test_init_over_encrypted_db_without_identity_returns_409(client, tmp_path, monkeypatch):
    import crypto
    import config
    vdir = _fresh_vault(client, tmp_path, monkeypatch, "orphanid")
    client.post("/api/v1/vault/init", json={"passphrase": "the-pass-222"})
    client.post("/api/v1/vault/lock")
    # Simulate lost identity files over a live encrypted DB.
    crypto.KeyVault(vdir).salt_path.unlink()
    crypto.KeyVault(vdir).verifier_path.unlink()
    r = client.post("/api/v1/vault/init", json={"passphrase": "the-pass-222"})
    assert r.status_code == 409
    assert r.json()["detail"] == "encrypted_db_without_identity"


def test_change_passphrase_survives_and_verifies_over_real_db(client, tmp_path, monkeypatch):
    """The endpoint wires the post-rekey verify_fn=check_key against the real
    DB; a normal change must succeed and the new key must genuinely open it."""
    vdir = _fresh_vault(client, tmp_path, monkeypatch, "rekeyreal")
    client.post("/api/v1/vault/init", json={"passphrase": "first-pass-abc"})
    client.post("/api/v1/characters", json={
        "name": "RealRekey", "description": "d", "first_mes": "hi",
    })
    r = client.post("/api/v1/vault/change-passphrase", json={
        "old_passphrase": "first-pass-abc", "new_passphrase": "second-pass-xyz",
    })
    assert r.status_code == 200
    # No rekey backup leftover on success.
    assert not list(vdir.glob("app.db.rekey.bak-*"))
    client.post("/api/v1/vault/lock")
    assert client.post(
        "/api/v1/vault/unlock", json={"passphrase": "second-pass-xyz"}
    ).status_code == 200
    assert "RealRekey" in [c["name"] for c in client.get("/api/v1/characters").json()]


def test_passphrase_too_long_is_rejected_without_echo(client, tmp_path, monkeypatch):
    _fresh_vault(client, tmp_path, monkeypatch, "toolong")
    long_pass = "a" * 2000
    r = client.post("/api/v1/vault/init", json={"passphrase": long_pass})
    assert r.status_code == 422
    assert r.json()["detail"] == "passphrase_too_long"
    # The rejected passphrase is not echoed anywhere in the body.
    assert long_pass not in r.text


# ── v1.1 FB5: change-passphrase parity (recover fallback, locked-window,
# bootstrap re-lock) ─────────────────────────────────────────────────────────

def test_fb5a_change_recovers_from_corrupt_verifier(client, tmp_path, monkeypatch):
    """FB5a: a corrupt verifier.bin must not reject a CORRECT old passphrase -
    the DB is the authority (parity with unlock's recover_with_db)."""
    vdir = _fresh_vault(client, tmp_path, monkeypatch, "fb5a")
    client.post("/api/v1/vault/init", json={"passphrase": "first-pass-abc"})
    client.post("/api/v1/characters", json={
        "name": "Fb5aChar", "description": "d", "first_mes": "hi",
    })
    client.post("/api/v1/vault/lock")

    # Corrupt the verifier so vault.unlock() returns None.
    (vdir / "verifier.bin").write_bytes(b"garbage-garbage-garbage")

    r = client.post("/api/v1/vault/change-passphrase", json={
        "old_passphrase": "first-pass-abc", "new_passphrase": "second-pass-xyz",
    })
    assert r.status_code == 200, r.text
    client.post("/api/v1/vault/lock")
    assert client.post(
        "/api/v1/vault/unlock", json={"passphrase": "second-pass-xyz"}
    ).status_code == 200


def test_fb5b_locked_vault_stays_locked_through_rekey(client, tmp_path, monkeypatch):
    """FB5b: a change issued while LOCKED must keep the 423 gate shut for the
    whole rekey window (no early set_key(old_key))."""
    import database
    import vault_state

    _fresh_vault(client, tmp_path, monkeypatch, "fb5b")
    client.post("/api/v1/vault/init", json={"passphrase": "first-pass-abc"})
    client.post("/api/v1/vault/lock")
    assert not vault_state.is_unlocked()

    real_rekey = database.rekey_db
    observed = {"unlocked_during_rekey": None}

    def spy_rekey(new_key, current_key=None):
        observed["unlocked_during_rekey"] = vault_state.is_unlocked()
        return real_rekey(new_key, current_key=current_key)

    monkeypatch.setattr(database, "rekey_db", spy_rekey)
    r = client.post("/api/v1/vault/change-passphrase", json={
        "old_passphrase": "first-pass-abc", "new_passphrase": "second-pass-xyz",
    })
    assert r.status_code == 200, r.text
    # The gate was still shut at rekey time.
    assert observed["unlocked_during_rekey"] is False


def test_fb5b_unlocked_change_still_works(client, tmp_path, monkeypatch):
    """FB5b regression: an UNLOCKED change still succeeds and stays unlocked."""
    import vault_state

    _fresh_vault(client, tmp_path, monkeypatch, "fb5bU")
    client.post("/api/v1/vault/init", json={"passphrase": "first-pass-abc"})
    assert vault_state.is_unlocked()
    r = client.post("/api/v1/vault/change-passphrase", json={
        "old_passphrase": "first-pass-abc", "new_passphrase": "second-pass-xyz",
    })
    assert r.status_code == 200
    assert vault_state.is_unlocked()  # unlocked callers stay unlocked


def test_fb5c_bootstrap_failure_relocks(client, tmp_path, monkeypatch):
    """FB5c: change SUCCEEDS but the locked-path bootstrap fails -> re-lock so
    no key stays resident; a subsequent unlock self-heals."""
    import routers.vault as vault_router
    import vault_state

    _fresh_vault(client, tmp_path, monkeypatch, "fb5c")
    client.post("/api/v1/vault/init", json={"passphrase": "first-pass-abc"})
    client.post("/api/v1/vault/lock")

    real_bootstrap = vault_router._bootstrap_unlocked
    monkeypatch.setattr(
        vault_router, "_bootstrap_unlocked",
        lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    r = client.post("/api/v1/vault/change-passphrase", json={
        "old_passphrase": "first-pass-abc", "new_passphrase": "second-pass-xyz",
    })
    assert r.status_code == 200, r.text  # the change itself succeeded
    assert not vault_state.is_unlocked()  # re-locked, no resident key

    # Restore ONLY the bootstrap (monkeypatch.undo would also revert DB_PATH).
    monkeypatch.setattr(vault_router, "_bootstrap_unlocked", real_bootstrap)
    # The new passphrase unlocks and self-heals.
    assert client.post(
        "/api/v1/vault/unlock", json={"passphrase": "second-pass-xyz"}
    ).status_code == 200


def test_fb5_change_failure_keeps_vault_locked(client, tmp_path, monkeypatch):
    """A rekey that fails must not leak the key: vault stays LOCKED, old
    passphrase still unlocks."""
    import crypto
    import vault_state

    _fresh_vault(client, tmp_path, monkeypatch, "fb5fail")
    client.post("/api/v1/vault/init", json={"passphrase": "first-pass-abc"})
    client.post("/api/v1/vault/lock")

    real_change = crypto.KeyVault.change_passphrase

    def boom(self, *a, **k):
        raise RuntimeError("rekey exploded")

    monkeypatch.setattr(crypto.KeyVault, "change_passphrase", boom)
    r = client.post("/api/v1/vault/change-passphrase", json={
        "old_passphrase": "first-pass-abc", "new_passphrase": "second-pass-xyz",
    })
    assert r.status_code == 500
    assert r.json()["detail"] == "change_passphrase_failed"
    assert not vault_state.is_unlocked()  # no set_key leak

    # Restore only change_passphrase (not DB_PATH) for the follow-up unlock.
    monkeypatch.setattr(crypto.KeyVault, "change_passphrase", real_change)
    assert client.post(
        "/api/v1/vault/unlock", json={"passphrase": "first-pass-abc"}
    ).status_code == 200  # old passphrase still valid


def test_rename_with_retry_recovers_after_a_held_handle_releases(tmp_path):
    """Proves the Windows file-lock retry actually recovers: hold an OS handle
    on the source (blocks os.replace on Windows), release it from a timer, and
    confirm _rename_with_retry ultimately completes. Migration uses this same
    helper, so this covers the 'migration bricks under a file lock' scenario."""
    import threading
    import database

    src = tmp_path / "src.bin"
    dest = tmp_path / "dest.bin"
    src.write_bytes(b"payload")

    handle = open(src, "rb")  # noqa: SIM115 - held deliberately, released below
    released = threading.Event()

    def _release():
        handle.close()
        released.set()

    timer = threading.Timer(0.25, _release)
    timer.start()
    try:
        database._rename_with_retry(src, dest, attempts=20)
    finally:
        timer.cancel()
        if not released.is_set():
            handle.close()

    assert dest.exists()
    assert not src.exists()
    assert dest.read_bytes() == b"payload"


def test_lock_mid_stream_yields_423_event_and_ends_cleanly(client, tmp_path, monkeypatch):
    """A /vault/lock landing mid-SSE (here: the key cleared as the generator's
    last act) must surface a clean 423 vault_locked event, NOT a 500/traceback,
    and must not raise out of the generator."""
    import json
    import vault_state
    import routers.completions as completions_router
    from conftest import make_character, make_chat

    char_id = make_character(client)
    chat_id = make_chat(client, char_id)

    def _locking_stream(messages, model_id, gen_params, provider, **kwargs):
        async def gen():
            yield "Half a "
            yield "reply"
            # Simulate the vault locking right before the done-path DB write.
            vault_state.clear_key()
        return gen()

    monkeypatch.setattr(completions_router, "complete_stream", _locking_stream)

    events = []
    with client.stream(
        "POST", f"/api/v1/chats/{chat_id}/complete/stream",
        json={"message": "hi", "model_id": "test/model-1"},
    ) as resp:
        assert resp.status_code == 200  # headers already sent before the lock
        for line in resp.iter_lines():
            line = line.strip()
            if line.startswith("data:"):
                events.append(json.loads(line[len("data:"):].strip()))

    # The deltas streamed, then a dedicated vault_locked error - no internal_error.
    types = [e["type"] for e in events]
    assert "error" in types
    err = next(e for e in events if e["type"] == "error")
    assert err["code"] == "vault_locked"
    assert err["status"] == 423
    assert "internal_error" not in [e.get("code") for e in events]

    # Re-unlock and confirm the app is still usable (no corruption from the abort).
    vault_state.set_key(TEST_VAULT_KEY)
    assert client.get("/api/v1/characters").status_code == 200


# ── The voice model is warmed at unlock, not on the first press of Speak ────
#
# Loading is slow: a cold Fish S2 pays a torch.compile the worker itself calls
# "first compile is slow", and TTS_LOAD_TIMEOUT_S is 180. Left lazy, all of it
# landed on the first Speak press - the one interaction that should feel
# instant. Unlock is the right moment because resolving the model reads its
# saved parameters out of the vault, which does not exist before then.


def test_unlock_starts_the_voice_preload_in_the_background(monkeypatch):
    import threading
    import routers.vault as vault_router

    started = threading.Event()

    def fake_thread(target, name=None, daemon=None):
        class T:
            def start(self_inner):
                started.set()
                assert daemon is True, "unlock must not wait on a GPU"
                assert name == "tts-preload"
        return T()

    monkeypatch.setattr(threading, "Thread", fake_thread)
    vault_router._preload_voice_model()
    assert started.is_set()


def test_the_preload_never_raises(monkeypatch):
    """No engine, no GPU, a renamed model folder, a worker that will not
    start - the UI reports all of them from /tts/active. None is a reason to
    disturb an unlock that otherwise worked."""
    import routers.vault as vault_router
    import database

    def explode(*a, **k):
        raise RuntimeError("no GPU on this machine")

    monkeypatch.setattr(database, "get_setting", explode)
    # Runs the worker body inline by making Thread call it immediately.
    import threading

    def inline_thread(target, name=None, daemon=None):
        class T:
            def start(self_inner):
                target()
        return T()

    monkeypatch.setattr(threading, "Thread", inline_thread)
    vault_router._preload_voice_model()   # must not raise


def test_the_preload_skips_when_no_model_is_chosen(client, monkeypatch):
    import threading
    import routers.vault as vault_router
    import database
    import routers.tts_runtime as runtime

    database.set_setting(runtime.SETTING_ACTIVE_UID, "")
    resolved = []
    monkeypatch.setattr(runtime, "_resolve", lambda uid: resolved.append(uid))

    def inline_thread(target, name=None, daemon=None):
        class T:
            def start(self_inner):
                target()
        return T()

    monkeypatch.setattr(threading, "Thread", inline_thread)
    vault_router._preload_voice_model()
    assert resolved == [], "nothing chosen - nothing to warm"


# ---------------------------------------------------------------------------
# The guards nothing was watching
#
# Added 2026-08-10. Each of these pins a line that an adversarial pass found
# could be deleted with the whole suite staying green. They are not new
# behaviour: the code already does all three. What was missing was anything
# that would notice if it stopped.
# ---------------------------------------------------------------------------


def test_a_wrong_old_passphrase_is_refused_even_while_the_vault_is_open(
    client, tmp_path, monkeypatch,
):
    """The old passphrase is CHECKED, not taken from the open session.

    database.rekey_db falls back to the live session key when it is handed
    current_key=None, so the 401 above it is the only thing between "prove you
    know the current passphrase" and "anything that can reach this route while
    the vault is open may rotate it". Every other test in the suite sends a
    CORRECT old passphrase, so deleting that guard changed nothing anywhere.
    """
    import vault_state

    _fresh_vault(client, tmp_path, monkeypatch, "wrongold")
    assert client.post(
        "/api/v1/vault/init", json={"passphrase": "first-pass-abc"},
    ).status_code == 200
    # The precondition that makes this sharp: a key IS resident, so a fallback
    # to it would succeed and look like an ordinary rotation.
    assert vault_state.is_unlocked()

    r = client.post("/api/v1/vault/change-passphrase", json={
        "old_passphrase": "not-the-passphrase",
        "new_passphrase": "second-pass-xyz",
    })
    assert r.status_code == 401, r.text
    assert r.json()["detail"] == "wrong_passphrase"

    # And the rotation really did not happen underneath the refusal.
    client.post("/api/v1/vault/lock")
    assert client.post(
        "/api/v1/vault/unlock", json={"passphrase": "second-pass-xyz"},
    ).status_code == 401, "the passphrase the caller tried to set must not work"
    assert client.post(
        "/api/v1/vault/unlock", json={"passphrase": "first-pass-abc"},
    ).status_code == 200, "the real passphrase must still open it"


def test_init_relocks_when_the_bootstrap_fails(client, tmp_path, monkeypatch):
    """A failed init must not leave the key resident.

    The same shape is already pinned for change-passphrase (FB5c). Init and
    unlock have the identical except-block and had no test at all, so a 500
    could have been reported to the user while the vault was in fact open.
    """
    import routers.vault as vault_router
    import vault_state

    _fresh_vault(client, tmp_path, monkeypatch, "initboom")
    monkeypatch.setattr(
        vault_router, "_bootstrap_unlocked",
        lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    r = client.post("/api/v1/vault/init", json={"passphrase": "first-pass-abc"})
    assert r.status_code == 500, r.text
    assert not vault_state.is_unlocked(), (
        "init reported failure while the vault was still open"
    )


def test_unlock_relocks_when_the_bootstrap_fails(client, tmp_path, monkeypatch):
    """The unlock half of the same guard, and the more dangerous one: it runs
    on every launch, not only on the one that creates the vault."""
    import routers.vault as vault_router
    import vault_state

    _fresh_vault(client, tmp_path, monkeypatch, "unlockboom")
    assert client.post(
        "/api/v1/vault/init", json={"passphrase": "first-pass-abc"},
    ).status_code == 200
    client.post("/api/v1/vault/lock")
    assert not vault_state.is_unlocked()

    monkeypatch.setattr(
        vault_router, "_bootstrap_unlocked",
        lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    r = client.post(
        "/api/v1/vault/unlock", json={"passphrase": "first-pass-abc"},
    )
    assert r.status_code == 500, r.text
    assert not vault_state.is_unlocked(), (
        "unlock reported failure while the key stayed resident"
    )
