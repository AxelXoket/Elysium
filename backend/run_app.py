"""run_app.py - Elysium desktop launcher.

Serves the app (API + the built frontend) on a loopback port from THIS process
and shows it in a native window. Closing the window returns from
webview.start(), the process exits, and the vault key - held only in RAM - is
gone. So the vault locks on close, exactly as intended; reopening the app shows
the lock screen and asks for the passphrase.

That promise only holds while ONE process owns the data folder, so a second
launch against the same folder is refused before it can touch anything. See
enforce_single_instance.
"""
from __future__ import annotations

import atexit
import ctypes
import hashlib
import logging
import os
import socket
import sys
import threading
import time
import urllib.request
import winreg
from ctypes import wintypes

import uvicorn
import webview

import browser_profile
import launch_token
import win_hardening

HOST = "127.0.0.1"
WINDOW_TITLE = "Elysium"
WEBVIEW2_DOWNLOAD = "https://developer.microsoft.com/microsoft-edge/webview2/"

# Loopback probes must NEVER go through a proxy: with a system-wide proxy
# configured (likely for this app's audience), Windows proxies 127.0.0.1 too
# unless an explicit bypass exists - the probe would then miss the local
# server and the app would look like it "does not start". The app's own
# traffic already uses trust_env=False (network_client.py); this opener is
# the launcher-side version of the same rule.
_LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _alert(message: str) -> None:
    """Native error dialog - the only failure surface a windowed exe has
    (no console, and SystemExit shows no PyInstaller traceback box)."""
    try:
        ctypes.windll.user32.MessageBoxW(None, message, WINDOW_TITLE, 0x10)
    except Exception:
        pass


def _setup_frozen_logging() -> None:
    """Windowed exe has no stderr, so a failed start would leave no trace.
    Route logs to DATA_DIR/elysium.log instead. Registered BEFORE
    `from main import app` runs, which makes main.py's logging.basicConfig a
    no-op (root already has a handler). Startup logs carry no chat content,
    keys, or passphrases (audited), so a log file is privacy-compatible."""
    if not getattr(sys, "frozen", False):
        return
    try:
        from logging.handlers import RotatingFileHandler

        from config import DATA_DIR

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            DATA_DIR / "elysium.log",
            maxBytes=512_000,
            backupCount=1,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
        )
        logging.basicConfig(level=logging.INFO, handlers=[handler])

        # The config import above happened BEFORE this handler existed, so a
        # redirected OPENROUTER_BASE_URL - the one thing that can send the API
        # key to an arbitrary host - warned into a dead stderr and was absent
        # from elysium.log in exactly the build where nothing else could show
        # it. Report it now that there is somewhere for it to land.
        import config as _config
        _config.warn_if_base_url_overridden()

        # uvicorn does NOT propagate to root: `uvicorn.Config(...)` installs its
        # own handlers on "uvicorn", "uvicorn.error" and "uvicorn.access" with
        # propagate=False. So a windowed build wrote every application log to
        # the file and every SERVER log to a stderr that does not exist - and
        # the failure dialog told people the details were in elysium.log.
        # Attaching the same handler is what makes that sentence true.
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            server_logger = logging.getLogger(name)
            server_logger.addHandler(handler)
            server_logger.setLevel(logging.INFO)
    except Exception:
        pass  # diagnostics must never block the launch


def _webview2_installed() -> bool:
    """Detect the WebView2 Evergreen runtime via its canonical registry keys.
    Without it pywebview silently falls back to the legacy IE engine and the
    React bundle renders a blank white window - better to say so up front."""
    client = r"\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    locations = (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node" + client),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE" + client),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE" + client),
    )
    for root, key_path in locations:
        try:
            with winreg.OpenKey(root, key_path) as key:
                version, _ = winreg.QueryValueEx(key, "pv")
                if version and version != "0.0.0.0":
                    return True
        except OSError:
            continue
    return False


# ── One instance, one vault ──────────────────────────────────────────────────
#
# CreateMutexW hands back a valid handle whether or not the name was already
# taken, so this code, not the return value, is what says somebody got here
# first.
_ALREADY_EXISTS = 183  # ERROR_ALREADY_EXISTS

