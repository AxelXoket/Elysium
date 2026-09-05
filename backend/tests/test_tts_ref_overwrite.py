"""H-35 - what happens to the PREVIOUS words when a voice reference is edited.

A reference voice is two secrets in a folder whose name is deliberately
opaque: `transcript.txt` is the user's own speech written out, and
`voice.json` carries the name they typed. The folder name is unreadable
precisely because the content is not.

Both were rewritten with a truncating open. Four things followed from that one
habit, and this file measures them one at a time:

  1. the file was overwritten IN PLACE, so the old blocks went back to the
     filesystem still holding the old words;
  2. `secure_delete` - the single door this app deletes user content through -
     was never called on either path;
  3. `_write_meta` was not atomic, so a write that died partway left
     `voice.json` holding half a document and the voice missing from the app;
  4. there was no length ceiling at all, and a five million character
     transcript reached the disk in one call.

On the reach of these tests, said plainly: a Python test cannot read a freed
disk block, so nothing here can assert directly that the platter is clean.
What it CAN assert is every observable step that leads there, and each of
these is red without the fix: the file is replaced rather than reused, the old
bytes are handed to the real overwriter while they are still the old bytes,
and no second copy of them survives as a file anywhere under the refs root.
The overwrite itself belongs to `secure_delete` and is measured by
`test_secure_delete.py`.
"""
import io
import json
import os
import wave
from pathlib import Path

import pytest

import config
import secure_delete
from tts import refs

#: The words that must not survive an edit. Synthetic on purpose: no test in
#: this repo puts a real transcript on disk, and this unit exists because a
#: transcript is recoverable, so writing a real one here would be making a
#: second copy of exactly the thing being protected.
SENTINEL = "SENTINEL-H35-0F3A-the-first-take-words-that-must-not-survive"
#: A label is user content the same way the transcript is. Same rule.
SENTINEL_LABEL = "SENTINEL-H35-9C21-the-first-label"


@pytest.fixture
def refs_root(monkeypatch, tmp_path):
    root = tmp_path / "voice" / "refs"
    root.mkdir(parents=True)
    monkeypatch.setattr(config, "TTS_REFS_DIR", str(root), raising=False)
    return root


def _wav_bytes(seconds=8.0, rate=44100):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


def _every_byte_under(root: Path) -> bytes:
    """Every byte of every file under `root`, end to end.

    The sweep looks at FILES, which is the honest limit of what a test can
    reach: it cannot see a block the filesystem has freed. What it does see is
    every surviving COPY, and that includes the temporary file the fix writes
    the new words under - the one new way this change could leave a second
    copy of the old ones lying about. It is checked against a positive control
    below, because an absence measured by a sweep that looks in the wrong
    place is not an absence.
    """
    blob = bytearray()
    for path in sorted(root.rglob("*")):
        if path.is_file():
            blob += path.read_bytes()
            blob += b"\x00"
    return bytes(blob)


def _watch_the_overwriter(monkeypatch) -> list[tuple[str, bytes, bool]]:
    """Record what the real overwriter was aimed at, and what the file still
    held at the moment it was aimed there.

    A passthrough, not a stub: the real `shred` runs, with all of its guards
    and its actual overwrite, and the test reads the outcome off the disk
    afterwards. The recording exists to answer the one question the disk
    cannot be asked from here - whether the OLD words were what got handed to
    the overwriter, or whether a truncating write had already thrown them away
    where nothing could reach them.
    """
    seen: list[tuple[str, bytes, bool]] = []
    real = secure_delete.shred

    def spy(path):
        target = Path(path)
        try:
            held = target.read_bytes()
        except OSError:
            held = b""
        answer = real(target)
        seen.append((target.name, held, answer))
        return answer

    monkeypatch.setattr(secure_delete, "shred", spy)
    return seen


