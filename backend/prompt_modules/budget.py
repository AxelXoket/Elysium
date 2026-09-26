"""H4: how much of the context the enabled prompt modules may take.

One rule, one definition, used in two places: at request time inside the
assembly (B1) and at toggle time when a module is switched on here (dF 4):

    with at least one module on,
        sum(len(module text)) + len(separator) * modules
      <= MODULE_BUDGET_SHARE_PERCENT% of (history budget - non-module fixed cost)

The non-module fixed cost is everything the request carries that is not a
module: card (incl. its system prompt and final note), persona, Limits, the
notebook blocks, the voice block and the current user message. The fixed layer
is NOT in it: when a module is on the layer is not sent (Ek O 7). There is no
wrapper (Ek O 6). A card that overflows on its own stays `context_too_large`;
the card never counts against the module rule.

Token figures in the refusal use the app's own estimate
(config.CHARS_PER_TOKEN_ESTIMATE), the same unit as the context gauge.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from config import CHARS_PER_TOKEN_ESTIMATE, CONTEXT_SAFETY_MARGIN

from . import errors as E
from .limits import MODULE_BUDGET_SHARE_PERCENT, MODULE_SEPARATOR


def module_addition_chars(contents: Sequence[str]) -> int:
    """What the enabled modules add to the request: text plus one separator
    each (between modules, and between the last one and the card data)."""
    if not contents:
        return 0
    return sum(len(c) for c in contents) + len(MODULE_SEPARATOR) * len(contents)


def module_room(available_chars: int, fixed_chars: int) -> int:
    """The most the modules (text + separators) may take. Never negative."""
    free = available_chars - fixed_chars
    if free <= 0:
        return 0
    return free * MODULE_BUDGET_SHARE_PERCENT // 100


def tokens_for(chars: int) -> int:
    return math.ceil(chars / CHARS_PER_TOKEN_ESTIMATE) if chars > 0 else 0


@dataclass(frozen=True)
class Verdict:
    added_chars: int
    room_chars: int
    history_left_chars: int


def check(modules: Sequence[Mapping], *, available_chars: int,
          fixed_chars: int) -> Verdict:
    """Apply H4. Raises PromptModuleError on refusal, returns a Verdict.

    `modules` are the ENABLED modules as dicts with at least `name` and
    `content` (`id` too, when there is one). An entry that only fires on some
    generations is counted anyway: the rule is the worst case (dF 5.1).

    Order of the two refusals is the whole of H4's fairness: the card's own
    overflow is checked first and keeps its old name, so a module is never
    blamed for a card that does not fit with no module at all.
    """
    if fixed_chars > available_chars:
        raise E.PromptModuleError(E.CONTEXT_TOO_LARGE)
    contents = [m["content"] for m in modules]
    added = module_addition_chars(contents)
    room = module_room(available_chars, fixed_chars)
    if modules and added > room:
        raise E.PromptModuleError(
            E.OVER_BUDGET,
            modules=[{"id": m.get("id"), "name": m["name"],
                      "tokens": tokens_for(len(m["content"]))} for m in modules],
            total_tokens=tokens_for(added),
            room_tokens=tokens_for(room),
        )
    return Verdict(added, room, available_chars - fixed_chars - added)


def history_budget_chars(*, context_budget_tokens: int | None,
                         model_context_tokens: int,
                         max_tokens: int) -> int:
    """`available` exactly as the completion path computes it today
    (routers/completions.py, `_prepare_completion` context budget block):
    user window capped by the model's, a safety margin, then the reply
    reserve, all at CHARS_PER_TOKEN_ESTIMATE. Kept here so the toggle-time
    check and the request-time check stand on one number (request to B1:
    call this instead of the inline copy)."""
    if context_budget_tokens is not None:
        effective = (min(context_budget_tokens, model_context_tokens)
                     if model_context_tokens > 0 else context_budget_tokens)
    else:
        effective = model_context_tokens
    safety = min(CONTEXT_SAFETY_MARGIN, effective // 8)
    budget_chars = max(0, effective - safety) * CHARS_PER_TOKEN_ESTIMATE
    reply_chars = max_tokens * CHARS_PER_TOKEN_ESTIMATE
    if reply_chars > budget_chars:
        reply_chars = max(0, budget_chars // 2)
    return budget_chars - reply_chars
