"""Worst-case dollars, from the prompts this harness actually builds.

The ceiling (dF 2.7) is, per call,
    input  = o200k tokens of the request x TOKENIZER_MARGIN x cap_in
    output = max_tokens x out_mult x cap_out
and for the run
    usage  = planned + RETRY_FRAC x planned + CONCURRENCY x largest call
    total  = usage x (1 + FEE_RATE_WORST)
A request above PROMPT_HARD_CAP o200k tokens is refused, never priced.

The token counter is a parameter so the gate tests can run without tiktoken;
the real run uses o200k_counter(), which fails closed when tiktoken is absent.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from . import config

Counter = Callable[[str], int]


class PromptTooLarge(ValueError):
    pass


class TokenizerMissing(RuntimeError):
    pass


def o200k_counter() -> Counter:
    """tiktoken's o200k_base. Without it the run cannot bound its input
    cost, so it refuses instead of guessing."""
    try:
        import tiktoken
    except ImportError:
        raise TokenizerMissing(
            "tiktoken is not installed in this interpreter; install "
            "evals/prompt_layer/requirements.txt (see README)") from None
    enc = tiktoken.get_encoding("o200k_base")
    return lambda text: len(enc.encode(text, disallowed_special=()))


def message_text(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(p.get("text", "") for p in content if isinstance(p, dict))


def prompt_tokens(messages: list[dict], count: Counter) -> int:
    return config.REQUEST_OVERHEAD_TOKENS + sum(
        config.MESSAGE_OVERHEAD_TOKENS + count(message_text(m["content"]))
        for m in messages)


def call_ceiling(model: config.Model, tokens_in: int) -> float:
    if tokens_in > config.PROMPT_HARD_CAP:
        raise PromptTooLarge(
            f"{tokens_in} o200k tokens > PROMPT_HARD_CAP {config.PROMPT_HARD_CAP}")
    return (tokens_in * config.TOKENIZER_MARGIN * model.cap_in / 1e6
            + model.max_tokens * model.out_mult * model.cap_out / 1e6)


@dataclass(frozen=True)
class Ceiling:
    calls: int
    planned: float
    retries: float
    overshoot: float
    already_spent: float

    @property
    def usage(self) -> float:
        return self.already_spent + self.planned + self.retries + self.overshoot

    @property
    def fee(self) -> float:
        return self.usage * config.FEE_RATE_WORST

    @property
    def total(self) -> float:
        return self.usage + self.fee

    @property
    def key_limit(self) -> float:
        """What to set as the OpenRouter key's credit limit, rounded up."""
        return math.ceil((self.already_spent + self.planned + self.retries) * 100) / 100

    def lines(self) -> list[str]:
        return [
            f"calls {self.calls}; planned worst ${self.planned:.4f}; "
            f"retries (<= {config.RETRY_FRAC:.0%}) ${self.retries:.4f}; "
            f"in-flight overshoot ${self.overshoot:.4f}; already spent ${self.already_spent:.4f}",
            f"usage worst ${self.usage:.4f}; worst fee "
            f"({config.FEE_RATE_WORST:.0%}) ${self.fee:.4f}",
            f"CEILING ${self.total:.4f} (refusal line ${config.BUDGET_USD:.2f}, "
            f"hard cap ${config.HARD_CAP_USD:.2f}); key credit limit ${self.key_limit:.2f}",
        ]


def ceiling(worst_per_call: Iterable[float], already_spent: float = 0.0) -> Ceiling:
    costs = list(worst_per_call)
    planned = sum(costs)
    return Ceiling(
        calls=len(costs),
        planned=planned,
        retries=planned * config.RETRY_FRAC,
        overshoot=config.CONCURRENCY * max(costs, default=0.0),
        already_spent=already_spent,
    )


# ------------------------------------------------------------------ dF nominal
def nominal_ceiling(layer_tokens: int = config.NOMINAL["LAYER"]) -> Ceiling:
    """dF_eval_cost.py's model with its nominal prompt sizes (no ACARD arm,
    which dF did not have). Kept so a test can pin the approved $0.837."""
    n = config.NOMINAL
    arm_tokens = {
        config.ARM_OFF: n["SYS_CARD"], config.ARM_LAYER: n["SYS_CARD"] + layer_tokens,
        config.ARM_MOD: n["SYS_CARD"] + n["MODULE"], config.ARM_NOLIM: n["SYS_CARD"],
        config.ARM_SAB: n["SYS_CARD"] + layer_tokens + n["SABOTAGE_EXTRA"],
    }
    hist = {"q": n["HIST_Q"], "o": n["HIST_S"], "l": n["HIST_S"]}
    counts = {"q": 16, "o": 8, "l": 24}
    costs: list[float] = []

    def one(m, arm, kind):
        tin = (arm_tokens[arm] + hist[kind] + n["USER"]) * config.TOKENIZER_MARGIN
        return tin * m.cap_in / 1e6 + m.max_tokens * m.out_mult * m.cap_out / 1e6

    for m in config.MODELS:
        for w in m.workloads:
            if w == "ACARD":
                continue
            kind, arms = config.WORKLOADS[w]
            for arm, samples in arms.items():
                costs += [one(m, arm, kind)] * (counts[kind] * samples)
        for w, arms in config.SMOKE.items():
            if w in m.workloads:
                kind = config.WORKLOADS[w][0]
                costs += [one(m, arm, kind) for arm in arms]
    return ceiling(costs)
