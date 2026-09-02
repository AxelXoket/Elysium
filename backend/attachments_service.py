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
    MAX_ATTACHMENTS_PER_MESSAGE,
    MAX_UPLOAD_BYTES,
)
from database import get_db, iter_chunks

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


#: Every reason an AttachmentError can carry.
#:
#: The routers relay `exc.reason` straight into an HTTPException detail, so
#: these are user-facing codes even though no raise site in a router spells one
#: out. `tests/error_enumeration.py` reads this to enumerate those sites; a new
#: reason that is not added here fails that test rather than reaching a user as
#: "Something went wrong. Please try again."
ATTACHMENT_REASONS: frozenset[str] = frozenset({
    "attachment_invalid",
    "attachment_too_large",
    "attachment_not_found",
    "attachment_unavailable",
    "too_many_attachments",
})


class AttachmentError(Exception):
    """Raised with a sanitized reason code (attachment_invalid, ...)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

def rebuilt_transparency(img):
    """The tRNS chunk this app is willing to write, rebuilt from the pixels.

    K-18. The old carve-out passed `img.info["transparency"]` through
    UNTOUCHED, and the comment beside it said it "carries no uploader
    information - it is a palette index or a grey level". That is true of the
    `int` form and false of the other one: in mode P a tRNS is often a BYTE
    STRING, one alpha value per palette entry, up to 256 of them. Every entry
    the image never uses is a byte nobody looks at, riding out of this machine
    inside a file the app re-encoded specifically so that nothing would.

    Measured on 3004 real PNGs before choosing: refusing `bytes` outright
    would have visibly broken 153 of the 153 files that carry one - every
    single one - so "just drop it" was never an option. Rebuilding it from
    what the pixels actually show changed NONE of the 168 affected files.

    Two things are rebuilt, and the second is the strict half:

      * the byte string is regenerated from the alpha values the image really
        uses, so unused entries carry nothing;
      * an `int` naming a palette index that no pixel refers to is dropped
        entirely - it says something about the file's history and nothing
        about its appearance.

    Returns what to store under "transparency", or None for "write no tRNS".
    """
    transparency = img.info.get("transparency")
    if transparency is None or img.mode not in ("P", "L"):
        return None

    if img.mode == "L":
        # One grey level, and it is either used or it is not.
        if not isinstance(transparency, int):
            return None
        return transparency if transparency in set(img.getdata()) else None

    used = {index for index, count in enumerate(img.histogram()) if count}
    if isinstance(transparency, int):
        # The strict variant. An index no pixel uses describes nothing on
        # screen.
        return transparency if transparency in used else None
    if isinstance(transparency, (bytes, bytearray)):
        rebuilt = bytearray(
            alpha if index in used else 255
            for index, alpha in enumerate(transparency)
        )
        # Trailing fully-opaque entries say nothing; the PNG spec lets a tRNS
        # stop early and treats the rest as opaque.
        while rebuilt and rebuilt[-1] == 255:
            rebuilt.pop()
        return bytes(rebuilt) if rebuilt else None
    return None


def normalise_image(data: bytes) -> tuple[bytes, str, int, int]:
    """Validate and strip an image; return (final_bytes, mime, width, height).

    Every image guard in the app lives in here, and it lives in ONE place
    on purpose. All of it used to sit inside save_upload, which only the
    multipart upload route can call - so a second writer (a picture the MODEL
    produced) would have inherited none of it: not the pixel ceiling, not the
    byte ceiling, not the format allowlist, not the downscale, not the
    metadata strip. A 20000x20000 solid PNG is a few KB on the wire and
    gigabytes once decoded, and provider bytes are no more trustworthy than
    browser bytes.

    - Decode-verifies via Pillow (rejects non-images regardless of mime).
    - The DECODED format wins over any declared mime (a PNG announced as
      image/jpeg is stored as PNG). Nothing the caller was TOLD is trusted.
    - Longest side above IMAGE_MAX_DIMENSION is downscaled (cost + provider
      limits); EXIF orientation is applied before measuring.
    - ALWAYS re-encoded through a metadata-free image: EXIF/GPS/ICC/XMP and
      text chunks are stripped so nothing about the uploader leaves with the
      image (L3). The decoded pixels are preserved; only metadata is dropped.

    Raises AttachmentError("attachment_invalid") on undecodable input and
    AttachmentError("attachment_too_large") past MAX_UPLOAD_BYTES. The byte
    ceiling is checked HERE rather than only in the HTTP handler, because the
    handler is one of two callers now and the other one is fed by a remote
    party that chooses its own response size.
    """
    if len(data) > MAX_UPLOAD_BYTES:
        logger.warning("Rejected oversized image payload (%d bytes).", len(data))
        raise AttachmentError("attachment_too_large")

    # Deliberately NOT warnings.catch_warnings(). That context manager swaps a
    # PROCESS-GLOBAL filter list and is documented as not thread-safe, while
    # save_upload runs in the anyio worker threadpool - so with two uploads in
    # flight, one context manager's exit restored the filter list while the
    # other was still inside img.load(), and a bomb walked straight through
    # the check that exists to stop it. Pillow's own warning only fires
    # between 1x and 2x MAX_IMAGE_PIXELS anyway.
    #
    # Image.open() parses the header only, so the dimensions are known here
    # without having decoded a single pixel. Comparing them directly is both
    # deterministic and cheaper: no global state, no ordering, no threads.
    try:
        img = Image.open(io.BytesIO(data))
        limit = Image.MAX_IMAGE_PIXELS
        if limit is not None:
            width, height = img.size
            if width * height > limit:
                logger.warning("Rejected oversized image upload (%dx%d).",
                               width, height)
                raise AttachmentError("attachment_invalid")
        img.load()
    except Image.DecompressionBombError:
        # Kept as a backstop: Pillow raises this above 2x the ceiling from
        # inside load(), for shapes the header check cannot see.
        logger.warning("Rejected decompression-bomb image upload.")
        raise AttachmentError("attachment_invalid")
    except (UnidentifiedImageError, OSError, ValueError):
        raise AttachmentError("attachment_invalid")

    fmt = (img.format or "").upper()
    # MPO is a JPEG carrying an MPF/APP2 multi-picture marker - what dual-lens
    # and 3D-capable cameras produce, and what several Android multi-frame
    # captures and iPhone exports look like. Pillow reports the container
    # format, so a perfectly valid image/jpeg was told it was not an image at
    # all, after the browser had already rendered its preview. Only the first
    # frame is kept, which is the frame every other viewer shows.
    if fmt == "MPO":
        fmt = "JPEG"
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

    # v1.1 audit L3 (privacy): ALWAYS re-encode - never store the upload
    # verbatim. exif_transpose re-attaches the non-orientation EXIF (camera
    # model, timestamps, GPS coordinates) to img.info, and every save plugin
    # (PNG/JPEG/WebP) falls back to img.info for exif/icc/xmp. Storing bytes
    # verbatim - or re-saving without clearing info - would forward the
    # uploader's location to the provider the moment the image is sent. Wiping
    # img.info before save strips EXIF/ICC/XMP/text chunks for all 3 formats.
    # tRNS is NOT metadata. For mode-P and mode-L images PIL keeps the PNG
    # transparency chunk in the SAME dict as EXIF/ICC/XMP, so wiping img.info
    # re-encoded every palette/greyscale PNG fully OPAQUE: "Save for Web"
    # exports, TinyPNG/ImageOptim output, most logo and icon files. The
    # composer preview and the provider payload were both built from the
    # corrupted copy, and the original bytes were already gone. Only
    # alpha-channel modes (RGBA/LA) survived. It carries no uploader
    # information - it is a palette index or a grey level.
    # K-18. Rebuilt rather than copied: the `bytes` form of tRNS carries one
    # alpha value per palette entry, and every entry the image does not use is
    # a byte nobody can see riding out inside a file this function re-encoded
    # precisely so that nothing would.
    transparency = rebuilt_transparency(img)
    img.info = {}
    if transparency is not None and fmt == "PNG":
        img.info["transparency"] = transparency
    save_img = img
    if mime == "image/jpeg" and save_img.mode not in ("RGB", "L"):
        save_img = save_img.convert("RGB")  # convert copies the now-empty info
    out = io.BytesIO()
    try:
        save_img.save(out, format=fmt, **({"quality": 90} if fmt in ("JPEG", "WEBP") else {}))
    except (OSError, ValueError):
        # Exotic mode the encoder cannot round-trip: reject rather than fall
        # back to storing the metadata-bearing original (privacy over fidelity).
        raise AttachmentError("attachment_invalid")
    final_bytes = out.getvalue()
    width, height = img.size
    return final_bytes, mime, width, height


def save_upload(data: bytes, declared_mime: str) -> dict:
    """Validate, normalise and STAGE an uploaded image; return the API row.

    Staged means message_id IS NULL: the client holds the id until it sends the
    message the image belongs to. Blob + metadata land in ONE transaction, so a
    failure anywhere rolls both back and no half-persisted state exists.
    """
    final_bytes, mime, width, height = normalise_image(data)
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


def store_generated_image(con, prepared: tuple[bytes, str, int, int],
                          message_id: int) -> dict:
    """Store an ALREADY-NORMALISED image a model produced, owned by `message_id`.

    Takes the CALLER'S connection, and deliberately opens no transaction of its
    own: the caller is already inside the BEGIN IMMEDIATE that writes the
    assistant row, and the bytes must commit with that row or not at all. A
    generated image is worthless without the reply it belongs to, and a reply
    that claims an image it does not have renders as a broken thumbnail forever.

    `prepared` comes from normalise_image, and it is a parameter rather than
    something this function does, because this function runs UNDER THE WRITER
    LOCK. Decoding here made it the only writer in the backend that does Pillow
    work inside BEGIN IMMEDIATE - save_upload normalises before it opens its
    transaction, and that is the shape to match. Measured: twenty 5000x5000
    solid PNGs, 84 KB each on the wire, held the writer for 4.5 seconds and made
    a concurrent write fail at its 800ms budget.

    Never staged. save_upload's NULL message_id is right for an upload - the
    client keeps the id and can retry - but a staged GENERATED row would sit in
    the pool that validate_staged hands to any subsequent user send, until the
    24h purge swept it.

    The per-message count cap is checked here as the LAST line of defence (it
    is one cheap COUNT, no decode). Callers are expected to stop earlier so the
    surplus is never decoded at all.
    """
    final_bytes, mime, width, height = prepared

    already = con.execute(
        "SELECT COUNT(*) AS c FROM attachments WHERE message_id = ?",
        (message_id,),
    ).fetchone()["c"]
    if already >= MAX_ATTACHMENTS_PER_MESSAGE:
        logger.warning("Refusing image %d for message %d: cap reached.",
                       already + 1, message_id)
        raise AttachmentError("too_many_attachments")

    sha = hashlib.sha256(final_bytes).hexdigest()
    # Content-addressed on the FINAL bytes, exactly as uploads are, so a model
    # that returns the same picture twice costs one blob.
    con.execute(
        "INSERT OR IGNORE INTO attachment_blobs (sha256, data) VALUES (?, ?)",
        (sha, final_bytes),
    )
    cur = con.execute(
        "INSERT INTO attachments (message_id, sha256, mime, width, height, byte_size) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (message_id, sha, mime, width, height, len(final_bytes)),
    )
    row_id = cur.lastrowid
    logger.info("Generated image stored: id=%d msg=%d %dx%d %d bytes",
                row_id, message_id, width, height, len(final_bytes))
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
    # FB12: chunk the IN(...) list so a thousand-message chat cannot overflow
    # SQLite's bound-parameter ceiling. Sorted per chunk; global ordering does
    # not matter (grouped by message_id below).
    out: dict[int, list[dict]] = {}
    with get_db() as con:
        for chunk in iter_chunks(message_ids):
            placeholders = ",".join("?" * len(chunk))
            rows = con.execute(
                f"SELECT id, message_id, sha256, mime, width, height "
                f"FROM attachments WHERE message_id IN ({placeholders}) "
                f"ORDER BY id ASC",
                chunk,
            ).fetchall()
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
    result: dict[str, bytes] = {}
    with get_db() as con:
        # FB12: the SIZES query runs over ALL unique shas BEFORE the byte cap,
        # so it must be chunked (a chat with thousands of images would overflow).
        sizes: dict[str, int] = {}
        for chunk in iter_chunks(unique):
            placeholders = ",".join("?" * len(chunk))
            for r in con.execute(
                f"SELECT sha256, length(data) AS n FROM attachment_blobs "
                f"WHERE sha256 IN ({placeholders})",
                chunk,
            ).fetchall():
                sizes[r["sha256"]] = r["n"]
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
        # Byte-capped, but chunk for uniformity (admitted can still be large).
        for chunk in iter_chunks(admitted):
            placeholders = ",".join("?" * len(chunk))
            for r in con.execute(
                f"SELECT sha256, data FROM attachment_blobs "
                f"WHERE sha256 IN ({placeholders})",
                chunk,
            ).fetchall():
                result[r["sha256"]] = bytes(r["data"])
    return result


def build_image_part(row: dict, blobs: dict[str, bytes],
                     omitted: list[int] | None = None) -> dict | None:
    """OpenRouter content part with a base64 data URL, read from the
    prefetched blob map.

    None when the blob is absent - the image is omitted and the request
    proceeds, which is correct: one unreadable picture must not fail a whole
    conversation. What was NOT correct is that the omission went no further
    than a log line, so the completion succeeded and the user got a normal
    answer from a model that had never seen their image.

    `omitted` collects the attachment ids that were left out so the caller can
    say so. Two different causes with the same shape: the payload cap is
    expected and informational, a missing blob is a data-integrity problem.
    """
    raw = blobs.get(row["sha256"])
    if raw is None:
        logger.warning("Attachment blob missing for payload: id=%d", row["id"])
        if omitted is not None:
            omitted.append(row["id"])
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
    attachments row references anymore. Atomic with the row deletes - a
    rollback restores rows and blobs together. FB12: `shas` is no longer
    bounded (callers now pass whole-chat sets), so chunk the IN(...) list."""
    if not shas:
        return
    sha_list = sorted(shas)
    for chunk in iter_chunks(sha_list):
        placeholders = ",".join("?" * len(chunk))
        con.execute(
            f"DELETE FROM attachment_blobs "
            f"WHERE sha256 IN ({placeholders}) "
            f"AND NOT EXISTS (SELECT 1 FROM attachments a "
            f"                WHERE a.sha256 = attachment_blobs.sha256)",
            chunk,
        )


