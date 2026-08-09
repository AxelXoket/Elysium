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

import anyio.to_thread
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlcipher3 import dbapi2 as sqlite3

from database import get_db, iter_chunks
from attachments_service import (
    load_for_messages,
    delete_for_messages,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chats", tags=["chats"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

# Length caps shared by create + rename so POST can never store a title PATCH
# could not produce. (v1.1 FB10.)
_MAX_TITLE_LEN = 200
_MAX_MODEL_ID_LEN = 300


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


# Shared with completions.py since v1.1 FB6 - one response shape, one guard.
from messages_common import msg_to_dict as _msg_to_dict, last_active_anchor


# ---------------------------------------------------------------------------
# GET /chats
# ---------------------------------------------------------------------------

def _list_chats_sync() -> list[dict]:
    """Worker-thread body - the READ half of audit KÖK 8.

    Every WRITE handler in this file was moved off the event loop because
    holding SQLite's writer lock there freezes every live SSE stream in the
    process. The reads were left behind, and that was the wrong half to leave:
    SQLCipher decrypts page by page on the calling thread, so a read is not
    cheap merely because it takes no lock. `_CHAT_SELECT` also carries a
    correlated COUNT(*) over `messages` per chat, so this grows with the whole
    vault, not with the page being drawn.

    Nothing about the query changes; only the thread it runs on.
    """
    with get_db() as con:
        rows = con.execute(
            _CHAT_SELECT + "ORDER BY c.updated_at DESC, c.id DESC"
        ).fetchall()
    return [_chat_to_dict(r) for r in rows]


@router.get("")
async def list_chats() -> list[dict]:
    """Return all chats ordered by updated_at DESC, id DESC."""
    return await anyio.to_thread.run_sync(_list_chats_sync)


# ---------------------------------------------------------------------------
# POST /chats
# ---------------------------------------------------------------------------

def _create_chat_sync(character_id: int, title_in: str | None,
                      model_id_in: str | None) -> dict:
    """Worker-thread body (audit KÖK 8): own connection, one write txn.

    BEGIN IMMEDIATE takes SQLite's writer lock, and taking it ON THE EVENT LOOP
    means every live SSE stream in the process is frozen for as long as this
    waits - up to the full 15 s busy_timeout if another writer holds it. The
    transaction below is unchanged; only the thread it runs on is. The five
    handlers that did this are the ones the audit named; the pattern they now
    follow is the one _delete_chat_sync has used all along.
    """
    with get_db() as con:
        # One write txn (v1.1 FB3): the character-exists guard must hold until
        # the FK-bearing chats INSERT commits. In autocommit a racing character
        # delete between guard and INSERT trips the FK -> IntegrityError 500.
        # BEGIN IMMEDIATE serializes against delete_character's own txn.
        con.execute("BEGIN IMMEDIATE")
        # 1. Verify character exists
        char_row = con.execute(
            "SELECT id, name, first_mes FROM characters WHERE id = ?",
            (character_id,),
        ).fetchone()
        if char_row is None:
            raise HTTPException(404, "character_not_found")

        char_name = char_row["name"]
        first_mes = char_row["first_mes"].strip() if char_row["first_mes"] else ""

        # 2. Normalize + cap title (parity with rename's title_too_long)
        title = title_in.strip() if title_in and title_in.strip() else None
        if title is not None and len(title) > _MAX_TITLE_LEN:
            raise HTTPException(400, "title_too_long")
        if title is None:
            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
            title = f"{char_name} - {now_str}"

        # 3. Normalize + cap model_id
        model_id = model_id_in.strip() if model_id_in and model_id_in.strip() else None
        if model_id is not None and len(model_id) > _MAX_MODEL_ID_LEN:
            raise HTTPException(400, "model_id_too_long")

        # 4. Insert chat
        try:
            cur = con.execute(
                "INSERT INTO chats (character_id, title, model_id) VALUES (?,?,?)",
                (character_id, title, model_id),
            )
        except sqlite3.IntegrityError:
            # Belt over the txn guard: the FK says the character vanished -
            # report a 404, never a 500.
            raise HTTPException(404, "character_not_found")
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

    logger.info("Chat created: id=%d character_id=%d", chat_id, character_id)
    return _chat_to_dict(row)


@router.post("", status_code=201)
async def create_chat(body: ChatCreate) -> dict:
    """Create a chat session. Optionally inserts character.first_mes."""
    return await anyio.to_thread.run_sync(
        _create_chat_sync, body.character_id, body.title, body.model_id,
    )


# ---------------------------------------------------------------------------
# GET /chats/{chat_id}
# ---------------------------------------------------------------------------

def _get_chat_sync(chat_id: int) -> dict:
    """Worker-thread body; see _list_chats_sync for why reads count too."""
    with get_db() as con:
        row = con.execute(
            _CHAT_SELECT + "WHERE c.id = ?", (chat_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(404, "chat_not_found")
    return _chat_to_dict(row)


@router.get("/{chat_id}")
async def get_chat(chat_id: int) -> dict:
    """Return a single chat by ID."""
    return await anyio.to_thread.run_sync(_get_chat_sync, chat_id)


# ---------------------------------------------------------------------------
# PATCH /chats/{chat_id}
# ---------------------------------------------------------------------------

class ChatPatch(BaseModel):
    title: str


def _rename_chat_sync(chat_id: int, title: str) -> dict:
    """Worker-thread body (KÖK 8): see _create_chat_sync for why."""
    with get_db() as con:
        # Guard + UPDATE + re-SELECT in one write txn (v1.1 FB3): the bare
        # SELECT ran in autocommit, so a racing delete made the re-SELECT
        # return None and _chat_to_dict(None) raise a TypeError 500 instead of
        # a clean 404.
        con.execute("BEGIN IMMEDIATE")
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
        if row is None:
            raise HTTPException(404, "chat_not_found")

    logger.info("Chat renamed: id=%d", chat_id)
    return _chat_to_dict(row)


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

    return await anyio.to_thread.run_sync(_rename_chat_sync, chat_id, title)


# ---------------------------------------------------------------------------
# GET /chats/{chat_id}/messages
# ---------------------------------------------------------------------------

def _list_messages_sync(chat_id: int) -> list[dict]:
    """Worker-thread body; see _list_chats_sync for why reads count too.

    This is the heaviest read in the app and the one that mattered most: the
    client refetches it unconditionally at the end of every exchange, so it
    used to decrypt an entire transcript on the event loop at the exact moment
    the streaming generator for that same chat was trying to ship its next
    sentence of audio. Two connections' worth of work (the rows here, the
    attachments in `load_for_messages`) now happen off the loop together.
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
        # The character's greeting is card prose, not model output, so it is
        # exempt from tag stripping (see voice_tags.strip_for_display). It is
        # identifiable without a schema column: a completion always writes the
        # user row BEFORE the assistant row, and regenerate refuses a reply with
        # no preceding user message, so the chat's oldest row can only be an
        # assistant row if first_mes seeded it. Asked as MIN(id) rather than
        # taken as rows[0] so that adding pagination here cannot silently start
        # stripping the greeting again. On a chat that opens with a user row the
        # id matches that row instead, which is harmless - strip_for_display
        # returns user text untouched either way.
        oldest_id = con.execute(
            "SELECT MIN(id) FROM messages WHERE chat_id = ?", (chat_id,)
        ).fetchone()[0]
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
            card_authored=(r["id"] == oldest_id),
        ))
    return out


@router.get("/{chat_id}/messages")
async def list_messages(chat_id: int) -> list[dict]:
    """Return ALL messages for a chat (active and inactive variants), id ASC.

    Inactive variant rows ride along so the client can flip between them
    without a fetch inside the carousel animation; each row carries its
    variant_index/variant_count within its group.
    """
    return await anyio.to_thread.run_sync(_list_messages_sync, chat_id)


# ---------------------------------------------------------------------------
# DELETE /chats/{chat_id}
# ---------------------------------------------------------------------------

def _delete_chat_sync(chat_id: int) -> None:
    """Worker-thread body (v1.1 FB2/I7): own connection, whole txn in this
    thread. An image-heavy cascade holds the writer for a while - on the event
    loop it would stall every live SSE stream. Refactor only, no behavior
    change. Raises HTTPException (propagates cleanly through run_sync)."""
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


@router.delete("/{chat_id}")
async def delete_chat(chat_id: int) -> dict:
    """Delete a chat and all its messages."""
    await anyio.to_thread.run_sync(_delete_chat_sync, chat_id)
    logger.info("Chat deleted: id=%d", chat_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# POST /chats/{chat_id}/clear
# ---------------------------------------------------------------------------

def _clear_chat_sync(chat_id: int) -> int:
    """Worker-thread body (v1.1 FB2/I7): own connection; see _delete_chat_sync."""
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
    return deleted


@router.post("/{chat_id}/clear")
async def clear_chat(chat_id: int) -> dict:
    """Delete all messages in a chat. The chat itself is preserved."""
    deleted = await anyio.to_thread.run_sync(_clear_chat_sync, chat_id)
    logger.info("Chat cleared: id=%d deleted_count=%d", chat_id, deleted)
    return {"ok": True, "deleted_count": deleted}


# ---------------------------------------------------------------------------
# DELETE /chats/{chat_id}/messages/{message_id}
# ---------------------------------------------------------------------------

def _delete_message_sync(chat_id: int, message_id: int) -> int:
    """Worker-thread body (v1.1 FB2/I7): own connection; see _delete_chat_sync."""
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
    return deleted


@router.delete("/{chat_id}/messages/{message_id}")
async def delete_message(chat_id: int, message_id: int) -> dict:
    """Delete a message and all following messages in the same chat."""
    deleted = await anyio.to_thread.run_sync(
        _delete_message_sync, chat_id, message_id,
    )
    logger.info("Messages deleted: chat_id=%d from_msg_id=%d count=%d",
                chat_id, message_id, deleted)
    return {"ok": True, "deleted_count": deleted}


# ---------------------------------------------------------------------------
# POST /chats/{chat_id}/messages/{message_id}/activate
# ---------------------------------------------------------------------------

def _activate_variant_sync(chat_id: int, message_id: int) -> dict:
    """Worker-thread body (KÖK 8): see _create_chat_sync for why."""
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

        if last_active_anchor(con, chat_id) != anchor:
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

    # The activated row's OWN attachments. Not decoration: the client merges
    # this dict over its cached message and deliberately does not refetch
    # afterwards (a refetch there would race a fast second arrow press), so an
    # empty array here would overwrite the cache and the picture would vanish -
    # and stay vanished, because nothing invalidates. A variant carrying a
    # generated image is exactly the case that makes this reachable.
    atts = load_for_messages([message_id]).get(message_id, [])

    logger.info(
        "Variant activated: chat_id=%d group=%d active=%d",
        chat_id, anchor, message_id,
    )
    return {
        "ok": True,
        "chat_id": chat_id,
        "variant_group": anchor,
        "message": _msg_to_dict(
            fresh, atts,
            variant_index=ids.index(message_id),
            variant_count=len(ids),
        ),
        "deactivated_message_id": (
            prev_active_id if prev_active_id != message_id else None
        ),
    }


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
    return await anyio.to_thread.run_sync(
        _activate_variant_sync, chat_id, message_id,
    )


