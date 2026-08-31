"""V3 - the endpoints that own a process, over the wire.

The interesting cases here are the ones where an endpoint could quietly do
something worse than fail: registering a broken environment, serving a file
from outside the cache, removing an engine while its worker is still holding
it, or answering "loaded" when nothing is.
"""
import sys
import time
from pathlib import Path

import pytest

import config
from tts import host as tts_host
from tts import provision, runtimes, stream_hook
from tests.test_tts_core import make_fish

FAKE = str(Path(__file__).resolve().parent / "fake_worker.py")


def _point_models_at(monkeypatch, tmp_path):
    root = tmp_path / "voice" / "models"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "TTS_MODELS_DIR", str(root), raising=False)
    return root


@pytest.fixture
def voice(monkeypatch, tmp_path):
    """A fully redirected voice environment: models, cache, runtimes, worker."""
    root = tmp_path / "voice"
    (root / "envs").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "TTS_DIR", root, raising=False)
    monkeypatch.setattr(config, "TTS_ENVS_DIR", str(root / "envs"), raising=False)
    monkeypatch.setattr(config, "TTS_BIN_DIR", str(root / "bin"), raising=False)
    monkeypatch.setattr(config, "TTS_CACHE_DIR", str(root / "cache"), raising=False)
    monkeypatch.setattr(config, "TTS_RUNTIMES_PATH", str(root / "runtimes.json"),
                        raising=False)
    models = _point_models_at(monkeypatch, tmp_path)
    make_fish(models)
    provision.reset_jobs()

    host = tts_host.VoiceHost()
    host.script_resolver = lambda engine_id: FAKE
    monkeypatch.setattr(tts_host, "_HOST", host, raising=False)
    yield root
    host.unload("test teardown")
    provision.reset_jobs()


def _fake_gpu(monkeypatch, free=14000):
    from tts import vram

    monkeypatch.setattr(
        vram, "_run_smi", lambda: "NVIDIA GeForce RTX 5080, 16303, %d, 2303\n" % free
    )


class TestState:
    def test_state_answers_before_anything_is_loaded(self, client, voice):
        body = client.get("/api/v1/tts/state").json()
        assert body["state"] == "unloaded" and body["error_code"] is None

    def test_loading_without_a_runtime_is_refused_with_the_real_reason(
        self, client, voice, monkeypatch
    ):
        _fake_gpu(monkeypatch)
        uid = client.get("/api/v1/tts/models").json()["models"][0]["uid"]
        r = client.post("/api/v1/tts/load", json={"uid": uid})
        assert r.status_code == 409
        assert r.json()["detail"] == "tts_runtime_missing"

    def test_a_full_load_speak_unload_round_trip(self, client, voice, monkeypatch):
        _fake_gpu(monkeypatch)
        runtimes.register("fish_s2", sys.executable)
        uid = client.get("/api/v1/tts/models").json()["models"][0]["uid"]

        assert client.post("/api/v1/tts/load", json={"uid": uid}).status_code == 200
        assert client.get("/api/v1/tts/state").json()["state"] == "loaded"

        spoken = client.post("/api/v1/tts/speak",
                             json={"text": "Merhaba, nasilsin?", "uid": uid})
        assert spoken.status_code == 200
        audio_id = spoken.json()["audio_id"]

        audio = client.get("/api/v1/tts/audio/%s" % audio_id)
        assert audio.status_code == 200
        assert audio.content[:4] == b"RIFF"

        assert client.post("/api/v1/tts/unload").json()["state"] == "unloaded"

    def test_speaking_nothing_is_refused(self, client, voice):
        r = client.post("/api/v1/tts/speak", json={"text": "   "})
        assert r.status_code == 400

    def test_a_busy_card_is_refused_before_any_process_starts(
        self, client, voice, monkeypatch
    ):
        _fake_gpu(monkeypatch, free=300)
        runtimes.register("fish_s2", sys.executable)
        uid = client.get("/api/v1/tts/models").json()["models"][0]["uid"]
        r = client.post("/api/v1/tts/load", json={"uid": uid})
        assert r.status_code == 409
        assert r.json()["detail"] == "tts_insufficient_vram"


