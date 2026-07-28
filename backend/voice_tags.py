"""voice_tags.py - delivery tags: one frozen vocabulary, both strippers.

The model writes `[whisper] come closer`. The person reads "come closer". The
engine hears HOW to say it. This module is the single source for all three
views, because they fail in different directions and only agreement keeps the
illusion intact:

  * The PROMPT (injected as a system message at call level, never shown, never
    stored on the character) teaches the model the vocabulary - and, more
    importantly, restraint. The bake-off finding was blunt: without explicit
    restraint rules the model tags every sentence and the voice becomes a
    caricature. Untagged is the desired default.
  * The STREAM stripper hides tags from the chat as deltas arrive. A tag can
    be split across two SSE chunks, so a naive regex flashes `[sedu` on screen
    for a frame; the fix is to withhold from any `[` until it either closes
    (drop the span) or proves it was never a tag (release it verbatim).
  * The STORAGE stripper cleans the same text at render time. It is literally
    the stream stripper run over the whole string, so the streamed text and
    the refreshed text can never disagree - a disagreement is a visible
    flicker seconds after `done`.

WHO GETS STRIPPED (the audit-2 correction - the first cut stripped everyone):
  * Only ASSISTANT text. User text is never model-tagged; eating a user's own
    "[sic]" would be silent display corruption, and the edit round-trip would
    then write the corruption back to the database.
  * Only once voice has EVER been enabled (`stripping_active()`). A user who
    never touched voice gets byte-identical pre-V4 behaviour - their assistant
    rows contain no tags, so there is nothing to protect them from and no
    false positive can reach them. The flag is sticky on purpose: rows written
    while voice was on still need hiding after it is turned off.

WHAT COUNTS AS A TAG (`_looks_like_tag`) - shape, not membership, because the
prompt explicitly invites invented tags ("[like sharing a secret]"):
  lowercase english words, commas/hyphens/apostrophes only, at most six words,
  3..40 chars, starting with a letter. Everything the audit demonstrated being
  eaten is outside that shape: `[1]` citations (digits), `[ ]`/`[x]` task
  boxes (too short), `[^1]` footnotes, `[YOUR NAME]` placeholders and
  `[Google]` reference links (uppercase), `arr[0]`/`x[i]` (digits). A span
  followed by `(` (markdown link) or `:` (reference-link definition) is never
  a tag, and a span inside backtick code is released untouched. The residue -
  a lowercase prose aside like "[sic]" in an ASSISTANT reply of a VOICE user -
  is documented and accepted: that is exactly the population whose model was
  instructed to write bracketed delivery, and "when unsure, show the text"
  still governs everything that fails the shape.

Raw text (tags included) is what gets STORED - stripping happens at the edges
(SSE deltas out, msg_to_dict out). No migration exists or is needed.

Engine dependence: only engines whose adapter declares inline_prosody_tags
(Fish S2) get the prompt and keep tags in their TTS text. For the others the
brackets would be READ ALOUD, so sanitize_for_tts strips everything.
"""
from __future__ import annotations

import logging
import re
import threading

logger = logging.getLogger(__name__)

# A real delivery tag is short. Anything past this is prose with a bracket.
MAX_TAG_CHARS = 40
# The prompt asks for 1-3; past this hard cap the extras are noise that
# distorts delivery. Words are always kept - only the excess tags go.
MAX_TAGS_PER_REPLY = 8

#: How many delivery tags a single reply may keep, as a user-facing dial.
#: The prompt asks the model for 1-3; the cap only ever removes EXCESS tags,
#: never words, so turning it down makes the delivery plainer and can never
#: make a reply shorter.
TAG_DENSITY_MIN = 0
TAG_DENSITY_MAX = 16

#: A standing delivery direction for the character, prepended to every spoken
#: reply. This is the lever for "make the voice deeper / slower / closer"
#: WITHOUT touching the reference clip: the reference gives timbre, tags give
#: performance. Empty means the model's own tagging is the only direction.
MAX_TONE_CHARS = 60

_TONE_UNSAFE = re.compile(r"[\[\]\r\n]+")
_WS = re.compile(r"\s+")

