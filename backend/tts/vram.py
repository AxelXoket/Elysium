"""tts/vram.py - what the GPU actually has free, without importing torch.

The host half runs inside the exe and must stay dependency-free, so this shells
out to nvidia-smi rather than using torch/pynvml. Every failure mode - no NVIDIA
driver, no GPU, a timeout, garbage output - returns None. Voice degrades to
"cannot check, so do not load"; it never crashes the app for lack of a GPU.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass

from ._which import which_trusted

logger = logging.getLogger(__name__)

_QUERY = "name,memory.total,memory.free,memory.used"
_TIMEOUT_S = 5


@dataclass(frozen=True)
class GpuInfo:
    name: str
    total_mb: int
    free_mb: int
    used_mb: int

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "total_mb": self.total_mb,
            "free_mb": self.free_mb,
            "used_mb": self.used_mb,
        }


#: Windows-only; 0 elsewhere so the same call site works on every platform.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run_smi() -> str | None:
    """Raw first line of nvidia-smi's CSV, or None. Patched in tests."""
    # Not shutil.which: it searches the working directory first on Windows,
    # and an app started from Downloads would happily run an nvidia-smi.exe
    # found among the user's other downloads. See tts/_which.py.
    exe = which_trusted("nvidia-smi")
    if not exe:
        return None
    out = subprocess.run(
        [exe, f"--query-gpu={_QUERY}", "--format=csv,noheader,nounits"],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT_S,
        # shell=False (default) and a fixed argv: nothing user-controlled is
        # interpolated into this command.
        #
        # CREATE_NO_WINDOW like every other production subprocess in this
        # package (worker_client, provision - which passes it even to taskkill).
        # nvidia-smi.exe is a console-subsystem program and the shipped build is
        # console=False, so without this each probe popped a black window in
        # front of the app and stole focus - and the voice settings page probes
        # on every window focus.
        creationflags=_NO_WINDOW,
    )
    if out.returncode != 0:
        return None
    return out.stdout


def query_gpu() -> GpuInfo | None:
    """First CUDA device, or None when it cannot be determined."""
    try:
        raw = _run_smi()
    except Exception as exc:                      # timeout, OSError, anything
        logger.warning("tts: nvidia-smi query failed (%s)", type(exc).__name__)
        return None
    if not raw:
        return None
    line = raw.strip().splitlines()[0] if raw.strip() else ""
    parts = [p.strip() for p in line.split(",")]
    if len(parts) < 4:
        return None
    try:
        return GpuInfo(parts[0], int(parts[1]), int(parts[2]), int(parts[3]))
    except (TypeError, ValueError):
        return None
