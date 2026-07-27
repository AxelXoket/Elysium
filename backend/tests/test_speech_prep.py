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
    out = prep("Use `a * b` here.", narrative="skip")
    assert "a * b" in out or "a  b" in out
    assert "here" in out


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


def test_a_bracket_that_is_not_a_tag_is_left_alone():
    # Digits disqualify it as a delivery tag - this is a citation.
    out = prep("As shown [1] earlier.", engine_supports_tags=True)
    assert "earlier" in out


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


def test_bold_is_not_narrative():
    out = prep("This is **really** important.", narrative="skip")
    assert "really" in out and "*" not in out


def test_arithmetic_asterisk_is_left_alone():
    # parseMessage's OPEN guard: a letter/digit before the run kills it.
    out = prep("The answer is 5*3*2 exactly.", narrative="skip")
    assert "5" in out and "exactly" in out


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
