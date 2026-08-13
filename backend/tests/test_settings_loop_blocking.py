"""Audit KÖK 8, settings half: ten handlers, none of them off the loop.

routers/settings.py was the last router still doing all of its database and
secret-store work on the event loop. The rule it was missing is stated in
routers/personas.py's own docstring: "BEGIN IMMEDIATE olay dongusunde alinirsa
tum canli SSE stream'leri donar." Every write handler here takes SQLite's
writer lock, so saving a setting while a reply was streaming froze the reply -
and the settings panel is exactly the screen people open mid-conversation to
change the model or arm the proxy.

The discriminator is the one test_chat_read_loop_blocking.py established: a
heartbeat coroutine counts ticks while the handler runs. A real wall-clock
stall is injected into the module's OWN reference to the blocking call, so the
assertion is falsifiable - without the stall a fast handler finishes before the
first tick and the test would pass either way, proving nothing.

What this proves is that the loop stayed free during a slow call, not that
anyio specifically is what freed it. That is the right bar: it is a behavior
test, and it would keep passing if the mechanism were ever swapped.
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

import routers.settings as settings
from config import SECRET_PROXY_URL
from database import get_db

from tests.loop_guard import (
    MAX_FREEZE_S,
    MIN_TICKS,
    STALL_S as _STALL_S,
    longest_freeze as _longest_freeze,
    ticks_during as _ticks_during,
)


_PROXY = {
    "proxy_url": "http://127.0.0.1:9",
    "proxy_required": False,
    "proxy_alias": "test",
}


def _stall(monkeypatch, name: str):
    """Make routers.settings' own reference to `name` cost real wall-clock time.

    Patched on the router module rather than on database/secrets_service so the
    fixture setup and the assertions below still run at full speed, and so the
    stall fires whether or not the handler hands the call to a thread - a stall
    that only exists on the fixed path would make the test circular.
    """
    real = getattr(settings, name)

    def slow(*args, **kwargs):
        time.sleep(_STALL_S)
        return real(*args, **kwargs)

    monkeypatch.setattr(settings, name, slow)


@pytest.fixture()
def slow_db(monkeypatch):
    _stall(monkeypatch, "get_db")


@pytest.fixture()
def slow_set_setting(monkeypatch):
    _stall(monkeypatch, "set_setting")


@pytest.fixture()
def slow_image_output(monkeypatch):
    _stall(monkeypatch, "set_image_output_enabled")


@pytest.fixture()
def slow_delete_secret(monkeypatch):
    _stall(monkeypatch, "delete_secret")


@pytest.fixture()
def slow_set_secret(monkeypatch):
    _stall(monkeypatch, "set_secret")


@pytest.fixture()
def key_validation_succeeds(monkeypatch):
    """Take the two awaited network steps out of save_api_key's way.

    They are the reason this handler is the awkward one: the gate and the
    validation are coroutines that must stay on the loop, ahead of the storage
    that moves off it. Stubbing them leaves exactly the blocking part under
    test.
    """
    import openrouter

    async def gate_open():
        return None

    async def valid(_key):
        return "valid"

    monkeypatch.setattr(settings, "enforce_proxy_gate", gate_open)
    monkeypatch.setattr(openrouter, "validate_api_key", valid)



# ── the loop keeps running ───────────────────────────────────────────────────

@pytest.mark.anyio
async def test_reading_settings_does_not_freeze_the_loop(
    anyio_backend, client, slow_db
):
    """The panel opening must not stall a reply that is mid-sentence."""
    ticks, out = await _ticks_during(settings.get_settings())
    assert out["proxy_configured"] is False
    assert ticks >= MIN_TICKS, "the loop was frozen while settings were read"


@pytest.mark.anyio
async def test_saving_auto_lock_does_not_freeze_the_loop(
    anyio_backend, client, slow_set_setting
):
    body = settings.AutoLockBody(auto_lock_minutes=7)
    ticks, out = await _ticks_during(settings.set_auto_lock(body))
    assert out["auto_lock_minutes"] == 7
    assert ticks >= MIN_TICKS, "the loop was frozen while auto-lock was saved"


@pytest.mark.anyio
async def test_saving_stop_sequences_does_not_freeze_the_loop(
    anyio_backend, client, slow_set_setting
):
    body = settings.StopSequencesBody(stop_sequences=["Anna:", "Human:"])
    ticks, out = await _ticks_during(settings.save_stop_sequences(body))
    assert out["stop_sequences"] == ["Anna:", "Human:"]
    assert ticks >= MIN_TICKS, "the loop was frozen while stop sequences were saved"


@pytest.mark.anyio
async def test_saving_image_output_does_not_freeze_the_loop(
    anyio_backend, client, slow_image_output
):
    body = settings.ImageOutputBody(image_output_enabled=True)
    ticks, out = await _ticks_during(settings.set_image_output(body))
    assert out["image_output_enabled"] is True
    assert ticks >= MIN_TICKS, "the loop was frozen while image output was saved"


@pytest.mark.anyio
async def test_saving_a_proxy_does_not_freeze_the_loop(
    anyio_backend, client, slow_db
):
    body = settings.ProxyBody(**_PROXY)
    ticks, out = await _ticks_during(settings.save_proxy(body))
    assert out["ok"] is True
    assert ticks >= MIN_TICKS, "the loop was frozen while the proxy was saved"


@pytest.mark.anyio
async def test_arming_the_kill_switch_does_not_freeze_the_loop(
    anyio_backend, client, slow_db
):
    client.post("/api/v1/settings/proxy", json=_PROXY)
    body = settings.ProxyRequiredBody(proxy_required=True)
    ticks, out = await _ticks_during(settings.set_proxy_required(body))
    assert out["ok"] is True
    assert ticks >= MIN_TICKS, "the loop was frozen while the kill-switch was armed"


@pytest.mark.anyio
async def test_renaming_the_proxy_does_not_freeze_the_loop(
    anyio_backend, client, slow_db
):
    client.post("/api/v1/settings/proxy", json=_PROXY)
    body = settings.ProxyAliasBody(proxy_alias="work")
    ticks, out = await _ticks_during(settings.set_proxy_alias(body))
    assert out["proxy_alias"] == "work"
    assert ticks >= MIN_TICKS, "the loop was frozen while the proxy was renamed"


@pytest.mark.anyio
async def test_deleting_the_proxy_does_not_freeze_the_loop(
    anyio_backend, client, slow_db
):
    client.post("/api/v1/settings/proxy", json=_PROXY)
    ticks, out = await _ticks_during(settings.delete_proxy())
    assert out["ok"] is True
    assert ticks >= MIN_TICKS, "the loop was frozen while the proxy was deleted"


@pytest.mark.anyio
async def test_saving_the_api_key_does_not_freeze_the_loop(
    anyio_backend, client, slow_set_secret, key_validation_succeeds
):
    """The awkward one: two awaits on the loop, then storage off it."""
    body = settings.ApiKeyBody(api_key="sk-or-v1-testtesttesttest")
    ticks, out = await _ticks_during(settings.save_api_key(body))
    assert out == {"ok": True, "key_status": "valid"}
    assert ticks >= MIN_TICKS, "the loop was frozen while the API key was stored"


@pytest.mark.anyio
async def test_deleting_the_api_key_does_not_freeze_the_loop(
    anyio_backend, client, slow_delete_secret
):
    ticks, out = await _ticks_during(settings.delete_api_key())
    assert out["ok"] is True
    assert ticks >= MIN_TICKS, "the loop was frozen while the API key was deleted"


# ── and moving them did not change what they answer ──────────────────────────

def test_a_proxy_round_trip_still_reads_back(client):
    assert client.post("/api/v1/settings/proxy", json=_PROXY).status_code == 200
    body = client.get("/api/v1/settings").json()
    assert body["proxy_configured"] is True
    assert body["proxy_alias"] == "test"
    assert body["proxy_required"] is False

    assert client.delete("/api/v1/settings/proxy").status_code == 200
    body = client.get("/api/v1/settings").json()
    assert body["proxy_configured"] is False
    assert body["proxy_alias"] is None


def test_an_invalid_proxy_url_is_still_refused_before_anything_is_written(client):
    """Validation runs on the loop, ahead of the thread hop. It has to still
    fail first, or a bad URL reaches the vault and only breaks afterwards."""
    bad = dict(_PROXY, proxy_url="ftp://nope")
    assert client.post("/api/v1/settings/proxy", json=bad).status_code == 400
    assert client.get("/api/v1/settings").json()["proxy_configured"] is False


def test_arming_without_a_proxy_is_still_refused(client):
    """HTTPException has to survive the thread hop, or a 400 becomes a 500."""
    r = client.post("/api/v1/settings/proxy/required",
                    json={"proxy_required": True})
    assert r.status_code == 400
    assert r.json()["detail"] == "proxy_url_required"


def test_renaming_without_a_proxy_is_still_refused(client):
    r = client.post("/api/v1/settings/proxy/alias", json={"proxy_alias": "x"})
    assert r.status_code == 400
    assert r.json()["detail"] == "proxy_url_required"


def test_a_refused_arming_writes_nothing(client):
    """A rejected attempt leaves the flag exactly as it was.

    This is a regression check on the thread hop, not on the shared
    transaction: it would pass under the old two-statement form too, because
    the guard raised before the write either way. What it actually proves is
    that HTTPException still travels out of the worker thread and still stops
    the write. The shared transaction is proved by the race test at the bottom
    of this file, which is the only test here that fails against the old form.
    """
    assert client.get("/api/v1/settings").json()["proxy_required"] is False
    client.post("/api/v1/settings/proxy/required", json={"proxy_required": True})
    assert client.get("/api/v1/settings").json()["proxy_required"] is False


def test_auto_lock_and_stop_sequences_still_persist(client):
    assert client.post("/api/v1/settings/auto-lock",
                       json={"auto_lock_minutes": 9}).status_code == 200
    assert client.post("/api/v1/settings/stop-sequences",
                       json={"stop_sequences": ["Anna:"]}).status_code == 200
    body = client.get("/api/v1/settings").json()
    assert body["auto_lock_minutes"] == 9
    assert body["stop_sequences"] == ["Anna:"]


# ── the race the shared transaction exists to close ──────────────────────────

def test_arming_cannot_commit_against_a_proxy_that_was_just_deleted(
    client, monkeypatch
):
    """The guard read and the write are one BEGIN IMMEDIATE transaction.

    They used to be two autocommit statements on two connections. A
    DELETE /settings/proxy landing in between armed the kill-switch against a
    proxy that no longer existed: every completion then refused with
    proxy_missing, and the screen that could disarm it had just reported
    success. The window is widened here by stalling inside the transaction,
    right after the guard read, so the delete has every chance to land.

    The assertion is the invariant, not the winner: whichever request commits
    second, the vault must never end up armed with no proxy stored.
    """
    client.post("/api/v1/settings/proxy", json=_PROXY)

    real_get_secret = settings.get_secret
    inside = threading.Event()

    def stalling_get_secret(*args, **kwargs):
        value = real_get_secret(*args, **kwargs)
        inside.set()
        time.sleep(0.3)          # hold the writer lock across the delete
        return value

    monkeypatch.setattr(settings, "get_secret", stalling_get_secret)

    def arm():
        try:
            settings._set_proxy_required_sync(True)
        except Exception:        # a 400 here is a legitimate outcome
            pass

    t = threading.Thread(target=arm)
    t.start()
    assert inside.wait(5), "the arming transaction never opened"
    monkeypatch.setattr(settings, "get_secret", real_get_secret)
    settings._delete_proxy_sync()
    t.join(20)
    assert not t.is_alive(), "the arming transaction never finished"

    with get_db() as con:
        row = con.execute(
            "SELECT value FROM settings WHERE key = 'proxy_required'"
        ).fetchone()
        armed = row is not None and row["value"] == "1"
        stored = settings.get_secret(SECRET_PROXY_URL, conn=con) is not None

    assert not (armed and not stored), (
        "kill-switch armed with no proxy stored: the guard read and the write "
        "did not share a transaction"
    )
