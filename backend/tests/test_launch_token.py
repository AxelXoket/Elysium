"""Loopback is not a permission boundary, and everything else assumed it was.

TrustedHostMiddleware kills DNS rebinding. csrf_shield refuses a foreign
Origin. CORS is narrow. Every one of those defends against a WEB PAGE, which
is the right threat for the web and the wrong one for a program.

Nothing stopped a PROCESS. Any program running as this user - a script, a sync
client, something that came down with a download - could

    curl http://127.0.0.1:<port>/api/v1/chats

and read every conversation, because none of the guards above is a check on
WHO is asking. The vault does not help: while the window is open it is
unlocked by definition, which is exactly when the data is worth taking.
"""
from __future__ import annotations

import pytest

import launch_token


@pytest.fixture()
def armed(monkeypatch: pytest.MonkeyPatch):
    """A launch with a token, as the packaged app runs."""
    secret = "test-launch-token-value"
    monkeypatch.setattr(launch_token, "_token", secret)
    monkeypatch.setenv(launch_token.ENV_VAR, secret)
    return secret


class TestTheGateIsUnarmedUnlessALaunchIssuedAToken:
    def test_no_token_means_everything_is_accepted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A developer running uvicorn by hand has no token. Refusing them
        # would produce a 403 describing a misconfiguration as an attack.
        monkeypatch.setattr(launch_token, "_token", None)
        monkeypatch.delenv(launch_token.ENV_VAR, raising=False)
        assert launch_token.configured() is None
        assert launch_token.accepts(None) is True
        assert launch_token.accepts("anything") is True

    def test_the_api_still_answers_without_one(self, client) -> None:
        assert client.get("/api/v1/settings").status_code == 200


class TestWhenItIsArmed:
    def test_the_right_token_is_accepted(self, armed: str) -> None:
        assert launch_token.accepts(armed) is True

    @pytest.mark.parametrize("presented", [
        None, "", "wrong", "test-launch-token-valu", "test-launch-token-value ",
    ])
    def test_anything_else_is_refused(self, armed: str, presented) -> None:
        assert launch_token.accepts(presented) is False

    def test_a_request_without_the_header_is_refused(
        self, client, armed: str
    ) -> None:
        # The whole point, at the HTTP boundary: this is the curl above.
        response = client.get("/api/v1/settings")
        assert response.status_code == 403
        assert response.json()["detail"] == "launch_token_invalid"

    def test_a_request_with_the_header_passes(self, client, armed: str
                                              ) -> None:
        response = client.get("/api/v1/settings",
                              headers={launch_token.HEADER: armed})
        assert response.status_code == 200

    def test_the_vault_routes_need_it_too(self, client, armed: str) -> None:
        """The route this gate exists FOR, and the one nothing was checking.

        vault_gate deliberately lets /vault/* through - it has to, or the
        passphrase screen could never reach unlock. The launch gate sits
        OUTSIDE it precisely so that exemption does not become a hole. Every
        other test here uses /settings or /characters, so a future change that
        exempted /vault/* "for symmetry with vault_gate" would reopen
        unauthenticated unlock probing with the suite still green.
        """
        for path in ("/api/v1/vault/status", "/api/v1/vault/unlock"):
            response = client.post(path, json={"passphrase": "irrelevant"}) \
                if path.endswith("unlock") else client.get(path)
            assert response.status_code == 403, path
            assert response.json()["detail"] == "launch_token_invalid", path

    def test_a_write_without_it_is_refused_too(self, client, armed: str
                                               ) -> None:
        response = client.post("/api/v1/characters",
                               json={"name": "x", "first_mes": "hi"})
        assert response.status_code == 403

    def test_the_refusal_says_nothing_about_the_token(
        self, client, armed: str
    ) -> None:
        # A 403 that echoed what was expected, or how close the guess was,
        # would be a slower way of handing the token over.
        body = client.get("/api/v1/settings").text
        assert armed not in body


