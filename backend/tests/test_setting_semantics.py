"""Audit KÖK 6: one setting, two paths, two meanings.

The live path synthesises sentence by sentence as the reply arrives; the Speak
button synthesises a stored message. Every delivery dial is read by both, and
each one was applied at a different granularity on each side - so the same
setting on the same message produced a different performance depending on
which button the user had pressed. Nothing failed; it just did not match.

These tests are written as COMPARISONS on purpose. Asserting each path against
a hard-coded expectation is what let them drift: both were individually
"correct", against two different ideas of what the number meant.
"""

from __future__ import annotations

import pytest

import voice_tags
from test_notice_channel import voice, _ready_voice  # noqa: F401


# ---------------------------------------------------------------------------
# density: a budget for the REPLY, not for each sentence
# ---------------------------------------------------------------------------

SENTENCES = [
    "[whisper] The house was quiet. ",
    "[soft] Nobody had been here in years. ",
    "[low voice] The door stood open. ",
]
WHOLE = "".join(SENTENCES)


def _tags(text: str) -> list[str]:
    out, buf = [], text
    while "[" in buf:
        i = buf.index("[")
        j = buf.find("]", i)
        if j == -1:
            break
        out.append(buf[i + 1:j])
        buf = buf[j + 1:]
    return out


def test_the_cap_means_the_same_thing_sentence_by_sentence_and_all_at_once():
    """The reported number: density 3 on a six-tag reply gave 6 tags live and
    3 on replay. The counter was call-local and the live path calls once per
    sentence."""
    whole = voice_tags.sanitize_for_tts(
        WHOLE, engine_supports_tags=True, max_tags=2)

    budget = voice_tags.TagBudget(2)
    piecewise = "".join(
        voice_tags.sanitize_for_tts(s, engine_supports_tags=True, budget=budget)
        for s in SENTENCES
    )

    assert len(_tags(whole)) == 2
    assert len(_tags(piecewise)) == len(_tags(whole))


def test_the_budget_runs_out_and_stays_out():
    budget = voice_tags.TagBudget(1)
    kept = [
        _tags(voice_tags.sanitize_for_tts(
            s, engine_supports_tags=True, budget=budget))
        for s in SENTENCES
    ]
    assert kept == [["whisper"], [], []]


def test_a_repeated_direction_collapses_across_a_sentence_boundary_too():
    """"[warm] ... [warm] ..." is one continuing instruction. Within a single
    call that already collapsed; across two calls it did not, so the live path
    re-stated a tag the replay path had absorbed."""
    budget = voice_tags.TagBudget(5)
    first = voice_tags.sanitize_for_tts(
        "[warm] Come in. ", engine_supports_tags=True, budget=budget)
    second = voice_tags.sanitize_for_tts(
        "[warm] Sit down. ", engine_supports_tags=True, budget=budget)
    assert _tags(first) == ["warm"]
    assert _tags(second) == []


def test_a_caller_without_a_budget_is_unchanged():
    """The whole-message callers must keep behaving exactly as before."""
    once = voice_tags.sanitize_for_tts(
        WHOLE, engine_supports_tags=True, max_tags=3)
    again = voice_tags.sanitize_for_tts(
        WHOLE, engine_supports_tags=True, max_tags=3)
    assert once == again
    assert len(_tags(once)) == 3


def test_the_live_synth_spends_one_budget_across_the_whole_reply(
    client, voice, monkeypatch,
):
    """The end of the wire, not just the helper.

    make_stream_synth's closure is called once per SENTENCE by the queue, and
    it used to build its cap from scratch on each call. This is the assertion
    that would have failed before: three sentences, one tag each, a cap of two.
    """
    import routers.tts_runtime as runtime

    _ready_voice(client, monkeypatch)
    client.post("/api/v1/tts/tag-prefs", json={"density": 2})

    spoken: list[str] = []

    class _Host:
        def snapshot(self):
            return {"state": "loaded", "uid": "u"}

        def load(self, *a, **k):
            return {}

        def speak(self, text, values, extra=None):
            spoken.append(text)
            return {"path": "a.wav", "seconds": 1.0, "sample_rate": 44100}

    monkeypatch.setattr(runtime, "_host", lambda: _Host())

    synth = runtime.make_stream_synth()
    if not getattr(synth, "engine_supports_tags", False):
        pytest.skip("this engine strips tags outright; the cap has no meaning")
    for sentence in SENTENCES:
        synth(sentence)

    kept = sum(len(_tags(s)) for s in spoken)
    assert kept == 2, (
        "the density dial is a budget for the reply; it reset every sentence"
    )


