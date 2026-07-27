"""Audit KÖK 1: detection with no carrier.

The same shape twelve times. The code detects the condition correctly, records
it, sometimes with a comment saying "so the endpoint can report it" - and then
no consumer reads the field. P4 ("no silent failure") is broken not by missing
detection but by a missing wire.

These tests assert the wire, not the detection: the detection was always fine.
"""

from __future__ import annotations

import json
import sys

import pytest

from tts import runtimes
from tts.speech_queue import MAX_DROPPED_SAMPLES
# A live voice environment is not worth rebuilding; these tests want the same
# one the runtime API tests already stand up.
from test_tts_runtime_api import voice, _fake_gpu  # noqa: F401
from test_streaming import stream_provider  # noqa: F401


def _ready_voice(client, monkeypatch) -> str:
    """A registered runtime and a selected model. Returns the model uid."""
    _fake_gpu(monkeypatch)
    runtimes.register("fish_s2", sys.executable)
    uid = client.get("/api/v1/tts/models").json()["models"][0]["uid"]
    client.post("/api/v1/tts/active", json={"uid": uid})
    return uid


def _speak_stream(client, text: str) -> list[dict]:
    res = client.post("/api/v1/tts/speak_stream", json={"text": text})
    assert res.status_code == 200, res.text
    return [
        json.loads(line[6:])
        for line in res.text.splitlines()
        if line.startswith("data: ")
    ]


class _FakeSpeaker:
    """Only what done_event reads."""

    def __init__(self, dropped: int = 0, samples: list[str] | None = None):
        self.dropped = dropped
        self.dropped_samples = samples or []


def _hook_with(speaker) -> object:
    from tts.stream_hook import SpeakHook

    hook = SpeakHook.__new__(SpeakHook)
    hook._speaker = speaker
    hook._done_sent = False
    hook._index = 3
    return hook


def test_voice_done_carries_a_dropped_line():
    """speech_queue's own comment says it counts these "so the endpoint can
    say so rather than letting the audio quietly skip a line". Nothing read
    the field - a grep of routers/ and tts/ found no reader outside the file
    that writes it."""
    hook = _hook_with(_FakeSpeaker(dropped=2, samples=["[Anna]: https://x.co"]))
    (event,) = hook.done_event()
    assert event["type"] == "voice_done"
    assert event["dropped"] == 2
    assert event["dropped_samples"] == ["[Anna]: https://x.co"]


def test_a_clean_reply_does_not_carry_the_field_at_all():
    """Absent, not zero: a client should not have to distinguish 0 from
    'nothing was lost' on every single reply."""
    hook = _hook_with(_FakeSpeaker())
    (event,) = hook.done_event()
    assert "dropped" not in event
    assert "dropped_samples" not in event


def test_the_sample_list_cannot_grow_without_bound():
    from tts.speech_queue import SpeechQueue

    q = SpeechQueue(synth=lambda t: {"audio_id": "a", "seconds": 1.0})
    for _ in range(MAX_DROPPED_SAMPLES + 20):
        q.push("[Anna]: https://example.com. ")
    q.close()
    q.pump()
    assert len(q.dropped_samples) == MAX_DROPPED_SAMPLES
    assert q.dropped > MAX_DROPPED_SAMPLES, "the COUNT must stay honest"


def test_a_divider_is_not_reported_as_lost_speech():
    """The counter over-counted: `---` prepares to nothing because it IS
    nothing. Wiring it to a notice without narrowing this first would warn
    about lost speech every time a model drew a horizontal rule."""
    from tts.speech_queue import SpeechQueue

    q = SpeechQueue(synth=lambda t: {"audio_id": "a", "seconds": 1.0})
    q.push("---")
    q.close()
    q.pump()
    assert q.dropped == 0


# ---------------------------------------------------------------------------
# truncation: computed on one path, reported on neither the user can reach
# ---------------------------------------------------------------------------

