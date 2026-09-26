"""H4, the module size rule: the shared function and the toggle-time check.

The rule (dF 4): with at least one module on, module text plus one separator
per module may take at most MODULE_BUDGET_SHARE_PERCENT of what is left of the
history budget after every non-module fixed cost. The layer is not a fixed
cost when a module is on; there is no wrapper; a card that overflows on its own
keeps `context_too_large`.

MT-12 cases here are the toggle-time half. Since contract amendment A5 the
toggle counts through the request path's own function
(routers.completions.module_budget_check), so the room each test expects is
found by asking that function, and the agreement tests then send a real
completion request and require the same decision.
"""

from __future__ import annotations

import pytest

import database
import notebook_store
import voice_tags
from prompt_modules import budget
from prompt_modules import errors as E
from prompt_modules.limits import MODULE_BUDGET_SHARE_PERCENT, MODULE_SEPARATOR
from tests.conftest import make_chat

BASE = "/api/v1/prompt-modules"
SEP = len(MODULE_SEPARATOR)
W16 = {"context_budget_tokens": 16384, "max_tokens": 1024}
W32 = {"context_budget_tokens": 32000, "max_tokens": 1024}


def _avail(window: dict) -> int:
    # No model id: the completion path's defaults, read by the router.
    from routers.completions import _DEFAULT_CONTEXT_LEN
    return budget.history_budget_chars(
        context_budget_tokens=window["context_budget_tokens"],
        model_context_tokens=_DEFAULT_CONTEXT_LEN,
        max_tokens=window["max_tokens"])


# ---------------------------------------------------------------------------
# The rule function
# ---------------------------------------------------------------------------

def _mods(*sizes):
    return [{"id": i, "name": f"M{i}", "content": "x" * s} for i, s in enumerate(sizes)]


class TestRule:
    def test_exact_room_passes_one_more_is_refused(self):
        avail, fixed = 45_312, 3_591
        room = budget.module_room(avail, fixed)
        assert room == (avail - fixed) * MODULE_BUDGET_SHARE_PERCENT // 100
        v = budget.check(_mods(room - SEP), available_chars=avail, fixed_chars=fixed)
        assert v.added_chars == room
        with pytest.raises(E.PromptModuleError) as info:
            budget.check(_mods(room - SEP + 1), available_chars=avail, fixed_chars=fixed)
        assert info.value.code == E.OVER_BUDGET

    def test_separators_count_once_per_module(self):
        assert budget.module_addition_chars(["ab", "cde"]) == 5 + 2 * SEP
        assert budget.module_addition_chars([]) == 0

    def test_no_module_means_no_module_refusal(self):
        v = budget.check([], available_chars=100, fixed_chars=100)
        assert v.added_chars == 0 and v.room_chars == 0

    def test_card_overflow_keeps_its_own_name(self):
        for mods in ([], _mods(10)):
            with pytest.raises(E.PromptModuleError) as info:
                budget.check(mods, available_chars=1000, fixed_chars=1001)
            assert info.value.code == E.CONTEXT_TOO_LARGE

    def test_refusal_names_every_module_with_its_tokens(self):
        with pytest.raises(E.PromptModuleError) as info:
            budget.check(_mods(300, 30), available_chars=100, fixed_chars=0)
        ex = info.value.extra
        assert [m["name"] for m in ex["modules"]] == ["M0", "M1"]
        assert [m["tokens"] for m in ex["modules"]] == [
            budget.tokens_for(300), budget.tokens_for(30)]
        assert ex["total_tokens"] == budget.tokens_for(330 + 2 * SEP)

    def test_history_budget_matches_the_completion_arithmetic(self):
        # 16k window, 1024-token reply, 32k model: the 45,312 characters the
        # synthesis measured as "history left at the default window".
        assert _avail(W16) == 45_312
        # The user's window is capped by the model's.
        assert budget.history_budget_chars(context_budget_tokens=64000,
                                           model_context_tokens=16384,
                                           max_tokens=1024) == _avail(W16)
        # A reply reserve larger than the window is halved, never negative.
        assert budget.history_budget_chars(context_budget_tokens=1000,
                                           model_context_tokens=32000,
                                           max_tokens=100000) >= 0


