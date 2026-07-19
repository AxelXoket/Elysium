"""Shared fixtures for backend API tests.

Strategy:
- A temporary SQLite file per test (config/database DB_PATH monkeypatched).
- Keyring is faked in-memory (no OS keyring access, no secrets touched).
- openrouter.complete is replaced per-test; captured payloads let tests
  assert exactly what would be sent to the provider.
- The keyring startup verification is bypassed so TestClient's lifespan
  does not depend on the host OS.
"""

import sys
import sqlite3
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# Fixed test key: tests bypass the passphrase→scrypt path entirely (that
# path has its own unit tests in test_vault.py) and pre-unlock the vault so
# the existing API tests run unchanged against an encrypted temp DB.
TEST_VAULT_KEY = bytes(range(32))


@pytest.fixture(autouse=True)
def _no_real_keyring(monkeypatch):
    """F1 guard - ALWAYS on: the OS keyring is machine-global, so the legacy
    migration helpers must never touch the developer's REAL Credential
    Manager entries from tests (reading is a leak; deleting would destroy the
    real API key). Migration tests monkeypatch their own fakes OVER these
    stubs deliberately."""
    import keyring_service

    monkeypatch.setattr(keyring_service, "read_legacy", lambda name: None)
    monkeypatch.setattr(keyring_service, "delete_legacy", lambda name: True)


@pytest.fixture()
def client(tmp_path, monkeypatch, request):
    import config
    import database
    import secrets_service
    import vault_state
    import main

    db_path = str(tmp_path / "test_app.db")
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(database, "DB_PATH", db_path)

    # Pre-unlock: the server starts locked by design; API tests are about the
    # data routes, so the vault is opened with a fixed key up front.
    # vault_state is process-global - register the clear FIRST (addfinalizer
    # always runs) so a failure anywhere in setup can never leak the key into
    # the next test.
    request.addfinalizer(vault_state.clear_key)
    vault_state.set_key(TEST_VAULT_KEY)
    database.init_db()

    # E6: image bytes live in the DB now; attachments_service no longer holds
    # an UPLOADS_DIR binding. config.UPLOADS_DIR still points migration tests
    # (legacy plaintext sweep) at a temp dir - the migration reads it
    # dynamically via `import config`.
    uploads_dir = str(tmp_path / "uploads")
    monkeypatch.setattr(config, "UPLOADS_DIR", uploads_dir)

    # E5: secrets live in the encrypted DB - seed the test key THERE. No
    # keyring fakes, no by-name router patches: every module reads the same
    # DB, which also closes the old real-Credential-Manager read leak the
    # keyring-era conftest had.
    secrets_service.set_secret("openrouter_api_key", "sk-test-key")

    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        yield c


@pytest.fixture()
def provider(monkeypatch):
    """Fake openrouter.complete; records every call's payload."""
    import routers.completions as completions_router

    class FakeProvider:
        def __init__(self):
            self.calls: list[dict] = []
            self.response_text = "fake assistant reply"
            self.error = None  # set to an OpenRouterError to fail the call

        async def _complete(self, messages, model_id, gen_params, provider):
            self.calls.append({
                "messages": messages,
                "model_id": model_id,
                "gen_params": gen_params,
                "provider": provider,
            })
            if self.error is not None:
                raise self.error
            return {"choices": [{"message": {"content": self.response_text}}]}

    fake = FakeProvider()
    monkeypatch.setattr(completions_router, "complete", fake._complete)
    return fake


def make_character(client, name="Testchar", first_mes="Hello there!") -> int:
    resp = client.post("/api/v1/characters", json={
        "name": name,
        "description": "A test character",
        "first_mes": first_mes,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def make_chat(client, character_id: int) -> int:
    resp = client.post("/api/v1/chats", json={"character_id": character_id})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def get_messages(client, chat_id: int) -> list[dict]:
    resp = client.get(f"/api/v1/chats/{chat_id}/messages")
    assert resp.status_code == 200, resp.text
    return resp.json()
