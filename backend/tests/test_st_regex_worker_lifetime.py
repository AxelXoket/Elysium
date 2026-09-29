"""The history cleanup worker never outlives the app.

A pattern that backtracks without end keeps the child busy, so it never
reads the closed pipe. If the app dies right then (a crash, a kill from the
Task Manager), the child must still go, or it holds a CPU core until the
machine restarts. Each test starts a real parent process, has it hand the
worker a runaway pattern, kills the parent hard, and watches the child.
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import textwrap
import time
from ctypes import wintypes
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32",
                                reason="the job object is Windows only")

BACKEND = Path(__file__).resolve().parent.parent

_PARENT = textwrap.dedent("""
    import sys, time
    sys.path.insert(0, {backend!r})
    from prompt_modules import st_regex

    if __name__ == "__main__":
        if {untied}:
            st_regex._kill_with_parent = lambda pid: None
        w = st_regex._Worker()
        assert w._start() is not None
        text = "a" * {length} + "c"
        w._conn.send((r"(a|aa)*b", 0, False, False, "", [], text))
        print(w._proc.pid, flush=True)
        time.sleep(600)
""")

_SYNCHRONIZE = 0x00100000
_QUERY = 0x1000
_WAIT_TIMEOUT = 0x102

_k32 = ctypes.WinDLL("kernel32", use_last_error=True) if sys.platform == "win32" else None
if _k32 is not None:
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]


def _run(tmp_path: Path, *, untied: bool, length: int = 60):
    """Start the parent, return (parent Popen, child pid, child handle)."""
    script = tmp_path / "parent.py"
    script.write_text(_PARENT.format(backend=str(BACKEND), untied=untied,
                                     length=length), encoding="utf-8")
    parent = subprocess.Popen([sys.executable, str(script)], cwd=tmp_path,
                              stdout=subprocess.PIPE, text=True,
                              creationflags=subprocess.CREATE_NO_WINDOW)
    line = parent.stdout.readline()
    pid = int(line.strip())
    handle = _k32.OpenProcess(_SYNCHRONIZE | _QUERY | 0x0001, False, pid)
    assert handle, "the child must be running while the parent is"
    return parent, pid, handle


def _exited_within(handle, seconds: float) -> bool:
    return _k32.WaitForSingleObject(handle, int(seconds * 1000)) != _WAIT_TIMEOUT


def _cleanup(parent, handle) -> None:
    parent.kill()
    parent.wait(timeout=10)
    if handle:
        _k32.TerminateProcess(handle, 1)
        _k32.CloseHandle(handle)


def test_the_worker_dies_with_a_killed_parent_mid_pattern(tmp_path):
    parent, _pid, handle = _run(tmp_path, untied=False)
    try:
        # Ground: while the parent lives, the child is busy and alive.
        assert not _exited_within(handle, 1.0)
        parent.kill()
        parent.wait(timeout=10)
        assert _exited_within(handle, 10.0), "the child outlived the app"
    finally:
        _cleanup(parent, handle)


def test_without_the_tie_the_child_is_left_running(tmp_path):
    """Positive control: the same run with the tie removed leaves the child
    spinning, so the test above can tell the two apart."""
    parent, _pid, handle = _run(tmp_path, untied=True)
    try:
        parent.kill()
        parent.wait(timeout=10)
        assert not _exited_within(handle, 3.0)
    finally:
        _cleanup(parent, handle)


def test_a_normal_stop_still_ends_the_child(tmp_path):
    from prompt_modules import st_regex

    w = st_regex._Worker()
    assert w._start() is not None
    pid = w._proc.pid
    handle = _k32.OpenProcess(_SYNCHRONIZE, False, pid)
    try:
        assert w._job is not None, "on Windows the child is tied to the app"
        w.stop()
        assert w._job is None
        assert _exited_within(handle, 5.0)
    finally:
        if handle:
            _k32.CloseHandle(handle)
