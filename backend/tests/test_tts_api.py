"""V2 - /tts discovery + settings endpoints.

No GPU, no engine, no worker: these cover the host half only. Load/preflight
land in V3 with the worker.

The models root is repointed at a tmp dir per test (the registry reads
config.TTS_MODELS_DIR at call time, so no restart is needed) - the user's real
voice folder is never touched.
"""
import json

import pytest

import config
from tests.test_tts_core import make_chatterbox, make_fish, make_xtts


def _point_models_at(monkeypatch, tmp_path):
    root = tmp_path / "voice" / "models"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "TTS_MODELS_DIR", str(root), raising=False)
    return root


class TestModelListing:
    def test_empty_root_lists_nothing_and_is_not_an_error(self, client, monkeypatch, tmp_path):
        _point_models_at(monkeypatch, tmp_path)
        r = client.get("/api/v1/tts/models")
        assert r.status_code == 200
        body = r.json()
        assert body["models"] == [] and body["unrecognized"] == []
        assert body["roots"]

    def test_lists_detected_models(self, client, monkeypatch, tmp_path):
        root = _point_models_at(monkeypatch, tmp_path)
        make_fish(root)
        make_xtts(root)
        body = client.get("/api/v1/tts/models").json()
        engines = sorted(m["engine_id"] for m in body["models"])
        assert engines == ["fish_s2", "xtts_v2"]
        assert all(m["uid"] and not m["incomplete"] for m in body["models"])

    def test_unrecognized_folder_is_reported_not_hidden(self, client, monkeypatch, tmp_path):
        root = _point_models_at(monkeypatch, tmp_path)
        junk = root / "some-download"
        junk.mkdir(parents=True)
        (junk / "readme.txt").write_text("hi", encoding="utf-8")
        body = client.get("/api/v1/tts/models").json()
        assert body["models"] == []
        assert len(body["unrecognized"]) == 1

    def test_incomplete_model_is_flagged(self, client, monkeypatch, tmp_path):
        root = _point_models_at(monkeypatch, tmp_path)
        d = make_fish(root, "broken")
        (d / "codec.pth").unlink()
        m = client.get("/api/v1/tts/models").json()["models"][0]
        assert m["incomplete"] is True and m["missing"]

    def test_rescan_picks_up_a_newly_dropped_model(self, client, monkeypatch, tmp_path):
        root = _point_models_at(monkeypatch, tmp_path)
        assert client.get("/api/v1/tts/models").json()["models"] == []
        make_fish(root)                       # user drops a model while running
        body = client.post("/api/v1/tts/rescan").json()
        assert len(body["models"]) == 1


class TestSchema:
    def test_schema_is_per_model_and_renderable(self, client, monkeypatch, tmp_path):
        root = _point_models_at(monkeypatch, tmp_path)
        make_fish(root)
        uid = client.get("/api/v1/tts/models").json()["models"][0]["uid"]
        body = client.get(f"/api/v1/tts/models/{uid}/schema").json()
        assert body["engine_id"] == "fish_s2"
        assert body["capabilities"]["inline_prosody_tags"] is True
        names = [p["name"] for p in body["params"]]
        assert "temperature" in names and "language" in names
        for p in body["params"]:          # everything the UI needs to render
            assert p["type"] in ("float", "int", "bool", "enum", "text", "voice_ref")
            assert p["label"]

    def test_wire_shape_carries_everything_the_ui_renders(self, client, monkeypatch, tmp_path):
        """Deleting choices/step/advanced from the payload would silently make
        the settings page unrenderable while every other test still passed."""
        root = _point_models_at(monkeypatch, tmp_path)
        make_fish(root)
        uid = client.get("/api/v1/tts/models").json()["models"][0]["uid"]
        params = {p["name"]: p for p in
                  client.get(f"/api/v1/tts/models/{uid}/schema").json()["params"]}
        assert params["language"]["choices"], "enum with no choices cannot render"
        assert params["temperature"]["step"] is not None
        assert params["top_p"]["advanced"] is True
        assert params["temperature"]["help"]

    def test_unknown_uid_is_a_clean_error(self, client, monkeypatch, tmp_path):
        _point_models_at(monkeypatch, tmp_path)
        r = client.get("/api/v1/tts/models/deadbeef/schema")
        assert r.status_code == 400
        assert r.json()["detail"] == "tts_model_unknown"


