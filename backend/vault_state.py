"""vault_state.py - process-wide holder for the unlocked DB key.

Deliberately tiny and import-cycle-free: database.py reads it on every
connection open; the vault router writes it on unlock/lock. The key lives
only in RAM (accepted boundary of the threat model) and is never logged.

WHAT LOCKING ACTUALLY DOES TO THE KEY, precisely, because the honest version
is shorter than the reassuring one:

  * held as a bytearray, not bytes, so clear_key() can OVERWRITE it. `bytes`
    is immutable - dropping the reference only asks the garbage collector to
    get round to it, and until then the key sits in the heap of a process the
    user believes is locked. This is the one copy that lives for hours, and it
    is the one this can do something about.
  * every other copy is outside Python's reach and is NOT claimed to be gone:
    the hex string built per connection (str is immutable), the SQL text
    handed to SQLCipher, SQLCipher's own derived key material, and anything
    the OS paged out. Zeroing one buffer is worth doing; calling it "the key
    is wiped from memory" would be a lie.
"""

from __future__ import annotations

import threading
import time

_db_key: bytearray | None = None

# ---------------------------------------------------------------- idle clock
#
# An unlocked vault is a decrypted vault, for as long as the window is open.
# The threat this answers is the ordinary one: a laptop left on a desk, a
# shared machine, a screen somebody else walks up to. Locking on idle is the
# only thing that shortens that window without asking the user to remember.
#
# Two numbers, not one. A timestamp alone would lock in the middle of a
# forty-minute streamed reply, because activity is recorded when a request
# ARRIVES and a long stream sends nothing after that. The in-flight count is
# what makes "idle" mean "nothing is happening" rather than "nothing started
# recently".
_lock = threading.Lock()
_last_activity: float = time.monotonic()
_in_flight: int = 0


class VaultLockedError(Exception):
    """Raised by the DB layer when a connection is requested while locked."""


def set_key(key: bytes) -> None:
    """Take a copy we own, so clear_key() has something it can overwrite."""
    global _db_key
    with _lock:
        previous = _db_key
        _db_key = bytearray(key)
    _zero(previous)


def clear_key() -> None:
    """Overwrite the key, then drop it.

    The overwrite is the point. Setting the reference to None hands the bytes
    to the garbage collector and no further: until a collection happens, and
    possibly long after, the key is still readable in the heap of a process
    whose window says "locked".
    """
    global _db_key
    with _lock:
        previous = _db_key
        _db_key = None
    _zero(previous)


def _zero(buffer: bytearray | None) -> None:
    """Overwrite in place. Called outside the lock: by the time it runs the
    buffer is unreachable from _db_key, so nobody can obtain it any more."""
    if buffer is None:
        return
    for index in range(len(buffer)):
        buffer[index] = 0


def get_key() -> bytes:
    """A snapshot, taken under the lock.

    Handing out the live buffer was cheaper and wrong. Requests run on worker
    threads (anyio.to_thread) while the idle watchdog runs on the event loop,
    so a connection opening in one thread could be part way through key.hex()
    when clear_key() zeroed the bytes underneath it - deriving a corrupted key
    rather than raising VaultLockedError. asyncio.Lock in the vault router
    serialises coroutines and does nothing about that.

    The copy is short-lived and immutable, so it cannot be zeroed. That is
    accepted: the copy this module exists to destroy is the one that lives for
    the whole session, and that one is still a bytearray and still overwritten
    on lock.
    """
    with _lock:
        if _db_key is None:
            raise VaultLockedError("vault_locked")
        return bytes(_db_key)


def is_unlocked() -> bool:
    return _db_key is not None


def touch() -> None:
    """Record that the user did something."""
    global _last_activity
    with _lock:
        _last_activity = time.monotonic()


def enter_request() -> None:
    global _in_flight, _last_activity
    with _lock:
        _in_flight += 1
        _last_activity = time.monotonic()


def leave_request() -> None:
    """A request finished. The clock restarts HERE, not when it arrived.

    A streamed reply can run for many minutes; treating its arrival as the
    last activity would lock the vault out from under it and end the reply
    the user is reading.
    """
    global _in_flight, _last_activity
    with _lock:
        _in_flight = max(0, _in_flight - 1)
        _last_activity = time.monotonic()


def idle_seconds() -> float:
    """How long nothing has been happening. Zero while a request is running."""
    with _lock:
        if _in_flight > 0:
            return 0.0
        return max(0.0, time.monotonic() - _last_activity)


def reset_idle_clock() -> None:
    """Test seam, and what unlock calls so a fresh session starts at zero."""
    global _last_activity, _in_flight
    with _lock:
        _last_activity = time.monotonic()
        _in_flight = 0
