"""U-30 - a call from one chat cannot reach another chat's notebook.

The READ side of this module has had a scope gate since it was written:
`list_boundaries` and `uses_global_boundaries` both take a chat and use it.
The WRITE side did not. Accept, patch and delete took the primary key as the
whole identity, so any note in the vault was reachable from any chat by id -
and two of those routes return the row, so an unscoped write was an unscoped
READ as well.

The second half is quieter and worse in its own way: `set_use_global_boundaries`
never looked at `rowcount`, so pointing it at a chat that does not exist
matched nothing and answered `{"ok": true}` - a caller told that a safety
setting had been applied to a conversation that was not there.
"""
from __future__ import annotations

import notebook_store as notebook
from database import get_db

from tests.conftest import make_character, make_chat

API = "/api/v1/notebook"


def two_chats(client) -> tuple[int, int]:
    char_id = make_character(client)
    return make_chat(client, char_id), make_chat(client, char_id)


def a_note(chat_id: int, text: str = "The mill is her family's.") -> dict:
    return notebook.create_entry(chat_id, text)


def a_proposal(chat_id: int) -> dict:
    return notebook.create_entry(
        chat_id, "A suggestion nobody has read.",
        provenance=notebook.PROV_MODEL, status=notebook.STATUS_PROPOSED)


def text_of(entry_id: int) -> str:
    with get_db() as con:
        return con.execute(
            "SELECT text FROM notebook_entries WHERE id = ?",
            (entry_id,)).fetchone()[0]


class TestAnotherChatsNote:
    def test_it_cannot_be_accepted(self, client) -> None:
        mine, theirs = two_chats(client)
        note = a_proposal(mine)

        r = client.post(f"{API}/entries/{note['id']}/accept?chat_id={theirs}")

        assert r.status_code == 404, r.text
        # And the row is untouched: still a proposal, in its own chat.
        rows = {e["id"]: e for e in notebook.list_entries(mine)}
        assert rows[note["id"]]["status"] == notebook.STATUS_PROPOSED

    def test_it_cannot_be_edited(self, client) -> None:
        mine, theirs = two_chats(client)
        note = a_note(mine)

        r = client.patch(f"{API}/entries/{note['id']}?chat_id={theirs}",
                         json={"text": "edited from somewhere else"})

        assert r.status_code == 404, r.text
        assert text_of(note["id"]) == "The mill is her family's."

    def test_it_cannot_be_deleted(self, client) -> None:
        mine, theirs = two_chats(client)
        note = a_note(mine)

        r = client.delete(f"{API}/entries/{note['id']}?chat_id={theirs}")

        assert r.status_code == 404, r.text
        assert note["id"] in {e["id"] for e in notebook.list_entries(mine)}

    def test_the_refusal_does_not_hand_back_the_text(self, client) -> None:
        """The half that is a READ.

        Accept and patch both return the row, so an unscoped write was also a
        way to ask "what does note 412 say" about a chat the caller never
        opened. The refusal must not answer that question either.
        """
        mine, theirs = two_chats(client)
        secret = a_note(mine, "Her real name is on the mill deed.")
        proposal = a_proposal(mine)

        for r in (
            client.patch(f"{API}/entries/{secret['id']}?chat_id={theirs}",
                         json={"text": "x"}),
            client.post(f"{API}/entries/{proposal['id']}/accept"
                        f"?chat_id={theirs}"),
        ):
            assert "mill deed" not in r.text
            assert "nobody has read" not in r.text


class TestTheSameCallsFromTheRightChat:
    def test_accept_patch_and_delete_all_work(self, client) -> None:
        """GROUND CONTROL. Without it every assertion above is satisfied by
        an application that refuses everything."""
        mine, _ = two_chats(client)
        proposal = a_proposal(mine)
        note = a_note(mine)

        accepted = client.post(
            f"{API}/entries/{proposal['id']}/accept?chat_id={mine}")
        edited = client.patch(f"{API}/entries/{note['id']}?chat_id={mine}",
                              json={"text": "edited here"})
        removed = client.delete(f"{API}/entries/{note['id']}?chat_id={mine}")

        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["status"] == notebook.STATUS_ACCEPTED
        assert edited.status_code == 200, edited.text
        assert removed.status_code == 200, removed.text
        assert note["id"] not in {e["id"] for e in notebook.list_entries(mine)}


