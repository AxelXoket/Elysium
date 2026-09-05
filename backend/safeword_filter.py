"""safeword_filter.py - the safeword taken out of a request body on its way out.

Public API:
    needles()               - every spelling of the safeword, read from the
                              vault. Raises VaultLockedError rather than
                              guessing.
    scrub(payload, needles) - returns the body with every occurrence of the
                              safeword removed from the parts a person wrote,
                              and everything else left exactly as it arrived.
                              Pure: the vault read is the caller's, made once
                              on the trip it already makes for the API key.

TWO ANSWERS, AND WHY THEY DIFFER
--------------------------------
A message BEING TYPED that carries the safeword is refused whole, in
`routers/completions.py`: the turn does not happen, nothing is sent, nothing
is stored, and the reader is told why. That is the emergency stop and nothing
here weakens it.

Everything else in an outgoing body is a different situation with the same
word in it. A line stored last week, a note the reader wrote about a scene, a
character card, a stop sequence: the turn carrying those was asked for NOW,
about text written BEFORE, and there is nobody who just typed the word to be
told anything. Refusing that turn would turn a word written once into a
permanent stop on every later conversation, announced at a moment that has
nothing to do with it. So the word is CUT and the rest of the body goes.

The two together are what makes the promise true in both directions: the word
never reaches a provider, and the app does not become unusable for having
contained it once.

WHERE THIS RUNS
---------------
`openrouter.py`, in both payload builders, after the body is complete and
before the socket. That is the only place all three senders meet - the plain
completion route, the streaming one, and the background note reader - and the
only place that sees the WHOLE body rather than one caller's idea of it. A cut
made in `_assemble_messages` would miss the note reader entirely, and would
miss `stop` on the two routes it does cover, because `stop` never travels
inside `messages`.

WHAT IS NEVER CUT
-----------------
`PROTOCOL_FIELDS` below. A model id, a provider policy, a response schema and
a modality list are not things a person wrote; they are protocol values, and a
three character safeword is short enough to land inside one by accident.
`exit` sits inside `vendor/exit-model`; `str` sits inside every `"string"` a
JSON schema declares. Cutting there protects nobody and produces a 404 or a
reply parsed against a schema that is no longer a schema. They are SCANNED, so
a match is visible in the log as a field name, and left alone.

Data URIs are skipped for a measured reason rather than a tidy one. Base64 is
sixty four characters wide, so a three letter safeword occurs in it by chance
about once every forty thousand characters: measured on one megabyte of real
random bytes, `kes` appears fifty one times and `dur` forty seven. Treated as
prose, one attached photograph comes out with fifty holes in it - not degraded,
destroyed, and destroyed silently, with the model answering anyway about a
picture it could not read.

WHAT THE CUT TAKES
------------------
The WORD, and nothing around it. Not the line, not the sentence, not the
message. The match is a SUBSTRING match, as it is everywhere else in this
feature, so `aKIRMIZIb` becomes `ab`; narrowing it to whole words would mean
missing, and a miss here is not recoverable.

Whitespace is left where it falls: `no wait KIRMIZI stop` becomes
`no wait  stop`, with two spaces. Tidying that up would be a second, separate
decision about text somebody else wrote, and there is no version of it that is
right for every language in the box.

The one case where the cut takes more than the word is a message it EMPTIES.
An empty turn is not a smaller message, it is a different request: providers
answer `{"role": "user", "content": ""}` or refuse it in ways that have
nothing to do with what was written, and the answer arrives looking like a
reply to the line the user can still see on their own screen. There is nothing
left of that line to send, so the line is not sent.

WHAT THIS DOES NOT DO
---------------------
It does not touch the vault. A safeword that got into a stored message or into
a note stays there, and stays visible on screen; only the copy on its way to
the provider is trimmed. That divergence is deliberate: the user's own record
of their conversation is not the provider's to edit, and rewriting stored rows
on a background timer is a destructive operation this control never asked for.
Cleaning old rows, if it is ever wanted, is a separate piece of work with its
own undo.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Sequence
from typing import Any

#: One folded spelling of a string, and where each of its characters came
#: from: the raw start and the raw end of the source run behind it. Two
#: sequences rather than one list of pairs because the fast path below hands
#: back a pair of `range` objects and allocates nothing at all.
FoldMap = tuple[str, Sequence[int], Sequence[int]]

logger = logging.getLogger(__name__)


#: Top-level body fields that are scanned and never cut. See the module
#: docstring: these are protocol values, not prose, and a short safeword lands
#: inside one by accident often enough to be worth naming.
PROTOCOL_FIELDS = frozenset({
    "model", "provider", "response_format", "modalities", "stream",
})

#: Where a picture's address sits, once it has been resolved. See below.
_PICTURE_ADDRESS: tuple[str, str] | None = None


def _picture_address() -> tuple[str, str]:
    """The two keys an image part is built from, asked of the builder.

    ONE value is skipped for WHERE it sits rather than for what it says: a
    picture's address. A hosted picture arrives as an ordinary https URL with
    nothing in it to recognise, and a URL is a protocol value like a model id
    - cut, it fetches nothing. Only the address is skipped, so a caption or an
    alt text beside it is still read.

    IMPORTED rather than spelled here. P-03 in
    tests/test_privacy_source_boundaries.py holds attachments_service as the
    single construction site for an outbound image part, and a filter that
    recognises one is not an exception to that rule: two spellings of the same
    key is how the two halves drift apart.

    LAZILY, because attachments_service pulls in Pillow and this module is
    imported by the provider client on every path, including all the ones with
    no picture anywhere in them.
    """
    global _PICTURE_ADDRESS
    if _PICTURE_ADDRESS is None:
        from attachments_service import IMAGE_PART_KEY, IMAGE_PART_URL_KEY
        _PICTURE_ADDRESS = (IMAGE_PART_KEY, IMAGE_PART_URL_KEY)
    return _PICTURE_ADDRESS

#: The other skip, and it reads what the value IS. A data URI is base64 and
#: base64 is not prose: sixty four characters wide, so a three letter safeword
#: occurs in it by chance about once every forty thousand characters.
#:
#: MATCHED WHOLE, and that is the point rather than pedantry. A test on the
#: first five characters is a test any user can pass by starting a sentence
#: with `data:` - and the sentence most likely to do that is a stored one,
#: which is the whole category this filter exists for. It failed silently:
#: the body went out untouched and nothing was logged. So the string has to be
#: a media type, `;base64`, and then nothing but base64 to the end.
#:
#: Case-insensitive, because a URI scheme is. `DATA:` skipped the guard and
#: the picture came out with holes in it.
_DATA_URI = re.compile(
    r"data:[\w.+-]+/[\w.+-]+(?:;[\w.+-]+=[\w.+-]*)*;base64,[A-Za-z0-9+/=]*\Z",
    re.IGNORECASE | re.ASCII)

#: The mark `notebook_store._fold_tr` strips from both candidates, so a
#: safeword typed decomposed cannot carry one the stored form does not.
_COMBINING_DOT = "\u0307"

#: How many times the cut may be re-applied to one string before the string is
#: emptied instead.
#:
#: More than one pass is needed because removing a match can build a new one
#: out of what was on either side of it: `KKeKEkesSses` needs three, with a
#: three letter safeword and no Unicode involved at all. Measured over a
#: fuzz corpus, no input needed more than four. Sixteen is far enough above
#: that to be a bound rather than a behaviour, and low enough that a string
#: crafted to reform forever costs sixteen linear passes and not a hang.
_MAX_PASSES = 16


def needles() -> tuple[str, ...]:
    """Every spelling of the safeword, or empty when it is not set.

    Folded by `notebook_store._fold_tr`, the same function the match itself
    uses. Re-deriving the fold here would be re-implementing the one thing in
    this feature that has already been wrong once, in a second place where the
    next fix would have to be remembered.

    READS THE VAULT, and does not soften a vault that will not open. A locked
    vault means the safeword is unknown, and a body sent while it is unknown
    is a body that may be carrying it. `VaultLockedError` therefore travels:
    the app already answers it everywhere else, nothing has been sent, and
    nothing was billed. Guessing "no safeword" here would turn the one control
    in this app that IS a control into a control that fails open.

    Public because it is also the seam a test uses to say "there is no vault
    in this scenario" - several files drive the two provider functions with a
    stubbed key and no database at all.
    """
    import notebook_store

    word = notebook_store.safeword()
    if not word:
        return ()
    return tuple(n for n in notebook_store._fold_tr(word) if n)


def _fold_candidates(text: str) -> tuple[str, ...]:
    import notebook_store

    return notebook_store._fold_tr(text)


def _matches(text: str, needles: tuple[str, ...]) -> bool:
    """`safeword_in`, without the setting read.

    `notebook_store.safeword_in` opens the vault to fetch the word. Called
    once per string leaf of a large body that is one SQLCipher read per leaf,
    which is the difference between a filter that costs a millisecond and one
    that costs seconds. The word is read once, in `scrub`.
    """
    hays = _fold_candidates(text)
    return any(needle in hay for needle in needles for hay in hays)


# ── The cut ─────────────────────────────────────────────────────────────────
#
# The match is found in FOLDED text and applied to RAW text, and those are not
# the same length. Two things move: NFC normalisation (a decomposed `Ğ` is two
# characters and composes to one) and a combining dot already present in the
# source, which the fold strips. So an offset found in the fold cannot be used
# on the raw string - it points one or more characters too far along, and the
# cut takes the wrong run: the safeword survives in pieces and a letter of the
# surrounding text goes with it. Measured on `unicodedata.normalize("NFD",
# "ĞUVEN")`, which is six characters folding to five.
#
# So the fold carries a map: every folded character remembers the raw run it
# came from. Text that already sits in NFC and has no dotted capital I in it
# cannot move a character, so it takes a fast path where the map is the
# identity and nothing is allocated; everything else is folded one run at a
# time and the map is built as it goes.


def _groups(text: str) -> list[tuple[int, int]]:
    """Split into (start, end) runs of a base character and its marks."""
    groups: list[tuple[int, int]] = []
    start = 0
    for i, ch in enumerate(text):
        if i and unicodedata.combining(ch) == 0:
            groups.append((start, i))
            start = i
    if text:
        groups.append((start, len(text)))
    return groups


def _segments(text: str) -> list[tuple[int, int, str]]:
    """Raw spans that NFC may be applied to one at a time, and their result.

    Composition mostly happens inside one base-plus-marks run, so those runs
    are the candidate boundaries. They are not all safe: a few hundred code
    points compose from two BASE characters (Bengali and Oriya vowel signs,
    Hangul jamo), and splitting between those two would normalise each half to
    itself and lose the composition.

    Rather than carry a table of which characters those are - which goes stale
    every Unicode release, and costs a fifth of a second to build by scanning -
    the split is REPAIRED against the answer. The whole string is normalised
    once, and a candidate segment is accepted only if it appears at the
    position it claims. One that does not is grown FORWARD, a run at a time,
    until it does: the run holds the first half of a composition and the
    second half is in the run after it. Candidate boundaries are a superset of
    the safe ones, because every one of them falls on a character with no
    combining class, so growing is the only repair needed and the result is
    exact for every script.

    The SHORTEST accepted segment is the right one. A run that composes with
    what follows cannot match on its own, because composing replaces its first
    character; a run that does not compose has nothing longer to be part of.
    """
    whole = unicodedata.normalize("NFC", text)
    groups = _groups(text)
    out: list[tuple[int, int, str]] = []
    at = 0
    i = 0
    while i < len(groups):
        a = groups[i][0]
        j = i
        while True:
            b = groups[j][1]
            piece = unicodedata.normalize("NFC", text[a:b])
            if whole.startswith(piece, at):
                break
            j += 1
            if j >= len(groups):        # pragma: no cover
                # Unreachable: the rest of the string always matches the rest
                # of its own normalisation. Here so that a future change
                # cannot make this loop silently drop the tail.
                b = len(text)
                piece = unicodedata.normalize("NFC", text[a:])
                break
        out.append((a, b, piece))
        at += len(piece)
        i = j + 1
    return out


def _map_to_composed(text: str) -> FoldMap:
    """NFC of `text`, and the raw span behind each character of it.

    The common case costs one quick-check and nothing else: text already in
    NFC composes to itself, so every character keeps its own place and the map
    is a pair of ranges that allocate nothing.

    Otherwise the segments above are normalised one at a time. A segment whose
    NFC is the same string it started as still maps character for character; a
    segment that actually composed maps as a whole, because there is no
    honest way to say which of `G` and its breve the `\u011e` came from. That
    is wider than the match by at most the marks on its first and last letter,
    and never narrower.
    """
    if unicodedata.is_normalized("NFC", text):
        return text, range(len(text)), range(1, len(text) + 1)

    pieces: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    for a, b, piece in _segments(text):
        pieces.append(piece)
        if piece == text[a:b]:
            starts.extend(range(a, b))
            ends.extend(range(a + 1, b + 1))
        else:
            starts.extend([a] * len(piece))
            ends.extend([b] * len(piece))
    return "".join(pieces), starts, ends


def _fold_mapped(composed: str, starts, ends, turkish: bool) -> FoldMap | None:
    """One candidate spelling of `composed`, carrying the map through it.

    The three steps are `notebook_store._fold_tr`'s, in its order, and they
    are applied to the WHOLE string rather than piece by piece. That is not a
    speed choice. `str.lower()` has one context-sensitive rule - a Greek sigma
    at the end of a word lowercases to a different letter than one in the
    middle - and a fold applied a segment at a time never sees the end of the
    word. Measured: it disagreed with the authority on about one percent of a
    mixed-script corpus, and every disagreement was a sigma. A disagreement
    there is not cosmetic; it is the matcher saying yes and the cut finding
    nothing, which ends with the whole value dropped.

    The map survives the lowercasing because that rule changes the letter and
    not the COUNT. Where some character does change the count, the widths are
    measured one character at a time and checked against the whole-string
    answer, so an assumption cannot go quietly wrong.

    Returns None if those two ever disagree, which no measured input does.
    """
    replaced = (composed.replace("İ", "i").replace("I", "ı") if turkish
                else composed)
    lowered = replaced.lower()

    if len(lowered) != len(replaced):
        # Some character lowercases to more than one. Rebuild the map by
        # measuring rather than by believing.
        widths = [len(ch.lower()) for ch in replaced]
        if sum(widths) != len(lowered):        # pragma: no cover
            return None
        wide_starts: list[int] = []
        wide_ends: list[int] = []
        for i, width in enumerate(widths):
            wide_starts.extend([starts[i]] * width)
            wide_ends.extend([ends[i]] * width)
        starts, ends = wide_starts, wide_ends

    if _COMBINING_DOT in lowered:
        keep = [i for i, ch in enumerate(lowered) if ch != _COMBINING_DOT]
        lowered = "".join(lowered[i] for i in keep)
        starts = [starts[i] for i in keep]
        ends = [ends[i] for i in keep]

    return lowered, starts, ends


def _folds_with_map(text: str) -> list[FoldMap]:
    """Each folded spelling of `text`, with a raw span per folded character.

    A match over `folded[i:j]` cuts `raw[starts[i]:ends[j - 1]]`.
    """
    composed, starts, ends = _map_to_composed(text)
    out: list[FoldMap] = []
    for turkish in (True, False):
        folded = _fold_mapped(composed, starts, ends, turkish)
        if folded is not None:
            out.append(folded)
    return out


def _cut(text: str, needles: tuple[str, ...]) -> str:
    """One pass: every match, in every spelling, removed from `text`."""
    spans: list[tuple[int, int]] = []
    for folded, starts, ends in _folds_with_map(text):
        for needle in needles:
            width = len(needle)
            if not width:
                # `find` returns a hit at every position for an empty needle,
                # and the last of them indexes one past the end of the map.
                # `needles()` filters these out; a caller that assembles its
                # own tuple would otherwise get an IndexError from inside the
                # send path.
                continue
            i = folded.find(needle)
            while i != -1:
                spans.append((starts[i], ends[i + width - 1]))
                i = folded.find(needle, i + 1)
    if not spans:
        return text

    # Merged rather than applied one at a time. The two folded spellings can
    # find the same letters at different offsets, and cutting the second span
    # out of a string the first one already shortened takes the wrong run.
    spans.sort()
    out: list[str] = []
    cursor = 0
    for start, end in spans:
        if start > cursor:
            out.append(text[cursor:start])
        cursor = max(cursor, end)
    out.append(text[cursor:])
    return "".join(out)


def _redact(text: str, needles: tuple[str, ...]) -> str:
    """The cut, with the matcher itself as the judge of whether it worked.

    The index map is exact for every script measured, and this loop is what
    makes that claim safe to rely on rather than something to hope about: the
    authoritative matcher gets the last word, and a string it still recognises
    is emptied rather than sent. A miss is not recoverable; an over-wide cut
    is.
    """
    for _ in range(_MAX_PASSES):
        shorter = _cut(text, needles)
        if shorter == text:
            break
        text = shorter
        if not _matches(text, needles):
            return text
    if _matches(text, needles):
        logger.warning(
            "Safeword survived the cut; the value was dropped instead.")
        return ""
    return text


# ── The walk ────────────────────────────────────────────────────────────────


def _walk(value: Any, needles: tuple[str, ...], parent: Any = None) -> Any:
    """Every string in `value`, cut. Returns `value` itself when nothing moved.

    Identity on a clean subtree is not an optimisation detail, it is what lets
    a caller compare a scrubbed body with the one it built and see that the
    filter did nothing.

    `parent` is the dict key this value arrived under, and it decides exactly
    one thing: a picture's address. Skipping the whole image part instead
    would take a caption or an alt text with it, and those are prose.

    Dict KEYS are never cut. They are field names the provider parses.
    """
    if isinstance(value, str):
        if _DATA_URI.match(value) or not _matches(value, needles):
            return value
        return _redact(value, needles)

    if isinstance(value, dict):
        changed = False
        out = {}
        for key, item in value.items():
            if (parent, key) == _picture_address():
                new = item
            else:
                new = _walk(item, needles, key)
            changed = changed or new is not item
            out[key] = new
        return out if changed else value

    if isinstance(value, (list, tuple)):
        items = [_walk(item, needles, parent) for item in value]
        if all(new is old for new, old in zip(items, value)):
            return value
        return items if isinstance(value, list) else tuple(items)

    # Numbers, booleans, None. Nothing to read and nothing to write.
    return value


def _scan(value: Any, needles: tuple[str, ...]) -> bool:
    """Is the word anywhere in here, without changing anything?"""
    if isinstance(value, str):
        return _matches(value, needles)
    if isinstance(value, dict):
        # Keys as well as values. The walk never CUTS a key, but a protocol
        # field that carries the word in one still has to be reportable.
        return any(_scan(key, needles) or _scan(item, needles)
                   for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_scan(item, needles) for item in value)
    return False


# ── What is left of a message once the cut has run ──────────────────────────


def _part_is_empty(part: Any) -> bool:
    """A content part carrying nothing a model could read."""
    if isinstance(part, dict) and part.get("type") == "text":
        return not str(part.get("text") or "").strip()
    # An image, or a shape this build does not know: not empty. Guessing
    # "empty" about an unrecognised part would drop it.
    return False


def _has_content(content: Any) -> bool:
    if content is None:
        return False
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, (list, tuple)):
        return any(not _part_is_empty(part) for part in content)
    return True


def _prune_messages(before: Any, after: Any) -> Any:
    """Drop what the cut emptied, and only what the cut emptied.

    Positional, comparing each message with the one it came from, because
    "content is blank" on its own is a different rule: a body that arrived
    with a blank turn in it must go out with that blank turn still there.
    Arming a safeword may not change a request that does not contain it.
    """
    if not isinstance(before, list) or not isinstance(after, list):
        return after
    if len(before) != len(after):
        return after

    kept = []
    for old, new in zip(before, after):
        if not isinstance(old, dict) or not isinstance(new, dict):
            kept.append(new)
            continue
        old_content = old.get("content")
        new_content = new.get("content")
        if (isinstance(old_content, list) and isinstance(new_content, list)
                and len(old_content) == len(new_content)):
            parts = [np for op, np in zip(old_content, new_content)
                     if not (_part_is_empty(np) and not _part_is_empty(op))]
            if len(parts) != len(new_content):
                new = dict(new, content=parts)
                new_content = parts
        if _has_content(old_content) and not _has_content(new_content):
            continue
        kept.append(new)
    return kept


def _prune_stop(before: Any, after: Any) -> tuple[bool, Any]:
    """An emptied stop sequence is dropped; an empty one is refused upstream.

    `validate_and_filter_gen_params` rejects an empty string in this list, so
    passing one on would be sending a value the app refuses to accept from its
    own user. Returns (keep, value); keep is False when nothing is left.
    """
    if isinstance(after, str):
        return bool(after), after
    if isinstance(after, list) and isinstance(before, list) \
            and len(before) == len(after):
        kept = [new for old, new in zip(before, after)
                if new != "" or old == ""]
        return bool(kept), kept
    return True, after


def scrub(payload: dict, spellings: tuple[str, ...]) -> dict:
    """The request body with the safeword taken out of everything a person wrote.

    PURE. `spellings` comes from `needles()` and is read by the caller, once,
    on the trip it already makes to the vault for the API key. Reading it in
    here instead cost a second SQLCipher connection on every provider request
    - measured at 1.8 ms of connection-open work, the same again as the key
    read - and it opened a window: the two reads sat a thread hop apart, and
    the idle watchdog could lock the vault in between, so a request that had
    a key raised `vault_locked` out of the middle of a body already
    assembled. Fused, either both are there or neither is.

    It also makes this function answerable. A filter that reaches into a
    database cannot be driven from a test without one, and eight files that
    drive the two provider functions have no database at all.

    Returns the SAME dict when the safeword is off or absent, so an ordinary
    request pays one folded pass over its own text and nothing else, and a
    caller can compare what it built with what it got and see that.

    Nothing in the body can make this raise.
    """
    if not spellings:
        return payload

    out: dict = {}
    changed = False
    matched_protocol: list[str] = []
    for key, value in payload.items():
        if key in PROTOCOL_FIELDS:
            if _scan(value, spellings):
                matched_protocol.append(key)
            out[key] = value
            continue
        new = _walk(value, spellings)
        changed = changed or new is not value
        out[key] = new

    if matched_protocol:
        # The FIELD NAMES, never the value and never the word. This is the one
        # place the filter deliberately declines to act, so it says so.
        logger.warning(
            "Safeword matched a protocol field and was left alone: %s",
            ", ".join(sorted(matched_protocol)))

    if not changed:
        return payload

    if "messages" in out:
        out["messages"] = _prune_messages(payload["messages"],
                                          out["messages"])
    if "stop" in out:
        keep, value = _prune_stop(payload.get("stop"), out["stop"])
        if keep:
            out["stop"] = value
        else:
            del out["stop"]
    return out