def _make_undestroyable(monkeypatch, name: str) -> None:
    """Make exactly ONE file refuse to die, the way the real world does it.

    A holder that denies writes, a second hardlink, a redirected name: all
    three come back from `secure_delete` as False with the bytes still in
    place. Stubbing the whole primitive would also break the cleanup of the
    file the fix has just written, and the test would then be measuring the
    stub.
    """
    real = secure_delete.shred
    monkeypatch.setattr(
        secure_delete, "shred",
        lambda path: False if Path(path).name == name else real(path))


def _saved_with_words(voice_id: str = "ayse", words: str = SENTINEL,
                      label: str = SENTINEL_LABEL) -> Path:
    refs.save_upload(voice_id, "ref.wav", _wav_bytes(),
                     label=label, transcript=words)
    return Path(refs.describe(voice_id).path)


# ── ground: the machinery these measurements are built on ────────────────────


class TestGround:
    def test_a_correction_reads_back_exactly(self, refs_root):
        """The point of the whole function. Everything below is worthless if
        the words stop arriving."""
        _saved_with_words()
        voice = refs.set_transcript("ayse", "the corrected words")
        assert voice.transcript == "the corrected words"
        assert refs.describe("ayse").transcript == "the corrected words"

    def test_voice_json_is_valid_json_after_a_write(self, refs_root):
        folder = _saved_with_words()
        loaded = json.loads((folder / refs.META_NAME).read_text(encoding="utf-8"))
        assert loaded["voice_id"] == "ayse"
        assert loaded["label"] == SENTINEL_LABEL

    def test_a_half_written_voice_json_would_be_noticed(self, refs_root):
        """POSITIVE CONTROL for every "still valid JSON" claim below. Without
        it those assertions would also pass against a parser that swallowed
        corruption, and the atomicity test would be measuring nothing."""
        folder = _saved_with_words()
        meta = folder / refs.META_NAME
        whole = meta.read_text(encoding="utf-8")
        meta.write_text(whole[:len(whole) // 2], encoding="utf-8")
        with pytest.raises(ValueError):
            json.loads(meta.read_text(encoding="utf-8"))

    def test_file_identity_is_meaningful_on_this_filesystem(self, refs_root):
        """GROUND CONTROL for the identity assertions below.

        They read st_ino, which is a real file index on the platforms this
        runs on but is documented as zero on some. If it were zero or unstable
        here, "the file was replaced" would pass on noise. So: it is non-zero,
        and it does NOT move when a file is genuinely written in place.
        """
        folder = _saved_with_words()
        witness = folder / "witness.txt"
        witness.write_text("first", encoding="utf-8")
        before = os.stat(witness).st_ino
        assert before != 0
        witness.write_text("second", encoding="utf-8")
        assert os.stat(witness).st_ino == before

    def test_the_sweep_finds_words_that_were_left_behind(self, refs_root):
        """POSITIVE CONTROL for _every_byte_under. An absence proved by a
        sweep that reads the wrong place is not an absence."""
        folder = _saved_with_words()
        (folder / "left-behind.txt").write_text(SENTINEL, encoding="utf-8")
        assert SENTINEL.encode("utf-8") in _every_byte_under(refs_root)


# ── 1 and 2: the old words are destroyed, not truncated away ─────────────────


class TestTheOldWordsAreDestroyed:
    def test_correcting_a_transcript_replaces_the_file_instead_of_reusing_it(
        self, refs_root
    ):
        """The truncating write kept the SAME file and shortened it, which is
        what leaves the tail of the old words in freed blocks. A replacement
        cannot: the new file has to exist before the old one is destroyed, so
        the two are different files and the index says so."""
        folder = _saved_with_words()
        tpath = folder / refs.TRANSCRIPT_NAME
        before = os.stat(tpath).st_ino

        refs.set_transcript("ayse", "short")

        assert os.stat(tpath).st_ino != before, (
            "the transcript was rewritten in place, so the previous words "
            "were freed rather than destroyed")

    def test_the_old_words_reach_the_overwriter_while_they_are_still_there(
        self, refs_root, monkeypatch
    ):
        """The other half of the same claim: the file is not only replaced,
        the previous content is handed to the app's one deletion primitive,
        and it is handed over WHOLE rather than after a truncation has already
        put it out of reach."""
        _saved_with_words()
        seen = _watch_the_overwriter(monkeypatch)

        refs.set_transcript("ayse", "short")

        handed = [held for name, held, _ in seen if name == refs.TRANSCRIPT_NAME]
        assert handed, "nothing ever asked to destroy the previous transcript"
        assert SENTINEL.encode("utf-8") in handed[0]
        assert all(ok for name, _, ok in seen if name == refs.TRANSCRIPT_NAME)

    def test_no_copy_of_the_old_words_survives_as_a_file(self, refs_root):
        folder = _saved_with_words()
        refs.set_transcript("ayse", "short")

        assert SENTINEL.encode("utf-8") not in _every_byte_under(refs_root)
        assert (folder / refs.TRANSCRIPT_NAME).read_text(
            encoding="utf-8") == "short"

    def test_no_temporary_file_survives_a_completed_write(self, refs_root):
        """The fix writes the new words under a temporary name first. A test
        has to be able to say that name is gone, and it reads the constant
        rather than retyping it, because a retyped literal stops matching the
        day the scheme changes and the assertion then passes forever."""
        _saved_with_words()
        refs.set_transcript("ayse", "short")

        leftovers = [p.name for p in refs_root.rglob("*" + refs._REPLACE_TMP_INFIX + "*")]
        assert leftovers == []

    def test_replacing_a_clip_destroys_the_words_of_the_take_it_replaces(
        self, refs_root, monkeypatch
    ):
        """The third write path. The upload route writes the transcript too,
        and a re-recording is the case where the previous words are most
        likely to be the ones somebody wanted gone."""
        _saved_with_words()
        seen = _watch_the_overwriter(monkeypatch)

        refs.save_upload("ayse", "ref.wav", _wav_bytes(9.0),
                         label="second", transcript="the words of take two")

        handed = [held for name, held, _ in seen if name == refs.TRANSCRIPT_NAME]
        assert handed, "the previous take's words were truncated, not destroyed"
        assert SENTINEL.encode("utf-8") in handed[0]
        assert SENTINEL.encode("utf-8") not in _every_byte_under(refs_root)


class TestTheOldLabelIsDestroyed:
    def test_changing_the_label_replaces_voice_json_instead_of_reusing_it(
        self, refs_root
    ):
        folder = _saved_with_words()
        meta = folder / refs.META_NAME
        before = os.stat(meta).st_ino

        refs.save_upload("ayse", "ref.wav", _wav_bytes(9.0), label="second")

        assert os.stat(meta).st_ino != before, (
            "voice.json was rewritten in place, so the previous label was "
            "freed rather than destroyed")

    def test_the_old_label_reaches_the_overwriter(self, refs_root, monkeypatch):
        _saved_with_words()
        seen = _watch_the_overwriter(monkeypatch)

        refs.set_transcript("ayse", "short")

        handed = [held for name, held, _ in seen if name == refs.META_NAME]
        assert handed, "nothing ever asked to destroy the previous voice.json"
        assert SENTINEL_LABEL.encode("utf-8") in handed[0]

    def test_no_copy_of_the_old_label_survives_as_a_file(self, refs_root):
        _saved_with_words()
        refs.save_upload("ayse", "ref.wav", _wav_bytes(9.0), label="second")

        assert SENTINEL_LABEL.encode("utf-8") not in _every_byte_under(refs_root)
        assert refs.describe("ayse").label == "second"


# ── 3: voice.json is never half a document ───────────────────────────────────


class _WillNotSerialise:
    """A value json refuses. It stands in for every way a write can die
    partway: the interesting property is not WHY it stopped but where the
    file was when it did."""


class TestVoiceJsonIsNeverHalfWritten:
    """_write_meta is called directly here, and deliberately.

    No route can hand it an unencodable value, which is the point: the failure
    being modelled is a write that stops in the middle, and the only way to
    stop one in the middle from a test is to make the encoding fail. What is
    measured is the file on disk afterwards, not the exception.
    """

    def test_a_write_that_dies_partway_leaves_the_previous_file_whole(
        self, refs_root
    ):
        folder = _saved_with_words()
        meta = refs._read_meta(folder)
        meta["label"] = _WillNotSerialise()

        with pytest.raises(TypeError):
            refs._write_meta(folder, meta)

        loaded = json.loads(
            (folder / refs.META_NAME).read_text(encoding="utf-8"))
        assert loaded["label"] == SENTINEL_LABEL
        assert loaded["voice_id"] == "ayse"

    def test_the_voice_is_still_there_after_that_failure(self, refs_root):
        """What the corruption actually costs. list_voices reads voice.json
        for the id, so a folder holding half a document drops out of the app
        while every one of its files is still on disk."""
        folder = _saved_with_words()
        meta = refs._read_meta(folder)
        meta["label"] = _WillNotSerialise()

        with pytest.raises(TypeError):
            refs._write_meta(folder, meta)

        assert [v.voice_id for v in refs.list_voices()] == ["ayse"]
        assert (folder / "ref.wav").exists()

    def test_a_write_that_succeeds_still_changes_the_file(self, refs_root):
        """GROUND CONTROL. Without it the test above would also pass against
        a _write_meta that never wrote anything at all."""
        folder = _saved_with_words()
        meta = refs._read_meta(folder)
        meta["label"] = "a name that encodes"

        refs._write_meta(folder, meta)

        loaded = json.loads(
            (folder / refs.META_NAME).read_text(encoding="utf-8"))
        assert loaded["label"] == "a name that encodes"


# ── 4: the ceiling ───────────────────────────────────────────────────────────


class TestTheCeiling:
    def test_a_transcript_past_the_ceiling_is_refused(self, refs_root):
        folder = _saved_with_words()
        tpath = folder / refs.TRANSCRIPT_NAME
        before = tpath.read_bytes()
        before_mtime = os.stat(tpath).st_mtime_ns

        with pytest.raises(refs.RefError) as exc:
            refs.set_transcript("ayse", "x" * (refs.TRANSCRIPT_MAX_CHARS + 1))

        assert exc.value.code == refs.TTS_REFERENCE_INVALID
        assert tpath.read_bytes() == before
        assert os.stat(tpath).st_mtime_ns == before_mtime

    def test_a_transcript_exactly_at_the_ceiling_is_accepted(self, refs_root):
        """POSITIVE CONTROL. A ceiling of zero would satisfy the refusal above
        and make the feature unusable, and no test would have noticed."""
        _saved_with_words()
        words = "y" * refs.TRANSCRIPT_MAX_CHARS

        voice = refs.set_transcript("ayse", words)

        assert voice.transcript == words
        assert refs.describe("ayse").transcript == words

    def test_the_ceiling_guards_the_upload_route_too(self, refs_root):
        """The words reach the disk by two doors. A ceiling on one of them is
        decoration."""
        with pytest.raises(refs.RefError) as exc:
            refs.save_upload("ayse", "ref.wav", _wav_bytes(),
                             transcript="x" * (refs.TRANSCRIPT_MAX_CHARS + 1))

        assert exc.value.code == refs.TTS_REFERENCE_INVALID
        assert list(refs_root.iterdir()) == [], (
            "the refused upload still put something on disk")

    def test_the_ceiling_is_answered_before_anything_is_destroyed(
        self, refs_root, monkeypatch
    ):
        """The ORDER, which is the whole reason these two defects are one
        change: measure, then destroy, then write. Answering the ceiling after
        the shred would refuse the correction with the previous words already
        gone."""
        folder = _saved_with_words()
        tpath = folder / refs.TRANSCRIPT_NAME
        before = tpath.read_bytes()
        seen = _watch_the_overwriter(monkeypatch)

        with pytest.raises(refs.RefError):
            refs.set_transcript("ayse", "x" * (refs.TRANSCRIPT_MAX_CHARS + 1))

        assert seen == [], "the refusal had already destroyed something"
        assert tpath.read_bytes() == before
        assert SENTINEL.encode("utf-8") in _every_byte_under(refs_root)


# ── the refusal: nothing is written through a closed door ────────────────────


class TestARefusalIsNotWrittenThrough:
    def test_words_that_will_not_die_stop_the_correction(
        self, refs_root, monkeypatch
    ):
        """secure_delete fails CLOSED, and a caller that writes anyway turns
        its refusal into the very defect it refused to allow: the old words
        truncated away unshredded, reported as a success."""
        folder = _saved_with_words()
        tpath = folder / refs.TRANSCRIPT_NAME
        before = tpath.read_bytes()
        _make_undestroyable(monkeypatch, refs.TRANSCRIPT_NAME)

        with pytest.raises(refs.RefError) as exc:
            refs.set_transcript("ayse", "the correction nobody gets")

        assert exc.value.code == refs.TTS_REFERENCE_CLIP_STUCK
        # Nothing was destroyed...
        assert tpath.read_bytes() == before
        # ...nothing was written on top of it...
        assert refs.describe("ayse").transcript == SENTINEL
        # ...and no copy of the new words was left staged beside it.
        assert b"the correction nobody gets" not in _every_byte_under(refs_root)
        assert [p.name for p in
                refs_root.rglob("*" + refs._REPLACE_TMP_INFIX + "*")] == []

    def test_when_the_old_words_do_die_the_correction_lands(self, refs_root):
        """POSITIVE CONTROL for the refusal. Without it the test above would
        pass against a set_transcript that refused everything."""
        _saved_with_words()

        voice = refs.set_transcript("ayse", "the correction that lands")

        assert voice.transcript == "the correction that lands"

    def test_a_voice_json_that_will_not_die_stops_the_write(
        self, refs_root, monkeypatch
    ):
        folder = _saved_with_words()
        meta = folder / refs.META_NAME
        before = meta.read_bytes()
        _make_undestroyable(monkeypatch, refs.META_NAME)

        with pytest.raises(refs.RefError) as exc:
            refs._write_meta(folder, {"voice_id": "ayse", "label": "second"})

        assert exc.value.code == refs.TTS_REFERENCE_CLIP_STUCK
        assert meta.read_bytes() == before
        assert [p.name for p in
                refs_root.rglob("*" + refs._REPLACE_TMP_INFIX + "*")] == []

    def test_clearing_words_that_will_not_die_says_so_out_loud(
        self, refs_root, monkeypatch, caplog
    ):
        """Clearing is a deletion and nothing else, so there is no replacement
        to withhold and refusing would leave no way to try again. What must
        not happen is a silent success while the words are still on disk."""
        import logging

        _saved_with_words()
        _make_undestroyable(monkeypatch, refs.TRANSCRIPT_NAME)

        with caplog.at_level(logging.WARNING, logger="tts.refs"):
            voice = refs.set_transcript("ayse", "   ")

        assert any("could not be removed" in r.message for r in caplog.records)
        # And the payload does not claim the words are gone either, because
        # describe() reads the file rather than the branch that tried.
        assert voice.transcript == SENTINEL

    def test_clearing_words_that_do_die_is_quiet(self, refs_root, caplog):
        """GROUND CONTROL for the warning above: it fires on the refusal, not
        on every clear."""
        import logging

        _saved_with_words()

        with caplog.at_level(logging.WARNING, logger="tts.refs"):
            voice = refs.set_transcript("ayse", "   ")

        assert not voice.has_transcript
        assert voice.transcript_source == "none"
        assert [r.message for r in caplog.records] == []
        assert SENTINEL.encode("utf-8") not in _every_byte_under(refs_root)
