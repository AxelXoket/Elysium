"""V3 - the single load slot, and never being silent about it.

One model at a time, because the card holds one. Every path out of that slot is
tested here: refusing before spawning when it will not fit, refusing to run at
all when the engine was never set up, unloading when the vault locks, unloading
when nobody has spoken for a while, and - the one that decides whether this
feature feels broken - saying so out loud when the worker dies on its own.
"""
import sys
import threading
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
    TTS_WORKER_UNAVAILABLE,
)
# Imported, never retyped: a test that spells "loading" out by hand goes on
# agreeing with itself after the host stops using that word.
from tts.host import STATE_ERROR, STATE_LOADED, STATE_LOADING
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

    # test_the_vault_lock_is_what_gives_the_card_back was deleted in KADEME
    # 20b: line for line the same body as test_locking_the_vault_unloads_the_
    # voice a few tests above - same load, same client capture, same two
    # assertions. Only the docstring differed ("An act, not an inference - and
    # immediate, because the user may need the VRAM for something else right
    # now"), which is prose about the same fact, kept here so the sentence
    # survives the duplicate.

    # test_locking_does_not_wait_for_a_synthesis_to_finish was deleted in
    # KADEME 20b. It asserted on an EMPTY STRING and could never fail:
    # `body.index("def ")` returned 0 because the slice already began at
    # `def unload(self`, so `body` was "" and `"_inflight" not in ""` was
    # vacuously true. The claim it meant to make is measured, not grepped,
    # by test_tts_lock_lifecycle.py::test_locking_does_not_wait_for_speech_
    # in_flight, which sets `_inflight = 2` and times the lock.

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


