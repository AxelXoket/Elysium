"""U-24 - the ceiling was measured with the wrong ruler, twice over.

`build_notebook_blocks` decides what fits by measuring a candidate set and
comparing it to a ceiling. It got the measurement wrong in two independent
ways and then ran it in a loop:

  * both groups were measured with the MODEL header. The two headers are not
    the same length (`_open_notebook` 296, `_open_notebook_model` 271), so
    the user block was measured 25 characters short;
  * the frame added +2 newlines per group where the assembly produces n + 1
    for n lines, and the per-line cost already carries one each - so every
    group was measured 1 character long.

Net for the user block: 24 characters short, meaning a user block could sit
24 characters OVER the ceiling. Those characters go into `system_chars` and
every send in that chat then fails with `context_too_large` - a message about
the context window that names nothing about the notebook and never suggests
the one thing that fixes it.

The measurement also rescanned every surviving row on each call, from inside
the eviction loop: n notes, up to n evictions, O(n) each, with no cap on how
many notes a chat may hold.

MEASURED ON THE OUTER SURFACE. `_size` and `_block` are closures inside
`build_notebook_blocks`; no test can call them. Everything they measure is
module level, and the function returns the assembled blocks, so the length
model is rebuilt here from the same module functions and checked against the
real output first (GROUND 1). Constants are imported, never retyped.
"""
from __future__ import annotations

import pytest

import notebook_store as notebook

from tests.conftest import make_character, make_chat


def _expected(tag: str, rows, opener) -> str:
    """The assembly, rebuilt from the module's own pieces."""
    return "\n".join([opener(tag),
                      *(notebook._entry_line(r) for r in rows),
                      notebook._close_notebook(tag)])


def _tag_of(block: str) -> str:
    """`[Notebook #<16 hex> - ...` -> `<16 hex>`.

    16 because `_tag()` is `secrets.token_hex(8)`, so every length here is
    deterministic and nothing has to be written down as a number.
    """
    return block.split("#", 1)[1][:16]


def _window_for(ceiling: int) -> int:
    """An `available_chars` whose percentage budget lands exactly on
    `ceiling`, with the flat cap out of the way."""
    assert ceiling <= notebook.NOTEBOOK_MAX_CHARS, (
        "the flat ceiling would bite first and the test would measure it")
    avail = ceiling * 10
    assert int(avail * notebook.NOTEBOOK_BUDGET_FRACTION) == ceiling
    return avail


def notes(client, *, user: int = 0, model: int = 0, chars: int = 100,
          importance: int = 2, model_importance: int | None = None) -> int:
    chat_id = make_chat(client, make_character(client))
    for i in range(user):
        notebook.create_entry(chat_id, text=f"u{i} " + "x" * chars,
                              importance=importance)
    for i in range(model):
        notebook.create_entry(chat_id, text=f"m{i} " + "y" * chars,
                              importance=(model_importance
                                          if model_importance is not None
                                          else importance),
                              provenance=notebook.PROV_MODEL)
    return chat_id


def rows_of(chat_id: int, prov: str):
    return [r for r in notebook.list_entries(chat_id, include_retired=False)
            if r["status"] == notebook.STATUS_ACCEPTED
            and r["provenance"] == prov]


class TestTheRulerMatchesTheThingItMeasures:
    def test_each_block_is_built_with_its_own_header(self, client) -> None:
        """GROUND 1, and it does two jobs.

        It proves this file's length model is byte-identical to the real
        output, which is what makes every ceiling number below trustworthy.
        And it nails down WHICH header belongs to which block, which is the
        only thing that catches the wrong fix - making both groups measure
        with the same header.
        """
        chat_id = notes(client, user=3, model=3)
        blocks = notebook.build_notebook_blocks(chat_id, 32000)

        tag = _tag_of(blocks["user_block"])
        assert _tag_of(blocks["model_block"]) == tag, (
            "one tag for the whole assembly, or the header's own instruction "
            "about where the block ends is false for one of them")

        assert blocks["user_block"] == _expected(
            tag, rows_of(chat_id, notebook.PROV_USER), notebook._open_notebook)
        assert blocks["model_block"] == _expected(
            tag, rows_of(chat_id, notebook.PROV_MODEL),
            notebook._open_notebook_model)

    def test_a_notebook_that_fits_loses_nothing(self, client) -> None:
        """GROUND 2. Without it the assertions below can go green on an
        empty block, which is the failure they are least able to see."""
        chat_id = notes(client, user=3)
        blocks = notebook.build_notebook_blocks(chat_id, 32000)

        assert blocks["excluded"] == []
        assert blocks["sent"] == blocks["total"] == 3


