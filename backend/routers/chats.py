"""routers/chats.py -- Chat session management endpoints (Phase 4).

Routes:
    GET    /chats                       - list all chats
    POST   /chats                       - create a chat session
    GET    /chats/{chat_id}             - get a single chat by ID
    PATCH  /chats/{chat_id}             - rename a chat (title only)
    GET    /chats/{chat_id}/messages    - list messages for a chat

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
from attachments_service import (
    load_for_messages,
    delete_for_messages,
    to_api as attachment_to_api,
)

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
       (SELECT COUNT(*) FROM messages m
        WHERE m.chat_id = c.id AND m.active = 1) AS message_count
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


def _msg_to_dict(
    row,
    attachments: list[dict] | None = None,
    variant_index: int | None = None,
    variant_count: int | None = None,
) -> dict:
    """Convert a message row to an API-safe dict (variant-aware)."""
    keys = row.keys() if hasattr(row, "keys") else []
    d = {
        "id":         row["id"],
        "chat_id":    row["chat_id"],
        "role":       row["role"],
        "content":    row["content"],
        "created_at": row["created_at"],
        "attachments": [attachment_to_api(a) for a in (attachments or [])],
        "variant_group": row["variant_group"] if "variant_group" in keys else None,
        "active": bool(row["active"]) if "active" in keys else True,
    }
    if variant_index is not None:
        d["variant_index"] = variant_index
    if variant_count is not None:
        d["variant_count"] = variant_count
    return d


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
# PATCH /chats/{chat_id}
# ---------------------------------------------------------------------------

_MAX_TITLE_LEN = 200


class ChatPatch(BaseModel):
    title: str


@router.patch("/{chat_id}")
async def rename_chat(chat_id: int, body: ChatPatch) -> dict:
    """Rename a chat. Title is trimmed; empty titles are rejected.

    Plain HTTPExceptions (not pydantic validators) so the client receives the
    stable string codes title_required / title_too_long instead of a 422
    validation array.
    """
    title = body.title.strip()
    if not title:
        raise HTTPException(400, "title_required")
    if len(title) > _MAX_TITLE_LEN:
        raise HTTPException(400, "title_too_long")

    with get_db() as con:
        existing = con.execute(
            "SELECT 1 FROM chats WHERE id = ?", (chat_id,)
        ).fetchone()
        if existing is None:
            raise HTTPException(404, "chat_not_found")

        con.execute(
            "UPDATE chats SET title = ?, updated_at = datetime('now') WHERE id = ?",
            (title, chat_id),
        )
        row = con.execute(
            _CHAT_SELECT + "WHERE c.id = ?", (chat_id,)
        ).fetchone()

    logger.info("Chat renamed: id=%d", chat_id)
    return _chat_to_dict(row)


# ---------------------------------------------------------------------------
# GET /chats/{chat_id}/messages
# ---------------------------------------------------------------------------

@router.get("/{chat_id}/messages")
async def list_messages(chat_id: int) -> list[dict]:
    """Return ALL messages for a chat (active and inactive variants), id ASC.

    Inactive variant rows ride along so the client can flip between them
    without a fetch inside the carousel animation; each row carries its
    variant_index/variant_count within its group.
    """
    with get_db() as con:
        # Verify chat exists first
        chat_exists = con.execute(
            "SELECT 1 FROM chats WHERE id = ?", (chat_id,)
        ).fetchone()
        if chat_exists is None:
            raise HTTPException(404, "chat_not_found")

        rows = con.execute(
            "SELECT id, chat_id, role, content, created_at, "
            "variant_group, active "
            "FROM messages WHERE chat_id = ? ORDER BY id ASC",
            (chat_id,),
        ).fetchall()
    att_map = load_for_messages([r["id"] for r in rows])
    group_ids: dict[int, list[int]] = {}
    for r in rows:
        group_ids.setdefault(r["variant_group"] or r["id"], []).append(r["id"])
    out = []
    for r in rows:
        ids = group_ids[r["variant_group"] or r["id"]]
        out.append(_msg_to_dict(
            r, att_map.get(r["id"]),
            variant_index=ids.index(r["id"]),
            variant_count=len(ids),
        ))
    return out


# ---------------------------------------------------------------------------
# DELETE /chats/{chat_id}
# ---------------------------------------------------------------------------

@router.delete("/{chat_id}")
async def delete_chat(chat_id: int) -> dict:
    """Delete a chat and all its messages."""
    with get_db() as con:
        # One write txn from the first read: the id-list must be computed on
        # the same snapshot the DELETE runs on, or a message linked
        # concurrently escapes msg_ids and its surviving attachment row trips
        # the FK on DELETE FROM messages (TOCTOU - see delete_message).
        con.execute("BEGIN IMMEDIATE")
        existing = con.execute(
            "SELECT 1 FROM chats WHERE id = ?", (chat_id,)
        ).fetchone()
        if existing is None:
            raise HTTPException(404, "chat_not_found")

        msg_ids = [r["id"] for r in con.execute(
            "SELECT id FROM messages WHERE chat_id = ?", (chat_id,)
        ).fetchall()]
        # Rows AND orphaned blobs go in this same transaction (E6) - there is
        # no post-commit file phase anymore.
        delete_for_messages(con, msg_ids)
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
        # One write txn (TOCTOU - same rationale as delete_chat).
        con.execute("BEGIN IMMEDIATE")
        existing = con.execute(
            "SELECT 1 FROM chats WHERE id = ?", (chat_id,)
        ).fetchone()
        if existing is None:
            raise HTTPException(404, "chat_not_found")

        msg_ids = [r["id"] for r in con.execute(
            "SELECT id FROM messages WHERE chat_id = ?", (chat_id,)
        ).fetchall()]
        delete_for_messages(con, msg_ids)  # rows + orphan blobs, same txn (E6)
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
        # Single write txn from the first read: the sweep set must be computed
        # against the same snapshot the DELETE runs on (see completions.py's
        # regenerate swap for the TOCTOU rationale).
        con.execute("BEGIN IMMEDIATE")
        chat_row = con.execute(
            "SELECT id FROM chats WHERE id = ?", (chat_id,)
        ).fetchone()
        if chat_row is None:
            raise HTTPException(404, "chat_not_found")

        row = con.execute(
            "SELECT id, variant_group FROM messages WHERE id = ? AND chat_id = ?",
            (message_id, chat_id),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "message_not_found")

        # Deleting any variant deletes its WHOLE group and everything after.
        # The anchor is the group's smallest id; sweeping from the pressed
        # row's id instead would leave earlier inactive siblings behind as
        # invisible orphans that then become the chat's "last" message.
        start_id = row["variant_group"] or row["id"]

        msg_ids = [r["id"] for r in con.execute(
            "SELECT id FROM messages WHERE chat_id = ? AND id >= ?",
            (chat_id, start_id),
        ).fetchall()]
        delete_for_messages(con, msg_ids)  # rows + orphan blobs, same txn (E6)
        deleted = con.execute(
            "DELETE FROM messages WHERE chat_id = ? AND id >= ?",
            (chat_id, start_id),
        ).rowcount
        con.execute(
            "UPDATE chats SET updated_at = datetime('now') WHERE id = ?",
            (chat_id,),
        )

    logger.info("Messages deleted: chat_id=%d from_msg_id=%d count=%d",
                chat_id, message_id, deleted)
    return {"ok": True, "deleted_count": deleted}


# ---------------------------------------------------------------------------
# POST /chats/{chat_id}/messages/{message_id}/activate
# ---------------------------------------------------------------------------

@router.post("/{chat_id}/messages/{message_id}/activate")
async def activate_variant(chat_id: int, message_id: int) -> dict:
    """Make one variant of the chat's LAST assistant group the active row.

    No provider call - a pure view/state switch driving the carousel arrows.
    v1 restricts navigation to the last active group (matching where new
    variants can be generated). chats.updated_at is deliberately untouched:
    flipping a view is not new content and must not reorder the chat list.

    Stable error codes: chat_not_found, message_not_found,
    not_a_variant_target (role != assistant), variant_group_not_last.
    """
    with get_db() as con:
        # Guard + flip in one write txn (TOCTOU - see completions.py). Without
        # this, a racing delete between guard and UPDATE could leave the flip
        # targeting rows that no longer exist (ids.index() would 500).
        con.execute("BEGIN IMMEDIATE")
        chat_row = con.execute(
            "SELECT id FROM chats WHERE id = ?", (chat_id,)
        ).fetchone()
        if chat_row is None:
            raise HTTPException(404, "chat_not_found")

        row = con.execute(
            "SELECT id, chat_id, role, content, created_at, "
            "variant_group, active "
            "FROM messages WHERE id = ? AND chat_id = ?",
            (message_id, chat_id),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "message_not_found")
        if row["role"] != "assistant":
            raise HTTPException(422, "not_a_variant_target")

        anchor = row["variant_group"] or row["id"]

        last_active = con.execute(
            "SELECT id, variant_group FROM messages "
            "WHERE chat_id = ? AND active = 1 ORDER BY id DESC LIMIT 1",
            (chat_id,),
        ).fetchone()
        if (
            last_active is None
            or (last_active["variant_group"] or last_active["id"]) != anchor
        ):
            raise HTTPException(409, "variant_group_not_last")

        prev_row = con.execute(
            "SELECT id FROM messages "
            "WHERE chat_id = ? AND COALESCE(variant_group, id) = ? AND active = 1",
            (chat_id, anchor),
        ).fetchone()
        prev_active_id = prev_row["id"] if prev_row else None

        # Deactivate the whole group first (one-active-per-group unique
        # index), then activate the target. Idempotent by construction.
        con.execute(
            "UPDATE messages SET variant_group = ?, active = 0 "
            "WHERE chat_id = ? AND COALESCE(variant_group, id) = ?",
            (anchor, chat_id, anchor),
        )
        con.execute(
            "UPDATE messages SET active = 1 WHERE id = ?", (message_id,)
        )

        ids = [r["id"] for r in con.execute(
            "SELECT id FROM messages "
            "WHERE chat_id = ? AND COALESCE(variant_group, id) = ? "
            "ORDER BY id ASC",
            (chat_id, anchor),
        ).fetchall()]
        fresh = con.execute(
            "SELECT id, chat_id, role, content, created_at, "
            "variant_group, active FROM messages WHERE id = ?",
            (message_id,),
        ).fetchone()

    logger.info(
        "Variant activated: chat_id=%d group=%d active=%d",
        chat_id, anchor, message_id,
    )
    return {
        "ok": True,
        "chat_id": chat_id,
        "variant_group": anchor,
        "message": _msg_to_dict(
            fresh,
            variant_index=ids.index(message_id),
            variant_count=len(ids),
        ),
        "deactivated_message_id": (
            prev_active_id if prev_active_id != message_id else None
        ),
    }


