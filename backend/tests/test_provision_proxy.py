"""The installer's network environment, which was the one that trusted the shell.

Everything else in this app builds its clients with trust_env=False. The voice
installer did the opposite: it handed a child process a copy of os.environ and
then added the configured proxy on top, so on a machine with HTTP_PROXY
exported a 2.6 GB download went through a host the app never chose, and on a
machine with NO_PROXY=* it went through none at all - past a proxy the user had
marked mandatory.

Underneath that sat a smaller and worse bug. _proxy_required() answered False
on any exception. The switch means "do not go out without the proxy", and its
failure mode was permission to go out without it, on the single code path where
the user is least able to see what happened.
"""
from __future__ import annotations

import subprocess

import pytest

from tts import provision
from tts.errors import TTS_RUNTIME_INSTALL_FAILED


class TestTheMandatoryProxySwitchFailsClosed:
    def test_an_unreadable_setting_means_required(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import database

        def explode(name):
            raise RuntimeError("vault is locked")

        monkeypatch.setattr(database, "get_setting", explode)
        assert provision._proxy_required() is True

    def test_the_switch_still_reads_off_when_it_is_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The control. Failing closed is only a fix if the ordinary answer
        # still comes through - otherwise every install refuses forever.
        import database

        monkeypatch.setattr(database, "get_setting", lambda name: "0")
        assert provision._proxy_required() is False

    def test_the_switch_reads_on_when_it_is_on(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import database

        monkeypatch.setattr(database, "get_setting", lambda name: "1")
        assert provision._proxy_required() is True


class TestUnreadableIsNotTheSameAsUnset:
    def test_reading_the_proxy_raises_when_the_vault_cannot_answer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import secrets_service

        def explode(name):
            raise RuntimeError("vault is locked")

        monkeypatch.setattr(secrets_service, "get_secret", explode)
        with pytest.raises(provision.ProxyUnreadable):
            provision._read_proxy()

    def test_the_non_raising_wrapper_still_answers_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import secrets_service

        def explode(name):
            raise RuntimeError("vault is locked")

        monkeypatch.setattr(secrets_service, "get_secret", explode)
        assert provision._proxy_url() is None

    def test_no_proxy_configured_is_not_an_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import secrets_service

        monkeypatch.setattr(secrets_service, "get_secret", lambda name: "")
        assert provision._read_proxy() is None


class TestTheChildDoesNotInheritTheShellsNetwork:
    @pytest.mark.parametrize("name", [
        "NO_PROXY", "no_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
        "http_proxy", "https_proxy", "all_proxy",
    ])
    def test_an_ambient_proxy_variable_is_removed(
        self, monkeypatch: pytest.MonkeyPatch, name: str
    ) -> None:
        monkeypatch.setattr(provision, "_proxy_url", lambda: None)
        assert provision._proxy_env({})[name] is None

    @pytest.mark.parametrize("name", [
        "UV_INDEX", "UV_INDEX_URL", "UV_EXTRA_INDEX_URL", "UV_DEFAULT_INDEX",
        "UV_FIND_LINKS", "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL",
        "PIP_TRUSTED_HOST", "PIP_FIND_LINKS", "PIP_CONFIG_FILE",
    ])
    def test_an_ambient_index_variable_is_removed(
        self, monkeypatch: pytest.MonkeyPatch, name: str
    ) -> None:
        # Not only privacy: these decide WHERE several gigabytes of code comes
        # from, and the installer then runs it.
        monkeypatch.setattr(provision, "_proxy_url", lambda: None)
        assert provision._proxy_env({})[name] is None

    def test_the_configured_proxy_still_wins(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(provision, "_proxy_url",
                            lambda: "socks5://127.0.0.1:9050")
        env = provision._proxy_env({})
        assert env["HTTPS_PROXY"] == "socks5://127.0.0.1:9050"
        assert env["all_proxy"] == "socks5://127.0.0.1:9050"
        # And NO_PROXY is still gone, so it cannot carve a hole in it.
        assert env["NO_PROXY"] is None

    def test_the_callers_own_variables_survive(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(provision, "_proxy_url", lambda: None)
        assert provision._proxy_env({"UV_CACHE_DIR": "x"})["UV_CACHE_DIR"] == "x"


class TestTheRemovalReachesTheRealChild:
    """_proxy_env only marks; _run is what has to act on the mark."""

    def _captured_env(self, monkeypatch, env: dict) -> dict:
        captured: dict[str, dict] = {}

        def fake_popen(*args, **kwargs):
            captured["env"] = dict(kwargs["env"])
            raise OSError("stopped after the environment was built")

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        provision._run(["uv", "--version"], on_line=lambda line: None,
                       cancel=None, timeout=5.0, env=env)
        return captured["env"]

    def test_a_none_value_takes_the_variable_away(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NO_PROXY", "*")
        env = self._captured_env(monkeypatch, {"NO_PROXY": None})
        assert "NO_PROXY" not in env

    def test_a_none_value_never_arrives_as_the_string_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The failure this branch exists to avoid: "None" is a truthy value to
        # uv, and NO_PROXY="None" is worse than the variable being inherited.
        monkeypatch.setenv("HTTPS_PROXY", "http://ambient:9")
        env = self._captured_env(monkeypatch, {"HTTPS_PROXY": None})
        assert env.get("HTTPS_PROXY") != "None"
        assert "HTTPS_PROXY" not in env

    def test_an_ordinary_value_is_still_passed_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = self._captured_env(monkeypatch, {"HTTPS_PROXY": "http://p:8080"})
        assert env["HTTPS_PROXY"] == "http://p:8080"

    def test_the_whole_ambient_environment_is_not_thrown_away(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # uv needs PATH, TEMP and the rest. Stripping the network variables is
        # not licence to hand the child an empty environment.
        monkeypatch.setenv("ELYSIUM_HARMLESS", "keep-me")
        env = self._captured_env(monkeypatch, {"NO_PROXY": None})
        assert env["ELYSIUM_HARMLESS"] == "keep-me"


class TestTheDownloadRefusesRatherThanGoBare:
    def test_the_opener_refuses_when_a_required_proxy_is_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # start_install's gate runs before the job is queued. The vault can
        # lock between there and the ~25 MB GitHub request, and this is the
        # check at the moment of the connection.
        monkeypatch.setattr(provision, "_proxy_url", lambda: None)
        monkeypatch.setattr(provision, "_proxy_required", lambda: True)
        with pytest.raises(provision.ProxyUnreadable):
            provision._url_opener()

    def test_the_opener_is_built_when_no_proxy_is_required(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(provision, "_proxy_url", lambda: None)
        monkeypatch.setattr(provision, "_proxy_required", lambda: False)
        assert provision._url_opener() is not None

    def test_a_configured_proxy_is_wired_into_the_opener(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import urllib.request

        monkeypatch.setattr(provision, "_proxy_url",
                            lambda: "http://127.0.0.1:8080")
        monkeypatch.setattr(provision, "_proxy_required", lambda: True)
        opener = provision._url_opener()
        handlers = [h for h in opener.handlers
                    if isinstance(h, urllib.request.ProxyHandler)]
        assert handlers, "no proxy handler was installed"
        assert handlers[0].proxies.get("https") == "http://127.0.0.1:8080"


class TestTheInstallGateTellsTheTwoCasesApart:
    def _plan(self, monkeypatch) -> None:
        monkeypatch.setattr(provision, "reset_jobs", provision.reset_jobs)
        provision.reset_jobs()

    def test_an_unreadable_vault_refuses_with_its_own_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._plan(monkeypatch)
        monkeypatch.setattr(provision, "_proxy_required", lambda: True)

        def unreadable():
            raise provision.ProxyUnreadable("locked")

        monkeypatch.setattr(provision, "_read_proxy", unreadable)

        with pytest.raises(provision.ProvisionError) as caught:
            provision.start_install("fish_s2")
        assert caught.value.code == TTS_RUNTIME_INSTALL_FAILED
        assert "vault" in caught.value.detail

    def test_no_proxy_configured_refuses_with_the_other_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._plan(monkeypatch)
        monkeypatch.setattr(provision, "_proxy_required", lambda: True)
        monkeypatch.setattr(provision, "_read_proxy", lambda: None)

        with pytest.raises(provision.ProvisionError) as caught:
            provision.start_install("fish_s2")
        assert "none is configured" in caught.value.detail

    def test_the_job_slot_is_released_after_the_refusal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A gate that leaves the engine marked busy turns one refusal into a
        # permanent one.
        self._plan(monkeypatch)
        monkeypatch.setattr(provision, "_proxy_required", lambda: True)
        monkeypatch.setattr(provision, "_read_proxy", lambda: None)

        with pytest.raises(provision.ProvisionError):
            provision.start_install("fish_s2")
        assert provision.job("fish_s2")["running"] is False
