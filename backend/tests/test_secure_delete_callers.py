"""Every place that removes decrypted user content, checked at the bytes.

secure_delete.py's docstring said "two callers need it", and that sentence was
the bug. Six other places deleted exactly the same class of file - the old salt
and verifier after a rotation, the pre-rotation database copy, spoken
conversation as WAV, the user's own recorded voice, plaintext uploads the
migration had just sealed into the vault - each with a plain unlink, which
returns the blocks to the filesystem with the contents intact for any undelete
tool to walk back out.

None of it was visible to the suite, because the suite asked "is the file
gone". Every test here asks the other question instead: os.unlink is stubbed
out, the operation runs, and the file that should have been destroyed is read
back. Same length, different bytes, or the deletion was a rename with extra
steps.
"""
from __future__ import annotations

import logging
import os
import time
import wave
from io import BytesIO
from pathlib import Path

import pytest

import config
import secure_delete


def _valid_wav(seconds: float = 8.0, rate: int = 44100) -> bytes:
    """A clip save_upload will accept.

    It has to be a real one. save_upload validates BEFORE it deletes anything
    (tts/refs.py:212-217, and the comment there says why), so a junk payload
    would make the junction test below pass without a guard ever running.
    """
    buf = BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(2) * int(rate * seconds))
    return buf.getvalue()


@pytest.fixture()
def no_unlink(monkeypatch: pytest.MonkeyPatch):
    """Let the overwrite happen, then keep the corpse for inspection."""
    monkeypatch.setattr(os, "unlink", lambda *a, **kw: None)


def _assert_destroyed(path: Path, original: bytes) -> None:
    survivor = path.read_bytes()
    assert len(survivor) == len(original), "the overwrite changed the length"
    assert survivor != original, f"{path.name} was unlinked without overwriting"


