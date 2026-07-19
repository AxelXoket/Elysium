"""vault_state.py - process-wide holder for the unlocked DB key.

Deliberately tiny and import-cycle-free: database.py reads it on every
connection open; the vault router writes it on unlock/lock. The key lives
only in RAM (accepted boundary of the threat model) and is never logged.
"""

from __future__ import annotations

_db_key: bytes | None = None


class VaultLockedError(Exception):
    """Raised by the DB layer when a connection is requested while locked."""


def set_key(key: bytes) -> None:
    global _db_key
    _db_key = key


def clear_key() -> None:
    global _db_key
    _db_key = None


def get_key() -> bytes:
    if _db_key is None:
        raise VaultLockedError("vault_locked")
    return _db_key


def is_unlocked() -> bool:
    return _db_key is not None
