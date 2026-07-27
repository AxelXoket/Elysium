"""tts/worker/_fit.py - one estimator, two callers, no drift.

Both halves of the voice system have to answer the same shape of question from
noisy samples: "given how big this piece of work is, what will it cost, and how
wrong might I be?" The worker asks it about VRAM (GB per frame). The host asks
it about time (seconds per second of speech). Writing that arithmetic twice is
how two copies of a subtle rule quietly stop agreeing, so it is written once,
here, and imported by both - the worker as a sibling module, the host as
`tts.worker._fit`, the same way `_wire` is shared.

Stdlib only, on purpose: this is imported inside the engine's own interpreter,
which has no idea what the app depends on.

WHY A LINE AND NOT A RATIO
    The first version of this measured cost per unit and nothing else. It was
    wrong in a way worth remembering: a generation's VRAM is nearly CONSTANT
    (the KV cache is allocated up front), so dividing it by a growing frame
    count produced a "per unit" figure that fell every sample. The estimator
    read that as wild variance, covered it with an enormous margin, and
    predicted 9.5 GB for an operation that measured 0.18. Measured on a 5080:

        decode    24 frames -> 0.126 GB    428 -> 2.200 GB   almost pure slope
        generate  24 frames -> 0.168 GB    428 -> 0.181 GB   almost pure fixed

    Both are ordinary cases of `fixed + slope * units`. Fitting only one of the
    two terms cannot represent both, and the failure is not a small inaccuracy
    - it is a policy that evicts before every sentence.

WHY LEAST SQUARES AND NOT AN EWMA OF THE RATIO
    An exact least-squares fit needs five running sums and no history at all,
    so it stays O(1) per sample and cannot grow over a long session. The EWMA
    is kept for the RESIDUAL, which is the thing that actually has to track
    change: how wrong the fit has been lately.

WHY THE MARGIN IS ASYMMETRIC
    Predictions are deliberately pessimistic - `fit + k * deviation`, the shape
    RFC 6298 uses for TCP's retransmission timer, and for the same reason. The
    two directions do not cost the same. Being wrong high costs an eviction
    that was not strictly needed, or a chunk that could have been longer. Being
    wrong low costs an out-of-memory, or a gap the listener hears.
"""
from __future__ import annotations

#: EWMA gain on the residual. RFC 6298's beta.
DEFAULT_BETA = 0.25

#: Deviations of headroom a prediction carries. RFC 6298's K.
DEFAULT_K = 4.0


class Line:
    """`y = fixed + slope * x`, fitted incrementally, predicted pessimistically.

    Before anything has been measured the seed answers instead, so a caller
    never has to special-case "no data" - it only has to supply a seed it is
    willing to stand behind.
    """

    __slots__ = ("n", "_sx", "_sy", "_sxx", "_sxy", "dev", "worst",
                 "k", "beta", "seed_fixed", "seed_slope")

    def __init__(self, *, seed_fixed: float = 0.0, seed_slope: float = 0.0,
                 seed_dev: float = 0.0, k: float = DEFAULT_K,
                 beta: float = DEFAULT_BETA) -> None:
        self.n = 0.0
        self._sx = self._sy = self._sxx = self._sxy = 0.0
        self.dev = max(0.0, seed_dev)
        self.worst = 0.0
        self.k = k
        self.beta = beta
        self.seed_fixed = max(0.0, seed_fixed)
        self.seed_slope = max(0.0, seed_slope)

    @property
    def measured(self) -> bool:
        """Has this seen anything real, or is it still answering from the seed?"""
        return self.n > 0

    def fit(self) -> tuple[float, float]:
        """(fixed, slope).

        The SLOPE is clamped non-negative: a cost that falls as the work grows
        is arithmetically reachable from noise and physically meaningless, and
        it under-predicts, which is the one direction that costs something real.

        The INTERCEPT is not clamped, and that matters. Forcing it to zero
        throws away measurements: one real sample of "800 frames cost 0.4 GB"
        against a seed slope of 3.0/800 gives an intercept of -2.6, and
        clamping that to 0 makes the prediction 3.0 again - the seed, as if
        nothing had been measured at all. Predictions are floored instead, in
        `predict`, which keeps the answer sane without discarding the evidence.
        """
        n = self.n
        if n <= 0:
            return self.seed_fixed, self.seed_slope
        mean_x = self._sx / n
        mean_y = self._sy / n
        den = n * self._sxx - self._sx * self._sx
        if n >= 2 and den > 1e-9:
            slope = (n * self._sxy - self._sx * self._sy) / den
        else:
            # Not determined: one sample, or every sample the same size. Keep
            # the DECLARED slope and let the measurements set the level.
            #
            # Reporting slope 0 here would be the claim "this cost does not
            # depend on size", which is a strong thing to conclude from data
            # that cannot see size at all - and it is the optimistic direction,
            # so a caller would believe an arbitrarily large piece of work is
            # free. The seed is a figure somebody stood behind; it stands until
            # the samples can contradict it.
            slope = self.seed_slope
        if slope < 0.0:
            slope = 0.0
        # Whatever the slope ended up being, the line passes through the centre
        # of what was actually seen. At the measured sizes the fit reproduces
        # the measurements; the slope only decides how it extrapolates away
        # from them.
        return mean_y - slope * mean_x, slope

    def observe(self, x: float, y: float) -> None:
        """Fold in one measurement. Non-positive `y` means the probe failed."""
        if y <= 0.0 or x < 0.0:
            return
        # Residual BEFORE folding the sample in, so the deviation measures how
        # wrong the model was about a point it had NOT seen. Folding first would
        # let the estimator grade its own homework.
        if self.n >= 2:
            fixed, slope = self.fit()
            self.dev = ((1 - self.beta) * self.dev
                        + self.beta * abs(y - (fixed + slope * x)))
        self.n += 1.0
        self._sx += x
        self._sy += y
        self._sxx += x * x
        self._sxy += x * y
        self.worst = max(self.worst, y)
        if self.n == 2.0:
            # The first fit worth anything. Seed the deviation at half the
            # estimate, the way RFC 6298 seeds its variance, so the margin
            # starts cautious rather than at zero.
            fixed, slope = self.fit()
            self.dev = (fixed + slope * (self._sx / self.n)) / 2.0

    def predict(self, x: float, k: float | None = None) -> float:
        """Pessimistic y for this x. `k` overrides the standing margin."""
        fixed, slope = self.fit()
        margin = self.k if k is None else k
        return max(0.0, fixed + slope * float(x) + margin * self.dev)
