"""tts/base.py - host-half types shared by every engine adapter.

HOST HALF RULE: nothing in this package may import torch, numpy, transformers
or any engine library. The three supported engines have mutually incompatible
dependency sets, so they run in their OWN interpreters (see runtimes.json).
The exe only ever identifies, describes, estimates and validates - which keeps
all of that unit-testable on a machine with no GPU and no engine installed.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from .errors import ParamError

# Param types the frontend knows how to render generically.
PARAM_TYPES = ("float", "int", "bool", "enum", "text", "voice_ref")

# Engines disagree on what to call the language knob; readiness must not care.
LANGUAGE_PARAM_NAMES = ("language", "language_id", "lang")


@dataclass(frozen=True)
class ParamSpec:
    """One tunable knob, described well enough that the UI can render it and
    the backend can validate it without knowing the engine."""

    name: str
    type: str
    default: Any
    label: str
    help: str = ""
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    choices: tuple[str, ...] | None = None
    group: str = "general"
    advanced: bool = False

    def __post_init__(self) -> None:
        if self.type not in PARAM_TYPES:
            raise ValueError(f"unknown param type: {self.type}")

    # -- validation ---------------------------------------------------------
    def clamp(self, value: Any) -> Any:
        """Coerce `value` into this spec, clamping numerics into range.

        Out-of-range is CLAMPED (a slider that overshoots should not fail the
        whole save); un-coercible or off-menu is a ParamError, because silently
        substituting a default would hide a real client bug.
        """
        t = self.type
        if t in ("float", "int"):
            try:
                num = float(value)
            except (TypeError, ValueError):
                raise ParamError(detail=f"{self.name}: not numeric")
            if num != num or num in (float("inf"), float("-inf")):
                raise ParamError(detail=f"{self.name}: not finite")
            if self.minimum is not None:
                num = max(num, float(self.minimum))
            if self.maximum is not None:
                num = min(num, float(self.maximum))
            return int(round(num)) if t == "int" else float(num)

        if t == "bool":
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return bool(value)
            if isinstance(value, str) and value.strip().lower() in ("true", "false", "1", "0"):
                return value.strip().lower() in ("true", "1")
            raise ParamError(detail=f"{self.name}: not boolean")

        if t == "enum":
            s = str(value)
            if not self.choices or s not in self.choices:
                raise ParamError(detail=f"{self.name}: not an allowed choice")
            return s

        # text / voice_ref
        if value is None:
            return ""
        return str(value)

    # -- wire ---------------------------------------------------------------
    def to_json(self) -> dict:
        return {
            "name": self.name,
            "type": self.type,
            "default": self.default,
            "label": self.label,
            "help": self.help,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "step": self.step,
            "choices": list(self.choices) if self.choices else None,
            "group": self.group,
            "advanced": self.advanced,
        }


@dataclass(frozen=True)
class EngineCapabilities:
    """What the engine can do - drives which UI affordances appear at all."""

    voice_cloning: bool = False
    needs_reference_transcript: bool = False
    inline_prosody_tags: bool = False
    streaming: bool = False
    #: Can this engine HEAR a reference clip and draft its words?
    #: No shipped engine can, and OP_TRANSCRIBE is refused by all three
    #: workers - but "Listen & fill in" was drawn unconditionally, always
    #: enabled, and its failure was translated as "The voice engine could not
    #: start" while the engine was running perfectly. Defaults False so a new
    #: adapter has to claim the ability before the button appears for it.
    transcribes_reference: bool = False
    languages: tuple[str, ...] = ()
    native_sample_rate: int = 24000

    def to_json(self) -> dict:
        return {
            "voice_cloning": self.voice_cloning,
            "needs_reference_transcript": self.needs_reference_transcript,
            "inline_prosody_tags": self.inline_prosody_tags,
            "streaming": self.streaming,
            "transcribes_reference": self.transcribes_reference,
            "languages": list(self.languages),
            "native_sample_rate": self.native_sample_rate,
        }


@dataclass(frozen=True)
class IdentifyResult:
    """Outcome of fingerprinting one directory."""

    engine_id: str
    source: str = "signature"          # "signature" | "sidecar"
    variant: str | None = None
    missing: tuple[str, ...] = ()      # required files that are absent

    @property
    def incomplete(self) -> bool:
        return bool(self.missing)


@dataclass(frozen=True)
class DetectedModel:
    uid: str
    engine_id: str
    name: str
    path: str
    variant: str | None = None
    source: str = "signature"
    missing: tuple[str, ...] = ()
    #: The uid this folder had before roots were mixed in. Carried only so a
    #: stored selection made under the old scheme can be recognised once and
    #: rewritten, instead of resolving to nothing. Never sent to the client.
    legacy_uid: str = ""

    @property
    def incomplete(self) -> bool:
        return bool(self.missing)

    def to_json(self) -> dict:
        return {
            "uid": self.uid,
            "engine_id": self.engine_id,
            "name": self.name,
            "path": self.path,
            "variant": self.variant,
            "source": self.source,
            "incomplete": self.incomplete,
            "missing": list(self.missing),
        }


@dataclass(frozen=True)
class VramEstimate:
    estimate_mb: int
    reason: str = ""

    def to_json(self) -> dict:
        return {"estimate_mb": self.estimate_mb, "reason": self.reason}


@dataclass
class UnrecognizedDir:
    path: str
    reason: str
    # The machine-readable name for "no engine signature matched", so the UI
    # can map it like every other failure instead of relying on the prose.
    code: str = "tts_model_unrecognized"

    def to_json(self) -> dict:
        return {"path": self.path, "reason": self.reason, "code": self.code}


@dataclass
class ScanResult:
    models: list[DetectedModel] = field(default_factory=list)
    unrecognized: list[UnrecognizedDir] = field(default_factory=list)
    roots: list[str] = field(default_factory=list)
    #: The walk stopped at TTS_SCAN_MAX_DIRS instead of running out of
    #: directories. Without this the cap was indistinguishable from "that is
    #: everything": a model past the limit was simply absent from the UI, and
    #: the next Speak on it failed with tts_model_unknown - an error about a
    #: model the user could see on disk and the app had never looked at.
    truncated: bool = False


class TtsAdapter(ABC):
    """Host half of an engine. Pure stdlib - see the HOST HALF RULE above."""

    engine_id: ClassVar[str]
    display_name: ClassVar[str]
    priority: ClassVar[int] = 100
    capabilities: ClassVar[EngineCapabilities]

    @classmethod
    @abstractmethod
    def identify(cls, model_dir: Path) -> IdentifyResult | None:
        """Content signature only: small reads, never opens weights, never
        imports torch. Return None when this is not our model."""

    @classmethod
    @abstractmethod
    def signature_files(cls, model_dir: Path) -> list[tuple[str, int]]:
        """(relpath, size) pairs describing the model's identifying files.

        Currently UNUSED by the registry - identity became path-based after the
        v1 audit (same-named copies, engine overrides and completed downloads
        all broke content-based uids). Kept on the interface because a future
        integrity check ("did the download finish?") needs exactly this list.
        """

    @classmethod
    @abstractmethod
    def describe_settings(cls, model: DetectedModel) -> list[ParamSpec]:
        """The full typed descriptor the settings UI renders generically."""

    @classmethod
    def languages_for(cls, model: DetectedModel) -> tuple[str, ...]:
        """Languages THIS model can actually speak.

        `capabilities.languages` describes the engine FAMILY; a downloaded build
        can be narrower than its family. The model's own descriptor is the
        truthful source, so read the language choice list it renders.
        """
        try:
            for spec in cls.describe_settings(model):
                if spec.name in LANGUAGE_PARAM_NAMES and spec.choices:
                    return tuple(spec.choices)
        except Exception:
            return ()
        return cls.capabilities.languages

    @classmethod
    def estimate_vram_mb(cls, model: DetectedModel, values: dict) -> VramEstimate:
        return VramEstimate(0, "no estimate available")

    @classmethod
    def clamp_values(cls, model: DetectedModel, values: dict) -> dict:
        """Validate a whole value map against the descriptor. Unknown keys are
        dropped (a stale client must not be able to inject arbitrary kwargs)."""
        specs = {s.name: s for s in cls.describe_settings(model)}
        out: dict[str, Any] = {}
        for key, val in (values or {}).items():
            spec = specs.get(key)
            if spec is None:
                continue
            out[key] = spec.clamp(val)
        return out
