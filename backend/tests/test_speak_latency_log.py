"""What the listener waited for, written down.

A user timed twelve seconds from pressing Speak to hearing a word. The log for
that exact run held every worker event - loading, generating, decoding, done -
and still could not answer the question, because it recorded nothing at all
about the request: not when it arrived, and not whether the engine was already
loaded when it did. So the twelve seconds could be split as "the model was
still loading" or as "synthesis is slow", and the two have nothing in common
except the silence.

These pin the wire, not the arithmetic. Timing that is only ever read by a
human is timing nobody notices has stopped being written.
"""

from __future__ import annotations

import json
import logging
import sys

import pytest

from tts import runtimes
# The same live voice environment the other runtime tests stand up.
from test_tts_runtime_api import voice, _fake_gpu  # noqa: F401


def _ready_voice(client, monkeypatch) -> str:
    _fake_gpu(monkeypatch)
    runtimes.register("fish_s2", sys.executable)
    uid = client.get("/api/v1/tts/models").json()["models"][0]["uid"]
    client.post("/api/v1/tts/active", json={"uid": uid})
    return uid


def _speak_stream(client, text: str) -> list[dict]:
    res = client.post("/api/v1/tts/speak_stream", json={"text": text})
    assert res.status_code == 200, res.text
    return [
        json.loads(line[6:])
        for line in res.text.splitlines()
        if line.startswith("data: ")
    ]


def test_first_audio_is_reported_once_however_many_chunks_follow(
    client, voice, monkeypatch,
):
    """FIRST audio, not total. Every later chunk is synthesised while this one
    is playing, so it costs the listener nothing and reporting it would bury
    the one figure that matters under an utterance-long list."""
    from routers import tts_runtime

    calls: list[tuple] = []
    monkeypatch.setattr(tts_runtime, "_log_first_audio",
                        lambda *a: calls.append(a))
    _ready_voice(client, monkeypatch)

    events = _speak_stream(
        client, "One thing happened. Then another. And then a third. A fourth.")
    chunks = [e for e in events if e["type"] == "voice_chunk"]

    assert len(chunks) > 1, "this case needs an utterance that streams"
    assert len(calls) == 1


def test_the_reported_speech_length_is_the_opening_piece(
    client, voice, monkeypatch,
):
    """The ratio is the point: five seconds in front of three seconds of audio
    is a different problem from five seconds in front of fifteen. Reporting the
    whole utterance's length here would make a fast opening look slow."""
    from routers import tts_runtime

    calls: list[tuple] = []
    monkeypatch.setattr(tts_runtime, "_log_first_audio",
                        lambda *a: calls.append(a))
    _ready_voice(client, monkeypatch)

    events = _speak_stream(
        client, "One thing happened. Then another. And then a third. A fourth.")
    chunks = [e for e in events if e["type"] == "voice_chunk"]

    assert calls[0][-1] == chunks[0]["seconds"]


def test_the_report_tells_a_slow_engine_from_a_missing_one(caplog):
    """The distinction the whole line exists for. A press that lands on a ready
    engine waits for synthesis; one that lands mid-load waits for the load and
    then for synthesis. Same silence, same elapsed figure, different fix."""
    from routers import tts_runtime

    with caplog.at_level(logging.INFO, logger="routers.tts_runtime"):
        tts_runtime._log_first_audio(0.0, 1.0, True, 100, 2.0)
        tts_runtime._log_first_audio(0.0, 1.0, False, 100, 2.0)

    ready, cold = [r.getMessage() for r in caplog.records]
    assert "not loaded" in cold
    assert "not loaded" not in ready


def test_the_two_halves_are_reported_separately(caplog, monkeypatch):
    """Setup is vault reads and model resolution; the engine is synthesis. One
    combined number cannot say which to go and fix."""
    from routers import tts_runtime

    # The helper reads the clock itself, on purpose: the moment first audio
    # becomes available is the moment it is called, and an end time passed in
    # by the caller is one more thing that can be passed in wrongly.
    monkeypatch.setattr(tts_runtime.time, "perf_counter", lambda: 10.0)
    with caplog.at_level(logging.INFO, logger="routers.tts_runtime"):
        # 10 s total, of which 2 s was setup and 8 s was the engine.
        tts_runtime._log_first_audio(0.0, 2.0, True, 100, 3.0)

    message = caplog.records[0].getMessage()
    assert "10.0" in message or "10.00" in message   # what the listener waited
    assert "2.00" in message                          # setup
    assert "8.0" in message                           # engine


def test_a_broken_diagnostic_never_costs_the_audio(monkeypatch):
    """This runs inside the streaming generator: a raise here ends the
    utterance. Somebody's reply must not stop talking because a log line
    failed."""
    from routers import tts_runtime

    def _boom(*args, **kwargs):
        raise RuntimeError("no handler")

    monkeypatch.setattr(tts_runtime.logger, "info", _boom)
    tts_runtime._log_first_audio(0.0, 1.0, True, 10, 1.0)
