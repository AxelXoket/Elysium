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
        "initialized": False, "unlocked": False,
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

    def _locking_stream(messages, model_id, gen_params, provider):
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
