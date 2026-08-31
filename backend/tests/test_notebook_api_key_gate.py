"""U-26 - a call that cannot leave the machine is not a call.

The daily quota was claimed before anyone asked whether a request was
possible at all, and `openrouter.complete`'s very first statement reads the
API key and raises. So on a vault with no key - never set, or deleted - every
turn burned a slot of a sixty-call budget without a single byte going out.
Five of those tripped the breaker, twenty stopped the worker, and the panel
said "stopped" with nothing attached to it.

The number on the screen said sixty. The effective ceiling was twenty.

KARAR 23 decides the direction: egress is a call that LEAVES. A call that
cannot leave is neither egress nor spend, so this is a named skip rather than
a claim followed by a failure.
"""
from __future__ import annotations

import asyncio
import json

import pytest

import config
import notebook_store as notebook
import notebook_worker
import openrouter
import secrets_service
from database import get_db, set_setting

from tests.test_notebook_worker import fact, seed


@pytest.fixture
def anyio_backend():
    return "asyncio"


def forget_the_key() -> None:
    """A vault with no API key in it - the state this whole unit is about."""
    secrets_service.set_secret(config.SECRET_API_KEY, "")


async def turns(count: int, chat_id: int) -> None:
    """Drive `_handle` `count` times, the way the offer loop would."""
    for _ in range(count):
        await notebook_worker.worker._handle(chat_id)


@pytest.fixture
def ready(client, monkeypatch):
    """A chat with enough backlog to plan work, and a healthy provider."""
    chat_id = seed(client, count=40)
    set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")

    sent: list[int] = []

    async def good(*a, **kw):
        sent.append(1)
        return {"id": f"gen-{len(sent)}", "choices": [{
            "finish_reason": "stop",
            "message": {"content": json.dumps({"facts": [fact()]})}}],
            "usage": {"tokens_in": 10, "tokens_out": 5, "cost": 0.0001}}

    monkeypatch.setattr(openrouter, "complete", good)
    notebook_worker.worker.breaker.reset()
    notebook_worker.worker.extraction_skips = {}
    return chat_id, sent


class TestACallThatCannotLeave:
    @pytest.mark.anyio
    async def test_it_does_not_spend_the_days_quota(self, ready) -> None:
        chat_id, sent = ready
        forget_the_key()

        await turns(25, chat_id)

        with get_db() as con:
            assert notebook.spend_today(con)["calls"] == 0
        assert sent == [], "nothing may reach the provider without a key"

    @pytest.mark.anyio
    async def test_it_does_not_stop_the_worker(self, ready) -> None:
        """Twenty of these used to walk the breaker to its stop, so a key
        that was simply never set read as a broken provider."""
        chat_id, _ = ready
        forget_the_key()

        await turns(25, chat_id)

        loop = asyncio.get_running_loop()
        assert notebook_worker.worker.breaker.allows(loop.time())

    @pytest.mark.anyio
    async def test_it_leaves_a_trace_with_a_readable_reason(self, ready):
        """POSITIVE CONTROL. "Counted nothing" and "silently ignored" are
        different outcomes and the panel has to be able to tell them apart."""
        chat_id, _ = ready
        forget_the_key()

        await turns(3, chat_id)

        with get_db() as con:
            stats = notebook.extraction_stats(con, chat_id)
        assert stats["skipped"] >= 1
        assert stats["skip_reasons"].get("api_key_not_set")
        assert "api_key_not_set" in notebook.SKIP_REASONS

    @pytest.mark.anyio
    async def test_with_a_key_the_same_loop_spends_and_stays_healthy(
            self, ready) -> None:
        """GROUND CONTROL.

        Without this the three tests above are satisfied by an application
        that counts nothing, sends nothing and refuses everything.
        """
        chat_id, sent = ready

        await turns(3, chat_id)

        with get_db() as con:
            assert notebook.spend_today(con)["calls"] >= 1
        assert sent, "the provider must be reached when a key IS set"
        loop = asyncio.get_running_loop()
        assert notebook_worker.worker.breaker.allows(loop.time())


class TestTheReasonTheProviderGave:
    @pytest.mark.anyio
    async def test_the_row_carries_the_reason_not_only_the_class(
            self, ready, monkeypatch):
        """`OpenRouterError` alone threw away the only part a reader could
        act on. `reason` is this app's own short code - never provider prose
        and never anybody's words - so it is safe to store."""
        chat_id, _ = ready

        async def refuses(*a, **kw):
            raise openrouter.OpenRouterError("provider_unreachable")

        monkeypatch.setattr(openrouter, "complete", refuses)
        await turns(1, chat_id)

        with get_db() as con:
            row = con.execute(
                "SELECT error_type FROM notebook_extractions "
                "WHERE chat_id = ? AND status = 'failed' "
                "ORDER BY created_at DESC LIMIT 1",
                (chat_id,)).fetchone()
        assert row is not None
        assert "provider_unreachable" in row[0]

    def test_a_bare_exception_still_records_its_class(self) -> None:
        """GROUND CONTROL: the class name is always there.

        The rule widened afterwards - a message that is a bare identifier is
        now carried too, because `openrouter` puts its separated reason in
        the message rather than in an attribute. So the assertion is that the
        CLASS survives, which is what this control was always about; the
        message half has its own tests next door.
        """
        recorded = notebook_worker._failure_type(RuntimeError("x"))
        assert recorded.startswith("RuntimeError")

    def test_a_reason_that_is_not_a_plain_code_is_refused(self) -> None:
        """The guard that keeps this from becoming a leak. `reason` is only
        used when it looks like one of our own identifiers; anything with a
        space in it could be prose, and prose can carry a person's words."""
        class Loud(Exception):
            reason = "the user said: meet me at the harbour"

        assert notebook_worker._failure_type(Loud()) == "Loud"
