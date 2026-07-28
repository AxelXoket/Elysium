"""tts/pacing.py - when playback may start, and how big the next piece may be.

THE QUESTION THIS REPLACES
    `SpeechQueue` used to bank a fixed 2.0 seconds of audio before letting
    playback begin. Two seconds was a guess, and a guess cannot be right for
    both a 25x-realtime engine and a 2x one - it is either needless latency or
    an audible gap, and which one depends on hardware nobody asked about.

THE RULE
    At the moment the first chunk is ready, ask one question:

        will the NEXT chunk arrive before this one finishes playing?

    Written out, with B for the seconds of audio banked and unplayed:

        B  >=  c + RTF * duration(next)

    If it holds, start now. If it does not, the shortfall IS the wait - and
    waiting shrinks it one second per second, so the delay computed here is the
    smallest one that works. Waiting for the next chunk to actually finish
    would also be correct, and needlessly slower.

    The same inequality solved for the other unknown caps the chunk size once
    playback is running, where a delay is no longer available:

        duration(next)  <=  (B - c) / RTF

WHY IT GENERALISES
    Everything above is in terms of RTF - compute time per second of audio -
    which is how every TTS engine is characterised. Fish S2 measures ~0.41 on
    this machine; XTTS-v2 is around 0.3, Piper around 0.04. Nothing here is
    specific to one of them: an engine declares a nominal figure, and the
    runtime measures the real one and overwrites it.

WHY THE COST MODEL HAS TWO TERMS
    `c + RTF * d` and not `RTF * d`. Measured, this engine spends about a
    second per call before it produces any audio at all - worker round trip,
    prompt encode, codec restore - and that fixed cost is what decides how
    short the FIRST chunk can usefully be. An engine can easily be fast per
    second of speech and slow to start; a single ratio cannot say so.

WHAT IT DOES NOT DO
    It never pauses playback that has already begun. A gap between chunks is
    bad; a stall in the middle of a sentence is worse. Once speech starts, the
    only lever is the size of what comes next.
"""
from __future__ import annotations

import threading as _threading

from tts.worker import _fit

#: Measured on an RTX 5080 by `verify/verify_tts_latency.py`, four consecutive
#: runs of the code as it stands:
#:
#:     c    1.44  1.57  1.62  1.65      median 1.60
#:     RTF  0.41  0.42  0.45  0.46      median 0.43
#:     s/ch 0.0647  0.0648  0.0656  0.0718
#:
#: An earlier table here quoted five runs and one of them also carried a
#: `_sweep()` change that was measured and reverted - a contaminated sample in
#: a set presented as controlled. These four share one build.
#:
#: `c` and RTF trade off against each other inside the fit, so a run that lands
#: high on one lands low on the other and neither is worth more precision than
#: this. For the record, before `fish_s2._codec_need` stopped reserving three
#: times what the codec costs: c = 2.292, with text2semantic parked to system
#: RAM and pulled back for EVERY sentence.
#:
#: These are SEEDS, not settings - the first few real chunks replace them, and
#: an adapter for another engine is expected to supply its own.
FISH_S2_FIXED_SECONDS = 1.6       # the cautious end of what was measured
FISH_S2_RTF = 0.46
SECONDS_PER_CHAR = 0.066          # ~15.1 characters of speech per second

#: How pessimistic each decision is, in deviations. Deliberately different.
#: At the start every 100 ms is heard, so a smaller margin is worth the risk of
#: one gap. Capping a chunk mid-stream costs nothing but a slightly shorter
#: piece, so there it can afford to be careful.
K_START = 2.0
K_CHUNK = 4.0

#: After an underrun, assume the next one is likelier than the base rate says -
#: the two-state prior every jitter-buffer design ends up with. Decays back one
#: clean chunk at a time so a single bad moment does not cost the whole reply.
UNDERRUN_PENALTY = 2.0
UNDERRUN_DECAY = 0.5

#: A chunk is never capped below this. A cap that can reach zero is a queue
#: that stops making progress, which is a worse failure than a gap.
MIN_CHUNK_SECONDS = 1.5

#: Time to first sound, the whole point of the exercise.
FIRST_CHUNK_BUDGET_SECONDS = 3.0

