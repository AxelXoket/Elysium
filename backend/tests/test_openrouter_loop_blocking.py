"""Audit KÖK 8, the worst one: the API key was read on the loop per message.

complete() and complete_stream() each opened the SQLCipher database to fetch
the API key, synchronously, on the event loop, unconditionally, immediately
before the outbound request. That is the single most expensive place in the
application to block: any other writer holding SQLite's lock stalls this read
for up to the busy_timeout, and every live SSE stream in the process freezes
with it - at the exact moment a stream is trying to ship its next sentence.

Unlike the router handlers, this one is not in a router at all. It is shared
code every send, regenerate and edit funnels through, so it fired on literally
every message the app sent.

The discriminator is the one used by test_settings_loop_blocking.py and
test_chat_read_loop_blocking.py: a real wall-clock stall injected into the
module's own reference to the blocking call, and a heartbeat coroutine counting
how many times the loop got control while the call was in flight.

The transport is faked and instant, so the stall is the ONLY thing in these
calls that can consume wall-clock time. That is what makes the assertion
falsifiable rather than decorative.
"""
from __future__ import annotations

import asyncio
import time

import httpx
import pytest

import openrouter

_STALL_S = 0.12

_OK_BODY = {"choices": [{"message": {"content": "hello"}}]}

_SSE = (
    b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
    b"data: [DONE]\n\n"
)


class _PostClient:
    """The narrowest shape complete() needs: one awaitable post()."""

    def __init__(self, response: httpx.Response):
        self._response = response

    async def post(self, *a, **kw):
        return self._response


class _StreamClient:
    """The narrowest shape complete_stream() needs."""

    def __init__(self, raw: bytes):
        self._raw = raw

    def stream(self, *a, **kw):
        raw = self._raw

        class _Ctx:
            async def __aenter__(self):
                class _Resp:
                    status_code = 200
                    is_success = True

                    async def aiter_bytes(self, *a, **kw):
                        yield raw

                return _Resp()

            async def __aexit__(self, *a):
                return False

        return _Ctx()


@pytest.fixture()
def slow_secret(monkeypatch):
    """Make openrouter's OWN get_secret reference cost real wall-clock time.

    Patched here rather than on secrets_service so the rest of the process
    keeps running at full speed, and so the stall fires whether or not the
    fix is in place - a stall that only existed on the fixed path would make
    the test circular.
    """
    real = openrouter.get_secret

    def slow(*args, **kwargs):
        time.sleep(_STALL_S)
        return real(*args, **kwargs)

    monkeypatch.setattr(openrouter, "get_secret", slow)


async def _ticks_during(coro) -> tuple[int, object]:
    """Run `coro`, counting how many times the loop got control meanwhile."""
    ticks = 0

    async def heartbeat():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.005)
            ticks += 1

    beat = asyncio.ensure_future(heartbeat())
    try:
        await asyncio.sleep(0.02)          # let the heartbeat settle
        before = ticks
        result = await coro
        return ticks - before, result
    finally:
        beat.cancel()


async def _drain(agen) -> list[str]:
    return [delta async for delta in agen]


# ── the loop keeps running ───────────────────────────────────────────────────

@pytest.mark.anyio
async def test_a_completion_does_not_freeze_the_loop(
    anyio_backend, client, slow_secret, monkeypatch
):
    monkeypatch.setattr(
        openrouter, "get_client",
        lambda: _PostClient(httpx.Response(status_code=200, json=_OK_BODY)),
    )
    ticks, out = await _ticks_during(
        openrouter.complete([{"role": "user", "content": "hi"}], "m", {}, None)
    )
    assert out == _OK_BODY
    assert ticks > 1, "the loop was frozen while the API key was read"


@pytest.mark.anyio
async def test_a_streaming_completion_does_not_freeze_the_loop(
    anyio_backend, client, slow_secret, monkeypatch
):
    """The one that actually hurt: it freezes the other streams."""
    monkeypatch.setattr(openrouter, "get_client", lambda: _StreamClient(_SSE))
    ticks, out = await _ticks_during(
        _drain(openrouter.complete_stream(
            [{"role": "user", "content": "hi"}], "m", {}, None,
        ))
    )
    assert out == ["hi"]
    assert ticks > 1, "the loop was frozen while a stream read the API key"


# ── and the key is still required, and still comes from the vault ────────────

@pytest.mark.anyio
async def test_a_missing_key_is_still_refused_before_any_request(
    anyio_backend, client, monkeypatch
):
    """The guard has to survive the thread hop, and it has to fire FIRST.

    get_client raising means the test fails loudly if the refusal ever stops
    happening before the transport is reached.
    """
    monkeypatch.setattr(openrouter, "get_secret", lambda *a, **kw: None)

    def explode():
        raise AssertionError("a request was built without an API key")

    monkeypatch.setattr(openrouter, "get_client", explode)

    with pytest.raises(openrouter.OpenRouterError) as excinfo:
        await openrouter.complete([{"role": "user", "content": "hi"}],
                                  "m", {}, None)
    assert excinfo.value.reason == "api_key_not_set"

    with pytest.raises(openrouter.OpenRouterError) as excinfo:
        async for _ in openrouter.complete_stream(
            [{"role": "user", "content": "hi"}], "m", {}, None,
        ):
            pass
    assert excinfo.value.reason == "api_key_not_set"


@pytest.mark.anyio
async def test_the_key_still_reaches_the_authorization_header(
    anyio_backend, client, monkeypatch
):
    """Moving the read must not change what gets sent."""
    seen: dict = {}

    class _Capture:
        async def post(self, *a, **kw):
            seen.update(kw.get("headers") or {})
            return httpx.Response(status_code=200, json=_OK_BODY)

    monkeypatch.setattr(openrouter, "get_secret", lambda *a, **kw: "sk-or-test")
    monkeypatch.setattr(openrouter, "get_client", lambda: _Capture())

    await openrouter.complete([{"role": "user", "content": "hi"}],
                              "m", {}, None)
    assert seen["Authorization"] == "Bearer sk-or-test"