class TestAudioIsConfined:
    def test_a_traversing_id_cannot_reach_outside_the_cache(self, client, voice):
        for bad in ["..%2F..%2Fapp", "....", "a/b", "%2e%2e"]:
            r = client.get("/api/v1/tts/audio/%s" % bad)
            assert r.status_code == 404

    def test_an_unknown_id_is_a_clean_404(self, client, voice):
        assert client.get("/api/v1/tts/audio/nothing-here").status_code == 404


class TestSetupOverTheWire:
    def test_the_plan_is_shown_before_the_download_starts(self, client, voice):
        body = client.get("/api/v1/tts/runtimes/fish_s2/plan").json()
        assert body["download_mb"] > 1000
        assert body["python_version"]

    def test_an_unknown_engine_has_no_setup(self, client, voice):
        r = client.get("/api/v1/tts/runtimes/not_real/plan")
        assert r.status_code == 400 and r.json()["detail"] == "tts_engine_unknown"

    def test_setup_status_is_readable_before_anything_has_run(self, client, voice):
        body = client.get("/api/v1/tts/runtimes/fish_s2/install").json()
        assert body["state"] == "idle" and body["running"] is False

    def test_setup_reports_a_real_reason_when_it_cannot_start(
        self, client, voice, monkeypatch
    ):
        """Reported by the JOB now, not by the POST.

        The ~25 MB uv fetch used to run inline on the request thread, so this
        POST did not answer until it finished (the frontend sets no fetch
        timeout) while the job already reported running=true and the UI drew a
        Cancel button that could not reach the download. The reason still
        reaches the user, through the channel that can also carry a cancel.
        """
        import time

        monkeypatch.setattr(provision, "find_uv", lambda: None)
        monkeypatch.setattr(provision, "_download_uv", lambda *a, **k: None)
        r = client.post("/api/v1/tts/runtimes/fish_s2/install")
        assert r.status_code == 200

        deadline = time.time() + 20.0
        while time.time() < deadline:
            body = client.get("/api/v1/tts/runtimes/fish_s2/install").json()
            if not body["running"]:
                break
            time.sleep(0.05)
        assert body["state"] == "failed"
        assert body["error_code"] == "tts_python_not_found"
        assert body["error_detail"]

    def test_cancelling_nothing_is_harmless(self, client, voice):
        assert client.post("/api/v1/tts/runtimes/fish_s2/install/cancel").status_code == 200

    def test_removing_an_engine_unloads_its_worker_first(
        self, client, voice, monkeypatch
    ):
        """Deleting an environment out from under a live worker is how a loaded
        .pyd turns into "Access is denied" and a half-removed install."""
        _fake_gpu(monkeypatch)
        runtimes.register("fish_s2", sys.executable)
        uid = client.get("/api/v1/tts/models").json()["models"][0]["uid"]
        client.post("/api/v1/tts/load", json={"uid": uid})
        client_obj = tts_host.get_host()._client
        assert client_obj is not None and client_obj.alive

        assert client.delete("/api/v1/tts/runtimes/fish_s2").status_code == 200
        assert not client_obj.alive
        assert runtimes.status("fish_s2").state == "missing"


class TestLockingStopsTheVoice:
    def test_locking_the_vault_unloads_and_wipes_the_audio(
        self, client, voice, monkeypatch
    ):
        """Audio of the conversation must not sit in the clear beside a
        database that went to the trouble of being encrypted."""
        import vault_state
        from conftest import TEST_VAULT_KEY

        _fake_gpu(monkeypatch)
        runtimes.register("fish_s2", sys.executable)
        uid = client.get("/api/v1/tts/models").json()["models"][0]["uid"]
        client.post("/api/v1/tts/load", json={"uid": uid})
        client.post("/api/v1/tts/speak", json={"text": "a secret", "uid": uid})
        cache = Path(config.TTS_CACHE_DIR)
        assert list(cache.glob("*.wav"))

        try:
            client.post("/api/v1/vault/lock")
            assert not list(cache.glob("*.wav")), "spoken audio survived the lock"
            assert tts_host.get_host().snapshot()["state"] == "unloaded"
        finally:
            vault_state.set_key(TEST_VAULT_KEY)


def _wav_bytes(seconds=8.0, rate=44100):
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


