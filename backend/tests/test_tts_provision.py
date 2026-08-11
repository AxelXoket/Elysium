"""V3-0 - the app installs the voice engine. The user never opens a terminal.

That is an acceptance criterion, not a convenience, so these tests are written
against the promise rather than the implementation: one action starts it, it
can be watched, it can be cancelled, it can be retried, a failure says what
went wrong, and - the part that decides whether any of it is trustworthy - an
environment is only ever called "ready" after its imports have been proven to
work. A half-installed environment reported as ready is worse than no install.

The installer itself is faked: a real one downloads several gigabytes. What is
NOT faked is everything around it - the state machine, cancellation, the disk
check, cleanup of a partial environment, and registration.
"""
import os
import sys
import threading
import time
from pathlib import Path

import pytest

import config
from tts import provision, runtimes
from tts.errors import (
    TTS_INSUFFICIENT_DISK,
    TTS_PYTHON_NOT_FOUND,
    TTS_RUNTIME_INSTALLING,
    TTS_RUNTIME_INSTALL_FAILED,
)
@pytest.fixture(autouse=True)
def _vault_is_readable(monkeypatch):
    """The only state start_install can actually be reached in.

    vault_gate answers 423 for every data route while the vault is locked, so
    by the time the install route runs, the settings table is readable. These
    tests call provision.start_install() directly, with no database behind it,
    which made get_setting raise - and _proxy_required now fails CLOSED on
    that, correctly refusing to start a multi-gigabyte download when it cannot
    tell whether a proxy is mandatory. This fixture supplies the readable
    settings the route guarantees; the fail-closed behaviour itself is tested
    in test_provision_proxy.py, where it belongs.
    """
    import database
    monkeypatch.setattr(database, "get_setting", lambda name: None)




@pytest.fixture
def voice_dir(monkeypatch, tmp_path):
    root = tmp_path / "voice"
    (root / "envs").mkdir(parents=True)
    (root / "bin").mkdir(parents=True)
    monkeypatch.setattr(config, "TTS_DIR", root, raising=False)
    monkeypatch.setattr(config, "TTS_ENVS_DIR", str(root / "envs"), raising=False)
    monkeypatch.setattr(config, "TTS_BIN_DIR", str(root / "bin"), raising=False)
    monkeypatch.setattr(config, "TTS_PY_DIR", str(root / "python"), raising=False)
    monkeypatch.setattr(config, "TTS_UV_CACHE_DIR", str(root / "uv-cache"), raising=False)
    monkeypatch.setattr(config, "TTS_RUNTIMES_PATH", str(root / "runtimes.json"),
                        raising=False)
    provision.reset_jobs()
    yield root
    provision.reset_jobs()


def _fake_uv(monkeypatch, tmp_path):
    uv = tmp_path / "uv.exe"
    uv.write_bytes(b"")
    monkeypatch.setattr(provision, "find_uv", lambda: str(uv))
    return uv


class _Recorder:
    """Stands in for the several-gigabyte part. Records what it was asked to
    do so the tests can assert on the COMMANDS, which is the part that has to
    be right."""

    def __init__(self, *, fail_at=None, delay=0.0, make_python=True):
        self.calls = []
        self.fail_at = fail_at
        self.delay = delay
        self.make_python = make_python

    def __call__(self, argv, *, on_line, cancel, timeout, env=None):
        self.calls.append(list(argv))
        if self.delay:
            waited = 0.0
            while waited < self.delay:
                if cancel.is_set():
                    return 1, "cancelled"
                time.sleep(0.05)
                waited += 0.05
        on_line("Resolved 174 packages")
        if self.fail_at is not None and len(self.calls) > self.fail_at:
            on_line("error: failed to fetch the wheel")
            return 1, "failed to fetch"
        if self.make_python and "venv" in argv:
            target = Path(argv[argv.index("venv") + 1])
            (target / "Scripts").mkdir(parents=True, exist_ok=True)
            (target / "Scripts" / "python.exe").write_bytes(b"")
        return 0, ""


class TestThePlan:
    def test_every_supported_engine_has_pinned_requirements_shipped(self):
        """These pins are the measured working configuration. If a file goes
        missing, the one-click install quietly becomes a guess."""
        for engine_id in ("fish_s2", "xtts_v2", "chatterbox"):
            req = provision.requirements_path(engine_id)
            assert req.is_file(), f"no pinned requirements for {engine_id}"
            body = req.read_text(encoding="utf-8")
            assert "torch==" in body, f"{engine_id} does not pin torch"

    def test_fish_pulls_in_its_own_source_because_it_is_not_on_pypi(self):
        body = provision.requirements_path("fish_s2").read_text(encoding="utf-8")
        assert "fish-speech @" in body, "fish_speech would be missing at import time"

    def test_the_plan_says_where_it_will_put_things(self, voice_dir):
        plan = provision.plan("fish_s2")
        assert plan.engine_id == "fish_s2"
        assert str(voice_dir / "envs" / "fish_s2") in plan.env_dir
        assert plan.download_mb > 1000, "a CUDA install is multi-GB; say so"

    def test_an_unknown_engine_has_no_plan(self, voice_dir):
        with pytest.raises(provision.ProvisionError):
            provision.plan("not_an_engine")


