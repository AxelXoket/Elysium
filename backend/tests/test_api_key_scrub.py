"""C9: the stored API key never leaves inside a request body.

Two answers, the same two the safeword gets (safeword_filter's docstring):
  - A message BEING TYPED that carries the key is refused whole with
    `api_key_in_message`: nothing sent, nothing stored.
  - The key ANYWHERE ELSE in an outgoing body (older history, a card, a note,
    a stop sequence) is cut, and the rest of the body goes.

What leaves is read off the httpx transport, not off a stand-in for
`openrouter.complete`: the cut happens inside that function, so a stand-in
installed over it would report the uncut body in green.

The key used here is a made-up value in the shape of a real one. The key the
shared fixtures store ("sk-test-key") is shorter than KEY_MIN_CHARS, which is
the ground control for the floor.
"""
from __future__ import annotations

import json
import logging

import httpx
import pytest

import config
import secrets_service
from safeword_filter import KEY_MIN_CHARS, api_key_in, key_needles

from tests.conftest import get_messages, make_character, make_chat

#: Not a real key: the documented prefix and sixty-four hex digits.
KEY = "sk-or-v1-" + "0123456789abcdef" * 4
MODEL = "vendor/model"
REPLY = {"id": "gen-1",
         "choices": [{"finish_reason": "stop",
                      "message": {"content": "ok"}}],
         "usage": {}}
REPLY_SSE = (b'data: {"choices":[{"delta":{"content":"ok"},'
             b'"finish_reason":"stop"}]}\n\n'
             b'data: [DONE]\n\n')


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def real_key(db):
    secrets_service.set_secret(config.SECRET_API_KEY, KEY)
    return KEY


@pytest.fixture
def wire(monkeypatch) -> list[dict]:
    import openrouter

    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if body.get("stream"):
            return httpx.Response(200, content=REPLY_SSE,
                                  headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json=REPLY)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(openrouter, "get_client", lambda: client)
    return bodies


async def _send(wire, messages, **gen) -> dict:
    import openrouter
    await openrouter.complete(messages, MODEL, gen, {"data_collection": "deny"})
    assert len(wire) == 1
    return wire[0]


def _flat(body: dict) -> str:
    return json.dumps(body, ensure_ascii=False)


# ── the pure half ───────────────────────────────────────────────────────────


class TestKeyNeedles:
    def test_a_real_shaped_key_becomes_needles(self):
        assert key_needles(KEY)
        assert all(KEY.lower() == n or KEY.lower() in n or n in KEY.lower()
                   for n in key_needles(KEY))

    def test_nothing_stored_means_no_needles(self):
        assert key_needles(None) == ()
        assert key_needles("") == ()
        assert key_needles("   ") == ()

    def test_a_value_under_the_floor_is_not_hunted_for(self):
        assert key_needles("k" * (KEY_MIN_CHARS - 1)) == ()
        assert key_needles("k" * KEY_MIN_CHARS) != ()


class TestTheTypedMessageCheck:
    def test_ground_an_ordinary_message_passes(self, real_key):
        assert not api_key_in("hello, how are you?")

    def test_the_key_alone_and_inside_a_sentence(self, real_key):
        assert api_key_in(KEY)
        assert api_key_in(f"my key is {KEY} can you check it")

    def test_in_capitals_too(self, real_key):
        assert api_key_in(KEY.upper())

    def test_part_of_the_key_is_not_the_key(self, real_key):
        assert not api_key_in(KEY[:-1])
        assert not api_key_in(KEY[1:])

    def test_no_key_stored_trips_nothing(self, db):
        secrets_service.set_secret(config.SECRET_API_KEY, "")
        assert not api_key_in(KEY)


# ── the cut, at the transport ───────────────────────────────────────────────


