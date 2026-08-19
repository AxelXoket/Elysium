"""The promises README makes, proved where they were only asserted in prose.

Every claim here was already TRUE. None of them was tested, which is a
different property: a claim nothing checks is a claim that survives the commit
that breaks it. The inventory that produced this file found the loudest three
promises in the whole document - zdr, data_collection, allow_fallbacks, each
marked "Never overridable" - covered by a regex in verify/ that greps config.py
for the literal text. That proves the constant is spelled correctly. It cannot
notice a router that stopped reading the constant.

So these are end-to-end where the promise is end-to-end: a request goes in at
the HTTP boundary carrying exactly the override the README says is impossible,
and the assertion is on the bytes leaving for the provider.
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path

import pytest

import network_client
import openrouter
from tests.conftest import make_chat, make_character, make_persona
from tests.egress_guard import EgressAttempt


def _capture_wire(monkeypatch) -> list[dict]:
    """Record the JSON body openrouter.py hands to httpx, and answer it.

    The seam is get_client, not httpx itself: everything above it - the route,
    the policy build, the parameter filter - runs for real, so what lands in
    the list is what would have gone out on the socket.
    """
    bodies: list[dict] = []

    class _Response:
        status_code = 200
        is_success = True

        def json(self) -> dict:
            return {"choices": [{"message": {"content": "ok"}}]}

        @property
        def text(self) -> str:
            return json.dumps(self.json())

    class _Client:
        async def post(self, url, headers=None, json=None, timeout=None):
            bodies.append(json)
            return _Response()

    monkeypatch.setattr(openrouter, "get_client", lambda: _Client())
    monkeypatch.setattr(openrouter, "get_secret", lambda name: "sk-test")
    return bodies


def _send(client, monkeypatch, **extra) -> list[dict]:
    """Drive one real completion request and return the wire bodies."""
    bodies = _capture_wire(monkeypatch)
    chat_id = make_chat(client, make_character(client))
    body = {"message": "hello", "model_id": "test/model-1", **extra}
    response = client.post(f"/api/v1/chats/{chat_id}/complete", json=body)
    assert response.status_code == 200, response.text
    assert bodies, "the request never reached the provider layer"
    return bodies


class TestTheLockedProviderPolicy:
    """"Never overridable" - stated three times in the README, tested nowhere.

    extra="ignore" on ProviderPolicy is what makes it true, and extra="ignore"
    is one word away from extra="allow". Nothing failed if it changed.
    """

    def test_a_client_cannot_turn_zero_data_retention_off(
        self, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bodies = _send(client, monkeypatch, provider={"zdr": False})
        assert bodies[0]["provider"]["zdr"] is True

    def test_a_client_cannot_opt_into_data_collection(
        self, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bodies = _send(client, monkeypatch,
                       provider={"data_collection": "allow"})
        assert bodies[0]["provider"]["data_collection"] == "deny"

    def test_a_client_cannot_re_enable_fallbacks(
        self, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The one with teeth. A fallback sends the conversation to a provider
        # the locked policy was never applied to.
        bodies = _send(client, monkeypatch,
                       provider={"allow_fallbacks": True})
        assert bodies[0]["provider"]["allow_fallbacks"] is False

    def test_all_three_survive_being_overridden_at_once(
        self, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bodies = _send(client, monkeypatch, provider={
            "zdr": False, "data_collection": "allow", "allow_fallbacks": True,
        })
        assert bodies[0]["provider"] == {
            **bodies[0]["provider"],
            "zdr": True, "data_collection": "deny", "allow_fallbacks": False,
        }

    def test_the_one_field_that_may_be_set_still_is(
        self, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Without this the whole class would pass on a router that ignored the
        # provider block entirely, which is a different bug wearing the same
        # green tick.
        bodies = _send(client, monkeypatch,
                       provider={"require_parameters": True})
        assert bodies[0]["provider"]["require_parameters"] is True


class TestWhatIsNeverSent:
    """Field names the README promises never leave this machine."""

    @pytest.mark.parametrize("field, value", [
        ("tools", [{"type": "function",
                    "function": {"name": "exfiltrate", "parameters": {}}}]),
        ("tool_choice", "auto"),
        ("response_format", {"type": "json_object"}),
        ("raw_json", {"anything": "at all"}),
        ("avatar_path", "C:/Users/someone/secret.png"),
    ])
    def test_the_field_does_not_reach_the_wire(
        self, client, monkeypatch: pytest.MonkeyPatch, field: str, value
    ) -> None:
        bodies = _send(client, monkeypatch, **{field: value})
        assert field not in bodies[0]

    def test_response_format_is_sent_by_the_extractor_and_nowhere_else(
        self,
    ) -> None:
        """The amended claim, proved by counting rather than by reading.

        Until v1.2 nothing in the app sent `response_format` and the README
        said so flatly. The notebook's extractor now does - it is the reason a
        cheap model returns a schema at all - and the sentence was left
        standing, certified by a test that only ever drove the chat route. A
        per-route promise checked on one route will keep certifying claims it
        does not visit, so this one asks the whole tree.

        What must stay true is narrower and is what the README now says: no
        request carrying a CONVERSATION to a chat model carries the field, and
        nothing the frontend sends can add it. The two tests around this one
        hold the second half; this holds the first.
        """
        import ast
        import pathlib

        root = pathlib.Path(__file__).resolve().parent.parent
        senders = []
        for path in root.rglob("*.py"):
            if ".venv" in path.parts or path.parts[-2:][0] == "tests":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                for kw in node.keywords:
                    if kw.arg == "response_format":
                        senders.append(path.relative_to(root).as_posix())
        assert sorted(set(senders)) == ["routers/notebook.py"], senders

    def test_the_context_budget_is_an_app_side_number_only(
        self, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # It IS honoured - it trims history here - but forwarding it would tell
        # the provider how much of the conversation exists beyond what it was
        # shown.
        bodies = _send(client, monkeypatch, context_budget_tokens=4096)
        assert "context_budget_tokens" not in bodies[0]
        # The control. "not in" is also true of an empty dict, and this file is
        # full of absence assertions: if the capture ever stopped seeing the
        # real payload, every one of them would go quietly green together.
        # Named fields rather than a length, so that a body which lost its
        # messages is caught too.
        assert bodies[0]["model"] and bodies[0]["messages"]

    def test_a_smuggled_field_cannot_ride_in_on_generation_params(
        self, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # gen_params is spread into the payload with **, so anything that
        # survives validation is sent verbatim. This is the path that would
        # carry a banned field if the model ever loosened.
        bodies = _send(client, monkeypatch, generation_params={
            "temperature": 0.5, "tools": [], "response_format": {"type": "x"},
        })
        assert "tools" not in bodies[0]
        assert "response_format" not in bodies[0]
        # The control: a legitimate parameter in the SAME dict does arrive, so
        # this cannot pass by dropping generation_params wholesale.
        assert bodies[0]["temperature"] == 0.5

    def test_the_context_length_cannot_be_overridden_from_outside(
        self, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # P-01, which used to be a grep for the string `context_length_override`
        # in backend source. That proves nobody spelled the name; it cannot see
        # a route that started honouring an equivalent field under another one.
        # How much of the conversation the provider is shown is this app's
        # decision, and the wire is where that is decided or not.
        bodies = _send(client, monkeypatch, context_length_override=999_999)
        assert "context_length_override" not in bodies[0]
        assert bodies[0]["model"] and bodies[0]["messages"]

    def test_it_cannot_ride_in_on_generation_params_either(
        self, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bodies = _send(client, monkeypatch, generation_params={
            "temperature": 0.5, "context_length_override": 999_999,
        })
        assert "context_length_override" not in bodies[0]
        assert bodies[0]["temperature"] == 0.5


    def test_a_persona_you_did_not_pick_is_not_in_the_payload(
        self, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """P-20, which was a grep for `SELECT * FROM personas`.

        That pattern only ever knew one way of writing the mistake. The thing
        the README actually promises is that a persona you are not using is
        not described to the provider, and there are as many ways to break
        that as there are ways to write a query.
        """
        make_persona(client, display_name="Nova",
                     description="tends the orchard at dusk", select=True)
        make_persona(client, display_name="Kestrel",
                     description="hunts the dry riverbed alone", select=False)

        bodies = _send(client, monkeypatch)
        wire = json.dumps(bodies[0])
        # The control first: the selected one has to be there, or the absence
        # below is just an empty prompt.
        assert "tends the orchard at dusk" in wire
        assert "hunts the dry riverbed alone" not in wire
        assert "Kestrel" not in wire


class TestNothingAReaderWroteReachesTheLog:
    """P-17 and P-18, which were greps for `logger.*content=` in source.

    A grep for the shape of a logging call cannot see the one that formats its
    argument first, or names the variable something else, or logs a dict that
    happens to contain the text. So this runs a real completion carrying words
    nothing else in the app would ever produce, and reads the log back.

    DEBUG, deliberately: the app does not ship at DEBUG, but a developer turns
    it on to look at exactly this path, and that is when a leak gets pasted
    into an issue.
    """

    #: Nonsense on purpose. A common word would be found in library output and
    #: prove nothing about ours.
    SECRET_SENTENCE = "zorbleflax the quivering marmalade"
    SECRET_PERSONA = "quenlithe of the hollow spindle"

    def test_what_the_user_typed_is_not_in_the_log(
        self, client, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        bodies = _capture_wire(monkeypatch)
        chat_id = make_chat(client, make_character(client))
        with caplog.at_level(logging.DEBUG):
            response = client.post(
                f"/api/v1/chats/{chat_id}/complete",
                json={"message": self.SECRET_SENTENCE,
                      "model_id": "test/model-1"},
            )
        assert response.status_code == 200, response.text
        # The control, and this file needs it more than most: an assertion that
        # a string is absent from an empty log passes for the wrong reason.
        # So the sentence must be proved to have gone somewhere first.
        assert self.SECRET_SENTENCE in json.dumps(bodies[0]), (
            "the message never reached the provider layer, so its absence "
            "from the log says nothing")
        assert self.SECRET_SENTENCE not in caplog.text

    def test_the_character_a_reader_wrote_is_not_in_the_log(
        self, client, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        bodies = _capture_wire(monkeypatch)
        created = client.post("/api/v1/characters", json={
            "name": "Testchar",
            "description": self.SECRET_PERSONA,
            "first_mes": "Hello there!",
        })
        assert created.status_code == 201, created.text
        chat_id = make_chat(client, created.json()["id"])
        with caplog.at_level(logging.DEBUG):
            response = client.post(
                f"/api/v1/chats/{chat_id}/complete",
                json={"message": "hello", "model_id": "test/model-1"},
            )
        assert response.status_code == 200, response.text
        assert self.SECRET_PERSONA in json.dumps(bodies[0]), (
            "the description never reached the prompt, so its absence from "
            "the log says nothing")
        assert self.SECRET_PERSONA not in caplog.text

    def test_the_log_was_actually_recording(
        self, client, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The floor under both tests above.

        caplog with no handler attached, a logger disabled somewhere in
        conftest, a level that filters everything: any of them makes `not in
        caplog.text` true forever. So one line is put through the same
        capture and required to arrive.
        """
        with caplog.at_level(logging.DEBUG):
            logging.getLogger("elysium.test").debug("marmalade check")
        assert "marmalade check" in caplog.text


