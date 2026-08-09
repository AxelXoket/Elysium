"""Who deletes a picture the model drew, and when.

The audit's worry was that nothing on any rollback path deletes an
assistant-linked attachment. The answer turns out to be structural rather than a
missing branch: a generated image is written ONLY inside the finalize
transaction, so there is no window in which one exists without the reply that
owns it. If finalize never runs, nothing was written; if it runs, the row is
real and the ordinary delete paths own it - and those are role-blind already.

That makes this file mostly proof rather than repair, which is the right outcome
to have arrived at. The one genuine decision recorded here is what a
DEACTIVATED regenerate variant does with its picture: it keeps it.
"""
from __future__ import annotations

import base64
import io
import json

import pytest
from PIL import Image

import database
import generated_images
import routers.completions as completions
from tests.conftest import get_messages, make_character, make_chat

BODY = {"message": "draw me something", "model_id": "test/model-1"}


def _url(colour=(7, 8, 9)) -> str:
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), colour).save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _enable() -> None:
    generated_images.set_image_output_enabled(True)


def _counts() -> tuple[int, int]:
    with database.get_db() as con:
        return (
            con.execute("SELECT COUNT(*) c FROM attachments").fetchone()["c"],
            con.execute("SELECT COUNT(*) c FROM attachment_blobs").fetchone()["c"],
        )


def _complete_with(monkeypatch, url: str, content="here") -> None:
    async def _c(messages, model_id, gen_params, provider, **kwargs):
        return {"choices": [{"message": {
            "content": content, "images": [{"image_url": {"url": url}}],
        }}]}

    monkeypatch.setattr(completions, "complete", _c)


def _stream_with(monkeypatch, urls, deltas=("hi",)):
    def _s(messages, model_id, gen_params, provider, modalities=None,
           on_image=None, **kwargs):
        async def gen():
            for d in deltas:
                yield d
            if on_image is not None:
                for u in urls:
                    on_image(u)

        return gen()

    monkeypatch.setattr(completions, "complete_stream", _s)


def _drain(resp) -> list[dict]:
    out = []
    for line in resp.iter_lines():
        line = line.strip()
        if line.startswith("data:"):
            out.append(json.loads(line[len("data:"):]))
    return out


def _chat_with_image(client, monkeypatch) -> int:
    _enable()
    _complete_with(monkeypatch, _url())
    chat = make_chat(client, make_character(client, first_mes="Hi."))
    assert client.post(f"/api/v1/chats/{chat}/complete",
                       json=BODY).status_code == 200
    assert _counts() == (1, 1)
    return chat


# ── the ordinary delete paths already own it ────────────────────────────────

def test_deleting_the_chat_removes_the_picture_and_its_bytes(client, monkeypatch):
    chat = _chat_with_image(client, monkeypatch)
    assert client.delete(f"/api/v1/chats/{chat}").status_code == 200
    assert _counts() == (0, 0)


def test_clearing_the_chat_removes_it_too(client, monkeypatch):
    chat = _chat_with_image(client, monkeypatch)
    assert client.post(f"/api/v1/chats/{chat}/clear").status_code == 200
    assert _counts() == (0, 0)


def test_deleting_the_message_removes_it(client, monkeypatch):
    chat = _chat_with_image(client, monkeypatch)
    msgs = get_messages(client, chat)
    target = [m for m in msgs if m["attachments"]][0]
    assert client.delete(
        f"/api/v1/chats/{chat}/messages/{target['id']}").status_code == 200
    assert _counts() == (0, 0)


def test_deleting_the_turn_before_it_takes_it_along(client, monkeypatch):
    """Delete sweeps forward, so removing the user's message removes the reply
    and therefore the picture."""
    chat = _chat_with_image(client, monkeypatch)
    user_msg = [m for m in get_messages(client, chat) if m["role"] == "user"][0]
    client.delete(f"/api/v1/chats/{chat}/messages/{user_msg['id']}")
    assert _counts() == (0, 0)


