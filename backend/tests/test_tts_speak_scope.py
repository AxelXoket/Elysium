"""Speaking a stored message stays inside the conversation that asked for it.

`messages.id` is a single global counter, so a replay request that carries an
id and nothing else addresses the WHOLE vault. Until the read was scoped, a
caller could name any id at all and get that row read aloud: a conversation
the user had closed, synthesised, and left sitting in the audio cache, from a
request that never said which chat it belonged to. The row is stored RAW
precisely because the client only ever holds the stripped view, so what came
back was the fullest copy of it that exists anywhere.

The lens is the one test_cross_chat_isolation.py already points at the write
paths, turned around onto a read: plant a SECOND chat, ask from the first, and
watch what actually reaches the engine. Chat B is created last so its ids are
the high ones, the same arrangement that file settled on.

Both replay endpoints share `_prepare_speech_text`, so both are driven here.
A guard on one of them is not a guard: /tts/speak_stream is the only one the
Speak button reaches, and /tts/speak is the one the contract documents.

The absence assertion that carries the weight is "the engine was never handed
this sentence", and that one has its positive control in the same test through
the same recorder: it also passes against a recorder nobody wired up, so the
allowed read runs first and the sweep is watched finding the sentence before it
is asked to miss it. The body sweeps beside it (`SENTINEL not in response`) are
belt to that braces and cannot be controlled the same way, because a successful
speak answers with an audio id or a stream of chunk events and never with the
words: there is no state in which the sweep SHOULD find them.
"""

from __future__ import annotations

import json
import sys

import pytest

from database import get_db
from tts import host as tts_host
from tts import runtimes
# Imported, never retyped: a test that spells the engine out by hand goes on
# agreeing with itself after the adapter stops using that name.
from tts.adapters.fish_s2 import FishS2Adapter
# The same live voice environment the other runtime tests stand up: a real
# subprocess over real pipes, no GPU.
from test_tts_runtime_api import voice, _fake_gpu  # noqa: F401

#: Invented, and distinctive enough that a substring sweep cannot match it by
#: accident. The tag is part of the point: the stored row keeps its delivery
#: tags and the endpoint is supposed to hand them over intact, so the fixture
#: has to carry one or "raw content came back" is untestable.
SENTINEL_A = "[laughs] Zilverdaal marks the first conversation."
SENTINEL_B = "[whispers] Kranenburgh marks the second conversation."

SPEAK_ROUTES = ["/api/v1/tts/speak", "/api/v1/tts/speak_stream"]


def _ready_voice(client, monkeypatch) -> str:
    """A registered runtime and a selected model. Returns the model uid."""
    _fake_gpu(monkeypatch)
    runtimes.register(FishS2Adapter.engine_id, sys.executable)
    uid = client.get("/api/v1/tts/models").json()["models"][0]["uid"]
    client.post("/api/v1/tts/active", json={"uid": uid})
    return uid


@pytest.fixture()
def two_chats(client):
    """Chat A then chat B, one stored assistant row each.

    Returns (chat_a, msg_a, chat_b, msg_b). B is planted second on purpose:
    its ids are the high ones, so a request that names A and reaches B has
    plainly walked out of the conversation it was given.
    """
    with get_db() as con:
        character = con.execute(
            "INSERT INTO characters (name) VALUES ('Scope fixture')"
        ).lastrowid
        chat_a = con.execute(
            "INSERT INTO chats (character_id) VALUES (?)", (character,)
        ).lastrowid
        msg_a = con.execute(
            "INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)",
            (chat_a, "assistant", SENTINEL_A),
        ).lastrowid
        chat_b = con.execute(
            "INSERT INTO chats (character_id) VALUES (?)", (character,)
        ).lastrowid
        msg_b = con.execute(
            "INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)",
            (chat_b, "assistant", SENTINEL_B),
        ).lastrowid
    assert msg_b > msg_a, "chat B must be the later one for this lens to bite"
    return chat_a, msg_a, chat_b, msg_b


@pytest.fixture()
def spoken(monkeypatch):
    """Every text the engine was handed, in order.

    A WRAPPER, not a stub: real synthesis still runs underneath, so a request
    that is refused is refused by the route rather than by a fake that
    happened to be wired in front of it. Both endpoints funnel into this one
    call - `_speak_in_sentences` for /speak, the synth closure for
    /speak_stream - so it is the single place where "was this row read out"
    can be answered for both.
    """
    said: list[str] = []
    real = tts_host.VoiceHost.speak

    def recording(self, text, *args, **kwargs):
        said.append(text)
        return real(self, text, *args, **kwargs)

    monkeypatch.setattr(tts_host.VoiceHost, "speak", recording)
    return said


