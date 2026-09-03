"""FAZ 4 audit round - six ways the extractor lost or crashed on real input.

Each class here is a defect that was found by reading, reproduced, and then
fixed. None of them could have been caught by the tests that already existed:
the file's "Turkish" fixtures were pure ASCII, without one diacritic, and the
work-key parametrisation omitted the language in exactly the way the key did.

Every chunk here carries its `role: ` prefix, because that is what a chunk
is: `notebook_worker` builds one `f"{role}: {content}"` per turn and the
grounding check now reads the speaker off it. A bare sentence would be
refused for having no speaker. MEASURED, because the first version of this
note guessed and guessed wrong: replaying every fixture in its pre-prefix
shape turns SIX POSITIVE tests red (the ones asserting a true quote
survives) and leaves the negative ones exactly where they were - they take
the boundary branch with or without a prefix, because their evidence really
is absent from the text. The prefix keeps the ground controls alive; it was
never the negatives that were at risk.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

import pytest

import config
import notebook_extract as ex

from tests.test_notebook_extract import CHUNK, fact, reply


def section(msg: str, name: str) -> str:
    """The body of one fenced section of an assembled prompt.

    The fences carry a random tag, so the section cannot be found by a fixed
    label - which is the whole point of the tag. Matched by its OWN opening
    and closing pair.
    """
    found = re.search(rf"<{name} (#[0-9a-f]{{16}})>\n(.*?)\n</{name} \1>",
                      msg, re.DOTALL)
    assert found, f"{name} is not in the message at all"
    return found.group(2)


class TestTheThingsThatLookIdenticalAndAreNot:
    """The grounding check is a verbatim substring test, and for a Turkish
    transcript that is a minefield. Every case here loses a TRUE fact and
    reports it as an invented quote - the defence firing on exactly what it
    was built to protect.
    """

    def test_a_real_turkish_quote_survives(self) -> None:
        chunk = "user: Nisha'nin babasi degirmeni isletiyordu, dedi kiz."
        kept, _ = ex.parse_reply(
            reply([fact(evidence="babasi degirmeni isletiyordu")]), chunk, [])
        assert len(kept) == 1

    def test_diacritics_survive(self) -> None:
        chunk = "user: Kızın babası değirmeni işletiyordu."
        kept, _ = ex.parse_reply(
            reply([fact(evidence="babası değirmeni "
                                 "işletiyordu")]), chunk, [])
        assert len(kept) == 1, "a fact with real Turkish letters was dropped"

    def test_a_decomposed_quote_matches_a_composed_source(self) -> None:
        """Windows types NFC; a model may emit the NFD pair. Byte-compared
        they differ; on screen they are the same word."""
        chunk = "user: Kizin babasi değirmeni isletiyordu."
        quote = unicodedata.normalize("NFD", "babasi değirmeni")
        assert quote not in chunk, "the fixture must be the hard case"
        kept, _ = ex.parse_reply(reply([fact(evidence=quote)]), chunk, [])
        assert len(kept) == 1

    def test_a_curly_apostrophe_matches_a_straight_one(self) -> None:
        chunk = "user: Nisha’nin babasi degirmeni isletiyordu."
        kept, _ = ex.parse_reply(
            reply([fact(evidence="Nisha'nin babasi")]), chunk, [])
        assert len(kept) == 1

    def test_a_non_breaking_space_matches_a_space(self) -> None:
        chunk = "user: Kizin babasi degirmeni isletiyordu."
        kept, _ = ex.parse_reply(
            reply([fact(evidence="Kizin babasi")]), chunk, [])
        assert len(kept) == 1

    def test_an_invented_quote_is_STILL_dropped(self) -> None:
        """The control for all five above: normalisation must not turn the
        grounding check into a check that passes everything."""
        chunk = "user: Kizin babasi değirmeni isletiyordu."
        kept, _ = ex.parse_reply(
            reply([fact(evidence="babasi firini isletiyordu")]), chunk, [])
        assert kept == []

    def test_case_is_not_folded(self) -> None:
        """Deliberate. Turkish dotted/dotless I makes casefold locale-
        dependent, and a check that occasionally misses beats one that
        silently equates two different Turkish words."""
        kept, _ = ex.parse_reply(reply([fact(evidence="KIZIN BABASI")]),
                                 "user: kizin babasi", [])
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
        over = 9 - ex.MAX_FACTS
        _, dropped = ex.parse_reply(reply([fact()] * 9), CHUNK, [])
        assert dropped["over_cap"] == over

    def test_a_clean_reply_drops_nothing(self) -> None:
        _, dropped = ex.parse_reply(reply([fact()]), CHUNK, [])
        assert dropped == {}


class TestTheNoteHasToComeFromItsQuote:
    """`evidence` was gated and `text` never was, and `text` is the field that
    gets stored, injected into every later prompt, and fed back in as
    EXISTING_NOTES. A fact could carry a true, verbatim, correctly attributed
    quote and a sentence about something else entirely.

    Constrained decoding is why a second gate is needed at all: with a strict
    schema every reply is schema-valid, so parseability stopped being a health
    signal. And the prompt makes the two fields cross-lingual on purpose -
    Turkish quote, English note - so no word-for-word comparison is available.
    What survives translation is what this gate uses: names, places, numbers.
    """

    # One Turkish message, with the names where names normally sit - inside
    # the sentence rather than opening it. TWO of them, deliberately: a quote
    # naming one person and one place, next to a one-sentence note that can
    # only mention one of them, is what tells "at least one anchor" apart from
    # "every anchor" - and the second of those would drop the ground control
    # below.
    CHUNK = ("user: O akşam Mira'ya bir daha Halden'e ayak "
             "basmayacağını söyledi.")
    QUOTE = "O akşam Mira'ya bir daha Halden'e ayak basmayacağını"

    def test_a_note_that_shares_nothing_with_its_quote_is_dropped(self) -> None:
        kept, dropped = ex.parse_reply(
            reply([fact(text="She loves the sea.", evidence=self.QUOTE)]),
            self.CHUNK, [])
        assert kept == []
        assert dropped == {"unanchored": 1}, (
            "the drop must have its own name: a quote the model invented and "
            "a note the model invented call for opposite responses")

    def test_the_note_is_the_ONLY_thing_wrong_with_the_dropped_fact(self):
        """POSITIVE CONTROL for an absence claim, and it has to discriminate.

        The claim was "`text` is never checked at all". Handing the extractor
        a second absurd note proves nothing the test above did not already
        prove - both would stay green if the gate were neutered in either
        direction. What separates them is the PREDICATE, so it is asked
        directly: two facts identical in every field but `text`, one anchored
        and one not, and the gate answering differently about them.
        """
        good = fact(text="Mira has sworn never to return to Halden.",
                    evidence=self.QUOTE)
        bad = fact(text="The capital of France is Paris.",
                   evidence=self.QUOTE)
        assert {k: v for k, v in good.items() if k != "text"} == \
               {k: v for k, v in bad.items() if k != "text"}, \
            "the two facts must differ in the note and nothing else"
        anchors = ex._anchors(self.QUOTE)
        assert anchors, "the quote must reach the gate at all"
        assert ex._is_anchored(good["text"], anchors)
        assert not ex._is_anchored(bad["text"], anchors)
        # And the predicate is what the extractor actually acts on.
        assert len(ex.parse_reply(reply([good]), self.CHUNK, [])[0]) == 1
        assert ex.parse_reply(reply([bad]), self.CHUNK, []) == (
            [], {"unanchored": 1})

    def test_a_one_character_token_is_too_small_to_be_an_anchor(self) -> None:
        """A lone letter or digit appears in almost every sentence ever
        written, so as an anchor it is noise in one direction and a false drop
        in the other. Measured: with no minimum, a quote reading "5 yil once"
        anchors on "5" and an honest note that spells the number out in
        English is refused."""
        quote = "kardeşi 5 yıl önce gitti"
        assert ex._anchors(quote) == []
        kept, dropped = ex.parse_reply(
            reply([fact(text="Her sibling left five years ago.",
                        evidence=quote)]),
            "user: kardeşi 5 yıl önce gitti, dedi.", [])
        assert len(kept) == 1, dropped

    def test_the_anchor_gate_runs_before_the_enum_gate(self) -> None:
        """The counters have to say which thing went wrong FIRST, or a run
        that lost everything to one cause reads as a run that lost a bit to
        several. A fact that is both unanchored and off-enum is counted once,
        by the gate that reached it."""
        kept, dropped = ex.parse_reply(
            reply([fact(text="She loves the sea.", evidence=self.QUOTE,
                        kind="invented")]),
            self.CHUNK, [])
        assert kept == [] and dropped == {"unanchored": 1}

    def test_a_genuine_cross_lingual_note_survives(self) -> None:
        """GROUND CONTROL, and the one that stops "drop everything" from
        passing this class. Turkish quote, English note, ONE shared name out
        of the quote's two - which is exactly what the prompt asks the model
        to produce, and what a gate demanding all of them would refuse."""
        assert len(ex._anchors(self.QUOTE)) == 2, "the fixture must be the hard case"
        kept, dropped = ex.parse_reply(
            reply([fact(text="She has sworn never to return to Halden.",
                        evidence=self.QUOTE)]),
            self.CHUNK, [])
        assert len(kept) == 1, dropped
        assert dropped == {}

    def test_a_number_anchors_the_same_way_a_name_does(self) -> None:
        chunk = "user: 1987 yılında Halden'den ayrıldığını söyledi."
        quote = "1987 yılında"
        wrong = ex.parse_reply(
            reply([fact(text="She left home as a child.", evidence=quote)]),
            chunk, [])
        right = ex.parse_reply(
            reply([fact(text="She left Halden in 1987.", evidence=quote)]),
            chunk, [])
        assert wrong == ([], {"unanchored": 1})
        assert len(right[0]) == 1, right[1]

    def test_an_invented_quote_is_still_ungrounded_not_unanchored(self) -> None:
        """GROUND CONTROL for the gate this one was added NEXT to. The
        evidence check runs first and keeps its own name; a fact that never
        had a real quote is not a fact whose note drifted."""
        kept, dropped = ex.parse_reply(
            reply([fact(text="She loves the sea.",
                        evidence="bir daha Bergen'e ayak basmayacağını")]),
            self.CHUNK, [])
        assert kept == [] and dropped == {"ungrounded": 1}

    def test_the_first_word_of_a_quote_is_not_treated_as_a_name(self) -> None:
        """The cost of this gate, pinned so it stays deliberate.

        Every sentence starts with a capital letter, and Turkish opens plenty
        of them on ordinary nouns - "Kardeşi" is "her sibling", not a name.
        Reading the leading token as a proper noun drops true facts, so a
        quote whose only capital is its first word yields no anchor and the
        note is not checked. The gate is built to MISS rather than to
        over-reach: automatic acceptance is on by default, so a wrong drop
        loses a true note silently while a wrong pass still faces every other
        filter and the panel.
        """
        assert ex._anchors("Kardeşi değirmenin sahibi") == []
        kept, dropped = ex.parse_reply(
            reply([fact(evidence="Kardeşi değirmenin sahibi")]),
            "user: Kardeşi değirmenin sahibi, dedi.", [])
        assert len(kept) == 1, dropped

    def test_a_name_inside_the_quote_is_an_anchor(self) -> None:
        """POSITIVE CONTROL for the rule above: the exemption is about
        POSITION, not about capital letters being ignored."""
        assert ex._anchors("bir daha Halden'e ayak basmayacağını") == ["Halden"]

    def test_the_turkish_case_ending_comes_off_the_name(self) -> None:
        """Turkish attaches case endings to a proper noun with an apostrophe,
        so the quote says "Halden'e" where the note says "Halden". Comparing
        the whole token would drop every true Turkish fact that names a
        place."""
        assert ex._is_anchored("She went back to Halden.",
                               ex._anchors("sonra Halden'e döndü"))

    def test_a_curly_apostrophe_does_not_hide_the_name(self) -> None:
        """The same normalisation the grounding check needs, on the same
        pass: a model may write U+2019 where the transcript has U+0027."""
        curly = "sonra Halden" + chr(0x2019) + "e döndü"
        assert ex._anchors(curly) == ["Halden"]

    def test_the_gate_is_not_all_or_nothing(self) -> None:
        """MUTATION GUARD, written down rather than left to memory: a version
        of this check requiring EVERY anchor to appear would drop the ground
        control above, because a one-sentence note cannot carry every name in
        a long quote."""
        anchors = ex._anchors("Halden'de Mira ve Nisha ile buluştu")
        assert len(anchors) > 1, "the fixture must have several anchors"
        assert ex._is_anchored("She met Mira again.", anchors)


#: Realistic cross-lingual proposals, as (chunk, evidence, text, is_true).
#:
#: A Turkish transcript line, a verbatim Turkish quote from it, and an English
#: note - the shape the prompt asks for. Half of them are honest extractions;
#: half are the failure this gate exists to catch, a note about something the
#: quote never said. Every quote is grounded and correctly attributed, so the
#: ONLY thing separating the two halves is whether the note came from it.
#:
#: The honest half is deliberately mixed. Some notes name the person the quote
#: names, which is what the prompt orders ("third person, named entities, no
#: pronouns"). Some answer with a pronoun instead, which the prompt forbids and
#: models do anyway. That second group is where this gate costs something, and
#: the point of the corpus is that the cost is a number in a test rather than a
#: sentence in a comment.
_CORPUS: tuple[tuple[str, str, str, bool], ...] = (
    # -- honest, and naming what the quote names ----------------------------
    ("user: Mira, \"Bir daha Halden'e ayak basmayacağım,\" dedi.",
     "Bir daha Halden'e ayak basmayacağım",
     "Mira has sworn never to return to Halden.", True),
    ("user: Kadın çayları doldurup \"Ağabeyim Demir değirmenin sahibi,\" dedi.",
     "Ağabeyim Demir değirmenin sahibi",
     "Demir owns the mill.", True),
    ("user: \"Babam Trabzon'da doğdu,\" dedi Selin.",
     "Babam Trabzon'da doğdu",
     "Selin's father was born in Trabzon.", True),
    ("user: \"Nisha'yı 1987'den beri görmedim,\" dedi.",
     "Nisha'yı 1987'den beri görmedim",
     "She has not seen Nisha since 1987.", True),
    ("user: \"Kardeşim Emre hâlâ Ankara'da yaşıyor,\" dedi.",
     "Kardeşim Emre hâlâ Ankara'da yaşıyor",
     "Emre still lives in Ankara.", True),
    ("user: \"O gece Halden'den ayrıldım ve bir daha dönmedim,\" dedi.",
     "O gece Halden'den ayrıldım ve bir daha dönmedim",
     "She left Halden that night and never went back.", True),
    ("user: \"Annem Ayşe bana hiç yazmadı,\" dedi.",
     "Annem Ayşe bana hiç yazmadı",
     "Her mother Ayse never wrote to her.", True),
    ("user: \"Demir'in değirmeni yandı,\" dedi kadın.",
     "Demir'in değirmeni yandı",
     "Demir's mill burned down.", True),
    ("user: \"İki yıl Mira ile çalıştım,\" dedi.",
     "İki yıl Mira ile çalıştım",
     "She worked with Mira for two years.", True),
    ("user: \"Arşiv 1990'da kapandı,\" dedi.",
     "Arşiv 1990'da kapandı",
     "The archive closed in 1990.", True),
    # -- honest, but answering with a pronoun the prompt forbids ------------
    ("user: \"Ağabeyim Demir değirmenin sahibi,\" dedi.",
     "Ağabeyim Demir değirmenin sahibi",
     "Her brother owns the mill.", True),
    ("user: \"Babam Trabzon'da doğdu,\" dedi.",
     "Babam Trabzon'da doğdu",
     "Her father was born in the north.", True),
    ("user: \"Kardeşim Emre hâlâ orada yaşıyor,\" dedi.",
     "Kardeşim Emre hâlâ orada yaşıyor",
     "Her sibling still lives there.", True),
    ("user: \"Annem Ayşe bana hiç yazmadı,\" dedi.",
     "Annem Ayşe bana hiç yazmadı",
     "Her mother never wrote to her.", True),
    ("user: \"Mira'yı bir daha görmedim,\" dedi.",
     "Mira'yı bir daha görmedim",
     "She never saw her again.", True),
    # -- quotes with no anchor at all: the gate cannot run, and must not ----
    ("user: \"Kardeşi değirmenin sahibi,\" dedi.",
     "Kardeşi değirmenin sahibi",
     "Her brother owns the mill.", True),
    ("user: \"Bir daha oraya dönmedim,\" dedi.",
     "Bir daha oraya dönmedim",
     "She never went back there.", True),
    ("user: \"Babam bana hiç yazmadı,\" dedi.",
     "Babam bana hiç yazmadı",
     "Her father never wrote to her.", True),
    ("user: \"O gece çok yağmur yağdı,\" dedi.",
     "O gece çok yağmur yağdı",
     "It rained heavily that night.", True),
    ("user: \"Değirmeni sattık,\" dedi.",
     "Değirmeni sattık",
     "The family sold the mill.", True),
    # -- fabricated: the quote is real, the note is about something else ----
    ("user: Mira, \"Bir daha Halden'e ayak basmayacağım,\" dedi.",
     "Bir daha Halden'e ayak basmayacağım",
     "The capital of France is Paris.", False),
    ("user: Mira, \"Bir daha Halden'e ayak basmayacağım,\" dedi.",
     "Bir daha Halden'e ayak basmayacağım",
     "Mira moved to Oslo and opened a bakery.", False),
    ("user: \"Ağabeyim Demir değirmenin sahibi,\" dedi.",
     "Ağabeyim Demir değirmenin sahibi",
     "Selin trained as a doctor in Vienna.", False),
    ("user: \"Babam Trabzon'da doğdu,\" dedi Selin.",
     "Babam Trabzon'da doğdu",
     "Emre inherited the house in Ankara.", False),
    ("user: \"Nisha'yı 1987'den beri görmedim,\" dedi.",
     "Nisha'yı 1987'den beri görmedim",
     "Ayse was married in 2001.", False),
    ("user: \"Kardeşim Emre hâlâ Ankara'da yaşıyor,\" dedi.",
     "Kardeşim Emre hâlâ Ankara'da yaşıyor",
     "Demir sold the mill to a stranger.", False),
    ("user: \"O gece Halden'den ayrıldım,\" dedi.",
     "O gece Halden'den ayrıldım",
     "Nisha refuses to speak about Trabzon.", False),
    ("user: \"Annem Ayşe bana hiç yazmadı,\" dedi.",
     "Annem Ayşe bana hiç yazmadı",
     "Mira keeps every letter she was ever sent.", False),
    ("user: \"Demir'in değirmeni yandı,\" dedi kadın.",
     "Demir'in değirmeni yandı",
     "Selin owns a bookshop in Vienna.", False),
    ("user: \"Arşiv 1990'da kapandı,\" dedi.",
     "Arşiv 1990'da kapandı",
     "Emre was born in Trabzon in 2004.", False),
)


class TestWhatTheAnchorGateCOSTS:
    """The queue that asked for this gate asked for its false-positive rate to
    be MEASURED against a fixture set, in both directions, and it was right to:
    too tight and true notes vanish while the feature looks healthy, too loose
    and nothing changed. A sentence in a comment cannot regress. A number can.

    The corpus above is thirty realistic proposals with the honest and the
    fabricated halves built the same way, so what is counted here is the gate
    and not the fixtures.
    """

    def _ran(self):
        out = []
        for chunk, evidence, text, is_true in _CORPUS:
            kept, dropped = ex.parse_reply(
                reply([fact(text=text, evidence=evidence)]), chunk, [])
            assert "ungrounded" not in dropped, (
                f"fixture is not grounded, so it measures nothing: {chunk!r}")
            out.append((is_true, bool(kept), dropped))
        return out

    def test_every_fabrication_the_gate_CAN_see_is_caught(self) -> None:
        """The reason the gate exists. Each of these carries a real, verbatim,
        correctly attributed quote, so every filter that existed before this
        one passes them - and the gate catches all of the ones whose quote it
        can read an anchor out of."""
        missed = [(chunk, ev) for (chunk, ev, _t, is_true), (_it, kept, _d)
                  in zip(_CORPUS, self._ran())
                  if not is_true and kept and ex._anchors(ev)]
        assert missed == [], f"{len(missed)} anchored fabrications survived"

    def test_what_the_gate_CANNOT_see_is_counted_too(self) -> None:
        """The blind spot, pinned rather than described.

        A quote whose only name is its FIRST word yields no anchor, because a
        leading capital is a position artefact - and a fabrication attached to
        such a quote walks through untouched. That is the price of not
        dropping true Turkish facts that open on an ordinary noun, and it is
        the reason this gate is a filter and never a guarantee.
        """
        blind = [ev for (_c, ev, _t, is_true), (_it, kept, _d)
                 in zip(_CORPUS, self._ran())
                 if not is_true and kept]
        assert all(not ex._anchors(ev) for ev in blind), blind
        assert len(blind) == 1, (
            f"{len(blind)} fabrications escaped the gate; the pinned number "
            f"is 1. It may only move with a reason written next to it.")

    def test_the_drop_rate_on_TRUE_notes_is_pinned(self) -> None:
        """The cost, as a number that has to be edited on purpose.

        Every drop counted here is a true fact lost silently - the cursor
        moves past the range and nothing reads it again. All of them share one
        shape: the quote names somebody and the note answers with a pronoun,
        which the prompt already forbids in two separate rules. That does not
        make the loss free, and this is where it stays visible.
        """
        true_runs = [(kept, d) for is_true, kept, d in self._ran() if is_true]
        dropped = [d for kept, d in true_runs if not kept]
        assert all(d == {"unanchored": 1} for d in dropped), dropped
        assert len(dropped) == 4, (
            f"{len(dropped)} of {len(true_runs)} true notes dropped; the "
            f"pinned number is 4. Moving it UP means the gate got hungrier "
            f"and somebody has to want that; moving it DOWN is good news and "
            f"still has to be written here on purpose.")

    def test_a_name_transliterated_into_ASCII_still_anchors(self) -> None:
        """The looseness that took the pinned number from five to four, and
        it is not a special case: the prompt ORDERS the note into English and
        the quote into Turkish, so a name typed on an English keyboard loses
        its cedilla on the way across. Both spellings are the same name and
        neither field is wrong."""
        assert ex._is_anchored("Her mother Ayse never wrote to her.",
                               ex._anchors("Annem Ayşe bana hiç yazmadı"))
        # And the looseness is not blindness: a different name is still a
        # different name once the diacritics are gone.
        assert not ex._is_anchored("Her mother Elif never wrote to her.",
                                   ex._anchors("Annem Ayşe bana hiç yazmadı"))

    def test_the_corpus_is_not_stacked(self) -> None:
        """GROUND CONTROL for the two tests above. A corpus whose honest half
        is trivially anchored measures nothing, and neither does one whose
        quotes carry no anchor at all - the gate would never run."""
        anchored = sum(1 for _c, e, _t, _ in _CORPUS if ex._anchors(e))
        assert 15 <= anchored <= len(_CORPUS) - 5, (
            f"{anchored} of {len(_CORPUS)} quotes reach the gate at all")
        assert sum(1 for *_x, t in _CORPUS if t) >= 15
        assert sum(1 for *_x, t in _CORPUS if not t) >= 10


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
        too_long = "x" * (ex.notebook_store.ENTRY_MAX_CHARS + 1)
        kept, dropped = ex.parse_reply(reply([fact(text=too_long)]),
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


class TestTheThreeCeilingsAreConfiguration:
    """Five arms decide what one extraction costs. Two of them read the
    environment; these three were source constants, unreachable without
    editing the app - and they are the two that decide how much text is SENT
    plus the one that grows forever. The values did not move; where they are
    written down did.

    What each test below measures is not that a name EXISTS in config. It is
    that the configured number is the one the prompt is actually built
    against, which is the only version of "configurable" worth anything.
    """

    LONG_LINE = "user: " + "x" * 5000

    def test_the_configured_turns_ceiling_bounds_NEW_TURNS(
            self, monkeypatch) -> None:
        monkeypatch.setattr(config, "NOTEBOOK_TURNS_MAX_CHARS", 2000)
        msg = ex.build_user_message(card="", existing=[], recent=[],
                                    new=[self.LONG_LINE])
        assert section(msg, "NEW_TURNS") == "", (
            "a 5000-character turn entered a 2000-character section")

    def test_the_same_turn_fits_under_the_shipped_ceiling(self) -> None:
        """GROUND CONTROL. Without the monkeypatch the line goes in whole, so
        the emptiness above is the configured ceiling doing it and not the
        fixture being too long for any ceiling."""
        msg = ex.build_user_message(card="", existing=[], recent=[],
                                    new=[self.LONG_LINE])
        assert section(msg, "NEW_TURNS") == self.LONG_LINE

    def test_the_configured_card_ceiling_bounds_CHARACTER_CARD(
            self, monkeypatch) -> None:
        monkeypatch.setattr(config, "NOTEBOOK_CARD_MAX_CHARS", 40)
        msg = ex.build_user_message(card="c" * 5000, existing=[], recent=[],
                                    new=["user: x"])
        assert section(msg, "CHARACTER_CARD") == "c" * 40

    def test_the_configured_existing_ceiling_bounds_EXISTING_NOTES(
            self, monkeypatch) -> None:
        notes = ["n" * 240] * 20
        roomy = ex.build_user_message(card="", existing=notes, recent=[],
                                      new=["user: x"])
        monkeypatch.setattr(config, "NOTEBOOK_EXISTING_MAX_CHARS", 600)
        cramped = ex.build_user_message(card="", existing=notes, recent=[],
                                        new=["user: x"])
        assert len(section(cramped, "EXISTING_NOTES")) < len(
            section(roomy, "EXISTING_NOTES"))
        # Still SHORTENED rather than dropped: the numbers are indices the
        # caller resolves against the full list, so a dropped line retires
        # the wrong note.
        assert "\n19. " in cramped

    @pytest.mark.parametrize("variable, alias, configured, shipped", [
        ("ELYSIUM_NOTEBOOK_CARD_CHARS", "CARD_MAX_CHARS",
         "NOTEBOOK_CARD_MAX_CHARS", 4000),
        ("ELYSIUM_NOTEBOOK_EXISTING_CHARS", "EXISTING_MAX_CHARS",
         "NOTEBOOK_EXISTING_MAX_CHARS", 6000),
        ("ELYSIUM_NOTEBOOK_TURNS_CHARS", "TURNS_MAX_CHARS",
         "NOTEBOOK_TURNS_MAX_CHARS", 12000),
    ])
    def test_the_move_did_not_move_the_number(self, variable, alias,
                                              configured, shipped) -> None:
        """GROUND CONTROL, and the one place in this class where a literal is
        typed on purpose: what is being asserted IS the number, so importing
        it would assert nothing. It is the default with NO environment
        variable set, and it is what shipped before the definitions moved.

        IN A CLEAN ENVIRONMENT, which is why it costs an interpreter. Read
        in-process it would ask the machine it happens to run on, and the
        variable it would trip over is the one this unit just shipped: a
        developer who exports the knob to try it out would turn a claim about
        the DEFAULT into a claim about their shell.

        THIS TEST DOES NOT PROVE THE TWO NAMES ARE COUPLED. A hardcoded 12000
        in either place satisfies it just as well as a derivation does; that
        claim needs a different environment, and it is the test below.
        """
        clean = {k: v for k, v in os.environ.items() if k != variable}
        probe = subprocess.run(
            [sys.executable, "-c",
             "import config, notebook_extract as ex;"
             f"print(getattr(config, {configured!r}),"
             f" getattr(ex, {alias!r}))"],
            cwd=str(Path(__file__).resolve().parents[1]), env=clean,
            capture_output=True, text=True, timeout=120)
        assert probe.returncode == 0, probe.stderr
        assert probe.stdout.split() == [str(shipped), str(shipped)], probe.stdout

    @pytest.mark.parametrize("variable, configured, alias", [
        ("ELYSIUM_NOTEBOOK_CARD_CHARS", "NOTEBOOK_CARD_MAX_CHARS",
         "CARD_MAX_CHARS"),
        ("ELYSIUM_NOTEBOOK_EXISTING_CHARS", "NOTEBOOK_EXISTING_MAX_CHARS",
         "EXISTING_MAX_CHARS"),
        ("ELYSIUM_NOTEBOOK_TURNS_CHARS", "NOTEBOOK_TURNS_MAX_CHARS",
         "TURNS_MAX_CHARS"),
    ])
    def test_the_environment_reaches_BOTH_names(self, variable, configured,
                                                alias) -> None:
        """The coupling itself, and it needs a fresh interpreter to see.

        Two names hold this number at runtime: `config`, which the prompt
        builder reads live, and the alias in `notebook_extract`, which the
        planner in `notebook_worker` budgets against. They agree only because
        the alias is DERIVED at import. Cut the derivation and put a literal
        back and every in-process test still passes, which is why this one
        starts a new interpreter with the variable set and asks both names.

        This is the whole claim of moving the constants: five arms of one
        cost, all five settable, and the two halves of the app that spend it
        moving together.
        """
        env = dict(os.environ, **{variable: "3000"})
        probe = subprocess.run(
            [sys.executable, "-c",
             "import config, notebook_extract as ex;"
             f"print(getattr(config, {configured!r}),"
             f" getattr(ex, {alias!r}))"],
            cwd=str(Path(__file__).resolve().parents[1]), env=env,
            capture_output=True, text=True, timeout=120)
        assert probe.returncode == 0, probe.stderr
        assert probe.stdout.split() == ["3000", "3000"], probe.stdout

    def test_a_negative_ceiling_cannot_cut_the_card_from_the_wrong_end(
            self, monkeypatch) -> None:
        """A slice is not an error in Python. `card[:-100]` silently returns
        everything but the LAST hundred characters, which is the opposite of a
        ceiling and leaves no log, no counter and nothing on the panel. The
        floor in config.py is what stops a typed minus sign from doing it, and
        this is the behaviour that floor buys.
        """
        assert config.NOTEBOOK_CARD_MAX_CHARS > 0
        env = dict(os.environ, ELYSIUM_NOTEBOOK_CARD_CHARS="-100")
        probe = subprocess.run(
            [sys.executable, "-c",
             "import config;"
             "import notebook_extract as ex;"
             "print(config.NOTEBOOK_CARD_MAX_CHARS,"
             " len(ex.build_user_message(card='c' * 500, existing=[],"
             " recent=[], new=['user: x']).split(chr(62), 1)[1]))"],
            cwd=str(Path(__file__).resolve().parents[1]), env=env,
            capture_output=True, text=True, timeout=120)
        assert probe.returncode == 0, probe.stderr
        ceiling = int(probe.stdout.split()[0])
        assert ceiling >= 1, "a negative ceiling reached the slice"
        assert ceiling < 400, (
            "a 500-character card must not come back at 400, which is what "
            "`card[:-100]` returns")
