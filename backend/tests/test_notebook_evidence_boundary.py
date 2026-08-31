"""U-20 - a quote has to come from ONE message.

`_fold` ends with `" ".join(text.split())`, so folding the chunk as a single
string destroys its line breaks - and the chunk is built one line per TURN.
The end of what one person said and the beginning of what the other said
became adjacent words in one blob, and the evidence gate is a substring test
over that blob. So a span that crosses a message boundary passed as a
"verbatim quote" of something nobody ever said.

`_speaker_of` next door has always walked the lines separately. It was
already answering "I cannot place this" for exactly those spans - and the row
was written anyway, with `evidence_role` set to None.
"""
from __future__ import annotations

import json

import notebook_extract


CHUNK = "\n".join([
    "user: her brother owns the mill",
    "assistant: the ferry runs at dawn",
])


def reply(*facts: dict) -> dict:
    return {
        "choices": [{
            "finish_reason": "stop",
            "message": {"content": json.dumps({"facts": list(facts)})},
        }],
    }


def fact(evidence: str, text: str = "Something true.") -> dict:
    return {
        "text": text,
        "evidence": evidence,
        "kind": "fact",
        "durability": "permanent",
        "importance": 2,
        "supersedes": None,
    }


class TestAQuoteThatCrossesAMessageBoundary:
    def test_it_is_refused(self) -> None:
        # The tail of one turn and the head of the next. Folded as one
        # string, these words really are adjacent; as two lines they are not
        # a quotation of anything.
        straddling = "owns the mill assistant: the ferry"

        kept, dropped = notebook_extract.parse_reply(
            reply(fact(straddling)), CHUNK, [])

        assert kept == []
        assert dropped["ungrounded"] == 1

    def test_a_quote_inside_one_message_is_kept(self) -> None:
        """GROUND CONTROL. Without it the fix is satisfied by a gate that
        refuses every quote, which would empty the feature silently."""
        kept, dropped = notebook_extract.parse_reply(
            reply(fact("her brother owns the mill")), CHUNK, [])

        assert len(kept) == 1
        assert dropped.get("ungrounded", 0) == 0
        assert kept[0]["evidence_role"] == "user"

    def test_a_quote_from_the_other_message_is_kept_too(self) -> None:
        kept, _ = notebook_extract.parse_reply(
            reply(fact("the ferry runs at dawn")), CHUNK, [])

        assert len(kept) == 1
        assert kept[0]["evidence_role"] == "assistant"


class TestTheFoldIsStillTheFold:
    def test_whitespace_inside_one_line_is_still_forgiven(self) -> None:
        """The reason `_fold` exists at all. A model that re-wraps a quote,
        or doubles a space, is still quoting - the check is about the words,
        not about the spacing."""
        kept, _ = notebook_extract.parse_reply(
            reply(fact("her   brother\nowns  the mill")), CHUNK, [])

        assert len(kept) == 1

    def test_a_quote_that_is_simply_absent_is_still_refused(self) -> None:
        kept, dropped = notebook_extract.parse_reply(
            reply(fact("her sister runs the bakery")), CHUNK, [])

        assert kept == []
        assert dropped["ungrounded"] == 1


