"""dsp.py - reading speed, done to the audio instead of to the model.

Fish S2 has no speaking-rate parameter. Its hosted API does (`prosody.speed`,
0.5-2.0) but the open-source server never exposed it, and the inline tag
vocabulary is free-form prose - `[slow deliberate pace]` is a hint the model
may or may not honour, and honours less reliably outside English. A dial that
sometimes moves is worse than no dial.

So the rate is applied to the rendered waveform, with WSOLA - Waveform
Similarity Overlap-Add. The audio is cut into overlapping frames; to slow down,
frames are repeated, to speed up they are skipped, and each frame is joined at
the offset where the two waveforms most resemble each other. That last part is
the whole difference between WSOLA and plain overlap-add: splicing at an
arbitrary offset fights the pitch period and produces the metallic warble that
makes cheap time-stretching obvious. PITCH IS UNCHANGED because the sample rate
never changes - this is not a tape played faster.

WHY HERE AND NOT IN THE APP PROCESS
    The app venv has no numpy and must not gain one: `test_tts_packaging.py`
    forbids engine modules at worker module scope, and `refs.py` records the
    reasoning - an audio library in the app process is a cost paid at every
    launch for something only the voice path ever needs. numpy is therefore
    imported inside the functions, which also keeps a damaged venv exiting with
    the "environment is broken" code instead of dying at import.

RANGE
    The dial is clamped to 0.80-1.25 on purpose. Inside that band the artefacts
    are inaudible on speech; outside it, transients smear (hard consonants soften)
    and breath turns watery - and this reference voice is already breathy.
"""
from __future__ import annotations

#: What the user is allowed to ask for. Wider is technically possible and
#: audibly worse; see the module docstring.
MIN_RATE = 0.80
MAX_RATE = 1.25

#: Below this, resampling would cost more than it buys - a 2% rate change is
#: not audible and not worth an extra pass over every sentence.
RATE_EPSILON = 0.02

_FRAME = 1024          # ~23 ms at 44.1 kHz: longer than a pitch period at any
                       # speaking F0, short enough to track prosody
_TOLERANCE = 256       # how far the similarity search may move a splice


def clamp_rate(rate: float | None) -> float:
    """Bring any requested rate into the range that still sounds like speech."""
    if rate is None:
        return 1.0
    try:
        value = float(rate)
    except (TypeError, ValueError):
        return 1.0
    if value != value:                      # NaN
        return 1.0
    return max(MIN_RATE, min(MAX_RATE, value))


def is_noop(rate: float | None) -> bool:
    """Is this rate close enough to 1.0 that stretching is pointless?"""
    return abs(clamp_rate(rate) - 1.0) < RATE_EPSILON


def time_stretch(samples, rate: float, *, frame: int = _FRAME,
                 tolerance: int = _TOLERANCE):
    """Change duration without changing pitch.

    `rate` reads like playback speed: 1.25 is faster and shorter, 0.8 is slower
    and longer. Returns a float32 array; the input is not modified.
    """
    import numpy as np

    x = np.asarray(samples, dtype=np.float32).reshape(-1)
    rate = clamp_rate(rate)
    if is_noop(rate) or x.size < frame * 2:
        # Too short to have two frames to blend: any stretch here would be one
        # window fading into nothing, which sounds worse than the wrong length.
        return x.copy()

    hop_out = frame // 2
    hop_in = hop_out * rate
    window = np.hanning(frame).astype(np.float32)

    # The output length is decided HERE, from the original input, and every
    # loop bound below has to be able to reach it.
    n0 = x.size
    end = int(n0 / rate)

    # Analysis and synthesis are two different coordinate systems, and the loop
    # used to stop in the first while the return length was measured in the
    # second. The last ~512 input samples were never rendered at ANY rate, and
    # at 0.80 the output ended in 683 samples of pure silence
    # (abs(out[-512:]).max() == 0.0) - the exact thing dsp_numeric_check.py:94
    # forbids, which never caught it because it only ever evaluated 1.25, the
    # one rate that cannot trigger it.
    #
    # Zero-padding the analysis tail lets the loop keep going until the
    # SYNTHESIS position is covered. The frames that read into the pad blend
    # real audio into silence, which is the taper a final frame should have
    # anyway, and the weight normalisation below already accounts for it.
    frames_needed = int(end / hop_out) + 2
    reach = int(frames_needed * hop_in) + tolerance + frame + 1
    if reach > x.size:
        x = np.concatenate(
            [x, np.zeros(reach - x.size, dtype=np.float32)])

    out_len = end + frame
    y = np.zeros(out_len + frame, dtype=np.float32)
    weight = np.zeros_like(y)

    # `target` is what SHOULD come next if the previous frame simply continued.
    # Each new frame is chosen to look as much like that as possible, which is
    # what keeps the pitch period from being cut mid-cycle.
    target = x[:frame].copy()
    prev = 0
    m = 0
    while True:
        ideal = int(m * hop_in)
        lo = max(0, ideal - tolerance)
        hi = min(x.size - frame, ideal + tolerance)
        if hi <= lo:
            break

        if hi - lo > 1:
            # One matmul over every candidate offset rather than a Python loop:
            # the search is the expensive part and this keeps a sentence inside
            # the realtime budget the queue depends on.
            view = np.lib.stride_tricks.sliding_window_view(
                x[lo:hi + frame], frame)
            scores = view @ target
            norms = np.sqrt((view * view).sum(axis=1)) + 1e-9
            best = int(np.argmax(scores / norms))
        else:
            best = 0
        k = lo + best

        start = m * hop_out
        y[start:start + frame] += x[k:k + frame] * window
        weight[start:start + frame] += window

        m += 1
        # Synthesis coordinate, not analysis: this is the bound that decides
        # whether the RETURNED array is fully rendered.
        if m * hop_out >= end:
            break
        nxt = k + hop_out
        if nxt + frame > x.size:
            break                      # safety net; the pad above prevents it
        target = x[nxt:nxt + frame]
        prev = k

    del prev
    # Hann at 50% overlap sums to 1 in the interior but tapers at both ends;
    # dividing by the actual window sum instead of assuming it keeps the first
    # and last frames at full level rather than fading them out for no reason.
    live = weight > 1e-6
    y[live] /= weight[live]
    # `end` came from the ORIGINAL length above. Recomputing it from x.size
    # here would now include the analysis pad and stretch the answer.
    return y[:max(1, end)].astype(np.float32)
