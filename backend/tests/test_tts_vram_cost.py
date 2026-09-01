"""What an operation costs is MEASURED, not picked.

Every VRAM threshold this worker ever had was a number somebody chose - 4.0,
then 3.0, then 1.0 - and each was wrong in its own way because none of them
was a measurement. These tests cover the machinery that replaces the guess:
an estimator that learns from what actually happened, and a probe that is
allowed to learn nothing rather than to learn something false.

The probe is exercised against a FAKE torch on purpose. The real one needs a
CUDA card, and the failures worth catching here are arithmetic and control
flow, not kernel behaviour.
"""
import contextlib
import importlib.util
import sys
from pathlib import Path

import pytest

import fish_synth_harness as synth


def _mod(*, codec_resident: bool = True):
    path = Path(__file__).resolve().parents[1] / "tts" / "worker" / "fish_s2.py"
    spec = importlib.util.spec_from_file_location("fish_s2_cost", path)
    mod = importlib.util.module_from_spec(spec)
    # setdefault kept the FIRST module ever built under this name while
    # handing every later caller a fresh one, so the registry entry went
    # stale after the first test. Registered for the exec and taken back
    # out again.
    sys.modules["fish_s2_cost"] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.modules.pop("fish_s2_cost", None)
    mod._COSTS.clear()
    # The codec's own term (KÖK 9) is on the NEED side of _fits whenever the
    # codec is off the card. These cases are about the decode/generate reserve
    # rule, so they say so explicitly rather than leaning on a default: they
    # were written when the term did not exist and silently assumed it was 0.
    mod.STATE["codec"] = object() if codec_resident else None
    return mod


class _FakeCuda:
    """Reports a fixed allocation and a fixed peak, or raises on demand.

    Takes GB for readability and answers in BYTES, because that is what the
    real `torch.cuda` returns - a fake that speaks different units than the
    thing it stands in for tests the wrong arithmetic.
    """

    def __init__(self, allocated=0.0, peak=0.0, explode=False):
        self._allocated = allocated * 1e9
        self._peak = peak * 1e9
        self._explode = explode
        self.resets = 0

    def memory_allocated(self):
        if self._explode:
            raise RuntimeError("no cuda here")
        return self._allocated

    def reset_peak_memory_stats(self):
        self.resets += 1

    def max_memory_allocated(self):
        if self._explode:
            raise RuntimeError("no cuda here")
        return self._peak


class _FakeTorch:
    def __init__(self, **kw):
        self.cuda = _FakeCuda(**kw)


def _with_torch(mod, **kw):
    torch = _FakeTorch(**kw)
    mod._ENGINE["torch"] = torch
    return torch


#: Measured on an RTX 5080, five samples per operation, by
#: `verify/verify_tts_latency.py`. These are the numbers the model has to be
#: able to represent - and the reason it fits an intercept as well as a slope.
MEASURED_DECODE = [(24, 0.126), (63, 0.327), (143, 0.738),
                   (264, 1.358), (428, 2.200)]      # ~0.00514 GB per frame
MEASURED_GENERATE = [(24, 0.168), (63, 0.169), (143, 0.171),
                     (264, 0.175), (428, 0.181)]    # ~flat: the KV cache

#: (characters, semantic frames produced) from ONE verify run - paired, which
#: matters: the warm-up utterance emits a decode row of its own, and reading the
#: table off by that row gives ratios of 0.6 against the real 1.4 and a fit with
#: a negative intercept. 442 frames over 20.53 s of audio is 21.5 Hz, which is
#: the codec's documented rate and the check that these two columns line up.
MEASURED_FRAMES = [(42, 62), (95, 142), (190, 257), (334, 442)]


class TestTheEstimatorLearnsTheRealShape:
    """The two operations sit at opposite ends of `fixed + slope * units`, and
    an estimator that cannot represent both is not a small inaccuracy - the
    per-unit-only version predicted 9.5 GB for a generation that wanted 0.18,
    and the codec was evicted before every single sentence because of it."""

    def test_a_proportional_cost_is_learnt_as_proportional(self):
        mod = _mod()
        for units, gb in MEASURED_DECODE:
            mod._observe_cost("decode", units, gb)
        fixed, slope = mod._COSTS["decode"].fit()
        assert slope == pytest.approx(0.00514, rel=0.05)
        assert fixed < 0.05

    def test_a_flat_cost_is_learnt_as_flat(self):
        mod = _mod()
        for units, gb in MEASURED_GENERATE:
            mod._observe_cost("generate", units, gb)
        fixed, slope = mod._COSTS["generate"].fit()
        assert fixed == pytest.approx(0.167, rel=0.05)
        assert slope < 0.0001

    def test_the_flat_cost_is_not_extrapolated_into_the_sky(self):
        """The regression this replaces, stated as a number: 428 frames of
        generation want 0.18 GB, and the old model said 9.5."""
        mod = _mod()
        for units, gb in MEASURED_GENERATE:
            mod._observe_cost("generate", units, gb)
        assert mod._predict_cost("generate", 428) < 0.6

    def test_a_prediction_still_carries_headroom(self):
        """Being wrong high costs one needless eviction. Being wrong low costs
        an OOM. The prediction is never the bare fit."""
        mod = _mod()
        for units, gb in MEASURED_DECODE:
            mod._observe_cost("decode", units, gb)
        fixed, slope = mod._COSTS["decode"].fit()
        assert mod._predict_cost("decode", 428) > fixed + slope * 428

    def test_a_steady_engine_earns_a_tighter_margin(self):
        """The deviation is what buys headroom, so an engine that keeps
        answering the same way should stop paying for it."""
        mod = _mod()
        for _ in range(40):
            for units, gb in MEASURED_DECODE:
                mod._observe_cost("decode", units, gb)
        assert mod._predict_cost("decode", 428) == pytest.approx(2.2, rel=0.1)

    def test_an_erratic_engine_keeps_a_wide_one(self):
        """Same fit, wildly different samples around it - the margin must NOT
        collapse. This is the whole reason the residual is tracked."""
        noisy = _mod()
        for _ in range(20):
            for units, gb in MEASURED_DECODE:
                noisy._observe_cost("decode", units, gb * 0.4)
                noisy._observe_cost("decode", units, gb * 1.6)
        steady = _mod()
        for _ in range(40):
            for units, gb in MEASURED_DECODE:
                steady._observe_cost("decode", units, gb)
        assert (noisy._predict_cost("decode", 428)
                > steady._predict_cost("decode", 428))

    def test_a_single_sample_falls_back_to_the_declared_slope(self):
        """One point does not determine a line, so the line is not invented
        from it - but reporting slope 0 would claim "cost does not depend on
        size", which is the OPTIMISTIC direction and would let an arbitrarily
        large decode look free. The seed stands until data can contradict it."""
        mod = _mod()
        mod._observe_cost("decode", units=100, gb=2.0)
        fixed, slope = mod._COSTS["decode"].fit()
        seed = mod._SEED_DECODE_GB / mod._SEED_DECODE_FRAMES
        assert slope == pytest.approx(seed)
        # The measured level is preserved: fixed + slope*100 == the 2.0 seen.
        assert fixed + slope * 100 == pytest.approx(2.0)

    def test_a_falling_cost_never_becomes_a_negative_slope(self):
        """Noise can produce a cost that falls as the work grows, which is
        meaningless AND under-predicts - the one direction that costs an OOM.
        The intercept is deliberately NOT clamped (see Line.fit); the
        prediction is floored instead, so evidence is kept and the answer still
        cannot go below zero."""
        mod = _mod()
        for units, gb in [(100, 2.0), (200, 1.0), (300, 0.2)]:
            mod._observe_cost("decode", units, gb)
        _fixed, slope = mod._COSTS["decode"].fit()
        assert slope >= 0.0
        assert mod._predict_cost("decode", 4000) >= 0.0


