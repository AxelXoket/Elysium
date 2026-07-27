"""run_app.py - Elysium desktop launcher.

Serves the app (API + the built frontend) on a loopback port from THIS process
and shows it in a native window. Closing the window returns from
webview.start(), the process exits, and the vault key - held only in RAM - is
gone. So the vault locks on close, exactly as intended; reopening the app shows
the lock screen and asks for the passphrase.
"""
from __future__ import annotations

import atexit
import ctypes
import logging
import os
import socket
import sys
import threading
import time
import urllib.request
import winreg

import uvicorn
import webview

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
        status = _LOCAL_OPENER.open(base + "/api/v1/vault/status", timeout=3).read().decode()
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


def main() -> None:
    _setup_frozen_logging()
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
    window = webview.create_window(
        WINDOW_TITLE,
        base + "/",
        width=1200,
        height=820,
        # Floor chosen so the two fixed side panels never squeeze the chat:
        # at 980px wide the sidebar+right panel ease to ~264+318 and the chat
        # keeps ~360px. Below this the composer would get uncomfortably narrow.
        min_size=(980, 660),
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
    # Persistent WebView2 profile: pywebview's default private mode wipes
    # localStorage/IndexedDB on every close, which would reset font size,
    # narration style, the wallpaper, and the last-open chat each launch.
    # This profile holds ONLY those cosmetic scalars/ids and the optional
    # wallpaper image (uiStore partialize is allowlisted) - chat content
    # stays in the encrypted DB.
    from config import DATA_DIR

    webview.start(  # blocks until the window closes; then the process exits
        private_mode=False,
        storage_path=str(DATA_DIR / "webview"),
    )


if __name__ == "__main__":
    main()
