"""The owner's rule, verbatim: a name a person typed and can read on screen
must never sit outside the vault as a name on disk. `voice_id` is exactly
that - the frontend slugs the label the user typed and uses the slug as both
the API id and, until this file's change, the folder name. `dir voice\\refs`
used to be a readable roster of who somebody has cloned, with no passphrase
and nothing to unlock, surviving every lock.

This file proves three things about the fix in tts/refs.py:
  1. a NEW voice never gets a folder name that says anything about its id.
  2. an EXISTING (pre-fix) install migrates - in-process, on first touch,
     with no separate step for the owner to remember.
  3. the migration is idempotent and safe to interrupt half way.

Behavioural throughout: nothing here greps refs.py's source. Every assertion
either drives the module through its public functions or inspects the real
files the module reads and writes.
"""
import json
import os
import subprocess
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


def _wav_bytes(seconds=4.0, rate=44100):
    import io

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


def _junction(link: Path, target: Path) -> bool:
    """A real NTFS junction, or False when this machine cannot make one."""
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True, shell=False)
    return result.returncode == 0 and link.exists()


def _make_legacy_folder(root: Path, voice_id: str, *, with_voice_id_in_meta=False,
                        label: str | None = None) -> Path:
    """A folder in the SHAPE the app produced before this fix: named after
    the id, holding ref.wav/transcript.txt/voice.json, and - the one detail
    that matters for the interrupted-migration test - voice.json normally
    has no "voice_id" key at all, because nothing ever wrote one."""
    folder = root / voice_id
    folder.mkdir(parents=True)
    (folder / "ref.wav").write_bytes(_wav_bytes())
    (folder / "transcript.txt").write_text("the words in the clip", encoding="utf-8")
    meta = {
        "label": label or voice_id,
        "added_at": 1700000000.0,
        "transcript_source": "user",
    }
    if with_voice_id_in_meta:
        meta["voice_id"] = voice_id
    (folder / refs.META_NAME).write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return folder


class TestNewVoicesGetAnOpaqueFolder:
    def test_the_folder_name_is_not_the_id_or_the_label(self, refs_root):
        voice = refs.save_upload("my-girlfriend", "ref.wav", _wav_bytes(),
                                 label="My Girlfriend")
        folder = Path(voice.path)
        assert folder.name != "my-girlfriend"
        assert "girlfriend" not in folder.name.lower()
        # 64 lowercase hex characters - a sha256 digest, not a slug.
        assert len(folder.name) == 64
        assert all(c in "0123456789abcdef" for c in folder.name)

    def test_listing_the_refs_root_shows_no_slug_at_all(self, refs_root):
        """The actual threat: `dir voice\\refs` as a roster. Every name it
        prints must be either the salt file or an opaque hex folder."""
        refs.save_upload("ayse", "ref.wav", _wav_bytes(), label="Ayse")
        refs.save_upload("mom", "ref.wav", _wav_bytes(), label="Mom")
        names = [p.name for p in refs_root.iterdir()]
        assert "ayse" not in names and "mom" not in names
        for name in names:
            if name == refs.INDEX_KEY_NAME:
                continue
            assert len(name) == 64 and all(c in "0123456789abcdef" for c in name)

    def test_the_id_still_resolves_and_still_round_trips_over_the_api_shape(
        self, refs_root
    ):
        """The folder name changed; nothing about the id's own behaviour did."""
        refs.save_upload("ayse", "ref.wav", _wav_bytes(),
                         label="Ayse", transcript="merhaba")
        voice = refs.describe("ayse")
        assert voice.voice_id == "ayse"
        assert voice.label == "Ayse"
        assert voice.transcript == "merhaba"
        assert [v.voice_id for v in refs.list_voices()] == ["ayse"]

    def test_two_different_labels_never_collide_on_disk(self, refs_root):
        a = refs.save_upload("twin", "ref.wav", _wav_bytes(), label="A")
        # Different id, same-ish shape - just confirming the hash is a
        # function of the id, not something coarser that would alias voices.
        b = refs.save_upload("twin2", "ref.wav", _wav_bytes(), label="B")
        assert Path(a.path) != Path(b.path)