class TestTheEstimatorRefusesToInvent:
    def test_never_measured_is_None_and_not_zero(self):
        """Zero would read as 'this costs nothing', turning the reserve check
        into a rubber stamp on exactly the first run - the one run where
        nothing is known."""
        mod = _mod()
        assert mod._predict_cost("decode", 100) is None

    def test_a_zero_unit_operation_teaches_nothing(self):
        mod = _mod()
        mod._observe_cost("decode", units=0, gb=2.0)
        assert "decode" not in mod._COSTS

    def test_a_free_operation_teaches_nothing(self):
        """A measurement of 0 GB means the probe could not see, not that the
        decode was free."""
        mod = _mod()
        mod._observe_cost("decode", units=100, gb=0.0)
        assert "decode" not in mod._COSTS


class TestTheProbeIsHarmless:
    def test_it_records_the_peak_above_where_it_started(self):
        """The op's own appetite, not what the rest of the machine is doing to
        the card while it runs."""
        mod = _mod()
        _with_torch(mod, allocated=7.0, peak=9.0)
        with mod._measure("decode", 100):
            pass
        fixed, slope = mod._COSTS["decode"].fit()
        assert fixed + slope * 100 == pytest.approx(2.0)   # 9.0 - 7.0, not 9.0

    def test_it_resets_the_peak_so_the_last_op_is_not_counted_twice(self):
        mod = _mod()
        torch = _with_torch(mod, allocated=1.0, peak=2.0)
        with mod._measure("decode", 100):
            pass
        assert torch.cuda.resets == 1

    def test_a_failed_operation_is_not_measured(self):
        """A failed op's peak says nothing about what a successful one costs -
        and the OOM path is exactly where a false sample would be recorded."""
        mod = _mod()
        _with_torch(mod, allocated=1.0, peak=9.0)
        with pytest.raises(RuntimeError):
            with mod._measure("decode", 100):
                raise RuntimeError("boom")
        assert "decode" not in mod._COSTS

    def test_it_never_swallows_the_exception(self):
        mod = _mod()
        _with_torch(mod, allocated=1.0, peak=2.0)
        with pytest.raises(ValueError):
            with mod._measure("decode", 100):
                raise ValueError("must reach the caller")

    def test_a_card_that_cannot_be_read_does_not_break_the_operation(self):
        """Measuring is an optimisation. Speech is not."""
        mod = _mod()
        _with_torch(mod, explode=True)
        ran = []
        with mod._measure("decode", 100):
            ran.append(True)
        assert ran == [True]
        assert "decode" not in mod._COSTS

    def test_with_no_engine_at_all_it_is_a_no_op(self):
        mod = _mod()
        mod._ENGINE.pop("torch", None)
        with mod._measure("decode", 100):
            pass
        assert "decode" not in mod._COSTS

    def test_it_reports_what_it_measured(self):
        """The reporting branch is only reached with a real `send`, and every
        other probe test passes None - so this line was never executed by the
        suite at all. It shipped broken: the cost table moved from a dict to a
        fitted Line and the report kept calling `.get()` on it, which took down
        the whole synthesis with "'Line' object has no attribute 'get'".

        Reported, not just recorded, is the point: every policy regression in
        this worker was caught by SEEING a decision, never by reasoning about
        one. A report that raises is worse than no report at all."""
        mod = _mod()
        _with_torch(mod, allocated=0.0, peak=2.0)
        sent = []
        with mod._measure("decode", 100, sent.append):
            pass
        assert len(sent) == 1
        event = sent[0]
        assert event["stage"] == "cost"
        assert event["kind"] == "decode"
        assert event["units"] == 100
        assert event["peak_gb"] == pytest.approx(2.0)
        assert event["samples"] == 1
        assert event["predict_gb"] > 0

    def test_reporting_survives_a_kind_it_has_never_seen(self):
        """A measurement that taught nothing (zero units) still reports."""
        mod = _mod()
        _with_torch(mod, allocated=0.0, peak=2.0)
        sent = []
        with mod._measure("decode", 0, sent.append):
            pass
        assert sent[0]["samples"] == 0

    def test_the_unit_can_be_corrected_before_the_block_closes(self):
        """A generation opens with its BUDGET and closes with what it actually
        produced. Recording the budget would teach a cost that never happened."""
        mod = _mod()
        _with_torch(mod, allocated=0.0, peak=2.0)
        with mod._measure("generate", 800) as measured:
            measured.units = 100
        assert mod._COSTS["generate"].n == 1
        fixed, slope = mod._COSTS["generate"].fit()
        assert fixed + slope * 100 == pytest.approx(2.0)


