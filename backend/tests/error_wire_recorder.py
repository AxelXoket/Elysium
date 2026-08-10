"""Records every error code that actually crosses the wire during a test run.

Test-only. `pytest` is in the exe's `excludes` list and nothing under
`backend/tests/` is bundled, so this file is unshippable by construction.

WHAT THIS IS FOR, AND WHY IT IS NOT A DUPLICATE OF THE STATIC CENSUS

`error_enumeration.py` reads the source and answers "what codes CAN this
backend produce". Its answer rests on `DECLARED_ALPHABETS`: at eleven sites the
code is computed, and a human writes down which alphabet that site draws from.
The census then trusts that sentence completely.

So the census cannot check a declaration. It cannot even in principle, because
the declaration IS its input.

That gap is not theoretical. On 2026-08-10 the two voice sites were registered
as drawing from `tts.errors.ALL_CODES`, and they did not: `_code_for` accepted
any string beginning `tts_`, so an exception carrying `tts_banana` would have
sent `tts_banana` to a client that has no sentence for it. The declaration was
false, the census was green, and it was caught by a person reading the funnel.

This recorder is what catches the next one. Anything that reaches the wire and
is not in the catalogue fails, whatever the source said it would be.

WHY IT SITS OUTSIDE THE APP

`routers/completions.py` wraps its whole streaming generator in a bare
`except Exception` that answers `internal_error`. An assertion raised from
inside the app is swallowed there and rewritten into a catalogued code, so an
in-app hook would report its own alarm as a green record. This wraps the raw
ASGI callable, so it sees the bytes after every handler has had its say.

WHY IT DOES NOT BUFFER A STREAM

Several tests exist to prove delivery is incremental: test_first_chunk.py,
test_stream_body.py, test_sse_line_split.py, and the voice latency contract.
A recorder that collected a stream to scan it would silently convert those into
tests of buffered behaviour, which is a worse defect than the one it was built
to find. So an event-stream chunk is inspected and forwarded in the same call,
and the only thing held back is the tail of a split event, which is prefixed
onto the next chunk. Nothing is delayed. Plain JSON responses are collected
until `more_body` is false, which is correct and touches no streaming test.

WHAT IT DELIBERATELY DOES NOT CHECK

The catalogue's `statuses` is a closed-world fact: every status a LITERAL raise
site pairs with a code, read from source. A run only samples whichever paths
happened to execute, so a status seen here proves nothing about the ones that
were not. And an SSE payload's own `status` field is not that fact at all - the
AST walker records `None` for every SSE emission. Comparing the two would be
checking a claim the catalogue never made.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Observed:
    code: str
    status: int | None
    channel: str            #: http | sse | voice_sse | notice


@dataclass
class WireRecorder:
    seen: set[Observed] = field(default_factory=set)

    def record(self, code: str, status: int | None, channel: str) -> None:
        self.seen.add(Observed(code, status, channel))

    def codes(self) -> set[str]:
        return {o.code for o in self.seen}

    def pairs(self) -> set[tuple[str, str]]:
        return {(o.code, o.channel) for o in self.seen}


#: One instance for the whole pytest process, not a fixture.
#:
#: The suite builds a TestClient in five places, not one: conftest's `client`
#: fixture plus test_phase0_hardening, test_security_headers (twice) and
#: test_release_hardening. A fixture-scoped recorder would have covered the
#: first and silently missed the rest.
RECORDER = WireRecorder()

#: Mirrors error_enumeration._SSE_TYPES exactly, so the two vocabularies stay
#: comparable. `voice_notice` is listed for symmetry and will never match:
#: its payload carries `note`, not `code`. The static census does not see it
#: either, for the same reason, so this is a shared blind spot and not a new
#: one - written down here rather than quietly worked around.
_SSE_CHANNEL = {
    "error": "sse",
    "voice_error": "voice_sse",
    "notice": "notice",
    "voice_notice": "voice_sse",
}


#: Answers this recorder ignores entirely, with the reason each is here.
#:
#: `Not Found` is Starlette's own body for a path no route matches. It does
#: reach a client, but it is the framework's word and not this app's
#: vocabulary: it has no sentence, no record, and no code shape at all. Naming
#: it here rather than filtering by a pattern, so that adding a second
#: exclusion is a visible line in a diff.
_NOT_OUR_VOCABULARY = frozenset({"Not Found", "Method Not Allowed"})


def consume_json(body: bytes, status: int | None, recorder: WireRecorder) -> None:
    """Record a FAILED response's `detail`, when it is a code.

    Two filters, both learned the hard way on the first full run.

    STATUS. Only 4xx and 5xx. `detail` is not a reserved word: `readiness.py`
    answers 200 with a `detail` field carrying a human sentence about VRAM
    ("fp8 weights + KV cache + generation working set"), and a first version of
    this function dutifully recorded that as an error code. A code lives in a
    failure; a 200 body that happens to use the same key is a different thing
    wearing the same name.

    SHAPE. FastAPI's own 422 answers with `detail` as a LIST of validation
    dicts, so the `isinstance(str)` is what keeps a shape out of the observed
    set that no catalogue could ever contain.
    """
    if not body or status is None or status < 400:
        return
    try:
        payload = json.loads(body)
    except ValueError:
        return
    if not isinstance(payload, dict):
        return
    detail = payload.get("detail")
    if isinstance(detail, str) and detail not in _NOT_OUR_VOCABULARY:
        recorder.record(detail, status, "http")


def consume_sse(text: str, recorder: WireRecorder) -> str:
    """Record complete events out of `text`, return the unconsumed tail.

    `split` on the blank-line separator, then drop the last element and hand it
    back to the caller. That last element is either empty, when the chunk ended
    on a boundary, or a partial event, and prefixing it onto the next chunk is
    what makes a split event survive. Nothing is parsed twice and nothing is
    lost, which is the whole contract of a buffered line protocol.
    """
    parts = text.split("\n\n")
    remainder = parts.pop()
    for raw_event in parts:
        for line in raw_event.splitlines():
            if not line.startswith("data:"):
                continue
            try:
                payload = json.loads(line[len("data:"):].strip())
            except ValueError:
                continue
            if not isinstance(payload, dict):
                continue
            channel = _SSE_CHANNEL.get(payload.get("type"))
            code = payload.get("code")
            if channel is not None and isinstance(code, str):
                recorder.record(code, payload.get("status"), channel)
    return remainder


class ErrorWireASGIMiddleware:
    """A pass-through ASGI wrapper that reads what goes out."""

    def __init__(self, app, recorder: WireRecorder | None = None) -> None:
        self.app = app
        self.recorder = RECORDER if recorder is None else recorder

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)

        state: dict = {"status": None, "sse": False}
        collected = bytearray()
        carry = ""

        async def wrapped_send(message):
            nonlocal carry
            kind = message.get("type")
            if kind == "http.response.start":
                state["status"] = message.get("status")
                for name, value in message.get("headers") or []:
                    if name.lower() == b"content-type":
                        state["sse"] = b"text/event-stream" in value.lower()
            elif kind == "http.response.body":
                body = message.get("body", b"") or b""
                if state["sse"]:
                    # Inspect and forward in the same breath. See the module
                    # docstring: buffering here would break the tests that
                    # prove delivery is incremental.
                    carry = consume_sse(
                        carry + body.decode("utf-8", "replace"), self.recorder)
                else:
                    collected.extend(body)
                    if not message.get("more_body", False):
                        consume_json(bytes(collected), state["status"],
                                     self.recorder)
            await send(message)

        await self.app(scope, receive, wrapped_send)


def install() -> None:
    """Make every TestClient in the process record, including the four that
    build their own.

    Patches the class rather than each call site, because there are five call
    sites and a sixth added tomorrow would silently not be covered. Called from
    conftest at import time: pytest imports a directory's conftest before its
    sibling test modules, so the patch lands before any module-level
    `from fastapi.testclient import TestClient` binds the original name.

    Idempotent. A second call would otherwise wrap the wrapper, and the
    recorder would see every response twice - harmless for a set, but the
    subset assertion would then be measuring something nobody designed.
    """
    import fastapi.testclient as testclient

    if getattr(testclient.TestClient, "_elysium_recording", False):
        return

    real = testclient.TestClient

    class RecordingTestClient(real):                    # type: ignore[misc]
        _elysium_recording = True

        def __init__(self, app, *args, **kwargs):
            super().__init__(ErrorWireASGIMiddleware(app), *args, **kwargs)

    testclient.TestClient = RecordingTestClient
