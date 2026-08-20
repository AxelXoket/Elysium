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
  * Screen-capture exclusion covers PIXELS and nothing else. It was measured
    to defeat PrintWindow and BitBlt, and it was ALSO measured to leave the
    conversation fully readable as text through the accessibility tree, with
    the affinity flag confirmed set. That second leak is what the accessibility
    switch here closes, and the two are not substitutes for one another.
  * Closing the accessibility tree stops the DOM being read. It does not hide
    the WINDOW: its title, its class and its position stay visible to any
    process, as they are for every window on the desktop. Elysium's title is
    the constant "Elysium" and no code makes it say anything about the chat,
    which is what keeps that acceptable rather than merely unavoidable.
  * Nothing here touches ReadProcessMemory. A process running as this user can
    read the renderer's memory and recover the conversation from it whatever
    these switches say, and that cannot be closed from inside the app.

Measured on the target machine before it was written - see the test file for
the assertions that keep each call honest.
"""
from __future__ import annotations

import ctypes
import logging
import os
import re
import subprocess
from collections.abc import MutableMapping
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
#:
#: What it covers, and what it does NOT, now that both have been measured. It
#: does stop the pixels: PrintWindow and BitBlt against an excluded window come
#: back a fully black buffer, so the capture paths this was bought for are
#: genuinely closed. It does NOTHING about the accessibility tree, because that
#: is not pixels - it is the same conversation as TEXT, offered to any process
#: running as this user through UI Automation and MSAA. An audit read the whole
#: transcript out of it with the affinity flag confirmed set to 0x11. That leak
#: is closed by the switch below, not by this one.
_SCREEN_PRIVACY_ENV = "ELYSIUM_SCREEN_PRIVACY"

#: The Chromium switch that stops the renderer from building an accessibility
#: tree at all, spelled exactly as content_switches.cc spells it. The spelling
#: is load-bearing in a way that hurts: Chromium ignores a switch it does not
#: recognise, in silence, so one wrong character here is a privacy control that
#: never runs and never says so. That is the whole reason this module verifies
#: the browser process below instead of trusting that the string was passed.
#:
#: Chromium's own description of it is "Disables the accessibility tree for the
#: renderer process", and it is checked once, in BrowserAccessibilityStateImpl,
#: where it also forbids later changes to the accessibility mode. Both halves
#: matter here. Chromium builds this tree ON DEMAND - the first client that
#: asks escalates it - so a switch that only refused the first request would be
#: reopened by the second. And it is ONE tree: IAccessible/MSAA, IAccessible2
#: and UI Automation are all served from the same browser-process cache of it,
#: which is why suppressing the tree closes all three, while blocking any one
#: API surface would close none of the others.
_RENDERER_ACCESSIBILITY_OFF = "--disable-renderer-accessibility"

#: How the switch reaches WebView2. Its loader reads this variable and APPENDS
#: what it finds to the arguments the host process already set - measured on 20
#: August 2026 against runtime 151.0.4129.93, where pywebview's own
#: --disable-features=ElasticOverscroll and --allow-file-access-from-files were
#: still on the browser process command line beside ours.
#:
#: The environment rather than CoreWebView2CreationProperties.
#: AdditionalBrowserArguments, deliberately: that property is set inside
#: webview/platforms/edgechromium.py, which is a dependency this app does not
#: own. Reaching in to patch it would mean shipping a modified pywebview, and
#: a protection that lives in somebody else's file survives exactly until the
#: next `pip install -U`.
#:
#: Inherited by every child this process starts, which is harmless: the voice
#: worker and uv.exe are not WebView2 hosts and ignore it.
_WEBVIEW2_ARGUMENTS_ENV = "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"

#: The WebView2 browser process, which is the one carrying the arguments. Its
#: renderers are its own children, not ours, and are not consulted.
_WEBVIEW2_IMAGE = "msedgewebview2.exe"

#: Every environment variable the WebView2 loader consults, taken from
#: Microsoft's CreateCoreWebView2EnvironmentWithOptions reference rather than
#: from memory. All nine are writable by any program running as this user
#: with one setx and no elevation, and each one changes the browser that
#: renders the decrypted conversation:
#:
#:   BROWSER_EXECUTABLE_FOLDER  - supplies msedgewebview2.exe itself, so every
#:                                other check on this page is checking a binary
#:                                the attacker provided
#:   USER_DATA_FOLDER           - moves the browser profile somewhere
#:                                browser_profile.purge() has never heard of,
#:                                so the cache of the conversation outlives the
#:                                sweep that is supposed to remove it
#:   ADDITIONAL_BROWSER_ARGUMENTS - --remote-debugging-port and friends
#:   CHANNEL_SEARCH_KIND        - which runtime gets picked
#:   RELEASE_CHANNELS           - the same, by channel
#:   WAIT_FOR_SCRIPT_DEBUGGER   - halts the browser for a debugger to attach
#:   PIPE_FOR_SCRIPT_DEBUGGER   - hands the debugger a pipe
#:   RELEASE_CHANNEL_PREFERENCE - which channel is preferred
#:   USE_EDGE_VIEW              - a different hosting mode entirely
#:
#: The last two are the only ones this function can close COMPLETELY, and that
#: is worth stating precisely: Microsoft documents no registry equivalent for
#: them, while the other seven have one under Software\Policies\Microsoft\Edge
#: \WebView2 readable from HKCU as well as HKLM. Deleting a variable therefore
#: closes the environment door and says nothing about the registry door. See
#: webview2_policy_overrides, which looks at the second door without claiming
#: to shut it.
_WEBVIEW2_OVERRIDE_ENV = (
    "WEBVIEW2_BROWSER_EXECUTABLE_FOLDER",
    "WEBVIEW2_USER_DATA_FOLDER",
    _WEBVIEW2_ARGUMENTS_ENV,
    "WEBVIEW2_CHANNEL_SEARCH_KIND",
    "WEBVIEW2_RELEASE_CHANNELS",
    "WEBVIEW2_RELEASE_CHANNEL_PREFERENCE",
    "WEBVIEW2_USE_EDGE_VIEW",
    "WEBVIEW2_WAIT_FOR_SCRIPT_DEBUGGER",
    "WEBVIEW2_PIPE_FOR_SCRIPT_DEBUGGER",
)

#: Why these are DELETED rather than pinned to a value of our own, which is
#: the question a reader will ask, because a disassembly of loader_x64.dll on
#: 20 August 2026 answered it in the opposite direction first.
#:
#: The loader resolves each field independently, environment variable first
#: and the registry policy only when the variable is EMPTY. So setting a
#: non-empty value would suppress that field's policy lookup as well, and
#: deleting leaves the policy door for that field open. That reads like an
#: argument for pinning, and it is not one:
#:
#:   * The policy door is shut by Windows on a default install, not by us.
#:     HKCU\Software\Policies has a protected ACL owned by SYSTEM which grants
#:     the interactive user read only, and creating the subkey was measured to
#:     be DENIED without elevation on this machine. The hive the attacker can
#:     write is the environment; the hive they cannot is policy. Deleting aims
#:     at the door that is actually open.
#:   * A pinned BROWSER_EXECUTABLE_FOLDER has to name a real folder, and the
#:     installed runtime is evergreen: the version number in that path changes
#:     under us without warning. Pinning would trade an attack nobody can
#:     currently mount for an app that stops opening after a Microsoft update.
#:
#: Stated as a limit rather than a footnote: this is measured on one machine
#: and on one runtime version. A managed machine whose administrator has
#: relaxed that ACL is outside what was measured.


#: Switches that turn the window rendering the decrypted conversation into
#: something another program can read, and which therefore make "our flag is
#: present" the wrong question. A DENYLIST rather than an allowlist, and that
#: is deliberate: the WebView2 browser process carries dozens of switches it
#: gives itself, so an allowlist would report every future Chromium version as
#: hostile and be switched off within a week.
#:
#: What a denylist cannot do is catch the next dangerous switch before anyone
#: has heard of it. That is the honest limit, and it is why this sits beside
#: the environment scrub rather than replacing it.
_WEBVIEW2_DANGEROUS_SWITCHES = frozenset({
    "--remote-debugging-port",        # the DevTools protocol, over a socket
    "--remote-debugging-pipe",        # the same, over a pipe
    "--remote-allow-origins",         # who may speak to the above
    "--auto-open-devtools-for-tabs",
    "--disable-web-security",
    "--disable-site-isolation-trials",
    "--headless",                     # a window nobody sees is not our window
    "--dump-dom",
})


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


#: What the process keeps for itself after narrow_own_process runs, spelled
#: out because every bit in it was a decision and a future edit will want to
#: know which ones are load-bearing.
#:
#:   PROCESS_TERMINATE               Task Manager, taskkill, and the job the
#:                                   launcher puts children in. Measured: with
#:                                   this removed the app cannot be killed
#:                                   normally, which is a support nightmare
#:                                   for a protection nobody asked for.
#:   PROCESS_QUERY_LIMITED_INFORMATION
#:                                   run_app._own_image() and the single
#:                                   instance handover read it. Measured open
#:                                   with error 0 after narrowing.
#:   SYNCHRONIZE                     waiting on us to exit, which the
#:                                   PyInstaller bootloader does.
#:   READ_CONTROL                    reading the DACL back, which is how this
#:                                   function verifies its own work.
_PROCESS_KEEP_MASK = 0x00121001

#: The OWNER RIGHTS well known SID. Without an ACE for it this whole function
#: is theatre, and that was MEASURED rather than reasoned: Windows grants the
#: object's owner an implicit READ_CONTROL and WRITE_DAC, and we own our own
#: process. Three calls defeated the first version - OpenProcess(WRITE_DAC),
#: SetSecurityInfo putting PROCESS_ALL_ACCESS back, reopen with VM_READ - and
#: the planted secret came back verbatim. An OWNER RIGHTS ACE replaces that
#: implicit grant with an explicit one, and the same three calls then fail
#: with error 5.
_OWNER_RIGHTS_SID = "S-1-3-4"

_SE_KERNEL_OBJECT = 6
_DACL_SECURITY_INFORMATION = 0x00000004
#: PROTECTED, so nothing inherits its way back in. Measured honestly: removing
#: this flag changes NOTHING on this machine and no test goes red, because a
#: process object has no inheritable parent DACL to receive. It stays because
#: it costs a constant and states the intent, and because "it happens not to
#: matter here" is a worse reason to omit a protection than to include one.
_PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000


def narrow_own_process() -> bool:
    """Take PROCESS_VM_READ and friends away from everything, including us.

    WHAT THIS STOPS, and it is exactly one thing: a program running as this
    same user, with no elevation, opening this process by pid and reading the
    vault key, the decrypted API key or the launch token straight out of its
    memory. That is not hypothetical here. launch_token.py's docstring carries
    the evidence of a working exploit against this very project, "handle
    opened with error 0", and the fix applied that day moved the secret from
    the environment block into a module global - four kilobytes away, not out
    of reach.

    WHEN IT RUNS, and this is the whole reason the function exists at all. It
    must NOT go in harden(). That was tried on 20 August 2026 and measured:
    with the mask below in place before the window is created, the .NET CLR
    that pywebview's WinForms backend needs cannot initialise, `import clr`
    raises "Failed to initialize Python.Runtime.dll", and the app has no
    window. Every right that is not itself a full bypass was added back and it
    still failed, so the CLR needs one of VM_READ, VM_WRITE, DUP_HANDLE,
    CREATE_THREAD or WRITE_DAC, and every one of those hands the whole thing
    back.

    Called LATE it works. Measured three times with a real WebView2 window on
    a throwaway vault: the browser process starts, the renderer answers, a
    second navigation works, WebGL composites, and OpenProcess(VM_READ) from
    another process goes from error 0 to error 5. The CLR is already up by
    then and does not ask again.

    WHAT THE TIMING COSTS, said plainly rather than left for a reader to work
    out. The launch token is issued before the window and is therefore
    readable for the few seconds until this runs. That is not the loss it
    looks like: the token is handed to the browser in a URL fragment, so the
    WebView2 renderer holds it too, and that process is not ours and cannot be
    narrowed. The vault key is the prize here, and it does not exist until
    somebody types a passphrase, which is long after this.

    THE CEILING. An attacker who already held a handle before this ran keeps
    it, because Windows checks access when a handle is opened and not after.
    An administrator with SeDebugPrivilege ignores the DACL entirely. And the
    conversation itself is rendered by msedgewebview2.exe, a process this app
    does not own and cannot protect, so this denies the durable capability -
    offline decryption of app.db forever, plus the API key - and does nothing
    about what is on the screen right now. SECURITY.md says so and this does
    not make that sentence false.
    """
    if not _on_windows():
        return False
    try:
        advapi = ctypes.windll.advapi32
        kernel = ctypes.windll.kernel32

        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        me = kernel.GetCurrentProcess()

        user = _own_user_sid()
        if not user:
            return False
        sddl = (f"D:P(A;;0x{_PROCESS_KEEP_MASK:08X};;;{user})"
                f"(A;;0x{_PROCESS_KEEP_MASK:08X};;;{_OWNER_RIGHTS_SID})"
                f"(A;;GA;;;SY)")

        descriptor = ctypes.c_void_p()
        convert = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
        convert.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                            ctypes.POINTER(ctypes.c_void_p),
                            ctypes.POINTER(wintypes.DWORD)]
        convert.restype = wintypes.BOOL
        if not convert(sddl, 1, ctypes.byref(descriptor), None):
            return False

        dacl = ctypes.c_void_p()
        present = wintypes.BOOL()
        defaulted = wintypes.BOOL()
        get_dacl = advapi.GetSecurityDescriptorDacl
        get_dacl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL),
                             ctypes.POINTER(ctypes.c_void_p),
                             ctypes.POINTER(wintypes.BOOL)]
        get_dacl.restype = wintypes.BOOL
        if not get_dacl(descriptor, ctypes.byref(present),
                        ctypes.byref(dacl), ctypes.byref(defaulted)):
            kernel.LocalFree(descriptor)
            return False

        set_info = advapi.SetSecurityInfo
        set_info.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.DWORD,
                             ctypes.c_void_p, ctypes.c_void_p,
                             ctypes.c_void_p, ctypes.c_void_p]
        set_info.restype = wintypes.DWORD
        rc = set_info(me, _SE_KERNEL_OBJECT,
                      _DACL_SECURITY_INFORMATION
                      | _PROTECTED_DACL_SECURITY_INFORMATION,
                      None, None, dacl, None)
        kernel.LocalFree(descriptor)
    except (AttributeError, OSError, ValueError):
        return False
    ok = rc == 0
    log.info("process memory: %s", "closed to other processes as this user"
             if ok else "READABLE by any program running as this user")
    return ok


def _own_user_sid() -> str | None:
    """This process's user SID as a string, or None if it cannot be had."""
    try:
        out = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"],
                             capture_output=True, text=True, timeout=10,
                             creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    parts = [p.strip().strip('"') for p in (out.stdout or "").split(",")]
    for part in parts:
        if part.startswith("S-1-"):
            return part
    return None


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