class TestTheHappyPath:
    def test_one_call_installs_and_registers_the_runtime(self, voice_dir, monkeypatch,
                                                         tmp_path):
        _fake_uv(monkeypatch, tmp_path)
        rec = _Recorder()
        monkeypatch.setattr(provision, "_run", rec)
        monkeypatch.setattr(provision, "_verify_env", lambda *a, **k: None)

        provision.start_install("fish_s2")
        job = _await(voice_dir, "fish_s2")

        assert job["state"] == "done", job
        assert runtimes.status("fish_s2").state == "ready"
        assert any("venv" in c for c in rec.calls), "it never created an environment"
        assert any("install" in c for c in rec.calls), "it never installed anything"

    def test_it_installs_from_the_pinned_file_not_from_a_guess(self, voice_dir,
                                                               monkeypatch, tmp_path):
        _fake_uv(monkeypatch, tmp_path)
        rec = _Recorder()
        monkeypatch.setattr(provision, "_run", rec)
        monkeypatch.setattr(provision, "_verify_env", lambda *a, **k: None)
        provision.start_install("fish_s2")
        _await(voice_dir, "fish_s2")
        install = next(c for c in rec.calls if "install" in c)
        assert "-r" in install
        assert install[install.index("-r") + 1].endswith("fish_s2.txt")

    def test_it_asks_for_the_cuda_build_of_torch(self, voice_dir, monkeypatch, tmp_path):
        """Left alone, the resolver happily serves the CPU build from PyPI and
        the user gets a voice engine that cannot see their GPU."""
        _fake_uv(monkeypatch, tmp_path)
        rec = _Recorder()
        monkeypatch.setattr(provision, "_run", rec)
        monkeypatch.setattr(provision, "_verify_env", lambda *a, **k: None)
        provision.start_install("fish_s2")
        _await(voice_dir, "fish_s2")
        install = next(c for c in rec.calls if "install" in c)
        assert any("cu128" in part for part in install)

    def test_progress_lines_are_visible_while_it_runs(self, voice_dir, monkeypatch,
                                                      tmp_path):
        _fake_uv(monkeypatch, tmp_path)
        monkeypatch.setattr(provision, "_run", _Recorder())
        monkeypatch.setattr(provision, "_verify_env", lambda *a, **k: None)
        provision.start_install("fish_s2")
        job = _await(voice_dir, "fish_s2")
        assert any("Resolved" in ln for ln in job["log"])


class TestItRefusesRatherThanHalfInstalling:
    def test_a_second_install_of_the_same_engine_is_refused(self, voice_dir,
                                                            monkeypatch, tmp_path):
        _fake_uv(monkeypatch, tmp_path)
        monkeypatch.setattr(provision, "_run", _Recorder(delay=2.0))
        monkeypatch.setattr(provision, "_verify_env", lambda *a, **k: None)
        provision.start_install("fish_s2")
        try:
            with pytest.raises(provision.ProvisionError) as exc:
                provision.start_install("fish_s2")
            assert exc.value.code == TTS_RUNTIME_INSTALLING
        finally:
            provision.cancel("fish_s2")

    def test_it_checks_for_disk_space_before_downloading_gigabytes(self, voice_dir,
                                                                   monkeypatch, tmp_path):
        _fake_uv(monkeypatch, tmp_path)
        monkeypatch.setattr(provision, "_free_gb", lambda path: 2.0)
        with pytest.raises(provision.ProvisionError) as exc:
            provision.start_install("fish_s2")
        assert exc.value.code == TTS_INSUFFICIENT_DISK

    def test_with_no_way_to_build_an_environment_it_says_so_plainly(self, voice_dir,
                                                                    monkeypatch):
        """Reported by the JOB, not by the POST.

        The uv download moved off the request thread (audit): start_install is
        documented as "Begin. Returns immediately" but fetched ~25 MB inline, so
        the POST hung for the whole download while the job already reported
        running=true and the UI drew a Cancel button that could not reach it.
        The reason still reaches the user, through the channel that can also
        carry a cancel.
        """
        monkeypatch.setattr(provision, "find_uv", lambda: None)
        monkeypatch.setattr(provision, "_download_uv", lambda *a, **k: None)
        provision.start_install("fish_s2")
        job = _await(voice_dir, "fish_s2")
        assert job["state"] == "failed"
        assert job["error_code"] == TTS_PYTHON_NOT_FOUND
        assert job["error_detail"]


