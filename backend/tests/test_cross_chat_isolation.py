"""One lens, one file: does an operation on THIS chat stay inside this chat?

Every message-lifecycle test in this suite works inside a single conversation.
That is the natural way to write them and it leaves the same hole in all of
them: a `WHERE` clause that has lost its `chat_id = ?` answers identically when
there is only one chat in the vault. The suite would stay green while the
predicate that separates one conversation from every other one was gone.

The operations audited here do not merely read across the boundary, they
DELETE across it. `_finalize_edit` and `_delete_message_sync` both sweep with
`id > ?` scoped by chat; drop the scope and editing an old turn in one chat
takes out every message written after it anywhere in the vault. Ids are
global, so "written after it" means every later conversation.

So every test below plants a SECOND chat, does the work in the first, and
asserts the second is byte-identical afterwards. The second chat is always
created LAST, which is the arrangement that hurts: its ids are the high ones,
the ones an unscoped `id >` sweep reaches.

Measured 2026-08-10 by removing each `chat_id` predicate in turn, and the
result is not uniform, so it is written down rather than summarised:

- edit sweep and delete sweep, the two that reach the WHOLE VAULT: with this
  file excluded, 2264 tests passed and 0 failed with both predicates gone.
  Nothing anywhere was watching them.
- `last_active_anchor`: caught, by test_stream_body.py - not on purpose, but
  because that file drives three endpoints and gives each one its own chat, so
  it is the only place in the suite where a second conversation exists. It
  fails there as five unrelated-looking assertions about speakers and provider
  failures. The test here fails as one sentence naming the predicate.
- the preceding-user lookup in `_validate_regenerate_target`: not caught, and
  not catchable in the ordinary arrangement either - see the note on that test.
"""

from __future__ import annotations

import pytest

from conftest import make_character, make_chat, get_messages

BODY = {"message": "How are you?", "model_id": "test/model-1"}


def _seed(client, provider, reply: str) -> tuple[int, int, int]:
    """A chat with one full exchange. Returns (chat_id, user_id, asst_id)."""
    provider.response_text = reply
    chat_id = make_chat(client, make_character(client))
    resp = client.post(f"/api/v1/chats/{chat_id}/complete", json=BODY)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return chat_id, body["user_message"]["id"], body["assistant_message"]["id"]


@pytest.fixture()
def two_chats(client, provider):
    """Chat A, then chat B. B's row ids are all higher than A's - deliberately.

    An unscoped `id > <a row in A>` reaches every row in B and nothing in A
    that matters, so this ordering is what turns a missing predicate into
    visible damage rather than a lucky miss.
    """
    a = _seed(client, provider, "reply in the first chat")
    b = _seed(client, provider, "reply in the second chat")
    # Message ids only: chats have their own id space and comparing the two
    # would be meaningless.
    assert min(b[1:]) > max(a[1:]), "chat B must be later for this lens to bite"
    return a, b


def test_editing_a_turn_does_not_sweep_a_later_chat(two_chats, client, monkeypatch):
    """`_finalize_edit` sweeps `id > message_id` to drop the answer it is
    replacing. Scoped to the chat that is being edited, that is exactly the
    rest of this conversation. Unscoped, it is the rest of the vault."""
    import routers.completions as cr

    (chat_a, user_a, _), (chat_b, _, _) = two_chats
    before_b = get_messages(client, chat_b)

    async def fake_complete(messages, model_id, gen_params, provider, **kwargs):
        return {"choices": [{"message": {"content": "A brand new answer."}}]}

    monkeypatch.setattr(cr, "complete", fake_complete)
    resp = client.post(
        f"/api/v1/chats/{chat_a}/messages/{user_a}/edit",
        json={"message": "a different question", "model_id": "test/model-1"},
    )
    assert resp.status_code == 200, resp.text

    assert get_messages(client, chat_b) == before_b, "the later chat was swept"
    # And the edit really did happen, or the assertion above is free.
    assert [m["content"] for m in get_messages(client, chat_a)][1:] == [
        "a different question", "A brand new answer.",
    ]


def test_deleting_a_message_does_not_sweep_a_later_chat(two_chats, client):
    """Delete-and-following has the same shape and the same `id >` sweep."""
    (chat_a, user_a, _), (chat_b, _, _) = two_chats
    before_b = get_messages(client, chat_b)

    resp = client.delete(f"/api/v1/chats/{chat_a}/messages/{user_a}")
    assert resp.status_code in (200, 204), resp.text

    assert get_messages(client, chat_b) == before_b, "the later chat was swept"
    assert not any(m["id"] == user_a for m in get_messages(client, chat_a))


