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
    TTS_PROXY_UNAVAILABLE,
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

    def test_fish_pulls_in_its_own_source_tree_because_it_is_not_on_pypi(self):
        """It used to be pinned as an archive REQUIREMENT, to be built into a
        wheel. Measured 8 September 2026: that wheel carries none of the
        modules the worker imports (the project's pyproject lists two
        top-level packages and no subpackages), so the freeze must not name
        it and the engine must declare the tree instead. TestEveryShipped
        FreezeIsExact holds the details; this keeps the plan's own claim."""
        assert "source_tree" in provision.ENGINES["fish_s2"]
        assert not any(line.startswith("fish-speech")
                       for line in provision.requirement_lines("fish_s2"))

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

    def test_the_downloader_goes_through_the_proxy(self, monkeypatch):
        monkeypatch.setattr(provision, "_proxy_url", lambda: "http://proxy:8080")
        client = provision._provision_client()
        try:
            assert client._mounts, "the download would not use the proxy"
        finally:
            client.close()

    def test_with_no_proxy_an_ambient_env_var_is_not_picked_up(self, monkeypatch):
        """Not a client that reads HTTP_PROXY from the process environment,
        which this app deliberately does not trust (every httpx client is
        built with trust_env=False for the same reason)."""
        monkeypatch.setattr(provision, "_proxy_url", lambda: None)
        monkeypatch.setenv("HTTP_PROXY", "http://ambient:9999")
        monkeypatch.setenv("HTTPS_PROXY", "http://ambient:9999")
        client = provision._provision_client()
        try:
            assert client.trust_env is False
            assert not client._mounts, (
                f"the download would go through an ambient proxy: "
                f"{client._mounts}")
        finally:
            client.close()

    def test_a_mandatory_proxy_with_none_configured_refuses_the_install(
        self, voice_dir, monkeypatch,
    ):
        monkeypatch.setattr(provision, "_proxy_required", lambda: True)
        monkeypatch.setattr(provision, "_proxy_url", lambda: None)
        with pytest.raises(provision.ProvisionError) as exc:
            provision.start_install("fish_s2")
        assert exc.value.code == TTS_PROXY_UNAVAILABLE
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
            def raise_for_status(self):
                return None

            def iter_bytes(self, n=None):  # pragma: no cover - not reached
                raise AssertionError("kept downloading after cancel")

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class Client:
            def stream(self, method, url, timeout=None):
                return NeverEndingResponse()

            def close(self):
                return None

        monkeypatch.setattr(provision, "_provision_client", lambda: Client())
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

        # This used to patch `provision._urlopen`, a name that has never
        # existed, with raising=False - so the fake landed on nothing, the
        # real download ran, the suite's egress guard refused the connect, and
        # _download_uv's generic handler turned that into the same `None` the
        # assertions below expect. Both of them passed for the wrong reason
        # and neither the SHA pin nor exploding_copy was ever reached.
        class _Resp:
            def raise_for_status(self):
                return None

            def iter_bytes(self, n=None):
                yield archive_bytes

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class _Client:
            def stream(self, method, url, timeout=0):
                return _Resp()

            def close(self):
                return None

        monkeypatch.setattr(provision, "_provision_client", lambda: _Client())

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

        class _Resp:
            def raise_for_status(self):
                return None

            def iter_bytes(self, n=None):
                yield payload

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        # Patched at provision's OWN seam: the downloader goes through
        # _provision_client(), which asks the egress gate for the proxy and is
        # allowed to be refused by it. Faking httpx one layer lower would step
        # over the gate and the host allowlist both.
        class _Client:
            def stream(self, method, url, timeout=0):
                return _Resp()

            def close(self):
                return None

        monkeypatch.setattr(provision, "_provision_client", lambda: _Client())

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


# ── the freeze goes in with its overrides ───────────────────────────────────
#
# Measured 8 September 2026 with uv 0.11.7: handed to the resolver as a
# request, fish_s2.txt and chatterbox.txt were unsatisfiable (chatterbox-tts
# declares torch 2.6.0 where the file pins 2.8.0, descript-audiotools declares
# protobuf below 3.20 where the file pins 5.29.6), so the one-click install
# had never been able to finish for two of the three engines. The installer
# now passes an overrides file that repeats the freeze's own pins for exactly
# those names, and the resolver checks everything else.

_ALL_ENGINES = sorted(provision.ENGINES)

#: The real fetch, bound before the fixture below replaces the module
#: attribute. TestTheSourceTree exercises it through a fake HTTP client.
_REAL_INSTALL_SOURCE_TREE = provision._install_source_tree
#: Same for the site-packages question, which TestTheSourceTree's staging
#: helper answers for the fake interpreter; one test asks the real one.
_REAL_SITE_PACKAGES = provision._site_packages


@pytest.fixture(autouse=True)
def _no_source_tree_fetch(monkeypatch):
    """Fish installs fetch a source archive after the packages. Every install
    test in this module replaces the subprocess and must not reach the
    network through that step either; the step is stubbed out for all of
    them and tested on its own below."""
    monkeypatch.setattr(provision, "_install_source_tree", lambda *a, **k: None)


def _install_calls(rec: _Recorder) -> list[list[str]]:
    return [c for c in rec.calls if "install" in c]


