"""Off means off, one picture means one row, and the wire is what gets asserted.

Every test in this file exists because an audit found the corresponding
assurance missing or inert.

The defect that made it necessary: the image sink was handed to complete_stream
UNCONDITIONALLY while openrouter only checked whether a sink existed, so a
picture the provider volunteered was decoded, normalised and committed to the
vault for somebody who had never turned the feature on. The reply through
non-streaming /complete stored nothing from the byte-identical response, because
that path gated correctly. And the only assertion that existed looked at the
outbound payload - which was innocent: the app really had not asked.

Two lessons are encoded here as much as two behaviours. Assert the JSON BODY,
not the keyword argument that was supposed to produce it. And a test that traps
egress by patching a factory function proves nothing, because any code path that
builds its own client walks straight past it.
"""
from __future__ import annotations

import base64
import io
import json

import pytest
from PIL import Image

import attachments_service as svc
import database
import generated_images
import openrouter
import routers.completions as completions
from config import MAX_ATTACHMENTS_PER_MESSAGE
from tests.conftest import make_character, make_chat

BODY = {"message": "draw me something", "model_id": "test/model-1"}


def _png(colour=(200, 30, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), colour).save(buf, format="PNG")
    return buf.getvalue()


def _url(raw: bytes | None = None) -> str:
    return "data:image/png;base64," + base64.b64encode(
        raw if raw is not None else _png()
    ).decode("ascii")


def _chat(client) -> int:
    return make_chat(client, make_character(client, first_mes="Hi."))


def _counts() -> tuple[int, int]:
    with database.get_db() as con:
        return (
            con.execute("SELECT COUNT(*) c FROM attachments").fetchone()["c"],
            con.execute("SELECT COUNT(*) c FROM attachment_blobs").fetchone()["c"],
        )


def _enable() -> None:
    generated_images.set_image_output_enabled(True)


def _stream(monkeypatch, deltas, urls):
    def _s(messages, model_id, gen_params, provider, modalities=None,
           on_image=None, **kwargs):
        async def gen():
            for d in deltas:
                yield d
            if on_image is not None:
                for u in urls:
                    on_image(u)

        return gen()

    monkeypatch.setattr(completions, "complete_stream", _s)


def _events(resp) -> list[dict]:
    out = []
    for line in resp.iter_lines():
        line = line.strip()
        if line.startswith("data:"):
            out.append(json.loads(line[len("data:"):]))
    return out


# ── off means off ───────────────────────────────────────────────────────────

def test_nothing_is_stored_when_the_feature_is_off(client, monkeypatch):
    """The defect, on the path the app actually uses."""
    _stream(monkeypatch, ["a reply"], [_url()])
    # Deliberately NOT enabling the setting.
    chat = _chat(client)
    with client.stream("POST", f"/api/v1/chats/{chat}/complete/stream",
                       json=BODY) as resp:
        events = _events(resp)

    done = [e for e in events if e["type"] == "done"][0]
    assert done["assistant_message"]["attachments"] == []
    assert _counts() == (0, 0)


def test_an_unsolicited_wordless_reply_is_still_refused_when_off(client, monkeypatch):
    """The widened emptiness gate reads the decoded list. If that list could fill
    while the feature was off, a reply with no words would commit an assistant
    row with empty content instead of raising."""
    _stream(monkeypatch, [], [_url()])
    chat = _chat(client)
    with client.stream("POST", f"/api/v1/chats/{chat}/complete/stream",
                       json=BODY) as resp:
        events = _events(resp)
    assert [e["type"] for e in events][-1] == "error"
    assert _counts() == (0, 0)


def test_a_wordless_reply_is_kept_when_the_feature_is_on(client, monkeypatch):
    """The other half of the sentence above, and the half nobody wrote.

    The emptiness gate reads `not _visible_view(full_text).strip() and not
    generated`. The test above pins the first clause; the second clause - the
    one that stops a picture-only reply being thrown away as an
    `openrouter_error` - had no test at all, on or off. Deleting `and not
    generated` leaves the whole suite green while the app silently discards an
    image the model had already produced and the user had already paid for.
    """
    _enable()
    _stream(monkeypatch, [], [_url()])
    chat = _chat(client)
    with client.stream("POST", f"/api/v1/chats/{chat}/complete/stream",
                       json=BODY) as resp:
        events = _events(resp)

    assert [e["type"] for e in events][-1] == "done", [e["type"] for e in events]
    done = [e for e in events if e["type"] == "done"][0]
    assert len(done["assistant_message"]["attachments"]) == 1
    assert done["assistant_message"]["content"] == ""
    assert _counts() == (1, 1)


def test_nothing_is_stored_when_off_on_the_non_streaming_path(client, monkeypatch):
    async def _c(messages, model_id, gen_params, provider, **kwargs):
        return {"choices": [{"message": {
            "content": "a reply", "images": [{"image_url": {"url": _url()}}],
        }}]}

    monkeypatch.setattr(completions, "complete", _c)
    chat = _chat(client)
    resp = client.post(f"/api/v1/chats/{chat}/complete", json=BODY)
    assert resp.json()["assistant_message"]["attachments"] == []
    assert _counts() == (0, 0)


