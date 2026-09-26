"""No log line names a model id, and a model id holding the API key is refused.

Since 26 Sep 2026: a model id is a name a person reads, and elysium.log is
plaintext outside the vault, so no route and no sender logs it. W1 round 2 (M1) found the four route lines in routers/completions.py
logging the raw id before the provider client could withhold anything; a key
typed into the model id went straight into the log from there.

Each check drives the real route with caplog:
  - ground control: an ordinary, distinctive model id goes through, the
    route's own request line IS in the log (positive control that caplog
    sees this logger), and the id is in no record;
  - a model id holding the stored API key is refused with
    `model_id_carries_api_key` before anything is stored or sent, and the key
    is in no record;
  - a model id holding the safeword is NOT refused (a model id is a protocol
    value: "exit" in `vendor/exit-model`, test_safeword.py) and, like every
    model id, is in no record.
"""
from __future__ import annotations

import logging

import pytest

import config
import database
import notebook_store
import secrets_service
from conftest import get_messages, make_character, make_chat

#: Distinctive enough that a hit in a log line can only be this id.
MODEL = "vendorq/canary-model-7x"
#: Long enough for the key matcher, mixed case so a case-sensitive match fails.
KEY = "sk-or-v1-0123456789AbCdEf0123456789abcdefXYZ"
SAFEWORD = "bluebird"

#: The line each route writes when it starts a request (the positive control).
ROUTE_LINES = {
    "complete": "Completion request: chat_id=",
    "complete/stream": "Streaming completion start: chat_id=",
    "regenerate": "Completion request: chat_id=",
    "regenerate/stream": "Streaming regenerate start: chat_id=",
    "edit": "Completion request: chat_id=",
    "edit/stream": "Streaming edit start: chat_id=",
}


@pytest.fixture()
def stream_calls(monkeypatch):
    import routers.completions as cr
    calls = []

    def fake_stream(messages, model_id, gen_params, provider, **kwargs):
        calls.append(model_id)

        async def gen():
            yield "streamed reply"
        return gen()

    monkeypatch.setattr(cr, "complete_stream", fake_stream)
    return calls


def _exchange(chat_id: int) -> tuple[int, int]:
    with database.get_db() as con:
        uid = con.execute("INSERT INTO messages (chat_id, role, content) "
                          "VALUES (?, 'user', 'a question')", (chat_id,)).lastrowid
        aid = con.execute("INSERT INTO messages (chat_id, role, content) "
                          "VALUES (?, 'assistant', 'old reply')", (chat_id,)).lastrowid
    return uid, aid


def _post(client, route: str, model_id: str):
    chat = make_chat(client, make_character(client))
    uid, aid = _exchange(chat)
    before = get_messages(client, chat)
    kind, _, stream = route.partition("/")
    suffix = "/stream" if stream else ""
    if kind == "complete":
        path, body = f"/api/v1/chats/{chat}/complete{suffix}", {"message": "hello"}
    elif kind == "regenerate":
        path, body = f"/api/v1/chats/{chat}/messages/{aid}/regenerate{suffix}", {}
    else:
        path, body = (f"/api/v1/chats/{chat}/messages/{uid}/edit{suffix}",
                      {"message": "a better question"})
    resp = client.post(path, json={"model_id": model_id, **body})
    return resp, chat, before


def _log_text(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records]


@pytest.mark.parametrize("route", list(ROUTE_LINES))
def test_ground_an_ordinary_model_id_goes_and_no_line_names_it(
        client, provider, stream_calls, caplog, route):
    with caplog.at_level(logging.DEBUG):
        resp, _chat, _before = _post(client, route, MODEL)
    assert resp.status_code == 200, resp.text
    # The id did reach the provider: what is kept out of the log is not kept
    # out of the request.
    sent = [c["model_id"] for c in provider.calls] + stream_calls
    assert sent == [MODEL]
    lines = _log_text(caplog)
    assert any(ROUTE_LINES[route] in m for m in lines), (
        "positive control: the route's request line should be in the log")
    assert not any(MODEL in m for m in lines)
    assert not any("canary-model" in m for m in lines)


@pytest.mark.parametrize("route", list(ROUTE_LINES))
def test_a_model_id_holding_the_key_is_refused_and_never_logged(
        client, provider, stream_calls, caplog, route):
    secrets_service.set_secret(config.SECRET_API_KEY, KEY)
    with caplog.at_level(logging.DEBUG):
        resp, chat, before = _post(client, route, f"vendor/{KEY.lower()}")
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "model_id_carries_api_key"
    assert provider.calls == [] and stream_calls == []
    assert get_messages(client, chat) == before
    assert KEY.lower() not in " ".join(_log_text(caplog)).lower()
    assert KEY.lower() not in resp.text.lower()


@pytest.mark.parametrize("route", list(ROUTE_LINES))
def test_a_model_id_holding_the_safeword_goes_and_is_never_logged(
        client, provider, stream_calls, caplog, route):
    notebook_store.set_safeword(SAFEWORD)
    model_id = f"vendor/{SAFEWORD}-model"
    try:
        with caplog.at_level(logging.DEBUG):
            resp, _chat, _before = _post(client, route, model_id)
    finally:
        notebook_store.set_safeword("")
    assert resp.status_code == 200, resp.text
    sent = [c["model_id"] for c in provider.calls] + stream_calls
    assert sent == [model_id]
    lines = _log_text(caplog)
    assert any(ROUTE_LINES[route] in m for m in lines)
    assert not any(SAFEWORD in m.lower() for m in lines)


def test_ground_the_key_check_passes_an_id_that_only_resembles_it(
        client, provider, stream_calls):
    """The key's prefix alone is not the key; the check must not refuse it."""
    secrets_service.set_secret(config.SECRET_API_KEY, KEY)
    resp, _chat, _before = _post(client, "complete", "sk-or-v1/some-model")
    assert resp.status_code == 200, resp.text
    assert [c["model_id"] for c in provider.calls] == ["sk-or-v1/some-model"]
