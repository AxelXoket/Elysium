"""U-09 - what gets marked read, and the way back to what did not.

`_plan_work` sat at the centre of an accumulating triangle:

  * a SKIPPED attempt set `notebook_extracted_ever` without moving the cursor,
    which then told the planner the chat had been read before;
  * the first read of a long chat jumps to the PRESENT on purpose, and the
    cursor is a MAX, so everything under it was unreachable for good;
  * the character budget dropped the oldest lines of a batch AFTER the range
    had been recorded, so messages no model ever saw were marked read;
  * `existing` was accepted notes only, and bare - so an imported-card chat
    showed the model nothing, and the model could not tell its own unreviewed
    suggestions from the reader's own writing;
  * raw voice tags went into the notebook's prompt and into stored evidence.

They compound: each one makes the next harder to see. Reading below the
cursor (`_plan_work(after_id=...)`) is the piece that makes the first three
recoverable rather than permanent, and it goes through the worker's own door
- one claim, one cursor, one accounting. It used to be reached by a one-press
sweep route as well as by a reading job; decision 69 a removed the route, and
the job from the reading strip is the one way in now.
"""
from __future__ import annotations

import json

import pytest

import config
import notebook_extract
import notebook_store as notebook
import notebook_worker
import openrouter
import voice_tags
from database import get_db, set_setting

from tests.test_notebook_worker import fact, seed


@pytest.fixture
def anyio_backend():
    return "asyncio"


def message_ids(chat_id: int) -> list[int]:
    with get_db() as con:
        return [r[0] for r in con.execute(
            "SELECT id FROM messages WHERE chat_id = ? AND active = 1 "
            "ORDER BY id", (chat_id,)).fetchall()]


class TestASkippedAttemptIsNotAReading:
    def test_it_does_not_set_the_ever_read_flag(self, client) -> None:
        """The flag exists to say "this chat has been read before", and a
        skip reads nothing. Set by a skip, it told the planner to stop
        jumping to the present - so a chat that had never been read was read
        from its OLDEST message, by the one path that read nothing at all."""
        chat_id = seed(client, count=4)

        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.commit_extraction(
                con, work_key="s1", chat_id=chat_id, from_id=1, to_id=4,
                proposals=[], status="skipped",
                skip_reason="notebook_daily_cap_reached")

        with get_db() as con:
            flag = con.execute(
                "SELECT notebook_extracted_ever FROM chats WHERE id = ?",
                (chat_id,)).fetchone()[0]
        assert flag == 0

    def test_a_real_extraction_still_sets_it(self, client) -> None:
        """POSITIVE CONTROL. Without it the fix is satisfied by never setting
        the flag at all, which brings back the defect it was added for."""
        chat_id = seed(client, count=4)

        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.commit_extraction(
                con, work_key="d1", chat_id=chat_id, from_id=1, to_id=4,
                proposals=[])

        with get_db() as con:
            flag = con.execute(
                "SELECT notebook_extracted_ever FROM chats WHERE id = ?",
                (chat_id,)).fetchone()[0]
        assert flag == 1


class TestWhatEnteredThePromptIsWhatIsMarkedRead:
    def test_a_batch_larger_than_the_budget_records_only_what_fits(
            self, client, monkeypatch) -> None:
        """The silent loss, rebuilt at the other end of the same function.

        `build_user_message` keeps whole lines from the NEW end and drops the
        rest. The planner recorded the whole SQL range anyway, so the dropped
        lines were marked read without ever being shown to anything.
        """
        chat_id = seed(client, count=40)
        # A ceiling that only a few lines can fit under, and a limit above
        # the whole chat so the SQL range is not the thing doing the cutting.
        monkeypatch.setattr(notebook_extract, "TURNS_MAX_CHARS", 120)

        plan = notebook_worker._plan_work(chat_id, "vendor/cheap", "en", 4, 200)

        assert plan is not None
        ids = message_ids(chat_id)
        assert plan["to_id"] == ids[-1], "the newest end is unchanged"
        assert plan["from_id"] > ids[0], (
            "the range still claims lines the budget threw away")
        # And the range is exactly the lines that survived.
        assert len(plan["new"]) == plan["to_id"] - plan["from_id"] + 1

    def test_a_batch_that_fits_is_recorded_whole(self, client) -> None:
        """GROUND CONTROL: the narrowing must not shrink an ordinary batch."""
        chat_id = seed(client, count=6)

        plan = notebook_worker._plan_work(chat_id, "vendor/cheap", "en", 4, 20)

        ids = message_ids(chat_id)
        assert plan["from_id"] == ids[0]
        assert plan["to_id"] == ids[-1]