class TestSettings:
    def _uid(self, client, root, maker=make_fish):
        maker(root)
        return client.get("/api/v1/tts/models").json()["models"][0]["uid"]

    def test_defaults_before_anything_is_saved(self, client, monkeypatch, tmp_path):
        root = _point_models_at(monkeypatch, tmp_path)
        uid = self._uid(client, root)
        body = client.get(f"/api/v1/tts/models/{uid}/settings").json()
        assert body["values"]["temperature"] == 0.7

    def test_save_clamps_and_returns_merged_values(self, client, monkeypatch, tmp_path):
        root = _point_models_at(monkeypatch, tmp_path)
        uid = self._uid(client, root)
        r = client.post(f"/api/v1/tts/models/{uid}/settings",
                        json={"values": {"temperature": 99, "top_k": 5}})
        assert r.status_code == 200
        vals = r.json()["values"]
        assert vals["temperature"] == 1.5      # clamped to the spec maximum
        assert vals["top_k"] == 5
        # persisted
        again = client.get(f"/api/v1/tts/models/{uid}/settings").json()["values"]
        assert again["temperature"] == 1.5

    def test_unknown_keys_are_dropped_not_stored(self, client, monkeypatch, tmp_path):
        root = _point_models_at(monkeypatch, tmp_path)
        uid = self._uid(client, root)
        r = client.post(f"/api/v1/tts/models/{uid}/settings",
                        json={"values": {"temperature": 0.8, "evil_kwarg": "rm -rf"}})
        # Absence alone proved nothing: an endpoint that dropped the whole
        # body, or refused the request outright, satisfied it too. The
        # legitimate key has to survive the same call.
        assert r.status_code == 200, r.text
        assert "evil_kwarg" not in r.json()["values"]
        assert r.json()["values"]["temperature"] == 0.8

    def test_bad_enum_is_rejected_with_its_code(self, client, monkeypatch, tmp_path):
        root = _point_models_at(monkeypatch, tmp_path)
        uid = self._uid(client, root)
        r = client.post(f"/api/v1/tts/models/{uid}/settings",
                        json={"values": {"language": "klingon"}})
        assert r.status_code == 400
        assert r.json()["detail"] == "tts_param_invalid"

    def test_partial_save_merges_with_previously_stored_values(self, client, monkeypatch, tmp_path):
        """Saving one field must not wipe the others - the settings page sends
        only what changed."""
        root = _point_models_at(monkeypatch, tmp_path)
        uid = self._uid(client, root)
        client.post(f"/api/v1/tts/models/{uid}/settings",
                    json={"values": {"temperature": 1.1}})
        client.post(f"/api/v1/tts/models/{uid}/settings",
                    json={"values": {"top_k": 11}})
        vals = client.get(f"/api/v1/tts/models/{uid}/settings").json()
        assert vals["values"]["temperature"] == 1.1      # survived the 2nd save
        assert vals["values"]["top_k"] == 11
        assert vals["source_map"]["temperature"] == "saved"
        assert vals["source_map"]["top_p"] == "default"

    def test_reset_restores_defaults(self, client, monkeypatch, tmp_path):
        root = _point_models_at(monkeypatch, tmp_path)
        uid = self._uid(client, root)
        client.post(f"/api/v1/tts/models/{uid}/settings",
                    json={"values": {"temperature": 1.2}})
        client.delete(f"/api/v1/tts/models/{uid}/settings")
        assert client.get(f"/api/v1/tts/models/{uid}/settings").json()["values"]["temperature"] == 0.7

    def test_corrupt_stored_json_is_tolerated(self, client, monkeypatch, tmp_path):
        """A mangled settings row must degrade to defaults, never 500."""
        from database import set_setting
        root = _point_models_at(monkeypatch, tmp_path)
        uid = self._uid(client, root)
        set_setting(f"tts_model:{uid}", "{ not json at all")
        r = client.get(f"/api/v1/tts/models/{uid}/settings")
        assert r.status_code == 200
        assert r.json()["values"]["temperature"] == 0.7

    def test_oversized_payload_is_refused(self, client, monkeypatch, tmp_path):
        root = _point_models_at(monkeypatch, tmp_path)
        uid = self._uid(client, root)
        r = client.post(f"/api/v1/tts/models/{uid}/settings",
                        json={"values": {"temperature": 0.7, "blob": "x" * 100_000}})
        assert r.status_code == 400
        assert r.json()["detail"] == "tts_values_too_large"