def accessibility_privacy_requested() -> bool:
    """Whether the accessibility tree should be closed. ON unless refused.

    The mirror image of screen_privacy_requested, and the asymmetry is the
    point. That one is off until somebody types "1" because its cost is
    immediate and visible. This one is on until somebody types "0" because its
    cost falls on assistive technology, which most users do not run, while what
    it prevents is any program on this machine reading the conversation as
    text without asking anyone for permission.

    Exactly "0" turns it off. "false", "no", "off" and an empty value do not,
    because a privacy control that a typo can disable is a privacy control that
    reports its own state wrongly. apply_accessibility_privacy logs which state
    took effect, so a value that did not do what its author meant is visible in
    elysium.log rather than silent.
    """
    # config is imported here rather than at module scope on purpose: this
    # module is imported by run_app before the data directory is resolved, and
    # config computes DATA_DIR at import time from the environment.
    from config import ACCESSIBILITY_PRIVACY_ENV, ACCESSIBILITY_PRIVACY_OFF

    return os.environ.get(
        ACCESSIBILITY_PRIVACY_ENV, "") != ACCESSIBILITY_PRIVACY_OFF


#: A WebView2 switch name and nothing else, used to decide what may be NAMED
#: in the log below. The value half of a switch carries whatever the person who
#: planted it chose - a path with the account name in it, a redirected profile
#: directory, pasted text - so it is cut off at the first "=" and the residue
#: still has to look like a switch before it is printed. This pattern admits no
#: backslash, no colon, no space and nothing outside ASCII, so a bare path or a
#: fragment of somebody's writing cannot reach elysium.log through it.
_SWITCH_NAME = re.compile(r"^--[a-z0-9][a-z0-9-]*$")


