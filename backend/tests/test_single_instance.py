"""One Elysium per data folder, proved by making a second one refuse.

WHY THIS EXISTS. Double clicking the exe a second time used to open a second
window against the SAME data folder, because bind_app_socket falls back to an
ephemeral port when the remembered one is busy. vault_state holds the key in a
module global, so the two windows each had their own copy of it: locking the
vault in window A cleared A's key and left B with the database fully
decryptable. The user performed the one gesture the whole at-rest design rests
on and got half of it.

HOW IT IS TESTED. Nothing here reads run_app's source. Every test makes the
claim and observes what a second claimant is told, and three of them do it from
a real second process, which is the only place the property that matters (a
hard kill releases the claim) can actually be observed.

GROUND AND POSITIVE CONTROL. Each pair is written out: a fresh folder is
claimed (ground) and the same folder is then refused (positive control); a
child alone runs through (ground) and the same child against a held folder
stops (positive control); a title nothing owns is not found (ground) and a real
top level window carrying that title is (positive control). Without the ground
half, a guard that refused everything would look identical to a guard that
works; without the positive half, a guard deleted entirely would look the same.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
import uuid
from ctypes import wintypes
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="the guard is a Win32 kernel object; there is no cross platform half",
)


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    """A data folder nothing else on this machine is using, wired into config
    the way the launcher reads it (lazily, per call), plus an unconditional
    release so one failing test cannot leave the claim held for the next."""
    import config
    import run_app

    folder = tmp_path / "instance-a"
    folder.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", folder)
    yield folder
    run_app.release_single_instance()


# ── The claim itself ─────────────────────────────────────────────────────────


def test_a_free_data_folder_is_claimed(data_dir):
    """GROUND. Nothing holds this folder, so the launcher proceeds. If this
    fails, every other assertion below is meaningless: a guard that refuses
    unconditionally would pass them all."""
    import run_app

    assert run_app.claim_single_instance() is True
    # ...and the launcher's own entry point agrees: it returns rather than
    # exiting, which is what lets main() go on to open a window.
    run_app.release_single_instance()
    assert run_app.enforce_single_instance() is None


def test_a_second_claim_on_the_same_folder_is_refused(data_dir):
    """POSITIVE CONTROL. CreateMutexW reports ERROR_ALREADY_EXISTS to whoever
    asks for a name that is already taken, including a caller in the same
    process, so this is the real refusal path and not a simulation of it."""
    import run_app

    assert run_app.claim_single_instance() is True
    assert run_app.claim_single_instance() is False


def test_a_refused_claim_does_not_steal_the_holder(data_dir):
    """The loser must not take the claim with it. A refused claim opens a
    handle to somebody else's object and has to close it again; if it kept it,
    the name would outlive the real instance and the NEXT launch would be
    refused by a process that is already gone."""
    import run_app

    assert run_app.claim_single_instance() is True
    assert run_app.claim_single_instance() is False
    # The holder is still the holder: releasing once is enough to free it.
    run_app.release_single_instance()
    assert run_app.claim_single_instance() is True


def test_releasing_lets_the_next_launch_in(data_dir):
    """Closing the last handle destroys the named object. This is the graceful
    half of the lifetime; the kill half is measured across processes below."""
    import run_app

    assert run_app.claim_single_instance() is True
    run_app.release_single_instance()
    assert run_app.claim_single_instance() is True


def test_a_different_data_folder_runs_alongside(data_dir, tmp_path, monkeypatch):
    """The guard is keyed to the folder, not to the program. Two vaults are two
    independent things, and this is also what keeps the frozen self check
    (which always redirects ELYSIUM_DATA_DIR) runnable while the app is open."""
    import config
    import run_app

    assert run_app.claim_single_instance() is True

    other = tmp_path / "instance-b"
    other.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", other)
    assert run_app.claim_single_instance() is True, (
        "a second vault was refused because of an unrelated first one"
    )
    run_app.release_single_instance()


def test_the_name_follows_the_folder_not_its_spelling(data_dir, monkeypatch):
    """Windows paths are case insensitive and the data folder arrives spelled
    differently depending on who expanded it. Two spellings of one folder must
    not produce two claims, because that is a second window against one vault
    wearing a disguise."""
    import config
    import run_app

    assert run_app.claim_single_instance() is True
    monkeypatch.setattr(config, "DATA_DIR", Path(str(data_dir).upper()))
    assert run_app.claim_single_instance() is False


# ── What the user is told ────────────────────────────────────────────────────


def test_the_user_is_told_when_the_window_cannot_be_raised(data_dir, monkeypatch):
    """POSITIVE CONTROL for the "not a silent no-op" requirement. If the
    running window cannot be found, the second launch must say something: an
    app that vanishes on a double click is indistinguishable from one that
    crashed."""
    import run_app

    shown: list[str] = []
    monkeypatch.setattr(run_app, "_alert", shown.append)
    monkeypatch.setattr(run_app, "_raise_existing_window", lambda *a, **k: False)

    assert run_app.claim_single_instance() is True
    with pytest.raises(SystemExit) as exit_info:
        run_app.enforce_single_instance()

    assert exit_info.value.code == 0, "a refused second launch is not a failure"
    assert len(shown) == 1
    assert "already running" in shown[0].lower()


def test_no_dialog_when_the_window_was_raised(data_dir, monkeypatch):
    """GROUND for the same behaviour. When the existing window HAS been brought
    forward, the user already has their answer on screen, and an extra modal
    dialog would be noise stacked on top of it."""
    import run_app

    shown: list[str] = []
    monkeypatch.setattr(run_app, "_alert", shown.append)
    monkeypatch.setattr(run_app, "_raise_existing_window", lambda *a, **k: True)

    assert run_app.claim_single_instance() is True
    with pytest.raises(SystemExit) as exit_info:
        run_app.enforce_single_instance()

    assert exit_info.value.code == 0
    assert shown == []


# ── Finding the window that is already open ──────────────────────────────────


@pytest.fixture()
def hidden_window():
    """A real top level window, created by THIS process, carrying a title
    nothing else on the machine could have.

    A real window rather than a stub, because the thing under test is a Win32
    enumeration: a fake would prove only that the fake was called. It is left
    hidden (no WS_VISIBLE) so the run does not flash a window across the
    developer's screen, and because SetForegroundWindow declines to move focus
    to an invisible window, which keeps the raise path harmless to run here.

    The STATIC class is borrowed rather than registered: a window class needs a
    WNDPROC, and this window never receives a message.
    """
    created: list[int] = []
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    create = user32.CreateWindowExW
    create.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                       wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                       ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                       wintypes.HINSTANCE, wintypes.LPVOID]
    create.restype = wintypes.HWND
    destroy = user32.DestroyWindow
    destroy.argtypes = [wintypes.HWND]
    destroy.restype = wintypes.BOOL

    def make(title: str) -> int:
        hwnd = create(0, "STATIC", title, 0, 0, 0, 10, 10,
                      None, None, None, None)
        assert hwnd, f"could not create the control window: {ctypes.get_last_error()}"
        created.append(hwnd)
        return int(hwnd)

    yield make
    for hwnd in created:
        destroy(hwnd)


def test_no_window_of_ours_means_nothing_to_raise(hidden_window):
    """GROUND. The title is random, so this holds no matter what the developer
    happens to have open while the suite runs."""
    import run_app

    title = "Elysium-probe-" + uuid.uuid4().hex
    assert run_app._find_app_window(title) == 0
    assert run_app._raise_existing_window(title) is False


def test_a_window_of_ours_is_found_and_raised(hidden_window):
    """POSITIVE CONTROL. The same title, now carried by a real top level window
    owned by a process running our own image, is found and brought forward.

    This is the half that caught a genuine bug: the first version compared the
    owning process against sys.executable, which inside a virtualenv is the
    shim in .venv rather than the interpreter the kernel reports, so the match
    never fired in dev and every second launch fell through to the dialog.
    """
    import run_app

    title = "Elysium-probe-" + uuid.uuid4().hex
    assert run_app._find_app_window(title) == 0  # before
    hwnd = hidden_window(title)
    assert run_app._find_app_window(title) == hwnd
    assert run_app._raise_existing_window(title) is True


def test_a_window_with_our_title_from_a_stranger_is_ignored(hidden_window, monkeypatch):
    """A title is not an identity. The packaged app's data folder is itself
    named Elysium, so an Explorer window sitting in it is titled exactly
    "Elysium"; raising that one and going quiet is the failure the dialog
    exists to prevent.

    Only one side is faked, and deliberately the side that is not under test:
    the window keeps its real owner (this interpreter) and its image is still
    read for real, while WE claim to be a different program. That is the same
    comparison a real stranger window produces, without needing a second
    program that happens to title a window like ours.
    """
    import run_app

    title = "Elysium-probe-" + uuid.uuid4().hex
    hidden_window(title)
    assert run_app._find_app_window(title) != 0

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(run_app, "_own_image", lambda: "c:\\windows\\explorer.exe")
        assert run_app._find_app_window(title) == 0
        assert run_app._raise_existing_window(title) is False
    finally:
        monkeypatch.undo()


# ── A real second process ────────────────────────────────────────────────────
#
# The in-process tests above cannot see the property this design was chosen
# for: that the claim dies with the process even when the process is killed
# rather than closed. That needs a second interpreter and a TerminateProcess,
# so these three spawn one. ELYSIUM_DATA_DIR points the child at the same
# throwaway folder the parent monkeypatched, which is how the two agree on the
# name without sharing memory.

_CHILD = r"""
import sys
sys.path.insert(0, sys.argv[1])
import run_app

