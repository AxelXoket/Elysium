"""Regression tests for the complete/regenerate flows.

These lock in the fixes for:
- regenerate deleting the old assistant message before the provider call
  (data loss on provider failure),
- the user message appearing twice in the regenerate payload,
- corrupted selected_persona_id causing a 500,
- outgoing max_tokens exceeding the reserved output budget.
"""

from openrouter import OpenRouterError

from conftest import make_character, make_chat, get_messages


BODY = {
    "message": "How are you?",
    "model_id": "test/model-1",
}


def _payload_user_turns(call: dict, text: str) -> list[dict]:
    return [
        m for m in call["messages"]
        if m["role"] == "user" and m["content"] == text
    ]


# ---------------------------------------------------------------------------
# complete
# ---------------------------------------------------------------------------

def test_complete_persists_user_and_assistant(client, provider):
    char_id = make_character(client)
    chat_id = make_chat(client, char_id)

    resp = client.post(f"/api/v1/chats/{chat_id}/complete", json=BODY)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["user_message"]["content"] == "How are you?"
    assert data["assistant_message"]["content"] == "fake assistant reply"

    msgs = get_messages(client, chat_id)
    # first_mes + user + assistant
    assert [m["role"] for m in msgs] == ["assistant", "user", "assistant"]

    # The payload contains the user turn exactly once.
    assert len(_payload_user_turns(provider.calls[0], "How are you?")) == 1


def test_complete_provider_failure_persists_nothing(client, provider):
    char_id = make_character(client)
    chat_id = make_chat(client, char_id)
    provider.error = OpenRouterError("openrouter_timeout")

    resp = client.post(f"/api/v1/chats/{chat_id}/complete", json=BODY)
    assert resp.status_code == 504
    msgs = get_messages(client, chat_id)
    assert [m["role"] for m in msgs] == ["assistant"]  # only first_mes


# ---------------------------------------------------------------------------
# regenerate
# ---------------------------------------------------------------------------

def _seed_exchange(client, provider) -> tuple[int, int]:
    """Create char+chat and one completed exchange; return (chat_id, asst_id)."""
    char_id = make_character(client)
    chat_id = make_chat(client, char_id)
    resp = client.post(f"/api/v1/chats/{chat_id}/complete", json=BODY)
    assert resp.status_code == 200
    return chat_id, resp.json()["assistant_message"]["id"]


def test_regenerate_no_duplicate_user_turn(client, provider):
    chat_id, asst_id = _seed_exchange(client, provider)
    provider.response_text = "second reply"

    resp = client.post(
        f"/api/v1/chats/{chat_id}/messages/{asst_id}/regenerate",
        json={"model_id": "test/model-1"},
    )
    assert resp.status_code == 200, resp.text

    regen_call = provider.calls[-1]
    # The user turn must appear exactly once in the payload (was duplicated).
    assert len(_payload_user_turns(regen_call, "How are you?")) == 1
    # The old assistant reply must not leak into the history.
    assert not any(
        m["content"] == "fake assistant reply" and m["role"] == "assistant"
        for m in regen_call["messages"][1:]  # skip system block
    )

    # Variant contract: the old reply is kept as an INACTIVE sibling, the new
    # one is the group's active row - nothing is deleted anymore.
    msgs = get_messages(client, chat_id)
    assert [m["role"] for m in msgs] == [
        "assistant", "user", "assistant", "assistant",
    ]
    active = [m for m in msgs if m["active"]]
    assert [m["role"] for m in active] == ["assistant", "user", "assistant"]
    assert active[-1]["content"] == "second reply"
    assert active[-1]["id"] != asst_id  # new row is the active variant

    old = next(m for m in msgs if m["id"] == asst_id)
    assert old["active"] is False
    assert old["content"] == "fake assistant reply"
    assert old["variant_group"] == asst_id            # anchor = first row's id
    assert active[-1]["variant_group"] == asst_id
    assert [old["variant_index"], old["variant_count"]] == [0, 2]
    assert [active[-1]["variant_index"], active[-1]["variant_count"]] == [1, 2]


def test_regenerate_provider_failure_keeps_old_message(client, provider):
    chat_id, asst_id = _seed_exchange(client, provider)
    provider.error = OpenRouterError("openrouter_rate_limited")

    resp = client.post(
        f"/api/v1/chats/{chat_id}/messages/{asst_id}/regenerate",
        json={"model_id": "test/model-1"},
    )
    assert resp.status_code == 429

    # Data-loss regression: the original assistant message must survive.
    msgs = get_messages(client, chat_id)
    assert msgs[-1]["id"] == asst_id
    assert msgs[-1]["content"] == "fake assistant reply"


