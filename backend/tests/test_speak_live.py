"""V9-2 - pressing Speak while the reply is still arriving.

The constraint this works around is not incidental: during a stream there is no
`message_id` (the assistant row is written after the last delta, deliberately),
and the client cannot supply the text either because it only ever holds the
stripped view. So a dormant hook buffers the RAW text and this is what wakes it.

What matters most below is that pressing Speak three sentences in speaks the
reply FROM THE START. Joining a reply mid-thought would be a worse experience
than not offering the button at all.
"""
import threading

import pytest

from tts import stream_hook
# The streaming harness lives with the streaming tests; the arming check below
# is about what the ENDPOINTS do, so it reuses theirs rather than rebuilding it.
from test_streaming import BODY, _seed_exchange, read_events, stream_provider  # noqa: F401


def synth_ok():
    calls = []
    lock = threading.Lock()

    def synth(text):
        with lock:
            calls.append(text)
        return {"audio_id": f"a{len(calls)}", "seconds": 0.5}

    synth.calls = calls
    synth.engine_supports_tags = False
    return synth


@pytest.fixture(autouse=True)
def clean_registry():
    yield
    with stream_hook._LIVE_LOCK:
        stream_hook._LIVE.clear()


def settle(hook, timeout=5.0):
    hook._speaker.wait_idle(timeout)
    return hook.events()


# ── dormant costs nothing until it is woken ──────────────────────────────────

def test_an_armable_hook_synthesises_nothing_until_enabled():
    synth = synth_ok()
    hook = stream_hook.open_speaker(False, armable=True, make_synth=lambda: synth)
    try:
        hook.feed("One. Two. Three.")
        assert not hook.active
        assert synth.calls == []
        assert hook.events() == []
    finally:
        hook.close()


def test_neither_enabled_nor_armable_allocates_nothing_at_all():
    hook = stream_hook.open_speaker(False, armable=False,
                                    make_synth=lambda: synth_ok())
    assert not hook.active
    assert hook.enable() is False       # honest: there is no voice here


# ── waking it speaks the reply from the beginning ────────────────────────────

def test_enabling_mid_reply_speaks_everything_that_already_arrived():
    synth = synth_ok()
    hook = stream_hook.open_speaker(False, armable=True, make_synth=lambda: synth)
    try:
        hook.feed("One. Two. ")
        assert hook.enable()
        hook.feed("Three.")
        hook.finish()
        settle(hook)
        # From the START - not "Three." alone.
        assert synth.calls == ["One.", "Two.", "Three."]
    finally:
        hook.close()


def test_enabling_after_the_stream_finished_still_speaks_the_whole_reply():
    synth = synth_ok()
    hook = stream_hook.open_speaker(False, armable=True, make_synth=lambda: synth)
    try:
        hook.feed("One. Two.")
        hook.finish()
        assert hook.enable()
        settle(hook)
        assert synth.calls == ["One.", "Two."]
    finally:
        hook.close()


def test_enabling_twice_is_not_an_error():
    synth = synth_ok()
    hook = stream_hook.open_speaker(False, armable=True, make_synth=lambda: synth)
    try:
        hook.feed("One.")
        assert hook.enable()
        assert hook.enable()            # somebody pressed the button twice
        hook.finish()
        settle(hook)
        assert synth.calls == ["One."]  # not spoken twice
    finally:
        hook.close()


def test_a_broken_engine_reports_failure_instead_of_pretending():
    def boom():
        raise RuntimeError("no model configured")

    hook = stream_hook.open_speaker(False, armable=True, make_synth=boom)
    try:
        hook.feed("One.")
        assert hook.enable() is False
        # "Reports failure instead of pretending" is the name of this test, and
        # it now holds: pressing Speak on a reply whose engine cannot start
        # produces an error the client can show, not silence.
        events = hook.events()
        assert [e["type"] for e in events] == ["voice_error"]
        assert not hook.active
    finally:
        hook.close()


# ── the registry ─────────────────────────────────────────────────────────────

def test_enable_live_reaches_the_stream_running_in_that_chat():
    synth = synth_ok()
    hook = stream_hook.open_speaker(False, armable=True, make_synth=lambda: synth)
    try:
        stream_hook.register_live(7, hook)
        hook.feed("One. Two.")
        assert stream_hook.enable_live(7)
        hook.finish()
        settle(hook)
        assert synth.calls == ["One.", "Two."]
    finally:
        hook.close()


def test_enable_live_on_a_chat_with_nothing_streaming_is_false():
    assert stream_hook.enable_live(999) is False


def test_unregistering_stops_speak_from_reaching_a_finished_reply():
    synth = synth_ok()
    hook = stream_hook.open_speaker(False, armable=True, make_synth=lambda: synth)
    try:
        stream_hook.register_live(7, hook)
        stream_hook.unregister_live(7, hook)
        # A stale entry would point Speak at a reply that ended minutes ago.
        assert stream_hook.enable_live(7) is False
    finally:
        hook.close()


