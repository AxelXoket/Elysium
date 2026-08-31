"""U-23 - `None` meant two things, and one of them was a broken app.

`extract_model` read the user's chosen model under a bare
`except Exception: return None`. The caller reads `None` as "no model
chosen" and stops without a word, which is right when it is true. When it
was not true - an unreadable settings row, a disk error - the same silence
came out: no row, no counter, no log line anywhere in the process, and a
panel saying suggestions were off while they were on and broken.

The fix is to stop swallowing. `run()` in `notebook_worker` already knows
the difference between the two failures that matter, and has known it since
long before this: a locked vault is skipped in silence, because background
work deliberately does not feed the idle timer and an ordinary lock cycle is
not an error, and everything else lands on the status panel by type name.
"""
from __future__ import annotations

import asyncio

import pytest

import config
import database
import notebook_extract
import notebook_worker

from tests.conftest import make_character, make_chat


@pytest.fixture
def anyio_backend():
    return "asyncio"


def seed(client) -> int:
    chat_id = make_chat(client, make_character(client))
    with database.get_db() as con:
        for i in range(4):
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


async def drain(w: notebook_worker.Worker, chat_id: int) -> None:
    """One trip through the supervised loop, then stop it.

    Through `run()` rather than `_handle` on purpose: the whole claim is that
    the failure reaches the SUPERVISOR, which is what puts it on the status
    screen. Calling `_handle` directly would prove only that something was
    raised, in a place nobody reads.
    """
    task = asyncio.create_task(w.run())
    w.queue.put_nowait(chat_id)
    await asyncio.wait_for(w.queue.join(), timeout=5)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


class TestAnUnreadableSettingIsNotAChoice:
    @pytest.mark.anyio
    async def test_the_failure_reaches_the_status_screen(
            self, client, monkeypatch) -> None:
        chat_id = seed(client)

        def boom(*a, **kw):
            raise RuntimeError("the settings row could not be read")

        monkeypatch.setattr(database, "get_setting", boom)

        w = worker()
        await drain(w, chat_id)

        status = w.status()
        assert status["unhandled"] == 1
        assert status["last_error"] == "RuntimeError"

    @pytest.mark.anyio
    async def test_nothing_chosen_stays_silent(self, client) -> None:
        """GROUND CONTROL. Without it the test above is satisfied by an
        application that treats every read as a failure, which would put an
        error on the panel of everyone who simply has not turned the feature
        on - the overwhelming majority, since it is off by default.
        """
        chat_id = seed(client)
        assert notebook_extract.extract_model() is None, "ground: unset"

        w = worker()
        await drain(w, chat_id)

        assert w.status()["unhandled"] == 0
        assert w.status()["last_error"] is None

    @pytest.mark.anyio
    async def test_an_empty_setting_stays_silent(self, client) -> None:
        """POSITIVE CONTROL for the ground above, and a real state.

        The setting can be present and empty - the model picker writes `""`
        when the empty option is chosen. A healthy read of an empty value is
        a choice to have the feature off, not a failure, and it must not
        raise or count.
        """
        chat_id = seed(client)
        database.set_setting(config.SETTING_NOTEBOOK_MODEL, "")
        assert notebook_extract.extract_model() is None

        w = worker()
        await drain(w, chat_id)

        assert w.status()["unhandled"] == 0
        assert w.status()["last_error"] is None


class TestALockedVaultIsStillNotAnError:
    @pytest.mark.anyio
    async def test_it_is_skipped_without_a_mark(
            self, client, monkeypatch) -> None:
        """The case letting the exception travel could have broken.

        `run()` singles out `VaultLockedError` and continues quietly, because
        background work does not feed the idle timer and an ordinary
        lock/unlock cycle used to leave a queueful of "unhandled errors" on
        the panel. A wrapper exception around this read would have hidden the
        type and undone that.
        """
        import vault_state

        chat_id = seed(client)

        def locked(*a, **kw):
            raise vault_state.VaultLockedError()

        monkeypatch.setattr(database, "get_setting", locked)

        w = worker()
        await drain(w, chat_id)

        assert w.status()["unhandled"] == 0, "a lock was counted as a fault"
        assert w.status()["last_error"] is None
