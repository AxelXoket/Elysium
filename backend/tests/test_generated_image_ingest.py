"""A model may hand back a picture, and it lands in the vault or nowhere.

The rules this file pins, in the order they matter:

  1. Nothing is asked for unless somebody asked for it. `modalities` is absent
     from the payload until a setting is on AND the model declares image output,
     and the hardcoded provider policy rides along untouched either way.
  2. A `data:` URL is decoded here. An `https://` URL is REFUSED - not fetched.
     This app has exactly one egress host, and a hosted asset would also mean
     the picture sits on somebody else's server for as long as they like.
  3. Base64 never reaches a message row, a response body or an SSE frame. The
     bytes go to `attachment_blobs`; the wire carries an id.
  4. A picture and the reply that owns it commit in ONE transaction.
  5. A reply that is a picture and no words is a reply. The gate that refuses
     an empty reply had to widen - carefully, because the reason it exists is a
     bug that shipped once: "the same bytes, opposite answers, and the stored
     one rendered as a permanently empty bubble forever".
  6. Losing a picture never loses the words that came with it.
"""
from __future__ import annotations

import base64
import io
import json

import pytest
from PIL import Image

import database
import generated_images
import routers.completions as completions
from tests.conftest import get_messages, make_character, make_chat

BODY = {"message": "draw me something", "model_id": "test/model-1"}


def _png_bytes(colour=(200, 30, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), colour).save(buf, format="PNG")
    return buf.getvalue()


def _data_url(raw: bytes | None = None) -> str:
    return "data:image/png;base64," + base64.b64encode(
        raw if raw is not None else _png_bytes()
    ).decode("ascii")


def _enable(client, *, models=("text", "image")) -> None:
    generated_images.set_image_output_enabled(True)
    import openrouter

    openrouter._model_cache["public"] = {
        "fetched_at": 9e18,     # never expires inside a test
        "data": {"source": "public", "cached": True, "count": 1, "models": [
            {"id": "test/model-1", "output_modalities": list(models),
             "input_modalities": ["text"], "supported_parameters": []},
        ]},
    }


@pytest.fixture(autouse=True)
def _clean_model_cache():
    import openrouter

    openrouter.invalidate_model_cache()
    yield
    openrouter.invalidate_model_cache()


def _fake_complete(monkeypatch, *, content="here you go", images=None, calls=None):
    async def _c(messages, model_id, gen_params, provider, **kwargs):
        if calls is not None:
            calls.append({"provider": provider, "kwargs": kwargs})
        message: dict = {"content": content}
        if images is not None:
            message["images"] = images
        return {"choices": [{"message": message}]}

    monkeypatch.setattr(completions, "complete", _c)


def _chat(client) -> int:
    return make_chat(client, make_character(client, first_mes="Hi."))


# ── 1. nothing is asked for unless it was asked for ─────────────────────────

def test_modalities_is_absent_by_default(client, monkeypatch):
    calls: list[dict] = []
    _fake_complete(monkeypatch, calls=calls)
    chat = _chat(client)
    assert client.post(f"/api/v1/chats/{chat}/complete", json=BODY).status_code == 200
    assert calls[0]["kwargs"].get("modalities") is None


def test_modalities_is_sent_once_the_setting_is_on(client, monkeypatch):
    calls: list[dict] = []
    _fake_complete(monkeypatch, calls=calls)
    _enable(client)
    chat = _chat(client)
    assert client.post(f"/api/v1/chats/{chat}/complete", json=BODY).status_code == 200
    assert list(calls[0]["kwargs"]["modalities"]) == ["text", "image"]


def test_a_text_only_model_is_never_asked_to_draw(client, monkeypatch):
    calls: list[dict] = []
    _fake_complete(monkeypatch, calls=calls)
    _enable(client, models=("text",))
    chat = _chat(client)
    assert client.post(f"/api/v1/chats/{chat}/complete", json=BODY).status_code == 200
    assert calls[0]["kwargs"].get("modalities") is None


