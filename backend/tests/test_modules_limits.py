"""The backend's module bounds are the frontend's: pinned to shared/.

shared/prompt_module_limits.json and shared/prompt_module_schema_v1.json are
what the UI reads; prompt_modules/limits.py is what the server enforces. The
packaged build cannot read shared/, so the two are kept equal by this test.
Plus the storage: both tables exist and a second schema run is a no-op.
"""

from __future__ import annotations

import json
from pathlib import Path

import database
from prompt_modules import limits as L

SHARED = Path(__file__).resolve().parents[2] / "shared"
LIMITS = json.loads((SHARED / "prompt_module_limits.json").read_text(encoding="utf-8"))
SCHEMA = json.loads((SHARED / "prompt_module_schema_v1.json").read_text(encoding="utf-8"))


def test_every_shared_limit_equals_the_backend_constant():
    pairs = {
        "txt_md_max_bytes": L.TXT_MD_MAX_BYTES,
        "json_max_bytes": L.JSON_MAX_BYTES,
        "module_max_chars": L.MODULE_MAX_CHARS,
        "module_name_max_chars": L.MODULE_NAME_MAX_CHARS,
        "module_note_max_chars": L.MODULE_NOTE_MAX_CHARS,
        "plain_json_max_modules": L.PLAIN_JSON_MAX_MODULES,
        "module_separator": L.MODULE_SEPARATOR,
        "module_budget_share_percent": L.MODULE_BUDGET_SHARE_PERCENT,
        "reference_card_chars": L.REFERENCE_CARD_CHARS,
        "reply_length_words_allowed": list(L.REPLY_LENGTH_WORDS_ALLOWED),
        "plain_json_format": L.PLAIN_JSON_FORMAT,
        "plain_json_version": L.PLAIN_JSON_VERSION,
        "filename_header": L.FILENAME_HEADER,
        "filename_max_chars": L.FILENAME_MAX_CHARS,
    }
    for key, value in pairs.items():
        assert LIMITS[key] == value, key
    # no shared key the backend does not mirror
    assert set(LIMITS) - {"_why"} == set(pairs)


def test_the_schema_file_says_what_the_parser_enforces():
    assert SCHEMA["properties"]["format"]["const"] == L.PLAIN_JSON_FORMAT
    assert SCHEMA["properties"]["version"]["const"] == L.PLAIN_JSON_VERSION
    mods = SCHEMA["properties"]["modules"]
    assert mods["maxItems"] == L.PLAIN_JSON_MAX_MODULES and mods["minItems"] == 1
    item = mods["items"]["properties"]
    assert item["name"]["maxLength"] == L.MODULE_NAME_MAX_CHARS
    assert item["content"]["maxLength"] == L.MODULE_MAX_CHARS
    assert item["note"]["maxLength"] == L.MODULE_NOTE_MAX_CHARS
    assert SCHEMA["additionalProperties"] is False
    assert mods["items"]["additionalProperties"] is False
    assert set(SCHEMA["properties"]) == {"format", "version", "modules"}
    assert set(item) == {"name", "content", "note"}


def test_the_byte_caps_cover_the_character_cap():
    # 24,000 characters of 4-byte UTF-8 would be 96,000 bytes: the text cap
    # must hold a full module of the widest characters.
    assert L.TXT_MD_MAX_BYTES >= L.MODULE_MAX_CHARS * 4


def test_tables_exist_and_a_second_init_changes_nothing(db):
    def shape():
        with database.get_db() as con:
            return sorted(tuple(r) for r in con.execute(
                "SELECT type, name, sql FROM sqlite_master "
                "WHERE name LIKE 'prompt_module%' OR name LIKE 'idx_prompt_module%'"))
    before = shape()
    names = {r[1] for r in before}
    assert {"prompt_modules", "prompt_module_imports",
            "idx_prompt_modules_order_v1"} <= names
    with database.get_db() as con:
        con.execute("INSERT INTO prompt_module_imports (source_type) VALUES ('txt')")
    database.init_db()
    assert shape() == before
    with database.get_db() as con:
        assert con.execute("SELECT COUNT(*) FROM prompt_module_imports").fetchone()[0] == 1