class TestTheHostSaysWhichModelIsComingUp:
    """These three replaced source-text scans (KADEME 21).

    The old pair sliced `tts/host.py`'s own text between two landmarks and
    asserted `"self._uid = model.uid"` appeared inside the slice, and that
    `"self._uid = prior_uid"` appeared anywhere in the file. Both pass on a
    line that is dead, unreachable, commented out inside a string, or that
    assigns the right name in the wrong order - which is the whole reason the
    rule exists. They are driven now: a load is held open mid-flight and the
    snapshot is read from ANOTHER thread, exactly as `/tts/active` reads it
    while the model comes up, and a refusal is fired at a host that already
    has a model resident.
    """

    def test_the_loading_uid_is_published_before_the_load_finishes(self, host):
        """What `/tts/active` can see WHILE the card is filling.

        The identity used to be written only on success, so for the whole
        (60-99 s) load the snapshot answered uid=None: every voice control
        stayed in its ready face instead of "still loading", and the readiness
        check counted our own in-flight allocation as somebody else's,
        announcing "Not enough GPU memory to load this voice model" about the
        load in progress. Observed live as 90 seconds of red error while the
        load ran to success.

        The hold sits in `check_fit`, which is the FIRST thing `load()` does
        after the state block - so a pass here says the identity is public
        before any preflight work, not merely before the worker spawns.
        """
        model = _model()

        # GROUND CONTROL. Nothing is published before the load starts, so a
        # mid-load uid of "uid1" cannot be something that was already there.
        before = host.snapshot()
        assert before["uid"] is None and before["engine_id"] is None, (
            "ground control failed: the fresh host already claimed an "
            "identity, so this test could not tell publication from leftovers")

        arrived = threading.Event()
        may_finish = threading.Event()
        held: list[bool] = []
        real_fit = tts_host.check_fit

        def hold_the_load_open(*a, **kw):
            arrived.set()
            held.append(may_finish.wait(30))
            return real_fit(*a, **kw)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(tts_host, "check_fit", hold_the_load_open)
            t = threading.Thread(target=lambda: host.load(model, {}),
                                 daemon=True)
            t.start()
            try:
                assert arrived.wait(10), (
                    "the load never reached the preflight, so nothing was "
                    "observed mid-flight and this run proved nothing")
                # Read from the MAIN thread while the loading thread is parked
                # inside check_fit: this is a concurrent reader, the same as
                # the status route.
                mid = host.snapshot()
            finally:
                may_finish.set()
            t.join(timeout=30)

        assert held == [True], (
            "the load was not still in flight when the snapshot was taken, so "
            "this run measured a finished load rather than a running one")
        assert mid["state"] == STATE_LOADING, (
            f"the host reported {mid['state']!r} while a model was coming up, "
            f"so the UI cannot show a loading face")
        assert mid["uid"] == model.uid, (
            f"mid-load the host reported uid {mid['uid']!r} instead of "
            f"{model.uid!r}: /tts/active cannot match the requested model, so "
            f"every voice control sits in its ready face and the readiness "
            f"check blames our own in-flight allocation on somebody else")
        assert mid["engine_id"] == model.engine_id, (
            f"mid-load the host reported engine {mid['engine_id']!r} instead "
            f"of {model.engine_id!r}, so nothing can tell WHICH engine is "
            f"occupying the card")

        # And the held load really did go on to succeed - the publication is
        # early, not a substitute for finishing.
        done = host.snapshot()
        assert done["state"] == STATE_LOADED and done["uid"] == model.uid

    def test_a_refused_load_still_restores_the_previous_identity(
        self, host, monkeypatch
    ):
        """Publishing early is only safe because the failure path puts it back.

        A refusal happens BEFORE `_drop_client`, so the previously loaded model
        is untouched and still holding VRAM. If the early publish is not undone
        the app reports "nothing loaded" while a process owns the card:
        invisible in the UI, and to anyone wondering where the memory went.
        """
        host.load(_model(), {})
        resident = host.snapshot()
        assert resident["state"] == STATE_LOADED and resident["uid"] == "uid1", (
            "ground control failed: there was no resident model to preserve")
        assert resident["vram_mb"] is not None, (
            "ground control failed: the resident model reported no VRAM "
            "figure, so a wiped one would look identical to a kept one")

        # A game grabs the card between the two loads.
        _fake_smi(monkeypatch, free=400, used=15903)
        with pytest.raises(WorkerFailure) as exc:
            host.load(_model(uid="uid2", path="/models/other"), {})
        assert exc.value.code == TTS_INSUFFICIENT_VRAM

        assert host._client is not None and host._client.alive, (
            "ground control failed: the refusal ended the resident worker, so "
            "there was nothing left for the snapshot to be wrong about")
        after = host.snapshot()
        for key in ("state", "uid", "engine_id", "vram_mb"):
            assert after[key] == resident[key], (
                f"a refused load rewrote {key}: the host now says "
                f"{after[key]!r} where the resident model is {resident[key]!r}, "
                f"so the app reports nothing loaded while a live worker holds "
                f"the card")
        assert after["error_code"] == TTS_INSUFFICIENT_VRAM, (
            "the refusal must still be reported - restoring the identity may "
            "not also swallow the reason the user was refused")

    def test_a_refusal_with_nothing_resident_claims_nothing(self, host,
                                                            monkeypatch):
        """POSITIVE CONTROL for the restore above.

        The restore must be conditional on something ACTUALLY being resident.
        A branch that always put an identity back - or one that never cleared
        it after the early publish - would pass the test above and leave a
        fresh host claiming a model it never loaded. That claim is what makes
        the UI offer a Speak button wired to no worker at all.
        """
        _fake_smi(monkeypatch, free=400, used=15903)
        with pytest.raises(WorkerFailure) as exc:
            host.load(_model(), {})
        assert exc.value.code == TTS_INSUFFICIENT_VRAM

        snap = host.snapshot()
        assert snap["state"] == STATE_ERROR, (
            f"a refusal with nothing resident left the host in "
            f"{snap['state']!r} instead of an error state")
        assert (snap["uid"], snap["engine_id"], snap["vram_mb"]) == (
            None, None, None), (
            f"the early publish survived a refusal that loaded nothing: the "
            f"host claims {snap['uid']!r} on {snap['engine_id']!r} with no "
            f"worker behind it")


# ── Unloading a model must not delete the audio being played ────────────────
#
# The idle reaper (TTS_IDLE_UNLOAD_S = 600) comes through unload(), which used
# to wipe every wav in the cache. A model that unloaded while the user was
# listening deleted the file mid-playback: the browser's in-flight request for
# the rest of it failed and the sentence stopped mid-word, with nothing
# anywhere to explain it. VRAM and privacy have different lifetimes.


