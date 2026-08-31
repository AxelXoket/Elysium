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


def test_a_repeated_direction_survives_the_next_sentence():
    """REVERSED, and the old assertion was the bug.

    This used to require the second "[warm]" to be swallowed, on the reasoning
    that a repeated direction is "one continuing instruction". Nothing in this
    app continues it. Each sentence is a separate host.speak() and therefore a
    separate autoregressive generation with its own KV cache - there is no
    acoustic history for sentence two to inherit a tone from. Dropping its tag
    did not sustain the warmth; it removed the only thing that would have
    produced it, and that sentence came out in the baseline voice. It is the
    "delivery goes plain the further a long reply runs" complaint, in code.

    Within ONE sentence the collapse is still right - see the next test.
    """
    budget = voice_tags.TagBudget(5)
    first = voice_tags.sanitize_for_tts(
        "[warm] Come in. ", engine_supports_tags=True, budget=budget)
    second = voice_tags.sanitize_for_tts(
        "[warm] Sit down. ", engine_supports_tags=True, budget=budget)
    assert _tags(first) == ["warm"]
    assert _tags(second) == ["warm"], (
        "the second sentence is its own generation and needs its own direction"
    )
    assert budget.remaining == 3, "and it costs the reply an allowance each"


def test_a_direction_repeated_inside_one_sentence_still_collapses():
    """The half that stays. Two identical directions in one breath are one
    instruction - here there IS a continuing generation to carry it."""
    budget = voice_tags.TagBudget(5)
    out = voice_tags.sanitize_for_tts(
        "[warm] Come in. [warm] Sit down.",
        engine_supports_tags=True, budget=budget)
    assert _tags(out) == ["warm"]


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

        def speak(self, text, values, extra=None, message_id=None,
                  stream_token=None):
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

        def speak(self, text, values, extra=None, message_id=None,
                  stream_token=None):
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

        def speak(self, text, values, extra=None, message_id=None,
                  stream_token=None):
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


# ---------------------------------------------------------------------------
# narration does not spend the density the dial reserves for the MODEL
# ---------------------------------------------------------------------------

import speech_prep  # noqa: E402

#: What both call sites exempt: the tags the APP injects, not the model.
_FREE = (speech_prep.DEFAULT_NARRATOR_TAG, speech_prep.DEFAULT_SPEECH_TAG)


#: Long enough that narration plus dialogue EXCEEDS the default allowance -
#: fourteen tags against a cap of eight. A shorter sample fits inside the
#: budget and passes whether or not narration is charged for, which is a test
#: that cannot fail.
RP = [
    "*She steps closer, the floorboards complaining.*",
    "[whisper] You came back.",
    "*Her hand finds the edge of the table.*",
    "[soft] I did not think you would.",
    "*The lamp gutters once and holds.*",
    "[laughing] Look at your face.",
    "*She pulls out the other chair with her foot.*",
    "[warm] Sit down before you fall down.",
    "*Outside, a car passes and the light sweeps the ceiling.*",
    "[sad] I waited a long time.",
    "*She does not look up when she says it.*",
    "[cold, clipped tone] Do not apologise.",
    "*The kettle starts somewhere behind her.*",
    "[teasing] You never could stand still.",
]


def _spoken(sentences, mode, cap=voice_tags.MAX_TAGS_PER_REPLY):
    """One reply through the real pipeline, the way both paths run it:
    prepare() decides narration, sanitize_for_tts() spends the budget."""
    budget = voice_tags.TagBudget(cap)
    opts = speech_prep.PrepOptions(engine_supports_tags=True, narrative=mode)
    return [
        voice_tags.sanitize_for_tts(
            speech_prep.prepare(s, opts), engine_supports_tags=True,
            budget=budget, free_tags=_FREE)
        for s in sentences
    ]


def _dialogue_tags(spoken: list[str]) -> list[str]:
    narrator = speech_prep.DEFAULT_NARRATOR_TAG
    return [t for line in spoken for t in _tags(line) if t != narrator]


def test_narration_mode_does_not_cost_the_model_its_delivery_tags():
    """MEASURED: on a roleplay reply in "narrator" mode the injected narration
    tags ate the allowance and 3 of 7 dialogue tags went out plain - all of
    them at the end, so the reply flattened as it went. A comparison, not a
    hard-coded count: whatever the model asked for in "same" mode it must
    still get when the user chooses how narration is read."""
    plain = _dialogue_tags(_spoken(RP, "same"))
    narrated = _dialogue_tags(_spoken(RP, "narrator"))
    assert narrated == plain


def test_the_narration_tag_itself_is_still_placed_on_every_span():
    """Exempting it from the budget must not make it optional."""
    spoken = _spoken(RP, "narrator")
    narrator = speech_prep.DEFAULT_NARRATOR_TAG
    tagged = [line for line in spoken if narrator in _tags(line)]
    assert len(tagged) == 7


def test_a_dialogue_tag_after_narration_is_not_swallowed_as_a_duplicate():
    """The exempt tag still takes part in the duplicate collapse, so it has to
    update `last_tag` - but that must not eat the NEXT direction."""
    budget = voice_tags.TagBudget(8)
    narrator = speech_prep.DEFAULT_NARRATOR_TAG
    first = voice_tags.sanitize_for_tts(
        "[warm] Come in.", engine_supports_tags=True, budget=budget,
        free_tags=_FREE)
    middle = voice_tags.sanitize_for_tts(
        f"[{narrator}] She closed the door.", engine_supports_tags=True,
        budget=budget, free_tags=_FREE)
    third = voice_tags.sanitize_for_tts(
        "[warm] Sit anywhere.", engine_supports_tags=True, budget=budget,
        free_tags=_FREE)
    assert _tags(first) == ["warm"]
    assert _tags(middle) == [narrator]
    assert _tags(third) == ["warm"], "narration interrupted, so restate it"


