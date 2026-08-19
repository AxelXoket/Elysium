"""notebook.py - the per-chat notebook, and the boundaries that outrank it.

Two things live here and they are deliberately not the same thing:

  * NOTES belong to one chat. They are what the story has established, they
    grow, they can be superseded, and they are subject to a token ceiling.
  * BOUNDARIES belong to the person. A `global` one applies everywhere; a
    `chat` one applies to that conversation alone, and a chat can be told to
    ignore the global set entirely. They never expire, never merge, and are
    never trimmed away to make room - which is why they are a separate table
    with a separate lifetime rather than a `kind` inside the notebook.

Two rules in here are enforced by the database rather than by this module, and
that is on purpose: a rule this file checks is a rule the next writer can skip.
An INFERRED boundary can never be `hard`, and a row's scope must agree with
whether it names a chat.

The third rule is enforced here because SQL cannot express it: PROVENANCE IS
WRITTEN ONCE. `update_entry` does not accept it and never will. If accepting a
model's suggestion could relabel it as the user's own, then `provenance='model'`
would have no live rows, the guard that keeps model text out of the system
block would pass forever by describing an empty set, and the rule would be
formally intact while doing nothing.
"""
from __future__ import annotations

from typing import Any

from database import get_db

#: Written by the caller who accepts a suggestion, never by the model itself.
STATUS_PROPOSED = "proposed"
STATUS_ACCEPTED = "accepted"

#: Set at INSERT and immutable thereafter. See the module docstring.
PROV_USER = "user"
PROV_MODEL = "model"

KINDS = ("fact", "event", "relationship", "open_thread",
         "entity", "state", "knowledge", "preference")
DURABILITIES = ("scene", "session", "permanent")
SEVERITIES = ("hard", "veiled", "soft")

#: One entry's ceiling. Long enough for a sentence with names in it, short
#: enough that no single note can crowd out the rest of the notebook - and
#: short enough to blunt anything pasted in from elsewhere.
ENTRY_MAX_CHARS = 240


#: Every code a NotebookError can carry. The routes forward `exc.code`, which
#: the catalogue census cannot resolve on its own - this is what it reads
#: instead, so a refusal added without a catalogue entry fails the gate rather
#: than reaching a user as a bare code with no sentence behind it.
ALL_CODES: frozenset[str] = frozenset({
    "notebook_entry_empty",
    "notebook_entry_too_long",
    "notebook_entry_invalid",
    "notebook_field_not_editable",
    "notebook_entry_not_found",
    "boundary_empty",
    "boundary_invalid",
    "chat_not_found",
})


