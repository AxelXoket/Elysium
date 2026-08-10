"""Behaviour tests for the wire recorder itself.

Every one of these drives the code with real ASGI messages or real event-stream
text and asserts on what came out. Nothing reads a source file.

They exist because of the shape of the assertion the recorder feeds: "the codes
observed on the wire are a subset of the catalogue". A subset assertion over an
EMPTY set is trivially true, so a recorder that quietly stopped working would
turn its own gate green. This repository has been bitten by that exact shape
twice, in test_verify_gate.py and test_release_tree.py, and the answer both
times was a floor plus named probes rather than trust.
"""

from __future__ import annotations

import asyncio
import json

from tests.error_wire_recorder import (
    ErrorWireASGIMiddleware,
    WireRecorder,
    consume_json,
    consume_sse,
)


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj)}\n\n"


class TestPlainResponses:
    def test_a_detail_string_is_recorded_with_its_status(self):
        rec = WireRecorder()
        consume_json(b'{"detail": "chat_not_found"}', 404, rec)
        assert rec.seen == {
            type(next(iter(rec.seen)))("chat_not_found", 404, "http")
        }

    def test_a_success_body_records_nothing(self):
        rec = WireRecorder()
        consume_json(b'{"id": 7, "title": "hello"}', 200, rec)
        assert rec.seen == set()

    def test_a_validation_error_list_is_not_mistaken_for_a_code(self):
        # FastAPI's own 422 answers with detail as a LIST of dicts. Recording
        # one would put a shape in the observed set that no catalogue could
        # ever contain, and the subset assertion would fail forever on a tree
        # with nothing wrong with it.
        body = json.dumps({"detail": [{"loc": ["body", "x"], "msg": "req"}]})
        rec = WireRecorder()
        consume_json(body.encode(), 422, rec)
        assert rec.seen == set()

    def test_a_detail_on_a_SUCCESSFUL_response_is_not_an_error_code(self):
        # The real one the first full run found. tts/readiness.py answers 200
        # with a `detail` field holding a human sentence about VRAM, and the
        # first version of this recorder filed "fp8 weights + KV cache +
        # generation working set" as an error code. `detail` is not a reserved
        # word; a code lives in a failure.
        rec = WireRecorder()
        consume_json(b'{"ok": true, "detail": "fp8 weights + KV cache"}', 200, rec)
        assert rec.seen == set()

    def test_the_frameworks_own_404_body_is_not_our_vocabulary(self):
        # Starlette answers an unrouted path with {"detail": "Not Found"}.
        # It reaches a client, and it is not a code this app chose, has no
        # sentence, and is not code-shaped. Excluded by name so that adding a
        # second exclusion is a visible line rather than a widened pattern.
        rec = WireRecorder()
        consume_json(b'{"detail": "Not Found"}', 404, rec)
        assert rec.seen == set()

    def test_a_body_that_is_not_json_is_survived(self):
        rec = WireRecorder()
        consume_json(b"<html>a proxy ate this</html>", 502, rec)
        assert rec.seen == set()


class TestEventStreams:
    def test_an_error_event_is_recorded_on_the_sse_channel(self):
        rec = WireRecorder()
        rest = consume_sse(_sse({"type": "error", "status": 409,
                                 "code": "edit_conflict"}), rec)
        assert rest == ""
        assert rec.pairs() == {("edit_conflict", "sse")}

    def test_a_voice_error_lands_on_its_own_channel(self):
        rec = WireRecorder()
        consume_sse(_sse({"type": "voice_error", "code": "tts_out_of_memory"}), rec)
        assert rec.pairs() == {("tts_out_of_memory", "voice_sse")}

    def test_a_notice_lands_on_the_notice_channel(self):
        rec = WireRecorder()
        consume_sse(_sse({"type": "notice", "code": "images_omitted"}), rec)
        assert rec.pairs() == {("images_omitted", "notice")}

    def test_deltas_and_done_are_not_error_codes(self):
        rec = WireRecorder()
        consume_sse(_sse({"type": "delta", "content": "hello"})
                    + _sse({"type": "done", "message_id": 3}), rec)
        assert rec.seen == set()

    def test_an_event_split_across_two_chunks_survives(self):
        # The case the whole carry-string design exists for. A transport is
        # free to break a chunk anywhere, including mid-JSON.
        whole = _sse({"type": "error", "status": 423, "code": "vault_locked"})
        cut = len(whole) // 2
        rec = WireRecorder()
        carry = consume_sse(whole[:cut], rec)
        assert rec.seen == set(), "half an event is not an event"
        carry = consume_sse(carry + whole[cut:], rec)
        assert rec.pairs() == {("vault_locked", "sse")}
        assert carry == ""

    def test_several_events_in_one_chunk_are_all_recorded(self):
        rec = WireRecorder()
        consume_sse(
            _sse({"type": "delta", "content": "hi"})
            + _sse({"type": "notice", "code": "images_omitted"})
            + _sse({"type": "error", "status": 500, "code": "internal_error"}),
            rec)
        assert rec.pairs() == {("images_omitted", "notice"),
                               ("internal_error", "sse")}

    def test_a_voice_notice_is_not_recorded_and_that_is_deliberate(self):
        # It carries `note`, not `code`. The static census cannot see it
        # either, so this is a shared blind spot rather than a new one. Pinned
        # so that a future change to either side has to change this line too.
        rec = WireRecorder()
        consume_sse(_sse({"type": "voice_notice", "note": "fell back to cpu"}), rec)
        assert rec.seen == set()


