"""V8-5 - the hook the streaming endpoints actually hold.

Two properties matter more than anything else here, because failing either one
costs the user their MESSAGE rather than just the audio:

  1. The silent hook must be a complete stand-in. Voice is off for most replies,
     and a missing method on the null object is an AttributeError inside an SSE
     generator - which is to say, a reply that vanishes.
  2. The context manager must clean up on EVERY exit, including the abort paths
     completions.py re-raises through. A leaked worker thread keeps synthesising
     sentences for a reply nobody is listening to any more.
"""
import threading

import pytest

from tts import stream_hook


def synth_ok(seconds=1.0):
    calls = []

    def synth(text):
        calls.append(text)
        return {"path": f"C:/cache/id{len(calls)}.wav",
                "audio_id": f"id{len(calls)}", "seconds": seconds}

    synth.calls = calls
    synth.engine_supports_tags = True
    return synth


def drain_all(hook, timeout=5.0):
    hook._speaker.wait_idle(timeout)
    return hook.events()


# ── the silent hook is a complete stand-in ───────────────────────────────────

def test_disabled_yields_a_hook_that_answers_every_call():
    with stream_hook.speaking(False) as voice:
        assert not voice.active
        voice.feed("anything")
        voice.finish()
        voice.cancel()
        assert voice.events() == []
        assert voice.done_event() == []
        assert voice.finished


def test_the_silent_hook_has_every_method_the_real_one_has():
    """A method present on one and missing on the other is an AttributeError
    raised inside an SSE generator - the reply would simply stop."""
    real = {n for n in dir(stream_hook.SpeakHook) if not n.startswith("_")}
    silent = {n for n in dir(stream_hook._Silent) if not n.startswith("_")}
    assert real <= silent, f"missing on _Silent: {sorted(real - silent)}"


def test_enabled_without_an_engine_is_silent_not_broken():
    with stream_hook.speaking(True, make_synth=None) as voice:
        assert not voice.active


def test_an_engine_that_fails_to_start_costs_the_audio_not_the_reply():
    """The TEXT survives - and the failure is SAID.

    This case used to assert `events() == []`, i.e. exactly the audit finding:
    enable()'s False return was dropped, so a continuous-mode reply whose
    engine could not start arrived as user_message -> delta* -> done with no
    voice_chunk, no voice_error and no voice_done. The user saw a normal reply,
    heard nothing, and had nothing to press (SpeakLiveButton hides itself while
    continuous is on).
    """
    def explode():
        raise RuntimeError("no model configured")

    with stream_hook.speaking(True, make_synth=explode) as voice:
        voice.feed("The text still streams.")
        events = voice.events()
        assert [e["type"] for e in events] == ["voice_error"]
        assert events[0]["code"].startswith("tts_")
        # Once. A second poll during the same reply must not re-report it.
        assert voice.events() == []
        assert not voice.active


# ── the real hook ────────────────────────────────────────────────────────────

def test_chunks_become_voice_events_with_a_running_index():
    with stream_hook.speaking(True, make_synth=synth_ok) as voice:
        voice.feed("One. Two. Three.")
        voice.finish()
        events = drain_all(voice)
        assert [e["type"] for e in events] == ["voice_chunk"] * 3
        assert [e["index"] for e in events] == [0, 1, 2]
        assert [e["audio_id"] for e in events] == ["id1", "id2", "id3"]
        assert all(e["seconds"] == 1.0 for e in events)


def test_the_done_event_reports_how_many_chunks_were_sent():
    with stream_hook.speaking(True, make_synth=synth_ok) as voice:
        voice.feed("One. Two.")
        voice.finish()
        drain_all(voice)
        assert voice.done_event() == [{"type": "voice_done", "count": 2}]


def test_the_done_event_is_sent_only_once():
    with stream_hook.speaking(True, make_synth=synth_ok) as voice:
        voice.feed("One.")
        voice.finish()
        drain_all(voice)
        assert voice.done_event()
        assert voice.done_event() == []


def test_a_failure_becomes_one_voice_error_event():
    def boom():
        def synth(text):
            raise RuntimeError("worker died")
        synth.engine_supports_tags = False
        return synth

    with stream_hook.speaking(True, make_synth=boom) as voice:
        voice.feed("One. Two.")
        voice.finish()
        events = drain_all(voice)
        assert events == [{"type": "voice_error", "code": "tts_synthesis_failed"}]
        assert voice.events() == []          # never repeated


def test_a_worker_code_is_passed_through_so_the_error_map_can_speak():
    class Coded(RuntimeError):
        code = "tts_out_of_memory"

    def boom():
        def synth(text):
            raise Coded("no vram")
        synth.engine_supports_tags = False
        return synth

    with stream_hook.speaking(True, make_synth=boom) as voice:
        voice.feed("One.")
        voice.finish()
        events = drain_all(voice)
        assert events == [{"type": "voice_error", "code": "tts_out_of_memory"}]


