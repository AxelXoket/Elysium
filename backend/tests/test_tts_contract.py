"""The four-place rule, enforced instead of remembered.

Every voice error code has to exist in four files at once, or a real failure
reaches the user as "Something went wrong":

  1. backend/tts/errors.py            the backend raises it (detail IS the code)
  2. frontend errorMessages.ts        the human sentence
  3. docs/frontend_contract.md        the contract of record
  4. frontend ErrorHandling.test.ts   the list asserting none falls back

Keeping four files in step by hand works right up until the day it does not,
and the failure is invisible: everything still compiles, every test still
passes, and one specific bad day produces a shrug instead of an explanation.
So it is checked here, mechanically, on every run.
"""
from pathlib import Path

import pytest

from tts.errors import ALL_CODES

REPO = Path(__file__).resolve().parents[2]
MESSAGES = REPO / "frontend" / "src" / "lib" / "errors" / "errorMessages.ts"
CONTRACT = REPO / "docs" / "frontend_contract.md"
FE_TEST = REPO / "frontend" / "src" / "test" / "components" / "ErrorHandling.test.ts"


def _read(path: Path) -> str:
    if not path.is_file():
        pytest.skip(f"{path.name} is not present in this checkout")
    return path.read_text(encoding="utf-8")


def test_every_code_has_a_human_sentence():
    body = _read(MESSAGES)
    missing = sorted(c for c in ALL_CODES if f"{c}:" not in body)
    assert not missing, f"no message for: {missing}"


def test_every_code_is_in_the_contract_as_a_table_row():
    """A TABLE row, not a prose mention: the table is where the status and the
    frontend action live, and a code that only appears in passing has neither."""
    import re

    body = _read(CONTRACT)
    missing = sorted(
        c for c in ALL_CODES
        if not re.search(r"\|\s*" + re.escape(c) + r"\s*\|", body)
    )
    assert not missing, f"codes without a contract table row: {missing}"


def test_every_code_is_covered_by_the_frontend_fallback_test():
    body = _read(FE_TEST)
    missing = sorted(c for c in ALL_CODES if f'"{c}"' not in body)
    assert not missing, f"not asserted against the fallback: {missing}"


def test_no_code_is_defined_and_then_forgotten():
    """The reverse direction: a constant in errors.py that never made it into
    ALL_CODES would be raised at runtime and match nothing anywhere."""
    import tts.errors as errors

    declared = {
        value for name, value in vars(errors).items()
        if name.startswith("TTS_") and isinstance(value, str)
    }
    assert declared == set(ALL_CODES), (
        f"declared but not in ALL_CODES: {sorted(declared - set(ALL_CODES))}"
    )