# ---------------------------------------------------------------------------
# Toggle time, over HTTP
# ---------------------------------------------------------------------------

def _card(client, desc_chars: int, phi: str = "") -> int:
    r = client.post("/api/v1/characters", json={
        "name": "Card", "description": "d" * desc_chars,
        "post_history_instruction": phi})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _module(client, chars: int, name: str = "m.txt", text: str | None = None) -> int:
    body = (text if text is not None else "w" * chars).encode()
    r = client.post(f"{BASE}/import", content=body,
                    headers={"X-Prompt-Module-Filename": name,
                             "Content-Type": "application/octet-stream"})
    assert r.status_code == 201, r.text
    return r.json()["modules"][0]["id"]


def _enable(client, mid: int, **budget_ctx):
    return client.patch(f"{BASE}/{mid}", json={"enabled": True, **budget_ctx})


def _boundary(chat_id: int | None, window: dict) -> int:
    """The largest single module (plain letters) the request path accepts,
    found by asking the request path's own counter."""
    from routers.completions import module_budget_check

    avail = _avail(window)

    def ok(n: int) -> bool:
        with database.get_db() as con:
            try:
                module_budget_check(con, chat_id, [
                    {"id": 0, "name": "probe", "content": "w" * n}], avail)
                return True
            except E.PromptModuleError:
                return False
    lo, hi = 0, 200_000
    while lo < hi:
        mid = (lo + hi + 1) // 2
        lo, hi = (mid, hi) if ok(mid) else (lo, mid - 1)
    return lo


@pytest.fixture()
def sample_chat(client):
    """A card the size of the synthesis' example card (~2,900 characters)."""
    char_id = _card(client, 2_850)
    return make_chat(client, char_id), char_id


