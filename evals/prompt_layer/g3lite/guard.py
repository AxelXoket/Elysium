"""The spend guard.

Two refusals, both measured in worst-case dollars INCLUDING the worst fee:

  preflight   before anything is sent: already spent + the scripted worst case
              of what remains must be <= budget, or the run does not start.
  reserve     before EVERY send (retries included): spent + everything in
              flight + this call's worst case, times (1 + fee), must stay
              <= budget, or the run stops there. A reply without usage.cost
              is charged at its worst case, never at zero.

The budget is config.BUDGET_USD ($0.84) unless a smaller one is given; a
larger one is refused outright, so no flag can lift my line.
"""

from __future__ import annotations

from . import config
from .costs import Ceiling


class OverBudget(RuntimeError):
    pass


class SpendGuard:
    def __init__(self, budget_usd: float = config.BUDGET_USD, *,
                 already_spent: float = 0.0) -> None:
        if not 0 < budget_usd <= config.BUDGET_USD:
            raise OverBudget(
                f"budget ${budget_usd:.2f} is above the refusal line "
                f"${config.BUDGET_USD:.2f}")
        self.budget = budget_usd
        self.fee_factor = 1 + config.FEE_RATE_WORST
        self.spent = already_spent
        self.reserved = 0.0
        self.anomalies: list[str] = []

    def preflight(self, ceiling: Ceiling) -> None:
        if ceiling.already_spent != self.spent:
            raise OverBudget("ceiling was computed without this run's spend")
        if ceiling.total > self.budget:
            raise OverBudget(
                f"scripted worst case ${ceiling.total:.4f} exceeds "
                f"${self.budget:.2f}; nothing was sent")

    def worst_total(self, extra: float = 0.0) -> float:
        return (self.spent + self.reserved + extra) * self.fee_factor

    def reserve(self, worst_usd: float) -> float:
        if worst_usd <= 0:
            raise OverBudget("a call with no worst-case price cannot be sent")
        if self.worst_total(worst_usd) > self.budget:
            raise OverBudget(
                f"next call could take the run to ${self.worst_total(worst_usd):.4f} "
                f"(> ${self.budget:.2f}); stopping before it")
        self.reserved += worst_usd
        return worst_usd

    def settle(self, reserved: float, cost: float | None) -> float:
        """Release a reservation and book what the call really cost. `None`
        (no usage.cost in the reply, or no reply) books the worst case."""
        self.reserved -= reserved
        if cost is None or cost < 0:
            charged = reserved
        else:
            charged = float(cost)
            if charged > reserved:
                self.anomalies.append(
                    f"a call cost ${charged:.6f}, above its ceiling ${reserved:.6f}")
        self.spent += charged
        return charged