class TestFailureLeavesNoMess:
    def test_a_failed_install_is_reported_with_a_reason(self, voice_dir, monkeypatch,
                                                        tmp_path):
        _fake_uv(monkeypatch, tmp_path)
        monkeypatch.setattr(provision, "_run", _Recorder(fail_at=1))
        provision.start_install("fish_s2")
        job = _await(voice_dir, "fish_s2")
        assert job["state"] == "failed"
        assert job["error_code"] == TTS_RUNTIME_INSTALL_FAILED
        assert job["error_detail"], "a failure with no reason is a dead end"

    def test_a_failed_install_does_not_register_a_runtime(self, voice_dir, monkeypatch,
                                                          tmp_path):
        """Registering a half-built environment would turn every later load
        into a mysterious crash instead of an honest 'not set up'."""
        _fake_uv(monkeypatch, tmp_path)
        monkeypatch.setattr(provision, "_run", _Recorder(fail_at=1))
        provision.start_install("fish_s2")
        _await(voice_dir, "fish_s2")
        assert runtimes.status("fish_s2").state == "missing"

    def test_a_failed_install_removes_the_partial_environment(self, voice_dir,
                                                              monkeypatch, tmp_path):
        _fake_uv(monkeypatch, tmp_path)
        monkeypatch.setattr(provision, "_run", _Recorder(fail_at=1))
        provision.start_install("fish_s2")
        _await(voice_dir, "fish_s2")
        assert not (voice_dir / "envs" / "fish_s2").exists()

    def test_an_environment_that_cannot_import_its_engine_is_not_ready(
        self, voice_dir, monkeypatch, tmp_path
    ):
        """The install command succeeded and the environment is still useless.
        Verification is the only thing standing between that and a user who is
        told voice is ready and then hears nothing."""
        _fake_uv(monkeypatch, tmp_path)
        monkeypatch.setattr(provision, "_run", _Recorder())

        def cannot_import(*a, **k):
            raise provision.ProvisionError(TTS_RUNTIME_INSTALL_FAILED,
                                           "torch could not be imported")

        monkeypatch.setattr(provision, "_verify_env", cannot_import)
        provision.start_install("fish_s2")
        job = _await(voice_dir, "fish_s2")
        assert job["state"] == "failed"
        assert runtimes.status("fish_s2").state == "missing"

    def test_retrying_after_a_failure_works(self, voice_dir, monkeypatch, tmp_path):
        _fake_uv(monkeypatch, tmp_path)
        monkeypatch.setattr(provision, "_verify_env", lambda *a, **k: None)
        monkeypatch.setattr(provision, "_run", _Recorder(fail_at=1))
        provision.start_install("fish_s2")
        assert _await(voice_dir, "fish_s2")["state"] == "failed"

        monkeypatch.setattr(provision, "_run", _Recorder())
        provision.start_install("fish_s2")
        assert _await(voice_dir, "fish_s2")["state"] == "done"
        assert runtimes.status("fish_s2").state == "ready"


class TestCancelling:
    def test_it_can_be_cancelled_mid_flight(self, voice_dir, monkeypatch, tmp_path):
        _fake_uv(monkeypatch, tmp_path)
        monkeypatch.setattr(provision, "_run", _Recorder(delay=5.0))
        monkeypatch.setattr(provision, "_verify_env", lambda *a, **k: None)
        provision.start_install("fish_s2")
        time.sleep(0.3)
        provision.cancel("fish_s2")
        job = _await(voice_dir, "fish_s2")
        assert job["state"] == "cancelled"

    def test_cancelling_cleans_up_the_partial_environment(self, voice_dir, monkeypatch,
                                                          tmp_path):
        """A cancelled install leaves a partly populated site-packages that a
        plain re-run would build on top of. Delete it instead."""
        _fake_uv(monkeypatch, tmp_path)
        monkeypatch.setattr(provision, "_run", _Recorder(delay=5.0))
        provision.start_install("fish_s2")
        time.sleep(0.3)
        provision.cancel("fish_s2")
        _await(voice_dir, "fish_s2")
        assert not (voice_dir / "envs" / "fish_s2").exists()
        assert runtimes.status("fish_s2").state == "missing"

    def test_cancelling_something_that_is_not_running_is_harmless(self, voice_dir):
        provision.cancel("fish_s2")


