"""G3-lite command line. Run from the repository root with the backend's
interpreter (it imports the app's assembly). See README.md for the order.

  plan                          build every request, count tokens, print the
                                scripted worst case; no key, no network
  run --stage smoke|full        paid: needs ELYSIUM_EVAL_OR_KEY in the env
  probe                         judge isolation probe (2 local claude calls)
  judge                         all judgments (local claude, subscription)
  calibrate [--port N]          serve the blind labelling page on 127.0.0.1
  gates                         compute the frozen gates and the verdict

Every command that writes takes --out DIR, which must be outside the repo.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from g3lite import config, costs, plan  # noqa: E402


def cmd_plan(args) -> int:
    count = costs.o200k_counter()
    smoke = plan.build(plan.STAGE_SMOKE, count=count)
    full = plan.build(plan.STAGE_FULL, count=count)
    everything = smoke + full
    print(f"prompt tokens (o200k): max {max(c.tokens_in for c in everything)} "
          f"(hard cap {config.PROMPT_HARD_CAP})")
    for label, calls in (("smoke", smoke), ("full", full), ("smoke + full", everything)):
        print(f"\n## {label}")
        for line in costs.ceiling([c.worst_usd for c in calls]).lines():
            print(line)
    print("\n## dF nominal model (no attack-card arm), for comparison")
    for line in costs.nominal_ceiling().lines():
        print(line)
    ceil = costs.ceiling([c.worst_usd for c in everything])
    ok = ceil.total <= config.BUDGET_USD
    print(f"\nspend guard: {'WOULD START' if ok else 'WOULD REFUSE'} "
          f"(${ceil.total:.4f} vs ${config.BUDGET_USD:.2f})")
    return 0 if ok else 2


def cmd_run(args, transport=None) -> int:
    from g3lite import runner, secret
    from g3lite.guard import OverBudget

    try:
        key = secret.read_key()
        count = costs.o200k_counter()
        out = runner.check_out_dir(Path(args.out))
    except (secret.MissingKey, costs.TokenizerMissing, runner.OutDirInRepo) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return runner.EXIT_SETUP
    redact = secret.Redactor(key)
    try:
        calls, guard, ceil = runner.prepare(args.stage, out, count)
    except OverBudget as exc:
        secret.safe_print(redact, f"refused: {exc}", stream=sys.stderr)
        return runner.EXIT_PREFLIGHT
    for line in ceil.lines():
        secret.safe_print(redact, line)
    status = out / "status.log"
    print("follow progress with:\n"
          f"  PowerShell: Get-Content -Wait \"{status}\"\n"
          f"  Git Bash:   tail -f \"{status}\"", flush=True)
    return asyncio.run(runner.run_stage(args.stage, calls, out, key=key, guard=guard,
                                         transport=transport))


def cmd_probe(args) -> int:
    from g3lite import judge
    res = judge.isolation_probe()
    print(f"ground (isolated, marker must be absent): seen={res['ground']} exit={res['ground_exit']}")
    print(f"positive (CLAUDE.md on, marker must be seen): seen={res['positive']} exit={res['positive_exit']}")
    print("PASS" if res["pass"] else "FAIL: do not run the judge")
    return 0 if res["pass"] else 1


def cmd_judge(args) -> int:
    from g3lite import judge, runner, secret
    out = runner.check_out_dir(Path(args.out))
    secrets = ()
    try:
        secrets = (secret.read_key(),)   # only to strip it from the child env
    except secret.MissingKey:
        pass
    return judge.run_all(out, secrets=secrets)


def cmd_calibrate(args) -> int:
    from g3lite import calibrate, runner
    out = runner.check_out_dir(Path(args.out))
    server, url = calibrate.make_server(out, args.port)
    print(f"open this address yourself (nothing is opened for you):\n  {url}\n"
          f"labels are saved to {out / calibrate.LABELS_FILE}; Ctrl+C to stop", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def cmd_gates(args) -> int:
    import json
    from g3lite import gates, runner
    out = runner.check_out_dir(Path(args.out))
    rep = gates.evaluate(out)
    path = gates.write_report(out, rep)
    print(json.dumps({k: rep[k] for k in ("gates", "module_gate", "validity", "secondary", "info")},
                     indent=1, ensure_ascii=False))
    print(f"\n{rep['verdict']}\n{rep['module_verdict']}\nmissing replies: {len(rep['missing_replies'])}")
    print(f"report: {path}")
    return 0


def main(argv=None, *, transport=None) -> int:
    """`transport` is an httpx transport for the gate tests; None is the network."""
    ap = argparse.ArgumentParser(prog="g3.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan")
    r = sub.add_parser("run")
    r.add_argument("--stage", choices=(plan.STAGE_SMOKE, plan.STAGE_FULL), required=True)
    r.add_argument("--out", required=True)
    sub.add_parser("probe")
    for name in ("judge", "gates"):
        sub.add_parser(name).add_argument("--out", required=True)
    c = sub.add_parser("calibrate")
    c.add_argument("--out", required=True)
    c.add_argument("--port", type=int, default=0)
    args = ap.parse_args(argv)
    if args.cmd == "run":
        return cmd_run(args, transport)
    return {"plan": cmd_plan, "probe": cmd_probe, "judge": cmd_judge,
            "calibrate": cmd_calibrate, "gates": cmd_gates}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