class TestTheInstallCommand:
    @pytest.mark.parametrize("engine_id", _ALL_ENGINES)
    def test_the_resolver_stays_on_and_the_freeze_is_the_request(
            self, voice_dir, monkeypatch, tmp_path, engine_id):
        _fake_uv(monkeypatch, tmp_path)
        rec = _Recorder()
        monkeypatch.setattr(provision, "_run", rec)
        monkeypatch.setattr(provision, "_verify_env", lambda *a, **k: None)
        monkeypatch.setattr(provision, "_install_source_tree", lambda *a, **k: None)

        provision.start_install(engine_id)
        job = _await(voice_dir, engine_id)

        assert job["state"] == "done", job
        (install,) = _install_calls(rec)
        assert "--no-deps" not in install, "the resolver is the only check on " \
            "a stale pin; switching it off moves that failure to the first load"
        assert install[install.index("-r") + 1].endswith(f"{engine_id}.txt")
        assert "--torch-backend=cu128" in install, "torch would come from PyPI, CPU-only"
        assert "--no-config" in install, "a uv.toml on the machine could name another index"

    @pytest.mark.parametrize("engine_id", _ALL_ENGINES)
    def test_an_engine_with_an_overrides_file_hands_it_over_and_one_without_does_not(
            self, voice_dir, monkeypatch, tmp_path, engine_id):
        _fake_uv(monkeypatch, tmp_path)
        rec = _Recorder()
        monkeypatch.setattr(provision, "_run", rec)
        monkeypatch.setattr(provision, "_verify_env", lambda *a, **k: None)
        monkeypatch.setattr(provision, "_install_source_tree", lambda *a, **k: None)

        provision.start_install(engine_id)
        _await(voice_dir, engine_id)

        (install,) = _install_calls(rec)
        expected = provision.overrides_path(engine_id)
        if expected.is_file():
            assert install[install.index("--overrides") + 1] == str(expected)
        else:
            assert "--overrides" not in install

    @pytest.mark.parametrize("engine_id", _ALL_ENGINES)
    def test_the_source_archive_pins_are_built_with_a_pinned_backend(
            self, voice_dir, monkeypatch, tmp_path, engine_id):
        """Five pins ship as source archives and are built on the machine;
        without this the build backend is fetched at whatever version is
        current that day, the one unpinned step in a pinned install."""
        _fake_uv(monkeypatch, tmp_path)
        rec = _Recorder()
        monkeypatch.setattr(provision, "_run", rec)
        monkeypatch.setattr(provision, "_verify_env", lambda *a, **k: None)
        provision.start_install(engine_id)
        _await(voice_dir, engine_id)
        (install,) = _install_calls(rec)
        assert install[install.index("--build-constraints") + 1] == \
            str(provision.build_constraints_path())
        lines = provision.build_constraint_lines()
        assert lines and all(provision.is_exact_pin(line) for line in lines), lines
        assert any(line.startswith("setuptools==") for line in lines)

    def test_the_shipped_overrides_are_exactly_where_the_resolver_objected(self):
        """Ground for the test above: the files exist for the two engines the
        measurement named, and not for the one that resolved on its own."""
        assert provision.overrides_path("fish_s2").is_file()
        assert provision.overrides_path("chatterbox").is_file()
        assert not provision.overrides_path("xtts_v2").is_file()

    @pytest.mark.parametrize("engine_id", _ALL_ENGINES)
    def test_the_worker_runs_exactly_what_the_verify_script_resolves(
            self, voice_dir, monkeypatch, tmp_path, engine_id):
        """verify_tts_resolve.py dry-runs provision.install_argv. If the
        worker ever composed its own command, the periodic check would be
        checking a command nobody runs."""
        uv = _fake_uv(monkeypatch, tmp_path)
        rec = _Recorder()
        monkeypatch.setattr(provision, "_run", rec)
        monkeypatch.setattr(provision, "_verify_env", lambda *a, **k: None)
        monkeypatch.setattr(provision, "_install_source_tree", lambda *a, **k: None)

        provision.start_install(engine_id)
        _await(voice_dir, engine_id)

        (install,) = _install_calls(rec)
        python = install[install.index("--python") + 1]
        assert install == provision.install_argv(str(uv), python,
                                                 provision.plan(engine_id))

    def test_the_environment_is_created_without_reading_a_config_file(
            self, voice_dir, monkeypatch, tmp_path):
        _fake_uv(monkeypatch, tmp_path)
        rec = _Recorder()
        monkeypatch.setattr(provision, "_run", rec)
        monkeypatch.setattr(provision, "_verify_env", lambda *a, **k: None)
        provision.start_install("xtts_v2")
        _await(voice_dir, "xtts_v2")
        (venv,) = [c for c in rec.calls if "venv" in c]
        assert venv == provision.venv_argv(venv[0], venv[2], provision.PYTHON_VERSION), \
            "the worker composed its own venv command; the verify script uses venv_argv"
        assert "--no-config" in venv
        assert "--managed-python" in venv, "the machine's own Python is not the pin"
        assert "--seed" not in venv, "--seed installs an unpinned pip from the index"
        assert venv[venv.index("--python") + 1] == provision.PYTHON_VERSION
        assert provision.PYTHON_VERSION.count(".") == 2, "a full patch, not a minor"