def test_the_privacy_policy_rides_along_unchanged(client, monkeypatch):
    """The assertion that must never be allowed to rot: asking for a picture
    does not touch the three fields that are hardcoded and immutable."""
    calls: list[dict] = []
    _fake_complete(monkeypatch, calls=calls)
    _enable(client)
    chat = _chat(client)
    client.post(f"/api/v1/chats/{chat}/complete", json=BODY)
    prov = calls[0]["provider"]
    assert prov["zdr"] is True
    assert prov["data_collection"] == "deny"
    assert prov["allow_fallbacks"] is False


def test_modalities_cannot_arrive_through_generation_params():
    """It is an explicit parameter for a reason: the gen-param validator is a
    numeric allow-list, so this key would be dropped silently there."""
    from openrouter import validate_and_filter_gen_params

    assert validate_and_filter_gen_params({"modalities": ["text", "image"]}) == {}


# ── 2 + 3 + 4. the non-streaming ingest ────────────────────────────────────

def test_a_generated_image_is_stored_and_served(client, monkeypatch):
    _fake_complete(monkeypatch, content="here it is",
                   images=[{"type": "image_url",
                            "image_url": {"url": _data_url()}}])
    _enable(client)
    chat = _chat(client)

    resp = client.post(f"/api/v1/chats/{chat}/complete", json=BODY)
    assert resp.status_code == 200, resp.text
    atts = resp.json()["assistant_message"]["attachments"]
    assert len(atts) == 1
    assert atts[0]["mime"] == "image/png"
    assert atts[0]["width"] == 16 and atts[0]["height"] == 16

    # It survives a reload...
    body = get_messages(client, chat)
    assert len(body[-1]["attachments"]) == 1
    # ...and it is actually servable.
    got = client.get(f"/api/v1/uploads/images/{atts[0]['id']}")
    assert got.status_code == 200
    assert got.headers["content-type"] == "image/png"


def test_an_entry_without_the_type_discriminator_still_counts(client, monkeypatch):
    """OpenRouter's published item schema omits `type` while their own SDK
    requires it. A parser strict about shape would report "no images returned"
    on a reply that returned some."""
    _fake_complete(monkeypatch, images=[{"image_url": {"url": _data_url()}}])
    _enable(client)
    chat = _chat(client)
    resp = client.post(f"/api/v1/chats/{chat}/complete", json=BODY)
    assert len(resp.json()["assistant_message"]["attachments"]) == 1


def test_the_stored_bytes_are_ours_not_the_providers(client, monkeypatch):
    """Re-encoded through the metadata-free pipeline, exactly like an upload."""
    from PIL import PngImagePlugin

    meta = PngImagePlugin.PngInfo()
    meta.add_text("provenance", "a string the provider chose")
    buf = io.BytesIO()
    Image.new("RGB", (16, 16)).save(buf, format="PNG", pnginfo=meta)
    raw = buf.getvalue()
    assert b"a string the provider chose" in raw

    _fake_complete(monkeypatch, images=[{"image_url": {"url": _data_url(raw)}}])
    _enable(client)
    chat = _chat(client)
    client.post(f"/api/v1/chats/{chat}/complete", json=BODY)

    with database.get_db() as con:
        blob = con.execute("SELECT data FROM attachment_blobs").fetchone()["data"]
    assert b"a string the provider chose" not in blob


def test_no_base64_reaches_the_row_or_the_response(client, monkeypatch):
    _fake_complete(monkeypatch, content="look",
                   images=[{"image_url": {"url": _data_url()}}])
    _enable(client)
    chat = _chat(client)
    resp = client.post(f"/api/v1/chats/{chat}/complete", json=BODY)

    assert "base64" not in resp.text
    assert "data:image" not in resp.text
    with database.get_db() as con:
        for row in con.execute("SELECT content FROM messages").fetchall():
            assert "base64" not in row["content"]


