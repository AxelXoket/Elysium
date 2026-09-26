"""H4: the modules may take at most half of what the rest of the request
leaves for history (dF 4, MT-12), checked at request time with the real
numbers and at toggle time by the SAME counting path (contract A5).

No budget arithmetic is retyped here: the threshold is FOUND by bisection
through the real endpoint, and the one comparison with the rule uses
prompt_modules.budget's own functions.
"""

from __future__ import annotations

import json

import pytest

import database
import notebook_store
from conftest import make_chat, make_persona
from prompt_modules import budget, parsers, store
from routers.completions import (
    _DEFAULT_CONTEXT_LEN,
    _DEFAULT_MAX_TOKENS,
    module_budget_check,
)
from prompt_modules.errors import OVER_BUDGET, PromptModuleError
from prompt_modules.limits import MODULE_MAX_CHARS

MODEL = "test/model-1"
WINDOW = 16384


def _card(client, **fields) -> int:
    resp = client.post("/api/v1/characters", json={"name": "Mira", "first_mes": "", **fields})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _chunks(length: int) -> list[str]:
    """`length` characters of module text as the fewest modules the per-module
    cap allows (a longer one would never be enabled)."""
    return ["m" * min(MODULE_MAX_CHARS, length - i)
            for i in range(0, length, MODULE_MAX_CHARS)]


def _set_module(length: int, name: str = "style.txt") -> int:
    """`length` characters of enabled module text, and nothing else on."""
    ids: list[int] = []
    with database.get_db() as con:
        for row in store.list_rows(con):
            store.delete(con, row["id"])
        for i, text in enumerate(_chunks(length)):
            fname = name if i == 0 else f"part{i}.txt"
            imp = store.insert_import(con, parsers.parse_upload(fname, text.encode()), fname)
            ids += [r["id"] for r in store.rows_for_import(con, imp)]
        store.set_enabled(con, ids, True)
    return ids[0]


def _send(client, chat, window=WINDOW, route="complete"):
    return client.post(f"/api/v1/chats/{chat}/{route}",
                       json={"message": "hello", "model_id": MODEL,
                             "context_budget_tokens": window})


def _refused(client, chat, n, window=WINDOW) -> bool:
    _set_module(n)
    resp = _send(client, chat, window)
    if resp.status_code == 200:
        return False
    assert resp.status_code == 400 and resp.json()["detail"] == OVER_BUDGET, resp.text
    return True


def _bisect_threshold(client, chat, window=WINDOW) -> int:
    """The longest module that still goes, FOUND through the endpoint (the
    refusal is monotone in it). Slow; used once, to prove the rule."""
    lo, hi = 1, 120_000
    assert not _refused(client, chat, lo, window), "ground: a tiny module goes"
    assert _refused(client, chat, hi, window), "ground: a huge one does not"
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if _refused(client, chat, mid, window):
            hi = mid
        else:
            lo = mid
    return lo


def _threshold(client, provider, chat, window=WINDOW) -> int:
    """The longest module that goes, from the rule's own functions applied to
    the fixed cost measured on a request with no module; then CHECKED at the
    edge through the endpoint, so a disagreement is a red test here."""
    with database.get_db() as con:
        for row in store.list_rows(con):
            store.delete(con, row["id"])
    assert _send(client, chat, window).status_code == 200
    sent = provider.calls[-1]["messages"]
    fixed = len(sent[0]["content"]) + len(sent[-1]["content"])
    available = budget.history_budget_chars(
        context_budget_tokens=window, model_context_tokens=_DEFAULT_CONTEXT_LEN,
        max_tokens=_DEFAULT_MAX_TOKENS)
    room = budget.module_room(available, fixed)
    t = max(n for n in range(max(1, room - 3 * len(_chunks(room + 1)) - 2), room + 1)
            if budget.module_addition_chars(_chunks(n)) <= room)
    assert not _refused(client, chat, t, window), "the edge refused a module the rule allows"
    assert _refused(client, chat, t + 1, window), "the edge let through one past the rule"
    return t


@pytest.fixture()
def chat(client) -> int:
    return make_chat(client, _card(client, description="A lighthouse keeper."))


def test_mt12_the_line_is_half_of_what_the_rest_leaves(client, provider, chat):
    t = _bisect_threshold(client, chat)
    # What the rest of the request costs, measured on a request without it.
    with database.get_db() as con:
        for row in store.list_rows(con):
            store.delete(con, row["id"])
    assert _send(client, chat).status_code == 200
    sent = provider.calls[-1]["messages"]
    fixed = len(sent[0]["content"]) + len(sent[-1]["content"])
    available = budget.history_budget_chars(
        context_budget_tokens=WINDOW, model_context_tokens=_DEFAULT_CONTEXT_LEN,
        max_tokens=_DEFAULT_MAX_TOKENS)
    room = budget.module_room(available, fixed)
    assert budget.module_addition_chars(_chunks(t)) <= room
    assert budget.module_addition_chars(_chunks(t + 1)) > room


def test_mt12_the_refusal_names_the_modules_and_what_they_cost(client, provider, chat):
    t = _threshold(client, provider, chat)
    calls = len(provider.calls)
    stored = client.get(f"/api/v1/chats/{chat}/messages").json()
    module_id = _set_module(t + 1, name="Stylebook.txt")
    for route in ("complete", "complete/stream"):
        resp = _send(client, chat, route=route)
        assert resp.status_code == 400, resp.text
        body = resp.json()
        assert body["detail"] == OVER_BUDGET
        assert body["modules"] == [{"id": module_id, "name": "Stylebook",
                                    "tokens": budget.tokens_for(t + 1)}]
        assert body["total_tokens"] == budget.tokens_for(
            budget.module_addition_chars(["m" * (t + 1)]))
    assert len(provider.calls) == calls, "a refused turn reached the provider"
    assert client.get(f"/api/v1/chats/{chat}/messages").json() == stored, (
        "a refused turn was stored")


