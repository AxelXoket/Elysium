"""Silent-failure audit, 2026-07-26: six confirmed losses, 25 refuted.

One class of defect only - the app produces a degraded, truncated or empty
result and says NOTHING, so the user cannot tell it apart from success. Each
case below was found by reading the source, then survived an independent agent
whose whole job was to refute it.

The bug that started it: /speak sent a whole message in one engine call and
Fish S2 stopped at max_new_tokens, so a four-paragraph reply ended mid-sentence
with no error, no event and no log line. Measured at 37.1 s of audio against a
37.2 s budget.
"""

import os
import pathlib
import re
import time
from pathlib import Path

import pytest

import speech_prep as sp

BACKEND = Path(__file__).resolve().parent.parent


def prep(text, **kw):
    kw.setdefault("engine_supports_tags", False)
    return sp.prepare(text, sp.PrepOptions(**kw))


# ── 1. an unclosed fence must not eat the rest of the reply ────────────────


class TestUnclosedFenceIsBounded:
    """`.*\\Z` under DOTALL ran to the end of the message. The docstring above
    it promised the opposite: "dropped only from the fence to the end of that
    block, never further"."""

    def test_prose_after_a_stray_fence_still_speaks(self):
        reply = (
            "Here you go:\n\n"
            "````markdown\n"
            "```python\n"
            "x = 1\n"
            "```\n"
            "````\n\n"
            "Hope that helps!"
        )
        spoken = prep(reply)
        assert "Hope that helps" in spoken, "the tail of the reply was deleted"
        assert "x = 1" not in spoken, "the code is still not read aloud"

    def test_an_odd_number_of_fences_only_costs_its_own_block(self):
        reply = "First line.\n\n```py\nimport os\n\nAnd the rest of it."
        spoken = prep(reply)
        assert "First line." in spoken
        assert "And the rest of it." in spoken
        assert "import os" not in spoken

    def test_a_normal_closed_fence_is_unchanged(self):
        reply = "Run this:\n\n```py\nx = 1\n```\n\nThat works."
        spoken = prep(reply)
        assert "Run this:" in spoken and "That works." in spoken
        assert "x = 1" not in spoken


# ── 2. a scripted line is not a markdown reference definition ──────────────


class TestBracketLabelLinesSurvive:
    """`^[Label]: text` was erased whole. In a character-chat app that is a
    line of dialogue, and the listener heard the scene skip it."""

    @pytest.mark.parametrize("line", [
        "[Anna]: I told you not to come back.",
        "[Note]: I fixed it.",
        "[Narrator]: The door closed behind her.",
    ])
    def test_a_scripted_line_is_spoken(self, line):
        spoken = prep(f"Before.\n{line}\nAfter.")
        assert "told you not to come back" in spoken or "fixed it" in spoken \
            or "door closed" in spoken, f"deleted: {line}"

    @pytest.mark.parametrize("line", [
        "[docs]: https://example.com/a/b",
        "[home]: www.example.com",
        "[readme]: ./README.md",
        "[mail]: mailto:someone@example.com",
    ])
    def test_a_real_reference_definition_is_still_removed(self, line):
        spoken = prep(f"See the docs.\n{line}\nDone.")
        assert "example.com" not in spoken
        assert "README" not in spoken
        assert "See the docs." in spoken and "Done." in spoken


# ── 3. the tag mask must use the same word limit voice_tags does ───────────


