"""Audit G8: one streaming body, and the promises it now has to keep alone.

Three endpoints ran ~60 byte-identical lines each. Three copies meant every
voice and abort fix had to be made three times, and the audit found places
where it had been made twice - `a_voice_model_is_selected` on one endpoint,
the display-view gate on the success path but not the abort path.

So these tests are written the way the duplication demanded and the greps
never were: whatever is asserted, is asserted for ALL THREE endpoints. A
promise that only one of them keeps is exactly the bug this group closes.

Covered here: KÖK 16 (the abort/error policy), KÖK 8 (write transactions off
the event loop) and the omitted-image notice G7 detected but never sent.
"""

from __future__ import annotations

import asyncio
import json
import threading

import pytest

import voice_tags
from routers.completions import (
    CompleteRequest,
    EditRequest,
    RegenerateRequest,
    StaleExchangeError,
    _insert_assistant_message,
    complete_chat_stream,
    edit_message_stream,
    regenerate_message_stream,
)
from database import get_db

from conftest import make_character, make_chat, get_messages
from test_streaming import BODY, read_events, stream_provider  # noqa: F401


# ---------------------------------------------------------------------------
# harness
# ---------------------------------------------------------------------------

def _install_stream(monkeypatch, deltas, *, fail_after=None, error=None):
    import routers.completions as cr

    def fake_stream(messages, model_id, gen_params, provider, **kwargs):
        async def gen():
            try:
                for i, d in enumerate(deltas):
                    if fail_after is not None and i >= fail_after:
                        raise error
                    yield d
            except GeneratorExit:
                return
            if fail_after is not None and fail_after >= len(deltas):
                raise error
        return gen()

    monkeypatch.setattr(cr, "complete_stream", fake_stream)


def _seed(client):
    """A chat with one complete exchange. Returns (chat_id, user_id, asst_id)."""
    chat_id = make_chat(client, make_character(client))
    with get_db() as con:
        user_id = con.execute(
            "INSERT INTO messages (chat_id, role, content) VALUES (?, 'user', ?)",
            (chat_id, "the question"),
        ).lastrowid
        asst_id = con.execute(
            "INSERT INTO messages (chat_id, role, content) "
            "VALUES (?, 'assistant', ?)",
            (chat_id, "the answer"),
        ).lastrowid
    return chat_id, user_id, asst_id


def _endpoints(client):
    """(label, coroutine factory) for each streaming endpoint, on its own chat.

    Each gets a fresh chat so one test's writes cannot change another's
    starting state - the three finalizers touch very different rows.
    """
    out = []
    chat_id = make_chat(client, make_character(client))
    out.append((
        "completion",
        chat_id,
        lambda: complete_chat_stream(
            chat_id, CompleteRequest(message="hi", model_id="test/model-1"),
        ),
    ))
    r_chat, r_user, r_asst = _seed(client)
    out.append((
        "regenerate",
        r_chat,
        lambda: regenerate_message_stream(
            r_chat, r_asst, RegenerateRequest(model_id="test/model-1"),
        ),
    ))
    e_chat, e_user, e_asst = _seed(client)
    out.append((
        "edit",
        e_chat,
        lambda: edit_message_stream(
            e_chat, e_user,
            EditRequest(model_id="test/model-1", message="rewritten"),
        ),
    ))
    return out


async def _collect(make_response, *, abort_after=None):
    """Drive one streaming endpoint. abort_after: stop and aclose() at that
    event type, which is how a real client disconnect reaches the generator."""
    resp = await make_response()
    agen = resp.body_iterator
    seen: list[dict] = []
    async for chunk in agen:
        seen.append(json.loads(chunk[len("data: "):].strip()))
        if abort_after is not None and seen[-1]["type"] == abort_after:
            break
    await agen.aclose()
    return seen


def _install_hanging_stream(monkeypatch, deltas):
    """Deltas, then a provider that never says anything more.

    Needed because a delta the stripper WITHHOLDS produces no SSE event at
    all, so "abort after the first delta" cannot reach the state this group
    cares about: text received, nothing shown yet.
    """
    import routers.completions as cr

    def fake_stream(messages, model_id, gen_params, provider, **kwargs):
        async def gen():
            for d in deltas:
                yield d
            await asyncio.Event().wait()   # the provider is still thinking
        return gen()

    monkeypatch.setattr(cr, "complete_stream", fake_stream)


