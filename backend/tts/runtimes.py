"""tts/runtimes.py - the APP-OWNED record of where each engine's interpreter is.

The engines need mutually incompatible dependency sets, so they cannot live
inside the exe; each runs in its own interpreter. That is an implementation
detail the USER MUST NEVER HAVE TO KNOW. They press "Set up voice" in Settings
and the app creates the environment and records it here.

runtimes.json is therefore an INTERNAL ARTEFACT, not a configuration surface:
the app writes it, the app reads it. Hand-editing is an escape hatch for a
broken install, never the installation path.

Every read degrades gracefully - a missing, corrupt or stale registry reports a
state the UI can explain, and never raises into a request.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import config

from .errors import TTS_RUNTIME_BROKEN, TTS_RUNTIME_MISSING

logger = logging.getLogger(__name__)

# state: missing (never installed) | ready | broken (recorded but gone)
@dataclass(frozen=True)
class RuntimeStatus:
    engine_id: str
    state: str
    python: str | None = None
    error_code: str | None = None

    def to_json(self) -> dict:
        return {
            "engine_id": self.engine_id,
            "state": self.state,
            "python": self.python,
            "error_code": self.error_code,
        }


def _path() -> Path:
    return Path(config.TTS_RUNTIMES_PATH)


def _load() -> dict:
    """Whole registry, or an empty one. Never raises - a corrupt file must not
    make voice unreachable, it must make voice say 'not set up'."""
    try:
        p = _path()
        if not p.is_file():
            return {}
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        logger.warning("tts: runtimes registry unreadable; treating as empty")
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: dict) -> None:
    """Write via a temp file + replace so a crash mid-write cannot leave a
    half-written registry that would read as 'not set up' next launch.

    The fsync is what makes that sentence true. `os.replace` reorders a
    directory entry; it says nothing about the bytes behind it. Without the
    flush+fsync below, a power cut just after the rename can leave the new name
    pointing at a file the filesystem has not written yet - and the cost of
    that lands on the user, not on us: `_load` reads the truncated file, warns
    to a log nobody opens, returns `{}`, and the app says voice is not set up.
    They are then sent to re-download gigabytes of models that are still
    sitting intact on their own disk.

    What this CANNOT promise: the rename itself is not flushed. Windows offers
    no portable way to fsync a directory entry (`os.open` on a directory
    fails), so a crash inside the window between the data landing and the
    directory entry landing still leaves the PREVIOUS registry in place. That
    is the safe direction to fail in - stale, never truncated - which is
    exactly the outcome the missing fsync could not guarantee.
    """
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def status(engine_id: str) -> RuntimeStatus:
    entry = (_load().get("engines") or {}).get(engine_id)
    if not isinstance(entry, dict) or not entry.get("python"):
        return RuntimeStatus(engine_id, "missing", None, TTS_RUNTIME_MISSING)
    python = str(entry["python"])
    if not Path(python).is_file():
        # Recorded once, gone now (disk cleanup, moved profile). Reporting
        # "ready" here would send the loader after a ghost interpreter.
        return RuntimeStatus(engine_id, "broken", python, TTS_RUNTIME_BROKEN)
    return RuntimeStatus(engine_id, "ready", python, None)


def register(engine_id: str, python_path: str, **meta) -> RuntimeStatus:
    data = _load()
    engines = data.setdefault("engines", {})
    engines[engine_id] = {"python": str(python_path), **meta}
    _save(data)
    return status(engine_id)


def unregister(engine_id: str) -> None:
    data = _load()
    engines = data.get("engines") or {}
    if engine_id in engines:
        del engines[engine_id]
        data["engines"] = engines
        _save(data)


def all_status(engine_ids: list[str]) -> list[RuntimeStatus]:
    return [status(e) for e in engine_ids]


def extra_roots() -> list[str]:
    """Additional model roots. Read on EVERY call on purpose: parsing this once
    at boot (ComfyUI's extra_model_paths.yaml behaviour) means adding a folder
    needs an app restart, which is a documented usability trap."""
    roots = _load().get("extra_roots")
    if not isinstance(roots, list):
        return []
    return [str(r) for r in roots if isinstance(r, str)]
