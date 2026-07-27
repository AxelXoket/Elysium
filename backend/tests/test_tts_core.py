"""V1 - TTS discovery core (host half): param specs, fingerprinting, registry.

These tests run WITHOUT a GPU, without torch and without any engine installed.
That is the point of the host/worker split: everything here is pure stdlib, so
model detection and settings description stay unit-testable on any machine.

Fixtures build SYNTHETIC model folders whose file layout mirrors the real ones
(verified against the actual downloads):
  fish        config.json {"model_type": "fish_qwen3_omni"} + codec.pth + shards
  xtts_v2     config.json {"model": "xtts"} + model.pth + vocab.json
  chatterbox  t3_*.safetensors + s3gen.* + ve.* + conds.pt
"""
import json
import os

import pytest

from tts import errors as tts_errors
from tts.base import ParamSpec, ParamError
from tts.registry import scan_roots, identify_dir, adapter_for


# ── fixtures: synthetic model folders ────────────────────────────────────────

def _touch(p, data=b"x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def make_fish(root, name="s2-pro"):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text(
        json.dumps({"model_type": "fish_qwen3_omni", "dtype": "bfloat16"}),
        encoding="utf-8",
    )
    _touch(d / "codec.pth")
    _touch(d / "model-00001-of-00002.safetensors")
    _touch(d / "model-00002-of-00002.safetensors")
    _touch(d / "model.safetensors.index.json", b"{}")
    _touch(d / "tokenizer.json", b"{}")
    return d


def make_xtts(root, name="xtts_v2"):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    # NOTE: the real coqui config.json contains emoji - the adapter must read
    # it as utf-8, never the Windows default codepage.
    (d / "config.json").write_text(
        json.dumps({"model": "xtts", "languages": ["en", "tr", "es"], "note": "\U0001f438"}),
        encoding="utf-8",
    )
    _touch(d / "model.pth")
    _touch(d / "vocab.json", b"{}")
    _touch(d / "speakers_xtts.pth")
    return d


def make_chatterbox(root, name="chatterbox-mtl", multilingual=True):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    _touch(d / ("t3_mtl23ls_v2.safetensors" if multilingual else "t3_cfg.safetensors"))
    _touch(d / "s3gen.pt")
    _touch(d / "ve.pt")
    _touch(d / "conds.pt")
    _touch(d / "tokenizer.json", b"{}")
    if multilingual:
        _touch(d / "grapheme_mtl_merged_expanded_v1.json", b"{}")
    return d


# ── ParamSpec ────────────────────────────────────────────────────────────────

class TestParamSpec:
    def test_float_clamps_to_range(self):
        s = ParamSpec("temperature", "float", 0.7, "Temperature",
                      minimum=0.1, maximum=1.5)
        assert s.clamp(0.9) == 0.9
        assert s.clamp(9.0) == 1.5      # above max -> max
        assert s.clamp(-1) == 0.1       # below min -> min
        assert s.clamp("0.5") == 0.5    # numeric string coerces

    def test_int_coerces_and_clamps(self):
        s = ParamSpec("top_k", "int", 30, "Top K", minimum=1, maximum=100)
        assert s.clamp(50) == 50
        assert s.clamp(500) == 100
        assert isinstance(s.clamp("7"), int)

    def test_bool_and_enum(self):
        b = ParamSpec("split", "bool", True, "Split")
        assert b.clamp(False) is False
        assert b.clamp("true") is True
        e = ParamSpec("language", "enum", "en", "Language", choices=("en", "tr"))
        assert e.clamp("tr") == "tr"
        with pytest.raises(ParamError):
            e.clamp("de")               # not a choice -> explicit error

    def test_garbage_raises_param_error(self):
        s = ParamSpec("temperature", "float", 0.7, "Temperature",
                      minimum=0.0, maximum=2.0)
        with pytest.raises(ParamError):
            s.clamp("not a number")

    def test_to_json_is_serialisable_and_complete(self):
        s = ParamSpec("temperature", "float", 0.7, "Temperature",
                      help="Higher is more varied", minimum=0.1, maximum=1.5,
                      step=0.05, group="quality")
        j = s.to_json()
        json.dumps(j)                    # must survive the wire
        for k in ("name", "type", "default", "label", "minimum", "maximum", "group"):
            assert k in j


# ── fingerprinting ───────────────────────────────────────────────────────────

