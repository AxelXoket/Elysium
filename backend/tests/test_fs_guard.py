"""The guard that keeps the suite off this machine's own data, and its tests.

Written the same way as test_egress_guard.py, and for the same reason: a guard
is only worth having if it fires. So this attacks it - every primitive the app
reaches the filesystem through, and every real path it must protect - and then
attacks it from the other side, because a guard that refused everything would
stop the suite rather than protect it.

One property is load-bearing and easy to lose: READS ARE FINE. The suite reads
its own source constantly, hashes the 29 MB exe, and opens the packaged tree.
A guard that refused those would be turned off within a day.
"""
from __future__ import annotations

import io
import os
import shutil
import tempfile
from pathlib import Path

import pytest

import config
from tests import fs_guard
from tests.fs_guard import ForbiddenWrite


def real_vault() -> Path:
    """The development vault: the file the developer opens Elysium with."""
    return Path(config.DB_PATH)


class TestItRefusesEveryDoorToTheRealData:
    def test_open_for_writing_is_refused(self) -> None:
        with pytest.raises(ForbiddenWrite, match="app.db"):
            open(real_vault(), "wb")

    def test_append_and_update_modes_are_refused_too(self) -> None:
        # "w" is the obvious one. A guard that only knew about it would let
        # through the two modes that damage an existing file rather than
        # replacing it.
        for mode in ("ab", "r+b"):
            with pytest.raises(ForbiddenWrite):
                open(real_vault(), mode)

    def test_the_path_methods_are_refused(self) -> None:
        # Path.write_bytes does not go through builtins.open - it goes through
        # io.open. Patching one and not the other leaves every one of these
        # working, which was true of the first draft.
        with pytest.raises(ForbiddenWrite):
            real_vault().write_bytes(b"x")
        with pytest.raises(ForbiddenWrite):
            real_vault().write_text("x")
        with pytest.raises(ForbiddenWrite):
            real_vault().unlink()
        with pytest.raises(ForbiddenWrite):
            (Path(config.DATA_DIR) / "voice" / "new").mkdir(parents=True)

    def test_both_ends_of_a_move_are_refused(self, tmp_path: Path) -> None:
        # Renaming AWAY from the vault destroys it just as thoroughly as
        # renaming onto it.
        decoy = tmp_path / "decoy"
        decoy.write_bytes(b"x")
        with pytest.raises(ForbiddenWrite):
            decoy.replace(real_vault())
        with pytest.raises(ForbiddenWrite):
            os.replace(str(decoy), str(real_vault()))
        with pytest.raises(ForbiddenWrite):
            real_vault().replace(decoy)

    def test_a_tree_removal_is_refused(self) -> None:
        with pytest.raises(ForbiddenWrite):
            shutil.rmtree(Path(config.TTS_DIR))

    def test_opening_the_vault_as_a_database_is_refused(self) -> None:
        # connect() creates the file, so it is a write. And it is not stdlib
        # sqlite3: the vault driver is sqlcipher3, and patching the wrong one
        # would guard nothing.
        from sqlcipher3 import dbapi2

        with pytest.raises(ForbiddenWrite):
            dbapi2.connect(str(real_vault()))

    @pytest.mark.parametrize("name", [
        "salt.bin", "verifier.bin", "kdf.json", "vault.recovery",
    ])
    def test_the_identity_files_are_guarded_by_name(self, name: str) -> None:
        # The near-miss this guard was opened for: one vault call inside the
        # wrong try block would have rewritten the developer's own identity
        # files. vault.recovery joins them because it is the same material:
        # overwriting it would destroy the copy that exists to survive the
        # loss of the others.
        with pytest.raises(ForbiddenWrite):
            (Path(config.DATA_DIR) / name).write_bytes(b"x")

    def test_the_spoken_audio_the_suite_was_already_destroying(self) -> None:
        """Not a hypothetical. This one was happening on every run.

        TTS_CACHE_DIR defaulted to backend/voice/cache and nothing redirected
        it, so the unlock bootstrap's sweep - which overwrites with random
        bytes before unlinking - ran over the developer's own spoken replies
        twenty-eight times in test_vault.py alone.

        The conftest fixture points the setting at tmp now. This asserts the
        net under it: the real folder is refused even if something reaches for
        it by its true path.
        """
        real_cache = Path(config.DATA_DIR) / "voice" / "cache" / "speak-x.wav"
        with pytest.raises(ForbiddenWrite):
            real_cache.write_bytes(b"RIFF")


