"""tts/preflight.py - decide, BEFORE loading, whether a model will fit.

This is the guard that protects the user's session. Loading a model that fills
the card does not fail cleanly: Windows starts paging VRAM to system memory and
everything - the desktop, a game, the app itself - crawls. Measured on this
project: a model that overran the card ran ~300x slower and looked like a hang.

So the rule is conservative on purpose. We require the estimate PLUS a headroom
margin to fit in what is free right now, and when we cannot see the GPU at all
we refuse rather than guess.
"""
from __future__ import annotations

from dataclasses import dataclass

import config

from .base import DetectedModel
from .errors import TTS_GPU_UNAVAILABLE, TTS_INSUFFICIENT_VRAM
from .registry import adapter_for
from .vram import GpuInfo, query_gpu


@dataclass(frozen=True)
class FitResult:
    fits: bool
    estimate_mb: int
    free_mb: int
    total_mb: int
    used_by_others_mb: int
    headroom_mb: int
    gpu_available: bool
    reason: str | None = None
    detail: str = ""

    def to_json(self) -> dict:
        return {
            "fits": self.fits,
            "estimate_mb": self.estimate_mb,
            "free_mb": self.free_mb,
            "total_mb": self.total_mb,
            "used_by_others_mb": self.used_by_others_mb,
            "headroom_mb": self.headroom_mb,
            "gpu_available": self.gpu_available,
            "reason": self.reason,
            "detail": self.detail,
        }


def check_fit(
    model: DetectedModel,
    values: dict | None = None,
    *,
    gpu: GpuInfo | None = None,
    probe: bool = True,
) -> FitResult:
    """Estimate this model's footprint under `values` and compare with free VRAM.

    `gpu`/`probe` let a caller checking a whole LIST of models take one reading
    and reuse it: the free-VRAM figure cannot meaningfully change between the
    first row and the last, and one nvidia-smi per row would stall the page.
    Pass `probe=False` with `gpu=None` to mean "there is no GPU", as opposed to
    "I did not look".
    """
    headroom = int(config.TTS_VRAM_HEADROOM_MB)
    adapter = adapter_for(model.engine_id)

    estimate = 0
    detail = ""
    if adapter is not None:
        try:
            est = adapter.estimate_vram_mb(model, values or {})
            estimate, detail = int(est.estimate_mb), est.reason
        except Exception:
            estimate, detail = 0, "estimate unavailable"

    if gpu is None and probe:
        gpu = query_gpu()

    if estimate <= 0:
        # No estimate is not "it fits" - zero plus headroom fits on ANY card,
        # which would quietly disable the one guard that keeps a load from
        # filling the card and dragging the whole desktop down. The documented
        # rule for the no-GPU branch below is "refuse rather than guess"; the
        # same rule applies when it is the ESTIMATE we cannot see.
        #
        # But it reports the GPU it ACTUALLY read (audit KÖK 14). This branch
        # used to answer gpu_available=False with free_mb=0, total_mb=0 without
        # ever looking at the card - so a working RTX 5080 with 14 GB free was
        # described to the user as "no readable NVIDIA GPU on this machine",
        # and the fit panel showed "0 MB / 0 MB" on a 16 GB board. The refusal
        # is right; the story told about the machine was not.
        return FitResult(
            fits=False, estimate_mb=0,
            free_mb=gpu.free_mb if gpu else 0,
            total_mb=gpu.total_mb if gpu else 0,
            used_by_others_mb=gpu.used_mb if gpu else 0,
            headroom_mb=headroom,
            gpu_available=gpu is not None,
            reason=TTS_INSUFFICIENT_VRAM,
            detail=detail or "no VRAM estimate for this model - refusing to guess",
        )

    if gpu is None:
        # No reading means no basis to promise it will fit, so we still refuse -
        # but the REASON is "no GPU", not "not enough VRAM". The wrong one sends
        # someone off to close programs on a machine with no NVIDIA card at all.
        return FitResult(
            fits=False, estimate_mb=estimate, free_mb=0, total_mb=0,
            used_by_others_mb=0, headroom_mb=headroom, gpu_available=False,
            reason=TTS_GPU_UNAVAILABLE,
            detail="no NVIDIA GPU reading available",
        )

    fits = (estimate + headroom) <= gpu.free_mb
    return FitResult(
        fits=fits,
        estimate_mb=estimate,
        free_mb=gpu.free_mb,
        total_mb=gpu.total_mb,
        used_by_others_mb=gpu.used_mb,
        headroom_mb=headroom,
        gpu_available=True,
        reason=None if fits else TTS_INSUFFICIENT_VRAM,
        detail=detail,
    )
