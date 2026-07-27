"""proxy_health.py - Proxy health probe with 30-second TTL cache.

Semantics (μ1, μ2):
- proxy_required=false AND no proxy URL  →  healthy=True (direct is intentional; no probe).
- proxy_required=false AND proxy URL set →  probe runs; result reported; does NOT block completion.
- proxy_required=true  AND no proxy URL  →  healthy=False, reason="proxy_missing"; no probe.
- proxy_required=true  AND proxy URL set →  probe runs; unhealthy blocks completion.

The kill-switch enforcement (block vs allow) lives in the completions router.
This module only reports the current health state.

Probe target: GET https://openrouter.ai/api/v1/models (public, no auth required).
Any 2xx/3xx response is considered a live proxy; any 4xx/5xx is reported as
unhealthy with reason "proxy_auth_failed" (a 4xx from a public endpoint usually
means the proxy itself rejected or mangled the request).

Reason codes:
  null              - healthy
  proxy_missing     - proxy_required=true but no URL in keyring
  proxy_unreachable - connection / DNS failure
  proxy_auth_failed - the proxy itself rejected the request (407 / SOCKS auth
                      failure), or HTTP 4xx from the probe target
  timeout           - exceeded HEALTH_PROBE_TIMEOUT
  unknown_error     - other exception

Privacy: response body is never read or logged.
"""

import time
import logging
import httpx
from fastapi import HTTPException

from config import OPENROUTER_BASE_URL, PROXY_HEALTH_TTL, HEALTH_PROBE_TIMEOUT, SECRET_PROXY_URL
from network_client import get_client
from secrets_service import get_secret

logger = logging.getLogger(__name__)

_cache: dict = {}
# Bumped by invalidate_health_cache(). A probe that was already in flight when
# the proxy settings changed carries the OLD epoch, so its result is reported
# to its own caller but never written back into the cache - otherwise it
# repopulated the just-cleared cache with the PREVIOUS proxy's verdict and a
# fresh 30 s timestamp, and the user's corrected proxy kept failing (or the
# broken one kept passing) until the TTL expired, with "Retry" returning the
# same cached lie.
_epoch: int = 0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def check_proxy_health() -> dict:
    """Return health status, using the 30 s TTL cache when valid."""
    now = time.monotonic()
    if _cache and (now - _cache["fetched_at"]) < PROXY_HEALTH_TTL:
        return {**_cache["result"], "cached": True}

    epoch_at_start = _epoch
    result = await _evaluate()
    if _epoch == epoch_at_start:
        _cache["fetched_at"] = time.monotonic()
        _cache["result"] = result
    return {**result, "cached": False}


def invalidate_health_cache() -> None:
    """Force the next call to re-probe. Called after proxy config changes."""
    global _epoch
    _epoch += 1
    _cache.clear()


async def enforce_proxy_gate() -> None:
    """THE gate every outbound path must pass. Raises HTTPException(503).

    No-op unless the user armed proxy_required; then an unhealthy (or missing)
    proxy refuses the request with the reason code documented above.

    It exists as one function because it used to be hand-copied at each call
    site, and a path that forgot it was invisible: POST /settings/api-key
    validated the key against openrouter.ai with no gate at all, so in the
    proxy_required + no-URL state ("proxy_missing" - the state every other path
    refuses outright) the user's API key and real IP went out unproxied from
    the very screen where they typed the key.
    """
    if not _read_proxy_required():
        return
    health = await check_proxy_health()
    if not health.get("healthy"):
        raise HTTPException(503, health.get("reason") or "proxy_unhealthy")


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

async def _evaluate() -> dict:
    """Determine health state based on proxy_required and proxy URL presence."""
    proxy_url = get_secret(SECRET_PROXY_URL)
    proxy_required = _read_proxy_required()

    if not proxy_url:
        if proxy_required:
            # Required but not configured - always unhealthy.
            return {"healthy": False, "latency_ms": None, "reason": "proxy_missing"}
        else:
            # Optional and not configured - direct connection is intentional.
            return {"healthy": True, "latency_ms": None, "reason": None}

    # Proxy URL is configured - probe regardless of proxy_required.
    return await _probe()


async def _probe() -> dict:
    """Hit the public /models endpoint through the configured client."""
    client = get_client()
    timeout = httpx.Timeout(HEALTH_PROBE_TIMEOUT)
    try:
        start = time.monotonic()
        response = await client.get(
            f"{OPENROUTER_BASE_URL}/models",
            timeout=timeout,
        )
        latency_ms = int((time.monotonic() - start) * 1000)

        if response.status_code < 400:
            logger.info("Proxy health probe OK: status=%d latency_ms=%d",
                        response.status_code, latency_ms)
            return {"healthy": True, "latency_ms": latency_ms, "reason": None}

        # 4xx from the public endpoint is unexpected; report it as a PROXY auth
        # failure. A distinct code (not OpenRouter's "auth_failed") keeps the
        # frontend from telling the user to check their API key for a proxy
        # problem.
        logger.warning("Proxy health probe 4xx: status=%d", response.status_code)
        return {"healthy": False, "latency_ms": latency_ms, "reason": "proxy_auth_failed"}

    except httpx.TimeoutException:
        logger.warning("Proxy health probe timed out after %.1f s", HEALTH_PROBE_TIMEOUT)
        return {"healthy": False, "latency_ms": None, "reason": "timeout"}

    except httpx.ProxyError:
        # The PROXY rejected us (407 to CONNECT, SOCKS auth failure, "could not
        # connect"). ProxyError is a sibling of NetworkError under
        # TransportError, NOT a subclass, so it used to fall through to the
        # generic handler and surface as "unknown_error" -> "Something went
        # wrong. Please try again.", leaving a user with a credentialled proxy
        # blocked from chatting and never told the proxy needs credentials.
        logger.warning("Proxy health probe rejected by the proxy.")
        return {"healthy": False, "latency_ms": None, "reason": "proxy_auth_failed"}

    except (httpx.ConnectError, httpx.NetworkError, httpx.RemoteProtocolError):
        logger.warning("Proxy health probe connection failure.")
        return {"healthy": False, "latency_ms": None, "reason": "proxy_unreachable"}

    except Exception as exc:
        logger.warning("Proxy health probe unexpected error: %s", type(exc).__name__)
        return {"healthy": False, "latency_ms": None, "reason": "unknown_error"}


def _read_proxy_required() -> bool:
    """Read proxy_required from the settings table. Defaults to False."""
    from database import get_db
    with get_db() as con:
        row = con.execute(
            "SELECT value FROM settings WHERE key = 'proxy_required'"
        ).fetchone()
    return row is not None and row["value"] == "1"
