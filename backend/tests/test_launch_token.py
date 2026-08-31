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
    """A launch with a token, as the packaged app runs.

    The module global and NOTHING ELSE. This fixture used to also set
    ENV_VAR, which matched an issue() that published the secret to the process
    environment - where any program running as the same user could read it
    back out of our PEB. Now that issue() keeps it in memory, arming it here
    the same way means every armed test below is also evidence that the gate
    needs no environment variable to work.
    """
    secret = "test-launch-token-value"
    monkeypatch.setattr(launch_token, "_token", secret)
    monkeypatch.delenv(launch_token.ENV_VAR, raising=False)
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

    @pytest.mark.parametrize("presented", [
        chr(0xE9) + "bad",                 # latin-1 range
        chr(0xFF) + "test-launch-token",   # the top of the byte
        "token" + chr(0x100),              # past latin-1 entirely
    ])
    def test_a_byte_over_ascii_is_refused_rather_than_crashing(
        self, armed: str, presented: str
    ) -> None:
        """It used to raise, and a raise is not a refusal.

        Starlette decodes header bytes as latin-1, so any byte over 0x7f
        reaches this function as a non-ASCII str - and hmac.compare_digest
        refuses to compare those, with TypeError. The exception escaped the
        gate and became a 500 with a traceback, produced by exactly the local
        process the gate exists to turn away.

        Invisible to this file until now for a mechanical reason worth
        recording: the test client ASCII-encodes headers and cannot send the
        byte, so only a call at this level, or one at the raw ASGI layer, can
        reach it.
        """
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
        # browser and cannot be forged from the page, so a hostile PAGE is
        # still refused. A local program is not, and the case below says so
        # out loud rather than leaving this comment to imply otherwise.
        response = client.get("/api/v1/uploads/images/999999",
                              headers={"sec-fetch-site": "same-origin"})
        assert response.status_code != 403

    def test_a_program_that_sets_the_header_by_hand_is_not_refused(
        self, client, armed: str
    ) -> None:
        """The size of the exemption, measured rather than described.

        README.md and SECURITY.md both said this gate "refuses a program with
        curl", and main.py's own comment said the same. It does not:
        `curl -H "Sec-Fetch-Site: same-origin"` is one flag, and nothing on
        the server can tell that request from a browser's. The documents now
        say what this test measures, and this test is what stops them drifting
        back.
        """
        # No browser anywhere in this call: the header is typed in, exactly as
        # a command-line tool would type it.
        response = client.get("/api/v1/uploads/images/999999",
                              headers={"sec-fetch-site": "same-origin"})
        assert response.status_code != 403, (
            "the same-origin exemption stopped accepting a hand-set header; "
            "if that is deliberate, the two documents have to change with it"
        )

        # GROUND CONTROL, and the half that IS still true: the same request
        # with a cross-site signal, which a browser would send from another
        # origin, is refused.
        cross = client.get("/api/v1/uploads/images/999999",
                           headers={"sec-fetch-site": "cross-site"})
        assert cross.status_code == 403

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

    def test_the_server_can_read_it_without_any_environment_variable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The reason publishing it was never needed.

        uvicorn runs on a THREAD of the process that issued the token
        (run_app.serve), so configured() reads the same module global the
        launcher wrote. This test is what replaced one asserting the opposite
        property - that os.environ carried it - which is how the leak below
        survived review for as long as it did.
        """
        import os
        monkeypatch.setattr(launch_token, "_token", None)
        monkeypatch.delenv(launch_token.ENV_VAR, raising=False)
        issued = launch_token.issue()
        try:
            assert launch_token.configured() == issued
            assert launch_token.accepts(issued) is True
            assert os.environ.get(launch_token.ENV_VAR) is None
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


def _win32_environment_block() -> list[str]:
    """This process's environment as the KERNEL holds it, not as Python
    remembers it.

    GetEnvironmentStringsW hands back the very block that PEB
    ProcessParameters.Environment points at, which is the block an attacker
    reads with OpenProcess(QUERY_INFORMATION|VM_READ) plus ReadProcessMemory.
    Same bytes, same address space, one API call instead of a struct walk over
    undocumented offsets - so it is evidence at the level the attack actually
    uses, without the fragility that would make it a bad tenant of a suite.

    It also sees things os.environ cannot. os.environ is Python's own dict; a
    bare os.putenv, or a ctypes SetEnvironmentVariableW, would publish the
    secret to exactly this block while leaving that dict clean.
    """
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetEnvironmentStringsW.restype = ctypes.c_void_p
    kernel32.FreeEnvironmentStringsW.argtypes = [ctypes.c_void_p]
    block = kernel32.GetEnvironmentStringsW()
    if not block:
        raise OSError(ctypes.get_last_error(), "GetEnvironmentStringsW failed")
    try:
        entries: list[str] = []
        offset = 0
        while True:
            # NAME=VALUE strings, NUL separated, terminated by an empty one.
            entry = ctypes.wstring_at(block + offset)
            if not entry:
                return entries
            entries.append(entry)
            offset += (len(entry) + 1) * ctypes.sizeof(ctypes.c_wchar)
    finally:
        kernel32.FreeEnvironmentStringsW(block)


class TestTheTokenIsNeverPublishedToTheProcessEnvironment:
    """The leak that a working exploit found, kept shut.

    issue() used to end with `os.environ[ENV_VAR] = _token`. A separate,
    unprivileged process running as the same user opened this one with
    QUERY_INFORMATION|VM_READ (error 0; being the same user was enough),
    walked the PEB to ProcessParameters.Environment, and read back the literal
    string `ELYSIUM_LAUNCH_TOKEN=<token>`. That token is the whole gate on
    /chats, /messages, /characters and everything else. The module had
    reasoned carefully about query strings, localStorage and session restore
    files, and then handed the secret to the one reader it named as the
    adversary.

    The publication bought nothing: the server runs on a thread of this same
    process, and configured() prefers the module global anyway.
    """

    def test_issuing_a_token_does_not_put_it_in_os_environ(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import os

        monkeypatch.setattr(launch_token, "_token", None)
        monkeypatch.delenv(launch_token.ENV_VAR, raising=False)
        issued = launch_token.issue()
        try:
            # POSITIVE CONTROL, first: a token really was issued, and it is
            # really armed. Without this the assertions below would pass just
            # as happily if issue() had returned an empty string or done
            # nothing at all.
            assert issued and len(issued) >= 32
            assert launch_token.configured() == issued

            assert launch_token.ENV_VAR not in os.environ
            # Not just under that name. A rename would be the same leak.
            assert issued not in os.environ.values()
        finally:
            launch_token.reset()

    def test_issuing_a_token_does_not_put_it_in_the_win32_environment_block(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The same property, proved where the attack reads."""
        import os

        monkeypatch.setattr(launch_token, "_token", None)
        monkeypatch.delenv(launch_token.ENV_VAR, raising=False)
        # POSITIVE CONTROL for the READER: prove this block shows what a
        # publication would look like, so that a silent GetEnvironmentStringsW
        # failure cannot pass as an absence of the token.
        monkeypatch.setenv("ELYSIUM_ENV_BLOCK_PROBE", "sentinel-is-readable")

        issued = launch_token.issue()
        try:
            assert launch_token.configured() == issued   # a token exists
            block = _win32_environment_block()
            assert any("sentinel-is-readable" in entry for entry in block), (
                "the environment block reader saw nothing; the absence of the "
                "token below would prove nothing")
            assert not [entry for entry in block if issued in entry]
            assert not [entry for entry in block
                        if entry.startswith(launch_token.ENV_VAR + "=")]
        finally:
            launch_token.reset()
            os.environ.pop("ELYSIUM_ENV_BLOCK_PROBE", None)

    def test_a_real_issued_token_still_gates_the_api_over_http(
        self, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """End to end, with no environment variable anywhere.

        The behavioural answer to "was the publication load bearing?". The
        gate arms, refuses a request without the header and accepts one with
        it, on a token that exists only in this process's memory.
        """
        monkeypatch.setattr(launch_token, "_token", None)
        monkeypatch.delenv(launch_token.ENV_VAR, raising=False)
        issued = launch_token.issue()
        try:
            assert client.get("/api/v1/settings").status_code == 403
            with_header = client.get(
                "/api/v1/settings", headers={launch_token.HEADER: issued})
            assert with_header.status_code == 200
        finally:
            launch_token.reset()


class TestTheDeveloperPathIsUnchanged:
    """`uvicorn main:app` by hand, before and after the leak was closed.

    Recorded as behaviour because it is the one thing removing the write could
    have broken quietly: the env read in configured() is now the ONLY use that
    variable has, and it belongs to whoever starts a server without run_app.
    """

    def test_a_hand_started_server_has_no_token_and_stays_open(
        self, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Nothing issued, nothing in the environment: exactly the state a
        # developer's shell leaves the module in.
        monkeypatch.setattr(launch_token, "_token", None)
        monkeypatch.delenv(launch_token.ENV_VAR, raising=False)
        assert launch_token.configured() is None
        assert client.get("/api/v1/settings").status_code == 200

    def test_a_developer_can_still_arm_it_from_their_own_shell(
        self, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Reading ENV_VAR is safe in a way writing it is not: another process
        # cannot put a value into this process's environment. Kept so the gate
        # can be exercised without building the exe.
        monkeypatch.setattr(launch_token, "_token", None)
        monkeypatch.setenv(launch_token.ENV_VAR, "a-developers-own-token")
        assert launch_token.configured() == "a-developers-own-token"
        assert client.get("/api/v1/settings").status_code == 403
        armed_response = client.get(
            "/api/v1/settings",
            headers={launch_token.HEADER: "a-developers-own-token"})
        assert armed_response.status_code == 200