class TestTagMaskMatchesVoiceTags:
    """voice_tags._looks_like_tag caps a delivery tag at six words. The mask
    had no limit, so bracketed PROSE was masked here and then deleted on every
    engine without inline tags - visible on screen, gone from the audio."""

    def test_long_bracketed_prose_is_spoken(self):
        spoken = prep("She paused. [i really am not sure about this] Then left.")
        assert "not sure about this" in spoken

    def test_a_real_delivery_tag_is_still_masked(self):
        dropped = prep("[low voice] come closer.", engine_supports_tags=False)
        assert "low voice" not in dropped
        kept = prep("[low voice] come closer.", engine_supports_tags=True)
        assert "[low voice]" in kept

    def test_the_two_modules_judge_the_same_bracket_the_same_way(self):
        """Two constants that happen to be equal today prove nothing about
        tomorrow. What has to hold is that ONE bracket gets one verdict from
        both sides: the module that decides what counts as a delivery tag, and
        the mask that decides what to hide before synthesis. A disagreement at
        the boundary is exactly how bracketed prose got deleted from the audio
        while staying on screen.

        Until KADEME 16a this asserted `"<= 6" in voice_tags.py`. Reflowing the
        comparison, or lifting the 6 into a named constant, broke it with the
        behaviour untouched; and moving the cap while leaving the literal
        somewhere else in the file kept it green.
        """
        import voice_tags

        six = "one two three four five six"
        seven = "one two three four five six seven"

        assert voice_tags.usable_as_tag(six) is True
        assert voice_tags.usable_as_tag(seven) is False, (
            "voice_tags widened past six words")

        # The mask follows the same line: a real tag is taken out of the
        # spoken text, prose in brackets is left in it.
        assert "five six" not in prep(f"[{six}] Come closer.")
        assert "five six seven" in prep(f"[{seven}] Come closer."), (
            "the mask deleted bracketed prose voice_tags calls prose")


# ── 4. a sentence that prepares to nothing is counted ──────────────────────


class TestDroppedSentencesAreCounted:
    """Behavioural now, not a grep.

    These two used to be `assert q.dropped == 0` on a queue that had never been
    run, plus `assert "self.dropped += 1" in source`. Between them they could
    not tell a working counter from a dead one - and while they were green
    there was an `if False:` sitting on the increment. The second one then
    broke the moment the condition was legitimately IMPROVED, which is the
    other half of the same problem: it pinned the text, not the behaviour.
    """

    @staticmethod
    def _run(text: str):
        from tts.speech_queue import SpeechQueue

        q = SpeechQueue(synth=lambda t: {"audio_id": "a", "seconds": 1.0})
        q.push(text)
        q.close()
        q.pump()
        return q

    def test_a_fresh_queue_has_counted_nothing(self):
        assert self._run("").dropped == 0

    def test_a_sentence_that_prepares_to_nothing_is_counted(self):
        """A line with words that survives none of the text pipeline is the
        audio silently skipping a line, which P4 forbids."""
        q = self._run("[Anna]: https://example.com")
        assert q.dropped == 1
        assert q.dropped_samples == ["[Anna]: https://example.com"]

    def test_a_divider_is_not_a_loss(self):
        """`---` prepares to nothing because it IS nothing. Counting it would
        warn the user about lost speech every time a model drew a rule."""
        assert self._run("---").dropped == 0

    def test_ordinary_speech_is_not_counted(self):
        assert self._run("She closed the door behind her.").dropped == 0

    def test_the_sample_list_is_bounded(self):
        """80-character extracts of the user's conversation, kept in RAM with
        no reader and no ceiling."""
        from tts.speech_queue import MAX_DROPPED_SAMPLES, SpeechQueue

        q = SpeechQueue(synth=lambda t: {"audio_id": "a", "seconds": 1.0})
        for _ in range(MAX_DROPPED_SAMPLES + 10):
            q.push("[Anna]: https://example.com. ")
        q.close()
        q.pump()
        assert q.dropped > MAX_DROPPED_SAMPLES
        assert len(q.dropped_samples) == MAX_DROPPED_SAMPLES


# ── 5. finished must not be True while a sentence is being synthesised ─────


