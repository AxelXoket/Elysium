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
