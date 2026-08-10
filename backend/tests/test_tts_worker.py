"""V3 - owning an engine subprocess without ever orphaning it.

These spawn a REAL subprocess over REAL pipes (tests/fake_worker.py speaks the
real protocol), because the failures that matter here are not logic errors -
they are pipe deadlocks, encoding faults, and orphaned processes sitting on
gigabytes of VRAM. A mock would pass while every one of those shipped.
"""
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from tts.errors import (
    TTS_LOAD_TIMEOUT,
    TTS_OUT_OF_MEMORY,
    TTS_REFERENCE_INVALID,
    TTS_RUNTIME_BROKEN,
    TTS_WORKER_CRASHED,
    TTS_WORKER_UNAVAILABLE,
)
from tts.worker import _wire
from tts.worker_client import WorkerClient, WorkerFailure

FAKE = str(Path(__file__).resolve().parent / "fake_worker.py")
BACKEND = str(Path(__file__).resolve().parents[1])


def _client(**kw):
    return WorkerClient(sys.executable, FAKE, engine_id="fake", **kw)


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        out = subprocess.run(
            ["tasklist", "/FI", "PID eq %d" % pid, "/NH"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        ).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


@pytest.fixture
def worker():
    c = _client()
    c.start(timeout=30)
    yield c
    c.close(grace=0.2)


class TestTheProtocolItself:
    def test_a_frame_survives_a_turkish_sentence(self):
        """The pipe carries user text. If encoding breaks here, it breaks on
        exactly the sentences this app exists to speak."""
        line = _wire.encode({"id": 1, "text": "Seni ozlemisim, cok fena. Gunaydin!"})
        assert _wire.decode(line)["text"] == "Seni ozlemisim, cok fena. Gunaydin!"

    def test_accented_text_survives_the_json_round_trip(self):
        turkish = "".join(chr(c) for c in (0x11F, 0x131, 0x15F, 0xE7, 0xF6, 0xFC))
        assert _wire.decode(_wire.encode({"t": turkish}))["t"] == turkish

    def test_garbage_lines_decode_to_none_rather_than_raising(self):
        assert _wire.decode("not json") is None
        assert _wire.decode("[1,2]") is None          # a list is not a frame
        assert _wire.decode("") is None
        assert _wire.decode("   ") is None
        assert _wire.decode(None) is None

    def test_an_absurdly_long_line_is_refused_not_parsed(self):
        assert _wire.decode('{"a":"' + "x" * (5 * 1024 * 1024) + '"}') is None

    def test_oom_is_recognised_without_importing_torch(self):
        class OutOfMemoryError(Exception):
            pass

        assert _wire.is_oom(OutOfMemoryError("boom"))
        assert _wire.is_oom(RuntimeError("CUDA out of memory. Tried to allocate"))
        assert not _wire.is_oom(ValueError("something else entirely"))


class TestTalkingToARealSubprocess:
    def test_it_starts_and_answers(self, worker):
        assert worker.alive
        assert worker.request(_wire.OP_PING)["pong"] is True

    def test_it_round_trips_text_through_the_pipes(self, worker, tmp_path):
        out = str(tmp_path / "a.wav")
        text = "Bugun hava cok guzel, degil mi?"
        res = worker.request(_wire.OP_SYNTHESIZE, {"text": text, "out": out})
        assert Path(out).is_file() and Path(out).read_bytes()[:4] == b"RIFF"
        assert res["text_len"] == len(text)

    def test_library_noise_on_stdout_does_not_desynchronise_the_stream(self, worker):
        """torch and friends print banners and progress bars. claim_stdout()
        moves them to stderr; one stray byte on the protocol channel would
        leave the app waiting forever on a worker that is perfectly fine."""
        assert worker.request(_wire.OP_LOAD, {"mode": "noisy"})["loaded"] is True
        assert worker.request(_wire.OP_PING)["pong"] is True     # still in sync

    def test_junk_on_the_protocol_channel_before_hello_is_skipped(self):
        c = WorkerClient(sys.executable, FAKE,
                         env={"ELYSIUM_FAKE_STDOUT_NOISE": "1"})
        c.start(timeout=30)
        try:
            assert c.request(_wire.OP_PING)["pong"] is True
        finally:
            c.close(grace=0.2)

    def test_progress_events_arrive_without_an_id(self, worker):
        worker.request(_wire.OP_LOAD, {"mode": "ok"})
        assert any(e.get("event") == "progress" for e in worker._events)

    def test_many_requests_in_a_row_stay_correlated(self, worker, tmp_path):
        """Ids exist so answers cannot be handed to the wrong caller."""
        for i in range(12):
            out = str(tmp_path / ("s%d.wav" % i))
            res = worker.request(_wire.OP_SYNTHESIZE, {"text": "x" * i, "out": out})
            assert res["text_len"] == i


class TestFailuresBecomeAdvice:
    def test_cuda_oom_maps_to_its_own_code_not_a_generic_crash(self, worker):
        """Exit code 2 is an EXPECTED outcome with specific advice attached:
        lower the memory settings. 'It crashed' would send the user nowhere."""
        with pytest.raises(WorkerFailure) as exc:
            worker.request(_wire.OP_LOAD, {"mode": "oom"})
        assert exc.value.code == TTS_OUT_OF_MEMORY

    def test_a_named_engine_failure_keeps_its_name(self, worker):
        with pytest.raises(WorkerFailure) as exc:
            worker.request(_wire.OP_LOAD, {"mode": "coded"})
        assert exc.value.code == TTS_REFERENCE_INVALID

    def test_a_named_failure_does_not_kill_the_worker(self, worker):
        with pytest.raises(WorkerFailure):
            worker.request(_wire.OP_LOAD, {"mode": "coded"})
        assert worker.alive, "a bad clip is not a reason to unload the model"
        assert worker.request(_wire.OP_PING)["pong"] is True

    def test_a_sudden_death_is_reported_as_a_crash_not_a_hang(self, worker):
        with pytest.raises(WorkerFailure) as exc:
            worker.request(_wire.OP_LOAD, {"mode": "crash"}, timeout=20)
        assert exc.value.code == TTS_WORKER_CRASHED
        assert not worker.alive

    def test_a_missing_interpreter_is_a_broken_runtime_with_a_one_click_fix(self):
        c = WorkerClient(os.path.join("C:", os.sep, "nope", "python.exe"), FAKE)
        with pytest.raises(WorkerFailure) as exc:
            c.start(timeout=5)
        assert exc.value.code == TTS_RUNTIME_BROKEN

    def test_a_request_after_death_says_unavailable(self, worker):
        worker.close(grace=0.1)
        with pytest.raises(WorkerFailure) as exc:
            worker.request(_wire.OP_PING)
        assert exc.value.code == TTS_WORKER_UNAVAILABLE

    def test_a_worker_that_stops_answering_is_taken_down_not_left_running(self):
        """It may be mid-allocation on the card. Leaving it running is the one
        option that ends with a machine the user has to reboot."""
        c = _client()
        c.start(timeout=30)
        try:
            with pytest.raises(WorkerFailure) as exc:
                c.request(_wire.OP_LOAD, {"mode": "hang"}, timeout=1.5)
            assert exc.value.code == TTS_LOAD_TIMEOUT
            assert not c.alive, "a silent worker must not survive its own timeout"
        finally:
            c.close(grace=0)

    def test_stderr_is_kept_so_a_crash_can_be_explained(self, worker):
        worker.request(_wire.OP_LOAD, {"mode": "noisy"})
        deadline = time.time() + 5
        while time.time() < deadline:
            if any("loud library warning" in ln for ln in worker.stderr_tail):
                return
            time.sleep(0.1)
        pytest.fail("stderr was not captured; a crash would be unexplainable")

    def test_death_releases_every_waiting_caller_at_once(self):
        """Without this each caller waits out its own full timeout while the
        VRAM it was waiting for has already been freed."""
        import threading

        c = _client()
        c.start(timeout=30)
        results = []

        def call():
            try:
                c.request(_wire.OP_LOAD, {"mode": "slow", "secs": 30}, timeout=60)
                results.append("ok")
            except WorkerFailure as exc:
                results.append(exc.code)

        threads = [threading.Thread(target=call, daemon=True) for _ in range(4)]
        for t in threads:
            t.start()
        time.sleep(1.0)
        started = time.time()
        c.close(grace=0)
        for t in threads:
            t.join(timeout=15)
        assert len(results) == 4, "callers were left hanging after the worker died"
        assert time.time() - started < 15


class TestNothingIsLeftBehind:
    def test_close_actually_ends_the_process(self, worker):
        pid = worker._proc.pid
        worker.close(grace=0.5)
        deadline = time.time() + 10
        while time.time() < deadline and _pid_alive(pid):
            time.sleep(0.1)
        assert not _pid_alive(pid)

    def test_close_is_safe_to_call_twice(self, worker):
        worker.close(grace=0.2)
        worker.close(grace=0.2)

    def test_closing_a_worker_that_never_started_is_harmless(self):
        _client().close()

    def test_the_job_object_was_actually_created(self, worker):
        """Without explicit ctypes restype/argtypes the 64-bit handle truncates
        on x64 and this is silently false - which is exactly how orphan
        protection disappears with no error anywhere."""
        if sys.platform != "win32":
            pytest.skip("job objects are a Windows mechanism")
        assert worker._job.active, "no job object: orphan protection is off"

    def test_a_killed_parent_cannot_leave_the_worker_running(self, tmp_path):
        """The real scenario: the app is force-killed, so no cleanup code of
        ours runs at all. The job object has to take the worker with it -
        stdin EOF alone would not cover a child that stopped reading."""
        if sys.platform != "win32":
            pytest.skip("job objects are a Windows mechanism")
        driver = tmp_path / "driver.py"
        driver.write_text(
            "import sys, time\n"
            "sys.path.insert(0, r%r)\n"
            "from tts.worker_client import WorkerClient\n"
            "c = WorkerClient(r%r, r%r)\n"
            "c.start(timeout=30)\n"
            "print(c._proc.pid, flush=True)\n"
            "time.sleep(60)\n" % (BACKEND, sys.executable, FAKE),
            encoding="utf-8",
        )
        parent = subprocess.Popen(
            [sys.executable, "-u", str(driver)],
            stdout=subprocess.PIPE, text=True, encoding="utf-8", cwd=BACKEND,
        )
        try:
            child_pid = int(parent.stdout.readline().strip())
            assert _pid_alive(child_pid)
            parent.kill()                       # no cleanup of ours runs
            parent.wait(timeout=10)
            deadline = time.time() + 20
            while time.time() < deadline and _pid_alive(child_pid):
                time.sleep(0.2)
            assert not _pid_alive(child_pid), "orphan worker survived; it holds VRAM"
        finally:
            if parent.poll() is None:
                parent.kill()

    def test_hard_close_runs_every_registered_teardown(self):
        from tts import worker_client as wc

        seen = []
        saved = list(wc._TEARDOWN)
        wc._TEARDOWN.clear()
        try:
            wc.register_teardown(lambda grace: seen.append("a"))

            def explodes(grace):
                raise RuntimeError("hook is broken")

            wc.register_teardown(explodes)
            wc.register_teardown(lambda grace: seen.append("b"))
            wc.hard_close(0)
            # A failing hook must not stop the ones after it - any one of them
            # may be the one actually holding the GPU.
            assert seen == ["a", "b"]
        finally:
            wc._TEARDOWN.clear()
            wc._TEARDOWN.extend(saved)


# ── Audit: the worker progress channel was write-only ──────────────────────
#
# _on_event appended every non-`ready` frame to a 200-entry ring buffer that had
# no accessor and no reader anywhere in the codebase, and logged nothing. All 29
# _progress() emissions from the fish worker were unreachable - including the
# actionable ones ("first compile is slow; a warm TORCHINDUCTOR_CACHE_DIR makes
# it ~59s", "compiling into a temporary cache; every load will be slow",
# "staying bf16; generation will be slower"). The user saw state "loading" for
# up to TTS_LOAD_TIMEOUT_S and then a bare tts_load_timeout.


def test_a_note_from_the_worker_is_logged_as_a_warning(caplog):
    from tts import worker_client

    frame = {
        "event": "progress", "stage": "cache_dir_unusable",
        "note": "compiling into a temporary cache; every load will be slow",
    }
    with caplog.at_level("WARNING"):
        worker_client._log_worker_event(
            worker_client.logger, "fish_s2", frame,
        )
    assert "cache_dir_unusable" in caplog.text
    assert "every load will be slow" in caplog.text
    assert "fish_s2" in caplog.text


def test_a_note_keeps_its_detail(caplog):
    """The note says WHAT happened; the detail says why. Logging only the note
    threw away the exception - which is what a skipped codec prewarm needed."""
    from tts import worker_client

    with caplog.at_level("WARNING"):
        worker_client._log_worker_event(worker_client.logger, "fish_s2", {
            "event": "progress", "stage": "codec_prewarm_skipped",
            "note": "the first spoken sentence will load it instead",
            "detail": "KeyError: 'load_dac'",
        })
    assert "the first spoken sentence" in caplog.text
    assert "KeyError" in caplog.text


def test_a_bare_stage_is_logged_as_progress(caplog):
    from tts import worker_client

    with caplog.at_level("INFO"):
        worker_client._log_worker_event(
            worker_client.logger, "fish_s2",
            {"event": "progress", "stage": "decoding"},
        )
    assert "decoding" in caplog.text


def test_non_progress_frames_are_not_logged(caplog):
    from tts import worker_client

    with caplog.at_level("INFO"):
        worker_client._log_worker_event(
            worker_client.logger, "fish_s2", {"event": "ready"},
        )
    assert caplog.text == ""


def test_the_event_buffer_is_readable():
    """It was write-only, which is why nothing noticed the channel went
    nowhere."""
    from tts.worker_client import WorkerClient

    assert isinstance(WorkerClient.events, property)


# ── Audit: an eviction rebuild must not resurrect a broken torch.compile ────
#
# _warmup handles a missing MSVC/triton toolchain by dropping to eager decoding
# - "a slow voice beats no voice". _build_model then unconditionally set
# compiled=True and the compiled decode, and _ensure_model re-runs _build_model
# after a VRAM eviction WITHOUT re-running _warmup. The next sentence hit the
# same "cl is not found" failure inside _op_synthesize, where there is no
# fallback: a working voice became permanently broken, rebuild-and-fail on every
# sentence, until the worker was restarted.


def _fish_source() -> str:
    from pathlib import Path
    return (Path(__file__).resolve().parent.parent
            / "tts" / "worker" / "fish_s2.py").read_text(encoding="utf-8")


def test_the_eager_fallback_is_recorded_as_sticky_state():
    source = _fish_source()
    assert '"compile_broken": False' in source, "the sticky flag is gone"
    assert 'STATE["compile_broken"] = True' in source, (
        "_warmup no longer records that the compiler is unusable here"
    )


def test_build_model_asks_for_compile_only_when_it_can_work():
    source = _fish_source()
    assert 'want_compile = not STATE.get("compile_broken")' in source
    assert 'compile=want_compile' in source, (
        "_build_model still asks for torch.compile unconditionally"
    )
    assert 'STATE["compiled"] = want_compile' in source
    assert 'STATE["compiled"] = True' not in source, (
        "a post-eviction rebuild would report itself as compiled again"
    )


# ── The load timeout is a SILENCE budget, not a wall clock ─────────────────
#
# This file's own header says the first inductor compile takes ~346 s while
# TTS_LOAD_TIMEOUT_S is 180. Charging that compile against a fixed budget made
# the documented cold path IMPOSSIBLE: every cold start was killed mid-compile
# at exactly 180 s, and voice only ever worked if enough repeated failures
# happened to warm the cache by accident. Observed twice in one evening's log,
# both times at 179-180 s, both times during "compiling".


def test_progress_frames_extend_the_deadline():
    from tts.worker_client import _Pending

    pending = _Pending()
    first = pending.last_progress
    import time as _time
    _time.sleep(0.01)
    pending.last_progress = _time.monotonic()
    assert pending.last_progress > first


def test_a_progress_frame_bumps_every_in_flight_request(monkeypatch):
    from tts.worker_client import WorkerClient, _Pending

    client = WorkerClient.__new__(WorkerClient)
    client._ready = threading.Event()
    client._pending_lock = threading.Lock()
    client._events = []
    client.engine_id = "fish_s2"
    pending = _Pending()
    pending.last_progress = 0.0
    client._pending = {1: pending}

    client._on_event({"event": "progress", "stage": "compiling"})
    assert pending.last_progress > 0.0, "a working worker must not time out"


def test_a_non_progress_frame_does_not_extend_it():
    """Only evidence of WORK counts - a stray frame must not keep a wedged
    worker alive forever."""
    from tts.worker_client import WorkerClient, _Pending

    client = WorkerClient.__new__(WorkerClient)
    client._ready = threading.Event()
    client._pending_lock = threading.Lock()
    client._events = []
    client.engine_id = "fish_s2"
    pending = _Pending()
    pending.last_progress = 0.0
    client._pending = {1: pending}

    client._on_event({"event": "ready"})
    assert pending.last_progress == 0.0


def test_the_wait_loop_measures_silence_not_elapsed_time():
    """The contract, read off the source: the budget is compared against time
    since the last progress frame."""
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent
              / "tts" / "worker_client.py").read_text(encoding="utf-8")
    assert "time.monotonic() - pending.last_progress" in source
    assert "if not pending.done.wait(timeout):" not in source, (
        "the fixed wall-clock wait is back - a cold compile cannot finish"
    )


