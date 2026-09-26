"""The tail is counted before the trim, separator included (W3 round 2, M1).

Everything the request adds after the user's own text (the model-notes block,
and in mode b the <reminder>) is folded into the last user message after one
PART_SEP. The history trim must charge all of it: `prompt_layer.tail_chars`
is the text of the tail PLUS that separator. A count that forgot the two
separator characters let two characters more of history through than fit, and
no test noticed, because a bisection only finds wherever the line is.

So these tests do not trust the line; they predict how far it MOVES. The trim
depends on the sum of the fixed parts and the typed text, so adding a part of
n characters must move the longest typed text that still keeps every turn by
exactly n. n is read off the payload the provider received, not recomputed
with the function under test.

Mode c: the same chat with and without a model note (the note goes only in
the tail). Mode b: the layer against the plain assembly for the same card;
the difference is the system text plus the whole folded reminder.
"""
from __future__ import annotations

import database
import notebook_store
import prompt_layer
from conftest import make_character, make_chat
from prompt_modules import store

MODEL = "test/model-1"
SEP = prompt_layer.PART_SEP
NOTE = "The lighthouse lamp is red."
#: History turns of a known size, and a small reply reserve, so the budget
#: below is mostly room for history and the notes block is not squeezed out.
PAD = 300
MAX_TOKENS = 64


def _history(messages: list[dict]) -> list[str]:
    turns = [str(m["content"]) for m in messages if m["role"] != "system"]
    return turns[:-1]


def _probe(client, provider, typed_len: int, *, budget: int, note: bool,
           layer: bool) -> tuple[int, list[dict]]:
    """A fresh chat with three uniform turns; returns the surviving history
    count and the payload for a typed turn of `typed_len` characters."""
    with database.get_db() as con:
        store.save_settings(con, layer=layer, words=None)
    chat = make_chat(client, make_character(client, first_mes=""))
    # Written straight into the table: the history is data here, and three
    # round trips per probe would make the bisection slow.
    with database.get_db() as con:
        for i in range(3):
            con.execute("INSERT INTO messages (chat_id, role, content) VALUES (?, 'user', ?)",
                        (chat, f"TURN{i}-" + "y" * PAD))
            con.execute("INSERT INTO messages (chat_id, role, content) "
                        "VALUES (?, 'assistant', ?)", (chat, f"REPLY{i}-" + "z" * PAD))
    if note:
        notebook_store.create_entry(chat, NOTE, provenance=notebook_store.PROV_MODEL)
    resp = client.post(f"/api/v1/chats/{chat}/complete",
                       json={"message": "q" * typed_len, "model_id": MODEL,
                             "context_budget_tokens": budget,
                             "generation_params": {"max_tokens": MAX_TOKENS}})
    if resp.status_code == 400 and resp.json()["detail"] == "context_too_large":
        return -1, []            # past every line: not even the turn fits
    assert resp.status_code == 200, resp.text
    msgs = provider.calls[-1]["messages"]
    return len(_history(msgs)), msgs


def _line(client, provider, lo: int = 1, hi: int = 6000, **kw) -> tuple[int, list[dict]]:
    """The longest typed text that keeps as much history as a one-character
    text does, found by bisection inside [lo, hi], and the payload there."""
    baseline, _ = _probe(client, provider, 1, **kw)
    assert baseline >= 2, ("nothing to lose at the small end", baseline)
    assert _probe(client, provider, lo, **kw)[0] == baseline, "narrow the search"
    assert _probe(client, provider, hi, **kw)[0] < baseline, "widen the search"
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if _probe(client, provider, mid, **kw)[0] < baseline:
            hi = mid
        else:
            lo = mid
    kept, msgs = _probe(client, provider, lo, **kw)
    assert kept == baseline
    assert _probe(client, provider, hi, **kw)[0] == baseline - 1
    return lo, msgs


def _folded(msgs: list[dict], typed_len: int) -> int:
    """What the request added to the typed text in the last user message."""
    return len(msgs[-1]["content"]) - typed_len


def test_mode_c_a_model_note_moves_the_line_by_exactly_what_it_adds(client, provider):
    plain, plain_msgs = _line(client, provider, budget=1600, note=False, layer=False)
    # The note moves the line by a few dozen characters: search near it.
    noted, noted_msgs = _line(client, provider, lo=plain - 400, hi=plain + 1,
                              budget=1600, note=True, layer=False)
    # Ground: without a note nothing is folded; with one, the note and its
    # separator are.
    assert _folded(plain_msgs, plain) == 0
    added = _folded(noted_msgs, noted)
    assert NOTE in noted_msgs[-1]["content"]
    assert noted_msgs[-1]["content"].startswith("q" * noted + SEP)
    assert added > len(SEP)
    # The system message is the same length either way (same card, same
    # salt length), so the whole move is the fold.
    assert len(plain_msgs[0]["content"]) == len(noted_msgs[0]["content"])
    assert plain - noted == added


def test_mode_b_the_reminder_is_charged_with_its_separator(client, provider):
    # The layer is about 860 tokens, so this budget is larger than mode c's.
    layered, layer_msgs = _line(client, provider, lo=1, hi=2048,
                                budget=2400, note=False, layer=True)
    assert layer_msgs[0]["content"].split(SEP)[1].startswith("<roleplay>")
    folded = _folded(layer_msgs, layered)
    # Positive control: the reminder is in the tail after one separator.
    assert layer_msgs[-1]["content"] == "q" * layered + SEP + prompt_layer.reminder("User", None)
    assert folded == len(SEP) + len(prompt_layer.reminder("User", None))
    _, plain_at_one = _probe(client, provider, 1, budget=2400, note=False, layer=False)
    grown = len(layer_msgs[0]["content"]) - len(plain_at_one[0]["content"])
    assert grown > 0
    # The prediction only narrows the search; the window is wide enough that
    # a line off by the separator is still found, and then fails below.
    expected = layered + grown + folded
    plain, plain_msgs = _line(client, provider, lo=expected - 64, hi=expected + 64,
                              budget=2400, note=False, layer=False)
    assert _folded(plain_msgs, plain) == 0
    assert plain - layered == grown + folded
