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
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tts import provision
from tts.host import worker_script
from tts.worker import _wire

BACKEND = Path(__file__).resolve().parents[1]
SPECS = [BACKEND / "elysium_onefile.spec", BACKEND / "elysium.spec"]
WORKER_DIR = BACKEND / "tts" / "worker"

#: A module that is genuinely installed beside this test, so the healthy half
#: of the ground control has something real to import. It stands in for an
#: engine package: the probe cares about "third party", not about which one.
_A_REAL_THIRD_PARTY_MODULE = "pytest"

#: Installed by `site` before the script runs, so it is in place for the
#: worker's own module scope - the only window that matters here.
#:
#: It raises OSError, not ImportError, on purpose. A half-installed engine on
#: Windows does not fail politely: torch reports "[WinError 126] The specified
#: module could not be found" out of its own DLLs, and a `try/except
#: ImportError` at module scope does not catch that. Poisoning with an
#: ImportError would let the guarded form look safe, which is precisely the
#: hole this test exists to close.
_POISON = '''\
import os
import sys

_LOCAL = os.environ["ELYSIUM_TEST_LOCAL_IMPORTS"]


class _DamagedInstall:
    def find_spec(self, fullname, path=None, target=None):
        top = fullname.partition(".")[0]
        if top in sys.stdlib_module_names:
            return None
        if os.path.exists(os.path.join(_LOCAL, top + ".py")):
            return None
        raise OSError("[WinError 126] %s: this install is damaged" % top)


sys.meta_path.insert(0, _DamagedInstall())
'''

#: Module scope, then a handler that imports only when asked. The shape every
#: real worker has, written out so the probe can be checked against a known
#: answer instead of only against the code it is guarding.
_SYNTHETIC = '''\
import sys
%(prologue)s
import _wire


def handle(op, req, send):
    if op == _wire.OP_LOAD:
        try:
            import %(engine)s                      # noqa: F401
        except Exception:
            sys.exit(_wire.EXIT_ENGINE_IMPORT)
        return {"loaded": True}
    return {"pong": True}


if __name__ == "__main__":
    sys.exit(_wire.serve(handle, channel=_wire.claim_stdout()))
'''


def _base_env() -> dict:
    """A child environment built from scratch, never `os.environ` mutated.

    A poison directory that leaked into this process would break every other
    test in the session, so it never touches `sys.path` here - it only ever
    reaches a child through its own copy of the environment.
    """
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def _healthy_env() -> dict:
    env = _base_env()
    env["PYTHONPATH"] = str(WORKER_DIR)
    return env


def _damaged_env(tmp_path: Path) -> dict:
    """Every third-party import in the child fails, whatever it is called.

    Derived rather than listed: an allowlist of engine module names is what
    the scan this replaces was built on, and `vocos` and `einops` walked
    straight through it. Anything outside the standard library and outside the
    worker's own folder is broken here, so a new dependency cannot be added to
    a worker's module scope without this noticing.
    """
    poison_dir = tmp_path / "damaged_install"
    poison_dir.mkdir(exist_ok=True)
    (poison_dir / "sitecustomize.py").write_text(_POISON, encoding="utf-8")
    env = _base_env()
    env["PYTHONPATH"] = os.pathsep.join([str(poison_dir), str(WORKER_DIR)])
    env["ELYSIUM_TEST_LOCAL_IMPORTS"] = str(WORKER_DIR)
    return env


def _synthetic_worker(tmp_path: Path, name: str, prologue: str) -> Path:
    path = tmp_path / f"{name}_worker.py"
    path.write_text(
        _SYNTHETIC % {"prologue": prologue,
                      "engine": _A_REAL_THIRD_PARTY_MODULE},
        encoding="utf-8")
    return path


