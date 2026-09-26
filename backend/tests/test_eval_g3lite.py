"""G3-lite harness guards (evals/prompt_layer). Free, local, no network.

Nothing here calls OpenRouter or runs the judge: the provider is an
httpx.MockTransport and the judge is a fake `claude` on PATH that records
what it was given.

  spend guard    refuses an over-budget plan (positive: an under-budget plan
                 starts); stops mid-run before the line; books a reply with
                 no usage.cost at its worst case; cannot be given a budget
                 above $0.84; caps retries
  key canary     the key reaches the Authorization header (positive control)
                 and nowhere else: not stdout, stderr, logs, status, ledger
  max_price      on every planned request, equal to the model's cap; a body
                 without it is refused before anything is sent
  judge          `claude -p --model opus`, no tools, no MCP, hooks off, no
                 CLAUDE.md or memory, empty cwd, and no API key or OpenRouter
                 key in its environment even when the parent has them
  arms           mode a has the module and no layer, mode b has the layer,
                 mode c has neither; NOLIM has no Limits; SAB is sabotaged
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import sys
import textwrap
from pathlib import Path

import httpx
import pytest

EVAL_DIR = Path(__file__).resolve().parents[2] / "evals" / "prompt_layer"
sys.path.insert(0, str(EVAL_DIR))

import g3  # noqa: E402
from g3lite import config, costs, fixtures, judge, plan, runner, secret  # noqa: E402
from g3lite.assemble import build_messages  # noqa: E402
from g3lite.guard import OverBudget, SpendGuard  # noqa: E402

import prompt_layer  # noqa: E402

FEE = 1 + config.FEE_RATE_WORST


def fake_count(text: str) -> int:
    """Deterministic stand-in for o200k (about 3 characters a token)."""
    return len(text) // 3 + 1


@pytest.fixture(scope="module")
def smoke_calls():
    return plan.build(plan.STAGE_SMOKE, count=fake_count)


@pytest.fixture(scope="module")
def full_calls():
    return plan.build(plan.STAGE_FULL, count=fake_count)


def ok_reply(cost):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        usage = {} if cost is None else {"cost": cost(body)}
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "A plain reply."}}],
            "usage": usage, "provider": "TestProvider", "model": body["model"]})
    return handler


def _run(calls, out, guard, handler, **kw):
    seen = []

    def recording(request):
        seen.append(request)
        return handler(request)

    code = asyncio.run(runner.run_stage(
        plan.STAGE_SMOKE, calls, out, key="k-" + secrets.token_hex(8), guard=guard,
        transport=httpx.MockTransport(recording), echo=False, status_every=3600,
        backoff=lambda a: 0, **kw))
    return code, seen


# ============================================================ spend guard
class TestSpendGuardPreflight:
    def test_a_plan_over_the_line_is_refused(self):
        over = costs.ceiling([0.40, 0.40])
        assert over.total > config.BUDGET_USD
        with pytest.raises(OverBudget):
            SpendGuard().preflight(over)

    def test_a_plan_under_the_line_starts(self):
        # POSITIVE CONTROL: a guard that refused everything would pass above.
        under = costs.ceiling([0.001] * 20)
        assert under.total < config.BUDGET_USD
        SpendGuard().preflight(under)

    def test_what_was_already_spent_counts(self):
        under = costs.ceiling([0.001] * 20, already_spent=0.72)
        SpendGuard().preflight(costs.ceiling([0.001] * 20))   # alone it fits
        assert under.total > config.BUDGET_USD
        with pytest.raises(OverBudget):
            SpendGuard(already_spent=0.72).preflight(under)

    def test_the_budget_cannot_be_raised_above_the_line(self):
        with pytest.raises(OverBudget):
            SpendGuard(config.BUDGET_USD + 0.01)
        SpendGuard(config.BUDGET_USD)          # the line itself is allowed

    def test_the_real_plan_is_refused_under_a_tiny_budget(self, tmp_path):
        with pytest.raises(OverBudget):
            runner.prepare(plan.STAGE_SMOKE, tmp_path, fake_count, budget=0.01)

    def test_the_real_plan_starts_under_the_owner_line(self, tmp_path):
        calls, guard, ceil = runner.prepare(plan.STAGE_SMOKE, tmp_path, fake_count)
        assert calls and ceil.total <= config.BUDGET_USD

    def test_the_cli_refuses_to_start_over_budget(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv(config.KEY_ENV, "k-" + secrets.token_hex(8))
        monkeypatch.setattr(costs, "o200k_counter", lambda: fake_count)
        monkeypatch.setattr(config, "BUDGET_USD", 0.001)
        sent = []
        code = g3.main(["run", "--stage", "smoke", "--out", str(tmp_path)],
                       transport=httpx.MockTransport(lambda r: sent.append(r)))
        assert code == runner.EXIT_PREFLIGHT
        assert sent == []


class TestSpendGuardMidRun:
    def test_the_run_stops_before_the_call_that_would_cross_the_line(self, tmp_path, smoke_calls):
        worst = [c.worst_usd for c in smoke_calls]
        budget = sum(sorted(worst)[:4]) * FEE        # room for about four calls
        guard = SpendGuard(budget)
        by_body = {c.call_id: c.worst_usd for c in smoke_calls}
        code, seen = _run(smoke_calls, tmp_path, guard,
                          ok_reply(lambda b: max(by_body.values())))
        assert code == runner.EXIT_STOPPED
        assert 0 < len(seen) < len(smoke_calls)
        assert guard.spent * FEE <= budget + 1e-12
        assert "STOPPED" in (tmp_path / "status.log").read_text(encoding="utf-8")

    def test_ground_control_a_sufficient_budget_sends_everything(self, tmp_path, smoke_calls):
        guard = SpendGuard()
        code, seen = _run(smoke_calls, tmp_path, guard, ok_reply(lambda b: 0.00001))
        assert code == runner.EXIT_OK
        assert len(seen) == len(smoke_calls)
        rows = runner.read_ledger(tmp_path)
        assert all(r["ok"] for r in rows) and len(rows) == len(smoke_calls)

    def test_a_reply_without_usage_cost_is_booked_at_its_worst_case(self, tmp_path, smoke_calls):
        guard = SpendGuard()
        code, _ = _run(smoke_calls, tmp_path, guard, ok_reply(None))
        assert code == runner.EXIT_OK
        assert guard.spent == pytest.approx(sum(c.worst_usd for c in smoke_calls))

    def test_a_reply_with_usage_cost_is_booked_at_that_cost(self, tmp_path, smoke_calls):
        # GROUND for the case above: booking the worst case always would pass it.
        guard = SpendGuard()
        _run(smoke_calls, tmp_path, guard, ok_reply(lambda b: 0.00002))
        assert guard.spent == pytest.approx(0.00002 * len(smoke_calls))

    def test_retries_are_capped_at_ten_percent_of_the_plan(self, tmp_path, smoke_calls):
        guard = SpendGuard()
        code, seen = _run(smoke_calls, tmp_path, guard,
                          lambda r: httpx.Response(500, json={"error": {"message": "down"}}))
        cap = int(config.RETRY_FRAC * len(smoke_calls))
        assert len(seen) == len(smoke_calls) + cap
        assert code == runner.EXIT_OK

    def test_a_finished_call_is_not_sent_again(self, tmp_path, smoke_calls):
        _run(smoke_calls, tmp_path, SpendGuard(), ok_reply(lambda b: 0.00001))
        spent = runner.spent_so_far(tmp_path)
        code, seen = _run(smoke_calls, tmp_path, SpendGuard(already_spent=spent),
                          ok_reply(lambda b: 0.00001))
        assert seen == [] and code == runner.EXIT_OK


# ============================================================ key canary
def _files_containing(root: Path, needle: str) -> list[Path]:
    hits = []
    for p in root.rglob("*"):
        if p.is_file() and needle in p.read_text(encoding="utf-8", errors="replace"):
            hits.append(p)
    return hits


class TestKeyNeverLeaves:
    CANARY = "CANARYKEY" + secrets.token_hex(12)   # not sk-or- shaped: exact-match path

    def _leaky_provider(self, auth_seen):
        n = {"i": 0}

        def handler(request):
            auth_seen.append(request.headers.get("authorization"))
            n["i"] += 1
            body = json.loads(request.content)
            if n["i"] % 3 == 0:        # an error body that echoes the key
                return httpx.Response(401, json={"error": {"message": f"bad key {self.CANARY}"}})
            return httpx.Response(200, json={   # a model that repeats the key
                "choices": [{"message": {"content": f"Here it is: {self.CANARY}"}}],
                "usage": {"cost": 0.00001}, "provider": f"P {self.CANARY}",
                "model": body["model"]})
        return handler

    def test_the_key_reaches_the_provider_and_no_output(self, tmp_path, monkeypatch,
                                                        capsys, caplog):
        caplog.set_level(logging.DEBUG)
        monkeypatch.setenv(config.KEY_ENV, self.CANARY)
        monkeypatch.setattr(costs, "o200k_counter", lambda: fake_count)
        auth = []
        code = g3.main(["run", "--stage", "smoke", "--out", str(tmp_path)],
                       transport=httpx.MockTransport(self._leaky_provider(auth)))
        assert code == runner.EXIT_OK
        # POSITIVE CONTROL: the key really was in hand and really was sent.
        assert auth and all(a == f"Bearer {self.CANARY}" for a in auth)
        out, err = capsys.readouterr()
        assert self.CANARY not in out and self.CANARY not in err
        assert self.CANARY not in caplog.text
        assert _files_containing(tmp_path, self.CANARY) == []
        # ...and the ledger did record the replies, redacted.
        assert secret.REDACTED in (tmp_path / "ledger.jsonl").read_text(encoding="utf-8")

    def test_the_scanner_finds_a_planted_key(self, tmp_path):
        # POSITIVE CONTROL for the scan above: it can see a leak.
        (tmp_path / "x.log").write_text(f"oops {self.CANARY}", encoding="utf-8")
        assert _files_containing(tmp_path, self.CANARY) == [tmp_path / "x.log"]

    def test_the_writer_refuses_rather_than_writes_a_key(self, tmp_path):
        w = secret.SafeWriter(tmp_path / "a.log", secret.Redactor(self.CANARY))
        w.write(f"line with {self.CANARY}\n")
        assert self.CANARY not in (tmp_path / "a.log").read_text(encoding="utf-8")

    def test_an_openrouter_shaped_key_is_redacted_even_if_unknown(self):
        other = "sk-or-v1-" + secrets.token_hex(16)
        assert other not in secret.Redactor()(f"echo {other}")

    def test_the_key_comes_only_from_the_environment(self, monkeypatch):
        monkeypatch.delenv(config.KEY_ENV, raising=False)
        with pytest.raises(secret.MissingKey) as exc:
            secret.read_key()
        assert config.KEY_ENV in str(exc.value)
        monkeypatch.setenv(config.KEY_ENV, self.CANARY)
        assert secret.read_key() == self.CANARY

    def test_a_missing_key_refuses_before_any_send(self, tmp_path, monkeypatch):
        monkeypatch.delenv(config.KEY_ENV, raising=False)
        sent = []
        code = g3.main(["run", "--stage", "smoke", "--out", str(tmp_path)],
                       transport=httpx.MockTransport(lambda r: sent.append(r)))
        assert code == runner.EXIT_SETUP and sent == []

    def test_outputs_may_not_go_into_the_repository(self, tmp_path, monkeypatch):
        # The guard defaults to the real repository...
        assert (runner.REPO_ROOT / "backend" / "prompt_layer.py").is_file()
        # ...and is measured on a stand-in, so a broken guard creates nothing
        # inside the real tree.
        repo = tmp_path / "repo"
        repo.mkdir()
        monkeypatch.setattr(runner, "REPO_ROOT", repo)
        for inside in (repo, repo / "evals" / "run-output"):
            with pytest.raises(runner.OutDirInRepo):
                runner.check_out_dir(inside)
        assert not (repo / "evals").exists()
        # GROUND: a directory beside the repository is accepted.
        assert runner.check_out_dir(tmp_path / "outside").is_dir()


# ============================================================ max_price
class TestEveryRequestIsPriceCapped:
    def test_every_planned_body_carries_max_price_and_zdr(self, smoke_calls, full_calls):
        calls = smoke_calls + full_calls
        assert len(calls) == 10 + 542
        for c in calls:
            p = c.body["provider"]
            assert p["max_price"] == {"prompt": c.model.cap_in, "completion": c.model.cap_out}
            assert p["zdr"] is True and p["data_collection"] == "deny"
            assert p["quantizations"] == list(config.QUANTIZATIONS)
            assert c.body["max_tokens"] == c.model.max_tokens
            assert c.worst_usd > 0

    def test_the_checker_refuses_a_body_without_max_price(self, smoke_calls):
        # POSITIVE CONTROL: the checker the test above relies on can say no.
        c = smoke_calls[0]
        body = json.loads(json.dumps(c.body))
        del body["provider"]["max_price"]
        with pytest.raises(plan.UnsafeRequest):
            plan.request_is_safe(body, c.model)
        body = json.loads(json.dumps(c.body))
        body["provider"]["max_price"]["completion"] = c.model.cap_out * 2
        with pytest.raises(plan.UnsafeRequest):
            plan.request_is_safe(body, c.model)

    def test_the_runner_will_not_send_an_uncapped_body(self, tmp_path):
        calls = plan.build(plan.STAGE_SMOKE, count=fake_count)
        del calls[0].body["provider"]["max_price"]
        sent = []
        with pytest.raises(plan.UnsafeRequest):
            asyncio.run(runner.run_stage(
                plan.STAGE_SMOKE, calls[:1], tmp_path, key="k", guard=SpendGuard(),
                transport=httpx.MockTransport(lambda r: sent.append(r)), echo=False))
        assert sent == []

    def test_a_prompt_above_the_hard_cap_is_refused_not_priced(self):
        with pytest.raises(costs.PromptTooLarge):
            costs.call_ceiling(config.MODELS[0], config.PROMPT_HARD_CAP + 1)
        assert costs.call_ceiling(config.MODELS[0], config.PROMPT_HARD_CAP) > 0

    def test_the_nominal_model_still_gives_the_approved_ceiling(self):
        n = costs.nominal_ceiling()
        assert n.calls == 546
        assert round(n.total, 3) == 0.837
        assert round(n.planned, 3) == 0.652


# ============================================================ judge isolation
FAKE_CLAUDE = textwrap.dedent("""
    import json, os, sys
    here = os.path.dirname(os.path.abspath(__file__))
    argv = sys.argv[1:]
    sysfile = argv[argv.index("--system-prompt-file") + 1] if "--system-prompt-file" in argv else None
    rec = {"argv": argv, "env": dict(os.environ), "cwd": os.getcwd(),
           "cwd_listing": os.listdir("."), "stdin": sys.stdin.read(),
           "system": open(sysfile, encoding="utf-8").read() if sysfile else None}
    with open(os.path.join(here, "record.json"), "w", encoding="utf-8") as fh:
        json.dump(rec, fh)
    model = open(os.path.join(here, "model.txt")).read().strip()
    print(json.dumps({"type": "result", "is_error": False,
                      "structured_output": {"speaks_for_user": False, "quote": ""},
                      "result": "ok", "modelUsage": {model: {"outputTokens": 7}}}))
