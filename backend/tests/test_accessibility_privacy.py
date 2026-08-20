"""The accessibility tree, which is the conversation as TEXT.

An audit built an unprivileged probe - no token, no HTTP, no elevation, the
same user - and read Elysium's whole transcript out of the WebView2 window
through UI Automation: chat title, character name, message bodies, verbatim.
It re-ran the probe with SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)
confirmed set and got the same strings, because that flag excludes pixels and
this is not pixels.

WHAT IS TESTED HERE, AND WHAT IS NOT

The claim "the tree is closed" can only be proved by walking the tree of a
running window from another process, and that needs a real window on a real
desktop. That proof is in tests/accessibility_tree_harness.py, which the owner
runs deliberately; it is not in this suite, because a test that opens a window
every time somebody types pytest is a test people stop running.

What IS here is everything around that claim which can be driven without a
window, and it is not nothing: the default, the refusal, the arming, and - the
part that would otherwise be pure faith - the read-back that asks the WebView2
browser process what arguments it actually received. Each of those has a
ground and a positive control, because the failure this project keeps finding
in its own gates is the test that passes by not working.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

import config
import win_hardening

WINDOWS_ONLY = pytest.mark.skipif(os.name != "nt", reason="Win32 API surface")

#: Long enough for the test to look at it, short enough to be gone by itself
#: if the test dies before its finally block.
_CHILD_LIFETIME = "20"


def _sleeping_child(marker: str) -> subprocess.Popen:
    """A real child process carrying a marker on its command line.

    No console window: CREATE_NO_WINDOW. A test that flashes a window on the
    owner's desktop is a test that gets deleted.
    """
    return subprocess.Popen(
        [sys.executable, "-c", f"import time; time.sleep({_CHILD_LIFETIME})",
         marker],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


class TestTheDefaultIsOn:
    """The owner chose the stronger stance knowingly, so the code has to
    default to it even when nobody has ever heard of the variable."""

    def test_nobody_asked_and_it_is_on(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(config.ACCESSIBILITY_PRIVACY_ENV, raising=False)
        assert win_hardening.accessibility_privacy_requested() is True

    def test_exactly_zero_turns_it_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The documented way out, and it has to work without editing code:
        # somebody who needs a screen reader sets this and restarts.
        monkeypatch.setenv(config.ACCESSIBILITY_PRIVACY_ENV,
                           config.ACCESSIBILITY_PRIVACY_OFF)
        assert win_hardening.accessibility_privacy_requested() is False

    @pytest.mark.parametrize(
        "value", ["", "1", "00", " 0", "0 ", "false", "no", "off", "yes"])
    def test_anything_else_leaves_it_on(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        # A privacy control a typo can disable reports its own state wrongly.
        # "false" reads like off to a person and is not, which is exactly why
        # the effective state is logged at launch.
        monkeypatch.setenv(config.ACCESSIBILITY_PRIVACY_ENV, value)
        assert win_hardening.accessibility_privacy_requested() is True, value


class TestArmingTheSwitch:
    """WebView2 reads its arguments once, when the environment is created, so
    the only place this can be done is before the window exists."""

    def test_it_puts_the_argument_where_webview2_will_find_it(self) -> None:
        env: dict[str, str] = {}
        assert win_hardening.apply_accessibility_privacy(env) is True
        assert env[win_hardening._WEBVIEW2_ARGUMENTS_ENV].split() == [
            win_hardening._RENDERER_ACCESSIBILITY_OFF]

    def test_the_argument_is_one_token(self) -> None:
        # Not spelling - shape. A stray space inside the constant would arm
        # two switches Chromium has never heard of, and Chromium ignores a
        # switch it does not recognise in silence, so the protection would
        # vanish with nothing anywhere saying it had.
        flag = win_hardening._RENDERER_ACCESSIBILITY_OFF
        assert flag.split() == [flag]
        assert flag.startswith("--")

    def test_it_does_not_clobber_arguments_somebody_else_set(self) -> None:
        # pywebview puts its own flags on this WebView2 through the same
        # property, and a machine we have never seen may add more. Taking
        # theirs away to add ours would break the app to protect it.
        env = {win_hardening._WEBVIEW2_ARGUMENTS_ENV: "--already-here"}
        assert win_hardening.apply_accessibility_privacy(env) is True
        assert env[win_hardening._WEBVIEW2_ARGUMENTS_ENV].split() == [
            "--already-here", win_hardening._RENDERER_ACCESSIBILITY_OFF]

    def test_arming_twice_arms_it_once(self) -> None:
        env: dict[str, str] = {}
        win_hardening.apply_accessibility_privacy(env)
        win_hardening.apply_accessibility_privacy(env)
        assert env[win_hardening._WEBVIEW2_ARGUMENTS_ENV].split().count(
            win_hardening._RENDERER_ACCESSIBILITY_OFF) == 1

    def test_an_environment_that_refuses_the_write_is_not_believed(
        self
    ) -> None:
        # The house rule: read the setting back rather than trust the call.
        # Without the read-back this returns True for an environment that
        # dropped the assignment on the floor, and the app would report a
        # protection it does not have.
        class Deaf(dict):
            def __setitem__(self, key, value):    # accepts, keeps nothing
                pass

        env = Deaf()
        assert win_hardening.apply_accessibility_privacy(env) is False

    def test_refusing_it_touches_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(config.ACCESSIBILITY_PRIVACY_ENV,
                           config.ACCESSIBILITY_PRIVACY_OFF)
        env = {win_hardening._WEBVIEW2_ARGUMENTS_ENV: "--already-here"}
        assert win_hardening.apply_accessibility_privacy(env) is False
        assert env == {win_hardening._WEBVIEW2_ARGUMENTS_ENV: "--already-here"}

    def test_it_defaults_to_the_real_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The launch path calls this with no argument, and the process
        # environment is the thing the WebView2 loader will actually read.
        monkeypatch.delenv(config.ACCESSIBILITY_PRIVACY_ENV, raising=False)
        monkeypatch.delenv(win_hardening._WEBVIEW2_ARGUMENTS_ENV, raising=False)
        assert win_hardening.apply_accessibility_privacy() is True
        assert (win_hardening._RENDERER_ACCESSIBILITY_OFF
                in os.environ[win_hardening._WEBVIEW2_ARGUMENTS_ENV].split())


@WINDOWS_ONLY
class TestTheLaunchPathArmsIt:
    def test_harden_reports_it(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # harden() is what run_app actually calls, and it runs before the
        # server starts - which is the only moment early enough to matter.
        monkeypatch.delenv(config.ACCESSIBILITY_PRIVACY_ENV, raising=False)
        monkeypatch.delenv(win_hardening._WEBVIEW2_ARGUMENTS_ENV, raising=False)
        assert win_hardening.harden(tmp_path)["accessibility_tree_closed"] is True

    def test_harden_respects_the_refusal(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(config.ACCESSIBILITY_PRIVACY_ENV,
                           config.ACCESSIBILITY_PRIVACY_OFF)
        monkeypatch.delenv(win_hardening._WEBVIEW2_ARGUMENTS_ENV, raising=False)
        assert win_hardening.harden(tmp_path)["accessibility_tree_closed"] is False
        assert win_hardening._WEBVIEW2_ARGUMENTS_ENV not in os.environ


@WINDOWS_ONLY
class TestItIsArmedBeforeTheWindowExists:
    """The one ordering that cannot be got wrong.

    WebView2 reads its arguments when the environment is created and never
    again, so arming after create_window would be a switch that is on in every
    log line and off in the only place it matters. This runs the real main()
    with its side effects stubbed - the pattern test_release_hardening.py uses
    for the DPI ordering - and looks at what the environment held at the
    moment the window was asked for.
    """

    def test_the_argument_is_in_place_when_the_window_is_created(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import types
        from unittest.mock import MagicMock

        import run_app

        monkeypatch.delenv(config.ACCESSIBILITY_PRIVACY_ENV, raising=False)
        monkeypatch.delenv(win_hardening._WEBVIEW2_ARGUMENTS_ENV, raising=False)
        # harden() is NOT stubbed here: it is the caller under test. It is
        # pointed at a temp folder so the real one it hardens is not the
        # owner's data directory.
        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        monkeypatch.delenv("ELYSIUM_SELFTEST", raising=False)
        monkeypatch.setattr(run_app, "_setup_frozen_logging", lambda: None)
        monkeypatch.setattr(run_app, "enforce_single_instance", lambda: None)
        monkeypatch.setattr(run_app.launch_token, "issue", lambda: None)
        monkeypatch.setattr(run_app.launch_token, "configured", lambda: "tok")
        monkeypatch.setattr(run_app, "_stop_voice_worker", lambda *a, **k: None)
        monkeypatch.setattr(
            run_app, "bind_app_socket",
            lambda: types.SimpleNamespace(
                getsockname=lambda: ("127.0.0.1", 55123)))
        monkeypatch.setattr(run_app, "serve", lambda sock: None)
        monkeypatch.setattr(run_app, "_webview2_installed", lambda: True)
        monkeypatch.setattr(
            run_app, "wait_until_ready", lambda url, timeout=30.0: True)
        monkeypatch.setattr(run_app, "clear_session_residue", lambda profile: {})
        monkeypatch.setattr(run_app, "_try_per_monitor_dpi", lambda: True)
        monkeypatch.setattr(run_app.webview, "start", lambda *a, **k: None)
        monkeypatch.setattr(run_app.browser_profile, "purge", lambda profile: 0)

        seen: list[str | None] = []

        def fake_create_window(*a, **k):
            seen.append(os.environ.get(win_hardening._WEBVIEW2_ARGUMENTS_ENV))
            return MagicMock()

        monkeypatch.setattr(run_app.webview, "create_window",
                            fake_create_window)

        run_app.main()

        # GROUND: an empty list here means create_window was never reached,
        # and the IndexError fails this as loudly as a missing argument would.
        assert win_hardening._RENDERER_ACCESSIBILITY_OFF in (
            seen[0] or "").split()

    def test_the_read_back_is_wired_to_the_window(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A verdict nobody asks for is a verdict nobody gets.

        `loaded` rather than `shown`: the browser process carries the argument
        and may not exist yet when the window first appears.
        """
        import types

        import run_app

        class Recorder:
            def __init__(self) -> None:
                self.handlers: list = []

            def __iadd__(self, handler):
                self.handlers.append(handler)
                return self

        events = types.SimpleNamespace(
            loaded=Recorder(), shown=Recorder(), closed=Recorder())

        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        monkeypatch.delenv("ELYSIUM_SELFTEST", raising=False)
        monkeypatch.setattr(run_app, "_setup_frozen_logging", lambda: None)
        monkeypatch.setattr(run_app, "enforce_single_instance", lambda: None)
        monkeypatch.setattr(run_app.win_hardening, "harden", lambda *a, **k: None)
        monkeypatch.setattr(run_app.launch_token, "issue", lambda: None)
        monkeypatch.setattr(run_app.launch_token, "configured", lambda: "tok")
        monkeypatch.setattr(run_app, "_stop_voice_worker", lambda *a, **k: None)
        monkeypatch.setattr(
            run_app, "bind_app_socket",
            lambda: types.SimpleNamespace(
                getsockname=lambda: ("127.0.0.1", 55123)))
        monkeypatch.setattr(run_app, "serve", lambda sock: None)
        monkeypatch.setattr(run_app, "_webview2_installed", lambda: True)
        monkeypatch.setattr(
            run_app, "wait_until_ready", lambda url, timeout=30.0: True)
        monkeypatch.setattr(run_app, "clear_session_residue", lambda profile: {})
        monkeypatch.setattr(run_app, "_try_per_monitor_dpi", lambda: True)
        monkeypatch.setattr(run_app.webview, "start", lambda *a, **k: None)
        monkeypatch.setattr(run_app.browser_profile, "purge", lambda profile: 0)
        monkeypatch.setattr(
            run_app.webview, "create_window",
            lambda *a, **k: types.SimpleNamespace(events=events))

        run_app.main()

        assert win_hardening.report_accessibility_privacy in events.loaded.handlers
        # GROUND: the same window object records the capture-exclusion handler
        # this app already wires. If this fake window were being ignored, both
        # lists would be empty and this would fail rather than pass quietly.
        assert win_hardening.apply_screen_privacy in events.shown.handlers


