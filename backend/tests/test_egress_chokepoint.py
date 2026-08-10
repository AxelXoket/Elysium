"""One host, enforced in the app instead of remembered by every caller.

The promise held because every outbound call happened to build the same URL
from the same constant. That is a habit. One f-string with a host in it -
added by somebody here, or by a dependency that borrows this app's shared
client - and the conversation goes elsewhere with the Authorization header
attached, and nothing anywhere would have objected.

The suite's egress guard covers the TESTS. This covers the shipped
application, which is the half that matters to a user.

Distinct from the test guard in one important way: this one runs in
production, so a false refusal breaks a working app. Both directions are
tested here for that reason.
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

import config
import network_client


class TestWhatIsAllowed:
    def test_the_configured_provider_is_on_the_list(self) -> None:
        assert httpx.URL(config.OPENROUTER_BASE_URL).host \
            in network_client.allowed_hosts()

    def test_loopback_is_on_the_list(self) -> None:
        # The app IS a local server, and a user's proxy on 127.0.0.1 - Tor, a
        # local SOCKS tunnel - is the ordinary case rather than the exotic one.
        allowed = network_client.allowed_hosts()
        assert "127.0.0.1" in allowed
        assert "localhost" in allowed

    def test_the_list_is_derived_not_hardcoded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # tests/mock_provider.py exists so end-to-end smoke runs need no
        # network at all. A hardcoded openrouter.ai would have refused it, and
        # somebody would have deleted this check rather than the constant.
        monkeypatch.setattr(config, "OPENROUTER_BASE_URL",
                            "http://127.0.0.1:9797/api/v1")
        monkeypatch.setattr(network_client, "OPENROUTER_BASE_URL",
                            "http://127.0.0.1:9797/api/v1")
        assert "127.0.0.1" in network_client.allowed_hosts()


class TestWhatIsRefused:
    @pytest.mark.anyio
    @pytest.mark.parametrize("url", [
        "https://evil.example/collect",
        "https://api.openai.com/v1/chat/completions",
        "https://openrouter.ai.evil.example/api/v1",   # suffix, not the host
        "https://raw.githubusercontent.com/x/y",
    ])
    async def test_a_request_to_another_host_never_leaves(
        self, anyio_backend, url: str
    ) -> None:
        request = httpx.Request("POST", url)
        with pytest.raises(network_client.EgressRefused) as caught:
            await network_client._one_host_only(request)
        assert httpx.URL(url).host in str(caught.value)

    @pytest.mark.anyio
    async def test_the_provider_itself_passes(self, anyio_backend) -> None:
        # The control. A hook that refuses everything is not a chokepoint, it
        # is an outage, and it would pass every test above.
        request = httpx.Request(
            "POST", f"{config.OPENROUTER_BASE_URL}/chat/completions")
        await network_client._one_host_only(request)

    @pytest.mark.anyio
    async def test_it_refuses_before_anything_is_sent(
        self, anyio_backend, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # An event hook on "request" runs before the transport, so the refusal
        # costs a connection that was never opened - not a request sent and
        # then regretted.
        monkeypatch.setattr(network_client, "get_secret", lambda name: None)
        client = network_client._build_client()
        try:
            with pytest.raises(network_client.EgressRefused):
                await client.get("https://evil.example/collect")
        finally:
            await client.aclose()


class TestTheHookIsActuallyInstalled:
    """A chokepoint nothing routes through is a comment."""

    def test_the_direct_client_carries_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(network_client, "get_secret", lambda name: None)
        client = network_client._build_client()
        try:
            assert network_client._one_host_only \
                in client.event_hooks["request"]
        finally:
            client.close() if hasattr(client, "close") else None

    def test_the_proxied_client_carries_it_too(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Two constructor calls in one function is exactly where a hook gets
        # added to one and forgotten on the other - and the proxied branch is
        # the one a privacy-conscious user is on.
        monkeypatch.setattr(network_client, "get_secret",
                            lambda name: "http://127.0.0.1:9050")
        client = network_client._build_client()
        try:
            assert network_client._one_host_only \
                in client.event_hooks["request"]
        finally:
            client.close() if hasattr(client, "close") else None

    @pytest.mark.anyio
    async def test_a_proxied_client_still_checks_the_destination(
        self, anyio_backend, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # With a proxy configured the socket goes to the proxy, so a check at
        # the connection layer would see only 127.0.0.1 and wave everything
        # through. The hook reads the TARGET url, which is what the proxy is
        # being asked to fetch.
        monkeypatch.setattr(network_client, "get_secret",
                            lambda name: "http://127.0.0.1:9050")
        client = network_client._build_client()
        try:
            with pytest.raises(network_client.EgressRefused):
                await client.get("https://evil.example/collect")
        finally:
            await client.aclose()


class TestTheHostComparisonFailsInTheSafeDirection:
    """Anything that is not exactly the provider host is refused.

    A comparison this strict has edge cases in BOTH directions, and only one
    of them is acceptable. A form of the provider's own name that does not
    match costs a broken request; a form of somebody else's that does match
    costs the conversation. These pin which way each one falls.
    """

    @pytest.mark.anyio
    @pytest.mark.parametrize("host", [
        "openrouter.ai.",              # trailing dot: a distinct FQDN form
        "0penrouter.ai",               # a digit for a letter
        "xn--openroutr-o1a.ai",        # punycode homograph
        "openrouter.ai:443@evil.example",
    ])
    async def test_a_lookalike_is_refused(self, anyio_backend, host: str
                                          ) -> None:
        request = httpx.Request("GET", f"https://{host}/api/v1/models")
        with pytest.raises(network_client.EgressRefused):
            await network_client._one_host_only(request)

    @pytest.mark.anyio
    async def test_an_uppercase_provider_host_still_passes(
        self, anyio_backend
    ) -> None:
        # httpx normalises the host, and the hook lowercases anyway. If this
        # ever failed, a perfectly ordinary URL would stop working.
        request = httpx.Request("GET", "https://OPENROUTER.AI/api/v1/models")
        await network_client._one_host_only(request)

    @pytest.mark.anyio
    async def test_an_ipv6_loopback_literal_passes(self, anyio_backend
                                                    ) -> None:
        # A user's local proxy or the mock provider can be addressed this way.
        request = httpx.Request("GET", "http://[::1]:9797/api/v1/models")
        await network_client._one_host_only(request)

    @pytest.mark.anyio
    async def test_an_unusual_loopback_spelling_is_refused_not_guessed(
        self, anyio_backend
    ) -> None:
        # 0:0:0:0:0:0:0:1 IS ::1, and this refuses it. That is the safe
        # direction - a refusal breaks a request nobody makes, where guessing
        # at equivalences is how an allowlist grows a hole - and it is written
        # down here so the behaviour is chosen rather than discovered.
        request = httpx.Request("GET", "http://[0:0:0:0:0:0:0:2]/x")
        with pytest.raises(network_client.EgressRefused):
            await network_client._one_host_only(request)


class TestNothingElseBuildsAClient:
    """The chokepoint only chokes what routes through it.

    `_one_host_only` is installed by `network_client._build_client`. Any module
    that constructs its own `httpx.Client`/`AsyncClient` gets no hook, no
    `trust_env=False` and no vault-configured proxy - it simply leaves by
    another door, and every test above would still pass.

    Several router modules promise this in their header prose ("This module
    does NOT import httpx"). The only thing that checked it was one line in
    `verify/verify_phase5b.py`, which looks at a single file AND is never
    collected by pytest (nothing under backend/verify matches test_*.py, and
    the repo has no pytest config to widen that), so it has been running
    exactly never. Added 2026-08-10.
    """

    #: The one module allowed to build a client, plus the test tree, which
    #: fakes clients on purpose.
    ALLOWED = {"network_client.py"}

    def _sources(self) -> list[Path]:
        backend = Path(__file__).resolve().parents[1]
        skip = {"tests", "verify", ".venv", "__pycache__", "build", "dist"}
        return [
            p for p in backend.rglob("*.py")
            if not (skip & set(p.relative_to(backend).parts))
        ]

    def test_only_the_chokepoint_constructs_an_http_client(self) -> None:
        import re

        sources = self._sources()
        # Floor: an empty walk would report perfect compliance.
        assert len(sources) >= 40, f"only {len(sources)} modules walked"

        builder = re.compile(r"httpx\.(Async)?Client\s*\(")
        offenders = [
            str(p.name) for p in sources
            if p.name not in self.ALLOWED
            and builder.search(p.read_text(encoding="utf-8", errors="strict"))
        ]
        assert not offenders, (
            f"these build their own httpx client, bypassing the single "
            f"egress chokepoint: {sorted(offenders)}"
        )

    def test_the_check_can_actually_fail(self) -> None:
        """Guard the guard: the pattern must match the thing it forbids."""
        import re

        builder = re.compile(r"httpx\.(Async)?Client\s*\(")
        assert builder.search("c = httpx.AsyncClient(timeout=5)")
        assert builder.search("c = httpx.Client()")
        assert not builder.search("except httpx.TimeoutException:")
