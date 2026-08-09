"""generated_images.py - turning a provider's picture into vault bytes.

One module for the whole seam, so an audit has one place to look: the setting
that gates the feature, and the single function that decides whether a URL the
provider sent is something this app is allowed to read.

Privacy rules this module exists to hold:
  - The ONLY acceptable form is an inline `data:` URL. An https:// URL is a
    second egress host, and this app has exactly one. It is refused, not
    fetched, and the refusal is reported rather than swallowed.
  - The provider's declared media type is used only as a cheap early filter.
    The mime that gets stored is re-derived from the bytes Pillow decoded
    (attachments_service._normalise_image), so nothing the far end CLAIMS can
    put `image/svg+xml` into a row.
  - Base64 never leaves this module. Callers get bytes.
"""
from __future__ import annotations

import base64
import binascii
import logging

from config import MAX_UPLOAD_BYTES

logger = logging.getLogger(__name__)

#: Vault setting. Absent means off, which is what a fresh install and every
#: existing vault both read as.
SETTING_IMAGE_OUTPUT = "image_output_enabled"

#: Reported through the existing `notice` channel rather than as errors: a reply
#: whose text arrived is a reply, and losing its picture must not lose its words.
NOTICE_IMAGE_REMOTE_URL = "image_output_remote_url_refused"
NOTICE_IMAGE_REJECTED = "image_output_rejected"

#: Longest base64 payload worth decoding. Base64 inflates by 4/3, and the
#: decoded bytes are checked against MAX_UPLOAD_BYTES again inside
#: _normalise_image - but that check happens AFTER decoding, and decoding is
#: where the memory goes. Nothing else bounds a provider's response size, so
#: this is the first ceiling the bytes meet.
_MAX_B64_CHARS = (MAX_UPLOAD_BYTES * 4) // 3 + 1024


def image_output_enabled() -> bool:
    """Is the model allowed to answer with a picture? Off unless asked for.

    Deliberately fails to False on any read problem, including a locked vault.
    The failure mode of a wrong True is a request the user did not ask for, sent
    to a provider, and billed. The failure mode of a wrong False is a missing
    feature that comes back next launch.
    """
    try:
        from database import get_setting

        return (get_setting(SETTING_IMAGE_OUTPUT) or "").strip() in ("1", "true")
    except Exception:                                    # noqa: BLE001
        logger.debug("could not read the image-output setting", exc_info=True)
        return False


def set_image_output_enabled(enabled: bool) -> None:
    from database import set_setting

    set_setting(SETTING_IMAGE_OUTPUT, "1" if enabled else "0")


class RemoteImageURL(Exception):
    """The provider sent a link instead of the picture.

    Its own category because the answer is different in kind: not "these bytes
    are bad" but "we are not allowed to go and get them". Fetching it would add
    an egress host that this app's entire privacy contract is built on not
    having, and the asset would be sitting on somebody else's server for as long
    as they choose to keep it.
    """


def decode_data_url(url: str) -> bytes:
    """Bytes from a `data:` image URL, or raise.

    Raises RemoteImageURL for anything that is not a data: URL, and ValueError
    for a data: URL this app will not decode. Fails closed in both cases: no
    fetch, no guess, no partial.
    """
    if not isinstance(url, str) or not url:
        raise ValueError("empty image url")
    if not url.startswith("data:"):
        # Includes https://, http://, blob: and anything else. One rule.
        raise RemoteImageURL(url.split(":", 1)[0][:16])

    head, _, payload = url[len("data:"):].partition(",")
    if not payload:
        raise ValueError("data url has no payload")
    # `;base64` must be present. A percent-encoded text data: URL cannot be an
    # image we would store, and guessing is how a decoder gets surprised.
    bits = [p.strip().lower() for p in head.split(";")]
    if "base64" not in bits:
        raise ValueError("data url is not base64")
    media_type = bits[0] if bits and bits[0] else ""
    if media_type and not media_type.startswith("image/"):
        # Cheap early filter only. The stored mime is re-derived from the
        # decoded bytes, so this is about not wasting a decode, not about trust.
        raise ValueError(f"data url is not an image: {media_type[:32]}")

    if len(payload) > _MAX_B64_CHARS:
        raise ValueError(f"data url too large: {len(payload)} b64 chars")

    try:
        # validate=True so stray characters are an error rather than being
        # silently discarded into bytes that then fail to decode as an image
        # for a reason nobody can read in the log.
        return base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"undecodable base64: {exc}") from None
