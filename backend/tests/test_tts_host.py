"""V3 - the single load slot, and never being silent about it.

One model at a time, because the card holds one. Every path out of that slot is
tested here: refusing before spawning when it will not fit, refusing to run at
all when the engine was never set up, unloading when the vault locks, unloading
when nobody has spoken for a while, and - the one that decides whether this
feature feels broken - saying so out loud when the worker dies on its own.
"""
import sys
import time
from pathlib import Path

import pytest

import config
from tts import host as tts_host
from tts import runtimes, vram
from tts.base import DetectedModel
from tts.errors import (
    TTS_INSUFFICIENT_VRAM,
    TTS_MODEL_ALREADY_LOADING,
    TTS_OUT_OF_MEMORY,
    TTS_RUNTIME_MISSING,
    TTS_WORKER_CRASHED,
)
from tts.worker_client import WorkerFailure

FAKE = str(Path(__file__).resolve().parent / "fake_worker.py")


def _fake_smi(monkeypatch, *, total=16303, free=14000, used=2303):
    monkeypatch.setattr(
        vram, "_run_smi", lambda: "NVIDIA GeForce RTX 5080, %d, %d, %d\n" % (total, free, used)
    )


def _model(**kw):
    base = dict(uid="uid1", engine_id="fish_s2", name="s2-pro", path="/models/s2-pro")
    base.update(kw)
    return DetectedModel(**base)


@pytest.fixture
def host(monkeypatch, tmp_path):
    """A host whose worker is the fake, and whose runtime is this interpreter."""
    reg = tmp_path / "voice" / "runtimes.json"
    reg.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "TTS_RUNTIMES_PATH", str(reg), raising=False)
    monkeypatch.setattr(config, "TTS_CACHE_DIR", str(tmp_path / "cache"), raising=False)
    runtimes.register("fish_s2", sys.executable)
    _fake_smi(monkeypatch)

    h = tts_host.VoiceHost()
    h.script_resolver = lambda engine_id: FAKE
    yield h
    h.unload("test teardown")


class TestTheSlot:
    def test_a_fresh_host_is_unloaded_and_says_nothing_is_wrong(self, host):
        snap = host.snapshot()
        assert snap["state"] == "unloaded"
        assert snap["error_code"] is None

    def test_loading_reports_loaded_with_the_model_that_is_in(self, host):
        host.load(_model(), {})
        snap = host.snapshot()
        assert snap["state"] == "loaded" and snap["uid"] == "uid1"

    def test_loading_the_same_model_again_is_a_no_op_not_a_reload(self, host):
        host.load(_model(), {})
        pid = host._client._proc.pid
        host.load(_model(), {})
        assert host._client._proc.pid == pid, "it reloaded a model that was already in"

    def test_loading_a_different_model_replaces_the_first(self, host):
        """The card holds one. Two resident models is how a machine ends up
        needing a reboot."""
        host.load(_model(), {})
        first = host._client
        host.load(_model(uid="uid2", path="/models/other"), {})
        assert host.snapshot()["uid"] == "uid2"
        assert not first.alive, "the previous worker was left holding VRAM"

    def test_unload_returns_to_unloaded_and_ends_the_process(self, host):
        host.load(_model(), {})
        client = host._client
        host.unload("test")
        assert host.snapshot()["state"] == "unloaded"
        assert not client.alive

    def test_unloading_when_nothing_is_loaded_is_harmless(self, host):
        host.unload("nothing to do")
        assert host.snapshot()["state"] == "unloaded"


