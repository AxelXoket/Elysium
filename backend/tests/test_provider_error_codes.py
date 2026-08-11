"""A refused message must not read as a broken API key.

OpenRouter answers a moderation block with HTTP 403 and a documented
`ModerationErrorMetadata` body. Elysium folded 401 and 403 into one reason,
`openrouter_auth_failed`, which the frontend renders as "Authentication failed.
Please check your API key." So somebody whose roleplay prompt was refused was
told to go and fix a key that was never broken - and the app's own composer
puts a "go to secrets settings" call-to-action next to that message, pointing
at the wrong place with a button.

These tests drive the REAL openrouter.complete() and complete_stream() with a
fake transport, because the status-to-reason table had no direct test of any
kind before this file: every existing provider-failure test hands the router a
ready-made OpenRouterError, which proves the router's mapping and nothing about
where those reasons come from. The whole table is pinned here, not just 403,
since it is the table that was easy to get wrong.
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest

import openrouter
from openrouter import OpenRouterError

from conftest import make_character, make_chat


# A real 403 body, trimmed to the fields the code looks at plus the ones it must
# be careful with. `flagged_input` is a verbatim copy of what the user typed and
# `message` is the provider's prose; neither may reach the reader or a log.
MODERATION_BODY = {
    "error": {
        "code": 403,
        "message": "This request was flagged by the provider's moderation.",
        "metadata": {
            "reasons": ["harassment"],
            "flagged_input": "SECRETPROMPTTEXT",
            "provider_name": "SomeProvider",
            "model_slug": "test/model-1",
        },
    },
}

# The other documented metadata shape: an upstream provider failure relayed by
# OpenRouter. It has metadata, but no `reasons`, so it must NOT be read as a
# moderation block.
PROVIDER_ERROR_BODY = {
    "error": {
        "code": 403,
        "message": "Provider returned an error",
        "metadata": {"provider_name": "SomeProvider", "raw": "upstream text"},
    },
}


class _PostClient:
    """The narrowest shape complete() needs: one awaitable post()."""

    def __init__(self, response: httpx.Response):
        self._response = response

    async def post(self, *a, **kw):
        return self._response


class _StreamClient:
    """The narrowest shape complete_stream() needs.

    Offers only status_code and aiter_bytes on the response, deliberately: a
    call site that reached for aread() or .text on the error path would fail
    here rather than quietly buffering an unbounded body.
    """

    def __init__(self, status_code: int, raw: bytes):
        self._status_code = status_code
        self._raw = raw

    def stream(self, *a, **kw):
        status_code, raw = self._status_code, self._raw

        class _Ctx:
            async def __aenter__(self):
                class _Resp:
                    pass

                resp = _Resp()
                resp.status_code = status_code

                async def aiter_bytes():
                    yield raw

                resp.aiter_bytes = aiter_bytes
                return resp

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


@pytest.fixture()
def fake_key(monkeypatch):
    monkeypatch.setattr(openrouter, "get_secret", lambda name: "sk-test")


def _response(status: int, body: object | bytes = b"") -> httpx.Response:
    if isinstance(body, bytes):
        return httpx.Response(status_code=status, content=body)
    return httpx.Response(status_code=status, json=body)


async def _complete_reason(monkeypatch, response: httpx.Response) -> str:
    monkeypatch.setattr(openrouter, "get_client", lambda: _PostClient(response))
    with pytest.raises(OpenRouterError) as excinfo:
        await openrouter.complete([{"role": "user", "content": "hi"}],
                                  "test/model-1", {}, None)
    return excinfo.value.reason


async def _stream_reason(monkeypatch, status: int, raw: bytes) -> str:
    monkeypatch.setattr(openrouter, "get_client",
                        lambda: _StreamClient(status, raw))
    with pytest.raises(OpenRouterError) as excinfo:
        async for _ in openrouter.complete_stream(
            [{"role": "user", "content": "hi"}], "test/model-1", {}, None,
        ):
            pass
    return excinfo.value.reason


# ---------------------------------------------------------------------------
# The whole status table, through the real call
# ---------------------------------------------------------------------------

# 403 is absent on purpose: it is the one status whose answer depends on the
# body, and it has its own tests below.
STATUS_TABLE = [
    (401, "openrouter_auth_failed"),
    (402, "openrouter_insufficient_credits"),
    (404, "openrouter_error"),
    (408, "openrouter_error"),
    (413, "openrouter_error"),
    (429, "openrouter_rate_limited"),
    (500, "openrouter_server_error"),
    (502, "openrouter_server_error"),
    (503, "openrouter_server_error"),
]


@pytest.mark.anyio
@pytest.mark.parametrize("status,reason", STATUS_TABLE)
async def test_the_status_table_holds_without_streaming(
    anyio_backend, monkeypatch, fake_key, status: int, reason: str,
):
    assert await _complete_reason(monkeypatch, _response(status)) == reason


@pytest.mark.anyio
@pytest.mark.parametrize("status,reason", STATUS_TABLE)
async def test_the_status_table_holds_while_streaming(
    anyio_backend, monkeypatch, fake_key, status: int, reason: str,
):
    """Same table, other code path. The two used to be hand-kept copies."""
    assert await _stream_reason(monkeypatch, status, b"") == reason


# ---------------------------------------------------------------------------
# 403: the bug this file exists for
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_a_moderation_block_is_not_reported_as_an_auth_failure(
    anyio_backend, monkeypatch, fake_key,
):
    reason = await _complete_reason(
        monkeypatch, _response(403, MODERATION_BODY),
    )
    assert reason == "openrouter_moderation_blocked"
    assert reason != "openrouter_auth_failed"


@pytest.mark.anyio
async def test_a_moderation_block_survives_the_streaming_path_too(
    anyio_backend, monkeypatch, fake_key,
):
    reason = await _stream_reason(
        monkeypatch, 403, json.dumps(MODERATION_BODY).encode("utf-8"),
    )
    assert reason == "openrouter_moderation_blocked"


@pytest.mark.anyio
async def test_a_mid_stream_moderation_block_is_named_as_one(
    anyio_backend, monkeypatch, fake_key,
):
    """A provider can also refuse partway through its own output, in which case
    the 403 arrives as an SSE frame rather than an HTTP status."""
    frame = dict(MODERATION_BODY)
    raw = (
        'data: {"choices":[{"delta":{"content":"Once "}}]}\n\n'
        f"data: {json.dumps(frame)}\n\n"
    ).encode("utf-8")

    monkeypatch.setattr(openrouter, "get_client", lambda: _StreamClient(200, raw))
    got: list[str] = []
    with pytest.raises(OpenRouterError) as excinfo:
        async for delta in openrouter.complete_stream(
            [{"role": "user", "content": "hi"}], "test/model-1", {}, None,
        ):
            got.append(delta)

    assert got == ["Once "]
    assert excinfo.value.reason == "openrouter_moderation_blocked"


@pytest.mark.anyio
async def test_a_malformed_frame_costs_its_text_and_says_nothing(
    anyio_backend, monkeypatch, fake_key, caplog,
):
    """CHARACTERISATION, not approval. KUSUR-DEFTERI K-20.

    Dropping the frame is the right trade and the code says why: ending the
    turn here would skip finalize(), voice.finish() and drain_events, so a
    reply the reader had already finished reading would come back as an error
    banner over shortened text. One frame is a hole, the whole turn is a loss.

    What this pins is the SILENCE around the hole. The stream yields the good
    frames and the caller receives nothing else - no count, no terminal field,
    no notice - even though completions.py already runs a `notice` channel
    built to say "something of yours was quietly dropped" (it carries
    NOTICE_IMAGE_REJECTED today). The carrier exists; this one path is not
    wired to it. Connect it and this test goes red, which is the point.
    """
    raw = (
        'data: {"choices":[{"delta":{"content":"Once upon "}}]}\n\n'
        'data: {not json at all\n\n'
        'data: {"choices":[{"delta":{"content":"a time."}}]}\n\n'
        "data: [DONE]\n\n"
    ).encode("utf-8")

    monkeypatch.setattr(openrouter, "get_client", lambda: _StreamClient(200, raw))
    got: list[str] = []
    with caplog.at_level("ERROR"):
        async for delta in openrouter.complete_stream(
            [{"role": "user", "content": "hi"}], "test/model-1", {}, None,
        ):
            got.append(delta)

    # The text around the hole survives, which is the half that is correct.
    # And this list being exactly the good chunks IS the silence: the caller
    # has no other channel out of this generator.
    assert got == ["Once upon ", "a time."]

    # The only record anywhere is a log line, and a log file on disk is not a
    # channel the person reading the reply has.
    messages = [r.getMessage() for r in caplog.records]
    assert any("Malformed stream frame" in m for m in messages)
    assert any("unparseable frame" in m for m in messages), (
        "not even the end-of-stream tally was written")


@pytest.mark.anyio
async def test_a_mid_stream_403_without_moderation_metadata_stays_generic(
    anyio_backend, monkeypatch, fake_key,
):
    raw = (
        f"data: {json.dumps(PROVIDER_ERROR_BODY)}\n\n"
    ).encode("utf-8")

    monkeypatch.setattr(openrouter, "get_client", lambda: _StreamClient(200, raw))
    with pytest.raises(OpenRouterError) as excinfo:
        async for _ in openrouter.complete_stream(
            [{"role": "user", "content": "hi"}], "test/model-1", {}, None,
        ):
            pass

    assert excinfo.value.reason == "openrouter_error"


@pytest.mark.anyio
@pytest.mark.parametrize("body,label", [
    (PROVIDER_ERROR_BODY, "an upstream provider failure relayed as 403"),
    ({"error": {"code": 403, "message": "no metadata at all"}}, "no metadata"),
    ({"error": {"code": 403, "metadata": {"reasons": "harassment"}}},
     "reasons present but not a list"),
    (b"<html><body>403 Forbidden</body></html>", "an HTML page from a proxy"),
    (b"", "an empty body"),
    (b"{ truncated", "a body that is not JSON"),
])
async def test_an_unrecognised_403_does_not_claim_moderation(
    anyio_backend, monkeypatch, fake_key, body, label: str,
):
    """Guessing "your message was blocked" for a 403 that came from a corporate
    proxy or a CDN is the same lie as the one being fixed, pointed the other
    way. An unrecognised body earns the generic code."""
    reason = await _complete_reason(monkeypatch, _response(403, body))
    assert reason == "openrouter_error", label


@pytest.mark.anyio
async def test_an_oversized_403_body_is_not_parsed(
    anyio_backend, monkeypatch, fake_key,
):
    """The peek is bounded. A body past the cap is classified on status alone,
    which means generic - not a crash, and not a hang."""
    padded = dict(MODERATION_BODY)
    padded["padding"] = "x" * (openrouter._ERROR_BODY_PEEK_LIMIT + 1)

    reason = await _complete_reason(monkeypatch, _response(403, padded))
    assert reason == "openrouter_error"


def _body_of_exactly(size: int) -> bytes:
    """A valid moderation body padded to exactly `size` bytes."""
    body = dict(MODERATION_BODY)
    body["padding"] = ""
    raw = json.dumps(body).encode("utf-8")
    if len(raw) > size:                     # pragma: no cover - guards the test
        raise AssertionError("the fixture body already exceeds the target size")
    body["padding"] = "x" * (size - len(raw))
    return json.dumps(body).encode("utf-8")


@pytest.mark.anyio
@pytest.mark.parametrize("delta,expected", [
    (0, "openrouter_moderation_blocked"),
    (1, "openrouter_error"),
])
async def test_the_peek_limit_is_a_ceiling_not_a_fence_post(
    anyio_backend, monkeypatch, fake_key, delta: int, expected: str,
):
    """A mutation round changed `>` to `>=` here and nothing noticed: every
    other test sat far from the boundary, so the one line that decides where
    the boundary IS was only ever exercised in its interior."""
    limit = openrouter._ERROR_BODY_PEEK_LIMIT
    raw = _body_of_exactly(limit + delta)
    assert len(raw) == limit + delta

    reason = await _complete_reason(monkeypatch, _response(403, raw))
    assert reason == expected


@pytest.mark.anyio
@pytest.mark.parametrize("delta,expected", [
    (0, "openrouter_moderation_blocked"),
    (1, "openrouter_error"),
])
async def test_the_streamed_peek_stops_at_the_same_boundary(
    anyio_backend, monkeypatch, fake_key, delta: int, expected: str,
):
    """The streaming path reads its body in chunks and stops at the cap, which
    is a second `>` on the same number. Same fence post, other code."""
    limit = openrouter._ERROR_BODY_PEEK_LIMIT
    reason = await _stream_reason(monkeypatch, 403, _body_of_exactly(limit + delta))
    assert reason == expected


# ---------------------------------------------------------------------------
# The body is read for its shape and for nothing else
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_nothing_from_the_403_body_reaches_the_reader_or_the_log(
    anyio_backend, monkeypatch, fake_key, caplog,
):
    """Parsing the body is a new liberty this fix takes, so the privacy promise
    it sits next to gets its own test. The prompt text and the moderation label
    are both about the user; neither may leave as prose."""
    with caplog.at_level(logging.DEBUG, logger="openrouter"):
        reason = await _complete_reason(
            monkeypatch, _response(403, MODERATION_BODY),
        )

    assert reason == "openrouter_moderation_blocked"
    logged = " ".join(r.getMessage() for r in caplog.records)
    for secret in ("SECRETPROMPTTEXT", "harassment", "SomeProvider",
                   "flagged by the provider"):
        assert secret not in logged, secret
        assert secret not in reason


@pytest.mark.anyio
async def test_nothing_from_a_streamed_403_body_reaches_the_log(
    anyio_backend, monkeypatch, fake_key, caplog,
):
    with caplog.at_level(logging.DEBUG, logger="openrouter"):
        reason = await _stream_reason(
            monkeypatch, 403, json.dumps(MODERATION_BODY).encode("utf-8"),
        )

    assert reason == "openrouter_moderation_blocked"
    logged = " ".join(r.getMessage() for r in caplog.records)
    for secret in ("SECRETPROMPTTEXT", "harassment", "SomeProvider"):
        assert secret not in logged, secret


# ---------------------------------------------------------------------------
# What the reader's browser actually receives
# ---------------------------------------------------------------------------

def test_a_blocked_send_answers_403_and_not_401(client, provider):
    """401 is what made the composer offer a "check your API key" button."""
    char_id = make_character(client)
    chat_id = make_chat(client, char_id)
    provider.error = OpenRouterError("openrouter_moderation_blocked")

    resp = client.post(
        f"/api/v1/chats/{chat_id}/complete",
        json={"message": "hi", "model_id": "test/model-1"},
    )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "openrouter_moderation_blocked"


def test_a_blocked_stream_reports_the_same_code(client, monkeypatch):
    import routers.completions as completions_router

    def _stream(*a, **kw):
        async def gen():
            raise OpenRouterError("openrouter_moderation_blocked")
            yield  # pragma: no cover - unreachable, makes gen() a generator

        return gen()

    monkeypatch.setattr(completions_router, "complete_stream", _stream)

    char_id = make_character(client)
    chat_id = make_chat(client, char_id)

    with client.stream(
        "POST", f"/api/v1/chats/{chat_id}/complete/stream",
        json={"message": "hi", "model_id": "test/model-1"},
    ) as resp:
        events = [
            json.loads(line[len("data:"):].strip())
            for line in resp.iter_lines()
            if line.strip().startswith("data:")
        ]

    assert events[-1]["type"] == "error"
    assert events[-1]["code"] == "openrouter_moderation_blocked"
    assert events[-1]["status"] == 403
