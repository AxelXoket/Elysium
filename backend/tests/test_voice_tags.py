"""V4 - delivery tags: invisible to the reader, meaningful to the voice.

The model writes `[whisper] come closer`; the person sees "come closer"; the
engine hears HOW to say it. Everything in this file guards one of the three
ways that goes wrong:

  * a bracket flashes on screen (a tag split across two SSE deltas),
  * a tag is read ALOUD ("open bracket whisper close bracket..."), or
  * legitimate bracketed text silently vanishes from the chat.

One module owns the vocabulary, the prompt, and both strippers, because the
stream stripper and the storage stripper MUST agree: the streamed text and the
stored text are shown to the same person seconds apart, and any disagreement
is a visible flicker at refresh.
"""
import pytest

import voice_tags
from voice_tags import StreamStripper, sanitize_for_tts, strip_tags


class TestStripForDisplay:
    def test_a_leading_tag_disappears_cleanly(self):
        assert strip_tags("[whisper] come closer") == "come closer"

    def test_a_mid_sentence_tag_leaves_single_spacing(self):
        got = strip_tags("I missed you... [low voice] come here.")
        assert got == "I missed you... come here."
        assert "  " not in got

    def test_multiple_tags_all_go(self):
        got = strip_tags("[seductive] I missed you. [pause] Come here. [soft] Stay.")
        assert got == "I missed you. Come here. Stay."

    def test_untagged_text_is_returned_byte_for_byte(self):
        text = "Hicbir etiket yok burada; oldugu gibi kalmali.\n\nIki paragraf."
        assert strip_tags(text) == text

    def test_a_markdown_link_is_not_eaten(self):
        """`[text](url)` is a LINK. Stripping the bracket span would leave a
        bare `(url)` and quietly break every link a model ever writes."""
        text = "Read [the guide](https://example.com) first."
        assert strip_tags(text) == text

    def test_an_unclosed_bracket_is_released_verbatim(self):
        """No closing bracket means it was never a tag. Swallowing it would
        delete the user-visible tail of the message."""
        text = "He wrote [ half a thought and stopped"
        assert strip_tags(text) == text

    def test_a_span_longer_than_a_tag_could_be_is_not_a_tag(self):
        text = "[this bracketed aside runs far past anything a delivery tag would ever reasonably be]"
        assert strip_tags(text) == text

    def test_a_span_with_a_newline_is_not_a_tag(self):
        text = "[first\nsecond] stays"
        assert strip_tags(text) == text

    def test_an_empty_pair_is_released_not_eaten(self):
        """Audit-2 predicate: too short to be a tag -> when unsure, show it."""
        assert strip_tags("well [] then") == "well [] then"

    def test_turkish_text_around_tags_survives_exactly(self):
        got = strip_tags("[soft] Seni özledim, çok fena hem de.")
        assert got == "Seni özledim, çok fena hem de."

    def test_stripping_already_clean_text_is_identity(self):
        """The migration story for old rows: strip == identity."""
        for text in ("hello", "a *narration* span", "code `[x]`(y)", ""):
            assert strip_tags(strip_tags(text)) == strip_tags(text)


class TestStreamStripper:
    def _run(self, chunks):
        s = StreamStripper()
        out = "".join(s.feed(c) for c in chunks)
        return out + s.flush()

    def test_a_tag_split_across_deltas_never_reaches_the_screen(self):
        """The flicker bug: a naive regex shows `[sedu` for a frame."""
        shown = self._run(["[sedu", "ctive] hi there"])
        assert shown == "hi there"
        assert "[" not in shown

    def test_a_tag_split_at_the_bracket_itself(self):
        assert self._run(["[", "whisper] hey"]) == "hey"

    def test_text_before_the_bracket_is_not_withheld(self):
        """Holding back MORE than the suspicious span would make streaming
        stutter - only the possible tag waits."""
        s = StreamStripper()
        assert s.feed("Hello there [whis") == "Hello there "
        assert s.feed("per] friend") == "friend"
        assert s.flush() == ""

    def test_a_false_alarm_is_released_once_it_cannot_be_a_tag(self):
        long_open = "[" + "x" * 60
        shown = self._run(["look ", long_open])
        assert shown == "look " + long_open

    def test_a_newline_releases_a_false_alarm_early(self):
        shown = self._run(["[not a tag\n", "more text"])
        assert shown == "[not a tag\nmore text"

    def test_an_unclosed_bracket_at_stream_end_is_flushed_verbatim(self):
        assert self._run(["ended with [half"]) == "ended with [half"

    def test_a_markdown_link_streams_through_intact(self):
        shown = self._run(["see [the ", "docs](http", "s://x.y) now"])
        assert shown == "see [the docs](https://x.y) now"

    def test_many_tags_across_many_chunks(self):
        chunks = ["[soft] I", "'m here. [pau", "se] Alw", "ays. [warm] Promise."]
        assert self._run(chunks) == "I'm here. Always. Promise."

    def test_stream_and_storage_stripping_agree(self):
        """The streamed text and the stored text are the same message shown to
        the same person seconds apart. Any disagreement is a visible flicker
        at the refresh after `done`."""
        raw = ("[seductive] I missed you... [low voice] come here and tell me "
               "everything. He wrote [ half. See [docs](https://x). [sigh] Fine.")
        streamed = self._run([raw[i:i + 7] for i in range(0, len(raw), 7)])
        assert streamed == strip_tags(raw)


