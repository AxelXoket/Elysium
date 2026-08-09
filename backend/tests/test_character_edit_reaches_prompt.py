"""Does editing a character change what the next reply is told?

REPORTED FROM USE: "I change something in the character details while a chat is
open, and that chat keeps the old information - the current details do not go
out as the prompt, the old ones stay."

Every layer reads correct on inspection: PATCH persists all nine fields and
accepts "" so a cleared field really is cleared, get_db() opens a fresh
connection per call so there is no stale WAL snapshot, and all four completion
entry points rebuild the system block from a fresh SELECT. Which is exactly the
situation where inspection stops being evidence. These assert the end-to-end
fact instead: what the provider is actually handed, before and after an edit.
"""

import json

import pytest

from conftest import make_character, make_chat
from test_streaming import stream_provider  # noqa: F401


BODY = {"message": "Say something", "model_id": "test/model-1"}


def _system_text(fake) -> str:
    """The system-role content of the LAST captured provider call."""
    messages = fake.calls[-1]["messages"]
    return "\n".join(m["content"] for m in messages if m["role"] == "system")


def _speak(client, chat_id: int) -> None:
    resp = client.post(f"/api/v1/chats/{chat_id}/complete/stream", json=BODY)
    assert resp.status_code == 200, resp.text
    resp.read()          # drain, so the exchange completes


def _patch(client, char_id: int, **fields) -> None:
    resp = client.patch(f"/api/v1/characters/{char_id}", json=fields)
    assert resp.status_code == 200, resp.text


class TestAnEditReachesTheVeryNextReply:
    """No restart, no new chat, no reload - the next message carries it."""

    def test_a_changed_field_replaces_the_old_value(
        self, client, stream_provider,  # noqa: F811
    ):
        char_id = make_character(client)
        chat_id = make_chat(client, char_id)

        _speak(client, chat_id)
        assert "A test character" in _system_text(stream_provider)

        _patch(client, char_id, description="Rewritten while the chat was open")
        _speak(client, chat_id)

        text = _system_text(stream_provider)
        assert "Rewritten while the chat was open" in text
        assert "A test character" not in text, (
            "the old description survived into the prompt - the reported bug"
        )

    def test_an_added_field_appears(self, client, stream_provider):  # noqa: F811
        # `scenario` starts empty on a character made through the factory, so
        # this is the "I added something" half of the report.
        char_id = make_character(client)
        chat_id = make_chat(client, char_id)
        _speak(client, chat_id)
        assert "moonlit rooftop" not in _system_text(stream_provider)

        _patch(client, char_id, scenario="A moonlit rooftop, long after midnight")
        _speak(client, chat_id)

        assert "moonlit rooftop" in _system_text(stream_provider)

    def test_a_cleared_field_stops_being_sent(
        self, client, stream_provider,  # noqa: F811
    ):
        # The "I removed something" half, and the one most likely to break:
        # an empty string is falsy in three languages between the textarea and
        # the SQL, and anything that treats it as "no change given" leaves the
        # deleted text in the prompt forever.
        char_id = make_character(client)
        chat_id = make_chat(client, char_id)
        _patch(client, char_id, personality="Brittle, sardonic")
        _speak(client, chat_id)
        assert "sardonic" in _system_text(stream_provider)

        _patch(client, char_id, personality="")
        _speak(client, chat_id)

        assert "sardonic" not in _system_text(stream_provider), (
            "a personality the user deleted was still being sent"
        )

    def test_the_trailing_instruction_follows_the_edit_too(
        self, client, stream_provider,  # noqa: F811
    ):
        # post_history_instruction is assembled separately from the system
        # block (it goes AFTER the history), so it is its own path and its own
        # chance to be stale.
        char_id = make_character(client)
        chat_id = make_chat(client, char_id)
        _patch(client, char_id, post_history_instruction="Stay in scene.")
        _speak(client, chat_id)
        assert "Stay in scene." in json.dumps(stream_provider.calls[-1]["messages"])

        _patch(client, char_id, post_history_instruction="Break the fourth wall.")
        _speak(client, chat_id)

        blob = json.dumps(stream_provider.calls[-1]["messages"])
        assert "Break the fourth wall." in blob
        assert "Stay in scene." not in blob


class TestEveryWayOfProducingAReply:
    """The four entry points each assemble their own payload. A fix that lands
    on the streaming path only would leave the button next to it stale."""

    def test_the_non_streaming_path_sees_the_edit(self, client, monkeypatch):
        import routers.completions as completions_router

        seen: list[list[dict]] = []

        async def fake_complete(messages, model_id, gen_params, provider, **kwargs):
            seen.append(messages)
            return {"choices": [{"message": {"content": "ok"}}]}

        monkeypatch.setattr(completions_router, "complete", fake_complete)

        char_id = make_character(client)
        chat_id = make_chat(client, char_id)
        _patch(client, char_id, description="Second draft")
        resp = client.post(f"/api/v1/chats/{chat_id}/complete", json=BODY)
        assert resp.status_code == 200, resp.text

        text = "\n".join(
            m["content"] for m in seen[-1] if m["role"] == "system"
        )
        assert "Second draft" in text
        assert "A test character" not in text

    def test_regenerating_sees_the_edit(
        self, client, stream_provider,  # noqa: F811
    ):
        # Regenerate is where a stale prompt would be least visible and most
        # annoying: the user edits the character precisely BECAUSE they did not
        # like the reply, then asks for another take.
        char_id = make_character(client)
        chat_id = make_chat(client, char_id)
        _speak(client, chat_id)

        messages = client.get(f"/api/v1/chats/{chat_id}/messages").json()
        target = [m for m in messages if m["role"] == "assistant"][-1]["id"]

        _patch(client, char_id, description="Third draft")
        resp = client.post(
            f"/api/v1/chats/{chat_id}/messages/{target}/regenerate/stream",
            json={"model_id": "test/model-1"},
        )
        assert resp.status_code == 200, resp.text
        resp.read()

        text = _system_text(stream_provider)
        assert "Third draft" in text
        assert "A test character" not in text
