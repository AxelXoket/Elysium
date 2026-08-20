"""The voice path must not write a reply, or a voice's name, into elysium.log.

elysium.log is plaintext, sits beside the vault, is written by the packaged
exe, and survives every lock. A numeric id in there is fine and useful. The
words a model wrote are not, and neither is a name a person reads on screen.

These are the BEHAVIOURAL half of that promise: they run the real speaker
thread, spawn the real worker subprocess over real pipes, and call the real
endpoint, then read what came out of the logger. `test_log_identifier_privacy`
is the other half, and it reads source text - it can see shapes nobody has
executed yet, and it cannot see a single actual log line. Neither is
sufficient on its own.

WHY THE SECRET IS A SENTENCE AND NOT A TOKEN

Every assertion below looks for a phrase that reads like something a model
would write. A test that searched for "SECRET123" would pass against a
sanitizer that only stripped uppercase, and would say nothing about the
failure mode that actually happened here: a whole sentence riding out inside
an exception's message.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

from tts import worker_client
from tts.errors import TTS_SYNTHESIS_FAILED, TTS_WORKER_FAILED
from tts.stream_speech import StreamSpeaker
from tts.worker import _wire
from tts.worker_client import (
    WORKER_FAULT_UNCLASSIFIED,
    WorkerClient,
    WorkerFailure,
    describe_unknown_code,
    sanitize_worker_detail,
)

#: A line a character might actually say, with a name in it.
SECRET = "Mariel leaned in and whispered that the harbour was already burning"

FAKE = str(Path(__file__).resolve().parent / "fake_worker.py")


@pytest.fixture
def loud(caplog):
    """Capture everything, at every level. A leak at DEBUG is still a leak:
    elysium.log's level is a setting, and privacy that depends on a setting is
    not privacy."""
    caplog.set_level(logging.DEBUG)
    return caplog


# ── the streaming speaker ────────────────────────────────────────────────────

class TestTheSpeakerThread:
    def test_a_failed_sentence_is_not_repeated_in_the_log(self, loud):
        """The leak, exactly: QueueFailed carries the engine's exception as
        `__cause__`, and an engine builds the text it was handed into its own
        message."""
        def boom(text):
            raise RuntimeError(f"cannot encode {text!r}")

        speaker = StreamSpeaker(boom, preroll_seconds=0.0)
        try:
            speaker.feed(SECRET + ".")
            speaker.finish()
            assert speaker.wait_idle(5.0)
            assert speaker.failed, "the fixture must actually have failed"
        finally:
            speaker.close()

        assert "harbour was already burning" not in loud.text
        assert "Mariel" not in loud.text

    def test_the_log_still_says_which_subsystem_and_what_kind_of_fault(
            self, loud):
        def boom(text):
            raise RuntimeError(f"cannot encode {text!r}")

        speaker = StreamSpeaker(boom, preroll_seconds=0.0)
        try:
            speaker.feed(SECRET + ".")
            speaker.finish()
            assert speaker.wait_idle(5.0)
        finally:
            speaker.close()

        assert "tts stream speech failed" in loud.text
        assert "fault=RuntimeError" in loud.text

    def test_the_whole_failure_is_still_available_to_the_caller(self):
        """What was removed from the LOG is not removed from the program. The
        exception is kept in memory, where stream_hook turns it into the code
        the client is shown - so the user-visible behaviour is unchanged and
        an attached debugger still sees everything."""
        def boom(text):
            raise RuntimeError(f"cannot encode {text!r}")

        speaker = StreamSpeaker(boom, preroll_seconds=0.0)
        try:
            speaker.feed(SECRET + ".")
            speaker.finish()
            assert speaker.wait_idle(5.0)
            err = speaker.take_error()
            assert isinstance(err, RuntimeError)
            assert "harbour was already burning" in str(err)
        finally:
            speaker.close()

    def test_an_unexpected_crash_names_its_class_without_its_message(
            self, loud, monkeypatch):
        """The other handler: anything the queue raises that is NOT a
        QueueFailed. speech_prep runs on the reply outside the queue's own
        try, so a failure in preparation arrives here holding the text."""
        import tts.stream_speech as stream_speech

        def explode(self, limit=None):
            raise ValueError(f"bad markup in {SECRET!r}")

        monkeypatch.setattr(stream_speech.SpeechQueue, "pump", explode)
        speaker = StreamSpeaker(lambda text: {}, preroll_seconds=0.0)
        try:
            speaker.feed(SECRET + ".")
            speaker.finish()
            assert speaker.wait_idle(5.0)
            assert speaker.failed
        finally:
            speaker.close()

        assert "harbour was already burning" not in loud.text
        assert "tts stream speech crashed" in loud.text
        assert "fault=ValueError" in loud.text


# ── the sanitizer at the process boundary ────────────────────────────────────

class TestSanitizingWhatAWorkerSays:
    def test_a_sentence_becomes_a_fault_class(self):
        out = sanitize_worker_detail(
            f"encode: TokenizerError: cannot encode {SECRET!r}")
        assert out == "worker fault: TokenizerError"

    def test_text_with_no_fault_class_in_it_becomes_one_fixed_string(self):
        assert sanitize_worker_detail(SECRET) == WORKER_FAULT_UNCLASSIFIED

    def test_nothing_at_all_is_still_a_readable_answer(self):
        for empty in (None, "", 17, {"detail": "x"}):
            assert sanitize_worker_detail(empty) == WORKER_FAULT_UNCLASSIFIED

    def test_a_fault_class_far_inside_a_long_reply_is_not_picked_up(self):
        """The class name is at the FRONT in both worker formats. A word deep
        inside prose that happens to end in Error is prose."""
        buried = " ".join(["the"] * 40) + " ImportError"
        assert sanitize_worker_detail(buried) == WORKER_FAULT_UNCLASSIFIED

    def test_the_first_fault_class_wins_and_nothing_around_it_travels(self):
        out = sanitize_worker_detail(
            f"synthesis: OutOfMemoryError: while saying {SECRET}")
        assert out == "worker fault: OutOfMemoryError"
        assert "Mariel" not in out

    def test_a_contract_shaped_code_is_readable_and_prose_is_not(self):
        assert describe_unknown_code("engine_specific_gibberish") == (
            "engine_specific_gibberish")
        assert describe_unknown_code(f"Cannot speak: {SECRET}") == (
            "non-conforming")
        assert describe_unknown_code(None) == "non-conforming"


# ── the real subprocess ──────────────────────────────────────────────────────

@pytest.fixture
def worker():
    client = WorkerClient(sys.executable, FAKE, engine_id="fake")
    client.start(timeout=30)
    yield client
    client.close(grace=0.2)


class TestOverRealPipes:
    def test_a_worker_error_quoting_the_reply_arrives_sanitized(
            self, worker, tmp_path):
        with pytest.raises(WorkerFailure) as raised:
            worker.request(_wire.OP_SYNTHESIZE, {
                "text": SECRET,
                "out": str(tmp_path / "a.wav"),
                "values": {"__fake_mode": "echo_text"},
            })
        exc = raised.value
        assert exc.code == TTS_SYNTHESIS_FAILED
        assert exc.detail == "worker fault: TokenizerError"
        assert exc.reason == exc.detail
        assert "Mariel" not in exc.detail
        assert "harbour" not in exc.detail

    def test_a_non_conforming_code_is_neither_trusted_nor_repeated(
            self, worker, tmp_path, loud):
        with pytest.raises(WorkerFailure) as raised:
            worker.request(_wire.OP_SYNTHESIZE, {
                "text": SECRET,
                "out": str(tmp_path / "b.wav"),
                "values": {"__fake_mode": "shouty_code"},
            })
        # Coerced to something the frontend can actually say out loud.
        assert raised.value.code == TTS_WORKER_FAILED
        assert "non-conforming" in loud.text
        assert "Mariel" not in loud.text
        assert "harbour" not in loud.text

    def test_a_working_worker_is_untouched_by_any_of_this(self, worker, tmp_path):
        out = str(tmp_path / "ok.wav")
        res = worker.request(_wire.OP_SYNTHESIZE, {"text": SECRET, "out": out})
        assert res["path"] == out
        assert res["text_len"] == len(SECRET)


# ── the router that logs it ──────────────────────────────────────────────────

class TestTheRouterLogLine:
    def test_the_worker_text_does_not_reach_the_log_through_the_router(
            self, loud):
        """The whole chain the leak travelled: a worker's error frame, into a
        WorkerFailure, into `_fail`'s log line."""
        from routers import tts_runtime

        frame = {
            "ok": False,
            "code": TTS_SYNTHESIS_FAILED,
            "detail": f"encode: TokenizerError: cannot encode {SECRET!r}",
        }
        exc = worker_client._failure_from(frame)
        with pytest.raises(HTTPException) as raised:
            tts_runtime._fail(exc)

        # The client still gets the contract code, at the documented status.
        assert raised.value.detail == TTS_SYNTHESIS_FAILED
        assert raised.value.status_code == 500
        # The log still says what failed and how.
        assert TTS_SYNTHESIS_FAILED in loud.text
        assert "worker fault: TokenizerError" in loud.text
        # And nothing of what was being said.
        assert "Mariel" not in loud.text
        assert "harbour" not in loud.text

    def test_a_voice_that_will_not_delete_is_not_named_in_the_log(
            self, loud, monkeypatch):
        """A voice_id is the frontend's own uuid for anything made since the
        voice folders were hashed, but on an older install it is a slug of the
        label the user typed. The log line cannot tell the two apart, so it
        names neither."""
        from routers import tts_runtime

        monkeypatch.setattr(tts_runtime.refs, "delete", lambda vid: False)
        body = tts_runtime.delete_voice("mariel-the-harbourmaster")

        # The ANSWER still carries the id: the client asked about that voice
        # and has it on screen already.
        assert body == {"voice_id": "mariel-the-harbourmaster",
                        "removed": False}
        # The log says a voice would not go, which is the diagnosable fact.
        assert "could not be removed" in loud.text
        assert "mariel" not in loud.text.lower()