class TestTheApiKeyIsNeverHandedBack:
    """"never returned by any endpoint" - a claim about ALL of them."""

    KEY = "sk-or-v1-privacypromisesentinel0000000000000000000000000000000000"

    def _store(self, client) -> None:
        import secrets_service
        from config import SECRET_API_KEY
        secrets_service.set_secret(SECRET_API_KEY, self.KEY)

    def test_settings_reports_presence_and_nothing_else(self, client) -> None:
        self._store(client)
        body = client.get("/api/v1/settings").json()
        assert body["api_key_set"] is True
        assert self.KEY not in json.dumps(body)

    def test_no_readable_endpoint_returns_it(self, client) -> None:
        # The promise is about every endpoint, so the test walks every one it
        # can call without inventing an id: a new GET route that happened to
        # serialise the secrets table fails here on the day it is added.
        self._store(client)
        from main import app

        checked = 0
        for route in app.routes:
            path = getattr(route, "path", "")
            methods = getattr(route, "methods", set()) or set()
            if "GET" not in methods or "{" in path:
                continue
            response = client.get(path)
            checked += 1
            assert self.KEY not in response.text, f"{path} returned the key"
        assert checked > 5, "the route sweep found almost nothing to check"

    def test_storing_and_reading_it_writes_nothing_to_the_log(
        self, client, caplog: pytest.LogCaptureFixture
    ) -> None:
        import secrets_service
        from config import SECRET_API_KEY
        with caplog.at_level(logging.DEBUG):
            secrets_service.set_secret(SECRET_API_KEY, self.KEY)
            secrets_service.get_secret(SECRET_API_KEY)
            client.get("/api/v1/settings")
        assert self.KEY not in caplog.text


