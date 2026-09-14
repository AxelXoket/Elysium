"""Resemble Chatterbox - host half.

Two builds the bundled worker (chatterbox_tts 0.1.7) can open, read off the
T3 backbone's file name, and the files each one needs. Verified against the
library's own from_local() and against the real HF snapshot:

  english       t3_cfg.safetensors         ve.safetensors  s3gen.safetensors
                tokenizer.json
  multilingual  t3_mtl23ls_v2.safetensors  ve.pt           s3gen.pt
                grapheme_mtl_merged_expanded_v1.json

The snapshot Resemble publishes holds both side by side, plus conds.pt (the
built-in voice, optional). Two more builds are RECOGNISED so they can be
refused by name rather than reported as a broken download: turbo
(t3_turbo_v1.safetensors, a different T3 architecture) and multilingual v3
(t3_mtl23ls_v3.safetensors, which opens different files). Neither loads in the
bundled library; see variant_refusal().

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

# Is it chatterbox at all: a T3 backbone of any build, plus the vocoder or the
# voice encoder beside it. Which build, and whether it is complete, is decided
# by the two tables below.
_T3 = ("t3_*.safetensors", "t3_*.pt")
_S3GEN = ("s3gen.safetensors", "s3gen.pt")
_VE = ("ve.safetensors", "ve.pt")

# What the worker opens per build, and so what "complete" means for it. This
# is a COPY of tts/worker/chatterbox.py's REQUIRED_FILES and VARIANT_T3, and
# that file is the original: the worker is the process that opens the files,
# it runs in the engine venv where the app cannot be imported, and the app
# never imports the worker. tests/test_tts_chatterbox_variants.py loads that
# file by path and holds the two copies equal; edit both or it goes red.
# Before the copy existed the host accepted s3gen.pt for english and the
# worker then refused for s3gen.safetensors: "download did not finish" about
# a download that had.
REQUIRED_FILES = {
    "multilingual": ("ve.pt", "t3_mtl23ls_v2.safetensors", "s3gen.pt",
                     "grapheme_mtl_merged_expanded_v1.json"),
    "english": ("ve.safetensors", "t3_cfg.safetensors", "s3gen.safetensors",
                "tokenizer.json"),
}

# The T3 backbone's file name is the build. Tried in this order: the snapshot
# Resemble publishes holds both loadable builds side by side and has always
# read as multilingual, which stays. The last two rows have no REQUIRED_FILES
# entry on purpose; they exist so the folder is refused by name instead of
# being read as english and then blamed for english's files.
VARIANT_T3 = (
    ("multilingual", ("t3_mtl23ls_v2.safetensors",)),
    ("english", ("t3_cfg.safetensors", "t3_cfg.pt")),
    ("turbo", ("t3_turbo*.safetensors", "t3_turbo*.pt")),
    ("multilingual_v3", ("t3_mtl23ls_v3.safetensors", "t3_mtl23ls_v3.pt")),
)
# A T3 file that matches no row above. Still chatterbox by its other files,
# still refused, and the refusal says so rather than naming a file the user
# dropped in: a planted file name is not something to echo into a payload.
UNKNOWN_VARIANT = "unknown"

_MTL_LANGS = ("ar", "da", "de", "el", "en", "es", "fi", "fr", "he", "hi", "it",
              "ja", "ko", "ms", "nl", "no", "pl", "pt", "ru", "sv", "sw", "tr", "zh")


def detect_variant(model_dir: Path) -> str:
    """The build in this folder, read off the T3 backbone's file name.

    Same table, same order as the worker's detect_variant, so the host and
    the process that opens the files reach one verdict about one folder.
    """
    for variant, patterns in VARIANT_T3:
        if has_any(model_dir, patterns):
            return variant
    return UNKNOWN_VARIANT


def _multilingual(variant: str | None) -> bool:
    """The multilingual family, v2 and v3 alike: both carry the language dial
    and the 23-language list (the "mtl23ls" in their T3 name). Turbo and the
    unknown build take the english descriptor, which is the smaller claim."""
    return bool(variant) and variant.startswith("multilingual")


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
        variant = detect_variant(model_dir)
        # `missing` names exactly what the worker will demand for this build,
        # in the worker's order. A build the worker cannot load has no list,
        # so nothing is "missing" from it: variant_refusal() reports that one,
        # and a redownload would not help it.
        required = REQUIRED_FILES.get(variant, ())
        missing = tuple(name for name in required
                        if not (model_dir / name).is_file())
        return IdentifyResult(cls.engine_id, "signature", variant, missing)

    @classmethod
    def variant_refusal(cls, model: DetectedModel) -> str:
        """Why the worker will not open this build, or "" when it will.

        Read by readiness.evaluate. Every file may be present and the load
        still refused: turbo is a different T3 architecture and multilingual
        v3 opens different files, and chatterbox_tts 0.1.7 has a loader for
        neither. That is not tts_model_incomplete, whose fix is a redownload
        of the same files; it is its own code, and this sentence names the
        build so the log says which one. A variant of None never went through
        identify() (a sidecar override the files did not corroborate, already
        reported as incomplete), so there is nothing to refuse.
        """
        variant = model.variant
        if variant is None or variant in REQUIRED_FILES:
            return ""
        loadable = " and ".join(sorted(REQUIRED_FILES))
        if variant == UNKNOWN_VARIANT:
            return (f"the T3 file in this folder names no chatterbox build the "
                    f"bundled engine loads ({loadable})")
        return (f"this is the {variant} build of chatterbox; the bundled engine "
                f"loads {loadable} only")

    @classmethod
    def signature_files(cls, model_dir: Path) -> list[tuple[str, int]]:
        out: list[tuple[str, int]] = []
        for pats in (_T3, _S3GEN, _VE):
            hit = first_match(model_dir, pats)
            out.append((hit.name if hit else pats[0], size_of(hit) if hit else 0))
        return out

    @classmethod
    def describe_settings(cls, model: DetectedModel) -> list[ParamSpec]:
        multilingual = _multilingual(model.variant)
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
        return _MTL_LANGS if _multilingual(model.variant) else ("en",)

    @classmethod
    def estimate_vram_mb(cls, model: DetectedModel, values: dict) -> VramEstimate:
        # Measured previously on this hardware at ~3.6 GB fp32 peak.
        return VramEstimate(3800, "Chatterbox fp32 weights + working set")
