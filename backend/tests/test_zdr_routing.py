"""One Enter, one answer, under zero data retention (Q-121, F4-15).

The owner pressed Enter about five times per message before a reply came. The
chat sends `provider.zdr: true` and `require_parameters: true`, so OpenRouter
keeps only private endpoints that accept every sent key. The app decided what
to send from the MODEL-level parameter list, which also counts non-private
endpoints, so a sampler could empty the pool; and nothing retried the passing
failures (a 429, a provider restart, a cold start that sent nothing).

What this file pins, each with a ground control and a positive control:

- the sent keys are judged against the model's PRIVATE endpoints, not the
  model-level list (mutant: the model-level list), and the kept keys are
  accepted together by at least one endpoint;
- the private endpoint list is cached, dropped on a key or proxy change, and
  a fetch that was in flight during that change does not write itself back;
- the list unknown: the old filter, and a log line that names no model;
- a model with no private endpoint is refused before sending, with nothing
  stored, and the model list marks it;
- stop sequences no private endpoint accepts are not sent, and the reply says
  so in a notice;
- one automatic retry, only before anything reached the reader, never for the
  "never" kind, never for a timeout (the first-token one included),
  Retry-After honoured and capped, no duplicate rows;
- "never for this model" and "not right now" arrive as different codes.
"""
from __future__ import annotations

import json
import logging

import httpx
import pytest

import openrouter
import routers.completions as cr
from openrouter import OpenRouterError

from conftest import get_messages, make_character, make_chat

#: The real list lookup, taken at import time: conftest's `_zdr_list_unknown`
#: replaces the module attribute for every test, and the tests about the list
#: itself put this back.
REAL_ZDR_INDEX = openrouter.zdr_index

MODEL = "test/model-1"
BODY = {"message": "How are you?", "model_id": MODEL}

#: The model-level list a catalogue would report: the union over every
#: endpoint, private or not. It lists every sampler.
MODEL_LEVEL = ["max_tokens", "temperature", "top_p", "top_k", "stop",
               "reasoning"]


def _meta(params=MODEL_LEVEL):
    return {"id": MODEL, "context_length": 32768,
            "max_completion_tokens": 4096, "supported_parameters": list(params)}


def _ep(*params: str) -> dict:
    """One private endpoint in the index shape `zdr_index` returns."""
    return {"accepts": frozenset(params)}


@pytest.fixture()
def catalogue(monkeypatch):
    """The model-level catalogue lists every sampler (the trap)."""
    monkeypatch.setattr(cr, "get_cached_model_metadata", lambda mid: _meta())
    monkeypatch.setattr(openrouter, "get_cached_model_metadata",
                        lambda mid: _meta())


def _serve_index(monkeypatch, index):
    async def _index(refresh: bool = False):
        return index
    monkeypatch.setattr(openrouter, "zdr_index", _index)


@pytest.fixture()
def sleeps(monkeypatch):
    """Every wait between attempts, recorded instead of slept."""
    waited: list[float] = []

    async def _sleep(seconds: float) -> None:
        waited.append(seconds)

    monkeypatch.setattr(cr, "_retry_sleep", _sleep)
    return waited


def _events(client, chat_id, body=BODY) -> list[dict]:
    with client.stream("POST", f"/api/v1/chats/{chat_id}/complete/stream",
                       json=body) as resp:
        assert resp.status_code == 200, resp.read()
        return [json.loads(line[len("data:"):].strip())
                for line in resp.iter_lines()
                if line.strip().startswith("data:")]


class _Script:
    """A fake complete_stream that plays one scripted attempt per call.

    Each step is ("fail", OpenRouterError), ("text", [deltas]), or
    ("text_then_fail", [deltas], OpenRouterError). `calls` counts attempts.
    """

    def __init__(self, *steps):
        self.steps = list(steps)
        self.calls = 0
        self.gen_params: list[dict] = []

    def __call__(self, messages, model_id, gen_params, provider, **kw):
        self.calls += 1
        self.gen_params.append(dict(gen_params))
        step = self.steps[min(self.calls, len(self.steps)) - 1]
        on_finish = kw.get("on_finish")
        on_notice = kw.get("on_notice")

        async def gen():
            kind = step[0]
            if kind == "fail":
                raise step[1]
            for d in step[1]:
                yield d
            if kind == "text_then_fail":
                raise step[2]
            if kind == "empty_length":
                if on_finish:
                    on_finish(True)
                return
            if kind == "empty_unfinished":
                if on_notice:
                    on_notice(openrouter.NOTICE_STREAM_UNFINISHED, 0)
                if on_finish:
                    on_finish(True)
                return
            if on_finish:
                on_finish(False)

        return gen()