class TestDiscard:
    """The wrapper the converted call sites use."""

    def test_a_name_that_was_never_there_is_success(self, tmp_path) -> None:
        # unlink(missing_ok=True) semantics: the caller asked for it to be
        # gone and it is. shred() alone cannot say this - its guards fail
        # closed, so a missing path comes back "redirected, left alone".
        assert secure_delete.discard(tmp_path / "never-existed") is True

    def test_a_real_file_is_overwritten_then_removed(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "secret"
        original = b"A" * 512
        target.write_bytes(original)
        monkeypatch.setattr(os, "unlink", lambda *a, **kw: None)

        assert secure_delete.discard(target) is True
        _assert_destroyed(target, original)

    def test_a_redirected_name_is_still_refused(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # discard() must not become the door around the guards.
        target = tmp_path / "looks-ordinary"
        target.write_bytes(b"x" * 32)
        monkeypatch.setattr(secure_delete, "is_redirected",
                            lambda p: Path(p).name == "looks-ordinary")
        assert secure_delete.discard(target) is False
        assert target.read_bytes() == b"x" * 32


class TestRotatingThePassphraseDestroysTheOldRecipe:
    """salt.bin + verifier.bin under the OLD key.

    scrypt parameters plus a 16-byte salt is a small, distinctive pattern -
    exactly what an undelete scan finds easily - and it sits beside snapshots
    that are still encrypted under the key it derives. A rotation that leaves
    it recoverable has revoked nothing for anyone who knew the old passphrase.
    """

    def test_the_shelved_identity_is_overwritten_not_just_unlinked(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import crypto

        vault = crypto.KeyVault(tmp_path)
        vault.initialize("first-passphrase-long-enough")
        old_salt = vault.salt_path.read_bytes()
        old_verifier = vault.verifier_path.read_bytes()

        shelved: list[Path] = []
        real_replace = Path.replace

        def watching_replace(self, target):
            if ".bak-" in Path(target).name:
                shelved.append(Path(target))
            return real_replace(self, target)

        monkeypatch.setattr(Path, "replace", watching_replace)
        monkeypatch.setattr(os, "unlink", lambda *a, **kw: None)

        vault.change_passphrase("second-passphrase-long-enough",
                                rekey_fn=lambda key: None,
                                verify_fn=lambda key: True)

        assert len(shelved) == 2, "the rotation shelved nothing to destroy"
        survivors = [p.read_bytes() for p in shelved if p.exists()]
        assert len(survivors) == 2, "the unlink stub should have kept both"
        assert old_salt not in survivors
        assert old_verifier not in survivors

    def test_a_failed_rekey_destroys_the_half_written_new_identity(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import crypto

        vault = crypto.KeyVault(tmp_path)
        vault.initialize("first-passphrase-long-enough")
        monkeypatch.setattr(os, "unlink", lambda *a, **kw: None)

        staged: dict[Path, bytes] = {}

        def refuse(key):
            # The failure lands after salt.bin.new and verifier.bin.new are
            # written, so read them here - comparing them afterwards to the
            # LIVE identity proves nothing, since a new key differs from the
            # old one whether or not anything overwrote it.
            for path in tmp_path.iterdir():
                if path.name.endswith(".new"):
                    staged[path] = path.read_bytes()
            raise RuntimeError("rekey failed")

        with pytest.raises(RuntimeError):
            vault.change_passphrase("second-passphrase-long-enough",
                                    rekey_fn=refuse, verify_fn=lambda k: True)

        assert staged, "nothing was staged, so this proves nothing"
        for leftover, before in staged.items():
            _assert_destroyed(leftover, before)


class TestTheSpokenConversationIsDestroyed:
    """WAV files of the conversation, in the clear, beside an encrypted DB."""

    def _cache(self, tmp_path, monkeypatch) -> Path:
        import config
        cache = tmp_path / "audio"
        cache.mkdir()
        monkeypatch.setattr(config, "TTS_CACHE_DIR", str(cache))
        return cache

    def test_the_launch_wipe_overwrites(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch, no_unlink
    ) -> None:
        from tts.host import wipe_audio_cache

        cache = self._cache(tmp_path, monkeypatch)
        spoken = cache / "speak-abc.wav"
        original = b"RIFF" + b"spoken words" * 40
        spoken.write_bytes(original)

        removed, left = wipe_audio_cache()

        assert removed == 1
        _assert_destroyed(spoken, original)

    def test_the_unlock_sweep_overwrites(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch, no_unlink
    ) -> None:
        # The one that runs on the mainline unlock path, and the one that had
        # no guards at all: not the junction check, not the hardlink check,
        # not the overwrite.
        from routers.vault import _purge_voice_cache

        cache = self._cache(tmp_path, monkeypatch)
        spoken = cache / "speak-xyz.wav"
        original = b"RIFF" + b"more spoken words" * 30
        spoken.write_bytes(original)

        _purge_voice_cache()

        _assert_destroyed(spoken, original)

    def test_the_unlock_sweep_refuses_a_shared_name(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch, no_unlink
    ) -> None:
        # A hardlink is not a reparse point, so only the link count catches it,
        # and overwriting through one destroys the file the other name opens.
        from routers.vault import _purge_voice_cache

        cache = self._cache(tmp_path, monkeypatch)
        victim = cache / "speak-shared.wav"
        original = b"RIFF" + b"z" * 200
        victim.write_bytes(original)
        monkeypatch.setattr(secure_delete, "is_shared",
                            lambda p: Path(p).name == "speak-shared.wav")

        _purge_voice_cache()

        assert victim.read_bytes() == original


class TestTheRecordedVoiceIsDestroyed:
    """A reference clip is a recording of the user, in their own voice."""

    def test_replacing_a_clip_overwrites_the_previous_take(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch, no_unlink
    ) -> None:
        # Someone who re-records because take one said something they did not
        # want kept still had take one on disk.
        from tts import refs

        monkeypatch.setattr(refs, "refs_dir", lambda: tmp_path)
        folder = tmp_path / "voice1"
        folder.mkdir()
        # A different suffix on purpose: the new take lands as ref.wav, so a
        # same-named old clip would be replaced by it and the read-back below
        # would inspect the NEW file instead of the corpse of the old one.
        old = folder / "ref.mp3"
        original = b"RIFF" + b"the first take" * 20
        old.write_bytes(original)

        monkeypatch.setattr(refs, "validate", lambda voice: None)
        # No try/except around this. A blanket "skip on any exception" would
        # turn a real regression in the replace path into a green run, which
        # is the failure mode this whole file exists to remove.
        refs.save_upload("voice1", "take2.wav",
                         b"RIFF" + b"the second take" * 20)

        _assert_destroyed(old, original)

    def test_deleting_a_voice_overwrites_its_clip(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch, no_unlink
    ) -> None:
        from tts import refs

        monkeypatch.setattr(refs, "refs_dir", lambda: tmp_path)
        folder = tmp_path / "voice2"
        folder.mkdir()
        clip = folder / "ref.wav"
        original = b"RIFF" + b"recorded speech" * 25
        clip.write_bytes(original)

        refs.delete("voice2")

        _assert_destroyed(clip, original)


class TestTheMigratedPlaintextUploadIsDestroyed:
    """The migration exists to move these bytes INTO the vault.

    Removing the original with a plain unlink left a recoverable copy of every
    picture outside it, which undoes the point of having run.
    """

    def test_half_written_upload_litter_is_overwritten(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch, no_unlink
    ) -> None:
        # The one branch of the sweep that needs no database behind it: a *.tmp
        # left by an interrupted upload is still a piece of the user's picture,
        # and it left by the same plain unlink as the rest.
        import config
        import legacy_migration

        uploads = tmp_path / "uploads"
        uploads.mkdir()
        litter = uploads / "halfwritten.tmp"
        original = b"\x89PNG\r\n\x1a\n" + b"picture bytes" * 40
        litter.write_bytes(original)
        monkeypatch.setattr(config, "UPLOADS_DIR", str(uploads))

        migrated, failed, removed = \
            legacy_migration.migrate_upload_files_to_blobs()

        assert removed == 1
        _assert_destroyed(litter, original)

    def test_the_premigrate_snapshot_is_overwritten(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch, no_unlink
    ) -> None:
        import config
        import legacy_migration

        monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "app.db"))
        snapshot = legacy_migration.premigrate_backup_path()
        original = b"a snapshot of the attachment state" * 30
        snapshot.write_bytes(original)

        legacy_migration.discard_premigrate_backup()

        _assert_destroyed(snapshot, original)


def _junction(link: Path, target: Path) -> bool:
    """A real NTFS junction, or False when this machine cannot make one."""
    import subprocess
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True, shell=False)
    return result.returncode == 0 and link.exists()


class TestNothingWalksThroughAJunction:
    """The attack the primitive exists to stop, at the callers that walk.

    Checking each FILE is not a guard. A file reached THROUGH a junction has
    an ordinary path of its own: is_redirected() answers False about it and
    the overwrite lands on whatever the junction points at. Only the ancestor
    is the reparse point, so the ancestor is where the check has to be - which
    is why the walk lives in one function instead of at each call site.
    """

    def _victim(self, tmp_path: Path) -> tuple[Path, Path, bytes]:
        outside = tmp_path / "documents"
        outside.mkdir()
        victim = outside / "notes.txt"
        content = b"the user's own notes, nowhere near this app" * 20
        victim.write_bytes(content)
        return outside, victim, content

    def test_shred_tree_does_not_follow_one(self, tmp_path: Path) -> None:
        outside, victim, content = self._victim(tmp_path)
        root = tmp_path / "cache"
        root.mkdir()
        (root / "ordinary.bin").write_bytes(b"real cache entry")
        if not _junction(root / "trap", outside):
            pytest.skip("this machine cannot create a junction")

        removed, stuck, pruned = secure_delete.shred_tree(root)

        assert pruned is True
        assert victim.read_bytes() == content, "walked through the junction"
        assert removed == 1, "the real entry should still have been destroyed"

    def test_deleting_a_voice_does_not_follow_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The regression: the first fix here used rglob with a per-file
        # redirect check, which is the shape that does not work.
        from tts import refs

        outside, victim, content = self._victim(tmp_path)
        refs_root = tmp_path / "refs"
        folder = refs_root / "voice3"
        folder.mkdir(parents=True)
        (folder / "ref.wav").write_bytes(b"RIFF" + b"recorded" * 20)
        monkeypatch.setattr(refs, "refs_dir", lambda: refs_root)
        if not _junction(folder / "trap", outside):
            pytest.skip("this machine cannot create a junction")

        result = refs.delete("voice3")

        assert victim.read_bytes() == content, "walked through the junction"
        assert result is False, "a folder it refused to empty is not deleted"


class TestTheFourWalksThatFollowedOne:
    """K-03: four sweeps that walk a directory and never ask what it is.

    The class above covers the sites that already refuse. These are the ones
    that did not, and the shape is identical in all four: enumerate a
    directory whose name the app chose, delete what is inside it. Put a
    junction at that name and the deletion lands on the target - somebody's
    Music library, somebody's Documents.

    They are ordered here by how often they fire, because that is what decides
    how long a user's files survive: once per spoken SENTENCE, then twice per
    unlock, then once per upload. Their guarded siblings sit a few lines away
    in the same files, so what was missing was never a design, only a line.
    """

    def _victim(self, tmp_path: Path) -> tuple[Path, Path, bytes]:
        outside = tmp_path / "somebody-elses-music"
        outside.mkdir()
        victim = outside / "speak-not-ours.wav"
        content = b"RIFF" + b"a recording that is not this app's" * 20
        victim.write_bytes(content)
        return outside, victim, content

    def _redirected_cache(self, tmp_path, monkeypatch) -> Path:
        """config.TTS_CACHE_DIR pointed at a junction, the way a user would.

        Nothing here is exotic. Somebody with a large audio cache moves it to
        another drive and junctions the old name back - Windows has shipped
        `mklink /J` for that since Vista.
        """
        import config
        cache = tmp_path / "cache"
        monkeypatch.setattr(config, "TTS_CACHE_DIR", str(cache))
        return cache

    def test_the_per_sentence_trim_does_not_follow_one(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The highest-frequency deletion in the app: once per synthesised
        # sentence, through _next_out_path. A stale file in the junction
        # target is gone before the second sentence of the first reply.
        from tts.host import VoiceHost

        outside, victim, content = self._victim(tmp_path)
        stale = time.time() - float(config.TTS_CACHE_MAX_AGE_S) - 60
        os.utime(victim, (stale, stale))
        cache = self._redirected_cache(tmp_path, monkeypatch)
        if not _junction(cache, outside):
            pytest.skip("this machine cannot create a junction")

        VoiceHost()._next_out_path()

        assert victim.read_bytes() == content, "walked through the junction"

    def test_the_unlock_sweep_does_not_follow_one(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Worse than the trim in reach, if not in frequency: no name prefix
        # and no age cutoff, so every .wav in the target goes, however new and
        # whoever made it. Runs unattended on every unlock and every init.
        from routers.vault import _purge_voice_cache

        outside, victim, content = self._victim(tmp_path)
        cache = self._redirected_cache(tmp_path, monkeypatch)
        if not _junction(cache, outside):
            pytest.skip("this machine cannot create a junction")

        _purge_voice_cache()

        assert victim.read_bytes() == content, "walked through the junction"

    def test_the_upload_migration_does_not_follow_one(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The one no defect record had, found by sweeping for the shape.

        It runs on the unlock bootstrap and shreds the user's plaintext image
        uploads. Its only check is `entry.is_symlink()`, which is precisely
        the check is_redirected's docstring exists to say does not catch a
        junction - a junction is a reparse point that islink() calls False.
        """
        import legacy_migration

        outside = tmp_path / "somebody-elses-pictures"
        outside.mkdir()
        # Named the way the migration expects, so nothing but the container
        # check stands between it and the file.
        victim = outside / ("a" * 64 + ".png")
        content = b"\x89PNG" + b"a picture that is not this app's" * 20
        victim.write_bytes(content)
        uploads = tmp_path / "uploads"
        monkeypatch.setattr(config, "UPLOADS_DIR", str(uploads))
        if not _junction(uploads, outside):
            pytest.skip("this machine cannot create a junction")

        legacy_migration.migrate_upload_files_to_blobs()

        assert victim.read_bytes() == content, "walked through the junction"

    def test_replacing_a_clip_does_not_follow_one(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # refs.delete twelve lines below this one refuses. save_upload does
        # not, and it deletes by suffix rather than by name, so every audio
        # file in the target goes when the user replaces one clip.
        from tts import refs

        outside = tmp_path / "somebody-elses-music"
        outside.mkdir()
        victim = outside / "wedding.wav"
        content = b"RIFF" + b"a recording that is not this app's" * 20
        victim.write_bytes(content)
        refs_root = tmp_path / "refs"
        refs_root.mkdir()
        monkeypatch.setattr(refs, "refs_dir", lambda: refs_root)
        if not _junction(refs_root / "voice9", outside):
            pytest.skip("this machine cannot create a junction")

        try:
            refs.save_upload("voice9", "new.wav", _valid_wav())
        except Exception:
            # Whether the upload itself succeeds is a different question. The
            # assertion below is the one this test is about.
            pass

        assert victim.read_bytes() == content, "walked through the junction"


class TestAFolderThatIsNotThereIsNotAJunction:
    """The false positive the guards would otherwise have, on every launch.

    is_redirected fails closed: it answers True for a path it cannot stat,
    ENOENT included. So `if is_redirected(dir): warn and return` fires on a
    folder that simply does not exist yet - which is the normal state of the
    voice cache on an install where nobody has used voice, and of the uploads
    folder on every install that never had legacy files.

    The idiom the codebase already had for this is is_dir() first, in
    secure_delete.shred_tree and browser_profile. These are the tests that keep
    the guards using it.
    """

    def test_the_unlock_sweep_says_nothing_about_a_folder_that_is_absent(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch, caplog
    ) -> None:
        from routers.vault import _purge_voice_cache

        monkeypatch.setattr(config, "TTS_CACHE_DIR",
                            str(tmp_path / "never-created"))
        with caplog.at_level(logging.WARNING):
            _purge_voice_cache()

        assert "redirected" not in caplog.text, (
            "every launch of a voice-less install would say this")

    def test_the_upload_migration_still_exits_clean_when_absent(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch, caplog
    ) -> None:
        import legacy_migration

        monkeypatch.setattr(config, "UPLOADS_DIR", str(tmp_path / "gone"))
        with caplog.at_level(logging.WARNING):
            migrated, failed, removed =                 legacy_migration.migrate_upload_files_to_blobs()

        assert (migrated, failed, removed) == (0, set(), 0)
        assert "redirected" not in caplog.text

    def test_the_per_sentence_trim_still_trims_an_ordinary_folder(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The discriminating half for all three: a guard that refused
        # everything would satisfy the two tests above and stop the app
        # cleaning up at all.
        from tts.host import VoiceHost

        cache = tmp_path / "cache"
        cache.mkdir()
        monkeypatch.setattr(config, "TTS_CACHE_DIR", str(cache))
        stale_file = cache / "speak-old.wav"
        stale_file.write_bytes(b"RIFF")
        stale = time.time() - float(config.TTS_CACHE_MAX_AGE_S) - 60
        os.utime(stale_file, (stale, stale))

        VoiceHost()._next_out_path()

        assert not stale_file.exists(), "the guard stopped ordinary trimming"


class TestAFailedRemovalIsNotCountedAsOne:
    """A summary line that says "removed 3" about a file still on disk.

    Worse than a missing count: it is the reason nobody looks again.
    """

    def test_the_migration_does_not_count_a_file_it_could_not_remove(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import config
        import legacy_migration

        uploads = tmp_path / "uploads"
        uploads.mkdir()
        litter = uploads / "halfwritten.tmp"
        litter.write_bytes(b"a picture the user never finished sending")
        monkeypatch.setattr(config, "UPLOADS_DIR", str(uploads))
        monkeypatch.setattr(secure_delete, "shred", lambda path: False)

        migrated, failed, removed = \
            legacy_migration.migrate_upload_files_to_blobs()

        assert removed == 0
        assert litter.exists()

    def test_a_rotation_reports_a_backup_it_could_not_destroy(
        self, client, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The complete vault under the passphrase being revoked. Its failure
        # used to answer {"unrevoked": []} - a clean rotation, reported
        # honestly, that had revoked nothing about this file.
        #
        # Through the ROUTE, not through a copy of its logic. The first cut of
        # this test monkeypatched discard and then re-ran the append itself in
        # the test body, so deleting the line under test would not have failed
        # it: a tautology wearing a regression test's clothes.
        import config
        import database
        import routers.vault as vault_router
        import vault_state

        vdir = tmp_path / "rotating"
        vdir.mkdir()
        db_path = str(vdir / "app.db")
        monkeypatch.setattr(config, "DB_PATH", db_path)
        monkeypatch.setattr(database, "DB_PATH", db_path)
        vault_state.clear_key()
        assert client.post("/api/v1/vault/init",
                           json={"passphrase": "seaside-orchid-9"}
                           ).status_code == 200

        refused: list[str] = []

        def refuse(path):
            if ".rekey.bak-" in Path(path).name:
                refused.append(Path(path).name)
                return False
            return True

        monkeypatch.setattr(vault_router.secure_delete, "discard", refuse)

        response = client.post("/api/v1/vault/change-passphrase", json={
            "old_passphrase": "seaside-orchid-9",
            "new_passphrase": "harbour-lantern-4",
        })
        assert response.status_code == 200, response.text

        assert refused, "the rotation never tried to remove its backup"
        assert response.json()["unrevoked"] == refused