class TestItIsWiredToTheOperationsThatMatter:
    """It used to say "source-level, because the alternative is a CUDA card
    in CI". The alternative turned out to be a fake card that REPORTS a
    peak: `_measure` records `peak - allocated`, so a probe with something
    to say makes the wiring observable without a GPU. Counting call sites
    in the file could not tell a probe that runs from one that is written
    down, and could not see the units it was given at all.
    """

    PEAK_GB = 3.4

    def test_the_decode_is_measured_against_the_frame_count(self):
        run = synth.decode_to_audio(frames=400, peak_gb=self.PEAK_GB)
        assert run.costs == [("decode", 400.0, self.PEAK_GB)], run.costs
        # The units are the FRAMES, not some other number that happens to
        # be lying around: a different frame count moves the record.
        other = synth.decode_to_audio(frames=137, peak_gb=self.PEAK_GB)
        assert other.costs == [("decode", 137.0, self.PEAK_GB)], other.costs

    def test_both_decode_attempts_are_measured_not_only_the_retry(self):
        """The failing attempt records nothing - `_measure` is allowed to
        learn nothing rather than something false - so the cost log alone
        cannot tell "wrapped and learnt nothing" from "not wrapped". The
        probe COUNT can: opening the block resets the peak either way.
        """
        clean = synth.decode_to_audio(peak_gb=self.PEAK_GB)
        assert clean.torch.cuda.probes == 1

        retried = synth.decode_to_audio(fail_times=1, peak_gb=self.PEAK_GB)
        assert retried.attempts == 2
        assert retried.torch.cuda.probes == 2, (
            "the retry ran outside the probe, so a decode that only OOMs "
            "on the first try teaches the estimator nothing about itself")
        # ... and only the attempt that SUCCEEDED was recorded.
        assert len(retried.costs) == 1, retried.costs

    def test_the_generation_is_measured_and_corrected_to_what_it_produced(self):
        """Opened with the budget, closed with the truth. Recording the
        budget would teach the estimator a cost that never happened."""
        run = synth.synthesize(produced=137, max_new=640,
                               peak_gb=self.PEAK_GB)
        assert run.costs_for("generate") == [("generate", 137.0, self.PEAK_GB)], (
            run.costs)
        assert run.max_new_used() == 640, "the budget itself did not change"


class _FakeModel:
    """A model that can be told to refuse a device move, the way a quantised
    tensor subclass or a compiled graph might."""

    def __init__(self, refuse_cpu=False, refuse_cuda=False):
        self.refuse_cpu = refuse_cpu
        self.refuse_cuda = refuse_cuda
        self.device = "cuda"
        self.moves = []

    def to(self, device):
        if device == "cpu" and self.refuse_cpu:
            raise RuntimeError("fp8 subclass will not leave the card")
        if device != "cpu" and self.refuse_cuda:
            raise RuntimeError("no room to come back")
        self.moves.append(device)
        self.device = device
        return self


def _quiet(*_a, **_k):
    """A `send` that goes nowhere - these tests are about state, not events."""


class _BuildCuda:
    """Only the allocator calls the build and the eviction actually make.

    `is_available` is here because `_sweep` asks: a fake that answers less than
    the real thing is asked would blow up inside the code under test and the
    failure would look like a policy bug rather than a missing stub.
    """

    def __init__(self):
        self.synchronized = 0
        self.emptied = 0

    def is_available(self):
        return True

    def synchronize(self):
        self.synchronized += 1

    def empty_cache(self):
        self.emptied += 1


class _BuildTorch:
    """A `torch` with a device context manager and nothing else invented."""

    bfloat16 = "bfloat16-sentinel"

    def __init__(self):
        self.cuda = _BuildCuda()
        self.devices = []

    def device(self, name):
        self.devices.append(name)
        return contextlib.nullcontext()


class _BuiltModel:
    """What `init_model` hands back - enough of it to cap the KV cache."""

    class _Config:
        max_seq_len = 4096

    def __init__(self):
        self.config = self._Config()
        self.max_seq_len = None
        self.caches = []
        self.device = "cuda"

    def setup_caches(self, max_batch_size, max_seq_len, dtype):
        self.caches.append((max_batch_size, max_seq_len, dtype))
        self.max_seq_len = max_seq_len


def _build(mod, *, kv_len: int = 2048):
    """Run the REAL `_build_model` against a fake engine.

    Nothing about the build's POLICY is faked - only the boundary it cannot
    have here: weights off disk, a CUDA allocator, and the fp8 quantiser.
    torchao is not installed in this environment, so the quantise step takes
    its documented "carry on in bf16" branch and the rest of the function runs
    exactly as it does in production.
    """
    torch = _BuildTorch()
    model = _BuiltModel()
    engine = {
        "torch": torch,
        "init_model": lambda *_a, **_k: (model, "a freshly compiled decode"),
        "decode_eager": "the eager decode",
    }
    mod._engine = lambda: engine
    mod._ENGINE["torch"] = torch
    sent = []
    mod._build_model(Path("nowhere"), kv_len, sent.append)
    return model, sent


def _lowest_true(predicate, lo: float, hi: float, eps: float = 0.005) -> float:
    """Bisect for the lowest value in (lo, hi] where `predicate` holds.

    Both ends are ground controls. A predicate already true at `lo`, or still
    false at `hi`, has no boundary inside the bracket at all, and the search
    would hand back the edge of the bracket as if it had found one.
    """
    assert not predicate(lo), f"the predicate was already true at {lo}"
    assert predicate(hi), f"the predicate was still false at {hi}"
    while hi - lo > eps:
        mid = (lo + hi) / 2.0
        if predicate(mid):
            hi = mid
        else:
            lo = mid
    return hi


