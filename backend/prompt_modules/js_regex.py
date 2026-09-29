"""A JavaScript regular expression, as SillyTavern builds it, run with Python.

SillyTavern turns a script's `findRegex` text into a RegExp with its
regexFromString: `/pattern/flags` is split off; flags that are not a set of
distinct letters from "gmixXsuUAJ" make the WHOLE text the pattern, with no
flags; a flag JavaScript does not know (x, X, U, A, J) makes the RegExp fail,
and the script does not run. This module does the same, then rewrites the
pattern so Python's `re` matches what JavaScript matches:

- \\d, \\w and \\b are ASCII only, as in JavaScript. \\s is JavaScript's
  whitespace set. "." leaves out \\n, \\r, U+2028 and U+2029 (unless the s
  flag). ^ and $ are the text's ends, or line ends (all four terminators)
  with the m flag.
- Without the u flag JavaScript reads text as UTF-16 code units, so "." takes
  half of an emoji; the text is matched as code units here too and put back
  together after.
- The i flag folds case the way JavaScript does without the u flag: two
  characters match when their single-character upper case is the same, and a
  non-ASCII character never matches an ASCII one ("\\u017f" is not "s").
- Annex B forms: `{` that does not start a quantifier, and a lone `}` or `]`,
  are plain characters; `\\A`, `\\Z`, `\\h` and other unknown escapes are the
  letter itself; `\\cX` is a control character.

Refused, with the reason the import report shows: inline flags such as
`(?i)`, backreferences, the i flag together with the u flag, Unicode property
escapes, and whatever Python cannot express (a lookbehind of varying length).
A refused script is not run, and neither is it in SillyTavern when its RegExp
fails; where SillyTavern would run it, the report says it was skipped here.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass
from functools import lru_cache

MAX_CODE_POINT = 0x10FFFF
MAX_UNIT = 0xFFFF

REASON_SYNTAX = "unsupported_syntax"
REASON_FLAGS = "unsupported_flags"

#: JavaScript's \s: WhiteSpace and LineTerminator.
JS_SPACE = (
    [(0x09, 0x0D), (0x20, 0x20), (0xA0, 0xA0), (0x1680, 0x1680), (0x2000, 0x200A),
     (0x2028, 0x2029), (0x202F, 0x202F), (0x205F, 0x205F), (0x3000, 0x3000),
     (0xFEFF, 0xFEFF)])
JS_WORD = [(0x30, 0x39), (0x41, 0x5A), (0x5F, 0x5F), (0x61, 0x7A)]
JS_DIGIT = [(0x30, 0x39)]
LINE_TERMINATORS = [(0x0A, 0x0A), (0x0D, 0x0D), (0x2028, 0x2029)]
_WORD_CLASS = "[0-9A-Z_a-z]"
_TERM_CLASS = "[\\n\\r\\u2028\\u2029]"

#: The literal SillyTavern reads: an optional slash, the pattern, the same
#: slash, the flags ("." as JavaScript's, which stops at line terminators).
_LITERAL = re.compile(r"(/?)([^\n\r\u2028\u2029]+)\1([A-Za-z]*)")
_FLAG_SET = re.compile(r"(?!.*?(.).*?\1)[gmixXsuUAJ]+")
_JS_FLAGS = frozenset("gimsu")
_QUANT = re.compile(r"\{(\d+)(,(\d*))?\}")
_SYNTAX_CHARS = frozenset("^$\\.*+?()[]{}|/")


class Unsupported(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class JsRegex:
    regex: re.Pattern
    global_: bool
    #: Match the text as UTF-16 code units (no u flag).
    utf16: bool


# ---------------------------------------------------------------------------
# Sets of code points, as sorted disjoint ranges
# ---------------------------------------------------------------------------

def _norm(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for lo, hi in sorted(ranges):
        if out and lo <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


def _complement(ranges, top: int = MAX_CODE_POINT):
    out, at = [], 0
    for lo, hi in _norm(ranges):
        if lo > at:
            out.append((at, lo - 1))
        at = hi + 1
    if at <= top:
        out.append((at, top))
    return out


def _contains(ranges, cp: int) -> bool:
    i = bisect.bisect_right(ranges, (cp, MAX_CODE_POINT + 1)) - 1
    return i >= 0 and ranges[i][0] <= cp <= ranges[i][1]


def _canon(c: str) -> str:
    """JavaScript's Canonicalize without the u flag."""
    u = c.upper()
    if len(u) != 1 or (ord(c) >= 128 and ord(u) < 128):
        return c
    return u