#: The kernel handle IS the claim. Held for the life of the process and never
#: waited on: the only question ever asked is whether the NAME exists.
_instance_mutex: int | None = None


def _close_handle(handle: int) -> None:
    """CloseHandle with its argtypes declared. Not optional: a HANDLE is
    pointer sized, and an undeclared ctypes call marshals a Python int as a
    32-bit value, so on 64-bit Windows the wrong handle (or none) gets closed
    and the failure is silent."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    close = kernel32.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    close(handle)


def _instance_mutex_name() -> str:
    """One claim per DATA FOLDER, not one per machine.

    What must not happen twice is not "Elysium runs", it is "two processes
    share one vault". vault_state keeps the key in a module global, so window A
    and window B each hold their own copy of it: locking A clears A's key and
    leaves B sitting on a fully decryptable database. The user performs the one
    gesture the whole at-rest design rests on and gets half of it, and auto
    lock has exactly the same blind spot. So the folder is the thing being
    guarded, and the name says so.

    That also keeps the harmless case legal. Two runs pointed at two different
    ELYSIUM_DATA_DIRs share no key, no database, no port file and no launch
    token, so they may sit side by side; that is what keeps the frozen self
    check runnable while the real app is open, since it always redirects
    ELYSIUM_DATA_DIR at a throwaway folder.

    normcase and abspath because Windows paths are case insensitive and the
    data folder arrives spelled differently depending on who expanded it. A
    hash because a kernel object name may not contain a backslash after its
    namespace prefix, and a path is mostly backslashes.

    Local rather than Global for the namespace: Local is per logon session,
    which is the same boundary the data folder already has, and it needs no
    privilege. Two people signed in to one machine get an app each instead of
    one of them locking the other out.
    """
    from config import DATA_DIR

    key = os.path.normcase(os.path.abspath(str(DATA_DIR)))
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
    return "Local\\Elysium-vault-" + digest


def claim_single_instance() -> bool:
    """True if THIS process now owns the data folder, False if another one does.

    A named mutex rather than a lockfile, and the reason is the kill case. A
    lockfile outlives whatever wrote it, so every later reader has to guess
    whether the pid inside is still the app or a number the OS has since handed
    to something else. Guess wrong one way and a crashed app can never be
    reopened; wrong the other way and the guard is decoration. A kernel object
    has no stale state to reason about: the name exists exactly as long as some
    handle to it does, and Windows closes every handle a process holds when it
    dies, whether it exited, crashed, or was ended from Task Manager. Nothing
    is written to disk, so there is nothing left to clean up.

    use_last_error=True builds a private WinDLL whose thunk saves the thread's
    error code the instant the call returns. ctypes.windll is a shared, cached
    handle without it, and then the one value this function exists to read
    could be overwritten by any ctypes bookkeeping in between, which would
    silently turn the guard off rather than break it loudly.
    """
    global _instance_mutex
    if os.name != "nt":
        return True
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel32.CreateMutexW
        create.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        create.restype = wintypes.HANDLE
        handle = create(None, False, _instance_mutex_name())
        code = ctypes.get_last_error()
    except Exception:
        # A guard that cannot run must never become the reason the app will not
        # open. Losing it costs the second-window protection; refusing here
        # would cost the app.
        logging.getLogger(__name__).warning(
            "single-instance guard unavailable; starting anyway", exc_info=True)
        return True
    if not handle:
        return True
    if code == _ALREADY_EXISTS:
        # Somebody else's object, opened by name. Let go of it immediately:
        # holding a handle to a mutex we do not own would keep the name alive
        # after the real instance quits.
        _close_handle(handle)
        return False
    _instance_mutex = handle
    return True


def release_single_instance() -> None:
    """Give the claim back. Only the tests call this, and that is the point: a
    real run holds it until the process ends, which is precisely the property a
    lockfile could not offer. Closing the last handle destroys the named object
    and lets the next launch straight through."""
    global _instance_mutex
    handle, _instance_mutex = _instance_mutex, None
    if handle:
        try:
            _close_handle(handle)
        except Exception:
            pass


def _process_image(pid: int) -> str:
    """Full path of the executable behind a pid, normcased, or "" if it cannot
    be read. QUERY_LIMITED_INFORMATION rather than QUERY_INFORMATION because it
    is the right that survives another process running at a different integrity
    level, and this only ever asks for a name."""
    query_limited_information = 0x1000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_process.restype = wintypes.HANDLE
    handle = open_process(query_limited_information, False, pid)
    if not handle:
        return ""
    try:
        query = kernel32.QueryFullProcessImageNameW
        query.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                          ctypes.POINTER(wintypes.DWORD)]
        query.restype = wintypes.BOOL
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not query(handle, 0, buffer, ctypes.byref(size)):
            return ""
        return os.path.normcase(buffer.value)
    finally:
        _close_handle(handle)


def _own_image() -> str:
    """This process's executable as the KERNEL reports it, not as
    sys.executable claims it. Inside a virtualenv the two disagree:
    sys.executable is the shim in .venv and the kernel reports the interpreter
    behind it, so comparing one against the other would never match in dev and
    every second launch would fall through to the dialog. sys.executable is
    only the fallback for the case where even our own image cannot be read."""
    return _process_image(os.getpid()) or os.path.normcase(
        os.path.abspath(sys.executable))


def _find_app_window(title: str = WINDOW_TITLE) -> int:
    """HWND of a window belonging to another copy of this same program, or 0.

    Two filters, and the second one is why this is not a one line FindWindowW.
    A title is not an identity: the packaged app's data folder is itself named
    Elysium, and an Explorer window sitting in it is titled exactly "Elysium".
    Raising THAT and then going quiet would be the precise failure this path
    exists to prevent, so the owning process must also be running the same
    image we are (python.exe in dev, the exe when frozen).

    The port file was the other candidate and it cannot do this job. It says a
    server answered, not where that server's window is, and the API is launch
    token gated on purpose, so we could not ask the other instance to show
    itself even if we wanted to.

    No filter on our own pid, deliberately: this runs before
    webview.create_window, so at this moment this process owns no window at
    all. The `title` argument exists so a test can look for a name nothing else
    on the machine could be carrying; the default is the only value the app
    itself ever passes.
    """
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    text_of = user32.GetWindowTextW
    text_of.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    text_of.restype = ctypes.c_int
    owner_of = user32.GetWindowThreadProcessId
    owner_of.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    owner_of.restype = wintypes.DWORD
    walk = user32.EnumWindows
    walk.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
    walk.restype = wintypes.BOOL

    mine = _own_image()
    found: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(hwnd, _lparam):
        buffer = ctypes.create_unicode_buffer(256)
        text_of(hwnd, buffer, 256)
        if buffer.value != title:
            return True
        pid = wintypes.DWORD()
        owner_of(hwnd, ctypes.byref(pid))
        if _process_image(pid.value) != mine:
            return True
        found.append(int(hwnd))
        return False  # stop walking: one is all this needs

    walk(callback_type(visit), 0)
    return found[0] if found else 0


def _raise_existing_window(title: str = WINDOW_TITLE) -> bool:
    """Put the instance that is already running back in front of the user.
    True if a window of ours was found and asked to come forward.

    SetForegroundWindow's return value is deliberately not the answer. Windows
    refuses foreground changes from a process the user did not just interact
    with, so it can fail while the restore above it has already put the window
    back on screen. Treating that as a failure would stack an error dialog on
    top of a window the user is looking at.
    """
    try:
        hwnd = _find_app_window(title)
        if not hwnd:
            return False
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        show = user32.ShowWindow
        show.argtypes = [wintypes.HWND, ctypes.c_int]
        show.restype = wintypes.BOOL
        show(hwnd, 9)  # SW_RESTORE: un-minimise, leaving a normal window's size alone
        front = user32.SetForegroundWindow
        front.argtypes = [wintypes.HWND]
        front.restype = wintypes.BOOL
        front(hwnd)
        return True
    except Exception:
        return False


def enforce_single_instance() -> None:
    """The second double click ends here, and it ends BEFORE anything touches
    the data folder.

    Order matters more than it looks, though not for the reason this comment
    used to give. It claimed a second process reaching launch_token.issue()
    would overwrite the token the live window is using and lock the app out of
    its own backend. That was never true: issue() has always been per process,
    and now that it writes nothing outside this process's memory it plainly
    cannot touch another launch. The real reason is the vault. A second copy
    that gets past this line holds its own copy of the vault key against the
    same folder, so locking one window would leave the other one open, and
    that is a half locked vault rather than an inconvenience.
    win_hardening.harden and the vault come after this, so it is the first
    thing main() does with any knowledge of DATA_DIR.

    IT APPLIES IN DEV TOO. The split key is a property of the code, not of the
    packaging: `python run_app.py` twice against the same folder produces the
    identical half locked vault. There is no env flag to switch this off,
    because a flag that lets two processes share one folder is only a
    documented way back into the hole. A developer who wants two windows sets
    ELYSIUM_DATA_DIR on one of them, which gives it a vault of its own and is
    safe by construction rather than by promise.
    """
    if claim_single_instance():
        return
    logging.getLogger(__name__).info(
        "another Elysium already holds this data folder; handing over to it")
    if not _raise_existing_window():
        # Never a silent exit. If we could not find the window then we cannot
        # assume the user can see it either, and an app that vanishes on a
        # double click is indistinguishable from an app that crashed.
        _alert(
            "Elysium is already running.\n\n"
            "Only one copy can use a data folder at a time. Two copies would "
            "each hold their own copy of the vault key, so locking one would "
            "leave the other one wide open.\n\n"
            "Switch to the Elysium window that is already open."
        )
    # 0, not 1. Nothing failed here: the user asked for Elysium and Elysium is
    # on screen. A non zero code would make the shell, and any script that
    # launches this, report an error for a launch that did what was wanted.
    raise SystemExit(0)


def _port_file():
    from config import DATA_DIR

    return DATA_DIR / "port"


def _remembered_port() -> int:
    try:
        value = int(_port_file().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0
    return value if 1024 <= value <= 65535 else 0


def _remember_port(port: int) -> None:
    try:
        path = _port_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(port), encoding="utf-8")
    except OSError:
        # A port we cannot remember only costs the next launch its stored UI
        # preferences; it must never stop the app from starting.
        logging.getLogger(__name__).warning("could not remember the app port")


def bind_app_socket() -> socket.socket:
    """Bind (not listen) the server socket here and hand it to uvicorn:
    no close-then-rebind gap, so the port cannot be lost to another process
    between picking it and serving on it. uvicorn's loop.create_server()
    takes over the bound socket and calls listen() itself.

    THE PORT IS REMEMBERED. localStorage and IndexedDB are keyed by
    scheme://host:port, so binding 0 every launch handed the persistent
    WebView2 profile a brand-new, empty storage bucket each time: font size,
    contrast preset, narration style, the chat wallpaper (its IndexedDB blob
    orphaned under the dead origin) and the last-open chat all reverted to
    defaults - exactly what private_mode=False was turned off to prevent - and
    every dead origin's storage stayed on disk forever. The shipping profile
    had twelve of them, one per port it had ever used.

    Still bound, never assumed: if the remembered port is taken (another
    instance, or something else claimed it), we fall back to an ephemeral one
    and remember THAT. A lost preference beats a refusal to start.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    wanted = _remembered_port()
    if wanted:
        try:
            sock.bind((HOST, wanted))
            return sock
        except OSError:
            logging.getLogger(__name__).info(
                "app port %d is busy; taking a new one", wanted,
            )
            sock.close()
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((HOST, 0))
    _remember_port(sock.getsockname()[1])
    return sock


