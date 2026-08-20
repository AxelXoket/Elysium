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
import logging
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

    def test_a_blocked_migration_does_not_log_the_legacy_folder_name(
        self, refs_root, caplog
    ):
        """The collision warning fires ONLY for a legacy folder, and there the
        folder name is the slug of the label the person typed - a name they
        read on screen, written to elysium.log in plaintext, at WARNING, on an
        install that is not brand new. The AST scanner in log_leak_scan.py
        structurally cannot catch this: it follows denylisted variable names
        and this was `child.name`, an attribute."""
        _make_legacy_folder(refs_root, "grandma")
        # Put a voice ALREADY in final form exactly where the legacy one is
        # about to move - the shape a restore-from-backup leaves, where both
        # the migrated folder and its pre-migration original are on disk. An
        # empty directory will not do: the loop adopts a nameless folder,
        # writes it a voice.json and renames it out of the way, so the
        # collision never happens and this test would pass on nothing.
        occupied = refs_root / refs._hash_name("grandma")
        occupied.mkdir()
        (occupied / refs.META_NAME).write_text(
            json.dumps({"voice_id": "grandma", "label": "grandma"}),
            encoding="utf-8")

        with caplog.at_level(logging.WARNING, logger="tts.refs"):
            result = refs.migrate_legacy_voice_dirs()

        # GROUND: the branch under test really did run. Without this the
        # assertion below passes on any tree where nothing was logged at all.
        assert result["skipped"] == ["grandma"]
        assert caplog.records, "the collision warning did not fire"

        logged = " ".join(r.getMessage() for r in caplog.records)
        assert "grandma" not in logged, logged
        # And it still says which voice, opaquely, or it would be useless.
        assert refs._hash_name("grandma") in logged

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

    def test_an_empty_key_file_is_rebuilt_rather_than_used(
        self, refs_root, monkeypatch
    ):
        """A 0-byte .voice-index-key is what a crash between the O_EXCL create
        and the write leaves behind, and the FileExistsError branch used to
        return it unchecked. From then on _hash_name salted with b"" forever:
        a bare sha256 of the voice id, which is exactly the guess-and-match
        attack the key exists to stop - silently, permanently, with no way to
        tell from the outside."""
        (refs_root / refs.INDEX_KEY_NAME).write_bytes(b"")
        fixed = bytes(range(32))
        monkeypatch.setattr(os, "urandom", lambda n: fixed)

        key = refs._index_key()

        assert key == fixed
        assert len(key) == 32, "an empty file was accepted as the salt"
        # And it is durable, not just correct in memory - the next call has to
        # read the same 32 bytes back off disk.
        assert refs._index_key() == fixed
        assert (refs_root / refs.INDEX_KEY_NAME).read_bytes() == fixed
        assert not (refs_root / (refs.INDEX_KEY_NAME + ".new")).exists()

    def test_the_salt_actually_changes_the_folder_name(self, refs_root, monkeypatch):
        """The discriminating control for the test above. If the repaired key
        made no difference to the hash, that test would pass while proving
        nothing - so measure that an unsalted sha256 is NOT what lands on
        disk."""
        import hashlib

        fixed = bytes(range(32))
        monkeypatch.setattr(os, "urandom", lambda n: fixed)

        salted = refs._hash_name("ayse")
        unsalted = hashlib.sha256(b"ayse").hexdigest()

        assert salted != unsalted
        assert salted == hashlib.sha256(fixed + b"ayse").hexdigest()

    def test_a_crlf_expanded_key_is_used_as_is_not_refused(
        self, refs_root, monkeypatch
    ):
        """The install this module has actually broken before: written by the
        pre-O_BINARY code, so every 0x0A in the key became 0x0D 0x0A and the
        file is 33-36 bytes. Those bytes still resolve every folder that
        install has ever made. Replacing them - or refusing them - would take
        a working install and cut it off from its own voices, which is a worse
        outcome than the non-canonical encoding it is fixing.

        Honest about what this guards: it is NOT a regression test, because
        the code it replaced already returned these bytes unchanged. It went
        in because the FIRST draft of the empty-key repair raised on any
        length other than 32, which would have bricked exactly these installs.
        It exists to keep the next person from making that same tightening."""
        expanded = bytes(range(32)).replace(b"\x0a", b"\x0d\x0a")
        assert len(expanded) == 33  # ground: the fixture really is oversized
        (refs_root / refs.INDEX_KEY_NAME).write_bytes(expanded)
        monkeypatch.setattr(refs.time, "sleep", lambda _s: None)

        assert refs._index_key() == expanded
        # Ground: left exactly as found, and still resolving the same folder
        # on the next call rather than drifting.
        assert (refs_root / refs.INDEX_KEY_NAME).read_bytes() == expanded
        assert refs._hash_name("ayse") == refs._hash_name("ayse")

    def test_concurrent_repairs_do_not_collide_or_disagree(
        self, refs_root, monkeypatch
    ):
        """The wait loop SYNCHRONISES every caller: they all give up at the
        same instant and all arrive at the repair together. A single shared
        temp name made that four threads racing on one file, and on Windows
        os.replace answers with an unhandled WinError 32 - a 500, not a TTS
        error. Worse, a loser that returned its own unwritten key would hash
        differently from the winner forever after, which is the exact
        disagreement O_EXCL exists to prevent."""
        import threading

        (refs_root / refs.INDEX_KEY_NAME).write_bytes(b"")
        # time.sleep is NOT patched out here on purpose: the retries are the
        # mechanism under test, and a no-op sleep would spin all 50 attempts
        # inside a single rename and measure nothing.
        counter = iter(range(1000))
        monkeypatch.setattr(
            os, "urandom",
            lambda n: bytes([next(counter) % 256]) * n)

        start = threading.Barrier(4)
        results: list = []

        def run():
            start.wait()
            try:
                results.append(refs._index_key())
            except BaseException as exc:        # noqa: BLE001 - the point
                results.append(exc)

        threads = [threading.Thread(target=run) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(isinstance(r, bytes) for r in results), results
        assert len(set(results)) == 1, "threads disagreed about the salt"
        assert results[0] == (refs_root / refs.INDEX_KEY_NAME).read_bytes()
        # No temp files left lying around in the refs root.
        assert [p.name for p in refs_root.glob("*.new*")] == []

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


class TestTheIdSurvivesAPowerCut:
    """migrate_legacy_voice_dirs() sells its own crash-safety in its docstring:
    the id is written into voice.json and "that write is flushed to disk BEFORE
    the rename is attempted", so a crash between the two leaves a folder whose
    voice.json already carries the id and the next run resumes from it.

    Without an fsync that is a hope, not a promise. The rename can reach the
    disk while the file's bytes are still in the page cache, and what survives
    is a folder under its new opaque name holding an empty voice.json - which
    list_voices() skips and _voice_dir() cannot find. The voice is gone from
    the app while every one of its files is still sitting there.

    Modelled on test_tts_runtime.py's registry test: watch os.fsync and
    os.replace/rename through the real calls and assert the ORDER, because
    ordering is the whole claim.
    """

    def test_the_id_is_synced_before_the_folder_is_renamed(
        self, refs_root, monkeypatch
    ):
        # Mint the index key FIRST, outside the window being watched. Without
        # this the key file's own fsync (also correct, also new) satisfies the
        # ordering assertion below and the test passes with _write_meta's
        # fsync deleted - measured. Now the only fsync that can happen inside
        # the watched call is voice.json's.
        refs._index_key()
        _make_legacy_folder(refs_root, "legacyvoice")
        events: list[tuple[str, int]] = []
        real_fsync = os.fsync
        real_rename = Path.rename

        def watched_fsync(fd):
            # Size THROUGH THE SAME DESCRIPTOR, so this is the file being made
            # durable rather than some other file that happens to exist.
            events.append(("fsync", os.fstat(fd).st_size))
            return real_fsync(fd)

        def watched_rename(self, target):
            events.append(("rename", 0))
            return real_rename(self, target)

        monkeypatch.setattr(os, "fsync", watched_fsync)
        monkeypatch.setattr(Path, "rename", watched_rename)

        refs.migrate_legacy_voice_dirs()

        names = [name for name, _ in events]
        assert "rename" in names, "the migration never renamed anything"
        assert "fsync" in names, "voice.json was renamed into place unsynced"
        assert names.index("fsync") < names.index("rename"), names
        # And it synced something with content in it, not an empty handle.
        synced = next(size for name, size in events if name == "fsync")
        assert synced > 0
