"""Audit KÖK 14 + 15, backend half.

Two families, one shape between them: something already knew the right answer
and a second surface either restated it wrongly or never asked.
"""

from __future__ import annotations

import pytest

from test_notice_channel import voice  # noqa: F401

from tts import preflight, readiness, registry
from tts.errors import TTS_GPU_UNAVAILABLE, TTS_INSUFFICIENT_VRAM
from tts.base import DetectedModel
from tts.vram import GpuInfo


# ---------------------------------------------------------------------------
# KÖK 14: a fit check that never looked at the GPU said there was no GPU
# ---------------------------------------------------------------------------

def _model(engine_id: str = "no_such_engine") -> DetectedModel:
    return DetectedModel(uid="u", engine_id=engine_id, name="m", path="/m")


def test_a_model_with_no_estimate_still_reports_the_real_card(monkeypatch):
    """The reported failure: an unestimatable model on a working RTX 5080 was
    described as "no readable NVIDIA GPU on this machine", and the fit panel
    showed 0 MB / 0 MB on a 16 GB board. Refusing is right; the machine
    description was invented."""
    gpu = GpuInfo(name="RTX 5080", total_mb=16303, free_mb=14000, used_mb=2303)
    fit = preflight.check_fit(_model(), {}, gpu=gpu)

    assert fit.fits is False
    assert fit.reason == TTS_INSUFFICIENT_VRAM, "the reason is the estimate"
    assert fit.gpu_available is True, "the card is right there"
    assert (fit.free_mb, fit.total_mb) == (14000, 16303)


def test_a_machine_with_no_card_still_says_so(monkeypatch):
    """The other branch must keep its own answer."""
    monkeypatch.setattr(preflight, "query_gpu", lambda: None)
    fit = preflight.check_fit(_model("fish_s2"), {}, probe=True)
    assert fit.gpu_available is False
    assert fit.reason == TTS_GPU_UNAVAILABLE


def test_readiness_repeats_the_reason_instead_of_re_deriving_it(monkeypatch):
    """evaluate threw fit.reason away and guessed from gpu_available, so the
    two surfaces reading the same FitResult (this one and host.py) disagreed
    about the same machine."""
    issues = _issues_for(monkeypatch, preflight.FitResult(
        fits=False, estimate_mb=0, free_mb=14000, total_mb=16303,
        used_by_others_mb=2303, headroom_mb=1024, gpu_available=True,
        reason=TTS_INSUFFICIENT_VRAM, detail="no VRAM estimate",
    ))
    assert TTS_INSUFFICIENT_VRAM in issues
    assert TTS_GPU_UNAVAILABLE not in issues


def test_a_genuinely_absent_gpu_is_still_reported_as_one(monkeypatch):
    issues = _issues_for(monkeypatch, preflight.FitResult(
        fits=False, estimate_mb=900, free_mb=0, total_mb=0,
        used_by_others_mb=0, headroom_mb=1024, gpu_available=False,
        reason=TTS_GPU_UNAVAILABLE, detail="no NVIDIA GPU reading available",
    ))
    assert TTS_GPU_UNAVAILABLE in issues
    assert TTS_INSUFFICIENT_VRAM not in issues


def _issues_for(monkeypatch, fit) -> set[str]:
    """The codes readiness.evaluate raises for this fit verdict.

    check_fit is stubbed rather than driven through a real GPU probe: the
    question here is only what evaluate does with the answer it is given.
    """
    monkeypatch.setattr(readiness, "check_fit", lambda *a, **k: fit)
    adapter = registry.all_adapters()[0]
    model = DetectedModel(uid="u", engine_id=adapter.engine_id, name="m", path="/m")
    verdict = readiness.evaluate(model, values={}, gpu=None, probe_gpu=False)
    return {issue.code for issue in verdict.issues}


# ---------------------------------------------------------------------------
# KÖK 15: one uid for two different models
# ---------------------------------------------------------------------------

