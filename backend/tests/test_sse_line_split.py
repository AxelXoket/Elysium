"""A model that writes one unusual character must not lose a sentence.

httpx's `aiter_lines()` was changed in 0.24.0 to mirror `str.splitlines()`,
which recognises eleven line boundaries. The WHATWG event-stream format
recognises three: CRLF, LF, CR. Of the eight extra ones, five are at or below
U+001F and RFC 8259 forces every conforming JSON writer to escape those - so
they can never arrive literally. Exactly three can: U+0085 NEL, U+2028 LINE
SEPARATOR and U+2029 PARAGRAPH SEPARATOR. JavaScript's JSON.stringify escapes
none of them and OpenRouter serialises in TypeScript.

So one U+2028 inside a content delta split the frame in two. Half one failed
`json.loads`; half two failed the `data:` prefix test and was dropped without
even a log line. The reader lost that piece of the reply from the screen, from
the vault and from the voice, and nothing said so.

Every character below is written as an escape ON PURPOSE. A literal U+2028 in a
source file is invisible to review and does not survive every editor - and
`json.dumps` would escape it back into safety, which is how this bug hides from
its own test.
"""
from __future__ import annotations

import pytest

from openrouter import _aiter_sse_lines

LS = " "      # LINE SEPARATOR - the one seen in the wild
PS = " "      # PARAGRAPH SEPARATOR
NEL = ""     # NEXT LINE; 0x85 > 0x1F, so JSON need not escape it either


class _Response:
    """Only what `_aiter_sse_lines` touches."""

    def __init__(self, chunks: list[bytes]):
        self._chunks = chunks

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk


async def _split(chunks: list[bytes]) -> list[str]:
    return [line async for line in _aiter_sse_lines(_Response(chunks))]


# ── the bug ──────────────────────────────────────────────────────────────────

@pytest.mark.anyio
@pytest.mark.parametrize("ch,name", [(LS, "U+2028"), (PS, "U+2029"), (NEL, "U+0085")])
async def test_a_json_unescaped_separator_does_not_tear_the_frame(anyio_backend, ch, name):
    payload = '{"choices":[{"delta":{"content":"a' + ch + 'b"}}]}'
    got = await _split([("data: " + payload + "\n\n").encode("utf-8")])
    assert got == ["data: " + payload, ""], f"{name} split the frame"


@pytest.mark.anyio
async def test_the_content_survives_all_the_way_to_a_parsed_delta(anyio_backend):
    """The end that matters: the character reaches the caller, in one piece."""
    import json

    payload = '{"choices":[{"delta":{"content":"Hello' + LS + 'world"}}]}'
    lines = await _split([("data: " + payload + "\n\n").encode("utf-8")])
    body = [l for l in lines if l.startswith("data:")][0][len("data:"):].strip()
    assert json.loads(body)["choices"][0]["delta"]["content"] == (
        "Hello" + LS + "world"
    )


# ── the three terminators the spec does allow ────────────────────────────────

@pytest.mark.anyio
@pytest.mark.parametrize("sep", [b"\n", b"\r", b"\r\n"])
async def test_every_spec_terminator_still_ends_a_line(anyio_backend, sep):
    assert await _split([b"data: a" + sep + b"data: b" + sep]) == [
        "data: a", "data: b",
    ]


@pytest.mark.anyio
async def test_mixed_terminators_in_one_stream(anyio_backend):
    assert await _split([b"a\r\nb\nc\rd\n"]) == ["a", "b", "c", "d"]


# ── chunk boundaries ─────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_a_crlf_straddling_two_reads_is_one_terminator(anyio_backend):
    """Without the trailing-CR holdback the CR ends the line and the LF then
    ends a second, empty one - and a blank line is SSE's dispatch signal."""
    assert await _split([b"data: a\r", b"\ndata: b\n"]) == ["data: a", "data: b"]


@pytest.mark.anyio
async def test_a_crlf_straddling_three_reads(anyio_backend):
    assert await _split([b"data: a", b"\r", b"\n"]) == ["data: a"]


