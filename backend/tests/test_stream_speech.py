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


def test_a_reply_is_not_finished_while_its_last_sentence_is_still_being_made():
    """The premature-done bug, pinned at THIS layer.

    A reply that ended and a reply that lost its ending look identical on the
    wire, so `finished` going true one sentence early costs the user the end of
    what was said. `speech_queue.py` has its own test for this; nothing tested
    the speaker on top of it, and the speaker is what the SSE poller asks.

    KADEME 15a measured something worth writing down: deleting `and not
    self._synthesising` from `finished` leaves the whole suite green, and that
    is NOT a decorative test - the queue's own `_in_flight` flag closes the same
    window one layer down, and the `not self._out` clause closes what is left.
    The speaker's flag is belt and braces. Anyone tempted to tidy the
    redundancy away should know it is redundancy on purpose, and that this test
    watches the composed behaviour rather than either flag.
    """
    entered = threading.Event()
    release = threading.Event()

    def blocking(text):
        entered.set()
        assert release.wait(5.0), "the test never released the engine"
        return {"path": "/a.wav", "seconds": 1.0}

    sp = StreamSpeaker(blocking, preroll_seconds=0.0)
    try:
        sp.feed("Only one sentence here.")
        sp.finish()                    # closing is true from here on
        assert entered.wait(5.0), "the engine was never called"
        # Everything else that could hold `finished` down is already satisfied:
        # the stream is closed and nothing is waiting to be handed over. The
        # only thing left is the sentence in flight.
        assert sp.drain() == []
        assert sp.finished is False, (
            "reported finished while the last sentence was still in the engine")

        release.set()
        assert sp.wait_idle(5.0)
        assert sp.finished is False, "the made audio has not been taken yet"
        assert [c["text"] for c in sp.drain()] == ["Only one sentence here."]
        assert sp.finished is True
    finally:
        release.set()
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
        # The half the name is about, and nothing checked it: the sentence that
        # HAD been synthesised is still there to play. A build that threw the
        # good audio away on failure passed this test unchanged.
        assert [c["text"] for c in sp.drain()] == ["One."]
        # ... and it stopped rather than working through the backlog.
        assert calls == ["One.", "Two."], calls
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


class TestTheFirstChunkIsNotHeldForTheSecond:
    """MEASURED BUG: 3.96 s of finished audio waiting on work nobody needed yet.

    `pump()` fills the lookahead before it returns, and the worker only
    published after it returned - so the opening chunk sat until the SECOND one
    was synthesised. It is the one delay a listener experiences in full,
    because nothing is playing yet to cover it.

    From the real app, one press of Speak: chunk one was written at
    06:27:29.041 and the first sound did not leave until 06:27:32.999, when
    chunk two finished. Inside a 10.46 s wait, 3.96 s of it was this.

    DEFAULT_LOOKAHEAD's own comment already said a deeper buffer "only adds
    latency at the START, which is the one place the delay is actually heard".
    The depth was never the problem; paying all of it before handing over any
    of it was.
    """

    def _blocking_after_first(self):
        """A synth that makes the first chunk, then parks inside the second."""
        started_second = threading.Event()
        release_second = threading.Event()
        calls = []
        lock = threading.Lock()

        def synth(text):
            with lock:
                calls.append(text)
                n = len(calls)
            if n >= 2:
                started_second.set()
                assert release_second.wait(5.0), "second synth never released"
            return {"path": f"/a/{n}.wav", "seconds": 3.0}

        synth.calls = calls
        synth.started_second = started_second
        synth.release_second = release_second
        return synth

    def test_the_opening_chunk_arrives_while_the_next_is_still_being_made(self):
        synth = self._blocking_after_first()
        sp = StreamSpeaker(synth, preroll_seconds=0.0)
        try:
            sp.feed("First sentence here. Second sentence here. Third one.")
            sp.finish()

            assert synth.started_second.wait(5.0), "the second synth never ran"
            # The engine is now BLOCKED inside chunk two. Chunk one has been
            # finished for as long as that has been true, and the listener is
            # entitled to it.
            deadline = time.monotonic() + 5.0
            out = []
            while not out and time.monotonic() < deadline:
                out = sp.drain()
                if not out:
                    time.sleep(0.01)

            assert out, (
                "the first chunk was still being withheld while the engine was "
                "busy with the second - which is the whole bug"
            )
            assert len(out) == 1
        finally:
            synth.release_second.set()
            sp.close()

    def test_every_chunk_still_arrives_in_order(self):
        """The fix must not cost the ordering the queue exists to guarantee."""
        sp, synth = speaker(preroll_seconds=0.0)
        try:
            sp.feed("One here. Two here. Three here. Four here.")
            sp.finish()
            assert sp.wait_idle(5.0)
            got = []
            deadline = time.monotonic() + 5.0
            while len(got) < 4 and time.monotonic() < deadline:
                got.extend(sp.drain())
                if len(got) < 4:
                    time.sleep(0.01)
            assert [c["text"] for c in got] == [
                "One here.", "Two here.", "Three here.", "Four here."]
        finally:
            sp.close()

    def test_cancelling_stops_the_engine_instead_of_finishing_the_reply(self):
        """REGRESSION, and a live one: pumping a chunk at a time moved the loop
        that used to sit outside this block - where `while not self._stop`
        caught an abort between sentences - inside it, and the check did not
        come along. A cancelled reply kept synthesising every sentence it had
        left, holding the engine's turn, so the next press queued behind an
        utterance nobody was listening to. Observed live: one reply spoke and
        every press after it did nothing at all.
        """
        synth = self._blocking_after_first()
        sp = StreamSpeaker(synth, preroll_seconds=0.0)
        try:
            # Eight sentences, so "kept going to the end" is unmistakable.
            sp.feed("One here. Two here. Three here. Four here. Five here. "
                    "Six here. Seven here. Eight here.")
            sp.finish()
            assert synth.started_second.wait(5.0)

            sp.cancel()
            synth.release_second.set()      # let the in-flight sentence finish

            deadline = time.monotonic() + 5.0
            while sp._thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert not sp._thread.is_alive(), "the worker ignored the abort"

            # Two: the one that was already in flight when cancel landed, and
            # the one before it. Not the whole reply.
            assert len(synth.calls) <= 2, (
                f"synthesised {len(synth.calls)} sentences after being "
                "cancelled - the abort is not being read between chunks"
            )
        finally:
            synth.release_second.set()
            sp.close()
