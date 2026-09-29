"""st_macros.py -- the SillyTavern macro engine for prompt module text.

A preset written for SillyTavern carries its own small program in `{{...}}`
macros: flags are reset with `{{setvar::x::}}`, set again by the modules that
are switched on, and read further down with `{{getvar::x}}`. This module runs
that program. It is pure: no I/O, no clock, no global state. The caller owns
the variable dict and decides whether the writes are kept.

The rules follow the current SillyTavern engine, not its older one:

- Nested macros are resolved first, and everything runs strictly left to
  right in document order. A value that comes out of a macro is never scanned
  again, so a variable holding `{{user}}` prints those eight characters.
- Arguments: `{{name::a::b}}`, `{{name arg}}` (one argument) and the older
  `{{name:arg}}` (one argument, `::` allowed inside it). Names are ASCII and
  case-insensitive, variable names are case-sensitive. Whitespace around the
  name and around each argument is dropped; whitespace inside a value that a
  nested macro produced is kept.
- Each known macro takes a fixed number of arguments. A wrong count leaves
  the macro as written (with its nested macros already resolved), and so does
  an unknown name. `{{getvar}}` of a name that was never set is the empty
  string, and so is `{{getvar::name::default}}` (read as one name,
  "name::default", as SillyTavern's older engine did; the import report
  still lists it).
- `<USER>`, `<BOT>` and `<CHAR>` (any case) are the user and the character.
- `\\{` and `\\}` print a brace; `\\{\\{user\\}\\}` is therefore not a macro.
- `{{trim}}` removes itself and the CR/LF run on both sides, after every other
  macro has run, exactly like the card engine does.

What the card engine already defines is kept identical here: `{{char}}`,
`{{user}}`, `{{original}}` (always empty), the `{{// comment}}` form (from
`{{//` to the first `}}`, nothing inside it runs) and the trim rule above.

VARIABLES

    {{setvar::n::v}}      ""            n = v, the text as given
    {{getvar::n}}         the value     "" when unset; a number-looking value
                                        comes back in number form ("01" -> "1")
    {{addvar::n::v}}      ""            JSON array: append; both numbers: add;
                                        otherwise append the text
    {{incvar::n}}         new value     add 1 (so "abc" becomes "abc1")
    {{decvar::n}}         new value     add -1
    {{hasvar::n}}         true/false    also {{varexists::n}}
    {{deletevar::n}}      ""            also {{flushvar::n}}

The `*globalvar` forms (setglobalvar, getglobalvar, ...) use the same dict:
global variables count per chat here.

OTHER MACROS

    {{roll::XdY+Z}}  {{roll 2d6}}  {{roll::6}}   dice total, "" if the formula
                                                 is invalid or too large
    {{random::a::b}}  {{random a,b}}             one of the items

`evaluate` never raises for a bad macro; the bad macro stays in the text. It
raises MacroOutputTooLarge (the card engine's error) when the output or the
work would pass a bound, and then the variable dict is left as it was.
"""

from __future__ import annotations

import json
import math
import random
import re
from decimal import Decimal
from typing import Callable, Union

from card_macros import FIELD_MAX_CODE_POINTS, MacroOutputTooLarge

#: Deepest `{{` nesting that is still parsed as macros. Deeper braces are
#: kept as plain text, so a wall of `{{{{...` cannot recurse.
MAX_NESTING_DEPTH = 16

#: Longest text evaluate returns, and longest value a variable may hold.
MAX_OUTPUT_CODE_POINTS = FIELD_MAX_CODE_POINTS

#: Total code points one evaluate call may build across every macro. This is
#: what stops `{{addvar::a::{{getvar::a}}}}` repeated from doubling forever.
MAX_WORK_CODE_POINTS = 8 * FIELD_MAX_CODE_POINTS

#: Most dice one `{{roll}}` may throw; more renders "".
MAX_DICE = 1000

#: Longest digit run a dice count, side count or modifier may have.
MAX_DICE_DIGITS = 15

# The text {{trim}} leaves behind for the final pass, and that pass.
TRIM_MARKER = "{{trim}}"
_TRIM_MARK = re.compile(r"\{\{[Tt][Rr][Ii][Mm]\}\}")
_NEWLINES = "\r\n"
_UNESCAPE = re.compile(r"\\([{}])")

