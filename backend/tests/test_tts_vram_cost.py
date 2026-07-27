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
import importlib.util
import sys
from pathlib import Path

import pytest


def _mod(*, codec_resident: bool = True):
    path = Path(__file__).resolve().parents[1] / "tts" / "worker" / "fish_s2.py"
    spec = importlib.util.spec_from_file_location("fish_s2_cost", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("fish_s2_cost", mod)
    spec.loader.exec_module(mod)
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
    """Source-level, because the alternative is a CUDA card in CI."""

    def _source(self):
        return (Path(__file__).resolve().parents[1] / "tts" / "worker"
                / "fish_s2.py").read_text(encoding="utf-8")

    def test_the_decode_is_measured_on_both_attempts(self):
        src = self._source()
        assert src.count('_measure("decode", frames, send)') == 2

    def test_the_decode_is_measured_against_the_frame_count(self):
        src = self._source()
        assert "frames = int(codes.shape[1])" in src

    def test_the_generation_is_measured_and_corrected(self):
        src = self._source()
        assert '_measure("generate", max_new, send)' in src
        assert "measured.units = int(codes.shape[1])" in src


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
        """Holding it would be ~7 GB nothing will ever restore."""
        src = (Path(__file__).resolve().parents[1] / "tts" / "worker"
               / "fish_s2.py").read_text(encoding="utf-8")
        build = src[src.index("def _build_model"):]
        build = build[: build.index("def _warmup")]
        assert 'STATE["model_parked"] = None' in build


class TestTheEvictionLadderIsOrderedByCost:
    def _source(self):
        return (Path(__file__).resolve().parents[1] / "tts" / "worker"
                / "fish_s2.py").read_text(encoding="utf-8")

    def test_the_cheap_rung_is_tried_before_the_expensive_one(self):
        """_park_model must run BEFORE the model is nulled out, or the park has
        nothing left to park."""
        src = self._source()
        body = src[src.index("def _free_for_codec"):]
        body = body[: body.index("# ── references")]
        assert body.index("_park_model(send)") < body.index('STATE["model"] = None')


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