def test_regenerate_rejects_non_last_message(client, provider):
    chat_id, _ = _seed_exchange(client, provider)
    msgs = get_messages(client, chat_id)
    first_mes_id = msgs[0]["id"]  # assistant, but not last

    resp = client.post(
        f"/api/v1/chats/{chat_id}/messages/{first_mes_id}/regenerate",
        json={"model_id": "test/model-1"},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "not_last_assistant_message"


# ---------------------------------------------------------------------------
# hardening
# ---------------------------------------------------------------------------

def test_corrupt_selected_persona_does_not_500(client, provider):
    import database
    database.set_setting("selected_persona_id", "not-a-number")

    char_id = make_character(client)
    chat_id = make_chat(client, char_id)
    resp = client.post(f"/api/v1/chats/{chat_id}/complete", json=BODY)
    assert resp.status_code == 200, resp.text

    # GET /settings must also survive the corrupted value.
    resp = client.get("/api/v1/settings")
    assert resp.status_code == 200
    assert resp.json()["selected_persona_id"] is None


def test_stop_sequences_pass_through_to_provider(client, provider):
    """stop reaches the provider payload after generic filtering (no cached
    metadata → no supported_parameters restriction applied)."""
    char_id = make_character(client)
    chat_id = make_chat(client, char_id)

    resp = client.post(f"/api/v1/chats/{chat_id}/complete", json={
        **BODY,
        "generation_params": {"stop": ["\nUser:", "###"], "temperature": 0.7},
    })
    assert resp.status_code == 200, resp.text

    sent = provider.calls[-1]["gen_params"]
    assert sent["stop"] == ["\nUser:", "###"]
    assert sent["temperature"] == 0.7


def test_stop_survives_supported_parameters_filter(client, provider, monkeypatch):
    """The real carve-out: when a model advertises supported_parameters that
    EXCLUDE 'stop', an unsupported param (top_k) is filtered out but stop is
    kept anyway (k in supported OR k == 'stop'). Guards against removing the
    carve-out at completions.py."""
    import routers.completions as cr
    monkeypatch.setattr(cr, "get_cached_model_metadata", lambda mid: {
        "id": "test/model-1",
        "context_length": 32768,
        "max_completion_tokens": 4096,
        "supported_parameters": ["temperature"],  # non-empty, lacks stop + top_k
    })

    char_id = make_character(client)
    chat_id = make_chat(client, char_id)
    resp = client.post(f"/api/v1/chats/{chat_id}/complete", json={
        **BODY,
        "generation_params": {
            "stop": ["###"], "temperature": 0.5, "top_k": 40,
        },
    })
    assert resp.status_code == 200, resp.text

    sent = provider.calls[-1]["gen_params"]
    assert sent["stop"] == ["###"]        # kept by the carve-out
    assert sent["temperature"] == 0.5     # supported → kept
    assert "top_k" not in sent            # unsupported → filtered out


def test_large_post_history_instruction_counts_toward_budget(client, provider):
    """A big post_history_instruction must be reserved in the budget so it can
    trigger context_too_large instead of silently overflowing the context."""
    # Character with a huge PHI; tiny budget so PHI alone blows it.
    resp = client.post("/api/v1/characters", json={
        "name": "PhiChar",
        "post_history_instruction": "X" * 8000,  # ~2666 tokens at 3 chars/tok
    })
    char_id = resp.json()["id"]
    chat_id = make_chat(client, char_id)

    resp = client.post(f"/api/v1/chats/{chat_id}/complete", json={
        **BODY,
        "context_budget_tokens": 512,  # far smaller than the PHI
    })
    assert resp.status_code == 400
    assert resp.json()["detail"] == "context_too_large"


def test_max_tokens_clamped_to_reserved_budget(client, provider):
    char_id = make_character(client)
    chat_id = make_chat(client, char_id)

    resp = client.post(f"/api/v1/chats/{chat_id}/complete", json={
        **BODY,
        "generation_params": {"max_tokens": 131072},
        "context_budget_tokens": 512,
    })
    assert resp.status_code == 200, resp.text

    sent = provider.calls[-1]["gen_params"]
    # effective budget 512 tokens, safety min(256, 512//8)=64 → 448 tokens
    # of budget; the requested reservation exceeds it, so the reservation is
    # halved (in chars) and max_tokens must be clamped to match it.
    assert sent["max_tokens"] <= 448
    assert sent["max_tokens"] >= 1
