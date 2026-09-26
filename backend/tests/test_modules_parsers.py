"""Prompt module parsers: .txt, .md, the plain JSON schema and SillyTavern .json.

Behavioural: every test feeds bytes to `parse_upload` and reads what came out.
All fixtures are written here by hand with neutral text; no third-party preset
is read or fetched (9A.1, rule 6).

Gate ids: MT-6 (character cap), MT-8 (the SillyTavern fixture), H3, H5, the
plain schema (dF 6), secrets (Ek P).
"""

from __future__ import annotations

import json

import pytest

from prompt_modules import errors as E
from prompt_modules import parsers as P
from prompt_modules.limits import (
    JSON_MAX_BYTES,
    MODULE_MAX_CHARS,
    MODULE_NAME_MAX_CHARS,
    PLAIN_JSON_FORMAT,
    PLAIN_JSON_VERSION,
    TXT_MD_MAX_BYTES,
)


def _refused(filename: str, data: bytes) -> E.PromptModuleError:
    with pytest.raises(E.PromptModuleError) as info:
        P.parse_upload(filename, data)
    return info.value


def _j(obj) -> bytes:
    return json.dumps(obj).encode("utf-8")


def _real(result) -> list[P.ParsedModule]:
    return [m for m in result.modules if m.kind == P.KIND_MODULE]


# ---------------------------------------------------------------------------
# .txt / .md
# ---------------------------------------------------------------------------

class TestTextFiles:
    def test_one_file_one_module_bom_stripped(self):
        r = P.parse_upload("Scene pacing.txt", "﻿Keep scenes short.".encode())
        assert [m.content for m in r.modules] == ["Keep scenes short."]
        assert r.modules[0].name == "Scene pacing"
        assert r.source_type == P.SOURCE_TXT
        # ground: without a BOM the text is the same
        r2 = P.parse_upload("a.txt", b"Keep scenes short.")
        assert r2.modules[0].content == r.modules[0].content

    def test_markdown_is_not_rendered(self):
        r = P.parse_upload("notes.MD", b"# Heading\n\n*emphasis* stays")
        assert r.modules[0].content == "# Heading\n\n*emphasis* stays"
        assert r.source_type == P.SOURCE_MD

    def test_crlf_becomes_lf(self):
        r = P.parse_upload("a.txt", b"one\r\ntwo")
        assert r.modules[0].content == "one\ntwo"

    def test_not_utf8_is_named(self):
        assert _refused("a.txt", b"\xff\xfe\x00bad").code == E.NOT_UTF8
        # positive control: valid UTF-8 with non-ASCII letters is fine
        assert P.parse_upload("a.txt", "Çiçek ağacı".encode()).modules

    def test_blank_file_is_refused(self):
        assert _refused("a.md", b"  \n\t ").code == E.EMPTY

    def test_char_cap_exact_and_one_over(self):
        """MT-6, the character half."""
        ok = P.parse_upload("a.txt", b"x" * MODULE_MAX_CHARS)
        assert len(ok.modules[0].content) == MODULE_MAX_CHARS
        err = _refused("a.txt", b"x" * (MODULE_MAX_CHARS + 1))
        assert err.code == E.TOO_LONG

    def test_char_cap_counts_characters_not_bytes(self):
        # 24,000 two-byte letters are 48,000 bytes: under the byte cap, at the
        # character cap. One more letter is refused on characters.
        assert P.parse_upload("a.txt", ("ğ" * MODULE_MAX_CHARS).encode()).modules
        assert _refused("a.txt", ("ğ" * (MODULE_MAX_CHARS + 1)).encode()).code == E.TOO_LONG

    def test_byte_cap_is_enforced_by_the_function_too(self):
        assert _refused("a.txt", b"x" * (TXT_MD_MAX_BYTES + 1)).code == E.FILE_TOO_LARGE
        assert _refused("a.json", b" " * (JSON_MAX_BYTES + 1)).code == E.FILE_TOO_LARGE

    def test_unsupported_extension_and_missing_name(self):
        assert _refused("a.docx", b"x").code == E.FILE_TYPE_UNSUPPORTED
        assert _refused("", b"x").code == E.FILENAME_MISSING
        assert _refused("noext", b"x").code == E.FILE_TYPE_UNSUPPORTED

    def test_long_file_name_is_cut_to_the_name_cap(self):
        r = P.parse_upload("n" * 200 + ".txt", b"text")
        assert len(r.modules[0].name) == MODULE_NAME_MAX_CHARS

    def test_module_starts_off_and_fires_on_every_generation(self):
        m = P.parse_upload("a.txt", b"text").modules[0]
        assert m.st_enabled is None
        assert m.triggers == list(P.ALL_GENERATIONS)


