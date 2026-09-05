"""The one limit in this app that is a control rather than a request.

Everything else the notebook carries is a paragraph in a prompt. A paragraph
in a prompt is something a model may decline to honour, and the specification
says so in as many words - which is why this one is matched in CODE, before
the provider is called, and why "it worked" here means the request never
existed rather than that the model agreed.

The tests are therefore about ABSENCE: nothing sent, nothing stored, on the
path the message is typed on.

That is not the only path that can send. The background note reader is the
second one, and it sends STORED message bodies rather than the one being
typed, and a stored body is not a message anybody is typing right now. Those
two situations get two different answers, and the difference is whether there
is still somebody at the keyboard to be told:

  - A message BEING TYPED is refused whole. The turn does not happen, nothing
    is stored, and the reader is told why. That is the emergency stop and the
    first class below is its measurement.
  - The word found ANYWHERE ELSE in an outgoing body - a line stored last
    week, a note the reader wrote itself, a character card, a stop sequence -
    is CUT OUT, and the rest of the body goes. There is no typed message to
    refuse there: the turn was asked for now, about something written before,
    and refusing it would stop the app over a word its user did not just type.

The last two classes are the measurement of the second half: what leaves the
machine is read off the transport rather than off a stand-in, because the cut
happens between the body being built and the socket.
"""
from __future__ import annotations

import base64
import json
import random
import unicodedata
from collections.abc import Iterator

import httpx
import pytest

import config
import notebook_store as notebook
import notebook_worker
from database import get_db, set_setting
from safeword_filter import PROTOCOL_FIELDS

from tests.conftest import TEST_VAULT_KEY, make_character, make_chat


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
    model, and the real one inside set_safeword. Pydantic's ran first, and at
    the time there was no `RequestValidationError` handler at all, so
    FastAPI's default answered - which serialises pydantic's `errors()`, and
    pydantic v2 puts the REJECTED VALUE in an `input` key. So typing too long
    a safeword sent it straight back over the wire, and the reader was shown
    a sentence written for generation parameters rather than the one written
    for this.

    H-42 has since closed the echo application-wide (`main.py` strips `input`
    and `ctx`), which is a second net under this one. This test still drives
    the field itself, because the ceiling being in ONE place is what decides
    WHICH refusal arrives, and that is a different property from whether the
    rejected value travels.
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


# ── The second thing in this app that sends a conversation ──────────────────
#
# Everything the class below needs is built OUTSIDE the test bodies, and every
# AssertionError that is not the final assertion is translated on the way out.
#
# That is not tidiness, and it outlived the marker that made it urgent. The
# last test here used to carry a strict expected-failure limited to
# AssertionError; `make_chat` and `make_character` assert on their status
# codes, so a route that grew a required field would raise AssertionError
# while building the fixture, be counted as the expected failure, and report a
# green suite for a test that had sent nothing at all. Measured, by breaking
# `make_chat` on purpose and watching the test report XFAIL rather than an
# error - a fixture alone did not close it, because pytest evaluates an xfail
# marker against the SETUP report as well as the call one.
#
# The marker is gone now that the question it stood for has an answer, so the
# absorbing surface is gone with it. The translation stays because it is still
# the difference between two failures that read the same and mean opposite
# things: "the reader sent something and it carried the word" and "the fixture
# never built a chat". The first is the defect these tests exist for; the
# second is a broken harness, and an untranslated AssertionError from a
# fixture reports the second wearing the first one's message.

#: One id, written by the fixture and read back off the wire. Any model would
#: do; what is proved by reading it back is that the reader used the one it
#: was given rather than some default of its own.
EXTRACT_MODEL = "vendor/cheap"

#: The character's opening line, and so the OLDEST message in the chat. It is
#: the window guard, and the direction matters: one extraction selects the
#: OLDEST unread messages up to its limit and drops from the NEW end, so the
#: greeting arriving in the payload is what proves the run read from the top
#: of the backlog and therefore reached the line written directly after it.
#: Measured on the default settings: twenty two messages are stored and the
#: two NEWEST never arrive, which is exactly why the guard is on the oldest
#: line and not on the newest.
GREETING = "Hello there!"

#: Ordinary conversation either side of the line under test.
FILLER = "her brother owns the mill"

#: A stored line with no safeword in it, written by one fixture and read back
#: by the ground control, so the two cannot drift apart.
CLEAN_LINE = "she wore a red coat"

#: How many lines `_chat_that_starts_with` puts at the very TOP of the chats
#: it builds: the greeting, and then the line under test. Everything after
#: those two is filler, so a read window of exactly this size holds them and
#: nothing else. This is a fact about the fixture in this file, not a number
#: copied out of the application.
HEAD_LINES = 2


def _window() -> int:
    """How many stored lines ONE ordinary extraction may read.

    Asked of a fresh worker rather than retyped. The reader derives this
    itself and halves it after failures, so a test that hard-coded the default
    would be measuring the window rather than the safeword the moment either
    number moved. A fresh worker has no failures, which is the state every
    test in this class runs in.
    """
    return notebook_worker.Worker().batch_size


#: Everything in a tainted line EXCEPT the safeword, written once so the
#: builder and the assertion cannot drift apart. The cut takes the word and
#: leaves this, and a filter that answered by dropping the line, or by sending
#: nothing at all, would satisfy "the word is not in what went out" while
#: losing it.
#:
#: Deliberately a sentence the application never writes. The first version of
#: this said "stop the scene", which is also the text of
#: `notebook_store._ON_VIOLATION_PROSE["hard_stop"]` - so a chat with a hard
#: stop among its limits would have put the same words in the payload for
#: reasons of its own, and the assertion would have gone quietly vacuous.
REMAINDER = "and then the letter arrived"