def test_the_preparer_reports_truncation_itself():
    """It was a logger.warning and a field only /speak carried - while the
    Speak button only ever calls /speak_stream. So the reachable path cut the
    reply at 5000 characters and said nothing."""
    from routers.tts_runtime import PreparedSpeech

    assert PreparedSpeech._fields == ("text", "truncated")


def test_speak_stream_puts_truncated_on_the_wire(client, voice, monkeypatch):
    """The reachable path cut the reply at 5000 characters and said nothing."""
    _ready_voice(client, monkeypatch)
    events = _speak_stream(client, "a" * 6000)
    assert events[-1]["type"] == "voice_done"
    assert events[-1]["truncated"] is True


def test_a_reply_that_fits_says_nothing_about_truncation(
    client, voice, monkeypatch,
):
    """Absent, not False: the flag exists to be noticed."""
    _ready_voice(client, monkeypatch)
    events = _speak_stream(client, "Short enough to say in full.")
    assert events[-1]["type"] == "voice_done"
    assert not events[-1].get("truncated")


def test_both_endpoints_agree_about_truncation(client, voice, monkeypatch):
    """/speak recomputed it from _speak_source and /speak_stream did not
    compute it at all - two answers to one question, one of them missing.

    Asserted on the two ANSWERS rather than on the source line they share:
    the previous version counted occurrences of one call expression, so it
    broke the moment that call moved into a worker thread - and would have
    stayed green if the two endpoints had been rewritten to disagree."""
    uid = _ready_voice(client, monkeypatch)
    long_text = "a" * 6000

    plain = client.post(
        "/api/v1/tts/speak", json={"text": long_text, "uid": uid},
    ).json()["truncated"]
    streamed = _speak_stream(client, long_text)[-1].get("truncated", False)

    assert plain is True
    assert streamed is True
    assert plain == streamed


# ---------------------------------------------------------------------------
# exceptions that reached the log with no stack
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label", ["completion", "regenerate", "edit"])
def test_the_sse_handlers_log_a_stack(
    client, stream_provider, caplog, label, monkeypatch,
):
    """"internal error: chat_id=N" with no file, no line and no traceback is
    the hardest possible shape to act on from a user's report.

    Driven through the endpoints rather than grepped for `exc_info=True`: a
    count of that string proves nothing about whether the handler that needs
    it has it, and it went stale the moment the three handlers became one.
    """
    from test_streaming import BODY, _seed_exchange, read_events

    chat_id, message_id = _seed_exchange(client, stream_provider)
    path, payload = {
        "completion": (f"/api/v1/chats/{chat_id}/complete/stream", BODY),
        "regenerate": (
            f"/api/v1/chats/{chat_id}/messages/{message_id}/regenerate/stream",
            {"model_id": "test/model-1"},
        ),
        "edit": (
            f"/api/v1/chats/{chat_id}/messages/{message_id - 1}/edit/stream",
            {"model_id": "test/model-1", "message": "rewritten"},
        ),
    }[label]

    # An unexpected failure - NOT an OpenRouterError, so it can only land in
    # the last-resort branch every handler funnels into.
    stream_provider.error = RuntimeError("something nobody predicted")
    stream_provider.error_after = 1

    caplog.clear()
    with caplog.at_level("WARNING"):
        with client.stream("POST", path, json=payload) as resp:
            events = read_events(resp)

    assert events[-1] == {
        "type": "error", "status": 500, "code": "internal_error",
    }, f"{label}: an unexpected failure must still terminate the stream"

    stacks = [
        r for r in caplog.records
        if "internal error" in r.getMessage() and r.exc_info is not None
    ]
    assert stacks, f"{label}: an unexplained 500 was logged with no traceback"
    assert "something nobody predicted" in "".join(
        str(r.exc_info[1]) for r in stacks
    )


