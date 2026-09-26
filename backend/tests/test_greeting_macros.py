"""A card greeting has its macros filled in once, when the chat is created.

Decision B2 (a): the greeting is rendered with the persona active at
creation and stored RENDERED, so the screen, TTS, the notebook extractor and
the prompt all read one string. And the rule that goes with it (B1, E6): what
the person types is never touched, even when it looks like a macro.
"""
from __future__ import annotations

import database
from card_macros import DEFAULT_USER_NAME, FIELD_MAX_CODE_POINTS, FIELD_TOO_LARGE_CODE
from tests.conftest import get_messages, make_character, make_chat, make_persona


def _stored_greeting(chat_id: int) -> str:
    with database.get_db() as con:
        return con.execute(
            "SELECT content FROM messages WHERE chat_id = ? ORDER BY id ASC LIMIT 1",
            (chat_id,),
        ).fetchone()["content"]


def _chat_count() -> int:
    with database.get_db() as con:
        return con.execute("SELECT COUNT(*) AS n FROM chats").fetchone()["n"]


# -- G01/G02: which name {{user}} becomes --------------------------------------

def test_the_selected_persona_name_fills_the_greeting(client):
    make_persona(client, display_name="Bob", select=True)
    char = make_character(client, name="Mira", first_mes="Hi {{user}}, I am {{char}}. <USER>? <BOT>.")
    chat = make_chat(client, char)
    assert _stored_greeting(chat) == "Hi Bob, I am Mira. Bob? Mira."


def test_no_selected_persona_uses_the_default_name(client):
    """Ground control: a persona that EXISTS but is not selected is not the
    active persona, so the default name is used."""
    make_persona(client, display_name="Bob", select=False)
    char = make_character(client, name="Mira", first_mes="Hi {{user}}.")
    chat = make_chat(client, char)
    assert _stored_greeting(chat) == f"Hi {DEFAULT_USER_NAME}."


def test_a_broken_persona_setting_opens_the_chat_with_the_default_name(client):
    char = make_character(client, name="Mira", first_mes="Hi {{user}}.")
    for bad in ("not-a-number", "999999"):
        database.set_setting("selected_persona_id", bad)
        chat = make_chat(client, char)
        assert _stored_greeting(chat) == f"Hi {DEFAULT_USER_NAME}."


# -- G03: one string for every reader -------------------------------------------

def test_what_is_shown_is_what_is_stored(client):
    make_persona(client, display_name="Bob", select=True)
    char = make_character(client, name="Mira", first_mes="Welcome, {{user}}.")
    chat = make_chat(client, char)
    shown = get_messages(client, chat)
    assert [m["role"] for m in shown] == ["assistant"]
    assert shown[0]["content"] == _stored_greeting(chat) == "Welcome, Bob."


def test_a_later_persona_change_does_not_rewrite_the_greeting(client):
    make_persona(client, display_name="Bob", select=True)
    char = make_character(client, name="Mira", first_mes="Hi {{user}}.")
    chat = make_chat(client, char)
    make_persona(client, display_name="Eve", select=True)
    assert _stored_greeting(chat) == "Hi Bob."
    # Positive control: a NEW chat does see the new persona.
    assert _stored_greeting(make_chat(client, char)) == "Hi Eve."


# -- G04: the card itself stays raw ---------------------------------------------

def test_the_card_keeps_its_raw_text(client):
    make_persona(client, display_name="Bob", select=True)
    char = make_character(client, name="Mira", first_mes="Hi {{user}}.")
    make_chat(client, char)
    card = client.get(f"/api/v1/characters/{char}").json()
    assert card["first_mes"] == "Hi {{user}}."


# -- edges ----------------------------------------------------------------------

def test_a_greeting_without_macros_is_stored_byte_for_byte(client):
    """Ground control for the render step."""
    greeting = "[she smiles] $& \\1 <b>{{unknown}}</b>"
    make_persona(client, display_name="Bob", select=True)
    chat = make_chat(client, make_character(client, first_mes=greeting))
    assert _stored_greeting(chat) == greeting


def test_a_greeting_that_is_only_a_comment_adds_no_row(client):
    chat = make_chat(client, make_character(client, first_mes="{{// note for readers}}"))
    assert get_messages(client, chat) == []
    # Positive control: text around the comment survives.
    chat2 = make_chat(client, make_character(client, first_mes="Hi{{// note}} there"))
    assert _stored_greeting(chat2) == "Hi there"


def test_a_persona_name_that_looks_like_a_macro_is_inserted_as_is(client):
    make_persona(client, display_name="{{char}}", select=True)
    chat = make_chat(client, make_character(client, name="Mira", first_mes="Hi {{user}}."))
    assert _stored_greeting(chat) == "Hi {{char}}."


def test_the_stored_greeting_carries_the_character_name_as_written(client):
    """The stored row is what the person reads, so the card's own name goes in
    as written and is not scanned again: a name that looks like a macro stays
    literal. Cutting and marker rules belong to the prompt copy (the
    sanitizer), not to this row. Ground control: a plain name."""
    make_persona(client, display_name="Bob", select=True)
    plain = make_chat(client, make_character(client, name="Mira", first_mes="I am {{char}}."))
    assert _stored_greeting(plain) == "I am Mira."

    odd = make_chat(client, make_character(
        client, name="{{user}} [Mira]", first_mes="I am {{char}}."))
    assert _stored_greeting(odd) == "I am {{user}} [Mira]."


def test_an_oversized_greeting_refuses_the_chat_and_leaves_nothing(client):
    make_persona(client, display_name="Bobby Tables", select=True)
    # 12-character name over 8-character tokens: 1.5 MiB rendered.
    bomb = "{{user}}" * (FIELD_MAX_CODE_POINTS // 8)
    char = make_character(client, first_mes=bomb)
    before = _chat_count()
    resp = client.post("/api/v1/chats", json={"character_id": char})
    assert resp.status_code == 400
    assert resp.json()["detail"] == FIELD_TOO_LARGE_CODE
    assert _chat_count() == before
    # Positive control: the same card with a name no longer than the token fits.
    make_persona(client, display_name="Bob", select=True)
    ok = client.post("/api/v1/chats", json={"character_id": char})
    assert ok.status_code == 201, ok.text
    assert _chat_count() == before + 1


# -- the user's own words are never touched -------------------------------------

def test_a_typed_message_is_never_rendered(client, provider):
    make_persona(client, display_name="Bob", select=True)
    char = make_character(client, name="Mira", first_mes="Hi {{user}}.")
    chat = make_chat(client, char)
    typed = "Say {{char}} and {{user}} and <BOT> {{// keep}} {{trim}}"
    resp = client.post(f"/api/v1/chats/{chat}/complete",
                       json={"message": typed, "model_id": "test/model-1"})
    assert resp.status_code == 200, resp.text

    rows = get_messages(client, chat)
    user_rows = [m for m in rows if m["role"] == "user"]
    assert [m["content"] for m in user_rows] == [typed]
    # What the provider was sent carries the typed text verbatim ...
    sent = provider.calls[-1]["messages"]
    user_sent = [m for m in sent if m["role"] == "user"]
    assert any(
        (m["content"] if isinstance(m["content"], str) else "").startswith(typed)
        for m in user_sent
    ), user_sent
    assert not any("Say Mira" in str(m["content"]) for m in sent)
    # ... while the greeting in the same history was rendered (positive control).
    assert rows[0]["content"] == "Hi Bob."
