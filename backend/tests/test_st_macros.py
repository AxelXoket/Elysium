"""The SillyTavern macro engine used for prompt module text.

Every table row is an input, a starting variable dict, the exact output and
the exact variable dict afterwards. The expected values are what SillyTavern's
current engine produces for the same text (its variable functions, its dice
formula and its trim and unescape passes), worked by hand from that code.
"""
from __future__ import annotations

import math
import random
import time

import pytest

from card_macros import MacroOutputTooLarge
from prompt_modules.st_macros import (
    MAX_DICE,
    MAX_NESTING_DEPTH,
    MAX_OUTPUT_CODE_POINTS,
    MAX_WORK_CODE_POINTS,
    SUPPORTED_MACROS,
    TRIM_MARKER,
    evaluate,
    unsupported_macros,
)

CHAR = "Bob"
USER = "Ann"


def run(text, variables=None, *, char=CHAR, user=USER, rng=None):
    variables = {} if variables is None else variables
    out = evaluate(text, variables, char=char, user=user, rng=rng)
    return out, variables


# (id, text, variables before, output, variables after)
CASES = [
    ("plain text untouched", "just text, no macros", {}, "just text, no macros", {}),
    ("setvar then getvar", "{{setvar::a::1}}{{getvar::a}}", {}, "1", {"a": "1"}),
    ("setvar prints nothing", "x{{setvar::a::v}}y", {}, "xy", {"a": "v"}),
    ("whitespace around name and args", "{{ setvar :: a :: b }}", {}, "", {"a": "b"}),
    ("names are case-insensitive", "{{SetVar::a::x}}{{GETVAR::a}}", {}, "x", {"a": "x"}),
    ("variable names are case-sensitive", "{{setvar::A::1}}[{{getvar::a}}]", {}, "[]", {"A": "1"}),
    ("getvar of an unset name is empty", "[{{getvar::nope}}]", {}, "[]", {}),
    ("getvar with a default is empty", "[{{getvar::flagX::None}}]", {"flagX": "1"}, "[]", {"flagX": "1"}),
    ("getvar with a default reads the joined name", "{{getvar::a::b}}", {"a::b": "v"}, "v", {"a::b": "v"}),
    ("getvar number form: leading zero", "{{getvar::a}}", {"a": "01"}, "1", {"a": "01"}),
    ("getvar number form: trailing zero", "{{getvar::a}}", {"a": "1.50"}, "1.5", {"a": "1.50"}),
    ("getvar number form: hex", "{{getvar::a}}", {"a": "0x10"}, "16", {"a": "0x10"}),
    ("getvar number form: big exponent", "{{getvar::a}}", {"a": "1e21"}, "1e+21", {"a": "1e21"}),
    ("getvar number form: small", "{{getvar::a}}", {"a": "0.0000001"}, "1e-7", {"a": "0.0000001"}),
    ("getvar number form: 21 digits", "{{getvar::a}}", {"a": "100000000000000000000"},
     "100000000000000000000", {"a": "100000000000000000000"}),
    ("getvar text stays text", "{{getvar::a}}", {"a": "abc"}, "abc", {"a": "abc"}),
    ("getvar whitespace stays", "[{{getvar::a}}]", {"a": "  "}, "[  ]", {"a": "  "}),
    ("incvar prints the new value", "{{incvar::n}}{{incvar::n}}", {}, "12", {"n": "2"}),
    ("decvar from unset", "{{decvar::n}}", {}, "-1", {"n": "-1"}),
    ("incvar on text appends", "{{incvar::n}}", {"n": "abc"}, "abc1", {"n": "abc1"}),
    ("decvar on text appends", "{{decvar::n}}", {"n": "abc"}, "abc-1", {"n": "abc-1"}),
    ("incvar on a decimal", "{{incvar::n}}", {"n": "1.5"}, "2.5", {"n": "2.5"}),
    ("incvar on an array pushes a number", "{{incvar::n}}", {"n": "[1]"}, "[1,1]", {"n": "[1,1]"}),
    ("addvar adds numbers", "{{addvar::n::3}}", {"n": "2"}, "", {"n": "5"}),
    ("addvar appends text", "{{addvar::n::y}}", {"n": "x"}, "", {"n": "xy"}),
    ("addvar number to text", "{{addvar::n::5}}", {"n": "x"}, "", {"n": "x5"}),
    ("addvar pushes to an array", "{{addvar::n::y}}", {"n": '["x"]'}, "", {"n": '["x","y"]'}),
    ("addvar on zero drops the zero", "{{addvar::n::abc}}", {"n": "0"}, "", {"n": "abc"}),
    ("addvar empty value adds zero", "{{addvar::n::}}", {}, "", {"n": "0"}),
    ("addvar text to unset", "{{addvar::n::abc}}", {}, "", {"n": "abc"}),
    ("hasvar false then true", "{{hasvar::a}}{{setvar::a::}}{{hasvar::a}}", {}, "falsetrue", {"a": ""}),
    ("varexists alias", "{{varexists::a}}", {"a": "1"}, "true", {"a": "1"}),
    ("deletevar", "{{deletevar::a}}[{{getvar::a}}]", {"a": "1"}, "[]", {}),
    ("flushvar alias", "{{flushvar::a}}{{hasvar::a}}", {"a": "1"}, "false", {}),
    ("global forms share the dict", "{{setglobalvar::g::1}}{{getvar::g}}{{incglobalvar::g}}", {},
     "12", {"g": "2"}),
    ("setvar with empty name stays", "{{setvar::::x}}", {}, "{{setvar::::x}}", {}),
    ("setvar with three args stays", "{{setvar::a::b::c}}", {}, "{{setvar::a::b::c}}", {}),
    ("setvar with one arg stays", "{{setvar::a}}", {}, "{{setvar::a}}", {}),
    ("nested macro in a value", "{{setvar::greeting::Hello, {{user}}!}}{{getvar::greeting}}", {},
     "Hello, Ann!", {"greeting": "Hello, Ann!"}),
    ("nested value with :: does not split", "{{setvar::a::{{getvar::b}}}}{{getvar::a}}",
     {"b": "x::y"}, "x::y", {"a": "x::y", "b": "x::y"}),
    ("nested value keeps its whitespace", "[{{setvar::a:: {{getvar::b}} }}{{getvar::a}}]",
     {"b": " x "}, "[ x ]", {"a": " x ", "b": " x "}),
    ("a value is not scanned again", "{{getvar::a}}", {"a": "{{user}}"}, "{{user}}", {"a": "{{user}}"}),
    ("left to right", "[{{getvar::a}}]{{setvar::a::1}}[{{getvar::a}}]", {}, "[][1]", {"a": "1"}),
    ("nested first, left to right", "{{setvar::a::{{incvar::n}}-{{incvar::n}}}}{{getvar::a}}", {},
     "1-2", {"a": "1-2", "n": "2"}),
    ("unknown stays with nested resolved", "{{foo::{{user}}}}", {}, "{{foo::Ann}}", {}),
    ("unknown hyphen name stays", "{{Some-Name}}", {}, "{{Some-Name}}", {}),
    ("unknown still runs its nested macros", "{{foo::{{setvar::a::1}}}}", {}, "{{foo::}}", {"a": "1"}),
    ("zero-arg macro with an arg stays", "{{user::x}}", {}, "{{user::x}}", {}),
    ("closing tag stays", "{{/setvar}}", {}, "{{/setvar}}", {}),
    ("char and user", "{{char}} and {{ user }}", {}, "Bob and Ann", {}),
    ("original is empty", "[{{original}}]", {}, "[]", {}),
    ("angle markers", "<USER> meets <bot> and <Char>", {}, "Ann meets Bob and Bob", {}),
    ("unknown angle stays", "<b>x</b>", {}, "<b>x</b>", {}),
    ("comment", "a{{// note}}b", {}, "ab", {}),
    ("multi-line comment", "a{{// line one\nline two}}b", {}, "ab", {}),
    ("comment alias", "a{{comment hidden}}b", {}, "ab", {}),
    ("trim eats newlines", "a\n\n{{trim}}\n\nb", {}, "ab", {}),
    ("trim with spaces and case", "a\r\n{{ TRIM }}\r\nb", {}, "ab", {}),
    ("trim keeps spaces", "a \n{{trim}}\n b", {}, "a  b", {}),
    ("trim stored in a value runs where printed", "{{setvar::t::x{{trim}}}}a\n{{getvar::t}}\nb", {},
     "a\nxb", {"t": "x" + TRIM_MARKER}),
    ("escaped braces print braces", "\\{\\{user\\}\\}", {}, "{{user}}", {}),
    ("unescape runs before the trim pass", "a\n\\{\\{trim\\}\\}\nb", {}, "ab", {}),
    ("single backslash before a macro", "\\{{user}}", {}, "\\Ann", {}),
    ("triple brace", "{{{user}}}", {}, "{Ann}", {}),
    ("stray closer", "a}}b", {}, "a}}b", {}),
    ("unclosed opener", "a{{user", {}, "a{{user", {}),
    ("unclosed opener still runs what is inside", "{{x {{setvar::a::1}}", {}, "{{x ", {"a": "1"}),
    ("roll with no formula stays", "{{roll}}", {}, "{{roll}}", {}),
    ("roll invalid formula", "[{{roll::abc}}]", {}, "[]", {}),
    ("roll zero sides", "[{{roll::0}}]", {}, "[]", {}),
    ("roll one-sided dice", "{{roll::5d1+2}}", {}, "7", {}),
    ("roll negative modifier", "{{roll::1d1-3}}", {}, "-2", {}),
    ("random with no items", "[{{random}}]", {}, "[]", {}),
    ("random one item", "{{random::only}}", {}, "only", {}),
]