# ---------------------------------------------------------------------------
# Plain JSON schema v1
# ---------------------------------------------------------------------------

EXAMPLE = {
    "format": PLAIN_JSON_FORMAT,
    "version": PLAIN_JSON_VERSION,
    "modules": [
        {"name": "Short replies", "content": "Keep each reply brief."},
        {"name": "Sensory detail", "content": "Include one sound {{char}} notices.",
         "note": "Never sent to the model."},
    ],
}


def _plain(mods=None, **top):
    d = {**EXAMPLE, **top}
    if mods is not None:
        d["modules"] = mods
    return d


class TestPlainJson:
    def test_example_is_valid(self):
        r = P.parse_upload("mods.json", _j(EXAMPLE))
        assert [m.name for m in r.modules] == ["Short replies", "Sensory detail"]
        assert r.modules[1].note == "Never sent to the model."
        assert r.source_type == P.SOURCE_PLAIN_JSON

    @pytest.mark.parametrize("label,doc", [
        ("top-level enabled", _plain(enabled=True)),
        ("module enabled", _plain([{"name": "x", "content": "y", "enabled": True}])),
    ])
    def test_unknown_keys_reject_the_file_and_are_listed(self, label, doc):
        err = _refused("m.json", _j(doc))
        assert err.code == E.JSON_UNKNOWN_KEYS, label
        assert err.extra["keys"] in (["$.enabled"], ["$.modules[0].enabled"])

    @pytest.mark.parametrize("label,doc", [
        ("blank content", _plain([{"name": "x", "content": "   "}])),
        ("empty list", _plain([])),
        ("name 65", _plain([{"name": "x" * 65, "content": "y"}])),
        ("content 24001", _plain([{"name": "x", "content": "y" * (MODULE_MAX_CHARS + 1)}])),
        ("blank name", _plain([{"name": " ", "content": "y"}])),
        ("note 501", _plain([{"name": "x", "content": "y", "note": "n" * 501}])),
        ("version 0", _plain(version=0)),
        ("101 modules", _plain([{"name": "x", "content": "y"}] * 101)),
    ])
    def test_schema_violations_are_named(self, label, doc):
        assert _refused("m.json", _j(doc)).code == E.JSON_INVALID, label

    def test_newer_version_says_so(self):
        err = _refused("m.json", _j(_plain(version=2)))
        assert err.code == E.JSON_VERSION_NEWER
        assert err.extra["version"] == 2

    def test_missing_format_is_not_plain(self):
        doc = {k: v for k, v in EXAMPLE.items() if k != "format"}
        assert _refused("m.json", _j(doc)).code == E.JSON_UNRECOGNIZED

    def test_boundaries_accepted(self):
        r = P.parse_upload("m.json", _j(_plain([
            {"name": "x" * MODULE_NAME_MAX_CHARS, "content": "y" * MODULE_MAX_CHARS}])))
        assert len(r.modules[0].content) == MODULE_MAX_CHARS


# ---------------------------------------------------------------------------
# .json detection order (dF 5.4)
# ---------------------------------------------------------------------------