class TestReferenceVoicesOverTheWire:
    @pytest.fixture(autouse=True)
    def _refs_root(self, monkeypatch, tmp_path):
        root = tmp_path / "voice" / "refs"
        root.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(config, "TTS_REFS_DIR", str(root), raising=False)

    def test_upload_list_edit_delete_round_trip(self, client, voice):
        up = client.post(
            "/api/v1/tts/voices/ayse",
            files={"file": ("ref.wav", _wav_bytes(), "audio/wav")},
            data={"label": "Ayse", "transcript": "Merhaba, ben burdayim."},
        )
        assert up.status_code == 200
        body = up.json()
        assert body["has_transcript"] and body["transcript_source"] == "user"

        listing = client.get("/api/v1/tts/voices").json()["voices"]
        assert [v["voice_id"] for v in listing] == ["ayse"]

        edited = client.post("/api/v1/tts/voices/ayse/transcript",
                             json={"text": "Merhaba, ben buradayim."})
        assert edited.json()["transcript"] == "Merhaba, ben buradayim."

        assert client.delete("/api/v1/tts/voices/ayse").json()["removed"] is True
        assert client.get("/api/v1/tts/voices").json()["voices"] == []

    def test_an_oversized_body_is_cut_by_the_route_not_by_refs(
        self, client, voice, monkeypatch,
    ) -> None:
        """The route used to call `await file.read()` with no argument.

        The cap lived in refs.save_upload, which runs after the whole body is
        already in memory, so the check could only ever describe a body that
        had been buffered in full. What is measured here is WHERE the refusal
        happens: refs must never be handed an oversized clip, because it can
        only see one by holding it.
        """
        import routers.tts_runtime as route

        seen: list[int] = []
        real_save = route.refs.save_upload

        def watched(voice_id, filename, data, **kw):
            seen.append(len(data))
            return real_save(voice_id, filename, data, **kw)

        monkeypatch.setattr(route.refs, "save_upload", watched)
        monkeypatch.setattr(config, "TTS_REF_MAX_BYTES", 4096)

        r = client.post(
            "/api/v1/tts/voices/buyuk",
            files={"file": ("ref.wav", bytes(40 * 1024), "audio/wav")},
        )
        assert r.status_code == 400
        assert r.json()["detail"] == "tts_reference_invalid"
        assert seen == [], (
            "refs.save_upload was handed the body, so the cut still happens "
            "after the whole file is in memory"
        )

    def test_a_clip_under_the_cap_still_reaches_refs(
        self, client, voice, monkeypatch,
    ) -> None:
        # GROUND CONTROL. Without it, a route that refused every upload would
        # satisfy the case above, and so would one that never called refs.
        import routers.tts_runtime as route

        seen: list[int] = []
        real_save = route.refs.save_upload

        def watched(voice_id, filename, data, **kw):
            seen.append(len(data))
            return real_save(voice_id, filename, data, **kw)

        monkeypatch.setattr(route.refs, "save_upload", watched)

        r = client.post(
            "/api/v1/tts/voices/normal",
            files={"file": ("ref.wav", _wav_bytes(), "audio/wav")},
        )
        assert r.status_code == 200
        assert seen and seen[0] <= int(config.TTS_REF_MAX_BYTES)

    def test_the_body_shield_covers_the_voice_route(
        self, client, voice, monkeypatch,
    ) -> None:
        """The shield only ever guarded /api/v1/uploads/.

        Content-Length is what a browser and curl both send, so refusing there
        means the body is never read at all. The ceiling is lowered here
        rather than sending thirty megabytes at a test.
        """
        import main

        # Derived from the real table, with only the NUMBER lowered. Written
        # out as a literal instead, removing the voice entry from main.py
        # would leave this test happily patching in its own.
        lowered = tuple(
            (prefix, 512 if prefix == "/api/v1/tts/voices/" else limit)
            for prefix, limit in main._BODY_LIMITS
        )
        assert any(p == "/api/v1/tts/voices/" for p, _ in lowered), (
            "the shield has no entry for the voice route at all"
        )
        monkeypatch.setattr(main, "_BODY_LIMITS", lowered)
        r = client.post(
            "/api/v1/tts/voices/buyuk",
            files={"file": ("ref.wav", bytes(4096), "audio/wav")},
        )
        assert r.status_code == 400
        assert r.json()["detail"] == "attachment_too_large"

    def test_the_shield_leaves_the_voice_route_alone_under_its_ceiling(
        self, client, voice,
    ) -> None:
        # GROUND: the voice route carries its OWN ceiling, three times the
        # image one. Reusing the image number would refuse a real clip.
        assert config.TTS_REF_BODY_LIMIT > config.UPLOAD_BODY_LIMIT
        r = client.post(
            "/api/v1/tts/voices/normal",
            files={"file": ("ref.wav", _wav_bytes(), "audio/wav")},
        )
        assert r.status_code == 200

    def test_a_too_short_clip_gets_its_own_code_over_the_wire(self, client, voice):
        r = client.post(
            "/api/v1/tts/voices/kisa",
            files={"file": ("ref.wav", _wav_bytes(seconds=0.5), "audio/wav")},
        )
        assert r.status_code == 400
        assert r.json()["detail"] == "tts_reference_too_short"

    def test_no_shipped_engine_transcribes_so_it_says_so(
        self, client, voice, monkeypatch
    ):
        """Audit KÖK 7: OP_TRANSCRIBE is refused by all three workers, and the
        refusal arrived as tts_worker_failed -> "The voice engine could not
        start", about an engine that had just loaded. The engine is fine; it
        simply does not do this, and that is a different sentence."""
        _fake_gpu(monkeypatch)
        runtimes.register("fish_s2", sys.executable)
        uid = client.get("/api/v1/tts/models").json()["models"][0]["uid"]
        client.post("/api/v1/tts/load", json={"uid": uid})

        client.post("/api/v1/tts/voices/ayse",
                    files={"file": ("ref.wav", _wav_bytes(), "audio/wav")})
        r = client.post("/api/v1/tts/voices/ayse/transcribe")
        assert r.status_code == 409
        assert r.json()["detail"] == "tts_transcribe_unsupported"

    def test_the_capability_is_declared_not_discovered_by_failing(
        self, client, voice, monkeypatch
    ):
        """The UI must be able to NOT DRAW the button, which means the answer
        has to be available without pressing it."""
        _fake_gpu(monkeypatch)
        runtimes.register("fish_s2", sys.executable)
        uid = client.get("/api/v1/tts/models").json()["models"][0]["uid"]
        caps = client.get(f"/api/v1/tts/models/{uid}/schema").json()["capabilities"]
        assert caps["transcribes_reference"] is False

    def test_every_shipped_adapter_agrees_with_its_worker(self):
        """The flag and the worker must not be able to drift: a True here with
        a refusing worker is the same broken button in a new place."""
        from tts.registry import all_adapters

        for adapter in all_adapters():
            assert adapter.capabilities.transcribes_reference is False, (
                f"{adapter.engine_id} claims to transcribe; its worker must "
                f"implement OP_TRANSCRIBE before this may be True"
            )

    def test_transcribe_without_a_loaded_engine_says_so(self, client, voice):
        client.post("/api/v1/tts/voices/ayse",
                    files={"file": ("ref.wav", _wav_bytes(), "audio/wav")})
        r = client.post("/api/v1/tts/voices/ayse/transcribe")
        assert r.status_code == 500
        assert r.json()["detail"] == "tts_worker_unavailable"


