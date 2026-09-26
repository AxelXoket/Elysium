"""What the file on disk and the log on disk actually contain.

Companion to test_privacy_promises.py, split off because the subject is
different: that file asserts on bytes LEAVING, this one asserts on bytes
STAYING.

The gap it closes is the uncomfortable one. The suite proved a great deal
about the vault - unlock, rotation, recovery, migration, key revocation - and
never once opened the resulting file to look at it. Every one of those tests
passes just as well against a database written in the clear with a passphrase
prompt in front of it. "Genuine SQLCipher ciphertext" is the loudest sentence
in the README and it was the one nothing checked.
"""
from __future__ import annotations

import io
import json
import logging
import sqlite3 as std_sqlite3
from pathlib import Path

import pytest

import openrouter
from tests.conftest import get_messages, make_chat, make_character


def _plaintext_db_holding(path: Path, text: str,
                          encoding: str | None = None) -> bytes:
    """A database nothing encrypts, carrying `text`, returned as raw bytes.

    The positive control for every "not in raw" assertion below. A byte sweep
    that finds nothing proves nothing until it has been shown finding
    something, and the cheapest way to show that is to hand it a file where
    the phrase is definitely present and definitely in the clear.

    `encoding` is passed straight to PRAGMA encoding, which SQLite only
    honours before the first table exists - that is what lets one call
    produce a UTF-8 file and another a UTF-16LE one, so both arms of the
    sweep get their own control rather than sharing a UTF-8 one.
    """
    con = std_sqlite3.connect(str(path))
    try:
        if encoding is not None:
            con.execute(f"PRAGMA encoding = '{encoding}'")
        con.execute("CREATE TABLE probe (body TEXT NOT NULL)")
        con.execute("INSERT INTO probe (body) VALUES (?)", (text,))
        con.commit()
    finally:
        con.close()
    return path.read_bytes()


