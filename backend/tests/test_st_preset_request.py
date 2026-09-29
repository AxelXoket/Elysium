"""An imported SillyTavern preset builds the request the way SillyTavern does.

Every test imports a small synthetic preset through the real upload route,
switches parts on through the real PATCH route (which runs the toggle-time
budget check), sends through POST /chats/{id}/complete with the fake
provider, and reads what the provider was handed. Nothing here copies a real
preset; tests/fixtures/st_layered_preset.json is written for these tests
and only mirrors the shape of a large community preset.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.parse import quote

import pytest

import database
import prompt_layer
from conftest import get_messages, make_chat, make_persona
from openrouter import OpenRouterError
from prompt_modules import parsers, placement, st_regex, store
from prompt_modules import variables as pm_variables
from prompt_modules.limits import FILENAME_HEADER

MODEL = "test/model-1"
BASE = "/api/v1/prompt-modules"
FIXTURES = Path(__file__).parent / "fixtures"
SEP = prompt_layer.PART_SEP
JOIN = placement.SYSTEM_JOIN


# -- helpers --
def _entry(ident, content="", role="system", pos=0, depth=4, order=None, **kw):
    e = {"identifier": ident, "name": ident, "content": content, "role": role,
         "injection_position": pos, "injection_depth": depth}
    if order is not None:
        e["injection_order"] = order
    e.update(kw)
    return e


def _marker(ident):
    return {"identifier": ident, "name": ident, "system_prompt": True, "marker": True}


def _preset(entries: list[dict], order: list[str] | None = None,
            off: tuple[str, ...] = (), extra_prompts: list[dict] = (),
            regex: list[dict] | None = None, **texts) -> dict:
    """A chat completion preset. `order` defaults to the entries' own order;
    `off` are order ids the preset had off; `extra_prompts` are entries that
    are not in the order list at all. The new-chat and example-chat lines are
    empty unless `texts` sets them, so most tests read the chat from its
    first message."""
    ids = order if order is not None else [e["identifier"] for e in entries]
    doc = {"new_chat_prompt": "", "new_example_chat_prompt": "", **texts,
           "prompts": [*entries, *extra_prompts],
           "prompt_order": [{"character_id": 100001, "order": [
               {"identifier": i, "enabled": i not in off} for i in ids]}]}
    if regex is not None:
        doc["extensions"] = {"regex_scripts": regex}
    return doc


def _upload(client, doc: dict, filename: str = "preset.json") -> dict:
    r = client.post(f"{BASE}/import", content=json.dumps(doc).encode(),
                    headers={FILENAME_HEADER: quote(filename),
                             "Content-Type": "application/octet-stream"})
    assert r.status_code == 201, r.text
    return r.json()


def _enable(client, rows: list[dict], *idents: str, chat: int | None = None) -> None:
    by_ident = {r["st_identifier"]: r for r in rows}
    for ident in idents:
        body = {"enabled": True}
        if chat is not None:
            body["chat_id"] = chat
        r = client.patch(f"{BASE}/{by_ident[ident]['id']}", json=body)
        assert r.status_code == 200, r.text


def _disable(client, rows: list[dict], *idents: str) -> None:
    by_ident = {r["st_identifier"]: r for r in rows}
    for ident in idents:
        r = client.patch(f"{BASE}/{by_ident[ident]['id']}", json={"enabled": False})
        assert r.status_code == 200, r.text


def _import_on(client, doc: dict, *idents: str) -> dict:
    """Import and switch on `idents` (default: every entry that is a module)."""
    res = _upload(client, doc)
    mods = [r for r in res["modules"] if r["kind"] == "module"]
    _enable(client, mods, *(idents or [m["st_identifier"] for m in mods]))
    return res


def _card(client, **fields) -> int:
    body = {"name": "Mira", "first_mes": "", **fields}
    r = client.post("/api/v1/characters", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _chat(client, history=(), **card) -> int:
    chat = make_chat(client, _card(client, **card))
    with database.get_db() as con:
        for role, text in history:
            con.execute("INSERT INTO messages (chat_id, role, content) "
                        "VALUES (?, ?, ?)", (chat, role, text))
    return chat


def _send(client, provider, chat: int, text: str = "NEW", **extra) -> list[dict]:
    r = client.post(f"/api/v1/chats/{chat}/complete",
                    json={"message": text, "model_id": MODEL, **extra})
    assert r.status_code == 200, r.text
    return provider.calls[-1]["messages"]


def _shape(msgs: list[dict]) -> list[tuple[str, str]]:
    return [(m["role"], m["content"] if isinstance(m["content"], str)
             else json.dumps(m["content"])) for m in msgs]


def _index(msgs, needle: str) -> int:
    hits = [i for i, m in enumerate(msgs) if needle in m["content"]]
    assert len(hits) == 1, (needle, _shape(msgs))
    return hits[0]


HIST = (("user", "H1 user"), ("assistant", "H2 reply"),
        ("user", "H3 user"), ("assistant", "H4 reply"))


# -- plain modules: byte for byte what they were --
def test_plain_modules_send_exactly_what_they_sent_before(client, provider):
    """The golden was recorded from the build before this change, with the
    same scenario; plain modules and the fixed card block must not move by a
    byte. {{setvar}} in a plain file stays raw text, as it always did."""
    golden = json.loads((FIXTURES / "plain_modules_request_golden.json")
                        .read_text(encoding="utf-8"))
    r = client.post("/api/v1/characters", json={
        "name": "Mira", "first_mes": "Welcome, {{user}}.",
        "description": "A keeper of {{char}}'s light.", "personality": "Dry.",
        "scenario": "A storm.", "mes_example": "<START>\n{{char}}: Mind the steps.",
        "system_prompt": "Speak plainly.", "post_history_instruction": "Stay brief."})
    chat = make_chat(client, r.json()["id"])
    make_persona(client, "Nova", "A sailor.", select=True)
    with database.get_db() as con:
        for name, text in (("rules.txt", "Plain rule one for {{user}}.\n{{setvar::x::y}}"),
                           ("style.md", "## Style\nShort lines.")):
            imp = store.insert_import(con, parsers.parse_upload(name, text.encode()), name)
            store.set_enabled(con, [x["id"] for x in store.rows_for_import(con, imp)], True)
        for role, text in (("user", "first question"), ("assistant", "first answer")):
            con.execute("INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)",
                        (chat, role, text))
    msgs = _send(client, provider, chat, "second question")
    got = json.loads(json.dumps(msgs).replace(prompt_layer.chat_salt(chat), "SALT"))
    assert got == golden
    # Ground: the golden is not empty and really holds the modules.
    assert "Plain rule one for Nova." in golden[0]["content"]
    with database.get_db() as con:
        assert pm_variables.rows_for_chat(con, chat) == 0, "a plain turn keeps no variables"


def test_a_plain_module_stays_in_the_head_next_to_a_preset(client, provider):
    """A .txt imported after a preset sorts after its chatHistory placeholder;
    it still goes before the chat, where it went with no preset on."""
    chat = _chat(client, HIST)
    _import_on(client, _preset([_entry("main", "MAIN"), _marker("chatHistory"),
                                _entry("jailbreak", "POST")]))
    with database.get_db() as con:
        imp = store.insert_import(con, parsers.parse_upload("n.txt", b"NOTES"), "n.txt")
        store.set_enabled(con, [x["id"] for x in store.rows_for_import(con, imp)], True)
    msgs = _send(client, provider, chat)
    assert _index(msgs, "NOTES") == 0 and msgs[0]["role"] == "system"
    assert _index(msgs, "POST") == len(msgs) - 1
    # Ground control: the same file with the preset off is in the head too.
    with database.get_db() as con:
        con.execute("UPDATE prompt_modules SET enabled = 0 WHERE source_type != 'txt'")
    msgs = _send(client, provider, chat)
    assert _index(msgs, "NOTES") == 0


def test_each_preset_is_placed_against_its_own_chat_history(client, provider):
    """Two presets on at once: the second one's before-the-chat part sorts
    after the first one's placeholder, and still goes before the chat."""
    chat = _chat(client, HIST)
    _import_on(client, _preset([_entry("main", "FIRST-HEAD"), _marker("chatHistory"),
                                _entry("jailbreak", "FIRST-POST")]))
    _import_on(client, _preset([_entry("second", "SECOND-HEAD"), _marker("chatHistory"),
                                _entry("second_post", "SECOND-POST")]))
    msgs = _send(client, provider, chat)
    new_at = _index(msgs, "NEW")
    assert _index(msgs, "SECOND-HEAD") < new_at
    assert _index(msgs, "FIRST-POST") > new_at and _index(msgs, "SECOND-POST") > new_at


