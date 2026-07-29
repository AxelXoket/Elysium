"""stream_speech.py - speaking a reply while it is still being written.

WHY THIS EXISTS AT ALL
    The obvious design is the one already in the app: the Speak button sends a
    `message_id` and the backend reads the row. It cannot be reused here,
    because during a stream THERE IS NO ROW - `_insert_assistant_message` runs
    after the last delta, deliberately: an assistant row opened up front and
    then abandoned resurrects an emptied chat, which is a bug this app already
    fixed once and is not going to reintroduce for a convenience.

    Nor can the client send the text: it only ever holds the STRIPPED view. The
    delivery tags that make the voice worth hearing live in the raw text, which
    exists in exactly one place - the accumulating buffer inside the streaming
    endpoint. So the speech pipeline attaches to the stream, not to the row.
    When the stream ends and the row is finally written, the Speak button's
    existing `message_id` path takes over for replays.

WHY A THREAD
    The SSE loop is the critical path for TEXT. Synthesis takes seconds; doing
    it inline would stall the very stream the user is reading, and (because the
    event loop is shared) every other live stream with it. So the queue is
    pumped on a worker thread and the loop only ever does two O(1) things:
    hand over a delta, and collect whatever is ready.

    `feed()` therefore never blocks, and `drain()` never waits. If nothing is
    ready yet the answer is an empty list, not a pause.

FAILURE
    One failure stops the whole utterance and is reported once. Carrying on
    after a failed sentence would produce speech that is quietly missing a
    clause, with nothing on screen to explain it - the listener would conclude
    the model wrote it that way.
"""
from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Any, Callable, Mapping

from . import pacing as pacing_module
import speech_prep

from .pacing import Pacing
from .speech_queue import QueueFailed, SpeechQueue

logger = logging.getLogger(__name__)

#: How long the worker parks when there is nothing to do. Short enough that a
#: delta is picked up promptly, long enough that an idle stream is not a spin.
_IDLE_WAIT = 0.05


