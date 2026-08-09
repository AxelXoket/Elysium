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

import hashlib
import json
import time
import logging
from typing import Any, AsyncIterator

import anyio.to_thread
import httpx

from config import (
    OPENROUTER_BASE_URL,
    MODEL_LIST_TTL,
    MODELS_FETCH_TIMEOUT,
    COMPLETION_TIMEOUT,
    STREAM_CONNECT_TIMEOUT,
    STREAM_READ_TIMEOUT,
    STREAM_FIRST_TOKEN_TIMEOUT,
    STREAM_TOTAL_TIMEOUT,
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
      api_key_invalid, openrouter_auth_failed, openrouter_moderation_blocked,
      openrouter_insufficient_credits, openrouter_rate_limited,
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
    except httpx.ProxyError as exc:
        # The user's OWN proxy rejected or could not establish the tunnel (407,
        # SOCKS auth, "could not connect"). Folding it into the generic handler
        # blamed OpenRouter for a local misconfiguration - "The provider
        # returned an error. Please try again." - and left the user retrying
        # forever against a proxy that will never work. The proxy health gate
        # cannot cover this: it only runs when proxy_required is on.
        logger.warning("%s rejected by the configured proxy.", "GET /models")
        raise OpenRouterError("proxy_auth_failed") from exc
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

#: The only value this app ever sends for `modalities`, built once so the two
#: payloads cannot drift. Asking for image output is asking for BOTH: dropping
#: "text" would tell the provider to answer with a picture and no words.
MODALITIES_WITH_IMAGE: tuple[str, ...] = ("text", "image")


def image_urls_from(container: object) -> list[str]:
    """Pull generated-image URLs out of a delta or an assistant message.

    The shape is a list under `images`, each entry holding an image_url object
    whose url is a data: URL, optionally with a `type` discriminator naming it.
    (Written in prose rather than as a literal so the release gate that confines
    OUTBOUND image_url construction has only real code to look at - see P-03.)

    Unrecognised entries are IGNORED rather than rejected, and that is a
    deliberate choice with a known failure mode on the other side: OpenRouter's
    own AI-SDK provider requires the `type` discriminator and silently filters
    entries that lack it - while their published schema for the item omits
    `type` entirely. A parser strict about shape would report "the model
    returned no images" on a reply that did return some. So anything that yields
    a usable string url is accepted, and anything else is skipped quietly
    because a malformed entry is the provider's problem, not the reader's.

    Nothing is validated about the URL here beyond it being a non-empty string.
    Whether it is a `data:` URL we may decode - as opposed to a third-party
    https:// host we must refuse - is a privacy decision, and it belongs to the
    caller that knows the egress rules.
    """
    if not isinstance(container, dict):
        return []
    entries = container.get("images")
    if not isinstance(entries, list):
        return []
    out: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        holder = entry.get("image_url")
        url = holder.get("url") if isinstance(holder, dict) else None
        if isinstance(url, str) and url:
            out.append(url)
    return out


# ---------------------------------------------------------------------------
# Upstream failure -> sanitized reason
# ---------------------------------------------------------------------------

#: How much of an error body we are willing to look at. The body is INSPECTED
#: for a shape and then dropped; nothing in it is forwarded or logged, so this
#: cap is not about privacy but about refusing to buffer megabytes of a reply
#: we have already decided to throw away.
_ERROR_BODY_PEEK_LIMIT = 64 * 1024


def _is_moderation_error(payload: object) -> bool:
    """True when an OpenRouter error envelope carries ModerationErrorMetadata.

    Only the SHAPE is read. That distinction is the whole reason this is a
    predicate returning a bool rather than a parser returning fields: the
    metadata holds `flagged_input`, a verbatim copy of what the reader just
    typed, and `reasons`, a moderation label applied to them. Neither leaves
    this function, neither reaches a log line, and the caller learns exactly
    one bit.

    `reasons` being a list is what separates this from ProviderErrorMetadata,
    which is the other documented metadata shape and carries `{provider_name,
    raw}` instead.
    """
    if not isinstance(payload, dict):
        return False
    error = payload.get("error")
    if not isinstance(error, dict):
        return False
    metadata = error.get("metadata")
    if not isinstance(metadata, dict):
        return False
    return isinstance(metadata.get("reasons"), list)


def _parse_error_payload(body: bytes | None) -> object | None:
    """Decode an error body far enough to tell its shape, or None.

    Returns None rather than raising for every way this can go wrong (absent,
    oversized, not UTF-8, not JSON) because every caller wants the same thing
    from a body it cannot read: fall back to classifying on the status alone.
    """
    if not body or len(body) > _ERROR_BODY_PEEK_LIMIT:
        return None
    try:
        return json.loads(body)
    except ValueError:
        # Covers JSONDecodeError and UnicodeDecodeError, both ValueError
        # subclasses. An HTML error page from a proxy lands here.
        return None


async def _peek_error_body(response: httpx.Response) -> bytes:
    """Read at most _ERROR_BODY_PEEK_LIMIT bytes of a streaming error response.

    aread() would be shorter and is deliberately not used: it has no ceiling,
    and this runs on a response we already know is a failure, from a host that
    is by definition not behaving as expected. Reading in chunks and stopping
    is the difference between a bounded peek and letting the far end decide how
    much memory this process spends on an error it will not repeat.
    """
    buf = b""
    try:
        async for chunk in response.aiter_bytes():
            buf += chunk
            if len(buf) > _ERROR_BODY_PEEK_LIMIT:
                break
    except Exception:  # pragma: no cover - the body is optional information
        # A truncated or reset error body must never turn into a different
        # exception than the status already earned. Classify on status alone.
        pass
    return buf


def _status_to_reason(status_code: int, payload: object | None = None) -> str:
    """Map an upstream HTTP status to the sanitized OpenRouterError reasons.

    payload is the decoded error envelope when one could be read, and is used
    for exactly one decision - see the 403 branch. Callers that have no body
    (or could not decode it) pass nothing and get the status-only answer.
    """
    if status_code == 401:
        return "openrouter_auth_failed"
    if status_code == 403:
        # 401 and 403 used to share openrouter_auth_failed. OpenRouter documents
        # 403 as exactly one thing - "your chosen model requires moderation and
        # your input was flagged" - so that mapping told a reader whose prompt
        # was refused to go and check an API key that was never the problem, and
        # sent them off rotating a working key.
        #
        # The body is parsed but NOT forwarded. The module rule above ("the raw
        # response body is never read or forwarded") is about handing the
        # provider's prose to the reader; recognising a documented shape and
        # dropping every field of it does not break that, and this is the one
        # place where the status alone genuinely is ambiguous.
        #
        # Ambiguous because a 403 on this route need not come from OpenRouter at
        # all: a corporate proxy or a CDN in front of it answers 403 with an
        # HTML page. Announcing "your message was blocked by moderation" there
        # would be the same lie pointing the other way, so an unrecognised body
        # falls through to the generic code rather than guessing.
        if _is_moderation_error(payload):
            return "openrouter_moderation_blocked"
        return "openrouter_error"
    if status_code == 402:
        return "openrouter_insufficient_credits"
    if status_code == 429:
        return "openrouter_rate_limited"
    if status_code >= 500:
        return "openrouter_server_error"
    return "openrouter_error"


async def complete(
    messages: list[dict],
    model_id: str,
    gen_params: dict,
    provider: dict,
    modalities: tuple[str, ...] | list[str] | None = None,
) -> dict:
    """Send a non-streaming completion request. Returns the raw OpenRouter response.

    gen_params must already be validated by validate_and_filter_gen_params().
    provider dict is passed through as-is under the "provider" key.
    Raises OpenRouterError with a sanitized reason on any failure (μ8).

    modalities is an EXPLICIT parameter and deliberately not a gen_param.
    validate_and_filter_gen_params is a numeric allow-list: it would drop the
    key silently, and widening _PARAM_SPEC to carry a list would break a guard
    whose whole value is that it only understands numbers. Absent by default, so
    every existing caller sends exactly the bytes it sent before.
    """
    # Off the event loop (audit KÖK 8). This is a SQLCipher open, and it used
    # to happen here on the loop for EVERY message sent - immediately before
    # the outbound request, which is the worst possible moment: any writer
    # holding the lock stalls it for up to the busy_timeout, and every other
    # live SSE stream in the process freezes with it.
    #
    # run_sync's default is abandon_on_cancel=False, so a client that
    # disconnects during this read has its CancelledError delivered after the
    # read returns rather than mid-query. completions.py:1489 already relies on
    # that same property and says so.
    api_key = await anyio.to_thread.run_sync(get_secret, SECRET_API_KEY)
    if not api_key:
        raise OpenRouterError("api_key_not_set")

    payload: dict = {
        "model": model_id,
        "messages": messages,
        "provider": provider,
        "stream": False,
        **gen_params,
    }
    if modalities:
        # AFTER the gen_params spread so a stray key of the same name in a
        # validated param dict could never decide this.
        payload["modalities"] = list(modalities)

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

        # Map HTTP error status to sanitized reason codes (μ8). The raw body is
        # NEVER forwarded; _status_to_reason inspects it for one documented
        # shape and keeps nothing. This branch used to carry its own copy of the
        # status table, kept in sync with _status_to_reason by hand across three
        # call sites - the drift that copy invited is the reason there is now
        # one table.
        if not response.is_success:
            raise OpenRouterError(_status_to_reason(
                response.status_code, _parse_error_payload(response.content),
            ))

        return response.json()

    except OpenRouterError:
        raise
    except httpx.TimeoutException:
        logger.warning("Completion request timed out: model=%s", model_id)
        raise OpenRouterError("openrouter_timeout")
    except httpx.ProxyError as exc:
        # The user's OWN proxy rejected or could not establish the tunnel (407,
        # SOCKS auth, "could not connect"). Folding it into the generic handler
        # blamed OpenRouter for a local misconfiguration - "The provider
        # returned an error. Please try again." - and left the user retrying
        # forever against a proxy that will never work. The proxy health gate
        # cannot cover this: it only runs when proxy_required is on.
        logger.warning("%s rejected by the configured proxy.", "Completion request")
        raise OpenRouterError("proxy_auth_failed") from exc
    except Exception as exc:
        logger.warning("Completion request failed: %s", type(exc).__name__)
        raise OpenRouterError("openrouter_error") from exc


# ---------------------------------------------------------------------------
# Streaming chat completion
# ---------------------------------------------------------------------------

async def _aiter_sse_lines(response: httpx.Response) -> AsyncIterator[str]:
    """Yield SSE lines, splitting only on CRLF, LF or CR.

    A drop-in replacement for `response.aiter_lines()`, which cannot be used
    here. httpx's LineDecoder was deliberately changed in 0.24.0 to mirror
    `str.splitlines()` (encode/httpx#2423, "resulting in significant speed up"),
    so it also breaks on U+0085 NEL, U+2028 LINE SEPARATOR and U+2029 PARAGRAPH
    SEPARATOR. The WHATWG event-stream format allows exactly three terminators:
    CRLF, LF, CR. And RFC 8259 section 7 only requires a JSON writer to escape
    U+0000-U+001F, so those three arrive literally - JavaScript's
    JSON.stringify escapes none of them, and OpenRouter serialises in
    TypeScript. One of them inside a content delta therefore tore the frame in
    two: the first half failed json.loads and was logged-and-skipped, the second
    half failed the "data:" prefix test and was dropped without even that. The
    reader lost that piece of the reply from the screen, from the vault AND from
    the voice, with no error anywhere. httpx does not consider its behaviour a
    bug, so there is no upstream fix to wait for.

    Splitting on BYTES is what fixes it: `bytes.splitlines()` knows only CR, LF
    and CRLF, and no UTF-8 continuation byte (0x80-0xBF) can be mistaken for
    either, so a boundary can never fall inside a multi-byte character - which
    is why decoding per finished line is safe. This is the same approach the
    official openai and anthropic SDKs take, both with the same one-line comment
    ("Split before decoding so splitlines() only uses \\r and \\n"); both
    abandoned aiter_lines for it.

    Deliberately line-level rather than event-level (httpx-sse, or an SDK-style
    SSEDecoder). The caller's timeout tick fires on EVERY line including
    keepalive comments, and its comment explains that this is the whole point:
    the comments are what buy a queued request time. An event decoder yields
    nothing at all for a comment-only stream, which would silently delete
    STREAM_FIRST_TOKEN_TIMEOUT.
    """
    # aiter_bytes(), NOT aiter_raw(): httpx advertises Accept-Encoding, so the
    # raw stream may be gzip-framed and the splitter would see compressed
    # bytes - an outage that only reproduces against providers that compress.
    # chunk_size is left unset on purpose: ByteChunker is then a pass-through,
    # so this adds no buffering to a first-token-critical path.
    buf = b""
    async for chunk in response.aiter_bytes():
        if not chunk:
            continue
        buf += chunk
        if buf.endswith(b"\r"):
            # That CR may be the first half of a CRLF straddling two network
            # reads. Hold it back rather than emit a phantom blank line - a
            # blank line is SSE's event-dispatch signal.
            head, buf = buf[:-1], b"\r"
        else:
            head, buf = buf, b""
        if not head:
            continue
        lines = head.splitlines()
        if lines and not head.endswith((b"\n", b"\r")):
            # The last segment has no terminator yet; re-buffer it, keeping any
            # held-back CR after it.
            buf = lines.pop() + buf
        for line in lines:
            yield line.decode("utf-8", "replace")
    # Whatever is left: an unterminated final line and/or a lone held-back CR.
    # splitlines() here strips that CR instead of leaking it into the payload.
    for line in buf.splitlines():
        yield line.decode("utf-8", "replace")


async def complete_stream(
    messages: list[dict],
    model_id: str,
    gen_params: dict,
    provider: dict,
    modalities: tuple[str, ...] | list[str] | None = None,
    on_image=None,
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

    modalities: see complete(). Absent by default.

    on_image: an optional sink for generated images. THIS GENERATOR ONLY EVER
    YIELDS `str`. Every consumer of the yielded value assumes that - the text
    accumulator, the tag stripper, the TTS speaker, the SSE encoder and the
    final "".join - so an image element travelling down the same channel would
    either raise a TypeError mid-reply or, worse, be treated as text: stored in
    messages.content, painted into the bubble, and read aloud. Splitting the
    channel HERE, at the one place that knows the difference, is what keeps all
    of that impossible instead of merely unlikely. The sink is called with a raw
    `data:` URL string and must not raise.
    """
    # Off the event loop (audit KÖK 8). See complete() for the full reasoning;
    # this is the streaming twin and the more damaging of the two, because the
    # freeze it caused landed on a path whose whole purpose is to keep other
    # streams flowing. The read sits before the first yield, so an async
    # generator that is never iterated still never pays for it.
    api_key = await anyio.to_thread.run_sync(get_secret, SECRET_API_KEY)
    if not api_key:
        raise OpenRouterError("api_key_not_set")

    payload: dict = {
        "model": model_id,
        "messages": messages,
        "provider": provider,
        "stream": True,
        **gen_params,
    }
    if modalities:
        payload["modalities"] = list(modalities)

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
                # The body is peeked at, bounded, and never forwarded or logged
                # (μ8) - only the sanitized reason reaches the log line below.
                # It has to be read here rather than skipped: a 403 means one of
                # two unrelated things and the body is the only thing that tells
                # them apart. See _status_to_reason.
                reason = _status_to_reason(
                    response.status_code,
                    _parse_error_payload(await _peek_error_body(response)),
                )
                logger.warning(
                    "Streaming completion HTTP error: model=%s status=%d reason=%s",
                    model_id, response.status_code, reason,
                )
                raise OpenRouterError(reason)

            saw_token = False
            dropped_frames = 0
            #: sha256 of every image url already handed to the sink. See the
            #: dedup comment in the loop below.
            seen_images: set[bytes] = set()
            async for line in _aiter_sse_lines(response):
                # The only bound that survives a keepalive. Everything below
                # `continue`s on a comment line, and each of those comments
                # reset httpx's per-read timeout - so a queued request with a
                # chatty provider could hold this generator open forever.
                # Checked on EVERY line, comments included, which is the whole
                # point: the comments are the thing that used to buy time.
                waited = time.monotonic() - start
                if not saw_token and waited > STREAM_FIRST_TOKEN_TIMEOUT:
                    logger.warning(
                        "Stream produced no token in %.0fs: model=%s",
                        waited, model_id,
                    )
                    raise OpenRouterError("openrouter_timeout")
                if waited > STREAM_TOTAL_TIMEOUT:
                    logger.warning(
                        "Stream exceeded its wall-clock budget (%.0fs): model=%s",
                        waited, model_id,
                    )
                    raise OpenRouterError("openrouter_timeout")

                line = line.strip()
                if not line or line.startswith(":"):
                    continue  # blank or keepalive comment
                if not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                if not data:
                    # A `data:` with no value is legal SSE and json.loads("")
                    # raises - which would now be reported as a protocol
                    # violation. It is not one.
                    continue

                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    # With a spec-correct splitter above, this is no longer a
                    # frame we tore in half: it is the provider sending
                    # something that is not JSON. Counted and logged at ERROR
                    # with a BOUNDED repr - the payload is model output, so it
                    # never goes to the log whole.
                    #
                    # Deliberately not fatal. Failing the turn here would skip
                    # finalize(), voice.finish() and drain_events, so a reply the
                    # reader had already finished reading would come back as an
                    # error banner with trim_broken_tail-shortened stored text
                    # and truncated audio. completions.py's KÖK 16 comment
                    # rejects exactly that trade for exactly this reason. One
                    # frame is a hole; the whole turn is a loss.
                    dropped_frames += 1
                    logger.error(
                        "Malformed stream frame: model=%s len=%d head=%.60r",
                        model_id, len(data), data,
                    )
                    continue

                error_obj = chunk.get("error")
                choices = chunk.get("choices") or []
                choice = choices[0] if choices and isinstance(choices[0], dict) else {}

                if error_obj is not None or choice.get("finish_reason") == "error":
                    code = error_obj.get("code") if isinstance(error_obj, dict) else None
                    # `chunk` IS the error envelope here, already decoded, so
                    # the 403 branch gets the same evidence it gets on the HTTP
                    # path. A moderation block can arrive either way: refused
                    # before the stream opens, or mid-reply once a provider has
                    # seen enough of what it is generating.
                    reason = (
                        _status_to_reason(code, chunk)
                        if isinstance(code, int)
                        else "openrouter_error"
                    )
                    logger.warning(
                        "Mid-stream provider error: model=%s reason=%s",
                        model_id, reason,
                    )
                    raise OpenRouterError(reason)

                delta = choice.get("delta") or {}
                # Images may ride the delta, or arrive only on a final
                # aggregated `message` - the live spec declares neither, so both
                # are read and neither is assumed. They leave through the sink,
                # never through the yield: see the docstring.
                if on_image is not None:
                    for url in image_urls_from(delta) + image_urls_from(
                        choice.get("message")
                    ):
                        # Deduplicated, and this is the ORDINARY case rather than
                        # a pathological one: the documented shape is images on
                        # each delta plus a final aggregated `message`, so every
                        # picture is seen at least twice and this loop runs on
                        # every chunk. Without the check each one became two
                        # attachment rows sharing one blob - which also halved
                        # the per-message cap, because that counts rows.
                        #
                        # Hashing the url rather than keeping it: these strings
                        # are megabytes, and the set only needs identity.
                        digest = hashlib.sha256(url.encode("utf-8")).digest()
                        if digest in seen_images:
                            continue
                        seen_images.add(digest)
                        saw_token = True
                        on_image(url)

                content = delta.get("content")
                if isinstance(content, str) and content:
                    # Past this point the provider has demonstrably started,
                    # so only the total budget still applies.
                    saw_token = True
                    yield content

        latency_ms = int((time.monotonic() - start) * 1000)
        if dropped_frames:
            logger.error(
                "Stream completed with %d unparseable frame(s): model=%s",
                dropped_frames, model_id,
            )
        logger.info(
            "Streaming completion finished: model=%s latency_ms=%d",
            model_id, latency_ms,
        )

    except OpenRouterError:
        raise
    except httpx.TimeoutException:
        logger.warning("Streaming completion timed out: model=%s", model_id)
        raise OpenRouterError("openrouter_timeout")
    except httpx.ProxyError as exc:
        # The user's OWN proxy rejected or could not establish the tunnel (407,
        # SOCKS auth, "could not connect"). Folding it into the generic handler
        # blamed OpenRouter for a local misconfiguration - "The provider
        # returned an error. Please try again." - and left the user retrying
        # forever against a proxy that will never work. The proxy health gate
        # cannot cover this: it only runs when proxy_required is on.
        logger.warning("%s rejected by the configured proxy.", "Streaming completion")
        raise OpenRouterError("proxy_auth_failed") from exc
    except Exception as exc:
        # CancelledError/GeneratorExit are BaseException subclasses and pass
        # through untouched, preserving client-abort semantics.
        logger.warning("Streaming completion failed: %s", type(exc).__name__)
        raise OpenRouterError("openrouter_error") from exc