def _sse(response) -> list[dict]:
    return [json.loads(line[6:]) for line in response.text.splitlines()
            if line.startswith("data: ")]


# -- the boundary ----------------------------------------------------------


@pytest.mark.parametrize("route", SPEAK_ROUTES)
def test_a_message_from_another_chat_is_never_read_out(
    client, voice, monkeypatch, two_chats, spoken, route,
):
    """The defect, from the caller's side: name chat A, hand over B's id.

    The positive control comes first and shares everything with the assertion
    it protects - same row, same sentinel, same recorder, same endpoint - so
    the only difference between the two halves is which chat was named.
    """
    uid = _ready_voice(client, monkeypatch)
    chat_a, _msg_a, chat_b, msg_b = two_chats

    allowed = client.post(
        route, json={"chat_id": chat_b, "message_id": msg_b, "uid": uid})
    assert allowed.status_code == 200, allowed.text
    assert any(SENTINEL_B in said for said in spoken), (
        "the recorder cannot see the sentence even when the read IS allowed, "
        "so the absence assertion below would prove nothing")

    spoken.clear()
    crossed = client.post(
        route, json={"chat_id": chat_a, "message_id": msg_b, "uid": uid})

    assert crossed.status_code == 404, crossed.text
    assert not any(SENTINEL_B in said for said in spoken), (
        "the other conversation was read aloud")
    assert SENTINEL_B not in crossed.text, "the refusal carried the content"


@pytest.mark.parametrize("route", SPEAK_ROUTES)
def test_the_refusal_cannot_be_told_apart_from_a_row_that_is_not_there(
    client, voice, monkeypatch, two_chats, route,
):
    """Otherwise the endpoint answers a question nobody may ask it.

    A refusal that said "wrong chat" where an unknown id says "no such row"
    would turn the route into an oracle: walk the id space, and the pair of
    answers maps out which conversation owns what without ever hearing a word
    of it. The two must be the same answer, and the code is taken from the
    endpoint's own not-found reply rather than retyped here.
    """
    uid = _ready_voice(client, monkeypatch)
    chat_a, _msg_a, _chat_b, msg_b = two_chats
    nowhere = msg_b + 10_000

    absent = client.post(
        route, json={"chat_id": chat_a, "message_id": nowhere, "uid": uid})
    crossed = client.post(
        route, json={"chat_id": chat_a, "message_id": msg_b, "uid": uid})

    assert absent.status_code == 404, absent.text
    assert crossed.status_code == absent.status_code
    assert crossed.json() == absent.json()


@pytest.mark.parametrize("route", SPEAK_ROUTES)
def test_an_id_with_no_chat_is_refused_before_the_vault_is_touched(
    client, voice, monkeypatch, two_chats, route,
):
    """The refusal belongs to the body, not to the lookup.

    Checked at parse time so no unscoped id ever reaches the MESSAGES table:
    the proof is that `_speak_source`, the only thing on this path that reads
    a row out of it, is replaced with something that cannot be called without
    failing loudly, and the request still comes back refused. Other settings
    reads happen on this path and are not what is being pinned here.

    The code is read off a second, deliberately malformed request rather than
    retyped, so this stays true if the app-wide validation answer is ever
    renamed.
    """
    uid = _ready_voice(client, monkeypatch)
    _chat_a, _msg_a, _chat_b, msg_b = two_chats

    import routers.tts_runtime as runtime

    def must_not_run(body):
        raise AssertionError("the vault was read for an unscoped id")

    monkeypatch.setattr(runtime, "_speak_source", must_not_run)

    unscoped = client.post(route, json={"message_id": msg_b, "uid": uid})
    malformed = client.post(
        route, json={"message_id": "not an id", "chat_id": 1, "uid": uid})

    assert unscoped.status_code == 422, unscoped.text
    assert unscoped.json()["detail"] == malformed.json()["detail"]