def wait_until_ready(url: str, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with _LOCAL_OPENER.open(url, timeout=1.0) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.15)
    return False


def serve(sock: socket.socket) -> None:
    """Start uvicorn on the pre-bound socket in a daemon thread. Daemon so it
    dies with the process when the window closes (which is what locks the
    vault)."""
    from main import app  # lazy: builds the app after any freeze setup

    port = sock.getsockname()[1]
    config = uvicorn.Config(app, host=HOST, port=port, log_level="warning")
    server = uvicorn.Server(config)
    # A non-main thread (and Windows' proactor loop) cannot install signal
    # handlers; disable them. The window close, not a signal, ends the app.
    server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
    threading.Thread(
        target=server.run,
        kwargs={"sockets": [sock]},
        name="uvicorn",
        daemon=True,
    ).start()


def _selftest(base: str) -> None:
    """Headless boot check (ELYSIUM_SELFTEST=1): proves the FROZEN exe's Python
    side works - imports resolve, the SQLCipher native lib loads, the server
    starts, and the bundled frontend serves - without needing a display for the
    window. Exits 0 on success."""
    healthz = wait_until_ready(base + "/healthz")
    try:
        # With the token gate armed, even our own probe has to present it.
        # This is the launcher, in the same process that issued it - the point
        # of the gate is that a DIFFERENT process cannot.
        probe = urllib.request.Request(
            base + "/api/v1/vault/status",
            headers={launch_token.HEADER: launch_token.configured() or ""},
        )
        status = _LOCAL_OPENER.open(probe, timeout=3).read().decode()
        root = _LOCAL_OPENER.open(base + "/", timeout=3).read().decode()
        root_ok = 'id="root"' in root
    except Exception as exc:  # pragma: no cover
        print(f"SELFTEST_FAIL {exc}", flush=True)
        sys.exit(1)
    # The two things the custom spec files EXIST for (audit KÖK 13). The HTTP
    # checks above pass on a build with no voice at all: the worker scripts and
    # the engine requirements are plain data files, so PyInstaller drops them
    # unless the spec says otherwise - and nothing here or in
    # test_tts_packaging.py ever looked at a real output. Both gates could be
    # green on an exe that cannot speak.
    voice_ok, voice_detail = _selftest_voice_payload()

    print(
        f"SELFTEST healthz={healthz} root_serves_spa={root_ok} "
        f"voice_payload={voice_ok} status={status}",
        flush=True,
    )
    if not voice_ok:
        print(f"SELFTEST_FAIL missing from the bundle: {voice_detail}", flush=True)
    sys.exit(0 if (healthz and root_ok and voice_ok) else 1)