# -- relative parts in list order at their role --
def test_relative_parts_keep_list_order_and_role(client, provider):
    chat = _chat(client, HIST)
    _import_on(client, _preset([
        _entry("main", "MAIN"), _entry("u_rel", "USER-REL", role="user"),
        _entry("s_mid", "SYS-MID"), _entry("a_rel", "ASST-REL", role="assistant"),
        _marker("chatHistory"), _entry("jailbreak", "POST")]))
    msgs = _send(client, provider, chat)
    roles = [m["role"] for m in msgs]
    assert roles[:4] == ["system", "user", "system", "assistant"]
    assert msgs[0]["content"].endswith("MAIN")
    assert msgs[1]["content"] == "USER-REL" and msgs[3]["content"] == "ASST-REL"
    # The card block closes the head (no placeholders), then the chat,
    # oldest first, the new message, then the post-history part.
    assert msgs[4]["content"].startswith("[Character: Mira]")
    chat_part = [m["content"] for m in msgs[5:-1]]
    assert chat_part[0] == "H1 user" and chat_part[-1] == "NEW"
    assert msgs[-1] == {"role": "system", "content": "POST"}


def test_consecutive_system_parts_are_joined(client, provider):
    chat = _chat(client)
    _import_on(client, _preset([_entry("main", "ONE"), _entry("two", "TWO"),
                                _entry("u", "U", role="user"), _entry("three", "THREE"),
                                _entry("four", "FOUR"), _marker("chatHistory")]))
    msgs = _send(client, provider, chat)
    assert msgs[0]["content"] == prompt_layer.chat_salt(chat) + JOIN + "ONE" + JOIN + "TWO"
    assert msgs[1] == {"role": "user", "content": "U"}
    assert msgs[2]["content"].startswith("THREE" + JOIN + "FOUR")
    # Chat messages are never joined, even two in a row with one role.
    chat2 = _chat(client, (("user", "A"), ("user", "B")))
    msgs = _send(client, provider, chat2)
    assert [m["content"] for m in msgs[-3:]] == ["A", "B", "NEW"]


# -- in-chat parts at depth, order and role --
def test_depth_zero_goes_after_the_new_message(client, provider):
    chat = _chat(client, HIST)
    _import_on(client, _preset([_entry("main", "MAIN"), _marker("chatHistory"),
                                _entry("d0", "DEPTH0", role="user", pos=1, depth=0)]))
    msgs = _send(client, provider, chat)
    assert [m["content"] for m in msgs[-2:]] == ["NEW", "DEPTH0"]
    assert msgs[-1]["role"] == "user"


def test_depths_count_chat_messages_from_the_end(client, provider):
    chat = _chat(client, HIST)
    _import_on(client, _preset([
        _entry("main", "MAIN"), _marker("chatHistory"),
        _entry("d1", "DEPTH1", role="system", pos=1, depth=1),
        _entry("d2", "DEPTH2", role="assistant", pos=1, depth=2),
        _entry("d9", "DEPTH9", role="user", pos=1, depth=9)]))
    msgs = _send(client, provider, chat)
    seq = [m["content"] for m in msgs[1:]]
    # chat: H1 H2 H3 H4 NEW. depth 1 before NEW, depth 2 before H4, depth 9
    # deeper than the chat: before the oldest message.
    assert seq == ["DEPTH9", "H1 user", "H2 reply", "H3 user", "DEPTH2",
                   "H4 reply", "DEPTH1", "NEW"]


def test_same_depth_merges_by_order_and_role(client, provider):
    """Same depth + order + role: one message, "\\n"-joined in list order.
    Lower order first. Inside one order: assistant, user, system."""
    chat = _chat(client, HIST)
    _import_on(client, _preset([
        _entry("main", "MAIN"), _marker("chatHistory"),
        _entry("s100", "S100", role="system", pos=1, depth=0),
        _entry("u100a", "U100a", role="user", pos=1, depth=0),
        _entry("a100", "A100", role="assistant", pos=1, depth=0),
        _entry("u100b", "U100b", role="user", pos=1, depth=0),
        _entry("u50", "U50", role="user", pos=1, depth=0, order=50)]))
    msgs = _send(client, provider, chat)
    tail = _shape(msgs[_index(msgs, "NEW") + 1:])
    assert tail == [("user", "U50"), ("assistant", "A100"),
                    ("user", "U100a\nU100b"), ("system", "S100")]


def test_in_chat_parts_are_never_trimmed(client, provider):
    """A tight context drops old chat messages, never an in-chat part."""
    long_hist = tuple((("user", "U" * 3000), ("assistant", "A" * 3000)) * 4)
    chat = _chat(client, long_hist)
    _import_on(client, _preset([_entry("main", "MAIN"), _marker("chatHistory"),
                                _entry("deep", "DEEP-PART", role="user", pos=1, depth=6)]))
    msgs = _send(client, provider, chat, context_budget_tokens=4000,
                 generation_params={"max_tokens": 100})
    assert len([m for m in msgs if m["content"] in ("U" * 3000, "A" * 3000)]) < 8, \
        "ground: the budget really trimmed history"
    assert any(m["content"] in ("U" * 3000, "A" * 3000) for m in msgs), \
        "ground: some history is kept"
    i = _index(msgs, "DEEP-PART")
    # Deeper than what was kept: right before the oldest message kept.
    assert msgs[i + 1]["content"] in ("U" * 3000, "A" * 3000)