class TestJsonDetection:
    def test_character_card_is_sent_to_character_import(self):
        assert _refused("c.json", _j({"spec": "chara_card_v2", "data": {}})).code \
            == E.JSON_IS_CHARACTER_CARD
        assert _refused("c.json", _j({"data": {"first_mes": "hi"}})).code \
            == E.JSON_IS_CHARACTER_CARD

    def test_plain_schema_wins_over_a_prompts_key(self):
        # `prompts` would make it a chat preset if detection order were wrong;
        # the unknown key then rejects it as plain JSON, which is the proof.
        err = _refused("m.json", _j({**EXAMPLE, "prompts": []}))
        assert err.code == E.JSON_UNKNOWN_KEYS

    @pytest.mark.parametrize("doc,kind", [
        ({"name": "i", "input_sequence": "", "output_sequence": ""}, "st_instruct_template"),
        ({"name": "c", "story_string": ""}, "st_context_template"),
        ({"temp": 1, "top_k": 0, "top_p": 1, "rep_pen": 1}, "st_text_completion_preset"),
        ({"name": "r", "prefix": "", "suffix": "", "separator": ""}, "st_reasoning_template"),
        ({"hello": 1}, None),
    ])
    def test_unrecognised_names_what_it_looks_like(self, doc, kind):
        err = _refused("x.json", _j(doc))
        assert err.code == E.JSON_UNRECOGNIZED
        assert err.extra["recognized_as"] == kind

    def test_invalid_json_and_non_object(self):
        assert _refused("x.json", b"{nope").code == E.JSON_INVALID
        assert _refused("x.json", b"[1,2]").code == E.JSON_INVALID
        assert _refused("x.json", b"[" * 100000 + b"]" * 100000).code == E.JSON_INVALID


# ---------------------------------------------------------------------------
# SillyTavern chat completion preset: MT-8, H3, H5
# ---------------------------------------------------------------------------

def _st_preset(order_lists=None, *, with_prompt_order=True):
    prompts = [
        {"identifier": "main", "name": "Main", "system_prompt": True,
         "role": "system", "content": "Write clear, well paced scenes."},
        {"identifier": "chatHistory", "name": "Chat History",
         "system_prompt": True, "marker": True},
        {"identifier": "charDescription", "name": "Char Description",
         "system_prompt": True, "marker": True},
        {"identifier": "style", "name": "Style note", "role": "system",
         "content": "Prefer short paragraphs.", "injection_trigger": ["normal"],
         "system_prompt": False, "marker": False},
        {"identifier": "imp", "name": "Setting only",
         "content": "Describe only the setting.", "injection_trigger": ["impersonate"]},
        {"identifier": "regen", "name": "Other angle",
         "content": "Offer a different angle.", "injection_trigger": ["swipe"]},
        {"identifier": "deep", "name": "Weather", "role": "user",
         "content": "Keep the weather consistent.", "injection_position": 1,
         "injection_depth": 4, "injection_order": 50},
        {"identifier": "jailbreak", "name": "Tone", "system_prompt": True,
         "content": "Stay within the established tone."},
        {"identifier": "loose", "name": "Time of day",
         "content": "Mention the time of day."},
        {"identifier": "extra", "name": "Macros",
         "content": "{{getvar::mood}} {{char}} {{random::a,b}} {{GetVar::x}}",
         "custom_field": 1},
    ]
    if order_lists is None:
        order_lists = [
            {"character_id": 100000, "order": [
                {"identifier": "loose", "enabled": True},
                {"identifier": "main", "enabled": False}]},
            {"character_id": 100001, "order": [
                {"identifier": "main", "enabled": True},
                {"identifier": "charDescription", "enabled": True},
                {"identifier": "style", "enabled": True},
                {"identifier": "imp", "enabled": True},
                {"identifier": "regen", "enabled": False},
                {"identifier": "deep", "enabled": True},
                {"identifier": "chatHistory", "enabled": True},
                {"identifier": "jailbreak", "enabled": True},
                {"identifier": "extra", "enabled": False},
                {"identifier": "ghost", "enabled": True},
            ]},
            {"character_id": 7, "order": [{"identifier": "main", "enabled": True}]},
        ]
    doc = {
        "temperature": 0.7,
        "openai_max_tokens": 300,
        "assistant_prefill": "Sure,",
        "impersonation_prompt": "Write as the user.",
        "chat_completion_source": "openrouter",
        "openrouter_model": "some/model",
        "reverse_proxy": "http://proxy.invalid/v1",
        "proxy_password": "not-a-real-password",
        "custom_include_headers": "Authorization: Bearer not-real",
        "my_api_key": "abc",
        "custom_note": "sk-" + "a1" * 12,
        "extensions": {"regex_scripts": [{"scriptName": "Trim spaces"}]},
        "some_future_key": 5,
        "prompts": prompts,
    }
    if with_prompt_order:
        doc["prompt_order"] = order_lists
    return doc


