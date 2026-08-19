"""FAZ 2 - what reaches the model, what does not, and who is told.

Three separate promises are under test, and they fail in different directions:

  * the notes the user wrote reach the model in the system channel, and the
    ones the model wrote reach it somewhere weaker - a split that is only
    meaningful if nothing can relabel a row on the way;
  * the ceiling drops the cheapest notes, never the pinned ones, and never
    silently;
  * the limits are the one block that refuses to shrink - because a limit that
    quietly stops being sent is worse than no limit at all.

Assertions are against the ASSEMBLED PAYLOAD, not against the store. A full
notebook proves nothing about what crossed the wire, and "the feature looks
enabled and does nothing" is the most-reported failure in shipped memory
systems.
"""
from __future__ import annotations

import pytest

import database
import notebook_store as notebook
from routers.completions import _assemble_messages


def _seed(con) -> int:
    con.execute("INSERT INTO characters (name) VALUES ('C')")
    ch = con.execute("SELECT MAX(id) FROM characters").fetchone()[0]
    con.execute("INSERT INTO chats (character_id, title) VALUES (?, 't')", (ch,))
    return con.execute("SELECT MAX(id) FROM chats").fetchone()[0]


@pytest.fixture()
def chat(db) -> int:
    with database.get_db() as con:
        return _seed(con)


def _payload(chat_id: int, *, budget: int = 90_000, history=None):
    blocks = notebook.build_notebook_blocks(chat_id, budget)
    trimmed: list[int] = []
    msgs = _assemble_messages(
        "[Character: X]", "", list(history or []), "hello", "",
        budget, 2000,
        boundary_block=blocks["boundary_block"],
        notebook_user_block=blocks["user_block"],
        notebook_model_block=blocks["model_block"],
        trimmed_out=trimmed,
    )
    return msgs, blocks, trimmed


class TestWhatReachesTheModel:
    def test_a_user_note_is_in_the_payload(self, db, chat) -> None:
        notebook.create_entry(chat, "Mira is her sister.")
        msgs, _, _ = _payload(chat)
        assert any("Mira is her sister." in m["content"] for m in msgs)

    def test_an_empty_notebook_adds_nothing(self, db, chat) -> None:
        before, _, _ = _payload(chat)
        notebook.create_entry(chat, "x")
        after, _, _ = _payload(chat)
        assert len(after) == len(before) + 1

    def test_a_proposed_note_never_reaches_the_model(self, db, chat) -> None:
        """§G-2. The whole safety of the extraction step rests on this one
        line: a suggestion is not memory until a person says so."""
        notebook.create_entry(chat, "unreviewed guess",
                              status=notebook.STATUS_PROPOSED,
                              provenance=notebook.PROV_MODEL)
        msgs, _, _ = _payload(chat)
        assert not any("unreviewed guess" in m["content"] for m in msgs)

    def test_a_retired_note_never_reaches_the_model(self, db, chat) -> None:
        entry = notebook.create_entry(chat, "the wound is open")
        notebook.retire_entry(entry["id"])
        msgs, _, _ = _payload(chat)
        assert not any("the wound is open" in m["content"] for m in msgs)

    def test_the_two_provenances_land_in_different_places(self, db, chat):
        """§G-1. What the user wrote sits with the persona; what the model
        wrote sits at the tail. Same table, same ceiling, different authority.
        """
        notebook.create_entry(chat, "user wrote this")
        notebook.create_entry(chat, "model wrote this",
                              provenance=notebook.PROV_MODEL)
        msgs, _, _ = _payload(chat)
        idx = {}
        for i, m in enumerate(msgs):
            for tag in ("user wrote this", "model wrote this"):
                if tag in m["content"]:
                    idx[tag] = i
        assert idx["user wrote this"] < idx["model wrote this"]
        # And the user's half is genuinely ahead of the conversation, not
        # merely ahead of the model's half.
        assert idx["user wrote this"] < len(msgs) - 1

    def test_the_block_says_it_is_data(self, db, chat) -> None:
        """A note is quoted material. The wrapper says so out loud, and the
        newline collapse stops a note forging the wrapper."""
        notebook.create_entry(chat, "anything")
        msgs, _, _ = _payload(chat)
        block = next(m["content"] for m in msgs if "anything" in m["content"])
        assert "DATA NOT INSTRUCTIONS" in block
        assert block.endswith("[End of notebook]")