class TestItRefusesBeforeItSpawns:
    def test_it_will_not_load_what_does_not_fit(self, host, monkeypatch):
        """A game is holding the card. Spawning anyway is how the desktop ends
        up paging VRAM and crawling - measured at ~300x slower."""
        _fake_smi(monkeypatch, free=400, used=15903)
        with pytest.raises(WorkerFailure) as exc:
            host.load(_model(), {})
        assert exc.value.code == TTS_INSUFFICIENT_VRAM
        assert host._client is None, "it spawned a worker it had already refused"

    def test_it_will_not_load_without_a_runtime(self, host, monkeypatch, tmp_path):
        monkeypatch.setattr(
            config, "TTS_RUNTIMES_PATH", str(tmp_path / "empty.json"), raising=False
        )
        with pytest.raises(WorkerFailure) as exc:
            host.load(_model(), {})
        assert exc.value.code == TTS_RUNTIME_MISSING

    def test_a_second_load_while_one_is_in_flight_is_refused_not_queued(self, host):
        """Two loads at once means two models on one card. The second caller is
        told plainly to wait, rather than silently making things worse."""
        import threading

        started = threading.Event()
        second_call_done = threading.Event()
        held: list[bool] = []
        real_start = tts_host.VoiceHost._start_worker

        def slow_start(self, *a, **kw):
            # Hold the in-flight window open for exactly as long as the second
            # caller needs, and not a moment more.
            #
            # This was `time.sleep(2.0)`, and it cost the suite two full seconds
            # every run. The sleep was not decoration - the second load has to
            # arrive while the first is genuinely mid-flight, or the test proves
            # nothing - so the fix is not a shorter sleep. A shorter sleep is a
            # narrower race that a loaded machine can lose, which trades two
            # honest seconds for an occasional false green. An event closes the
            # window on the exact fact it was waiting for.
            started.set()
            held.append(second_call_done.wait(30))
            return real_start(self, *a, **kw)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(tts_host.VoiceHost, "_start_worker", slow_start)
            t = threading.Thread(target=lambda: host.load(_model(), {}), daemon=True)
            t.start()
            assert started.wait(5)
            try:
                with pytest.raises(WorkerFailure) as exc:
                    host.load(_model(uid="uid2"), {})
                assert exc.value.code == TTS_MODEL_ALREADY_LOADING
            finally:
                second_call_done.set()
            t.join(timeout=30)

        # Asserted here rather than in the thread: an AssertionError raised in a
        # worker thread is swallowed, and the test would pass having measured a
        # first load that had already finished.
        assert held == [True], (
            "the first load was not still in flight when the second arrived, so "
            "this run proved nothing about concurrent loads"
        )


class TestSpeaking:
    def test_it_writes_audio_and_reports_where(self, host):
        host.load(_model(), {})
        res = host.speak("Merhaba canim, nasilsin?", {})
        assert Path(res["path"]).is_file()
        assert res["sample_rate"] == 44100

    def test_speaking_with_nothing_loaded_says_so(self, host):
        with pytest.raises(WorkerFailure):
            host.speak("hello", {})

    def test_speaking_refreshes_the_last_used_stamp(self, host):
        """Still reported in the snapshot, still worth keeping honest - it is
        just no longer wired to a reaper."""
        host.load(_model(), {})
        before = host._last_used
        time.sleep(0.05)
        host.speak("still here", {})
        assert host._last_used > before


class TestItIsNeverSilent:
    def test_a_worker_that_dies_on_its_own_leaves_a_visible_error(self, host):
        """Silence is the one outcome that is not allowed. The user pressed
        speak and heard nothing - the app has to be able to say why."""
        host.load(_model(), {})
        host._client._proc.kill()
        deadline = time.time() + 15
        while time.time() < deadline and host.snapshot()["state"] != "error":
            host.poll_health()
            time.sleep(0.1)
        snap = host.snapshot()
        assert snap["state"] == "error"
        assert snap["error_code"] == TTS_WORKER_CRASHED

    def test_an_oom_is_remembered_as_an_oom(self, host):
        with pytest.raises(WorkerFailure) as exc:
            host.load(_model(), {"__fake_mode": "oom"})
        assert exc.value.code == TTS_OUT_OF_MEMORY
        assert host.snapshot()["error_code"] == TTS_OUT_OF_MEMORY

    def test_the_error_clears_once_something_works_again(self, host):
        with pytest.raises(WorkerFailure):
            host.load(_model(), {"__fake_mode": "oom"})
        assert host.snapshot()["error_code"] is not None
        host.load(_model(), {})
        assert host.snapshot()["error_code"] is None


