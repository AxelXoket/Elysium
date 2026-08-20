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
