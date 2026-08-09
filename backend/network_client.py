"""network_client.py - Singleton httpx.AsyncClient. Proxy-aware, trust_env=False.

Design (μ1, C3, M1, M2):
- The client ALWAYS uses the proxy URL from the vault (secrets_service, E5)
  if one is configured. proxy_required is a completions-router concern.
- trust_env=False on every client instance. No system HTTP_PROXY/HTTPS_PROXY leakage.
- No global timeout: callers specify per-request timeout from config.py constants.
- get_client() raises VaultLockedError while the vault is locked (secrets
  live in the encrypted DB) and NEVER falls back to a proxyless client -
  that would silently bypass the user's proxy. Every caller sits behind the
  423 gate or the SSE VaultLockedError handlers, which absorb it.
- reset_client() is async - must be awaited by the settings router.
- close_client() is async - lifespan shutdown AND vault lock call it, so a
  locked vault does not keep a proxy-configured client (a secret) in RAM.

Privacy:
- Proxy URL is read from the vault at build time. It is never logged.
"""

import logging
import httpx

from config import OPENROUTER_BASE_URL, SECRET_PROXY_URL
from secrets_service import get_secret

logger = logging.getLogger(__name__)

_client: httpx.AsyncClient | None = None

#: How long an idle connection to openrouter.ai is kept for reuse.
#:
#: httpx's default is 5 seconds. A person chatting sends a message every ten to
#: sixty, so the pooled connection had already been retired before nearly every
#: message and each one paid a fresh DNS + TCP + TLS handshake. Measured against
#: openrouter.ai: 0.277s cold against 0.085s reused, so roughly 190ms per
#: message - larger than every piece of local work on the send path combined.
#:
#: The ceiling is not ours to choose. openrouter.ai sits behind Cloudflare,
#: whose client-edge keep-alive idle timeout is 400 seconds, and that was
#: confirmed by measurement: an idle connection's has_expired() flips at exactly
#: t+400s. Past that the socket is dead whatever this number says, so a larger
#: value buys no reuse and only widens the window in which the pool hands out a
#: connection the server has already closed.
#:
#: 120 covers the real cadence with margin and normal reading pauses, and stays
#: far enough under 400 that WE retire the socket first. It also bounds the one
#: dead-socket case httpcore cannot detect: a peer that VANISHES - laptop
#: suspend, Wi-Fi change, VPN toggle - sends no FIN at all, so the pool's
#: readability probe sees nothing wrong and the request goes into a black hole
#: until the read timeout fires.
_KEEPALIVE_EXPIRY_S = 120.0

#: Pool ceilings, well above real use. Every caller of this client targets the
#: one origin; peak concurrency is about four (a live completion stream plus an
#: occasional model list, key check or health probe). The default ceiling of 100
#: is meaningless for a desktop app, and on Windows each idle connection costs a
#: select() probe when the pool hands one out.
#:
#: The idle count is deliberately loose rather than tight against that peak:
#: httpcore's surplus-idle trim measures the TOTAL connection count, not the
#: idle count, so a tight value would evict live-enough connections.
_LIMITS = httpx.Limits(
    max_connections=20,
    max_keepalive_connections=10,
    keepalive_expiry=_KEEPALIVE_EXPIRY_S,
)

# Deliberately NOT paired with a retry. The obvious companion change - retry once
# when a pooled socket turns out to be dead - cannot be made safe at this layer:
# httpx reads the response body inside its own send(), so the same exception is
# raised both when the request never left and when it was accepted, generated,
# billed and then truncated. Re-sending the second case pays for the reply twice.
# For a stream, headers arriving at all already means the model is running, so a
# "nothing emitted yet" guard does not mean "nothing happened yet" either.
#
# It is not needed for the ordinary case regardless: when the server closes an
# idle connection, httpcore's readability probe notices before handing it out and
# transparently opens a new one. Keeping this expiry under Cloudflare's means we
# usually retire the socket before that can even come up.


class EgressRefused(RuntimeError):
    """An outbound request named a host this app does not talk to."""


def allowed_hosts() -> frozenset[str]:
    """The complete set of hosts this process may open a connection to.

    Derived from the configured provider rather than hardcoded, so the local
    mock provider used for end-to-end smoke tests still works and so does an
    OPENROUTER_BASE_URL override - which config.py already logs loudly,
    because it is where the Authorization header goes.

    Loopback is on the list because the app IS a local server: the health
    probe and the launcher talk to it, and a proxy the user configured on
    127.0.0.1 (Tor, a local SOCKS tunnel) is the ordinary case.
    """
    provider = httpx.URL(OPENROUTER_BASE_URL).host or ""
    return frozenset({provider.lower(), "127.0.0.1", "localhost", "::1"})


async def _one_host_only(request: httpx.Request) -> None:
    """Refuse, before anything is sent, any destination but the provider.

    The one-host promise was kept by every caller happening to build the same
    URL. That is a habit, not a control: one f-string with a host in it,
    anywhere in this codebase or in a dependency that borrows this client, and
    the conversation goes somewhere else with the Authorization header
    attached. This is the chokepoint that makes it a rule.

    It reads request.url, which is the TARGET - a configured proxy is a
    transport detail underneath and does not change what is being asked for,
    so this checks where the data is actually going rather than which hop it
    takes first.
    """
    host = (request.url.host or "").lower()
    if host not in allowed_hosts():
        raise EgressRefused(
            f"refused an outbound request to {host!r}: this app talks to "
            f"exactly one host and that is not it"
        )


def _build_client() -> httpx.AsyncClient:
    """Build a fresh AsyncClient. Uses proxy from keyring if present."""
    proxy_url = get_secret(SECRET_PROXY_URL)
    if proxy_url:
        # Proxy URL is not logged - only presence is reported.
        logger.info("Building HTTP client with configured proxy.")
        return httpx.AsyncClient(
            proxy=proxy_url,
            trust_env=False,
            limits=_LIMITS,
            event_hooks={"request": [_one_host_only]},
        )
    logger.info("Building HTTP client (direct - no proxy configured).")
    return httpx.AsyncClient(trust_env=False, limits=_LIMITS,
                             event_hooks={"request": [_one_host_only]})


def get_client() -> httpx.AsyncClient:
    """Return the singleton AsyncClient, building it lazily on first call.

    Never raises - proxy_required enforcement lives in the completions router.
    """
    global _client
    if _client is None:
        _client = _build_client()
    return _client


async def reset_client() -> None:
    """Close the current client and build a new one from current keyring state.

    Must be awaited. Called after proxy settings change so the new (or absent)
    proxy URL takes effect immediately for subsequent requests.
    """
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
    _client = _build_client()


async def close_client() -> None:
    """Cleanly close the client. Called from the lifespan shutdown hook."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
