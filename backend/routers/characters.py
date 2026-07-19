"""routers/characters.py -- Character management endpoints (Phase 3).

Routes:
    GET    /characters              - list all characters
    POST   /characters              - create a character manually
    POST   /characters/import       - import from JSON character card
    GET    /characters/{id}         - get a single character by ID

Privacy invariants:
    - raw_json is NEVER returned in any API response.
    - raw_json is NEVER logged.
    - Only character id and name are logged.
    - This module does NOT import httpx, requests, urllib.request,
      keyring, openrouter, network_client, or proxy_health.
"""

import json
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from database import get_db
from attachments_service import delete_for_messages

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/characters", tags=["characters"])

MAX_IMPORT_BYTES = 1_048_576  # 1 MiB


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class CharacterCreate(BaseModel):
    name: str
    description: str = ""
    personality: str = ""
    scenario: str = ""
    first_mes: str = ""
    mes_example: str = ""
    system_prompt: str = ""
    post_history_instruction: str = ""
    tags: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def name_must_be_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name must not be empty.")
        return v.strip()

    @field_validator("tags")
    @classmethod
    def tags_must_be_list_of_str(cls, v: list) -> list:
        if not all(isinstance(t, str) for t in v):
            raise ValueError("tags must be a list of strings.")
        return v


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SELECT_COLS = (
    "id, name, description, personality, scenario, first_mes, "
    "mes_example, system_prompt, post_history_instruction, tags, created_at"
)


def _row_to_dict(row) -> dict:
    """Convert a sqlite3.Row to an API-safe dict. raw_json is excluded."""
    return {
        "id":                       row["id"],
        "name":                     row["name"],
        "description":              row["description"],
        "personality":              row["personality"],
        "scenario":                 row["scenario"],
        "first_mes":                row["first_mes"],
        "mes_example":              row["mes_example"],
        "system_prompt":            row["system_prompt"],
        "post_history_instruction": row["post_history_instruction"],
        "tags":                     json.loads(row["tags"]),
        "created_at":               row["created_at"],
    }


def _text(data: dict, key: str) -> str:
    """Extract a text field from import data. Non-string values become ''."""
    v = data.get(key)
    return v if isinstance(v, str) else ""


# ---------------------------------------------------------------------------
# GET /characters
# ---------------------------------------------------------------------------

@router.get("")
async def list_characters() -> list[dict]:
    """Return all characters ordered by id. raw_json is excluded."""
    with get_db() as con:
        rows = con.execute(
            f"SELECT {_SELECT_COLS} FROM characters ORDER BY id ASC"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# POST /characters
# ---------------------------------------------------------------------------

@router.post("", status_code=201)
async def create_character(body: CharacterCreate) -> dict:
    """Create a character from validated fields. raw_json stored as '{}'."""
    with get_db() as con:
        cur = con.execute(
            "INSERT INTO characters (name, description, personality, scenario, "
            "first_mes, mes_example, system_prompt, post_history_instruction, "
            "tags, raw_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (body.name, body.description, body.personality, body.scenario,
             body.first_mes, body.mes_example, body.system_prompt,
             body.post_history_instruction,
             json.dumps(body.tags), "{}"),
        )
        row_id = cur.lastrowid
        row = con.execute(
            f"SELECT {_SELECT_COLS} FROM characters WHERE id = ?", (row_id,)
        ).fetchone()
    logger.info("Character created: id=%d", row["id"])
    return _row_to_dict(row)


# ---------------------------------------------------------------------------
# POST /characters/import  - declared BEFORE /{character_id}
# ---------------------------------------------------------------------------

@router.post("/import", status_code=201)
async def import_character(request: Request) -> dict:
    """Import a character from a raw JSON body (direct or CharCard V2)."""
    raw = await request.body()
    if len(raw) > MAX_IMPORT_BYTES:
        raise HTTPException(400, "character_json_too_large")

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(400, "invalid_character_json")

    if not isinstance(payload, dict):
        raise HTTPException(400, "invalid_character_json")

    # Unwrap Character Card V2 wrapper
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload

    # Name: required, stripped
    name = _text(data, "name").strip()
    if not name:
        raise HTTPException(400, "character_name_required")

    # post_history_instruction: singular wins over plural
    post_history = (
        _text(data, "post_history_instruction")
        or _text(data, "post_history_instructions")
    )

    # Tags: lenient - non-list → [], non-str items dropped
    raw_tags = data.get("tags")
    tags: list[str] = (
        [t for t in raw_tags if isinstance(t, str)]
        if isinstance(raw_tags, list)
        else []
    )

    # raw_json: preserve original body exactly as received
    raw_json_str = raw.decode("utf-8", errors="replace")

    with get_db() as con:
        cur = con.execute(
            "INSERT INTO characters (name, description, personality, scenario, "
            "first_mes, mes_example, system_prompt, post_history_instruction, "
            "tags, raw_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (name, _text(data, "description"), _text(data, "personality"),
             _text(data, "scenario"), _text(data, "first_mes"),
             _text(data, "mes_example"), _text(data, "system_prompt"),
             post_history, json.dumps(tags), raw_json_str),
        )
        row_id = cur.lastrowid
        row = con.execute(
            f"SELECT {_SELECT_COLS} FROM characters WHERE id = ?", (row_id,)
        ).fetchone()
    logger.info("Character imported: id=%d", row["id"])
    return _row_to_dict(row)