def _selftest_voice_payload() -> tuple[bool, str]:
    """Is every engine's worker script and requirements file actually here?

    Returns (ok, what is missing). Never raises: a self-test that dies on its
    own import is indistinguishable from the failure it is looking for.
    """
    try:
        from tts import provision
        from tts.host import worker_script

        missing: list[str] = []
        for engine_id in provision.ENGINES:
            script = worker_script(engine_id)
            if not script.is_file():
                missing.append(str(script))
            reqs = provision.requirements_path(engine_id)
            if not reqs.is_file():
                missing.append(str(reqs))
        return (not missing), ", ".join(missing)
    except Exception as exc:                             # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def _stop_voice_worker(grace: float = 1.0) -> None:
    """Take the voice worker down. Registered on three different exits because
    the obvious one does not fire in the packaged app.

    uvicorn runs in a daemon thread here, so when webview.start() returns the
    process exits and FastAPI's shutdown hook never runs. The worker holds
    several GB of VRAM, which makes an orphan the difference between closing an
    app and rebooting a machine. Belt (window closed), braces (atexit), and a
    Win32 job object underneath for the case where neither gets to run.
    """
    try:
        from tts.worker_client import hard_close

        hard_close(grace=grace)
    except Exception:
        logging.getLogger(__name__).warning("voice teardown failed", exc_info=True)


