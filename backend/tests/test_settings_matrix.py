"""V9-5 - the unified settings matrix.

A panel that only shows what the selected engine supports teaches the wrong
thing: controls appear and disappear on a model swap, and nobody learns the
difference belongs to the ENGINE rather than to the app. So the panel shows the
union, disabled where it cannot act - and the REASON is the part that has to be
right, because three very different situations look identical from outside.
"""
import pytest

import config
from tests.test_tts_core import make_fish
from tts import matrix, speed
from tts.base import ParamSpec
from tts.registry import adapter_for


@pytest.fixture()
def fish_model(client, monkeypatch, tmp_path):
    """A real detected model, so the endpoint tests go through the real path."""
    root = tmp_path / "voice" / "models"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "TTS_MODELS_DIR", str(root), raising=False)
    make_fish(root)
    return client.get("/api/v1/tts/models").json()["models"][0]["uid"]


def rows_for(engine_id: str):
    adapter = adapter_for(engine_id)
    specs = adapter.describe_settings(matrix._nominal(engine_id))
    return {r["name"]: r for r in matrix.describe(engine_id, specs)}


# ── the union is actually a union ────────────────────────────────────────────

def test_the_union_covers_parameters_from_every_engine():
    names = {s.name for s in matrix.union_specs()}
    # One from each adapter - if any engine drops out of the probe, the matrix
    # silently shrinks and stops being a matrix at all.
    assert {"kv_cache_len", "exaggeration", "length_penalty"} <= names


def test_a_variant_only_parameter_is_still_in_the_union():
    # Chatterbox only offers language_id on the multilingual variant; probing
    # the smaller one would hide a real parameter, which is the single thing
    # this feature exists to prevent.
    assert "language_id" in {s.name for s in matrix.union_specs()}


def test_every_engine_sees_the_whole_union():
    sizes = {e: len(rows_for(e)) for e in ("fish_s2", "xtts_v2", "chatterbox")}
    assert len(set(sizes.values())) == 1, sizes


# ── the three statuses ───────────────────────────────────────────────────────

def test_a_parameter_the_engine_really_has_is_editable():
    row = rows_for("fish_s2")["temperature"]
    assert row["editable"] and row["status"] == "supported"


def test_a_parameter_no_engine_of_this_kind_has_is_disabled_with_a_reason():
    row = rows_for("fish_s2")["exaggeration"]
    assert not row["editable"] and row["status"] == "unsupported"
    assert row["reason"]


def test_a_dead_parameter_says_it_is_dead_not_missing():
    """Fish declares repetition_penalty at every layer and never applies it.

    Calling that "unsupported" would be a lie in the direction of making us
    look tidy - and it would send somebody hunting for a model that "has" the
    setting when no such model would behave differently.
    """
    row = rows_for("fish_s2")["repetition_penalty"]
    assert not row["editable"]
    assert row["status"] == "dead"
    assert "never applies it" in row["reason"]


def test_the_same_parameter_is_live_on_an_engine_that_does_apply_it():
    row = rows_for("xtts_v2")["repetition_penalty"]
    assert row["editable"] and row["status"] == "supported"


# ── the app-level dial ───────────────────────────────────────────────────────

def test_reading_speed_is_shown_but_not_operable_in_the_model_panel():
    """Regression from the V9 audit.

    It used to render as an editable slider here - and the model save path
    validates against that ENGINE's parameters and drops unknown keys SILENTLY,
    so dragging it changed nothing and said nothing. A dial that moves and does
    nothing is the exact failure this matrix exists to prevent, so the row now
    says where the real control lives.
    """
    row = rows_for("fish_s2")["speed"]
    assert row["status"] == "app_level"
    assert row["editable"] is False
    assert "Delivery" in row["reason"]
    assert row["implemented_by"] == "elysium"


def test_saving_an_app_level_param_with_the_model_is_still_dropped():
    """The proof the row above is telling the truth."""
    adapter = adapter_for("fish_s2")
    kept = adapter.clamp_values(matrix._nominal("fish_s2"),
                                {"speed": 1.2, "temperature": 0.8})
    assert "speed" not in kept and kept["temperature"] == 0.8


def test_reading_speed_says_when_the_engine_itself_does_it():
    # The two paths genuinely sound different: a native rate is GENERATED at
    # that pace, a stretched one is reshaped afterwards.
    row = rows_for("xtts_v2")["speed"]
    assert row["status"] == "app_level"
    assert row["implemented_by"] == "engine"


def test_there_is_exactly_one_speed_row_on_an_engine_that_has_its_own():
    names = [r["name"] for r in
             matrix.describe("xtts_v2",
                             adapter_for("xtts_v2").describe_settings(
                                 matrix._nominal("xtts_v2")))]
    assert names.count("speed") == 1


def test_the_speed_row_advertises_the_apps_range_not_the_engines():
    row = rows_for("xtts_v2")["speed"]
    assert row["minimum"] == speed.MIN_RATE
    assert row["maximum"] == speed.MAX_RATE


# ── robustness ───────────────────────────────────────────────────────────────

def test_an_engine_only_parameter_survives_into_its_own_matrix():
    extra = ParamSpec("invented_knob", "float", 1.0, "Invented")
    rows = {r["name"]: r for r in matrix.describe("fish_s2", [extra])}
    assert rows["invented_knob"]["editable"]


def test_rows_are_ordered_by_group_so_the_panel_is_stable():
    order = [r["group"] for r in
             matrix.describe("fish_s2",
                             adapter_for("fish_s2").describe_settings(
                                 matrix._nominal("fish_s2")))]
    first_seen = []
    for group in order:
        if group not in first_seen:
            first_seen.append(group)
    assert first_seen == [g for g in matrix._GROUP_ORDER if g in first_seen]


# ── the endpoint ─────────────────────────────────────────────────────────────

def test_the_schema_endpoint_still_carries_the_engines_own_params(client,
                                                                  fish_model):
    body = client.get(f"/api/v1/tts/models/{fish_model}/schema").json()
    # `params` is what save/validate work from and must not become the union -
    # merging them would make the save path guess which knobs are real.
    own = {p["name"] for p in body["params"]}
    assert "exaggeration" not in own
    assert "temperature" in own


def test_the_schema_endpoint_exposes_the_matrix_too(client, fish_model):
    body = client.get(f"/api/v1/tts/models/{fish_model}/schema").json()
    rows = {r["name"]: r for r in body["matrix"]}
    assert rows["exaggeration"]["editable"] is False
    assert rows["temperature"]["editable"] is True
