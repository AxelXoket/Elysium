"""A rejected request used to come back carrying what was in it.

FastAPI's default validation handler writes `jsonable_encoder(exc.errors())`
into the 422 body, and pydantic 2 attaches an `input` field to every error
item. For a MISSING field that `input` is the WHOLE BODY. So a request that
forgot one key was answered with the API key, the proxy URL, the message
text and whatever card or persona was in it, in the clear.

Not a hypothesis about pydantic: the repository already knew and had written
the knowledge down somewhere else. `routers/vault.py`'s `confirm` field
carries a `max_length` whose justification is that echoing THAT field back
in a 422 leaks nothing because it is a fixed public phrase. That argument
only needs making if echoing is what happens. `routers/notebook.py`'s
`SafewordBody` declines a `max_length` for the mirror-image reason: it
refused to hand pydantic a way to quote a safeword back over the wire.

`loc`, `type` and `msg` are what a caller needs in order to fix the request.
`input` is what they sent, which they already have.

WHY THIS FILE DRIVES NINE ROUTE GROUPS AND NOT ONE

The handler is installed on the application, not on a route, so a file that
only ever posts to `/settings` cannot tell an application-wide handler from
one narrowed to a single prefix. That was measured rather than imagined:
narrowing the handler to `/api/v1/settings` left vault, notebook,
completions, tts, chats, characters and personas answering with pydantic's
own body, and the previous version of this file stayed green through all of
it. Every route in the table below belongs to a router that narrowing would
strand, and the table itself is gated so a router added later cannot be
left out in silence.

WHY THE ERROR CODE IS NEVER SPELLED OUT HERE

It was, in three places, and a rename caught two of them. The name is now
READ from the response and then held to what the shared catalogue says about
it, so this file has no copy of the string to go stale. What pins it is not
a literal but four facts a wrong code cannot satisfy at once: it is a
string and not the old array, the catalogue declares it, that record pairs
it with 422 on the http channel, and that record does NOT carry a
`no_backend_producer` note - which is precisely what disqualifies the code
this handler used to send, now recorded as frontend-only.

WHY THERE IS A SECOND APPLICATION IN HERE

Every claim below is an ABSENCE: the secret is gone, `input` is gone. An
absence proves nothing on its own, because a route answering with an empty
body, or a sweep looking for the wrong string, would satisfy it while
leaking everything. So the same body models are mounted on a throwaway app
that keeps the framework default handler, driven with the same payloads, and
that app is asserted to LEAK. The sweep sees the secret when the secret is
there; when it reports the secret gone, the report means something.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from routers.characters import CharacterCreate
from routers.chats import ChatCreate
from routers.completions import CompleteRequest
from routers.notebook import EntryBody
from routers.personas import PersonaCreate
from routers.settings import ApiKeyBody
from routers.tts import ActiveBody
from routers.tts_runtime import VoiceModeBody
from routers.vault import RESET_CONFIRM_MAX_LEN, PassphraseBody, VaultResetBody

#: Shaped like the real thing, so a sweep over the response text looks for
#: what would actually ride out: the provider key is the worst single string
#: this application holds outside the vault.
_SECRET = "sk-or-v1-THIS-MUST-NEVER-COME-BACK"

#: The whole permitted vocabulary of an error item. Asserting the SET rather
#: than three named absences is what makes this survive pydantic growing a
#: fourth field that quotes the input under a name nobody here has heard of.
_ALLOWED_ITEM_KEYS = frozenset({"loc", "type", "msg"})

#: Loaded the same way `test_error_catalogue.py` loads it, and for the same
#: reason: this file is the shared declaration of the error vocabulary that
#: both halves of the application read.
_CATALOGUE = (Path(__file__).resolve().parents[2] / "shared"
              / "error_catalogue.json")
_RECORDS = {record["code"]: record for record in
            json.loads(_CATALOGUE.read_text(encoding="utf-8"))["codes"]}


#: (label, path, payload, the field the refusal must name).
#:
#: One row per ROUTER, chosen for reachability rather than for drama: every
#: one of these fails validation before its handler runs, so none of them
#: needs a chat, a model, an unlocked vault or a provider. The `1` in the
#: completions and notebook paths never has to exist, for the same reason.
#:
#: `/notebook/{chat_id}` rather than `/notebook/safeword`, measured: the
#: safeword body has no required field, so a payload with a wrong key is
#: accepted and answers 200. A row that cannot 422 proves nothing, which is
#: the trap `/settings/stop-sequences` set for the version of this file that
#: used it as a control - that route clamps instead of refusing, on purpose,
#: so a stale window cannot 422 a settings save.
_LEAKY_ROUTES = [
    pytest.param("settings", "/api/v1/settings/api-key",
                 {"wrong_key": _SECRET}, "api_key", id="settings"),
    pytest.param("vault", "/api/v1/vault/unlock",
                 {"wrong_key": _SECRET}, "passphrase", id="vault"),
    pytest.param("notebook", "/api/v1/notebook/1",
                 {"wrong_key": _SECRET}, "text", id="notebook"),
    pytest.param("completions", "/api/v1/chats/1/complete",
                 {"message": _SECRET}, "model_id", id="completions"),
    pytest.param("tts", "/api/v1/tts/active",
                 {"wrong_key": _SECRET}, "uid", id="tts"),
    pytest.param("chats", "/api/v1/chats",
                 {"wrong_key": _SECRET}, "character_id", id="chats"),
    pytest.param("personas", "/api/v1/personas",
                 {"wrong_key": _SECRET}, "display_name", id="personas"),
    pytest.param("characters", "/api/v1/characters",
                 {"wrong_key": _SECRET}, "name", id="characters"),
    pytest.param("tts_runtime", "/api/v1/tts/voice-mode",
                 {"wrong_key": _SECRET}, "enabled", id="tts_runtime"),
]

#: The one router group with a body route and no row, named here so its
#: absence is a decision on the page rather than a gap somebody has to
#: notice. `uploads` takes multipart file parts, so its refusal quotes a form
#: field and a filename rather than a JSON body - a different shape, driven
#: by test_attachments.py. `models` needs no entry at all: it mounts one GET
#: and accepts no body anywhere, so it never reaches this handler.
_TAGS_WITHOUT_A_ROW = frozenset({"uploads"})

#: The same rejections at the model layer, for the throwaway app below. The
#: models are IMPORTED from the routers rather than restated, so a field that
#: stops being required stops being a leak case here at the same moment.
_LEAKY_MODELS = [
    (ApiKeyBody, {"wrong_key": _SECRET}),
    (PassphraseBody, {"wrong_key": _SECRET}),
    (EntryBody, {"wrong_key": _SECRET}),
    (CompleteRequest, {"message": _SECRET}),
    (ActiveBody, {"wrong_key": _SECRET}),
    (ChatCreate, {"wrong_key": _SECRET}),
    (PersonaCreate, {"wrong_key": _SECRET}),
    (CharacterCreate, {"wrong_key": _SECRET}),
    (VoiceModeBody, {"wrong_key": _SECRET}),
]

#: One secret plus a full ceiling's worth of padding, computed from the
#: production constant so the payload cannot drift back into range if the
#: confirmation phrase is ever reworded.
_TOO_LONG_CONFIRM = _SECRET + "x" * RESET_CONFIRM_MAX_LEN


def _reached_the_handler(response, expected_field: str) -> list[dict]:
    """GROUND CONTROL, in one place because every test below needs it.

    Returns the error items, having first proved the request was actually
    refused by validation and that the refusal is about the field it was
    aimed at. Without this a route that quietly answered 200, or 404, or a
    422 with an empty list, would satisfy every absence assertion in this
    file by doing nothing at all.
    """
    assert response.status_code == 422, (
        f"expected a validation refusal, got {response.status_code}: "
        f"{response.text[:300]}")
    body = response.json()
    assert "errors" in body, (
        f"the refusal has no `errors` field, so it did not come from this "
        f"application's handler at all - the framework default answers with "
        f"the items under `detail`, and with the rejected body inside them: "
        f"{response.text[:300]}")
    errors = body["errors"]
    assert isinstance(errors, list) and errors, body
    located = {str(part) for item in errors for part in item["loc"]}
    assert expected_field in located, (
        f"the refusal never mentions {expected_field!r}, so this route is "
        f"failing for some other reason and proves nothing: {body}")
    return errors


def _code_on_the_wire(client) -> str:
    """The code the handler actually sends, read rather than remembered.

    Every assertion about the name goes through here, so there is exactly one
    place in this file that knows what the code is and it is not a literal.
    """
    response = client.post("/api/v1/settings/api-key", json={"nope": 1})
    assert response.status_code == 422, response.text
    code = response.json()["detail"]
    assert isinstance(code, str) and code, (
        f"`detail` is not a code at all: {code!r}. FastAPI's default puts the "
        f"error LIST here, which is the shape this handler exists to replace")
    return code


class TestNoRejectedBodyComesBack:
    @pytest.mark.parametrize("label,path,payload,field", _LEAKY_ROUTES)
    def test_no_router_echoes_the_rejected_body(
            self, client, label, path, payload, field) -> None:
        """The rule, once per router, and the reason there are seven rows.

        Each payload carries the secret in the position that makes pydantic
        quote the most: a missing required field, where `input` is the entire
        body rather than one value. The completions row is the worst of them,
        because the string it carries is the message a person typed.
        """
        r = client.post(path, json=payload)
        _reached_the_handler(r, field)

        assert _SECRET not in r.text, (
            f"{label} handed the rejected body back; anything that logs, "
            f"caches or displays a failed response now has the secret")

    def test_the_vault_passphrase_route_in_particular(self, client) -> None:
        """Named on its own, deliberately, and not left to the table.

        This file's opening argument is built on `routers/vault.py`, so the
        vault is the one router whose coverage must not be able to disappear
        in a table edit nobody reads. A missing `passphrase` is the pydantic
        case where `input` is the whole body, so the string that opens the
        encrypted database is what gets quoted back.
        """
        r = client.post("/api/v1/vault/unlock", json={"wrong_key": _SECRET})
        errors = _reached_the_handler(r, "passphrase")

        assert _SECRET not in r.text, (
            "the passphrase route quoted the rejected body back")
        assert any(item["type"] == "missing" for item in errors), errors

    @pytest.mark.parametrize("label,path,payload,field", _LEAKY_ROUTES)
    def test_every_error_item_carries_only_loc_type_and_msg(
            self, client, label, path, payload, field) -> None:
        """Stated over the SHAPE rather than over one secret.

        A sweep for a known string catches the secret this file planted. It
        does not catch the proxy URL, the persona, or whatever the next body
        carries, and it does not catch a value quoted back under a key that
        did not exist when this was written. An allow-list does.

        `input` is the sender's own bytes. `ctx` can carry the same value a
        second time inside a constraint message. `url` is pydantic's
        documentation link, which the framework already drops on its way in
        (`fastapi/_compat/v2.py` asks for errors with `include_url=False`) -
        it is covered here so that a framework upgrade which stops doing that
        arrives as a red line rather than as a wider body nobody notices.
        """
        r = client.post(path, json=payload)

        for item in _reached_the_handler(r, field):
            # The three by name first, so a failure says which one arrived,
            # then the allow-list for everything nobody has thought of yet.
            assert "input" not in item, (
                f"{label} quoted the sender's own bytes back: {item}")
            assert "ctx" not in item, (
                f"{label} answered with a constraint context, which is the "
                f"second place a rejected value can ride out: {item}")
            assert "url" not in item, (
                f"{label} answered with pydantic's documentation link: "
                f"{item}")
            assert set(item) == _ALLOWED_ITEM_KEYS, (
                f"{label} answered with an error item carrying "
                f"{sorted(set(item) - _ALLOWED_ITEM_KEYS)}: {item}")
            assert item["msg"], f"{label} lost the human-readable reason"

    @pytest.mark.parametrize("label,path,payload,field", _LEAKY_ROUTES)
    def test_the_same_code_answers_on_every_router(
            self, client, label, path, payload, field) -> None:
        """The shape is a contract, and it changed on purpose.

        `detail` used to be the error ARRAY, so the frontend read the shape
        and invented a code from it - which is why the catalogue recorded
        that invented code as having no backend producer, and why the error
        census could not resolve this site. `detail` now carries a catalogued
        name like every other route, and the items ride in `errors`.
        `normalizeDetail` returns a string `detail` unchanged, so the reader
        sees the same sentence by a shorter road.

        The name is route-NEUTRAL, and asserting one code across seven
        routers is what makes that word mean something: a single
        application-level handler answers every rejected body in the tree and
        cannot know whether it is looking at generation parameters, a
        passphrase or a note. It is also the assertion that catches a handler
        narrowed to one prefix, because a stranded router answers with the
        framework's array instead.
        """
        r = client.post(path, json=payload)
        _reached_the_handler(r, field)

        assert r.json()["detail"] == _code_on_the_wire(client), (
            f"{label} answers with a different code from the rest of the "
            f"tree: {r.text[:300]}")

    def test_the_code_is_one_the_catalogue_declares_the_backend_sends(
            self, client) -> None:
        """The name is pinned without being retyped.

        Reading the code off the wire keeps this file from going stale on a
        rename, but read alone it would accept anything, including the empty
        habit of sending whatever the handler happens to hold. So the code is
        held to the shared catalogue, which is where the frontend gets the
        sentence it shows a reader.

        The last assertion is the sharp one. A record carrying
        `no_backend_producer` is a written statement that the backend never
        sends this code - that is exactly what the catalogue now says about
        the code this handler used to send. A regression to it would satisfy
        every other line here and fail this one.
        """
        code = _code_on_the_wire(client)

        record = _RECORDS.get(code)
        assert record is not None, (
            f"{code!r} goes over the wire and the catalogue has no record of "
            f"it, so the frontend has no sentence to show for it")
        assert 422 in record["statuses"], record
        assert "http" in record["channels"], record
        assert "no_backend_producer" not in record, (
            f"the catalogue states that {code!r} has no backend producer, "
            f"and the backend just produced it: "
            f"{record['no_backend_producer']}")

    def test_the_body_is_json_the_frontend_can_read(self, client) -> None:
        """The contract as the client actually parses it.

        `response.json()` would hide a body that is only accidentally
        JSON-shaped, so this one goes through the raw text.
        """
        r = client.post("/api/v1/settings/api-key", json={"nope": 1})

        body = json.loads(r.text)
        assert body["detail"] == _code_on_the_wire(client)
        assert isinstance(body["errors"], list) and body["errors"]

    def test_the_table_covers_every_router_that_takes_a_body(self) -> None:
        """The coverage itself has to be a gate, or it decays.

        These rows were once one row, and nothing failed while eight route
        groups went uncovered: the narrowing stayed green through all of it. A
        table nobody checks grows stale the same way: a router added next
        month accepts a body, leaks it, and every test above passes because
        no row points at it.

        So the live application is asked which route groups accept a request
        body at all, and each one must either have a row here or be named as
        deliberately absent. `app.routes` is the running route table, not a
        reading of the source: a route that is defined but never mounted does
        not appear, and one mounted from somewhere unexpected does.
        """
        import main

        with_bodies: set[str] = set()
        for route in main.app.routes:
            methods = getattr(route, "methods", None) or set()
            if getattr(route, "body_field", None) is None:
                continue
            if not methods & {"POST", "PATCH", "PUT"}:
                continue
            with_bodies.update(getattr(route, "tags", None) or [])

        assert with_bodies, (
            "no mounted route was found to take a body at all, so this gate "
            "is asserting nothing and the table below it is unguarded")

        covered = {param.values[1].split("/")[3] for param in _LEAKY_ROUTES}
        missing = with_bodies - covered - _TAGS_WITHOUT_A_ROW
        assert not missing, (
            f"{sorted(missing)} accept a request body and no row drives "
            f"them, so a handler narrowed away from them would not be "
            f"noticed here")

    def test_a_valid_request_is_untouched(self, client) -> None:
        """GROUND CONTROL for the handler's reach. An exception handler that
        fired on everything would satisfy every assertion above and break
        the application."""
        r = client.get("/api/v1/settings")
        assert r.status_code == 200, r.text


class TestTheSweepCanSeeWhatItClaimsIsGone:
    """POSITIVE CONTROLS. Every assertion above is an absence; these are the
    proof that the absence was ever a presence."""

    @staticmethod
    def _default_handler_app(model) -> TestClient:
        """One production body model, the framework's own validation handler.

        This is what this application answered with before the handler in
        `main.py` existed, reconstructed from a production model rather than
        from a remembered description of pydantic's output. The app has no
        routers, no database and no lifespan, so nothing here can reach the
        real one.

        The signature is built rather than written, because this module runs
        under `from __future__ import annotations`: a written annotation
        would arrive at the framework as the string "model" and resolve to
        nothing. An `inspect.Signature` carries the class itself.
        """
        app = FastAPI()

        def endpoint(body):
            return {}

        endpoint.__signature__ = inspect.Signature([
            inspect.Parameter("body", inspect.Parameter.POSITIONAL_OR_KEYWORD,
                              annotation=model),
        ])
        app.post("/")(endpoint)
        return TestClient(app)

    @pytest.mark.parametrize("model,payload", _LEAKY_MODELS,
                             ids=lambda value: getattr(value, "__name__", ""))
    def test_the_default_handler_hands_the_whole_body_back(
            self, model, payload) -> None:
        """Without this the file is a set of assertions about nothing.

        If the framework default did NOT leak, every "the secret is gone"
        line above would be green on a machine where the secret was never
        capable of arriving - and would stay green the day the handler is
        deleted. So the leak is demonstrated, per model, on an app that
        differs from the real one in exactly one thing: the handler.
        """
        r = self._default_handler_app(model).post("/", json=payload)

        assert r.status_code == 422, r.text
        assert _SECRET in r.text, (
            f"{model.__name__} did not quote the rejected body back even "
            f"under the default handler, so this control is not controlling "
            f"anything and the absences above prove nothing")
        items = r.json()["detail"]
        assert any("input" in item for item in items), items

    def test_a_constraint_message_is_stripped_of_the_value_it_quotes(
            self, client) -> None:
        """POSITIVE CONTROL for `ctx`, over a field that really produces one.

        A `missing` error has no `ctx` at all, so asserting `ctx` is absent
        from a missing-field response is satisfied by a key that was never
        going to be there. A length violation does produce one, and pydantic
        puts the ceiling inside it - which is how a length-limited secret
        leaks a second time even after `input` is gone.

        Proved present at the pydantic layer first, over the same model and
        the same payload, so the sweep afterwards is subtracting something
        that exists rather than confirming a coincidence.
        """
        with pytest.raises(ValidationError) as raised:
            VaultResetBody.model_validate({"confirm": _TOO_LONG_CONFIRM})
        raw = raised.value.errors()
        assert [item["type"] for item in raw] == ["string_too_long"], raw
        assert raw[0]["ctx"] == {"max_length": RESET_CONFIRM_MAX_LEN}, raw[0]
        assert raw[0]["input"] == _TOO_LONG_CONFIRM, (
            "the raw error stopped quoting the value, so this control no "
            "longer shows what the handler has to remove")

        r = client.post("/api/v1/vault/reset",
                        json={"confirm": _TOO_LONG_CONFIRM})
        errors = _reached_the_handler(r, "confirm")

        assert [item["type"] for item in errors] == ["string_too_long"], errors
        for item in errors:
            assert set(item) == _ALLOWED_ITEM_KEYS, item
        assert _SECRET not in r.text
        assert str(RESET_CONFIRM_MAX_LEN) in errors[0]["msg"], (
            "the ceiling has to survive in the sentence; a caller who cannot "
            "read the limit cannot fix the request")

    def test_the_documentation_link_is_present_before_it_is_dropped(
            self) -> None:
        """POSITIVE CONTROL for `url`, and an honest one.

        `url` never reaches the handler: the framework asks pydantic for
        errors with `include_url=False`. So its absence over the wire is
        currently held up by the framework and not by `main.py`, and this
        says so rather than letting the allow-list take credit for a
        subtraction it did not make. What it pins is that `url` is a real key
        on a real error here, so the allow-list stands over something and
        would catch it the day that default changes.
        """
        with pytest.raises(ValidationError) as raised:
            PassphraseBody.model_validate({"wrong_key": _SECRET})
        raw = raised.value.errors()

        assert raw, "the model stopped refusing this payload"
        assert all("url" in item for item in raw), raw
        assert all(item["url"].startswith("https://errors.pydantic.dev/")
                   for item in raw), raw