class TestActiveModel:
    def test_active_starts_empty(self, client, monkeypatch, tmp_path):
        _point_models_at(monkeypatch, tmp_path)
        body = client.get("/api/v1/tts/active").json()
        assert body["uid"] is None and body["state"] == "unloaded"

    def test_set_and_read_back(self, client, monkeypatch, tmp_path):
        root = _point_models_at(monkeypatch, tmp_path)
        make_chatterbox(root)
        uid = client.get("/api/v1/tts/models").json()["models"][0]["uid"]
        assert client.post("/api/v1/tts/active", json={"uid": uid}).status_code == 200
        body = client.get("/api/v1/tts/active").json()
        assert body["uid"] == uid and body["engine_id"] == "chatterbox"
        assert body["state"] == "unloaded"     # selecting does NOT load

    def test_setting_unknown_uid_fails(self, client, monkeypatch, tmp_path):
        _point_models_at(monkeypatch, tmp_path)
        r = client.post("/api/v1/tts/active", json={"uid": "nope"})
        assert r.status_code == 400 and r.json()["detail"] == "tts_model_unknown"

    def test_active_pointing_at_a_removed_model_degrades(self, client, monkeypatch, tmp_path):
        """User deletes the folder while it is selected - report, do not crash."""
        root = _point_models_at(monkeypatch, tmp_path)
        d = make_fish(root)
        uid = client.get("/api/v1/tts/models").json()["models"][0]["uid"]
        client.post("/api/v1/tts/active", json={"uid": uid})
        for p in d.iterdir():
            p.unlink()
        d.rmdir()
        body = client.get("/api/v1/tts/active").json()
        assert body["state"] == "error" and body["error_code"] == "tts_model_unknown"


class TestEngineOverride:
    def test_sidecar_override_is_written_and_applied(self, client, monkeypatch, tmp_path):
        root = _point_models_at(monkeypatch, tmp_path)
        d = make_fish(root, "mystery")
        uid = client.get("/api/v1/tts/models").json()["models"][0]["uid"]
        r = client.post(f"/api/v1/tts/models/{uid}/engine", json={"engine_id": "xtts_v2"})
        assert r.status_code == 200
        side = json.loads((d / "elysium-model.json").read_text(encoding="utf-8"))
        assert side["engine_id"] == "xtts_v2"
        listed = client.get("/api/v1/tts/models").json()["models"][0]
        assert listed["engine_id"] == "xtts_v2" and listed["source"] == "sidecar"

    def test_sidecar_write_refuses_to_follow_a_symlink(self, client, monkeypatch, tmp_path):
        """The only place voice writes into a user folder. A link planted in a
        downloaded archive must not redirect the write outside the models root."""
        import os
        import pytest
        root = _point_models_at(monkeypatch, tmp_path)
        d = make_fish(root, "linked")
        outside = tmp_path / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        try:
            os.symlink(outside, d / "elysium-model.json")
        except (OSError, NotImplementedError):
            pytest.skip("symlink creation needs privileges on this machine")
        uid = client.get("/api/v1/tts/models").json()["models"][0]["uid"]
        r = client.post(f"/api/v1/tts/models/{uid}/engine", json={"engine_id": "xtts_v2"})
        assert r.status_code == 400
        assert outside.read_text(encoding="utf-8") == "{}"   # untouched

    def test_unknown_engine_is_refused(self, client, monkeypatch, tmp_path):
        root = _point_models_at(monkeypatch, tmp_path)
        make_fish(root)
        uid = client.get("/api/v1/tts/models").json()["models"][0]["uid"]
        r = client.post(f"/api/v1/tts/models/{uid}/engine", json={"engine_id": "wav2bogus"})
        assert r.status_code == 400 and r.json()["detail"] == "tts_engine_unknown"


