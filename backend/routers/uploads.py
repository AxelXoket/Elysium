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
from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import Response

from config import MAX_UPLOAD_BYTES
from attachments_service import (
    AttachmentError,
    save_upload,
    get_blob,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/uploads", tags=["uploads"])


@router.post("/images", status_code=201)
async def upload_image(file: UploadFile) -> dict:
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
        return await anyio.to_thread.run_sync(
            save_upload, data, file.content_type or ""
        )
    except AttachmentError as exc:
        raise HTTPException(400, exc.reason)


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
    return Response(
        content=data,
        media_type=mime,
        headers={"Cache-Control": "no-store"},
    )