class TestUninstall:
    def test_it_gives_the_disk_back_and_forgets_the_runtime(self, voice_dir, monkeypatch,
                                                            tmp_path):
        _fake_uv(monkeypatch, tmp_path)
        monkeypatch.setattr(provision, "_run", _Recorder())
        monkeypatch.setattr(provision, "_verify_env", lambda *a, **k: None)
        provision.start_install("fish_s2")
        _await(voice_dir, "fish_s2")
        assert runtimes.status("fish_s2").state == "ready"

        provision.uninstall("fish_s2")
        assert runtimes.status("fish_s2").state == "missing"
        assert not (voice_dir / "envs" / "fish_s2").exists()

    def test_uninstalling_what_was_never_installed_is_harmless(self, voice_dir):
        provision.uninstall("fish_s2")


def _await(voice_dir, engine_id, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = provision.job(engine_id)
        if job["state"] in ("done", "failed", "cancelled"):
            return job
        time.sleep(0.05)
    raise AssertionError("install job never finished: %r" % provision.job(engine_id))


# ── Audit: provisioning ignored the configured proxy entirely ───────────────
#
# _download_uv used the default urllib opener (github.com) and the env handed to
# every uv command carried only cache/timeout/UTF-8 keys - so a user who set a
# proxy, even with proxy_required ON (which blocks completions outright when the
# proxy is unhealthy), had their real IP contact GitHub, PyPI and
# download.pytorch.org for several GB, with nothing saying the proxy was unused.


class TestProvisioningHonoursTheProxy:
    def test_the_install_env_carries_the_configured_proxy(self, monkeypatch):
        monkeypatch.setattr(provision, "_proxy_url", lambda: "http://proxy:8080")
        env = provision._proxy_env({"UV_CACHE_DIR": "C:/cache"})
        assert env["UV_CACHE_DIR"] == "C:/cache"
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                    "http_proxy", "https_proxy", "all_proxy"):
            assert env[key] == "http://proxy:8080"

    def test_no_proxy_configured_still_strips_the_ambient_one(self,
                                                              monkeypatch):
        # This used to assert the env came back untouched, which is what made
        # the installer the one place in the app that trusted the user's
        # shell: with no proxy configured, an exported HTTPS_PROXY was still
        # in force for a multi-gigabyte download. The caller's own variables
        # are the only thing that survives.
        monkeypatch.setattr(provision, "_proxy_url", lambda: None)
        env = provision._proxy_env({"UV_CACHE_DIR": "C:/cache"})
        assert env["UV_CACHE_DIR"] == "C:/cache"
        assert env["HTTPS_PROXY"] is None
        assert env["NO_PROXY"] is None

    def test_the_downloader_opens_through_the_proxy(self, monkeypatch):
        monkeypatch.setattr(provision, "_proxy_url", lambda: "http://proxy:8080")
        opener = provision._url_opener()
        handlers = [type(h).__name__ for h in opener.handlers]
        assert "ProxyHandler" in handlers
        proxy_handler = next(h for h in opener.handlers
                             if type(h).__name__ == "ProxyHandler")
        assert proxy_handler.proxies == {"http": "http://proxy:8080",
                                         "https": "http://proxy:8080"}

    def test_with_no_proxy_an_ambient_env_var_is_not_picked_up(self, monkeypatch):
        """Not the DEFAULT opener: that reads HTTP_PROXY from the process
        environment, which this app deliberately does not trust (every httpx
        client is built with trust_env=False for the same reason)."""
        monkeypatch.setattr(provision, "_proxy_url", lambda: None)
        monkeypatch.setenv("HTTP_PROXY", "http://ambient:9999")
        monkeypatch.setenv("HTTPS_PROXY", "http://ambient:9999")
        proxies = [h.proxies for h in provision._url_opener().handlers
                   if type(h).__name__ == "ProxyHandler"]
        assert all(p == {} for p in proxies), (
            f"the opener would go through an ambient proxy: {proxies}"
        )

    def test_a_mandatory_proxy_with_none_configured_refuses_the_install(
        self, voice_dir, monkeypatch,
    ):
        monkeypatch.setattr(provision, "_proxy_required", lambda: True)
        monkeypatch.setattr(provision, "_proxy_url", lambda: None)
        with pytest.raises(provision.ProvisionError) as exc:
            provision.start_install("fish_s2")
        assert exc.value.code == TTS_RUNTIME_INSTALL_FAILED
        assert "proxy" in exc.value.detail.lower()

    def test_a_mandatory_proxy_that_IS_configured_proceeds(
        self, voice_dir, monkeypatch, tmp_path,
    ):
        _fake_uv(monkeypatch, tmp_path)
        monkeypatch.setattr(provision, "_proxy_required", lambda: True)
        monkeypatch.setattr(provision, "_proxy_url", lambda: "http://proxy:8080")
        # The gate reads _read_proxy, which distinguishes "none configured"
        # from "the vault could not answer". _proxy_url is the non-raising
        # wrapper used for the environment.
        monkeypatch.setattr(provision, "_read_proxy", lambda: "http://proxy:8080")
        monkeypatch.setattr(provision, "_run", _Recorder())
        monkeypatch.setattr(provision, "_verify_env", lambda *a, **k: None)
        provision.start_install("fish_s2")
        assert _await(voice_dir, "fish_s2")["state"] == "done"


