"""Audit KÖK 5: cutters that delete silently or cut in the wrong place.

speech_prep's module header says "if you are not sure, do nothing". Five
places did the opposite. Every case below is one the audit measured on real
text, written as the output a listener would actually have heard.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import speech_prep
from speech_prep import PrepOptions, first_chunk, prepare, sentences


WINDOW = {"min_chars": 40, "max_chars": 100}


def _head(text: str) -> str | None:
    cut = first_chunk(text, **WINDOW)
    return None if cut is None else cut[0]


# ---------------------------------------------------------------------------
# 1. first_chunk had NONE of _split's four guards
# ---------------------------------------------------------------------------

def test_a_decimal_is_not_a_sentence_end():
    """Measured: "3.5 million" came out as "three." then "five million"."""
    text = ("The programme was eventually costed at 3.5 million pounds, which "
            "nobody in the room had expected to hear said out loud.")
    head = _head(text)
    assert head is None or not head.rstrip().endswith("3.")


def test_an_ambiguous_abbreviation_keeps_its_sentence():
    """Measured: "Dr. Smith" came out as "Doctor" then "Smith"."""
    text = ("She had been waiting for Dr. Smith since the middle of the "
            "afternoon and the corridor had not grown any warmer.")
    head = _head(text)
    assert head is None or not head.rstrip().endswith("Dr.")


def test_a_url_is_never_cut_in_half():
    """A cut URL stops matching _BARE_URL, so the stripper that exists to
    keep addresses out of the audio no longer sees it - and the listener gets
    it read out character by character."""
    text = ("The whole thing is documented at https://example.com/a.b.c/page "
            "if you want to read the original notes yourself.")
    cut = first_chunk(text, **WINDOW)
    if cut is not None:
        head, tail = cut
        for match in speech_prep._BARE_URL.finditer(text):
            assert not (match.start() < len(head) < match.end()), \
                f"cut inside the URL: {head!r}"


def test_a_terminal_glued_to_the_next_word_is_not_a_seam():
    text = ("The file was called notes.final and nobody could remember which "
            "of the three versions that actually was any more.")
    head = _head(text)
    assert head is None or not head.rstrip().endswith("notes.")


def test_a_delivery_tag_is_never_split_at_its_comma():
    """[cold, clipped tone] contains a comma, which ranked as a legal seam.
    voice_tags treats an UNCLOSED tag as plain text, so BOTH halves were
    spoken aloud as words."""
    text = ("She put the cup down without looking up at him [cold, clipped "
            "tone] and asked the question again exactly as before.")
    head = _head(text)
    if head is not None:
        assert head.count("[") == head.count("]"), f"tag split: {head!r}"


def test_a_thousands_separator_is_not_a_seam():
    text = ("The final count came to 1,000 signatures which was rather more "
            "than the committee had prepared itself for that morning.")
    head = _head(text)
    assert head is None or not head.rstrip().endswith("1,")


def test_a_good_seam_is_still_taken():
    """The guards must refuse bad cuts without refusing every cut - otherwise
    the whole first-chunk feature is off again, just more quietly."""
    text = ("She closed the door behind her. The corridor was colder than the "
            "room had been, and much longer than she remembered it.")
    head = _head(text)
    assert head is not None, "no cut at all on obviously cuttable text"
    # Any ranked break character, not specifically a full stop: the period
    # here sits at index 30, below min_chars, so the comma at 79 is the
    # earliest legal seam and taking it is correct.
    assert head[-1] in ".!?;:,-", repr(head)
    assert 40 <= len(head) <= 100


# ---------------------------------------------------------------------------
# 2. one unbalanced quote used to swallow the rest of the reply
# ---------------------------------------------------------------------------

def test_a_stray_inch_mark_does_not_weld_the_reply_together():
    """`24"` is a measurement, not an open quote. While depth_quote stayed
    true every terminal was suppressed, so the remainder became ONE utterance
    - which then hits the worker's max_new_tokens ceiling and the audio is
    cut off mid-word."""
    text = ('The shelf is 24" across. That is wider than the alcove.\n\n'
            'She measured it twice. Then she measured it again.')
    out = sentences(text)
    assert len(out) > 1, f"the whole reply became one chunk: {out!r}"


def test_a_blank_line_force_closes_an_open_quote():
    """Exactly what _scan_emphasis already does for an open `*` span."""
    text = 'He said "come back later\n\nShe did not come back. Not that day.'
    out = sentences(text)
    assert len(out) > 1, f"still one chunk: {out!r}"


def test_a_balanced_quote_is_still_held_together():
    text = '"Come back later," he said. She did not.'
    out = sentences(text)
    assert any('"' in s for s in out)


# ---------------------------------------------------------------------------
# 3. _MD_LINK deleted stage directions
# ---------------------------------------------------------------------------

def test_a_parenthetical_after_a_bracket_is_not_a_link():
    """Measured: "[Anna](whispering) You never listen." became "Anna You
    never listen." - the direction gone from the audio while it is still on
    screen. _REF_DEF was hardened for this exact class; _MD_LINK was not."""
    out = prepare("[Anna](whispering) You never listen.", PrepOptions())
    assert "whispering" in out, out


def test_a_real_link_still_says_only_its_label():
    out = prepare("See [the notes](https://example.com/notes) for more.",
                  PrepOptions())
    assert "the notes" in out
    assert "example.com" not in out
    assert "https" not in out


def test_a_relative_path_link_is_still_a_link():
    out = prepare("Open [the file](./notes.txt) when you get a moment.",
                  PrepOptions())
    assert "the file" in out
    assert "notes.txt" not in out


# ---------------------------------------------------------------------------
# 4. a remainder of just the re-opened span marker
# ---------------------------------------------------------------------------

def test_a_lone_reopened_marker_is_not_handed_on_as_speech():
    """prepare("*") returns "*", which is truthy, so the `if not spoken`
    guard never fired and a full engine call - measured fixed cost 0.89 to
    1.43 s - was spent saying nothing at all."""
    out = sentences("*She turned away from the window.*")
    assert all(s.strip("*").strip() for s in out), \
        f"a marker-only chunk survived: {out!r}"


def test_an_open_span_still_survives_a_growing_stream():
    """The prefix is bookkeeping the NEXT delta needs; only a final flush may
    drop it."""
    done, rest = speech_prep._split("*She turned away. And then", require_complete=True)
    assert rest.startswith("*"), rest


# ---------------------------------------------------------------------------
# 5. a pronunciation entry could delete a delivery tag
# ---------------------------------------------------------------------------

def test_a_pronunciation_entry_cannot_corrupt_a_masked_tag():
    """_apply_pronunciations runs at step 7, when tags are already
    `\\x01t<letters>\\x01` markers. \\x01 is not a word character, so the marker
    BODY matched the same boundaries a real word does - and _unmask_tags
    answers an index it never issued with "", deleting the tag."""
    table = {key: "REPLACED" for key in ("ta", "tb", "tc", "td", "te")}
    opts = PrepOptions(engine_supports_tags=True, pronunciations=table)
    out = prepare("[whispering] Come here. [louder] Now.", opts)
    assert "REPLACED" not in out, out
    assert "whispering" in out and "louder" in out, out


def test_a_pronunciation_entry_still_applies_to_ordinary_words():
    opts = PrepOptions(pronunciations={"Aoife": "EE-fa"})
    assert "EE-fa" in prepare("Aoife came back.", opts)


# ---------------------------------------------------------------------------
# 6. P10: no em dash in backend source
# ---------------------------------------------------------------------------

def test_no_backend_source_file_contains_an_em_dash():
    """Ledger item 111 records P10 as "verified (test enforces)". The only
    guard in the repo was frontend/src/test/settings-copy.test.ts, which scans
    frontend settings copy - so no Python file was checked by anything, and a
    literal U+2014 sat in speech_prep._CHUNK_BREAKS, the module the rule is
    most about."""
    # Written as an escape, or this file would be its own first offender.
    em_dash = chr(0x2014)
    root = Path(".").resolve()
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        parts = set(path.parts)
        if ".venv" in parts or "__pycache__" in parts or "node_modules" in parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), 1):
            if em_dash in line:
                offenders.append(f"{path.relative_to(root)}:{number}")
    assert not offenders, "em dash in: " + ", ".join(offenders[:10])
