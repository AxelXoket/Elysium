"""routers/personas.py -- Persona management endpoints (Part C).

Routes:
    GET    /personas             - list all personas
    POST   /personas             - create a persona
    PATCH  /personas/{id}        - edit a persona
    DELETE /personas/{id}        - delete a persona
    POST   /personas/{id}/select - set as active persona

Privacy invariants:
    - Persona description is NEVER logged.
    - Only persona id and display_name are logged.
    - This module does NOT import httpx, requests, urllib.request,
      keyring, openrouter, network_client, or proxy_health.
"""

import logging

import anyio.to_thread
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

# set_setting and delete_setting are gone from this module on purpose: the two
# handlers that used them now do their settings write inline, inside the same
# transaction as their guard read. See _select_persona_sync.
from database import get_db, get_setting

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/personas", tags=["personas"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class PersonaCreate(BaseModel):
    display_name: str
    description: str = ""

    @field_validator("display_name")
    @classmethod
    def name_must_be_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("display_name must not be empty.")
        return v.strip()


class PersonaPatch(BaseModel):
    display_name: str | None = None
    description: str | None = None

    @field_validator("display_name")
    @classmethod
    def name_must_be_non_empty(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("display_name must not be empty.")
        return v.strip() if v is not None else v


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_to_dict(row, is_active: bool = False) -> dict:
    """Convert a persona row to an API-safe dict."""
    return {
        "id":           row["id"],
        "display_name": row["display_name"],
        "description":  row["description"],
        "is_active":    is_active,
        "created_at":   row["created_at"],
        "updated_at":   row["updated_at"],
    }


_SETTINGS_KEY = "selected_persona_id"


def _read_selected_id() -> int | None:
    """Read selected_persona_id defensively; a corrupted value means None."""
    raw = get_setting(_SETTINGS_KEY)
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning("Ignoring non-integer selected_persona_id setting.")
        return None


# ---------------------------------------------------------------------------
# GET /personas
# ---------------------------------------------------------------------------

def _list_personas_sync() -> list[dict]:
    """Worker-thread body (audit KÖK 8): two reads, two connections.

    A read is not free: each get_db() pays a SQLCipher open, and _read_selected_id
    opens a second one. On the loop that is two chances to queue behind a writer
    and freeze every live SSE stream.
    """
    with get_db() as con:
        rows = con.execute(
            "SELECT id, display_name, description, created_at, updated_at "
            "FROM personas ORDER BY id ASC"
        ).fetchall()
    selected_id = _read_selected_id()
    return [_row_to_dict(r, is_active=(r["id"] == selected_id)) for r in rows]


@router.get("")
async def list_personas() -> list[dict]:
    """Return all personas ordered by id, with is_active derived from settings."""
    return await anyio.to_thread.run_sync(_list_personas_sync)


# ---------------------------------------------------------------------------
# POST /personas
# ---------------------------------------------------------------------------

def _create_persona_sync(body: "PersonaCreate") -> dict:
    """Worker-thread body (audit KÖK 8): INSERT plus the re-SELECT of its own
    new row. No guard of prior state, so no BEGIN IMMEDIATE is needed here -
    unlike delete and select below, which read before they write."""
    with get_db() as con:
        cur = con.execute(
            "INSERT INTO personas (display_name, description) VALUES (?, ?)",
            (body.display_name, body.description),
        )
        row_id = cur.lastrowid
        row = con.execute(
            "SELECT id, display_name, description, created_at, updated_at "
            "FROM personas WHERE id = ?",
            (row_id,),
        ).fetchone()
    logger.info("Persona created: id=%d", row["id"])
    return _row_to_dict(row)


@router.post("", status_code=201)
async def create_persona(body: PersonaCreate) -> dict:
    """Create a persona. Returns the created persona."""
    return await anyio.to_thread.run_sync(_create_persona_sync, body)


# ---------------------------------------------------------------------------
# PATCH /personas/{persona_id}
# ---------------------------------------------------------------------------

def _patch_persona_sync(persona_id: int, body: "PersonaPatch") -> dict:
    """Worker-thread body (audit KÖK 8): own connection, one write txn.

    BEGIN IMMEDIATE takes SQLite's writer lock; taking it on the event loop
    freezes every live SSE stream in the process until it is granted - up to
    the full busy_timeout when another writer holds it.
    """
    with get_db() as con:
        # Guard + UPDATE + re-SELECT in one write txn (v1.1 FB3): the bare
        # SELECT ran in autocommit, so a racing delete made the re-SELECT
        # None and _row_to_dict(None) raise a TypeError 500 instead of a 404.
        con.execute("BEGIN IMMEDIATE")
        existing = con.execute(
            "SELECT id FROM personas WHERE id = ?", (persona_id,)
        ).fetchone()
        if existing is None:
            raise HTTPException(404, "persona_not_found")

        updates: list[str] = []
        params: list = []
        if body.display_name is not None:
            updates.append("display_name = ?")
            params.append(body.display_name)
        if body.description is not None:
            updates.append("description = ?")
            params.append(body.description)

        if updates:
            updates.append("updated_at = datetime('now')")
            params.append(persona_id)
            con.execute(
                f"UPDATE personas SET {', '.join(updates)} WHERE id = ?",
                params,
            )

        row = con.execute(
            "SELECT id, display_name, description, created_at, updated_at "
            "FROM personas WHERE id = ?",
            (persona_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "persona_not_found")
    selected_id = _read_selected_id()
    logger.info("Persona updated: id=%d", persona_id)
    return _row_to_dict(row, is_active=(row["id"] == selected_id))


@router.patch("/{persona_id}")
async def patch_persona(persona_id: int, body: PersonaPatch) -> dict:
    """Partially update a persona. Only provided fields are changed."""
    return await anyio.to_thread.run_sync(_patch_persona_sync, persona_id, body)


# ---------------------------------------------------------------------------
# DELETE /personas/{persona_id}
# ---------------------------------------------------------------------------

def _delete_persona_sync(persona_id: int) -> dict:
    """Worker-thread body (audit KÖK 8), and the guard joins the write txn.

    The guard SELECT ran in autocommit: the writer lock was only taken by the
    DELETE that followed it. Two concurrent deletes of the same id could both
    pass the guard, and the loser's DELETE matched nothing - so it reported
    {"ok": true} for a persona it did not delete, where every sibling handler
    answers 404. BEGIN IMMEDIATE makes the guard mean what it says.
    """
    with get_db() as con:
        con.execute("BEGIN IMMEDIATE")
        existing = con.execute(
            "SELECT id FROM personas WHERE id = ?", (persona_id,)
        ).fetchone()
        if existing is None:
            raise HTTPException(404, "persona_not_found")

        con.execute("DELETE FROM personas WHERE id = ?", (persona_id,))

        # Clear selection if this was the active persona (same connection)
        row = con.execute(
            "SELECT value FROM settings WHERE key = ?", (_SETTINGS_KEY,)
        ).fetchone()
        if row and row["value"] == str(persona_id):
            con.execute(
                "DELETE FROM settings WHERE key = ?", (_SETTINGS_KEY,)
            )

    logger.info("Persona deleted: id=%d", persona_id)
    return {"ok": True}


@router.delete("/{persona_id}")
async def delete_persona(persona_id: int) -> dict:
    """Delete a persona. Clears selected_persona_id if it was selected."""
    return await anyio.to_thread.run_sync(_delete_persona_sync, persona_id)


# ---------------------------------------------------------------------------
# POST /personas/{persona_id}/select
# ---------------------------------------------------------------------------

def _select_persona_sync(persona_id: int) -> dict:
    """Worker-thread body (audit KÖK 8), and it closes a real dangling
    reference while it is here.

    The guard ran in its OWN connection, which committed and closed, and then
    set_setting opened a SECOND connection to write. A delete_persona landing
    in that gap deleted the very persona the guard had just approved, and the
    write still went ahead: selected_persona_id was left naming a row that no
    longer exists. Nothing reported an error - the panel simply showed no
    active persona, and anything trusting the setting to be valid was wrong.

    The write is inlined here rather than delegated to set_setting for exactly
    that reason: set_setting always opens its own connection, so calling it
    would preserve the second transaction and therefore the bug. Same SQL, same
    upsert semantics, joined to this transaction. Same shape as the fix applied
    to set_proxy_required in routers/settings.py.
    """
    with get_db() as con:
        con.execute("BEGIN IMMEDIATE")
        existing = con.execute(
            "SELECT id FROM personas WHERE id = ?", (persona_id,)
        ).fetchone()
        if existing is None:
            raise HTTPException(404, "persona_not_found")

        con.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_SETTINGS_KEY, str(persona_id)),
        )

    logger.info("Persona selected: id=%d", persona_id)
    return {"ok": True, "selected_persona_id": persona_id}


@router.post("/{persona_id}/select")
async def select_persona(persona_id: int) -> dict:
    """Set the active persona for completions. Persisted in settings.

    Clears previous selection. Returns selected_persona_id.
    Error: 404 persona_not_found if persona does not exist.
    """
    return await anyio.to_thread.run_sync(_select_persona_sync, persona_id)