@lru_cache(maxsize=1)
def _case_groups() -> list[list[int]]:
    """Every group of two or more code units that the i flag makes equal."""
    groups: dict[str, list[int]] = {}
    for cp in range(MAX_UNIT + 1):
        if 0xD800 <= cp <= 0xDFFF:
            continue
        groups.setdefault(_canon(chr(cp)), []).append(cp)
    return [g for g in groups.values() if len(g) > 1]


def _fold(ranges):
    ranges = _norm(ranges)
    extra = []
    for group in _case_groups():
        if any(_contains(ranges, cp) for cp in group):
            extra += [(cp, cp) for cp in group]
    return _norm(ranges + extra)


def _esc(cp: int) -> str:
    return f"\\U{cp:08x}"


def _emit(ranges, negate: bool = False) -> str:
    ranges = _norm(ranges)
    if not ranges:
        return "[^\\U00000000-\\U0010ffff]" if not negate else "[\\U00000000-\\U0010ffff]"
    body = "".join(_esc(lo) if lo == hi else f"{_esc(lo)}-{_esc(hi)}"
                   for lo, hi in ranges)
    return f"[{'^' if negate else ''}{body}]"


# ---------------------------------------------------------------------------
# The rewrite
# ---------------------------------------------------------------------------

class _Rewriter:
    def __init__(self, src: str, flags: str) -> None:
        self.u = "u" in flags
        # Without the u flag JavaScript reads the pattern as code units too:
        # an emoji in it is two characters, in a class two members.
        self.src = src if self.u else to_units(src)
        self.i = 0
        self.icase = "i" in flags
        self.multiline = "m" in flags
        self.dotall = "s" in flags
        self.named = "(?<" in src.replace("(?<=", "").replace("(?<!", "")
        self.out: list[str] = []

    def fail(self, reason: str = REASON_SYNTAX):
        raise Unsupported(reason)

    def peek(self, k: int = 0) -> str:
        j = self.i + k
        return self.src[j] if j < len(self.src) else ""

    # -- pieces -----------------------------------------------------------

    def _units(self, cp: int) -> list[int]:
        if self.u or cp <= MAX_UNIT:
            return [cp]
        cp -= 0x10000
        return [0xD800 + (cp >> 10), 0xDC00 + (cp & 0x3FF)]

    def literal(self, cp: int) -> str:
        units = self._units(cp)
        return "".join(self._set([(c, c)]) if self.icase else re.escape(chr(c))
                       for c in units)

    def _set(self, ranges, negate: bool = False) -> str:
        if self.icase:
            ranges = _fold(ranges)
        if len(ranges) == 1 and ranges[0][0] == ranges[0][1] and not negate:
            return re.escape(chr(ranges[0][0]))
        return _emit(ranges, negate)

    def _hex(self, n: int) -> int | None:
        s = self.src[self.i:self.i + n]
        if len(s) == n and all(c in "0123456789abcdefABCDEF" for c in s):
            self.i += n
            return int(s, 16)
        return None

    def escape(self, in_class: bool):
        """After a backslash: a code point (int) or a set (list of ranges),
        or ("assert", text) outside a class."""
        c = self.peek()
        if not c:
            self.fail()
        self.i += 1
        sets = {"d": JS_DIGIT, "w": JS_WORD, "s": JS_SPACE}
        if c in sets:
            return sets[c]
        if c.lower() in sets:
            return _complement(sets[c.lower()])
        if c in "bB" and not in_class:
            w = _WORD_CLASS
            return ("assert", f"(?:(?<={w})(?!{w})|(?<!{w})(?={w}))" if c == "b"
                    else f"(?:(?<={w})(?={w})|(?<!{w})(?!{w}))")
        simple = {"f": 0x0C, "n": 0x0A, "r": 0x0D, "t": 0x09, "v": 0x0B}
        if c in simple:
            return simple[c]
        if c == "b":            # in a class: backspace
            return 0x08
        if c == "0" and not self.peek().isdigit():
            return 0
        if c.isdigit():
            self.fail()         # a backreference, or a legacy octal escape
        if c == "c":
            x = self.peek()
            if x and x.isascii() and x.isalpha():
                self.i += 1
                return ord(x) % 32
            if self.u:
                self.fail()
            self.i -= 1         # "\c" is a backslash, then the c
            return ord("\\")
        if c == "x":
            v = self._hex(2)
            if v is not None:
                return v
            if self.u:
                self.fail()
            return ord("x")
        if c == "u":
            if self.u and self.peek() == "{":
                end = self.src.find("}", self.i)
                digits = self.src[self.i + 1:end] if end != -1 else ""
                if not digits or not all(d in "0123456789abcdefABCDEF" for d in digits):
                    self.fail()
                self.i = end + 1
                cp = int(digits, 16)
                if cp > MAX_CODE_POINT:
                    self.fail()
                return cp
            v = self._hex(4)
            if v is not None:
                if self.u and 0xD800 <= v <= 0xDBFF and self.src.startswith("\\u", self.i):
                    save = self.i
                    self.i += 2
                    lo = self._hex(4)
                    if lo is not None and 0xDC00 <= lo <= 0xDFFF:
                        return 0x10000 + ((v - 0xD800) << 10) + (lo - 0xDC00)
                    self.i = save
                return v
            if self.u:
                self.fail()
            return ord("u")
        if c == "k" and (self.u or self.named):
            self.fail()         # a named backreference
        if c in "pP" and self.u:
            self.fail()         # a Unicode property escape
        if self.u and c not in _SYNTAX_CHARS and not (in_class and c == "-"):
            self.fail()
        return ord(c)

    # -- classes ----------------------------------------------------------

    def klass(self) -> str:
        negate = self.peek() == "^"
        if negate:
            self.i += 1
        items: list[tuple[int, int]] = []
        while True:
            c = self.peek()
            if not c:
                self.fail()
            if c == "]":
                self.i += 1
                break
            a = self._class_atom()
            if self.peek() == "-" and self.peek(1) not in ("]", ""):
                self.i += 1
                b = self._class_atom()
                if isinstance(a, int) and isinstance(b, int):
                    if a > b:
                        self.fail()
                    items.append((a, b))
                    continue
                if self.u:
                    self.fail()
                for x in (a, b):
                    items += [(x, x)] if isinstance(x, int) else x
                items.append((ord("-"), ord("-")))
                continue
            items += [(a, a)] if isinstance(a, int) else a
        if not self.u:
            # The text is code units: nothing past the BMP can match, so a
            # set like \W is cut to it (the source itself is already units).
            items = [(lo, min(hi, MAX_UNIT)) for lo, hi in items if lo <= MAX_UNIT]
        if self.icase:
            items = _fold(items)
        return _emit(items, negate)

    def _class_atom(self):
        c = self.peek()
        self.i += 1
        if c == "\\":
            got = self.escape(in_class=True)
            return got
        return ord(c)

    # -- the whole pattern --------------------------------------------------

    def run(self) -> str:
        src, out = self.src, self.out
        n = len(src)
        while self.i < n:
            c = src[self.i]
            if c == "\\":
                self.i += 1
                got = self.escape(in_class=False)
                if isinstance(got, tuple):
                    out.append(got[1])
                elif isinstance(got, int):
                    out.append(self.literal(got))
                else:
                    out.append(self._set(got))
                continue
            if c == "[":
                self.i += 1
                out.append(self.klass())
                continue
            if c == ".":
                self.i += 1
                ranges = ([(0, MAX_CODE_POINT)] if self.dotall
                          else _complement(LINE_TERMINATORS))
                out.append(_emit(ranges))
                continue
            if c == "^":
                self.i += 1
                out.append(f"(?:\\A|(?<={_TERM_CLASS}))" if self.multiline else "\\A")
                continue
            if c == "$":
                self.i += 1
                out.append(f"(?={_TERM_CLASS}|\\Z)" if self.multiline else "\\Z")
                continue
            if c == "(":
                self.i += 1
                if self.peek() == "?":
                    rest = src[self.i:]
                    for js, py in ((("?:", "?:")), ("?=", "?="), ("?!", "?!"),
                                   ("?<=", "?<="), ("?<!", "?<!")):
                        if rest.startswith(js):
                            out.append("(" + py)
                            self.i += len(js)
                            break
                    else:
                        m = re.match(r"\?<([A-Za-z_$][A-Za-z0-9_$]*)>", rest)
                        if m is None or "$" in m.group(1):
                            self.fail()     # inline flags and the like
                        out.append(f"(?P<{m.group(1)}>")
                        self.i += m.end()
                    continue
                out.append("(")
                continue
            if c == "{":
                m = _QUANT.match(src, self.i)
                if m is not None:
                    out.append(m.group(0))
                    self.i = m.end()
                    continue
                if self.u:
                    self.fail()
                out.append("\\{")
                self.i += 1
                continue
            if c in "}]":
                if self.u:
                    self.fail()
                out.append("\\" + c)
                self.i += 1
                continue
            if c in ")|*+?":
                out.append(c)
                self.i += 1
                continue
            self.i += 1
            out.append(self.literal(ord(c)))
        return "".join(out)


