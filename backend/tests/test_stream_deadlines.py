"""Audit KÖK 4: long work with no heartbeat, and long waits with no ceiling.

Two loops that could run forever. The completion stream's only bound was a
PER-READ idle timeout, and OpenRouter's ": OPENROUTER PROCESSING" keepalive
comments reset it on every tick - so a request queued behind a busy provider
held a generator, an HTTP response and a worker slot open indefinitely, with
nothing anywhere to stop it. /speak_stream's drain loop had no deadline at
all, while its structurally identical sibling in stream_hook carried one and
documented why.
"""

from __future__ import annotations

import itertools

import pytest

import config
import openrouter
from openrouter import OpenRouterError


class _FakeResponse:
    def __init__(self, lines):
        self.status_code = 200
        self._lines = lines

    async def aiter_bytes(self):
        """Bytes, not lines: complete_stream splits the stream itself now.

        httpx's aiter_lines() breaks on U+2028/U+2029/U+0085, which the SSE
        spec says are ordinary content, so openrouter._aiter_sse_lines does the
        splitting on raw bytes instead. The fixtures below stay as str - one
        logical line each - and the terminator is added here, which is also what
        the wire looks like.
        """
        for line in self._lines:
            yield line.encode("utf-8") + b"\n"


class _FakeStreamCtx:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *exc):
        return False


class _FakeClient:
    def __init__(self, lines):
        self._lines = lines

    def stream(self, *a, **kw):
        return _FakeStreamCtx(_FakeResponse(self._lines))


class _ScriptedClock:
    """A clock for THIS module only, handing out a written-down sequence.

    `monkeypatch.setattr(openrouter.time, "monotonic", ...)` looks local and is
    not: openrouter.time IS the shared time module, so the patch lands on
    everything in the process - including asyncio's own scheduler, which reads
    the clock several times per await. Measured 2026-08-10 while writing the
    test below: twenty three readings were taken in one short stream and only
    twelve of them came from openrouter. A sequence handed out that way is
    consumed by whoever asks first, and a test that needs reading number four
    to be the long one gets whatever the event loop left it.

    The fixture underneath survives that because its readings only ever climb;
    anything that has to place a specific value at a specific line cannot.
    Everything except monotonic falls through to the real module.
    """

    def __init__(self, readings):
        import time as _real_time
        self._real = _real_time
        self._readings = iter(readings)
        self._last = readings[-1]

    def __getattr__(self, name):
        return getattr(self._real, name)

    def monotonic(self):
        self._last = next(self._readings, self._last)
        return self._last


@pytest.fixture()
def fake_clock(monkeypatch):
    """A monotonic clock the test drives, so no test ever really waits."""
    ticks = itertools.count(0.0, 5.0)
    current = {"t": 0.0}

    def _monotonic():
        current["t"] = next(ticks)
        return current["t"]

    monkeypatch.setattr(openrouter.time, "monotonic", _monotonic)
    return current


async def _drain(agen):
    out = []
    async for item in agen:
        out.append(item)
    return out


@pytest.mark.anyio
async def test_endless_keepalives_do_not_buy_endless_time(monkeypatch, fake_clock):
    """The bug in one test: the loop `continue`s on every comment line, and
    each of those continues used to restart the 90 s read timeout."""
    keepalives = [": OPENROUTER PROCESSING"] * 500
    monkeypatch.setattr(openrouter, "get_client", lambda: _FakeClient(keepalives))
    monkeypatch.setattr(openrouter, "get_secret", lambda name: "sk-test")

    with pytest.raises(OpenRouterError) as exc:
        await _drain(openrouter.complete_stream(
            [{"role": "user", "content": "hi"}], "test/model", {}, None,
        ))
    assert "openrouter_timeout" in str(exc.value)


@pytest.mark.anyio
async def test_a_provider_that_starts_talking_is_not_cut_off(monkeypatch):
    """The first-token budget must not fire on a stream that is working."""
    lines = [
        ": OPENROUTER PROCESSING",
        'data: {"choices":[{"delta":{"content":"Once "}}]}',
        'data: {"choices":[{"delta":{"content":"upon a time."}}]}',
        "data: [DONE]",
    ]
    monkeypatch.setattr(openrouter, "get_client", lambda: _FakeClient(lines))
    monkeypatch.setattr(openrouter, "get_secret", lambda name: "sk-test")

    out = await _drain(openrouter.complete_stream(
        [{"role": "user", "content": "hi"}], "test/model", {}, None,
    ))
    assert "".join(out) == "Once upon a time."


