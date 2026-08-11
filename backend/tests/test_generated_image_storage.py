"""Bytes a MODEL produced go through the same door as bytes a person uploaded.

Every image guard in this app used to live inside `save_upload`, which only the
multipart upload route can call: the 32M-pixel ceiling, the 10 MiB byte ceiling,
the format allowlist, the 2048px downscale, the always-re-encode. A second
writer would have inherited none of them - and the existing bomb test drives
POST /uploads/images, so it proves nothing about a second path.

Provider bytes deserve less trust than browser bytes, not more: the size of that
response is chosen by the far end, and a 20000x20000 solid PNG is a few KB on the
wire and gigabytes once decoded.
"""
from __future__ import annotations

import base64
import io

import pytest
from PIL import Image

import attachments_service as svc
import database
from config import MAX_ATTACHMENTS_PER_MESSAGE, MAX_UPLOAD_BYTES
from tests.conftest import make_character, make_chat


def _png(size=(8, 8), colour=(10, 20, 30), **save_kw) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, format="PNG", **save_kw)
    return buf.getvalue()


def _assistant_row(client) -> int:
    """A real assistant message row to own the image."""
    char = make_character(client, first_mes="Hi.")
    chat = make_chat(client, char)
    with database.get_db() as con:
        return con.execute(
            "SELECT id FROM messages WHERE chat_id = ? ORDER BY id ASC LIMIT 1",
            (chat,),
        ).fetchone()["id"]


def _store(con, raw: bytes, msg: int) -> dict:
    """What production does: normalise OUTSIDE the write, store INSIDE it.

    The split is the point. store_generated_image runs under the SQLite writer
    lock, so Pillow work must not happen there - save_upload has always
    normalised before opening its transaction, and this writer now matches.
    """
    return svc.store_generated_image(con, svc.normalise_image(raw), msg)


def _counts() -> tuple[int, int]:
    with database.get_db() as con:
        rows = con.execute("SELECT COUNT(*) AS c FROM attachments").fetchone()["c"]
        blobs = con.execute("SELECT COUNT(*) AS c FROM attachment_blobs").fetchone()["c"]
    return rows, blobs


# ── the happy path ───────────────────────────────────────────────────────────

def test_a_generated_image_is_owned_by_its_message_from_birth(client):
    """Never staged. A staged generated row would sit in the pool that
    validate_staged hands to any later user send."""
    msg = _assistant_row(client)
    with database.get_db() as con:
        con.execute("BEGIN IMMEDIATE")
        out = _store(con, _png(), msg)

    assert out["mime"] == "image/png"
    assert out["width"] == 8 and out["height"] == 8
    with database.get_db() as con:
        row = con.execute("SELECT message_id FROM attachments WHERE id = ?",
                          (out["id"],)).fetchone()
    assert row["message_id"] == msg


def test_it_commits_with_the_callers_transaction_not_its_own(client):
    """The bytes must land with the reply or not at all. If this function opened
    its own transaction, a caller that rolled back would leave an orphan blob."""
    msg = _assistant_row(client)
    before = _counts()
    with pytest.raises(RuntimeError):
        with database.get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            _store(con, _png(), msg)
            raise RuntimeError("caller failed after the image was written")
    assert _counts() == before


def test_two_identical_images_share_one_blob(client):
    """Content-addressed on the FINAL bytes, exactly as uploads are."""
    msg = _assistant_row(client)
    data = _png()
    with database.get_db() as con:
        con.execute("BEGIN IMMEDIATE")
        _store(con, data, msg)
        _store(con, data, msg)
    rows, blobs = _counts()
    assert rows == 2 and blobs == 1


# ── the guards it now inherits ───────────────────────────────────────────────

def test_a_decompression_bomb_is_refused(client):
    """A few KB on the wire, gigabytes decoded. The header check sees it before
    a single pixel is decoded."""
    msg = _assistant_row(client)
    # 36M pixels, past the 32M ceiling, in a file small enough that no byte
    # limit would ever have caught it. Same shape as the upload-path bomb test.
    bomb = _png(size=(6000, 6000), compress_level=9)
    assert len(bomb) < MAX_UPLOAD_BYTES, "fixture is not actually a bomb"
    before = _counts()
    with database.get_db() as con:
        con.execute("BEGIN IMMEDIATE")
        with pytest.raises(svc.AttachmentError) as exc:
            _store(con, bomb, msg)
    assert exc.value.reason == "attachment_invalid"
    assert _counts() == before


def test_a_payload_past_the_byte_ceiling_is_refused_before_decoding():
    """The ceiling used to live only in the HTTP handler, so a caller fed by a
    remote party that chooses its own response size had none at all.

    Checked against normalise_image directly, because that is where the refusal
    now belongs: it happens before any write is opened at all."""
    with pytest.raises(svc.AttachmentError) as exc:
        svc.normalise_image(b"\x00" * (MAX_UPLOAD_BYTES + 1))
    assert exc.value.reason == "attachment_too_large"


