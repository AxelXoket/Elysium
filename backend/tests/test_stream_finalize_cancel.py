"""Bare asyncio.Task.cancel() landing inside routers.completions._stream_
exchange's persist step.

Context (measured, see openrouter.py's complete() and the `finalizing`
comment in _stream_exchange): anyio.to_thread.run_sync(fn,
abandon_on_cancel=False) defers a cancellation raised through anyio's own
CancelScope machinery - which is what a client disconnect is, in this app,
under this Starlette build - but it does NOT defer a bare asyncio.
Task.cancel(), the kind uvicorn's own forced-shutdown path uses
(server.py's `t.cancel()` once timeout_graceful_shutdown is exceeded). A
bare cancel unwinds the awaiting coroutine immediately while the worker
thread underneath keeps running, detached.

These tests reproduce that specific cancellation - not a client disconnect,
not GeneratorExit/aclose() (test_streaming_abort.py already covers those) -
by driving the streaming generator inside a real asyncio.Task and calling
.cancel() on that Task directly while a to_thread write is provably in
flight (a threading.Event proves the worker thread has started before the
cancel is issued).
"""
import asyncio
import threading

import routers.completions as cr
from routers.completions import complete_chat_stream, CompleteRequest

from conftest import make_character, make_chat, get_messages


def _install_stream(monkeypatch, deltas):
    def fake_stream(messages, model_id, gen_params, provider, **kwargs):
        async def gen():
            for d in deltas:
                yield d
        return gen()

    monkeypatch.setattr(cr, "complete_stream", fake_stream)


async def _drive_and_bare_cancel_at(agen_factory, started: threading.Event):
    """Start consuming the generator as a real asyncio.Task, wait until
    `started` fires (proving a worker thread is mid-write), then call
    Task.cancel() directly - the same call uvicorn's forced-shutdown path
    makes, not agen.aclose()."""
    async def drive():
        agen = await agen_factory()
        async for _chunk in agen:
            pass

    task = asyncio.ensure_future(drive())
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, started.wait, 5)
    task.cancel()
    try:
        await task
        raise AssertionError("expected the bare cancel to propagate")
    except asyncio.CancelledError:
        pass


def test_bare_cancel_during_finalize_does_not_duplicate_or_mislabel(client, monkeypatch):
    """GROUND + the fix under test.

    Cancel lands while finalize()'s own `_insert_assistant_message` write is
    genuinely in flight in its worker thread. Before the `finalizing` guard,
    this raced the detached write against the urgent-rescue's own duplicate
    insert: exactly one won the tail guard, and it was a coin flip whether
    the survivor was the correct, complete, truncated=False row or rescue's
    always-truncated=True copy. The fix must leave the ORIGINAL write as the
    only writer.
    """
    _install_stream(monkeypatch, ["Hello ", "world."])
    char_id = make_character(client)
    chat_id = make_chat(client, char_id)

    started = threading.Event()
    released = threading.Event()
    real_insert = cr._insert_assistant_message
    calls = []

    def slow_insert(*a, **kw):
        calls.append(a[2])  # the text argument
        started.set()
        released.wait(timeout=5)
        return real_insert(*a, **kw)

    monkeypatch.setattr(cr, "_insert_assistant_message", slow_insert)

    async def agen_factory():
        body = CompleteRequest(message="hi", model_id="test/model-1")
        resp = await complete_chat_stream(chat_id, body)
        return resp.body_iterator

    async def scenario():
        drive_coro = _drive_and_bare_cancel_at(agen_factory, started)
        await drive_coro
        # The write was genuinely still in flight at cancel time (blocked on
        # `released`) - let it finish and give it a moment to commit.
        released.set()
        await asyncio.sleep(0.2)

    asyncio.run(scenario())

    # Exactly one _insert_assistant_message call: the fix stops the urgent
    # rescue from attempting a second one while finalize's own write is
    # still outstanding.
    assert calls == ["Hello world."], calls

    msgs = get_messages(client, chat_id)
    assistant = [m for m in msgs if m["role"] == "assistant"]
    # first_mes + the one real reply - never two copies of the reply.
    assert len(assistant) == 2, assistant
    reply = assistant[-1]
    assert reply["content"] == "Hello world."
    # The reply was complete (both deltas were consumed before finalize()
    # started) - it must not be mislabelled as cut off.
    assert reply["truncated"] is False


