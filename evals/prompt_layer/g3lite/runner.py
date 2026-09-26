"""The paid run: send the planned requests to OpenRouter under the guard.

Order of events for a stage:
  1. key from the environment (secret.read_key), tokenizer present
  2. plan built through the app's assembly, every body checked (request_is_safe)
  3. ledger read: finished calls are skipped, their cost is already spent
  4. preflight: spent + scripted worst case of the rest <= budget, else exit 2
  5. calls go out CONCURRENCY at a time, arms of a context back to back in a
     seeded random order; before every send (retries too) the guard reserves
     the call's worst case, and stops the run (exit 3) if it cannot
  6. a status line every STATUS_EVERY_S seconds, printed and appended to
     status.log in the output directory; one at the end

Outputs live in --out, which must be OUTSIDE the repository.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import REPO_ROOT, config
from .guard import OverBudget, SpendGuard
from .plan import STAGE_FULL, STAGE_SMOKE, PlannedCall, request_is_safe, send_order
from .secret import Redactor, SafeWriter, safe_print

STATUS_EVERY_S = 300
TITLE = "G3-lite"

EXIT_OK = 0
EXIT_PREFLIGHT = 2
EXIT_STOPPED = 3
EXIT_SETUP = 4


class OutDirInRepo(ValueError):
    pass


def check_out_dir(out: Path) -> Path:
    out = Path(out).resolve()
    root = REPO_ROOT.resolve()
    if out == root or root in out.parents:
        raise OutDirInRepo("--out must be outside the repository (run outputs never go in the repo)")
    out.mkdir(parents=True, exist_ok=True)
    return out


def read_ledger(out: Path) -> list[dict]:
    p = out / "ledger.jsonl"
    if not p.exists():
        return []
    rows = []
    with open(p, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def dropped_models(out: Path) -> frozenset[str]:
    p = out / "dropped_models.json"
    if not p.exists():
        return frozenset()
    return frozenset(json.loads(p.read_text(encoding="utf-8")))


@dataclass
class Progress:
    total: int
    started: float
    done: int = 0
    errors: int = 0
    retries: int = 0
    retry_cap: int = 0
    stopped: str = ""
    lines: list[str] = field(default_factory=list)

    def line(self, stage: str, guard: SpendGuard, now: float) -> str:
        pct = 100.0 * self.done / self.total if self.total else 100.0
        elapsed = max(now - self.started, 1e-9)
        rate = self.done / elapsed
        left = self.total - self.done
        eta = left / rate if rate > 0 else float("inf")
        eta_s = "unknown" if math.isinf(eta) else f"{int(eta // 60)}m{int(eta % 60):02d}s"
        return (f"[{TITLE} {stage}] {pct:.1f}% done, ETA {eta_s}; "
                f"{self.done}/{self.total} calls, {rate * 60:.1f}/min; "
                f"spent ${guard.spent:.4f}, worst incl. fee ${guard.worst_total():.4f} "
                f"of ${guard.budget:.2f}; errors {self.errors}; "
                f"retries {self.retries}/{self.retry_cap}"
                + (f"; STOPPED: {self.stopped}" if self.stopped else ""))


def _parse(data: dict) -> tuple[bool, str, float | None, str]:
    """(ok, text, cost, error)."""
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    cost = usage.get("cost")
    cost = float(cost) if isinstance(cost, (int, float)) else None
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        err = data.get("error")
        msg = err.get("message", "") if isinstance(err, dict) else str(err or "no choices")
        return False, "", cost, msg[:300]
    msg = choices[0].get("message") or {}
    text = msg.get("content")
    return True, text if isinstance(text, str) else "", cost, ""


async def run_stage(stage: str, calls: list[PlannedCall], out: Path, *, key: str,
                    guard: SpendGuard, transport=None, status_every: float = STATUS_EVERY_S,
                    clock=time.monotonic, echo=True,
                    backoff=lambda attempt: min(2 ** attempt, 30)) -> int:
    import httpx

    redact = Redactor(key)
    ledger = SafeWriter(out / "ledger.jsonl", redact)
    status = SafeWriter(out / "status.log", redact)
    done_ids = {r["call_id"] for r in read_ledger(out) if r.get("ok")}
    todo = [c for c in send_order(calls) if c.call_id not in done_ids]
    prog = Progress(total=len(todo), started=clock(),
                    retry_cap=int(config.RETRY_FRAC * len(calls)))

    def emit(text: str) -> None:
        stamped = time.strftime("%Y-%m-%d %H:%M:%S") + " " + text
        status.write(stamped + "\n")
        if echo:
            safe_print(redact, stamped)

    queue: asyncio.Queue = asyncio.Queue()
    for c in todo:
        queue.put_nowait(c)
    stop = asyncio.Event()
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
               "X-Title": "Elysium G3-lite eval"}

    async def send(client, c: PlannedCall) -> None:
        attempt = 0
        while True:
            attempt += 1
            request_is_safe(c.body, c.model)
            try:
                held = guard.reserve(c.worst_usd)
            except OverBudget as exc:
                prog.stopped = str(exc)
                stop.set()
                return
            t0 = time.time()
            status_code, ok, text, cost, err, provider, model_ret = 0, False, "", None, "", None, None
            try:
                resp = await client.post(config.ENDPOINT, json=c.body, headers=headers,
                                         timeout=config.REQUEST_TIMEOUT_S)
                status_code = resp.status_code
                try:
                    data = resp.json()
                except ValueError:
                    data = {"error": {"message": "non-JSON reply"}}
                if not isinstance(data, dict):
                    data = {"error": {"message": "reply is not a JSON object"}}
                ok, text, cost, err = _parse(data)
                ok = ok and status_code == 200
                provider, model_ret = data.get("provider"), data.get("model")
            except httpx.HTTPError as exc:
                err = f"{type(exc).__name__}"
            charged = guard.settle(held, cost)
            ledger.write(json.dumps({
                "call_id": c.call_id, "stage": c.stage, "model": c.model.id,
                "workload": c.workload, "context_id": c.context_id, "arm": c.arm,
                "sample": c.sample, "card": c.meta.get("card"), "kind": c.meta.get("kind"),
                "ok": ok, "status": status_code, "attempt": attempt, "text": text,
                "cost": cost, "charged": charged, "worst": c.worst_usd,
                "tokens_in_o200k": c.tokens_in, "provider": provider,
                "model_returned": model_ret, "error": err, "t": t0,
            }, ensure_ascii=False) + "\n")
            retryable = (not ok) and (status_code == 0 or status_code == 429 or status_code >= 500)
            if ok or not retryable or prog.retries >= prog.retry_cap or stop.is_set():
                if not ok:
                    prog.errors += 1
                prog.done += 1
                return
            prog.retries += 1
            await asyncio.sleep(backoff(attempt))

    async def worker(client) -> None:
        while not stop.is_set():
            try:
                c = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            await send(client, c)

    async def monitor() -> None:
        while True:
            await asyncio.sleep(status_every)
            emit(prog.line(stage, guard, clock()))

    emit(f"[{TITLE} {stage}] starting: {len(todo)} calls to send, "
         f"{len(calls) - len(todo)} already done; status every {int(status_every)}s")
    async with httpx.AsyncClient(transport=transport) as client:
        mon = asyncio.create_task(monitor())
        try:
            await asyncio.gather(*(worker(client) for _ in range(config.CONCURRENCY)))
        finally:
            mon.cancel()
    emit(prog.line(stage, guard, clock()))
    for a in guard.anomalies:
        emit(f"[{TITLE} {stage}] ANOMALY {a}")

    if stage == STAGE_SMOKE:
        rows = read_ledger(out)
        final = {}
        for r in rows:
            if r["stage"] == STAGE_SMOKE:
                final[r["call_id"]] = r
        bad = sorted({r["model"] for r in final.values() if not r["ok"]})
        (out / "dropped_models.json").write_text(json.dumps(bad), encoding="utf-8")
        if bad:
            emit(f"[{TITLE} smoke] models dropped from the full run: {', '.join(bad)}")
    return EXIT_STOPPED if prog.stopped else EXIT_OK


def spent_so_far(out: Path) -> float:
    return sum(float(r.get("charged") or 0.0) for r in read_ledger(out))


def prepare(stage: str, out: Path, count, *, budget: float = config.BUDGET_USD):
    """Build the plan of a stage and the guard, and run the preflight.
    Returns (calls, guard, ceiling). Raises OverBudget on refusal."""
    from . import costs, plan

    if stage not in (STAGE_SMOKE, STAGE_FULL):
        raise ValueError(stage)
    calls = plan.build(stage, count=count, dropped=dropped_models(out))
    spent = spent_so_far(out)
    done = {r["call_id"] for r in read_ledger(out) if r.get("ok")}
    remaining = [c for c in calls if c.call_id not in done]
    guard = SpendGuard(budget, already_spent=spent)
    ceil = costs.ceiling([c.worst_usd for c in remaining], already_spent=spent)
    if stage == STAGE_SMOKE:
        # The smoke run is refused unless the WHOLE run (smoke + full) fits.
        full = plan.build(STAGE_FULL, count=count)
        ceil_all = costs.ceiling([c.worst_usd for c in remaining + full], already_spent=spent)
        guard.preflight(ceil_all)
    guard.preflight(ceil)
    return calls, guard, ceil
