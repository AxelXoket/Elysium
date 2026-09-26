"""routers/models_router.py -- OpenRouter model listing endpoint (Phase 5A).

Routes:
    GET /models/openrouter   - list available OpenRouter models

Privacy invariants:
    - API key is NEVER logged, returned, or forwarded.
    - Raw OpenRouter response bodies are NEVER logged.
    - This module does NOT instantiate httpx.AsyncClient directly.
    - This module does NOT import requests, urllib.request, or keyring.
"""

import asyncio
import logging

from fastapi import APIRouter, HTTPException

import openrouter
from openrouter import fetch_models, model_id_blocked, OpenRouterError
from proxy_health import enforce_proxy_gate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/models", tags=["models"])


# ---------------------------------------------------------------------------
# GET /models/openrouter
# ---------------------------------------------------------------------------

@router.get("/openrouter")
async def list_openrouter_models(refresh: bool = False) -> dict:
    """Return available OpenRouter models with caching and proxy gating."""
    # 1. Proxy gate
    await enforce_proxy_gate()

    # 2. Fetch models, and the private endpoint list beside them (Q-121). The
    # second never raises: unknown is an answer, and the list still loads.
    zdr_task = asyncio.ensure_future(openrouter.zdr_index(refresh=refresh))
    try:
        data = await fetch_models(refresh=refresh)
    except OpenRouterError as e:
        zdr_task.cancel()
        # Every branch below raises `reason` itself rather than a literal, so
        # the source shows a variable name and nothing about what it can hold.
        # RELAY_DETAILS at the bottom of this file is that answer, and it is
        # what `tests/error_enumeration.py` reads.
        reason = e.reason
        if reason in ("api_key_invalid", "api_key_required_by_openrouter"):
            raise HTTPException(401, reason)
        elif reason == "proxy_auth_failed":
            # The user's own proxy refused the tunnel - never blamed on the
            # provider, so the UI can point at the proxy settings.
            raise HTTPException(502, reason)
        elif reason == "openrouter_timeout":
            raise HTTPException(504, reason)
        elif reason == "invalid_openrouter_models_response":
            raise HTTPException(502, reason)
        else:
            raise HTTPException(502, "openrouter_models_error")

    # 3. C4: a model the send point refuses (web search, the auto router,
    # presets) is not offered. The same predicate as the refusal, so the list
    # and the send cannot disagree. A new dict: the cached one keeps every
    # model, because the metadata lookups read it by id.
    kept = [m for m in data.get("models") or []
            if not model_id_blocked(str(m.get("id") or ""))]

    # 4. Q-121: how many zero-data-retention endpoints each model has that
    # take the keys every chat request carries. 0 is a model the send point
    # refuses (model_no_private_endpoint), so the picker shows it disabled
    # with the reason instead of hiding it. None: the list is unknown, and
    # nothing is claimed either way. The same count the send point uses.
    index = await zdr_task
    rows = [{**m, "zdr_endpoints": (
                None if index is None else openrouter.private_endpoint_count(
                    openrouter.zdr_endpoints_in(index, str(m.get("id") or ""))))}
            for m in kept]
    return {**data, "models": rows, "count": len(rows),
            "zdr_known": index is not None}


#: Every detail the /models route can put in front of a user.
#:
#: The elif chain above raises the relayed `reason` for five of these and a
#: literal for the sixth, so five of the six are invisible to any reader that
#: only sees string literals. This is the second relay map in the backend and
#: neither had ever been counted before `tests/error_enumeration.py`.
RELAY_DETAILS: frozenset[str] = frozenset({
    "api_key_invalid",
    "api_key_required_by_openrouter",
    "proxy_auth_failed",
    "openrouter_timeout",
    "invalid_openrouter_models_response",
    "openrouter_models_error",
})