class TestToggleTime:
    def test_1_module_past_half_the_remainder_is_refused(self, client, sample_chat):
        chat_id, _ = sample_chat
        b = _boundary(chat_id, W16)
        assert b > 0
        too_big = _module(client, b + 1, "Big.txt")
        r = _enable(client, too_big, chat_id=chat_id, **W16)
        assert r.status_code == 400
        body = r.json()
        assert body["detail"] == E.OVER_BUDGET
        assert [m["name"] for m in body["modules"]] == ["Big"]
        # positive control: exactly the boundary passes
        fits = _module(client, b, "Fits.txt")
        assert _enable(client, fits, chat_id=chat_id, **W16).status_code == 200

    def test_1_positive_control_a_bigger_window_takes_it(self, client, sample_chat):
        chat_id, _ = sample_chat
        mid = _module(client, _boundary(chat_id, W16) + 100)
        assert _enable(client, mid, chat_id=chat_id, **W16).status_code == 400
        assert _enable(client, mid, chat_id=chat_id, **W32).status_code == 200

    def test_2_enabled_at_32k_then_the_window_shrinks(self, client, sample_chat):
        chat_id, _ = sample_chat
        b32 = _boundary(chat_id, W32)
        half = (b32 - 2 * SEP - 20) // 2
        for name, size in (("Large", half), ("Larger", half), ("Small", 10)):
            mid = _module(client, size, f"{name}.txt")
            r = _enable(client, mid, chat_id=chat_id, **W32)
            assert r.status_code == 200, r.text
        # the window drops to 16k: the next toggle names every enabled module
        tiny = _module(client, 5, "Tiny.txt")
        r = _enable(client, tiny, chat_id=chat_id, **W16)
        assert r.status_code == 400 and r.json()["detail"] == E.OVER_BUDGET
        assert [m["name"] for m in r.json()["modules"]] == [
            "Large", "Larger", "Small", "Tiny"]
        # positive control: at 32k the same toggle still fits
        assert _enable(client, tiny, chat_id=chat_id, **W32).status_code == 200

    def test_3_a_24k_card_leaves_less_room(self, client, sample_chat):
        small_chat, _ = sample_chat
        big_chat = make_chat(client, _card(client, 24_000))
        b_small = _boundary(small_chat, W16)
        b_big = _boundary(big_chat, W16)
        assert b_big < b_small
        mid = _module(client, b_big + 1)
        assert _enable(client, mid, chat_id=big_chat, **W16).json()["detail"] == E.OVER_BUDGET
        # ground: the same module fits beside the small card
        assert _enable(client, mid, chat_id=small_chat, **W16).status_code == 200

    def test_4_a_100_token_module_works_with_a_17626_char_card(self, client, monkeypatch):
        """Also with voice on and Limits and notebook at their ceilings: the
        card is never charged to the module rule (c8's rule would refuse)."""
        chat_id = make_chat(client, _card(client, 17_626 - 40))
        mid = _module(client, 458)
        assert _enable(client, mid, chat_id=chat_id, **W16).status_code == 200
        client.patch(f"{BASE}/{mid}", json={"enabled": False})

        monkeypatch.setattr(voice_tags, "voice_block", lambda: voice_tags.VOICE_PROMPT)
        full = {"boundary_block": "L" * (notebook_store.BOUNDARY_SET_MAX_CHARS + 216),
                "user_block": "U" * (notebook_store.NOTEBOOK_MAX_CHARS + 639),
                "model_block": ""}
        monkeypatch.setattr(notebook_store, "build_notebook_blocks",
                            lambda chat, avail, *a, **k: dict(full, excluded=[]))
        r = _enable(client, mid, chat_id=chat_id, **W16)
        assert r.status_code == 200, r.text

    def test_4b_the_notebook_counts_as_fixed_cost(self, client, sample_chat):
        chat_id, char_id = sample_chat
        b_empty = _boundary(chat_id, W16)
        for n in range(8):
            r = client.post(f"/api/v1/notebook/{chat_id}",
                            json={"text": f"Plain remembered detail number {n}, " + "z" * 150})
            assert r.status_code == 200, r.text
        mid = _module(client, b_empty)
        r = _enable(client, mid, chat_id=chat_id, **W16)
        assert r.status_code == 400 and r.json()["detail"] == E.OVER_BUDGET
        # ground: the same module in a chat with an empty notebook
        other = make_chat(client, char_id)
        assert _enable(client, mid, chat_id=other, **W16).status_code == 200

    def test_5_card_alone_overflowing_stays_context_too_large(self, client):
        chat_id = make_chat(client, _card(client, 46_000))
        mid = _module(client, 10)
        r = _enable(client, mid, chat_id=chat_id, **W16)
        assert r.status_code == 400 and r.json()["detail"] == E.CONTEXT_TOO_LARGE

    def test_no_open_chat_uses_the_reference_card(self, client, sample_chat):
        b = _boundary(None, W16)
        assert b > 0
        over = _module(client, b + 1)
        assert _enable(client, over, **W16).json()["detail"] == E.OVER_BUDGET
        fits = _module(client, b)
        assert _enable(client, fits, **W16).status_code == 200

    def test_the_layer_setting_does_not_change_the_room(self, client, sample_chat):
        """A module replaces the layer, it does not ride on it (Ek O 7)."""
        chat_id, _ = sample_chat
        b_off = _boundary(chat_id, W16)
        client.put(f"{BASE}/settings", json={"prompt_layer_enabled": True,
                                             "reply_length_words": 300})
        assert _boundary(chat_id, W16) == b_off
        mid = _module(client, b_off)
        assert _enable(client, mid, chat_id=chat_id, **W16).status_code == 200

    def test_disabling_never_checks(self, client, sample_chat):
        chat_id, _ = sample_chat
        mid = _module(client, 10)
        assert _enable(client, mid, chat_id=chat_id, **W32).status_code == 200
        r = client.patch(f"{BASE}/{mid}", json={"enabled": False, "chat_id": chat_id,
                                                "context_budget_tokens": 100,
                                                "max_tokens": 1})
        assert r.status_code == 200 and r.json()["enabled"] is False

    def test_unknown_chat_is_named(self, client):
        mid = _module(client, 10)
        r = _enable(client, mid, chat_id=9999, **W16)
        assert r.status_code == 404 and r.json()["detail"] == "chat_not_found"

    def test_a_field_too_large_is_a_named_refusal_not_a_500(self, client, sample_chat,
                                                           monkeypatch):
        import card_macros
        import prompt_layer
        chat_id, _ = sample_chat

        def too_big(*a, **k):
            raise card_macros.MacroOutputTooLarge()
        monkeypatch.setattr(prompt_layer, "module_texts", too_big)
        mid = _module(client, 10)
        r = _enable(client, mid, chat_id=chat_id, **W16)
        assert r.status_code == 400 and r.json()["detail"] == "prompt_field_too_large"

    def test_a_sanitizer_ceiling_is_the_same_named_refusal(self, client, sample_chat,
                                                           monkeypatch):
        """The sanitizer's own ceiling (PromptFieldTooLarge) is caught the
        same way as the macro engine's. Ground: the same module, unpatched,
        switches on."""
        import prompt_layer
        import prompt_sanitizer
        chat_id, _ = sample_chat
        mid = _module(client, 10)

        def too_big(*a, **k):
            raise prompt_sanitizer.PromptFieldTooLarge()
        with monkeypatch.context() as m:
            m.setattr(prompt_layer, "module_texts", too_big)
            r = _enable(client, mid, chat_id=chat_id, **W16)
            assert r.status_code == 400 and r.json()["detail"] == "prompt_field_too_large"
        assert _enable(client, mid, chat_id=chat_id, **W16).status_code == 200


