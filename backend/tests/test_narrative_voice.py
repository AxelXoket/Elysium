"""V9-3 - how *narration* is voiced, end to end.

The mode is chosen on the display side (it is the same span the screen
italicises) and travels on the request. What matters here is that an unknown
value can never reach `speech_prep` - it raises there, and a raise inside an
SSE generator costs the user their reply, not just the audio.
"""
import pytest

import speech_prep
from conftest import make_character, make_chat
from tests.test_streaming import stream_provider, read_events  # noqa: F401
from tests.test_streaming_voice import fake_voice  # noqa: F401

BODY = {"message": "Say something", "model_id": "test/model-1"}
NARRATED = "*She turns away.* \"Fine.\" *He waits.*"


def stream(client, chat_id, **extra):
    with client.stream(
        "POST", f"/api/v1/chats/{chat_id}/complete/stream",
        json={**BODY, **extra},
    ) as resp:
        assert resp.status_code == 200
        return read_events(resp)


@pytest.mark.parametrize("mode", speech_prep.NARRATIVE_MODES)
def test_every_mode_speech_prep_knows_is_one_the_request_accepts(
    client, stream_provider, fake_voice, mode,
):
    """The two lists have to stay one list.

    The modes are declared twice: as a Literal on the request body
    (completions.py:182) and as speech_prep.NARRATIVE_MODES, which raises on
    anything outside it. Parametrizing over the PRODUCTION tuple rather than a
    copy of it means adding a mode in speech_prep and forgetting the Literal
    shows up here as a 422 instead of as a setting nobody can select.

    The floor matters as much as the status: without it a build that accepted
    every value and then voiced nothing would satisfy all three cases.
    """
    stream_provider.deltas = [NARRATED]
    chat_id = make_chat(client, make_character(client))
    events = stream(client, chat_id, speak=True, speak_narrative=mode)
    assert any(e["type"] == "done" for e in events)
    assert fake_voice.made, f"mode {mode} was accepted and then spoke nothing"


def test_an_unknown_mode_is_refused_before_it_can_break_a_reply(client):
    chat_id = make_chat(client, make_character(client))
    resp = client.post(
        f"/api/v1/chats/{chat_id}/complete/stream",
        json={**BODY, "speak": True, "speak_narrative": "shout"},
    )
    # 422 from validation - NOT a 200 that dies mid-stream and takes the
    # message with it.
    assert resp.status_code == 422


def test_skip_speaks_the_dialogue_and_leaves_the_narration_out(client,
                                                              stream_provider,
                                                              fake_voice):
    stream_provider.deltas = [NARRATED]
    chat_id = make_chat(client, make_character(client))
    stream(client, chat_id, speak=True, speak_narrative="skip")
    spoken = " ".join(fake_voice.made)
    assert "Fine" in spoken
    assert "turns away" not in spoken and "He waits" not in spoken


def test_same_speaks_everything_without_the_asterisks(client, stream_provider,
                                                      fake_voice):
    stream_provider.deltas = [NARRATED]
    chat_id = make_chat(client, make_character(client))
    stream(client, chat_id, speak=True, speak_narrative="same")
    spoken = " ".join(fake_voice.made)
    assert "turns away" in spoken and "Fine" in spoken
    assert "*" not in spoken


def test_narrator_marks_the_narration_for_a_tag_capable_engine(client,
                                                               stream_provider,
                                                               fake_voice):
    stream_provider.deltas = [NARRATED]
    chat_id = make_chat(client, make_character(client))
    stream(client, chat_id, speak=True, speak_narrative="narrator")
    spoken = " ".join(fake_voice.made)
    assert "[" in spoken                      # a delivery direction was added
    assert "turns away" in spoken and "Fine" in spoken


def test_the_default_is_same_so_nothing_changes_unless_asked(client,
                                                             stream_provider,
                                                             fake_voice):
    stream_provider.deltas = [NARRATED]
    chat_id = make_chat(client, make_character(client))
    stream(client, chat_id, speak=True)
    spoken = " ".join(fake_voice.made)
    assert "turns away" in spoken and "[" not in spoken


def test_speak_live_honours_the_narration_setting(client, stream_provider,
                                                  fake_voice, monkeypatch):
    """Regression from the V9 audit.

    The dormant speaker that the per-message Speak button wakes is configured
    from THIS request. The client only sent the narration mode when continuous
    mode was on, so speak-live sat permanently on the default - the setting
    silently did nothing for exactly the people who had not turned continuous
    on. The mode now travels regardless of `speak`.

    KADEME 12: this test used to assert only that nothing was synthesised, and
    that is true whatever mode was passed - it would have stayed green on the
    broken build it was written for. What has to be observed is the mode the
    dormant speaker was CONFIGURED with, since by definition it has not spoken
    yet. The three tests above prove what each mode then does with it.
    """
    import routers.completions as completions_router
    from tts import stream_hook

    asked: list[str] = []
    real_open = stream_hook.open_speaker

    def spy(enabled, **kwargs):
        asked.append(kwargs.get("narrative"))
        return real_open(enabled, **kwargs)

    monkeypatch.setattr(completions_router.stream_hook, "open_speaker", spy)

    stream_provider.deltas = [NARRATED]
    chat_id = make_chat(client, make_character(client))
    # No `speak` - this is the arming case, not the speaking one.
    events = stream(client, chat_id, speak_narrative="skip")
    assert any(e["type"] == "done" for e in events)
    assert fake_voice.made == []          # dormant: nothing synthesised yet
    assert asked == ["skip"], (
        "the speaker was armed with the default, so the setting does nothing "
        "for anyone who has not turned continuous speech on")
