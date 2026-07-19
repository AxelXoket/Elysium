"""openrouter.py - OpenRouter API calls and generation parameter validation.

Public API:
    OpenRouterError                    - raised on API failure; carries sanitized reason.
    validate_and_filter_gen_params()   - whitelist + range check; raises ValueError on bad range.
    async fetch_models()               - 5-min cached model list.
    invalidate_model_cache()           - clears model cache (called on settings change).
    async complete()                   - non-streaming chat completion.

Proxy semantics:
    All calls use get_client() which already applies the configured proxy.
    This module does not check proxy_required - that is the completions router's job.

Privacy rules:
    - API key read at call time; never stored in a module-level variable.
    - messages array and request body are NEVER logged.
    - Response body content is NEVER logged or forwarded on error.
    - Only model_id, HTTP status, and latency are logged.

μ3 - /models/user fallback:
    - No API key → skip /models/user, use public /models.
    - /models/user 401/403 → raise OpenRouterError("api_key_invalid"). No public fallback.
    - /models/user timeout or non-auth failure → fall back to public /models.

μ8 - Sanitized errors:
    complete() raises OpenRouterError with a sanitized reason code.
    The raw response body is never included.
"""

import json
import time
import logging
from typing import Any, AsyncIterator

import httpx

from config import (
    OPENROUTER_BASE_URL,
    MODEL_LIST_TTL,
    MODELS_FETCH_TIMEOUT,
    COMPLETION_TIMEOUT,
    STREAM_CONNECT_TIMEOUT,
    STREAM_READ_TIMEOUT,
    SECRET_API_KEY,
)
from network_client import get_client
from secrets_service import get_secret

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------

class OpenRouterError(Exception):
    """Raised when the OpenRouter API call fails.

    reason is a sanitized code safe to return to the frontend:
      api_key_invalid, openrouter_auth_failed, openrouter_rate_limited,
      openrouter_server_error, openrouter_timeout, openrouter_error.
    """
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# ---------------------------------------------------------------------------
# Generation parameter validation
# ---------------------------------------------------------------------------

# (type, min, max)
_PARAM_SPEC: dict[str, tuple] = {
    "temperature":        (float, 0.0,    2.0),
    "top_p":              (float, 0.0,    1.0),
    "top_k":              (int,   0,      131072),
    "min_p":              (float, 0.0,    1.0),
    "top_a":              (float, 0.0,    1.0),
    "max_tokens":         (int,   1,      131072),
    "frequency_penalty":  (float, -2.0,   2.0),
    "presence_penalty":   (float, -2.0,   2.0),
    "repetition_penalty": (float, 0.001,  2.0),
    "seed":               (int,   -(2**31), 2**31 - 1),
}


def validate_and_filter_gen_params(raw: dict) -> dict:
    """Return a filtered dict with only whitelisted, in-range parameters.

    - Unknown keys: silently dropped.
    - None values: silently dropped.
    - Out-of-range values: raise ValueError with a clear message.
    - stop: handled separately (no numeric range).
    """
    result: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in _PARAM_SPEC or value is None:
            continue
        typ, lo, hi = _PARAM_SPEC[key]
        try:
            value = typ(value)
        except (TypeError, ValueError):
            raise ValueError(f"'{key}' must be {typ.__name__}.")
        if not (lo <= value <= hi):
            raise ValueError(f"'{key}' out of range [{lo}, {hi}], got {value}.")
        result[key] = value

    # stop: separate branch (no numeric range)
    stop_raw = raw.get("stop")
    if stop_raw is not None:
        if isinstance(stop_raw, str):
            if stop_raw != "":
                result["stop"] = stop_raw
        elif isinstance(stop_raw, list):
            if stop_raw:
                for s in stop_raw:
                    if not isinstance(s, str):
                        raise ValueError("'stop' list elements must be strings.")
                    if s == "":
                        raise ValueError(
                            "'stop' list elements must not be empty strings."
                        )
                result["stop"] = stop_raw
        else:
            raise ValueError("'stop' must be a string or list of strings.")

    return result


# ---------------------------------------------------------------------------
# Model list
# ---------------------------------------------------------------------------

# Source-keyed cache: {"user": {"fetched_at": ..., "data": {...}}, "public": ..., ...}
_model_cache: dict[str, Any] = {}


