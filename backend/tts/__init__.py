"""Voice / TTS subsystem - HOST HALF.

Nothing in this package imports torch, numpy or any engine library. The three
supported engines need mutually incompatible dependency sets, so they run in
their own interpreters (runtimes.json) behind a JSON-lines worker protocol.
The exe only identifies, describes, estimates and validates - which is exactly
what keeps discovery unit-testable on a machine with no GPU.

Data lives under config.TTS_DIR (`<data>/voice/`), deliberately NOT `<data>/tts/`
so it can never collide with this package in a dev checkout.
"""
from __future__ import annotations

from .base import (
    DetectedModel,
    EngineCapabilities,
    IdentifyResult,
    ParamSpec,
    ScanResult,
    TtsAdapter,
    VramEstimate,
)
from .errors import ALL_CODES, ParamError, TtsError
from .preflight import FitResult, check_fit
from .readiness import Issue, Readiness, evaluate, evaluate_all
from .registry import adapter_for, all_adapters, identify_dir, scan_roots
from .vram import GpuInfo, query_gpu

__all__ = [
    "DetectedModel", "EngineCapabilities", "IdentifyResult", "ParamSpec",
    "ScanResult", "TtsAdapter", "VramEstimate",
    "ALL_CODES", "ParamError", "TtsError",
    "adapter_for", "all_adapters", "identify_dir", "scan_roots",
    "FitResult", "check_fit", "GpuInfo", "query_gpu",
    "Issue", "Readiness", "evaluate", "evaluate_all",
]
