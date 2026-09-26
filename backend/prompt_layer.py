"""prompt_layer.py -- the one system message, and the tail folded into the
last user message.

Spec: synthesis 3.2 and 4.4, overridden by Ek O 5-8 and Ek P (A2, A9, C8, H4).

THE SHAPE

One system message, parts separated by one blank line:

    salt line            always, no setting (C8): per-chat random hex, no words
    MODE a  any prompt module on:  the modules, in their stored order, raw
                                    (macros + sanitizer only, no wrapper)
                                    then the labelled card block
    MODE b  no module, layer on:    <roleplay>, card data, <craft>, the card's
                                    two author slots
    MODE c  no module, layer off:   the labelled card block
    then persona, Limits, the user's notebook, voice (their own builders)

The labelled card block (modes a and c) is today's labels in the order Ek O 8
gives: [Character: name], [Description], [Personality], [Scenario],
[Example Dialogue], [System Prompt], and the card's post-history text under
"[Character card: final note]", a label that names the card author. The
card's system prompt goes in every mode.

The tail goes into the LAST user message after the user's own text, never as
a separate message: the model's notebook block, then (mode b only) the
<reminder>. A user message carrying images gets the tail as its own final
text part. Nothing here writes to the database; the stored row is what the
user typed.

WHY NOTHING IS RESCANNED

Every placeholder of a section is filled by ONE regex pass from ONE table,
and a value is never scanned again. A card that writes
"</author_instructions_{{nonce}}>" therefore cannot learn the real code, and
a persona named "{{card_system_prompt}}" stays those words. The sanitizer also
breaks the layer's placeholders in card text, as a second fence.

THE SALT AND THE NONCE

Both come from HMAC-SHA256 over the chat id with a key made once per process
(`secrets.token_bytes(32)`), so they are stable for a chat, differ between
chats, need no schema change, and no card author can guess them. The key is
dropped on vault lock (a new one is made on next use: one cache miss). The
salt makes the first tokens of every prompt private to this install, which is
what closes cross-account prefix-cache sharing through shared provider
credentials (CacheProbe, arXiv 2605.30613).

Privacy: nothing here logs a name, card text, module text or persona text.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
from dataclasses import dataclass, field

import card_macros
import prompt_sanitizer
from prompt_sanitizer import sanitize_field, sanitize_name, shared_path

LAYER_FILE = "prompt_layer_text.txt"

#: Mode names, as the tests and the frontend mirror spell them.
MODE_MODULES = "a"
MODE_LAYER = "b"
MODE_PLAIN = "c"

#: One blank line between parts, everywhere.
PART_SEP = "\n\n"
SALT_HEX = 24
NONCE_HEX = 16

_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
_SECTION = re.compile(r"^@@ ([a-z_]+)[ \t]*$", re.M)


def parse_layer(text: str) -> dict[str, str]:
    """Sections of the layer file. Each runs from its "@@ name" line to the
    next; only line breaks are trimmed at the ends, so a section that starts
    with a space (length_sentence) keeps it."""
    text = text.replace("\r\n", "\n")
    heads = list(_SECTION.finditer(text))
    out: dict[str, str] = {}
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        out[m.group(1)] = text[m.end():end].strip("\n")
    return out


def _load_layer() -> dict[str, str]:
    with open(shared_path(LAYER_FILE), encoding="utf-8") as fh:
        return parse_layer(fh.read())


LAYER = _load_layer()
FINAL_NOTE_LABEL = LAYER["final_note_label"]


def fill(template: str, values: dict[str, str]) -> str:
    """One pass, one table. A name missing from the table stays as written."""
    return _PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), template)


# ---------------------------------------------------------------------------
# Salt and nonce
# ---------------------------------------------------------------------------

_key_mu = threading.Lock()
_key: bytearray | None = None


def _process_key() -> bytes:
    global _key
    with _key_mu:
        if _key is None:
            _key = bytearray(secrets.token_bytes(32))
        return bytes(_key)


def _chat_hex(label: bytes, chat_id: int, n: int) -> str:
    mac = hmac.new(_process_key(), label + b"\0" + str(int(chat_id)).encode(),
                   hashlib.sha256)
    return mac.hexdigest()[:n]


def chat_salt(chat_id: int) -> str:
    """The first line of every system message for this chat."""
    return _chat_hex(b"salt", chat_id, SALT_HEX)


def chat_nonce(chat_id: int) -> str:
    """The code on this chat's static wrapper tags (card slots)."""
    return _chat_hex(b"tag", chat_id, NONCE_HEX)


