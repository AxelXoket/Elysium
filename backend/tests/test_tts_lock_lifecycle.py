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
dead branch. The rule this file now follows is the one the audit set: a
source-text test may pin a DELETION (a policy that must stay deleted); it may
not stand in for a BEHAVIOUR.
"""
import threading
import time
from pathlib import Path

import pytest

import config

SOURCES = {
    "config": Path(__file__).resolve().parents[1] / "config.py",
    "host": Path(__file__).resolve().parents[1] / "tts" / "host.py",
}


def _src(name):
    return SOURCES[name].read_text(encoding="utf-8")


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
# These two DO read the source, and that is the one thing a source test is
# for: proving something is still absent. There is no behaviour to drive for a
# policy that was deleted.


class TestTheTimerIsGone:
    def test_the_setting_no_longer_exists(self):
        """Not set to zero - GONE. A disabled knob is a knob someone turns back
        on without knowing why it was disabled."""
        assert not hasattr(config, "TTS_IDLE_UNLOAD_S")
        assert "TTS_IDLE_UNLOAD_S" not in _src("config")

    # KEPT in KADEME 20b, against section 4's list. Section 4 was right that
    # prose read by humans does not belong in an assertion, and proposed a
    # replacement: docs/adr/0007-no-idle-unload.md plus a pygrep hook whose
    # `name:` field becomes the durable marker. Measured: docs/adr/ does not
    # exist, no ADR was written, and no such hook is configured. Deleting
    # this today removes the only thing keeping the rationale in the tree,
    # with neither promised replacement built. It goes when they exist.
    def test_the_removal_is_explained_where_someone_would_look_for_it(self):
        """A deleted policy leaves no trace to grep for, so the reason lives at
        both places a reader would go looking."""
        assert "no idle unload" in _src("host").lower()
        assert "idle unload" in _src("config").lower()

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
        assert client.closed is False


# ── the lock gives the card back ───────────────────────────────────────────


class TestTheLockGivesTheCardBack:
    def test_locking_unloads_and_wipes(self, host):
        client = _loaded(host)
        cache = _cached_audio()

        host.on_vault_locked()

        assert host.snapshot()["state"] == "unloaded"
        assert client.closed is True
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
        assert host.snapshot()["state"] == "unloaded"

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
        assert client.post("/api/v1/vault/lock").status_code == 200
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
        assert resp.status_code in (200, 400, 401, 409)

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
        assert loaded == ["chosen-uid"], loaded

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

        assert loaded == []


def _stub_model(uid="chosen-uid"):
    from tts.base import DetectedModel

    return DetectedModel(uid=uid, engine_id="fish_s2", name="m", path="/m")