class TestItLetsTheSuiteWork:
    def test_reading_the_real_vault_is_not_refused(self) -> None:
        # Constant, legitimate, and changes nothing. test_artifact_gate.py
        # hashes this very file to prove the exe did not touch it.
        if not real_vault().is_file():
            pytest.skip("no development vault on this machine")
        assert real_vault().read_bytes()[:1]

    def test_reading_source_is_not_refused(self) -> None:
        assert Path(__file__).read_text(encoding="utf-8")

    def test_writing_under_tmp_path_is_not_refused(self, tmp_path: Path
                                                   ) -> None:
        # The discriminating half. A guard that refused every write would
        # satisfy every assertion above and stop the suite dead.
        target = tmp_path / "ordinary.txt"
        target.write_text("fine")
        assert target.read_text() == "fine"
        (tmp_path / "sub").mkdir()
        io.open(tmp_path / "raw.bin", "wb").close()
        shutil.rmtree(tmp_path / "sub")

    def test_a_temp_database_is_not_refused(self, tmp_path: Path) -> None:
        from sqlcipher3 import dbapi2

        dbapi2.connect(str(tmp_path / "fine.db")).close()

    def test_the_redirected_voice_cache_is_writable(self) -> None:
        # The autouse fixture points it at tmp. If that ever stopped working
        # this would go red rather than the suite quietly shredding real audio
        # again.
        cache = Path(config.TTS_CACHE_DIR)
        cache.mkdir(parents=True, exist_ok=True)
        (cache / "speak-test.wav").write_bytes(b"RIFF")
        assert Path(config.DATA_DIR) not in cache.parents


class TestTheGuardKnowsWhatItIsGuarding:
    def test_it_derives_the_list_from_config_rather_than_repeating_it(
        self,
    ) -> None:
        """The gate contract's first rule, applied to this gate.

        A hand-written list of paths goes stale in the direction that matters:
        config grows a directory, nobody comes back here, and the new one is
        unguarded while the guard still looks healthy. So the list is asked of
        config, and this is the floor under that.
        """
        guarded = fs_guard._real_data_paths()
        names = {str(p) for p in guarded}
        assert str(Path(config.DB_PATH)) in names
        assert str(Path(config.UPLOADS_DIR)) in names
        assert str(Path(config.TTS_DIR)) in names
        assert len(guarded) >= 6

    def test_it_says_which_path_and_which_root(self) -> None:
        # The message is the whole value of a guard that fires during somebody
        # else's test run. "assert False" would be a worse outcome than the
        # write it prevented.
        with pytest.raises(ForbiddenWrite) as caught:
            real_vault().write_bytes(b"x")
        text = str(caught.value)
        assert "app.db" in text
        assert "tmp_path" in text, "it does not say what to do instead"

    def test_something_that_is_not_a_path_does_not_crash_the_guard(
        self, tmp_path: Path
    ) -> None:
        # os.replace and friends accept file descriptors, and a guard that
        # tried to resolve one would turn an ordinary call into a TypeError
        # somewhere far from here.
        handle = os.open(str(tmp_path / "byfd.txt"), os.O_CREAT | os.O_WRONLY)
        try:
            assert os.write(handle, b"fine") == 4
        finally:
            os.close(handle)


class TestTheTwoHolesMeasuredOn22August:
    """Both were the same shape: a wrapper that ran and refused nothing.

    The first: `mkstemp` was wrapped at argument 0, which is the SUFFIX.
    Every call went through a guarded_call that checked ".tmp" against the
    vault paths, found no match, and let the write through. A wrapper that
    cannot fail is not a guard, and this one had been green since it was
    written.

    The second: the system temp directory was outside the deny-list
    altogether, which is where the upload path spools a body once it outgrows
    its buffer. `E-0593` says the voice route's clip goes with it, in the
    clear, and no test could see that because nothing here looked at temp.
    """

    def test_mkstemp_is_guarded_by_its_directory_not_its_suffix(self) -> None:
        """The discriminating case: a suffix that would match nothing, and a
        `dir` that must be refused. Before the fix this call succeeded."""
        with pytest.raises(fs_guard.ForbiddenWrite):
            tempfile.mkstemp(suffix=".json", dir=str(config.TTS_DIR))

    def test_mkstemp_is_guarded_when_the_directory_is_positional(self) -> None:
        with pytest.raises(fs_guard.ForbiddenWrite):
            tempfile.mkstemp(".json", "pre", str(config.TTS_DIR))

    def test_mkdtemp_is_guarded_too(self) -> None:
        """It has the same signature and was not wrapped at all."""
        with pytest.raises(fs_guard.ForbiddenWrite):
            tempfile.mkdtemp(dir=str(config.TTS_DIR))

    def test_a_temp_directory_a_test_asks_for_is_still_allowed(
            self, tmp_path: Path) -> None:
        """Ground control for the three above, and the reason the temp rule
        asks WHO is writing. A guard that refused this would fail the suite
        for doing the right thing."""
        with tempfile.TemporaryDirectory(dir=str(tmp_path)) as scratch:
            assert Path(scratch).is_dir()

    def test_the_suites_own_scratch_in_the_temp_root_is_allowed(self) -> None:
        """`test_image_verify_unlock.py` does exactly this, on purpose."""
        with tempfile.TemporaryDirectory(prefix="elysium-fsguard-") as made:
            assert Path(made).parent == Path(tempfile.gettempdir()).resolve()

    def test_application_code_writing_into_the_temp_root_is_refused(
            self) -> None:
        """The half that matters, driven through a frame that is NOT a test.

        The stack walk decides on the first frame somebody in this repository
        wrote. Calling from here would be judged a test and allowed, which is
        correct and proves nothing, so the call is made from a module outside
        the tests directory.
        """
        import config as not_a_test_module

        marker = Path(tempfile.gettempdir()) / "elysium-fsguard-probe.tmp"
        # Cleared first, deliberately. Proving the guard by mutation LEAVES
        # this file behind - that is what the mutation demonstrates - and a
        # later run would then read the leftover as a live failure. The
        # assertion below is about this call, not about the folder's history.
        marker.unlink(missing_ok=True)
        caller = compile(
            "open(str(target), 'wb').close()",
            not_a_test_module.__file__,
            "exec",
        )
        with pytest.raises(fs_guard.ForbiddenWrite):
            exec(caller, {"open": open, "target": marker})
        assert not marker.exists()