def test_an_in_chat_part_without_depth_is_reported_and_never_fires(client):
    doc = _preset([_entry("main", "MAIN"), _marker("chatHistory"),
                   _entry("nodepth", "ND", role="user", pos=1, depth=None),
                   _entry("badrole", "BR", role="narrator", pos=1, depth=0),
                   _entry("fine", "FINE", role="user", pos=1, depth=0)])
    res = _upload(client, doc)
    got = {e["identifier"]: e["reason"] for e in res["report"]["in_chat_not_sent"]}
    assert got == {"nodepth": "no_depth", "badrole": "bad_role"}
    rows = {r["st_identifier"]: r for r in res["modules"]}
    assert parsers.BADGE_IN_CHAT_NOT_SENT in rows["nodepth"]["badges"]
    assert rows["nodepth"]["can_enable"] is False
    # Positive control: the well-formed one can be switched on.
    assert rows["fine"]["can_enable"] is True
    assert parsers.BADGE_IN_CHAT_NOT_SENT not in rows["fine"]["badges"]


# -- Post-History Instructions and the card's own text --
def test_post_history_goes_after_depth_zero(client, provider):
    chat = _chat(client, HIST)
    _import_on(client, _preset([_entry("main", "MAIN"), _marker("chatHistory"),
                                _entry("d0", "D0", role="user", pos=1, depth=0),
                                _entry("jailbreak", "PRESET-POST")]))
    msgs = _send(client, provider, chat)
    assert _shape(msgs[-3:]) == [("user", "NEW"), ("user", "D0"),
                                 ("system", "PRESET-POST")]


def test_the_cards_post_history_text_replaces_the_presets(client, provider):
    chat = _chat(client, HIST, post_history_instruction="CARD-POST [{{original}}]")
    _import_on(client, _preset([_entry("main", "MAIN"), _marker("chatHistory"),
                                _entry("jailbreak", "PRESET-POST")]))
    msgs = _send(client, provider, chat)
    assert msgs[-1] == {"role": "system", "content": "CARD-POST [PRESET-POST]"}
    # Once, not twice: the card's final note is not also in the head.
    assert sum("CARD-POST" in m["content"] for m in msgs) == 1


def test_only_the_first_original_is_the_presets_text(client, provider):
    chat = _chat(client, HIST, post_history_instruction="A{{original}}B{{ original }}C")
    _import_on(client, _preset([_entry("main", "MAIN"), _marker("chatHistory"),
                                _entry("jailbreak", "POST")]))
    assert _send(client, provider, chat)[-1]["content"] == "APOSTBC"


@pytest.mark.parametrize("jailbreak", [
    pytest.param({"forbid_overrides": True}, id="forbidden"),
    pytest.param(None, id="entry off"),
])
def test_the_cards_post_history_text_needs_the_presets_slot(client, provider, jailbreak):
    """Forbidden, or the preset's own entry off: the card's text is not sent."""
    chat = _chat(client, HIST, post_history_instruction="CARD-POST")
    entries = [_entry("main", "MAIN"), _marker("chatHistory"),
               _entry("jailbreak", "PRESET-POST", **(jailbreak or {}))]
    _import_on(client, _preset(entries, off=() if jailbreak else ("jailbreak",)),
               *(("main", "jailbreak") if jailbreak else ("main",)))
    msgs = _send(client, provider, chat)
    assert all("CARD-POST" not in m["content"] for m in msgs)
    if jailbreak:
        assert msgs[-1]["content"] == "PRESET-POST"


def test_the_cards_system_prompt_replaces_the_main_prompt(client, provider):
    chat = _chat(client, HIST, system_prompt="CARDSYS <{{original}}>{{original}}")
    _import_on(client, _preset([_entry("main", "MAINTEXT{{setvar::m::1}}"),
                                _entry("reader", "m=[{{getvar::m}}]"),
                                _marker("chatHistory")]))
    head = _send(client, provider, chat)[0]["content"]
    # The main prompt's text is replaced, its setvar still ran.
    assert head.startswith(prompt_layer.chat_salt(chat) + JOIN + "CARDSYS <MAINTEXT>"
                           + JOIN + "m=[1]")
    assert "[System Prompt]" not in head


def test_without_a_main_prompt_the_cards_system_prompt_is_not_sent(client, provider):
    chat = _chat(client, HIST, system_prompt="CARDSYS")
    _import_on(client, _preset([_entry("main", "MAINTEXT"), _entry("other", "OTHER"),
                                _marker("chatHistory")], off=("main",)), "other")
    msgs = _send(client, provider, chat)
    assert all("CARDSYS" not in m["content"] for m in msgs)
    # Ground: the same card with the main prompt on sends it.
    _enable(client, [r for r in client.get(BASE).json()["modules"]
                     if r["kind"] == "module"], "main")
    assert "CARDSYS" in _send(client, provider, chat)[0]["content"]


# -- markers: card fields at their slots --
def test_card_fields_go_to_their_placeholders(client, provider):
    make_persona(client, "Nova", "PERSONA-DESC", select=True)
    chat = _chat(client, HIST, description="CARD-DESC", personality="CARD-PERS",
                 scenario="CARD-SCEN", mes_example="<START>\n{{char}}: EX-LINE")
    _import_on(client, _preset([
        _entry("main", "MAIN"), _entry("between", "BETWEEN", role="user"),
        _marker("personaDescription"), _marker("charDescription"),
        _entry("after_desc", "AFTER-DESC", role="user"),
        _marker("charPersonality"), _marker("scenario"),
        _marker("dialogueExamples"), _marker("chatHistory")]))
    msgs = _send(client, provider, chat)
    text = [m["content"] for m in msgs]
    i_desc = _index(msgs, "CARD-DESC")
    # The persona description as it is, no name header, joined with the
    # description by the squash.
    assert msgs[i_desc]["content"] == "PERSONA-DESC\nCARD-DESC", "squashed with one newline"
    assert _index(msgs, "BETWEEN") < i_desc < _index(msgs, "AFTER-DESC") \
        < _index(msgs, "CARD-PERS")
    assert msgs[_index(msgs, "CARD-PERS")]["content"] == "CARD-PERS" + JOIN + "CARD-SCEN"
    assert "[Description]" not in "".join(text)
    assert "[Character: Mira]" not in "".join(text)
    assert "[User Persona" not in "".join(text)