class TestTheModelParksInsteadOfDying:
    """Destroying the model costs a ~28 s rebuild to reclaim memory a PCIe copy
    gives back in a second. That trade was only worth taking while this rung
    did not exist."""

    def test_parking_holds_the_model_and_its_compiled_decode_together(self):
        """The compiled decode is a function over THIS model. Restoring one
        without the other rebuilds a graph that already exists."""
        mod = _mod()
        model = _FakeModel()
        mod.STATE["model"] = model
        mod.STATE["decode"] = "compiled-decode"
        assert mod._park_model(_quiet) is True
        assert mod.STATE["model_parked"] == (model, "compiled-decode")
        assert model.device == "cpu"

    def test_restoring_skips_the_rebuild_entirely(self):
        mod = _mod()
        model = _FakeModel()
        model.device = "cpu"
        mod.STATE["model"] = None
        mod.STATE["model_path"] = "somewhere"
        mod.STATE["model_parked"] = (model, "compiled-decode")
        mod.STATE["evicted"] = True
        mod._ensure_model(_quiet)
        assert mod.STATE["model"] is model
        assert mod.STATE["decode"] == "compiled-decode"
        assert mod.STATE["model_parked"] is None
        assert mod.STATE["evicted"] is False

    def test_a_model_that_refuses_to_park_falls_back_to_the_old_path(self):
        """Slower, never broken. The caller nulls it out and rebuilds."""
        mod = _mod()
        mod.STATE["model"] = _FakeModel(refuse_cpu=True)
        mod.STATE["decode"] = "compiled-decode"
        assert mod._park_model(_quiet) is False
        assert mod.STATE["model_parked"] is None

    def test_a_park_that_cannot_come_back_rebuilds_from_disk(self):
        mod = _mod()
        rebuilt = []
        mod._build_model = lambda *a, **k: rebuilt.append(True)
        mod.STATE["model"] = None
        mod.STATE["model_path"] = "somewhere"
        mod.STATE["model_parked"] = (_FakeModel(refuse_cuda=True), "decode")
        mod._ensure_model(_quiet)
        assert rebuilt == [True]
        assert mod.STATE["model_parked"] is None


class TestNothingIsLeftHoldingHostMemory:
    def test_unloading_releases_both_parked_copies(self):
        """This was a real leak: unload cleared the VRAM slots and left the
        parked codec holding ~1.9 GB of host memory for the life of the
        worker. With the model parking too it would have been ~7 GB."""
        mod = _mod()
        mod.STATE["model_parked"] = (_FakeModel(), "decode")
        mod.STATE["codec_parked"] = object()
        mod._unload_everything()
        assert mod.STATE["model_parked"] is None
        assert mod.STATE["codec_parked"] is None

    def test_a_fresh_build_drops_a_stale_park(self):
        """Holding it would be ~7 GB nothing will ever restore.

        KADEME 13 lesson applied again: this used to slice `_build_model`'s own
        SOURCE TEXT out of the file, between two `def` lines, and look for the
        string `STATE["model_parked"] = None` inside it. That passes on a build
        where the line sits in a branch nothing reaches, and fails on one where
        the same rule is spelled `STATE.update(model_parked=None)` - it tested
        the file, not the program. It runs the real function now, against a
        fake engine, and looks at the slot afterwards.
        """
        mod = _mod()
        stale = _FakeModel()
        mod.STATE["model"] = None
        mod.STATE["model_parked"] = (stale, "a decode compiled for a dead model")
        # Positive control for the absence asserted below: the slot is NOT
        # empty going in, so "None afterwards" is a thing the build did rather
        # than a thing that was already true.
        assert mod.STATE["model_parked"] is not None

        built, _sent = _build(mod)

        assert mod.STATE["model"] is built, (
            "the fake engine never got as far as installing a model, so this "
            "test is measuring a crash rather than the park slot")
        assert built is not stale, "the build handed back the parked copy"
        assert mod.STATE["model_parked"] is None, (
            "a rebuild from disk left the previous park in system RAM: ~7 GB "
            "held for the life of the worker that nothing can ever restore")

    def test_the_build_that_drops_the_park_is_a_working_build(self):
        """Ground control for the test above. A `_build_model` that fell over
        early would also leave `model_parked` untouched-looking, so what the
        build DID is pinned too: a model, its decode, and the KV cap."""
        mod = _mod()
        mod.STATE["model_parked"] = None
        built, sent = _build(mod, kv_len=2048)
        assert mod.STATE["model"] is built
        assert mod.STATE["decode"] == "a freshly compiled decode"
        assert mod.STATE["kv_len"] == 2048, "the KV cache cap was not applied"
        assert mod.STATE["evicted"] is False
        assert [e["stage"] for e in sent][-1] == "loaded", (
            "the build did not reach the end of its own progress sequence")


