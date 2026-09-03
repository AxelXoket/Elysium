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
typed, so the sentence above had to be narrowed to say what these tests
actually cover. The last class in this file is the measurement of what the
safeword reaches there and what it does not.
"""
from __future__ import annotations

import inspect
import json
import unicodedata
from collections.abc import Iterator

import pytest

import config
import notebook_store as notebook
import notebook_worker
from database import get_db, set_setting

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
# That is not tidiness. The invariant test at the end carries a strict
# expected-failure limited to AssertionError, and `make_chat` and
# `make_character` assert on their status codes, so a route that grew a
# required field would raise AssertionError while building the fixture, be
# counted as the expected failure, and report a green suite for a test that
# had sent nothing at all.
#
# A fixture is NOT enough on its own, which is the part that is easy to get
# wrong: pytest evaluates an xfail marker against the SETUP report as well as
# the call one. Measured, by breaking `make_chat` on purpose and watching the
# test report XFAIL rather than an error. Only the translation closes it, and
# with the translation in place the same break reports two errors.
#
# TEARDOWN is the one phase this cannot cover, and it is measured rather than
# assumed: an AssertionError raised while a fixture tears down is absorbed by
# the marker in the same way, even when the body passed. Nothing in the chain
# used here asserts on the way out, so the funnel holds today, and a teardown
# that grew one would have to be translated the same way.

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


def _tainted(word: str) -> str:
    """The word as somebody in a hurry would actually have typed it.

    In CAPITALS, and that is load-bearing rather than colour. `safeword_in`
    folds case the Turkish way; a raw substring does not. Stored verbatim, a
    leak check written with `in` would have found the word just as well and
    the choice of matcher further down would have been decoration that could
    not be made to fail. Typed this way, only the real matcher sees it.
    """
    return f"{word.upper()} stop the scene"


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
    identical request. The expected failure below is meant to be DELETED when
    the scope question is answered, and the check that outlives it has to see
    the whole call, so this flattens every argument the call was made with.
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


def _always_sent() -> set[str]:
    """The arguments `openrouter.complete` cannot be called without.

    Read off the real function rather than listed here, so the width of the
    capture below is checked against the code it stands in for instead of
    against a copy of its parameter list that goes stale in silence. Only the
    arguments with no default: the optional ones arrive through `**kw` and are
    present only on the calls that pass them.

    Call this BEFORE `_capture` installs the stand-in, or it reads the
    stand-in's own signature back to itself and proves nothing.
    """
    import openrouter

    return {name for name, p in
            inspect.signature(openrouter.complete).parameters.items()
            if p.default is inspect.Parameter.empty
            and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)}


def _capture(monkeypatch) -> list[dict]:
    """What the note reader actually hands the provider, ALL of it.

    The `provider` fixture cannot see this path - it patches the completion
    router's own reference - so the capture goes on `openrouter.complete`
    itself, which is where every other test of this worker puts it.

    Every argument is recorded, `**kw` included. Keeping `messages` and
    `model_id` alone put the blind spot in the HARNESS rather than in the
    assertion, and no amount of widening an assertion reaches something the
    harness dropped at the door: `gen_params`, `provider` and the optional
    payload switches all leave the machine on the same request.
    """
    import openrouter

    sent: list[dict] = []

    async def _complete(messages, model_id, gen_params, provider, **kw):
        sent.append({"messages": messages, "model_id": model_id,
                     "gen_params": gen_params, "provider": provider, **kw})
        return {"id": "gen-1",
                "choices": [{"finish_reason": "stop",
                             "message": {"content":
                                         json.dumps({"facts": []})}}],
                "usage": {}}

    monkeypatch.setattr(openrouter, "complete", _complete)
    return sent


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

    The safeword is matched in one place, against the message going out right
    now. The note reader wakes later, reads message bodies straight out of the
    database and builds a prompt of its own, so a safeword armed today has
    nothing to say about what was stored yesterday.

    Whether it should is an open question with three answers, recorded as H-20
    in the audit queue, and it is not settled in this file. What IS settled
    here is the measurement: the first two tests hold under every one of those
    answers and are ordinary green tests, and they are the only two here that
    stand as proofs. The third carries the invariant as a tripwire rather than
    as evidence, for the reason written in its own docstring.
    """

    @pytest.mark.anyio
    async def test_an_ordinary_stored_chat_is_read_and_sent(
            self, clean_chat, monkeypatch) -> None:
        """GROUND, and the one that refuses the cheap answer.

        "The safeword is not in the body" is satisfied by a note reader that
        stopped sending anything at all, and by one that was never asked to
        send in the first place. This says the path is live WHILE the safeword
        is armed: the configured model was called, the window reached the
        oldest line, the stored text was in it, and the run completed rather
        than merely being attempted. A fix that widens into "send nothing"
        fails here instead of passing quietly next door.
        """
        sent = _capture(monkeypatch)

        w = await _read_the_chat(clean_chat)

        assert sent, "nothing was sent, so nothing in this class measures anything"
        assert sent[0]["model_id"] == EXTRACT_MODEL
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
            self, clean_chat, armed, monkeypatch) -> None:
        """POSITIVE CONTROL for the absence the test below asserts.

        A claim that the safeword is nowhere in what went out is worth exactly
        what the sweep behind it is worth, and this one was worth `messages`
        and nothing else. Measured: with the transcript moved into
        `gen_params` the check reported a clean payload while the same text
        left on the same request, and it reported it in green. The expected
        failure below is meant to be DELETED the day the scope question is
        answered, so the sweep that outlives it has to be the wide one.

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

        Independent of the scope question, and deliberately so: this reads a
        chat with no safeword in it, so it is green under all three answers
        and stays a proof rather than turning into a second tripwire.
        """
        # BEFORE the stand-in is installed, or it reads the stand-in.
        required = _always_sent()

        sent = _capture(monkeypatch)
        await _read_the_chat(clean_chat)

        assert sent, "nothing was sent, so there is no payload to control on"
        call = sent[0]

        # GROUND. The unplanted payload is clean, so every match below is the
        # planted word rather than something the reader always sends anyway.
        assert not notebook.safeword_in(_body_of(call))

        assert required <= set(call), (
            f"the capture dropped {sorted(required - set(call))} at the door, "
            f"so no assertion downstream can see what went out in it")

        planted = f"user: {_tainted(armed)}"
        for slot in call:
            reshaped = dict(call)
            reshaped[slot] = planted
            assert notebook.safeword_in(_body_of(reshaped)), (
                f"the sweep is blind to the `{slot}` slot")

        # And one level down, which is the exact reshaping that was measured:
        # the transcript out of `messages` and in under a generation
        # parameter, with the message list left behind as empty scaffolding.
        nested = dict(call)
        nested["messages"] = [{"role": m["role"], "content": ""}
                              for m in call["messages"]]
        nested["gen_params"] = dict(call["gen_params"], transcript=planted)
        assert notebook.safeword_in(_body_of(nested)), (
            "the sweep is blind to text one level inside a slot")

    @pytest.mark.anyio
    @pytest.mark.xfail(
        strict=True, raises=AssertionError,
        reason="H-20. The scope of the safeword on this path is an open "
               "question with three answers: filter the matching lines out of "
               "the range, skip the range entirely, or write the narrower "
               "scope down and leave the behaviour. Under either of the first "
               "two this test passes, and a strict expected-failure turns "
               "that pass into a red result, so the marker cannot outlive the "
               "change. Under the third the test goes with the decision.")
    async def test_a_stored_line_carrying_the_safeword_is_not_sent(
            self, tainted_chat, monkeypatch) -> None:
        """A TRIPWIRE, not a proof. It carries the invariant and nothing else.

        `_contract_gate.py:353` states this repository's position and
        `TestAnExpectedFailureIsNotAProof` enforces it: an expected failure is
        not evidence, whichever way it lands, because its own author declared
        it broken. So this test proves nothing about today and does not try
        to. Today's behaviour was measured by running it with `--runxfail`,
        which reported `the safeword was in what went out`, and that recorded
        run is the evidence. The two tests above are the ones that stand as
        proofs, and they are ordinary green tests for exactly that reason.

        What this earns its place for is the other direction. The moment the
        scope question is answered by code, this starts passing, strict turns
        the pass into a red result, and the marker cannot be left behind. A
        comment would not have done that.

        A stored message body carrying the safeword, and the note reader
        reading the range on its own timer with the user's own API key.
        Everything it hands the provider is read back. When the row was
        written relative to the safeword being set is not part of the setup
        and is not claimed to be: the reader looks at stored rows, never at
        when they arrived.

        NO CALL AT ALL satisfies this, because skipping the range is one of
        the three answers and sending nothing for it is the whole point. That
        is also what a dead harness looks like, which is why the ground
        control above is a SEPARATE test rather than a first leg of this one:
        a harness that stopped sending turns that test red, and this one
        cannot then be misread as the invariant having landed.

        The match is `safeword_in`, not a substring. This file already carries
        the story of a control that missed on capitals, and a leak check that
        re-implemented the match with `in` would call a body clean while the
        word sat in it in a spelling the real matcher folds.
        """
        sent = _capture(monkeypatch)

        try:
            await _read_the_chat(tainted_chat)
        except AssertionError as exc:
            # Same reason as the fixture: the reader carries asserts of its
            # own, and one of them firing must not arrive at the marker above
            # looking like the measurement having been taken.
            raise RuntimeError(
                f"the reader raised before anything was measured: {exc}") from exc

        if sent and GREETING not in _body_of(sent[0]):
            # NOT an assertion. The marker above accepts an AssertionError and
            # nothing else, so a window that no longer reaches the line under
            # test has to fail loudly rather than be counted as the expected
            # failure, or later as the change having landed.
            raise RuntimeError(
                "the range this run read does not cover the line under test")

        leaked = [call for call in sent
                  if notebook.safeword_in(_body_of(call))]
        assert not leaked, "the safeword was in what went out"
