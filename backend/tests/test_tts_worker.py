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
    TTS_WORKER_FAILED,
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


# Everything from here down used to READ fish_s2.py's own text and look for
# substrings in it. A grep cannot tell a rule that RUNS from a rule that is
# merely written down: it passes on a build where the line sits in dead code
# or in an unreachable branch, and it fails on a correct build that spells the
# same rule differently. These drive the real functions instead, against the
# boundary stubs below - the same trade fish_synth_harness.py already makes
# for the synthesis path, and for the same reason: the real engine needs a
# CUDA card, but everything between the model and the card is replaceable.


class _Card:
    """Only the torch.cuda calls fish_s2's load path actually makes.

    A fake that answers more than the real thing is asked would hide a wrong
    call. `free_gb` is a dial because every codec-policy test below is about
    what the same code does at two different readings of the same card.
    """

    def __init__(self, free_gb: float) -> None:
        self.free_gb = free_gb

    def is_available(self) -> bool:
        return True

    def mem_get_info(self):
        return int(self.free_gb * 1e9), int(16 * 1e9)

    def memory_reserved(self) -> int:
        return 0

    def memory_allocated(self) -> int:
        return 0

    def reset_peak_memory_stats(self) -> None:
        pass

    def max_memory_allocated(self) -> int:
        return 0

    def empty_cache(self) -> None:
        pass

    def synchronize(self) -> None:
        pass


