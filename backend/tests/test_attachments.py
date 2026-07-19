"""Tests for image attachments (Part H): upload, linking, payload, cascade."""

import io

import pytest
from PIL import Image

from conftest import make_character, make_chat, get_messages


def make_png(width=8, height=8, color=(200, 30, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


def upload(client, data: bytes, mime="image/png", name="t.png") -> dict:
    resp = client.post(
        "/api/v1/uploads/images",
        files={"file": (name, data, mime)},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


VISION_META = {
    "id": "test/model-1",
    "context_length": 32768,
    "max_completion_tokens": 4096,
    "input_modalities": ["text", "image"],
    "supported_parameters": [],
}

TEXT_ONLY_META = {**VISION_META, "input_modalities": ["text"]}


@pytest.fixture()
def vision_model(monkeypatch):
    import routers.completions as completions_router
    monkeypatch.setattr(
        completions_router, "get_cached_model_metadata", lambda mid: VISION_META,
    )


@pytest.fixture()
def text_only_model(monkeypatch):
    import routers.completions as completions_router
    monkeypatch.setattr(
        completions_router, "get_cached_model_metadata", lambda mid: TEXT_ONLY_META,
    )


# ---------------------------------------------------------------------------
# Upload + serve
# ---------------------------------------------------------------------------

def test_upload_and_serve_roundtrip(client):
    meta = upload(client, make_png())
    assert meta["mime"] == "image/png"
    assert meta["width"] == 8 and meta["height"] == 8

    resp = client.get(f"/api/v1/uploads/images/{meta['id']}")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    served = Image.open(io.BytesIO(resp.content))
    assert served.size == (8, 8)


def test_upload_rejects_non_image(client):
    resp = client.post(
        "/api/v1/uploads/images",
        files={"file": ("evil.png", b"definitely not an image", "image/png")},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "attachment_invalid"


def test_upload_downscales_oversized(client):
    big = make_png(3000, 1500)
    meta = upload(client, big)
    assert max(meta["width"], meta["height"]) == 2048
    assert meta["height"] == 1024  # aspect preserved


def test_upload_dedupes_identical_files(client):
    data = make_png(color=(1, 2, 3))
    a = upload(client, data)
    b = upload(client, data)
    assert a["id"] != b["id"]  # separate staged rows...

    import attachments_service
    ra = attachments_service.get_attachment(a["id"])
    rb = attachments_service.get_attachment(b["id"])
    assert ra["sha256"] == rb["sha256"]  # ...sharing one content-addressed file


def test_serve_unknown_404(client):
    resp = client.get("/api/v1/uploads/images/99999")
    assert resp.status_code == 404


def test_upload_rejects_decompression_bomb(client):
    """A tiny solid PNG that decodes above the pixel ceiling must be rejected
    with 400 attachment_invalid, not crash with a 500."""
    # 6000x6000 = 36M px > MAX_IMAGE_PIXELS (32M); a solid color compresses to
    # a few KB, so it passes the byte cap and only trips on decode.
    buf = io.BytesIO()
    Image.new("RGB", (6000, 6000), (0, 0, 0)).save(buf, format="PNG")
    resp = client.post(
        "/api/v1/uploads/images",
        files={"file": ("bomb.png", buf.getvalue(), "image/png")},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "attachment_invalid"


# ---------------------------------------------------------------------------
# Completion payload + linking
# ---------------------------------------------------------------------------

def test_complete_with_attachment_builds_image_parts(client, provider, vision_model):
    char_id = make_character(client)
    chat_id = make_chat(client, char_id)
    att = upload(client, make_png())

    resp = client.post(f"/api/v1/chats/{chat_id}/complete", json={
        "message": "Look at this",
        "model_id": "test/model-1",
        "attachments": [att["id"]],
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["user_message"]["attachments"] == [
        {"id": att["id"], "mime": "image/png", "width": 8, "height": 8}
    ]
    assert data["assistant_message"]["attachments"] == []

    # Provider payload: last user turn is a parts array with a data URL.
    call = provider.calls[-1]
    user_turn = [m for m in call["messages"] if m["role"] == "user"][-1]
    assert isinstance(user_turn["content"], list)
    kinds = [p["type"] for p in user_turn["content"]]
    assert kinds == ["text", "image_url"]
    assert user_turn["content"][1]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )

    # Messages API returns the attachment metadata.
    msgs = get_messages(client, chat_id)
    user_rows = [m for m in msgs if m["role"] == "user"]
    assert user_rows[-1]["attachments"][0]["id"] == att["id"]

    # Linked id can no longer be reused.
    resp = client.post(f"/api/v1/chats/{chat_id}/complete", json={
        "message": "again",
        "model_id": "test/model-1",
        "attachments": [att["id"]],
    })
    assert resp.status_code == 400
    assert resp.json()["detail"] == "attachment_unavailable"


def test_history_images_ride_along_for_vision_models(client, provider, vision_model):
    char_id = make_character(client)
    chat_id = make_chat(client, char_id)
    att = upload(client, make_png())
    client.post(f"/api/v1/chats/{chat_id}/complete", json={
        "message": "first with image",
        "model_id": "test/model-1",
        "attachments": [att["id"]],
    })

    resp = client.post(f"/api/v1/chats/{chat_id}/complete", json={
        "message": "follow-up, no image",
        "model_id": "test/model-1",
    })
    assert resp.status_code == 200
    call = provider.calls[-1]
    history_user = [m for m in call["messages"] if m["role"] == "user"][0]
    assert isinstance(history_user["content"], list)  # image rode along
    current_user = [m for m in call["messages"] if m["role"] == "user"][-1]
    assert isinstance(current_user["content"], str)   # no pending image


def test_text_only_model_strips_history_images(client, provider, text_only_model):
    char_id = make_character(client)
    chat_id = make_chat(client, char_id)

    # Seed a linked image directly (bypassing the request gate) to simulate
    # history created earlier with a vision model.
    att = upload(client, make_png())
    import routers.completions as completions_router
    from database import get_db
    with get_db() as con:
        cur = con.execute(
            "INSERT INTO messages (chat_id, role, content) VALUES (?, 'user', ?)",
            (chat_id, "old image message"),
        )
        old_user_id = cur.lastrowid
        con.execute(
            "UPDATE attachments SET message_id = ? WHERE id = ?",
            (att["id"], old_user_id),
        )
        con.execute(
            "INSERT INTO messages (chat_id, role, content) VALUES (?, 'assistant', 'ok')",
            (chat_id,),
        )

    resp = client.post(f"/api/v1/chats/{chat_id}/complete", json={
        "message": "text only now",
        "model_id": "test/model-1",
    })
    assert resp.status_code == 200
    call = provider.calls[-1]
    # Every content in the payload is a plain string - images silently omitted.
    assert all(isinstance(m["content"], str) for m in call["messages"])


def test_text_only_model_gate(client, provider, text_only_model):
    char_id = make_character(client)
    chat_id = make_chat(client, char_id)
    att = upload(client, make_png())

    resp = client.post(f"/api/v1/chats/{chat_id}/complete", json={
        "message": "hi", "model_id": "test/model-1",
        "attachments": [att["id"]],
    })
    assert resp.status_code == 400
    assert resp.json()["detail"] == "model_no_image_input"


def test_attachment_gates(client, provider, vision_model):
    char_id = make_character(client)
    chat_id = make_chat(client, char_id)

    resp = client.post(f"/api/v1/chats/{chat_id}/complete", json={
        "message": "hi", "model_id": "test/model-1",
        "attachments": [1, 2, 3, 4, 5],
    })
    assert resp.status_code == 400
    assert resp.json()["detail"] == "too_many_attachments"

    resp = client.post(f"/api/v1/chats/{chat_id}/complete", json={
        "message": "hi", "model_id": "test/model-1",
        "attachments": [99999],
    })
    assert resp.status_code == 404
    assert resp.json()["detail"] == "attachment_not_found"


# ---------------------------------------------------------------------------
# Regenerate re-sends the user turn's images
# ---------------------------------------------------------------------------

def test_regenerate_resends_user_images(client, provider, vision_model):
    """The regenerate flow excludes the preceding user message from history
    and re-appends it as the current turn - its linked images must ride
    along again, exactly like a fresh send."""
    char_id = make_character(client)
    chat_id = make_chat(client, char_id)
    att = upload(client, make_png())

    resp = client.post(f"/api/v1/chats/{chat_id}/complete", json={
        "message": "what is in this image?",
        "model_id": "test/model-1",
        "attachments": [att["id"]],
    })
    assert resp.status_code == 200, resp.text
    asst_id = resp.json()["assistant_message"]["id"]

    resp = client.post(
        f"/api/v1/chats/{chat_id}/messages/{asst_id}/regenerate",
        json={"model_id": "test/model-1"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # The response's user_message reports its attachments (not an empty list).
    assert data["user_message"]["attachments"] == [
        {"id": att["id"], "mime": "image/png", "width": 8, "height": 8}
    ]

    # Provider payload: the re-sent user turn carries the image parts again.
    call = provider.calls[-1]
    user_turn = [m for m in call["messages"] if m["role"] == "user"][-1]
    assert isinstance(user_turn["content"], list)
    assert [p["type"] for p in user_turn["content"]] == ["text", "image_url"]
    assert user_turn["content"][1]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )


def test_regenerate_stream_resends_user_images(
    client, provider, vision_model, monkeypatch,
):
    import json as _json
    import routers.completions as completions_router

    char_id = make_character(client)
    chat_id = make_chat(client, char_id)
    att = upload(client, make_png())

    client.post(f"/api/v1/chats/{chat_id}/complete", json={
        "message": "img", "model_id": "test/model-1",
        "attachments": [att["id"]],
    })
    msgs = get_messages(client, chat_id)
    asst_id = [m for m in msgs if m["role"] == "assistant"][-1]["id"]

    captured: list[list[dict]] = []

    def fake_stream(messages, model_id, gen_params, provider_dict):
        captured.append(messages)
        async def gen():
            yield "regenerated "
            yield "reply"
        return gen()

    monkeypatch.setattr(completions_router, "complete_stream", fake_stream)

    with client.stream(
        "POST", f"/api/v1/chats/{chat_id}/messages/{asst_id}/regenerate/stream",
        json={"model_id": "test/model-1"},
    ) as resp:
        events = [
            _json.loads(line[len("data:"):].strip())
            for line in resp.iter_lines() if line.strip().startswith("data:")
        ]

    # user_message event and done both carry the attachment metadata.
    assert events[0]["type"] == "user_message"
    assert events[0]["message"]["attachments"][0]["id"] == att["id"]
    assert events[-1]["type"] == "done"
    assert events[-1]["user_message"]["attachments"][0]["id"] == att["id"]

    # Provider payload: image parts present on the re-sent user turn.
    user_turn = [m for m in captured[-1] if m["role"] == "user"][-1]
    assert isinstance(user_turn["content"], list)
    assert [p["type"] for p in user_turn["content"]] == ["text", "image_url"]


# ---------------------------------------------------------------------------
# Failure unlink + cascade cleanup
# ---------------------------------------------------------------------------

def test_stream_failure_unlinks_attachment_for_retry(client, vision_model, monkeypatch):
    import json as _json
    import routers.completions as completions_router
    from openrouter import OpenRouterError

    def failing_stream(messages, model_id, gen_params, provider):
        async def gen():
            raise OpenRouterError("openrouter_rate_limited")
            yield  # pragma: no cover
        return gen()

    monkeypatch.setattr(completions_router, "complete_stream", failing_stream)

    char_id = make_character(client)
    chat_id = make_chat(client, char_id)
    att = upload(client, make_png())

    with client.stream(
        "POST", f"/api/v1/chats/{chat_id}/complete/stream",
        json={"message": "img send", "model_id": "test/model-1",
              "attachments": [att["id"]]},
    ) as resp:
        events = [
            _json.loads(line[len("data:"):].strip())
            for line in resp.iter_lines() if line.strip().startswith("data:")
        ]
    assert events[-1]["type"] == "error"

    # User message rolled back; attachment back to staged → retry works.
    import attachments_service
    row = attachments_service.get_attachment(att["id"])
    assert row["message_id"] is None

    monkeypatch.setattr(
        completions_router, "get_cached_model_metadata", lambda mid: VISION_META,
    )


def _blob_exists(sha256: str) -> bool:
    import database

    with database.get_db() as con:
        row = con.execute(
            "SELECT 1 FROM attachment_blobs WHERE sha256 = ?", (sha256,)
        ).fetchone()
    return row is not None


def test_delete_chat_removes_attachment_rows_and_orphan_blobs(
    client, provider, vision_model,
):
    import attachments_service

    char_id = make_character(client)
    chat_id = make_chat(client, char_id)
    att = upload(client, make_png(color=(9, 9, 9)))
    client.post(f"/api/v1/chats/{chat_id}/complete", json={
        "message": "with image", "model_id": "test/model-1",
        "attachments": [att["id"]],
    })
    row = attachments_service.get_attachment(att["id"])
    assert _blob_exists(row["sha256"])

    resp = client.delete(f"/api/v1/chats/{chat_id}")
    assert resp.status_code == 200
    assert attachments_service.get_attachment(att["id"]) is None
    assert not _blob_exists(row["sha256"])  # orphan blob gone, same txn


def test_shared_blob_survives_partial_delete(client, provider, vision_model):
    import attachments_service

    data = make_png(color=(4, 5, 6))
    char_id = make_character(client)
    chat_a = make_chat(client, char_id)
    chat_b = make_chat(client, char_id)
    att_a = upload(client, data)
    att_b = upload(client, data)  # same sha256, second row

    client.post(f"/api/v1/chats/{chat_a}/complete", json={
        "message": "a", "model_id": "test/model-1", "attachments": [att_a["id"]],
    })
    client.post(f"/api/v1/chats/{chat_b}/complete", json={
        "message": "b", "model_id": "test/model-1", "attachments": [att_b["id"]],
    })

    row = attachments_service.get_attachment(att_a["id"])
    sha = row["sha256"]

    client.delete(f"/api/v1/chats/{chat_a}")
    assert _blob_exists(sha)      # chat B still references the same blob

    client.delete(f"/api/v1/chats/{chat_b}")
    assert not _blob_exists(sha)  # last reference gone → blob removed


def test_upload_dedup_stores_single_blob(client):
    """Same content twice: two attachment rows, ONE blob row."""
    import database

    data = make_png(color=(7, 7, 7))
    a = upload(client, data)
    b = upload(client, data)
    assert a["id"] != b["id"]
    with database.get_db() as con:
        sha_rows = con.execute(
            "SELECT DISTINCT sha256 FROM attachments WHERE id IN (?, ?)",
            (a["id"], b["id"]),
        ).fetchall()
        assert len(sha_rows) == 1
        n = con.execute(
            "SELECT COUNT(*) AS n FROM attachment_blobs WHERE sha256 = ?",
            (sha_rows[0]["sha256"],),
        ).fetchone()["n"]
    assert n == 1


def test_serve_sends_no_store_header(client):
    att = upload(client, make_png(color=(3, 141, 59)))
    resp = client.get(f"/api/v1/uploads/images/{att['id']}")
    assert resp.status_code == 200
    assert resp.headers.get("cache-control") == "no-store"
