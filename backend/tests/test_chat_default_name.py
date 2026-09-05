"""What a chat is called when nobody said - over the real HTTP surface.

The generated title used to be `<character> - <UTC minute>`, so three chats
opened inside the same minute were byte-for-byte identical and the sidebar
showed three rows nobody could tell apart. That was measured, not guessed.

Every expected string here is built from `default_names`, never retyped.
"""
from __future__ import annotations

import default_names
from conftest import make_character


def _new_chat(client, character_id: int, **body) -> dict:
    resp = client.post("/api/v1/chats",
                       json={"character_id": character_id, **body})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _titles(client) -> list[str]:
    resp = client.get("/api/v1/chats")
    assert resp.status_code == 200, resp.text
    return [row["title"] for row in resp.json()]


def test_unnamed_chats_are_numbered_in_order(client):
    char_id = make_character(client)

    first = _new_chat(client, char_id)
    second = _new_chat(client, char_id)
    third = _new_chat(client, char_id)

    assert [first["title"], second["title"], third["title"]] == [
        f"{default_names.CHAT} 1",
        f"{default_names.CHAT} 2",
        f"{default_names.CHAT} 3",
    ]


def test_three_unnamed_chats_in_a_row_are_three_different_names(client):
    """The defect this replaced, stated as its own assertion. The old scheme
    produced one distinct name out of three inside a single minute."""
    char_id = make_character(client)

    titles = [_new_chat(client, char_id)["title"] for _ in range(3)]

    assert len(set(titles)) == 3, titles


def test_a_deleted_number_is_handed_out_again(client):
    """Smallest free, not max+1."""
    char_id = make_character(client)
    _new_chat(client, char_id)
    second = _new_chat(client, char_id)
    _new_chat(client, char_id)

    assert client.delete(f"/api/v1/chats/{second['id']}").status_code == 200

    assert _new_chat(client, char_id)["title"] == f"{default_names.CHAT} 2"


def test_a_hand_typed_number_is_not_walked_over(client):
    """A chat somebody named "New Chat 5" holds 5, and the next unnamed one
    takes the lowest free number rather than 6."""
    char_id = make_character(client)
    _new_chat(client, char_id, title=f"{default_names.CHAT} 5")

    assert _new_chat(client, char_id)["title"] == f"{default_names.CHAT} 1"


def test_a_differently_cased_name_still_holds_its_number(client):
    """The columns are plain TEXT, so SQLite would happily store both
    spellings side by side; the scan is what stops two rows reading alike."""
    char_id = make_character(client)
    _new_chat(client, char_id, title="new chat 1")

    assert _new_chat(client, char_id)["title"] == f"{default_names.CHAT} 2"


def test_every_shape_of_empty_gets_a_name(client):
    """Empty string, spaces, explicit null and the key left out entirely -
    all four used to take four different paths through this route."""
    char_id = make_character(client)

    titles = [
        _new_chat(client, char_id, title="")["title"],
        _new_chat(client, char_id, title="   ")["title"],
        _new_chat(client, char_id, title=None)["title"],
        _new_chat(client, char_id)["title"],
    ]

    assert titles == [f"{default_names.CHAT} {n}" for n in (1, 2, 3, 4)]


def test_an_emptied_rename_box_gets_a_name(client):
    """The rename half. A person clearing the box is saying they no longer
    want the name they had, and the answer to that is a name they can change
    again rather than a refusal."""
    char_id = make_character(client)
    chat = _new_chat(client, char_id, title="A night out")

    resp = client.patch(f"/api/v1/chats/{chat['id']}", json={"title": "   "})

    assert resp.status_code == 200, resp.text
    assert resp.json()["title"] == f"{default_names.CHAT} 1"


def test_a_rename_does_not_count_the_name_it_is_replacing(client):
    """The row being renamed is left out of the scan. Its old title is on its
    way out, so letting it hold a number would push the replacement along for
    no reason a reader could see."""
    char_id = make_character(client)
    chat = _new_chat(client, char_id)
    assert chat["title"] == f"{default_names.CHAT} 1"

    resp = client.patch(f"/api/v1/chats/{chat['id']}", json={"title": ""})

    assert resp.status_code == 200, resp.text
    assert resp.json()["title"] == f"{default_names.CHAT} 1"


def test_a_rename_with_no_title_key_at_all_is_still_malformed(client):
    """An emptied box is a rename; a body with no title in it is not a rename
    with nothing in it, it is a broken request."""
    char_id = make_character(client)
    chat = _new_chat(client, char_id)

    assert client.patch(f"/api/v1/chats/{chat['id']}", json={}) \
        .status_code == 422


def test_a_title_that_was_given_is_stored_exactly(client):
    """GROUND CONTROL. Everything above is about the empty box; without this,
    a route that replaced EVERY title with a generated one would pass all of
    it."""
    char_id = make_character(client)

    chat = _new_chat(client, char_id, title="  A night with the sea  ")

    assert chat["title"] == "A night with the sea"
    assert "A night with the sea" in _titles(client)


def test_unrelated_names_do_not_advance_the_sequence(client):
    """POSITIVE CONTROL. Names a person chose are not part of the sequence,
    so the first generated name is still 1."""
    char_id = make_character(client)
    _new_chat(client, char_id, title="A night with Aria")
    _new_chat(client, char_id, title="work notes")

    assert _new_chat(client, char_id)["title"] == f"{default_names.CHAT} 1"


def test_the_length_ceiling_still_refuses(client):
    """The cap did not move. A generated name cannot reach it, but a typed
    one still can."""
    char_id = make_character(client)

    resp = client.post("/api/v1/chats",
                       json={"character_id": char_id, "title": "x" * 201})

    assert resp.status_code == 400
    assert resp.json()["detail"] == "title_too_long"