class TestAChunkThatNamesNobody:
    """The case only the speaker check can catch, and the unit it works in.

    A chunk is one `role: content` entry per turn - but a message body has
    newlines of its own, so a physical line is NOT a message. Reading the
    speaker off physical lines refuses every paragraph after the first of a
    long reply, and hands back "note" as the speaker for a continuation line
    that happens to read "Note: he left". Both were live once this gate
    started refusing, which is why the segmentation is by role vocabulary
    (`_ROLES`, from the CHECK constraint on `messages.role`).

    What genuinely names nobody is a chunk with no speaker anywhere. That is
    not a shape this application produces, and it is refused rather than
    guessed at.
    """

    NO_ROLE = "she poured the tea and said her brother owns the mill"

    def test_evidence_from_it_is_refused(self) -> None:
        # GROUND for the branch: the quote really is present in the chunk, so
        # the substring test is satisfied and the ONLY thing left to refuse
        # it is the missing speaker. Without this the test could be passing
        # through the boundary branch and nobody would know - which is what
        # an earlier version of this class did.
        assert "her brother owns the mill" in self.NO_ROLE
        assert notebook_extract._messages(self.NO_ROLE) == [
            (None, self.NO_ROLE)]

        kept, dropped = notebook_extract.parse_reply(
            reply(fact("her brother owns the mill")), self.NO_ROLE, [])

        assert kept == []
        assert dropped["ungrounded"] == 1

    def test_a_second_paragraph_still_belongs_to_its_speaker(self) -> None:
        """THE ground control, and the regression this class exists to stop.

        Two paragraphs of one assistant turn. Under a physical-line reading
        the second one has no prefix, so a true quote from it was refused and
        the feature went quiet on exactly the long replies it is for.
        """
        chunk = "\n".join([
            "user: tell me about the mill",
            "assistant: it stood by the water.",
            "her brother owns it now",
        ])

        kept, _ = notebook_extract.parse_reply(
            reply(fact("her brother owns it now")), chunk, [])

        assert len(kept) == 1
        assert kept[0]["evidence_role"] == "assistant"

    def test_a_continuation_line_is_not_read_as_a_speaker(self) -> None:
        """The other half of the same mistake. `partition(":")` on a physical
        line turns any colon into a role, so a paragraph beginning "Note:"
        used to be attributed to a speaker called "note"."""
        chunk = "\n".join([
            "assistant: it stood by the water.",
            "Note: her brother owns it now",
        ])

        kept, _ = notebook_extract.parse_reply(
            reply(fact("her brother owns it now")), chunk, [])

        assert len(kept) == 1
        assert kept[0]["evidence_role"] == "assistant"

    def test_every_kept_note_names_a_speaker(self) -> None:
        """The invariant, stated once: nothing survives with None."""
        kept, _ = notebook_extract.parse_reply(
            reply(fact("her brother owns the mill"),
                  fact("the ferry runs at dawn", "Another thing.")),
            CHUNK, [])

        assert len(kept) == 2
        for note in kept:
            assert note["evidence_role"] in {"user", "assistant"}


class TestTheStoredQuoteIsBounded:
    """U-21 - the ceiling was the provider's to keep, and only theirs.

    `evidence` had `maxLength: 240` in the JSON schema and nothing anywhere
    else: `parse_reply` checked presence and verbatim-ness, `_collapse`
    folded whitespace without shortening, and `commit_extraction` wrote what
    arrived. A provider that does not honour `strict`, or answers outside the
    schema at all, met no ceiling on this side - and this is the field that
    carries somebody's own sentence word for word.
    """

    def test_a_quote_longer_than_the_ceiling_is_stored_trimmed(self) -> None:
        long_line = "user: " + ("word " * 200).strip()
        quote = long_line[len("user: "):]
        assert len(quote) > notebook_extract.EVIDENCE_MAX_CHARS, (
            "ground: the fixture is actually over the ceiling")

        kept, dropped = notebook_extract.parse_reply(
            reply(fact(quote)), long_line, [])

        # POSITIVE CONTROL for the branch actually taken. Trimming and
        # refusing are both defensible answers to an over-long quote; the
        # code trims, so the note survives and NOTHING is counted as dropped.
        # Reading only the length would be green under either.
        assert len(kept) == 1, "an over-long quote is trimmed, not dropped"
        assert sum(dropped.values()) == 0
        assert len(kept[0]["evidence"]) == notebook_extract.EVIDENCE_MAX_CHARS

    def test_the_verbatim_check_still_sees_the_whole_quote(self) -> None:
        """The ORDER, and the one case that tells the two orders apart.

        Trimming first would make the gate ask whether a SHORTENED string
        appears in the transcript - a weaker question with a real answer
        attached to it. Here the model quotes a long real sentence and then
        appends something nobody said. The first 240 characters are genuine,
        so a gate reading the trimmed string is satisfied and the invention
        is written down under the trimmed quote's authority. A gate reading
        what the model actually sent refuses the whole thing.
        """
        real = ("alpha " * 60).strip()
        long_line = "user: " + real
        assert len(real) > notebook_extract.EVIDENCE_MAX_CHARS, "ground"

        invented = real + " zulu-never-said"
        # The prefix a trim-first gate would be asked about IS in the source.
        assert notebook_extract._fold(
            invented[:notebook_extract.EVIDENCE_MAX_CHARS]) in             notebook_extract._fold(long_line), "ground: the trap is armed"

        kept, dropped = notebook_extract.parse_reply(
            reply(fact(invented)), long_line, [])

        assert kept == [], "invented text rode in on a real quote's prefix"
        assert dropped["ungrounded"] == 1

    def test_an_ordinary_quote_is_untouched(self) -> None:
        """GROUND CONTROL: the ceiling must not shorten what already fits."""
        kept, _ = notebook_extract.parse_reply(
            reply(fact("her brother owns the mill")), CHUNK, [])

        assert kept[0]["evidence"] == "her brother owns the mill"

    def test_a_quote_copied_WITH_its_speaker_prefix_is_kept(self) -> None:
        """The false refusal message-segmentation introduced.

        The model is shown the transcript as `user: ...` and told to copy
        evidence VERBATIM. A model that copies the whole printed line is
        obeying - and for a while it was refused as an invented quote, which
        is not a visible failure: the run lands empty, the cursor moves, and
        that range is never read again.
        """
        line = "user: her brother owns the mill"
        kept, dropped = notebook_extract.parse_reply(
            reply(fact(line)), CHUNK, [])

        assert len(kept) == 1, dropped
        assert kept[0]["evidence"] == line
        assert kept[0]["evidence_role"] == "user"


