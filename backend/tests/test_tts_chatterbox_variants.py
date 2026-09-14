"""The adapter and the worker agree on what a complete Chatterbox checkpoint is.

Two files decide it. adapters/chatterbox.py fingerprints a folder on the host
and says which build it holds and what is missing; tts/worker/chatterbox.py
runs in the engine venv and, right before from_local(), checks the same folder
against the files the library will actually open. They cannot share an object:
the worker may not import the app, and the app never imports the worker. So
each carries a copy of the tables, and this file is what holds the copies
equal, and what proves the verdicts they reach about one folder are the same.

Measured before this file existed (8 September 2026): the host accepted
s3gen.pt / ve.pt / t3_*.pt for the english build and named `s3gen.pt` as
missing whatever the build, while the worker demanded the .safetensors names
plus tokenizer.json. An english folder read as complete on the settings page
and then failed at load with "english checkpoint is incomplete", and a Turbo
folder (t3_turbo_v1.safetensors, no tokenizer.json) read as a complete english
checkpoint the bundled library cannot load at all.

The worker is loaded by path, the way fish_synth_harness.py loads fish_s2.py.
Everything past its completeness check is replaced at the boundary (no torch,
no library); the check itself, the refusal and the sentence they print are
the real ones.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import config
from tts import host as tts_host
from tts import provision, runtimes, vram
from tts.adapters import chatterbox as adapter_mod
from tts.adapters.chatterbox import ChatterboxAdapter
from tts.base import DetectedModel
from tts.errors import (
    ALL_CODES,
    TTS_MODEL_VARIANT_UNSUPPORTED,
    TTS_RUNTIME_BROKEN,
    TTS_WORKER_FAILED,
)
from tts.registry import identify_dir
from tts.worker import _wire
from tts.worker_client import WorkerClient, WorkerFailure

WORKER = Path(__file__).resolve().parents[1] / "tts" / "worker" / "chatterbox.py"


def load_worker():
    """A fresh copy of the worker module.

    Its import block forces the HF/transformers offline switches into the
    environment and moves its own directory to the back of sys.path. Both are
    right for the engine process and wrong to leave behind in a test process,
    so both are put back exactly as found.
    """
    env = dict(os.environ)
    path = list(sys.path)
    spec = importlib.util.spec_from_file_location(
        "chatterbox_worker_under_test", WORKER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["chatterbox_worker_under_test"] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.modules.pop("chatterbox_worker_under_test", None)
        os.environ.clear()
        os.environ.update(env)
        sys.path[:] = path
    return mod


@pytest.fixture(scope="module")
def worker():
    return load_worker()


def _folder(tmp_path: Path, *files: str, name: str = "model") -> Path:
    d = tmp_path / name
    d.mkdir()
    for name in files:
        (d / name).write_bytes(b"x")
    return d


def _stub_engine(worker, monkeypatch, loaded: list):
    """Everything past the completeness check, replaced at the boundary.

    `_ensure_engine` is the torch import; `ENGINE["torch"]` answers the one
    question `_device` asks; the two model classes record what from_local was
    handed and return an object with the two attributes `_op_load` reads.
    STATE is swapped for a fresh dict so a load in one test is not "cached"
    in the next.
    """
    monkeypatch.setattr(worker, "_ensure_engine", lambda variant=None: None)
    monkeypatch.setitem(worker.ENGINE, "torch", SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: False,
                             empty_cache=lambda: None)))

    class Loader:
        @staticmethod
        def from_local(model_dir, device):
            loaded.append((Path(model_dir), device))
            return SimpleNamespace(conds=None, sr=worker.SAMPLE_RATE)

    monkeypatch.setitem(worker.ENGINE, "mtl", Loader)
    monkeypatch.setitem(worker.ENGINE, "en", Loader)
    monkeypatch.setattr(worker, "STATE", dict(worker.STATE))


# ── the folders ──────────────────────────────────────────────────────────────
# File lists as they are on the Hub and on this machine, not as the tables
# would like them to be. A layout built from the table under test would pass
# whatever the table said.

#: ResembleAI/chatterbox, the english build, as chatterbox_tts 0.1.7 opens it.
ENGLISH = ("t3_cfg.safetensors", "s3gen.safetensors", "ve.safetensors",
           "tokenizer.json", "conds.pt")
#: The same build with only the .pt weights and no tokenizer.
ENGLISH_PT_ONLY = ("t3_cfg.pt", "s3gen.pt", "ve.pt", "conds.pt")
#: The multilingual v2 build as mtl_tts.py opens it.
MULTILINGUAL_V2 = ("t3_mtl23ls_v2.safetensors", "s3gen.pt", "ve.pt",
                   "grapheme_mtl_merged_expanded_v1.json", "conds.pt")
MULTILINGUAL_V2_NO_GRAPHEME = tuple(
    f for f in MULTILINGUAL_V2 if f != "grapheme_mtl_merged_expanded_v1.json")
#: ResembleAI/chatterbox-turbo as listed on the Hub. No tokenizer.json.
TURBO = ("t3_turbo_v1.safetensors", "s3gen_meanflow.safetensors",
         "s3gen.safetensors", "ve.safetensors", "conds.pt", "vocab.json",
         "merges.txt", "tokenizer_config.json")
#: The multilingual v3 files in the live ResembleAI/chatterbox repo.
MULTILINGUAL_V3 = ("t3_mtl23ls_v3.safetensors", "s3gen_v3.pt", "ve.pt",
                   "mtl_tokenizer.json")
#: The snapshot in this machine's HF cache: both loadable builds side by side.
BOTH_BUILDS = ("Cangjie5_TC.json", "conds.pt",
               "grapheme_mtl_merged_expanded_v1.json", "s3gen.pt",
               "s3gen.safetensors", "t3_cfg.safetensors",
               "t3_mtl23ls_v2.safetensors", "tokenizer.json", "ve.pt",
               "ve.safetensors")
#: A T3 name neither table knows, beside chatterbox's other files.
UNKNOWN_T3 = ("t3_mystery_v9.safetensors", "s3gen.safetensors", "ve.safetensors")

#: (files, build, what the worker will demand) for every folder above.
LAYOUTS = [
    pytest.param(ENGLISH, "english", (), id="english_safetensors"),
    pytest.param(ENGLISH_PT_ONLY, "english",
                 adapter_mod.REQUIRED_FILES["english"], id="english_pt_only"),
    pytest.param(MULTILINGUAL_V2, "multilingual", (), id="multilingual_v2"),
    pytest.param(MULTILINGUAL_V2_NO_GRAPHEME, "multilingual",
                 ("grapheme_mtl_merged_expanded_v1.json",),
                 id="multilingual_v2_no_grapheme"),
    pytest.param(TURBO, "turbo", (), id="turbo"),
    pytest.param(MULTILINGUAL_V3, "multilingual_v3", (), id="multilingual_v3"),
    pytest.param(BOTH_BUILDS, "multilingual", (), id="both_builds"),
    pytest.param(UNKNOWN_T3, adapter_mod.UNKNOWN_VARIANT, (), id="unknown_t3"),
]


# ── one table ────────────────────────────────────────────────────────────────

class TestOneTable:
    def test_required_files_are_the_same_table_on_both_sides(self, worker):
        assert adapter_mod.REQUIRED_FILES == worker.REQUIRED_FILES
        # Ground: two empty dicts are equal too. The worker's table is the
        # original and it names both loadable builds, four files each, which
        # is what chatterbox_tts 0.1.7's two from_local() bodies open.
        assert set(worker.REQUIRED_FILES) == {"english", "multilingual"}
        assert all(len(names) == 4 and all(names)
                   for names in worker.REQUIRED_FILES.values())

    def test_the_build_table_is_the_same_on_both_sides(self, worker):
        assert adapter_mod.VARIANT_T3 == worker.VARIANT_T3
        assert adapter_mod.UNKNOWN_VARIANT == worker.UNKNOWN_VARIANT
        builds = [v for v, _ in worker.VARIANT_T3]
        assert builds, "an empty table would also be equal on both sides"
        # Every loadable build is in the table, and multilingual is tried
        # first: that order is what keeps the both-builds snapshot reading as
        # multilingual, which is what the app has always shown for it.
        assert set(worker.REQUIRED_FILES) <= set(builds)
        assert builds[0] == "multilingual"

    def test_every_name_in_the_table_is_a_plain_file_name(self, worker):
        # The worker joins these onto the model directory. A name with a
        # separator in it would be a path, and a path is not a required file.
        for names in worker.REQUIRED_FILES.values():
            for name in names:
                assert Path(name).name == name, name


# ── one verdict per folder ───────────────────────────────────────────────────

@pytest.mark.parametrize("files, build, demanded", LAYOUTS)
class TestOneVerdictPerFolder:
    def test_the_adapter_reads_the_build_and_names_the_workers_files(
            self, tmp_path, files, build, demanded):
        res = identify_dir(_folder(tmp_path, *files))
        assert res is not None and res.engine_id == "chatterbox"
        assert res.variant == build
        assert res.missing == demanded
        # And nothing it names is outside the worker's list for that build.
        assert set(res.missing) <= set(adapter_mod.REQUIRED_FILES.get(build, ()))

    def test_the_worker_reads_the_same_build_off_the_same_folder(
            self, worker, tmp_path, files, build, demanded):
        d = _folder(tmp_path, *files)
        # A request that forgot `variant`: the disk decides, the same way.
        assert worker._variant_of({}, d) == ChatterboxAdapter.identify(d).variant
        assert worker._variant_of({}, d) == build

    def test_the_worker_demands_exactly_what_the_adapter_named(
            self, worker, tmp_path, monkeypatch, capsys, files, build, demanded):
        d = _folder(tmp_path, *files)
        loaded: list = []
        _stub_engine(worker, monkeypatch, loaded)
        res = ChatterboxAdapter.identify(d)
        req = {"model_path": str(d), "variant": res.variant}

        if build not in worker.REQUIRED_FILES:
            # No loader: refused by name, never blamed for another build's
            # files. An error FRAME with the app's own code, not exit 4: the
            # host maps that exit to tts_worker_failed, whose sentence sends
            # the reader to reinstall an engine that is fine.
            with pytest.raises(worker._wire.WorkerError) as ex:
                worker._op_load(req, lambda event: None)
            assert ex.value.code == worker._wire.CODE_MODEL_VARIANT_UNSUPPORTED
            detail = ex.value.detail
            assert "incomplete" not in detail and "missing" not in detail
            assert loaded == []
            return

        if demanded:
            with pytest.raises(SystemExit) as ex:
                worker._op_load(req, lambda event: None)
            assert ex.value.code == worker._wire.EXIT_MODEL_LOAD
            err = capsys.readouterr().err
            named = tuple(err.split("missing: ", 1)[1].strip().split(", "))
            assert named == res.missing == demanded
            assert loaded == []
            return

        # Complete: the worker's own check lets it through to from_local, on
        # this folder, as this build.
        out = worker._op_load(req, lambda event: None)
        assert out["loaded"] is True and out["variant"] == build
        assert loaded == [(d.resolve(), "cpu")]


# ── the refusal ──────────────────────────────────────────────────────────────

class TestABuildTheWorkerCannotLoad:
    def test_turbo_is_refused_by_name_before_torch_is_imported(
            self, worker, tmp_path, monkeypatch):
        d = _folder(tmp_path, *TURBO)
        imported: list = []
        monkeypatch.setattr(worker, "_ensure_engine",
                            lambda variant=None: imported.append(variant))
        with pytest.raises(worker._wire.WorkerError) as ex:
            worker._op_load({"model_path": str(d), "variant": "turbo"},
                            lambda event: None)
        assert ex.value.code == worker._wire.CODE_MODEL_VARIANT_UNSUPPORTED
        assert imported == [], "torch was imported for an answer that was no"
        detail = ex.value.detail
        assert "turbo" in detail
        for loadable in worker.REQUIRED_FILES:
            assert loadable in detail, "the refusal names what does load"

    def test_positive_control_the_incomplete_folder_is_still_exit_four(
            self, worker, tmp_path, monkeypatch):
        # The frame is specific to the refusal. A build the worker does load
        # with a file gone is the older verdict, and that one still exits 4:
        # the boundary between "no loader" and "no file" is what the test
        # above is asserting, so both sides of it are shown here.
        d = _folder(tmp_path, *ENGLISH_PT_ONLY)
        _stub_engine(worker, monkeypatch, [])
        with pytest.raises(SystemExit) as ex:
            worker._op_load({"model_path": str(d), "variant": "english"},
                            lambda event: None)
        assert ex.value.code == worker._wire.EXIT_MODEL_LOAD

    def test_the_hosts_word_is_kept_for_a_build_it_cannot_load(
            self, worker, tmp_path):
        # The old fallback re-read the disk for any name it did not know and
        # called the folder whatever the disk said. So the folder here is one
        # the disk reads as ENGLISH: the host's word has to win over it, or
        # this test cannot tell the two rules apart.
        d = _folder(tmp_path, *ENGLISH)
        assert worker._variant_of({}, d) == "english"
        assert worker._variant_of({"variant": "turbo"}, d) == "turbo"
        assert worker._variant_of({"variant": " Multilingual_V3 "}, d) == "multilingual_v3"
        # And a name that is nobody's build is not taken on trust.
        assert worker._variant_of({"variant": "nonsense"}, d) == "english"

    def test_positive_control_an_incomplete_folder_does_say_incomplete(
            self, worker, tmp_path, monkeypatch, capsys):
        # The refusal test above asserts the word "incomplete" is ABSENT. This
        # is the folder for which the same code path prints it.
        d = _folder(tmp_path, *ENGLISH_PT_ONLY)
        _stub_engine(worker, monkeypatch, [])
        with pytest.raises(SystemExit):
            worker._op_load({"model_path": str(d), "variant": "english"},
                            lambda event: None)
        assert "english checkpoint is incomplete" in capsys.readouterr().err

    def test_the_adapter_refuses_the_same_builds_the_worker_refuses(self, worker):
        for build in [v for v, _ in worker.VARIANT_T3] + [worker.UNKNOWN_VARIANT]:
            model = DetectedModel(uid="u", engine_id="chatterbox", name="m",
                                  path="/m", variant=build)
            refusal = ChatterboxAdapter.variant_refusal(model)
            if build in worker.REQUIRED_FILES:
                assert refusal == "", build
            else:
                assert refusal, build
                if build != worker.UNKNOWN_VARIANT:
                    assert build in refusal

    def test_a_model_that_never_went_through_identify_is_not_refused(self):
        # variant=None is a sidecar override the files did not corroborate;
        # that one is already reported as incomplete.
        model = DetectedModel(uid="u", engine_id="chatterbox", name="m",
                              path="/m", variant=None)
        assert ChatterboxAdapter.variant_refusal(model) == ""


# ── what the unloadable builds still get right ───────────────────────────────

class TestTheDescriptorForAnUnloadableBuild:
    def test_multilingual_v3_keeps_the_language_dial(self):
        v3 = DetectedModel(uid="u", engine_id="chatterbox", name="m",
                           path="/m", variant="multilingual_v3")
        names = [s.name for s in ChatterboxAdapter.describe_settings(v3)]
        assert "language_id" in names
        assert "tr" in ChatterboxAdapter.languages_for(v3)

    def test_turbo_takes_the_smaller_claim(self):
        turbo = DetectedModel(uid="u", engine_id="chatterbox", name="m",
                              path="/m", variant="turbo")
        names = [s.name for s in ChatterboxAdapter.describe_settings(turbo)]
        assert "language_id" not in names
        assert ChatterboxAdapter.languages_for(turbo) == ("en",)


# ── the refusal, across the process boundary ─────────────────────────────────
# The worker here is the REAL one, run by path under this venv the way the app
# runs it. The refusal comes before the engine import, so no torch is needed
# to reach it; a build the worker does load goes one step further, to the
# import, which in a venv without torch is exit 3. That exit is the positive
# control: it only comes from a worker that was reached and not refused.

def _real_worker() -> WorkerClient:
    c = WorkerClient(sys.executable, str(WORKER), engine_id="chatterbox")
    c.start(timeout=30)
    return c


class TestTheRefusalCrossesTheProcessBoundary:
    """Measured before this class existed: the refusal was exit 4.
    worker_client maps exit 4 to tts_worker_failed, the route answered 500,
    and the sentence for that code says "press Set up again to reinstall the
    engine": the wrong errand about an environment that is fine. As an error
    frame the refusal carries its own code, which _failure_from passes
    through because tts/errors.py declares it."""

    def test_the_wire_code_is_the_apps_own_word(self, worker):
        # The host checks a frame's code against tts.errors.ALL_CODES and
        # rewrites a stranger to tts_worker_failed, the exact misreport this
        # ends. So the worker's constant has to BE the app's.
        assert worker._wire.CODE_MODEL_VARIANT_UNSUPPORTED == TTS_MODEL_VARIANT_UNSUPPORTED
        assert worker._wire.CODE_MODEL_VARIANT_UNSUPPORTED in ALL_CODES

    def test_a_turbo_load_comes_back_under_its_own_code_and_the_worker_lives(
            self, tmp_path):
        d = _folder(tmp_path, *TURBO)
        c = _real_worker()
        try:
            with pytest.raises(WorkerFailure) as ex:
                c.request(_wire.OP_LOAD,
                          {"model_path": str(d), "variant": "turbo"},
                          timeout=30)
            assert ex.value.code == TTS_MODEL_VARIANT_UNSUPPORTED
            assert ex.value.code != TTS_WORKER_FAILED
            # A refusal is an answer, not a death: the process is still there
            # for the next request, and nothing is loaded in it.
            assert c.alive, "the refusal took the worker down"
            assert c.request(_wire.OP_PING)["loaded"] is False
        finally:
            c.close(grace=0.2)

    def test_the_disk_alone_reaches_the_same_refusal(self, tmp_path):
        # A request that forgot `variant`: the worker reads the T3 file name
        # off the folder and refuses under the same code.
        d = _folder(tmp_path, *MULTILINGUAL_V3)
        c = _real_worker()
        try:
            with pytest.raises(WorkerFailure) as ex:
                c.request(_wire.OP_LOAD, {"model_path": str(d)}, timeout=30)
            assert ex.value.code == TTS_MODEL_VARIANT_UNSUPPORTED
        finally:
            c.close(grace=0.2)

    def test_positive_control_a_build_it_loads_goes_on_to_the_engine_import(
            self, tmp_path):
        d = _folder(tmp_path, *ENGLISH)
        c = _real_worker()
        try:
            with pytest.raises(WorkerFailure) as ex:
                c.request(_wire.OP_LOAD,
                          {"model_path": str(d), "variant": "english"},
                          timeout=30)
            assert ex.value.code == TTS_RUNTIME_BROKEN
            assert ex.value.code != TTS_MODEL_VARIANT_UNSUPPORTED
        finally:
            c.close(grace=0.2)


# ── the status the route answers ─────────────────────────────────────────────

@pytest.fixture
def voice(monkeypatch, tmp_path):
    """A redirected voice environment whose worker is the real chatterbox
    script. Mirrors test_tts_runtime_api's fixture of the same name, with the
    engine's own worker in place of the fake."""
    root = tmp_path / "voice"
    (root / "envs").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "TTS_DIR", root, raising=False)
    monkeypatch.setattr(config, "TTS_ENVS_DIR", str(root / "envs"), raising=False)
    monkeypatch.setattr(config, "TTS_BIN_DIR", str(root / "bin"), raising=False)
    monkeypatch.setattr(config, "TTS_CACHE_DIR", str(root / "cache"), raising=False)
    monkeypatch.setattr(config, "TTS_RUNTIMES_PATH", str(root / "runtimes.json"),
                        raising=False)
    models = root / "models"
    models.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "TTS_MODELS_DIR", str(models), raising=False)
    monkeypatch.setattr(
        vram, "_run_smi", lambda: "NVIDIA GeForce RTX 5080, 16303, 14000, 2303\n")
    provision.reset_jobs()
    host = tts_host.VoiceHost()
    host.script_resolver = lambda engine_id: str(WORKER)
    monkeypatch.setattr(tts_host, "_HOST", host, raising=False)
    yield models
    host.unload("test teardown")
    provision.reset_jobs()