def _drive(script: Path, frames: list[dict], env: dict, timeout: float = 90.0):
    """Spawn a worker for real and speak the real protocol at it.

    Returns its exit code and every frame it managed to send. The frames are
    built with `_wire.encode`, so the test cannot drift away from the framing
    the workers actually parse.
    """
    proc = subprocess.run(
        [sys.executable, "-u", str(script)],
        input="".join(_wire.encode(f) for f in frames),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=timeout,
    )
    return proc.returncode, [f for f in
                             (_wire.decode(line)
                              for line in proc.stdout.splitlines())
                             if f]


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
    """The engine workers cannot LOAD A MODEL here - the weights and the engine
    packages live in their own interpreters - but they can be RUN here, and
    that is the whole contract: a worker must start, speak the protocol and
    reach exit 3 in an interpreter where no engine package works at all."""

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

    def test_a_worker_still_starts_when_no_engine_package_works(self, tmp_path):
        """A worker must survive its own module import in a damaged install.

        This is what "no engine import at module scope" MEANS, and it used to
        be approximated by walking `tree.body` for a hand-written set of module
        names. That scan let three shapes through: a `try/except ImportError`
        around the same import is an `ast.Try` node and was never looked
        inside, a module not on the list (`vocos`, `einops`) was not an
        offender at all, and an import inside a helper the worker imports was
        only caught because the helpers happen to sit in the same folder.
        Driving the process instead cares about none of that - it asks the
        question the user's machine asks.

        The floor is asserted, not assumed: an empty list would make this loop
        pass over nothing, which is the exact failure the scan it replaces had
        a ground line for.
        """
        entrypoints = self._entrypoints()
        assert entrypoints, "no worker entrypoints found - the loop is vacuous"
        for script in entrypoints:
            code, frames = _drive(script, [{"id": 1, "op": _wire.OP_PING},
                                           {"id": 2, "op": _wire.OP_SHUTDOWN}],
                                  _damaged_env(tmp_path))
            assert code == _wire.EXIT_OK, (
                f"{script.name} did not survive module import with every "
                f"engine package broken (exit {code})")
            assert any(f.get("event") == "ready" for f in frames), (
                f"{script.name} started but never said hello")

    def test_a_damaged_install_exits_with_the_code_that_has_a_fix(self, tmp_path):
        """Exit 3 is the one diagnosis the app can turn into a single click.

        Exit 1 reads as "no idea what happened", and the runtime panel offers
        nothing for it. `EXIT_ENGINE_IMPORT` is imported from `_wire`, never
        retyped, so the two halves of the protocol cannot drift.
        """
        ckpt = tmp_path / "model"
        ckpt.mkdir()
        # The smallest folder every engine's load accepts before it reaches
        # its own import. Not a real checkpoint: the point is to get PAST the
        # file checks, because a worker that refuses the request never asks
        # its environment anything.
        (ckpt / "config.json").write_text("{}", encoding="utf-8")
        (ckpt / "codec.pth").write_bytes(b"")

        entrypoints = self._entrypoints()
        assert entrypoints, "no worker entrypoints found - the loop is vacuous"
        for script in entrypoints:
            code, _ = _drive(
                script,
                [{"id": 1, "op": _wire.OP_LOAD, "model_path": str(ckpt)}],
                _damaged_env(tmp_path),
            )
            assert code == _wire.EXIT_ENGINE_IMPORT, (
                f"{script.name} answered a damaged install with exit {code}; "
                f"only {_wire.EXIT_ENGINE_IMPORT} maps to \"set voice up again\"")

    def test_the_probe_sees_a_module_scope_import_when_there_is_one(self, tmp_path):
        """The positive control, in both shapes the old scan missed.

        Without it, a probe that always reported EXIT_OK would pass the test
        above and prove nothing. `vocos` was never on the scan's list and the
        guarded form was invisible to it; here both die where they should.
        """
        bare = _synthetic_worker(tmp_path, "bare", "import vocos")
        guarded = _synthetic_worker(tmp_path, "guarded",
                                    "try:\n    import torch\n"
                                    "except ImportError:\n    torch = None")
        for script in (bare, guarded):
            code, frames = _drive(script, [{"id": 1, "op": _wire.OP_PING},
                                           {"id": 2, "op": _wire.OP_SHUTDOWN}],
                                  _damaged_env(tmp_path))
            assert code != _wire.EXIT_OK, (
                f"{script.name} imported an engine package at module scope in "
                f"a damaged install and the probe called it healthy")
            assert not any(f.get("event") == "ready" for f in frames)

    def test_the_damage_is_what_makes_the_difference(self, tmp_path):
        """Ground control for the poison itself.

        The same script, the same frames, the same interpreter - only the
        broken environment removed. If exit 3 arrived either way the probe
        would be measuring the script's own habits, not the install.
        """
        script = _synthetic_worker(tmp_path, "lazy", "")
        frames = [{"id": 1, "op": _wire.OP_LOAD},
                  {"id": 2, "op": _wire.OP_SHUTDOWN}]

        broken, _ = _drive(script, frames, _damaged_env(tmp_path))
        assert broken == _wire.EXIT_ENGINE_IMPORT

        healthy, answers = _drive(script, frames, _healthy_env())
        assert healthy == _wire.EXIT_OK
        assert any(f.get("result", {}).get("loaded") for f in answers), (
            "the healthy run never got as far as importing anything")

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
        # The MEASURED post-decode reading on this card. This much free
        # VRAM is fine to live with, so the codec stays.
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


class TestTheAppReadsThatExitAsAdvice:
    """Second layer: exit 3 is only worth having if the app says the right
    sentence about it.

    The worker half is asserted next door; this is the seam where the exit code
    becomes a contract code. `tts_runtime_broken` is the one that puts "Set up
    voice" in front of the user, and `tts_worker_crashed` - the default for any
    unrecognised exit - puts nothing in front of them at all.
    """

    def test_a_damaged_install_reaches_the_client_as_a_reinstallable_runtime(
        self, tmp_path
    ):
        from tts.errors import TTS_RUNTIME_BROKEN
        from tts.worker_client import WorkerClient, WorkerFailure

        ckpt = tmp_path / "model"
        ckpt.mkdir()
        (ckpt / "config.json").write_text("{}", encoding="utf-8")
        (ckpt / "codec.pth").write_bytes(b"")
        env = _damaged_env(tmp_path)

        script = worker_script("xtts_v2")
        client = WorkerClient(sys.executable, str(script), engine_id="xtts_v2",
                              env={k: env[k] for k in
                                   ("PYTHONPATH", "ELYSIUM_TEST_LOCAL_IMPORTS")})
        client.start(timeout=60)
        try:
            # It handshakes: module scope is clean, so the damage is invisible
            # until something asks the environment for an engine. That is the
            # whole reason the diagnosis has to survive to this point.
            with pytest.raises(WorkerFailure) as exc:
                client.request(_wire.OP_LOAD, {"model_path": str(ckpt)},
                               timeout=60)
            assert exc.value.code == TTS_RUNTIME_BROKEN
        finally:
            client.close(grace=0.2)