class TestALimitsScope:
    def test_a_chat_scoped_limit_cannot_be_deleted_from_elsewhere(self, client):
        mine, theirs = two_chats(client)
        row = notebook.create_boundary("just here", "Only in this chat.",
                                       "soft", chat_id=mine)

        r = client.delete(f"{API}/boundaries/{row['id']}?chat_id={theirs}")

        assert r.status_code == 404, r.text
        assert row["id"] in {b["id"] for b in notebook.list_boundaries(mine)}

    def test_a_global_limit_can_be_deleted_from_anywhere(self, client) -> None:
        """GROUND CONTROL, and the reason the scope is optional on this one:
        a global limit belongs to no chat, so demanding one would mean
        inventing a scope for a row that has none."""
        mine, theirs = two_chats(client)
        row = notebook.create_boundary("no gore", "Avoid graphic injury.",
                                       "hard")

        r = client.delete(f"{API}/boundaries/{row['id']}?chat_id={theirs}")

        assert r.status_code == 200, r.text
        assert row["id"] not in {b["id"] for b in notebook.list_boundaries(mine)}


class TestAChatThatDoesNotExist:
    def test_use_global_refuses_instead_of_answering_ok(self, client) -> None:
        r = client.post(f"{API}/999999/use-global", json={"use_global": False})

        assert r.status_code == 404, r.text
        assert r.json()["detail"] == "chat_not_found"

    def test_a_real_chat_still_works(self, client) -> None:
        """GROUND CONTROL for the rowcount check."""
        mine, _ = two_chats(client)

        r = client.post(f"{API}/{mine}/use-global", json={"use_global": False})

        assert r.status_code == 200, r.text
        assert r.json() == {"ok": True, "use_global": False}
        assert notebook.uses_global_boundaries(mine) is False


class TestTheRefusalNamesWhatIsActuallyMissing:
    """404 is not the whole answer - the CODE is what reaches the reader.

    The chat-scope guard raised `chat_not_found` for every refusal, and the
    frontend renders that as "This chat no longer exists." The chat in the
    query parameter is right there in every one of these cases; it is the
    note, or the limit, that is not in it. The reader was sent looking for
    the wrong thing.

    For the limit it was a regression as well as a wording error. Deleting a
    limit somebody already removed in another window takes this same branch,
    and that case has a sentence of its own - "It may have been removed in
    another window." - which stopped rendering the moment the code changed.
    """

    def test_a_note_in_another_chat_is_not_a_missing_chat(self, client):
        mine, theirs = two_chats(client)
        row = notebook.create_entry(mine, "Only in this chat.")

        r = client.delete(f"{API}/entries/{row['id']}?chat_id={theirs}")

        assert r.status_code == 404, r.text
        assert r.json()["detail"] == "notebook_entry_not_found"

    def test_patching_one_says_the_same_thing(self, client) -> None:
        mine, theirs = two_chats(client)
        row = notebook.create_entry(mine, "Only in this chat.")

        r = client.patch(f"{API}/entries/{row['id']}?chat_id={theirs}",
                         json={"pinned": True})

        assert r.status_code == 404, r.text
        assert r.json()["detail"] == "notebook_entry_not_found"

    def test_a_limit_that_is_already_gone_says_so(self, client) -> None:
        """The regression, stated as the case that actually happens: two
        windows open, one of them deletes the limit first."""
        mine, _ = two_chats(client)
        row = notebook.create_boundary("just here", "Only in this chat.",
                                       "soft", chat_id=mine)
        assert notebook.delete_boundary(row["id"], chat_id=mine), "ground"

        r = client.delete(f"{API}/boundaries/{row['id']}?chat_id={mine}")

        assert r.status_code == 404, r.text
        assert r.json()["detail"] == "boundary_not_found"

    def test_the_chat_really_is_still_there(self, client) -> None:
        """GROUND CONTROL for all three. If the chat had actually vanished,
        `chat_not_found` would be the right answer and these tests would be
        arguing for a worse message rather than a truer one."""
        mine, theirs = two_chats(client)
        row = notebook.create_entry(mine, "Only in this chat.")
        client.delete(f"{API}/entries/{row['id']}?chat_id={theirs}")

        assert client.get(f"/api/v1/chats/{theirs}").status_code == 200
        assert client.get(f"/api/v1/chats/{mine}").status_code == 200



