"""A prompt module that is off never reaches the provider, on any request path.

The whole app runs here: a SillyTavern preset goes in through the import
route, modules are switched with the same PATCH and preset calls the settings
page makes, and every chat route that builds a request (send, regenerate and
edit, each plain and streamed) runs against a recorder standing in for the
provider. The recorder keeps the exact `messages` list the provider would
have been sent, and the checks read that list.

Ground control: with every module off, no module text is in any request.
Positive control: a module switched on IS found in every request, so the
detector is not blind; switched off again, it is gone on the very next call.
"""
import json
from urllib.parse import quote

import pytest

from prompt_modules.limits import FILENAME_HEADER
from conftest import get_messages, make_character, make_chat

BASE = "/api/v1/prompt-modules"
MODEL = "test/model-1"

# Plain sentences, no macros, so the text the request carries is the text
# written here and a substring test finds it.
TEXT = {
    "alpha": "Alpha rule: describe the weather in every reply.",
    "bravo": "Bravo rule: end each reply with a question.",
    "charlie": "Charlie rule: keep replies under five sentences.",
}
IMPERSONATION = "Impersonation field that this app never sends anywhere."
PRESET = {
    "impersonation_prompt": IMPERSONATION,
    "prompts": [
        {"identifier": key, "name": key.title(), "content": text, "role": "system"}
        for key, text in TEXT.items()
    ] + [{"identifier": "chatHistory", "name": "Chat History", "marker": True}],
    # What SillyTavern itself had on: alpha and charlie, not bravo.
    "prompt_order": [{"character_id": 100001, "order": [
        {"identifier": "alpha", "enabled": True},
        {"identifier": "bravo", "enabled": False},
        {"identifier": "chatHistory", "enabled": True},
        {"identifier": "charlie", "enabled": True},
    ]}],
}


@pytest.fixture()
def sent(monkeypatch):
    """Every provider call's messages, in order."""
    import routers.completions as cr
    calls: list[list[dict]] = []

    async def fake_complete(messages, model_id, gen_params, provider, **kwargs):
        calls.append(json.loads(json.dumps(messages, default=str)))
        return {"choices": [{"message": {"content": "plain reply"}}]}

    def fake_stream(messages, model_id, gen_params, provider, **kwargs):
        calls.append(json.loads(json.dumps(messages, default=str)))

        async def gen():
            yield "streamed "
            yield "reply"
        return gen()

    monkeypatch.setattr(cr, "complete", fake_complete)
    monkeypatch.setattr(cr, "complete_stream", fake_stream)
    return calls


def _blob(messages: list[dict]) -> str:
    out = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            out.append(c)
        elif isinstance(c, list):
            out += [p.get("text", "") for p in c if isinstance(p, dict)]
    return "\n".join(out)


def _present(messages: list[dict]) -> list[str]:
    blob = _blob(messages)
    return sorted(key for key, text in TEXT.items() if text in blob)


def _import(client) -> tuple[int, dict[str, int]]:
    r = client.post(
        f"{BASE}/import",
        content=json.dumps(PRESET).encode("utf-8"),
        headers={FILENAME_HEADER: quote("preset.json"),
                 "Content-Type": "application/octet-stream"},
    )
    assert r.status_code == 201, r.text
    ids = {m["st_identifier"]: m["id"] for m in r.json()["modules"]
           if m["kind"] == "module"}
    assert set(ids) == set(TEXT)
    return r.json()["import_id"], ids


def _last(client, chat_id: int, role: str) -> int:
    return [m for m in get_messages(client, chat_id) if m["role"] == role][-1]["id"]


PATHS = ("send", "send_stream", "regenerate", "regenerate_stream",
         "edit", "edit_stream")


def _every_path(client, chat_id: int, sent: list) -> dict[str, list[str]]:
    """One request down each path; the modules each one carried."""
    chat = f"/api/v1/chats/{chat_id}"
    steps = {
        "send": lambda: client.post(
            f"{chat}/complete", json={"message": "hello", "model_id": MODEL}),
        "send_stream": lambda: client.post(
            f"{chat}/complete/stream", json={"message": "hello", "model_id": MODEL}),
        "regenerate": lambda: client.post(
            f"{chat}/messages/{_last(client, chat_id, 'assistant')}/regenerate",
            json={"model_id": MODEL}),
        "regenerate_stream": lambda: client.post(
            f"{chat}/messages/{_last(client, chat_id, 'assistant')}/regenerate/stream",
            json={"model_id": MODEL}),
        "edit": lambda: client.post(
            f"{chat}/messages/{_last(client, chat_id, 'user')}/edit",
            json={"model_id": MODEL, "message": "edited"}),
        "edit_stream": lambda: client.post(
            f"{chat}/messages/{_last(client, chat_id, 'user')}/edit/stream",
            json={"model_id": MODEL, "message": "edited again"}),
    }
    assert tuple(steps) == PATHS
    out = {}
    for name, step in steps.items():
        before = len(sent)
        r = step()
        assert r.status_code == 200, (name, r.text)
        # The path really reached the provider; a path that silently skipped
        # it would make every absence below trivially true.
        assert len(sent) == before + 1, name
        out[name] = _present(sent[-1])
        assert IMPERSONATION not in _blob(sent[-1]), name
    return out


def _patch(client, module_id: int, enabled: bool) -> None:
    r = client.patch(f"{BASE}/{module_id}", json={"enabled": enabled})
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is enabled


def _all(result: dict[str, list[str]], expected: list[str]) -> bool:
    return set(result) == set(PATHS) and all(v == expected for v in result.values())


def test_ground_every_module_off_sends_none(client, sent):
    _import(client)
    chat_id = make_chat(client, make_character(client))
    result = _every_path(client, chat_id, sent)
    assert _all(result, []), result


def test_a_module_turned_off_is_gone_from_the_next_request(client, sent):
    _, ids = _import(client)
    chat_id = make_chat(client, make_character(client))

    # POSITIVE: bravo on (SillyTavern had it off), and only bravo is sent.
    _patch(client, ids["bravo"], True)
    result = _every_path(client, chat_id, sent)
    assert _all(result, ["bravo"]), result

    # Off again: gone on every path.
    _patch(client, ids["bravo"], False)
    result = _every_path(client, chat_id, sent)
    assert _all(result, []), result


def test_on_off_on_off_in_a_row_ends_off(client, sent):
    """Four writes back to back, as a fast double double-click sends them."""
    _, ids = _import(client)
    chat_id = make_chat(client, make_character(client))
    for enabled in (True, False, True, False):
        _patch(client, ids["alpha"], enabled)
    result = _every_path(client, chat_id, sent)
    assert _all(result, []), result
    # POSITIVE: one more on, and it is there.
    _patch(client, ids["alpha"], True)
    result = _every_path(client, chat_id, sent)
    assert _all(result, ["alpha"]), result


def test_preset_defaults_send_what_sillytavern_had_on_and_never_the_rest(client, sent):
    import_id, ids = _import(client)
    chat_id = make_chat(client, make_character(client))
    r = client.post(f"{BASE}/enable-preset-defaults", json={"import_id": import_id})
    assert r.status_code == 200, r.text
    assert sorted(r.json()["enabled_ids"]) == sorted([ids["alpha"], ids["charlie"]])
    result = _every_path(client, chat_id, sent)
    assert _all(result, ["alpha", "charlie"]), result

    # One of the set off again: only the other stays.
    _patch(client, ids["alpha"], False)
    result = _every_path(client, chat_id, sent)
    assert _all(result, ["charlie"]), result
