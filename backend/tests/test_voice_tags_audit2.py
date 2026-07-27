"""Audit-2 - the stripper redesign, pinned.

The first predicate classified EVERY short bracketed span as a delivery tag
and quietly deleted citations, checkboxes, code subscripts and user asides -
in every role, voice on or off. This file pins the redesign three ways: the
demonstrated-corrupted corpus survives byte-for-byte, the two doors agree on
every chunking of hostile input, and stripping is confined to the one
population that can actually contain tags (assistant rows of a voice user).
"""
import random

import pytest

import voice_tags
from voice_tags import StreamStripper, strip_tags


class TestAudit2Corpus:
    """Every string the audit demonstrated being eaten, pinned as identity."""

    SURVIVORS = (
        "arr[0] += 1",
        "if x[i] > x[j]:\n    x[i], x[j] = x[j], x[i]",
        "The answer is 42 [1]. See [2] for details.",
        "- [ ] buy milk\n- [x] call mom",
        "`data[key]` holds the value",
        "[Google][1] is a search engine.\n\n[1]: https://google.com",
        "my code fails: total[idx] = vals[idx] * 2",
        "see [^1] for the footnote",
        "Please fill [YOUR NAME] here",
        "code `[x]`(y)",
        "[Reference]: https://example.com",
        "```\nresult[a] = b\n```",
    )

    def test_the_audit_corpus_survives_byte_for_byte(self):
        for text in self.SURVIVORS:
            got = strip_tags(text)
            assert got == text, "eaten: %r -> %r" % (text, got)

    def test_real_tags_still_go(self):
        assert strip_tags("[whisper] come closer") == "come closer"
        assert strip_tags("[cold, clipped tone] I asked once.") == "I asked once."
        assert strip_tags("[like sharing a secret] listen") == "listen"

    def test_the_documented_residue_is_the_shape_the_prompt_teaches(self):
        # Honesty check: lowercase prose asides DO match the taught shape.
        # This is the accepted residue for voice users - if this assertion
        # ever needs to change, the predicate design note changes with it.
        assert strip_tags("see the [attached] file") == "see the file"


class TestSpaceSeamGuard:
    def test_tag_flush_against_punctuation_keeps_the_only_space(self):
        """C5: 'world.[pause] Come' must not become 'world.Come'."""
        assert strip_tags("world.[pause] Come") == "world. Come"

    def test_tag_between_spaces_still_leaves_single_spacing(self):
        assert strip_tags("you. [pause] Come") == "you. Come"

    def test_leading_tag_swallows_its_space(self):
        assert strip_tags("[soft] hey") == "hey"

    def test_the_guard_holds_across_a_chunk_boundary(self):
        s = StreamStripper()
        out = s.feed("world.") + s.feed("[pause] Come") + s.flush()
        assert out == "world. Come"


class TestNestedAndCode:
    def test_a_nested_span_is_released_whole_with_no_stray_bracket(self):
        """C12: judging the span '[warm [soft]' as one tag leaked a bare ']'."""
        text = "x [warm [soft] y] z"
        assert strip_tags(text) == text

    def test_brackets_inside_inline_code_are_code(self):
        assert strip_tags("`m[soft]` rest") == "`m[soft]` rest"

    def test_brackets_inside_a_fence_are_code(self):
        text = "```py\nm = d[soft]\n```\nafter"
        assert strip_tags(text) == text

    def test_a_tag_after_balanced_code_is_still_a_tag(self):
        assert strip_tags("`x` [soft] hey") == "`x` hey"


class TestStreamStorageAgreement:
    def test_disqualified_span_resyncs_at_the_same_bracket_as_storage(self):
        """C4: after an oversize release the stream used to resume scanning
        immediately while storage skipped to the first ']' - a '[pause]' in
        that window was dropped by one door and kept by the other."""
        text = "[" + "x" * 60 + " [pause] tail] end [soft] hi"
        s = StreamStripper()
        streamed = ""
        for i in range(0, len(text), 7):
            streamed += s.feed(text[i:i + 7])
        streamed += s.flush()
        assert streamed == strip_tags(text)

    def test_fuzz_every_chunking_of_a_hostile_corpus(self):
        """The golden invariant, brute-forced: for hostile texts, EVERY
        two-split and a spread of random multi-splits agree with storage."""
        corpus = [
            "[soft] hey [pause] there",
            "a [" + "z" * 45 + "] b [warm] c",
            "x[i] and [1] and [ ] and [whisper] go",
            "line one\n[hesitant] line two",
            "`code[a]` [soft] prose",
            "tag at end [chuckle]",
            "[unclosed forever",
            "link [text](url) [soft] after",
            "[def]: url\n[gentle] hi",
            "world.[pause] Come",
            "x [warm [soft] y] z",
        ]
        rng = random.Random(42)
        for text in corpus:
            want = strip_tags(text)
            for cut in range(1, len(text)):
                s = StreamStripper()
                got = s.feed(text[:cut]) + s.feed(text[cut:]) + s.flush()
                assert got == want, "2-split@%d of %r: %r != %r" % (
                    cut, text, got, want)
            for _ in range(25):
                s = StreamStripper()
                got, i = "", 0
                while i < len(text):
                    step = rng.randint(1, 9)
                    got += s.feed(text[i:i + step])
                    i += step
                got += s.flush()
                assert got == want, "multisplit of %r" % text


class TestBrokenTail:
    def test_a_partial_ending_mid_tag_is_trimmed(self):
        """C14: the stream withheld '[sedu' - persisting it would show the
        user a broken bracket they never saw."""
        assert voice_tags.trim_broken_tail("I missed you. [sedu") == "I missed you."

    def test_a_partial_ending_in_prose_is_untouched(self):
        assert voice_tags.trim_broken_tail("see arr[0") == "see arr[0"
        assert voice_tags.trim_broken_tail("plain text") == "plain text"
        assert voice_tags.trim_broken_tail("") == ""

    def test_a_closed_tag_at_the_end_is_not_trimmed(self):
        # Closed spans are the strippers' business, not the trimmer's.
        assert voice_tags.trim_broken_tail("hey [soft]") == "hey [soft]"


class TestWhoGetsStripped:
    def test_user_text_is_never_stripped(self):
        """The worst finding: a user's own '[sic]' was eaten in display and
        the edit round-trip persisted the corruption."""
        voice_tags.mark_voice_ever_enabled()
        text = "my note [sic] and my code x[i]"
        assert voice_tags.strip_for_display(text, "user") == text

    def test_assistant_text_is_stripped_only_after_voice_was_ever_on(self, monkeypatch):
        monkeypatch.setattr(voice_tags, "stripping_active", lambda: False)
        assert voice_tags.strip_for_display("[soft] hi", "assistant") == "[soft] hi"
        monkeypatch.setattr(voice_tags, "stripping_active", lambda: True)
        assert voice_tags.strip_for_display("[soft] hi", "assistant") == "hi"

    def test_the_flag_is_sticky_in_ram_once_marked(self):
        voice_tags.reset_stripping_cache()
        voice_tags.mark_voice_ever_enabled()
        assert voice_tags.stripping_active() is True
