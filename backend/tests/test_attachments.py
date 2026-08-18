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


def _png_with_metadata() -> bytes:
    from PIL import PngImagePlugin
    img = Image.new("RGB", (8, 8), (10, 20, 30))
    meta = PngImagePlugin.PngInfo()
    meta.add_text("UserSecret", "GPS:51.5074,-0.1278")
    exif = Image.Exif()
    exif[0x9286] = "hidden-comment"  # UserComment
    buf = io.BytesIO()
    img.save(buf, format="PNG", pnginfo=meta, exif=exif.tobytes())
    return buf.getvalue()


def _webp_with_exif() -> bytes:
    img = Image.new("RGB", (8, 8), (40, 50, 60))
    exif = Image.Exif()
    exif[0x9286] = "webp-secret-location"
    buf = io.BytesIO()
    img.save(buf, format="WEBP", exif=exif.tobytes())
    return buf.getvalue()


def test_upload_strips_png_metadata(client):
    """v1.1 audit L3: EXIF/GPS/text chunks must never survive into storage -
    they would ride along to the provider the moment the image is sent."""
    raw = _png_with_metadata()
    # Source genuinely carries the secrets, else the test proves nothing.
    assert b"GPS:51.5074" in raw
    assert Image.open(io.BytesIO(raw)).info.get("exif")

    meta = upload(client, raw)
    stored = client.get(f"/api/v1/uploads/images/{meta['id']}").content
    assert b"GPS:51.5074" not in stored  # tEXt chunk gone
    reopened = Image.open(io.BytesIO(stored))
    assert not reopened.info.get("exif")  # EXIF gone
    assert "UserSecret" not in reopened.info
    assert reopened.size == (8, 8)  # pixels preserved


def test_upload_strips_webp_metadata(client):
    """v1.1 audit L3: same strip guarantee for the WebP path."""
    raw = _webp_with_exif()
    assert Image.open(io.BytesIO(raw)).info.get("exif")  # source HAS exif

    meta = upload(client, raw, mime="image/webp", name="t.webp")
    assert meta["mime"] == "image/webp"
    stored = client.get(f"/api/v1/uploads/images/{meta['id']}").content
    assert not Image.open(io.BytesIO(stored)).info.get("exif")


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
    # Floor first: the seeded turn has to BE in the payload. all() over a
    # payload that had quietly stopped carrying image-bearing history would
    # pass while the model lost the conversation, which is the opposite of
    # what this test is for.
    assert any(m["content"] == "old image message" for m in call["messages"]), (
        call["messages"])
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

    def fake_stream(messages, model_id, gen_params, provider_dict, **kwargs):
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

    def failing_stream(messages, model_id, gen_params, provider, **kwargs):
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


# ── v1.1 FB8/I12: unstage endpoint (DELETE /uploads/images/{id}) ─────────────

def test_unstage_staged_removes_row_and_blob(client):
    import attachments_service

    att = upload(client, make_png(color=(11, 22, 33)))
    row = attachments_service.get_attachment(att["id"])
    assert _blob_exists(row["sha256"])

    resp = client.delete(f"/api/v1/uploads/images/{att['id']}")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    assert attachments_service.get_attachment(att["id"]) is None
    assert not _blob_exists(row["sha256"])
    # The binary no longer serves.
    assert client.get(f"/api/v1/uploads/images/{att['id']}").status_code == 404


def test_unstage_unknown_returns_404(client):
    resp = client.delete("/api/v1/uploads/images/99999")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "attachment_not_found"


def test_unstage_linked_returns_409_and_keeps_row(client, provider, vision_model):
    import attachments_service

    char_id = make_character(client)
    chat_id = make_chat(client, char_id)
    att = upload(client, make_png(color=(44, 55, 66)))
    r = client.post(f"/api/v1/chats/{chat_id}/complete", json={
        "message": "with image", "model_id": "test/model-1",
        "attachments": [att["id"]],
    })
    assert r.status_code == 200, r.text

    resp = client.delete(f"/api/v1/uploads/images/{att['id']}")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "attachment_unavailable"

    # Row still linked, blob intact, image still serves.
    linked = attachments_service.get_attachment(att["id"])
    assert linked is not None and linked["message_id"] is not None
    assert _blob_exists(linked["sha256"])
    assert client.get(f"/api/v1/uploads/images/{att['id']}").status_code == 200


def test_unstage_shared_blob_survives_other_staged_row(client):
    data = make_png(color=(77, 88, 99))
    a = upload(client, data)
    b = upload(client, data)  # same sha256, second staged row
    import attachments_service
    sha = attachments_service.get_attachment(a["id"])["sha256"]

    client.delete(f"/api/v1/uploads/images/{a['id']}")
    assert _blob_exists(sha)  # b still references it

    client.delete(f"/api/v1/uploads/images/{b['id']}")
    assert not _blob_exists(sha)  # last reference gone


def test_upload_triggers_opportunistic_stale_purge(client):
    import attachments_service
    import database

    stale = upload(client, make_png(color=(1, 2, 3)))
    stale_sha = attachments_service.get_attachment(stale["id"])["sha256"]
    # Backdate it past the 24h purge window.
    with database.get_db() as con:
        con.execute(
            "UPDATE attachments SET created_at = datetime('now', '-25 hours') "
            "WHERE id = ?",
            (stale["id"],),
        )

    # A fresh upload schedules the opportunistic purge; TestClient runs
    # background tasks before the response returns.
    fresh = upload(client, make_png(color=(4, 5, 6)))

    assert attachments_service.get_attachment(stale["id"]) is None
    assert not _blob_exists(stale_sha)
    assert attachments_service.get_attachment(fresh["id"]) is not None


def test_upload_survives_purge_failure(client, monkeypatch):
    import routers.uploads as uploads_router

    def boom():
        raise RuntimeError("simulated purge lock timeout")

    monkeypatch.setattr(uploads_router, "purge_stale_staged", boom)
    # The best-effort purge is isolated: the upload still succeeds.
    att = upload(client, make_png(color=(9, 8, 7)))
    assert att["id"] > 0


# ── v1.1 FB12: SQL bound-parameter chunking (huge chats never 500) ───────────

def _seed_big_chat(client, n_messages: int, attach_every: int = 0):
    """Insert n_messages directly + optionally attach a shared image every
    `attach_every` rows. Returns (chat_id, sha_of_shared_image_or_None)."""
    import database
    import hashlib

    char_id = make_character(client)
    chat_id = make_chat(client, char_id)
    shared_sha = None
    if attach_every:
        data = make_png(color=(12, 34, 56))
        shared_sha = hashlib.sha256(data).hexdigest()
    with database.get_db() as con:
        if attach_every:
            con.execute(
                "INSERT OR IGNORE INTO attachment_blobs (sha256, data) VALUES (?, ?)",
                (shared_sha, data),
            )
        for i in range(n_messages):
            role = "user" if i % 2 == 0 else "assistant"
            mid = con.execute(
                "INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)",
                (chat_id, role, f"m{i}"),
            ).lastrowid
            if attach_every and i % attach_every == 0:
                con.execute(
                    "INSERT INTO attachments (message_id, sha256, mime, width, "
                    "height, byte_size) VALUES (?, ?, 'image/png', 8, 8, ?)",
                    (mid, shared_sha, len(data)),
                )
    return chat_id, shared_sha