#: The floor is arithmetic, not taste. Chunk one has to keep playing while
#: chunk two is made, so at ~2.44x realtime a first chunk of N seconds covers a
#: second chunk of about 2.44N. Forty characters is 2.6 seconds of speech,
#: which covers an ordinary six second sentence; go much below it and the fast
#: start is paid back as a gap two seconds later.
#:
#: The ceiling keeps the budget honest. At the trained c and RTF, 100
#: characters is already 6.6 seconds of speech and 4.4 seconds of work - past
#: the point where starting early was the goal.
FIRST_CHUNK_MIN_CHARS = 40
FIRST_CHUNK_MAX_CHARS = 100


class Pacing:
    """Learns this engine's real timing and answers the two questions above.

    Deterministic and clock-free on purpose: every input is passed in, so the
    behaviour is fully testable without sleeping.
    """

    def __init__(self, *, rtf: float | None = None,
                 fixed_seconds: float | None = None,
                 seconds_per_char: float | None = None) -> None:
        rtf = FISH_S2_RTF if rtf is None else rtf
        fixed = FISH_S2_FIXED_SECONDS if fixed_seconds is None else fixed_seconds
        per_char = (SECONDS_PER_CHAR if seconds_per_char is None
                    else seconds_per_char)
        #: seconds of compute = fixed + rtf * seconds of audio
        self._time = _fit.Line(seed_fixed=fixed, seed_slope=rtf,
                               seed_dev=fixed / 2.0)
        #: seconds of audio = per_char * characters. No intercept: an empty
        #: string is zero seconds of speech, and letting the fit say otherwise
        #: would put a floor under every estimate.
        self._length = _fit.Line(seed_slope=per_char,
                                 seed_dev=per_char * 4)
        self._penalty = 0.0

    # ── learning ─────────────────────────────────────────────────────────────

    def observe(self, *, chars: int, audio_seconds: float,
                gen_seconds: float) -> None:
        """One finished chunk: how long its text was, how much speech it became,
        and how long it took to make."""
        if audio_seconds > 0:
            self._time.observe(audio_seconds, gen_seconds)
            if chars > 0:
                self._length.observe(float(chars), audio_seconds)

    def note_underrun(self) -> None:
        self._penalty = UNDERRUN_PENALTY

    def note_clean_chunk(self) -> None:
        self._penalty = max(0.0, self._penalty - UNDERRUN_DECAY)

    @property
    def measured(self) -> bool:
        return self._time.measured

    # ── predicting ───────────────────────────────────────────────────────────

    def audio_seconds_for(self, text: str) -> float:
        """How much speech this text will probably become."""
        return self._length.predict(len(text or ""), k=0.0)

    def gen_seconds_for(self, audio_seconds: float, *, k: float) -> float:
        """How long this engine will probably take to produce that speech."""
        return self._time.predict(max(0.0, audio_seconds), k=k + self._penalty)

    def gen_seconds_for_text(self, text: str, *, k: float) -> float:
        return self.gen_seconds_for(self.audio_seconds_for(text), k=k)

    # ── deciding ─────────────────────────────────────────────────────────────

    def start_delay(self, banked_seconds: float, next_text: str | None) -> float:
        """Seconds to wait before playback may begin. The smallest that works.

        With nothing known to follow the answer is zero. That is a choice: the
        alternative is stalling on text that may never arrive, and the
        scheduler's crossfade absorbs a small gap far better than the listener
        absorbs silence at the start.
        """
        if not next_text:
            return 0.0
        need = self.gen_seconds_for_text(next_text, k=K_START)
        return max(0.0, need - max(0.0, banked_seconds))

    def may_start(self, banked_seconds: float, next_text: str | None) -> bool:
        return self.start_delay(banked_seconds, next_text) <= 0.0

    def max_chunk_seconds(self, banked_seconds: float) -> float:
        """The longest next chunk that the bank can cover.

        Solved from the same inequality. The floor is not a fudge: a cap that
        reaches zero stops the queue, and a queue that stops is worse than the
        gap it was avoiding.
        """
        fixed, rtf = self._time.fit()
        margin = (K_CHUNK + self._penalty) * self._time.dev
        room = max(0.0, banked_seconds) - fixed - margin
        if rtf <= 0:
            return max(MIN_CHUNK_SECONDS, room)
        return max(MIN_CHUNK_SECONDS, room / rtf)

    def max_chunk_chars(self, banked_seconds: float) -> int:
        """The same cap, in the unit the splitter actually works in."""
        _fixed, per_char = self._length.fit()
        if per_char <= 0:
            per_char = SECONDS_PER_CHAR
        return int(self.max_chunk_seconds(banked_seconds) / per_char)

    def first_chunk_chars(self, budget_seconds: float) -> int:
        """How much text may go in the FIRST chunk and still start speaking
        inside `budget_seconds`.

        This is where the 40-100 character window comes from. It is not a
        constant anybody chose: at the trained c = 1.650 s and RTF = 0.409 a
        3 second budget leaves about 3.1 seconds of speech, which at ~15.1
        characters a second is 47 characters. On a faster engine the same call
        returns a bigger number without anyone editing it.
        """
        fixed, rtf = self._time.fit()
        margin = (K_START + self._penalty) * self._time.dev
        room = budget_seconds - fixed - margin
        if room <= 0 or rtf <= 0:
            return 0
        _f, per_char = self._length.fit()
        if per_char <= 0:
            per_char = SECONDS_PER_CHAR
        return int((room / rtf) / per_char)

    def first_chunk_window(
        self, budget_seconds: float = FIRST_CHUNK_BUDGET_SECONDS,
    ) -> tuple[int, int] | None:
        """`(min_chars, max_chars)` for the opening piece, or None.

        None means this engine cannot start inside the budget however little it
        is given - a real answer, and the caller's cue to speak whole sentences
        rather than to cut one badly for no gain.

        The bounds clamp a MEASURED figure rather than replacing it. On a slow
        engine the measurement lands under the floor and there is nothing to be
        won by cutting; on a fast one it lands over the ceiling and cutting
        further stops helping.
        """
        target = self.first_chunk_chars(budget_seconds)
        if target < FIRST_CHUNK_MIN_CHARS:
            return None
        return FIRST_CHUNK_MIN_CHARS, min(target, FIRST_CHUNK_MAX_CHARS)


