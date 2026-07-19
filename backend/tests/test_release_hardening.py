"""Release-hardening regression tests (v0.5.0 audit follow-ups).

Locks in three invariants the audits flagged as untested:
  1. The 423 vault gate covers EVERY data route automatically - a future
     router added without thought cannot silently ship ungated.
  2. Gate prefix edge cases stay closed (/api/v1vault, dot-dot smuggling).
  3. DATA_DIR resolution: env override wins, frozen mode goes to
     %LOCALAPPDATA%\\Elysium, dev stays beside the code.
"""

import re
import sys
from pathlib import Path

from starlette.routing import Route

import vault_state

# Same fixed key the client fixture pre-unlocks with (kept local so this file
# does not depend on tests/ being an importable package).
TEST_VAULT_KEY = bytes(range(32))


def _api_data_routes(app) -> list[tuple[str, str]]:
    """(method, concrete_path) for every /api/v1 route that is NOT a vault
    route, with every path param filled with a dummy id."""
    out = []
    for route in app.routes:
        if not isinstance(route, Route):
            continue
        path = route.path
        if not path.startswith("/api/v1") or path.startswith("/api/v1/vault"):
            continue
        concrete = re.sub(r"\{[^}]+\}", "1", path)
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            out.append((method, concrete))
    return out


def test_gate_covers_every_data_route_while_locked(client):
    """Iterate the real route table: locked vault => 423 on every data route.
    (Not a handful of spot checks - a newly added router is covered by
    construction or this test fails.)"""
    import main

    routes = _api_data_routes(main.app)
    assert len(routes) >= 30, f"route walk looks broken: {routes}"

    vault_state.clear_key()
    try:
        for method, path in routes:
            resp = client.request(method, path)
            assert resp.status_code == 423, (
                f"{method} {path} answered {resp.status_code} while locked"
            )
            assert resp.json() == {"detail": "vault_locked"}
    finally:
        vault_state.set_key(TEST_VAULT_KEY)


def test_gate_prefix_edge_cases_stay_closed(client):
    """Lookalike prefixes must NOT pass the gate while locked. The dot-dot
    path asserts the end-to-end property: however the stack treats it
    (client normalization, gate ".." exclusion, router literalism), it can
    never answer with data while locked."""
    vault_state.clear_key()
    try:
        # Not a vault route at all (no slash) - must be gated.
        assert client.get("/api/v1vault").status_code == 423
        # Dot-dot smuggling around the vault exemption.
        assert client.get("/api/v1/vault/../chats").status_code == 423
        # The real vault route stays reachable while locked (it is the way in).
        assert client.get("/api/v1/vault/status").status_code == 200
    finally:
        vault_state.set_key(TEST_VAULT_KEY)


def test_data_dir_env_override_wins(monkeypatch, tmp_path):
    import config

    override = str(tmp_path / "custom-data")
    monkeypatch.setenv("ELYSIUM_DATA_DIR", override)
    assert config._resolve_data_dir() == Path(override)


def test_data_dir_frozen_goes_to_localappdata(monkeypatch, tmp_path):
    import config

    monkeypatch.delenv("ELYSIUM_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert config._resolve_data_dir() == tmp_path / "Elysium"


def test_data_dir_dev_stays_beside_code(monkeypatch):
    import config

    monkeypatch.delenv("ELYSIUM_DATA_DIR", raising=False)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert config._resolve_data_dir() == Path(config.__file__).resolve().parent