class TestTheHistoryIsReachableAgain:
    def test_the_scope_query_finds_a_hole_a_MAX_stepped_over(self, client):
        """The whole reason the sweep can work at all.

        The cursor is `MAX(to_message_id)`. Given a covered range at the TOP
        of a chat and nothing below it, a maximum answers "everything up to
        here is done" and there is no way to ask the other question. The
        range table has always been able to answer it.
        """
        chat_id = seed(client, count=20)
        ids = message_ids(chat_id)
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.commit_extraction(
                con, work_key="top", chat_id=chat_id,
                from_id=ids[-4], to_id=ids[-1], proposals=[])

        with get_db() as con:
            cursor = con.execute(
                "SELECT COALESCE(MAX(to_message_id), 0) FROM "
                "notebook_extractions WHERE chat_id = ?", (chat_id,)
            ).fetchone()[0]
            after = notebook.first_unread_message(con, chat_id)

        assert cursor == ids[-1], "ground: the MAX really is at the top"
        assert after == 0, "the first unread message is the very first one"

    def test_it_reports_nothing_unread_when_everything_is_covered(
            self, client) -> None:
        """POSITIVE CONTROL in the other direction: a fully-read chat must
        not offer a sweep, or the button reads as broken."""
        chat_id = seed(client, count=6)
        ids = message_ids(chat_id)
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.commit_extraction(
                con, work_key="all", chat_id=chat_id,
                from_id=ids[0], to_id=ids[-1], proposals=[])

        with get_db() as con:
            assert notebook.first_unread_message(con, chat_id) is None

    def test_the_planner_reads_below_the_cursor_when_told_to(self, client):
        chat_id = seed(client, count=20)
        ids = message_ids(chat_id)
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.commit_extraction(
                con, work_key="top2", chat_id=chat_id,
                from_id=ids[-4], to_id=ids[-1], proposals=[])

        ordinary = notebook_worker._plan_work(
            chat_id, "vendor/cheap", "en", 4, 20)
        swept = notebook_worker._plan_work(
            chat_id, "vendor/cheap", "en", 4, 20, min_new=1, after_id=0)

        assert ordinary is None, "ground: the ordinary path has nothing left"
        assert swept is not None
        assert swept["from_id"] == ids[0]

    def test_a_long_first_read_still_jumps_to_the_present(self, client):
        """The branch this does NOT change. Reading an existing chat from its
        oldest end would put the notebook a session behind and inject those
        notes into every turn until it caught up - each one a paid call."""
        chat_id = seed(client, count=200)

        plan = notebook_worker._plan_work(chat_id, "vendor/cheap", "en", 4, 20)

        assert plan["to_id"] == message_ids(chat_id)[-1]

    def test_but_a_sweep_of_the_same_chat_starts_at_the_beginning(
            self, client) -> None:
        """And that is the difference the whole unit is about: the jump is
        still the default and is no longer permanent."""
        chat_id = seed(client, count=200)

        plan = notebook_worker._plan_work(
            chat_id, "vendor/cheap", "en", 4, 20, min_new=1, after_id=0)

        assert plan["from_id"] == message_ids(chat_id)[0]


class TestTheOldSweepRouteIsGone:
    """Decision 69 a (3.18). `POST /notebook/sweep/{chat_id}` read a stretch
    of history with no preview and could spend the ten calls the live
    reader keeps for the day's conversations; no screen called it any more.
    It is removed, route and method, and the reading strip's `next` job is
    the door that is left."""

    def test_the_old_sweep_route_is_gone(self, client) -> None:
        chat_id = seed(client, count=6)

        r = client.post(f"/api/v1/notebook/sweep/{chat_id}")

        assert r.status_code in (404, 405), (
            f"the sweep route still answers: {r.status_code} {r.text}")
        # POSITIVE CONTROL: the same client, the same chat, the door that
        # is left. It answers 200 - started or refused with a reason, both
        # are ordinary answers - so the 404 above is about the old path and
        # not about a router that answers nothing.
        ok = client.post(f"/api/v1/notebook/extract/run/{chat_id}",
                         json={"scope": "next"})
        assert ok.status_code == 200, ok.text
        assert "started" in ok.json()

    def test_the_worker_has_no_second_door(self) -> None:
        """The method went with the route. A `sweep` left on the worker is
        a door any later route could wire up again without a preview."""
        assert not hasattr(notebook_worker.Worker, "sweep")
        # GROUND: the attribute check does see the worker's real doors.
        assert hasattr(notebook_worker.Worker, "backfill")


