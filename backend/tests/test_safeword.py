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

import unicodedata

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
        import secrets_service

        secrets_service.delete_secret(config.SECRET_API_KEY)
        chat_id = self._chat(client)

        # The precondition, asserted rather than assumed. Without this the
        # test claims an ORDER while proving nothing: with a key still in
        # place the safeword fires first no matter which check comes first,
        # so the test stayed green against code that checked the key first.
        # Measured - stubbing delete_secret to a no-op left it passing.
        ordinary = client.post(f"/api/v1/chats/{chat_id}/complete",
                               json={"message": "she wore a red coat",
                                     "model_id": "m/1"})
        assert ordinary.json()["detail"] == "api_key_missing", (
            "the key was not actually gone, so this test proves no ordering")

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


class TestItCannotBeSetToSomethingThatDisarmsIt:
    """The two ways this control silently stopped being one."""

    def test_a_space_is_refused(self, db) -> None:
        """It collapsed to empty, empty means OFF, and the box went on showing
        the space - so the only tell arrived on the next remount and until
        then the user believed their stop was armed."""
        with pytest.raises(notebook.NotebookError) as exc:
            notebook.set_safeword(" ")
        assert exc.value.code == "safeword_blank"
        assert notebook.safeword() == ""

    def test_an_EMPTY_string_still_turns_it_off(self, db) -> None:
        """Ground: refusing whitespace must not make the feature unremovable."""
        notebook.set_safeword("kırmızı")
        notebook.set_safeword("")
        assert notebook.safeword() == ""

    @pytest.mark.parametrize("tiny", ["a", "bi"])
    def test_one_or_two_letters_are_refused(self, db, tiny) -> None:
        """A single letter appears inside almost every sentence, so the app
        becomes unsendable with the only cure buried in a panel the user is
        not looking at."""
        with pytest.raises(notebook.NotebookError) as exc:
            notebook.set_safeword(tiny)
        assert exc.value.code == "safeword_too_short"

    def test_three_is_enough(self, db) -> None:
        notebook.set_safeword("kes")
        assert notebook.safeword() == "kes"

    def test_the_route_refuses_them_too(self, client) -> None:
        for word, code in ((" ", "safeword_blank"), ("a", "safeword_too_short")):
            resp = client.post("/api/v1/notebook/safeword", json={"word": word})
            assert resp.status_code == 400
            assert resp.json()["detail"] == code


@pytest.fixture
def armed_latin(db):
    """A safeword with no Turkish letters in it at all.

    The fixture above picked `kırmızı`, and every one of its three `ı` is
    already dotless. That word cannot fail the way the folding fails, so the
    suite was green while the control was broken for a whole family of
    ordinary words: `exit`, `quit`, `limit`, `pain`, `kill`.
    """
    notebook.set_safeword("exit")
    yield "exit"
    notebook.set_safeword("")


class TestALatinSafewordSurvivesCapitals:
    """The case the old folding could not pass.

    `_fold_tr` turned every `I` into `ı` before lowercasing, unconditionally.
    So `EXIT` folded to `exıt`, the stored `exit` folded to `exit`, and the
    check missed. A safeword that misses is the one failure this feature
    exists to prevent, and it fails silently: the message goes to the model.
    """

    def test_the_word_alone(self, armed_latin) -> None:
        assert notebook.safeword_in("exit")

    @pytest.mark.parametrize("typed", ["EXIT", "Exit", "eXiT", "EXIT!"])
    def test_whatever_case_it_was_typed_in(self, armed_latin, typed) -> None:
        assert notebook.safeword_in(typed)

    def test_an_ordinary_message_still_does_not_trip_it(self, armed_latin) -> None:
        # POSITIVE CONTROL. Widening the fold must not make everything match.
        assert not notebook.safeword_in("the story continues")


