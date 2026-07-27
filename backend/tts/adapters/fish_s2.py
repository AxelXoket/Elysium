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
from ..util import has_any, read_json, size_of

_REQUIRED = ("config.json", "codec.pth")
_WEIGHT_PATTERNS = ("model*.safetensors", "model.safetensors.index.json", "*.pth")


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
        missing = [n for n in _REQUIRED if not (model_dir / n).is_file()]
        if not has_any(model_dir, _WEIGHT_PATTERNS):
            missing.append("model weights (*.safetensors)")
        return IdentifyResult(cls.engine_id, "signature", None, tuple(missing))

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
