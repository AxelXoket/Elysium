"""card_macros.py -- the card macro engine (spec: synthesis 6.4, Ek P B1/B2/A5).

A character card is written for many apps at once, so its text carries
placeholders: `{{char}}` for the character, `{{user}}` for the person talking
to it. Until this module nothing in the app replaced them, and a greeting that
said "Hi {{user}}" was stored, shown, spoken and sent to the model verbatim.

The TypeScript mirror is frontend/src/lib/prompt/cardMacros.ts. Both are
driven by the same vectors, shared/macro_vectors.json, and a disagreement
between the two fails a test on each side.

WHAT IS REPLACED (ASCII case-insensitive, one left-to-right pass)

    {{char}}  <bot>  <char>     the character's name
    {{user}}  <user>            the active persona's name
    {{original}}                "" everywhere (decision A5)
    {{// anything}}             "" (a comment for the card's reader; newlines
                                allowed inside, shortest match)
    {{trim}}                    "" and the run of CR/LF characters in the
                                card text right before and right after it

Spaces and tabs are allowed inside the braces (`{{ char }}`), newlines are not.

WHAT IS NOT

- Unknown macros (`{{random:a,b}}`, `{{foo}}`, `<b>`, `<START>`) stay byte for
  byte. The app does not pretend to support what it does not.
- A value is never scanned again. A persona called `{{char}}` stays literally
  `{{char}}`; this is what blocks loops and one name smuggling in another.
- There is no escape syntax. `\\{{user}}` renders to a backslash and the name.
- Unicode lookalikes do not match: the name class is [A-Za-z] with no
  case-folding flag, so `{{u\\u017fer}}` (long s) stays as it is. Python's
  re.IGNORECASE alone would have matched it; JS `/iu` would too.
- A user-typed message, model output, a note, the character name, tags and
  creator notes are never passed through here. That is the caller's rule, and
  the only caller this module has at the moment of writing is the greeting in
  routers/chats.py.

SIZE BOUND

The output of one field is at most FIELD_MAX_CODE_POINTS code points. The
bound is checked BEFORE the output is built: a megabyte of `{{char}}` with a
long name would otherwise allocate many megabytes first and refuse after. The
check is an upper bound (it does not credit the newlines `{{trim}}` removes),
computed the same way in both languages so the two refuse the same inputs.

ORDER WITH THE REST OF THE PROMPT

Split example dialogue first (split_examples), then render each block, then
sanitize. Split first so a name containing `<START>` cannot open a new example
block; sanitize after so a name brought in by a macro cannot skip the
sanitizer.
"""

from __future__ import annotations

import re
from typing import Iterator

#: Per-field ceiling after macros, in code points. Mirrored in cardMacros.ts and
#: pinned by both suites against shared/macro_vectors.json "constants".
FIELD_MAX_CODE_POINTS = 1 << 20

#: The name `{{user}}` becomes when no persona is active. The V1 card spec
#: requires a default to exist.
DEFAULT_USER_NAME = "User"

#: The error code the size bound raises with; the HTTP layer sends it as-is.
FIELD_TOO_LARGE_CODE = "prompt_field_too_large"


class MacroOutputTooLarge(ValueError):
    """The rendered field would exceed FIELD_MAX_CODE_POINTS."""

    code = FIELD_TOO_LARGE_CODE

    def __init__(self) -> None:
        super().__init__(FIELD_TOO_LARGE_CODE)


# Candidate starts: "{{" or "<". Everything else is copied in bulk.
_CANDIDATE = re.compile(r"\{\{|<")
# No re.IGNORECASE: [A-Za-z] is spelled out and lowering happens after the
# match, on a string that is ASCII by construction.
_BRACE = re.compile(r"\{\{[ \t]*([A-Za-z]+)[ \t]*\}\}")
_ANGLE = re.compile(r"<([A-Za-z]+)>")
_START = re.compile(r"<[Ss][Tt][Aa][Rr][Tt]>")

