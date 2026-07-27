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
MEASURED = [(3.20, 1.96), (7.38, 4.46), (12.77, 6.55), (19.69, 9.29)]


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
        """c ~ 0.9 s and RTF ~ 0.44 are what the hardware actually did."""
        fixed, rtf = _trained()._time.fit()
        assert fixed == pytest.approx(0.9, abs=0.4)
        assert rtf == pytest.approx(0.44, abs=0.06)

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
        c and RTF a 3 second budget works out at 60-70, inside that window and
        near its centre - the window was right for a reason, not by luck."""
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
