"""V3 core - GPU query, fit preflight, and the app-owned runtime registry.

Everything here runs with NO GPU and NO engine installed: nvidia-smi is faked,
so the logic that decides "this will not fit, refuse to load" is testable on any
machine. That decision is the one that protects the user's session - a model
that fills the card makes the whole desktop crawl instead of failing cleanly.

The runtime registry is deliberately APP-OWNED: runtimes.json is written by the
app, never hand-edited by the user, so these tests treat it as an internal
artefact with a state machine (missing -> installing -> ready | broken).
"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import hashlib

import pytest

import config
from tts import runtimes, vram
from tts.base import DetectedModel
from tts.errors import (
    TTS_GPU_UNAVAILABLE,
    TTS_INSUFFICIENT_VRAM,
    TTS_RUNTIME_BROKEN,
    TTS_RUNTIME_MISSING,
    TTS_RUNTIME_UNTRUSTED,
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
        # The confinement check reads config.TTS_ENVS_DIR at call time, so a
        # test that plants an interpreter has to say where this run's install
        # folder is, exactly as a real install does.
        monkeypatch.setattr(config, "TTS_ENVS_DIR",
                            str(tmp_path / "envs"), raising=False)
        return p

    def test_absent_registry_reports_missing_not_error(self, monkeypatch, tmp_path):
        self._point_at(monkeypatch, tmp_path)
        st = runtimes.status("fish_s2")
        assert st.state == "missing" and st.error_code == TTS_RUNTIME_MISSING

    def test_register_then_read_back(self, monkeypatch, tmp_path):
        self._point_at(monkeypatch, tmp_path)
        exe = tmp_path / "envs" / "fish_s2" / "Scripts" / "python.exe"
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
        exe = tmp_path / "envs" / "fish_s2" / "Scripts" / "python.exe"
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
        # Named and placed the way a real install is. The registry now
        # refuses an interpreter that is not under the folder this app
        # installs into and not called what env_python composes, so a loose
        # a.exe beside the registry is exactly what it is written to reject.
        a = tmp_path / "envs" / "fish_s2" / "Scripts" / "python.exe"
        b = tmp_path / "envs" / "xtts_v2" / "Scripts" / "python.exe"
        for exe in (a, b):
            exe.parent.mkdir(parents=True, exist_ok=True)
            exe.write_bytes(b"")
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


class TestTheRecordedInterpreterIsNotRunOnTrust:
    """runtimes.json names a program and the app runs it.

    Any process running as this user can write that file with one open() and
    no elevation, so until 20 August 2026 the file was a plain code execution
    primitive: the only check was is_file(). These tests are the check.

    What is NOT claimed here, and the honest limit belongs beside the tests
    that could be read as claiming it: an attacker who can write inside the
    install folder can replace the interpreter AND rewrite the digest recorded
    beside it, and wins. Closing that needs a key they cannot read, and the
    only such key is the vault key, which is not available when this file is
    written because an engine install is deliberately allowed to outlive the
    vault lock. This removes the single-write attack, not the class.
    """

    def _registry(self, monkeypatch, tmp_path):
        reg = tmp_path / "voice" / "runtimes.json"
        reg.parent.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(config, "TTS_RUNTIMES_PATH", str(reg),
                            raising=False)
        monkeypatch.setattr(config, "TTS_ENVS_DIR", str(tmp_path / "envs"),
                            raising=False)
        return reg

    def _installed(self, tmp_path, engine="fish_s2", body=b"an interpreter"):
        exe = tmp_path / "envs" / engine / "Scripts" / "python.exe"
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_bytes(body)
        return exe

    def test_ground_a_normally_installed_interpreter_is_ready(
        self, monkeypatch, tmp_path
    ):
        # GROUND. Without this every refusal below would also pass for a check
        # that refused everything, which would ship an app whose voice never
        # starts.
        self._registry(monkeypatch, tmp_path)
        exe = self._installed(tmp_path)
        runtimes.register("fish_s2", str(exe))
        assert runtimes.status("fish_s2").state == "ready"

    def test_an_interpreter_outside_the_install_folder_is_refused(
        self, monkeypatch, tmp_path
    ):
        # The attack in one line: rewrite runtimes.json to name anything on
        # disk. This is what used to work.
        self._registry(monkeypatch, tmp_path)
        planted = tmp_path / "elsewhere" / "Scripts" / "python.exe"
        planted.parent.mkdir(parents=True)
        planted.write_bytes(b"the attacker's program")
        runtimes.register("fish_s2", str(planted))
        st = runtimes.status("fish_s2")
        # Its own state, not "broken". readiness.py maps a state to a
        # sentence, and "gone" is a different sentence from "not the one we
        # installed".
        assert st.state == "untrusted"
        assert st.error_code == TTS_RUNTIME_UNTRUSTED

    def test_a_different_filename_under_the_folder_is_refused(
        self, monkeypatch, tmp_path
    ):
        # Being under the anchor is not enough. The install folder is one a
        # same-user attacker can also write to, so a second executable dropped
        # beside the real interpreter must not become a candidate.
        self._registry(monkeypatch, tmp_path)
        beside = tmp_path / "envs" / "fish_s2" / "Scripts" / "payload.exe"
        beside.parent.mkdir(parents=True, exist_ok=True)
        beside.write_bytes(b"not an interpreter")
        runtimes.register("fish_s2", str(beside))
        assert runtimes.status("fish_s2").error_code == TTS_RUNTIME_UNTRUSTED

    def test_a_relative_path_is_refused(self, monkeypatch, tmp_path):
        # A relative path resolves against whatever the working directory
        # happens to be at launch, which is not a decision this app gets to
        # make and not one an allowlist can reason about.
        self._registry(monkeypatch, tmp_path)
        self._installed(tmp_path)
        runtimes.register("fish_s2", "envs/fish_s2/Scripts/python.exe")
        assert runtimes.status("fish_s2").error_code == TTS_RUNTIME_UNTRUSTED

    def test_a_traversal_that_climbs_out_is_refused(
        self, monkeypatch, tmp_path
    ):
        # The string starts under the anchor and does not stay there. The
        # check compares realpaths for exactly this reason; comparing the
        # written string would have admitted it.
        self._registry(monkeypatch, tmp_path)
        outside = tmp_path / "outside" / "Scripts"
        outside.mkdir(parents=True)
        (outside / "python.exe").write_bytes(b"the attacker's program")
        climbed = (tmp_path / "envs" / "fish_s2" / ".." / ".."
                   / "outside" / "Scripts" / "python.exe")
        (tmp_path / "envs" / "fish_s2").mkdir(parents=True, exist_ok=True)
        runtimes.register("fish_s2", str(climbed))
        assert runtimes.status("fish_s2").error_code == TTS_RUNTIME_UNTRUSTED

    @pytest.mark.skipif(os.name != "nt", reason="junctions are a Win32 thing")
    def test_a_junction_pointing_out_of_the_install_folder_is_refused(
        self, monkeypatch, tmp_path
    ):
        # The mutation this test exists for: swapping realpath for abspath.
        # A ".." traversal does not catch it, because abspath normalises ".."
        # too and both answers land outside the anchor. A junction does: the
        # string is genuinely under the install folder and the file is
        # genuinely somewhere else, so only a resolver that follows reparse
        # points can tell. os.path.islink returns False for a junction, which
        # is why the check does not use it.
        self._registry(monkeypatch, tmp_path)
        outside = tmp_path / "outside" / "Scripts"
        outside.mkdir(parents=True)
        (outside / "python.exe").write_bytes(b"the attacker's program")
        envs = tmp_path / "envs" / "fish_s2"
        envs.mkdir(parents=True)
        made = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(envs / "Scripts"),
             str(outside)],
            capture_output=True, text=True)
        if made.returncode != 0:
            pytest.skip("this filesystem would not make a junction")
        planted = envs / "Scripts" / "python.exe"
        assert planted.is_file()            # ground: the junction works
        assert str(planted).startswith(str(tmp_path / "envs"))   # and it lies
        runtimes.register("fish_s2", str(planted))
        assert runtimes.status("fish_s2").error_code == TTS_RUNTIME_UNTRUSTED

    def test_the_running_interpreter_is_admitted_only_outside_a_frozen_build(
        self, monkeypatch, tmp_path
    ):
        # The allowance exists so a development tree and this suite can
        # register the interpreter they are running under, which is nowhere
        # near the install folder. It must not survive into the shipped app:
        # there sys.executable is Elysium.exe, and admitting it would mean the
        # confinement check has an exception nobody asked for.
        self._registry(monkeypatch, tmp_path)
        runtimes.register("fish_s2", sys.executable)
        assert runtimes.status("fish_s2").state == "ready"      # ground: dev

        monkeypatch.setattr(sys, "frozen", True, raising=False)
        assert runtimes.status("fish_s2").error_code == TTS_RUNTIME_UNTRUSTED

    def test_an_interpreter_swapped_after_install_is_refused(
        self, monkeypatch, tmp_path
    ):
        # The digest is taken from the binary at install time. Overwriting it
        # afterwards, in place, under the right name, in the right folder, is
        # the attack the path check alone cannot see.
        self._registry(monkeypatch, tmp_path)
        exe = self._installed(tmp_path)
        runtimes.register("fish_s2", str(exe))
        assert runtimes.status("fish_s2").state == "ready"      # ground
        exe.write_bytes(b"the attacker's program")
        st = runtimes.status("fish_s2")
        # Its own state, not "broken". readiness.py maps a state to a
        # sentence, and "gone" is a different sentence from "not the one we
        # installed".
        assert st.state == "untrusted"
        assert st.error_code == TTS_RUNTIME_UNTRUSTED

    def test_the_digest_is_taken_at_install_not_at_first_use(
        self, monkeypatch, tmp_path
    ):
        # Computing it lazily would fingerprint whatever is there by then,
        # which is the thing being guarded against. Registering records it.
        self._registry(monkeypatch, tmp_path)
        exe = self._installed(tmp_path)
        runtimes.register("fish_s2", str(exe))
        recorded = json.loads(
            Path(config.TTS_RUNTIMES_PATH).read_text(encoding="utf-8"))
        digest = recorded["engines"]["fish_s2"].get("sha256")
        assert digest == hashlib.sha256(b"an interpreter").hexdigest()

    def test_a_registry_without_a_digest_is_refused(
        self, monkeypatch, tmp_path
    ):
        # This assertion is the reverse of the one it replaces, and the
        # reversal closes a hole the first version of this test had written
        # down as intended behaviour. That version let an entry with no sha256
        # through, on the reasoning that installs predating the check should
        # keep working. Measured: omitting the key is part of the SAME single
        # write that plants the path, so an attacker never writes one and the
        # fingerprint never runs. A test that blesses the bypass is worse than
        # no test, because it stops anybody looking.
        self._registry(monkeypatch, tmp_path)
        exe = self._installed(tmp_path)
        reg = Path(config.TTS_RUNTIMES_PATH)
        reg.write_text(json.dumps(
            {"engines": {"fish_s2": {"python": str(exe)}}}), encoding="utf-8")
        assert runtimes.status("fish_s2").error_code == TTS_RUNTIME_UNTRUSTED

    def test_planting_an_entry_with_no_digest_does_not_win(
        self, monkeypatch, tmp_path
    ):
        # The attack in the shape it is actually mounted: one write to the
        # registry, naming an executable the attacker put inside the app's own
        # install folder, with no digest recorded for it.
        self._registry(monkeypatch, tmp_path)
        planted = tmp_path / "envs" / "zz" / "Scripts" / "python.exe"
        planted.parent.mkdir(parents=True)
        planted.write_bytes(b"the attacker's program")
        reg = Path(config.TTS_RUNTIMES_PATH)
        reg.write_text(json.dumps(
            {"engines": {"fish_s2": {"python": str(planted)}}}),
            encoding="utf-8")
        st = runtimes.status("fish_s2")
        assert st.error_code == TTS_RUNTIME_UNTRUSTED
        assert st.state != "ready"

    def test_a_refusal_reads_differently_from_never_set_up(
        self, monkeypatch, tmp_path
    ):
        # "Somebody changed this" and "you never set voice up" send the user
        # to the same button and mean very different things. Collapsing them
        # would hide the first behind a routine reinstall prompt.
        self._registry(monkeypatch, tmp_path)
        assert runtimes.status("fish_s2").error_code == TTS_RUNTIME_MISSING
        planted = tmp_path / "elsewhere" / "python.exe"
        planted.parent.mkdir(parents=True)
        planted.write_bytes(b"")
        runtimes.register("fish_s2", str(planted))
        assert runtimes.status("fish_s2").error_code == TTS_RUNTIME_UNTRUSTED
        assert TTS_RUNTIME_UNTRUSTED != TTS_RUNTIME_MISSING
        assert TTS_RUNTIME_UNTRUSTED != TTS_RUNTIME_BROKEN

    def test_a_gone_interpreter_still_reads_as_broken_not_untrusted(
        self, monkeypatch, tmp_path
    ):
        # The discriminating half of the sentence above. A disk cleanup is not
        # an attack, and reporting one as the other would train the user to
        # ignore the message that matters.
        self._registry(monkeypatch, tmp_path)
        exe = self._installed(tmp_path)
        runtimes.register("fish_s2", str(exe))
        exe.unlink()
        assert runtimes.status("fish_s2").error_code == TTS_RUNTIME_BROKEN