def test_something_that_is_not_an_image_is_refused(client):
    msg = _assistant_row(client)
    with database.get_db() as con:
        con.execute("BEGIN IMMEDIATE")
        with pytest.raises(svc.AttachmentError):
            _store(con, b"<svg xmlns='http://www.w3.org/2000/svg'/>", msg)


def test_the_mime_is_derived_from_the_bytes_not_from_a_claim(client):
    """The provider's own media_type is discarded, which is what keeps
    image/svg+xml out of a row.

    This used to assert the two functions' PARAMETER NAMES via
    inspect.signature, on the reasoning that a type they cannot be told cannot
    be trusted. That is not the claim: a rename would have failed it with no
    behaviour changed, and an optional declared_mime= added later would have
    passed it while contradicting it. So state the claim the way a provider
    can actually make it - a data: URL whose header LIES about the payload -
    and read back what got stored.
    """
    import generated_images

    msg = _assistant_row(client)
    buf = io.BytesIO()
    Image.new("RGB", (4, 4)).save(buf, format="JPEG")
    lying_url = ("data:image/png;base64,"
                 + base64.b64encode(buf.getvalue()).decode("ascii"))

    raw = generated_images.decode_data_url(lying_url)
    with database.get_db() as con:
        con.execute("BEGIN IMMEDIATE")
        out = _store(con, raw, msg)

    assert out["mime"] == "image/jpeg", "the header's claim was believed"


def test_metadata_does_not_survive(client):
    """A generated image can carry text chunks too, and the same privacy rule
    applies: nothing rides along."""
    from PIL import PngImagePlugin

    msg = _assistant_row(client)
    meta = PngImagePlugin.PngInfo()
    meta.add_text("prompt", "a secret the user never typed")
    buf = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buf, format="PNG", pnginfo=meta)
    raw = buf.getvalue()
    assert b"a secret the user never typed" in raw

    with database.get_db() as con:
        con.execute("BEGIN IMMEDIATE")
        out = _store(con, raw, msg)
    with database.get_db() as con:
        stored = con.execute(
            "SELECT data FROM attachment_blobs WHERE sha256 = ("
            "  SELECT sha256 FROM attachments WHERE id = ?)", (out["id"],),
        ).fetchone()["data"]
    assert b"a secret the user never typed" not in stored


def test_an_oversized_image_is_downscaled(client):
    msg = _assistant_row(client)
    with database.get_db() as con:
        con.execute("BEGIN IMMEDIATE")
        out = _store(con, _png(size=(3000, 1500)), msg)
    assert max(out["width"], out["height"]) == 2048


def test_a_palette_png_keeps_its_transparency(client):
    """The scar at attachments_service.py's transparency carve-out: wiping
    img.info made every palette PNG opaque. Both writers must keep the fix."""
    msg = _assistant_row(client)
    img = Image.new("P", (8, 8))
    buf = io.BytesIO()
    img.save(buf, format="PNG", transparency=0)
    with database.get_db() as con:
        con.execute("BEGIN IMMEDIATE")
        out = _store(con, buf.getvalue(), msg)
    with database.get_db() as con:
        stored = con.execute(
            "SELECT data FROM attachment_blobs WHERE sha256 = ("
            "  SELECT sha256 FROM attachments WHERE id = ?)", (out["id"],),
        ).fetchone()["data"]
    assert Image.open(io.BytesIO(stored)).info.get("transparency") is not None


# ── the count cap, which existed in exactly one place before ────────────────

def test_the_per_message_cap_applies_to_generated_images_too(client):
    msg = _assistant_row(client)
    with database.get_db() as con:
        con.execute("BEGIN IMMEDIATE")
        for i in range(MAX_ATTACHMENTS_PER_MESSAGE):
            _store(con, _png(colour=(i, i, i)), msg)
        with pytest.raises(svc.AttachmentError) as exc:
            _store(con, _png(colour=(99, 99, 99)), msg)
    assert exc.value.reason == "too_many_attachments"


# test_the_cap_counts_images_already_on_that_message lived here: store one
# image inside a transaction, count 1. The cap test above can only reach its
# raise by counting rows an earlier statement in the SAME open transaction
# wrote, so it fails first on anything this could have caught - and it fails
# at the boundary rather than one row in.


# ── and the upload path is untouched ────────────────────────────────────────

def test_uploads_still_stage_with_a_null_message_id(db):
    out = svc.save_upload(_png(), "image/png")
    with database.get_db() as con:
        row = con.execute("SELECT message_id FROM attachments WHERE id = ?",
                          (out["id"],)).fetchone()
    assert row["message_id"] is None


def test_uploads_still_reject_a_bomb(db):
    with pytest.raises(svc.AttachmentError):
        svc.save_upload(_png(size=(20000, 20000)), "image/png")