# ── The codec must not be a surprise on the first Speak ────────────────────
#
# Measured on the real app: load finished, the model reported ready, and the
# first press of Speak then took 51.6 s - 16.3 s of generation and 35.3 s of
# "loading the DAC codec" (codec.pth is 1.7 GB, read from disk). From the
# SECOND sentence on it was fine, because _drop_codec parks it in system RAM
# (~0.3 s to bring back). Only the first one had nothing parked to restore.


def _fish_source() -> str:
    from pathlib import Path
    return (Path(__file__).resolve().parent.parent
            / "tts" / "worker" / "fish_s2.py").read_text(encoding="utf-8")


def test_the_codec_is_warmed_during_load():
    source = _fish_source()
    assert "_prewarm_codec(send)" in source, "the first Speak still pays the disk read"
    # ...and only AFTER model_path is published. _codec() resolves codec.pth
    # relative to it, so an earlier call looked for a bare "codec.pth" in the
    # working directory and skipped the prewarm with tts_worker_failed -
    # observed in the shipped build, twice.
    published = source.index('STATE["model_path"] = str(ckpt)')
    called = source.index("    _prewarm_codec(send)")
    assert called > published, "prewarm runs before it can resolve codec.pth"


def test_the_prewarmed_codec_stays_resident_when_there_is_room():
    """The user hears the difference: a parked codec costs a PCIe copy on the
    first sentence, for nothing on a card with headroom."""
    source = _fish_source()
    body = source[source.index("def _prewarm_codec"):]
    body = body[: body.index("def _drop_codec")]
    assert "_should_keep_codec(_free_gb())" in body, (
        "the prewarm no longer consults the measured keep policy"
    )