def _try_per_monitor_dpi() -> bool:
    """Ask Windows for PER-MONITOR DPI awareness. Opt-in, and here is why.

    pywebview already calls `SetProcessDPIAware()` - the Vista-era API, which
    means SYSTEM dpi aware: the app is sharp at the scale factor the primary
    monitor had at login, and Windows BITMAP-STRETCHES it anywhere else. Move
    the window to a second monitor on a different scale, or change the display
    scale while it runs, and every glyph goes soft. That is the whole of the
    "WebView2 looks blurry" class of reports.

    `PER_MONITOR_AWARE_V2` fixes it - but pywebview's WinForms host was written
    against the old model ("Bounds are already in logical pixels due to
    SetProcessDPIAware"), so changing the contract underneath it could just as
    easily produce a wrongly-sized window. That is not a trade to make blind on
    somebody else's machine, and it cannot be verified without a screen.

    It shipped opt-in for exactly one launch to answer that question on a real
    screen. It did not happen - window normal, everything sharp - so this is
    now ON by default, with `ELYSIUM_PER_MONITOR_DPI=0` as the way out if a
    display configuration somewhere disagrees.

    Note the machine it was verified on runs at 100% scale, so what that launch
    PROVED is "does not break the window", not "makes text sharper". The
    sharpness claim rests on the API contract and applies to the setups this
    exists for: fractional scaling and mixed-DPI multi-monitor.

    Must run BEFORE pywebview starts: a process's DPI awareness can only be set
    once, and the first caller wins.
    """
    if os.name != "nt" or os.environ.get("ELYSIUM_PER_MONITOR_DPI") == "0":
        return False
    try:
        import ctypes

        # -4 = DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2. Windows 10 1703+;
        # older builds simply do not export it, which is not an error here.
        #
        # The argtype is NOT optional. The parameter is a HANDLE, so on 64-bit
        # a bare Python -4 is marshalled as a 32-bit int and the call fails
        # silently, returning 0 - a switch that reports success by doing
        # nothing at all. Declaring it as a pointer is what makes it real.
        fn = ctypes.windll.user32.SetProcessDpiAwarenessContext
        fn.argtypes = [ctypes.c_void_p]
        fn.restype = ctypes.c_bool
        ok = bool(fn(ctypes.c_void_p(-4)))
    except (AttributeError, OSError):
        return False
    logging.getLogger(__name__).info(
        "per-monitor DPI awareness: %s", "on" if ok else "refused")
    return ok