class TestAMessageBodyCannotForgeABoundary:
    """Q-26 - the separator was never escaped.

    The chunk is `f"{role}: {content}"` per turn joined with newlines, and
    the gate used to split it back apart by looking for the role vocabulary
    at the start of a line. A message BODY can contain such a line: a pasted
    transcript, a YAML file, a code block. So an assistant reply carrying a
    line that begins `user: ` was read as a new USER message, and the words
    the model wrote were attributed to the person - which also silences the
    panel's "taken from the model's own reply" mark, on exactly the note
    class that mark exists for.

    The planner has the rows and never lost the distinction. It passes the
    messages down in pieces now instead of letting the gate rebuild them.
    """

    BODY = chr(10).join(["here it is:", "user: root", "and that is all"])
    CHUNK = chr(10).join(["user: show me the compose file",
                          "assistant: " + BODY])
    SPANS = [("user", "show me the compose file"), ("assistant", BODY)]

    def test_the_quote_keeps_the_speaker_who_really_said_it(self) -> None:
        kept, _ = notebook_extract.parse_reply(
            reply(fact("root")), self.CHUNK, [], spans=self.SPANS)

        assert len(kept) == 1
        assert kept[0]["evidence_role"] == "assistant", (
            "the model's own line was handed back as the user's")

    def test_rebuilding_from_text_is_what_gets_it_wrong(self) -> None:
        """The control that makes the test above mean something.

        Same reply, same chunk, no spans - the old path. It answers `user`,
        which is the defect. If this ever starts answering `assistant`, the
        text parser has been fixed and the spans are no longer load-bearing;
        somebody should find out which before deleting either.
        """
        kept, _ = notebook_extract.parse_reply(
            reply(fact("root")), self.CHUNK, [])

        assert kept[0]["evidence_role"] == "user"

    def test_a_quote_still_has_to_be_in_a_message(self) -> None:
        """GROUND. Carrying the spans must not turn the gate off: a span set
        is a stricter source than the text, never a looser one."""
        kept, dropped = notebook_extract.parse_reply(
            reply(fact("nobody said this")), self.CHUNK, [], spans=self.SPANS)

        assert kept == []
        assert dropped["ungrounded"] == 1

    def test_a_span_crossing_two_messages_is_still_refused(self) -> None:
        """POSITIVE CONTROL for the boundary itself, now that the boundary
        comes from the caller rather than from the text."""
        straddling = "show me the compose file assistant: here it is:"

        kept, dropped = notebook_extract.parse_reply(
            reply(fact(straddling)), self.CHUNK, [], spans=self.SPANS)

        assert kept == []
        assert dropped["ungrounded"] == 1

    def test_without_spans_it_falls_back_to_the_text(self) -> None:
        """The fallback is not dead: every caller without the rows, and every
        test in this file, still drives the text path."""
        kept, _ = notebook_extract.parse_reply(
            reply(fact("her brother owns the mill")), CHUNK, [])

        assert len(kept) == 1
        assert kept[0]["evidence_role"] == "user"

