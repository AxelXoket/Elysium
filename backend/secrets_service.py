"""secrets_service.py - app secrets sealed inside the encrypted vault DB.

Replaces the OS-keyring storage (E5): the OpenRouter API key and the proxy
URL live as rows in `vault_secrets`, inside the SQLCipher-encrypted app.db.
Consequences, all intentional:

- Locked vault => secrets unreachable: get/set/delete raise VaultLockedError,
  which the API layer already maps to 423 (the routes that need secrets are
  all behind the vault gate; the SSE mid-stream window catches it too).
- Passphrase change re-keys the secrets together with everything else.
- Nothing secret remains readable by other same-user software while locked
  (the old keyring entries were).

Every function accepts an optional `conn`: pass the caller's connection to
join an existing transaction (e.g. save_proxy writes secret + alias + flag
atomically); omit it for a self-contained read/write. Secret VALUES are
never logged - callers must uphold the same rule.
"""

from __future__ import annotations

import sqlite3

from database import get_db


def get_secret(name: str, *, conn: sqlite3.Connection | None = None) -> str | None:
    """Return the secret's value, or None if unset. VaultLockedError while
    locked (via get_db) - never swallowed here."""
    if conn is not None:
        row = conn.execute(
            "SELECT value FROM vault_secrets WHERE name = ?", (name,)
        ).fetchone()
        return row[0] if row is not None else None
    with get_db() as con:
        row = con.execute(
            "SELECT value FROM vault_secrets WHERE name = ?", (name,)
        ).fetchone()
        return row[0] if row is not None else None


def set_secret(name: str, value: str, *, conn: sqlite3.Connection | None = None) -> None:
    """Upsert the secret. With `conn`, joins the caller's transaction (the
    caller commits); without, commits on its own (get_db context exit)."""
    sql = (
        "INSERT INTO vault_secrets (name, value) VALUES (?, ?) "
        "ON CONFLICT(name) DO UPDATE SET value = excluded.value"
    )
    if conn is not None:
        conn.execute(sql, (name, value))
        return
    with get_db() as con:
        con.execute(sql, (name, value))


def delete_secret(name: str, *, conn: sqlite3.Connection | None = None) -> None:
    """Delete the secret if present (no-op otherwise)."""
    sql = "DELETE FROM vault_secrets WHERE name = ?"
    if conn is not None:
        conn.execute(sql, (name,))
        return
    with get_db() as con:
        con.execute(sql, (name,))
