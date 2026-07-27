"""A long reply must not stop at the engine's own token cap.

/speak sent the WHOLE message in one engine call, and Fish S2's
`max_new_tokens` defaults to 800 semantic tokens - about 37 seconds of speech.
Pressing Speak on a four-paragraph reply produced a wav that stopped near the
end of the second paragraph, and nothing reported it: the audio simply ended,
indistinguishable from a reply that had finished. Measured on the real app at
37.1 s of audio against a 37.2 s budget - a match to a tenth of a second.

The live streaming path never had this problem because it splits into
sentences. make_stream_synth's own docstring already requires the two to
agree - "deliberately the SAME path /speak takes ... Two code paths would
drift, and the drift would be audible" - and only this one did not split.
"""

import wave
from pathlib import Path

import routers.tts_runtime as runtime


class RecordingHost:
    def __init__(self, seconds: float = 1.0):
        self.calls: list[str] = []
        self.seconds = seconds

    def speak(self, text, values, extra=None):
        self.calls.append(text)
        return {"path": "", "seconds": self.seconds, "sample_rate": 44100}


def _wav(path: Path, frames: int, rate: int = 44100) -> str:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames * 2))
    return str(path)


class TestSpeakSplitsLikeTheLivePath:
    def test_a_multi_sentence_message_is_synthesised_sentence_by_sentence(self):
        host = RecordingHost()
        runtime._speak_in_sentences(
            host, "First one. Second one. Third one.", {}, None,
        )
        assert len(host.calls) == 3, "the whole message went in one engine call"

    def test_a_single_sentence_still_takes_one_call(self):
        host = RecordingHost()
        runtime._speak_in_sentences(host, "Just the one.", {}, None)
        assert host.calls == ["Just the one."]

    def test_the_reported_length_is_the_whole_message(self, tmp_path):
        # Real files, so the parts actually join - with unjoinable paths the
        # caller falls back to the first part, which is the OLD behaviour and
        # is asserted separately below.
        class WritingHost:
            def __init__(self):
                self.n = 0

            def speak(self, text, values, extra=None):
                self.n += 1
                path = _wav(tmp_path / f"part{self.n}.wav", 44100)
                return {"path": path, "seconds": 2.5, "sample_rate": 44100}

        out = runtime._speak_in_sentences(
            WritingHost(), "One. Two. Three.", {}, None,
        )
        assert out["seconds"] == 7.5
        with wave.open(out["path"], "rb") as w:
            assert w.getnframes() == 3 * 44100

    def test_an_unjoinable_result_still_returns_a_real_answer(self):
        """The first part alone is what this endpoint returned before."""
        host = RecordingHost(seconds=2.5)
        out = runtime._speak_in_sentences(host, "One. Two. Three.", {}, None)
        assert out["seconds"] == 2.5


class TestJoiningTheParts:
    def test_one_file_of_the_summed_length(self, tmp_path):
        parts = [
            _wav(tmp_path / "a.wav", 1000),
            _wav(tmp_path / "b.wav", 2000),
            _wav(tmp_path / "c.wav", 500),
        ]
        joined = runtime._join_wavs(parts)
        assert joined is not None
        with wave.open(joined, "rb") as w:
            assert w.getnframes() == 3500
            assert w.getframerate() == 44100
            assert w.getnchannels() == 1
        # The parts are spent - they must not pile up in the audio cache.
        for part in parts:
            assert not Path(part).exists()

    def test_mismatched_formats_refuse_to_join(self, tmp_path):
        """A joined file that quietly changed format is worse than not
        joining: the caller falls back to the first part, which is what this
        endpoint returned before."""
        parts = [
            _wav(tmp_path / "a.wav", 100, rate=44100),
            _wav(tmp_path / "b.wav", 100, rate=22050),
        ]
        assert runtime._join_wavs(parts) is None

    def test_fewer_than_two_parts_is_not_a_join(self, tmp_path):
        assert runtime._join_wavs([]) is None
        assert runtime._join_wavs(["nope.wav"]) is None
        assert runtime._join_wavs([_wav(tmp_path / "only.wav", 10)]) is None

    def test_a_failed_join_leaves_no_half_written_file(self, tmp_path):
        parts = [
            _wav(tmp_path / "a.wav", 100, rate=44100),
            _wav(tmp_path / "b.wav", 100, rate=22050),
        ]
        runtime._join_wavs(parts)
        assert not (tmp_path / "a-joined.wav").exists()


class TestTheEngineSaysWhenItRanOutOfBudget:
    """Reaching max_new_tokens means the speech was CUT - the model was still
    talking. Nothing reported it: no error, no event, no log line, and the
    audio ended exactly like a reply that had finished."""

    def _source(self):
        return (Path(__file__).resolve().parent.parent
                / "tts" / "worker" / "fish_s2.py").read_text(encoding="utf-8")

    def test_the_worker_compares_what_it_produced_against_the_budget(self):
        source = self._source()
        assert "length_capped" in source, "hitting the cap is still silent"
        assert "produced >= int(max_new) - 1" in source

    #: The event ITSELF, not a comment that happens to name it. The eviction
    #: comment above `_free_for_codec` quotes "length_capped" while explaining
    #: the OOM it guards against, and a bare substring search finds that first.
    _REPORT = '_progress(send, "length_capped"'

    def test_the_report_says_what_to_do_about_it(self):
        source = self._source()
        block = source[source.index(self._REPORT):]
        block = block[:400]
        assert "cut short" in block
        assert "Max length" in block or "smaller pieces" in block

    def test_it_is_checked_before_the_audio_is_decoded(self):
        """So the report travels with the same request, not after it."""
        source = self._source()
        assert source.index(self._REPORT) < source.index('_progress(send, "decoding"')
