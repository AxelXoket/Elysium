"""POST /activity - the ping the frontend sends when a person did something.

Until this route existed the idle clock only moved for HTTP requests, so
twenty minutes of reading, scrolling and typing without pressing Send looked
to the backend like an empty room, and the vault locked on the fifth minute.
The frontend now watches real input events and sends this ping; what has to
hold on this side is narrow and is all measured here:

- it answers 204 with no body, and the idle clock resets - because the
  vault_gate middleware stamps every non-exempt /api/v1 request, and for no
  other reason;
- while the vault is locked it answers 423 and the clock does NOT move,
  because the gate returns before the stamp - which is why the route lives
  outside /vault, where the gate lets locked requests through;
- it is not in main._IDLE_EXEMPT, since a ping that did not count would be
  the feature switched off while reporting itself as on;
- vault_state.touch() is never called by it. A second path to the same
  clock would be a subset of what enter_request() already does, and the day
  somebody wires it "to be safe" is the day activity has two definitions.

Every test goes through the TestClient, because the behaviour under test
lives in the middleware, not in the handler: calling the handler function
directly would prove nothing about the 423 or about the stamp.
"""
from __future__ import annotations

import pytest

import vault_state

PING = "/api/v1/activity"


@pytest.fixture(autouse=True)
def _fresh_clock():
    # Same shape as test_auto_lock.py: the clock is process-global, and one
    # test here locks the vault on purpose. Neither state may reach the next
    # test.
    vault_state.reset_idle_clock()
    had_key = vault_state.is_unlocked()
    yield
    vault_state.reset_idle_clock()
    if had_key and not vault_state.is_unlocked():
        from tests.conftest import TEST_VAULT_KEY
        vault_state.set_key(TEST_VAULT_KEY)


def _age_the_clock() -> float:
    """Push the last activity far into the past; return the idle seconds seen.

    The return value is the ground every test below stands on: an assertion
    that the clock "reset" means nothing unless it was measurably old first.
    """
    vault_state._last_activity -= 100_000
    return vault_state.idle_seconds()


class TestThePingMovesTheClock:
    def test_it_answers_204_with_no_body_and_resets_the_idle_clock(
        self, client
    ) -> None:
        before = _age_the_clock()
        assert before > 1.0, "ground: the clock was not old, so a reset proves nothing"

        resp = client.post(PING)

        assert resp.status_code == 204
        assert resp.content == b""
        assert vault_state.idle_seconds() < 1.0, (
            "the ping did not reset the idle clock - either the route is in "
            "_IDLE_EXEMPT or it is not under /api/v1")

    def test_the_status_poll_beside_it_leaves_the_clock_alone(self, client) -> None:
        """Positive control for the assertion above: the same measurement can
        tell a counted request from an exempt one. Without this, a clock that
        reset for every request whatsoever would pass the test above."""
        before = _age_the_clock()
        assert before > 1.0

        assert client.get("/api/v1/vault/status").status_code == 200

        assert vault_state.idle_seconds() >= before - 1.0

    def test_it_is_not_in_the_exempt_set(self) -> None:
        """The membership twin of the first test. Adding the path to
        main._IDLE_EXEMPT turns both red: this one by name, that one because
        the clock stops moving."""
        import main

        assert PING not in main._IDLE_EXEMPT


class TestALockedVaultIsNotKeptAwake:
    def test_it_answers_423_and_the_clock_does_not_move(self, client) -> None:
        # The lock is performed the way test_auto_lock.py performs it: drop
        # the key. The fixture above puts it back.
        vault_state.clear_key()
        before = _age_the_clock()
        assert before > 1.0

        resp = client.post(PING)

        assert resp.status_code == 423
        assert resp.json() == {"detail": "vault_locked"}
        # Ground for "the clock did not move" is the unlocked test above,
        # where the identical request brought the same measurement under a
        # second. Here it stays where it was pushed.
        assert vault_state.idle_seconds() >= before - 1.0, (
            "a locked vault's idle clock was reset by the ping - the route "
            "is reachable while locked, so it must have moved under /vault")


class TestTouchIsNotWired:
    def test_the_route_never_calls_touch_and_the_clock_resets_anyway(
        self, client, monkeypatch
    ) -> None:
        calls: list[str] = []
        real_touch = vault_state.touch

        def counting_touch() -> None:
            calls.append("touch")
            real_touch()

        monkeypatch.setattr(vault_state, "touch", counting_touch)
        before = _age_the_clock()
        assert before > 1.0

        assert client.post(PING).status_code == 204

        assert calls == [], (
            "the route called vault_state.touch(); the clock is stamped by "
            "the middleware, and a second path to it is not wanted")
        assert vault_state.idle_seconds() < 1.0, (
            "the clock did not reset, so the middleware path is broken too")

        # Ground: the wrapper really can see a call. The direct call in
        # test_auto_lock.py::TestTheIdleClock::test_touch_marks_activity is
        # the same call, made here through the wrapper so the zero above is a
        # count and not a wrapper that never fired.
        vault_state.touch()
        assert calls == ["touch"]
