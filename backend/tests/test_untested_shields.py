"""Audit KÖK 13: two guards with no test at all.

Both were verified experimentally in the audit: remove them and the suite stays
green. That is the whole finding - not that they are wrong, but that nothing
would notice if they stopped being right.
"""

from __future__ import annotations

import pytest

from conftest import make_character, make_chat
from test_notice_channel import voice, _ready_voice  # noqa: F401


# ---------------------------------------------------------------------------
# the DNS-rebinding shield
# ---------------------------------------------------------------------------

def test_a_foreign_host_header_is_refused(client):
    """CORS alone cannot stop a hostile page whose domain re-resolves to
    127.0.0.1 - the browser then treats this API as same-origin, and the local
    API is unauthenticated. TrustedHostMiddleware is the only thing closing
    that path, and removing it left the `Host: evil.example` +
    `Origin: http://evil.example` pair passing the CSRF shield with the suite
    entirely green."""
    r = client.get(
        "/api/v1/settings",
        headers={"Host": "evil.example", "Origin": "http://evil.example"},
    )
    assert r.status_code == 400, (
        "a foreign Host reached the API - the rebinding shield is gone"
    )


def test_the_ordinary_localhost_hosts_still_work(client):
    """The guard has to be narrow enough to leave the app usable: the desktop
    shell, the dev server and the packaged build do not all use one spelling."""
    for host in ("127.0.0.1:8000", "localhost:8000", "127.0.0.1"):
        r = client.get("/api/v1/settings", headers={"Host": host})
        assert r.status_code != 400, host


def test_a_foreign_host_cannot_reach_the_chat_data(client):
    """Named separately because this is what the shield is FOR - chats and
    personas are exactly the data that must never leave this machine."""
    char_id = make_character(client)
    chat_id = make_chat(client, char_id)
    r = client.get(
        f"/api/v1/chats/{chat_id}/messages",
        headers={"Host": "attacker.test"},
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# /speak_stream's error contract
# ---------------------------------------------------------------------------

def _events(response) -> list[dict]:
    import json

    return [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


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

    res = client.post("/api/v1/tts/speak_stream",
                      json={"text": "One sentence. Two sentences."})
    assert res.status_code == 200
    events = _events(res)

    errors = [e for e in events if e["type"] == "voice_error"]
    assert errors, "the utterance stopped with nothing on the wire to say why"
    assert errors[-1]["code"] == TTS_SYNTHESIS_FAILED
    assert not any(e["type"] == "voice_done" for e in events[len(events) - 1:]), (
        "a failed utterance must not also report itself complete"
    )


def test_the_error_carries_a_code_the_frontend_already_knows(
    client, voice, monkeypatch,
):
    """Not prose. Every tts_* code has a sentence in errorMessages.ts, and a
    code invented here would render as the generic fallback."""
    from tts.errors import ALL_CODES
    import routers.tts_runtime as runtime

    _ready_voice(client, monkeypatch)
    monkeypatch.setattr(
        runtime, "make_stream_synth",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no engine")),
    )
    res = client.post("/api/v1/tts/speak_stream", json={"text": "Hello."})

    if res.status_code == 200:
        for event in _events(res):
            if event["type"] == "voice_error":
                assert event["code"] in ALL_CODES, event["code"]
    else:
        assert res.json()["detail"] in ALL_CODES, res.json()


@pytest.mark.parametrize("text", ["", "   ", "---"])
def test_nothing_to_say_is_its_own_answer(client, voice, monkeypatch, text):
    """Distinct from a synthesis failure: the engine was never asked."""
    _ready_voice(client, monkeypatch)
    res = client.post("/api/v1/tts/speak_stream", json={"text": text})
    assert res.status_code == 400
    assert res.json()["detail"] == "tts_nothing_to_speak"