def test_two_roots_with_the_same_folder_name_no_longer_collide(tmp_path):
    """Verified in the audit: two `velvet` folders under different roots hashed
    to ONE uid. _resolve returned whichever came first (loading the wrong
    model), evaluate_all overwrote one verdict with the other, the two shared
    their saved settings, and React rendered a duplicate key."""
    a, b = tmp_path / "a", tmp_path / "b"
    (a / "velvet").mkdir(parents=True)
    (b / "velvet").mkdir(parents=True)

    uid_a = registry._uid_for(a, a / "velvet")
    uid_b = registry._uid_for(b, b / "velvet")
    assert uid_a != uid_b


def test_the_same_folder_still_keeps_one_uid(tmp_path):
    """The property the scheme exists for: identity is WHERE it sits, and that
    must not wobble between two reads of the same place."""
    root = tmp_path / "models"
    (root / "velvet").mkdir(parents=True)
    assert registry._uid_for(root, root / "velvet") == registry._uid_for(
        root, root / "velvet",
    )


def test_case_still_does_not_split_one_folder_in_two():
    """Windows paths are case-insensitive, so two spellings of one folder must
    not become two models. Asserted on the digest rather than through the
    filesystem: on a case-SENSITIVE runner the two paths really are different
    folders, and the test would be asserting the platform, not the code."""
    assert registry._digest("R", "Velvet") == registry._digest("R", "velvet")
    assert registry._digest("Root", "v") == registry._digest("root", "v")


def test_a_selection_saved_under_the_old_scheme_is_migrated_once(
    client, voice, monkeypatch,
):
    """Changing the id scheme must not silently turn the user's chosen voice
    into "no model selected"."""
    import sys
    import routers.tts_runtime as runtime
    from database import get_setting, set_setting
    from tts import runtimes

    runtimes.register("fish_s2", sys.executable)
    models = runtime.scan_roots().models
    assert models, "the voice fixture stood up no models"
    model = models[0]
    assert model.legacy_uid and model.legacy_uid != model.uid

    set_setting(runtime.SETTING_ACTIVE_UID, model.legacy_uid)
    resolved = runtime._resolve(None)

    assert resolved.uid == model.uid
    assert get_setting(runtime.SETTING_ACTIVE_UID) == model.uid, (
        "the migration has to be written down, or it runs on every call"
    )


def test_an_id_that_matches_nothing_is_still_an_error(client, voice):
    import routers.tts_runtime as runtime
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        runtime._resolve("deadbeefdeadbeef")
    assert exc.value.detail == "tts_model_unknown"


# ---------------------------------------------------------------------------
# KÖK 15: naming a proxy that is already configured
# ---------------------------------------------------------------------------

def test_the_proxy_can_be_renamed_without_retyping_its_url(client):
    """The URL is write-only and never displayed, and the alias could only ride
    along with a full proxy write - so naming an existing proxy was not hard,
    it was impossible."""
    client.post("/api/v1/settings/proxy", json={
        "proxy_url": "http://user:pw@127.0.0.1:8888",
        "proxy_required": False,
        "proxy_alias": "Home",
    })
    assert client.get("/api/v1/settings").json()["proxy_alias"] == "Home"

    r = client.post("/api/v1/settings/proxy/alias", json={"proxy_alias": "Work"})
    assert r.status_code == 200
    assert client.get("/api/v1/settings").json()["proxy_alias"] == "Work"
    # And the proxy itself is untouched - the point of a separate write.
    assert client.get("/api/v1/settings").json()["proxy_configured"] is True


def test_the_alias_can_be_cleared(client):
    client.post("/api/v1/settings/proxy", json={
        "proxy_url": "http://127.0.0.1:8888",
        "proxy_required": False,
        "proxy_alias": "Home",
    })
    client.post("/api/v1/settings/proxy/alias", json={"proxy_alias": ""})
    assert client.get("/api/v1/settings").json()["proxy_alias"] is None


def test_naming_a_proxy_that_does_not_exist_is_refused(client):
    """A label for a thing that is not there would render as a proxy that is
    not there."""
    r = client.post("/api/v1/settings/proxy/alias", json={"proxy_alias": "Ghost"})
    assert r.status_code == 400
    assert r.json()["detail"] == "proxy_url_required"
