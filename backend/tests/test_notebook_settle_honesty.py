"""U-27 - not raising is not succeeding.

`_settle` never looked at what `_write` returned. `breaker.succeeded()` and
`runs += 1` ran whenever the call simply failed to throw - and
`commit_extraction` reports three of its failures by RETURNING them:

  * `duplicate`     - this work key was already answered;
  * `stale_attempt` - the row has since been reclaimed by a retry;
  * `written == 0` with a skip reason - the range was cleared or rewritten
    while the reply was out.

Each was counted as a success. Worse: `succeeded()` clears the breaker's
`since_reset`, so every one of them ERASED the real failures before it. A
worker failing on every turn could show a healthy breaker and a rising run
count indefinitely.

The third discarded value is `parse_reply`'s dropped-suggestion counts, which
came back from every call and were assigned to a name nobody read. Without
them "0 notes this week" and "every suggestion failed the grounding check"
are the same screen.
"""
from __future__ import annotations

import asyncio
import json

import pytest

import config
import notebook_extract
import notebook_worker
import openrouter
from database import set_setting

from tests.test_notebook_worker import fact, seed


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def ready(client, monkeypatch):
    """A chat with a backlog, a healthy provider, and clean counters."""
    chat_id = seed(client, count=40)
    set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")

    async def good(*a, **kw):
        return {"id": "gen-1", "choices": [{
            "finish_reason": "stop",
            "message": {"content": json.dumps({"facts": [fact()]})}}],
            "usage": {"tokens_in": 10, "tokens_out": 5, "cost": 0.0001}}

    monkeypatch.setattr(openrouter, "complete", good)
    w = notebook_worker.worker
    w.breaker.reset()
    w.runs = 0
    w.settled_empty = 0
    w.dropped = {}
    return chat_id, w


def load_three_real_failures(w) -> None:
    """Three genuine provider failures on the breaker, to be erased or not."""
    for _ in range(3):
        w.breaker.failed(asyncio.get_event_loop().time())
    assert w.breaker.failures == 3, "ground: the failures really are there"


class TestASettleThatLandedNothing:
    @pytest.mark.anyio
    async def test_a_duplicate_does_not_erase_real_failures(
            self, ready, monkeypatch) -> None:
        chat_id, w = ready
        load_three_real_failures(w)
        monkeypatch.setattr(
            notebook_worker, "_write",
            lambda *a, **kw: {"duplicate": True, "written": 0, "retired": 0})

        await w._handle(chat_id)

        assert w.breaker.failures == 3, "a duplicate is not a success"
        assert w.runs == 0
        assert w.settled_empty == 1
        assert w.last_error == "settled_duplicate"

    @pytest.mark.anyio
    async def test_a_stale_attempt_is_not_a_run(self, ready, monkeypatch):
        chat_id, w = ready
        monkeypatch.setattr(
            notebook_worker, "_write",
            lambda *a, **kw: {"duplicate": True, "written": 0, "retired": 0,
                              "stale_attempt": True})

        await w._handle(chat_id)

        assert w.runs == 0
        assert w.last_error == "settled_stale_attempt"

    @pytest.mark.anyio
    async def test_a_reply_whose_notes_all_dropped_is_not_a_run(
            self, ready, monkeypatch) -> None:
        """`written == 0` while the reply DID carry proposals: the range was
        cleared or rewritten under it, so nothing landed."""
        chat_id, w = ready
        monkeypatch.setattr(
            notebook_worker, "_write",
            lambda *a, **kw: {"duplicate": False, "written": 0, "retired": 0})

        await w._handle(chat_id)

        assert w.runs == 0
        assert w.settled_empty == 1

    @pytest.mark.anyio
    async def test_a_healthy_write_still_counts_and_forgives(
            self, ready) -> None:
        """GROUND CONTROL.

        Without it every assertion above is satisfied by an application that
        never calls `succeeded()` at all - which would quietly turn the
        breaker into a one-way ratchet.
        """
        chat_id, w = ready
        load_three_real_failures(w)

        await w._handle(chat_id)

        assert w.runs == 1
        assert w.settled_empty == 0
        assert w.breaker.failures == 0, "a real write forgives the run"

    @pytest.mark.anyio
    async def test_a_reply_that_honestly_found_nothing_is_a_run(
            self, ready, monkeypatch) -> None:
        """The case that must NOT be counted as empty.

        A quiet stretch of conversation with nothing worth noting is a
        completed run, not a failure to land. Counting it as empty would put
        the panel back to crying wolf on the most ordinary outcome there is.
        """
        chat_id, w = ready

        async def nothing_worth_noting(*a, **kw):
            return {"id": "gen-2", "choices": [{
                "finish_reason": "stop",
                "message": {"content": json.dumps({"facts": []})}}],
                "usage": {"tokens_in": 10, "tokens_out": 5, "cost": 0.0001}}

        monkeypatch.setattr(openrouter, "complete", nothing_worth_noting)

        await w._handle(chat_id)

        assert w.runs == 1
        assert w.settled_empty == 0


class TestWhatTheParserRefused:
    @pytest.mark.anyio
    async def test_a_dropped_suggestion_reaches_the_status_screen(
            self, ready, monkeypatch) -> None:
        chat_id, w = ready
        real = notebook_extract.parse_reply
        monkeypatch.setattr(
            notebook_extract, "parse_reply",
            lambda *a, **kw: ([], {"ungrounded": 2}))

        await w._handle(chat_id)

        assert w.status()["dropped"].get("ungrounded") == 2
        assert real is not None      # the real one is restored by monkeypatch

    @pytest.mark.anyio
    async def test_a_clean_round_reports_nothing_dropped(self, ready) -> None:
        """POSITIVE CONTROL in the other direction: the counter must stay
        empty when nothing was refused, or it says "something is wrong" on
        every healthy install."""
        chat_id, w = ready

        await w._handle(chat_id)

        assert w.status()["dropped"] == {}