@pytest.mark.anyio
async def test_a_trailing_cr_at_end_of_stream_is_a_terminator_not_payload(anyio_backend):
    """The held-back CR has to be resolved at EOF, and resolved as the
    terminator it is - decoding the buffer directly would leak a literal "\\r"
    into the last frame."""
    assert await _split([b"data: x\r"]) == ["data: x"]


@pytest.mark.anyio
async def test_two_crs_across_two_reads_are_two_terminators(anyio_backend):
    """b"data: a\\r" then b"\\r" is "data: a" followed by a genuine blank line -
    the CRs are not a CRLF and must not be collapsed into one."""
    assert await _split([b"data: a\r", b"\r"]) == ["data: a", ""]


@pytest.mark.anyio
async def test_a_multibyte_character_split_across_reads(anyio_backend):
    """U+2028 is three bytes. Splitting mid-sequence must not corrupt it and
    must not be mistaken for a terminator."""
    assert await _split([b"data: \xe2", b"\x80\xa8x\n\n"]) == [
        "data: " + LS + "x", "",
    ]


@pytest.mark.anyio
async def test_an_empty_chunk_is_ignored(anyio_backend):
    assert await _split([b"", b"data: a\n", b"", b"data: b\n"]) == [
        "data: a", "data: b",
    ]


@pytest.mark.anyio
async def test_one_byte_at_a_time(anyio_backend):
    raw = b"data: a\r\ndata: " + LS.encode("utf-8") + b"\n\n"
    assert await _split([raw[i:i + 1] for i in range(len(raw))]) == [
        "data: a", "data: " + LS, "",
    ]


@pytest.mark.anyio
async def test_an_unterminated_final_line_is_still_delivered(anyio_backend):
    assert await _split([b"data: a\n", b"data: b"]) == ["data: a", "data: b"]


@pytest.mark.anyio
async def test_a_line_split_across_many_reads_arrives_whole(anyio_backend):
    assert await _split([b"da", b"ta:", b" hel", b"lo\n"]) == ["data: hello"]


# ── shapes the caller depends on ─────────────────────────────────────────────

@pytest.mark.anyio
async def test_keepalive_comments_are_yielded_not_swallowed(anyio_backend):
    """The caller's first-token timeout ticks on EVERY line, comments included,
    because those comments are what buy a queued request time. An event-level
    parser would yield nothing here and delete that bound."""
    assert await _split([
        b": OPENROUTER PROCESSING\n\n: OPENROUTER PROCESSING\n\n",
    ]) == [": OPENROUTER PROCESSING", "", ": OPENROUTER PROCESSING", ""]


@pytest.mark.anyio
async def test_multiple_fields_and_the_blank_dispatch_line(anyio_backend):
    assert await _split([b"event: message\nid: 7\ndata: {}\n\n"]) == [
        "event: message", "id: 7", "data: {}", "",
    ]


@pytest.mark.anyio
async def test_data_with_no_space_after_the_colon(anyio_backend):
    assert await _split([b"data:{}\n\n"]) == ["data:{}", ""]


@pytest.mark.anyio
async def test_the_done_sentinel_arrives_intact(anyio_backend):
    assert await _split([b"data: [DONE]\n\n"]) == ["data: [DONE]", ""]


@pytest.mark.anyio
async def test_invalid_utf8_is_replaced_not_raised(anyio_backend):
    """A stream must never die on a bad byte; the frame will fail json.loads
    and be reported, which is the honest outcome."""
    got = await _split([b"data: \xff\xfe\n"])
    assert got[0].startswith("data: ")
    assert "�" in got[0]


@pytest.mark.anyio
async def test_an_empty_stream_yields_nothing(anyio_backend):
    assert await _split([]) == []


# ── why the helper exists at all ─────────────────────────────────────────────

