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
import sys
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
