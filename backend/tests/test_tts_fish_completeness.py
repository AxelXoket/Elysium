"""Fish S2 Pro checkpoint completeness: the host verdict and the worker refusal.

The bug this file exists to prevent read as complete a folder that could not
speak. `*.pth` in the old weight patterns was satisfied by `codec.pth`, which
is the DAC codec and already required on its own, so a folder with no
text2semantic weights at all (or with shard 1 of 2) passed the check and the
engine crashed later with a bare FileNotFoundError. The index the download
ships names its shards, and nothing read it. Both halves now do, and this file
holds the two copies of the rule to the same answer on every layout, which is
what lets the app's "download did not finish" verdict and the worker's refusal
name the same files.

Layouts are built in tmp_path after the real download (checkpoints/s2-pro):
config.json, codec.pth, model-00001-of-00002.safetensors,
model-00002-of-00002.safetensors, model.safetensors.index.json,
tokenizer.json, tokenizer_config.json, special_tokens_map.json,
chat_template.jinja. Every file is a stand-in byte: the check reads names and
the index, never weights. The malformed-index layouts are synthetic, one per
rule in `shard_names`, so that a rule dropped from either copy is a red test
and not a folder that reads as complete.
"""
import json
import os
from types import SimpleNamespace

import pytest

import fish_synth_harness as synth
from tts.adapters.fish_s2 import (
    MISSING_WEIGHTS,
    REQUIRED_FILES,
    SINGLE_WEIGHT_FILES,
    WEIGHT_INDEX,
    FishS2Adapter,
    missing_files,
    shard_names,
)
from tts.util import MAX_METADATA_BYTES

SHARDS = ("model-00001-of-00002.safetensors", "model-00002-of-00002.safetensors")
#: Twelve names for the sort proof. `shard_names` collects into a set, so a
#: dropped `sorted` hands back set order, which is sorted by luck about one
#: run in two with two names and one in six with three (measured over 300
#: hash seeds: 49% and 17%); with twelve it was 0 of 300.
MANY_SHARDS = tuple(f"model-{i:05d}-of-00012.safetensors" for i in range(1, 13))
TOKENIZER_PAIR = ("tokenizer.json", "tokenizer_config.json")
#: In the real download, read by nothing: transformers tolerates their absence
#: and fish_speech never opens them. Their presence or absence must not move
#: the verdict, and the layouts below prove both directions.
OPTIONAL = ("special_tokens_map.json", "chat_template.jinja")


def _index_for(shards) -> str:
    # Two tensors per shard: a map with several tensors on one shard must
    # still yield each shard name exactly once.
    weight_map = {}
    for i, shard in enumerate(shards):
        weight_map[f"layers.{i}.weight"] = shard
        weight_map[f"layers.{i}.bias"] = shard
    return json.dumps({"metadata": {"total_size": 0}, "weight_map": weight_map})


def _folder(tmp_path, name, files=(), *, index_shards=None, index_text=None):
    d = tmp_path / name
    d.mkdir()
    for f in files:
        if f == "config.json":
            (d / f).write_text(json.dumps({"model_type": "fish_qwen3_omni"}),
                               encoding="utf-8")
        else:
            (d / f).write_bytes(b"x")
    if index_shards is not None:
        (d / WEIGHT_INDEX).write_text(_index_for(index_shards), encoding="utf-8")
    if index_text is not None:
        (d / WEIGHT_INDEX).write_text(index_text, encoding="utf-8")
    return d


def _index_of_size(shards, size: int) -> str:
    """A valid index naming `shards`, padded to exactly `size` bytes, so the
    two read-cap layouts differ by one byte and nothing else."""
    doc = json.loads(_index_for(shards))
    doc["metadata"]["padding"] = ""
    bare = len(json.dumps(doc).encode("utf-8"))
    doc["metadata"]["padding"] = "x" * (size - bare)
    text = json.dumps(doc)
    assert len(text.encode("utf-8")) == size
    return text


def _shard_in_a_subfolder(tmp_path):
    """The index names `sub/model.safetensors` and that file EXISTS: the
    refusal is the rule, not a missing file, and a dropped name check would
    read this folder as complete."""
    d = _folder(tmp_path, "subfolder", REQUIRED_FILES + SHARDS,
                index_text=json.dumps({"weight_map": {"w": "sub/model.safetensors"}}))
    (d / "sub").mkdir()
    (d / "sub" / "model.safetensors").write_bytes(b"x")
    return d


