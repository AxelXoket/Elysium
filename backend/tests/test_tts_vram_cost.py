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
        which is a slow way back to the bug."""
        import inspect

        src = inspect.getsource(_mod()._op_synthesize)
        observe = src.index('_observe_cost("frames"')
        guard = src.rindex("if produced and not", 0, observe)
        assert "max_new" in src[guard:observe]