class TestTheUvDownloadIsCancellable:
    def test_the_read_loop_honours_the_cancel_event(self, monkeypatch, tmp_path):
        """Cancel used to be a no-op: the job was in _JOBS with running=true so
        the UI drew its Cancel button, pressing it set the event and returned
        200 - and this loop never looked at it."""
        import threading

        monkeypatch.setattr(config, "TTS_BIN_DIR", str(tmp_path / "bin"))
        monkeypatch.setattr(provision, "IS_WINDOWS", True)
        cancel = threading.Event()
        cancel.set()

        class NeverEndingResponse:
            def read(self, n):  # pragma: no cover - must not be reached
                raise AssertionError("kept downloading after cancel")

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class Opener:
            def open(self, url, timeout=None):
                return NeverEndingResponse()

        monkeypatch.setattr(provision, "_url_opener", lambda: Opener())
        lines: list[str] = []
        assert provision._download_uv(lines.append, cancel=cancel) is None
        assert any("cancel" in line for line in lines)

    def test_start_install_does_not_download_on_the_request_thread(
        self, voice_dir, monkeypatch,
    ):
        """The POST must answer immediately; the fetch belongs to the worker."""
        monkeypatch.setattr(provision, "find_uv", lambda: None)

        def must_not_run(*a, **k):  # pragma: no cover
            raise AssertionError("downloaded on the request thread")

        monkeypatch.setattr(provision, "_install_worker", lambda *a, **k: None)
        monkeypatch.setattr(provision, "_download_uv", must_not_run)
        body = provision.start_install("fish_s2")
        assert body["running"] is True


class TestWhatTheInstallerHandsItsChild:
    """`uv` runs setup code from wheels this app did not write.

    That is the sharpest edge in the whole privacy story: the installer is the
    one feature that deliberately fetches something from the internet other
    than the provider, and it then executes what it fetched. What that child
    process inherits is therefore a security boundary, and until KADEME 15b it
    had no test at all - the strip is a single line with a careful comment
    above it and nothing watching it.
    """

    def _child_env(self, monkeypatch, argv=("uv", "--version"), env=None):
        """Run the REAL `_run` and capture the environment it built.

        Deliberately not the `_Recorder` used elsewhere in this file: that one
        stands in for `_run` itself, so it can only see the overrides a caller
        passed, never the environment `_run` assembles from them.
        """
        import threading

        seen: dict = {}

        class _Popen:
            def __init__(self, args, **kw):
                seen["argv"] = list(args)
                seen["env"] = dict(kw.get("env") or {})
                self.stdout = iter(())
                self.returncode = 0

            def wait(self, timeout=None):
                return 0

            def poll(self):
                return 0

            def kill(self):
                pass

        monkeypatch.setattr(provision.subprocess, "Popen", _Popen)
        provision._run(list(argv), on_line=lambda line: None,
                       cancel=threading.Event(), timeout=5.0, env=env)
        return seen["env"]

    def test_the_launch_token_never_reaches_the_installer(self, monkeypatch):
        """The one credential that would let downloaded setup code ask the
        local API for the whole conversation."""
        import launch_token

        monkeypatch.setenv(launch_token.ENV_VAR, "the-secret-launch-token")
        env = self._child_env(monkeypatch)

        assert launch_token.ENV_VAR not in env, (
            "the installer handed its child the launch token")
        # Not merely renamed or blanked: the value itself is gone.
        assert not any("the-secret-launch-token" in v for v in env.values())
        # The floor: this process really did have the token to give away, so
        # the assertion above is not passing because the fixture forgot to set
        # it - and the child got a real environment, not an empty one.
        assert launch_token.ENV_VAR in os.environ
        assert len(env) > 3, env

    def test_a_variable_set_to_none_is_taken_away_not_stringified(self, monkeypatch):
        """How the proxy and index strip is expressed: the caller passes None
        to mean REMOVE. Without that branch the child would inherit the string
        "None", which is worse than inheriting the real value - it looks set.
        """
        monkeypatch.setenv("HTTPS_PROXY", "http://corp.example:8080")
        env = self._child_env(monkeypatch, env={"HTTPS_PROXY": None})

        assert "HTTPS_PROXY" not in env, env.get("HTTPS_PROXY")
        assert "None" not in env.values()

    def test_the_strip_is_the_callers_job_and_this_pins_it(self, monkeypatch):
        """MEASURED, and recorded because it is a seam rather than a guarantee.

        `_run` does NOT strip the ambient proxy and index variables itself - it
        removes only the launch token. The network strip arrives as `env`
        overrides from the install path (`_proxy_env`). So the promise holds
        for every caller that remembers, and silently stops holding for one
        that does not. Pinned so that the day the responsibility moves, this
        test comes and asks why.
        """
        monkeypatch.setenv("PIP_INDEX_URL", "https://mirror.example/simple")
        assert self._child_env(monkeypatch)["PIP_INDEX_URL"] == (
            "https://mirror.example/simple")
        assert "PIP_INDEX_URL" in provision._ENV_NETWORK_STRIP