def test_prewarming_parks_rather_than_keeping_it_resident():
    """The VRAM policy is unchanged: measured, this card reports 1.76-2.02 GB
    free with the codec resident, so it cannot stay."""
    source = _fish_source()
    body = source[source.index("def _prewarm_codec"):]
    body = body[: body.index("def _drop_codec")]
    assert "_codec(send)" in body
    # Parked only when the measured policy says the card is tight.
    assert "_drop_codec()" in body


def test_a_failed_prewarm_is_not_fatal():
    """A head start, not a requirement - the lazy path still works."""
    source = _fish_source()
    body = source[source.index("def _prewarm_codec"):]
    body = body[: body.index("def _drop_codec")]
    assert "except BaseException" in body
    assert "codec_prewarm_skipped" in body


def test_the_park_restore_block_is_not_duplicated():
    """It was pasted twice; the second copy was unreachable."""
    assert _fish_source().count("restoring the codec from memory") == 1


def test_the_pre_generation_guard_measures_the_work_that_is_coming():
    """It used to compare free memory against a fixed 3.0 GB floor.

    Two separate faults, one cause - a forecast made without the forecast's
    input. The floor was fixed while a decode's cost is not (it scales with the
    frames produced), and the guard ran ABOVE the block that computes
    `max_new`, so it could not have used the size even if it had wanted to.
    Both showed up in the same crash: a card sitting comfortably over 3.0 GB
    kept the codec, then OOMed on a maximal decode.

    Asserted as BEHAVIOUR. This used to read the source and look for the
    literal `_fits(max_new, ...)`, which pinned the spelling rather than the
    property - and then failed the day the forecast got better, because the
    call now passes the size of the work rather than the largest size allowed.
    """
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "fish_s2_guard", Path(__file__).resolve().parents[1] / "tts" / "worker" / "fish_s2.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("fish_s2_guard", mod)
    spec.loader.exec_module(mod)
    mod._COSTS.clear()
    mod.STATE["codec"] = object()
    for units, gb in [(24, 0.126), (143, 0.738), (428, 2.200)]:
        mod._observe_cost("decode", units, gb)
    mod._free_gb = lambda: 4.0

    # The property: one card, one moment, two different sizes of work, two
    # different answers. A fixed floor cannot produce that.
    assert mod._fits(50, "generate", "decode") is True
    assert mod._fits(1200, "generate", "decode") is False
    assert not hasattr(mod, "_DECODE_FLOOR_GB"), "the fixed floor came back"