class TestTheCeiling:
    def _fill(self, chat: int, n: int, *, importance: int = 2,
              pinned: bool = False) -> None:
        for i in range(n):
            notebook.create_entry(chat, f"note number {i} " + "x" * 60,
                                  importance=importance, pinned=pinned)

    def test_a_small_notebook_is_sent_whole(self, db, chat) -> None:
        self._fill(chat, 5)
        _, blocks, _ = _payload(chat)
        assert blocks["sent"] == blocks["total"] == 5
        assert blocks["excluded"] == []

    def test_past_the_ceiling_some_are_left_out(self, db, chat) -> None:
        self._fill(chat, 80)
        _, blocks, _ = _payload(chat)
        assert blocks["sent"] < blocks["total"]
        assert blocks["excluded"], "dropped without recording it"

    def test_a_pinned_note_is_never_dropped(self, db, chat) -> None:
        """A ceiling with no priority lever is the documented starvation
        failure: the entry you care about stops arriving and nothing says so.
        """
        pinned = notebook.create_entry(chat, "PINNED AND CRITICAL",
                                       importance=1, pinned=True)
        self._fill(chat, 80, importance=3)
        msgs, blocks, _ = _payload(chat)
        assert pinned["id"] not in {i for i, _r in blocks["excluded"]}
        assert any("PINNED AND CRITICAL" in m["content"] for m in msgs)

    def test_the_cheapest_notes_go_first(self, db, chat) -> None:
        cheap = notebook.create_entry(chat, "flavour " + "x" * 60, importance=1)
        dear = notebook.create_entry(chat, "defining " + "x" * 60, importance=3)
        self._fill(chat, 80, importance=2)
        _, blocks, _ = _payload(chat)
        dropped = {i for i, _r in blocks["excluded"]}
        assert cheap["id"] in dropped
        assert dear["id"] not in dropped

    def test_a_dropped_note_is_still_in_the_panel_with_a_reason(self, db, chat):
        """The owner's rule: a note never disappears. It stops being sent, and
        the screen says why."""
        self._fill(chat, 80)
        _, blocks, _ = _payload(chat)
        notebook.record_exclusions(chat, blocks["excluded"])
        rows = notebook.list_entries(chat)
        assert len(rows) == 80, "a note was deleted to make room"
        excused = [r for r in rows if r["excluded_reason"]]
        assert len(excused) == len(blocks["excluded"])
        assert all(r["excluded_reason"] == "over_ceiling" for r in excused)

    def test_the_reason_is_cleared_when_it_fits_again(self, db, chat) -> None:
        self._fill(chat, 80)
        _, blocks, _ = _payload(chat)
        notebook.record_exclusions(chat, blocks["excluded"])
        for row in notebook.list_entries(chat)[10:]:
            notebook.delete_entry(row["id"])
        _, blocks2, _ = _payload(chat)
        notebook.record_exclusions(chat, blocks2["excluded"])
        assert all(r["excluded_reason"] is None
                   for r in notebook.list_entries(chat))


class TestLimitsRefuseRatherThanShrink:
    def test_a_limit_is_in_the_payload(self, db, chat) -> None:
        notebook.create_boundary("no gore", "Avoid graphic injury.", "hard")
        msgs, _, _ = _payload(chat)
        assert any("Avoid graphic injury." in m["content"] for m in msgs)

    def test_limits_survive_a_full_notebook(self, db, chat) -> None:
        """§G-6. The one block that is not a candidate for the ceiling."""
        notebook.create_boundary("no gore", "Avoid graphic injury.", "hard")
        for i in range(80):
            notebook.create_entry(chat, f"filler {i} " + "x" * 60)
        msgs, blocks, _ = _payload(chat)
        assert blocks["sent"] < blocks["total"], "the ceiling never bit"
        assert any("Avoid graphic injury." in m["content"] for m in msgs)

    def test_a_chat_standing_alone_carries_none_of_the_global_limits(
        self, db, chat
    ) -> None:
        notebook.create_boundary("global", "Never write this.", "hard")
        notebook.set_use_global_boundaries(chat, False)
        msgs, _, _ = _payload(chat)
        assert not any("Never write this." in m["content"] for m in msgs)


class TestTheTurnSaysWhatItDropped:
    def test_history_trimming_is_counted(self, db, chat) -> None:
        """It never was. A conversation could lose ten turns and the only trace
        was a gauge in the corner - so shipping a count for the notebook alone
        would teach that dropped context gets announced, and that would be
        false for the larger case."""
        history = [{"role": "user", "content": "x" * 4000, "attachments": []}
                   for _ in range(40)]
        _, _, trimmed = _payload(chat, budget=20_000, history=history)
        assert trimmed and trimmed[0] > 0

    def test_nothing_trimmed_reports_zero(self, db, chat) -> None:
        _, _, trimmed = _payload(chat, history=[
            {"role": "user", "content": "short", "attachments": []}])
        assert trimmed == [0]


class TestTheBlocksArePaidForBeforeTheTrimRuns:
    """A mutation found this hole: removing the boundary block from
    `system_chars` broke nothing.

    The order is the whole rule. `system_chars` is what the trim loop subtracts
    from `available` to decide how much history survives. A block added to the
    payload but not to that sum is sent anyway, and the trim keeps history it
    has no room for - so the request goes out over budget, or the provider
    truncates it, and no counter noticed either.
    """

    def _surviving_history(self, chat: int, **blocks) -> int:
        history = [{"role": "user", "content": "x" * 500, "attachments": []}
                   for _ in range(60)]
        msgs = _assemble_messages(
            "[Character: X]", "", list(history), "hello", "",
            30_000, 2000, **blocks)
        return sum(1 for m in msgs if m["role"] == "user") - 1  # minus the new one

    def test_a_boundary_block_costs_history(self, db, chat) -> None:
        without = self._surviving_history(chat)
        with_limits = self._surviving_history(
            chat, boundary_block="L" * 3000)
        assert with_limits < without, (
            "the limits were sent but never charged, so the trim left history "
            "there was no room for")

    def test_a_notebook_block_costs_history(self, db, chat) -> None:
        without = self._surviving_history(chat)
        with_notes = self._surviving_history(
            chat, notebook_user_block="N" * 3000)
        assert with_notes < without

    def test_the_model_block_costs_history_too(self, db, chat) -> None:
        """It sits at the tail, which makes it the easiest one to forget."""
        without = self._surviving_history(chat)
        with_model = self._surviving_history(
            chat, notebook_model_block="M" * 3000)
        assert with_model < without
