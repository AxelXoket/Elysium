"""Tests for chat management endpoints: rename, and the destructive pair."""

from conftest import make_character, make_chat, get_messages


def _get_chat(client, chat_id: int) -> dict:
    resp = client.get(f"/api/v1/chats/{chat_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_rename_chat_happy_path(client):
    char_id = make_character(client)
    chat_id = make_chat(client, char_id)
    before = _get_chat(client, chat_id)

    resp = client.patch(
        f"/api/v1/chats/{chat_id}", json={"title": "  Yeni Başlık  "}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # Trimmed title, full chat shape, updated_at bumped or equal-formatted.
    assert data["title"] == "Yeni Başlık"
    assert data["id"] == chat_id
    assert set(data.keys()) == {
        "id", "character_id", "character_name", "title", "model_id",
        "created_at", "updated_at", "message_count",
    }
    assert data["message_count"] == before["message_count"]
    assert _get_chat(client, chat_id)["title"] == "Yeni Başlık"


def test_rename_chat_empty_title_rejected(client):
    char_id = make_character(client)
    chat_id = make_chat(client, char_id)
    before_title = _get_chat(client, chat_id)["title"]

    resp = client.patch(f"/api/v1/chats/{chat_id}", json={"title": "   "})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "title_required"
    assert _get_chat(client, chat_id)["title"] == before_title


def test_rename_chat_too_long_rejected(client):
    char_id = make_character(client)
    chat_id = make_chat(client, char_id)

    resp = client.patch(
        f"/api/v1/chats/{chat_id}", json={"title": "x" * 201}
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "title_too_long"

    # Exactly at the limit is fine.
    resp = client.patch(
        f"/api/v1/chats/{chat_id}", json={"title": "x" * 200}
    )
    assert resp.status_code == 200


def test_rename_chat_not_found(client):
    resp = client.patch("/api/v1/chats/99999", json={"title": "Anything"})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "chat_not_found"


# ---------------------------------------------------------------------------
# The neighbour
#
# Added 2026-08-10. The suite proved a great deal about deleting a chat - that
# its rows go, that a blob shared with another chat survives - and never once
# asked what happened to the OTHER chat's own messages. Every DELETE in
# chats.py is scoped by chat_id today; drop one of those predicates and the
# blob tests still pass, because the blob is shared and survives either way.
# These two are the negative side: somebody else's conversation is still there.
# ---------------------------------------------------------------------------


def _seeded_pair(client) -> tuple[int, int]:
    """Two chats under one character, each carrying its own greeting row."""
    char_id = make_character(client, first_mes="hello from the card")
    return make_chat(client, char_id), make_chat(client, char_id)


def test_deleting_a_chat_leaves_the_other_ones_messages_alone(client):
    doomed, neighbour = _seeded_pair(client)
    before = get_messages(client, neighbour)
    assert before, "the neighbour started with nothing, so this proves nothing"

    assert client.delete(f"/api/v1/chats/{doomed}").status_code in (200, 204)

    assert client.get(f"/api/v1/chats/{doomed}").status_code == 404
    assert get_messages(client, neighbour) == before


def test_clearing_a_chat_leaves_the_other_ones_messages_alone(client):
    cleared, neighbour = _seeded_pair(client)
    before = get_messages(client, neighbour)
    assert before, "the neighbour started with nothing, so this proves nothing"

    resp = client.post(f"/api/v1/chats/{cleared}/clear")
    assert resp.status_code in (200, 204), resp.text

    # The cleared one really was cleared - otherwise a no-op clear would pass
    # the neighbour check trivially.
    assert get_messages(client, cleared) == []
    assert get_messages(client, neighbour) == before