def test_two_replies_sharing_one_picture_keep_the_blob_until_both_go(client, monkeypatch):
    """Content-addressed storage means a repeated picture is one blob. Deleting
    one owner must not blind the other."""
    _enable()
    _complete_with(monkeypatch, _url())
    chat_a = make_chat(client, make_character(client, name="A", first_mes="Hi."))
    chat_b = make_chat(client, make_character(client, name="B", first_mes="Hi."))
    client.post(f"/api/v1/chats/{chat_a}/complete", json=BODY)
    client.post(f"/api/v1/chats/{chat_b}/complete", json=BODY)
    assert _counts() == (2, 1)

    client.delete(f"/api/v1/chats/{chat_a}")
    assert _counts() == (1, 1), "the surviving reply lost its bytes"
    client.delete(f"/api/v1/chats/{chat_b}")
    assert _counts() == (0, 0)


# ── nothing is written before finalize, so an abort leaves nothing ──────────

def test_a_stream_that_never_finalizes_stores_no_picture(client, monkeypatch):
    """The provider fails after the image arrived but before the reply is
    committed. There is no half state to clean up because there was never a
    half state: the write happens once, inside finalize."""
    from openrouter import OpenRouterError

    _enable()

    def _s(messages, model_id, gen_params, provider, modalities=None,
           on_image=None, **kwargs):
        async def gen():
            yield "starting"
            if on_image is not None:
                on_image(_url())
            raise OpenRouterError("openrouter_rate_limited")

        return gen()

    monkeypatch.setattr(completions, "complete_stream", _s)
    chat = make_chat(client, make_character(client, first_mes="Hi."))
    with client.stream("POST", f"/api/v1/chats/{chat}/complete/stream",
                       json=BODY) as resp:
        events = _drain(resp)

    assert [e["type"] for e in events][-1] == "error"
    assert _counts() == (0, 0)


def test_a_kept_partial_carries_no_picture(client, monkeypatch):
    """Partial TEXT is salvaged on a mid-stream failure - that is deliberate and
    documented. A picture is not: it is all-or-nothing by design, so a reply
    that survives as a partial survives without it."""
    from openrouter import OpenRouterError

    _enable()

    def _s(messages, model_id, gen_params, provider, modalities=None,
           on_image=None, **kwargs):
        async def gen():
            yield "the text the reader already saw"
            if on_image is not None:
                on_image(_url())
            raise OpenRouterError("openrouter_rate_limited")

        return gen()

    monkeypatch.setattr(completions, "complete_stream", _s)
    chat = make_chat(client, make_character(client, first_mes="Hi."))
    with client.stream("POST", f"/api/v1/chats/{chat}/complete/stream",
                       json=BODY) as resp:
        _drain(resp)

    kept = [m for m in get_messages(client, chat) if m["role"] == "assistant"]
    assert any("already saw" in m["content"] for m in kept)
    assert all(m["attachments"] == [] for m in kept)
    assert _counts() == (0, 0)


def test_no_staged_row_is_ever_created_for_a_generated_picture(client, monkeypatch):
    """A staged (message_id IS NULL) generated row would sit in the pool
    validate_staged hands to any later user send, until the 24h purge."""
    _chat_with_image(client, monkeypatch)
    with database.get_db() as con:
        staged = con.execute(
            "SELECT COUNT(*) c FROM attachments WHERE message_id IS NULL",
        ).fetchone()["c"]
    assert staged == 0


def test_a_generated_id_cannot_be_attached_to_a_user_message(client, monkeypatch):
    """Even knowing the id, the client cannot adopt it: link_attachments only
    claims rows that are still unclaimed."""
    chat = _chat_with_image(client, monkeypatch)
    with database.get_db() as con:
        att_id = con.execute("SELECT id FROM attachments").fetchone()["id"]

    resp = client.post(f"/api/v1/chats/{chat}/complete",
                       json={**BODY, "attachments": [att_id]})
    # The existing guard already covers this, and its code is exactly right:
    # the row belongs to another message.
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "attachment_unavailable"


