"""Release-hardening regression tests (v0.5.0 audit follow-ups).

Locks in three invariants the audits flagged as untested:
  1. The 423 vault gate covers EVERY data route automatically - a future
     router added without thought cannot silently ship ungated.
  2. Gate prefix edge cases stay closed (/api/v1vault, dot-dot smuggling).
  3. DATA_DIR resolution: env override wins, frozen mode goes to
     %LOCALAPPDATA%\\Elysium, dev stays beside the code.
"""

import pytest

import re
import sys
from pathlib import Path

from starlette.routing import Route

import vault_state

# Same fixed key the client fixture pre-unlocks with (kept local so this file
# does not depend on tests/ being an importable package).
TEST_VAULT_KEY = bytes(range(32))


def _api_data_routes(app) -> list[tuple[str, str]]:
    """(method, concrete_path) for every /api/v1 route that is NOT a vault
    route, with every path param filled with a dummy id."""
    out = []
    for route in app.routes:
        if not isinstance(route, Route):
            continue
        path = route.path
        if not path.startswith("/api/v1") or path.startswith("/api/v1/vault"):
            continue
        concrete = re.sub(r"\{[^}]+\}", "1", path)
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            out.append((method, concrete))
    return out


def test_gate_covers_every_data_route_while_locked(client):
    """Iterate the real route table: locked vault => 423 on every data route.
    (Not a handful of spot checks - a newly added router is covered by
    construction or this test fails.)"""
    import main

    routes = _api_data_routes(main.app)
    assert len(routes) >= 30, f"route walk looks broken: {routes}"

    vault_state.clear_key()
    try:
        for method, path in routes:
            resp = client.request(method, path)
            assert resp.status_code == 423, (
                f"{method} {path} answered {resp.status_code} while locked"
            )
            assert resp.json() == {"detail": "vault_locked"}
    finally:
        vault_state.set_key(TEST_VAULT_KEY)


def test_gate_prefix_edge_cases_stay_closed(client):
    """Lookalike prefixes must NOT pass the gate while locked. The dot-dot
    path asserts the end-to-end property: however the stack treats it
    (client normalization, gate ".." exclusion, router literalism), it can
    never answer with data while locked."""
    vault_state.clear_key()
    try:
        # Not a vault route at all (no slash) - must be gated.
        assert client.get("/api/v1vault").status_code == 423
        # Dot-dot smuggling around the vault exemption.
        assert client.get("/api/v1/vault/../chats").status_code == 423
        # The real vault route stays reachable while locked (it is the way in).
        assert client.get("/api/v1/vault/status").status_code == 200
    finally:
        vault_state.set_key(TEST_VAULT_KEY)


def test_data_dir_env_override_wins(monkeypatch, tmp_path):
    import config

    override = str(tmp_path / "custom-data")
    monkeypatch.setenv("ELYSIUM_DATA_DIR", override)
    assert config._resolve_data_dir() == Path(override)