#: Where the same five overrides live when they come from the registry rather
#: than the environment. HKLM is checked first and then HKCU, and HKCU needs no
#: elevation, which is what makes this worth looking at.
_WEBVIEW2_POLICY_KEY = r"Software\Policies\Microsoft\Edge\WebView2"

#: Written this way so the separator is never a lone escape in a source
#: line. It is the registry path separator, nothing more.
_SEP = chr(92)

#: The five policy names, in the order Microsoft lists them.
_WEBVIEW2_POLICY_NAMES = (
    "BrowserExecutableFolder",
    "ChannelSearchKind",
    "ReleaseChannels",
    "AdditionalBrowserArguments",
    "UserDataFolder",
)


def webview2_policy_overrides(open_key=None) -> list[str]:
    """Which WebView2 policy keys exist on this machine. Names, never values.

    LOOKS, does not touch. Nothing in this project writes to another product's
    policy keys, and a registry key under the Microsoft Edge WebView2 policy path is
    shared with every WebView2 application on this machine, not ours
    to edit.

    Reported because the honest limit of the environment scrub is exactly here.
    A policy value under HKCU is writable without elevation and can hand
    WebView2 a browserExecutableFolder of somebody else's choosing, at which
    point accessibility_privacy_verified is reading a command line off a binary
    the attacker supplied. Saying "the browser arguments are ours" while that
    is unexamined would be the kind of half-true claim this module exists to
    avoid.

    What is deliberately NOT claimed: Microsoft's reference says "if none of
    those environment variables exist, then the registry is examined next",
    and whether that is per field or for the block as a whole is not stated
    anywhere in the documentation. So setting our variable MIGHT suppress the
    registry entirely and might not. This function does not rely on either
    reading; it reports what is there and lets the log say so.

    open_key is an injection point for the test, in the same spirit as the
    environ argument on apply_accessibility_privacy: the alternative would be
    a test that writes to the machine's real policy hive to prove a read.
    """
    if open_key is None:
        if not _on_windows():
            return []
        import winreg

        roots = (("HKLM", winreg.HKEY_LOCAL_MACHINE),
                 ("HKCU", winreg.HKEY_CURRENT_USER))

        def open_key(root, subkey):                      # noqa: F811
            return winreg.OpenKey(root, subkey)
    else:
        roots = (("HKLM", "HKLM"), ("HKCU", "HKCU"))
    found = []
    for label, root in roots:
        for name in _WEBVIEW2_POLICY_NAMES:
            try:
                handle = open_key(root, _WEBVIEW2_POLICY_KEY + _SEP + name)
            except OSError:
                continue
            close = getattr(handle, "Close", None)
            if close is not None:
                close()
            found.append(label + _SEP + name)
    if found:
        log.warning(
            "webview2 policy: %s present under %s - a policy value can replace "
            "the browser binary or its profile folder, and this app does not "
            "and will not edit another product's policy keys",
            ", ".join(found), _WEBVIEW2_POLICY_KEY)
    return found