class TestFinishedWaitsForTheEngine:
    """drain_events believed it, emitted voice_done, and close() threw the
    audio away: the last sentence of a reply was never heard, and the wire
    carried the exact shape of a reply that had nothing more to say."""

    #: All three of these read stream_speech.py as TEXT, and one pinned the
    #: line-wrapping of an `if` into a regex (audit KOK 13). A mutation that
    #: inverted the flag's polarity passed every one of them. They drive the
    #: speaker now: what matters is that a reply is not called finished while
    #: its last sentence is still inside the engine.

    @staticmethod
    def _slow_synth(gate):
        def synth(text):
            gate.wait(5.0)
            return {"audio_id": "a", "seconds": 0.5}

        synth.engine_supports_tags = False
        return synth

    def _wait_until_synthesising(self, speaker) -> bool:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if speaker._synthesising:
                return True
            time.sleep(0.01)
        return False

    def test_a_reply_is_not_finished_while_the_engine_still_has_it(self):
        import threading

        from tts.stream_speech import StreamSpeaker

        gate = threading.Event()
        speaker = StreamSpeaker(self._slow_synth(gate))
        try:
            speaker.feed("One sentence here. ")
            speaker.finish()
            # The exact window the flag exists for: the sentence has left the
            # inbox, the queue has no pending work, and no chunk has come back.
            assert self._wait_until_synthesising(speaker), "window never reached"
            assert speaker.finished is False, (
                "a reply was reported complete with its last sentence still "
                "inside the engine - drain_events would emit voice_done and "
                "close() would then throw that audio away"
            )
        finally:
            gate.set()
            speaker.close()

    def test_it_is_finished_once_the_engine_hands_the_audio_back(self):
        import threading

        from tts.stream_speech import StreamSpeaker

        gate = threading.Event()
        gate.set()
        speaker = StreamSpeaker(self._slow_synth(gate))
        try:
            speaker.feed("One sentence here. ")
            speaker.finish()
            assert speaker.wait_idle(5.0)
            assert speaker.drain(), "no audio came back at all"
            assert speaker.finished is True
        finally:
            speaker.close()

    def test_the_worker_does_not_exit_mid_synthesis(self):
        """The loop's own exit condition, driven rather than regex-matched."""
        import threading

        from tts.stream_speech import StreamSpeaker

        gate = threading.Event()
        speaker = StreamSpeaker(self._slow_synth(gate))
        try:
            speaker.feed("Only sentence. ")
            speaker.finish()
            assert self._wait_until_synthesising(speaker), "window never reached"
            assert speaker._thread.is_alive(), (
                "the worker left while a sentence was still being synthesised"
            )
        finally:
            gate.set()
            speaker.close()


# ── 6. audio we failed to delete must be named ─────────────────────────────


class TestUndeletableAudioIsReported:
    """On Windows a wav the browser is still streaming raises PermissionError.
    Skipping it silently left the user's spoken conversation in the clear while
    the vault showed locked - the opposite of what on_vault_locked promises."""

    @pytest.mark.skipif(os.name != "nt", reason="Windows file locking")
    def test_the_failure_is_logged_with_the_file_names(
        self, tmp_path, monkeypatch, caplog
    ):
        """Was a source slice, which stopped meaning anything the moment the
        body moved. This holds a wav open - which on Windows is exactly what
        the browser does while streaming it - and asks whether the app SAYS
        so. The promise is that the one file still readable gets named."""
        import logging as _logging

        import config
        from tts.host import VoiceHost

        monkeypatch.setattr(config, "TTS_CACHE_DIR", str(tmp_path / "cache"))
        cache = pathlib.Path(config.TTS_CACHE_DIR)
        cache.mkdir(parents=True)
        (cache / "speak-1-1.wav").write_bytes(b"RIFF")
        stuck = cache / "speak-held-open.wav"
        stuck.write_bytes(b"RIFF")

        host = VoiceHost()
        with open(stuck, "rb"), caplog.at_level(_logging.WARNING):
            removed = host.wipe_cache()

        assert removed == 1, "the deletable file should still have gone"
        assert stuck.exists(), "fixture failed: the file was not actually held"
        assert stuck.name in caplog.text, "the surviving file was not named"
        assert "readable on disk" in caplog.text
        assert host._last_wipe_left == [stuck.name]


# ── 7. tags can never be asked for without also being hidden ───────────────


