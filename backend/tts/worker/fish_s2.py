"""tts/worker/fish_s2.py - Fish Audio S2 Pro, the ENGINE half.

Runs in the engine's own interpreter (`<runtime>/python.exe -u fish_s2.py`), so
it may import torch and `fish_speech` and may import NOTHING from the app except
the sibling `_wire` protocol module.

THE RECIPE IS MEASURED, NOT INVENTED
------------------------------------
Every line below that looks like a magic number came off an RTX 5080 during the
bake-off (`Desktop/tts_bakeoff/fish-speech/gen_fish*.py`, roadmap section V3-a).
Changing one of them changes a number somebody measured:

  * `Float8DynamicActivationFloat8WeightConfig` - fp8 activations AND weights, so
    torch.compile fuses the dequant into a real `_scaled_mm`. `Float8WeightOnlyConfig`
    was measured SLOWER than bf16 at batch size 1 (~0.46x: eager dequant every step).
  * `init_model(..., compile=True)` - needs triton-windows and an MSVC environment
    (`vcvars64.bat`, else "cl is not found"). torch.compile is lazy, so a broken
    toolchain only explodes on the first decoded token: the warm-up below is where
    we find out, and it falls back to the eager `decode_one_token_ar` and says so
    through a progress event instead of dying.
  * KV cache capped. `model.config.max_seq_len` is 32768 and that cache is the real
    VRAM hog. NOTE the trap: `init_model` sets `model._cache_setup_done = False`, and
    `generate()` re-runs `setup_caches(max_seq_len=model.config.max_seq_len)` unless
    that flag is True - so capping the cache without setting the flag caps nothing.
    We set it, which is why `kv_cache_len` is a knob that actually moves memory.
  * `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`, set before torch is imported.
  * A persistent `TORCHINDUCTOR_CACHE_DIR`: first compile ~346 s, cached ~59 s. The
    host's `TTS_LOAD_TIMEOUT_S` is 180 s, so the cache is not a nicety - a cold
    inductor cache will outlast the load timeout. Taken from `values` or the env.
  * The aggressive free between text2semantic and the DAC decode
    (`model=None; decode_one_token=None; gc.collect(); torch.cuda.empty_cache()`) -
    without it the decode OOMs. In the bake-off that was unconditional because the
    script ran once and exited. A worker has to survive the next sentence, so it is
    conditional on measured free VRAM (see `_free_for_codec`): the eviction is
    byte-for-byte the measured one, it just only fires on a card that needs it, and
    the next request transparently reloads.
  * `chunk_length=300` is what the bake-off passed, and it is a BYTE budget that
    `group_turns_into_batches` applies only BETWEEN `<|speaker:N|>` turns. One tag
    is one turn is one batch, so the text is NOT chunked and the whole reply is
    encoded into a single prompt. The context arithmetic below measures that
    prompt with the model's own tokenizer (`_text_tokens`) instead of assuming
    chunk_length, because the cache is capped and `KVCache.update` indexes it.
  * Cloning REQUIRES a transcript. `generate_long` computes
    `use_prompt = bool(prompt_text) and bool(prompt_tokens)`; audio alone silently
    produces a NON-cloned voice, which is worse than an error. A reference without a
    transcript is `tts_transcript_required`.
  * Output is 44100 Hz (`configs/modded_dac_vq.yaml: sample_rate: 44100`).

PAYLOADS
--------
load        {"model_path", "engine_id", "variant", "values", "cache_dir"}
synthesize  {"text", "out", "values", ...optional reference fields}
prepare_ref {"audio"|"path"|"clip", "transcript", "out"(optional .npy)}
ping        {}

The reference for a synthesize may arrive as `req["reference"]` (a dict or a path
string), as `values["reference_voice"]`, or as a voice DIRECTORY laid out the way
the roadmap describes: `ref.wav` + `transcript.txt` + `prompt_tokens.npy`.
"""
from __future__ import annotations

import contextlib
import gc
import os
import re
import sys
import threading
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _wire  # noqa: E402  (the sibling protocol module, stdlib-only)
import _dsp   # noqa: E402  (sibling DSP; numpy is imported inside it)
import _fit   # noqa: E402  (sibling estimator, shared with the host's pacing)

# Must be in place BEFORE torch initialises its CUDA allocator, which is why it
# lives at module scope and not in the load handler.
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

# fish_speech loads its tokenizer through `transformers.AutoTokenizer.from_pretrained`
# (fish_speech/tokenizer.py). With a real local directory that stays on disk, but a
# single missing file is enough for transformers to fall back to resolving the name
# against the Hub. This app never talks to the network, so say so before the import
# happens rather than trusting that the checkpoint is complete.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

# fish_speech logs through loguru and the model text can be Turkish. On a cp1254
# console a single "ş" would raise UnicodeEncodeError inside a log call and take
# the worker down for no reason at all.
for _stream in (sys.stderr,):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - an old stream object; not worth dying for
        pass

# ── the recipe's constants ───────────────────────────────────────────────────
_DEVICE = "cuda"
_SAMPLE_RATE = 44100          # configs/modded_dac_vq.yaml
_CHUNK_LENGTH = 300           # generate_long's `chunk_length`: a BYTE budget that
                              # only splits BETWEEN <|speaker:N|> turns. The value
                              # the bake-off used. It is NOT a token count and must
                              # never be used as one - see `_text_tokens`.
_PROMPT_OVERHEAD = 192        # system/user/assistant framing around the text
# ONE number. There used to be three - 4.0, 3.0, 1.0 - each answering a
# slightly different question with a guess, and every one of them wrong in its
# own way: 4.0 evicted the codec after every sentence by counting the codec's
# own footprint against it, 3.0 was a fixed forecast of a cost that is not
# fixed, and 1.0 was correct but only ever consulted by one of the three
# callers.
#
#: What the REST OF THE MACHINE is owed, at all times. Not a TTS budget - a
#: floor under the desktop, the browser, and whatever game is running. Measured:
#: with both models resident this card reports 1.76-2.02 GB free and the owner
#: confirmed that is comfortable (streaming and video playback still fine).
#: Every other question - may the codec stay, is there room to decode - is now
#: answered by measuring the operation and checking it against THIS.
_VRAM_RESERVE_GB = 1.0

#: The prior, and the last resort. A reserve check needs to know what an
#: operation costs, and on the very first run nothing has been measured yet -
#: applying "evict at 1 GB" with no cost model is exactly the OOM this replaces.
#: So the old fixed floor does not disappear, it changes job: until a real
#: measurement exists, assume a full-budget decode wants what 3.0 GB reserved.
#: The first real decode overwrites it. Keeping it means run one behaves like
#: the proven-safe old code instead of trusting a check with nothing to check.
_SEED_DECODE_GB = 3.0
_SEED_DECODE_FRAMES = 800     # the default max_new_tokens the seed refers to
#: What bringing the DAC codec onto the card costs before anything has measured
#: it. NOT the file size: `codec.pth` is 1.74 GB on disk and 1.9 was that plus
#: a little, but the thing is 4.915 GB once it is on the card - measured, peak
#: and retained alike (5.22 on the very first load from disk, 4.915 on every
#: restore since). Seeding at 1.9 under-reserved by a factor of two and a half
#: on the one load where nothing is known yet, which is the load least able to
#: recover from being wrong. A prior, like _SEED_DECODE_GB: the first real load
#: replaces it.
_SEED_CODEC_GB = 5.2
_WARMUP_TEXT = "<|speaker:0|>Ready."
_WARMUP_TOKENS = 16
_REF_MIN_SECONDS = 0.6
_REF_MAX_SECONDS = 120.0
_TOKENS_SUFFIX = ".prompt_tokens.npy"

# ── knobs: names and defaults MUST match adapters/fish_s2.py ─────────────────
# A name that does not match is a settings dial the user turns to no effect.
_DEFAULTS = {
    "language": "en",
    "temperature": 0.7,
    "top_p": 0.8,
    "top_k": 30,
    "max_new_tokens": 800,
    "kv_cache_len": 2048,
    "reference_voice": "",
}
#: `generate_long` requires the argument and then never applies it - the real
#: repetition control is a hardcoded RAS inside decode_one_token_ar. It is a
#: constant here rather than a dial precisely because a knob that cannot move
#: anything is worse than no knob.
_REPETITION_PENALTY_UNUSED = 1.1

_LIMITS = {                    # ParamSpec minimum/maximum, re-applied here because
    "temperature": (0.1, 1.5),  # a worker never trusts a number it did not clamp
    "top_p": (0.1, 1.0),
    "top_k": (1, 200),
    "max_new_tokens": (64, 2048),
    "kv_cache_len": (512, 8192),
}

