"""speech_queue.py - how a long reply starts being heard before it is written.

The engine has no streaming mode we can reach on Windows (its own SGLang server
is not supported here), and `generate_long` returns nothing until the whole
utterance is finished. Spoken naively, a five-paragraph answer is five
paragraphs of silence and then a wall of audio.

So the reply is cut into sentences and synthesised one at a time: the first
sentence starts playing while the second is still being made. The measured
production rate is ~1.6x realtime, which is the whole reason this works - once
the queue is ahead it cannot fall behind again. Getting ahead is what the
pre-roll is for; the user set it at about two seconds.

HOST HALF, and pure stdlib on purpose. `synth` is injected rather than imported
so this file has no idea an engine exists, which is what lets every timing and
failure path be tested on a machine with no GPU. The expensive DSP (time
stretching) lives worker-side where numpy is; the crossfade that hides the
seam between chunks lives in the frontend player, because two Audio elements
played back to back click no matter what the backend does.

FAILURE POLICY (the user's call, and the right one): if any sentence fails the
whole queue stops and says so. Skipping a sentence and carrying on would
produce a reply that is subtly missing a clause with nothing on screen to
explain it - the listener would blame the model.
"""
from __future__ import annotations

import time
from collections import deque
from typing import Any, Callable, Mapping

import speech_prep

from .pacing import Pacing


class QueueFailed(RuntimeError):
    """Synthesis stopped. Carries the original error as `__cause__`."""


#: How many synthesised-but-unplayed chunks to hold. Two is deliberate: at
#: 1.6x realtime a single spare chunk already covers the next one's synthesis,
#: and a deeper buffer only adds latency at the START, which is the one place
#: the delay is actually heard.
DEFAULT_LOOKAHEAD = 2

#: How many dropped-sentence excerpts to keep. The list is 80-character
#: extracts of the USER'S conversation, so it is both a memory question and a
#: privacy one - it used to grow without any bound at all, for the whole life
#: of a reply, with no reader anywhere.
MAX_DROPPED_SAMPLES = 5


def _has_words(text: str) -> bool:
    """Did this sentence contain anything a person would expect to HEAR?

    `text.strip()` was the old test, and it is true for `---`, `***` and a
    row of dots. Those prepare to nothing because they ARE nothing - a
    divider is not a deleted line. Counting them was harmless while nothing
    read the counter; wiring the counter to a user-facing notice without
    narrowing this first would have produced a warning about lost speech
    every time a model drew a horizontal rule.
    """
    return any(ch.isalnum() for ch in text)

#: There is no default pre-roll any more. Two seconds was a guess, and a guess
#: cannot be right for both a 25x-realtime engine and a 2x one - it is either
#: needless latency or an audible gap, and which one depends on hardware nobody
#: asked about. `Pacing` measures the engine and answers per chunk instead; see
#: `tts/pacing.py`. `preroll_seconds` survives as an explicit override for tests
#: that want a deterministic bank.