def test_data_dir_frozen_goes_to_localappdata(monkeypatch, tmp_path):
    import config

    monkeypatch.delenv("ELYSIUM_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert config._resolve_data_dir() == tmp_path / "Elysium"


def test_data_dir_dev_stays_beside_code(monkeypatch):
    import config

    monkeypatch.delenv("ELYSIUM_DATA_DIR", raising=False)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert config._resolve_data_dir() == Path(config.__file__).resolve().parent


class TestPerMonitorDpi:
    """V10 - the opt-in sharpness switch.

    pywebview calls the Vista-era `SetProcessDPIAware()`, which means the app
    is sharp only at the primary monitor's login-time scale; anywhere else
    Windows bitmap-stretches it. This is the switch that fixes that, and the
    thing worth pinning is that it is REAL - the first version returned False
    every time because a HANDLE was being marshalled as a 32-bit int.
    """

    def test_it_is_on_by_default_now_that_a_real_window_confirmed_it(self,
                                                                     monkeypatch):
        """Verified on a real screen: window normal size, everything sharp.

        What that launch proved is "does not break the window" - the machine
        runs at 100% scale, so the sharpness win belongs to the fractional-
        scaling and mixed-DPI setups this exists for.
        """
        import sys

        import run_app

        monkeypatch.delenv("ELYSIUM_PER_MONITOR_DPI", raising=False)
        if sys.platform != "win32":
            assert run_app._try_per_monitor_dpi() is False
        else:
            assert run_app._try_per_monitor_dpi() is True

    def test_there_is_a_way_out_if_a_display_setup_disagrees(self, monkeypatch):
        import run_app

        monkeypatch.setenv("ELYSIUM_PER_MONITOR_DPI", "0")
        assert run_app._try_per_monitor_dpi() is False

    # Removed 2026-08-10: test_the_handle_is_marshalled_as_a_pointer_not_an_int.
    #
    # It read the function's source for "argtypes" and "c_void_p". The failure
    # it guarded against - a missing argtypes making the call silently return 0
    # on 64-bit - is exactly what the behavioural test above
    # (test_it_is_on_by_default_now_that_a_real_window_confirmed_it) already
    # catches, by calling the real function on the real machine and asserting
    # it returns True. A source match adds nothing there and would pass for a
    # mention in a comment.

    def test_it_runs_before_the_window_is_created(self, monkeypatch):
        """A process's DPI awareness can only be set once and the first caller
        wins - after pywebview starts it is too late.

        Rewritten 2026-08-19: this used to read main()'s own source and check
        which of two names appeared first in the text, which pins prose, not
        execution - a helper called from a comment, or moved into a branch
        that never runs, would still read "before" here. This runs the real
        main() with every side effect it has (the single-instance mutex, the
        registry hardening, the socket, the server, the window itself)
        stubbed to a recorder or a no-op, and reads the ORDER two calls
        actually happened in.
        """
        import types
        from unittest.mock import MagicMock

        import run_app

        order: list[str] = []

        # Every side effect main() has before and around the window, stubbed
        # so this test can run it for real without touching the registry, a
        # real socket, a real server thread, or a real window. None of these
        # are the thing under test; _try_per_monitor_dpi and create_window are.
        monkeypatch.delenv("ELYSIUM_SELFTEST", raising=False)
        monkeypatch.setattr(run_app, "_setup_frozen_logging", lambda: None)
        monkeypatch.setattr(run_app, "enforce_single_instance", lambda: None)
        monkeypatch.setattr(run_app.win_hardening, "harden", lambda *a, **k: None)
        monkeypatch.setattr(run_app.launch_token, "issue", lambda: None)
        monkeypatch.setattr(run_app.launch_token, "configured", lambda: "tok")
        monkeypatch.setattr(run_app, "_stop_voice_worker", lambda *a, **k: None)
        monkeypatch.setattr(
            run_app, "bind_app_socket",
            lambda: types.SimpleNamespace(
                getsockname=lambda: ("127.0.0.1", 55123)))
        monkeypatch.setattr(run_app, "serve", lambda sock: None)
        monkeypatch.setattr(run_app, "_webview2_installed", lambda: True)
        monkeypatch.setattr(
            run_app, "wait_until_ready", lambda url, timeout=30.0: True)
        monkeypatch.setattr(run_app, "clear_session_residue", lambda profile: {})
        monkeypatch.setattr(run_app.webview, "start", lambda *a, **k: None)
        monkeypatch.setattr(run_app.browser_profile, "purge", lambda profile: 0)

        # POSITIVE CONTROL and GROUND both live in `order`: if
        # _try_per_monitor_dpi or create_window stopped being called at all,
        # the missing entry (or the IndexError from a short list) fails the
        # assert below just as loudly as the wrong order would.
        monkeypatch.setattr(
            run_app, "_try_per_monitor_dpi",
            lambda: order.append("dpi") or True)

        def fake_create_window(*a, **k):
            order.append("create_window")
            return MagicMock()

        monkeypatch.setattr(run_app.webview, "create_window", fake_create_window)

        run_app.main()

        assert order == ["dpi", "create_window"], (
            f"expected the DPI call before the window is created, got {order}"
        )


class TestApiIsNeverCachedToDisk:
    """Audit finding (2026-07-25): the packaged app runs WebView2 with a
    PERSISTENT profile, so Chromium was free to write the JSON data routes -
    the whole conversation, characters and personas - to its on-disk HTTP cache
    in plaintext, outside the encrypted vault. Uploads, audio and the SSE
    stream had each been given a policy already; the ordinary data routes, the
    largest volume of the most sensitive content, were the gap.
    """

    def test_data_routes_are_no_store(self, client):
        """Swept from the route table, not from a list typed here.

        This named three paths: settings, chats, characters. Narrowing the
        middleware's prefix to miss personas, models, tts or uploads would
        have left those routers writing plaintext JSON into the WebView2 disk
        cache with this test still green. The same sweep pattern the CSRF
        shield already uses on its own route table.
        """
        import main

        checked = 0
        for route in main.app.routes:
            path = getattr(route, "path", "")
            methods = getattr(route, "methods", set()) or set()
            if not path.startswith("/api/v1/") or "GET" not in methods:
                continue
            if "{" in path:          # needs an id that may not exist
                continue
            got = client.get(path).headers.get("cache-control")
            assert got == "no-store", f"{path} -> {got!r}"
            checked += 1
        # Floor: an empty sweep would assert nothing at all.
        assert checked >= 8, f"only {checked} data routes swept"

    def test_a_locked_423_is_covered_too(self):
        """The vault gate short-circuits without calling downstream, so this
        only holds while the policy is the OUTERMOST middleware layer - which
        is the whole reason it is registered last."""
        from fastapi.testclient import TestClient

        import main

        with TestClient(main.app) as c:          # a fresh app: vault locked
            r = c.get("/api/v1/chats")
            assert r.status_code == 423
            assert r.headers.get("cache-control") == "no-store"

    def test_the_spa_bundle_stays_cacheable(self):
        # Hashed static output SHOULD be cached; making it uncacheable would
        # slow every launch to protect nothing.
        from fastapi.testclient import TestClient

        import main

        with TestClient(main.app) as c:
            assert c.get("/").headers.get("cache-control") is None


class TestAuditRegressions2026_07_25:
    """Three findings from the whole-repo audit, each a silent failure."""

    def test_a_non_numeric_proxy_port_is_refused_before_it_is_saved(self):
        """It used to pass validation, reach the vault, and only fail later
        inside the httpx client build - an opaque 500 with all networking
        already broken and the settings page showing the value as saved."""
        from fastapi import HTTPException

        from routers.settings import _validate_proxy_url

        with pytest.raises(HTTPException) as exc:
            _validate_proxy_url("http://host:notaport")
        assert exc.value.detail == "proxy_url_invalid"
        _validate_proxy_url("http://host:8080")      # still accepted

    def test_uvicorn_logs_reach_the_file_the_error_dialog_points_at(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch,
    ):
        """uvicorn installs its own handlers with propagate=False, so a
        windowed build wrote every SERVER log to a stderr that does not exist -
        while the failure dialog promised the details were in elysium.log.

        Rewritten 2026-08-10: this read the function's SOURCE and looked for
        the strings "uvicorn.error" and "addHandler". That passes for a
        mention in a comment and fails for a correct rewrite, on the one path
        whose whole job is to be readable when nothing else can report. Now it
        runs the function and reads the file back.
        """
        import logging

        import config
        import run_app

        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(config, "DATA_DIR", tmp_path)

        run_app._setup_frozen_logging()
        try:
            logging.getLogger("uvicorn.error").warning("probe-from-uvicorn")
            logging.getLogger("uvicorn.access").warning("probe-from-access")
            for handler in logging.getLogger("uvicorn.error").handlers:
                handler.flush()
            written = (tmp_path / "elysium.log").read_text(
                encoding="utf-8", errors="replace")
        finally:
            # This function attaches handlers to process-global loggers, so it
            # cannot be left installed: the next test would write into a
            # tmp_path that no longer exists.
            for name in ("", "uvicorn", "uvicorn.error", "uvicorn.access"):
                logger = logging.getLogger(name)
                for handler in list(logger.handlers):
                    logger.removeHandler(handler)
                    handler.close()

        assert "probe-from-uvicorn" in written, (
            "a uvicorn server log did not reach the file the dialog names"
        )
        assert "probe-from-access" in written

    def test_a_refused_load_does_not_orphan_the_resident_model(
        self, monkeypatch, tmp_path
    ) -> None:
        """A pre-spawn refusal leaves the running worker untouched, so the
        state must keep describing it. Wiping the identity reported "nothing
        loaded" while a process still held its VRAM.

        Rewritten 2026-08-19: this used to read VoiceHost.load's own source
        and check that the words "prior" and "STATE_LOADED" both appeared in
        it, which passes for either word sitting in a comment. This loads a
        real model into the fake worker, refuses a SECOND load on a real VRAM
        check, and reads what the host reports and what worker is actually
        still alive afterwards.
        """
        import sys as _sys
        from pathlib import Path as _Path

        import config
        from tts import host as tts_host
        from tts import runtimes, vram
        from tts.base import DetectedModel
        from tts.errors import TTS_INSUFFICIENT_VRAM
        from tts.worker_client import WorkerFailure

        fake_worker = str(_Path(__file__).resolve().parent / "fake_worker.py")

        reg = tmp_path / "voice" / "runtimes.json"
        reg.parent.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(config, "TTS_RUNTIMES_PATH", str(reg), raising=False)
        monkeypatch.setattr(
            config, "TTS_CACHE_DIR", str(tmp_path / "cache"), raising=False)
        runtimes.register("fish_s2", _sys.executable)

        def fake_smi(*, total=16303, free=14000, used=2303):
            monkeypatch.setattr(
                vram, "_run_smi",
                lambda: "NVIDIA GeForce RTX 5080, %d, %d, %d\n" % (
                    total, free, used))

        fake_smi()
        host = tts_host.VoiceHost()
        host.script_resolver = lambda engine_id: fake_worker
        try:
            model = DetectedModel(uid="resident", engine_id="fish_s2",
                                  name="s", path="/models/resident")
            host.load(model, {})
            client = host._client
            assert host.snapshot()["state"] == "loaded", "setup did not load"

            # GROUND: nothing fits now, so this second load must be refused
            # BEFORE it touches the worker that is already up.
            fake_smi(free=400, used=15903)
            with pytest.raises(WorkerFailure) as exc:
                host.load(
                    DetectedModel(uid="other", engine_id="fish_s2", name="o",
                                  path="/models/other"),
                    {},
                )
            assert exc.value.code == TTS_INSUFFICIENT_VRAM

            # POSITIVE CONTROL: the refusal above proves the pre-spawn check
            # ran. What follows is the actual guarantee - the resident model
            # is still what the host reports and its worker is still alive,
            # not wiped by a load it never let past preflight.
            snap = host.snapshot()
            assert snap["state"] == "loaded", (
                "a refused second load wiped the resident model's state")
            assert snap["uid"] == "resident", (
                "a refused second load reported the wrong model as resident")
            assert host._client is client, (
                "the resident worker was replaced by a load that was refused")
            assert client.alive, (
                "the resident worker was ended by a load it never reached")
        finally:
            host.unload("test teardown")


# ── Audit: the base-URL override warning must reach the LOG FILE ────────────
#
# config.py's warning is the only guard against a poisoned environment
# redirecting every completion - Authorization header included - to an
# arbitrary host. It fired at config IMPORT time, and run_app.py imports config
# for DATA_DIR before installing the file handler, so in the shipped
# console=False build it went to logging.lastResort (a stderr no windowed exe
# can show) and never reached elysium.log.


def test_the_override_warning_is_callable_after_logging_exists():
    import config
    assert callable(config.warn_if_base_url_overridden)


def test_config_import_alone_emits_nothing(monkeypatch, caplog):
    """Import must be silent: whoever imports first would otherwise decide
    whether the warning is visible."""
    import importlib
    import config

    monkeypatch.setenv("OPENROUTER_BASE_URL", "http://evil.example/v1")
    with caplog.at_level("WARNING"):
        reloaded = importlib.reload(config)
    assert "evil.example" not in caplog.text
    assert reloaded.OPENROUTER_BASE_URL_OVERRIDDEN is True

    # ...and the report lands when the caller asks for it.
    with caplog.at_level("WARNING"):
        reloaded.warn_if_base_url_overridden()
    assert "evil.example" in caplog.text
    assert "Authorization" in caplog.text

    # ONCE. run_app (frozen) and main (dev) both call it, and in the packaged
    # app both run - the shipped log carried the same warning twice.
    caplog.clear()
    with caplog.at_level("WARNING"):
        reloaded.warn_if_base_url_overridden()
    assert caplog.text == ""

    monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)
    importlib.reload(config)


