"""Prompt modules in the vault: rows, order, the enabled set, two settings.

Tables (database.py): `prompt_module_imports` holds one row per imported file
with its preset-level fields (secret-stripped, no entry text) and its report; `prompt_modules` holds
one row per entry, every structural SillyTavern field in its own column plus
the entry's raw JSON. The preset fields and the raw entry JSON are never returned
by any function a router serialises, and nothing here logs text or names.

Every function takes the caller's connection, so a toggle's read-check-write
runs in one transaction.
"""

from __future__ import annotations

import json
import sqlite3

from prompt_sanitizer import strip_space

from . import errors as E
from .limits import MODULE_MAX_CHARS, MODULE_NAME_MAX_CHARS, REPLY_LENGTH_WORDS_ALLOWED
from .parsers import KIND_MODULE, ImportResult

SETTING_LAYER_ENABLED = "prompt_layer_enabled"
SETTING_REPLY_LENGTH_WORDS = "reply_length_words"

# Why a row cannot be switched on, as stable keys.
BLOCK_MARKER = "marker"
BLOCK_EMPTY = "empty_content"
BLOCK_OVER_CAP = "over_char_cap"
BLOCK_NEVER_TRIGGERS = "never_triggers"

#: Columns a router may serialise. `raw_entry_json` is deliberately absent.
_PUBLIC_COLS = (
    "id, import_id, kind, name, content, note, enabled, sort_order, revision, "
    "source_type, source_filename, st_identifier, st_enabled, st_role, "
    "st_system_prompt, st_marker, st_injection_position, st_injection_depth, "
    "st_injection_order, st_injection_trigger, st_forbid_overrides, "
    "st_extension, after_history, triggers, badges, created_at, updated_at"
)


def _b(v: bool | None) -> int | None:
    return None if v is None else int(bool(v))


def block_reason(row: sqlite3.Row | dict) -> str | None:
    """None when the row may be enabled, else the reason key."""
    if row["kind"] != KIND_MODULE:
        return BLOCK_MARKER
    # "Blank" is the sanitizer table's whitespace set, the one set both sides
    # share; assembly.ts moduleFires is the frontend copy of this function.
    if not strip_space(row["content"] or ""):
        return BLOCK_EMPTY
    if len(row["content"]) > MODULE_MAX_CHARS:
        return BLOCK_OVER_CAP
    if not json.loads(row["triggers"] or "[]"):
        return BLOCK_NEVER_TRIGGERS
    return None


def row_to_public(row: sqlite3.Row) -> dict:
    d = {k: row[k] for k in row.keys()}
    for k in ("enabled", "st_marker", "after_history"):
        d[k] = bool(d[k])
    for k in ("st_enabled", "st_system_prompt", "st_forbid_overrides", "st_extension"):
        d[k] = None if d[k] is None else bool(d[k])
    d["st_injection_trigger"] = (None if d["st_injection_trigger"] is None
                                 else json.loads(d["st_injection_trigger"]))
    d["triggers"] = json.loads(d["triggers"] or "[]")
    d["badges"] = json.loads(d["badges"] or "[]")
    d["chars"] = len(d["content"] or "")
    reason = block_reason(row)
    d["can_enable"] = reason is None
    d["blocked_reason"] = reason
    return d


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

def insert_import(con: sqlite3.Connection, result: ImportResult,
                  filename: str) -> int:
    """Store one parsed file: the import row, then every entry after the
    current last row, in file order. Everything starts OFF (H3)."""
    cur = con.execute(
        "INSERT INTO prompt_module_imports (source_type, source_filename, "
        "preset_json, report_json) VALUES (?, ?, ?, ?)",
        (result.source_type, filename,
         json.dumps(result.preset_json, ensure_ascii=False),
         json.dumps(result.report, ensure_ascii=False)))
    import_id = cur.lastrowid
    base = con.execute(
        "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM prompt_modules"
    ).fetchone()[0]
    for n, m in enumerate(result.modules):
        con.execute(
            "INSERT INTO prompt_modules (import_id, kind, name, content, note, "
            "enabled, sort_order, source_type, source_filename, st_identifier, "
            "st_enabled, st_role, st_system_prompt, st_marker, "
            "st_injection_position, st_injection_depth, st_injection_order, "
            "st_injection_trigger, st_forbid_overrides, st_extension, "
            "after_history, triggers, badges, raw_entry_json) "
            "VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "?, ?, ?, ?)",
            (import_id, m.kind, m.name, m.content, m.note, base + n,
             result.source_type, filename, m.st_identifier, _b(m.st_enabled),
             m.st_role, _b(m.st_system_prompt), int(m.st_marker),
             m.st_injection_position, m.st_injection_depth,
             m.st_injection_order,
             None if m.st_injection_trigger is None
             else json.dumps(m.st_injection_trigger),
             _b(m.st_forbid_overrides), _b(m.st_extension),
             int(m.after_history), json.dumps(m.triggers),
             json.dumps(m.badges),
             None if m.raw_entry is None
             else json.dumps(m.raw_entry, ensure_ascii=False)))
    return import_id


def get_row(con: sqlite3.Connection, module_id: int) -> sqlite3.Row:
    row = con.execute(f"SELECT {_PUBLIC_COLS} FROM prompt_modules WHERE id = ?",
                      (module_id,)).fetchone()
    if row is None:
        raise E.PromptModuleError(E.NOT_FOUND, 404)
    return row


