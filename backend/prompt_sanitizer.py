"""prompt_sanitizer.py -- makes card and module text unable to pass for the app.

Spec: synthesis 4.4 (eleventh write) and Ek O 5. Port of
audit/prompt-research/sanitizer_ref.py; its vectors travel in
shared/sanitizer_vectors.json and the TypeScript mirror is
frontend/src/lib/prompt/sanitizer.ts.

WHAT IT IS NOT

It is not a content filter. It never reads meaning and never blocks a word or
a topic (owner, Ek O 5). It only breaks the FORMS that let third-party text
imitate the app: the app's own tags (`<craft>`, `</author_instructions_...>`),
the Limits / notebook / persona block prefixes, OOC markers, the layer's
placeholders, the voice block's header, and invisible characters used to
smuggle any of those past a reader. What the user types in the chat is never
passed through here.

WHAT CHANGES IN THE SENT TEXT, AND NOTHING ELSE

0.  line breaks folded to "\\n" (CR, CRLF, VT, FF, FS, GS, RS, NEL, LS, PS);
0b. tag characters outside a listed subdivision flag, bidi embeddings and
    isolates, variation selectors that are not the one legitimate selector
    after their base, and unassigned Default_Ignorable code points are
    deleted; every run of two or more invisible code points collapses to its
    first member (the one exemption is U+FE0F + ZWJ in an emoji sequence);
R.  matched marker spans are rewritten ("<" -> U+2039, "]"-closer -> U+203A,
    OOC -> "author aside", a space inside "((", after a line-start "/", inside
    "{{placeholder}}", and the voice header's first mark -> U+2039).

Matching runs on a SHADOW copy in which lookalikes, compatibility forms and
marks are folded away, so "(\u00d3OC:" and "\uff1ccraft\uff1e" are found. Turkish and every
other ordinary text is sent byte for byte as it came in.

WHY THE TABLE IS DATA

The per-character fold (NFKD, mark drop, confusables, the 4-code-point
expansion bound), the invisible class, the unassigned class, the Han ranges
and Python's whitespace set are all read from shared/prompt_sanitizer.json.
Nothing here asks `unicodedata` at run time for anything that changes the
output: the TypeScript mirror reads the same file, and two Unicode versions
would otherwise give two answers (c13 L1). Regenerating the table is a
deliberate change: `python prompt_sanitizer.py --write-table`.

CACHE

Card and module text is the same turn after turn, so the result is cached by
sha256 of the post-macro text plus RULES_VERSION: at most 256 entries and 32
MiB of text. Every piece of state sits behind one lock. A vault lock clears
it, bumps a generation and marks it locked; a build that started before the
lock may still serve its own request but is never stored. The unlock hook
lifts the mark. Both hooks are called from prompt_layer.on_vault_lock /
on_vault_unlock.

Privacy: no function here logs its input or its output.
"""

from __future__ import annotations

import bisect
import hashlib
import io
import json
import re
import sys
import threading
from array import array
from collections import OrderedDict
from pathlib import Path

#: Bumped whenever a rule or the table changes; part of the cache key.
RULES_VERSION = 11
#: Per prompt field after macros, in code points.
FIELD_MAX = 1 << 20
NAME_MAX = 64
#: A name is cut to this many code points before anything heavier runs on it.
NAME_PRECUT = 4 * NAME_MAX
DEFAULT_NAME = "Character"
CACHE_MAX_ENTRIES = 256
CACHE_MAX_CHARS = 32 << 20
FIELD_TOO_LARGE_CODE = "prompt_field_too_large"

LT, GT = "\u2039", "\u203a"
OOC_REPL = "author aside"
EXPANSION_MAX = 4
INERT = "~"

RESERVED = ["roleplay", "craft", "character", "scenario", "examples", "example",
            "author_instructions", "author_note", "reminder", "user_persona",
            "prompt_module"]
PREFIX_PHRASES = ["Limits", "End of limits", "Notebook", "End of notebook",
                  "User Persona"]
PLACEHOLDERS = ["nonce", "card_system_prompt", "card_post_history_instructions",
                "length_words", "module_text"]
DOUBLE_MAP = {"\u2e28": "( (", "\u2985": "( (", "\u2e29": ") )", "\u2986": ") )"}

_TABLE_NAME = "prompt_sanitizer.json"