#: The model id every direct call below sends. One definition, because two
#: tests read it back off the wire and a second copy would let them drift.
MODEL = "vendor/model"


def _tainted(word: str) -> str:
    """The word as somebody in a hurry would actually have typed it.

    In CAPITALS, and that is load-bearing rather than colour. `safeword_in`
    folds case the Turkish way; a raw substring does not. Stored verbatim, a
    leak check written with `in` would have found the word just as well and
    the choice of matcher further down would have been decoration that could
    not be made to fail. Typed this way, only the real matcher sees it.
    """
    return f"{word.upper()} {REMAINDER}"


def _filler(i: int) -> str:
    """One definition, used both to write the chat and to recognise it after.

    Two copies would have let the builder and the assertion drift apart, and
    the assertion would then be looking for a sentence nothing ever stored.
    """
    return f"line {i}: {FILLER}"


def _strings(value) -> Iterator[str]:
    """Every string anywhere inside a captured payload, keys included.

    Recursive rather than a walk over the slots that are known today, because
    the shape of a provider request is the provider's business and it has been
    reshaped before. A sweep that only knows this week's shape reports clean
    on next week's.
    """
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(key)
            yield from _strings(item)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            yield from _strings(item)
    else:
        # Numbers, booleans, None, and whatever a future argument turns out to
        # be. Rendered rather than skipped: a text carrier that is not a `str`
        # - an enum, a model object with a `__str__` - would otherwise be a
        # hole in the sweep that looks exactly like a clean payload.
        yield str(value)


def _body_of(call: dict) -> str:
    """EVERY part of the outgoing payload, not the part expected to carry it.

    Reading one message by index answers a narrower question than the one
    being asked, and it goes quiet rather than red if the payload is ever
    reshaped. The safeword must not be anywhere in what goes out.

    This joined `messages` alone until it was measured, and that is the same
    mistake one level up: with the transcript moved into `gen_params` the leak
    check reported a clean payload while the identical text left on the
    identical request. So this flattens the whole body, every key and every
    value, at every depth.

    The BODY, and only the body. Headers and the URL are swept separately, by
    the one test that reads `wire.requests`.
    """
    return "\n".join(_strings(call))


def _chat_that_starts_with(client, line: str) -> int:
    """A chat whose first stored message is `line`, with enough after it.

    First rather than last on purpose. One extraction reads a BOUNDED window,
    so a line appended to the end of a long backlog sits outside the range the
    run reads, and a test built that way measures the window instead of the
    safeword. Measured: appended last, the line never appeared in the body at
    all and both tests here were answering the wrong question.

    Written through the database rather than the send route, because a body
    carrying the safeword is the one thing the send route correctly refuses to
    create. Rows like that exist in real conversations because they were
    stored before anybody set a safeword, and the reader treats a stored row
    the same whenever it arrived.
    """
    insert = ("INSERT INTO messages (chat_id, role, content, active) "
              "VALUES (?,?,?,1)")
    chat_id = make_chat(client, make_character(client, first_mes=GREETING))
    with get_db() as con:
        con.execute(insert, (chat_id, "user", line))
        for i in range(config.NOTEBOOK_EXTRACT_EVERY_TURNS):
            con.execute(insert, (chat_id,
                                 "user" if i % 2 == 0 else "assistant",
                                 _filler(i)))
    # The model setting is the on/off switch for this whole path: with no
    # model chosen the reader returns before it plans anything.
    set_setting(config.SETTING_NOTEBOOK_MODEL, EXTRACT_MODEL)
    return chat_id


#: The smallest reply every caller in this file can finish reading. The note
#: reader decodes `content` as JSON, so it carries an empty extraction rather
#: than prose.
REPLY = {"id": "gen-1",
         "choices": [{"finish_reason": "stop",
                      "message": {"content": json.dumps({"facts": []})}}],
         "usage": {}}

#: The streaming twin of REPLY, in the shape the SSE reader expects.
REPLY_SSE = (b'data: {"choices":[{"delta":{"content":"ok"},'
             b'"finish_reason":"stop"}]}\n\n'
             b'data: [DONE]\n\n')


class _Wire(list):
    """The bodies that left, and the whole requests they were serialised into.

    A list of bodies is what almost every test here wants, so that is what
    this IS. The requests ride along beside them because a body is not the
    whole of what leaves: OpenRouter reads `HTTP-Referer` and `X-Title` from
    headers, and a URL can carry a query. One test sweeps those.
    """

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[httpx.Request] = []


#: The wire the running test installed, so `wire_of` can find its way from
#: a captured body back to the request it came out of without every test in
#: the file having to take a second fixture it does not use.
_LAST_WIRE: list[_Wire] = []


@pytest.fixture
def wire(monkeypatch) -> _Wire:
    """Every request body that actually LEAVES, read off the transport.

    NOT a stand-in for `openrouter.complete`, which is where this capture used
    to sit and is the one place it cannot sit any more. The cut happens inside
    that function, between the body being built and the socket, so a stand-in
    installed over the function replaces the thing under test: it would report
    the uncut body forever, and report it in green. Measured - with the
    capture in its old place the whole of this file passes against a filter
    that is never called.

    Installed under httpx instead, so what is recorded is the JSON httpx
    serialised, taken at the last point the request is still an object. That
    also closes the failure the old capture needed a signature check to guard
    against: a stand-in can drop an argument at the door, and a serialised
    body has no door. Every argument the call was made with is already spread
    into the shape the provider receives.

    NOT a socket, and the difference matters for what this file may claim.
    `MockTransport` is itself a stand-in; nothing leaves the machine and the
    always-on egress guard has nothing to refuse. What is proved is that the
    cut happened before httpx was handed the request, which rules out the real
    client, a transport hook and `network_client`, and rules in nothing about
    the proxy or the destination gate.
    """
    import openrouter

    bodies = _Wire()

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        bodies.requests.append(request)
        if body.get("stream"):
            return httpx.Response(
                200, content=REPLY_SSE,
                headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json=REPLY)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(openrouter, "get_client", lambda: client)
    _LAST_WIRE.clear()
    _LAST_WIRE.append(bodies)
    return bodies


