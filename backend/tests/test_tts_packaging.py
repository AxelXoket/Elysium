"""Packaging parity: what the code looks for is what the build ships.

Voice depends on two things existing as REAL FILES beside the frozen app -
the engine worker scripts (run by an interpreter that cannot see inside the
exe) and the pinned requirements (without which "Set up voice" would resolve
"latest" and install something nobody tested).

Both are resolved by path at runtime, and both are placed by the .spec at build
time. Nothing connects the two except agreement, and that kind of agreement
breaks silently: the dev checkout keeps working perfectly while the packaged
exe cannot set voice up at all. So the agreement is asserted here.
"""
import re
import sys
from pathlib import Path

import pytest

from tts import provision
from tts.host import worker_script

BACKEND = Path(__file__).resolve().parents[1]
SPECS = [BACKEND / "elysium_onefile.spec", BACKEND / "elysium.spec"]


def _spec_bodies():
    """The PyInstaller specs, or a RED test.

    This used to skip. A packaging gate that turns green when the artefact
    it guards is missing has passed on exactly the state it exists to catch:
    "no spec here" is not evidence that the packaging is correct, it is the
    absence of any evidence at all.

    Latent today - both specs are on disk, so this branch never runs and the
    change alters nothing about the current suite. It only speaks in the
    checkout where they are gone.
    """
    found = [(p, p.read_text(encoding="utf-8")) for p in SPECS if p.is_file()]
    if not found:
        pytest.fail("no PyInstaller spec in this checkout: "
                    + ", ".join(str(s) for s in SPECS))
    return found


class TestTheFilesExist:
    def test_every_supported_engine_has_a_worker_script(self):
        for engine_id in provision.ENGINES:
            path = worker_script(engine_id)
            assert path.is_file(), f"no worker script for {engine_id}: {path}"

    def test_every_supported_engine_has_pinned_requirements(self):
        for engine_id in provision.ENGINES:
            assert provision.requirements_path(engine_id).is_file()

    def test_the_protocol_module_is_not_mistaken_for_an_engine(self):
        """_wire is imported by the app; it is not something to spawn."""
        assert "_wire" not in provision.ENGINES


class TestTheBuildShipsThem:
    def test_the_spec_bundles_the_worker_scripts_where_the_code_looks(self):
        """host.worker_script() resolves <_MEIPASS>/tts_worker/<engine>.py, so
        the spec's destination must be exactly that folder name."""
        for path, body in _spec_bodies():
            assert '"tts_worker"' in body, f"{path.name} does not ship the workers"

    def test_the_spec_bundles_the_requirements_where_the_code_looks(self):
        """provision.requirements_path() resolves from tts/provision.py's own
        directory, which in a frozen build is <_MEIPASS>/tts - so the pins must
        land in <_MEIPASS>/tts/requirements."""
        for path, body in _spec_bodies():
            assert '"tts/requirements"' in body, f"{path.name} does not ship the pins"

    def test_the_worker_directory_the_spec_reads_is_the_one_that_exists(self):
        for path, body in _spec_bodies():
            m = re.search(r'_WORKER_DIR\s*=\s*os\.path\.join\((.+?)\)', body)
            assert m, f"{path.name} lost its worker-directory line"
            assert '"tts"' in m.group(1) and '"worker"' in m.group(1)