def test_examples_go_as_sillytavern_splits_them(client, provider):
    make_persona(client, "Nova", "", select=True)
    chat = _chat(client, HIST, mes_example=(
        "<START>\nlead line\n{{user}}: hi there\nmore\n{{char}}: hello {{user}}\n"
        "<START>\n{{char}}: second block\n<START>\nno speaker here"))
    _import_on(client, _preset([_entry("main", "MAIN"), _marker("dialogueExamples"),
                                _marker("chatHistory")],
                               new_example_chat_prompt="[Example Chat]"))
    msgs = _send(client, provider, chat)
    start = _index(msgs, "lead line") - 1
    assert msgs[start:start + 5] == [
        {"role": "system", "content": "[Example Chat]"},
        {"role": "system", "content": "lead line\n hi there\nmore", "name": "example_user"},
        {"role": "system", "content": "hello Nova", "name": "example_assistant"},
        {"role": "system", "content": "[Example Chat]"},
        {"role": "system", "content": "second block", "name": "example_assistant"},
    ]
    assert all("no speaker here" not in m["content"] for m in msgs), \
        "a block with no speaker sends nothing"


def test_the_presets_new_chat_line_opens_the_chat(client, provider):
    chat = _chat(client, HIST)
    _import_on(client, _preset([_entry("main", "MAIN"), _marker("chatHistory"),
                                _entry("deep", "DEEP", role="user", pos=1, depth=50)],
                               new_chat_prompt="[Start {{char}}]"))
    msgs = _send(client, provider, chat)
    # First in the chat block, before even the deepest in-chat part, and not
    # squashed into the head.
    assert _shape(msgs[1:4]) == [("system", "[Start Mira]"), ("user", "DEEP"),
                                 ("user", "H1 user")]
    # No chatHistory placeholder, no new-chat line (the chat is still sent).
    chat2 = _chat(client, HIST)
    _disable(client, [r for r in client.get(BASE).json()["modules"]
                      if r["kind"] == "module"], "main", "deep")
    _import_on(client, _preset([_entry("m2", "M2")], new_chat_prompt="[Start]"))
    msgs = _send(client, provider, chat2)
    assert all(m["content"] != "[Start]" for m in msgs)
    assert _index(msgs, "H1 user") > 0


def test_a_preset_without_new_chat_keys_uses_sillytaverns_defaults(client, provider):
    chat = _chat(client, HIST)
    doc = _preset([_entry("main", "MAIN"), _marker("chatHistory")])
    del doc["new_chat_prompt"]
    _import_on(client, doc)
    msgs = _send(client, provider, chat)
    assert msgs[1] == {"role": "system",
                       "content": store.PRESET_TEXT_DEFAULTS["new_chat_prompt"]}


def test_personality_and_scenario_formats_are_applied(client, provider):
    chat = _chat(client, HIST, personality="dry", scenario="storm")
    _import_on(client, _preset([_entry("main", "MAIN"), _marker("charPersonality"),
                                _marker("scenario"), _marker("chatHistory")],
                               personality_format="[{{char}} is {{personality}}]",
                               scenario_format="<{{scenario}}|{{personality}}>"))
    head = _send(client, provider, chat)[0]["content"]
    assert head.endswith("MAIN" + JOIN + "[Mira is dry]" + JOIN + "<storm|dry>")
    # An empty field stays empty: the format is not sent on its own.
    chat2 = _chat(client, HIST, personality="dry")
    head = _send(client, provider, chat2)[0]["content"]
    assert head.endswith("[Mira is dry]")


def test_card_fields_run_the_full_macro_engine(client, provider):
    chat = _chat(client, HIST, description="DESC {{setvar::d::1}}{{getvar::d}}",
                 post_history_instruction="PHI {{getvar::d}}")
    _import_on(client, _preset([_entry("main", "MAIN d={{getvar::d}}"),
                                _marker("charDescription"), _marker("chatHistory"),
                                _entry("jailbreak", "POST")]))
    msgs = _send(client, provider, chat)
    assert "MAIN d=1" in msgs[0]["content"], "the card's setvar runs before the modules"
    assert "DESC 1" in msgs[0]["content"]
    assert msgs[-1]["content"] == "PHI 1"
    # Chat text is still never run.
    msgs = _send(client, provider, chat, "{{getvar::d}}")
    assert msgs[-2]["content"] == "{{getvar::d}}"


def test_in_chat_parts_join_as_written_and_trim_once(client, provider):
    chat = _chat(client, HIST)
    _import_on(client, _preset([_entry("main", "MAIN"), _marker("chatHistory"),
                                _entry("a", "  A-part\n\n", role="user", pos=1, depth=0),
                                _entry("b", "\n\nB-part  ", role="user", pos=1, depth=0)]))
    assert _send(client, provider, chat)[-1]["content"] == "A-part\n\n\n\n\nB-part"


def test_a_relative_part_with_an_unknown_role_goes_as_system(client, provider):
    chat = _chat(client, HIST)
    res = _import_on(client, _preset([_entry("main", "MAIN"),
                                      _entry("odd", "ODD", role="narrator"),
                                      _entry("u", "U", role="user"),
                                      _marker("chatHistory")]))
    msgs = _send(client, provider, chat)
    assert msgs[0]["content"].endswith("MAIN" + JOIN + "ODD")
    assert all(m["role"] in ("system", "user", "assistant") for m in msgs)
    assert res["report"]["role_sent_as_system"] == ["odd"]


def test_depth_and_position_must_be_numbers(client, provider):
    chat = _chat(client, HIST)
    res = _import_on(client, _preset([
        _entry("main", "MAIN"), _marker("chatHistory"),
        _entry("strdepth", "STRDEPTH", role="user", pos=1, depth="0"),
        _entry("strpos", "STRPOS", role="user", pos="1", depth=0),
        _entry("toodeep", "TOODEEP", role="user", pos=1, depth=10001),
        _entry("deepest", "DEEPEST", role="user", pos=1, depth=10000)]),
        "main", "strpos", "deepest")
    reasons = {e["identifier"]: e["reason"] for e in res["report"]["in_chat_not_sent"]}
    assert reasons == {"strdepth": "no_depth", "toodeep": "too_deep"}
    msgs = _send(client, provider, chat)
    # A position written as text is relative: it stays after the chat marker.
    assert msgs[-1]["content"] == "STRPOS"
    assert _index(msgs, "DEEPEST") == 1, "the deepest allowed depth is sent"


def test_the_report_says_the_chat_is_sent_without_a_placeholder(client, provider):
    res = _upload(client, _preset([_entry("main", "MAIN")]))
    assert res["report"]["no_chat_history_marker"] is True
    res = _upload(client, _preset([_entry("main", "MAIN"), _marker("chatHistory")]))
    assert res["report"]["no_chat_history_marker"] is False


def test_without_placeholders_the_card_block_stays(client, provider):
    chat = _chat(client, HIST, description="CARD-DESC", system_prompt="CARDSYS")
    _import_on(client, _preset([_entry("main", "MAIN"), _marker("chatHistory")]))
    msgs = _send(client, provider, chat)
    assert "[Character: Mira]" + SEP + "[Description]\nCARD-DESC" in msgs[0]["content"]
    assert "[System Prompt]" not in msgs[0]["content"], "the system prompt took main"