class TestIdentify:
    def test_identifies_each_engine(self, tmp_path):
        assert identify_dir(make_fish(tmp_path)).engine_id == "fish_s2"
        assert identify_dir(make_xtts(tmp_path)).engine_id == "xtts_v2"
        assert identify_dir(make_chatterbox(tmp_path)).engine_id == "chatterbox"

    def test_chatterbox_variant_detected(self, tmp_path):
        mtl = identify_dir(make_chatterbox(tmp_path, "cb-mtl", multilingual=True))
        assert mtl.variant == "multilingual"
        mono = identify_dir(make_chatterbox(tmp_path, "cb-en", multilingual=False))
        assert mono.variant != "multilingual"

    def test_unrelated_folder_is_not_identified(self, tmp_path):
        d = tmp_path / "vacation-photos"
        d.mkdir()
        _touch(d / "beach.jpg")
        assert identify_dir(d) is None

    def test_config_json_alone_is_not_enough(self, tmp_path):
        """A bare config.json must not be claimed - both fish and xtts have one."""
        d = tmp_path / "ambiguous"
        d.mkdir()
        (d / "config.json").write_text("{}", encoding="utf-8")
        assert identify_dir(d) is None

    def test_incomplete_model_is_identified_but_flagged(self, tmp_path):
        d = make_fish(tmp_path, "broken")
        os.remove(d / "codec.pth")          # engine still recognisable, load impossible
        res = identify_dir(d)
        assert res is not None and res.engine_id == "fish_s2"
        assert res.missing and "codec.pth" in res.missing

    def test_utf8_config_does_not_crash(self, tmp_path):
        """Real coqui config.json carries emoji; reading with the OS codepage
        raises UnicodeDecodeError on Windows."""
        assert identify_dir(make_xtts(tmp_path, "emoji")) is not None

    def test_sidecar_overrides_signature(self, tmp_path):
        d = make_fish(tmp_path, "override")
        (d / "elysium-model.json").write_text(
            json.dumps({"engine_id": "xtts_v2"}), encoding="utf-8"
        )
        res = identify_dir(d)
        assert res.engine_id == "xtts_v2"
        assert res.source == "sidecar"

    def test_bad_sidecar_is_ignored_not_fatal(self, tmp_path):
        d = make_fish(tmp_path, "badside")
        (d / "elysium-model.json").write_text("{ not json", encoding="utf-8")
        res = identify_dir(d)
        assert res.engine_id == "fish_s2"      # falls back to signature
        assert res.source == "signature"


# ── registry scan ────────────────────────────────────────────────────────────

class TestScan:
    def test_finds_all_models_and_reports_unrecognized(self, tmp_path):
        root = tmp_path / "models"
        make_fish(root)
        make_xtts(root)
        junk = root / "random-folder"
        junk.mkdir(parents=True)
        _touch(junk / "notes.txt")

        res = scan_roots([root])
        ids = {m.engine_id for m in res.models}
        assert ids == {"fish_s2", "xtts_v2"}
        assert any("random-folder" in u.path for u in res.unrecognized)

    def test_missing_root_is_not_an_error(self, tmp_path):
        res = scan_roots([tmp_path / "does-not-exist"])
        assert res.models == [] and res.unrecognized == []

    def test_uid_is_stable_across_scans(self, tmp_path):
        root = tmp_path / "models"
        make_fish(root)
        a = scan_roots([root]).models[0].uid
        b = scan_roots([root]).models[0].uid
        assert a == b and len(a) >= 8

    def test_uid_differs_between_models(self, tmp_path):
        root = tmp_path / "models"
        make_fish(root, "one")
        make_fish(root, "two")
        uids = {m.uid for m in scan_roots([root]).models}
        assert len(uids) == 2

    def test_nested_snapshot_layout_is_found(self, tmp_path):
        """HF caches nest as models--X--Y/snapshots/<rev>/ - the weights sit
        directly in the revision dir, i.e. depth 3 below the scan root."""
        root = tmp_path / "models"
        make_fish(root / "models--fishaudio--s2-pro" / "snapshots", "abc123")
        res = scan_roots([root])
        assert [m.engine_id for m in res.models] == ["fish_s2"]

    def test_container_dirs_are_not_reported_as_unrecognized(self, tmp_path):
        """models--X/ and snapshots/ are just containers on the way to a model;
        flagging them would spam the UI with false 'unrecognized' rows."""
        root = tmp_path / "models"
        make_fish(root / "models--fishaudio--s2-pro" / "snapshots", "abc123")
        res = scan_roots([root])
        assert res.unrecognized == []

    def test_adapter_lookup(self):
        assert adapter_for("fish_s2") is not None
        assert adapter_for("nope") is None


# ── error codes ──────────────────────────────────────────────────────────────

