"""Client-disconnect / abort tests for the streaming completion generator.

These drive the StreamingResponse body_iterator directly so we can throw
GeneratorExit at a precise suspension point - something TestClient can't do -
covering the two disconnect paths the audit flagged as untested:

- disconnect exactly at the `done` yield must NOT double-insert the assistant
  message (regression for the persisted-guard fix),
- disconnect mid-stream with a partial must persist that partial exactly once.
"""

import asyncio
import json

from routers.completions import complete_chat_stream, CompleteRequest

from conftest import make_character, make_chat, get_messages


def _run(coro):
    return asyncio.run(coro)


def _install_stream(monkeypatch, deltas):
    import routers.completions as cr

    def fake_stream(messages, model_id, gen_params, provider):
        async def gen():
            try:
                for d in deltas:
                    yield d
            except GeneratorExit:
                return  # graceful close when the outer generator is aborted
        return gen()

    monkeypatch.setattr(cr, "complete_stream", fake_stream)


async def _drive(chat_id, message, model_id, stop_after_type):
    """Drive the generator until an event of stop_after_type, then aclose()."""
    body = CompleteRequest(message=message, model_id=model_id)
    resp = await complete_chat_stream(chat_id, body)
    agen = resp.body_iterator
    seen = []
    async for chunk in agen:
        data = chunk[len("data: "):].strip()
        evt = json.loads(data)
        seen.append(evt)
        if evt["type"] == stop_after_type:
            break
    # Simulate the client dropping the connection at this suspension point.
    await agen.aclose()
    return seen


def test_disconnect_at_done_does_not_double_insert(client, monkeypatch):
    _install_stream(monkeypatch, ["Hello ", "world."])
    char_id = make_character(client)
    chat_id = make_chat(client, char_id)

    events = _run(_drive(chat_id, "hi", "test/model-1", stop_after_type="done"))
    assert events[-1]["type"] == "done"

    # Exactly one assistant turn - the abort handler must not re-insert.
    msgs = get_messages(client, chat_id)
    assistant = [m for m in msgs if m["role"] == "assistant"]
    # first_mes + the one completion assistant = 2; never 3.
    assert len(assistant) == 2
    assert assistant[-1]["content"] == "Hello world."


def test_disconnect_midstream_persists_partial_once(client, monkeypatch):
    _install_stream(monkeypatch, ["Partial ", "answer ", "here."])
    char_id = make_character(client)
    chat_id = make_chat(client, char_id)

    # Stop right after the first delta → a non-empty partial, no done.
    events = _run(_drive(chat_id, "hi", "test/model-1", stop_after_type="delta"))
    assert events[0]["type"] == "user_message"
    assert events[-1]["type"] == "delta"

    msgs = get_messages(client, chat_id)
    # user message kept, partial persisted exactly once as assistant.
    assert [m["role"] for m in msgs] == ["assistant", "user", "assistant"]
    assert msgs[-1]["content"] == "Partial "  # only the delta streamed before abort


def test_disconnect_before_any_delta_rolls_back_user(client, monkeypatch):
    _install_stream(monkeypatch, ["late"])  # we abort before consuming it
    char_id = make_character(client)
    chat_id = make_chat(client, char_id)

    # Stop at user_message (before any delta) → empty partial → user removed.
    events = _run(_drive(chat_id, "hi", "test/model-1", stop_after_type="user_message"))
    assert events[-1]["type"] == "user_message"

    msgs = get_messages(client, chat_id)
    assert [m["role"] for m in msgs] == ["assistant"]  # only first_mes remains