def _unavailable(status=503, retry_after=None):
    """The exception the provider layer raises for this status: the reason
    from its own table, so the test cannot drift from it."""
    return OpenRouterError(openrouter._status_to_reason(status), status=status,
                           retryable=status in openrouter.RETRYABLE_STATUSES,
                           retry_after=retry_after)


# ---------------------------------------------------------------------------
# 1. The planner: private endpoints decide, together
# ---------------------------------------------------------------------------

class TestThePlanner:
    def test_a_key_no_private_endpoint_takes_is_dropped(self):
        kept, dropped, eligible = openrouter.plan_private_params(
            [_ep("max_tokens"), _ep("max_tokens", "stop")],
            {"max_tokens": 100, "temperature": 0.7})
        assert kept == {"max_tokens": 100}
        assert dropped == ["temperature"]
        assert eligible == 2

    def test_a_key_a_private_endpoint_takes_is_kept(self):
        """Ground control: the same call with the key listed keeps it."""
        kept, dropped, eligible = openrouter.plan_private_params(
            [_ep("max_tokens", "temperature"), _ep("max_tokens")],
            {"max_tokens": 100, "temperature": 0.7})
        assert kept == {"max_tokens": 100, "temperature": 0.7}
        assert dropped == []
        assert eligible == 1

    def test_the_kept_keys_are_taken_together_by_one_endpoint(self):
        """Union alone would keep both and leave require_parameters with no
        endpoint that takes the pair. The first in order wins."""
        kept, dropped, eligible = openrouter.plan_private_params(
            [_ep("max_tokens", "temperature"), _ep("max_tokens", "top_k")],
            {"max_tokens": 100, "temperature": 0.7, "top_k": 40})
        assert kept == {"max_tokens": 100, "temperature": 0.7}
        assert dropped == ["top_k"]
        assert eligible == 1

    def test_max_completion_tokens_satisfies_max_tokens(self):
        """OpenAI's private endpoints list only the newer name."""
        kept, _, eligible = openrouter.plan_private_params(
            [_ep("max_completion_tokens")], {"max_tokens": 100})
        assert kept == {"max_tokens": 100}
        assert eligible == 1

    def test_no_endpoint_for_a_required_key_is_zero(self):
        _, _, eligible = openrouter.plan_private_params(
            [_ep("temperature")], {"max_tokens": 100})
        assert eligible == 0

    def test_no_endpoints_at_all_is_zero(self):
        assert openrouter.plan_private_params([], {"max_tokens": 1})[2] == 0

    def test_a_suffix_borrows_the_base_list_only_when_it_is_no_model(
            self, monkeypatch):
        index = {"v/m": [_ep("max_tokens")]}
        monkeypatch.setattr(openrouter, "get_cached_model_metadata",
                            lambda mid: None)
        assert openrouter.zdr_endpoints_in(index, "v/m:nitro") == index["v/m"]
        # `:free` is a model of its own in the catalogue: no borrowing.
        monkeypatch.setattr(openrouter, "get_cached_model_metadata",
                            lambda mid: {"id": mid})
        assert openrouter.zdr_endpoints_in(index, "v/m:free") == []


# ---------------------------------------------------------------------------
# 2. Through the route: what actually goes on the wire
# ---------------------------------------------------------------------------

class TestWhatIsSent:
    def test_the_private_list_decides_not_the_model_level_one(
            self, client, provider, catalogue, monkeypatch):
        """The Opus 5.5 case: the catalogue lists temperature, no private
        endpoint does. It must not be sent."""
        _serve_index(monkeypatch, {MODEL: [_ep("max_tokens", "stop")]})
        chat_id = make_chat(client, make_character(client))
        resp = client.post(f"/api/v1/chats/{chat_id}/complete", json={
            **BODY, "generation_params": {"temperature": 0.7,
                                          "max_tokens": 200}})
        assert resp.status_code == 200, resp.text
        sent = provider.calls[-1]["gen_params"]
        assert "temperature" not in sent
        assert sent["max_tokens"] == 200

    def test_a_key_the_private_list_carries_is_sent(
            self, client, provider, catalogue, monkeypatch):
        """Positive control for the test above."""
        _serve_index(monkeypatch,
                     {MODEL: [_ep("max_tokens", "temperature")]})
        chat_id = make_chat(client, make_character(client))
        resp = client.post(f"/api/v1/chats/{chat_id}/complete", json={
            **BODY, "generation_params": {"temperature": 0.7,
                                          "max_tokens": 200}})
        assert resp.status_code == 200, resp.text
        assert provider.calls[-1]["gen_params"]["temperature"] == 0.7

    def test_the_provider_block_is_untouched(
            self, client, provider, catalogue, monkeypatch):
        """The locked contract: exactly the four fields, no order/only/..."""
        _serve_index(monkeypatch, {MODEL: [_ep("max_tokens")]})
        chat_id = make_chat(client, make_character(client))
        client.post(f"/api/v1/chats/{chat_id}/complete", json=BODY)
        assert provider.calls[-1]["provider"] == {
            "zdr": True, "data_collection": "deny",
            "allow_fallbacks": True, "require_parameters": True}

    def test_an_unknown_list_keeps_todays_filter_and_says_so(
            self, client, provider, catalogue, caplog):
        """conftest leaves the list unknown. The model-level list decides,
        and one log line says so without naming the model."""
        chat_id = make_chat(client, make_character(client))
        with caplog.at_level(logging.INFO, logger="routers.completions"):
            resp = client.post(f"/api/v1/chats/{chat_id}/complete", json={
                **BODY, "generation_params": {"temperature": 0.7}})
        assert resp.status_code == 200, resp.text
        assert provider.calls[-1]["gen_params"]["temperature"] == 0.7
        lines = [r.getMessage() for r in caplog.records
                 if "model-level list" in r.getMessage()]
        assert lines, "the fallback left no trace"
        assert not any(MODEL in line for line in lines)


