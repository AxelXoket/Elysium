"""Playback starts when it is SAFE to start, not after a fixed two seconds.

The rule under test, with B for banked-and-unplayed audio:

    B >= c + RTF * duration(next)      -> start
    duration(next) <= (B - c) / RTF    -> the cap once speech is running

Pure arithmetic and no clock, so every case here is exact rather than timed.
"""
import pytest

from tts.pacing import (
    FISH_S2_RTF,
    K_START,
    MIN_CHUNK_SECONDS,
    SECONDS_PER_CHAR,
    Pacing,
)

#: Measured on an RTX 5080 by verify/verify_tts_latency.py: (audio, wall).
#: Re-taken after fish_s2._codec_need stopped reserving three times what the
#: codec costs - before that, text2semantic was parked to system RAM and pulled
#: back for every sentence, and these numbers carried that round trip in them.
MEASURED = [(3.25, 2.91), (6.64, 4.46), (13.70, 7.25), (22.62, 10.90)]


def _trained(repeats=12):
    """A Pacing that has seen the real engine enough times to trust itself."""
    p = Pacing()
    for _ in range(repeats):
        for audio, wall in MEASURED:
            p.observe(chars=int(audio / SECONDS_PER_CHAR),
                      audio_seconds=audio, gen_seconds=wall)
    return p


class TestItLearnsTheRealEngine:
    def test_it_recovers_the_measured_coefficients(self):
        """Whatever MEASURED says, the fit has to say it back. Stated as a
        least-squares solve of the same four points rather than as a literal,
        because a literal here is a second copy of the data that goes stale the
        next time the engine is measured - which is exactly what happened."""
        n = len(MEASURED)
        sx = sum(a for a, _w in MEASURED)
        sy = sum(w for _a, w in MEASURED)
        sxy = sum(a * w for a, w in MEASURED)
        sxx = sum(a * a for a, _w in MEASURED)
        want_rtf = (n * sxy - sx * sy) / (n * sxx - sx * sx)
        want_fixed = (sy - want_rtf * sx) / n

        fixed, rtf = _trained()._time.fit()
        assert fixed == pytest.approx(want_fixed, abs=0.05)
        assert rtf == pytest.approx(want_rtf, abs=0.02)

    def test_before_measuring_it_answers_from_the_seed(self):
        """A cold engine still has to decide something, and the seed is a
        figure somebody stands behind rather than a zero."""
        p = Pacing()
        assert not p.measured
        assert p.gen_seconds_for(10.0, k=0.0) > 0

    def test_another_engine_can_declare_its_own_speed(self):
        """Piper is ~0.04 RTF. Nothing in the policy is Fish-specific."""
        piper = Pacing(rtf=0.04, fixed_seconds=0.1)
        fish = Pacing()
        assert (piper.gen_seconds_for(10.0, k=0.0)
                < fish.gen_seconds_for(10.0, k=0.0))


class TestWhenPlaybackMayStart:
    def test_a_bank_that_covers_the_next_chunk_starts_immediately(self):
        p = _trained()
        assert p.start_delay(banked_seconds=30.0, next_text="A short line.") == 0.0
        assert p.may_start(30.0, "A short line.") is True

    def test_a_thin_bank_waits_exactly_the_shortfall(self):
        """Not until the next chunk is finished - that is also correct and
        needlessly slower. The wait shrinks the shortfall one second per
        second, so the smallest wait that works is the shortfall itself."""
        p = _trained()
        text = "x" * 300
        need = p.gen_seconds_for_text(text, k=K_START)
        assert p.start_delay(banked_seconds=1.0, next_text=text) == pytest.approx(
            need - 1.0)

    def test_the_wait_never_goes_negative(self):
        p = _trained()
        assert p.start_delay(banked_seconds=999.0, next_text="hello") == 0.0

    def test_with_nothing_known_to_follow_it_starts(self):
        """Stalling on text that may never arrive is worse than a gap the
        crossfade can hide."""
        p = _trained()
        assert p.start_delay(banked_seconds=0.0, next_text=None) == 0.0
        assert p.start_delay(banked_seconds=0.0, next_text="") == 0.0

    def test_a_long_next_chunk_demands_a_bigger_bank(self):
        """The failure a fixed preroll cannot see: two seconds of banked audio
        is plenty before a short sentence and not nearly enough before a long
        one."""
        p = _trained()
        short = p.start_delay(2.0, "Yes.")
        long = p.start_delay(2.0, "x" * 600)
        assert short == 0.0
        assert long > 0.0