def clear_session_residue(profile) -> dict[str, object]:
    """Everything a session must not leave behind, cleared before the next one.

    All three targets are the same thing in different shapes: the decrypted
    conversation, sitting in the clear beside a vault that went to the trouble
    of being encrypted.

      * the browser's disk cache - which held whole /api responses, chats,
        character cards and personas, as plain readable JSON;
      * Crashpad - which would dump the renderer's memory, the conversation
        included, and upload it to Microsoft;
      * the audio cache - the conversation in audible form.

    Launch is the edge that matters. The vault lock and the shutdown path
    already cover a graceful exit; a crash, a kill or a power cut reaches
    neither, and nothing else ever comes back for what they left.

    A function rather than four lines inside main() so the guarantee can be
    tested. main() cannot be: it binds a socket and opens a window.

    Returns what each step did. Never raises - failing to start is worse than
    residue, and a caller that wants to know can read the result.
    """
    from tts.host import wipe_audio_cache

    purged = browser_profile.purge(profile)
    # After the purge, so a Crashpad database left by an older build is shred
    # first and the blocker lands on clean ground.
    blocked = browser_profile.block_crash_reporting(profile)
    stale, stuck = wipe_audio_cache()

    log = logging.getLogger(__name__)
    if purged or stale:
        log.info("launch: cleared %d cached file(s) and %d audio file(s) "
                 "from a previous session", purged, stale)
    if not blocked:
        log.warning("launch: crash reporting could NOT be blocked")
    return {"cached_files": purged, "crash_reporting_blocked": blocked,
            "audio_files": stale, "audio_left": stuck}