@pytest.mark.parametrize("route", SPEAK_ROUTES)
def test_the_answer_is_the_same_for_every_chat_when_no_engine_is_selected(
    client, voice, monkeypatch, two_chats, route,
):
    """The scope predicate withholds an ownership map; the ORDER keeps it.

    Both ids are read before an engine is picked, so if the engine were
    resolved second the owning chat would answer "no model" while every other
    chat answered "no such message" - the map handed over with no engine, no
    audio and nothing heard. The controls are at both ends: identical answers
    while nothing is loaded, and a genuinely different answer for the chat the
    row belongs to once an engine is there, which is what proves the identity
    above is the order and not an endpoint that answers everything the same.
    """
    chat_a, _msg_a, chat_b, msg_b = two_chats
    nowhere = msg_b + 10_000

    owner = client.post(route, json={"chat_id": chat_b, "message_id": msg_b})
    foreign = client.post(route, json={"chat_id": chat_a, "message_id": msg_b})
    absent = client.post(route, json={"chat_id": chat_a, "message_id": nowhere})

    assert owner.status_code == foreign.status_code == absent.status_code
    assert owner.json() == foreign.json() == absent.json()

    uid = _ready_voice(client, monkeypatch)
    spoken = client.post(
        route, json={"chat_id": chat_b, "message_id": msg_b, "uid": uid})
    still_refused = client.post(
        route, json={"chat_id": chat_a, "message_id": msg_b, "uid": uid})
    assert spoken.status_code == 200, spoken.text
    assert still_refused.status_code == 404, still_refused.text


@pytest.mark.parametrize("route", SPEAK_ROUTES)
@pytest.mark.parametrize("field", ["chat_id", "message_id"])
def test_an_id_wider_than_the_database_is_refused_not_crashed(
    client, voice, monkeypatch, two_chats, route, field,
):
    """Both ids go straight into a SQL statement, so both need a ceiling.

    Without one the driver raised on the way past and the caller got a bare
    500 with no contract code on /speak, and "the voice engine could not
    start" on /speak_stream - a database complaint sending somebody to the
    engine panel. The ground control is the same request one digit narrower,
    which reaches the lookup and is refused by the scope instead.
    """
    import routers.tts_runtime as runtime

    uid = _ready_voice(client, monkeypatch)
    chat_a, _msg_a, _chat_b, msg_b = two_chats
    ok = {"chat_id": chat_a, "message_id": msg_b, "uid": uid}

    narrow = client.post(route, json=ok)
    assert narrow.status_code == 404, narrow.text

    wide = client.post(route, json={**ok, field: runtime._MAX_ROW_ID + 1})
    malformed = client.post(route, json={**ok, field: "not an id"})

    assert wide.status_code == 422, wide.text
    assert wide.headers["content-type"].startswith("application/json")
    assert wide.json()["detail"] == malformed.json()["detail"]


@pytest.mark.parametrize("route", SPEAK_ROUTES)
def test_speaking_words_the_caller_supplied_still_names_no_chat(
    client, voice, monkeypatch, route,
):
    """The scope is owed by the id, not by the endpoint.

    Text handed over in the request reads no row and crosses no boundary, so
    demanding a chat for it would buy nothing and cost every caller that has
    one - twenty-two of them in this suite alone. This is the test that keeps
    the field from quietly becoming unconditional later.
    """
    uid = _ready_voice(client, monkeypatch)
    res = client.post(route, json={"text": "Two short sentences. Here they are.",
                                   "uid": uid})
    assert res.status_code == 200, res.text
    if route.endswith("_stream"):
        assert any(e.get("type") == "voice_chunk" for e in _sse(res))
    else:
        assert res.json()["audio_id"]


# -- rawness, which the scope must not cost --------------------------------


def test_the_matching_read_hands_back_the_row_exactly_as_stored(
    client, two_chats,
):
    """Scope was added; RAWNESS was not traded for it.

    The stripped view is all the client ever holds, which is the whole reason
    per-message speak comes back to the server for the words. Asserted on the
    returned string rather than through the engine, because the engine's own
    preparation legitimately rewrites tags for adapters that cannot speak
    them - and it is the string leaving the vault that this unit changed.
    """
    import routers.tts_runtime as runtime

    chat_a, msg_a, _chat_b, _msg_b = two_chats
    body = runtime.SpeakBody(chat_id=chat_a, message_id=msg_a)

    assert runtime._speak_source(body) == SENTINEL_A


def test_the_lookup_itself_refuses_a_row_from_another_chat(client, two_chats):
    """One layer under the routes, where the SELECT actually happens.

    The route tests above can only see a status; this one can see the string,
    which is the thing that must not come back.
    """
    from fastapi import HTTPException

    import routers.tts_runtime as runtime

    chat_a, _msg_a, _chat_b, msg_b = two_chats

    with pytest.raises(HTTPException) as caught:
        runtime._speak_source(
            runtime.SpeakBody(chat_id=chat_a, message_id=msg_b))

    assert caught.value.status_code == 404
    assert SENTINEL_B not in str(caught.value.detail)