class NotebookError(ValueError):
    """Refusals a route turns into an HTTP answer."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _flat(text: str) -> str:
    """Collapse line breaks so a stored note cannot forge a prompt section.

    The notebook is assembled into a labelled block. A note containing a
    newline followed by `[Character:` would close its own section and open
    another one - which is how a note stops being data and starts being
    instructions. Wisteria carried exactly this defence and it was its only
    one; the newline is the whole mechanism, so it is removed rather than
    escaped.
    """
    return " / ".join(part.strip() for part in text.splitlines() if part.strip())


def _clean_entry_text(raw: str) -> str:
    text = _flat(raw)
    if not text:
        raise NotebookError("notebook_entry_empty")
    if len(text) > ENTRY_MAX_CHARS:
        raise NotebookError("notebook_entry_too_long")
    return text


def _row(r) -> dict[str, Any]:
    return dict(r)


# ---------------------------------------------------------------------------
# notes
# ---------------------------------------------------------------------------

def list_entries(chat_id: int, *, include_retired: bool = True) -> list[dict]:
    """Everything this chat holds, retired rows included by default.

    Retired is not deleted and the panel has to show it: the owner's rule is
    that a note never disappears, and a superseded fact is the one case where
    the app decided something on their behalf. It is excluded from the prompt,
    not from the screen.
    """
    where = "chat_id = ?"
    if not include_retired:
        where += " AND retired_at IS NULL"
    with get_db() as con:
        rows = con.execute(
            f"SELECT * FROM notebook_entries WHERE {where} "
            "ORDER BY position ASC, id ASC", (chat_id,)).fetchall()
    return [_row(r) for r in rows]


def create_entry(chat_id: int, text: str, *, kind: str = "fact",
                 durability: str = "permanent", importance: int = 2,
                 pinned: bool = False, evidence: str | None = None,
                 status: str = STATUS_ACCEPTED, provenance: str = PROV_USER,
                 source_message_id: int | None = None) -> dict:
    """Append a note. `provenance` is set HERE and nowhere else, ever."""
    text = _clean_entry_text(text)
    if kind not in KINDS or durability not in DURABILITIES:
        raise NotebookError("notebook_entry_invalid")
    if importance not in (1, 2, 3):
        raise NotebookError("notebook_entry_invalid")
    with get_db() as con:
        # BEGIN IMMEDIATE: the position is read and then written, and two
        # writers landing between those two moments would collide on the
        # unique index rather than queue.
        con.execute("BEGIN IMMEDIATE")
        nxt = con.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM notebook_entries "
            "WHERE chat_id = ?", (chat_id,)).fetchone()[0]
        cur = con.execute(
            "INSERT INTO notebook_entries "
            "(chat_id, position, kind, text, evidence, durability, importance,"
            " pinned, status, provenance, source_message_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (chat_id, nxt, kind, text, evidence, durability, importance,
             1 if pinned else 0, status, provenance, source_message_id))
        new_id = cur.lastrowid
        row = con.execute(
            "SELECT * FROM notebook_entries WHERE id = ?", (new_id,)).fetchone()
    return _row(row)


def update_entry(entry_id: int, **fields) -> dict:
    """Edit a note's content or its flags.

    `provenance`, `chat_id` and `source_message_id` are NOT accepted, and the
    omission is the feature: editing a model's suggestion must not launder it
    into the user's own. See the module docstring.
    """
    allowed = {"text", "kind", "durability", "importance", "pinned", "status"}
    unknown = set(fields) - allowed
    if unknown:
        # Loud rather than ignored: silently dropping `provenance` from an
        # update would look like it had been applied.
        raise NotebookError("notebook_field_not_editable")
    if "text" in fields:
        fields["text"] = _clean_entry_text(fields["text"])
    if "kind" in fields and fields["kind"] not in KINDS:
        raise NotebookError("notebook_entry_invalid")
    if "durability" in fields and fields["durability"] not in DURABILITIES:
        raise NotebookError("notebook_entry_invalid")
    if "importance" in fields and fields["importance"] not in (1, 2, 3):
        raise NotebookError("notebook_entry_invalid")
    if "pinned" in fields:
        fields["pinned"] = 1 if fields["pinned"] else 0

    sets = ", ".join(f"{k} = ?" for k in fields)
    with get_db() as con:
        con.execute(
            f"UPDATE notebook_entries SET {sets}, updated_at = datetime('now') "
            "WHERE id = ?", (*fields.values(), entry_id))
        row = con.execute(
            "SELECT * FROM notebook_entries WHERE id = ?", (entry_id,)).fetchone()
    if row is None:
        raise NotebookError("notebook_entry_not_found")
    return _row(row)


def delete_entry(entry_id: int) -> bool:
    """Remove a note the USER asked to remove.

    A hard delete, unlike supersession. The distinction matters: retirement is
    the app deciding a fact stopped being true, so the row stays visible and
    auditable. This is the person saying they never wanted it - and leaving
    behind a row they asked to be gone would be the app overruling them.
    """
    with get_db() as con:
        con.execute("BEGIN IMMEDIATE")
        # Anything that pointed at it stops pointing, so no row is left naming
        # a target that does not exist.
        con.execute("UPDATE notebook_entries SET superseded_by = NULL "
                    "WHERE superseded_by = ?", (entry_id,))
        cur = con.execute("DELETE FROM notebook_entries WHERE id = ?", (entry_id,))
    return cur.rowcount > 0


def retire_entry(entry_id: int, superseded_by: int | None = None) -> bool:
    """Take a note out of the prompt while leaving it on the screen.

    Called when a newer fact replaces an older one. The replacement decides
    this in code from the order the rows arrived - never by asking a model
    which of two statements is current, because that is a question whose
    answer nothing here could check.
    """
    with get_db() as con:
        cur = con.execute(
            "UPDATE notebook_entries "
            "SET retired_at = datetime('now'), superseded_by = ?, "
            "    updated_at = datetime('now') "
            "WHERE id = ? AND retired_at IS NULL", (superseded_by, entry_id))
    return cur.rowcount > 0


def reorder(chat_id: int, ordered_ids: list[int]) -> None:
    """Set the order the user dragged the list into.

    Two passes through a negative range first. `position` is uniquely indexed
    per chat, so writing the final numbers directly would collide with the rows
    that still hold them - the index fires mid-statement, and the list is left
    half-renumbered.
    """
    with get_db() as con:
        con.execute("BEGIN IMMEDIATE")
        for offset, entry_id in enumerate(ordered_ids):
            con.execute(
                "UPDATE notebook_entries SET position = ? "
                "WHERE id = ? AND chat_id = ?",
                (-1 - offset, entry_id, chat_id))
        for offset, entry_id in enumerate(ordered_ids):
            con.execute(
                "UPDATE notebook_entries SET position = ?, "
                "updated_at = datetime('now') WHERE id = ? AND chat_id = ?",
                (offset, entry_id, chat_id))


# ---------------------------------------------------------------------------
# boundaries
# ---------------------------------------------------------------------------

def list_boundaries(chat_id: int | None = None) -> list[dict]:
    """The limits in force for a chat, or the global set when chat_id is None.

    A chat with `use_global_boundaries = 0` sees ONLY its own. That switch is
    the whole reason both scopes exist in one table: somebody running a story
    that its global limits would not fit needs to set them aside for that
    story without deleting what they wrote once.
    """
    with get_db() as con:
        if chat_id is None:
            rows = con.execute(
                "SELECT * FROM boundaries WHERE scope = 'global' "
                "ORDER BY id ASC").fetchall()
            return [_row(r) for r in rows]
        use_global = con.execute(
            "SELECT use_global_boundaries FROM chats WHERE id = ?",
            (chat_id,)).fetchone()
        if use_global is None:
            raise NotebookError("chat_not_found")
        if use_global[0]:
            rows = con.execute(
                "SELECT * FROM boundaries WHERE scope = 'global' "
                "   OR (scope = 'chat' AND chat_id = ?) ORDER BY id ASC",
                (chat_id,)).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM boundaries WHERE scope = 'chat' AND chat_id = ? "
                "ORDER BY id ASC", (chat_id,)).fetchall()
    return [_row(r) for r in rows]


def create_boundary(label: str, phrasing: str, severity: str, *,
                    chat_id: int | None = None, polarity: str = "avoid",
                    on_violation: str = "pause", source: str = "explicit",
                    rating_ceiling: str | None = None) -> dict:
    """Write a limit. `chat_id=None` makes it global.

    The database refuses an inferred hard limit; this function does not repeat
    that check, because repeating it here would suggest the check lives here.
    """
    label, phrasing = _flat(label), _flat(phrasing)
    if not label or not phrasing:
        raise NotebookError("boundary_empty")
    if severity not in SEVERITIES:
        raise NotebookError("boundary_invalid")
    scope = "chat" if chat_id is not None else "global"
    with get_db() as con:
        cur = con.execute(
            "INSERT INTO boundaries (scope, chat_id, label, phrasing, severity,"
            " polarity, on_violation, source, rating_ceiling) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (scope, chat_id, label, phrasing, severity, polarity,
             on_violation, source, rating_ceiling))
        row = con.execute("SELECT * FROM boundaries WHERE id = ?",
                          (cur.lastrowid,)).fetchone()
    return _row(row)


def delete_boundary(boundary_id: int) -> bool:
    with get_db() as con:
        cur = con.execute("DELETE FROM boundaries WHERE id = ?", (boundary_id,))
    return cur.rowcount > 0


def set_use_global_boundaries(chat_id: int, use: bool) -> None:
    with get_db() as con:
        con.execute("UPDATE chats SET use_global_boundaries = ? WHERE id = ?",
                    (1 if use else 0, chat_id))


# ---------------------------------------------------------------------------
# lifetime - called from the chat/character delete paths, inside THEIR
# transaction. Nothing here opens its own connection.
# ---------------------------------------------------------------------------

def delete_for_chats(con, chat_ids) -> None:
    """Remove everything these chats own, on the caller's connection.

    Takes the connection rather than opening one, because it has to land in
    the same transaction as the chat rows - `chat_id` is a foreign key with no
    cascade, so leaving these behind does not orphan them, it makes the chat
    UNDELETABLE. The delete fails, the route 500s, and the chat cannot be
    removed at all.

    Global boundaries are deliberately untouched: they belong to the person,
    not to any conversation, and losing them because a chat was deleted would
    be the app forgetting a limit it was told to keep.
    """
    ids = list(chat_ids)
    if not ids:
        return
    marks = ",".join("?" * len(ids))
    con.execute(f"DELETE FROM notebook_entries WHERE chat_id IN ({marks})", ids)
    con.execute(
        f"DELETE FROM notebook_extractions WHERE chat_id IN ({marks})", ids)
    con.execute(
        f"DELETE FROM boundaries WHERE scope = 'chat' AND chat_id IN ({marks})",
        ids)


def forget_proposals_from_messages(con, message_ids) -> None:
    """Drop UNACCEPTED suggestions that came from messages being deleted.

    Accepted notes stay. Deleting a message is the user removing a turn, not
    retracting a fact they approved - and a fact they approved may well have
    been stated in several places. A suggestion they never looked at is the
    opposite: its only evidence is going away, so reviewing it later would mean
    judging a quote that can no longer be checked.
    """
    ids = list(message_ids)
    if not ids:
        return
    marks = ",".join("?" * len(ids))
    con.execute(
        f"DELETE FROM notebook_entries WHERE status = '{STATUS_PROPOSED}' "
        f"AND source_message_id IN ({marks})", ids)