def test_a_field_without_its_placeholder_keeps_the_labelled_block(client, provider):
    chat = _chat(client, HIST, description="CARD-DESC", scenario="CARD-SCEN")
    _import_on(client, _preset([_entry("main", "MAIN"), _marker("charDescription"),
                                _marker("scenario"), _marker("chatHistory")],
                               off=("scenario",)))
    msgs = _send(client, provider, chat)
    head = msgs[0]["content"]
    assert "[Description]" not in head and "CARD-DESC" in head
    # The preset had the scenario placeholder off: the field is still sent.
    assert "[Scenario]\nCARD-SCEN" in head


# -- prefill --
@pytest.mark.parametrize("model,thinking,refuses", [
    ("anthropic/claude-opus-4", False, False),
    ("claude-opus-4-20250514", False, False),
    ("anthropic/claude-sonnet-4.5", False, False),
    ("anthropic/claude-opus-4.6", False, True),
    ("claude-opus-4-6", False, True),
    ("anthropic/claude-sonnet-4.6", False, True),
    ("anthropic/claude-opus-4.8", False, True),
    ("anthropic/claude-sonnet-5", False, True),
    ("anthropic/claude-opus-5.1", False, True),
    ("anthropic/claude-haiku-5", False, False),
    ("anthropic/claude-fable-5", False, True),
    ("anthropic/claude-3-7-sonnet", True, True),
    ("anthropic/claude-sonnet-4.5", True, True),
    ("anthropic/claude-3.5-sonnet", True, False),
    ("google/gemini-3.6-flash", False, True),
    ("google/gemini-3.5-flash-lite", False, True),
    ("google/gemini-3-flash", False, False),
    ("google/gemini-3.5-flash", False, False),
    ("openai/gpt-5", True, False),
])
def test_prefill_rule(model, thinking, refuses):
    assert placement.model_refuses_prefill(model, thinking=thinking) is refuses


def test_a_trailing_assistant_part_becomes_user_on_a_refusing_model(client, provider):
    chat = _chat(client, HIST)
    _import_on(client, _preset([_entry("main", "MAIN"), _marker("chatHistory"),
                                _entry("prefill", "PREFILL", role="assistant")]))
    msgs = _send(client, provider, chat)
    assert msgs[-1] == {"role": "assistant", "content": "PREFILL"}, \
        "ground: a model with no rule keeps the prefill"
    r = client.post(f"/api/v1/chats/{chat}/complete",
                    json={"message": "NEW", "model_id": "anthropic/claude-opus-4.6"})
    assert r.status_code == 200, r.text
    assert provider.calls[-1]["messages"][-1] == {"role": "user", "content": "PREFILL"}


def test_an_assistant_part_before_the_chat_is_not_a_prefill(client, provider):
    chat = _chat(client, HIST)
    _import_on(client, _preset([_entry("main", "MAIN"),
                                _entry("warm", "WARM", role="assistant"),
                                _marker("chatHistory")]))
    r = client.post(f"/api/v1/chats/{chat}/complete",
                    json={"message": "NEW", "model_id": "anthropic/claude-opus-4.6"})
    assert r.status_code == 200
    msgs = provider.calls[-1]["messages"]
    assert msgs[_index(msgs, "WARM")]["role"] == "assistant"
    assert msgs[-1] == {"role": "user", "content": "NEW"}


# -- parts outside the order list --
def test_a_part_outside_the_order_can_be_switched_on_and_is_sent(client, provider):
    chat = _chat(client, HIST)
    res = _upload(client, _preset([_entry("main", "MAIN"), _marker("chatHistory")],
                                  extra_prompts=[_entry("loose", "LOOSE")]))
    rows = [r for r in res["modules"] if r["kind"] == "module"]
    loose = next(r for r in rows if r["st_identifier"] == "loose")
    assert parsers.BADGE_NOT_IN_ORDER in loose["badges"]
    _enable(client, rows, "main")
    assert all("LOOSE" not in m["content"] for m in _send(client, provider, chat))
    _enable(client, rows, "loose")
    assert _index(_send(client, provider, chat), "LOOSE") >= 0


# -- variables over all parts, before placement --
def test_variables_run_in_list_order_before_placement(client, provider):
    """Reset in the first part, set in an enabled part, read in a part that
    is placed after the chat: the read sees this turn's set. A part that is
    off never sets anything."""
    chat = _chat(client, HIST)
    res = _upload(client, _preset([
        _entry("main", "MAIN{{setvar::a::}}{{setvar::b::}}"),
        _entry("set_a", "SET-A{{setvar::a::on}}"),
        _entry("set_b", "SET-B{{setvar::b::on}}"),
        _marker("chatHistory"),
        _entry("reader", "READ a=[{{getvar::a}}] b=[{{getvar::b}}] "
               "c=[{{getvar::never_set}}]", role="user", pos=1, depth=0)]))
    rows = [r for r in res["modules"] if r["kind"] == "module"]
    _enable(client, rows, "main", "set_a", "reader")
    msgs = _send(client, provider, chat)
    assert msgs[-1]["content"] == "READ a=[on] b=[] c=[]"
    assert all("setvar" not in m["content"] for m in msgs)
    assert ("MAIN" + JOIN + "SET-A" + JOIN) in msgs[0]["content"]


def test_chat_messages_are_never_macro_evaluated(client, provider):
    chat = _chat(client, (("user", "{{setvar::x::from-chat}}"), ("assistant", "ok")))
    _import_on(client, _preset([_entry("main", "x=[{{getvar::x}}]"),
                                _marker("chatHistory")]))
    msgs = _send(client, provider, chat, "{{setvar::x::typed}} {{user}}")
    assert "x=[]" + JOIN in msgs[0]["content"]
    assert msgs[1]["content"] == "{{setvar::x::from-chat}}"
    assert msgs[-1]["content"] == "{{setvar::x::typed}} {{user}}"


def _turn_prompt(client, provider, chat, text="NEW", **kw):
    return _send(client, provider, chat, text, **kw)


def test_variables_persist_per_chat_and_regenerate_restores_them(client, provider):
    chat = _chat(client)
    res = _upload(client, _preset([_entry("setter", "{{setvar::flag::on}}SETTER"),
                                   _entry("main", "flag=[{{getvar::flag}}]"),
                                   _marker("chatHistory")]))
    rows = [r for r in res["modules"] if r["kind"] == "module"]
    _enable(client, rows, "main")
    assert _turn_prompt(client, provider, chat)[0]["content"].count("flag=[]" + JOIN) == 1
    _enable(client, rows, "setter")
    assert _turn_prompt(client, provider, chat)[0]["content"].count("flag=[on]" + JOIN) == 1
    _disable(client, rows, "setter")
    # Turn 3: nothing sets it, the value from turn 2 is still there.
    assert _turn_prompt(client, provider, chat)[0]["content"].count("flag=[on]" + JOIN) == 1
    # Another chat has its own variables.
    other = _chat(client)
    assert _turn_prompt(client, provider, other)[0]["content"].count("flag=[]" + JOIN) == 1

    # Delete turn 3, then regenerate turn 2 with the setter off: it starts
    # from the values BEFORE turn 2, so the flag is unset again.
    msgs = get_messages(client, chat)
    client.delete(f"/api/v1/chats/{chat}/messages/{msgs[4]['id']}")
    r = client.post(f"/api/v1/chats/{chat}/messages/{msgs[3]['id']}/regenerate",
                    json={"model_id": MODEL})
    assert r.status_code == 200, r.text
    assert provider.calls[-1]["messages"][0]["content"].count("flag=[]" + JOIN) == 1
    # The regenerated reply now carries the values of ITS turn.
    assert _turn_prompt(client, provider, chat)[0]["content"].count("flag=[]" + JOIN) == 1


