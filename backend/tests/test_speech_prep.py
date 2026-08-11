"""V8-1 - speech_prep: what the engine READS.

The written reply and the spoken reply are not the same text. Markdown is a
typography instruction, not a word; "Dr." is two syllables, not one; and a code
block read aloud is noise. This module is the difference, and these tests are
its contract.

The governing rule everywhere below is BELIRSIZSE DOKUNMA - when a pattern is
ambiguous the text passes through unchanged. A reply that speaks a stray
asterisk is a small annoyance; a reply that silently drops half a sentence
because a heuristic over-reached is a bug the user cannot diagnose by ear.
"""
import pytest

import voice_tags
import speech_prep as sp


def prep(text, **kw):
    kw.setdefault("engine_supports_tags", False)
    return sp.prepare(text, sp.PrepOptions(**kw))


# ── layer 1: code ────────────────────────────────────────────────────────────

def test_fenced_code_block_is_dropped_entirely():
    out = prep("Here is how:\n```python\nx = [1, 2]\nprint(x)\n```\nThat is it.")
    assert "x = " not in out and "print" not in out
    assert "Here is how" in out and "That is it" in out


def test_inline_code_keeps_the_word_without_the_backticks():
    assert prep("Call `useState` first.") == "Call useState first."


def test_asterisks_inside_code_are_not_narrative():
    # MEASURED: the backticks go and the freed asterisk stays, flanked by
    # its spaces. The old assertion accepted "a  b" as well, i.e. it was
    # willing to pass whether or not the asterisk survived - two
    # contradictory outcomes, so neither was pinned.
    out = prep("Use `a * b` here.", narrative="skip")
    assert out == "Use a * b here."


def test_unclosed_fence_does_not_eat_the_rest_of_the_reply():
    # A stream cut mid-block, or a model that forgot to close: the words after
    # it still deserve to be heard.
    out = prep("Look:\n```\nnot closed")
    assert "Look" in out


# ── layer 2: links ───────────────────────────────────────────────────────────

def test_markdown_link_speaks_only_its_label():
    assert prep("See [the docs](https://example.com/a/b) now.") == \
        "See the docs now."


def test_bare_url_is_not_spelled_out():
    out = prep("Go to https://example.com/x?y=1 today.")
    assert "https" not in out and "example" not in out
    assert "Go to" in out and "today" in out


def test_reference_style_definition_is_dropped():
    out = prep("Text here.\n\n[1]: https://example.com")
    assert "Text here." in out and "https" not in out


# ── layer 3: tags are protected from every other layer ───────────────────────

def test_tag_survives_symbol_and_punctuation_layers():
    out = prep("[soft, close to the ear] Come here & sit.",
               engine_supports_tags=True)
    assert "[soft, close to the ear]" in out
    assert "and sit" in out


def test_tag_is_removed_for_an_engine_without_tag_support():
    out = prep("[whisper] Come here.", engine_supports_tags=False)
    assert "[" not in out and "whisper" not in out
    assert "Come here." in out


def test_a_citation_becomes_a_delivery_direction_on_the_way_to_the_engine():
    """CHARACTERIZATION of K-19, not approval. Do not "fix" this test.

    This used to be called `test_a_bracket_that_is_not_a_tag_is_left_alone` and
    asserted `"earlier" in out` - it never looked at the bracket the name was
    about, so it passed while the opposite of its name was happening.

    A digit disqualifies `[1]` from being a delivery tag, so `_mask_tags` (step
    3) leaves it unprotected. `_expand_numbers` (step 8) then rewrites the digit
    into a word and the span comes out newly tag SHAPED. The module's own rule
    is that a span is decided before anything chews on its contents; here the
    decision was made on the old text and the test applied to the new.
    """
    out = prep("As shown [1] earlier.", engine_supports_tags=True)
    assert out == "As shown [one] earlier.", (
        "K-19 changed shape - re-measure before editing this")
    # And it is now well formed enough that the tag sanitiser hands it to a
    # tag reading engine as a real instruction rather than as words.
    assert voice_tags.usable_as_tag("one")


