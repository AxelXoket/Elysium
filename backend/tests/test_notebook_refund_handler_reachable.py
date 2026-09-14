"""The abandon paths promise never to raise, and one missing import broke it.

`notebook_worker` has two blocks that hand things back after a turn was
given up on: `_release_one`, which returns the claimed daily call slot, and
the `_undo` closure inside `_undo_abandoned_preamble`, which deletes the
`running` row a turn left behind. Both are written as

    try:
        ... touch the database ...
    except vault_state.VaultLockedError:
        ... expected, log at INFO ...
    except Exception:
        ... anything else, log at WARNING ...

and both are on paths whose whole contract is that they absorb failure:
`_release_one`'s docstring says "Never raises into the caller", and `_undo`
runs inside a task nobody retrieves the exception of.

An `except` clause is an EXPRESSION, evaluated only when something is caught.
So a name that is not bound at module level costs nothing until the moment
the block has to do its job, and then it costs everything: the lookup raises
`NameError` while an exception is already in flight, the `except Exception`
underneath is never consulted, and the failure the block exists to swallow
leaves anyway wearing a different name.

These tests are about the SECOND except clause being reachable at all, so
each site gets both halves:

  * an ordinary failure inside the block (nothing to do with the vault) must
    be absorbed and logged at WARNING;
  * a locked vault must be absorbed and logged at INFO, which is what tells
    the two branches apart. A block that had collapsed into one bare
    `except Exception` would pass the first half and fail this one.

Ground control in both classes: with a working database the same call really
does reach the code under test and really does move the day's counter, so a
green "it did not raise" is never satisfied by a function that did nothing.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

import config
import database
import notebook_store as notebook
import notebook_worker
import vault_state
from database import get_db

from tests.conftest import make_character, make_chat


@pytest.fixture
def anyio_backend():
    return "asyncio"


WORKER_LOGGER = notebook_worker.logger.name


class Ordinary(RuntimeError):
    """A failure with no relation to the vault. Stands in for a corrupt
    page, a disk error, a bug in the statement - anything the second
    `except` is supposed to be there for."""


def raising_db(exc: BaseException):
    """Replace `get_db` with something that fails the way the real one does.

    Both blocks under test resolve `get_db` from `database` at call time
    (`from database import get_db` sits inside the function body), so
    patching the module reaches them without touching the worker.
    """
    def _boom(*args, **kwargs):
        raise exc

    return _boom


def calls_today() -> int:
    with get_db() as con:
        return notebook.spend_today(con)["calls"]


class TestTheSlotRefundAbsorbsWhatItPromisesTo:
    """`_release_one`, the site both `_handle` and the undo callback lean on."""

    def test_an_ordinary_failure_does_not_escape_the_refund(
            self, db, monkeypatch, caplog) -> None:
        """The claim under test, stated as behaviour: a database error that
        is NOT a locked vault stays inside `_release_one`.

        Both callers are already unwinding an abandoned turn. One of them is
        a done-callback, where an escaping exception is asyncio's
        "exception was never retrieved"; the other is a `finally` that would
        replace the real error with this one.
        """
        monkeypatch.setattr(database, "get_db", raising_db(Ordinary("disk")))

        with caplog.at_level(logging.WARNING, logger=WORKER_LOGGER):
            notebook_worker._release_one("2026-01-01")

        assert any(r.levelno == logging.WARNING for r in caplog.records), (
            "the ordinary branch has to be the one that ran, and it is the "
            "one that warns; silence here would mean the call was absorbed "
            "somewhere else entirely")

    def test_a_locked_vault_is_recognised_rather_than_merely_survived(
            self, db, monkeypatch, caplog) -> None:
        """The expected failure on this path, and it must not be reported
        as a fault.

        A lock is the usual REASON a refund is needed at all, so it is
        logged at INFO and the generic branch warns. Asserting the level is
        what makes this a test of the vault clause specifically: a block
        that caught everything with one handler would warn here and go red.
        """
        monkeypatch.setattr(
            database, "get_db", raising_db(vault_state.VaultLockedError()))

        with caplog.at_level(logging.INFO, logger=WORKER_LOGGER):
            notebook_worker._release_one("2026-01-01")

        levels = {r.levelno for r in caplog.records
                  if r.name == WORKER_LOGGER}
        assert logging.INFO in levels, (
            "a locked vault produced no line at all; the refund went "
            "missing with nothing to find it by")
        assert logging.WARNING not in levels, (
            "a locked vault was reported as a fault, which means the "
            "generic handler caught it and the vault clause never ran")

    def test_a_working_refund_really_gives_the_day_its_call_back(
            self, db, caplog) -> None:
        """GROUND CONTROL for both tests above.

        Without it, `_release_one` could be a no-op and every "it did not
        raise" assertion would still be green.
        """
        with get_db() as con:
            notebook.claim_call(con, config.NOTEBOOK_DAILY_CALL_CAP)
            day = notebook.spend_day(con)

        assert calls_today() == 1, "ground: the claim did not land"

        with caplog.at_level(logging.INFO, logger=WORKER_LOGGER):
            notebook_worker._release_one(day)

        assert calls_today() == 0, (
            "the refund did not move the stored count, so the two tests "
            "above are measuring a function that does nothing")
        assert not [r for r in caplog.records if r.name == WORKER_LOGGER], (
            "a refund that worked still complained; the handlers are firing "
            "on the happy path")


class TestTheAbandonedPreambleUndoAbsorbsItsOwnFailure:
    """The second site: the `running` row deletion inside `_undo`.

    Reached as a done-callback on `_prepare` when the turn that would have
    used its plan was cancelled. It is scheduled onto a thread and tracked,
    so the way to observe it is to await the tracked task and see whether
    anything came out of it.
    """

    def _plan(self, chat_id: int) -> dict:
        return {"work_key": f"wk-{chat_id}",
                "chat_id": chat_id,
                "claim_day": "2026-01-01"}

    async def _run_undo(self, worker: notebook_worker.Worker,
                        chat_id: int) -> None:
        """Drive `_undo_abandoned_preamble` and wait for what it schedules.

        The callback wants a finished task whose result is what `_prepare`
        returns, so it gets one - a real task, resolved, not a stub object,
        because the callback interrogates `cancelled()` and `exception()`
        before it ever looks at the result.
        """
        async def prepared():
            return (self._plan(chat_id), "vendor/cheap", "en")

        task = asyncio.create_task(prepared())
        await task

        worker._undo_abandoned_preamble(task)

        assert worker.abandoned_preambles == 1, (
            "ground: the callback did not take the abandon path, so "
            "nothing below is measuring the undo")
        pending = list(worker._inflight)
        assert pending, "ground: the undo was never scheduled"
        await asyncio.gather(*pending)

    @pytest.mark.anyio
    async def test_an_ordinary_failure_does_not_escape_the_undo(
            self, db, monkeypatch, caplog) -> None:
        """The row deletion fails for a reason that is not a lock. The undo
        must record it and end quietly; it runs where nobody is waiting to
        catch anything.

        The slot refund is stubbed out here on purpose - it is the previous
        class's subject, it runs BEFORE this try block, and leaving it live
        would mean this test went red for the other site's reason.
        """
        monkeypatch.setattr(notebook_worker, "_release_one", lambda *a: None)
        monkeypatch.setattr(database, "get_db", raising_db(Ordinary("disk")))

        w = notebook_worker.Worker()
        with caplog.at_level(logging.WARNING, logger=WORKER_LOGGER):
            await self._run_undo(w, 11)

        assert any(r.levelno == logging.WARNING for r in caplog.records), (
            "the undo swallowed the failure without leaving a line, or "
            "never reached its handlers at all")

    @pytest.mark.anyio
    async def test_a_locked_vault_in_the_undo_is_recognised(
            self, db, monkeypatch, caplog) -> None:
        """The expected failure again, on the second site.

        By the time this callback runs the key may already be gone - the
        lock is why the turn was abandoned. That is an INFO, and the next
        cycle closes the row out.
        """
        monkeypatch.setattr(notebook_worker, "_release_one", lambda *a: None)
        monkeypatch.setattr(
            database, "get_db", raising_db(vault_state.VaultLockedError()))

        w = notebook_worker.Worker()
        with caplog.at_level(logging.INFO, logger=WORKER_LOGGER):
            await self._run_undo(w, 12)

        levels = {r.levelno for r in caplog.records
                  if r.name == WORKER_LOGGER}
        assert logging.INFO in levels, (
            "the undo said nothing about a row it could not remove")
        assert logging.WARNING not in levels, (
            "a locked vault was reported as a fault, so the vault clause "
            "in the undo never ran")

    @pytest.mark.anyio
    async def test_a_working_undo_really_removes_the_running_row(
            self, client, monkeypatch) -> None:
        """GROUND CONTROL for the pair above, and for the callback itself.

        A real chat, a real `running` row, a real claim - so "it did not
        raise" is backed by the two writes actually landing.
        """
        chat_id = make_chat(client, make_character(client))
        plan = self._plan(chat_id)

        with get_db() as con:
            notebook.claim_call(con, config.NOTEBOOK_DAILY_CALL_CAP)
            con.execute(
                "INSERT INTO notebook_extractions "
                "(chat_id, work_key, from_message_id, to_message_id, "
                " status, started_at) "
                "VALUES (?,?,1,2,'running', datetime('now'))",
                (chat_id, plan["work_key"]))
            day = notebook.spend_day(con)
        plan["claim_day"] = day

        async def prepared():
            return (plan, "vendor/cheap", "en")

        task = asyncio.create_task(prepared())
        await task

        w = notebook_worker.Worker()
        w._undo_abandoned_preamble(task)
        await asyncio.gather(*list(w._inflight))

        with get_db() as con:
            left = con.execute(
                "SELECT COUNT(*) FROM notebook_extractions "
                "WHERE work_key = ? AND status = 'running'",
                (plan["work_key"],)).fetchone()[0]
        assert left == 0, (
            "the running row survived a healthy undo, so the two tests "
            "above are watching a callback that does nothing")
        assert calls_today() == 0, (
            "the slot was not given back on a healthy undo")