# ---------------------------------------------------------------------------
# GET /characters/{character_id}
# ---------------------------------------------------------------------------

@router.get("/{character_id}")
async def get_character(character_id: int) -> dict:
    """Return a single character by ID. raw_json is excluded."""
    with get_db() as con:
        row = con.execute(
            f"SELECT {_SELECT_COLS} FROM characters WHERE id = ?",
            (character_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(404, "character_not_found")
    return _row_to_dict(row)


# ---------------------------------------------------------------------------
# PATCH /characters/{character_id}
# ---------------------------------------------------------------------------

class CharacterPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    personality: str | None = None
    scenario: str | None = None
    first_mes: str | None = None
    mes_example: str | None = None
    system_prompt: str | None = None
    post_history_instruction: str | None = None
    tags: list[str] | None = None

    @field_validator("name")
    @classmethod
    def name_must_be_non_empty(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("name must not be empty.")
        return v.strip() if v is not None else v

    @field_validator("tags")
    @classmethod
    def tags_must_be_list_of_str(cls, v: list | None) -> list | None:
        if v is not None and not all(isinstance(t, str) for t in v):
            raise ValueError("tags must be a list of strings.")
        return v


@router.patch("/{character_id}")
async def patch_character(character_id: int, body: CharacterPatch) -> dict:
    """Partially update a character. Only provided fields are changed."""
    with get_db() as con:
        existing = con.execute(
            "SELECT id FROM characters WHERE id = ?", (character_id,)
        ).fetchone()
        if existing is None:
            raise HTTPException(404, "character_not_found")

        updates: list[str] = []
        params: list = []
        for field_name in ("name", "description", "personality", "scenario",
                           "first_mes", "mes_example", "system_prompt",
                           "post_history_instruction"):
            val = getattr(body, field_name)
            if val is not None:
                updates.append(f"{field_name} = ?")
                params.append(val)

        if body.tags is not None:
            updates.append("tags = ?")
            params.append(json.dumps(body.tags))

        if updates:
            params.append(character_id)
            con.execute(
                f"UPDATE characters SET {', '.join(updates)} WHERE id = ?",
                params,
            )

        row = con.execute(
            f"SELECT {_SELECT_COLS} FROM characters WHERE id = ?",
            (character_id,),
        ).fetchone()
    logger.info("Character updated: id=%d", character_id)
    return _row_to_dict(row)


# ---------------------------------------------------------------------------
# DELETE /characters/{character_id}
# ---------------------------------------------------------------------------

@router.delete("/{character_id}")
async def delete_character(character_id: int) -> dict:
    """Delete a character and cascade-delete all associated chats + messages."""
    with get_db() as con:
        # Write lock up front (parity with delete_chat/clear_chat): without
        # it the msg_ids SELECT runs in autocommit, and a concurrent send
        # could link a new attachment between that SELECT and the DELETE -
        # its row would escape delete_for_messages and the DELETE would then
        # hit a FOREIGN KEY error.
        con.execute("BEGIN IMMEDIATE")
        existing = con.execute(
            "SELECT id FROM characters WHERE id = ?", (character_id,)
        ).fetchone()
        if existing is None:
            raise HTTPException(404, "character_not_found")

        # Cascade: messages → chats → character
        chat_ids = [r["id"] for r in con.execute(
            "SELECT id FROM chats WHERE character_id = ?", (character_id,)
        ).fetchall()]

        if chat_ids:
            placeholders = ",".join("?" * len(chat_ids))
            msg_ids = [r["id"] for r in con.execute(
                f"SELECT id FROM messages WHERE chat_id IN ({placeholders})",
                chat_ids,
            ).fetchall()]
            # Rows + orphaned blobs in this same transaction (E6) - no
            # post-commit file phase anymore.
            delete_for_messages(con, msg_ids)
            con.execute(
                f"DELETE FROM messages WHERE chat_id IN ({placeholders})",
                chat_ids,
            )
            con.execute(
                f"DELETE FROM chats WHERE id IN ({placeholders})",
                chat_ids,
            )

        con.execute("DELETE FROM characters WHERE id = ?", (character_id,))

    logger.info("Character deleted: id=%d (cascaded %d chats)", character_id, len(chat_ids))
    return {"ok": True}