class TestWhatTheModelIsShown:
    def test_an_unreviewed_suggestion_is_visible_and_marked(self, client):
        """An imported-card chat forces review, so every note it has is
        `proposed` - and `existing` used to be accepted-only, so the model
        was shown an empty list and proposed the same facts every turn."""
        chat_id = seed(client, count=6)
        notebook.create_entry(chat_id, "Her brother owns the mill.",
                              provenance=notebook.PROV_MODEL,
                              status=notebook.STATUS_PROPOSED)

        plan = notebook_worker._plan_work(chat_id, "vendor/cheap", "en", 4, 20)

        assert plan["existing"], "the model was shown nothing"
        assert "unreviewed suggestion" in plan["existing"][0]

    def test_the_readers_own_note_is_marked_as_theirs(self, client) -> None:
        chat_id = seed(client, count=6)
        notebook.create_entry(chat_id, "Her brother owns the mill.")

        plan = notebook_worker._plan_work(chat_id, "vendor/cheap", "en", 4, 20)

        assert "written by the reader" in plan["existing"][0]

    def test_the_two_lists_stay_aligned(self, client) -> None:
        """The alignment `parse_reply` and `commit_extraction` both depend on.
        Marking entries is safe; dropping, sorting or trimming one is not."""
        chat_id = seed(client, count=6)
        for i in range(3):
            notebook.create_entry(chat_id, f"Fact number {i}.",
                                  provenance=notebook.PROV_MODEL)

        plan = notebook_worker._plan_work(chat_id, "vendor/cheap", "en", 4, 20)

        assert len(plan["existing"]) == len(plan["existing_ids"]) == 3
        for line, entry_id in zip(plan["existing"], plan["existing_ids"]):
            with get_db() as con:
                text = con.execute(
                    "SELECT text FROM notebook_entries WHERE id = ?",
                    (entry_id,)).fetchone()[0]
            assert text in line


class TestVoiceTagsDoNotReachTheNotebook:
    def test_the_prompt_and_the_grounding_text_are_stripped_together(
            self, client) -> None:
        """Both halves, in one test, because separating them is the bug.

        `parse_reply` checks a quote against `chunk`, and `chunk` is built
        from the same lines as the prompt. Strip one side and not the other
        and EVERY quote is ungrounded - the feature empties itself silently
        and no fixture without tags in it would ever notice.
        """
        chat_id = seed(client, count=6)
        with get_db() as con:
            con.execute(
                "UPDATE messages SET content = ? WHERE chat_id = ? "
                "AND id = (SELECT MAX(id) FROM messages WHERE chat_id = ?)",
                ("[whisper] Her brother owns the mill.", chat_id, chat_id))
            con.execute(
                "UPDATE messages SET role = 'assistant' WHERE chat_id = ? "
                "AND id = (SELECT MAX(id) FROM messages WHERE chat_id = ?)",
                (chat_id, chat_id))
        voice_tags.mark_voice_ever_enabled()

        plan = notebook_worker._plan_work(chat_id, "vendor/cheap", "en", 4, 20)

        assert "[whisper]" not in plan["chunk"]
        assert "[whisper]" not in "".join(plan["new"])
        # And the two are the SAME text, which is what keeps grounding alive.
        assert plan["chunk"] == "\n".join(plan["new"])
        assert "Her brother owns the mill." in plan["chunk"]

    def test_a_users_own_square_brackets_survive(self, client) -> None:
        """GROUND CONTROL, and it is the rule strip_for_display already
        states: user text is never stripped. Eating somebody's own "[sic]"
        is display corruption."""
        chat_id = seed(client, count=6)
        with get_db() as con:
            con.execute(
                "UPDATE messages SET content = ?, role = 'user' "
                "WHERE chat_id = ? AND id = "
                "(SELECT MAX(id) FROM messages WHERE chat_id = ?)",
                ("[sic] her brother owns the mill", chat_id, chat_id))
        voice_tags.mark_voice_ever_enabled()

        plan = notebook_worker._plan_work(chat_id, "vendor/cheap", "en", 4, 20)

        assert "[sic]" in plan["chunk"]


class TestTheEdgeOfTheJumpThreshold:
    """The edge, bracketed from both sides.

    It was written against `limit * 2` and is now `threshold * 2`. Same
    bracket, different operand, and the operand is the whole point: `limit`
    is `Worker.batch_size`, which halves on the breaker's consecutive
    failures, so the edge used to move with the provider's health. See
    test_notebook_planner_budget.py for what that cost.
    """

    def test_just_over_the_edge_jumps(self, client) -> None:
        """Counted from the real message list rather than from the seed
        count: a chat opened from a character carries its greeting as a real
        row, so "twenty seeded messages" is twenty-one pending."""
        chat_id = seed(client, count=20)
        pending = len(message_ids(chat_id))
        threshold = (pending - 1) // 2      # pending > threshold * 2, just

        plan = notebook_worker._plan_work(
            chat_id, "vendor/cheap", "en", threshold, 20)

        ids = message_ids(chat_id)
        assert plan["to_id"] == ids[-1]
        assert plan["from_id"] == ids[-20]

    def test_exactly_on_the_edge_does_not(self, client) -> None:
        chat_id = seed(client, count=20)
        pending = len(message_ids(chat_id))
        threshold = (pending + 1) // 2      # pending == threshold * 2 (or under)

        plan = notebook_worker._plan_work(
            chat_id, "vendor/cheap", "en", threshold, 20)

        assert plan["from_id"] == message_ids(chat_id)[0], (
            "pending == threshold * 2 is NOT more than threshold * 2")
