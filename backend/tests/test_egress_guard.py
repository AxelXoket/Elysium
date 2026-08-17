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

#: Everything in the socket module that turns a name or an address into
#: another one. Asked of the module rather than listed, so that a resolver
#: nobody thought of still has to be answered for.
_RESOLVER_PREFIXES = ("gethost", "getfqdn", "getnameinfo", "getaddrinfo")

#: The one match that is not a lookup, and why. The reason is not a comment:
#: a test below calls it and proves the sentence.
NOT_A_LOOKUP = {
    "gethostname": "takes no host argument - it asks this machine its own name",
}

RESOLVERS_THE_SOCKET_MODULE_OFFERS = sorted(
    name for name in dir(socket)
    if name.startswith(_RESOLVER_PREFIXES)
    and name not in NOT_A_LOOKUP
    and callable(getattr(socket, name, None))
)

#: How each one is handed a host, since they do not agree on the shape.
_FOREIGN_CALL = {
    "getaddrinfo": lambda fn: fn("evil.example", 443),
    "getnameinfo": lambda fn: fn(("evil.example", 443), 0),
}


def call_with_a_foreign_host(name: str):
    fn = getattr(socket, name)
    return _FOREIGN_CALL.get(name, lambda f: f("evil.example"))(fn)


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

    @pytest.mark.parametrize("resolver", RESOLVERS_THE_SOCKET_MODULE_OFFERS)
    def test_every_resolver_the_socket_module_offers_is_covered(
        self, resolver: str
    ) -> None:
        """No name in this module may turn a foreign host into an address.

        The list of what to patch is asked of the socket module, not read from
        the guard's own _RESOLVERS tuple, and that is the whole design of this
        test. The first draft parametrised on _RESOLVERS; emptying that tuple
        turned four failures into four SKIPS and the suite stayed green - the
        exact shape of gate the ledger keeps finding. Derived from the module,
        emptying the tuple is red.

        It is also how getnameinfo was found: it was in no defect record, and
        it went straight past getaddrinfo like the other four.
        """
        with pytest.raises(EgressAttempt, match="evil.example"):
            call_with_a_foreign_host(resolver)

    def test_the_discovery_rule_finds_more_than_a_handful(self) -> None:
        # The floor. If dir(socket) or the prefixes ever stop matching, the
        # parametrised test above degrades into nothing at all, silently.
        assert len(RESOLVERS_THE_SOCKET_MODULE_OFFERS) >= 6

    @pytest.mark.parametrize("name,reason", sorted(NOT_A_LOOKUP.items()))
    def test_what_the_guard_leaves_alone_is_left_alone_for_a_reason(
        self, name: str, reason: str
    ) -> None:
        # The exemptions are claims, so they are checked rather than trusted:
        # gethostname really does refuse a host argument, which is why asking
        # it to resolve a foreign name is not a thing that can happen.
        assert "no host argument" in reason
        with pytest.raises(TypeError):
            getattr(socket, name)("evil.example")

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

    def test_the_older_resolvers_still_answer_for_loopback(self) -> None:
        # The wrappers have to stay transparent, or this stops being a guard
        # and starts being a breakage. gethostbyaddr wants an address rather
        # than a name, so it gets one.
        assert socket.gethostbyname("localhost") == "127.0.0.1"
        assert socket.gethostbyname_ex("localhost")[2]
        assert socket.gethostbyaddr("127.0.0.1")

    def test_getfqdn_with_no_argument_still_works(self) -> None:
        # Written before the fix and it failed, which is how the hole in the
        # first draft was found: getfqdn() resolves the empty string by asking
        # this machine its own name and then handing THAT to gethostbyaddr, so
        # guarding gethostbyaddr made socket.getfqdn() raise.
        assert socket.getfqdn()

    def test_this_machines_own_name_is_not_egress(self) -> None:
        assert socket.gethostbyaddr(socket.gethostname())

    def test_the_allowance_for_our_own_name_did_not_widen_getaddrinfo(
        self,
    ) -> None:
        # The exception lives in the resolver wrappers, not in is_local. If it
        # ever moves, getaddrinfo starts accepting a name that can go to a
        # corporate DNS server, and this test is what says so.
        assert egress_guard.is_local(socket.gethostname()) is False
        with pytest.raises(EgressAttempt):
            socket.getaddrinfo(socket.gethostname(), 80)


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
