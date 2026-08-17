"""The one-host promise, made checkable.

README says the app talks to exactly one host. Until now nothing enforced it.
Two tests set up their own socket traps by hand, around one request each, and
every other test in the suite could have opened a connection to anywhere
without a single assertion noticing. "One egress host" was a habit, and habits
are not testable.

This turns it into a fixture that is on for the WHOLE suite, so the way to
break the promise is to write code that breaks it, not to forget a decorator.

It traps at the socket layer on purpose. Patching httpx would prove only that
httpx behaves; a new dependency, or urllib in some corner of provisioning,
goes nowhere near httpx and straight through getaddrinfo and connect. The trap
sits under every client this process could ever grow.

THIS PROCESS is the honest boundary, and it is worth stating rather than
implying. monkeypatch rewrites attributes in one interpreter's memory; a child
started by subprocess.Popen - the TTS engine worker, uv during provisioning -
gets a fresh, unpatched socket module and is invisible here. What covers those
is a different mechanism: worker_client strips every credential and forces
HF_HUB_OFFLINE before the spawn, and test_privacy_promises.py asserts on the
environment the child is actually handed.

What is allowed, and why so little:

  * loopback, because the app IS a local server and the test client, the
    health probe and the launcher all talk to it;
  * "testserver", Starlette's placeholder host for in-process ASGI calls.

openrouter.ai is deliberately NOT on the list. No test should reach the real
provider - they all use a fake - so an attempt to resolve it means a test is
about to make a live, billable request, and that is worth failing on.
"""
from __future__ import annotations

import asyncio
import asyncio.base_events
import asyncio.proactor_events
import ipaddress
import socket

_LOOPBACK = frozenset({
    "127.0.0.1", "::1", "localhost", "localhost.", "testserver",
    "0.0.0.0", "",
})


class EgressAttempt(AssertionError):
    """Raised when something in the suite tried to leave this machine."""


def is_local(host: object) -> bool:
    """Loopback only, decided by parsing rather than by prefix.

    The first cut asked whether the name started with "127." and my own test
    caught it: "127.0.0.1.evil.example" starts with "127." and is somebody
    else's server. A hostname that merely RESEMBLES an address has to be
    treated as a hostname, so 127.0.0.0/8 is recognised by parsing an actual
    address and everything else has to match the small literal set.
    """
    if host is None:
        return True  # an already-resolved connection with no name to check
    if isinstance(host, (bytes, bytearray)):
        try:
            host = host.decode("utf-8", "replace")
        except Exception:  # pragma: no cover - defensive
            return False
    text = str(host).strip("[]").lower()
    if text in _LOOPBACK:
        return True
    try:
        return ipaddress.ip_address(text).is_loopback
    except ValueError:
        return False


def _target(address: object) -> object:
    """The host inside a connect() address, or None when there is no host.

    An AF_INET address is always a tuple. Anything else - a filesystem path
    for AF_UNIX, a Bluetooth address - has no network host in it, and reading
    it as one made the guard refuse local IPC: a path fails IP parsing, fails
    the loopback name set, and comes out looking like somebody's server.
    """
    if isinstance(address, tuple):
        return address[0] if address else None
    return None


def _refuse(host: object, how: str) -> EgressAttempt:
    return EgressAttempt(
        f"the test suite tried to {how} {host!r}. Nothing here may leave this "
        f"machine: the app promises a single egress host and the tests are "
        f"where that promise is kept. If a test needs to prove an outbound "
        f"call is REFUSED, assert on EgressAttempt instead of allowing it."
    )


#: The older resolver calls, which reach the C resolver on their own rather
#: than through getaddrinfo. Patching getaddrinfo does not cover a single one
#: of them: measured, with getaddrinfo replaced by something that raises,
#: gethostbyname("localhost") still returned an address. A dependency that
#: reaches for one of these would have sent a live DNS query - leaking the
#: name it asked for to every resolver on the way - while the suite stayed
#: green. What leaks there is intent rather than content, but this guard's own
#: test already calls resolution alone a breach, so the family belongs here.
#:
#: These take the thing being looked up as their first argument.
_RESOLVERS = ("gethostbyname", "gethostbyname_ex", "gethostbyaddr", "getfqdn")

#: And this one takes an address TUPLE instead, which is why it needs its own
#: wrapper rather than a place in the list above. It was not in the defect
#: record; it turned up when the test stopped trusting that list and started
#: asking the socket module what it offers. Measured the same way: with
#: getaddrinfo trapped, getnameinfo(("127.0.0.1", 80), 0) still came back with
#: a resolved name.
_ADDRESS_RESOLVERS = ("getnameinfo",)