def _scrub_webview2_environment(env) -> list[str]:
    """Delete every WebView2 override variable. Returns the ones that were set.

    All nine, not just the one we care about, because the variable we set is
    the least dangerous member of the family. A planted
    WEBVIEW2_USER_DATA_FOLDER moves the browser profile out from under
    browser_profile.purge, which means the cached conversation survives the
    sweep written to remove it, and nothing about closing the accessibility
    tree would have noticed.

    Deletes rather than blanks. An empty variable is still a variable, and
    Microsoft's reference distinguishes "exist" from "non-empty" for some of
    these and not others, so the only state with one documented meaning is
    absent.
    """
    was_set = [name for name in _WEBVIEW2_OVERRIDE_ENV if env.get(name, "")]
    if _WEBVIEW2_ARGUMENTS_ENV in was_set:
        _report_planted_arguments(env.get(_WEBVIEW2_ARGUMENTS_ENV, ""))
    others = [name for name in was_set if name != _WEBVIEW2_ARGUMENTS_ENV]
    if others:
        # Names only. Two of these carry a filesystem path and a path on
        # this machine carries the account name; the rest carry channel
        # names and debugger flags. The value is withheld for all of them
        # rather than for the two, because a rule with an exception is a
        # rule somebody has to get right every time it changes.
        log.warning("webview2 environment: discarded %s - their values are "
                    "not printed here", ", ".join(others))
    for name in _WEBVIEW2_OVERRIDE_ENV:
        try:
            env.pop(name, None)
        except (KeyError, OSError):   # a mapping that refuses to forget
            pass
    stubborn = [name for name in _WEBVIEW2_OVERRIDE_ENV if env.get(name, "")]
    if stubborn:
        log.warning("webview2 environment: %s could NOT be cleared and the "
                    "contents will reach the browser process",
                    ", ".join(stubborn))
    return was_set


