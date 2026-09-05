"""H-16: the trigger threshold and the batch move together, or not at all.

The batch halved under the breaker's consecutive failures and the trigger
threshold did not, so the same backlog was bought in more pieces and the fixed
part of every prompt - the card, the existing notes, the instructions - was
paid again in each of them. Closing that means ONE function answers both
questions, and one floor holds both.

It also means the first-read edge has to be taken off the UNPRESSURED number.
H-14 was the same shape with the batch: an edge computed from a number the
breaker had halved sent a short chat down the branch built for a
four-hundred-message backlog, and everything under the newest few went
permanently unread with the breaker still CLOSED. Putting the threshold on the
same rule reopens that door unless the edge is passed in explicitly.

Behavioural throughout. Nothing here reads source text, and every constant is
imported from the module that owns it.
"""
from __future__ import annotations

import pytest

import config
import notebook_worker

from tests.test_notebook_planner_budget import MODEL, message_ids
from tests.test_notebook_worker import seed


@pytest.fixture
def anyio_backend():
    return "asyncio"


class TestPressureMovesBothNumbersTogether:
    def test_the_trigger_threshold_and_the_batch_halve_together(self) -> None:
        """M1. One rule, two properties. The defect this closes is that they
        were two rules and drifted apart from ONE consecutive failure."""
        w = notebook_worker.Worker()

        assert w.trigger_threshold == config.NOTEBOOK_EXTRACT_EVERY_TURNS, (
            "ground: with nothing failing the threshold is the configured one")
        assert w.batch_size == config.NOTEBOOK_EXTRACT_EVERY_TURNS, (
            "ground: and so is the batch")

        seen = []
        for failures in range(0, 5):
            w.breaker.failures = failures
            assert w.trigger_threshold == w.batch_size, (
                f"{w.trigger_threshold} != {w.batch_size} at "
                f"{failures} failures")
            seen.append(w.batch_size)

        assert seen[0] > seen[1] > seen[-1], (
            "ground: pressure really does move the number, so the equality "
            "above is not satisfied by a pair that never changes")
        assert seen[-1] == notebook_worker.PRESSURE_FLOOR, (
            "ground: and it lands on the shared floor")

    @pytest.mark.anyio
    async def test_the_planner_is_asked_with_the_halved_threshold(
            self, client, monkeypatch) -> None:
        """M2. The property is not the contract; what the planner is ASKED
        is. The worker used to hand it `NOTEBOOK_EXTRACT_EVERY_TURNS` as the
        threshold and a halved number as the limit, in the same call."""
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
        assert seen[-1][0] == config.NOTEBOOK_EXTRACT_EVERY_TURNS, (
            "ground: an unpressured worker asks with the configured number")
        assert seen[-1][1] == seen[-1][0], "ground: and the batch matches it"

        w.breaker.failures = 3
        await w._prepare(chat_id)
        threshold, limit, edge = seen[-1]

        assert limit == w.batch_size_for(chat_id), (
            "ground: the pressured batch really did reach the planner")
        assert threshold == limit, (
            f"the planner was asked with threshold={threshold} while the "
            f"batch was {limit}")
        assert edge == config.NOTEBOOK_EXTRACT_EVERY_TURNS * 2, (
            "the first-read edge was taken off the pressured number")

    @pytest.mark.anyio
    async def test_a_pressured_trigger_does_not_reopen_the_first_read_branch(
            self, client, monkeypatch) -> None:
        """M3, and the reason `first_read_edge` exists at all."""
        import database

        pending = config.NOTEBOOK_EXTRACT_EVERY_TURNS
        chat_id = seed(client, count=pending - 1)
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

    def test_the_floor_never_lets_the_trigger_rise(self, monkeypatch) -> None:
        """M4, a defect in the shipped line rather than a new risk.

        `max(2, size // 2)` on a configured value of 1 returns 2: halving
        moved the number UPWARD. Harmless while only the batch read it; with
        the threshold on the same rule it would fire HALF as often under
        pressure as when healthy.
        """
        monkeypatch.setattr(config, "NOTEBOOK_EXTRACT_EVERY_TURNS", 1)
        w = notebook_worker.Worker()

        assert w.trigger_threshold == 1 and w.batch_size == 1, (
            "ground: an unpressured worker reads the configured 1")

        w.breaker.failures = 3
        assert w.trigger_threshold <= 1, (
            f"a configured threshold of 1 became {w.trigger_threshold} under "
            "pressure: halving moved it upward")
        assert w.batch_size <= 1, (
            f"a configured batch of 1 became {w.batch_size} under pressure")

    def test_the_floor_is_where_an_ordinary_value_lands(self) -> None:
        """GROUND for the test above: `min(size, ...)` must not stop an
        ordinary value from reaching the floor."""
        w = notebook_worker.Worker()
        w.breaker.failures = notebook_worker.PRESSURE_STEPS + 2
        assert w.batch_size == notebook_worker.PRESSURE_FLOOR
        assert w.trigger_threshold == notebook_worker.PRESSURE_FLOOR
