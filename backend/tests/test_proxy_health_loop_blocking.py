"""Audit KÖK 8, the gate: enforce_proxy_gate read the vault on the event loop.

This one matters more than the settings handlers it follows. enforce_proxy_gate
runs at the top of EVERY completion, regenerate and edit, streaming or not, and
it is reached from three routers (completions, settings, models). Its first act
was a synchronous SQLCipher open to read one boolean, on the loop, milliseconds
before the outbound request - which is exactly when other streams are shipping
their next sentence.

Same discriminator as test_settings_loop_blocking.py: inject a real wall-clock
stall into the module's own reference to the blocking call, then count how many
times a heartbeat coroutine got control while the call was in flight. Without
the stall the assertion would be unfalsifiable, because the real read finishes
before the first tick either way.

The proxy_required=False path is used deliberately: it returns before any probe,
so no network fake is needed and the only thing that can consume wall-clock time
is the read under test.
"""
from __future__ import annotations

import asyncio
import time

import pytest

import proxy_health

_STALL_S = 0.12


@pytest.fixture()
def slow_db(monkeypatch):
    """Stall proxy_health's OWN reference to get_db.

    That reference only exists because this change hoisted the import out of
    _read_proxy_required's body; as a function-local import it was re-resolved
    from `database` on every call and could not be intercepted here without
    slowing every other module in the process too.
    """
    real = proxy_health.get_db

    def slow():
        time.sleep(_STALL_S)
        return real()

    monkeypatch.setattr(proxy_health, "get_db", slow)


async def _ticks_during(coro) -> tuple[int, object]:
    """Run `coro`, counting how many times the loop got control meanwhile."""
    ticks = 0

    async def heartbeat():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.005)
            ticks += 1

    beat = asyncio.ensure_future(heartbeat())
    try:
        await asyncio.sleep(0.02)          # let the heartbeat settle
        before = ticks
        result = await coro
        return ticks - before, result
    finally:
        beat.cancel()


# ── the loop keeps running ───────────────────────────────────────────────────

@pytest.mark.anyio
async def test_the_gate_does_not_freeze_the_loop(anyio_backend, client, slow_db):
    """Every message pays this. It must not cost the other streams anything."""
    ticks, out = await _ticks_during(proxy_health.enforce_proxy_gate())
    assert out is None                      # an open gate returns nothing
    assert ticks > 1, "the loop was frozen while the proxy gate read the vault"


@pytest.mark.anyio
async def test_reading_the_flag_does_not_freeze_the_loop(
    anyio_backend, client, slow_db
):
    ticks, out = await _ticks_during(proxy_health._read_proxy_required())
    assert out is False
    assert ticks > 1, "the loop was frozen while proxy_required was read"


# ── and the gate still says the same things ──────────────────────────────────

def test_the_gate_stays_open_when_the_kill_switch_is_off(client):
    """Nothing configured, switch off: completions must flow."""
    assert client.get("/api/v1/settings").json()["proxy_required"] is False
    r = client.get("/api/v1/settings/proxy/health")
    assert r.status_code == 200
    assert r.json()["healthy"] is True
    assert r.json()["reason"] is None


def test_the_gate_still_refuses_when_armed_with_no_proxy(client, monkeypatch):
    """The reason code is a contract the frontend maps to a message.

    Arming normally requires a stored proxy, so the flag is written directly
    here to reach the proxy_missing state the gate is supposed to refuse.
    """
    from database import set_setting

    set_setting("proxy_required", "1")
    proxy_health.invalidate_health_cache()

    health = client.get("/api/v1/settings/proxy/health").json()
    assert health["healthy"] is False
    assert health["reason"] == "proxy_missing"