# ---------------------------------------------------------------------------
# 3. Preflight: a model no private endpoint serves is refused, and marked
# ---------------------------------------------------------------------------

class TestPreflight:
    def test_no_private_endpoint_is_refused_before_anything(
            self, client, provider, catalogue, monkeypatch):
        _serve_index(monkeypatch, {"other/model": [_ep("max_tokens")]})
        chat_id = make_chat(client, make_character(client))
        resp = client.post(f"/api/v1/chats/{chat_id}/complete", json=BODY)
        assert resp.status_code == 400
        assert resp.json()["detail"] == cr.MODEL_NO_PRIVATE_ENDPOINT
        assert provider.calls == []
        assert [m["role"] for m in get_messages(client, chat_id)] == [
            "assistant"]

    def test_the_stream_route_refuses_before_storing_the_turn(
            self, client, catalogue, monkeypatch):
        _serve_index(monkeypatch, {})
        script = _Script(("text", ["hi"]))
        monkeypatch.setattr(cr, "complete_stream", script)
        chat_id = make_chat(client, make_character(client))
        resp = client.post(f"/api/v1/chats/{chat_id}/complete/stream",
                           json=BODY)
        assert resp.status_code == 400
        assert resp.json()["detail"] == cr.MODEL_NO_PRIVATE_ENDPOINT
        assert script.calls == 0
        assert len(get_messages(client, chat_id)) == 1

    def test_endpoints_that_refuse_the_required_key_are_refused_too(
            self, client, provider, catalogue, monkeypatch):
        _serve_index(monkeypatch, {MODEL: [_ep("temperature")]})
        chat_id = make_chat(client, make_character(client))
        resp = client.post(f"/api/v1/chats/{chat_id}/complete", json={
            **BODY, "generation_params": {"max_tokens": 100}})
        assert resp.status_code == 400
        assert resp.json()["detail"] == cr.MODEL_NO_PRIVATE_ENDPOINT
        assert provider.calls == []

    def test_one_private_endpoint_is_enough(
            self, client, provider, catalogue, monkeypatch):
        """Positive control: the refusal is about zero, not about few."""
        _serve_index(monkeypatch, {MODEL: [_ep("max_tokens")]})
        chat_id = make_chat(client, make_character(client))
        resp = client.post(f"/api/v1/chats/{chat_id}/complete", json={
            **BODY, "generation_params": {"max_tokens": 100}})
        assert resp.status_code == 200, resp.text
        assert len(provider.calls) == 1

    def test_the_model_list_marks_it_instead_of_hiding_it(
            self, client, monkeypatch):
        async def _models(refresh=False):
            return {"source": "public", "cached": False, "count": 2,
                    "models": [{"id": "a/private"}, {"id": "b/none"}]}
        import routers.models_router as mr
        monkeypatch.setattr(mr, "fetch_models", _models)
        monkeypatch.setattr(openrouter, "get_cached_model_metadata",
                            lambda mid: None)
        _serve_index(monkeypatch, {"a/private": [_ep("max_tokens"),
                                                 _ep("max_completion_tokens")]})
        data = client.get("/api/v1/models/openrouter").json()
        got = {m["id"]: m["zdr_endpoints"] for m in data["models"]}
        assert got == {"a/private": 2, "b/none": 0}
        assert data["zdr_known"] is True

    def test_an_unknown_list_marks_nothing(self, client, monkeypatch):
        """Ground control: unknown is not zero. Nothing is claimed."""
        async def _models(refresh=False):
            return {"source": "public", "cached": False, "count": 1,
                    "models": [{"id": "b/none"}]}
        import routers.models_router as mr
        monkeypatch.setattr(mr, "fetch_models", _models)
        data = client.get("/api/v1/models/openrouter").json()
        assert data["models"][0]["zdr_endpoints"] is None
        assert data["zdr_known"] is False