def test_a_citation_is_deleted_outright_on_a_plain_engine():
    """The other half of K-19.

    An engine that cannot read directions gets the whole span REMOVED, so the
    citation is not merely unspoken, it is gone, and the sentence keeps the
    hole where it was. The neighbouring words DO survive - an earlier draft of
    this note claimed otherwise and an adversary check corrected it; the
    equality below is what was actually measured.

    The reader's door keeps `[1]` untouched (SURVIVORS corpus, in
    test_voice_tags.py), so the reader and the listener are shown two
    different sentences. That divergence is the finding, not word loss.
    """
    prepared = prep("The answer is 42 [1]. See [2] for details.")
    assert prepared == "The answer is forty-two [one]. See [two] for details."
    spoken = voice_tags.sanitize_for_tts(prepared, engine_supports_tags=False)
    # Exact on purpose, gap in the spacing included. This is a
    # characterization test: ANY change to this string means somebody
    # touched the behaviour and has to come and read K-19 first.
    assert spoken == "The answer is forty-two . See for details.", spoken
    # The display door, for contrast, is intact.
    assert voice_tags.strip_tags("The answer is 42 [1]. See [2] for details.") == (
        "The answer is 42 [1]. See [2] for details.")


# ── layer 4: narrative ───────────────────────────────────────────────────────

NARR = "*She leans closer.* \"Come here,\" she says."


def test_narrative_same_reads_everything_without_the_asterisks():
    out = prep(NARR, narrative="same")
    assert "*" not in out
    assert "She leans closer." in out and "Come here" in out


def test_narrative_skip_drops_the_narration_but_keeps_the_speech():
    out = prep(NARR, narrative="skip")
    assert "She leans closer" not in out
    assert "Come here" in out


def test_narrative_narrator_tags_the_span_for_a_tag_capable_engine():
    out = prep(NARR, narrative="narrator", engine_supports_tags=True)
    assert out.count("[") >= 1
    assert "She leans closer." in out and "Come here" in out


def test_narrator_mode_falls_back_to_plain_when_tags_unsupported():
    # A bracket read aloud is worse than a missing tone change.
    out = prep(NARR, narrative="narrator", engine_supports_tags=False)
    assert "[" not in out
    assert "She leans closer." in out


def test_the_narration_tone_stops_where_the_asterisks_stop():
    """MEASURED BUG: a direction stands until the next tag or the end of the
    sentence, and narration was opened without ever being closed. So

        *She smiles and waves.* "It is good to see you again."

    went to the engine as ONE measured, detached line - the greeting performed
    in the narrator's voice. The asterisks end; the direction has to end too.
    """
    out = prep(NARR, narrative="narrator", engine_supports_tags=True)
    narration_at = out.index(sp.DEFAULT_NARRATOR_TAG)
    speech_at = out.index(sp.DEFAULT_SPEECH_TAG)
    assert narration_at < out.index("She leans closer.") < speech_at
    assert speech_at < out.index("Come here")


def test_dialogue_before_any_narration_is_not_given_a_direction():
    """Nothing has been opened yet, so there is nothing to close. An unasked-for
    tag on ordinary speech is the caricature the prompt works to avoid."""
    out = prep("I missed you. *She looks away.* Really.",
               narrative="narrator", engine_supports_tags=True)
    assert out.index("I missed you.") < out.index(sp.DEFAULT_NARRATOR_TAG)
    assert out.count(sp.DEFAULT_SPEECH_TAG) == 1


def test_the_models_own_direction_closes_the_narration_by_itself():
    """Two directions on one clause muddy both. The model chose this one for
    THIS line, which is more specific than our generic return-to-voice."""
    out = prep("*She steps closer.* [whisper] You came back.",
               narrative="narrator", engine_supports_tags=True)
    assert "[whisper]" in out
    assert sp.DEFAULT_SPEECH_TAG not in out


def test_narration_is_not_closed_in_the_modes_that_never_opened_it():
    for mode in ("same", "skip"):
        out = prep(NARR, narrative=mode, engine_supports_tags=True)
        assert sp.DEFAULT_SPEECH_TAG not in out, mode
    plain = prep(NARR, narrative="narrator", engine_supports_tags=False)
    assert sp.DEFAULT_SPEECH_TAG not in plain


def test_bold_is_not_narrative():
    out = prep("This is **really** important.", narrative="skip")
    assert "really" in out and "*" not in out


def test_arithmetic_asterisk_is_left_alone():
    # parseMessage's OPEN guard: a letter/digit before the run kills it.
    # Exact, because "5" and "exactly" survive almost any transformation:
    # the claim is that the ASTERISKS are untouched, and only equality
    # says so. Each * is preceded by a digit, which fails the emphasis
    # opening guard, and _expand_numbers excludes * on both edges.
    out = prep("The answer is 5*3*2 exactly.", narrative="skip")
    assert out == "The answer is 5*3*2 exactly."


# ── layer 5: structural markdown ─────────────────────────────────────────────

def test_heading_marker_is_removed_but_the_words_stay():
    out = prep("## The Plan\nWe begin.")
    assert "#" not in out
    assert "The Plan" in out and "We begin" in out