class TestContractStatuses:
    def test_truncated_speech_says_it_was_truncated(self, client, voice, monkeypatch):
        _fake_gpu(monkeypatch)
        runtimes.register("fish_s2", sys.executable)
        uid = client.get("/api/v1/tts/models").json()["models"][0]["uid"]
        r = client.post("/api/v1/tts/speak", json={"text": "a" * 6000, "uid": uid})
        assert r.status_code == 200
        assert r.json()["truncated"] is True

        r2 = client.post("/api/v1/tts/speak", json={"text": "short", "uid": uid})
        assert r2.json()["truncated"] is False

    def test_audio_is_served_with_no_store(self, client, voice, monkeypatch):
        """The embedded browser keeps a persistent profile; a cacheable 200
        would let it keep a copy of the conversation our wipe cannot reach."""
        _fake_gpu(monkeypatch)
        runtimes.register("fish_s2", sys.executable)
        uid = client.get("/api/v1/tts/models").json()["models"][0]["uid"]
        spoken = client.post("/api/v1/tts/speak", json={"text": "hi", "uid": uid})
        audio = client.get("/api/v1/tts/audio/%s" % spoken.json()["audio_id"])
        assert "no-store" in audio.headers.get("cache-control", "")

    def test_get_active_reports_the_live_state_not_a_hardcoded_one(
        self, client, voice, monkeypatch
    ):
        """The audit caught /tts/active answering "unloaded" forever - even
        with a model resident, even after its worker crashed."""
        _fake_gpu(monkeypatch)
        runtimes.register("fish_s2", sys.executable)
        uid = client.get("/api/v1/tts/models").json()["models"][0]["uid"]
        client.post("/api/v1/tts/active", json={"uid": uid})
        client.post("/api/v1/tts/load", json={"uid": uid})
        assert client.get("/api/v1/tts/active").json()["state"] == "loaded"


