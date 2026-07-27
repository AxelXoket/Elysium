"""Resemble Chatterbox (incl. Multilingual V3) - host half.

Signature verified against the real HF snapshot:
  t3_mtl23ls_v2.safetensors | t3_cfg.safetensors   <- the T3 backbone
  s3gen.pt / s3gen.safetensors, ve.pt / ve.safetensors, conds.pt
  grapheme_mtl_merged_expanded_v1.json             <- multilingual variant only

There is NO config.json here, which is what separates it from fish/xtts at
fingerprint time. MIT-licensed, clones from a short clip with no transcript,
and exposes an explicit emotion knob (`exaggeration`) that the others lack.
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
from ..util import first_match, has_any, size_of

_T3 = ("t3_*.safetensors", "t3_*.pt")
_S3GEN = ("s3gen.safetensors", "s3gen.pt")
_VE = ("ve.safetensors", "ve.pt")
_MTL_MARKERS = ("grapheme_mtl*.json", "t3_mtl*.safetensors")

_MTL_LANGS = ("ar", "da", "de", "el", "en", "es", "fi", "fr", "he", "hi", "it",
              "ja", "ko", "ms", "nl", "no", "pl", "pt", "ru", "sv", "sw", "tr", "zh")


class ChatterboxAdapter(TtsAdapter):
    engine_id = "chatterbox"
    display_name = "Chatterbox (Resemble)"
    priority = 30
    capabilities = EngineCapabilities(
        voice_cloning=True,
        needs_reference_transcript=False,
        inline_prosody_tags=False,
        streaming=False,
        # Clones from audio alone, so there is nothing to transcribe.
        transcribes_reference=False,
        languages=_MTL_LANGS,
        native_sample_rate=24000,
    )

    @classmethod
    def identify(cls, model_dir: Path) -> IdentifyResult | None:
        if not has_any(model_dir, _T3):
            return None
        # T3 alone could be someone else's file; require the vocoder+encoder pair.
        if not (has_any(model_dir, _S3GEN) or has_any(model_dir, _VE)):
            return None
        missing = []
        if not has_any(model_dir, _S3GEN):
            missing.append("s3gen.pt")
        if not has_any(model_dir, _VE):
            missing.append("ve.pt")
        variant = "multilingual" if has_any(model_dir, _MTL_MARKERS) else "english"
        return IdentifyResult(cls.engine_id, "signature", variant, tuple(missing))

    @classmethod
    def signature_files(cls, model_dir: Path) -> list[tuple[str, int]]:
        out: list[tuple[str, int]] = []
        for pats in (_T3, _S3GEN, _VE):
            hit = first_match(model_dir, pats)
            out.append((hit.name if hit else pats[0], size_of(hit) if hit else 0))
        return out

    @classmethod
    def describe_settings(cls, model: DetectedModel) -> list[ParamSpec]:
        multilingual = model.variant == "multilingual"
        specs = [
            ParamSpec("exaggeration", "float", 0.5, "Emotion intensity",
                      minimum=0.0, maximum=1.0, step=0.05, group="voice",
                      help="This engine's expressiveness dial. High values "
                           "destabilise the voice - 0.3-0.5 stays natural."),
            ParamSpec("cfg_weight", "float", 0.5, "Guidance", minimum=0.0,
                      maximum=1.0, step=0.05, group="quality",
                      help="Lower tracks the reference voice more loosely."),
            ParamSpec("temperature", "float", 0.8, "Expressiveness",
                      minimum=0.05, maximum=1.5, step=0.05, group="quality"),
            ParamSpec("repetition_penalty", "float", 2.0, "Repetition penalty",
                      minimum=1.0, maximum=10.0, step=0.1, group="quality",
                      advanced=True),
            ParamSpec("min_p", "float", 0.05, "Min-p", minimum=0.0, maximum=1.0,
                      step=0.01, group="quality", advanced=True),
            ParamSpec("top_p", "float", 1.0, "Top-p", minimum=0.1, maximum=1.0,
                      step=0.05, group="quality", advanced=True),
            ParamSpec("reference_voice", "voice_ref", "", "Reference voice",
                      group="voice", help="Short clip; no transcript needed."),
        ]
        if multilingual:
            specs.insert(0, ParamSpec("language_id", "enum", "en", "Language",
                                      choices=_MTL_LANGS, group="voice"))
        return specs

    @classmethod
    def languages_for(cls, model: DetectedModel) -> tuple[str, ...]:
        """The english build renders no language control at all, so the generic
        descriptor-derived answer would fall back to the 23-language family list
        and promise Turkish this checkpoint cannot produce."""
        return _MTL_LANGS if model.variant == "multilingual" else ("en",)

    @classmethod
    def estimate_vram_mb(cls, model: DetectedModel, values: dict) -> VramEstimate:
        # Measured previously on this hardware at ~3.6 GB fp32 peak.
        return VramEstimate(3800, "Chatterbox fp32 weights + working set")