async def _read_the_chat(chat_id: int):
    """One extraction, and the worker after it so its counters can be read."""
    w = notebook_worker.Worker()
    await w._handle(chat_id)
    return w


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def clean_chat(client, armed) -> int:
    """Stored history with no safeword anywhere in it."""
    return _chat_that_starts_with(client, CLEAN_LINE)


@pytest.fixture
def tainted_chat(client, armed) -> int:
    """Stored history whose oldest line carries the safeword.

    The translation below is the half a fixture cannot do on its own. See the
    note at the top of this section for what it is protecting against.
    """
    try:
        return _chat_that_starts_with(client, _tainted(armed))
    except AssertionError as exc:
        raise RuntimeError(f"the fixture could not build the chat: {exc}") from exc


class TestTheOtherPathThatCanSend:
    """The background note reader sends STORED bodies, not the typed one.

    The refusal is matched in one place, against the message going out right
    now. The note reader wakes later, reads message bodies straight out of the
    database and builds a prompt of its own, so the refusal has nothing to say
    about what was stored yesterday - and for a while nothing else did either,
    which is the leak this class was written to measure.

    It is closed by a cut rather than by a refusal, and that needs three
    things said, one per test: the path is still live while the safeword is
    armed, the sweep that judges a body is wide enough to see the word
    anywhere in it, and the word is out of what leaves while the rest of the
    line is still in it.
    """

    @pytest.mark.anyio
    async def test_an_ordinary_stored_chat_is_read_and_sent(
            self, clean_chat, wire) -> None:
        """GROUND, and the one that refuses the cheap answer.

        "The safeword is not in the body" is satisfied by a note reader that
        stopped sending anything at all, and by one that was never asked to
        send in the first place. This says the path is live WHILE the safeword
        is armed: the configured model was called, the window reached the
        oldest line, the stored text was in it, and the run completed rather
        than merely being attempted. A fix that widens into "send nothing"
        fails here instead of passing quietly next door.
        """
        sent = wire

        w = await _read_the_chat(clean_chat)

        assert sent, "nothing was sent, so nothing in this class measures anything"
        assert sent[0]["model"] == EXTRACT_MODEL
        # The one configuration this whole class cannot measure, named out
        # loud instead of failing further down with a confusing message: a
        # window narrower than the fixture's own head never reaches the line
        # under test, so both the ground and the tripwire would be answering a
        # question about the window.
        assert _window() >= HEAD_LINES, (
            f"a read window of {_window()} is narrower than the {HEAD_LINES} "
            f"lines this fixture puts at the top of the chat, so nothing in "
            f"this class reaches the line under test")
        body = _body_of(sent[0])
        assert GREETING in body, "the window did not reach the oldest line"
        assert CLEAN_LINE in body
        if _window() > HEAD_LINES:
            assert _filler(0) in body
        else:
            # THE BOUNDARY, asserted rather than skipped. At the narrowest
            # window this class supports, the greeting and the line under test
            # fill it exactly and no filler can arrive. This test used to
            # demand `_filler(0)` unconditionally and went red on a perfectly
            # healthy reader at two turns per extraction - measured, by
            # running the class with ELYSIUM_NOTEBOOK_EVERY_TURNS=2. Filler
            # turning up HERE would mean the run read further than the reader
            # said it would, which is the direction still worth catching.
            assert _filler(0) not in body
        assert w.runs == 1, (
            # `last_error` alone is None on the commonest failure here: the
            # branch that rejects an untrustworthy reply records the reason on
            # the row and never on the worker. The other two counters are what
            # separate "the reply was refused" from "it landed as nothing".
            f"the extraction did not complete: last_error={w.last_error} "
            f"settled_empty={w.settled_empty} "
            f"failures={w.breaker.total_failures}")

    @pytest.mark.anyio
    async def test_the_leak_sweep_sees_the_word_in_any_part_of_the_payload(
            self, clean_chat, armed, wire) -> None:
        """POSITIVE CONTROL for the absence the test below asserts.

        A claim that the safeword is nowhere in what went out is worth exactly
        what the sweep behind it is worth, and this one was worth `messages`
        and nothing else. Measured: with the transcript moved into
        `gen_params` the check reported a clean payload while the same text
        left on the same request, and it reported it in green.

        This drives a real extraction, takes the payload the reader actually
        built, and plants the safeword in each of its slots in turn, including
        one level down inside a slot. Every plant has to be found.

        What was here before asserted that `safeword_in` matches a capitalised
        line with a role prefix in front of it. The first class in this file
        already proves the folding, and the prefix adds no letter the fold
        touches, so that block was a restatement rather than a control: it
        could not go red for any defect this class exists to catch. Its one
        real claim - that the matcher is not the broken half - is carried
        better here, where the matcher is driven through the sweep the
        assertion below actually uses.

        Deliberately independent of the cut: this reads a chat with no
        safeword in it, so it measures the sweep and nothing else. If it
        needed the cut to be working it would fail twice for one defect and
        say nothing about which.
        """
        sent = wire
        await _read_the_chat(clean_chat)

        assert sent, "nothing was sent, so there is no payload to control on"
        call = sent[0]

        # GROUND. The unplanted payload is clean, so every match below is the
        # planted word rather than something the reader always sends anyway.
        assert not notebook.safeword_in(_body_of(call))

        # The capture is the wire body, so nothing can be dropped between the
        # call and here - the failure the old signature check guarded against
        # cannot happen to a transport. What is still worth asserting is that
        # the loop below has more than one slot to plant into: a body that
        # arrived as a single key would make the sweep look wide while proving
        # one thing.
        assert len(call) > 1, f"the wire body is only {sorted(call)}"

        planted = f"user: {_tainted(armed)}"
        for slot in call:
            reshaped = dict(call)
            reshaped[slot] = planted
            assert notebook.safeword_in(_body_of(reshaped)), (
                f"the sweep is blind to the `{slot}` slot")

        # And one level down, which is the shape of the reshaping that was
        # measured: the transcript out of `messages` and in under a slot that
        # holds a dict, with the message list left behind as empty
        # scaffolding.
        nested = dict(call)
        nested["messages"] = [{"role": m["role"], "content": ""}
                              for m in call["messages"]]
        nested["provider"] = dict(call["provider"], transcript=planted)
        assert notebook.safeword_in(_body_of(nested)), (
            "the sweep is blind to text one level inside a slot")

    @pytest.mark.anyio
    async def test_a_stored_line_carrying_the_safeword_is_cut_out_of_what_is_sent(
            self, tainted_chat, wire) -> None:
        """The word is taken out of the stored line; the rest of it goes.

        This used to be a strict expected-failure carrying an open question:
        filter the matching lines out of the range, skip the range entirely,
        or write the narrower scope down and leave the behaviour. It is now an
        ordinary test, because the question was answered and the answer is
        none of those three exactly - it is the first one made narrower still.
        The unit that goes is the WORD, not the line and not the range, and
        that choice is what the second assertion below is for.

        Why not refuse the turn here, the way the typed path does: there is
        nobody typing. The note reader wakes on its own timer and reads rows
        that were stored before anybody armed anything. Refusing on their
        account would turn a word written last week into a permanent stop on a
        background job, with no message on screen to explain it.

        NO CALL AT ALL would satisfy the first assertion, and a reader that
        dropped the whole line would satisfy it too. Both are refused: the
        ground control above is a separate green test that fails when the
        reader stops sending, and REMAINDER below is what fails when the line
        is thrown away rather than trimmed.

        The match is `safeword_in`, not a substring. This file already carries
        the story of a control that missed on capitals, and a leak check that
        re-implemented the match with `in` would call a body clean while the
        word sat in it in a spelling the real matcher folds.
        """
        sent = wire

        try:
            await _read_the_chat(tainted_chat)
        except AssertionError as exc:
            # The reader carries asserts of its own, and one of them firing
            # must not arrive here looking like a measurement having been
            # taken.
            raise RuntimeError(
                f"the reader raised before anything was measured: {exc}") from exc

        assert sent, "nothing was sent, so nothing here measures anything"
        if GREETING not in _body_of(sent[0]):
            raise RuntimeError(
                "the range this run read does not cover the line under test")

        leaked = [call for call in sent
                  if notebook.safeword_in(_body_of(call))]
        assert not leaked, "the safeword was in what went out"
        assert REMAINDER in _body_of(sent[0]), (
            "the line was thrown away rather than trimmed")


