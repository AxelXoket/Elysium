"""Windows makes copies of this app's memory and files that the vault cannot see.

A crash dump carries the process heap, where the SQLCipher key and every
decrypted message are. The search indexer reads file content into a database
that is neither encrypted nor ours. A screenshot is available to anything else
running as this user.

Each is one Win32 call away from being closed, and each call is easy to make
and easier to believe without checking - so these tests read the result back
from Windows rather than trusting a return code. They exercise the real API on
the real machine; nothing here is mocked, because a mocked SetFileAttributesW
would prove only that the mock works.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
from ctypes import wintypes
from pathlib import Path

import pytest

import win_hardening

WINDOWS_ONLY = pytest.mark.skipif(os.name != "nt", reason="Win32 API surface")

_NOT_CONTENT_INDEXED = 0x2000
_READONLY = 0x0001
_HIDDEN = 0x0002
_NORMAL = 0x0080


def _attributes(path: Path) -> int:
    fn = ctypes.windll.kernel32.GetFileAttributesW
    fn.argtypes = [wintypes.LPCWSTR]
    fn.restype = wintypes.DWORD
    return fn(str(path))


def _make_window() -> int:
    """A real, invisible, top-level window - the only way to drive the
    success path of display-affinity code from a test process that has none.

    STATIC is a class every Windows install already registers, so this needs
    no WNDCLASS and no message loop: SetWindowDisplayAffinity operates on the
    handle, not on a running pump.
    """
    user32 = ctypes.windll.user32
    user32.CreateWindowExW.restype = wintypes.HWND
    hwnd = user32.CreateWindowExW(
        0, "STATIC", "elysium-test", 0x80000000,  # WS_POPUP, never shown
        0, 0, 10, 10, None, None, None, None)
    assert hwnd, "could not create a test window"
    return hwnd


@WINDOWS_ONLY
class TestSearchIndexExclusion:
    def test_the_indexer_is_actually_told_to_skip_the_folder(
        self, tmp_path: Path
    ) -> None:
        # Read the attribute back from Windows: SetFileAttributesW can return
        # success on a filesystem that drops the bit, and an unverified claim
        # about where the conversation is not indexed is worse than none.
        assert not _attributes(tmp_path) & _NOT_CONTENT_INDEXED
        assert win_hardening.exclude_from_search_index(tmp_path) is True
        assert _attributes(tmp_path) & _NOT_CONTENT_INDEXED

    def test_other_attributes_are_left_alone(self, tmp_path: Path) -> None:
        # OR, never assign. This has to be asserted on something that CAN be
        # cleared: a fresh directory carries only FILE_ATTRIBUTE_DIRECTORY,
        # which the filesystem refuses to change anyway, so the earlier
        # version of this test held even when the code assigned instead of
        # OR-ing. READONLY and HIDDEN on a file are really clearable, so this
        # asks the question the previous phrasing only appeared to ask.
        target = tmp_path / "elysium.log"
        target.write_text("x", encoding="utf-8")
        setter = ctypes.windll.kernel32.SetFileAttributesW
        setter.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
        setter.restype = wintypes.BOOL
        setter(str(target), _READONLY | _HIDDEN)
        assert _attributes(target) & (_READONLY | _HIDDEN) == _READONLY | _HIDDEN

        assert win_hardening.exclude_from_search_index(target) is True

        after = _attributes(target)
        assert after & _READONLY, "READONLY was clobbered"
        assert after & _HIDDEN, "HIDDEN was clobbered"
        assert after & _NOT_CONTENT_INDEXED
        setter(str(target), _NORMAL)  # let tmp_path cleanup remove it

    def test_saying_it_twice_still_reports_success(self, tmp_path: Path) -> None:
        assert win_hardening.exclude_from_search_index(tmp_path) is True
        assert win_hardening.exclude_from_search_index(tmp_path) is True

    def test_a_missing_folder_is_a_no_not_a_crash(self, tmp_path: Path) -> None:
        assert win_hardening.exclude_from_search_index(tmp_path / "nope") is False

    def test_it_works_on_a_file_too_not_only_a_directory(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "elysium.log"
        target.write_text("x", encoding="utf-8")
        assert win_hardening.exclude_from_search_index(target) is True
        assert _attributes(target) & _NOT_CONTENT_INDEXED


@WINDOWS_ONLY
class TestTheDllSearchPathIsReset:
    """PyInstaller's bootloader points the DLL search at the app directory and
    that setting is inherited by children - including uv.exe and the voice
    engine, neither of which is code this project wrote."""

    def test_windows_accepts_the_reset(self) -> None:
        assert win_hardening.reset_dll_search_path() is True

    def test_it_actually_clears_a_directory_that_was_set(
        self, tmp_path: Path
    ) -> None:
        """Read it back from Windows rather than trusting the return value.
        GetDllDirectoryW reports what the process will search."""
        setter = ctypes.windll.kernel32.SetDllDirectoryW
        setter.argtypes = [wintypes.LPCWSTR]
        setter.restype = wintypes.BOOL
        getter = ctypes.windll.kernel32.GetDllDirectoryW
        getter.argtypes = [wintypes.DWORD, wintypes.LPWSTR]
        getter.restype = wintypes.DWORD

        assert setter(str(tmp_path))
        buf = ctypes.create_unicode_buffer(1024)
        getter(1024, buf)
        assert buf.value == str(tmp_path), "the fixture did not take"

        assert win_hardening.reset_dll_search_path() is True

        getter(1024, buf)
        assert buf.value == "", "the app directory is still on the search path"

    def test_the_launch_path_reports_it(self, tmp_path: Path) -> None:
        assert win_hardening.harden(tmp_path)["dll_search_path_reset"] is True


@WINDOWS_ONLY
class TestCrashDumpHeapExclusion:
    def test_windows_accepts_the_no_heap_flag(self) -> None:
        assert win_hardening.restrict_crash_dump_contents() is True

    def test_it_is_safe_to_call_more_than_once(self) -> None:
        assert win_hardening.restrict_crash_dump_contents() is True
        assert win_hardening.restrict_crash_dump_contents() is True

    def test_a_refusal_is_reported_as_a_refusal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # WerSetFlags always succeeds on this machine, so the HRESULT check
        # was never exercised - deleting it and returning True unconditionally
        # kept the suite green. Windows is faked here precisely because the
        # failure it guards against cannot be produced any other way, and a
        # protection that reports success when it did nothing is the one kind
        # of lie this module must not tell.
        monkeypatch.setattr(ctypes.windll.kernel32, "WerSetFlags",
                            lambda flags: -2147024809)  # E_INVALIDARG
        assert win_hardening.restrict_crash_dump_contents() is False


@WINDOWS_ONLY
class TestScreenCaptureExclusion:
    def test_it_stays_off_unless_the_user_asks(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The cost lands on the user's own screenshots, so this must never
        # switch itself on. Anything other than an explicit "1" is off.
        monkeypatch.delenv("ELYSIUM_SCREEN_PRIVACY", raising=False)
        assert win_hardening.screen_privacy_requested() is False
        assert win_hardening.apply_screen_privacy() == 0

        for value in ("", "0", "true", "yes", "on"):
            monkeypatch.setenv("ELYSIUM_SCREEN_PRIVACY", value)
            assert win_hardening.screen_privacy_requested() is False, value

        monkeypatch.setenv("ELYSIUM_SCREEN_PRIVACY", "1")
        assert win_hardening.screen_privacy_requested() is True

    def test_asking_for_it_does_not_touch_windows_it_does_not_own(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A pytest process has no visible top-level window of its own, so the
        # count is zero - and the point is that it reached that answer by
        # process id rather than by sweeping the desktop.
        monkeypatch.setenv("ELYSIUM_SCREEN_PRIVACY", "1")
        assert win_hardening.apply_screen_privacy() == 0

    def test_window_enumeration_is_scoped_to_this_process(self) -> None:
        # Every handle it returns must belong to us. A version that matched on
        # the window TITLE could hide, or fail to hide, somebody else's window.
        user32 = ctypes.windll.user32
        own_pid = os.getpid()
        for hwnd in win_hardening.own_top_level_windows():
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            assert pid.value == own_pid

    def test_an_invalid_handle_is_a_no_not_a_crash(self) -> None:
        assert win_hardening.exclude_from_screen_capture(0) is False
        assert win_hardening.exclude_from_screen_capture(0xDEADBEEF) is False

    def test_a_real_window_is_actually_excluded(self) -> None:
        # A pytest process has no window, so nothing in this suite ever drove
        # the success path - both the call and its read-back went untested.
        # This makes one real top-level window and checks Windows agrees.
        hwnd = _make_window()
        try:
            assert win_hardening.exclude_from_screen_capture(hwnd) is True
            affinity = wintypes.DWORD()
            ctypes.windll.user32.GetWindowDisplayAffinity(
                hwnd, ctypes.byref(affinity))
            assert affinity.value == 0x11  # WDA_EXCLUDEFROMCAPTURE
        finally:
            ctypes.windll.user32.DestroyWindow(hwnd)

    def test_a_windows_that_says_yes_but_did_nothing_is_not_believed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The read-back exists for exactly one case: a build where the setter
        # reports success and the affinity never changes. Removing the check
        # left every test green, so the case is simulated here. Trusting the
        # return code would hide a window that is still being captured while
        # the user believes it is not.
        hwnd = _make_window()
        try:
            monkeypatch.setattr(
                ctypes.windll.user32, "GetWindowDisplayAffinity",
                lambda handle, out: (
                    ctypes.cast(out, ctypes.POINTER(wintypes.DWORD)).contents
                    .__setattr__("value", 0x00) or 1))
            assert win_hardening.exclude_from_screen_capture(hwnd) is False
        finally:
            ctypes.windll.user32.DestroyWindow(hwnd)


@WINDOWS_ONLY
class TestHardenOnTheLaunchPath:
    def test_it_reports_what_actually_took_effect(self, tmp_path: Path) -> None:
        # Not a log line and a shrug: the launch path needs to be able to tell
        # a working protection from a silent no-op.
        result = win_hardening.harden(tmp_path)
        assert result["crash_dump_heap_excluded"] is True
        assert result["search_index_excluded"] is True
        assert result["windows_excluded_from_capture"] == 0

    def test_it_never_raises_on_a_path_that_is_not_there(
        self, tmp_path: Path
    ) -> None:
        # This runs before the window exists. An exception here means the app
        # does not start at all, which is far worse than an unhardened launch.
        result = win_hardening.harden(tmp_path / "missing")
        assert result["search_index_excluded"] is False
        assert result["crash_dump_heap_excluded"] is True


@WINDOWS_ONLY
class TestDataFolderIsNotShared:
    """The vault's folder should be reachable by this machine's owner and
    nobody else. It already is - measured on a real install, the DACL
    inherits SYSTEM, Administrators and the current user and nothing more.

    Which is why this reports instead of repairing. Removing SYSTEM and
    Administrators stops neither an attacker running as this user (who has
    full access already) nor an administrator (who can take ownership). The
    default is right; what nothing watched for was it being widened LATER by
    a sync client, an installer or a stray right-click.
    """

    def test_a_normal_folder_is_not_shared_with_anyone(
        self, tmp_path: Path
    ) -> None:
        assert win_hardening.data_dir_shared_with(tmp_path) == []

    def test_it_reads_the_real_data_directory_without_falling_over(self) -> None:
        # Deliberately NOT "and finds it clean". That asserts something about
        # the machine the suite runs on rather than about this code - and it
        # failed exactly that way: in a dev checkout DATA_DIR is the repo
        # folder, which inherits a BUILTIN\Users grant from Desktop. A real
        # finding, in the wrong place; the launch-time warning is where it
        # belongs, and where a user can act on it.
        import config
        assert isinstance(win_hardening.data_dir_shared_with(config.DATA_DIR), list)

    def test_granting_everyone_access_is_reported(self, tmp_path: Path) -> None:
        # Widen it the way a careless share does, and check the app notices.
        target = tmp_path / "widened"
        target.mkdir()
        result = subprocess.run(
            ["icacls", str(target), "/grant", "*S-1-1-0:(OI)(CI)F"],
            capture_output=True, text=True,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
        if result.returncode != 0:
            pytest.skip(f"icacls refused: {result.stdout or result.stderr}")

        assert win_hardening.data_dir_shared_with(target) == ["Everyone"]

    def test_a_path_that_is_not_there_is_not_an_error(self, tmp_path: Path) -> None:
        # Runs on the launch path before the folder may exist.
        assert win_hardening.data_dir_shared_with(tmp_path / "missing") == []


def _sddl(target: Path) -> str:
    """The folder's DACL as text, for before/after comparison."""
    return win_hardening._dacl_sddl(target) or ""