def parse(find_regex: str) -> JsRegex:
    """findRegex as SillyTavern builds it, rewritten for Python. Raises
    Unsupported when SillyTavern would not run it either, or when Python
    cannot match it the same way."""
    if not isinstance(find_regex, str) or not find_regex:
        raise Unsupported(REASON_SYNTAX)
    m = _LITERAL.search(find_regex)
    if m is None:
        raise Unsupported(REASON_SYNTAX)
    body, flags = m.group(2), m.group(3)
    if flags and not _FLAG_SET.fullmatch(flags):
        body, flags = find_regex, ""
    if set(flags) - _JS_FLAGS:
        raise Unsupported(REASON_FLAGS)
    if "u" in flags and "i" in flags:
        raise Unsupported(REASON_FLAGS)
    src = _Rewriter(body, flags).run()
    try:
        rx = re.compile(src)
    except (re.error, RecursionError, OverflowError, ValueError):
        raise Unsupported(REASON_SYNTAX) from None
    return JsRegex(rx, "g" in flags, "u" not in flags)


def to_units(text: str) -> str:
    """The text as UTF-16 code units (a character past the BMP becomes its
    surrogate pair)."""
    if all(ord(c) <= MAX_UNIT for c in text):
        return text
    out = []
    for c in text:
        cp = ord(c)
        if cp <= MAX_UNIT:
            out.append(c)
        else:
            cp -= 0x10000
            out.append(chr(0xD800 + (cp >> 10)) + chr(0xDC00 + (cp & 0x3FF)))
    return "".join(out)


def from_units(text: str) -> str:
    """Pairs put back together; a surrogate left alone becomes U+FFFD."""
    if not any(0xD800 <= ord(c) <= 0xDFFF for c in text):
        return text
    return text.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")