# ── shared instances ─────────────────────────────────────────────────────────
# A Pacing that lives for ONE reply can never answer the question it exists to
# answer. SpeechQueue built a fresh one per utterance, so `measured` was always
# False, `first_chunk_window()` read a seeded-not-measured estimate that landed
# under FIRST_CHUNK_MIN_CHARS, and the window was None every single time.
# speech_prep.first_chunk() was therefore never called in production: the
# "<3 s to first audio" mechanism the whole module is built around had no path
# to fire, on any machine, ever.
#
# WHERE IT STANDS NOW, at the seeds above and the measurements behind them:
#
#   fresh      window None - and that is the honest answer, not a leftover of
#              the bug. At c = 1.6 s with the uncertainty a cold estimator is
#              entitled to, no first chunk fits inside 3 s.
#   ~6 chunks  the window opens; by a dozen it is (40, 47).
#
# An 86-character opening sentence, computed from the trained fit (c = 1.650,
# RTF = 0.409, 15.1 characters a second): 5.68 s of speech, first audio at
# 3.98 s if it is spoken whole, 2.92 s if the opening 47 characters are cut off
# and sent first. That gap is the entire mechanism, and keeping the timings is
# what lets it fire at all.
#
# Keyed per MODEL, because that is the thing whose speed is being learned -
# and so that swapping models does not carry the old model's timings over.
# Entries are intentionally kept across load/unload: a reload of the same
# model should not have to relearn what it already knew.
_shared: dict[str, Pacing] = {}
_shared_lock = _threading.Lock()


def for_model(key: str | None) -> Pacing:
    """The shared Pacing for this model, created on first use.

    `None` (an unidentified engine) gets its own bucket rather than a fresh
    object per call: even an anonymous engine is the same engine twice.
    """
    with _shared_lock:
        instance = _shared.get(key or "")
        if instance is None:
            instance = Pacing()
            _shared[key or ""] = instance
        return instance


def reset_shared() -> None:
    """Drop every learned timing. For tests, and for a deliberate re-measure."""
    with _shared_lock:
        _shared.clear()