class TestTheUserBlockCannotExceedTheCeiling:
    def test_it_stays_under_a_ceiling_one_character_below_it(
            self, client) -> None:
        """The 24-character overrun, made visible.

        Eviction happens in whole notes of ~200 characters, so an overrun of
        24 only surfaces when the true size sits within 24 of the ceiling.
        This puts it exactly one character over.
        """
        chat_id = notes(client, user=6)
        rows = rows_of(chat_id, notebook.PROV_USER)
        target = len(_expected("0" * 16, rows, notebook._open_notebook))
        ceiling = target - 1

        blocks = notebook.build_notebook_blocks(
            chat_id, _window_for(ceiling))

        # POSITIVE CONTROLS: a block that was emptied would satisfy any
        # inequality, and prove nothing about the arithmetic.
        assert blocks["sent"] >= 1
        assert blocks["user_block"] != ""

        assert len(blocks["user_block"]) <= ceiling


class TestTheModelBlockIsNotEvictedForOneCharacter:
    def test_a_set_that_fits_exactly_keeps_every_note(self, client) -> None:
        """The +1, and the reverse of the header error in one measurement.

        The ceiling is the block's exact length. Measuring one long evicts a
        note that fits; measuring the model group with the USER header would
        come out 25 long and evict one too. Only the correct arithmetic
        keeps all six.
        """
        chat_id = notes(client, model=6)
        rows = rows_of(chat_id, notebook.PROV_MODEL)
        ceiling = len(_expected("0" * 16, rows, notebook._open_notebook_model))

        blocks = notebook.build_notebook_blocks(
            chat_id, _window_for(ceiling))

        assert blocks["excluded"] == []
        assert blocks["sent"] == blocks["total"] == 6
        assert len(blocks["model_block"]) == ceiling


class TestAnEmptiedGroupStopsCostingItsHeader:
    def test_the_model_notes_all_survive(self, client) -> None:
        """NOT a red-green criterion - a trap for the incremental total.

        Missing this decrement does not overrun the ceiling; it evicts MORE
        than needed. The block gets SHORTER, every `<= ceiling` assertion
        stays green, and notes go missing quietly. So the assertion is an
        EQUALITY on `sent`.

        Six cheap user notes and six important model ones, with room for
        exactly the model block: the user notes all go, and when the last one
        does its header and closing line must leave the arithmetic with it.
        """
        chat_id = notes(client, user=6, model=6, importance=1,
                        model_importance=3)
        rows = rows_of(chat_id, notebook.PROV_MODEL)
        ceiling = len(_expected("0" * 16, rows, notebook._open_notebook_model))

        blocks = notebook.build_notebook_blocks(
            chat_id, _window_for(ceiling))

        assert blocks["user_block"] == ""
        assert blocks["sent"] == 6
        assert [reason for _, reason in blocks["excluded"]] == (
            ["over_ceiling"] * 6)


class TestEvictionIsLinearInTheNumberOfNotes:
    def test_the_line_is_measured_a_bounded_number_of_times(
            self, client, monkeypatch) -> None:
        """Deterministic, not a wall clock.

        `_entry_line` is a module-level name, and the closures look it up as
        a global at call time, so a counting wrapper is visible from inside
        both the measurement and the assembly.

        Budget: 3 per note - one pass to build the cost table, one to seed
        the running total, one to assemble the blocks, with `pinned_size`
        reading the same table rather than re-measuring.

        RE-MEASURED with this fixture: 510 calls for 500 notes. The figure
        recorded here first said 1000, which left the budget carrying three
        times the real cost - a regression that tripled the work would have
        passed. The bound is deliberately a little loose (the exact count
        depends on how many notes survive), but not that loose.

        Before the change the measurement rescanned every survivor on each
        turn of the eviction loop: ~500 notes, ~490 evictions, six figures.
        """
        n = 500
        chat_id = notes(client, user=n, chars=200)

        real = notebook._entry_line
        calls = 0

        def counting(row):
            nonlocal calls
            calls += 1
            return real(row)

        # The SECOND detector, and it is needed. The line budget above sees
        # a cost table rebuilt per step, but not a running total recomputed
        # per step - that reads the table rather than re-measuring, so it
        # costs no `_entry_line` calls at all while still being O(n) work on
        # every one of n evictions.
        #
        # `_close_notebook` is what the measurement calls once per group, so
        # counting it counts how many times the whole assembly was sized.
        # That number is a small constant: twice for the seed and the pinned
        # set, once per group that empties, twice for the two blocks. It has
        # no business growing with the number of notes.
        real_close = notebook._close_notebook
        closes = 0

        def counting_close(tag):
            nonlocal closes
            closes += 1
            return real_close(tag)

        monkeypatch.setattr(notebook, "_entry_line", counting)
        monkeypatch.setattr(notebook, "_close_notebook", counting_close)
        blocks = notebook.build_notebook_blocks(chat_id, 32000)

        # GROUND: the eviction this measures actually happened. A run that
        # dropped nothing would meet any call budget.
        assert len(blocks["excluded"]) > n // 2, "no real eviction pressure"
        assert calls <= 2 * n, f"{calls} line measurements for {n} notes"
        # RE-MEASURED with this fixture: 2. The comment first said 6 and the
        # bound 20, which is ten times the real number.
        assert closes <= 6, f"the assembly was sized {closes} times"
