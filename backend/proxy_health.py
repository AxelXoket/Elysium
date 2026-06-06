"""proxy_health.py — Proxy health probe with 30-second TTL cache.

Semantics (μ1, μ2):
- proxy_required=false AND no proxy URL  →  healthy=True (direct is intentional; no probe).
- proxy_required=false AND proxy URL set →  probe runs; result reported; does NOT block completion.
- proxy_required=true  AND no proxy URL  →  healthy=False, reason="proxy_missing"; no probe.
- proxy_required=true  AND proxy URL set →  probe runs; unhealthy blocks completion.

The kill-switch enforcement (block vs allow) lives in the completions router.
This module only reports the current health state.

Probe target: GET https://openrouter.ai/api/v1/models (public, no auth required).
Any non-5xx response is considered a live proxy.

Reason codes:
  null              — healthy
  proxy_missing     — proxy_required=true but no URL in keyring
  proxy_unreachable — connection / DNS failure
  auth_failed       — HTTP 4xx from probe target
  timeout           — exceeded HEALTH_PROBE_TIMEOUT
  unknown_error     — other exception

Privacy: response body is never read or logged.
"""

import time
import logging
import httpx

from config import OPENROUTER_BASE_URL, PROXY_HEALTH_TTL, HEALTH_PROBE_TIMEOUT, KEYRING_PROXY_URL
from network_client import get_client
from keyring_service import get_secret

logger = logging.getLogger(__name__)

_cache: dict = {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def check_proxy_health() -> dict:
    """Return health status, using the 30 s TTL cache when valid."""
    now = time.monotonic()
    if _cache and (now - _cache["fetched_at"]) < PROXY_HEALTH_TTL:
        return {**_cache["result"], "cached": True}

    result = await _evaluate()
    _cache["fetched_at"] = now
    _cache["result"] = result
    return {**result, "cached": False}


def invalidate_health_cache() -> None:
    """Force the next call to re-probe. Called after proxy config changes."""
    _cache.clear()


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

async def _evaluate() -> dict:
    """Determine health state based on proxy_required and proxy URL presence."""
    proxy_url = get_secret(KEYRING_PROXY_URL)
    proxy_required = _read_proxy_required()

    if not proxy_url:
        if proxy_required:
            # Required but not configured — always unhealthy.
            return {"healthy": False, "latency_ms": None, "reason": "proxy_missing"}
        else:
            # Optional and not configured — direct connection is intentional.
            return {"healthy": True, "latency_ms": None, "reason": None}

    # Proxy URL is configured — probe regardless of proxy_required.
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

        # 4xx from the public endpoint is unexpected but treated as auth_failed.
        logger.warning("Proxy health probe 4xx: status=%d", response.status_code)
        return {"healthy": False, "latency_ms": latency_ms, "reason": "auth_failed"}

    except httpx.TimeoutException:
        logger.warning("Proxy health probe timed out after %.1f s", HEALTH_PROBE_TIMEOUT)
        return {"healthy": False, "latency_ms": None, "reason": "timeout"}

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
