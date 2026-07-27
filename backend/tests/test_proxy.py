"""Proxy health/gate tests - currently the auth-failed reason distinction."""


def test_proxy_probe_4xx_uses_distinct_reason(monkeypatch):
    """A 4xx from the proxy probe must report `proxy_auth_failed`, not the
    OpenRouter `auth_failed` code (which would tell the user to check their
    API key for a proxy problem)."""
    import asyncio
    import httpx
    import proxy_health

    class FakeResponse:
        status_code = 407  # proxy authentication required

    class FakeClient:
        async def get(self, url, timeout=None):
            return FakeResponse()

    monkeypatch.setattr(proxy_health, "get_client", lambda: FakeClient())

    result = asyncio.run(proxy_health._probe())
    assert result["healthy"] is False
    assert result["reason"] == "proxy_auth_failed"


# ── Audit: httpx.ProxyError is a SIBLING of NetworkError, not a subclass ────


def test_proxy_error_is_not_a_network_error():
    """Pins the hierarchy assumption the classification below rests on."""
    import httpx
    assert not issubclass(httpx.ProxyError, httpx.NetworkError)
    assert issubclass(httpx.ProxyError, httpx.TransportError)


def test_probe_classifies_proxy_rejection_as_proxy_auth_failed(monkeypatch):
    """407 to CONNECT / SOCKS auth failure raises ProxyError, never a status.

    It used to fall through to the generic handler and surface as
    `unknown_error` -> "Something went wrong. Please try again.", so a user
    with a credentialled proxy was fully blocked from chatting and never told
    the proxy needed credentials.
    """
    import asyncio
    import httpx
    import proxy_health

    class FakeClient:
        async def get(self, url, timeout=None):
            raise httpx.ProxyError("Invalid username/password")

    monkeypatch.setattr(proxy_health, "get_client", lambda: FakeClient())
    result = asyncio.run(proxy_health._probe())
    assert result["healthy"] is False
    assert result["reason"] == "proxy_auth_failed"


def test_probe_still_reports_unreachable_for_connect_errors(monkeypatch):
    """Control: a closed port stays `proxy_unreachable`."""
    import asyncio
    import httpx
    import proxy_health

    class FakeClient:
        async def get(self, url, timeout=None):
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(proxy_health, "get_client", lambda: FakeClient())
    assert asyncio.run(proxy_health._probe())["reason"] == "proxy_unreachable"


# ── Audit: an in-flight probe must not re-poison a just-cleared cache ───────


def test_invalidate_during_an_in_flight_probe_wins(monkeypatch):
    """The settings change is newer than the probe - the probe must not write.

    Sequence: the settings panel has a probe in flight; the user fixes the
    proxy URL and saves, which calls invalidate_health_cache(). If the probe's
    continuation then wrote its (stale) verdict with a fresh timestamp, every
    completion for the next 30 s read that lie and the corrected proxy kept
    yielding 503.
    """
    import asyncio
    import proxy_health

    proxy_health.invalidate_health_cache()

    async def slow_evaluate():
        # The settings save lands while we are "in flight".
        proxy_health.invalidate_health_cache()
        return {"healthy": False, "latency_ms": None, "reason": "proxy_unreachable"}

    monkeypatch.setattr(proxy_health, "_evaluate", slow_evaluate)
    result = asyncio.run(proxy_health.check_proxy_health())

    # The caller still gets its own answer...
    assert result["reason"] == "proxy_unreachable"
    # ...but nothing was cached, so the next read re-probes.
    assert proxy_health._cache == {}


def test_uninterrupted_probe_still_caches(monkeypatch):
    """Control: without an invalidate, the TTL cache still works."""
    import asyncio
    import proxy_health

    proxy_health.invalidate_health_cache()
    calls = []

    async def fake_evaluate():
        calls.append(1)
        return {"healthy": True, "latency_ms": 5, "reason": None}

    monkeypatch.setattr(proxy_health, "_evaluate", fake_evaluate)

    async def run_twice():
        first = await proxy_health.check_proxy_health()
        second = await proxy_health.check_proxy_health()
        return first, second

    first, second = asyncio.run(run_twice())
    assert first["cached"] is False and second["cached"] is True
    assert len(calls) == 1
    proxy_health.invalidate_health_cache()


# ── Audit: every outbound path passes the SAME gate ─────────────────────────


def test_save_api_key_refuses_when_the_proxy_kill_switch_is_armed(
    client, monkeypatch,
):
    """proxy_required=1 with no proxy URL is "proxy_missing" - the state every
    other outbound path refuses. POST /settings/api-key used to validate the
    key against openrouter.ai anyway, sending the key and the user's real IP
    unproxied from the very screen where the key is typed.
    """
    import database
    import openrouter

    database.set_setting("proxy_required", "1")
    proxy_health_reset()

    called = []

    async def spy_validate(key):
        called.append(key)
        return "valid"

    monkeypatch.setattr(openrouter, "validate_api_key", spy_validate)

    resp = client.post("/api/v1/settings/api-key", json={"api_key": "sk-secret-leak"})
    assert resp.status_code == 503
    assert resp.json()["detail"] == "proxy_missing"
    assert called == [], "no outbound request may be made behind an armed gate"

    database.set_setting("proxy_required", "0")
    proxy_health_reset()