def _by_ident(result):
    return {m.st_identifier: m for m in result.modules}


class TestStChatPreset:
    def test_uses_the_global_list_100001(self):
        r = P.parse_upload("p.json", _j(_st_preset()))
        assert r.source_type == P.SOURCE_ST_CHAT_PRESET
        assert r.report["order_source"] == "100001"
        assert r.report["per_character_orders_unused"] == 2
        order = [m.st_identifier for m in r.modules]
        assert order[:8] == ["main", "charDescription", "style", "imp", "regen",
                             "deep", "chatHistory", "jailbreak"]

    def test_falls_back_to_100000_then_prompts_array(self):
        only_default = _st_preset([{"character_id": 100000, "order": [
            {"identifier": "loose", "enabled": True},
            {"identifier": "main", "enabled": False}]}])
        r = P.parse_upload("p.json", _j(only_default))
        assert r.report["order_source"] == "100000"
        assert [m.st_identifier for m in r.modules][:2] == ["loose", "main"]
        # ground: with no order at all, the prompts[] order stands
        r2 = P.parse_upload("p.json", _j(_st_preset(with_prompt_order=False)))
        assert r2.report["order_source"] == "prompts_array"
        assert [m.st_identifier for m in r2.modules][:2] == ["main", "chatHistory"]

    def test_markers_are_structure_rows_never_modules(self):
        r = P.parse_upload("p.json", _j(_st_preset()))
        by = _by_ident(r)
        assert by["chatHistory"].kind == P.KIND_MARKER
        assert by["charDescription"].kind == P.KIND_MARKER
        assert by["chatHistory"].triggers == []
        assert set(r.report["markers"]) == {"chatHistory", "charDescription"}
        # positive control: a non-marker from the same file is a module
        assert by["style"].kind == P.KIND_MODULE

    def test_nothing_is_dropped(self):
        doc = _st_preset()
        r = P.parse_upload("p.json", _j(doc))
        idents = {m.st_identifier for m in r.modules}
        assert idents == {p["identifier"] for p in doc["prompts"]}
        assert r.report["order_ids_missing_from_prompts"] == ["ghost"]
        assert r.report["modules_created"] + r.report["structure_rows"] == len(doc["prompts"])

    def test_after_history_badge_includes_the_jailbreak_slot(self):
        by = _by_ident(P.parse_upload("p.json", _j(_st_preset())))
        assert by["jailbreak"].after_history
        assert P.BADGE_AFTER_HISTORY in by["jailbreak"].badges
        # ground: an entry before chatHistory carries no such badge
        assert not by["style"].after_history
        assert P.BADGE_AFTER_HISTORY not in by["style"].badges

    def test_trigger_mapping(self):
        by = _by_ident(P.parse_upload("p.json", _j(_st_preset())))
        assert by["main"].triggers == ["send", "regenerate"]      # no trigger list
        assert by["style"].triggers == ["send"]                   # normal
        assert by["regen"].triggers == ["regenerate"]             # swipe
        assert by["imp"].triggers == []                           # impersonate only
        assert P.BADGE_NEVER_TRIGGERS in by["imp"].badges
        assert P.BADGE_REGENERATE_ONLY in by["regen"].badges
        assert by["imp"].st_injection_trigger == ["impersonate"]

    def test_never_triggered_entry_is_stored_and_reported(self):
        r = P.parse_upload("p.json", _j(_st_preset()))
        assert _by_ident(r)["imp"].content == "Describe only the setting."
        assert r.report["never_triggered"] == [
            {"identifier": "imp", "triggers": ["impersonate"]}]

    def test_role_and_depth_are_kept_with_badges(self):
        m = _by_ident(P.parse_upload("p.json", _j(_st_preset())))["deep"]
        assert (m.st_role, m.st_injection_position, m.st_injection_depth,
                m.st_injection_order) == ("user", 1, 4, 50)
        assert {P.BADGE_ROLE_USER, P.BADGE_IN_CHAT_DEPTH} <= set(m.badges)

    def test_preset_state_is_kept_and_everything_starts_off(self):
        r = P.parse_upload("p.json", _j(_st_preset()))
        by = _by_ident(r)
        assert by["style"].st_enabled is True
        assert by["regen"].st_enabled is False
        assert by["loose"].st_enabled is None
        assert P.BADGE_NOT_IN_ORDER in by["loose"].badges
        assert r.report["all_start_off"] is True

    def test_prefill_and_samplers_are_reported_not_turned_into_modules(self):
        r = P.parse_upload("p.json", _j(_st_preset()))
        names = {m.name for m in r.modules}
        assert "Sure," not in {m.content for m in r.modules}
        assert "assistant_prefill" not in names
        fields = {f["key"]: f for f in r.report["preset_fields"]}
        assert fields["assistant_prefill"]["group"] == "prefill"
        assert fields["assistant_prefill"]["applied"] is False
        assert fields["temperature"]["value"] == 0.7
        assert fields["impersonation_prompt"]["chars"] == len("Write as the user.")
        assert fields["openrouter_model"]["value_hidden"] is True
        assert fields["some_future_key"]["group"] == "unknown"
        assert fields["extensions"]["regex_names"] == ["Trim spaces"]

    def test_secrets_are_removed_before_anything_is_kept(self):
        r = P.parse_upload("p.json", _j(_st_preset()))
        kept = json.dumps(r.preset_json) + json.dumps(r.report) + "".join(
            json.dumps(m.raw_entry) for m in r.modules)
        for secret in ("not-a-real-password", "Bearer not-real",
                       "proxy.invalid", "abc", "a1a1a1a1"):
            assert secret not in kept, secret
        assert set(r.report["secrets_removed"]) >= {
            "$.proxy_password", "$.custom_include_headers", "$.reverse_proxy",
            "$.my_api_key", "$.custom_note"}
        fields = {f["key"]: f for f in r.report["preset_fields"]}
        assert fields["proxy_password"]["removed"] is True
        # ground: a non-secret field survives in the kept copy
        assert r.preset_json["temperature"] == 0.7
        # prompt text is the user's and is never value-scanned
        doc = _st_preset()
        doc["prompts"][0]["content"] = "Mention sk-" + "b2" * 12 + " here."
        r2 = P.parse_upload("p.json", _j(doc))
        assert "sk-" in _by_ident(r2)["main"].content

    def test_unknown_entry_keys_are_listed_and_kept_raw(self):
        r = P.parse_upload("p.json", _j(_st_preset()))
        assert {"identifier": "extra", "keys": ["custom_field"]} in \
            r.report["unknown_entry_keys"]
        assert _by_ident(r)["extra"].raw_entry["custom_field"] == 1

    def test_unprocessed_macros_are_counted_by_name(self):
        r = P.parse_upload("p.json", _j(_st_preset()))
        um = r.report["unprocessed_macros"]
        assert um.get("getvar") == 2 and um.get("random") == 1
        assert "char" not in um

    def test_over_cap_entry_is_stored_with_a_badge(self):
        doc = _st_preset()
        doc["prompts"][8]["content"] = "z" * (MODULE_MAX_CHARS + 1)
        m = _by_ident(P.parse_upload("p.json", _j(doc)))["loose"]
        assert len(m.content) == MODULE_MAX_CHARS + 1
        assert P.BADGE_OVER_CHAR_CAP in m.badges

    def test_st_warning_key_is_in_the_report(self):
        assert P.WARNING_ST_BEHAVIOUR in P.parse_upload(
            "p.json", _j(_st_preset())).report["warnings"]
        # ground: a text file carries no SillyTavern warning
        assert P.parse_upload("a.txt", b"x").report["warnings"] == []

    def test_only_markers_is_no_modules(self):
        doc = {"prompts": [{"identifier": "chatHistory", "marker": True}]}
        assert _refused("p.json", _j(doc)).code == E.JSON_NO_MODULES


