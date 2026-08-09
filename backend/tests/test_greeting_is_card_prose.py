"""The character's greeting is prose a PERSON wrote, not model output.

`first_mes` is seeded as a real assistant message row (routers/chats.py), which
made it look like model output to the one door that hides delivery tags. And
`_looks_like_tag` accepts any lowercase span of 3-40 chars and up to six words -
a fair description of "[she smiles]", which is how a great many character cards
open. So in every vault where voice had ever been switched on, that greeting
quietly lost words, while the model - which reads the raw row - went on seeing
them. The reader and the model disagreed about the first thing either of them
was shown.

The exemption has to be narrow: the SAME chat's real replies must still be
stripped, or the bug this all exists to prevent comes back as a visible
`[low voice]` in a bubble.
"""
import database
import voice_tags
from tests.conftest import get_messages, make_character, make_chat


def _voice_has_been_on(client) -> None:
    """Through the real endpoint - it writes the sticky flag AND the toggle in
    one transaction, which is what a returning user's vault actually looks
    like."""
    resp = client.post("/api/v1/tts/voice-mode", json={"enabled": True})
    assert resp.status_code == 200, resp.text
    voice_tags.reset_stripping_cache()
    assert voice_tags.stripping_active() is True


# ── the greeting keeps its brackets ──────────────────────────────────────────

def test_a_card_greeting_keeps_its_bracketed_prose(client):
    _voice_has_been_on(client)
    char = make_character(client, first_mes="[she smiles] Hello there, darling.")
    chat = make_chat(client, char)

    body = get_messages(client, chat)
    assert [m["role"] for m in body] == ["assistant"]
    assert body[0]["content"] == "[she smiles] Hello there, darling."


def test_the_greeting_is_untouched_even_when_the_bracket_ends_it(client):
    _voice_has_been_on(client)
    char = make_character(client, first_mes="Come in. [she closes the door]")
    chat = make_chat(client, char)

    assert get_messages(client, chat)[0]["content"] == (
        "Come in. [she closes the door]"
    )


def test_a_greeting_with_no_brackets_is_returned_byte_for_byte(client):
    """The cheap early return must not start rewriting ordinary greetings."""
    _voice_has_been_on(client)
    text = "Seni bekliyordum. Gec kalmadin, iyi."
    char = make_character(client, first_mes=text)
    chat = make_chat(client, char)

    assert get_messages(client, chat)[0]["content"] == text


def test_the_stored_row_was_verbatim_all_along(client):
    """The fix is at the display door, not in storage: the model must keep
    seeing exactly what the card author wrote."""
    _voice_has_been_on(client)
    char = make_character(client, first_mes="[softly] Come here.")
    chat = make_chat(client, char)

    with database.get_db() as con:
        stored = con.execute(
            "SELECT content FROM messages WHERE chat_id = ? ORDER BY id ASC "
            "LIMIT 1", (chat,),
        ).fetchone()["content"]
    assert stored == "[softly] Come here."


# ── and the exemption stops there ────────────────────────────────────────────

def test_a_real_reply_in_the_same_chat_is_still_stripped(client, provider):
    """The narrowness IS the feature. One row is exempt; the next is not."""
    _voice_has_been_on(client)
    char = make_character(client, first_mes="[she smiles] Hello there.")
    chat = make_chat(client, char)

    provider.response_text = "[low voice] I missed you."
    resp = client.post(f"/api/v1/chats/{chat}/complete",
                       json={"message": "hi", "model_id": "test/model-1"})
    assert resp.status_code == 200, resp.text

    body = get_messages(client, chat)
    assert body[0]["content"] == "[she smiles] Hello there."     # greeting
    assert body[-1]["content"] == "I missed you."                # model reply
    assert body[-1]["role"] == "assistant"


def test_a_reply_is_stripped_when_the_card_had_no_greeting(client, provider):
    """The oldest row of THIS chat is the user's own message, so the assistant
    reply that follows must not inherit the exemption by being second."""
    _voice_has_been_on(client)
    char = make_character(client, first_mes="")
    chat = make_chat(client, char)

    provider.response_text = "[whisper] Stay close."
    resp = client.post(f"/api/v1/chats/{chat}/complete",
                       json={"message": "hi", "model_id": "test/model-1"})
    assert resp.status_code == 200, resp.text

    body = get_messages(client, chat)
    assert [m["role"] for m in body] == ["user", "assistant"]
    assert body[-1]["content"] == "Stay close."


def test_clearing_the_chat_does_not_hand_the_exemption_to_a_reply(client, provider):
    """`/clear` deletes the greeting and does NOT re-seed it, so after a clear
    the chat's oldest row is a user row again."""
    _voice_has_been_on(client)
    char = make_character(client, first_mes="[she smiles] Hello there.")
    chat = make_chat(client, char)
    assert client.post(f"/api/v1/chats/{chat}/clear").status_code == 200

    provider.response_text = "[soft] Back again."
    resp = client.post(f"/api/v1/chats/{chat}/complete",
                       json={"message": "hi", "model_id": "test/model-1"})
    assert resp.status_code == 200, resp.text

    body = get_messages(client, chat)
    assert [m["role"] for m in body] == ["user", "assistant"]
    assert body[-1]["content"] == "Back again."


def test_a_users_own_brackets_are_still_never_touched(client, provider):
    """The pre-existing user-row exemption, re-asserted here because this fix
    rests on the same argument and must not have narrowed it."""
    _voice_has_been_on(client)
    char = make_character(client, first_mes="Hi.")
    chat = make_chat(client, char)

    resp = client.post(f"/api/v1/chats/{chat}/complete",
                       json={"message": "he wrote [sic] and meant it",
                             "model_id": "test/model-1"})
    assert resp.status_code == 200, resp.text

    body = get_messages(client, chat)
    user_rows = [m for m in body if m["role"] == "user"]
    assert user_rows[-1]["content"] == "he wrote [sic] and meant it"


# ── a vault that never turned voice on is unaffected either way ──────────────

def test_nothing_is_stripped_at_all_before_voice_is_ever_enabled(client, provider):
    char = make_character(client, first_mes="[she smiles] Hello there.")
    chat = make_chat(client, char)

    provider.response_text = "[low voice] I missed you."
    resp = client.post(f"/api/v1/chats/{chat}/complete",
                       json={"message": "hi", "model_id": "test/model-1"})
    assert resp.status_code == 200, resp.text

    body = get_messages(client, chat)
    assert body[0]["content"] == "[she smiles] Hello there."
    assert body[-1]["content"] == "[low voice] I missed you."


# ── the seam itself ─────────────────────────────────────────────────────────

def test_the_exemption_is_opt_in_so_forgetting_it_cannot_leak_a_tag(client):
    """A caller that has never heard of `card_authored` must behave exactly as
    it did before: the default grants nothing."""
    _voice_has_been_on(client)
    assert voice_tags.strip_for_display("[soft] hey", "assistant") == "hey"
    assert voice_tags.strip_for_display(
        "[soft] hey", "assistant", card_authored=True) == "[soft] hey"
