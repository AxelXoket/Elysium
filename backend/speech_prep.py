"""speech_prep.py - the difference between the reply you READ and the one you HEAR.

A model writes for a screen: markdown emphasis, bullet lists, code fences, links,
digits, "Dr.". None of that is speech. Handed to an engine unchanged it produces
spoken asterisks, spelled-out URLs, and a pause in the middle of "Dr. Smith"
because the sentence splitter believed the period.

THE GOVERNING RULE IS: WHEN UNSURE, DO NOTHING.
Every layer here can only ever make a reply *worse* in one of two ways - by
speaking noise, or by silently deleting words. The first is a small annoyance
the user can hear and report. The second is undiagnosable by ear: the sentence
simply came out shorter and nobody knows why. So every heuristic below is
written to fail towards "leave it alone", and the ambiguous cases (`St.`,
version strings, arithmetic asterisks) are deliberately untouched with a test
each pinning that inaction.

ORDER IS LOAD-BEARING, and each step exists because a later one would otherwise
corrupt its input:
    1. code      - a fence may contain anything at all, so it leaves first
    2. links     - `[label](url)` must resolve before brackets mean "tag"
    3. tag mask  - `[whisper]` is frozen behind a placeholder so that symbol,
                   punctuation and number layers cannot chew on it
    4. narrative - `*...*` spans are decided while the asterisks still exist
    5. structure - headings/lists/rules become pauses, not spoken characters
    6. words     - abbreviations, then numbers, then punctuation and symbols
    7. user dict - applied FIRST within step 6 so a person can overrule us
    8. unmask    - the tags come back exactly as they were

The narrative scanner is a deliberate port of the frontend's `parseMessage.ts`.
Display and audio must agree about what counts as narration - if the screen
tints a span the ear should hear it as narration, and a divergence between the
two is the kind of bug nobody thinks to look for. `tests/narrative_corpus.py`
holds the shared cases; the same table is asserted on both sides.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Mapping

# ── options ──────────────────────────────────────────────────────────────────

NARRATIVE_MODES = ("same", "narrator", "skip")

#: What a narration span is prefixed with when the engine can read directions.
#: Free-form on purpose - this engine takes natural language, not an enum.
DEFAULT_NARRATOR_TAG = "narrator, measured, detached"

#: What CLOSES a narration span. A direction stands until the next tag or the
#: end of the sentence, so tagging narration without closing it handed the
#: narrator's tone to the dialogue that followed in the same breath:
#:     *She smiles and waves.* "It is good to see you again."
#: became one measured, detached line instead of a told action and a spoken
#: greeting. The asterisks end; the direction has to end with them.
DEFAULT_SPEECH_TAG = "in character, natural"

def injected_tags(speech_tag: str = DEFAULT_SPEECH_TAG) -> tuple[str, ...]:
    """Every tag THIS MODULE puts in the text, as opposed to the ones the model
    wrote. `voice_tags.sanitize_for_tts` is handed these so it can keep them
    without charging them to the density dial, which is a budget for the
    model's enthusiasm and not for a rendering choice the user made in
    settings.

    DERIVED, not a constant, because the closing direction is not fixed: it is
    the user's standing tone when they have set one. A constant tuple was
    right for exactly as long as both tags were hard-coded, and would have
    started silently charging the tone to the budget the moment it was not -
    the reply going plainer as it runs on, which reads as the model being dull
    rather than as an allowance running out.

    Takes the SAME value that goes into `PrepOptions.speech_tag`, so the
    injection and the exemption cannot name different strings.
    """
    return (DEFAULT_NARRATOR_TAG, speech_tag or DEFAULT_SPEECH_TAG)


@dataclass(frozen=True)
class PrepOptions:
    """How to turn one reply into speech.

    `engine_supports_tags` is not cosmetic: for an engine without inline
    directions a bracket is READ ALOUD, so both the model's tags and any tone
    we would add ourselves have to disappear instead of being risked.
    """
    engine_supports_tags: bool = False
    narrative: str = "same"
    narrator_tag: str = DEFAULT_NARRATOR_TAG
    speech_tag: str = DEFAULT_SPEECH_TAG
    pronunciations: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.narrative not in NARRATIVE_MODES:
            raise ValueError(f"narrative must be one of {NARRATIVE_MODES}")


# ── 1. code ──────────────────────────────────────────────────────────────────

_FENCE = re.compile(r"^[ \t]*```[^\n]*\n.*?^[ \t]*```[ \t]*$",
                    re.DOTALL | re.MULTILINE)
# Bounded by a BLANK LINE, not by end-of-string. `.*\Z` under DOTALL ran to the
# end of the message, so a reply whose fences did not pair up - the ordinary
# nested case, where a four-backtick outer fence never closes and _FENCE
# consumes the inner pair instead - had every word after the stray line deleted
# from the audio while the full text stayed on screen. The docstring below
# already promised the opposite: "dropped only from the fence to the end of
# that block, never further".
# The opener line plus the NON-BLANK lines under it - i.e. the block, and only
# the block. Spelled this way rather than as a lookahead because the newline
# that ends the opener is already consumed, so "the next line is blank" is not
# something a `(?=\n\s*\n)` can see from here.
_FENCE_UNCLOSED = re.compile(r"^[ \t]*```[^\n]*\n(?:[ \t]*\S[^\n]*\n?)*",
                             re.MULTILINE)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")


def _strip_code(text: str) -> str:
    """Blocks go; inline spans keep their word.

    A block is a wall of syntax - reading it is noise, and skipping it silently
    is the one deletion this module permits, because there is no plausible
    reading of `x = [1, 2]` that helps a listener. An unclosed fence (a cut
    stream, or a model that forgot) must NOT swallow the rest of the reply, so
    it is dropped only from the fence to the end of that block, never further.
    """
    text = _FENCE.sub("\n", text)
    text = _FENCE_UNCLOSED.sub("\n", text)
    return _INLINE_CODE.sub(r"\1", text)


# ── 2. links ─────────────────────────────────────────────────────────────────

# The target must LOOK like a target. Its sibling _REF_DEF below was hardened
# for exactly this and this one was not, so any parenthesis after a bracket was
# treated as a link and silently deleted: in a character chat
# "[Anna](whispering) You never listen." became "Anna You never listen." - the
# stage direction gone from the audio while it is still on screen. A URL, a
# path, a scheme or an anchor qualifies; an English word does not.
_MD_LINK = re.compile(
    r"!?\[([^\]\n]*)\]\(\s*<?"
    r"((?:https?://|www\.|mailto:|tel:|#|[./]|[A-Za-z]:[\\/])[^)\s]*)"
    r">?[^)]*\)")
# A markdown reference DEFINITION points at a URL or a path. The old `\S+`
# matched any text at all, so in a character-chat app a scripted line like
# "[Anna]: I told you not to come back." was erased whole before synthesis and
# the listener heard the scene skip a line that is still on screen.
_REF_DEF = re.compile(
    r"^[ \t]*\[[^\]\n]+\]:[ \t]*"
    r"<?(?:https?://|www\.|mailto:|[./]|[A-Za-z]:[\\/])\S*>?"
    r"(?:[ \t]+\S.*)?$",
    re.MULTILINE)
_BARE_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)


def _resolve_links(text: str) -> str:
    """Say the label, never the address.

    A URL read character by character is the single loudest way to ruin a
    reply, and nobody wants to hear "h t t p s colon slash slash". The label is
    what the sentence was built around, so it stays and the address goes.
    """
    text = _REF_DEF.sub("", text)
    text = _MD_LINK.sub(lambda m: m.group(1), text)
    return _BARE_URL.sub("", text)


# ── 3. tag masking ───────────────────────────────────────────────────────────

# The placeholder must survive EVERY later layer, which rules out digits: a
# numeric marker gets read as a number and comes back as the word "zero" - the
# exact collision the masking exists to prevent. Letters between NULs pass
# through the number, abbreviation, punctuation and symbol passes untouched,
# and it is category Cc so `_strip_symbols` leaves it alone too.
_SENTINEL = chr(1)          # Cc, so _strip_symbols leaves it; never typed
_MASK_FIND = re.compile(_SENTINEL + "t([a-j]+)" + _SENTINEL)


def _mask_key(i: int) -> str:
    body = "".join(chr(ord("a") + int(d)) for d in str(i))
    return _SENTINEL + "t" + body + _SENTINEL
# Same shape rule as voice_tags._looks_like_tag, kept in step with it on
# purpose: lowercase english words, few of them, no digits. Anything else is a
# citation, a checkbox or an array index and is none of our business.
# The `(?<![\w])` is the LEFT-hand boundary voice_tags.StreamStripper has had
# all along (`if self._last.isalnum(): emit`). Without it a bracket glued to a
# preceding word was masked as a delivery tag - and for the majority of engines
# (engine_supports_tags=False) _unmask_tags(keep=False) then DELETED it, so
# "The list is arr[idx] and mapping[name] here." was spoken as "The list is arr
# and mapping here.": words on screen, gone from the audio, with nothing to
# tell the listener the app removed them.
_TAG_SPAN = re.compile(r"(?<![\w])\[([a-z][a-z ,'\-]{2,39})\](?![(:])")
#: Kept in step with voice_tags._looks_like_tag, which uses the same number.
_TAG_MAX_WORDS = 6


def _mask_tags(text: str) -> tuple[str, list[str]]:
    held: list[str] = []

    def take(m: re.Match) -> str:
        # The SAME shape rule voice_tags uses, word count included:
        # _looks_like_tag caps a delivery tag at six words ("the prompt's own
        # under-six-words"). The mask had no word limit, so bracketed PROSE the
        # chat keeps on screen - "[i really am not sure about this]" - was
        # masked here and then deleted outright on every engine without inline
        # tags. A span voice_tags would not call a tag is not ours to remove.
        words = [w for w in re.split(r"[ ,\-]+", m.group(1).strip()) if w]
        if len(words) > _TAG_MAX_WORDS:
            return m.group(0)
        held.append(m.group(0))
        return _mask_key(len(held) - 1)

    return _TAG_SPAN.sub(take, text), held


def _unmask_tags(text: str, held: list[str], *, keep: bool) -> str:
    def put(m: re.Match) -> str:
        idx = int("".join(str(ord(c) - ord("a")) for c in m.group(1)))
        # An index we never issued means the SENTINEL PATTERN was already in
        # the reply rather than put there by us. Vanishingly unlikely - it is a
        # control character - but the alternative is an IndexError raised deep
        # inside preparation, which the queue turns into a dead utterance. A
        # reply must not lose its voice because a model emitted a stray byte.
        if idx >= len(held):
            return ""
        return held[idx] if keep else ""

    return _MASK_FIND.sub(put, text)


# ── 4. narrative ─────────────────────────────────────────────────────────────
# A port of frontend/src/lib/chat/parseMessage.ts. The rules are copied, not
# reinvented: an asterisk run of 1-3 is a delimiter candidate (1 = em,
# 2 = strong, 3 = both); opening requires a non-word char before and a
# non-space after; closing requires a non-space before; a blank line
# force-closes. Everything failing those guards stays literal text - which is
# what keeps `5*3*2` and `un*believ*able` out of this.

@dataclass
class _Seg:
    text: str
    em: bool


def _scan_emphasis(text: str) -> list[_Seg]:
    segs: list[_Seg] = []
    buf, em, strong = "", False, False
    n, i = len(text), 0

    def flush() -> None:
        nonlocal buf
        if buf:
            segs.append(_Seg(buf, em))
            buf = ""

    while i < n:
        c = text[i]
        if c != "*":
            if c == "\n" and i + 1 < n and text[i + 1] == "\n" and (em or strong):
                flush()
                em = strong = False
            buf += c
            i += 1
            continue

        run = 1
        while i + run < n and text[i + run] == "*":
            run += 1
        if run > 3:                                  # decorative divider
            buf += "*" * run
            i += run
            continue

        wants_em = run in (1, 3)
        wants_strong = run in (2, 3)
        any_on = (wants_em and em) or (wants_strong and strong)
        all_off = not (wants_em and em) and not (wants_strong and strong)
        prev = text[i - 1] if i > 0 else None
        nxt = text[i + run] if i + run < n else None

        if any_on and prev is not None and not prev.isspace():
            flush()
            if wants_em:
                em = False
            if wants_strong:
                strong = False
            i += run
            continue
        if (all_off and not (prev is not None and (prev.isalnum()))
                and nxt is not None and not nxt.isspace()):
            flush()
            if wants_em:
                em = True
            if wants_strong:
                strong = True
            i += run
            continue

        buf += "*" * run                             # literal asterisks
        i += run

    flush()
    return segs


def _apply_narrative(text: str, opts: PrepOptions) -> str:
    segs = _scan_emphasis(text)
    if not segs:
        return ""

    tag_narration = (opts.narrative == "narrator" and opts.engine_supports_tags)
    out: list[str] = []
    #: The narrator direction is still standing and has to be closed before
    #: anything that is NOT narration is performed under it.
    narrating = False
    for seg in segs:
        if not seg.em:
            if narrating and seg.text.strip():
                narrating = False
                lead = seg.text[:len(seg.text) - len(seg.text.lstrip())]
                body = seg.text.lstrip()
                # A direction the model chose for this very clause already
                # closes the narration and is more specific than ours, so two
                # are never stacked - the same rule apply_default_tone follows.
                # `[` is not what to look for: _mask_tags runs BEFORE this step
                # (see prepare), so the model's tag is already a sentinel
                # marker by now and a bracket test silently never matched.
                if not body.startswith((_SENTINEL, "[")):
                    out.append(f"{lead}[{opts.speech_tag}] {body}")
                    continue
                out.append(f"{lead}{body}")
                continue
            out.append(seg.text)
            continue
        if opts.narrative == "skip":
            # Drop the words but leave a boundary, so the speech either side
            # does not collide into one run-on breath.
            out.append(" ")
            continue
        if tag_narration:
            out.append(f"[{opts.narrator_tag}] {seg.text.strip()} ")
            narrating = True
        else:
            out.append(seg.text)
    return "".join(out)


# ── 5. structure ─────────────────────────────────────────────────────────────

_HEADING = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+(.*?)[ \t]*#*[ \t]*$", re.MULTILINE)
_LIST = re.compile(r"^[ \t]*(?:[-*+]|\d{1,3}[.)])[ \t]+", re.MULTILINE)
_QUOTE = re.compile(r"^[ \t]{0,3}>[ \t]?", re.MULTILINE)
_RULE = re.compile(r"^[ \t]{0,3}(?:[-*_][ \t]*){3,}$", re.MULTILINE)
_TABLE_SEP = re.compile(r"^[ \t]*\|?[ \t]*:?-{2,}:?[ \t]*(?:\|[ \t]*:?-{2,}:?[ \t]*)*\|?[ \t]*$",
                        re.MULTILINE)


def _strip_structure(text: str) -> str:
    """Layout markers become pauses.

    A heading is a sentence said with a break after it; a bullet is a beat. The
    marker itself is a typographic instruction and has no pronunciation, so it
    is replaced by the punctuation that produces the same rhythm - which also
    gives the sentence splitter a boundary it can see.
    """
    text = _RULE.sub("", text)
    text = _TABLE_SEP.sub("", text)
    text = _HEADING.sub(lambda m: _end_stop(m.group(1)), text)
    text = _QUOTE.sub("", text)
    text = _LIST.sub("", text)
    text = re.sub(r"[ \t]*\|[ \t]*", ", ", text)      # table cells
    return text


def _end_stop(s: str) -> str:
    s = s.strip()
    if not s:
        return ""
    return s if s[-1] in ".!?:;," else s + "."


# ── 6a. abbreviations ────────────────────────────────────────────────────────
# Expanding these does double duty: it fixes the pronunciation AND removes the
# period that would otherwise convince the sentence splitter it had reached the
# end of a sentence. `sentences()` therefore runs this pass too.

ABBREVIATIONS: dict[str, str] = {
    # titles
    "Dr.": "Doctor", "Mr.": "Mister", "Mrs.": "Missus", "Ms.": "Miz",
    "Prof.": "Professor", "Sr.": "Senior", "Jr.": "Junior",
    "Capt.": "Captain", "Sgt.": "Sergeant", "Lt.": "Lieutenant",
    # latin
    "e.g.": "for example", "i.e.": "that is", "etc.": "et cetera",
    "vs.": "versus", "cf.": "compare", "et al.": "and others",
    "N.B.": "note well",
    # measurement / commerce
    "approx.": "approximately", "est.": "estimated", "min.": "minimum",
    "max.": "maximum",
    # "No." (capitalised, and only mid-sentence) really is "number"; the
    # lowercase form is the commonest word in a conversation and was being
    # spoken as "number" while ALSO losing the full stop that ended the
    # sentence. Ambiguity this sharp belongs in the do-not-touch list.
    "No.": "number",
    "Inc.": "Incorporated", "Ltd.": "Limited", "Co.": "Company",
    "Dept.": "Department", "Univ.": "University",
    # time
    "a.m.": "A M", "p.m.": "P M", "Mon.": "Monday", "Tue.": "Tuesday",
    "Wed.": "Wednesday", "Thu.": "Thursday", "Fri.": "Friday",
    "Sat.": "Saturday", "Sun.": "Sunday",
    "Jan.": "January", "Feb.": "February", "Mar.": "March",
    "Apr.": "April", "Jun.": "June", "Jul.": "July", "Aug.": "August",
    "Sep.": "September", "Sept.": "September", "Oct.": "October",
    "Nov.": "November", "Dec.": "December",
}

#: Deliberately NOT expanded. Each is genuinely two words in English and the
#: sentence rarely settles which; guessing here would put the wrong word in the
#: user's ear with no way to tell it was us. They keep their period, which also
#: means the splitter keeps treating them conservatively.
AMBIGUOUS_ABBREVIATIONS = frozenset({
    "St.",      # Saint / Street
    "Ave.",     # Avenue, but also a name
    "Mt.",      # Mount / Mountain
    "Fr.",      # Father / French / from
    "Pl.",      # Place / Plural
    "Ct.",      # Court / Count
})

_ABBR_RE = re.compile(
    "(?<![\\w.])(" + "|".join(
        re.escape(k) for k in sorted(ABBREVIATIONS, key=len, reverse=True)
    ) + ")(?!\\w)")


#: Abbreviations whose period can ALSO be the end of the sentence. English
#: writes one dot for both jobs ("...bread, etc." is a finished sentence), so
#: expanding these blind removes a terminal the splitter and the engine both
#: need. Titles are deliberately absent: "Dr. Smith" would become "Doctor."
_TERMINAL_CAPABLE = frozenset({"etc.", "et al.", "a.m.", "p.m.", "N.B."})

_SENTENCE_END_AHEAD = re.compile(r"\s*(?:\Z|\n|[\"“(]?[A-Z])")


def _expand_abbreviations(text: str) -> str:
    def swap(m: re.Match) -> str:
        key = m.group(1)
        out = ABBREVIATIONS[key]
        if key in _TERMINAL_CAPABLE and _SENTENCE_END_AHEAD.match(text, m.end()):
            return out + "."
        return out

    return _ABBR_RE.sub(swap, text)


# ── 6b. numbers ──────────────────────────────────────────────────────────────

_ONES = ("zero one two three four five six seven eight nine ten eleven twelve "
         "thirteen fourteen fifteen sixteen seventeen eighteen nineteen").split()
_TENS = ("_ _ twenty thirty forty fifty sixty seventy eighty ninety").split()
_SCALES = ((1_000_000_000, "billion"), (1_000_000, "million"), (1_000, "thousand"))
_ORDINAL = {
    "one": "first", "two": "second", "three": "third", "five": "fifth",
    "eight": "eighth", "nine": "ninth", "twelve": "twelfth",
}


def _int_to_words(n: int) -> str:
    if n < 0:
        return "minus " + _int_to_words(-n)
    if n < 20:
        return _ONES[n]
    if n < 100:
        t, r = divmod(n, 10)
        return _TENS[t] + ("-" + _ONES[r] if r else "")
    if n < 1000:
        h, r = divmod(n, 100)
        return _ONES[h] + " hundred" + (" " + _int_to_words(r) if r else "")
    for value, name in _SCALES:
        if n >= value:
            q, r = divmod(n, value)
            return (_int_to_words(q) + " " + name
                    + (" " + _int_to_words(r) if r else ""))
    return str(n)


def _ordinal_words(n: int) -> str:
    words = _int_to_words(n)
    head, _, tail = words.rpartition("-")
    last = tail or words
    if last in _ORDINAL:
        new = _ORDINAL[last]
    elif last.endswith("y"):
        new = last[:-1] + "ieth"
    else:
        new = last + "th"
    return (head + "-" + new) if head else new


def _year_words(n: int) -> str:
    """1984 is "nineteen eighty-four", 2005 is "two thousand five".

    Both readings exist in English and they are not interchangeable: the paired
    form is how pre-2000 years are said, and the "two thousand" form is how the
    2000s are said. Picking one rule for all of them mispronounces half.
    """
    if 1100 <= n <= 1999:
        hi, lo = divmod(n, 100)
        if lo == 0:
            return _int_to_words(hi) + " hundred"
        return _int_to_words(hi) + " " + (
            "oh " + _ONES[lo] if lo < 10 else _int_to_words(lo))
    if 2000 <= n <= 2099:
        rest = n - 2000
        if rest == 0:
            return "two thousand"
        if n >= 2010:
            return "twenty " + _int_to_words(rest)
        return "two thousand " + _int_to_words(rest)
    return _int_to_words(n)


# A number is only converted when nothing alphanumeric, no asterisk and no dot
# touches it. That single guard is what protects `v1.2.0`, `5*3*2`, `a1`, and
# any identifier we have not thought of yet.
_EDGE_L = r"(?<![\w*./-])"
_EDGE_R = r"(?![\w*/-])"

_MONEY = re.compile(_EDGE_L + r"\$(\d{1,3}(?:,\d{3})*|\d+)(?:\.(\d{2}))?" + _EDGE_R)
_PERCENT = re.compile(_EDGE_L + r"(\d{1,3}(?:,\d{3})*|\d+)(?:\.(\d+))?%" + _EDGE_R)
_ORDINAL_RE = re.compile(_EDGE_L + r"(\d+)(?:st|nd|rd|th)\b", re.IGNORECASE)
_DECIMAL = re.compile(_EDGE_L + r"(\d+)\.(\d+)" + _EDGE_R)
_GROUPED = re.compile(_EDGE_L + r"(\d{1,3}(?:,\d{3})+)" + _EDGE_R)
_PLAIN = re.compile(_EDGE_L + r"(\d+)" + _EDGE_R)


def _digits_to_words(s: str) -> str:
    return " ".join(_ONES[int(d)] for d in s)


def _expand_numbers(text: str) -> str:
    def money(m: re.Match) -> str:
        whole = _int_to_words(int(m.group(1).replace(",", "")))
        unit = "dollar" if m.group(1).replace(",", "") == "1" else "dollars"
        if m.group(2):
            cents = int(m.group(2))
            if cents:
                return f"{whole} {unit} {_int_to_words(cents)}"
        return f"{whole} {unit}"

    def percent(m: re.Match) -> str:
        whole = _int_to_words(int(m.group(1).replace(",", "")))
        if m.group(2):
            return f"{whole} point {_digits_to_words(m.group(2))} percent"
        return f"{whole} percent"

    def plain(m: re.Match) -> str:
        n = int(m.group(1))
        # A bare four-digit number in prose is nearly always a year; anything
        # else keeps the ordinary cardinal reading.
        if len(m.group(1)) == 4 and 1100 <= n <= 2099:
            return _year_words(n)
        return _int_to_words(n)

    text = _MONEY.sub(money, text)
    text = _PERCENT.sub(percent, text)
    text = _ORDINAL_RE.sub(lambda m: _ordinal_words(int(m.group(1))), text)
    text = _DECIMAL.sub(
        lambda m: f"{_int_to_words(int(m.group(1)))} point "
                  f"{_digits_to_words(m.group(2))}", text)
    text = _GROUPED.sub(
        lambda m: _int_to_words(int(m.group(1).replace(",", ""))), text)
    return _PLAIN.sub(plain, text)


# ── 6c. punctuation, symbols, emoji ──────────────────────────────────────────

_SPACED_DASH = re.compile(r"\s+[-‐-―]+\s+")
_REPEAT_BANG = re.compile(r"([!?])\1+")
_ELLIPSIS = re.compile(r"\.{4,}|…")
_SLASH = re.compile(r"(?<=\w)\s*/\s*(?=\w)")
_AMP = re.compile(r"(?<!\w)&(?!\w)|(?<=\s)&(?=\s)")
_AT = re.compile(r"(?<=\s)@(?=\s)")
_ARROW = re.compile(r"\s*(?:->|→|=>)\s*")


def _clean_punctuation(text: str) -> str:
    """Marks that mean rhythm become rhythm; marks that mean a word become it.

    A spaced dash is a breath, so it becomes a comma rather than the spoken
    word "dash" - while a hyphen inside `well-known` is part of the word and
    must survive untouched. Repeated terminals are typed emphasis, not three
    separate questions, so they collapse. `...` is left exactly as it is: the
    engine already reads it as a trailing pause, which is the whole point of
    writing it.
    """
    text = _ELLIPSIS.sub("...", text)
    text = _REPEAT_BANG.sub(r"\1", text)
    text = _ARROW.sub(" to ", text)
    text = _SPACED_DASH.sub(", ", text)
    text = _SLASH.sub(" or ", text)
    text = _AMP.sub("and", text)
    text = _AT.sub("at", text)
    return text


def _strip_symbols(text: str) -> str:
    """Emoji and lone decoration leave; letters, digits and punctuation stay.

    Removal is by unicode CATEGORY rather than a hand-written list, because a
    list of emoji is out of date the day it is written. `So` (other symbol)
    covers emoji and dingbats; `Cf`/`Cs` covers the joiners and variation
    selectors that would otherwise be left behind as invisible debris.
    """
    out = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat in ("So", "Cf", "Cs", "Co"):
            continue
        out.append(ch)
    return "".join(out)


# ── 7. user pronunciations ───────────────────────────────────────────────────

def _apply_pronunciations(text: str, table: Mapping[str, str]) -> str:
    """The user's own dictionary, applied before ours.

    Somebody who has typed a pronunciation for a name has already heard us get
    it wrong; our built-in table must not get a second chance at it. Matching
    is whole-token so `Elysium` never fires inside `Elysiumly`, and entries
    that end in a period (`Dr.`) still match without the trailing-word guard
    tripping over their own punctuation.
    """
    if not table:
        return text

    def _substitute(segment: str) -> str:
        for src in sorted(table, key=len, reverse=True):
            if not src.strip():
                continue
            left = r"(?<![\w])"
            right = r"(?!\w)" if src.endswith(".") else r"(?![\w])"
            segment = re.sub(left + re.escape(src) + right,
                             lambda _m, v=table[src]: v, segment)
        return segment

    # Only BETWEEN the mask placeholders, never inside one. This runs at step 7,
    # by which point delivery tags have been replaced by `\x01t<letters>\x01`
    # markers - and \x01 is not a word character, so the marker's BODY matched
    # the very same `(?<![\w])...(?![\w])` boundaries a real word does. A user
    # entry for a short token like "ta" rewrote the INDEX of a held tag, and
    # _unmask_tags answers an index it never issued with `return ""`: the tag
    # was DELETED rather than restored. Splitting first makes that unreachable
    # instead of merely unlikely.
    out: list[str] = []
    last = 0
    for match in _MASK_FIND.finditer(text):
        out.append(_substitute(text[last:match.start()]))
        out.append(match.group(0))          # the placeholder, untouched
        last = match.end()
    out.append(_substitute(text[last:]))
    return "".join(out)


# ── whitespace ───────────────────────────────────────────────────────────────

def _tidy(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{2,}", "\n", text)
    text = re.sub(r"\n", " ", text)
    text = re.sub(r" +([,.!?;:])", r"\1", text)
    # `...` is a written pause the engine already reads correctly, so the
    # de-duplication below must not touch it: collapse four-or-more back to
    # three and a bare double to one, and leave an exact ellipsis alone.
    text = re.sub(r"\.{4,}", "...", text)
    text = re.sub(r"(?<!\.)\.\.(?!\.)", ".", text)
    text = re.sub(r"([,!?;:])\1+", r"\1", text)
    text = re.sub(r" {2,}", " ", text)
    return text.strip()


# ── the pipeline ─────────────────────────────────────────────────────────────

def prepare(text: str, opts: PrepOptions | None = None) -> str:
    """Written reply in, speakable text out.

    Idempotent by construction: running it twice yields the same string, which
    matters because the synthesis queue may re-prepare a tail after a retry and
    a pipeline that kept chewing would drift from what was already spoken.
    """
    opts = opts or PrepOptions()
    if not text or not text.strip():
        return ""

    text = _strip_code(text)
    text = _resolve_links(text)
    text, held = _mask_tags(text)
    text = _apply_narrative(text, opts)
    text = _strip_structure(text)
    text = _apply_pronunciations(text, opts.pronunciations)
    text = _expand_abbreviations(text)
    text = _expand_numbers(text)
    text = _clean_punctuation(text)
    text = _strip_symbols(text)
    text = _unmask_tags(text, held, keep=opts.engine_supports_tags)
    return _tidy(text)


# ── sentence splitting ───────────────────────────────────────────────────────
# Feeds the V8 synthesis queue. Two entry points because there are two callers:
# a finished message (`sentences`) and a stream still arriving
# (`sentences_ready`), and the second must never hand over half a sentence -
# an utterance cut mid-clause is spoken with the wrong cadence and no crossfade
# can repair it afterwards.

_TERMINAL = ".!?"
_CLOSERS = "\"'”’)]}"


def _split(text: str, *, require_complete: bool) -> tuple[list[str], str]:
    text = _expand_abbreviations(text)
    out: list[str] = []
    start = 0
    depth_quote = False
    # Emphasis depth, tracked for the same reason as quote depth. `_split` knew
    # about quotes but not about `*...*`, so a narration span covering more than
    # one sentence was cut into separate chunks and _apply_narrative - which
    # decides per chunk - lost the classification for every sentence after the
    # first: with "Narration: skip" the second half was spoken aloud, asterisk
    # included, and in "narrator" mode the tone flipped mid-span. The screen
    # tints the WHOLE span, so display and audio disagreed.
    #
    # The span is not held together (a long roleplay paragraph would become one
    # unstreamable utterance); it is re-opened instead - see `take`.
    depth_em = False
    prefix = ""
    i, n = 0, len(text)

    def take(end_at: int) -> None:
        """Emit text[start:end_at], keeping an open emphasis span open."""
        nonlocal start, prefix
        chunk = (prefix + text[start:end_at]).strip()
        if depth_em:
            # Close here and re-open at the head of the next chunk, so every
            # chunk carries its own complete `*...*` and prepares identically.
            chunk += "*"
            prefix = "*"
        else:
            prefix = ""
        if chunk:
            out.append(chunk)
        start = end_at

    while i < n:
        c = text[i]
        if c == "\n" and i + 1 < n and text[i + 1] == "\n" and depth_quote:
            # A blank line force-closes an open quote, exactly as _scan_emphasis
            # (`:221`) does for an open `*` span. Without it ONE unbalanced
            # quote mark suppressed every terminal for the rest of the reply:
            # a `24"` inch mark, or a closing quote the model simply dropped,
            # turned the remainder into a single unsplittable utterance. That
            # then hits the worker's max_new_tokens ceiling and the audio is
            # CUT OFF - and _breaks_a_span refuses every first_chunk seam on
            # the same parity check, so the fast start goes too.
            depth_quote = False
            i += 1
            continue
        if c in "\"“”":
            depth_quote = not depth_quote
            i += 1
            continue
        if c == "*":
            run = 1
            while i + run < n and text[i + run] == "*":
                run += 1
            # `**bold**` toggles twice and nets out; a lone `*` (or `***`) opens
            # or closes emphasis. 4+ is a decorative divider, not emphasis.
            if run <= 3 and run % 2 == 1:
                prev = text[i - 1] if i > 0 else None
                nxt_c = text[i + run] if i + run < n else None
                # Same opener/closer conditions as _scan_emphasis, so the two
                # cannot disagree about where a span starts. "5 * 3 = 15." has
                # spaces on both sides and toggles nothing.
                opening = (
                    not depth_em
                    and nxt_c is not None and not nxt_c.isspace()
                    and not (prev is not None and prev.isalnum())
                )
                closing = (
                    depth_em and prev is not None and not prev.isspace()
                )
                if opening or closing:
                    depth_em = not depth_em
            i += run
            continue
        if c not in _TERMINAL or depth_quote:
            i += 1
            continue

        run = i
        while run + 1 < n and text[run + 1] in _TERMINAL:
            run += 1
        end = run + 1
        while end < n and text[end] in _CLOSERS:
            end += 1

        nxt = text[end] if end < n else None
        # A terminal glued to a digit on both sides is a decimal, not an end.
        if (c == "." and i > 0 and text[i - 1].isdigit()
                and nxt is not None and nxt.isdigit()):
            i = end
            continue
        # A deliberately-unexpanded ambiguous abbreviation keeps its period
        # (see AMBIGUOUS_ABBREVIATIONS) - and that period is not a sentence end.
        if (c == "." and run == i
                and _ends_with_ambiguous_abbreviation(text, i)):
            i = end
            continue
        if nxt is None:
            if require_complete and _may_still_grow(text, i):
                break
            take(end)
            i = end
            continue
        if nxt.isspace():
            take(end)
            while start < n and text[start].isspace():
                start += 1
            i = start
            continue
        i = end

    # LSTRIP ONLY. The remainder is carried back into a GROWING buffer and the
    # next delta is concatenated onto it, so stripping the TRAILING space turns
    # "How are " + "you" into "How areyou" - two words welded together in the
    # middle of a spoken sentence, while the display copy stays correct so
    # nothing on screen reveals it. The leading space is safe to drop: it can
    # only be the boundary this function just cut.
    tail = text[start:].lstrip()
    # The re-opened prefix is BOOKKEEPING, not text. While the stream is still
    # growing it has to survive - the next delta is concatenated onto it and
    # completes the span - but on a final flush there is no next delta, and a
    # remainder of just `"*"` was handed on as a sentence. prepare("*") returns
    # "*", which is truthy, so the `if not spoken` guard never fired and a full
    # engine call (measured fixed cost 0.89-1.43 s) was spent saying nothing.
    if not require_complete and not tail:
        return [s for s in out if s], ""
    rest = prefix + tail
    return [s for s in out if s], rest


def _may_still_grow(text: str, dot: int) -> bool:
    """Is this trailing terminal possibly the middle of something?

    `It cost 3.` one delta later is `It cost 3.5`. Releasing it now would speak
    "It cost three." and then "five units" as a separate breath. Holding costs
    one delta of latency; releasing costs a wrong sentence.
    """
    if text[dot] != "." or dot <= 0:
        return False
    if text[dot - 1].isdigit():
        return True
    return _ends_with_ambiguous_abbreviation(text, dot)


# Longest ambiguous abbreviation, used to bound the lookbehind below.
_AMBIGUOUS_MAX = max(len(a) for a in AMBIGUOUS_ABBREVIATIONS)


def _ends_with_ambiguous_abbreviation(text: str, dot: int) -> bool:
    """Does the period at `dot` close a deliberately-unexpanded abbreviation?

    AMBIGUOUS_ABBREVIATIONS keep their period precisely so they stay readable
    ("St." can be Saint or Street). The splitter treated that period as a
    sentence terminal, so "He lives on St. Mary Ave. in town." became three
    utterances - each spoken with sentence-final falling intonation and a pause,
    which is exactly the half-sentence release this splitter forbids.
    """
    window = text[max(0, dot - _AMBIGUOUS_MAX): dot + 1]
    for abbr in AMBIGUOUS_ABBREVIATIONS:
        if window.endswith(abbr):
            before = dot - len(abbr)
            if before < 0 or not (text[before].isalnum() or text[before] == "."):
                return True
    return False


#: Where a first chunk may be cut, best first. The rank is acoustic, not
#: grammatical: it is how much the seam will be HEARD.
#:
#:   0  a sentence end. The model already puts falling intonation there and a
#:      listener already expects a pause - effectively inaudible.
#:   1  a semicolon, colon or dash. A real prosodic break in most readings.
#:   2  a comma. Audible: the fragment gets read as a complete sentence, so its
#:      pitch falls and the next piece starts fresh. Worth about three seconds
#:      of latency, and not worth more than that.
#:
#: A raw character count is deliberately NOT on this list. Cutting mid-phrase
#: makes the engine put a full stop where there is none, and that does not
#: sound like a seam - it sounds wrong.
# The dashes are written as escapes on purpose: P10 says an em dash appears
# nowhere in the source, and a literal U+2014 sat right here - in the module
# the rule is most about - while the only guard in the repo scanned frontend
# settings copy. See tests/test_p10_no_em_dash.py.
_CHUNK_BREAKS = ((_TERMINAL, 0), (";:", 1), ("-\u2013\u2014", 1), (",", 2))


def _breaks_a_span(head: str) -> bool:
    """Would cutting here leave an emphasis, quote or tag span hanging open?

    `_split` already refuses to cut a `*...*` narration span, and for the same
    reason: narrative tone is decided per chunk, so half a span is spoken in
    the wrong voice with the asterisk still in it.

    Brackets are here for a sharper reason. `[cold, clipped tone]` contains a
    comma, which first_chunk ranked as a legal seam, so a delivery tag was
    split down the middle - and voice_tags treats an UNCLOSED tag as plain
    text, so both halves were spoken out loud as words.
    """
    return (head.count("*") % 2 == 1
            or head.count('"') % 2 == 1
            or head.count("[") != head.count("]"))


def _unsafe_seam(text: str, i: int) -> bool:
    """Would cutting immediately after `text[i]` land inside something?

    `_split` grew four guards for precisely this question and `first_chunk`
    had none of them - it treated every `.` `;` `:` `-` `,` inside its window
    as a legal seam. Measured on real replies: "3.5 million" became "three."
    plus "five million"; "Dr. Smith" became "Doctor" plus "Smith"; a URL cut
    in half stopped matching _BARE_URL and was therefore read ALOUD as an
    address instead of being dropped.

    The guards live here rather than being copied so the two cutters cannot
    drift apart again.
    """
    ch = text[i]
    nxt = text[i + 1] if i + 1 < len(text) else None
    prev = text[i - 1] if i > 0 else None

    # 1. A terminal that is not followed by space is not ending a sentence -
    #    the same rule _split applies, allowing for closing punctuation.
    if ch in _TERMINAL and nxt is not None and not nxt.isspace() \
            and nxt not in _CLOSERS:
        return True
    # 2. A separator glued to digits on both sides is part of a NUMBER:
    #    3.5, 1,000, 2-3, 10:30. Every one of these ranks as a seam.
    if (prev is not None and prev.isdigit()
            and nxt is not None and nxt.isdigit()):
        return True
    # 3. A deliberately-unexpanded ambiguous abbreviation keeps its period,
    #    and that period is not a sentence end.
    if ch == "." and _ends_with_ambiguous_abbreviation(text, i):
        return True
    # 4. Inside a bare URL. Cutting one both speaks it and defeats the
    #    stripper that exists to stop that.
    for match in _BARE_URL.finditer(text):
        if match.start() <= i < match.end():
            return True
    return False


def first_chunk(text: str, *, min_chars: int,
                max_chars: int) -> tuple[str, str] | None:
    """Cut a short opening piece off `text` so speech can start sooner.

    Returns `(head, tail)`, or None when there is no good place to cut - in
    which case the caller speaks the whole thing and accepts the slower start.
    Refusing is a real answer: a bad seam is permanent, the latency is not.

    The window is the caller's, measured rather than chosen (see
    `tts/pacing.py`). The lower bound is not politeness - a first chunk shorter
    than that cannot cover the second chunk's synthesis, so it buys a fast start
    and pays for it with a gap two seconds later.
    """
    if not text or max_chars <= 0 or len(text) <= max_chars:
        return None                  # already short enough to be the first chunk
    # ONE left-to-right pass, not one pass per class. Scanning class by class
    # looks equivalent and is not: `;` and `-` share a rank, so a semicolon at
    # 80 would lock in before the dash class was ever consulted and a dash at
    # 45 - equally good and earlier - could never win.
    #
    # Because the scan runs left to right, the first sighting of any rank is
    # already the earliest one at that rank, so a strictly-better test is all
    # that is needed to get "strongest break, earliest among equals".
    best: tuple[int, int] | None = None       # (rank, index)
    for i, ch in enumerate(text[:max_chars + 1]):
        if i < min_chars:
            continue
        rank = next((r for chars, r in _CHUNK_BREAKS if ch in chars), None)
        if rank is None or (best is not None and rank >= best[0]):
            continue
        if _unsafe_seam(text, i):
            continue
        if _breaks_a_span(text[:i + 1]):
            continue
        best = (rank, i)
        if rank == 0:
            break                     # nothing can beat a sentence end
    if best is None:
        return None
    head = text[:best[1] + 1].strip()
    tail = text[best[1] + 1:].strip()
    if not head or not tail:
        return None
    return head, tail


def sentences(text: str) -> list[str]:
    """Split a COMPLETE text. The tail is always released."""
    if not text or not text.strip():
        return []
    done, rest = _split(text, require_complete=False)
    if rest:
        done.append(rest)
    return done


def sentences_ready(buffer: str, *, flush: bool = False) -> tuple[list[str], str]:
    """Split a GROWING buffer.

    Returns the sentences that are certainly finished plus whatever is left to
    carry into the next delta. `flush=True` at end-of-stream releases the tail
    even without a terminal - a reply that simply stopped still gets spoken.
    """
    if not buffer or not buffer.strip():
        return [], ""

    # ORDER IS LOAD-BEARING (module header, rule 1): code leaves FIRST.
    #
    # This used to split the RAW buffer, so a fence containing sentence-ending
    # punctuation was cut across the boundary: the opening half was then dropped
    # by prepare() as an unclosed fence, and the closing half was judged as
    # ordinary prose. A reply whose fence held `import os. os.path` reached the
    # engine as "Run this:" then "os.path foo.bar" - the code read aloud and the
    # real sentence that followed the fence silently deleted.
    speakable = _FENCE.sub("\n", buffer)

    # A fence that has not closed yet is still ARRIVING. Everything from its
    # opener onward is carried forward untouched; splitting it now would make
    # exactly the cut described above.
    unclosed = _FENCE_UNCLOSED.search(speakable)
    carried = ""
    if unclosed is not None:
        speakable, carried = speakable[:unclosed.start()], speakable[unclosed.start():]

    done, rest = _split(speakable, require_complete=True)
    rest += carried
    if flush and rest:
        # prepare() drops an unclosed fence, so a flushed tail that is nothing
        # but code yields no audio and no error - it simply had no words.
        done.append(rest)
        rest = ""
    return done, rest