class TestStPromptManagerExport:
    def test_flat_order_is_used_directly(self):
        doc = {"version": 1, "type": "full", "data": {
            "prompts": [
                {"identifier": "a1", "name": "First", "content": "One."},
                {"identifier": "b2", "name": "Second", "content": "Two."},
            ],
            "prompt_order": [
                {"identifier": "b2", "enabled": True},
                {"identifier": "chatHistory", "enabled": True},
                {"identifier": "a1", "enabled": False},
            ]}}
        r = P.parse_upload("pm.json", _j(doc))
        assert r.source_type == P.SOURCE_ST_PROMPT_MANAGER
        assert r.report["order_source"] == "flat"
        assert [m.st_identifier for m in r.modules] == ["b2", "chatHistory", "a1"]
        by = _by_ident(r)
        assert by["chatHistory"].kind == P.KIND_MARKER
        assert by["a1"].after_history and not by["b2"].after_history
        assert (by["b2"].st_enabled, by["a1"].st_enabled) == (True, False)


class TestStSysprompt:
    def test_content_and_post_history_become_two_modules(self):
        r = P.parse_upload("s.json", _j({"name": "Narrator", "content": "Narrate calmly.",
                                         "post_history": "Stay in scene."}))
        assert r.source_type == P.SOURCE_ST_SYSPROMPT
        assert [m.content for m in r.modules] == ["Narrate calmly.", "Stay in scene."]
        assert r.modules[1].after_history
        assert P.BADGE_AFTER_HISTORY in r.modules[1].badges
        # ground: no post_history, one module
        r2 = P.parse_upload("s.json", _j({"name": "Narrator", "content": "Narrate."}))
        assert len(r2.modules) == 1