class TestTheMiddlewarePassesEverythingThrough:
    """The recorder must not change what a client receives, at all."""

    def _run(self, messages: list[dict]) -> tuple[list[dict], WireRecorder]:
        rec = WireRecorder()
        forwarded: list[dict] = []

        async def app(scope, receive, send):
            for message in messages:
                await send(message)

        async def send(message):
            forwarded.append(message)

        wrapped = ErrorWireASGIMiddleware(app, rec)
        asyncio.run(wrapped({"type": "http"}, None, send))
        return forwarded, rec

    def test_every_message_is_forwarded_unchanged(self):
        messages = [
            {"type": "http.response.start", "status": 404,
             "headers": [(b"content-type", b"application/json")]},
            {"type": "http.response.body", "body": b'{"detail":"chat_not_found"}'},
        ]
        forwarded, rec = self._run(messages)
        assert forwarded == messages
        assert rec.codes() == {"chat_not_found"}

    def test_a_stream_is_forwarded_chunk_by_chunk_not_collected(self):
        # THE property. Tests elsewhere prove delivery is incremental, and a
        # recorder that gathered a stream to scan it would silently turn those
        # into tests of buffered behaviour. Asserting on the message list
        # proves each chunk went out as its own send.
        whole = _sse({"type": "error", "status": 429,
                      "code": "openrouter_rate_limited"})
        messages = [
            {"type": "http.response.start", "status": 200,
             "headers": [(b"content-type", b"text/event-stream")]},
            {"type": "http.response.body", "body": whole[:10].encode(),
             "more_body": True},
            {"type": "http.response.body", "body": whole[10:].encode(),
             "more_body": True},
            {"type": "http.response.body", "body": b"", "more_body": False},
        ]
        forwarded, rec = self._run(messages)
        assert forwarded == messages, "the stream was reshaped on the way out"
        assert len(forwarded) == 4, "chunks were merged"
        assert rec.pairs() == {("openrouter_rate_limited", "sse")}

    def test_a_non_http_scope_is_left_alone(self):
        rec = WireRecorder()
        touched = []

        async def app(scope, receive, send):
            touched.append(scope["type"])

        asyncio.run(ErrorWireASGIMiddleware(app, rec)(
            {"type": "lifespan"}, None, None))
        assert touched == ["lifespan"]
        assert rec.seen == set()


class TestInstallIsSafeToRepeat:
    def test_installing_twice_does_not_wrap_twice(self):
        # A doubled wrapper would record every response twice. Harmless for a
        # set, but the floor assertion downstream would then be counting
        # something nobody designed, which is worse than a wrong number.
        import fastapi.testclient as testclient

        from tests.error_wire_recorder import install

        first = testclient.TestClient
        install()
        assert testclient.TestClient is first

    def test_there_is_exactly_one_recorder_in_this_process(self):
        """The bug that made the first full run observe zero codes.

        `tests/` has no `__init__.py`, so it resolves as a namespace package
        and BOTH `import error_wire_recorder` and
        `import tests.error_wire_recorder` succeed. They load the same file
        into two separate module objects with two separate RECORDER
        singletons. conftest wrote into one, this file read the other, and the
        subset assertion was measuring an empty set that nothing ever filled.

        The floor caught it, which is exactly what the floor is for. This pins
        it so it cannot come back the next time somebody adds an import.
        """
        # Read sys.modules; do NOT import. A first version of this test called
        # import_module on both names and thereby CREATED the second module it
        # was written to detect, failing on a tree where nothing was wrong.
        import sys

        loaded = [name for name in ("error_wire_recorder",
                                    "tests.error_wire_recorder")
                  if name in sys.modules]
        assert len(loaded) == 1, (
            f"the recorder module is loaded under {loaded}: two module objects "
            f"means two RECORDER singletons, and whichever one conftest fills "
            f"is not the one the gate reads. Pick one import path."
        )

    def test_the_patch_is_in_place_for_this_session(self):
        # If conftest's install() ever stops running, the session gate turns
        # vacuous. This says so immediately instead.
        import fastapi.testclient as testclient

        assert getattr(testclient.TestClient, "_elysium_recording", False), (
            "TestClient is not the recording subclass: conftest's install() "
            "did not run, and the wire gate is now asserting over an empty set"
        )
