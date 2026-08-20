"""Proof that the accessibility tree is closed, run by hand, on purpose.

WHY THIS IS NOT A TEST IN THE SUITE

The only honest proof of "an unprivileged process cannot read the conversation
out of this window" is to make a real window on a real desktop and attack it
from a real second process. That opens a window, takes focus and takes about a
minute. A test that does that on every `pytest` run is a test people learn to
skip, and this app's whole problem with gates has been gates that stopped
telling the truth. So it lives here, is not named test_*, is never collected,
and is run when somebody wants the answer:

    backend\\.venv\\Scripts\\python.exe backend\\tests\\accessibility_tree_harness.py

It prints a four-cell table and exits non-zero unless every cell is what it
should be. Nothing else in the suite makes this claim; tests/
test_accessibility_privacy.py covers the machinery around it - the default, the
arming, the read-back - none of which is the same as the tree being shut.

THE GROUND IS THE POINT

Two arms, and the first one has to FAIL to be worth anything. With the switch
OFF the probes must recover the marker strings; if they do not, this harness is
broken (or UI Automation is not working on this machine) and it says
HARNESS BROKEN rather than reporting the app as safe. That failure mode -
a probe that finds nothing because it is not looking properly, read as
"nothing to find" - is exactly what a privacy gate must never do.

TWO CHANNELS, IN THIS ORDER

UI Automation and MSAA/IAccessible are not two trees. Chromium builds ONE
accessibility tree per frame in the renderer, caches it in the browser process,
and serves IAccessible, IAccessible2 and UI Automation from that single cache.
It also builds it ON DEMAND: cold, before any assistive client has asked,
even an unprotected window gives an MSAA caller nothing but the document title.
The tree is escalated the moment any client asks for it - a UIA walk, or a
screen reader starting.

So MSAA is probed AFTER the UIA walk has escalated the tree, never before.
A cold MSAA probe would come back empty against a completely unprotected
window and would be read as a pass.

WHAT EACH ARM ACTUALLY EXERCISES

The target window is built the way backend/run_app.py builds the real one, and
it arms the switch by calling win_hardening.apply_accessibility_privacy() -
the shipped function, not a flag typed in here. If that function stops working,
this harness goes red.
"""
from __future__ import annotations

import http.server
import json
import os
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
sys.path.insert(0, str(BACKEND))

