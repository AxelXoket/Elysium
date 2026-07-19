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

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from database import get_db, get_setting, set_setting, delete_setting

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

@router.get("")
async def list_personas() -> list[dict]:
    """Return all personas ordered by id, with is_active derived from settings."""
    with get_db() as con:
        rows = con.execute(
            "SELECT id, display_name, description, created_at, updated_at "
            "FROM personas ORDER BY id ASC"
        ).fetchall()
    selected_id = _read_selected_id()
    return [_row_to_dict(r, is_active=(r["id"] == selected_id)) for r in rows]


# ---------------------------------------------------------------------------
# POST /personas
# ---------------------------------------------------------------------------

@router.post("", status_code=201)
async def create_persona(body: PersonaCreate) -> dict:
    """Create a persona. Returns the created persona."""
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


# ---------------------------------------------------------------------------
# PATCH /personas/{persona_id}
# ---------------------------------------------------------------------------

@router.patch("/{persona_id}")
async def patch_persona(persona_id: int, body: PersonaPatch) -> dict:
    """Partially update a persona. Only provided fields are changed."""
    with get_db() as con:
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
    selected_id = _read_selected_id()
    logger.info("Persona updated: id=%d", persona_id)
    return _row_to_dict(row, is_active=(row["id"] == selected_id))


# ---------------------------------------------------------------------------
# DELETE /personas/{persona_id}
# ---------------------------------------------------------------------------

@router.delete("/{persona_id}")
async def delete_persona(persona_id: int) -> dict:
    """Delete a persona. Clears selected_persona_id if it was selected."""
    with get_db() as con:
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


# ---------------------------------------------------------------------------
# POST /personas/{persona_id}/select
# ---------------------------------------------------------------------------

@router.post("/{persona_id}/select")
async def select_persona(persona_id: int) -> dict:
    """Set the active persona for completions. Persisted in settings.

    Clears previous selection. Returns selected_persona_id.
    Error: 404 persona_not_found if persona does not exist.
    """
    with get_db() as con:
        existing = con.execute(
            "SELECT id FROM personas WHERE id = ?", (persona_id,)
        ).fetchone()
        if existing is None:
            raise HTTPException(404, "persona_not_found")

    set_setting(_SETTINGS_KEY, str(persona_id))
    logger.info("Persona selected: id=%d", persona_id)
    return {"ok": True, "selected_persona_id": persona_id}
