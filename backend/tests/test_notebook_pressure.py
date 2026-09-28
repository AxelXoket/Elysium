"""Decision 28 a: under failures the batch shrinks and the threshold does not.

H-16 put the trigger threshold on the same halving rule as the batch, so that
the two numbers could not drift apart. Decision 28 a took the threshold back
off it. The halving read the GLOBAL counter, so one chat's provider errors
dropped every chat's threshold to 10, 5 and 2 messages: the other
conversations were read more often, on smaller batches, exactly while the
provider was failing. The rule is twenty messages, and it holds in a
failing period too. Only what one call reads shrinks.

These tests were written for H-16 and asserted the opposite. They are updated
here, and every changed assertion names 28 a, because the behaviour changed by
decision and not because a defect was patched over.

The first-read edge is still taken off the UNPRESSURED number (H-14): the
batch still halves, and an edge computed from it would send a short chat down
the branch built for a long history. That half is unchanged.

Behavioural throughout. Nothing here reads source text, and every constant is
imported from the module that owns it.
"""
from __future__ import annotations

import pytest

import config
import notebook_worker

from tests.test_notebook_planner_budget import MODEL, add_messages, message_ids
from tests.test_notebook_worker import seed


@pytest.fixture
def anyio_backend():
    return "asyncio"


