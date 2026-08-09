"""routers/uploads.py -- Image attachment upload/serve endpoints (Part H).

Routes:
    POST /uploads/images        - stage an image (multipart field "file")
    GET  /uploads/images/{id}   - serve the stored binary to the frontend

Privacy invariants:
    - Image bytes are NEVER logged.
    - Only attachment id, dimensions, and byte size are logged.
    - Image bytes live as blobs INSIDE the encrypted DB (E6) and are served
      only to the localhost frontend with Cache-Control: no-store; they are
      never exposed as public URLs (the provider receives base64 data URLs
      built at request time instead).
    - This module does NOT import httpx, openrouter, network_client,
      proxy_health, or keyring.
"""

import logging

import anyio.to_thread
from fastapi import APIRouter, BackgroundTasks, HTTPException, UploadFile
from fastapi.responses import Response

from starlette.formparsers import MultiPartParser

from config import ALLOWED_IMAGE_MIMES, MAX_UPLOAD_BYTES, UPLOAD_SPOOL_LIMIT
from attachments_service import (
    AttachmentError,
    save_upload,
    get_blob,
    delete_staged,
    purge_stale_staged,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/uploads", tags=["uploads"])


def _purge_stale_best_effort() -> None:
    """FB8 opportunistic purge after a successful upload. Never raises - a
    cleanup failure must not fail the upload that triggered it (FB4 parity)."""
    try:
        purge_stale_staged()
    except Exception:
        logger.exception(
            "opportunistic staged-purge failed; next upload/unlock retries.",
        )


# Starlette rolls a multipart FILE part to a real temp file as soon as it grows
# past spool_max_size, and its max_part_size bound is applied only to non-file
# parts. So every upload over 1 MiB was written to %TEMP% in CLEARTEXT before
# this handler ever ran - recoverable after unlink until the sectors are reused,
# and flatly contradicting attachments_service's "No plaintext image ever
# touches the filesystem". Raising the spool above our own cap keeps a LEGAL
# upload entirely in RAM; the Content-Length gate in main.py keeps an illegal
# one from being read at all.
#
# Derived from the SAME constant as that gate (config.UPLOAD_BODY_LIMIT), and
# deliberately one byte above it: while these were two independently-written
# numbers there was a band between them where the gate said yes and the spool
# said disk. See config.py for the measurement.
MultiPartParser.spool_max_size = UPLOAD_SPOOL_LIMIT


@router.post("/images", status_code=201)
async def upload_image(file: UploadFile, background_tasks: BackgroundTasks) -> dict:
    """Stage an image for a future message. Returns attachment metadata."""
    # Read at most one byte past the cap so an oversized upload is rejected
    # without buffering an unbounded body into memory.
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "attachment_too_large")
    if not data:
        raise HTTPException(400, "attachment_invalid")

    try:
        # Worker thread: Pillow decode + LANCZOS thumbnail + sha256 + disk
        # write take hundreds of ms for a large image - on the event loop
        # they would freeze every live SSE stream mid-delta.
        result = await anyio.to_thread.run_sync(
            save_upload, data, file.content_type or ""
        )
    except AttachmentError as exc:
        raise HTTPException(400, exc.reason)

    # Staged rows now also die via the DELETE endpoint below, but anything the
    # client failed to unstage still ages out - sweep opportunistically off
    # the response (Starlette runs this in its threadpool, after the reply).
    background_tasks.add_task(_purge_stale_best_effort)
    return result


@router.delete("/images/{attachment_id}")
async def unstage_image(attachment_id: int) -> dict:
    """Unstage a not-yet-sent upload (v1.1 FB8/I12). Staged rows only - a
    linked image belongs to its message and dies with it (409), not here."""
    try:
        await anyio.to_thread.run_sync(delete_staged, attachment_id)
    except AttachmentError as exc:
        status = 404 if exc.reason == "attachment_not_found" else 409
        raise HTTPException(status, exc.reason)
    return {"ok": True}


@router.get("/images/{attachment_id}")
async def serve_image(attachment_id: int) -> Response:
    """Serve a stored image to the frontend (localhost only by binding).

    Worker thread: the blob SELECT decrypts up to several MB - on the event
    loop it would stall live SSE streams. Missing row and missing blob are
    the same 404 (matches the historical missing-file semantics).
    no-store: the browser must not keep plaintext image bytes in its HTTP
    cache once the vault locks.
    """
    result = await anyio.to_thread.run_sync(get_blob, attachment_id)
    if result is None:
        raise HTTPException(404, "attachment_not_found")
    mime, data = result
    if mime not in ALLOWED_IMAGE_MIMES:
        # Defence in depth on the ONE value in this response that a browser
        # will act on. save_upload derives the mime from the bytes Pillow
        # actually decoded, so today a row cannot hold anything else - but this
        # URL is same-origin with the SPA and with the whole unauthenticated
        # local API, so "image/svg+xml got into the column somehow" must not be
        # one edit away from a scripted same-origin document. Checking here
        # means the guarantee does not depend on every future writer
        # remembering. 415, not 404: the row exists and the bytes are there.
        logger.warning("Refusing to serve an unexpected media type: id=%d",
                       attachment_id)
        raise HTTPException(415, "unsupported_media_type")
    return Response(
        content=data,
        media_type=mime,
        headers={"Cache-Control": "no-store"},
    )