""")


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    d = tmp_path / "bin"
    d.mkdir()
    (d / "fake_claude.py").write_text(FAKE_CLAUDE, encoding="utf-8")
    (d / "model.txt").write_text("claude-opus-5-5", encoding="utf-8")
    if os.name == "nt":
        (d / "claude.cmd").write_text(
            f'@echo off\r\n"{sys.executable}" "{d / "fake_claude.py"}" %*\r\n', encoding="utf-8")
    else:
        sh = d / "claude"
        sh.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{d / "fake_claude.py"}" "$@"\n')
        sh.chmod(0o755)
    monkeypatch.setenv("PATH", str(d) + os.pathsep + os.environ.get("PATH", ""))
    work = tmp_path / "judge-tmp"
    work.mkdir()
    monkeypatch.setattr(judge, "TEMP_BASE", str(work))
    return d


class TestJudgeIsolation:
    OR_KEY = "CANARYOR" + secrets.token_hex(8)

    def _call(self, parent_env):
        return judge.call_claude("the prompt", "the system text",
                                 judge.schema("flag.schema.json"), parent_env=parent_env,
                                 secrets=(self.OR_KEY,))

    def _record(self, d):
        return json.loads((d / "record.json").read_text(encoding="utf-8"))

    def test_flags_env_and_cwd(self, fake_claude):
        parent = dict(os.environ)
        parent.update({"ANTHROPIC_API_KEY": "sk-ant-" + secrets.token_hex(8),
                       "ANTHROPIC_BASE_URL": "https://example.invalid",
                       config.KEY_ENV: self.OR_KEY, "SOME_ALIAS": self.OR_KEY})
        self._call(parent)
        rec = self._record(fake_claude)
        argv = rec["argv"]
        assert argv[:len(judge.BASE_FLAGS)] == list(judge.BASE_FLAGS)
        for flag in ("-p", "--disable-slash-commands", "--strict-mcp-config",
                     "--no-session-persistence"):
            assert flag in argv
        assert argv[argv.index("--model") + 1] == "opus"
        assert argv[argv.index("--tools") + 1] == ""
        assert json.loads(argv[argv.index("--mcp-config") + 1]) == {"mcpServers": {}}
        assert json.loads(argv[argv.index("--settings") + 1]) == {"disableAllHooks": True}
        assert argv[argv.index("--setting-sources") + 1] == "local"
        assert "--bare" not in argv
        assert json.loads(argv[argv.index("--json-schema") + 1])["required"] == [
            "speaks_for_user", "quote"]
        env = rec["env"]
        for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", config.KEY_ENV):
            assert name not in env
        assert self.OR_KEY not in json.dumps(env)
        assert env["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] == "1"
        assert env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"
        assert rec["cwd_listing"] == []
        repo = EVAL_DIR.parents[1].resolve()
        assert repo not in Path(rec["cwd"]).resolve().parents
        assert rec["system"] == "the system text" and rec["stdin"] == "the prompt"

    def test_ground_control_a_parent_without_keys(self, fake_claude):
        parent = {k: v for k, v in os.environ.items() if k not in judge.STRIP_ENV}
        self._call(parent)
        env = self._record(fake_claude)["env"]
        assert "ANTHROPIC_API_KEY" not in env
        # the rest of the environment does pass: PATH is what found the CLI
        assert env.get("PATH") or env.get("Path")

    def test_a_verdict_from_another_model_is_refused(self, fake_claude):
        (fake_claude / "model.txt").write_text("claude-haiku-4-5", encoding="utf-8")
        with pytest.raises(judge.JudgeRefused):
            self._call(dict(os.environ))

    def test_an_opus_verdict_is_accepted(self, fake_claude):
        # POSITIVE CONTROL for the refusal above.
        out = self._call(dict(os.environ))
        assert out["structured_output"] == {"speaks_for_user": False, "quote": ""}

    def test_evidence_must_be_quoted_word_for_word(self):
        reply = "She turns to the window. You nod and agree to stay."
        assert judge.verify_flag({"speaks_for_user": True, "quote": "You nod and agree"}, reply)
        assert not judge.verify_flag({"speaks_for_user": True, "quote": "You nod, agreeing"}, reply)
        assert not judge.verify_pair({"winner": "A", "reason": "", "flaws": [
            {"reply": "B", "quote": "She turns", "severity": 2}]}, reply, "Other text.")
        assert judge.verify_pair({"winner": "A", "reason": "", "flaws": [
            {"reply": "A", "quote": "She turns", "severity": 2}]}, reply, "Other text.")


# ============================================================ arms
def _system(msgs):
    assert msgs[0]["role"] == "system" and sum(m["role"] == "system" for m in msgs) == 1
    return msgs[0]["content"]


def has_layer(msgs) -> bool:
    s = _system(msgs)
    return ("<roleplay>" in s and "<craft>" in s
            and prompt_layer.LAYER["roleplay"].splitlines()[1][:60] in s)


def has_module(msgs, attack) -> bool:
    return attack in _system(msgs)


def has_limits(msgs) -> bool:
    return "[Limits #" in _system(msgs)


def is_sabotaged(msgs, user) -> bool:
    return config.SABOTAGE.replace("{{user}}", user) in _system(msgs)


class TestArmsReallyDiffer:
    ATTACK = fixtures.attack_module()
    PERSONA = fixtures.persona()
    CTX = fixtures.contexts("l")[0]

    def _msgs(self, arm):
        return build_messages(self.CTX, arm, max_tokens=400, persona=self.PERSONA,
                              attack_module=self.ATTACK)

    def test_mode_a_has_the_module_and_no_layer(self):
        m = self._msgs(config.ARM_MOD)
        assert has_module(m, self.ATTACK) and not has_layer(m) and has_limits(m)
        assert _system(m).index(self.ATTACK) < _system(m).index("[Character:")

    def test_mode_b_has_the_layer_and_no_module(self):
        m = self._msgs(config.ARM_LAYER)
        assert has_layer(m) and not has_module(m, self.ATTACK) and has_limits(m)
        assert "<reminder>" in m[-1]["content"]

    def test_mode_c_has_neither(self):
        m = self._msgs(config.ARM_OFF)
        assert not has_layer(m) and not has_module(m, self.ATTACK) and has_limits(m)
        assert "<reminder>" not in m[-1]["content"]

    def test_nolim_drops_only_the_limits(self):
        off, nolim = self._msgs(config.ARM_OFF), self._msgs(config.ARM_NOLIM)
        assert not has_limits(nolim) and not has_layer(nolim)
        assert _system(off).startswith(_system(nolim).split("\n\n[Limits #")[0])

    def test_sab_is_the_layer_plus_the_sabotage(self):
        m = self._msgs(config.ARM_SAB)
        assert has_layer(m) and is_sabotaged(m, self.PERSONA["display_name"])
        assert not is_sabotaged(self._msgs(config.ARM_LAYER), self.PERSONA["display_name"])
        assert config.SABOTAGE not in prompt_layer.LAYER["craft"]   # restored

    def test_the_card_system_prompt_goes_in_every_mode(self):
        for arm in config.ARMS:
            assert "brass pocket watch" in _system(self._msgs(arm)), arm

    def test_every_planned_body_matches_its_arm(self, full_calls):
        user = self.PERSONA["display_name"]
        want = {
            config.ARM_OFF: (False, False, True, False),
            config.ARM_LAYER: (True, False, True, False),
            config.ARM_MOD: (False, True, True, False),
            config.ARM_NOLIM: (False, False, False, False),
            config.ARM_SAB: (True, False, True, True),
        }
        seen = set()
        for c in full_calls:
            msgs = c.body["messages"]
            got = (has_layer(msgs), has_module(msgs, self.ATTACK), has_limits(msgs),
                   is_sabotaged(msgs, user))
            assert got == want[c.arm], c.call_id
            seen.add(c.arm)
        assert seen == set(config.ARMS)