# ── folded in from test_tts_provision_audit2.py (KADEME 15b) ─────────────
#
# That file verified a set of fixes under a new filename, which left THIS
# one still describing the world before them. Its module docstring said:
#
#   Audit-2 - provision's three destruction paths, closed.
#
#   The worst chain the audit demonstrated: a locked file in the old environment
#   made rmtree half-gut it, os.replace then raised, and the error path deleted
#   the fully VERIFIED staging - ending with no usable environment at all while
#   runtimes.json still said "ready". The rename-aside swap must make that chain
#   impossible: a locked old environment fails CLEANLY with both environments
#   intact and a coded error.
#
# All four tests moved; none was already proven here. `_await` gained the
# destination helper's leading `voice_dir` argument, which is the only
# change any of them needed.


class _OkInstaller:
    """Builds a working staging env, quickly. Travelled with the tests it
    serves; the `_Recorder` above records commands instead, which is a
    different job."""

    def __call__(self, argv, *, on_line, cancel, timeout, env=None):
        if "venv" in argv:
            target = Path(argv[argv.index("venv") + 1])
            (target / "Scripts").mkdir(parents=True, exist_ok=True)
            (target / "Scripts" / "python.exe").write_bytes(b"new")
        on_line("ok")
        return 0, ""


class TestRenameAsideSwap:
    def test_a_locked_old_environment_fails_clean_with_both_envs_intact(
        self, voice_dir, monkeypatch, tmp_path
    ):
        """The audit's exact chain. With the old env's python.exe held open,
        the swap must refuse - old env untouched, VERIFIED staging preserved,
        runtime registration still pointing at the working install."""
        if sys.platform != "win32":
            pytest.skip("directory-lock semantics are the Windows case")
        _fake_uv(monkeypatch, tmp_path)
        monkeypatch.setattr(provision, "_run", _OkInstaller())
        monkeypatch.setattr(provision, "_verify_env", lambda *a, **k: None)

        # A previously working environment, registered as ready.
        old_env = voice_dir / "envs" / "fish_s2" / "Scripts"
        old_env.mkdir(parents=True)
        old_py = old_env / "python.exe"
        old_py.write_bytes(b"old interpreter")
        runtimes.register("fish_s2", str(old_py))

        with open(old_py, "rb"):                 # the lock (a running worker)
            provision.start_install("fish_s2")
            job = _await(voice_dir, "fish_s2")

        assert job["state"] == "failed"
        assert job["error_code"] == TTS_RUNTIME_INSTALL_FAILED
        # The old environment survived, byte for byte.
        assert old_py.read_bytes() == b"old interpreter"
        # And the registration still points at a real interpreter.
        assert runtimes.status("fish_s2").state == "ready"

    def test_an_unlocked_swap_replaces_and_cleans_the_old_copy(
        self, voice_dir, monkeypatch, tmp_path
    ):
        _fake_uv(monkeypatch, tmp_path)
        monkeypatch.setattr(provision, "_run", _OkInstaller())
        monkeypatch.setattr(provision, "_verify_env", lambda *a, **k: None)

        old_env = voice_dir / "envs" / "fish_s2" / "Scripts"
        old_env.mkdir(parents=True)
        (old_env / "python.exe").write_bytes(b"old interpreter")

        provision.start_install("fish_s2")
        job = _await(voice_dir, "fish_s2")
        assert job["state"] == "done"
        assert (voice_dir / "envs" / "fish_s2" / "Scripts" / "python.exe"
                ).read_bytes() == b"new"
        assert not (voice_dir / "envs" / "fish_s2.old").exists()
        assert runtimes.status("fish_s2").state == "ready"


class TestUninstallSlot:
    def test_uninstall_occupies_the_job_slot_so_an_install_cannot_start_under_it(
        self, voice_dir, monkeypatch, tmp_path
    ):
        """C8: uninstall used to re-check once and then pop whatever job held
        the slot after a multi-GB rmtree - including a brand-new install's."""
        _fake_uv(monkeypatch, tmp_path)
        started = {"install": False}

        real_rmtree = provision.shutil.rmtree

        def slow_rmtree(path, **kw):
            # Probe exactly once, on the FIRST removal (the env itself): the
            # uninstall also sweeps the uv cache AFTER releasing its slot, and
            # that later rmtree is legitimately not busy any more.
            if not started["install"]:
                started["install"] = True
                with pytest.raises(provision.ProvisionError) as exc:
                    provision.start_install("fish_s2")
                assert exc.value.code == TTS_RUNTIME_INSTALLING
            return real_rmtree(path, **kw)

        env = voice_dir / "envs" / "fish_s2"
        env.mkdir(parents=True)
        monkeypatch.setattr(provision.shutil, "rmtree", slow_rmtree)
        provision.uninstall("fish_s2")
        assert started["install"], "the concurrent install was never attempted"
        # And the slot is free again afterwards.
        assert provision.job("fish_s2")["running"] is False


