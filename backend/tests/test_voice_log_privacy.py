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

    def test_the_delete_route_does_not_name_the_voice(
            self, loud, monkeypatch):
        """A voice_id is the frontend's own uuid for anything made since the
        voice folders were hashed, but on an older install it is a slug of the
        label the user typed. The log line cannot tell the two apart, so it
        names neither.

        NARROWED. This was called `test_a_voice_that_will_not_delete_is_not_
        named_in_the_log`, which is a promise about the whole delete surface,
        and it keeps that promise by replacing `refs.delete` with a lambda -
        so the four lines inside refs.delete that DID name the voice never
        ran, and the gate that was supposed to cover them measured the one
        function that had already been fixed. The name now says what it
        measures: this route. TestRefsDeleteItself below drives the real one.
        """
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


# ── the module the route calls, driven for real ────────────────────────────

class TestRefsDeleteItself:
    """`refs.delete` and `refs.list_voices`, with nothing replaced.

    Four log lines in tts/refs.py wrote `voice_id` verbatim. Every one is a
    branch you only reach when something on disk has gone wrong, which is why
    they survived: the happy path never touches them, and the one test that
    named this surface stubbed the function out entirely.

    Each test below drives one of the four branches, and each makes three
    assertions rather than one:

      * the id is NOT in the log - the promise;
      * a record was actually emitted - the ground control, without which the
        test also passes on a build that logs nothing at all;
      * the opaque handle IS in the log - the positive control, without which
        "delete the log line" would look like a fix and the diagnosis would
        be gone.
    """

    ID = "mariel-the-harbourmaster"

    @pytest.fixture
    def refs_root(self, monkeypatch, tmp_path):
        import config
        from tts import refs

        root = tmp_path / "voice" / "refs"
        root.mkdir(parents=True)
        monkeypatch.setattr(config, "TTS_REFS_DIR", str(root), raising=False)
        # _migrated_roots is process-wide and keyed by path; a fresh tmp_path
        # is a fresh key, so nothing carries over from an earlier test.
        return root

    def _voice(self, refs_root, voice_id=None, *, audio=True):
        """A voice folder on disk, made the way the module makes them."""
        from tts import refs

        folder = refs._voice_dir(voice_id or self.ID)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "voice.json").write_text(
            '{"voice_id": "%s", "label": "Mariel"}' % (voice_id or self.ID),
            encoding="utf-8")
        if audio:
            (folder / "ref.wav").write_bytes(b"RIFF----WAVEfmt ")
        return folder

    def _handle(self, voice_id=None):
        from tts import refs
        return refs._hash_name(voice_id or self.ID)[:12]

    def _clean(self, log):
        text = log.text
        assert self.ID not in text
        assert "mariel" not in text.lower()
        assert "harbourmaster" not in text.lower()
        return text

    def test_a_redirected_voice_folder_is_named_by_its_handle(
            self, loud, refs_root, monkeypatch):
        import secure_delete
        from tts import refs

        self._voice(refs_root)
        # The real branch needs a junction. Rather than ask the test runner
        # for the privilege to create one, the DETECTOR is replaced and the
        # leaking function is not: refs.delete runs exactly as shipped.
        monkeypatch.setattr(secure_delete, "is_redirected", lambda p: True)

        assert refs.delete(self.ID) is False
        text = self._clean(loud)
        assert "is a redirected name" in text          # ground control
        assert self._handle() in text                  # positive control

    def test_a_file_that_will_not_shred_is_named_by_its_handle(
            self, loud, refs_root, monkeypatch):
        import secure_delete
        from tts import refs

        self._voice(refs_root)
        monkeypatch.setattr(secure_delete, "is_redirected", lambda p: False)
        monkeypatch.setattr(secure_delete, "shred_tree",
                            lambda folder: (0, ["ref.wav"], []))

        refs.delete(self.ID)
        text = self._clean(loud)
        assert "file(s) could not be removed" in text  # ground control
        assert self._handle() in text                  # positive control

    def test_a_pruned_subfolder_is_named_by_its_handle(
            self, loud, refs_root, monkeypatch):
        import secure_delete
        from tts import refs

        self._voice(refs_root)
        monkeypatch.setattr(secure_delete, "is_redirected", lambda p: False)
        monkeypatch.setattr(secure_delete, "shred_tree",
                            lambda folder: (0, [], ["inner"]))

        assert refs.delete(self.ID) is False
        text = self._clean(loud)
        assert "contains a redirected folder" in text  # ground control
        assert self._handle() in text                  # positive control

    def test_an_unusable_voice_folder_is_named_by_its_handle(
            self, loud, refs_root):
        """The fourth line, and the only one on a read path.

        Nothing is replaced here at all: a folder with a recorded id and no
        audio in it is exactly what `describe` refuses, which is the branch
        list_voices logs from.
        """
        from tts import refs

        self._voice(refs_root, audio=False)

        assert refs.list_voices() == []
        text = self._clean(loud)
        assert "skipping unusable voice folder" in text   # ground control
        assert self._handle() in text                     # positive control

    def test_the_handle_survives_a_refs_dir_that_cannot_be_read(
            self, loud, refs_root, monkeypatch):
        """_handle never raises: a log call is not allowed to be the thing
        that breaks a delete."""
        from tts import refs

        monkeypatch.setattr(refs, "_hash_name",
                            lambda vid: (_ for _ in ()).throw(OSError("no")))
        assert refs._handle(self.ID) == "unresolved"
        assert self.ID not in loud.text

