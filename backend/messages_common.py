"""Shared message-row helpers (v1.1 FB6).

`_msg_to_dict` existed as two byte-identical copies (routers/chats.py and
routers/completions.py) and the last-active-group lookup as four; the edit
endpoints would have inherited a fifth of each. One module, one truth - the
API response shape and the group-guard semantics can no longer drift apart.
"""

from attachments_service import to_api as attachment_to_api


def msg_to_dict(
    row,
    attachments: list[dict] | None = None,
    variant_index: int | None = None,
    variant_count: int | None = None,
    *,
    card_authored: bool = False,
) -> dict:
    """Convert a message DB row to the API response shape.

    variant_group/active are read defensively (older SELECTs may not include
    them); variant_index/variant_count are attached only when the caller
    computed them - the frontend schema defaults the rest.

    `card_authored` marks a row whose text a person wrote on the character card
    rather than a model producing it, and only the caller can know that. It
    defaults to False so a caller that never heard of it keeps today's
    behaviour: forgetting it cannot invent a NEW way to leak raw tags, it can
    only fail to grant an exemption.
    """
    keys = row.keys() if hasattr(row, "keys") else []
    # V4 (audit-2 corrected): delivery tags are stored RAW so re-speak and
    # regenerate keep them, and hidden at this door - but ONLY on assistant
    # rows, and only once voice has ever been enabled. User text is never
    # model-tagged; stripping it would silently corrupt "[sic]"-style writing
    # in display AND let the edit round-trip persist the corruption. A vault
    # where voice was never on strips nothing at all.
    from voice_tags import strip_for_display

    d = {
        "id":         row["id"],
        "chat_id":    row["chat_id"],
        "role":       row["role"],
        "content":    strip_for_display(row["content"], row["role"],
                                        card_authored=card_authored),
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


def last_active_anchor(con, chat_id: int) -> int | None:
    """Group key (COALESCE(variant_group, id)) of the chat's last ACTIVE row.

    None when the chat has no active messages. This is THE guard query for
    regenerate/edit swaps: run it inside the same BEGIN IMMEDIATE transaction
    as the mutation, or the check is a TOCTOU hole.
    """
    row = con.execute(
        "SELECT id, variant_group FROM messages "
        "WHERE chat_id = ? AND active = 1 ORDER BY id DESC LIMIT 1",
        (chat_id,),
    ).fetchone()
    if row is None:
        return None
    return row["variant_group"] or row["id"]
