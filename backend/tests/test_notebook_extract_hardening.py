"""FAZ 4 audit round - six ways the extractor lost or crashed on real input.

Each class here is a defect that was found by reading, reproduced, and then
fixed. None of them could have been caught by the tests that already existed:
the file's "Turkish" fixtures were pure ASCII, without one diacritic, and the
work-key parametrisation omitted the language in exactly the way the key did.
"""
from __future__ import annotations

import unicodedata

import pytest

import notebook_extract as ex

from tests.test_notebook_extract import CHUNK, fact, reply


class TestTheThingsThatLookIdenticalAndAreNot:
    """The grounding check is a verbatim substring test, and for a Turkish
    transcript that is a minefield. Every case here loses a TRUE fact and
    reports it as an invented quote - the defence firing on exactly what it
    was built to protect.
    """

    def test_a_real_turkish_quote_survives(self) -> None:
        chunk = "Nisha'nin babasi degirmeni isletiyordu, dedi kiz."
        kept, _ = ex.parse_reply(
            reply([fact(evidence="babasi degirmeni isletiyordu")]), chunk, [])
        assert len(kept) == 1

    def test_diacritics_survive(self) -> None:
        chunk = "Kızın babası değirmeni işletiyordu."
        kept, _ = ex.parse_reply(
            reply([fact(evidence="babası değirmeni "
                                 "işletiyordu")]), chunk, [])
        assert len(kept) == 1, "a fact with real Turkish letters was dropped"

    def test_a_decomposed_quote_matches_a_composed_source(self) -> None:
        """Windows types NFC; a model may emit the NFD pair. Byte-compared
        they differ; on screen they are the same word."""
        chunk = "Kizin babasi değirmeni isletiyordu."
        quote = unicodedata.normalize("NFD", "babasi değirmeni")
        assert quote not in chunk, "the fixture must be the hard case"
        kept, _ = ex.parse_reply(reply([fact(evidence=quote)]), chunk, [])
        assert len(kept) == 1

    def test_a_curly_apostrophe_matches_a_straight_one(self) -> None:
        chunk = "Nisha’nin babasi degirmeni isletiyordu."
        kept, _ = ex.parse_reply(
            reply([fact(evidence="Nisha'nin babasi")]), chunk, [])
        assert len(kept) == 1

    def test_a_non_breaking_space_matches_a_space(self) -> None:
        chunk = "Kizin babasi degirmeni isletiyordu."
        kept, _ = ex.parse_reply(
            reply([fact(evidence="Kizin babasi")]), chunk, [])
        assert len(kept) == 1

    def test_an_invented_quote_is_STILL_dropped(self) -> None:
        """The control for all five above: normalisation must not turn the
        grounding check into a check that passes everything."""
        chunk = "Kizin babasi değirmeni isletiyordu."
        kept, _ = ex.parse_reply(
            reply([fact(evidence="babasi firini isletiyordu")]), chunk, [])
        assert kept == []

    def test_case_is_not_folded(self) -> None:
        """Deliberate. Turkish dotted/dotless I makes casefold locale-
        dependent, and a check that occasionally misses beats one that
        silently equates two different Turkish words."""
        kept, _ = ex.parse_reply(reply([fact(evidence="KIZIN BABASI")]),
                                 "kizin babasi", [])
        assert kept == []


class TestEverySpellingOfCutOff:
    """`finish_reason == "length"` was one provider's vocabulary away from the
    exact bug this module exists to prevent. With a strict schema a
    constrained decoder closes the array cleanly on token exhaustion, so a
    truncated answer arrives as VALID, SHORT JSON - and this string is the
    only thing separating it from "nothing found".
    """

    @pytest.mark.parametrize("word", ["length", "MAX_TOKENS", "max_tokens"])
    def test_the_normalised_field(self, word) -> None:
        with pytest.raises(ex.ExtractionFailed) as exc:
            ex.parse_reply(reply([], finish=word), CHUNK, [])
        assert "truncated" in str(exc.value)

    def test_the_native_field_is_read_too(self) -> None:
        """OpenRouter normalises `finish_reason` and relays the upstream word
        in `native_finish_reason`. Reading only the first trusts a
        normalisation this repo has no test for."""
        body = reply([])
        body["choices"][0]["native_finish_reason"] = "MAX_TOKENS"
        with pytest.raises(ex.ExtractionFailed):
            ex.parse_reply(body, CHUNK, [])

    def test_a_clean_stop_is_still_a_clean_stop(self) -> None:
        assert ex.parse_reply(reply([]), CHUNK, [])[0] == []

    def test_a_refusal_says_refused_not_empty(self) -> None:
        """A provider that explicitly declined, reported as "the content was
        empty", sends somebody debugging a schema nobody objected to."""
        body = reply([])
        body["choices"][0]["message"] = {"content": None, "refusal": "no"}
        with pytest.raises(ex.ExtractionFailed) as exc:
            ex.parse_reply(body, CHUNK, [])
        assert "refused" in str(exc.value)


