"""Regressions for the V3 full audit - each test is one confirmed finding.

The audit's method was to describe, concretely, how a user ends up with a
machine that needs a reboot, a conversation left audible on disk, or advice
that cannot work. These tests pin the fixes to those exact scenarios, so a
refactor that quietly reintroduces one fails with the story attached.
"""
import sys
import threading
import time
from pathlib import Path

import pytest

import config
from tts import host as tts_host
from tts import provision, runtimes, vram
from tts.base import DetectedModel
from tts.errors import (
    TTS_OUT_OF_MEMORY,
    TTS_REFERENCE_INVALID,
    TTS_RUNTIME_BROKEN,
    TTS_WORKER_CRASHED,
    TTS_WORKER_FAILED,
    TTS_WORKER_UNAVAILABLE,
)
from tts.worker import _wire
from tts.worker_client import WorkerClient, WorkerFailure

FAKE = str(Path(__file__).resolve().parent / "fake_worker.py")


def _fake_smi(monkeypatch, *, free=14000):
    monkeypatch.setattr(
        vram, "_run_smi",
        lambda: "NVIDIA GeForce RTX 5080, 16303, %d, 2303\n" % free)


def _model(**kw):
    base = dict(uid="uid1", engine_id="fish_s2", name="s2-pro", path="/models/s2-pro")
    base.update(kw)
    return DetectedModel(**base)


@pytest.fixture
def host(monkeypatch, tmp_path):
    reg = tmp_path / "voice" / "runtimes.json"
    reg.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "TTS_RUNTIMES_PATH", str(reg), raising=False)
    monkeypatch.setattr(config, "TTS_CACHE_DIR", str(tmp_path / "cache"), raising=False)
    runtimes.register("fish_s2", sys.executable)
    _fake_smi(monkeypatch)
    h = tts_host.VoiceHost()
    h.script_resolver = lambda engine_id: FAKE
    yield h
    h.unload("teardown")


def _track_clients(monkeypatch):
    created = []
    real = tts_host.WorkerClient

    def tracking(*a, **kw):
        c = real(*a, **kw)
        created.append(c)
        return c

    monkeypatch.setattr(tts_host, "WorkerClient", tracking)
    return created


class TestFailedLoadLeaksNothing:
    def test_an_engine_error_during_load_ends_the_worker_process(
        self, host, monkeypatch
    ):
        """The audit's repro: three failed loads left three live workers, each
        invisible to unload/lock/shutdown, each holding VRAM. The client is
        local until published, so every failure path must end it."""
        created = _track_clients(monkeypatch)
        for _ in range(3):
            with pytest.raises(WorkerFailure):
                host.load(_model(), {"__fake_mode": "coded"})
        assert len(created) == 3
        deadline = time.time() + 10
        while time.time() < deadline and any(c.alive for c in created):
            time.sleep(0.1)
        assert not any(c.alive for c in created), (
            "a failed load left a worker running with no reference to it")


class TestTeardownRacingALoad:
    def test_an_unload_during_the_load_round_trip_wins(self, host, monkeypatch):
        """The audit scenario: lock the vault while a model loads. The load
        finishes AFTER the teardown - it must not publish a live worker into
        an app that already let go."""
        created = _track_clients(monkeypatch)
        results = {}

        def slow_load():
            try:
                host.load(_model(), {"__fake_mode": "slow", "secs": 2.0})
                results["outcome"] = "loaded"
            except WorkerFailure as exc:
                results["outcome"] = exc.code

        t = threading.Thread(target=slow_load, daemon=True)
        t.start()
        deadline = time.time() + 10
        while time.time() < deadline and not created:
            time.sleep(0.05)
        time.sleep(0.3)                  # the OP_LOAD round trip is in flight
        host.unload("vault locked mid-load")
        t.join(timeout=30)

        assert results["outcome"] == TTS_WORKER_UNAVAILABLE
        snap = host.snapshot()
        assert snap["state"] == "unloaded", "the aborted load resurrected itself"
        deadline = time.time() + 10
        while time.time() < deadline and any(c.alive for c in created):
            time.sleep(0.1)
        assert not any(c.alive for c in created)


class TestNothingOutlivesTheSession:
    def test_process_teardown_wipes_the_audio_cache(self, host):
        """Closing the window is the NORMAL way the app ends, and it does not
        go through the vault. The conversation must not stay audible on disk
        because the exit was the X button instead of the lock."""
        host.load(_model(), {})
        host.speak("something private", {})
        cache = Path(config.TTS_CACHE_DIR)
        assert list(cache.glob("*.wav"))
        host._teardown(grace=0.2)
        assert not list(cache.glob("*.wav"))

    def test_plain_unload_keeps_audio_that_may_still_be_playing(self, host):
        """VRAM and privacy have different lifetimes.

        This used to assert the opposite, and the reaper that once lived here
        came through unload() - so a model that
        unloaded while somebody was listening deleted the wav mid-playback: the
        browser's in-flight request for the rest of the file failed and the
        sentence stopped mid-word, with nothing to explain it.

        Nothing about the promise changed. It is kept at the moments the
        SESSION ends - the vault lock and teardown cases above and below this
        one, both still asserted. An idle GPU is not one of those moments.
        """
        host.load(_model(), {})
        host.speak("also private", {})
        host.unload("user asked")
        assert list(Path(config.TTS_CACHE_DIR).glob("*.wav"))

    def test_locking_the_vault_still_wipes(self, host):
        host.load(_model(), {})
        host.speak("also private", {})
        host.on_vault_locked()
        assert not list(Path(config.TTS_CACHE_DIR).glob("*.wav"))

    def test_unload_dismisses_the_last_error(self, host):
        with pytest.raises(WorkerFailure):
            host.load(_model(), {"__fake_mode": "oom"})
        assert host.snapshot()["error_code"] == TTS_OUT_OF_MEMORY
        host.unload("dismissed")
        assert host.snapshot()["error_code"] is None