class TestTheChunkCapOnceSpeechIsRunning:
    def test_a_deep_bank_allows_a_long_chunk(self):
        p = _trained()
        assert p.max_chunk_seconds(30.0) > 30.0

    def test_a_thin_bank_shortens_the_next_chunk(self):
        p = _trained()
        assert p.max_chunk_seconds(3.0) < p.max_chunk_seconds(30.0)

    def test_the_cap_never_reaches_zero(self):
        """A queue that stops making progress is worse than the gap it was
        avoiding."""
        p = _trained()
        assert p.max_chunk_seconds(0.0) >= MIN_CHUNK_SECONDS
        assert p.max_chunk_chars(0.0) > 0

    def test_the_cap_grows_as_the_bank_does(self):
        """After two or three chunks the lead is deep enough that this stops
        binding at all - which is the intended steady state."""
        p = _trained()
        caps = [p.max_chunk_seconds(b) for b in (2.0, 5.0, 10.0, 20.0)]
        assert caps == sorted(caps)


class TestTheFirstChunkWindowIsDerivedNotChosen:
    def test_the_measured_engine_lands_on_the_agreed_window(self):
        """40-100 characters was agreed before this was measured. At the real
        c and RTF a 3 second budget works out at ~47, inside that window - the
        bounds were right for a reason, not by luck. Asserted as the range and
        not as 50, because the exact figure moves with the engine and a literal
        here would be a second copy of the measurement."""
        chars = _trained().first_chunk_chars(budget_seconds=3.0)
        assert 40 <= chars <= 100

    def test_a_faster_engine_is_allowed_a_bigger_first_chunk(self):
        """Nobody edits a constant when the engine changes."""
        piper = Pacing(rtf=0.04, fixed_seconds=0.1)
        assert (piper.first_chunk_chars(3.0)
                > _trained().first_chunk_chars(3.0))

    def test_an_engine_too_slow_for_the_budget_says_so(self):
        """Zero is a real answer: it means no first chunk, however short, can
        start inside this budget. Silently returning a few characters would
        promise something the hardware cannot do."""
        glacial = Pacing(rtf=8.0, fixed_seconds=20.0)
        assert glacial.first_chunk_chars(3.0) == 0


class TestItGetsMoreCarefulAfterAGap:
    def test_an_underrun_widens_the_margin(self):
        p = _trained()
        before = p.start_delay(1.0, "x" * 300)
        p.note_underrun()
        assert p.start_delay(1.0, "x" * 300) > before

    def test_the_caution_decays_over_clean_chunks(self):
        """A single bad moment must not cost the rest of the reply."""
        p = _trained()
        base = p.start_delay(1.0, "x" * 300)
        p.note_underrun()
        worst = p.start_delay(1.0, "x" * 300)
        for _ in range(8):
            p.note_clean_chunk()
        assert p.start_delay(1.0, "x" * 300) < worst
        assert p.start_delay(1.0, "x" * 300) == pytest.approx(base)

    def test_an_underrun_also_shortens_the_next_chunk(self):
        p = _trained()
        before = p.max_chunk_seconds(6.0)
        p.note_underrun()
        assert p.max_chunk_seconds(6.0) < before


