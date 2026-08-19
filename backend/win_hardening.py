"""win_hardening.py - the copies Windows makes that the vault cannot reach.

SQLCipher protects the bytes in app.db. It says nothing about the copies the
operating system makes on its own, outside the vault and outside its threat
model:

  * a crash dump, written when the process dies, containing the process heap -
    where the vault key and every decrypted message live;
  * a search index, built over the data folder by a service that reads files
    the user never opened;
  * a screenshot, taken by anything else running as this user.

Each is closed by one documented Win32 call. None of them needs a dependency,
and none of them is worth an exception: every function here reports whether it
took effect rather than raising, because a hardening step that fails must never
be the reason the app refuses to start.

What this does NOT cover, stated here so nobody reads the list above as more
than it is:

  * WerSetFlags applies to THIS process. The WebView2 renderer is a separate
    process, and a dump of it holds the decrypted DOM.

    This paragraph used to end "the upload is not prevented here or anywhere
    yet", which was already untrue when it was written and is worth correcting
    rather than deleting: browser_profile.block_crash_reporting() occupies
    <profile>/EBWebView/Crashpad with a FILE, so Crashpad cannot create its
    database directory and its handler is never launched at all. Nothing is
    written, so nothing is uploaded. It also says so - it returns False when
    the block could not be put in place, which is the case this note is really
    about.

    That fallback was an open question and was then measured twice, in that
    order, because the first measurement answered a different question than it
    appeared to (9 August 2026):

      1. An ordinary child process crashed on purpose - an unhandled access
         violation, no user data - DID get a 1.5 MB minidump written to
         %LOCALAPPDATA%\\CrashDumps, with no LocalDumps registry entry for it.
         So the OS-level mechanism is real.
      2. A real WebView2 renderer crashed on purpose (edge://crash, renderer
         PIDs confirmed gone) produced NO dump - not in the system folder, not
         in the profile - under three configurations: Crashpad blocked as we
         ship it, Crashpad blocked plus SetErrorMode(SEM_NOGPFAULTERRORBOX),
         and Crashpad left alone. A Chromium renderer takes its own controlled
         path down and never raises the kind of unhandled fault WER collects.

    So the leak this paragraph feared does not reproduce for the window we
    actually ship. Nothing was added to prevent it, deliberately: two candidate
    defences were measured and neither had anything to prevent, and unproven
    code in the hardening path reads as protection nobody has to check.

    The honest limit: one crash mechanism was tested. A GPU driver fault or an
    out-of-memory kill may behave differently and were not tested. SECURITY.md
    states this to the user in those terms.
  * The heap is excluded from a dump; stack memory is not.
  * The search-index attribute is set on one directory, not recursively, and
    does not retroactively remove anything already in the index.

Measured on the target machine before it was written - see the test file for
the assertions that keep each call honest.
"""
from __future__ import annotations

import ctypes
import logging
import os
import subprocess
from ctypes import wintypes
from pathlib import Path

log = logging.getLogger(__name__)

#: WerSetFlags: leave the process heap OUT of any error report. The heap is
#: where the vault key and the decrypted conversation are; the rest of a
#: minidump (module list, stacks, registers) is what makes a crash
#: diagnosable. This keeps the diagnosis and drops the secrets.
_WER_FAULT_REPORTING_FLAG_NOHEAP = 0x0001

#: SetFileAttributesW: tell the Windows Search indexer to skip this tree. The
#: indexer reads file CONTENT and stores extracts in its own database, which
#: is not encrypted and not ours.
_FILE_ATTRIBUTE_NOT_CONTENT_INDEXED = 0x2000

#: SetWindowDisplayAffinity: the window is composited normally on the physical
#: screen and omitted entirely from every capture path - screenshots, screen
#: recording, screen sharing, and Windows Recall. Windows 10 2004+; older
#: builds fail the call rather than doing something partial.
_WDA_EXCLUDEFROMCAPTURE = 0x00000011
_WDA_NONE = 0x00000000

