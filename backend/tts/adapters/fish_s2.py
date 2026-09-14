"""Fish Audio S2 Pro - host half.

Signature verified against the real download (checkpoints/s2-pro):
  config.json {"model_type": "fish_qwen3_omni", ...}   <- the decisive marker
  codec.pth                                            <- DAC/firefly codec
  model-0000N-of-0000M.safetensors + model.safetensors.index.json
  tokenizer.json, chat_template.jinja

Notable capability: S2 Pro reads FREE-FORM inline [tag] directions
("[whisper]", "[slow, intimate tone]") for word-level prosody control, which is
what the voice-delivery prompt layer depends on. Cloning needs the reference
TRANSCRIPT as well as the audio - audio alone does not enable it.
"""
from __future__ import annotations

from pathlib import Path

from ..base import (
    DetectedModel,
    EngineCapabilities,
    IdentifyResult,
    ParamSpec,
    TtsAdapter,
    VramEstimate,
)
from ..util import read_json, size_of

# The files fish_speech opens at load, by the names the download uses.
#
# `config.json` and `codec.pth` are the engine's own. The tokenizer pair is
# there because `FishTokenizer` is `transformers.AutoTokenizer` pointed at the
# model folder: without `tokenizer_config.json` transformers falls through to
# `config.json`, meets a `model_type` it has never heard of and refuses;
# without `tokenizer.json` the fast tokenizer has no vocabulary to build from.
# Measured against transformers 4.56.1 offline, one synthetic layout per
# missing file. fish_speech SWALLOWS that failure at load (a warning and
# `model.tokenizer = None`, llama.py `from_pretrained`) and then dereferences
# the tokenizer on the first sentence (inference.py `generate_long`), so a
# folder without the pair loads "fine" and cannot speak. Naming the pair here
# is what turns that into a download verdict instead of a crash at synthesis.
# `special_tokens_map.json` and `chat_template.jinja` ship in the real download
# but are optional: transformers tolerates their absence and nothing in
# fish_speech reads them.
REQUIRED_FILES = ("config.json", "codec.pth", "tokenizer.json",
                  "tokenizer_config.json")
WEIGHT_INDEX = "model.safetensors.index.json"
#: The single-file forms fish_speech falls back to when there is no index, in
#: the order it tries them. `codec.pth` is deliberately NOT in this list and
#: no glob stands in for it: the old `*.pth` pattern was satisfied by the DAC
#: codec, which is already required above, so a folder with no text2semantic
#: weights at all read as complete.
SINGLE_WEIGHT_FILES = ("model.safetensors", "model.pth")
#: The `missing` entry for a folder with neither an index nor a single file.
#: Names what the S2 Pro download contains, so the fix reads as "finish the
#: download" and not as a hunt for a file that was never part of it.
MISSING_WEIGHTS = ("model weights (model.safetensors.index.json with its "
                   "shards, or model.safetensors)")
# Two places where `shard_names` is stricter than the loader on purpose, kept
# here so nobody "fixes" them back toward it:
# 1. An empty `weight_map` is "not an index" here, although the loader would
#    accept it: `from_pretrained` ends in `load_state_dict(weights,
#    strict=False)` (fish_speech llama.py), so an index that maps nothing
#    loads a model with random weights. A download that produced it is
#    broken, and "index missing" is the verdict that makes the fix a
#    re-download.
# 2. A `weight_map` value with a subfolder in it (`sub/model.safetensors`) is
#    refused, although the loader would open it (`path_obj / shard`),
#    because no download the app targets ships that layout, and a value that
#    can name a path outside the folder is not worth the exception.
# The index itself comes through `read_json` (tts/util.py), which gives up
# above MAX_METADATA_BYTES (4 MiB). The worker's copy retypes that cap, so an
# oversized index is "not an index" on both sides. The real one is 32 KB.


def shard_names(index: object) -> list[str] | None:
    """The shard files an index maps its tensors onto, sorted and unique.

    None when the document is not a shard index: not a dict, no `weight_map`,
    an empty one, or a value that is not a bare file name. A model folder is
    untrusted input the user dropped in, and a `weight_map` entry with a path
    separator in it would send the check (and fish_speech) outside the folder.
    """
    if not isinstance(index, dict):
        return None
    weight_map = index.get("weight_map")
    if not isinstance(weight_map, dict) or not weight_map:
        return None
    names: set[str] = set()
    for value in weight_map.values():
        if (not isinstance(value, str) or not value
                or Path(value).name != value or value in (".", "..")):
            return None
        names.add(value)
    return sorted(names)