async def _abort_while_waiting(make_response, *, after_events: int):
    """Consume `after_events` events, then drop the connection mid-wait.

    Cancelling the pending pull throws CancelledError into the generator at
    the await it is parked on - which is precisely where a real disconnect
    lands, and the branch the abort policy lives in.
    """
    import contextlib

    resp = await make_response()
    agen = resp.body_iterator
    for _ in range(after_events):
        await agen.__anext__()
    task = asyncio.ensure_future(agen.__anext__())
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
        await task
    with contextlib.suppress(Exception):
        await agen.aclose()


def _fake_synth():
    """An engine that makes a sound without needing one."""
    def synth(text):
        return {"audio_id": "a", "seconds": 0.5}

    synth.engine_supports_tags = False
    return synth


def _raw_contents(chat_id: int) -> list[str]:
    """Straight from the DB: msg_to_dict hides delivery tags at the API door,
    so the response body cannot answer "what was actually stored"."""
    with get_db() as con:
        return [
            r["content"] for r in con.execute(
                "SELECT content FROM messages WHERE chat_id = ? ORDER BY id",
                (chat_id,),
            ).fetchall()
        ]


# ---------------------------------------------------------------------------
# KÖK 16a - an interrupted reply is judged on what it would SHOW
# ---------------------------------------------------------------------------

def test_a_partial_that_is_only_delivery_tags_is_not_stored(
    client, monkeypatch, voice_on,
):
    """The reported failure exactly.

    The model opens with "[whisper] " and the user presses Stop before the
    second delta. The abort branch tested `partial.strip()` - RAW - so the row
    was stored, and every later render of that chat showed an empty assistant
    bubble that nothing could remove. The success path had refused the same
    bytes three lines above, calling it "the one silence R3 bans".
    """
    _install_hanging_stream(monkeypatch, ["[whisper] "])
    chat_id = make_chat(client, make_character(client))

    asyncio.run(_abort_while_waiting(
        lambda: complete_chat_stream(
            chat_id, CompleteRequest(message="hi", model_id="test/model-1"),
        ),
        after_events=1,          # user_message; the tag is never shown
    ))

    msgs = get_messages(client, chat_id)
    assert [m["role"] for m in msgs] == ["assistant"], (
        "a reply with nothing to show was stored as a permanently empty bubble"
    )


def test_a_partial_with_real_words_is_still_kept(client, monkeypatch, voice_on):
    """The other side: the display-view gate must not swallow real text that
    merely happens to carry a tag."""
    _install_hanging_stream(monkeypatch, ["[whisper] Some real words. "])
    chat_id = make_chat(client, make_character(client))

    asyncio.run(_abort_while_waiting(
        lambda: complete_chat_stream(
            chat_id, CompleteRequest(message="hi", model_id="test/model-1"),
        ),
        after_events=2,          # user_message, then the visible remainder
    ))

    msgs = get_messages(client, chat_id)
    assert [m["role"] for m in msgs] == ["assistant", "user", "assistant"]
    # RAW is what gets STORED - the tags are what make a replay worth hearing,
    # and only msg_to_dict's display door hides them.
    assert _raw_contents(chat_id)[-1] == "[whisper] Some real words. "


# ---------------------------------------------------------------------------
# KÖK 16b - the finalize guard checks the tail, like both its siblings
# ---------------------------------------------------------------------------

def test_a_reply_cannot_land_behind_a_turn_that_arrived_while_it_streamed(
    client,
):
    """_append_variant and _finalize_edit both verify the chat's tail is where
    they left it; this one only checked that its user row still EXISTED. A
    write that won the race put the answer under the wrong question."""
    chat_id = make_chat(client, make_character(client))
    with get_db() as con:
        user_id = con.execute(
            "INSERT INTO messages (chat_id, role, content) VALUES (?, 'user', ?)",
            (chat_id, "first question"),
        ).lastrowid
        # A second turn lands while the provider is still streaming the first.
        con.execute(
            "INSERT INTO messages (chat_id, role, content) VALUES (?, 'user', ?)",
            (chat_id, "second question"),
        )

    with pytest.raises(StaleExchangeError):
        _insert_assistant_message(chat_id, "test/model-1", "an answer", user_id)