def test_get_messages_over_chunk_boundary(client):
    # 1000 rows > SQL_VAR_CHUNK(900): load_for_messages spans two chunks.
    # (+1 for the character's first_mes seeded by make_chat.)
    chat_id, sha = _seed_big_chat(client, 1000, attach_every=250)
    msgs = get_messages(client, chat_id)
    assert len(msgs) == 1001
    # Attachment mapping survives the multi-chunk read (rows 0,250,500,750).
    with_images = [m for m in msgs if m.get("attachments")]
    assert len(with_images) == 4


def test_delete_big_chat_stays_atomic_and_sweeps_blobs(client):
    import attachments_service
    chat_id, sha = _seed_big_chat(client, 1000, attach_every=250)
    assert _blob_exists(sha)

    resp = client.delete(f"/api/v1/chats/{chat_id}")
    assert resp.status_code == 200
    assert get_messages_missing(client, chat_id)
    assert not _blob_exists(sha)  # orphan blob swept in the chunked txn


def get_messages_missing(client, chat_id: int) -> bool:
    # After a chat delete the messages endpoint returns [] (chat gone -> its
    # rows gone). We assert the rows are truly absent via the DB.
    import database
    with database.get_db() as con:
        n = con.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE chat_id = ?", (chat_id,)
        ).fetchone()["n"]
    return n == 0


def test_clear_big_chat_keeps_chat_removes_rows(client):
    chat_id, sha = _seed_big_chat(client, 1000, attach_every=250)
    resp = client.post(f"/api/v1/chats/{chat_id}/clear")
    assert resp.status_code == 200
    assert get_messages(client, chat_id) == []
    assert not _blob_exists(sha)


def test_chunk_helpers_unit_small_chunk(client, monkeypatch):
    """With SQL_VAR_CHUNK monkeypatched to 10, chunked reads/deletes match the
    unchunked result and preserve a blob referenced outside the deleted set."""
    import database
    import attachments_service
    import hashlib

    monkeypatch.setattr(database, "SQL_VAR_CHUNK", 10)

    char_id = make_character(client)
    chat_id = make_chat(client, char_id)
    data = make_png(color=(1, 2, 3))
    sha = hashlib.sha256(data).hexdigest()
    keep_data = make_png(color=(9, 9, 9))
    keep_sha = hashlib.sha256(keep_data).hexdigest()

    mids = []
    with database.get_db() as con:
        con.execute(
            "INSERT OR IGNORE INTO attachment_blobs (sha256, data) VALUES (?, ?)",
            (sha, data),
        )
        con.execute(
            "INSERT OR IGNORE INTO attachment_blobs (sha256, data) VALUES (?, ?)",
            (keep_sha, keep_data),
        )
        for i in range(25):
            mid = con.execute(
                "INSERT INTO messages (chat_id, role, content) VALUES (?, 'user', ?)",
                (chat_id, f"m{i}"),
            ).lastrowid
            mids.append(mid)
            con.execute(
                "INSERT INTO attachments (message_id, sha256, mime, width, "
                "height, byte_size) VALUES (?, ?, 'image/png', 8, 8, ?)",
                (mid, sha, len(data)),
            )
        # One message OUTSIDE the delete set still references keep_sha.
        keep_mid = con.execute(
            "INSERT INTO messages (chat_id, role, content) VALUES (?, 'user', 'keep')",
            (chat_id,),
        ).lastrowid
        con.execute(
            "INSERT INTO attachments (message_id, sha256, mime, width, height, "
            "byte_size) VALUES (?, ?, 'image/png', 8, 8, ?)",
            (keep_mid, keep_sha, len(keep_data)),
        )

    # Chunked read over 25 ids (3 chunks of 10) returns the full mapping.
    loaded = attachments_service.load_for_messages(mids)
    assert len(loaded) == 25

    # Chunked delete removes the 25 rows + their orphan blob, keeps keep_sha.
    with database.get_db() as con:
        con.execute("BEGIN IMMEDIATE")
        attachments_service.delete_for_messages(con, mids)
        con.execute(
            f"DELETE FROM messages WHERE id IN "
            f"({','.join('?' * len(mids))})", mids,
        )
    assert not _blob_exists(sha)      # deleted set's blob is now orphaned -> gone
    assert _blob_exists(keep_sha)     # still referenced by keep_mid


def test_purge_stale_over_chunk(client, monkeypatch):
    import database
    import attachments_service
    import hashlib

    monkeypatch.setattr(database, "SQL_VAR_CHUNK", 10)
    # Seed 25 stale staged rows DIRECTLY (the upload endpoint's opportunistic
    # purge would delete earlier rows between uploads and skew the count).
    with database.get_db() as con:
        for i in range(25):
            data = make_png(color=(i % 200, 7, 7))
            sha = hashlib.sha256(data).hexdigest()
            con.execute(
                "INSERT OR IGNORE INTO attachment_blobs (sha256, data) VALUES (?, ?)",
                (sha, data),
            )
            con.execute(
                "INSERT INTO attachments (message_id, sha256, mime, width, "
                "height, byte_size, created_at) VALUES (NULL, ?, 'image/png', "
                "8, 8, ?, datetime('now','-25 hours'))",
                (sha, len(data)),
            )
    purged = attachments_service.purge_stale_staged()
    assert purged == 25


# ── Audit H1: unknown model metadata must not silently strip images ──────
#
# _validate_request_attachments deliberately accepts attachments when metadata
# is not cached ("the provider is the final arbiter"), but payload assembly
# used to derive include_images from that same absent metadata and get False -
# so the images were stripped from the payload the provider never got to
# arbitrate: 200 OK, thumbnail rendered in the bubble, and a reply about an
# image the model never received. Both now read ONE rule
# (_model_accepts_images), so the gate and the payload cannot disagree.


@pytest.fixture()
def uncached_model(monkeypatch):
    """The state after a fresh start or any invalidate_model_cache() call."""
    import routers.completions as completions_router
    monkeypatch.setattr(
        completions_router, "get_cached_model_metadata", lambda mid: None,
    )


def _image_parts(content) -> list[dict]:
    if not isinstance(content, list):
        return []
    return [p for p in content if p.get("type") == "image_url"]