def _load(client, models: Path, *files: str):
    """Put one folder in the models root, register this interpreter as the
    chatterbox runtime, and press load on it over HTTP."""
    _folder(models, *files, name="the-model")
    runtimes.register("chatterbox", sys.executable)
    listed = client.get("/api/v1/tts/models").json()["models"]
    uid = next(m["uid"] for m in listed if m["engine_id"] == "chatterbox")
    return client.post("/api/v1/tts/load", json={"uid": uid})


class TestTheRouteAnswersTheContractsStatus:
    """POST /tts/load never consults readiness: the host checks the runtime
    and the fit and then spawns the worker, so a turbo folder reaches
    _op_load over HTTP. The only producer of the code on that path is the
    worker's frame, and the route has to answer the 409 the contract row
    records, not the 500 that exit 4 became."""

    def test_a_turbo_folder_answers_409_under_its_own_code(self, client, voice):
        r = _load(client, voice, *TURBO)
        assert r.status_code == 409, r.text
        assert r.json()["detail"] == TTS_MODEL_VARIANT_UNSUPPORTED
        # The state the UI polls afterwards carries the same word, so the
        # panel does not say "could not start" about a refusal.
        state = client.get("/api/v1/tts/state").json()
        assert state["error_code"] == TTS_MODEL_VARIANT_UNSUPPORTED
        assert state["state"] == tts_host.STATE_ERROR

    def test_positive_control_an_english_folder_reaches_the_engine_import(
            self, client, voice):
        # Not refused, so the same worker goes on to import torch, which this
        # venv does not have: exit 3, the damaged-install code. It proves the
        # request reached the worker and that the refusal is about the build.
        r = _load(client, voice, *ENGLISH)
        assert r.status_code == 409, r.text
        assert r.json()["detail"] == TTS_RUNTIME_BROKEN