def test_list_markers_become_pauses_not_the_word_dash():
    out = prep("- first\n- second")
    assert "-" not in out
    assert "first" in out and "second" in out


def test_blockquote_and_rule_markers_go():
    out = prep("> quoted line\n\n---\n\nafter")
    assert ">" not in out and "---" not in out
    assert "quoted line" in out and "after" in out


# ── layer 6: abbreviations ───────────────────────────────────────────────────

@pytest.mark.parametrize("src,want", [
    ("Dr. Smith arrived.", "Doctor Smith arrived."),
    ("Mr. and Mrs. Vale.", "Mister and Missus Vale."),
    ("e.g. this one.", "for example this one."),
    ("i.e. the other.", "that is the other."),
    ("cats vs. dogs", "cats versus dogs"),
    ("bread, etc.", "bread, et cetera."),
])
def test_abbreviation_expansion(src, want):
    assert prep(src) == want


def test_ambiguous_abbreviation_is_left_alone():
    # "St." is Saint or Street and nothing in the sentence settles it.
    out = prep("He lives on Elm St. now.")
    assert "St." in out


def test_abbreviation_period_no_longer_splits_a_sentence():
    # The same dictionary that fixes pronunciation protects the splitter.
    assert sp.sentences("Dr. Smith arrived. Then he left.") == \
        ["Doctor Smith arrived.", "Then he left."]


# ── layer 7: numbers ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("src,want", [
    ("in 1984", "in nineteen eighty-four"),
    ("in 2026", "in twenty twenty-six"),
    ("in 2005", "in two thousand five"),
    ("in 2000", "in two thousand"),
    ("3.14 exactly", "three point one four exactly"),
    ("1,234 items", "one thousand two hundred thirty-four items"),
    ("20% done", "twenty percent done"),
    ("$5 each", "five dollars each"),
    ("the 1st time", "the first time"),
    ("the 22nd row", "the twenty-second row"),
])
def test_number_expansion(src, want):
    assert prep(src) == want


def test_a_version_string_is_left_alone():
    # v1.2.0 is not a decimal and guessing would mangle it.
    out = prep("running v1.2.0 now")
    assert "v1.2.0" in out


# ── layer 8: punctuation, symbols, emoji ─────────────────────────────────────

@pytest.mark.parametrize("src,want", [
    ("you & me", "you and me"),
    ("mail me @ home", "mail me at home"),
    ("a/b choice", "a or b choice"),
    ("wait...", "wait..."),
    ("stop!!!", "stop!"),
    ("really???", "really?"),
])
def test_symbol_and_punctuation(src, want):
    assert prep(src) == want


def test_em_dash_becomes_a_pause_not_a_word():
    out = prep("I waited - then left.")
    assert "dash" not in out and "hyphen" not in out
    assert "waited" in out and "then left" in out


def test_hyphenated_word_keeps_its_hyphen():
    assert "well-known" in prep("a well-known face")


def test_emoji_is_removed_and_does_not_leave_a_double_space():
    assert prep("Hello 🙂 there") == "Hello there"


# ── layer 9: user pronunciation dictionary ───────────────────────────────────

def test_user_pronunciation_is_applied_whole_word_only():
    out = prep("Elysium and Elysiumly",
               pronunciations={"Elysium": "eh LISS ee um"})
    assert "eh LISS ee um and Elysiumly" == out


def test_user_pronunciation_beats_the_builtin_dictionary():
    out = prep("Dr. Vale", pronunciations={"Dr.": "Drah"})
    assert out.startswith("Drah")


# ── whole-pipeline behaviour ─────────────────────────────────────────────────

def test_empty_and_whitespace_survive_without_exploding():
    assert prep("") == ""
    assert prep("   \n\n  ") == ""


def test_a_reply_that_is_only_a_code_block_yields_nothing_to_say():
    assert prep("```\nx = 1\n```") == ""


def test_order_is_stable_under_repeat_application():
    # prepare() must be idempotent: V8-3 may re-prepare a tail after a retry,
    # and a second pass that keeps chewing would drift from the first.
    once = prep("## Dr. Smith\n- 20% of [it](http://x.y) & more")
    assert once, "prep() returned nothing - the check below is vacuous"
    assert prep(once) == once


def test_the_internal_mask_marker_cannot_crash_preparation():
    """Regression: a reply carrying the sentinel byte pattern used to raise
    IndexError from inside `prepare`, which the synthesis queue turns into a
    dead utterance. A reply must not lose its voice over a stray byte."""
    hostile = sp._SENTINEL + "tj" + sp._SENTINEL + " hello"
    out = prep(hostile, engine_supports_tags=True)
    assert "hello" in out


