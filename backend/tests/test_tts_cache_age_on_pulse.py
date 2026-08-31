"""U-54 - the retention window only applied while somebody kept talking.

`_trim_cache` had exactly one trigger: `_next_out_path`, which runs per
synthesised sentence. Stop speaking and nothing enforced
`TTS_CACHE_MAX_AGE_S` until the vault locked - so "audio older than the
window is gone" was true only for people who never paused.

This cache is the conversation in audible form, in the clear, beside a
database that went to the trouble of being encrypted.

THE ENCRYPTION HALF IS NOT TOUCHED. Whether the cache should be encrypted at
all is an open owner decision (ESK-6); this file measures the age window and
nothing else.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

import config
from tts import host as tts_host


@pytest.fixture
def cache(tmp_path, monkeypatch) -> Path:
    d = tmp_path / "audio-cache"
    d.mkdir()
    monkeypatch.setattr(config, "TTS_CACHE_DIR", str(d))
    return d


def wav(cache: Path, name: str, *, age_s: float) -> Path:
    p = cache / name
    p.write_bytes(b"RIFF____WAVE")
    when = time.time() - age_s
    import os

    os.utime(p, (when, when))
    return p


def a_host() -> tts_host.VoiceHost:
    """A host with no worker. `poll_health` is safe to call on one: its other
    three jobs all read state that is None here."""
    return tts_host.VoiceHost()


class TestThePulseEnforcesTheAgeWindow:
    def test_an_expired_file_goes_without_anyone_speaking(
            self, cache) -> None:
        old = wav(cache, "speak-1-111-222.wav",
                  age_s=config.TTS_CACHE_MAX_AGE_S + 60)
        assert old.exists(), "ground: the fixture really is on disk"

        a_host().poll_health()

        assert not old.exists()

    def test_a_file_inside_the_window_stays(self, cache) -> None:
        """GROUND CONTROL. Without it "delete everything on every pulse"
        passes the test above - and that would shred the audio of the reply
        somebody is listening to right now."""
        # A FIXED age, with the constant used only to assert the fixture is
        # inside the window. Deriving the age from the constant instead
        # (`MAX_AGE - 60`) makes the fixture move with it, and this test then
        # stays green even when the window is set to zero - measured: that
        # mutation survived until this line changed.
        assert 60 < config.TTS_CACHE_MAX_AGE_S, "ground: 60s is inside it"
        fresh = wav(cache, "speak-2-333-444.wav", age_s=60)

        a_host().poll_health()

        assert fresh.exists()

    def test_the_compiler_cache_is_left_alone(self, cache) -> None:
        """POSITIVE CONTROL for the DO NOT TOUCH decision next door.

        `inductor/` is deliberately not swept: deleting it brings back a
        ~346 second first compile on every lock. The pulse must not become
        the thing that finally deletes it.
        """
        kernels = cache / "inductor"
        kernels.mkdir()
        kernel = kernels / "compiled.bin"
        kernel.write_bytes(b"x")
        import os

        when = time.time() - (config.TTS_CACHE_MAX_AGE_S * 10)
        os.utime(kernel, (when, when))

        a_host().poll_health()

        assert kernel.exists()

    def test_a_file_that_is_not_generated_audio_stays(self, cache) -> None:
        """The name gate, from the other side: only `speak-*.wav` is ours."""
        theirs = wav(cache, "holiday.wav",
                     age_s=config.TTS_CACHE_MAX_AGE_S * 10)

        a_host().poll_health()

        assert theirs.exists()

    def test_a_file_being_written_now_is_not_taken(self, cache) -> None:
        """REGRESSION GUARD, not a red-green gate.

        Putting the trim on the pulse opens a window that did not exist:
        the timer can now walk the directory while synthesis is writing into
        it. The age threshold is what makes that safe - a file created a
        moment ago is nowhere near the cutoff - and this pins that reasoning
        rather than assuming it.

        Under the `yas-esigini-sifirlama` mutation this test goes red, which
        is the correct reaction: a zero window really would delete the file
        being written.
        """
        being_written = wav(cache, "speak-9-999-999.wav", age_s=0)

        a_host().poll_health()

        assert being_written.exists()
