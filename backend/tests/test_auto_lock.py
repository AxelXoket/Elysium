"""An unlocked vault stays unlocked until somebody remembers to close it.

Every other defence in this app is about data at rest. This is the only one
that shortens the window in which the data is NOT at rest - the hours a window
sits open on a desk, on a shared machine, on a laptop somebody walks away from.
Without it the encryption protects a stolen disk and nothing else.

The two things that could go wrong are opposite, and both are here: a vault
that never locks, and a vault that locks in the middle of a reply somebody is
reading.
"""
from __future__ import annotations

import asyncio

import pytest

import auto_lock
import vault_state


@pytest.fixture(autouse=True)
def _fresh_clock():
    vault_state.reset_idle_clock()
    had_key = vault_state.is_unlocked()
    yield
    vault_state.reset_idle_clock()
    # Several tests here lock the vault on purpose. Leaving it locked would
    # be a state one test hands to the next, which is how a suite grows an
    # order dependency nobody can see.
    if had_key and not vault_state.is_unlocked():
        from tests.conftest import TEST_VAULT_KEY
        vault_state.set_key(TEST_VAULT_KEY)


class TestTheSettingIsReadSafely:
    def test_never_configured_means_the_default_not_off(self, client) -> None:
        """An unlocked vault on an unattended machine is the thing this module
        exists to prevent, so leaving it open has to be something the user
        CHOSE rather than something they never got around to."""
        assert auto_lock.configured_minutes() == auto_lock.DEFAULT_MINUTES == 5

    def test_choosing_off_still_means_off(self, client) -> None:
        """A default may not overrule a choice. Somebody who turned it off
        turned it off."""
        from database import set_setting
        set_setting(auto_lock.SETTING, "0")
        assert auto_lock.configured_minutes() == 0

    def test_the_screen_and_the_watchdog_read_the_same_number(self, client) -> None:
        """These were two separate copies of the same parsing. That survived
        while both said "absent means off"; with a default it would have meant
        the panel reading "never" while the vault locked every five minutes."""
        from routers.settings import _read_auto_lock

        shown = client.get("/api/v1/settings").json()["auto_lock_minutes"]
        assert shown == auto_lock.configured_minutes() == auto_lock.DEFAULT_MINUTES
        for raw in (None, "", "0", "15", "nonsense"):
            assert _read_auto_lock(raw) == auto_lock.minutes_from_raw(raw), raw

    def test_a_configured_value_is_read(self, client) -> None:
        from database import set_setting
        set_setting(auto_lock.SETTING, "15")
        assert auto_lock.configured_minutes() == 15

    @pytest.mark.parametrize("raw", ["off", "-5", "nonsense", "0"])
    def test_a_value_that_is_not_a_timeout_reads_as_off(
        self, client, raw: str
    ) -> None:
        # Off, never "lock now". Guessing a timeout the user did not choose
        # would throw away the session they are in the middle of.
        from database import set_setting
        set_setting(auto_lock.SETTING, raw)
        assert auto_lock.configured_minutes() == 0

    def test_a_locked_vault_reads_as_off_instead_of_raising(
        self, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The watchdog runs forever; an exception here would kill it silently
        # and disable auto-lock for the whole session while the user believed
        # it was on.
        monkeypatch.setattr("database.get_setting", lambda name: (
            _ for _ in ()).throw(RuntimeError("vault_locked")))
        assert auto_lock.configured_minutes() == 0

    # Removed 2026-08-10: test_a_value_below_the_floor_is_treated_as_off.
    # It set "0" and asserted 0, which the parametrized case above already
    # covers ("0" is in its table). Byte-for-byte the same exercise.


class TestTheIdleClock:
    def test_a_fresh_clock_is_not_idle(self) -> None:
        assert vault_state.idle_seconds() < 1.0

    def test_a_request_in_flight_means_zero_idle(self) -> None:
        # THE property that keeps a streamed reply alive. A forty-minute
        # stream sends nothing after its first byte, so a clock that only
        # looked at arrival times would lock the vault out from under it.
        vault_state.enter_request()
        try:
            vault_state._last_activity -= 100_000  # pretend a long time passed
            assert vault_state.idle_seconds() == 0.0
        finally:
            vault_state.leave_request()

    def test_the_clock_restarts_when_a_request_finishes(self) -> None:
        vault_state.enter_request()
        vault_state._last_activity -= 100_000
        vault_state.leave_request()
        assert vault_state.idle_seconds() < 1.0

    def test_unbalanced_completions_cannot_drive_the_count_negative(
        self
    ) -> None:
        # A negative count would read as "a request is in flight" forever and
        # silently disable auto-lock.
        vault_state.leave_request()
        vault_state.leave_request()
        vault_state._last_activity -= 100_000
        assert vault_state.idle_seconds() > 1.0

    def test_touch_marks_activity(self) -> None:
        vault_state._last_activity -= 100_000
        vault_state.touch()
        assert vault_state.idle_seconds() < 1.0


class TestWhenItDecidesToLock:
    def test_a_locked_vault_is_not_locked_again(self, client) -> None:
        vault_state.clear_key()
        try:
            assert auto_lock.should_lock() is False
        finally:
            from tests.conftest import TEST_VAULT_KEY
            vault_state.set_key(TEST_VAULT_KEY)

    def test_an_idle_vault_with_the_setting_off_stays_open(self, client
                                                            ) -> None:
        # Explicitly off. This used to rely on "no setting means off", which
        # made it a test of the DEFAULT wearing the name of a test about the
        # off switch - and it broke the day the default changed, which is
        # exactly the right time to find out.
        from database import set_setting
        set_setting(auto_lock.SETTING, "0")
        vault_state._last_activity -= 100_000
        assert auto_lock.should_lock() is False

    def test_an_idle_vault_nobody_configured_locks_on_the_default(
        self, client
    ) -> None:
        vault_state._last_activity -= auto_lock.DEFAULT_MINUTES * 60 + 1
        assert auto_lock.should_lock() is True

    def test_an_idle_vault_with_the_setting_on_locks(self, client) -> None:
        from database import set_setting
        set_setting(auto_lock.SETTING, "5")
        vault_state._last_activity -= 5 * 60 + 1
        assert auto_lock.should_lock() is True

    def test_a_busy_vault_does_not_lock_however_long_it_has_been(
        self, client
    ) -> None:
        from database import set_setting
        set_setting(auto_lock.SETTING, "1")
        vault_state.enter_request()
        try:
            vault_state._last_activity -= 100_000
            assert auto_lock.should_lock() is False
        finally:
            vault_state.leave_request()

    def test_it_waits_for_the_whole_timeout(self, client) -> None:
        # Off by one in the other direction is a vault that locks early, which
        # is how a user learns to turn the feature off.
        from database import set_setting
        set_setting(auto_lock.SETTING, "10")
        vault_state._last_activity -= 10 * 60 - 5
        assert auto_lock.should_lock() is False


class TestTheLockItPerforms:
    @pytest.mark.anyio
    async def test_it_is_the_same_lock_the_button_performs(
        self, anyio_backend, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A second, slightly different lock would be a second, slightly weaker
        # one: the key cleared but the voice worker still holding audio, or
        # the HTTP client still holding the proxy URL it snapshotted.
        import network_client
        import routers.vault as vault_router

        torn_down: list[str] = []
        monkeypatch.setattr(vault_router, "_lock_down_voice",
                            lambda: _noop(torn_down))

        async def note_client_closed() -> None:
            torn_down.append("client")

        monkeypatch.setattr(network_client, "close_client", note_client_closed)

        await auto_lock.lock_now()

        assert vault_state.is_unlocked() is False
        # BOTH, not just the voice worker. The HTTP client snapshots the proxy
        # URL - a secret - at build time, and the first cut of this test named
        # that in its docstring and then never checked it.
        assert sorted(torn_down) == ["client", "voice"]
        from tests.conftest import TEST_VAULT_KEY
        vault_state.set_key(TEST_VAULT_KEY)


    @pytest.mark.anyio
    async def test_the_idle_lock_reaches_the_voice_host(
        self, anyio_backend, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The test above stubs the voice teardown out, so it proves the call
        is made and nothing about what it reaches. That gap matters more now
        that auto-lock is ON by default: the vault locking itself is also the
        moment the GPU is supposed to be handed back, and somebody in a game
        will notice if it is not.

        The host's own side - on_vault_locked() unloading and wiping - is
        pinned in test_tts_lock_lifecycle. This is the link between them.
        """
        import tts.host as tts_host

        reached: list[str] = []

        class _Spy:
            def on_vault_locked(self) -> list[str]:
                reached.append("unloaded")
                return []

            def wipe_cache(self) -> int:                 # pragma: no cover
                return 0

        monkeypatch.setattr(tts_host, "get_host", lambda: _Spy())

        await auto_lock.lock_now()

        assert reached == ["unloaded"], (
            "the idle lock never reached the voice host, so the model stayed "
            "in VRAM while the vault reported itself locked"
        )
        assert vault_state.is_unlocked() is False
        from tests.conftest import TEST_VAULT_KEY
        vault_state.set_key(TEST_VAULT_KEY)


async def _noop(record: list[str]) -> list[str]:
    record.append("voice")
    return []


class TestTheWatchdogSurvivesItself:
    @pytest.mark.anyio
    async def test_a_failing_check_does_not_end_the_loop(
        self, anyio_backend, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Created once for the process lifetime, so one unhandled exception
        # would disable auto-lock until the app restarted, silently.
        calls = {"n": 0}

        def explode() -> bool:
            calls["n"] += 1
            raise RuntimeError("something went wrong")

        monkeypatch.setattr(auto_lock, "should_lock", explode)
        task = asyncio.ensure_future(auto_lock.watch(tick=0.01))
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert calls["n"] > 2, "the loop stopped after the first failure"

    @pytest.mark.anyio
    async def test_it_locks_when_the_check_says_so(
        self, anyio_backend, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        locked = {"n": 0}

        async def fake_lock() -> None:
            locked["n"] += 1

        monkeypatch.setattr(auto_lock, "should_lock", lambda: True)
        monkeypatch.setattr(auto_lock, "lock_now", fake_lock)
        monkeypatch.setattr(auto_lock, "configured_minutes", lambda: 5)
        task = asyncio.ensure_future(auto_lock.watch(tick=0.01))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert locked["n"] >= 1


class TestTheRoute:
    def test_the_setting_round_trips(self, client) -> None:
        assert client.post("/api/v1/settings/auto-lock",
                           json={"auto_lock_minutes": 20}).status_code == 200
        assert client.get("/api/v1/settings").json()["auto_lock_minutes"] == 20

    def test_zero_turns_it_off(self, client) -> None:
        client.post("/api/v1/settings/auto-lock",
                    json={"auto_lock_minutes": 20})
        client.post("/api/v1/settings/auto-lock",
                    json={"auto_lock_minutes": 0})
        assert client.get("/api/v1/settings").json()["auto_lock_minutes"] == 0

    def test_one_minute_is_allowed(self, client) -> None:
        # A tight timeout is a legitimate choice for somebody who wants one.
        # The route refuses values that are not timeouts at all, not values it
        # disagrees with.
        assert client.post("/api/v1/settings/auto-lock",
                           json={"auto_lock_minutes": 1}).status_code == 200
        assert client.get("/api/v1/settings").json()["auto_lock_minutes"] == 1

    def test_an_absurd_value_is_refused(self, client) -> None:
        assert client.post("/api/v1/settings/auto-lock",
                           json={"auto_lock_minutes": 100000}
                           ).status_code == 422

    def test_a_negative_value_is_refused(self, client) -> None:
        assert client.post("/api/v1/settings/auto-lock",
                           json={"auto_lock_minutes": -1}).status_code == 422

    def test_polling_the_vault_status_does_not_count_as_activity(
        self, client
    ) -> None:
        # The frontend asks for /vault/status on a timer whether or not
        # anybody is at the keyboard. Counting it would mean the vault never
        # goes idle and auto-lock never fires - a feature that reports itself
        # as on and does nothing.
        vault_state._last_activity -= 100_000
        client.get("/api/v1/vault/status")
        assert vault_state.idle_seconds() > 1.0

    def test_a_real_request_does_count(self, client) -> None:
        vault_state._last_activity -= 100_000
        client.get("/api/v1/settings")
        assert vault_state.idle_seconds() < 1.0


class TestLockingActuallyDestroysTheKey:
    """"Locked" is a claim about what is readable in this process.

    The key was held as `bytes`, which is immutable: clearing it set a
    reference to None and handed the buffer to the garbage collector, which
    may not run for a long time. Until it did, the 32 bytes that open every
    chat sat in the heap of a process whose window said locked - readable by a
    debugger, a crash dump, or anything that can attach.
    """

    def test_the_bytes_are_overwritten_not_merely_dropped(self) -> None:
        secret = bytes(range(1, 33))
        vault_state.set_key(secret)
        # The module's own buffer. get_key() hands out a snapshot on purpose
        # (a worker thread must not be reading the live bytes while the idle
        # watchdog zeroes them), so the thing under test has to be reached
        # directly.
        held = vault_state._db_key
        assert bytes(held) == secret

        vault_state.clear_key()

        assert bytes(held) == bytes(32), "the key was dropped, not destroyed"
        assert vault_state.is_unlocked() is False

    def test_replacing_a_key_destroys_the_previous_one(self) -> None:
        # A passphrase change and the KDF upgrade both do this. The old key
        # opens every snapshot the rotation has not re-keyed yet.
        first = bytes(range(1, 33))
        vault_state.set_key(first)
        held = vault_state._db_key

        vault_state.set_key(bytes(range(33, 65)))

        assert bytes(held) == bytes(32)
        vault_state.clear_key()

    def test_the_caller_s_own_copy_is_not_stolen(self) -> None:
        # set_key takes a copy. Zeroing the caller's array instead would
        # destroy a key crypto.py is still using mid-rotation.
        caller_owned = bytearray(range(1, 33))
        vault_state.set_key(caller_owned)
        vault_state.clear_key()
        assert bytes(caller_owned) == bytes(range(1, 33))

    def test_a_locked_vault_still_refuses_to_hand_anything_out(self) -> None:
        vault_state.set_key(bytes(range(1, 33)))
        vault_state.clear_key()
        with pytest.raises(vault_state.VaultLockedError):
            vault_state.get_key()


class TestAStreamedReplyIsNotInterrupted:
    """The failure the in-flight counter was built for, and did not prevent.

    BaseHTTPMiddleware returns from call_next the moment the endpoint sends
    http.response.start - and StreamingResponse sends that BEFORE it touches
    its body iterator. So a `finally: leave_request()` around call_next fired
    at the START of a stream, the counter dropped to zero, and the idle clock
    began running while the reply was still being generated. Auto-lock would
    then clear the key and close the HTTP client out from under it.

    This drives a real StreamingResponse through the real middleware stack,
    because that is the only place the bug lived.
    """

    @pytest.fixture()
    def probe_route(self):
        """A real StreamingResponse on the real app, removed afterwards.

        Leaving it behind is not cosmetic: the route-contract test asserts
        every path the app serves is documented, so a test fixture that
        outlives its test fails a completely unrelated guard.
        """
        import anyio
        from fastapi import APIRouter
        from fastapi.responses import StreamingResponse
        from main import app

        seen: list[float] = []
        router = APIRouter()

        @router.get("/api/v1/_idle_probe_stream")
        async def probe():
            async def body():
                for _ in range(3):
                    # Read the clock from INSIDE the stream: this is the
                    # window in which the vault used to look idle.
                    seen.append(vault_state.idle_seconds())
                    await anyio.sleep(0.02)
                    yield b"chunk\n"

            return StreamingResponse(body(), media_type="text/plain")

        # In FRONT of the StaticFiles mount at "/", which matches every path
        # and would answer 404 for anything registered after it.
        app.include_router(router)
        added = app.router.routes.pop()
        app.router.routes.insert(0, added)
        try:
            yield seen
        finally:
            app.router.routes.remove(added)

    def test_the_vault_is_never_idle_while_the_body_is_streaming(
        self, client, probe_route
    ) -> None:
        seen = probe_route
        vault_state._last_activity -= 100_000   # long since anything arrived

        with client.stream("GET", "/api/v1/_idle_probe_stream") as response:
            assert response.status_code == 200
            body = b"".join(response.iter_bytes())

        assert body.count(b"chunk") == 3
        assert seen, "the stream body never ran"
        assert all(value == 0.0 for value in seen), (
            f"the vault looked idle mid-stream: {seen}")

    def test_the_counter_is_released_once_the_body_finishes(
        self, client, probe_route
    ) -> None:
        # The other half. A counter that never comes back down disables
        # auto-lock for the rest of the session - a feature that reports
        # itself as on and does nothing.
        with client.stream("GET", "/api/v1/_idle_probe_stream") as response:
            b"".join(response.iter_bytes())

        vault_state._last_activity -= 100_000
        assert vault_state.idle_seconds() > 1.0

    def test_an_abandoned_stream_still_releases_it(self, client, probe_route
                                                    ) -> None:
        # A client that closes the tab mid-reply cancels the generator. If the
        # release only happened on the happy path, one abandoned stream would
        # leave the vault permanently "busy".
        with client.stream("GET", "/api/v1/_idle_probe_stream") as response:
            next(response.iter_bytes())          # take one chunk, then leave

        vault_state._last_activity -= 100_000
        assert vault_state.idle_seconds() > 1.0


class TestAPollIsNotAPerson:
    """The idle clock is what makes auto-lock mean anything, and a route the
    frontend asks for on a timer feeds it whether or not somebody is at the
    keyboard.

    This was one exempt route. Adding the notebook's status card - a 20-second
    poll, live whenever the Notes tab is open - silently disabled auto-lock
    entirely: the shortest configurable timeout is one minute, so the clock
    could never reach it. The vault stopped locking itself and nothing said
    so. The rule now lives in a named set, and the test is here so the next
    poller cannot arrive without one.
    """

    def _idle(self):
        import vault_state
        return vault_state.idle_seconds()

    def test_a_polled_route_does_not_reset_the_clock(self, client) -> None:
        import time

        import vault_state

        vault_state.enter_request()
        vault_state.leave_request()
        time.sleep(0.05)
        before = self._idle()

        assert client.get("/api/v1/notebook/worker").status_code == 200
        assert self._idle() >= before, (
            "a poll nobody made was counted as somebody being at the keyboard")

    def test_the_tts_active_poll_does_not_reset_the_clock(self, client) -> None:
        """/tts/active is polled every 1.5s while a voice model is loading
        (frontend/src/lib/query/tts.ts). Same shape as the notebook poll: at
        that rate the idle clock could never reach even a one-minute
        auto-lock for as long as a load runs."""
        import time

        import vault_state

        vault_state.enter_request()
        vault_state.leave_request()
        time.sleep(0.05)
        before = self._idle()

        resp = client.get("/api/v1/tts/active")
        assert resp.status_code == 200
        assert self._idle() >= before, (
            "a poll nobody made was counted as somebody being at the keyboard")
        # Operational metadata only - no message ever rides on this route.
        assert set(resp.json()) <= {
            "uid", "state", "engine_id", "vram_mb", "error_code",
            "readiness", "voice_installed",
        }

    def test_the_tts_install_status_poll_does_not_reset_the_clock(
        self, client
    ) -> None:
        """/tts/runtimes/{engine_id}/install is polled every 700ms while an
        engine install runs (same file) - the fastest poll in the app, and a
        running install can take minutes. The path carries an engine id, so
        the exemption in main.py has to be built from the real registry
        rather than a literal template string; this is what actually proves
        that construction reaches the route a real poll hits."""
        import time

        import vault_state
        from tts.registry import all_adapters

        engine_id = all_adapters()[0].engine_id
        vault_state.enter_request()
        vault_state.leave_request()
        time.sleep(0.05)
        before = self._idle()

        resp = client.get(f"/api/v1/tts/runtimes/{engine_id}/install")
        assert resp.status_code == 200
        assert self._idle() >= before, (
            "a poll nobody made was counted as somebody being at the keyboard")
        # Install progress only - engine ids, a state enum, log lines about
        # setup/download, error info, timestamps. No message ever rides here.
        assert set(resp.json()) <= {
            "engine_id", "state", "log", "error_code", "error_detail",
            "started_at", "finished_at", "running",
        }

    def test_an_ORDINARY_route_does(self, client) -> None:
        """The ground. Without it the test above is satisfied by a clock that
        never moves at all, which would mean the vault locks mid-sentence."""
        import time

        import vault_state

        vault_state.enter_request()
        vault_state.leave_request()
        time.sleep(0.05)
        assert self._idle() > 0

        assert client.get("/api/v1/chats").status_code == 200
        assert self._idle() < 0.05

    def test_every_exempt_route_actually_exists(self, client) -> None:
        """An exemption for a path the app does not serve is a typo that
        silently exempts nothing - which is how this broke the first time.

        vault_gate compares _IDLE_EXEMPT against request.url.path, the
        RESOLVED path a browser asks for - never a route template. A route
        with a path parameter (the tts install poll) therefore has to appear
        here as a concrete path, and a plain "in served" check (served being
        route TEMPLATES like "/api/v1/tts/runtimes/{engine_id}/install")
        would never find it - which would make this exact test the thing
        hiding the bug it exists to catch. compile_path turns each served
        template into the same regex Starlette itself matches requests
        against, so a concrete exempt path is checked the way the app really
        checks it.
        """
        import main
        from starlette.routing import compile_path

        served = [getattr(r, "path", "") for r in main.app.routes]
        patterns = [compile_path(p)[0] for p in served if p]
        for path in main._IDLE_EXEMPT:
            assert path in served or any(rx.match(path) for rx in patterns), path
