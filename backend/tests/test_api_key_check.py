"""POST /settings/api-key/check - is the key we already hold still good?

The app could always tell a good key from a bad one at SAVE time, and only
then. A key that stops working stops working later: revoked on the provider's
dashboard, expired, out of credit. Until this route existed the only ways to
learn that were to send a message and read the failure, or to retype the key
into the save box - which is not a test, it is a save.

WHAT THESE TESTS ARE ACTUALLY GUARDING

One thing above all: that "OpenRouter rejected your key" and "we could not
reach OpenRouter" stay two different answers. They are opposite instructions -
replace the key, versus the key was never even asked about - and every cheap
implementation of this feature collapses them into one failure. That is how a
proxy outage gets read as a dead key and a working key gets thrown away. The
ground test underneath them is the one that makes the rest mean anything: with
nobody pressing the button, nothing outbound happens at all.
"""

from __future__ import annotations

import json
import logging

import pytest

STORED_KEY = "sk-test-key"  # what the `client` fixture seeds into the vault


@pytest.fixture(autouse=True)
def _proxy_health_is_not_left_armed():
    """proxy_health caches its verdict in a PROCESS global, so an armed gate
    outlives the test that armed it. Teardown rather than a trailing call, for
    the reason test_proxy.py writes out: teardown runs on the way out of a
    failed assertion too."""
    yield
    import proxy_health
    proxy_health.invalidate_health_cache()


@pytest.fixture()
def verdict(monkeypatch):
    """Stand in for the live call and record what it was handed.

    Patched on the `openrouter` module rather than on routers.settings, because
    the handler imports the function inside its own body - the same shape
    test_proxy.py and test_settings_loop_blocking.py already rely on.
    """
    import openrouter

    calls: list[str] = []
    box = {"answer": "valid"}

    async def fake_validate(key: str) -> str:
        calls.append(key)
        return box["answer"]

    monkeypatch.setattr(openrouter, "validate_api_key", fake_validate)
    return type("Verdict", (), {"calls": calls, "box": box})()


def _check(client):
    return client.post("/api/v1/settings/api-key/check")


# ── the ground ───────────────────────────────────────────────────────────────

def test_nothing_is_checked_until_the_route_is_called(client, verdict):
    """THE GROUND. Without it every assertion below is satisfied by a route
    that checks the key on a timer, on startup, or on every settings read - and
    each of those puts the key on the wire without anybody asking.

    The three reads below are the ones a settings panel performs when it simply
    opens. None of them may cost an outbound request.
    """
    assert client.get("/api/v1/settings").status_code == 200
    assert client.get("/api/v1/settings").status_code == 200
    assert client.get("/api/v1/settings/proxy/health").status_code == 200

    assert verdict.calls == [], (
        "something checked the stored key with nobody pressing the button"
    )

    # And the positive control: pressing it DOES check, so the emptiness above
    # is a fact about the app and not about a fixture that never wired up.
    assert _check(client).status_code == 200
    assert verdict.calls == [STORED_KEY]


# ── the three outcomes ───────────────────────────────────────────────────────

def test_an_accepted_key_reports_valid(client, verdict):
    verdict.box["answer"] = "valid"
    resp = _check(client)
    assert resp.status_code == 200
    assert resp.json() == {"key_status": "valid"}


def test_a_rejected_key_reports_invalid_and_is_not_an_http_error(
    client, verdict,
):
    """200 with "invalid", deliberately, and NOT the 422 that save_api_key
    answers with.

    save_api_key is right to fail: the request it was given - store this key -
    did not happen. Here the request is "check it", and a check that comes back
    "rejected" succeeded. A 4xx would also send this through the frontend's
    parseApiError, where it would arrive wearing the same generic sentence as
    an unreachable provider, which is the one collapse this route exists to
    prevent.
    """
    verdict.box["answer"] = "invalid"
    resp = _check(client)
    assert resp.status_code == 200, "a rejection is an answer, not a failure"
    assert resp.json() == {"key_status": "invalid"}


def test_an_unreachable_provider_reports_something_else_entirely(
    client, verdict,
):
    """The distinction, asserted as a distinction rather than as two literals.

    Comparing each answer to its own expected string would still pass on the
    day both branches start returning the same word. This compares the two
    answers to EACH OTHER, which is the property the UI depends on.
    """
    verdict.box["answer"] = "invalid"
    rejected = _check(client).json()["key_status"]

    verdict.box["answer"] = "validation_unavailable"
    resp = _check(client)
    assert resp.status_code == 200
    unreachable = resp.json()["key_status"]

    assert unreachable == "validation_unavailable"
    assert unreachable != rejected, (
        "a rejected key and an unreachable provider are opposite facts and "
        "must not share an answer"
    )


def test_no_stored_key_is_reported_without_asking_anyone(client, verdict):
    """The fourth answer, and the outbound call that must not happen.

    The button is hidden when no key is stored, so this is the race: the key
    was removed in another window between the render and the click. Sending an
    empty string to the provider to be told it is invalid would be both a
    pointless request and a wrong answer.
    """
    assert client.delete("/api/v1/settings/api-key").status_code == 200
    verdict.calls.clear()

    resp = _check(client)
    assert resp.status_code == 200
    assert resp.json() == {"key_status": "not_set"}
    assert verdict.calls == [], "nothing to check must mean nothing was sent"


# ── the two invariants that outrank the feature ──────────────────────────────

def test_the_armed_kill_switch_refuses_before_the_key_is_read(
    client, verdict,
):
    """The same gate as every other outbound path, and it must refuse FIRST.

    proxy_required=1 with no proxy URL is "proxy_missing", the state every
    outbound path refuses. This route carries the stored key, so a gate it
    skipped would send that key and the user's real IP in the clear from the
    very screen that promises otherwise - which is the exact defect
    enforce_proxy_gate was written to close on save_api_key.
    """
    import database
    import proxy_health

    database.set_setting("proxy_required", "1")
    proxy_health.invalidate_health_cache()

    resp = _check(client)
    assert resp.status_code == 503
    assert resp.json()["detail"] == "proxy_missing"
    assert verdict.calls == [], "no outbound request may be made behind a gate"


def test_the_key_is_never_returned_or_logged(
    client, verdict, caplog: pytest.LogCaptureFixture,
):
    """The check reports a verdict about the key, never the key.

    Both exits are covered because they fail independently: a response that
    echoed the secret, and a log line written to help somebody debug this
    exact route.
    """
    verdict.box["answer"] = "valid"
    with caplog.at_level(logging.DEBUG):
        resp = _check(client)

    assert STORED_KEY not in json.dumps(resp.json())
    assert STORED_KEY not in resp.text
    assert STORED_KEY not in caplog.text

    # Positive control on the assertions above: the key really is the string
    # being guarded, and it really did reach the validator.
    assert verdict.calls == [STORED_KEY]