class TestOnlyTheBatchMovesUnderPressure:
    def test_the_threshold_stays_twenty_while_the_batch_shrinks(self) -> None:
        """Rewritten for decision 28 a. From zero to five consecutive
        failures the threshold is the configured twenty every time, while
        the batch really does shrink to the floor - so the constancy is not
        a pair of numbers that never move."""
        w = notebook_worker.Worker()

        thresholds, batches = [], []
        for failures in range(0, 6):
            w.breaker.failures = failures
            thresholds.append(w.trigger_threshold)
            batches.append(w.batch_size)

        assert thresholds == [config.NOTEBOOK_EXTRACT_EVERY_TURNS] * 6, (
            f"the threshold moved under failures: {thresholds}")
        # GROUND: the pressure is real. The batch starts at the configured
        # number, strictly shrinks, and lands on the floor.
        assert batches[0] == config.NOTEBOOK_EXTRACT_EVERY_TURNS
        assert batches[0] > batches[1] > batches[2] > batches[3]
        assert batches[-1] == notebook_worker.PRESSURE_FLOOR

    def test_a_chats_own_failures_do_not_lower_its_threshold(self) -> None:
        """The per-chat accessor, which is the one `_prepare` asks. A chat
        whose own breaker holds three failures reads a smaller batch and
        keeps the twenty."""
        w = notebook_worker.Worker()
        w._fail_chat(7, 0.0)
        w._fail_chat(7, 0.0)
        w._fail_chat(7, 0.0)

        assert w.batch_size_for(7) < config.NOTEBOOK_EXTRACT_EVERY_TURNS, (
            "ground: the chat's own failures really do reach its batch")
        assert w.trigger_threshold_for(7) == config.NOTEBOOK_EXTRACT_EVERY_TURNS

    @pytest.mark.anyio
    async def test_the_planner_is_asked_with_twenty_and_the_halved_batch(
            self, client, monkeypatch) -> None:
        """Rewritten for decision 28 a. What the planner is ASKED is the
        contract: under three failures, the configured threshold beside the
        pressured batch, and the first-read edge off the unpressured number."""
        import database

        chat_id = seed(client, count=4)
        database.set_setting(config.SETTING_NOTEBOOK_MODEL, MODEL)

        seen: list[tuple] = []

        def spy(cid, model_id, language, threshold, limit, **kw):
            seen.append((threshold, limit, kw.get("first_read_edge")))
            return None

        monkeypatch.setattr(notebook_worker, "_plan_work", spy)

        w = notebook_worker.Worker()
        await w._prepare(chat_id)
        assert seen, "ground: the planner was reached at all"
        assert seen[-1][:2] == (config.NOTEBOOK_EXTRACT_EVERY_TURNS,
                                config.NOTEBOOK_EXTRACT_EVERY_TURNS), (
            "ground: an unpressured worker asks with the configured numbers")

        w.breaker.failures = 3
        await w._prepare(chat_id)
        threshold, limit, edge = seen[-1]

        assert limit == w.batch_size_for(chat_id) < threshold, (
            "ground: the pressured batch really did reach the planner")
        assert threshold == config.NOTEBOOK_EXTRACT_EVERY_TURNS, (
            f"the planner was asked with threshold={threshold} under "
            "pressure; decision 28 a keeps it at the configured number")
        assert edge == notebook_worker.from_start_edge(), (
            "the first-read edge was taken off the pressured number")

    @pytest.mark.anyio
    async def test_a_failing_chat_does_not_make_another_chat_read_sooner(
            self, client, monkeypatch) -> None:
        """The case decision 28 a is about. Chat A's provider errors move the
        GLOBAL counter to three. Chat B, which never failed, is not read on
        nineteen unread messages - which H-16 did, at a threshold of two -
        and is read on its twentieth: the positive control that the same
        worker, under the same pressure, does read B at twenty."""
        import database

        database.set_setting(config.SETTING_NOTEBOOK_MODEL, MODEL)
        w = notebook_worker.Worker()
        # Three provider faults, the kind `_fault_scope` counts globally,
        # all of them chat A's.
        for _ in range(3):
            w._note_failure(1, 0.0, "OpenRouterError:openrouter_timeout")
        assert w.breaker.failures == 3, "ground: the global counter moved"
        assert w.breaker.allows(0.0), (
            "ground: three is not an open breaker, so nothing else refuses")

        real = notebook_worker._plan_work
        plans: list = []

        def spy(*a, **kw):
            plan = real(*a, **kw)
            plans.append(plan)
            return plan

        monkeypatch.setattr(notebook_worker, "_plan_work", spy)

        # The unread count is read back rather than assumed from `count`.
        # No greeting: since decision 29 a a greeting chat waits for one
        # more, and this test is about the threshold, not the greeting.
        chat_b = seed(client, count=config.NOTEBOOK_EXTRACT_EVERY_TURNS - 1,
                      greeting=False)
        assert len(message_ids(chat_b)) == (
            config.NOTEBOOK_EXTRACT_EVERY_TURNS - 1), "ground: nineteen unread"
        await w._prepare(chat_b)
        assert plans and plans[-1] is None, (
            "chat B was planned on nineteen messages while chat A was failing")

        add_messages(chat_b, ["the twentieth"], role="assistant")
        assert len(message_ids(chat_b)) == config.NOTEBOOK_EXTRACT_EVERY_TURNS
        await w._prepare(chat_b)
        assert plans[-1] is not None, (
            "POSITIVE CONTROL: on its twentieth message chat B is planned")
        assert len(plans[-1]["new"]) == w.batch_size_for(chat_b), (
            "and it reads the pressured batch, which is the half that "
            "still shrinks")

    @pytest.mark.anyio
    async def test_a_pressured_trigger_does_not_reopen_the_first_read_branch(
            self, client, monkeypatch) -> None:
        """M3, and the reason `first_read_edge` exists at all."""
        import database

        pending = config.NOTEBOOK_EXTRACT_EVERY_TURNS
        # No greeting (decision 29 a): see the test above.
        chat_id = seed(client, count=pending, greeting=False)
        ids = message_ids(chat_id)
        assert len(ids) == pending, "ground: enough to trigger, not a backlog"
        database.set_setting(config.SETTING_NOTEBOOK_MODEL, MODEL)

        real = notebook_worker._plan_work
        seen: list = []

        def spy(*a, **kw):
            plan = real(*a, **kw)
            seen.append(plan)
            return plan

        monkeypatch.setattr(notebook_worker, "_plan_work", spy)

        w = notebook_worker.Worker()
        w.breaker.failures = 3
        assert w.breaker.allows(0.0) is True, (
            "ground: three failures is not an open breaker, so nothing else "
            "is refusing this work and any loss here is silent")
        await w._prepare(chat_id)

        assert seen and seen[-1] is not None, "ground: a plan was made"
        lost = ids.index(seen[-1]["from_id"])
        assert seen[-1]["from_id"] == ids[0], (
            f"a pressured trigger sent a {pending}-message chat down the "
            f"upgrading-user branch and abandoned {lost} messages")

    @pytest.mark.anyio
    async def test_the_first_read_branch_still_fires_for_a_real_backlog(
            self, client, monkeypatch) -> None:
        """POSITIVE CONTROL. The branch is right and is not removed: a chat
        with a genuine backlog still starts at the PRESENT even while the
        worker is pressured. Without this the test above passes by deleting
        the branch."""
        import database

        chat_id = seed(client, count=config.NOTEBOOK_EXTRACT_EVERY_TURNS * 3)
        ids = message_ids(chat_id)
        database.set_setting(config.SETTING_NOTEBOOK_MODEL, MODEL)

        real = notebook_worker._plan_work
        seen: list = []

        def spy(*a, **kw):
            plan = real(*a, **kw)
            seen.append(plan)
            return plan

        monkeypatch.setattr(notebook_worker, "_plan_work", spy)

        w = notebook_worker.Worker()
        w.breaker.failures = 3
        await w._prepare(chat_id)

        assert seen and seen[-1] is not None
        assert seen[-1]["to_id"] == ids[-1], "it did not jump to the present"
        assert seen[-1]["from_id"] == ids[-w.batch_size_for(chat_id)], (
            "and it read exactly the pressured batch off the newest end")

    def test_the_floor_never_lets_the_batch_rise(self, monkeypatch) -> None:
        """`max(2, size // 2)` on a configured value of 1 returns 2:
        halving moved the number UPWARD. `min(size, ...)` is what stops it.
        Since decision 28 a the threshold is not on this rule at all, so it
        is asserted as the configured value rather than as "not above"."""
        monkeypatch.setattr(config, "NOTEBOOK_EXTRACT_EVERY_TURNS", 1)
        w = notebook_worker.Worker()

        assert w.trigger_threshold == 1 and w.batch_size == 1, (
            "ground: an unpressured worker reads the configured 1")

        w.breaker.failures = 3
        assert w.batch_size <= 1, (
            f"a configured batch of 1 became {w.batch_size} under pressure")
        assert w.trigger_threshold == 1

    def test_the_floor_is_where_an_ordinary_value_lands(self) -> None:
        """GROUND for the test above: `min(size, ...)` must not stop an
        ordinary value from reaching the floor. Decision 28 a: only the
        batch goes there; the threshold stays at the configured number."""
        w = notebook_worker.Worker()
        w.breaker.failures = notebook_worker.PRESSURE_STEPS + 2
        assert w.batch_size == notebook_worker.PRESSURE_FLOOR
        assert w.trigger_threshold == config.NOTEBOOK_EXTRACT_EVERY_TURNS