def _report_planted_arguments(existing: str) -> None:
    """Say that somebody had written to the WebView2 variable. Names only.

    Silence here would be the worst outcome of the scrub: the value is gone,
    the attempt is invisible, and the machine looks healthy. So it is a
    warning rather than an info - a value on this variable is either an
    attacker or a misconfigured machine, and both are worth a line.

    Counts what it will not name rather than dropping it, because "two tokens
    were discarded and I would not repeat them" is a different fact from
    "nothing was there".
    """
    tokens = existing.split()
    if not tokens:
        return
    heads = [token.split("=", 1)[0] for token in tokens]
    printable = [head for head in heads if _SWITCH_NAME.match(head)]
    named = sorted(set(printable))
    # Against the printable LIST, not the deduplicated set: two copies of the
    # same switch are two named tokens, and subtracting the set would report
    # the duplicate as something we refused to name.
    unnamed = len(tokens) - len(printable)
    log.warning(
        "webview2 arguments: discarded %d pre-existing token(s) from %s - %s%s",
        len(tokens), _WEBVIEW2_ARGUMENTS_ENV,
        ", ".join(named) or "none in switch form",
        f" (and {unnamed} token(s) whose names are not printed here)"
        if unnamed else "")


def apply_accessibility_privacy(
    environ: MutableMapping[str, str] | None = None,
) -> bool:
    """Arm the switch for the WebView2 that has not been created yet.

    Everything else in this module acts on something that already exists - a
    process, a folder, a window. This one cannot: browser arguments are read
    once, when the WebView2 environment is created, and after that the tree is
    either being built or it is not. So this runs on the launch path, BEFORE
    the window, and what it changes is the environment the browser process will
    inherit.

    Which also means this switch cannot follow the vault lock the way
    set_screen_privacy does, and cannot be changed while the app runs. Nothing
    here is stored in the vault either: the decision has to be made before a
    passphrase exists, and the way out of it has to work for somebody who
    cannot read the screen at all.

    Returns whether the argument is in place, read back out of the environment
    rather than assumed from having written it. That read-back is the weaker
    half of the house rule and this module should not pretend otherwise: it
    proves the variable says what we meant, not that WebView2 honoured it.
    accessibility_privacy_verified() is the half that asks the browser process.

    Assigns rather than appends, and the reversal is the point. This function
    used to append, on the argument that something else in the environment may
    already be passing arguments to WebView2 and that clobbering somebody
    else's flags would break a machine we have never seen. That argument was
    measured on 20 August 2026 and does not hold: pywebview supplies its own
    flags through CoreWebView2CreationProperties.AdditionalBrowserArguments
    (webview/platforms/edgechromium.py:82-90), a channel this variable does not
    touch, and nothing else in this repository writes the variable at all. So
    the only thing appending preserved was a value somebody else put there.

    Which is the whole problem. The variable is writable by any program running
    as this user with a single setx and no elevation, and what it carries goes
    onto the browser process command line. --remote-debugging-port opens the
    DevTools protocol on the window that is rendering the decrypted
    conversation, and hands over the launch token in the URL fragment besides.
    Appending politely forwarded that. Assigning refuses it.

    Both branches scrub, including the one where the user refused the switch.
    That branch used to return without touching the variable at all, which
    meant the user who turned this off was the one user whose planted flags
    sailed through - and a user who turned it off to run assistive technology
    is exactly the user least able to notice a warning on screen. The refusal
    branch DELETES the key rather than blanking it, because an empty variable
    is still a variable and harden's own refusal test asks whether the name is
    absent.
    """
    env = os.environ if environ is None else environ
    _scrub_webview2_environment(env)
    if not accessibility_privacy_requested():
        log.info("accessibility tree: left OPEN at the user's request - the "
                 "conversation is readable by any program running as this user")
        return False
    env[_WEBVIEW2_ARGUMENTS_ENV] = _RENDERER_ACCESSIBILITY_OFF
    # Equality, not membership. Membership would call a variable carrying our
    # flag AND a planted one armed, which is the state this function exists to
    # make impossible.
    armed = env.get(
        _WEBVIEW2_ARGUMENTS_ENV, "").split() == [_RENDERER_ACCESSIBILITY_OFF]
    log.info("accessibility tree: %s", "closed" if armed else "NOT closed")
    return armed