def test_a_newer_stream_replaces_the_registry_entry_for_that_chat():
    first = stream_hook.open_speaker(False, armable=True,
                                     make_synth=lambda: synth_ok())
    second_synth = synth_ok()
    second = stream_hook.open_speaker(False, armable=True,
                                      make_synth=lambda: second_synth)
    try:
        stream_hook.register_live(7, first)
        stream_hook.register_live(7, second)
        second.feed("Newer.")
        assert stream_hook.enable_live(7)
        second.finish()
        settle(second)
        assert second_synth.calls == ["Newer."]
        assert not first.active
    finally:
        first.close()
        second.close()


def test_unregistering_a_hook_that_was_already_replaced_leaves_the_new_one():
    first = stream_hook.open_speaker(False, armable=True,
                                     make_synth=lambda: synth_ok())
    second = stream_hook.open_speaker(False, armable=True,
                                      make_synth=lambda: synth_ok())
    try:
        stream_hook.register_live(7, first)
        stream_hook.register_live(7, second)
        first.close()
        stream_hook.unregister_live(7, first)   # the loser cleaning up late
        assert stream_hook.enable_live(7)       # the winner is still reachable
    finally:
        second.close()


def test_the_silent_hook_is_never_put_in_the_registry():
    silent = stream_hook.open_speaker(False, armable=False, make_synth=None)
    stream_hook.register_live(7, silent)
    assert stream_hook.enable_live(7) is False


# ── Audit: the Speak button and the stream disagreed about when it works ────
#
# SpeakLiveButton renders on model readiness alone, but the stream armed its
# dormant hook only when the sticky `tts_voice_ever_enabled` flag was set. A
# user who installed an engine, picked a model and a reference voice, and never
# touched the "Voice replies" toggle (its own description: "Off - chat stays
# text-only") pressed the Speak icon mid-reply and got 404 tts_nothing_streaming
# for a reply that was still arriving.


def test_a_selected_voice_model_is_enough_to_arm_the_stream(client):
    import database
    import routers.tts_runtime as runtime

    database.set_setting(runtime.SETTING_ACTIVE_UID, "u1")
    assert runtime.a_voice_model_is_selected() is True

    database.set_setting(runtime.SETTING_ACTIVE_UID, "")
    assert runtime.a_voice_model_is_selected() is False


def test_a_locked_vault_reads_as_no_model_rather_than_raising():
    """It runs inside an SSE generator; an exception there costs the reply."""
    import routers.tts_runtime as runtime
    assert runtime.a_voice_model_is_selected() is False


def test_the_check_is_a_settings_read_not_a_probe(monkeypatch):
    """It runs on the hot path of every stream: no scan, no readiness
    evaluation, no VRAM probe."""
    import routers.tts_runtime as runtime
    from tts import vram

    def explode(*a, **k):  # pragma: no cover - must never be reached
        raise AssertionError("a GPU probe on the streaming hot path")

    monkeypatch.setattr(vram, "query_gpu", explode)
    monkeypatch.setattr(runtime, "scan_roots", explode)
    runtime.a_voice_model_is_selected()


def test_the_streams_arm_on_either_predicate(client, stream_provider, monkeypatch):
    """All three streaming endpoints, not just send.

    Was a grep for "a_voice_model_is_selected() == 3", which stopped meaning
    anything the moment the three copies of the streaming body became one - it
    would have passed on a build where two endpoints armed nothing. This runs
    each endpoint instead and looks at what it actually asked for.
    """
    import voice_tags
    import routers.completions as completions_router
    from tts import stream_hook

    # The interesting half of "either": voice was never enabled, so the sticky
    # flag is off and ONLY the selected-model predicate can arm the hook.
    monkeypatch.setattr(voice_tags, "stripping_active", lambda: False)
    monkeypatch.setattr(
        completions_router.tts_runtime, "a_voice_model_is_selected", lambda: True,
    )

    armable: list[bool] = []
    real_open = stream_hook.open_speaker

    def spy(enabled, **kwargs):
        armable.append(bool(kwargs.get("armable")))
        return real_open(enabled, **kwargs)

    monkeypatch.setattr(completions_router.stream_hook, "open_speaker", spy)

    chat_id, message_id = _seed_exchange(client, stream_provider)
    for path, payload in (
        (f"/api/v1/chats/{chat_id}/complete/stream", BODY),
        (f"/api/v1/chats/{chat_id}/messages/{message_id}/regenerate/stream",
         {"model_id": "test/model-1"}),
        (f"/api/v1/chats/{chat_id}/messages/{message_id - 1}/edit/stream",
         {"model_id": "test/model-1", "message": "rewritten"}),
    ):
        with client.stream("POST", path, json=payload) as resp:
            read_events(resp)

    assert armable == [True, True, True], (
        "every streaming endpoint must arm the hook, or SpeakLiveButton "
        "answers tts_nothing_streaming on a reply that is still arriving"
    )