def test_switching_variants_switches_the_values(client, provider):
    chat = _chat(client)
    res = _upload(client, _preset([_entry("setter", "{{setvar::flag::on}}S"),
                                   _entry("main", "flag=[{{getvar::flag}}]"),
                                   _marker("chatHistory")]))
    rows = [r for r in res["modules"] if r["kind"] == "module"]
    _enable(client, rows, "main", "setter")
    _turn_prompt(client, provider, chat)            # reply 1 carries flag=on
    first = get_messages(client, chat)[1]
    _disable(client, rows, "setter")
    r = client.post(f"/api/v1/chats/{chat}/messages/{first['id']}/regenerate",
                    json={"model_id": MODEL})     # variant 2 carries no flag
    assert r.status_code == 200
    assert _send(client, provider, chat, "Q2")[0]["content"].count("flag=[]" + JOIN) == 1
    # Back to variant 1: its values come back with it.
    q2 = next(m for m in get_messages(client, chat) if m["content"] == "Q2")
    client.delete(f"/api/v1/chats/{chat}/messages/{q2['id']}")
    r = client.post(f"/api/v1/chats/{chat}/messages/{first['id']}/activate")
    assert r.status_code == 200, r.text
    assert _send(client, provider, chat, "Q3")[0]["content"].count("flag=[on]" + JOIN) == 1


def test_a_failed_turn_and_a_toggle_change_no_variables(client, provider):
    chat = _chat(client)
    res = _upload(client, _preset([_entry("setter", "{{setvar::flag::on}}S"),
                                   _entry("main", "flag=[{{getvar::flag}}]"),
                                   _marker("chatHistory")]))
    rows = [r for r in res["modules"] if r["kind"] == "module"]
    # The toggle-time budget check with a chat runs the macros on a copy.
    _enable(client, rows, "main", "setter", chat=chat)
    with database.get_db() as con:
        assert pm_variables.rows_for_chat(con, chat) == 0
    provider.error = OpenRouterError("openrouter_server_error", status=500)
    r = client.post(f"/api/v1/chats/{chat}/complete",
                    json={"message": "NEW", "model_id": MODEL})
    assert r.status_code >= 400
    provider.error = None
    with database.get_db() as con:
        assert pm_variables.rows_for_chat(con, chat) == 0
    _disable(client, rows, "setter")
    assert _send(client, provider, chat)[0]["content"].count("flag=[]" + JOIN) == 1
    # Positive control: a turn that did happen keeps its values.
    with database.get_db() as con:
        assert pm_variables.rows_for_chat(con, chat) == 1


def test_deleting_or_clearing_a_chat_deletes_its_variables(client, provider):
    chat = _chat(client)
    _import_on(client, _preset([_entry("main", "{{setvar::k::v}}M"),
                                _marker("chatHistory")]))
    _send(client, provider, chat)
    other = _chat(client)
    _send(client, provider, other)
    with database.get_db() as con:
        assert pm_variables.rows_for_chat(con, chat) == 1
    assert client.post(f"/api/v1/chats/{chat}/clear").status_code == 200
    with database.get_db() as con:
        assert pm_variables.rows_for_chat(con, chat) == 0
        assert pm_variables.rows_for_chat(con, other) == 1, "only that chat's"
    assert client.delete(f"/api/v1/chats/{other}").status_code == 200
    with database.get_db() as con:
        assert pm_variables.rows_for_chat(con, other) == 0


def test_streamed_turns_keep_variables_too(client, monkeypatch):
    """The stream routes store the values with their reply as well: a send,
    then a regenerate that starts from before the send."""
    import routers.completions as cr

    seen: list[list[dict]] = []

    def fake_stream(messages, model_id, gen_params, provider, **kwargs):
        seen.append(messages)

        async def gen():
            yield "streamed reply"
        return gen()

    monkeypatch.setattr(cr, "complete_stream", fake_stream)
    chat = _chat(client)
    res = _upload(client, _preset([_entry("setter", "{{setvar::k::v}}S"),
                                   _entry("main", "k=[{{getvar::k}}]"),
                                   _marker("chatHistory")]))
    rows = [r for r in res["modules"] if r["kind"] == "module"]
    _enable(client, rows, "setter", "main")
    with client.stream("POST", f"/api/v1/chats/{chat}/complete/stream",
                       json={"message": "NEW", "model_id": MODEL}) as r:
        body = "".join(r.iter_text())
    assert '"done"' in body, body
    with database.get_db() as con:
        assert pm_variables.rows_for_chat(con, chat) == 1
    _disable(client, rows, "setter")
    reply = get_messages(client, chat)[1]
    with client.stream("POST", f"/api/v1/chats/{chat}/messages/{reply['id']}"
                       "/regenerate/stream", json={"model_id": MODEL}) as r:
        body = "".join(r.iter_text())
    assert '"done"' in body, body
    assert "k=[]" + JOIN in seen[-1][0]["content"], "regenerate starts before the turn"
    with database.get_db() as con:
        assert pm_variables.rows_for_chat(con, chat) == 2, "one per variant"


# -- history cleanup --
_STRIP = {"id": "s", "scriptName": "Strip state", "findRegex": "/<s>[\\s\\S]*?<\\/s>/g",
          "replaceString": "", "trimStrings": [], "placement": [2], "disabled": False,
          "markdownOnly": False, "promptOnly": True, "minDepth": 2, "maxDepth": None}


def _cleanup_preset(*scripts):
    return _preset([_entry("main", "MAIN"), _marker("chatHistory")],
                   regex=list(scripts))


def test_history_cleanup_is_off_by_default(client, provider):
    chat = _chat(client, (("user", "u1"), ("assistant", "r1 <s>old</s>"),
                          ("user", "u2"), ("assistant", "r2 <s>new</s>")))
    res = _import_on(client, _cleanup_preset(_STRIP))
    imports = client.get(BASE).json()["imports"]
    assert [i["history_cleanup"] for i in imports] == [False]
    assert imports[0]["cleanup_scripts"] == 1
    msgs = _send(client, provider, chat)
    assert _index(msgs, "r1 <s>old</s>") > 0 and _index(msgs, "r2 <s>new</s>") > 0
    r = client.patch(f"{BASE}/imports/{res['import_id']}", json={"history_cleanup": True})
    assert r.status_code == 200 and r.json()["history_cleanup"] is True
    # Delete the turn the first send added, so the chat is u1 r1 u2 r2 again.
    added = next(m for m in get_messages(client, chat) if m["content"] == "NEW")
    client.delete(f"/api/v1/chats/{chat}/messages/{added['id']}")
    msgs = _send(client, provider, chat)
    # r1 is depth 3, older than minDepth 2: cleaned. r2 is depth 1: kept.
    assert [m["content"] for m in msgs[1:5]] == ["u1", "r1 ", "u2", "r2 <s>new</s>"]
    # The stored messages never change.
    stored = [m["content"] for m in get_messages(client, chat)]
    assert "r1 <s>old</s>" in stored


