"""stream_hook.py - the five lines completions.py is allowed to grow by.

`routers/completions.py` is already ~1800 lines and the last full audit named it
as the one real maintenance debt in the app. Attaching speech to three streaming
endpoints could easily have added a hundred lines of thread lifecycle, event
shaping and abort handling to each. All of that lives here instead; the endpoint
gets a context manager, `feed()`, `finish()` and `events()`.

The null object matters as much as the real one. Voice is off for most replies
and for every user who never enabled it, and a hook that has to be checked for
None at four call sites inside an already-dense generator is how a cleanup path
gets missed. `_Silent` costs nothing and cannot leak a thread.

ORDER OF EVENTS ON THE WIRE
    Text finishes first, audio keeps arriving behind it. `done` is emitted as
    soon as the reply is written - the user should not wait to READ a message
    because it is still being SPOKEN - and `voice_chunk` events continue after
    it until `voice_done`. A client that ignores the voice events sees exactly
    the stream it saw before this feature existed.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
from typing import Any, Callable, Iterator, Mapping

import anyio.to_thread

from .errors import ALL_CODES, TTS_SYNTHESIS_FAILED
from .stream_speech import StreamSpeaker

logger = logging.getLogger(__name__)

#: Never wait longer than this for the tail of an utterance after the text is
#: done. A worker that has wedged must not hold the HTTP response open.
DRAIN_TIMEOUT_S = 120.0

#: How long to park between polls while draining. Short enough to feel
#: immediate, long enough not to spin a core on an idle stream.
DRAIN_POLL_S = 0.05


class _Silent:
    """Voice off. Every method is a no-op, so no call site needs a guard."""

    active = False

    def feed(self, delta: str) -> None:
        pass

    def finish(self) -> None:
        pass

    def cancel(self) -> None:
        pass

    def events(self) -> list[dict]:
        return []

    def done_event(self) -> list[dict]:
        return []

    def close(self) -> None:
        pass

    def enable(self) -> bool:
        """There is no voice here to start. False is the honest answer, and it
        is what turns a pressed Speak button into a message rather than a
        button that quietly did nothing."""
        return False

    @property
    def finished(self) -> bool:
        return True


class SpeakHook:
    """Voice for one reply - possibly not yet.

    A hook can be ARMED (synthesising from the first delta, because the user
    had continuous mode on when they sent) or DORMANT: buffering the raw text
    and synthesising nothing, waiting to see whether Speak gets pressed.

    Dormant exists because of a hard constraint. During a stream there is no
    `message_id` - the assistant row is written after the last delta, on
    purpose - so the Speak button cannot use its normal path, and the client
    cannot send the text either because it only ever holds the stripped view.
    Buffering the raw deltas here is the only place that keeps the option open,
    and it costs one list of strings per live reply.

    `enable()` builds the engine and replays the buffer, so pressing Speak
    three sentences in still speaks the reply FROM THE START rather than
    joining it mid-thought.
    """

    def __init__(
        self,
        make_synth: Callable[[], Callable[[str], Mapping[str, Any]]],
        *,
        armed: bool,
        engine_supports_tags: bool = False,
        narrative: str = "same",
        pronunciations: Mapping[str, str] | Callable[[], Mapping[str, str]] | None = None,
    ) -> None:
        self._make_synth = make_synth
        self._engine_supports_tags = engine_supports_tags
        self._narrative = narrative
        self._pronunciations = pronunciations
        self._speaker: StreamSpeaker | None = None
        self._buffer: list[str] = []
        self._finished = False
        self._index = 0
        self._done_sent = False
        self._lock = threading.Lock()
        # Set by close(). Deliberately an Event, not a plain flag guarded by
        # _lock: enable() holds _lock across the whole (slow) _make_synth, so
        # close() must be able to publish "we are done" WITHOUT waiting for it.
        self._closed = threading.Event()
        # A voice that could not START is still a voice failure the listener
        # has to be told about. enable()'s False return was discarded here, and
        # a hook whose speaker never came up emitted nothing at all: events()
        # returned [] and drain_events short-circuited on active=False. So a
        # continuous-mode reply whose reference clip had been deleted arrived
        # as user_message -> delta* -> done with no voice_chunk, no voice_error
        # and no voice_done - a normal-looking reply and total silence, with
        # SpeakLiveButton hidden (continuous is on) and nothing to press.
        self._pending_error: str | None = None
        self._error_sent = False
        # enable() builds the engine OUTSIDE _lock, so "already building" is a
        # state of its own: without it two presses of Speak would each start an
        # engine and one of them would be abandoned mid-load.
        self._starting = False
        if armed:
            self.enable()

    # ── arming ───────────────────────────────────────────────────────────────

    @property
    def active(self) -> bool:
        # A queued start-up failure counts as active: drain_events returns
        # immediately on active=False, and that error still has to reach the
        # client on the paths that only drain.
        if self._speaker is None:
            return self._pending_error is not None and not self._error_sent
        return True

    def enable(self) -> bool:
        """Start speaking, replaying whatever has arrived so far.

        Returns False if the engine could not be brought up. Idempotent: a
        second press while already speaking is not an error, it is somebody
        pressing a button twice.
        """
        if self._closed.is_set():
            return False
        # _make_synth() runs OUTSIDE the lock. It resolves the model, walks the
        # models folder and reads the vault settings - hundreds of milliseconds
        # - and holding _lock across it blocked feed(): every delta of a reply
        # that pressed Speak mid-stream queued behind the engine start-up, on
        # the event loop, stalling the SSE writes for every OTHER live stream
        # too. Nothing inside needs the lock; it protects _speaker, and the
        # winner-takes-all check below is what actually decides who installs.
        with self._lock:
            if self._speaker is not None:
                return True
            if self._starting:
                # Somebody else is already building one. Report success: the
                # caller asked for speech and speech is on its way.
                return True
            self._starting = True
        try:
            try:
                synth = self._make_synth()
            except Exception as exc:                     # noqa: BLE001
                logger.warning("voice unavailable for this reply", exc_info=True)
                # Queued rather than raised: the TEXT of the reply must not be
                # lost because its audio could not start. events() delivers it.
                self._pending_error = _code_for(exc)
                return False
            supports = bool(getattr(synth, "engine_supports_tags",
                                    self._engine_supports_tags))
            speaker = StreamSpeaker(
                synth,
                engine_supports_tags=supports,
                narrative=self._narrative,
                pronunciations=self._reading_rules(),
            )
            return self._install(speaker)
        finally:
            self._starting = False

    def _reading_rules(self) -> Mapping[str, str]:
        """The user's pronunciation table, resolved as late as possible.

        Accepts a callable so the caller can hand over "how to read it" rather
        than the answer: the answer is a vault read, and a stream that merely
        ARMS a dormant speaker (most of them) must not pay for one on the
        event loop. Failure here is not allowed to cost the speech - the rules
        are an improvement to it, not a precondition.
        """
        source = self._pronunciations
        if not callable(source):
            return source or {}
        try:
            return source() or {}
        except Exception:                                # noqa: BLE001
            logger.warning("voice: reading rules unavailable", exc_info=True)
            return {}

    def _install(self, speaker: "StreamSpeaker") -> bool:
        """Adopt a freshly built speaker, replaying whatever arrived meanwhile."""
        with self._lock:
            # close() may have run during _make_synth() above - it takes
            # hundreds of milliseconds (model resolve + models-folder scan), and
            # a user who presses Speak and then Stop lands exactly there.
            # close() could not see this speaker: it did not exist yet.
            # Installing it now leaves a StreamSpeaker whose queue never reaches
            # `finished` (finish() is never called on the abort path), so its
            # daemon thread polls at 20 Hz for the LIFE OF THE PROCESS while the
            # GPU synthesises a reply nobody will hear.
            stillborn = self._closed.is_set()
            if not stillborn:
                for delta in self._buffer:
                    speaker.feed(delta)
                self._buffer.clear()
                if self._finished:
                    speaker.finish()
                self._speaker = speaker
        if stillborn:
            # Outside the lock: close() joins the worker thread, and a join
            # under _lock is the same stall this method was moved out of
            # enable() to avoid.
            speaker.cancel()
            speaker.close()
            return False
        return True

    # ── the stream's side ────────────────────────────────────────────────────

    def feed(self, delta: str) -> None:
        with self._lock:
            if self._speaker is None:
                self._buffer.append(delta)
                return
            speaker = self._speaker
        speaker.feed(delta)

    def finish(self) -> None:
        with self._lock:
            self._finished = True
            speaker = self._speaker
        if speaker is not None:
            speaker.finish()

    def cancel(self) -> None:
        with self._lock:
            self._buffer.clear()
            speaker = self._speaker
        if speaker is not None:
            speaker.cancel()

    @property
    def finished(self) -> bool:
        speaker = self._speaker
        if speaker is None:
            return self._finished
        return speaker.finished or speaker.failed

    def events(self) -> list[dict]:
        """Whatever is ready right now. Never waits."""
        speaker = self._speaker
        if speaker is None:
            # The engine never came up. Say so ONCE - silence with no
            # explanation is the one failure mode this feature is not allowed
            # to have, and it is exactly what a dropped enable() produced.
            if self._pending_error is not None and not self._error_sent:
                self._error_sent = True
                return [{"type": "voice_error", "code": self._pending_error}]
            return []
        out: list[dict] = []
        # Sibling of voice_error, and the carrier KÖK 1 was missing. The worker
        # has been emitting these all along; nothing read them, so a machine
        # without MSVC/triton spoke 2-3x slower on every load forever and said
        # nothing about it.
        for note in _host_notes():
            out.append({"type": "voice_notice", "note": note})
        for chunk in speaker.drain():
            out.append({
                "type": "voice_chunk",
                "audio_id": chunk.get("audio_id") or _stem(chunk.get("path")),
                "seconds": chunk.get("seconds"),
                "index": self._index,
            })
            self._index += 1

        err = speaker.take_error()
        if err is not None:
            # One sentence failing stops the utterance, and the client is told
            # so. Audio that simply stopped is indistinguishable from a reply
            # that had nothing more to say.
            out.append({"type": "voice_error", "code": _code_for(err)})
        return out

    def done_event(self) -> list[dict]:
        if self._done_sent or self._speaker is None:
            return []
        self._done_sent = True
        # `dropped` rides on the event that already exists rather than getting
        # a frame of its own: it is a property OF this utterance, and a client
        # that has just been told the speech is complete is exactly the client
        # that needs to know a line of it was never spoken. P4 - the audio must
        # not quietly skip a line - is only satisfiable from here.
        event: dict = {"type": "voice_done", "count": self._index}
        dropped = self._speaker.dropped
        if dropped:
            event["dropped"] = dropped
            event["dropped_samples"] = self._speaker.dropped_samples
        return [event]

    def close(self) -> None:
        # Publish the closed state FIRST, unlocked: an enable() already inside
        # _make_synth() is holding _lock and re-checks this flag before it
        # installs its speaker.
        self._closed.set()
        with self._lock:
            speaker = self._speaker
            self._speaker = None
        if speaker is not None:
            # cancel() before close(): an aborted stream must not keep
            # synthesising sentences nobody will ever hear, and close() alone
            # would wait for the current one to finish first.
            speaker.cancel()
            speaker.close()


def _stem(path: Any) -> str | None:
    if not path:
        return None
    text = str(path).replace("\\", "/").rsplit("/", 1)[-1]
    return text.rsplit(".", 1)[0] or None


def _code_for(err: BaseException) -> str:
    """The frontend already has a sentence for every tts_* code; reuse them
    rather than inventing a message the error map has never heard of.

    Membership in ALL_CODES, not a `tts_` prefix. The prefix test was the whole
    check until 2026-08-10, and it let any string an exception happened to
    carry through to the client as long as it started with four right
    characters. That made this funnel's vocabulary unbounded by construction:
    the error catalogue declares that this site draws from ALL_CODES, and with
    a prefix test that declaration was simply untrue. A code the map has never
    heard of reaches the reader as "Something went wrong. Please try again.",
    which is the one outcome the sentence above says this function exists to
    prevent.
    """
    code = getattr(err, "code", None)
    if isinstance(code, str) and code in ALL_CODES:
        return code
    return TTS_SYNTHESIS_FAILED


# ── the live registry ────────────────────────────────────────────────────────
# Speak-during-a-stream needs a way to reach a reply that has no message id
# yet. One entry per live stream, keyed by chat: a chat has at most one reply
# arriving at a time, and "the one that is streaming right now" is exactly what
# the button means when it is pressed.

_LIVE: dict[int, "SpeakHook"] = {}
_LIVE_LOCK = threading.Lock()


def register_live(chat_id: int | None, hook: Any) -> None:
    if chat_id is None or not isinstance(hook, SpeakHook):
        return
    with _LIVE_LOCK:
        # Last writer wins. A previous entry can only be a stream that failed
        # to unregister, and holding on to it would keep Speak pointed at a
        # reply that finished minutes ago.
        _LIVE[int(chat_id)] = hook


def unregister_live(chat_id: int | None, hook: Any) -> None:
    if chat_id is None:
        return
    with _LIVE_LOCK:
        if _LIVE.get(int(chat_id)) is hook:
            del _LIVE[int(chat_id)]


def enable_live(chat_id: int) -> bool:
    """Start speaking the reply currently streaming in this chat.

    False means there is nothing streaming (or the engine refused). The caller
    turns that into an honest answer rather than a silent no-op - a Speak
    button that does nothing is worse than one that says why.
    """
    with _LIVE_LOCK:
        hook = _LIVE.get(int(chat_id))
    return bool(hook and hook.enable())


def open_speaker(
    enabled: bool,
    *,
    make_synth: Callable[[], Callable[[str], Mapping[str, Any]]] | None = None,
    armable: bool = False,
    engine_supports_tags: bool = False,
    narrative: str = "same",
    pronunciations: Mapping[str, str] | Callable[[], Mapping[str, str]] | None = None,
) -> Any:
    """Build the hook for one reply. ALWAYS returns something usable.

    Three outcomes, and the middle one is the reason this signature has two
    booleans instead of one:

      enabled          - continuous mode was on when the message was sent, so
                         synthesis starts with the first delta.
      armable          - voice exists but was not asked for. The hook buffers
                         the raw text and synthesises nothing, so that Speak
                         can still be pressed mid-reply and hear it FROM THE
                         START. Costs one list of strings.
      neither          - the silent hook. Nothing is allocated and no call site
                         needs a guard.

    The reading-speed dial is bound by the CALLER into `make_synth`: the rate
    belongs to the engine set-up, and threading it through two layers would
    give this module an opinion about a number it never uses.

    Anything raised while starting the engine is logged and swallowed. Voice is
    a feature ON TOP of a reply, and no failure to speak may cost the text.

    The caller owns `close()`. `completions.py` puts it in the `finally` next to
    the abort handling it already has; anywhere else, use `speaking()` below.
    """
    if make_synth is None or not (enabled or armable):
        return _Silent()
    try:
        return SpeakHook(
            make_synth,
            armed=bool(enabled),
            engine_supports_tags=engine_supports_tags,
            narrative=narrative,
            pronunciations=pronunciations,
        )
    except Exception:                                    # noqa: BLE001
        logger.warning("voice unavailable for this reply", exc_info=True)
        return _Silent()


async def aclose(hook: Any) -> None:
    """close() for a caller running ON THE EVENT LOOP.

    close() joins the synthesis worker with a five-second timeout. Called
    straight from the SSE generator's `finally` - which is every reply, not
    just the failures - that join blocks the loop, and the thread it waits for
    is the one that may be inside a GPU call. Every other live stream stopped
    receiving deltas for the duration.

    The join has to happen (an un-joined worker keeps synthesising a reply
    nobody will hear); it just must not happen here. Silent hooks skip the
    thread hop entirely - there is nothing to wait for.
    """
    if isinstance(hook, _Silent):
        return
    try:
        await anyio.to_thread.run_sync(hook.close)
    except Exception:                                    # noqa: BLE001
        # Cleanup must not be able to replace the outcome of the stream it is
        # cleaning up after - including the GeneratorExit a disconnect re-raises.
        logger.warning("voice: speaker shutdown failed", exc_info=True)


@contextlib.contextmanager
def speaking(enabled: bool, **kwargs: Any) -> Iterator[Any]:
    """`open_speaker` with the cleanup attached, for callers that can wrap."""
    hook = open_speaker(enabled, **kwargs)
    try:
        yield hook
    finally:
        hook.close()


def _host_notes() -> list[str]:
    """Worker notes, isolated: a reporting channel must not be able to break
    the thing it reports on."""
    try:
        from tts.host import get_host
        return get_host().take_notes()
    except Exception:                            # noqa: BLE001
        logger.debug("voice: could not read worker notes", exc_info=True)
        return []


async def drain_events(hook: Any, *, timeout: float = DRAIN_TIMEOUT_S):
    """Keep the audio coming after the TEXT is already finished.

    Reading must never wait on speaking: `done` goes out as soon as the reply is
    written, and the remaining sentences arrive behind it. This is the only
    place that waits at all, and it waits by parking - not by blocking the
    thread the rest of the app shares.

    The timeout is a backstop, not a schedule. A wedged worker must not hold an
    HTTP response open forever; when it fires the client simply stops receiving
    audio for a message it has already read in full.
    """
    if not getattr(hook, "active", False):
        return
    deadline = time.monotonic() + timeout
    while True:
        for event in hook.events():
            yield event
        if hook.finished:
            break
        if time.monotonic() >= deadline:
            # A wedged worker is NOT a reply that finished speaking. Falling
            # through to voice_done alone put the two on the wire identically,
            # so a listener whose reply stopped two paragraphs early had no way
            # to tell that from a reply that had nothing more to say - the exact
            # failure SpeakHook.events() emits voice_error to avoid.
            logger.warning("voice drain timed out after %.0fs", timeout)
            yield {"type": "voice_error", "code": TTS_SYNTHESIS_FAILED}
            break
        await asyncio.sleep(DRAIN_POLL_S)
    for event in hook.done_event():
        yield event