STATE = {
    "model": None,          # the DualARTransformer
    "decode": None,         # decode_one_token (compiled, or the eager fallback)
    "codec": None,          # the DAC / firefly codec, loaded lazily
    "model_path": "",
    "kv_len": 0,
    "compiled": False,
    "quantized": False,
    "evicted": False,       # the t2s model was freed to let a decode finish
    "codec_parked": None,   # the codec, held in system RAM between sentences
    #: The t2s model and its compiled decode, held in system RAM. The rung
    #: between "codec parked" and "model destroyed": ~7 GB back for a PCIe copy
    #: instead of a 28 s rebuild from disk. Both are kept together because the
    #: compiled decode is a function OVER this model - restoring one without
    #: the other would rebuild a graph that already exists.
    "model_parked": None,
    # Sticky once _warmup proves the compiler is unusable here (no MSVC /
    # triton-windows). A property of the MACHINE, so a post-eviction rebuild
    # must not ask for torch.compile again - see _build_model.
    "compile_broken": False,
}

_ENGINE: dict = {}

# The exception `_wire.oom()` hands back. Recognising it by type means an OOM that
# has already been named stays named, instead of being re-wrapped by every layer
# it passes through - or, worse, being read as "this model would not load".
_OOM_TYPE = type(_wire.oom(""))


# ── engine import: a damaged install must exit 3, not 1 ──────────────────────
def _engine() -> dict:
    """Import torch and fish_speech exactly once.

    Anything that goes wrong here means the runtime is broken rather than the
    model, and `EXIT_ENGINE_IMPORT` is the only way to say that.
    """
    if _ENGINE:
        return _ENGINE
    try:
        import numpy as np
        import soundfile as sf
        import torch
        import torchaudio
        from fish_speech.models.dac.inference import load_model as load_dac
        from fish_speech.models.text2semantic.inference import (
            decode_one_token_ar,
            generate_long,
            init_model,
        )
    except Exception:  # noqa: BLE001 - report the real cause, then exit 3
        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()
        sys.exit(_wire.EXIT_ENGINE_IMPORT)
    _ENGINE.update(
        np=np, sf=sf, torch=torch, torchaudio=torchaudio,
        load_dac=load_dac, init_model=init_model, generate_long=generate_long,
        decode_eager=decode_one_token_ar,
    )
    return _ENGINE


# ── small helpers ────────────────────────────────────────────────────────────
def _progress(send, stage: str, pct: float | None = None, **fields) -> None:
    payload = dict(fields)
    if pct is not None:
        payload["pct"] = round(float(pct), 3)
    send(_wire.event("progress", stage=stage, **payload))


#: How often to prove we are still alive during an opaque long operation.
#: Comfortably inside the host's 180 s silence budget, and rare enough that
#: the frames are not themselves noise.
_HEARTBEAT_SECONDS = 20.0


@contextlib.contextmanager
def _heartbeat(send, stage: str, pct: float | None = None, **fields):
    """Emit a `progress` frame every ~20 s until the block exits.

    The host's load budget is a SILENCE budget, not a duration one:
    worker_client measures `monotonic() - last_progress` against
    TTS_LOAD_TIMEOUT_S (180 s) and kills the worker when it lapses. The first
    torch.compile takes ~346 s and emits nothing, so a clean install could
    NEVER finish its first model load - the host killed the worker at 180 s
    and answered tts_load_timeout / HTTP 504, every time, on every machine
    that had not compiled before.

    It has to be a thread. The compile is opaque: `generate_long`'s FIRST
    yield is the slow one, so there is no loop body to report from and
    nothing inside the operation can be instrumented from here.

    The thread is stopped and JOINED on exit, so the main thread's next send
    cannot interleave with a heartbeat write.
    """
    stop = threading.Event()

    def _tick() -> None:
        elapsed = 0.0
        while not stop.wait(_HEARTBEAT_SECONDS):
            elapsed += _HEARTBEAT_SECONDS
            try:
                _progress(send, stage, pct,
                          elapsed_seconds=round(elapsed, 1), **fields)
            except Exception:                   # noqa: BLE001
                # A dead pipe is the host's business, not a reason to take
                # the compile down with us.
                return

    thread = threading.Thread(target=_tick, name=f"tts-heartbeat-{stage}",
                              daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=2.0)


def _num(values: dict, key: str, cast):
    raw = values.get(key, _DEFAULTS[key])
    try:
        out = cast(raw)
    except (TypeError, ValueError):
        out = cast(_DEFAULTS[key])
    lo, hi = _LIMITS[key]
    return cast(min(max(out, lo), hi))


def _knobs(values: dict) -> dict:
    return {
        "language": str(values.get("language", _DEFAULTS["language"]) or "en"),
        "temperature": _num(values, "temperature", float),
        "top_p": _num(values, "top_p", float),
        "top_k": _num(values, "top_k", int),
        # NOT read from `values`. The adapter stopped offering the dial once the
        # engine was read and found to accept `repetition_penalty` and never
        # apply it - but this line kept asking for it, and `_num`'s default
        # argument `_DEFAULTS[key]` is evaluated EAGERLY, so a key absent from
        # `_DEFAULTS` raised KeyError on every load and every synthesis whether
        # or not the caller supplied a value. That killed the worker outright.
        # The constant below is passed on only because `generate_long` demands
        # the argument; the engine's real repetition control is a fixed RAS.
        "repetition_penalty": _REPETITION_PENALTY_UNUSED,
        "max_new_tokens": _num(values, "max_new_tokens", int),
        "kv_cache_len": _num(values, "kv_cache_len", int),
    }


def _vram_snapshot() -> dict:
    """What the allocator is holding, for the retention decisions to report.

    `_free_gb` reads the DRIVER's free memory, which counts torch's own cached
    blocks as used - so the obvious theory for a needless eviction is that the
    check is mistaking its own cache for somebody else's memory. Measured here,
    that cache is 0.32 GB: the theory is wrong and the numbers say so, which is
    why they are printed rather than reasoned about.
    """
    torch = _ENGINE.get("torch")
    if torch is None:
        return {}
    try:
        res = torch.cuda.memory_reserved() / 1e9
        alloc = torch.cuda.memory_allocated() / 1e9
    except Exception:  # noqa: BLE001
        return {}
    return {"reserved_gb": round(res, 2), "allocated_gb": round(alloc, 2),
            "cached_free_gb": round(res - alloc, 2)}


def _free_gb() -> float:
    torch = _ENGINE.get("torch")
    if torch is None or not torch.cuda.is_available():
        return 0.0
    try:
        free, _total = torch.cuda.mem_get_info()
        return free / 1e9
    except Exception:  # noqa: BLE001 - a VRAM reading is advice, not a contract
        return 0.0


def _sweep() -> None:
    torch = _ENGINE.get("torch")
    gc.collect()
    if torch is not None and torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.synchronize()


# ── what an operation actually costs: measured, never guessed ────────────────
# Every threshold in this file used to be a number somebody picked. 4.0, then
# 3.0, then 1.0 - each one a guess about a cost nobody had measured, and each
# one wrong in a different way. This is the machinery that replaces the guess.
#
#: kind -> `_fit.Line` over (frames, GB). The estimator itself lives in `_fit`
#: because the host's pacing model needs exactly the same arithmetic about
#: seconds, and two copies of a subtle rule are two rules that will disagree.
#:
#: Seeded with the OLD fixed floor rather than with nothing: until a real
#: measurement exists, assume a full-budget decode wants what 3.0 GB reserved.
#: See `_SEED_DECODE_GB` - the number did not vanish, it changed job from
#: threshold to prior, and the first real decode overwrites it.
_COSTS: dict[str, _fit.Line] = {}


def _cost_line(kind: str) -> _fit.Line:
    line = _COSTS.get(kind)
    if line is None:
        if kind == "frames":
            # Semantic frames per character. Measured on an RTX 5080 across the
            # four verify samples: 22.6 s of audio from 334 characters at the
            # codec's ~21.5 Hz is 1.42. Declared only so that ONE sample can be
            # placed on a line - with two the data determines the slope itself,
            # and `_expected_frames` answers nothing at all until then.
            line = _fit.Line(seed_slope=1.42)
        elif kind == "codec":
            # A fixed-size object: it does not grow with frames, so declaring a
            # per-frame slope for it would be a claim the data can never
            # contradict (every sample is units=1).
            line = _fit.Line(seed_fixed=_SEED_CODEC_GB, seed_slope=0.0)
        else:
            line = _fit.Line(seed_slope=_SEED_DECODE_GB / _SEED_DECODE_FRAMES)
        _COSTS[kind] = line
    return line


def _observe_cost(kind: str, units: int, gb: float) -> None:
    """Fold one measurement into the running fit for `kind`."""
    if units <= 0 or gb <= 0.0:
        return                       # an unsized or unmeasured op teaches nothing
    _cost_line(kind).observe(float(units), gb)


def _expected_frames(chars: int) -> int | None:
    """How many semantic frames THIS text will probably need, or None.

    The reserve guard wants the size of the work it is about to do. What it had
    was `max_new_tokens`, the user's "Max length" dial - a CEILING for one
    utterance, and on a 16 GB card a ruinous forecast: 800 frames reads as
    ~4.4 GB, so with the codec resident the check failed and the codec was
    parked before every single generation. Measured, the sentences the queue
    actually hands over run 25-435 frames; 800 is about 37 seconds of speech
    and sentence-level chunking makes it unreachable.

    Learnt rather than declared - `produced` is counted after every generation
    anyway, so the ratio is free. None until something has been measured, and
    then the caller keeps its old worst case, which is why run one behaves
    exactly as before.
    """
    line = _COSTS.get("frames")
    if line is None or not line.measured or chars <= 0:
        return None
    return int(line.predict(float(chars)))