class TestTheMarginsAreDeliberatelyAsymmetric:
    def test_starting_is_less_cautious_than_capping(self):
        """At the start every 100 ms is heard, so a smaller margin is worth one
        risked gap. Capping a chunk costs only a slightly shorter piece."""
        p = _trained()
        assert (p.gen_seconds_for(10.0, k=K_START)
                < p.gen_seconds_for(10.0, k=4.0))

    def test_a_prediction_is_never_the_bare_fit(self):
        p = _trained()
        fixed, rtf = p._time.fit()
        assert p.gen_seconds_for(10.0, k=K_START) > fixed + rtf * 10.0


class TestTheWindowOpensWhileTheSessionIsStillYoung:
    """The sub-3s start is worth nothing if it arrives on the fourth reply.

    `first_chunk_window()` needs `dev` to come down before it will answer, and
    `dev` used to be re-seeded on the SECOND sample at half the estimate - which
    at the mean audio length is about 2.9 s, four times the 0.75 s Pacing had
    declared. At beta = 0.25 that took twelve chunks to decay, so the mechanism
    the whole module exists for stayed shut for most of a session.
    """

    def _observe(self, p, n):
        for i in range(n):
            audio, wall = MEASURED[i % len(MEASURED)]
            p.observe(chars=int(audio / SECONDS_PER_CHAR),
                      audio_seconds=audio, gen_seconds=wall)

    def test_the_declared_seed_is_not_replaced_by_a_larger_guess(self):
        p = Pacing()
        declared = p._time.dev
        self._observe(p, 2)
        assert p._time.dev <= declared, (
            "the second sample overwrote a seed the caller stood behind"
        )

    def _chunks_until_it_answers(self, p):
        for n in range(1, 40):
            self._observe(p, 1)
            if p.first_chunk_window() is not None:
                return n
        return None

    def test_honouring_the_seed_opens_the_window_sooner_than_bootstrapping(self):
        """A COMPARISON, not a threshold. "under N chunks" is a number that
        drifts with the engine and passes for the wrong reason the moment the
        hardware changes; what has to hold is that keeping the declared seed
        beats replacing it, which is the mechanism itself."""
        honoured = Pacing()
        bootstrapped = Pacing()
        # Exactly what `observe` used to do to every line: forget that the
        # caller declared anything, so the second sample re-seeds from the level.
        bootstrapped._time.seed_dev = None

        fast = self._chunks_until_it_answers(honoured)
        slow = self._chunks_until_it_answers(bootstrapped)
        assert fast is not None, "the window never opened at all"
        assert slow is not None, "the comparison needs both to open"
        assert fast < slow, f"honoured {fast}, bootstrapped {slow}"

    def test_it_answers_inside_a_session_rather_than_after_one(self):
        """The absolute bound still matters - "sooner" would be satisfied by
        39 chunks against 40. A reply is about four sentences here."""
        assert self._chunks_until_it_answers(Pacing()) <= 8

    def test_a_cold_estimator_still_refuses_rather_than_guessing(self):
        """Not a regression: at c = 1.6 s with honest uncertainty, no first
        chunk fits inside 3 s. Answering anyway would promise what the hardware
        has not yet shown it can do."""
        assert Pacing().first_chunk_window() is None

    def test_the_seed_still_bootstraps_when_the_caller_declared_nothing(self):
        """The VRAM lines pass no seed_dev, and their margin comes entirely
        from this. Narrowing it there would trade an eviction for an OOM."""
        from tts.worker._fit import Line

        line = Line(seed_slope=0.004)
        line.observe(100, 0.4)
        line.observe(200, 0.8)
        assert line.dev > 0.0

    def test_a_declared_zero_is_a_declaration_and_not_a_silence(self):
        """`seed_dev=0.0` used to be indistinguishable from "unset", so a
        caller asking for no margin got the bootstrap instead - the same
        sentinel confusion that let the seed be overwritten at all."""
        from tts.worker._fit import Line

        line = Line(seed_slope=0.004, seed_dev=0.0)
        line.observe(100, 0.4)
        line.observe(200, 0.8)
        assert line.dev == 0.0