def test_every_narration_sentence_keeps_its_own_narrator_tag():
    """REVERSED for the same reason as the dialogue case above, and this one
    is worse: swallowing the second narrator tag meant the second sentence of
    a two-sentence narration span was performed in the CHARACTER's voice.
    Narration mode appeared to work and then quietly stopped one sentence in.
    """
    budget = voice_tags.TagBudget(8)
    narrator = speech_prep.DEFAULT_NARRATOR_TAG
    first = voice_tags.sanitize_for_tts(
        f"[{narrator}] She closed the door.", engine_supports_tags=True,
        budget=budget, free_tags=_FREE)
    second = voice_tags.sanitize_for_tts(
        f"[{narrator}] The lamp guttered.", engine_supports_tags=True,
        budget=budget, free_tags=_FREE)
    assert _tags(first) == [narrator]
    assert _tags(second) == [narrator]
    assert budget.remaining == 8, "narration never touches the allowance"


# ---------------------------------------------------------------------------
# the standing tone closes its own narration
# ---------------------------------------------------------------------------

def _through_the_pipeline(text, tone, mode="narrator"):
    """Both halves the way production runs them: prepare() injects, then
    sanitize_for_tts filters, then the standing tone is prefixed."""
    import routers.tts_runtime as runtime

    closing = runtime._closing_tag(tone)
    opts = speech_prep.PrepOptions(engine_supports_tags=True, narrative=mode,
                                   speech_tag=closing)
    budget = voice_tags.TagBudget(voice_tags.MAX_TAGS_PER_REPLY)
    spoken = voice_tags.sanitize_for_tts(
        speech_prep.prepare(text, opts), engine_supports_tags=True,
        budget=budget, free_tags=speech_prep.injected_tags(closing))
    return voice_tags.apply_default_tone(spoken, tone,
                                         engine_supports_tags=True)


TONE = "deep, slow, close to the ear"


def test_the_standing_tone_is_what_ends_a_narration_span():
    """MEASURED BUG: the closing direction was a hard-coded "in character,
    natural", so the voice the user configured was replaced by a generic one
    for every clause following a `*...*` span."""
    out = _through_the_pipeline("I said nothing. *She looks away.* Really.", TONE)
    assert out.count(f"[{TONE}]") == 2, out
    assert speech_prep.DEFAULT_SPEECH_TAG not in out


def test_a_reply_that_opens_with_narration_still_reaches_the_tone():
    """The worst case: apply_default_tone only prefixes text that does not
    already start with a tag, so with narration first the standing tone never
    reached the engine at all."""
    out = _through_the_pipeline("*She looks away.* Really, I mean it.", TONE)
    assert TONE in out, out


def test_a_tone_too_long_to_be_a_tag_falls_back_instead_of_vanishing():
    """sanitize_tone allows 60 characters; _looks_like_tag allows 40 and six
    words. Injecting an unusable span drops it as malformed and leaves the
    narrator's direction standing over the dialogue."""
    long_tone = "a very long standing tone that goes on and on past the limit"
    assert not voice_tags.usable_as_tag(long_tone)
    out = _through_the_pipeline("*She waits.* Say it.", long_tone)
    assert f"[{speech_prep.DEFAULT_SPEECH_TAG}]" in out, out


def test_with_no_tone_set_nothing_changes():
    out = _through_the_pipeline("*She waits.* Say it.", "")
    assert f"[{speech_prep.DEFAULT_SPEECH_TAG}]" in out


def test_the_closing_tag_is_never_charged_to_the_density_budget():
    """It is the app's rendering choice, not the model's enthusiasm - and now
    that it can BE the tone, a constant exempt list would have started charging
    for it silently."""
    import routers.tts_runtime as runtime

    closing = runtime._closing_tag(TONE)
    budget = voice_tags.TagBudget(2)
    opts = speech_prep.PrepOptions(engine_supports_tags=True,
                                   narrative="narrator", speech_tag=closing)
    for sentence in ["*She waits.* [whisper] Say it.",
                     "*He turns.* [soft] Again.",
                     "*They stop.* [warm] Once more."]:
        voice_tags.sanitize_for_tts(
            speech_prep.prepare(sentence, opts), engine_supports_tags=True,
            budget=budget, free_tags=speech_prep.injected_tags(closing))
    assert budget.remaining == 0, "the model spent its two"


def test_a_long_reply_keeps_its_direction_all_the_way_down():
    """MEASURED: a real reply from this app split into 13 sentences.

    With the prompt asking for a tone tag on each sentence that needs one, the
    old ceiling of 8 stripped every direction from the ninth sentence on -
    silently, and always at the END, so the reply started performed and
    finished flat. That is the complaint this ceiling was raised to answer, and
    the ceiling is the thing that has to be tested: the prompt can ask for
    whatever it likes, this module is what binds.
    """
    budget = voice_tags.TagBudget(voice_tags.MAX_TAGS_PER_REPLY)
    moods = ["warm", "soft", "amused", "quiet, hurt", "low voice", "teasing",
             "tired", "urgent", "seductive", "firm", "sad", "playful", "flat"]
    kept = []
    for i, mood in enumerate(moods):
        out = voice_tags.sanitize_for_tts(
            f"[{mood}] Sentence number {i}.", engine_supports_tags=True,
            budget=budget)
        kept.append(_tags(out))

    assert all(k for k in kept), (
        f"sentence {[i for i, k in enumerate(kept) if not k]} lost its "
        "direction - a long reply still goes flat before it ends"
    )
