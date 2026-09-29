"""Where each part of an imported SillyTavern preset goes in the request.

SillyTavern builds a chat completion request as a LIST of messages, not one
system message, and this module reproduces its placement rules:

RELATIVE parts (position 0, the default) are sent in list order, each as its
own message with its own role (system when the entry has none, or one that is
not system, user or assistant; the import report lists those). A part listed
before its preset's `chatHistory` placeholder goes before the chat; a part
listed after it goes after the chat, which is where Post-History Instructions
sit. With no chatHistory placeholder SillyTavern sends no chat at all; this
app still sends it, and the import report says so.

IN-CHAT parts (position 1) go INSIDE the chat at a depth: depth 0 is after the
newest message (the user's new message included), depth 1 before it, depth N
before the Nth message from the end, and a depth deeper than the chat lands
before the oldest message kept. At one depth, parts with the same order and
role are joined with "\\n" as they are, and only the joined text is trimmed; a
lower order comes earlier; and at one depth and order the roles come out
assistant, user, system (system closest to the end). An in-chat part with no
depth, a depth past MAX_INJECTION_DEPTH, or a role that is not one of the three
is never sent there, and never here either; the import report lists it.

PLACEHOLDERS ("markers") say where the card's fields go: description,
personality and scenario (through the preset's formats), the persona
description, and the example dialogue as SillyTavern splits it (one named
system message per line of speech, after the preset's example-chat line). A
field with no placeholder keeps the labelled card block.

The card's system prompt replaces the text of the preset's Main Prompt, and
its post-history text replaces the preset's Post-History Instructions, each
with `{{original}}` read as the preset's text once. When the preset has that
entry off, or forbids overrides, the card's text is not sent: that is what
SillyTavern does.

The chat starts with the preset's new-chat line, when it has one and a
chatHistory placeholder.

Consecutive system messages built here are joined with "\\n", the way
SillyTavern squashes them: never a chat message, a named message or the
new-chat lines.

A trailing assistant message is a prefill. A model that refuses prefill gets
it as a user message instead (model_refuses_prefill).

Nothing here reads the database or logs text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

ROLE_SYSTEM = "system"
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ROLES = (ROLE_SYSTEM, ROLE_USER, ROLE_ASSISTANT)
#: Chronological order of roles inside one depth-and-order group.
ROLE_ORDER_IN_GROUP = (ROLE_ASSISTANT, ROLE_USER, ROLE_SYSTEM)

POSITION_RELATIVE = 0
POSITION_IN_CHAT = 1
#: The deepest in-chat depth SillyTavern looks at; a deeper part is never sent.
MAX_INJECTION_DEPTH = 10000
#: SillyTavern's default for a part that names no order.
DEFAULT_ORDER = 100
#: The separator SillyTavern joins same-slot in-chat parts with, and squashes
#: system messages with.
IN_CHAT_JOIN = "\n"
SYSTEM_JOIN = "\n"
#: What JavaScript's trim() removes: its whitespace and line terminators.
JS_TRIM = ("\t\n\x0b\x0c\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006"
           "\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff")

MARKER_CHAT_HISTORY = "chatHistory"
#: Placeholder id -> the card field it holds.
CARD_MARKERS = {
    "charDescription": "description",
    "charPersonality": "personality",
    "scenario": "scenario",
    "dialogueExamples": "examples",
    "personaDescription": "persona",
}
#: The two entries a card can override.
ID_MAIN = "main"
ID_POST_HISTORY = "jailbreak"

#: The `name` SillyTavern gives example dialogue messages.
EXAMPLE_USER = "example_user"
EXAMPLE_ASSISTANT = "example_assistant"

SOURCE_MODULE = "module"
SOURCE_CARD = "card"
SOURCE_APP = "app"
SOURCE_HISTORY = "history"
SOURCE_USER = "user"


@dataclass
class Part:
    role: str
    text: str
    source: str = SOURCE_MODULE
    module_id: int | None = None
    #: A message `name` (example dialogue).
    name: str | None = None
    #: False for the lines SillyTavern never squashes (new chat, new example).
    merge: bool = True


@dataclass
class Injection:
    depth: int
    order: int
    role: str
    text: str
    module_id: int | None = None


@dataclass
class CardFields:
    """The card's fields as they go to their placeholders, macros done."""
    description: str = ""
    personality: str = ""
    scenario: str = ""
    persona: str = ""
    #: Per example block: its messages, the example-chat line first.
    examples: list[list[Part]] = field(default_factory=list)


