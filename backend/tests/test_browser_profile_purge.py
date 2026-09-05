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


def _victim(base: Path) -> Path:
    """A folder outside the profile, standing in for the user's documents."""
    victim = base / "Documents"
    victim.mkdir()
    (victim / "notes.txt").write_text("MUST SURVIVE", encoding="utf-8")
    (victim / "taxes.pdf").write_bytes(b"%PDF-1.7")
    return victim


def _intact(victim: Path) -> bool:
    return (victim.is_dir()
            and sorted(p.name for p in victim.iterdir())
            == ["notes.txt", "taxes.pdf"]
            and (victim / "notes.txt").read_text(encoding="utf-8")
            == "MUST SURVIVE")


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

    The class name is historic and half of it is now wrong. What is still
    refused is FOLLOWING a redirected name - every assertion here is about
    the folder at the far end keeping every byte, and that is unchanged. What
    is no longer refused is the NAME: since H-142 the link entry is removed,
    because leaving it meant the sweep could never finish and one unprivileged
    `mklink /J` disabled this module for the life of the install.
    """

    _victim = staticmethod(_victim)
    _intact = staticmethod(_intact)

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

    def test_a_junction_on_the_swept_name_itself_is_removed(
        self, tmp_path: Path
    ) -> None:
        """M4. The contract this test used to nail was the opposite one.

        It asserted the link stayed, and that reading is what let the trap
        become permanent: leaving the name means the sweep can never finish,
        so a single `mklink /J` - which needs no privilege - switched this
        module off for good with no signal. The link ENTRY is now removed;
        the folder it points at is not touched, which the sibling test pins.

        The position matters. This one puts the junction on the swept name
        ITSELF, where `shutil.rmtree` is a silent no-op: it raises "Cannot
        call rmtree on a symbolic link" and `ignore_errors=True` eats it.
        Only an explicit `os.rmdir` on the confirmed reparse point clears it.
        """
        import _winapi

        victim = self._victim(tmp_path)
        link = tmp_path / "webview" / "EBWebView" / "Default" / "Cache"
        link.parent.mkdir(parents=True, exist_ok=True)
        _winapi.CreateJunction(str(victim), str(link))
        assert os.path.lexists(link), "GROUND: the junction was never created"

        browser_profile.purge(tmp_path / "webview")

        assert not os.path.lexists(link), (
            "the junction entry is still on disk; rmtree cannot remove a "
            "top-level reparse point, so os.rmdir has to")
        assert self._intact(victim), "the target was destroyed with the link"

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
    def test_a_redirected_crashpad_path_is_replaced_by_the_blocker(
        self, tmp_path: Path
    ) -> None:
        """The old contract here returned False and left the link standing.

        MEASURED consequence: the block could then never be established. The
        name stayed occupied by a directory, `purge` stopped on it too, and
        Crashpad was free to keep writing minidumps of the decrypted DOM into
        whatever the link pointed at - for the life of the install, from one
        unprivileged `mklink /J`. Removing the ENTRY is what lets the blocker
        file be written; the target keeps every byte.
        """
        import _winapi

        victim = tmp_path / "Documents"
        victim.mkdir()
        (victim / "notes.txt").write_text("MUST SURVIVE", encoding="utf-8")
        (tmp_path / "EBWebView").mkdir(parents=True)
        blocker = tmp_path / "EBWebView" / "Crashpad"
        _winapi.CreateJunction(str(victim), str(blocker))

        assert browser_profile.block_crash_reporting(tmp_path) is True
        assert blocker.is_file(), "a directory here means Crashpad can run"
        assert (victim / "notes.txt").read_text(encoding="utf-8") == "MUST SURVIVE"
        assert victim.is_dir(), "the folder behind the link was destroyed"


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


@pytest.mark.skipif(os.name != "nt", reason="NTFS junctions")
class TestTheLinkGoesAndWhatItPointsAtStays:
    """The sweep used to stop dead on a junction, and stopping was the bug.

    Refusing to delete THROUGH a redirected name is right and stays. Refusing
    to remove the redirected NAME was a different decision wearing the same
    coat, and it had a measured cost: the entry stayed, `shutil.rmtree` was
    skipped, `block_crash_reporting` returned False, and one unprivileged
    `mklink /J` disabled both halves of this module permanently, silently, on
    every future launch.

    So the entry is removed and the target is not. Those are two separate
    claims and each one gets its own test here, in both directions.
    """

    def test_the_junction_entry_under_a_swept_folder_is_removed(
        self, tmp_path: Path
    ) -> None:
        """M1. The link sits INSIDE a swept folder, which is the position
        `shutil.rmtree` can already clear on its own - so this one also has
        to prove the sweep finished rather than stopped."""
        import _winapi

        victim = _victim(tmp_path)
        default = tmp_path / "webview" / "EBWebView" / "Default"
        leak = _write(default / "Cache" / "Cache_Data" / "f_1", CANARY)
        link = default / "Cache" / "trap"
        _winapi.CreateJunction(str(victim), str(link))
        assert os.path.lexists(link) and leak.exists(), "GROUND: nothing planted"

        browser_profile.purge(tmp_path / "webview")

        assert not os.path.lexists(link), (
            "the junction entry under the swept folder is still on disk")
        assert not (default / "Cache").exists(), (
            "the sweep stopped on the junction instead of finishing")
        assert _intact(victim), "the target was destroyed with the link"

    def test_the_junction_target_is_never_touched(self, tmp_path: Path) -> None:
        """M3. The whole reason the old code stopped. Removing a link must
        never turn into deleting what it points at - the folder can be the
        user's documents, put there by an attacker who needs no privilege."""
        import _winapi

        victim = _victim(tmp_path)
        notes = victim / "notes.txt"
        before = notes.read_bytes()
        link = tmp_path / "webview" / "EBWebView" / "Default" / "Cache"
        link.parent.mkdir(parents=True, exist_ok=True)
        _winapi.CreateJunction(str(victim), str(link))
        # GROUND plus POSITIVE CONTROL in one: a real leak in a real folder
        # beside the trap. Without it every assertion below holds on a purge
        # that does nothing at all.
        leak = _write(link.parent / "Network" / "Cookies", CANARY)

        browser_profile.purge(tmp_path / "webview")

        assert not leak.exists(), (
            "POSITIVE CONTROL: the purge did not run, so leaving the target "
            "alone proves nothing")
        assert not os.path.lexists(link), "the link entry was not removed"
        assert victim.is_dir(), "the folder behind the link was removed"
        found = notes.read_bytes() if notes.exists() else b""
        assert found == before, (
            f"the target notes.txt changed; expected {len(before)} bytes, "
            f"found {len(found)}")
        assert (victim / "taxes.pdf").read_bytes() == b"%PDF-1.7"

    def test_a_junction_free_tree_is_still_swept_to_nothing(
        self, tmp_path: Path
    ) -> None:
        """POSITIVE CONTROL for all three above. A sweep that had quietly
        become "remove links, delete nothing" would satisfy them."""
        default = tmp_path / "webview" / "EBWebView" / "Default"
        leak = _write(default / "Cache" / "Cache_Data" / "f_1", CANARY)

        removed = browser_profile.purge(tmp_path / "webview")

        assert removed == 1
        assert not leak.exists()
        assert not (default / "Cache").exists()

    def test_a_profile_folder_that_is_a_link_is_removed_and_named_out_loud(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A profile MOVED there on purpose is the accepted cost of this.

        Somebody who ran `mklink /J` to put the profile on another disk loses
        the link and gets a fresh profile on the default one; the data at the
        other end is not touched. That trade is only defensible if it is
        AUDIBLE, so the full path has to reach the log.
        """
        import _winapi
        import logging

        victim = _victim(tmp_path)
        link = tmp_path / "webview" / "EBWebView" / "Default"
        link.parent.mkdir(parents=True, exist_ok=True)
        _winapi.CreateJunction(str(victim), str(link))

        with caplog.at_level(logging.WARNING):
            browser_profile.purge(tmp_path / "webview")

        text = "\n".join(r.getMessage() for r in caplog.records)
        assert not os.path.lexists(link)
        assert _intact(victim)
        assert (browser_profile.PROFILE_LINK_REMOVED % link) in text, (
            "the removed profile link was not named in the log")
        assert str(victim) in text, "the log did not say where it pointed"

    def test_the_profile_root_itself_can_be_a_link(self, tmp_path: Path) -> None:
        """EBWebView, one level above the profile. MEASURED before this
        existed: `purge` returned 0 and `block_crash_reporting` returned
        False here, so the trap was permanent at this position too."""
        import _winapi

        victim = _victim(tmp_path)
        link = tmp_path / "webview" / "EBWebView"
        link.parent.mkdir(parents=True, exist_ok=True)
        _winapi.CreateJunction(str(victim), str(link))

        browser_profile.purge(tmp_path / "webview")

        assert not os.path.lexists(link)
        assert _intact(victim)

    def test_an_ordinary_profile_folder_is_not_removed(
        self, tmp_path: Path
    ) -> None:
        """POSITIVE CONTROL for the two above. A check that answered "link"
        too readily would delete the real profile on every launch, taking the
        wallpaper and the settings with it."""
        default = tmp_path / "webview" / "EBWebView" / "Default"
        kept = _write(default / "IndexedDB" / "wallpaper" / "1.ldb", b"PNGDATA")

        browser_profile.purge(tmp_path / "webview")

        assert default.is_dir()
        assert kept.read_bytes() == b"PNGDATA"


@pytest.mark.skipif(os.name != "nt", reason="NTFS junctions")
class TestOurOwnMinidumpsBehindALinkAreDestroyed:
    """The one thing that IS reached through the link, and why it is narrow.

    Removing the entry stops any FURTHER minidump landing out there, but the
    ones already written are ours: a renderer minidump is a snapshot of the
    decrypted DOM, which is the whole conversation. Leaving them because they
    are on the far side of a link would be this app declining to clean up
    after itself.

    So they are removed - and ONLY on three independent signs at once, never
    on one. One sign is a weapon: a `.dmp` suffix alone, or the MDMP magic
    alone, points this shredder at anything an attacker can name. All three
    are checked below, each with the other two satisfied, so a sign that
    quietly stopped firing shows up here rather than in a documents folder.
    """

    REPORT = "3f2504e0-4f89-11d3-9a0c-0305e82c3301.dmp"
    DUMP = browser_profile._MINIDUMP_MAGIC + b"\x93\xa7" + CANARY

    def _link(self, tmp_path: Path) -> tuple[Path, Path]:
        """A junction on Crashpad/reports pointing outside the profile.

        The root is created FIRST, on purpose: "written after our profile
        existed" is the third sign, and a fixture that seeded the far side
        first would make it vacuous.
        """
        import _winapi

        root = tmp_path / "webview"
        (root / "EBWebView" / "Crashpad").mkdir(parents=True)
        outside = tmp_path / "Documents"
        outside.mkdir()
        (outside / "notes.txt").write_text("MUST SURVIVE", encoding="utf-8")
        _winapi.CreateJunction(str(outside),
                               str(root / "EBWebView" / "Crashpad" / "reports"))
        return root, outside

    def _twin(self, outside: Path, name: str | None = None) -> Path:
        """A dump that satisfies EVERY sign, planted next to the bait.

        Without it each refusal below is satisfied by the search simply never
        running, which is the one failure a sign test cannot afford to be
        blind to: a scan that quietly became a no-op looks exactly like a
        scan that correctly refused.
        """
        twin = outside / (name or self.REPORT)
        twin.write_bytes(self.DUMP)
        return twin

    def test_a_minidump_we_wrote_is_shredded_and_its_full_path_reported(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        root, outside = self._link(tmp_path)
        dump = outside / self.REPORT
        dump.write_bytes(self.DUMP)

        with caplog.at_level(logging.WARNING):
            browser_profile.purge(root)

        text = "\n".join(r.getMessage() for r in caplog.records)
        assert not dump.exists(), "our own minidump survived behind the link"
        assert (browser_profile.MINIDUMP_SHREDDED % dump) in text, (
            "a file was destroyed without its full path reaching the log")
        assert outside.is_dir(), "the folder it pointed at was destroyed"
        assert (outside / "notes.txt").read_text(encoding="utf-8") \
            == "MUST SURVIVE"

    def test_a_file_that_is_not_named_like_a_crashpad_report_is_left(
        self, tmp_path: Path
    ) -> None:
        """Sign (i) alone removed. Same bytes, same timestamp, ordinary
        name - the shape of an attacker planting MDMP magic in a folder they
        want emptied."""
        root, outside = self._link(tmp_path)
        bait = outside / "quarterly-report.dmp"
        bait.write_bytes(self.DUMP)
        twin = self._twin(outside)

        browser_profile.purge(root)

        assert bait.read_bytes() == self.DUMP
        assert not twin.exists(), (
            "POSITIVE CONTROL: the search never ran at all, so the refusal "
            "above says nothing about sign (i)")

    def test_a_file_that_is_not_really_a_minidump_is_left(
        self, tmp_path: Path
    ) -> None:
        """Sign (ii) alone removed. Right name, right age, not a minidump."""
        root, outside = self._link(tmp_path)
        bait = outside / self.REPORT
        bait.write_bytes(b"PK\x03\x04 somebody archive")
        twin = self._twin(outside, "5c2b1e40-0000-4000-8000-0000000000ff.dmp")

        browser_profile.purge(root)

        assert bait.read_bytes() == b"PK\x03\x04 somebody archive"
        assert not twin.exists(), (
            "POSITIVE CONTROL: the search never ran at all")

    def test_a_file_older_than_our_profile_is_left(self, tmp_path: Path) -> None:
        """Sign (iii) alone removed. Right name, right magic, but it was
        already there before this app had a profile folder at all, so it
        cannot be something this app wrote."""
        import time

        root, outside = self._link(tmp_path)
        bait = outside / self.REPORT
        bait.write_bytes(self.DUMP)
        long_ago = time.time() - 86400
        os.utime(bait, (long_ago, long_ago))
        twin = self._twin(outside, "5c2b1e40-0000-4000-8000-0000000000ff.dmp")

        browser_profile.purge(root)

        assert bait.read_bytes() == self.DUMP
        assert not twin.exists(), (
            "POSITIVE CONTROL: the search never ran at all")

    def test_the_link_entry_still_goes_in_every_one_of_those_cases(
        self, tmp_path: Path
    ) -> None:
        """GROUND for the three refusals above: they must be refusals to
        delete a FILE, not the sweep quietly bailing out before it started."""
        root, outside = self._link(tmp_path)
        (outside / "quarterly-report.dmp").write_bytes(self.DUMP)
        twin = self._twin(outside)

        browser_profile.purge(root)

        assert not os.path.lexists(root / "EBWebView" / "Crashpad" / "reports")
        assert outside.is_dir()
        assert not twin.exists(), "the search did not run"


class TestNothingLeavesWithoutBeingOverwritten:
    """M6. The reason `rmtree` is not allowed to run on its own judgement.

    `shred_tree` overwrites each file before unlinking it. `shutil.rmtree`
    only unlinks. So a file `shred_tree` could not overwrite - locked by the
    browser, or a name Win32 cannot open - would be handed to `rmtree` and
    plainly unlinked, leaving every readable byte on disk for an undelete
    tool. The count would say it was removed and the warning would say it was
    not; both would be describing the same file.

    The sweep therefore only calls `rmtree` over a tree `shred_tree` has just
    walked end to end with nothing left behind and nothing pruned.
    """

    def test_a_file_that_could_not_be_overwritten_is_not_unlinked(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        default = tmp_path / "webview" / "EBWebView" / "Default"
        stubborn = _write(default / "Cache" / "Cache_Data" / "f_000066", CANARY)
        ordinary = _write(default / "Cache" / "Cache_Data" / "data_3", CARD)
        assert stubborn.read_bytes() == CANARY, "GROUND: nothing planted"

        real_open = builtins.open

        def refuse_open(file, *args, **kwargs):  # type: ignore[no-untyped-def]
            if isinstance(file, (str, os.PathLike)) and Path(file) == stubborn:
                raise PermissionError(32, "in use by another process")
            return real_open(file, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", refuse_open)

        browser_profile.purge(tmp_path / "webview")

        assert stubborn.exists(), (
            "a file that was never overwritten was unlinked anyway - its "
            "bytes are still on disk and an undelete tool recovers the chat")
        assert stubborn.read_bytes() == CANARY
        assert not ordinary.exists(), (
            "POSITIVE CONTROL: the rest of the sweep did not run at all")

    def test_an_ordinary_tree_is_still_taken_down_to_the_folder(
        self, tmp_path: Path
    ) -> None:
        """POSITIVE CONTROL. Holding back on a refusal must not become
        holding back always: with nothing stuck, the folder itself goes."""
        default = tmp_path / "webview" / "EBWebView" / "Default"
        _write(default / "Cache" / "Cache_Data" / "f_1", CANARY)

        browser_profile.purge(tmp_path / "webview")

        assert not (default / "Cache").exists()


class TestTheBlockerIsStillTheMechanism:
    """M2. The existence of the file is not the guarantee - the failed
    `CreateDirectory` is. A test that only checked `is_file()` would stay
    green if the mechanism stopped working."""

    def test_crashpad_cannot_create_its_directory(self, tmp_path: Path) -> None:
        assert browser_profile.block_crash_reporting(tmp_path) is True
        blocker = tmp_path / "EBWebView" / "Crashpad"

        with pytest.raises(FileExistsError):
            os.mkdir(blocker)

        # POSITIVE CONTROL: the same mkdir on the same path succeeds the
        # moment the blocker is gone. Without this the assertion above would
        # pass on a path that could not be created for some unrelated reason.
        os.unlink(blocker)
        os.mkdir(blocker)
        assert blocker.is_dir()

    def test_it_still_cannot_after_a_purge(self, tmp_path: Path) -> None:
        browser_profile.block_crash_reporting(tmp_path)
        browser_profile.purge(tmp_path)

        with pytest.raises(FileExistsError):
            os.mkdir(tmp_path / "EBWebView" / "Crashpad")

    @pytest.mark.skipif(os.name != "nt", reason="NTFS junctions")
    def test_it_cannot_after_the_crashpad_name_was_a_junction(
        self, tmp_path: Path
    ) -> None:
        """The position that used to make the block unreachable for good."""
        import _winapi

        outside = tmp_path / "Documents"
        outside.mkdir()
        keepsake = outside / "notes.txt"
        keepsake.write_text("MUST SURVIVE", encoding="utf-8")
        (tmp_path / "EBWebView").mkdir(parents=True)
        _winapi.CreateJunction(str(outside),
                               str(tmp_path / "EBWebView" / "Crashpad"))

        assert browser_profile.block_crash_reporting(tmp_path) is True
        with pytest.raises(FileExistsError):
            os.mkdir(tmp_path / "EBWebView" / "Crashpad")
        assert keepsake.read_text(encoding="utf-8") == "MUST SURVIVE"

    @pytest.mark.skipif(os.name != "nt", reason="NTFS junctions")
    def test_it_cannot_after_the_profile_root_was_a_junction(
        self, tmp_path: Path
    ) -> None:
        import _winapi

        outside = tmp_path / "Documents"
        outside.mkdir()
        (outside / "notes.txt").write_text("MUST SURVIVE", encoding="utf-8")
        _winapi.CreateJunction(str(outside), str(tmp_path / "EBWebView"))

        assert browser_profile.block_crash_reporting(tmp_path) is True
        with pytest.raises(FileExistsError):
            os.mkdir(tmp_path / "EBWebView" / "Crashpad")
        assert (outside / "notes.txt").read_text(encoding="utf-8") \
            == "MUST SURVIVE"
        assert not (outside / "Crashpad").exists(), (
            "the blocker was written THROUGH the link, into somebody else "
            "folder")


@pytest.mark.skipif(os.name != "nt", reason="NTFS junctions")
class TestTheFarSideIsOnlySearchedWhereCrashpadCouldHaveWritten:
    """The narrowing that keeps the far-side shred from becoming a weapon.

    MEASURED before it existed: a junction on `Default/Cache` - a name
    Crashpad never writes to - reached another browser's crash database two
    directories down and destroyed five of its dumps, on nothing more than a
    UUID filename and four magic bytes. Two things fix that and both are
    pinned here: the far side is searched ONLY at the positions where this
    app's own Crashpad could have written through the link, and a file is
    only ours if it is younger than the LINK, not merely younger than the
    install.
    """

    REPORT = "3f2504e0-4f89-11d3-9a0c-0305e82c3301.dmp"
    DUMP = browser_profile._MINIDUMP_MAGIC + b"\x93\xa7" + CANARY

    def test_the_fixture_really_is_what_the_signs_look_for(self) -> None:
        """GROUND for this whole class and the one below it. If the bait
        stopped matching the pattern, every refusal here would be vacuous."""
        assert browser_profile._CRASHPAD_REPORT.match(self.REPORT)
        assert self.DUMP.startswith(browser_profile._MINIDUMP_MAGIC)

    def _outside(self, tmp_path: Path) -> Path:
        outside = tmp_path / "another-app"
        outside.mkdir()
        (outside / "notes.txt").write_text("MUST SURVIVE", encoding="utf-8")
        return outside

    def test_a_link_on_a_cache_name_does_not_have_its_far_side_searched(
        self, tmp_path: Path
    ) -> None:
        import _winapi

        root = tmp_path / "webview"
        default = root / "EBWebView" / "Default"
        default.mkdir(parents=True)
        outside = self._outside(tmp_path)
        _winapi.CreateJunction(str(outside), str(default / "Cache"))
        dump = outside / self.REPORT
        dump.write_bytes(self.DUMP)

        browser_profile.purge(root)

        assert dump.read_bytes() == self.DUMP, (
            "a crash dump was destroyed behind a link on a name Crashpad "
            "never writes to")
        assert not os.path.lexists(default / "Cache"), (
            "GROUND: the link was not even removed, so nothing was exercised")

    def test_the_same_dump_behind_a_crashpad_link_does_go(
        self, tmp_path: Path
    ) -> None:
        """POSITIVE CONTROL for the test above, and the only difference
        between them is WHICH name carries the link."""
        import _winapi

        root = tmp_path / "webview"
        crashpad = root / "EBWebView" / "Crashpad"
        crashpad.mkdir(parents=True)
        outside = self._outside(tmp_path)
        _winapi.CreateJunction(str(outside), str(crashpad / "reports"))
        dump = outside / self.REPORT
        dump.write_bytes(self.DUMP)

        browser_profile.purge(root)

        assert not dump.exists()
        assert (outside / "notes.txt").read_text(encoding="utf-8") \
            == "MUST SURVIVE"

    def test_a_dump_older_than_the_link_is_left_alone(
        self, tmp_path: Path
    ) -> None:
        """The sign that survives an old install.

        The profile root's birth date is the install date, so on its own it
        lets through every minidump on the machine. Nothing this app wrote
        through a link can predate the link, so a dump that was already there
        when the link was made is somebody else's by construction.
        """
        import _winapi
        import time

        root = tmp_path / "webview"
        crashpad = root / "EBWebView" / "Crashpad"
        crashpad.mkdir(parents=True)
        outside = self._outside(tmp_path)
        # Strictly between the two, and the two real sleeps are the point.
        # The dump is written AFTER the profile root exists, so the install
        # date alone lets it through; it is written BEFORE the link, so only
        # the link's own age can refuse it. Windows file times move in
        # ~15ms steps, so the gaps have to be real ones.
        time.sleep(0.05)
        dump = outside / self.REPORT
        dump.write_bytes(self.DUMP)
        time.sleep(0.05)
        _winapi.CreateJunction(str(outside), str(crashpad / "reports"))

        browser_profile.purge(root)

        assert dump.read_bytes() == self.DUMP, (
            "a dump that predates the link was treated as one of ours")
        assert not os.path.lexists(crashpad / "reports"), (
            "GROUND: the link was not removed, so the scan never ran")


@pytest.mark.skipif(os.name != "nt", reason="NTFS junctions")
class TestTheSweepSaysWhatItDid:
    """Every deletion this module makes without being asked has to be
    audible, and every refusal has to be audible too. These read the log,
    because the log is the only place a user could ever see either."""

    def test_a_clean_profile_says_nothing_at_all(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """GROUND CONTROL for every log assertion below, and a real guard.

        The removal decision uses `confirmed_reparse`, which fails OPEN. Wire
        it to `is_redirected`, which fails CLOSED, and every one of the nine
        body directories that simply does not exist starts reporting itself
        as a redirected name that could not be removed. A warning that fires
        on the happy path is how a real one stops being read.
        """
        import logging

        default = tmp_path / "webview" / "EBWebView" / "Default"
        leak = _write(default / "Cache" / "Cache_Data" / "f_1", CANARY)

        with caplog.at_level(logging.WARNING):
            removed = browser_profile.purge(tmp_path / "webview")

        assert removed == 1 and not leak.exists(), "POSITIVE: nothing swept"
        assert [r.getMessage() for r in caplog.records
                if r.levelno >= logging.WARNING] == []

    def test_a_link_inside_a_swept_folder_is_named_with_its_target(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        import _winapi
        import logging

        victim = _victim(tmp_path)
        default = tmp_path / "webview" / "EBWebView" / "Default"
        (default / "Cache").mkdir(parents=True)
        link = default / "Cache" / "trap"
        _winapi.CreateJunction(str(victim), str(link))

        with caplog.at_level(logging.WARNING):
            browser_profile.purge(tmp_path / "webview")

        text = "\n".join(r.getMessage() for r in caplog.records)
        assert (browser_profile.LINK_ENTRY_REMOVED % link) in text
        assert (browser_profile.LINK_TARGET_KEPT % victim) in text
        assert (browser_profile.PROFILE_LINK_REMOVED % link) not in text, (
            "a cache entry was reported as though it were the whole profile")

    def test_the_profile_root_is_named_with_the_profile_sentence(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The two sentences are not interchangeable. One says a link inside
        the cache went; the other says the profile itself is being rebuilt
        somewhere else, which is the only warning that would explain to
        somebody why their moved profile stopped being used."""
        import _winapi
        import logging

        victim = _victim(tmp_path)
        link = tmp_path / "webview" / "EBWebView"
        link.parent.mkdir(parents=True, exist_ok=True)
        _winapi.CreateJunction(str(victim), str(link))

        with caplog.at_level(logging.WARNING):
            browser_profile.purge(tmp_path / "webview")

        text = "\n".join(r.getMessage() for r in caplog.records)
        assert (browser_profile.PROFILE_LINK_REMOVED % link) in text
        assert (browser_profile.LINK_ENTRY_REMOVED % link) not in text

    def test_a_folder_that_had_to_be_left_says_so(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture
    ) -> None:
        """The `stuck` half of the hold-back, read from the log rather than
        from a return value nobody sees."""
        import logging

        default = tmp_path / "webview" / "EBWebView" / "Default"
        stubborn = _write(default / "Cache" / "Cache_Data" / "f_1", CANARY)
        real_open = builtins.open

        def refuse_open(file, *args, **kwargs):  # type: ignore[no-untyped-def]
            if isinstance(file, (str, os.PathLike)) and Path(file) == stubborn:
                raise PermissionError(32, "in use by another process")
            return real_open(file, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", refuse_open)

        with caplog.at_level(logging.WARNING):
            browser_profile.purge(tmp_path / "webview")

        text = "\n".join(r.getMessage() for r in caplog.records)
        assert (browser_profile.SWEEP_HELD_BACK % (default / "Cache")) in text
        assert (default / "Cache").is_dir(), "the folder went anyway"

    def test_a_link_that_survives_its_own_removal_is_named(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture
    ) -> None:
        """The detector is replaced, not the code under test.

        `_unlink_entry` reads the reparse attribute a SECOND time, from its
        own `lstat`, rather than trusting the caller's answer - because the
        far-side search runs in between and the name in between belongs to
        anyone who can write to that directory. MEASURED with a wide target:
        the swap won on the first attempt and an ordinary file was unlinked
        with no overwrite at all. Telling `confirmed_reparse` to lie is the
        same shape as winning that race, and the file must survive.
        """
        import logging

        default = tmp_path / "webview" / "EBWebView" / "Default"
        # An ordinary FILE standing where a swept directory is expected. If
        # the second check is dropped, `os.unlink` takes it - unlinked, never
        # overwritten, every byte still recoverable.
        ordinary = _write(default / "Cache", CANARY)
        monkeypatch.setattr(browser_profile, "_confirmed_reparse",
                            lambda path: True)

        with caplog.at_level(logging.WARNING):
            browser_profile.purge(tmp_path / "webview")

        text = "\n".join(r.getMessage() for r in caplog.records)
        assert ordinary.exists() and ordinary.read_bytes() == CANARY, (
            "an ordinary name was removed on the caller's word alone, "
            "without being overwritten first")
        assert (browser_profile.LINK_ENTRY_STUCK % ordinary) in text

    def test_a_real_link_is_still_removed_with_the_same_detector(
        self, tmp_path: Path
    ) -> None:
        """POSITIVE CONTROL for the test above. The second check must refuse
        a name that is not a link and still clear one that is."""
        import _winapi

        victim = _victim(tmp_path)
        default = tmp_path / "webview" / "EBWebView" / "Default"
        default.mkdir(parents=True)
        _winapi.CreateJunction(str(victim), str(default / "Cache"))

        browser_profile.purge(tmp_path / "webview")

        assert not os.path.lexists(default / "Cache")
        assert _intact(victim)


@pytest.mark.skipif(os.name != "nt", reason="NTFS junctions")
class TestOnlyOurOwnNamesAreRemoved:
    def test_a_redirected_name_that_is_not_a_profile_is_left_alone(
        self, tmp_path: Path
    ) -> None:
        """EBWebView holds names this app did not invent. Removing a link
        just because it sits under our folder would be this module deleting
        on the basis of location rather than responsibility."""
        import _winapi

        victim = _victim(tmp_path)
        ebwebview = tmp_path / "webview" / "EBWebView"
        (ebwebview / "Default").mkdir(parents=True)
        stranger = ebwebview / "GrShaderCache"
        _winapi.CreateJunction(str(victim), str(stranger))

        browser_profile.purge(tmp_path / "webview")

        assert os.path.lexists(stranger), (
            "a name outside this module's remit was removed")
        assert _intact(victim)

    def test_a_profile_name_at_the_same_level_still_goes(
        self, tmp_path: Path
    ) -> None:
        """POSITIVE CONTROL. The two differ only in the name."""
        import _winapi

        victim = _victim(tmp_path)
        ebwebview = tmp_path / "webview" / "EBWebView"
        ebwebview.mkdir(parents=True)
        _winapi.CreateJunction(str(victim), str(ebwebview / "Profile 1"))

        browser_profile.purge(tmp_path / "webview")

        assert not os.path.lexists(ebwebview / "Profile 1")
        assert _intact(victim)


class TestANameWin32CannotOpenIsReportedNotIgnored:
    """The other half of the fail-closed repair, and the dangerous half.

    Loosening `is_redirected` fixed the trailing-SPACE case: the folder is now
    walked and the plaintext in it is shredded. A trailing DOT is a different
    animal - `scandir` on it raises ENOENT through the ordinary path, so
    `os.walk` yields nothing for it and, with the default `onerror`, says
    nothing either. The plaintext would then survive a sweep that reported a
    clean tree, and `rmtree` would be authorised over a walk that never
    happened.
    """

    @staticmethod
    def _dotted(tmp_path: Path) -> tuple[Path, Path]:
        default = tmp_path / "webview" / "EBWebView" / "Default"
        cache = default / "Cache"
        cache.mkdir(parents=True)
        odd = cache / "reports."
        os.mkdir("\\\\?\\" + str(odd))
        if not any(e.name == "reports." for e in os.scandir(cache)):
            pytest.skip("this filesystem folded the trailing dot away")
        secret = "\\\\?\\" + str(odd / "secret.json")
        with open(secret, "wb") as handle:
            handle.write(CANARY)
        return cache, odd

    def test_the_folder_stays_and_the_refusal_is_audible(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        cache, odd = self._dotted(tmp_path)
        assert os.path.exists("\\\\?\\" + str(odd / "secret.json")), (
            "GROUND: the plaintext was never planted")

        with caplog.at_level(logging.WARNING):
            browser_profile.purge(tmp_path / "webview")

        text = "\n".join(r.getMessage() for r in caplog.records)
        assert cache.is_dir(), (
            "rmtree ran over a tree containing a folder the walk never "
            "entered, so a file was unlinked without being overwritten")
        assert (browser_profile.SWEEP_HELD_BACK % cache) in text, (
            "the unwalkable folder was skipped in silence")

    def test_an_ordinary_neighbour_is_still_swept(
        self, tmp_path: Path
    ) -> None:
        """POSITIVE CONTROL. Reporting the refusal must not become refusing
        to work: everything the walk COULD reach still goes."""
        cache, _odd = self._dotted(tmp_path)
        leak = _write(cache / "Cache_Data" / "f_1", CANARY)

        removed = browser_profile.purge(tmp_path / "webview")

        assert not leak.exists()
        assert removed == 1


class TestASingleFileThatWillNotShredIsCounted:
    """The `_BODY_FILES` half of the same silence.

    `History` and `DIPS` are shredded one by one rather than swept, and that
    loop used to have no else. MEASURED: a hardlink on `History` made `shred`
    refuse - correctly, because overwriting would destroy whatever the other
    name opens - and the purge said nothing at all, so a profile with its
    entire browsing history still in the clear reported a clean run.
    """

    def test_it_is_named_rather_than_passed_over(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        default = tmp_path / "webview" / "EBWebView" / "Default"
        history = _write(default / "History", CANARY)
        dips = _write(default / "DIPS", b"visit state")
        real_open = builtins.open

        def refuse_open(file, *args, **kwargs):  # type: ignore[no-untyped-def]
            if isinstance(file, (str, os.PathLike)) and Path(file) == history:
                raise PermissionError(32, "in use by another process")
            return real_open(file, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", refuse_open)

        with caplog.at_level(logging.WARNING):
            removed = browser_profile.purge(tmp_path / "webview")

        text = "\n".join(r.getMessage() for r in caplog.records)
        assert history.read_bytes() == CANARY, "GROUND: it was shredded anyway"
        assert (browser_profile.STILL_READABLE % (1, history.name)) in text, (
            "a single file that could not be shredded went unreported by "
            "the purge itself; secure_delete naming it is a different claim")
        assert removed == 1 and not dips.exists(), (
            "POSITIVE CONTROL: the loop did not run at all")


@pytest.mark.skipif(os.name != "nt", reason="NTFS junctions")
class TestTheFarSideSearchIsBounded:
    """The search runs before the window opens, over a folder somebody else
    chose. An unbounded walk there is a denial of service with no attacker
    skill at all: point the link at a drive root and every launch stalls."""

    REPORT = "3f2504e0-4f89-11d3-9a0c-0305e82c3301.dmp"
    DUMP = browser_profile._MINIDUMP_MAGIC + b"\x93\xa7" + CANARY

    DEEP = 6      # _SCAN_DEPTH + 2, pinned by the ground control below
    SHALLOW = 2   # _SCAN_DEPTH - 2

    def test_the_bound_is_where_these_fixtures_assume_it_is(self) -> None:
        """GROUND. The two trees below are built at literal depths, on
        purpose: derived from the constant they would move WITH a mutation
        of it and prove nothing."""
        assert browser_profile._SCAN_DEPTH == 4

    def _linked(self, tmp_path: Path, depth: int) -> tuple[Path, Path]:
        import _winapi

        root = tmp_path / "webview"
        crashpad = root / "EBWebView" / "Crashpad"
        crashpad.mkdir(parents=True)
        outside = tmp_path / "far"
        deep = outside.joinpath(*[f"d{n}" for n in range(depth)])
        deep.mkdir(parents=True)
        _winapi.CreateJunction(str(outside), str(crashpad / "reports"))
        dump = deep / self.REPORT
        dump.write_bytes(self.DUMP)
        return root, dump

    def test_a_dump_below_the_depth_bound_is_left_where_it_is(
        self, tmp_path: Path
    ) -> None:
        root, dump = self._linked(tmp_path, self.DEEP)

        browser_profile.purge(root)

        assert dump.read_bytes() == self.DUMP

    def test_a_dump_within_the_depth_bound_still_goes(
        self, tmp_path: Path
    ) -> None:
        """POSITIVE CONTROL. The two differ only by how deep the file sits."""
        root, dump = self._linked(tmp_path, self.SHALLOW)

        browser_profile.purge(root)

        assert not dump.exists()

    def test_running_out_of_budget_is_said_out_loud(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture
    ) -> None:
        """A search that stopped early has NOT proved the far side is clean,
        and the only way anybody could know is the log."""
        import logging

        monkeypatch.setattr(browser_profile, "_SCAN_ENTRIES", 0)
        root, dump = self._linked(tmp_path, 1)

        with caplog.at_level(logging.WARNING):
            browser_profile.purge(root)

        text = "\n".join(r.getMessage() for r in caplog.records)
        assert dump.read_bytes() == self.DUMP, "GROUND: the budget was ignored"
        assert any(browser_profile.SCAN_CUT_SHORT.split("%s")[0] in line
                   for line in text.splitlines()), (
            "the search gave up without saying so")
