"""Numeric assertions for tts/worker/_dsp.py.

A standalone script rather than a pytest module because it needs numpy, and
numpy deliberately does not exist in the app venv (see _dsp.py's docstring).
`test_worker_dsp.py` runs this through a registered engine interpreter and
fails if it exits non-zero, so the maths really is covered on any machine that
has a voice engine set up - and honestly skipped on one that does not.

Run directly with any numpy-capable python:  python dsp_numeric_check.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np                                          # noqa: E402

from tts.worker import _dsp as dsp   # noqa: E402

SR = 44100
failures = []


def check(name, condition, detail=""):
    if condition:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        failures.append(name)


def tone(freq, seconds, sr=SR):
    t = np.arange(int(seconds * sr)) / sr
    # A few harmonics: a pure sine is the easiest possible case and would hide
    # the splice artefacts this is meant to catch.
    return (np.sin(2 * np.pi * freq * t)
            + 0.5 * np.sin(4 * np.pi * freq * t)
            + 0.25 * np.sin(6 * np.pi * freq * t)).astype(np.float32) / 1.75


def dominant_hz(x, sr=SR):
    spectrum = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return float(np.fft.rfftfreq(len(x), 1 / sr)[int(np.argmax(spectrum))])


print("dsp.time_stretch")

# ── duration ────────────────────────────────────────────────────────────────
src = tone(200, 2.0)
for rate in (0.80, 0.9, 1.1, 1.25):
    out = dsp.time_stretch(src, rate)
    want = len(src) / rate
    check(f"duration at rate {rate}",
          abs(len(out) - want) / want < 0.02,
          f"got {len(out)} want ~{want:.0f}")

# ── pitch must NOT move: that is the entire promise ─────────────────────────
for rate in (0.80, 1.25):
    out = dsp.time_stretch(src, rate)
    before, after = dominant_hz(src), dominant_hz(out)
    cents = 1200 * np.log2(after / before)
    check(f"pitch unchanged at rate {rate} ({after:.1f} Hz vs {before:.1f} Hz)",
          abs(cents) < 30, f"{cents:+.0f} cents")

# ── identity and guards ─────────────────────────────────────────────────────
out = dsp.time_stretch(src, 1.0)
check("rate 1.0 returns the input untouched", np.array_equal(out, src))

out = dsp.time_stretch(src, 1.01)
check("a sub-audible rate is a no-op", np.array_equal(out, src))

short = tone(200, 0.01)
check("too-short input is returned as-is",
      np.array_equal(dsp.time_stretch(short, 1.25), short))

check("rate is clamped low", dsp.clamp_rate(0.1) == dsp.MIN_RATE)
check("rate is clamped high", dsp.clamp_rate(9.0) == dsp.MAX_RATE)
check("a bad rate falls back to 1.0", dsp.clamp_rate("nonsense") == 1.0)
check("None falls back to 1.0", dsp.clamp_rate(None) == 1.0)
check("NaN falls back to 1.0", dsp.clamp_rate(float("nan")) == 1.0)

# ── the output has to be playable, at EVERY rate ────────────────────────────
# One rate is not a check, it is a sample. These assertions used to run at
# 1.25 only - and 1.25 happens to be the single rate at which the tail bug
# could not fire, because the loop's analysis-coordinate exit outran the
# synthesis-coordinate return length only when stretching. At 0.80 the output
# ended in 683 samples of digital silence and this file said PASS.
RATES = (0.80, 0.9, 1.0, 1.1, 1.25)

for rate in RATES:
    out = dsp.time_stretch(src, rate)
    check(f"[{rate}] no NaN or inf in the output", bool(np.all(np.isfinite(out))))
    check(f"[{rate}] no runaway gain from the overlap-add",
          float(np.abs(out).max()) <= float(np.abs(src).max()) * 1.2,
          f"peak {np.abs(out).max():.3f} vs {np.abs(src).max():.3f}")
    check(f"[{rate}] level is preserved, not faded",
          0.7 < float(np.sqrt((out ** 2).mean())
                      / np.sqrt((src ** 2).mean())) < 1.3)

    # ── the edges are the classic bug: a fade nobody asked for ──────────────
    head = float(np.abs(out[:512]).max())
    check(f"[{rate}] the output does not start from silence",
          head > 0.05, f"peak {head:.3f}")
    tail = float(np.abs(out[-512:]).max())
    check(f"[{rate}] the output does not fade out at the end",
          tail > 0.05, f"peak {tail:.3f}")

    # The length has to be the one the caller asked for, in the synthesis
    # coordinate. A short return is the tail bug wearing a different hat.
    expected = int(src.size / rate)
    check(f"[{rate}] the output is the length the rate implies",
          abs(out.size - expected) <= 1,
          f"got {out.size}, expected {expected}")

out = dsp.time_stretch(src, 1.25)

# ── silence stays silence, and does not become noise ────────────────────────
quiet = np.zeros(SR, dtype=np.float32)
out = dsp.time_stretch(quiet, 1.25)
check("silence stretches to silence", float(np.abs(out).max()) < 1e-6)

print()
if failures:
    print(f"{len(failures)} FAILED: {', '.join(failures)}")
    sys.exit(1)
print("all dsp numeric checks passed")
