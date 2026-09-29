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
from .st_regex import prompt_scripts
from .parsers import (
    BADGE_NOT_IN_ORDER,
    KIND_MARKER,
    KIND_MODULE,
    SOURCE_ST_PROMPT_MANAGER,
    ImportResult,
)

SETTING_LAYER_ENABLED = "prompt_layer_enabled"
SETTING_REPLY_LENGTH_WORDS = "reply_length_words"
#: Per import, "1" when history cleanup is on; no row means off.
SETTING_HISTORY_CLEANUP_PREFIX = "prompt_module_history_cleanup:"

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
    out = []
    for r in con.execute(
            "SELECT id, source_type, source_filename, created_at "
            "FROM prompt_module_imports ORDER BY id").fetchall():
        d = dict(r)
        d["history_cleanup"] = history_cleanup(con, r["id"])
        d["cleanup_scripts"] = len(prompt_scripts(regex_scripts(con, r["id"])))
        out.append(d)
    return out


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
            con.execute("DELETE FROM settings WHERE key = ?", (_cleanup_key(imp),))


# ---------------------------------------------------------------------------
# Reads the request path uses (contract with B1)
# ---------------------------------------------------------------------------

#: Columns list_enabled reads. Placement needs every st_* field that says
#: where a part goes; the raw entry JSON is still never read here.
_ENABLED_COLS = (
    "id, import_id, kind, name, content, sort_order, revision, triggers, "
    "badges, source_type, st_identifier, st_role, st_injection_position, "
    "st_injection_depth, st_injection_order, st_forbid_overrides, "
    "after_history"
)


def module_dict(row) -> dict:
    """One module as the request path reads it: text, order and placement."""
    badges = json.loads(row["badges"] or "[]") if "badges" in row.keys() else []
    return {
        "id": row["id"], "name": row["name"], "content": row["content"],
        "sort_order": row["sort_order"], "revision": row["revision"],
        "import_id": row["import_id"], "source_type": row["source_type"],
        "st_identifier": row["st_identifier"], "st_role": row["st_role"],
        "st_injection_position": row["st_injection_position"],
        "st_injection_depth": row["st_injection_depth"],
        "st_injection_order": row["st_injection_order"],
        "st_forbid_overrides": (None if row["st_forbid_overrides"] is None
                                else bool(row["st_forbid_overrides"])),
        "after_history": bool(row["after_history"]),
        "in_order": BADGE_NOT_IN_ORDER not in badges,
    }


def list_enabled(con: sqlite3.Connection,
                 generation: str | None = None) -> list[dict]:
    """Enabled modules in stored order, as module_dict gives them.

    `generation` filters by trigger: "send" for an ordinary turn (and an edit),
    "regenerate" for a new variant. None returns every enabled module that
    can fire at all, which is what H4 counts (worst case).
    """
    rows = con.execute(
        f"SELECT {_ENABLED_COLS} FROM prompt_modules "
        "WHERE enabled = 1 AND kind = ? ORDER BY sort_order, id",
        (KIND_MODULE,)).fetchall()
    out = []
    for r in rows:
        # One predicate: a row that could not be switched on does not count
        # as on, for the mode or for H4 (the frontend reads the same answer).
        if block_reason(r) is not None:
            continue
        if generation is not None and generation not in json.loads(r["triggers"]):
            continue
        out.append(module_dict(r))
    return out


def list_markers(con: sqlite3.Connection,
                 import_ids: set[int] | None = None) -> list[dict]:
    """The placeholders of every import with a module switched on (or of
    `import_ids`, for a module about to be switched on), in stored order,
    minus the ones the preset itself had off: a card field whose
    placeholder was off goes to the labelled card block instead."""
    rows = con.execute(
        "SELECT id, import_id, st_identifier, st_enabled, sort_order "
        "FROM prompt_modules WHERE kind = ? AND import_id IS NOT NULL "
        "ORDER BY sort_order, id", (KIND_MARKER,)).fetchall()
    if import_ids is None:
        import_ids = {r["import_id"] for r in con.execute(
            "SELECT DISTINCT import_id FROM prompt_modules WHERE enabled = 1 "
            "AND kind = ? AND import_id IS NOT NULL", (KIND_MODULE,)).fetchall()}
    return [{"kind": KIND_MARKER, "id": r["id"], "import_id": r["import_id"],
             "st_identifier": r["st_identifier"], "sort_order": r["sort_order"]}
            for r in rows if r["import_id"] in import_ids
            and (r["st_enabled"] is None or r["st_enabled"])]


