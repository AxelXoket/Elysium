"""U-42 - the money path says what actually happened.

Three places, one class of mistake: information thrown away on the way to the
screen.

  * every provider failure was recorded as `OpenRouterError`, because the
    class name was stored and the separated reason - `openrouter_timeout`,
    `proxy_auth_failed`, `api_key_not_set` - lives in the message;
  * the breaker had three behaviours and two names, so "Paused, it will try
    again by itself" was printed over a trial call that was already going
    out, and that call is billed;
  * "Nothing was lost" was drawn from `failed - abandoned`, and a write that
    fails AFTER the reply has been sent, generated and billed is not
    abandoned - it is the other kind of lost.
"""
from __future__ import annotations

import asyncio
import json

import pytest

import config
import notebook_store as notebook
import notebook_worker
import openrouter
from database import get_db, set_setting

from tests.test_notebook_worker import fact, seed


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def ready(client, monkeypatch):
    chat_id = seed(client, count=40)
    set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")
    notebook_worker.worker.breaker.reset()
    return chat_id


def last_failure(chat_id: int) -> str | None:
    with get_db() as con:
        row = con.execute(
            "SELECT error_type FROM notebook_extractions "
            "WHERE chat_id = ? AND status = 'failed' "
            "ORDER BY created_at DESC LIMIT 1", (chat_id,)).fetchone()
    return row[0] if row else None


class TestTheReasonTheProviderGave:
    @pytest.mark.anyio
    async def test_a_timeout_is_recorded_as_a_timeout(self, ready, monkeypatch):
        async def times_out(*a, **kw):
            raise openrouter.OpenRouterError("openrouter_timeout")

        monkeypatch.setattr(openrouter, "complete", times_out)

        await notebook_worker.worker._handle(ready)

        recorded = last_failure(ready)
        assert recorded is not None
        assert "openrouter_timeout" in recorded

    @pytest.mark.anyio
    async def test_a_refused_proxy_is_told_apart_from_a_timeout(
            self, ready, monkeypatch) -> None:
        """POSITIVE CONTROL. One class for everything is exactly what this
        fixes, so two different causes must produce two different rows."""
        async def proxy(*a, **kw):
            raise openrouter.OpenRouterError("proxy_auth_failed")

        monkeypatch.setattr(openrouter, "complete", proxy)

        await notebook_worker.worker._handle(ready)

        assert "proxy_auth_failed" in (last_failure(ready) or "")

    def test_a_message_that_is_not_one_of_our_codes_is_refused(self) -> None:
        """The guard, and it is the reason this is not just `str(exc)`.

        An arbitrary exception message can carry a path, a URL, or something
        a person typed. Only a bare identifier - which is what every code in
        our own vocabulary is - gets through.
        """
        loud = RuntimeError("could not open the file at that path")
        assert notebook_worker._failure_type(loud) == "RuntimeError"

    def test_the_write_prefix_is_untouched(self) -> None:
        """GROUND CONTROL. `write_` and `_unrecorded` are pinned by two other
        tests as literal text, and the cursor query matches
        `abandoned_in_flight` by equality - none of that may move."""
        assert notebook.ABANDONED_IN_FLIGHT == "abandoned_in_flight"


class TestTheBreakerHasThreeStates:
    def test_it_says_half_open_once_the_cooldown_has_elapsed(self) -> None:
        b = notebook_worker.Breaker()
        for i in range(notebook_worker.TRIP_AFTER):
            b.failed(float(i))
        assert b.state == "open", "ground: it really did open"

        # The moment the worker next asks, past the cooldown.
        assert b.allows(1e6) is True
        assert b.state == "half_open"

    def test_it_still_says_open_while_it_really_is(self) -> None:
        """GROUND CONTROL: a state that reported half_open too early would
        tell the reader a call is going out when none is."""
        b = notebook_worker.Breaker()
        for i in range(notebook_worker.TRIP_AFTER):
            b.failed(float(i))
        assert b.allows(1.0) is False
        assert b.state == "open"

    def test_a_success_closes_it_again(self) -> None:
        b = notebook_worker.Breaker()
        for i in range(notebook_worker.TRIP_AFTER):
            b.failed(float(i))
        b.allows(1e6)
        b.succeeded()
        assert b.state == "closed"