class StreamSpeaker:
    """Drives a SpeechQueue off-thread for the lifetime of one reply."""

    def __init__(
        self,
        synth: Callable[[str], Mapping[str, Any]],
        *,
        engine_supports_tags: bool = False,
        narrative: str = "same",
        pronunciations: Mapping[str, str] | None = None,
        preroll_seconds: float | None = None,
        lookahead: int | None = None,
        pacing: Pacing | None = None,
    ) -> None:
        kwargs: dict[str, Any] = {
            "engine_supports_tags": engine_supports_tags,
            "narrative": narrative,
            "pronunciations": pronunciations,
            # Off the synth, exactly like `uid` below and like
            # `engine_supports_tags` at SpeakHook.enable. make_stream_synth is
            # where the standing tone is read, and threading it through
            # open_speaker and SpeakHook to reach PrepOptions would be three
            # signatures carrying a value none of them has an opinion about.
            "speech_tag": getattr(synth, "speech_tag",
                                  speech_prep.DEFAULT_SPEECH_TAG),
        }
        if preroll_seconds is not None:
            kwargs["preroll_seconds"] = preroll_seconds
        if lookahead is not None:
            kwargs["lookahead"] = lookahead
        # THE fix for the dead "<3 s to first audio" promise. A StreamSpeaker
        # lives for one reply, and it used to hand SpeechQueue a brand-new
        # Pacing, so the estimator was reset before it had ever measured
        # anything - and first_chunk_window() consequently returned None on
        # every reply, on every machine, forever. The timings belong to the
        # MODEL, not to the reply, so they are fetched from the shared
        # registry keyed by the uid make_stream_synth already attaches.
        #
        # `pacing` stays overridable: tests want a deterministic bank.
        if pacing is None:
            pacing = pacing_module.for_model(getattr(synth, "uid", None))
        kwargs["pacing"] = pacing
        self._queue = SpeechQueue(synth=synth, **kwargs)

        # The queue belongs to the WORKER THREAD ALONE. Text arrives through
        # `_inbox` (a deque, whose append/popleft are atomic under the GIL) and
        # control through the two flags below, so no caller ever needs the lock
        # that synthesis runs under - see `_run` for why that matters.
        self._inbox: deque[str] = deque()
        self._closing = False
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._idle = threading.Event()
        self._out: deque[dict] = deque()
        self._stop = False
        self._closed = False
        # True while the worker is INSIDE pump(), i.e. an engine call is in
        # flight. None of the other flags cover that window: the sentence has
        # already left _inbox, the queue does not call itself finished while it
        # still has pending work, and its chunk has not reached _out yet - so
        # `finished` could answer True mid-synthesis. drain_events believed it,
        # emitted voice_done, and close() then threw the audio away: the last
        # sentence of a reply was never heard, and the wire carried the exact
        # shape of a reply that had nothing more to say.
        self._synthesising = False
        self.error: BaseException | None = None
        self._reported = False

        self._thread = threading.Thread(
            target=self._run, name="tts-stream-speaker", daemon=True)
        self._thread.start()

    # ── the streaming endpoint's side (must never block) ─────────────────────

    def feed(self, delta: str) -> None:
        if self._stop or not delta:
            return
        self._inbox.append(delta)
        self._idle.clear()
        self._wake.set()

    def finish(self) -> None:
        """No more text. Whatever is left gets spoken, including a tail with no
        terminal punctuation."""
        if self._stop:
            return
        self._closing = True
        self._idle.clear()
        self._wake.set()

    def drain(self) -> list[dict]:
        """Chunks that are ready RIGHT NOW. Never waits."""
        with self._lock:
            out = list(self._out)
            self._out.clear()
        return out

    def cancel(self) -> None:
        """Abort - the user stopped generation, or the stream died."""
        # No queue touching here: cancelling is a FLAG the worker reads, so an
        # abort never has to wait for the sentence currently being synthesised.
        self._stop = True
        # An abort means "no more text" just as certainly as finish() does, so
        # it sets the same flag. See `finished` for why that alone was not
        # enough to make a cancelled speaker report itself done.
        self._closing = True
        self._inbox.clear()
        with self._lock:
            self._out.clear()
        self._idle.set()
        self._wake.set()

    def close(self, timeout: float = 5.0) -> None:
        """Stop the worker. Idempotent, and safe to call from a failure path."""
        if self._closed:
            return
        self._closed = True
        self._stop = True
        self._wake.set()
        self._thread.join(timeout=timeout)

    # ── state the endpoint reports on ────────────────────────────────────────

    @property
    def dropped(self) -> int:
        """Sentences that had words going in and no audio coming out.

        The queue has counted these all along and its comment says why - "so
        the endpoint can say so rather than letting the audio quietly skip a
        line" - but nothing outside speech_queue.py had ever read the field.
        This is the wire it was missing.
        """
        return self._queue.dropped

    @property
    def dropped_samples(self) -> list[str]:
        return list(self._queue.dropped_samples)

    @property
    def failed(self) -> bool:
        return self.error is not None

    def take_error(self) -> BaseException | None:
        """The failure, ONCE. A second call returns None so a retry loop cannot
        emit the same error event twice."""
        if self.error is None or self._reported:
            return None
        self._reported = True
        return self.error

    @property
    def finished(self) -> bool:
        # Read without the lock ON PURPOSE: this is polled by the SSE loop while
        # the worker may be mid-synthesis, and blocking here would reintroduce
        # exactly the stall this class was restructured to remove. Both reads
        # are of a single flag/collection, and a one-poll-late answer is
        # harmless - the drain loop simply asks again.
        #
        # `_stop` short-circuits, and it has to: it is set by cancel(), close()
        # and the worker's own failure branches, and in every one of them the
        # worker has ALREADY left its loop - so it never reaches the
        # `_queue.close()` that would make `_queue.finished` true. Without this
        # line a cancelled or failed speaker answered False forever, and
        # drain_events - whose loop is "until finished, or the deadline" - had
        # no `finished` left to find: an aborted reply held its SSE body open
        # for the whole backstop with nothing to send down it. Safe against the
        # premature-done bug the comment on `_synthesising` records, because
        # none of the three setters can fire mid-synthesis on the success path.
        if self._stop:
            return True
        return (self._closing and not self._inbox
                and not self._synthesising
                and self._queue.finished and not self._out)

    def wait_idle(self, timeout: float = 5.0) -> bool:
        """Block until the worker has done everything it can.

        For tests and for the end of a stream - production code between deltas
        uses `drain()`, which never waits.
        """
        return self._idle.wait(timeout)

    # ── the worker ───────────────────────────────────────────────────────────

    def _run(self) -> None:
        while not self._stop:
            worked = False
            try:
                if self._stop:
                    break
                # Drain the inbox and pump OUTSIDE the lock. `pump()` calls the
                # engine - seconds per sentence - and holding a lock across it
                # made `feed()` and `drain()` block for exactly that long. The
                # SSE loop calls both between deltas, so the whole event loop
                # (and every other live stream on it) stalled behind synthesis:
                # the one thing the worker thread exists to prevent.
                while self._inbox:
                    self._queue.push(self._inbox.popleft())
                if self._closing:
                    self._queue.close()
                # ONE CHUNK AT A TIME, publishing between them.
                #
                # `pump()` on its own fills the lookahead before it returns, and
                # this loop only published after it returned - so the opening
                # chunk was held until the SECOND one finished, every time. It
                # is the one delay a listener experiences in full: nothing is
                # playing yet to cover it.
                #
                # Measured on the real app, from the Speak button: chunk one was
                # written at 06:27:29.041 and the first sound did not leave
                # until 06:27:32.999, when chunk two completed. 3.96 s of
                # finished audio waiting on work the listener had no need of
                # yet, inside a 10.46 s wait.
                #
                # DEFAULT_LOOKAHEAD's own comment says a deeper buffer "only
                # adds latency at the START, which is the one place the delay is
                # actually heard". The depth was never the problem - paying for
                # all of it before handing over any of it was.
                self._synthesising = True
                try:
                    # `not self._stop` is the whole reason this reads a flag
                    # rather than looping freely. Pumping one chunk at a time
                    # moved the loop that used to be OUT here - where the outer
                    # `while not self._stop` caught an abort between sentences -
                    # to in here, and the first version did not carry the check
                    # with it. A cancelled reply went on synthesising every
                    # sentence it had left, holding the engine's turn, so the
                    # NEXT press queued behind an utterance nobody was listening
                    # to any more. Observed live: one reply spoke in 5.64 s and
                    # every press after it did nothing at all.
                    while not self._stop:
                        made = self._queue.pump(limit=1)
                        # Only hand chunks over once the pre-roll is banked: the
                        # client starts playing on the first one it receives,
                        # and starting before there is a cushion is how a reply
                        # ends up stuttering between sentences.
                        if self._queue.ready():
                            while True:
                                chunk = self._queue.take()
                                if chunk is None:
                                    break
                                with self._lock:
                                    self._out.append(chunk)
                                worked = True
                        if not made:
                            break
                finally:
                    self._synthesising = False
            except QueueFailed as exc:
                logger.warning("tts stream speech failed: %s", exc.__cause__ or exc)
                self.error = exc.__cause__ or exc
                self._stop = True
                break
            except Exception as exc:                    # noqa: BLE001
                # The worker thread must not die silently: a speaker that
                # simply stopped would look identical to a reply with nothing
                # left to say.
                logger.exception("tts stream speech crashed")
                self.error = exc
                self._stop = True
                break

            if worked:
                continue
            self._idle.set()
            self._wake.wait(_IDLE_WAIT)
            self._wake.clear()
            if (self._closing and not self._inbox
                    and not self._synthesising and self._queue.finished):
                break
        self._idle.set()
