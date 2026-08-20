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

import hashlib
import json
import logging
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import config

from .errors import (TTS_RUNTIME_BROKEN, TTS_RUNTIME_MISSING,
                     TTS_RUNTIME_UNTRUSTED)

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


def _interpreter_leaf() -> str:
    """The interpreter filename this platform's installs end in."""
    return "python.exe" if os.name == "nt" else "python"


def _confined(python: str) -> bool:
    """Whether an interpreter path is one this app could have installed.

    The value in this file becomes argv[0] of a subprocess. Until 20 August
    2026 the only check was is_file(), which means the file said where to find
    a program and the app ran whatever was there. Any program running as this
    user can write this file with no elevation, so that was a plain code
    execution primitive sitting in a JSON document.

    The anchor is config.TTS_ENVS_DIR, read at call time rather than bound at
    import, because provision.env_python composes every legitimate path from
    it. Nothing this app installs is ever resolved from sys._MEIPASS, so the
    onefile temp directory is deliberately NOT an anchor.

    realpath on BOTH sides, not abspath. On Windows realpath goes through
    GetFinalPathNameByHandle, which resolves junctions and symlinks and
    expands 8.3 short names, so DESKTO~1 and a junction planted mid-path both
    collapse to the string an honest path produces. normcase because NTFS is
    case insensitive and this is a string compare. The junction is the
    important one: os.path.islink returns False for a junction, so a check
    built on islink would have let it straight through.

    The filename is pinned as well. Being somewhere under the anchor is not
    enough when the anchor is a directory a same-user attacker can also write
    to; requiring the leaf that env_python composes means a stray executable
    dropped beside the interpreter is not a candidate.

    THE ALLOWANCE, and why it is safe. In a development tree the suite and the
    developer register the running interpreter, which is nowhere near the
    anchor. It is admitted only when this process is NOT frozen. In the
    shipped app sys.executable is Elysium.exe, which is not an interpreter at
    all, and anybody able to replace it owns the application outright, so the
    allowance is inert exactly where it would have mattered.
    """
    if not python or not os.path.isabs(python):
        return False
    if not getattr(sys, "frozen", False):
        try:
            if os.path.normcase(os.path.realpath(python)) == os.path.normcase(
                    os.path.realpath(sys.executable)):
                return True
        except OSError:
            pass
    try:
        real = Path(os.path.realpath(python))
        anchor = Path(os.path.realpath(str(config.TTS_ENVS_DIR)))
    except OSError:
        return False
    if os.path.normcase(real.name) != os.path.normcase(_interpreter_leaf()):
        return False
    try:
        real.relative_to(anchor)
    except ValueError:
        return False
    # NOT SAFE STANDALONE. realpath falls back to a lexical answer for a path
    # that does not exist, so this returns True for a plausible name under the
    # anchor with no file behind it. status() gates on is_file() as well, and
    # a future caller has to do the same.
    return True


def _fingerprint(python: str) -> str | None:
    """SHA-256 of the interpreter binary, or None if it cannot be read.

    Recorded at install time and compared before launch. It is the move
    provision.py already makes for the uv download it pins, and it buys the
    same thing here: overwriting the binary under the anchor stops working on
    its own, because the recorded digest no longer matches.

    THE CEILING, and it is lower than a first draft of this docstring
    claimed. That draft said this "removes the single-write attack". It does
    not, and the measurement that refuted it is worth carrying here so nobody
    reinstates the sentence: a `.pth` file dropped into the environment's site
    directory runs arbitrary Python at interpreter startup with the
    interpreter's SHA-256 BYTE IDENTICAL. `sitecustomize.py` in the same place
    does the same, and `pyvenv.cfg`'s `home=` is a third redirect this never
    looks at. All of them live under the anchor, so path confinement admits
    them and a digest over one file is blind to them.

    So what this actually buys, in one sentence: it reduces the attack from
    "point at any executable on the disk" to "write a file under the app's own
    voice folder". That is a real narrowing and it is not a boundary. Closing
    the rest would need a MAC keyed by something the attacker cannot read, and
    the only such key here is the vault key, which is NOT available when this
    file is written: an engine install is deliberately allowed to keep running
    after the vault locks, and it registers its runtime when it finishes.
    """
    try:
        digest = hashlib.sha256()
        with open(python, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


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
    # "Somebody changed this" and "you never set voice up" must not read
    # alike. A separate code, so the screen can say one sentence and the log
    # can carry the other. Deliberately not a raise: this module's contract is
    # that every read degrades into a state the UI can explain.
    if not _confined(python):
        logger.warning(
            "tts: the interpreter recorded for %s is outside the folder this "
            "app installs into; refusing to run it", engine_id)
        return RuntimeStatus(engine_id, "untrusted", python,
                             TTS_RUNTIME_UNTRUSTED)
    if not Path(python).is_file():
        # Recorded once, gone now (disk cleanup, moved profile). Reporting
        # "ready" here would send the loader after a ghost interpreter.
        return RuntimeStatus(engine_id, "broken", python, TTS_RUNTIME_BROKEN)
    recorded = entry.get("sha256")
    if not isinstance(recorded, str) or not recorded:
        # REQUIRED, not optional, and the difference is the whole check. A
        # first draft skipped the comparison when no digest was recorded, on
        # the reasoning that installs predating this check should keep
        # working. Measured: omitting the key is part of the SAME single write
        # that plants the path, so the attacker simply does not write one and
        # the fingerprint never runs. An entry without a digest is refused;
        # the way back is the Set up button, which records one.
        logger.warning(
            "tts: the interpreter recorded for %s carries no fingerprint, so "
            "there is nothing to check it against; refusing to run it",
            engine_id)
        return RuntimeStatus(engine_id, "untrusted", python,
                             TTS_RUNTIME_UNTRUSTED)
    if _fingerprint(python) != recorded:
        logger.warning(
            "tts: the interpreter recorded for %s no longer matches the "
            "fingerprint taken when it was installed; refusing to run it",
            engine_id)
        return RuntimeStatus(engine_id, "untrusted", python,
                             TTS_RUNTIME_UNTRUSTED)
    return RuntimeStatus(engine_id, "ready", python, None)


def register(engine_id: str, python_path: str, **meta) -> RuntimeStatus:
    data = _load()
    engines = data.setdefault("engines", {})
    entry = {"python": str(python_path), **meta}
    # Taken HERE, at install time, from the binary this app has just put in
    # place. Computing it lazily on first use would fingerprint whatever is
    # there by then, which is the thing being guarded against.
    fingerprint = _fingerprint(str(python_path))
    if fingerprint is not None:
        entry["sha256"] = fingerprint
    else:
        # Silence here would leave a permanently unrefusable entry with
        # nothing anywhere saying why. status() refuses an entry with no
        # digest, so this is the line that explains the refusal the user is
        # about to see.
        logger.warning(
            "tts: could not read the interpreter just installed for %s, so no "
            "fingerprint was recorded and it will not be trusted to run",
            engine_id)
    engines[engine_id] = entry
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