class TestPreflight:
    """The guard that refuses a load rather than letting it fill the card."""

    def _uid(self, client, root):
        make_fish(root)
        return client.get("/api/v1/tts/models").json()["models"][0]["uid"]

    def test_reports_a_full_fit_picture(self, client, monkeypatch, tmp_path):
        from tts import vram
        monkeypatch.setattr(
            vram, "_run_smi", lambda: "NVIDIA GeForce RTX 5080, 16303, 14000, 2303\n"
        )
        root = _point_models_at(monkeypatch, tmp_path)
        uid = self._uid(client, root)
        body = client.post("/api/v1/tts/preflight", json={"uid": uid, "values": {}}).json()
        for k in ("fits", "estimate_mb", "free_mb", "total_mb",
                  "used_by_others_mb", "headroom_mb", "gpu_available"):
            assert k in body
        assert body["gpu_available"] is True

    def test_refuses_when_the_card_is_busy(self, client, monkeypatch, tmp_path):
        """Exactly the situation that froze the machine mid-game: a game already
        holds most of the VRAM, so loading must be refused, not attempted."""
        from tts import vram
        monkeypatch.setattr(
            vram, "_run_smi", lambda: "NVIDIA GeForce RTX 5080, 16303, 2500, 13803\n"
        )
        root = _point_models_at(monkeypatch, tmp_path)
        uid = self._uid(client, root)
        body = client.post("/api/v1/tts/preflight", json={"uid": uid, "values": {}}).json()
        assert body["fits"] is False
        assert body["reason"] == "tts_insufficient_vram"
        assert body["used_by_others_mb"] == 13803

    def test_unknown_model_is_a_clean_error(self, client, monkeypatch, tmp_path):
        _point_models_at(monkeypatch, tmp_path)
        r = client.post("/api/v1/tts/preflight", json={"uid": "nope", "values": {}})
        assert r.status_code == 400 and r.json()["detail"] == "tts_model_unknown"


