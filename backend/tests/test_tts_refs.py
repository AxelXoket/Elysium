"""V3-b - the voices a model clones from.

The failure this file mostly exists to prevent is the quiet one: Fish clones
from audio AND the words in it, and with the words missing it produces a
generic voice without complaining. Someone would sit there wondering why the
clone sounds nothing like the person. So a missing transcript is refused out
loud, and the auto-filled text stays editable, because Whisper mishears.
"""
import struct
import wave
from pathlib import Path

import pytest

import config
from tts import refs


@pytest.fixture
def refs_root(monkeypatch, tmp_path):
    root = tmp_path / "voice" / "refs"
    root.mkdir(parents=True)
    monkeypatch.setattr(config, "TTS_REFS_DIR", str(root), raising=False)
    return root


def _wav_bytes(seconds=8.0, rate=44100):
    import io

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


class TestSavingAClip:
    def test_a_clip_and_its_words_are_stored_together(self, refs_root):
        voice = refs.save_upload("ayse", "ref.wav", _wav_bytes(),
                                 label="Ayse", transcript="Merhaba, ben burdayim.")
        assert voice.has_transcript
        assert voice.transcript_source == "user"
        assert (refs_root / "ayse" / "transcript.txt").is_file()

    def test_the_clip_length_is_read_back(self, refs_root):
        voice = refs.save_upload("ayse", "ref.wav", _wav_bytes(seconds=9.0))
        assert 8.5 < voice.seconds < 9.5

    def test_replacing_a_clip_leaves_only_one(self, refs_root):
        refs.save_upload("ayse", "ref.wav", _wav_bytes())
        refs.save_upload("ayse", "ref.mp3", b"fake mp3 bytes" * 100)
        audio = [p.name for p in (refs_root / "ayse").iterdir()
                 if p.suffix in refs.AUDIO_SUFFIXES]
        assert len(audio) == 1, "the old clip was left behind: %r" % audio

    def test_a_non_wav_is_accepted_but_flagged_for_conversion(self, refs_root):
        voice = refs.save_upload("ayse", "ref.mp3", b"id3 fake" * 100)
        assert voice.needs_conversion is True

    def test_listing_skips_a_folder_the_user_is_still_filling_in(self, refs_root):
        refs.save_upload("ayse", "ref.wav", _wav_bytes())
        (refs_root / "half-done").mkdir()
        names = [v.voice_id for v in refs.list_voices()]
        assert names == ["ayse"]


class TestItRefusesWhatCannotWork:
    def test_a_clip_that_is_too_short_says_how_short(self, refs_root):
        with pytest.raises(refs.RefError) as exc:
            refs.save_upload("ayse", "ref.wav", _wav_bytes(seconds=0.8))
        assert exc.value.code == "tts_reference_too_short"
        assert "seconds" in exc.value.detail

    def test_a_file_that_is_not_audio_is_refused(self, refs_root):
        with pytest.raises(refs.RefError):
            refs.save_upload("ayse", "notes.txt", b"hello")

    def test_an_empty_file_is_refused(self, refs_root):
        with pytest.raises(refs.RefError):
            refs.save_upload("ayse", "ref.wav", b"")

    def test_an_enormous_file_is_refused(self, refs_root, monkeypatch):
        monkeypatch.setattr(config, "TTS_REF_MAX_BYTES", 1000, raising=False)
        with pytest.raises(refs.RefError):
            refs.save_upload("ayse", "ref.wav", _wav_bytes())

    def test_a_voice_id_that_is_a_path_is_refused_not_sanitised(self, refs_root):
        """The id becomes a folder name. Cleaning it up would leave the guess
        of what the user meant; refusing leaves no path to smuggle."""
        for bad in ["../escape", "a/b", "C:\\evil", "..", "", "UPPER"]:
            with pytest.raises(refs.RefError):
                refs.save_upload(bad, "ref.wav", _wav_bytes())


