"""The biggest avoidable cost on the send path was the speaker's own set-up.

With continuous voice on, `stream_hook.open_speaker` BUILDS the engine:
SpeakHook.__init__ -> enable() -> make_stream_synth, which walks the models
folder uncached and opens the vault about five times. `enable`'s own docstring
calls it "hundreds of milliseconds". It ran synchronously on the event loop, in
front of the first SSE event, the provider request, the first token AND the first
audio - and while it ran, every OTHER live stream in the process was frozen.

The two sibling endpoints already did it correctly: `/tts/speak_live` is a plain
`def` so FastAPI hands it to a threadpool, and `/tts/speak_stream` wraps the
identical call in `anyio.to_thread.run_sync`, with the reason in a comment. Only
the continuous-mode send paid.

Both tests here prove behaviour by STARVATION rather than by inspecting how the
code is written: the loop is given something to do that only a free loop can do.
"""
from __future__ import annotations

import asyncio
import threading

import anyio.to_thread
import pytest

import routers.completions as completions
from tests.conftest import make_character, make_chat
from tests.test_streaming import BODY, stream_provider  # noqa: F401


async def _first_event(chat: int, body: completions.CompleteRequest):
    """Drive the SSE generator as far as its first yield and stop.

    `open_speaker` happens before that yield, so this is the smallest amount of
    the endpoint that exercises it.
    """
    resp = await completions.complete_chat_stream(chat, body)
    agen = resp.body_iterator
    try:
        return await agen.__anext__()
    finally:
        await agen.aclose()


# ── the loop stays free while the engine is built ────────────────────────────

@pytest.mark.anyio
async def test_building_the_speaker_does_not_freeze_the_loop(
    anyio_backend, client, stream_provider, monkeypatch,      # noqa: F811
):
    char = make_character(client, first_mes="Hi.")
    chat = make_chat(client, char)

    real = completions.stream_hook.open_speaker
    gate = threading.Event()
    opened_by_the_loop: list[bool] = []

    def slow_open(*args, **kwargs):
        # Stands in for the models-folder walk and the five vault opens. If this
        # runs on the loop it holds the only thread there is and open_the_gate
        # never gets to run, so the wait times out and this assertion fires.
        assert gate.wait(3.0), "the event loop was frozen while the engine built"
        return real(*args, **kwargs)

    monkeypatch.setattr(completions.stream_hook, "open_speaker", slow_open)

    async def open_the_gate():
        await asyncio.sleep(0.05)
        opened_by_the_loop.append(True)
        gate.set()

    opener = asyncio.ensure_future(open_the_gate())
    try:
        first = await _first_event(chat, completions.CompleteRequest(**BODY))
    finally:
        opener.cancel()

    assert opened_by_the_loop == [True]
    assert first.startswith("data:")


# ── and a hook orphaned by cancellation is closed, not leaked ────────────────

@pytest.mark.anyio
async def test_a_speaker_orphaned_by_a_disconnect_is_closed(
    anyio_backend, client, stream_provider, monkeypatch,      # noqa: F811
):
    """`run_sync` does not abandon its worker when the awaiting task is
    cancelled: the function finishes, and only then is CancelledError delivered.
    So the built hook - holding a loaded engine and its VRAM - is returned into
    a frame that is already unwinding, before the generator's own try/finally
    exists to close it. This reproduces exactly that ordering.
    """
    char = make_character(client, first_mes="Hi.")
    chat = make_chat(client, char)

    closed: list[str] = []

    class FakeHook:
        active = False

        def close(self):
            closed.append("closed")

        def feed(self, delta):
            pass

        def events(self):
            return []

    monkeypatch.setattr(completions.stream_hook, "open_speaker",
                        lambda *a, **k: FakeHook())

    real_run_sync = anyio.to_thread.run_sync

    async def cancel_right_after_the_build(fn, *args, **kwargs):
        out = await real_run_sync(fn, *args, **kwargs)
        if getattr(fn, "__name__", "") == "_open_speaker":
            raise asyncio.CancelledError()
        return out

    monkeypatch.setattr(anyio.to_thread, "run_sync", cancel_right_after_the_build)

    with pytest.raises(asyncio.CancelledError):
        await _first_event(chat, completions.CompleteRequest(**BODY))

    assert closed == ["closed"], "the orphaned speaker was left holding the engine"


# ── nothing about the reply changed ─────────────────────────────────────────

def test_a_streamed_reply_is_unchanged(client, stream_provider):    # noqa: F811
    from tests.test_streaming import read_events

    char = make_character(client, first_mes="Hi.")
    chat = make_chat(client, char)

    with client.stream("POST", f"/api/v1/chats/{chat}/complete/stream",
                       json=BODY) as resp:
        assert resp.status_code == 200
        events = read_events(resp)

    kinds = [e["type"] for e in events]
    assert kinds[0] == "user_message"
    assert kinds[-1] == "done"
    assert "".join(e["content"] for e in events if e["type"] == "delta") == (
        "Once upon a time."
    )