def test_an_unsolicited_image_does_not_keep_a_dead_stream_alive(client, monkeypatch):
    """saw_token is set off the image sink, so an unsolicited picture would have
    satisfied the first-token deadline for a provider that then said nothing."""
    calls: list[object] = []

    def _s(messages, model_id, gen_params, provider, modalities=None,
           on_image=None, **kwargs):
        calls.append(on_image)

        async def gen():
            yield "hi"

        return gen()

    monkeypatch.setattr(completions, "complete_stream", _s)
    chat = _chat(client)
    with client.stream("POST", f"/api/v1/chats/{chat}/complete/stream",
                       json=BODY) as resp:
        _events(resp)
    assert calls == [None], "a sink was handed over with the feature off"


# ── what actually leaves the process ────────────────────────────────────────

def _capture_wire(monkeypatch) -> list[dict]:
    """Intercept at the httpx boundary, so the assertion is on the JSON BODY."""
    bodies: list[dict] = []

    class _Resp:
        status_code = 200
        is_success = True

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    class _Client:
        async def post(self, url, headers=None, json=None, timeout=None):
            bodies.append(json)
            return _Resp()

        def stream(self, method, url, headers=None, json=None, timeout=None):
            bodies.append(json)

            class _Ctx:
                async def __aenter__(self):
                    class _S:
                        status_code = 200

                        async def aiter_bytes(self):
                            yield b"data: [DONE]\n\n"

                    return _S()

                async def __aexit__(self, *exc):
                    return False

            return _Ctx()

    monkeypatch.setattr(openrouter, "get_client", lambda: _Client())
    monkeypatch.setattr(openrouter, "get_secret", lambda name: "sk-test")
    return bodies


@pytest.mark.anyio
async def test_modalities_reaches_the_wire_when_not_streaming(
    anyio_backend, client, monkeypatch,
):
    bodies = _capture_wire(monkeypatch)
    await openrouter.complete([{"role": "user", "content": "hi"}], "m", {},
                              {"zdr": True},
                              modalities=openrouter.MODALITIES_WITH_IMAGE)
    assert bodies[0]["modalities"] == ["text", "image"]
    assert bodies[0]["provider"]["zdr"] is True


@pytest.mark.anyio
async def test_modalities_reaches_the_wire_when_streaming(
    anyio_backend, client, monkeypatch,
):
    bodies = _capture_wire(monkeypatch)
    async for _ in openrouter.complete_stream(
        [{"role": "user", "content": "hi"}], "m", {}, {"zdr": True},
        modalities=openrouter.MODALITIES_WITH_IMAGE,
    ):
        pass
    assert bodies[0]["modalities"] == ["text", "image"]
    assert bodies[0]["stream"] is True


@pytest.mark.anyio
async def test_the_wire_carries_no_such_key_when_none_was_asked(
    anyio_backend, client, monkeypatch,
):
    bodies = _capture_wire(monkeypatch)
    await openrouter.complete([{"role": "user", "content": "hi"}], "m", {},
                              {"zdr": True})
    assert "modalities" not in bodies[0]


@pytest.mark.anyio
async def test_a_generation_parameter_cannot_overwrite_it(
    anyio_backend, client, monkeypatch,
):
    """The assignment sits AFTER the gen_params spread on purpose."""
    bodies = _capture_wire(monkeypatch)
    await openrouter.complete(
        [{"role": "user", "content": "hi"}], "m",
        {"modalities": ["text"]}, {"zdr": True},
        modalities=openrouter.MODALITIES_WITH_IMAGE,
    )
    assert bodies[0]["modalities"] == ["text", "image"]


# ── one picture, one row ────────────────────────────────────────────────────

def test_the_same_picture_seen_twice_is_one_row(client, monkeypatch):
    """The DOCUMENTED shape is images on each delta PLUS a final aggregated
    message, so every picture is seen at least twice. Without dedup each became
    two rows sharing one blob - which also halved the per-message cap, because
    that counts rows."""
    url = _url()
    _stream(monkeypatch, ["here"], [url, url])
    _enable()
    chat = _chat(client)
    with client.stream("POST", f"/api/v1/chats/{chat}/complete/stream",
                       json=BODY) as resp:
        events = _events(resp)

    done = [e for e in events if e["type"] == "done"][0]
    assert len(done["assistant_message"]["attachments"]) == 1
    assert _counts() == (1, 1)


