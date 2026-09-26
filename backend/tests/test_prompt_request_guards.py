"""What the request path refuses, and what it adds, before anything is sent
or stored. Each check runs on every door a typed message enters.

- C9: a typed message holding the stored API key is refused whole
  (api_key_in_message), beside the safeword check, before the stream route
  stores the user turn.
- C4: a model id that makes the provider search the web is refused by name
  before anything is stored (model_web_search_blocked).
- D3: the `reasoning` field is derived from the cached catalogue.
- D1: samplers the user never set are not sent.
- The vault lock, through the real route, empties the sanitizer cache and
  drops the salt key; unlocking makes the cache writable again.
"""

from __future__ import annotations

import pytest

import config
import database
import prompt_layer
import prompt_sanitizer
import secrets_service
from conftest import TEST_VAULT_KEY, get_messages, make_character, make_chat

MODEL = "test/model-1"
#: Long enough for the key matcher (safeword_filter.KEY_MIN_CHARS), and mixed
#: case so a case-sensitive match would be caught (W2).
KEY = "sk-or-v1-0123456789AbCdEf0123456789abcdefXYZ"


@pytest.fixture()
def stream_calls(monkeypatch):
    import routers.completions as cr
    calls = []

    def fake_stream(messages, model_id, gen_params, provider, **kwargs):
        calls.append(messages)

        async def gen():
            yield "streamed reply"
        return gen()

    monkeypatch.setattr(cr, "complete_stream", fake_stream)
    return calls


def _exchange(chat_id: int, user_text: str) -> tuple[int, int]:
    """A stored user turn and its reply, as an older conversation would hold."""
    with database.get_db() as con:
        uid = con.execute("INSERT INTO messages (chat_id, role, content) "
                          "VALUES (?, 'user', ?)", (chat_id, user_text)).lastrowid
        aid = con.execute("INSERT INTO messages (chat_id, role, content) "
                          "VALUES (?, 'assistant', 'old reply')", (chat_id,)).lastrowid
    return uid, aid


