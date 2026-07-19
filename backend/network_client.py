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

from config import SECRET_PROXY_URL
from secrets_service import get_secret

logger = logging.getLogger(__name__)

_client: httpx.AsyncClient | None = None


def _build_client() -> httpx.AsyncClient:
    """Build a fresh AsyncClient. Uses proxy from keyring if present."""
    proxy_url = get_secret(SECRET_PROXY_URL)
    if proxy_url:
        # Proxy URL is not logged - only presence is reported.
        logger.info("Building HTTP client with configured proxy.")
        return httpx.AsyncClient(
            proxy=proxy_url,
            trust_env=False,
        )
    logger.info("Building HTTP client (direct - no proxy configured).")
    return httpx.AsyncClient(trust_env=False)


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