def test_save_api_key_works_when_the_gate_is_open(client, monkeypatch):
    """Control: the default (no kill-switch) path is unchanged."""
    import database
    import openrouter
    import routers.settings as settings_router

    database.set_setting("proxy_required", "0")
    proxy_health_reset()

    async def ok_validate(key):
        return "valid"

    monkeypatch.setattr(openrouter, "validate_api_key", ok_validate)
    monkeypatch.setattr(settings_router, "set_secret", lambda *a, **k: None)
    monkeypatch.setattr(
        settings_router.keyring_service, "delete_legacy", lambda *a, **k: None,
    )

    resp = client.post("/api/v1/settings/api-key", json={"api_key": "sk-fine"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["key_status"] == "valid"


def proxy_health_reset() -> None:
    import proxy_health
    proxy_health.invalidate_health_cache()


def test_completion_proxy_error_is_not_blamed_on_the_provider(monkeypatch):
    """A ProxyError from the user's own proxy must not read as an OpenRouter
    failure ("The provider returned an error. Please try again.") - that sent
    users retrying forever against a proxy that will never work."""
    import asyncio
    import httpx
    import openrouter

    class FakeClient:
        def stream(self, *a, **k):
            raise httpx.ProxyError("Proxy Server could not connect")

        async def post(self, *a, **k):
            raise httpx.ProxyError("407 Proxy Authentication Required")

    monkeypatch.setattr(openrouter, "get_client", lambda: FakeClient())
    monkeypatch.setattr(openrouter, "get_secret", lambda name: "sk-test")

    async def drain():
        async for _ in openrouter.complete_stream(
            [{"role": "user", "content": "hi"}], "test/model-1", {}, None,
        ):
            pass

    with __import__("pytest").raises(openrouter.OpenRouterError) as exc:
        asyncio.run(drain())
    assert exc.value.reason == "proxy_auth_failed"


# ── Audit HIGH: the kill-switch needs a write path that is not the URL ──────


def test_proxy_required_can_be_armed_without_retyping_the_url(client, monkeypatch):
    import database
    import routers.settings as settings_router

    monkeypatch.setattr(settings_router, "get_secret", lambda name, **k: "http://p:8080")

    resp = client.post("/api/v1/settings/proxy/required", json={"proxy_required": True})
    assert resp.status_code == 200, resp.text
    assert database.get_setting("proxy_required") == "1"

    resp = client.post("/api/v1/settings/proxy/required", json={"proxy_required": False})
    assert resp.status_code == 200
    assert database.get_setting("proxy_required") == "0"


def test_arming_without_a_proxy_is_refused(client, monkeypatch):
    """Otherwise every completion is blocked behind proxy_missing and the only
    screen that could undo it is the one that just did it."""
    import database
    import routers.settings as settings_router

    monkeypatch.setattr(settings_router, "get_secret", lambda name, **k: None)
    database.set_setting("proxy_required", "0")

    resp = client.post("/api/v1/settings/proxy/required", json={"proxy_required": True})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "proxy_url_required"
    assert database.get_setting("proxy_required") == "0"


def test_disarming_is_always_allowed(client, monkeypatch):
    import database
    import routers.settings as settings_router

    monkeypatch.setattr(settings_router, "get_secret", lambda name, **k: None)
    database.set_setting("proxy_required", "1")

    resp = client.post("/api/v1/settings/proxy/required", json={"proxy_required": False})
    assert resp.status_code == 200
    assert database.get_setting("proxy_required") == "0"


# ── Stop sequences belong in the VAULT, not in browser storage ──────────────
#
# They are the one generation setting that is user CONTENT - stop sequences are
# character names - so they are banned from localStorage by the S-09b privacy
# test and were kept in memory. The cost was retyping them every session, and
# losing them on every vault lock. The encrypted settings table already holds
# the content-bearing preferences (selected persona, API key).


def test_stop_sequences_round_trip(client):
    assert client.get("/api/v1/settings").json()["stop_sequences"] == []

    resp = client.post("/api/v1/settings/stop-sequences",
                       json={"stop_sequences": ["Human:", "Anna:"]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["stop_sequences"] == ["Human:", "Anna:"]
    assert client.get("/api/v1/settings").json()["stop_sequences"] == [
        "Human:", "Anna:",
    ]


def test_stop_sequences_are_capped_and_deduped_not_rejected(client):
    """A stale UI must not be able to 422 a settings save."""
    resp = client.post("/api/v1/settings/stop-sequences", json={
        "stop_sequences": ["A", "A", "B", "C", "D", "E", "F"],
    })
    assert resp.status_code == 200
    assert resp.json()["stop_sequences"] == ["A", "B", "C", "D"]


def test_an_over_long_stop_sequence_is_trimmed(client):
    resp = client.post("/api/v1/settings/stop-sequences",
                       json={"stop_sequences": ["x" * 500]})
    assert len(resp.json()["stop_sequences"][0]) == 100


def test_empty_entries_are_dropped(client):
    resp = client.post("/api/v1/settings/stop-sequences",
                       json={"stop_sequences": ["", "Human:", ""]})
    assert resp.json()["stop_sequences"] == ["Human:"]


def test_clearing_them_persists_as_empty(client):
    client.post("/api/v1/settings/stop-sequences",
                json={"stop_sequences": ["Human:"]})
    client.post("/api/v1/settings/stop-sequences", json={"stop_sequences": []})
    assert client.get("/api/v1/settings").json()["stop_sequences"] == []


def test_a_corrupt_value_reads_as_none_set(client):
    """Same rule selected_persona_id follows: a bad row must not break the
    whole settings response."""
    import database

    database.set_setting("stop_sequences", "{not json")
    resp = client.get("/api/v1/settings")
    assert resp.status_code == 200
    assert resp.json()["stop_sequences"] == []