def test_cleanup_skips_display_and_permanent_scripts(client, provider):
    display = {**_STRIP, "scriptName": "Display", "findRegex": "/u1/g",
               "replaceString": "X", "promptOnly": False, "markdownOnly": True,
               "placement": [1], "minDepth": None}
    permanent = {**display, "scriptName": "Permanent", "findRegex": "/u2/g",
                 "markdownOnly": False}
    chat = _chat(client, (("user", "u1"), ("assistant", "r1 <s>a</s>"),
                          ("user", "u2"), ("assistant", "r2")))
    res = _import_on(client, _cleanup_preset(_STRIP, display, permanent))
    rep = res["report"]["regex_scripts"]
    assert rep[st_regex.KIND_PERMANENT] == ["Permanent"]
    assert rep[st_regex.KIND_DISPLAY] == ["Display"]
    assert rep[st_regex.KIND_PROMPT] == ["Strip state"]
    client.patch(f"{BASE}/imports/{res['import_id']}", json={"history_cleanup": True})
    msgs = _send(client, provider, chat)
    contents = [m["content"] for m in msgs]
    assert "u1" in contents and "u2" in contents
    assert "r1 " in contents, "positive control: the prompt-only script ran"


def test_cleanup_replacement_follows_sillytavern(client, provider):
    script = {**_STRIP, "findRegex": "/<b>(?<inner>.*?)<\\/b> (\\w+)/",
              "replaceString": "[{{match}}|$<inner>|$2|$9|{{char}}]",
              "trimStrings": ["!"], "placement": [1], "minDepth": None}
    chat = _chat(client, (("user", "<b>hi!</b> there <b>x</b> y"), ("assistant", "r")))
    res = _import_on(client, _cleanup_preset(script))
    client.patch(f"{BASE}/imports/{res['import_id']}", json={"history_cleanup": True})
    msgs = _send(client, provider, chat, "{{char}}")
    # First match only (no g flag), groups trimmed, macros only in the
    # script's own text: the typed {{char}} stays.
    assert msgs[1]["content"] == "[<b>hi</b> there|hi|there||Mira] <b>x</b> y"
    assert msgs[-1]["content"] == "{{char}}"


def test_a_runaway_pattern_cannot_hold_the_turn():
    """Measured shapes that backtrack forever: both stop at the turn's
    budget, and the pattern is skipped afterwards."""
    for pattern, text in (("/(a|a)*b/", "a" * 40 + "c"), ("/.*.*.*x/", "y" * 600)):
        scripts = st_regex.prepare([{**_STRIP, "findRegex": pattern,
                                     "placement": [1], "minDepth": None}])
        assert scripts, "ground: the pattern was accepted"
        hist = [{"role": "user", "content": text}]
        t0 = time.perf_counter()
        st_regex.clean_chat(hist, "new", scripts, char="C", user="U", budget_s=0.5)
        assert time.perf_counter() - t0 < 5.0
        assert hist[0]["content"] == text
        key = (scripts[0].regex.pattern, scripts[0].regex.flags)
        assert key in st_regex._worker.too_slow
    # Positive control: a sound pattern still runs afterwards.
    ok = st_regex.prepare([{**_STRIP, "findRegex": "/y+/", "replaceString": "Y",
                            "placement": [1], "minDepth": None}])
    hist = [{"role": "user", "content": "yyy"}]
    st_regex.clean_chat(hist, "new", ok, char="C", user="U", budget_s=5)
    assert hist[0]["content"] == "Y"


def test_a_slow_worker_start_is_not_charged_to_the_pattern(monkeypatch):
    st_regex.stop_worker()
    real = st_regex._Worker._spawn

    def slow(self):
        time.sleep(1.0)
        return real(self)

    monkeypatch.setattr(st_regex._Worker, "_spawn", slow)
    scripts = st_regex.prepare([{**_STRIP, "findRegex": "/q/g", "replaceString": "Q",
                                 "placement": [1], "minDepth": None}])
    hist = [{"role": "user", "content": "qq"}]
    st_regex.clean_chat(hist, "new", scripts, char="C", user="U", budget_s=0.3)
    assert hist[0]["content"] == "QQ"
    assert (scripts[0].regex.pattern, scripts[0].regex.flags) not in st_regex._worker.too_slow


@pytest.mark.parametrize("literal,reason", [
    ("/(a+)+b/", st_regex.UNSUPPORTED_NESTED_REPEAT),
    ("/(x)\\1/", st_regex.UNSUPPORTED_SYNTAX),
    ("/x/gX", st_regex.UNSUPPORTED_FLAGS),
    ("/(?i)x/", st_regex.UNSUPPORTED_SYNTAX),
    ("/(?<=a+)b/", st_regex.UNSUPPORTED_SYNTAX),
    ("/x/giu", st_regex.UNSUPPORTED_FLAGS),
])
def test_unsupported_patterns_are_refused_and_reported(literal, reason):
    with pytest.raises(st_regex.UnsupportedPattern) as exc:
        st_regex.compile_js(literal)
    assert exc.value.reason == reason
    rep = st_regex.report([{**_STRIP, "findRegex": literal}])
    assert rep["unsupported"] == [{"name": "Strip state", "reason": reason}]


JS_VECTORS = json.loads((FIXTURES / "js_regex_vectors.json").read_text(encoding="utf-8"))
#: Where this app does not match JavaScript, and why. Each is refused and
#: reported, except a lone surrogate, which is sent as U+FFFD because a lone
#: surrogate cannot be encoded for the request at all.
JS_REFUSED_HERE = {"/(?<a>x)\\k<a>/g", "/\\p{L}/u", "/x/giu"}
JS_LONE_SURROGATE = {"/\\ud83d/g"}


@pytest.mark.parametrize("case", JS_VECTORS["vectors"],
                         ids=[ascii(v["find"]) + " " + ascii(v["text"])[:20]
                              for v in JS_VECTORS["vectors"]])
def test_regex_matches_what_javascript_gives(case):
    """Fixed vectors recorded from SillyTavern's own regexFromString and
    replace callback in node. A case JavaScript does not run (js null) must
    be refused here; every other case must give JavaScript's text."""
    template = st_regex._MATCH_MACRO.sub("$0", case["replace"])
    try:
        js = st_regex.compile_js(case["find"])
    except st_regex.UnsupportedPattern:
        assert case["js"] is None or case["find"] in JS_REFUSED_HERE, case
        return
    assert case["js"] is not None, "JavaScript refuses this pattern, so must we"
    got = st_regex.substitute(js.regex, js.global_, template, [], case["text"], js.utf16)
    if case["find"] in JS_LONE_SURROGATE:
        assert got == case["js"].encode("utf-16-le", "surrogatepass").decode(
            "utf-16-le", "replace")
        return
    assert got == case["js"]


