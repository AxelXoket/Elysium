"""What the model is actually handed: the order of it, and what falls off the back.

KADEME 12 measurement. `_assemble_messages` (routers/completions.py:384-477)
decides two things nobody could see from outside: where each block sits in the
list, and which turns are dropped when the conversation outgrows the budget.
The eviction loop at :433-438 had NO test anywhere in the suite - the two
budget tests that existed (test_completions_flow.py:313-348) both fail the
request before the loop can run even once, so the `while` had never executed
under an assertion.

That loop is the everyday behaviour of a long chat. If it dropped from the
wrong end, the user would keep the beginning of a conversation and lose what
was just said, with no error and nothing red. That is the shape of bug that
gets reported as "it forgot what I told it" and blamed on the model.

Everything here goes through the real endpoint. The budget arithmetic is
deliberately NOT restated in this file: a test that recomputes the formula
agrees with a broken formula. Sizes are DISCOVERED by measurement instead.
"""

import pytest

import database
from conftest import make_character, make_chat, make_persona

MODEL = "test/model-1"
#: The smallest budget the request model will accept (ge=512), which is what
#: makes a trim reachable at all without seeding a book.
SMALL_BUDGET = 512


def _send(client, chat: int, text: str, **extra):
    resp = client.post(f"/api/v1/chats/{chat}/complete",
                       json={"message": text, "model_id": MODEL, **extra})
    return resp


def _ok(client, chat: int, text: str, **extra):
    resp = _send(client, chat, text, **extra)
    assert resp.status_code == 200, resp.text
    return resp


def _history_of(messages: list[dict]) -> list[str]:
    """The replayed turns: everything that is not a block, minus the turn being
    sent right now (which is always the last non-system entry)."""
    turns = [str(m["content"]) for m in messages if m["role"] != "system"]
    return turns[:-1]


def _seeded_chat(client, provider, turns: int = 3, pad: int = 120) -> int:
    """A chat whose history is `turns` exchanges of a known, uniform size.

    `first_mes` is empty on purpose so the only rows are the ones counted here.
    """
    chat = make_chat(client, make_character(client, first_mes=""))
    for i in range(turns):
        provider.response_text = f"REPLY{i}-" + "z" * pad
        _ok(client, chat, f"TURN{i}-" + "y" * pad)
    return chat


def _survivors(client, provider, current_len: int) -> list[str]:
    """How much history is left when the current turn is `current_len` chars.

    A fresh chat each time: the previous probe's own message would otherwise
    become history and move the line it is trying to find.
    """
    chat = _seeded_chat(client, provider)
    _ok(client, chat, "q" * current_len, context_budget_tokens=SMALL_BUDGET)
    return _history_of(provider.calls[-1]["messages"])


# ── the order ────────────────────────────────────────────────────────────────

def test_the_payload_arrives_in_the_order_the_model_is_promised(client, provider):
    """Character, then persona, then the conversation, then the turn being
    sent, and the trailing instruction LAST.

    post_history_instruction is named for its position. A well meant "fix"
    moving it up next to the system block would leave every assertion in
    test_character_edit_reaches_prompt.py green - that file checks the string
    is somewhere in the payload, never where.
    """
    char = make_character(client, first_mes="")
    chat = make_chat(client, char)
    assert client.patch(f"/api/v1/characters/{char}",
                        json={"post_history_instruction": "PHI-BLOCK"}
                        ).status_code == 200
    make_persona(client, display_name="Nova", description="PERSONA-BLOCK",
                 select=True)

    provider.response_text = "ANSWER"
    _ok(client, chat, "FIRST")
    _ok(client, chat, "SECOND")

    sent = provider.calls[-1]["messages"]
    shape = [(m["role"], str(m["content"])) for m in sent]
    assert shape[0][0] == "system" and "A test character" in shape[0][1]
    assert shape[1] == ("system", "[User Persona: Nova]\nPERSONA-BLOCK")
    assert shape[2] == ("user", "FIRST")
    assert shape[3] == ("assistant", "ANSWER")
    assert shape[4] == ("user", "SECOND")
    assert shape[5] == ("system", "PHI-BLOCK")
    assert len(shape) == 6, shape