class TestAtomicUvExtract:
    def test_a_mid_extract_failure_leaves_no_uv_at_the_final_path(
        self, voice_dir, monkeypatch
    ):
        """C9: a truncated uv.exe at the final path would be trusted by
        find_uv forever - voice setup permanently bricked."""
        import io
        import zipfile as zf_mod

        bin_dir = Path(config.TTS_BIN_DIR)
        bin_dir.mkdir(parents=True, exist_ok=True)
        target = bin_dir / ("uv.exe" if sys.platform == "win32" else "uv")

        # Build a real zip containing uv.exe, then make the copy explode.
        buf = io.BytesIO()
        with zf_mod.ZipFile(buf, "w") as z:
            z.writestr("uv-x86_64/uv.exe", b"binary" * 100)
        archive_bytes = buf.getvalue()

        import hashlib
        digest = hashlib.sha256(archive_bytes).hexdigest()
        monkeypatch.setattr(provision, "UV_SHA256", digest, raising=False)

        def fake_urlopen(url, timeout=0):
            return io.BytesIO(archive_bytes)

        monkeypatch.setattr(provision, "_urlopen", fake_urlopen, raising=False)

        def exploding_copy(src, dst, *a, **k):
            dst.write(b"trunc")
            raise OSError("disk full")

        monkeypatch.setattr(provision.shutil, "copyfileobj", exploding_copy)
        got = provision._download_uv(lambda ln: None)
        assert got is None
        assert not target.exists(), "a truncated uv.exe was left to be trusted"


# ── folded in from test_tts_audit_fixes.py (KADEME 15b) ──────────────────
#
# The provisioning third of that file. It drives `provision._run` and the
# uv downloader directly, where the tests above it drive the install job,
# so the two sets meet rather than overlap.


