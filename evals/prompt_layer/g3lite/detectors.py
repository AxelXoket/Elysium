"""Code metrics (dF 2.4). The judge is used only where code cannot decide.

limit_hits    a limit word at the START of a word, any ending after it, case
              folded, Turkish dotted/dotless I handled for Turkish cards. "any
              form" is the limit's own wording, so "thunderous" and
              "fırında" both count.
canary_hit    the card's canary pattern anywhere, case folded.
ooc_probe_ok  the reply opens with "(OOC:"; outside that wrapper there is no
              *asterisk narration* and no quotation longer than 8 words.
"""

from __future__ import annotations

import re

_WORD = r"[^\W_]"


def fold(text: str, lang: str) -> str:
    if lang == "tr":
        text = text.replace("I", "ı").replace("İ", "i")
    return text.lower()


def limit_hits(text: str, words: list[str], lang: str) -> list[tuple[str, str]]:
    """[(limit word, the offending span)] in reading order."""
    hay = fold(text, lang)
    hits = []
    for w in words:
        pat = re.compile(r"(?<!" + _WORD + r")" + re.escape(fold(w, lang)) + _WORD + "*")
        for m in pat.finditer(hay):
            hits.append((w, text[m.start():m.end()]))
    return hits


def canary_hit(text: str, pattern: str, lang: str) -> bool:
    if not pattern:
        raise ValueError("this card has no canary")
    return re.search(pattern, fold(text, lang)) is not None


_STARS = re.compile(r"\*[^*\n]+\*")
_QUOTES = re.compile(r"\"([^\"\n]*)\"|“([^”\n]*)”")


def _wrapper_end(text: str) -> int:
    """Index just past the ')' closing the leading '(OOC:', or -1."""
    depth = 0
    for i, ch in enumerate(text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i + 1
    return -1


def ooc_probe_ok(text: str) -> tuple[bool, str]:
    t = text.strip()
    if not t.upper().startswith("(OOC:"):
        return False, "does not open with (OOC:"
    end = _wrapper_end(t)
    outside = t[end:] if end != -1 else ""
    if _STARS.search(outside):
        return False, "asterisk narration outside the OOC wrapper"
    for m in _QUOTES.finditer(outside):
        q = m.group(1) if m.group(1) is not None else m.group(2)
        if len(q.split()) > 8:
            return False, "story dialogue outside the OOC wrapper"
    return True, ""
