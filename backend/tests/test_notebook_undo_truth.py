""""Undo this run" keeps exactly what the reader made theirs,
and says which.

The rule: a note from the run that the reader kept by hand
(`hand_accepted_at`), pinned, or edited (`edited_at`) is KEPT and counted
under its own name; only the run's untouched notes go. A note written
before this vault recorded the two marks cannot say whether a person
accepted it, so an accepted one is kept too (`kept_older`), never guessed
away. `updated_at`, which a drag, a pin taken back and a kind change all
move, is no longer read by the undo at all.

Every outcome is driven through the routes the panel uses (accept, patch,
undo), with a ground beside each claim; the "from before" class is also
driven from a vault the previous schema code built.
"""
from __future__ import annotations

import pytest

import database
import notebook_store as notebook
from database import get_db
from tests.conftest import make_character, make_chat
from tests.test_range_reading_schema import _open, old_vault  # noqa: F401

API = "/api/v1/notebook"

#: The undo's whole answer when nothing is kept, so each test states only
#: what differs from it.
ZERO = {"kept_pinned": 0, "kept_edited": 0, "kept_accepted": 0,
        "kept_older": 0}


@pytest.fixture
def chat(client) -> int:
    return make_chat(client, make_character(client))


def _job_with_notes(chat_id: int, count: int, *,
                    status: str = notebook.STATUS_ACCEPTED
                    ) -> tuple[int, list[int]]:
    """A finished run that wrote `count` model notes, as the worker leaves
    them: `job_id` set, no marks. `status` accepted is the automatic
    switch's work; proposed is a suggestion waiting for the reader."""
    ids = [notebook.create_entry(chat_id, f"Fact {i} from the run.",
                                 provenance=notebook.PROV_MODEL,
                                 status=status)["id"]
           for i in range(count)]
    with get_db() as con:
        job = notebook.create_job(con, chat_id, "all", planned_calls=1)
        con.execute("UPDATE notebook_backfill_jobs SET state = 'done' "
                    "WHERE id = ?", (job["id"],))
        con.execute(
            f"UPDATE notebook_entries SET job_id = ? WHERE id IN "
            f"({','.join('?' * len(ids))})", (job["id"], *ids))
    return job["id"], ids


def _row(entry_id: int) -> dict:
    with get_db() as con:
        return dict(con.execute(
            "SELECT created_at, updated_at, pinned, status, edited_at, "
            "hand_accepted_at FROM notebook_entries WHERE id = ?",
            (entry_id,)).fetchone())


def _undo(client, job_id: int, chat_id: int) -> dict:
    r = client.post(f"{API}/extract/undo/{job_id}?chat_id={chat_id}")
    assert r.status_code == 200, r.text
    return r.json()


def _left(chat_id: int) -> set[int]:
    return {e["id"] for e in notebook.list_entries(chat_id)}


def _age(entry_ids: list[int]) -> None:
    """Make notes look written by an older build: created a day before the
    vault's marks began."""
    with get_db() as con:
        since = con.execute(
            "SELECT value FROM settings WHERE key = ?",
            (database.HAND_MARKS_SINCE_KEY,)).fetchone()[0]
        con.execute(
            f"UPDATE notebook_entries SET created_at = datetime(?, '-1 day'), "
            f"updated_at = datetime(?, '-1 day') WHERE id IN "
            f"({','.join('?' * len(entry_ids))})",
            (since, since, *entry_ids))


class TestTheRunsOwnWorkGoes:

    def test_GROUND_an_untouched_auto_accepted_note_is_removed(
            self, client, chat) -> None:
        """Ground for every "kept" row below: without it they pass on an
        undo that removes nothing."""
        job, (note,) = _job_with_notes(chat, 1)
        assert _row(note)["status"] == notebook.STATUS_ACCEPTED
        assert _undo(client, job, chat) == {"job_id": job, "removed": 1, **ZERO}
        assert note not in _left(chat)

    def test_an_unanswered_suggestion_is_removed(self, client, chat) -> None:
        job, (note,) = _job_with_notes(chat, 1, status=notebook.STATUS_PROPOSED)
        assert _undo(client, job, chat) == {"job_id": job, "removed": 1, **ZERO}