def delete_for_messages(con, message_ids: list[int]) -> None:
    """Delete attachment rows for messages AND their now-orphaned blobs, all
    inside the caller's transaction. Nothing happens outside the transaction
    anymore (the old post-commit file phase is gone). FB12: chunked IN(...)
    lists - still atomic (all chunks share the caller's one txn)."""
    if not message_ids:
        return
    shas: set[str] = set()
    for chunk in iter_chunks(message_ids):
        placeholders = ",".join("?" * len(chunk))
        shas.update(
            r["sha256"] for r in con.execute(
                f"SELECT sha256 FROM attachments "
                f"WHERE message_id IN ({placeholders})",
                chunk,
            ).fetchall()
        )
        con.execute(
            f"DELETE FROM attachments WHERE message_id IN ({placeholders})",
            chunk,
        )
    _delete_orphan_blobs(con, shas)


def delete_staged(attachment_id: int) -> None:
    """Unstage one upload (v1.1 FB8/I12): delete the row ONLY while it is
    staged (message_id IS NULL), plus its now-orphaned blob, in one txn.

    Raises AttachmentError:
      attachment_not_found   - no such row
      attachment_unavailable - already linked to a message (it belongs to
                               that message now and dies with it, not here)
    """
    with get_db() as con:
        # Upfront write lock: the check feeds a DELETE, so a concurrent link
        # must not slip between (same rationale as purge_stale_staged).
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT id, message_id, sha256 FROM attachments WHERE id = ?",
            (attachment_id,),
        ).fetchone()
        if row is None:
            raise AttachmentError("attachment_not_found")
        if row["message_id"] is not None:
            raise AttachmentError("attachment_unavailable")
        con.execute("DELETE FROM attachments WHERE id = ?", (attachment_id,))
        _delete_orphan_blobs(con, {row["sha256"]})
    logger.info("Attachment unstaged: id=%d", attachment_id)


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
        for chunk in iter_chunks(ids):  # FB12: unbounded staged set
            placeholders = ",".join("?" * len(chunk))
            con.execute(
                f"DELETE FROM attachments WHERE id IN ({placeholders})", chunk,
            )
        _delete_orphan_blobs(con, {r["sha256"] for r in rows})
    logger.info("Purged %d stale staged attachments.", len(rows))
    return len(rows)