class TestMigratingAnExistingInstall:
    def test_a_legacy_folder_is_renamed_and_still_resolves(self, refs_root):
        legacy = _make_legacy_folder(refs_root, "legacyvoice")
        assert legacy.is_dir()  # ground: the old shape really is there first

        result = refs.migrate_legacy_voice_dirs()

        assert result["migrated"] == ["legacyvoice"]
        assert not legacy.exists(), "the old, identity-named folder must be gone"
        new_folder = refs_root / refs._hash_name("legacyvoice")
        assert new_folder.is_dir()
        assert new_folder.name != "legacyvoice"

        voice = refs.describe("legacyvoice")
        assert voice.label == "legacyvoice"
        assert voice.transcript == "the words in the clip"
        assert voice.path == str(new_folder)

    def test_the_migrated_folder_now_carries_its_own_id(self, refs_root):
        """So a later list_voices() (which cannot read the opaque folder
        name) has something to recover the id from."""
        _make_legacy_folder(refs_root, "legacyvoice")
        refs.migrate_legacy_voice_dirs()
        new_folder = refs_root / refs._hash_name("legacyvoice")
        meta = json.loads((new_folder / refs.META_NAME).read_text(encoding="utf-8"))
        assert meta["voice_id"] == "legacyvoice"

    def test_running_it_twice_does_nothing_the_second_time(self, refs_root):
        _make_legacy_folder(refs_root, "legacyvoice")
        first = refs.migrate_legacy_voice_dirs()
        assert first["migrated"] == ["legacyvoice"]

        before = {p.name: p.stat().st_mtime_ns
                 for p in refs_root.rglob("*") if p.is_file()}
        second = refs.migrate_legacy_voice_dirs()
        after = {p.name: p.stat().st_mtime_ns
                for p in refs_root.rglob("*") if p.is_file()}

        assert second == {"migrated": [], "skipped": []}
        assert before == after, "an idempotent pass must not touch anything"
        assert refs.describe("legacyvoice").label == "legacyvoice"

    def test_it_runs_automatically_on_first_touch_with_no_separate_step(
        self, refs_root
    ):
        """The owner should not have to remember to run anything. The very
        first call into the module that resolves a folder must migrate."""
        _make_legacy_folder(refs_root, "legacyvoice")
        # No call to migrate_legacy_voice_dirs() anywhere in this test.
        voice = refs.describe("legacyvoice")
        assert voice.label == "legacyvoice"
        assert not (refs_root / "legacyvoice").exists()

    def test_list_voices_also_triggers_it(self, refs_root):
        _make_legacy_folder(refs_root, "legacyvoice")
        names = [v.voice_id for v in refs.list_voices()]
        assert names == ["legacyvoice"]
        assert not (refs_root / "legacyvoice").exists()

    def test_a_second_unrelated_legacy_voice_migrates_independently(
        self, refs_root
    ):
        _make_legacy_folder(refs_root, "ayse")
        _make_legacy_folder(refs_root, "mom", label="Mom")
        result = refs.migrate_legacy_voice_dirs()
        assert sorted(result["migrated"]) == ["ayse", "mom"]
        assert refs.describe("ayse").label == "ayse"
        assert refs.describe("mom").label == "Mom"


class TestInterruptedMigrationResumes:
    def test_meta_written_but_rename_not_yet_done_still_completes(self, refs_root):
        """The exact half-way state a crash between the two steps leaves:
        voice.json already carries the id (that write happened and was
        flushed), but the folder is still sitting under its old, identity
        name because the process died before the rename. The NEXT run must
        finish the job using the id already on record, not re-derive it."""
        half_migrated = _make_legacy_folder(
            refs_root, "interrupted", with_voice_id_in_meta=True)
        assert half_migrated.name == "interrupted"  # ground: not renamed yet

        result = refs.migrate_legacy_voice_dirs()

        assert result["migrated"] == ["interrupted"]
        assert not half_migrated.exists()
        new_folder = refs_root / refs._hash_name("interrupted")
        assert new_folder.is_dir()
        assert refs.describe("interrupted").voice_id == "interrupted"

    def test_resuming_twice_is_still_idempotent(self, refs_root):
        _make_legacy_folder(refs_root, "interrupted", with_voice_id_in_meta=True)
        refs.migrate_legacy_voice_dirs()
        again = refs.migrate_legacy_voice_dirs()
        assert again == {"migrated": [], "skipped": []}
        assert refs.describe("interrupted").voice_id == "interrupted"