class TestAuditRegressions:
    """One guard per defect the V0-V2 audit confirmed. Each of these failed
    before its fix, so a regression here is a real regression."""

    def test_deeply_nested_sidecar_does_not_kill_the_scan(self, tmp_path):
        """A hostile elysium-model.json raises RecursionError inside json.load -
        a RuntimeError, NOT a ValueError. A narrow except let it escape and took
        every /tts endpoint down with an uncoded 500."""
        root = tmp_path / "models"
        make_fish(root, "good")
        bad = root / "poisoned"
        bad.mkdir(parents=True)
        _touch(bad / "config.json", b"{}")
        (bad / "elysium-model.json").write_text(
            "[" * 60000 + "]" * 60000, encoding="utf-8"
        )
        res = scan_roots([root])                     # must not raise
        assert [m.name for m in res.models] == ["good"]

    def test_oversized_metadata_is_ignored_not_loaded(self, tmp_path):
        from tts.util import MAX_METADATA_BYTES, read_json
        big = tmp_path / "huge.json"
        big.write_text("{" + '"k":"' + "x" * (MAX_METADATA_BYTES + 10) + '"}',
                       encoding="utf-8")
        assert read_json(big) is None

    def test_uid_survives_content_changes(self, tmp_path):
        """Finishing a partial download must not orphan the user's settings."""
        root = tmp_path / "models"
        d = make_fish(root, "dl")
        before = scan_roots([root]).models[0].uid
        (d / "model-00001-of-00002.safetensors").write_bytes(b"x" * 4096)
        assert scan_roots([root]).models[0].uid == before

    def test_uid_survives_engine_override(self, tmp_path):
        """Re-identifying the same folder must keep its settings attached."""
        root = tmp_path / "models"
        d = make_fish(root, "switch")
        before = scan_roots([root]).models[0].uid
        (d / "elysium-model.json").write_text(
            json.dumps({"engine_id": "xtts_v2"}), encoding="utf-8"
        )
        after = scan_roots([root]).models[0]
        assert after.uid == before and after.engine_id == "xtts_v2"

    def test_same_named_models_in_different_folders_get_different_uids(self, tmp_path):
        root = tmp_path / "models"
        make_fish(root / "a", "s2-pro")
        make_fish(root / "b", "s2-pro")
        uids = {m.uid for m in scan_roots([root]).models}
        assert len(uids) == 2, "identically-named copies collided onto one id"

    def test_uncorroborated_override_is_not_reported_as_complete(self, tmp_path):
        """Forcing an engine the files do not support must not fabricate a
        ready-to-load model; the user would only discover it at load time."""
        root = tmp_path / "models"
        d = make_fish(root, "forced")
        (d / "elysium-model.json").write_text(
            json.dumps({"engine_id": "chatterbox"}), encoding="utf-8"
        )
        m = scan_roots([root]).models[0]
        assert m.engine_id == "chatterbox"
        assert m.incomplete and m.missing

    def test_hf_cache_siblings_are_not_flagged_unrecognized(self, tmp_path):
        """blobs/ and refs/ are normal next to snapshots/ - flagging them would
        put permanent false noise in the UI."""
        root = tmp_path / "models"
        cache = root / "models--fishaudio--s2-pro"
        make_fish(cache / "snapshots", "abc123")
        for sibling in ("blobs", "refs"):
            d = cache / sibling
            d.mkdir(parents=True)
            _touch(d / "deadbeef")
        res = scan_roots([root])
        assert len(res.models) == 1
        assert res.unrecognized == []

    def test_scan_depth_limit_is_a_known_boundary(self, tmp_path):
        """Documents the limit rather than leaving it to surprise someone: a
        model deeper than TTS_SCAN_MAX_DEPTH is not found."""
        import config
        root = tmp_path / "models"
        deep = root
        for i in range(config.TTS_SCAN_MAX_DEPTH + 1):
            deep = deep / f"lvl{i}"
        make_fish(deep, "too-deep")
        assert scan_roots([root]).models == []

    def test_xtts_default_language_is_always_a_valid_choice(self, tmp_path):
        """When a model's own config.json omits English, a hardcoded "en"
        default sits outside its choices and every clamp() raises - the whole
        settings page for that model becomes unopenable."""
        from tts.base import DetectedModel
        from tts.adapters.xtts_v2 import XttsV2Adapter
        d = tmp_path / "xtts-noeng"
        d.mkdir(parents=True)
        (d / "config.json").write_text(
            json.dumps({"model": "xtts", "languages": ["tr", "de"]}), encoding="utf-8"
        )
        _touch(d / "model.pth")
        _touch(d / "vocab.json", b"{}")
        model = DetectedModel("u", "xtts_v2", d.name, str(d))
        spec = next(s for s in XttsV2Adapter.describe_settings(model)
                    if s.name == "language")
        assert spec.default in spec.choices
        assert spec.clamp(spec.default) == spec.default   # would raise before


class TestErrorCodes:
    def test_every_code_is_snake_case_and_prefixed(self):
        codes = tts_errors.ALL_CODES
        assert codes, "no codes exported"
        for c in codes:
            assert c.startswith("tts_"), c
            assert c == c.lower() and " " not in c, c

    def test_codes_match_the_backend_vocabulary(self):
        """The real four-place enforcement lives in test_tts_contract.py, which
        opens the actual frontend and docs files. This asserts only the backend
        half - every TTS_ constant is in ALL_CODES - and exists to fail fast
        with a readable message when someone adds one and forgets the set.
        (Its earlier version compared errors.py with itself and could not fail.)"""
        declared = {v for k, v in vars(tts_errors).items()
                    if k.startswith("TTS_") and isinstance(v, str)}
        assert declared == set(tts_errors.ALL_CODES)