def _planning_cost(kind: str, units: int) -> float | None:
    """What the reserve check will ACTUALLY be told this operation costs.

    One function so the policy and the report cannot disagree. They did: the
    codec's reserve moved to `worst` (see `_codec_need`) while the progress
    frame kept printing `predict`, so the diagnostic still showed 15.2 GB for a
    decision that was by then being made on 4.9. A number nobody consults, in
    the one place people go to read the decisions, is worse than no number.
    """
    line = _COSTS.get(kind)
    if line is None or not line.measured or units < 0:
        return None
    if kind.startswith("codec"):
        # Fixed-size: the largest ever seen, never an extrapolation.
        return line.worst
    return line.predict(float(units))


def _predict_cost(kind: str, units: int) -> float | None:
    """Pessimistic GB this operation will want, or None if never measured.

    None is not zero. A caller with no measurement must say so and fall back to
    its own conservative behaviour - silently predicting 0 GB would turn the
    reserve check into a rubber stamp on exactly the first run, which is the one
    run where nothing is known.
    """
    line = _COSTS.get(kind)
    if line is None or not line.measured or units < 0:
        return None
    return line.predict(float(units))


def _seed_gb(units: int) -> float:
    """What to assume an operation costs before anything has been measured."""
    if units <= 0:
        return _SEED_DECODE_GB
    return _SEED_DECODE_GB * (float(units) / _SEED_DECODE_FRAMES)


def _codec_need() -> float:
    """What putting the codec back on the card will cost, or 0 if it is there.

    The missing term (audit KÖK 9). Every caller of `_fits` is on its way to
    the codec, and `_free_gb()` is read while the codec is OFF the card -
    `_decode_to_audio` calls `_codec(send)` immediately after, which brings
    ~4.9 GB back. So the check compared today's free memory against a cost
    that provably excluded the one thing about to be loaded, and with a 1.0 GB
    reserve the gate could pass with less headroom than the codec alone needs.

    Worse on the `frames=0` callers, where the decode term is ~0 and `_fits`
    collapsed to "free >= 1.0" - and `_codec(send)` sits OUTSIDE the try that
    carries the OOM retry ladder, so the sentence was simply lost.

    Measured once loaded (see `_codec`), constant until then. Units are 1
    because the codec is one indivisible object: it does not scale with frames.

    THE WORST SEEN, NOT A PREDICTION (measured on an RTX 5080). `Line.predict`
    answers `fit + k * dev` with k = 4, and `dev` is seeded at HALF the estimate
    on the second sample the way RFC 6298 seeds its variance. That is right for
    a quantity with real spread - a network round trip, a decode whose size
    varies - and wrong for this one: every sample is the same indivisible
    object, so the margin is bootstrapped from the level rather than from any
    observed variation. The arithmetic came out at 4.9 + 4 x 2.53 = 15.2 GB for
    a 4.9 GB load, and `beta` = 0.25 needs about ten samples to decay it back:

        samples   2      3      4      5      6
        peak     4.9    4.9    4.9    4.9    4.9
        predict 15.2   12.8   10.9    9.5    8.4

    On a 16 GB card that meant `_fits` refused for the first ~10 sentences
    after every load, so text2semantic was parked to system RAM and pulled back
    for each one: about 1.2 s of the 2.29 s fixed cost per call, paid for a
    shortage that was arithmetic rather than real.

    The honest pessimistic answer for a fixed-size object is the worst value
    actually seen, `Line.worst` - which decays, so one freak sample cannot pin
    the reserve there for the life of the worker the way a plain running
    maximum would. Headroom is not lost: `_fits` still holds
    `_VRAM_RESERVE_GB` back on top of this.
    """
    if STATE.get("codec") is not None:
        return 0.0
    planned = _planning_cost("codec", 1)
    return _SEED_CODEC_GB if planned is None else planned


def _fits(units: int, *kinds: str) -> bool:
    """Do these operations fit while leaving the reserve intact?

    The LARGEST cost, not the sum: a generation finishes before its decode
    begins, so their peaks never coexist. Adding them would evict for a
    high-water mark that never happens.

    The codec is the exception and is ADDED rather than maxed: it is resident
    THROUGHOUT the decode rather than peaking alongside it, so its footprint
    is a floor under the decode's peak, not an alternative to it.
    """
    need = 0.0
    for kind in (kinds or ("decode",)):
        predicted = _predict_cost(kind, units)
        need = max(need, _seed_gb(units) if predicted is None else predicted)
    need += _codec_need()
    return (_free_gb() - need) >= _VRAM_RESERVE_GB


class _measure:
    """Record the allocator peak of one operation, in GB above where it started.

    `max_memory_allocated()` and NOT `mem_get_info`: the question here is what
    THIS operation asks for, not what the rest of the machine is doing to the
    card while it runs. The reserve check reads free memory separately. Those
    are two different questions, and mixing them is how the old thresholds got
    confused in the first place.

    Never fatal, and never a swallower: a measurement that cannot be taken
    leaves the estimate exactly as it was, and an operation that raised is not
    measured at all - a failed op's peak says nothing about what a successful
    one costs.
    """

    def __init__(self, kind: str, units: int, send=None) -> None:
        self.kind = kind
        self.units = max(0, int(units))
        self.send = send
        self.torch = _ENGINE.get("torch")
        self.base = 0.0

    def __enter__(self) -> "_measure":
        torch = self.torch
        if torch is None:
            return self
        try:
            self.base = torch.cuda.memory_allocated() / 1e9
            torch.cuda.reset_peak_memory_stats()
        except Exception:  # noqa: BLE001 - measuring must never break the op
            self.torch = None
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        torch = self.torch
        if torch is None or exc_type is not None:
            return False
        try:
            peak = torch.cuda.max_memory_allocated() / 1e9
        except Exception:  # noqa: BLE001
            return False
        used = max(0.0, peak - self.base)
        _observe_cost(self.kind, self.units, used)
        # PEAK and RETAINED are two different questions. The peak is the
        # high-water mark the operation has to be able to reach; what it LEAVES
        # BEHIND is what still occupies the card while the next one runs.
        #
        # REPORTED, not fitted. It answered one question and answering it was
        # the point: `codec.pth` is 1.74 GB on disk, so reasoning from the file
        # said the 5 GB was mostly a load-time transient and `_fits` was
        # over-reserving by three gigabytes. It is not - the codec measures
        # 4.915 GB peak AND 4.915 GB retained, while a decode retains 0.0. That
        # is what licenses `_fits` to ADD the codec's term and MAX the others.
        # Folding it into a second estimator nothing consults would be a fit
        # kept warm for a reader that does not exist; the frame is enough.
        try:
            retained = max(0.0, torch.cuda.memory_allocated() / 1e9 - self.base)
        except Exception:  # noqa: BLE001
            return False
        if self.send is not None:
            line = _COSTS.get(self.kind)
            # Reported, not just recorded. Every policy regression in this file
            # was caught by SEEING a decision, never by reasoning about one.
            _progress(self.send, "cost", kind=self.kind, units=self.units,
                      peak_gb=round(used, 3),
                      samples=int(line.n) if line is not None else 0,
                      predict_gb=round(_planning_cost(self.kind, self.units) or 0.0, 3),
                      retained_gb=round(retained, 3))
        return False


def _oom_guard(exc: BaseException, code: str, what: str):
    """Turn an engine explosion into something the app can say out loud."""
    if isinstance(exc, _OOM_TYPE):
        return exc
    if _wire.is_oom(exc):
        return _wire.oom(f"{what}: {exc}")
    return _wire.WorkerError(code, f"{what}: {type(exc).__name__}: {exc}")


# ── loading ──────────────────────────────────────────────────────────────────
def _inductor_cache_dir(req: dict) -> str:
    """The persistent compile cache. 346 s cold, 59 s warm - it is the difference
    between a load that fits inside the host's timeout and one that does not."""
    values = req.get("values") or {}
    for candidate in (values.get("inductor_cache_dir"),
                      os.environ.get("TORCHINDUCTOR_CACHE_DIR")):
        if candidate:
            return str(candidate)
    base = req.get("cache_dir") or ""
    if base:
        return str(Path(base) / "inductor")
    return ""