async def fetch_models(refresh: bool = False) -> dict:
    """Return {source, cached, count, models} with source-keyed caching.

    Cache keys: "user", "public", "public_fallback".
    Returns cached response if TTL is valid and refresh=False.
    Raises OpenRouterError on auth, network, or malformed response failures.

    Auth flow (μ3):
      - No API key → public /models (no Authorization header).
      - API key → try /models/user (Bearer token).
        - 200 with valid data → source="user".
        - 200 with malformed data → 502, no fallback.
        - 401/403 → raise api_key_invalid, no fallback.
        - Other failure → fallback to public /models (no Authorization header).
    """
    api_key = get_secret(SECRET_API_KEY)
    timeout = httpx.Timeout(MODELS_FETCH_TIMEOUT)
    client = get_client()
    now = time.monotonic()

    # ── Determine primary cache key based on auth state ───────────────────
    primary_key = "user" if api_key else "public"

    # ── Check primary cache ───────────────────────────────────────────────
    if not refresh and primary_key in _model_cache:
        entry = _model_cache[primary_key]
        if (now - entry["fetched_at"]) < MODEL_LIST_TTL:
            return {**entry["data"], "cached": True}

    fallback_reason: str | None = None

    # ── Authenticated path: /models/user ──────────────────────────────────
    if api_key:
        try:
            resp = await client.get(
                f"{OPENROUTER_BASE_URL}/models/user",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=timeout,
            )
            if resp.status_code == 200:
                raw_data = resp.json().get("data")
                if not isinstance(raw_data, list):
                    raise OpenRouterError("invalid_openrouter_models_response")
                models = [_normalise_model(m) for m in raw_data]
                result = {
                    "source": "user",
                    "cached": False,
                    "count": len(models),
                    "models": models,
                }
                _model_cache["user"] = {
                    "fetched_at": time.monotonic(), "data": result,
                }
                logger.info("Fetched models from /models/user (%d).", len(models))
                return result
            elif resp.status_code in (401, 403):
                logger.warning(
                    "GET /models/user auth failure: status=%d", resp.status_code,
                )
                raise OpenRouterError("api_key_invalid")
            else:
                fallback_reason = f"http_{resp.status_code}"
                logger.warning(
                    "GET /models/user status=%d, falling back.", resp.status_code,
                )
        except OpenRouterError:
            raise
        except httpx.TimeoutException:
            fallback_reason = "timeout"
            logger.warning("GET /models/user timed out, falling back.")
        except Exception as exc:
            fallback_reason = type(exc).__name__
            logger.warning(
                "GET /models/user failed (%s), falling back.", fallback_reason,
            )

        # Check public_fallback cache before network call
        if not refresh and "public_fallback" in _model_cache:
            entry = _model_cache["public_fallback"]
            if (now - entry["fetched_at"]) < MODEL_LIST_TTL:
                return {**entry["data"], "cached": True}

    # ── Public path: /models (no Authorization header) ────────────────────
    source = "public_fallback" if api_key else "public"
    try:
        resp = await client.get(
            f"{OPENROUTER_BASE_URL}/models",
            timeout=timeout,
        )
        if resp.status_code in (401, 403):
            raise OpenRouterError("api_key_required_by_openrouter")
        if not resp.is_success:
            raise OpenRouterError("openrouter_models_error")
        raw_data = resp.json().get("data")
        if not isinstance(raw_data, list):
            raise OpenRouterError("invalid_openrouter_models_response")
        models = [_normalise_model(m) for m in raw_data]
        result: dict = {
            "source": source,
            "cached": False,
            "count": len(models),
            "models": models,
        }
        if fallback_reason:
            result["fallback_reason"] = fallback_reason
        _model_cache[source] = {"fetched_at": time.monotonic(), "data": result}
        logger.info(
            "Fetched models from /models (%d, source=%s).", len(models), source,
        )
        return result
    except OpenRouterError:
        raise
    except httpx.TimeoutException:
        raise OpenRouterError("openrouter_timeout")
    except Exception as exc:
        logger.warning("GET /models failed: %s", type(exc).__name__)
        raise OpenRouterError("openrouter_models_error") from exc