def test_uncached_metadata_still_sends_the_image(client, provider, uncached_model):
    char_id = make_character(client)
    chat_id = make_chat(client, char_id)
    att = upload(client, make_png(color=(1, 2, 3)))

    resp = client.post(f"/api/v1/chats/{chat_id}/complete", json={
        "message": "what is in this image?", "model_id": "test/model-1",
        "attachments": [att["id"]],
    })
    assert resp.status_code == 200, resp.text

    last = provider.calls[-1]["messages"][-1]
    assert last["role"] == "user"
    assert len(_image_parts(last["content"])) == 1, (
        "the gate accepted the attachment, so the payload must carry it"
    )


def test_empty_input_modalities_still_sends_the_image(
    client, provider, monkeypatch,
):
    """Metadata present but with no modality list is 'unknown', not 'text-only'."""
    import routers.completions as completions_router
    meta = {**VISION_META, "input_modalities": []}
    monkeypatch.setattr(
        completions_router, "get_cached_model_metadata", lambda mid: meta,
    )
    char_id = make_character(client)
    chat_id = make_chat(client, char_id)
    att = upload(client, make_png(color=(4, 5, 6)))

    resp = client.post(f"/api/v1/chats/{chat_id}/complete", json={
        "message": "look", "model_id": "test/model-1", "attachments": [att["id"]],
    })
    assert resp.status_code == 200, resp.text
    assert len(_image_parts(provider.calls[-1]["messages"][-1]["content"])) == 1


def test_known_text_only_model_still_refuses_and_strips(
    client, provider, text_only_model,
):
    """The positive 'no image input' signal keeps its behaviour."""
    char_id = make_character(client)
    chat_id = make_chat(client, char_id)
    att = upload(client, make_png(color=(7, 8, 9)))

    resp = client.post(f"/api/v1/chats/{chat_id}/complete", json={
        "message": "look", "model_id": "test/model-1", "attachments": [att["id"]],
    })
    assert resp.status_code == 400
    assert resp.json()["detail"] == "model_no_image_input"


def test_text_only_model_does_not_charge_history_images_to_the_budget(
    client, provider, vision_model, text_only_model,
):
    """Images that will not be sent must not consume the trim budget."""
    import routers.completions as completions_router

    history = [{"role": "user", "content": "hi",
                "attachments": [{"sha256": "a" * 64, "mime": "image/png"}]}]
    with_images = completions_router._entry_chars(
        "hi", history[0]["attachments"], True,
    )
    without = completions_router._entry_chars(
        "hi", history[0]["attachments"], False,
    )
    assert with_images > without == len("hi")


# ── Audit: metadata stripping also wiped tRNS, and MPO was rejected ─────────


def make_palette_png_with_transparency() -> bytes:
    """PNG-8 with a transparent index - "Save for Web", TinyPNG, icon exports."""
    img = Image.new("P", (8, 8))
    palette = [0, 0, 0] + [255, 0, 0] * 255
    img.putpalette(palette)
    buf = io.BytesIO()
    img.save(buf, format="PNG", transparency=0)
    return buf.getvalue()


def test_palette_png_keeps_its_transparency(client):
    from PIL import Image as PILImage

    src = make_palette_png_with_transparency()
    assert PILImage.open(io.BytesIO(src)).convert("RGBA").getpixel((0, 0))[3] == 0

    row = upload(client, src)
    stored = client.get(f"/api/v1/uploads/images/{row['id']}").content
    pixel = PILImage.open(io.BytesIO(stored)).convert("RGBA").getpixel((0, 0))
    assert pixel[3] == 0, "transparent pixels were re-encoded opaque"


# ── K-18: the tRNS exception is wider than its own justification ───────────
#
# normalise_image wipes img.info and then puts ONE key back, with this
# reasoning written above it: "It carries no uploader information - it is a
# palette index or a grey level."
#
# That is true for the picture the test above builds, and only for it. Pillow
# collapses a tRNS chunk to a single int only when the raw bytes match
# ^\xff*\x00\xff*$ - one transparent entry, everything else opaque. Any other
# pattern is handed back as `bytes`, one byte per palette entry, up to 256 of
# them, and normalise_image re-attaches that object unexamined. Palette slots
# no pixel uses still get their byte, so the carrier is invisible on screen.
# Grey-level images are worse per byte than the comment suggests too: mode L
# tRNS is a 16 bit value read straight off the chunk, not an index into
# anything.
#
# The two tests below are CHARACTERISATION, not approval. They assert what
# this app does today so that the day someone narrows the exception, they go
# red and have to come here. Recorded in KUSUR-DEFTERI as K-18; nothing is
# repaired during the ladder.

_TRNS_PAYLOAD = bytes([13, 250, 7, 199, 0, 255, 42, 88])


def _palette_png_with_trns(trns: bytes) -> bytes:
    img = Image.new("P", (8, 8), 0)
    img.putpalette([c for i in range(256) for c in (i, i, i)])
    buf = io.BytesIO()
    img.save(buf, format="PNG", transparency=trns)
    return buf.getvalue()


def test_a_palette_transparency_array_is_rebuilt_from_what_the_pixels_show(client):
    """K-18, closed. This used to assert the 256 bytes rode through verbatim.

    The old comment said tRNS "carries no uploader information - it is a
    palette index or a grey level". True of the int form, false of this one:
    in mode P a tRNS is a byte string with one alpha value per palette entry,
    and every entry the image never uses is a byte nobody looks at, leaving
    this machine inside a file the app re-encoded specifically so that nothing
    would.

    Refusing it outright was measured and rejected: 153 of the 153 real files
    carrying a `bytes` tRNS would have been visibly broken. Rebuilding it from
    the alpha values the pixels actually use changed none of the 168 affected
    files.
    """
    from PIL import Image as PILImage

    arr = bytearray(b"\xff" * 256)
    arr[200:208] = _TRNS_PAYLOAD          # indices no pixel in this image uses
    src = _palette_png_with_trns(bytes(arr))
    # Floor: Pillow must actually be handing back the array form, otherwise
    # this test is measuring the collapsed-int case.
    assert isinstance(PILImage.open(io.BytesIO(src)).info["transparency"], bytes)

    row = upload(client, src)
    stored = client.get(f"/api/v1/uploads/images/{row['id']}").content
    out = PILImage.open(io.BytesIO(stored)).info.get("transparency")

    if out is not None:
        assert _TRNS_PAYLOAD not in out, (
            "the payload in unused palette entries survived the re-encode")


