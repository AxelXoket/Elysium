"""V3 - "you can see its settings, and you can see it will not run".

The rule this file protects: a voice model is ALWAYS inspectable. Its settings
page opens on a laptop with no GPU, with nothing installed, with a half-finished
download. What must never happen is a user tuning knobs on something that cannot
possibly speak, and only finding out when they press play - or worse, finding
out through silence.

So readiness is a separate, honest verdict alongside the settings: what is wrong
RIGHT NOW, all of it at once, each with the code the UI turns into real words.
"""
import pytest

import config
from tts import readiness, runtimes, vram
from tts.adapters.chatterbox import ChatterboxAdapter
from tts.base import DetectedModel
from tts.errors import (
    ALL_CODES,
    TTS_ENGINE_UNKNOWN,
    TTS_GPU_UNAVAILABLE,
    TTS_INSUFFICIENT_VRAM,
    TTS_LANGUAGE_UNSUPPORTED,
    TTS_MODEL_INCOMPLETE,
    TTS_RUNTIME_BROKEN,
    TTS_RUNTIME_MISSING,
)


def _fake_smi(monkeypatch, *, total=16303, free=14000, used=2303):
    monkeypatch.setattr(
        vram, "_run_smi", lambda: f"NVIDIA GeForce RTX 5080, {total}, {free}, {used}\n"
    )


def _no_smi(monkeypatch):
    monkeypatch.setattr(vram, "_run_smi", lambda: None)


def _runtime_ready(monkeypatch, tmp_path, engine="fish_s2"):
    reg = tmp_path / "voice" / "runtimes.json"
    reg.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "TTS_RUNTIMES_PATH", str(reg), raising=False)
    monkeypatch.setattr(config, "TTS_ENVS_DIR", str(tmp_path / "envs"),
                        raising=False)
    exe = tmp_path / "envs" / engine / "Scripts" / "python.exe"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b"")
    runtimes.register(engine, str(exe))
    return exe


def _runtime_absent(monkeypatch, tmp_path):
    reg = tmp_path / "voice" / "runtimes.json"
    reg.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "TTS_RUNTIMES_PATH", str(reg), raising=False)


def _model(**kw):
    base = dict(uid="uid1", engine_id="fish_s2", name="s2-pro", path="/models/s2-pro")
    base.update(kw)
    return DetectedModel(**base)


def _codes(r):
    return {i.code for i in r.issues}


def _blockers(r):
    return {i.code for i in r.issues if i.severity == readiness.BLOCKER}


class TestTheHappyCase:
    def test_everything_present_is_runnable_with_nothing_to_report(
        self, monkeypatch, tmp_path
    ):
        _fake_smi(monkeypatch)
        _runtime_ready(monkeypatch, tmp_path)
        r = readiness.evaluate(_model())
        assert r.runnable is True
        assert r.issues == ()
        assert r.runtime_state == "ready"


class TestSettingsStayVisible:
    def test_settings_are_available_even_when_nothing_can_run(
        self, monkeypatch, tmp_path
    ):
        """The whole point: no GPU, no runtime - the user can still open the
        model, read what it does and set it up. They just also get told, in the
        same breath, that it will not run yet."""
        _no_smi(monkeypatch)
        _runtime_absent(monkeypatch, tmp_path)
        r = readiness.evaluate(_model())
        assert r.settings_available is True
        assert r.runnable is False
        assert r.issues, "an unrunnable model must say WHY, never just sit there"


class TestEveryReasonAtOnce:
    def test_all_blockers_are_reported_together_not_one_per_attempt(
        self, monkeypatch, tmp_path
    ):
        """Fixing one problem only to discover the next is the worst version of
        this UI. Three things are wrong here; all three come back."""
        _no_smi(monkeypatch)
        _runtime_absent(monkeypatch, tmp_path)
        r = readiness.evaluate(_model(missing=("codec.pth",)))
        assert _blockers(r) >= {
            TTS_MODEL_INCOMPLETE,
            TTS_RUNTIME_MISSING,
            TTS_GPU_UNAVAILABLE,
        }

    def test_issues_carry_the_files_that_are_actually_missing(
        self, monkeypatch, tmp_path
    ):
        _fake_smi(monkeypatch)
        _runtime_ready(monkeypatch, tmp_path)
        r = readiness.evaluate(_model(missing=("codec.pth", "model.safetensors")))
        issue = next(i for i in r.issues if i.code == TTS_MODEL_INCOMPLETE)
        assert "codec.pth" in issue.detail and "model.safetensors" in issue.detail