class TestTheEvictionLadderIsOrderedByCost:
    """The cheap rung runs while the model is still reachable, or it is not a
    rung at all.

    This used to compare two SUBSTRING POSITIONS in the file's own text -
    `_park_model(send)` earlier in the string than `STATE["model"] = None`.
    Text order is not execution order: an early-returning branch, a `finally`,
    or a park moved into a helper all keep the substring positions and break
    the behaviour. What is measured now is where the model ENDED UP.
    """

    def _card(self, mod, free_gb=0.2):
        """A card with a torch on it and a fixed free reading."""
        torch = _BuildTorch()
        mod._ENGINE["torch"] = torch
        mod._free_gb = lambda: free_gb
        return torch

    def test_the_cheap_rung_is_tried_before_the_expensive_one(self):
        """`_park_model` must run BEFORE the model is nulled out, or the park
        has nothing left to park. Run in the wrong order the eviction still
        frees exactly the same VRAM and still returns True - the only thing
        that changes is that the park slot comes out empty, which is why that
        slot is what this looks at."""
        mod = _mod()
        model = _FakeModel()
        mod.STATE["model"] = model
        mod.STATE["decode"] = "compiled-decode"
        mod.STATE["model_parked"] = None
        card = self._card(mod)

        assert mod._free_for_codec(_quiet, "a tight card", force=True) is True

        assert mod.STATE["model"] is None, "the eviction did not happen at all"
        assert mod.STATE["decode"] is None
        assert mod.STATE["evicted"] is True
        assert card.cuda.emptied >= 1, "the allocator was never asked to let go"
        assert mod.STATE["model_parked"] == (model, "compiled-decode"), (
            "the eviction nulled the model out before trying to park it, so "
            "the cheap rung found nothing to park and the next sentence pays "
            "a ~28 s rebuild from disk instead of a one-second PCIe copy")
        assert model.device == "cpu", "the model never left the card"

    def test_the_park_is_what_the_next_sentence_comes_back_from(self):
        """The payoff, end to end: evict, then bring the model back. In the
        wrong order there is nothing parked and `_ensure_model` rebuilds."""
        mod = _mod()
        model = _FakeModel()
        mod.STATE["model"] = model
        mod.STATE["decode"] = "compiled-decode"
        mod.STATE["model_path"] = "somewhere"
        self._card(mod)
        rebuilt = []
        mod._build_model = lambda *a, **k: rebuilt.append(True)

        mod._free_for_codec(_quiet, "a tight card", force=True)
        mod._ensure_model(_quiet)

        assert rebuilt == [], (
            "the model was destroyed rather than parked, so coming back cost "
            "a ~28 s rebuild from disk")
        assert mod.STATE["model"] is model, "a different model came back"
        assert mod.STATE["decode"] == "compiled-decode", (
            "the compiled decode did not come back with its model, so the "
            "graph gets rebuilt for a model that already has one")
        assert model.device == "cuda"
        assert mod.STATE["model_parked"] is None

    def test_the_ordinary_tight_card_climbs_the_same_ladder(self):
        """`force=True` is the OOM-retry route. The everyday one is `_fits`
        saying no, and the rungs have to be in that order there too."""
        mod = _mod()
        model = _FakeModel()
        mod.STATE["model"] = model
        mod.STATE["decode"] = "compiled-decode"
        self._card(mod, free_gb=0.5)
        assert mod._fits(400) is False, "the fixture is not a tight card"

        assert mod._free_for_codec(_quiet, "a tight card", frames=400) is True
        assert mod.STATE["model_parked"] == (model, "compiled-decode")

    def test_with_nothing_resident_there_is_nothing_to_park(self):
        """The positive control for all three above. `model_parked` is not a
        tuple whatever happens and `_build_model` is not uncalled whatever
        happens: with the model already gone the cheap rung has nothing to
        take, the slot stays empty, and the restore really does rebuild."""
        mod = _mod()
        mod.STATE["model"] = None
        mod.STATE["decode"] = None
        mod.STATE["model_path"] = "somewhere"
        self._card(mod)
        rebuilt = []
        mod._build_model = lambda *a, **k: rebuilt.append(True)

        mod._free_for_codec(_quiet, "a tight card", force=True)
        assert mod.STATE["model_parked"] is None

        mod._ensure_model(_quiet)
        assert rebuilt == [True]

    def test_a_card_with_room_does_not_climb_the_ladder_at_all(self):
        """The other ground control: the eviction is not unconditional, so a
        park appearing above is a decision rather than a reflex."""
        mod = _mod()
        model = _FakeModel()
        mod.STATE["model"] = model
        mod.STATE["decode"] = "compiled-decode"
        self._card(mod, free_gb=12.0)

        assert mod._free_for_codec(_quiet, "plenty of room", frames=400) is False
        assert mod.STATE["model"] is model, "a card with room lost its model"
        assert mod.STATE["model_parked"] is None
        assert model.device == "cuda"


class TestOneReserveDecidesEverything:
    """Not "is free memory above a number" but "will this operation leave the
    desktop its 1 GB". Those are different questions and only the second one
    survives a cost that varies."""

    def _fixed_free(self, mod, gb):
        mod._free_gb = lambda: gb

    def test_a_measured_operation_that_fits_is_allowed(self):
        mod = _mod()
        mod._observe_cost("decode", units=100, gb=0.5)   # ~0.005 GB/frame
        self._fixed_free(mod, 8.0)
        assert mod._fits(100) is True

    def test_the_same_card_refuses_a_bigger_operation(self):
        """The failure a fixed floor could not see: identical free memory,
        different answer, because the work is eight times the size.

        Two sizes of sample, because one point is deliberately NOT turned into
        a slope - with a single measurement there is nothing to extrapolate
        from and the estimator says so."""
        mod = _mod()
        for units, gb in MEASURED_DECODE:
            mod._observe_cost("decode", units, gb)
        self._fixed_free(mod, 3.0)
        assert mod._fits(100) is True
        assert mod._fits(800) is False

    def test_the_reserve_is_what_is_left_over_not_what_is_used(self):
        """The check is on what REMAINS, not on what is consumed. Sit the card
        right at the line and it passes; take one decimal away and it does not."""
        mod = _mod()
        for _ in range(60):
            for units, gb in MEASURED_DECODE:
                mod._observe_cost("decode", units, gb)
        need = mod._predict_cost("decode", 264)
        self._fixed_free(mod, need + 1.0 + 0.01)
        assert mod._fits(264) is True
        self._fixed_free(mod, need + 1.0 - 0.01)
        assert mod._fits(264) is False

    def test_concurrent_costs_take_the_largest_not_the_sum(self):
        """A generation finishes before its decode starts, so their peaks never
        coexist. Summing them would evict for a high-water mark that never
        happens."""
        mod = _mod()
        # Converged, so the deviation margin is out of the way and the test is
        # about the max-vs-sum rule rather than about headroom arithmetic.
        for _ in range(60):
            mod._observe_cost("generate", units=100, gb=1.0)
            mod._observe_cost("decode", units=100, gb=2.0)
        assert mod._predict_cost("decode", 100) == pytest.approx(2.0, rel=0.05)
        self._fixed_free(mod, 3.5)
        # largest (~2.0) leaves 1.5, over the reserve; the sum (~3.0) leaves 0.5
        assert mod._fits(100, "generate", "decode") is True
        self._fixed_free(mod, 2.5)
        assert mod._fits(100, "generate", "decode") is False


