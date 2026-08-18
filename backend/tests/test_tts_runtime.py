"""V3 core - GPU query, fit preflight, and the app-owned runtime registry.

Everything here runs with NO GPU and NO engine installed: nvidia-smi is faked,
so the logic that decides "this will not fit, refuse to load" is testable on any
machine. That decision is the one that protects the user's session - a model
that fills the card makes the whole desktop crawl instead of failing cleanly.

The runtime registry is deliberately APP-OWNED: runtimes.json is written by the
app, never hand-edited by the user, so these tests treat it as an internal
artefact with a state machine (missing -> installing -> ready | broken).
"""
import json
import os

import pytest

import config
from tts import runtimes, vram
from tts.base import DetectedModel
from tts.errors import (
    TTS_GPU_UNAVAILABLE,
    TTS_INSUFFICIENT_VRAM,
    TTS_RUNTIME_BROKEN,
    TTS_RUNTIME_MISSING,
)
from tts.preflight import check_fit


def _fake_smi(monkeypatch, *, total=16303, free=14000, used=2303, name="NVIDIA GeForce RTX 5080"):
    """Stand in for the nvidia-smi CSV line, without a GPU."""
    line = f"{name}, {total}, {free}, {used}\n"
    monkeypatch.setattr(vram, "_run_smi", lambda: line)


def _no_smi(monkeypatch):
    monkeypatch.setattr(vram, "_run_smi", lambda: None)


class TestGpuQuery:
    def test_parses_a_real_smi_line(self, monkeypatch):
        _fake_smi(monkeypatch)
        gpu = vram.query_gpu()
        assert gpu is not None
        assert gpu.name.startswith("NVIDIA")
        assert gpu.total_mb == 16303 and gpu.free_mb == 14000

    def test_absent_nvidia_smi_is_not_an_error(self, monkeypatch):
        """An integrated-graphics machine must still be able to browse models."""
        _no_smi(monkeypatch)
        assert vram.query_gpu() is None

    def test_garbage_output_is_swallowed(self, monkeypatch):
        monkeypatch.setattr(vram, "_run_smi", lambda: "not a csv at all")
        assert vram.query_gpu() is None

    def test_query_never_raises(self, monkeypatch):
        def boom():
            raise OSError("nvidia-smi exploded")
        monkeypatch.setattr(vram, "_run_smi", boom)
        assert vram.query_gpu() is None


class TestFitCheck:
    def _model(self):
        return DetectedModel("uid1", "fish_s2", "s2-pro", "/models/s2-pro")

    def test_fits_when_there_is_room_plus_headroom(self, monkeypatch):
        _fake_smi(monkeypatch, free=14000)
        fit = check_fit(self._model(), {})
        assert fit.fits is True
        assert fit.estimate_mb > 0 and fit.free_mb == 14000
        assert fit.headroom_mb == config.TTS_VRAM_HEADROOM_MB

    def test_refuses_when_the_estimate_plus_headroom_exceeds_free(self, monkeypatch):
        _fake_smi(monkeypatch, free=4000)          # Fish needs ~10 GB
        fit = check_fit(self._model(), {})
        assert fit.fits is False
        assert fit.reason == TTS_INSUFFICIENT_VRAM

    def test_headroom_is_what_makes_a_borderline_case_fail(self, monkeypatch):
        """The margin is the whole point: a model that technically fits but
        leaves nothing behind is what froze the desktop mid-game."""
        model = self._model()
        _fake_smi(monkeypatch, free=99999)
        estimate = check_fit(model, {}).estimate_mb
        _fake_smi(monkeypatch, free=estimate + 10)      # fits, no margin
        assert check_fit(model, {}).fits is False
        _fake_smi(monkeypatch, free=estimate + config.TTS_VRAM_HEADROOM_MB + 10)
        assert check_fit(model, {}).fits is True

    def test_other_apps_holding_vram_are_reported(self, monkeypatch):
        _fake_smi(monkeypatch, total=16303, free=6000, used=10303)
        fit = check_fit(self._model(), {})
        assert fit.used_by_others_mb == 10303
        assert fit.gpu_available is True

    def test_without_a_gpu_it_reports_instead_of_guessing(self, monkeypatch):
        _no_smi(monkeypatch)
        fit = check_fit(self._model(), {})
        assert fit.gpu_available is False
        # Still refuses - but as "no GPU", not "not enough VRAM". The latter
        # sends someone off closing programs on a machine with no NVIDIA card.
        assert fit.fits is False and fit.reason == TTS_GPU_UNAVAILABLE
        assert fit.reason != TTS_INSUFFICIENT_VRAM

    def test_settings_change_the_estimate(self, monkeypatch):
        """A bigger KV cache costs VRAM; preflight must see the user's values."""
        _fake_smi(monkeypatch)
        small = check_fit(self._model(), {"kv_cache_len": 2048}).estimate_mb
        big = check_fit(self._model(), {"kv_cache_len": 8192}).estimate_mb
        assert big > small