def _normalise_model(raw: dict) -> dict:
    """Normalize a raw OpenRouter model object to the stable 12-field shape."""
    arch = raw.get("architecture") or {}
    top = raw.get("top_provider") or {}
    ctx = raw.get("context_length")
    if ctx is None:
        ctx = top.get("context_length")
    return {
        "id":                    raw.get("id", ""),
        "name":                  raw.get("name") or raw.get("id", ""),
        "description":           raw.get("description") or "",
        "context_length":        ctx,
        "max_completion_tokens": top.get("max_completion_tokens"),
        "supported_parameters":  raw.get("supported_parameters") or [],
        "input_modalities":      arch.get("input_modalities") or [],
        "output_modalities":     arch.get("output_modalities") or [],
        "pricing":               raw.get("pricing") or {},
        "top_provider":          top,
        "created":               raw.get("created"),
        "canonical_slug":        raw.get("canonical_slug") or "",
    }


def invalidate_model_cache() -> None:
    """Clear model cache. Called when API key or proxy config changes."""
    _model_cache.clear()


def get_cached_model_metadata(model_id: str) -> dict | None:
    """Return the normalised model dict from the in-process cache, or None.

    Pure read - no network calls, no async. Returns None if the model
    has not been fetched or is not in any cached source.
    """
    for entry in _model_cache.values():
        for m in entry.get("data", {}).get("models", []):
            if m.get("id") == model_id:
                return m
    return None


# ---------------------------------------------------------------------------
# API key validation
# ---------------------------------------------------------------------------

async def validate_api_key(candidate_key: str) -> str:
    """Validate a candidate API key via GET /api/v1/key.

    Returns:
        "valid"                   - 200 from /key, or server reachable but
                                    endpoint unknown (e.g. 404, 500).
        "invalid"                 - 401 or 403 from /key.
        "validation_unavailable"  - timeout, network error, or connection failure.
    """
    client = get_client()
    timeout = httpx.Timeout(MODELS_FETCH_TIMEOUT)
    try:
        resp = await client.get(
            f"{OPENROUTER_BASE_URL}/key",
            headers={"Authorization": f"Bearer {candidate_key}"},
            timeout=timeout,
        )
        if resp.status_code in (401, 403):
            return "invalid"
        # 200 or any non-auth response → treat as valid
        return "valid"
    except (httpx.TimeoutException, httpx.ConnectError, OSError):
        return "validation_unavailable"
    except Exception as exc:
        logger.warning(
            "API key validation failed with unexpected error type: %s",
            type(exc).__name__,
        )
        return "validation_unavailable"


# ---------------------------------------------------------------------------
# Chat completion
# ---------------------------------------------------------------------------

async def complete(
    messages: list[dict],
    model_id: str,
    gen_params: dict,
    provider: dict,
) -> dict:
    """Send a non-streaming completion request. Returns the raw OpenRouter response.

    gen_params must already be validated by validate_and_filter_gen_params().
    provider dict is passed through as-is under the "provider" key.
    Raises OpenRouterError with a sanitized reason on any failure (μ8).
    """
    api_key = get_secret(SECRET_API_KEY)
    if not api_key:
        raise OpenRouterError("api_key_not_set")

    payload: dict = {
        "model": model_id,
        "messages": messages,
        "provider": provider,
        "stream": False,
        **gen_params,
    }

    timeout = httpx.Timeout(COMPLETION_TIMEOUT)
    client = get_client()

    # Log only non-sensitive fields.
    logger.info("Completion request: model=%s", model_id)
    start = time.monotonic()

    try:
        response = await client.post(
            f"{OPENROUTER_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout,
        )
        latency_ms = int((time.monotonic() - start) * 1000)
        logger.info("Completion response: model=%s status=%d latency_ms=%d",
                    model_id, response.status_code, latency_ms)

        # Map HTTP error status to sanitized reason codes (μ8).
        # Raw response body is NEVER read or forwarded.
        if response.status_code in (401, 403):
            raise OpenRouterError("openrouter_auth_failed")
        if response.status_code == 402:
            raise OpenRouterError("openrouter_insufficient_credits")
        if response.status_code == 429:
            raise OpenRouterError("openrouter_rate_limited")
        if response.status_code >= 500:
            raise OpenRouterError("openrouter_server_error")
        if not response.is_success:
            raise OpenRouterError("openrouter_error")

        return response.json()

    except OpenRouterError:
        raise
    except httpx.TimeoutException:
        logger.warning("Completion request timed out: model=%s", model_id)
        raise OpenRouterError("openrouter_timeout")
    except Exception as exc:
        logger.warning("Completion request failed: %s", type(exc).__name__)
        raise OpenRouterError("openrouter_error") from exc