class TestRuntimes:
    def test_lists_every_engine_with_its_state(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(
            config, "TTS_RUNTIMES_PATH", str(tmp_path / "runtimes.json"), raising=False
        )
        body = client.get("/api/v1/tts/runtimes").json()
        ids = {r["engine_id"] for r in body["runtimes"]}
        assert {"fish_s2", "xtts_v2", "chatterbox"} <= ids
        assert all(r["state"] == "missing" for r in body["runtimes"])
        assert all(r["error_code"] == "tts_runtime_missing" for r in body["runtimes"])
        assert any(e["display_name"] for e in body["engines"])

    def test_a_registered_runtime_shows_ready(self, client, monkeypatch, tmp_path):
        from tts import runtimes
        monkeypatch.setattr(
            config, "TTS_RUNTIMES_PATH", str(tmp_path / "runtimes.json"), raising=False
        )
        exe = tmp_path / "python.exe"; exe.write_bytes(b"")
        runtimes.register("fish_s2", str(exe))
        body = client.get("/api/v1/tts/runtimes").json()
        fish = next(r for r in body["runtimes"] if r["engine_id"] == "fish_s2")
        assert fish["state"] == "ready" and fish["error_code"] is None


class TestReadinessOverTheWire:
    """A model is always inspectable; whether it can RUN is a separate, honest
    answer that travels with it. Both halves are asserted here.

    Deterministic on purpose: the GPU and the runtime registry are both faked,
    so these tests mean the same thing on a dev box with a provisioned runtime
    and a real card as they do anywhere else."""

    @pytest.fixture(autouse=True)
    def _hermetic(self, monkeypatch, tmp_path):
        from tts import vram

        monkeypatch.setattr(vram, "_run_smi", lambda: None)
        monkeypatch.setattr(config, "TTS_RUNTIMES_PATH",
                            str(tmp_path / "no-runtimes.json"), raising=False)

    def test_every_listed_model_carries_a_verdict(self, client, monkeypatch, tmp_path):
        make_fish(_point_models_at(monkeypatch, tmp_path))
        body = client.get("/api/v1/tts/models").json()
        assert body["models"], "fixture did not produce a model"
        for m in body["models"]:
            assert "readiness" in m
            assert set(m["readiness"]) >= {"runnable", "issues", "settings_available"}

    def test_settings_open_even_for_a_model_that_cannot_run(self, client, monkeypatch, tmp_path):
        """The requirement in one test: no GPU and no runtime in this
        environment, so it cannot run - and its settings page still works."""
        make_fish(_point_models_at(monkeypatch, tmp_path))
        uid = client.get("/api/v1/tts/models").json()["models"][0]["uid"]

        verdict = client.get(f"/api/v1/tts/models/{uid}/readiness").json()
        assert verdict["settings_available"] is True
        assert verdict["runnable"] is False
        assert verdict["issues"], "unrunnable must always say why"

        schema = client.get(f"/api/v1/tts/models/{uid}/schema")
        settings = client.get(f"/api/v1/tts/models/{uid}/settings")
        assert schema.status_code == 200 and schema.json()["params"]
        assert settings.status_code == 200

    def test_the_reasons_are_codes_the_frontend_knows(self, client, monkeypatch, tmp_path):
        from tts.errors import ALL_CODES

        make_fish(_point_models_at(monkeypatch, tmp_path))
        uid = client.get("/api/v1/tts/models").json()["models"][0]["uid"]
        codes = {i["code"] for i in
                 client.get(f"/api/v1/tts/models/{uid}/readiness").json()["issues"]}
        assert codes and codes <= ALL_CODES

    def test_the_selected_model_reports_why_voice_will_not_work(
        self, client, monkeypatch, tmp_path
    ):
        """The active model is the one the user expects to hear. Selecting it
        must not look like success when nothing can come out."""
        make_fish(_point_models_at(monkeypatch, tmp_path))
        uid = client.get("/api/v1/tts/models").json()["models"][0]["uid"]
        client.post("/api/v1/tts/active", json={"uid": uid})
        active = client.get("/api/v1/tts/active").json()
        assert active["readiness"]["runnable"] is False
        assert active["error_code"] in {i["code"] for i in active["readiness"]["issues"]}

    def test_readiness_of_an_unknown_uid_is_the_contract_code(self, client):
        r = client.get("/api/v1/tts/models/deadbeef/readiness")
        assert r.status_code == 400 and r.json()["detail"] == "tts_model_unknown"


# ── The chat has to tell "no voice here" from "voice here, none chosen" ─────
#
# Every in-chat voice control (SpeakButton, SpeakLiveButton,
# ContinuousVoiceToggle) renders nothing until a model is SELECTED. Right rule,
# but it collapsed two states: with no engine, silence is correct; with an
# engine installed, a model downloaded and a reference voice recorded and
# simply nothing selected, silence made the chat identical to a fresh install.


def test_active_reports_no_engine_installed(client):
    body = client.get("/api/v1/tts/active").json()
    assert body["uid"] is None
    assert body["voice_installed"] is False


def test_active_reports_an_installed_engine_with_nothing_selected(
    client, monkeypatch,
):
    import routers.tts as tts_router
    from tts import runtimes as tts_runtimes

    monkeypatch.setattr(
        tts_router.tts_runtimes, "status",
        lambda engine_id: tts_runtimes.RuntimeStatus(
            engine_id, "ready", "C:/env/python.exe", None,
        ),
    )
    body = client.get("/api/v1/tts/active").json()
    assert body["uid"] is None
    assert body["voice_installed"] is True


def test_a_broken_runtime_does_not_count_as_installed(
    client, monkeypatch,
):
    """Recorded once, gone now. Offering to "choose a voice" would be a dead
    end - the setup step is what is missing, not the selection."""
    import routers.tts as tts_router
    from tts import runtimes as tts_runtimes

    monkeypatch.setattr(
        tts_router.tts_runtimes, "status",
        lambda engine_id: tts_runtimes.RuntimeStatus(
            engine_id, "broken", "C:/gone/python.exe", "tts_runtime_broken",
        ),
    )
    assert client.get("/api/v1/tts/active").json()["voice_installed"] is False


def test_the_check_is_a_json_read_not_a_scan(client, monkeypatch):
    """It rides on /tts/active, which the chat polls."""
    import routers.tts as tts_router

    def explode(*a, **k):  # pragma: no cover - must not be reached
        raise AssertionError("a model scan on the /tts/active hot path")

    monkeypatch.setattr(tts_router, "scan_roots", explode)
    client.get("/api/v1/tts/active")
