"""The idle-lock watchdog read its setting on the event loop.

`watch()` wakes every TICK_S and asks `should_lock()`. That question opens
SQLCipher through `database.get_setting`, and `get_db` queues on a held write
lock for its default busy_timeout before it gives up. Asked straight from the
coroutine, the queue is the EVENT LOOP's queue: every SSE stream in the
process stops for as long as the writer holds on, and the thing that stops
them is the feature whose entire job is to be invisible until it locks.

Two things are worth writing down about the shape of this defect, because
both of them shaped the tests below.

The first is that the watchdog is coarse on purpose - TICK_S is thirty
seconds, not one - so the exposure is one window per half minute rather than
one per second. That makes it rarer, not narrower: the window is as wide as
the busy_timeout whenever it does open.

The second is that a live reproduction needs `PRAGMA locking_mode =
EXCLUSIVE`, which this application never sets. Measured against a held write
lock: 13.4s of unbroken freeze under EXCLUSIVE, 0.017s under the plain WAL
mode the app actually runs in, because `get_setting` is a bare SELECT and WAL
exists precisely so a reader never waits for a writer. So the tests here do
not stage a contended database. Staging one either proves nothing (plain WAL)
or costs the whole busy_timeout in wall clock (EXCLUSIVE), and neither is
what the fix is about. What the fix is about is WHERE the synchronous call
runs, and that is asserted directly - by thread identity on the real
`get_setting`, and by loop_guard's freeze measurement against a stall of a
length this suite can afford.
"""
from __future__ import annotations

import asyncio
import contextlib
import threading
import time

import pytest

import auto_lock
import database
import vault_state

from tests.loop_guard import (
    MAX_FREEZE_S,
    STALL_S,
    longest_freeze as _longest_freeze,
)


@pytest.fixture(autouse=True)
def _measure_without_locking_the_vault(monkeypatch: pytest.MonkeyPatch):
    """vault_state is process-global and some of these tests drive the REAL
    `should_lock`, so a stale idle clock left by an earlier test would have
    the watchdog lock the vault out from under whatever runs next. The clock
    is reset at both ends and the lock itself is stubbed; the one test that
    wants the locking branch installs its own recorder over this."""
    vault_state.reset_idle_clock()

    async def _do_not_really_lock() -> None:
        return None

    monkeypatch.setattr(auto_lock, "lock_now", _do_not_really_lock)
    yield
    vault_state.reset_idle_clock()


def _remember_this_thread(record: list[int]) -> bool:
    """Stands in for `should_lock`, and records where it was answered."""
    record.append(threading.get_ident())
    return False


def _minutes_from_this_thread(record: list[int]) -> int:
    """Stands in for `configured_minutes`, same question."""
    record.append(threading.get_ident())
    return 5


async def _one_check(record: list, timeout: float = 5.0) -> None:
    """Run the watchdog until one check has finished, then stop it.

    `watch()` is a forever loop, so nothing can await it to completion. The
    record the check appends to IS the completion signal, and it is appended
    to LAST so that polling for it cannot outrun the work being measured.
    """
    task = asyncio.ensure_future(auto_lock.watch(tick=0.001))
    deadline = time.monotonic() + timeout
    try:
        while not record and time.monotonic() < deadline:
            await asyncio.sleep(0.001)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


