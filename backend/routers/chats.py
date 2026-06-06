"""routers/chats.py -- Chat session management endpoints (Phase 4).

Routes:
    GET    /chats                       — list all chats
    POST   /chats                       — create a chat session
    GET    /chats/{chat_id}             — get a single chat by ID
    GET    /chats/{chat_id}/messages    — list messages for a chat

Privacy invariants:
    - raw_json is NEVER returned in any chat/message response.
    - Message content is NEVER logged.
    - Character first_mes is NEVER logged.
    - Only chat id, character id, and operation status are logged.
    - This module does NOT import httpx, requests, urllib.request,
      keyring, openrouter, network_client, or proxy_health.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chats", tags=["chats"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class ChatCreate(BaseModel):
    character_id: int
    title: str | None = None
    model_id: str | None = None


# ---------------------------------------------------------------------------
# SQL helpers
# ---------------------------------------------------------------------------

_CHAT_SELECT = """\
SELECT c.id, c.character_id, ch.name AS character_name,
       c.title, c.model_id, c.created_at, c.updated_at,
       (SELECT COUNT(*) FROM messages m WHERE m.chat_id = c.id) AS message_count
FROM chats c
JOIN characters ch ON c.character_id = ch.id
"""


def _chat_to_dict(row) -> dict:
    """Convert a chat row (with JOIN) to an API-safe dict."""
    return {
        "id":             row["id"],
        "character_id":   row["character_id"],
        "character_name": row["character_name"],
        "title":          row["title"],
        "model_id":       row["model_id"],
        "created_at":     row["created_at"],
        "updated_at":     row["updated_at"],
        "message_count":  row["message_count"],
    }


def _msg_to_dict(row) -> dict:
    """Convert a message row to an API-safe dict."""
    return {
        "id":         row["id"],
        "chat_id":    row["chat_id"],
        "role":       row["role"],
        "content":    row["content"],
        "created_at": row["created_at"],
    }


# ---------------------------------------------------------------------------
# GET /chats
# ---------------------------------------------------------------------------

@router.get("")
async def list_chats() -> list[dict]:
    """Return all chats ordered by updated_at DESC, id DESC."""
    with get_db() as con:
        rows = con.execute(
            _CHAT_SELECT + "ORDER BY c.updated_at DESC, c.id DESC"
        ).fetchall()
    return [_chat_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# POST /chats
# ---------------------------------------------------------------------------

@router.post("", status_code=201)
async def create_chat(body: ChatCreate) -> dict:
    """Create a chat session. Optionally inserts character.first_mes."""
    with get_db() as con:
        # 1. Verify character exists
        char_row = con.execute(
            "SELECT id, name, first_mes FROM characters WHERE id = ?",
            (body.character_id,),
        ).fetchone()
        if char_row is None:
            raise HTTPException(404, "character_not_found")

        char_name = char_row["name"]
        first_mes = char_row["first_mes"].strip() if char_row["first_mes"] else ""

        # 2. Normalize title
        title = body.title.strip() if body.title and body.title.strip() else None
        if title is None:
            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
            title = f"{char_name} - {now_str}"

        # 3. Normalize model_id
        model_id = body.model_id.strip() if body.model_id and body.model_id.strip() else None

        # 4. Insert chat
        cur = con.execute(
            "INSERT INTO chats (character_id, title, model_id) VALUES (?,?,?)",
            (body.character_id, title, model_id),
        )
        chat_id = cur.lastrowid

        # 5. Insert first_mes as assistant message if non-empty
        if first_mes:
            con.execute(
                "INSERT INTO messages (chat_id, role, content) "
                "VALUES (?, 'assistant', ?)",
                (chat_id, first_mes),
            )
            con.execute(
                "UPDATE chats SET updated_at = datetime('now') WHERE id = ?",
                (chat_id,),
            )

        # 6. Fetch the full chat row
        row = con.execute(
            _CHAT_SELECT + "WHERE c.id = ?", (chat_id,)
        ).fetchone()

    logger.info("Chat created: id=%d character_id=%d", chat_id, body.character_id)
    return _chat_to_dict(row)


# ---------------------------------------------------------------------------
# GET /chats/{chat_id}
# ---------------------------------------------------------------------------

@router.get("/{chat_id}")
async def get_chat(chat_id: int) -> dict:
    """Return a single chat by ID."""
    with get_db() as con:
        row = con.execute(
            _CHAT_SELECT + "WHERE c.id = ?", (chat_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(404, "chat_not_found")
    return _chat_to_dict(row)


# ---------------------------------------------------------------------------
# GET /chats/{chat_id}/messages
# ---------------------------------------------------------------------------

@router.get("/{chat_id}/messages")
async def list_messages(chat_id: int) -> list[dict]:
    """Return all messages for a chat, ordered by id ASC."""
    with get_db() as con:
        # Verify chat exists first
        chat_exists = con.execute(
            "SELECT 1 FROM chats WHERE id = ?", (chat_id,)
        ).fetchone()
        if chat_exists is None:
            raise HTTPException(404, "chat_not_found")

        rows = con.execute(
            "SELECT id, chat_id, role, content, created_at "
            "FROM messages WHERE chat_id = ? ORDER BY id ASC",
            (chat_id,),
        ).fetchall()
    return [_msg_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# DELETE /chats/{chat_id}
# ---------------------------------------------------------------------------

@router.delete("/{chat_id}")
async def delete_chat(chat_id: int) -> dict:
    """Delete a chat and all its messages."""
    with get_db() as con:
        existing = con.execute(
            "SELECT 1 FROM chats WHERE id = ?", (chat_id,)
        ).fetchone()
        if existing is None:
            raise HTTPException(404, "chat_not_found")

        con.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
        con.execute("DELETE FROM chats WHERE id = ?", (chat_id,))

    logger.info("Chat deleted: id=%d", chat_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# POST /chats/{chat_id}/clear
# ---------------------------------------------------------------------------

@router.post("/{chat_id}/clear")
async def clear_chat(chat_id: int) -> dict:
    """Delete all messages in a chat. The chat itself is preserved."""
    with get_db() as con:
        existing = con.execute(
            "SELECT 1 FROM chats WHERE id = ?", (chat_id,)
        ).fetchone()
        if existing is None:
            raise HTTPException(404, "chat_not_found")

        deleted = con.execute(
            "DELETE FROM messages WHERE chat_id = ?", (chat_id,)
        ).rowcount
        con.execute(
            "UPDATE chats SET updated_at = datetime('now') WHERE id = ?",
            (chat_id,),
        )

    logger.info("Chat cleared: id=%d deleted_count=%d", chat_id, deleted)
    return {"ok": True, "deleted_count": deleted}


# ---------------------------------------------------------------------------
# DELETE /chats/{chat_id}/messages/{message_id}
# ---------------------------------------------------------------------------

@router.delete("/{chat_id}/messages/{message_id}")
async def delete_message(chat_id: int, message_id: int) -> dict:
    """Delete a message and all following messages in the same chat."""
    with get_db() as con:
        chat_row = con.execute(
            "SELECT id FROM chats WHERE id = ?", (chat_id,)
        ).fetchone()
        if chat_row is None:
            raise HTTPException(404, "chat_not_found")

        row = con.execute(
            "SELECT id FROM messages WHERE id = ? AND chat_id = ?",
            (message_id, chat_id),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "message_not_found")

        deleted = con.execute(
            "DELETE FROM messages WHERE chat_id = ? AND id >= ?",
            (chat_id, message_id),
        ).rowcount
        con.execute(
            "UPDATE chats SET updated_at = datetime('now') WHERE id = ?",
            (chat_id,),
        )

    logger.info("Messages deleted: chat_id=%d from_msg_id=%d count=%d",
                chat_id, message_id, deleted)
    return {"ok": True, "deleted_count": deleted}