def real_download(tmp_path, name="s2-pro"):
    """The complete sharded download, file for file."""
    return _folder(tmp_path, name, REQUIRED_FILES + SHARDS + OPTIONAL,
                   index_shards=SHARDS)


#: name -> (builder, the exact `missing` tuple both halves must report)
LAYOUTS = {
    "complete_sharded": (
        real_download, ()),
    "complete_single_file": (
        lambda t: _folder(t, "single", REQUIRED_FILES + ("model.safetensors",)),
        ()),
    "optional_files_absent": (
        lambda t: _folder(t, "noopt", REQUIRED_FILES + SHARDS, index_shards=SHARDS),
        ()),
    "model_pth_is_the_engines_third_form": (
        lambda t: _folder(t, "pth", REQUIRED_FILES + ("model.pth",)),
        ()),
    "index_with_one_shard_missing": (
        lambda t: _folder(t, "half", REQUIRED_FILES + SHARDS[:1] + OPTIONAL,
                          index_shards=SHARDS),
        (SHARDS[1],)),
    "index_with_every_shard_missing": (
        lambda t: _folder(t, "none", REQUIRED_FILES + OPTIONAL, index_shards=SHARDS),
        SHARDS),
    "no_index_and_no_weights": (
        lambda t: _folder(t, "bare", REQUIRED_FILES + OPTIONAL),
        (MISSING_WEIGHTS,)),
    "config_and_codec_only": (
        lambda t: _folder(t, "twofiles", ("config.json", "codec.pth")),
        TOKENIZER_PAIR + (MISSING_WEIGHTS,)),
    "codec_pth_only": (
        lambda t: _folder(t, "codec", ("codec.pth",)),
        ("config.json",) + TOKENIZER_PAIR + (MISSING_WEIGHTS,)),
    "tokenizer_pair_missing": (
        lambda t: _folder(t, "notok", ("config.json", "codec.pth") + SHARDS + OPTIONAL,
                          index_shards=SHARDS),
        TOKENIZER_PAIR),
    "index_cut_mid_file": (
        lambda t: _folder(t, "cut", REQUIRED_FILES + SHARDS,
                          index_text='{"weight_map": {"layers.0.weight": "model-0000'),
        (WEIGHT_INDEX,)),
    "index_without_a_weight_map": (
        lambda t: _folder(t, "empty", REQUIRED_FILES + SHARDS, index_text="{}"),
        (WEIGHT_INDEX,)),
    "index_pointing_outside_the_folder": (
        lambda t: _folder(t, "escape", REQUIRED_FILES + SHARDS,
                          index_text=json.dumps({"weight_map": {"w": "../codec.pth"}})),
        (WEIGHT_INDEX,)),
    # One layout per rule in `shard_names`, each with every required file and
    # both shards present, so the index is the only thing left to report.
    "index_that_is_a_list_not_a_document": (
        lambda t: _folder(t, "listdoc", REQUIRED_FILES + SHARDS, index_text="[]"),
        (WEIGHT_INDEX,)),
    "index_whose_weight_map_is_a_list": (
        lambda t: _folder(t, "listmap", REQUIRED_FILES + SHARDS,
                          index_text=json.dumps({"weight_map": list(SHARDS)})),
        (WEIGHT_INDEX,)),
    "index_with_an_empty_weight_map": (
        lambda t: _folder(t, "emptymap", REQUIRED_FILES + SHARDS,
                          index_text=json.dumps({"weight_map": {}})),
        (WEIGHT_INDEX,)),
    "index_with_a_number_for_a_shard": (
        lambda t: _folder(t, "number", REQUIRED_FILES + SHARDS,
                          index_text=json.dumps({"weight_map": {"w": 1}})),
        (WEIGHT_INDEX,)),
    "index_with_an_empty_string_for_a_shard": (
        lambda t: _folder(t, "emptyname", REQUIRED_FILES + SHARDS,
                          index_text=json.dumps({"weight_map": {"w": ""}})),
        (WEIGHT_INDEX,)),
    "index_naming_the_parent_folder": (
        lambda t: _folder(t, "parent", REQUIRED_FILES + SHARDS,
                          index_text=json.dumps({"weight_map": {"w": ".."}})),
        (WEIGHT_INDEX,)),
    "index_naming_a_shard_in_a_subfolder": (
        _shard_in_a_subfolder, (WEIGHT_INDEX,)),
    "index_with_twelve_shards_all_missing": (
        lambda t: _folder(t, "twelve", REQUIRED_FILES, index_shards=MANY_SHARDS),
        MANY_SHARDS),
    # The host's `read_json` cap, one byte either side of it: at the cap the
    # index is read and the folder is complete, above it the index is "not
    # an index" on both halves.
    "index_at_the_read_cap": (
        lambda t: _folder(t, "atcap", REQUIRED_FILES + SHARDS,
                          index_text=_index_of_size(SHARDS, MAX_METADATA_BYTES)),
        ()),
    "index_above_the_read_cap": (
        lambda t: _folder(t, "overcap", REQUIRED_FILES + SHARDS,
                          index_text=_index_of_size(SHARDS, MAX_METADATA_BYTES + 1)),
        (WEIGHT_INDEX,)),
}