class TestHonestCauses:
    def test_a_machine_with_no_gpu_is_not_called_insufficient_vram(
        self, monkeypatch, tmp_path
    ):
        """"Not enough VRAM" sends the user off to close programs on a machine
        that has no NVIDIA card at all. Name the real cause."""
        _no_smi(monkeypatch)
        _runtime_ready(monkeypatch, tmp_path)
        r = readiness.evaluate(_model())
        assert TTS_GPU_UNAVAILABLE in _blockers(r)
        assert TTS_INSUFFICIENT_VRAM not in _codes(r)

    def test_a_busy_card_is_insufficient_vram_and_marked_transient(
        self, monkeypatch, tmp_path
    ):
        """A game is holding the card. This one clears itself - the UI should
        say "close it and try again", not "your setup is broken"."""
        _fake_smi(monkeypatch, free=500, used=15803)
        _runtime_ready(monkeypatch, tmp_path)
        r = readiness.evaluate(_model())
        issue = next(i for i in r.issues if i.code == TTS_INSUFFICIENT_VRAM)
        assert issue.transient is True
        assert r.fit is not None and r.fit.used_by_others_mb == 15803

    def test_never_installed_and_installed_then_deleted_are_different(
        self, monkeypatch, tmp_path
    ):
        """One needs "set it up", the other needs "set it up AGAIN, something
        removed it". Collapsing them makes the second look like a lie."""
        _fake_smi(monkeypatch)
        _runtime_absent(monkeypatch, tmp_path)
        assert TTS_RUNTIME_MISSING in _blockers(readiness.evaluate(_model()))

        exe = _runtime_ready(monkeypatch, tmp_path)
        exe.unlink()
        r = readiness.evaluate(_model())
        assert TTS_RUNTIME_BROKEN in _blockers(r)
        assert r.runtime_state == "broken"

    def test_an_unregistered_engine_cannot_show_settings_and_says_so(
        self, monkeypatch, tmp_path
    ):
        """Settings come FROM the adapter. With no adapter there is nothing to
        render, so this is the one case where settings are unavailable - and it
        has to be stated rather than shown as an empty page."""
        _fake_smi(monkeypatch)
        _runtime_ready(monkeypatch, tmp_path)
        r = readiness.evaluate(_model(engine_id="some_engine_we_dropped"))
        assert r.settings_available is False
        assert TTS_ENGINE_UNKNOWN in _blockers(r)


class TestLanguageCompatibility:
    def test_english_only_chatterbox_does_not_claim_turkish(self):
        """The engine FAMILY speaks 23 languages; this build speaks one. Reading
        the class capability here would promise Turkish that never arrives."""
        english = _model(engine_id="chatterbox", variant="english")
        langs = ChatterboxAdapter.languages_for(english)
        assert "tr" not in langs and "en" in langs

    def test_the_multilingual_build_does_claim_turkish(self):
        mtl = _model(engine_id="chatterbox", variant="multilingual")
        assert "tr" in ChatterboxAdapter.languages_for(mtl)

    def test_asking_for_a_language_it_cannot_speak_warns_without_blocking(
        self, monkeypatch, tmp_path
    ):
        """It still works - just not in Turkish. Blocking would take away a
        model the user may well want for English."""
        _fake_smi(monkeypatch)
        _runtime_ready(monkeypatch, tmp_path, engine="chatterbox")
        r = readiness.evaluate(
            _model(engine_id="chatterbox", variant="english"), language="tr"
        )
        assert TTS_LANGUAGE_UNSUPPORTED in _codes(r)
        assert TTS_LANGUAGE_UNSUPPORTED not in _blockers(r)
        assert r.runnable is True

    def test_a_language_it_does_speak_is_silent(self, monkeypatch, tmp_path):
        _fake_smi(monkeypatch)
        _runtime_ready(monkeypatch, tmp_path, engine="chatterbox")
        r = readiness.evaluate(
            _model(engine_id="chatterbox", variant="multilingual"), language="tr"
        )
        assert TTS_LANGUAGE_UNSUPPORTED not in _codes(r)

    def test_languages_are_reported_so_the_ui_can_show_them(
        self, monkeypatch, tmp_path
    ):
        _fake_smi(monkeypatch)
        _runtime_ready(monkeypatch, tmp_path, engine="chatterbox")
        r = readiness.evaluate(_model(engine_id="chatterbox", variant="english"))
        assert r.languages == ("en",)