SETTING_NARRATIVE = "tts_narrative"
SETTING_TAG_DENSITY = "tts_tag_density"
#: Reading speed. App-level: it is honoured by every engine, either
#: through the engine's own rate knob or by time-stretching the audio.
SETTING_SPEED = "tts_speed"
SETTING_DEFAULT_TONE = "tts_default_tone"
#: Silence inserted BETWEEN sentences, in seconds. Purely a playback dial - it
#: changes nothing about the audio itself, so it carries no quality risk - but
#: it lives here with the other delivery preferences rather than in device
#: storage, because "how it sounds" is one list to the person adjusting it.
SETTING_SENTENCE_GAP = "tts_sentence_gap"
GAP_MIN = 0.0
GAP_MAX = 1.5
GAP_DEFAULT = 0.0
#: The user's own reading rules: {written form -> how to say it}. Stored as
#: JSON. speech_prep has applied these since it was written and nothing ever
#: supplied them, so somebody whose character is called "Aoife" heard it
#: mispronounced in every single reply with nowhere to correct it.
SETTING_PRONUNCIATIONS = "tts_pronunciations"
#: Bounds, because this rides on EVERY sentence of every reply: each entry is
#: a regex substitution over the text, so an unbounded table is an unbounded
#: cost on the synthesis path (and an unbounded settings row).
MAX_PRONUNCIATIONS = 200
MAX_PRONUNCIATION_CHARS = 80
SETTING_VOICE_ENABLED = "tts_voice_enabled"
# Sticky: set the first time voice is enabled, never cleared. Rows written
# while voice was on still need their tags hidden after it is turned off.
SETTING_VOICE_EVER = "tts_voice_ever_enabled"

# ── the injected block ───────────────────────────────────────────────────────
# Kept as ONE frozen string: the prompt, the vocabulary and the strippers must
# move together, and a vocabulary edit that forgets the prompt (or vice versa)
# should be impossible by construction.

PROMPT_EXAMPLE_LINES = (
    "[seductive] I missed you... [low voice] come here and tell me everything.",
    "[excited] Wait - you actually did that? I can't believe it!",
    "[sigh] It's been a long day. [soft] I'm glad you're here.",
    "[whisper] Don't say anything. [pause] Just stay.",
    "You're impossible. [chuckle] And that is exactly the problem.",
    "[cold, clipped tone] I asked you once. [pause] I will not ask again.",
)

VOICE_PROMPT = """## VOICE DELIVERY

Your reply is spoken aloud by a text-to-speech engine that reads inline delivery
directions written in square brackets. Use them to control HOW words are
performed. They are stripped before display - the reader never sees them and
they are never spoken literally.

### Syntax
- Place a tag immediately before the words it affects: [whisper] come closer
- It stays in effect until the next tag or the end of that sentence.
- Never nest brackets, never place a tag inside a word, never emit an empty [].
- Tags are always English, always lowercase, even when you speak another
  language.

### Situation -> tag map
Intimacy: [whisper] [low voice] [soft] [barely audible] [warm, close to the ear] [hushed] [gentle]
Seduction: [seductive] [sultry breathy whisper] [slow, intimate, seductive tone] [breathless] [husky] [teasing, playful tone]
Affection: [warm] [fond] [soft, affectionate tone] [smiling while speaking]
Joy: [laughing] [laughing tone] [chuckle] [giggling] [amused] [delight]
Excitement: [excited] [volume up] [breathless with excitement] [rushed]
Surprise: [surprised] [shocked] [gasp] [stunned] [disbelief]
Sadness: [sad] [sigh] [voice trembling] [on the verge of tears] [quiet, hurt] [resigned]
Anger: [angry] [shouting] [cold, clipped tone] [through gritted teeth] [icy calm]
Fear: [nervous] [shaky] [urgent] [panicked] [whisper, frightened]
Teasing: [teasing] [playful] [mock-serious] [sing-song] [smirking] [tsk]
Authority: [firm] [commanding] [measured, deliberate] [emphasis]
Fatigue: [tired] [sleepy] [yawning] [slurred] [hoarse] [weak]
Breath: [inhale] [exhale] [sigh] [gasp] [panting] [clearing throat] [sniff]
Dynamics: [volume up] [volume down] [loud] [low volume] [emphasis] [flat]
Pacing: [pause] [short pause] [slow] [fast] [hesitant] [interrupting]
Pitch: [pitch up] [pitch down] [deeper voice] [higher voice]
Special: [singing] [echo] [muffled] [talking to oneself]

The engine also understands descriptions it has never seen. If nothing above
fits, invent one: [like sharing a secret], [wry, unimpressed]. Keep it under
six words.

### How to use them well
1. 1-3 tags per reply. Tags are seasoning, not punctuation. An untagged
   sentence is already spoken in your natural voice - that is the desired
   default, and most replies need no tag at all.
2. Tag the moment the delivery CHANGES - into intimacy, a laugh, a flare of
   anger. Never open every message with a tag out of habit.
3. Only tag what is genuinely happening in the scene. A forced [seductive] on
   a neutral line sounds fake and cheapens the ones that matter.
4. Never double up. Either [seductive] or "she said seductively" - the tag
   replaces the stage direction, never both.
5. "..." slows delivery on its own; combine with soft tags for intimate
   beats: [low voice] stay... just a little longer.
6. Do not tag action or narration spans (*she leans closer*) - those are
   handled separately.
7. Escalate with specificity, not repetition: [sultry breathy whisper] beats
   writing [seductive] three times.

### Examples
""" + "\n".join(PROMPT_EXAMPLE_LINES) + "\n"