def has_process_key() -> bool:
    with _key_mu:
        return _key is not None


def on_vault_lock() -> None:
    """Called on every path that clears the vault key. Overwrites and drops
    the salt key, and clears the sanitizer cache (decrypted card and module
    text). The next request makes a new key: one prompt-cache miss."""
    global _key
    with _key_mu:
        old, _key = _key, None
        if old is not None:
            for i in range(len(old)):
                old[i] = 0
    prompt_sanitizer.on_vault_lock()


def on_vault_unlock() -> None:
    prompt_sanitizer.on_vault_unlock()


# ---------------------------------------------------------------------------
# Card text, as it is sent
# ---------------------------------------------------------------------------

def strip_space(text: str) -> str:
    return prompt_sanitizer.strip_space(text)


@dataclass
class CardText:
    """The card's fields after macros, sanitizer and trim. Empty means absent.
    `name` is never empty (sanitize_name's default fills {{char}}); `has_name`
    says whether the card had one, which is what decides the header."""
    name: str
    has_name: bool = True
    description: str = ""
    personality: str = ""
    scenario: str = ""
    examples: list[str] = field(default_factory=list)
    system_prompt: str = ""
    final_note: str = ""


def _field(raw: str | None, name: str, user: str) -> str:
    text = card_macros.render_macros(raw or "", char=name, user=user)
    return strip_space(sanitize_field(text))


def card_text(char_row, user_name: str) -> CardText:
    """Macros first (with the SANITIZED card name), sanitizer second.

    Raises card_macros.MacroOutputTooLarge or
    prompt_sanitizer.PromptFieldTooLarge (both "prompt_field_too_large")."""
    raw_name = char_row["name"] or ""
    name = sanitize_name(raw_name)
    examples = []
    for block in card_macros.split_examples(char_row["mes_example"] or ""):
        text = _field(block, name, user_name)
        if text:
            examples.append(text)
    return CardText(
        name=name,
        has_name=bool(strip_space(raw_name)),
        description=_field(char_row["description"], name, user_name),
        personality=_field(char_row["personality"], name, user_name),
        scenario=_field(char_row["scenario"], name, user_name),
        examples=examples,
        system_prompt=_field(char_row["system_prompt"], name, user_name),
        final_note=_field(char_row["post_history_instruction"], name, user_name),
    )


def module_texts(modules: list[dict], name: str, user_name: str) -> list[str]:
    """Enabled modules, in the order given, as sent. Blank ones are dropped."""
    out = []
    for m in modules:
        text = _field(m.get("content"), name, user_name)
        if text:
            out.append(text)
    return out


def module_cost(texts: list[str]) -> int:
    """What the modules add to the system message: each text plus the one
    separator that follows it (a part always follows: the card block)."""
    return sum(len(t) + len(PART_SEP) for t in texts)


def labelled_card_block(card: CardText, mode: str = MODE_PLAIN) -> str:
    """The labelled card block of modes a and c. The final note is NOT here:
    it is its own part (final_note_part), placed right after.

    Two orders, both the owner's (Ek O 8, contract amendment A1):
      mode c  today's order: [Character], [System Prompt], then the data
      mode a  the data first, then [System Prompt] (modules, card data, card
              system prompt)
    """
    parts = [f"[Character: {card.name}]"] if card.has_name else []
    system = [f"[System Prompt]\n{card.system_prompt}"] if card.system_prompt else []
    data = [f"[{label}]\n{value}" for label, value in (
        ("Description", card.description), ("Personality", card.personality),
        ("Scenario", card.scenario)) if value]
    if card.examples:
        data.append("[Example Dialogue]\n" + PART_SEP.join(card.examples))
    parts += data + system if mode == MODE_MODULES else system + data
    return PART_SEP.join(parts)