@pytest.fixture(params=sorted(LAYOUTS))
def layout(request, tmp_path):
    build, expected = LAYOUTS[request.param]
    return request.param, build(tmp_path), expected


class TestHostVerdict:
    def test_every_layout_reports_exactly_what_is_missing(self, layout):
        name, d, expected = layout
        assert missing_files(d) == expected, name

    def test_identify_carries_the_same_verdict(self, layout):
        name, d, expected = layout
        res = FishS2Adapter.identify(d)
        if "config.json" in expected:
            # Without config.json the folder is not recognisably ours at all;
            # that is the registry's "unrecognized" lane, not "incomplete".
            assert res is None, name
        else:
            assert res is not None and res.engine_id == "fish_s2", name
            assert res.missing == expected, name
            assert res.incomplete is bool(expected), name

    def test_the_dac_codec_never_stands_in_for_the_weights(self, tmp_path):
        """The original bug. `codec.pth` is required in its own right and
        must not satisfy the weights check; `model.safetensors` beside it is
        the positive control that the check is looking at the right files."""
        assert "codec.pth" not in SINGLE_WEIGHT_FILES
        d = _folder(tmp_path, "codec", REQUIRED_FILES)
        assert missing_files(d) == (MISSING_WEIGHTS,)
        (d / "model.safetensors").write_bytes(b"x")
        assert missing_files(d) == ()

    def test_a_shard_the_index_names_is_reported_by_that_name(self, tmp_path):
        """Shard 1 of 2 used to read as complete. The name in `missing` is
        the file name from the index, so the verdict says which file to
        finish downloading; the complete folder is the positive control."""
        d = _folder(tmp_path, "half", REQUIRED_FILES + SHARDS[:1],
                    index_shards=SHARDS)
        assert missing_files(d) == (SHARDS[1],)
        (d / SHARDS[1]).write_bytes(b"x")
        assert missing_files(d) == ()

    def test_the_tokenizer_pair_is_required_and_the_other_two_are_not(self, tmp_path):
        """transformers.AutoTokenizer needs `tokenizer.json` and
        `tokenizer_config.json` from the folder offline (measured on 4.56.1,
        one layout per missing file), and fish_speech turns their absence
        into a warning at load and a crash on the first sentence. The other
        two files in the download are read by nothing."""
        d = _folder(tmp_path, "tok", ("config.json", "codec.pth") + SHARDS,
                    index_shards=SHARDS)
        assert missing_files(d) == TOKENIZER_PAIR
        for name in TOKENIZER_PAIR:
            (d / name).write_bytes(b"x")
        assert missing_files(d) == ()
        for name in OPTIONAL:
            (d / name).write_bytes(b"x")
        assert missing_files(d) == ()

    def test_shard_names_are_unique_sorted_and_only_bare_file_names(self):
        # Listed backwards with two tensors per shard: the answer is each
        # name once, in sorted order, whatever order the map came in.
        backwards = {}
        for i, shard in enumerate(reversed(MANY_SHARDS)):
            backwards[f"layers.{i}.weight"] = shard
            backwards[f"layers.{i}.bias"] = shard
        assert shard_names({"weight_map": backwards}) == sorted(MANY_SHARDS)
        for not_an_index in (None, [], "text", {}, {"weight_map": {}},
                             {"weight_map": []}, {"weight_map": {"a": 1}},
                             {"weight_map": {"a": ""}},
                             {"weight_map": {"a": "sub/model.safetensors"}},
                             {"weight_map": {"a": "../model.safetensors"}},
                             {"weight_map": {"a": "."}},
                             {"weight_map": {"a": ".."}}):
            assert shard_names(not_an_index) is None, not_an_index

    def test_a_backslash_is_a_separator_only_where_the_os_says_so(self):
        """`Path(value).name` follows the host's own rules. On Windows a
        backslash splits the path, so the value is not a bare name and the
        index is refused; on POSIX it is an ordinary character, a file by
        that name sits inside the folder, and there is nothing to refuse."""
        verdict = shard_names({"weight_map": {"a": "..\\model.safetensors"}})
        if os.name == "nt":
            assert verdict is None
        else:
            assert verdict == ["..\\model.safetensors"]