def test_the_transparency_a_picture_really_uses_is_kept(client):
    """The discriminating half, and the reason this is a rebuild not a ban.

    A palette image whose transparent index is actually on screen must come
    back transparent. Dropping tRNS wholesale would turn every "Save for Web"
    export, every logo and every icon fully opaque - which is the defect this
    carve-out was written to fix in the first place.
    """
    from PIL import Image as PILImage

    img = Image.new("P", (8, 8), 0)
    img.putpixel((0, 0), 1)
    palette = [0, 0, 0, 255, 255, 255] + [0] * (768 - 6)
    img.putpalette(palette)
    buf = io.BytesIO()
    img.save(buf, format="PNG", transparency=bytes([0, 255]))

    row = upload(client, buf.getvalue())
    stored = client.get(f"/api/v1/uploads/images/{row['id']}").content
    out = PILImage.open(io.BytesIO(stored)).info.get("transparency")

    assert out is not None, "a picture that really is transparent came back opaque"
    # Either shape is correct: a one-entry tRNS trimmed to its used range is
    # read back by Pillow as the int form. What matters is that index 0 is
    # still transparent.
    assert (out == 0 if isinstance(out, int) else out[0] == 0), out


def test_an_index_no_pixel_uses_is_dropped(client):
    """The strict variant the owner asked for.

    An `int` tRNS naming a palette entry that no pixel refers to says
    something about the file's history and nothing about its appearance.
    Measured: none of the 168 real files affected by this change.
    """
    from PIL import Image as PILImage

    img = Image.new("P", (8, 8), 0)
    palette = [0, 0, 0] + [255, 255, 255] * 255
    img.putpalette(palette[:768])
    buf = io.BytesIO()
    # Index 7 is never drawn - every pixel is index 0.
    img.save(buf, format="PNG", transparency=7)

    row = upload(client, buf.getvalue())
    stored = client.get(f"/api/v1/uploads/images/{row['id']}").content

    assert PILImage.open(io.BytesIO(stored)).info.get("transparency") is None


def test_a_greyscale_value_no_pixel_shows_is_dropped(client):
    """K-18's second door. Mode L tRNS is 16 bits, not a grey level index.

    54321 cannot be a grey this 8-bit image displays, so it described nothing
    on screen and rode out anyway.
    """
    from PIL import Image as PILImage

    buf = io.BytesIO()
    Image.new("L", (8, 8), 128).save(buf, format="PNG", transparency=54321)

    row = upload(client, buf.getvalue())
    stored = client.get(f"/api/v1/uploads/images/{row['id']}").content

    assert PILImage.open(io.BytesIO(stored)).info.get("transparency") is None


def test_a_greyscale_value_the_picture_does_show_is_kept(client):
    # The other side of the same rule: a grey that IS on screen is real
    # transparency and must survive.
    from PIL import Image as PILImage

    buf = io.BytesIO()
    Image.new("L", (8, 8), 128).save(buf, format="PNG", transparency=128)

    row = upload(client, buf.getvalue())
    stored = client.get(f"/api/v1/uploads/images/{row['id']}").content

    assert PILImage.open(io.BytesIO(stored)).info.get("transparency") == 128


# test_stripping_still_removes_exif lived here: a JPEG carrying EXIF tag 271
# (Make), uploaded, asserted absent. TestEveryKindOfMetadataTheImagePromiseNames
# below builds the same format and asserts 0x010F along with Model,
# DateTimeOriginal, UserComment, the GPS sub-IFD, ICC, XMP and the COM segment,
# so it fails first on anything this could have caught. The two PNG and WebP
# strip tests are NOT redundant with it - those exercise tEXt chunks and the
# WebP EXIF container, which the JPEG class never touches - and they stay.


def test_multi_picture_jpeg_is_accepted(client):
    """MPO is a JPEG with an MPF marker - what dual-lens cameras produce."""
    from PIL import Image as PILImage

    frame = Image.new("RGB", (8, 8), (9, 9, 9))
    buf = io.BytesIO()
    frame.save(buf, format="MPO", save_all=True, append_images=[frame])
    data = buf.getvalue()
    assert PILImage.open(io.BytesIO(data)).format == "MPO"

    row = upload(client, data, mime="image/jpeg", name="dual.jpg")
    assert row["mime"] == "image/jpeg"


