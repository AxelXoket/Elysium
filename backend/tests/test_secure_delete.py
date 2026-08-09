"""The one deletion primitive, tested where it lives.

Both callers - the browser-profile purge and the vault's plaintext-backup
discard - inherit their safety from `shred()`. Its guards were only ever
exercised THROUGH those callers, and browser_profile filters redirected names
itself before it ever reaches here, so deleting the guard inside `shred()`
left the whole suite green. A primitive whose protection is only tested via
one caller is protected for one caller.

Both refusals were found by trying them, not by reasoning:

  * a junction is not a symlink to `os.path.islink()`, so `os.walk` marches
    into it and the sweep shreds whatever it points at;
  * a hardlink is not a reparse point at all, so it slips the junction guard
    entirely - and because this overwrites BEFORE unlinking, it corrupts the
    file the other name still opens.

Neither needs administrator rights to create.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

import secure_delete

WINDOWS_ONLY = pytest.mark.skipif(os.name != "nt", reason="NTFS link semantics")

SECRET = b"KEEP THIS" * 16


def _victim(folder: Path, name: str = "someones_notes.txt") -> Path:
    path = folder / name
    path.write_bytes(SECRET)
    return path


class TestItDeletesWhatItShould:
    def test_an_ordinary_file_is_overwritten_then_removed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = _victim(tmp_path, "leak.bin")
        monkeypatch.setattr(os, "unlink", lambda *a, **kw: None)

        assert secure_delete.shred(target) is True

        survivor = target.read_bytes()
        assert len(survivor) == len(SECRET)
        assert survivor != SECRET

    def test_it_really_removes_the_file(self, tmp_path: Path) -> None:
        target = _victim(tmp_path, "leak.bin")
        assert secure_delete.shred(target) is True
        assert not target.exists()

    def test_an_empty_file_is_still_removed(self, tmp_path: Path) -> None:
        target = tmp_path / "empty.bin"
        target.touch()
        assert secure_delete.shred(target) is True
        assert not target.exists()

    def test_a_missing_file_is_a_no_not_a_crash(self, tmp_path: Path) -> None:
        assert secure_delete.shred(tmp_path / "never") is False


@WINDOWS_ONLY
class TestItRefusesARedirectedName:
    def test_a_junction_is_left_alone(self, tmp_path: Path) -> None:
        import _winapi

        hidden = tmp_path / "elsewhere"
        hidden.mkdir()
        _victim(hidden)
        link = tmp_path / "looks-ordinary"
        _winapi.CreateJunction(str(hidden), str(link))

        assert secure_delete.is_redirected(link) is True
        assert secure_delete.shred(link) is False
        assert (hidden / "someones_notes.txt").read_bytes() == SECRET

    def test_an_ordinary_name_is_not_mistaken_for_one(self, tmp_path: Path) -> None:
        # Guard the guard: a check that answered True for everything would
        # stop this module deleting anything at all, silently.
        assert secure_delete.is_redirected(_victim(tmp_path)) is False


@WINDOWS_ONLY
class TestItRefusesASharedName:
    def test_a_hardlink_is_left_alone_and_so_is_its_twin(
        self, tmp_path: Path
    ) -> None:
        victim = _victim(tmp_path)
        alias = tmp_path / "app.db.plain.bak-999"
        os.link(victim, alias)

        assert secure_delete.is_shared(alias) is True
        assert secure_delete.shred(alias) is False
        assert victim.read_bytes() == SECRET, "the other name's bytes were destroyed"
        assert alias.exists()

    def test_a_file_with_one_name_is_not_treated_as_shared(
        self, tmp_path: Path
    ) -> None:
        assert secure_delete.is_shared(_victim(tmp_path)) is False

    def test_a_name_that_gains_a_twin_after_the_check_is_still_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The guards read the name; open() reads it again. In between, the
        name belongs to whoever can write to that directory - which is the same
        threat model is_redirected and is_shared were written under.

        Reproduced against the real module before the fix: is_shared answered
        honestly about the cache file, the name was swapped for a hardlink to a
        notes file, and shred() overwrote the notes and returned True. The swap
        below is that attack, performed at exactly the moment it opens.
        """
        notes = _victim(tmp_path)
        target = tmp_path / "cache.bin"
        target.write_bytes(b"junk" * 36)

        honest = secure_delete.is_shared

        def racing(path: Path) -> bool:
            answer = honest(path)
            alias = tmp_path / "alias.tmp"
            os.link(notes, alias)
            os.replace(alias, target)      # same name, somebody else's bytes
            return answer

        monkeypatch.setattr(secure_delete, "is_shared", racing)

        assert secure_delete.shred(target) is False
        assert notes.read_bytes() == SECRET, "the swapped-in file was destroyed"

    def test_a_name_that_becomes_a_different_file_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The same race without a hardlink: the name simply comes to mean a
        different file. Link counting cannot see this one; identity can."""
        notes = _victim(tmp_path)
        target = tmp_path / "cache.bin"
        target.write_bytes(b"junk" * 36)

        honest = secure_delete.is_shared

        def racing(path: Path) -> bool:
            answer = honest(path)
            os.replace(notes, target)
            return answer

        monkeypatch.setattr(secure_delete, "is_shared", racing)

        assert secure_delete.shred(target) is False
        assert target.read_bytes() == SECRET, "the swapped-in file was destroyed"

    def test_an_ordinary_file_that_nobody_touches_is_not_refused(
        self, tmp_path: Path
    ) -> None:
        # Guard the guard, again: an identity check that never matches would
        # stop this module deleting anything, and every caller of it treats
        # False as "left on disk" rather than as an error.
        target = _victim(tmp_path, "leak.bin")
        assert secure_delete.shred(target) is True
        assert not target.exists()

    def test_it_fails_closed_when_it_cannot_count_the_names(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Unknown must mean "do not touch". The opposite default turns an
        # unreadable stat into an overwrite of somebody else's file.
        target = _victim(tmp_path)

        def refuse(*args, **kwargs):
            raise OSError(5, "access denied")

        monkeypatch.setattr(os, "stat", refuse)
        assert secure_delete.is_shared(target) is True