class TestTheTranscript:
    def test_an_engine_that_needs_words_refuses_without_them(self, refs_root):
        """Without this the clone silently falls back to a generic voice, and
        nobody can tell why it sounds wrong."""
        voice = refs.save_upload("ayse", "ref.wav", _wav_bytes())
        with pytest.raises(refs.RefError) as exc:
            refs.require_transcript(voice, engine_needs_transcript=True)
        assert exc.value.code == "tts_transcript_required"

    def test_an_engine_that_does_not_need_words_is_fine_without_them(self, refs_root):
        voice = refs.save_upload("ayse", "ref.wav", _wav_bytes())
        refs.require_transcript(voice, engine_needs_transcript=False)

    def test_an_auto_transcript_is_marked_as_auto(self, refs_root):
        refs.save_upload("ayse", "ref.wav", _wav_bytes())
        voice = refs.set_transcript("ayse", "you are mine", source="auto")
        assert voice.transcript_source == "auto"

    def test_an_auto_transcript_can_be_corrected(self, refs_root):
        """Whisper heard 'your mind' where the clip said 'you're mine'. A
        transcript nobody can fix is a voice nobody can fix."""
        refs.save_upload("ayse", "ref.wav", _wav_bytes())
        refs.set_transcript("ayse", "your mind", source="auto")
        voice = refs.set_transcript("ayse", "you're mine", source="user")
        assert voice.transcript == "you're mine"
        assert voice.transcript_source == "user"

    def test_clearing_the_transcript_marks_it_absent_again(self, refs_root):
        refs.save_upload("ayse", "ref.wav", _wav_bytes(), transcript="hi")
        voice = refs.set_transcript("ayse", "   ")
        assert not voice.has_transcript and voice.transcript_source == "none"


class TestDeleting:
    def test_deleting_removes_the_folder(self, refs_root):
        refs.save_upload("ayse", "ref.wav", _wav_bytes())
        refs.delete("ayse")
        assert not (refs_root / "ayse").exists()
        assert refs.list_voices() == []

    def test_deleting_something_that_is_not_there_is_harmless(self, refs_root):
        refs.delete("ayse")

    def test_deleting_cannot_be_pointed_outside_the_refs_folder(self, refs_root):
        victim = refs_root.parent / "models"
        victim.mkdir()
        with pytest.raises(refs.RefError):
            refs.delete("../models")
        assert victim.exists()


# ── Audit HIGH: a refused upload must not have destroyed the previous clip ──


def test_a_rejected_upload_leaves_the_previous_clip_intact(refs_root):
    good = _wav_bytes(10.0)
    refs.save_upload("narrator2", "take1.wav", good, transcript="the old words")
    before = refs.describe("narrator2")
    assert before.seconds is not None and before.seconds > 9

    with pytest.raises(refs.RefError) as exc:
        refs.save_upload("narrator2", "take2.wav", _wav_bytes(1.0))
    assert exc.value.code == refs.TTS_REFERENCE_TOO_SHORT

    after = refs.describe("narrator2")
    assert after.seconds == before.seconds, "the good take must survive a refusal"
    assert after.transcript == "the old words"
    # No staging litter left behind.
    assert not any(p.name.startswith(".incoming-") for p in refs_root.iterdir())


def test_replacing_a_clip_does_not_keep_the_previous_transcript(refs_root):
    """The transcript belongs to the RECORDING. Keeping take 1's words with
    take 2's audio conditions Fish on a mismatch and reports success."""
    refs.save_upload("narrator", "take1.wav", _wav_bytes(10.0),
                     transcript="the old words")
    assert refs.describe("narrator").transcript == "the old words"

    # Re-upload with no transcript - what the UI sends for a replacement.
    refs.save_upload("narrator", "take1.wav", _wav_bytes(11.0))
    after = refs.describe("narrator")
    assert after.transcript == ""
    assert after.transcript_source == "none"
    # And the payload no longer contradicts itself.
    assert (after.transcript == "") == (after.transcript_source == "none")


def test_replacing_a_clip_with_a_new_transcript_uses_the_new_one(refs_root):
    refs.save_upload("narrator3", "a.wav", _wav_bytes(10.0), transcript="first words")
    refs.save_upload("narrator3", "b.wav", _wav_bytes(10.0), transcript="second words")
    voice = refs.describe("narrator3")
    assert voice.transcript == "second words"
    assert voice.transcript_source == "user"
