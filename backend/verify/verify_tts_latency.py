"""verify/verify_tts_latency.py - what the voice engine ACTUALLY costs.

Every remaining number in the latency work is supposed to come from here rather
than from somebody's estimate: the fixed per-call overhead, the real-time
factor, seconds of speech per character, and the VRAM a decode wants per frame.

Runs the real engine through the real host. It does NOT open the app and does
NOT need the vault passphrase - the models are found by scanning the same roots
the app scans, so nothing encrypted is touched.

    .venv/Scripts/python.exe verify/verify_tts_latency.py

Reports raw measurements first and the fitted model second, so a reader can
disagree with the fit without losing the data.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from tts import host as tts_host                # noqa: E402
from tts import refs as tts_refs                # noqa: E402
from tts.registry import scan_roots             # noqa: E402

#: Four sizes, because one point cannot separate a fixed cost from a
#: proportional one. Roughly 3 / 6 / 12 / 20 seconds of speech at ~15 chars a
#: second - English, since Turkish was dropped for this engine.
SAMPLES = [
    "Right, let me take a look at that for you.",
    "Right, let me take a look at that for you. It should only take a moment, "
    "so stay where you are.",
    "Right, let me take a look at that for you. It should only take a moment, "
    "so stay where you are. I have seen this particular problem before, and it "
    "is almost never as bad as it first appears.",
    "Right, let me take a look at that for you. It should only take a moment, "
    "so stay where you are. I have seen this particular problem before, and it "
    "is almost never as bad as it first appears. The trick is to change one "
    "thing at a time and write down what happened, because the alternative is "
    "guessing twice and learning nothing at all.",
]


def _reference_voice_id() -> str:
    """Which voice to clone from, without naming anybody.

    This used to be a string literal, and the string was a personal
    voice label - live in tracked source, and from there published. A
    measurement script has no business carrying it: the id is a property of
    the machine the measurement runs on, not of the code.

    ELYSIUM_LATENCY_VOICE names one explicitly. With nothing set, the first
    voice this install actually has is used, which is what somebody running
    this by hand wants anyway.
    """
    chosen = os.environ.get("ELYSIUM_LATENCY_VOICE", "").strip()
    if chosen:
        return chosen
    from tts import refs as tts_refs
    found = tts_refs.list_voices()
    if not found:
        raise LookupError("no reference voice on this install")
    return found[0].voice_id


def _line(char="-"):
    print(char * 78)


def _find_model():
    # No roots argument: scan_roots already reads the extra roots out of
    # voice/runtimes.json, which is where the checkpoint actually lives.
    result = scan_roots()
    for model in result.models:
        if model.engine_id == "fish_s2":
            return model
    raise SystemExit("no fish_s2 model found - is voice/runtimes.json set up?")


def _reference():
    """The reference voice the app itself uses, as the worker wants it.

    Goes through tts.refs rather than building the path by hand: the folder
    a voice id resolves to is opaque now (an install's own choice of names
    must not sit readable on disk as folder names), so the id is no longer
    a path component - only refs.describe still knows how to find it.

    Which id, in turn, comes from _reference_voice_id: naming one here in
    the source would put back on paper exactly the thing the opaque folder
    names were introduced to take off the disk.
    """
    try:
        voice = tts_refs.describe(_reference_voice_id())
    except tts_refs.RefError:
        return {}, {}
    wav = Path(voice.path) / voice.audio_name
    if not wav.is_file():
        return {}, {}
    extra = {"reference_transcript": voice.transcript} if voice.transcript else {}
    return {"reference_voice": str(wav)}, extra


def _costs(client):
    """The `cost` frames the worker emitted. The ring buffer is private and
    read directly on purpose: this is a measurement harness, not app code, and
    a public accessor invented for one script is worse than an honest reach."""
    out = []
    for frame in list(getattr(client, "_events", [])):
        if frame.get("event") == "progress" and frame.get("stage") == "cost":
            out.append(frame)
    return out


def _fit(xs, ys):
    """Least squares y = c + m*x. Returns (c, m) or None when degenerate."""
    n = len(xs)
    if n < 2:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom <= 0:
        return None
    m = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denom
    return mean_y - m * mean_x, m


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    model = _find_model()
    values, extra = _reference()
    if not values:
        print("WARNING: no reference voice found; measuring UNCLONED speech, "
              "which is not the path the app takes.")

    host = tts_host.get_host()
    print(f"model: {model.uid}")
    _line("=")

    t0 = time.perf_counter()
    host.load(model, values)
    load_seconds = time.perf_counter() - t0
    print(f"load: {load_seconds:.1f}s")
    _line()

    # NOT measured, and the reason is a finding in itself: the first speak
    # after a load encodes the reference clip, and encoding needs the codec, so
    # `_free_for_codec` parks the ~7 GB model and the call pays to bring it
    # back. The prompt tokens are cached afterwards, so it happens exactly
    # once - which makes it a one-off cost that would otherwise contaminate
    # every fitted coefficient below.
    t0 = time.perf_counter()
    host.speak("Warming up.", values, extra=extra)
    print(f"warm-up (excluded): {time.perf_counter() - t0:.2f}s "
          f"- reference encode + model restore, paid once per load")
    _line()

    rows = []
    for i, text in enumerate(SAMPLES, 1):
        t0 = time.perf_counter()
        result = host.speak(text, values, extra=extra)
        wall = time.perf_counter() - t0
        audio = float(result.get("seconds") or 0.0)
        rows.append({"n": i, "chars": len(text), "wall": wall, "audio": audio})
        rtf = wall / audio if audio else float("nan")
        print(f"{i}. {len(text):4d} chars -> {audio:6.2f}s audio in "
              f"{wall:6.2f}s wall   (RTF {rtf:.3f})")

    _line()
    client = getattr(host, "_client", None)
    cost_frames = _costs(client) if client is not None else []
    pol = [f for f in list(getattr(client, "_events", []))
           if f.get("event") == "progress"
           and f.get("stage") == "codec_policy"] if client is not None else []
    if pol:
        print("codec retention decisions:")
        for f in pol:
            print(f"  {f.get('where'):15s} keep={str(f.get('keep')):5s} "
                  f"driver_free={f.get('free_gb')} reserved={f.get('reserved_gb')} "
                  f"allocated={f.get('allocated_gb')} "
                  f"cached_free={f.get('cached_free_gb')} "
                  f"frames={f.get('budget_frames')} "
                  f"forecast={f.get('forecast_gb')}")
    if cost_frames:
        print("VRAM, measured per operation:")
        for f in cost_frames:
            print(f"  {f.get('kind'):9s} units={f.get('units'):5} "
                  f"peak={f.get('peak_gb')} GB  retained={f.get('retained_gb')} GB  "
                  f"predicted={f.get('predict_gb')} GB "
                  f"(samples {f.get('samples')})")
    else:
        print("no cost frames - a build without the measurement probe, or no CUDA.")

    _line("=")
    print("FITTED MODEL")
    good = [r for r in rows if r["audio"] > 0]
    fit = _fit([r["audio"] for r in good], [r["wall"] for r in good])
    if fit is None:
        print("  not enough usable points to fit.")
    else:
        c, rtf = fit
        print(f"  fixed cost per call   c   = {c:6.3f} s")
        print(f"  real-time factor      RTF = {rtf:6.3f}   "
              f"({1 / rtf:.2f}x realtime)" if rtf > 0 else "")
        print(f"  predicted time(d)     = {c:.3f} + {rtf:.3f} * d")
    secs_per_char = [r["audio"] / r["chars"] for r in good if r["chars"]]
    if secs_per_char:
        avg = sum(secs_per_char) / len(secs_per_char)
        print(f"  seconds per character = {avg:6.4f}  "
              f"({1 / avg:.1f} chars/s of speech)")
    if fit and secs_per_char:
        c, rtf = fit
        target = 3.0
        # The number items 3 and 4 actually need: how much text may go in the
        # first chunk and still start speaking inside the budget.
        room = (target - c) / rtf if rtf > 0 else 0.0
        print(f"\n  at a {target:.0f}s budget the first chunk may be up to "
              f"{room:.1f}s of speech = ~{int(room / avg)} characters")
    _line("=")

    host.unload("measurement finished")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