# _host_source() was deleted in KADEME 21. It read tts/host.py's whole text and
# handed it back, and NOTHING called it - the last caller went with
# test_wipe_cache_still_exists_for_the_callers_that_need_it below. A dead
# source reader is not a test that needs converting, it is a loaded gun left on
# the table for the next person who wants to grep instead of drive.


# test_wipe_cache_still_exists_for_the_callers_that_need_it was deleted in
# KADEME 20b. Its whole body was `assert callable(VoiceHost.wipe_cache)`.
# A method that provably deletes two files is necessarily callable, and
# all three callers are covered end to end: test_audio_cache_launch_wipe.py
# (the method itself and the launch path), test_tts_lock_lifecycle.py
# (on_vault_locked), and test_process_teardown_wipes_the_audio_cache below.


# ── folded in from test_tts_audit_fixes.py (KADEME 15b) ──────────────────
#
# The host third of that file. Two of these REPLACE tests that used to
# live here as source-text scans: `test_unload_does_not_wipe_the_audio_cache`
# asserted the string "self.wipe_cache()" was absent from unload()'s body,
# and `test_the_lock_path_still_wipes` asserted it was present in the lock
# path. The versions below load a model, speak, and then look at the wav
# files - which is the promise, rather than the spelling of it.
#
# The triage also claimed test_a_crashed_workers_client_is_fully_closed was
# a stronger version of test_a_worker_that_dies_on_its_own_leaves_a_visible_error.
# It is not: the older one asserts the error code the user is shown, the
# newer one asserts the OS job handle was not leaked. Both kept.


def _track_clients(monkeypatch):
    """Every WorkerClient the host builds, in order.

    Travelled with the tests below rather than being left behind: a moved
    test depends on its helpers and its constants, not only on its own
    body, and this one was forgotten once already in this fold.
    """
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
    # test_process_teardown_wipes_the_audio_cache moved to
    # test_tts_host.py::TestItLetsGo (KADEME 15b). It is the proof of a
    # documented privacy claim ("wiped ... on exit"), and a proof has to
    # live where the registry in test_security_contract.py can point at
    # it and where a reader of the host tests will meet it.

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


class TestAStuckFileGetsAnotherTry:
    """K-46. The lock wipe already reports what it could not remove
    (`on_vault_locked`'s return value, `_last_wipe_left`) - what it never did
    is try AGAIN. A file Windows refuses to release (a browser tab mid-
    stream, Defender mid-scan) used to sit there, readable and playable,
    until the next unlock+speak called wipe_cache() a second time - which,
    while the vault stays locked, may be never.

    The message id in its name is not what this closes: the privacy rule
    permits a numeric id outside the vault, and nothing here disputes that.
    What it closes is the audible content surviving indefinitely - by
    piggybacking on poll_health, which already beats regardless of lock
    state (it is how a dead worker gets noticed with nobody looking at the
    UI), so a released handle is picked up within one health tick instead of
    waiting for the user to unlock and speak again.
    """

    def _speak_and_capture(self, host):
        host.load(_model(), {})
        host.speak("something private", {})
        cache = Path(config.TTS_CACHE_DIR)
        wavs = list(cache.glob("*.wav"))
        assert len(wavs) == 1
        return wavs[0]

    def test_a_released_file_is_cleared_on_the_next_health_beat(
        self, host, monkeypatch
    ):
        from tts import host as tts_host_module

        wav = self._speak_and_capture(host)
        real_shred = tts_host_module.secure_delete.shred
        released = {"now": False}

        def flaky(path):
            if Path(path).name == wav.name and not released["now"]:
                return False
            return real_shred(path)

        monkeypatch.setattr(tts_host_module.secure_delete, "shred", flaky)

        left = host.on_vault_locked()
        assert left == [wav.name], "ground: the lock really could not clear it"
        assert wav.exists(), "the reply is still audible after a locked vault"

        # Whatever was holding it lets go, and the health beat runs again -
        # exactly what happens every TTS_HEALTH_POLL_S regardless of lock
        # state, with nobody touching the UI.
        released["now"] = True
        host.poll_health()

        assert not wav.exists(), "poll_health did not retry the stuck file"
        assert host._last_wipe_left == []

    def test_a_file_that_stays_stuck_is_never_falsely_reported_cleared(
        self, host, monkeypatch
    ):
        from tts import host as tts_host_module

        wav = self._speak_and_capture(host)
        monkeypatch.setattr(
            tts_host_module.secure_delete, "shred",
            lambda path: Path(path).name != wav.name)

        host.on_vault_locked()
        host.poll_health()
        host.poll_health()  # a second beat must not misbehave either

        assert wav.exists(), "still stuck - must not have been deleted"
        assert host._last_wipe_left == [wav.name]

    def test_nothing_stuck_is_a_quiet_no_op(self, host):
        wav = self._speak_and_capture(host)
        host.on_vault_locked()
        assert host._last_wipe_left == []
        assert not wav.exists()

        host.poll_health()  # must not raise, must not invent stuck entries

        assert host._last_wipe_left == []


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