# ---------------------------------------------------------------------------
# 4. Stop sequences: never dropped silently
# ---------------------------------------------------------------------------

class TestStop:
    def test_stop_no_private_endpoint_takes_is_not_sent_and_said(
            self, client, provider, catalogue, monkeypatch):
        _serve_index(monkeypatch, {MODEL: [_ep("max_tokens")]})
        chat_id = make_chat(client, make_character(client))
        resp = client.post(f"/api/v1/chats/{chat_id}/complete", json={
            **BODY, "generation_params": {"stop": ["###"]}})
        assert resp.status_code == 200, resp.text
        assert "stop" not in provider.calls[-1]["gen_params"]
        assert {"type": "notice", "code": "stop_not_sent_privately"} in (
            resp.json()["notices"])

    def test_the_stream_says_it_before_the_first_delta(
            self, client, catalogue, monkeypatch):
        _serve_index(monkeypatch, {MODEL: [_ep("max_tokens")]})
        script = _Script(("text", ["hello"]))
        monkeypatch.setattr(cr, "complete_stream", script)
        chat_id = make_chat(client, make_character(client))
        events = _events(client, chat_id, {
            **BODY, "generation_params": {"stop": ["###"]}})
        kinds = [(e["type"], e.get("code")) for e in events]
        notice = kinds.index(("notice", "stop_not_sent_privately"))
        first_delta = next(i for i, k in enumerate(kinds) if k[0] == "delta")
        assert notice < first_delta
        assert "stop" not in script.gen_params[0]

    def test_stop_a_private_endpoint_takes_is_sent_without_a_notice(
            self, client, provider, catalogue, monkeypatch):
        """Positive control."""
        _serve_index(monkeypatch, {MODEL: [_ep("max_tokens", "stop")]})
        chat_id = make_chat(client, make_character(client))
        resp = client.post(f"/api/v1/chats/{chat_id}/complete", json={
            **BODY, "generation_params": {"stop": ["###"]}})
        assert provider.calls[-1]["gen_params"]["stop"] == ["###"]
        assert not any(n.get("code") == "stop_not_sent_privately"
                       for n in resp.json()["notices"])


# ---------------------------------------------------------------------------
# 5. The cache: fetched once, dropped on a key or proxy change (F4-15)
# ---------------------------------------------------------------------------

class _GetClient:
    def __init__(self, rows):
        self.rows = rows
        self.urls: list[str] = []
        self.gate = None

    async def get(self, url, *a, **kw):
        self.urls.append(url)
        if self.gate is not None:
            await self.gate()
        if isinstance(self.rows, Exception):
            raise self.rows
        return httpx.Response(200, json={"data": self.rows},
                              request=httpx.Request("GET", url))


@pytest.fixture()
def real_index(monkeypatch):
    monkeypatch.setattr(openrouter, "zdr_index", REAL_ZDR_INDEX)
    monkeypatch.setattr(openrouter, "get_secret", lambda *a, **kw: None)


def _rows():
    return [{"model_id": MODEL, "supported_parameters": ["max_tokens"]},
            {"model_id": MODEL, "supported_parameters": ["max_tokens", "stop"]}]