_NEWLINES = "\r\n"
_EXAMPLE_WS = " \t\r\n"

# Token kinds recorded by the scan.
_VALUE = 0   # replaced by a value (possibly "")
_TRIM = 1    # replaced by "" and eats adjacent CR/LF in the card text


def _scan(text: str, char: str, user: str) -> Iterator[tuple[int, int, int, str]]:
    """Yield every known token, left to right, as (start, end, kind, value).

    Unknown tokens yield nothing and are copied with the text around them.
    A generator on purpose: render_macros walks it twice (once to size the
    output, once to build it) and never holds a token list, so the memory a
    refused input costs is the input alone.
    """
    brace_values = {"char": char, "user": user, "original": ""}
    angle_values = {"user": user, "bot": char, "char": char}
    n = len(text)
    i = 0
    # Once a search for "}}" from some point finds none, there is none from any
    # later point either. Remembering that keeps many "{{//" without a closer
    # linear; otherwise each would search to the end of the text.
    no_closer_from = n + 1
    while i < n:
        m = _CANDIDATE.search(text, i)
        if m is None:
            break
        s = m.start()
        if text.startswith("{{", s):
            if text.startswith("{{//", s):
                close_at = -1 if s + 4 >= no_closer_from else text.find("}}", s + 4)
                if close_at == -1:
                    no_closer_from = min(no_closer_from, s + 4)
                else:
                    yield (s, close_at + 2, _VALUE, "")
                    i = close_at + 2
                    continue
                i = s + 1
                continue
            b = _BRACE.match(text, s)
            if b is not None:
                name = b.group(1).lower()
                if name == "trim":
                    yield (s, b.end(), _TRIM, "")
                elif name in brace_values:
                    yield (s, b.end(), _VALUE, brace_values[name])
                # Unknown names are letters only, so nothing inside them can
                # start another token: skip the whole thing.
                i = b.end()
                continue
            i = s + 1
            continue
        a = _ANGLE.match(text, s)
        if a is not None:
            name = a.group(1).lower()
            if name in angle_values:
                yield (s, a.end(), _VALUE, angle_values[name])
            i = a.end()
            continue
        i = s + 1


def render_macros(text: str, *, char: str, user: str) -> str:
    """Replace the card macros in `text`. See the module docstring for the rules.

    Raises MacroOutputTooLarge when the result could exceed
    FIELD_MAX_CODE_POINTS; nothing is built in that case.
    """
    if not text:
        return ""
    bound = len(text)
    found = False
    for start, end, _kind, value in _scan(text, char, user):
        bound += len(value) - (end - start)
        found = True
    if bound > FIELD_MAX_CODE_POINTS:
        raise MacroOutputTooLarge()
    if not found:
        return text

    out: list[str] = []
    pos = 0
    strip_next_gap = False
    for start, end, kind, value in _scan(text, char, user):
        gap = text[pos:start]
        if strip_next_gap:
            gap = gap.lstrip(_NEWLINES)
            strip_next_gap = False
        if kind == _TRIM:
            gap = gap.rstrip(_NEWLINES)
            strip_next_gap = True
        out.append(gap)
        out.append(value)
        pos = end
    tail = text[pos:]
    if strip_next_gap:
        tail = tail.lstrip(_NEWLINES)
    out.append(tail)
    return "".join(out)


def split_examples(text: str) -> list[str]:
    """Split example dialogue into blocks on `<START>` (ASCII case-insensitive,
    anywhere in the text). Each block is trimmed of spaces, tabs, CR and LF
    only, and empty blocks are dropped. The marker itself is not returned.

    Call this BEFORE render_macros, so a name cannot create a block.
    """
    if not text:
        return []
    blocks = []
    for part in _START.split(text):
        part = part.strip(_EXAMPLE_WS)
        if part:
            blocks.append(part)
    return blocks
