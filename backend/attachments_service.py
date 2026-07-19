"""attachments_service.py - Image attachment storage and lifecycle.

Design (E6 - encrypted at rest):
- Image BYTES live inside the SQLCipher-encrypted DB as content-addressed
  rows in attachment_blobs (sha256 of the FINAL, possibly downscaled bytes),
  so identical images share one blob. No plaintext image ever touches the
  filesystem; lock, rekey, and backup cover images automatically.
- Metadata rows live in the attachments table. message_id is NULL while an
  upload is merely staged; sending a message links it. A failed/aborted send
  UNLINKS (back to staged) so the client can retry with the same ids.
- Deleting messages deletes their attachment rows AND, in the same
  transaction, any blob no remaining row references (refcount-by-query).
  There is no post-commit file phase anymore - rollback restores everything.

Privacy rules:
- Image bytes are never logged. Only ids, dimensions, and byte sizes are.
- Blobs are served only to the localhost frontend via the uploads router,
  with Cache-Control: no-store.
"""

import base64
import hashlib
import io
import logging
import warnings

from PIL import Image, UnidentifiedImageError

from config import (
    ALLOWED_IMAGE_MIMES,
    IMAGE_MAX_DIMENSION,
    IMAGE_PAYLOAD_MAX_TOTAL_BYTES,
)
from database import get_db

logger = logging.getLogger(__name__)

# Decompression-bomb hardening. Pillow's own default (~89M px) only WARNS up to
# 2x and raises DecompressionBombError above that; the raise is an Exception
# subclass (not OSError/ValueError), so it must be caught explicitly. We set a
# stricter ceiling (2048x2048 downscale target ⇒ ~4.2M px final, so 32M px of
# decoded input is generous) and promote the warning to an error so a crafted
# small file cannot decode to hundreds of MB of pixels.
Image.MAX_IMAGE_PIXELS = 32_000_000

_PIL_FORMAT_BY_MIME = {
    "image/png": "PNG",
    "image/jpeg": "JPEG",
    "image/webp": "WEBP",
}