class TestStMasterExport:
    def test_sysprompt_imported_others_listed_unsupported(self):
        doc = {"instruct": {"name": "i", "input_sequence": "", "output_sequence": ""},
               "sysprompt": {"name": "Guide", "content": "Guide the scene."},
               "srw": {"value": "Well,", "show": False}}
        r = P.parse_upload("master.json", _j(doc))
        assert r.source_type == P.SOURCE_ST_MASTER
        assert [m.content for m in r.modules] == ["Guide the scene."]
        assert {u["section"] for u in r.report["unsupported_sections"]} == {"instruct", "srw"}

    def test_master_without_sysprompt_is_named(self):
        doc = {"context": {"name": "c", "story_string": "x"}}
        err = _refused("master.json", _j(doc))
        assert err.code == E.JSON_NO_MODULES
        assert err.extra["unsupported_sections"] == ["context"]


class TestSecretsNested:
    def _doc(self):
        return {
            "openai_max_tokens": 300,          # a sampler, NOT a secret
            "vertexai_region": "somewhere",
            "extensions": {"deep": {"api_key": "nested-secret-1",
                                    "list": [{"Authorization": "nested-secret-2"},
                                             {"session_cookie": "nested-secret-3"}]}},
            "openrouter_providers": {"headers": {"X-Any": "nested-secret-4"}},
            "prompts": [{"identifier": "a", "name": "A", "content": "Alpha.",
                         "access_token": "nested-secret-5",
                         "meta": {"proxy": "nested-secret-6", "pwd": "nested-secret-7"}}],
        }

    def test_secrets_at_any_depth_never_reach_storage_or_report(self):
        r = P.parse_upload("p.json", _j(self._doc()))
        stored = json.dumps(r.preset_json) + json.dumps(r.modules[0].raw_entry)             + json.dumps(r.report)
        for n in range(1, 8):
            assert f"nested-secret-{n}" not in stored, n
        assert set(r.report["secrets_removed"]) >= {
            "$.extensions.deep.api_key", "$.extensions.deep.list[0].Authorization",
            "$.extensions.deep.list[1].session_cookie",
            "$.openrouter_providers.headers", "$.prompts[0].access_token",
            "$.prompts[0].meta.proxy", "$.prompts[0].meta.pwd"}

    def test_lookalike_keys_survive(self):
        """Ground: a key that only resembles a secret word is kept."""
        r = P.parse_upload("p.json", _j(self._doc()))
        kept = r.preset_json
        assert kept["openai_max_tokens"] == 300
        assert kept["vertexai_region"] == "somewhere"
        assert r.modules[0].content == "Alpha."

    def test_deep_nesting_is_a_named_refusal_not_a_crash(self):
        deep = "[" * 5000 + "]" * 5000
        doc = '{"prompts": [{"identifier": "a", "content": "x", "junk": ' + deep + "}]}"
        assert _refused("p.json", doc.encode()).code == E.JSON_INVALID
        mid = "[" * 900 + "]" * 900   # parses, then the walks must not crash
        doc = '{"prompts": [{"identifier": "a", "content": "x", "junk": ' + mid + "}]}"
        try:
            r = P.parse_upload("p.json", doc.encode())
            assert r.modules[0].content == "x"
        except E.PromptModuleError as exc:
            assert exc.code == E.JSON_INVALID


