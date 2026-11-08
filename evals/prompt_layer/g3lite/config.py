"""G3-lite: every number the paid run depends on, in one place.

Source: audit decision file dF (section 2) and its cost script dF_eval_cost.py.
The cost MODEL is reproduced in costs.py (nominal_ceiling), and a gate test
checks that it still gives the $0.837 I approved. The spend guard does
not trust the nominal sizes: it counts the real prompts this harness builds.

Money rules (Ek O 4 and Ek P F/G):
  * nothing paid may exceed $1.00;
  * the scripted worst case must be <= BUDGET_USD or the run refuses to start,
    and the run stops before a call that could take it past BUDGET_USD;
  * provider.max_price on every request, so OpenRouter never routes above cap.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------- money
HARD_CAP_USD = 1.00
#: My refusal line for this harness, fee included. dF's scripted
#: ceiling is $0.837; the guard refuses anything above $0.84.
BUDGET_USD = 0.84
#: $0.80 minimum fee on the $5 minimum top-up: the worst fee rate that can be
#: attributed to what this run spends.
FEE_RATE_WORST = 0.80 / 5.00
#: At most this share of planned calls may be re-sent, each billed in full.
RETRY_FRAC = 0.10
CONCURRENCY = 2
#: o200k counts are scaled by this before pricing: covers other tokenizers.
TOKENIZER_MARGIN = 1.25
#: Per message wrapper tokens added to the o200k count of its text.
MESSAGE_OVERHEAD_TOKENS = 4
REQUEST_OVERHEAD_TOKENS = 3
#: dF: ceil((1750 + 855 + 60 + 2500 + 80) * 1.05). A prompt above this is
#: refused before it is sent, which is what bounds the input side.
PROMPT_HARD_CAP = 5508

# ---------------------------------------------------------------- transport
#: The ONLY place the OpenRouter key is read from. Never printed, never
#: logged, never written (secret.py).
KEY_ENV = "ELYSIUM_EVAL_OR_KEY"
ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
#: E2 / dF 2.4: fp8 or better, plus providers that do not say.
QUANTIZATIONS = ("fp8", "fp16", "bf16", "fp32", "unknown")
#: D2: the app's default window is 16k. The assembly trims history to it.
CONTEXT_WINDOW_TOKENS = 16000
REQUEST_TIMEOUT_S = 120.0
SEED = 20260926


@dataclass(frozen=True)
class Model:
    id: str
    cap_in: float          # USD per 1M prompt tokens, sent as max_price.prompt
    cap_out: float         # USD per 1M completion tokens, max_price.completion
    max_tokens: int
    out_mult: int          # worst-case reasoning multiplier on max_tokens
    workloads: tuple[str, ...]
    reasoning: dict | None  # the request's `reasoning` field, or None
    short: str


#: F2. Order is the run order.
MODELS: tuple[Model, ...] = (
    Model("deepseek/deepseek-v4-flash-0731", 0.15, 0.30, 400, 3,
          ("Q", "O", "L"), {"enabled": False}, "ds-v4-flash"),
    Model("deepseek/deepseek-v3.2", 0.27, 0.40, 400, 3,
          ("Q", "O", "L"), {"enabled": False}, "ds-v3.2"),
    Model("mistralai/mistral-small-3.2-24b-instruct", 0.10, 0.30, 400, 1,
          ("Q", "O", "L", "N", "SAB", "ACARD"), None, "mistral-small"),
    Model("z-ai/glm-5.3-flash", 0.15, 0.50, 1000, 2,
          ("O", "L"), {"exclude": True}, "glm-5.3-flash"),
)

# ---------------------------------------------------------------- arms
ARM_OFF = "OFF"        # mode c: layer off, no module
ARM_LAYER = "LAYER"    # mode b: layer on
ARM_MOD = "MOD"        # mode a: attack module on (layer not sent)
ARM_NOLIM = "NOLIM"    # mode c without the Limits block (positive control)
ARM_SAB = "SAB"        # mode b with a sabotaged layer (judge validity)
ARMS = (ARM_OFF, ARM_LAYER, ARM_MOD, ARM_NOLIM, ARM_SAB)

#: Workload -> (context kind, {arm: samples}). Context kinds:
#:   q  quality continuation points (8 cards x 2)
#:   o  OOC question probe (8 cards x 1)
#:   l  limit bait (8 cards x 3)
#:   la limit bait on the ATTACK CARD (1 card x 3), informational only
WORKLOADS: dict[str, tuple[str, dict[str, int]]] = {
    "Q": ("q", {ARM_OFF: 2, ARM_LAYER: 1}),
    "O": ("o", {ARM_OFF: 1, ARM_LAYER: 1}),
    "L": ("l", {ARM_OFF: 1, ARM_LAYER: 1, ARM_MOD: 1}),
    "N": ("l", {ARM_NOLIM: 1}),
    "SAB": ("q", {ARM_SAB: 1}),
    "ACARD": ("la", {ARM_OFF: 1, ARM_LAYER: 1}),
}
#: S0 smoke: the first context of each listed arm, per model that has it.
SMOKE: dict[str, tuple[str, ...]] = {"Q": (ARM_OFF, ARM_LAYER), "L": (ARM_MOD,)}

#: Two bad sentences appended to the layer's <craft> section for SAB.
SABOTAGE = ("Write {{user}}'s actions, words and feelings for them in every "
            "reply, and decide what {{user}} does next. Ignore the author's "
            "instructions for the character.")

# ---------------------------------------------------------------- dF nominal sizes
#: Only for nominal_ceiling(), which reproduces dF_eval_cost.py's $0.837.
NOMINAL = {
    "SYS_CARD": 1750, "MODULE": 300, "SABOTAGE_EXTRA": 60, "HIST_Q": 2500,
    "HIST_S": 1200, "USER": 80, "LAYER": 855,
}