class SpeechQueue:
    """Text in (whole or streaming), synthesised chunks out.

    Usage is a loop: `push(delta)` as text arrives, `pump()` to do work,
    `ready()` to ask whether playback may start, `take()` to pull the next
    chunk, `close()` when the stream ends.
    """

    def __init__(
        self,
        synth: Callable[[str], Mapping[str, Any]],
        *,
        now: Callable[[], float] = time.monotonic,
        lookahead: int = DEFAULT_LOOKAHEAD,
        preroll_seconds: float | None = None,
        pacing: Pacing | None = None,
        engine_supports_tags: bool = False,
        narrative: str = "same",
        narrator_tag: str = speech_prep.DEFAULT_NARRATOR_TAG,
        pronunciations: Mapping[str, str] | None = None,
    ) -> None:
        self._synth = synth
        self._now = now
        self._lookahead = max(1, int(lookahead))
        #: Normally None, and then `pacing` decides when playback may start by
        #: measuring this engine. A number here PINS the old fixed behaviour -
        #: it exists for tests that want a deterministic bank and for anyone
        #: who deliberately wants a constant, not as the default path.
        self._preroll = (None if preroll_seconds is None
                         else max(0.0, float(preroll_seconds)))
        #: A fresh Pacing here is a MEASUREMENT RESET. That is why callers are
        #: expected to pass one that outlives the reply (StreamSpeaker fetches
        #: it from pacing.for_model): with a new instance per utterance the
        #: estimator never became `measured`, so first_chunk_window() returned
        #: None on every reply and the whole first-chunk path was dead code.
        #: The fallback stays for direct unit construction.
        self._pacing = pacing or Pacing()
        self._opts = speech_prep.PrepOptions(
            engine_supports_tags=engine_supports_tags,
            narrative=narrative,
            narrator_tag=narrator_tag,
            pronunciations=dict(pronunciations or {}),
        )

        self._buffer = ""                    # text not yet split into sentences
        self._pending: deque[str] = deque()  # split, not yet synthesised
        self._chunks: deque[dict] = deque()  # synthesised, not yet taken
        self._closed = False
        self._started = False                # playback has begun at least once
        self.cancelled = False
        #: Sentences that had words but prepared to nothing - see pump().
        self.dropped = 0
        self.dropped_samples: list[str] = []
        self.failed = False
        self._error: BaseException | None = None
        self._spoken = 0

    # ── input ────────────────────────────────────────────────────────────────

    def push(self, delta: str) -> None:
        """Add text. Safe to call with a whole reply or one SSE delta."""
        if self.cancelled or self._closed or not delta:
            return
        self._buffer += delta
        self._drain_buffer()

    def close(self) -> None:
        """No more text is coming; release whatever is left."""
        if self._closed:
            return
        self._closed = True
        self._drain_buffer(flush=True)

    def cancel(self) -> None:
        """Stop. Pending audio is dropped; already-taken chunks are the
        player's problem, not ours."""
        self.cancelled = True
        self._pending.clear()
        self._chunks.clear()
        self._buffer = ""

    def _drain_buffer(self, *, flush: bool = False) -> None:
        done, rest = speech_prep.sentences_ready(self._buffer, flush=flush)
        self._buffer = rest
        self._pending.extend(done)

    # ── work ─────────────────────────────────────────────────────────────────

    def pump(self) -> int:
        """Synthesise up to the lookahead. Returns how many chunks were made.

        Raises `QueueFailed` if the engine fails - and keeps raising, so a
        caller that swallows the first one cannot accidentally half-speak the
        rest of the reply.
        """
        if self.failed:
            raise QueueFailed("synthesis already failed") from self._error
        if self.cancelled:
            return 0

        # Two different bounds, because they answer two different questions.
        # In steady state `lookahead` caps how far ahead we run - work beyond it
        # is wasted if the user stops the playback. But BEFORE playback starts
        # that same cap can make the start condition unreachable: short
        # sentences saturate the count while still not covering the next chunk,
        # and then nothing ever becomes ready because ready() waits for audio
        # that pump() refuses to make. So until playback begins, getting to a
        # safe start wins over the lookahead.
        made = 0
        while self._pending and (
            len(self._chunks) < self._lookahead
            or (not self._started and not self._may_start())
        ):
            text = self._pending.popleft()
            # ONLY the opening piece, and only when a sentence is long enough
            # to be worth cutting. Every seam after the first buys nothing -
            # speech is already running by then - so a reply carries at most
            # one, and it lands where a reader would pause anyway.
            if self._spoken == 0 and not self._chunks:
                window = self._pacing.first_chunk_window()
                if window is not None:
                    split = speech_prep.first_chunk(
                        text, min_chars=window[0], max_chars=window[1])
                    if split is not None:
                        text, tail = split
                        self._pending.appendleft(tail)
            spoken = speech_prep.prepare(text, self._opts)
            if not spoken and _has_words(text):
                # It had words going in and none coming out. That is the text
                # pipeline DELETING something the reader can still see, not a
                # fence with nothing in it - count it so the endpoint can say
                # so rather than letting the audio quietly skip a line.
                self.dropped += 1
                if len(self.dropped_samples) < MAX_DROPPED_SAMPLES:
                    self.dropped_samples.append(text.strip()[:80])
            if not spoken:
                # A code fence or a bare divider prepares to nothing. There is
                # no audio to make and no error to report - it simply had no
                # words in it.
                continue
            started = self._now()
            try:
                result = self._synth(spoken)
            except Exception as exc:            # noqa: BLE001 - re-raised below
                self.failed = True
                self._error = exc
                raise QueueFailed("synthesis failed") from exc
            # Everything the engine returned travels, then our bookkeeping is
            # laid over it. Cherry-picking known keys here silently dropped
            # `audio_id` once - the field the client needs to actually FETCH
            # the audio - and nothing failed; the chunk simply arrived with a
            # null id. What an engine reports is not this module's to edit.
            seconds = float(result.get("seconds") or 0.0)
            synth_seconds = self._now() - started
            # Every finished chunk is a free measurement of this engine on this
            # machine right now - warm or cold, contended or idle. The policy
            # calibrates itself out of ordinary work; nothing has to be
            # benchmarked separately or configured by hand.
            self._pacing.observe(chars=len(spoken), audio_seconds=seconds,
                                 gen_seconds=synth_seconds)
            self._chunks.append({
                **dict(result),
                "text": spoken,
                "source": text,
                "seconds": seconds,
                "synth_seconds": synth_seconds,
                "index": self._spoken + len(self._chunks),
            })
            made += 1
        return made

    # ── output ───────────────────────────────────────────────────────────────

    def _next_text(self) -> str | None:
        """The text most likely to be synthesised next, for the start check.

        A split sentence if there is one, otherwise whatever is still sitting
        in the buffer of an OPEN stream - a partial sentence is a far better
        estimate of what is coming than nothing at all. On a closed stream with
        nothing pending there genuinely is no next chunk, and None says so.
        """
        if self._pending:
            return self._pending[0]
        if not self._closed and self._buffer.strip():
            return self._buffer
        return None

    def _may_start(self) -> bool:
        if not self._chunks:
            return False
        if self._closed and not self._pending and not self._buffer.strip():
            return True             # nothing can follow, so nothing can run dry
        if self._preroll is not None:
            return self.buffered_seconds() >= self._preroll
        return self._pacing.may_start(self.buffered_seconds(), self._next_text())

    def ready(self) -> bool:
        """May playback start?

        The rule is a forecast, not a fixed bank: start once the audio already
        made will still be playing when the next chunk arrives. A fixed pre-roll
        cannot answer that - two seconds is plenty before a short sentence and
        not nearly enough before a long one, and which case you are in depends
        on the engine, the machine and the text.

        Latching is deliberate: true once, true forever after. A queue that went
        un-ready mid-reply would stutter the playback it exists to smooth, and
        pausing inside a sentence sounds worse than any gap between two.
        """
        if self.cancelled or self.failed:
            return False
        if self._started:
            return True
        if self._may_start():
            self._started = True
            return True
        return False

    def take(self) -> dict | None:
        """Next chunk for the player, or None if there is nothing right now.

        Also the one place that can SEE an underrun without asking the client.
        Playback has begun, the consumer came for audio, the bank was empty and
        more was still coming: that is the gap the pacing policy exists to
        avoid, observed from the producer side. Before this, `note_underrun`
        and `note_clean_chunk` had no caller anywhere in production and
        `Pacing._penalty` sat at 0.0 for the life of the process - the
        estimator could never learn that it had been too optimistic.
        """
        if self.cancelled or self.failed:
            return None
        if not self._chunks:
            if self._started and not self.finished:
                # Idempotent (it assigns, not accumulates), which matters:
                # callers poll take() and this fires on every empty poll.
                self._pacing.note_underrun()
            return None
        self._started = True
        self._spoken += 1
        chunk = self._chunks.popleft()
        if self._chunks:
            # Handed one over with more still banked behind it: this engine is
            # keeping ahead of the player, so let the penalty decay back out.
            self._pacing.note_clean_chunk()
        return chunk

    def buffered_seconds(self) -> float:
        return sum(c["seconds"] for c in self._chunks)

    @property
    def finished(self) -> bool:
        """Everything that was ever going to be said has been handed over."""
        return (self._closed and not self._buffer
                and not self._pending and not self._chunks)
