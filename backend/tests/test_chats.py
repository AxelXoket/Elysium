"""Tests for chat management endpoints - currently the rename flow."""

from conftest import make_character, make_chat


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