def test_the_ordinary_finalize_is_untouched_by_the_tail_guard(client):
    chat_id = make_chat(client, make_character(client))
    with get_db() as con:
        user_id = con.execute(
            "INSERT INTO messages (chat_id, role, content) VALUES (?, 'user', ?)",
            (chat_id, "a question"),
        ).lastrowid

    row = _insert_assistant_message(chat_id, "test/model-1", "an answer", user_id)
    assert row["content"] == "an answer"


# ---------------------------------------------------------------------------
# the omitted-image notice: detected in G7, sent here
# ---------------------------------------------------------------------------

def test_an_image_the_model_never_saw_is_announced_before_the_reply(
    client, monkeypatch,
):
    """build_image_part has collected these all along and the list went no
    further than a log line, so a completion answered from a payload with a
    picture missing looked exactly like one that had it."""
    import routers.completions as cr

    _install_stream(monkeypatch, ["Sure, "])
    chat_id = make_chat(client, make_character(client))

    # One attachment whose blob cannot be read: the exact state build_image_part
    # reports and the payload silently proceeded without.
    monkeypatch.setattr(cr, "_model_accepts_images", lambda meta: True)
    monkeypatch.setattr(cr, "prefetch_blobs", lambda shas: {})
    monkeypatch.setattr(
        cr, "_validate_request_attachments",
        lambda ids, model_id: [
            {"id": 7, "sha256": "f" * 64, "mime": "image/png"},
        ] if ids else [],
    )

    events = asyncio.run(_collect(lambda: complete_chat_stream(
        chat_id,
        CompleteRequest(message="what is this?", model_id="test/model-1",
                        attachments=[7]),
    )))

    types = [e["type"] for e in events]
    notice = next(e for e in events if e["type"] == "notice")
    assert notice["code"] == "images_omitted"
    assert notice["count"] == 1
    assert types.index("notice") < types.index("delta"), (
        "a model that never saw the picture changes how the answer should be "
        "read, so it cannot arrive as a footnote after it"
    )


def test_an_ordinary_reply_carries_no_notice(client, monkeypatch):
    _install_stream(monkeypatch, ["All good."])
    chat_id = make_chat(client, make_character(client))
    events = asyncio.run(_collect(lambda: complete_chat_stream(
        chat_id, CompleteRequest(message="hi", model_id="test/model-1"),
    )))
    assert not [e for e in events if e["type"] == "notice"]


# ---------------------------------------------------------------------------
# the shared body: what every endpoint must do, asserted on every endpoint
# ---------------------------------------------------------------------------

def test_every_endpoint_speaks_the_same_event_grammar(client, monkeypatch):
    _install_stream(monkeypatch, ["One ", "two."])
    for label, _chat_id, make in _endpoints(client):
        events = asyncio.run(_collect(make))
        types = [e["type"] for e in events]
        assert types[0] == "user_message", label
        assert "delta" in types, label
        assert types[-1] == "done", label


def test_every_endpoint_refuses_a_reply_with_nothing_to_show(
    client, monkeypatch, voice_on,
):
    """A reply that is only delivery tags renders as a permanently empty
    bubble. The gate existed on all three; this is what keeps it there."""
    _install_stream(monkeypatch, ["[whisper]", " [softly]"])
    for label, _chat_id, make in _endpoints(client):
        events = asyncio.run(_collect(make))
        assert events[-1]["type"] == "error", label
        assert events[-1]["status"] == 502, label


def test_every_endpoint_reports_a_provider_failure(client, monkeypatch):
    from openrouter import OpenRouterError

    _install_stream(
        monkeypatch, ["Half "],
        fail_after=1, error=OpenRouterError("openrouter_rate_limited"),
    )
    for label, _chat_id, make in _endpoints(client):
        events = asyncio.run(_collect(make))
        assert events[-1]["type"] == "error", label
        assert events[-1]["code"] == "openrouter_rate_limited", label


