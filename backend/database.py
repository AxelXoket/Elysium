"""database.py — Raw sqlite3 helpers. No ORM.

Public API:
    init_db()          — idempotent schema bootstrap, called once at startup.
    get_db()           — context manager yielding a committed/rolled-back connection.
    get_setting()      — read one settings row; returns default if missing.
    set_setting()      — upsert one settings row (INSERT OR REPLACE).
    delete_setting()   — delete one settings row; silent if missing.

Rules:
- WAL mode is set before any DDL.
- row_factory=sqlite3.Row so callers can use dict(row) on any result.
- Commit on clean exit, rollback on exception, always close.
- Message order is always enforced by the caller with ORDER BY id ASC.
"""

import sqlite3
import contextlib
import logging
from typing import Generator

from config import DB_PATH

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
_SCHEMA = """\
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS characters (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    name                     TEXT NOT NULL,
    description              TEXT NOT NULL DEFAULT '',
    personality              TEXT NOT NULL DEFAULT '',
    scenario                 TEXT NOT NULL DEFAULT '',
    first_mes                TEXT NOT NULL DEFAULT '',
    mes_example              TEXT NOT NULL DEFAULT '',
    system_prompt            TEXT NOT NULL DEFAULT '',
    post_history_instruction TEXT NOT NULL DEFAULT '',
    tags                     TEXT NOT NULL DEFAULT '[]',
    raw_json                 TEXT NOT NULL DEFAULT '{}',
    created_at               TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS chats (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id INTEGER NOT NULL REFERENCES characters(id),
    title        TEXT,
    model_id     TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL REFERENCES chats(id),
    role       TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_messages_chat_id ON messages(chat_id);

CREATE TABLE IF NOT EXISTS personas (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    display_name TEXT    NOT NULL,
    description  TEXT    NOT NULL DEFAULT '',
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def init_db() -> None:
    """Create all tables if they don't exist. Safe to call on every startup.

    executescript() commits implicitly before running each statement, which is
    intentional here — DDL does not need transactional rollback semantics.
    """
    con = sqlite3.connect(DB_PATH)
    try:
        con.executescript(_SCHEMA)
        logger.info("Database ready at %s", DB_PATH)
    finally:
        con.close()


@contextlib.contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    """Yield a sqlite3 connection. Commits on success, rolls back on exception."""
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------

def get_setting(key: str, default: str | None = None) -> str | None:
    """Read one settings row. Returns default if the key does not exist."""
    with get_db() as con:
        row = con.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    """Upsert one settings row. Atomic via ON CONFLICT."""
    with get_db() as con:
        con.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def delete_setting(key: str) -> None:
    """Delete one settings row. Silent if key does not exist."""
    with get_db() as con:
        con.execute("DELETE FROM settings WHERE key = ?", (key,))