def test_regenerating_is_judged_against_its_own_chats_last_message(
    two_chats, client, provider,
):
    """`last_active_anchor()` decides whether the target is still the last
    reply, and that 409 is the whole guard. Without its `chat_id` predicate it
    answers with the vault's last active row - so a chat that is not the most
    recently used one could never be regenerated at all, and the chat that IS
    would accept a target belonging to somebody else's conversation.

    Chat A is deliberately NOT the last one written to.
    """
    (chat_a, _, asst_a), (chat_b, _, _) = two_chats
    before_b = get_messages(client, chat_b)

    provider.response_text = "A second attempt."
    resp = client.post(
        f"/api/v1/chats/{chat_a}/messages/{asst_a}/regenerate",
        json={"model_id": "test/model-1"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["assistant_message"]["content"] == "A second attempt."
    assert get_messages(client, chat_b) == before_b


def test_a_regenerate_sends_its_own_chats_question_to_the_provider(
    client, provider,
):
    """The preceding user turn is looked up to rebuild the history, and the
    lookup is `role = 'user' AND id < anchor ORDER BY id DESC LIMIT 1`.

    Dropping its chat scope is invisible in the ordinary arrangement, and that
    is worth writing down because it is why the first version of this test was
    useless: nearest-below-the-anchor is normally the chat's OWN question, so
    the unscoped query lands on the right row by luck. Measured 2026-08-10 -
    with the predicate removed and the two chats built back to back, this test
    stayed green.

    The arrangement that bites is interleaving, which this app makes easy: ask
    in chat A, switch to chat B while A is still generating, and A's reply row
    is written after B's whole exchange. Now the nearest user row below A's
    anchor belongs to B, and the reader's question in one conversation would be
    sent to the provider as the question in another. Built here with direct
    inserts, because that ordering is a race the test client cannot schedule.
    """
    import database

    chat_a = make_chat(client, make_character(client))
    chat_b = make_chat(client, make_character(client))
    with database.get_db() as con:
        con.execute(
            "INSERT INTO messages (chat_id, role, content) "
            "VALUES (?, 'user', 'the question asked in chat A')", (chat_a,),
        )
        con.execute(
            "INSERT INTO messages (chat_id, role, content) "
            "VALUES (?, 'user', 'the question asked in chat B')", (chat_b,),
        )
        con.execute(
            "INSERT INTO messages (chat_id, role, content) "
            "VALUES (?, 'assistant', 'the reply in chat B')", (chat_b,),
        )
        asst_a = con.execute(
            "INSERT INTO messages (chat_id, role, content) "
            "VALUES (?, 'assistant', 'the late reply in chat A')", (chat_a,),
        ).lastrowid

    provider.response_text = "Fresh."
    provider.calls.clear()
    resp = client.post(
        f"/api/v1/chats/{chat_a}/messages/{asst_a}/regenerate",
        json={"model_id": "test/model-1"},
    )
    assert resp.status_code == 200, resp.text

    sent = provider.calls[-1]["messages"]
    assert any(
        m["content"] == "the question asked in chat A" for m in sent
    ), sent
    assert not any(
        m["content"] == "the question asked in chat B" for m in sent
    ), "another conversation's question reached the provider"


# -- the sibling predicate: the ATTACHMENT sweep ----------------------------
#
# Everything above audits the query that picks which message ROWS an
# operation destroys. Each of those functions runs a SECOND query first, and
# it is a separate line: the one that collects the ids handed to
# delete_for_messages, which deletes attachment rows and then any blob left
# with no owner.
#
# Scoping one and not the other fails in a way the tests above cannot see.
# The messages of the other chat survive - so every assertion up to here
# passes - while their pictures are deleted out from under them. The rows
# keep pointing at bytes that are gone, and the reader gets a broken image on
# a message the app still shows.
#
# Measured 2026-08-10: with the chat_id predicate removed from the swept-id
# query in _finalize_edit, _delete_message_sync and _clear_chat_sync, the
# whole suite stayed green. Not one test in it pairs a destructive operation
# with a picture in a second conversation.

def _seed_with_picture(client, provider, colour) -> tuple[int, int, int]:
    """A chat whose user turn carries an image. (chat_id, msg_id, att_id)."""
    from tests.test_attachments import make_png, upload

    att = upload(client, make_png(color=colour))
    chat_id = make_chat(client, make_character(client))
    resp = client.post(f"/api/v1/chats/{chat_id}/complete", json={
        **BODY, "attachments": [att["id"]],
    })
    assert resp.status_code == 200, resp.text
    return chat_id, resp.json()["user_message"]["id"], att["id"]


@pytest.fixture()
def _vision(monkeypatch):
    import routers.completions as completions_router

    from tests.test_attachments import VISION_META

    monkeypatch.setattr(completions_router, "get_cached_model_metadata",
                        lambda mid: VISION_META)


def _still_shows(client, att_id: int) -> bool:
    return client.get(f"/api/v1/uploads/images/{att_id}").status_code == 200


@pytest.fixture()
def two_chats_with_pictures(client, provider, _vision):
    """A first, B second, different pictures. B's ids are the high ones."""
    a = _seed_with_picture(client, provider, (10, 10, 200))
    b = _seed_with_picture(client, provider, (200, 10, 10))
    assert b[1] > a[1], "chat B must be later for this lens to bite"
    assert _still_shows(client, a[2]) and _still_shows(client, b[2])
    return a, b


def test_editing_a_turn_does_not_delete_a_later_chats_picture(
    client, provider, two_chats_with_pictures,
):
    a, b = two_chats_with_pictures
    resp = client.post(f"/api/v1/chats/{a[0]}/messages/{a[1]}/edit",
                       json={"message": "different question",
                             "model_id": "test/model-1"})
    assert resp.status_code == 200, resp.text

    assert _still_shows(client, b[2]), (
        "the edit swept an attachment belonging to another conversation")


def test_deleting_a_message_does_not_delete_a_later_chats_picture(
    client, provider, two_chats_with_pictures,
):
    a, b = two_chats_with_pictures
    resp = client.delete(f"/api/v1/chats/{a[0]}/messages/{a[1]}")
    assert resp.status_code == 200, resp.text

    assert _still_shows(client, b[2]), (
        "the delete swept an attachment belonging to another conversation")


def test_clearing_a_chat_does_not_delete_another_chats_picture(
    client, provider, two_chats_with_pictures,
):
    """Clear has no `id >` at all, so an unscoped collect reaches EVERY
    picture in the vault rather than only the later ones. The chat cleared
    here is the EARLIER one, so nothing about ordering can hide the damage.
    """
    a, b = two_chats_with_pictures
    resp = client.post(f"/api/v1/chats/{a[0]}/clear")
    assert resp.status_code == 200, resp.text

    assert _still_shows(client, b[2]), (
        "clearing one chat deleted another chat's attachment bytes")
