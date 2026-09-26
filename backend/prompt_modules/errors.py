"""The named errors this feature can answer with.

Every code is a module constant so the error census (tests/error_enumeration.py)
can read it, and ALL_CODES is the declared alphabet for the one router site
that relays a parser's code.
"""

from __future__ import annotations

FILE_TYPE_UNSUPPORTED = "prompt_module_file_type_unsupported"
FILENAME_MISSING = "prompt_module_filename_missing"
FILENAME_INVALID = "prompt_module_filename_invalid"
FILE_TOO_LARGE = "prompt_module_file_too_large"
UPLOAD_RAW_ONLY = "prompt_module_upload_raw_only"
LENGTH_REQUIRED = "prompt_module_length_required"
NOT_UTF8 = "prompt_module_not_utf8"
EMPTY = "prompt_module_empty"
TOO_LONG = "prompt_module_too_long"
JSON_INVALID = "prompt_module_json_invalid"
JSON_UNKNOWN_KEYS = "prompt_module_json_unknown_keys"
JSON_VERSION_NEWER = "prompt_module_json_version_newer"
JSON_IS_CHARACTER_CARD = "prompt_module_json_is_character_card"
JSON_UNRECOGNIZED = "prompt_module_json_unrecognized"
JSON_NO_MODULES = "prompt_module_json_no_modules"

#: What a parser can raise. The router relays `exc.code`, so this set is the
#: alphabet of that site.
PARSE_CODES: frozenset[str] = frozenset({
    FILE_TYPE_UNSUPPORTED, FILENAME_MISSING, FILENAME_INVALID, FILE_TOO_LARGE,
    UPLOAD_RAW_ONLY,
    LENGTH_REQUIRED,
    NOT_UTF8, EMPTY, TOO_LONG, JSON_INVALID, JSON_UNKNOWN_KEYS,
    JSON_VERSION_NEWER, JSON_IS_CHARACTER_CARD, JSON_UNRECOGNIZED,
    JSON_NO_MODULES,
})

NOT_FOUND = "prompt_module_not_found"
IMPORT_NOT_FOUND = "prompt_module_import_not_found"
CANNOT_ENABLE = "prompt_module_cannot_enable"
NAME_INVALID = "prompt_module_name_invalid"
REQUEST_INVALID = "prompt_module_request_invalid"
SETTINGS_INVALID = "prompt_settings_invalid"
OVER_BUDGET = "prompt_modules_over_budget"
#: Not ours: the card alone overflowing keeps the app's existing code.
CONTEXT_TOO_LARGE = "context_too_large"

ALL_CODES: frozenset[str] = PARSE_CODES | frozenset({
    NOT_FOUND, IMPORT_NOT_FOUND, CANNOT_ENABLE, NAME_INVALID, REQUEST_INVALID,
    SETTINGS_INVALID, OVER_BUDGET, CONTEXT_TOO_LARGE,
})


class PromptModuleError(Exception):
    """A refusal with a named code, an HTTP status and optional detail fields.

    `extra` travels to the client beside `detail` (unknown key paths, the
    module names over budget). It never carries file text.
    """

    def __init__(self, code: str, status: int = 400, **extra) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.extra = extra
