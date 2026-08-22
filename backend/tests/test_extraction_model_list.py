"""FAZ 4 - the list of models a background job is allowed to spend money on.

This list is a promise, not a convenience. The app tells the user that nothing
they type leaves without a policy behind it, and this is the only place that
claim is enforced for the extractor: whatever is NOT in this list can never be
picked, and whatever IS in it has been checked against the one endpoint that
actually carries a data policy.

Two conditions, measured rather than assumed:

  * `/endpoints/zdr` is the ONLY authoritative per-endpoint policy source.
    `/models/{slug}/endpoints` has no policy field at all and silently ignores
    `?zdr=true`, so a list built there would look compliant and would not be;
  * `structured_outputs`, not `response_format` - different values, and models
    carry the second without the first, meaning `json_object` but no schema.
    Such a model is pickable, then fails at request time, every time.

The function had no test until the release sweep crashed on it with a
NameError. That is the real reason this file exists: an untested fetch is a
model picker that is empty forever and a feature that never runs.
"""
from __future__ import annotations

import httpx
import pytest

import openrouter


class _GetClient:
    """The narrowest shape fetch_extraction_models needs."""

    def __init__(self, response: httpx.Response | Exception):
        self._response = response
        self.urls: list[str] = []

    async def get(self, url, *a, **kw):
        self.urls.append(url)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def _json(payload, status=200):
    return httpx.Response(status, json=payload,
                          request=httpx.Request("GET", "http://x"))


def endpoint(**over):
    base = {
        "model_id": "vendor/cheap",
        "provider_name": "P",
        "context_length": 131072,
        "pricing": {"prompt": "0.00000006"},
        "supported_parameters": ["response_format", "structured_outputs"],
    }
    base.update(over)
    return base


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch):
    """The cache is module state; a leaked entry would make every later test
    assert against the first test's answer."""
    monkeypatch.setattr(openrouter, "_zdr_cache", None)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _serve(monkeypatch, payload, status=200):
    client = _GetClient(_json(payload, status))
    monkeypatch.setattr(openrouter, "get_client", lambda: client)
    return client


class TestOnlyTheZdrEndpointIsConsulted:
    @pytest.mark.anyio
    async def test_the_policy_endpoint_is_the_one_called(self, monkeypatch):
        """If this ever points at /models/.../endpoints the list keeps
        working and stops meaning anything - the failure is invisible."""
        client = _serve(monkeypatch, {"data": [endpoint()]})
        await openrouter.fetch_extraction_models()
        assert client.urls[0].endswith("/endpoints/zdr")

    @pytest.mark.anyio
    async def test_an_unreachable_host_is_reported_not_returned_empty(
            self, monkeypatch):
        """An empty list and a failed fetch look identical on screen, and one
        of them means "no model qualifies" while the other means nothing."""
        client = _GetClient(httpx.ConnectError("down"))
        monkeypatch.setattr(openrouter, "get_client", lambda: client)
        with pytest.raises(openrouter.OpenRouterError):
            await openrouter.fetch_extraction_models()

    @pytest.mark.anyio
    async def test_an_error_status_raises(self, monkeypatch):
        _serve(monkeypatch, {"error": {"message": "nope"}}, status=500)
        with pytest.raises(openrouter.OpenRouterError):
            await openrouter.fetch_extraction_models()


class TestTheSchemaCondition:
    @pytest.mark.anyio
    async def test_response_format_alone_is_not_enough(self, monkeypatch):
        """The trap this filter exists for: `response_format` without
        `structured_outputs` is `json_object` - free-form JSON, no schema.
        Every extraction from such a model fails the parse, forever."""
        _serve(monkeypatch, {"data": [
            endpoint(supported_parameters=["response_format"])]})
        assert await openrouter.fetch_extraction_models() == []

    @pytest.mark.anyio
    async def test_a_qualifying_endpoint_survives(self, monkeypatch):
        """Positive control: without it the test above passes on a filter
        that rejects everything."""
        _serve(monkeypatch, {"data": [endpoint()]})
        got = await openrouter.fetch_extraction_models()
        assert [m["id"] for m in got] == ["vendor/cheap"]

    @pytest.mark.anyio
    async def test_an_endpoint_with_no_model_id_is_skipped(self, monkeypatch):
        _serve(monkeypatch, {"data": [endpoint(model_id=None, name=None)]})
        assert await openrouter.fetch_extraction_models() == []

    @pytest.mark.anyio
    async def test_an_empty_payload_is_an_empty_list(self, monkeypatch):
        _serve(monkeypatch, {"data": []})
        assert await openrouter.fetch_extraction_models() == []