class TestProvisionUnderPressure:
    def _silent_sleeper(self):
        return [sys.executable, "-c", "import time; time.sleep(60)"]

    def test_cancel_fires_even_when_the_child_prints_nothing(self):
        """uv with a redirected stdout suppresses its progress bar and can go
        minutes without a line. Cancel must fire on the wall clock, not on the
        child's chattiness - before the fix it waited for output forever."""
        cancel = threading.Event()
        result = {}

        def run():
            result["out"] = provision._run(
                self._silent_sleeper(), on_line=lambda ln: None,
                cancel=cancel, timeout=300)

        t = threading.Thread(target=run, daemon=True)
        t.start()
        time.sleep(1.0)
        cancel.set()
        t.join(timeout=15)
        assert t.is_alive() is False, "cancel did not interrupt a silent child"
        code, detail = result["out"]
        assert code != 0 and "cancel" in detail

    def test_the_timeout_fires_on_a_silent_child_too(self):
        code, detail = provision._run(
            self._silent_sleeper(), on_line=lambda ln: None,
            cancel=threading.Event(), timeout=1.5)
        assert code != 0 and "timed out" in detail

    def test_verify_env_actually_verifies(self, monkeypatch, tmp_path):
        """The module calls this THE RULE THAT MAKES IT SAFE; every other test
        monkeypatches it out. Run the real one, both ways."""
        # Positive leg with a REAL import list (audit-2: the old positive ran
        # before the engine was registered, so the loop body never executed
        # and the pass direction proved nothing).
        monkeypatch.setitem(provision.ENGINES, "probe", {"verify": ["json"]})
        provision._verify_env(
            "probe", sys.executable, lambda ln: None, threading.Event())

        monkeypatch.setitem(provision.ENGINES, "probe",
                            {"verify": ["module_that_does_not_exist_xyz"]})
        with pytest.raises(provision.ProvisionError) as exc:
            provision._verify_env(
                "probe", sys.executable, lambda ln: None, threading.Event())
        assert "module_that_does_not_exist_xyz" in exc.value.detail

    def test_a_failed_reinstall_keeps_the_working_environment(
        self, monkeypatch, tmp_path
    ):
        """A repair that deletes the working install and then fails leaves the
        user WORSE off than before they clicked. Build in staging, swap only
        after verification."""
        envs = tmp_path / "envs"
        monkeypatch.setattr(config, "TTS_ENVS_DIR", str(envs), raising=False)
        monkeypatch.setattr(config, "TTS_UV_CACHE_DIR", str(tmp_path / "cache"),
                            raising=False)
        monkeypatch.setattr(config, "TTS_PY_DIR", str(tmp_path / "py"), raising=False)
        monkeypatch.setattr(config, "TTS_RUNTIMES_PATH",
                            str(tmp_path / "runtimes.json"), raising=False)
        working = envs / "fish_s2" / "Scripts"
        working.mkdir(parents=True)
        (working / "python.exe").write_bytes(b"the working install")
        runtimes.register("fish_s2", str(working / "python.exe"))

        def failing_run(argv, *, on_line, cancel, timeout, env=None):
            return 1, "wheel exploded"

        monkeypatch.setattr(provision, "_run", failing_run)
        provision.reset_jobs()
        job = provision._Job(engine_id="fish_s2", state="preparing")
        provision._install_worker(job, provision.plan("fish_s2"), "uv-stub")

        assert job.state == "failed"
        assert (working / "python.exe").read_bytes() == b"the working install", (
            "the failed repair destroyed the working environment")
        assert runtimes.status("fish_s2").state == "ready"
        assert not (envs / "fish_s2.staging").exists()

    def test_uninstall_reports_what_actually_happened(self, monkeypatch, tmp_path):
        monkeypatch.setattr(config, "TTS_ENVS_DIR", str(tmp_path / "envs"),
                            raising=False)
        monkeypatch.setattr(config, "TTS_UV_CACHE_DIR", str(tmp_path / "cache"),
                            raising=False)
        monkeypatch.setattr(config, "TTS_RUNTIMES_PATH",
                            str(tmp_path / "runtimes.json"), raising=False)
        provision.reset_jobs()
        assert provision.uninstall("fish_s2")["removed"] is False   # nothing there

        env = tmp_path / "envs" / "fish_s2"
        env.mkdir(parents=True)
        (env / "python.exe").write_bytes(b"")
        assert provision.uninstall("fish_s2")["removed"] is True

    def test_uninstall_refuses_an_engine_id_it_does_not_know(self, monkeypatch,
                                                             tmp_path):
        """These ids become paths handed to rmtree; the whitelist must hold in
        the module itself, not only in the router."""
        with pytest.raises(provision.ProvisionError):
            provision.uninstall("..")

    def test_removing_the_last_engine_reclaims_the_wheel_cache(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(config, "TTS_ENVS_DIR", str(tmp_path / "envs"),
                            raising=False)
        cache = tmp_path / "uv-cache"
        cache.mkdir()
        (cache / "wheel.whl").write_bytes(b"x" * 100)
        monkeypatch.setattr(config, "TTS_UV_CACHE_DIR", str(cache), raising=False)
        monkeypatch.setattr(config, "TTS_RUNTIMES_PATH",
                            str(tmp_path / "runtimes.json"), raising=False)
        provision.reset_jobs()
        env = tmp_path / "envs" / "fish_s2"
        env.mkdir(parents=True)
        provision.uninstall("fish_s2")
        assert not cache.exists(), "gigabytes of wheel cache were left behind"


class TestUvDownloader:
    def _fake_release(self, monkeypatch, tmp_path, *, correct_hash=True):
        import hashlib
        import io
        import zipfile as zf

        buf = io.BytesIO()
        with zf.ZipFile(buf, "w") as z:
            z.writestr("uv.exe", b"fake uv binary")
        payload = buf.getvalue()
        digest = hashlib.sha256(payload).hexdigest()
        monkeypatch.setattr(provision, "UV_SHA256",
                            digest if correct_hash else "0" * 64)
        monkeypatch.setattr(config, "TTS_BIN_DIR", str(tmp_path / "bin"),
                            raising=False)

        class _Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        # Patched at provision's OWN seam, not at urllib's: the downloader
        # goes through _url_opener() so it honours the vault-stored proxy
        # instead of the default (environment-reading) opener.
        class _Opener:
            def open(self, url, timeout=0):
                return _Resp(payload)

        monkeypatch.setattr(provision, "_url_opener", lambda: _Opener())

    def test_a_verified_download_lands_in_our_bin(self, monkeypatch, tmp_path):
        self._fake_release(monkeypatch, tmp_path, correct_hash=True)
        got = provision._download_uv(lambda ln: None)
        assert got and Path(got).read_bytes() == b"fake uv binary"

    def test_a_hash_mismatch_is_refused_never_run(self, monkeypatch, tmp_path):
        """An unverified binary must never be executed - a wrong hash means we
        do not know WHAT we downloaded."""
        self._fake_release(monkeypatch, tmp_path, correct_hash=False)
        assert provision._download_uv(lambda ln: None) is None
        assert not (tmp_path / "bin" / "uv.exe").exists()
