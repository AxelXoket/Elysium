"""U-43 - the auto-accept indicator and the decision are one answer.

`notebook_auto_accept_override` was supposed to be a single source of truth
and was wrong from three directions:

  * the status route read the GLOBAL setting for itself, so the switch on
    screen and `auto_accept_for` - the function that actually decides -
    disagreed for exactly the chats the override exists to protect;
  * the migration that added the column never backfilled it, so every chat
    opened before it landed carries NULL, including the ones from imported
    cards, and they fell through to a global switch that defaults to ON;
  * nothing could ever write the column except the chat INSERT, so a chat
    that was wrongly trusted stayed that way for its whole life.

The fourth gap is named rather than closed, because it cannot be closed:
somebody who pastes a downloaded card's fields into the form by hand leaves
no record that the text came from outside. Nothing in the application can
know that. The answer is that the state is visible and reversible.
"""
from __future__ import annotations

import json

import notebook_store as notebook
from database import get_db

from tests.conftest import make_character, make_chat

API = "/api/v1/notebook"


def imported_character(client) -> int:
    """A character created the way the importer creates one: raw card kept."""
    char_id = make_character(client)
    with get_db() as con:
        con.execute("UPDATE characters SET raw_json = ? WHERE id = ?",
                    (json.dumps({"name": "Someone", "description": "..."}),
                     char_id))
    return char_id


class TestTheIndicatorAndTheDecision:
    def test_they_agree_for_a_chat_from_an_imported_card(self, client) -> None:
        chat_id = make_chat(client, imported_character(client))

        body = client.get(f"{API}/auto-accept?chat_id={chat_id}").json()
        with get_db() as con:
            decided = notebook.auto_accept_for(con, chat_id)

        assert decided is False, "ground: the extractor forces review here"
        assert body["effective"] is False
        assert body["overridden"] is True
        # The global switch itself is unchanged and still reported as it is.
        assert body["enabled"] is True

    def test_they_agree_for_an_ordinary_chat(self, client) -> None:
        """GROUND CONTROL: the ordinary chat follows the global switch, and
        the route must not start reporting every chat as overridden."""
        chat_id = make_chat(client, make_character(client))

        body = client.get(f"{API}/auto-accept?chat_id={chat_id}").json()
        with get_db() as con:
            decided = notebook.auto_accept_for(con, chat_id)

        assert decided is True
        assert body["effective"] is True
        assert body["overridden"] is False

    def test_without_a_chat_it_answers_the_global_question(self, client):
        body = client.get(f"{API}/auto-accept").json()
        assert body["enabled"] is True
        assert body["effective"] is True
        assert body["overridden"] is False


class TestTheEscapeHatch:
    def test_a_chat_can_be_told_to_hold_suggestions(self, client) -> None:
        chat_id = make_chat(client, make_character(client))

        r = client.post(f"{API}/{chat_id}/auto-accept", json={"enabled": False})

        assert r.status_code == 200, r.text
        with get_db() as con:
            assert notebook.auto_accept_for(con, chat_id) is False

    def test_an_imported_chat_cannot_be_handed_back(self, client) -> None:
        """The shield an imported card gets is ABSOLUTE, and this test used
        to say the opposite.

        README and SECURITY promise, inside their SHA-256 locked sections,
        that a chat opened from an imported card always requires approval
        regardless of the setting. This route arrived without a guard, so the
        promise held everywhere except through the one door built to change
        it - and this test pinned the hole in place.

        A DELIBERATE CONTRACT CHANGE, decided on 31 August 2026, not an
        application defect: the alternative was to reword a locked privacy
        promise, and the owner kept the promise. `null` is refused alongside
        `true` because the global default is ON, so handing the chat back
        lowers the shield just as surely.
        """
        chat_id = make_chat(client, imported_character(client))
        with get_db() as con:
            assert notebook.auto_accept_for(con, chat_id) is False

        for body in ({"enabled": None}, {"enabled": True}):
            r = client.post(f"{API}/{chat_id}/auto-accept", json=body)
            assert r.status_code == 400, (body, r.text)
            assert r.json()["detail"] == "imported_chat_always_reviews"

        with get_db() as con:
            assert notebook.auto_accept_for(con, chat_id) is False

    def test_an_ordinary_chat_can_still_be_handed_back(self, client) -> None:
        """GROUND CONTROL. The refusal is about imported cards, not about the
        route - a guard that refused everything would pass the test above and
        take the escape hatch away from every chat."""
        chat_id = make_chat(client, make_character(client))

        assert client.post(f"{API}/{chat_id}/auto-accept",
                           json={"enabled": False}).status_code == 200
        with get_db() as con:
            assert notebook.auto_accept_for(con, chat_id) is False

        r = client.post(f"{API}/{chat_id}/auto-accept", json={"enabled": None})

        assert r.status_code == 200, r.text
        with get_db() as con:
            assert notebook.auto_accept_for(con, chat_id) is True

    def test_the_hatch_still_closes_on_a_chat_it_cannot_see(self, client):
        """And the direction the hatch actually exists for still works.

        The case the import signal cannot see: somebody pastes a downloaded
        card's fields into the form by hand, so `raw_json` stays `{}` and
        nothing in the app can know the text came from outside. The reader
        knows, and this is where they say so.
        """
        chat_id = make_chat(client, make_character(client))
        with get_db() as con:
            assert notebook.auto_accept_for(con, chat_id) is True, "ground"

        r = client.post(f"{API}/{chat_id}/auto-accept", json={"enabled": False})

        assert r.status_code == 200, r.text
        with get_db() as con:
            assert notebook.auto_accept_for(con, chat_id) is False

    def test_a_chat_that_does_not_exist_is_refused(self, client) -> None:
        r = client.post(f"{API}/999999/auto-accept", json={"enabled": False})
        assert r.status_code == 404, r.text
        assert r.json()["detail"] == "chat_not_found"

    def test_the_indicator_follows_the_hatch(self, client) -> None:
        """The two halves together: setting it changes what the panel reads,
        because both come from the same function now."""
        chat_id = make_chat(client, make_character(client))
        client.post(f"{API}/{chat_id}/auto-accept", json={"enabled": False})

        body = client.get(f"{API}/auto-accept?chat_id={chat_id}").json()

        assert body["effective"] is False
        assert body["overridden"] is True
        assert body["enabled"] is True, "the global switch is untouched"