def _worker():
    return synth.load_worker()


class TestWorkerAgrees:
    """The worker may import nothing from the app, so it carries its own copy
    of the rule. These tests are the contract that keeps the copies equal."""

    def test_every_layout_gets_the_same_answer_from_both_halves(self, layout):
        name, d, expected = layout
        mod = _worker()
        assert tuple(mod._missing_files(d)) == expected, name
        assert tuple(mod._missing_files(d)) == missing_files(d), name

    def test_the_constants_are_the_same_on_both_sides(self):
        mod = _worker()
        assert mod._REQUIRED_FILES == REQUIRED_FILES
        assert mod._WEIGHT_INDEX == WEIGHT_INDEX
        assert mod._SINGLE_WEIGHT_FILES == SINGLE_WEIGHT_FILES
        assert mod._MISSING_WEIGHTS == MISSING_WEIGHTS
        assert mod._MAX_INDEX_BYTES == MAX_METADATA_BYTES

    def test_load_refuses_an_incomplete_folder_before_importing_the_engine(
        self, tmp_path
    ):
        """A missing shard is a download verdict, not a runtime one: the
        refusal names the shard, carries the app's failure code, and happens
        before torch is asked for (an `_engine()` call here fails the test)."""
        d = _folder(tmp_path, "half", REQUIRED_FILES + SHARDS[:1],
                    index_shards=SHARDS)
        mod = _worker()
        mod._engine = lambda: pytest.fail(
            "the worker imported the engine for a folder it should have refused")
        events = []
        with pytest.raises(mod._wire.WorkerError) as exc:
            mod._op_load({"model_path": str(d), "values": {}}, events.append)
        assert exc.value.code == mod._wire.CODE_WORKER_FAILED
        assert SHARDS[1] in exc.value.detail
        assert SHARDS[0] not in exc.value.detail
        for name in missing_files(d):
            assert name in exc.value.detail

    def test_load_refuses_the_folder_the_old_check_let_through(self, tmp_path):
        """config.json + codec.pth alone reached fish_speech and died there
        with a bare FileNotFoundError. Now the refusal names everything the
        host would list, in the host's words."""
        d = _folder(tmp_path, "twofiles", ("config.json", "codec.pth"))
        mod = _worker()
        mod._engine = lambda: pytest.fail("the engine was imported for a bare folder")
        with pytest.raises(mod._wire.WorkerError) as exc:
            mod._op_load({"model_path": str(d), "values": {}}, lambda e: None)
        assert exc.value.code == mod._wire.CODE_WORKER_FAILED
        assert MISSING_WEIGHTS in exc.value.detail
        for name in TOKENIZER_PAIR:
            assert name in exc.value.detail

    def test_a_complete_folder_gets_past_the_check_to_the_engine(self, tmp_path):
        """The positive control for the two refusals above: the real
        download's layout reaches `_build_model`. The stub raises a sentinel
        WorkerError, which `_op_load` re-raises untouched, so reaching it is
        observable without a model."""
        d = real_download(tmp_path)
        mod = _worker()
        cuda = SimpleNamespace(is_available=lambda: True,
                               empty_cache=lambda: None,
                               synchronize=lambda: None)
        mod._ENGINE.update(torch=SimpleNamespace(cuda=cuda))
        reached = []

        def build(ckpt, kv_len, send):
            reached.append(str(ckpt))
            raise mod._wire.WorkerError("sentinel-reached-build", "stub")

        mod._build_model = build
        with pytest.raises(mod._wire.WorkerError) as exc:
            mod._op_load({"model_path": str(d), "values": {}}, lambda e: None)
        assert exc.value.code == "sentinel-reached-build"
        assert reached == [str(d)]

    def test_a_missing_directory_is_still_its_own_verdict(self, tmp_path):
        """The directory check stays ahead of the file check, so a wrong
        path says "no such directory" rather than listing every file."""
        mod = _worker()
        gone = tmp_path / "not-here"
        with pytest.raises(mod._wire.WorkerError) as exc:
            mod._op_load({"model_path": str(gone), "values": {}}, lambda e: None)
        assert exc.value.code == mod._wire.CODE_WORKER_FAILED
        assert "no such model directory" in exc.value.detail
        assert MISSING_WEIGHTS not in exc.value.detail