@dataclass
class Layout:
    """The prompt around the chat. `head` starts with the salt line."""
    head: list[Part] = field(default_factory=list)
    post: list[Part] = field(default_factory=list)
    injections: list[Injection] = field(default_factory=list)
    #: The preset's new-chat line, sent first in the chat; None for none.
    new_chat: Part | None = None
    #: The persona went to its placeholder.
    persona_placed: bool = False
    #: Module ids that were not sent and why ("no_depth", "bad_role", ...).
    not_sent: dict[int, str] = field(default_factory=dict)
    #: The chat has macro variables (the budget refusal says how to clear them).
    has_variables: bool = False


def position_of(m: dict) -> int:
    p = m.get("st_injection_position")
    return POSITION_IN_CHAT if p == POSITION_IN_CHAT else POSITION_RELATIVE


def role_of(m: dict) -> str:
    r = m.get("st_role")
    return r if r in ROLES else ROLE_SYSTEM


def in_chat_problem(m: dict) -> str | None:
    """Why an in-chat part cannot be placed, or None when it can."""
    if position_of(m) != POSITION_IN_CHAT:
        return None
    depth = m.get("st_injection_depth")
    if not isinstance(depth, int) or isinstance(depth, bool) or depth < 0:
        return "no_depth"
    if depth > MAX_INJECTION_DEPTH:
        return "too_deep"
    if m.get("st_role") not in ROLES:
        return "bad_role"
    return None


def example_messages(blocks: list[str], *, user: str, char: str,
                     header: str) -> list[list[Part]]:
    """Example dialogue as SillyTavern sends it (parseExampleIntoIndividual).

    Each block is split on lines that start with "<user>:" or "<char>:"; the
    lines of one speaker become one system message named example_user or
    example_assistant, with the first "<name>:" removed and the text trimmed.
    Lines before the first speaker belong to the first speaker's message; a
    block with no speaker line sends nothing. Each block that sends anything
    starts with `header` (the preset's example-chat line) when it has one.
    """
    out: list[list[Part]] = []
    for block in blocks:
        msgs: list[Part] = []
        lines: list[str] = []
        in_user = in_bot = False

        def add(name: str, label: str) -> None:
            text = "\n".join(lines).replace(name + ":", "", 1).strip()
            msgs.append(Part(ROLE_SYSTEM, text, SOURCE_CARD, name=label))
            lines.clear()

        for line in block.split("\n"):
            if line.startswith(user + ":"):
                if in_bot:
                    add(char, EXAMPLE_ASSISTANT)
                in_user, in_bot = True, False
            elif line.startswith(char + ":"):
                if in_user:
                    add(user, EXAMPLE_USER)
                in_bot, in_user = True, False
            lines.append(line)
        if in_user:
            add(user, EXAMPLE_USER)
        elif in_bot:
            add(char, EXAMPLE_ASSISTANT)
        if msgs:
            head = [Part(ROLE_SYSTEM, header, SOURCE_CARD, merge=False)] if header else []
            out.append(head + msgs)
    return out