class TestAnOmittedScopeIsNotALiftedScope:
    """`DELETE /notebook/boundaries/{id}` with no `chat_id` deleted another
    chat's safety limit.

    The guard ran only `if chat_id is not None`. Sending the WRONG chat was
    refused; sending NONE went straight through - a scope every caller opts
    out of by not typing it, which is word for word the reason
    `accept_entry` made its own `chat_id` required. And the row it removes
    is a limit: the thing the reader wrote down to stop the model doing
    something.
    """

    def test_a_chat_scoped_limit_is_not_deletable_without_its_chat(
            self, client) -> None:
        from tests.conftest import make_character, make_chat

        chat_id = make_chat(client, make_character(client))
        made = client.post("/api/v1/notebook/boundaries", json={
            "label": "off screen",
            "phrasing": "no one dies off screen",
            "severity": "soft",
            "chat_id": chat_id,
        })
        assert made.status_code == 200, made.text
        bid = made.json()["id"]

        # GROUND CONTROL: it is really there, and really scoped to the chat.
        listed = client.get(f"/api/v1/notebook/{chat_id}/boundaries")
        assert [b["id"] for b in listed.json()["boundaries"]] == [bid]

        gone = client.delete(f"/api/v1/notebook/boundaries/{bid}")

        assert gone.status_code == 404, gone.text
        assert gone.json()["detail"] == "boundary_not_found"
        still = client.get(f"/api/v1/notebook/{chat_id}/boundaries")
        assert [b["id"] for b in still.json()["boundaries"]] == [bid], (
            "a limit belonging to a chat was removed by a request that "
            "named no chat at all")

    def test_it_is_still_deletable_from_its_own_chat(self, client) -> None:
        """POSITIVE CONTROL. The scope must not become "undeletable"."""
        from tests.conftest import make_character, make_chat

        chat_id = make_chat(client, make_character(client))
        bid = client.post("/api/v1/notebook/boundaries", json={
            "label": "off screen",
            "phrasing": "no one dies off screen",
            "severity": "soft",
            "chat_id": chat_id,
        }).json()["id"]

        gone = client.delete(
            f"/api/v1/notebook/boundaries/{bid}?chat_id={chat_id}")

        assert gone.status_code == 200, gone.text
        assert client.get(
            f"/api/v1/notebook/{chat_id}/boundaries").json()["boundaries"] == []

    def test_a_global_limit_still_needs_no_chat(self, client) -> None:
        """The whole reason the parameter is optional.

        A global limit belongs to no chat, so demanding one would mean
        inventing a scope for a row that has none. If the fix had simply
        made `chat_id` required, this is what would have broken.
        """
        bid = client.post("/api/v1/notebook/boundaries", json={
            "label": "second person",
            "phrasing": "never write in the second person",
            "severity": "soft",
        }).json()["id"]

        gone = client.delete(f"/api/v1/notebook/boundaries/{bid}")

        assert gone.status_code == 200, gone.text
        assert client.get(
            "/api/v1/notebook/boundaries").json()["boundaries"] == []


class TestAChatThatIsNotThere:
    def test_the_notes_route_answers_404_rather_than_a_traceback(
            self, client) -> None:
        """It was the one route in the file that answered 500.

        `list_entries` returns `[]` without a chat check; the block builder
        underneath it raises `chat_not_found`, uncaught. The reader got
        Starlette's plain-text `Internal Server Error` - not JSON, no code -
        and the frontend fell through to the generic toast. Reached by an
        ordinary race: a chat deleted in one window while the notebook panel
        refreshes in another.
        """
        missing = client.get("/api/v1/notebook/999999")

        assert missing.status_code == 404, missing.text
        assert missing.json()["detail"] == "chat_not_found"

    def test_a_chat_that_IS_there_still_answers(self, client) -> None:
        """GROUND CONTROL: the guard did not turn the route off."""
        from tests.conftest import make_character, make_chat

        chat_id = make_chat(client, make_character(client))
        ok = client.get(f"/api/v1/notebook/{chat_id}")
        assert ok.status_code == 200, ok.text
        assert ok.json()["entries"] == []
        assert "notebook_chars" in ok.json()