class TestWhatCostMoney:
    def test_a_failed_write_is_counted_as_paid_for(self, client) -> None:
        chat_id = seed(client, count=6)
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.commit_extraction(
                con, work_key="w1", chat_id=chat_id, from_id=1, to_id=4,
                proposals=[], status="failed",
                error_type="write_RuntimeError")

        with get_db() as con:
            stats = notebook.extraction_stats(con, chat_id)

        assert stats["failed"] == 1
        assert stats["abandoned"] == 0, (
            "ground: it was not abandoned - the app was never killed")
        assert stats["paid_and_lost"] == 1

    def test_an_ordinary_failure_is_not(self, client) -> None:
        """GROUND CONTROL. A call that never reached the provider costs
        nothing, and the reassuring sentence is true of it."""
        chat_id = seed(client, count=6)
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.commit_extraction(
                con, work_key="f1", chat_id=chat_id, from_id=1, to_id=4,
                proposals=[], status="failed",
                error_type="OpenRouterError:openrouter_timeout")

        with get_db() as con:
            stats = notebook.extraction_stats(con, chat_id)

        assert stats["failed"] == 1
        assert stats["paid_and_lost"] == 0

    def test_an_abandoned_call_is_still_counted(self, client) -> None:
        chat_id = seed(client, count=6)
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.commit_extraction(
                con, work_key="a1", chat_id=chat_id, from_id=1, to_id=4,
                proposals=[], status="failed",
                error_type=notebook.ABANDONED_IN_FLIGHT)

        with get_db() as con:
            stats = notebook.extraction_stats(con, chat_id)

        assert stats["abandoned"] == 1
        assert stats["paid_and_lost"] == 1, "the wider count includes it"

    def test_the_two_counts_can_differ(self, client) -> None:
        """The whole reason for a second field: one is a subset of the other,
        and a fixture where they are equal cannot tell them apart."""
        chat_id = seed(client, count=6)
        for key, kind in (("x1", notebook.ABANDONED_IN_FLIGHT),
                          ("x2", "write_RuntimeError"),
                          ("x3", "OpenRouterError:openrouter_timeout")):
            with get_db() as con:
                con.execute("BEGIN IMMEDIATE")
                notebook.commit_extraction(
                    con, work_key=key, chat_id=chat_id, from_id=1, to_id=4,
                    proposals=[], status="failed", error_type=kind)

        with get_db() as con:
            stats = notebook.extraction_stats(con, chat_id)

        assert stats["failed"] == 3
        assert stats["abandoned"] == 1
        assert stats["paid_and_lost"] == 2

    def test_the_status_route_carries_the_new_count(self, client) -> None:
        """C-5: the count is useless if it does not reach the panel."""
        seed(client, count=6)
        body = client.get("/api/v1/notebook/worker").json()
        assert "paid_and_lost" in body["stats"]
        assert isinstance(body["stats"]["paid_and_lost"], int)
        assert json.dumps(body["stats"])      # serialises
        assert asyncio  # imported for the anyio backend fixture above


class TestTheBreakerIsAskedAboutNow:
    """The panel read a clock nobody had wound.

    `state` answered from `self._now`, written only as a side effect of
    `allows()` - and `allows()` runs only when a queue item arrives, which on
    an idle machine never happens: the vault locks and `offer()` returns
    early. So the cooldown expired and the panel went on saying "Paused after
    repeated failures. It will try again by itself" for as long as the reader
    stayed away, which is the ordinary case rather than an edge one.

    WITHOUT CALLING `allows()` FIRST. Calling it is what hid the defect; a
    test that calls it is measuring the implementation.
    """

    def test_a_cooled_breaker_says_half_open_without_being_asked(self) -> None:
        b = notebook_worker.Breaker()
        for i in range(notebook_worker.TRIP_AFTER):
            b.failed(100.0 + i)
        assert b.state_at(100.0) == "open", "ground: it really did trip"

        # From the moment the breaker actually opened, not from the first
        # failure:  is stamped with the LAST clock it was given.
        later = b.opened_at + b.cooldown + 1
        assert b.state_at(later) == "half_open"

    def test_the_status_route_uses_the_present(self, client) -> None:
        """End to end: the same question through the wire, with no `allows()`
        anywhere in between."""
        w = notebook_worker.worker
        w.breaker.reset()
        for i in range(notebook_worker.TRIP_AFTER):
            w.breaker.failed(1.0 + i)
        # Stamp the trip far enough in the past that any live clock is past
        # the cooldown - `loop.time()` starts near the process's own zero.
        w.breaker.opened_at = -10_000.0

        body = client.get("/api/v1/notebook/worker").json()["worker"]

        assert body["state"] == "half_open"
        w.breaker.reset()

    def test_a_breaker_still_inside_its_cooldown_says_open(self) -> None:
        """GROUND CONTROL. A fix that simply stopped reporting `open` would
        pass the two above and tell the reader a paused notebook is running."""
        b = notebook_worker.Breaker()
        for i in range(notebook_worker.TRIP_AFTER):
            b.failed(100.0 + i)

        assert b.state_at(b.opened_at + b.cooldown - 1) == "open"

    def test_with_no_clock_at_all_it_stays_conservative(self) -> None:
        """POSITIVE CONTROL for the None branch: no clock must not read as
        `closed`, which would advertise a call the breaker would refuse."""
        b = notebook_worker.Breaker()
        for i in range(notebook_worker.TRIP_AFTER):
            b.failed(100.0 + i)

        assert b.state_at(None) == "open"

    def test_stopped_outranks_the_clock(self) -> None:
        b = notebook_worker.Breaker()
        for i in range(notebook_worker.STOP_AFTER):
            b.failed(100.0 + i)

        assert b.state_at(1e9) == "stopped"