class TestTheUvVersionGate:
    """A uv older than the pin does not know --torch-backend. The retry that
    used to cover it pointed the resolver at the PyTorch index with
    --extra-index-url, which uv ranks above PyPI for every name that index
    serves, so the same pin could arrive from a different origin than the one
    the freeze was measured against. The gate makes the installer fetch its
    own uv instead."""

    def _machine_uv(self, monkeypatch, tmp_path, version):
        found = tmp_path / "machine-uv.exe"
        found.write_bytes(b"")
        monkeypatch.setattr(config, "TTS_BIN_DIR", str(tmp_path / "bin"), raising=False)
        monkeypatch.setattr(provision, "which_trusted", lambda name: str(found))
        monkeypatch.setattr(provision, "_uv_version", lambda path: version)
        return str(found)

    def test_a_machine_uv_at_the_pin_is_used(self, monkeypatch, tmp_path):
        found = self._machine_uv(monkeypatch, tmp_path,
                                 provision._version_tuple(provision.UV_VERSION))
        assert provision.find_uv() == found

    def test_a_newer_machine_uv_is_passed_over_too(self, monkeypatch, tmp_path):
        """The interpreter manifest and the torch-backend package list live
        inside uv; a newer one can build a different environment from the
        same command. Exactly the pin, or the pinned copy is fetched."""
        major, minor, patch = provision._version_tuple(provision.UV_VERSION)
        self._machine_uv(monkeypatch, tmp_path, (major, minor + 1, 0))
        assert provision.find_uv() is None

    def test_an_older_machine_uv_is_passed_over(self, monkeypatch, tmp_path):
        self._machine_uv(monkeypatch, tmp_path, (0, 4, 30))
        assert provision.find_uv() is None

    def test_a_uv_that_cannot_say_its_version_is_passed_over(self, monkeypatch, tmp_path):
        self._machine_uv(monkeypatch, tmp_path, None)
        assert provision.find_uv() is None

    def test_our_own_copy_is_taken_on_trust_of_its_pin_not_its_version(
            self, monkeypatch, tmp_path):
        """The own copy is the one this module downloaded and checked
        against UV_SHA256; asking it for a version would only re-run it."""
        own_dir = tmp_path / "bin"
        own_dir.mkdir()
        own = own_dir / "uv.exe"
        own.write_bytes(b"")
        monkeypatch.setattr(config, "TTS_BIN_DIR", str(own_dir), raising=False)
        monkeypatch.setattr(provision, "_uv_version",
                            lambda path: pytest.fail("the own copy was version-checked"))
        assert provision.find_uv() == str(own)

    def test_the_version_is_read_from_uv_s_own_answer(self, monkeypatch):
        """Ground for the fakes above: the parser reads the real format, and
        it asks through _run, the runner that strips the launch token, hides
        the window and kills the tree at the deadline."""
        asked = []

        def _fake_run(argv, *, on_line, cancel, timeout, env=None):
            asked.append(list(argv))
            on_line("uv 0.11.7 (9d177269e 2026-04-15 x86_64-pc-windows-msvc)")
            return 0, ""
        monkeypatch.setattr(provision, "_run", _fake_run)
        assert provision._uv_version("anything") == (0, 11, 7)
        assert asked == [["anything", "--version"]]

        def _odd_run(argv, *, on_line, cancel, timeout, env=None):
            on_line("not uv at all")
            return 0, ""
        monkeypatch.setattr(provision, "_run", _odd_run)
        assert provision._uv_version("anything") is None
        monkeypatch.setattr(provision, "_run", lambda *a, **k: (1, "timed out"))
        assert provision._uv_version("anything") is None

    def test_the_gate_reaches_the_worker_as_a_download(self, voice_dir, monkeypatch,
                                                       tmp_path):
        """The whole point, end to end: an old machine uv means the pinned
        copy is fetched, and the job log says so before it starts."""
        self._machine_uv(monkeypatch, tmp_path, (0, 4, 30))
        fetched = []

        def _fake_download(on_line, cancel=None):
            fetched.append(True)
            on_line("uv ready")
            return str(tmp_path / "bin" / "uv.exe")
        monkeypatch.setattr(provision, "_download_uv", _fake_download)
        monkeypatch.setattr(provision, "_run", _Recorder())
        monkeypatch.setattr(provision, "_verify_env", lambda *a, **k: None)
        provision.start_install("xtts_v2")
        job = _await(voice_dir, "xtts_v2")
        assert fetched == [True]
        assert any("fetching the pinned copy" in line for line in job["log"])


class TestEveryShippedFreezeIsExact:
    """The overrides make the resolver believe the freeze. That is only
    honest while nothing in a freeze can float and every override repeats a
    line the freeze already has."""

    @pytest.mark.parametrize("engine_id", _ALL_ENGINES)
    def test_every_line_is_an_exact_pin(self, engine_id):
        lines = provision.requirement_lines(engine_id)
        assert len(lines) > 50, "a freeze this short is not a freeze"
        loose = [line for line in lines if not provision.is_exact_pin(line)]
        assert loose == [], f"{engine_id}: these can float: {loose}"

    @pytest.mark.parametrize("engine_id", _ALL_ENGINES)
    def test_every_override_repeats_the_freeze_verbatim(self, engine_id):
        freeze = set(provision.requirement_lines(engine_id))
        for line in provision.override_lines(engine_id):
            assert provision.is_exact_pin(line), line
            assert line in freeze, (
                f"{engine_id}: override {line!r} is not a line of the freeze, "
                f"so it would introduce a version nothing measured")

    def test_an_override_that_is_not_in_the_freeze_is_caught(self, tmp_path, monkeypatch):
        """Positive control for the test above."""
        req = tmp_path / "x.txt"
        req.write_text("numpy==1.26.4\n", encoding="utf-8")
        ov = tmp_path / "x.overrides.txt"
        ov.write_text("numpy==1.26.5\n", encoding="utf-8")
        monkeypatch.setattr(provision, "requirements_path", lambda e: req)
        monkeypatch.setattr(provision, "overrides_path", lambda e: ov)
        freeze = set(provision.requirement_lines("x"))
        assert [line for line in provision.override_lines("x") if line not in freeze] \
            == ["numpy==1.26.5"]

    def test_the_classifier_refuses_what_can_float(self):
        """Ground for the pin test: the classifier must be able to say no,
        or holding every file to it proves nothing."""
        commit = "e5e292632cb11e7a27b2b7487f58f612bc101e13"
        sha = "0" * 64
        accepted = [
            "torch==2.11.0+cu128",
            "python-dateutil==2.9.0.post0",
            "einx[torch]==0.2.2",
            f"torchao @ https://files.pythonhosted.org/p/torchao-0.17.0-py3-none-any.whl#sha256={sha}",
        ]
        refused = [
            # a commit id is not a content pin: GitHub has re-packed commit
            # archives before, and any host can put forty hex digits in a URL
            f"fish-speech @ https://github.com/fishaudio/fish-speech/archive/{commit}.tar.gz",
            f"evil @ https://evil.example/dl/{commit}.tar.gz",
            "torch",
            "torch>=2.0",
            "torch==2.*",
            "torch==2.8.0,<3",
            'torch==2.11.0+cu128 ; sys_platform == "win32"',
            "fish-speech @ https://github.com/fishaudio/fish-speech/archive/refs/heads/main.tar.gz",
            "fish-speech @ https://github.com/fishaudio/fish-speech/archive/v2.0.0.tar.gz",
            "torchao @ https://files.pythonhosted.org/p/torchao-0.17.0-py3-none-any.whl",
            f"torchao @ http://files.pythonhosted.org/p/t.whl#sha256={sha}",
            "-e .",
        ]
        assert [line for line in accepted if not provision.is_exact_pin(line)] == []
        assert [line for line in refused if provision.is_exact_pin(line)] == []

    def test_requirement_lines_keep_a_hash_fragment_intact(self, tmp_path, monkeypatch):
        """A `#sha256=` fragment is not a comment. Stripping from the first
        `#` would turn a hash-pinned line into a bare URL and the classifier
        above would then refuse a line that is in fact the strictest kind."""
        sha = "1" * 64
        body = (
            "# a comment\n"
            "\n"
            "   # an indented comment\n"
            "numpy==1.26.4\n"
            f"torchao @ https://files.pythonhosted.org/p/t.whl#sha256={sha}\n"
        )
        req = tmp_path / "x.txt"
        req.write_text(body, encoding="utf-8")
        monkeypatch.setattr(provision, "requirements_path", lambda engine_id: req)

        assert provision.requirement_lines("x") == [
            "numpy==1.26.4",
            f"torchao @ https://files.pythonhosted.org/p/t.whl#sha256={sha}",
        ]

    def test_fish_takes_torchao_from_pypi_by_hash_not_from_the_cuda_index(self):
        """--torch-backend=cu128 routes torchao to the CUDA index, which has no
        Windows wheel for 0.17.0. The measured environment runs the pure
        wheel from PyPI, and only a direct URL keeps the flag from redirecting
        it. Measured: as a plain `torchao==0.17.0` pin the install is
        unsatisfiable."""
        lines = provision.requirement_lines("fish_s2")
        torchao = [line for line in lines if line.startswith("torchao")]
        assert len(torchao) == 1, torchao
        (line,) = torchao
        assert line.startswith("torchao @ https://files.pythonhosted.org/")
        assert "torchao-0.17.0-py3-none-any.whl#sha256=" in line

    def test_fish_speech_is_a_source_tree_not_a_requirement(self):
        """A wheel built from the archive has no subpackages (the project's
        pyproject lists two top-level packages and nothing under them), so
        the freeze must not name it and the engine must declare the tree."""
        assert not any(line.startswith("fish-speech")
                       for line in provision.requirement_lines("fish_s2"))
        tree = provision.ENGINES["fish_s2"]["source_tree"]
        assert tree["url"].startswith("https://github.com/fishaudio/fish-speech/archive/")
        assert len(tree["tree_sha256"]) == 64
        assert tree["dir"] and tree["pth"].endswith(".pth")


