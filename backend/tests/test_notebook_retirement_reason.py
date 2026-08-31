"""U-25 - a note cannot stay out of the prompt because of a row that is gone.

Retirement is the app deciding a fact stopped being true, and the thing that
decided it is the note that replaced it. `delete_entry` cleared the POINTER
to the deleted note - so no row named a target that did not exist - and left
`retired_at` exactly where it was.

So the older note was out of the prompt permanently, on the strength of a row
nobody can look at any more, and nothing in the application could bring it
back: `update_entry` does not allow the column, no route exists for it, and
the only other place that clears it is a narrow foreign-key repair with a
different purpose.
"""
from __future__ import annotations

import notebook_store as notebook
from database import get_db

from tests.test_notebook_worker import seed


def blocks(chat_id: int) -> str:
    out = notebook.build_notebook_blocks(chat_id, 9000)
    return out["user_block"] + out["model_block"]


class TestDeletingTheReplacement:
    def test_the_older_note_comes_back_into_the_prompt(self, client) -> None:
        chat_id = seed(client, count=4)
        older = notebook.create_entry(chat_id, "The mill belonged to her uncle.")
        newer = notebook.create_entry(chat_id, "The mill belongs to her brother.")
        assert notebook.retire_entry(older["id"], superseded_by=newer["id"])

        # GROUND CONTROL: while the replacement stands, the older note is
        # correctly out. Without this the test passes against an application
        # where retirement never worked at all.
        assert "her uncle" not in blocks(chat_id)

        assert notebook.delete_entry(newer["id"])

        assert "her uncle" in blocks(chat_id), (
            "the note stayed out of the prompt because of a row that no "
            "longer exists")

    def test_the_pointer_is_cleared_too(self, client) -> None:
        """POSITIVE CONTROL. The old behaviour cleared the pointer and left
        the retirement; a fix that cleared the retirement and left the
        pointer would be the same contradiction the other way up, with a row
        naming a target that is not there."""
        chat_id = seed(client, count=4)
        older = notebook.create_entry(chat_id, "The mill belonged to her uncle.")
        newer = notebook.create_entry(chat_id, "The mill belongs to her brother.")
        notebook.retire_entry(older["id"], superseded_by=newer["id"])

        notebook.delete_entry(newer["id"])

        rows = {e["id"]: e for e in notebook.list_entries(chat_id)}
        assert rows[older["id"]]["superseded_by"] is None
        assert rows[older["id"]]["retired_at"] is None

    def test_a_note_retired_by_something_else_is_untouched(self, client):
        """The scope of the change. Deleting one replacement must not revive
        every retired note in the chat."""
        chat_id = seed(client, count=4)
        a = notebook.create_entry(chat_id, "A, replaced by B.")
        b = notebook.create_entry(chat_id, "B, the replacement.")
        c = notebook.create_entry(chat_id, "C, replaced by D.")
        d = notebook.create_entry(chat_id, "D, the other replacement.")
        notebook.retire_entry(a["id"], superseded_by=b["id"])
        notebook.retire_entry(c["id"], superseded_by=d["id"])

        notebook.delete_entry(b["id"])

        rows = {e["id"]: e for e in notebook.list_entries(chat_id)}
        assert rows[a["id"]]["retired_at"] is None
        assert rows[c["id"]]["retired_at"] is not None, (
            "an unrelated retirement was undone")

    def test_a_plain_delete_retires_nothing_and_revives_nothing(
            self, client) -> None:
        """GROUND CONTROL for the WHERE clause: a note nothing points at is
        deleted exactly as before."""
        chat_id = seed(client, count=4)
        kept = notebook.create_entry(chat_id, "Still true.")
        spare = notebook.create_entry(chat_id, "Also true.")

        assert notebook.delete_entry(spare["id"])

        rows = {e["id"]: e for e in notebook.list_entries(chat_id)}
        assert rows[kept["id"]]["retired_at"] is None
        assert spare["id"] not in rows

    def test_it_can_be_retired_again_afterwards(self, client) -> None:
        """The deliberate consequence, asserted rather than left implied.

        `retire_entry` refuses a second call while the first reason stands -
        that is what its `retired_at IS NULL` guard is for. Once the reason
        is gone, a later replacement must be able to retire it again, or the
        revival would be a one-way door.
        """
        chat_id = seed(client, count=4)
        older = notebook.create_entry(chat_id, "The mill belonged to her uncle.")
        newer = notebook.create_entry(chat_id, "The mill belongs to her brother.")
        notebook.retire_entry(older["id"], superseded_by=newer["id"])
        assert notebook.retire_entry(older["id"]) is False, (
            "ground: while the reason stands, retiring again is not an event")

        notebook.delete_entry(newer["id"])
        third = notebook.create_entry(chat_id, "The mill is a ruin now.")

        assert notebook.retire_entry(older["id"], superseded_by=third["id"])
        with get_db() as con:
            row = con.execute(
                "SELECT retired_at FROM notebook_entries WHERE id = ?",
                (older["id"],)).fetchone()
        assert row[0] is not None