# ---------------------------------------------------------------------------
# A5: toggle time and request time decide the same way
# ---------------------------------------------------------------------------

def _request_decision(client, chat_id: int) -> str:
    r = client.post(f"/api/v1/chats/{chat_id}/complete", json={
        "message": "x", "model_id": "test/model-1",
        "context_budget_tokens": W16["context_budget_tokens"],
        "generation_params": {"max_tokens": W16["max_tokens"]}})
    if r.status_code == 200:
        return "ok"
    return r.json()["detail"]


class TestOneCountingPath:
    @pytest.mark.parametrize("offset", [-40, -2, 1, 40])
    def test_toggle_and_request_agree_near_the_boundary(
            self, client, provider, sample_chat, offset):
        chat_id, _ = sample_chat
        size = _boundary(chat_id, W16) + offset
        mid = _module(client, size)
        toggle = _enable(client, mid, chat_id=chat_id, **W16)
        toggle_ok = toggle.status_code == 200
        # force the module on for the request, whatever the toggle said
        with database.get_db() as con:
            con.execute("UPDATE prompt_modules SET enabled = 1 WHERE id = ?", (mid,))
        request = _request_decision(client, chat_id)
        assert toggle_ok == (request == "ok"), (offset, toggle.json(), request)
        assert toggle_ok == (offset < 0)

    def test_a_long_comment_is_counted_as_sent_not_as_stored(self, client, provider):
        """The macro engine drops `{{// ...}}`. Stored, this module is past the
        room; prepared, it is well inside it. Both paths must say it fits.
        A small window keeps the room under the per-module cap."""
        chat_id = make_chat(client, _card(client, 500))
        win = {"context_budget_tokens": 4096, "max_tokens": 256}
        b = _boundary(chat_id, win)
        text = "Keep scenes short. {{// " + "c" * (b + 500) + "}}"
        assert b < len(text) <= 24_000
        mid = _module(client, 0, "Commented.txt", text=text)
        assert _enable(client, mid, chat_id=chat_id, **win).status_code == 200
        r = client.post(f"/api/v1/chats/{chat_id}/complete", json={
            "message": "x", "model_id": "test/model-1",
            "context_budget_tokens": win["context_budget_tokens"],
            "generation_params": {"max_tokens": win["max_tokens"]}})
        assert r.status_code == 200, r.text


class TestListCount:
    def test_enabled_chars_is_counted_on_the_prepared_text(self, client):
        mid = _module(client, 0, "c.txt", text="Hello. {{// a long private remark }}")
        client.patch(f"{BASE}/{mid}", json={"enabled": True})
        got = client.get(BASE).json()
        assert got["enabled_count"] == 1
        assert got["enabled_chars"] == len("Hello.") + SEP
        # ground: nothing on, nothing counted
        client.patch(f"{BASE}/{mid}", json={"enabled": False})
        assert client.get(BASE).json()["enabled_chars"] == 0
