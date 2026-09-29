"""One SillyTavern-shaped prompt, from the stored rows to a placement.Layout.

Shared by the request path and the toggle-time budget check, so both measure
the same thing. Nothing here reads the database: the caller hands in the
rows, and decides whether the variables this returns are kept.

ORDER OF EVALUATION, as SillyTavern runs it:

1. The card's fields (description, personality, scenario, the persona
   description, the card's system prompt and post-history text, the example
   dialogue), each through the full macro engine, sharing one set of
   variables. `{{original}}` in the card's two override texts is kept for
   step 3.
2. Every enabled module that fires this turn, in stored order, same
   variables. A module that does not fire this turn is only counted for the
   budget check, on a copy.
3. The card's system prompt replaces the Main Prompt's text and its
   post-history text replaces the Post-History Instructions' text, the first
   `{{original}}` in each read as the text it replaces and any later one as
   nothing.
4. The preset's formats for personality and scenario, and its new-chat and
   example-chat lines.

Chat messages are never run through macros. Every text goes through the
sanitizer after its macros, as every other prompt text in the app does.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import card_macros
import prompt_layer
from prompt_sanitizer import sanitize_field, sanitize_name

from . import placement as P
from . import st_macros


logger = logging.getLogger(__name__)

_ORIGINAL = re.compile(r"\{\{[ \t]*original[ \t]*\}\}", re.IGNORECASE)
_PERSONALITY = re.compile(r"\{\{[ \t]*personality[ \t]*\}\}", re.IGNORECASE)
_SCENARIO = re.compile(r"\{\{[ \t]*scenario[ \t]*\}\}", re.IGNORECASE)
#: Private-use stand-ins that carry a macro past the engine untouched.
_HOLD_ORIGINAL = "\ue000"
_HOLD_PERSONALITY = "\ue001"
_HOLD_SCENARIO = "\ue002"
_HOLDS = (_HOLD_ORIGINAL, _HOLD_PERSONALITY, _HOLD_SCENARIO)


@dataclass
class Built:
    layout: P.Layout
    #: Every enabled module as the budget check counts it.
    modules: list[dict]
    #: The variables after this turn.
    variables: dict
    card_name: str


def _clean(raw: str | None) -> str:
    """The text with any stand-in character it happened to hold removed, so
    only the stand-ins put in here are read back."""
    raw = raw or ""
    for h in _HOLDS:
        raw = raw.replace(h, "")
    return raw


def _run(raw: str, variables: dict, char: str, user: str) -> str:
    """The full engine, then nothing else (the caller sanitizes). An engine
    failure keeps the card macros only; a field past its ceiling refuses."""
    try:
        return st_macros.evaluate(raw, variables, char=char, user=user)
    except card_macros.MacroOutputTooLarge:
        raise
    except Exception as exc:  # noqa: BLE001 - never fail the turn on a macro
        logger.warning("Macro evaluation failed: %s", type(exc).__name__)
        return card_macros.render_macros(raw, char=char, user=user)


def _field(raw, variables, char, user) -> str:
    return prompt_layer.strip_space(sanitize_field(
        _run(_clean(raw), variables, char, user)))


def _override(raw: str | None, variables: dict, char: str, user: str):
    """The card's replacement for a preset slot: a function of the text it
    replaces, or None when the card has none."""
    raw = _clean(raw).strip()
    if not raw:
        return None
    held = _run(_ORIGINAL.sub(_HOLD_ORIGINAL, raw), variables, char, user)

    def fill(original: str) -> str:
        first, _sep, rest = held.partition(_HOLD_ORIGINAL)
        text = first + (original if _sep else "") + rest.replace(_HOLD_ORIGINAL, "")
        return prompt_layer.strip_space(sanitize_field(text))
    return fill


def _formatted(fmt: str, value: str, other: dict[str, str], variables: dict,
               char: str, user: str) -> str:
    """SillyTavern's personality_format / scenario_format: only for a field
    that has text, with {{personality}} and {{scenario}} filled in."""
    if not value or not fmt:
        return value
    held = _PERSONALITY.sub(_HOLD_PERSONALITY, _clean(fmt))
    held = _SCENARIO.sub(_HOLD_SCENARIO, held)
    text = _run(held, variables, char, user)
    text = (text.replace(_HOLD_PERSONALITY, other["personality"])
            .replace(_HOLD_SCENARIO, other["scenario"]))
    return prompt_layer.strip_space(sanitize_field(text))


def build(*, salt: str, char_row, persona_row, user_name: str,
          modules_enabled: list[dict], modules_sent: list[dict],
          markers: list[dict], variables: dict | None,
          preset_texts: dict[str, str]) -> Built:
    """The layout. `char_row` has the card columns (routers.completions
    _CardRow); `persona_row` may be None."""
    vars_ = dict(variables or {})
    char = sanitize_name(char_row["name"] or "")
    has_name = bool(prompt_layer.strip_space(char_row["name"] or ""))

    # 1. The card, as SillyTavern reads its fields.
    description = _field(char_row["description"], vars_, char, user_name)
    personality = _field(char_row["personality"], vars_, char, user_name)
    scenario = _field(char_row["scenario"], vars_, char, user_name)
    persona = (_field(persona_row["description"], vars_, char, user_name)
               if persona_row is not None else "")
    main_override = _override(char_row["system_prompt"], vars_, char, user_name)
    phi_override = _override(char_row["post_history_instruction"], vars_, char,
                             user_name)
    blocks = [t for t in (_field(b, vars_, char, user_name) for b in
                          card_macros.split_examples(char_row["mes_example"] or ""))
              if t]

    # 2. The modules.
    sent_ids = {m["id"] for m in modules_sent}
    texts: dict[int, str] = {}
    for m in modules_enabled:
        if m["id"] in sent_ids:
            texts[m["id"]] = prompt_layer.st_module_text(
                m, vars_, char, user_name,
                strip=P.position_of(m) != P.POSITION_IN_CHAT)
    for m in modules_enabled:
        if m["id"] not in sent_ids:
            texts[m["id"]] = prompt_layer.st_module_text(
                m, dict(vars_), char, user_name)
    modules = [{"id": m["id"], "name": m["name"],
                "content": prompt_layer.strip_space(texts[m["id"]]),
                "sent": m["id"] in sent_ids}
               for m in modules_enabled if prompt_layer.strip_space(texts[m["id"]])]
    sent = [{**m, "kind": "module", "text": texts[m["id"]]}
            for m in modules_enabled if m["id"] in sent_ids]

    # 4. The preset's own texts.
    fields = {"personality": personality, "scenario": scenario}
    new_chat = _field(preset_texts.get("new_chat_prompt"), vars_, char, user_name)
    example_head = _field(preset_texts.get("new_example_chat_prompt"), vars_,
                          char, user_name)
    card_fields = P.CardFields(
        description=description,
        personality=_formatted(preset_texts.get("personality_format", ""),
                               personality, fields, vars_, char, user_name),
        scenario=_formatted(preset_texts.get("scenario_format", ""), scenario,
                            fields, vars_, char, user_name),
        persona=persona,
        examples=P.example_messages(blocks, user=user_name, char=char,
                                    header=example_head),
    )
    fallback = prompt_layer.CardText(
        name=char, has_name=has_name, description=description,
        personality=personality, scenario=scenario, examples=blocks)
    entries = sorted([*sent, *markers], key=lambda e: (e["sort_order"], e["id"]))
    layout = P.build_layout(salt=salt, entries=entries, fields=card_fields,
                            fallback_card=fallback, main_override=main_override,
                            phi_override=phi_override, new_chat=new_chat)
    layout.has_variables = bool(vars_)
    return Built(layout, modules, vars_, char)
