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

    def _failing_provider(self, monkeypatch, status: int) -> None:
        leak = self.LEAK

        class _Response:
            status_code = status
            is_success = False

            def json(self) -> dict:
                return {"error": {"message": leak}}

            @property
            def text(self) -> str:
                return json.dumps(self.json())

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
                                return leak.encode()

                            async def aiter_bytes(self):
                                yield leak.encode()

                        return _Stream()

                    async def __aexit__(self, *exc):
                        return False

                return _Ctx()

        monkeypatch.setattr(openrouter, "get_client", lambda: _Client())
        monkeypatch.setattr(openrouter, "get_secret", lambda name: "sk-test")

    @pytest.mark.parametrize("status", [400, 402, 429, 500, 503])
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

    @pytest.mark.parametrize("status", [400, 429, 500])
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
