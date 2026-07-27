"""Audit KÖK 7: the pronunciation dictionary that had a pipe and no tap.

`pronunciations` was threaded through speech_prep, SpeechQueue, StreamSpeaker,
SpeakHook and open_speaker, with a unit test at every layer - and no production
caller ever passed one. So a character called "Aoife" was read wrong in every
single reply, and the Settings section that would fix it did not exist.

Nothing here tests the substitution itself (speech_prep already does). These
test the thing that was actually missing: that a rule the user saves reaches
BOTH the live reply and the Speak button, and means the same on each.
"""

from __future__ import annotations

import pytest

import voice_tags
# A live voice environment (a registered runtime and a selected model) is what
# _prepare_speech_text needs to resolve one; the notice-channel tests already
# stand one up.
from test_notice_channel import voice, _ready_voice  # noqa: F401


# ---------------------------------------------------------------------------
# storing them
# ---------------------------------------------------------------------------

def test_a_rule_survives_a_round_trip(client):
    assert client.get("/api/v1/tts/pronunciations").json()["pronunciations"] == {}

    saved = client.post("/api/v1/tts/pronunciations",
                        json={"pronunciations": {"Aoife": "EE-fa"}})
    assert saved.status_code == 200
    assert saved.json()["pronunciations"] == {"Aoife": "EE-fa"}
    assert client.get("/api/v1/tts/pronunciations").json()["pronunciations"] == {
        "Aoife": "EE-fa",
    }


def test_the_whole_table_is_sent_so_a_rule_can_be_removed(client):
    """A merge-only endpoint cannot express a deletion, and people remove
    reading rules at least as often as they add them."""
    client.post("/api/v1/tts/pronunciations",
                json={"pronunciations": {"Aoife": "EE-fa", "Siobhan": "shiv-AWN"}})
    body = client.post("/api/v1/tts/pronunciations",
                       json={"pronunciations": {"Aoife": "EE-fa"}}).json()
    assert body["pronunciations"] == {"Aoife": "EE-fa"}


def test_a_bracket_cannot_be_smuggled_into_every_sentence(client):
    """The replacement is substituted into text that is about to be handed to a
    tag-reading engine. A closing bracket would end a delivery span early and
    the rest of the sentence would be READ ALOUD as a direction - the exact
    failure sanitize_for_tts exists to prevent, arriving through the back door.
    """
    body = client.post("/api/v1/tts/pronunciations", json={
        "pronunciations": {"Aoife": "EE-fa] and [shouting"},
    }).json()
    assert "[" not in body["pronunciations"]["Aoife"]
    assert "]" not in body["pronunciations"]["Aoife"]


def test_an_empty_written_form_is_dropped_not_stored(client):
    """It would match everywhere."""
    body = client.post("/api/v1/tts/pronunciations",
                       json={"pronunciations": {"": "anything", " ": "x"}}).json()
    assert body["pronunciations"] == {}


def test_an_empty_replacement_is_a_legitimate_rule(client):
    """"Say nothing for this" is a real answer - a decorative symbol in a
    character name is the obvious case."""
    body = client.post("/api/v1/tts/pronunciations",
                       json={"pronunciations": {"~": ""}}).json()
    assert body["pronunciations"] == {"~": ""}


def test_the_table_is_bounded(client):
    """Every entry is a regex substitution over every sentence of every reply."""
    too_many = {f"name{i}": "x" for i in range(voice_tags.MAX_PRONUNCIATIONS + 25)}
    body = client.post("/api/v1/tts/pronunciations",
                       json={"pronunciations": too_many}).json()
    assert len(body["pronunciations"]) == voice_tags.MAX_PRONUNCIATIONS


def test_a_corrupt_row_is_ignored_rather_than_breaking_every_reply(client):
    """Hand-edited or left by an older build: reading rules are an improvement
    to speech, never a precondition for it."""
    import routers.tts_runtime as runtime
    from database import get_db

    with get_db() as con:
        con.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (voice_tags.SETTING_PRONUNCIATIONS, "not json at all"),
        )
    assert runtime.stored_pronunciations() == {}


# ---------------------------------------------------------------------------
# reaching the speech
# ---------------------------------------------------------------------------

def test_the_replay_path_applies_them(client, voice, monkeypatch):
    """The Speak button preps the whole message itself."""
    import routers.tts_runtime as runtime

    _ready_voice(client, monkeypatch)
    client.post("/api/v1/tts/pronunciations",
                json={"pronunciations": {"Aoife": "EE-fa"}})

    body = runtime.SpeakBody(text="Aoife opened the door.")
    prepared, _truncated = runtime._prepare_speech_text(body)
    assert "EE-fa" in prepared
    assert "Aoife" not in prepared


def test_the_live_path_gets_the_same_table(client):
    """The stream hands over a FUNCTION, so the vault is not read on the event
    loop for the many replies that never speak. It still has to be the same
    table when it finally is read."""
    from tts import stream_hook
    import routers.tts_runtime as runtime

    client.post("/api/v1/tts/pronunciations",
                json={"pronunciations": {"Aoife": "EE-fa"}})

    hook = stream_hook.SpeakHook(
        lambda: _synth(), armed=False,
        pronunciations=runtime.stored_pronunciations,
    )
    assert hook._reading_rules() == {"Aoife": "EE-fa"}
    hook.close()


def test_a_reading_rule_that_cannot_be_read_does_not_cost_the_speech(client):
    """It is an improvement to the voice, not a precondition for it."""
    from tts import stream_hook

    def explode():
        raise RuntimeError("the vault said no")

    hook = stream_hook.SpeakHook(
        lambda: _synth(), armed=False, pronunciations=explode,
    )
    assert hook._reading_rules() == {}
    assert hook.enable() is True
    hook.close()


def test_a_plain_table_still_works(client):
    """Direct construction (tests, and any caller that already has the answer)
    must not be forced through a callable."""
    from tts import stream_hook

    hook = stream_hook.SpeakHook(
        lambda: _synth(), armed=False, pronunciations={"Aoife": "EE-fa"},
    )
    assert hook._reading_rules() == {"Aoife": "EE-fa"}
    hook.close()


def _synth():
    def synth(text):
        return {"audio_id": "a", "seconds": 0.5}

    synth.engine_supports_tags = False
    return synth


# ---------------------------------------------------------------------------
# the pause dial: mechanism shipped, value never did
# ---------------------------------------------------------------------------

def test_the_pause_dial_round_trips(client):
    """ChunkScheduler's gapSeconds has been implemented and tested all along;
    all three production callers built the player with no options, so the value
    was always 0 and the dial existed nowhere."""
    assert client.get("/api/v1/tts/tag-prefs").json()["gap"] == 0.0

    body = client.post("/api/v1/tts/tag-prefs", json={"gap": 0.35}).json()
    assert body["gap"] == pytest.approx(0.35)
    assert client.get("/api/v1/tts/tag-prefs").json()["gap"] == pytest.approx(0.35)


@pytest.mark.parametrize("sent,expected", [(-5.0, 0.0), (99.0, 1.5)])
def test_the_pause_is_clamped_to_something_hearable(client, sent, expected):
    body = client.post("/api/v1/tts/tag-prefs", json={"gap": sent}).json()
    assert body["gap"] == pytest.approx(expected)
    assert body["gap_min"] == 0.0
    assert body["gap_max"] == 1.5