VOICE_PROMPT_CHARS = len(VOICE_PROMPT)


# ── what counts as a tag ─────────────────────────────────────────────────────

# Lowercase words, comma/hyphen/apostrophe/space, starting with a letter -
# the exact shape the prompt teaches and its examples use.
_TAG_SHAPE = re.compile(r"[a-z][a-z ,'\-]*")
# An in-progress tag at the very end of a partial (for trim_broken_tail).
_BROKEN_TAIL = re.compile(r"\[[a-z ,'\-]{0,%d}$" % (MAX_TAG_CHARS + 1))


def _looks_like_tag(inner: str) -> bool:
    """Shape judgement for a CLOSED bracket span's inner text.

    Deliberately strict in the safe direction: everything that fails is
    RELEASED verbatim, so a false negative shows a bracket the reader can see
    and shrug at, while a false positive silently deletes their text.
    """
    if len(inner) > MAX_TAG_CHARS or "\n" in inner or "[" in inner or "`" in inner:
        return False
    t = inner.strip()
    if len(t) < 3:                    # kills [x], [ ], [i], []
        return False
    if _TAG_SHAPE.fullmatch(t) is None:   # kills [1], [^1], [Google], [YOUR NAME]
        return False
    words = [w for w in re.split(r"[ ,\-]+", t) if w]
    return 0 < len(words) <= 6            # the prompt's own "under six words"


# ── the stripper (one algorithm, two doors) ──────────────────────────────────