class AttachmentError(Exception):
    """Raised with a sanitized reason code (attachment_invalid, ...)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

def save_upload(data: bytes, declared_mime: str) -> dict:
    """Validate, normalise, store an uploaded image; return the API row.

    - Decode-verifies via Pillow (rejects non-images regardless of mime).
    - The DECODED format wins over the declared mime (a PNG uploaded as
      image/jpeg is stored as PNG).
    - Longest side above IMAGE_MAX_DIMENSION is downscaled (cost + provider
      limits); EXIF orientation is applied before measuring.
    - Blob + metadata land in ONE transaction: a failure anywhere rolls both
      back - no half-persisted state exists.
    Raises AttachmentError("attachment_invalid") on undecodable input.
    """
    try:
        with warnings.catch_warnings():
            # A DecompressionBombWarning (image between 1x and 2x the pixel
            # ceiling) becomes an error so it is rejected, not silently decoded.
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            img = Image.open(io.BytesIO(data))
            img.load()
    except Image.DecompressionBombError:
        logger.warning("Rejected decompression-bomb image upload.")
        raise AttachmentError("attachment_invalid")
    except Image.DecompressionBombWarning:
        logger.warning("Rejected oversized image upload (bomb warning).")
        raise AttachmentError("attachment_invalid")
    except (UnidentifiedImageError, OSError, ValueError):
        raise AttachmentError("attachment_invalid")

    fmt = (img.format or "").upper()
    mime_by_fmt = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
    mime = mime_by_fmt.get(fmt)
    if mime is None:
        raise AttachmentError("attachment_invalid")

    # Respect EXIF orientation so width/height and the stored pixels agree.
    from PIL import ImageOps
    img = ImageOps.exif_transpose(img)

    needs_downscale = max(img.size) > IMAGE_MAX_DIMENSION
    if needs_downscale:
        img.thumbnail((IMAGE_MAX_DIMENSION, IMAGE_MAX_DIMENSION), Image.LANCZOS)

    if needs_downscale or fmt == "JPEG":
        # Re-encode: downscaled images always; JPEG also normalises EXIF away.
        out = io.BytesIO()
        save_img = img
        if mime == "image/jpeg" and save_img.mode not in ("RGB", "L"):
            save_img = save_img.convert("RGB")
        save_img.save(out, format=fmt, **({"quality": 90} if fmt in ("JPEG", "WEBP") else {}))
        final_bytes = out.getvalue()
    else:
        final_bytes = data

    width, height = img.size
    sha = hashlib.sha256(final_bytes).hexdigest()

    with get_db() as con:
        # Upfront write lock (parity with the delete paths) and the blob
        # INSERT as the FIRST statement: concurrent identical uploads
        # serialize on the WAL writer lock, the loser's OR IGNORE no-ops,
        # and a failure in the attachments INSERT rolls the blob back too.
        con.execute("BEGIN IMMEDIATE")
        con.execute(
            "INSERT OR IGNORE INTO attachment_blobs (sha256, data) VALUES (?, ?)",
            (sha, final_bytes),
        )
        cur = con.execute(
            "INSERT INTO attachments (message_id, sha256, mime, width, height, byte_size) "
            "VALUES (NULL, ?, ?, ?, ?, ?)",
            (sha, mime, width, height, len(final_bytes)),
        )
        row_id = cur.lastrowid

    logger.info(
        "Attachment staged: id=%d %dx%d %d bytes", row_id, width, height, len(final_bytes),
    )
    return {"id": row_id, "mime": mime, "width": width, "height": height,
            "byte_size": len(final_bytes)}


def get_attachment(attachment_id: int) -> dict | None:
    with get_db() as con:
        row = con.execute(
            "SELECT id, message_id, sha256, mime, width, height, byte_size "
            "FROM attachments WHERE id = ?",
            (attachment_id,),
        ).fetchone()
    return dict(row) if row else None


def get_blob(attachment_id: int) -> tuple[str, bytes] | None:
    """(mime, bytes) for serving, or None when the row OR its blob is gone -
    both collapse to the same 404 upstream, matching the old missing-file
    semantics."""
    with get_db() as con:
        row = con.execute(
            "SELECT a.mime AS mime, b.data AS data "
            "FROM attachments a "
            "JOIN attachment_blobs b ON b.sha256 = a.sha256 "
            "WHERE a.id = ?",
            (attachment_id,),
        ).fetchone()
    return (row["mime"], bytes(row["data"])) if row else None


# ---------------------------------------------------------------------------
# Linking lifecycle
# ---------------------------------------------------------------------------

def validate_staged(ids: list[int]) -> list[dict]:
    """Return rows for ids, raising AttachmentError on any problem.

    attachment_not_found  - id does not exist
    attachment_unavailable - id already linked to a message
    """
    rows: list[dict] = []
    with get_db() as con:
        for aid in ids:
            row = con.execute(
                "SELECT id, message_id, sha256, mime, width, height "
                "FROM attachments WHERE id = ?",
                (aid,),
            ).fetchone()
            if row is None:
                raise AttachmentError("attachment_not_found")
            if row["message_id"] is not None:
                raise AttachmentError("attachment_unavailable")
            rows.append(dict(row))
    return rows


def link_attachments(con, ids: list[int], message_id: int) -> list[dict]:
    """Link staged rows to a message; return the rows actually linked (id ASC).

    The WHERE message_id IS NULL guard means a staged id already claimed by a
    concurrent send links 0 rows. We log that drop and return only what truly
    linked, so the caller's response reflects reality instead of echoing the
    pre-validated ids (which could report an image that landed on another
    message). Caller owns the transaction.
    """
    linked = 0
    for aid in ids:
        cur = con.execute(
            "UPDATE attachments SET message_id = ? "
            "WHERE id = ? AND message_id IS NULL",
            (message_id, aid),
        )
        linked += cur.rowcount
    if linked != len(ids):
        logger.warning(
            "Attachment link mismatch: requested=%d linked=%d message_id=%d",
            len(ids), linked, message_id,
        )
    rows = con.execute(
        "SELECT id, message_id, sha256, mime, width, height "
        "FROM attachments WHERE message_id = ? ORDER BY id ASC",
        (message_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Reads for API / payload
# ---------------------------------------------------------------------------

def load_for_messages(message_ids: list[int]) -> dict[int, list[dict]]:
    """Map message_id -> attachment rows (id ASC). Missing ids are absent."""
    if not message_ids:
        return {}
    placeholders = ",".join("?" * len(message_ids))
    with get_db() as con:
        rows = con.execute(
            f"SELECT id, message_id, sha256, mime, width, height "
            f"FROM attachments WHERE message_id IN ({placeholders}) "
            f"ORDER BY id ASC",
            message_ids,
        ).fetchall()
    out: dict[int, list[dict]] = {}
    for r in rows:
        out.setdefault(r["message_id"], []).append(dict(r))
    return out


def to_api(row: dict) -> dict:
    return {"id": row["id"], "mime": row["mime"],
            "width": row["width"], "height": row["height"]}


def prefetch_blobs(shas_newest_first: list[str]) -> dict[str, bytes]:
    """One-shot blob fetch for provider-payload assembly.

    Called OFF the event loop (anyio.to_thread) before message assembly, so
    build_image_part never opens a connection per image on the loop.

    RAM ceiling: sizes are read first and shas are admitted NEWEST-first
    until IMAGE_PAYLOAD_MAX_TOTAL_BYTES; anything beyond the cap is left out
    of the map (the assembly then drops that image with a warning, exactly
    like the historical missing-file case - the request still proceeds).
    """
    if not shas_newest_first:
        return {}
    unique: list[str] = []
    seen: set[str] = set()
    for sha in shas_newest_first:
        if sha not in seen:
            seen.add(sha)
            unique.append(sha)
    placeholders = ",".join("?" * len(unique))
    with get_db() as con:
        size_rows = con.execute(
            f"SELECT sha256, length(data) AS n FROM attachment_blobs "
            f"WHERE sha256 IN ({placeholders})",
            unique,
        ).fetchall()
        sizes = {r["sha256"]: r["n"] for r in size_rows}
        admitted: list[str] = []
        total = 0
        for sha in unique:  # newest-first order = newest wins under the cap
            n = sizes.get(sha)
            if n is None:
                continue
            if total + n > IMAGE_PAYLOAD_MAX_TOTAL_BYTES:
                continue
            total += n
            admitted.append(sha)
        if len(admitted) < len(sizes):
            logger.warning(
                "Image payload cap: admitted %d of %d blobs (%d bytes).",
                len(admitted), len(sizes), total,
            )
        if not admitted:
            return {}
        placeholders = ",".join("?" * len(admitted))
        rows = con.execute(
            f"SELECT sha256, data FROM attachment_blobs "
            f"WHERE sha256 IN ({placeholders})",
            admitted,
        ).fetchall()
    return {r["sha256"]: bytes(r["data"]) for r in rows}


def build_image_part(row: dict, blobs: dict[str, bytes]) -> dict | None:
    """OpenRouter content part with a base64 data URL, read from the
    prefetched blob map. None when the blob is absent (deleted, or dropped by
    the payload cap) - the image is omitted and the request proceeds."""
    raw = blobs.get(row["sha256"])
    if raw is None:
        logger.warning("Attachment blob missing for payload: id=%d", row["id"])
        return None
    b64 = base64.b64encode(raw).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{row['mime']};base64,{b64}"},
    }


# ---------------------------------------------------------------------------
# Deletion / cleanup
# ---------------------------------------------------------------------------

def _delete_orphan_blobs(con, shas: set[str]) -> None:
    """Within the caller's transaction: drop blobs in `shas` that no
    attachments row references anymore. Parameter-safe (the set is bounded by
    the delete batch) and atomic with the row deletes - a rollback restores
    rows and blobs together."""
    if not shas:
        return
    sha_list = sorted(shas)
    placeholders = ",".join("?" * len(sha_list))
    con.execute(
        f"DELETE FROM attachment_blobs "
        f"WHERE sha256 IN ({placeholders}) "
        f"AND NOT EXISTS (SELECT 1 FROM attachments a "
        f"                WHERE a.sha256 = attachment_blobs.sha256)",
        sha_list,
    )


def delete_for_messages(con, message_ids: list[int]) -> None:
    """Delete attachment rows for messages AND their now-orphaned blobs, all
    inside the caller's transaction. Nothing happens outside the transaction
    anymore (the old post-commit file phase is gone)."""
    if not message_ids:
        return
    placeholders = ",".join("?" * len(message_ids))
    rows = con.execute(
        f"SELECT sha256 FROM attachments "
        f"WHERE message_id IN ({placeholders})",
        message_ids,
    ).fetchall()
    if not rows:
        return
    con.execute(
        f"DELETE FROM attachments WHERE message_id IN ({placeholders})",
        message_ids,
    )
    _delete_orphan_blobs(con, {r["sha256"] for r in rows})


def purge_stale_staged(hours: int = 24) -> int:
    """Remove staged rows (message_id NULL) older than `hours` + their
    orphaned blobs, in one transaction. Runs at unlock bootstrap."""
    with get_db() as con:
        # Upfront write lock: the SELECT below feeds a DELETE - without it the
        # read runs in autocommit and a concurrent link could slip between.
        con.execute("BEGIN IMMEDIATE")
        rows = con.execute(
            "SELECT id, sha256 FROM attachments "
            "WHERE message_id IS NULL "
            "AND created_at < datetime('now', ?)",
            (f"-{hours} hours",),
        ).fetchall()
        if not rows:
            return 0
        ids = [r["id"] for r in rows]
        placeholders = ",".join("?" * len(ids))
        con.execute(f"DELETE FROM attachments WHERE id IN ({placeholders})", ids)
        _delete_orphan_blobs(con, {r["sha256"] for r in rows})
    logger.info("Purged %d stale staged attachments.", len(rows))
    return len(rows)
