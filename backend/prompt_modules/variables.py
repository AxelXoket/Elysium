"""Per-chat variables for SillyTavern macros ({{setvar}}, {{getvar}}).

Kept in the vault's settings table, one row per assistant reply, keyed
`prompt_module_vars:<chat id>:<reply id>`: the values as they stood after the
turn that produced that reply. A turn starts from the row of the newest
ACTIVE reply before its user message, so:

- regenerate starts from the values before the turn it replaces, because the
  reply being replaced is newer than its user message;
- a deleted reply has no message left to join, so the next turn starts from
  the one before it (the orphan row is removed on the next write);
- switching to another variant switches to that variant's values.

A row is written in the same transaction as the reply it belongs to, so a
turn that was refused, failed or never produced a reply changes nothing.
Deleting or clearing a chat deletes its rows (forget_chat). Global variables
are kept per chat like local ones.

Values are text from module files (and the persona or card name through
{{user}} / {{char}}); nothing here logs them.
"""

from __future__ import annotations

import json
import logging
import sqlite3

logger = logging.getLogger(__name__)

KEY_PREFIX = "prompt_module_vars:"
#: A snapshot larger than this is not kept; the next turn starts from the
#: previous one. Module texts are capped far below it.
VARS_MAX_JSON_CHARS = 1 << 20


def _range(chat_id: int) -> tuple[str, str]:
    """[low, high) of this chat's keys. ";" sorts right after ":"."""
    base = f"{KEY_PREFIX}{int(chat_id)}"
    return base + ":", base + ";"


def pre_turn(con: sqlite3.Connection, chat_id: int,
             before_id: int | None) -> dict:
    """The values a turn starts from. `before_id` is the id of the user
    message the turn answers when it already exists (regenerate, edit);
    None for a new message."""
    lo, hi = _range(chat_id)
    params: list = [len(lo) + 1, lo, hi, chat_id]
    bound = ""
    if before_id is not None:
        bound = "AND m.id < ? "
        params.append(before_id)
    row = con.execute(
        "SELECT s.value FROM settings s JOIN messages m "
        "ON m.id = CAST(substr(s.key, ?) AS INTEGER) "
        "WHERE s.key >= ? AND s.key < ? AND m.chat_id = ? AND m.active = 1 "
        f"AND m.role = 'assistant' {bound}"
        "ORDER BY m.id DESC LIMIT 1", params).fetchone()
    if row is None:
        return {}
    try:
        got = json.loads(row["value"])
    except ValueError:
        return {}
    return got if isinstance(got, dict) else {}


def dump(variables: dict | None) -> str | None:
    """The snapshot text for save(), or None when there is nothing to keep."""
    if variables is None:
        return None
    text = json.dumps(variables, ensure_ascii=False, sort_keys=True, default=str)
    if len(text) > VARS_MAX_JSON_CHARS:
        logger.warning("Prompt module variables too large to keep: %d chars.",
                       len(text))
        return None
    return text


def save(con: sqlite3.Connection, chat_id: int, message_id: int,
         snapshot: str | None) -> None:
    """Record the values after the turn that wrote `message_id`, and drop
    rows whose reply is gone. Call it in the transaction that inserts the
    reply."""
    if snapshot is None:
        return
    lo, hi = _range(chat_id)
    con.execute(
        "DELETE FROM settings WHERE key >= ? AND key < ? AND "
        "CAST(substr(key, ?) AS INTEGER) NOT IN "
        "(SELECT id FROM messages WHERE chat_id = ?)",
        (lo, hi, len(lo) + 1, chat_id))
    con.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (f"{lo}{int(message_id)}", snapshot))


def forget_chat(con: sqlite3.Connection, chat_id: int) -> None:
    """Delete every row of a chat (chat deleted or cleared)."""
    lo, hi = _range(chat_id)
    con.execute("DELETE FROM settings WHERE key >= ? AND key < ?", (lo, hi))


def rows_for_chat(con: sqlite3.Connection, chat_id: int) -> int:
    lo, hi = _range(chat_id)
    return con.execute("SELECT COUNT(*) FROM settings WHERE key >= ? AND key < ?",
                       (lo, hi)).fetchone()[0]