def test_httpx_still_splits_where_the_sse_spec_says_it_must_not():
    """Pins the third-party behaviour this module works around.

    If httpx ever narrows LineDecoder back to CR/LF/CRLF (it was correct up to
    0.23.3 and changed deliberately for speed in 0.24.0), this test fails and
    `_aiter_sse_lines` can be deleted. Until then it is load-bearing, and this
    is the evidence.
    """
    from httpx._decoders import LineDecoder

    dec = LineDecoder()
    got = dec.decode("data: a" + LS + "b\n") + dec.flush()
    assert len(got) > 1, "httpx no longer splits on U+2028 - drop the workaround"


# ── end to end, through the real generator ───────────────────────────────────

class _Client:
    """The narrowest shape complete_stream needs. Deliberately offers ONLY
    aiter_bytes: a call site that went back to aiter_lines would fail here."""

    def __init__(self, raw: bytes):
        self._raw = raw

    def stream(self, *a, **kw):
        raw = self._raw

        class _Ctx:
            async def __aenter__(self):
                class _Resp:
                    status_code = 200

                    async def aiter_bytes(self):
                        yield raw

                return _Resp()

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


@pytest.mark.anyio
async def test_a_reply_containing_the_separator_arrives_whole(anyio_backend, monkeypatch):
    """The bug as the reader experienced it: a sentence went missing."""
    import openrouter

    raw = (
        'data: {"choices":[{"delta":{"content":"I missed you."}}]}\n\n'
        'data: {"choices":[{"delta":{"content":" Come' + LS + 'here."}}]}\n\n'
        "data: [DONE]\n\n"
    ).encode("utf-8")

    monkeypatch.setattr(openrouter, "get_client", lambda: _Client(raw))
    monkeypatch.setattr(openrouter, "get_secret", lambda name: "sk-test")

    out = [d async for d in openrouter.complete_stream(
        [{"role": "user", "content": "hi"}], "test/model", {}, None,
    )]
    assert "".join(out) == "I missed you. Come" + LS + "here."


@pytest.mark.anyio
async def test_a_frame_that_is_genuinely_not_json_is_reported_not_fatal(
    anyio_backend, monkeypatch, caplog,
):
    """Once the splitting is right, an unparseable frame means the provider sent
    junk. The rest of the reply is still delivered - failing the turn would cost
    the reader text they had already read - but it is no longer silent."""
    import logging

    import openrouter

    raw = (
        'data: {"choices":[{"delta":{"content":"kept "}}]}\n\n'
        "data: {not json at all\n\n"
        'data: {"choices":[{"delta":{"content":"also kept"}}]}\n\n'
        "data: [DONE]\n\n"
    ).encode("utf-8")

    monkeypatch.setattr(openrouter, "get_client", lambda: _Client(raw))
    monkeypatch.setattr(openrouter, "get_secret", lambda name: "sk-test")

    with caplog.at_level(logging.ERROR):
        out = [d async for d in openrouter.complete_stream(
            [{"role": "user", "content": "hi"}], "test/model", {}, None,
        )]

    assert "".join(out) == "kept also kept"
    assert any("Malformed stream frame" in r.message for r in caplog.records)
    assert any("1 unparseable frame" in r.getMessage() for r in caplog.records)


@pytest.mark.anyio
async def test_a_data_line_with_no_value_is_not_reported_as_malformed(
    anyio_backend, monkeypatch, caplog,
):
    """`data:` with an empty value is legal SSE. json.loads("") raises, so
    without the guard every one of them would now be logged as a violation."""
    import logging

    import openrouter

    raw = (
        "data:\n\n"
        'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
        "data: [DONE]\n\n"
    ).encode("utf-8")

    monkeypatch.setattr(openrouter, "get_client", lambda: _Client(raw))
    monkeypatch.setattr(openrouter, "get_secret", lambda name: "sk-test")

    with caplog.at_level(logging.ERROR):
        out = [d async for d in openrouter.complete_stream(
            [{"role": "user", "content": "hi"}], "test/model", {}, None,
        )]

    assert "".join(out) == "hi"
    assert not [r for r in caplog.records if "Malformed" in r.message]