# "{{" that is not followed by another "{" (in "{{{x}}}" the macro starts at
# the second brace), "}}", or an angle marker.
_TOKEN = re.compile(r"\{\{(?!\{)|\}\}|<[A-Za-z]+>")
_NAME = re.compile(r"\s*([A-Za-z][A-Za-z0-9_-]*)")
_ANGLE_VALUES = {"user": "user", "bot": "char", "char": "char"}

# The characters JavaScript's trim() and Number() treat as whitespace.
_JS_WS = " \t\n\v\f\r" + "".join(map(chr, (
    0xA0, 0x1680, *range(0x2000, 0x200B), 0x2028, 0x2029, 0x202F, 0x205F, 0x3000, 0xFEFF)))
# No digit can be matched two ways, so fullmatch stays linear on long runs.
_DECIMAL = re.compile(r"[+-]?(?:(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?|Infinity)")
_RADIX = re.compile(r"0([xXoObB])([0-9A-Za-z]+)")
_RADIX_BASE = {"x": 16, "o": 8, "b": 2}
_DICE = re.compile(r"([1-9][0-9]*)?[dD]([1-9][0-9]*)([+-][0-9]+)?")
_DIGITS = re.compile(r"[0-9]+")
_COMMA_HOLD = "\x00comma\x00"


class _Angle:
    __slots__ = ("key",)

    def __init__(self, key: str) -> None:
        self.key = key


class _Macro:
    __slots__ = ("parts",)

    def __init__(self, parts: list) -> None:
        self.parts = parts


_Part = Union[str, _Angle, _Macro]


class _Failed(Exception):
    """A known macro could not run; it stays as written."""


# --------------------------------------------------------------------- parse


def _parse(text: str) -> list:
    """Split `text` into plain strings, angle markers and macro nodes.

    Pure and linear. Nothing is evaluated here, so unsupported_macros and
    evaluate see the same structure.
    """
    stack: list[list] = [[]]
    suppressed = 0
    lit_start = 0
    pos = 0
    n = len(text)
    no_closer_from = n + 1

    def flush(end: int) -> None:
        if end > lit_start:
            stack[-1].append(text[lit_start:end])

    while True:
        m = _TOKEN.search(text, pos)
        if m is None:
            break
        s, e = m.span()
        tok = m.group()
        pos = e
        if tok == "{{":
            if text.startswith("//", e):
                close = -1 if e + 2 >= no_closer_from else text.find("}}", e + 2)
                if close == -1:
                    no_closer_from = min(no_closer_from, e + 2)
                    continue
                flush(s)
                lit_start = pos = close + 2
                continue
            if suppressed or len(stack) - 1 >= MAX_NESTING_DEPTH:
                suppressed += 1
                continue
            flush(s)
            stack.append([])
            lit_start = e
        elif tok == "}}":
            if suppressed:
                suppressed -= 1
                continue
            if len(stack) == 1:
                continue
            flush(s)
            parts = stack.pop()
            stack[-1].append(_Macro(parts))
            lit_start = e
        else:
            key = _ANGLE_VALUES.get(tok[1:-1].lower())
            if key is None:
                continue
            flush(s)
            stack[-1].append(_Angle(key))
            lit_start = e
    flush(n)
    # An unclosed "{{" is plain text; what was inside it still counts.
    while len(stack) > 1:
        parts = stack.pop()
        stack[-1].append("{{")
        stack[-1].extend(parts)
    return stack[0]


def _lstrip_parts(parts: list) -> list:
    parts = list(parts)
    while parts and isinstance(parts[0], str):
        head = parts[0].lstrip()
        if head:
            parts[0] = head
            break
        parts.pop(0)
    return parts


def _strip_parts(parts: list) -> list:
    parts = _lstrip_parts(parts)
    while parts and isinstance(parts[-1], str):
        tail = parts[-1].rstrip()
        if tail:
            parts[-1] = tail
            break
        parts.pop()
    return parts


