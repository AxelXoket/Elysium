"""A picture the model drew is shown to the reader and never sent back to it.

Nothing in the schema can enforce that: `attachments` has no role column,
`load_for_messages` never mentions role, and `_content_for` had no role
parameter - its docstring asserted "user messages only" as if that were a rule
rather than a comment. So the moment an assistant row owned an attachment, the
very next turn would have shipped
`{"role":"assistant","content":[text, image_url]}`.

Two costs, one silent. A provider that REJECTS that answers 400, which this app
maps to a 502 one turn late with the body unlogged - nothing in the logs would
say "images". A provider that ACCEPTS it charges for re-uploading the same
picture every turn forever, and `_entry_chars` charges 3300 budget chars for it,
so the trim starts evicting real history to make room for bytes nobody asked to
resend.

This gate must exist BEFORE anything can write an attachment onto an assistant
row, which is why it lands on its own.
"""
from __future__ import annotations

import database
import routers.completions as completions
from tests.conftest import get_messages, make_character, make_chat

BODY = {"message": "and what did that look like?", "model_id": "test/model-1"}


def _fake_row(sha: str = "a" * 64) -> dict:
    return {"id": 1, "sha256": sha, "mime": "image/png", "width": 8, "height": 8,
            "byte_size": 64}


def _blobs(sha: str = "a" * 64) -> dict:
    return {sha: b"\x89PNG\r\n\x1a\n" + b"\x00" * 32}


# ── _content_for, the door itself ────────────────────────────────────────────

def test_a_user_row_still_gets_its_image_parts():
    out = completions._content_for(
        "look at this", [_fake_row()], True, _blobs(), None, role="user",
    )
    assert isinstance(out, list)
    assert any(p.get("type") == "image_url" for p in out)


def test_an_assistant_row_never_gets_image_parts():
    out = completions._content_for(
        "here is what I drew", [_fake_row()], True, _blobs(), None,
        role="assistant",
    )
    assert out == "here is what I drew"


# test_a_system_row_never_gets_image_parts lived here. _IMAGE_REPLAY_ROLES is
# a one-element frozenset, so "system" and "assistant" take the identical
# branch - and a system row cannot carry an attachment in the first place.
# The assistant case is the one this whole file exists for, and it is above.


def test_the_default_role_is_the_permissive_one_so_existing_callers_are_unchanged():
    """Every pre-existing call site passed no role and meant the user's turn."""
    out = completions._content_for("hi", [_fake_row()], True, _blobs())
    assert isinstance(out, list)


# ── budget accounting has to agree with the door ─────────────────────────────

def test_an_assistant_image_costs_no_budget():
    """Charging for it would make the trim evict real history to reserve room
    for bytes the gate is about to drop."""
    assert completions._entry_chars("hello", [_fake_row()], True,
                                   role="assistant") == len("hello")


def test_a_user_image_still_costs_its_flat_estimate():
    charged = completions._entry_chars("hello", [_fake_row()], True, role="user")
    assert charged == len("hello") + completions._IMAGE_CHAR_COST


# ── end to end through the real assembly ────────────────────────────────────

def test_the_payload_carries_no_assistant_image_part(client, provider):
    """Build the exact situation the feature creates: an assistant row that owns
    an image, then another turn."""
    char = make_character(client, first_mes="Hi.")
    chat = make_chat(client, char)

    # Seed a real generated image onto the greeting (an assistant row).
    import io

    from PIL import Image

    import attachments_service as svc

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (1, 2, 3)).save(buf, format="PNG")
    with database.get_db() as con:
        asst_id = con.execute(
            "SELECT id FROM messages WHERE chat_id = ? ORDER BY id ASC LIMIT 1",
            (chat,),
        ).fetchone()["id"]
        con.execute("BEGIN IMMEDIATE")
        svc.store_generated_image(
            con, svc.normalise_image(buf.getvalue()), asst_id,
        )

    # It is visible to the reader.
    body = get_messages(client, chat)
    assert len(body[0]["attachments"]) == 1

    # And absent from the next request.
    resp = client.post(f"/api/v1/chats/{chat}/complete", json=BODY)
    assert resp.status_code == 200, resp.text
    sent = provider.calls[0]["messages"]
    # Floor. Both assertions below iterate the assistant entries, so a change
    # that dropped the assistant turn from history ALTOGETHER - a worse bug
    # than the one this file guards - would satisfy an empty loop and an
    # any() over nothing. The image must be gone because it was stripped,
    # not because the turn carrying it stopped being sent.
    assert any(e["role"] == "assistant" for e in sent), sent
    for entry in sent:
        if entry["role"] != "user":
            assert isinstance(entry["content"], str), entry
    assert not any(
        isinstance(e["content"], list)
        and any(p.get("type") == "image_url" for p in e["content"])
        for e in sent if e["role"] == "assistant"
    )


def test_a_users_own_attached_image_is_still_replayed(client, provider):
    """The gate must be narrow. Break this and vision chat stops working."""
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (9, 9, 9)).save(buf, format="PNG")
    up = client.post("/api/v1/uploads/images",
                     files={"file": ("a.png", buf.getvalue(), "image/png")})
    assert up.status_code == 201, up.text

    char = make_character(client, first_mes="Hi.")
    chat = make_chat(client, char)
    resp = client.post(f"/api/v1/chats/{chat}/complete",
                       json={**BODY, "attachments": [up.json()["id"]]})
    assert resp.status_code == 200, resp.text

    sent = provider.calls[0]["messages"]
    user_entries = [e for e in sent if e["role"] == "user"]
    assert any(isinstance(e["content"], list) for e in user_entries)


# ── the output-modality predicate ───────────────────────────────────────────

def test_a_model_that_declares_image_output_may_be_asked():
    assert completions._model_emits_images(
        {"output_modalities": ["text", "image"]}) is True


def test_a_text_only_model_may_not():
    assert completions._model_emits_images({"output_modalities": ["text"]}) is False


def test_unknown_metadata_is_permissive_like_its_sibling():
    """Same rule as _model_accepts_images: after any invalidate_model_cache the
    metadata is None, and reading that as "no" would silently switch the whole
    feature off until the catalogue was refetched."""
    assert completions._model_emits_images(None) is True
    assert completions._model_emits_images({}) is True
    assert completions._model_emits_images({"output_modalities": []}) is True


def test_it_reads_output_modalities_not_input():
    """A vision model that cannot draw must not be asked to draw."""
    assert completions._model_emits_images(
        {"input_modalities": ["text", "image"], "output_modalities": ["text"]},
    ) is False
