"""v1.1 Faz 2: FB6 (messages_common) + I13 (messages.updated_at migration).

_migrate is pure sqlite (encryption plays no role in DDL), so these tests
drive it directly on throwaway connections that replicate the REAL v1.0
schema - the honest equivalent of opening an old DB copy with the new build.
"""

import sqlite3

from database import _migrate, _SCHEMA
from messages_common import last_active_anchor, msg_to_dict


# The messages table exactly as v1.0 shipped it (pre-variant, pre-updated_at)
# plus the minimum sibling tables _migrate's index statements reference.
_V10_SCHEMA = """
CREATE TABLE characters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE chats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id INTEGER NOT NULL REFERENCES characters(id),
    title TEXT,
    model_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL REFERENCES chats(id),
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER REFERENCES messages(id),
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _old_db() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(_V10_SCHEMA)
    con.execute("INSERT INTO characters (name) VALUES ('C')")
    con.execute("INSERT INTO chats (character_id) VALUES (1)")
    con.execute(
        "INSERT INTO messages (chat_id, role, content, created_at) "
        "VALUES (1, 'user', 'old row', '2025-01-02 03:04:05')"
    )
    con.commit()
    return con


def test_migrate_adds_updated_at_with_deterministic_backfill():
    con = _old_db()
    _migrate(con)

    cols = {r[1] for r in con.execute("PRAGMA table_info(messages)").fetchall()}
    assert "updated_at" in cols
    row = con.execute(
        "SELECT created_at, updated_at FROM messages WHERE id = 1"
    ).fetchone()
    # Never-edited rows stamp updated_at = created_at (I13 determinism).
    assert row["updated_at"] == row["created_at"] == "2025-01-02 03:04:05"
    assert con.execute("PRAGMA user_version").fetchone()[0] == 2


def test_migrate_is_idempotent():
    con = _old_db()
    _migrate(con)
    before = con.execute("SELECT id, updated_at FROM messages").fetchall()
    _migrate(con)  # second boot on an already-migrated DB
    after = con.execute("SELECT id, updated_at FROM messages").fetchall()
    assert [tuple(r) for r in before] == [tuple(r) for r in after]
    assert con.execute("PRAGMA user_version").fetchone()[0] == 2


def test_migrate_heals_crash_window_nulls():
    """Crash between ALTER and backfill leaves NULL updated_at - the next
    boot's unconditional backfill heals it instead of bricking (I13)."""
    con = _old_db()
    con.execute("ALTER TABLE messages ADD COLUMN updated_at TEXT")  # simulated crash point
    con.commit()
    _migrate(con)
    nulls = con.execute(
        "SELECT COUNT(*) FROM messages WHERE updated_at IS NULL"
    ).fetchone()[0]
    assert nulls == 0


def test_fresh_schema_has_not_null_updated_at():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(_SCHEMA)
    _migrate(con)
    con.execute("INSERT INTO characters (name) VALUES ('C')")
    con.execute("INSERT INTO chats (character_id) VALUES (1)")
    con.execute("INSERT INTO messages (chat_id, role, content) VALUES (1, 'user', 'x')")
    row = con.execute("SELECT updated_at FROM messages WHERE id = 1").fetchone()
    assert row["updated_at"] is not None


# ── FB6: shared helpers behave like the copies they replaced ────────────────

def test_last_active_anchor_semantics():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(_SCHEMA)
    _migrate(con)
    con.execute("INSERT INTO characters (name) VALUES ('C')")
    con.execute("INSERT INTO chats (character_id) VALUES (1)")
    assert last_active_anchor(con, 1) is None  # empty chat

    con.execute("INSERT INTO messages (chat_id, role, content) VALUES (1, 'user', 'q')")
    assert last_active_anchor(con, 1) == 1  # singleton: anchor = own id

    # Variant pair: rows 2+3 share group 2; 3 is active -> anchor stays 2.
    con.execute(
        "INSERT INTO messages (chat_id, role, content, variant_group, active) "
        "VALUES (1, 'assistant', 'a1', 2, 0)"
    )
    con.execute(
        "INSERT INTO messages (chat_id, role, content, variant_group, active) "
        "VALUES (1, 'assistant', 'a2', 2, 1)"
    )
    assert last_active_anchor(con, 1) == 2


def test_msg_to_dict_defensive_keys():
    """Rows from older SELECTs (no variant columns) still map safely."""
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(_SCHEMA)
    _migrate(con)  # variant columns come from the migration, not _SCHEMA
    con.execute("INSERT INTO characters (name) VALUES ('C')")
    con.execute("INSERT INTO chats (character_id) VALUES (1)")
    con.execute("INSERT INTO messages (chat_id, role, content) VALUES (1, 'user', 'q')")
    narrow = con.execute(
        "SELECT id, chat_id, role, content, created_at FROM messages WHERE id = 1"
    ).fetchone()
    d = msg_to_dict(narrow)
    assert d["variant_group"] is None and d["active"] is True
    assert d["attachments"] == []

    wide = con.execute(
        "SELECT id, chat_id, role, content, created_at, variant_group, active "
        "FROM messages WHERE id = 1"
    ).fetchone()
    d2 = msg_to_dict(wide, variant_index=0, variant_count=1)
    assert d2["variant_index"] == 0 and d2["variant_count"] == 1