def missing_weights(model_dir: Path) -> list[str]:
    """Which text2semantic weight files are absent, by the engine's own rules.

    Mirrors `DualARTransformer.from_pretrained` (fish_speech llama.py): when
    `model.safetensors.index.json` exists every shard its `weight_map` names is
    opened, otherwise `model.safetensors`, otherwise `model.pth`, otherwise
    FileNotFoundError. So with an index the answer is the shards that are not
    there, by name; an index that is present but not a shard index (a download
    cut mid-file, or a foreign file under the name) is reported under its own
    name, because the engine crashes on it and "missing" is the verdict that
    makes the fix a re-download.
    """
    index = model_dir / WEIGHT_INDEX
    if index.is_file():
        shards = shard_names(read_json(index))
        if shards is None:
            return [WEIGHT_INDEX]
        return [s for s in shards if not (model_dir / s).is_file()]
    if any((model_dir / n).is_file() for n in SINGLE_WEIGHT_FILES):
        return []
    return [MISSING_WEIGHTS]


def missing_files(model_dir: Path) -> tuple[str, ...]:
    """Every file the engine will fail without, in a fixed order.

    The worker's `_missing_files` (tts/worker/fish_s2.py) is this function
    written out again for an interpreter that cannot import the app; the test
    suite holds the two to the same answer on every layout, which is what lets
    the app's "download did not finish" verdict and the worker's refusal name
    the same files.
    """
    missing = [n for n in REQUIRED_FILES if not (model_dir / n).is_file()]
    missing.extend(missing_weights(model_dir))
    return tuple(missing)


class FishS2Adapter(TtsAdapter):
    engine_id = "fish_s2"
    display_name = "Fish Audio S2 Pro"
    priority = 10
    capabilities = EngineCapabilities(
        voice_cloning=True,
        needs_reference_transcript=True,   # use_prompt needs text AND tokens
        inline_prosody_tags=True,
        streaming=False,                   # worker v1 synthesises per utterance
        # Whisper is not part of the S2 runtime; OP_TRANSCRIBE is refused.
        transcribes_reference=False,
        languages=("en", "zh", "ja", "ko", "de", "fr", "es", "it", "pt",
                   "ru", "ar", "tr"),
        native_sample_rate=44100,
    )

    @classmethod
    def identify(cls, model_dir: Path) -> IdentifyResult | None:
        cfg = read_json(model_dir / "config.json")
        if not cfg:
            return None
        model_type = str(cfg.get("model_type") or "")
        if not model_type.startswith("fish_"):
            return None
        return IdentifyResult(cls.engine_id, "signature", None,
                              missing_files(model_dir))

    @classmethod
    def signature_files(cls, model_dir: Path) -> list[tuple[str, int]]:
        names = ["config.json", "codec.pth", "model.safetensors.index.json"]
        return [(n, size_of(model_dir / n)) for n in names]

    @classmethod
    def describe_settings(cls, model: DetectedModel) -> list[ParamSpec]:
        # NOTE (verified by reading the engine, not assumed): fish-speech
        # accepts `repetition_penalty` at every layer and then never applies it
        # to the logits - the real repetition control is the hardcoded
        # Repetition-Aware Sampling inside decode_one_token_ar. A dial that
        # cannot move anything is worse than no dial, so it is not offered.
        # top_p 0.8 is the engine's OWN schema/webui default; 0.9 came from a
        # bake-off script, not from the project.
        langs = cls.capabilities.languages
        return [
            ParamSpec("language", "enum", "en", "Language", choices=langs,
                      group="voice",
                      help="Turkish is supported but noticeably weaker than English."),
            ParamSpec("temperature", "float", 0.7, "Expressiveness",
                      minimum=0.1, maximum=1.5, step=0.05, group="quality",
                      help="Higher is more varied; too high causes wobble."),
            ParamSpec("top_p", "float", 0.8, "Top-p", minimum=0.1, maximum=1.0,
                      step=0.05, group="quality", advanced=True),
            ParamSpec("top_k", "int", 30, "Top-k", minimum=1, maximum=200,
                      group="quality", advanced=True),
            ParamSpec("max_new_tokens", "int", 800, "Max length",
                      minimum=64, maximum=2048, group="limits", advanced=True,
                      help="Hard stop so a runaway generation cannot hang the voice."),
            ParamSpec("kv_cache_len", "int", 2048, "Context window",
                      minimum=512, maximum=8192, group="limits", advanced=True,
                      help="The model's own default (32768) allocates several GB "
                           "of KV cache that a single sentence never needs."),
            ParamSpec("reference_voice", "voice_ref", "", "Reference voice",
                      group="voice",
                      help="A short clip plus its transcript. Cloning does not "
                           "work from audio alone on this engine."),
        ]

    @classmethod
    def estimate_vram_mb(cls, model: DetectedModel, values: dict) -> VramEstimate:
        # Measured on an RTX 5080: ~6.9 GB resident for the fp8-quantised model
        # plus generation working set peaking near 14.5 GB with a 2048 KV cache.
        kv = int(values.get("kv_cache_len", 2048) or 2048)
        base = 7000
        kv_mb = int(kv / 2048 * 1200)
        return VramEstimate(base + kv_mb + 2000,
                            "fp8 weights + KV cache + generation working set")
