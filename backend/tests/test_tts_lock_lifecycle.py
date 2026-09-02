"""The vault lock owns the model's lifetime. Nothing else does.

There used to be an idle timer: ten minutes of silence and the model was
reaped. That is a GUESS about whether the user has gone away, and it was wrong
in both directions - it threw away a model that costs 60-99 s to rebuild while
someone was still reading the reply, and it went on holding the card for ten
minutes after they really had left.

The lock says the same thing without guessing, because it is an act rather than
an inference: locked means gone, unlocked means back.

Audit KÖK 13: eight of the nine tests here read config.py / host.py / vault.py
as TEXT. One asserted that a call string appeared somewhere in a file - which
would have passed with that call inside an `if False:`, in a docstring, or in a
dead branch.

KADEME S03 finishes the job. The audit's carve-out - "a source-text test may
pin a DELETION" - was still a source-text test, and it was still weak: a grep
for the absent idle-unload setting passes with the identical ten-minute
reaper hard-coded, or renamed, or read off an env overlay the .py file never
mentions. Every read of production source is gone from this file. The deleted
policy is now pinned by driving the pulse: no name in the live config
namespace is shaped like an idle timer, and no VALUE of any TTS setting makes
the pulse let go of a model that is merely idle.
"""
import threading
import time
from pathlib import Path

import pytest

import config


class _FakeClient:
    """Just enough of a worker client for the host's lifecycle to be real."""

    def __init__(self):
        self.closed = False

    def request(self, *a, **k):
        return {}

    def close(self, *a, **k):
        self.closed = True

    def poll(self, *a, **k):
        return None

    @property
    def alive(self):
        return not self.closed


@pytest.fixture()
def host(tmp_path, monkeypatch):
    from tts.host import VoiceHost

    monkeypatch.setattr(config, "TTS_CACHE_DIR", str(tmp_path / "cache"))
    return VoiceHost()


def _loaded(host, uid="u"):
    """Put the host in the state a lock has to undo, without a GPU."""
    host._client = _FakeClient()
    host._state = "loaded"
    host._uid = uid
    return host._client


def _cached_audio(count=3) -> Path:
    cache = Path(config.TTS_CACHE_DIR)
    cache.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        (cache / f"speak-{i}-{i}.wav").write_bytes(b"RIFF")
    return cache


# ── the timer is gone, and must stay gone ──────────────────────────────────
# KADEME S03. Two tests here used to read config.py and host.py as TEXT.
#
#   * one asserted the idle-unload setting's NAME was absent from the
#     text of config.py.
#     A name being absent is not a policy being absent: the same ten-minute
#     reaper reintroduced as TTS_MODEL_TTL_S, or hard-coded with no knob at
#     all, passed that assertion untouched.
#   * one asserted two comment blocks still contained the words "idle
#     unload". Comments do not run. That assertion measured nothing a user
#     could ever experience, and it is DELETED rather than converted - there
#     is no behaviour to drive for prose. The rationale it guarded has four
#     copies (config.py beside TTS_LOAD_TIMEOUT_S, host.py where the reaper
#     used to be, this module's docstring, and the decision ledger),
#     so deleting the assertion loses no knowledge, only a false guard.
#
# What replaces them drives the host: no NAME in the config namespace looks
# like an idle timer, and - the part that actually matters - no VALUE of any
# TTS setting makes the pulse let go of a model that is merely idle.

_IDLE_TIMER_WORDS = ("IDLE", "REAP", "EVICT")


def _idle_timer_knobs(namespace) -> list[str]:
    """Public names in `namespace` shaped like a knob for reaping idle models.

    Reads the imported module's LIVE namespace, not its source text: a knob
    injected by an env overlay or a settings merge is caught here and would
    not be caught by a grep of the .py file. Shared with the positive control
    below so the control proves THIS predicate fires, not a copy of it.
    """
    return sorted(
        name for name in dir(namespace)
        if not name.startswith("_")
        and any(word in name.upper() for word in _IDLE_TIMER_WORDS)
    )