def test_only_send_keeps_the_partial_of_a_failed_provider_call(
    client, monkeypatch,
):
    """Regenerate and edit write nothing until their atomic swap, so keeping
    their partial would destroy the complete reply it was meant to replace.
    Send has no such reply to protect - it has a user turn already committed
    and text the user has already read."""
    from openrouter import OpenRouterError

    _install_stream(
        monkeypatch, ["Half an answer. "],
        fail_after=1, error=OpenRouterError("openrouter_rate_limited"),
    )
    kept = {}
    for label, chat_id, make in _endpoints(client):
        events = asyncio.run(_collect(make))
        kept[label] = events[-1].get("partial_saved", False)
        if label != "completion":
            contents = [m["content"] for m in get_messages(client, chat_id)]
            assert "Half an answer. " not in contents, label

    assert kept == {"completion": True, "regenerate": False, "edit": False}


def test_every_endpoint_closes_its_speaker_on_the_way_out(client, monkeypatch):
    """A surviving speaker keeps synthesising a reply nobody is listening to,
    and a stale registry entry points Speak at a reply that finished minutes
    ago. The `finally` is shared now - so is this check."""
    import routers.completions as cr
    from tts import stream_hook

    _install_stream(monkeypatch, ["Some text."])
    opened: list[object] = []
    real_open = stream_hook.open_speaker

    def spy(enabled, **kwargs):
        hook = real_open(enabled, **kwargs)
        opened.append(hook)
        return hook

    monkeypatch.setattr(cr.stream_hook, "open_speaker", spy)

    for label, chat_id, make in _endpoints(client):
        asyncio.run(_collect(make))
        assert stream_hook.enable_live(chat_id) is False, (
            f"{label}: a finished reply is still registered as live"
        )
    assert len(opened) == 3


def test_an_aborted_stream_closes_its_speaker_too(client, monkeypatch):
    """The path where cleanup is hardest: the `finally` runs while a
    CancelledError is in flight, so it cannot await. A speaker left behind
    here keeps a GPU busy on a reply whose reader is already gone."""
    import routers.completions as cr
    from tts import stream_hook

    _install_hanging_stream(monkeypatch, ["Some text. "])
    chat_id = make_chat(client, make_character(client))

    opened: list[object] = []
    real_open = stream_hook.open_speaker

    def spy(enabled, **kwargs):
        hook = real_open(True, **kwargs)   # force a real speaker, not _Silent
        opened.append(hook)
        return hook

    monkeypatch.setattr(cr.stream_hook, "open_speaker", spy)
    monkeypatch.setattr(
        cr.tts_runtime, "make_stream_synth",
        lambda **kw: _fake_synth(),
    )

    asyncio.run(_abort_while_waiting(
        lambda: complete_chat_stream(
            chat_id, CompleteRequest(message="hi", model_id="test/model-1"),
        ),
        after_events=2,
    ))

    assert opened, "no speaker was opened"
    assert opened[0]._speaker is None, "the speaker outlived the aborted stream"
    assert stream_hook.enable_live(chat_id) is False


# ---------------------------------------------------------------------------
# KÖK 8 - the write transactions left the event loop
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("post", "/api/v1/chats", {"character_id": None, "title": "t"}),
        ("patch", "/api/v1/chats/{chat}", {"title": "renamed"}),
        ("patch", "/api/v1/characters/{char}", {"name": "Renamed"}),
        ("patch", "/api/v1/personas/{persona}", {"display_name": "Nova II"}),
    ],
)
def test_write_handlers_do_not_take_the_writer_lock_on_the_event_loop(
    client, monkeypatch, method, path, payload,
):
    """BEGIN IMMEDIATE takes SQLite's writer lock. Taken from a coroutine, it
    freezes every live SSE stream in the process until it is granted - up to
    the full 15 s busy_timeout when another writer holds it.

    Observed by which THREAD opens the connection, because that is the thing
    the fix actually changes; the transaction itself is untouched.
    """
    import database
    from conftest import make_character, make_persona

    char_id = make_character(client)
    chat_id = make_chat(client, char_id)
    persona_id = make_persona(client)

    main = threading.get_ident()
    threads: list[int] = []
    real_get_db = database.get_db

    def watching_get_db(*a, **kw):
        threads.append(threading.get_ident())
        return real_get_db(*a, **kw)

    for module in ("routers.chats", "routers.characters", "routers.personas"):
        monkeypatch.setattr(f"{module}.get_db", watching_get_db)

    body = dict(payload)
    if body.get("character_id", "missing") is None:
        body["character_id"] = char_id
    url = path.format(chat=chat_id, char=char_id, persona=persona_id)

    threads.clear()
    resp = getattr(client, method)(url, json=body)
    assert resp.status_code == 200 or resp.status_code == 201, resp.text
    assert threads, "the handler opened no connection at all"
    assert main not in threads, (
        "a BEGIN IMMEDIATE transaction still runs on the event loop"
    )