class TestTheCache:
    @pytest.mark.anyio
    async def test_the_list_is_fetched_once(self, real_index, monkeypatch):
        fake = _GetClient(_rows())
        monkeypatch.setattr(openrouter, "get_client", lambda: fake)
        first = await openrouter.zdr_index()
        await openrouter.zdr_index()
        assert len(fake.urls) == 1
        assert len(first[MODEL]) == 2

    @pytest.mark.anyio
    async def test_a_key_or_proxy_change_drops_it(self, real_index,
                                                  monkeypatch):
        fake = _GetClient(_rows())
        monkeypatch.setattr(openrouter, "get_client", lambda: fake)
        await openrouter.zdr_index()
        openrouter.invalidate_model_cache()
        await openrouter.zdr_index()
        assert len(fake.urls) == 2

    def test_saving_a_proxy_drops_it_through_the_real_route(
            self, client, real_index, monkeypatch):
        """The call site, not just the function: the settings route that
        changes the egress path is what must clear the list."""
        openrouter._remember_zdr_index(_rows(), openrouter._zdr_generation)
        assert openrouter._zdr_index_cache is not None
        resp = client.delete("/api/v1/settings/proxy")
        assert resp.status_code in (200, 204), resp.text
        assert openrouter._zdr_index_cache is None

    @pytest.mark.anyio
    async def test_a_fetch_in_flight_during_the_change_is_not_kept(
            self, real_index, monkeypatch):
        fake = _GetClient(_rows())

        async def _change_midway():
            openrouter.invalidate_model_cache()

        fake.gate = _change_midway
        monkeypatch.setattr(openrouter, "get_client", lambda: fake)
        await openrouter.zdr_index()
        assert openrouter._zdr_index_cache is None

    @pytest.mark.anyio
    async def test_a_failed_fetch_is_remembered_and_logged_without_names(
            self, real_index, monkeypatch, caplog):
        fake = _GetClient(httpx.ConnectError("down"))
        monkeypatch.setattr(openrouter, "get_client", lambda: fake)
        with caplog.at_level(logging.WARNING, logger="openrouter"):
            assert await openrouter.zdr_index() is None
            assert await openrouter.zdr_index() is None
        assert len(fake.urls) == 1, "a down list cost every send a timeout"
        text = " ".join(r.getMessage() for r in caplog.records)
        assert "model-level list" in text
        assert MODEL not in text

    @pytest.mark.anyio
    async def test_the_extraction_list_fills_the_chat_index_too(
            self, real_index, monkeypatch):
        rows = [{"model_id": MODEL, "provider_name": "P",
                 "supported_parameters": ["max_tokens", "structured_outputs"],
                 "pricing": {"prompt": "0"}}]
        fake = _GetClient(rows)
        monkeypatch.setattr(openrouter, "get_client", lambda: fake)
        await openrouter.fetch_extraction_models()
        await openrouter.zdr_index()
        assert len(fake.urls) == 1


# ---------------------------------------------------------------------------
# 6. The one retry
# ---------------------------------------------------------------------------

@pytest.fixture()
def private(catalogue, monkeypatch):
    """Two private endpoints take the request: the 503 rule allows a retry."""
    _serve_index(monkeypatch, {MODEL: [_ep("max_tokens"), _ep("max_tokens")]})


