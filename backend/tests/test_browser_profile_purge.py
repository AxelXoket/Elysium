"""The vault is encrypted. The browser's cache next to it was not.

WebView2 runs with a persistent profile so the wallpaper and the last-open chat
survive a restart, and Chromium duly wrote the /api JSON response bodies to
disk - the whole conversation, the character cards and the personas, as plain
readable JSON, outside the vault, surviving every lock. Anyone holding the disk
could skip app.db entirely and open the cache in a text editor.

no_store_api stopped new entries. Nothing removed the old ones, and nothing
stops the pattern returning if a route ever forgets the header. So the profile
gets emptied on both edges of a session.

These tests scan real bytes on a real tree. None of them reads the source of
the thing they are testing: a purge that quietly stopped covering a directory
would still pass a grep for its name, and fail here.
"""
from __future__ import annotations

import builtins
import os
from pathlib import Path

import pytest

import browser_profile

CANARY = b'[{"id":10,"chat_id":1,"role":"user","content":"selamlar madeline"}]'
CARD = b'{"name":"Madeline","first_mes":"hi","system_prompt":"you are"}'


def _write(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


@pytest.fixture()
def profile(tmp_path: Path) -> Path:
    """A WebView2 profile shaped like the real one, seeded with real leaks."""
    root = tmp_path / "webview"
    default = root / "EBWebView" / "Default"

    # The leak, exactly where it was found on the real machine.
    _write(default / "Cache" / "Cache_Data" / "f_000066", CANARY)
    _write(default / "Cache" / "Cache_Data" / "f_00002b", CARD)
    _write(default / "Cache" / "Cache_Data" / "data_3", CANARY * 4)
    _write(default / "Network" / "Cookies", b"session-ish")
    _write(default / "blob_storage" / "abc" / "0", CANARY)
    _write(default / "Service Worker" / "CacheStorage" / "x" / "y", CARD)
    _write(default / "Session Storage" / "000003.log", CANARY)
    _write(default / "History", CANARY)
    _write(default / "DIPS", b"visit state")

    # A renderer minidump holds the decrypted DOM.
    _write(root / "EBWebView" / "Crashpad" / "reports" / "a.dmp", CANARY)

    # The things the persistent profile EXISTS for. Must survive.
    _write(default / "Local Storage" / "leveldb" / "000003.log", b"font-size:16")
    _write(default / "IndexedDB" / "wallpaper" / "1.ldb", b"PNGDATA")
    _write(default / "WebStorage" / "state", b"last-chat:7")
    _write(default / "Preferences", b'{"ui":"dark"}')
    # Compiled bytecode for our own bundle - wiping it costs startup, protects
    # nothing.
    _write(default / "Code Cache" / "js" / "0f_0", b"v8-bytecode")
    return root


def _files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file()]


def _leaks(root: Path) -> list[Path]:
    found = []
    for path in _files(root):
        blob = path.read_bytes()
        if b'"role":' in blob or b"system_prompt" in blob or b"first_mes" in blob:
            found.append(path)
    return found


class TestPurgeRemovesConversation:
    def test_no_byte_of_the_conversation_survives(self, profile: Path) -> None:
        assert _leaks(profile), "fixture did not actually plant a leak"
        browser_profile.purge(profile)
        assert _leaks(profile) == []

    def test_reports_how_many_it_removed(self, profile: Path) -> None:
        assert browser_profile.purge(profile) == 10

    def test_crash_reports_go_too(self, profile: Path) -> None:
        browser_profile.purge(profile)
        crashpad = profile / "EBWebView" / "Crashpad" / "reports"
        assert not crashpad.exists()

    def test_history_and_visit_state_go_too(self, profile: Path) -> None:
        default = profile / "EBWebView" / "Default"
        browser_profile.purge(profile)
        assert not (default / "History").exists()
        assert not (default / "DIPS").exists()

    def test_a_second_profile_is_not_overlooked(self, tmp_path: Path) -> None:
        # Chromium can create "Profile 1" alongside "Default". Sweeping only
        # the name we happen to have seen would leave a whole conversation.
        root = tmp_path / "webview"
        _write(root / "EBWebView" / "Profile 1" / "Cache" / "f_1", CANARY)
        browser_profile.purge(root)
        assert _leaks(root) == []