# ---------------------------------------------------------------------------
# tone: a prefix on EVERY sentence, because every sentence is its own call
# ---------------------------------------------------------------------------

def test_the_standing_tone_reaches_every_sentence():
    """/speak applied the tone once to the whole message and THEN split it
    into one engine call per sentence - so sentence two onwards was spoken
    with no direction at all, while the live path (which prefixes per call)
    got it right. The engine calls are independent; the prefix has to be too.
    """
    import routers.tts_runtime as runtime

    spoken: list[str] = []

    class _Host:
        def snapshot(self):
            return {"state": "loaded", "uid": "u"}

        def load(self, *a, **k):
            return {}

        def speak(self, text, values, extra=None):
            spoken.append(text)
            return {"path": "", "seconds": 1.0, "sample_rate": 44100}

    runtime._speak_in_sentences(
        _Host(), "First one here. Second one here. Third one here.",
        {}, None, supports_tags=True, density=5, tone="warm",
    )

    assert len(spoken) == 3, "the message must reach the engine in pieces"
    assert all(s.startswith("[warm]") for s in spoken), (
        "every sentence is its own engine call, so every one needs the tone"
    )


def test_the_tone_yields_to_the_models_own_direction():
    """Unchanged rule: a sentence that already opens with a tag keeps it - the
    model's choice for THIS line beats a standing default."""
    import routers.tts_runtime as runtime

    spoken: list[str] = []

    class _Host:
        def snapshot(self):
            return {"state": "loaded", "uid": "u"}

        def load(self, *a, **k):
            return {}

        def speak(self, text, values, extra=None):
            spoken.append(text)
            return {"path": "", "seconds": 1.0, "sample_rate": 44100}

    runtime._speak_in_sentences(
        _Host(), "[urgent] Run. Then keep running.", {}, None,
        supports_tags=True, density=5, tone="warm",
    )
    assert spoken[0].startswith("[urgent]")
    assert spoken[1].startswith("[warm]")


# ---------------------------------------------------------------------------
# narrative: read by the replay path, written by nothing
# ---------------------------------------------------------------------------

def test_the_narration_mode_can_actually_be_stored(client):
    """_narrative_pref has read tts_narrative since it was written and NO code
    path ever wrote it. Picking "Skip" applied while a reply streamed (the mode
    rides on the request) and was ignored the moment the Speak button repeated
    the same message."""
    assert client.get("/api/v1/tts/tag-prefs").json()["narrative"] == "same"

    saved = client.post("/api/v1/tts/tag-prefs", json={"narrative": "skip"})
    assert saved.status_code == 200
    assert saved.json()["narrative"] == "skip"
    # And it survives a fresh read - the point of storing it.
    assert client.get("/api/v1/tts/tag-prefs").json()["narrative"] == "skip"


def test_the_replay_path_reads_what_was_stored(client):
    import routers.tts_runtime as runtime

    client.post("/api/v1/tts/tag-prefs", json={"narrative": "narrator"})
    assert runtime._narrative_pref() == "narrator"


@pytest.mark.parametrize("bad", ["", "loud", "SKIP", "narrator voice"])
def test_a_mode_nobody_implements_is_refused(client, bad):
    """Silently coercing it to "same" would be the same silence this fixes."""
    r = client.post("/api/v1/tts/tag-prefs", json={"narrative": bad})
    assert r.status_code == 422
    assert r.json()["detail"] == "tts_invalid_narrative"


def test_surrounding_whitespace_is_a_typo_not_a_new_mode(client):
    """Rejecting " skip " would be pedantry; accepting "loud" would be the
    silent coercion this endpoint exists to avoid. Only the first is trimmed."""
    body = client.post("/api/v1/tts/tag-prefs",
                       json={"narrative": "  skip  "}).json()
    assert body["narrative"] == "skip"


def test_saving_one_dial_does_not_disturb_the_others(client):
    client.post("/api/v1/tts/tag-prefs",
                json={"narrative": "skip", "density": 2, "tone": "warm"})
    body = client.post("/api/v1/tts/tag-prefs", json={"density": 4}).json()
    assert body["narrative"] == "skip"
    assert body["tone"] == "warm"
    assert body["density"] == 4