def build_layout(*, salt: str, entries: list[dict], fields: CardFields,
                 fallback_card, main_override=None, phi_override=None,
                 new_chat: str = "") -> Layout:
    """The layout for enabled parts and active placeholders, in list order.

    `entries` are dicts in stored order. A module entry has kind "module",
    its id, its `text` as sent (macros applied; an in-chat part untrimmed)
    and its st_* fields; a placeholder has kind "marker" and `st_identifier`.
    `fields` go to the placeholders; `fallback_card` (a prompt_layer.CardText)
    holds what goes to the labelled block for a field with no placeholder.
    `main_override(original)` and `phi_override(original)` return the card's
    text for that slot, or None when the card has none.
    """
    from prompt_layer import MODE_MODULES, CardText, labelled_card_block

    from .parsers import ST_SOURCES

    out = Layout(head=[Part(ROLE_SYSTEM, salt, SOURCE_APP)])
    # Each preset's own chatHistory placeholder decides which of ITS parts go
    # after the chat; another file's placeholder says nothing about them.
    hist_at: dict = {}
    for n, e in enumerate(entries):
        if (e["kind"] == "marker" and e.get("st_identifier") == MARKER_CHAT_HISTORY
                and e.get("import_id") not in hist_at):
            hist_at[e.get("import_id")] = n
    if hist_at and new_chat:
        out.new_chat = Part(ROLE_SYSTEM, new_chat, SOURCE_MODULE, merge=False)
    placed: set[str] = set()

    def slot(n: int, entry: dict) -> list[Part]:
        if entry["kind"] == "module" and entry.get("source_type") not in ST_SOURCES:
            # A plain file module goes where it always went: before the chat.
            return out.head
        at = hist_at.get(entry.get("import_id"))
        if at is not None:
            return out.post if n > at else out.head
        return out.post if entry.get("after_history") else out.head

    for n, e in enumerate(entries):
        if e["kind"] == "marker":
            fld = CARD_MARKERS.get(e.get("st_identifier") or "")
            if fld is None or fld in placed:
                continue
            placed.add(fld)
            target = slot(n, e)
            if fld == "examples":
                for block in fields.examples:
                    target.extend(block)
            elif getattr(fields, fld):
                target.append(Part(ROLE_SYSTEM, getattr(fields, fld), SOURCE_CARD))
            if fld == "persona":
                out.persona_placed = True
            continue

        problem = in_chat_problem(e)
        if problem is not None:
            out.not_sent[e["id"]] = problem
            continue
        text = e.get("text") or ""
        ident = e.get("st_identifier")
        override = {ID_MAIN: main_override, ID_POST_HISTORY: phi_override}.get(ident)
        if override is not None and not e.get("st_forbid_overrides"):
            card_text = override(text)
            if card_text is not None:
                text = card_text
        if position_of(e) == POSITION_IN_CHAT:
            if not text.strip(JS_TRIM):
                continue
            order = e.get("st_injection_order")
            out.injections.append(Injection(
                depth=e["st_injection_depth"],
                order=order if isinstance(order, int) and not isinstance(order, bool)
                else DEFAULT_ORDER,
                role=e["st_role"], text=text, module_id=e["id"]))
            continue
        if not text:
            continue
        slot(n, e).append(Part(role_of(e), text, SOURCE_MODULE, e["id"]))

    # What no placeholder took stays in the labelled block. The card's system
    # prompt and post-history text are never here: they go to the preset's
    # slots or, as in SillyTavern, nowhere.
    rest = CardText(
        name=fallback_card.name,
        has_name=fallback_card.has_name and not (placed - {"persona"}),
        description="" if "description" in placed else fallback_card.description,
        personality="" if "personality" in placed else fallback_card.personality,
        scenario="" if "scenario" in placed else fallback_card.scenario,
        examples=[] if "examples" in placed else fallback_card.examples,
    )
    block = labelled_card_block(rest, MODE_MODULES)
    if block:
        out.head.append(Part(ROLE_SYSTEM, block, SOURCE_CARD))
    return out