class TestWorkerScriptHygiene:
    """The engine workers cannot be EXECUTED here (their dependencies live in
    their own interpreters), but their contract-critical shape is checkable
    without running them: they must parse, keep every engine import out of
    module scope (a broken venv must exit EXIT_ENGINE_IMPORT, not die at
    import), and speak the protocol through _wire."""

    ENGINE_MODULES = {"torch", "torchaudio", "torchao", "numpy", "soundfile",
                      "librosa", "transformers", "TTS", "chatterbox",
                      "fish_speech", "perth"}

    def _worker_dir(self):
        return Path(__file__).resolve().parents[1] / "tts" / "worker"

    def _scripts(self):
        """Every file the engine interpreter will load.

        Helpers included: a module-scope `import torch` in a SHARED module
        breaks the failure contract exactly as badly as one in an engine
        script, because the engine script imports it.
        """
        return [p for p in self._worker_dir().glob("*.py")
                if p.name != "__init__.py"]

    def _entrypoints(self):
        """The engine halves that actually speak the protocol.

        Leading underscore marks a shared helper (`_wire` the protocol itself,
        `_dsp` the time-stretch maths). Those are imported BY a worker, never
        run as one, so demanding handle()/serve() of them would be asking a
        library to be a program.
        """
        return [p for p in self._scripts() if not p.name.startswith("_")]

    def test_every_worker_script_parses(self):
        import ast

        scripts = self._scripts()
        assert len(scripts) >= 3, "engine workers are missing"
        for script in scripts:
            ast.parse(script.read_text(encoding="utf-8"), filename=str(script))

    def test_no_engine_import_sits_at_module_scope(self):
        """A module-scope `import torch` would crash a damaged venv with exit 1
        ("no idea what happened") instead of exit 3 ("the environment is
        damaged - set up voice again"), destroying the one diagnosis that maps
        to a one-click fix."""
        import ast

                # The floor lives in a sibling test, which is one deletion away
        # from leaving this loop running over nothing.
        assert self._scripts(), "no worker scripts found - the loop is vacuous"
        for script in self._scripts():
            tree = ast.parse(script.read_text(encoding="utf-8"))
            offenders = []
            for node in tree.body:               # module scope only
                if isinstance(node, ast.Import):
                    offenders += [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    offenders.append(node.module.split(".")[0])
            bad = sorted(set(offenders) & self.ENGINE_MODULES)
            assert not bad, f"{script.name} imports {bad} at module scope"

    def test_every_worker_defines_the_protocol_entrypoints(self):
        import ast

        assert self._entrypoints(), "engine entrypoints are missing"
        for script in self._entrypoints():
            tree = ast.parse(script.read_text(encoding="utf-8"))
            names = {n.name for n in ast.walk(tree)
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
            assert "handle" in names, f"{script.name} has no handle()"
            body_src = script.read_text(encoding="utf-8")
            assert "_wire.claim_stdout" in body_src, (
                f"{script.name} never claims stdout - one library banner would "
                "desynchronise the protocol stream")
            assert "_wire.serve" in body_src


class TestFrozenResolution:
    def test_worker_scripts_resolve_inside_the_bundle_when_frozen(self, monkeypatch,
                                                                   tmp_path):
        meipass = tmp_path / "_MEI42"
        (meipass / "tts_worker").mkdir(parents=True)
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)
        assert worker_script("fish_s2") == meipass / "tts_worker" / "fish_s2.py"

    def test_requirements_resolve_beside_the_module_not_the_working_directory(self):
        """Resolving from __file__ is what makes this work frozen AND however
        the app happens to be launched - a relative path would depend on the
        shortcut's "start in" field, which nobody controls."""
        got = provision.requirements_path("fish_s2")
        assert got.parent.name == "requirements"
        assert got.parent.parent.name == "tts"

    def test_requirements_resolve_in_a_frozen_layout_too(self, monkeypatch, tmp_path):
        """The frozen bundle places provision.py under <_MEIPASS>/tts/, so
        __file__-relative resolution must land on <_MEIPASS>/tts/requirements -
        which is exactly where the spec ships the pins. This pins the two
        conventions together; worker_script already has the same test."""
        frozen_tts = tmp_path / "_MEI7" / "tts"
        (frozen_tts / "requirements").mkdir(parents=True)
        (frozen_tts / "requirements" / "fish_s2.txt").write_text("torch==1",
                                                                encoding="utf-8")
        monkeypatch.setattr(provision, "__file__",
                            str(frozen_tts / "provision.py"))
        got = provision.requirements_path("fish_s2")
        assert got == frozen_tts / "requirements" / "fish_s2.txt"
        assert got.is_file()


class TestFishCodecResidency:
    """The measured regression: a policy that evicted the codec after every
    sentence and reloaded it for the next one.

    The worker module is importable here because it keeps every engine import
    out of module scope (asserted above), so its pure decisions are testable
    with no GPU and no torch.
    """

    def _mod(self):
        import importlib.util

        path = (Path(__file__).resolve().parents[1] / "tts" / "worker"
                / "fish_s2.py")
        spec = importlib.util.spec_from_file_location("fish_s2_worker", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules.setdefault("fish_s2_worker", mod)
        spec.loader.exec_module(mod)
        return mod

    def test_the_codec_stays_resident_at_the_measured_free_memory(self):
        """3.1 GB free is what a 16 GB card actually reports with the codec
        loaded. The old threshold (4.0) called that "no room" and paid a full
        reload - about five seconds - for every single sentence."""
        mod = self._mod()
        assert mod._should_keep_codec(3.1) is True
        assert mod._should_keep_codec(5.0) is True
        # The MEASURED post-decode reading on this card. The owner confirmed
        # this much free VRAM is fine to live with, so the codec stays.
        assert mod._should_keep_codec(1.76) is True

    def test_a_genuinely_tight_card_still_gives_the_memory_back(self):
        mod = self._mod()
        assert mod._should_keep_codec(0.5) is False
        assert mod._should_keep_codec(0.0) is False

    def test_the_keep_floor_matches_the_pre_generation_guard(self):
        """One number, not two: the pre-generation guard and the post-decode
        keep decision use the SAME codec floor. Two different thresholds are
        exactly how the self-defeating policy happened."""
        mod = self._mod()
        assert mod._should_keep_codec(mod._VRAM_RESERVE_GB) is True
        assert mod._should_keep_codec(mod._VRAM_RESERVE_GB - 0.1) is False