def list_rows(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute(f"SELECT {_PUBLIC_COLS} FROM prompt_modules "
                       "ORDER BY sort_order, id").fetchall()


def rows_for_import(con: sqlite3.Connection, import_id: int) -> list[sqlite3.Row]:
    return con.execute(f"SELECT {_PUBLIC_COLS} FROM prompt_modules "
                       "WHERE import_id = ? ORDER BY sort_order, id",
                       (import_id,)).fetchall()


def list_imports(con: sqlite3.Connection) -> list[dict]:
    """Import rows without their source copy."""
    return [dict(r) for r in con.execute(
        "SELECT id, source_type, source_filename, created_at "
        "FROM prompt_module_imports ORDER BY id").fetchall()]


def set_enabled(con: sqlite3.Connection, ids: list[int], enabled: bool) -> None:
    for i in ids:
        con.execute("UPDATE prompt_modules SET enabled = ?, "
                    "revision = revision + 1, updated_at = datetime('now') "
                    "WHERE id = ?", (int(enabled), i))


def rename(con: sqlite3.Connection, module_id: int, name: str) -> None:
    if not isinstance(name, str) or not name.strip() \
            or len(name) > MODULE_NAME_MAX_CHARS:
        raise E.PromptModuleError(E.NAME_INVALID)
    con.execute("UPDATE prompt_modules SET name = ?, revision = revision + 1, "
                "updated_at = datetime('now') WHERE id = ?", (name, module_id))


def move(con: sqlite3.Connection, module_id: int, position: int) -> None:
    """Move a row to `position` (0-based, clamped) and renumber everything
    contiguously, so the order is always 0..n-1 with no ties."""
    ids = [r[0] for r in con.execute(
        "SELECT id FROM prompt_modules ORDER BY sort_order, id").fetchall()]
    ids.remove(module_id)
    position = max(0, min(position, len(ids)))
    ids.insert(position, module_id)
    for n, i in enumerate(ids):
        con.execute("UPDATE prompt_modules SET sort_order = ? WHERE id = ? "
                    "AND sort_order IS NOT ?", (n, i, n))


def delete(con: sqlite3.Connection, module_id: int) -> None:
    row = get_row(con, module_id)
    con.execute("DELETE FROM prompt_modules WHERE id = ?", (module_id,))
    imp = row["import_id"]
    if imp is not None:
        left = con.execute(
            "SELECT COUNT(*) FROM prompt_modules WHERE import_id = ? AND kind = ?",
            (imp, KIND_MODULE)).fetchone()[0]
        if left == 0:
            # The last real entry of a file is gone: its structure rows and its
            # source copy go with it, so nothing of the file lingers unseen.
            con.execute("DELETE FROM prompt_modules WHERE import_id = ?", (imp,))
            con.execute("DELETE FROM prompt_module_imports WHERE id = ?", (imp,))


# ---------------------------------------------------------------------------
# Reads the request path uses (contract with B1)
# ---------------------------------------------------------------------------

def list_enabled(con: sqlite3.Connection,
                 generation: str | None = None) -> list[dict]:
    """Enabled modules in stored order: dicts of id, name, content,
    sort_order, revision.

    `generation` filters by trigger: "send" for an ordinary turn (and an edit),
    "regenerate" for a new variant. None returns every enabled module that
    can fire at all, which is what H4 counts (worst case).
    """
    rows = con.execute(
        "SELECT id, name, content, sort_order, revision, triggers, kind "
        "FROM prompt_modules WHERE enabled = 1 AND kind = ? "
        "ORDER BY sort_order, id", (KIND_MODULE,)).fetchall()
    out = []
    for r in rows:
        # One predicate: a row that could not be switched on does not count
        # as on, for the mode or for H4 (the frontend reads the same answer).
        if block_reason(r) is not None:
            continue
        if generation is not None and generation not in json.loads(r["triggers"]):
            continue
        out.append({"id": r["id"], "name": r["name"], "content": r["content"],
                    "sort_order": r["sort_order"], "revision": r["revision"]})
    return out


def layer_enabled(con: sqlite3.Connection) -> bool:
    row = con.execute("SELECT value FROM settings WHERE key = ?",
                      (SETTING_LAYER_ENABLED,)).fetchone()
    return bool(row) and row["value"] == "1"


def reply_length_words(con: sqlite3.Connection) -> int | None:
    row = con.execute("SELECT value FROM settings WHERE key = ?",
                      (SETTING_REPLY_LENGTH_WORDS,)).fetchone()
    if not row:
        return None
    try:
        v = int(row["value"])
    except (TypeError, ValueError):
        return None
    return v if v in REPLY_LENGTH_WORDS_ALLOWED else None


def save_settings(con: sqlite3.Connection, *, layer: bool,
                  words: int | None) -> None:
    con.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (SETTING_LAYER_ENABLED, "1" if layer else "0"))
    if words is None:
        con.execute("DELETE FROM settings WHERE key = ?",
                    (SETTING_REPLY_LENGTH_WORDS,))
    else:
        con.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (SETTING_REPLY_LENGTH_WORDS, str(words)))