def test_the_default_destination_warns_about_nothing(caplog):
    import config
    assert config.OPENROUTER_BASE_URL_OVERRIDDEN is False
    with caplog.at_level("WARNING"):
        config.warn_if_base_url_overridden()
    assert caplog.text == ""


def test_run_app_reports_the_override_after_installing_its_handler(
    tmp_path, monkeypatch
):
    """Ordering is the whole bug: the call must come AFTER basicConfig, or the
    one guard against a hijacked API destination goes to a stderr no windowed
    exe can show and never reaches elysium.log.

    Rewritten 2026-08-19: this used to read run_app.py's own text and check
    which of two names came first in it - the same class of test as the other
    two on this page (VoiceHost.load, main()'s DPI call), fixed for the same
    reason: source order is not execution order, and a rename or a comment
    passes it. This runs the real frozen-logging setup with a real base-URL
    override in the environment and reads the file the warning is promised to
    reach.
    """
    import importlib
    import logging

    import config
    import run_app

    monkeypatch.setenv("OPENROUTER_BASE_URL", "http://evil.example/v1")
    reloaded = importlib.reload(config)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(reloaded, "DATA_DIR", tmp_path)

    # logging.basicConfig() is a no-op once the root logger already has a
    # handler - which pytest's own log-capture plugin has installed by the
    # time any test runs. A real launch starts from a bare interpreter with
    # no root handler at all, so that is what this test has to recreate for
    # basicConfig to do anything, or it would be testing pytest's logging
    # setup instead of run_app's.
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    for handler in saved_handlers:
        root.removeHandler(handler)

    run_app._setup_frozen_logging()
    try:
        for handler in logging.getLogger().handlers:
            handler.flush()
        written = (tmp_path / "elysium.log").read_text(
            encoding="utf-8", errors="replace")
    finally:
        # Same cleanup as the neighbouring uvicorn-log test: this installs
        # handlers on process-global loggers, and leaving them up would have
        # the next test writing into a tmp_path that no longer exists.
        for name in ("", "uvicorn", "uvicorn.error", "uvicorn.access"):
            logger = logging.getLogger(name)
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()
        for handler in saved_handlers:
            root.addHandler(handler)
        root.setLevel(saved_level)
        monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)
        # sys.frozen must go back BEFORE this reload, not after: monkeypatch
        # only restores it once this function returns, and config.py reads
        # sys.frozen at IMPORT time to compute FRONTEND_ORIGINS. Reloading
        # while frozen was still True baked an empty FRONTEND_ORIGINS into
        # the live config module and left it there - this test still passed,
        # and test_trust_boundary.py failed instead, in whatever unrelated
        # test happened to run after this one and read config next.
        monkeypatch.setattr(sys, "frozen", False, raising=False)
        importlib.reload(config)

    # GROUND: swap the two calls inside _setup_frozen_logging (warn before
    # basicConfig) and this file disappears or stays empty - logging.warning
    # has nowhere configured to write yet, so the message falls through to
    # logging.lastResort, which this test never reads.
    assert "OPENROUTER_BASE_URL overridden" in written, (
        "the override warning did not reach elysium.log"
    )
    assert "evil.example" in written
    assert "Authorization" in written