def test_events_never_wait():
    """Called between SSE deltas, so it has to answer instantly whether or not
    any audio is ready."""
    gate = threading.Event()

    def slow():
        def synth(text):
            gate.wait(5.0)
            return {"audio_id": "x", "seconds": 1.0}
        synth.engine_supports_tags = False
        return synth

    with stream_hook.speaking(True, make_synth=slow) as voice:
        try:
            voice.feed("One. Two.")
            assert voice.events() == []
        finally:
            gate.set()


def test_the_audio_id_falls_back_to_the_file_stem():
    def synth_path_only():
        def synth(text):
            return {"path": r"C:\cache\deadbeef.wav", "seconds": 1.0}
        synth.engine_supports_tags = False
        return synth

    with stream_hook.speaking(True, make_synth=synth_path_only) as voice:
        voice.feed("One.")
        voice.finish()
        events = drain_all(voice)
        assert events[0]["audio_id"] == "deadbeef"


# ── cleanup on every exit path ───────────────────────────────────────────────

def test_the_worker_thread_is_gone_after_a_normal_exit():
    with stream_hook.speaking(True, make_synth=synth_ok) as voice:
        voice.feed("One.")
        speaker = voice._speaker
    assert not speaker._thread.is_alive()


def test_the_worker_thread_is_gone_after_an_exception():
    # completions.py re-raises GeneratorExit through this block; a speaker that
    # survived would keep synthesising a reply nobody is listening to.
    speaker = None
    with pytest.raises(GeneratorExit):
        with stream_hook.speaking(True, make_synth=synth_ok) as voice:
            speaker = voice._speaker
            voice.feed("One. Two. Three.")
            raise GeneratorExit
    assert speaker is not None and not speaker._thread.is_alive()


def test_an_abort_stops_synthesis_rather_than_finishing_the_backlog():
    synth = synth_ok()
    with pytest.raises(RuntimeError):
        with stream_hook.speaking(True, make_synth=lambda: synth) as voice:
            voice.feed("One. Two. Three. Four. Five. Six. Seven.")
            raise RuntimeError("client vanished")
    # It stopped early rather than working through every sentence first.
    assert len(synth.calls) < 7


# ── Audit HIGH: enable()/close() race leaked a speaker thread forever ───────


def test_enable_racing_close_does_not_install_a_speaker():
    """Press Speak, then Stop while the model is still resolving.

    enable() holds the lock across the whole (slow) _make_synth, so close()
    used to see `_speaker is None`, do nothing, and let enable() install a
    brand-new StreamSpeaker afterwards. finish() is never called on the abort
    path, so that speaker's queue never reaches `finished` and its daemon
    thread polls at 20 Hz for the life of the process - synthesising a reply
    nobody will hear.
    """
    entered = threading.Event()
    release = threading.Event()

    def slow_make_synth():
        entered.set()
        release.wait(5.0)
        return synth_ok()

    hook = stream_hook.SpeakHook(slow_make_synth, armed=False)
    hook.feed("A sentence that would be spoken. ")

    result = {}
    worker = threading.Thread(target=lambda: result.update(ok=hook.enable()))
    worker.start()
    assert entered.wait(5.0), "make_synth never ran"

    # The abort lands while the engine is still coming up.
    closer = threading.Thread(target=hook.close)
    closer.start()
    release.set()
    worker.join(5.0)
    closer.join(5.0)

    assert result.get("ok") is False, "enable() must refuse after close()"
    assert hook.active is False
    # Scoped to THIS hook. Counting every "tts-stream-speaker" in the process
    # made the assertion depend on what other tests happened to leave running,
    # so the claim it appeared to make ("no speaker outlived this reply") was
    # really "no speaker exists anywhere right now" - a different, and much
    # more fragile, statement.
    assert hook._speaker is None, "a speaker was installed after close()"


def test_enable_after_close_is_refused_outright():
    hook = stream_hook.SpeakHook(synth_ok, armed=False)
    hook.close()
    assert hook.enable() is False
    assert hook.active is False


def test_close_is_idempotent():
    hook = stream_hook.SpeakHook(synth_ok, armed=True)
    assert hook.active is True
    hook.close()
    hook.close()
    assert hook.active is False


# ── Audit LOW: a drain timeout is not a reply that finished speaking ────────


def test_drain_timeout_reports_an_error_before_done():
    import asyncio

    class WedgedHook:
        active = True
        finished = False

        def events(self):
            return []

        def done_event(self):
            return [{"type": "voice_done", "count": 3}]

    async def collect():
        return [
            event
            async for event in stream_hook.drain_events(WedgedHook(), timeout=0.0)
        ]

    events = asyncio.run(collect())
    assert events[0]["type"] == "voice_error"
    assert events[0]["code"] == "tts_synthesis_failed"
    # voice_done still closes the sequence - the client's player must finish.
    assert events[-1]["type"] == "voice_done"


def test_a_clean_drain_reports_no_error():
    import asyncio

    class DoneHook:
        active = True
        finished = True

        def events(self):
            return []

        def done_event(self):
            return [{"type": "voice_done", "count": 2}]

    async def collect():
        return [
            event
            async for event in stream_hook.drain_events(DoneHook(), timeout=5.0)
        ]

    events = asyncio.run(collect())
    assert [e["type"] for e in events] == ["voice_done"]