class TestTheGuardCannotSwitchItselfOff:
    """The three ways this protection was conditional.

    It is the last thing between a test suite and the owner's own vault - the
    real `salt.bin`, `verifier.bin` and `kdf.json`, which are not recoverable
    once overwritten. It was conditional on an environment variable being
    set, and on a path snapshot taken before the test body could change it.
    """

    def test_the_packaged_vault_is_guarded_by_name(self, monkeypatch) -> None:
        """The branch nothing asserted.

        `%LOCALAPPDATA%\\Elysium` is the only entry in the list that names
        the REAL vault during a test run: `config` reports the development
        directory, because it computes the packaged one only in a frozen
        build. Every existing test here drives the development branch.

        The write is aimed at a file inside a SUBDIRECTORY THAT DOES NOT
        EXIST, and that is not fastidiousness. A mutation experiment on this
        very guard removed the packaged path from the list, ran this test,
        and the write landed in the owner's real data directory - a harmless
        probe file, removed immediately, but the shape of the accident is the
        one that is not recoverable if it lands on `salt.bin`.

        With a missing parent, an unguarded write raises FileNotFoundError
        instead of creating anything. So the test fails when the guard fails,
        and it cannot write to the real vault in either case.
        """
        import os

        local = os.environ.get("LOCALAPPDATA")
        assert local, "ground: this machine has the variable the guard needs"
        parent = Path(local) / "Elysium" / "fs-guard-probe-dir-never-created"
        assert not parent.exists(), "ground: the probe directory is absent"
        target = parent / "probe.tmp"

        with pytest.raises(fs_guard.ForbiddenWrite):
            target.write_text("this must never land", encoding="utf-8")
        assert not parent.exists(), "the guard let a directory be created"

    def test_it_refuses_to_run_at_all_without_the_variable(
            self, monkeypatch) -> None:
        """Silence was the defect. With LOCALAPPDATA unset the list simply
        stopped covering the real vault, every write to it became legal, and
        the suite went on passing - a protection that switches itself off
        according to the environment is not one."""

        monkeypatch.delenv("LOCALAPPDATA", raising=False)

        with pytest.raises(RuntimeError, match="LOCALAPPDATA"):
            fs_guard._real_data_paths()

    def test_it_follows_a_config_that_moved_to_a_real_directory(
            self, monkeypatch) -> None:
        """The snapshot timing, and the reason the answer is a UNION.

        The guarded list is built at fixture setup, before the test body
        runs, and that timing is right for the ordinary case: a test points
        `config.DB_PATH` at its own tmp_path, and a list rebuilt afterwards
        would start guarding the fixture - every test refused by its own
        protection.

        It is wrong for the other case. Two tests here set `sys.frozen` and
        reload `config`, which moves `DATA_DIR` to the PACKAGED location, and
        the snapshot then describes a directory the application would no
        longer write to. So the list is re-read as well, and anything the
        re-read finds OUTSIDE the temp root is added.

        Both halves are asserted: a real directory config moved to is
        guarded, and a fixture directory it moved to is not.
        """
        import os

        import config

        local = os.environ.get("LOCALAPPDATA")
        assert local, "ground: the variable the guard needs"
        # A path outside the temp root that does not exist, so nothing can be
        # created here whatever the guard does.
        moved = Path(local) / "Elysium-not-a-real-directory-for-this-test"
        monkeypatch.setattr(config, "DATA_DIR", str(moved))

        guarded = fs_guard._real_data_paths()
        assert any(str(moved) in str(path) for path in guarded), (
            "the guard is still describing the directory config used to have")

        with pytest.raises(fs_guard.ForbiddenWrite):
            (moved / "salt.bin").write_text("x", encoding="utf-8")
        assert not moved.exists(), "the guard let a directory be created"

    def test_a_fixture_directory_is_not_guarded(
            self, monkeypatch, tmp_path) -> None:
        """THE ground control for the union, and the reason it is one.

        Pointing config at a tmp_path is what almost every test in this suite
        does. If the re-read guarded that too, the protection would refuse
        the whole suite - which is a failure mode that looks like safety and
        is not.
        """
        import config

        fixture = tmp_path / "fixture-data"
        fixture.mkdir()
        monkeypatch.setattr(config, "DATA_DIR", str(fixture))

        (fixture / "salt.bin").write_text("x", encoding="utf-8")
        assert (fixture / "salt.bin").read_text(encoding="utf-8") == "x"