class TestWhatThePickerNeedsToShow:
    @pytest.mark.anyio
    async def test_price_is_per_million_not_per_token(self, monkeypatch):
        """OpenRouter quotes per token. Passed through raw, every cheap model
        renders as "$0.000" and the price column stops distinguishing
        anything - which is the entire reason it is on screen."""
        _serve(monkeypatch, {"data": [endpoint()]})
        got = await openrouter.fetch_extraction_models()
        assert got[0]["prompt_price"] == pytest.approx(0.06)

    @pytest.mark.anyio
    async def test_a_missing_price_does_not_break_the_list(self, monkeypatch):
        _serve(monkeypatch, {"data": [endpoint(pricing={})]})
        got = await openrouter.fetch_extraction_models()
        assert got[0]["prompt_price"] == 0.0

    @pytest.mark.anyio
    async def test_providers_are_counted_so_a_pinned_model_is_visible(
            self, monkeypatch):
        """Fallbacks are disabled for this call, so a single-endpoint model is
        pinned to one machine: when that machine is down extraction simply
        stops. The user is the one who has to know that before choosing."""
        _serve(monkeypatch, {"data": [
            endpoint(provider_name="P"),
            endpoint(provider_name="Q", pricing={"prompt": "0.00000009"}),
            endpoint(model_id="vendor/lonely", provider_name="R"),
        ]})
        got = {m["id"]: m for m in await openrouter.fetch_extraction_models()}
        assert got["vendor/cheap"]["endpoints"] == 2
        assert got["vendor/lonely"]["endpoints"] == 1

    @pytest.mark.anyio
    async def test_the_cheapest_endpoint_represents_the_model(self, monkeypatch):
        _serve(monkeypatch, {"data": [
            endpoint(provider_name="Dear", pricing={"prompt": "0.0000009"}),
            endpoint(provider_name="Cheap", pricing={"prompt": "0.00000006"}),
        ]})
        got = await openrouter.fetch_extraction_models()
        assert got[0]["provider"] == "Cheap"
        assert got[0]["prompt_price"] == pytest.approx(0.06)

    @pytest.mark.anyio
    async def test_the_list_is_ordered_cheapest_first(self, monkeypatch):
        _serve(monkeypatch, {"data": [
            endpoint(model_id="b/dear", pricing={"prompt": "0.000002"}),
            endpoint(model_id="a/cheap", pricing={"prompt": "0.00000006"}),
        ]})
        got = await openrouter.fetch_extraction_models()
        assert [m["id"] for m in got] == ["a/cheap", "b/dear"]


class TestTheCache:
    @pytest.mark.anyio
    async def test_a_second_call_does_not_hit_the_network(self, monkeypatch):
        client = _serve(monkeypatch, {"data": [endpoint()]})
        await openrouter.fetch_extraction_models()
        await openrouter.fetch_extraction_models()
        assert len(client.urls) == 1

    @pytest.mark.anyio
    async def test_refresh_goes_back_out(self, monkeypatch):
        client = _serve(monkeypatch, {"data": [endpoint()]})
        await openrouter.fetch_extraction_models()
        await openrouter.fetch_extraction_models(refresh=True)
        assert len(client.urls) == 2

    @pytest.mark.anyio
    async def test_a_failed_fetch_is_not_cached_as_an_answer(self, monkeypatch):
        """Otherwise one flaky minute empties the picker until restart."""
        bad = _GetClient(httpx.ConnectError("down"))
        monkeypatch.setattr(openrouter, "get_client", lambda: bad)
        with pytest.raises(openrouter.OpenRouterError):
            await openrouter.fetch_extraction_models()

        good = _serve(monkeypatch, {"data": [endpoint()]})
        assert len(await openrouter.fetch_extraction_models()) == 1
        assert good.urls, "the second call never reached the network"


class TestTheSameGateAsEveryOtherOutboundPath:
    """proxy_required=1 with no proxy URL is "proxy_missing" - the state every
    other outbound path refuses outright. Both extraction routes go to
    openrouter.ai, so both had to be walked through it; a path that forgets the
    gate is invisible, which is exactly how POST /settings/api-key once sent a
    freshly typed key and the user's real IP out unproxied.
    """

    def _arm(self, monkeypatch):
        import database
        import proxy_health
        database.set_setting("proxy_required", "1")
        proxy_health.invalidate_health_cache()

    def _disarm(self):
        import database
        import proxy_health
        database.set_setting("proxy_required", "0")
        proxy_health.invalidate_health_cache()

    def test_the_model_list_refuses_behind_an_armed_gate(
            self, client, monkeypatch):
        import openrouter

        called = []

        async def spy(*a, **kw):
            called.append(1)
            return []

        monkeypatch.setattr(openrouter, "fetch_extraction_models", spy)
        self._arm(monkeypatch)
        try:
            resp = client.get("/api/v1/notebook/extract/models")
            assert resp.status_code == 503
            assert resp.json()["detail"] == "proxy_missing"
            assert called == [], "no request may leave behind an armed gate"
        finally:
            self._disarm()

    def test_the_model_list_works_when_the_gate_is_open(
            self, client, monkeypatch):
        """Positive control: without it the assertion above is satisfied by a
        route that refuses unconditionally."""
        import openrouter

        async def ok(*a, **kw):
            return [{"id": "vendor/cheap", "provider": "P",
                     "prompt_price": 0.06, "context_length": 1, "endpoints": 2}]

        monkeypatch.setattr(openrouter, "fetch_extraction_models", ok)
        self._disarm()
        resp = client.get("/api/v1/notebook/extract/models")
        assert resp.status_code == 200
        assert resp.json()["models"][0]["id"] == "vendor/cheap"


