"""The settings page's per-module cost equals the server's H4 count.

shared/prompt_module_cost_vectors.json holds module texts and the characters
each adds to the request: prompt_layer.module_texts (macros, sanitizer, trim,
blank dropped) then prompt_layer.module_cost (code points plus one
separator). This test holds the backend to those numbers; the frontend test
frontend/src/test/settings/promptModuleCost.test.ts holds
components/settings/promptModuleCost.ts to the same file, so the two sides
cannot drift apart without one of them going red.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import prompt_layer
from prompt_modules.limits import MODULE_SEPARATOR

_VECTORS = json.loads(
    (Path(__file__).resolve().parents[2] / "shared"
     / "prompt_module_cost_vectors.json").read_text(encoding="utf-8"))
CASES = _VECTORS["cases"]
STAND_IN_CHAR, STAND_IN_USER = "{{char}}", "{{user}}"


def _cost(content: str) -> int:
    return prompt_layer.module_cost(
        prompt_layer.module_texts([{"content": content}], STAND_IN_CHAR, STAND_IN_USER))


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_backend_count_matches_the_shared_vector(case):
    assert _cost(case["content"]) == case["expected_chars"]


def test_ground_the_vectors_tell_code_points_from_utf16_units():
    """An astral character is one code point but two UTF-16 units. If no
    vector told the two apart, a frontend counting `.length` would pass."""
    telling = [c for c in CASES
               if c["expected_chars"] > 0
               and len(c["content"].encode("utf-16-le")) // 2 != len(c["content"])]
    assert telling, "no vector carries an astral character"


def test_positive_a_blank_module_adds_nothing_and_a_plain_one_adds_its_separator():
    by_id = {c["id"]: c for c in CASES}
    assert by_id["blank"]["expected_chars"] == 0
    plain = by_id["ascii"]
    assert plain["expected_chars"] == len(plain["content"]) + len(MODULE_SEPARATOR)