class TestTheBackfill:
    def test_an_old_chat_from_an_imported_card_gets_the_shield(self, client):
        """The migration's sibling eleven lines up backfills; this one did
        not, so every chat that predates the column - including the ones the
        override exists for - fell through to a global switch that defaults
        to ON."""
        import database

        char_id = imported_character(client)
        chat_id = make_chat(client, char_id)
        # Exactly the state an old vault is in: the column exists, the row
        # is NULL, and the backfill has never run - which is what the flag
        # records, so clearing it is what makes this an old vault.
        with get_db() as con:
            con.execute("UPDATE chats SET notebook_auto_accept_override = NULL "
                        "WHERE id = ?", (chat_id,))
            con.execute("DELETE FROM settings WHERE key = ?",
                        (database._AUTO_ACCEPT_BACKFILL_KEY,))
            assert notebook.auto_accept_for(con, chat_id) is True, (
                "ground: unbackfilled, it follows the global switch")

        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            database._migrate_notebook_backfill(con)

        with get_db() as con:
            assert notebook.auto_accept_for(con, chat_id) is False

    def test_it_leaves_a_hand_written_character_alone(self, client) -> None:
        """GROUND CONTROL: the backfill must not force review on every chat
        in the vault, only the ones whose text came from a card."""
        import database

        chat_id = make_chat(client, make_character(client))

        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute("DELETE FROM settings WHERE key = ?",
                        (database._AUTO_ACCEPT_BACKFILL_KEY,))
            database._migrate_notebook_backfill(con)

        with get_db() as con:
            assert notebook.auto_accept_for(con, chat_id) is True

    def test_it_runs_once_and_does_not_undo_a_later_choice(self, client):
        """The reason it is guarded by a flag rather than by the column.

        Handing a chat back to the global switch writes NULL - the same NULL
        the backfill looks for. Run on every launch it would quietly undo
        that at the next start, and there would be no way to make the choice
        stick.
        """
        import database

        char_id = imported_character(client)
        chat_id = make_chat(client, char_id)
        with get_db() as con:
            assert notebook.auto_accept_for(con, chat_id) is False

        # The reader releases it - on a chat the shield does not apply to,
        # since an imported one can no longer be released at all. The
        # backfill's question is the same either way: does a NULL somebody
        # CHOSE survive the next launch?
        ordinary = make_chat(client, make_character(client))
        assert client.post(
            f"{API}/{ordinary}/auto-accept",
            json={"enabled": False}).status_code == 200
        assert client.post(
            f"{API}/{ordinary}/auto-accept",
            json={"enabled": None}).status_code == 200

        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            database._migrate_notebook_backfill(con)

        with get_db() as con:
            assert notebook.auto_accept_for(con, ordinary) is True, (
                "the backfill undid a choice the reader had made")
            # And the imported chat keeps its shield throughout.
            assert notebook.auto_accept_for(con, chat_id) is False

    def test_the_migration_itself_runs_it(self, client) -> None:
        """The wiring, not just the function.

        The three tests above call `_migrate_notebook_backfill` directly, so
        none of them notices if the migration stops calling it - which would
        leave the backfill perfectly correct and never executed on a single
        real vault.
        """
        import database

        char_id = imported_character(client)
        chat_id = make_chat(client, char_id)
        with get_db() as con:
            con.execute("UPDATE chats SET notebook_auto_accept_override = NULL "
                        "WHERE id = ?", (chat_id,))
            con.execute("DELETE FROM settings WHERE key = ?",
                        (database._AUTO_ACCEPT_BACKFILL_KEY,))
            assert notebook.auto_accept_for(con, chat_id) is True, (
                "ground: an old vault, unbackfilled")

        # What every launch does.
        database.init_db()

        with get_db() as con:
            assert notebook.auto_accept_for(con, chat_id) is False

