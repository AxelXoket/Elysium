"""U-74 - the existing-notes budget was divided equally, so long notes lost.

`build_user_message` numbers the notes already in the notebook and hands them
to the extractor so it can supersede them. The budget for that block was
split EQUALLY across the notes: each got
`(EXISTING_MAX_CHARS - prefix) // len(existing)` characters and was cut to
it, whether or not anybody else was using their share.

Measured at sixty notes with one of them 240 characters: the share works out
near 95, that note loses 145 characters, and about 2,900 characters of the
block sit empty. Nothing is logged and nothing is marked - the note simply
reaches the model with its end missing, and the model then judges whether to
supersede a fact it was shown half of.

The ledger had closed this as dead code reachable only from a dry-run button
that was removed. That was wrong: `build_user_message` is called from
`notebook_worker` on the live path.
"""
from __future__ import annotations

import notebook_extract as ex


def prefix_for(n: int) -> int:
    """The numbering prefixes, charged to the budget the same way the code
    charges them. Computed, not copied."""
    return sum(len(f"{i}. ") + 1 for i in range(n))


def block_of(message: str) -> str:
    """The EXISTING_NOTES section of an assembled user message.

    The fences carry a random per-request tag, so the closing marker cannot
    be written down here - it is read off the opening one.
    """
    open_at = message.index("<EXISTING_NOTES ")
    tag_end = message.index(">", open_at)
    tag = message[open_at + len("<EXISTING_NOTES "):tag_end]
    close = message.index(f"</EXISTING_NOTES {tag}>", tag_end)
    return message[tag_end + 1:close]


def build(existing: list[str]) -> str:
    return ex.build_user_message(card="", recent=[], new=["user: hello"],
                                 existing=existing)


class TestNothingIsCutWhileTheBudgetHolds:
    def test_a_long_note_among_short_ones_arrives_whole(self) -> None:
        long_note = "y" * 240
        existing = ["x" * 20] * 59 + [long_note]
        assert sum(len(n) for n in existing) + prefix_for(60) \
            < ex.EXISTING_MAX_CHARS, "ground: the whole set fits the budget"

        assert long_note in block_of(build(existing)), (
            "the note was cut to its equal share while the block sat "
            "two thirds empty")

    def test_a_single_note_under_the_budget_is_untouched(self) -> None:
        """POSITIVE CONTROL. The one-note case worked before and must still:
        a change that only helps crowds is not the fix."""
        note = "z" * 300
        assert note in block_of(build([note]))


class TestTheBudgetStillHolds:
    def test_a_set_that_cannot_fit_is_still_cut(self) -> None:
        """The other direction, and the reason this is not just "stop
        cutting". Removing the cut entirely makes the block unbounded in the
        note count again, which is exactly what the code's own comment
        forbids.
        """
        existing = ["z" * 500] * 60
        assert sum(len(n) for n in existing) > ex.EXISTING_MAX_CHARS, "ground"

        block = block_of(build(existing))

        # NO SLACK. An earlier version allowed `+ len("EXISTING_NOTES")`,
        # which is not part of the measured string at all - `block_of`
        # returns what is between the fences - so the guard tolerated a real
        # fourteen-character overrun for no reason anybody could name.
        assert len(block) <= ex.EXISTING_MAX_CHARS

    def test_every_note_still_gets_a_line(self) -> None:
        """Lines are SHORTENED, never dropped. The numbers are indices the
        caller resolves against the full list, so a dropped line shifts every
        later index and retires the wrong note."""
        existing = ["z" * 500] * 60
        block = block_of(build(existing))

        for i in range(60):
            assert f"\n{i}. " in block or block.startswith(f"{i}. "), (
                f"note {i} lost its line")

    def test_the_longest_notes_carry_the_loss(self) -> None:
        """Where the cutting falls when it has to fall somewhere.

        Under the equal share a 20-character note and a 500-character note
        were both cut to the same width, so the short one lost nothing and
        the long one lost most of itself. Filling greedily means the short
        ones arrive whole and only the long ones are trimmed.
        """
        existing = ["x" * 20] * 59 + ["y" * 6000]
        block = block_of(build(existing))

        assert block.count("x" * 20) == 59, "a short note was cut"
        assert "y" * 6000 not in block, "the budget was ignored"

        # AND the room the short ones did not use reached the long one.
        #
        # Without this the test cannot see the defect at all: an equal share
        # here works out near 95, and cutting a 20-character note at 95
        # leaves it exactly as it was. The only visible difference is how
        # much the LONG note got, so that is what has to be asserted.
        equal_share = ((ex.EXISTING_MAX_CHARS - prefix_for(60)) // 60)
        longest = max(len(line) for line in block.splitlines() if line.strip())
        assert longest > equal_share * 2, (
            f"the long note got {longest}, barely more than the equal share "
            f"of {equal_share} - the freed space was not redistributed")