class StreamStripper:
    """Removes tag spans from a stream without ever showing half a bracket.

    Withholds from an opening `[` until:
      * `]` arrives  -> the span is judged (tag: dropped / not-a-tag: released)
      * the span exceeds MAX_TAG_CHARS, or a newline arrives -> released, and
        the stripper PASSES THROUGH verbatim until that span's first `]` has
        gone by. That mirrors exactly what the whole-string door does (its
        span always extends to the first `]`), which is what keeps the two
        doors byte-identical - the module's core no-flicker invariant.
      * flush() at stream end -> released (an unclosed span was never a tag)

    Extra context the judgement needs, tracked across chunks:
      * backtick parity - a span inside inline/fenced code (`data[key]`) is
        code, not delivery, and is released untouched;
      * the last character emitted - the one-space swallow after a dropped tag
        only applies when the tag sat between spaces; "world.[pause] Come"
        must keep the space that follows.
    """

    def __init__(self) -> None:
        self._held = ""
        self._dropped = 0
        self._ticks = 0          # backticks seen so far in emitted text
        self._passthrough = False  # emitting verbatim until a "]" passes
        self._last = ""          # last character emitted to the reader

    def _emit(self, s: str, out: list[str]) -> None:
        if s:
            out.append(s)
            self._ticks += s.count("`")
            self._last = s[-1]

    def feed(self, delta: str) -> str:
        if not delta:
            return ""
        buf = self._held + delta
        self._held = ""
        out: list[str] = []
        while buf:
            if self._passthrough:
                j = buf.find("]")
                if j == -1:
                    self._emit(buf, out)
                    buf = ""
                    break
                self._emit(buf[: j + 1], out)
                buf = buf[j + 1:]
                self._passthrough = False
                continue

            i = buf.find("[")
            if i == -1:
                self._emit(buf, out)
                buf = ""
                break
            self._emit(buf[:i], out)
            span_and_rest = buf[i:]
            j = span_and_rest.find("]")

            if j == -1:
                span = span_and_rest
                if len(span) > MAX_TAG_CHARS + 2 or "\n" in span:
                    # Disqualified while still open: release, and stay verbatim
                    # until its eventual "]" so both doors resync at the same
                    # character.
                    self._emit(span, out)
                    self._passthrough = True
                    buf = ""
                else:
                    self._held = span      # possibly a tag still being typed
                    buf = ""
                break

            span = span_and_rest[: j + 1]
            inner = span[1:-1]
            rest = span_and_rest[j + 1:]

            if self._ticks % 2 == 1:
                # Inside inline/fenced code: brackets are code, always.
                self._emit(span, out)
                buf = rest
                continue
            if self._last.isalnum():
                # Glued to a word ("total[idx]"): the prompt's own rule is
                # "never place a tag inside a word" - a subscript, not a tag.
                self._emit(span, out)
                buf = rest
                continue
            if not _looks_like_tag(inner):
                self._emit(span, out)
                buf = rest
                continue
            if rest == "":
                # Closed at the chunk edge: the very next character decides
                # link/definition vs tag. Wait for it.
                self._held = span
                buf = ""
                break
            if rest.startswith(("(", ":")):
                # Markdown link "[text](url)" or reference definition
                # "[label]: url" - never eaten.
                self._emit(span, out)
                buf = rest
                continue

            # A genuine tag. Drop it, and swallow ONE following space only when
            # the tag sat between spaces (or opened the text) - so the seam is
            # single-spaced everywhere and "world.[pause] Come" keeps its gap.
            self._dropped += 1
            if rest.startswith(" ") and (self._last == "" or self._last.isspace()):
                buf = rest[1:]
            else:
                buf = rest
        return "".join(out)

    def flush(self) -> str:
        """Stream over: whatever is held was never completed into a judgeable
        tag - release it rather than eat visible text. Exception: a fully
        closed span held only for the link-lookahead is judged now (end of
        stream means no `(` or `:` can follow)."""
        held, self._held = self._held, ""
        if held.startswith("[") and held.endswith("]"):
            if (self._ticks % 2 == 0 and not self._last.isalnum()
                    and _looks_like_tag(held[1:-1])):
                self._dropped += 1
                return ""
        return held


def strip_tags(text: str) -> str:
    """The storage-side door to the SAME algorithm - fed whole, then flushed.
    Sharing the implementation is what guarantees the streamed text and the
    stored text can never disagree."""
    if not text or "[" not in text:
        return text or ""
    s = StreamStripper()
    return s.feed(text) + s.flush()


def trim_broken_tail(partial: str) -> str:
    """For the ABORT path: a partial that ends mid-tag holds text the stream
    deliberately never displayed. Persisting it verbatim makes the reloaded
    chat show a broken bracket the user never saw - so the in-progress tag is
    trimmed before the partial is stored. Only an unmistakable tag-in-progress
    is touched; anything else stays."""
    if not partial or not partial.endswith(tuple("[abcdefghijklmnopqrstuvwxyz ,'-")):
        return partial
    m = _BROKEN_TAIL.search(partial)
    if m is None:
        return partial
    return partial[: m.start()].rstrip(" ")


# ── TTS-side sanitising ──────────────────────────────────────────────────────

