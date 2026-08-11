"""Audit KÖK 1: detection with no carrier.

The same shape twelve times. The code detects the condition correctly, records
it, sometimes with a comment saying "so the endpoint can report it" - and then
no consumer reads the field. P4 ("no silent failure") is broken not by missing
detection but by a missing wire.

These tests assert the wire, not the detection: the detection was always fine.
"""

from __future__ import annotations

from pathlib import Path

import json
import sys

import pytest

from tts import runtimes
from tts.speech_queue import MAX_DROPPED_SAMPLES
# A live voice environment is not worth rebuilding; these tests want the same
# one the runtime API tests already stand up.
from test_tts_runtime_api import voice, _fake_gpu  # noqa: F401
from test_streaming import stream_provider  # noqa: F401

#: Absolute, because a test that only passes from one directory is a test that
#: will surprise somebody. Running `pytest backend/` from the repo root used to
#: fail eleven tests across four files with FileNotFoundError on a relative
#: path like 'tts/provision.py'. Measured 2026-08-10 and fixed here.
BACKEND = Path(__file__).resolve().parents[1]


def _ready_voice(client, monkeypatch) -> str:
    """A registered runtime and a selected model. Returns the model uid."""
    _fake_gpu(monkeypatch)
    runtimes.register("fish_s2", sys.executable)
    uid = client.get("/api/v1/tts/models").json()["models"][0]["uid"]
    client.post("/api/v1/tts/active", json={"uid": uid})
    return uid