# ── Audit: a random port every launch made the persistent profile useless ───
#
# localStorage and IndexedDB are keyed by scheme://host:port. bind((HOST, 0))
# gave every launch a new origin, so the WebView2 profile that private_mode=
# False was turned OFF to keep delivered none of it: font size, contrast preset,
# narration style, the wallpaper and the last-open chat reverted every time, and
# the dead origins' storage accumulated on disk (twelve buckets on the shipping
# profile, one per port it had ever used).


def test_the_port_is_remembered_and_reused(tmp_path, monkeypatch):
    import config
    import run_app

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)

    first = run_app.bind_app_socket()
    try:
        port = first.getsockname()[1]
        assert port != 0
        assert (tmp_path / "port").read_text(encoding="utf-8").strip() == str(port)
    finally:
        first.close()

    second = run_app.bind_app_socket()
    try:
        assert second.getsockname()[1] == port, "the origin changed between launches"
    finally:
        second.close()


def test_a_busy_remembered_port_falls_back_instead_of_refusing(tmp_path, monkeypatch):
    """A lost preference beats a refusal to start."""
    import config
    import run_app

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)

    held = run_app.bind_app_socket()
    try:
        taken = held.getsockname()[1]
        other = run_app.bind_app_socket()
        try:
            assert other.getsockname()[1] != taken
            # ...and the new one is what the NEXT launch will try.
            assert (tmp_path / "port").read_text(encoding="utf-8").strip() == str(
                other.getsockname()[1]
            )
        finally:
            other.close()
    finally:
        held.close()