class TestPurgeKeepsWhatTheProfileIsFor:
    def test_settings_and_wallpaper_survive_intact(self, profile: Path) -> None:
        default = profile / "EBWebView" / "Default"
        browser_profile.purge(profile)
        assert (default / "Local Storage" / "leveldb" / "000003.log").read_bytes() \
            == b"font-size:16"
        assert (default / "IndexedDB" / "wallpaper" / "1.ldb").read_bytes() \
            == b"PNGDATA"
        assert (default / "WebStorage" / "state").read_bytes() == b"last-chat:7"
        assert (default / "Preferences").read_bytes() == b'{"ui":"dark"}'

    def test_compiled_bundle_bytecode_survives(self, profile: Path) -> None:
        # Startup cost, not a secret: this is OUR bundle, already on disk.
        kept = profile / "EBWebView" / "Default" / "Code Cache" / "js" / "0f_0"
        browser_profile.purge(profile)
        assert kept.read_bytes() == b"v8-bytecode"


class TestPurgeShredsRatherThanUnlinks:
    def test_bytes_are_overwritten_before_the_file_is_removed(
        self, profile: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Neuter every removal so the file is still there to inspect. What is
        # left must not be the conversation - proving the overwrite happens
        # first, so an undelete tool recovers noise rather than the chat.
        target = profile / "EBWebView" / "Default" / "Cache" / "Cache_Data" / "f_000066"
        monkeypatch.setattr(os, "unlink", lambda *a, **kw: None)
        monkeypatch.setattr(os, "remove", lambda *a, **kw: None)

        browser_profile.purge(profile)

        survivor = target.read_bytes()
        assert len(survivor) == len(CANARY)
        assert survivor != CANARY
        assert b"madeline" not in survivor.lower()


class TestPurgeNeverBreaksTheLaunch:
    def test_a_missing_profile_is_not_an_error(self, tmp_path: Path) -> None:
        assert browser_profile.purge(tmp_path / "never-created") == 0

    def test_an_empty_profile_is_not_an_error(self, tmp_path: Path) -> None:
        (tmp_path / "webview").mkdir()
        assert browser_profile.purge(tmp_path / "webview") == 0

    def test_a_locked_file_does_not_stop_the_rest(
        self, profile: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The browser still holds one entry open. Every other entry must still
        # go: a partial purge beats an aborted one.
        default = profile / "EBWebView" / "Default"
        locked = default / "Cache" / "Cache_Data" / "f_000066"

        def is_locked(path: object) -> bool:
            return isinstance(path, (str, os.PathLike)) and Path(path) == locked

        real_open = builtins.open

        def refuse_open(file, *args, **kwargs):  # type: ignore[no-untyped-def]
            if is_locked(file):
                raise PermissionError(32, "in use by another process")
            return real_open(file, *args, **kwargs)

        real_unlink = os.unlink

        def refuse_unlink(path, **kwargs):  # type: ignore[no-untyped-def]
            if is_locked(path):
                raise PermissionError(32, "in use by another process")
            return real_unlink(path, **kwargs)

        monkeypatch.setattr(builtins, "open", refuse_open)
        monkeypatch.setattr(os, "unlink", refuse_unlink)

        browser_profile.purge(profile)

        assert locked.exists(), "the locked file should have been left alone"
        assert not (default / "Cache" / "Cache_Data" / "data_3").exists()
        assert not (default / "History").exists()

    def test_purging_twice_is_harmless(self, profile: Path) -> None:
        first = browser_profile.purge(profile)
        second = browser_profile.purge(profile)
        assert first > 0
        assert second == 0


@pytest.mark.skipif(os.name != "nt", reason="NTFS junctions")
class TestPurgeRefusesToFollowRedirectedNames:
    """The first version of this purge deleted files it was never pointed at.

    An NTFS junction reads as an ordinary directory to the checks that look
    obvious: os.path.islink() says False, DirEntry.is_dir(follow_symlinks=
    False) says True, and os.walk marches straight through. Creating one needs
    no administrator rights. So any code running as this user could replace a
    cache folder with a junction to the user's documents and let the purge
    that runs on every launch shred them - which it did, twice, in a sandbox,
    before this class existed.

    Four positions, because a guard on only the obvious one is not a guard.
    """

    @staticmethod
    def _victim(base: Path) -> Path:
        victim = base / "Documents"
        victim.mkdir()
        (victim / "notes.txt").write_text("MUST SURVIVE", encoding="utf-8")
        (victim / "taxes.pdf").write_bytes(b"%PDF-1.7")
        return victim

    @staticmethod
    def _intact(victim: Path) -> bool:
        return (victim.is_dir()
                and sorted(p.name for p in victim.iterdir())
                == ["notes.txt", "taxes.pdf"]
                and (victim / "notes.txt").read_text(encoding="utf-8")
                == "MUST SURVIVE")

    @pytest.mark.parametrize("where", [
        "webview/EBWebView/Default/Cache",          # the swept folder itself
        "webview/EBWebView/Default/Network/inner",  # nested inside a swept one
        "webview/EBWebView/Default",                # the whole profile
        "webview/EBWebView",                        # the profile root
    ])
    def test_files_behind_a_junction_are_left_alone(
        self, tmp_path: Path, where: str
    ) -> None:
        import _winapi

        victim = self._victim(tmp_path)
        link = tmp_path / where
        link.parent.mkdir(parents=True, exist_ok=True)
        _winapi.CreateJunction(str(victim), str(link))

        browser_profile.purge(tmp_path / "webview")

        assert self._intact(victim), f"purge destroyed files via {where}"

    def test_a_junction_does_not_stop_the_real_purge(self, tmp_path: Path) -> None:
        # Refusing to follow the trap must not turn into refusing to work: the
        # conversation sitting in a genuine folder still has to go.
        import _winapi

        victim = self._victim(tmp_path)
        default = tmp_path / "webview" / "EBWebView" / "Default"
        leak = _write(default / "Network" / "Cookies", CANARY)
        (default / "Cache").parent.mkdir(parents=True, exist_ok=True)
        _winapi.CreateJunction(str(victim), str(default / "Cache"))

        browser_profile.purge(tmp_path / "webview")

        assert self._intact(victim)
        assert not leak.exists(), "the genuine leak survived"

    def test_the_junction_itself_is_left_in_place(self, tmp_path: Path) -> None:
        # Deleting it would be a second surprise, and removing a link is not
        # this function's job. Skipping is the whole contract.
        import _winapi

        victim = self._victim(tmp_path)
        link = tmp_path / "webview" / "EBWebView" / "Default" / "Cache"
        link.parent.mkdir(parents=True, exist_ok=True)
        _winapi.CreateJunction(str(victim), str(link))

        browser_profile.purge(tmp_path / "webview")

        assert link.exists()

    def test_an_ordinary_folder_of_the_same_name_is_still_swept(
        self, tmp_path: Path
    ) -> None:
        # Guard the guard: if the redirect check were too eager it would skip
        # everything and this whole module would quietly stop working.
        leak = _write(
            tmp_path / "webview" / "EBWebView" / "Default" / "Cache" / "f_1",
            CANARY)
        browser_profile.purge(tmp_path / "webview")
        assert not leak.exists()


class TestCrashReportingIsBlocked:
    """The renderer holds the decrypted conversation, and by default a crash
    of that process ships it to Microsoft.

    Neither Chromium switch stops it - measured: with --disable-breakpad and
    with --crash-dumps-dir pointed elsewhere, the crashpad handler still
    started and still built its database. What does stop it is occupying the
    path it needs, so the directory it must create cannot be created.
    """

    def test_the_path_crashpad_needs_is_taken(self, tmp_path: Path) -> None:
        assert browser_profile.block_crash_reporting(tmp_path) is True
        blocker = tmp_path / "EBWebView" / "Crashpad"
        assert blocker.is_file(), "a directory here means Crashpad can run"

    def test_it_works_on_a_profile_that_does_not_exist_yet(
        self, tmp_path: Path
    ) -> None:
        # First launch: the profile tree is created by WebView2 itself, so
        # the block has to get there first.
        fresh = tmp_path / "never-launched"
        assert browser_profile.block_crash_reporting(fresh) is True
        assert (fresh / "EBWebView" / "Crashpad").is_file()

    def test_an_existing_database_is_shredded_not_merely_replaced(
        self, tmp_path: Path
    ) -> None:
        # Upgrading an install that already crashed once: the dumps sitting
        # there are the exact thing being defended against.
        crashpad = tmp_path / "EBWebView" / "Crashpad"
        dump = _write(crashpad / "reports" / "abc.dmp", CANARY)
        _write(crashpad / "settings.dat", b"client-id")

        assert browser_profile.block_crash_reporting(tmp_path) is True

        assert not dump.exists()
        assert crashpad.is_file()
        assert _leaks(tmp_path) == []

    def test_saying_it_twice_changes_nothing(self, tmp_path: Path) -> None:
        assert browser_profile.block_crash_reporting(tmp_path) is True
        assert browser_profile.block_crash_reporting(tmp_path) is True
        assert (tmp_path / "EBWebView" / "Crashpad").is_file()

    def test_the_purge_does_not_undo_the_block(self, tmp_path: Path) -> None:
        # These two run back to back on every launch. A purge that removed
        # the blocker would hand crash reporting straight back.
        browser_profile.block_crash_reporting(tmp_path)
        browser_profile.purge(tmp_path)
        assert (tmp_path / "EBWebView" / "Crashpad").is_file()

    @pytest.mark.skipif(os.name != "nt", reason="NTFS junctions")
    def test_it_refuses_a_redirected_crashpad_path(self, tmp_path: Path) -> None:
        # Same trap as the purge: a junction here would have this function
        # shredding whatever it points at.
        import _winapi

        victim = tmp_path / "Documents"
        victim.mkdir()
        (victim / "notes.txt").write_text("MUST SURVIVE", encoding="utf-8")
        (tmp_path / "EBWebView").mkdir(parents=True)
        _winapi.CreateJunction(str(victim),
                               str(tmp_path / "EBWebView" / "Crashpad"))

        assert browser_profile.block_crash_reporting(tmp_path) is False
        assert (victim / "notes.txt").read_text(encoding="utf-8") == "MUST SURVIVE"


class TestPurgeStaysInsideTheProfile:
    def test_the_vault_beside_it_is_never_touched(self, tmp_path: Path) -> None:
        # DATA_DIR holds app.db, salt.bin and verifier.bin next to webview/.
        # A purge that walked one directory too far would destroy the vault.
        vault = _write(tmp_path / "app.db", b"SQLite format 3\x00encrypted")
        salt = _write(tmp_path / "salt.bin", b"0123456789abcdef")
        root = tmp_path / "webview"
        _write(root / "EBWebView" / "Default" / "Cache" / "f_1", CANARY)

        browser_profile.purge(root)

        assert vault.read_bytes() == b"SQLite format 3\x00encrypted"
        assert salt.read_bytes() == b"0123456789abcdef"


class TestTheServerSweepsAProfileTheLastSessionAbandoned:
    """The exit sweep is not enough, and the gap was measured rather than
    imagined.

    A first draft of this docstring said `run_app.clear_session_residue()`
    runs on the way OUT and that a crash therefore escapes it. That was
    wrong, and the same wrong sentence was caught and corrected in main.py
    while this copy survived. run_app calls it on the way IN, before the
    window opens, and calls `browser_profile.purge()` on the way out, so a
    crash IS cleaned up by the next launch of the packaged app.

    The real gap is narrower and it is the dev path: `start_backend.bat`
    runs uvicorn directly and never imports run_app at all, so nothing
    sweeps anything there. On 2026-08-20 that left 21 MB of WebView2
    cache in the dev tree, dated 25 July, with ten files carrying `first_mes`
    and ten carrying `system_prompt` as plain readable JSON - and `git status`
    said nothing, because the folder is gitignored.

    So the sweep also runs at SERVER STARTUP, which is the one thing both
    entry points share. Driven through the real ASGI lifespan rather than by
    calling the helper, because "purge works" was already true; what was
    missing was anybody calling it on this path.
    """

    def test_starting_the_server_shreds_what_a_killed_session_left(
        self, profile: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import config
        import main
        from fastapi.testclient import TestClient

        # DATA_DIR is read inside the lifespan, so patch the module attribute
        # the lifespan actually reads.
        monkeypatch.setattr(config, "DATA_DIR", str(profile.parent),
                            raising=False)
        leak = profile / "EBWebView" / "Default" / "Cache" / "Cache_Data" / "f_000066"
        keep = profile / "EBWebView" / "Default" / "IndexedDB" / "wallpaper" / "1.ldb"
        # GROUND: the leak is really there before the server starts, or the
        # assertion below passes on an empty directory.
        assert leak.exists() and CANARY in leak.read_bytes()
        assert keep.exists()

        with TestClient(main.app):
            pass

        assert not leak.exists(), (
            "a cached API response survived server startup - the only sweep "
            "on this path"
        )
        # And the sweep stayed a sweep: the profile still exists for what it
        # is for. A startup that wiped the wallpaper would be a worse bug.
        assert keep.exists() and keep.read_bytes() == b"PNGDATA"