def main() -> None:
    _setup_frozen_logging()
    # First, and before anything reads or writes the data folder. A second copy
    # that gets past this line would hold its own copy of the vault key against
    # the same folder. (It would also issue its own launch token, which is
    # harmless: the token lives in one process's memory and gates only that
    # process's server.)
    enforce_single_instance()
    # Before the vault key can exist in this process, and before the data
    # directory has anything worth indexing: a crash dump that excludes the
    # heap is only useful if the flag was set before the crash.
    from config import DATA_DIR as _DATA_DIR

    win_hardening.harden(_DATA_DIR)
    # BEFORE the server starts. Issuing it after would leave a window - short,
    # but real - in which the API is live and the gate is unarmed.
    launch_token.issue()
    _try_per_monitor_dpi()
    # Covers the selftest path too, which returns via sys.exit and would
    # otherwise leave a worker behind on every frozen-exe check.
    atexit.register(_stop_voice_worker)
    sock = bind_app_socket()
    port = sock.getsockname()[1]
    serve(sock)
    base = f"http://{HOST}:{port}"
    if os.environ.get("ELYSIUM_SELFTEST"):
        _selftest(base)
        return
    if not _webview2_installed():
        message = (
            "Elysium needs the Microsoft Edge WebView2 Runtime, which was not "
            "found on this PC.\n\nInstall it (free, one time) from:\n"
            + WEBVIEW2_DOWNLOAD
            + "\n\nThen start Elysium again."
        )
        logging.getLogger(__name__).error("WebView2 runtime not found; aborting launch.")
        _alert(message)
        raise SystemExit(1)
    if not wait_until_ready(base + "/healthz"):
        message = (
            "Elysium's local server did not start in time.\n\n"
            "Details were written to elysium.log in the app's data folder\n"
            "(%LOCALAPPDATA%\\Elysium). Please try again."
        )
        logging.getLogger(__name__).error("Backend not ready within timeout; aborting launch.")
        _alert(message)
        raise SystemExit("Elysium backend did not start in time.")
    # The secret only this window gets. In the FRAGMENT, which is never sent
    # to a server and never written to a request log - the page reads it once
    # at boot and keeps it in memory. Without this, any program running as
    # this user could curl the API and read the whole conversation while the
    # app is open, which is exactly when the vault is unlocked.
    token = launch_token.configured()
    window = webview.create_window(
        WINDOW_TITLE,
        f"{base}/#elysium-token={token}",
        width=1200,
        height=820,
        # Floor chosen so the two fixed side panels never squeeze the chat:
        # at 980px wide the sidebar+right panel ease to ~264+318 and the chat
        # keeps ~360px. Below this the composer would get uncomfortably narrow.
        min_size=(980, 660),
        # Without this the window refuses to let anyone select a word.
        # pywebview defaults text_select to False and then injects
        # `body {user-select: none; cursor: default}` into every page after
        # each navigation (webview/js/customize.js). Nothing in this app asked
        # for that, and the WebView2 context menu is tied to the debug flag,
        # which is off - so there was no right-click Copy either, and a
        # conversation could be read but never quoted.
        #
        # Turning it on changes exactly one thing: that stylesheet is not
        # injected. Accelerator keys, the context menu, DevTools and drag
        # behaviour all hang off other flags and are untouched. Textareas were
        # already exempt - the CSS UI spec makes editable elements ignore an
        # inherited `none`, which is why the composer always worked and the
        # messages never did.
        #
        # Ctrl+A followed by Ctrl+C now reaches the whole transcript; WebView2
        # keeps text-editing accelerators enabled regardless of our settings.
        # SECURITY.md says so out loud rather than pretending otherwise.
        text_select=True,
    )
    # Fire-and-forget on a thread: even with grace=0 the teardown still waits
    # briefly for the terminated process to be reaped, and a pywebview event
    # handler that blocks freezes the close. If the process exits before the
    # thread finishes, the kernel closes the job object handle and
    # KILL_ON_JOB_CLOSE reaps the worker anyway - the guarantee does not
    # depend on this thread winning the race.
    window.events.closed += lambda: threading.Thread(
        target=_stop_voice_worker, args=(0.0,), daemon=True
    ).start()
    # Capture exclusion needs an actual HWND, which does not exist until the
    # window is shown. Off unless ELYSIUM_SCREEN_PRIVACY=1, so for everyone
    # else this handler enumerates nothing and returns.
    window.events.shown += win_hardening.apply_screen_privacy
    # The accessibility switch itself was already armed by harden(), before the
    # server started, because WebView2 reads its arguments once and this window
    # did not exist then. What runs here is only the read-back, and it hangs on
    # `loaded` rather than `shown`: the browser process is what carries the
    # argument, and by the time a page has loaded it is certainly running,
    # while at `shown` it may not be yet. An unanswerable question is logged as
    # unanswered, never as a failure.
    window.events.loaded += win_hardening.report_accessibility_privacy
    # Persistent WebView2 profile: pywebview's default private mode wipes
    # localStorage/IndexedDB on every close, which would reset font size,
    # narration style, the wallpaper, and the last-open chat each launch.
    # This profile holds ONLY those cosmetic scalars/ids and the optional
    # wallpaper image (uiStore partialize is allowlisted) - chat content
    # stays in the encrypted DB.
    from config import DATA_DIR

    profile = DATA_DIR / "webview"
    clear_session_residue(profile)

    webview.start(  # blocks until the window closes; then the process exits
        private_mode=False,
        storage_path=str(profile),
    )

    browser_profile.purge(profile)


if __name__ == "__main__":
    main()
