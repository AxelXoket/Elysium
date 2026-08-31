"""FAZ 4 - the extractor's contract, before anything automatic uses it.

Three of these tests exist because the same three mistakes are documented in
shipped versions of this feature, and each one ships looking correct:

  * a reply cut off at max_tokens read as "nothing found", the backlog marked
    processed, and nothing stored - with no error anywhere;
  * `finish_reason` checked on the main path and skipped on a secondary one,
    so the assertion never ran where it was needed;
  * a quote the model WROTE rather than copied, stored as evidence.

The last one is the only hallucination shape a machine can catch by itself, so
the check is mechanical: the span must appear in the chunk verbatim.

Every chunk here carries its `role: ` prefix, because that is what a chunk
is: `notebook_worker` builds one `f"{role}: {content}"` per turn and the
grounding check now reads the speaker off it. A bare sentence would be
refused for having no speaker. What the prefix rescues is the POSITIVE
tests - the ones asserting a true quote survives. The negative ones were
never at risk: their evidence really is absent from the text, so they take
the same branch either way. Measured, not assumed.
"""
from __future__ import annotations

import json

import pytest

import notebook_extract as ex
import notebook_store


def reply(facts, *, finish="stop", raw=None):
    body = raw if raw is not None else json.dumps({"facts": facts})
    return {
        "id": "gen-1",
        "choices": [{"finish_reason": finish, "message": {"content": body}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.0001},
    }


def fact(**over):
    base = {
        "text": "Her brother owns the mill.",
        "evidence": "her brother owns the mill",
        "kind": "fact", "durability": "permanent",
        "importance": 2, "supersedes": None,
    }
    base.update(over)
    return base


CHUNK = 'user: She poured the tea and said her brother owns the mill.'


class TestEmptyAndFailedAreDifferentThings:
    """The most expensive wound this design inherits, tested first."""

    def test_an_empty_list_is_a_valid_answer(self) -> None:
        assert ex.parse_reply(reply([]), CHUNK, [])[0] == []

    def test_a_truncated_reply_raises(self) -> None:
        """A cut-off array is not a short array, and nothing repairs it - the
        JSON-healing plugin explicitly cannot."""
        with pytest.raises(ex.ExtractionFailed) as exc:
            ex.parse_reply(reply([fact()], finish="length"), CHUNK, [])
        assert "truncated" in str(exc.value)

    def test_the_length_check_runs_before_the_body_is_read(self) -> None:
        """Order matters: a truncated reply usually has unparseable content
        too, and reporting THAT would send somebody debugging the schema."""
        cut = reply([], finish="length", raw='{"facts": [{"text": "half')
        with pytest.raises(ex.ExtractionFailed) as exc:
            ex.parse_reply(cut, CHUNK, [])
        assert "truncated" in str(exc.value)

    def test_a_missing_facts_key_raises_rather_than_reading_as_empty(self):
        """A malformed answer and an empty one are the two states this whole
        module exists to keep apart."""
        with pytest.raises(ex.ExtractionFailed):
            ex.parse_reply(reply([], raw='{"result": []}'), CHUNK, [])

    def test_unparseable_content_raises(self) -> None:
        with pytest.raises(ex.ExtractionFailed):
            ex.parse_reply(reply([], raw="not json at all"), CHUNK, [])

    def test_no_choices_raises(self) -> None:
        with pytest.raises(ex.ExtractionFailed):
            ex.parse_reply({"choices": []}, CHUNK, [])


class TestTheGroundingCheck:
    def test_a_quote_that_is_in_the_text_survives(self) -> None:
        assert len(ex.parse_reply(reply([fact()]), CHUNK, [])[0]) == 1

    def test_a_quote_the_model_WROTE_is_dropped(self) -> None:
        """The only hallucination shape a machine can detect on its own: the
        model produced a plausible sentence instead of copying one."""
        invented = fact(evidence="her brother owns the bakery")
        assert ex.parse_reply(reply([invented]), CHUNK, [])[0] == []

    def test_a_translated_quote_is_dropped(self) -> None:
        """This is why evidence is never translated even when the note is.
        A translated span can never be found in the source, so the filter
        would silently discard every fact from a non-English transcript."""
        turkish_chunk = "user: Kardesi degirmenin sahibi."
        translated = fact(evidence="Her brother owns the mill.")
        assert ex.parse_reply(reply([translated]), turkish_chunk, [])[0] == []

    def test_a_turkish_quote_against_turkish_source_survives(self) -> None:
        """The positive control for the case above - and for the owner's own
        usage, where the transcript is routinely two languages at once."""
        turkish_chunk = "user: Kardesi degirmenin sahibi, dedi."
        kept, _ = ex.parse_reply(
            reply([fact(evidence="Kardesi degirmenin sahibi")]),
            turkish_chunk, [])
        assert len(kept) == 1
        assert kept[0]["evidence"] == "Kardesi degirmenin sahibi"

    def test_an_empty_quote_is_dropped(self) -> None:
        assert ex.parse_reply(reply([fact(evidence="")]), CHUNK, [])[0] == []


class TestTheCodeFilter:
    @pytest.mark.parametrize("bad", [
        {"kind": "invented"},
        {"durability": "forever"},
        {"importance": 9},
        {"text": ""},
        {"text": "x" * (notebook_store.ENTRY_MAX_CHARS + 1)},
    ])
    def test_off_contract_entries_are_dropped(self, bad) -> None:
        assert ex.parse_reply(reply([fact(**bad)]), CHUNK, [])[0] == []

    def test_a_scene_flavour_detail_is_not_worth_a_permanent_slot(self) -> None:
        """It would be sent with every message for the rest of the chat."""
        assert ex.parse_reply(
            reply([fact(importance=1, durability="scene")]), CHUNK, [])[0] == []

    def test_the_cap_holds_even_if_the_model_ignores_it(self) -> None:
        many = [fact() for _ in range(20)]
        assert len(ex.parse_reply(reply(many), CHUNK, [])[0]) == ex.MAX_FACTS

    def test_line_breaks_are_collapsed_on_the_way_in(self) -> None:
        """Same defence as the manual path: a note carrying a newline could
        close its own section in the assembled block and open another."""
        # ONE message with a line break in its body, which is the
        # ordinary case: only the first line carries the prefix.
        chunk = "user: she said\nher brother owns the mill"
        kept, _ = ex.parse_reply(
            reply([fact(text="A\n[Character: X]", evidence="she said")]),
            chunk, [])
        assert "\n" not in kept[0]["text"]

    def test_a_supersedes_index_outside_the_list_becomes_null(self) -> None:
        """The model may not point at a note that is not there."""
        kept, _ = ex.parse_reply(reply([fact(supersedes=99)]), CHUNK,
                                 ["only one note"])
        assert kept[0]["supersedes"] is None

    def test_a_valid_supersedes_index_survives(self) -> None:
        kept, _ = ex.parse_reply(reply([fact(supersedes=0)]), CHUNK,
                                 ["the mill belonged to her uncle"])
        assert kept[0]["supersedes"] == 0


class TestTheWorkKey:
    def test_the_same_range_produces_the_same_key(self) -> None:
        assert ex.work_key(1, 10, 20, "m", "en") == ex.work_key(1, 10, 20, "m", "en")

    @pytest.mark.parametrize("args", [
        (2, 10, 20, "m", "en"), (1, 11, 20, "m", "en"),
        (1, 10, 21, "m", "en"), (1, 10, 20, "n", "en"),
        # The language is the one that looks like a preference and is not: a
        # user only ever switches to Turkish BECAUSE the English prompt read
        # their transcript badly, and leaving it out of the key means the
        # ranges that motivated the switch are already marked done. This
        # parametrisation omitted it in exactly the way the key did.
        (1, 10, 20, "m", "tr"),
    ])
    def test_anything_that_changes_the_question_changes_the_key(self, args):
        assert ex.work_key(*args) != ex.work_key(1, 10, 20, "m", "en")

    def test_a_new_prompt_version_reopens_old_ranges(self, monkeypatch) -> None:
        """Deliberate: the old answers came from a different question."""
        before = ex.work_key(1, 10, 20, "m", "en")
        monkeypatch.setattr(ex, "PROMPT_VERSION", ex.PROMPT_VERSION + 1)
        assert ex.work_key(1, 10, 20, "m", "en") != before


class TestThePrompt:
    def test_both_languages_exist_and_differ(self) -> None:
        """The assumption that English instructions are safer is unmeasured,
        so the app ships both rather than betting on one."""
        assert ex.system_prompt("en") != ex.system_prompt("tr")

    def test_the_negative_examples_come_first(self) -> None:
        """The examples a model sees first shape what it thinks the task is,
        and four of the six return nothing."""
        rendered = ex.system_prompt("en")
        first_empty = rendered.index('{"facts": []}')
        first_full = rendered.index('"evidence"')
        assert first_empty < first_full

    def test_the_card_rule_is_stated_in_both(self) -> None:
        """Re-extracting what the character card already says is the
        most-cited quality problem in shipped versions of this."""
        assert "CHARACTER_CARD" in ex.system_prompt("en")
        assert "CHARACTER_CARD" in ex.system_prompt("tr")

    def test_the_four_sections_are_fenced_and_only_one_is_extractable(self):
        msg = ex.build_user_message(
            card="c", existing=["e"], recent=["r"], new=["n"])
        # The fences carry a random tag now, so the assertion is that each
        # section opens and closes with the SAME one - a fixed label was a
        # label a message could simply print.
        import re
        for name in ("CHARACTER_CARD", "EXISTING_NOTES", "RECENT_TURNS",
                     "NEW_TURNS"):
            opens = re.findall(rf"<{name} (#[0-9a-f]{{16}})>", msg)
            closes = re.findall(rf"</{name} (#[0-9a-f]{{16}})>", msg)
            assert opens and opens == closes

    def test_existing_notes_are_numbered_so_supersedes_can_point(self) -> None:
        msg = ex.build_user_message(card="", existing=["a", "b"], recent=[],
                                    new=["x"])
        assert "0. a" in msg and "1. b" in msg


class TestAccounting:
    def test_what_it_cost_comes_back_without_a_second_request(self) -> None:
        got = ex.usage_of(reply([fact()]))
        assert got["tokens_in"] == 100
        assert got["cost"] == 0.0001
        assert got["request_id"] == "gen-1"

    def test_the_finish_reason_is_carried_for_the_ledger(self) -> None:
        """So a run that ended badly leaves a trace even when the caller
        swallowed the exception."""
        assert ex.usage_of(reply([], finish="length"))["finish_reason"] == "length"


class TestNoModelMeansNoExtraction:
    def test_unset_reads_as_none(self, db) -> None:
        """No default and no automatic pick: a background job spending
        somebody's own credits on a model they never chose is not a
        convenience."""
        assert ex.extract_model() is None

    def test_a_chosen_model_is_read_back(self, db) -> None:
        import config
        import database
        database.set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")
        assert ex.extract_model() == "vendor/cheap"