def test_an_oversized_body_is_refused_before_it_is_read(client):
    """The cap used to be enforced only AFTER the whole body had been written
    to the temp directory - one request could fill the disk."""
    from config import MAX_UPLOAD_BYTES

    huge = b"x" * (MAX_UPLOAD_BYTES + 4 * 1024 * 1024)
    resp = client.post(
        "/api/v1/uploads/images",
        files={"file": ("big.png", huge, "image/png")},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "attachment_too_large"


def _watch_rollover(monkeypatch) -> list[str]:
    """Record every time an uploaded part is written out to %TEMP%.

    THE observation point for "No plaintext image ever touches the filesystem".
    SpooledTemporaryFile.rollover() is the exact moment a part stops being a
    BytesIO and becomes a file on disk, so a test that never sees it called has
    proved the promise rather than restated the arithmetic behind it.
    """
    import tempfile

    rolled: list[str] = []
    real = tempfile.SpooledTemporaryFile.rollover

    def spy(self):
        rolled.append("rolled")
        return real(self)

    monkeypatch.setattr(tempfile.SpooledTemporaryFile, "rollover", spy)
    return rolled


def test_a_legal_upload_never_rolls_to_a_temp_file(client, monkeypatch):
    """Was `assert spool_max_size > MAX_UPLOAD_BYTES` - two integers, no
    upload, and no contact with the thing the numbers exist to prevent."""
    from config import MAX_UPLOAD_BYTES

    rolled = _watch_rollover(monkeypatch)
    payload = make_png(1200, 900)
    assert len(payload) < MAX_UPLOAD_BYTES

    resp = client.post("/api/v1/uploads/images",
                       files={"file": ("ok.png", payload, "image/png")})

    assert resp.status_code == 201, resp.text
    assert rolled == [], "a legal upload was written to disk in the clear"


def test_the_band_that_used_to_reach_the_disk_no_longer_does(client, monkeypatch):
    """The reported gap: the spool ceiling was MAX_UPLOAD_BYTES + 1 and the
    body shield was MAX_UPLOAD_BYTES + 1 MiB, leaving ~1 MiB where the
    middleware let the body through, Starlette rolled the part out of RAM, and
    the WHOLE image was written to %TEMP% in the clear before the handler
    answered 400. Nothing touched that band."""
    from config import MAX_UPLOAD_BYTES, UPLOAD_BODY_LIMIT

    rolled = _watch_rollover(monkeypatch)
    # Inside the old band: over the file cap, under the body shield.
    size = (MAX_UPLOAD_BYTES + UPLOAD_BODY_LIMIT) // 2
    assert MAX_UPLOAD_BYTES < size < UPLOAD_BODY_LIMIT

    resp = client.post("/api/v1/uploads/images",
                       files={"file": ("big.png", b"x" * size, "image/png")})

    assert resp.status_code in (400, 413), resp.status_code
    assert rolled == [], (
        "a rejected image still reached %TEMP% in the clear - the band is open"
    )


def test_the_ceilings_still_cannot_drift_apart():
    """The arithmetic the old test checked, kept as what it actually is: a
    structural invariant behind the behaviour above, not a substitute for it."""
    from starlette.formparsers import MultiPartParser
    from config import MAX_UPLOAD_BYTES, UPLOAD_BODY_LIMIT
    import routers.uploads  # noqa: F401 - importing sets the spool size

    assert UPLOAD_BODY_LIMIT > MAX_UPLOAD_BYTES
    assert MultiPartParser.spool_max_size > UPLOAD_BODY_LIMIT, (
        "anything that survives the body shield can now be spooled to disk"
    )


# ---------------------------------------------------------------------------
# The promise says EXIF and GPS. The tests above said "one text tag".
# ---------------------------------------------------------------------------

def _jpeg_with_everything() -> bytes:
    """A JPEG carrying every kind of metadata the promise is about.

    The three strip tests that existed before this one each set a single
    generic EXIF text tag: UserComment, or Make. That proves the exif blob is
    dropped and says nothing about the four things a reader of README line 43
    would actually care about, because they arrive through four different
    mechanisms:

      GPS   a sub-IFD inside the exif blob, not a tag in it
      ICC   its own APP2 marker segment, nothing to do with exif
      XMP   an APP1 segment with its own namespace, nothing to do with exif
      COM   a JPEG comment segment, older than all of them

    All of them go today, and this is the test that keeps saying so if somebody
    ever replaces the wholesale re-encode with a tag allowlist.

    NOT COVERED HERE, AND SAID PLAINLY RATHER THAN IMPLIED
    The embedded thumbnail (IFD1) is the sharpest case of all: crop a face or a
    document out of a photo in most editors and the ORIGINAL frame survives in
    the thumbnail, so publishing the file publishes what the crop was hiding.
    It rides inside the same exif blob this test proves is gone, so it goes
    too - but that is REASONING, not measurement. Pillow will not write an IFD1
    thumbnail through `Exif.tobytes()`; probed on 2026-08-10, the entry is
    accepted and then silently dropped on save. A test with no floor under it
    is the shape this whole exercise exists to remove, so the claim is written
    down instead of asserted.
    """
    img = Image.new("RGB", (64, 48), (90, 120, 150))

    exif = Image.Exif()
    exif[0x010F] = "TestCameraBrand"            # Make
    exif[0x0110] = "TestCameraModel"            # Model
    exif[0x9003] = "2019:07:14 11:02:33"        # DateTimeOriginal
    exif[0x9286] = "a private note"             # UserComment
    # GPS IFD. 51.5074 N, 0.1278 W, as rationals, the way a phone writes it.
    exif.get_ifd(0x8825).update({
        1: "N", 2: (51.0, 30.0, 26.64),
        3: "W", 4: (0.0, 7.0, 40.08),
    })
    buf = io.BytesIO()
    img.save(buf, format="JPEG",
             exif=exif.tobytes(),
             icc_profile=b"\x00\x00\x02\x0cICCTESTPROFILEBYTES" + b"\x00" * 512,
             comment=b"a JPEG COM comment")
    raw = buf.getvalue()
    # XMP spliced in as its own APP1 segment, because Pillow writes an XMP
    # packet for some formats and not for JPEG. The segment is what a phone or
    # Lightroom actually produces, so this is the real shape rather than a
    # convenient one.
    xmp = (b'<?xpacket begin="?"?><x:xmpmeta xmlns:x="adobe:ns:meta/">'
           b'<photoshop:City>Reykjavik</photoshop:City></x:xmpmeta>')
    app1 = b"\xff\xe1" + (len(xmp) + 2 + 29).to_bytes(2, "big") + \
           b"http://ns.adobe.com/xap/1.0/\x00" + xmp
    return raw[:2] + app1 + raw[2:]


class TestEveryKindOfMetadataTheImagePromiseNames:
    """README line 43: "EXIF/GPS and other embedded metadata are dropped".

    The promise is absolute and it is kept. What was missing was a test that
    covers the same ground as the sentence: the three that existed set one
    generic tag each, so a change that stripped the exif blob and kept ICC or
    XMP would have left every one of them green.
    """

    def test_the_source_really_carries_all_of_it(self):
        """The floor, and the reason the rest of this class is not vacuous.

        Every assertion below is of the form "this is absent from the output".
        Absent is also what you get from an input that never had it, from a
        Pillow that quietly refused to write it, and from an assertion looking
        at the wrong field. So the input is checked first, by the same means
        the output will be.
        """
        raw = _jpeg_with_everything()
        src = Image.open(io.BytesIO(raw))
        exif = src.getexif()

        assert exif.get(0x010F) == "TestCameraBrand"
        assert exif.get(0x9003) == "2019:07:14 11:02:33"
        assert exif.get_ifd(0x8825), "no GPS IFD in the source"
        assert src.info.get("icc_profile"), "no ICC profile in the source"
        assert b"Reykjavik" in raw, "no XMP packet in the source"
        assert b"a JPEG COM comment" in raw, "no COM segment in the source"

    def test_none_of_it_survives_the_upload(self, client):
        raw = _jpeg_with_everything()
        meta = upload(client, raw, mime="image/jpeg", name="holiday.jpg")
        stored = client.get(f"/api/v1/uploads/images/{meta['id']}").content

        out = Image.open(io.BytesIO(stored))
        exif = out.getexif()

        assert not exif.get_ifd(0x8825), "GPS coordinates survived the upload"
        assert not exif.get(0x010F), "the camera make survived the upload"
        assert not exif.get(0x0110), "the camera model survived the upload"
        assert not exif.get(0x9003), "the capture date survived the upload"
        assert not out.info.get("icc_profile"), "the ICC profile survived"

        # Searched in the raw bytes, not through Pillow. XMP and COM are their
        # own marker segments, and a reader that does not know about them
        # reports nothing rather than reporting what is there.
        assert b"Reykjavik" not in stored, "the XMP packet survived the upload"
        assert b"TestCameraBrand" not in stored, "the make survived as bytes"
        assert b"a JPEG COM comment" not in stored, "the COM segment survived"

    def test_the_picture_itself_is_still_the_picture(self, client):
        """The control. Stripping everything is easy if you also lose the
        image, and a test that only checks for absences would not notice."""
        raw = _jpeg_with_everything()
        meta = upload(client, raw, mime="image/jpeg", name="holiday.jpg")
        stored = client.get(f"/api/v1/uploads/images/{meta['id']}").content

        out = Image.open(io.BytesIO(stored))
        assert out.size == (64, 48)
        # The colour survives a JPEG round trip within a wide tolerance; the
        # point is that these are the original pixels and not a blank frame.
        r, g, b = out.convert("RGB").getpixel((32, 24))
        assert abs(r - 90) < 24 and abs(g - 120) < 24 and abs(b - 150) < 24


# -- the branches nothing walked into ---------------------------------------
#
# Every test above this line drives the decode path with either a picture the
# app accepts or bytes that are not a picture at all. Between those two sits
# the part of normalise_image that decides which pictures count, and a
# measurement of the whole file found no test standing in it: the format
# allowlist, the load-time failure, the two ceilings AT their boundary, and
# the orientation transpose. Each one below is a distinct branch, and each one
# is the kind that fails silently rather than loudly.

def _boundary_bytes(target: int) -> bytes:
    """A decodable PNG padded to exactly `target` bytes.

    Everything after IEND is ignored by every decoder, so this is a real image
    of an exact size - which is the only way to stand ON a byte ceiling rather
    than near it.
    """
    png = make_png()
    assert len(png) < target
    return png + b"\x00" * (target - len(png))


def test_a_gif_is_refused_even_though_it_decodes(client):
    """The format allowlist, which no test had entered.

    "Not an image" and "an image of a format we do not keep" leave
    normalise_image through different doors: the first dies in Image.open, the
    second reaches the mime lookup and finds None. Only a real, perfectly
    valid picture of an unlisted format goes through the second one, and GIF
    is the format a person is most likely to try.
    """
    buf = io.BytesIO()
    Image.new("P", (8, 8)).save(buf, format="GIF")
    assert buf.getvalue()[:3] == b"GIF"

    resp = client.post("/api/v1/uploads/images",
                       files={"file": ("a.gif", buf.getvalue(), "image/gif")})
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "attachment_invalid"


def test_an_svg_is_refused_by_the_upload_endpoint(client):
    """The provider path has this test; the browser path did not.

    SVG is the one refusal that matters more than the others, because the
    serve route is same-origin with the whole local API and an SVG is a
    document that can run script. It must not depend on the client's declared
    type, so declare it truthfully and watch it be refused on its bytes.
    """
    svg = b"<svg xmlns='http://www.w3.org/2000/svg'><script>x()</script></svg>"
    resp = client.post("/api/v1/uploads/images",
                       files={"file": ("a.svg", svg, "image/svg+xml")})
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "attachment_invalid"


def test_a_truncated_image_is_refused_and_leaves_nothing_behind(client):
    """A valid header over a body that stops halfway.

    This is the OTHER half of the same except-clause the garbage test uses:
    the header parses, so Image.open succeeds and the failure lands in
    img.load(). The rows assertion is the part worth having - a decode that
    blew up after a write had started would leave a blob nobody references.
    """
    import database

    whole = make_png(64, 64)
    resp = client.post("/api/v1/uploads/images",
                       files={"file": ("cut.png", whole[:len(whole) // 2],
                                       "image/png")})
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "attachment_invalid"
    with database.get_db() as con:
        assert con.execute(
            "SELECT COUNT(*) c FROM attachments").fetchone()["c"] == 0
        assert con.execute(
            "SELECT COUNT(*) c FROM attachment_blobs").fetchone()["c"] == 0


def test_the_longest_side_at_the_ceiling_is_left_alone(client):
    """Measured, and the measurement corrected the intent.

    This was written to separate `>` from `>=` on the downscale test. It does
    not, and cannot: Image.thumbnail only ever shrinks, so asking it to fit an
    image into exactly its own size is a no-op and both comparisons produce
    identical bytes. The mutation is benign, not missed.

    What it does guard is the ceiling VALUE - an image at the documented limit
    coming back re-encoded to something smaller, which is what a changed
    constant or a stray -1 would do, and which no other test would see because
    every other fixture is far from the line.
    """
    from config import IMAGE_MAX_DIMENSION

    row = upload(client, make_png(IMAGE_MAX_DIMENSION, 64))
    assert (row["width"], row["height"]) == (IMAGE_MAX_DIMENSION, 64)


def test_one_pixel_past_the_ceiling_is_downscaled(client):
    """Guard the guard above. Greater-than and greater-or-equal differ by
    exactly this image, and the existing downscale test sits 1000px clear of
    the line, so it cannot see the difference."""
    from config import IMAGE_MAX_DIMENSION

    row = upload(client, make_png(IMAGE_MAX_DIMENSION + 1, 64))
    assert row["width"] == IMAGE_MAX_DIMENSION


def test_an_upload_that_lands_exactly_on_the_byte_ceiling_is_kept(client):
    """MAX_UPLOAD_BYTES is a limit, not a forbidden value. Two independent
    checks read it - the handler's bounded read and normalise_image's own -
    and either one written with the wrong comparison refuses a legal upload
    while every "too large" test in this file stays green."""
    from config import MAX_UPLOAD_BYTES

    row = upload(client, _boundary_bytes(MAX_UPLOAD_BYTES))
    assert row["width"] == 8


def test_one_byte_past_the_byte_ceiling_is_refused(client):
    from config import MAX_UPLOAD_BYTES

    resp = client.post(
        "/api/v1/uploads/images",
        files={"file": ("big.png", _boundary_bytes(MAX_UPLOAD_BYTES + 1),
                        "image/png")},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "attachment_too_large"


def test_a_sideways_photo_is_stored_the_way_up_it_is_meant_to_be_seen(client):
    """exif_transpose, which the code says exists "so width/height and the
    stored pixels agree" - and which nothing measured.

    A phone writes the sensor's landscape frame and an Orientation tag saying
    to rotate it. If the transpose were dropped, the pixels would come back
    sideways AND the reported dimensions would still be the sensor's, so the
    frontend would reserve a box of the wrong shape for every portrait photo
    ever taken. Both halves are asserted here.
    """
    src = Image.new("RGB", (40, 20), (10, 90, 200))
    exif = src.getexif()
    exif[274] = 6                      # Orientation: rotate 90 CW to display
    buf = io.BytesIO()
    src.save(buf, format="JPEG", exif=exif)

    row = upload(client, buf.getvalue(), mime="image/jpeg", name="p.jpg")

    assert (row["width"], row["height"]) == (20, 40), (
        "the sensor's frame was stored as-is, tag and all")
    stored = client.get(f"/api/v1/uploads/images/{row['id']}").content
    assert Image.open(io.BytesIO(stored)).size == (20, 40)


# -- the request-level attachment gate --------------------------------------

def test_the_same_attachment_twice_in_one_send_is_refused(client, vision_model,
                                                          provider):
    """The over-cap test sends four distinct ids; nobody sent one id twice.

    Without the duplicate check the list falls through to link_attachments,
    which links the row once - so the message quietly carries fewer pictures
    than the sender listed, which is a worse failure than a refusal.
    """
    char = make_character(client)
    chat = make_chat(client, char)
    att = upload(client, make_png())

    resp = client.post(f"/api/v1/chats/{chat}/complete", json={
        "message": "look at these", "model_id": "test/model-1",
        "attachments": [att["id"], att["id"]],
    })

    assert resp.status_code == 400, resp.text
    assert provider.calls == []


def test_exactly_the_cap_of_attachments_goes_through(client, vision_model,
                                                     provider):
    """The refusal is tested at cap+1. Nothing proved cap itself is allowed,
    so a boundary written one off would reject a legal send and stay green."""
    from config import MAX_ATTACHMENTS_PER_MESSAGE

    char = make_character(client)
    chat = make_chat(client, char)
    ids = [upload(client, make_png(color=(i + 1, 40, 40)))["id"]
           for i in range(MAX_ATTACHMENTS_PER_MESSAGE)]

    resp = client.post(f"/api/v1/chats/{chat}/complete", json={
        "message": "look at these", "model_id": "test/model-1",
        "attachments": ids,
    })

    assert resp.status_code == 200, resp.text
    sent = provider.calls[-1]["messages"][-1]["content"]
    assert sum(1 for p in sent if p.get("type") == "image_url") == (
        MAX_ATTACHMENTS_PER_MESSAGE)


def test_the_payload_ram_ceiling_keeps_the_newest_pictures(client, monkeypatch):
    """The guard against assembling a chat's whole image history into RAM.

    Every test that touches prefetch_blobs is far under the cap or replaces
    the function outright, so the admission loop had never run to its second
    branch. Two things are pinned: newest wins, and the loop CONTINUES past an
    image it cannot afford rather than stopping - a later, smaller picture
    still gets in. A break there would look identical under any fixture where
    the blobs happen to be the same size.
    """
    import hashlib

    import attachments_service
    import database

    blobs = {}
    # Order matters more than size here. The one that does NOT fit must sit in
    # the MIDDLE, with an affordable picture after it - otherwise skipping and
    # stopping produce the same map and the test cannot tell them apart.
    for label, pad in (("first", 4000), ("unaffordable", 4000), ("last", 400)):
        data = make_png(color=(len(blobs) + 1, 7, 7)) + b"\x00" * pad
        blobs[label] = hashlib.sha256(data).hexdigest()
        with database.get_db() as con:
            con.execute("INSERT OR IGNORE INTO attachment_blobs (sha256, data) "
                        "VALUES (?, ?)", (blobs[label], data))

    with database.get_db() as con:
        sizes = {k: con.execute(
            "SELECT length(data) n FROM attachment_blobs WHERE sha256 = ?",
            (v,)).fetchone()["n"] for k, v in blobs.items()}
    cap = sizes["first"] + sizes["last"]
    assert cap < sizes["first"] + sizes["unaffordable"], "fixture does not bite"
    monkeypatch.setattr(attachments_service, "IMAGE_PAYLOAD_MAX_TOTAL_BYTES", cap)

    got = attachments_service.prefetch_blobs(
        [blobs["first"], blobs["unaffordable"], blobs["last"]])

    assert blobs["first"] in got, "the newest picture was left out"
    assert blobs["unaffordable"] not in got, (
        "the cap admitted more than it can hold")
    assert blobs["last"] in got, (
        "the admission loop STOPPED at the first picture it could not afford "
        "instead of skipping it - every older image is now dropped from the "
        "payload the moment one large one appears")


def test_deleting_a_character_takes_its_pictures_and_leaves_the_others(
    client, vision_model, provider,
):
    """The deepest cascade in the app, and the only one with no pytest at all.

    Character -> chats -> messages -> attachments -> blobs, four levels, and
    the only thing that ever checked it was a verify/ script that walks raw
    SQL. Two things are asserted, because the cascade can fail in opposite
    directions and each one looks fine from the other side:

      * everything belonging to the deleted character is gone, blobs
        included - a cascade that stops at `chats` leaves orphan rows and a
        vault that only grows;
      * a picture ANOTHER character's chat still shows is untouched - the
        shared blob is the discriminating part, because message ids are
        global here, so an unscoped sweep reaches the whole vault and a test
        with one character in it would never notice.
    """
    import database

    shared = make_png(color=(4, 4, 200))
    own = make_png(color=(200, 4, 4))

    doomed = make_character(client, name="Doomed")
    chat_a = make_chat(client, doomed)
    keeper = make_character(client, name="Keeper")
    chat_b = make_chat(client, keeper)

    def _send(chat, data, text):
        att = upload(client, data)
        resp = client.post(f"/api/v1/chats/{chat}/complete", json={
            "message": text, "model_id": "test/model-1",
            "attachments": [att["id"]],
        })
        assert resp.status_code == 200, resp.text

    _send(chat_a, shared, "the doomed one")
    _send(chat_a, own, "and one only it has")
    _send(chat_b, shared, "the survivor, same picture")

    with database.get_db() as con:
        assert con.execute(
            "SELECT COUNT(*) c FROM attachment_blobs").fetchone()["c"] == 2

    assert client.delete(f"/api/v1/characters/{doomed}").status_code == 200

    with database.get_db() as con:
        assert con.execute(
            "SELECT COUNT(*) c FROM chats WHERE character_id = ?",
            (doomed,)).fetchone()["c"] == 0
        assert con.execute(
            "SELECT COUNT(*) c FROM messages WHERE chat_id = ?",
            (chat_a,)).fetchone()["c"] == 0
        assert con.execute(
            "SELECT COUNT(*) c FROM attachments").fetchone()["c"] == 1
        # The picture only the deleted character had is gone; the one its
        # neighbour still shows is not.
        assert con.execute(
            "SELECT COUNT(*) c FROM attachment_blobs").fetchone()["c"] == 1

    kept = [m for m in get_messages(client, chat_b) if m["attachments"]]
    assert len(kept) == 1, kept
    served = client.get(
        f"/api/v1/uploads/images/{kept[0]['attachments'][0]['id']}")
    assert served.status_code == 200, "the survivor's picture stopped serving"


# -- what an adversary would change, given a free hand ----------------------

def test_the_stale_purge_never_touches_a_picture_a_message_owns(client):
    """The single most destructive line in the attachment code.

    purge_stale_staged runs unattended: on every vault unlock, and again
    opportunistically after every upload. Its WHERE clause carries two
    conditions - old enough, and NOT owned by a message. Drop the second and
    the sweep stops meaning "abandon what nobody sent" and starts meaning
    "delete every picture in the vault older than a day", rows and bytes, on
    the next unlock, with no user action and no error.

    Both rows below are backdated past the cutoff, so age cannot be what
    separates them. The only thing that can is ownership.
    """
    import database

    abandoned = upload(client, make_png(color=(9, 9, 9)))
    char = make_character(client)
    chat = make_chat(client, char)
    owned = upload(client, make_png(color=(9, 9, 200)))
    with database.get_db() as con:
        con.execute("BEGIN IMMEDIATE")
        mid = con.execute(
            "INSERT INTO messages (chat_id, role, content) VALUES (?, 'user', 'x')",
            (chat,),
        ).lastrowid
        con.execute("UPDATE attachments SET message_id = ? WHERE id = ?",
                    (mid, owned["id"]))
        con.execute("UPDATE attachments SET created_at = "
                    "datetime('now', '-48 hours')")

    import attachments_service
    purged = attachments_service.purge_stale_staged()

    assert purged == 1, f"the sweep took {purged} rows, not just the abandoned one"
    assert client.get(
        f"/api/v1/uploads/images/{owned['id']}").status_code == 200, (
        "the unattended purge deleted a picture a message owns")
    assert client.get(
        f"/api/v1/uploads/images/{abandoned['id']}").status_code == 404


def test_a_send_gets_back_only_its_own_attachments(client, vision_model,
                                                   provider):
    """link_attachments returns the rows the API echoes as this message's
    pictures. Unscoped, that query returns every attachment row in the vault,
    and the client renders a thumbnail for each - other conversations'
    pictures appearing under a message that never had them.

    The existing send test asserts `attachments[0]["id"]`, which is index 0 of
    a list whose length it never checks, in a vault holding exactly one row.
    So plant a stranger first: with one, scoped and unscoped agree.
    """
    stranger = upload(client, make_png(color=(1, 250, 1)))
    char = make_character(client)
    chat = make_chat(client, char)
    mine = upload(client, make_png(color=(250, 1, 1)))

    resp = client.post(f"/api/v1/chats/{chat}/complete", json={
        "message": "just this one", "model_id": "test/model-1",
        "attachments": [mine["id"]],
    })

    assert resp.status_code == 200, resp.text
    got = resp.json()["user_message"]["attachments"]
    assert [a["id"] for a in got] == [mine["id"]], got
    assert stranger["id"] not in [a["id"] for a in got]


def test_the_draw_setting_reads_as_off_when_it_cannot_be_read(monkeypatch):
    """Fail closed, and say so in a test rather than only in a comment.

    A wrong False costs a feature the user can turn back on. A wrong True is a
    request they never made, sent to a provider, and billed. Every existing
    test for this flag sets it explicitly, so the except-branch - the only
    place the asymmetry is decided - had never been executed.
    """
    import database
    import generated_images

    def _boom(*a, **kw):
        raise RuntimeError("vault unreadable")

    monkeypatch.setattr(database, "get_setting", _boom)
    assert generated_images.image_output_enabled() is False


def test_an_oversized_data_url_is_refused_before_it_is_decoded(client):
    """The base64 length ceiling in decode_data_url.

    normalise_image re-checks the byte cap, but only on the DECODED bytes -
    and decoding is where the memory goes. The provider chooses the size of
    its own response, so this is the only check standing between a hostile
    reply and a multi-hundred-megabyte allocation. The byte-ceiling test in
    test_generated_image_storage.py calls normalise_image directly and never
    reaches this line.
    """
    import generated_images
    from config import MAX_UPLOAD_BYTES

    huge = "data:image/png;base64," + "A" * (MAX_UPLOAD_BYTES * 2)
    with pytest.raises(ValueError, match="too large"):
        generated_images.decode_data_url(huge)


def test_a_cropped_photos_original_frame_does_not_ride_along(client):
    """The EXIF thumbnail (IFD1), which the file next to this one flagged as
    reasoned-about rather than measured.

    A camera writes a small copy of the frame into the EXIF block. Phones and
    editors routinely leave it there after a CROP, so the thumbnail can still
    show what the crop removed - a face, a screen, a street sign. Nothing in
    this suite looked at it, because Pillow's own Exif.tobytes() will not
    WRITE an IFD1, so a fixture built with Pillow alone cannot carry one. That
    is why this is the one test in the file that needs piexif: it exists to
    build an input Pillow refuses to build.

    WHAT IT GUARDS, measured rather than assumed. Deleting `img.info = {}`
    leaves this test GREEN while the EXIF test above goes red: Pillow's
    Exif.tobytes() cannot write an IFD1 back, so the thumbnail dies in the
    re-encode whether or not anything wipes info. The protection here is
    INHERITED from a dependency, not implemented by this app - which is
    precisely why it needs a test rather than a comment. What does turn it red
    is storing the upload verbatim, i.e. the always-re-encode decision itself
    (measured: mutate `final_bytes` to `data` and this is the test that
    catches it). If a future Pillow gains IFD1 write support, the same
    mutation is no longer needed to break the promise, and this test is what
    will say so.

    Two assertions, because they fail differently. The structure check would
    still pass if the bytes were copied somewhere else in the file; the byte
    check would still pass if the tag survived but happened to be empty.

    The thumbnail is NOISE on purpose. A solid-colour one compresses to long
    repeated runs that appear in any other JPEG by coincidence - measured, and
    it produced a false positive. Its entropy-coded scan is compared, not the
    whole file: the tables before the scan marker are the standard JPEG
    Huffman tables, identical in every image Pillow writes.
    """
    import os

    import piexif

    tbuf = io.BytesIO()
    Image.frombytes("RGB", (64, 64), os.urandom(64 * 64 * 3)).save(
        tbuf, format="JPEG", quality=95)
    thumb = tbuf.getvalue()
    scan = thumb[thumb.find(b"\xff\xda"):]           # the pixels, not the tables
    assert len(scan) > 512, "fixture thumbnail has no scan to look for"

    photo = Image.frombytes("RGB", (200, 120), os.urandom(200 * 120 * 3))
    buf = io.BytesIO()
    photo.save(buf, format="JPEG", exif=piexif.dump({
        "0th": {piexif.ImageIFD.Make: b"SecretCam"},
        "Exif": {}, "GPS": {},
        "1st": {piexif.ImageIFD.Compression: 6},
        "thumbnail": thumb,
    }))
    raw = buf.getvalue()
    # The source really carries it, by both measures.
    assert len(piexif.load(raw)["thumbnail"] or b"") > 0
    assert scan[:64] in raw

    row = upload(client, raw, mime="image/jpeg", name="cropped.jpg")
    stored = client.get(f"/api/v1/uploads/images/{row['id']}").content

    after = piexif.load(stored)
    assert not after["thumbnail"], "the embedded thumbnail survived"
    assert after["1st"] == {}, after["1st"]
    assert not any(scan[i:i + 32] in stored for i in range(0, len(scan) - 32, 16)), (
        "the thumbnail's pixels are still in the file, outside the EXIF block")