class _UNICODE_STRING(ctypes.Structure):
    _fields_ = [
        ("Length", wintypes.USHORT),
        ("MaximumLength", wintypes.USHORT),
        ("Buffer", ctypes.c_void_p),
    ]


class _PROCESS_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("Reserved1", ctypes.c_void_p),
        ("PebBaseAddress", ctypes.c_void_p),
        ("Reserved2", ctypes.c_void_p * 2),
        ("UniqueProcessId", ctypes.c_void_p),
        ("Reserved3", ctypes.c_void_p),
    ]


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


#: Offsets into the 64-bit PEB. Undocumented in the sense that Microsoft
#: reserves the right to move them, and stable in the sense that they have not
#: moved in twenty years - which is why every failure below returns "cannot
#: tell" instead of "not protected". A wrong offset must never be reported as
#: a broken protection: that is a false alarm on every launch, and a warning
#: nobody can act on is a warning people learn to ignore.
_PEB_PROCESS_PARAMETERS = 0x20
_PARAMETERS_COMMAND_LINE = 0x70

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROCESS_VM_READ = 0x0010
_TH32CS_SNAPPROCESS = 0x00000002
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


def _same_bitness(handle: int) -> bool:
    """Whether another process runs under the same PEB layout as this one."""
    kernel32 = ctypes.windll.kernel32
    is_wow64 = kernel32.IsWow64Process
    is_wow64.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    is_wow64.restype = wintypes.BOOL
    theirs, ours = wintypes.BOOL(), wintypes.BOOL()
    if not is_wow64(wintypes.HANDLE(handle), ctypes.byref(theirs)):
        return False
    if not is_wow64(kernel32.GetCurrentProcess(), ctypes.byref(ours)):
        return False
    return bool(theirs.value) == bool(ours.value)