class TestTheDatabaseFileIsCiphertext:
    SENTINEL = "ciphertextsentinelphrasexyzzy"

    def test_the_sweep_finds_the_sentinel_when_nothing_encrypts_it(
        self, tmp_path
    ) -> None:
        """The floor under all three "not in raw" tests in this class.

        Those three read as strong claims and are worth exactly as much as
        the sweep behind them. If SENTINEL were mistyped, if read_bytes came
        back empty, if the two encodings were the same encoding, every one of
        them would stay green against a database written in the clear - the
        precise failure this file exists to catch.
        """
        utf8 = _plaintext_db_holding(tmp_path / "clear-utf8.db", self.SENTINEL)
        assert self.SENTINEL.encode() in utf8

        utf16 = _plaintext_db_holding(tmp_path / "clear-utf16.db",
                                      self.SENTINEL, encoding="UTF-16le")
        assert self.SENTINEL.encode("utf-16-le") in utf16

    def test_the_header_is_not_a_sqlite_header(self, client) -> None:
        import config
        assert not Path(config.DB_PATH).read_bytes()[:16].startswith(
            b"SQLite format 3")

    def test_stdlib_sqlite_cannot_read_it(self, client) -> None:
        import config
        con = std_sqlite3.connect(config.DB_PATH)
        try:
            with pytest.raises(std_sqlite3.DatabaseError):
                con.execute("SELECT count(*) FROM sqlite_master").fetchone()
        finally:
            con.close()

    def test_a_message_is_not_findable_in_the_raw_bytes(
        self, client, provider
    ) -> None:
        # The assertion that would notice a cipher that was configured but not
        # applied - the failure that leaves every other vault test green.
        import config
        chat_id = make_chat(client, make_character(client))
        response = client.post(f"/api/v1/chats/{chat_id}/complete",
                               json={"message": self.SENTINEL,
                                     "model_id": "test/model-1"})
        assert response.status_code == 200, response.text

        # The phrase has to have REACHED the file before its absence from the
        # file says anything. A route that answered 200 and persisted nothing
        # leaves every assertion below true and the cipher untested, so the
        # message is read back through the application's own read path first.
        stored = get_messages(client, chat_id)
        assert any(self.SENTINEL in m["content"] for m in stored), (
            "the message was never persisted, so its absence from the raw "
            "bytes says nothing about the cipher")

        raw = Path(config.DB_PATH).read_bytes()
        assert self.SENTINEL.encode() not in raw
        assert self.SENTINEL.encode("utf-16-le") not in raw

    def test_a_character_card_is_not_findable_in_the_raw_bytes(
        self, client
    ) -> None:
        # A card carries the system prompt and the opening line, which is what
        # the WebView2 cache leak turned out to be full of.
        import config
        character_id = make_character(client, name="Cardsentinelzzz",
                                      first_mes="opening-line-sentinel-zzz")
        card = client.get(f"/api/v1/characters/{character_id}")
        assert card.status_code == 200, card.text
        assert card.json()["name"] == "Cardsentinelzzz"
        assert card.json()["first_mes"] == "opening-line-sentinel-zzz", (
            "the card was never persisted, so its absence from the raw bytes "
            "says nothing about the cipher")

        raw = Path(config.DB_PATH).read_bytes()
        assert b"Cardsentinelzzz" not in raw
        assert b"opening-line-sentinel-zzz" not in raw

    def test_an_image_blob_is_not_findable_in_the_raw_bytes(
        self, client
    ) -> None:
        # Images are the one payload stored as a blob rather than as text, so
        # this needs its own look: a blob written past the cipher would be
        # indistinguishable from ordinary binary noise by eye.
        import config
        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (8, 8), (7, 11, 13)).save(buffer, format="PNG")
        payload = buffer.getvalue()

        response = client.post("/api/v1/uploads/images",
                               files={"file": ("probe.png", payload,
                                               "image/png")})
        # Not a skip. A privacy gate that cannot reach the route it measures
        # knows nothing about that route, and "knows nothing" is the state
        # that has to be loud: a skip reports a green suite while the one
        # claim this file exists to make goes unmeasured.
        assert response.status_code in (200, 201), response.text

        # Positive control, and it needs the stored bytes rather than the
        # posted ones: save_upload re-encodes through Pillow, so `payload` on
        # its own could be absent from the file simply because those exact
        # bytes were never written. What must not be findable is what the
        # application will hand back.
        attachment_id = response.json()["id"]
        served = client.get(f"/api/v1/uploads/images/{attachment_id}")
        assert served.status_code == 200, served.text
        stored = served.content
        assert stored.startswith(b"\x89PNG\r\n\x1a\n"), (
            "no image came back, so its absence from the raw bytes says "
            "nothing about the cipher")

        raw = Path(config.DB_PATH).read_bytes()
        assert stored not in raw
        assert payload not in raw
        assert b"\x89PNG\r\n\x1a\n" not in raw


