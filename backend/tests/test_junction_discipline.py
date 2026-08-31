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
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import browser_profile
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
    def test_it_refuses_a_redirected_ebwebview_parent(self, tmp_path) -> None:
        profile = tmp_path / "profile"
        elsewhere = tmp_path / "someone-elses-documents"
        keepsake = elsewhere / "keepsake.txt"
        elsewhere.mkdir()
        keepsake.write_text("not ours", encoding="utf-8")
        junction(profile / "EBWebView", elsewhere)

        assert browser_profile.block_crash_reporting(str(profile)) is False

        # Nothing created THROUGH the junction, and nothing destroyed.
        assert keepsake.read_text(encoding="utf-8") == "not ours"
        assert not (elsewhere / "Crashpad").exists()

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