def _build_model(ckpt: Path, kv_len: int, send) -> None:
    """init -> fp8 dynamic quant -> capped KV cache -> warm the compiler.

    Exactly the order the bake-off measured. Quantising after `init_model` is
    deliberate: `init_model` wraps `decode_one_token` in torch.compile first, so
    the fp8 modules are what inductor eventually traces.
    """
    eng = _engine()
    torch = eng["torch"]

    _progress(send, "loading", 0.1, detail="text2semantic weights")
    t0 = time.perf_counter()
    # Once _warmup has PROVEN the compiler is unusable on this machine, asking
    # for it again is not optimism, it is a crash. _ensure_model rebuilds after
    # a VRAM eviction WITHOUT re-running _warmup, so this used to restore
    # compiled=True and the compiled decode - and the first token then hit the
    # same "cl is not found" failure inside _op_synthesize, where there is no
    # fallback. A voice that was working (slowly) became permanently broken,
    # rebuild-and-fail on every sentence, until the worker was restarted.
    want_compile = not STATE.get("compile_broken")
    model, decode_one_token = eng["init_model"](
        str(ckpt), _DEVICE, torch.bfloat16, compile=want_compile,
    )
    STATE["compiled"] = want_compile

    # fp8 DYNAMIC activation + weight. Weight-only was measured at ~0.46x of bf16.
    try:
        from torchao.quantization import (
            Float8DynamicActivationFloat8WeightConfig,
            quantize_,
        )
        _progress(send, "quantizing", 0.4, detail="fp8 dynamic activation + weight")
        quantize_(model, Float8DynamicActivationFloat8WeightConfig())
        torch.cuda.synchronize()
        torch.cuda.empty_cache()
        STATE["quantized"] = True
    except Exception as exc:  # noqa: BLE001 - bf16 still works, just slower
        if _wire.is_oom(exc):
            raise                       # an OOM is not a "carry on in bf16" case
        STATE["quantized"] = False
        _progress(send, "quantize_skipped", 0.4,
                  detail=f"{type(exc).__name__}: {exc}"[:200],
                  note=_wire.NOTE_STAYING_BF16)

    # The cap only holds because of the flag: see the module docstring.
    hard = int(model.config.max_seq_len)
    cap = max(512, min(int(kv_len), hard))
    _progress(send, "cache", 0.55, max_seq_len=cap, model_default=hard)
    with torch.device(_DEVICE):
        model.setup_caches(max_batch_size=1, max_seq_len=cap, dtype=torch.bfloat16)
    model._cache_setup_done = True
    torch.cuda.synchronize()

    STATE["model"] = model
    STATE["decode"] = (decode_one_token if want_compile
                       else eng["decode_eager"])
    STATE["kv_len"] = int(getattr(model, "max_seq_len", cap))
    STATE["evicted"] = False
    # A freshly built model makes any parked copy stale AND redundant - holding
    # it would be ~7 GB of host memory nothing will ever restore.
    STATE["model_parked"] = None
    _progress(send, "loaded", 0.65, seconds=round(time.perf_counter() - t0, 1))


def _warmup(send) -> float:
    """Compile now, so the user does not pay 346 s in the middle of a sentence.

    torch.compile is lazy: this is the first place a missing MSVC/triton toolchain
    can be observed. If it blows up we drop to the eager `decode_one_token_ar`,
    say so, and keep the model - a slow voice beats no voice.
    """
    eng = _engine()
    t0 = time.perf_counter()
    _progress(send, "compiling", 0.7,
              note=_wire.NOTE_FIRST_COMPILE_SLOW)
    try:
        # ~346 s of total silence without this, against a 180 s host budget:
        # the single reason a clean install could never complete its first
        # model load. See _heartbeat.
        with _heartbeat(send, "compiling", 0.7,
                        note=_wire.NOTE_COMPILING):
            _run_generation(_WARMUP_TEXT, None, None, _WARMUP_TOKENS,
                            temperature=0.7, top_p=0.9, top_k=30,
                            repetition_penalty=1.1)
    except Exception as exc:  # noqa: BLE001
        if _wire.is_oom(exc):
            raise                       # not a toolchain problem; do not retry
        _progress(send, "compile_failed", 0.7,
                  detail=f"{type(exc).__name__}: {exc}"[:200],
                  note=_wire.NOTE_EAGER_FALLBACK)
        STATE["compiled"] = False
        STATE["decode"] = eng["decode_eager"]
        # Sticky for the life of the worker: a missing MSVC/triton toolchain is
        # a property of the machine, not of this load. _build_model reads it.
        STATE["compile_broken"] = True
        _sweep()
        # The eager retry is far quicker but not instant, and it runs on the
        # machine that has just proved it is the slow kind.
        with _heartbeat(send, "compiling", 0.7,
                        note=_wire.NOTE_COMPILE_RETRY):
            _run_generation(_WARMUP_TEXT, None, None, _WARMUP_TOKENS,
                            temperature=0.7, top_p=0.9, top_k=30,
                            repetition_penalty=1.1)
    _sweep()
    return time.perf_counter() - t0


def _op_load(req: dict, send) -> dict:
    values = req.get("values") or {}
    knobs = _knobs(values)
    model_path = str(req.get("model_path") or "").strip()
    if not model_path:
        raise _wire.WorkerError(_wire.CODE_WORKER_FAILED,
                                "the load request carried no model_path")
    ckpt = Path(model_path)

    # Idempotent: the same checkpoint is already resident and usable.
    if STATE["model"] is not None and STATE["model_path"] == str(ckpt):
        return _load_result(reused=True, compile_seconds=0.0)

    missing = [name for name in ("config.json", "codec.pth")
               if not (ckpt / name).is_file()]
    if not ckpt.is_dir():
        raise _wire.WorkerError(_wire.CODE_WORKER_FAILED,
                                f"no such model directory: {ckpt}")
    if missing:
        raise _wire.WorkerError(
            _wire.CODE_WORKER_FAILED,
            f"the model folder is incomplete, missing: {', '.join(missing)}")

    cache_dir = _inductor_cache_dir(req)
    if cache_dir:
        try:
            Path(cache_dir).mkdir(parents=True, exist_ok=True)
            os.environ["TORCHINDUCTOR_CACHE_DIR"] = cache_dir
        except OSError as exc:
            _progress(send, "cache_dir_unusable", detail=str(exc)[:200],
                      note=_wire.NOTE_TEMP_COMPILE_CACHE)

    _unload_everything()
    eng = _engine()
    torch = eng["torch"]
    if not torch.cuda.is_available():
        raise _wire.WorkerError(
            _wire.CODE_WORKER_FAILED,
            "the engine runtime has no usable CUDA device; Fish S2 Pro needs one")

    try:
        _build_model(ckpt, knobs["kv_cache_len"], send)
        compile_seconds = _warmup(send)
    except (_wire.WorkerError, SystemExit):
        raise
    except BaseException as exc:  # noqa: BLE001
        if isinstance(exc, _OOM_TYPE):
            _unload_everything()
            raise
        if _wire.is_oom(exc):
            _unload_everything()
            raise _wire.oom(str(exc)) from exc
        # The runtime imported fine, so this is the model refusing to load: the
        # coarse exit code says exactly that, and the stderr tail carries why.
        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()
        sys.exit(_wire.EXIT_MODEL_LOAD)

    STATE["model_path"] = str(ckpt)
    # AFTER model_path is published: _codec() resolves codec.pth relative to
    # it, so calling this any earlier looked for a bare "codec.pth" in the
    # working directory and skipped the prewarm with tts_worker_failed.
    _prewarm_codec(send)
    _progress(send, "ready", 1.0)
    return _load_result(reused=False, compile_seconds=compile_seconds)


def _load_result(reused: bool, compile_seconds: float) -> dict:
    torch = _ENGINE.get("torch")
    vram_mb = None
    if torch is not None and torch.cuda.is_available():
        vram_mb = int(torch.cuda.memory_reserved() / (1024 * 1024))
    return {
        "loaded": True,
        "reused": reused,
        "engine_id": "fish_s2",
        "model_path": STATE["model_path"],
        "sample_rate": _SAMPLE_RATE,
        "kv_cache_len": STATE["kv_len"],
        "compiled": STATE["compiled"],
        "quantized": STATE["quantized"],
        "compile_seconds": round(compile_seconds, 1),
        "vram_mb": vram_mb,
    }


def _unload_everything() -> None:
    """Give back everything, including what is parked in system RAM.

    The parked copies were a leak: unloading cleared the three VRAM slots and
    left `codec_parked` holding ~4.9 GB of host memory with nothing able to
    reach it, for the whole life of the worker. Nobody noticed because the
    number that gets watched is VRAM. Now that the model can park too the same
    oversight would hold ~7 GB, and the lock-time eject exists precisely to
    hand memory back - so it hands back all of it.
    """
    STATE["model"] = None
    STATE["decode"] = None
    STATE["codec"] = None
    STATE["model_parked"] = None
    STATE["codec_parked"] = None
    STATE["kv_len"] = 0
    _sweep()


def _park_model(send) -> bool:
    """Move the t2s model to system RAM instead of destroying it.

    The cheap rung. Destroying it costs a ~28 s rebuild - weights off disk,
    fp8 quantisation, cache setup, and a compile - to reclaim memory a PCIe
    copy gives back in a second or two. That trade was only ever worth taking
    because this rung did not exist.

    Never a duty. fp8 tensor subclasses and a compiled graph are the two things
    most likely to object to a device move, and if either does we fall straight
    through to the old behaviour: the caller nulls the model out and rebuilds
    from disk exactly as before. Slower, never broken.
    """
    model = STATE["model"]
    if model is None:
        return False
    try:
        model.to("cpu")
        STATE["model_parked"] = (model, STATE["decode"])
        _progress(send, "parked", detail="text2semantic held in system memory")
        return True
    except Exception as exc:  # noqa: BLE001 - reported, then the old path runs
        STATE["model_parked"] = None
        _progress(send, "park_failed", error=f"{type(exc).__name__}: {exc}"[:200],
                  note=_wire.NOTE_REBUILD_FROM_DISK)
        return False


