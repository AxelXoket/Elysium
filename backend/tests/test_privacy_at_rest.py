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
from tests.conftest import make_chat, make_character


class TestTheDatabaseFileIsCiphertext:
    SENTINEL = "ciphertextsentinelphrasexyzzy"

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

        raw = Path(config.DB_PATH).read_bytes()
        assert self.SENTINEL.encode() not in raw
        assert self.SENTINEL.encode("utf-16-le") not in raw

    def test_a_character_card_is_not_findable_in_the_raw_bytes(
        self, client
    ) -> None:
        # A card carries the system prompt and the opening line, which is what
        # the WebView2 cache leak turned out to be full of.
        import config
        make_character(client, name="Cardsentinelzzz",
                       first_mes="opening-line-sentinel-zzz")
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
        if response.status_code not in (200, 201):
            pytest.skip(f"upload route answered {response.status_code}")

        raw = Path(config.DB_PATH).read_bytes()
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