def test_plain_prose_is_returned_untouched():
    src = "She looked at me and smiled, then said nothing at all."
    assert prep(src) == src


# ── sentence splitting (feeds the V8 synthesis queue) ────────────────────────

def test_sentences_splits_on_terminals():
    assert sp.sentences("One. Two! Three?") == ["One.", "Two!", "Three?"]


def test_sentences_keeps_decimals_and_ellipsis_intact():
    assert sp.sentences("It cost 3.5 units... then more.") == \
        ["It cost 3.5 units...", "then more."]


def test_sentences_does_not_split_inside_a_quote():
    assert sp.sentences('She said "Wait. Stop." and left.') == \
        ['She said "Wait. Stop." and left.']


def test_sentences_on_empty_is_empty():
    assert sp.sentences("") == []


def test_incremental_only_yields_complete_sentences():
    # V8-2: the splitter runs on a GROWING buffer. A half-written sentence must
    # never be handed to the engine - it would be spoken with a false cadence
    # and the rest would arrive as a separate utterance.
    done, rest = sp.sentences_ready("Hello there. How are y")
    assert done == ["Hello there."]
    assert rest == "How are y"


def test_incremental_holds_a_terminal_that_may_still_grow():
    # "3." could be "3.5" one delta later.
    done, rest = sp.sentences_ready("It cost 3.")
    assert done == []
    assert rest == "It cost 3."


def test_incremental_flush_releases_the_tail():
    done, rest = sp.sentences_ready("No terminal here", flush=True)
    assert done == ["No terminal here"]
    assert rest == ""


# ── audit regressions (2026-07-25 whole-repo audit) ──────────────────────────

def test_the_word_no_is_not_read_as_the_word_number():
    """Regression: "no." was in the abbreviation table, so the commonest word in
    a conversation was spoken as "number" AND lost the full stop that ended the
    sentence. Capitalised mid-sentence "No. 5" really is a number and stays."""
    assert prep("Absolutely no.") == "Absolutely no."
    assert "number five" in prep("See No. 5 there.")


def test_a_carried_over_tail_keeps_the_space_that_separates_two_words():
    """Regression: the growing-buffer remainder was returned .strip()ed and the
    next delta concatenated straight onto it, welding "How are " + "you" into
    "How areyou" - inaudibly wrong, with the on-screen copy still correct."""
    done, rest = sp.sentences_ready("How are ")
    assert done == []
    done, _ = sp.sentences_ready(rest + "you today?")
    assert done == ["How are you today?"]


def test_a_finished_text_still_has_its_tail_trimmed():
    assert sp.sentences("One.   ") == ["One."]

# ── folded in from test_speech_prep_audit.py (KADEME 13) ───────────────
#
# Four defects, one theme: the pipeline's own rule is that a SPAN is decided
# before anything chews on its contents ("ORDER IS LOAD-BEARING", module
# header), and the sentence splitter did not know about spans at all - not
# code fences, not emphasis, not the abbreviations that are deliberately
# left unexpanded. The tag mask had the same shape of hole on its left hand
# side. Each failure is silent by construction: words vanish from the audio
# while staying on screen, or a sentence is spoken with the wrong cadence.
# Nothing errors, so nothing is diagnosable by ear.
#
# They were verified under a separate filename, which left THIS file still
# describing the world before the fix. Two of the fourteen were already
# proven here and were dropped rather than moved:
#   test_ordinary_sentences_still_split -> test_sentences_splits_on_terminals
#     covers the same split with three different terminals, not just periods
#   test_a_real_delivery_tag_is_still_recognised -> the dropped half is
#     test_tag_is_removed_for_an_engine_without_tag_support, and the kept
#     half is test_tag_survives_symbol_and_punctuation_layers

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


def test_two_finished_sentences_are_released_in_one_call():
    """Also the control the fence tests above lean on: ordinary prose is
    unaffected. Kept in the fold because no other test hands
    `sentences_ready` a buffer holding two already complete sentences, so
    the multi-sentence return path is proven nowhere else."""
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


def test_array_subscripts_survive_on_engines_without_tags():
    """`arr[idx]` was masked as a delivery tag and then DELETED, so the words
    were on screen and gone from the audio with nothing to explain it."""
    spoken = prep("The list is arr[idx] and mapping[name] here.")
    assert "arr" in spoken and "mapping" in spoken
    assert "here" in spoken
    assert spoken.count("and") == 1


def test_a_tag_after_punctuation_is_still_a_tag():
    """The guard is `\\w` only - punctuation and space still open a tag."""
    dropped = prep("Wait. [softly] come here.", engine_supports_tags=False)
    assert "softly" not in dropped
    assert "Wait" in dropped and "come here" in dropped
