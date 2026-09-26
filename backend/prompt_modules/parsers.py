"""Turn an uploaded file into prompt modules plus an import report.

Four kinds of file, told apart by extension and then, for .json, by shape
(dF 5.4, first match wins):

  .txt / .md   one module each, UTF-8, BOM stripped, text sent as text
  .json        1. {"format": "elysium.prompt-modules"}: the app's plain schema
               2. a character card: refused, it belongs in character import
               3. top-level `prompts` list: SillyTavern chat completion preset
               4. `data.prompts` list: SillyTavern Prompt Manager export
               5. an instruct/context/sysprompt/preset/reasoning/srw object:
                  SillyTavern master export (only `sysprompt` becomes modules)
               6. `name` + `content`: SillyTavern system prompt template
               7. anything else: refused, naming what it looks like if it can

Nothing is dropped in silence (H5, Ek O 1). Every entry is kept, markers as
structure rows; every field is kept in the entry's raw JSON; everything the app
does not act on is listed in the report. The one deliberate exception is
secrets: connection passwords, headers, proxy URLs and anything shaped like an
API key are deleted before anything is stored, and the report says which keys
were removed (Ek P, H arbitration).

Every module starts OFF (H3). The preset's own enabled state is kept in
`st_enabled` so the "enable what SillyTavern had on" button can use it.

This module never logs file text, entry names or content.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from pathlib import PurePath

from . import errors as E
from .limits import (
    FILENAME_MAX_CHARS,
    JSON_MAX_BYTES,
    MODULE_MAX_CHARS,
    MODULE_NAME_MAX_CHARS,
    MODULE_NOTE_MAX_CHARS,
    PLAIN_JSON_FORMAT,
    PLAIN_JSON_MAX_MODULES,
    PLAIN_JSON_VERSION,
    TXT_MD_MAX_BYTES,
)

# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------

KIND_MODULE = "module"
KIND_MARKER = "marker"

SOURCE_TXT = "txt"
SOURCE_MD = "md"
SOURCE_PLAIN_JSON = "plain_json"
SOURCE_ST_CHAT_PRESET = "st_chat_preset"
SOURCE_ST_PROMPT_MANAGER = "st_prompt_manager"
SOURCE_ST_MASTER = "st_master_export"
SOURCE_ST_SYSPROMPT = "st_sysprompt"

ST_SOURCES = frozenset({SOURCE_ST_CHAT_PRESET, SOURCE_ST_PROMPT_MANAGER,
                        SOURCE_ST_MASTER, SOURCE_ST_SYSPROMPT})

#: The app's generations an entry can ride on. "send" is an ordinary turn (and
#: an edit, which resends); "regenerate" is a new variant of a reply.
GEN_SEND = "send"
GEN_REGENERATE = "regenerate"
ALL_GENERATIONS = (GEN_SEND, GEN_REGENERATE)

#: SillyTavern generation types -> ours. `continue`, `impersonate` and `quiet`
#: have no counterpart here, so an entry that lists only those never fires,
#: exactly as it never fired in SillyTavern outside those generations.
_ST_TRIGGER_MAP = {
    "normal": GEN_SEND,
    "regenerate": GEN_REGENERATE,
    "swipe": GEN_REGENERATE,
}

# Badges, as stable keys the UI translates.
BADGE_AFTER_HISTORY = "after_history"
BADGE_IN_CHAT_DEPTH = "in_chat_depth"
BADGE_ROLE_USER = "role_user"
BADGE_ROLE_ASSISTANT = "role_assistant"
BADGE_EXTENSION = "extension"
BADGE_NOT_IN_ORDER = "not_in_order"
BADGE_REGENERATE_ONLY = "trigger_regenerate_only"
BADGE_SEND_ONLY = "trigger_send_only"
BADGE_NEVER_TRIGGERS = "never_triggers"
BADGE_OVER_CHAR_CAP = "over_char_cap"
BADGE_EMPTY = "empty_content"

#: The warning-3 key ("this preset was written for SillyTavern").
WARNING_ST_BEHAVIOUR = "st_preset_behaviour"

#: Fields of SillyTavern's Prompt class (st_pm.js:82-194) plus `enabled`.
_ST_PROMPT_FIELDS = frozenset({
    "identifier", "name", "content", "role", "system_prompt", "marker",
    "position", "injection_position", "injection_depth", "injection_order",
    "injection_trigger", "forbid_overrides", "extension", "enabled",
})

#: SillyTavern's built-in marker identifiers (st_pm.js defaults). A Prompt
#: Manager export leaves markers out of `prompts` but keeps them in the order,
#: so an order id from this set is a structure row, not a missing entry.
_ST_MARKER_IDS = frozenset({
    "chatHistory", "dialogueExamples", "worldInfoBefore", "worldInfoAfter",
    "charDescription", "charPersonality", "scenario", "personaDescription",
})
_CHAT_HISTORY_ID = "chatHistory"

#: character_id of SillyTavern's global order (st_openai.js:698), then the
#: Prompt Manager default dummy id (st_pm.js:336).
ORDER_GLOBAL_ID = 100001
ORDER_DEFAULT_ID = 100000

# Preset-level fields, grouped as in dF 5.3. Stored in the kept JSON, never
# applied; listed in the report.
_PRESET_GROUPS: dict[str, frozenset[str]] = {
    "sampler": frozenset({
        "temperature", "top_p", "top_k", "top_a", "min_p",
        "frequency_penalty", "presence_penalty", "repetition_penalty", "seed",
        "n", "openai_max_context", "openai_max_tokens", "max_context_unlocked",
        "reasoning_effort", "verbosity", "show_thoughts"}),
    "generation_text": frozenset({
        "impersonation_prompt", "continue_nudge_prompt", "group_nudge_prompt",
        "new_group_chat_prompt", "send_if_empty"}),
    "structure_text": frozenset({
        "new_chat_prompt", "new_example_chat_prompt", "wi_format",
        "scenario_format", "personality_format"}),
    "prefill": frozenset({
        "assistant_prefill", "assistant_impersonation", "continue_prefill",
        "continue_postfix"}),
    "format": frozenset({
        "names_behavior", "squash_system_messages", "use_sysprompt",
        "media_inlining", "inline_image_quality", "function_calling",
        "tool_call_recurse_limit", "stream_openai", "request_images",
        "request_image_aspect_ratio", "request_image_resolution",
        "enable_web_search", "tool_reasoning_mode", "bypass_status_check"}),
    "logit_bias": frozenset({"bias_preset_selected"}),
    "extensions": frozenset({"extensions"}),
}
_CONNECTION_EXACT = frozenset({
    "chat_completion_source", "reverse_proxy", "proxy_password", "custom_url",
    "custom_include_body", "custom_exclude_body", "custom_include_headers",
    "custom_prompt_post_processing", "zai_endpoint", "siliconflow_endpoint",
    "minimax_endpoint", "pollinations_endpoint", "nanogpt_provider",
    "nanogpt_payg_override", "workers_ai_account_id", "group_models",
    "sort_models", "show_external_models",
})
_CONNECTION_PREFIXES = ("openrouter_", "azure_", "vertexai_")

# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------

#: Keys deleted wherever they appear, at any depth, before anything is stored
#: or reported: the kept copy, each entry's raw JSON and the report alike.
_SECRET_KEYS_EXACT = frozenset({
    "proxy_password", "custom_include_headers", "reverse_proxy", "custom_url",
    "custom_include_body",
})
#: A credential named inside a compound key ("openrouter_apikey").
_SECRET_SUBSTRINGS = ("password", "passwd", "secret", "apikey", "api_key",
                      "api-key", "authorization", "cookie", "credential",
                      "private_key", "bearer")
#: Whole segments of a key split on _ - . and space. Segment match, not
#: substring, for `token`: `openai_max_tokens` is a sampler setting.
_SECRET_SEGMENTS = frozenset({"token", "headers", "header", "proxy", "auth",
                              "key", "pass", "pwd", "session"})
_SEGMENT_SPLIT = re.compile(r"[_\-.\s]+")
#: A value shaped like a provider key, in a field that is not prompt text.
_KEY_VALUE_RE = re.compile(r"\b(sk|pk|rk)-[A-Za-z0-9_\-]{16,}")


#: Where a camelCase key changes word: "accessToken" -> "access Token",
#: "APIKey" -> "API Key".
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
#: A key that ENDS in one of these, whatever its case ("ACCESSTOKEN",
#: "refreshtoken"). `max_tokens` does not end in "token", so samplers stay.
_SECRET_SUFFIXES = ("token", "key")


def _is_secret_key(key: str) -> bool:
    k = key.lower()
    if k in _SECRET_KEYS_EXACT or any(s in k for s in _SECRET_SUBSTRINGS):
        return True
    if k.endswith(_SECRET_SUFFIXES):
        return True
    words = _CAMEL_BOUNDARY.sub(" ", key).lower()
    return any(seg in _SECRET_SEGMENTS for seg in _SEGMENT_SPLIT.split(words))


def strip_secrets(obj, path: str = "$", *, text_paths: frozenset[str] = frozenset(),
                  removed: list[str] | None = None):
    """Return a deep copy with every secret key and key-shaped value removed.

    `removed` collects the JSON paths that were deleted (paths, never values).
    Prompt text (`prompts[i].content` and the like) is not value-scanned: that
    is the user's text and the outgoing key scrub covers it on the way out.
    """
    if removed is None:
        removed = []
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            p = f"{path}.{k}"
            if isinstance(k, str) and _is_secret_key(k):
                removed.append(p)
                continue
            if (isinstance(v, str) and p not in text_paths
                    and _KEY_VALUE_RE.search(v) and not _is_prompt_text(p)):
                removed.append(p)
                continue
            out[k] = strip_secrets(v, p, text_paths=text_paths, removed=removed)
        return out
    if isinstance(obj, list):
        return [strip_secrets(v, f"{path}[{i}]", text_paths=text_paths,
                              removed=removed) for i, v in enumerate(obj)]
    return obj


_PROMPT_TEXT_PATH = re.compile(
    r"(\.prompts\[\d+\]\.content|\.sysprompt\.(content|post_history)|"
    r"^\$\.(content|post_history))$")


def _is_prompt_text(path: str) -> bool:
    return bool(_PROMPT_TEXT_PATH.search(path))


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class ParsedModule:
    kind: str
    name: str
    content: str = ""
    note: str = ""
    st_identifier: str | None = None
    #: The preset's own state: True / False / None (entry not in the order).
    st_enabled: bool | None = None
    st_role: str | None = None
    st_system_prompt: bool | None = None
    st_marker: bool = False
    st_injection_position: int | None = None
    st_injection_depth: int | None = None
    st_injection_order: int | None = None
    st_injection_trigger: list[str] | None = None
    st_forbid_overrides: bool | None = None
    st_extension: bool | None = None
    after_history: bool = False
    #: Our generations this entry fires on; [] means never.
    triggers: list[str] = field(default_factory=lambda: list(ALL_GENERATIONS))
    badges: list[str] = field(default_factory=list)
    #: The entry exactly as the file had it, secrets removed. None for text.
    raw_entry: dict | None = None


@dataclass
class ImportResult:
    modules: list[ParsedModule]
    report: dict
    source_type: str = ""
    #: The file's preset-level fields that belong to no entry (samplers,
    #: order lists, envelope), secrets stripped, WITHOUT any entry text: each
    #: entry's text lives only in its own row, so deleting a module removes
    #: its text from the vault. Never returned, never logged.
    preset_json: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_EXT_CAPS = {".txt": TXT_MD_MAX_BYTES, ".md": TXT_MD_MAX_BYTES,
             ".json": JSON_MAX_BYTES}
#: C0 controls, DEL and C1 controls: none belongs in a file name.
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def extension_of(filename: str) -> str:
    """The lower-cased extension, or raise the named error.

    The name is stored with the import and shown in the report, so a name
    longer than any file system allows, or one carrying a control character
    (NUL included), is refused rather than kept (W1 r2 L2).
    """
    if not filename or not filename.strip():
        raise E.PromptModuleError(E.FILENAME_MISSING)
    if len(filename) > FILENAME_MAX_CHARS or _CONTROL_CHARS.search(filename):
        raise E.PromptModuleError(E.FILENAME_INVALID,
                                  max_chars=FILENAME_MAX_CHARS)
    ext = PurePath(filename.strip()).suffix.lower()
    if ext not in _EXT_CAPS:
        raise E.PromptModuleError(E.FILE_TYPE_UNSUPPORTED, 415)
    return ext


def byte_cap_for(filename: str) -> int:
    """Checked BEFORE the body is read; the router needs it from the name."""
    return _EXT_CAPS[extension_of(filename)]


def parse_upload(filename: str, data: bytes) -> ImportResult:
    ext = extension_of(filename)
    if len(data) > _EXT_CAPS[ext]:
        # The router refuses earlier; this keeps the function safe on its own.
        raise E.PromptModuleError(E.FILE_TOO_LARGE, 413,
                                  max_bytes=_EXT_CAPS[ext])
    text = _decode(data)
    if ext in (".txt", ".md"):
        return _parse_text(filename, text,
                           SOURCE_TXT if ext == ".txt" else SOURCE_MD)
    try:
        return _parse_json(filename, text)
    except RecursionError:
        # json.loads accepts nesting deeper than the walks below can follow;
        # a file that deep is not a preset anyone wrote by hand.
        raise E.PromptModuleError(E.JSON_INVALID,
                                  problems=["nested too deeply"]) from None


def _decode(data: bytes) -> str:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise E.PromptModuleError(E.NOT_UTF8) from None
    if text.startswith("﻿"):
        text = text[1:]
    return text


def _base_report(filename: str, source_type: str) -> dict:
    return {
        "source_type": source_type,
        "filename": filename,
        "modules_created": 0,
        "structure_rows": 0,
        "all_start_off": True,
        "warnings": [WARNING_ST_BEHAVIOUR] if source_type in ST_SOURCES else [],
    }


# ---------------------------------------------------------------------------
# .txt / .md
# ---------------------------------------------------------------------------

def _stem_name(filename: str) -> str:
    stem = PurePath(filename.strip()).stem.strip()
    return (stem or "Module")[:MODULE_NAME_MAX_CHARS]


def _parse_text(filename: str, text: str, source_type: str) -> ImportResult:
    content = text.replace("\r\n", "\n")
    if not content.strip():
        raise E.PromptModuleError(E.EMPTY)
    if len(content) > MODULE_MAX_CHARS:
        raise E.PromptModuleError(E.TOO_LONG, max_chars=MODULE_MAX_CHARS,
                                  chars=len(content))
    module = ParsedModule(kind=KIND_MODULE, name=_stem_name(filename),
                          content=content)
    report = _base_report(filename, source_type)
    report["modules_created"] = 1
    report["unprocessed_macros"] = count_unprocessed_macros([content])
    return ImportResult([module], report, source_type, {})


# ---------------------------------------------------------------------------
# .json dispatch
# ---------------------------------------------------------------------------

def _parse_json(filename: str, text: str) -> ImportResult:
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, RecursionError, ValueError):
        raise E.PromptModuleError(E.JSON_INVALID, problems=["not valid JSON"]) from None
    if not isinstance(payload, dict):
        raise E.PromptModuleError(E.JSON_INVALID,
                                  problems=["top level is not an object"])

    # 1. The app's own schema.
    if payload.get("format") == PLAIN_JSON_FORMAT:
        return _parse_plain(filename, payload)
    # 2. A character card.
    if _looks_like_card(payload):
        raise E.PromptModuleError(E.JSON_IS_CHARACTER_CARD)
    # 3. Chat completion preset.
    if isinstance(payload.get("prompts"), list):
        return _parse_st_preset(filename, payload, SOURCE_ST_CHAT_PRESET)
    # 4. Prompt Manager export.
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("prompts"), list):
        return _parse_st_preset(filename, payload, SOURCE_ST_PROMPT_MANAGER)
    # 5. Master export.
    if any(isinstance(payload.get(k), dict) for k in _MASTER_SECTIONS):
        return _parse_master(filename, payload)
    # 6. System prompt template.
    if isinstance(payload.get("name"), str) and isinstance(payload.get("content"), str):
        return _parse_sysprompt(filename, payload)
    # 7. Nothing we read.
    raise E.PromptModuleError(E.JSON_UNRECOGNIZED,
                              recognized_as=_guess_other_st_kind(payload))


def _looks_like_card(p: dict) -> bool:
    if p.get("spec") in ("chara_card_v2", "chara_card_v3"):
        return True
    d = p.get("data")
    return isinstance(d, dict) and "first_mes" in d


_MASTER_SECTIONS = ("instruct", "context", "sysprompt", "preset", "reasoning", "srw")
_MASTER_SECTION_NAMES = {
    "instruct": "Instruct Template", "context": "Context Template",
    "sysprompt": "System Prompt", "preset": "Text Completion Preset",
    "reasoning": "Reasoning Formatting", "srw": "Start Reply With",
}


def _guess_other_st_kind(p: dict) -> str | None:
    """Name a SillyTavern file we recognise but do not import (st_presetmgr)."""
    keys = set(p)
    if {"name", "input_sequence", "output_sequence"} <= keys:
        return "st_instruct_template"
    if {"name", "story_string"} <= keys:
        return "st_context_template"
    if {"temp", "top_k", "top_p", "rep_pen"} <= keys:
        return "st_text_completion_preset"
    if {"name", "prefix", "suffix", "separator"} <= keys:
        return "st_reasoning_template"
    return None


# ---------------------------------------------------------------------------
# Plain JSON, schema v1 (shared/prompt_module_schema_v1.json)
# ---------------------------------------------------------------------------

_PLAIN_TOP_KEYS = ("format", "version", "modules")
_PLAIN_ITEM_KEYS = ("name", "content", "note")


def _parse_plain(filename: str, p: dict) -> ImportResult:
    unknown = [f"$.{k}" for k in p if k not in _PLAIN_TOP_KEYS]
    mods = p.get("modules")
    if isinstance(mods, list):
        for i, m in enumerate(mods):
            if isinstance(m, dict):
                unknown += [f"$.modules[{i}].{k}" for k in m
                            if k not in _PLAIN_ITEM_KEYS]
    if unknown:
        raise E.PromptModuleError(E.JSON_UNKNOWN_KEYS, keys=unknown)

    version = p.get("version")
    if isinstance(version, int) and not isinstance(version, bool) \
            and version > PLAIN_JSON_VERSION:
        raise E.PromptModuleError(E.JSON_VERSION_NEWER, version=version)

    problems: list[str] = []
    if "version" not in p:
        problems.append("$.version is required")
    elif version != PLAIN_JSON_VERSION or isinstance(version, bool):
        problems.append(f"$.version must be {PLAIN_JSON_VERSION}")
    if not isinstance(mods, list):
        problems.append("$.modules must be a list")
        mods = []
    elif not 1 <= len(mods) <= PLAIN_JSON_MAX_MODULES:
        problems.append(f"$.modules must hold 1 to {PLAIN_JSON_MAX_MODULES} items")

    out: list[ParsedModule] = []
    for i, m in enumerate(mods):
        at = f"$.modules[{i}]"
        if not isinstance(m, dict):
            problems.append(f"{at} must be an object")
            continue
        name, content, note = m.get("name"), m.get("content"), m.get("note", "")
        if not isinstance(name, str) or not name.strip() \
                or len(name) > MODULE_NAME_MAX_CHARS:
            problems.append(f"{at}.name must be 1 to {MODULE_NAME_MAX_CHARS} "
                            "characters, not only spaces")
        if not isinstance(content, str) or not content.strip() \
                or len(content) > MODULE_MAX_CHARS:
            problems.append(f"{at}.content must be 1 to {MODULE_MAX_CHARS} "
                            "characters, not only spaces")
        if not isinstance(note, str) or len(note) > MODULE_NOTE_MAX_CHARS:
            problems.append(f"{at}.note must be text of at most "
                            f"{MODULE_NOTE_MAX_CHARS} characters")
        if isinstance(name, str) and isinstance(content, str) \
                and isinstance(note, str):
            out.append(ParsedModule(kind=KIND_MODULE, name=name,
                                    content=content, note=note,
                                    raw_entry=copy.deepcopy(m)))
    if problems:
        raise E.PromptModuleError(E.JSON_INVALID, problems=problems)

    report = _base_report(filename, SOURCE_PLAIN_JSON)
    report["modules_created"] = len(out)
    report["unprocessed_macros"] = count_unprocessed_macros(
        [m.content for m in out])
    return ImportResult(out, report, SOURCE_PLAIN_JSON,
                        {"format": p["format"], "version": p["version"]})


# ---------------------------------------------------------------------------
# SillyTavern chat completion preset and Prompt Manager export
# ---------------------------------------------------------------------------

def _as_int(v) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    # ASCII digits only, and short: str.isdigit() is true for "12²",
    # which int() then refuses, and an unbounded digit string is a slow int().
    if isinstance(v, str) and _SHORT_INT.fullmatch(v.strip()):
        return int(v.strip())
    return None


_SHORT_INT = re.compile(r"-?[0-9]{1,9}")


def _as_bool(v) -> bool | None:
    return v if isinstance(v, bool) else None


def map_triggers(raw) -> list[str]:
    """SillyTavern `injection_trigger` -> our generations.

    Mirrors `shouldTrigger` (st_pm.js:1549-1553): not a list, or an empty list,
    fires on every generation.
    """
    if not isinstance(raw, list) or not raw:
        return list(ALL_GENERATIONS)
    got = {_ST_TRIGGER_MAP[t] for t in raw if isinstance(t, str) and t in _ST_TRIGGER_MAP}
    return [g for g in ALL_GENERATIONS if g in got]


def _pick_order(payload: dict, source_type: str) -> tuple[list | None, str, int]:
    """(order list, which list it was, how many per-character lists went unused)."""
    if source_type == SOURCE_ST_PROMPT_MANAGER:
        po = payload["data"].get("prompt_order")
    else:
        po = payload.get("prompt_order")
    if not isinstance(po, list) or not po:
        return None, "prompts_array", 0
    # A flat list ({identifier, enabled}) is the Prompt Manager export shape.
    if all(isinstance(x, dict) and "identifier" in x for x in po):
        return po, "flat", 0
    lists = {}
    for x in po:
        if isinstance(x, dict) and isinstance(x.get("order"), list):
            cid = _as_int(x.get("character_id"))
            if cid is not None and cid not in lists:
                lists[cid] = x["order"]
    for cid in (ORDER_GLOBAL_ID, ORDER_DEFAULT_ID):
        if cid in lists:
            return lists[cid], str(cid), len(lists) - 1
    return None, "prompts_array", len(lists)


def _parse_st_preset(filename: str, payload: dict, source_type: str) -> ImportResult:
    removed: list[str] = []
    clean = strip_secrets(payload, removed=removed)
    container = clean["data"] if source_type == SOURCE_ST_PROMPT_MANAGER else clean
    prompts = container["prompts"]

    report = _base_report(filename, source_type)
    report["secrets_removed"] = removed

    by_id: dict[str, tuple[int, dict]] = {}
    invalid_entries: list[int] = []
    duplicate_ids: list[str] = []
    for i, entry in enumerate(prompts):
        if not isinstance(entry, dict):
            invalid_entries.append(i)
            continue
        ident = entry.get("identifier")
        ident = ident if isinstance(ident, str) and ident else f"#{i}"
        if ident in by_id:
            # ST keeps the last one (mergeKeepNewer); keep both here, the
            # later one under a suffixed id so nothing vanishes.
            duplicate_ids.append(ident)
            ident = f"{ident}#{i}"
        by_id[ident] = (i, entry)

    order, order_source, unused_lists = _pick_order(clean, source_type)
    report["order_source"] = order_source
    report["per_character_orders_unused"] = unused_lists

    sequence: list[tuple[str, dict | None, bool | None, bool]] = []
    # (identifier, entry or None for an order-only marker, st_enabled, in_order)
    seen: set[str] = set()
    missing_from_prompts: list[str] = []
    if order is not None:
        for item in order:
            if not isinstance(item, dict):
                continue
            ident = item.get("identifier")
            if not isinstance(ident, str) or ident in seen:
                continue
            en = _as_bool(item.get("enabled"))
            if ident in by_id:
                seen.add(ident)
                sequence.append((ident, by_id[ident][1], en, True))
            elif ident in _ST_MARKER_IDS:
                seen.add(ident)
                sequence.append((ident, None, en, True))
            else:
                missing_from_prompts.append(ident)
        for ident, (_i, entry) in by_id.items():
            if ident not in seen:
                sequence.append((ident, entry, None, False))
    else:
        for ident, (_i, entry) in by_id.items():
            sequence.append((ident, entry, _as_bool(entry.get("enabled")), True))

    history_at = next((n for n, s in enumerate(sequence)
                       if s[0] == _CHAT_HISTORY_ID), None)

    modules: list[ParsedModule] = []
    unknown_entry_keys: list[dict] = []
    never_triggered: list[dict] = []
    badge_counts: dict[str, int] = {}
    for n, (ident, entry, st_enabled, in_order) in enumerate(sequence):
        after = history_at is not None and n > history_at
        if entry is None:
            modules.append(ParsedModule(
                kind=KIND_MARKER, name=ident, st_identifier=ident,
                st_enabled=st_enabled, st_marker=True, after_history=after,
                triggers=[], raw_entry=None))
            continue
        pm = _entry_to_module(ident, entry, st_enabled, in_order, after)
        extra = sorted(k for k in entry if k not in _ST_PROMPT_FIELDS)
        if extra:
            unknown_entry_keys.append({"identifier": ident, "keys": extra})
        if pm.kind == KIND_MODULE and not pm.triggers:
            never_triggered.append({"identifier": ident,
                                    "triggers": pm.st_injection_trigger or []})
        for b in pm.badges:
            badge_counts[b] = badge_counts.get(b, 0) + 1
        modules.append(pm)

    real = [m for m in modules if m.kind == KIND_MODULE]
    if not real:
        raise E.PromptModuleError(E.JSON_NO_MODULES)

    top_fields = {k: v for k, v in container.items()
                  if k not in ("prompts", "prompt_order")}
    report.update({
        "modules_created": len(real),
        "structure_rows": len(modules) - len(real),
        "markers": [m.st_identifier for m in modules if m.kind == KIND_MARKER],
        "badges": badge_counts,
        "never_triggered": never_triggered,
        "order_ids_missing_from_prompts": missing_from_prompts,
        "invalid_entries": invalid_entries,
        "duplicate_identifiers": duplicate_ids,
        "unknown_entry_keys": unknown_entry_keys,
        "preset_fields": _report_preset_fields(top_fields, removed),
        "unprocessed_macros": count_unprocessed_macros(
            [m.content for m in real]),
    })
    if source_type == SOURCE_ST_PROMPT_MANAGER:
        report["export_envelope"] = {
            "version": clean.get("version"), "type": clean.get("type"),
            "unknown_keys": sorted(k for k in clean
                                   if k not in ("version", "type", "data")),
        }
    if source_type == SOURCE_ST_PROMPT_MANAGER:
        kept = {k: v for k, v in clean.items() if k != "data"}
        kept["data"] = {k: v for k, v in container.items() if k != "prompts"}
    else:
        kept = {k: v for k, v in clean.items() if k != "prompts"}
    return ImportResult(modules, report, source_type, kept)


def _entry_to_module(ident: str, entry: dict, st_enabled: bool | None,
                     in_order: bool, after: bool) -> ParsedModule:
    marker = entry.get("marker") is True
    raw_name = entry.get("name")
    name = raw_name if isinstance(raw_name, str) and raw_name.strip() else ident
    content = entry.get("content") if isinstance(entry.get("content"), str) else ""
    role = entry.get("role") if isinstance(entry.get("role"), str) else None
    trig_raw = entry.get("injection_trigger")
    trig_list = [t for t in trig_raw if isinstance(t, str)] \
        if isinstance(trig_raw, list) else None
    pm = ParsedModule(
        kind=KIND_MARKER if marker else KIND_MODULE,
        name=name, content=content if not marker else "",
        st_identifier=ident, st_enabled=st_enabled, st_role=role,
        st_system_prompt=_as_bool(entry.get("system_prompt")),
        st_marker=marker,
        st_injection_position=_as_int(entry.get("injection_position")),
        st_injection_depth=_as_int(entry.get("injection_depth")),
        st_injection_order=_as_int(entry.get("injection_order")),
        st_injection_trigger=trig_list,
        st_forbid_overrides=_as_bool(entry.get("forbid_overrides")),
        st_extension=_as_bool(entry.get("extension")),
        after_history=after,
        triggers=[] if marker else map_triggers(trig_raw),
        raw_entry=copy.deepcopy(entry),
    )
    if marker:
        # A marker that does carry text keeps it in raw_entry; it is still a
        # placeholder in SillyTavern and never a module here.
        return pm
    b = pm.badges
    if after:
        b.append(BADGE_AFTER_HISTORY)
    if pm.st_injection_position == 1:
        b.append(BADGE_IN_CHAT_DEPTH)
    if role == "user":
        b.append(BADGE_ROLE_USER)
    elif role == "assistant":
        b.append(BADGE_ROLE_ASSISTANT)
    if pm.st_extension:
        b.append(BADGE_EXTENSION)
    if not in_order:
        b.append(BADGE_NOT_IN_ORDER)
    if not pm.triggers:
        b.append(BADGE_NEVER_TRIGGERS)
    elif pm.triggers == [GEN_REGENERATE]:
        b.append(BADGE_REGENERATE_ONLY)
    elif pm.triggers == [GEN_SEND]:
        b.append(BADGE_SEND_ONLY)
    if not content.strip():
        b.append(BADGE_EMPTY)
    elif len(content) > MODULE_MAX_CHARS:
        b.append(BADGE_OVER_CHAR_CAP)
    return pm


def _group_of(key: str) -> str:
    for group, keys in _PRESET_GROUPS.items():
        if key in keys:
            return group
    if key in _CONNECTION_EXACT or key.endswith("_model") \
            or key.startswith(_CONNECTION_PREFIXES):
        return "connection"
    return "unknown"


def _report_preset_fields(fields: dict, removed: list[str]) -> list[dict]:
    """One line per preset-level key: its group and what we did with it.

    Numbers and booleans are shown; text is reported by length; connection
    values are never shown. Keys removed as secrets are listed as removed.
    """
    out: list[dict] = []
    removed_top = {p.split(".", 2)[1] for p in removed
                   if p.count(".") >= 1 and p.startswith("$.")}
    for key in sorted(set(fields) | {k for k in removed_top
                                     if k not in ("prompts", "prompt_order", "data")}):
        group = _group_of(key)
        line = {"key": key, "group": group, "applied": False}
        if key not in fields:
            line["removed"] = True
            out.append(line)
            continue
        v = fields[key]
        if group == "connection":
            line["value_hidden"] = True
        elif key == "extensions" and isinstance(v, dict):
            line["keys"] = sorted(v)
            scripts = v.get("regex_scripts")
            if isinstance(scripts, list):
                line["regex_scripts"] = len(scripts)
                line["regex_names"] = [s.get("scriptName") for s in scripts
                                       if isinstance(s, dict)
                                       and isinstance(s.get("scriptName"), str)]
        elif isinstance(v, (bool, int, float)) or v is None:
            line["value"] = v
        elif isinstance(v, str):
            line["chars"] = len(v)
        else:
            line["type"] = type(v).__name__
        out.append(line)
    return out


# ---------------------------------------------------------------------------
# System prompt template and master export
# ---------------------------------------------------------------------------

_SYSPROMPT_KEYS = ("name", "content", "post_history")


def _sysprompt_modules(obj: dict, filename: str) -> tuple[list[ParsedModule], list[str]]:
    name = obj.get("name") if isinstance(obj.get("name"), str) and obj["name"].strip() \
        else _stem_name(filename)
    unknown = sorted(k for k in obj if k not in _SYSPROMPT_KEYS)
    mods: list[ParsedModule] = []
    content = obj.get("content") if isinstance(obj.get("content"), str) else ""
    ph = obj.get("post_history") if isinstance(obj.get("post_history"), str) else ""
    for text, suffix, after in ((content, "", False), (ph, " (post-history)", True)):
        if suffix and not text.strip():
            continue
        # Each module keeps only its own text in its raw entry, so deleting
        # one never leaves the other's text behind, and vice versa.
        raw = {k: copy.deepcopy(v) for k, v in obj.items()
               if k not in ("content", "post_history")}
        raw["post_history" if after else "content"] = text
        pm = ParsedModule(kind=KIND_MODULE, name=(name + suffix),
                          content=text, st_identifier=("post_history" if after
                                                       else "content"),
                          after_history=after, raw_entry=raw)
        if after:
            pm.badges.append(BADGE_AFTER_HISTORY)
        if not text.strip():
            pm.badges.append(BADGE_EMPTY)
        elif len(text) > MODULE_MAX_CHARS:
            pm.badges.append(BADGE_OVER_CHAR_CAP)
        mods.append(pm)
    return mods, unknown


def _parse_sysprompt(filename: str, payload: dict) -> ImportResult:
    removed: list[str] = []
    clean = strip_secrets(payload, removed=removed)
    mods, unknown = _sysprompt_modules(clean, filename)
    report = _base_report(filename, SOURCE_ST_SYSPROMPT)
    report.update({
        "modules_created": len(mods),
        "unknown_keys": unknown,
        "secrets_removed": removed,
        "badges": _count_badges(mods),
        "unprocessed_macros": count_unprocessed_macros([m.content for m in mods]),
    })
    return ImportResult(mods, report, SOURCE_ST_SYSPROMPT,
                        {k: v for k, v in clean.items()
                         if k not in ("content", "post_history")})


def _parse_master(filename: str, payload: dict) -> ImportResult:
    removed: list[str] = []
    clean = strip_secrets(payload, removed=removed)
    sections = [k for k in _MASTER_SECTIONS if isinstance(clean.get(k), dict)]
    unsupported = [{"section": k, "name": _MASTER_SECTION_NAMES[k]}
                   for k in sections if k != "sysprompt"]
    unknown_top = sorted(k for k in clean if k not in _MASTER_SECTIONS)
    sp = clean.get("sysprompt")
    mods, unknown_sp = ([], [])
    if isinstance(sp, dict) and isinstance(sp.get("content"), str):
        mods, unknown_sp = _sysprompt_modules(sp, filename)
    if not any(m.content.strip() for m in mods):
        raise E.PromptModuleError(E.JSON_NO_MODULES,
                                  unsupported_sections=[u["section"] for u in unsupported])
    report = _base_report(filename, SOURCE_ST_MASTER)
    report.update({
        "modules_created": len(mods),
        "unsupported_sections": unsupported,
        "unknown_keys": unknown_top,
        "sysprompt_unknown_keys": unknown_sp,
        "secrets_removed": removed,
        "badges": _count_badges(mods),
        "unprocessed_macros": count_unprocessed_macros([m.content for m in mods]),
    })
    kept = dict(clean)
    if isinstance(sp, dict):
        kept["sysprompt"] = {k: v for k, v in sp.items()
                             if k not in ("content", "post_history")}
    return ImportResult(mods, report, SOURCE_ST_MASTER, kept)


def _count_badges(mods: list[ParsedModule]) -> dict[str, int]:
    out: dict[str, int] = {}
    for m in mods:
        for b in m.badges:
            out[b] = out.get(b, 0) + 1
    return out


# ---------------------------------------------------------------------------
# Macros the engine leaves alone
# ---------------------------------------------------------------------------

_MACRO_RE = re.compile(r"\{\{(.*?)\}\}", re.S)


def count_unprocessed_macros(texts: list[str]) -> dict[str, int]:
    """How many `{{...}}` macros the card macro engine will leave untouched.

    Asked of the engine itself rather than of a list kept here: a macro counts
    as processed exactly when render_macros changes it. So the report cannot
    drift from what B2's engine actually handles.
    """
    from card_macros import render_macros

    counts: dict[str, int] = {}
    for text in texts:
        for m in _MACRO_RE.finditer(text):
            token = m.group(0)
            try:
                same = render_macros(token, char="\x00c", user="\x00u") == token
            except Exception:  # noqa: BLE001 - an engine refusal is "not processed"
                same = True
            if same:
                key = m.group(1).split("::", 1)[0].strip().lower() or "?"
                counts[key] = counts.get(key, 0) + 1
    return counts
