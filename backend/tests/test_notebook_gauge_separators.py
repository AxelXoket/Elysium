"""The notebook's gauge figure is what the notes add to the real request.

GET /notebook/{chat_id} answers `notebook_chars`, which the context gauge
charges as an opaque number. Every note block the request carries is a part
of the single system message (user notes, boundaries) or of the folded tail
(model notes), and each part brings one prompt_layer.PART_SEP before it.
The figure is checked against the ASSEMBLED PAYLOAD: the characters the
notes add to _assemble_messages' output, not a re-derived formula.
"""
from __future__ import annotations

import notebook_store as notebook
from prompt_layer import PART_SEP
from routers.completions import _assemble_messages
from tests.conftest import make_character, make_chat

_BUDGET = 900_000


def _payload_chars(chat_id: int) -> int:
    blocks = notebook.build_notebook_blocks(chat_id, _BUDGET)
    msgs = _assemble_messages(
        "[Character: X]", "", [], "hello", "", _BUDGET, 2000,
        boundary_block=blocks["boundary_block"],
        notebook_user_block=blocks["user_block"],
        notebook_model_block=blocks["model_block"],
        trimmed_out=[],
    )
    return sum(len(m["content"]) for m in msgs)


def _gauge(client, chat_id: int) -> int:
    r = client.get(f"/api/v1/notebook/{chat_id}")
    assert r.status_code == 200, r.text
    return r.json()["notebook_chars"]


def test_ground_an_empty_notebook_costs_nothing(client) -> None:
    chat = make_chat(client, make_character(client))
    assert _gauge(client, chat) == 0


def test_a_user_note_costs_what_it_adds_to_the_payload(client) -> None:
    chat = make_chat(client, make_character(client))
    before = _payload_chars(chat)
    notebook.create_entry(chat, "The lighthouse keeps a red lamp.")
    added = _payload_chars(chat) - before
    assert added > 0  # positive control: the note did reach the payload
    assert _gauge(client, chat) == added


def test_a_model_note_in_the_tail_costs_what_it_adds(client) -> None:
    chat = make_chat(client, make_character(client))
    before = _payload_chars(chat)
    notebook.create_entry(chat, "The harbour froze last winter.",
                          provenance=notebook.PROV_MODEL)
    added = _payload_chars(chat) - before
    assert added > 0
    assert _gauge(client, chat) == added


def test_both_blocks_together_cost_what_they_add(client) -> None:
    chat = make_chat(client, make_character(client))
    before = _payload_chars(chat)
    notebook.create_entry(chat, "A user note.")
    notebook.create_entry(chat, "A model note.", provenance=notebook.PROV_MODEL)
    added = _payload_chars(chat) - before
    blocks = notebook.build_notebook_blocks(chat, _BUDGET)
    raw = sum(len(blocks[k]) for k in ("user_block", "model_block", "boundary_block"))
    # Ground: the raw block sum is short of the payload by one separator per
    # block, which is the drift this figure had.
    assert added == raw + 2 * len(PART_SEP)
    assert _gauge(client, chat) == added
