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
from tts.errors import TTS_PROXY_UNAVAILABLE


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
        # Both halves of the premise, not one. "No proxy is configured" only
        # describes a state the installer may proceed from when the proxy is
        # also not MANDATORY; with the switch armed, the same two facts are
        # the refusal tested further down. Stating only the first half used to
        # be enough because _proxy_env decided nothing - which was the defect.
        monkeypatch.setattr(provision, "_proxy_required", lambda: False)
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
        monkeypatch.setattr(provision, "_proxy_required", lambda: False)
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
        monkeypatch.setattr(provision, "_proxy_required", lambda: False)
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
    def test_the_client_refuses_when_a_required_proxy_is_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # start_install's gate runs before the job is queued. The vault can
        # lock between there and the ~25 MB GitHub request, and this is the
        # check at the moment of the connection.
        monkeypatch.setattr(provision, "_proxy_url", lambda: None)
        monkeypatch.setattr(provision, "_proxy_required", lambda: True)
        with pytest.raises(provision.ProxyUnreadable):
            provision._provision_client()

    def test_the_client_is_built_when_no_proxy_is_required(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(provision, "_proxy_url", lambda: None)
        monkeypatch.setattr(provision, "_proxy_required", lambda: False)
        client = provision._provision_client()
        try:
            assert client is not None
        finally:
            client.close()

    def test_a_configured_proxy_is_wired_into_the_client(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Read off the transport map rather than a constructor argument: the
        # claim is that a request would GO there, and httpx keeps one mounted
        # transport per proxied scheme. A client that stored the url and
        # mounted nothing would satisfy a constructor assertion and still
        # connect direct.
        monkeypatch.setattr(provision, "_proxy_url",
                            lambda: "http://127.0.0.1:8080")
        monkeypatch.setattr(provision, "_proxy_required", lambda: True)
        client = provision._provision_client()
        try:
            assert client._mounts, "no proxy transport was mounted"
        finally:
            client.close()

    def test_the_client_does_not_read_the_ambient_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Ground control for the test above: with no proxy configured there
        # must be NO mounted transport, even with the shell exporting one.
        # Without this leg, "_mounts is truthy" could be true of every client
        # this function builds and would prove nothing about the vault's proxy.
        monkeypatch.setenv("HTTPS_PROXY", "http://ambient:9999")
        monkeypatch.setenv("ALL_PROXY", "http://ambient:9999")
        monkeypatch.setattr(provision, "_proxy_url", lambda: None)
        monkeypatch.setattr(provision, "_proxy_required", lambda: False)
        client = provision._provision_client()
        try:
            assert client.trust_env is False
            assert not client._mounts, (
                f"the download would go through an ambient proxy: "
                f"{client._mounts}")
        finally:
            client.close()


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
        # Its OWN code, not tts_runtime_install_failed. That one's sentence is
        # "setup did not finish, you can try again", and trying again is
        # exactly what does not help here: the answer will be the same until
        # the vault is unlocked, and nothing on screen would ever say so.
        assert caught.value.code == TTS_PROXY_UNAVAILABLE
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


# -- The gate: one door, and nothing goes out around it ----------------------
#
# The ruling this section answers to: if the user set their proxy that way,
# everything that leaves the app, downloads included, has to go through it.
# Two conditions fall out of it. The install path fails CLOSED, and the
# install actually travels the proxy. Both are claims about behaviour, so both
# are measured by counting what left and by watching a real socket, never by
# reading provision.py back.

import socket
import threading
from pathlib import Path

import config

from tts.provision import PROXY_REQUIRED_UNAVAILABLE


class _Loopback:
    """A listener that records the first bytes a client says to it.

    A proxy is a thing you talk to, so "the proxy was used" is a claim about
    bytes on a socket, not about a constructor argument. This is the only
    instrument that can tell a socks5h url actually speaking SOCKS from a
    socks5h url quietly emitting HTTP CONNECT, which is what the hand-built
    urllib opener did while every structural test agreed it was configured
    correctly.
    """

    def __init__(self) -> None:
        self.first: bytes = b""
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self._sock.settimeout(10.0)
        self.port = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        try:
            conn, _ = self._sock.accept()
            with conn:
                conn.settimeout(5.0)
                try:
                    self.first = conn.recv(4096)
                except OSError:
                    pass
        except OSError:
            pass

    def url(self, scheme: str) -> str:
        return f"{scheme}://127.0.0.1:{self.port}"

    def close(self) -> None:
        self._thread.join(timeout=10.0)
        try:
            self._sock.close()
        except OSError:
            pass


@pytest.fixture
def voice_dirs(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """The installer's directories, redirected into tmp_path.

    conftest's _isolated_voice_caches moves the wheel cache but deliberately
    not TTS_BIN_DIR or TTS_ENVS_DIR, so a test that drives the worker without
    this would build into the real ones.
    """
    root = tmp_path / "voice"
    (root / "envs").mkdir(parents=True)
    (root / "bin").mkdir(parents=True)
    for name, value in (
        ("TTS_DIR", root),
        ("TTS_ENVS_DIR", str(root / "envs")),
        ("TTS_BIN_DIR", str(root / "bin")),
        ("TTS_PY_DIR", str(root / "python")),
        ("TTS_UV_CACHE_DIR", str(root / "uv-cache")),
        ("TTS_RUNTIMES_PATH", str(root / "runtimes.json")),
    ):
        monkeypatch.setattr(config, name, value, raising=False)
    provision.reset_jobs()
    yield root
    provision.reset_jobs()


class _WorkerHarness:
    """Runs the whole install worker with fakes, recording WHAT LEFT and WHEN.

    `events` is an ordered log, which is the part that matters: "the gate was
    called" and "the gate was called BEFORE anything went out" are different
    claims, and only the second one is worth anything.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.events: list[str] = []
        self.envs: list[dict] = []
        real_gate = provision._egress_gate

        def watched_gate():
            self.events.append("gate")
            return real_gate()

        def fake_run(argv, *, on_line, cancel, timeout, env=None):
            if str(argv[0]).endswith("uv-stub"):
                # Only the uv invocations reach the network. _verify_env runs
                # the freshly built interpreter with `-c import x`, which does
                # not, and counting that as egress would make the log a lie.
                self.events.append("egress")
                self.envs.append(dict(env or {}))
            if "venv" in argv:
                target = Path(argv[argv.index("venv") + 1])
                (target / "Scripts").mkdir(parents=True, exist_ok=True)
                (target / "Scripts" / "python.exe").write_bytes(b"")
            on_line("Resolved 174 packages")
            return 0, ""

        monkeypatch.setattr(provision, "_egress_gate", watched_gate)
        monkeypatch.setattr(provision, "_run", fake_run)
        monkeypatch.setattr(provision, "_verify_env", lambda *a, **k: None)

    def drive(self, uv=None):
        job = provision._Job(engine_id="fish_s2", state="preparing")
        provision._install_worker(job, provision.plan("fish_s2"),
                                  uv if uv is not None else "uv-stub")
        return job


def _count_popen(monkeypatch: pytest.MonkeyPatch) -> list:
    """Every child this module would actually start.

    The real instrument for "nothing was downloaded": `_run` is our own code
    and a test that trusts it to report on itself proves less than it looks.
    """
    started: list = []

    def fake_popen(*args, **kwargs):
        started.append(list(args[0]) if args else [])
        raise OSError("counted, never run")

    monkeypatch.setattr(provision.subprocess, "Popen", fake_popen)
    return started


def _locked():
    raise provision.ProxyUnreadable("vault is locked")


class TestM1TheInstallDoesNotStartWhenTheProxyCannotBeGot:
    """Proxy mandatory, vault locked, and the install went ahead anyway.

    The state is reachable without anybody doing anything strange. The install
    route is exempt from the idle timer, so pressing Set up and walking away
    locks the vault five minutes later while the job is still running - and
    the several-gigabyte half of the download had not asked the question yet.
    """

    def test_the_subprocess_half_starts_nothing(
        self, voice_dirs, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(provision, "_proxy_required", lambda: True)
        monkeypatch.setattr(provision, "_read_proxy", _locked)
        started = _count_popen(monkeypatch)

        job = provision._Job(engine_id="fish_s2", state="preparing")
        provision._install_worker(job, provision.plan("fish_s2"), "uv-stub")

        assert started == [], f"a child was started anyway: {started}"
        assert job.state == "failed"
        assert job.error_code == TTS_PROXY_UNAVAILABLE
        assert PROXY_REQUIRED_UNAVAILABLE in job.error_detail

    def test_the_download_half_starts_nothing(
        self, voice_dirs, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The same refusal on the other path: no uv on this machine, so the
        # worker fetches it first. That fetch was the only fail-closed egress
        # in the module, and it reported the refusal as TTS_PYTHON_NOT_FOUND.
        monkeypatch.setattr(provision, "_proxy_required", lambda: True)
        monkeypatch.setattr(provision, "_read_proxy", _locked)

        # Trapped UNDER the gate, not over it. Faking provision's own
        # _provision_client would remove the very call being tested and the
        # test would pass on a module with no gate in it at all.
        import network_client

        def must_not_build(proxy):
            raise AssertionError("a client was built past the gate")

        monkeypatch.setattr(network_client, "provisioning_client",
                            must_not_build)
        started = _count_popen(monkeypatch)

        job = provision._Job(engine_id="fish_s2", state="preparing")
        provision._install_worker(job, provision.plan("fish_s2"), None)

        assert started == [], f"a child was started anyway: {started}"
        assert job.state == "failed"
        assert job.error_code == TTS_PROXY_UNAVAILABLE, (
            "the proxy refusal was reported as something else, which is how "
            "it used to reach the user as a broken machine")

    def test_positive_control_a_readable_proxy_lets_it_start(
        self, voice_dirs, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without this leg, "nothing was started" is also what an empty run
        looks like, and the two tests above would pass on a harness that never
        reached the worker at all."""
        monkeypatch.setattr(provision, "_proxy_required", lambda: True)
        monkeypatch.setattr(provision, "_read_proxy",
                            lambda: "http://127.0.0.1:1")
        started = _count_popen(monkeypatch)

        job = provision._Job(engine_id="fish_s2", state="preparing")
        provision._install_worker(job, provision.plan("fish_s2"), "uv-stub")

        assert started, "the same fixture started nothing with a WORKING proxy"
        assert job.error_code != TTS_PROXY_UNAVAILABLE


class TestM2NothingLeavesWithoutPassingTheGate:
    """The install never called the gate at all.

    Not "a gate exists" and not "the gate answers correctly" - both were
    already true of the uv download while the multi-gigabyte path walked past
    it. The claim is about ORDER: nothing goes out before the gate has spoken.
    """

    def test_the_gate_speaks_before_the_first_egress(
        self, voice_dirs, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(provision, "_proxy_required", lambda: False)
        monkeypatch.setattr(provision, "_read_proxy",
                            lambda: "http://127.0.0.1:8080")
        harness = _WorkerHarness(monkeypatch)

        job = harness.drive()

        assert job.state == "done", job.error_detail
        assert "egress" in harness.events, "nothing reached the network at all"
        assert "gate" in harness.events, "the gate was never called"
        assert harness.events.index("gate") < harness.events.index("egress"), (
            f"something went out before the gate was asked: {harness.events}")

    def test_every_child_carries_what_the_gate_returned(
        self, voice_dirs, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Calling the gate and then ignoring it would satisfy the ordering
        # test above. This is the other half: the answer has to arrive.
        monkeypatch.setattr(provision, "_proxy_required", lambda: False)
        monkeypatch.setattr(provision, "_read_proxy",
                            lambda: "http://127.0.0.1:8080")
        harness = _WorkerHarness(monkeypatch)

        harness.drive()

        assert harness.envs, "no child environment was recorded"
        for env in harness.envs:
            assert env["HTTPS_PROXY"] == "http://127.0.0.1:8080"
            assert env["ALL_PROXY"] == "http://127.0.0.1:8080"
            assert env["NO_PROXY"] is None

    def test_the_download_half_asks_the_gate_too(
        self, voice_dirs, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(provision, "_proxy_required", lambda: False)
        monkeypatch.setattr(provision, "_read_proxy", lambda: None)
        asked: list = []
        real_gate = provision._egress_gate

        def watched_gate():
            asked.append("gate")
            return real_gate()

        monkeypatch.setattr(provision, "_egress_gate", watched_gate)
        monkeypatch.setattr(provision, "IS_WINDOWS", True)
        monkeypatch.setattr(provision, "UV_SHA256", "0" * 64)

        provision._download_uv(lambda line: None)

        assert asked, "the uv download reached the network without the gate"


class TestM3TheInstallReallyTravelsTheProxy:
    """Configured is not the same as used, and only a socket can tell."""

    def test_the_proxy_sees_the_connect(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        listener = _Loopback()
        try:
            monkeypatch.setattr(config, "TTS_BIN_DIR", str(tmp_path / "bin"),
                                raising=False)
            monkeypatch.setattr(provision, "IS_WINDOWS", True)
            monkeypatch.setattr(provision, "UV_SHA256", "0" * 64)
            monkeypatch.setattr(provision, "_proxy_required", lambda: True)
            monkeypatch.setattr(provision, "_read_proxy",
                                lambda: listener.url("http"))

            provision._download_uv(lambda line: None)
        finally:
            listener.close()

        assert listener.first.startswith(b"CONNECT "), (
            f"the proxy was never spoken to: {listener.first!r}")
        assert b"github.com:443" in listener.first, (
            f"the proxy was asked for the wrong destination: "
            f"{listener.first!r}")

    def test_ground_control_no_proxy_means_the_listener_hears_nothing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """The instrument, checked. A listener that records nothing whatever
        we do would make the test above meaningless."""
        listener = _Loopback()
        try:
            monkeypatch.setattr(config, "TTS_BIN_DIR", str(tmp_path / "bin"),
                                raising=False)
            monkeypatch.setattr(provision, "IS_WINDOWS", True)
            monkeypatch.setattr(provision, "UV_SHA256", "0" * 64)
            monkeypatch.setattr(provision, "_proxy_required", lambda: False)
            monkeypatch.setattr(provision, "_read_proxy", lambda: None)

            provision._download_uv(lambda line: None)
        finally:
            listener.close()

        assert listener.first == b"", (
            f"an unproxied download somehow reached the listener: "
            f"{listener.first!r}")


class TestM4ASocksProxyIsNotQuietlyDowngraded:
    """Measured, and it is why this download moved off urllib.

    The settings router accepts http, https, socks5 and socks5h. urllib's
    ProxyHandler emits plain `CONNECT host:443` for all four. A user who
    configured socks5h therefore had HTTP bytes sent at a SOCKS port, the
    download failed, and the failure was reported to them as "Elysium could
    not find a way to build the voice engine environment on this computer" -
    a proxy problem, delivered as a verdict on their machine.
    """

    def _first_bytes(self, monkeypatch, tmp_path, scheme: str) -> bytes:
        listener = _Loopback()
        try:
            monkeypatch.setattr(config, "TTS_BIN_DIR",
                                str(tmp_path / f"bin-{scheme}"), raising=False)
            monkeypatch.setattr(provision, "IS_WINDOWS", True)
            monkeypatch.setattr(provision, "UV_SHA256", "0" * 64)
            monkeypatch.setattr(provision, "_proxy_required", lambda: True)
            monkeypatch.setattr(provision, "_read_proxy",
                                lambda: listener.url(scheme))
            provision._download_uv(lambda line: None)
        finally:
            listener.close()
        return listener.first

    def test_socks5h_speaks_socks(self, monkeypatch, tmp_path) -> None:
        first = self._first_bytes(monkeypatch, tmp_path, "socks5h")
        assert first[:1] == b"\x05", (
            f"a SOCKS5 proxy was not greeted with SOCKS5: {first!r}")
        assert not first.startswith(b"CONNECT "), (
            "HTTP CONNECT was sent at a SOCKS port, which is the silent "
            "downgrade this test exists for")

    def test_ground_control_http_still_speaks_http(
        self, monkeypatch, tmp_path
    ) -> None:
        # The two schemes must differ. Without this leg, a client that said
        # b"\x05..." to everything would pass the test above.
        first = self._first_bytes(monkeypatch, tmp_path, "http")
        assert first.startswith(b"CONNECT "), first


class TestM5ARefusalIsNotTheNewDefault:
    """Ground control for the whole section.

    Every test above asserts a refusal. A gate that refused EVERY install
    would satisfy all of them, break the feature for everyone who never asked
    for a proxy, and nothing here would have said a word.
    """

    def test_with_no_proxy_required_the_install_finishes(
        self, voice_dirs, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(provision, "_proxy_required", lambda: False)
        monkeypatch.setattr(provision, "_read_proxy", lambda: None)
        harness = _WorkerHarness(monkeypatch)

        job = harness.drive()

        assert job.state == "done", job.error_detail
        assert job.error_code is None

    def test_and_no_proxy_variable_is_invented_for_the_child(
        self, voice_dirs, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(provision, "_proxy_required", lambda: False)
        monkeypatch.setattr(provision, "_read_proxy", lambda: None)
        harness = _WorkerHarness(monkeypatch)

        harness.drive()

        assert harness.envs
        for env in harness.envs:
            for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                         "http_proxy", "https_proxy", "all_proxy", "NO_PROXY"):
                assert env[name] is None, (
                    f"{name} was set for a user who configured no proxy")