def injection_groups(injections: list[Injection]) -> list[tuple[int, list[Part]]]:
    """(depth, messages in chronological order) for each depth, shallowest
    first, with same depth + order + role joined as they are and the joined
    text trimmed."""
    by_depth: dict[int, list[Injection]] = {}
    for inj in injections:
        by_depth.setdefault(inj.depth, []).append(inj)
    out = []
    for depth in sorted(by_depth):
        orders: dict[int, list[Injection]] = {}
        for inj in by_depth[depth]:
            orders.setdefault(inj.order, []).append(inj)
        msgs: list[Part] = []
        for order in sorted(orders):
            for role in ROLE_ORDER_IN_GROUP:
                joined = IN_CHAT_JOIN.join(
                    i.text for i in orders[order] if i.role == role).strip(JS_TRIM)
                if joined:
                    msgs.append(Part(role, joined, SOURCE_MODULE))
        out.append((depth, msgs))
    return out


def place_in_chat(chat: list[Part], injections: list[Injection]) -> list[Part]:
    """`chat` (oldest first, the new user message last) with the in-chat
    parts inserted at their depths. Depth counts chat messages only."""
    result = list(chat)
    n = len(chat)
    # Deepest first, so an insert never shifts where a shallower one goes:
    # every index below is counted in chat messages from the end.
    for depth, msgs in sorted(injection_groups(injections), key=lambda g: -g[0]):
        at = max(0, n - depth)
        # Map "before the chat message at index `at`" into `result`.
        seen = 0
        idx = len(result)
        for k, p in enumerate(result):
            if p.source in (SOURCE_HISTORY, SOURCE_USER):
                if seen == at:
                    idx = k
                    break
                seen += 1
        result[idx:idx] = msgs
    return result


def _squashable(p: Part) -> bool:
    return (p.role == ROLE_SYSTEM and p.merge and p.name is None
            and p.source not in (SOURCE_HISTORY, SOURCE_USER))


def merge_system(parts: list[Part], sep: str = SYSTEM_JOIN) -> list[Part]:
    """Join consecutive system parts that may be squashed (see _squashable)."""
    out: list[Part] = []
    for p in parts:
        if out and _squashable(p) and _squashable(out[-1]):
            prev = out[-1]
            out[-1] = Part(ROLE_SYSTEM, prev.text + sep + p.text,
                           prev.source, prev.module_id)
        else:
            out.append(p)
    return out


# ---------------------------------------------------------------------------
# Prefill
# ---------------------------------------------------------------------------

#: Claude models that refuse a trailing assistant message, and the ones whose
#: thinking mode refuses it. Matched on the model's own name, with the dots of
#: a version written as hyphens ("claude-opus-4.6" is "claude-opus-4-6").
_CLAUDE_NO_PREFILL = re.compile(r"^claude-(opus-4-6|sonnet-4-6|opus-4-7|opus-4-8)")
_CLAUDE_5_OR_FABLE = re.compile(r"claude-fable|claude-(opus-5|sonnet-5)")
_CLAUDE_THINKING = re.compile(
    r"^claude-(3-7|opus-4|sonnet-4|haiku-4-5|opus-4-5|opus-4-6|sonnet-4-6|opus-4-7)")
#: Gemini models that refuse a trailing model turn, matched on the whole id.
_GEMINI_NO_PREFILL = re.compile(r"gemini-3\.[67]-flash|gemini-3\.5-flash-lite")


def model_refuses_prefill(model_id: str, *, thinking: bool = False) -> bool:
    """Whether a trailing assistant message would be refused: the Claude
    models listed above, any Claude model with thinking whose thinking is on,
    and the Gemini models listed above."""
    mid = (model_id or "").lower()
    if _GEMINI_NO_PREFILL.search(mid):
        return True
    name = mid.split(":", 1)[0].rsplit("/", 1)[-1].replace(".", "-")
    if _CLAUDE_NO_PREFILL.search(name) or _CLAUDE_5_OR_FABLE.search(name):
        return True
    return thinking and bool(_CLAUDE_THINKING.search(name))