def test_the_variant_flip_also_left_the_loop(client, monkeypatch):
    """activate_variant is a pure state switch, but it is still a write txn."""
    import database
    import routers.chats as chats_router

    chat_id, _user_id, asst_id = _seed(client)
    main = threading.get_ident()
    threads: list[int] = []
    real_get_db = database.get_db

    def watching_get_db(*a, **kw):
        threads.append(threading.get_ident())
        return real_get_db(*a, **kw)

    monkeypatch.setattr(chats_router, "get_db", watching_get_db)
    resp = client.post(
        f"/api/v1/chats/{chat_id}/messages/{asst_id}/activate",
    )
    assert resp.status_code == 200, resp.text
    assert threads and main not in threads


@pytest.fixture()
def voice_on(monkeypatch):
    """Delivery-tag stripping enabled, without a voice engine anywhere near it.

    The display-view gates only exist when stripping is on; with it off the raw
    text IS the display text and there is nothing to judge.
    """
    monkeypatch.setattr(voice_tags, "stripping_active", lambda: True)
    return True


# ---------------------------------------------------------------------------
# The red line: delivery is incremental, not one buffer flushed at the end
# ---------------------------------------------------------------------------

def test_a_delta_reaches_the_reader_before_the_provider_stops_talking(
    client, monkeypatch,
):
    """The one promise a streaming endpoint exists to make, and the only one
    its own tests never checked.

    Every other test in this file - and every test in test_streaming.py - reads
    the body to exhaustion and then asserts on the concatenation. A server that
    collected the whole reply and flushed it in one piece at the very end would
    pass all of them, byte for byte, while the reader sat looking at nothing for
    the length of the reply and then got everything at once. That is the entire
    difference between these endpoints and the non-streaming /complete, and
    nothing was watching it.

    So the provider below parks on an event that is only released AFTER a delta
    has already been handed to this test. Under buffering the release never
    comes, the pull hits its ceiling, and the failure is a sentence rather than
    a hung suite - which matters, because the one place buffering WAS
    detectable today (the abort tests, which park the provider the same way)
    would have reported it as a job that never finished.
    """
    import contextlib

    import routers.completions as cr

    gate: dict = {}

    def fake_stream(messages, model_id, gen_params, provider, **kwargs):
        async def gen():
            yield "Once "
            await gate["released"].wait()
            yield "upon a time."

        return gen()

    monkeypatch.setattr(cr, "complete_stream", fake_stream)

    async def drive(label, make):
        # Inside the coroutine: an asyncio.Event binds to the running loop, and
        # each endpoint here gets its own asyncio.run().
        gate["released"] = asyncio.Event()
        resp = await make()
        agen = resp.body_iterator
        seen: list[dict] = []
        try:
            while not any(e["type"] == "delta" for e in seen):
                chunk = await asyncio.wait_for(agen.__anext__(), 5.0)
                seen.append(json.loads(chunk[len("data: "):].strip()))
        except asyncio.TimeoutError:
            pytest.fail(
                f"{label}: not one event reached the reader while the provider "
                f"was still talking - this body is buffered, not streamed"
            )
        finally:
            gate["released"].set()
            with contextlib.suppress(Exception):
                await agen.aclose()
        return seen

    for label, chat_id, make in _endpoints(client):
        seen = asyncio.run(drive(label, make))
        # Exactly the first one: the second is still held behind the event, so
        # anything more than this would mean the endpoint had waited for it.
        assert [e["content"] for e in seen if e["type"] == "delta"] == ["Once "], (
            f"{label}: {seen}"
        )