# ── what falls off, and from which end ───────────────────────────────────────

def test_the_oldest_turns_are_the_ones_that_go(client, provider):
    """The whole point. Dropping the newest would be invisible to a length
    check and catastrophic to a conversation."""
    chat = _seeded_chat(client, provider, turns=6)
    _ok(client, chat, "NOW", context_budget_tokens=SMALL_BUDGET)

    kept = _history_of(provider.calls[-1]["messages"])
    assert kept, "everything was dropped - the budget is too tight to measure"
    assert len(kept) < 12, "nothing was dropped - the fixture does not bite"
    # The survivors are a SUFFIX of the conversation, in order.
    assert "TURN0-" not in "".join(kept), "the oldest turn survived a trim"
    assert kept[-1].startswith("REPLY5-"), kept[-1]
    numbers = [int(t.split("-")[0][-1]) for t in kept]
    assert numbers == sorted(numbers), ("the surviving turns were reordered", kept)


def test_a_budget_that_fits_leaves_every_turn_where_it_was(client, provider):
    """The positive control. Without it, code that dropped history
    unconditionally would satisfy every assertion above."""
    chat = _seeded_chat(client, provider, turns=6)
    _ok(client, chat, "NOW")          # no budget given: the model's own

    kept = _history_of(provider.calls[-1]["messages"])
    assert len(kept) == 12, kept
    assert kept[0].startswith("TURN0-")


def test_a_turn_that_survives_arrives_whole(client, provider):
    """Messages are dropped, never shortened. A trimmer that cut the oldest
    message down to size instead of removing it would keep the payload legal
    and hand the model half a sentence."""
    chat = _seeded_chat(client, provider, turns=4, pad=200)
    _ok(client, chat, "NOW", context_budget_tokens=SMALL_BUDGET)

    kept = _history_of(provider.calls[-1]["messages"])
    assert kept, "the fixture dropped everything"
    for entry in kept:
        _marker, _, body = entry.partition("-")
        assert body in ("y" * 200, "z" * 200), (len(entry), entry[:40])


def test_one_character_past_the_line_costs_exactly_one_turn(client, provider):
    """The boundary, found by measurement rather than by restating the formula.

    The line is located by bisection through the real endpoint, so this test
    keeps working when the character block, the safety margin or the chars-per
    -token estimate change. What it pins is the SHAPE of the edge: one turn
    leaves, not two and not all of them.

    Honest about what it cannot see: shifting the comparison by one character
    (`>` to `>=`) moves the line, and bisection would simply find the new one.
    This catches a trimmer that over-drops, under-drops, or drops in a batch.
    """
    baseline = len(_survivors(client, provider, 1))
    assert baseline >= 2, ("nothing to lose at the small end", baseline)

    # survivors() is non-increasing in the length of the current turn, so the
    # first length that loses a turn can be bisected for.
    lo, hi = 1, 400
    assert len(_survivors(client, provider, hi)) < baseline, (
        "no turn is lost even at the far end - widen the search")
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if len(_survivors(client, provider, mid)) < baseline:
            hi = mid
        else:
            lo = mid

    assert len(_survivors(client, provider, lo)) == baseline
    assert len(_survivors(client, provider, hi)) == baseline - 1, (
        "crossing the line cost more than one turn")


def test_a_turn_too_big_to_fit_at_all_is_refused_not_quietly_cut(client, provider):
    """There is no honest way to send this, so the user is told. A 200 with a
    silently shortened message would read as a model that stopped mid thought.
    """
    chat = _seeded_chat(client, provider, turns=1)
    resp = _send(client, chat, "q" * 5000, context_budget_tokens=SMALL_BUDGET)
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "context_too_large"