def _ensure_model(send) -> None:
    """Bring the t2s model back after a decode-pressure eviction."""
    if STATE["model"] is not None:
        return
    if not STATE["model_path"]:
        raise _wire.WorkerError(_wire.CODE_SYNTHESIS_FAILED,
                                "no voice model is loaded")
    _drop_codec()
    parked = STATE.get("model_parked")
    if parked is not None:
        model, decode = parked
        _progress(send, "reloading", 0.02,
                  note=_wire.NOTE_RESTORING_FROM_RAM)
        try:
            STATE["model"] = model.to(_DEVICE)
            STATE["decode"] = decode
            STATE["model_parked"] = None
            STATE["evicted"] = False
            return
        except Exception as exc:  # noqa: BLE001 - fall through to the disk path
            STATE["model_parked"] = None
            _progress(send, "restore_failed",
                      error=f"{type(exc).__name__}: {exc}"[:200],
                      note=_wire.NOTE_REBUILDING_FROM_DISK)
    _progress(send, "reloading", 0.02,
              note=_wire.NOTE_FREED_FOR_DECODE)
    _build_model(Path(STATE["model_path"]), STATE["kv_len"] or _DEFAULTS["kv_cache_len"], send)
    # No warm-up here on purpose: the generation that follows compiles the same
    # graph, and the inductor cache on disk already makes that the cheap path.


# ── the codec (DAC): ~4.9 GB, so it is a guest, not a resident ───────────────
def _codec(send):
    if STATE["codec"] is not None:
        return STATE["codec"]
    parked = STATE.get("codec_parked")
    if parked is not None:
        _progress(send, "codec", 0.75, detail="restoring the codec from memory")
        try:
            # Measured so _codec_need stops guessing (KÖK 9). A park-restore is
            # a PCIe copy of exactly the weights a disk load produces, so both
            # rungs teach the same cost line.
            with _measure("codec", 1, send):
                STATE["codec"] = parked.to(_DEVICE)
            STATE["codec_parked"] = None
            return STATE["codec"]
        except Exception:  # noqa: BLE001 - fall through to the disk load
            STATE["codec_parked"] = None
    eng = _engine()
    path = Path(STATE["model_path"] or "") / "codec.pth"
    if not path.is_file():
        raise _wire.WorkerError(_wire.CODE_WORKER_FAILED,
                                f"the codec is missing: {path}")
    _progress(send, "codec", 0.75, detail="loading the DAC codec")
    try:
        with _measure("codec", 1, send):
            STATE["codec"] = eng["load_dac"](
                "modded_dac_vq", str(path), device=_DEVICE)
    except Exception as exc:  # noqa: BLE001
        raise _oom_guard(exc, _wire.CODE_WORKER_FAILED, "loading the codec") from exc
    return STATE["codec"]


def _prewarm_codec(send) -> None:
    """Read the DAC in from disk NOW, then park it in system RAM.

    The codec cannot STAY resident - see _drop_codec: measured, this card
    reports 1.76-2.02 GB free with it on the card - so this changes the VRAM
    policy not at all. It only moves a ~1.7 GB disk read off the user's first
    press of Speak, which is where it was the whole difference between "a
    couple of seconds" and half a minute. From the second sentence onwards the
    parked copy was already doing this; the first one had nothing to restore.

    Never fatal. A codec that will not preload is loaded lazily exactly as
    before - this is a head start, not a requirement.
    """
    try:
        _codec(send)
        # Stays RESIDENT when there is room, decided by the same measured
        # policy that runs after every decode (_should_keep_codec). Parking it
        # would cost a PCIe copy on the first sentence for no reason on a card
        # with headroom; on a tight card the policy parks it exactly as before.
        if not _should_keep_codec(_free_gb()):
            _drop_codec()
    except BaseException as exc:                # noqa: BLE001
        _progress(send, "codec_prewarm_skipped", 0.95,
                  detail=f"{type(exc).__name__}: {exc}"[:200],
                  note=_wire.NOTE_LAZY_FIRST_SENTENCE)


def _drop_codec() -> bool:
    """Give the codec's ~4.9 GB of VRAM back - by PARKING it in system RAM, not
    by throwing it away.

    MEASURED: after a decode this card reports 1.76-2.02 GB free, so the codec
    genuinely cannot stay resident; that part of the old policy was right. What
    was wrong was HOW it left. Setting it to None meant reloading it from
    disk for the next sentence - about five seconds, every single time, which is
    most of the gap between the engine's own reported 3.2 s and the 8.3 s the
    app measured. A CPU park frees exactly the same VRAM and costs one PCIe copy
    (~0.3 s) to bring back.
    """
    codec = STATE["codec"]
    if codec is None:
        return False
    try:
        codec.to("cpu")
        STATE["codec_parked"] = codec
    except Exception:  # noqa: BLE001 - parking is an optimisation, never a duty
        STATE["codec_parked"] = None
    STATE["codec"] = None
    _sweep()
    return True


def _should_keep_codec(free_gb: float) -> bool:  # noqa: D401
    """After a decode, is there room to keep the ~4.9 GB codec resident?

    The ONE place that decides it - the decode asks here, and so does every
    path that borrowed the codec to encode a reference clip.

    MEASURED BUG this replaces: the old test was `free < 4.0`, where `free`
    is read with the codec ALREADY resident. On a 16 GB card that
    reads ~3.1 GB - below 4.0 - so the codec was evicted after EVERY sentence
    and reloaded from disk for the next one. The engine reported 3.2 s of work
    while the app measured 8.3 s; the missing ~5 s was that reload, every time.
    The policy was judging the codec unaffordable by counting its own footprint
    against it.

    What actually protects the generation peak is the guard BEFORE generation
    (`_op_synthesize` measures the upcoming work against the reserve), so
    keeping it here is safe by construction: real pressure is still handled,
    one step later, by code that already exists. This only refuses to keep it
    when the card is genuinely tight right now.
    """
    return free_gb >= _VRAM_RESERVE_GB


def _free_for_codec(send, why: str, *, force: bool = False, frames: int = 0) -> bool:
    """The measured free between text2semantic and the codec.

    Always: drop the generation working set. On a card that is genuinely tight,
    also perform the bake-off's eviction verbatim - without it the codec OOMs.
    Both users of the codec go through here: the decode, and encoding a reference
    clip while the model happens to be resident.
    """
    torch = _ENGINE.get("torch")
    if torch is None:
        return False            # nothing is on a card yet: nothing to evict
    _sweep()
    # Measured against THIS decode's size, not against a fixed floor. The old
    # test was `free >= 3.0`, and it could not see the one case that mattered:
    # the decode's cost scales with the frames it is handed, so a card sitting
    # comfortably above a fixed floor still OOMs on a maximal run. Observed
    # live at 03:52 - "length_capped", then "tts_out_of_memory" three seconds
    # later, and the worker died with it.
    #
    # `force` still exists for the case where the caller KNOWS the codes are
    # maximal. It is now a belt on top of braces rather than the only guard.
    if not force and _fits(frames):
        return False
    _progress(send, "freeing", 0.7, free_gb=round(_free_gb(), 1), detail=why,
              note=_wire.NOTE_FREEING_FOR_CODEC)
    # Cheapest rung first. Parking frees exactly the same VRAM as destroying;
    # the only difference is what the NEXT sentence pays to get it back - a
    # PCIe copy rather than a rebuild from disk. If the park will not take, the
    # lines below are the old behaviour, unchanged.
    _park_model(send)
    STATE["model"] = None
    STATE["decode"] = None
    gc.collect()
    torch.cuda.empty_cache()
    torch.cuda.synchronize()
    STATE["evicted"] = True
    return True


# ── references ───────────────────────────────────────────────────────────────
def _encode_ref(path: Path, codec, device: str):
    """Encode a reference wav to VQ codes WITHOUT torchaudio.load - on torch 2.11
    that path requires the torchcodec package. soundfile reads the wav directly and
    resample is a pure tensor op, so no extra dependency is needed."""
    eng = _engine()
    torch, sf, torchaudio = eng["torch"], eng["sf"], eng["torchaudio"]
    try:
        data, sr = sf.read(str(path), dtype="float32")
    except Exception as exc:  # noqa: BLE001 - an unreadable clip is user-fixable
        raise _wire.WorkerError(
            _wire.CODE_REFERENCE_INVALID,
            f"could not read the reference clip ({type(exc).__name__}: {exc})") from exc
    if data.ndim > 1:
        data = data.mean(axis=1)
    seconds = len(data) / float(sr or 1)
    if seconds < _REF_MIN_SECONDS:
        raise _wire.WorkerError(
            _wire.CODE_REFERENCE_INVALID,
            f"the reference clip is only {seconds:.2f}s long; use a few seconds of speech")
    if seconds > _REF_MAX_SECONDS:
        raise _wire.WorkerError(
            _wire.CODE_REFERENCE_INVALID,
            f"the reference clip is {seconds:.0f}s long; trim it to "
            f"under {int(_REF_MAX_SECONDS)}s (a single clean clip clones best)")
    audio = torch.from_numpy(data)[None, :]
    if sr != codec.sample_rate:
        audio = torchaudio.functional.resample(audio, sr, codec.sample_rate)
    audios = audio[None].to(device)
    lengths = torch.tensor([audios.shape[2]], device=device, dtype=torch.long)
    try:
        with torch.no_grad():
            indices, _ = codec.encode(audios, lengths)
    except Exception as exc:  # noqa: BLE001
        raise _oom_guard(exc, _wire.CODE_REFERENCE_INVALID,
                         "encoding the reference clip") from exc
    if indices.ndim == 3:
        indices = indices[0]
    return indices.cpu(), seconds, int(sr)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""