def test_the_guard_runs_after_the_budget_is_known():
    """Ordering IS the fix; asserting the call alone would pass on the broken
    layout too."""
    source = _fish_source()
    body = source[source.index("def _op_synthesize"):]
    assert body.index("max_new = _fit_tokens") < body.index('where="pre-generation"')


def test_one_reserve_answers_every_question():
    """4.0, 3.0 and 1.0 are gone. What is left is a floor under the DESKTOP,
    and a prior that the first real measurement overwrites."""
    source = _fish_source()
    assert "_DECODE_FLOOR_GB" not in source
    assert "_CODEC_FLOOR_GB" not in source
    assert "_CODEC_KEEP_GB" not in source
    assert "_VRAM_RESERVE_GB = 1.0" in source


def test_only_should_keep_codec_decides_whether_the_codec_stays():
    """The 4.0 GB threshold is gone, not just lowered in one of its homes.

    It survived in the two reference-encoding paths long after the decode
    stopped using it, so "we set it to 1 GB" was true of exactly one caller
    and the other two kept dropping the codec on a card with room to spare.
    """
    source = _fish_source()
    assert "_CODEC_KEEP_GB" not in source
    # Prewarm + the two reference-encoding paths; the decode asks in its own
    # phrasing because it already has the reading in hand.
    assert source.count("not _should_keep_codec(_free_gb())") == 3
    assert "_should_keep_codec(free_after)" in source


