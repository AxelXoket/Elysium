"""tts/registry.py - fingerprint a dropped folder, scan the model roots.

Discovery contract:
  * A model is always a DIRECTORY (true for all three engines), never a file.
  * Identification reads only small metadata files - never weights, never torch.
  * Chain: explicit sidecar override  >  content signature  >  unrecognized.
  * A folder the user dropped that we cannot place is REPORTED, not ignored;
    silence would look like the app is broken.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import logging

import config

from .adapters import ADAPTERS
from .base import DetectedModel, IdentifyResult, ScanResult, TtsAdapter, UnrecognizedDir
from .runtimes import extra_roots
from .util import read_json

logger = logging.getLogger(__name__)

SIDECAR_NAME = "elysium-model.json"

# HuggingFace cache siblings: they sit next to snapshots/ and hold blobs and
# ref pointers, never a loadable model. Listing them as "unrecognized" would
# put permanent noise in the UI for a perfectly normal cache layout.
_CONTAINER_NAMES = frozenset({"blobs", "refs", ".no_exist", ".locks"})

_BY_ID: dict[str, type[TtsAdapter]] = {a.engine_id: a for a in ADAPTERS}
_ORDERED: tuple[type[TtsAdapter], ...] = tuple(
    sorted(ADAPTERS, key=lambda a: (a.priority, a.engine_id))
)


def adapter_for(engine_id: str) -> type[TtsAdapter] | None:
    return _BY_ID.get(engine_id)


def all_adapters() -> tuple[type[TtsAdapter], ...]:
    return _ORDERED


def identify_dir(model_dir: Path) -> IdentifyResult | None:
    """Identify one directory, or None if nothing claims it."""
    model_dir = Path(model_dir)
    if not model_dir.is_dir():
        return None

    # 1. Sidecar override - lets a user rescue a model we guessed wrong.
    side = read_json(model_dir / SIDECAR_NAME)
    if side:
        forced = str(side.get("engine_id") or "")
        adapter = _BY_ID.get(forced)
        if adapter is not None:
            try:
                own = adapter.identify(model_dir)
            except Exception:
                own = None      # same rule as the signature loop below
            if own is not None:
                missing = own.missing
                variant = own.variant
            else:
                # The forced engine does not recognise this folder. Reporting
                # missing=() here would fabricate a "complete, ready to load"
                # model out of an override the files do not support - the user
                # would only find out at load time. Say so instead.
                missing = ("(engine override not corroborated by these files)",)
                variant = None
            return IdentifyResult(adapter.engine_id, "sidecar", variant, missing)
        # A sidecar naming an unknown engine is bad data, not a veto: fall
        # through to the signature chain rather than refusing the folder.

    # 2. Content signature, in priority order.
    for adapter in _ORDERED:
        try:
            res = adapter.identify(model_dir)
        except Exception:
            continue          # a broken adapter must never break the whole scan
        if res is not None:
            return res
    return None


def _uid_for(root: Path, model_dir: Path) -> str:
    """Identity = WHERE the model sits, relative to its scan root.

    Deliberately NOT derived from the engine, the folder name alone, or file
    sizes. Those were tried and each broke a real flow:
      * name + sizes  -> two identically-named copies collided onto one settings
                         row, and finishing a partial download silently changed
                         the id, losing the user's saved values;
      * anything engine-derived -> a manual engine override re-identified the
                         same folder as a different model, orphaning its
                         settings and the active selection.
    A path is unique per folder and survives content edits and re-identification.
    Cost, accepted and documented: moving or renaming the folder is a new model
    and starts from defaults.
    """
    return _digest(_root_key(root), _rel_key(root, model_dir))


def legacy_uid_for(root: Path, model_dir: Path) -> str:
    """The uid this folder had BEFORE the root was mixed in.

    Kept so a stored selection can be recognised and rewritten once, rather
    than silently resolving to nothing (see _resolve in routers/tts_runtime).
    """
    return _digest("", _rel_key(root, model_dir))


def _rel_key(root: Path, model_dir: Path) -> str:
    try:
        return model_dir.resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        return model_dir.name


def _root_key(root: Path) -> str:
    """Which root this folder was found under.

    Mixed into the uid because the relative path alone is NOT unique across
    roots (audit KÖK 15): runtimes.json carries `extra_roots`, so two roots can
    each hold a folder called `velvet` - verified, one uid for both. _resolve
    then returned whichever came first (loading the wrong model),
    evaluate_all overwrote one verdict with the other, the two shared their
    saved settings, and React rendered a duplicate key.
    """
    try:
        return root.resolve().as_posix()
    except OSError:
        return str(root)


def _digest(root_key: str, rel: str) -> str:
    # Windows paths are case-insensitive; the same folder must not yield two ids.
    raw = f"{root_key}\x00{rel}" if root_key else rel
    return hashlib.sha256(raw.casefold().encode("utf-8")).hexdigest()[:16]


def _has_files(d: Path) -> bool:
    try:
        return any(p.is_file() for p in d.iterdir())
    except OSError:
        return False


def scan_roots(roots: list[Path] | None = None) -> ScanResult:
    """Walk the model roots and classify every directory.

    Bounded by TTS_SCAN_MAX_DEPTH/TTS_SCAN_MAX_DIRS so a root accidentally
    pointed at something huge cannot hang the request.
    """
    if roots is None:
        # Extra roots are re-read here, not cached at import: adding a folder
        # must take effect without restarting the app.
        roots = [Path(config.TTS_MODELS_DIR)] + [Path(r) for r in extra_roots()]
    roots = [Path(r) for r in roots]

    result = ScanResult(roots=[str(r) for r in roots])
    identified: list[Path] = []
    candidates: list[Path] = []
    seen = 0

    for root in roots:
        if not root.is_dir():
            continue          # a missing root is normal (user has not made it yet)
        stack: list[tuple[Path, int]] = []
        try:
            stack = [(c, 1) for c in sorted(root.iterdir()) if c.is_dir()]
        except OSError:
            continue

        while stack:
            if seen >= config.TTS_SCAN_MAX_DIRS:
                # Say so rather than returning a short list that looks
                # complete. `stack` still holding entries is the proof the
                # walk was cut, not finished.
                result.truncated = True
                logger.warning(
                    "tts: model scan stopped at the %d directory limit; "
                    "some models may be missing from the list.",
                    config.TTS_SCAN_MAX_DIRS,
                )
                break
            current, depth = stack.pop(0)
            seen += 1

            res = identify_dir(current)
            if res is not None:
                adapter = _BY_ID.get(res.engine_id)
                if adapter is not None:
                    result.models.append(
                        DetectedModel(
                            uid=_uid_for(root, current),
                            legacy_uid=legacy_uid_for(root, current),
                            engine_id=res.engine_id,
                            name=current.name,
                            path=str(current),
                            variant=res.variant,
                            source=res.source,
                            missing=res.missing,
                        )
                    )
                    identified.append(current)
                continue      # never descend into a recognised model

            if _has_files(current) and current.name not in _CONTAINER_NAMES:
                candidates.append(current)
            if depth < config.TTS_SCAN_MAX_DEPTH:
                try:
                    stack.extend(
                        (c, depth + 1) for c in sorted(current.iterdir()) if c.is_dir()
                    )
                except OSError:
                    pass

    # A container on the way to a model (models--X/, snapshots/) is not an error.
    for cand in candidates:
        if any(_is_ancestor(cand, m) for m in identified):
            continue
        result.unrecognized.append(
            UnrecognizedDir(str(cand), "no engine signature matched")
        )

    result.models.sort(key=lambda m: m.name.lower())
    return result


def _is_ancestor(parent: Path, child: Path) -> bool:
    try:
        return os.path.commonpath([str(parent), str(child)]) == str(parent) and parent != child
    except ValueError:
        return False
