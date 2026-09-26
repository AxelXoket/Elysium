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
import sys
from pathlib import Path

import audio_sweep

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

import anyio.to_thread

import default_names
import notebook_store as notebook
from database import get_db, iter_chunks
from attachments_service import delete_for_messages

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/characters", tags=["characters"])

MAX_IMPORT_BYTES = 1_048_576  # 1 MiB

#: The longest name a character or a persona may be given, in code points
#: (Ek P, B8). The name travels into the prompt in several places and the
#: prompt side cuts it here anyway, so a longer name was only ever a name
#: the model never saw in full. personas.py imports this, so the two doors
#: cannot drift apart; the number itself lives in shared/name_limits.json,
#: which the frontend mirror is tested against.
def _shared_dir() -> Path:
    """shared/ beside the exe in a frozen build, else in the repository. Same
    rule as prompt_sanitizer.shared_path; the spec has to ship shared/."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", ".")) / "shared"
    return Path(__file__).resolve().parents[2] / "shared"


NAME_MAX_CODE_POINTS: int = json.loads(
    (_shared_dir() / "name_limits.json").read_text(encoding="utf-8")
)["name_max_code_points"]


def name_is_too_long(name: str | None) -> bool:
    """Longer than the cap, counted in code points (Python `len`).

    None is not too long. A body that leaves the name out keeps the model's
    default of None (pydantic does not run validators on defaults), and that
    name is filled in by `default_names` at the write.
    """
    return name is not None and len(name) > NAME_MAX_CODE_POINTS


def unpaired_surrogate_in(text: str) -> bool:
    """Whether this string holds a lone UTF-16 surrogate.

    JSON can spell one (`"\\ud800"`), Python holds it as a str, and the first
    thing that tries to write it as UTF-8 raises: the database INSERT and the
    response encoder both did, and both answered 500 (PK-07). I ask the codec
    itself rather than scanning ranges, because the codec is what fails.
    """
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return True
    return False


def refuse_unpaired_surrogate(value):
    """Pydantic hook shared by the create and patch models here and in
    personas.py. Raising ValueError makes it a 422 `validation_failed`, and
    main.py's handler drops the input from that answer, so the broken text is
    never echoed back."""
    texts = value if isinstance(value, list) else [value]
    if any(isinstance(t, str) and unpaired_surrogate_in(t) for t in texts):
        raise ValueError("text holds an unpaired surrogate code point")
    return value


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class CharacterCreate(BaseModel):
    #: Optional, and that is the whole change. It used to be required and a
    #: blank one was a 422 - so the dialog kept its Create button disabled and
    #: a person who only wanted to paste a description first had to invent a
    #: word. The name is filled in by `default_names` at the write instead.
    #:
    #: `str | None` rather than `str`, matching ChatCreate.title: four shapes
    #: of "nothing" reach these routes - "", "   ", null, and the key left
    #: out - and they used to take four different paths. One answer to all
    #: four is the point of the change, so a null is an empty box here too
    #: rather than a type error about a field the caller did fill in.
    name: str | None = None
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
    def name_is_trimmed(cls, v: str | None) -> str:
        """Trim, and flatten null to empty. Whether the result is empty is
        decided at the write, where the other names in the vault are readable
        and a default can be numbered against them."""
        return (v or "").strip()

    @field_validator("tags")
    @classmethod
    def tags_must_be_list_of_str(cls, v: list) -> list:
        if not all(isinstance(t, str) for t in v):
            raise ValueError("tags must be a list of strings.")
        return v

    # mode="before" so the check sees the raw value, ahead of the trim above.
    _no_surrogates = field_validator(
        "name", "description", "personality", "scenario", "first_mes",
        "mes_example", "system_prompt", "post_history_instruction", "tags",
        mode="before",
    )(refuse_unpaired_surrogate)


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

def _list_characters_sync() -> list[dict]:
    """Worker-thread body (audit KÖK 8). A read still pays a SQLCipher open and
    can still queue behind a writer; on the loop that freezes live streams."""
    with get_db() as con:
        rows = con.execute(
            f"SELECT {_SELECT_COLS} FROM characters ORDER BY id ASC"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


@router.get("")
async def list_characters() -> list[dict]:
    """Return all characters ordered by id. raw_json is excluded."""
    return await anyio.to_thread.run_sync(_list_characters_sync)


# ---------------------------------------------------------------------------
# POST /characters
# ---------------------------------------------------------------------------

def _create_character_sync(body: "CharacterCreate") -> dict:
    """Worker-thread body (audit KÖK 8): INSERT plus the re-SELECT of its own
    new row.

    BEGIN IMMEDIATE, and the reason is new. This used to read nothing before
    it wrote, and the docstring said so; naming an unnamed character turns it
    into read-then-write, because the number in "New Character 3" is derived
    from the names already stored. Without the writer lock two creations
    landing together would each scan a set that does not contain the other and
    both take the same number - the one race a generated name can lose.
    """
    if name_is_too_long(body.name):
        raise HTTPException(400, "name_too_long")
    with get_db() as con:
        con.execute("BEGIN IMMEDIATE")
        name = default_names.name_or_default(
            body.name, default_names.CHARACTER,
            (r["name"] for r in con.execute("SELECT name FROM characters")),
        )
        cur = con.execute(
            "INSERT INTO characters (name, description, personality, scenario, "
            "first_mes, mes_example, system_prompt, post_history_instruction, "
            "tags, raw_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (name, body.description, body.personality, body.scenario,
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


@router.post("", status_code=201)
async def create_character(body: CharacterCreate) -> dict:
    """Create a character from validated fields. raw_json stored as '{}'."""
    return await anyio.to_thread.run_sync(_create_character_sync, body)


# ---------------------------------------------------------------------------
# POST /characters/import  - declared BEFORE /{character_id}
# ---------------------------------------------------------------------------

async def _read_capped_body(request: Request) -> bytes:
    """Read the body, refusing to hold more than MAX_IMPORT_BYTES of it.

    `await request.body()` reads EVERYTHING into RAM and only then compares
    the length, so the 1 MiB cap was enforced after the damage: a declared
    50 MB import was fully buffered before being rejected. The equivalent
    pre-read guard exists in main.py but is scoped to /api/v1/uploads/ only,
    so this route was the one door without it.

    Two gates, because they catch different senders: Content-Length rejects an
    honest client before a byte is read, and the streaming count rejects one
    that lies about the length or declares none at all.
    """
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > MAX_IMPORT_BYTES:
                raise HTTPException(400, "character_json_too_large")
        except ValueError:
            raise HTTPException(400, "invalid_character_json")

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_IMPORT_BYTES:
            raise HTTPException(400, "character_json_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


def _import_character_sync(raw: bytes) -> dict:
    """Worker-thread body (audit KÖK 8): parse, validate and insert.

    Takes the already-read bytes rather than the Request, because the body
    read CANNOT move: _read_capped_body iterates request.stream(), an async
    generator bound to the ASGI connection, and a worker thread has no event
    loop to iterate it on. So the split is: read on the loop, everything after
    it here. The size cap and its two refusals stay on the loop with the read,
    where they have always been.

    The HTTPExceptions raised below travel out of the thread unchanged.
    """
    # ValueError covers JSONDecodeError and UnicodeDecodeError (both are
    # subclasses) and also the integer-digit limit: a 5000-digit number is
    # valid JSON that Python refuses to build. RecursionError is a deeply
    # nested array or object. Both used to escape as a 500 (PK-06); a file
    # that cannot be read as a card is the same answer whatever broke it.
    try:
        payload = json.loads(raw)
    except (ValueError, RecursionError):
        raise HTTPException(400, "invalid_character_json")

    if not isinstance(payload, dict):
        raise HTTPException(400, "invalid_character_json")

    # Unwrap Character Card V2 wrapper
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload

    # Name: optional now, stripped here and defaulted at the write below.
    #
    # A card with no name used to be refused outright with
    # `character_name_required` - the whole import thrown away over one
    # missing field, with a description, a greeting and example dialogue in
    # the same file that were perfectly good. That code is gone from the
    # catalogue because this was the only place that raised it. What is still
    # refused is a body that is not a card at all.
    name = _text(data, "name").strip()

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

    fields = {key: _text(data, key) for key in (
        "description", "personality", "scenario", "first_mes", "mes_example",
        "system_prompt")}

    # Every string this import stores or answers with, checked before the
    # write. A lone surrogate made the INSERT raise (text columns) or the
    # response encoder raise (tags, stored as escaped JSON and decoded on the
    # way out), both as a 500 (PK-07).
    if any(unpaired_surrogate_in(t)
           for t in (name, post_history, *fields.values(), *tags)):
        raise HTTPException(400, "invalid_character_json")

    # A long name is shortened, not refused (Ek P, B8): one field is not a
    # reason to throw the card away, the same rule the missing name follows
    # above. The answer says it happened; the name itself is not logged.
    report: dict = {}
    if name_is_too_long(name):
        report["name_shortened_from"] = len(name)
        name = name[:NAME_MAX_CODE_POINTS].rstrip()

    # raw_json: preserve original body exactly as received
    raw_json_str = raw.decode("utf-8", errors="replace")

    with get_db() as con:
        # BEGIN IMMEDIATE for the same reason _create_character_sync now takes
        # it: an unnamed card reads the names already stored before it writes
        # its own, and two imports landing together would otherwise both take
        # the same number.
        con.execute("BEGIN IMMEDIATE")
        name = default_names.name_or_default(
            name, default_names.CHARACTER,
            (r["name"] for r in con.execute("SELECT name FROM characters")),
        )
        cur = con.execute(
            "INSERT INTO characters (name, description, personality, scenario, "
            "first_mes, mes_example, system_prompt, post_history_instruction, "
            "tags, raw_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (name, fields["description"], fields["personality"],
             fields["scenario"], fields["first_mes"],
             fields["mes_example"], fields["system_prompt"],
             post_history, json.dumps(tags), raw_json_str),
        )
        row_id = cur.lastrowid
        row = con.execute(
            f"SELECT {_SELECT_COLS} FROM characters WHERE id = ?", (row_id,)
        ).fetchone()
    logger.info("Character imported: id=%d", row["id"])
    # `import_report` rides on the import answer only. It is empty when the
    # card went in as written; the frontend schema strips keys it does not
    # know, so an older client is unaffected.
    return {**_row_to_dict(row), "import_report": report}


@router.post("/import", status_code=201)
async def import_character(request: Request) -> dict:
    """Import a character from a raw JSON body (direct or CharCard V2)."""
    raw = await _read_capped_body(request)
    return await anyio.to_thread.run_sync(_import_character_sync, raw)


# ---------------------------------------------------------------------------
# GET /characters/{character_id}
# ---------------------------------------------------------------------------

def _get_character_sync(character_id: int) -> dict:
    """Worker-thread body (audit KÖK 8): one read plus its 404."""
    with get_db() as con:
        row = con.execute(
            f"SELECT {_SELECT_COLS} FROM characters WHERE id = ?",
            (character_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(404, "character_not_found")
    return _row_to_dict(row)


@router.get("/{character_id}")
async def get_character(character_id: int) -> dict:
    """Return a single character by ID. raw_json is excluded."""
    return await anyio.to_thread.run_sync(_get_character_sync, character_id)


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
    def name_is_trimmed(cls, v: str | None) -> str | None:
        """Trim only, and note what the two empties mean here. An ABSENT name
        leaves the stored one alone, which is what every other field in this
        model does. A name PRESENT and blank is an emptied box, and that is a
        rename to the default, decided at the write below."""
        return v.strip() if v is not None else v

    @field_validator("tags")
    @classmethod
    def tags_must_be_list_of_str(cls, v: list | None) -> list | None:
        if v is not None and not all(isinstance(t, str) for t in v):
            raise ValueError("tags must be a list of strings.")
        return v

    _no_surrogates = field_validator(
        "name", "description", "personality", "scenario", "first_mes",
        "mes_example", "system_prompt", "post_history_instruction", "tags",
        mode="before",
    )(refuse_unpaired_surrogate)


def _patch_character_sync(character_id: int, body: "CharacterPatch") -> dict:
    """Worker-thread body (audit KÖK 8): own connection, one write txn.

    BEGIN IMMEDIATE takes SQLite's writer lock; taking it on the event loop
    freezes every live SSE stream in the process until it is granted - up to
    the full busy_timeout when another writer holds it. Same transaction, off
    the loop, matching _delete_character_sync next door.
    """
    with get_db() as con:
        # Guard + UPDATE + re-SELECT in one write txn (v1.1 FB3): the bare
        # SELECT ran in autocommit, so a racing delete turned the re-SELECT
        # into None and a _row_to_dict(None) TypeError 500 instead of a 404.
        con.execute("BEGIN IMMEDIATE")
        existing = con.execute(
            "SELECT id, name FROM characters WHERE id = ?", (character_id,)
        ).fetchone()
        if existing is None:
            raise HTTPException(404, "character_not_found")

        # The cap is checked on CHANGE only (Ek P, B8). A record saved before
        # the cap existed keeps its name, and a PATCH that carries that same
        # name back (the edit dialog sends every field) is not a rename.
        if (body.name is not None and body.name != existing["name"]
                and name_is_too_long(body.name)):
            raise HTTPException(400, "name_too_long")

        updates: list[str] = []
        params: list = []
        for field_name in ("name", "description", "personality", "scenario",
                           "first_mes", "mes_example", "system_prompt",
                           "post_history_instruction"):
            val = getattr(body, field_name)
            if val is None:
                continue
            if field_name == "name" and default_names.is_blank(val):
                # An emptied name box is a rename to the default, the same as
                # it is on create. The scan excludes this character's own row:
                # the name being replaced is on its way out, so it must not
                # hold a number against its own replacement.
                val = default_names.next_default_name(
                    default_names.CHARACTER,
                    (r["name"] for r in con.execute(
                        "SELECT name FROM characters WHERE id != ?",
                        (character_id,))),
                )
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
        if row is None:
            # Defensive: a delete that raced the guard inside this txn window.
            raise HTTPException(404, "character_not_found")
    logger.info("Character updated: id=%d", character_id)
    return _row_to_dict(row)


@router.patch("/{character_id}")
async def patch_character(character_id: int, body: CharacterPatch) -> dict:
    """Partially update a character. Only provided fields are changed."""
    return await anyio.to_thread.run_sync(
        _patch_character_sync, character_id, body,
    )


# ---------------------------------------------------------------------------
# DELETE /characters/{character_id}
# ---------------------------------------------------------------------------

def _delete_character_sync(character_id: int) -> list[int]:
    """Worker-thread body (v1.1 FB2/I7): opens its own connection here, not on
    the event loop - an image-heavy cascade holds the writer for a while and
    would otherwise stall every live SSE stream. Returns the cascaded chat
    IDS. Raises HTTPException (propagates cleanly through run_sync).

    The ids rather than their count, because the caller has one more thing to
    forget with them: the notebook worker keeps a per-chat circuit breaker in
    memory, and a refusal that outlives the conversation it was about would
    refuse a chat id that has since been handed to somebody else.
    """
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

        # Cascade: messages → chats → character. FB12: chunk the chat-id lists
        # so a character with thousands of chats cannot overflow SQLite's
        # bound-parameter ceiling. All chunks share this one txn (atomic).
        chat_ids = [r["id"] for r in con.execute(
            "SELECT id FROM chats WHERE character_id = ?", (character_id,)
        ).fetchall()]

        # Collected across every chunk and swept AFTER the transaction, not
        # inside it. Deleting a character takes every chat it ever had, and
        # every spoken reply in them: this path had no audio sweep at all, so
        # the recordings outlived the rows they belonged to.
        spoken: list[int] = []

        for chunk in iter_chunks(chat_ids):
            placeholders = ",".join("?" * len(chunk))
            msg_ids = [r["id"] for r in con.execute(
                f"SELECT id FROM messages WHERE chat_id IN ({placeholders})",
                chunk,
            ).fetchall()]
            # Rows + orphaned blobs in this same transaction (E6).
            delete_for_messages(con, msg_ids)
            spoken.extend(msg_ids)
            # Inside the chunk loop, not after it: `chunk` is what this pass
            # is about to delete, and this is the path that orphans at scale -
            # deleting a character takes every chat it ever had.
            notebook.delete_for_chats(con, chunk)
            con.execute(
                f"DELETE FROM messages WHERE chat_id IN ({placeholders})", chunk,
            )
            con.execute(
                f"DELETE FROM chats WHERE id IN ({placeholders})", chunk,
            )

        con.execute("DELETE FROM characters WHERE id = ?", (character_id,))

    # Outside the `with`, so the rows have committed. The same rule the chat
    # delete follows: the database is the source of truth and lands whether or
    # not a file on disk lets go.
    audio_sweep.forget_spoken_audio(spoken)
    return chat_ids


@router.delete("/{character_id}")
async def delete_character(character_id: int) -> dict:
    """Delete a character and cascade-delete all associated chats + messages."""
    chat_ids = await anyio.to_thread.run_sync(
        _delete_character_sync, character_id)
    # Same rule as `delete_chat`, at the scale that made this path the one
    # that orphans: deleting a character takes every chat it ever had, and
    # every per-chat refusal those chats were holding.
    import notebook_worker
    for chat_id in chat_ids:
        notebook_worker.worker.forget_chat(chat_id)
    logger.info(
        "Character deleted: id=%d (cascaded %d chats)",
        character_id, len(chat_ids),
    )
    return {"ok": True}
