"""Audit KÖK 8, voice half: three places the speaker held the event loop.

None of these are voice bugs. They are latency bugs that happen to live in the
voice code: a thread join, a lock, and a flag - each one taken or awaited on
the loop every other live stream shares. The audio was always fine; what was
not fine was that reading somebody else's reply stopped while it happened.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from tts import stream_hook
from tts.stream_speech import StreamSpeaker


def _synth(delay: float = 0.0):
    def synth(text):
        if delay:
            time.sleep(delay)
        return {"audio_id": "a", "seconds": 0.5}

    synth.engine_supports_tags = False
    return synth


# ---------------------------------------------------------------------------
# 1. close() joins a worker thread; aclose() does it off the loop
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_aclose_does_not_block_the_event_loop(anyio_backend):
    """close() joins the synthesis worker with a five-second timeout, and the
    SSE generator's `finally` called it straight from the loop - on EVERY
    reply, not just the failures. The join has to happen; it just must not
    happen here."""
    released = threading.Event()

    def slow_synth(text):
        released.wait(2.0)
        return {"audio_id": "a", "seconds": 0.5}

    slow_synth.engine_supports_tags = False
    hook = stream_hook.open_speaker(True, make_synth=lambda: slow_synth)
    hook.feed("A sentence that takes a while. ")

    ticks = 0

    async def heartbeat():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    beat = asyncio.ensure_future(heartbeat())
    try:
        await asyncio.sleep(0.05)
        before = ticks
        await stream_hook.aclose(hook)
        # The loop kept running for the whole shutdown - which is the entire
        # point. A blocking close() pins it and `ticks` does not move.
        assert ticks > before, "the loop was frozen while the speaker shut down"
    finally:
        released.set()
        beat.cancel()


@pytest.mark.anyio
async def test_aclose_actually_closes(anyio_backend):
    """Off the loop, but not skipped: a surviving speaker keeps synthesising a
    reply nobody will hear."""
    hook = stream_hook.open_speaker(True, make_synth=lambda: _synth())
    hook.feed("Hello there. ")
    await stream_hook.aclose(hook)
    assert hook._speaker is None


@pytest.mark.anyio
async def test_aclose_on_a_silent_hook_is_free(anyio_backend):
    """Voice off is the common case; it must not pay for a thread hop."""
    await stream_hook.aclose(stream_hook.open_speaker(False))


@pytest.mark.anyio
async def test_a_failing_shutdown_cannot_replace_the_streams_outcome(
    anyio_backend, monkeypatch,
):
    """This runs in the `finally` of a generator that may be re-raising a
    GeneratorExit. An exception from cleanup would escape into ASGI
    finalization as an unexplained error about a reply that was fine."""

    class Exploding:
        def close(self):
            raise RuntimeError("shutdown went wrong")

    await stream_hook.aclose(Exploding())


# ---------------------------------------------------------------------------
# 2. cancel() left `finished` false forever
# ---------------------------------------------------------------------------

def test_a_cancelled_speaker_reports_itself_finished():
    """drain_events loops until `finished` or its deadline. cancel() set
    `_stop` - which stops the worker before it can ever close the queue - so
    `finished` stayed False and an aborted reply held its SSE body open for
    the whole backstop with nothing left to send down it."""
    speaker = StreamSpeaker(_synth())
    speaker.feed("Something to say. ")
    speaker.cancel()
    assert speaker.finished is True
    speaker.close()


def test_the_drain_loop_ends_promptly_after_an_abort():
    """The user-visible half of the same fact."""
    async def drive():
        hook = stream_hook.open_speaker(True, make_synth=lambda: _synth())
        hook.feed("A sentence. ")
        hook.cancel()
        started = time.monotonic()
        async for _ in stream_hook.drain_events(hook):
            pass
        elapsed = time.monotonic() - started
        await stream_hook.aclose(hook)
        return elapsed

    elapsed = asyncio.run(drive())
    assert elapsed < stream_hook.DRAIN_TIMEOUT_S / 2, (
        "an aborted reply waited out the full drain backstop"
    )


def test_a_finished_reply_is_still_not_cut_short():
    """The guard the fix must not break: `finished` may not answer True while
    a sentence is still being synthesised, or drain_events emits voice_done
    and close() throws the last sentence away."""
    speaker = StreamSpeaker(_synth(delay=0.15))
    speaker.feed("One sentence here. ")
    speaker.finish()
    time.sleep(0.05)                      # the worker is inside the engine now
    assert speaker.finished is False
    assert speaker.wait_idle(5.0)
    # Still not finished: a chunk nobody has collected yet is audio the client
    # has not been sent.
    assert speaker.finished is False
    assert speaker.drain()
    assert speaker.finished is True
    speaker.close()


# ---------------------------------------------------------------------------
# 3. enable() held the lock across the whole engine start-up
# ---------------------------------------------------------------------------

def test_feeding_a_reply_does_not_wait_for_the_engine_to_come_up():
    """_make_synth resolves the model, walks the models folder and reads the
    vault settings - hundreds of milliseconds. Holding _lock across it meant
    every delta of a reply that pressed Speak mid-stream queued behind engine
    start-up, on the event loop."""
    building = threading.Event()
    release = threading.Event()

    def slow_make_synth():
        building.set()
        release.wait(5.0)
        return _synth()

    hook = stream_hook.SpeakHook(slow_make_synth, armed=False)
    starter = threading.Thread(target=hook.enable, daemon=True)
    starter.start()
    assert building.wait(2.0), "the engine never started building"

    fed = threading.Event()

    def feeder():
        hook.feed("A delta that must not wait. ")
        fed.set()

    threading.Thread(target=feeder, daemon=True).start()
    assert fed.wait(1.0), "feed() blocked behind the engine start-up"

    release.set()
    starter.join(5.0)
    hook.close()


def test_the_replay_still_starts_from_the_beginning():
    """What the lock was protecting, and what must survive moving it: text
    that arrived DURING the start-up is replayed, so pressing Speak three
    sentences in speaks the reply from the start rather than joining it."""
    release = threading.Event()
    spoken: list[str] = []

    def recording_synth(text):
        spoken.append(text)
        return {"audio_id": "a%d" % len(spoken), "seconds": 0.5}

    recording_synth.engine_supports_tags = False

    def gated_make_synth():
        release.wait(5.0)
        return recording_synth

    hook = stream_hook.SpeakHook(gated_make_synth, armed=False)
    starter = threading.Thread(target=hook.enable, daemon=True)
    starter.start()
    time.sleep(0.05)
    hook.feed("First sentence. ")
    hook.feed("Second sentence. ")
    release.set()
    starter.join(5.0)

    hook.finish()
    assert hook._speaker is not None
    hook._speaker.wait_idle(5.0)
    hook.close()

    said = " ".join(spoken)
    assert "First sentence." in said, (
        "the reply was joined mid-thought instead of spoken from the start"
    )
    assert "Second sentence." in said


def test_stopping_during_start_up_does_not_leave_a_speaker_running():
    """A user who presses Speak and then Stop lands exactly in the start-up
    window. The speaker close() could not see - it did not exist yet - used to
    poll at 20 Hz for the life of the process."""
    release = threading.Event()

    def gated_make_synth():
        release.wait(5.0)
        return _synth()

    hook = stream_hook.SpeakHook(gated_make_synth, armed=False)
    result: list[bool] = []
    starter = threading.Thread(target=lambda: result.append(hook.enable()),
                               daemon=True)
    starter.start()
    time.sleep(0.05)
    hook.close()                      # Stop, while the engine is still loading
    release.set()
    starter.join(5.0)

    assert result == [False]
    assert hook._speaker is None