class PromptFieldTooLarge(ValueError):
    """A prompt field is over FIELD_MAX code points after macros."""

    code = FIELD_TOO_LARGE_CODE

    def __init__(self) -> None:
        super().__init__(FIELD_TOO_LARGE_CODE)


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------

def shared_path(name: str) -> Path:
    """Where a shared/ file lives: beside the exe in a frozen build, else in
    the repository's shared/ directory."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", ".")) / "shared" / name
    return Path(__file__).resolve().parent.parent / "shared" / name


def _load_table() -> dict:
    with open(shared_path(_TABLE_NAME), encoding="utf-8") as fh:
        return json.load(fh)


def _class(ranges) -> str:
    """A regex character class body from [[a, b], ...] code point ranges."""
    out = []
    for a, b in ranges:
        out.append(re.escape(chr(a)) if a == b
                   else re.escape(chr(a)) + "-" + re.escape(chr(b)))
    return "".join(out)


def _ranges(cps) -> list[list[int]]:
    out: list[list[int]] = []
    for cp in sorted(cps):
        if out and out[-1][1] == cp - 1:
            out[-1][1] = cp
        else:
            out.append([cp, cp])
    return out


_HANGUL_FIRST, _HANGUL_LAST = 0xAC00, 0xD7A3


def _hangul(cp: int) -> str:
    """Canonical decomposition of a precomposed Hangul syllable (Unicode 3.12,
    arithmetic, the same in every Unicode version)."""
    s = cp - _HANGUL_FIRST
    lead, vowel, trail = 0x1100 + s // 588, 0x1161 + (s % 588) // 28, 0x11A7 + s % 28
    return chr(lead) + chr(vowel) + (chr(trail) if trail != 0x11A7 else "")


def expand_fold(table: dict) -> dict[int, str]:
    """The fold map from its compact form: [cp, s] one code point; [a, b, s]
    a run folding to the same s; [a, b, s, 1] a run folding to consecutive
    single characters starting at s; plus the arithmetic Hangul block."""
    out: dict[int, str] = {}
    for e in table["fold"]:
        if len(e) == 2:
            out[e[0]] = e[1]
        elif len(e) == 3:
            for cp in range(e[0], e[1] + 1):
                out[cp] = e[2]
        else:
            base = ord(e[2])
            for cp in range(e[0], e[1] + 1):
                out[cp] = chr(base + cp - e[0])
    if table.get("hangul"):
        for cp in range(_HANGUL_FIRST, _HANGUL_LAST + 1):
            out[cp] = _hangul(cp)
    return out


def compress_fold(fold: dict[int, str]) -> list:
    """Inverse of expand_fold, minus the Hangul block (the caller checks it)."""
    cps = sorted(c for c in fold if not _HANGUL_FIRST <= c <= _HANGUL_LAST)
    entries: list = []
    i = 0
    while i < len(cps):
        a, v = cps[i], fold[cps[i]]
        j = i
        while j + 1 < len(cps) and cps[j + 1] == cps[j] + 1 and fold[cps[j + 1]] == v:
            j += 1
        if j > i:
            entries.append([a, cps[j], v])
            i = j + 1
            continue
        if len(v) == 1:
            while (j + 1 < len(cps) and cps[j + 1] == cps[j] + 1
                   and len(fold[cps[j + 1]]) == 1
                   and ord(fold[cps[j + 1]]) == ord(v) + cps[j + 1] - a):
                j += 1
        if j > i:
            entries.append([a, cps[j], v, 1])
            i = j + 1
            continue
        entries.append([a, v])
        i += 1
    return entries


TABLE = _load_table()
FOLD: dict[str, str] = {chr(k): v for k, v in expand_fold(TABLE).items()}
_SPACE_CLASS = _class(TABLE["space"])
_INVIS_CLASS = _class(TABLE["invisible"])
_HAN = [tuple(r) for r in TABLE["han"]]
_HAN_STARTS = [a for a, _ in _HAN]
_NAME_STRIP = frozenset(chr(cp) for a, b in TABLE["name_strip"]
                        for cp in range(a, b + 1)) | frozenset("[]<>{}")
VALID_SUBDIVISION_FLAGS = frozenset(TABLE["subdivision_flags"])

SPECIAL = re.compile("[" + _class(_ranges(ord(c) for c in FOLD)) + "]")
INVIS_ONE = re.compile("[" + _INVIS_CLASS + "]")
#: Possessive quantifiers (3.11+): a megabyte of alternating members and
#: spaces would otherwise keep backtracking state per member (104 MiB, P9).
INVIS_RUN = re.compile("[" + _INVIS_CLASS + "](?:[" + _SPACE_CLASS + "]*+["
                       + _INVIS_CLASS + "])++")
CN_DI = re.compile("[" + _class(TABLE["unassigned_ignorable"]) + "]")
_SPACE_RUN = re.compile("[" + _SPACE_CLASS + "]+")
_SPACE_CHARS = "".join(chr(cp) for a, b in TABLE["space"]
                       for cp in range(a, b + 1))

BIDI_CONTROLS = re.compile("[\u202a-\u202e\u2066-\u2069]")
EMOJI_TAG_SEQ = re.compile("\U0001F3F4[\U000E0020-\U000E007E]+\U000E007F")
TAG_CHAR = re.compile("[\U000E0000-\U000E007F]")
VS = re.compile("[\ufe00-\ufe0f\U000E0100-\U000E01EF]")
LINEBREAKS = re.compile("\r\n|[\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029]")
DOUBLE_LOOKALIKE = re.compile("[\u2e28\u2e29\u2985\u2986]")
EMOJI_PAIR = "\ufe0f\u200d"

# ASCII rules. re.A keeps \s and case folding to ASCII, which is what the
# TypeScript mirror (no /u flag, explicit [ \t\n\r\f\v]) reproduces.
_WS = r"[ \t\n\r\f\v]"


def _words(phrase: str) -> str:
    return (_WS + "*").join((_WS + "*").join(re.escape(c) for c in w)
                            for w in phrase.split(" "))


def _sep(name: str) -> str:
    return name.replace("_", "[_ -]?")


R1 = re.compile(r"<%s*(?:/+%s*)?(?:%s)(?![A-Za-z0-9])"
                % (_WS, _WS, "|".join(_sep(n) for n in RESERVED)), re.I | re.A)
R2 = re.compile(r"\[%s*(?:%s|Character%s*:)"
                % (_WS, "|".join(_words(p) for p in PREFIX_PHRASES), _WS),
                re.I | re.A)
_SEPC = r"[ \t\n\r\f\v./\-_]"
_R3_VAR = r"[O0]%s?[O0]%s?C(?![A-Za-z0-9])\.?" % (_SEPC, _SEPC)
R3 = re.compile(r"(?<![A-Za-z0-9])OOC(?![A-Za-z0-9])\.?(?:%s*:)?" % _WS
                + r"|(?<=[(\[])%s*" % _WS + _R3_VAR + r"(?:%s*:)?" % _WS
                + r"|(?<![A-Za-z0-9])" + _R3_VAR + r"%s*:" % _WS
                + r"|out%s+of%s+character%s*:" % (_WS, _WS, _WS), re.I | re.A)
R5 = re.compile(r"(?m)^[ \t]*/(?=/)")
R6 = re.compile(r"\{\{%s?(?:%s)%s?\}\}" % (_WS, "|".join(PLACEHOLDERS), _WS),
                re.I | re.A)
R7 = re.compile(r"(?m)^[ \t]*([#*_=])[ \t#*_=]*VOICE%s+DELIVERY" % _WS,
                re.I | re.A)
_R4 = re.compile(r"(?=(\(\(|\)\)))")


def fold_linebreaks(t: str) -> str:
    return LINEBREAKS.sub("\n", t)


def _is_han(ch: str) -> bool:
    cp = ord(ch)
    i = bisect.bisect_right(_HAN_STARTS, cp) - 1
    return i >= 0 and cp <= _HAN[i][1]


def _valid_flag(seq: str) -> bool:
    return "".join(chr(ord(c) - 0xE0000) for c in seq[1:-1]) in VALID_SUBDIVISION_FLAGS


def _collapse_invisible(m: re.Match) -> str:
    g = m.group(0)
    head = EMOJI_PAIR if g.startswith(EMOJI_PAIR) else g[0]
    return head + INVIS_ONE.sub("", g[len(head):])


def strip_sent(t: str) -> str:
    """Step 0b, on the text that is sent."""
    if TAG_CHAR.search(t):
        keep = {m.start(): m.end() for m in EMOJI_TAG_SEQ.finditer(t)
                if _valid_flag(m.group(0))}
        out, i = [], 0
        while i < len(t):
            if i in keep:
                out.append(t[i:keep[i]])
                i = keep[i]
                continue
            if not TAG_CHAR.match(t[i]):
                out.append(t[i])
            i += 1
        t = "".join(out)
    t = BIDI_CONTROLS.sub("", t)
    if CN_DI.search(t):
        t = CN_DI.sub("", t)
    if VS.search(t):
        out = []
        pos = 0
        for m in VS.finditer(t):
            i = m.start()
            ch = t[i]
            out.append(t[pos:i])
            pos = i + 1
            prev = t[i - 1] if i else ""
            if not prev or VS.match(prev):
                continue
            if "\ufe00" <= ch <= "\ufe0f":
                if ch not in ("\ufe0e", "\ufe0f"):
                    continue
            elif not _is_han(prev):
                continue
            out.append(ch)
        out.append(t[pos:])
        t = "".join(out)
    return INVIS_RUN.sub(_collapse_invisible, t)


class _Identity:
    def __getitem__(self, i: int) -> int:
        return i


def shadow(t: str):
    """The matching copy and its index map back to `t`. ASCII is its own
    shadow; otherwise only table characters take the per-character path."""
    if t.isascii():
        return t, _Identity()
    out, idx, pos = [], array("I"), 0
    for m in SPECIAL.finditer(t):
        i = m.start()
        if i > pos:
            out.append(t[pos:i])
            idx.extend(range(pos, i))
        f = FOLD[t[i]]
        if f:
            out.append(f)
            idx.extend([i] * len(f))
        pos = i + 1
    if pos < len(t):
        out.append(t[pos:])
        idx.extend(range(pos, len(t)))
    return "".join(out), idx


def _positions(sh: str, ch: str) -> array:
    return array("I", (m.start() for m in re.finditer(re.escape(ch), sh)))


def _close_on_line(close_pos, nl_pos, after: int, sh_len: int, ops) -> int | None:
    """The first closer at or after `after` on the same line, by bisection."""
    if ops is not None:
        ops["close"] += 1
    j = bisect.bisect_left(close_pos, after)
    k = bisect.bisect_left(nl_pos, after)
    nl = nl_pos[k] if k < len(nl_pos) else sh_len
    return close_pos[j] if j < len(close_pos) and close_pos[j] < nl else None


def _r3_text(g: str) -> str:
    return OOC_REPL + (":" if g.rstrip().endswith(":") else "")


def _sanitize(text: str, ops: dict | None = None) -> str:
    """The rules, uncached. `ops` counts close-bracket searches (gate lane)."""
    t = strip_sent(fold_linebreaks(text))
    if DOUBLE_LOOKALIKE.search(t):
        # str.replace is linear; str.translate with str values is not.
        for k, v in DOUBLE_MAP.items():
            t = t.replace(k, v)
    done_simple = False
    if t.isascii():
        # None of these outputs can create or break an R1/R2 match, so R1/R2
        # run on the result below.
        t = R3.sub(lambda m: _r3_text(m.group(0)), t)
        while "((" in t:
            t = t.replace("((", "( (")
        while "))" in t:
            t = t.replace("))", ") )")
        t = R5.sub(lambda m: m.group(0) + " ", t)
        t = R6.sub(lambda m: "{ " + m.group(0)[1:], t)
        t = R7.sub(lambda m: m.group(0)[:m.start(1) - m.start()] + LT
                   + m.group(0)[m.end(1) - m.start():], t)
        done_simple = True
    sh, idx = shadow(t)
    ltp, gtp, ins = array("I"), array("I"), array("I")
    dbl: dict[int, str] = {}
    spans: dict[int, tuple[int, str]] = {}
    nl_pos = _positions(sh, "\n")
    gt_pos = _positions(sh, ">") if "<" in sh else array("I")
    rb_pos = _positions(sh, "]") if "[" in sh else array("I")
    for rx, closes in ((R1, gt_pos), (R2, rb_pos)):
        for m in rx.finditer(sh):
            ltp.append(idx[m.start()])
            p = _close_on_line(closes, nl_pos, m.end(), len(sh), ops)
            if p is not None:
                gtp.append(idx[p])
    if not done_simple:
        for m in R3.finditer(sh):
            g = m.group(0)
            a = m.start() + (len(g) - len(g.lstrip()))
            b = m.end() - (len(g) - len(g.rstrip()))
            spans[idx[a]] = (idx[b - 1] + 1, _r3_text(g))
        for m in _R4.finditer(sh):
            k = m.start()
            if idx[k] == idx[k + 1]:
                dbl[idx[k]] = "( (" if sh[k] == "(" else ") )"
            else:
                ins.append(idx[k])
        for m in R5.finditer(sh):
            ins.append(idx[m.end() - 1])
        for m in R6.finditer(sh):
            ins.append(idx[m.start()])
        for m in R7.finditer(sh):
            ltp.append(idx[m.start(1)])
    if not (ltp or gtp or ins or dbl or spans):
        return t
    events = sorted([(p, 0) for p in ltp] + [(p, 1) for p in gtp]
                    + [(p, 2) for p in dbl] + [(p, 3) for p in spans]
                    + [(p, 9) for p in ins])
    w = io.StringIO()
    pos, last = 0, None
    for i, code in events:
        if (i, code) == last or i < pos and code != 9:
            continue
        last = (i, code)
        if code == 9:                               # a space after char i
            if i < pos - 1:
                continue
            if i >= pos:
                w.write(t[pos:i + 1])
                pos = i + 1
            w.write(" ")
            continue
        w.write(t[pos:i])
        if code == 3:
            end, text_ = spans[i]
            w.write(text_)
            pos = end
            continue
        w.write(LT if code == 0 else GT if code == 1 else dbl[i])
        pos = i + 1
    w.write(t[pos:])
    return w.getvalue()


def strip_space(text: str) -> str:
    """Trim by the table's whitespace set. Python's str.strip and JavaScript's
    trim disagree on U+0085, U+001C-001F and U+FEFF (PK-10); both mirrors use
    this set instead."""
    return text.strip(_SPACE_CHARS)


def sanitize_name(text: str) -> str:
    """A card name, safe to print six times in the system message: one line,
    no brackets of any script, no markers, at most NAME_MAX code points."""
    t = (text or "")[:NAME_PRECUT]
    t = fold_linebreaks(t)
    t = _SPACE_RUN.sub(" ", t).strip(_SPACE_CHARS)
    t = "".join(c for c in t if c not in _NAME_STRIP)
    t = _sanitize(t)
    t = t[:NAME_MAX].strip(_SPACE_CHARS)
    return t or DEFAULT_NAME


def markers(text: str) -> list[str]:
    """What marker forms `text` still carries. A detector for tests and
    reports; it never changes what is sent."""
    import unicodedata

    found = []
    text = fold_linebreaks(text)
    for view in (shadow(text)[0], unicodedata.normalize("NFKC", text)):
        for label, rx in (("((", re.compile(r"\(\(")), ("))", re.compile(r"\)\)")),
                          ("OOC", R3), ("tag", R1), ("prefix", R2), ("//", R5),
                          ("placeholder", R6), ("voice", R7)):
            if rx.search(view):
                found.append(label)
    no_valid = EMOJI_TAG_SEQ.sub(
        lambda mm: "" if _valid_flag(mm.group(0)) else mm.group(0), text)
    if TAG_CHAR.search(no_valid):
        found.append("tagchar")
    if BIDI_CONTROLS.search(text):
        found.append("bidi")
    if any(_collapse_invisible(m) != m.group(0) for m in INVIS_RUN.finditer(text)):
        found.append("zwrun")
    if CN_DI.search(text):
        found.append("unassigned-ignorable")
    if re.search("[\ufe00-\ufe0f\U000E0100-\U000E01EF]{2,}", text):
        found.append("vsrun")
    return sorted(set(found))


# ---------------------------------------------------------------------------
# The cache
# ---------------------------------------------------------------------------

class SanitizeCache:
    """Bounded, lock-aware. See the module docstring."""

    def __init__(self, fn=None, max_entries: int = CACHE_MAX_ENTRIES,
                 max_chars: int = CACHE_MAX_CHARS) -> None:
        self._fn = fn or _sanitize
        self._d: OrderedDict = OrderedDict()
        self._chars = 0
        self._max_entries, self._max_chars = max_entries, max_chars
        self._gen = 0
        self._locked = False
        self._mu = threading.Lock()

    def get(self, text: str) -> str:
        if len(text) > FIELD_MAX:
            raise PromptFieldTooLarge()
        key = (hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest(),
               RULES_VERSION)
        with self._mu:
            if key in self._d:
                self._d.move_to_end(key)
                return self._d[key]
            gen = self._gen
        val = self._fn(text)                     # heavy work outside the lock
        with self._mu:
            if self._locked or gen != self._gen:
                return val                       # stale build: this request only
            self._d[key] = val
            self._chars += len(val)
            while len(self._d) > self._max_entries or self._chars > self._max_chars:
                _, old = self._d.popitem(last=False)
                self._chars -= len(old)
        return val

    def lock(self) -> None:
        with self._mu:
            self._d.clear()
            self._chars = 0
            self._gen += 1
            self._locked = True

    def unlock(self) -> None:
        with self._mu:
            self._locked = False

    def __len__(self) -> int:
        with self._mu:
            return len(self._d)


CACHE = SanitizeCache()


def sanitize_field(text: str) -> str:
    """Card field, greeting, module or model note: the copy that is SENT.
    Raises PromptFieldTooLarge over FIELD_MAX code points."""
    if not text:
        return text or ""
    return CACHE.get(text)


def on_vault_lock() -> None:
    CACHE.lock()


def on_vault_unlock() -> None:
    CACHE.unlock()


# ---------------------------------------------------------------------------
# Table generation (never runs in the app)
# ---------------------------------------------------------------------------

_IGNORABLE_RANGES = [
    (0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160),
    (0x17B4, 0x17B5), (0x180B, 0x180F), (0x200B, 0x200F), (0x202A, 0x202E),
    (0x2060, 0x206F), (0x3164, 0x3164), (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8), (0x1BCA0, 0x1BCA3), (0x1D173, 0x1D17A),
    (0xE0000, 0xE0FFF)]
_CONFUSABLES = {
    "\u0410": "A", "\u0412": "B", "\u0415": "E", "\u041a": "K", "\u041c": "M",
    "\u041d": "H", "\u041e": "O", "\u0420": "P", "\u0421": "C", "\u0422": "T",
    "\u0425": "X", "\u0430": "a", "\u0435": "e", "\u043e": "o", "\u0440": "p",
    "\u0441": "c", "\u0443": "y", "\u0445": "x", "\u0456": "i", "\u0406": "I",
    "\u0458": "j", "\u0455": "s", "\u04cf": "l",
    "\u0391": "A", "\u0392": "B", "\u0395": "E", "\u0396": "Z", "\u0397": "H",
    "\u0399": "I", "\u039a": "K", "\u039c": "M", "\u039d": "N", "\u039f": "O",
    "\u03a1": "P", "\u03a4": "T", "\u03a5": "Y", "\u03a7": "X", "\u03bf": "o",
    "\u03b9": "i", "\u03bd": "v", "\u03f9": "C", "\u03f2": "c",
    "\u0555": "O", "\u0585": "o", "\u054d": "U",
    "\u1d00": "a", "\u0299": "b", "\u1d04": "c", "\u1d07": "e", "\u026a": "i",
    "\u1d0b": "k", "\u029f": "l", "\u1d0d": "m", "\u0274": "n", "\u1d0f": "o",
    "\u0280": "r", "\ua731": "s", "\u1d1b": "t", "\u1d1c": "u",
    "\u0130": "I", "\u0131": "i", "\u0141": "L", "\u0142": "l", "\u00d8": "O",
    "\u00f8": "o",
    "\u2e28": "((", "\u2e29": "))", "\u2768": "(", "\u2769": ")", "\u276a": "(",
    "\u276b": ")", "\u27ee": "(", "\u27ef": ")", "\u2985": "((", "\u2986": "))",
    "\u3008": "<", "\u3009": ">", "\u02c2": "<", "\u02c3": ">", "\u27e8": "<",
    "\u27e9": ">", "\u276e": "<", "\u276f": ">", "\u226e": "<", "\u226f": ">",
    "\u27e6": "[", "\u27e7": "]", "\u3010": "[", "\u3011": "]",
    "\u2236": ":", "\ua789": ":", "\u0589": ":", "\u05c3": ":", "\u02d0": ":",
    "\u1361": ":", "\u2982": ":", "\u205a": ":",
}
_STRUCTURAL = set("()<>[]:{}/")


def build_table() -> dict:
    """The table, from this interpreter's unicodedata. Mirrors the reference
    implementation's per-character path exactly."""
    import unicodedata

    def ignorable(cp):
        return any(a <= cp <= b for a, b in _IGNORABLE_RANGES)

    def drop(ch):
        return ignorable(ord(ch)) or unicodedata.category(ch) in ("Cf", "Mn", "Me")

    def fold_char(ch):
        if drop(ch):
            return ""
        ch = _CONFUSABLES.get(ch, ch)
        piece = unicodedata.normalize("NFKD", ch)
        if len(piece) > EXPANSION_MAX:
            st = "".join(c for c in piece if c in _STRUCTURAL)
            return (st[:1] + INERT + st[1:])[:EXPANSION_MAX] if st else INERT
        res = []
        for p in piece:
            if drop(p):
                continue
            p = _CONFUSABLES.get(p, p)
            for q in p:
                if unicodedata.category(q) == "Zs" or q == "\u2800":
                    q = " "
                res.append(q)
        return "".join(res)

    fold, invisible, cn, han, name_strip, space = {}, [], [], [], [], []
    for cp in range(0x110000):
        if 0xD800 <= cp <= 0xDFFF:
            continue
        ch = chr(cp)
        if ch.isspace():
            space.append(cp)
        if cp < 0x80:
            continue
        cat = unicodedata.category(ch)
        if cat in ("Cn", "Co") and not ignorable(cp):
            continue          # unassigned or private use: in no class, no fold
        if (cat == "Cf" or ignorable(cp)) and not 0xE0020 <= cp <= 0xE007F:
            invisible.append(cp)
        if ignorable(cp) and cat == "Cn":
            cn.append(cp)
        name = unicodedata.name(ch, "")
        if "CJK UNIFIED IDEOGRAPH" in name or "CJK COMPATIBILITY IDEOGRAPH" in name:
            han.append(cp)
        if set(unicodedata.normalize("NFKC", ch)) & set("[]<>{}"):
            name_strip.append(cp)
        special = (cat in ("Cf", "Mn", "Me", "Zs") or cp == 0x2800 or ignorable(cp)
                   or ch in _CONFUSABLES or unicodedata.normalize("NFKD", ch) != ch)
        if special:
            f = fold_char(ch)
            if f != ch:
                fold[cp] = f
    hangul = all(fold.get(cp) == _hangul(cp)
                 for cp in range(_HANGUL_FIRST, _HANGUL_LAST + 1))
    if not hangul:
        raise RuntimeError("Hangul block no longer folds arithmetically; "
                           "extend compress_fold before regenerating.")
    return {
        "_why": ("Per-character fold and character classes for the prompt "
                 "sanitizer. Generated by `python backend/prompt_sanitizer.py "
                 "--write-table`; backend/prompt_sanitizer.py and "
                 "frontend/src/lib/prompt/sanitizer.ts both read it so they "
                 "cannot disagree on a Unicode version. Do not edit by hand."),
        "format": 1,
        "rules_version": RULES_VERSION,
        "unicode_version": unicodedata.unidata_version,
        "subdivision_flags": ["gbeng", "gbsct", "gbwls"],
        "space": _ranges(space),
        "invisible": _ranges(invisible),
        "unassigned_ignorable": _ranges(cn),
        "han": _ranges(han),
        "name_strip": _ranges(name_strip),
        "hangul": True,
        "fold": compress_fold(fold),
    }