# -- What actually leaves, on every one of the three routes ------------------
#
# The class above drives the note reader, which is one caller. These drive the
# provider functions themselves, which is where all three callers meet: the
# plain completion route, the streaming one, and the note reader. A test
# written one level up measures its own caller's assembly and says nothing
# about the other two.


@pytest.fixture
def sent_body(wire):
    """Make one real provider call and hand back the body that left.

    `provider` and `model_id` have defaults so a test can name only the thing
    it is about; both are overridable, because two of the tests below are
    about exactly those fields.
    """
    async def go(messages, *, model_id=MODEL, gen_params=None,
                 provider=None, **kw) -> dict:
        import openrouter

        await openrouter.complete(
            messages, model_id, dict(gen_params or {}),
            dict(provider or {"data_collection": "deny"}), **kw)
        assert wire, "nothing left the machine, so there is nothing to read"
        return wire[-1]

    return go


#: The protocol fields a test in this class plants the safeword inside and
#: reads back unchanged. Compared against the real frozenset below rather than
#: standing in for it.
#:
#: `stream` is the one that cannot be driven: both payload builders write it
#: as a bool literal (`openrouter.py`), so there is no string to plant and no
#: way for a cut to reach it.
COVERED_PROTOCOL_FIELDS = {"model", "provider", "response_format", "modalities"}


def wire_of(body: dict):
    """The request a captured body was serialised into.

    Held on the fixture rather than passed around, so the ordinary tests can
    go on reading a plain list of bodies.
    """
    return _LAST_WIRE[0].requests[_LAST_WIRE[0].index(body)]


def _live_control(word: str) -> dict:
    """A message on the SAME request that the cut must take.

    Every "never cut" test below asserts that a value came back exactly as it
    went. On its own each of them is green against a filter that was never
    called at all - measured, by deleting the two call lines in
    `openrouter.py` and watching six of them pass. This rides along so the
    absence and the presence are proved on one request instead of in two
    tests that can drift apart.
    """
    return {"role": "user", "content": f"{word.upper()} {REMAINDER}"}