class TestRetry:
    def test_a_passing_failure_is_retried_once_and_answered(
            self, client, private, sleeps, monkeypatch):
        script = _Script(("fail", _unavailable(502)), ("text", ["fine"]))
        monkeypatch.setattr(cr, "complete_stream", script)
        chat_id = make_chat(client, make_character(client))
        events = _events(client, chat_id)
        assert script.calls == 2
        assert events[-1]["type"] == "done"
        assert events[-1]["retried"] == 1
        roles = [m["role"] for m in get_messages(client, chat_id)]
        assert roles == ["assistant", "user", "assistant"], "a duplicate row"
        assert sleeps == [cr.RETRY_BACKOFF_S]

    def test_an_ordinary_reply_carries_no_retry_count(
            self, client, private, sleeps, monkeypatch):
        """Ground control: nothing failed, nothing is said."""
        script = _Script(("text", ["fine"]))
        monkeypatch.setattr(cr, "complete_stream", script)
        chat_id = make_chat(client, make_character(client))
        events = _events(client, chat_id)
        assert script.calls == 1
        assert "retried" not in events[-1]
        assert sleeps == []

    def test_the_cap_is_two_attempts(self, client, private, sleeps,
                                     monkeypatch):
        script = _Script(("fail", _unavailable(503)))
        monkeypatch.setattr(cr, "complete_stream", script)
        chat_id = make_chat(client, make_character(client))
        events = _events(client, chat_id)
        assert script.calls == cr.MAX_PROVIDER_ATTEMPTS == 2
        assert events[-1] == {"type": "error", "status": 502,
                              "code": "openrouter_temporarily_unavailable"}
        assert [m["role"] for m in get_messages(client, chat_id)] == [
            "assistant"], "the failed turn left a row"

    def test_nothing_is_retried_after_the_first_delta(
            self, client, private, sleeps, monkeypatch):
        script = _Script(("text_then_fail", ["half a"], _unavailable(502)),
                         ("text", ["whole"]))
        monkeypatch.setattr(cr, "complete_stream", script)
        chat_id = make_chat(client, make_character(client))
        events = _events(client, chat_id)
        assert script.calls == 1
        assert events[-1]["type"] == "error"
        assert events[-1].get("partial_saved") is True

    def test_a_404_is_never_retried_and_says_never(
            self, client, private, sleeps, monkeypatch):
        script = _Script(("fail", OpenRouterError("openrouter_error",
                                                  status=404)),
                         ("text", ["x"]))
        monkeypatch.setattr(cr, "complete_stream", script)
        chat_id = make_chat(client, make_character(client))
        events = _events(client, chat_id)
        assert script.calls == 1
        assert events[-1]["code"] == "openrouter_no_provider_meets_privacy"

    def test_a_503_is_not_retried_when_no_private_endpoint_is_known(
            self, client, catalogue, sleeps, monkeypatch):
        """The list unknown: the preflight showed nothing, so a 503 may be
        the empty pool and is not sent twice."""
        script = _Script(("fail", _unavailable(503)), ("text", ["x"]))
        monkeypatch.setattr(cr, "complete_stream", script)
        chat_id = make_chat(client, make_character(client))
        _events(client, chat_id)
        assert script.calls == 1

    def test_a_502_is_retried_even_with_the_list_unknown(
            self, client, catalogue, sleeps, monkeypatch):
        """Positive control for the test above: only 503 needs the count."""
        script = _Script(("fail", _unavailable(502)), ("text", ["x"]))
        monkeypatch.setattr(cr, "complete_stream", script)
        chat_id = make_chat(client, make_character(client))
        _events(client, chat_id)
        assert script.calls == 2

    def test_retry_after_is_waited(self, client, private, sleeps,
                                   monkeypatch):
        script = _Script(("fail", _unavailable(429, retry_after=2.0)),
                         ("text", ["x"]))
        monkeypatch.setattr(cr, "complete_stream", script)
        chat_id = make_chat(client, make_character(client))
        _events(client, chat_id)
        assert sleeps == [2.0]
        assert script.calls == 2

    def test_a_retry_after_over_the_cap_is_not_cut_short(
            self, client, private, sleeps, monkeypatch):
        """Waiting 4 s against a 30 s Retry-After would not honour it; the
        reader is told to wait instead."""
        script = _Script(("fail", _unavailable(
            429, retry_after=cr.RETRY_AFTER_CAP_S + 26)), ("text", ["x"]))
        monkeypatch.setattr(cr, "complete_stream", script)
        chat_id = make_chat(client, make_character(client))
        events = _events(client, chat_id)
        assert script.calls == 1
        assert sleeps == []
        assert events[-1]["code"] == "openrouter_rate_limited"

    def test_an_empty_reply_cut_by_length_is_named_not_retried(
            self, client, private, sleeps, monkeypatch):
        script = _Script(("empty_length", []), ("text", ["x"]))
        monkeypatch.setattr(cr, "complete_stream", script)
        chat_id = make_chat(client, make_character(client))
        events = _events(client, chat_id)
        assert script.calls == 1
        assert events[-1]["code"] == "reasoning_used_reply_budget"

    def test_an_empty_reply_with_no_reason_is_retried(
            self, client, private, sleeps, monkeypatch):
        script = _Script(("text", []), ("text", ["x"]))
        monkeypatch.setattr(cr, "complete_stream", script)
        chat_id = make_chat(client, make_character(client))
        events = _events(client, chat_id)
        assert script.calls == 2
        assert events[-1]["type"] == "done"

    def test_an_empty_stream_that_never_finished_is_retried(
            self, client, private, sleeps, monkeypatch):
        """Cut off by silence, not by length: the passing kind."""
        script = _Script(("empty_unfinished", []), ("text", ["x"]))
        monkeypatch.setattr(cr, "complete_stream", script)
        chat_id = make_chat(client, make_character(client))
        events = _events(client, chat_id)
        assert script.calls == 2
        assert events[-1]["type"] == "done"
        assert not any(e.get("code") == openrouter.NOTICE_STREAM_UNFINISHED
                       for e in events), "the failed attempt's notice leaked"

    def test_a_first_token_timeout_is_not_retried(
            self, client, private, sleeps, fake_key, monkeypatch):
        """The real provider layer, not a scripted error: a stream that sends
        nothing but keepalives past the first-token limit is sent ONCE. The
        reader already waited the whole limit; a second attempt would make
        that wait up to twice as long."""
        monkeypatch.setattr(openrouter, "STREAM_FIRST_TOKEN_TIMEOUT", -1.0)
        upstream = _CountingStream(lambda: b": OPENROUTER PROCESSING\n\n")
        monkeypatch.setattr(openrouter, "get_client", lambda: upstream)
        chat_id = make_chat(client, make_character(client))
        events = _events(client, chat_id)
        assert upstream.calls == 1
        assert events[-1]["type"] == "error"
        assert events[-1]["code"] == "openrouter_timeout"
        assert sleeps == []

    def test_a_read_timeout_is_not_retried(
            self, client, private, sleeps, fake_key, monkeypatch):
        """httpx's own read timeout on the stream: the same wait, the same
        rule."""
        upstream = _CountingStream(httpx.ReadTimeout("slow"))
        monkeypatch.setattr(openrouter, "get_client", lambda: upstream)
        chat_id = make_chat(client, make_character(client))
        events = _events(client, chat_id)
        assert upstream.calls == 1
        assert events[-1]["code"] == "openrouter_timeout"

    def test_a_dropped_connection_before_output_is_retried(
            self, client, private, sleeps, fake_key, monkeypatch):
        """Positive control for the two above, through the same real layer:
        a connection that drops before any output is still retried once."""
        upstream = _CountingStream(httpx.ReadError("gone"), then=(
            b'data: {"choices": [{"delta": {"content": "fine"}}]}\n\n'
            b"data: [DONE]\n\n"))
        monkeypatch.setattr(openrouter, "get_client", lambda: upstream)
        chat_id = make_chat(client, make_character(client))
        events = _events(client, chat_id)
        assert upstream.calls == 2
        assert events[-1]["type"] == "done"
        assert events[-1]["retried"] == 1

    def test_a_non_retryable_failure_is_sent_once(
            self, client, private, sleeps, monkeypatch):
        script = _Script(("fail", OpenRouterError(
            "openrouter_moderation_blocked", status=403)), ("text", ["x"]))
        monkeypatch.setattr(cr, "complete_stream", script)
        chat_id = make_chat(client, make_character(client))
        _events(client, chat_id)
        assert script.calls == 1

    def test_the_retry_log_names_only_attempt_and_status(
            self, client, private, sleeps, monkeypatch, caplog):
        script = _Script(("fail", _unavailable(502)), ("text", ["x"]))
        monkeypatch.setattr(cr, "complete_stream", script)
        chat_id = make_chat(client, make_character(client))
        with caplog.at_level(logging.INFO, logger="routers.completions"):
            _events(client, chat_id)
        lines = [r.getMessage() for r in caplog.records
                 if "attempt" in r.getMessage()]
        assert lines == ["Provider attempt 1 failed: status=502; retrying."]

    def test_the_non_streaming_route_retries_the_same_way(
            self, client, private, sleeps, monkeypatch):
        calls = []

        async def _complete(messages, model_id, gen_params, provider, **kw):
            calls.append(1)
            if len(calls) == 1:
                raise _unavailable(429, retry_after=1.0)
            return {"choices": [{"message": {"content": "ok"}}]}

        monkeypatch.setattr(cr, "complete", _complete)
        chat_id = make_chat(client, make_character(client))
        resp = client.post(f"/api/v1/chats/{chat_id}/complete", json=BODY)
        assert resp.status_code == 200, resp.text
        assert resp.json()["retried"] == 1
        assert len(calls) == 2
        assert sleeps == [1.0]
        assert [m["role"] for m in get_messages(client, chat_id)] == [
            "assistant", "user", "assistant"]