# ── the variant decision, written down ─────────────────────────────────────

def test_a_deactivated_variant_keeps_its_picture(client, monkeypatch):
    """Regenerate never deletes: old variants stay navigable, and a variant
    whose picture had been reclaimed would render a broken thumbnail forever.
    Content-addressing keeps the cost of a repeated picture at one blob.
    """
    chat = _chat_with_image(client, monkeypatch)
    asst = [m for m in get_messages(client, chat) if m["role"] == "assistant"][-1]

    _complete_with(monkeypatch, _url(colour=(90, 90, 90)), content="another take")
    resp = client.post(f"/api/v1/chats/{chat}/messages/{asst['id']}/regenerate",
                       json=BODY)
    assert resp.status_code == 200, resp.text

    rows, blobs = _counts()
    assert rows == 2, "the deactivated variant lost its picture"
    assert blobs == 2

    both = [m for m in get_messages(client, chat) if m["role"] == "assistant"]
    with_images = [m for m in both if m["attachments"]]
    assert len(with_images) == 2


def test_deleting_a_variant_group_takes_every_variants_picture(client, monkeypatch):
    """The counterweight to keeping them: one delete reclaims all of it."""
    chat = _chat_with_image(client, monkeypatch)
    asst = [m for m in get_messages(client, chat) if m["role"] == "assistant"][-1]
    _complete_with(monkeypatch, _url(colour=(90, 90, 90)), content="another take")
    client.post(f"/api/v1/chats/{chat}/messages/{asst['id']}/regenerate", json=BODY)
    assert _counts() == (2, 2)

    target = [m for m in get_messages(client, chat)
              if m["role"] == "assistant" and m["attachments"]][-1]
    assert client.delete(
        f"/api/v1/chats/{chat}/messages/{target['id']}").status_code == 200
    assert _counts() == (0, 0)


def test_activating_a_variant_returns_that_rows_picture(client, monkeypatch):
    """The client merges this response over its cached message and deliberately
    does NOT refetch afterwards, so an empty attachments array here would
    overwrite the cache and the picture would vanish permanently."""
    chat = _chat_with_image(client, monkeypatch)
    asst = [m for m in get_messages(client, chat) if m["role"] == "assistant"][-1]
    _complete_with(monkeypatch, _url(colour=(200, 10, 10)), content="take two")
    client.post(f"/api/v1/chats/{chat}/messages/{asst['id']}/regenerate", json=BODY)

    variants = [m for m in get_messages(client, chat)
                if m["role"] == "assistant" and m["variant_count"] > 1]
    assert len(variants) == 2
    for v in variants:
        resp = client.post(
            f"/api/v1/chats/{chat}/messages/{v['id']}/activate", json={},
        )
        assert resp.status_code == 200, resp.text
        got = resp.json()["message"]
        assert len(got["attachments"]) == 1, got
        assert got["attachments"][0]["mime"] == "image/png"


def test_an_edit_sweeps_the_replaced_replys_picture(client, monkeypatch):
    """_finalize_edit already deletes the swept tail through the same helper, so
    an edited turn does not leave its old picture behind."""
    chat = _chat_with_image(client, monkeypatch)
    user_msg = [m for m in get_messages(client, chat) if m["role"] == "user"][0]

    _complete_with(monkeypatch, _url(colour=(1, 250, 1)), content="new answer")
    resp = client.post(
        f"/api/v1/chats/{chat}/messages/{user_msg['id']}/edit",
        json={"message": "actually draw something else",
              "model_id": "test/model-1"},
    )
    assert resp.status_code == 200, resp.text

    rows, blobs = _counts()
    assert rows == 1, "the replaced reply's picture survived the edit"
    assert blobs == 1