class TestCrashHousekeeping:
    def test_a_crashed_workers_client_is_fully_closed(self, host):
        """Noticing the death is not enough: the dead worker's client still
        holds the job handle, a blocked stdin writer and three pipes."""
        host.load(_model(), {})
        client = host._client
        client._proc.kill()
        deadline = time.time() + 15
        while time.time() < deadline and host.snapshot()["state"] != "error":
            host.poll_health()
            time.sleep(0.1)
        assert client._job._handle is None, "the job handle was leaked"

    def test_the_host_polls_itself_without_any_ui(self, host, monkeypatch):
        """A minimised window polls nothing. A dead worker must be noticed anyway.

        This used to watch the idle reaper reclaim VRAM. The reaper is gone -
        the vault lock replaced it - so the observable effect is now the other
        thing the beat exists for: a worker that dies on its own has to be
        reported without anybody looking at the UI, or the app sits there
        claiming a model is loaded that is not.

        The beat is one MODULE-LEVEL thread over whichever host is current
        (audit-2 killed the per-instance while-True threads), so the test's
        host must BE the current one - exactly what the app does via get_host.
        """
        from tts import host as tts_host_module

        monkeypatch.setattr(config, "TTS_HEALTH_POLL_S", 0.1, raising=False)
        monkeypatch.setattr(tts_host_module, "_HOST", host, raising=False)
        host.load(_model(), {})
        client = host._client
        client._proc.kill()
        deadline = time.time() + 15
        while time.time() < deadline and host.snapshot()["state"] != "error":
            time.sleep(0.1)              # NOTE: no poll_health() calls here
        assert host.snapshot()["state"] == "error"
        assert not client.alive


class TestDeathDiagnosis:
    def test_a_worker_that_dies_before_hello_reports_its_exit_code(self, tmp_path):
        """Exit 3 means "the environment is damaged" - the one diagnosis that
        maps to a one-click fix. start() must not return success and let the
        first request shrug with 'unavailable'."""
        script = tmp_path / "dies_early.py"
        script.write_text("import sys\nsys.exit(3)\n", encoding="utf-8")
        c = WorkerClient(sys.executable, str(script))
        with pytest.raises(WorkerFailure) as exc:
            c.start(timeout=15)
        assert exc.value.code == TTS_RUNTIME_BROKEN

    def test_exit_two_without_oom_evidence_is_a_crash_not_oom(self, tmp_path):
        """Exit 2 is also CPython's own usage-error code. 'Lower your memory
        settings' must not be the advice for a missing file."""
        c = WorkerClient(sys.executable, str(tmp_path / "no_such_script.py"))
        with pytest.raises(WorkerFailure) as exc:
            c.start(timeout=15)
        assert exc.value.code == TTS_WORKER_CRASHED
        assert exc.value.code != TTS_OUT_OF_MEMORY

    def test_a_graceful_close_actually_exits_zero(self):
        """The goodbye frame must really reach the child: before the fix the
        writer thread died on a nulled attribute and every single close fell
        through to terminate()."""
        c = WorkerClient(sys.executable, FAKE)
        c.start(timeout=30)
        c.close(grace=5.0)
        assert c.exit_code == 0, "the shutdown frame never reached the worker"

    def test_an_unknown_code_from_the_worker_is_not_passed_through(self):
        """The code crosses a process boundary; it is data. An unknown string
        would reach the frontend and fall through to the generic toast."""
        c = WorkerClient(sys.executable, FAKE)
        c.start(timeout=30)
        try:
            with pytest.raises(WorkerFailure) as exc:
                c.request(_wire.OP_LOAD, {"mode": "alien"})
            assert exc.value.code == TTS_WORKER_FAILED
        finally:
            c.close(grace=0.2)

    def test_pending_map_does_not_grow_with_successful_requests(self):
        c = WorkerClient(sys.executable, FAKE)
        c.start(timeout=30)
        try:
            for _ in range(10):
                c.request(_wire.OP_PING)
            assert len(c._pending) == 0
        finally:
            c.close(grace=0.2)

    def test_a_frame_bigger_than_the_pipe_buffer_round_trips(self, tmp_path):
        """The 4096-byte Windows pipe buffer is the module's headline hazard;
        prove the dedicated writer/reader threads actually clear it."""
        c = WorkerClient(sys.executable, FAKE)
        c.start(timeout=30)
        try:
            big = "m" * 20_000
            out = str(tmp_path / "big.wav")
            res = c.request(_wire.OP_SYNTHESIZE, {"text": big, "out": out},
                            timeout=30)
            assert res["text_len"] == 20_000
        finally:
            c.close(grace=0.2)

    def test_a_malformed_frame_does_not_kill_the_reader(self):
        """One bad frame must be dropped like noise - before the fix it ended
        the reader loop, whose cleanup then declared a live worker dead."""
        assert _wire.decode('{"id": [1], "ok": true}') is not None  # decodes...
        c = WorkerClient(sys.executable, FAKE)
        c.start(timeout=30)
        try:
            # REAL junk on the wire (audit-2: the old line here was a no-op
            # attribute access that injected nothing): the fake writes two
            # non-frame lines onto the protocol channel mid-conversation.
            assert c.request(_wire.OP_LOAD, {"mode": "midjunk"})["loaded"] is True
            # The reader survived the junk and stays in sync.
            assert c.request(_wire.OP_PING)["pong"] is True
            assert c.alive
        finally:
            c.close(grace=0.2)


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