class TestTagsAreNeverRequestedWithoutTheStripper:
    """Two gates, and their order is the whole guarantee.

    voice_block() injects the prompt that ASKS the model for delivery tags.
    StreamStripper is the layer that HIDES them from the reader. If the first
    were ever broader than the second, a reply would arrive with [low voice]
    visible in the chat.
    """

    def test_the_asking_gate_is_narrower_than_the_hiding_gate(self, client):
        import database
        import voice_tags

        # Toggle OFF: nothing is asked for, so there is nothing to hide.
        database.set_setting(voice_tags.SETTING_VOICE_ENABLED, "0")
        database.set_setting(voice_tags.SETTING_VOICE_EVER, "")
        voice_tags.reset_stripping_cache()
        assert voice_tags.voice_block() == ""

        # Toggle ON: the moment tags CAN be asked for, stripping is active.
        database.set_setting(voice_tags.SETTING_VOICE_ENABLED, "1")
        voice_tags.reset_stripping_cache()
        assert voice_tags.stripping_active() is True

    def test_enabling_voice_marks_the_sticky_flag(self, client):
        """So stripping stays on after the toggle goes off again - replies
        already written with tags must not start showing them."""
        import database
        import voice_tags

        # Through the real endpoint: it writes BOTH rows in one transaction,
        # which is what makes the flag survive a restart. Calling
        # mark_voice_ever_enabled() alone only warms the in-process cache.
        resp = client.post("/api/v1/tts/voice-mode", json={"enabled": True})
        assert resp.status_code == 200, resp.text
        assert database.get_setting(voice_tags.SETTING_VOICE_EVER) == "1"

        client.post("/api/v1/tts/voice-mode", json={"enabled": False})
        voice_tags.reset_stripping_cache()
        assert voice_tags.stripping_active() is True

    def test_the_prompt_is_backend_owned_and_not_stored_on_the_character(
        self, client, provider, monkeypatch,
    ):
        """It is a system block built at call time, never persisted.

        Until KADEME 16a this read completions.py and looked for two exact call
        expressions. Reflowing either call, renaming the local, or moving the
        injection into a helper turned it red with the behaviour untouched; and
        a build that computed the block and then never appended it kept both
        strings and stayed green.
        """
        import database
        import voice_tags
        from conftest import make_character, make_chat

        assert voice_tags.VOICE_PROMPT.strip()

        char = make_character(client, first_mes="")
        chat = make_chat(client, char)

        database.set_setting(voice_tags.SETTING_VOICE_ENABLED, "1")
        database.set_setting(voice_tags.SETTING_VOICE_EVER, "1")
        voice_tags.reset_stripping_cache()
        # The second gate voice_block() reads is whether the SELECTED engine
        # can use inline tags at all. That lookup walks the models folder and
        # has its own tests; here it is a fixture, because the subject is the
        # carrier - where the block ends up once it has been decided on.
        monkeypatch.setattr(voice_tags, "_active_engine_supports_tags",
                            lambda: True)
        resp = client.post(f"/api/v1/chats/{chat}/complete",
                           json={"message": "Say something.",
                                 "model_id": "test/model-1"})
        assert resp.status_code == 200, resp.text

        sent = provider.calls[-1]["messages"]
        carriers = [m for m in sent
                    if voice_tags.VOICE_PROMPT.strip() in str(m["content"])]
        assert carriers, "voice was on and the model was never asked for tags"
        assert all(m["role"] == "system" for m in carriers), (
            "the prompt arrived as something other than a system block")

        # Backend-owned: it is built for the call, and the character the user
        # can read and export never gains a word of it.
        stored = client.get(f"/api/v1/characters/{char}").json()
        assert voice_tags.VOICE_PROMPT.strip() not in str(stored), (
            "the prompt was written into the character record")

        # And with the toggle off, nothing asks for tags at all.
        database.set_setting(voice_tags.SETTING_VOICE_ENABLED, "0")
        database.set_setting(voice_tags.SETTING_VOICE_EVER, "")
        voice_tags.reset_stripping_cache()
        client.post(f"/api/v1/chats/{chat}/complete",
                    json={"message": "Again.", "model_id": "test/model-1"})
        assert not [m for m in provider.calls[-1]["messages"]
                    if voice_tags.VOICE_PROMPT.strip() in str(m["content"])]
