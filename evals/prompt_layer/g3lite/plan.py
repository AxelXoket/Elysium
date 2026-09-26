"""The work list: every request the paid run may send, fully built.

A PlannedCall carries its complete request body. Nothing is added to a body
after planning except the Authorization header at send time, so what the
spend guard priced and what request_is_safe checked is exactly what goes out.
"""

from __future__ import annotations

import copy
import random
import sys
from dataclasses import dataclass, field

from . import BACKEND_DIR, config, fixtures
from .assemble import build_messages

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from config import PROVIDER_POLICY  # noqa: E402  (the app's locked policy)

STAGE_SMOKE = "smoke"
STAGE_FULL = "full"


@dataclass
class PlannedCall:
    call_id: str
    stage: str
    model: config.Model
    workload: str
    context_id: str
    arm: str
    sample: int
    body: dict
    tokens_in: int = 0
    worst_usd: float = 0.0
    meta: dict = field(default_factory=dict)


def provider_object(model: config.Model) -> dict:
    """The app's locked provider policy plus the price cap and quantizations."""
    p = dict(PROVIDER_POLICY)
    p["zdr"] = True
    p["data_collection"] = "deny"
    p["require_parameters"] = True
    p["max_price"] = {"prompt": model.cap_in, "completion": model.cap_out}
    p["quantizations"] = list(config.QUANTIZATIONS)
    return p


def request_body(model: config.Model, messages: list[dict]) -> dict:
    """No sampler is sent (dF 2.4, D1): provider defaults."""
    body = {
        "model": model.id,
        "messages": messages,
        "max_tokens": model.max_tokens,
        "stream": False,
        "usage": {"include": True},
        "provider": provider_object(model),
    }
    if model.reasoning is not None:
        body["reasoning"] = dict(model.reasoning)
    return body


class UnsafeRequest(ValueError):
    pass


def request_is_safe(body: dict, model: config.Model) -> None:
    """Refuse a body that could be billed above its cap or leave ZDR.
    Called on every planned body and again right before every send."""
    p = body.get("provider")
    if not isinstance(p, dict):
        raise UnsafeRequest("no provider object")
    mp = p.get("max_price")
    if not isinstance(mp, dict) or set(mp) != {"prompt", "completion"}:
        raise UnsafeRequest("provider.max_price missing or malformed")
    if not (0 < mp["prompt"] <= model.cap_in and 0 < mp["completion"] <= model.cap_out):
        raise UnsafeRequest("provider.max_price above the model's cap")
    if p.get("zdr") is not True or p.get("data_collection") != "deny":
        raise UnsafeRequest("provider must be zdr=true, data_collection=deny")
    if list(p.get("quantizations") or []) != list(config.QUANTIZATIONS):
        raise UnsafeRequest("provider.quantizations differs from fp8-or-better")
    if body.get("model") != model.id:
        raise UnsafeRequest("body model differs from the planned model")
    if body.get("max_tokens") != model.max_tokens:
        raise UnsafeRequest("max_tokens differs from the priced value")
    if body.get("stream") is not False:
        raise UnsafeRequest("streaming is not priced")


def _contexts_by_kind() -> dict[str, list]:
    return {k: fixtures.contexts(k) for k in ("q", "o", "l", "la")}


def build(stage: str, *, models: tuple[config.Model, ...] = config.MODELS,
          count=None, dropped: frozenset[str] = frozenset()) -> list[PlannedCall]:
    """Every call of a stage. `count` (a costs.Counter) fills tokens_in and
    worst_usd; without it they stay 0 (shape-only planning)."""
    from . import costs

    persona = fixtures.persona()
    attack = fixtures.attack_module()
    by_kind = _contexts_by_kind()
    calls: list[PlannedCall] = []
    for m in models:
        if m.id in dropped:
            continue
        if stage == STAGE_SMOKE:
            wanted = [(w, arms) for w, arms in config.SMOKE.items() if w in m.workloads]
            for w, arms in wanted:
                kind = config.WORKLOADS[w][0]
                ctx = by_kind[kind][0]
                for arm in arms:
                    calls.append(_call(stage, m, w, ctx, arm, 1, persona, attack))
        elif stage == STAGE_FULL:
            for w in m.workloads:
                kind, arms = config.WORKLOADS[w]
                for ctx in by_kind[kind]:
                    for arm, samples in arms.items():
                        for s in range(1, samples + 1):
                            calls.append(_call(stage, m, w, ctx, arm, s, persona, attack))
        else:
            raise ValueError(f"unknown stage {stage!r}")
    for c in calls:
        request_is_safe(c.body, c.model)
        if count is not None:
            c.tokens_in = costs.prompt_tokens(c.body["messages"], count)
            c.worst_usd = costs.call_ceiling(c.model, c.tokens_in)
    return calls


def _call(stage, m, w, ctx, arm, sample, persona, attack) -> PlannedCall:
    msgs = build_messages(ctx, arm, max_tokens=m.max_tokens, persona=persona,
                          attack_module=attack)
    return PlannedCall(
        call_id=f"{stage}/{m.short}/{w}/{ctx.id}/{arm}#{sample}",
        stage=stage, model=m, workload=w, context_id=ctx.id, arm=arm,
        sample=sample, body=request_body(m, copy.deepcopy(msgs)),
        meta={"card": ctx.card["id"], "lang": ctx.card["lang"], "kind": ctx.kind})


def send_order(calls: list[PlannedCall], seed: int = config.SEED) -> list[PlannedCall]:
    """Plan order by context; the arms (and samples) of one context are sent
    back to back in a seeded random order, so provider drift falls evenly on
    every arm (dF 2.2)."""
    rng = random.Random(seed)
    groups: dict[tuple, list[PlannedCall]] = {}
    for c in calls:
        groups.setdefault((c.stage, c.model.id, c.context_id), []).append(c)
    out: list[PlannedCall] = []
    for key in groups:
        g = groups[key][:]
        rng.shuffle(g)
        out.extend(g)
    return out
