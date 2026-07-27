"""Coqui XTTS-v2 - host half.

Signature verified against the real coqui cache:
  config.json {"model": "xtts", "languages": [... 17 ...]}   <- decisive marker
  model.pth, vocab.json, speakers_xtts.pth

Clones from ~6 s of reference audio and needs NO transcript. Turkish is a
first-class listed language here (17 langs incl. "tr"), which is why it stays
in the set even though it is a 2023-era model.
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

_REQUIRED = ("config.json", "model.pth", "vocab.json")
_FALLBACK_LANGS = ("en", "es", "fr", "de", "it", "pt", "pl", "tr", "ru", "nl",
                   "cs", "ar", "zh-cn", "ja", "hu", "ko", "hi")


class XttsV2Adapter(TtsAdapter):
    engine_id = "xtts_v2"
    display_name = "XTTS-v2 (Coqui)"
    priority = 20
    capabilities = EngineCapabilities(
        voice_cloning=True,
        needs_reference_transcript=False,   # audio alone is enough here
        inline_prosody_tags=False,          # no [tag] vocabulary
        streaming=True,
        # No ASR in the XTTS runtime; OP_TRANSCRIBE is refused.
        transcribes_reference=False,
        languages=_FALLBACK_LANGS,
        native_sample_rate=24000,
    )

    @classmethod
    def identify(cls, model_dir: Path) -> IdentifyResult | None:
        cfg = read_json(model_dir / "config.json")
        if not cfg:
            return None
        if str(cfg.get("model") or "").lower() != "xtts":
            return None
        missing = tuple(n for n in _REQUIRED if not (model_dir / n).is_file())
        return IdentifyResult(cls.engine_id, "signature", None, missing)

    @classmethod
    def signature_files(cls, model_dir: Path) -> list[tuple[str, int]]:
        return [(n, size_of(model_dir / n)) for n in _REQUIRED]

    @classmethod
    def _languages(cls, model: DetectedModel) -> tuple[str, ...]:
        cfg = read_json(Path(model.path) / "config.json") or {}
        langs = cfg.get("languages")
        if isinstance(langs, list) and langs:
            return tuple(str(x) for x in langs)
        return _FALLBACK_LANGS

    @classmethod
    def describe_settings(cls, model: DetectedModel) -> list[ParamSpec]:
        langs = cls._languages(model)
        # The choices come from the model's OWN config.json, so "en" is not
        # guaranteed to be among them. A default outside its own choice list
        # makes every clamp() of that field raise - the settings page would be
        # unopenable for that model.
        default_lang = "en" if "en" in langs else (langs[0] if langs else "en")
        return [
            ParamSpec("language", "enum", default_lang, "Language",
                      choices=langs or ("en",), group="voice"),
            ParamSpec("temperature", "float", 0.65, "Expressiveness",
                      minimum=0.05, maximum=1.5, step=0.05, group="quality",
                      help="Lower is steadier; high values cause pitch wobble."),
            ParamSpec("repetition_penalty", "float", 2.0, "Repetition penalty",
                      minimum=1.0, maximum=15.0, step=0.5, group="quality"),
            ParamSpec("top_k", "int", 50, "Top-k", minimum=1, maximum=100,
                      group="quality", advanced=True),
            ParamSpec("top_p", "float", 0.85, "Top-p", minimum=0.1, maximum=1.0,
                      step=0.05, group="quality", advanced=True),
            ParamSpec("length_penalty", "float", 1.0, "Length penalty",
                      minimum=0.5, maximum=2.0, step=0.1, group="quality",
                      advanced=True),
            ParamSpec("speed", "float", 1.0, "Speed", minimum=0.5, maximum=1.5,
                      step=0.05, group="voice"),
            ParamSpec("enable_text_splitting", "bool", True, "Split long text",
                      group="quality",
                      help="Splits into sentences before synthesis; steadier output."),
            ParamSpec("reference_voice", "voice_ref", "", "Reference voice",
                      group="voice", help="About 6 seconds is enough; no transcript needed."),
        ]

    @classmethod
    def estimate_vram_mb(cls, model: DetectedModel, values: dict) -> VramEstimate:
        return VramEstimate(2600, "XTTS-v2 weights + generation working set")