# ── the source tree ─────────────────────────────────────────────────────────

def _archive_bytes(files: dict[str, bytes], top: str = "fish-speech-abc") -> bytes:
    import io
    import tarfile as tf

    buf = io.BytesIO()
    with tf.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            info = tf.TarInfo(f"{top}/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _fake_archive_client(monkeypatch, payload: bytes):
    class _Resp:
        def raise_for_status(self):
            return None

        def iter_bytes(self, n=None):
            for i in range(0, len(payload), 1024):
                yield payload[i:i + 1024]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Client:
        def stream(self, method, url, timeout=0):
            return _Resp()

        def close(self):
            return None

    monkeypatch.setattr(provision, "_provision_client", lambda: _Client())


def _digest_of(files: dict[str, bytes], tmp_path) -> str:
    root = tmp_path / "digest-me"
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return provision._tree_digest(root)


_TREE = {
    ".project-root": b"",
    "fish_speech/models/dac/inference.py": b"x = 1\n",
    "configs/modded_dac_vq.yaml": b"sample_rate: 44100\n",
}


class TestTheSourceTree:
    def _staging(self, tmp_path, monkeypatch):
        staging = tmp_path / "envs" / "fish_s2.staging"
        site = staging / "Lib" / "site-packages"
        site.mkdir(parents=True)
        python = staging / "Scripts" / "python.exe"
        python.parent.mkdir(parents=True)
        python.write_bytes(b"")
        # The interpreter is asked where its site-packages is; answer for it.
        monkeypatch.setattr(provision, "_site_packages", lambda *a, **k: site)
        return staging, python, site

    def _spec(self, digest, **extra):
        return {"url": "https://github.com/x/y/archive/abc.tar.gz", "dir": "fish-speech",
                "pth": "elysium-fish-speech.pth", "max_bytes": 1024 * 1024,
                "tree_sha256": digest, **extra}

    def test_a_verified_tree_lands_under_the_environment_with_one_pth_line(
            self, tmp_path, monkeypatch):
        staging, python, site = self._staging(tmp_path, monkeypatch)
        _fake_archive_client(monkeypatch, _archive_bytes(_TREE))
        lines = []
        _REAL_INSTALL_SOURCE_TREE(self._spec(_digest_of(_TREE, tmp_path)),
                                       staging, python, lines.append, threading.Event())
        dest = staging / "src" / "fish-speech"
        assert (dest / "fish_speech" / "models" / "dac" / "inference.py").read_bytes() == b"x = 1\n"
        assert (dest / ".project-root").is_file()
        # What the .pth DOES, through site itself, AFTER the environment is
        # renamed into place the way staging is: processed from the moved
        # site-packages, it puts the moved tree on the path. An absolute line
        # would still name the staging folder here, which is the measured
        # failure of 8 September 2026.
        final = staging.with_name("fish_s2")
        os.replace(str(staging), str(final))
        final_site = final / site.relative_to(staging)
        final_dest = final / "src" / "fish-speech"
        monkeypatch.setattr(sys, "path", list(sys.path))
        import site as site_mod
        site_mod.addsitedir(str(final_site))
        on_path = {os.path.normcase(p) for p in sys.path}
        assert os.path.normcase(str(final_dest)) in on_path
        assert os.path.normcase(str(dest)) not in on_path, "the staging name is gone"
        # nothing left behind but the tree
        assert sorted(p.name for p in (final / "src").iterdir()) == ["fish-speech"]

    def test_a_tree_that_does_not_match_its_pin_is_removed_and_refused(
            self, tmp_path, monkeypatch):
        staging, python, site = self._staging(tmp_path, monkeypatch)
        tampered = {**_TREE, "fish_speech/models/dac/inference.py": b"import os; os.system('x')\n"}
        _fake_archive_client(monkeypatch, _archive_bytes(tampered))
        with pytest.raises(provision.ProvisionError) as info:
            _REAL_INSTALL_SOURCE_TREE(self._spec(_digest_of(_TREE, tmp_path)),
                                           staging, python, lambda ln: None, threading.Event())
        assert info.value.code == TTS_RUNTIME_INSTALL_FAILED
        assert "checksum" in info.value.detail
        assert not (staging / "src" / "fish-speech").exists()
        assert not (site / "elysium-fish-speech.pth").exists()
        assert list((staging / "src").iterdir()) == []

    def test_the_pin_is_over_content_not_over_the_archive_bytes(self, tmp_path, monkeypatch):
        """The same files packed twice, differently, must pass the same pin.
        GitHub has re-packed unchanged commits before."""
        staging, python, site = self._staging(tmp_path, monkeypatch)
        import gzip
        import io
        import tarfile as tf
        buf = io.BytesIO()
        raw = io.BytesIO()
        with tf.open(fileobj=raw, mode="w") as tar:
            for name, data in reversed(list(_TREE.items())):
                info = tf.TarInfo(f"fish-speech-abc/{name}")
                info.size = len(data)
                info.mtime = 1234567890
                tar.addfile(info, io.BytesIO(data))
        with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=1, mtime=0) as gz:
            gz.write(raw.getvalue())
        repacked = buf.getvalue()
        assert repacked != _archive_bytes(_TREE)
        _fake_archive_client(monkeypatch, repacked)
        _REAL_INSTALL_SOURCE_TREE(self._spec(_digest_of(_TREE, tmp_path)),
                                       staging, python, lambda ln: None, threading.Event())
        assert (staging / "src" / "fish-speech" / ".project-root").is_file()

    def test_an_archive_over_the_cap_is_cut_off_and_refused(self, tmp_path, monkeypatch):
        staging, python, site = self._staging(tmp_path, monkeypatch)
        # Random bytes: gzip cannot shrink them, so the archive really is
        # over the cap (the next test is the compressible case).
        big = {**_TREE, "big.bin": os.urandom(2 * 1024 * 1024)}
        _fake_archive_client(monkeypatch, _archive_bytes(big))
        with pytest.raises(provision.ProvisionError) as info:
            _REAL_INSTALL_SOURCE_TREE(self._spec(_digest_of(big, tmp_path)),
                                      staging, python, lambda ln: None, threading.Event())
        assert "larger" in info.value.detail
        assert not (staging / "src" / "fish-speech").exists()
        assert list((staging / "src").iterdir()) == []

    def test_a_small_archive_that_unpacks_past_the_cap_is_refused_before_unpacking(
            self, tmp_path, monkeypatch):
        staging, python, site = self._staging(tmp_path, monkeypatch)
        zeros = {**_TREE, "big.bin": b"\0" * (2 * 1024 * 1024)}
        payload = _archive_bytes(zeros)
        assert len(payload) < 64 * 1024, "ground: the archive itself is tiny"
        _fake_archive_client(monkeypatch, payload)
        with pytest.raises(provision.ProvisionError) as info:
            _REAL_INSTALL_SOURCE_TREE(self._spec(_digest_of(zeros, tmp_path)),
                                      staging, python, lambda ln: None, threading.Event())
        assert "unpacks" in info.value.detail
        assert not (staging / "src" / "fish-speech").exists()
        assert list((staging / "src").iterdir()) == []

    def test_an_archive_with_a_parent_reference_is_refused_and_writes_nothing_outside(
            self, tmp_path, monkeypatch):
        staging, python, site = self._staging(tmp_path, monkeypatch)
        import io
        import tarfile as tf
        buf = io.BytesIO()
        with tf.open(fileobj=buf, mode="w:gz") as tar:
            info = tf.TarInfo("fish-speech-abc/../../escaped.txt")
            data = b"out"
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        _fake_archive_client(monkeypatch, buf.getvalue())
        with pytest.raises(provision.ProvisionError) as info:
            _REAL_INSTALL_SOURCE_TREE(self._spec("0" * 64), staging, python,
                                      lambda ln: None, threading.Event())
        # The filter's refusal, not a later complaint about the tree's shape.
        assert "could not be unpacked" in info.value.detail
        # Where `fish-speech-abc/../../escaped.txt` lands from the extract
        # folder under staging/src: two levels up is staging/src itself.
        assert not (staging / "src" / "escaped.txt").exists()
        assert not (staging / "escaped.txt").exists()
        assert not (tmp_path / "escaped.txt").exists()
        assert list((staging / "src").iterdir()) == []

    def test_an_archive_with_too_many_members_is_refused_before_unpacking(
            self, tmp_path, monkeypatch):
        staging, python, site = self._staging(tmp_path, monkeypatch)
        many = {f"f/{i}.txt": b"" for i in range(provision.MAX_TREE_MEMBERS + 1)}
        _fake_archive_client(monkeypatch, _archive_bytes(many))
        with pytest.raises(provision.ProvisionError) as info:
            _REAL_INSTALL_SOURCE_TREE(self._spec("0" * 64), staging, python,
                                      lambda ln: None, threading.Event())
        assert "unpacks to more" in info.value.detail
        assert list((staging / "src").iterdir()) == []

    def test_site_packages_is_read_back_intact_under_a_non_ascii_profile_name(
            self, tmp_path):
        """Under -I the interpreter ignores PYTHONUTF8 and prints in the ANSI
        code page while _run decodes UTF-8, so a profile name like Ömer came
        back with a replacement character and named a folder that does not
        exist. Measured 8 September 2026. A real interpreter, a real path."""
        import subprocess
        import venv as venv_mod
        root = tmp_path / "Ömer" / "env"
        venv_mod.EnvBuilder(with_pip=False, symlinks=False).create(str(root))
        python = root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        expected = Path(subprocess.run(
            [str(python), "-X", "utf8", "-c",
             "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
            capture_output=True, text=True, encoding="utf-8").stdout.strip())
        assert "Ömer" in str(expected), "ground: the name is in the path"
        got = _REAL_SITE_PACKAGES(python, lambda ln: None, threading.Event())
        assert got == expected
        assert got.is_dir()

    def test_a_cancel_while_asking_for_site_packages_is_a_cancel_not_a_failure(
            self, tmp_path, monkeypatch):
        staging, python, site = self._staging(tmp_path, monkeypatch)
        cancel = threading.Event()

        def _run_then_cancel(argv, *, on_line, cancel, timeout, env=None):
            cancel.set()
            return 1, "cancelled"
        monkeypatch.setattr(provision, "_run", _run_then_cancel)
        with pytest.raises(provision._Cancelled):
            _REAL_SITE_PACKAGES(python, lambda ln: None, cancel)
        # ground: the same refusal without a cancel is the install failure
        monkeypatch.setattr(provision, "_run", lambda *a, **k: (1, "broken"))
        with pytest.raises(provision.ProvisionError):
            _REAL_SITE_PACKAGES(python, lambda ln: None, threading.Event())

    def test_cancel_stops_the_download_and_leaves_nothing(self, tmp_path, monkeypatch):
        staging, python, site = self._staging(tmp_path, monkeypatch)
        _fake_archive_client(monkeypatch, _archive_bytes(_TREE))
        cancel = threading.Event()
        cancel.set()
        with pytest.raises(provision._Cancelled):
            _REAL_INSTALL_SOURCE_TREE(self._spec(_digest_of(_TREE, tmp_path)),
                                           staging, python, lambda ln: None, cancel)
        assert list((staging / "src").iterdir()) == []

    def test_a_refused_proxy_is_reported_as_the_proxy_not_the_machine(
            self, tmp_path, monkeypatch):
        staging, python, site = self._staging(tmp_path, monkeypatch)

        def _refuse():
            raise provision.ProxyUnreadable("a proxy is required but none is available")
        monkeypatch.setattr(provision, "_provision_client", _refuse)
        with pytest.raises(provision.ProvisionError) as info:
            _REAL_INSTALL_SOURCE_TREE(self._spec("0" * 64), staging, python,
                                      lambda ln: None, threading.Event())
        assert info.value.code == TTS_PROXY_UNAVAILABLE

    def test_the_worker_fetches_the_tree_after_the_packages_and_before_the_proof(
            self, voice_dir, monkeypatch, tmp_path):
        _fake_uv(monkeypatch, tmp_path)
        order = []
        rec = _Recorder()

        def _run(argv, **kw):
            if "install" in argv:
                order.append("packages")
            return rec(argv, **kw)
        monkeypatch.setattr(provision, "_run", _run)
        monkeypatch.setattr(provision, "_install_source_tree",
                            lambda *a, **k: order.append("tree"))
        monkeypatch.setattr(provision, "_verify_env",
                            lambda *a, **k: order.append("verify"))
        provision.start_install("fish_s2")
        job = _await(voice_dir, "fish_s2")
        assert job["state"] == "done", job
        assert order == ["packages", "tree", "verify"]

    def test_an_engine_without_a_tree_never_fetches_one(self, voice_dir, monkeypatch,
                                                       tmp_path):
        _fake_uv(monkeypatch, tmp_path)
        monkeypatch.setattr(provision, "_run", _Recorder())
        monkeypatch.setattr(provision, "_verify_env", lambda *a, **k: None)
        monkeypatch.setattr(provision, "_install_source_tree",
                            lambda *a, **k: pytest.fail("xtts has no source tree"))
        provision.start_install("xtts_v2")
        assert _await(voice_dir, "xtts_v2")["state"] == "done"


class TestTheVerifyScriptRunsTheInstallersCommands:
    """verify_tts_resolve.py is the periodic check on the freezes. It has to
    run the installer's own two commands, or it checks something nobody
    runs. Loaded by path: verify/ is a folder of scripts, not a package."""

    def _script(self):
        import importlib.util
        path = Path(provision.__file__).resolve().parents[1] / "verify" / "verify_tts_resolve.py"
        spec = importlib.util.spec_from_file_location("verify_tts_resolve_probe", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_the_dry_run_is_venv_argv_then_install_argv_plus_dry_run(self, monkeypatch,
                                                                     tmp_path):
        script = self._script()
        ran: list[list[str]] = []

        class _Done:
            returncode = 0
            stdout = f"Would install {len(provision.requirement_lines('xtts_v2'))} packages\n"
            stderr = ""

        def _fake_run(argv, **kw):
            ran.append(list(argv))
            return _Done()
        monkeypatch.setattr(script.subprocess, "run", _fake_run)
        ok, summary = script.resolve_one("uv.exe", "xtts_v2", tmp_path, {})
        assert ok, summary
        plan = provision.plan("xtts_v2")
        venv = tmp_path / "xtts_v2"
        python = venv / ("Scripts/python.exe" if provision.IS_WINDOWS else "bin/python")
        assert ran == [
            provision.venv_argv("uv.exe", venv, plan.python_version),
            provision.install_argv("uv.exe", python, plan) + ["--dry-run"],
        ]

    def test_a_count_that_does_not_match_the_freeze_is_a_failure(self, monkeypatch,
                                                                 tmp_path):
        """A transitive dependency missing from the freeze would resolve to
        the newest of the day and the install would still pass."""
        script = self._script()

        class _Done:
            returncode = 0
            stdout = f"Would install {len(provision.requirement_lines('xtts_v2')) + 1} packages\n"
            stderr = ""
        monkeypatch.setattr(script.subprocess, "run", lambda argv, **kw: _Done())
        ok, summary = script.resolve_one("uv.exe", "xtts_v2", tmp_path, {})
        assert not ok
        listed = len(provision.requirement_lines("xtts_v2"))
        assert str(listed + 1) in summary and str(listed) in summary

    def test_the_script_strips_what_the_installer_strips(self, monkeypatch, tmp_path):
        script = self._script()
        monkeypatch.setenv("UV_INDEX_URL", "https://evil.example/simple")
        monkeypatch.setattr(script.provision, "find_uv", lambda: "uv.exe")

        class _Scratch:                      # the real one writes under %TEMP%
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return str(tmp_path)

            def __exit__(self, *a):
                return False
        monkeypatch.setattr(script.tempfile, "TemporaryDirectory", _Scratch)
        seen = {}

        def _fake_resolve(uv, engine_id, workdir, env):
            seen.update(env)
            return True, "ok"
        monkeypatch.setattr(script, "resolve_one", _fake_resolve)
        assert script.main(["xtts_v2"]) == 0
        assert "UV_INDEX_URL" not in seen
        assert seen["UV_HTTP_TIMEOUT"] == "600"     # positive control


class TestTheTreeDigest:
    def test_timestamps_and_order_do_not_change_it_but_one_byte_does(self, tmp_path):
        a = _digest_of(_TREE, tmp_path / "a")
        b = _digest_of(dict(reversed(list(_TREE.items()))), tmp_path / "b")
        assert a == b
        import os
        for f in (tmp_path / "a" / "digest-me").rglob("*"):
            if f.is_file():
                os.utime(f, (1, 1))
        assert provision._tree_digest(tmp_path / "a" / "digest-me") == a
        c = _digest_of({**_TREE, "configs/modded_dac_vq.yaml": b"sample_rate: 44101\n"},
                       tmp_path / "c")
        assert c != a

    def test_a_renamed_file_changes_it(self, tmp_path):
        a = _digest_of(_TREE, tmp_path / "a")
        moved = dict(_TREE)
        moved["fish_speech/models/dac/inferences.py"] = moved.pop(
            "fish_speech/models/dac/inference.py")
        assert _digest_of(moved, tmp_path / "b") != a

    def test_the_order_is_the_posix_path_as_bytes_on_every_platform(self, tmp_path):
        """The recipe, written out: path, NUL, size, NUL, bytes, NUL, in
        posix path string order. Sorting Path objects would fold case on
        Windows and give this machine a digest no other platform reproduces:
        here `LICENSE` sorts before `fish_speech`, as bytes do."""
        import hashlib
        files = {"fish_speech/x.py": b"1", "LICENSE": b"2", "README.md": b"3"}
        digest = hashlib.sha256()
        for rel in sorted(files):                      # str order == byte order here
            digest.update(rel.encode() + b"\0" + str(len(files[rel])).encode() + b"\0")
            digest.update(files[rel] + b"\0")
        assert _digest_of(files, tmp_path) == digest.hexdigest()
        assert sorted(files) == ["LICENSE", "README.md", "fish_speech/x.py"]


class TestNothingInTheEnvironmentRedirectsTheChild:
    """The child uv reads its whole configuration from the environment as
    well as the command line. Every name that can change what a pin means,
    where a download comes from, or whether a certificate is checked is
    taken away before the child starts. Measured 8 September 2026: the TLS
    names were not on the list."""

    #: The test's own reference list, not an import of the constant: a name
    #: deleted from the constant must go red here, which an import could not.
    NAMES = (
        "NO_PROXY", "no_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
        "http_proxy", "https_proxy", "all_proxy",
        "UV_INDEX", "UV_INDEX_URL", "UV_EXTRA_INDEX_URL", "UV_DEFAULT_INDEX",
        "UV_FIND_LINKS", "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL",
        "PIP_TRUSTED_HOST", "PIP_FIND_LINKS", "PIP_CONFIG_FILE",
        "UV_OVERRIDE", "UV_CONSTRAINT", "UV_BUILD_CONSTRAINT", "UV_EXCLUDE",
        "UV_INDEX_STRATEGY", "UV_TORCH_BACKEND", "UV_NO_BUILD_ISOLATION",
        "UV_NO_INDEX", "UV_CONFIG_FILE", "UV_PYTHON_INSTALL_MIRROR",
        "UV_PYPY_INSTALL_MIRROR",
        "UV_INSECURE_HOST", "SSL_CERT_FILE", "SSL_CLIENT_CERT", "UV_NATIVE_TLS",
        "UV_NO_VERIFY_HASHES", "UV_KEYRING_PROVIDER",
        "UV_PYTHON", "UV_MANAGED_PYTHON", "UV_NO_MANAGED_PYTHON",
        "UV_PYTHON_DOWNLOADS", "UV_PYTHON_PREFERENCE",
        "UV_PYTHON_DOWNLOADS_JSON_URL", "UV_PYTHON_CPYTHON_BUILD", "UV_PYTHON_CACHE_DIR",
        "UV_SYSTEM_CERTS", "SSL_CERT_DIR", "UV_NO_BINARY", "UV_NO_BINARY_PACKAGE",
        "UV_VENV_SEED", "UV_VENV_CLEAR", "UV_VENV_RELOCATABLE",
        "UV_EXCLUDE_NEWER", "UV_RESOLUTION", "UV_PRERELEASE", "UV_FORK_STRATEGY",
        "UV_INSECURE_NO_ZIP_VALIDATION", "UV_SKIP_WHEEL_FILENAME_CHECK",
        "UV_NO_CACHE", "UV_LINK_MODE",
    )

    def test_every_redirect_the_child_would_read_is_taken_away(
            self, voice_dir, monkeypatch, tmp_path):
        for name in self.NAMES:
            monkeypatch.setenv(name, "planted")
        monkeypatch.setenv("UV_HTTP_TIMEOUT", "1")     # positive control below
        _fake_uv(monkeypatch, tmp_path)
        seen: list[dict] = []

        class _EnvRecorder(_Recorder):
            def __call__(self, argv, *, on_line, cancel, timeout, env=None):
                seen.append(dict(env or {}))
                return super().__call__(argv, on_line=on_line, cancel=cancel,
                                        timeout=timeout, env=env)
        monkeypatch.setattr(provision, "_run", _EnvRecorder())
        monkeypatch.setattr(provision, "_verify_env", lambda *a, **k: None)
        monkeypatch.setattr(provision, "_proxy_required", lambda: False)
        monkeypatch.setattr(provision, "_read_proxy", lambda: None)
        provision.start_install("xtts_v2")
        assert _await(voice_dir, "xtts_v2")["state"] == "done"
        children = [e for e in seen if "UV_CACHE_DIR" in e]
        assert children, "no uv child was started"
        for env in children:
            for name in self.NAMES:
                assert name in env and env[name] is None, f"{name} would reach the child"
            # positive control: the value the installer sets itself survives
            assert env["UV_HTTP_TIMEOUT"] == "600"


# ── the proof names what the workers import ─────────────────────────────────

class TestTheVerifyListsAreTheWorkersImports:
    """`import fish_speech` passes on a bare directory (a namespace package),
    which is how a wheel with none of the worker's modules would have been
    called ready. The lists have to name the modules the workers bind."""

    def test_fish_names_the_two_modules_the_worker_binds_and_the_quantiser(self):
        verify = provision.ENGINES["fish_s2"]["verify"]
        assert "fish_speech.models.dac.inference" in verify
        assert "fish_speech.models.text2semantic.inference" in verify
        assert "torchao.quantization" in verify
        assert "triton" in verify, "torch.compile on Windows needs it; without it fish runs eager"
        assert "fish_speech" not in verify, "the bare name proves nothing, see above"

    def test_each_list_is_asked_of_the_new_interpreter_module_by_module(
            self, voice_dir, monkeypatch, tmp_path):
        _fake_uv(monkeypatch, tmp_path)
        rec = _Recorder()
        monkeypatch.setattr(provision, "_run", rec)
        monkeypatch.setattr(provision, "_install_source_tree", lambda *a, **k: None)
        provision.start_install("fish_s2")
        assert _await(voice_dir, "fish_s2")["state"] == "done"
        asked = [c[-1] for c in rec.calls if len(c) >= 3 and c[1] == "-I" and "-c" in c]
        for module in provision.ENGINES["fish_s2"]["verify"]:
            assert f"import {module}" in asked, module

    @pytest.fixture(scope="class")
    def bare_interpreter(self, tmp_path_factory):
        """A real, empty interpreter of the same version, built once for the
        class: the only honest way to show what `-I -c "import x"` does with
        a namespace package."""
        import subprocess
        import venv as venv_mod
        root = tmp_path_factory.mktemp("bare")
        venv_mod.EnvBuilder(with_pip=False, symlinks=False).create(str(root))
        python = root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        done = subprocess.run([str(python), "-I", "-c",
                               "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                              capture_output=True, text=True)
        return python, Path(done.stdout.strip())

    def test_the_bare_name_passes_on_an_empty_directory_and_the_real_import_fails(
            self, bare_interpreter, monkeypatch):
        python, site = bare_interpreter
        (site / "fish_speech" / "models" / "dac").mkdir(parents=True)
        monkeypatch.setitem(provision.ENGINES, "probe_bare", {"verify": ["fish_speech"]})
        monkeypatch.setitem(provision.ENGINES, "probe_real",
                            {"verify": ["fish_speech.models.dac.inference"]})
        # ground: the bare name is blind
        provision._verify_env("probe_bare", python, lambda ln: None, threading.Event())
        # the real import sees the missing module
        with pytest.raises(provision.ProvisionError) as info:
            provision._verify_env("probe_real", python, lambda ln: None, threading.Event())
        assert "fish_speech.models.dac.inference" in info.value.detail
        # positive control: once the module exists, the real import passes
        (site / "fish_speech" / "models" / "dac" / "inference.py").write_text(
            "x = 1\n", encoding="utf-8")
        provision._verify_env("probe_real", python, lambda ln: None, threading.Event())

    def test_a_relative_pth_line_survives_the_environment_being_renamed(self, tmp_path):
        """The mechanism _install_source_tree relies on, shown on a real
        interpreter under `-I`: one .pth line in site-packages, relative to
        it, and a module under that directory imports. Then the environment
        is renamed the way staging is renamed into place, and it still
        imports. An absolute line is the ground control: it dies with the
        old name, which is what the measurement of 8 September 2026 found."""
        import subprocess
        import venv as venv_mod
        root = tmp_path / "engine.staging"
        venv_mod.EnvBuilder(with_pip=False, symlinks=False).create(str(root))
        python_rel = "Scripts/python.exe" if os.name == "nt" else "bin/python"
        python = root / python_rel
        site = Path(subprocess.run(
            [str(python), "-I", "-c",
             "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
            capture_output=True, text=True).stdout.strip())
        tree = root / "src" / "tree"
        (tree / "fish_speech" / "models").mkdir(parents=True)
        (tree / "fish_speech" / "models" / "probe.py").write_text("ANSWER = 42\n",
                                                                  encoding="utf-8")
        code = "from fish_speech.models.probe import ANSWER; print(ANSWER)"

        def imports(py) -> bool:
            done = subprocess.run([str(py), "-I", "-c", code], capture_output=True, text=True)
            return done.returncode == 0 and done.stdout.strip() == "42"

        assert not imports(python), "ground: not importable before the .pth"
        (site / "elysium-probe.pth").write_text(os.path.relpath(tree, site) + "\n",
                                                encoding="utf-8")
        assert imports(python)
        final = tmp_path / "engine"
        os.replace(str(root), str(final))
        assert imports(final / python_rel), "the relative line moved with the environment"
        # ground control: the absolute form is exactly what broke
        final_site = final / site.relative_to(root)
        (final_site / "elysium-probe.pth").write_text(str(tree) + "\n", encoding="utf-8")
        assert not imports(final / python_rel)