def test_the_image_and_the_reply_commit_together(client, monkeypatch):
    """A chat deleted while the provider answered must leave neither."""
    chat = _chat(client)

    async def _vanish(messages, model_id, gen_params, provider, **kwargs):
        with database.get_db() as con:
            con.execute("DELETE FROM messages WHERE chat_id = ?", (chat,))
            con.execute("DELETE FROM chats WHERE id = ?", (chat,))
        return {"choices": [{"message": {
            "content": "orphan", "images": [{"image_url": {"url": _data_url()}}],
        }}]}

    monkeypatch.setattr(completions, "complete", _vanish)
    _enable(client)

    assert client.post(f"/api/v1/chats/{chat}/complete",
                       json=BODY).status_code == 404
    with database.get_db() as con:
        assert con.execute("SELECT COUNT(*) c FROM attachments").fetchone()["c"] == 0
        assert con.execute(
            "SELECT COUNT(*) c FROM attachment_blobs").fetchone()["c"] == 0


# ── the refusals ────────────────────────────────────────────────────────────

def test_a_remotely_hosted_image_is_refused_not_fetched(client, monkeypatch):
    _fake_complete(monkeypatch, content="see the link",
                   images=[{"image_url": {"url": "https://example.com/a.png"}}])
    _enable(client)
    chat = _chat(client)

    resp = client.post(f"/api/v1/chats/{chat}/complete", json=BODY)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["assistant_message"]["content"] == "see the link"
    assert data["assistant_message"]["attachments"] == []
    codes = [n["code"] for n in data["notices"]]
    assert generated_images.NOTICE_IMAGE_REMOTE_URL in codes


def test_nothing_is_fetched_for_a_remote_url(client, monkeypatch):
    """The only egress host is the provider. A link must not become a request.

    Trapped at the SOCKET, not at network_client.get_client. The earlier version
    of this test patched that factory, which any code path that built its own
    httpx client - or used requests, or urllib - would simply walk past: it
    stayed green while decode_data_url was made to genuinely fetch. A name-level
    guard cannot prove the absence of egress. This one is library-agnostic:
    anything that reaches the network must first resolve a host or connect to an
    address, and both are trapped here.
    The trap is no longer built here. tests/conftest.py installs it for every
    test in the suite, so this asserts the app's behaviour and the guard is
    somebody else's job - which is the point: a promise kept by one decorator
    is kept nowhere.
    """
    _fake_complete(monkeypatch,
                   images=[{"image_url": {"url": "https://evil.example/x.png"}}])
    _enable(client)
    chat = _chat(client)
    resp = client.post(f"/api/v1/chats/{chat}/complete", json=BODY)
    assert resp.status_code == 200, resp.text
    # Reaching the network would have raised EgressAttempt from the guard and
    # failed this request outright, so a 200 here IS the assertion.
    # And the picture really was refused rather than quietly succeeding.
    assert resp.json()["assistant_message"]["attachments"] == []


def test_the_socket_trap_in_the_test_above_actually_works(client):
    """Guards the guard. If the trap cannot see a real fetch, the test above
    proves nothing - which is exactly how its predecessor failed.

    It now proves the SUITE-WIDE guard, installed by conftest, and that is a
    strictly bigger claim than the local copy it replaced.
    """
    import httpx

    from tests.egress_guard import EgressAttempt

    with pytest.raises(EgressAttempt, match="evil.example"):
        httpx.Client(timeout=1.0).get("https://evil.example/x.png")


def test_undecodable_base64_costs_the_picture_not_the_words(client, monkeypatch):
    _fake_complete(monkeypatch, content="the words survive",
                   images=[{"image_url": {"url": "data:image/png;base64,!!!!"}}])
    _enable(client)
    chat = _chat(client)

    resp = client.post(f"/api/v1/chats/{chat}/complete", json=BODY)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["assistant_message"]["content"] == "the words survive"
    assert data["assistant_message"]["attachments"] == []
    assert generated_images.NOTICE_IMAGE_REJECTED in [
        n["code"] for n in data["notices"]
    ]


