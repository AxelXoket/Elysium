"""The opening piece is cut short so speech can start, but only where a
listener would already expect a pause.

The cost of getting this wrong is asymmetric and that shapes every rule here:
the latency saved is a few seconds, once; a seam in the wrong place is in the
audio forever. So the splitter is allowed to refuse.
"""
import pytest

from speech_prep import first_chunk
from tts.pacing import (
    FIRST_CHUNK_MAX_CHARS,
    FIRST_CHUNK_MIN_CHARS,
    Pacing,
    SECONDS_PER_CHAR,
)

W = {"min_chars": 40, "max_chars": 100}


def _scannable(text: str) -> str:
    """Guard the guard: first_chunk returns None immediately for any input at
    or under max_chars, so a short fixture makes `assert out is None or ...`
    pass without the scan loop ever running.

    test_it_never_cuts_inside_a_quote was 96 characters against a 100
    character ceiling: it exercised the length check and nothing else, and the
    quote half of _breaks_a_span therefore had zero coverage in the file whose
    whole purpose is pinning what first_chunk refuses to do.
    """
    assert len(text) > W["max_chars"], (
        f"fixture is {len(text)} chars, under the {W['max_chars']} ceiling - "
        "first_chunk exits before it scans, so this test proves nothing"
    )
    return text


class TestItCutsWhereTheEarExpectsAPause:
    def test_a_sentence_end_inside_the_window_is_taken(self):
        text = ("Right, let me take a look at that for you now. It should "
                "only take a moment, so stay where you are please.")
        head, tail = first_chunk(text, **W)
        assert head.endswith("now.")
        assert tail.startswith("It should")

    def test_a_comma_will_do_when_there_is_no_sentence_end(self):
        text = ("I have seen this particular problem before and it is almost "
                "never as bad as it looks, which is worth remembering.")
        head, tail = first_chunk(text, **W)
        assert head.endswith(",")
        assert tail

    def test_a_stronger_break_beats_an_earlier_weaker_one(self):
        """A comma at 45 and a full stop at 70 - take the full stop. The seam
        is inaudible there, and three seconds is not worth a heard one."""
        text = ("The first part is here, and the second part ends about now. "
                "Then a third clause continues well past the window edge.")
        head, _tail = first_chunk(text, **W)
        assert head.endswith("now.")

    def test_the_earliest_break_wins_among_equals(self):
        """The first comma AT OR AFTER the floor - not the last one in the
        window, and not one below the floor."""
        text = ("One clause here, another clause here, a third clause here, "
                "and a fourth that runs on well past the window edge.")
        head, _ = first_chunk(text, **W)
        assert head.endswith("a third clause here,")

    def test_a_dash_is_not_beaten_by_a_later_semicolon(self):
        """Same rank, so the earlier one wins. Scanning class by class instead
        of position by position gets this backwards."""
        opening = "The opening clause runs along for a while to here"
        assert len(opening) >= W["min_chars"]      # the dash lands in the window
        text = (opening + " - then it continues; and then it keeps going "
                "well past the window edge.")
        head, _ = first_chunk(text, **W)
        assert head.endswith("-")


class TestItRefusesRatherThanCutBadly:
    def test_nothing_in_the_window_means_no_cut(self):
        """A long unpunctuated run gets spoken whole. Slower start, no seam."""
        text = "word " * 60
        assert first_chunk(text, **W) is None

    def test_a_short_text_is_left_alone(self):
        assert first_chunk("Yes, of course.", **W) is None

    def test_a_break_before_the_floor_is_not_used(self):
        """Under the floor the first chunk cannot cover the second one, so the
        fast start is paid back as a gap two seconds later."""
        text = _scannable("Yes, " + "word " * 40)
        assert first_chunk(text, **W) is None
        # The positive control. Without it this test reads "the answer was
        # None", which a splitter that had stopped splitting altogether would
        # also produce - and every other refusal test in this class would agree
        # with it. Same shape, break moved past the floor, and now it cuts.
        moved = _scannable("Yes of course, I can certainly do that for you, "
                           + "word " * 30)
        assert first_chunk(moved, **W)[0].endswith("for you,")

    def test_it_never_cuts_inside_an_emphasis_span(self):
        """Narrative tone is decided per chunk, so half a `*...*` span is read
        in the wrong voice with the asterisk still in it.

        The fixture is built so refusing is NOT one of the answers: there are
        two breaks in the window, the later one inside the span. Everywhere
        else this splitter takes the last break it can reach, so taking the
        earlier one is the span guard and nothing else. `out is None or
        balanced` used to accept both a refusal and a splitter that never cut.
        """
        text = _scannable(
            "She paused there for a moment and looked up, *her voice dropping "
            "to almost nothing, and waits.* Then quiet.")
        head, tail = first_chunk(text, **W)
        assert head.count("*") % 2 == 0
        assert head.endswith("looked up,"), head

    def test_it_never_cuts_inside_a_quote(self):
        """Same construction, and the same reason: the tempting break is the
        one inside the quotation marks, thirty characters further along."""
        text = _scannable(
            'He said this in his considered view just now, "the first half of '
            'it matters, and the second is noise." So there.')
        head, tail = first_chunk(text, **W)
        assert head.count('"') % 2 == 0
        assert head.endswith("just now,"), head

    def test_it_never_returns_an_empty_side(self):
        for text in (",,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,"
                     ",,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,",
                     "." * 120):
            out = first_chunk(_scannable(text), **W)
            assert out is None or (out[0].strip() and out[1].strip())
        # A floor on the loop above: it is a pair of `None or ...` assertions,
        # so with both cases refused it would check nothing. Measured
        # 2026-08-10: the comma run is cut, the dot run is refused.
        assert first_chunk(_scannable("," * 104), **W) is not None

    def test_the_two_halves_lose_nothing_but_whitespace(self):
        text = ("The first clause is here, and the second clause carries on "
                "for a good while after that, and then a third one arrives.")
        head, tail = first_chunk(text, **W)
        assert (head + " " + tail).split() == text.split()