#: Screen-capture exclusion is OFF by default and this is a deliberate choice.
#: Recall is absent on the machine this was built for (no WindowsAI policy
#: keys, no CoreAIPlatform folder), so the headline reason does not apply,
#: while the cost lands immediately and visibly: the user's OWN screenshots of
#: their OWN app come out black, with nothing on screen to explain why. What
#: remains is real but narrower - screenshot-grabbing malware, and sharing a
#: screen by accident - so it ships as a switch the user turns on knowingly.
_SCREEN_PRIVACY_ENV = "ELYSIUM_SCREEN_PRIVACY"


def _on_windows() -> bool:
    return os.name == "nt"


def reset_dll_search_path() -> bool:
    """Undo the DLL search path PyInstaller's bootloader hands to our children.

    The bootloader calls SetDllDirectoryW to point the search at the unpacked
    application directory, and PyInstaller's own documentation says the setting
    is INHERITED by child processes. That matters here because this app starts
    children that are not ours: uv.exe and the voice engine, both running code
    this project did not write. A DLL dropped beside the exe - or into the
    onefile temp directory - would be found by them before the system copy.

    SetDllDirectoryW(NULL) restores the default order. It is the fix
    PyInstaller documents for exactly this, and it costs one call.

    The realistic bound, stated so nobody reads this as more than it is: an
    attacker who can write into those directories is already running as this
    user, which is a boundary this whole module works inside rather than
    against. This closes a documented path, not the class.
    """
    if not _on_windows():
        return False
    try:
        fn = ctypes.windll.kernel32.SetDllDirectoryW
        fn.argtypes = [wintypes.LPCWSTR]
        fn.restype = wintypes.BOOL
        return bool(fn(None))
    except (AttributeError, OSError):
        return False


def restrict_crash_dump_contents() -> bool:
    """Keep the process heap out of Windows Error Reporting dumps.

    When a process dies badly Windows writes a minidump, and by default that
    dump carries the heap. For this app the heap holds the SQLCipher key and
    every message decrypted so far - so a crash turns the encrypted vault into
    a plaintext file, and WER may upload it.

    Partial by nature, and worth saying plainly: stack memory still ships, so
    a value that happened to be on a stack frame at the moment of the crash
    can still appear. This removes the bulk, not the possibility.
    """
    if not _on_windows():
        return False
    try:
        fn = ctypes.windll.kernel32.WerSetFlags
        fn.argtypes = [wintypes.DWORD]
        fn.restype = ctypes.c_long  # HRESULT
        hresult = fn(_WER_FAULT_REPORTING_FLAG_NOHEAP)
    except (AttributeError, OSError):
        return False
    ok = hresult == 0
    log.info("crash dumps: heap %s", "excluded" if ok else "NOT excluded")
    return ok


def exclude_from_search_index(path: Path | str) -> bool:
    """Mark a directory so the Windows Search indexer skips its contents.

    The indexer extracts text from files it can parse and keeps that extract
    in its own database, which is neither encrypted nor under our control.

    Scope, measured rather than assumed. Windows gives this attribute to files
    and folders created AFTER it is set, and inheritance does follow down the
    tree - but files that already exist keep whatever they had. So this is a
    forward guarantee, applied once and early, not a sweep.

    Worth knowing before reading much into it: a stock Windows install already
    excludes %LOCALAPPDATA% from the index, so on most machines this changes
    nothing. It earns its place when ELYSIUM_DATA_DIR points somewhere that IS
    indexed - Documents, a synced folder - which is the case where nothing
    else would have protected the folder at all.
    """
    if not _on_windows():
        return False
    target = Path(path)
    if not target.exists():
        return False
    try:
        get_attrs = ctypes.windll.kernel32.GetFileAttributesW
        get_attrs.argtypes = [wintypes.LPCWSTR]
        get_attrs.restype = wintypes.DWORD
        set_attrs = ctypes.windll.kernel32.SetFileAttributesW
        set_attrs.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
        set_attrs.restype = wintypes.BOOL

        name = str(target)
        current = get_attrs(name)
        if current == 0xFFFFFFFF:  # INVALID_FILE_ATTRIBUTES
            return False
        if current & _FILE_ATTRIBUTE_NOT_CONTENT_INDEXED:
            return True  # already set; setting it again is a pointless write
        if not set_attrs(name, current | _FILE_ATTRIBUTE_NOT_CONTENT_INDEXED):
            return False
        # Read it back. SetFileAttributesW can report success on a filesystem
        # that silently drops the bit, and an unverified claim about where the
        # conversation is NOT indexed is worse than no claim.
        return bool(get_attrs(name) & _FILE_ATTRIBUTE_NOT_CONTENT_INDEXED)
    except (AttributeError, OSError):
        return False