class TestTheFirstRunIsNotATrustFall:
    def test_with_nothing_measured_it_assumes_the_old_proven_floor(self):
        """Applying "evict at 1 GB" with no cost model is exactly the OOM this
        replaces. Until something is measured, a full-budget decode is assumed
        to want what the old fixed floor reserved."""
        mod = _mod()
        assert mod._seed_gb(mod._SEED_DECODE_FRAMES) == pytest.approx(3.0)

    def test_the_seed_scales_with_size_like_a_real_cost_does(self):
        mod = _mod()
        assert mod._seed_gb(400) == pytest.approx(1.5)
        assert mod._seed_gb(1600) == pytest.approx(6.0)

    def test_an_unsized_operation_assumes_the_whole_budget(self):
        """Encoding a reference clip has no frame count of its own; it gets the
        cautious answer rather than a free pass."""
        mod = _mod()
        assert mod._seed_gb(0) == pytest.approx(3.0)

    def test_one_real_measurement_replaces_the_prior(self):
        """A single honest sample has to be able to move the answer. It could
        not while the intercept was clamped at zero: the prediction snapped
        back to the seed and the measurement was thrown away."""
        mod = _mod()
        seeded = mod._seed_gb(800)
        mod._observe_cost("decode", units=800, gb=0.4)
        assert mod._predict_cost("decode", 800) == pytest.approx(0.4)
        assert mod._predict_cost("decode", 800) < seeded


#: Six consecutive codec loads on an RTX 5080, from verify/verify_tts_latency.py.
#: Every one of them is the SAME indivisible object, which is the whole point:
#: there is no size to learn from, so a margin can only come from the level.
MEASURED_CODEC = [5.22, 4.915, 4.913, 4.913, 4.913, 4.913]


class TestAFixedSizeCostIsNotGivenAMarginItCannotJustify:
    """MEASURED BUG: text2semantic was parked to system RAM and pulled back for
    every sentence, ~1.2 s of the 2.29 s fixed cost per call, for a shortage
    that was arithmetic rather than real.

    `Line.predict` answers `fit + 4 * dev`, and `dev` is seeded at half the
    estimate on the second sample (RFC 6298). For the decode that is honest -
    its cost really does vary with the work. The codec is one object loaded the
    same way every time, so the margin was bootstrapped from its own level:
    15.2 GB reserved for a 4.9 GB load, decaying over ~10 samples.
    """

    def _observed(self, mod):
        for gb in MEASURED_CODEC:
            mod._observe_cost("codec", 1, gb)
        return mod

    def test_the_reserve_settles_on_what_the_codec_steadily_costs(self):
        """5.22 GB is the first load, from disk; every restore since has been
        4.91. A running maximum would hold the reserve at the one-off forever,
        so the ceiling decays and the figure that keeps being re-confirmed is
        the one that ends up being reserved."""
        mod = self._observed(_mod(codec_resident=False))
        assert mod._codec_need() == pytest.approx(MEASURED_CODEC[-1], abs=0.01)
        assert mod._codec_need() < max(MEASURED_CODEC)

    def test_a_freak_sample_is_covered_at_once_and_then_forgotten(self):
        """Both halves matter. A cost that has actually been seen must be
        reserved for immediately - being wrong low is the OOM direction. But a
        fragmented allocator or a process that arrived mid-load must not pin
        the reserve there for the life of the worker, which is what a plain
        maximum does: the eviction it causes never goes away."""
        mod = self._observed(_mod(codec_resident=False))
        steady = mod._codec_need()

        mod._observe_cost("codec", 1, 9.0)
        assert mod._codec_need() == pytest.approx(9.0), "an outlier is covered"

        for _ in range(20):
            mod._observe_cost("codec", 1, MEASURED_CODEC[-1])
        assert mod._codec_need() == pytest.approx(steady, abs=0.01), (
            "and then forgotten"
        )

    def test_it_does_not_ask_for_three_times_the_measurement(self):
        """The regression as a number: six samples of ~4.9 GB, and the answer
        was 8.4 GB and still falling."""
        mod = self._observed(_mod(codec_resident=False))
        assert mod._codec_need() < 1.2 * max(MEASURED_CODEC)

    def test_the_second_sentence_costs_about_what_the_twentieth_does(self):
        """It used to take ~10 loads for the margin to decay out, so a short
        session never reached the steady state and paid the eviction every
        time. The answer must not depend on how long the session has run - not
        to the last megabyte, since the samples themselves differ by that much,
        but nowhere near the 15.2-to-8.4 GB slide it used to be."""
        early = _mod(codec_resident=False)
        for gb in MEASURED_CODEC[:2]:
            early._observe_cost("codec", 1, gb)
        late = self._observed(_mod(codec_resident=False))
        assert early._codec_need() == pytest.approx(late._codec_need(), abs=0.05)

    def test_a_codec_already_on_the_card_costs_nothing_to_put_there(self):
        mod = self._observed(_mod(codec_resident=True))
        assert mod._codec_need() == 0.0

    def test_with_nothing_measured_it_reserves_the_prior(self):
        """The first load has no evidence, and it is the load least able to
        recover from being wrong."""
        mod = _mod(codec_resident=False)
        assert mod._codec_need() == pytest.approx(mod._SEED_CODEC_GB)
        assert mod._SEED_CODEC_GB >= max(MEASURED_CODEC) * 0.9, (
            "the prior has to cover the load peak, not the file size"
        )

    def test_a_larger_codec_than_the_prior_still_moves_the_answer(self):
        """Evidence beats the prior in BOTH directions - a prior that could
        only ever be raised would be a threshold wearing a measurement's hat."""
        mod = _mod(codec_resident=False)
        mod._observe_cost("codec", 1, 1.4)
        assert mod._codec_need() == pytest.approx(1.4)


class TestTheEvictionDecisionItself:
    """`_fits` is where the arithmetic became a park/restore round trip."""

    def _card(self, mod, free_gb):
        mod._free_gb = lambda: free_gb
        return mod

    def test_a_decode_fits_beside_a_resident_model_on_a_16gb_card(self):
        """The measured working set: fp8 text2semantic ~3.9 GB, KV cache at
        kv_cache_len=2048 ~0.28 GB, codec load peak ~5.2 GB, decode of 450
        frames ~2.3 GB. That leaves room, and the eviction was never needed.
        """
        mod = _mod(codec_resident=False)
        for gb in MEASURED_CODEC:
            mod._observe_cost("codec", 1, gb)
        for units, gb in MEASURED_DECODE:
            mod._observe_cost("decode", units, gb)
        self._card(mod, free_gb=11.0)
        assert mod._fits(450, "decode") is True

    def test_a_card_that_really_is_tight_still_refuses(self):
        """The check has to keep working. Being wrong high costs one needless
        eviction; being wrong low costs an OOM."""
        mod = _mod(codec_resident=False)
        for gb in MEASURED_CODEC:
            mod._observe_cost("codec", 1, gb)
        for units, gb in MEASURED_DECODE:
            mod._observe_cost("decode", units, gb)
        self._card(mod, free_gb=6.0)
        assert mod._fits(450, "decode") is False


