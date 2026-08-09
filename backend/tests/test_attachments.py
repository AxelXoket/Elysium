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


def test_stripping_still_removes_exif(client):
    """Control: the privacy guarantee the info-wipe exists for is intact."""
    from PIL import Image as PILImage

    buf = io.BytesIO()
    img = Image.new("RGB", (8, 8), (1, 2, 3))
    exif = img.getexif()
    exif[271] = "SecretCameraMaker"
    img.save(buf, format="JPEG", exif=exif)

    row = upload(client, buf.getvalue(), mime="image/jpeg", name="p.jpg")
    stored = client.get(f"/api/v1/uploads/images/{row['id']}").content
    assert b"SecretCameraMaker" not in stored
    assert dict(PILImage.open(io.BytesIO(stored)).getexif()) == {}


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
