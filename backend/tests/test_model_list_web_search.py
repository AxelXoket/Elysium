"""C4 in the pickers: a model the send point refuses is not offered (W4 r2 M1).

The send point (`openrouter.model_id_blocked`) already refuses web-search
variants, the auto router and presets. Before this, both pickers still listed
them, so a user could choose one and every send then failed. Both lists now
use the same predicate, and the notebook reader's model setting refuses such
an id by name (W1 r2 L4) instead of failing later under "unknown".

Every check drives the real route. The provider fetch is replaced at the
function the route calls; the id lists are the module's own predicate inputs.
"""
from __future__ import annotations

import copy

import pytest

import config
import database
import openrouter

#: A mix: the first two must stay (ground), the rest must go.
ORDINARY = ["openai/gpt-4o", "vendor/online-model"]
BLOCKED = ["openai/gpt-4o:online", "openrouter/auto", "perplexity/sonar-pro",
           "openai/gpt-4o-search-preview", "vendor/deep-research-1"]


def _row(model_id: str) -> dict:
    return {"id": model_id, "name": model_id}


@pytest.fixture()
def gate_open():
    import proxy_health
    database.set_setting("proxy_required", "0")
    proxy_health.invalidate_health_cache()


class TestTheChatModelList:
    @pytest.fixture()
    def served(self, monkeypatch):
        import routers.models_router as mr
        payload = {"source": "user", "cached": False,
                   "count": len(ORDINARY) + len(BLOCKED),
                   "models": [_row(m) for m in ORDINARY + BLOCKED]}
        original = copy.deepcopy(payload)

        async def fake(refresh: bool = False):
            return payload
        monkeypatch.setattr(mr, "fetch_models", fake)
        return payload, original

    def test_blocked_ids_are_not_offered_and_ordinary_ones_are(
            self, client, gate_open, served):
        resp = client.get("/api/v1/models/openrouter")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        ids = [m["id"] for m in body["models"]]
        assert ids == ORDINARY
        assert body["count"] == len(ORDINARY)
        assert body["source"] == "user"

    def test_the_cached_list_keeps_every_model(self, client, gate_open, served):
        """Metadata lookups read the cache by id; filtering must not edit it."""
        payload, original = served
        client.get("/api/v1/models/openrouter")
        assert payload == original

    def test_every_id_the_list_hides_is_one_the_send_point_refuses(self):
        # Ground and positive control on the predicate the route uses.
        assert not any(openrouter.model_id_blocked(m) for m in ORDINARY)
        assert all(openrouter.model_id_blocked(m) for m in BLOCKED)


class TestTheNotebookReaderList:
    def test_blocked_ids_are_not_offered_and_ordinary_ones_are(
            self, client, gate_open, monkeypatch):
        async def fake(*a, **kw):
            return [{"id": m, "provider": "P", "prompt_price": 0.1,
                     "context_length": 1, "endpoints": 2}
                    for m in ORDINARY + BLOCKED]
        monkeypatch.setattr(openrouter, "fetch_extraction_models", fake)
        resp = client.get("/api/v1/notebook/extract/models")
        assert resp.status_code == 200, resp.text
        assert [m["id"] for m in resp.json()["models"]] == ORDINARY


class TestTheNotebookReaderSetting:
    @pytest.mark.parametrize("model_id", ["openai/gpt-4o:online", "openrouter/auto",
                                          "perplexity/sonar", "x/y:nitro:ONLINE"])
    def test_a_blocked_id_is_refused_by_name_and_not_stored(self, client, model_id):
        ok = client.post("/api/v1/notebook/extract/settings",
                         json={"model_id": "vendor/good"})
        assert ok.status_code == 200
        resp = client.post("/api/v1/notebook/extract/settings",
                           json={"model_id": model_id})
        assert resp.status_code == 400
        assert resp.json()["detail"] == openrouter.MODEL_BLOCKED_REASON
        assert database.get_setting(config.SETTING_NOTEBOOK_MODEL) == "vendor/good"

    @pytest.mark.parametrize("model_id", ORDINARY)
    def test_ground_an_ordinary_id_is_saved(self, client, model_id):
        resp = client.post("/api/v1/notebook/extract/settings",
                           json={"model_id": model_id})
        assert resp.status_code == 200, resp.text
        assert database.get_setting(config.SETTING_NOTEBOOK_MODEL) == model_id