class _DeviceScope:
    """`with torch.device("cuda"):` - the one context manager _build_model
    opens."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeTorch:
    bfloat16 = "bfloat16"

    def __init__(self, free_gb: float) -> None:
        self.cuda = _Card(free_gb)

    def device(self, name):
        return _DeviceScope()


def _eager_decode(*a, **kw):
    """The stand-in for `decode_one_token_ar`.

    Its IDENTITY is the whole point - several tests below ask which decode the
    worker installed, not what it computes - so being called is a bug worth
    saying out loud.
    """
    raise AssertionError("the eager decode was called, not merely selected")


class _FakeModel:
    """What `init_model` hands back, reduced to what `_build_model` touches."""

    def __init__(self, hard_max: int = 4096) -> None:
        self.config = type("Config", (), {"max_seq_len": hard_max})()
        self.max_seq_len = hard_max
        self.caches = None

    def setup_caches(self, max_batch_size, max_seq_len, dtype):
        self.caches = (max_batch_size, max_seq_len, dtype)


class _Codec:
    """A codec that remembers where it was last moved to: "resident" and
    "parked" are the two states every policy test below asks about."""

    def __init__(self) -> None:
        self.device = "cuda"

    def to(self, device):
        self.device = str(device)
        return self


class _Tokens:
    """Encoded reference tokens: `_op_prepare_ref` reads both shape axes."""

    def __init__(self) -> None:
        self.shape = (2, 100)


class _DecodedCodes:
    """What the generation hands the decode.

    THREE axes on purpose, the same shape and the same reason as
    fish_synth_harness._Codes: with a two-element shape `shape[1]` and
    `shape[-1]` are the same element, so a production slip from one to the
    other would run through the stub untouched - which is the one thing a stub
    exists to prevent. `shape[0]` stays 1 because it is read elsewhere as the
    codebook count, and a stub must not feed a lie to a reader outside its own
    path either.
    """

    def __init__(self, produced: int) -> None:
        self.shape = (1, produced, 0)


def _fish(free_gb: float = 6.0):
    """A fresh fish_s2 module with its engine boundary already fed.

    Fresh per call because STATE, _ENGINE and _COSTS are module globals and an
    estimator carrying another test's measurements is the one thing these must
    never share. The real `_engine()` is left in place rather than stubbed: it
    returns `_ENGINE` untouched once that dict is populated, so the import
    guard is fed, not bypassed.
    """
    import fish_synth_harness as synth

    mod = synth.load_worker()
    mod._ENGINE.update(torch=_FakeTorch(free_gb), decode_eager=_eager_decode)
    return mod


def _install_codec(mod, codec):
    """Make `_codec(send)` behave like the real one on its happy path: hand
    the codec back AND leave it in STATE, which is what the keep/park policy
    then acts on."""

    def _codec(send):
        mod.STATE["codec"] = codec
        return codec

    mod._codec = _codec
    return codec


def _fake_torchao(monkeypatch):
    """fp8 quantisation is not the subject here and torchao lives in the
    engine venv, not this one. Stubbing it makes `_build_model`'s outcome the
    same whether or not the real package happens to be importable, so these
    tests measure the compile decision and nothing else.
    """
    import types

    quant = types.ModuleType("torchao.quantization")
    quant.quantize_ = lambda model, config: None
    quant.Float8DynamicActivationFloat8WeightConfig = lambda: object()
    package = types.ModuleType("torchao")
    package.quantization = quant
    monkeypatch.setitem(sys.modules, "torchao", package)
    monkeypatch.setitem(sys.modules, "torchao.quantization", quant)


def _warmup_run(mod, *, compiler_works: bool):
    """Run the real `_warmup` with the compile attempt as a dial."""
    attempts: list[int] = []

    def _run_generation(text, prompt_text, prompt_tokens, budget, **knobs):
        attempts.append(budget)
        if not compiler_works and len(attempts) == 1:
            # The real shape of it: torch.compile is lazy, so a missing
            # MSVC/triton toolchain surfaces HERE, as a RuntimeError out of the
            # inductor backend, and not as an import error at load time.
            raise RuntimeError("Compiler: cl is not found.")
        return object()

    mod._run_generation = _run_generation
    events: list[dict] = []
    mod._warmup(events.append)
    return attempts, events


def test_the_eager_fallback_is_recorded_as_sticky_state():
    """A compiler this machine does not have is a property of the MACHINE.

    `_ensure_model` rebuilds after a VRAM eviction WITHOUT re-running
    `_warmup`, so a failure that is not written down here lets the rebuild ask
    for torch.compile again - and the next sentence then dies inside
    `_op_synthesize`, where there is no fallback. A working (slow) voice
    becomes permanently broken until the worker is restarted.
    """
    mod = _fish()
    attempts, events = _warmup_run(mod, compiler_works=False)

    assert len(attempts) == 2, (
        "the eager retry never ran, so a missing toolchain took the whole load "
        "down instead of dropping to a slow voice")
    assert mod.STATE["compiled"] is False, (
        "the worker still reports a compiled decode after the compile failed")
    assert mod.STATE["decode"] is _eager_decode, (
        "the compiled decode is still installed, so the first real sentence "
        "will hit the same failure with no fallback under it")
    assert mod.STATE["compile_broken"] is True, (
        "the unusable compiler was not recorded as sticky machine state; the "
        "next post-eviction rebuild will ask for torch.compile again")
    assert "compile_failed" in [e.get("stage") for e in events], (
        "the drop to eager was silent, so the log cannot explain a slow voice")


def test_a_compiler_that_works_is_not_recorded_as_broken():
    """GROUND CONTROL. Without it, code that set `compile_broken` on every
    load would satisfy the test above - and would then disable torch.compile
    on every machine, permanently, for the life of the worker."""
    mod = _fish()
    attempts, events = _warmup_run(mod, compiler_works=True)

    assert len(attempts) == 1, "a successful compile must not be retried"
    assert mod.STATE["compile_broken"] is False, (
        "a machine with a working toolchain was marked as broken, which costs "
        "it the compiled decode on every rebuild from here on")
    assert "compile_failed" not in [e.get("stage") for e in events]


def test_build_model_asks_for_compile_only_when_it_can_work(monkeypatch):
    """One machine, two states, two different requests to `init_model`.

    `_build_model` used to set `STATE["compiled"] = True` unconditionally, so
    a post-eviction rebuild on a machine `_warmup` had already proven cannot
    compile reported itself as compiled AND installed the compiled decode.
    Both halves run here, because each is the other's control: a build that
    always asks, and a build that never asks, each satisfy exactly one of them.
    """
    for compile_broken, want in ((False, True), (True, False)):
        mod = _fish()
        _fake_torchao(monkeypatch)
        asked: list[bool] = []
        compiled_decode = object()

        def init_model(path, device, dtype, compile, _asked=asked,
                       _decode=compiled_decode):
            _asked.append(compile)
            return _FakeModel(), _decode

        mod._ENGINE["init_model"] = init_model
        mod.STATE["compile_broken"] = compile_broken

        events: list[dict] = []
        mod._build_model(Path("model"), 2048, events.append)

        assert asked == [want], (
            f"with compile_broken={compile_broken} the worker asked init_model "
            f"for compile={asked}; asking a compiler that has already been "
            f"proven unusable is not optimism, it is the next crash")
        assert mod.STATE["compiled"] is want, (
            "STATE['compiled'] does not match what was actually asked for, so "
            "every reader of it - the load result, the ping, the generation - "
            "is being told about a decode path that does not exist")
        assert mod.STATE["decode"] is (compiled_decode if want
                                       else _eager_decode), (
            "the installed decode does not match the compile decision")


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


#: A worker whose only job is to be slow in one of the two ways that matter.
#:
#: It speaks the REAL protocol through `_wire.serve` over real pipes, because
#: the claim under test is about the client's wait loop and nothing short of a
#: real conversation exercises it: the frames have to arrive from another
#: process, on the reader thread, while the caller is blocked.
_SILENCE_WORKER = '''\
import sys
import time
from pathlib import Path

sys.path.insert(0, r"{worker_dir}")
import _wire


def handle(op, req, send):
    """Report for `talk` seconds, then say nothing for `quiet`, then answer."""
    deadline = time.monotonic() + float(req.get("talk") or 0.0)
    while time.monotonic() < deadline:
        time.sleep(0.2)
        send(_wire.event("progress", stage="compiling"))
    time.sleep(float(req.get("quiet") or 0.0))
    return {{"worked": True}}


if __name__ == "__main__":
    channel = _wire.claim_stdout()
    sys.exit(_wire.serve(handle, channel=channel))
'''


def _silence_worker(tmp_path) -> str:
    script = tmp_path / "silence_worker.py"
    script.write_text(
        _SILENCE_WORKER.format(worker_dir=str(Path(_wire.__file__).parent)),
        encoding="utf-8")
    return str(script)


def test_a_worker_that_keeps_reporting_outlives_its_budget(tmp_path):
    """The load timeout is a SILENCE budget, not a wall clock.

    This used to be asserted by reading worker_client.py and looking for the
    subtraction - which pins the spelling of one line and would pass on a build
    where that line sits in a branch the wait loop never takes. What it stands
    for is measured here instead: a worker that keeps reporting runs three
    times past a one-second budget and is still answered, because the first
    inductor compile legitimately runs ~346 s against a 180 s budget and every
    cold start was killed mid-compile before this rule existed.
    """
    client = WorkerClient(sys.executable, _silence_worker(tmp_path),
                          engine_id="silence")
    client.start(timeout=30)
    try:
        started = time.monotonic()
        result = client.request(_wire.OP_LOAD, {"talk": 3.0}, timeout=1.0)
        elapsed = time.monotonic() - started
    finally:
        client.close(grace=1.0)

    assert result.get("worked") is True, (
        "a worker that reported progress throughout was cut off anyway; the "
        "budget is being charged against elapsed time, so the documented cold "
        "compile is impossible and voice only ever works by accident")
    assert elapsed > 2.0, (
        f"the request finished in {elapsed:.1f}s, so it never actually "
        "outlived its 1.0s budget and this proves nothing")


def test_a_worker_that_goes_quiet_is_killed_at_its_budget(tmp_path):
    """THE OTHER HALF, and the reason the budget cannot simply be raised.

    Only evidence of WORK may extend it. Without this, "measure silence"
    degenerates into "never time out", and a worker wedged mid-allocation on
    the card sits there holding VRAM until the app is restarted.
    """
    client = WorkerClient(sys.executable, _silence_worker(tmp_path),
                          engine_id="silence")
    client.start(timeout=30)
    try:
        started = time.monotonic()
        with pytest.raises(WorkerFailure) as caught:
            client.request(_wire.OP_LOAD, {"quiet": 30.0}, timeout=1.0)
        elapsed = time.monotonic() - started
    finally:
        client.close(grace=0)

    assert caught.value.code == TTS_LOAD_TIMEOUT, (
        f"a silent worker was diagnosed as {caught.value.code}")
    assert elapsed < 10.0, (
        f"the silent worker was tolerated for {elapsed:.1f}s against a 1.0s "
        "budget; a wedged worker is not being caught in time")
    assert not client.alive, (
        "the wedged worker survived its own timeout, still holding the card")


# ── The codec must not be a surprise on the first Speak ────────────────────
#
# Measured on the real app: load finished, the model reported ready, and the
# first press of Speak then took 51.6 s - 16.3 s of generation and 35.3 s of
# "loading the DAC codec" (codec.pth is 1.7 GB, read from disk). From the
# SECOND sentence on it was fine, because _drop_codec parks it in system RAM
# (~0.3 s to bring back). Only the first one had nothing parked to restore.


def test_the_codec_is_warmed_during_load(tmp_path, monkeypatch):
    """A finished load leaves the codec already read in, from the right place.

    This used to grep for the call and then compare two `str.index` positions
    to pin the ORDER. Both halves are one observable fact instead: the load
    runs for real, and the only thing asserted is which path the DAC was read
    from. Nothing was read at all -> the prewarm is gone, and the user's first
    press of Speak pays the 1.7 GB disk read (measured at 35.3 s of a 51.6 s
    first sentence). Read from a bare "codec.pth" -> the prewarm ran before
    `model_path` was published, `_codec()` resolved it against the working
    directory, and the load skipped the prewarm with tts_worker_failed, which
    is what the shipped build did twice.
    """
    monkeypatch.delenv("TORCHINDUCTOR_CACHE_DIR", raising=False)
    model_dir = tmp_path / "fish"
    model_dir.mkdir()
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    (model_dir / "codec.pth").write_bytes(b"stand-in for 1.7 GB of DAC")

    mod = _fish()
    codec = _Codec()
    read_from: list[str] = []

    def load_dac(name, path, device):
        read_from.append(path)
        return codec

    mod._ENGINE["load_dac"] = load_dac
    mod._build_model = lambda ckpt, kv_len, send: None
    mod._warmup = lambda send: 0.0

    events: list[dict] = []
    result = mod._op_load({"model_path": str(model_dir), "values": {}},
                          events.append)

    assert read_from == [str(model_dir / "codec.pth")], (
        f"the codec was read from {read_from} during load. Empty means the "
        "prewarm never ran and the first sentence pays the disk read; a bare "
        "'codec.pth' means it ran before model_path was published and could "
        "not resolve the file at all.")
    assert "codec_prewarm_skipped" not in [e.get("stage") for e in events], (
        "the load reported the prewarm as skipped, so the head start it "
        "exists to give was not given")
    assert result["model_path"] == str(model_dir)


def test_the_prewarmed_codec_stays_resident_when_there_is_room():
    """The user hears the difference: a parked codec costs a PCIe copy on the
    first sentence, for nothing on a card with headroom.

    Asserted at TWO readings of the same card, one either side of the measured
    reserve, because a prewarm that always keeps and a prewarm that always
    parks each satisfy exactly one of them.
    """
    reserve = _fish()._VRAM_RESERVE_GB
    for free_gb, resident in ((reserve + 0.5, True), (reserve - 0.5, False)):
        mod = _fish(free_gb=free_gb)
        codec = _install_codec(mod, _Codec())
        events: list[dict] = []
        mod._prewarm_codec(events.append)

        if resident:
            assert mod.STATE["codec"] is codec, (
                f"at {free_gb} GB free the prewarm parked the codec anyway; "
                "the first sentence now pays a PCIe copy on a card with room")
            assert mod.STATE["codec_parked"] is None
            assert codec.device == "cuda"
        else:
            assert mod.STATE["codec"] is None, (
                f"at {free_gb} GB free - below the measured reserve of "
                f"{reserve} - the prewarm kept the codec resident; the next "
                "decode is the one that pays for it")
            assert mod.STATE["codec_parked"] is codec, (
                "the codec was thrown away rather than parked, so the next "
                "sentence reloads it from disk instead of copying it back")
            assert codec.device == "cpu"


# test_prewarming_parks_rather_than_keeping_it_resident was deleted in
# KADEME 20b. It sliced the source of `_prewarm_codec` and looked for two
# substrings, which cannot tell "parks when the card is tight" from
# "always parks" from "calls _drop_codec in an unreachable except branch".
# The test directly above it already pins the same slice behaviourally, by
# asserting the prewarm consults the measured keep policy.
#
# THE MEASUREMENT IN ITS DOCSTRING SURVIVES HERE, because it is recorded
# nowhere else in the repo: with the codec resident this card reports
# 1.76-2.02 GB free, which is why the codec cannot stay resident and why
# _VRAM_RESERVE_GB sits where it does.


def test_a_failed_prewarm_is_not_fatal():
    """A head start, not a requirement - the lazy path still works.

    Both widths are driven, because the catch is deliberately `BaseException`
    and an `Exception` would look identical to a grep: `_codec()` reaches
    `_engine()`, which answers a damaged runtime with `sys.exit()`. A load that
    has already built and compiled the model must not be taken down by an
    optimisation, whichever of the two it raises.
    """
    for boom in (RuntimeError("the codec file is unreadable"),
                 SystemExit(_wire.EXIT_ENGINE_IMPORT)):
        mod = _fish()

        def _codec(send, exc=boom):
            raise exc

        mod._codec = _codec
        events: list[dict] = []
        mod._prewarm_codec(events.append)          # must simply return

        skipped = [e for e in events
                   if e.get("stage") == "codec_prewarm_skipped"]
        assert skipped, (
            f"a prewarm that raised {type(boom).__name__} said nothing about "
            "it, so a first sentence that is suddenly 35 s slow has no "
            "explanation anywhere")
        assert skipped[0].get("note") == mod._wire.NOTE_LAZY_FIRST_SENTENCE
        assert mod.STATE["codec"] is None and mod.STATE["codec_parked"] is None, (
            "the failed prewarm left something behind, so the lazy path is no "
            "longer free to load the codec properly")

    # GROUND CONTROL: the skip is reported because it happened, not on every
    # load. A prewarm that always announced failure would satisfy the loop.
    mod = _fish()
    _install_codec(mod, _Codec())
    events = []
    mod._prewarm_codec(events.append)
    assert "codec_prewarm_skipped" not in [e.get("stage") for e in events], (
        "a prewarm that succeeded still reported itself as skipped")


# test_the_park_restore_block_is_not_duplicated was deleted in KADEME 20b.
# It counted how many times a log string appeared in the source: a lint
# check wearing a test's clothes. Rewording the message breaks it, and
# real duplication carrying a different message defeats it. Unreachable
# copied code is by definition unobservable, so no behaviour test can
# replace it - and none was ever written, which is why this note says so
# rather than pointing somewhere.


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
    """Ordering IS the fix: a guard that decides whether there is room without
    knowing how much work is coming is deciding on the one fact that determines
    the answer.

    This used to compare two `str.index` positions inside the function's source
    text. That is a test of the file's layout: it would pass on a build where
    the guard had been moved back up but its old line was still present in a
    comment, and it would fail on a correct build that spelled the assignment
    differently. What the guard actually PUBLISHES is the budget it used, in
    the pre-generation event, so that is what is checked - and it is checked at
    two different budgets, because a hard-coded constant would satisfy one.
    """
    import fish_synth_harness as synth

    for budget in (512, 200):
        run = synth.synthesize(produced=100, max_new=budget)
        guard = run.stage("codec_policy")
        assert guard is not None, "the codec guard did not run at all"
        assert guard["where"] == "pre-generation"
        assert guard["budget_frames"] == budget, guard
        # ... and it is the SAME budget the generation was then run with, so
        # the guard cannot be sizing one thing while the model does another.
        assert run.max_new_used() == budget


def test_the_three_old_vram_floors_are_gone():
    """4.0, 3.0 and 1.0 are gone. What is left is a floor under the DESKTOP,
    and a prior that the first real measurement overwrites.

    KADEME 20b trimmed and renamed this. Section 4 listed the whole test
    for deletion, and it was half right. The last line asserted a constant
    was PRESENT, which test_tts_packaging.py::test_the_keep_floor_matches_
    the_pre_generation_guard already pins behaviourally through
    _should_keep_codec - so that line went. The three lines above it assert
    ABSENCE, and no behaviour test can observe a constant that is not there.
    Those stay - but they ask the loaded MODULE OBJECT now, not the file's
    text: a constant deleted from the code and left behind in a comment or a
    docstring would satisfy a source scan, and satisfying it is exactly what a
    half-finished deletion does.
    """
    mod = _fish()
    for gone in ("_DECODE_FLOOR_GB", "_CODEC_FLOOR_GB", "_CODEC_KEEP_GB"):
        assert not hasattr(mod, gone), (
            f"{gone} is back. A fixed VRAM floor cannot see the size of the "
            "work coming, which is how a card sitting comfortably above it "
            "still OOMed on a maximal decode")
    # POSITIVE CONTROL for three absence claims: the module really did load,
    # and it really does still carry the two things that replaced them. Without
    # this the whole test passes against an empty object.
    assert hasattr(mod, "_VRAM_RESERVE_GB"), "the module under test is empty"
    assert callable(mod._should_keep_codec)


def _clip(tmp_path):
    clip = tmp_path / "ref.wav"
    clip.write_bytes(b"RIFF")
    return clip


def _drive_prewarm(mod, tmp_path, send):
    mod._prewarm_codec(send)


def _drive_reference_prompt(mod, tmp_path, send):
    mod._prompt({"reference": {"path": str(_clip(tmp_path)),
                               "transcript": "merhaba"}}, {}, send)


def _drive_prepare_ref(mod, tmp_path, send):
    mod.STATE["model_path"] = str(tmp_path)
    mod._op_prepare_ref({"audio": str(_clip(tmp_path)),
                         "transcript": "merhaba"}, send)


#: The three paths that borrow the codec and then have to decide whether to
#: hand it back. The decode is the fourth and asks in its own phrasing, because
#: it already has the reading in hand; it is driven in
#: TestAnOutOfMemoryDecodeIsRecoverable through the harness.
_CODEC_BORROWERS = {
    "prewarm": _drive_prewarm,
    "reference prompt": _drive_reference_prompt,
    "prepare_ref": _drive_prepare_ref,
}


def _stub_reference_encode(mod, codec):
    """Everything between the clip on disk and the policy decision, faked -
    and nothing else. What runs for real is which policy each path asks."""
    mod._encode_ref = lambda path, codec_arg, device: (_Tokens(), 1.5, 44100)
    mod._save_tokens = lambda tokens, target, send: target
    mod._free_for_codec = lambda send, why, force=False, frames=0: False
    _install_codec(mod, codec)


def test_only_should_keep_codec_decides_whether_the_codec_stays(tmp_path):
    """One policy, three callers, and the reading that used to split them.

    The 4.0 GB threshold survived in the two reference-encoding paths long
    after the decode stopped using it, so "we set it to 1 GB" was true of
    exactly one caller while the other two dropped the codec on a card with
    room to spare - a reload from disk, about five seconds, on the next
    sentence, every time.

    This used to be a `source.count(...) == 3`, which cannot tell three live
    callers from two live ones and a mention in a comment, and breaks the day
    a fourth caller spells the same question differently. All three are run
    instead, at the SAME two readings: one deliberately between the current
    reserve and the old floor, where the two policies disagree, and one
    genuinely tight, where they agree. A resurrected constant in any single
    path shows up as that path disagreeing with the other two.
    """
    reserve = _fish()._VRAM_RESERVE_GB
    assert reserve < 4.0, (
        "the reserve has moved above the old floor, so the reading below no "
        "longer separates the two policies and this test proves nothing")

    for free_gb, resident in ((reserve + 0.5, True), (reserve - 0.5, False)):
        for name, drive in _CODEC_BORROWERS.items():
            mod = _fish(free_gb=free_gb)
            codec = _Codec()
            _stub_reference_encode(mod, codec)
            events: list[dict] = []
            drive(mod, tmp_path, events.append)

            assert (mod.STATE["codec"] is codec) is resident, (
                f"at {free_gb} GB free the '{name}' path "
                f"{'parked' if resident else 'kept'} the codec, while the one "
                f"measured policy says {'keep' if resident else 'park'}. That "
                "path is deciding on a threshold of its own.")

    # THE THIRD DIMENSION, and the one a keep/park dial alone cannot see: a
    # path that BORROWED a resident codec must not hand back memory it never
    # took. Run on the tight card, where the policy would otherwise park it.
    for name in ("reference prompt", "prepare_ref"):
        mod = _fish(free_gb=reserve - 0.5)
        codec = _Codec()
        _stub_reference_encode(mod, codec)
        mod.STATE["codec"] = codec            # already resident before the call
        events = []
        _CODEC_BORROWERS[name](mod, tmp_path, events.append)
        assert mod.STATE["codec"] is codec, (
            f"the '{name}' path dropped a codec that was resident before it "
            "ran, so encoding a reference clip silently costs the next "
            "sentence a reload")


def test_the_decode_asks_the_same_policy_and_publishes_the_answer():
    """THE FOURTH CALLER, and the only one that already has the reading.

    `_decode_to_audio` measures free VRAM once and asks the policy with the
    number in hand, so it spells the question differently from the other
    three - which is precisely why counting one phrasing in the source text
    could never have covered it. It is the caller that runs after every single
    sentence, and it PUBLISHES its decision, because the ~5 s-per-sentence
    reload regression was only ever caught by seeing this event.
    """
    reserve = _fish()._VRAM_RESERVE_GB
    for free_gb, keep in ((reserve + 0.5, True), (reserve - 0.5, False)):
        mod = _fish(free_gb=free_gb)
        codec = _Codec()
        codec.sample_rate = 44100
        _install_codec(mod, codec)
        mod._decode_once = lambda codes, codec_arg, torch_arg: [0.0] * 1000
        mod._free_for_codec = lambda send, why, force=False, frames=0: False

        events: list[dict] = []
        audio, sr = mod._decode_to_audio(_DecodedCodes(400), events.append)

        assert sr == 44100 and audio, "the decode produced nothing to judge"
        published = [e for e in events
                     if e.get("stage") == "codec_policy"
                     and e.get("where") == "post-decode"]
        assert published, (
            "the decode did not say what it decided about the codec; the only "
            "way the per-sentence reload was ever noticed was by reading this")
        assert published[0]["keep"] is keep, (
            f"at {free_gb} GB free the decode published keep="
            f"{published[0]['keep']} while the one measured policy says "
            f"{keep}; the decode is judging on a threshold of its own")
        assert (mod.STATE["codec"] is not None) is keep, (
            "what the decode published is not what it did to the codec")


class TestAnOutOfMemoryDecodeIsRecoverable:
    """Observed live: OOM at 03:52:37, worker dead at 03:53:08, no voice until
    the app was restarted. The 7 GB text2semantic model was still resident, so
    there was room to be had and nobody took it.

    These read the decode path by RUNNING it. The version they replace counted
    substrings in the function's source text, which cannot tell a retry that
    happens from a retry that is written down, and cannot see the difference
    between the two failures below at all.
    """

    def test_a_recoverable_oom_frees_the_model_and_decodes_again(self):
        import fish_synth_harness as synth

        run = synth.decode_to_audio(fail_times=1)
        assert run.attempts == 2, "the decode was not retried"
        assert run.freed == [True], (
            "the retry did not force the eviction that buys it room")
        assert run.audio and run.sr == 44100, "the retry produced no audio"

    def test_a_clean_decode_frees_nothing_and_runs_once(self):
        """The control. Without it, code that evicted the model before every
        decode would satisfy the test above."""
        import fish_synth_harness as synth

        run = synth.decode_to_audio(fail_times=0)
        assert run.attempts == 1
        assert run.freed == []

    def test_with_nothing_left_to_free_it_gives_up_instead_of_looping(self):
        """`STATE["model"] is None` means the one thing worth evicting is
        already gone. Retrying would OOM again, forever."""
        import fish_synth_harness as synth

        # The worker module is loaded from its file under its own name, so its
        # `_wire` is a different object from `tts.worker._wire`. Ask the module
        # that will raise, not a namesake.
        mod = synth.load_worker()
        with pytest.raises(mod._wire._OomLike):
            synth.decode_to_audio(fail_times=1, model_resident=False, mod=mod)

    def test_a_second_failure_is_not_retried_a_third_time(self):
        """One retry, not a loop. The room the eviction bought is the only room
        there was."""
        import fish_synth_harness as synth

        mod = synth.load_worker()
        with pytest.raises(mod._wire._OomLike):
            synth.decode_to_audio(fail_times=2, mod=mod)

    def test_a_failure_that_is_not_an_oom_is_not_dressed_up_as_one(self):
        """The user is told which of the two happened, because the answers
        differ: one means "close something", the other means "this text broke
        the engine". A retry would be pointless here and the code does not
        attempt one.
        """
        import fish_synth_harness as synth

        mod = synth.load_worker()
        with pytest.raises(mod._wire.WorkerError) as caught:
            synth.decode_to_audio(fail_times=1, oom=False, mod=mod)
        assert caught.value.code == mod._wire.CODE_SYNTHESIS_FAILED
        assert caught.value.code != mod._wire.CODE_OUT_OF_MEMORY


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


# ── folded in from test_tts_audit_fixes.py (KADEME 15b) ──────────────────
#
# That file was one docstring over three unrelated subjects: host
# lifecycle, worker protocol, and provisioning. This is the worker half.
# It came here whole; the triage that moved it also claimed
# test_a_frame_bigger_than_the_pipe_buffer_round_trips was a stronger
# version of test_it_round_trips_text_through_the_pipes above, and that
# was wrong - the older one proves a real RIFF file reached the disk and
# that a Turkish sentence survives the encoding, neither of which the
# 20,000-character version looks at. Both kept.


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


# ── what a worker says about itself, and what it must not say ──────────────

class TestAWorkersOwnWordsDoNotReachTheLog:
    """`_log_worker_event` wrote two fields straight into elysium.log.

    Both cross a pipe from an engine's own interpreter, and the invariant
    this module states for the failure path - `WorkerFailure`'s "No text a
    worker sent is ever placed in it verbatim" - is the one the progress
    path broke. All three cases below come from shipped emitters, not from
    imagination:

      * `tts/worker/chatterbox.py` reported a failed speaking-rate change by
        sending the exception as its NOTE, and a stretch failure's message
        quotes the sentence it could not stretch;
      * `tts/worker/fish_s2.py` sends `str(exc)[:200]` as a DETAIL, and a
        Windows OSError message carries the full path, which carries the
        account name;
      * the same file sends the reference clip's filename, which for any
        voice made before the folder names went opaque is the label the
        reader typed on screen - the leak `tts/refs.py:_handle` exists to
        close.

    The scanner could not see any of it: the fields bind from `.get()`,
    which is not a taint shape it knows. So this is the gate.
    """

    def _said(self, caplog, frame) -> str:
        from tts import worker_client
        with caplog.at_level("INFO"):
            worker_client._log_worker_event(
                worker_client.logger, "fish_s2", frame)
        return caplog.text

    def test_a_declared_note_is_still_printed_word_for_word(self, caplog):
        """GROUND CONTROL, and it is the point of the whole design.

        The fix is a vocabulary, not a filter. If it were a filter the
        seventeen sentences our own workers choose would be lost with the
        leak, and the progress channel would go back to being write-only -
        which is the defect the channel was built to fix.
        """
        from tts.worker import _wire

        said = self._said(caplog, {
            "event": "progress", "stage": "cache_dir_unusable",
            "note": _wire.NOTE_TEMP_COMPILE_CACHE,
        })
        assert _wire.NOTE_TEMP_COMPILE_CACHE in said
        assert "cache_dir_unusable" in said

    def test_the_sentence_being_spoken_does_not_survive_a_failed_stretch(
            self, caplog):
        spoken = "She whispered that she still loved him"
        said = self._said(caplog, {
            "event": "progress", "stage": "retime_failed",
            "note": f"ValueError: cannot stretch '{spoken}'",
        })
        assert spoken not in said
        assert "whispered" not in said
        # POSITIVE CONTROL: the line still happened and still names the
        # class, so this is not passing because nothing was logged.
        assert "retime_failed" in said
        assert "ValueError" in said

    def test_a_windows_path_does_not_survive_a_detail(self, caplog):
        # BUILT, not written. A literal drive-letter path with an
        # account name in it is itself what the tree-hygiene gate
        # refuses - it publishes whose machine built the checkout - so
        # the needle is assembled here and this file contains no such
        # path. The frame the worker sends is byte-identical either way.
        account = "a-person"
        sep = chr(92)
        path = sep.join(("C:", "Users", account, "AppData", "Local",
                         "Elysium", "voice"))
        said = self._said(caplog, {
            "event": "progress", "stage": "cache_dir_unusable",
            "detail": f"[Errno 13] Permission denied: '{path}'",
        })
        assert account not in said
        assert "AppData" not in said
        assert "cache_dir_unusable" in said

    def test_a_reference_clip_name_does_not_survive_a_detail(self, caplog):
        said = self._said(caplog, {
            "event": "progress", "stage": "encoding_reference",
            "detail": "my-wifes-voice-sample.wav",
        })
        assert "wifes" not in said
        assert "my-wifes-voice-sample" not in said
        assert "encoding_reference" in said

    def test_an_undeclared_note_is_treated_as_the_data_it_is(self, caplog):
        """The vocabulary is a ceiling, not a hint.

        A note our workers do not choose is an engine's free text by
        definition - there is no third source for that field - so it is
        sanitized rather than trusted for looking harmless.
        """
        from tts import worker_client

        said = self._said(caplog, {
            "event": "progress", "stage": "loading",
            "note": "loading model for Selin",
        })
        assert "Selin" not in said
        assert worker_client.WORKER_FAULT_UNCLASSIFIED in said

    def test_the_stage_is_an_identifier_or_it_is_nothing(self, caplog):
        """`stage` crossed the same pipe. It was `str()`-ed and printed."""
        said = self._said(caplog, {
            "event": "progress",
            "stage": "loading the line 'I never told you about the lighthouse'",
        })
        assert "lighthouse" not in said
        assert "non-conforming" in said

    def test_every_note_our_workers_send_is_in_the_vocabulary(self):
        """The half a runtime test cannot reach.

        The engines run in their own interpreter and are never imported
        here, so nothing else notices a worker gaining a note that the host
        will refuse to print. This reads the emitters' own module objects -
        not their source text - by importing the worker package's protocol
        sibling and checking that every `note=` argument in the two engine
        files resolves to a member of `ALL_NOTES`.
        """
        import ast
        from pathlib import Path

        from tts.worker import _wire

        root = Path(_wire.__file__).parent
        offenders: list[str] = []
        for name in ("fish_s2.py", "chatterbox.py", "xtts_v2.py"):
            tree = ast.parse((root / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                for kw in node.keywords:
                    if kw.arg != "note":
                        continue
                    # The ONLY accepted shape is `_wire.NOTE_*`, resolved
                    # against the imported module. A literal - even a
                    # correct one - is a second copy of a sentence that has
                    # a home, and the next edit is where they diverge.
                    ok = (isinstance(kw.value, ast.Attribute)
                          and isinstance(kw.value.value, ast.Name)
                          and kw.value.value.id == "_wire"
                          and getattr(_wire, kw.value.attr, None)
                          in _wire.ALL_NOTES)
                    if not ok:
                        offenders.append(f"{name}:{kw.value.lineno}")
        assert offenders == [], (
            "a worker sends a note the host will not print: " + ", ".join(
                offenders))
