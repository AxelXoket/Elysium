"""The naming helper itself, and the five mutations that make it mean something.

`default_names.py` is the one answer five surfaces now give to an empty name
box, so it is the one place where a quiet mistake would be five bugs. This
file drives it directly - no HTTP, no database - and then BREAKS it on purpose
five times to show that the assertions above can actually go red.

The mutations live in memory. The source bytes are read once, mutated in a
string, compiled into a throwaway module, and the last test asserts the file
on disk is unchanged.

Every prefix and every sentence of expected output is IMPORTED. Nothing here
retypes "New Chat", so a reworded prefix cannot leave this file asserting a
string the application no longer produces.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import default_names as dn

# ---------------------------------------------------------------------------
# Behaviour
# ---------------------------------------------------------------------------


def test_an_empty_list_gives_the_first_number():
    assert dn.next_default_name(dn.CHAT, []) == f"{dn.CHAT} 1"


def test_the_second_unnamed_thing_is_two():
    """The sequence, stated once. Everything else here is an edge of it."""
    first = dn.next_default_name(dn.CHAT, [])
    second = dn.next_default_name(dn.CHAT, [first])
    third = dn.next_default_name(dn.CHAT, [first, second])
    assert (first, second, third) == (
        f"{dn.CHAT} 1", f"{dn.CHAT} 2", f"{dn.CHAT} 3")


def test_names_a_person_chose_do_not_advance_the_sequence():
    """POSITIVE CONTROL. Without it, a helper that always answered "1" would
    still satisfy several assertions in this file."""
    assert dn.next_default_name(
        dn.CHAT, ["a night with Aria", "work notes", ""]) == f"{dn.CHAT} 1"


def test_a_deleted_number_becomes_available_again():
    """Smallest free, not max+1. This is the assertion that pins the choice."""
    assert dn.next_default_name(
        dn.CHAT, [f"{dn.CHAT} 1", f"{dn.CHAT} 3"]) == f"{dn.CHAT} 2"


def test_a_hand_typed_number_is_taken():
    """Somebody who typed "New Chat 5" owns 5, so the generator works around
    it instead of colliding with it."""
    assert dn.next_default_name(
        dn.CHAT, [f"{dn.CHAT} 1", f"{dn.CHAT} 5"]) == f"{dn.CHAT} 2"
    assert dn.next_default_name(
        dn.CHAT, [f"{dn.CHAT} {i}" for i in range(1, 6)]) == f"{dn.CHAT} 6"


def test_the_scan_ignores_case():
    """Two rows that read identically on screen are never generated."""
    assert dn.next_default_name(dn.CHAT, ["new chat 1"]) == f"{dn.CHAT} 2"
    assert dn.next_default_name(
        dn.CHAT, ["NEW CHAT 1", "New chat 2"]) == f"{dn.CHAT} 3"


def test_the_generated_name_is_always_the_canonical_spelling():
    out = dn.next_default_name(dn.CHAT, ["new chat 1"])
    assert out == f"{dn.CHAT} 2"
    assert out.startswith(dn.CHAT)


def test_the_fold_is_a_full_one_not_lower():
    """casefold(), not lower(), and this is the case that separates them.

    "ß".lower() is "ß"; "ß".casefold() is "ss". With lower() the two spellings
    of the same prefix would be two sequences, and the second one would start
    again at 1 beside a row that already reads like it.
    """
    assert dn.next_default_name("Straße", ["STRASSE 1"]) == "Straße 2"


def test_rows_stored_with_surrounding_space_still_count():
    assert dn.next_default_name(
        dn.CHAT, [f"  {dn.CHAT} 1  ", None]) == f"{dn.CHAT} 2"


def test_names_that_are_close_but_not_the_pattern_are_not_taken():
    """POSITIVE CONTROL for the pattern. Each of these would hold number 1 if
    an anchor or a separator were dropped."""
    near = [
        f"{dn.CHAT}",           # no number
        f"{dn.CHAT} 1a",        # not a number
        f"{dn.CHAT}1",          # no separator
        f"Old {dn.CHAT} 1",     # prefix is not at the start
        f"{dn.CHAT} -1",        # negative
        f"{dn.CHAT} 1.5",       # decimal
        f"{dn.CHAT} 1 notes",   # trailing words
    ]
    assert dn.next_default_name(dn.CHAT, near) == f"{dn.CHAT} 1"


def test_each_surface_counts_only_its_own():
    """A chat's sequence must not move a character's along."""
    chats = [f"{dn.CHAT} 1", f"{dn.CHAT} 2"]
    assert dn.next_default_name(dn.CHARACTER, chats) == f"{dn.CHARACTER} 1"
    assert dn.next_default_name(dn.VOICE, chats) == f"{dn.VOICE} 1"
    assert dn.next_default_name(dn.PERSONA, chats) == f"{dn.PERSONA} 1"
    assert dn.next_default_name(dn.BOUNDARY, chats) == f"{dn.BOUNDARY} 1"
    assert dn.next_default_name(dn.PROXY, chats) == f"{dn.PROXY} 1"


def test_every_prefix_is_distinct():
    """A duplicate prefix would silently merge two surfaces' sequences."""
    prefixes = [dn.CHAT, dn.CHARACTER, dn.PERSONA,
                dn.PROXY, dn.VOICE, dn.BOUNDARY]
    assert len(set(prefixes)) == len(prefixes)


