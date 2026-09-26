"""D3, the Thinking switch, through the real completion route.

`generation_params.thinking` is true only when the user turns Thinking on.
It reaches `reasoning_param` as `thinking=True` for a model whose reasoning is
OPTIONAL, so that model is sent no `reasoning` field (the provider default,
thinking on). A mandatory model keeps its lowest effort and `exclude`, and an
unknown one keeps nothing, whatever the switch says. The switch itself never
crosses the wire under its own name.
"""
from __future__ import annotations

import pytest

import openrouter
import routers.completions as completions_router
from tests.conftest import make_character, make_chat

_CATALOGUE = {
    "opt/thinker": {"id": "opt/thinker", "context_length": 32000,
                    "max_completion_tokens": 4000,
                    "supported_parameters": ["temperature", "max_tokens", "reasoning"],
                    "reasoning": {"mandatory": False, "supported_efforts": ["low", "high"]}},
    "must/thinker": {"id": "must/thinker", "context_length": 32000,
                     "max_completion_tokens": 4000,
                     "supported_parameters": ["max_tokens", "reasoning"],
                     "reasoning": {"mandatory": True, "supported_efforts": ["high", "low"]}},
    "unknown/thinker": {"id": "unknown/thinker", "context_length": 32000,
                        "max_completion_tokens": 4000,
                        "supported_parameters": ["max_tokens", "reasoning"],
                        "reasoning": None},
}


@pytest.fixture
def catalogue(monkeypatch):
    lookup = _CATALOGUE.get
    monkeypatch.setattr(openrouter, "get_cached_model_metadata", lookup)
    monkeypatch.setattr(completions_router, "get_cached_model_metadata", lookup)


def _gen(client, provider, model_id: str, **gen) -> dict:
    chat = make_chat(client, make_character(client))
    r = client.post(f"/api/v1/chats/{chat}/complete", json={
        "message": "hi", "model_id": model_id,
        "generation_params": {"max_tokens": 100, **gen}})
    assert r.status_code == 200, r.text
    return provider.calls[-1]["gen_params"]


def test_ground_switch_off_turns_optional_reasoning_off(client, provider, catalogue):
    assert _gen(client, provider, "opt/thinker")["reasoning"] == {"enabled": False}


def test_switch_on_leaves_optional_reasoning_to_the_provider(client, provider, catalogue):
    sent = _gen(client, provider, "opt/thinker", thinking=True)
    assert "reasoning" not in sent
    assert "thinking" not in sent, "the switch crossed the wire under its own name"
    assert sent["max_tokens"] == 100   # positive control: the params still went


def test_a_mandatory_model_keeps_its_default_whatever_the_switch(client, provider, catalogue):
    off = _gen(client, provider, "must/thinker")["reasoning"]
    on = _gen(client, provider, "must/thinker", thinking=True)["reasoning"]
    assert off == on == {"exclude": True, "effort": "low"}


def test_an_unknown_model_gets_nothing_either_way(client, provider, catalogue):
    assert "reasoning" not in _gen(client, provider, "unknown/thinker")
    assert "reasoning" not in _gen(client, provider, "unknown/thinker", thinking=True)


def test_a_string_is_not_a_yes(client, provider, catalogue):
    chat = make_chat(client, make_character(client))
    r = client.post(f"/api/v1/chats/{chat}/complete", json={
        "message": "hi", "model_id": "opt/thinker",
        "generation_params": {"thinking": "true"}})
    assert r.status_code == 422
