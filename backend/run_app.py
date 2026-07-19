"""run_app.py - Elysium desktop launcher.

Serves the app (API + the built frontend) on a loopback port from THIS process
and shows it in a native window. Closing the window returns from
webview.start(), the process exits, and the vault key - held only in RAM - is
gone. So the vault locks on close, exactly as intended; reopening the app shows
the lock screen and asks for the passphrase.
"""
from __future__ import annotations

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


def bind_app_socket() -> socket.socket:
    """Bind (not listen) the server socket here and hand it to uvicorn:
    no close-then-rebind gap, so the port cannot be lost to another process
    between picking it and serving on it. uvicorn's loop.create_server()
    takes over the bound socket and calls listen() itself."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((HOST, 0))
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
    print(f"SELFTEST healthz={healthz} root_serves_spa={root_ok} status={status}", flush=True)
    sys.exit(0 if (healthz and root_ok) else 1)


def main() -> None:
    _setup_frozen_logging()
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
    webview.create_window(
        WINDOW_TITLE,
        base + "/",
        width=1200,
        height=820,
        # Floor chosen so the two fixed side panels never squeeze the chat:
        # at 980px wide the sidebar+right panel ease to ~264+318 and the chat
        # keeps ~360px. Below this the composer would get uncomfortably narrow.
        min_size=(980, 660),
    )
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