def dump_table(table: dict) -> str:
    return json.dumps(table, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n"


#: The frontend's copy of the table. The dev server refuses imports from
#: outside the frontend root, so the frontend cannot read shared/ directly; a
#: frontend test holds this copy equal to shared/prompt_sanitizer.json.
TS_MIRROR = (Path(__file__).resolve().parent.parent / "frontend" / "src" / "lib"
             / "prompt" / "sanitizerTable.ts")


def dump_ts_mirror(table: dict) -> str:
    return ("// Generated by `python backend/prompt_sanitizer.py --write-table`. Do not\n"
            "// edit: it is shared/prompt_sanitizer.json, which a test compares it to.\n"
            "import type { SanitizerTable } from \"./sanitizerTableType\";\n\n"
            "export const SANITIZER_TABLE: SanitizerTable = "
            + json.dumps(table, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
            + ";\n")


if __name__ == "__main__":
    if sys.argv[1:] == ["--write-table"]:
        built = build_table()
        shared_path(_TABLE_NAME).write_text(dump_table(built), encoding="utf-8")
        TS_MIRROR.write_text(dump_ts_mirror(built), encoding="utf-8")
        print("wrote", shared_path(_TABLE_NAME), "and", TS_MIRROR)
    else:
        print("usage: python prompt_sanitizer.py --write-table")