class TestTheSameLetterTypedTwoWays:
    """NFC and NFD spell `İ` differently and look identical on screen."""

    def test_a_decomposed_capital_matches_a_composed_one(self, db) -> None:
        notebook.set_safeword("İZİN")           # composed, U+0130
        try:
            decomposed = "İŻİN".replace("Ż", "Z")
            assert notebook.safeword_in(decomposed)
        finally:
            notebook.set_safeword("")

    def test_an_unrelated_message_does_not_match(self, db) -> None:
        # POSITIVE CONTROL for the normalisation.
        notebook.set_safeword("İZİN")
        try:
            assert not notebook.safeword_in("nothing like it here")
        finally:
            notebook.set_safeword("")


class TestALetterThatDecomposesToSomethingElse:
    """The half NFC is actually load-bearing for.

    A decomposed `İ` is rescued by the combining-dot strip further down, so it
    proves nothing about normalisation on its own - measured, by removing the
    NFC call and watching nothing fail. `Ğ` has no such rescue: decomposed it
    is `G` plus a combining breve, and `.lower()` leaves the breve exactly
    where it is. Composed and decomposed then fold to two different strings
    and the safeword misses.
    """

    def test_a_decomposed_breve_matches_a_composed_one(self, db) -> None:
        composed = "ĞUVEN"                       # G with breve
        decomposed = unicodedata.normalize("NFD", composed)
        assert composed != decomposed, "the fixture is not testing anything"
        notebook.set_safeword(composed)
        try:
            assert notebook.safeword_in(decomposed)
        finally:
            notebook.set_safeword("")

    def test_an_unrelated_message_does_not_match(self, db) -> None:
        # POSITIVE CONTROL: normalising both sides must not match everything.
        notebook.set_safeword("ĞUVEN")
        try:
            assert not notebook.safeword_in("nothing like it here")
        finally:
            notebook.set_safeword("")


class TestARefusedSafewordDoesNotComeBack:
    """The one string in this app that must never appear in a response.

    Two ceilings were declared for the same field: pydantic's on the request
    model, and the real one inside set_safeword. Pydantic's ran first, and
    with no RequestValidationError handler in this app FastAPI's default
    answered - which serialises pydantic's `errors()`, and pydantic v2 puts
    the REJECTED VALUE in an `input` key. So typing too long a safeword sent
    it straight back over the wire, and the user was shown a sentence written
    for generation parameters rather than the one written for this.
    """

    LONG = "please stop the scene right now " * 8   # 256 characters

    def test_the_refusal_carries_a_code_and_not_the_word(self, client) -> None:
        resp = client.post("/api/v1/notebook/safeword",
                           json={"word": self.LONG})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "safeword_too_long"
        assert self.LONG not in resp.text
        # Not just the whole string: no recognisable run of it either.
        assert "please stop the scene" not in resp.text

    def test_an_accepted_safeword_is_not_echoed_either(self, client) -> None:
        # GROUND CONTROL. Without it, a response body that was empty for
        # every request would satisfy the assertion above.
        try:
            resp = client.post("/api/v1/notebook/safeword",
                               json={"word": "amber"})
            assert resp.status_code == 200
            assert "amber" not in resp.text
        finally:
            client.post("/api/v1/notebook/safeword", json={"word": ""})

    def test_a_long_looking_phrase_that_is_short_when_typed_is_accepted(
        self, client,
    ) -> None:
        """The behaviour change the second ceiling was hiding.

        set_safeword collapses runs of whitespace before measuring. Pydantic
        measured the raw string, so a phrase padded with spaces was refused
        for a length it does not have once typed out.
        """
        padded = "red" + (" " * 70) + "light"      # 78 raw, 9 collapsed
        assert len(padded) > 64
        try:
            resp = client.post("/api/v1/notebook/safeword",
                               json={"word": padded})
            assert resp.status_code == 200
            assert notebook.safeword() == "red light"
        finally:
            client.post("/api/v1/notebook/safeword", json={"word": ""})
