"""The hand-written fixtures, loaded and turned into contexts.

A context is one request's worth of story: a card, a history and the user
message that ends it. The kinds (config.WORKLOADS):
  q   quality continuation point: greeting + 4 exchanges (+ 1 more for point 2)
  o   OOC question probe: greeting + 2 exchanges, then the probe
  l   limit bait: greeting + 2 exchanges, then one of three baits
  la  limit bait on the attack card, same shape as l
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache

from . import FIXTURES_DIR

CARDS_FILE = FIXTURES_DIR / "cards.json"
ATTACK_MODULE_FILE = FIXTURES_DIR / "attack_module.txt"

#: Exchanges of history before an o, l or la context.
SHORT_HISTORY_EXCHANGES = 2


@dataclass(frozen=True)
class Context:
    id: str              # e.g. "c3/q2", "c1/l3", "atk/l1"
    kind: str            # q, o, l, la
    card: dict
    history: tuple       # ({"role", "content"}, ...), oldest first
    user: str


@lru_cache(maxsize=1)
def load() -> dict:
    with open(CARDS_FILE, encoding="utf-8") as fh:
        return json.load(fh)


def attack_module() -> str:
    with open(ATTACK_MODULE_FILE, encoding="utf-8") as fh:
        return fh.read().strip()


def persona() -> dict:
    return dict(load()["persona"])


def cards() -> list[dict]:
    return list(load()["cards"])


def attack_card() -> dict:
    return dict(load()["attack_card"])


def _history(card: dict, exchanges: int) -> list[dict]:
    out = [{"role": "assistant", "content": card["greeting"]}]
    for user, reply in card["history"][:exchanges]:
        out.append({"role": "user", "content": user})
        out.append({"role": "assistant", "content": reply})
    return out


def contexts(kind: str) -> list[Context]:
    """Every context of one kind, in a fixed order."""
    out: list[Context] = []
    if kind == "q":
        for card in cards():
            base = _history(card, len(card["history"]))
            p1, p2 = card["q_points"]
            out.append(Context(f"{card['id']}/q1", "q", card, tuple(base), p1["user"]))
            longer = base + [{"role": "user", "content": p1["user"]},
                             {"role": "assistant", "content": p1["reply"]}]
            out.append(Context(f"{card['id']}/q2", "q", card, tuple(longer), p2["user"]))
    elif kind == "o":
        for card in cards():
            out.append(Context(f"{card['id']}/o1", "o", card,
                               tuple(_history(card, SHORT_HISTORY_EXCHANGES)),
                               card["ooc_probe"]))
    elif kind in ("l", "la"):
        pool = cards() if kind == "l" else [attack_card()]
        for card in pool:
            hist = tuple(_history(card, SHORT_HISTORY_EXCHANGES))
            for i, bait in enumerate(card["baits"], 1):
                out.append(Context(f"{card['id']}/{kind}{i}", kind, card, hist, bait))
    else:
        raise ValueError(f"unknown context kind {kind!r}")
    return out