class TestTheRequestCarriesTheSameIdentity:
    @pytest.mark.anyio
    async def test_the_key_is_sent_when_there_is_one(self, monkeypatch):
        captured = {}

        class _Spy(_GetClient):
            async def get(self, url, *a, **kw):
                captured.update(kw.get("headers") or {})
                return await super().get(url, *a, **kw)

        spy = _Spy(_json({"data": [endpoint()]}))
        monkeypatch.setattr(openrouter, "get_client", lambda: spy)
        monkeypatch.setattr(openrouter, "get_secret", lambda *a, **kw: "sk-x")
        await openrouter.fetch_extraction_models()
        assert captured.get("Authorization") == "Bearer sk-x"

    @pytest.mark.anyio
    async def test_a_locked_vault_does_not_raise_out_of_the_fetch(
            self, monkeypatch):
        """The policy list is public, so a locked vault means "no header", not
        a 500 - the background worker must never crash on the lock it is
        supposed to stand down for."""
        import vault_state

        def locked(*a, **kw):
            raise vault_state.VaultLockedError("vault_locked")

        client = _serve(monkeypatch, {"data": [endpoint()]})
        monkeypatch.setattr(openrouter, "get_secret", locked)
        assert len(await openrouter.fetch_extraction_models()) == 1
        assert client.urls, "the fetch never happened"


class TestWhatMayBeSavedAsTheModel:
    """The picker filters the LIST; nothing filtered the SETTING.

    `set_setting(SETTING_NOTEBOOK_MODEL, body.model_id)` took any string of any
    length. The wire guarantee does not depend on this - PROVIDER_POLICY pins
    zdr/deny/no-fallbacks on the request itself, so a model whose endpoint
    policy changed gets no qualifying endpoint rather than a downgraded one -
    but an unbounded string reaching the settings table and, from there, the
    outbound payload is not something to leave to the UI's good manners.
    """

    def test_a_real_model_id_is_accepted(self, client) -> None:
        resp = client.post("/api/v1/notebook/extract/settings",
                           json={"model_id": "vendor/cheap-model:free"})
        assert resp.status_code == 200

    def test_clearing_the_choice_is_allowed(self, client) -> None:
        """Empty means "off", and turning the feature off must never be
        harder than turning it on."""
        resp = client.post("/api/v1/notebook/extract/settings",
                           json={"model_id": ""})
        assert resp.status_code == 200

    @pytest.mark.parametrize("bad", [
        "not-a-model-id", "vendor/../../etc/passwd", "vendor/model spaces",
        "<script>", "vendor" + chr(92) + "model",
    ])
    def test_a_string_that_is_not_a_model_id_is_refused(self, client, bad):
        resp = client.post("/api/v1/notebook/extract/settings",
                           json={"model_id": bad})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "notebook_model_id_invalid"

    def test_an_unbounded_string_is_refused(self, client) -> None:
        resp = client.post("/api/v1/notebook/extract/settings",
                           json={"model_id": "a/" + "b" * 500})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "notebook_model_id_too_long"

    def test_a_refused_id_is_not_stored(self, client) -> None:
        """The control. A 400 that saved anyway would be the worse bug."""
        import config
        import database

        client.post("/api/v1/notebook/extract/settings",
                    json={"model_id": "vendor/good"})
        client.post("/api/v1/notebook/extract/settings",
                    json={"model_id": "rubbish"})
        assert database.get_setting(config.SETTING_NOTEBOOK_MODEL) == "vendor/good"


class TestFreeVariantsAreRefused:
    @pytest.mark.anyio
    async def test_a_free_variant_is_not_offered(self, monkeypatch) -> None:
        """`:free` endpoints sit behind a training/logging consent the ZDR
        list does not describe, so membership there is not the same promise
        for them. A background job reading somebody's conversation is the last
        place to accept a policy this app cannot read."""
        _serve(monkeypatch, {"data": [endpoint(model_id="vendor/cheap:free")]})
        assert await openrouter.fetch_extraction_models() == []

    @pytest.mark.anyio
    async def test_the_paid_sibling_is_still_offered(self, monkeypatch) -> None:
        """Control: the filter must reject the variant, not the model."""
        _serve(monkeypatch, {"data": [
            endpoint(model_id="vendor/cheap:free"),
            endpoint(model_id="vendor/cheap"),
        ]})
        got = await openrouter.fetch_extraction_models()
        assert [m["id"] for m in got] == ["vendor/cheap"]
