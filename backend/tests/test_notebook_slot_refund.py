"""U-34(a) - the day's budget paid for calls that never left.

A slot is reserved BEFORE the request, and that is right: a counter
incremented on success cannot bound anything, because the calls that fail
are billed too and a failing model is the one a retry loop calls hardest.

It is wrong for the two paths that abandon the turn between the claim and
the socket - the running-row trace cannot be written, or the prompt fails to
build. Nothing is sent, nothing is billed, and the day is one call poorer
anyway. Sixty a day, so a chat that hits either path repeatedly can spend the
whole allowance on requests that never happened, and the panel reports a day
of spending with no calls to show for it.

THE THIRD WINDOW IS NOT COVERED HERE, and the test names say so.
`openrouter.complete` raises for a request that never reached the socket
(`api_key_not_set`, a proxy refusal) and for one that was sent and billed,
through the same `except`. Telling them apart needs a `sent_at` stamp the
table does not have. Refunding on a guess would give back calls that really
were paid for - the exact failure the reservation exists to prevent - so that
half waits for the migration window.
"""
from __future__ import annotations

import asyncio

import pytest

import config
import notebook_store as notebook
import notebook_worker
from database import get_db

from tests.conftest import make_character, make_chat


@pytest.fixture
def anyio_backend():
    return "asyncio"


def seed(client, count: int = 30) -> int:
    chat_id = make_chat(client, make_character(client))
    with get_db() as con:
        for i in range(count):
            con.execute(
                "INSERT INTO messages (chat_id, role, content, active) "
                "VALUES (?,?,?,1)",
                (chat_id, "user" if i % 2 == 0 else "assistant",
                 f"line {i}: her brother owns the mill"))
    return chat_id


def worker() -> notebook_worker.Worker:
    w = notebook_worker.Worker()
    w.queue = asyncio.Queue(maxsize=8)
    return w


def calls_today() -> int:
    with get_db() as con:
        return notebook.spend_today(con)["calls"]


def replying(monkeypatch, facts, *, usage=None):
    import openrouter
    import json

    async def _reply(*a, **kw):
        return {"id": "gen",
                "choices": [{"finish_reason": "stop",
                             "message": {"content": json.dumps(
                                 {"facts": facts})}}],
                "usage": usage or {"prompt_tokens": 10,
                                   "completion_tokens": 5, "cost": 0.0001}}

    monkeypatch.setattr(openrouter, "complete", _reply)


def fact():
    return {"text": "Her brother owns the mill.",
            "evidence": "her brother owns the mill",
            "kind": "fact", "durability": "permanent",
            "importance": 2, "supersedes": None}


@pytest.fixture
def armed(client, monkeypatch):
    """A chat the worker will actually try to extract from."""
    import database

    database.set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")
    return seed(client)


class TestACallThatNeverLeftDoesNotCostTheDay:
    @pytest.mark.anyio
    async def test_an_unwritable_trace_gives_the_slot_back(
            self, armed, monkeypatch) -> None:
        """Window 1. The running row is the evidence a paid call left; when
        it cannot be written the turn stops there, BEFORE the request."""
        async def refuse(*a, **kw):
            return False

        monkeypatch.setattr(notebook_worker, "_record_running", refuse)

        before = calls_today()
        w = worker()
        await w._handle(armed)

        assert w.last_error == "running_row_unwritable", (
            "ground: the run really took the window this test is about")
        assert calls_today() == before

    @pytest.mark.anyio
    async def test_a_prompt_that_cannot_be_built_gives_the_slot_back(
            self, armed, monkeypatch) -> None:
        """Window 2. The prompt is assembled after the claim and before the
        request, so a failure here means nothing was sent either."""
        def boom(*a, **kw):
            raise RuntimeError("prompt is unbuildable")

        monkeypatch.setattr(notebook_worker.notebook_extract,
                            "build_user_message", boom)

        before = calls_today()
        w = worker()
        await w._handle(armed)

        with get_db() as con:
            row = con.execute(
                "SELECT error_type FROM notebook_extractions "
                "WHERE chat_id = ? ORDER BY started_at DESC LIMIT 1",
                (armed,)).fetchone()
        assert row and row["error_type"].startswith("prompt_"), (
            "ground: the run really took the prompt-build window")
        assert calls_today() == before

    @pytest.mark.anyio
    async def test_a_call_that_succeeded_keeps_its_slot(
            self, armed, monkeypatch) -> None:
        """GROUND CONTROL. Without it every assertion above is satisfied by
        a release that fires on every turn, which would uncap the day."""
        replying(monkeypatch, [fact()])

        before = calls_today()
        w = worker()
        await w._handle(armed)

        assert calls_today() == before + 1

    @pytest.mark.anyio
    async def test_a_call_that_reached_the_provider_and_failed_keeps_its_slot(
            self, armed, monkeypatch) -> None:
        """POSITIVE CONTROL, and the boundary of what shipped.

        A provider failure is billed, so its slot must NOT come back. Until
        `sent_at` exists the code cannot tell this from a request that never
        reached the socket, and it keeps the slot for BOTH - the conservative
        half. This test pins that choice so a later change cannot start
        refunding paid calls without a test going red.
        """
        import openrouter

        async def fails(*a, **kw):
            raise openrouter.OpenRouterError("openrouter_timeout")

        monkeypatch.setattr(openrouter, "complete", fails)

        before = calls_today()
        w = worker()
        await w._handle(armed)

        assert calls_today() == before + 1


class TestTheCounterCannotGoNegative:
    def test_releasing_more_than_was_claimed_floors_at_zero(
            self, db) -> None:
        """`calls` counts calls made. A negative one is not a smaller
        number, it is a row that hands out free calls tomorrow."""
        with get_db() as con:
            notebook.claim_call(con, config.NOTEBOOK_DAILY_CALL_CAP)
            assert notebook.spend_today(con)["calls"] == 1

            # The STORED count, not only the return value. A `release_call`
            # that touched no row and returned 0 satisfied all three of
            # these - measured, it stayed green with the body replaced.
            assert notebook.release_call(con) == 0
            assert notebook.spend_today(con)["calls"] == 0, (
                "the row did not move; only the return value did")
            assert notebook.release_call(con) == 0
            assert notebook.release_call(con) == 0
            assert notebook.spend_today(con)["calls"] == 0

    def test_a_released_slot_is_claimable_again(self, db) -> None:
        """The point of the refund, stated as behaviour rather than as a
        number: the day really does get the call back."""
        with get_db() as con:
            for _ in range(config.NOTEBOOK_DAILY_CALL_CAP):
                notebook.claim_call(con, config.NOTEBOOK_DAILY_CALL_CAP)

            with pytest.raises(notebook.NotebookError):
                notebook.claim_call(con, config.NOTEBOOK_DAILY_CALL_CAP)

            notebook.release_call(con)
            # Does not raise.
            notebook.claim_call(con, config.NOTEBOOK_DAILY_CALL_CAP)
