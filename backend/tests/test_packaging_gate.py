"""Audit KÖK 13: two gates that could be green on a broken build.

The packaged app's self-test checked HTTP and nothing else, and the packaging
test grepped the spec files for two string literals. Neither ever looked at a
real output, so both would pass on an exe with no voice payload at all - which
is the only thing the custom spec files exist to carry.

The other half is a drain loop with a deadline and no test: /speak_stream's
backstop was added after the audit and nothing exercised it.
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from test_notice_channel import voice, _ready_voice  # noqa: F401


# ---------------------------------------------------------------------------
# the payload the spec files exist to carry
# ---------------------------------------------------------------------------

def test_the_selftest_looks_for_the_voice_payload_at_all():
    """A frozen build drops plain data files unless the spec names them, so
    "the server started and served the SPA" says nothing about whether the app
    can speak. Run here against the dev tree, where the answer must be yes."""
    import run_app

    ok, missing = run_app._selftest_voice_payload()
    assert ok, f"missing from this tree: {missing}"


def test_it_names_what_is_missing_rather_than_just_failing(monkeypatch):
    """A packaging gate that says only "no" leaves somebody diffing spec files
    by hand. The path it could not find is the whole message."""
    import run_app
    from tts import host as tts_host

    monkeypatch.setattr(
        tts_host, "worker_script",
        lambda engine_id: __import__("pathlib").Path(f"/nowhere/{engine_id}.py"),
    )
    ok, missing = run_app._selftest_voice_payload()

    assert ok is False
    assert "nowhere" in missing


def test_a_broken_import_is_a_failure_not_a_crash(monkeypatch):
    """The self-test runs inside the frozen exe. Dying on its own import is
    indistinguishable from the failure it exists to report."""
    import run_app
    from tts import provision

    monkeypatch.setattr(
        provision, "requirements_path",
        lambda engine_id: (_ for _ in ()).throw(RuntimeError("frozen weirdness")),
    )
    ok, missing = run_app._selftest_voice_payload()

    assert ok is False
    assert "frozen weirdness" in missing


def test_every_shipped_engine_is_covered_by_the_check():
    """Not just the one that happens to be installed here."""
    from tts import provision
    from tts.host import worker_script

    assert len(provision.ENGINES) >= 3, provision.ENGINES
    for engine_id in provision.ENGINES:
        assert worker_script(engine_id).is_file(), engine_id
        assert provision.requirements_path(engine_id).is_file(), engine_id


# ---------------------------------------------------------------------------
# the drain loop that could hold an HTTP response open forever
# ---------------------------------------------------------------------------

def _events(response) -> list[dict]:
    return [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def test_a_wedged_worker_does_not_hold_the_response_open(
    client, voice, monkeypatch,
):
    """stream_hook.drain_events has carried this backstop all along, with a
    docstring saying a wedged worker must not hold an HTTP response open
    forever. The structurally identical loop in /speak_stream had no deadline
    and, once it got one, still had no test (KÖK 13 + KÖK 4)."""
    import routers.tts_runtime as runtime
    from tts import stream_hook

    _ready_voice(client, monkeypatch)
    # A second, not two minutes: the point is that SOME ceiling fires, and the
    # real number would make this test a two-minute test.
    monkeypatch.setattr(stream_hook, "DRAIN_TIMEOUT_S", 0.5)

    # Releasable rather than a flat sleep: the speaker's worker is a daemon
    # thread, and leaving one parked for half a minute makes this test somebody
    # else's problem.
    release = threading.Event()

    def wedged_synth(*a, **k):
        def synth(text):
            release.wait(30)                # never comes back on its own
            return {"audio_id": "a", "seconds": 1.0}

        synth.engine_supports_tags = False
        return synth

    monkeypatch.setattr(runtime, "make_stream_synth", wedged_synth)

    started = time.monotonic()
    try:
        res = client.post("/api/v1/tts/speak_stream",
                          json={"text": "Say something."})
        elapsed = time.monotonic() - started
    finally:
        release.set()

    assert res.status_code == 200
    assert elapsed < 15.0, f"the response stayed open {elapsed:.0f}s"
    events = _events(res)
    assert events, "the stream closed with nothing on it at all"
    assert events[-1]["type"] == "voice_error", (
        "audio that simply stops is indistinguishable from a reply that had "
        "nothing more to say"
    )


def test_the_backstop_is_a_ceiling_not_a_schedule(client, voice, monkeypatch):
    """A healthy utterance must not be cut short by the same timer."""
    _ready_voice(client, monkeypatch)
    res = client.post("/api/v1/tts/speak_stream",
                      json={"text": "First one. Second one."})
    events = _events(res)
    assert events[-1]["type"] == "voice_done", [e["type"] for e in events]