def _shape(parts: list) -> tuple[str, list[list]] | None:
    """Name and argument part-lists of a macro, or None if it has no name.

    The argument separators are read from the macro's own text only, so a
    value containing "::" that a nested macro produced never splits anything.
    """
    if not parts or not isinstance(parts[0], str):
        return None
    first = parts[0]
    m = _NAME.match(first)
    if m is None:
        return None
    rest = first[m.end():]
    if rest and not (rest[0].isspace() or rest[0] == ":"):
        return None
    if not rest and len(parts) > 1:
        return None
    tail = _lstrip_parts(([rest] if rest else []) + parts[1:])
    if not tail:
        return m.group(1), []
    head = tail[0]
    if isinstance(head, str) and head.startswith("::"):
        tail[0] = head[2:]
        args: list[list] = []
        current: list = []
        for part in tail:
            if isinstance(part, str):
                pieces = part.split("::")
                current.append(pieces[0])
                for piece in pieces[1:]:
                    args.append(current)
                    current = [piece]
            else:
                current.append(part)
        args.append(current)
    else:
        if isinstance(head, str) and head.startswith(":"):
            tail[0] = head[1:]
        args = [tail]
    return m.group(1), [_strip_parts([p for p in a if p != ""]) for a in args]


def _label(parts: list) -> str:
    """What the import report calls a macro that has no readable name."""
    if parts and isinstance(parts[0], str):
        head = parts[0].strip()
        if head:
            return re.split(r"[\s:]", head, maxsplit=1)[0][:40]
    return "{{...}}"


# ------------------------------------------------ number and JSON, as ST does


def _js_number(value: Union[str, float]) -> float | None:
    """Number(value) in JavaScript; None stands for NaN."""
    if isinstance(value, float):
        return value
    t = value.strip(_JS_WS)
    if t == "":
        return 0.0
    if _DECIMAL.fullmatch(t):
        body = t.lstrip("+-")
        if body == "Infinity":
            return -math.inf if t.startswith("-") else math.inf
        return float(t)
    m = _RADIX.fullmatch(t)
    if m is not None:
        try:
            whole = int(m.group(2), _RADIX_BASE[m.group(1).lower()])
        except ValueError:
            return None
        try:
            return float(whole)
        except OverflowError:
            return math.inf
    return None


def _js_num_str(x: float) -> str:
    """String(x) for a JavaScript number."""
    if math.isnan(x):
        return "NaN"
    if x == 0:
        return "0"
    if math.isinf(x):
        return "Infinity" if x > 0 else "-Infinity"
    if x < 0:
        return "-" + _js_num_str(-x)
    _sign, digit_tuple, exp = Decimal(repr(x)).as_tuple()
    digits = "".join(str(d) for d in digit_tuple)
    stripped = digits.rstrip("0")
    exp += len(digits) - len(stripped)
    digits = stripped
    k = len(digits)
    n = exp + k
    if k <= n <= 21:
        return digits + "0" * (n - k)
    if 0 < n <= 21:
        return digits[:n] + "." + digits[n:]
    if -6 < n <= 0:
        return "0." + "0" * (-n) + digits
    e = n - 1
    exp_text = ("+" if e >= 0 else "-") + str(abs(e))
    if k == 1:
        return digits + "e" + exp_text
    return digits[0] + "." + digits[1:] + "e" + exp_text


def _reject_constant(_name: str) -> None:
    raise ValueError("not JSON")


_NOT_JSON = object()