def test_an_out_of_memory_decode_retries_instead_of_killing_the_worker():
    """Observed live: OOM at 03:52:37, worker dead at 03:53:08, no voice until
    the app was restarted. The 7 GB text2semantic model was still resident."""
    source = _fish_source()
    body = source[source.index("def _decode_to_audio"):]
    body = body[: body.index("def _op_synthesize")]
    assert "_wire.is_oom(exc)" in body
    assert 'STATE["model"] is None' in body      # nothing left to free: give up
    assert body.count("_decode_once(codes, codec, torch)") == 2
    assert "force=True" in body


class TestWhatTheChildProcessActuallySees:
    """The one question the environment tests upstairs cannot answer.

    Every existing test of the worker's environment mocks `subprocess.Popen`,
    captures the `env=` dict, and asserts on it. That is the right way to test
    the stripping rules and it is thorough: the credentials, the proxies, the
    forced offline flags and the launch token are all covered, each with a
    dirty parent environment set up first.

    What none of them can show is that any of it ARRIVES. They prove what the
    parent MEANT. Between the dict and the child sit `Popen`'s own env
    handling, Windows' case-insensitive variable names, and whatever the rest
    of `start()` does after the dict is built. A regression in any of those
    would leave all of those tests green.

    So this one spawns a real child over a real pipe and asks it. It costs one
    process; the fake worker is already spawned this way by two dozen tests in
    this file, and answering `ping` with a named slice of `os.environ` is a
    handful of lines in it.
    """

    #: What the child is asked about. Read from production rather than
    #: retyped: a variable added to the strip list and not to this tuple would
    #: otherwise be tested by nothing at all.
    def _seen(self, monkeypatch, keys):
        from tts import worker_client

        client = _client()
        client.start(timeout=30)
        try:
            answer = client.request(_wire.OP_PING,
                                    {"mode": "env", "keys": list(keys)},
                                    timeout=30)
        finally:
            client.close(grace=0.2)
        assert answer.get("pong") is True, answer
        return answer["env"]

    def test_a_credential_in_this_process_does_not_reach_the_child(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The parent is deliberately dirty first.

        Asserting on a clean environment would pass on a machine where the
        variable was never set, which is most machines, which is exactly how a
        stripping bug ships.
        """
        from tts import worker_client

        for name in worker_client._ENV_STRIP:
            monkeypatch.setenv(name, "leaked-" + name.lower())

        seen = self._seen(monkeypatch, worker_client._ENV_STRIP)
        leaked = {k: v for k, v in seen.items() if v is not None}
        assert not leaked, (
            f"the child process can read these: {sorted(leaked)}. The env dict "
            f"handed to Popen is stripped, so something between building it "
            f"and the child reading it is putting them back."
        )

    def test_the_offline_flags_are_what_the_child_reads(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """And they are read against a parent that says the opposite.

        `_ENV_FORCE` is applied with update rather than setdefault precisely so
        an inherited HF_HUB_OFFLINE=0 from a developer's shell cannot re-enable
        Hub access inside the engine. That ordering is asserted upstairs on the
        dict; this asserts it on the process.
        """
        from tts import worker_client

        for name in worker_client._ENV_FORCE:
            monkeypatch.setenv(name, "definitely-not-the-forced-value")

        seen = self._seen(monkeypatch, worker_client._ENV_FORCE)
        wrong = {k: seen.get(k) for k, want in worker_client._ENV_FORCE.items()
                 if seen.get(k) != want}
        assert not wrong, (
            f"the child reads {wrong}, and the parent forced "
            f"{ {k: worker_client._ENV_FORCE[k] for k in wrong} }"
        )

    def test_the_probe_can_actually_see_a_variable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The floor under the two tests above.

        Both of them pass if the child reports None for everything, which is
        also what a broken probe reports: a typo in the op, a `keys` list that
        never arrives, a fake worker that answers a stale shape. This sets a
        variable that nothing strips and requires the child to see it, so
        "nothing leaked" cannot quietly mean "nothing was read".
        """
        monkeypatch.setenv("ELYSIUM_ENV_PROBE", "the-child-can-read-this")
        seen = self._seen(monkeypatch, ["ELYSIUM_ENV_PROBE"])
        assert seen["ELYSIUM_ENV_PROBE"] == "the-child-can-read-this", (
            "the child could not read an ordinary variable, so the two tests "
            "above are asserting over an empty answer and prove nothing"
        )