class TestTheKeyIsCutFromWhatGoesOut:
    @pytest.mark.anyio
    async def test_ground_an_ordinary_body_goes_out_unchanged(self, real_key, wire):
        messages = [{"role": "system", "content": "a card"},
                    {"role": "user", "content": "she wore a red coat"}]
        body = await _send(wire, [dict(m) for m in messages])
        assert body["messages"] == messages

    @pytest.mark.anyio
    async def test_the_key_in_old_history_is_cut_and_the_line_stays(self, real_key, wire):
        body = await _send(wire, [
            {"role": "user", "content": f"here it is {KEY} thanks"},
            {"role": "assistant", "content": "got it"},
            {"role": "user", "content": "next"},
        ])
        assert KEY.lower() not in _flat(body).lower()
        assert body["messages"][0]["content"] == "here it is  thanks"
        assert body["messages"][1]["content"] == "got it"

    @pytest.mark.anyio
    async def test_the_key_in_a_card_and_a_stop_sequence_is_cut(self, real_key, wire):
        body = await _send(wire, [
            {"role": "system", "content": f"[Character: X]\nsecret {KEY.upper()} end"},
            {"role": "user", "content": "hi"},
        ], stop=[f"x{KEY}", "END"])
        assert KEY.lower() not in _flat(body).lower()
        assert "secret" in body["messages"][0]["content"]
        assert body["stop"] == ["x", "END"]

    @pytest.mark.anyio
    async def test_a_line_that_was_only_the_key_is_dropped(self, real_key, wire):
        body = await _send(wire, [
            {"role": "user", "content": KEY},
            {"role": "user", "content": "still here"},
        ])
        assert body["messages"] == [{"role": "user", "content": "still here"}]

    @pytest.mark.anyio
    async def test_a_short_stored_value_under_the_floor_cuts_nothing(self, db, wire):
        """Ground for the floor: the shared fixture's short key is not a
        needle, so its text rides untouched."""
        body = await _send(wire, [{"role": "user", "content": "sk-test-key here"}])
        assert body["messages"][0]["content"] == "sk-test-key here"

    @pytest.mark.anyio
    async def test_the_streaming_twin_cuts_it_too(self, real_key, wire):
        import openrouter
        async for _ in openrouter.complete_stream(
                [{"role": "user", "content": f"a {KEY} b"}],
                MODEL, {}, {"data_collection": "deny"}):
            pass
        assert len(wire) == 1
        assert KEY.lower() not in _flat(wire[0]).lower()
        assert wire[0]["messages"][0]["content"] == "a  b"

    @pytest.mark.anyio
    async def test_a_model_id_carrying_the_key_is_refused_before_the_body(
            self, real_key, wire, caplog):
        """The model field is a protocol value the scrub does not cut, so a
        key typed into it would go out as the model name. Both senders refuse
        it before a body exists, and nothing logged carries the key."""
        import openrouter
        with caplog.at_level(logging.DEBUG):
            with pytest.raises(openrouter.OpenRouterError) as plain:
                await openrouter.complete(
                    [{"role": "user", "content": "x"}],
                    f"vendor/{KEY.upper()}", {}, {"data_collection": "deny"})
            with pytest.raises(openrouter.OpenRouterError) as streamed:
                async for _ in openrouter.complete_stream(
                        [{"role": "user", "content": "x"}],
                        f"vendor/{KEY}", {}, {"data_collection": "deny"}):
                    pass
        assert plain.value.reason == openrouter.MODEL_KEY_REASON
        assert streamed.value.reason == openrouter.MODEL_KEY_REASON
        assert not plain.value.reached_provider
        assert wire == []
        assert not any(KEY.lower() in r.getMessage().lower() for r in caplog.records)

    @pytest.mark.anyio
    async def test_a_safeword_in_the_model_id_is_sent_and_never_logged(
            self, real_key, wire, caplog):
        """The safeword is not a reason to refuse a model id ("exit" in
        `vendor/exit-model`, test_safeword.py); it is kept out of the log
        because no line names a model id at all."""
        import notebook_store
        import openrouter
        notebook_store.set_safeword("bluebird")
        try:
            with caplog.at_level(logging.DEBUG):
                await openrouter.complete([{"role": "user", "content": "x"}],
                                          "vendor/bluebird-1", {},
                                          {"data_collection": "deny"})
        finally:
            notebook_store.set_safeword("")
        assert [b["model"] for b in wire] == ["vendor/bluebird-1"]
        assert caplog.records
        assert not any("bluebird" in r.getMessage() for r in caplog.records)

    @pytest.mark.anyio
    async def test_positive_control_an_ordinary_model_id_goes_and_is_not_logged(
            self, real_key, wire, caplog):
        """No log line names a model id. The request lines
        are still there (positive control), without the id."""
        import openrouter
        with caplog.at_level(logging.DEBUG):
            await openrouter.complete([{"role": "user", "content": "x"}],
                                      MODEL, {}, {"data_collection": "deny"})
            async for _ in openrouter.complete_stream(
                    [{"role": "user", "content": "x"}],
                    MODEL, {}, {"data_collection": "deny"}):
                pass
        assert [b["model"] for b in wire] == [MODEL, MODEL]
        messages = [r.getMessage() for r in caplog.records]
        assert any(m.startswith("Completion request") for m in messages)
        assert any(m.startswith("Streaming completion finished") for m in messages)
        assert not any(MODEL in m for m in messages)


# ── the typed message, end to end ──────────────────────────────────────────


class TestTheTypedMessageIsRefused:
    """Wired in routers/completions.py next to the safeword check, through
    `safeword_filter.api_key_in`."""

    def test_the_plain_route_sends_and_stores_nothing(self, client, provider):
        secrets_service.set_secret(config.SECRET_API_KEY, KEY)
        chat_id = make_chat(client, make_character(client))
        before = get_messages(client, chat_id)
        resp = client.post(f"/api/v1/chats/{chat_id}/complete",
                           json={"model_id": MODEL, "message": f"key: {KEY}"})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "api_key_in_message"
        assert provider.calls == []
        assert get_messages(client, chat_id) == before

    def test_positive_control_an_ordinary_message_still_goes(self, client, provider):
        secrets_service.set_secret(config.SECRET_API_KEY, KEY)
        chat_id = make_chat(client, make_character(client))
        resp = client.post(f"/api/v1/chats/{chat_id}/complete",
                           json={"model_id": MODEL, "message": "hello"})
        assert resp.status_code == 200, resp.text
        assert len(provider.calls) == 1