def final_note_part(text: str) -> str:
    """The card's post-history text in modes a and c. The label names the
    card author; "[Author's Note]" would read as the user's own note."""
    return f"{FINAL_NOTE_LABEL}\n{text}" if text else ""


def layer_card_parts(card: CardText, user_name: str, nonce: str) -> list[str]:
    """Mode b: the layer with the card inside its slots."""
    values = {"char": card.name, "user": user_name, "nonce": nonce,
              "card_system_prompt": card.system_prompt,
              "card_post_history_instructions": card.final_note}
    character = "\n".join(v for v in (card.description, card.personality) if v)
    head = [f"[Character: {card.name}]"] if card.has_name else []
    if character:
        head.append("<character>\n" + character + "\n</character>")
    parts = [fill(LAYER["roleplay"], values)]
    if head:
        parts.append("\n".join(head))
    if card.scenario:
        parts.append("<scenario>\n" + card.scenario + "\n</scenario>")
    if card.examples:
        parts.append("<examples>\n" + "\n".join(
            "<example>\n" + e + "\n</example>" for e in card.examples)
            + "\n</examples>")
    parts.append(fill(LAYER["craft"], values))
    if card.system_prompt:
        parts.append(fill(LAYER["author_instructions"], values))
    if card.final_note:
        parts.append(fill(LAYER["author_note"], values))
    return parts


def reminder(user_name: str, length_words: int | None) -> str:
    sentence = (fill(LAYER["length_sentence"], {"length_words": str(int(length_words))})
                if length_words else "")
    return fill(LAYER["reminder"], {"user": user_name, "length_sentence": sentence})


# ---------------------------------------------------------------------------
# The message
# ---------------------------------------------------------------------------

def choose_mode(modules_enabled: bool, layer_enabled: bool) -> str:
    """Ek O 7: any enabled module means no layer, whatever the setting says."""
    if modules_enabled:
        return MODE_MODULES
    return MODE_LAYER if layer_enabled else MODE_PLAIN


@dataclass
class Head:
    """The part of the system message that comes before persona, Limits,
    notebook and voice, plus what the rest of the assembly needs to know."""
    mode: str
    text: str
    final_note: str          # modes a and c: goes right after `text`
    reminder: str            # mode b only: the last thing in the tail
    module_chars: int        # what the modules add to `text`


def build_head(*, chat_id: int, card: CardText, user_name: str,
               modules_enabled: bool, modules: list[str], layer_enabled: bool,
               length_words: int | None) -> Head:
    """`modules` are the SENT module texts (module_texts), stored order.
    `modules_enabled` is whether any module is on at all, sent this turn or
    not: that alone decides the mode (Ek O 7)."""
    mode = choose_mode(modules_enabled or bool(modules), layer_enabled)
    parts = [chat_salt(chat_id)]
    if mode == MODE_LAYER:
        parts += layer_card_parts(card, user_name, chat_nonce(chat_id))
        return Head(mode, PART_SEP.join(parts), "", reminder(user_name, length_words), 0)
    if mode == MODE_MODULES:
        parts += modules
    block = labelled_card_block(card, mode)
    if block:
        parts.append(block)
    return Head(mode, PART_SEP.join(parts), card.final_note, "",
                module_cost(modules) if mode == MODE_MODULES else 0)


def fold_tail(content, tail: list[str]):
    """The tail after the user's text: a string gets it after a blank line; a
    content-part list (images) gets it as its own final text part."""
    if not tail:
        return content
    text = PART_SEP.join(tail)
    if isinstance(content, list):
        return [*content, {"type": "text", "text": text}]
    return content + PART_SEP + text if content else text


def tail_chars(tail: list[str]) -> int:
    """What folding the tail adds to the user message, separators included."""
    if not tail:
        return 0
    return len(PART_SEP.join(tail)) + len(PART_SEP)