def test_bare_cancel_during_finalize_still_notifies_notebook(client, monkeypatch):
    """The `_notify_notebook_from_thread` fix.

    finalize() never returns to `_stream_exchange` after a bare cancel, so
    the `_offer_to_notebook(chat_id)` call that normally follows
    `persisted = True` never runs - even though the detached write goes on
    to commit a real turn. Without notifying from inside the worker thread,
    the notebook silently stops hearing about turns saved this way.
    """
    _install_stream(monkeypatch, ["Hello ", "world."])
    char_id = make_character(client)
    chat_id = make_chat(client, char_id)

    started = threading.Event()
    released = threading.Event()
    real_insert = cr._insert_assistant_message
    notified = []
    notified_lock = threading.Lock()

    def slow_insert(*a, **kw):
        started.set()
        released.wait(timeout=5)
        return real_insert(*a, **kw)

    def fake_offer(cid):
        with notified_lock:
            notified.append(cid)

    monkeypatch.setattr(cr, "_insert_assistant_message", slow_insert)
    monkeypatch.setattr(cr, "_offer_to_notebook", fake_offer)

    async def agen_factory():
        body = CompleteRequest(message="hi", model_id="test/model-1")
        resp = await complete_chat_stream(chat_id, body)
        return resp.body_iterator

    async def scenario():
        await _drive_and_bare_cancel_at(agen_factory, started)
        released.set()
        # call_soon_threadsafe needs the loop to actually get a turn.
        for _ in range(20):
            await asyncio.sleep(0.05)
            with notified_lock:
                if notified:
                    break

    asyncio.run(scenario())

    with notified_lock:
        assert notified == [chat_id], notified


def test_bare_cancel_before_finalize_still_rescues_partial(client, monkeypatch):
    """POSITIVE CONTROL: the `finalizing` guard must only suppress rescue
    while finalize() itself is genuinely in flight. Cancelling EARLIER -
    mid-stream, before finalize() is ever called - must still go through the
    pre-existing urgent-rescue path and persist the partial, exactly as
    test_streaming_abort.py's GeneratorExit variant already proves for the
    aclose() path. This proves the guard is scoped correctly, not just
    "always skip rescue"."""
    release_delta = threading.Event()
    started = threading.Event()

    def fake_stream(messages, model_id, gen_params, provider, **kwargs):
        async def gen():
            yield "Partial "
            started.set()
            # Block INSIDE the generator, before finalize() could ever run -
            # this is where the bare cancel below will land.
            await asyncio.get_event_loop().run_in_executor(
                None, release_delta.wait, 5)
            yield "never reached"
        return gen()

    monkeypatch.setattr(cr, "complete_stream", fake_stream)
    char_id = make_character(client)
    chat_id = make_chat(client, char_id)

    async def agen_factory():
        body = CompleteRequest(message="hi", model_id="test/model-1")
        resp = await complete_chat_stream(chat_id, body)
        return resp.body_iterator

    async def scenario():
        await _drive_and_bare_cancel_at(agen_factory, started)
        release_delta.set()
        await asyncio.sleep(0.1)

    asyncio.run(scenario())

    msgs = get_messages(client, chat_id)
    assistant = [m for m in msgs if m["role"] == "assistant"]
    assert len(assistant) == 2, assistant
    assert assistant[-1]["content"] == "Partial "
    assert assistant[-1]["truncated"] is True


def test_bare_cancel_during_speaker_build_closes_orphaned_hook(client, monkeypatch):
    """The `_open_speaker` lock/`_build_state` fix. Without it, a hook whose
    build (VRAM allocation) finishes AFTER the cancel has already been
    handled is appended to a list nobody looks at again - orphaned, still
    holding its engine. With the fix, whichever side loses the race closes
    the hook."""
    _install_stream(monkeypatch, ["Hello ", "world."])
    char_id = make_character(client)
    chat_id = make_chat(client, char_id)

    started = threading.Event()
    released = threading.Event()
    closed = threading.Event()

    class _FakeHook:
        def close(self):
            closed.set()

    def slow_open_speaker(*a, **kw):
        started.set()
        released.wait(timeout=5)
        return _FakeHook()

    monkeypatch.setattr(cr.stream_hook, "open_speaker", slow_open_speaker)

    async def agen_factory():
        body = CompleteRequest(message="hi", model_id="test/model-1")
        resp = await complete_chat_stream(chat_id, body)
        return resp.body_iterator

    async def scenario():
        await _drive_and_bare_cancel_at(agen_factory, started)
        released.set()
        await asyncio.get_event_loop().run_in_executor(None, closed.wait, 5)

    asyncio.run(scenario())

    assert closed.is_set(), "orphaned speaker hook was never closed"