@WINDOWS_ONLY
class TestReadingBackWhatTheBrowserProcessGot:
    """Setting an environment variable and believing in it is the failure this
    module refuses everywhere else. These exercise the machinery that asks a
    real process what it was really started with - against real processes,
    because a mocked PEB read would prove only that the mock works."""

    def test_it_reads_a_real_command_line(self) -> None:
        marker = "--elysium-marker-9f2c"
        child = _sleeping_child(marker)
        try:
            line = win_hardening.command_line_of(child.pid)
            assert line is not None
            assert marker in line.split()
        finally:
            child.kill()
            child.wait(timeout=10)

    def test_a_missing_argument_is_actually_missing(self) -> None:
        # The ground for the test above. If command_line_of returned the same
        # thing for every process, both halves of the read-back would be
        # meaningless and nothing else here would notice.
        child = _sleeping_child("--elysium-marker-not-this-one")
        try:
            line = win_hardening.command_line_of(child.pid)
            assert line is not None
            assert "--elysium-marker-9f2c" not in line.split()
        finally:
            child.kill()
            child.wait(timeout=10)

    def test_a_process_that_is_gone_is_unknown_not_false(self) -> None:
        child = _sleeping_child("--elysium-marker-shortlived")
        child.kill()
        child.wait(timeout=10)
        assert win_hardening.command_line_of(child.pid) is None

    def test_it_finds_its_own_children_by_name(self) -> None:
        child = _sleeping_child("--elysium-marker-child")
        try:
            image = os.path.basename(sys.executable)
            assert child.pid in win_hardening.own_child_processes(image)
        finally:
            child.kill()
            child.wait(timeout=10)

    def test_it_does_not_invent_children(self) -> None:
        assert win_hardening.own_child_processes(
            "no-such-program-elysium.exe") == []

    def test_a_child_that_is_not_ours_is_not_counted(self) -> None:
        # own_child_processes matches on the PARENT id. A second copy of the
        # same program, started by somebody else, is not this app's WebView2 -
        # and answering about it would make the read-back a coin toss on a
        # machine with Edge open.
        child = _sleeping_child("--elysium-marker-grandchild")
        try:
            image = os.path.basename(sys.executable)
            for pid in win_hardening.own_child_processes(image):
                assert pid == child.pid
        finally:
            child.kill()
            child.wait(timeout=10)