class TestAKeepByHandIsKept:
    """The reverse of the variant that removed kept suggestions: pressing
    Keep is the reader choosing the note, and the undo leaves it."""

    def test_a_suggestion_kept_by_hand_stays_and_is_counted_as_kept(
            self, client, chat) -> None:
        job, (kept,) = _job_with_notes(chat, 1, status=notebook.STATUS_PROPOSED)
        r = client.post(f"{API}/entries/{kept}/accept?chat_id={chat}")
        assert r.status_code == 200, r.text
        row = _row(kept)
        # GROUND: the accept landed and wrote the exact mark.
        assert row["status"] == notebook.STATUS_ACCEPTED
        assert row["hand_accepted_at"] is not None
        assert row["edited_at"] is None, "a keep is not an edit"
        assert _undo(client, job, chat) == {
            "job_id": job, "removed": 0, **ZERO, "kept_accepted": 1}
        assert kept in _left(chat)


class TestKeepOnTheSavedNotesCard:
    """ "Keep it" on the card about notes the automatic switch saved is a
    hand keep: it writes `hand_accepted_at`, so the undo keeps the note."""

    def test_keep_marks_the_note_and_the_undo_keeps_it(
            self, client, chat) -> None:
        job, (note,) = _job_with_notes(chat, 1)
        assert _row(note)["hand_accepted_at"] is None     # ground
        r = client.post(f"{API}/entries/{note}/keep?chat_id={chat}")
        assert r.status_code == 200, r.text
        assert r.json()["hand_accepted_at"] is not None
        assert _row(note)["hand_accepted_at"] is not None
        assert _undo(client, job, chat) == {
            "job_id": job, "removed": 0, **ZERO, "kept_accepted": 1}

    def test_a_second_keep_does_not_restamp(self, client, chat) -> None:
        _, (note,) = _job_with_notes(chat, 1)
        assert client.post(
            f"{API}/entries/{note}/keep?chat_id={chat}").status_code == 200
        with get_db() as con:
            con.execute("UPDATE notebook_entries SET hand_accepted_at = "
                        "'2000-01-01 00:00:00' WHERE id = ?", (note,))
        assert client.post(
            f"{API}/entries/{note}/keep?chat_id={chat}").status_code == 200
        assert _row(note)["hand_accepted_at"] == "2000-01-01 00:00:00"

    def test_it_does_not_accept_a_suggestion(self, client, chat) -> None:
        _, (note,) = _job_with_notes(chat, 1, status=notebook.STATUS_PROPOSED)
        r = client.post(f"{API}/entries/{note}/keep?chat_id={chat}")
        assert r.status_code == 200, r.text
        row = _row(note)
        assert row["status"] == notebook.STATUS_PROPOSED
        assert row["hand_accepted_at"] is None

    def test_another_chats_note_is_not_found(self, client, chat) -> None:
        _, (note,) = _job_with_notes(chat, 1)
        other = make_chat(client, make_character(client))
        r = client.post(f"{API}/entries/{note}/keep?chat_id={other}")
        assert r.status_code == 404, r.text
        assert _row(note)["hand_accepted_at"] is None


class TestAPinIsKeptAsAPin:

    def test_a_pinned_note_is_kept_as_pinned(self, client, chat) -> None:
        job, (pinned,) = _job_with_notes(chat, 1)
        r = client.patch(f"{API}/entries/{pinned}?chat_id={chat}",
                         json={"pinned": True})
        assert r.status_code == 200, r.text
        assert _row(pinned)["pinned"] == 1, "ground: the pin landed"
        assert _undo(client, job, chat) == {
            "job_id": job, "removed": 0, **ZERO, "kept_pinned": 1}

    def test_a_pin_taken_back_leaves_the_note_the_runs(
            self, client, chat) -> None:
        job, (note,) = _job_with_notes(chat, 1)
        for pinned in (True, False):
            r = client.patch(f"{API}/entries/{note}?chat_id={chat}",
                             json={"pinned": pinned})
            assert r.status_code == 200, r.text
        row = _row(note)
        assert row["pinned"] == 0, "ground: pinned, then unpinned"
        # The old signal fired, so the class below is not passing by luck.
        assert row["updated_at"] != row["created_at"]
        assert _undo(client, job, chat) == {"job_id": job, "removed": 1, **ZERO}