def test_a_regex_character_in_a_prefix_is_escaped():
    """No prefix contains one today; the escape is what keeps that from
    mattering the day one does."""
    assert dn.next_default_name("A.B", ["AXB 1"]) == "A.B 1"


def test_everything_that_counts_as_blank():
    for value in (None, "", " ", "   ", "\t", "\n", "\t\n "):
        assert dn.is_blank(value) is True, repr(value)


def test_everything_that_does_not():
    """POSITIVE CONTROL: a real name is never mistaken for an empty box."""
    for value in ("a", " a ", "0", "New Chat 1"):
        assert dn.is_blank(value) is False, repr(value)


def test_a_real_name_is_kept_and_trimmed():
    """GROUND CONTROL for the whole feature. If a filled-in name were replaced
    by a generated one, every "the box was empty" assertion in this suite
    would still pass and the application would be unusable."""
    assert dn.name_or_default("  A night out  ", dn.CHAT, []) == "A night out"


def test_an_empty_name_is_generated():
    assert dn.name_or_default("   ", dn.CHAT, [f"{dn.CHAT} 1"]) \
        == f"{dn.CHAT} 2"


# ---------------------------------------------------------------------------
# Mutations - each breaks one line and measures the red
# ---------------------------------------------------------------------------

_SOURCE_PATH = Path(dn.__file__)
SOURCE = _SOURCE_PATH.read_text(encoding="utf-8")


def _mutate(old: str, new: str):
    """Compile a broken copy of the module. Nothing is written to disk."""
    assert old in SOURCE, f"mutation anchor not found: {old!r}"
    code = SOURCE.replace(old, new, 1)
    module = type(sys)("default_names_mutant")
    module.__dict__["__file__"] = "<mutant>"
    exec(compile(code, "<mutant>", "exec"), module.__dict__)
    return module


def _went_red(check) -> bool:
    try:
        check()
    except AssertionError:
        return True
    return False


def test_MUTATION_1_the_helper_always_answers_one():
    """Drop the scan loop -> "the second unnamed thing is two" must go red."""
    m = _mutate("    n = 1\n    while n in taken:\n        n += 1",
                "    n = 1")

    def measure():
        first = m.next_default_name(m.CHAT, [])
        assert m.next_default_name(m.CHAT, [first]) == f"{m.CHAT} 2"

    assert _went_red(measure), "the mutant survived: the sequence test is decor"

    first = dn.next_default_name(dn.CHAT, [])
    assert dn.next_default_name(dn.CHAT, [first]) == f"{dn.CHAT} 2"