class _ConfigThatBroughtTheTimerBack:
    """Stand-in for a config module that grew the knob back."""

    TTS_IDLE_UNLOAD_S = 600
    TTS_LOAD_TIMEOUT_S = 180


class TestTheTimerIsGone:
    def test_the_setting_no_longer_exists(self):
        """Not set to zero - GONE. A disabled knob is a knob someone turns back
        on without knowing why it was disabled."""
        assert not hasattr(config, "TTS_IDLE_UNLOAD_S"), (
            "the idle-unload knob is back on the config module - the timer "
            "that reaps a model someone is still listening to has a switch "
            "again"
        )
        assert _idle_timer_knobs(config) == [], (
            "config grew a setting shaped like an idle/reap/evict timer: "
            f"{_idle_timer_knobs(config)}. The vault lock decides the model's "
            "lifetime; a clock does not get a vote."
        )

    def test_the_scan_for_an_idle_knob_can_actually_fire(self):
        """POSITIVE CONTROL for the assertion above.

        An empty list is what a working scan and a broken scan both return.
        This runs the same predicate over a namespace that HAS the knob, so
        an empty result above means absent rather than unlooked-for."""
        found = _idle_timer_knobs(_ConfigThatBroughtTheTimerBack)
        assert found == ["TTS_IDLE_UNLOAD_S"], (
            "the idle-knob scan cannot see an idle knob that is right in "
            f"front of it (got {found}) - every absence it reports elsewhere "
            "in this class is therefore worthless"
        )

    def test_no_tts_setting_at_any_value_reaps_an_idle_model(
        self, host, monkeypatch,
    ):
        """The behaviour the deleted grep stood in for, stated properly: there
        is no number anywhere in the TTS config that turns the pulse back into
        a reaper.

        A reaper hiding behind a knob whose default happens to be generous
        passes every test that only exercises the shipped values. This one
        walks every numeric TTS setting and drives the pulse at 0 and at 1
        with the model a full day idle."""
        client = _loaded(host)
        host._last_used = time.monotonic() - 24 * 60 * 60

        # GROUND CONTROL: the sweep is worthless if it starts unloaded, and
        # equally worthless if it finds nothing to sweep.
        assert host.snapshot()["state"] == "loaded", (
            "ground control failed: the host was not loaded before the sweep, "
            "so 'still loaded' afterwards would prove nothing"
        )
        knobs = [
            name for name in dir(config)
            if name.startswith("TTS_")
            and isinstance(getattr(config, name), (int, float))
            and not isinstance(getattr(config, name), bool)
        ]
        assert len(knobs) >= 5, (
            f"ground control failed: only {len(knobs)} numeric TTS settings "
            "found to sweep - the sweep is not reaching the config surface"
        )

        for name in knobs:
            for value in (0, 1):
                with monkeypatch.context() as m:
                    m.setattr(config, name, value)
                    for _ in range(3):
                        host.poll_health()
                assert host.snapshot()["state"] == "loaded", (
                    f"config.{name}={value} made the pulse unload a model "
                    "that was only idle - a clock decides the model's fate "
                    "again, and it costs 60-99 s to rebuild"
                )
                assert client.closed is False, (
                    f"config.{name}={value} killed the worker process behind "
                    "an idle model - the VRAM went back without anyone asking"
                )

    def test_nothing_in_the_pulse_lets_go_of_a_merely_idle_model(
        self, host, monkeypatch,
    ):
        """Instrumented at the two methods any reaper has to go through.

        Reading the snapshot alone would miss a reaper that unloads and
        reloads within the same tick, or one that drops the worker while
        leaving the state field looking loaded. The spies see the call
        itself."""
        client = _loaded(host)
        host._last_used = time.monotonic() - 24 * 60 * 60

        let_go: list[str] = []
        real_unload = host.unload
        real_drop = host._drop_client

        def spy_unload(reason: str = ""):
            let_go.append(f"unload({reason!r})")
            return real_unload(reason)

        def spy_drop(reason: str, grace: float = 2.0):
            let_go.append(f"_drop_client({reason!r})")
            return real_drop(reason, grace)

        monkeypatch.setattr(host, "unload", spy_unload)
        monkeypatch.setattr(host, "_drop_client", spy_drop)

        for _ in range(5):
            host.poll_health()

        assert let_go == [], (
            f"the health pulse let go of an idle model: {let_go}. poll_health "
            "watches; the vault lock is the only thing that reaps."
        )
        assert host.snapshot()["state"] == "loaded", (
            "an idle model was reaped - the timer is back in some form"
        )
        assert client.closed is False, (
            "the worker holding the model was closed while merely idle"
        )

        # POSITIVE CONTROL: the empty list above proves nothing unless these
        # exact spies can be seen firing. The vault lock is the one path that
        # IS allowed to let go, so it is the honest trigger.
        host.on_vault_locked()
        assert let_go, (
            "the spies did not fire even on a vault lock, which unloads by "
            "definition - so they were never attached to the reaping path "
            "and the assertion above was vacuous"
        )

    def test_health_polling_does_not_decide_the_models_fate(self, host):
        """poll_health watches; it does not reap. Driven, because the old
        version of this test read the function body as text and would have
        passed on an unload call it could not see."""
        client = _loaded(host)
        host._last_used = time.monotonic() - 10 * 60 * 60   # idle for hours

        for _ in range(3):
            host.poll_health()

        assert host.snapshot()["state"] == "loaded", (
            "an idle model was reaped - the timer is back in some form"
        )
        assert client.closed is False, (
            "the worker was closed on a health poll - the pulse reaped what "
            "it is only supposed to watch"
        )


