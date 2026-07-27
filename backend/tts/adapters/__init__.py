"""Registered engine adapters (host halves).

The list is STATIC on purpose. A frozen PyInstaller build does not follow
dynamic imports, so a pkgutil sweep would silently find nothing in the packaged
exe while working perfectly in dev - the worst kind of bug. Adding an engine is
two lines here plus a hiddenimports entry in the spec.
"""
from __future__ import annotations

from ..base import TtsAdapter
from .chatterbox import ChatterboxAdapter
from .fish_s2 import FishS2Adapter
from .xtts_v2 import XttsV2Adapter

ADAPTERS: tuple[type[TtsAdapter], ...] = (
    FishS2Adapter,
    XttsV2Adapter,
    ChatterboxAdapter,
)

__all__ = ["ADAPTERS", "FishS2Adapter", "XttsV2Adapter", "ChatterboxAdapter"]