class TestTheVerdictHasThreeAnswers:
    """True, False and "cannot tell". The third is not a nicety: reporting
    "not protected" when the truth is "could not check" is a false alarm on
    every launch, and a warning nobody can act on is one people learn to
    ignore."""

    def _browser_processes(self, monkeypatch, lines: dict[int, str | None]):
        monkeypatch.setattr(win_hardening, "own_child_processes",
                            lambda image: list(lines))
        monkeypatch.setattr(win_hardening, "command_line_of",
                            lambda pid: lines[pid])

    def test_the_flag_on_every_browser_process_is_a_yes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(config.ACCESSIBILITY_PRIVACY_ENV, raising=False)
        self._browser_processes(monkeypatch, {
            11: f"msedgewebview2.exe --embedded-browser-webview=1 "
                f"{win_hardening._RENDERER_ACCESSIBILITY_OFF}"})
        assert win_hardening.accessibility_privacy_verified() is True
        assert win_hardening.report_accessibility_privacy() is True

    def test_one_browser_process_without_it_is_a_no(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The silent failure this exists for: pywebview changing how it builds
        # those arguments, or a loader that stops reading the variable, looks
        # exactly like success from inside this process.
        monkeypatch.delenv(config.ACCESSIBILITY_PRIVACY_ENV, raising=False)
        self._browser_processes(monkeypatch, {
            11: f"msedgewebview2.exe {win_hardening._RENDERER_ACCESSIBILITY_OFF}",
            12: "msedgewebview2.exe --embedded-browser-webview=1"})
        assert win_hardening.accessibility_privacy_verified() is False

    def test_a_flag_that_is_only_a_prefix_does_not_count(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Substring matching would call --disable-renderer-accessibility-x a
        # match, and Chromium would ignore that switch entirely.
        monkeypatch.delenv(config.ACCESSIBILITY_PRIVACY_ENV, raising=False)
        self._browser_processes(monkeypatch, {
            11: f"msedgewebview2.exe "
                f"{win_hardening._RENDERER_ACCESSIBILITY_OFF}-x"})
        assert win_hardening.accessibility_privacy_verified() is False

    def test_no_browser_process_yet_is_unknown(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(config.ACCESSIBILITY_PRIVACY_ENV, raising=False)
        self._browser_processes(monkeypatch, {})
        assert win_hardening.accessibility_privacy_verified() is None
        assert win_hardening.report_accessibility_privacy() is None

    def test_an_unreadable_process_is_unknown(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(config.ACCESSIBILITY_PRIVACY_ENV, raising=False)
        self._browser_processes(monkeypatch, {11: None})
        assert win_hardening.accessibility_privacy_verified() is None

    def test_nothing_was_asked_for_so_nothing_is_verified(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(config.ACCESSIBILITY_PRIVACY_ENV,
                           config.ACCESSIBILITY_PRIVACY_OFF)
        self._browser_processes(monkeypatch, {
            11: f"msedgewebview2.exe {win_hardening._RENDERER_ACCESSIBILITY_OFF}"})
        assert win_hardening.accessibility_privacy_verified() is None

    def test_the_report_says_so_out_loud(
        self, monkeypatch: pytest.MonkeyPatch, caplog
    ) -> None:
        # The whole value of the read-back is that somebody can find out. A
        # verdict returned to a caller that logs nothing helps no one reading
        # elysium.log after the fact.
        monkeypatch.delenv(config.ACCESSIBILITY_PRIVACY_ENV, raising=False)
        self._browser_processes(monkeypatch, {
            11: "msedgewebview2.exe --embedded-browser-webview=1"})
        with caplog.at_level("WARNING", logger=win_hardening.log.name):
            assert win_hardening.report_accessibility_privacy() is False
        assert win_hardening._RENDERER_ACCESSIBILITY_OFF in caplog.text

    def test_a_broken_reader_never_raises_on_the_launch_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # This runs as a window event handler. pywebview swallows and logs an
        # exception there, so a raise would not crash the app - it would do
        # something worse and leave no verdict at all while looking fine.
        monkeypatch.delenv(config.ACCESSIBILITY_PRIVACY_ENV, raising=False)
        assert win_hardening.command_line_of(-1) is None
        assert win_hardening.own_child_processes("") == []
