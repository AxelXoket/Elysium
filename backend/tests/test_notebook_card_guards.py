"""C1 and C2 (prompt-layer decisions, 26 Sep 2026) at the one place every
model note is written: `commit_extraction`.

C1: a suggestion whose quote is card text, the chat's greeting row, or the
text of a prompt module that is switched on is dropped. It was handed to the
model; the chat did not establish it.

C2: when the card has a system prompt or a final note, a suggestion quoting
the model's own reply waits for review even with automatic acceptance on.
A quote from the user's own message is accepted as before, and a card with
no instruction fields changes nothing.

Every test drives the real function in a real transaction on a real database,
and every drop or hold sits next to a sibling in the same call that is kept,
so an empty result can never pass for a working guard.
"""
from __future__ import annotations

import json
from urllib.parse import quote

import pytest

import notebook_store as notebook
from database import get_db
from prompt_modules.limits import FILENAME_HEADER

from tests.conftest import make_chat, make_persona

MODULES = "/api/v1/prompt-modules"

#: Chat-only text: said in the conversation, in no card, greeting or module.
CHAT_QUOTE = "my sister keeps bees behind the old chapel"
CHAT_QUOTE_2 = "the ferry to Oskar island leaves at dawn"


def card(client, **fields) -> int:
    body = {"name": "Testchar", "description": "A test character",
            "first_mes": "Hello there!"}
    body.update(fields)
    r = client.post("/api/v1/characters", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def chat_for(client, **fields) -> int:
    chat_id = make_chat(client, card(client, **fields))
    with get_db() as con:
        for i, text in enumerate((CHAT_QUOTE, "ok", CHAT_QUOTE_2, "sure")):
            con.execute(
                "INSERT INTO messages (chat_id, role, content) VALUES (?,?,?)",
                (chat_id, "user" if i % 2 == 0 else "assistant", text))
    return chat_id


def fact(evidence: str, role: str = "user", text: str | None = None, **over):
    base = {"text": text or ("Note about " + evidence[:40] + "."),
            "evidence": evidence, "evidence_role": role,
            "kind": "fact", "durability": "permanent",
            "importance": 2, "supersedes": None}
    base.update(over)
    return base


def commit(chat_id, proposals, key="k", existing_ids=None):
    with get_db() as con:
        con.execute("BEGIN IMMEDIATE")
        return notebook.commit_extraction(
            con, work_key=key, chat_id=chat_id, from_id=1, to_id=2,
            proposals=proposals, existing_ids=existing_ids or [])


def evidences(chat_id) -> dict[str, str]:
    """evidence -> status, for every model note in the chat."""
    return {e["evidence"]: e["status"] for e in notebook.list_entries(chat_id)
            if e["provenance"] == notebook.PROV_MODEL}


def upload_module(client, text: str, enabled: bool) -> None:
    r = client.post(f"{MODULES}/import", content=text.encode(),
                    headers={FILENAME_HEADER: quote("m.txt"),
                             "Content-Type": "application/octet-stream"})
    assert r.status_code == 201, r.text
    mid = client.get(MODULES).json()["modules"][-1]["id"]
    if enabled:
        r = client.patch(f"{MODULES}/{mid}", json={"enabled": True})
        assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# C1
# ---------------------------------------------------------------------------

CARD_LINE = "the lighthouse keeper lost his left hand to the sea"


class TestCardTextNeverBecomesANote:
    def test_ground_chat_text_is_kept(self, client) -> None:
        """GROUND. A card with text in every field; a quote from the chat
        alone is still written."""
        chat_id = chat_for(client, description=CARD_LINE)
        out = commit(chat_id, [fact(CHAT_QUOTE)])
        assert out["written"] == 1
        assert CHAT_QUOTE in evidences(chat_id)

    @pytest.mark.parametrize("field", [
        "name", "description", "personality", "scenario", "first_mes",
        "mes_example", "system_prompt", "post_history_instruction"])
    def test_a_quote_from_any_card_field_is_dropped(self, client, field):
        value = CARD_LINE if field != "name" else "Captain Of The Northern Light"
        chat_id = chat_for(client, **{field: value})
        # Same words, other case and other spacing: still card text.
        echo = "  " + value.upper().replace(" ", "   ") + " "
        out = commit(chat_id, [fact(echo, role="assistant"),
                               fact(CHAT_QUOTE)])
        assert out["written"] == 1
        assert list(evidences(chat_id)) == [CHAT_QUOTE]

    def test_the_user_quoting_card_text_is_dropped_too(self, client) -> None:
        """The rule is about where the words live, not who said them."""
        chat_id = chat_for(client, description=CARD_LINE)
        out = commit(chat_id, [fact(CARD_LINE, role="user"),
                               fact(CHAT_QUOTE)])
        assert out["written"] == 1
        assert list(evidences(chat_id)) == [CHAT_QUOTE]

    def test_a_quote_from_an_alternate_greeting_is_dropped(self, client):
        """Alternate greetings live only in the card's raw JSON."""
        chat_id = chat_for(client)
        with get_db() as con:
            con.execute(
                "UPDATE characters SET raw_json = ? WHERE id = "
                "(SELECT character_id FROM chats WHERE id = ?)",
                (json.dumps({"data": {"alternate_greetings": [
                    "x", "The tide brought the drowned bell back at noon"]}}),
                 chat_id))
        out = commit(chat_id, [
            fact("the drowned bell back at noon", role="assistant"),
            fact(CHAT_QUOTE)])
        assert out["written"] == 1
        assert list(evidences(chat_id)) == [CHAT_QUOTE]

    def test_card_macros_are_filled_before_comparing(self, client) -> None:
        """The chat shows `{{char}}` as the name, so that is what a model
        reply repeats."""
        make_persona(client, "Ilka", select=True)
        chat_id = chat_for(
            client, name="Maren",
            description="{{char}} owes {{user}} three silver coins since winter")
        out = commit(chat_id, [
            fact("Maren owes Ilka three silver coins", role="assistant"),
            fact(CHAT_QUOTE)])
        assert out["written"] == 1
        assert list(evidences(chat_id)) == [CHAT_QUOTE]

    def test_the_stored_greeting_row_is_card_text(self, client) -> None:
        """The greeting row was rendered with the persona of the day it was
        written. Switching persona afterwards makes the card render differ,
        so only the row itself can catch this quote."""
        make_persona(client, "Ilka", select=True)
        chat_id = chat_for(
            client, first_mes="Welcome, {{user}}, to the salt harbour of Vell.")
        make_persona(client, "Ravi", select=True)
        out = commit(chat_id, [
            fact("Welcome, Ilka, to the salt harbour", role="assistant"),
            fact(CHAT_QUOTE)])
        assert out["written"] == 1
        assert list(evidences(chat_id)) == [CHAT_QUOTE]

    def test_an_enabled_module_is_card_text(self, client) -> None:
        chat_id = chat_for(client)
        upload_module(client, "The city of Varn has no king since the flood.",
                      enabled=True)
        out = commit(chat_id, [
            fact("Varn has no king since the flood", role="assistant"),
            fact(CHAT_QUOTE)])
        assert out["written"] == 1
        assert list(evidences(chat_id)) == [CHAT_QUOTE]

    def test_a_disabled_module_is_not(self, client) -> None:
        """POSITIVE CONTROL for the one above: switched off, it is not in
        the prompt, so it is not a reason to drop anything."""
        chat_id = chat_for(client)
        upload_module(client, "The city of Varn has no king since the flood.",
                      enabled=False)
        out = commit(chat_id, [
            fact("Varn has no king since the flood", role="assistant"),
            fact(CHAT_QUOTE)])
        assert out["written"] == 2

    def test_the_length_floor(self, client) -> None:
        """A quote one character under the floor is kept even though the
        card contains it; one at the floor is dropped."""
        n = notebook.CARD_ECHO_MIN_CHARS
        line = "abcdefghij klmnopqrstuvwxyz0123456789"
        assert len(line) > n
        chat_id = chat_for(client, description=line)
        short, exact = line[:n - 1], line[:n]
        out = commit(chat_id, [fact(short), fact(exact, text="Other note.")])
        assert out["written"] == 1
        assert list(evidences(chat_id)) == [short]


# ---------------------------------------------------------------------------
# C2
# ---------------------------------------------------------------------------

class TestInstructionCardHoldsTheModelsOwnQuotes:
    @pytest.mark.parametrize("field", ["system_prompt",
                                       "post_history_instruction"])
    def test_model_quote_waits_user_quote_does_not(self, client, field):
        chat_id = chat_for(client, **{field: "Stay in character."})
        out = commit(chat_id, [fact(CHAT_QUOTE_2, role="assistant"),
                               fact(CHAT_QUOTE, role="user")])
        assert out["written"] == 2
        assert evidences(chat_id) == {
            CHAT_QUOTE_2: notebook.STATUS_PROPOSED,
            CHAT_QUOTE: notebook.STATUS_ACCEPTED}

    def test_ground_a_card_without_instructions_auto_accepts(self, client):
        chat_id = chat_for(client, system_prompt="   ",
                           post_history_instruction="\n\t")
        commit(chat_id, [fact(CHAT_QUOTE_2, role="assistant"),
                         fact(CHAT_QUOTE, role="user")])
        assert evidences(chat_id) == {
            CHAT_QUOTE_2: notebook.STATUS_ACCEPTED,
            CHAT_QUOTE: notebook.STATUS_ACCEPTED}

    def test_a_held_note_retires_nothing(self, client) -> None:
        """A held suggestion is one nobody has read; it keeps its intent
        but the note it names stays."""
        chat_id = chat_for(client, system_prompt="Stay in character.")
        old = notebook.create_entry(chat_id, "The ferry leaves at noon.",
                                    provenance=notebook.PROV_MODEL)
        out = commit(chat_id, [fact(CHAT_QUOTE_2, role="assistant",
                                    supersedes=0)],
                     existing_ids=[old["id"]])
        assert out["retired"] == 0
        rows = {e["id"]: e for e in notebook.list_entries(chat_id)}
        assert rows[old["id"]]["retired_at"] is None
        held = next(e for e in rows.values() if e["evidence"] == CHAT_QUOTE_2)
        assert held["status"] == notebook.STATUS_PROPOSED
        with get_db() as con:
            target = con.execute(
                "SELECT supersedes_id FROM notebook_entries WHERE id = ?",
                (held["id"],)).fetchone()[0]
        assert target == old["id"]

    def test_positive_control_the_same_note_retires_without_instructions(
            self, client) -> None:
        chat_id = chat_for(client)
        old = notebook.create_entry(chat_id, "The ferry leaves at noon.",
                                    provenance=notebook.PROV_MODEL)
        out = commit(chat_id, [fact(CHAT_QUOTE_2, role="assistant",
                                    supersedes=0)],
                     existing_ids=[old["id"]])
        assert out["retired"] == 1

    def test_review_mode_is_unchanged(self, client) -> None:
        import config
        from database import set_setting
        set_setting(config.SETTING_NOTEBOOK_AUTO_ACCEPT, "0")
        chat_id = chat_for(client, system_prompt="Stay in character.")
        commit(chat_id, [fact(CHAT_QUOTE_2, role="assistant"),
                         fact(CHAT_QUOTE, role="user")])
        assert set(evidences(chat_id).values()) == {notebook.STATUS_PROPOSED}
