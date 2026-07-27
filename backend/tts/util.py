"""tts/util.py - tiny filesystem helpers for the host half. Stdlib only."""
from __future__ import annotations

import json
from pathlib import Path


MAX_METADATA_BYTES = 4 * 1024 * 1024   # model metadata is KBs; MBs means junk


def read_json(path: Path) -> dict | None:
    """Read a small JSON file as UTF-8, or None if it is unusable for ANY reason.

    Two hard-won rules:

    1. Explicit utf-8 is not optional - the real coqui XTTS config.json contains
       an emoji, and the Windows default codepage raises UnicodeDecodeError.
    2. Catch EVERYTHING. A model folder is untrusted input the user dropped in.
       A deeply nested document raises RecursionError (a RuntimeError, NOT a
       ValueError) and a huge one raises MemoryError; either would escape a
       narrow except and take down every /tts endpoint with a bare 500 that
       carries no error code the UI can explain. Fingerprinting must degrade to
       "not my model", never crash the scan.
    """
    try:
        if path.stat().st_size > MAX_METADATA_BYTES:
            return None
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def size_of(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def first_match(model_dir: Path, patterns: tuple[str, ...]) -> Path | None:
    """First file matching any glob, in a stable (sorted) order."""
    for pat in patterns:
        hits = sorted(model_dir.glob(pat))
        if hits:
            return hits[0]
    return None


def has_any(model_dir: Path, patterns: tuple[str, ...]) -> bool:
    return first_match(model_dir, patterns) is not None