class TestNoEntryTextOutsideItsRow:
    """Deleting a module must remove its text from the vault: the import
    record keeps preset fields only, and each raw entry holds its own text."""

    def test_preset_json_carries_no_entry_text(self):
        r = P.parse_upload("p.json", _j(_st_preset()))
        blob = json.dumps(r.preset_json)
        for m in _real(r):
            if m.content:
                assert m.content not in blob, m.st_identifier
        assert "prompt_order" in r.preset_json      # structure is kept
        assert "prompts" not in r.preset_json

    def test_prompt_manager_envelope_without_entries(self):
        doc = {"version": 1, "type": "full", "data": {
            "prompts": [{"identifier": "a", "name": "A", "content": "Entry text A."}],
            "prompt_order": [{"identifier": "a", "enabled": True}]}}
        r = P.parse_upload("pm.json", _j(doc))
        assert "Entry text A." not in json.dumps(r.preset_json)
        assert r.preset_json["data"]["prompt_order"] == doc["data"]["prompt_order"]

    def test_sysprompt_modules_hold_only_their_own_text(self):
        r = P.parse_upload("s.json", _j({"name": "N", "content": "Main words.",
                                         "post_history": "After words."}))
        main, after = r.modules
        assert "After words." not in json.dumps(main.raw_entry)
        assert "Main words." not in json.dumps(after.raw_entry)
        assert "Main words." not in json.dumps(r.preset_json)
        assert "After words." not in json.dumps(r.preset_json)

    def test_master_export_sysprompt_text_only_in_rows(self):
        doc = {"sysprompt": {"name": "G", "content": "Guide words."},
               "context": {"name": "c", "story_string": "s"}}
        r = P.parse_upload("m.json", _j(doc))
        assert "Guide words." not in json.dumps(r.preset_json)
        assert r.preset_json["context"] == {"name": "c", "story_string": "s"}

    def test_plain_json_keeps_only_the_envelope(self):
        r = P.parse_upload("m.json", _j(EXAMPLE))
        assert r.preset_json == {"format": PLAIN_JSON_FORMAT,
                                 "version": PLAIN_JSON_VERSION}


class TestOddNumbers:
    def test_superscript_digit_is_not_a_crash(self):
        """W2 M1: "12²".isdigit() is True and int() refuses it."""
        doc = {"prompts": [{"identifier": "a", "name": "A", "content": "x",
                            "injection_depth": "12²", "injection_order": "7"}],
               "prompt_order": [{"character_id": "1²", "order": []}]}
        r = P.parse_upload("p.json", _j(doc))
        m = r.modules[0]
        assert m.st_injection_depth is None
        assert m.st_injection_order == 7          # positive control
        assert m.raw_entry["injection_depth"] == "12²"   # kept raw

    def test_huge_digit_string_is_not_parsed(self):
        doc = {"prompts": [{"identifier": "a", "content": "x",
                            "injection_depth": "9" * 5000}]}
        assert P.parse_upload("p.json", _j(doc)).modules[0].st_injection_depth is None
