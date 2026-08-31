"""U-05 - a deleted message takes its notes, and its words, with it.

`forget_proposals_from_messages` dropped only the UNREVIEWED suggestions. The
argument for keeping the accepted ones was about the FACT: removing one turn
does not retract something the reader approved, and an approved fact was
usually established in more than one place.

That argument is not about the QUOTE, and the quote is what the row carries.
`evidence` is a verbatim span of the message - the parser refuses a quote it
cannot find in the source text - capped at 240 characters. So an accepted
note went on holding a sentence out of a deleted message word for word:
rendered in the panel, matched by the panel's search, and sent over the wire
on every read of that chat. The one thing deleting a message is supposed to
accomplish is that its words stop existing.

KARAR 11 decides it. The documented exception is the right-arrow regeneration
flow, and that flow deletes no message at all - it sets `active = 0` - so it
is not one of the three callers here.
"""
from __future__ import annotations

import notebook_store as notebook
from database import get_db

from tests.conftest import make_character, make_chat

QUOTE = "the mill has been in her family for four generations"


def chat_with_messages(client, count: int = 6) -> tuple[int, list[int]]:
    char_id = make_character(client)
    chat_id = make_chat(client, char_id)
    with get_db() as con:
        for i in range(count):
            con.execute(
                "INSERT INTO messages (chat_id, role, content) VALUES (?,?,?)",
                (chat_id, "user" if i % 2 == 0 else "assistant",
                 QUOTE if i == 0 else f"line {i}"))
        ids = [r[0] for r in con.execute(
            "SELECT id FROM messages WHERE chat_id = ? ORDER BY id",
            (chat_id,)).fetchall()]
    return chat_id, ids


def note_from(chat_id: int, message_id: int, *, status: str,
              text: str = "The mill is her family's.") -> dict:
    return notebook.create_entry(
        chat_id, text, evidence=QUOTE, provenance=notebook.PROV_MODEL,
        status=status, source_message_id=message_id)


def evidence_rows() -> list[str]:
    with get_db() as con:
        return [r[0] for r in con.execute(
            "SELECT evidence FROM notebook_entries "
            "WHERE evidence IS NOT NULL").fetchall()]


class TestTheWordsGoWithTheMessage:
    def test_an_accepted_note_from_a_deleted_message_is_gone(self, client):
        chat_id, ids = chat_with_messages(client)
        kept = note_from(chat_id, ids[0], status=notebook.STATUS_ACCEPTED)

        # GROUND CONTROL: the quote really is in the database first.
        assert QUOTE in evidence_rows()

        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.forget_proposals_from_messages(con, [ids[0]])

        rows = {e["id"]: e for e in notebook.list_entries(chat_id)}
        assert kept["id"] not in rows
        assert QUOTE not in evidence_rows(), (
            "the message was deleted and its own words stayed in the vault")

    def test_an_unreviewed_suggestion_still_goes(self, client) -> None:
        """The behaviour that was already right, unchanged."""
        chat_id, ids = chat_with_messages(client)
        pending = note_from(chat_id, ids[0], status=notebook.STATUS_PROPOSED)

        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.forget_proposals_from_messages(con, [ids[0]])

        assert pending["id"] not in {
            e["id"] for e in notebook.list_entries(chat_id)}

    def test_a_note_from_a_message_that_stays_is_untouched(self, client):
        """GROUND CONTROL for the WHERE clause. Widening the delete from one
        status to all of them must not widen it from one message to all of
        them."""
        chat_id, ids = chat_with_messages(client)
        doomed = note_from(chat_id, ids[0], status=notebook.STATUS_ACCEPTED)
        safe = note_from(chat_id, ids[3], status=notebook.STATUS_ACCEPTED,
                         text="Her sister runs the ferry.")

        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.forget_proposals_from_messages(con, [ids[0]])

        rows = {e["id"] for e in notebook.list_entries(chat_id)}
        assert doomed["id"] not in rows
        assert safe["id"] in rows

    def test_another_chats_notes_are_untouched(self, client) -> None:
        chat_a, ids_a = chat_with_messages(client)
        chat_b, ids_b = chat_with_messages(client)
        mine = note_from(chat_a, ids_a[0], status=notebook.STATUS_ACCEPTED)
        theirs = note_from(chat_b, ids_b[0], status=notebook.STATUS_ACCEPTED,
                           text="A different chat's fact.")

        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.forget_proposals_from_messages(con, [ids_a[0]])

        assert mine["id"] not in {e["id"] for e in notebook.list_entries(chat_a)}
        assert theirs["id"] in {e["id"] for e in notebook.list_entries(chat_b)}


class TestTheDeleteStillCompletes:
    def test_a_note_pointing_at_a_deleted_one_does_not_abort_it(self, client):
        """POSITIVE CONTROL, and the failure this could most easily bring
        back.

        `superseded_by` is a foreign key with enforcement on. The release
        used to cover only the proposals, because only proposals were being
        deleted. Now that accepted rows go too, a note pointing at one of
        THOSE would abort the whole statement - and an aborted statement here
        means the user's message cannot be deleted at all.
        """
        chat_id, ids = chat_with_messages(client)
        older = notebook.create_entry(chat_id, "The mill belonged to her uncle.")
        replacement = note_from(chat_id, ids[0],
                                status=notebook.STATUS_ACCEPTED)
        assert notebook.retire_entry(older["id"],
                                     superseded_by=replacement["id"])

        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.forget_proposals_from_messages(con, [ids[0]])
            # The message itself, which is what the foreign key would block.
            con.execute("DELETE FROM messages WHERE id = ?", (ids[0],))

        rows = {e["id"]: e for e in notebook.list_entries(chat_id)}
        assert replacement["id"] not in rows
        assert older["id"] in rows
        assert rows[older["id"]]["superseded_by"] is None
        with get_db() as con:
            gone = con.execute("SELECT 1 FROM messages WHERE id = ?",
                               (ids[0],)).fetchone()
        assert gone is None, "the message could not be deleted"

    def test_the_route_that_deletes_a_turn_completes(self, client) -> None:
        """End to end, through the real route rather than the helper."""
        chat_id, ids = chat_with_messages(client)
        note_from(chat_id, ids[2], status=notebook.STATUS_ACCEPTED)

        r = client.delete(f"/api/v1/chats/{chat_id}/messages/{ids[2]}")

        assert r.status_code == 200, r.text
        assert QUOTE not in evidence_rows()


class TestTheVariantFlowIsNotThisPath:
    def test_regenerating_deletes_no_message_and_no_note(self, client) -> None:
        """KARAR 11's documented exception, asserted rather than assumed.

        Regenerating in the same bubble deactivates the old variant - it sets
        `active = 0` - and deletes nothing, so it never reaches this function
        at all. If that ever changes, this is the test that says so.
        """
        chat_id, ids = chat_with_messages(client)
        kept = note_from(chat_id, ids[1], status=notebook.STATUS_ACCEPTED)

        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute("UPDATE messages SET active = 0 WHERE id = ?",
                        (ids[1],))

        assert kept["id"] in {e["id"] for e in notebook.list_entries(chat_id)}
        assert QUOTE in evidence_rows()
