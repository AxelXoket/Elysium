"""Drive the fish_s2 worker's `_op_synthesize` without a GPU.

Three tests used to assert things about this function by READING ITS SOURCE
TEXT: that a capped run is not folded into the estimator, that the codec guard
runs after the budget is known, and that hitting the length limit is reported.
A source scan passes on a build where the line it greps for sits in dead code,
and it fails on a build where the same rule was written differently - it is a
test of the file, not of the program.

The reason those tests were written that way is real: `_op_synthesize` is the
top of the worker and the real one needs a CUDA card. But everything between
the model and the card is replaceable, and once it is, the function runs in
process and says what it did on the wire. That is what this module provides.

What is faked is deliberately only the boundary: loading a model, running the
generation, decoding to audio, freeing VRAM, and writing a file. The control
flow under test - when the guard runs, what budget it publishes, whether the
cap is reported, what the estimator is taught - is the real one.

One honest exception: `synthesize()` also stubs `_fits`, so the guard's
DECISION is a dial here rather than a calculation. Its arithmetic is tested
directly in test_tts_vram_cost.py against the real function; what runs
through this harness is the ordering and the reporting around it. A test
that wants to prove the decision itself must not use this path.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

FISH = Path(__file__).resolve().parents[1] / "tts" / "worker" / "fish_s2.py"


def load_worker():
    """A fresh copy of the worker module, with its learnt costs cleared.

    Fresh per call because `_COSTS` is module state that a previous test would
    otherwise teach, and an estimator carrying another test's measurements is
    the one thing these tests must never share.
    """
    spec = importlib.util.spec_from_file_location("fish_s2_synth", FISH)
    mod = importlib.util.module_from_spec(spec)
    # Registered only for the duration of the exec, because the module
    # imports itself by name in one place, and REMOVED afterwards: leaving
    # it behind means every later caller gets a stale object under a name
    # that looks live, which is a landmine rather than a leak.
    sys.modules["fish_s2_synth"] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.modules.pop("fish_s2_synth", None)
    mod._COSTS.clear()
    return mod


class _Cuda:
    """Answers in BYTES, like the real one, and takes GB for readability.

    The peak matters: `_measure` records `peak - allocated`, so a card that
    reports nothing teaches nothing, and a test about whether an operation IS
    measured needs a card that has something to say.
    """

    def __init__(self, allocated_gb=0.0, peak_gb=0.0):
        self._allocated = allocated_gb * 1e9
        self._peak = peak_gb * 1e9
        #: How many times the probe opened. One per `_measure` block entered,
        #: which is how a test can see that a retry was measured as well as the
        #: attempt before it - an attempt that RAISES records no cost, so the
        #: cost log alone cannot tell "wrapped and learnt nothing" from "not
        #: wrapped at all".
        self.probes = 0

    def memory_allocated(self):
        return int(self._allocated)

    def reset_peak_memory_stats(self):
        self.probes += 1

    def max_memory_allocated(self):
        return int(self._peak)

    def manual_seed(self, seed):
        pass

    def empty_cache(self):
        pass


class _Torch:
    """Only the four calls `_op_synthesize` makes. A fake that answers more
    than the real thing is asked would hide a wrong call."""

    def __init__(self, allocated_gb=0.0, peak_gb=0.0):
        self.cuda = _Cuda(allocated_gb, peak_gb)
        self.seeds = []

    def manual_seed(self, seed):
        self.seeds.append(seed)


class _Codes:
    """What the generation returns: the token count is all this path reads."""

    def __init__(self, produced: int):
        self.shape = (1, produced)


class _SoundFile:
    def __init__(self):
        self.written = []

    def write(self, path, audio, sr):
        self.written.append({"path": path, "samples": len(audio), "sr": sr})


class Run:
    """One synthesis and everything observable about it."""

    def __init__(self, mod, result, events, sf, generation_calls, costs):
        self.mod = mod
        self.result = result
        self.events = events
        self.sf = sf
        self.generation_calls = generation_calls
        #: Every `_observe_cost` the run made, as (kind, units, gb). This is the
        #: production call being watched, not a fake standing in for it.
        self.costs = costs

    def costs_for(self, kind: str) -> list[tuple[str, float, float]]:
        return [c for c in self.costs if c[0] == kind]

    def stages(self) -> list[str]:
        return [e.get("stage") for e in self.events]

    def stage(self, name: str) -> dict | None:
        """The first event with this stage, or None. Named rather than indexed
        so a test says which event it means."""
        for event in self.events:
            if event.get("stage") == name:
                return event
        return None

    def learnt_frames(self) -> bool:
        """Did this run teach the frames-per-character estimator?"""
        return self.mod._COSTS.get("frames") is not None

    def max_new_used(self) -> int | None:
        """The budget the generation was actually run with."""
        return self.generation_calls[-1] if self.generation_calls else None


def synthesize(*, produced: int = 100, max_new: int = 800,
               codec_resident: bool = True, fits: bool = True,
               free_gb: float = 6.0, text: str = "Hello there, this is a line.",
               rate=None, seed=None, out: str = "out.wav", mod=None,
               peak_gb: float = 0.0) -> Run:
    """Run one `_op_synthesize` against fakes and report what it did.

    `produced` vs `max_new` is the interesting dial: a run where they meet is
    a run the model was CUT off in, and the whole point of several of these
    tests is that the two cases are treated differently.
    """
    mod = mod or load_worker()
    mod.STATE["codec"] = object() if codec_resident else None
    mod.STATE["model"] = object()
    mod.STATE["kv_len"] = 0

    sf = _SoundFile()
    engine = {"torch": _Torch(peak_gb=peak_gb), "sf": sf}
    mod._ENGINE["torch"] = engine["torch"]
    generation_calls: list[int] = []
    costs = _watch_costs(mod)

    def _run_generation(spoken, prompt_text, prompt_tokens, budget, **knobs):
        generation_calls.append(int(budget))
        return _Codes(produced)

    mod._engine = lambda: engine
    mod._ensure_model = lambda send: None
    mod._prompt = lambda req, values, send: ("", None)
    mod._fit_tokens = lambda *a, **k: max_new
    mod._ensure_capacity = lambda *a, **k: None
    mod._fits = lambda *a, **k: fits
    mod._run_generation = _run_generation
    mod._decode_to_audio = lambda codes, send: ([0.0] * 44100, 44100)
    mod._free_for_codec = lambda send, why, force=False, frames=0: bool(force)
    mod._free_gb = lambda: free_gb

    events: list[dict] = []
    request = {"text": text, "out": out, "values": {}, "rate": rate}
    if seed is not None:
        request["seed"] = seed
    result = mod._op_synthesize(request, lambda payload: events.append(payload))
    return Run(mod, result, events, sf, generation_calls, costs)


class Decode:
    """One `_decode_to_audio` and everything observable about it."""

    def __init__(self, mod, audio, sr, events, attempts, freed, costs, torch):
        self.costs = costs
        self.mod = mod
        self.audio = audio
        self.sr = sr
        self.events = events
        #: How many times the engine was actually asked to decode.
        self.attempts = attempts
        #: One entry per `_free_for_codec` call, holding its `force` flag.
        self.freed = freed
        self.torch = torch


def decode_to_audio(*, frames: int = 400, fail_times: int = 0,
                    model_resident: bool = True, oom: bool = True,
                    free_gb: float = 6.0, mod=None,
                    peak_gb: float = 0.0) -> Decode:
    """Run the real decode path with a controllable engine underneath.

    `fail_times` decides how many decode attempts raise before one succeeds;
    `oom` decides whether the failure is the recoverable kind. `model_resident`
    is the other half of "recoverable": with nothing left to free there is
    nothing to retry with.
    """
    mod = mod or load_worker()
    mod.STATE["model"] = object() if model_resident else None
    mod.STATE["codec"] = object()

    torch = _Torch(peak_gb=peak_gb)
    engine = {"torch": torch}
    mod._ENGINE["torch"] = torch
    costs = _watch_costs(mod)
    codec = type("Codec", (), {"sample_rate": 44100})()
    attempts: list[int] = []
    freed: list[bool] = []

    def _decode_once(codes, codec_arg, torch_arg):
        attempts.append(1)
        if len(attempts) <= fail_times:
            raise mod._wire.oom("cuda out of memory") if oom else RuntimeError(
                "something else entirely")
        return [0.0] * 1000

    mod._engine = lambda: engine
    mod._codec = lambda send: codec
    mod._decode_once = _decode_once
    mod._free_for_codec = lambda send, why, force=False, frames=0: freed.append(
        bool(force))
    mod._free_gb = lambda: free_gb

    events: list[dict] = []
    audio, sr = mod._decode_to_audio(
        _Codes(frames), lambda payload: events.append(payload))
    return Decode(mod, audio, sr, events, len(attempts), freed, costs, torch)


def _watch_costs(mod) -> list:
    """Record every `_observe_cost` without replacing it.

    A spy, not a stub: the real estimator still learns, so a test can look at
    either the calls or their effect.
    """
    recorded: list = []
    real = mod._observe_cost

    def spy(kind, units, gb):
        recorded.append((kind, float(units), float(gb)))
        return real(kind, units, gb)

    mod._observe_cost = spy
    return recorded
