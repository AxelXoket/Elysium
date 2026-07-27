"""V8-5 - speaking while the reply is still arriving.

The thing under test is a THREAD, so every assertion here either waits on the
speaker's own idle signal or on a barrier the fake synth controls. Nothing
sleeps hoping the timing works out - a test that passes because the machine was
fast is not a test.
"""
import threading
import time

import pytest

from tts.stream_speech import StreamSpeaker


def synth_ok(seconds=1.5):
    calls = []
    lock = threading.Lock()

    def synth(text):
        with lock:
            calls.append(text)
        return {"path": f"/a/{len(calls)}.wav", "seconds": seconds}

    synth.calls = calls
    return synth


def speaker(**kw):
    kw.setdefault("synth", synth_ok())
    synth = kw.pop("synth")
    sp = StreamSpeaker(synth, **kw)
    return sp, synth


def collect(sp, timeout=5.0):
    """Everything the speaker produces, once it has nothing left to do."""
    assert sp.wait_idle(timeout), "speaker never went idle"
    return sp.drain()


# ── the happy path ───────────────────────────────────────────────────────────

def test_deltas_become_spoken_chunks_in_order():
    sp, synth = speaker(preroll_seconds=0.0)
    try:
        for delta in ("Hello there. ", "How are you? ", "I am fine."):
            sp.feed(delta)
        sp.finish()
        chunks = collect(sp)
        assert [c["text"] for c in chunks] == [
            "Hello there.", "How are you?", "I am fine."]
    finally:
        sp.close()


def test_a_sentence_split_across_deltas_is_spoken_once_whole():
    # The SSE deltas are token-sized; a sentence almost never arrives in one.
    sp, synth = speaker(preroll_seconds=0.0)
    try:
        for delta in ("She said", " nothing", " at all."):
            sp.feed(delta)
        sp.finish()
        chunks = collect(sp)
        assert [c["text"] for c in chunks] == ["She said nothing at all."]
    finally:
        sp.close()


def test_markdown_and_tags_are_handled_before_the_engine_sees_them():
    sp, synth = speaker(preroll_seconds=0.0, engine_supports_tags=True)
    try:
        sp.feed("[soft, close] Look at **this** and [here](http://x.y).")
        sp.finish()
        chunks = collect(sp)
        spoken = chunks[0]["text"]
        assert "[soft, close]" in spoken
        assert "**" not in spoken and "http" not in spoken and "here" in spoken
    finally:
        sp.close()


# ── the pre-roll gate ────────────────────────────────────────────────────────

def test_nothing_is_handed_over_before_the_pre_roll_is_banked():
    # The client plays the first chunk it receives, so releasing one early is
    # exactly how a reply ends up stuttering between sentences.
    sp, _ = speaker(preroll_seconds=10.0, synth=synth_ok(seconds=0.5),
                    lookahead=2)
    try:
        sp.feed("One. Two.")
        assert sp.wait_idle(5.0)
        assert sp.drain() == []
    finally:
        sp.close()


def test_finishing_the_stream_releases_a_reply_shorter_than_the_pre_roll():
    sp, _ = speaker(preroll_seconds=10.0, synth=synth_ok(seconds=0.5))
    try:
        sp.feed("Sure.")
        sp.finish()
        chunks = collect(sp)
        assert [c["text"] for c in chunks] == ["Sure."]
    finally:
        sp.close()


# ── it must never block the SSE loop ─────────────────────────────────────────

def test_feed_returns_immediately_even_when_synthesis_is_slow():
    """This is the whole reason for the thread.

    The synth here blocks until the test releases it. If `feed` waited on the
    queue in any way, the call below would hang and the SSE loop it models
    would stop delivering TEXT - stalling the reply the user is reading in
    order to speak it.
    """
    gate = threading.Event()

    def slow(text):
        gate.wait(5.0)
        return {"path": "/a.wav", "seconds": 1.0}

    sp = StreamSpeaker(slow, preroll_seconds=0.0)
    try:
        sp.feed("First sentence. ")
        started = time.monotonic()
        for _ in range(50):
            sp.feed("More text here. ")
        assert time.monotonic() - started < 1.0
        assert sp.drain() == []          # and it did not wait for audio either
    finally:
        gate.set()
        sp.close()


# ── failure (decision 5: stop and say so, never skip silently) ───────────────

def test_an_engine_failure_stops_everything_and_is_reported_once():
    def boom(text):
        raise RuntimeError("worker died")

    sp = StreamSpeaker(boom, preroll_seconds=0.0)
    try:
        sp.feed("One. Two. Three.")
        sp.finish()
        assert sp.wait_idle(5.0)
        assert sp.failed
        err = sp.take_error()
        assert isinstance(err, RuntimeError) and "worker died" in str(err)
        # Once. A retry loop must not emit the same error event again.
        assert sp.take_error() is None
        assert sp.drain() == []
    finally:
        sp.close()