def test_a_bomb_from_the_provider_costs_the_picture_not_the_words(client, monkeypatch):
    buf = io.BytesIO()
    Image.new("RGB", (6000, 6000)).save(buf, format="PNG", compress_level=9)
    _fake_complete(monkeypatch, content="still here",
                   images=[{"image_url": {"url": _data_url(buf.getvalue())}}])
    _enable(client)
    chat = _chat(client)

    resp = client.post(f"/api/v1/chats/{chat}/complete", json=BODY)
    assert resp.status_code == 200, resp.text
    assert resp.json()["assistant_message"]["content"] == "still here"
    assert resp.json()["assistant_message"]["attachments"] == []


def test_a_declared_svg_never_becomes_a_row(client, monkeypatch):
    _fake_complete(
        monkeypatch, content="nope",
        images=[{"image_url": {"url": "data:image/svg+xml;base64," + base64.b64encode(
            b"<svg xmlns='http://www.w3.org/2000/svg'><script/></svg>").decode()}}],
    )
    _enable(client)
    chat = _chat(client)
    resp = client.post(f"/api/v1/chats/{chat}/complete", json=BODY)
    assert resp.json()["assistant_message"]["attachments"] == []
    with database.get_db() as con:
        assert con.execute("SELECT COUNT(*) c FROM attachments").fetchone()["c"] == 0


# ── 5. a picture and no words is still a reply ──────────────────────────────

def test_an_image_only_reply_is_not_treated_as_empty(client, monkeypatch):
    _fake_complete(monkeypatch, content="",
                   images=[{"image_url": {"url": _data_url()}}])
    _enable(client)
    chat = _chat(client)

    resp = client.post(f"/api/v1/chats/{chat}/complete", json=BODY)
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["assistant_message"]["attachments"]) == 1
    # And the user's turn survives.
    assert [m["role"] for m in get_messages(client, chat)] == [
        "assistant", "user", "assistant",
    ]


def test_a_reply_with_neither_words_nor_picture_is_still_refused(client, monkeypatch):
    """The widening must not become "accept anything"."""
    _fake_complete(monkeypatch, content="", images=[])
    _enable(client)
    chat = _chat(client)
    resp = client.post(f"/api/v1/chats/{chat}/complete", json=BODY)
    assert resp.status_code == 502
    assert resp.json()["detail"] == "invalid_openrouter_completion_response"


def test_a_tag_only_reply_with_a_picture_is_accepted(client, monkeypatch):
    """Tags are stripped from the display, so tag-only text is visibly empty -
    but it is not an empty REPLY when a picture came with it."""
    client.post("/api/v1/tts/voice-mode", json={"enabled": True})
    import voice_tags

    voice_tags.reset_stripping_cache()

    _fake_complete(monkeypatch, content="[whisper]",
                   images=[{"image_url": {"url": _data_url()}}])
    _enable(client)
    chat = _chat(client)
    resp = client.post(f"/api/v1/chats/{chat}/complete", json=BODY)
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["assistant_message"]["attachments"]) == 1


def test_a_tag_only_reply_with_no_picture_is_still_refused(client, monkeypatch):
    client.post("/api/v1/tts/voice-mode", json={"enabled": True})
    import voice_tags

    voice_tags.reset_stripping_cache()

    _fake_complete(monkeypatch, content="[whisper]")
    _enable(client)
    chat = _chat(client)
    assert client.post(f"/api/v1/chats/{chat}/complete",
                       json=BODY).status_code == 502


# ── the streaming path ──────────────────────────────────────────────────────

def _fake_stream(monkeypatch, deltas, images=None, on_final_message=False,
                 calls=None):
    """A provider double shaped like the real generator: text through the
    return value, images through the sink."""

    def _s(messages, model_id, gen_params, provider, modalities=None,
           on_image=None, **kwargs):
        if calls is not None:
            calls.append({"provider": provider, "modalities": modalities})

        async def gen():
            for d in deltas:
                yield d
            if on_image is not None:
                for url in (images or []):
                    on_image(url)

        return gen()

    monkeypatch.setattr(completions, "complete_stream", _s)