# ---------------------------------------------------------------------------
# 7. Where retryability comes from: the real provider layer
# ---------------------------------------------------------------------------

class _StreamClient:
    def __init__(self, status: int, raw: bytes, headers=None):
        self.status, self.raw, self.headers = status, raw, headers or {}

    def stream(self, *a, **kw):
        outer = self

        class _Ctx:
            async def __aenter__(self):
                resp = httpx.Response(outer.status, headers=outer.headers,
                                      request=httpx.Request("POST", "http://x"))

                async def aiter_bytes():
                    yield outer.raw

                resp.aiter_bytes = aiter_bytes
                return resp

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


class _CountingStream:
    """An httpx client stand-in for the real complete_stream. The first
    attempt answers 200 and then does `first`: raise it when it is an
    exception, else yield the bytes it returns. Later attempts yield `then`.
    `calls` counts attempts that reached the upstream."""

    def __init__(self, first, then: bytes | None = None):
        self.first, self.then, self.calls = first, then, 0

    def stream(self, *a, **kw):
        self.calls += 1
        attempt, outer = self.calls, self

        class _Ctx:
            async def __aenter__(self):
                resp = httpx.Response(200, request=httpx.Request("POST",
                                                                 "http://x"))

                async def aiter_bytes():
                    if attempt > 1 and outer.then is not None:
                        yield outer.then
                        return
                    if isinstance(outer.first, BaseException):
                        raise outer.first
                    yield outer.first()

                resp.aiter_bytes = aiter_bytes
                return resp

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


@pytest.fixture()
def fake_key(monkeypatch):
    monkeypatch.setattr(openrouter, "get_secret", lambda name: "sk-test")
    monkeypatch.setattr(openrouter.safeword_filter, "needles", lambda: ())