class TestItLetsGo:
    def test_locking_the_vault_unloads_the_voice(self, host):
        """While locked nothing may speak, and no sentence of the user's should
        still be sitting in a child process."""
        host.load(_model(), {})
        client = host._client
        host.on_vault_locked()
        assert host.snapshot()["state"] == "unloaded"
        assert not client.alive

    def test_an_idle_model_is_kept(self, host):
        """The reaper is GONE, and its absence is the behaviour under test.

        A timer answering "has the user gone away?" is a guess, and it was
        wrong in both directions - it threw away a model that costs 60-99 s to
        rebuild while someone was still reading, and it held the card for ten
        minutes after they really had left. Health polling now only watches."""
        host.load(_model(), {})
        for _ in range(8):
            host.poll_health()
            time.sleep(0.1)
        assert host.snapshot()["state"] == "loaded"
        assert host._client.alive

    def test_the_vault_lock_is_what_gives_the_card_back(self, host):
        """An act, not an inference - and immediate, because the user may need
        the VRAM for something else right now."""
        host.load(_model(), {})
        client = host._client
        host.on_vault_locked()
        assert host.snapshot()["state"] == "unloaded"
        assert not client.alive

    def test_locking_does_not_wait_for_a_synthesis_to_finish(self):
        """Someone who just locked the app does not want to hear the rest of
        the sentence, and may want the card back urgently."""
        from pathlib import Path
        source = (Path(__file__).resolve().parent.parent
                  / "tts" / "host.py").read_text(encoding="utf-8")
        body = source[source.index("def unload(self"):]
        body = body[: body.index("def ")] if "def " in body[10:] else body
        assert "_inflight" not in body

    def test_process_teardown_reaches_the_current_host_exactly_once(
        self, monkeypatch, tmp_path
    ):
        """Audit-2: per-instance registration pinned every VoiceHost a test
        suite created into worker_client._TEARDOWN forever. The contract now:
        ONE module-level hook, registered by get_host(), reaching whichever
        host is current - and constructing N hosts must not grow the list."""
        from tts import worker_client as wc

        saved = list(wc._TEARDOWN)
        wc._TEARDOWN.clear()
        hooked = tts_host._TEARDOWN_HOOKED
        monkeypatch.setattr(tts_host, "_TEARDOWN_HOOKED", False, raising=False)
        try:
            tts_host.VoiceHost()
            tts_host.VoiceHost()
            assert wc._TEARDOWN == [], "instances must not self-register"

            first = tts_host.get_host()
            tts_host.get_host()
            assert len(wc._TEARDOWN) == 1, "one hook for the process, ever"

            # And the hook reaches the CURRENT host, not a captured one.
            calls = []
            monkeypatch.setattr(first, "_teardown", lambda grace=1.0: calls.append(grace))
            wc.hard_close(0.5)
            assert calls == [0.5]
        finally:
            wc._TEARDOWN.clear()
            wc._TEARDOWN.extend(saved)
            monkeypatch.setattr(tts_host, "_TEARDOWN_HOOKED", hooked, raising=False)


class TestWorkerScriptResolution:
    def test_in_a_dev_checkout_it_points_at_the_package(self):
        path = tts_host.worker_script("fish_s2")
        assert path.name == "fish_s2.py" and "worker" in str(path)

    def test_in_a_frozen_build_it_points_inside_the_bundle(self, monkeypatch, tmp_path):
        """A onefile exe extracts its data files to _MEIPASS at launch; the
        engine's interpreter needs a real file on disk to run."""
        bundle = tmp_path / "_MEI123" / "tts_worker"
        bundle.mkdir(parents=True)
        (bundle / "fish_s2.py").write_text("# stub", encoding="utf-8")
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "_MEI123"), raising=False)
        assert tts_host.worker_script("fish_s2") == bundle / "fish_s2.py"


# ── The host must say WHICH model is coming up, while it comes up ──────────
#
# The identity used to be written only on success, so for the whole (long) load
# /tts/active could not match the requested uid and answered "unloaded" - while
# the model was in fact filling the card. Every voice control stayed in its
# ready face instead of saying "still loading", and the readiness check counted
# our own in-flight allocation as somebody else's, announcing "Not enough GPU
# memory to load this voice model" about the load in progress. Both were
# observed live: 90 seconds of red error while the load ran to success.


def test_the_loading_uid_is_published_before_the_load_finishes():
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent
              / "tts" / "host.py").read_text(encoding="utf-8")
    head = source[source.index("self._state = STATE_LOADING"):]
    body = head[: head.index("client = None")]
    assert "self._uid = model.uid" in body, (
        "the host does not publish which model is loading"
    )
    assert "self._engine_id = model.engine_id" in body


def test_a_refused_load_still_restores_the_previous_identity():
    """Publishing early is only safe because the failure path puts it back."""
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent
              / "tts" / "host.py").read_text(encoding="utf-8")
    assert "prior_uid, prior_engine, prior_vram, prior_alive = prior" in source
    assert "self._uid = prior_uid" in source


# ── Unloading a model must not delete the audio being played ────────────────
#
# The idle reaper (TTS_IDLE_UNLOAD_S = 600) comes through unload(), which used
# to wipe every wav in the cache. A model that unloaded while the user was
# listening deleted the file mid-playback: the browser's in-flight request for
# the rest of it failed and the sentence stopped mid-word, with nothing
# anywhere to explain it. VRAM and privacy have different lifetimes.


def _host_source() -> str:
    from pathlib import Path
    return (Path(__file__).resolve().parent.parent
            / "tts" / "host.py").read_text(encoding="utf-8")


def test_unload_does_not_wipe_the_audio_cache():
    source = _host_source()
    body = source[source.index("def unload(self"):]
    body = body[: body.index("def _drop_client")]
    assert "self.wipe_cache()" not in body, (
        "unloading still deletes audio that may be playing"
    )


def test_the_lock_path_still_wipes():
    """The privacy promise is kept where it is actually made."""
    source = _host_source()
    body = source[source.index("def on_vault_locked"):]
    body = body[: body.index("def _teardown")]
    assert "self.wipe_cache()" in body


def test_wipe_cache_still_exists_for_the_callers_that_need_it():
    from tts.host import VoiceHost
    assert callable(VoiceHost.wipe_cache)
