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

from pathlib import Path

import itertools

import pytest

import config
import openrouter
from openrouter import OpenRouterError

#: Absolute, because a test that only passes from one directory is a test that
#: will surprise somebody. Running `pytest backend/` from the repo root used to
#: fail eleven tests across four files with FileNotFoundError on a relative
#: path like 'tts/provision.py'. Measured 2026-08-10 and fixed here.
BACKEND = Path(__file__).resolve().parents[1]


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


def test_the_two_budgets_are_ordered_sensibly():
    """A first-token budget above the total one would be unreachable."""
    assert config.STREAM_FIRST_TOKEN_TIMEOUT < config.STREAM_TOTAL_TIMEOUT
    # And both must exceed the per-read idle timeout, or they would pre-empt
    # the timeout that already handles an actually-dead connection.
    assert config.STREAM_FIRST_TOKEN_TIMEOUT > config.STREAM_READ_TIMEOUT


def test_the_speak_stream_drain_loop_has_a_deadline():
    """Its sibling stream_hook.drain_events has carried DRAIN_TIMEOUT_S all
    along, with a docstring saying a wedged worker must not hold an HTTP
    response open forever. The same loop next door had no ceiling."""
    from pathlib import Path

    source = (BACKEND / "routers" / "tts_runtime.py").read_text(encoding="utf-8")
    body = source.split("async def event_source", 1)[1]
    assert "DRAIN_TIMEOUT_S" in body
    assert "deadline" in body
    # And it must be a CODED stop, not a silent one: audio that simply stops
    # is indistinguishable from a reply that had nothing more to say.
    head = body.split("sent_any = False", 1)[0]
    assert "voice_error" in head