class TestWhatStaysReachable:
    def test_the_health_probe_is_not_gated(self, client, armed: str) -> None:
        # The launcher polls it BEFORE the window exists, so it cannot present
        # a token yet. It answers a fixed string with nothing of the user's.
        assert client.get("/healthz").status_code == 200

    def test_a_preflight_is_not_gated(self, client, armed: str) -> None:
        response = client.options(
            "/api/v1/settings",
            headers={"Origin": "http://127.0.0.1:8787",
                     "Access-Control-Request-Method": "GET"})
        assert response.status_code != 403

    def test_an_element_loaded_image_passes_with_a_same_origin_signal(
        self, client, armed: str
    ) -> None:
        # <img src> cannot carry a custom header. Sec-Fetch-Site is set by the
        # browser and cannot be forged from the page, so requiring it still
        # refuses a program with curl.
        response = client.get("/api/v1/uploads/images/999999",
                              headers={"sec-fetch-site": "same-origin"})
        assert response.status_code != 403

    def test_the_same_image_route_is_refused_without_that_signal(
        self, client, armed: str
    ) -> None:
        # This is the curl case for the exempted routes, and it must still be
        # refused or the exemption is a hole rather than a narrowing.
        assert client.get("/api/v1/uploads/images/999999").status_code == 403

    def test_a_cross_site_signal_does_not_open_it(self, client, armed: str
                                                   ) -> None:
        response = client.get("/api/v1/uploads/images/999999",
                              headers={"sec-fetch-site": "cross-site"})
        assert response.status_code == 403

    def test_the_exemption_does_not_cover_the_data_routes(
        self, client, armed: str
    ) -> None:
        # The narrowing must be exactly two routes. A same-origin signal on
        # /chats would make the whole gate decorative.
        for path in ("/api/v1/chats", "/api/v1/settings", "/api/v1/personas"):
            response = client.get(path,
                                  headers={"sec-fetch-site": "same-origin"})
            assert response.status_code == 403, path

    def test_the_exemption_does_not_cover_writes_to_those_routes(
        self, client, armed: str
    ) -> None:
        response = client.delete("/api/v1/uploads/images/999999",
                                 headers={"sec-fetch-site": "same-origin"})
        assert response.status_code == 403


class TestTheTokenItself:
    def test_issue_produces_something_worth_guessing_at(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(launch_token, "_token", None)
        first = launch_token.issue()
        try:
            assert len(first) >= 32
            second = launch_token.issue()
            assert second != first, "two launches shared a token"
        finally:
            launch_token.reset()

    def test_it_is_published_where_the_server_can_read_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import os
        monkeypatch.setattr(launch_token, "_token", None)
        issued = launch_token.issue()
        try:
            assert os.environ[launch_token.ENV_VAR] == issued
        finally:
            launch_token.reset()

    def test_a_prefix_of_the_token_is_not_enough(self, armed: str) -> None:
        # The property a constant-time compare is there to protect, stated as
        # behaviour rather than by reading the source for compare_digest.
        for cut in range(1, len(armed)):
            assert launch_token.accepts(armed[:cut]) is False


class TestTheTokenDoesNotReachOurOwnSubprocesses:
    """The token stops another PROCESS reading the conversation.

    Handing it to a subprocess is therefore not a detail - it is the hole,
    reopened from the inside. Both children this app spawns run code it did
    not write: the voice engine is a stack of third-party ML packages, and uv
    executes setup code out of wheels during an install. Either one could read
    ELYSIUM_LAUNCH_TOKEN out of its own environment and ask the local API for
    everything, with the vault unlocked by definition.
    """

    def test_the_voice_engine_is_not_given_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import subprocess
        import sys
        from pathlib import Path
        from tts import worker_client

        monkeypatch.setenv(launch_token.ENV_VAR, "the-launch-secret")
        captured: dict[str, dict[str, str]] = {}

        def fake_popen(*args, **kwargs):
            captured["env"] = dict(kwargs["env"])
            raise OSError("stopped after the environment was built")

        monkeypatch.setattr(worker_client.subprocess, "Popen", fake_popen)
        worker = worker_client.WorkerClient(
            sys.executable, str(Path(sys.executable)), engine_id="test")
        with pytest.raises(worker_client.WorkerFailure):
            worker.start(timeout=0.1)

        assert launch_token.ENV_VAR not in captured["env"]
        assert "the-launch-secret" not in "".join(captured["env"].values())

    def test_the_installer_subprocess_is_not_given_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import subprocess
        from tts import provision

        monkeypatch.setenv(launch_token.ENV_VAR, "the-launch-secret")
        captured: dict[str, dict[str, str]] = {}

        def fake_popen(*args, **kwargs):
            captured["env"] = dict(kwargs["env"])
            raise OSError("stopped after the environment was built")

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        provision._run(["uv", "--version"], on_line=lambda line: None,
                       cancel=None, timeout=5.0, env={})

        assert launch_token.ENV_VAR not in captured["env"]

    def test_the_rest_of_the_environment_still_arrives(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The control: stripping one name is not licence to hand uv an empty
        # environment, and it needs PATH and TEMP to run at all.
        import subprocess
        from tts import provision

        monkeypatch.setenv("ELYSIUM_HARMLESS", "keep-me")
        captured: dict[str, dict[str, str]] = {}

        def fake_popen(*args, **kwargs):
            captured["env"] = dict(kwargs["env"])
            raise OSError("stopped")

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        provision._run(["uv", "--version"], on_line=lambda line: None,
                       cancel=None, timeout=5.0, env={})
        assert captured["env"]["ELYSIUM_HARMLESS"] == "keep-me"
