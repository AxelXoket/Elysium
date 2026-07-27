"""routers/models_router.py -- OpenRouter model listing endpoint (Phase 5A).

Routes:
    GET /models/openrouter   - list available OpenRouter models

Privacy invariants:
    - API key is NEVER logged, returned, or forwarded.
    - Raw OpenRouter response bodies are NEVER logged.
    - This module does NOT instantiate httpx.AsyncClient directly.
    - This module does NOT import requests, urllib.request, or keyring.
"""

import logging

from fastapi import APIRouter, HTTPException

from openrouter import fetch_models, OpenRouterError
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

    # 2. Fetch models
    try:
        return await fetch_models(refresh=refresh)
    except OpenRouterError as e:
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