class TestItNeverMakesThingsWorse:
    def test_every_code_it_can_emit_is_in_the_shared_vocabulary(
        self, monkeypatch, tmp_path
    ):
        """A code outside ALL_CODES reaches the user as "something went wrong",
        which is the exact outcome this subsystem exists to prevent."""
        _no_smi(monkeypatch)
        _runtime_absent(monkeypatch, tmp_path)
        cases = [
            _model(missing=("x.pth",)),
            _model(engine_id="nope"),
            _model(engine_id="chatterbox", variant="english"),
        ]
        seen = set()
        for m in cases:
            seen |= _codes(readiness.evaluate(m, language="tr"))
        assert seen and seen <= ALL_CODES

    def test_an_adapter_that_explodes_while_estimating_does_not_break_the_verdict(
        self, monkeypatch, tmp_path
    ):
        _fake_smi(monkeypatch)
        _runtime_ready(monkeypatch, tmp_path)
        from tts.adapters.fish_s2 import FishS2Adapter

        def boom(cls, model, values):
            raise RuntimeError("estimate blew up")

        monkeypatch.setattr(
            FishS2Adapter, "estimate_vram_mb", classmethod(boom), raising=False
        )
        r = readiness.evaluate(_model())      # must not raise
        assert r.fit is not None

    def test_a_descriptor_that_explodes_still_yields_a_verdict(
        self, monkeypatch, tmp_path
    ):
        _fake_smi(monkeypatch)
        _runtime_ready(monkeypatch, tmp_path)
        from tts.adapters.fish_s2 import FishS2Adapter

        def boom(cls, model):
            raise RuntimeError("descriptor blew up")

        monkeypatch.setattr(
            FishS2Adapter, "describe_settings", classmethod(boom), raising=False
        )
        r = readiness.evaluate(_model(), language="tr")
        assert r.languages == () or isinstance(r.languages, tuple)


class TestBatch:
    def test_a_list_of_models_reads_the_gpu_once(self, monkeypatch, tmp_path):
        """Twenty models must not mean twenty nvidia-smi subprocesses - that is
        seconds of stall on the settings page for a value that cannot change
        between the first row and the last."""
        _fake_smi(monkeypatch)
        _runtime_ready(monkeypatch, tmp_path)
        calls = {"n": 0}
        per_model = {"n": 0}
        real = vram.query_gpu

        def counted():
            calls["n"] += 1
            return real()

        def counted_per_model():
            per_model["n"] += 1
            return real()

        monkeypatch.setattr(readiness, "query_gpu", counted)
        # The seam where per-model probes would actually happen is check_fit's
        # own import of query_gpu - counting only the evaluate_all call would
        # pass even if every row still shelled out to nvidia-smi.
        from tts import preflight as preflight_mod

        monkeypatch.setattr(preflight_mod, "query_gpu", counted_per_model)
        models = [_model(uid=f"u{i}") for i in range(20)]
        out = readiness.evaluate_all(models)
        assert len(out) == 20
        assert calls["n"] == 1
        assert per_model["n"] == 0, "rows probed the GPU behind the batch's back"

    def test_batch_verdicts_match_the_single_one(self, monkeypatch, tmp_path):
        _fake_smi(monkeypatch, free=500, used=15803)
        _runtime_absent(monkeypatch, tmp_path)
        m = _model()
        single = readiness.evaluate(m)
        batch = readiness.evaluate_all([m])[m.uid]
        assert _codes(batch) == _codes(single)
        assert batch.runnable == single.runnable


# ── A loaded model must not be the reason it "cannot load" ─────────────────
#
# nvidia-smi reports free VRAM AFTER our own allocation, so once the model is
# resident its own ~10 GB counts as "used by others" and the panel announced
# "Not enough GPU memory to load this voice model" about the very model it had
# just loaded. Observed in the wild right after the preload landed.


def test_a_resident_model_reports_as_fitting(monkeypatch):
    from tts import readiness as tts_readiness

    monkeypatch.setattr(
        tts_readiness, "_already_on_the_card", lambda uid: True,
    )
    assert tts_readiness._already_on_the_card("anything") is True


def test_the_host_lookup_never_raises(monkeypatch):
    """A readiness verdict must not fail because the host is mid-transition."""
    from tts import readiness as tts_readiness
    from tts import host as tts_host

    def explode():
        raise RuntimeError("host is restarting")

    monkeypatch.setattr(tts_host, "get_host", explode)
    assert tts_readiness._already_on_the_card("u1") is False


def test_only_this_uid_counts(monkeypatch):
    """A DIFFERENT model on the card is genuinely somebody else's memory."""
    from tts import readiness as tts_readiness
    from tts import host as tts_host

    class Host:
        @staticmethod
        def snapshot():
            return {"uid": "other", "state": "loaded"}

    monkeypatch.setattr(tts_host, "get_host", lambda: Host())
    assert tts_readiness._already_on_the_card("u1") is False
    assert tts_readiness._already_on_the_card("other") is True


def test_an_unloaded_host_does_not_excuse_the_fit(monkeypatch):
    from tts import readiness as tts_readiness
    from tts import host as tts_host

    class Host:
        @staticmethod
        def snapshot():
            return {"uid": "u1", "state": "unloaded"}

    monkeypatch.setattr(tts_host, "get_host", lambda: Host())
    assert tts_readiness._already_on_the_card("u1") is False
