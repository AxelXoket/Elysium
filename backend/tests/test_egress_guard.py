"""The guard that keeps the one-host promise, and the tests that keep the guard.

README says the app talks to exactly one host. Before this, nothing enforced
it: two tests built their own socket traps around one request each, and the
other fifteen hundred could have dialled anywhere without a single assertion
noticing. That is not a promise, it is a habit.

A suite-wide guard is only worth having if it actually fires, so this file
attacks it: every library the app could plausibly grow into, every socket API
that reaches the network by a different door, and the loopback traffic that
must keep working or the whole suite stops.
"""
from __future__ import annotations

import asyncio
import socket

import pytest

from tests import egress_guard
from tests.egress_guard import EgressAttempt, is_local


class TestItStopsEveryDoorToTheNetwork:
    def test_httpx_cannot_reach_out(self) -> None:
        import httpx

        with pytest.raises(EgressAttempt, match="evil.example"):
            httpx.Client(timeout=1.0).get("https://evil.example/x")

    def test_urllib_cannot_reach_out(self) -> None:
        # Provisioning uses urllib, not httpx. A guard that only knew about
        # the app's current HTTP client would miss the download path entirely.
        import urllib.error
        import urllib.request

        with pytest.raises((EgressAttempt, urllib.error.URLError)):
            urllib.request.urlopen("https://evil.example/x", timeout=1)

    def test_a_bare_socket_cannot_reach_out(self) -> None:
        with pytest.raises(EgressAttempt, match="evil.example"):
            socket.socket().connect(("evil.example", 443))

    def test_create_connection_cannot_reach_out(self) -> None:
        # Its own entry point: it resolves AND connects inside the C layer, so
        # patching getaddrinfo and socket.connect does not cover it.
        with pytest.raises(EgressAttempt, match="evil.example"):
            socket.create_connection(("evil.example", 443), timeout=1)

    def test_connect_ex_cannot_reach_out(self) -> None:
        # Returns an error code instead of raising, so a port-scan style probe
        # would have slipped past a guard that only wrapped connect().
        with pytest.raises(EgressAttempt, match="evil.example"):
            socket.socket().connect_ex(("evil.example", 443))

    def test_resolving_a_name_is_enough_to_fail(self) -> None:
        # Failing at resolution, not just at connect, is what makes a DNS
        # lookup - which already tells a network something - count as egress.
        with pytest.raises(EgressAttempt, match="evil.example"):
            socket.getaddrinfo("evil.example", 443)

    def test_the_provider_itself_is_not_exempt(self) -> None:
        # openrouter.ai is deliberately NOT allowed. Every test uses a fake,
        # so an attempt to resolve the real thing means a live, billable
        # request is about to happen.
        with pytest.raises(EgressAttempt, match="openrouter"):
            socket.getaddrinfo("openrouter.ai", 443)


class TestItLetsTheAppTalkToItself:
    @pytest.mark.parametrize("host", [
        "127.0.0.1", "localhost", "::1", "testserver", "127.0.0.53",
    ])
    def test_loopback_still_resolves(self, host: str) -> None:
        # The app IS a local server: the test client, the health probe and the
        # launcher all speak to it. A guard that blocked this would not be
        # strict, it would be broken.
        assert is_local(host) is True

    def test_the_test_client_still_works(self, client) -> None:
        assert client.get("/api/v1/vault/status").status_code == 200

    def test_a_real_loopback_lookup_is_not_refused(self) -> None:
        assert socket.getaddrinfo("127.0.0.1", 80)


class TestTheAllowListIsNotAccidentallyWide:
    @pytest.mark.parametrize("host", [
        "evil.example",
        "localhost.evil.example",     # suffix, not the host
        "127.0.0.1.evil.example",     # prefix that only looks like loopback
        "notlocalhost",
        "openrouter.ai",
        "example.com",
    ])
    def test_a_host_that_merely_resembles_loopback_is_refused(
        self, host: str
    ) -> None:
        assert is_local(host) is False


class TestTheAddressShapesThatAreNotHosts:
    """Not every connect() address contains a host to check.

    The first cut read `address[0] if isinstance(address, tuple) else address`,
    which turns an AF_UNIX socket path into a "hostname": it fails IP parsing,
    fails the loopback set, and comes out looking like somebody's server. The
    docstring already claimed unix sockets were exempt. The code disagreed.
    """

    def test_a_filesystem_path_is_not_read_as_a_host(self) -> None:
        assert egress_guard._target("/tmp/app.sock") is None
        assert is_local(egress_guard._target("/tmp/app.sock")) is True

    def test_a_bytes_path_is_not_read_as_a_host(self) -> None:
        assert egress_guard._target(b"\x00abstract-socket") is None

    def test_an_empty_address_tuple_does_not_raise(self) -> None:
        assert egress_guard._target(()) is None

    def test_a_normal_inet_address_still_yields_its_host(self) -> None:
        # The control: exempting the shapes above must not exempt the one that
        # actually carries a destination.
        assert egress_guard._target(("evil.example", 443)) == "evil.example"
        assert is_local(egress_guard._target(("evil.example", 443))) is False

    def test_a_hostname_given_as_bytes_is_still_checked(self) -> None:
        # getaddrinfo accepts bytes. str(b"evil.example") is
        # "b'evil.example'", which matches nothing and would have been refused
        # by accident rather than on purpose - and the reverse mistake, a
        # bytes loopback name, would have been refused wrongly.
        assert is_local(b"evil.example") is False
        assert is_local(b"localhost") is True


class TestTheAsyncioLoopIsCoveredToo:
    """asyncio does not have to come through socket.socket.connect.

    On Windows the default loop is the Proactor one, and its sock_connect
    reaches _overlapped.WSAConnect in C, past every socket-module patch. Today
    nothing gets there because every caller resolves a name first; that is an
    ordering, not a guarantee.
    """

    @pytest.mark.anyio
    async def test_the_loop_refuses_a_remote_create_connection(
        self, anyio_backend
    ) -> None:
        loop = asyncio.get_running_loop()
        with pytest.raises(EgressAttempt):
            await loop.create_connection(asyncio.Protocol, "evil.example", 443)

    @pytest.mark.anyio
    async def test_the_loop_refuses_a_pre_resolved_remote_address(
        self, anyio_backend
    ) -> None:
        # The case the socket-layer patches cannot see: no name to resolve, so
        # getaddrinfo never runs.
        loop = asyncio.get_running_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setblocking(False)
        try:
            with pytest.raises(EgressAttempt):
                await loop.sock_connect(sock, ("93.184.216.34", 443))
        finally:
            sock.close()