class TestTheKeepDecisionUsesTheSameReserveTheFitCheckDoes:
    """`_should_keep_codec(free)` is `free >= _VRAM_RESERVE_GB`, so a test that
    imports that constant and feeds it straight back asks whether `x >= x`.
    That is true of EVERY threshold, including the `free < 4.0` this function's
    own docstring records as the bug it replaced - the one that evicted the
    codec after every sentence and paid a ~5 s reload for the next one.

    The number is pinned from the other side instead. `_fits` enforces the same
    reserve, and its boundary is observable without naming it: bisect the free
    reading at which it flips, subtract the cost it was forecasting, and what
    is left is the reserve as the fit check actually applies it. The keep
    policy is then compared against THAT. Nothing is retyped, and the two
    thresholds can no longer drift apart in silence.
    """

    UNITS = 400

    def _reserve_from_fits(self, mod) -> float:
        """What `_fits` demands be LEFT OVER, measured rather than read."""
        def fits(free_gb):
            mod._free_gb = lambda: free_gb
            return mod._fits(self.UNITS)

        return _lowest_true(fits, 0.0, 40.0) - mod._seed_gb(self.UNITS)

    def test_the_fit_check_has_a_reserve_and_it_is_a_real_boundary(self):
        """Ground control for the measurement the next two tests lean on: the
        bisected figure is re-checked against the function directly, one step
        either side."""
        mod = _mod(codec_resident=True)
        reserve = self._reserve_from_fits(mod)
        assert 0.0 < reserve < 40.0, f"no reserve was found at all: {reserve}"

        need = mod._seed_gb(self.UNITS)
        mod._free_gb = lambda: need + reserve + 0.05
        assert mod._fits(self.UNITS) is True
        mod._free_gb = lambda: need + reserve - 0.05
        assert mod._fits(self.UNITS) is False

    def test_the_codec_is_kept_exactly_while_that_reserve_survives(self):
        mod = _mod(codec_resident=True)
        reserve = self._reserve_from_fits(mod)
        keeps_above = _lowest_true(mod._should_keep_codec, 0.0, 40.0)
        assert keeps_above == pytest.approx(reserve, abs=0.02), (
            f"the codec is kept down to {keeps_above:.2f} GB free while the "
            f"fit check reserves {reserve:.2f} GB - two thresholds for one "
            "policy, and the wider one decides by accident")

    def test_the_real_decode_drops_the_codec_on_that_same_line(self):
        """Through `_decode_to_audio`, the caller that ACTS on the answer: the
        codec is still on the card above the line and gone below it, and the
        progress frame reports the decision that was actually taken."""
        def survived(free_gb):
            mod = synth.load_worker()
            # `_drop_codec` sweeps the allocator on its way out. The harness's
            # fake card does not answer `is_available`, and a gc pass is not
            # what is being measured here.
            mod._sweep = lambda: None
            run = synth.decode_to_audio(free_gb=free_gb, mod=mod)
            reported = [e for e in run.events if e.get("stage") == "codec_policy"]
            assert len(reported) == 1, "the decode did not report a codec policy"
            kept = run.mod.STATE["codec"] is not None
            assert reported[0]["keep"] is kept, (
                "the frame this policy is read from disagrees with what the "
                "policy did - the only way the last regression was caught was "
                "by seeing this number")
            return kept

        boundary = _lowest_true(survived, 0.0, 40.0)
        assert boundary == pytest.approx(
            self._reserve_from_fits(_mod(codec_resident=True)), abs=0.02), (
            f"a real decode holds the codec down to {boundary:.2f} GB free, "
            "which is not where the reserve is")


class TestPeakAndRetainedAreMeasuredSeparately:
    """`_fits` MAXes the work terms and ADDS the codec, and that asymmetry is
    only correct if the codec really does stay on the card while the decode
    runs. Reasoning from `codec.pth` (1.74 GB on disk) said it was mostly a
    load-time transient and the addition was over-reserving by ~3 GB. The
    measurement said otherwise: 4.915 GB peak AND 4.915 GB retained, no
    transient at all, while a decode retains nothing.
    """

    def _run(self, mod, kind, units, *, allocated, peak, ends_at):
        """One measured operation. `ends_at` is what is still allocated when it
        returns - the whole point of the second number."""
        sent = []
        _with_torch(mod, allocated=allocated, peak=peak)
        with mod._measure(kind, units, sent.append):
            mod._ENGINE["torch"].cuda._allocated = ends_at * 1e9
        assert len(sent) == 1, "one operation reports once"
        return sent[0]

    def test_an_operation_that_frees_everything_retains_nothing(self):
        """A decode's whole cost is its peak: 2.3 GB while it runs, nothing
        afterwards. `_fits` MAXes these against each other for that reason."""
        mod = _mod()
        event = self._run(mod, "decode", 400,
                          allocated=4.0, peak=6.3, ends_at=4.0)
        assert event["peak_gb"] == pytest.approx(2.3)
        assert event["retained_gb"] == pytest.approx(0.0, abs=1e-9)
        # Reported, never fitted: a second estimator nothing consults would be
        # a fit kept warm for a reader that does not exist.
        assert not [k for k in mod._COSTS if "resident" in k]

    def test_an_operation_that_keeps_its_allocation_says_so(self):
        """The codec: 4.915 GB peak and 4.915 GB still there afterwards. That
        is what licenses `_fits` to ADD it rather than MAX it."""
        mod = _mod()
        event = self._run(mod, "codec", 1,
                          allocated=4.0, peak=8.915, ends_at=8.915)
        assert event["peak_gb"] == pytest.approx(4.915)
        assert event["retained_gb"] == pytest.approx(4.915)

    def test_a_failed_operation_teaches_nothing(self):
        mod = _mod()
        _with_torch(mod, allocated=1.0, peak=9.0)
        try:
            with mod._measure("decode", 400):
                raise RuntimeError("engine died")
        except RuntimeError:
            pass
        assert "decode" not in mod._COSTS