def own_top_level_windows() -> list[int]:
    """Visible top-level window handles belonging to THIS process.

    Matching on the process id rather than the window title: the title is
    attacker-influenced in the general case and merely ambiguous here (another
    program is free to name a window "Elysium"), while the process id is
    exactly the question being asked.
    """
    if not _on_windows():
        return []
    handles: list[int] = []
    try:
        user32 = ctypes.windll.user32
        # Declared, not assumed. Real HWND values happen to stay inside 32 bits
        # so an undeclared call works today - but that is a property of the
        # values, not of the call, and it is exactly the silent-marshalling
        # trap _try_per_monitor_dpi documents in run_app.py.
        owner_of = user32.GetWindowThreadProcessId
        owner_of.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        owner_of.restype = wintypes.DWORD
        is_visible = user32.IsWindowVisible
        is_visible.argtypes = [wintypes.HWND]
        is_visible.restype = wintypes.BOOL

        callback_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        own_pid = os.getpid()

        def visit(hwnd: int, _lparam: int) -> bool:
            pid = wintypes.DWORD()
            owner_of(hwnd, ctypes.byref(pid))
            if pid.value == own_pid and is_visible(hwnd):
                handles.append(hwnd)
            return True

        user32.EnumWindows(callback_type(visit), 0)
    except (AttributeError, OSError):
        return []
    return handles


def exclude_from_screen_capture(hwnd: int) -> bool:
    """Hide one window from every capture path, and verify it took.

    The call is cheap to make and easy to believe without checking, so this
    reads the affinity back: GetWindowDisplayAffinity is the only thing that
    distinguishes "Windows accepted the flag" from "this build does not
    support it and said yes anyway".
    """
    if not _on_windows():
        return False
    try:
        user32 = ctypes.windll.user32
        setter = user32.SetWindowDisplayAffinity
        setter.argtypes = [wintypes.HWND, wintypes.DWORD]
        setter.restype = wintypes.BOOL
        getter = user32.GetWindowDisplayAffinity
        getter.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        getter.restype = wintypes.BOOL

        if not setter(hwnd, _WDA_EXCLUDEFROMCAPTURE):
            return False
        affinity = wintypes.DWORD()
        if not getter(hwnd, ctypes.byref(affinity)):
            return False
        return affinity.value == _WDA_EXCLUDEFROMCAPTURE
    except (AttributeError, OSError):
        return False


def screen_privacy_requested() -> bool:
    """Whether the user asked for capture exclusion. Off unless asked."""
    return os.environ.get(_SCREEN_PRIVACY_ENV, "") == "1"


def apply_screen_privacy() -> int:
    """Exclude this process's windows from capture. Returns how many took.

    A no-op unless ELYSIUM_SCREEN_PRIVACY=1, and a no-op that says so: the
    caller can run this unconditionally on the launch path.

    Covers the windows that exist WHEN IT RUNS. Wired to the shown event, that
    is the main window - which is where the conversation is. A window opened
    later (the native file picker, a permission prompt) is not covered, so
    with the switch on, a capture taken while one of those is up still records
    it. Closing that would mean hooking window creation for the lifetime of
    the process, which is not worth it for a dialog that shows filenames.
    """
    if not screen_privacy_requested():
        return 0
    excluded = sum(1 for hwnd in own_top_level_windows()
                   if exclude_from_screen_capture(hwnd))
    log.info("screen capture: %d window(s) excluded", excluded)
    return excluded