class TagBudget:
    """A tag allowance that belongs to the REPLY, not to the call.

    sanitize_for_tts's cap and its consecutive-duplicate check used to be
    call-local, and the two paths call it differently: the replay path once
    over the whole message, the live path once per sentence as the queue hands
    them over. So "at most 3 tags" meant three for the WHOLE reply when the
    Speak button read it and three PER SENTENCE while the same reply was
    arriving - one setting, one message, six tags against three, and the user
    with no way to tell which number the dial meant.

    Threading one of these through every call of an utterance is what makes
    the number mean the same thing on both paths. Not thread-safe and does not
    need to be: one utterance is synthesised by one worker, in order.
    """

    __slots__ = ("remaining", "last_tag")

    def __init__(self, cap: int) -> None:
        self.remaining = max(0, int(cap))
        #: The previous KEPT tag, so a run of identical directions collapses
        #: across a sentence boundary too - "[warm] ... [warm] ..." reads as
        #: one continuing instruction, not two.
        self.last_tag: str | None = None


def sanitize_for_tts(text: str, *, engine_supports_tags: bool,
                     max_tags: int | None = None,
                     budget: TagBudget | None = None,
                     free_tags: tuple[str, ...] = ()) -> str:
    """What the ENGINE gets to read.

    An engine without inline-tag support would speak the brackets out loud
    ("open bracket whisper close bracket"), so it gets fully stripped text.
    A tag-capable engine keeps well-formed tags, but malformed spans are
    dropped rather than risked: a 200-char bracketed aside read aloud verbatim
    is far worse than losing a direction. Consecutive duplicates collapse and
    a hard cap stops a tag flood from distorting the whole delivery.

    budget: share one across every sentence of a reply so the cap is a
    per-reply allowance. Omitted, the call gets its own - which is right for
    a caller that really is handling the whole message at once.

    free_tags: tags THIS APP injected, kept without spending the allowance.
    MEASURED BUG: in "narrator" narration mode speech_prep puts a narrator tag
    in front of every `*...*` span and a closing one after it, and this
    function - which runs afterwards - could not tell either from a tag the
    model chose. On a roleplay reply the narration ate the budget and the
    dialogue further down went out plain: 4 of 7 delivery tags survived
    instead of 7, and the ones lost were all at the end. The dial means "how
    enthusiastic may the MODEL be" (see MAX_TAGS_PER_REPLY); a rendering
    decision the user made in settings is not the model being enthusiastic.
    They still take part in the duplicate collapse, so consecutive narration
    reads as one continuing instruction.

    NOT CAPPED, and that asymmetry is deliberate rather than an oversight. The
    dial exists because a model left to itself tags every sentence and the
    voice becomes a caricature - it is a limit on ENTHUSIASM, and enthusiasm is
    not what produces these. An app tag is emitted once per `*...*` span, so
    the count is a fact about how the reply is written, not a number anybody
    chose; a reply that alternates narration and speech thirty times needs
    thirty direction changes to be read correctly. Capping them would silently
    stop switching voices partway down exactly the replies that need it most,
    which is the failure this exemption was added to fix.
    """
    if not text:
        return ""
    if not engine_supports_tags:
        return strip_tags(text)
    cap = MAX_TAGS_PER_REPLY if max_tags is None else max(0, int(max_tags))
    state = budget if budget is not None else TagBudget(cap)

    out: list[str] = []
    buf = text
    while buf:
        i = buf.find("[")
        if i == -1:
            out.append(buf)
            break
        out.append(buf[:i])
        rest = buf[i:]
        j = rest.find("]")
        if j == -1:
            out.append(rest)                     # unclosed: prose, keep
            break
        span, after = rest[: j + 1], rest[j + 1:]
        inner = span[1:-1].strip()
        malformed = not inner or not _looks_like_tag(span[1:-1])
        is_link = after.startswith(("(", ":"))
        if is_link:
            out.append(span)
            buf = after
            continue
        free = inner in free_tags
        if malformed or inner == state.last_tag or (
                not free and state.remaining <= 0):
            # Drop the span but keep the words around it; swallow one
            # following space so the seam does not double.
            buf = after[1:] if after.startswith(" ") and (
                not out or not out[-1] or out[-1][-1].isspace()) else after
            continue
        out.append(span)
        if not free:
            state.remaining -= 1
        state.last_tag = inner
        buf = after
    result = "".join(out)
    while "  " in result:
        result = result.replace("  ", " ")
    return result.strip() if result.strip() else result


