"""The readiness proof names what the workers import.

After an install, provision._verify_env imports a list of modules in the new
interpreter and calls the environment ready only if every one imports. That
list is only worth something while it names what the WORKER binds when it
loads the engine: `import fish_speech` passed on a directory with no
__init__.py at all (a namespace package) and so proved nothing about the two
modules the fish worker actually imports, which is how a wheel with none of
them could have been called ready. Measured 8 September 2026.

Each worker now carries ENGINE_IMPORTS, the names its engine import walks
before binding, and this module holds provision's list to a superset of it.
The workers may import nothing from the app, so they are loaded here the way
fish_synth_harness does, by path.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

from tts import provision
from tts.worker import _wire

WORKER_DIR = Path(__file__).resolve().parents[1] / "tts" / "worker"

#: engine id -> (worker file, the function that imports the engine, its args)
IMPORTERS = {
    "fish_s2": ("fish_s2.py", "_engine", ()),
    "chatterbox": ("chatterbox.py", "_ensure_engine", ("english",)),
    "xtts_v2": ("xtts_v2.py", "_import_engine", ()),
}


def _load_worker(engine_id: str):
    name = f"verify_contract_{engine_id}"
    spec = importlib.util.spec_from_file_location(name, WORKER_DIR / IMPORTERS[engine_id][0])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.modules.pop(name, None)
    return mod


@pytest.fixture(autouse=True)
def _no_stub_left_behind():
    """chatterbox's _patch_perth plants a stub `perth` module when the real
    one is absent, which it is in this interpreter. It must not outlive the
    test that caused it."""
    had = "perth" in sys.modules
    yield
    if not had and isinstance(sys.modules.get("perth"), types.ModuleType) \
            and getattr(sys.modules.get("perth"), "__file__", None) is None:
        sys.modules.pop("perth", None)


class TestTheListsAgree:
    @pytest.mark.parametrize("engine_id", sorted(IMPORTERS))
    def test_every_module_the_worker_binds_is_in_the_proof(self, engine_id):
        worker = _load_worker(engine_id)
        binds = set(worker.ENGINE_IMPORTS)
        proof = set(provision.ENGINES[engine_id]["verify"])
        assert len(binds) >= 3 and "torch" in binds, "ground: the worker names its engine"
        assert binds <= proof, (
            f"{engine_id}: the worker binds {sorted(binds - proof)} and the "
            f"install would be called ready without importing them")

    def test_the_subset_check_can_say_no(self):
        """Positive control for the test above."""
        proof = set(provision.ENGINES["fish_s2"]["verify"])
        assert not ({"torch", "a.module.nobody.ships"} <= proof)

    def test_fish_names_the_two_modules_and_not_the_bare_package(self):
        binds = _load_worker("fish_s2").ENGINE_IMPORTS
        assert "fish_speech.models.dac.inference" in binds
        assert "fish_speech.models.text2semantic.inference" in binds
        assert "fish_speech" not in binds


class TestTheTupleIsWhatRuns:
    """A constant nobody reads can drift from the import statements beside
    it. Each worker's engine import walks ENGINE_IMPORTS by name before it
    binds anything, and that walk is what this observes."""

    @pytest.mark.parametrize("engine_id", sorted(IMPORTERS))
    def test_the_engine_import_walks_the_tuple_by_name(self, engine_id, monkeypatch):
        worker = _load_worker(engine_id)
        _, func_name, args = IMPORTERS[engine_id]
        walked: list[str] = []

        def _record(name):
            walked.append(name)
            return types.ModuleType(name)

        monkeypatch.setattr(worker.importlib, "import_module", _record)
        # This interpreter has no torch, so the binds after the walk fail
        # and the worker answers with the exit code that means "set the
        # engine up again". The walk has happened by then, which is the point.
        with pytest.raises(SystemExit) as info:
            getattr(worker, func_name)(*args)
        assert info.value.code == _wire.EXIT_ENGINE_IMPORT
        assert walked == list(worker.ENGINE_IMPORTS)

    def test_chatterbox_asks_for_no_model_class_when_none_is_needed(self, monkeypatch):
        """prepare_ref decodes audio with torch alone; the walk is for the
        model path only, so a reference clip never pays for chatterbox's
        import."""
        worker = _load_worker("chatterbox")
        walked: list[str] = []
        monkeypatch.setattr(worker.importlib, "import_module",
                            lambda name: walked.append(name) or types.ModuleType(name))
        with pytest.raises(SystemExit):
            worker._ensure_engine(None)
        assert walked == []