def _tokens_path(clip: Path) -> Path:
    return clip.with_name(clip.stem + _TOKENS_SUFFIX)


def _resolve_reference(req: dict, values: dict) -> tuple[Path | None, str, Path | None]:
    """Work out (clip, transcript, cached tokens) from whatever the host sent."""
    ref = req.get("reference")
    if isinstance(ref, str):
        ref = {"path": ref}
    if not isinstance(ref, dict):
        ref = {}

    spec = str(ref.get("path") or ref.get("audio") or ref.get("clip")
               or ref.get("wav") or req.get("reference_voice")
               or values.get("reference_voice") or "").strip()
    transcript = str(ref.get("transcript") or ref.get("text")
                     or req.get("reference_transcript")
                     or values.get("reference_transcript") or "").strip()
    tokens_spec = str(ref.get("tokens_path") or ref.get("tokens")
                      or req.get("prompt_tokens_path") or "").strip()

    tokens = Path(tokens_spec) if tokens_spec else None
    if not spec:
        if tokens is None and transcript:
            raise _wire.WorkerError(
                _wire.CODE_REFERENCE_INVALID,
                "a reference transcript was given but no reference clip")
        return None, transcript, tokens

    base = Path(spec)
    if not base.exists():
        raise _wire.WorkerError(
            _wire.CODE_REFERENCE_INVALID,
            f"the reference voice does not exist on disk: {spec}")

    if base.is_dir():
        # The roadmap layout: ref.wav + transcript.txt + prompt_tokens.npy
        clip = base / "ref.wav"
        if not clip.is_file():
            wavs = sorted(p for p in base.glob("*.wav") if p.is_file())
            if not wavs:
                raise _wire.WorkerError(
                    _wire.CODE_REFERENCE_INVALID,
                    f"the reference voice folder holds no .wav: {base}")
            clip = wavs[0]
        if not transcript:
            transcript = _read_text(base / "transcript.txt") or _read_text(
                clip.with_suffix(".txt"))
        if tokens is None:
            for candidate in (_tokens_path(clip), base / "prompt_tokens.npy"):
                if candidate.is_file():
                    tokens = candidate
                    break
        return clip, transcript, tokens

    clip = base
    if not transcript:
        transcript = _read_text(clip.with_suffix(".txt")) or _read_text(
            clip.parent / "transcript.txt")
    if tokens is None:
        for candidate in (_tokens_path(clip), clip.parent / "prompt_tokens.npy"):
            if candidate.is_file():
                tokens = candidate
                break
    return clip, transcript, tokens


def _load_tokens(path: Path, clip: Path | None):
    """A cached .npy, unless it is older than the clip it claims to describe."""
    eng = _engine()
    if not path.is_file():
        return None
    try:
        if clip is not None and clip.is_file():
            if path.stat().st_mtime < clip.stat().st_mtime:
                return None                      # stale: the clip was replaced
        array = eng["np"].load(str(path))
    except Exception:  # noqa: BLE001 - a bad cache is a cache miss, not a failure
        return None
    try:
        tensor = eng["torch"].from_numpy(array).long()
    except Exception:  # noqa: BLE001
        return None
    if tensor.ndim == 3:
        tensor = tensor[0]
    if tensor.ndim != 2 or tensor.numel() == 0:
        return None
    return tensor


def _prompt(req: dict, values: dict, send):
    """(prompt_text, prompt_tokens) for `generate_long`, or (None, None).

    `use_prompt = bool(prompt_text) and bool(prompt_tokens)` inside generate_long:
    a reference with no transcript would silently generate a NON-cloned voice, so
    that combination is an error the user can act on instead of a surprise.
    """
    clip, transcript, tokens_path = _resolve_reference(req, values)
    if clip is None and tokens_path is None:
        return None, None

    if not transcript:
        raise _wire.WorkerError(
            _wire.CODE_TRANSCRIPT_REQUIRED,
            "Fish S2 Pro clones from a clip AND its transcript; the audio alone "
            "is not enough. Add the words spoken in the reference clip.")

    tokens = _load_tokens(tokens_path, clip) if tokens_path else None
    if tokens is None:
        if clip is None:
            raise _wire.WorkerError(
                _wire.CODE_REFERENCE_INVALID,
                "the cached prompt tokens are unreadable and there is no clip to "
                "rebuild them from")
        # Encoding needs the codec, which is ~4.9 GB: make room the same way the
        # decode path does, then hand the memory straight back.
        _progress(send, "encoding_reference", 0.05, detail=str(clip.name))
        had_codec = STATE["codec"] is not None
        _free_for_codec(send, "encoding a reference clip")
        tokens, _seconds, _sr = _encode_ref(clip, _codec(send), _DEVICE)
        if not had_codec and not _should_keep_codec(_free_gb()):
            _drop_codec()
        target = tokens_path or _tokens_path(clip)
        _save_tokens(tokens, target, send)
    return [transcript], [tokens]


def _save_tokens(tokens, target: Path, send) -> Path | None:
    eng = _engine()
    # numpy.save APPENDS ".npy" when the name does not already end in it. A caller
    # that passes `out` without the suffix would otherwise be told about a path that
    # does not exist, and `_load_tokens` would miss that cache forever.
    if target.suffix.lower() != ".npy":
        target = target.with_name(target.name + ".npy")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        eng["np"].save(str(target), tokens.numpy())
        return target
    except Exception as exc:  # noqa: BLE001 - a cache we cannot write is not fatal
        _progress(send, "cache_write_failed", detail=str(exc)[:200],
                  note=_wire.NOTE_REFERENCE_REENCODE)
        return None


# ── generating ───────────────────────────────────────────────────────────────
_SPEAKER_RE = re.compile(r"<\|speaker:\d+\|>")


def _spoken(text: str) -> str:
    """Tag the speaker, the way gen_fish.py did, so the model gets the prompt
    shape it was trained on.

    It does NOT make the text chunk. Checked against the real functions:
    `split_text_by_speaker` splits on the tag and `group_turns_into_batches`
    only starts a new batch BETWEEN turns, so one tag is one turn is one batch -
    a 1392-byte reply goes to the model whole with chunk_length=300. Sizing the
    KV cache as if the prompt were 300 long is therefore wrong; `_text_tokens`
    measures it instead.
    """
    return text if _SPEAKER_RE.search(text) else f"<|speaker:0|>{text}"


def _text_tokens(text: str) -> int:
    """How many sequence positions this text will really occupy.

    The KV cache is capped and `_cache_setup_done` keeps `generate()` from quietly
    resizing it, so an under-count is not a slow path - `KVCache.update` does
    `k_out[:, :, input_pos] = k_val` and writes past the end of the buffer.
    """
    if not text:
        return 0
    model = STATE.get("model")
    tokenizer = getattr(model, "tokenizer", None) if model is not None else None
    if tokenizer is not None:
        try:
            return len(tokenizer.encode(text))
        except Exception:  # noqa: BLE001 - fall through to the safe bound below
            pass
    # A BPE token always covers at least one UTF-8 byte, so this never under-counts.
    return len(text.encode("utf-8"))


def _ensure_capacity(need: int, send) -> None:
    """Grow the KV cache if this request genuinely needs more than the cap.

    `setup_caches` is grow-only, and growing invalidates the compiled guards, so
    this costs a recompile. It happens rather than corrupting a run, and it says
    so out loud.
    """
    model = STATE["model"]
    torch = _ENGINE["torch"]
    current = int(getattr(model, "max_seq_len", 0))
    if need <= current:
        return
    hard = int(model.config.max_seq_len)
    grow = min(int(need), hard)
    _progress(send, "kv_grow", from_len=current, to_len=grow,
              note=_wire.NOTE_RECOMPILE_LONGER_CONTEXT)
    try:
        with torch.device(_DEVICE):
            model.setup_caches(max_batch_size=1, max_seq_len=grow,
                               dtype=torch.bfloat16)
        model._cache_setup_done = True
        torch.cuda.synchronize()
    except Exception as exc:  # noqa: BLE001
        raise _oom_guard(exc, _wire.CODE_SYNTHESIS_FAILED, "growing the KV cache") from exc
    STATE["kv_len"] = int(getattr(model, "max_seq_len", grow))