class TestRuntimeRegistry:
    def _point_at(self, monkeypatch, tmp_path):
        p = tmp_path / "voice" / "runtimes.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(config, "TTS_RUNTIMES_PATH", str(p), raising=False)
        return p

    def test_absent_registry_reports_missing_not_error(self, monkeypatch, tmp_path):
        self._point_at(monkeypatch, tmp_path)
        st = runtimes.status("fish_s2")
        assert st.state == "missing" and st.error_code == TTS_RUNTIME_MISSING

    def test_register_then_read_back(self, monkeypatch, tmp_path):
        self._point_at(monkeypatch, tmp_path)
        exe = tmp_path / "env" / "python.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"")
        runtimes.register("fish_s2", str(exe))
        st = runtimes.status("fish_s2")
        assert st.state == "ready" and st.python == str(exe)

    def test_registry_pointing_at_a_deleted_interpreter_is_broken_not_ready(
        self, monkeypatch, tmp_path
    ):
        """The user cleaned their disk. Say so; do not try to spawn a ghost."""
        self._point_at(monkeypatch, tmp_path)
        exe = tmp_path / "env" / "python.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"")
        runtimes.register("fish_s2", str(exe))
        exe.unlink()
        st = runtimes.status("fish_s2")
        # NOT "missing": "set it up" and "set it up again, something removed
        # it" are different sentences, and only one of them is true here.
        assert st.state == "broken" and st.error_code == TTS_RUNTIME_BROKEN

    def test_corrupt_registry_degrades_to_missing(self, monkeypatch, tmp_path):
        p = self._point_at(monkeypatch, tmp_path)
        p.write_text("{ not json", encoding="utf-8")
        assert runtimes.status("fish_s2").state == "missing"

    def test_unregister_removes_only_that_engine(self, monkeypatch, tmp_path):
        self._point_at(monkeypatch, tmp_path)
        a = tmp_path / "a.exe"; a.write_bytes(b"")
        b = tmp_path / "b.exe"; b.write_bytes(b"")
        runtimes.register("fish_s2", str(a))
        runtimes.register("xtts_v2", str(b))
        runtimes.unregister("fish_s2")
        assert runtimes.status("fish_s2").state == "missing"
        assert runtimes.status("xtts_v2").state == "ready"

    def test_registry_write_is_atomic_enough_to_survive_a_reread(
        self, monkeypatch, tmp_path
    ):
        p = self._point_at(monkeypatch, tmp_path)
        exe = tmp_path / "x.exe"; exe.write_bytes(b"")
        runtimes.register("fish_s2", str(exe))
        data = json.loads(p.read_text(encoding="utf-8"))
        assert "engines" in data and "fish_s2" in data["engines"]

    def test_the_bytes_are_on_the_disk_before_the_name_points_at_them(
        self, monkeypatch, tmp_path
    ):
        """K-51. `_save` promises a crash cannot leave a half-written registry.

        `os.replace` only reorders a directory entry - it says nothing about
        the bytes behind it. So the promise is kept by the flush+fsync, and
        only if that happens BEFORE the rename; afterwards it is decoration.

        The cost of getting this wrong is not an unreadable file, it is a
        sentence: a truncated registry reads as `{}`, the app says voice was
        never set up, and the user is sent to re-download gigabytes of models
        that are still sitting on their disk.

        Three things are asserted, so three different mistakes are caught:
        that a sync happened at all, that it happened before the rename, and
        that at the moment it happened the file already held the whole
        registry (a sync of an unflushed buffer syncs nothing).
        """
        p = self._point_at(monkeypatch, tmp_path)
        exe = tmp_path / "x.exe"; exe.write_bytes(b"")

        events: list[tuple[str, int]] = []
        real_fsync, real_replace = os.fsync, os.replace

        def watched_fsync(fd):
            # Size THROUGH THE SAME DESCRIPTOR: this is the file the call is
            # actually durable-ing, not some other file that happens to exist.
            events.append(("fsync", os.fstat(fd).st_size))
            return real_fsync(fd)

        def watched_replace(src, dst):
            events.append(("replace", 0))
            return real_replace(src, dst)

        monkeypatch.setattr(os, "fsync", watched_fsync)
        monkeypatch.setattr(os, "replace", watched_replace)
        runtimes.register("fish_s2", str(exe))

        names = [name for name, _ in events]
        assert "fsync" in names, "the registry was renamed into place unsynced"
        assert names.index("fsync") < names.index("replace")
        synced_bytes = next(size for name, size in events if name == "fsync")
        assert synced_bytes == p.stat().st_size, (
            "the sync ran while the registry was still in a buffer"
        )

    def test_extra_roots_are_read_from_the_registry_on_every_call(
        self, monkeypatch, tmp_path
    ):
        """Adding a models root must not require an app restart (ComfyUI's
        parse-once-at-boot behaviour is a documented anti-pattern)."""
        p = self._point_at(monkeypatch, tmp_path)
        assert runtimes.extra_roots() == []
        p.write_text(json.dumps({"engines": {}, "extra_roots": [str(tmp_path / "more")]}),
                     encoding="utf-8")
        assert runtimes.extra_roots() == [str(tmp_path / "more")]