# ── the lock gives the card back ───────────────────────────────────────────


class TestTheLockGivesTheCardBack:
    def test_locking_unloads_and_wipes(self, host):
        client = _loaded(host)
        cache = _cached_audio()

        host.on_vault_locked()

        assert host.snapshot()["state"] == "unloaded", (
            "the vault reported itself locked while the model was still "
            "resident - the card was never given back"
        )
        assert client.closed is True, (
            "the worker process survived the lock, still holding the VRAM "
            "and the user's last sentence in its memory"
        )
        assert not list(cache.glob("speak-*.wav")), (
            "the conversation stayed on disk as audio while the vault showed "
            "locked - the opposite of what locking promises"
        )

    def test_locking_does_not_wait_for_speech_in_flight(self, host):
        """Someone who just locked the app does not want to hear the rest of
        the sentence, and may want the VRAM back this second - they could be in
        a game. No grace period, by explicit decision.

        Measured rather than grepped for the absence of `sleep`: what matters
        is that it returns promptly with work outstanding, not which mechanism
        it avoids."""
        _loaded(host)
        host._inflight = 2                      # a sentence is being synthesised

        started = time.monotonic()
        host.on_vault_locked()
        elapsed = time.monotonic() - started

        assert elapsed < 1.0, f"locking waited {elapsed:.1f}s for speech"
        assert host.snapshot()["state"] == "unloaded", (
            "locking returned without unloading because speech was in "
            "flight - the sentence outranked the lock"
        )

    def test_the_lock_path_actually_reaches_the_host(self, client, monkeypatch):
        """END TO END, through the endpoint. The old version asserted that the
        string "get_host().on_vault_locked()" appeared in vault.py."""
        import tts.host as tts_host

        called = threading.Event()

        class _Spy:
            def on_vault_locked(self):
                called.set()

            def wipe_cache(self):
                return 0

        monkeypatch.setattr(tts_host, "get_host", lambda: _Spy())
        assert client.post("/api/v1/vault/lock").status_code == 200, (
            "the lock endpoint itself failed, so what follows would be "
            "measuring a broken request rather than the host wiring"
        )
        assert called.is_set(), "locking the vault never reached the voice host"


