"""Tests for response variants ("swipes"): regenerate-as-append, activate,
group-aware delete, active-only context, and the SSE contract.

Contract under test:
- Regenerate never deletes: the old reply becomes an inactive sibling in a
  variant group anchored at the FIRST row's id; the new reply is active.
- POST /chats/{id}/messages/{mid}/activate flips which sibling is active
  (last-group-only in v1).
- Context assembly and chat message_count see ACTIVE rows only.
- Delete-and-following expands to the group anchor.
"""

import json as _json

from conftest import make_character, make_chat, get_messages

BODY = {"message": "How are you?", "model_id": "test/model-1"}


def _seed_exchange(client, provider) -> tuple[int, int]:
    char_id = make_character(client)
    chat_id = make_chat(client, char_id)
    resp = client.post(f"/api/v1/chats/{chat_id}/complete", json=BODY)
    assert resp.status_code == 200, resp.text
    return chat_id, resp.json()["assistant_message"]["id"]


def _regen(client, chat_id: int, target_id: int, text: str, provider) -> dict:
    provider.response_text = text
    resp = client.post(
        f"/api/v1/chats/{chat_id}/messages/{target_id}/regenerate",
        json={"model_id": "test/model-1"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Regenerate = append variant
# ---------------------------------------------------------------------------

def test_regenerate_response_carries_variant_fields(client, provider):
    chat_id, v0 = _seed_exchange(client, provider)
    data = _regen(client, chat_id, v0, "variant one", provider)

    asst = data["assistant_message"]
    assert asst["variant_group"] == v0
    assert asst["active"] is True
    assert asst["variant_index"] == 1
    assert asst["variant_count"] == 2
    assert data["deactivated_message_id"] == v0


def test_second_regenerate_grows_the_same_group(client, provider):
    chat_id, v0 = _seed_exchange(client, provider)
    d1 = _regen(client, chat_id, v0, "variant one", provider)
    v1 = d1["assistant_message"]["id"]
    # Regenerating the now-active row appends to the SAME group.
    d2 = _regen(client, chat_id, v1, "variant two", provider)

    asst = d2["assistant_message"]
    assert asst["variant_group"] == v0
    assert asst["variant_count"] == 3
    assert asst["variant_index"] == 2
    assert d2["deactivated_message_id"] == v1

    msgs = get_messages(client, chat_id)
    group = [m for m in msgs if (m["variant_group"] or m["id"]) == v0]
    assert [m["active"] for m in group] == [False, False, True]


# ---------------------------------------------------------------------------
# Activate (variant navigation)
# ---------------------------------------------------------------------------

def test_activate_switches_active_variant(client, provider):
    chat_id, v0 = _seed_exchange(client, provider)
    d1 = _regen(client, chat_id, v0, "variant one", provider)
    v1 = d1["assistant_message"]["id"]

    resp = client.post(f"/api/v1/chats/{chat_id}/messages/{v0}/activate")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["message"]["id"] == v0
    assert data["message"]["active"] is True
    assert data["message"]["variant_index"] == 0
    assert data["message"]["variant_count"] == 2
    assert data["deactivated_message_id"] == v1

    msgs = get_messages(client, chat_id)
    by_id = {m["id"]: m for m in msgs}
    assert by_id[v0]["active"] is True
    assert by_id[v1]["active"] is False

    # Idempotent: re-activating the active row is a no-op.
    resp = client.post(f"/api/v1/chats/{chat_id}/messages/{v0}/activate")
    assert resp.status_code == 200
    assert resp.json()["deactivated_message_id"] is None


def test_activate_rejects_non_assistant_and_non_last(client, provider):
    chat_id, v0 = _seed_exchange(client, provider)
    _regen(client, chat_id, v0, "variant one", provider)

    msgs = get_messages(client, chat_id)
    user_id = next(m["id"] for m in msgs if m["role"] == "user")
    resp = client.post(f"/api/v1/chats/{chat_id}/messages/{user_id}/activate")
    assert resp.status_code == 422
    assert resp.json()["detail"] == "not_a_variant_target"

    # Continue the conversation - the group is no longer last.
    resp = client.post(f"/api/v1/chats/{chat_id}/complete", json=BODY)
    assert resp.status_code == 200
    resp = client.post(f"/api/v1/chats/{chat_id}/messages/{v0}/activate")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "variant_group_not_last"

    resp = client.post(f"/api/v1/chats/{chat_id}/messages/99999/activate")
    assert resp.status_code == 404


def test_regenerate_after_switch_back_does_not_conflict(client, provider):
    """Pitfall: last-message checks against MAX(id) break once an inactive
    sibling holds the newest id. Regenerating while an OLDER variant is
    active must still work and append to the group."""
    chat_id, v0 = _seed_exchange(client, provider)
    _regen(client, chat_id, v0, "variant one", provider)
    client.post(f"/api/v1/chats/{chat_id}/messages/{v0}/activate")

    d2 = _regen(client, chat_id, v0, "variant two", provider)
    assert d2["assistant_message"]["variant_count"] == 3
    assert d2["deactivated_message_id"] == v0

    msgs = get_messages(client, chat_id)
    active = [m for m in msgs if m["active"]]
    assert active[-1]["content"] == "variant two"


def test_regenerate_reports_sibling_deactivated_at_swap_time(
    client, provider, monkeypatch,
):
    """If the active variant changes while the provider runs (e.g. a second
    client activates an older sibling), deactivated_message_id must name the
    row deactivated AT SWAP TIME - reporting the pre-call id would leave the
    client cache with two active-looking rows."""
    import routers.completions as completions_router
    from database import get_db

    chat_id, v0 = _seed_exchange(client, provider)
    d1 = _regen(client, chat_id, v0, "variant one", provider)
    v1 = d1["assistant_message"]["id"]  # active going into the next regen

    async def flipping_complete(messages, model_id, gen_params, provider_dict):
        # Mid-provider-call: another client switches the group back to v0
        # (mirrors POST .../activate without a nested TestClient call).
        with get_db() as con:
            con.execute(
                "UPDATE messages SET variant_group = ?, active = 0 "
                "WHERE chat_id = ? AND COALESCE(variant_group, id) = ?",
                (v0, chat_id, v0),
            )
            con.execute("UPDATE messages SET active = 1 WHERE id = ?", (v0,))
        return {"choices": [{"message": {"content": "variant two"}}]}

    monkeypatch.setattr(completions_router, "complete", flipping_complete)

    resp = client.post(
        f"/api/v1/chats/{chat_id}/messages/{v1}/regenerate",
        json={"model_id": "test/model-1"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # v0 was the active row at swap time - THAT is what this swap deactivated.
    assert data["deactivated_message_id"] == v0

    msgs = get_messages(client, chat_id)
    group_actives = [
        m["id"] for m in msgs
        if (m["variant_group"] or m["id"]) == v0 and m["active"]
    ]
    # Exactly one active row in the regenerated group (the greeting is its
    # own singleton group and stays active independently).
    assert group_actives == [data["assistant_message"]["id"]]


# ---------------------------------------------------------------------------
# Active-only context + counts
# ---------------------------------------------------------------------------

def test_context_uses_only_the_active_variant(client, provider):
    chat_id, v0 = _seed_exchange(client, provider)
    _regen(client, chat_id, v0, "variant one", provider)
    # Switch back to v0, then send a follow-up: history must contain the
    # ACTIVE variant text exactly once and the inactive one never.
    client.post(f"/api/v1/chats/{chat_id}/messages/{v0}/activate")

    resp = client.post(f"/api/v1/chats/{chat_id}/complete", json={
        "message": "follow-up", "model_id": "test/model-1",
    })
    assert resp.status_code == 200
    call = provider.calls[-1]
    contents = [m["content"] for m in call["messages"]]
    assert contents.count("fake assistant reply") == 1   # active v0
    assert all("variant one" not in c for c in contents)  # inactive v1


def test_message_count_counts_active_rows_only(client, provider):
    chat_id, v0 = _seed_exchange(client, provider)
    _regen(client, chat_id, v0, "variant one", provider)
    _regen_ids = [m["id"] for m in get_messages(client, chat_id)]
    assert len(_regen_ids) == 4  # first_mes, user, v0 (inactive), v1 (active)

    chats = client.get("/api/v1/chats").json()
    chat = next(c for c in chats if c["id"] == chat_id)
    assert chat["message_count"] == 3  # active rows only


# ---------------------------------------------------------------------------
# Delete expands to the group anchor
# ---------------------------------------------------------------------------

def test_delete_any_variant_removes_the_whole_group(client, provider):
    chat_id, v0 = _seed_exchange(client, provider)
    d1 = _regen(client, chat_id, v0, "variant one", provider)
    v1 = d1["assistant_message"]["id"]

    # Delete via the NEWER row id - the sweep must start at the anchor (v0),
    # not at v1, or v0 would survive as an invisible inactive orphan.
    resp = client.delete(f"/api/v1/chats/{chat_id}/messages/{v1}")
    assert resp.status_code == 200
    assert resp.json()["deleted_count"] == 2

    msgs = get_messages(client, chat_id)
    ids = [m["id"] for m in msgs]
    assert v0 not in ids and v1 not in ids
    # first_mes + user remain
    assert [m["role"] for m in msgs] == ["assistant", "user"]


# ---------------------------------------------------------------------------
# Streaming contract
# ---------------------------------------------------------------------------

def test_stream_regenerate_done_has_variant_fields(client, provider, monkeypatch):
    import routers.completions as completions_router

    chat_id, v0 = _seed_exchange(client, provider)

    def fake_stream(messages, model_id, gen_params, provider_dict):
        async def gen():
            yield "streamed "
            yield "variant"
        return gen()

    monkeypatch.setattr(completions_router, "complete_stream", fake_stream)

    with client.stream(
        "POST", f"/api/v1/chats/{chat_id}/messages/{v0}/regenerate/stream",
        json={"model_id": "test/model-1"},
    ) as resp:
        events = [
            _json.loads(line[len("data:"):].strip())
            for line in resp.iter_lines() if line.strip().startswith("data:")
        ]

    done = events[-1]
    assert done["type"] == "done"
    asst = done["assistant_message"]
    assert asst["content"] == "streamed variant"
    assert asst["variant_group"] == v0
    assert asst["active"] is True
    assert asst["variant_index"] == 1
    assert asst["variant_count"] == 2
    assert done["deactivated_message_id"] == v0

    # Old variant is still in the DB, inactive.
    msgs = get_messages(client, chat_id)
    old = next(m for m in msgs if m["id"] == v0)
    assert old["active"] is False
