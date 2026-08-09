"""V4 wiring - the tag pipeline through the real request paths.

test_voice_tags.py proves the algorithms; this file proves the PLUMBING:
the prompt actually reaches the provider payload (and only when it should),
no bracket ever crosses the SSE wire or the message API, the raw text really
is what lands in the database, and a chat with voice off is byte-for-byte
unaffected. The provider is faked at the network seam (openrouter functions),
never at the seams under test.
"""
import json

import pytest

import config
import voice_tags
from database import get_db
from tests.test_tts_core import make_fish, make_xtts


def _mk_chat(client):
    char = client.post("/api/v1/characters", json={
        "name": "Mira", "system_prompt": "You are Mira.",
    }).json()
    chat = client.post("/api/v1/chats", json={
        "character_id": char["id"], "model_id": "test/model",
    }).json()
    return chat["id"]


def _enable_voice(client, monkeypatch, tmp_path, engine="fish_s2"):
    root = tmp_path / "models"
    root.mkdir(exist_ok=True)
    monkeypatch.setattr(config, "TTS_MODELS_DIR", str(root), raising=False)
    make_fish(root)
    make_xtts(root)
    with get_db() as con:
        con.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (voice_tags.SETTING_VOICE_ENABLED, "1"),
        )
    body = client.get("/api/v1/tts/models").json()
    uid = next(m["uid"] for m in body["models"] if m["engine_id"] == engine)
    client.post("/api/v1/tts/active", json={"uid": uid})


def _capture_payload(monkeypatch, reply_chunks):
    """Fake the provider at the network seam and keep what it was sent."""
    captured = {}

    async def fake_stream(messages, model_id, gen_params, provider, **kwargs):
        captured["messages"] = messages
        for chunk in reply_chunks:
            yield chunk

    async def fake_complete(messages, model_id, gen_params, provider, **kwargs):
        captured["messages"] = messages
        return "".join(reply_chunks)

    import routers.completions as comp

    monkeypatch.setattr(comp, "complete_stream", fake_stream)
    monkeypatch.setattr(comp, "complete", fake_complete)
    return captured


def _sse_events(raw: str) -> list[dict]:
    return [json.loads(line[len("data: "):])
            for line in raw.splitlines() if line.startswith("data: ")]


class TestInjection:
    def test_voice_on_and_fish_selected_injects_the_block(
        self, client, monkeypatch, tmp_path
    ):
        _enable_voice(client, monkeypatch, tmp_path, engine="fish_s2")
        captured = _capture_payload(monkeypatch, ["hello"])
        chat_id = _mk_chat(client)
        r = client.post(f"/api/v1/chats/{chat_id}/complete/stream",
                        json={"model_id": "test/model", "message": "hi"})
        assert r.status_code == 200
        systems = [m["content"] for m in captured["messages"]
                   if m["role"] == "system"]
        assert any("VOICE DELIVERY" in s for s in systems), (
            "the delivery prompt never reached the provider payload")

    def test_the_block_is_invisible_to_the_message_api(
        self, client, monkeypatch, tmp_path
    ):
        """Call-level means call-level: nothing about the prompt may surface
        in the chat history the client reads."""
        _enable_voice(client, monkeypatch, tmp_path)
        _capture_payload(monkeypatch, ["hello"])
        chat_id = _mk_chat(client)
        client.post(f"/api/v1/chats/{chat_id}/complete/stream",
                    json={"model_id": "test/model", "message": "hi"})
        messages = client.get(f"/api/v1/chats/{chat_id}/messages").json()
        assert all("VOICE DELIVERY" not in (m.get("content") or "")
                   for m in messages)

    def test_voice_off_payload_is_untouched(self, client, monkeypatch, tmp_path):
        captured = _capture_payload(monkeypatch, ["hello"])
        chat_id = _mk_chat(client)
        client.post(f"/api/v1/chats/{chat_id}/complete/stream",
                    json={"model_id": "test/model", "message": "hi"})
        systems = [m["content"] for m in captured["messages"]
                   if m["role"] == "system"]
        assert not any("VOICE DELIVERY" in s for s in systems)

    def test_an_xtts_selection_gets_no_block(self, client, monkeypatch, tmp_path):
        """XTTS cannot use inline tags - teaching the model to write them
        would put brackets in the voice's mouth."""
        _enable_voice(client, monkeypatch, tmp_path, engine="xtts_v2")
        captured = _capture_payload(monkeypatch, ["hello"])
        chat_id = _mk_chat(client)
        client.post(f"/api/v1/chats/{chat_id}/complete/stream",
                    json={"model_id": "test/model", "message": "hi"})
        systems = [m["content"] for m in captured["messages"]
                   if m["role"] == "system"]
        assert not any("VOICE DELIVERY" in s for s in systems)