@pytest.mark.parametrize("text,before,expected,after",
                         [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_table(text, before, expected, after):
    out, variables = run(text, dict(before))
    assert out == expected
    assert variables == after


def test_user_name_with_separator_is_one_value():
    out, variables = run("{{setvar::x::<user>}}{{getvar::x}}", user="a::b")
    assert out == "a::b"
    assert variables == {"x": "a::b"}


def test_variables_dict_is_mutated_in_place():
    variables = {"keep": "1"}
    same = variables
    evaluate("{{setvar::a::2}}", variables, char=CHAR, user=USER)
    assert same is variables
    assert variables == {"keep": "1", "a": "2"}


# ------------------------------------------------ the reset-then-set pattern

FLAGS = [f"flag{i}" for i in range(16)]


def test_flag_reset_then_set_then_read():
    # Last turn left every flag on.
    variables = {name: "1" for name in FLAGS}
    main = "Main prompt.\n" + "".join(f"{{{{setvar::{name}::}}}}{{{{trim}}}}\n" for name in FLAGS)
    module = "{{setvar::flag3::1}}Module three text."
    reader = "".join(f"{name}=[{{{{getvar::{name}}}}}]\n" for name in FLAGS)

    out_main, _ = run(main, variables)
    out_module, _ = run(module, variables)
    out_reader, _ = run(reader, variables)

    assert out_main == "Main prompt."
    assert out_module == "Module three text."
    expected = "".join(f"{name}=[{'1' if name == 'flag3' else ''}]\n" for name in FLAGS)
    assert out_reader == expected
    assert variables == {name: ("1" if name == "flag3" else "") for name in FLAGS}


def test_flag_reset_positive_control_without_reset():
    # Without the reset line the old values survive: the reset is what clears them.
    variables = {name: "1" for name in FLAGS}
    out, _ = run("".join(f"{{{{getvar::{name}}}}}" for name in FLAGS), variables)
    assert out == "1" * len(FLAGS)


# ----------------------------------------------------------- dice and random


def _droll_reference(seed, count, sides, modifier):
    r = random.Random(seed)
    return sum(1 + math.floor(r.random() * sides) for _ in range(count)) + modifier


@pytest.mark.parametrize("text,count,sides,modifier", [
    ("{{roll::3d6+4}}", 3, 6, 4),
    ("{{roll 2d20}}", 2, 20, 0),
    ("{{roll:1d8-1}}", 1, 8, -1),
    ("{{roll::6}}", 1, 6, 0),
    ("{{roll::d12}}", 1, 12, 0),
    ("{{ROLL::2D4}}", 2, 4, 0),
])
def test_roll_forms_with_seeded_rng(text, count, sides, modifier):
    for seed in range(20):
        out, _ = run(text, rng=random.Random(seed))
        assert out == str(_droll_reference(seed, count, sides, modifier))


def test_roll_is_deterministic_per_seed_and_covers_range():
    seen = set()
    for seed in range(300):
        a, _ = run("{{roll::1d6}}", rng=random.Random(seed))
        b, _ = run("{{roll::1d6}}", rng=random.Random(seed))
        assert a == b
        seen.add(int(a))
    assert seen == {1, 2, 3, 4, 5, 6}


def test_roll_dice_bound():
    assert run(f"{{{{roll::{MAX_DICE}d1}}}}")[0] == str(MAX_DICE)
    assert run(f"[{{{{roll::{MAX_DICE + 1}d1}}}}]")[0] == "[]"


def test_rolls_run_left_to_right_on_one_rng():
    out, _ = run("{{roll::1d100}},{{roll::1d100}}", rng=random.Random(5))
    r = random.Random(5)
    first = 1 + math.floor(r.random() * 100)
    second = 1 + math.floor(r.random() * 100)
    assert out == f"{first},{second}"


@pytest.mark.parametrize("text,items", [
    ("{{random::a::b::c}}", ["a", "b", "c"]),
    ("{{random a, b ,c}}", ["a", "b", "c"]),
    ("{{random:a::b}}", ["a", "b"]),
    ("{{random::x\\,y,z}}", ["x,y", "z"]),
])
def test_random_with_seeded_rng(text, items):
    got = set()
    for seed in range(60):
        out, _ = run(text, rng=random.Random(seed))
        r = random.Random(seed)
        assert out == items[int(r.random() * len(items))]
        got.add(out)
    assert got == set(items)


# ------------------------------------------------------------------ bounds


def _nest(depth):
    return "{{random::" * (depth - 1) + "{{user}}" + "}}" * (depth - 1)


def test_nesting_at_the_bound_is_evaluated():
    assert run(_nest(MAX_NESTING_DEPTH))[0] == USER


def test_nesting_past_the_bound_is_plain_text():
    assert run(_nest(MAX_NESTING_DEPTH + 1))[0] == "{{user}}"


def test_brace_wall_is_kept_as_written():
    text = "{{ " * 20_000 + "user" + "}}" * 20_000
    start = time.perf_counter()
    out, _ = run(text)
    assert time.perf_counter() - start < 5
    assert out == text


def test_huge_brace_wall_hits_the_work_bound_fast():
    text = "{{ " * 200_000 + "user" + "}}" * 200_000
    start = time.perf_counter()
    with pytest.raises(MacroOutputTooLarge):
        run(text)
    assert time.perf_counter() - start < 5


def test_input_over_the_bound_is_refused():
    with pytest.raises(MacroOutputTooLarge):
        run("x" * (MAX_OUTPUT_CODE_POINTS + 1))
    assert run("x" * MAX_OUTPUT_CODE_POINTS)[0] == "x" * MAX_OUTPUT_CODE_POINTS


def test_output_over_the_bound_is_refused_and_variables_kept():
    half = "x" * (MAX_OUTPUT_CODE_POINTS // 2 + 1)
    variables = {"a": half}
    with pytest.raises(MacroOutputTooLarge):
        run("{{setvar::b::1}}{{getvar::a}}{{getvar::a}}", variables)
    assert variables == {"a": half}
    out, _ = run("{{getvar::a}}", variables)
    assert out == half


def test_one_value_over_the_bound_is_refused():
    half = "x" * (MAX_OUTPUT_CODE_POINTS // 2 + 1)
    variables = {"a": half}
    with pytest.raises(MacroOutputTooLarge):
        run("{{setvar::b::{{getvar::a}}{{getvar::a}}}}", variables)
    assert variables == {"a": half}
    # Ground control: one copy fits.
    out, variables = run("{{setvar::b::{{getvar::a}}}}", {"a": half})
    assert out == "" and variables["b"] == half


def test_long_digit_run_is_linear():
    digits = "1" * 200_000
    start = time.perf_counter()
    out, _ = run("{{getvar::a}}", {"a": digits + "x"})
    assert time.perf_counter() - start < 0.5
    assert out == digits + "x"


def test_long_newline_run_is_linear():
    run_of_newlines = "\n" * 200_000
    for text, expected in ((run_of_newlines + "x", run_of_newlines + "x"),
                           (run_of_newlines + "{{trim}}" + run_of_newlines + "x", "x")):
        start = time.perf_counter()
        out, _ = run(text)
        assert time.perf_counter() - start < 0.5
        assert out == expected


def test_doubling_value_is_refused():
    text = "{{setvar::a::x}}" + "{{addvar::a::{{getvar::a}}}}" * 40
    variables: dict[str, str] = {}
    start = time.perf_counter()
    with pytest.raises(MacroOutputTooLarge):
        run(text, variables)
    assert time.perf_counter() - start < 5
    assert variables == {}
    # Ground control: a few doublings are fine.
    out, variables = run("{{setvar::a::x}}" + "{{addvar::a::{{getvar::a}}}}" * 3)
    assert variables == {"a": "x" * 8}


def test_total_work_bound():
    chunk = "y" * (MAX_OUTPUT_CODE_POINTS // 4)
    per_copy = 3 * len(chunk)  # built as argument, stored, and read back
    copies = MAX_WORK_CODE_POINTS // per_copy + 2
    text = "".join(f"{{{{setvar::c{i}::{{{{getvar::src}}}}}}}}" for i in range(copies))
    with pytest.raises(MacroOutputTooLarge):
        run(text, {"src": chunk})
    out, variables = run(text[: text.index("{{setvar::c3::")], {"src": chunk})
    assert out == ""
    assert variables["c2"] == chunk


# ------------------------------------------------------- unsupported report


def test_unsupported_macros_lists_what_stays():
    text = ("{{foo}}{{getvar::x::y}}{{/setvar}}{{user}}"
            "{{setvar::a::{{bar}}}}{{FOO}}{{.x}}{{// {{nope}}}}")
    assert unsupported_macros(text) == ["foo", "getvar (2 arguments)", "/setvar", "bar", ".x"]


def test_unsupported_macros_every_use():
    text = "{{foo}}{{user}}{{foo}}{{getvar::a::b}}{{FOO}}{{getvar::c::d}}"
    assert unsupported_macros(text, unique=False) == [
        "foo", "foo", "getvar (2 arguments)", "foo", "getvar (2 arguments)"]
    # Ground control: the default still lists each entry once.
    assert unsupported_macros(text) == ["foo", "getvar (2 arguments)"]


def test_unsupported_macros_ground_control():
    text = ("{{setvar::a::1}}{{getvar::a}}{{trim}}{{roll::1d6}}{{char}}{{user}}"
            "{{// c}}{{incvar::n}}{{random::a::b}}{{setglobalvar::g::1}}")
    assert unsupported_macros(text) == []
    assert unsupported_macros("") == []


def test_unsupported_macros_agrees_with_evaluate():
    # Every listed name is left as written by evaluate; every supported one is not.
    for name in sorted(SUPPORTED_MACROS):
        assert name not in unsupported_macros("{{" + name + "}}")
    out, _ = run("{{foo}}")
    assert out == "{{foo}}" and unsupported_macros("{{foo}}") == ["foo"]


def test_unsupported_macros_does_not_evaluate():
    assert unsupported_macros("{{setvar::a::1}}{{roll::1d6}}") == []