@pytest.mark.anyio
async def test_the_check_is_not_answered_on_the_event_loop_thread(
    anyio_backend, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole of the fix, stated as the property it buys: whatever
    `should_lock` costs, it is not the loop that pays it."""
    seen: list[int] = []
    monkeypatch.setattr(auto_lock, "should_lock",
                        lambda: _remember_this_thread(seen))
    loop_thread = threading.get_ident()

    await _one_check(seen)

    assert seen, "the watchdog never ran its check"
    assert loop_thread not in seen, (
        "should_lock was answered on the event loop thread, so a SQLCipher "
        "open that queues on a held lock queues the whole process behind it")

    # Ground control for the comparison itself. `loop_thread not in seen`
    # would also pass if the recorder were broken, or if thread ids here did
    # not mean what this test assumes they mean.
    called_straight_from_here: list[int] = []
    _remember_this_thread(called_straight_from_here)
    assert called_straight_from_here == [loop_thread], (
        "the instrument cannot tell this thread apart from another one, so "
        "the assertion above proves nothing")


@pytest.mark.anyio
async def test_the_real_setting_read_reaches_sqlcipher_off_the_loop(
    anyio_backend, db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The test above replaces `should_lock`, so it pins where `watch()` puts
    a call and nothing at all about where the database open ends up. This one
    leaves the whole real chain alone - `should_lock`, `configured_minutes`,
    `get_setting`, `get_db` - and watches the layer that opens the file.
    """
    seen: list[int] = []
    real_get_setting = database.get_setting

    def note_the_thread(name: str):
        seen.append(threading.get_ident())
        return real_get_setting(name)

    # `configured_minutes` imports get_setting inside its own body, so the
    # lookup happens per call against this module object and the patch is
    # seen from the worker thread too.
    monkeypatch.setattr(database, "get_setting", note_the_thread)
    loop_thread = threading.get_ident()

    await _one_check(seen)

    assert seen, "the watchdog never read its setting from the database"
    assert loop_thread not in seen, (
        "the SQLCipher open still happens on the event loop thread")


@pytest.mark.anyio
async def test_a_check_that_blocks_does_not_freeze_the_loop(
    anyio_backend, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What a user would feel. The sleep stands in for the busy_timeout queue
    at a length the suite can afford; the assertion is loop_guard's, so it
    fails from 50% blocking upwards rather than only on a total freeze."""
    done: list[int] = []

    def a_check_that_waits_on_the_database() -> bool:
        time.sleep(STALL_S)
        return _remember_this_thread(done)

    monkeypatch.setattr(auto_lock, "should_lock",
                        a_check_that_waits_on_the_database)

    freeze, _ = await _longest_freeze(_one_check(done))

    assert done, "the watchdog never ran its check"
    assert freeze < MAX_FREEZE_S, (
        f"the event loop went {freeze:.4f}s without control while the "
        f"watchdog waited on the database (ceiling {MAX_FREEZE_S}s)")


@pytest.mark.anyio
async def test_the_same_stall_placed_on_the_loop_is_seen(
    anyio_backend
) -> None:
    """Positive control for the ceiling above. A measurement that always
    reported "short" would pass the test above whatever `watch()` did, so the
    identical stall, deliberately left on the loop, has to break the ceiling.
    """
    async def block_on_purpose() -> None:
        time.sleep(STALL_S)

    freeze, _ = await _longest_freeze(block_on_purpose())

    assert freeze >= MAX_FREEZE_S, (
        f"a {STALL_S}s stall put on the event loop on purpose measured "
        f"{freeze:.4f}s, so this instrument cannot see one that arrives by "
        f"accident either")


@pytest.mark.anyio
async def test_the_minutes_it_logs_are_read_off_the_loop_too(
    anyio_backend, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second synchronous database read in the same tick, and the easier
    one to leave behind: it only runs on the branch that locks. It opens the
    same file through the same `get_setting`.
    """
    seen: list[int] = []
    locked: list[str] = []

    async def note_the_lock() -> None:
        locked.append("locked")

    monkeypatch.setattr(auto_lock, "should_lock", lambda: True)
    monkeypatch.setattr(auto_lock, "configured_minutes",
                        lambda: _minutes_from_this_thread(seen))
    monkeypatch.setattr(auto_lock, "lock_now", note_the_lock)
    loop_thread = threading.get_ident()

    # Polls on the lock, not on the read: the read happens first, so waiting
    # for the later of the two cannot miss either.
    await _one_check(locked)

    assert locked == ["locked"], "the locking branch never ran"
    assert seen, "the minutes were never read"
    assert loop_thread not in seen, (
        "configured_minutes was answered on the event loop thread")