def command_line_of(pid: int) -> str | None:
    """Another process's command line, or None if it cannot be read.

    Windows has no supported API for this. WMI is the usual answer and is not
    one here: it means starting a process, on the launch path, to check a flag.
    So this reads the PEB, the way every process explorer does, and treats
    every way that can go wrong as "cannot tell" rather than as an answer.
    """
    if not _on_windows():
        return None
    try:
        kernel32 = ctypes.windll.kernel32
        ntdll = ctypes.windll.ntdll
        handle = kernel32.OpenProcess(
            _PROCESS_QUERY_LIMITED_INFORMATION | _PROCESS_VM_READ, False, pid)
        if not handle:
            return None
        try:
            if not _same_bitness(handle):
                return None
            info = _PROCESS_BASIC_INFORMATION()
            written = wintypes.ULONG()
            if ntdll.NtQueryInformationProcess(
                    handle, 0, ctypes.byref(info), ctypes.sizeof(info),
                    ctypes.byref(written)) != 0:
                return None
            if not info.PebBaseAddress:
                return None

            def read(address: int, size: int) -> bytes | None:
                buffer = (ctypes.c_char * size)()
                got = ctypes.c_size_t()
                if not kernel32.ReadProcessMemory(
                        handle, ctypes.c_void_p(address), buffer, size,
                        ctypes.byref(got)) or got.value != size:
                    return None
                return buffer.raw

            pointer = read(info.PebBaseAddress + _PEB_PROCESS_PARAMETERS, 8)
            if pointer is None:
                return None
            raw = read(int.from_bytes(pointer, "little")
                       + _PARAMETERS_COMMAND_LINE,
                       ctypes.sizeof(_UNICODE_STRING))
            if raw is None:
                return None
            text = _UNICODE_STRING.from_buffer_copy(raw)
            if not text.Buffer or not text.Length:
                return None
            body = read(text.Buffer, text.Length)
            if body is None:
                return None
            return body.decode("utf-16-le", "replace")
        finally:
            kernel32.CloseHandle(handle)
    except (AttributeError, OSError, ValueError):
        return None


def own_child_processes(image_name: str) -> list[int]:
    """Process ids of THIS process's direct children with that exe name.

    Direct children only, matched on the parent id, for the same reason
    own_top_level_windows matches on the process id: another copy of the same
    program running for somebody else is not the question being asked.
    """
    if not _on_windows():
        return []
    found: list[int] = []
    try:
        kernel32 = ctypes.windll.kernel32
        snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
        if snapshot == _INVALID_HANDLE_VALUE or not snapshot:
            return []
        try:
            entry = _PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
            own = os.getpid()
            more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            while more:
                if (entry.th32ParentProcessID == own
                        and entry.szExeFile.lower() == image_name.lower()):
                    found.append(entry.th32ProcessID)
                more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(snapshot)
    except (AttributeError, OSError):
        return []
    return found