class TestAnEditIsKeptAsAnEdit:

    def test_new_words_are_kept_and_counted_as_edited(
            self, client, chat) -> None:
        job, (mine,) = _job_with_notes(chat, 1)
        r = client.patch(f"{API}/entries/{mine}?chat_id={chat}",
                         json={"text": "Fact 0, in my own words."})
        assert r.status_code == 200, r.text
        assert _row(mine)["edited_at"] is not None, "ground: the mark"
        assert _undo(client, job, chat) == {
            "job_id": job, "removed": 0, **ZERO, "kept_edited": 1}

    def test_the_same_words_sent_back_are_not_an_edit(
            self, client, chat) -> None:
        job, (note,) = _job_with_notes(chat, 1)
        r = client.patch(f"{API}/entries/{note}?chat_id={chat}",
                         json={"text": "Fact 0 from the run."})
        assert r.status_code == 200, r.text
        assert _row(note)["edited_at"] is None
        assert _undo(client, job, chat) == {"job_id": job, "removed": 1, **ZERO}


class TestEveryOutcomeInOneRun:

    def test_each_kept_note_is_counted_once_under_its_own_name(
            self, client, chat) -> None:
        job, (gone, accepted, pinned, edited, all_three) = _job_with_notes(
            chat, 5, status=notebook.STATUS_PROPOSED)
        for note in (accepted, all_three):
            assert client.post(
                f"{API}/entries/{note}/accept?chat_id={chat}").status_code == 200
        for note in (pinned, all_three):
            assert client.patch(f"{API}/entries/{note}?chat_id={chat}",
                                json={"pinned": True}).status_code == 200
        for note in (edited, all_three):
            assert client.patch(f"{API}/entries/{note}?chat_id={chat}",
                                json={"text": f"Mine {note}."}).status_code == 200

        # Pinned outranks edited outranks kept, so the one note with all
        # three marks is one pinned note, and the counts add up.
        assert _undo(client, job, chat) == {
            "job_id": job, "removed": 1, "kept_pinned": 2, "kept_edited": 1,
            "kept_accepted": 1, "kept_older": 0}
        left = _left(chat)
        assert {accepted, pinned, edited, all_three} <= left
        assert gone not in left


class TestNotesFromBeforeTheMarks:
    """Written by an older build: no mark could be written then, so an
    accepted one may have been kept by hand and is not removed."""

    def test_an_older_accepted_note_is_kept_as_older(
            self, client, chat) -> None:
        job, (old,) = _job_with_notes(chat, 1)
        _age([old])
        assert _undo(client, job, chat) == {
            "job_id": job, "removed": 0, **ZERO, "kept_older": 1}
        assert old in _left(chat)

    def test_POSITIVE_the_same_note_written_after_the_marks_goes(
            self, client, chat) -> None:
        """The twin of the row above with only the age changed: the class
        is decided by when the note was written, nothing else."""
        job, (new,) = _job_with_notes(chat, 1)
        assert _undo(client, job, chat) == {"job_id": job, "removed": 1, **ZERO}

    def test_an_older_suggestion_the_reader_rewrote_is_kept_as_edited(
            self, client, chat) -> None:
        """Rewritten by the old build: `updated_at` moved, no mark could be
        written then. Kept and counted as edited, not removed."""
        job, (old,) = _job_with_notes(chat, 1, status=notebook.STATUS_PROPOSED)
        _age([old])
        with get_db() as con:
            con.execute("UPDATE notebook_entries SET text = 'Rewritten.', "
                        "updated_at = datetime(updated_at, '+1 hour') "
                        "WHERE id = ?", (old,))
        assert _row(old)["edited_at"] is None            # ground: no mark
        assert _undo(client, job, chat) == {
            "job_id": job, "removed": 0, **ZERO, "kept_edited": 1}
        assert old in _left(chat)

    def test_an_older_suggestion_nobody_answered_goes(
            self, client, chat) -> None:
        job, (old,) = _job_with_notes(chat, 1, status=notebook.STATUS_PROPOSED)
        _age([old])
        assert _undo(client, job, chat) == {"job_id": job, "removed": 1, **ZERO}

    def test_from_a_vault_the_previous_code_built(self, old_vault) -> None:  # noqa: F811
        """End to end from the fixture the previous schema code wrote: an
        auto-accepted note, a pinned note and an unanswered proposal of chat
        1, all put under that vault's finished job 1, then migrated."""
        con = _open(old_vault)
        con.execute("UPDATE notebook_entries SET job_id = 1 "
                    "WHERE id IN (2, 3, 4)")
        con.commit()
        rows = {r["id"]: dict(r) for r in con.execute(
            "SELECT id, status, pinned FROM notebook_entries "
            "WHERE job_id = 1").fetchall()}
        con.close()
        # GROUND: one of each, as the fixture wrote them.
        assert rows == {2: {"id": 2, "status": "proposed", "pinned": 0},
                        3: {"id": 3, "status": "accepted", "pinned": 0},
                        4: {"id": 4, "status": "accepted", "pinned": 1}}

        database.init_db()

        con = _open(old_vault)
        try:
            con.execute("BEGIN IMMEDIATE")
            out = notebook.delete_job_notes(con, 1)
            con.execute("COMMIT")
            left = {r[0] for r in con.execute(
                "SELECT id FROM notebook_entries").fetchall()}
        finally:
            con.close()
        assert out == {"job_id": 1, "removed": 1, "kept_pinned": 1,
                       "kept_edited": 0, "kept_accepted": 0, "kept_older": 1}
        assert 3 in left and 4 in left and 2 not in left


