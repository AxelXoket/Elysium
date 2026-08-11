"""Speech that outlived its session.

The audio cache is the conversation in audible form, in the clear, beside a
database that went to the trouble of being encrypted. Two callers emptied it:
locking the vault, and shutting down. Both are graceful exits.

So a crash, a kill or a power cut left every wav on disk with nothing coming
to remove it - the 30-minute age trim runs during the NEXT synthesis, which
never happens if the user does not use voice again. Launch is the third edge,
and the only one that covers an exit nobody chose.
"""
from __future__ import annotations

import os
import pathlib

import pytest

import config
from tts.host import VoiceHost, wipe_audio_cache


@pytest.fixture()
def cache(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    directory = tmp_path / "tts" / "cache"
    directory.mkdir(parents=True)
    monkeypatch.setattr(config, "TTS_CACHE_DIR", str(directory))
    return directory


def _speak(cache: pathlib.Path, name: str) -> pathlib.Path:
    wav = cache / name
    wav.write_bytes(b"RIFF____WAVEfmt ")
    return wav


class TestLaunchClearsWhatTheLastSessionLeft:
    def test_audio_from_a_previous_session_does_not_survive(
        self, cache: pathlib.Path
    ) -> None:
        left_behind = [_speak(cache, f"speak-{i}-{i}.wav") for i in range(3)]

        removed, stuck = wipe_audio_cache()

        assert removed == 3
        assert stuck == []
        assert not any(w.exists() for w in left_behind)

    def test_an_empty_cache_is_not_an_error(self, cache: pathlib.Path) -> None:
        assert wipe_audio_cache() == (0, [])

    def test_a_cache_directory_that_was_never_created_is_not_an_error(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # First ever launch: voice has not been used, so nothing made the
        # folder. This runs before the window opens and must not raise.
        monkeypatch.setattr(config, "TTS_CACHE_DIR", str(tmp_path / "never"))
        assert wipe_audio_cache() == (0, [])

    def test_it_leaves_everything_that_is_not_generated_audio(
        self, cache: pathlib.Path
    ) -> None:
        # The same folder holds conditioning caches keyed to the voice sample,
        # which are derived from a file the user chose - not from anything
        # they said. Deleting those would cost a slow re-derivation and buy
        # no privacy.
        conditioning = cache / "cond-abc123.bin"
        conditioning.write_bytes(b"not speech")
        _speak(cache, "speak-1-1.wav")

        removed, _ = wipe_audio_cache()

        assert removed == 1
        assert conditioning.exists()


class TestTheHostAndTheLaunchPathShareOneDeletion:
    def test_the_method_still_deletes(self, cache: pathlib.Path) -> None:
        # wipe_cache moved its body out; the guarantee its callers rely on -
        # the vault lock and shutdown - has to be unchanged.
        #
        # KADEME 16a folded test_silent_failures.py's duplicate of this in.
        # Its history is worth keeping: it started life as
        # `assert callable(VoiceHost.wipe_cache)`, which stays true of a method
        # that returns None, raises, or deletes nothing at all. The count and
        # the empty directory below are what replaced that.
        _speak(cache, "speak-1-1.wav")
        _speak(cache, "speak-2-2.wav")

        assert VoiceHost().wipe_cache() == 2
        assert not list(cache.glob("*.wav"))

    @pytest.mark.skipif(os.name != "nt", reason="Windows file locking")
    def test_the_method_still_reports_what_it_could_not_delete(
        self, cache: pathlib.Path
    ) -> None:
        # /vault/lock answers with this list. If it silently emptied, the
        # route would promise a closed vault while audio stayed readable.
        stuck = _speak(cache, "speak-held.wav")
        host = VoiceHost()
        with open(stuck, "rb"):
            host.wipe_cache()
        assert host._last_wipe_left == [stuck.name]


class TestLaunchClearsAllThreeTogether:
    """The three residues are one promise, and it is kept in one place.

    main() cannot be tested - it binds a socket and opens a window - so the
    hygiene it performs lives in a function that can be. Without this, three
    covered pieces were wired together by four lines nothing exercised.
    """

    def test_one_call_clears_browser_cache_crash_dumps_and_audio(
        self, cache: pathlib.Path, tmp_path: pathlib.Path
    ) -> None:
        import run_app

        profile = tmp_path / "webview"
        chat = profile / "EBWebView" / "Default" / "Cache" / "f_000066"
        chat.parent.mkdir(parents=True)
        chat.write_bytes(b'[{"role":"user","content":"selamlar"}]')
        _speak(cache, "speak-1-1.wav")

        result = run_app.clear_session_residue(profile)

        assert not chat.exists(), "the conversation stayed in the browser cache"
        assert (profile / "EBWebView" / "Crashpad").is_file(), (
            "crash reporting was left able to dump the renderer")
        assert not list(cache.glob("*.wav")), "the audio survived"
        assert result["cached_files"] == 1
        assert result["crash_reporting_blocked"] is True
        assert result["audio_files"] == 1

    def test_a_first_ever_launch_is_not_an_error(
        self, cache: pathlib.Path, tmp_path: pathlib.Path
    ) -> None:
        import run_app

        result = run_app.clear_session_residue(tmp_path / "never-launched")

        assert result["cached_files"] == 0
        assert result["crash_reporting_blocked"] is True


class TestItSweepsEveryGeneratedWav:
    def test_a_wav_that_is_not_named_speak_still_goes(
        self, cache: pathlib.Path
    ) -> None:
        # The glob is *.wav, not speak-*.wav, and that width is deliberate:
        # anything an engine leaves in this folder as audio is the user's
        # conversation out loud. Narrowing it kept every test green, because
        # nothing here ever wrote a wav under a different name.
        odd = cache / "chunk-0007.wav"
        odd.write_bytes(b"RIFF____WAVEfmt ")

        removed, _ = wipe_audio_cache()

        assert removed == 1
        assert not odd.exists()