mode, marker, alert_file = sys.argv[2], sys.argv[3], sys.argv[4]

if mode == "hold":
    ok = run_app.claim_single_instance()
    open(marker, "w", encoding="utf-8").write("CLAIMED" if ok else "REFUSED")
    import time
    time.sleep(300)
else:
    # The real _alert is a modal MessageBox, which in a child nobody can click
    # would hang the test forever. Recorded to a file instead, so the assertion
    # can still be "the user was told something".
    run_app._alert = lambda text: open(alert_file, "w", encoding="utf-8").write(text)
    # Forced, because whether a real Elysium window happens to be open on this
    # machine is not something the test may depend on. The raise path has its
    # own tests against a real window above.
    run_app._raise_existing_window = lambda *a, **k: False
    run_app.enforce_single_instance()
    open(marker, "w", encoding="utf-8").write("KEPT GOING")
"""


def _child_argv(mode: str, tmp_path: Path) -> tuple[list[str], Path, Path]:
    marker = tmp_path / f"{mode}-marker.txt"
    alert = tmp_path / f"{mode}-alert.txt"
    argv = [sys.executable, "-c", _CHILD, str(BACKEND), mode,
            str(marker), str(alert)]
    return argv, marker, alert


def _child_env(folder: Path) -> dict[str, str]:
    return dict(os.environ, ELYSIUM_DATA_DIR=str(folder))


def _wait_for(path: Path, timeout: float = 90.0) -> str:
    """Poll for a file instead of reading a pipe: a child that dies during
    import closes nothing a blocking readline would notice, and a hung test is
    a worse outcome than a failed one."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            time.sleep(0.1)
            continue
        if text:
            return text
    return ""


