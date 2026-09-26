"""C4 and D3 helpers in openrouter.py: blocked model ids, and the reasoning field.

`reasoning_param` is driven through the real model list path: a /models reply
is served by an httpx MockTransport, normalised by the app, cached, and read
back. So the tests cover `_normalise_model` keeping the reasoning object as
well as the decision itself.
"""
from __future__ import annotations

import json

import httpx
import pytest

import openrouter
from openrouter import REASONING_EFFORT_ORDER, model_id_blocked, reasoning_param


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _raw(model_id: str, *, params=("temperature",), reasoning=None) -> dict:
    row = {"id": model_id, "name": model_id,
           "supported_parameters": list(params)}
    if reasoning is not None:
        row["reasoning"] = reasoning
    return row


CATALOGUE = [
    _raw("plain/model"),
    _raw("opt/thinker", params=("temperature", "reasoning"),
         reasoning={"mandatory": False, "supported_efforts": ["low", "high"]}),
    _raw("must/thinker", params=("reasoning",),
         reasoning={"mandatory": True, "supported_efforts": ["high", "medium", "low"]}),
    _raw("must/noefforts", params=("reasoning",),
         reasoning={"mandatory": True}),
    _raw("unknown/thinker", params=("reasoning",)),
    _raw("weird/reasoning", params=("reasoning",), reasoning="yes"),
]


@pytest.fixture
async def cached(monkeypatch, db):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": CATALOGUE})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(openrouter, "get_client", lambda: client)
    await openrouter.fetch_models(refresh=True)
    yield


class TestReasoningParam:
    @pytest.mark.anyio
    async def test_ground_a_model_without_reasoning_gets_nothing(self, cached):
        assert reasoning_param("plain/model") is None

    @pytest.mark.anyio
    async def test_the_normaliser_keeps_the_reasoning_object(self, cached):
        meta = openrouter.get_cached_model_metadata("must/thinker")
        assert meta["reasoning"] == CATALOGUE[2]["reasoning"]
        assert openrouter.get_cached_model_metadata("plain/model")["reasoning"] is None
        assert openrouter.get_cached_model_metadata("weird/reasoning")["reasoning"] is None

    @pytest.mark.anyio
    async def test_optional_reasoning_is_turned_off(self, cached):
        assert reasoning_param("opt/thinker") == {"enabled": False}

    @pytest.mark.anyio
    async def test_mandatory_reasoning_gets_the_lowest_effort_and_exclude(self, cached):
        out = reasoning_param("must/thinker")
        assert out == {"exclude": True, "effort": "low"}
        # never a disable on a model that rejects one
        assert "enabled" not in out

    @pytest.mark.anyio
    async def test_the_order_is_the_constant_not_the_listing(self, cached):
        listed = CATALOGUE[2]["reasoning"]["supported_efforts"]
        lowest = next(e for e in REASONING_EFFORT_ORDER if e in listed)
        assert reasoning_param("must/thinker")["effort"] == lowest
        assert listed[0] != lowest, "the fixture is not testing the ordering"

    @pytest.mark.anyio
    async def test_mandatory_with_no_listed_efforts_still_excludes(self, cached):
        assert reasoning_param("must/noefforts") == {"exclude": True}

    @pytest.mark.anyio
    async def test_no_reasoning_object_means_hands_off(self, cached):
        assert reasoning_param("unknown/thinker") is None
        assert reasoning_param("weird/reasoning") is None

    @pytest.mark.anyio
    async def test_the_thinking_toggle_restores_the_provider_default(self, cached):
        assert reasoning_param("opt/thinker", thinking=True) is None
        assert reasoning_param("must/thinker", thinking=True) is None

    def test_no_cached_list_means_nothing_is_sent(self):
        assert reasoning_param("opt/thinker") is None


ORDINARY_IDS = [
    "vendor/model", "vendor/model:free", "vendor/model:nitro",
    "vendor/online-model", "onlineco/model", "vendor/model-online",
    "open-router/auto", "openrouter/horizon-alpha", "vendor/researcher",
    "vendor/preset-model", "deepseek/deepseek-v4-flash-0731",
]
BLOCKED_IDS = [
    "vendor/model:online", "vendor/model:ONLINE", " vendor/model:online ",
    "vendor/model:free:online", "vendor/model:online:nitro",
    "openrouter/auto", "OpenRouter/Auto", "openrouter/auto:nitro",
    "@preset/my-preset", "@Preset/x",
    "perplexity/sonar", "perplexity/sonar-pro", "perplexity/SONAR-reasoning",
    "openai/gpt-4o-search-preview", "openai/o4-mini-deep-research",
]


class TestModelIdBlocked:
    @pytest.mark.parametrize("model_id", ORDINARY_IDS)
    def test_ground_ordinary_ids_pass(self, model_id):
        assert not model_id_blocked(model_id)

    @pytest.mark.parametrize("model_id", BLOCKED_IDS)
    def test_web_search_and_router_ids_are_blocked(self, model_id):
        assert model_id_blocked(model_id)