def test_a_corrupt_port_file_is_ignored(tmp_path, monkeypatch):
    import config
    import run_app

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    (tmp_path / "port").write_text("not a port", encoding="utf-8")
    sock = run_app.bind_app_socket()
    try:
        assert 1024 <= sock.getsockname()[1] <= 65535
    finally:
        sock.close()


def test_a_privileged_port_is_not_trusted(tmp_path, monkeypatch):
    import config
    import run_app

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    (tmp_path / "port").write_text("80", encoding="utf-8")
    assert run_app._remembered_port() == 0


def test_the_completions_header_describes_what_the_module_does():
    """Audit LOW: the header of the app's most load-bearing backend module
    declared "Text-only, non-streaming ... No streaming (stream: true)" and
    listed one route, while the file implements three SSE endpoints and builds
    image_url parts. The docstring is what the next change gets built on."""
    # KADEME 20b removed the docstring-freshness half of this test. It read
    # completions.py's own header and asserted the Scope block no longer said
    # "No streaming"/"Text-only" and did say "image_url" and the three route
    # names. A module docstring has no observable effect on any call, any
    # response or any file; nothing but a human can tell whether it is true,
    # and a test that pins prose goes red for a rewording rather than for a
    # regression. Fix the header by reading it, not by failing a build.
    #
    # What remains below is the half that answers to the CODE: the three
    # streaming handlers still exist under those names and are still
    # coroutines. That is the part a text match was always blind to.
    import inspect

    import routers.completions as completions_module

    for fn in ("complete_chat_stream", "regenerate_message_stream",
               "edit_message_stream"):
        handler = getattr(completions_module, fn, None)
        assert handler is not None, f"{fn} is named in the header but gone"
        assert inspect.iscoroutinefunction(handler), (
            f"{fn} is no longer an async handler"
        )