#: SDDL aliases and raw SIDs that mean "more than this machine's owner".
#: A deny-list rather than an allow-list, and the reason matters: an
#: allow-list needs this process's own SID, and getting that wrong turns a
#: warning into a false alarm on every launch. These are the principals whose
#: presence on a private data folder is unambiguously wrong.
_BROAD_PRINCIPALS: dict[str, str] = {
    "WD": "Everyone",
    "S-1-1-0": "Everyone",
    "AU": "Authenticated Users",
    "S-1-5-11": "Authenticated Users",
    "BU": "Users",
    "S-1-5-32-545": "Users",
    "BG": "Guests",
    "S-1-5-32-546": "Guests",
    "AN": "Anonymous",
    "S-1-5-7": "Anonymous",
    "IU": "Interactive",
    "NU": "Network",
    "WR": "Restricted Code",
}


#: SDDL ACE type codes that GRANT access. Audit types (AU, AL, SU) are
#: deliberately absent: they record, they do not permit.
_ALLOW_ACE_TYPES = frozenset({"A", "OA", "CA", "XA", "ZA"})


def _dacl_sddl(path: Path) -> str | None:
    """The folder's permission list as SDDL, or None if it cannot be read.

    SDDL rather than icacls: icacls prints localised names, so parsing it
    would break on a Turkish Windows. SDDL is a machine format and identical
    everywhere.
    """
    try:
        advapi32 = ctypes.windll.advapi32
        kernel32 = ctypes.windll.kernel32
        dacl_info = 0x00000004  # DACL_SECURITY_INFORMATION
        se_file_object = 1

        get_info = advapi32.GetNamedSecurityInfoW
        get_info.argtypes = [
            wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD,
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        get_info.restype = wintypes.DWORD

        descriptor = ctypes.c_void_p()
        if get_info(str(path), se_file_object, dacl_info,
                    None, None, None, None, ctypes.byref(descriptor)) != 0:
            return None
        try:
            to_string = advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW
            to_string.argtypes = [
                ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(wintypes.ULONG),
            ]
            to_string.restype = wintypes.BOOL
            text = wintypes.LPWSTR()
            length = wintypes.ULONG()
            if not to_string(descriptor, 1, dacl_info,
                             ctypes.byref(text), ctypes.byref(length)):
                return None
            try:
                return text.value
            finally:
                kernel32.LocalFree(text)
        finally:
            kernel32.LocalFree(descriptor)
    except (AttributeError, OSError, ValueError):
        return None


def data_dir_shared_with(path: Path | str) -> list[str]:
    """Principals beyond this machine's owner that can reach the data folder.

    Empty is the answer on a healthy install, and that was measured before
    this was written: %LOCALAPPDATA%\\Elysium inherits SYSTEM, Administrators
    and the current user, and nothing else. Which is exactly why this CHECKS
    rather than SETS.

    Tightening the folder was the obvious move and it buys nothing. Removing
    SYSTEM and Administrators does not stop an attacker running as this user -
    they already have full access - and does not stop an administrator, who
    can take ownership regardless. The default is already correct.

    What is NOT covered by the default is drift: a sync client, an installer,
    a backup tool or a stray right-click can widen it later, and nothing would
    ever say so. So this reports rather than repairs, because a silent repair
    would also hide the fact that something on this machine did that.
    """
    if not _on_windows():
        return []
    sddl = _dacl_sddl(Path(path))
    if not sddl:
        return []
    found: list[str] = []
    for ace in sddl.split("("):
        # (type;flags;rights;object;inherit;trustee) - the trustee is last.
        fields = ace.rstrip(")").split(";")
        # Allow-ACE type codes, spelled out. startswith("A") read as "allow"
        # and it is not one: it misses CA/XA/OA (callback, conditional and
        # object allow ACEs, which grant access without starting with A) while
        # matching AU/AL, which are audit entries and grant nothing. The whole
        # point of this function is to notice a widening, so a type filter
        # that quietly skips three ways of granting it is worse than none.
        if len(fields) < 6 or fields[0].strip().upper() not in _ALLOW_ACE_TYPES:
            continue
        name = _BROAD_PRINCIPALS.get(fields[5].strip().upper())
        if name and name not in found:
            found.append(name)
    return found


#: The SID for each principal data_dir_shared_with can name. SIDs rather than
#: names for the same reason _dacl_sddl reads SDDL rather than icacls output:
#: "Users" is "Benutzer" on a German Windows and "Kullanicilar" on a Turkish
#: one, and a removal that silently matches nothing is worse than no removal.
_SID_FOR_PRINCIPAL: dict[str, str] = {
    "Everyone": "S-1-1-0",
    "Authenticated Users": "S-1-5-11",
    "Users": "S-1-5-32-545",
    "Guests": "S-1-5-32-546",
    "Anonymous": "S-1-5-7",
    "Interactive": "S-1-5-4",
    "Network": "S-1-5-2",
    "Restricted Code": "S-1-5-33",
}

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

#: Full path rather than PATH lookup. A hardening step is the last place to
#: let the environment decide which binary runs.
_ICACLS = str(Path(os.environ.get("SystemRoot", r"C:\Windows"))
              / "System32" / "icacls.exe")


def _icacls(*args: str) -> bool:
    """Run icacls, quietly. True on exit 0."""
    try:
        done = subprocess.run(
            [_ICACLS, *args], capture_output=True, timeout=30,
            creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def narrow_data_dir(path: Path | str) -> list[str]:
    """Take the data folder back from any group beyond this machine's owner.

    Returns the principals whose access was actually removed.

    This module used to only REPORT a widened folder, and the argument for
    that was good: removing SYSTEM and Administrators buys nothing against an
    attacker already running as this user, and a silent repair hides that
    something on this machine widened it. Both halves survive here. What
    changed is the owner's decision that a second ACCOUNT on the same
    computer should not be able to read salt.bin and verifier.bin - the two
    files an offline passphrase attack needs - and that this has to hold for
    everyone who installs the app, not just on a machine somebody audited by
    hand.

    So it narrows AND says so. SYSTEM, Administrators and the owning user are
    never touched; only the broad groups in _BROAD_PRINCIPALS are, and each
    one removed is named in the log.

    Deliberately NOT recursive. In a development checkout the data folder is
    the source tree, `.venv` and all, and walking an ACL change through tens
    of thousands of files to fix a folder is the kind of "helpful" sweep that
    ends up in an incident report. Children inherit from the folder, so
    narrowing the folder is what carries; a child holding its own explicit
    grant is left alone and reported by the re-audit below.
    """
    if not _on_windows():
        return []
    target = str(Path(path))
    shared = data_dir_shared_with(target)
    if not shared:
        return []

    # Inherited ACEs cannot be removed at the child, so they are converted to
    # explicit ones first. This also stops the parent from handing the access
    # straight back the next time somebody widens IT, which - on a folder
    # under the Desktop - is precisely how this one arrived.
    if not _icacls(target, "/inheritance:d"):
        log.warning("data folder: could not detach inherited permissions; "
                    "leaving them alone rather than half-applying a change")
        return []

    removed: list[str] = []
    for principal in shared:
        sid = _SID_FOR_PRINCIPAL.get(principal)
        if not sid:                                       # pragma: no cover
            # A principal the audit can name but this table cannot address.
            # Reported, not guessed at.
            log.warning("data folder: %s can reach it and was NOT removed - "
                        "no SID mapping for it here", principal)
            continue
        # The FOLDER only, and that is enough: an inherited ACE on a child is
        # recomputed from its parent rather than owned by the child, so
        # removing it here removes it from salt.bin and verifier.bin as well.
        #
        # A reviewer read it the other way (that Windows materialises the ACE
        # onto each child, leaving the files behind) and that reading is what
        # a per-file loop here would be for. It was written, and then two
        # mutation rounds deleted it with every test still green - twice,
        # because the first fixture reproduced an inherited ACE rather than an
        # owned one. Unproven code in the app's hardening path is worse than
        # none: it reads as protection nobody has to check. The reporting side
        # investigated and left out, rather than kept "just in case".
        if _icacls(target, "/remove:g", f"*{sid}"):
            removed.append(principal)

    # Read it back rather than trusting the exit codes. icacls returns 0 for a
    # removal that matched nothing, and this module's habit everywhere else is
    # to verify the effect rather than the call.
    still = data_dir_shared_with(target)
    if removed:
        log.info("data folder: removed access for %s", ", ".join(removed))
    if still:
        log.warning(
            "data folder is STILL reachable by %s - the vault file is "
            "encrypted, but salt.bin and verifier.bin beside it are what an "
            "offline passphrase attack needs", ", ".join(still))
    return removed


def harden(data_dir: Path | str) -> dict[str, bool | int]:
    """Every protection in this module, in one call for the launch path.

    Returns what actually took effect rather than logging and forgetting, so
    a caller - or a test - can tell a working protection from a silent no-op.
    """
    # Narrow first, then report what is LEFT. The order matters for honesty:
    # reporting the pre-narrowing state would name groups that no longer have
    # access, and a warning nobody can act on is a warning people learn to
    # ignore. narrow_data_dir logs what it took away.
    narrowed = narrow_data_dir(data_dir)
    shared = data_dir_shared_with(data_dir)
    return {
        # First: it changes what every child spawned after this point can load.
        "dll_search_path_reset": reset_dll_search_path(),
        "crash_dump_heap_excluded": restrict_crash_dump_contents(),
        "search_index_excluded": exclude_from_search_index(data_dir),
        "windows_excluded_from_capture": apply_screen_privacy(),
        "shared_with": shared,
        "narrowed": narrowed,
    }


def restore_screen_capture(hwnd: int) -> bool:
    """Undo the exclusion for one window. The mirror of the function above.

    Read back like its twin, because SetWindowDisplayAffinity reports success
    for a call the compositor did not honour - and "the setting says off while
    the window is still black in every capture" is the shape of complaint
    nobody can diagnose.
    """
    if not _on_windows():
        return False
    try:
        user32 = ctypes.windll.user32
        setter = user32.SetWindowDisplayAffinity
        setter.argtypes = [wintypes.HWND, wintypes.DWORD]
        setter.restype = wintypes.BOOL
        getter = user32.GetWindowDisplayAffinity
        getter.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        getter.restype = wintypes.BOOL
        if not setter(hwnd, _WDA_NONE):
            return False
        affinity = wintypes.DWORD()
        if not getter(hwnd, ctypes.byref(affinity)):
            return False
        return affinity.value == _WDA_NONE
    except OSError:
        return False


def set_screen_privacy(enabled: bool) -> int:
    """Turn capture exclusion on or off for every window this process owns.

    Called on VAULT TRANSITIONS, not at launch, and that is not a detail:
    `harden()` runs before the server starts and long before a passphrase has
    been entered, so a setting stored inside the vault is unreadable there.
    The lock-aware rule resolves it - protection belongs on only while a
    conversation is on screen, and while the vault is locked there is nothing
    on screen but a passphrase box.

    Returns how many windows took the change; 0 on a build with no window
    (a bare uvicorn run), where this is a silent no-op by design.
    """
    fn = exclude_from_screen_capture if enabled else restore_screen_capture
    changed = sum(1 for hwnd in own_top_level_windows() if fn(hwnd))
    log.info("screen capture: %s on %d window(s)",
             "excluded" if enabled else "restored", changed)
    return changed