@pytest.mark.anyio
async def test_a_stream_that_never_ends_still_ends(monkeypatch, fake_clock):
    """Past the first token only the total budget applies - but it does."""
    lines = ['data: {"choices":[{"delta":{"content":"and "}}]}'] * 5000
    monkeypatch.setattr(openrouter, "get_client", lambda: _FakeClient(lines))
    monkeypatch.setattr(openrouter, "get_secret", lambda name: "sk-test")

    with pytest.raises(OpenRouterError) as exc:
        await _drain(openrouter.complete_stream(
            [{"role": "user", "content": "hi"}], "test/model", {}, None,
        ))
    assert "openrouter_timeout" in str(exc.value)


@pytest.mark.anyio
async def test_a_long_silence_after_the_first_token_is_not_the_first_token_budget(
    monkeypatch,
):
    """The two budgets have to stay told apart, and no test told them apart.

    Both of the timeout tests above assert only that `openrouter_timeout` is
    raised eventually - neither says WHICH ceiling fired. Drop `not saw_token`
    from the first-token check and the short budget starts applying to every
    line for the rest of the stream: a model that thinks for three minutes
    between paragraphs, which is ordinary for a long reply on a busy provider,
    gets killed at 120 s with the reply already half written and thrown away.
    The whole suite stayed green under that change.

    So: one token, then a silence far past the first-token budget and far short
    of the total one. The stream must finish.
    """
    gap = config.STREAM_FIRST_TOKEN_TIMEOUT * 2
    assert gap < config.STREAM_TOTAL_TIMEOUT, "the fixture must sit between them"
    # One reading per line, plus the one taken before the loop starts. The last
    # value repeats so an extra call (a latency log, say) cannot shift the run.
    monkeypatch.setattr(
        openrouter, "time", _ScriptedClock([0.0, 1.0, 2.0, gap, gap + 1.0]))
    lines = [
        ": OPENROUTER PROCESSING",
        'data: {"choices":[{"delta":{"content":"Once "}}]}',
        'data: {"choices":[{"delta":{"content":"upon a time."}}]}',
        "data: [DONE]",
    ]
    monkeypatch.setattr(openrouter, "get_client", lambda: _FakeClient(lines))
    monkeypatch.setattr(openrouter, "get_secret", lambda name: "sk-test")

    out = await _drain(openrouter.complete_stream(
        [{"role": "user", "content": "hi"}], "test/model", {}, None,
    ))
    assert "".join(out) == "Once upon a time."


def test_the_two_budgets_are_ordered_sensibly():
    """A first-token budget above the total one would be unreachable."""
    assert config.STREAM_FIRST_TOKEN_TIMEOUT < config.STREAM_TOTAL_TIMEOUT
    # And both must exceed the per-read idle timeout, or they would pre-empt
    # the timeout that already handles an actually-dead connection.
    assert config.STREAM_FIRST_TOKEN_TIMEOUT > config.STREAM_READ_TIMEOUT


# The /speak_stream half of this audit item lived here as a source scan: it
# split routers/tts_runtime.py on "async def event_source" and looked for the
# words DRAIN_TIMEOUT_S, deadline and voice_error. A comment carrying those
# words satisfied it; a deadline computed and never compared would too.
#
# The behaviour it was standing in for is now proven for real, one file over,
# in test_packaging_gate.py::test_a_wedged_worker_does_not_hold_the_response_open
# - a wedged synth, DRAIN_TIMEOUT_S turned down to half a second, and the
# assertion that the response closes with a coded voice_error rather than
# staying open. Measured 2026-08-10: with the `if anyio.current_time() >=
# deadline` line disabled, that test fails on a 30 s response while the scan
# above stayed green. So the scan is deleted rather than rewritten; there was
# nothing left for it to prove.