class TestTheWindowIsMeasuredNotChosen:
    def _trained(self):
        p = Pacing()
        for _ in range(12):
            for audio, wall in [(3.20, 1.96), (7.38, 4.46),
                                (12.77, 6.55), (19.69, 9.29)]:
                p.observe(chars=int(audio / SECONDS_PER_CHAR),
                          audio_seconds=audio, gen_seconds=wall)
        return p

    def test_the_measured_engine_produces_the_agreed_window(self):
        lo, hi = self._trained().first_chunk_window()
        assert lo == FIRST_CHUNK_MIN_CHARS
        assert FIRST_CHUNK_MIN_CHARS < hi <= FIRST_CHUNK_MAX_CHARS

    def test_a_fast_engine_is_capped_by_the_ceiling_not_by_arithmetic(self):
        lo, hi = Pacing(rtf=0.04, fixed_seconds=0.1).first_chunk_window()
        assert (lo, hi) == (FIRST_CHUNK_MIN_CHARS, FIRST_CHUNK_MAX_CHARS)

    def test_an_engine_that_cannot_make_the_budget_says_so(self):
        """None is the cue to speak whole sentences. Cutting one badly for a
        budget that cannot be met would pay the seam and get nothing."""
        assert Pacing(rtf=8.0, fixed_seconds=20.0).first_chunk_window() is None

    def test_a_cold_engine_cuts_only_when_its_seed_says_it_can(self):
        """The seed is cautious on purpose, and the first reply of a session is
        exactly where a needless seam would be most noticed - so the answer has
        to come from the seed's arithmetic rather than from optimism.

        Pinned as the RULE. This read `Pacing() is None`, which held while the
        seeded fixed cost was 1.6 s and quietly became a false claim about the
        hardware when it dropped to 0.6."""
        assert Pacing(rtf=8.0, fixed_seconds=20.0).first_chunk_window() is None
        assert Pacing().first_chunk_window() is not None


class TestOnlyTheOpeningPieceIsCut:
    """Every seam after the first buys nothing - speech is already running."""

    def test_the_queue_cuts_once_and_then_stops_cutting(self):
        from tests.test_speech_queue import FakeClock
        from tts.speech_queue import SpeechQueue

        clock = FakeClock()

        def synth(text):
            clock.advance(max(1.0, len(text) * 0.07) / 4.0)
            return {"path": "/a.wav", "seconds": max(1.0, len(text) * 0.07)}

        q = SpeechQueue(synth=synth, now=clock)
        # The seeded window is narrow - 40 to 49 characters - so a sentence
        # only gets cut if it has a break inside it. The old fixture's comma
        # sat at 33 and its sentences were therefore never cut at all, which
        # made `len(commas) <= 1` an assertion about an empty list: it held on
        # a queue that had stopped cutting entirely, the one regression this
        # class exists to catch. Measured 2026-08-10: four pieces out, none of
        # them a cut. Here the comma is at 43, inside the window, and the three
        # LATER sentences carry the same comma at the same offset - so one cut
        # rather than four is the "only the opening piece" rule and nothing
        # else about the fixture.
        first = ("The first clause runs along right here now, and then the "
                 "second clause keeps going for a good while after that. ")
        rest = ("The next one goes along right here too, and it keeps going "
                "for a good while as well. ")
        q.push(first + rest * 3)
        q.close()
        made = []
        while True:
            q.pump()
            chunk = q.take()
            if chunk is None:
                break
            made.append(chunk["text"])
        commas = [t for t in made if t.rstrip().endswith(",")]
        assert len(commas) == 1, made
        assert commas[0] == "The first clause runs along right here now,"