def _fit_tokens(ref_frames: int, prompt_cost: int, want: int, budget: int, send) -> int:
    """How many tokens this sentence may actually generate.

    `prompt_cost` is the measured token length of the text plus the reference
    transcript - not a constant, because the text is never chunked (see `_spoken`).

    The budget is the user's "Context window", because a knob that memory is
    sized from has to be the thing generation respects too. A reference clip that
    does not fit inside it raises the ceiling to the model's own rather than
    refusing - the clip is the user's voice, the window was only a default.
    """
    hard = int(STATE["model"].config.max_seq_len)
    fixed = ref_frames + prompt_cost + _PROMPT_OVERHEAD
    room = budget - fixed
    if room < 64:
        room = hard - fixed
        if room < 64:
            if ref_frames >= prompt_cost:
                raise _wire.WorkerError(
                    _wire.CODE_REFERENCE_INVALID,
                    f"the reference clip is too long for this model's context "
                    f"({ref_frames} frames of {hard}); use a shorter clip")
            raise _wire.WorkerError(
                _wire.CODE_SYNTHESIS_FAILED,
                f"this text is too long for the model's context "
                f"({prompt_cost} tokens of {hard}); say it in smaller pieces")
        _progress(send, "context_raised", limit=hard, ref_frames=ref_frames,
                  prompt_tokens=prompt_cost,
                  note=_wire.NOTE_DOES_NOT_FIT_CONTEXT)
    if want > room:
        _progress(send, "clamped", max_new_tokens=room, ref_frames=ref_frames,
                  prompt_tokens=prompt_cost,
                  note=_wire.NOTE_LESS_CONTEXT_THAN_LIMIT)
        return room
    return want


def _run_generation(spoken: str, prompt_text, prompt_tokens, max_new_tokens: int,
                    *, temperature: float, top_p: float, top_k: int,
                    repetition_penalty: float):
    """NOTE on `repetition_penalty`: `generate_long` declares it and then never
    uses it - it is not forwarded to `generate()` and the only other mention in
    the whole module is the unused `model.fixed_repetition_penalty`. It is passed
    anyway because the keyword is real (no TypeError) and a future fish_speech
    may honour it; the repetition control that IS live is the RAS resampling
    inside `decode_one_token_ar`. The settings dial is honest about temperature
    and top_p/top_k; it is inert for this one on this version of the engine.
    """
    eng = _engine()
    torch = eng["torch"]
    gen = eng["generate_long"](
        model=STATE["model"],
        device=_DEVICE,
        decode_one_token=STATE["decode"],
        text=spoken,
        num_samples=1,
        max_new_tokens=max_new_tokens,
        top_p=top_p,
        top_k=top_k,
        temperature=temperature,
        repetition_penalty=repetition_penalty,
        compile=STATE["compiled"],
        iterative_prompt=True,
        chunk_length=_CHUNK_LENGTH,
        prompt_text=prompt_text,
        prompt_tokens=prompt_tokens,
    )
    chunks = [r.codes for r in gen if getattr(r, "action", None) == "sample"]
    if not chunks:
        return None
    return torch.cat(chunks, dim=1).cpu()


def _decode_once(codes, codec, torch):
    idx = codes.to(_DEVICE).long()
    if idx.ndim == 2:
        idx = idx.unsqueeze(0)
    try:
        with torch.no_grad():
            fake = codec.from_indices(idx)
        audio = fake[0, 0].float().cpu().numpy()
    finally:
        # On the OOM path `fake` never existed; `idx` did, and it is the tensor
        # standing between the retry and the room it needs.
        idx = None
        fake = None
        torch.cuda.empty_cache()
    return audio


def _decode_to_audio(codes, send):
    eng = _engine()
    torch = eng["torch"]
    codec = _codec(send)
    # The unit a decode's memory scales with. Measuring against the frame count
    # is the whole point: the run that OOMed was the one holding the MOST
    # frames, and a fixed floor could not see that coming.
    frames = int(codes.shape[1]) if hasattr(codes, "shape") else 0
    try:
        with _measure("decode", frames, send):
            audio = _decode_once(codes, codec, torch)
    except Exception as exc:  # noqa: BLE001
        # An OOM here used to be terminal: the sentence was lost AND the worker
        # died with it (observed live - "tts_out_of_memory" at 03:52:37, "worker
        # died on its own" at 03:53:08, and the app had no voice until it was
        # restarted). It does not have to be. The decode's cost scales with the
        # code count, and the one thing that reliably buys room is the ~7 GB
        # text2semantic model. If it is still resident, this is recoverable:
        # evict it and decode again. The next sentence pays a rebuild, which is
        # a far better price than a dead worker.
        recoverable = isinstance(exc, _OOM_TYPE) or _wire.is_oom(exc)
        if not recoverable or STATE["model"] is None:
            raise _oom_guard(exc, _wire.CODE_SYNTHESIS_FAILED,
                             "decoding the audio") from exc
        _free_for_codec(send, "retrying the decode after an out-of-memory",
                        force=True)
        try:
            with _measure("decode", frames, send):
                audio = _decode_once(codes, codec, torch)
        except Exception as retry_exc:  # noqa: BLE001 - nothing left to free
            raise _oom_guard(retry_exc, _wire.CODE_SYNTHESIS_FAILED,
                             "decoding the audio") from retry_exc
    sr = int(getattr(codec, "sample_rate", _SAMPLE_RATE) or _SAMPLE_RATE)
    # Reported, not silent: a reload costs ~5 s per sentence, and the only way
    # that regression was ever caught was by seeing this decision.
    free_after = _free_gb()
    keep = _should_keep_codec(free_after)
    _progress(send, "codec_policy", 0.85, free_gb=round(free_after, 2),
              keep=keep, where="post-decode", **_vram_snapshot())
    if not keep:
        _drop_codec()
    return audio, sr