#: Strings that stand in for a chat title, a character name and two message
#: bodies. Distinctive enough that finding one anywhere is unambiguous.
MARKERS = ["ZQTITLE4471", "ZQCHARNAME4471", "ZQMSGBODY4471", "ZQUSERMSG4471"]

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Elysium</title></head>
<body style="background:#12101a;color:#e8e4f0;font:15px system-ui">
<h1 id="chat-title">ZQTITLE4471</h1>
<div id="char-name" aria-label="Character ZQCHARNAME4471">ZQCHARNAME4471</div>
<div class="msg" role="article">ZQMSGBODY4471 the marker sentence that
stands in for a private line.</div>
<div class="msg" role="article">ZQUSERMSG4471 what the owner typed.</div>
<textarea id="composer" aria-label="Write a message"></textarea>
</body></html>
"""

#: Outside the repository, deliberately. A WebView2 profile and a pid file
#: dropped into backend/tests/ would make the working tree dirty, and
#: test_tree_hygiene.py goes red on a dirty tree - so running this would break
#: the suite it is meant to support.
_SCRATCH = Path(tempfile.gettempdir()) / "elysium-accessibility-harness"
_PROFILE = _SCRATCH / "webview"
_PID_FILE = _SCRATCH / "target.pid"

#: How many times to walk before giving up on the tree appearing. Chromium
#: escalates lazily: measured on 20 August 2026, an unprotected window gives
#: the document title on the first walk and the whole DOM from the second.
_UIA_PASSES = 5
_PASS_PAUSE = 2.0


# ── the target: one window, built the way run_app builds it ──────────────────

def run_target() -> None:
    import webview

    import win_hardening

    # The shipped arming function, before the window exists, exactly as
    # harden() calls it on the launch path.
    win_hardening.apply_accessibility_privacy()

    body = PAGE.encode("utf-8")

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):                                    # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    server = socketserver.TCPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    _PROFILE.mkdir(parents=True, exist_ok=True)
    _PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    webview.create_window(
        "Elysium", f"http://127.0.0.1:{port}/",
        width=1200, height=820, min_size=(980, 660), text_select=True)
    webview.start(private_mode=False, storage_path=str(_PROFILE))


# ── probe one: UI Automation, through .NET's client ──────────────────────────
#
# pythonnet ships with pywebview, so this needs nothing installed. The
# assemblies are loaded by strong name because a plain AddReference("...")
# does not find them in the GAC.

def probe_uia(pid: int) -> list[str]:
    import clr

    strong = (", Version=4.0.0.0, Culture=neutral, "
              "PublicKeyToken=31bf3856ad364e35")
    for assembly in ("WindowsBase", "UIAutomationTypes", "UIAutomationClient"):
        clr.AddReference(assembly + strong)
    from System.Windows.Automation import AutomationElement, TreeWalker

    walker = TreeWalker.ControlViewWalker
    seen: list[str] = []

    def visit(element, depth: int = 0) -> None:
        if element is None or depth > 60 or len(seen) > 6000:
            return
        try:
            name = element.Current.Name
        except Exception:
            name = ""
        if name:
            seen.append(name)
        try:
            child = walker.GetFirstChild(element)
        except Exception:
            return
        while child is not None:
            visit(child, depth + 1)
            try:
                child = walker.GetNextSibling(child)
            except Exception:
                return

    top = walker.GetFirstChild(AutomationElement.RootElement)
    while top is not None:
        try:
            mine = int(top.Current.ProcessId) == pid
        except Exception:
            mine = False
        if mine:
            visit(top)
        try:
            top = walker.GetNextSibling(top)
        except Exception:
            break
    return _hits(seen)


# ── probe two: MSAA / IAccessible only, no UIA in this process ───────────────
#
# comtypes is NOT a dependency of this app and is not installed by it. Without
# it this half reports UNAVAILABLE, which the driver treats as "not proven",
# never as a pass. To run it:
#
#     backend\\.venv\\Scripts\\python.exe -m pip install --target
#         %TEMP%\\elysium-probe-libs comtypes
#     set PYTHONPATH=%TEMP%\\elysium-probe-libs
#
# Installing into a throwaway directory rather than the app's venv on purpose:
# the shipped exe's dependency set is audited, and a probe is not a dependency.

def probe_msaa(pid: int) -> list[str] | None:
    try:
        import comtypes
        import comtypes.automation as automation
        import comtypes.client
    except ImportError:
        return None

    import ctypes
    from ctypes import POINTER, byref, wintypes

    comtypes.client.GetModule("oleacc.dll")
    from comtypes.gen import Accessibility

    comtypes.CoInitialize()
    user32 = ctypes.windll.user32
    oleacc = ctypes.oledll.oleacc
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    windows: list[int] = []

    def add_children(parent: int) -> None:
        def visit(handle, _lparam):
            if handle not in windows:
                windows.append(handle)
                add_children(handle)
            return True
        user32.EnumChildWindows(parent, callback(visit), 0)

    def visit_top(handle, _lparam):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(handle, byref(owner))
        if owner.value == pid:
            if handle not in windows:
                windows.append(handle)
            add_children(handle)
        return True

    user32.EnumWindows(callback(visit_top), 0)

    seen: list[str] = []

    def texts(accessible, child: int = 0) -> None:
        for prop in ("accName", "accValue", "accDescription"):
            try:
                value = getattr(accessible, prop)(child)
            except Exception:
                value = None
            if value:
                seen.append(str(value))

    def walk(accessible, depth: int = 0) -> None:
        if depth > 45 or len(seen) > 8000:
            return
        texts(accessible)
        try:
            count = accessible.accChildCount
        except Exception:
            return
        if not count or count > 4000:
            return
        children = (automation.VARIANT * count)()
        got = ctypes.c_long()
        try:
            oleacc.AccessibleChildren(accessible, 0, count, children, byref(got))
        except Exception:
            return
        for index in range(got.value):
            item = children[index]
            try:
                if item.vt == automation.VT_DISPATCH and item.value is not None:
                    walk(item.value.QueryInterface(Accessibility.IAccessible),
                         depth + 1)
                elif item.vt == automation.VT_I4:
                    texts(accessible, item.value)
            except Exception:
                continue

    for handle in windows:
        for objid in (0xFFFFFFFC, 0x00000000):     # OBJID_CLIENT, OBJID_WINDOW
            pointer = POINTER(Accessibility.IAccessible)()
            try:
                oleacc.AccessibleObjectFromWindow(
                    wintypes.HWND(handle), wintypes.DWORD(objid),
                    byref(Accessibility.IAccessible._iid_), byref(pointer))
            except Exception:
                continue
            try:
                walk(pointer)
            except Exception:
                continue
    return _hits(seen)


def _hits(strings: list[str]) -> list[str]:
    return sorted({marker for marker in MARKERS
                   for text in strings if marker in text})


# ── the driver ───────────────────────────────────────────────────────────────

def _run_arm(switch_off: bool) -> dict:
    environment = dict(os.environ)
    import config

    if switch_off:
        environment[config.ACCESSIBILITY_PRIVACY_ENV] = \
            config.ACCESSIBILITY_PRIVACY_OFF
    else:
        environment.pop(config.ACCESSIBILITY_PRIVACY_ENV, None)

    _PID_FILE.unlink(missing_ok=True)
    target = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "target"],
        env=environment)
    try:
        deadline = time.time() + 60
        while time.time() < deadline and not _PID_FILE.exists():
            time.sleep(0.5)
        if not _PID_FILE.exists():
            return {"error": "the target window never started"}
        pid = int(_PID_FILE.read_text(encoding="utf-8").strip())
        time.sleep(6)

        uia: list[str] = []
        for _ in range(_UIA_PASSES):
            uia = _in_child("probe-uia", pid) or []
            if uia:
                break
            time.sleep(_PASS_PAUSE)
        # AFTER the UIA walk, never before: cold, Chromium has not built the
        # tree yet and MSAA would come back empty against anything.
        msaa = _in_child("probe-msaa", pid)
        return {"uia": uia, "msaa": msaa}
    finally:
        target.kill()
        target.wait(timeout=30)
        _PID_FILE.unlink(missing_ok=True)


def _in_child(role: str, pid: int) -> list[str] | None:
    """Each probe in its own process: a UIA client in the same process as the
    MSAA probe would escalate the tree for it and hide a real difference."""
    done = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), role, str(pid)],
        capture_output=True, text=True, timeout=300)
    for line in reversed(done.stdout.splitlines()):
        if line.startswith("RESULT="):
            return json.loads(line[len("RESULT="):])
    return None


def main() -> int:
    print("Two windows will open and close. This takes about a minute.\n")
    ground = _run_arm(switch_off=True)
    armed = _run_arm(switch_off=False)
    if "error" in ground or "error" in armed:
        print("HARNESS BROKEN:", ground.get("error") or armed.get("error"))
        return 2

    rows = [
        ("switch OFF  UIA ", ground["uia"], True),
        ("switch OFF  MSAA", ground["msaa"], True),
        ("switch ON   UIA ", armed["uia"], False),
        ("switch ON   MSAA", armed["msaa"], False),
    ]
    print(f"{'arm':18} {'markers recovered':40} verdict")
    broken = failed = False
    for label, hits, want_hits in rows:
        if hits is None:
            verdict = "UNAVAILABLE (comtypes not installed)"
            broken = broken or want_hits
        elif want_hits:
            verdict = "ground ok" if hits else "NO GROUND"
            broken = broken or not hits
        else:
            verdict = "closed" if not hits else "STILL READABLE"
            failed = failed or bool(hits)
        print(f"{label:18} {str(hits):40} {verdict}")

    if broken:
        print("\nHARNESS BROKEN: the switch was OFF and the probes still found "
              "nothing. Believe nothing above; this machine's accessibility "
              "clients are not working, or this file is.")
        return 2
    if failed:
        print("\nFAILED: the conversation is readable with the switch ON.")
        return 1
    print("\nPASS: readable with the switch off, closed with it on, on both "
          "channels.")
    return 0


if __name__ == "__main__":
    role = sys.argv[1] if len(sys.argv) > 1 else "drive"
    if role == "target":
        run_target()
    elif role == "probe-uia":
        print("RESULT=" + json.dumps(probe_uia(int(sys.argv[2]))))
    elif role == "probe-msaa":
        print("RESULT=" + json.dumps(probe_msaa(int(sys.argv[2]))))
    else:
        raise SystemExit(main())