class TestStreamedAudioBecomesFindable:
    """The most common way audio is made was the one way it could not be deleted.

    A live reply has no message id while it streams: the assistant row is
    written after the last delta, deliberately. So every streamed wav was
    tagged 0, and `forget_message_audio` globs `speak-<mid>-*` and never
    matched one. Deleting the message removed the row and left the recording
    of it on disk in the clear.
    """

    def _cache(self, monkeypatch, tmp_path) -> Path:
        cache = tmp_path / "audio"
        cache.mkdir()
        monkeypatch.setattr(config, "TTS_CACHE_DIR", str(cache), raising=False)
        return cache

    def test_a_streamed_wav_is_named_after_its_stream_not_zero(
        self, monkeypatch, tmp_path,
    ) -> None:
        self._cache(monkeypatch, tmp_path)
        host = tts_host.VoiceHost()
        name = Path(host._next_out_path(None, stream_token="abc123")).name
        assert name.startswith("speak-tabc123-"), name
        assert not name.startswith("speak-0-")

    def test_a_path_with_neither_still_falls_back_to_zero(
        self, monkeypatch, tmp_path,
    ) -> None:
        # GROUND CONTROL. A preview or a probe has no message and no stream,
        # and those are still swept by age, by the lock and at launch.
        self._cache(monkeypatch, tmp_path)
        host = tts_host.VoiceHost()
        assert Path(host._next_out_path(None)).name.startswith("speak-0-")

    def test_adoption_makes_the_stream_deletable_by_message_id(
        self, monkeypatch, tmp_path,
    ) -> None:
        cache = self._cache(monkeypatch, tmp_path)
        host = tts_host.VoiceHost()

        streamed = Path(host._next_out_path(None, stream_token="tok1"))
        streamed.write_bytes(b"RIFF" + bytes(32))
        # GROUND: before adoption, the deletion machinery cannot see it. This
        # is the bug, asserted rather than described.
        host.forget_message_audio(77)
        assert streamed.exists()

        assert host.adopt_stream_audio("tok1", 77) == []
        assert not streamed.exists(), "the stream's wav kept its old name"
        adopted = list(cache.glob("speak-77-*.wav"))
        assert len(adopted) == 1

        assert host.forget_message_audio(77) == []
        assert list(cache.glob("speak-77-*.wav")) == []

    def test_adoption_takes_only_its_own_stream(
        self, monkeypatch, tmp_path,
    ) -> None:
        # POSITIVE CONTROL, and the reason a per-stream token exists at all:
        # two concurrent streams used to share one `speak-0-` pattern, so a
        # bulk rename onto one message would have taken the other's audio.
        cache = self._cache(monkeypatch, tmp_path)
        host = tts_host.VoiceHost()

        mine = Path(host._next_out_path(None, stream_token="mine"))
        mine.write_bytes(b"RIFF" + bytes(32))
        theirs = Path(host._next_out_path(None, stream_token="theirs"))
        theirs.write_bytes(b"RIFF" + bytes(32))

        host.adopt_stream_audio("mine", 5)

        assert theirs.exists(), "adoption took another stream's audio"
        assert len(list(cache.glob("speak-5-*.wav"))) == 1

    def test_adoption_refuses_a_meaningless_pair(
        self, monkeypatch, tmp_path,
    ) -> None:
        self._cache(monkeypatch, tmp_path)
        host = tts_host.VoiceHost()
        assert host.adopt_stream_audio("", 5) == []
        assert host.adopt_stream_audio("tok", 0) == []