def test_a_first_instance_in_a_child_process_runs_through(data_dir, tmp_path):
    """GROUND for the cross process pair. The parent holds nothing, so the
    child gets all the way past the guard and says so. Without this, the
    refusal below could be a child that simply failed to start."""
    argv, marker, alert = _child_argv("enforce", tmp_path)
    done = subprocess.run(argv, env=_child_env(data_dir), capture_output=True,
                          text=True, timeout=180)

    assert done.returncode == 0, done.stderr
    assert marker.read_text(encoding="utf-8") == "KEPT GOING"
    assert not alert.exists(), "a first instance was told it was a second one"


def test_a_second_instance_exits_zero_and_says_so(data_dir, tmp_path):
    """POSITIVE CONTROL, and the whole feature in one assertion: with the
    folder already claimed, the second process stops before the vault, exits
    clean (no traceback, code 0), and does not do it in silence."""
    import run_app

    assert run_app.claim_single_instance() is True

    argv, marker, alert = _child_argv("enforce", tmp_path)
    done = subprocess.run(argv, env=_child_env(data_dir), capture_output=True,
                          text=True, timeout=180)

    assert not marker.exists(), "the second instance ran on past the guard"
    assert done.returncode == 0, f"a refused launch reported failure: {done.stderr}"
    assert "Traceback" not in done.stderr, done.stderr
    assert "already running" in alert.read_text(encoding="utf-8").lower()


def test_a_hard_killed_instance_leaves_nothing_behind(data_dir, tmp_path):
    """The reason this is a kernel object and not a lockfile.

    A child claims the folder, the parent confirms it is locked out, and the
    child is then TERMINATED rather than asked to exit, so no cleanup code of
    ours ever runs. The claim still has to be gone, because Windows closes the
    handle when the process dies. A lockfile would still be sitting there with
    a dead pid inside it, and the app would refuse to reopen after a crash.
    """
    import run_app

    argv, marker, _alert = _child_argv("hold", tmp_path)
    child = subprocess.Popen(argv, env=_child_env(data_dir),
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        assert _wait_for(marker) == "CLAIMED", "the child never took the claim"
        assert run_app.claim_single_instance() is False, (
            "the parent was let in while another process held the folder"
        )
    finally:
        child.kill()
        child.wait(timeout=30)

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if run_app.claim_single_instance():
            return
        time.sleep(0.1)
    pytest.fail("the claim outlived the process that was holding it")