def accessibility_privacy_verified() -> bool | None:
    """Did the browser process actually get the switch? None means unknown.

    Three answers rather than two, and the third one is the honest part. True
    means the argument was found on the command line of every WebView2 browser
    process this app started. False means one of them started WITHOUT it, which
    is the silent failure this whole function exists for - pywebview changing
    how it builds those arguments, or a WebView2 loader that stops reading the
    environment variable, would otherwise look exactly like success.

    None means the question could not be answered: no browser process yet, or
    the PEB could not be read. It is deliberately not False. Reporting "not
    protected" when the truth is "could not check" trains everybody to ignore
    the message, and this one has to still mean something the day it fires.

    Only THIS process's own children are asked, and that is not tidiness. An
    earlier attempt at this check enumerated every WebView2 process on the
    machine and cheerfully reported an argument that this run had never
    carried, picked up from something else's leftover browser process.

    The stronger version of this check, and why it is not here: the app could
    attack its own window through UI Automation and assert the web document
    exposes no children and no text, which would test the EFFECT rather than
    the argument. It was designed and deliberately not shipped. It needs a
    separate MTA thread or a child process to avoid deadlocking against our own
    UI thread, and neither could be measured on this machine under the
    constraint the work was finished under - so it would have gone into the
    launch path unproven, which is the one thing this module has twice refused
    to do. It exists instead in tests/accessibility_tree_harness.py, run by
    hand, where it is the attack rather than a claim.
    """
    if not accessibility_privacy_requested():
        return None
    lines = []
    for pid in own_child_processes(_WEBVIEW2_IMAGE):
        line = command_line_of(pid)
        if line is not None:
            lines.append(line)
    if not lines:
        return None
    return all(ours and not dangerous
               for ours, dangerous in map(_command_line_is_ours, lines))


def _command_line_is_ours(line: str) -> tuple[bool, frozenset[str]]:
    """Our switch present, and which denylisted switches came with it.

    Returns the two halves SEPARATELY because a caller that cannot tell them
    apart writes the wrong sentence. The first draft returned one boolean, and
    report_accessibility_privacy then logged "the browser did not get
    --disable-renderer-accessibility" for a browser that had got it and was
    ALSO carrying --remote-debugging-port. Measured: the two cases produced a
    byte-identical warning, and the switch that was actually listening on the
    process rendering the conversation was named nowhere in the log.

    This function used to ask only whether _RENDERER_ACCESSIBILITY_OFF was
    present, which was the wrong question in a way that mattered: because the
    loader APPENDS what it finds to what the host set, a planted
    --remote-debugging-port arrives alongside our flag rather than instead of
    it. So the old check found our flag, returned True, and
    report_accessibility_privacy logged "verified closed on the browser
    process" while the DevTools protocol was listening on that same process.
    A verification that reports success during the attack it was written to
    notice is worse than no verification, because somebody reads that line and
    stops looking.
    """
    switches = {token.split("=", 1)[0] for token in line.split()}
    if _RENDERER_ACCESSIBILITY_OFF not in switches:
        return False, frozenset()
    return True, frozenset(switches & _WEBVIEW2_DANGEROUS_SWITCHES)


def report_accessibility_privacy() -> bool | None:
    """Log what the browser process really got. Wired to the window's events.

    Takes no arguments because pywebview inspects the signature and calls a
    zero-argument handler with none.
    """
    verdict = accessibility_privacy_verified()
    # Which of the two failures happened, so the line names the real one.
    planted: set[str] = set()
    if verdict is False:
        for pid in own_child_processes(_WEBVIEW2_IMAGE):
            line = command_line_of(pid)
            if line is not None:
                planted |= _command_line_is_ours(line)[1]
    if verdict is False and planted:
        log.warning(
            "accessibility tree: the flag IS on the WebView2 browser process, "
            "but so is %s - something has opened a debugging channel on the "
            "process drawing the conversation. Switch names only; no values "
            "are printed here", ", ".join(sorted(planted)))
        return verdict
    if verdict is True:
        log.info("accessibility tree: verified closed on the browser process")
    elif verdict is False:
        log.warning(
            "accessibility tree: the switch is ON but the WebView2 browser "
            "process did not get %s - the conversation is readable as text by "
            "any program running as this user", _RENDERER_ACCESSIBILITY_OFF)
    return verdict


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
        # Also has to be early. It is read when the WebView2 environment is
        # created, and after that the tree exists or it does not.
        "accessibility_tree_closed": apply_accessibility_privacy(),
        # LOOKS, does not touch, and it has to be called from here or the
        # claim is false. It was written, tested and then never wired in:
        # SECURITY.md told the reader that Elysium reports a WebView2 policy
        # key while the only thing that ever called this was pytest. A control
        # that runs nowhere is a sentence, and this file exists to not write
        # those.
        "webview2_policy_overrides": webview2_policy_overrides(),
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