def test_mt12_positive_control_a_bigger_window_takes_the_same_module(client, provider, chat):
    t = _threshold(client, provider, chat)
    _set_module(t + 1)
    assert _send(client, chat, window=2 * WINDOW).status_code == 200


def test_mt12_a_window_shrink_after_switching_on_is_caught_per_request(client, provider, chat):
    """(2): switched on under a big window, then the window shrinks. A check
    only at toggle time would miss this; the request says so by name."""
    big = _threshold(client, provider, chat, window=2 * WINDOW)
    _set_module(big)
    assert _send(client, chat, window=2 * WINDOW).status_code == 200
    resp = _send(client, chat, window=WINDOW)
    assert resp.status_code == 400 and resp.json()["detail"] == OVER_BUDGET


def test_mt12_the_notebook_and_the_limits_count_as_fixed_cost(client, provider, chat):
    """(4b): a full notebook leaves the modules less room."""
    before = _threshold(client, provider, chat)
    for i in range(5):
        notebook_store.create_boundary(f"x{i}", f"Avoid graphic injury, case {i}.", "hard")
    for i in range(20):
        notebook_store.create_entry(chat, f"note {i} " + "n" * 80)
    after = _threshold(client, provider, chat)
    assert after < before


def test_mt12_a_bigger_card_leaves_less_room_but_is_not_blamed_alone(client, provider):
    small = make_chat(client, _card(client, description="x" * 100))
    large = make_chat(client, _card(client, description="x" * 24_000))
    assert _threshold(client, provider, large) < _threshold(client, provider, small)
    # Ground control: with no module on, the large card goes.
    with database.get_db() as con:
        for row in store.list_rows(con):
            store.delete(con, row["id"])
    assert _send(client, large).status_code == 200


def test_mt12_a_small_module_goes_with_a_big_card(client, provider):
    """(4): a 100-token module and a 17,626-character card, with a persona
    and Limits: goes."""
    chat = make_chat(client, _card(client, description="x" * 17_626))
    make_persona(client, "Ann", "p" * 300, select=True)
    notebook_store.create_boundary("x", "Avoid graphic injury.", "hard")
    _set_module(458)
    assert _send(client, chat).status_code == 200


def test_mt12_the_card_alone_overflowing_keeps_its_old_name(client, provider):
    """(5): the card does not fit even without the module: context_too_large,
    never the module's name for it."""
    chat = make_chat(client, _card(client, description="x" * 60_000))
    _set_module(10)
    resp = _send(client, chat)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "context_too_large"


def test_worst_case_a_regenerate_only_module_counts_on_a_send(client, provider, chat):
    """dF 5.1: H4 counts every enabled module, even one that does not fire on
    this kind of generation; the text itself is not sent on a send."""
    preset = {"prompts": [{"identifier": "a", "name": "R", "content": "REGEN ONLY TEXT",
                           "injection_trigger": ["regenerate"]}],
              "prompt_order": [{"character_id": 100001,
                                "order": [{"identifier": "a", "enabled": True}]}]}
    with database.get_db() as con:
        imp = store.insert_import(con, parsers.parse_upload(
            "p.json", json.dumps(preset).encode()), "p.json")
        store.set_enabled(con, [r["id"] for r in store.rows_for_import(con, imp)], True)
    assert _send(client, chat).status_code == 200
    system = provider.calls[-1]["messages"][0]["content"]
    assert "REGEN ONLY TEXT" not in system
    assert "[Character: Mira]" in system and system.index("[Character: Mira]") > 0


# ── toggle time uses the same count (A5) ─────────────────────────────────────

def test_the_toggle_check_agrees_with_the_request_to_the_character(client, provider, chat):
    t = _threshold(client, provider, chat)
    available = budget.history_budget_chars(
        context_budget_tokens=WINDOW, model_context_tokens=_DEFAULT_CONTEXT_LEN,
        max_tokens=_DEFAULT_MAX_TOKENS)
    with database.get_db() as con:
        module_budget_check(con, chat, [{"id": 1, "name": "m", "content": "m" * t}],
                            available, user_message_chars=len("hello"))
        with pytest.raises(PromptModuleError) as exc:
            module_budget_check(con, chat, [{"id": 1, "name": "m", "content": "m" * (t + 1)}],
                                available, user_message_chars=len("hello"))
    assert exc.value.code == OVER_BUDGET


def test_the_toggle_check_counts_prepared_text_not_stored_text(client, provider, chat):
    """A {{// comment}} never reaches the model, so it costs nothing at the
    toggle either (W4 M1)."""
    t = _threshold(client, provider, chat)
    available = budget.history_budget_chars(
        context_budget_tokens=WINDOW, model_context_tokens=_DEFAULT_CONTEXT_LEN,
        max_tokens=_DEFAULT_MAX_TOKENS)
    content = "m" * t + "{{// " + "c" * 5000 + "}}"
    with database.get_db() as con:
        module_budget_check(con, chat, [{"id": 1, "name": "m", "content": content}],
                            available, user_message_chars=len("hello"))


def test_the_toggle_check_without_a_chat_uses_the_reference_card(client, provider):
    available = budget.history_budget_chars(
        context_budget_tokens=WINDOW, model_context_tokens=_DEFAULT_CONTEXT_LEN,
        max_tokens=_DEFAULT_MAX_TOKENS)
    with database.get_db() as con:
        module_budget_check(con, None, [{"id": 1, "name": "m", "content": "m" * 100}], available)
        with pytest.raises(PromptModuleError):
            module_budget_check(con, None, [{"id": 1, "name": "m", "content": "m" * available}],
                                available)
