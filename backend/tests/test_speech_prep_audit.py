"""Audit regressions for speech_prep's splitter and tag mask.

Four defects, one theme: the pipeline's own rule is that a SPAN is decided
before anything chews on its contents ("ORDER IS LOAD-BEARING", module header),
and the sentence splitter did not know about spans at all - not code fences,
not emphasis, not the abbreviations that are deliberately left unexpanded. The
tag mask had the same shape of hole on its left-hand side.

Each failure is silent by construction: words vanish from the audio while
staying on screen, or a sentence is spoken with the wrong cadence. Nothing
errors, so nothing is diagnosable by ear.
"""
import speech_prep as sp


def prep(text, **kw):
    kw.setdefault("engine_supports_tags", False)
    return sp.prepare(text, sp.PrepOptions(**kw))


# ── 1. the queue must strip code BEFORE it splits ──────────────────────────

FENCED = (
    "Run this:\n\n```py\nimport os. os.path\nfoo.bar\n```\n\nThat works."
)


def test_a_fence_with_sentence_punctuation_does_not_eat_the_next_sentence():
    """The whole-text path has always been right; the streaming path was not."""
    whole = prep(FENCED)
    assert "That works." in whole
    assert "os.path" not in whole

    done, rest = sp.sentences_ready(FENCED, flush=True)
    spoken = [prep(chunk) for chunk in done]
    spoken = [s for s in spoken if s]
    joined = " ".join(spoken)
    assert "That works." in joined, "the sentence after the fence was deleted"
    assert "os.path" not in joined, "the code was read aloud"
    assert "foo.bar" not in joined
    assert rest == ""


def test_an_unclosed_fence_is_carried_forward_not_split():
    """Mid-stream the fence is still arriving; splitting it makes the cut."""
    partial = "Say this. Then run:\n\n```py\nimport os. os.path"
    done, rest = sp.sentences_ready(partial)
    # The prose before the fence still streams; the code inside it does not.
    assert [prep(c) for c in done if prep(c)] == ["Say this."]
    assert "os.path" not in " ".join(done)
    assert "```py" in rest, "the open fence must be carried, not consumed"

    # The rest of the fence arrives and closes it.
    done2, rest2 = sp.sentences_ready(rest + "\nfoo.bar\n```\n\nThat works.",
                                      flush=True)
    joined = " ".join(prep(c) for c in done2 if prep(c))
    assert "That works." in joined
    assert "os.path" not in joined


def test_prose_still_streams_sentence_by_sentence():
    """Control: nothing about ordinary text changed."""
    done, rest = sp.sentences_ready("First one. Second one. And a tail")
    assert done == ["First one.", "Second one."]
    assert rest == "And a tail"


# ── 2. a narration span may cover more than one sentence ───────────────────

NARRATION = "*She crosses the room. Her hand rests on the door.* Hello there."


def test_narration_mode_applies_to_every_sentence_of_the_span():
    """`skip` used to drop only the FIRST sentence of a multi-sentence span -
    the rest was spoken aloud, asterisk included, to a user who asked for
    dialogue only."""
    whole = prep(NARRATION, narrative="skip")
    assert whole.strip() == "Hello there."

    done, _ = sp.sentences_ready(NARRATION, flush=True)
    spoken = [prep(c, narrative="skip") for c in done]
    joined = " ".join(s for s in spoken if s).strip()
    assert joined == "Hello there."
    assert "*" not in joined
    assert "door" not in joined


def test_a_split_narration_span_stays_narration_in_every_chunk():
    """Each emitted chunk carries its own complete `*...*`, so per-chunk
    classification agrees with what the screen tints."""
    done, _ = sp.sentences_ready(NARRATION, flush=True)
    narrated = [c for c in done if c.startswith("*")]
    assert narrated, "the span produced no narration chunk"
    for chunk in narrated:
        assert chunk.count("*") % 2 == 0, f"unbalanced span in {chunk!r}"


def test_narrator_mode_tags_every_sentence_of_the_span():
    done, _ = sp.sentences_ready(NARRATION, flush=True)
    tagged = [
        prep(c, narrative="narrator", engine_supports_tags=True) for c in done
    ]
    body = [t for t in tagged if "door" in t or "room" in t]
    assert body, "the narration body was dropped"
    for line in body:
        assert line.lstrip().startswith("["), f"lost the narrator tag: {line!r}"


def test_multiplication_asterisks_are_not_emphasis():
    """Control: `5 * 3 = 15.` must still end a sentence."""
    done, rest = sp.sentences_ready("5 * 3 = 15. Next one.", flush=True)
    assert done == ["5 * 3 = 15.", "Next one."]
    assert rest == ""


def test_bold_does_not_open_an_emphasis_span():
    """Control: `**bold**` toggles twice and nets out."""
    done, _ = sp.sentences_ready("We shipped **three** fixes. Done.", flush=True)
    assert done == ["We shipped **three** fixes.", "Done."]


# ── 3. ambiguous abbreviations keep their period AND their sentence ────────


def test_ambiguous_abbreviations_do_not_cut_the_sentence():
    """`St.` / `Ave.` are deliberately left unexpanded so they stay readable.
    The splitter treated that period as a terminal, so the listener heard
    sentence-final falling intonation and a pause after each one."""
    assert sp.sentences("He lives on St. Mary Ave. in town.") == [
        "He lives on St. Mary Ave. in town."
    ]


def test_a_real_sentence_end_after_an_abbreviation_still_splits():
    """Control: the guard is about the abbreviation, not about periods."""
    out = sp.sentences("He lives on Mt. Rainier. We drove there.")
    assert out == ["He lives on Mt. Rainier.", "We drove there."]


def test_ordinary_sentences_still_split():
    assert sp.sentences("One. Two. Three.") == ["One.", "Two.", "Three."]


# ── 4. the tag mask must not eat an array subscript ────────────────────────


def test_array_subscripts_survive_on_engines_without_tags():
    """`arr[idx]` was masked as a delivery tag and then DELETED, so the words
    were on screen and gone from the audio with nothing to explain it."""
    spoken = prep("The list is arr[idx] and mapping[name] here.")
    assert "arr" in spoken and "mapping" in spoken
    assert "here" in spoken
    assert spoken.count("and") == 1


def test_a_real_delivery_tag_is_still_recognised():
    """Control: a standalone tag still masks (kept or dropped by engine)."""
    kept = prep("[whisper] come closer.", engine_supports_tags=True)
    assert "[whisper]" in kept
    dropped = prep("[whisper] come closer.", engine_supports_tags=False)
    assert "[" not in dropped and "whisper" not in dropped


def test_a_tag_after_punctuation_is_still_a_tag():
    """The guard is `\\w` only - punctuation and space still open a tag."""
    dropped = prep("Wait. [softly] come here.", engine_supports_tags=False)
    assert "softly" not in dropped
    assert "Wait" in dropped and "come here" in dropped