class TestNoBracketCrossesTheWire:
    def test_deltas_are_stripped_even_when_a_tag_spans_chunks(
        self, client, monkeypatch, tmp_path
    ):
        _enable_voice(client, monkeypatch, tmp_path)
        _capture_payload(monkeypatch, ["[sedu", "ctive] I miss", "ed you."])
        chat_id = _mk_chat(client)
        r = client.post(f"/api/v1/chats/{chat_id}/complete/stream",
                        json={"model_id": "test/model", "message": "hi"})
        deltas = [e["content"] for e in _sse_events(r.text)
                  if e.get("type") == "delta"]
        assert "".join(deltas) == "I missed you."
        assert all("[" not in d for d in deltas), (
            "a bracket flashed on screen: %r" % deltas)

    def test_the_stored_row_keeps_the_raw_tags(self, client, monkeypatch, tmp_path):
        """Raw in the vault, stripped at the door - re-speak needs the tags."""
        _enable_voice(client, monkeypatch, tmp_path)
        _capture_payload(monkeypatch, ["[soft] Stay with me."])
        chat_id = _mk_chat(client)
        client.post(f"/api/v1/chats/{chat_id}/complete/stream",
                    json={"model_id": "test/model", "message": "hi"})
        with get_db() as con:
            row = con.execute(
                "SELECT content FROM messages WHERE chat_id = ? AND "
                "role = 'assistant' ORDER BY id DESC LIMIT 1",
                (chat_id,),
            ).fetchone()
        assert row["content"] == "[soft] Stay with me."

    def test_the_message_api_serves_the_stripped_view_of_that_same_row(
        self, client, monkeypatch, tmp_path
    ):
        _enable_voice(client, monkeypatch, tmp_path)
        _capture_payload(monkeypatch, ["[soft] Stay with me."])
        chat_id = _mk_chat(client)
        client.post(f"/api/v1/chats/{chat_id}/complete/stream",
                    json={"model_id": "test/model", "message": "hi"})
        messages = client.get(f"/api/v1/chats/{chat_id}/messages").json()
        reply = next(m for m in messages if m["role"] == "assistant")
        assert reply["content"] == "Stay with me."

    def test_streamed_and_refreshed_text_agree(self, client, monkeypatch, tmp_path):
        """The flicker check, end to end: what streamed onto the screen equals
        what the refresh after `done` renders."""
        _enable_voice(client, monkeypatch, tmp_path)
        raw = "[seductive] I missed you... [low voice] come closer. See [docs](https://x.y)."
        _capture_payload(monkeypatch, [raw[i:i + 5] for i in range(0, len(raw), 5)])
        chat_id = _mk_chat(client)
        r = client.post(f"/api/v1/chats/{chat_id}/complete/stream",
                        json={"model_id": "test/model", "message": "hi"})
        streamed = "".join(e["content"] for e in _sse_events(r.text)
                           if e.get("type") == "delta")
        messages = client.get(f"/api/v1/chats/{chat_id}/messages").json()
        refreshed = next(m for m in messages if m["role"] == "assistant")["content"]
        assert streamed == refreshed
        assert "[docs](https://x.y)" in refreshed, "the link was eaten"

    def test_a_voiceless_chat_streams_byte_for_byte(self, client, monkeypatch,
                                                    tmp_path):
        """R2: adding the feature must not change what already worked. With no
        tags in the reply the stripper is identity - prove it on the wire."""
        text = "Plain reply. *narration* and a [link](https://a.b) too."
        _capture_payload(monkeypatch, [text[i:i + 9] for i in range(0, len(text), 9)])
        chat_id = _mk_chat(client)
        r = client.post(f"/api/v1/chats/{chat_id}/complete/stream",
                        json={"model_id": "test/model", "message": "hi"})
        streamed = "".join(e["content"] for e in _sse_events(r.text)
                           if e.get("type") == "delta")
        assert streamed == text


class TestVoiceModeEndpoint:
    def test_toggle_round_trip(self, client):
        assert client.get("/api/v1/tts/voice-mode").json()["enabled"] is False
        body = client.post("/api/v1/tts/voice-mode", json={"enabled": True}).json()
        assert body["enabled"] is True
        assert body["prompt_chars"] == voice_tags.VOICE_PROMPT_CHARS
        assert client.get("/api/v1/tts/voice-mode").json()["enabled"] is True

    def test_enabled_without_a_tag_capable_model_is_not_active(
        self, client, monkeypatch, tmp_path
    ):
        """`active` is the truth the context gauge needs: the toggle can be on
        while nothing would actually inject."""
        client.post("/api/v1/tts/voice-mode", json={"enabled": True})
        assert client.get("/api/v1/tts/voice-mode").json()["active"] is False