def _icacls(target: Path, *args: str) -> None:
    """Run icacls for a test's own setup or teardown, not for the app.

    Deliberately separate from win_hardening._icacls: a test that reached into
    the module for its fixtures would go green when both the module and the
    expectation drifted the same way.
    """
    result = subprocess.run(
        ["icacls", str(target), *args],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        pytest.skip(f"icacls refused: {result.stdout or result.stderr}")


def _widen(target: Path, sid: str) -> None:
    """Hand a broad group full access, the way a careless share does."""
    result = subprocess.run(
        ["icacls", str(target), "/grant", f"*{sid}:(OI)(CI)F"],
        capture_output=True, text=True,
        creationflags=0x08000000,  # CREATE_NO_WINDOW
    )
    if result.returncode != 0:
        pytest.skip(f"icacls refused: {result.stdout or result.stderr}")


@WINDOWS_ONLY
class TestTheDataFolderIsNarrowed:
    """Owner's decision, 8 August 2026: report was not enough, take it back.

    The previous reasoning - that the default is already correct and a silent
    repair hides whatever widened it - was sound and only half of it is gone.
    The removal is loud: every principal taken away is named in the log, and
    anything still reachable afterwards is still warned about. What changed is
    that a second ACCOUNT on the same machine must not be able to read
    salt.bin and verifier.bin, and that this has to hold on every install
    rather than on a machine somebody audited by hand.
    """

    def test_the_undo_command_in_security_md_actually_undoes_it(
        self, tmp_path: Path,
    ) -> None:
        """SECURITY.md promises a way back. This is that promise, executed.

        The document recommended `/inheritance:e` for years and it does not do
        what the sentence next to it claimed. Breaking inheritance COPIES the
        parent's entries onto the folder as its own; switching inheritance back
        on adds the parent's entries again but leaves those copies in place,
        minus whatever was removed in between. The folder ends up in a third
        state that is neither where it started nor where narrowing left it, and
        a reader following the document would believe they had restored it.

        Written as one test with both commands in it, because the point is not
        that `/reset` works. The point is that the two differ, and no test
        existed that could tell them apart.
        """
        parent = tmp_path / "parent"
        parent.mkdir()
        _widen(parent, "S-1-1-0")                       # Everyone, on the PARENT
        child = parent / "data"
        child.mkdir()                                   # inherits, no entries of its own
        before = _sddl(child)
        assert win_hardening.data_dir_shared_with(child) == ["Everyone"]

        assert win_hardening.narrow_data_dir(child) == ["Everyone"]
        assert _sddl(child) != before

        _icacls(child, "/inheritance:e")
        assert _sddl(child) != before, (
            "if this ever passes, the documented command was fine after all "
            "and this test should be simplified rather than deleted"
        )

        _icacls(child, "/reset")
        assert _sddl(child) == before
        assert win_hardening.data_dir_shared_with(child) == ["Everyone"], (
            "restored means restored: the wide access is back, which is the "
            "whole point of an undo the reader can trust"
        )

    def test_a_widened_folder_is_taken_back(self, tmp_path: Path) -> None:
        target = tmp_path / "widened"
        target.mkdir()
        _widen(target, "S-1-1-0")                       # Everyone
        assert win_hardening.data_dir_shared_with(target) == ["Everyone"]

        removed = win_hardening.narrow_data_dir(target)

        assert removed == ["Everyone"]
        assert win_hardening.data_dir_shared_with(target) == [], (
            "the folder is still reachable by a group that is not the owner"
        )

    def test_the_owner_can_still_use_the_folder_afterwards(
        self, tmp_path: Path
    ) -> None:
        """Guard the guard. A narrowing that locked the app out of its own
        vault would pass every assertion above and brick the product."""
        target = tmp_path / "widened"
        target.mkdir()
        _widen(target, "S-1-5-32-545")                  # Users
        win_hardening.narrow_data_dir(target)

        probe = target / "app.db"
        probe.write_bytes(b"still writable")
        assert probe.read_bytes() == b"still writable"

    def test_an_already_narrow_folder_is_left_completely_alone(
        self, tmp_path: Path
    ) -> None:
        """The common case on a healthy install, and it must be a no-op - not
        a detached-inheritance folder that merely looks the same."""
        before = win_hardening._dacl_sddl(tmp_path)

        assert win_hardening.narrow_data_dir(tmp_path) == []

        assert win_hardening._dacl_sddl(tmp_path) == before

    def test_it_is_not_recursive(self, tmp_path: Path) -> None:
        """In a dev checkout the data folder IS the source tree, .venv and
        all. Walking an ACL change through tens of thousands of files to fix
        one folder is the kind of helpful sweep that ends up in an incident
        report - the folder is what carries, through inheritance."""
        target = tmp_path / "widened"
        target.mkdir()
        child = target / "sub"
        child.mkdir()
        _widen(child, "S-1-1-0")                        # only the CHILD
        _widen(target, "S-1-1-0")

        win_hardening.narrow_data_dir(target)

        # The child kept its own explicit grant: it was not walked into.
        assert win_hardening.data_dir_shared_with(child) == ["Everyone"]

    def test_a_missing_folder_is_not_an_error(self, tmp_path: Path) -> None:
        assert win_hardening.narrow_data_dir(tmp_path / "missing") == []

    def test_the_vault_files_inside_it_are_taken_back_too(
        self, tmp_path: Path
    ) -> None:
        """salt.bin and verifier.bin are what this is named after: the two
        files an offline passphrase attack needs. Narrowing a folder while
        they stayed readable would be the whole point missed, so the check is
        on the FILES, whatever mechanism gets them there."""
        target = tmp_path / "widened"
        target.mkdir()
        _widen(target, "S-1-5-32-545")
        salt = target / "salt.bin"
        salt.write_bytes(b"not a real salt")
        assert win_hardening.data_dir_shared_with(salt) == ["Users"]

        win_hardening.narrow_data_dir(target)

        assert win_hardening.data_dir_shared_with(salt) == []
        assert salt.read_bytes() == b"not a real salt"

    def test_a_clean_report_means_the_files_are_clean_too(
        self, tmp_path: Path
    ) -> None:
        """harden() saying shared_with=[] has to be about the files as well,
        or "clean" is a sentence about the wrong object."""
        _widen(tmp_path, "S-1-5-32-545")
        (tmp_path / "verifier.bin").write_bytes(b"x")

        result = win_hardening.harden(tmp_path)

        assert result["shared_with"] == []
        assert win_hardening.data_dir_shared_with(
            tmp_path / "verifier.bin") == []

    def test_the_launch_path_reports_what_it_took_away(
        self, tmp_path: Path
    ) -> None:
        """harden() is what the launch actually calls, and the caller has to
        be able to tell "nothing was wrong" from "something was, and is now
        fixed"."""
        _widen(tmp_path, "S-1-1-0")

        result = win_hardening.harden(tmp_path)

        assert result["narrowed"] == ["Everyone"]
        # AFTER the narrowing, not before: naming groups that no longer have
        # access is a warning nobody can act on.
        assert result["shared_with"] == []

    def test_it_reads_permissions_without_shelling_out(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # icacls prints LOCALISED names, so parsing it breaks on a Turkish
        # Windows - and this machine is one. The SDDL path must not depend on
        # a subprocess at all.
        def refuse(*args, **kwargs):
            raise AssertionError("data_dir_shared_with spawned a process")

        monkeypatch.setattr(subprocess, "run", refuse)
        monkeypatch.setattr(subprocess, "Popen", refuse)
        assert win_hardening.data_dir_shared_with(tmp_path) == []


@WINDOWS_ONLY
class TestItRecognisesEveryWayToGrantAccess:
    """The ACE type filter was `startswith("A")`, read as "allow".

    It is not one. Windows spells allow four more ways - CA and XA (callback
    and conditional), OA and ZA (object) - none of which start with A, and all
    of which grant. Meanwhile AU is an AUDIT entry, which grants nothing and
    does start with A. So the filter skipped three ways of widening the folder
    and would have counted a logging rule as access.

    icacls cannot produce a conditional ACE, so the SDDL is fed in directly.
    That is the point: the parser is what is under test.
    """

    @pytest.mark.parametrize("ace_type", ["A", "CA", "XA", "OA", "ZA"])
    def test_every_allow_type_is_counted(
        self, ace_type: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            win_hardening, "_dacl_sddl",
            lambda path: f"D:PAI(A;;FA;;;BA)(A;;FA;;;SY)({ace_type};;FA;;;WD)")
        assert win_hardening.data_dir_shared_with("anywhere") == ["Everyone"]

    def test_a_conditional_ace_with_a_real_condition_body_is_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The parametrised case above flattens XA/ZA into the shape of an
        # ordinary ACE, which is not what Windows emits: a conditional entry
        # carries a bracketed expression that contains its OWN parentheses,
        # and the parser splits on "(". Feeding it the flat form tested the
        # type list and quietly skipped the parsing the class docstring says
        # is under test.
        sddl = ("D:PAI(A;;FA;;;BA)"
                "(XA;;FA;;;WD;(Member_of{SID(S-1-5-21-1-2-3-513)}))"
                "(ZA;;FA;;;S-1-5-32-546;(@USER.Title==\"x\"))")
        monkeypatch.setattr(win_hardening, "_dacl_sddl", lambda path: sddl)
        assert "Everyone" in win_hardening.data_dir_shared_with("anywhere")

    @pytest.mark.parametrize("ace_type", ["D", "OD", "XD"])
    def test_a_deny_entry_is_not_a_grant(
        self, ace_type: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Denying Everyone is the opposite of sharing with them. Reporting it
        # would train the user to ignore the one line that must be believed.
        monkeypatch.setattr(
            win_hardening, "_dacl_sddl",
            lambda path: f"D:PAI(A;;FA;;;BA)({ace_type};;FA;;;WD)")
        assert win_hardening.data_dir_shared_with("anywhere") == []

    def test_an_audit_entry_is_not_a_grant(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # AU starts with "A" and permits nothing.
        monkeypatch.setattr(
            win_hardening, "_dacl_sddl",
            lambda path: "D:PAI(A;;FA;;;BA)(AU;;FA;;;WD)")
        assert win_hardening.data_dir_shared_with("anywhere") == []
