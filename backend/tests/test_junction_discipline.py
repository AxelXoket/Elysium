"""U-62 - two places that deleted or wrote without asking where the path led.

This tree has a settled discipline: before deleting a directory or writing
into one, check whether the name is a junction pointing somewhere else.
`host.py`'s cache trim does it, `routers/tts.py` does it, and
`browser_profile._profile_dirs` does it. Two places did not.

  * `block_crash_reporting` checked `Crashpad` itself but not its PARENT, and
    the check came AFTER `mkdir(parents=True, exist_ok=True)` - which
    succeeds quietly on an EBWebView that is already a junction. Everything
    after it was operating inside somebody else's directory.

  * `tts/provision.py` had nine `shutil.rmtree(..., ignore_errors=True)`
    calls and no check at all. MEASURED, because the obvious reading is
    wrong: `shutil.rmtree` already refuses a junction, raising
    `OSError("Cannot call rmtree on a symbolic link")` and deleting nothing.
    Those calls were never deleting through one. What `ignore_errors=True`
    does is swallow the refusal - nothing deleted, nothing logged, and the
    caller unpacks a fresh environment onto a name that still points at
    somebody else's folder. That failure needs a voice, not another guard.

A junction needs no privileges to create on Windows.

Since H-142 the discipline has a second half, and the first half alone was
half a defence. Refusing to delete THROUGH a redirected name stays. Refusing
to remove the redirected NAME does not: the entry stayed, the sweep stopped,
and one unprivileged `mklink /J` switched the whole module off for the life
of the install with no signal a user would ever see. The entry now goes; what
it points at is not touched.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

import browser_profile
import secure_delete
import config
from tts import provision


def junction(link: Path, target: Path) -> None:
    """Make `link` a junction to `target`, or skip the test.

    `mklink /J` needs no elevation. If the filesystem or the runner will not
    take one, the test cannot measure what it claims and says so instead of
    passing.
    """
    target.mkdir(parents=True, exist_ok=True)
    link.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True, text=True)
    if result.returncode != 0 or not link.exists():
        pytest.skip(f"no junction on this filesystem: {result.stderr.strip()}")


class TestTheCrashBlockerLooksAtItsParent:
    def test_a_redirected_ebwebview_parent_is_replaced_not_obeyed(
            self, tmp_path) -> None:
        """This used to return False and leave the junction standing.

        Both halves of that were wrong in the same direction. Nothing was
        created through the link, which is right and still holds - but the
        link stayed, so the block could never be established afterwards
        either. `mklink /J` needs no elevation, so one command turned crash
        reporting back on permanently and said so only once, in a warning
        nobody sees on the launch path.

        The link ENTRY is removed instead, and the folder it points at is not
        touched. A profile deliberately moved to another disk is the accepted
        cost, which is why the full path is logged rather than swallowed.
        """
        profile = tmp_path / "profile"
        elsewhere = tmp_path / "someone-elses-documents"
        keepsake = elsewhere / "keepsake.txt"
        elsewhere.mkdir()
        keepsake.write_text("not ours", encoding="utf-8")
        junction(profile / "EBWebView", elsewhere)

        assert browser_profile.block_crash_reporting(str(profile)) is True

        # Nothing created THROUGH the junction, and nothing destroyed.
        assert keepsake.read_text(encoding="utf-8") == "not ours"
        assert not (elsewhere / "Crashpad").exists()
        # And the block really is in place, on our own disk this time.
        assert (profile / "EBWebView" / "Crashpad").is_file()

    def test_it_still_blocks_a_normal_profile(self, tmp_path) -> None:
        """POSITIVE CONTROL. A guard that refuses everything would satisfy
        the test above and quietly switch off crash-report blocking."""
        profile = tmp_path / "profile"

        assert browser_profile.block_crash_reporting(str(profile)) is True
        assert (profile / "EBWebView" / "Crashpad").is_file()

    def test_it_works_on_a_profile_that_does_not_exist_yet(
            self, tmp_path) -> None:
        """GROUND CONTROL, and the reason the check is behind `is_dir()`.

        `_is_redirected` fails CLOSED on any OSError, ENOENT included, so a
        path that does not exist answers True. Written without that gate, the
        guard refuses every first launch - which looks like caution and is a
        silently disabled protection.
        """
        assert browser_profile.block_crash_reporting(
            str(tmp_path / "never-launched")) is True


class TestProvisionDoesNotDeleteThroughAJunction:
    def test_a_redirected_target_is_refused_OUT_LOUD(
            self, tmp_path, caplog) -> None:
        """MEASURED, and the queue's premise here was wrong.

        `shutil.rmtree` already refuses a junction - it raises
        `OSError("Cannot call rmtree on a symbolic link")` and deletes
        nothing, which the test below pins. So these call sites were never
        deleting through a junction.

        What they were doing is passing `ignore_errors=True`, which swallows
        that refusal entirely: nothing deleted, nothing said, and the caller
        goes on to unpack a fresh environment onto a name that still points
        at somebody else's folder. The refusal needs a voice, not another
        guard, and that is what is being measured here.
        """
        import logging

        elsewhere = tmp_path / "someone-elses-music"
        song = elsewhere / "song.mp3"
        elsewhere.mkdir()
        song.write_bytes(b"not ours")

        cache = tmp_path / "uv-cache"
        junction(cache, elsewhere)

        with caplog.at_level(logging.WARNING):
            provision._rmtree(cache)

        assert song.exists()
        assert any("redirected" in r.message for r in caplog.records), (
            "the refusal was silent, which is the defect")

    def test_the_stdlib_refusal_really_is_silent(self, tmp_path) -> None:
        """The GROUND under the test above: proof that `ignore_errors=True`
        hides the whole thing, so the log line is a real addition rather than
        a restatement of something already visible."""
        import shutil

        elsewhere = tmp_path / "elsewhere"
        keepsake = elsewhere / "keepsake.txt"
        elsewhere.mkdir()
        keepsake.write_text("not ours", encoding="utf-8")
        link = tmp_path / "link"
        junction(link, elsewhere)

        # No exception, no deletion, no word.
        shutil.rmtree(link, ignore_errors=True)

        assert keepsake.exists()
        assert link.exists()
        with pytest.raises(OSError):
            shutil.rmtree(link)

    def test_an_ordinary_directory_is_still_deleted(self, tmp_path) -> None:
        """POSITIVE CONTROL. A guard that refuses every delete would pass the
        test above and leave a failed install permanently un-reinstallable."""
        env = tmp_path / "envs" / "piper"
        env.mkdir(parents=True)
        (env / "python.exe").write_bytes(b"x")

        provision._rmtree(env)

        assert not env.exists()

    def test_a_path_that_is_not_there_says_nothing(
            self, tmp_path, caplog) -> None:
        """GROUND CONTROL for the `is_dir()` gate, and it has to check the
        LOG rather than just the absence of an exception.

        `is_redirected` fails CLOSED, so without the gate a path that simply
        does not exist answers True and every missing `.staging` or `.old`
        sibling - the common case, on every install - warns that it refused
        to delete through a redirected name. A warning that fires on the
        happy path is how a real one stops being read.
        """
        import logging

        with caplog.at_level(logging.WARNING):
            provision._rmtree(tmp_path / "never-existed")

        assert not [r for r in caplog.records if "redirected" in r.message]


class TestWhatTheStdlibActuallyDoesWithAJunction:
    """Two measurements, kept because a decision rests on them.

    `block_crash_reporting` runs `shutil.rmtree` over the crash database after
    `shred_tree` has refused any redirected name inside it, and the obvious
    reading of that is wrong in both directions. These pin the real behaviour
    so the next person to weigh the trade is weighing it against facts.
    """

    def test_rmtree_removes_the_junction_it_will_not_follow(
            self, tmp_path) -> None:
        """It does NOT descend, so the target keeps every byte. It DOES
        unlink the junction entry, and on Windows that succeeds on a
        reparse-point directory. So the residue of the sweep is one deleted
        name, not the files behind it."""
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        keepsake = elsewhere / "keepsake.txt"
        keepsake.write_text("not ours", encoding="utf-8")
        root = tmp_path / "root"
        root.mkdir()
        junction(root / "trap", elsewhere)

        shutil.rmtree(root, ignore_errors=True)

        assert keepsake.read_text(encoding="utf-8") == "not ours",             "rmtree descended through the junction after all"
        assert not root.exists(),             "the junction did not even stop rmtree from taking the tree"

    def test_plain_os_walk_does_descend_into_one(self, tmp_path) -> None:
        """GROUND for why the prune in `shred_tree` cannot be handed to the
        standard library. `os.walk` with its default `followlinks=False`
        walks straight into a junction, because a junction is not a symlink
        and `os.path.islink` answers False about it. Any hand-rolled walk
        that trusted that flag would overwrite files outside the profile."""
        elsewhere = tmp_path / "elsewhere"
        (elsewhere / "inner").mkdir(parents=True)
        (elsewhere / "inner" / "keepsake.txt").write_text(
            "not ours", encoding="utf-8")
        root = tmp_path / "root"
        root.mkdir()
        junction(root / "trap", elsewhere)

        seen = {name for _d, _s, files in os.walk(root) for name in files}

        assert "keepsake.txt" in seen,             "os.walk stopped at the junction on its own; the prune is dead code"
        assert os.path.islink(root / "trap") is False,             "islink would have been enough, which it is not"

class TestAnOrdinaryNameIsNotCalledRedirected:
    """M5. Failing closed had a price, and it was paid in plaintext.

    Win32 path normalisation trims a trailing space or dot from the LAST
    component of a path. So `os.lstat(r"...\\reports ")` looks for `reports`,
    does not find it, and raises ENOENT - and the fail-closed branch turned
    that into "this is a redirected name". Two things followed, and the
    second one is the real defect:

      * the log said a junction had been refused when there was none, and
      * `shred_tree` pruned the folder, so the files inside it were never
        overwritten and never counted. They stayed readable, and the sweep
        reported success.

    The fix is a SECOND lstat through the extended-length prefix, which turns
    normalisation off. `os.path.abspath` cannot stand in for it - measured:
    it trims the space too. Only after that second attempt also fails does
    the answer fall back to closed.
    """

    @staticmethod
    def _odd_tree(tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        (root / "plain.json").write_text("ordinary", encoding="utf-8")
        odd = root / "reports "
        # mkdir through the extended-length prefix, which is the only way to
        # create the name; the ordinary path cannot even open it afterwards.
        os.mkdir("\\\\?\\" + str(odd))
        if not any(e.name == "reports " for e in os.scandir(root)):
            pytest.skip("this filesystem folded the trailing space away")
        secret = odd / "secret.json"
        secret.write_text('{"role":"user"}', encoding="utf-8")
        return root, odd, secret

    def test_an_odd_name_is_not_called_redirected(self, tmp_path) -> None:
        root, odd, secret = self._odd_tree(tmp_path)
        assert secret.read_text(encoding="utf-8") == '{"role":"user"}', (
            "GROUND: the plaintext was never planted")

        assert secure_delete.is_redirected(odd) is False
        assert secure_delete.confirmed_reparse(odd) is False

        removed, stuck, pruned = secure_delete.shred_tree(root)

        assert pruned == [], (
            "an ordinary folder was still reported as a redirected name")
        assert removed == 2, (
            "the file under the odd name was not shredded; expected 2 "
            f"removals, got {removed} with {stuck} left behind")
        assert not secret.exists(), "the plaintext is still on disk"

    def test_a_real_junction_is_still_pruned_and_named(self, tmp_path) -> None:
        """POSITIVE CONTROL. Loosening the fail-closed branch must not
        loosen the guard it exists for."""
        elsewhere = tmp_path / "elsewhere"
        keepsake = elsewhere / "keepsake.txt"
        elsewhere.mkdir()
        keepsake.write_text("not ours", encoding="utf-8")
        root = tmp_path / "root"
        root.mkdir()
        (root / "ours.bin").write_bytes(b"cache entry")
        junction(root / "trap", elsewhere)

        assert secure_delete.is_redirected(root / "trap") is True
        assert secure_delete.confirmed_reparse(root / "trap") is True

        removed, _stuck, pruned = secure_delete.shred_tree(root)

        assert pruned == [str(root / "trap")]
        assert removed == 1
        assert keepsake.read_text(encoding="utf-8") == "not ours"

    def test_a_name_that_is_not_there_is_still_refused(self, tmp_path) -> None:
        """GROUND CONTROL. The fail-closed floor stays where it was: a path
        nothing can answer for is still treated as redirected, which is what
        every caller's `is_dir()` gate is written against."""
        assert secure_delete.is_redirected(tmp_path / "never-existed") is True
        assert secure_delete.confirmed_reparse(
            tmp_path / "never-existed") is False

    def test_an_ordinary_folder_is_not_redirected(self, tmp_path) -> None:
        """POSITIVE CONTROL for the ground control above: the fail-closed
        floor must not have swallowed the ordinary answer."""
        plain = tmp_path / "plain"
        plain.mkdir()
        assert secure_delete.is_redirected(plain) is False
        assert secure_delete.confirmed_reparse(plain) is False

    def test_a_trailing_dot_is_reported_rather_than_walked_past(
            self, tmp_path) -> None:
        """The other half of the name class, and the dangerous half.

        A trailing SPACE and a trailing DOT are trimmed by the same Win32
        rule, but they do not end the same way. `\\\\?\\` reaches a name
        ending in a space through the ordinary path afterwards, so the folder
        is walked and the plaintext in it is shredded. A name ending in a dot
        is still unreachable: `scandir` raises ENOENT, `os.walk` yields
        nothing for it, and with the default `onerror` it says nothing
        either - so the file inside survives a sweep that reports a clean
        tree, and `rmtree` is then authorised over a walk that never
        happened. The refusal has to reach `stuck`, which is the value every
        caller reads before it lets a plain unlink near the tree.
        """
        root = tmp_path / "root"
        root.mkdir()
        (root / "plain.json").write_text("ordinary", encoding="utf-8")
        odd = root / "reports."
        os.mkdir("\\\\?\\" + str(odd))
        if not any(e.name == "reports." for e in os.scandir(root)):
            pytest.skip("this filesystem folded the trailing dot away")
        secret = "\\\\?\\" + str(odd / "secret.json")
        with open(secret, "w", encoding="utf-8") as handle:
            handle.write('{"role":"user"}')

        removed, stuck, pruned = secure_delete.shred_tree(root)

        assert removed == 1, "the reachable file should still have gone"
        assert pruned == [], "an ordinary folder was called a redirected name"
        assert stuck, (
            "a folder the walk could not open was skipped in silence, and "
            "the plaintext inside it is still readable")
        assert os.path.exists(secret), (
            "GROUND: the fixture never planted anything under the odd name")