# ── unlocking brings it back ───────────────────────────────────────────────


class TestUnlockingBringsItBack:
    def test_the_preload_never_blocks_the_unlock(self, client, monkeypatch):
        """A voice that will not come up must cost the audio and nothing else.

        Driven by making the preload hang: the old test looked for the strings
        "daemon=True" and "except Exception", which a rewrite could keep while
        losing the property."""
        import routers.vault as vault_router

        release = threading.Event()

        def wedged():
            release.wait(10.0)

        monkeypatch.setattr(vault_router, "_preload_voice_model", wedged)

        started = time.monotonic()
        try:
            resp = client.post("/api/v1/vault/unlock",
                               json={"passphrase": "test-passphrase"})
            elapsed = time.monotonic() - started
        finally:
            release.set()

        assert elapsed < 5.0, f"unlock waited {elapsed:.1f}s for the voice model"
        assert resp.status_code in (200, 400, 401, 409), resp.status_code

    def test_a_preload_that_raises_does_not_break_the_unlock(
        self, client, monkeypatch,
    ):
        import routers.vault as vault_router

        def explode():
            raise RuntimeError("no GPU today")

        monkeypatch.setattr(vault_router, "_preload_voice_model", explode)
        resp = client.post("/api/v1/vault/unlock",
                           json={"passphrase": "test-passphrase"})
        assert resp.status_code in (200, 400, 401, 409), (
            f"a voice model that will not load broke the unlock itself "
            f"({resp.status_code}) - the user is locked out of their own "
            "vault because a GPU is busy"
        )

    def test_the_preload_loads_only_the_chosen_model(self, client, monkeypatch):
        """Not everything installed - the one this vault selected.

        The load itself is the observation point, not _resolve: asserting on a
        call that may legitimately not happen is how a test ends up passing
        vacuously, which is the failure this whole file is being rewritten for.
        """
        import routers.vault as vault_router
        import routers.tts_runtime as runtime
        import tts.host as tts_host
        from database import set_setting

        loaded: list[str] = []
        done = threading.Event()

        class _Spy:
            def snapshot(self):
                return {"state": "unloaded", "uid": None}

            def load(self, model, values):
                loaded.append(model.uid)
                done.set()
                return {}

        monkeypatch.setattr(tts_host, "get_host", lambda: _Spy())
        monkeypatch.setattr(runtime, "_resolve", lambda uid: _stub_model(uid))
        monkeypatch.setattr(runtime, "_values_for", lambda model: {})
        set_setting(runtime.SETTING_ACTIVE_UID, "chosen-uid")

        vault_router._preload_voice_model()

        assert done.wait(5.0), "the preload never reached the host"
        assert loaded == ["chosen-uid"], (
            f"the preload warmed {loaded!r} instead of the one model this "
            "vault selected"
        )

    def test_no_selection_means_no_preload_at_all(self, client, monkeypatch):
        """A vault with no voice model chosen must not warm anything up."""
        import routers.vault as vault_router
        import routers.tts_runtime as runtime
        import tts.host as tts_host
        from database import set_setting

        loaded: list[str] = []

        class _Spy:
            def snapshot(self):
                return {"state": "unloaded", "uid": None}

            def load(self, model, values):
                loaded.append(model.uid)
                return {}

        monkeypatch.setattr(tts_host, "get_host", lambda: _Spy())
        set_setting(runtime.SETTING_ACTIVE_UID, "")

        vault_router._preload_voice_model()
        time.sleep(0.3)

        assert loaded == [], (
            f"a vault with no voice model chosen still warmed {loaded!r} - "
            "VRAM taken for something the user never asked for"
        )


def _stub_model(uid="chosen-uid"):
    from tts.base import DetectedModel

    return DetectedModel(uid=uid, engine_id="fish_s2", name="m", path="/m")