def _json_parse(text: str):
    try:
        return json.loads(text, parse_int=float, parse_float=float,
                          parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        return _NOT_JSON


def _json_stringify(value) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, float):
        return _js_num_str(value) if math.isfinite(value) else "null"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ",".join(_json_stringify(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(
            json.dumps(k, ensure_ascii=False) + ":" + _json_stringify(v)
            for k, v in value.items()
        ) + "}"
    raise _Failed()


def _js_string(value) -> str:
    """String(value) for what the variable functions return."""
    if isinstance(value, float):
        return _js_num_str(value)
    if isinstance(value, list):
        return _json_stringify(value)
    return value


def _falsy(value) -> bool:
    return value == "" or (isinstance(value, float) and (value == 0 or math.isnan(value)))


# ------------------------------------------------------------------ evaluate


class _State:
    def __init__(self, variables: dict[str, str], char: str, user: str,
                 rng: random.Random) -> None:
        self.vars = variables
        self.char = char
        self.user = user
        self.rng = rng
        self.work = 0

    def charge(self, amount: int) -> None:
        self.work += amount
        if self.work > MAX_WORK_CODE_POINTS:
            raise MacroOutputTooLarge()

    # Variable functions, shaped like SillyTavern's so the quirks match.

    def get(self, name: str) -> Union[str, float]:
        value = self.vars.get(name)
        if value is None:
            return ""
        if value.strip(_JS_WS) == "":
            return value
        number = _js_number(value)
        return value if number is None else number

    def set(self, name: str, value: str) -> None:
        if not name:
            raise _Failed()
        if len(value) > MAX_OUTPUT_CODE_POINTS:
            raise MacroOutputTooLarge()
        self.charge(len(value))
        self.vars[name] = value

    def add(self, name: str, value: Union[str, float]):
        current = self.get(name)
        if _falsy(current):
            current = 0.0
        if isinstance(current, str):
            parsed = _json_parse(current)
            if isinstance(parsed, list):
                parsed.append(value)
                self.set(name, _json_stringify(parsed))
                return parsed
        increment = _js_number(value)
        current_number = _js_number(current)
        if increment is None or current_number is None:
            text = ("" if _falsy(current) else _js_string(current)) + _js_string(value)
            self.set(name, text)
            return text
        new_value = current_number + increment
        if math.isnan(new_value):
            return ""
        self.set(name, _js_num_str(new_value))
        return new_value


def _m_char(st: _State, _a: list[str]) -> str:
    return st.char


def _m_user(st: _State, _a: list[str]) -> str:
    return st.user


def _m_empty(_st: _State, _a: list[str]) -> str:
    return ""


def _m_trim(_st: _State, _a: list[str]) -> str:
    return TRIM_MARKER


def _m_setvar(st: _State, a: list[str]) -> str:
    st.set(a[0], a[1])
    return ""


def _m_getvar(st: _State, a: list[str]) -> str:
    # `{{getvar::name::default}}` is not a form SillyTavern knows. Its older
    # engine read a variable literally called "name::default", which is
    # empty, and that is what this does too rather than leaving it as written.
    return _js_string(st.get("::".join(a)))


def _m_addvar(st: _State, a: list[str]) -> str:
    st.add(a[0], a[1])
    return ""


def _m_incvar(st: _State, a: list[str]) -> str:
    return _js_string(st.add(a[0], 1.0))


def _m_decvar(st: _State, a: list[str]) -> str:
    return _js_string(st.add(a[0], -1.0))


def _m_hasvar(st: _State, a: list[str]) -> str:
    return "true" if a[0] in st.vars else "false"


def _m_deletevar(st: _State, a: list[str]) -> str:
    st.vars.pop(a[0], None)
    return ""


def _m_roll(st: _State, a: list[str]) -> str:
    formula = a[0]
    if _DIGITS.fullmatch(formula):
        formula = "1d" + formula
    m = _DICE.fullmatch(formula)
    if m is None:
        return ""
    count_text, sides_text, mod_text = m.groups()
    if any(g is not None and len(g.lstrip("+-")) > MAX_DICE_DIGITS
           for g in (count_text, sides_text, mod_text)):
        return ""
    count = int(count_text) if count_text else 1
    if count > MAX_DICE:
        return ""
    sides = int(sides_text)
    total = sum(1 + math.floor(st.rng.random() * sides) for _ in range(count))
    total += int(mod_text) if mod_text else 0
    return str(total)


def _m_random(st: _State, a: list[str]) -> str:
    items = a
    if len(items) == 1:
        single = items[0]
        if "::" in single:
            items = [item.strip() for item in single.split("::")]
        else:
            items = [item.strip().replace(_COMMA_HOLD, ",")
                     for item in single.replace("\\,", _COMMA_HOLD).split(",")]
    if not items:
        return ""
    return items[int(st.rng.random() * len(items))]


_Handler = Callable[[_State, list[str]], str]

#: name -> (fewest arguments, most arguments or None for any, handler)
_MACROS: dict[str, tuple[int, int | None, _Handler]] = {
    "char": (0, 0, _m_char),
    "user": (0, 0, _m_user),
    "original": (0, 0, _m_empty),
    "comment": (1, 1, _m_empty),
    "trim": (0, 1, _m_trim),
    "roll": (1, 1, _m_roll),
    "random": (0, None, _m_random),
}
for _scope in ("var", "globalvar"):
    _MACROS.update({
        "set" + _scope: (2, 2, _m_setvar),
        "get" + _scope: (1, None, _m_getvar),
        "add" + _scope: (2, 2, _m_addvar),
        "inc" + _scope: (1, 1, _m_incvar),
        "dec" + _scope: (1, 1, _m_decvar),
        "has" + _scope: (1, 1, _m_hasvar),
        _scope + "exists": (1, 1, _m_hasvar),
        "delete" + _scope: (1, 1, _m_deletevar),
        "flush" + _scope: (1, 1, _m_deletevar),
    })
del _scope

#: Every macro name evaluate runs (lower case), for callers and tests.
SUPPORTED_MACROS = frozenset(_MACROS)

#: Macros that run with extra arguments but are still reported, because the
#: extra arguments are ignored rather than understood.
_REPORTED_ABOVE = {"getvar": 1, "getglobalvar": 1}


def _arity_ok(spec: tuple[int, int | None, _Handler], count: int) -> bool:
    low, high, _ = spec
    return count >= low and (high is None or count <= high)


def _resolve(part: _Part, st: _State) -> str:
    if isinstance(part, str):
        return part
    if isinstance(part, _Angle):
        return st.user if part.key == "user" else st.char
    return _eval_macro(part, st)


def _eval_macro(node: _Macro, st: _State) -> str:
    # Nested macros run first, once each, left to right. The argument
    # strings and the as-written fallback are both built from these results.
    resolved: dict[int, str] = {}
    for part in node.parts:
        if not isinstance(part, str):
            resolved[id(part)] = _resolve(part, st)

    def text_of(parts: list) -> str:
        return "".join(p if isinstance(p, str) else resolved[id(p)] for p in parts)

    def as_written() -> str:
        raw = "{{" + text_of(node.parts) + "}}"
        st.charge(len(raw))
        return raw

    shape = _shape(node.parts)
    if shape is None:
        return as_written()
    name, arg_parts = shape
    spec = _MACROS.get(name.lower())
    if spec is None or not _arity_ok(spec, len(arg_parts)):
        return as_written()
    args = [text_of(a) for a in arg_parts]
    st.charge(sum(len(a) for a in args))
    try:
        out = spec[2](st, args)
    except (_Failed, RecursionError):
        return as_written()
    st.charge(len(out))
    return out


def _trim_pass(text: str) -> str:
    """Drop every {{trim}} and the CR/LF run on each side of it.

    Done by hand, not with one regex over the output: a regex that starts
    with a newline run rescans that run from every position in it.
    """
    out: list[str] = []
    pos = 0
    strip_next = False
    for m in _TRIM_MARK.finditer(text):
        gap = text[pos:m.start()]
        if strip_next:
            gap = gap.lstrip(_NEWLINES)
        out.append(gap.rstrip(_NEWLINES))
        pos = m.end()
        strip_next = True
    if not strip_next:
        return text
    out.append(text[pos:].lstrip(_NEWLINES))
    return "".join(out)


def evaluate(text: str, variables: dict[str, str], *, char: str, user: str,
             rng: random.Random | None = None) -> str:
    """Run the macros in `text` and return the result.

    `variables` is changed in place by setvar/addvar/incvar/decvar/deletevar
    and their global forms, but only when the whole call succeeds.
    """
    if not text:
        return ""
    if len(text) > MAX_OUTPUT_CODE_POINTS:
        raise MacroOutputTooLarge()
    work = dict(variables)
    st = _State(work, char, user, rng if rng is not None else random.Random())
    out = "".join(_resolve(part, st) for part in _parse(text))
    out = _UNESCAPE.sub(r"\1", out)
    out = _trim_pass(out)
    if len(out) > MAX_OUTPUT_CODE_POINTS:
        raise MacroOutputTooLarge()
    variables.clear()
    variables.update(work)
    return out


def unsupported_macros(text: str, *, unique: bool = True) -> list[str]:
    """Macros in `text` that evaluate leaves as written, for the import report.

    An unknown name is listed in lower case. A known name used with the wrong
    number of arguments is listed as "getvar (2 arguments)". A macro with no
    readable name is listed by its first word ("/setvar", ".x"). Entries come
    in document order; each appears once, or once per use with unique=False
    (so a caller can count uses). Nothing is evaluated.
    """
    found: list[str] = []
    seen: set[str] = set()
    todo = list(reversed(_parse(text or "")))
    while todo:
        part = todo.pop()
        if not isinstance(part, _Macro):
            continue
        todo.extend(reversed(part.parts))
        shape = _shape(part.parts)
        if shape is None:
            label = _label(part.parts)
        else:
            name = shape[0].lower()
            spec = _MACROS.get(name)
            if spec is None:
                label = name
            elif (not _arity_ok(spec, len(shape[1]))
                  or len(shape[1]) > _REPORTED_ABOVE.get(name, len(shape[1]))):
                label = f"{name} ({len(shape[1])} arguments)"
            else:
                continue
        if not unique or label not in seen:
            seen.add(label)
            found.append(label)
    return found
