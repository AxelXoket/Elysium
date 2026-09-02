"""v1.1 Faz 0 backend hardening: busy_timeout, loopback bind, GET /settings
single-connection, create-chat length caps, purge isolation."""

import config
import database


def _make_char(client) -> int:
    r = client.post("/api/v1/characters", json={"name": "C", "first_mes": "hi"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ── FB9: busy_timeout ────────────────────────────────────────────────────────

def test_get_db_sets_busy_timeout(client):
    with database.get_db() as con:
        val = con.execute("PRAGMA busy_timeout").fetchone()[0]
    assert val == 15000


# ── I4: loopback-only binding constants ──────────────────────────────────────

def test_backend_binds_loopback_only():
    # The API must never bind a routable interface. Dev (start_backend.bat) and
    # the packaged launcher both use this host.
    assert config.BACKEND_HOST in ("127.0.0.1", "localhost", "::1")
    import run_app
    assert run_app.HOST == "127.0.0.1"


# ── FB10: create-chat length caps (parity with rename) ───────────────────────

def test_create_chat_rejects_overlong_title(client):
    char_id = _make_char(client)
    resp = client.post(
        "/api/v1/chats",
        json={"character_id": char_id, "title": "x" * 201},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "title_too_long"


def test_create_chat_accepts_max_title(client):
    char_id = _make_char(client)
    resp = client.post(
        "/api/v1/chats",
        json={"character_id": char_id, "title": "x" * 200},
    )
    assert resp.status_code == 201, resp.text


def test_create_chat_rejects_overlong_model_id(client):
    char_id = _make_char(client)
    resp = client.post(
        "/api/v1/chats",
        json={"character_id": char_id, "model_id": "m" * 301},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "model_id_too_long"


# ── FB11: GET /settings unchanged behavior (now single connection) ───────────

def test_get_settings_shape_unchanged(client):
    resp = client.get("/api/v1/settings")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {
        "api_key_set", "proxy_required", "proxy_configured",
        "proxy_alias", "selected_persona_id",
        # Added deliberately: stop sequences are character names, i.e. user
        # content, so browser storage is closed to them - which is why they used
        # to be in-memory only and had to be retyped every session and after
        # every vault lock. The encrypted settings table keeps the privacy rule
        # and drops the retyping. The frontend schema defaults the field, so an
        # older client reading a newer server still parses.
        "stop_sequences",
        # Added deliberately: whether a model may answer with a PICTURE changes
        # what is sent to the provider, so it belongs in the vault with the other
        # request-shaping settings rather than in browser storage. Off unless the
        # row says otherwise, so every existing vault reads exactly as before.
        "image_output_enabled",
        # Added deliberately: how long the vault stays unlocked with nothing
        # happening. In the vault rather than in browser storage because it is
        # a protection setting, and browser storage is readable without the
        # passphrase - a lock timeout somebody else can read and change is not
        # a lock timeout. 0 (never) unless the row says otherwise.
        "auto_lock_minutes",
        # FAZ 3: the capture-exclusion switch. Lives in the vault, not in
        # browser storage - a protection setting readable without the
        # passphrase is not one.
        "screen_privacy_enabled",
        # v1.2: which model is chosen. A model id ("anthropic/claude-3.5-
        # sonnet") is a NAME a person reads on screen, not a number, so it
        # moved here out of localStorage's elysium-ui-state blob - the other
        # two selections in that blob (chat, character) are bare ids and the
        # rule permits those to stay.
        "selected_model_id",
    }
    # conftest seeds an api key in the vault.
    assert body["api_key_set"] is True


# ── FB4: purge isolation - a failing purge must not fail unlock ──────────────

def test_unlock_survives_purge_failure(monkeypatch, tmp_path):
    """Drive a real init/unlock while purge_stale_staged raises; unlock must
    still succeed (bootstrap keeps schema-init fatal but purge non-fatal)."""
    import importlib
    import attachments_service
    import vault_state
    import main
    from fastapi.testclient import TestClient

    db_path = str(tmp_path / "purge.db")
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(database, "DB_PATH", db_path)
    monkeypatch.setattr(config, "UPLOADS_DIR", str(tmp_path / "uploads"))

    def boom(*a, **k):
        raise RuntimeError("simulated purge lock timeout")

    monkeypatch.setattr(attachments_service, "purge_stale_staged", boom)

    vault_state.clear_key()
    try:
        with TestClient(main.app) as c:
            r = c.post("/api/v1/vault/init", json={"passphrase": "purge-test-pass"})
            assert r.status_code == 200, r.text  # unlock succeeded despite boom
            # And the DB is usable (schema built).
            assert c.get("/api/v1/characters").status_code == 200
    finally:
        vault_state.clear_key()
    importlib.reload  # keep the import referenced; no-op