@pytest.fixture
def wire(monkeypatch) -> list[dict]:
    import json
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if body.get("stream"):
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"},
                content=(b'data: {"choices":[{"delta":{"content":"ok"},'
                         b'"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'))
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "stop", "message": {"content": "ok"}}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(openrouter, "get_client", lambda: client)
    return bodies


class TestTheSendPointRefusesBlockedIds:
    """The guard sits in both payload builders, before a client is built, so
    the chat routes and the notebook reader all pass through it."""

    @pytest.mark.anyio
    async def test_ground_an_ordinary_id_is_sent(self, db, wire):
        await openrouter.complete([{"role": "user", "content": "hi"}],
                                  "vendor/model", {}, {})
        async for _ in openrouter.complete_stream(
                [{"role": "user", "content": "hi"}], "vendor/model", {}, {}):
            pass
        assert [b["model"] for b in wire] == ["vendor/model", "vendor/model"]

    @pytest.mark.anyio
    @pytest.mark.parametrize("model_id", ["vendor/m:online", "perplexity/sonar",
                                          "openrouter/auto", "@preset/p"])
    async def test_plain_route_sends_nothing(self, db, wire, model_id):
        with pytest.raises(openrouter.OpenRouterError) as err:
            await openrouter.complete([{"role": "user", "content": "hi"}],
                                      model_id, {}, {})
        assert err.value.reason == openrouter.MODEL_BLOCKED_REASON
        assert err.value.reached_provider is False
        assert wire == []

    @pytest.mark.anyio
    async def test_streaming_route_sends_nothing(self, db, wire):
        with pytest.raises(openrouter.OpenRouterError) as err:
            async for _ in openrouter.complete_stream(
                    [{"role": "user", "content": "hi"}],
                    "vendor/m:ONLINE", {}, {}):
                pass
        assert err.value.reason == openrouter.MODEL_BLOCKED_REASON
        assert err.value.reached_provider is False
        assert wire == []

    def test_the_chat_route_sends_nothing(self, client, wire):
        """End to end through the real provider client (no stand-in)."""
        from tests.conftest import make_character, make_chat
        chat_id = make_chat(client, make_character(client))
        resp = client.post(f"/api/v1/chats/{chat_id}/complete",
                           json={"model_id": "perplexity/sonar-pro", "message": "hi"})
        assert resp.status_code >= 400
        assert wire == []
        # Positive control on the same chat: an ordinary id goes out.
        ok = client.post(f"/api/v1/chats/{chat_id}/complete",
                         json={"model_id": "vendor/model", "message": "hi"})
        assert ok.status_code == 200, ok.text
        assert len(wire) == 1

    def test_the_chat_route_names_the_refusal(self, client, wire):
        from tests.conftest import make_character, make_chat
        chat_id = make_chat(client, make_character(client))
        resp = client.post(f"/api/v1/chats/{chat_id}/complete",
                           json={"model_id": "vendor/m:online", "message": "hi"})
        assert resp.status_code == 400
        assert resp.json()["detail"] == openrouter.MODEL_BLOCKED_REASON


class TestClaudeCacheControl:
    """Ek P C8: one top-level `cache_control` for `anthropic/...` ids, read
    off the JSON that actually leaves (the httpx transport), both senders."""

    async def _bodies(self, model_id: str, wire) -> list[dict]:
        await openrouter.complete([{"role": "user", "content": "hi"}],
                                  model_id, {}, {})
        async for _ in openrouter.complete_stream(
                [{"role": "user", "content": "hi"}], model_id, {}, {}):
            pass
        assert len(wire) == 2
        return wire

    @pytest.mark.anyio
    @pytest.mark.parametrize("model_id", ["anthropic/claude-sonnet-5",
                                          " Anthropic/Claude-Opus-5:beta"])
    async def test_claude_gets_one_top_level_ephemeral_field(self, db, wire, model_id):
        for body in await self._bodies(model_id, wire):
            assert body["cache_control"] == openrouter.ANTHROPIC_CACHE_CONTROL
            assert body["cache_control"] == {"type": "ephemeral"}
            # Top level only: no message part carries its own marker.
            assert "cache_control" not in json.dumps(body["messages"])

    @pytest.mark.anyio
    @pytest.mark.parametrize("model_id", ["openai/gpt-4o", "google/gemini-2.5-pro",
                                          "vendor/anthropic-like", "meta/claude-ish"])
    async def test_ground_other_models_get_nothing(self, db, wire, model_id):
        for body in await self._bodies(model_id, wire):
            assert "cache_control" not in body

    @pytest.mark.anyio
    async def test_a_safeword_spelled_like_the_value_does_not_cut_it(self, db, wire):
        import notebook_store
        notebook_store.set_safeword("ephemeral")
        try:
            for body in await self._bodies("anthropic/claude-sonnet-5", wire):
                assert body["cache_control"] == {"type": "ephemeral"}
        finally:
            notebook_store.set_safeword("")

    def test_the_value_is_a_fresh_copy_each_call(self):
        first = openrouter.cache_control_for("anthropic/x")
        first["type"] = "changed"
        assert openrouter.cache_control_for("anthropic/x") == {"type": "ephemeral"}
        assert openrouter.cache_control_for("openai/x") is None
