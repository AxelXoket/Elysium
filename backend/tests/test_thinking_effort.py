"""The thinking choice per model: what reaches the provider, and where it is kept.

Every request test here goes through the real pieces: a /models reply served
by an httpx MockTransport and normalised and cached by the app, the chat
route, and (for the wire tests) the real provider client, so the JSON read
back is the JSON that would leave the machine.

The six catalogue shapes, A to F:
  A  no `reasoning` in supported_parameters
  B  `reasoning` listed, no reasoning object
  C  optional, levels listed
  D  optional, no levels
  E  mandatory, levels listed
  F  mandatory, no levels

Ground control for "the default is today's request": a body with no
`thinking_effort` gets the reasoning field the app sent before the choice
existed, pinned below as literal dicts and also by the older tests in
test_openrouter_request_params.py and test_thinking_switch.py, which are
unchanged.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

import openrouter
from openrouter import (
    REASONING_EFFORT_ORDER,
    THINKING_CHOICES,
    THINKING_DEFAULT,
    THINKING_OFF,
    UNTOUCHED_MANDATORY_ORDER,
    listed_efforts,
    reasoning_param,
)
from tests.conftest import make_character, make_chat

ALL_LEVELS = ["none", *REASONING_EFFORT_ORDER]


def _raw(model_id: str, *, params=("temperature", "max_tokens"), reasoning=None):
    row = {"id": model_id, "name": model_id, "context_length": 32000,
           "top_provider": {"max_completion_tokens": 8000},
           "supported_parameters": list(params)}
    if reasoning is not None:
        row["reasoning"] = reasoning
    return row


R = ("temperature", "max_tokens", "reasoning")
CATALOGUE = [
    _raw("a/plain"),
    _raw("b/unknown", params=R),
    # Listed out of order and with every word the catalogue uses, so the
    # order and the "none" rule are tested, not the listing.
    _raw("c/optional", params=R, reasoning={
        "mandatory": False, "supported_efforts": list(reversed(ALL_LEVELS)),
        "default_effort": "medium", "default_enabled": False}),
    _raw("c/two", params=R, reasoning={
        "mandatory": False, "supported_efforts": ["high", "low"]}),
    _raw("d/optional", params=R, reasoning={
        "mandatory": False, "supports_max_tokens": True}),
    _raw("e/mandatory", params=R, reasoning={
        "mandatory": True, "supported_efforts": ["max", "high", "low"],
        "default_effort": "high"}),
    _raw("f/mandatory", params=R, reasoning={"mandatory": True}),
    # Two shapes the untouched request must keep exactly: a mandatory model
    # listing only "max", and a listing that is not a list at all.
    _raw("g/maxonly", params=R, reasoning={
        "mandatory": True, "supported_efforts": ["max"]}),
    _raw("h/stringlist", params=R, reasoning={
        "mandatory": True, "supported_efforts": "high,low"}),
    _raw("h/optstring", params=R, reasoning={
        "mandatory": False, "supported_efforts": "high"}),
    _raw("h/numberlist", params=R, reasoning={
        "mandatory": True, "supported_efforts": 3}),
]


@pytest.fixture
def anyio_backend():
    return "asyncio"


class _Wire:
    """Serves the model list and records every chat body, as bytes."""

    def __init__(self):
        self.bodies: list[bytes] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and "/models" in request.url.path:
            return httpx.Response(200, json={"data": CATALOGUE})
        self.bodies.append(request.content)
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "stop", "message": {"content": "ok"}}]})

    def reasoning(self, index: int = -1):
        return json.loads(self.bodies[index]).get("reasoning", "ABSENT")


@pytest.fixture
def wire(monkeypatch):
    w = _Wire()
    client = httpx.AsyncClient(transport=httpx.MockTransport(w))
    monkeypatch.setattr(openrouter, "get_client", lambda: client)
    return w


@pytest.fixture
async def cached(wire, db):
    await openrouter.fetch_models(refresh=True)
    return wire


# ---------------------------------------------------------------------------
# 1. The constants, held to the file the frontend mirror is held to
# ---------------------------------------------------------------------------

def test_the_shared_contract_is_the_constants():
    path = Path(__file__).resolve().parents[2] / "shared" / "thinking_effort_contract.json"
    contract = json.loads(path.read_text(encoding="utf-8"))
    assert tuple(contract["effort_order"]) == REASONING_EFFORT_ORDER
    assert tuple(contract["choices"]) == THINKING_CHOICES
    assert contract["off"] == THINKING_OFF
    assert contract["default"] == THINKING_DEFAULT
    assert tuple(contract["untouched_mandatory_order"]) == UNTOUCHED_MANDATORY_ORDER
    # max is a level and the highest one; none never is.
    assert REASONING_EFFORT_ORDER[-1] == "max"
    assert "none" not in THINKING_CHOICES


def test_listed_levels_come_in_the_constant_order_without_none():
    info = CATALOGUE[2]["reasoning"]
    assert listed_efforts(info) == list(REASONING_EFFORT_ORDER)
    assert listed_efforts({"supported_efforts": ["none"]}) == []
    # Ground: an empty or missing list is an empty answer, never a guess.
    assert listed_efforts({}) == []
    assert listed_efforts(None) == []
    assert listed_efforts({"supported_efforts": "high"}) == []


# ---------------------------------------------------------------------------
# 2. reasoning_param, case by case
# ---------------------------------------------------------------------------

#: What each model got before the choice existed. The default rows below
#: are compared to this, so "no choice" is proven to be today's request.
TODAY = {
    "a/plain": None,
    "b/unknown": None,
    "c/optional": {"enabled": False},
    "c/two": {"enabled": False},
    "d/optional": {"enabled": False},
    "e/mandatory": {"exclude": True, "effort": "low"},
    "f/mandatory": {"exclude": True},
    # "max" was not a level the old pick knew, so no level went out.
    "g/maxonly": {"exclude": True},
    # The old pick used a plain `in`, which matches inside a string.
    "h/stringlist": {"exclude": True, "effort": "low"},
    "h/optstring": {"enabled": False},
    "h/numberlist": {"exclude": True},
}


class TestTheDefaultIsToday:
    @pytest.mark.anyio
    @pytest.mark.parametrize("model_id", list(TODAY))
    async def test_no_choice_is_the_old_request(self, cached, model_id):
        assert reasoning_param(model_id) == TODAY[model_id]
        assert reasoning_param(model_id, effort=None) == TODAY[model_id]

    @pytest.mark.anyio
    @pytest.mark.parametrize("model_id", list(TODAY))
    async def test_an_unknown_word_is_the_old_request(self, cached, model_id):
        for word in ("none", "HIGH", "turbo", ""):
            assert reasoning_param(model_id, effort=word) == TODAY[model_id], word


class TestCaseByCase:
    @pytest.mark.anyio
    async def test_a_and_b_get_nothing_whatever_is_chosen(self, cached):
        for choice in THINKING_CHOICES:
            assert reasoning_param("a/plain", effort=choice) is None
            assert reasoning_param("b/unknown", effort=choice) is None

    @pytest.mark.anyio
    async def test_c_every_listed_level_is_sent_with_exclude(self, cached):
        for level in REASONING_EFFORT_ORDER:
            assert reasoning_param("c/optional", effort=level) == {
                "exclude": True, "effort": level}

    @pytest.mark.anyio
    async def test_c_off_and_default(self, cached):
        assert reasoning_param("c/optional", effort=THINKING_OFF) == {"enabled": False}
        assert reasoning_param("c/optional", effort=THINKING_DEFAULT) is None

    @pytest.mark.anyio
    async def test_c_a_level_the_model_does_not_list_is_the_old_request(self, cached):
        # Positive control first: a listed level goes out.
        assert reasoning_param("c/two", effort="high") == {"exclude": True, "effort": "high"}
        for level in ("minimal", "medium", "xhigh", "max"):
            assert reasoning_param("c/two", effort=level) == {"enabled": False}, level

    @pytest.mark.anyio
    async def test_d_off_default_and_no_level(self, cached):
        assert reasoning_param("d/optional", effort=THINKING_OFF) == {"enabled": False}
        assert reasoning_param("d/optional", effort=THINKING_DEFAULT) is None
        for level in REASONING_EFFORT_ORDER:
            assert reasoning_param("d/optional", effort=level) == {"enabled": False}

    @pytest.mark.anyio
    async def test_e_listed_levels_including_max(self, cached):
        assert reasoning_param("e/mandatory", effort="max") == {"exclude": True, "effort": "max"}
        assert reasoning_param("e/mandatory", effort="high") == {"exclude": True, "effort": "high"}
        assert reasoning_param("e/mandatory", effort="low") == {"exclude": True, "effort": "low"}

    @pytest.mark.anyio
    async def test_e_is_never_disabled(self, cached):
        # "off" and a level the model does not list give the lowest listed.
        for choice in (THINKING_OFF, "minimal", "medium", "xhigh"):
            out = reasoning_param("e/mandatory", effort=choice)
            assert out == {"exclude": True, "effort": "low"}, choice
            assert "enabled" not in out

    @pytest.mark.anyio
    async def test_e_and_f_provider_default_sends_exclude_and_no_level(self, cached):
        # The thinking text is never shown, so it stays off the wire.
        for model_id in ("e/mandatory", "f/mandatory", "g/maxonly"):
            assert reasoning_param(model_id, effort=THINKING_DEFAULT) == {
                "exclude": True}, model_id

    @pytest.mark.anyio
    async def test_a_max_only_model_sends_max_once_it_is_picked(self, cached):
        # Positive control for the untouched row above: picking it sends it.
        assert reasoning_param("g/maxonly", effort="max") == {
            "exclude": True, "effort": "max"}

    @pytest.mark.anyio
    async def test_a_listing_that_is_not_a_list_offers_no_level(self, cached):
        for model_id in ("h/stringlist", "h/optstring", "h/numberlist"):
            for level in REASONING_EFFORT_ORDER:
                assert reasoning_param(model_id, effort=level) == TODAY[model_id], (
                    model_id, level)

    @pytest.mark.anyio
    async def test_f_every_other_choice_is_the_old_request(self, cached):
        for choice in (THINKING_OFF, *REASONING_EFFORT_ORDER):
            assert reasoning_param("f/mandatory", effort=choice) == {"exclude": True}

    @pytest.mark.anyio
    async def test_no_request_ever_carries_effort_none_or_a_budget(self, cached):
        for row in CATALOGUE:
            for choice in (*THINKING_CHOICES, None, "none"):
                out = reasoning_param(row["id"], effort=choice) or {}
                assert out.get("effort") != "none"
                assert "max_tokens" not in out

    def test_no_cached_list_means_nothing_is_sent(self):
        for choice in THINKING_CHOICES:
            assert reasoning_param("c/optional", effort=choice) is None


# ---------------------------------------------------------------------------
# 3. On the wire, through the chat route and the real provider client
# ---------------------------------------------------------------------------

def _send(client, chat_id: int, model_id: str, **gen) -> None:
    resp = client.post(f"/api/v1/chats/{chat_id}/complete", json={
        "message": "hi", "model_id": model_id,
        "generation_params": {"max_tokens": 100, **gen}})
    assert resp.status_code == 200, resp.text


@pytest.fixture
def chat_wire(client, wire):
    import anyio
    anyio.run(openrouter.fetch_models, True)
    return make_chat(client, make_character(client)), wire


class TestOnTheWire:
    def test_ground_no_field_is_byte_for_byte_the_old_body(self, client, chat_wire):
        _, wire = chat_wire

        def settings_bytes(raw: bytes) -> bytes:
            # Everything but the conversation, in the order it was sent.
            body = json.loads(raw)
            body.pop("messages")
            return json.dumps(body).encode()

        for model_id, expected in TODAY.items():
            character = make_character(client)
            _send(client, make_chat(client, character), model_id)
            without = settings_bytes(wire.bodies[-1])
            _send(client, make_chat(client, character), model_id,
                  thinking_effort=None, thinking=False)
            assert settings_bytes(wire.bodies[-1]) == without, model_id
            got = wire.reasoning()
            assert got == ("ABSENT" if expected is None else expected), model_id

    def test_the_old_switch_still_means_what_it_meant(self, client, chat_wire):
        chat_id, wire = chat_wire
        _send(client, chat_id, "c/optional", thinking=True)
        assert wire.reasoning() == "ABSENT"
        # A mandatory model never followed the switch: still the lowest level.
        _send(client, chat_id, "e/mandatory", thinking=True)
        assert wire.reasoning() == {"exclude": True, "effort": "low"}

    def test_a_chosen_level_leaves_as_reasoning_effort(self, client, chat_wire):
        chat_id, wire = chat_wire
        _send(client, chat_id, "c/optional", thinking_effort="max")
        body = json.loads(wire.bodies[-1])
        assert body["reasoning"] == {"exclude": True, "effort": "max"}
        assert "thinking_effort" not in body and "thinking" not in body
        assert body["max_tokens"] == 100  # positive control: the rest went

    def test_the_choice_beats_the_old_switch(self, client, chat_wire):
        chat_id, wire = chat_wire
        _send(client, chat_id, "c/optional", thinking=True, thinking_effort="off")
        assert wire.reasoning() == {"enabled": False}

    def test_provider_default_on_a_mandatory_model(self, client, chat_wire):
        chat_id, wire = chat_wire
        _send(client, chat_id, "e/mandatory", thinking_effort="default")
        assert wire.reasoning() == {"exclude": True}

    def test_the_field_is_strict(self, client, chat_wire):
        chat_id, wire = chat_wire
        for bad in (True, 3, "x" * 17, ["high"]):
            resp = client.post(f"/api/v1/chats/{chat_id}/complete", json={
                "message": "hi", "model_id": "c/optional",
                "generation_params": {"thinking_effort": bad}})
            assert resp.status_code == 422, bad
        assert wire.bodies == []


# ---------------------------------------------------------------------------
# 4. Private endpoints: a dropped reasoning field is said, not hidden
# ---------------------------------------------------------------------------

def _ep(*params: str) -> dict:
    return {"accepts": frozenset(params)}


def _serve_index(monkeypatch, index):
    async def _index(refresh: bool = False):
        return index
    monkeypatch.setattr(openrouter, "zdr_index", _index)


class TestPrivateEndpoints:
    def _complete(self, client, provider, chat_id, **gen):
        resp = client.post(f"/api/v1/chats/{chat_id}/complete", json={
            "message": "hi", "model_id": "c/optional",
            "generation_params": {"max_tokens": 100, **gen}})
        assert resp.status_code == 200, resp.text
        return resp.json()["notices"], provider.calls[-1]["gen_params"]

    def test_dropped_reasoning_comes_with_a_notice(self, client, provider, chat_wire,
                                                   monkeypatch):
        chat_id, _ = chat_wire
        _serve_index(monkeypatch, {"c/optional": [_ep("max_tokens")]})
        notices, sent = self._complete(client, provider, chat_id,
                                       thinking_effort="high")
        assert "reasoning" not in sent
        assert {"type": "notice", "code": "reasoning_not_sent_privately"} in notices
        # The default (Off) is dropped the same way and said the same way.
        notices, sent = self._complete(client, provider, chat_id)
        assert "reasoning" not in sent
        assert {"type": "notice", "code": "reasoning_not_sent_privately"} in notices

    def test_kept_reasoning_has_no_notice_and_keeps_the_pool(
            self, client, provider, chat_wire, monkeypatch):
        chat_id, _ = chat_wire
        _serve_index(monkeypatch, {"c/optional": [
            _ep("max_tokens", "reasoning"), _ep("max_tokens", "reasoning")]})
        notices, sent = self._complete(client, provider, chat_id,
                                       thinking_effort="high")
        assert sent["reasoning"] == {"exclude": True, "effort": "high"}
        assert not any(n.get("code") == "reasoning_not_sent_privately" for n in notices)
        # Same pool as the default request: a level narrows nothing.
        _, default_sent = self._complete(client, provider, chat_id)
        assert default_sent["reasoning"] == {"enabled": False}


# ---------------------------------------------------------------------------
# 5. Where the choice is kept: the vault, per model
# ---------------------------------------------------------------------------

URL = "/api/v1/settings/thinking-effort"


def _efforts(client) -> dict:
    return client.get("/api/v1/settings").json()["thinking_efforts"]


class TestKeptPerModel:
    def test_ground_a_fresh_vault_has_no_choice(self, client):
        assert _efforts(client) == {}

    def test_set_read_change_and_clear(self, client):
        r = client.post(URL, json={"model_id": "c/optional", "effort": "max"})
        assert r.status_code == 200, r.text
        assert r.json()["thinking_efforts"] == {"c/optional": "max"}
        client.post(URL, json={"model_id": "e/mandatory", "effort": "default"})
        assert _efforts(client) == {"c/optional": "max", "e/mandatory": "default"}
        client.post(URL, json={"model_id": "c/optional", "effort": "off"})
        assert _efforts(client)["c/optional"] == "off"
        client.post(URL, json={"model_id": "c/optional", "effort": None})
        assert _efforts(client) == {"e/mandatory": "default"}

    def test_every_choice_is_accepted_and_nothing_else(self, client):
        for choice in THINKING_CHOICES:
            assert client.post(URL, json={"model_id": "m/x", "effort": choice}).status_code == 200
        for bad in ("none", "HIGH", "", 1, True):
            assert client.post(URL, json={"model_id": "m/x", "effort": bad}).status_code == 422
        assert _efforts(client) == {"m/x": THINKING_CHOICES[-1]}

    def test_a_misspelt_or_missing_field_writes_nothing(self, client):
        client.post(URL, json={"model_id": "m/x", "effort": "high"})
        for body in ({"model": "m/x", "effort": None},
                     {"model_id": "m/x", "effort": None, "extra": 1},
                     {"model_id": "m/x"},
                     {"model_id": "   ", "effort": None},
                     {"model_id": "m" * 201, "effort": None}):
            assert client.post(URL, json=body).status_code == 422, body
        assert _efforts(client) == {"m/x": "high"}

    def test_the_ceiling_drops_the_oldest_set(self, client, monkeypatch):
        import routers.settings as settings_router
        monkeypatch.setattr(settings_router, "THINKING_EFFORTS_MAX_MODELS", 2)
        for mid in ("m/1", "m/2", "m/3"):
            client.post(URL, json={"model_id": mid, "effort": "low"})
        assert list(_efforts(client)) == ["m/2", "m/3"]
        # Setting an entry again makes it the newest.
        client.post(URL, json={"model_id": "m/2", "effort": "high"})
        client.post(URL, json={"model_id": "m/4", "effort": "low"})
        assert _efforts(client) == {"m/2": "high", "m/4": "low"}

    def test_a_corrupted_row_reads_as_no_choice(self, client):
        import config
        import database
        for raw in ("{not json", "[1,2]", '"high"',
                    json.dumps({"m/ok": "high", "m/bad": "turbo", "": "low"})):
            database.set_setting(config.SETTING_THINKING_EFFORTS, raw)
            got = _efforts(client)
            assert got == ({"m/ok": "high"} if "m/ok" in raw else {}), raw
        # And the next write starts from what could be read.
        client.post(URL, json={"model_id": "m/new", "effort": "max"})
        assert _efforts(client) == {"m/ok": "high", "m/new": "max"}

    def test_the_row_is_in_the_vault_not_in_plain_sight(self, client):
        """The ids are names: the row is in the encrypted settings table."""
        from database import get_setting
        import config
        client.post(URL, json={"model_id": "c/optional", "effort": "high"})
        assert json.loads(get_setting(config.SETTING_THINKING_EFFORTS)) == {
            "c/optional": "high"}


# ---------------------------------------------------------------------------
# 6. The notebook reader does not think
# ---------------------------------------------------------------------------

class TestTheNotebookReaderStaysWithoutThinking:
    @pytest.mark.anyio
    async def test_a_stored_choice_does_not_reach_the_reader(self, client, monkeypatch):
        import asyncio
        import config
        import database
        import notebook_worker
        from tests.test_notebook_worker import seed

        chat_id = seed(client, 30)
        database.set_setting(config.SETTING_NOTEBOOK_MODEL, "c/optional")
        client.post(URL, json={"model_id": "c/optional", "effort": "max"})
        sent: list[dict] = []

        async def spy(messages, model_id, gen_params, provider, **kw):
            sent.append(dict(gen_params))
            raise openrouter.OpenRouterError("openrouter_error")

        monkeypatch.setattr(openrouter, "complete", spy)
        w = notebook_worker.Worker()
        w.queue = asyncio.Queue(maxsize=4)
        await w._handle(chat_id)
        assert sent, "ground: the reader did send a request"
        for params in sent:
            assert "reasoning" not in params
            assert "thinking_effort" not in params
