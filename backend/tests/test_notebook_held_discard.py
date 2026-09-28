"""A held discard deletes a note only while it is still a suggestion.

The Notes tab holds a Discard back (the row offers Undo) and sends the
DELETE when the panel leaves the chat. In between the reader may have kept,
pinned or edited the note - from this tab or another window. Then the
discard no longer stands: the note stays and the answer says `kept`. The
check runs in the same transaction as the delete (notebook_store).
"""
from __future__ import annotations

import pytest

import notebook_store as notebook
from tests.conftest import make_character, make_chat

API = "/api/v1/notebook"


def a_chat(client) -> int:
    return make_chat(client, make_character(client))


def a_suggestion(chat_id: int) -> dict:
    return notebook.create_entry(
        chat_id, "A suggestion nobody has read.",
        provenance=notebook.PROV_MODEL, status=notebook.STATUS_PROPOSED)


def ids_in(chat_id: int) -> set[int]:
    return {e["id"] for e in notebook.list_entries(chat_id)}


def test_a_still_suggested_note_is_removed(client) -> None:
    # GROUND: the held discard does delete what it was held for.
    chat = a_chat(client)
    note = a_suggestion(chat)
    r = client.delete(f"{API}/entries/{note['id']}?chat_id={chat}&only_suggestion=true")
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}
    assert note["id"] not in ids_in(chat)


@pytest.mark.parametrize("change", ["accept", "pin", "edit"])
def test_a_note_kept_pinned_or_edited_since_is_left_alone(client, change) -> None:
    chat = a_chat(client)
    note = a_suggestion(chat)
    if change == "accept":
        assert client.post(f"{API}/entries/{note['id']}/accept?chat_id={chat}").status_code == 200
    elif change == "pin":
        assert client.patch(f"{API}/entries/{note['id']}?chat_id={chat}",
                            json={"pinned": True}).status_code == 200
    else:
        assert client.patch(f"{API}/entries/{note['id']}?chat_id={chat}",
                            json={"text": "The reader's own words now."}).status_code == 200
    r = client.delete(f"{API}/entries/{note['id']}?chat_id={chat}&only_suggestion=true")
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "kept": True}
    assert note["id"] in ids_in(chat)


def test_a_plain_delete_still_removes_a_kept_note(client) -> None:
    # POSITIVE CONTROL: the condition belongs to the held discard only; the
    # row's own Delete of an accepted note works as before.
    chat = a_chat(client)
    note = a_suggestion(chat)
    client.post(f"{API}/entries/{note['id']}/accept?chat_id={chat}")
    r = client.delete(f"{API}/entries/{note['id']}?chat_id={chat}")
    assert r.status_code == 200
    assert note["id"] not in ids_in(chat)


def test_another_chats_note_is_still_refused(client) -> None:
    mine, theirs = a_chat(client), a_chat(client)
    note = a_suggestion(mine)
    notebook.update_entry(note["id"], mine, pinned=True)
    r = client.delete(f"{API}/entries/{note['id']}?chat_id={theirs}&only_suggestion=true")
    assert r.status_code == 404
    assert note["id"] in ids_in(mine)
