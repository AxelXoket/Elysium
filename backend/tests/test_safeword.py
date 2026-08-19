"""The one limit in this app that is a control rather than a request.

Everything else the notebook carries is a paragraph in a prompt. A paragraph
in a prompt is something a model may decline to honour, and the specification
says so in as many words - which is why this one is matched in CODE, before
the provider is called, and why "it worked" here means the request never
existed rather than that the model agreed.

The tests are therefore about ABSENCE: nothing sent, nothing stored, on every
path that can send.
"""
from __future__ import annotations

import pytest

import config
import notebook_store as notebook

from tests.conftest import make_character, make_chat


@pytest.fixture
def armed(db):
    notebook.set_safeword("kırmızı")
    yield "kırmızı"
    notebook.set_safeword("")


class TestItMatchesTheWayPeopleType:
    def test_the_word_alone(self, armed) -> None:
        assert notebook.safeword_in("kırmızı")

    def test_inside_a_sentence(self, armed) -> None:
        """Somebody reaching for a safeword is not composing carefully."""
        assert notebook.safeword_in("no wait kırmızı stop")

    @pytest.mark.parametrize("typed", ["KIRMIZI", "Kırmızı", "KIRMIZI!"])
    def test_whatever_case_it_was_typed_in(self, armed, typed) -> None:
        """Turkish case folding is the trap: `I` lowercases to `i` under the
        invariant rules and to `ı` under Turkish ones, so a safeword typed in
        capitals would have failed to match exactly once - the one time it
        mattered."""
        assert notebook.safeword_in(typed)

    def test_an_ordinary_message_does_not_trip_it(self, armed) -> None:
        assert not notebook.safeword_in("she wore a red coat")

    def test_nothing_trips_it_when_it_is_not_set(self, db) -> None:
        """Ground: unset must mean off, not match-everything."""
        notebook.set_safeword("")
        assert not notebook.safeword_in("kırmızı")
        assert not notebook.safeword_in("")

    def test_it_is_refused_if_it_is_too_long_to_type_in_a_hurry(self, db):
        with pytest.raises(notebook.NotebookError) as exc:
            notebook.set_safeword("x" * 200)
        assert exc.value.code == "safeword_too_long"


class TestTheTurnDoesNotHAPPEN:
    """Not "the model was asked to stop" - the request is never made."""

    def _chat(self, client) -> int:
        return make_chat(client, make_character(client))

    def test_the_plain_route_sends_nothing(self, client, provider, armed):
        chat_id = self._chat(client)
        resp = client.post(f"/api/v1/chats/{chat_id}/complete",
                           json={"message": "kırmızı", "model_id": "m/1"})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "safeword_triggered"
        assert provider.calls == [], "the provider was called anyway"

    def test_the_streaming_route_sends_nothing(self, client, provider, armed):
        chat_id = self._chat(client)
        resp = client.post(f"/api/v1/chats/{chat_id}/complete/stream",
                           json={"message": "kırmızı", "model_id": "m/1"})
        assert resp.status_code == 400
        assert provider.calls == []

    def test_nothing_is_stored(self, client, provider, armed) -> None:
        """Not even the user's own message. The turn did not happen."""
        chat_id = self._chat(client)
        before = client.get(f"/api/v1/chats/{chat_id}/messages").json()
        client.post(f"/api/v1/chats/{chat_id}/complete",
                    json={"message": "kırmızı", "model_id": "m/1"})
        after = client.get(f"/api/v1/chats/{chat_id}/messages").json()
        assert len(after) == len(before)

    def test_an_ordinary_message_still_goes(self, client, provider, armed):
        """The positive control, and the one that matters most: a safeword
        that stopped everything would be indistinguishable from a broken app."""
        chat_id = self._chat(client)
        resp = client.post(f"/api/v1/chats/{chat_id}/complete",
                           json={"message": "she wore a red coat",
                                 "model_id": "m/1"})
        assert resp.status_code == 200, resp.text
        assert len(provider.calls) == 1

    def test_it_is_checked_before_the_API_KEY(self, client, armed,
                                              monkeypatch) -> None:
        """Order is the whole design. Checked after the key, a user with no
        key configured would get "add an API key" when they typed their
        safeword - the app answering a question they did not ask, at the worst
        possible moment."""
        import database
        import secrets_service

        secrets_service.delete_secret(config.SECRET_API_KEY)
        assert database is not None

        chat_id = self._chat(client)
        resp = client.post(f"/api/v1/chats/{chat_id}/complete",
                           json={"message": "kırmızı", "model_id": "m/1"})
        assert resp.json()["detail"] == "safeword_triggered"


class TestTheRoute:
    def test_it_round_trips(self, client) -> None:
        assert client.get("/api/v1/notebook/safeword").json()["word"] == ""
        client.post("/api/v1/notebook/safeword", json={"word": "  red  "})
        assert client.get("/api/v1/notebook/safeword").json()["word"] == "red"

    def test_it_can_be_turned_off(self, client) -> None:
        client.post("/api/v1/notebook/safeword", json={"word": "red"})
        client.post("/api/v1/notebook/safeword", json={"word": ""})
        assert client.get("/api/v1/notebook/safeword").json()["word"] == ""

    def test_the_word_never_reaches_the_log(self, client, caplog) -> None:
        import logging

        with caplog.at_level(logging.DEBUG):
            client.post("/api/v1/notebook/safeword",
                        json={"word": "peynirli pide"})
        assert "peynirli" not in caplog.text
        # Ground: something WAS logged, so this is not passing on silence.
        assert "Safeword" in caplog.text