# ── who gets stripped ────────────────────────────────────────────────────────

_EVER_LOCK = threading.Lock()
_EVER_CACHE: bool | None = None     # None = not read yet; True is sticky


def stripping_active() -> bool:
    """Has voice EVER been enabled in this vault?

    False means this installation has never produced a tagged row, so no
    stripping happens anywhere and pre-V4 behaviour is byte-identical. The
    answer is cached: True is sticky by definition, and False is only cached
    after a successful read (a locked vault reads as False but is NOT cached,
    so unlocking gets a fresh answer). `mark_voice_ever_enabled` flips the
    cache the moment the toggle turns on.
    """
    global _EVER_CACHE
    with _EVER_LOCK:
        if _EVER_CACHE is not None:
            return _EVER_CACHE
    try:
        from database import get_setting

        ever = (get_setting(SETTING_VOICE_EVER) or "").strip() in ("1", "true")
        if not ever:
            # Tolerate a vault where the toggle predates the sticky flag.
            ever = (get_setting(SETTING_VOICE_ENABLED) or "").strip() in ("1", "true")
        with _EVER_LOCK:
            _EVER_CACHE = ever
        return ever
    except Exception:                            # noqa: BLE001
        # Written for the locked-vault case, where False is correct and quiet
        # is correct. But a bare `return False` swallows EVERY other failure
        # with it, and the visible symptom of guessing wrong here is raw
        # `[whisper]` tags in every bubble - which reads as a bug in the model,
        # not in a settings read. Debug level: on the locked path this fires
        # constantly and is not news.
        logger.debug("voice_tags: could not read the stripping setting",
                     exc_info=True)
        return False                             # locked/unreadable: not cached


def mark_voice_ever_enabled() -> None:
    """Called by the voice-mode endpoint the moment the toggle turns on."""
    global _EVER_CACHE
    with _EVER_LOCK:
        _EVER_CACHE = True


def reset_stripping_cache() -> None:
    """Test seam only."""
    global _EVER_CACHE
    with _EVER_LOCK:
        _EVER_CACHE = None


def strip_for_display(content: str, role: str) -> str:
    """The one rule for what leaves the API: assistant text, and only once
    voice has ever been on. User text is NEVER stripped - eating a user's own
    "[sic]" is display corruption, and the edit round-trip would write it back."""
    if role != "assistant" or not content or "[" not in content:
        return content
    if not stripping_active():
        return content
    return strip_tags(content)


# ── conditional injection ────────────────────────────────────────────────────

_TAG_SUPPORT_LOCK = threading.Lock()
_TAG_SUPPORT_CACHE: dict = {"uid": None, "val": False}


def _active_engine_supports_tags() -> bool:
    """Does the SELECTED voice model's engine understand inline tags?

    The uid is re-read every call (the user can change selection between two
    messages), but the uid -> capability answer is cached: it requires a
    filesystem scan, and an engine's tag capability cannot change while the
    same model stays selected.
    """
    from database import get_setting
    from tts import scan_roots
    from tts.registry import adapter_for

    uid = get_setting("tts_active_uid")
    if not uid:
        return False
    with _TAG_SUPPORT_LOCK:
        if _TAG_SUPPORT_CACHE["uid"] == uid:
            return _TAG_SUPPORT_CACHE["val"]
    model = next((m for m in scan_roots().models if m.uid == uid), None)
    val = False
    if model is not None:
        adapter = adapter_for(model.engine_id)
        val = bool(adapter and adapter.capabilities.inline_prosody_tags)
    with _TAG_SUPPORT_LOCK:
        _TAG_SUPPORT_CACHE.update(uid=uid, val=val)
    return val