# ── the Speak button streams now ─────────────────────────────────────────────

class TestSpeakStream:
    """`/speak` cannot make a sound until the LAST sentence is finished.

    On a four-paragraph reply that is the whole utterance - twenty seconds or
    more - of silence before anything is heard, and no amount of engine speed
    fixes it: the shape of the endpoint is the latency. `/speak_stream` sends
    each piece as it is made, in the same wire format the live reply uses.
    """

    def _events(self, response):
        import json
        out = []
        for line in response.text.splitlines():
            if line.startswith("data: "):
                out.append(json.loads(line[6:]))
        return out

    def _ready(self, client, monkeypatch):
        _fake_gpu(monkeypatch)
        runtimes.register("fish_s2", sys.executable)
        uid = client.get("/api/v1/tts/models").json()["models"][0]["uid"]
        client.post("/api/v1/tts/active", json={"uid": uid})
        return uid

    def test_it_speaks_a_stored_message_in_pieces(self, client, voice, monkeypatch):
        self._ready(client, monkeypatch)
        res = client.post("/api/v1/tts/speak_stream",
                          json={"text": "First sentence here. Second one here. "
                                        "And a third sentence to finish it off."})
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/event-stream")
        events = self._events(res)
        chunks = [e for e in events if e["type"] == "voice_chunk"]
        assert len(chunks) >= 2, "a multi-sentence reply must arrive in pieces"
        assert events[-1]["type"] == "voice_done"
        assert events[-1]["count"] == len(chunks)

    def test_every_chunk_carries_an_id_and_its_place(self, client, voice, monkeypatch):
        self._ready(client, monkeypatch)
        res = client.post("/api/v1/tts/speak_stream",
                          json={"text": "One here. Two here. Three here."})
        chunks = [e for e in self._events(res) if e["type"] == "voice_chunk"]
        assert all(c["audio_id"] for c in chunks)
        assert [c["index"] for c in chunks] == list(range(len(chunks)))

    def test_the_wire_format_matches_the_live_reply(self, client, voice, monkeypatch):
        """Two ways to hear a sentence would be two players to keep working."""
        self._ready(client, monkeypatch)
        res = client.post("/api/v1/tts/speak_stream", json={"text": "Hello there."})
        kinds = {e["type"] for e in self._events(res)}
        assert kinds <= {"voice_chunk", "voice_error", "voice_done"}

    def test_nothing_to_say_is_still_refused_up_front(self, client, voice, monkeypatch):
        self._ready(client, monkeypatch)
        res = client.post("/api/v1/tts/speak_stream", json={"text": "   "})
        assert res.status_code == 400

    def test_the_worker_thread_is_not_joined_on_the_event_loop(
            self, client, voice, monkeypatch) -> None:
        """`close()` JOINS the synthesis thread, and a join that lands while
        the engine is mid-sentence blocks for as long as that sentence takes.
        The generator's cleanup runs ON the event loop, so doing it inline
        would freeze every other request in the app - the same trap the
        vault-lock path documents and avoids.

        DRIVEN, not read. This used to slice `tts_runtime.py`'s own text
        between two `def` lines and compare substring positions: it asserted
        that `speaker.cancel()` appears before `speaker.close` in the FILE.
        That is true of a `finally` block that never runs, of a `close` inside
        a branch nothing reaches, and of a comment. What it claimed to
        protect - the join not happening on the event loop - was never
        measured at all.

        The stub records the thread each call arrives on. The assertion is
        that `close` did NOT arrive on the loop's own thread, which is the
        whole property.
        """
        import threading

        calls: list[tuple[str, int]] = []

        class RecordingSpeaker:
            """Every call, with the thread it came in on."""

            finished = True
            dropped = 0
            dropped_samples: list[str] = []

            def feed(self, text: str) -> None:
                calls.append(("feed", threading.get_ident()))

            def finish(self) -> None:
                calls.append(("finish", threading.get_ident()))

            def drain(self):
                return []

            def take_error(self):
                return None

            def cancel(self) -> None:
                calls.append(("cancel", threading.get_ident()))

            def close(self) -> None:
                # Long enough that an inline join would be visible, short
                # enough not to slow the suite. The point is the THREAD, not
                # the duration.
                calls.append(("close", threading.get_ident()))
                time.sleep(0.05)

        monkeypatch.setattr(stream_hook, "StreamSpeaker",
                            lambda *a, **kw: RecordingSpeaker())
        _fake_gpu(monkeypatch)
        runtimes.register("fish_s2", sys.executable)
        uid = client.get("/api/v1/tts/models").json()["models"][0]["uid"]

        with client.stream("POST", "/api/v1/tts/speak_stream",
                           json={"text": "hello there", "uid": uid}) as r:
            assert r.status_code == 200
            for _ in r.iter_lines():
                pass

        names = [name for name, _ in calls]
        assert "cancel" in names, "the teardown never ran"
        assert "close" in names
        assert names.index("cancel") < names.index("close"), (
            "close was awaited before the worker was told to stop")
        # The loop's thread is learned from INSIDE the request, not from the
        # test: TestClient runs the app in a worker thread of its own, so the
        # test's own thread id is nobody's event loop. `feed` runs in the
        # generator body, which is on the loop.
        feed_thread = next(tid for name, tid in calls if name == "feed")
        close_thread = next(tid for name, tid in calls if name == "close")
        assert close_thread != feed_thread, (
            "the join happened on the event loop's own thread")

    def test_a_client_that_leaves_does_not_strand_the_worker(
            self, client, voice, monkeypatch) -> None:
        """A browser that navigates away mid-utterance must not leave a
        thread synthesising into a queue nobody will ever drain.

        DRIVEN, not read. The old version asserted that the substring
        `finally:` appears somewhere in a slice of the source file and that
        it appears before `speaker.cancel()`. A `finally` wrapping an empty
        block satisfies both.
        """
        import threading

        closed = threading.Event()
        calls: list[str] = []

        class RecordingSpeaker:
            finished = False
            dropped = 0
            dropped_samples: list[str] = []

            def feed(self, text: str) -> None:
                calls.append("feed")

            def finish(self) -> None:
                calls.append("finish")

            def drain(self):
                # Something to send, so the client has a chance to walk away
                # in the middle rather than after the end.
                return [{"audio_id": "a1", "seconds": 1.0,
                         "sample_rate": 44100, "text": "hello"}]

            def take_error(self):
                return None

            def cancel(self) -> None:
                calls.append("cancel")

            def close(self) -> None:
                calls.append("close")
                closed.set()

        monkeypatch.setattr(stream_hook, "StreamSpeaker",
                            lambda *a, **kw: RecordingSpeaker())
        # The wall-clock backstop, shortened. This speaker never reports
        # `finished`, which is the point - a worker that is still going is
        # what the client walks away from - so the endpoint's own deadline is
        # what ends the generator. Three minutes of it in a unit test is not
        # a measurement, it is a hang.
        monkeypatch.setattr(stream_hook, "DRAIN_TIMEOUT_S", 0.5)
        _fake_gpu(monkeypatch)
        runtimes.register("fish_s2", sys.executable)
        uid = client.get("/api/v1/tts/models").json()["models"][0]["uid"]

        # Read ONE line and abandon the stream - the navigate-away shape.
        with client.stream("POST", "/api/v1/tts/speak_stream",
                           json={"text": "hello there", "uid": uid}) as r:
            assert r.status_code == 200
            for _ in r.iter_lines():
                break

        assert closed.wait(5.0), "the worker was never closed"
        assert "cancel" in calls, "the worker was never told to stop"
        assert calls.index("cancel") < calls.index("close")