class TestTheHttpClientIgnoresTheAmbientEnvironment:
    """trust_env=False, stated in two places in the README, tested in none.

    A user with HTTPS_PROXY exported - a corporate machine, a debugging
    session, mitmproxy left on - would otherwise have every request to the
    provider routed through that host, carrying the conversation, while the app
    still described itself as talking to one host directly.
    """

    @pytest.mark.anyio
    async def test_an_exported_proxy_does_not_capture_traffic(
        self, anyio_backend, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HTTPS_PROXY", "http://ambient-proxy.invalid:9")
        monkeypatch.setenv("HTTP_PROXY", "http://ambient-proxy.invalid:9")
        monkeypatch.setenv("ALL_PROXY", "http://ambient-proxy.invalid:9")

        built = network_client._build_client()
        try:
            with pytest.raises(EgressAttempt) as caught:
                await built.get("https://openrouter.ai/api/v1/models")
        finally:
            await built.aclose()

        # The suite-wide guard names whichever host was actually dialled, so
        # the assertion reads the destination rather than an httpx internal.
        assert "openrouter.ai" in str(caught.value)
        assert "ambient-proxy" not in str(caught.value)

    @pytest.mark.anyio
    async def test_the_configured_proxy_is_still_used(
        self, anyio_backend, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Same shape as the require_parameters test above: proving a setting is
        # ignored is only half of it if the deliberate one is ignored too.
        monkeypatch.setattr(network_client, "get_secret",
                            lambda name: "http://chosen-proxy.invalid:8080")
        built = network_client._build_client()
        try:
            with pytest.raises(EgressAttempt) as caught:
                await built.get("https://openrouter.ai/api/v1/models")
        finally:
            await built.aclose()
        assert "chosen-proxy.invalid" in str(caught.value)


class TestTheVoiceEngineGetsNoCredentialsAndNoWayHome:
    """The TTS worker runs third-party engine code. The deal is that it is LOCAL.

    _ENV_STRIP and _ENV_FORCE are what make that true, and neither name
    appeared anywhere under tests/ before this file. The engines import
    huggingface_hub, transformers and gradio; each of those reads exactly these
    variables and each will happily reach the internet with a token it found in
    the environment.
    """

    def _spawn_env(self, monkeypatch, **overrides) -> dict[str, str]:
        """Start a worker far enough to capture the environment it was given."""
        from tts import worker_client

        captured: dict[str, dict[str, str]] = {}

        def fake_popen(*args, **kwargs):
            captured["env"] = dict(kwargs["env"])
            raise OSError("stopped after the environment was built")

        monkeypatch.setattr(worker_client.subprocess, "Popen", fake_popen)
        worker = worker_client.WorkerClient(
            sys.executable, str(Path(sys.executable)),
            engine_id="test", env=overrides or None,
        )
        with pytest.raises(worker_client.WorkerFailure):
            worker.start(timeout=0.1)
        return captured["env"]

    @pytest.mark.parametrize("secret", [
        "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HUGGINGFACE_TOKEN",
        "WANDB_API_KEY",
    ])
    def test_a_credential_in_the_parent_environment_is_stripped(
        self, monkeypatch: pytest.MonkeyPatch, secret: str
    ) -> None:
        monkeypatch.setenv(secret, "leaked-token-value")
        assert secret not in self._spawn_env(monkeypatch)

    @pytest.mark.parametrize("proxy", ["HTTP_PROXY", "HTTPS_PROXY",
                                       "ALL_PROXY"])
    def test_an_exported_proxy_does_not_reach_the_engine(
        self, monkeypatch: pytest.MonkeyPatch, proxy: str
    ) -> None:
        monkeypatch.setenv(proxy, "http://ambient-proxy.invalid:9")
        assert proxy not in self._spawn_env(monkeypatch)

    def test_offline_is_forced_over_an_inherited_setting(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # setdefault would lose here, which is why it is update.
        monkeypatch.setenv("HF_HUB_OFFLINE", "0")
        monkeypatch.setenv("TRANSFORMERS_OFFLINE", "0")
        env = self._spawn_env(monkeypatch)
        assert env["HF_HUB_OFFLINE"] == "1"
        assert env["TRANSFORMERS_OFFLINE"] == "1"

    def test_telemetry_is_off_in_every_engine_the_app_ships(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = self._spawn_env(monkeypatch)
        assert env["HF_HUB_DISABLE_TELEMETRY"] == "1"
        assert env["GRADIO_ANALYTICS_ENABLED"] == "False"
        assert env["WANDB_MODE"] == "disabled"
        assert env["DO_NOT_TRACK"] == "1"

    def test_a_per_worker_override_cannot_switch_offline_back_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The forced block used to be applied BEFORE the caller's env, so this
        # override won. No caller passes one today; the ordering is the
        # guarantee, and an ordering nothing tests is an accident.
        env = self._spawn_env(monkeypatch, HF_HUB_OFFLINE="0",
                              HF_HUB_DISABLE_TELEMETRY="0")
        assert env["HF_HUB_OFFLINE"] == "1"
        assert env["HF_HUB_DISABLE_TELEMETRY"] == "1"

    def test_an_unrelated_override_still_reaches_the_worker(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = self._spawn_env(monkeypatch, ELYSIUM_TEST_KNOB="7")
        assert env["ELYSIUM_TEST_KNOB"] == "7"


class TestTheCharacterHasAName:
    """K-31. Five fields went out and the one naming who is speaking did not.

    The user's persona has carried its name since the beginning, so the model
    was told who it was talking TO and not who it was playing. Measured before
    the fix: a prototype changed zero tests, because nothing anywhere asserted
    what the system block contains.

    A LABEL rather than an instruction, and the persona header's exact twin.
    "You are X." competes with the card - an author who has already set the
    voice in system_prompt would have the app talking over them.
    """

    def test_the_name_reaches_the_provider(
        self, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        created = client.post("/api/v1/characters", json={
            "name": "Marisol Vance",
            "description": "a lighthouse keeper",
            "first_mes": "Hello there!",
        })
        assert created.status_code == 201, created.text
        bodies = _capture_wire(monkeypatch)
        chat_id = make_chat(client, created.json()["id"])
        response = client.post(f"/api/v1/chats/{chat_id}/complete",
                               json={"message": "hi",
                                     "model_id": "test/model-1"})
        assert response.status_code == 200, response.text

        system = bodies[0]["messages"][0]
        assert system["role"] == "system"
        assert "[Character: Marisol Vance]" in system["content"]
        # The control: the rest of the block is still there, so this cannot
        # pass by replacing it.
        assert "a lighthouse keeper" in system["content"]

    def test_the_header_comes_first(
        self, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Who is speaking, before what they are like. Same order the persona
        # block uses for the person on the other side.
        created = client.post("/api/v1/characters", json={
            "name": "Marisol Vance",
            "description": "a lighthouse keeper",
            "first_mes": "Hello there!",
        })
        bodies = _capture_wire(monkeypatch)
        chat_id = make_chat(client, created.json()["id"])
        client.post(f"/api/v1/chats/{chat_id}/complete",
                    json={"message": "hi", "model_id": "test/model-1"})

        content = bodies[0]["messages"][0]["content"]
        assert content.index("[Character:") < content.index("[Description]")

    def test_a_nameless_row_emits_no_empty_header(self) -> None:
        # Defensive, exactly like the persona block's own branch: the schema
        # forbids a blank name, and a hand-edited vault must not produce
        # "[Character: ]" for the model to reason about.
        from routers.completions import _build_system_block

        block = _build_system_block({
            "name": "   ",
            "system_prompt": "",
            "description": "a lighthouse keeper",
            "personality": "",
            "scenario": "",
            "mes_example": "",
        })
        assert "[Character:" not in block
        assert "a lighthouse keeper" in block