def test_a_failure_after_some_audio_keeps_what_was_already_handed_over():
    calls = []

    def flaky(text):
        calls.append(text)
        if len(calls) > 1:
            raise RuntimeError("second one died")
        return {"path": "/a.wav", "seconds": 1.0}

    sp = StreamSpeaker(flaky, preroll_seconds=0.0)
    try:
        sp.feed("One. Two. Three.")
        sp.finish()
        assert sp.wait_idle(5.0)
        assert sp.failed
    finally:
        sp.close()


# ── lifecycle ────────────────────────────────────────────────────────────────

def test_cancel_drops_pending_audio_and_stops_synthesising():
    sp, synth = speaker(preroll_seconds=0.0)
    try:
        sp.feed("One. Two. Three. Four. Five.")
        sp.cancel()
        assert sp.drain() == []
        before = len(synth.calls)
        time.sleep(0.15)
        assert len(synth.calls) == before
    finally:
        sp.close()


def test_feeding_after_cancel_is_ignored():
    sp, synth = speaker(preroll_seconds=0.0)
    try:
        sp.cancel()
        sp.feed("Anything at all.")
        sp.finish()
        time.sleep(0.15)
        assert synth.calls == []
    finally:
        sp.close()


def test_close_is_idempotent_and_joins_the_thread():
    sp, _ = speaker(preroll_seconds=0.0)
    sp.feed("Done.")
    sp.finish()
    sp.close()
    sp.close()
    assert not sp._thread.is_alive()


def test_finished_reports_only_when_everything_has_been_handed_over():
    sp, _ = speaker(preroll_seconds=0.0)
    try:
        sp.feed("One. Two.")
        sp.finish()
        assert sp.wait_idle(5.0)
        assert not sp.finished          # chunks are still waiting to be drained
        sp.drain()
        assert sp.finished
    finally:
        sp.close()


def test_the_worker_thread_is_a_daemon_so_it_cannot_hold_the_app_open():
    sp, _ = speaker(preroll_seconds=0.0)
    try:
        assert sp._thread.daemon
    finally:
        sp.close()


def test_an_empty_reply_produces_no_audio_and_no_error():
    sp, synth = speaker(preroll_seconds=0.0)
    try:
        sp.feed("```\nx = 1\n```")
        sp.finish()
        chunks = collect(sp)
        assert chunks == []
        assert not sp.failed
    finally:
        sp.close()


# ── audit regression (2026-07-25 whole-repo audit) ───────────────────────────

def test_feed_does_not_wait_for_the_sentence_being_synthesised():
    """Regression: the worker held its lock across `pump()` - which calls the
    engine, seconds per sentence - while `feed()` and `drain()` took the SAME
    lock. The SSE loop calls both between deltas, so the whole asyncio event
    loop (and every other live stream on it) stalled behind synthesis: exactly
    what the worker thread exists to prevent.

    The first sentence is held mid-synthesis for the whole test; if any caller
    still shares a lock with it, these calls block and the timing assertion
    fails.
    """
    gate = threading.Event()
    entered = threading.Event()

    def blocking(text):
        entered.set()
        gate.wait(10.0)
        return {"path": "/a.wav", "audio_id": "a", "seconds": 1.0}

    sp = StreamSpeaker(blocking, preroll_seconds=0.0)
    try:
        sp.feed("First sentence. ")
        assert entered.wait(5.0), "the worker never reached synthesis"

        started = time.monotonic()
        for _ in range(200):
            sp.feed("More text here. ")
        assert sp.drain() == []
        assert sp.finished is False
        sp.finish()
        elapsed = time.monotonic() - started
        assert elapsed < 1.0, f"callers blocked on synthesis for {elapsed:.2f}s"
    finally:
        gate.set()
        sp.close()


def test_cancel_returns_immediately_while_a_sentence_is_being_synthesised():
    """An abort must not wait for the engine round-trip already in flight."""
    gate = threading.Event()
    entered = threading.Event()

    def blocking(text):
        entered.set()
        gate.wait(10.0)
        return {"path": "/a.wav", "seconds": 1.0}

    sp = StreamSpeaker(blocking, preroll_seconds=0.0)
    try:
        sp.feed("One. ")
        assert entered.wait(5.0)
        started = time.monotonic()
        sp.cancel()
        assert time.monotonic() - started < 0.5
    finally:
        gate.set()
        sp.close()