def _events(resp) -> list[dict]:
    out = []
    for line in resp.iter_lines():
        line = line.strip()
        if line.startswith("data:"):
            out.append(json.loads(line[len("data:"):]))
    return out


def test_a_streamed_image_never_appears_in_a_delta(client, monkeypatch):
    _fake_stream(monkeypatch, ["Here ", "you go."], [_data_url()])
    _enable(client)
    chat = _chat(client)

    with client.stream("POST", f"/api/v1/chats/{chat}/complete/stream",
                       json=BODY) as resp:
        events = _events(resp)

    for e in events:
        if e["type"] == "delta":
            assert "base64" not in e["content"]
            assert "data:image" not in e["content"]
    assert "".join(e["content"] for e in events if e["type"] == "delta") == (
        "Here you go."
    )


def test_a_streamed_image_arrives_on_the_done_event(client, monkeypatch):
    _fake_stream(monkeypatch, ["Here."], [_data_url()])
    _enable(client)
    chat = _chat(client)

    with client.stream("POST", f"/api/v1/chats/{chat}/complete/stream",
                       json=BODY) as resp:
        events = _events(resp)

    done = [e for e in events if e["type"] == "done"][0]
    assert len(done["assistant_message"]["attachments"]) == 1
    assert done["assistant_message"]["attachments"][0]["mime"] == "image/png"


def test_a_streamed_image_survives_the_reload(client, monkeypatch):
    _fake_stream(monkeypatch, ["Here."], [_data_url()])
    _enable(client)
    chat = _chat(client)
    with client.stream("POST", f"/api/v1/chats/{chat}/complete/stream",
                       json=BODY) as resp:
        _events(resp)
    assert len(get_messages(client, chat)[-1]["attachments"]) == 1


def test_an_image_only_stream_commits_instead_of_erroring(client, monkeypatch):
    """Zero text deltas. Before the gate widened this raised openrouter_error,
    and the rescue path then DELETED the user's message."""
    _fake_stream(monkeypatch, [], [_data_url()])
    _enable(client)
    chat = _chat(client)

    with client.stream("POST", f"/api/v1/chats/{chat}/complete/stream",
                       json=BODY) as resp:
        events = _events(resp)

    kinds = [e["type"] for e in events]
    assert "error" not in kinds, events
    assert kinds[-1] == "done"
    assert [m["role"] for m in get_messages(client, chat)] == [
        "assistant", "user", "assistant",
    ]


def test_an_empty_stream_with_no_image_still_errors(client, monkeypatch):
    _fake_stream(monkeypatch, [], [])
    _enable(client)
    chat = _chat(client)
    with client.stream("POST", f"/api/v1/chats/{chat}/complete/stream",
                       json=BODY) as resp:
        events = _events(resp)
    assert [e["type"] for e in events][-1] == "error"


def test_a_refused_stream_image_is_reported_as_a_notice(client, monkeypatch):
    _fake_stream(monkeypatch, ["text survives"], ["https://example.com/a.png"])
    _enable(client)
    chat = _chat(client)
    with client.stream("POST", f"/api/v1/chats/{chat}/complete/stream",
                       json=BODY) as resp:
        events = _events(resp)
    codes = [e.get("code") for e in events if e["type"] == "notice"]
    assert generated_images.NOTICE_IMAGE_REMOTE_URL in codes
    assert [e["type"] for e in events][-1] == "done"


def test_the_stream_payload_carries_modalities_only_when_asked(client, monkeypatch):
    calls: list[dict] = []
    _fake_stream(monkeypatch, ["hi"], [], calls=calls)
    chat = _chat(client)
    with client.stream("POST", f"/api/v1/chats/{chat}/complete/stream",
                       json=BODY) as resp:
        _events(resp)
    assert calls[0]["modalities"] is None

    calls.clear()
    _enable(client)
    with client.stream("POST", f"/api/v1/chats/{chat}/complete/stream",
                       json=BODY) as resp:
        _events(resp)
    assert list(calls[0]["modalities"]) == ["text", "image"]