class TestAnUntrustedReplyCannotCrash:
    """Everything here reached `parsed["facts"]` as a TypeError - an exception
    no caller catches, which turned a BILLED call into a bare 500 that never
    reported what it cost."""

    @pytest.mark.parametrize("body", [
        '"facts are unavailable"',   # `in` is a SUBSTRING test on a str
        '["facts"]',                 # and a membership test on a list
        "5", "null", "true",
    ])
    def test_a_non_object_body_is_a_named_failure(self, body) -> None:
        with pytest.raises(ex.ExtractionFailed):
            ex.parse_reply(reply([], raw=body), CHUNK, [])


class TestWhyEachOneWasDropped:
    """One integer cannot tell "a quote was invented" - the defence working -
    from "a Turkish quote failed a byte comparison" - the defence eating a
    true fact. Those call for opposite responses."""

    def test_an_invented_quote_is_counted_as_ungrounded(self) -> None:
        _, dropped = ex.parse_reply(
            reply([fact(evidence="not in the text at all")]), CHUNK, [])
        assert dropped == {"ungrounded": 1}

    def test_an_off_enum_answer_is_counted_separately(self) -> None:
        _, dropped = ex.parse_reply(reply([fact(kind="invented")]), CHUNK, [])
        assert dropped == {"off_schema": 1}

    def test_ignoring_the_item_cap_is_counted_as_a_schema_violation(self):
        """maxItems is 6. More than that is the model ignoring the schema, and
        it must not hide inside the same number as a caught hallucination."""
        _, dropped = ex.parse_reply(reply([fact()] * 9), CHUNK, [])
        assert dropped["over_cap"] == 3

    def test_a_clean_reply_drops_nothing(self) -> None:
        _, dropped = ex.parse_reply(reply([fact()]), CHUNK, [])
        assert dropped == {}


class TestATextThatHonouredTheSchemaIsNotDroppedForLength:
    def test_a_full_length_text_with_a_line_break_survives(self) -> None:
        """notebook_store._flat joins lines with " / ", adding two characters
        per break - so a fact at exactly the schema's 240-character cap came
        out at 242 and was dropped for being too long. A model that fully
        honoured the schema still lost the fact."""
        text = ("x" * 200) + "\n" + ("y" * 39)
        assert len(text) == ex.notebook_store.ENTRY_MAX_CHARS
        kept, dropped = ex.parse_reply(
            reply([fact(text=text, evidence="her brother owns the mill")]),
            CHUNK, [])
        assert len(kept) == 1, dropped
        assert "\n" not in kept[0]["text"]

    def test_a_genuinely_over_length_text_is_still_refused(self) -> None:
        kept, dropped = ex.parse_reply(reply([fact(text="x" * 400)]),
                                       CHUNK, [])
        assert kept == [] and dropped["too_long"] == 1


class TestThePromptCannotGrowWithoutBound:
    """config.py's note on the daily cap says it plainly: the largest
    documented runaway in this space was not a loop, it was a context that
    grew every call. EXISTING_NOTES is that context - every accepted note
    joins every future prompt, forever - and nothing capped how many notes a
    chat may hold, how long a card may be, or how long a message may be.
    """

    def test_a_thousand_notes_are_SHORTENED_not_dropped(self) -> None:
        """Dropping renumbers, and the numbers are indices the caller resolves
        against the full list - so a dropped note shifted every later index
        and retired the wrong row. Shortening keeps every index in place."""
        msg = ex.build_user_message(card="", existing=["n" * 240] * 1000,
                                    recent=[], new=["x"])
        assert len(msg) < ex.EXISTING_MAX_CHARS + 2000
        # Every index still there, first and last.
        assert "\n0. " in msg and "\n999. " in msg

    def test_the_worst_case_prompt_is_bounded(self) -> None:
        msg = ex.build_user_message(
            card="c" * 100_000, existing=["n" * 300] * 400,
            recent=["r" * 5000] * 20, new=["x" * 4000] * 20)
        ceiling = (ex.CARD_MAX_CHARS + ex.EXISTING_MAX_CHARS
                   + ex.TURNS_MAX_CHARS * 2 + 2000)
        assert len(msg) < ceiling

    def test_the_NEWEST_turns_are_the_ones_kept(self) -> None:
        """Dropping the tail would silently skip the newest messages while
        reporting the whole range as processed."""
        turns = [f"turn {i}: " + "x" * 3000 for i in range(20)]
        msg = ex.build_user_message(card="", existing=[], recent=[], new=turns)
        assert "turn 19:" in msg
        assert "turn 0:" not in msg

    def test_whole_lines_only(self) -> None:
        """Half a turn attributed to a speaker is worse than a missing one:
        the model would credit the wrong person for it."""
        turns = [f"user: {i} " + "x" * 4000 for i in range(10)]
        msg = ex.build_user_message(card="", existing=[], recent=[], new=turns)
        body = msg.split("<NEW_TURNS ")[1]
        for line in body.splitlines():
            if line.startswith("user:"):
                assert len(line) == len(turns[0])

    def test_a_normal_conversation_is_not_trimmed_at_all(self) -> None:
        """Positive control: a ceiling that trims ordinary use is a bug, not
        a safeguard."""
        existing = [f"note {i}" for i in range(30)]
        new = [f"user: message {i}" for i in range(8)]
        msg = ex.build_user_message(card="a card", existing=existing,
                                    recent=["earlier"], new=new)
        assert "29. note 29" in msg
        assert all(n in msg for n in new)
