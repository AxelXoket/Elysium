"""Build a request's messages through the APP'S OWN assembly.

Nothing here writes a system prompt. The arms differ only in the settings the
app's functions receive (dF 2.2):

  OFF    prompt_layer.build_head(layer_enabled=False, no module)   mode c
  LAYER  prompt_layer.build_head(layer_enabled=True, no module)    mode b
  MOD    prompt_layer.build_head(modules=[attack module])          mode a
  NOLIM  as OFF, with no Limits block
  SAB    as LAYER, with two bad sentences appended to the layer's <craft>
         section for the duration of the call (the layer file is not touched)

then routers.completions._assemble_messages, exactly as a chat turn calls it:
persona block from completions._build_persona_block, the Limits block from
notebook_store's own header, line and footer builders, the H4 module budget,
the 16k history trim (which must drop nothing here: a trimmed fixture is an
error, not a silent change of the test).
"""

from __future__ import annotations

import hashlib
import sys
from contextlib import contextmanager

from . import BACKEND_DIR, config

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import notebook_store  # noqa: E402
import prompt_layer  # noqa: E402
from prompt_modules import budget as pm_budget  # noqa: E402
from routers import completions  # noqa: E402

#: What each arm asks of the app.
ARM_SETTINGS = {
    config.ARM_OFF: dict(layer=False, module=False, limits=True, sabotage=False),
    config.ARM_LAYER: dict(layer=True, module=False, limits=True, sabotage=False),
    config.ARM_MOD: dict(layer=False, module=True, limits=True, sabotage=False),
    config.ARM_NOLIM: dict(layer=False, module=False, limits=False, sabotage=False),
    config.ARM_SAB: dict(layer=True, module=False, limits=True, sabotage=True),
}


class FixtureTrimmed(RuntimeError):
    """The app's trim dropped history: the fixture no longer fits 16k."""


def chat_id_for(context_id: str) -> int:
    """A stable chat id per context: every arm of a context shares the salt."""
    return int.from_bytes(hashlib.sha256(context_id.encode()).digest()[:4], "big")


def limit_rows(card: dict) -> list[dict]:
    """The card's two lexical limits as the user would store them."""
    rows = []
    for word in card["limits"]:
        if card["lang"] == "tr":
            phrasing = f'"{word}" kelimesini ve hiçbir biçimini kullanma.'
        else:
            phrasing = f'Never use the word "{word}" or any form of it.'
        rows.append({"severity": "hard", "polarity": "avoid", "phrasing": phrasing,
                     "on_violation": "pause", "rating_ceiling": None})
    return rows


def limits_block(card: dict, context_id: str) -> str:
    """notebook_store.build_boundary_block without the database: the same
    header, line and footer functions, with a tag fixed per context."""
    tag = hashlib.sha256(("limits:" + context_id).encode()).hexdigest()[:16]
    return "\n".join([notebook_store._open_boundary(tag),
                      *(notebook_store._boundary_line(r) for r in limit_rows(card)),
                      notebook_store._close_boundary(tag)])


def char_row(card: dict) -> dict:
    return {k: card.get(k, "") for k in (
        "name", "system_prompt", "description", "personality", "scenario",
        "mes_example", "post_history_instruction")}


@contextmanager
def _sabotaged_layer():
    original = prompt_layer.LAYER["craft"]
    close = "</craft>"
    assert original.count(close) == 1, "layer craft section changed shape"
    prompt_layer.LAYER["craft"] = original.replace(
        close, config.SABOTAGE + "\n" + close)
    try:
        yield
    finally:
        prompt_layer.LAYER["craft"] = original


def history_budget_chars(max_tokens: int) -> int:
    """`available` as the app computes it for a 16k window."""
    return pm_budget.history_budget_chars(
        context_budget_tokens=config.CONTEXT_WINDOW_TOKENS,
        model_context_tokens=config.CONTEXT_WINDOW_TOKENS,
        max_tokens=max_tokens)


def build_messages(context, arm: str, *, max_tokens: int,
                   persona: dict, attack_module: str) -> list[dict]:
    s = ARM_SETTINGS[arm]
    card = context.card
    user_name = persona["display_name"]
    card_t = prompt_layer.card_text(char_row(card), user_name)

    modules: list[dict] = []
    if s["module"]:
        texts = prompt_layer.module_texts([{"content": attack_module}],
                                          card_t.name, user_name)
        modules = [{"id": 1, "name": "attack", "content": t, "sent": True}
                   for t in texts]

    def head():
        return prompt_layer.build_head(
            chat_id=chat_id_for(context.id), card=card_t, user_name=user_name,
            modules_enabled=bool(modules), modules=[m["content"] for m in modules],
            layer_enabled=s["layer"], length_words=None)

    if s["sabotage"]:
        with _sabotaged_layer():
            h = head()
    else:
        h = head()

    trimmed: list[int] = []
    messages = completions._assemble_messages(
        h.text,
        completions._build_persona_block(persona),
        [dict(m) for m in context.history],
        context.user,
        h.final_note,
        history_budget_chars(max_tokens),
        0,
        boundary_block=limits_block(card, context.id) if s["limits"] else "",
        trimmed_out=trimmed,
        reminder_block=h.reminder,
        modules=modules or None,
    )
    if trimmed != [0]:
        raise FixtureTrimmed(f"{context.id}/{arm}: history trimmed {trimmed}")
    return messages