# ---------------------------------------------------------------------------
# Streaming chat completion
# ---------------------------------------------------------------------------

def _status_to_reason(status_code: int) -> str:
    """Map an HTTP status to the sanitized OpenRouterError reason codes."""
    if status_code in (401, 403):
        return "openrouter_auth_failed"
    if status_code == 402:
        return "openrouter_insufficient_credits"
    if status_code == 429:
        return "openrouter_rate_limited"
    if status_code >= 500:
        return "openrouter_server_error"
    return "openrouter_error"


async def complete_stream(
    messages: list[dict],
    model_id: str,
    gen_params: dict,
    provider: dict,
) -> AsyncIterator[str]:
    """Send a streaming completion request; yield content deltas as they arrive.

    SSE handling per the OpenRouter spec:
      - lines starting with ':' are keepalive comments and are skipped,
      - 'data: [DONE]' terminates the stream,
      - a chunk carrying an "error" object (or finish_reason == "error") maps
        to a sanitized OpenRouterError; the upstream message is never
        forwarded (μ8).

    Privacy rules match complete(): request/response bodies are never logged;
    only model_id, HTTP status, and latency are logged.
    """
    api_key = get_secret(SECRET_API_KEY)
    if not api_key:
        raise OpenRouterError("api_key_not_set")

    payload: dict = {
        "model": model_id,
        "messages": messages,
        "provider": provider,
        "stream": True,
        **gen_params,
    }

    timeout = httpx.Timeout(
        connect=STREAM_CONNECT_TIMEOUT,
        read=STREAM_READ_TIMEOUT,
        write=STREAM_CONNECT_TIMEOUT,
        pool=STREAM_CONNECT_TIMEOUT,
    )
    client = get_client()

    logger.info("Streaming completion request: model=%s", model_id)
    start = time.monotonic()

    try:
        async with client.stream(
            "POST",
            f"{OPENROUTER_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout,
        ) as response:
            if response.status_code != 200:
                # Error body is never read or forwarded (μ8).
                logger.warning(
                    "Streaming completion HTTP error: model=%s status=%d",
                    model_id, response.status_code,
                )
                raise OpenRouterError(_status_to_reason(response.status_code))

            async for line in response.aiter_lines():
                line = line.strip()
                if not line or line.startswith(":"):
                    continue  # blank or keepalive comment
                if not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break

                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    logger.warning("Skipping malformed stream chunk: model=%s", model_id)
                    continue

                error_obj = chunk.get("error")
                choices = chunk.get("choices") or []
                choice = choices[0] if choices and isinstance(choices[0], dict) else {}

                if error_obj is not None or choice.get("finish_reason") == "error":
                    code = error_obj.get("code") if isinstance(error_obj, dict) else None
                    reason = (
                        _status_to_reason(code)
                        if isinstance(code, int)
                        else "openrouter_error"
                    )
                    logger.warning(
                        "Mid-stream provider error: model=%s reason=%s",
                        model_id, reason,
                    )
                    raise OpenRouterError(reason)

                delta = choice.get("delta") or {}
                content = delta.get("content")
                if isinstance(content, str) and content:
                    yield content

        latency_ms = int((time.monotonic() - start) * 1000)
        logger.info(
            "Streaming completion finished: model=%s latency_ms=%d",
            model_id, latency_ms,
        )

    except OpenRouterError:
        raise
    except httpx.TimeoutException:
        logger.warning("Streaming completion timed out: model=%s", model_id)
        raise OpenRouterError("openrouter_timeout")
    except Exception as exc:
        # CancelledError/GeneratorExit are BaseException subclasses and pass
        # through untouched, preserving client-abort semantics.
        logger.warning("Streaming completion failed: %s", type(exc).__name__)
        raise OpenRouterError("openrouter_error") from exc
