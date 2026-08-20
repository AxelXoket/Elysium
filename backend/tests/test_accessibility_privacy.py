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

#: The registry path separator. Named rather than typed inline so no test
#: below carries a lone escape character.
SEP = chr(92)

#: A planted value with a person's account name in it. The point of the tests
#: below is that this string never reaches elysium.log, so it is built here
#: once rather than retyped in each of them.
#:
#: JOINED rather than written as a literal, and that is not style. An absolute
#: path spelled out in a source file publishes whose machine built the tree,
#: which is hygiene rule H-04, and requirements.lock.txt carried exactly that
#: into a public repository 47 times. A test about not leaking a name is a
#: poor place to leak one.
SECRET_PATH = SEP.join(("C:", "Users", "Somebody", "a-chat-title"))


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

    def test_it_clobbers_arguments_somebody_else_set(self) -> None:
        # This assertion is the reverse of the one it replaces, and the
        # reversal is the fix. The old test enforced appending, on the belief
        # that pywebview passed its own flags through this variable. It does
        # not: it sets CoreWebView2CreationProperties.AdditionalBrowserArguments
        # directly (webview/platforms/edgechromium.py:82-90), and nothing else
        # in this repository writes the variable at all. So the only thing
        # appending preserved was whatever a program running as this user had
        # planted with setx, and a debugging port planted there opens the
        # DevTools protocol on the window showing the decrypted conversation.
        env = {win_hardening._WEBVIEW2_ARGUMENTS_ENV:
               "--remote-debugging-port=9222"}
        assert win_hardening.apply_accessibility_privacy(env) is True
        assert env[win_hardening._WEBVIEW2_ARGUMENTS_ENV].split() == [
            win_hardening._RENDERER_ACCESSIBILITY_OFF]

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

    def test_refusing_it_still_scrubs_what_somebody_else_planted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The refusal branch used to return without touching the variable,
        # which made the user who turned this off the one user whose planted
        # flags reached the browser untouched. Refusing the accessibility
        # switch is a statement about assistive technology; it is not consent
        # to a debugging port. And somebody who turned the switch off to use a
        # screen reader is the least able to notice a warning on screen.
        monkeypatch.setenv(config.ACCESSIBILITY_PRIVACY_ENV,
                           config.ACCESSIBILITY_PRIVACY_OFF)
        env = {win_hardening._WEBVIEW2_ARGUMENTS_ENV:
               "--remote-debugging-port=9222"}
        assert win_hardening.apply_accessibility_privacy(env) is False
        assert win_hardening._WEBVIEW2_ARGUMENTS_ENV not in env

    def test_refusing_it_does_not_believe_a_mapping_that_kept_the_value(
        self, monkeypatch: pytest.MonkeyPatch, caplog
    ) -> None:
        # The read-back rule, applied to the delete. The armed branch has had
        # a Deaf-mapping test since this module was written; the refusal
        # branch had none, so an environment that silently kept the planted
        # value would have been reported as scrubbed.
        class Stubborn(dict):
            def pop(self, key, default=None):     # accepts, forgets nothing
                return default

        monkeypatch.setenv(config.ACCESSIBILITY_PRIVACY_ENV,
                           config.ACCESSIBILITY_PRIVACY_OFF)
        env = Stubborn({win_hardening._WEBVIEW2_ARGUMENTS_ENV:
                        "--remote-debugging-port=9222"})
        with caplog.at_level("WARNING", logger=win_hardening.log.name):
            assert win_hardening.apply_accessibility_privacy(env) is False
        assert any("could NOT be cleared" in r.getMessage()
                   for r in caplog.records)

    def test_it_names_the_switch_it_discarded_but_never_its_value(
        self, caplog
    ) -> None:
        # Scrubbing in silence would be the worst outcome: the flag is gone,
        # the attempt is invisible, and the machine looks healthy. So it warns.
        # But the value half of a switch is chosen by whoever planted it, and
        # a path carries an account name, so only the name may be printed.
        secret = SECRET_PATH
        env = {win_hardening._WEBVIEW2_ARGUMENTS_ENV:
               f"--user-data-dir={secret}"}
        with caplog.at_level("WARNING", logger=win_hardening.log.name):
            win_hardening.apply_accessibility_privacy(env)
        said = " ".join(r.getMessage() for r in caplog.records)
        assert "--user-data-dir" in said          # ground: it did report
        assert secret not in said                 # and it named only names
        assert "Somebody" not in said

    def test_a_planted_token_that_is_not_a_switch_is_counted_not_printed(
        self, caplog
    ) -> None:
        # The filter has to fail closed on shapes it was not designed for. A
        # bare path is not a switch, so it cannot be named; saying nothing
        # about it would under-report the discard, so it is counted instead.
        env = {win_hardening._WEBVIEW2_ARGUMENTS_ENV: SECRET_PATH}
        with caplog.at_level("WARNING", logger=win_hardening.log.name):
            win_hardening.apply_accessibility_privacy(env)
        said = " ".join(r.getMessage() for r in caplog.records)
        assert "Somebody" not in said
        assert "1 token(s) whose names are not printed" in said

    def test_it_scrubs_every_variable_the_webview2_loader_reads(self) -> None:
        # Not just the one this function sets. The variable we care about is
        # the least dangerous member of the family: a planted
        # WEBVIEW2_BROWSER_EXECUTABLE_FOLDER supplies msedgewebview2.exe
        # itself, at which point every other check on this page is inspecting
        # a binary the attacker provided.
        env = {name: "planted" for name in win_hardening._WEBVIEW2_OVERRIDE_ENV}
        # GROUND, by name rather than by count: a count rots every time
        # Microsoft documents another one, and the three named here are the
        # three whose absence would make this test pass while proving nothing.
        assert "WEBVIEW2_BROWSER_EXECUTABLE_FOLDER" in env
        assert "WEBVIEW2_USER_DATA_FOLDER" in env
        assert win_hardening._WEBVIEW2_ARGUMENTS_ENV in env
        assert win_hardening.apply_accessibility_privacy(env) is True
        assert set(env) == {win_hardening._WEBVIEW2_ARGUMENTS_ENV}
        assert env[win_hardening._WEBVIEW2_ARGUMENTS_ENV].split() == [
            win_hardening._RENDERER_ACCESSIBILITY_OFF]

    def test_a_planted_profile_redirect_does_not_survive(self) -> None:
        # WEBVIEW2_USER_DATA_FOLDER moves the browser profile somewhere
        # browser_profile.purge has never heard of, so the cached conversation
        # would outlive the sweep written to remove it. Closing the
        # accessibility tree would not have noticed.
        key = "WEBVIEW2_USER_DATA_FOLDER"
        assert key in win_hardening._WEBVIEW2_OVERRIDE_ENV
        env = {key: SECRET_PATH}
        win_hardening.apply_accessibility_privacy(env)
        assert key not in env

    def test_the_debugger_variables_go_even_when_the_switch_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # These two attach a script debugger and Microsoft documents no
        # registry equivalent for them, which makes them the only members of
        # the family this can close completely. Refusing the accessibility
        # switch is a statement about assistive technology, not consent to a
        # debugger.
        monkeypatch.setenv(config.ACCESSIBILITY_PRIVACY_ENV,
                           config.ACCESSIBILITY_PRIVACY_OFF)
        env = {"WEBVIEW2_WAIT_FOR_SCRIPT_DEBUGGER": "1",
               "WEBVIEW2_PIPE_FOR_SCRIPT_DEBUGGER": "1"}
        assert win_hardening.apply_accessibility_privacy(env) is False
        assert env == {}

    def test_the_family_is_discarded_by_name_never_by_value(
        self, caplog
    ) -> None:
        # BROWSER_EXECUTABLE_FOLDER and USER_DATA_FOLDER carry a
        # filesystem path, and a path on this machine carries the
        # account name. The value is withheld for the whole family
        # rather than for those two.
        env = {"WEBVIEW2_BROWSER_EXECUTABLE_FOLDER": SECRET_PATH}
        with caplog.at_level("WARNING", logger=win_hardening.log.name):
            win_hardening.apply_accessibility_privacy(env)
        said = " ".join(r.getMessage() for r in caplog.records)
        assert "WEBVIEW2_BROWSER_EXECUTABLE_FOLDER" in said   # ground
        assert SECRET_PATH not in said
        assert "Somebody" not in said

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

    def test_our_flag_beside_a_debugging_port_is_a_no(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The reason this cannot be answered by presence alone. The WebView2
        # loader APPENDS what it finds to what the host set, so a planted
        # --remote-debugging-port arrives BESIDE our flag rather than instead
        # of it. Asking only "is our flag there" answered True during exactly
        # the attack this function exists to notice, and logged "verified
        # closed" while the DevTools protocol was listening on that process.
        monkeypatch.delenv(config.ACCESSIBILITY_PRIVACY_ENV, raising=False)
        self._browser_processes(monkeypatch, {
            11: f"msedgewebview2.exe {win_hardening._RENDERER_ACCESSIBILITY_OFF}"
                f" --remote-debugging-port=9222"})
        assert win_hardening.accessibility_privacy_verified() is False

    def test_the_same_holds_for_the_pipe_form(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # --remote-debugging-pipe reaches the same protocol without opening a
        # socket, so a check written around the word "port" would miss it.
        monkeypatch.delenv(config.ACCESSIBILITY_PRIVACY_ENV, raising=False)
        self._browser_processes(monkeypatch, {
            11: f"msedgewebview2.exe --remote-debugging-pipe "
                f"{win_hardening._RENDERER_ACCESSIBILITY_OFF}"})
        assert win_hardening.accessibility_privacy_verified() is False

    def test_the_ordinary_switches_webview2_gives_itself_are_still_a_yes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # GROUND, and the reason the check is a denylist. The browser process
        # carries dozens of switches it sets for itself; an allowlist would
        # call the next Chromium version hostile and be turned off in a week.
        monkeypatch.delenv(config.ACCESSIBILITY_PRIVACY_ENV, raising=False)
        self._browser_processes(monkeypatch, {
            11: f"msedgewebview2.exe --embedded-browser-webview=1 "
                f"--webview-exe-name=Elysium.exe --mojo-named-platform-channel"
                f" --disable-features=ElasticOverscroll "
                f"{win_hardening._RENDERER_ACCESSIBILITY_OFF}"})
        assert win_hardening.accessibility_privacy_verified() is True

    def test_one_clean_process_does_not_excuse_a_dirty_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(config.ACCESSIBILITY_PRIVACY_ENV, raising=False)
        flag = win_hardening._RENDERER_ACCESSIBILITY_OFF
        self._browser_processes(monkeypatch, {
            11: f"msedgewebview2.exe {flag}",
            12: f"msedgewebview2.exe {flag} --remote-debugging-port=9222"})
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



class TestTheReportNamesTheFailureThatHappened:
    """Two ways to fail, and they used to share one sentence.

    _command_line_is_ours returns False when our flag is missing AND when a
    denylisted switch is present. The report collapsed both into "the browser
    did not get --disable-renderer-accessibility", so in the second case, the
    case the check was rewritten for, the log named a cause that was not true
    and never mentioned the debugging port it had just found. A reader
    following that line rechecks a control that is working.
    """

    def _browser(self, monkeypatch, line: str) -> None:
        monkeypatch.setattr(win_hardening, "own_child_processes",
                            lambda image: [11])
        monkeypatch.setattr(win_hardening, "command_line_of", lambda pid: line)

    def test_a_missing_flag_still_reads_as_a_missing_flag(
        self, monkeypatch: pytest.MonkeyPatch, caplog
    ) -> None:
        # GROUND. The sentence that was always correct must stay correct.
        monkeypatch.delenv(config.ACCESSIBILITY_PRIVACY_ENV, raising=False)
        self._browser(monkeypatch, "msedgewebview2.exe --embedded-browser-webview=1")
        with caplog.at_level("WARNING", logger=win_hardening.log.name):
            assert win_hardening.report_accessibility_privacy() is False
        said = " ".join(r.getMessage() for r in caplog.records)
        assert win_hardening._RENDERER_ACCESSIBILITY_OFF in said
        assert "--remote-debugging-port" not in said

    def test_a_debugging_port_beside_our_flag_is_named(
        self, monkeypatch: pytest.MonkeyPatch, caplog
    ) -> None:
        monkeypatch.delenv(config.ACCESSIBILITY_PRIVACY_ENV, raising=False)
        self._browser(
            monkeypatch,
            f"msedgewebview2.exe {win_hardening._RENDERER_ACCESSIBILITY_OFF} "
            f"--remote-debugging-port=9222")
        with caplog.at_level("WARNING", logger=win_hardening.log.name):
            assert win_hardening.report_accessibility_privacy() is False
        said = " ".join(r.getMessage() for r in caplog.records)
        assert "--remote-debugging-port" in said
        # and it must NOT blame the flag, which is present
        assert "did not get" not in said

    def test_the_port_number_is_not_printed(
        self, monkeypatch: pytest.MonkeyPatch, caplog
    ) -> None:
        # Names, never values. The same rule the environment scrub follows,
        # and it has to hold here too because this string comes off another
        # process's command line.
        monkeypatch.delenv(config.ACCESSIBILITY_PRIVACY_ENV, raising=False)
        self._browser(
            monkeypatch,
            f"msedgewebview2.exe {win_hardening._RENDERER_ACCESSIBILITY_OFF} "
            f"--user-data-dir={SECRET_PATH} --remote-debugging-port=9222")
        with caplog.at_level("WARNING", logger=win_hardening.log.name):
            win_hardening.report_accessibility_privacy()
        said = " ".join(r.getMessage() for r in caplog.records)
        assert "9222" not in said
        assert SECRET_PATH not in said
        assert "Somebody" not in said


@WINDOWS_ONLY
class TestTheLaunchPathActuallyLooksAtThePolicyDoor:
    def test_harden_calls_the_policy_check(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The control existed, was tested, and was wired to nothing.

        Measured on 20 August 2026: the only callers of
        webview2_policy_overrides were five tests. It never ran in the shipped
        app, while SECURITY.md told the reader that Elysium reports a WebView2
        policy key. This asserts the wiring rather than the function, because
        the function already has its own tests and the wiring is what was
        missing.
        """
        called: list[bool] = []

        def spy():
            called.append(True)
            return []

        monkeypatch.setattr(win_hardening, "webview2_policy_overrides", spy)
        result = win_hardening.harden(tmp_path)
        assert called, "harden() does not consult the policy door"
        assert "webview2_policy_overrides" in result


class TestTheSecondDoorIsLookedAtButNotShut:
    """The registry half of the same override mechanism.

    Microsoft's reference lists five policy values that override the same
    environment options, read from HKLM and then HKCU, and HKCU needs no
    elevation. Deleting an environment variable says nothing about them, so
    this module reports them rather than pretending the environment scrub
    covered both doors. It does not edit them: a key under the Microsoft Edge
    WebView2 policy path is shared with every WebView2 application on the
    machine and is not this app's to change.
    """

    @staticmethod
    def _hive(*present: str):
        """A registry stand-in. present is ("HKCU", "BrowserExecutableFolder")
        style pairs joined by the separator, exactly as the real subkey reads.
        """
        class _Handle:
            def Close(self) -> None:
                pass

        def open_key(root, subkey):
            leaf = subkey.rsplit(SEP, 1)[-1]
            if f"{root}{SEP}{leaf}" in present:
                return _Handle()
            raise OSError(2, "not found")

        return open_key

    def test_a_machine_with_no_policy_values_reports_nothing(self) -> None:
        # GROUND. Without this the refusal below would also pass for a reader
        # that never manages to open anything.
        assert win_hardening.webview2_policy_overrides(self._hive()) == []

    def test_a_policy_value_under_hkcu_is_reported(self) -> None:
        planted = "HKCU" + SEP + "BrowserExecutableFolder"
        found = win_hardening.webview2_policy_overrides(self._hive(planted))
        assert found == [planted]

    def test_both_hives_are_looked_at(self) -> None:
        # HKLM is where a real administrator would put it and HKCU is where an
        # attacker running as this user can. Reading only one would report a
        # clean machine for half the ways it can be dirty.
        planted = ("HKLM" + SEP + "UserDataFolder",
                   "HKCU" + SEP + "AdditionalBrowserArguments")
        found = win_hardening.webview2_policy_overrides(self._hive(*planted))
        assert sorted(found) == sorted(planted)

    def test_it_reports_the_name_and_never_reads_the_value(
        self, caplog
    ) -> None:
        # The value of BrowserExecutableFolder is a path the attacker chose,
        # and a path carries the account name. This function opens the key to
        # learn that it exists and closes it again; it never calls QueryValue.
        planted = "HKCU" + SEP + "BrowserExecutableFolder"
        with caplog.at_level("WARNING", logger=win_hardening.log.name):
            win_hardening.webview2_policy_overrides(self._hive(planted))
        said = " ".join(r.getMessage() for r in caplog.records)
        assert "BrowserExecutableFolder" in said      # ground: it did report
        assert SECRET_PATH not in said

    def test_every_name_microsoft_documents_is_looked_for(self) -> None:
        asked = []

        def open_key(root, subkey):
            asked.append(subkey.rsplit(SEP, 1)[-1])
            raise OSError(2, "not found")

        win_hardening.webview2_policy_overrides(open_key)
        assert set(asked) == set(win_hardening._WEBVIEW2_POLICY_NAMES)
        assert len(win_hardening._WEBVIEW2_POLICY_NAMES) == 5