class TestTheReportAgreesWithThePolicy:
    """The progress frame is where every policy regression in this file was
    caught, so a number printed there has to be the number being acted on.
    After the codec's reserve moved to `worst`, the frame still printed
    `predict` - 15.2 GB beside a decision made on 4.9."""

    def test_the_reported_codec_cost_is_the_one_the_reserve_uses(self):
        mod = _mod(codec_resident=False)
        for gb in MEASURED_CODEC:
            mod._observe_cost("codec", 1, gb)
        assert mod._planning_cost("codec", 1) == pytest.approx(mod._codec_need())

    def test_a_work_scaled_cost_still_reports_its_pessimistic_prediction(self):
        """Only the fixed-size line changed. A decode's margin is what keeps an
        OOM from being the way we find out the estimate was low."""
        mod = _mod()
        for units, gb in MEASURED_DECODE:
            mod._observe_cost("decode", units, gb)
        assert (mod._planning_cost("decode", 428)
                == pytest.approx(mod._predict_cost("decode", 428)))
        fixed, slope = mod._COSTS["decode"].fit()
        assert mod._planning_cost("decode", 428) > fixed + slope * 428

    def test_nothing_measured_reports_nothing_rather_than_zero(self):
        mod = _mod()
        assert mod._planning_cost("codec", 1) is None
        assert mod._planning_cost("decode", 400) is None


class TestTheGuardForecastsTheWorkNotTheCeiling:
    """MEASURED BUG: with the codec resident the pre-generation check refused
    on every single sentence, so the codec was parked and copied back for each
    one. It was forecasting `max_new_tokens` - the user's "Max length" dial,
    800 frames, ~4.4 GB - while the sentences the queue hands over measured
    25-435 frames. 800 is about 37 seconds of speech; sentence-level chunking
    makes it unreachable.

    The dial is untouched: it still bounds the generation. Only the forecast
    changed, and the forecast is now learnt from what generations actually
    produce.
    """

    def _taught(self, mod, samples=MEASURED_FRAMES):
        for chars, frames in samples:
            mod._observe_cost("frames", chars, float(frames))
        return mod

    def test_it_says_nothing_until_it_has_seen_something(self):
        """Run one keeps the old worst case rather than a guess - there is no
        measurement yet, and inventing one is how a guard starts firing after
        the OOM instead of before it."""
        assert _mod()._expected_frames(200) is None

    def test_a_short_sentence_is_not_forecast_as_a_long_one(self):
        """Against the CEILING, which is what it used to be measured against.
        A 42-character line produces ~62 frames and was being forecast at 800;
        it lands near 200 now, margin included. The absolute figure moves with
        the engine, so what is pinned is that it is a fraction of the cap and
        that it tracks the text."""
        mod = self._taught(_mod())
        short, long = mod._expected_frames(42), mod._expected_frames(334)
        assert short < 800 / 3, f"still forecasting near the ceiling: {short}"
        assert short < long

    def test_the_forecast_leans_high_rather_than_low(self):
        """Being wrong high costs one eviction. Being wrong low costs a decode
        that has to free memory and retry, which IS the eviction - so the
        margin points the same way here as everywhere else in this file."""
        mod = self._taught(_mod())
        assert mod._expected_frames(190) >= 126

    def test_the_codec_survives_a_sentence_it_used_to_be_evicted_for(self):
        """The whole point, as the arithmetic that produced the bug: 3.59 GB
        free, a 1.0 GB reserve, and a decode forecast that has to fit in what
        is left."""
        mod = self._taught(_mod(codec_resident=True))
        for units, gb in MEASURED_DECODE:
            mod._observe_cost("decode", units, gb)
        mod._free_gb = lambda: 3.59

        assert mod._fits(800, "generate", "decode") is False, "the old forecast"
        assert mod._fits(mod._expected_frames(95), "generate", "decode") is True

    def test_a_long_sentence_may_still_lose_the_codec_and_that_is_correct(self):
        """The card is 16 GB and the codec is 4.9 of it. Honest forecasting was
        the goal, not keeping the codec at any cost - a decode that genuinely
        does not fit still has to say so."""
        mod = self._taught(_mod(codec_resident=True))
        for units, gb in MEASURED_DECODE:
            mod._observe_cost("decode", units, gb)
        mod._free_gb = lambda: 3.59
        assert mod._fits(mod._expected_frames(334), "generate", "decode") is False

    def test_a_run_that_hit_the_ceiling_does_not_teach_the_estimator(self):
        """A capped run says how long the budget was, not how long the text
        wanted to be. Folding it in drags every later forecast towards the cap,
        which is a slow way back to the bug.

        KADEME 13 lesson applied in KADEME 14: this used to read the FUNCTION'S
        SOURCE TEXT and look for the word `max_new` between two other strings.
        That passes on a build where the guard sits in dead code and fails on
        one where the same rule is spelled differently - it tested the file,
        not the program. It now runs the real function, against the harness in
        fish_synth_harness.py, and looks at what the estimator learnt.
        """
        # 640, not 800: 800 is both fish_s2._DEFAULTS["max_new_tokens"] and
        # the harness default, so a build that ignored the budget and
        # hard-coded 800 would satisfy this test without threading
        # anything. The number has to be one nobody would pick by accident.
        finished = synth.synthesize(produced=100, max_new=640)
        assert finished.learnt_frames(), (
            "an ordinary run taught the estimator nothing - the fixture is "
            "not exercising the path this test is about")

        capped = synth.synthesize(produced=640, max_new=640)
        assert not capped.learnt_frames(), (
            "a run that hit the ceiling was folded into the estimator")

    def test_stopping_one_token_short_of_the_ceiling_still_counts_as_capped(self):
        """The guard is `produced >= max_new - 1`, not `== max_new`.

        The generation stops when the budget is reached, and whether that lands
        exactly on it or one short is an implementation detail of the sampler.
        A test sitting only on the exact value would leave the off-by-one free
        to move.
        """
        assert not synth.synthesize(produced=639, max_new=640).learnt_frames()
        assert synth.synthesize(produced=638, max_new=640).learnt_frames()