def test_dedup_happens_at_the_source():
    """Proven where it lives, so the end-to-end test above cannot be the only
    thing standing between the provider's ordinary shape and duplicate rows."""
    seen: list[str] = []

    async def drive():
        class _S:
            status_code = 200

            async def aiter_bytes(self):
                frame = (
                    'data: {"choices":[{"delta":{"images":'
                    '[{"image_url":{"url":"data:image/png;base64,AAAA"}}]},'
                    '"message":{"images":'
                    '[{"image_url":{"url":"data:image/png;base64,AAAA"}}]}}]}\n\n'
                )
                yield frame.encode()
                yield frame.encode()
                yield b"data: [DONE]\n\n"

        class _Ctx:
            async def __aenter__(self):
                return _S()

            async def __aexit__(self, *exc):
                return False

        class _Client:
            def stream(self, *a, **kw):
                return _Ctx()

        import contextlib

        with contextlib.ExitStack():
            pass
        return _Client()

    import anyio

    async def run():
        client_obj = await drive()
        orig_get_client = openrouter.get_client
        orig_get_secret = openrouter.get_secret
        openrouter.get_client = lambda: client_obj
        openrouter.get_secret = lambda name: "sk-test"
        try:
            async for _ in openrouter.complete_stream(
                [{"role": "user", "content": "hi"}], "m", {}, {},
                modalities=openrouter.MODALITIES_WITH_IMAGE,
                on_image=seen.append,
            ):
                pass
        finally:
            openrouter.get_client = orig_get_client
            openrouter.get_secret = orig_get_secret

    anyio.run(run)
    # Four sightings of one url across two frames and two shapes.
    assert seen == ["data:image/png;base64,AAAA"]


def test_a_provider_flood_cannot_exceed_the_cap(client, monkeypatch):
    urls = [_url(_png(colour=(i, i, i))) for i in range(12)]
    _stream(monkeypatch, ["flood"], urls)
    _enable()
    chat = _chat(client)
    with client.stream("POST", f"/api/v1/chats/{chat}/complete/stream",
                       json=BODY) as resp:
        events = _events(resp)

    done = [e for e in events if e["type"] == "done"][0]
    assert len(done["assistant_message"]["attachments"]) == MAX_ATTACHMENTS_PER_MESSAGE
    codes = [e.get("code") for e in events if e["type"] == "notice"]
    assert generated_images.NOTICE_IMAGE_REJECTED in codes


def test_the_surplus_is_never_decoded(client, monkeypatch):
    """The cap has to bite BEFORE the Pillow work, not after. Twenty solid
    5000x5000 PNGs are 84 KB each on the wire and cost seconds of pure CPU, and
    the surplus used to pay full decode + LANCZOS + re-encode before a COUNT
    threw it away - while holding the SQLite writer lock."""
    calls: list[int] = []
    real = svc.normalise_image

    def counted(data):
        calls.append(len(data))
        return real(data)

    monkeypatch.setattr(completions, "normalise_image", counted)
    urls = [_url(_png(colour=(i, i, i))) for i in range(12)]
    got, _notices = completions._decode_generated_images(urls, 1)
    assert len(got) == MAX_ATTACHMENTS_PER_MESSAGE
    assert len(calls) == MAX_ATTACHMENTS_PER_MESSAGE, (
        f"decoded {len(calls)} images in order to keep {MAX_ATTACHMENTS_PER_MESSAGE}"
    )


def test_no_pillow_work_happens_under_the_writer_lock(client, monkeypatch):
    """store_generated_image is the one backend writer that used to decode inside
    BEGIN IMMEDIATE. save_upload normalises before it opens its transaction, and
    this writer now takes bytes that are already final.

    This used to read the function's SOURCE and assert the string
    "normalise_image(" was absent, which a rename, an alias or a wrapper walks
    straight past - and which a harmless parameter rename would fail. So make
    the decoder a landmine instead: normalise BEFORE arming it, then arm it and
    do the write. A writer that decodes anything under its own lock steps on it
    and the test says so; one that only writes bytes it was handed cannot.
    """
    chat = _chat(client)
    with database.get_db() as con:
        msg = con.execute(
            "SELECT id FROM messages WHERE chat_id = ? ORDER BY id ASC LIMIT 1",
            (chat,),
        ).fetchone()["id"]

    prepared = svc.normalise_image(_png())          # decode happens out here

    def _landmine(_data):
        raise AssertionError(
            "store_generated_image decoded an image while holding the writer lock")

    monkeypatch.setattr(svc, "normalise_image", _landmine)

    with database.get_db() as con:
        con.execute("BEGIN IMMEDIATE")
        out = svc.store_generated_image(con, prepared, msg)

    assert out["mime"] == "image/png"
    assert _counts() == (1, 1)


# ── egress, trapped where it actually happens ───────────────────────────────

# Two tests lived here - a remote provider URL refused rather than fetched,
# and the guard-of-the-guard proving the socket trap can see a real fetch.
# Both were the same scenario, the same assertions and (for the second) the
# same three lines as test_generated_image_ingest.py's pair, which is where a
# reader looks for what happens to a provider's image URL. The trap itself is
# installed suite-wide by tests/conftest.py, so it covers this file's tests
# whether or not this file names it. Nothing about egress is specific to the
# on/off gate: it is refused identically with the feature on and off.