def _events_of(response) -> list[dict]:
    return [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def _speak_stream(client, text: str) -> list[dict]:
    res = client.post("/api/v1/tts/speak_stream", json={"text": text})
    assert res.status_code == 200, res.text
    return _events_of(res)


class _FakeSpeaker:
    """Only what done_event and events() read."""

    def __init__(self, dropped: int = 0, samples: list[str] | None = None):
        self.dropped = dropped
        self.dropped_samples = samples or []

    def drain(self) -> list[dict]:
        return []

    def take_error(self):
        return None


def _pending_notes(monkeypatch, *notes: str):
    """Put worker notes where BOTH speech paths go looking for them.

    Handed over once and then gone, which is the host's real contract: the
    worker's ring buffer keeps its last 200 frames, so a host that re-answered
    would repeat the same compile note on every utterance for its whole life.
    """
    import tts.host

    class _Host:
        def __init__(self):
            self.pending = list(notes)

        def take_notes(self) -> list[str]:
            out, self.pending = self.pending, []
            return out

    host = _Host()
    monkeypatch.setattr(tts.host, "get_host", lambda: host)
    return host


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

def test_speak_stream_puts_truncated_on_the_wire(client, voice, monkeypatch):
    """It was a logger.warning and a field only /speak carried - while the
    Speak button only ever calls /speak_stream. So the reachable path cut the
    reply at 5000 characters and said nothing.

    This absorbed `test_the_preparer_reports_truncation_itself`, which asserted
    `PreparedSpeech._fields == ("text", "truncated")`. That is introspection of
    a namedtuple's shape, and the flag cannot arrive on the wire below without
    the field existing above, so the shape test could only ever fail in the
    company of this one."""
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


def _reads_raise(monkeypatch, message: str = "the setting could not be read"):
    """Every settings read fails, the way a locked or damaged vault fails."""
    import database

    def _unreadable(_name):
        raise RuntimeError(message)

    monkeypatch.setattr(database, "get_setting", _unreadable)


def test_the_stripping_guard_no_longer_swallows_silently(monkeypatch, caplog):
    """A bare `except Exception: return False` written for the locked-vault
    case took every other failure with it - and the visible symptom is raw
    [whisper] tags in every bubble, which reads as a model bug.

    Until KADEME 16a this was a source scan, and a broken one. It sliced
    voice_tags.py from `def stripping_active` to END OF FILE, so the
    `exc_info=True` it looked for was also satisfied by voice_block()'s own
    guard a hundred lines further down. Deleting the traceback from the guard
    this test is named after would have left it green.
    """
    import voice_tags

    voice_tags.reset_stripping_cache()
    _reads_raise(monkeypatch)

    with caplog.at_level("DEBUG", logger="voice_tags"):
        assert voice_tags.stripping_active() is False

    told = [r for r in caplog.records
            if r.name == "voice_tags" and r.exc_info is not None]
    assert told, "the read failed and nothing anywhere says what went wrong"
    assert "the setting could not be read" in str(told[-1].exc_info[1]), (
        "something was logged, but not the failure that actually happened")


def test_a_read_that_failed_is_not_remembered_as_a_no(monkeypatch):
    """The guard's own comment claims the False it returns is deliberately NOT
    cached, because it is an answer about the lock and not about the vault. If
    it were cached, one unlucky read would keep stripping off for the rest of
    the process and the tags would stay visible until a restart."""
    import voice_tags

    voice_tags.reset_stripping_cache()
    _reads_raise(monkeypatch)
    assert voice_tags.stripping_active() is False

    import database
    monkeypatch.setattr(
        database, "get_setting",
        lambda name: "1" if name == voice_tags.SETTING_VOICE_EVER else "")
    assert voice_tags.stripping_active() is True, (
        "the failed read was cached, so unlocking the vault changed nothing")


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


def test_the_speak_endpoint_carries_a_worker_note(client, voice, monkeypatch):
    """Half of "wired into both speech paths". This is the path the Speak
    button takes.

    Until KADEME 16a both halves were one test that read the two source files
    and looked for the string `"type": "voice_notice"`. Commenting out either
    emission, or moving it behind a branch that never runs, leaves those
    characters in the file and the old test green.
    """
    _ready_voice(client, monkeypatch)
    _pending_notes(monkeypatch, "falling back to eager decoding")

    events = _speak_stream(client, "One sentence.")

    assert [e["note"] for e in events if e["type"] == "voice_notice"] == [
        "falling back to eager decoding"]


def test_the_streaming_reply_carries_a_worker_note(monkeypatch):
    """The other half. A note that only reaches the Speak button is a note the
    listener never gets during an ordinary streamed reply, which is where the
    slow-compile case actually bites."""
    _pending_notes(monkeypatch, "first compile is slow")
    hook = _hook_with(_FakeSpeaker())

    events = hook.events()

    assert [e["note"] for e in events if e["type"] == "voice_notice"] == [
        "first compile is slow"]


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

def test_a_walk_that_stopped_at_the_cap_says_so(tmp_path, monkeypatch):
    """A short list that stopped at a limit must not be presented as a
    complete one: the missing rows are models the user owns and cannot see.

    Both halves used to be substring checks against registry.py and tts.py.
    `"result.truncated = True" in source` is satisfied by that same line
    commented out, which is the one edit that would actually break this.
    """
    import config
    from tts import registry

    root = tmp_path / "models"
    for i in range(4):
        (root / f"m{i}").mkdir(parents=True)

    assert registry.scan_roots([root]).truncated is False, (
        "four directories under the real cap is not a truncated walk"
    )

    monkeypatch.setattr(config, "TTS_SCAN_MAX_DIRS", 2)
    assert registry.scan_roots([root]).truncated is True


def test_the_models_endpoint_repeats_the_cap(client, voice, monkeypatch):
    """Computing the flag and not sending it is the KÖK 1 shape this whole
    file is about: detection with no carrier."""
    import config

    root = Path(config.TTS_MODELS_DIR)
    for i in range(4):
        (root / f"m{i}").mkdir(parents=True, exist_ok=True)

    assert client.get("/api/v1/tts/models").json()["truncated"] is False

    monkeypatch.setattr(config, "TTS_SCAN_MAX_DIRS", 2)
    assert client.get("/api/v1/tts/models").json()["truncated"] is True


# ---------------------------------------------------------------------------
# images dropped from the provider payload
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# /speak_stream's error contract
#
# Moved here in KADEME 16a from test_untested_shields.py, a file whose name
# described a GAP rather than a subject: it was opened for two unrelated
# audit findings and kept them together only because both were untested. The
# DNS-rebinding half went to test_cors_contract.py, which already owned the
# Host allowlist; this half is the same subject as everything above, what the
# voice wire says when something goes wrong.
# ---------------------------------------------------------------------------

def test_a_failing_sentence_ends_the_utterance_with_a_coded_error(
    client, voice, monkeypatch,
):
    """The contract is "emit voice_error and stop", and it had no test - so
    _code_for_error was never once executed. Audio that simply stops is
    indistinguishable from a reply that had nothing more to say, which is the
    one failure mode voice is not allowed to have."""
    import routers.tts_runtime as runtime
    from tts.errors import TTS_SYNTHESIS_FAILED

    _ready_voice(client, monkeypatch)
    real = runtime.make_stream_synth

    def failing(*a, **k):
        synth = real(*a, **k)

        def boom(text):
            raise RuntimeError("the engine gave up")

        boom.engine_supports_tags = getattr(synth, "engine_supports_tags", False)
        return boom

    monkeypatch.setattr(runtime, "make_stream_synth", failing)

    events = _speak_stream(client, "One sentence. Two sentences.")

    errors = [e for e in events if e["type"] == "voice_error"]
    assert errors, "the utterance stopped with nothing on the wire to say why"
    assert errors[-1]["code"] == TTS_SYNTHESIS_FAILED
    # Was `events[len(events) - 1:]`, a one-element slice, so it only ever
    # looked at the last event: a voice_done anywhere before the error passed.
    assert not any(e["type"] == "voice_done" for e in events), (
        "a failed utterance must not also report itself complete"
    )


def test_the_error_carries_a_code_the_frontend_already_knows(
    client, voice, monkeypatch,
):
    """Not prose. Every tts_* code has a sentence in errorMessages.ts, and a
    code invented here would render as the generic fallback.

    The floor matters more than the check. This body used to be entirely
    conditional - `if status == 200: for event: if type == voice_error:` - so
    on a build that emitted no voice_error at all, not one assertion ran and
    the test passed by never reaching anything.
    """
    from tts.errors import ALL_CODES
    import routers.tts_runtime as runtime

    _ready_voice(client, monkeypatch)
    monkeypatch.setattr(
        runtime, "make_stream_synth",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no engine")),
    )
    res = client.post("/api/v1/tts/speak_stream", json={"text": "Hello."})

    if res.status_code == 200:
        codes = [e["code"] for e in _events_of(res) if e["type"] == "voice_error"]
        assert codes, "the engine never came up and the wire said nothing"
    else:
        codes = [res.json()["detail"]]

    assert codes[-1] in ALL_CODES, codes


@pytest.mark.parametrize("text", ["", "   ", "---"])
def test_nothing_to_say_is_its_own_answer(client, voice, monkeypatch, text):
    """Distinct from a synthesis failure: the engine was never asked."""
    _ready_voice(client, monkeypatch)
    res = client.post("/api/v1/tts/speak_stream", json={"text": text})
    assert res.status_code == 400
    assert res.json()["detail"] == "tts_nothing_to_speak"


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