def test_the_blocks_that_are_not_history_are_never_trimmed(client, provider):
    """Identity, persona and the trailing instruction are reserved before
    history is measured. They must still be there after a trim severe enough to
    take most of the conversation with it."""
    char = make_character(client, first_mes="")
    chat = make_chat(client, char)
    assert client.patch(f"/api/v1/characters/{char}",
                        json={"post_history_instruction": "PHI-BLOCK"}
                        ).status_code == 200
    make_persona(client, display_name="Nova", description="PERSONA-BLOCK",
                 select=True)
    for i in range(6):
        provider.response_text = f"REPLY{i}-" + "z" * 120
        _ok(client, chat, f"TURN{i}-" + "y" * 120)

    _ok(client, chat, "NOW", context_budget_tokens=SMALL_BUDGET)
    sent = provider.calls[-1]["messages"]
    assert len(_history_of(sent)) < 12, "no trim happened, so nothing is proven"

    blocks = [str(m["content"]) for m in sent if m["role"] == "system"]
    assert any("A test character" in b for b in blocks), blocks
    assert any("PERSONA-BLOCK" in b for b in blocks), blocks
    assert blocks[-1] == "PHI-BLOCK", blocks


def test_the_card_greeting_is_ordinary_history_and_can_be_pushed_out(client,
                                                                     provider):
    """CHARACTERIZATION, not approval.

    `first_mes` is seeded as a plain assistant row (routers/chats.py:174), so
    it is exactly as evictable as any later turn - the prose the card author
    wrote to set the scene is the FIRST thing a long chat forgets. That may
    well be the right call; it is not a decision anyone recorded. Pinned here
    so the day it changes is a day somebody has to come and read this.
    """
    char = make_character(client, first_mes="OPENING-LINE from the card")
    chat = make_chat(client, char)
    for i in range(6):
        provider.response_text = f"REPLY{i}-" + "z" * 120
        _ok(client, chat, f"TURN{i}-" + "y" * 120)

    _ok(client, chat, "NOW")                    # roomy budget: still there
    assert any("OPENING-LINE" in t
               for t in _history_of(provider.calls[-1]["messages"]))

    _ok(client, chat, "NOW", context_budget_tokens=SMALL_BUDGET)
    kept = _history_of(provider.calls[-1]["messages"])
    assert not any("OPENING-LINE" in t for t in kept), (
        "the greeting is pinned now - that is a product change, not a test fix")


# ── the persona is edited mid chat, like the character was ───────────────────

def test_editing_a_persona_reaches_the_very_next_reply(client, provider):
    """The sibling of test_character_edit_reaches_prompt.py, which exists
    because a REPORTED bug had a chat keep sending details the user had already
    changed. The persona travels the same road and had no such test: the only
    PATCH of a persona in the suite (test_toctou.py:134) checks the HTTP echo
    and never sends a message afterwards.
    """
    persona = make_persona(client, display_name="Nova",
                           description="Keeps to herself.", select=True)
    chat = make_chat(client, make_character(client, first_mes=""))

    _ok(client, chat, "hello")
    assert "Keeps to herself." in str(provider.calls[-1]["messages"][1]["content"])

    assert client.patch(f"/api/v1/personas/{persona}",
                        json={"description": "Talks to everyone."}
                        ).status_code == 200
    _ok(client, chat, "hello again")

    blocks = [str(m["content"]) for m in provider.calls[-1]["messages"]
              if m["role"] == "system"]
    joined = "\n".join(blocks)
    assert "Talks to everyone." in joined, joined
    assert "Keeps to herself." not in joined, (
        "the persona the user rewrote was still being sent")


def test_clearing_a_persona_description_stops_sending_it(client, provider):
    """The half most likely to break: an empty string is falsy between the
    textarea and the SQL, and anything reading it as "no change given" leaves
    the deleted sentence in the prompt forever."""
    persona = make_persona(client, display_name="Nova",
                           description="Keeps to herself.", select=True)
    chat = make_chat(client, make_character(client, first_mes=""))
    _ok(client, chat, "hello")
    assert "Keeps to herself." in str(provider.calls[-1]["messages"][1]["content"])

    assert client.patch(f"/api/v1/personas/{persona}",
                        json={"description": ""}).status_code == 200
    _ok(client, chat, "hello again")

    joined = "\n".join(str(m["content"])
                       for m in provider.calls[-1]["messages"]
                       if m["role"] == "system")
    assert "Keeps to herself." not in joined, joined
    assert "[User Persona: Nova]" in joined, (
        "clearing the description took the name with it")