def test_MUTATION_2_trimming_is_removed():
    """`is_blank` stops trimming -> a name of spaces would be stored."""
    m = _mutate('return not (value or "").strip()',
                'return not (value or "")')

    def measure():
        assert m.is_blank("   ") is True

    assert _went_red(measure), "the mutant survived: the trim test is decor"

    def measure_stored():
        assert m.name_or_default("   ", m.CHAT, []) == f"{m.CHAT} 1"

    assert _went_red(measure_stored), \
        "the mutant survived: a name of spaces was stored"

    assert dn.is_blank("   ") is True


def test_MUTATION_3_the_collision_check_becomes_case_sensitive():
    """Drop the fold -> "new chat 1" stops holding number 1.

    One of the two casefold() calls is enough to break the pair: the stored
    name is still folded, the prefix is not, so they can no longer meet.
    """
    m = _mutate("re.escape(prefix.casefold())", "re.escape(prefix)")

    def measure():
        assert m.next_default_name(m.CHAT, ["new chat 1"]) == f"{m.CHAT} 2"

    assert _went_red(measure), "the mutant survived: the case test is decor"
    assert dn.next_default_name(dn.CHAT, ["new chat 1"]) == f"{dn.CHAT} 2"


def test_MUTATION_4_the_closing_anchor_is_removed():
    r"""Drop `$` -> "New Chat 1 notes" wrongly holds number 1.

    Measured, and worth writing down: the first attempt mutated `^` and the
    MUTANT SURVIVED. Not a defect - `re.match` anchors at the start already,
    so `^` is a second belt and removing one alone changes nothing. A test
    written over `^` would have been decor. `$` is the single-point anchor,
    so `$` is the mutation.
    """
    m = _mutate(r'(\d+)$"', r'(\d+)"')

    def measure():
        assert m.next_default_name(m.CHAT, [f"{m.CHAT} 1 notes"]) \
            == f"{m.CHAT} 1"

    assert _went_red(measure), "the mutant survived: the anchor test is decor"
    assert dn.next_default_name(dn.CHAT, [f"{dn.CHAT} 1 notes"]) \
        == f"{dn.CHAT} 1"


def test_the_opening_anchor_is_redundant_on_purpose():
    """`^` and `re.match` both anchor at the start, so neither can be
    mutation-tested alone. This test shrinks the claim to what is true: `^` is
    what would keep the anchor if `match` ever became `search`."""
    with_anchor = re.compile(rf"^{re.escape(dn.CHAT.casefold())}\s+(\d+)$")
    assert with_anchor.search(f"old {dn.CHAT.casefold()} 1") is None
    without = re.compile(rf"{re.escape(dn.CHAT.casefold())}\s+(\d+)$")
    assert without.search(f"old {dn.CHAT.casefold()} 1") is not None


def test_MUTATION_5_smallest_free_becomes_max_plus_one():
    """Swap the semantics -> the reuse test must catch it."""
    m = _mutate(
        "    n = 1\n    while n in taken:\n        n += 1",
        "    n = (max(taken) + 1) if taken else 1")

    def measure():
        assert m.next_default_name(m.CHAT, [f"{m.CHAT} 1", f"{m.CHAT} 3"]) \
            == f"{m.CHAT} 2"

    assert _went_red(measure), "the mutant survived: the reuse rule is open"
    assert dn.next_default_name(dn.CHAT, [f"{dn.CHAT} 1", f"{dn.CHAT} 3"]) \
        == f"{dn.CHAT} 2"


def test_MUTATION_the_source_on_disk_is_untouched():
    """The five mutations lived in memory. The file is byte-identical."""
    assert _SOURCE_PATH.read_text(encoding="utf-8") == SOURCE