class TestUpstreamErrorBodiesAreNotForwarded:
    """An upstream error body is written by somebody else.

    It can contain anything: an echo of the prompt, an account identifier, a
    moderation excerpt naming which part of the conversation tripped it. It
    reaches a client that renders it.
    """

    LEAK = "upstream-body-that-must-not-be-relayed"

    def _failing_provider(self, monkeypatch, status: int,
                          moderation: bool = False) -> None:
        leak = self.LEAK

        def _body() -> dict:
            if not moderation:
                return {"error": {"message": leak}}
            # The shape a real moderation refusal arrives in. flagged_input is
            # a verbatim copy of what the reader just typed, which is why
            # openrouter._is_moderation_error is careful that neither the
            # reasons nor the input leave the predicate.
            return {"error": {"message": leak, "code": 403, "metadata": {
                "reasons": ["harassment"], "flagged_input": leak,
                "provider_name": "someprovider",
            }}}

        class _Response:
            status_code = status
            is_success = False

            def json(self) -> dict:
                return _body()

            @property
            def text(self) -> str:
                return json.dumps(self.json())

            @property
            def content(self) -> bytes:
                # What the production error path actually reads. The first cut
                # of this fake carried only .json()/.text, so the body never
                # reached _parse_error_payload: the 403 branch was never
                # entered and the moderation test below could not have failed.
                return self.text.encode()

        class _Client:
            async def post(self, url, headers=None, json=None, timeout=None):
                return _Response()

            def stream(self, method, url, headers=None, json=None,
                       timeout=None):
                class _Ctx:
                    async def __aenter__(self):
                        class _Stream:
                            status_code = status
                            is_success = False

                            async def aread(self):
                                return json.dumps(_body()).encode()

                            async def aiter_bytes(self):
                                yield leak.encode()

                        return _Stream()

                    async def __aexit__(self, *exc):
                        return False

                return _Ctx()

        monkeypatch.setattr(openrouter, "get_client", lambda: _Client())
        monkeypatch.setattr(openrouter, "get_secret", lambda name: "sk-test")

    @pytest.mark.parametrize("status", [400, 402, 403, 429, 500, 503])
    def test_the_plain_path_relays_a_code_and_nothing_else(
        self, client, monkeypatch: pytest.MonkeyPatch, status: int
    ) -> None:
        self._failing_provider(monkeypatch, status)
        chat_id = make_chat(client, make_character(client))
        response = client.post(f"/api/v1/chats/{chat_id}/complete",
                               json={"message": "hi",
                                     "model_id": "test/model-1"})
        assert response.status_code != 200
        assert self.LEAK not in response.text

    @pytest.mark.parametrize("status", [400, 403, 429, 500])
    def test_the_streaming_path_relays_a_code_and_nothing_else(
        self, client, monkeypatch: pytest.MonkeyPatch, status: int
    ) -> None:
        # The path where a body is easiest to pass through by accident: the
        # generator is already forwarding bytes it does not parse.
        self._failing_provider(monkeypatch, status)
        chat_id = make_chat(client, make_character(client))
        response = client.post(f"/api/v1/chats/{chat_id}/complete/stream",
                               json={"message": "hi",
                                     "model_id": "test/model-1"})
        assert self.LEAK not in response.text

    #: A model id no other request in these tests uses, so the log line that
    #: names it can only have come from this request.
    PROBE_MODEL = "vendor/leak-probe-9"

    #: A 200 is not a success on this API. OpenRouter commits the status as
    #: soon as a provider accepts the request, so a provider that fails after
    #: that answers 200 with the error in the body. Each shape carries the
    #: leak marker where OpenRouter puts provider text: the envelope's message
    #: and its raw metadata.
    PLAIN_200_ERRORS = {
        "error_and_no_choices": lambda leak: {
            "error": {"code": 502, "message": leak,
                      "metadata": {"provider_name": leak, "raw": leak}}},
        "error_inside_the_choice": lambda leak: {
            "choices": [{"message": {"role": "assistant",
                                     "content": "partial answer"},
                         "finish_reason": "error",
                         "error": {"code": 502, "message": leak,
                                   "metadata": {"raw": leak}}}]},
    }

    def _ok_status_error_body(self, monkeypatch, plain: dict,
                              frames: list[bytes]) -> list[str]:
        """Answer 200 with `plain` or the SSE `frames`, and return the list
        each call is recorded in, so a test can prove it was reached."""
        calls: list[str] = []

        class _Response:
            status_code = 200
            is_success = True

            def json(self) -> dict:
                return plain

            @property
            def content(self) -> bytes:
                return json.dumps(plain).encode()

        class _Client:
            async def post(self, url, headers=None, json=None, timeout=None):
                calls.append("post")
                return _Response()

            def stream(self, method, url, headers=None, json=None,
                       timeout=None):
                calls.append("stream")

                class _Ctx:
                    async def __aenter__(self):
                        class _Stream:
                            status_code = 200

                            async def aiter_bytes(self):
                                for frame in frames:
                                    yield frame

                        return _Stream()

                    async def __aexit__(self, *exc):
                        return False

                return _Ctx()

        monkeypatch.setattr(openrouter, "get_client", lambda: _Client())
        monkeypatch.setattr(openrouter, "get_secret", lambda name: "sk-test")
        return calls

    def _provider_logged(self, caplog: pytest.LogCaptureFixture) -> bool:
        """True when openrouter.py's own logger wrote its request line, which
        proves caplog sees that module's records at all. Matched by logger
        name, since the router writes a request line of its own. No line
        names the model any more (a model id is a name a person reads), so
        the probe model is required to be ABSENT from every record."""
        records = [r for r in caplog.records if r.name == openrouter.logger.name]
        assert not any(self.PROBE_MODEL in r.getMessage() for r in caplog.records)
        return any(r.getMessage().startswith(("Completion request",
                                              "Streaming completion request"))
                   for r in records)

    @pytest.mark.parametrize("shape", sorted(PLAIN_200_ERRORS))
    def test_a_200_carrying_an_error_relays_nothing_of_it(
        self, client, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture, shape: str,
    ) -> None:
        calls = self._ok_status_error_body(
            monkeypatch, self.PLAIN_200_ERRORS[shape](self.LEAK), [])
        chat_id = make_chat(client, make_character(client))
        before = get_messages(client, chat_id)
        with caplog.at_level(logging.DEBUG):
            response = client.post(f"/api/v1/chats/{chat_id}/complete",
                                   json={"message": "hi",
                                         "model_id": self.PROBE_MODEL})
        stored = json.dumps(get_messages(client, chat_id))
        # GROUND: the request left and this body is the one that came back.
        # A key gate, a validation error or a route that never called out
        # would all leave `calls` empty.
        assert calls == ["post"], (calls, response.text)
        # POSITIVE CONTROL for the log check: the provider module's own line
        # for this request was captured, so the marker's absence is measured
        # on the logger that holds the body, not only on the router's.
        assert self._provider_logged(caplog), caplog.text
        if shape == "error_and_no_choices":
            assert response.status_code == 502
            assert response.json() == {
                "detail": "invalid_openrouter_completion_response"}
            # Nothing from this exchange is kept, so there is no row for the
            # marker to land in; the stored check below would be decoration.
            assert get_messages(client, chat_id) == before
        else:
            # The choice still carried content, and today that is kept as a
            # reply. Pinned rather than left open, so a route that starts
            # refusing it changes this line on purpose. The stored row is
            # where the marker would land, and it has to hold the words the
            # route took, or it is not this reply.
            assert response.status_code == 200, response.text
            assert "partial answer" in response.text
            assert "partial answer" in stored
        assert self.LEAK not in response.text
        assert self.LEAK not in caplog.text
        assert self.LEAK not in stored

    @pytest.mark.parametrize("after_text", [False, True],
                             ids=["before_any_text", "after_some_text"])
    def test_a_streamed_error_frame_relays_nothing_of_it(
        self, client, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture, after_text: bool,
    ) -> None:
        frame = {"error": {"code": 502, "message": self.LEAK,
                           "metadata": {"provider_name": self.LEAK,
                                        "raw": self.LEAK}},
                 "choices": [{"index": 0, "delta": {"content": ""},
                              "finish_reason": "error"}]}
        frames = []
        if after_text:
            frames.append(b'data: {"choices":[{"delta":{"content":"Once "}}]}'
                          b'\n\n')
        frames.append(b"data: " + json.dumps(frame).encode() + b"\n\n")
        calls = self._ok_status_error_body(monkeypatch, {}, frames)
        chat_id = make_chat(client, make_character(client))
        before = get_messages(client, chat_id)
        with caplog.at_level(logging.DEBUG):
            response = client.post(f"/api/v1/chats/{chat_id}/complete/stream",
                                   json={"message": "hi",
                                         "model_id": self.PROBE_MODEL})
        stored = json.dumps(get_messages(client, chat_id))
        # GROUND: the stream was opened and ended on its error event, so the
        # frame carrying the marker was read.
        assert calls == ["stream"], (calls, response.text)
        assert '"type": "error"' in response.text, response.text
        # POSITIVE CONTROL for the log check, as in the plain test.
        assert self._provider_logged(caplog), caplog.text
        if after_text:
            # The text before the error is kept as a partial reply, so the
            # stored row exists and is where the marker would land.
            assert "Once " in response.text
            assert "Once " in stored
        else:
            # Nothing reached the reader before the error, so nothing from
            # this exchange is kept and the stored check has no row to read.
            assert get_messages(client, chat_id) == before
        assert self.LEAK not in response.text
        assert self.LEAK not in caplog.text
        assert self.LEAK not in stored

    @pytest.mark.parametrize("route", ["complete", "complete/stream"])
    def test_a_moderation_refusal_does_not_relay_what_was_flagged(
        self, client, monkeypatch: pytest.MonkeyPatch, route: str,
    ) -> None:
        """403 is the one branch that reads the body, so it is the one to pin.

        Every other status in this class maps to a constant without looking at
        the payload; 403 has to look, because it decides whether this was a
        moderation refusal. The metadata it reads carries `flagged_input`, a
        verbatim copy of what the reader just typed, and both lists above ran
        without 403 in them.

        What the red-green measurement then showed is worth writing down,
        because it is not what the gap looked like from `_status_to_reason`
        alone. Making that function return the flagged text does NOT reach the
        reader: the detail a client sees is looked up in `_ERROR_MAP`, whose
        fallback is a constant, and that lookup happens at TWO independent
        sites (the raise in the plain path and the helper the SSE path uses).
        This test only goes red when the closed vocabulary is opened at both.
        So the promise is not held by any one line that could be edited away
        by accident; it is held by the vocabulary being closed, and that is
        the thing this test actually guards.

        The streaming case stayed green even then, which says the SSE error
        event filters through the vocabulary a third time.
        """
        self._failing_provider(monkeypatch, 403, moderation=True)
        chat_id = make_chat(client, make_character(client))
        response = client.post(f"/api/v1/chats/{chat_id}/{route}",
                               json={"message": "hi",
                                     "model_id": "test/model-1"})
        assert self.LEAK not in response.text
        # The refusal must still be sayable, or the app has swapped a leak for
        # a shrug: the reader needs to learn it was refused, just not with the
        # provider's own words.
        assert response.text.strip(), "a refusal with no code at all"