async def _stream_error(monkeypatch, client) -> OpenRouterError:
    monkeypatch.setattr(openrouter, "get_client", lambda: client)
    with pytest.raises(OpenRouterError) as info:
        async for _ in openrouter.complete_stream(
                [{"role": "user", "content": "hi"}], MODEL, {}, None):
            pass
    return info.value


class _RaisingPost:
    def __init__(self, exc: BaseException):
        self.exc = exc

    async def post(self, *a, **kw):
        raise self.exc


async def _post_error(monkeypatch, exc: BaseException) -> OpenRouterError:
    monkeypatch.setattr(openrouter, "get_client", lambda: _RaisingPost(exc))
    with pytest.raises(OpenRouterError) as info:
        await openrouter.complete([{"role": "user", "content": "hi"}], MODEL,
                                  {}, None)
    return info.value


class TestTheProviderLayerMarksIt:
    @pytest.mark.anyio
    async def test_a_503_with_retry_after_is_marked(self, fake_key,
                                                    monkeypatch):
        exc = await _stream_error(monkeypatch, _StreamClient(
            503, b"{}", {"Retry-After": "3"}))
        assert (exc.status, exc.retryable, exc.retry_after) == (503, True, 3.0)
        assert exc.reason == "openrouter_server_error", "the reason moved"

    @pytest.mark.anyio
    async def test_a_404_is_not(self, fake_key, monkeypatch):
        exc = await _stream_error(monkeypatch, _StreamClient(404, b"{}"))
        assert (exc.status, exc.retryable) == (404, False)

    @pytest.mark.anyio
    async def test_an_error_chunk_carries_its_code(self, fake_key,
                                                   monkeypatch):
        chunk = {"error": {"code": 502, "message": "x"},
                 "choices": [{"finish_reason": "error", "delta": {}}]}
        raw = f"data: {json.dumps(chunk)}\n\n".encode()
        exc = await _stream_error(monkeypatch, _StreamClient(200, raw))
        assert (exc.status, exc.retryable) == (502, True)

    @pytest.mark.anyio
    async def test_a_400_chunk_is_not(self, fake_key, monkeypatch):
        chunk = {"error": {"code": 400, "message": "x"}}
        raw = f"data: {json.dumps(chunk)}\n\n".encode()
        exc = await _stream_error(monkeypatch, _StreamClient(200, raw))
        assert (exc.status, exc.retryable) == (400, False)

    @pytest.mark.anyio
    async def test_a_non_streaming_connect_timeout_is_not(self, fake_key,
                                                          monkeypatch):
        """Nothing was billed, but the reader already waited the whole
        timeout; a second attempt could make them wait it again."""
        exc = await _post_error(monkeypatch, httpx.ConnectTimeout("slow"))
        assert (exc.reason, exc.reached_provider, exc.retryable) == (
            "openrouter_timeout", False, False)

    @pytest.mark.anyio
    async def test_a_non_streaming_connect_error_is(self, fake_key,
                                                    monkeypatch):
        """Positive control for the test above: the same call site, a
        connection that failed fast, is still the passing kind."""
        exc = await _post_error(monkeypatch, httpx.ConnectError("refused"))
        assert (exc.reached_provider, exc.retryable) == (False, True)

    @pytest.mark.parametrize("value, seconds", [
        ("2", 2.0), ("0.5", 0.5), ("-3", 0.0), ("soon", None), (None, None),
        ("Wed, 21 Oct 2015 07:28:00 GMT", 0.0),
    ])
    def test_retry_after_is_read_both_ways(self, value, seconds):
        assert openrouter._parse_retry_after(value) == seconds


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---------------------------------------------------------------------------
# 8. "Never" and "not now" are different codes
# ---------------------------------------------------------------------------

class TestTheTwoKinds:
    def test_never_and_not_now_do_not_share_a_code(self):
        never = {
            cr._provider_failure(OpenRouterError("openrouter_error",
                                                 status=404))[1],
            cr.MODEL_NO_PRIVATE_ENDPOINT,
        }
        not_now = {
            cr._provider_failure(_unavailable(502))[1],
            cr._provider_failure(_unavailable(503))[1],
            cr._provider_failure(OpenRouterError(
                "openrouter_error", retryable=True))[1],
        }
        assert never == {"openrouter_no_provider_meets_privacy",
                         "model_no_private_endpoint"}
        assert not_now == {"openrouter_temporarily_unavailable"}
        assert never.isdisjoint(not_now)

    def test_an_unmarked_failure_keeps_its_old_code(self):
        """Ground control: a 500 (not marked) reads the table as before."""
        assert cr._provider_failure(OpenRouterError(
            "openrouter_server_error", status=500)) == (
            502, "openrouter_completion_error")
