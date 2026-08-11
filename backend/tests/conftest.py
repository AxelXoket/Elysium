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

from tests import egress_guard, error_wire_recorder

# At import time, not inside a fixture, and deliberately.
#
# pytest imports a directory's conftest before its sibling test modules, so
# this lands before any module-level `from fastapi.testclient import
# TestClient` binds the original name. Four test files build their own client
# rather than using the `client` fixture below, and wiring the recorder into
# that fixture alone would have missed every one of them.
error_wire_recorder.install()


# Fixed test key: tests bypass the passphrase→scrypt path entirely (that
# path has its own unit tests in test_vault.py) and pre-unlock the vault so
# the existing API tests run unchanged against an encrypted temp DB.
TEST_VAULT_KEY = bytes(range(32))


@pytest.fixture(autouse=True)
def _no_egress(monkeypatch):
    """ALWAYS on: nothing in this suite may leave the machine.

    The app promises a single egress host, and until this existed the promise
    was enforced by nobody. Two tests built their own socket traps around one
    request each; the other ~1500 could have dialled anywhere in silence.

    Trapped at the socket layer rather than at httpx, because the things most
    likely to grow a new outbound call - a provisioning download, a subprocess
    helper, a new dependency - do not go through httpx. They all go through
    getaddrinfo and connect.

    Safe for the suite as it stands: TestClient runs ASGI in-process and opens
    no socket at all, and the voice worker talks over stdio pipes.
    """
    egress_guard.install(monkeypatch)


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
def db(tmp_path, monkeypatch, request):
    """A keyed, schema-built encrypted DB with NO HTTP app on top.

    Split out of `client` because several tests want an unlocked vault and an
    initialised schema but must NOT start a live server: they drive migration
    or recovery functions directly, and one of them breaks Path.iterdir
    process-wide - something a TestClient has no reason to survive. Before this
    split those tests hand-copied these same lines; test_data_loss_guards.py's
    local `unlocked_db` fixture said so in its own docstring.

    Pre-unlock: the server starts locked by design; the vault is opened with a
    fixed key up front. vault_state is process-global - register the clear
    FIRST (addfinalizer always runs) so a failure anywhere in setup can never
    leak the key into the next test.
    """
    import config
    import database
    import vault_state

    db_path = str(tmp_path / "test_app.db")
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(database, "DB_PATH", db_path)

    request.addfinalizer(vault_state.clear_key)
    vault_state.set_key(TEST_VAULT_KEY)
    database.init_db()
    return db_path


@pytest.fixture()
def client(db, tmp_path, monkeypatch):
    # Builds ON the `db` fixture (DB + unlocked vault) and adds the HTTP stack.
    # The teardown order is preserved by the dependency: this fixture's
    # TestClient context exits before `db`'s clear-key finaliser runs.
    import config
    import secrets_service
    import main

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

        async def _complete(self, messages, model_id, gen_params, provider,
                            **kwargs):
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


def make_persona(client, display_name="Nova", description="", select=False) -> int:
    resp = client.post("/api/v1/personas", json={
        "display_name": display_name,
        "description": description,
    })
    assert resp.status_code == 201, resp.text
    pid = resp.json()["id"]
    if select:
        r = client.post(f"/api/v1/personas/{pid}/select")
        assert r.status_code == 200, r.text
    return pid


@pytest.fixture(autouse=True)
def _voice_tag_caches_reset():
    """voice_tags memoises two answers for the process lifetime (the sticky
    voice-ever flag and the engine tag-capability). Tests that flip either
    must not poison their neighbours."""
    import voice_tags

    voice_tags.reset_stripping_cache()
    voice_tags.reset_tag_support_cache()
    yield
    voice_tags.reset_stripping_cache()
    voice_tags.reset_tag_support_cache()


@pytest.fixture(autouse=True)
def _model_cache_reset():
    """openrouter._model_cache is a module-level dict that outlives the temp
    vault every test gets, so a test that seeds it hands its answer to
    whatever runs next.

    This used to be pasted into each file that seeded the cache, which is the
    same shape as the proxy health-cache bug: correct in the files that
    remembered, and silently wrong the first time somebody wrote
    `openrouter._model_cache[...] = ...` in a file that did not. Guarding it
    once, here, is the only version that cannot be forgotten."""
    import openrouter

    openrouter.invalidate_model_cache()
    yield
    openrouter.invalidate_model_cache()


@pytest.fixture(autouse=True)
def _isolated_voice_registry(tmp_path_factory, monkeypatch):
    """No test may see the DEVELOPER'S real voice registry.

    runtimes.json carries `extra_roots`, and scan_roots() honours it on every
    call - so a machine with a real engine registered (as this one now has)
    leaks that model into every discovery test that only redirected
    TTS_MODELS_DIR. Same class of guard as `_no_real_keyring`: the suite must
    describe the code, not the machine it runs on.
    """
    import config

    reg = tmp_path_factory.mktemp("voice-registry") / "runtimes.json"
    monkeypatch.setattr(config, "TTS_RUNTIMES_PATH", str(reg), raising=False)
    yield