class TestTheLogNeverCarriesWhatWasSaid:
    """A log file outlives the session and sits outside the vault entirely."""

    PHRASE = "the-private-thing-the-user-typed-into-the-box"

    def test_a_completed_turn_logs_no_message_text(
        self, client, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        class _Response:
            status_code = 200
            is_success = True

            def json(self) -> dict:
                return {"choices": [{"message": {"content": "ok"}}]}

        class _Client:
            async def post(self, *args, **kwargs):
                return _Response()

        monkeypatch.setattr(openrouter, "get_client", lambda: _Client())
        monkeypatch.setattr(openrouter, "get_secret", lambda name: "sk-test")

        chat_id = make_chat(client, make_character(client))
        with caplog.at_level(logging.DEBUG):
            response = client.post(
                f"/api/v1/chats/{chat_id}/complete",
                json={"message": self.PHRASE, "model_id": "test/model-1"})
            assert response.status_code == 200, response.text
        assert self.PHRASE not in caplog.text

    def test_a_failed_turn_logs_no_message_text(
        self, client, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # The failure path is where content escapes: an exception repr, or a
        # "payload was" line somebody added while debugging and left in.
        class _Client:
            async def post(self, *args, **kwargs):
                raise RuntimeError("provider exploded")

        monkeypatch.setattr(openrouter, "get_client", lambda: _Client())
        monkeypatch.setattr(openrouter, "get_secret", lambda name: "sk-test")

        chat_id = make_chat(client, make_character(client))
        with caplog.at_level(logging.DEBUG):
            client.post(f"/api/v1/chats/{chat_id}/complete",
                        json={"message": self.PHRASE,
                              "model_id": "test/model-1"})
        assert self.PHRASE not in caplog.text