def _assert_the_cut_was_live(body: dict) -> None:
    """The control above did its job on THIS request.

    Read off the control message alone rather than off the whole body, and
    that is the point rather than a shortcut. Every test that uses this
    deliberately leaves the word somewhere the filter is documented never to
    touch - inside a model id, inside a provider policy, inside the base64 of
    a picture - so a sweep of the whole body would find it there and this
    would be a check that can never pass. Absent from the prose and present in
    the protected value IS the claim.
    """
    control = [m["content"] for m in body["messages"]
               if isinstance(m.get("content"), str) and REMAINDER in m["content"]]
    assert control, (
        "the control message was dropped or emptied rather than trimmed, so "
        "the absence beside it proves nothing")
    assert not notebook.safeword_in(control[0]), (
        "the safeword survived in the control message, so the cut was not "
        "running on this request and the value beside it was never protected")


class TestTheWordIsCutOutOfWhatGoesOut:
    """The other half of the answer, measured at the transport.

    A typed message is refused whole and that is a different test, one class
    up. Everything else - a stored line, a note the reader wrote itself, a
    character card, a stop sequence - carries no such moment: the turn was
    asked for now, about text written before, and nobody at the keyboard typed
    the word to be told anything about it. So it is CUT and the rest goes.
    """

    @pytest.mark.anyio
    async def test_the_word_is_cut_and_the_rest_of_the_line_stays(
            self, armed, sent_body) -> None:
        body = await sent_body(
            [{"role": "user", "content": f"no wait {armed.upper()} stop"}])

        assert not notebook.safeword_in(_body_of(body)), (
            "the safeword was in what went out")
        # The unit that goes is the WORD. Not the line, not the sentence: a
        # filter that took either would pass the assertion above.
        out = body["messages"][0]["content"]
        assert "no wait" in out and "stop" in out

    @pytest.mark.anyio
    async def test_a_stop_sequence_carrying_the_word_is_cut_too(
            self, armed, sent_body) -> None:
        """`stop` is the only free user text that never travels inside
        `messages`. A filter that walks the message list and stops there
        passes every other test in this class and leaks on every request that
        carries a stop sequence.
        """
        body = await sent_body(
            [{"role": "user", "content": "an ordinary line"}],
            gen_params={"stop": [f"{armed.upper()}\n\nUser:", "###"]})

        assert not notebook.safeword_in(_body_of(body)), (
            "the safeword was in what went out")
        # The word came out of the sequence and the sequence stayed. Written
        # with the word INSIDE a longer sequence on purpose: a tainted entry
        # that is nothing but the word is emptied and dropped either way, so a
        # filter that discards any stop sequence containing the word - which
        # silently throws away a generation setting somebody chose - passed
        # the first version of this test.
        assert "\n\nUser:" in body["stop"]
        # POSITIVE CONTROL: the untainted sequence is not collateral either.
        assert "###" in body["stop"]

    @pytest.mark.anyio
    async def test_a_decomposed_letter_is_cut_at_the_right_place(
            self, db, sent_body) -> None:
        """The fold is not length preserving, so an offset found in the folded
        text is not an offset in the text that goes out.

        `GUVEN` with a breve on the G, written decomposed, is six characters
        and folds to five. A cut applied at the folded offset takes five
        characters starting one too far along: the safeword survives in pieces
        and a letter of the surrounding text goes with it.
        """
        composed = "ĞUVEN"
        typed = "a" + unicodedata.normalize("NFD", composed) + "b"
        assert len(typed) == len(composed) + 3, (
            "the fixture is not decomposed, so it is not testing anything")

        notebook.set_safeword(composed)
        try:
            body = await sent_body([{"role": "user", "content": typed}])
            assert body["messages"][0]["content"] == "ab"
        finally:
            notebook.set_safeword("")

    @pytest.mark.anyio
    async def test_an_attached_image_is_not_touched(self, db, sent_body) -> None:
        """A picture is not prose and must not be read as any.

        Base64 is 64 characters wide, so a three letter safeword turns up in
        it by chance roughly once every forty thousand characters. Measured on
        the megabyte below: fifty one collisions. Cut, the image is not
        damaged in a way anybody would notice - it is destroyed, silently, and
        the model answers anyway about a picture it could not read.
        """
        raw = random.Random(20).randbytes(1024 * 1024)
        b64 = base64.b64encode(raw).decode("ascii")
        url = f"data:image/png;base64,{b64}"

        notebook.set_safeword("kes")
        try:
            # GROUND: without a collision in it this fixture proves nothing,
            # and a change of seed or size could take the collisions away
            # without anything else in the test noticing.
            assert notebook.safeword_in(b64), "no collision, so no measurement"

            body = await sent_body([
                _live_control("kes"),
                {"role": "user", "content": [
                    {"type": "text", "text": "look at this"},
                    {"type": "image_url", "image_url": {"url": url}},
                ]}])

            out = body["messages"][1]["content"][1]["image_url"]["url"]
            assert base64.b64decode(out.split(",", 1)[1]) == raw
            # And the prose beside it was still read.
            assert body["messages"][1]["content"][0]["text"] == "look at this"
            _assert_the_cut_was_live(body)
        finally:
            notebook.set_safeword("")

    @pytest.mark.anyio
    async def test_a_picture_is_known_by_its_bytes_and_not_only_by_its_slot(
            self, db, sent_body) -> None:
        """The wrapper around a picture is the provider's business.

        The test above puts one where this build puts it, under an `image_url`
        key, and that whole key is skipped. This puts the same bytes in a slot
        this build does not use, because the shape of a provider request has
        been reshaped before and a guard that only knows this week's slot
        reports clean on next week's. Two guards, and this is the one that
        reads what the value IS rather than where it sits.
        """
        raw = random.Random(0).randbytes(64 * 1024)
        b64 = base64.b64encode(raw).decode("ascii")
        url = f"data:image/png;base64,{b64}"

        notebook.set_safeword("kes")
        try:
            assert notebook.safeword_in(b64), "no collision, so no measurement"
            body = await sent_body([
                _live_control("kes"),
                {"role": "user", "content": [
                    {"type": "input_image", "image": url},
                ]}])
            out = body["messages"][1]["content"][0]["image"]
            assert base64.b64decode(out.split(",", 1)[1]) == raw
            _assert_the_cut_was_live(body)
        finally:
            notebook.set_safeword("")

    @pytest.mark.anyio
    async def test_a_picture_that_is_a_link_rather_than_bytes_is_left_alone(
            self, db, sent_body) -> None:
        """And this is the other guard, the one that reads the slot.

        A hosted picture arrives as an ordinary URL with no `data:` in front
        of it, so the bytes test above has nothing to recognise. A URL is a
        protocol value like a model id: cut, it fetches nothing, and the model
        answers about a picture that was never there.
        """
        link = "https://example.invalid/kesit/photo.png"

        notebook.set_safeword("kes")
        try:
            assert notebook.safeword_in(link), "no collision, so no measurement"
            body = await sent_body([
                _live_control("kes"),
                {"role": "user", "content": [
                    {"type": "image_url",
                     "image_url": {"url": link, "alt": f"a KESIT {REMAINDER}"}},
                ]}])
            part = body["messages"][1]["content"][0]["image_url"]
            assert part["url"] == link
            # The address is skipped; the CAPTION beside it is prose and is
            # not. Skipping the whole `image_url` object would take this with
            # it, which is a hole one added field wide.
            assert REMAINDER in part["alt"]
            _assert_the_cut_was_live(body)
        finally:
            notebook.set_safeword("")

    @pytest.mark.anyio
    async def test_a_line_that_merely_starts_with_data_is_still_cut(
            self, armed, sent_body) -> None:
        """Five characters a user can type must not turn the filter off.

        The skip above exists for base64, and base64 is what it has to look
        for. A skip that trusts the first five characters is a skip anybody
        can arm by starting a sentence with the word `data` and a colon - and
        the sentence most likely to do it is a stored one, which is the exact
        category this whole filter was written for. It fails silently: the
        body goes out unchanged and nothing is logged.
        """
        typed = f"data: 30/08 - reminder, {armed.upper()} means stop"

        body = await sent_body([{"role": "user", "content": typed}])

        assert not notebook.safeword_in(_body_of(body)), (
            "the safeword was in what went out")
        out = body["messages"][0]["content"]
        assert out.startswith("data: 30/08"), "the rest of the line went too"

    @pytest.mark.anyio
    async def test_a_stop_sequence_dressed_as_a_data_uri_is_still_cut(
            self, armed, sent_body) -> None:
        """The same five characters, in the one slot that is not a message."""
        body = await sent_body(
            [{"role": "user", "content": "an ordinary line"}],
            gen_params={"stop": [f"data:{armed.upper()}"]})

        assert not notebook.safeword_in(_body_of(body)), (
            "the safeword was in what went out")

    @pytest.mark.anyio
    async def test_a_data_uri_is_recognised_whatever_case_the_scheme_is_in(
            self, db, sent_body) -> None:
        """A URI scheme is case-insensitive, and the damage is silent.

        Measured on the byte guard alone: with the scheme test spelled in
        lower case, `DATA:` fell through and the picture came out of the cut
        with holes in it. The model answers either way, about a picture it
        could not decode.
        """
        raw = random.Random(0).randbytes(64 * 1024)
        b64 = base64.b64encode(raw).decode("ascii")

        notebook.set_safeword("kes")
        try:
            assert notebook.safeword_in(b64), "no collision, so no measurement"
            body = await sent_body([
                _live_control("kes"),
                {"role": "user", "content": [
                    {"type": "input_image",
                     "image": f"DATA:image/png;base64,{b64}"},
                ]}])
            out = body["messages"][1]["content"][0]["image"]
            assert base64.b64decode(out.split(",", 1)[1]) == raw
            _assert_the_cut_was_live(body)
        finally:
            notebook.set_safeword("")

    @pytest.mark.anyio
    async def test_the_model_id_is_never_cut(self, armed_latin, sent_body) -> None:
        """A model id is a protocol value, not something a person wrote.

        `exit` is an ordinary safeword and `vendor/exit-model` is an ordinary
        model id. Cutting there protects nobody and the request 404s.
        """
        model_id = f"vendor/{armed_latin}-model"
        # GROUND: derived from the fixture rather than hand-typed, so a change
        # of safeword cannot leave this measuring nothing while staying green.
        assert notebook.safeword_in(model_id), "no collision, so no measurement"

        body = await sent_body([_live_control(armed_latin)], model_id=model_id)

        assert body["model"] == model_id
        _assert_the_cut_was_live(body)

    @pytest.mark.anyio
    async def test_the_provider_policy_is_never_cut(
            self, armed_latin, sent_body) -> None:
        """Same reason, and this one carries the privacy promise itself: the
        policy is what tells the provider not to keep the request."""
        policy = {"data_collection": "deny",
                  "order": [f"{armed_latin}-lab"]}
        assert notebook.safeword_in(policy["order"][0]), (
            "no collision, so no measurement")

        body = await sent_body([_live_control(armed_latin)],
                               provider=dict(policy))

        assert body["provider"] == policy
        _assert_the_cut_was_live(body)

    @pytest.mark.anyio
    async def test_the_response_schema_is_never_cut(self, db, sent_body) -> None:
        """A schema cut to pieces is not a narrower schema, it is a broken one.

        `str` is three characters and therefore a legal safeword, and it sits
        inside every `"string"` a JSON schema declares. The reply would then be
        parsed against something that is no longer JSON schema, on a request
        the reader never connected to the word they typed.
        """
        def schema() -> dict:
            return {"type": "json_schema",
                    "json_schema": {"name": "facts",
                                    "schema": {"type": "object",
                                               "properties": {
                                                   "facts": {
                                                       "type": "string"}}}}}

        notebook.set_safeword("str")
        try:
            assert notebook.safeword_in(json.dumps(schema())), (
                "no collision, so no measurement")

            body = await sent_body([_live_control("str")],
                                   response_format=schema())

            assert body["response_format"] == schema()
            _assert_the_cut_was_live(body)
        finally:
            notebook.set_safeword("")

    @pytest.mark.anyio
    async def test_a_card_and_an_old_reply_are_cut_the_same_as_a_line_typed(
            self, armed, sent_body) -> None:
        """The two turns nothing else in this file plants the word in.

        A character card and a note the reader wrote about the scene both
        travel in the SYSTEM message; an old reply travels as an ASSISTANT
        one. Neither is ever seen by the refusal a layer up, which reads the
        message being typed and nothing else - so this class is the only place
        either is measured, and a filter that walked the user turns alone
        passed every other test here. Measured, by writing that filter.

        The assistant half is the one with no other net at all: a reply the
        model wrote is never checked on its way IN, and it is replayed as
        history on every turn afterwards.
        """
        body = await sent_body([
            {"role": "system",
             "content": f"[Character: Ada] {armed.upper()} {REMAINDER}"},
            {"role": "assistant",
             "content": f"she said {armed.upper()} once. {REMAINDER}"},
            {"role": "user", "content": CLEAN_LINE},
        ])

        assert not notebook.safeword_in(_body_of(body)), (
            "the safeword was in what went out")
        # And all three turns are still there, trimmed rather than thrown.
        assert len(body["messages"]) == 3
        for message in body["messages"][:2]:
            assert REMAINDER in message["content"]
        assert body["messages"][2]["content"] == CLEAN_LINE

    @pytest.mark.anyio
    async def test_a_long_line_is_read_all_the_way_through(
            self, armed, sent_body) -> None:
        """Length is not a reason to stop reading.

        "Skip anything big, it is probably a picture" is the cheap version of
        the data URI rule, and it is the version that leaks: an ordinary
        conversation reaches tens of thousands of characters and a stored line
        near the end of one is exactly what this filter exists for. Measured:
        with strings over five hundred characters left alone, every other test
        in this class stayed green.
        """
        filler = "the river runs past the mill. " * 80
        typed = f"{filler}{armed.upper()} {REMAINDER}"
        assert len(typed) > 2000, "the fixture is not long enough to test it"

        body = await sent_body([{"role": "user", "content": typed}])

        assert not notebook.safeword_in(_body_of(body)), (
            "the safeword was in what went out")
        out = body["messages"][0]["content"]
        assert out.startswith(filler) and out.endswith(REMAINDER)

    @pytest.mark.anyio
    async def test_the_modality_list_is_never_cut(self, db, sent_body) -> None:
        """The fourth protocol field, and the one with no other test.

        `modalities` is a list of protocol words. A three letter safeword
        inside one of them would leave the provider a modality it does not
        know, on a request the reader never connected to the word they typed.
        """
        notebook.set_safeword("mag")
        try:
            modalities = ["text", "image"]
            planted = ["text", "imagmage"]
            assert notebook.safeword_in(planted[1]), (
                "no collision, so no measurement")

            body = await sent_body([_live_control("mag")],
                                   modalities=planted)

            assert body["modalities"] == planted
            _assert_the_cut_was_live(body)
            assert modalities != planted, "the fixture is not testing anything"
        finally:
            notebook.set_safeword("")

    def test_every_protocol_field_is_driven_or_named(self) -> None:
        """The list of untouchable fields is IMPORTED, not retyped.

        A field added to `PROTOCOL_FIELDS` is a new promise never to cut
        something, and a promise with no test is how the promise stops being
        true. This goes red when one is added, and names the one field that
        cannot be driven so the exemption is visible rather than assumed.
        """
        assert PROTOCOL_FIELDS - COVERED_PROTOCOL_FIELDS == {"stream"}
        assert COVERED_PROTOCOL_FIELDS < PROTOCOL_FIELDS

    @pytest.mark.anyio
    async def test_nothing_travels_outside_the_json_body(
            self, armed, sent_body) -> None:
        """A body is not the whole request.

        OpenRouter reads `HTTP-Referer` and `X-Title` off the headers, and a
        URL can carry a query. Neither is scrubbed, because nothing puts user
        text in either today - and "today" is the whole reason to measure it
        rather than assume it. The sweep this file uses everywhere else reads
        the body alone, so this is the one place the rest of the request is
        looked at.
        """
        body = await sent_body([_live_control(armed)])
        request = wire_of(body)

        outside = f"{request.url}\n" + "\n".join(
            f"{k}\n{v}" for k, v in request.headers.items())
        assert not notebook.safeword_in(outside), (
            "the safeword was outside the body")
        # POSITIVE CONTROL: the sweep can see it when it IS there. Without
        # this, a request with no headers at all would satisfy the assertion.
        assert notebook.safeword_in(outside + "\n" + armed.upper())
        # GROUND: the request went where this file thinks it went.
        assert str(request.url).endswith("/chat/completions")

    @pytest.mark.anyio
    async def test_a_vault_that_will_not_open_stops_the_request(
            self, db, wire, monkeypatch) -> None:
        """UNKNOWN and UNSET are different states, and only one means "off".

        The word lives in the vault. If the vault will not open, the app does
        not know whether a safeword is armed, and a body sent while it does
        not know is a body that may be carrying one. Answering "no safeword"
        there would make the one control in this app that IS a control fail
        open at the moment the machine is least trustworthy - and it would do
        it silently, because a locked vault is a normal, expected state that
        happens every time the app idles.

        So the request is refused. Nothing was sent, nothing was billed, and
        `main.py` already turns this exception into the same 423 every other
        locked read produces.

        The key is stubbed on purpose. Without that the key read raises first
        and this test would pass against a filter that had been softened to
        guess, which is exactly the defect it exists to catch.
        """
        import openrouter
        import vault_state

        notebook.set_safeword("kırmızı")
        monkeypatch.setattr(openrouter, "get_secret", lambda name: "sk-test")
        try:
            vault_state.clear_key()

            with pytest.raises(vault_state.VaultLockedError):
                await openrouter.complete(
                    [{"role": "user", "content": f"{REMAINDER} KIRMIZI"}],
                    MODEL, {}, {"data_collection": "deny"})

            assert wire == [], "a request left the machine with the vault shut"
        finally:
            vault_state.set_key(TEST_VAULT_KEY)
            notebook.set_safeword("")

    @pytest.mark.anyio
    async def test_a_missing_api_key_is_still_answered_as_a_missing_api_key(
            self, db, wire, monkeypatch) -> None:
        """GROUND for the test above, and it pins an ORDER.

        The key and the safeword are now read on one trip to the vault. Read
        in the wrong order, somebody with no key configured and a locked vault
        would be told about a database when the thing they need to hear about
        is the key. The key is read first and short-circuits: nothing that
        cannot be sent is worth a second look inside the vault.
        """
        import openrouter
        import vault_state

        monkeypatch.setattr(openrouter, "get_secret", lambda name: None)
        try:
            vault_state.clear_key()

            with pytest.raises(openrouter.OpenRouterError) as exc:
                await openrouter.complete(
                    [{"role": "user", "content": CLEAN_LINE}],
                    MODEL, {}, {"data_collection": "deny"})

            assert exc.value.reason == "api_key_not_set"
            assert exc.value.reached_provider is False
            assert wire == []
        finally:
            vault_state.set_key(TEST_VAULT_KEY)

    @pytest.mark.anyio
    async def test_a_greek_sigma_does_not_cost_the_rest_of_the_sentence(
            self, db, sent_body) -> None:
        """Lowercasing has one context rule, and it is enough to lose a line.

        A Greek sigma at the END of a word lowercases to a different letter
        than one in the middle. A fold applied a piece at a time never sees
        the end of the word, so it produces one letter where the authority
        produces the other: the matcher says the word is there and the cut
        finds nothing. The value is then dropped rather than sent, which is
        the safe direction and the wrong outcome - the sentence around the
        word goes with it, and nothing on screen says why.
        """
        notebook.set_safeword("ΛΟΣ")
        try:
            typed = f"İΛΟΣ {REMAINDER}"
            body = await sent_body([{"role": "user", "content": typed}])

            assert not notebook.safeword_in(_body_of(body)), (
                "the safeword was in what went out")
            assert REMAINDER in body["messages"][0]["content"], (
                "the sentence was dropped rather than trimmed")
        finally:
            notebook.set_safeword("")

    @pytest.mark.anyio
    async def test_a_message_the_cut_empties_is_dropped(
            self, armed, sent_body) -> None:
        """An empty turn is not a smaller message, it is a different request.

        A user turn with nothing in it asks the model to answer nothing, and
        providers answer that or refuse it in ways that have nothing to do
        with what was written. There is nothing left of that line to send, so
        the line is not sent and the rest of the conversation keeps its shape.
        """
        body = await sent_body([
            {"role": "system", "content": "you are a test"},
            {"role": "user", "content": armed},
            {"role": "assistant", "content": "still here"},
        ])
        assert [m["content"] for m in body["messages"]] == [
            "you are a test", "still here"]

    @pytest.mark.anyio
    async def test_an_ordinary_body_goes_out_unchanged(
            self, armed, sent_body) -> None:
        """POSITIVE CONTROL. A filter that cut everything would pass every
        absence in this class and would be indistinguishable from a broken
        app."""
        messages = [{"role": "user", "content": CLEAN_LINE}]
        body = await sent_body([dict(m) for m in messages])
        assert body["messages"] == messages

    @pytest.mark.anyio
    async def test_nothing_is_touched_when_the_safeword_is_off(
            self, db, sent_body) -> None:
        """GROUND: unset must mean off here too, not cut-everything."""
        notebook.set_safeword("")
        messages = [{"role": "user",
                     "content": "kırmızı, and every other word"}]
        body = await sent_body([dict(m) for m in messages])
        assert body["messages"] == messages

    @pytest.mark.anyio
    async def test_the_streaming_twin_cuts_it_too(self, armed, wire) -> None:
        """Two payload builders, two sockets, one filter needed on each.

        The non-streaming half is what every other test in this class drives.
        This one is the route a person actually watches a reply arrive on, and
        a filter fitted to one of the two is a filter the other does not have.
        """
        import openrouter

        async for _chunk in openrouter.complete_stream(
                [{"role": "user", "content": f"no wait {armed.upper()} stop"}],
                MODEL, {}, {"data_collection": "deny"}):
            pass

        assert len(wire) == 1, (
            f"{len(wire)} requests left, so `wire[0]` is not the one this "
            f"test means")
        assert not notebook.safeword_in(_body_of(wire[0])), (
            "the safeword was in what went out")
        out = wire[0]["messages"][0]["content"]
        assert "no wait" in out and "stop" in out