# ── C9 ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("route", ["complete", "complete/stream"])
def test_c9_a_typed_key_is_refused_and_nothing_is_stored(client, provider, stream_calls, route):
    secrets_service.set_secret(config.SECRET_API_KEY, KEY)
    chat = make_chat(client, make_character(client))
    before = get_messages(client, chat)
    resp = client.post(f"/api/v1/chats/{chat}/{route}",
                       json={"model_id": MODEL, "message": f"my key is {KEY.lower()}"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "api_key_in_message"
    assert KEY.lower() not in resp.text and KEY not in resp.text
    assert provider.calls == [] and stream_calls == []
    assert get_messages(client, chat) == before


@pytest.mark.parametrize("route", ["complete", "complete/stream"])
def test_c9_ground_control_an_ordinary_message_goes(client, provider, stream_calls, route):
    secrets_service.set_secret(config.SECRET_API_KEY, KEY)
    chat = make_chat(client, make_character(client))
    resp = client.post(f"/api/v1/chats/{chat}/{route}",
                       json={"model_id": MODEL, "message": "hello"})
    assert resp.status_code == 200, resp.text
    assert len(provider.calls) + len(stream_calls) == 1


@pytest.mark.parametrize("stream", [False, True])
def test_c9_regenerating_a_stored_key_message_is_refused(client, provider, stream_calls, stream):
    secrets_service.set_secret(config.SECRET_API_KEY, KEY)
    chat = make_chat(client, make_character(client))
    _uid, aid = _exchange(chat, f"key {KEY}")
    before = get_messages(client, chat)
    path = f"/api/v1/chats/{chat}/messages/{aid}/regenerate" + ("/stream" if stream else "")
    resp = client.post(path, json={"model_id": MODEL})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "api_key_in_message"
    assert provider.calls == [] and stream_calls == []
    assert get_messages(client, chat) == before


@pytest.mark.parametrize("stream", [False, True])
def test_c9_an_edit_that_adds_the_key_is_refused(client, provider, stream_calls, stream):
    secrets_service.set_secret(config.SECRET_API_KEY, KEY)
    chat = make_chat(client, make_character(client))
    uid, _aid = _exchange(chat, "an innocent question")
    before = get_messages(client, chat)
    path = f"/api/v1/chats/{chat}/messages/{uid}/edit" + ("/stream" if stream else "")
    resp = client.post(path, json={"model_id": MODEL, "message": f"now {KEY}"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "api_key_in_message"
    assert provider.calls == [] and stream_calls == []
    assert get_messages(client, chat) == before


def test_c9_positive_control_an_edit_without_the_key_goes(client, provider):
    secrets_service.set_secret(config.SECRET_API_KEY, KEY)
    chat = make_chat(client, make_character(client))
    uid, _aid = _exchange(chat, "an innocent question")
    resp = client.post(f"/api/v1/chats/{chat}/messages/{uid}/edit",
                       json={"model_id": MODEL, "message": "a better question"})
    assert resp.status_code == 200, resp.text


# ── C4 ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("route", ["complete", "complete/stream"])
@pytest.mark.parametrize("model_id", ["openai/gpt-4o:online", "openrouter/auto"])
def test_c4_a_web_searching_model_is_refused_before_anything_is_stored(
        client, provider, stream_calls, route, model_id):
    chat = make_chat(client, make_character(client))
    before = get_messages(client, chat)
    resp = client.post(f"/api/v1/chats/{chat}/{route}",
                       json={"model_id": model_id, "message": "hello"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "model_web_search_blocked"
    assert provider.calls == [] and stream_calls == []
    assert get_messages(client, chat) == before


def test_c4_ground_control_the_plain_model_goes(client, provider):
    chat = make_chat(client, make_character(client))
    resp = client.post(f"/api/v1/chats/{chat}/complete",
                       json={"model_id": "openai/gpt-4o", "message": "hello"})
    assert resp.status_code == 200, resp.text


# ── D3 and D1 ───────────────────────────────────────────────────────────────

def _catalogue(monkeypatch, meta):
    import openrouter
    import routers.completions as cr
    monkeypatch.setattr(openrouter, "get_cached_model_metadata", lambda mid: meta)
    monkeypatch.setattr(cr, "get_cached_model_metadata", lambda mid: meta)


def test_d3_a_mandatory_thinker_gets_the_lowest_effort_and_no_returned_text(
        client, provider, monkeypatch):
    _catalogue(monkeypatch, {"context_length": 32000, "max_completion_tokens": 2048,
                             "supported_parameters": ["reasoning", "temperature"],
                             "reasoning": {"mandatory": True,
                                           "supported_efforts": ["high", "low", "medium"]}})
    chat = make_chat(client, make_character(client))
    assert client.post(f"/api/v1/chats/{chat}/complete",
                       json={"model_id": MODEL, "message": "hi"}).status_code == 200
    assert provider.calls[-1]["gen_params"]["reasoning"] == {"exclude": True, "effort": "low"}


def test_d3_an_optional_thinker_is_switched_off(client, provider, monkeypatch):
    _catalogue(monkeypatch, {"context_length": 32000,
                             "supported_parameters": ["reasoning"],
                             "reasoning": {"mandatory": False}})
    chat = make_chat(client, make_character(client))
    client.post(f"/api/v1/chats/{chat}/complete", json={"model_id": MODEL, "message": "hi"})
    assert provider.calls[-1]["gen_params"]["reasoning"] == {"enabled": False}


def test_d3_ground_control_a_model_without_reasoning_gets_no_field(client, provider, monkeypatch):
    _catalogue(monkeypatch, {"context_length": 32000, "supported_parameters": ["temperature"]})
    chat = make_chat(client, make_character(client))
    client.post(f"/api/v1/chats/{chat}/complete", json={"model_id": MODEL, "message": "hi"})
    assert "reasoning" not in provider.calls[-1]["gen_params"]


def test_d1_samplers_the_user_never_set_are_not_sent(client, provider):
    chat = make_chat(client, make_character(client))
    client.post(f"/api/v1/chats/{chat}/complete", json={"model_id": MODEL, "message": "hi"})
    sent = provider.calls[-1]["gen_params"]
    for sampler in ("temperature", "top_p", "top_k", "min_p", "frequency_penalty",
                    "presence_penalty", "repetition_penalty"):
        assert sampler not in sent


def test_d1_positive_control_a_set_sampler_is_sent(client, provider):
    chat = make_chat(client, make_character(client))
    client.post(f"/api/v1/chats/{chat}/complete",
                json={"model_id": MODEL, "message": "hi",
                      "generation_params": {"temperature": 0.5}})
    assert provider.calls[-1]["gen_params"]["temperature"] == 0.5


# ── the vault lock, through the real route (W2, ST-12) ──────────────────────

def test_the_vault_lock_empties_the_prompt_cache_and_drops_the_salt_key(client, provider):
    import routers.vault as vault
    import vault_state
    card = client.patch(f"/api/v1/characters/{make_character(client)}",
                        json={"description": "a cached card field (OOC: x)"})
    assert card.status_code == 200
    chat = make_chat(client, card.json()["id"])
    assert client.post(f"/api/v1/chats/{chat}/complete",
                       json={"model_id": MODEL, "message": "hi"}).status_code == 200
    # Positive control: there is something to drop.
    assert len(prompt_sanitizer.CACHE) > 0
    assert prompt_layer.has_process_key()

    assert client.post("/api/v1/vault/lock").status_code == 200

    assert len(prompt_sanitizer.CACHE) == 0, "decrypted card text survived the lock"
    assert not prompt_layer.has_process_key(), "the salt key survived the lock"
    prompt_sanitizer.sanitize_field("written while locked")
    assert len(prompt_sanitizer.CACHE) == 0, "the cache stored while the vault was locked"

    # Unlock: the bootstrap lifts the mark, and the cache stores again.
    vault_state.set_key(TEST_VAULT_KEY)
    vault._bootstrap_unlocked()
    prompt_sanitizer.sanitize_field("written after unlock")
    assert len(prompt_sanitizer.CACHE) == 1