def test_the_vectors_cover_the_differences_that_matter():
    """Ground for the vector test: the set holds the cases where Python's own
    reading differs from JavaScript's, so passing it means something."""
    import re as py_re

    finds = {v["find"] for v in JS_VECTORS["vectors"]}
    for needed in ("/\\bTime\\b/g", "/\\w+/g", "/./g", "/s/i", "/\u00e7/gi", "/a{,3}/g",
                   "/\\Z/g", "/$/gm", "/abc/d"):
        assert needed in finds
    # A plain Python reading of one of them really does differ.
    case = next(v for v in JS_VECTORS["vectors"] if v["find"] == "/\\w+/g")
    assert py_re.sub(r"\w+", "X", case["text"]) != case["js"]


# -- the import report --
def test_the_report_lists_what_is_not_supported(client):
    doc = json.loads((FIXTURES / "st_layered_preset.json").read_text(encoding="utf-8"))
    doc["prompts"].append(_entry("gv", "{{setglobalvar::g::1}}{{nosuch}}"))
    doc["prompts"].append(_entry("tail_a", "TAIL", role="assistant"))
    doc["prompt_order"][0]["order"].append({"identifier": "tail_a", "enabled": True})
    rep = _upload(client, doc)["report"]
    assert rep["global_variables_per_chat"] is True
    assert rep["trailing_assistant"] == ["tail_a"]
    assert rep["regex_scripts"][st_regex.KIND_PERMANENT] == ["Fix typos"]
    assert "nosuch" in rep["unprocessed_macros"]
    assert "getvar" not in rep["unprocessed_macros"]
    # Ground: a preset without any of it says so.
    plain = _upload(client, _preset([_entry("main", "M")]))["report"]
    assert plain["global_variables_per_chat"] is False
    assert plain["trailing_assistant"] == [] and "regex_scripts" not in plain


def test_history_cleanup_route_refuses_bad_bodies(client):
    res = _upload(client, _cleanup_preset(_STRIP))
    url = f"{BASE}/imports/{res['import_id']}"
    assert client.patch(url, json={"history_cleanup": "yes"}).status_code == 400
    assert client.patch(url, json={"history_cleanup": True, "x": 1}).status_code == 400
    assert client.patch(f"{BASE}/imports/999999",
                        json={"history_cleanup": True}).status_code == 404
    assert client.patch(url, json={"history_cleanup": True}).status_code == 200
    # Deleting the import's last module deletes the import and its option.
    for row in res["modules"]:
        if row["kind"] == "module":
            client.delete(f"{BASE}/{row['id']}")
    with database.get_db() as con:
        assert con.execute("SELECT COUNT(*) FROM settings WHERE key = ?",
                           (store.SETTING_HISTORY_CLEANUP_PREFIX
                            + str(res["import_id"]),)).fetchone()[0] == 0


# -- a layered preset end to end --
def test_layered_preset_end_to_end(client, provider):
    make_persona(client, "Nova", "PERSONA: a sailor.", select=True)
    chat = _chat(client, (("user", "U1"), ("assistant", "A1 <state>calm</state>"),
                          ("user", "U2"), ("assistant", "A2 <state>wary</state>")),
                 description="DESC", personality="PERS", scenario="SCEN",
                 mes_example="<START>\n{{char}}: EX", system_prompt="CARDSYS")
    raw = (FIXTURES / "st_layered_preset.json").read_bytes()
    res = _upload(client, json.loads(raw))
    r = client.post(f"{BASE}/enable-preset-defaults", json={"import_id": res["import_id"]})
    assert r.status_code == 200, r.text
    client.patch(f"{BASE}/imports/{res['import_id']}", json={"history_cleanup": True})
    msgs = _send(client, provider, chat, "U3")
    shape = [(m["role"], m["content"][:12]) for m in msgs]
    salt = prompt_layer.chat_salt(chat)
    assert shape[0] == ("system", salt[:12])
    # The card's system prompt took the Main Prompt's place (its setvars ran).
    assert msgs[0]["content"].startswith(salt + JOIN + "CARDSYS" + JOIN + "STYLE")
    assert shape[1:] == [
        ("assistant", "WARMUP: I am"), ("system", "PERSONA: a s"),
        ("system", "[Example Cha"), ("system", "EX"),
        ("system", "[Start a new"),
        ("user", "U1"), ("assistant", "A1 "), ("user", "U2"),
        ("assistant", "A2 <state>wa"), ("user", "U3"),
        ("user", "STATE: end y"), ("system", "POST: stay i")]
    assert msgs[2]["content"] == JOIN.join(("PERSONA: a sailor.", "DESC", "PERS", "SCEN"))
    assert msgs[4]["name"] == placement.EXAMPLE_ASSISTANT
    assert msgs[-2]["content"].endswith("READ: mood=yes diary=yes")
    assert "PACE" not in json.dumps(msgs) and "THINK" not in json.dumps(msgs)
    assert "LOOSE" not in json.dumps(msgs), "not in the order: off by default"


# -- a chat whose variables outgrew the budget --
def test_a_chat_stuck_on_its_variables_can_clear_them(client, provider):
    chat = _chat(client)
    _import_on(client, _preset([
        _entry("main", "{{addvar::big::" + "x" * 3000 + "}}BIG={{getvar::big}}"),
        _marker("chatHistory")]))
    window = {"context_budget_tokens": 4000, "generation_params": {"max_tokens": 100}}
    assert len(_send(client, provider, chat, **window)[0]["content"]) > 3000
    r = client.post(f"/api/v1/chats/{chat}/complete",
                    json={"message": "NEW", "model_id": MODEL, **window})
    assert r.status_code == 400 and r.json()["detail"] == "prompt_modules_over_budget"
    assert r.json()["chat_variables"] is True, "the refusal says the variables grew"
    assert client.delete(f"{BASE}/variables/999999").status_code == 404
    r = client.delete(f"{BASE}/variables/{chat}")
    assert r.status_code == 200 and r.json()["cleared"] == 1
    # Unstuck: the next message starts from no variables.
    msgs = _send(client, provider, chat, **window)
    assert msgs[0]["content"].count("x") == 3000


def test_a_plain_refusal_does_not_mention_variables(client, provider):
    chat = _chat(client)
    _import_on(client, _preset([_entry("main", "y" * 9000), _marker("chatHistory")]))
    r = client.post(f"/api/v1/chats/{chat}/complete",
                    json={"message": "NEW", "model_id": MODEL,
                          "context_budget_tokens": 4000,
                          "generation_params": {"max_tokens": 100}})
    assert r.status_code == 400 and r.json()["detail"] == "prompt_modules_over_budget"
    assert "chat_variables" not in r.json()
