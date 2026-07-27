"""Audit KÖK 9 + 10, backend half.

KÖK 9 is one missing term in an arithmetic that decides whether to evict. KÖK
10 is a set of collections with no ceiling. They share a shape: nothing here
was ever wrong on a short run, and all of it goes wrong on a long one.
"""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import config


def _fish():
    """The worker module, loaded WITHOUT torch (it imports lazily)."""
    path = Path(__file__).resolve().parents[1] / "tts" / "worker" / "fish_s2.py"
    spec = importlib.util.spec_from_file_location("fish_s2_bounded", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("fish_s2_bounded", mod)
    spec.loader.exec_module(mod)
    mod._COSTS.clear()
    return mod


# ---------------------------------------------------------------------------
# KÖK 9: the codec was free, according to the check that ran before it loaded
# ---------------------------------------------------------------------------

def test_a_card_that_only_fits_the_decode_still_evicts():
    """The audit's own worked example: 4.0 GB free, a measured 2.2 GB decode,
    codec NOT resident. Decode plus the 1.0 GB reserve leaves 0.8 GB - and the
    codec that is about to be loaded wants about 1.9. The old check passed."""
    mod = _fish()
    mod.STATE["codec"] = None
    for _ in range(60):
        mod._observe_cost("decode", units=100, gb=2.2)
    mod._free_gb = lambda: 4.0

    assert mod._fits(100) is False, (
        "the gate passed with less headroom than the codec alone needs"
    )


def test_the_same_card_is_fine_once_the_codec_is_already_there():
    """The other half: the term is a real cost, not a blanket penalty. With the
    codec resident the arithmetic is exactly what it always was."""
    mod = _fish()
    mod.STATE["codec"] = object()
    for _ in range(60):
        mod._observe_cost("decode", units=100, gb=2.2)
    mod._free_gb = lambda: 4.0

    assert mod._fits(100) is True


def test_the_zero_frame_callers_are_no_longer_a_rubber_stamp():
    """_fits(0) collapsed to "free >= 1.0", and _codec(send) sits OUTSIDE the
    try that carries the OOM retry ladder - so an OOM there lost the sentence
    outright rather than retrying it smaller."""
    mod = _fish()
    mod.STATE["codec"] = None
    mod._free_gb = lambda: 1.5           # over the bare reserve, under the codec

    assert mod._fits(0) is False


def test_the_codec_term_is_a_measurement_once_one_exists():
    """A constant is the prior, not the answer. A machine whose codec is
    smaller than the bake-off's must not keep evicting for a number nobody has
    seen there."""
    mod = _fish()
    mod.STATE["codec"] = None
    assert mod._codec_need() == mod._SEED_CODEC_GB

    for _ in range(60):
        mod._observe_cost("codec", units=1, gb=0.4)
    assert mod._codec_need() < mod._SEED_CODEC_GB
    assert mod._codec_need() > 0.0


def test_a_resident_codec_costs_nothing_to_load():
    mod = _fish()
    mod.STATE["codec"] = object()
    assert mod._codec_need() == 0.0


# ---------------------------------------------------------------------------
# KÖK 10: the spoken conversation, in the clear, with no ceiling
# ---------------------------------------------------------------------------

def _host_with_cache(tmp_path, monkeypatch):
    from tts.host import VoiceHost

    monkeypatch.setattr(config, "TTS_CACHE_DIR", str(tmp_path / "cache"))
    return VoiceHost()


def test_generated_audio_older_than_the_window_is_cleared(tmp_path, monkeypatch):
    """wipe_cache's only callers are the vault lock and shutdown, so a long day
    without locking kept the WHOLE conversation as plaintext wav on disk."""
    host = _host_with_cache(tmp_path, monkeypatch)
    cache = Path(config.TTS_CACHE_DIR)
    cache.mkdir(parents=True, exist_ok=True)

    old = cache / "speak-1-1.wav"
    old.write_bytes(b"RIFF")
    stale = time.time() - float(config.TTS_CACHE_MAX_AGE_S) - 60
    import os
    os.utime(old, (stale, stale))
    fresh = cache / "speak-2-2.wav"
    fresh.write_bytes(b"RIFF")

    host._next_out_path()

    assert not old.exists(), "an expired recording of the user was kept"
    assert fresh.exists(), "a reply still on screen must stay replayable"


def test_it_touches_nothing_that_is_not_ours(tmp_path, monkeypatch):
    """The cache directory also holds conditioning caches. An age sweep that
    ate those would make every reply pay to rebuild them."""
    host = _host_with_cache(tmp_path, monkeypatch)
    cache = Path(config.TTS_CACHE_DIR)
    cache.mkdir(parents=True, exist_ok=True)

    import os
    stale = time.time() - float(config.TTS_CACHE_MAX_AGE_S) - 60
    for name in ("conditioning.pt", "speak-3-3.txt", "notes.json"):
        path = cache / name
        path.write_bytes(b"x")
        os.utime(path, (stale, stale))

    host._next_out_path()

    for name in ("conditioning.pt", "speak-3-3.txt", "notes.json"):
        assert (cache / name).exists(), name


def test_a_file_that_cannot_be_removed_is_not_an_error(tmp_path, monkeypatch):
    """On Windows the player may still hold one open. This runs on the
    synthesis path; it may never be the reason a reply fails."""
    host = _host_with_cache(tmp_path, monkeypatch)
    cache = Path(config.TTS_CACHE_DIR)
    cache.mkdir(parents=True, exist_ok=True)

    def _boom(self, *a, **k):
        raise OSError("in use by another process")

    monkeypatch.setattr(Path, "unlink", _boom)
    stuck = cache / "speak-4-4.wav"
    stuck.write_bytes(b"RIFF")
    import os
    stale = time.time() - float(config.TTS_CACHE_MAX_AGE_S) - 60
    os.utime(stuck, (stale, stale))

    assert host._next_out_path()          # did not raise


def test_the_retention_window_is_shorter_than_a_session():
    """The whole point: "until the vault locks" was the old bound, and that is
    not a bound at all for somebody who leaves the app open."""
    assert 0 < config.TTS_CACHE_MAX_AGE_S <= 2 * 60 * 60
