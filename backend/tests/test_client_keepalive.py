"""The provider client threw its connection away between every message.

`httpx.AsyncClient` was built with only `proxy` and `trust_env=False`, so the
default pool limits applied - and the default keeps an idle connection for five
seconds. A person chatting sends a message every ten to sixty, so the connection
was always already gone and every single message paid a fresh DNS + TCP + TLS
handshake: measured at roughly 190ms against openrouter.ai, which is larger than
every piece of local work on the send path put together.

Two things are asserted here. That the pool actually keeps connections now - and
that the value stays under the ceiling the server imposes, because above that it
is not merely useless but actively worse: the pool starts handing out sockets the
edge has already closed.

Reaching into the pool is deliberate. The whole change is a number handed to a
third-party library, so the only thing worth asserting is that the library
received it.

No `client` fixture anywhere below, and that is the point: every test here took
one and not one of them touched it, so each was standing up an encrypted
database, a schema and a whole ASGI app to read an integer off an httpx pool.
`get_secret` is monkeypatched in every case, which is the only thing the vault
was ever being asked for.
"""
from __future__ import annotations

import httpx

import network_client


#: Cloudflare's client-edge keep-alive idle timeout, which openrouter.ai sits
#: behind. Measured, not assumed: an idle connection's has_expired() flips at
#: exactly t+400s. This is a ceiling on us, not a target.
_SERVER_IDLE_TIMEOUT_S = 400.0


def _pool(client: httpx.AsyncClient):
    return client._transport._pool


def _all_pools(client: httpx.AsyncClient) -> list:
    """Every connection pool the client can route through.

    With `proxy=` set, httpx does not put the proxy on `_transport`; it MOUNTS a
    second, proxy-backed transport under a URL pattern and leaves the default
    transport in place. So checking `_transport` alone would silently miss the
    pool that a proxied user's traffic actually goes through - which is the one
    that matters, since that is also the branch where httpx drops `retries`.
    """
    return [_pool(client)] + [t._pool for t in client._mounts.values()
                              if getattr(t, "_pool", None) is not None]


# ── the connection is kept ───────────────────────────────────────────────────

def test_the_direct_client_keeps_an_idle_connection(monkeypatch):
    monkeypatch.setattr(network_client, "get_secret", lambda name: None)
    assert _pool(network_client._build_client())._keepalive_expiry == 120.0


def test_the_proxied_client_keeps_it_too(monkeypatch):
    """The limits have to reach the PROXY pool as well, which is a separate
    object mounted alongside the default one. httpx forwards `limits` on every
    branch - unlike `retries`, which it silently drops for a proxy, and which is
    one of the reasons no retry was added here."""
    monkeypatch.setattr(network_client, "get_secret",
                        lambda name: "http://127.0.0.1:9999")
    pools = _all_pools(network_client._build_client())
    names = [type(p).__name__ for p in pools]
    assert "AsyncHTTPProxy" in names, names
    for pool in pools:
        assert pool._keepalive_expiry == 120.0


def test_it_is_longer_than_a_pause_between_messages(monkeypatch):
    """The bug in one assertion: the old default was 5 seconds."""
    monkeypatch.setattr(network_client, "get_secret", lambda name: None)
    assert _pool(network_client._build_client())._keepalive_expiry > 60.0


def test_it_stays_under_what_the_server_allows(monkeypatch):
    """Above the edge's own idle timeout the socket is dead whatever we think,
    so a bigger number adds no reuse and only widens the window in which the
    pool hands out a connection the server already closed."""
    monkeypatch.setattr(network_client, "get_secret", lambda name: None)
    expiry = _pool(network_client._build_client())._keepalive_expiry
    assert expiry is not None, "never None - that is unbounded reuse of a dead socket"
    assert expiry < _SERVER_IDLE_TIMEOUT_S * 0.95


def test_the_pool_is_sized_for_a_desktop_app(monkeypatch):
    monkeypatch.setattr(network_client, "get_secret", lambda name: None)
    pool = _pool(network_client._build_client())
    assert pool._max_connections == 20
    assert pool._max_keepalive_connections == 10


# ── and nothing else about the client changed ───────────────────────────────

def test_the_environment_is_still_not_trusted(monkeypatch):
    """A system HTTPS_PROXY must never be able to redirect provider traffic."""
    monkeypatch.setattr(network_client, "get_secret", lambda name: None)
    assert network_client._build_client().trust_env is False


def test_a_configured_proxy_is_still_used(monkeypatch):
    monkeypatch.setattr(network_client, "get_secret",
                        lambda name: "http://127.0.0.1:9999")
    built = network_client._build_client()
    assert "AsyncHTTPProxy" in [type(p).__name__ for p in _all_pools(built)]
    assert built.trust_env is False


def test_the_connection_stays_http_1_1(monkeypatch):
    """openrouter.ai does offer h2 over ALPN, but multiplexing is worth nothing
    at one concurrent request, the package is not installed, and it would need
    PyInstaller hidden imports for the packaged build. Longer-lived connections
    must not be read as a reason to reach for it."""
    monkeypatch.setattr(network_client, "get_secret", lambda name: None)
    pool = _pool(network_client._build_client())
    assert pool._http1 is True
    assert pool._http2 is False


def test_the_singleton_is_still_reused(monkeypatch):
    monkeypatch.setattr(network_client, "get_secret", lambda name: None)
    network_client._client = None
    try:
        assert network_client.get_client() is network_client.get_client()
    finally:
        network_client._client = None