def install(monkeypatch) -> None:
    """Trap name resolution and connection for the duration of one test."""
    real_getaddrinfo = socket.getaddrinfo
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_create = socket.create_connection

    def guarded_getaddrinfo(host, *args, **kwargs):
        if not is_local(host):
            raise _refuse(host, "resolve")
        return real_getaddrinfo(host, *args, **kwargs)

    def guarded_connect(self, address, *args, **kwargs):
        host = _target(address)
        if not is_local(host):
            raise _refuse(host, "connect to")
        return real_connect(self, address, *args, **kwargs)

    def guarded_connect_ex(self, address, *args, **kwargs):
        host = _target(address)
        if not is_local(host):
            raise _refuse(host, "connect to")
        return real_connect_ex(self, address, *args, **kwargs)

    def guarded_create_connection(address, *args, **kwargs):
        # Its own entry point, not a wrapper around the patched connect: it
        # resolves and connects inside the C layer, so patching the two above
        # does not cover it.
        host = _target(address)
        if not is_local(host):
            raise _refuse(host, "connect to")
        return real_create(address, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    monkeypatch.setattr(socket, "create_connection", guarded_create_connection)

    for resolver_name in _RESOLVERS:
        original = getattr(socket, resolver_name, None)
        if original is None:  # pragma: no cover - every platform has all four
            continue
        monkeypatch.setattr(
            socket, resolver_name, _guarded_resolver(original, resolver_name)
        )

    for resolver_name in _ADDRESS_RESOLVERS:
        original = getattr(socket, resolver_name, None)
        if original is None:  # pragma: no cover - defensive
            continue
        monkeypatch.setattr(
            socket, resolver_name, _guarded_address_resolver(original)
        )

    # asyncio does not have to come through socket.socket.connect. On Windows
    # the default loop is the Proactor one, whose sock_connect goes to
    # _overlapped.WSAConnect in C - past every patch above. Nothing in the app
    # reaches it today, because every current caller resolves a NAME first and
    # getaddrinfo is covered; a future path that connects to an already-parsed
    # address would not. Patch the loops' own entry points so the guard does
    # not depend on that ordering holding.
    for module_name in ("base_events", "proactor_events", "selector_events"):
        module = getattr(asyncio, module_name, None)
        if module is None:
            continue
        for loop_class in vars(module).values():
            if not isinstance(loop_class, type):
                continue
            for method in ("sock_connect", "create_connection"):
                original = loop_class.__dict__.get(method)
                if original is None:
                    continue
                monkeypatch.setattr(loop_class, method,
                                    _guarded_loop_method(original, method))


def _is_this_machine(host: object) -> bool:
    """Whether the name being looked up is the name of THIS computer.

    Deliberately not folded into is_local, and the difference is the point.
    getfqdn() with no argument asks this machine its own name, and does it by
    handing that name straight to gethostbyaddr - so the moment gethostbyaddr
    was guarded, socket.getfqdn() started raising. Refusing that is not strict,
    it is broken: nothing has left the machine, and third-party code asks for
    it routinely.

    The allowance stops here, at the four legacy resolvers, rather than going
    into is_local, because getaddrinfo(gethostname()) is refused today and
    there is no reason to loosen it. Only the door that needed opening opens.
    """
    if host is None:
        return False
    text = str(host).strip().rstrip(".").lower()
    if not text:
        return False
    own = socket.gethostname().rstrip(".").lower()
    return text == own or text == own.split(".", 1)[0]


def _guarded_resolver(original, name: str):
    """Wrap a resolver whose first argument is the thing being looked up.

    getfqdn is the odd one: it is pure Python, it defaults to the empty string
    and it swallows OSError. The empty string is on the loopback list so the
    no-argument call still works, and EgressAttempt is an AssertionError rather
    than an OSError, so it travels out through that except clause instead of
    being quietly turned into a hostname.
    """
    def guarded(host=("" if name == "getfqdn" else None), *args, **kwargs):
        if not is_local(host) and not _is_this_machine(host):
            raise _refuse(host, "resolve")
        return original(host, *args, **kwargs)

    return guarded


def _guarded_address_resolver(original):
    """Wrap a resolver whose first argument is a connect()-shaped address.

    Same allowance as the name-first ones: this machine's own name is not
    egress, because a reverse lookup of a loopback address answers with it.
    """
    def guarded(address=None, *args, **kwargs):
        host = _target(address)
        if not is_local(host) and not _is_this_machine(host):
            raise _refuse(host, "resolve")
        return original(address, *args, **kwargs)

    return guarded


def _guarded_loop_method(original, method: str):
    """Wrap a loop method whose first positional argument names a destination.

    sock_connect takes (sock, address); create_connection takes
    (protocol_factory, host, port). Both are checked the same way, by looking
    at whichever argument carries the destination.
    """
    async def guarded(self, *args, **kwargs):
        if method == "sock_connect":
            host = _target(args[1] if len(args) > 1 else kwargs.get("address"))
        else:
            host = args[1] if len(args) > 1 else kwargs.get("host")
        if not is_local(host):
            raise _refuse(host, "connect to")
        return await original(self, *args, **kwargs)

    return guarded