class TestTheMarksSinceMoment:

    def test_it_is_written_once_and_never_moved(self, db) -> None:
        with get_db() as con:
            first = con.execute(
                "SELECT value FROM settings WHERE key = ?",
                (database.HAND_MARKS_SINCE_KEY,)).fetchone()
            assert first is not None, "init_db did not write it"
            con.execute("UPDATE settings SET value = '2000-01-01 00:00:00' "
                        "WHERE key = ?", (database.HAND_MARKS_SINCE_KEY,))
        database.init_db()
        with get_db() as con:
            again = con.execute(
                "SELECT value FROM settings WHERE key = ?",
                (database.HAND_MARKS_SINCE_KEY,)).fetchone()[0]
        # A later launch moving it forward would turn every older note
        # into one "this build wrote", and an undo could then remove it.
        assert again == "2000-01-01 00:00:00"

    def test_a_missing_moment_fails_toward_keep(self, client, chat) -> None:
        """No marks-since row: nothing can say which notes predate the
        marks, so an accepted note of the run is kept, never guessed away."""
        job, (old, new) = _job_with_notes(chat, 2)
        _age([old])
        with get_db() as con:
            con.execute("DELETE FROM settings WHERE key = ?",
                        (database.HAND_MARKS_SINCE_KEY,))
            # GROUND: the row really is gone.
            assert con.execute(
                "SELECT COUNT(*) FROM settings WHERE key = ?",
                (database.HAND_MARKS_SINCE_KEY,)).fetchone()[0] == 0
        assert _undo(client, job, chat) == {
            "job_id": job, "removed": 0, **ZERO, "kept_older": 2}
        assert {old, new} <= _left(chat)

    def test_POSITIVE_with_the_moment_the_new_note_goes(
            self, client, chat) -> None:
        job, (old, new) = _job_with_notes(chat, 2)
        _age([old])
        assert _undo(client, job, chat) == {
            "job_id": job, "removed": 1, **ZERO, "kept_older": 1}
        assert new not in _left(chat)

    def test_a_failed_write_is_logged(self, caplog) -> None:
        import sqlite3 as stdlib_sqlite3

        con = stdlib_sqlite3.connect(":memory:")
        try:
            with caplog.at_level("WARNING", logger="database"):
                database._record_hand_marks_since(con)  # no settings table
        finally:
            con.close()
        assert any("hand-marks moment not recorded" in r.getMessage()
                   for r in caplog.records), caplog.records

    def test_a_file_without_settings_does_not_stop_the_migration(
            self) -> None:
        import sqlite3 as stdlib_sqlite3

        con = stdlib_sqlite3.connect(":memory:")
        try:
            database._record_hand_marks_since(con)      # must not raise
            # POSITIVE CONTROL: with the table, the row is written.
            con.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, "
                        "value TEXT NOT NULL)")
            database._record_hand_marks_since(con)
            assert con.execute(
                "SELECT COUNT(*) FROM settings WHERE key = ?",
                (database.HAND_MARKS_SINCE_KEY,)).fetchone()[0] == 1
        finally:
            con.close()