def test_the_stripping_guard_no_longer_swallows_silently():
    """A bare `except Exception: return False` written for the locked-vault
    case took every other failure with it - and the visible symptom is raw
    [whisper] tags in every bubble, which reads as a model bug."""
    from pathlib import Path

    source = Path("voice_tags.py").read_text(encoding="utf-8")
    guard = source.split("def stripping_active", 1)[1]
    assert "logger.debug" in guard
    assert "exc_info=True" in guard


# ---------------------------------------------------------------------------
# worker notes: 29 emissions, no reader anywhere
# ---------------------------------------------------------------------------

class _FakeClient:
    def __init__(self, frames):
        self.events = frames


def _host_with(frames):
    from tts.host import VoiceHost

    host = VoiceHost.__new__(VoiceHost)
    import threading
    host._lock = threading.RLock()
    host._client = _FakeClient(frames)
    host._notes_sent = set()
    return host


def test_worker_notes_become_readable():
    """All 29 _progress() notes landed in worker_client's ring buffer, which
    had no reader in tts/ or routers/. On a Windows box without MSVC or
    triton, compile_failed fired on every load, speech ran 2-3x slower
    forever, and nothing said so."""
    host = _host_with([
        {"stage": "compiling", "note": "first compile is slow"},
        {"stage": "loaded"},
        {"stage": "compile_failed", "note": "falling back to eager decoding"},
    ])
    assert host.take_notes() == [
        "first compile is slow", "falling back to eager decoding",
    ]


def test_a_note_is_said_once():
    """The ring buffer keeps its last 200 frames, so without this the same
    compile note would be re-sent on every utterance for the worker's life."""
    frames = [{"stage": "compiling", "note": "first compile is slow"}]
    host = _host_with(frames)
    assert host.take_notes() == ["first compile is slow"]
    assert host.take_notes() == []


def test_no_worker_means_no_notes_and_no_error():
    host = _host_with([])
    host._client = None
    assert host.take_notes() == []


def test_the_notice_frame_is_wired_into_both_speech_paths():
    from pathlib import Path

    hook = Path("tts/stream_hook.py").read_text(encoding="utf-8")
    assert '"type": "voice_notice"' in hook
    assert "_host_notes()" in hook
    runtime = Path("routers/tts_runtime.py").read_text(encoding="utf-8")
    assert '"type": "voice_notice"' in runtime


def test_a_broken_note_channel_cannot_break_speech():
    """A reporting channel must not be able to take down the thing it
    reports on."""
    import tts.stream_hook as sh

    def _boom():
        raise RuntimeError("host is gone")

    original = sh._host_notes.__globals__.get("get_host")
    import tts.host
    saved = tts.host.get_host
    tts.host.get_host = _boom
    try:
        assert sh._host_notes() == []
    finally:
        tts.host.get_host = saved
        void = original


# ---------------------------------------------------------------------------
# a scan that stopped at the cap must not look complete
# ---------------------------------------------------------------------------

def test_a_truncated_scan_says_so():
    from tts.base import ScanResult

    assert ScanResult().truncated is False
    source = __import__("pathlib").Path("tts/registry.py").read_text(encoding="utf-8")
    assert "result.truncated = True" in source


def test_the_scan_payload_carries_it():
    from pathlib import Path

    source = Path("routers/tts.py").read_text(encoding="utf-8")
    assert '"truncated": result.truncated' in source


# ---------------------------------------------------------------------------
# images dropped from the provider payload
# ---------------------------------------------------------------------------

def test_a_missing_blob_is_collected_not_just_logged():
    """The completion succeeded and the user got a normal answer from a model
    that had never seen their picture."""
    from attachments_service import build_image_part

    omitted: list[int] = []
    part = build_image_part({"id": 7, "sha256": "absent", "mime": "image/png"},
                            {}, omitted)
    assert part is None
    assert omitted == [7]


def test_a_present_blob_is_not_reported_as_omitted():
    from attachments_service import build_image_part

    omitted: list[int] = []
    part = build_image_part({"id": 7, "sha256": "s", "mime": "image/png"},
                            {"s": b"bytes"}, omitted)
    assert part is not None
    assert omitted == []