class TestMigrationNeverDestroysAUserFile:
    def test_a_target_collision_is_skipped_not_overwritten(self, refs_root):
        """A pre-existing correctly-named folder, plus a STRAY folder that
        also happens to be named after the same id (a restored backup, a
        manual copy - anything). Migration must not guess which one is
        right; destroying either is the wrong direction to fail in."""
        good = refs.save_upload("dup", "ref.wav", _wav_bytes(), label="Good take")
        good_folder = Path(good.path)
        good_bytes = (good_folder / "ref.wav").read_bytes()

        stray = _make_legacy_folder(refs_root, "dup", label="Stray copy")
        stray_bytes = (stray / "ref.wav").read_bytes()

        result = refs.migrate_legacy_voice_dirs()

        assert "dup" in result["skipped"]
        assert good_folder.is_dir() and (good_folder / "ref.wav").read_bytes() == good_bytes
        assert stray.is_dir() and (stray / "ref.wav").read_bytes() == stray_bytes

    def test_a_folder_that_leads_elsewhere_is_not_migrated(self, tmp_path, refs_root):
        outside = tmp_path / "somebody-elses-documents"
        outside.mkdir()
        victim = outside / "notes.txt"
        content = b"nothing to do with this app"
        victim.write_bytes(content)

        link = refs_root / "redirected"
        if not _junction(link, outside):
            pytest.skip("this machine cannot create a junction")

        result = refs.migrate_legacy_voice_dirs()

        assert "redirected" in result["skipped"]
        assert victim.read_bytes() == content, "walked through the junction"

    def test_a_stray_folder_that_is_not_a_valid_id_is_left_alone(self, refs_root):
        """Something under voice/refs/ that this module never created - the
        migration must not invent an id for it."""
        stray = refs_root / "Not A Valid Slug!"
        stray.mkdir()
        (stray / "whatever.txt").write_text("not ours", encoding="utf-8")

        result = refs.migrate_legacy_voice_dirs()

        assert "Not A Valid Slug!" in result["skipped"]
        assert stray.is_dir()


class TestTheIndexKeyStaysStable:
    """os.open() on Windows opens in TEXT mode unless os.O_BINARY is passed,
    and text mode rewrites a 0x0A byte in the data as 0x0D 0x0A on write. 32
    random bytes carry a 0x0A about 1 time in 9, so without O_BINARY this
    surfaced as an intermittent failure - a handful of iterations of the
    naming tests above, run in a loop, reliably found it, but any single run
    had roughly a 9-in-10 chance of passing anyway. Forcing the byte in makes
    the bug deterministic instead of a coin flip landing on the wrong side.
    """

    def test_a_key_containing_a_newline_byte_still_round_trips(
        self, refs_root, monkeypatch
    ):
        fixed = bytes([10, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15,
                       16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29,
                       30, 10])
        assert len(fixed) == 32 and fixed.count(10) >= 2  # ground: chosen on purpose
        monkeypatch.setattr(os, "urandom", lambda n: fixed)

        # The write happens on the first call; every later call - including
        # every OTHER call within a single save_upload - reads the file back.
        first = refs._index_key()
        second = refs._index_key()

        assert len(first) == 32, "a text-mode write silently grew the file"
        assert first == second == fixed

    def test_save_upload_resolves_to_the_same_folder_it_created(
        self, refs_root, monkeypatch
    ):
        """The end-to-end version of the same bug: save_upload computes the
        folder to create once and, through describe() at the end, a second
        time - a key that changes on disk between those two reads makes it
        create one folder and then fail to find it."""
        fixed = bytes([10] * 32)
        monkeypatch.setattr(os, "urandom", lambda n: fixed)

        voice = refs.save_upload("ayse", "ref.wav", _wav_bytes(), label="Ayse")

        assert Path(voice.path).is_dir()
        assert voice.voice_id == "ayse"
