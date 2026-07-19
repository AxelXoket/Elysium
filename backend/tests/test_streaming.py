"""Tests for the SSE streaming endpoints (/complete/stream, /regenerate/stream).

Persistence semantics under test:
- success: user + assistant rows persisted, done event carries both.
- provider failure mid-stream: error event; the just-inserted user message is
  rolled back (complete) / the old assistant message survives (regenerate).
"""

import json

import pytest

from openrouter import OpenRouterError

from conftest import make_character, make_chat, get_messages


BODY = {"message": "Stream me a story", "model_id": "test/model-1"}


@pytest.fixture()
def stream_provider(monkeypatch):
    """Fake openrouter.complete_stream; deltas + optional mid-stream error."""
    import routers.completions as completions_router

    class FakeStream:
        def __init__(self):
            self.calls: list[dict] = []
            self.deltas = ["Once ", "upon ", "a time."]
            self.error_after: int | None = None  # index to fail at
            self.error = OpenRouterError("openrouter_rate_limited")

        def _stream(self, messages, model_id, gen_params, provider):
            self.calls.append({
                "messages": messages,
                "model_id": model_id,
                "gen_params": gen_params,
                "provider": provider,
            })

            async def gen():
                for i, d in enumerate(self.deltas):
                    if self.error_after is not None and i >= self.error_after:
                        raise self.error
                    yield d
                if self.error_after is not None and self.error_after >= len(self.deltas):
                    raise self.error

            return gen()

    fake = FakeStream()
    monkeypatch.setattr(completions_router, "complete_stream", fake._stream)
    return fake


def read_events(resp) -> list[dict]:
    events = []
    for line in resp.iter_lines():
        line = line.strip()
        if line.startswith("data:"):
            events.append(json.loads(line[len("data:"):].strip()))
    return events


# ---------------------------------------------------------------------------
# /complete/stream
# ---------------------------------------------------------------------------

def test_stream_complete_happy_path(client, stream_provider):
    char_id = make_character(client)
    chat_id = make_chat(client, char_id)

    with client.stream(
        "POST", f"/api/v1/chats/{chat_id}/complete/stream", json=BODY,
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        events = read_events(resp)

    types = [e["type"] for e in events]
    assert types == ["user_message", "delta", "delta", "delta", "done"]
    assert events[0]["message"]["content"] == "Stream me a story"
    assert "".join(e["content"] for e in events[1:4]) == "Once upon a time."
    assert events[-1]["assistant_message"]["content"] == "Once upon a time."

    msgs = get_messages(client, chat_id)
    assert [m["role"] for m in msgs] == ["assistant", "user", "assistant"]
    assert msgs[-1]["content"] == "Once upon a time."

    # Payload sanity: user turn exactly once, stream flag handled upstream.
    call = stream_provider.calls[0]
    user_turns = [
        m for m in call["messages"]
        if m["role"] == "user" and m["content"] == "Stream me a story"
    ]
    assert len(user_turns) == 1


def test_stream_complete_provider_error_rolls_back_user_message(client, stream_provider):
    char_id = make_character(client)
    chat_id = make_chat(client, char_id)
    stream_provider.error_after = 1  # one delta, then failure

    with client.stream(
        "POST", f"/api/v1/chats/{chat_id}/complete/stream", json=BODY,
    ) as resp:
        events = read_events(resp)

    assert [e["type"] for e in events] == ["user_message", "delta", "error"]
    assert events[-1]["code"] == "openrouter_rate_limited"
    assert events[-1]["status"] == 429

    # The half-turn must be rolled back: only first_mes remains.
    msgs = get_messages(client, chat_id)
    assert [m["role"] for m in msgs] == ["assistant"]


def test_stream_complete_validation_errors_are_plain_http(client, stream_provider):
    resp = client.post("/api/v1/chats/99999/complete/stream", json=BODY)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "chat_not_found"


# ---------------------------------------------------------------------------
# /regenerate/stream
# ---------------------------------------------------------------------------

def _seed_exchange(client, stream_provider) -> tuple[int, int]:
    char_id = make_character(client)
    chat_id = make_chat(client, char_id)
    with client.stream(
        "POST", f"/api/v1/chats/{chat_id}/complete/stream", json=BODY,
    ) as resp:
        events = read_events(resp)
    assert events[-1]["type"] == "done"
    return chat_id, events[-1]["assistant_message"]["id"]


def test_stream_regenerate_swaps_only_on_success(client, stream_provider):
    chat_id, asst_id = _seed_exchange(client, stream_provider)
    stream_provider.deltas = ["Brand ", "new."]

    with client.stream(
        "POST",
        f"/api/v1/chats/{chat_id}/messages/{asst_id}/regenerate/stream",
        json={"model_id": "test/model-1"},
    ) as resp:
        events = read_events(resp)

    assert [e["type"] for e in events] == ["user_message", "delta", "delta", "done"]
    msgs = get_messages(client, chat_id)
    assert msgs[-1]["content"] == "Brand new."
    assert msgs[-1]["id"] != asst_id

    # History passed to the provider has the user turn once and no old reply.
    call = stream_provider.calls[-1]
    user_turns = [
        m for m in call["messages"]
        if m["role"] == "user" and m["content"] == "Stream me a story"
    ]
    assert len(user_turns) == 1
    assert not any(
        m["content"] == "Once upon a time." for m in call["messages"]
    )


def test_stream_regenerate_error_keeps_old_message(client, stream_provider):
    chat_id, asst_id = _seed_exchange(client, stream_provider)
    stream_provider.error_after = 1

    with client.stream(
        "POST",
        f"/api/v1/chats/{chat_id}/messages/{asst_id}/regenerate/stream",
        json={"model_id": "test/model-1"},
    ) as resp:
        events = read_events(resp)

    assert events[-1]["type"] == "error"
    msgs = get_messages(client, chat_id)
    assert msgs[-1]["id"] == asst_id
    assert msgs[-1]["content"] == "Once upon a time."