class TestSanitizeForTts:
    def test_valid_tags_are_kept_for_a_tag_capable_engine(self):
        raw = "[whisper] stay close"
        assert sanitize_for_tts(raw, engine_supports_tags=True) == raw

    def test_all_tags_are_removed_for_an_engine_without_tag_support(self):
        """XTTS and Chatterbox would READ the brackets out loud."""
        got = sanitize_for_tts("[whisper] stay close", engine_supports_tags=False)
        assert got == "stay close"

    def test_a_malformed_span_is_dropped_rather_than_read_aloud(self):
        long_span = "[" + "y" * 80 + "]"
        got = sanitize_for_tts(long_span + " hello", engine_supports_tags=True)
        assert "y" * 80 not in got
        assert "hello" in got

    def test_a_span_with_a_newline_is_dropped_for_tts(self):
        got = sanitize_for_tts("[bad\ntag] words", engine_supports_tags=True)
        assert got.strip() == "words"

    def test_an_empty_pair_is_dropped_for_tts(self):
        assert sanitize_for_tts("[] hi", engine_supports_tags=True).strip() == "hi"

    def test_consecutive_duplicate_tags_collapse(self):
        got = sanitize_for_tts("[soft] [soft] hey", engine_supports_tags=True)
        assert got.count("[soft]") == 1

    def test_a_tag_flood_is_capped(self):
        """Spam control: the prompt asks for 1-3; past a hard cap the extras
        are noise that distorts delivery, so they are dropped - words kept."""
        words = ("soft warm fond hushed gentle husky teasing playful amused "
                 "excited rushed shocked stunned resigned angry shouting "
                 "nervous shaky urgent tired").split()
        raw = " ".join(f"[{words[i]}] word{i}" for i in range(20))
        got = sanitize_for_tts(raw, engine_supports_tags=True)
        assert got.count("[") <= voice_tags.MAX_TAGS_PER_REPLY
        for i in range(20):
            assert f"word{i}" in got

    def test_narration_spans_are_not_touched(self):
        raw = "*she leans closer* [soft] hello"
        got = sanitize_for_tts(raw, engine_supports_tags=True)
        assert "*she leans closer*" in got


class TestThePromptItself:
    def test_the_block_exists_and_is_substantial(self):
        assert len(voice_tags.VOICE_PROMPT) > 1500

    def test_it_bounds_density_per_sentence_not_per_reply(self):
        """The unit changed, and the unit is the point.

        "1-3 tags per reply" was roughly eight times stricter than the engine
        vendor's own guidance ("sentence-level emotion cues usually work best
        at the beginning of sentences", up to three combined emotions per
        sentence). It was also unenforceable: the model cannot count its own
        output reliably, and the cap in this module is what actually binds.
        A positional rule is something a model CAN follow.
        """
        prompt = voice_tags.VOICE_PROMPT.lower()
        assert "per sentence" in prompt
        assert "1-3 tags per reply" not in prompt
        assert "most sentences need none" in prompt

    def test_it_tells_the_model_the_prose_is_all_the_reader_gets(self):
        """REVERSED RULE. It used to say the tag "replaces the stage
        direction, never both" - which, since tags are stripped before
        display, instructed the model to delete words the reader would
        otherwise have read. The tag and the prose serve different audiences.
        """
        prompt = voice_tags.VOICE_PROMPT.lower()
        assert "the prose is all the reader gets" in prompt
        assert "she said seductively" not in prompt

    def test_the_examples_it_gives_would_survive_its_own_pipeline(self):
        """Every example the prompt shows the model must strip cleanly and
        sanitize cleanly - the prompt must not teach output our own pipeline
        would mangle."""
        for line in voice_tags.PROMPT_EXAMPLE_LINES:
            shown = strip_tags(line)
            assert "[" not in shown
            spoken = sanitize_for_tts(line, engine_supports_tags=True)
            assert spoken            # never empties a real line

    def test_the_declared_char_cost_matches_the_block(self):
        assert voice_tags.VOICE_PROMPT_CHARS == len(voice_tags.VOICE_PROMPT)


class TestConditionalInjection:
    def test_no_block_when_the_toggle_is_off(self, client):
        assert voice_tags.voice_block() == ""

    def test_no_block_when_no_model_is_selected(self, client, monkeypatch):
        from database import get_db

        with get_db() as con:
            con.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                ("tts_voice_enabled", "1"),
            )
        assert voice_tags.voice_block() == ""

    def test_the_block_appears_only_for_a_tag_capable_selected_engine(
        self, client, monkeypatch, tmp_path
    ):
        """Fish understands inline tags; XTTS would read them aloud. The
        prompt must follow the ENGINE, not just the toggle."""
        import config
        from database import get_db
        from tests.test_tts_core import make_fish, make_xtts

        root = tmp_path / "models"
        root.mkdir()
        monkeypatch.setattr(config, "TTS_MODELS_DIR", str(root), raising=False)
        make_fish(root)
        make_xtts(root)
        with get_db() as con:
            con.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                ("tts_voice_enabled", "1"),
            )

        body = client.get("/api/v1/tts/models").json()
        by_engine = {m["engine_id"]: m["uid"] for m in body["models"]}

        client.post("/api/v1/tts/active", json={"uid": by_engine["fish_s2"]})
        assert voice_tags.voice_block() == voice_tags.VOICE_PROMPT

        client.post("/api/v1/tts/active", json={"uid": by_engine["xtts_v2"]})
        assert voice_tags.voice_block() == ""

    def test_the_block_never_takes_a_request_down(self, client, monkeypatch):
        """Voice must never block chatting: if anything in the voice lookup
        breaks, the block is simply absent."""
        monkeypatch.setattr(voice_tags, "_active_engine_supports_tags",
                            lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        assert voice_tags.voice_block() == ""
