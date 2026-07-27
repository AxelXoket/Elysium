"""V8-5 - voice riding along on the real SSE endpoints.

The unit tests around `SpeechQueue`, `StreamSpeaker` and `SpeakHook` already
pin the behaviour in isolation. What is asserted HERE is the wiring: that the
endpoints actually feed the raw text in, that the voice events reach the wire in
the right order, and - most importantly - that none of it can cost the user
their message. Voice is an addition to a reply; a reply is not an addition to
voice.
"""
import json

import pytest

from conftest import make_character, make_chat, get_messages
from tests.test_streaming import stream_provider, read_events  # noqa: F401

BODY = {"message": "Stream me a story", "model_id": "test/model-1"}


@pytest.fixture()
def fake_voice(monkeypatch):
    """Stand in for the whole engine: one wav per sentence, instantly."""
    import routers.completions as completions_router

    made: list[str] = []

    class Runtime:
        broken = False
        fails_on = None
        # The stream asks this to decide whether to arm a dormant speaker for
        # the mid-reply Speak button (see completions.py). Off by default here:
        # these cases drive the `speak` flag explicitly.
        selected = False

        @staticmethod
        def a_voice_model_is_selected():
            return Runtime.selected

        @staticmethod
        def stored_pronunciations():
            # The real one reads the vault; these cases have no reading rules.
            # Present at all because the stream now asks for it, and a double
            # that is missing a method the caller uses is not a double.
            return {}

        @staticmethod
        def make_stream_synth(rate=None):
            if Runtime.broken:
                raise RuntimeError("no voice model configured")

            def synth(text):
                made.append(text)
                if Runtime.fails_on and Runtime.fails_on in text:
                    raise RuntimeError("worker died")
                return {"audio_id": f"aud{len(made)}", "seconds": 0.5}

            synth.engine_supports_tags = True
            synth.rate = rate
            return synth

    monkeypatch.setattr(completions_router, "tts_runtime", Runtime)
    Runtime.made = made
    return Runtime


def stream(client, chat_id, **extra):
    body = {**BODY, **extra}
    with client.stream(
        "POST", f"/api/v1/chats/{chat_id}/complete/stream", json=body,
    ) as resp:
        assert resp.status_code == 200
        return read_events(resp)


# ── off by default ───────────────────────────────────────────────────────────

def test_no_voice_events_when_speaking_was_not_asked_for(client, stream_provider,
                                                         fake_voice):
    chat_id = make_chat(client, make_character(client))
    events = stream(client, chat_id)
    assert [e["type"] for e in events] == [
        "user_message", "delta", "delta", "delta", "done"]
    assert fake_voice.made == []          # the engine was never even built


def test_the_stream_is_byte_identical_to_before_when_voice_is_off(client,
                                                                  stream_provider,
                                                                  fake_voice):
    chat_id = make_chat(client, make_character(client))
    events = stream(client, chat_id)
    assert events[-1]["assistant_message"]["content"] == "Once upon a time."


# ── on ───────────────────────────────────────────────────────────────────────

def test_voice_events_arrive_and_the_text_is_unchanged(client, stream_provider,
                                                       fake_voice):
    chat_id = make_chat(client, make_character(client))
    events = stream(client, chat_id, speak=True)
    types = [e["type"] for e in events]

    assert types[:4] == ["user_message", "delta", "delta", "delta"]
    assert "done" in types and "voice_done" in types
    # Reading never waits on speaking: the reply is complete before the audio.
    assert types.index("done") < types.index("voice_done")

    chunks = [e for e in events if e["type"] == "voice_chunk"]
    assert [c["index"] for c in chunks] == list(range(len(chunks)))
    assert all(c["audio_id"] for c in chunks)

    assert "".join(e["content"] for e in events if e["type"] == "delta") == \
        "Once upon a time."
    assert get_messages(client, chat_id)[-1]["content"] == "Once upon a time."


def test_the_engine_is_given_the_raw_text_not_the_display_view(client,
                                                               stream_provider,
                                                               fake_voice,
                                                               monkeypatch):
    """The delivery tags are what make the voice worth hearing, and only the
    raw text has them - the client is never sent that view."""
    import voice_tags

    monkeypatch.setattr(voice_tags, "stripping_active", lambda: True)
    stream_provider.deltas = ["[soft] ", "Come ", "here."]
    chat_id = make_chat(client, make_character(client))
    events = stream(client, chat_id, speak=True)

    shown = "".join(e["content"] for e in events if e["type"] == "delta")
    assert "[soft]" not in shown                     # hidden from the screen
    assert "[soft]" in " ".join(fake_voice.made)     # kept for the engine


def test_a_clause_is_never_spoken_twice(client, stream_provider, fake_voice,
                                        monkeypatch):
    """Regression: the stripper's flushed tail is text it WITHHELD from the
    display, not new text - the speaker already had it as a raw delta. Feeding
    it again would say that clause a second time."""
    import voice_tags

    monkeypatch.setattr(voice_tags, "stripping_active", lambda: True)
    stream_provider.deltas = ["All done", " [not-a-tag-because-it-is-long-"]
    chat_id = make_chat(client, make_character(client))
    stream(client, chat_id, speak=True)
    spoken = " ".join(fake_voice.made)
    assert spoken.count("All done") == 1


def test_speak_rate_reaches_the_engine_setup(client, stream_provider, fake_voice):
    captured = {}
    original = fake_voice.make_stream_synth

    def spy(rate=None):
        captured["rate"] = rate
        return original(rate=rate)

    fake_voice.make_stream_synth = spy
    chat_id = make_chat(client, make_character(client))
    stream(client, chat_id, speak=True, speak_rate=1.2)
    assert captured["rate"] == 1.2


# ── voice must never cost the reply ──────────────────────────────────────────

def test_a_broken_engine_costs_the_audio_and_nothing_else(client, stream_provider,
                                                          fake_voice):
    fake_voice.broken = True
    chat_id = make_chat(client, make_character(client))
    events = stream(client, chat_id, speak=True)
    types = [e["type"] for e in events]

    assert "done" in types
    assert "voice_chunk" not in types
    assert get_messages(client, chat_id)[-1]["content"] == "Once upon a time."


def test_a_mid_utterance_failure_is_reported_and_stops_the_audio(client,
                                                                 stream_provider,
                                                                 fake_voice):
    stream_provider.deltas = ["One. ", "Two. ", "Three."]
    fake_voice.fails_on = "Two"
    chat_id = make_chat(client, make_character(client))
    events = stream(client, chat_id, speak=True)

    errors = [e for e in events if e["type"] == "voice_error"]
    assert len(errors) == 1                      # once, never repeated
    assert errors[0]["code"] == "tts_synthesis_failed"
    # The reply itself is untouched.
    assert get_messages(client, chat_id)[-1]["content"] == "One. Two. Three."


def test_a_provider_failure_still_tears_the_speaker_down(client, stream_provider,
                                                         fake_voice):
    stream_provider.error_after = 1
    chat_id = make_chat(client, make_character(client))
    events = stream(client, chat_id, speak=True)
    assert any(e["type"] == "error" for e in events)
    # No thread left behind: the finally in the endpoint owns this.
    import threading
    assert not [t for t in threading.enumerate()
                if t.name == "tts-stream-speaker" and t.is_alive()]