# ---------------------------------------------------------------------------
# Per-import options: history cleanup
# ---------------------------------------------------------------------------

def _cleanup_key(import_id: int) -> str:
    return f"{SETTING_HISTORY_CLEANUP_PREFIX}{int(import_id)}"


def history_cleanup(con: sqlite3.Connection, import_id: int) -> bool:
    row = con.execute("SELECT value FROM settings WHERE key = ?",
                      (_cleanup_key(import_id),)).fetchone()
    return bool(row) and row["value"] == "1"


def set_history_cleanup(con: sqlite3.Connection, import_id: int,
                        on: bool) -> None:
    """Off is the absence of the row, so a new import starts off."""
    if con.execute("SELECT 1 FROM prompt_module_imports WHERE id = ?",
                   (import_id,)).fetchone() is None:
        raise E.PromptModuleError(E.IMPORT_NOT_FOUND, 404)
    if on:
        con.execute("INSERT INTO settings (key, value) VALUES (?, '1') "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (_cleanup_key(import_id),))
    else:
        con.execute("DELETE FROM settings WHERE key = ?",
                    (_cleanup_key(import_id),))


def _kept_preset(con: sqlite3.Connection, import_id: int) -> dict:
    """The import's kept preset-level fields (the chat completion preset's
    top level, or a Prompt Manager export's `data`)."""
    row = con.execute("SELECT source_type, preset_json FROM prompt_module_imports "
                      "WHERE id = ?", (import_id,)).fetchone()
    if row is None:
        return {}
    try:
        kept = json.loads(row["preset_json"] or "{}")
    except ValueError:
        return {}
    if row["source_type"] == SOURCE_ST_PROMPT_MANAGER:
        kept = kept.get("data")
    return kept if isinstance(kept, dict) else {}


def regex_scripts(con: sqlite3.Connection, import_id: int) -> list[dict]:
    """The preset's regex scripts as the file had them (kept preset fields)."""
    ext = _kept_preset(con, import_id).get("extensions")
    scripts = ext.get("regex_scripts") if isinstance(ext, dict) else None
    if not isinstance(scripts, list):
        return []
    return [s for s in scripts if isinstance(s, dict)]


#: The preset texts the layout sends, with SillyTavern's defaults for a file
#: that does not carry them.
PRESET_TEXT_DEFAULTS = {
    "new_chat_prompt": "[Start a new Chat]",
    "new_example_chat_prompt": "[Example Chat]",
    "personality_format": "{{personality}}",
    "scenario_format": "{{scenario}}",
}


def preset_texts(con: sqlite3.Connection, import_id: int | None) -> dict[str, str]:
    """The new-chat lines and field formats of one import."""
    kept = _kept_preset(con, import_id) if import_id is not None else {}
    return {k: kept[k] if isinstance(kept.get(k), str) else v
            for k, v in PRESET_TEXT_DEFAULTS.items()}


def cleanup_scripts(con: sqlite3.Connection) -> list[dict]:
    """Every regex script that runs on the outgoing history this turn: from
    imports with history cleanup on and a module switched on, in import
    order."""
    ids = [r["import_id"] for r in con.execute(
        "SELECT DISTINCT import_id FROM prompt_modules WHERE enabled = 1 "
        "AND kind = ? AND import_id IS NOT NULL ORDER BY import_id",
        (KIND_MODULE,)).fetchall()]
    out: list[dict] = []
    for imp in ids:
        if history_cleanup(con, imp):
            out += regex_scripts(con, imp)
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