def reset_tag_support_cache() -> None:
    """Test seam, and called on rescan so a re-identified model re-answers."""
    with _TAG_SUPPORT_LOCK:
        _TAG_SUPPORT_CACHE.update(uid=None, val=False)


def voice_block() -> str:
    """The system block to inject, or "" - and NEVER an exception.

    Empty when the toggle is off, when no model is selected, when the selected
    engine cannot use tags (they would be read aloud), and when anything in
    the lookup breaks: voice must never be the reason a chat request fails.

    NOTE for callers on the event loop: the cache-miss path walks the models
    folder. completions runs this via anyio.to_thread.run_sync so a cold cache
    cannot stall every in-flight request.
    """
    try:
        from database import get_setting

        if (get_setting(SETTING_VOICE_ENABLED) or "").strip() not in ("1", "true"):
            return ""
        return VOICE_PROMPT if _active_engine_supports_tags() else ""
    except Exception:                           # noqa: BLE001
        logger.warning("voice_tags: block lookup failed; injecting nothing",
                       exc_info=True)
        return ""


# ── the standing delivery direction ─────────────────────────────────────────

def sanitize_pronunciations(table: object) -> dict[str, str]:
    """Reduce a submitted reading table to something safe to substitute with.

    Every entry becomes a regex substitution run over every sentence of every
    reply, so this is a hot path AND a text-rewriting one. Rules:
      - both sides are plain text; brackets and newlines go, exactly as in
        sanitize_tone (a bracket in a replacement would open a delivery span
        and the rest of the sentence would be read as a direction);
      - an empty written form is dropped - it would match everywhere;
      - an empty replacement is KEPT, because "say nothing for this" is a
        legitimate rule (a decorative symbol in a character name);
      - length and count are capped rather than rejected: a table one entry
        over the limit should lose that entry, not the user's whole dictionary.
    """
    if not isinstance(table, dict):
        return {}
    out: dict[str, str] = {}
    for raw_src, raw_dst in table.items():
        if len(out) >= MAX_PRONUNCIATIONS:
            break
        src = _WS.sub(" ", _TONE_UNSAFE.sub(" ", str(raw_src or ""))).strip()
        dst = _WS.sub(" ", _TONE_UNSAFE.sub(" ", str(raw_dst or ""))).strip()
        if not src:
            continue
        out[src[:MAX_PRONUNCIATION_CHARS]] = dst[:MAX_PRONUNCIATION_CHARS]
    return out


def usable_as_tag(inner: str) -> bool:
    """Would this survive `sanitize_for_tts` as a delivery direction?

    Public because a CALLER that injects a tag has to know: a span failing the
    shape rule is dropped as malformed, silently, and the words around it are
    performed under whatever direction was standing before. The standing tone
    is the case that made this necessary - `sanitize_tone` allows 60
    characters, `_looks_like_tag` allows 40 and six words, so a long tone is
    a perfectly good SETTING and an unusable TAG.
    """
    return _looks_like_tag(inner)


def sanitize_tone(text: str) -> str:
    """Reduce a typed tone to something safe to put in brackets.

    Brackets and newlines are the whole attack surface: a tone carrying a
    closing bracket would end the span early and the remainder would be READ
    ALOUD - the exact failure `sanitize_for_tts` exists to prevent. Rejecting
    would be unhelpful for a typo, so it is cleaned instead.
    """
    cleaned = _TONE_UNSAFE.sub(" ", str(text or ""))
    cleaned = _WS.sub(" ", cleaned).strip(" ,;")
    return cleaned[:MAX_TONE_CHARS].strip()


def apply_default_tone(text: str, tone: str, *, engine_supports_tags: bool) -> str:
    """Put the standing direction in front of a reply.

    Only for engines that read inline directions - anywhere else the brackets
    would be spoken aloud. Skipped when the reply already opens with a tag: the
    model's own choice for THIS line is more specific than a standing default,
    and stacking two directions on one clause muddies both.
    """
    if not engine_supports_tags:
        return text
    clean = sanitize_tone(tone)
    if not clean:
        return text
    if text.lstrip().startswith("["):
        return text
    return "[" + clean + "] " + text.lstrip()