def _op_synthesize(req: dict, send) -> dict:
    text = str(req.get("text") or "").strip()
    if not text:
        raise _wire.WorkerError(_wire.CODE_SYNTHESIS_FAILED,
                                "there is nothing to say")
    out_spec = str(req.get("out") or "").strip()
    if not out_spec:
        raise _wire.WorkerError(_wire.CODE_SYNTHESIS_FAILED,
                                "the request carried no output path")
    values = req.get("values") or {}
    knobs = _knobs(values)

    _ensure_model(send)
    eng = _engine()
    torch = eng["torch"]

    prompt_text, prompt_tokens = _prompt(req, values, send)
    _ensure_model(send)          # encoding a reference may have evicted the model

    # Measure the prompt instead of assuming it: the text is fed to the model in
    # one piece (see `_spoken`), and the cache is capped, so an assumption here is
    # an out-of-bounds KV write later.
    spoken = _spoken(text)
    prompt_cost = _text_tokens(spoken)
    if prompt_text:
        prompt_cost += sum(_text_tokens(t) for t in prompt_text)
    ref_frames = int(prompt_tokens[0].shape[1]) if prompt_tokens else 0
    budget = max(knobs["kv_cache_len"], int(STATE["kv_len"] or 0))
    max_new = _fit_tokens(ref_frames, prompt_cost, knobs["max_new_tokens"], budget, send)
    _ensure_capacity(ref_frames + prompt_cost + _PROMPT_OVERHEAD + max_new, send)

    # The codec is a ~4.9 GB guest. It stays unless the work about to run would
    # eat into the reserve with it resident.
    #
    # This guard used to sit ABOVE the block that computes `max_new`, which
    # meant it decided whether there was room without knowing how much work was
    # coming - the one fact that determines the answer. It reads the budget now.
    # `max_new` is the WORST case (the run may stop early), and that is the
    # right input for a forecast: a guard that is optimistic about size is a
    # guard that fires after the OOM.
    if STATE["codec"] is not None:
        # The user's ceiling still bounds the GENERATION - `max_new` below is
        # untouched, and a dial that silently stopped applying would be its own
        # bug. It is only the FORECAST that gets the honest number: the guard
        # should size the work it is about to do, not the largest work it is
        # permitted to do. Under-forecasting is survivable and over-forecasting
        # is not free - a decode that runs out of room retries through
        # `_free_for_codec(force=True)`, which is precisely the eviction this
        # avoids, so the worst case of being wrong here IS the old behaviour.
        expected = _expected_frames(len(spoken))
        forecast = max_new if expected is None else min(max_new, expected)
        keep = _fits(forecast, "generate", "decode")
        _progress(send, "codec_policy", 0.15, free_gb=round(_free_gb(), 2),
                  keep=keep, where="pre-generation", budget_frames=max_new,
                  forecast_frames=forecast,
                  forecast_gb=round(_predict_cost("decode", forecast) or 0.0, 2),
                  **_vram_snapshot())
        if not keep:
            _drop_codec()

    seed = req.get("seed")
    if seed is not None:
        try:
            torch.manual_seed(int(seed))
            torch.cuda.manual_seed(int(seed))
        except (TypeError, ValueError):
            pass

    _progress(send, "generating", 0.2, cloned=bool(prompt_tokens))
    t0 = time.perf_counter()
    try:
        # Opened with the BUDGET and corrected to what was actually produced:
        # a run that stops early costs what it produced, not what it was
        # allowed. Recording the budget would teach the estimator a cost that
        # never happened.
        with _measure("generate", max_new, send) as measured:
            codes = _run_generation(
                spoken, prompt_text, prompt_tokens, max_new,
                temperature=knobs["temperature"], top_p=knobs["top_p"],
                top_k=knobs["top_k"], repetition_penalty=knobs["repetition_penalty"],
            )
            if codes is not None and hasattr(codes, "shape"):
                measured.units = int(codes.shape[1])
    except (_wire.WorkerError, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        raise _oom_guard(exc, _wire.CODE_SYNTHESIS_FAILED, "generating speech") from exc
    generate_seconds = time.perf_counter() - t0
    if codes is None:
        raise _wire.WorkerError(_wire.CODE_SYNTHESIS_FAILED,
                                "the model produced no audio tokens for this text")

    # Did it stop because it finished, or because it ran out of budget?
    #
    # Reaching max_new_tokens means the speech is CUT - the model was still
    # talking. At ~21.5 semantic frames per second an 800-token budget is about
    # 37 seconds, and a message longer than that used to end mid-paragraph with
    # nothing anywhere saying so: no error, no event, no log line. Measured on
    # the real app at 37.1 s of audio against a 37.2 s budget.
    #
    # The host splits a message into sentences now, so this should no longer be
    # reachable in normal use. Saying so anyway is the point: the failure this
    # guards against was invisible precisely because nobody was looking.
    produced = int(codes.shape[1]) if hasattr(codes, "shape") else 0
    # Free measurement, taken from work that had to happen anyway - the same
    # bargain `_measure` strikes for VRAM. A run that hit the ceiling is NOT
    # folded in: it was cut short, so it says how long the budget was rather
    # than how long the text wanted to be, and teaching the estimator that
    # would drag every later forecast down towards the cap.
    if produced and not (max_new and produced >= int(max_new) - 1):
        _observe_cost("frames", len(spoken), float(produced))
    capped = bool(produced and max_new and produced >= int(max_new) - 1)
    if capped:
        _progress(send, "length_capped", 0.78,
                  produced_tokens=produced, limit=int(max_new),
                  note=_wire.NOTE_LENGTH_CAPPED)

    evicted = _free_for_codec(send, "decoding to audio", force=capped,
                              frames=produced)
    _progress(send, "decoding", 0.8)
    audio, sr = _decode_to_audio(codes, send)
    del codes

    # Reading speed. Fish has no rate parameter of its own (its hosted API does;
    # the open-source server never exposed it), so the pace is applied to the
    # rendered waveform with WSOLA - pitch untouched, because the sample rate
    # never changes. `_dsp` decides that a rate near 1.0 is not worth a pass.
    rate = req.get("rate")
    if not _dsp.is_noop(rate):
        try:
            audio = _dsp.time_stretch(audio, rate)
            _progress(send, "retimed", 0.9, rate=round(_dsp.clamp_rate(rate), 3))
        except Exception as exc:  # noqa: BLE001
            # Speaking at the wrong pace beats not speaking: a failed stretch
            # must not lose a sentence that was generated perfectly well. But
            # it is REPORTED, for the same reason the codec policy is - the
            # only way that regression was ever caught was by seeing the
            # decision, and a silently ignored dial is indistinguishable from
            # a broken one.
            _progress(send, "retime_failed", 0.9,
                      error=f"{type(exc).__name__}: {exc}")

    out = Path(out_spec)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        eng["sf"].write(str(out), audio, sr)
    except Exception as exc:  # noqa: BLE001
        raise _wire.WorkerError(
            _wire.CODE_SYNTHESIS_FAILED,
            f"could not write the audio ({type(exc).__name__}: {exc})") from exc

    seconds = len(audio) / float(sr or _SAMPLE_RATE)
    _progress(send, "done", 1.0, seconds=round(seconds, 2))
    return {
        "path": str(out),
        "sample_rate": sr,
        "seconds": round(seconds, 3),
        "cloned": bool(prompt_tokens),
        "language": knobs["language"],   # Fish takes no language argument: the
                                         # text carries it. Reported so the UI's
                                         # "Turkish is weak here" warning is honest.
        "compiled": STATE["compiled"],
        "quantized": STATE["quantized"],
        "generate_seconds": round(generate_seconds, 2),
        "evicted": evicted,
    }


# ── preparing a reference voice ──────────────────────────────────────────────
def _op_prepare_ref(req: dict, send) -> dict:
    """Encode a reference clip + its transcript into prompt tokens, once.

    Cached next to the clip so that speaking never pays for the codec twice, and
    so a voice keeps working when the app is offline (it always is).
    """
    values = req.get("values") or {}
    spec = str(req.get("audio") or req.get("path") or req.get("clip")
               or req.get("wav") or req.get("reference")
               or values.get("reference_voice") or "").strip()
    if not spec:
        raise _wire.WorkerError(_wire.CODE_REFERENCE_INVALID,
                                "no reference clip was given")
    clip = Path(spec)
    if clip.is_dir():
        found = clip / "ref.wav"
        if not found.is_file():
            wavs = sorted(p for p in clip.glob("*.wav") if p.is_file())
            if not wavs:
                raise _wire.WorkerError(
                    _wire.CODE_REFERENCE_INVALID,
                    f"the reference voice folder holds no .wav: {clip}")
            found = wavs[0]
        clip = found
    if not clip.is_file():
        raise _wire.WorkerError(_wire.CODE_REFERENCE_INVALID,
                                f"no such reference clip: {clip}")

    transcript = str(req.get("transcript") or req.get("text") or "").strip()
    if not transcript:
        transcript = (_read_text(clip.with_suffix(".txt"))
                      or _read_text(clip.parent / "transcript.txt"))
    if not transcript:
        raise _wire.WorkerError(
            _wire.CODE_TRANSCRIPT_REQUIRED,
            "Fish S2 Pro needs the words spoken in the reference clip; the audio "
            "alone does not enable cloning.")

    if not STATE["model_path"]:
        model_path = str(req.get("model_path") or "").strip()
        if not model_path:
            raise _wire.WorkerError(_wire.CODE_WORKER_FAILED,
                                    "no model is loaded, so the codec is unknown")
        STATE["model_path"] = model_path

    _engine()
    had_codec = STATE["codec"] is not None
    _progress(send, "encoding_reference", 0.3, detail=clip.name)
    _free_for_codec(send, "encoding a reference clip")
    tokens, seconds, source_sr = _encode_ref(clip, _codec(send), _DEVICE)
    if not had_codec and not _should_keep_codec(_free_gb()):
        _drop_codec()

    out_spec = str(req.get("out") or "").strip()
    target = Path(out_spec) if out_spec else _tokens_path(clip)
    written = _save_tokens(tokens, target, send)
    _progress(send, "done", 1.0)
    return {
        "clip": str(clip),
        "tokens_path": str(written) if written else "",
        "cached": bool(written),
        "frames": int(tokens.shape[1]),
        "codebooks": int(tokens.shape[0]),
        "seconds": round(seconds, 3),
        "source_sample_rate": source_sr,
        "sample_rate": _SAMPLE_RATE,
        "transcript_chars": len(transcript),
    }


# ── the loop ─────────────────────────────────────────────────────────────────
def _op_ping(req: dict) -> dict:
    # Deliberately touches no torch: a ping must answer while the card is busy.
    return {
        "pong": True,
        "engine_id": "fish_s2",
        "pid": os.getpid(),
        "loaded": STATE["model"] is not None,
        "model_path": STATE["model_path"],
        "compiled": STATE["compiled"],
        "quantized": STATE["quantized"],
        "kv_cache_len": STATE["kv_len"],
        "sample_rate": _SAMPLE_RATE,
    }


def handle(op: str, req: dict, send):
    try:
        if op == _wire.OP_PING:
            return _op_ping(req)
        if op == _wire.OP_LOAD:
            return _op_load(req, send)
        if op == _wire.OP_SYNTHESIZE:
            return _op_synthesize(req, send)
        if op == _wire.OP_PREPARE_REF:
            return _op_prepare_ref(req, send)
        if op == _wire.OP_TRANSCRIBE:
            raise _wire.WorkerError(
                _wire.CODE_WORKER_FAILED,
                "fish_s2 does not transcribe; the transcript comes from the app")
        raise _wire.WorkerError(_wire.CODE_WORKER_FAILED,
                                f"unsupported op: {op!r}")
    except (_wire.WorkerError, SystemExit):
        raise
    except BaseException as exc:  # noqa: BLE001
        # Last net: an OOM anywhere must exit 2 with advice attached, never 1.
        if isinstance(exc, _OOM_TYPE):
            raise
        if _wire.is_oom(exc):
            raise _wire.oom(str(exc)) from exc
        raise


if __name__ == "__main__":
    channel = _wire.claim_stdout()
    sys.exit(_wire.serve(handle, channel=channel))
