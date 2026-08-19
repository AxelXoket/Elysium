"""routers/notebook.py -- the per-chat notebook and the boundaries (FAZ 1).

Routes:
    GET    /notebook/{chat_id}                 - notes for one chat
    POST   /notebook/{chat_id}                 - add a note
    PATCH  /notebook/entries/{id}              - edit a note
    DELETE /notebook/entries/{id}              - remove a note
    POST   /notebook/{chat_id}/reorder         - set the order
    GET    /notebook/{chat_id}/boundaries      - limits in force for this chat
    GET    /notebook/boundaries                - the global set
    POST   /notebook/boundaries                - add a limit (global or chat)
    DELETE /notebook/boundaries/{id}           - remove a limit
    POST   /notebook/{chat_id}/use-global      - follow the global set, or not

Privacy invariants:
    - Note and boundary TEXT is never logged, at any level. Ids and counts
      only. A shipped memory feature elsewhere leaked health and relationship
      data through eighteen INFO-level statements; the notebook is the most
      distilled text this app holds, so the rule here is absolute rather than
      case-by-case.
    - This module does NOT import httpx, requests, urllib.request, keyring,
      openrouter, network_client, or proxy_health.

Every handler hops off the event loop before touching the database (audit
KÖK 8). The reads are small, but "small" is a property of today's data and the
loop being stalled at a fixed cadence is how the last one of these was found.
"""

import logging

import anyio.to_thread
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import notebook_store as notebook

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notebook", tags=["notebook"])

#: Large enough that the fraction never binds, so the gauge reports what the
#: notebook actually costs rather than what one particular model would allow.
_GAUGE_AVAILABLE = 10_000_000


class EntryBody(BaseModel):
    text: str = Field(min_length=1)
    kind: str = "fact"
    durability: str = "permanent"
    importance: int = Field(default=2, ge=1, le=3)
    pinned: bool = False


class EntryPatch(BaseModel):
    text: str | None = Field(default=None, min_length=1)
    kind: str | None = None
    durability: str | None = None
    importance: int | None = Field(default=None, ge=1, le=3)
    pinned: bool | None = None
    # `provenance` is absent and stays absent. Accepting it here - even to
    # ignore it - would put the idea in the schema that it is a thing an edit
    # can carry, and the next reader would wire it up.


class ReorderBody(BaseModel):
    ordered_ids: list[int]


class BoundaryBody(BaseModel):
    label: str = Field(min_length=1)
    phrasing: str = Field(min_length=1)
    severity: str
    chat_id: int | None = None
    polarity: str = "avoid"
    on_violation: str = "pause"
    rating_ceiling: str | None = None
    # `source` is absent: a limit created through this route is one a person
    # typed, so it is `explicit` by construction. The database refuses an
    # inferred hard limit, and this route cannot produce an inferred one at all.


class UseGlobalBody(BaseModel):
    use_global: bool


def _refuse(exc: notebook.NotebookError):
    raise HTTPException(400, exc.code)


# LITERAL PATHS FIRST. FastAPI matches in registration order, so with
# `/{chat_id}` declared above them a request for `/notebook/boundaries` binds
# chat_id="boundaries" and 422s - the route exists and is unreachable.
@router.get("/boundaries")
async def list_global_boundaries() -> dict:
    rows = await anyio.to_thread.run_sync(notebook.list_boundaries, None)
    return {"boundaries": rows}


@router.post("/boundaries")
async def create_boundary(body: BoundaryBody) -> dict:
    try:
        row = await anyio.to_thread.run_sync(
            lambda: notebook.create_boundary(
                body.label, body.phrasing, body.severity,
                chat_id=body.chat_id, polarity=body.polarity,
                on_violation=body.on_violation,
                rating_ceiling=body.rating_ceiling))
    except notebook.NotebookError as exc:
        _refuse(exc)
    logger.info("Boundary added: scope=%s id=%d", row["scope"], row["id"])
    return row


@router.delete("/boundaries/{boundary_id}")
async def delete_boundary(boundary_id: int) -> dict:
    removed = await anyio.to_thread.run_sync(
        notebook.delete_boundary, boundary_id)
    if not removed:
        raise HTTPException(404, "boundary_not_found")
    logger.info("Boundary removed: id=%d", boundary_id)
    return {"ok": True}


@router.get("/{chat_id}")
async def list_entries(chat_id: int) -> dict:
    """The notes, and what they COST - measured here, not re-derived there.

    `notebook_chars` crosses the wire the way `/tts/voice-mode` sends
    `prompt_chars`: the frontend charges an opaque number instead of rebuilding
    the block. The voice block is the one part of the fixed cost the estimator
    does not duplicate, and it is the one part that cannot drift. The character
    header drifted once already because two languages built the same string;
    this is that lesson applied before it can happen again.
    """
    def _read() -> dict:
        entries = notebook.list_entries(chat_id)
        blocks = notebook.build_notebook_blocks(chat_id, _GAUGE_AVAILABLE)
        return {
            "entries": entries,
            # Gauge figure only: the real ceiling depends on the model chosen
            # for the turn, which this route does not know. Sized against a
            # generous budget so it reports the full cost rather than a
            # truncated one, and the per-turn truth arrives in the done frame.
            "notebook_chars": (len(blocks["user_block"])
                               + len(blocks["model_block"])
                               + len(blocks["boundary_block"])),
        }
    return await anyio.to_thread.run_sync(_read)


@router.post("/{chat_id}")
async def create_entry(chat_id: int, body: EntryBody) -> dict:
    try:
        entry = await anyio.to_thread.run_sync(
            lambda: notebook.create_entry(
                chat_id, body.text, kind=body.kind,
                durability=body.durability, importance=body.importance,
                pinned=body.pinned))
    except notebook.NotebookError as exc:
        _refuse(exc)
    logger.info("Notebook entry added: chat=%d id=%d", chat_id, entry["id"])
    return entry


@router.patch("/entries/{entry_id}")
async def patch_entry(entry_id: int, body: EntryPatch) -> dict:
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(400, "notebook_entry_invalid")
    try:
        entry = await anyio.to_thread.run_sync(
            lambda: notebook.update_entry(entry_id, **fields))
    except notebook.NotebookError as exc:
        _refuse(exc)
    return entry


@router.delete("/entries/{entry_id}")
async def delete_entry(entry_id: int) -> dict:
    removed = await anyio.to_thread.run_sync(notebook.delete_entry, entry_id)
    if not removed:
        raise HTTPException(404, "notebook_entry_not_found")
    logger.info("Notebook entry removed: id=%d", entry_id)
    return {"ok": True}


@router.post("/{chat_id}/reorder")
async def reorder(chat_id: int, body: ReorderBody) -> dict:
    await anyio.to_thread.run_sync(
        lambda: notebook.reorder(chat_id, body.ordered_ids))
    return {"ok": True}


@router.get("/{chat_id}/boundaries")
async def list_chat_boundaries(chat_id: int) -> dict:
    """What is actually in force here - global plus this chat's, or this
    chat's alone when it has been told to stand on its own."""
    try:
        rows = await anyio.to_thread.run_sync(notebook.list_boundaries, chat_id)
    except notebook.NotebookError as exc:
        raise HTTPException(404, exc.code) from None
    return {"boundaries": rows}


@router.post("/{chat_id}/use-global")
async def set_use_global(chat_id: int, body: UseGlobalBody) -> dict:
    await anyio.to_thread.run_sync(
        lambda: notebook.set_use_global_boundaries(chat_id, body.use_global))
    return {"ok": True, "use_global": body.use_global}
