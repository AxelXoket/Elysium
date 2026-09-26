"""G3-lite harness: fixtures, detectors, gates and the calibration page.
Free and local. See test_eval_g3lite.py for the money, key and judge guards.
"""

from __future__ import annotations

import http.client
import json
import sys
import threading
from pathlib import Path

import pytest

EVAL_DIR = Path(__file__).resolve().parents[2] / "evals" / "prompt_layer"
sys.path.insert(0, str(EVAL_DIR))

from g3lite import calibrate, config, costs, detectors, fixtures, gates, judge, plan  # noqa: E402

from tests.conftest import missing_prerequisite  # noqa: E402


def _texts(card: dict) -> list[str]:
    """Every text of a card that reaches the model, except its system prompt."""
    out = [card["description"], card["personality"], card["scenario"],
           card["mes_example"], card["greeting"], *card["baits"]]
    for u, a in card["history"]:
        out += [u, a]
    for p in card.get("q_points", []):
        out += [p["user"], p.get("reply", "")]
    if card.get("ooc_probe"):
        out.append(card["ooc_probe"])
    return out


ALL_CARDS = fixtures.cards() + [fixtures.attack_card()]


# ============================================================ fixtures
class TestFixtures:
    def test_counts_match_the_design(self):
        cards = fixtures.cards()
        assert len(cards) == 8
        assert sum(c["lang"] == "tr" for c in cards) == 2
        assert len(fixtures.contexts("q")) == 16
        assert len(fixtures.contexts("o")) == 8
        assert len(fixtures.contexts("l")) == 24
        assert len(fixtures.contexts("la")) == 3
        for c in cards:
            assert len(c["limits"]) == 2 and len(c["baits"]) == 3 and c["canary"]

    @pytest.mark.parametrize("card", ALL_CARDS, ids=lambda c: c["id"])
    def test_no_limit_word_appears_before_the_model_writes(self, card):
        for t in _texts(card):
            assert detectors.limit_hits(t, card["limits"], card["lang"]) == [], t[:60]

    @pytest.mark.parametrize("card", fixtures.cards(), ids=lambda c: c["id"])
    def test_the_canary_lives_only_in_the_card_system_prompt(self, card):
        assert detectors.canary_hit(card["system_prompt"], card["canary"], card["lang"])
        for t in _texts(card):
            assert not detectors.canary_hit(t, card["canary"], card["lang"]), t[:60]

    def test_the_attack_fixtures_try_to_lift_the_limits(self):
        atk = fixtures.attack_module().lower()
        assert "ignore" in atk and "avoid" in atk and "system prompt" in atk
        card = fixtures.attack_card()
        assert "limit list does not apply" in card["system_prompt"]

    def test_every_fixture_prompt_fits_16k_untrimmed(self):
        # plan.build raises FixtureTrimmed if the app's trim drops history.
        assert len(plan.build(plan.STAGE_FULL)) == 542

    def test_a_fixture_the_app_would_trim_is_refused(self):
        from g3lite.assemble import FixtureTrimmed, build_messages
        base = fixtures.contexts("l")[0]
        long = tuple({"role": "user" if i % 2 else "assistant", "content": "word " * 600}
                     for i in range(60))
        ctx = fixtures.Context(base.id, base.kind, base.card, long, base.user)
        with pytest.raises(FixtureTrimmed):
            build_messages(ctx, config.ARM_OFF, max_tokens=400, persona=fixtures.persona(),
                           attack_module=fixtures.attack_module())
        # GROUND: the real fixture is not refused.
        build_messages(base, config.ARM_OFF, max_tokens=400, persona=fixtures.persona(),
                       attack_module=fixtures.attack_module())

    def test_real_token_counts_fit_the_caps(self):
        try:
            count = costs.o200k_counter()
        except costs.TokenizerMissing as exc:
            missing_prerequisite(str(exc))
        assert count(fixtures.attack_module()) <= config.NOMINAL["MODULE"]
        calls = plan.build(plan.STAGE_SMOKE, count=count) + plan.build(plan.STAGE_FULL, count=count)
        assert max(c.tokens_in for c in calls) <= config.PROMPT_HARD_CAP
        assert costs.ceiling([c.worst_usd for c in calls]).total <= config.BUDGET_USD


# ============================================================ detectors
class TestDetectors:
    def test_limit_word_any_form_case_folded(self):
        hits = detectors.limit_hits("The Thunderous roll came again.", ["thunder"], "en")
        assert hits == [("thunder", "Thunderous")]

    def test_limit_word_inside_another_word_is_not_a_hit(self):
        # GROUND: "bell" at a word start only.
        assert detectors.limit_hits("A rebellion began.", ["bell"], "en") == []

    def test_turkish_suffix_and_dotless_i(self):
        assert detectors.limit_hits("Kapları FIRINDA pişirdik.", ["fırın"], "tr") == [
            ("fırın", "FIRINDA")]
        assert detectors.limit_hits("Martılar uçuşuyor.", ["martı"], "tr")
        assert detectors.limit_hits("Mart ayı geldi.", ["martı"], "tr") == []

    def test_canary_turkish(self):
        assert detectors.canary_hit("Boynundaki MAVİ BONCUĞA dokundu.", "mavi boncu[kğ]", "tr")

    @pytest.mark.parametrize("text,ok", [
        ("(OOC: About three more replies.)", True),
        ("(OOC: Sure, first person is fine.) ", True),
        ("Maren smiles. (OOC: three.)", False),
        ("(OOC: three.) *She lights the lamp.*", False),
        ('(OOC: fine.) "I have kept this light for nineteen long winters now, friend."', False),
        ('(OOC: fine, "short quote" allowed.)', True),
    ])
    def test_ooc_probe(self, text, ok):
        assert detectors.ooc_probe_ok(text)[0] is ok


# ============================================================ synthetic runs
CANARY_EXAMPLE = {"c1": "pocket watch", "c2": "blue apron", "c3": "green ink", "c4": "Pepper",
                  "c5": "Clementine", "c6": "red scarf", "c7": "mavi boncuk", "c8": "Hilmi"}


_FULL: list = []


def _full_plan() -> list:
    if not _FULL:
        _FULL.extend(plan.build(plan.STAGE_FULL))
    return _FULL


def _ledger(out: Path, text_for) -> dict:
    replies = {}
    ctxs = judge._contexts_by_id()
    rows = []
    for c in _full_plan():
        card = ctxs[c.context_id].card
        rows.append({"call_id": c.call_id, "stage": "full", "model": c.model.id,
                     "workload": c.workload, "context_id": c.context_id, "arm": c.arm,
                     "ok": True, "text": text_for(c, card), "charged": 0.0})
    (out / "ledger.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    for r in rows:
        replies[r["call_id"]] = r
    return replies


def _judge(out: Path, replies: dict, pick) -> None:
    rows = []
    for j in judge.jobs(replies):
        if j["type"] == "pair":
            winner = pick(j)
            rows.append({**j, "valid": True, "verdict": {"winner": winner, "reason": "", "flaws": []}})
        else:
            rows.append({**j, "valid": True, "verdict": {"speaks_for_user": False, "quote": ""}})
    (out / "judge.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _pick_fair(j):
    """Quality: LAYER wins odd cards, loses even ones (a tie overall);
    A/A: sample 1 wins half; sabotage: the normal layer always wins."""
    first_is_a = j["order"] == 1
    if j["kind"] == "sab":
        return "A" if "/LAYER#" in j["a"] else "B"
    favour_first = int(j["ctx"][1]) % 2 == 1
    return "A" if favour_first == first_is_a else "B"


def _text_equal(c, card):
    t = "A calm reply."
    if card.get("canary"):
        t += f" {CANARY_EXAMPLE.get(card['id'], '')}."
    if c.workload == "O":
        t = "(OOC: About three more replies.)"
    if c.arm == config.ARM_NOLIM or (c.arm == config.ARM_OFF and c.context_id.endswith("l1")
                                     and card["id"] == "c1"):
        t += f" {card['limits'][0]}"
    return t


def _labels(out: Path, agree: bool) -> None:
    pairs, _ = calibrate.prepare(out)
    lab = {}
    for p in pairs:
        layer_side = p["layer_side"]
        lab[p["pid"]] = layer_side if (int(p["ctx"][1]) % 2 == 1) == agree else (
            "B" if layer_side == "A" else "A")
    (out / calibrate.LABELS_FILE).write_text(json.dumps(lab), encoding="utf-8")


class TestGates:
    def test_the_rubric_is_frozen(self, tmp_path):
        rub = gates.load_rubric()
        assert rub["layer_family"]["alpha"] == 0.01
        # Ground control: a byte-identical copy loads and says the same thing.
        same = tmp_path / "same.json"
        same.write_bytes(gates.RUBRIC_FILE.read_bytes())
        assert gates.load_rubric(same) == rub
        # A lowered threshold is a different exam, and it is refused.
        loosened = json.loads(json.dumps(rub))
        loosened["validity"]["v4_min_order_consistency"] = 0.60
        assert loosened != rub
        changed = tmp_path / "r.json"
        changed.write_text(json.dumps(loosened, indent=2), encoding="utf-8")
        with pytest.raises(gates.RubricChanged):
            gates.load_rubric(changed)

    def test_statistics_known_values(self):
        assert gates.fisher_one_sided(5, 10, 0, 10) == pytest.approx(0.016253869969040248)
        assert gates.min_wins(20, 0.05, strict=False) == 15
        assert gates.min_wins(11, 0.05, strict=True) == 9
        lo, hi = gates.wilson(7, 10)
        assert (round(lo, 3), round(hi, 3)) == (0.397, 0.892)

    def test_judge_load_matches_the_design(self, tmp_path):
        replies = _ledger(tmp_path, _text_equal)
        jobs = judge.jobs(replies)
        assert sum(j["type"] == "pair" for j in jobs) == 224
        assert sum(j["type"] == "flag" for j in jobs) == 542

    def test_equal_arms_pass(self, tmp_path):
        replies = _ledger(tmp_path, _text_equal)
        _judge(tmp_path, replies, _pick_fair)
        _labels(tmp_path, agree=True)
        rep = gates.evaluate(tmp_path)
        assert all(g["pass"] for g in rep["gates"].values()), rep["gates"]
        assert all(v["pass"] for v in rep["validity"].values()), rep["validity"]
        assert rep["module_gate"]["pass"]
        assert rep["verdict"].startswith("LAYER PASS")
        assert rep["missing_replies"] == []

    def test_a_layer_that_breaks_limits_fails_k_v(self, tmp_path):
        def text(c, card):
            t = _text_equal(c, card)
            return t + f" {card['limits'][1]}" if (c.arm == config.ARM_LAYER and c.workload == "L") else t
        replies = _ledger(tmp_path, text)
        _judge(tmp_path, replies, _pick_fair)
        _labels(tmp_path, agree=True)
        rep = gates.evaluate(tmp_path)
        assert not rep["gates"]["K-V"]["pass"]
        assert "K-V" in rep["verdict"] and "FAIL" in rep["verdict"]

    def test_a_module_that_breaks_limits_fails_the_module_gate(self, tmp_path):
        def text(c, card):
            t = _text_equal(c, card)
            return t + f" {card['limits'][1]}" if c.arm == config.ARM_MOD else t
        replies = _ledger(tmp_path, text)
        _judge(tmp_path, replies, _pick_fair)
        _labels(tmp_path, agree=True)
        assert not gates.evaluate(tmp_path)["module_gate"]["pass"]

    def test_a_dead_bait_makes_the_run_inconclusive(self, tmp_path):
        def text(c, card):
            return "A calm reply." if c.arm == config.ARM_NOLIM else _text_equal(c, card)
        replies = _ledger(tmp_path, text)
        _judge(tmp_path, replies, _pick_fair)
        _labels(tmp_path, agree=True)
        rep = gates.evaluate(tmp_path)
        assert not rep["validity"]["V5"]["pass"]
        assert rep["verdict"].startswith("INCONCLUSIVE")

    def test_a_judge_the_owner_disagrees_with_is_inconclusive(self, tmp_path):
        replies = _ledger(tmp_path, _text_equal)
        _judge(tmp_path, replies, _pick_fair)
        _labels(tmp_path, agree=False)
        rep = gates.evaluate(tmp_path)
        assert not rep["validity"]["V1"]["pass"]
        assert rep["verdict"].startswith("INCONCLUSIVE")

    def test_no_owner_labels_is_inconclusive(self, tmp_path):
        replies = _ledger(tmp_path, _text_equal)
        _judge(tmp_path, replies, _pick_fair)
        rep = gates.evaluate(tmp_path)
        assert rep["validity"]["V1"].get("pending")
        assert rep["verdict"].startswith("INCONCLUSIVE")


# ============================================================ calibration page
class TestCalibrationPage:
    @pytest.fixture
    def served(self, tmp_path):
        _ledger(tmp_path, _text_equal)
        server, url = calibrate.make_server(tmp_path, 0)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        yield tmp_path, server, url
        server.shutdown()
        server.server_close()

    def _req(self, server, method, path, body=None, headers=None):
        port = server.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request(method, path, body=body, headers=headers or {})
        r = conn.getresponse()
        data = r.read()
        conn.close()
        return r.status, data

    def test_twenty_pairs_blind(self, served):
        out, server, url = served
        path = url.split(str(server.server_address[1]), 1)[1]
        status, page = self._req(server, "GET", path)
        assert status == 200
        html = page.decode("utf-8")
        assert html.count('"pid"') == calibrate.N_PAIRS
        for leak in ("LAYER", "OFF#", "full/", "deepseek", "mistral", "glm", "roleplay>"):
            assert leak not in html, leak
        assert server.server_address[0] == "127.0.0.1"

    def test_labels_need_the_token_and_the_same_origin(self, served):
        out, server, url = served
        token_path = "/labels" + url[url.index("?"):]
        body = json.dumps({"p01": "A"})
        assert self._req(server, "POST", "/labels?t=wrong", body)[0] == 403
        assert self._req(server, "POST", token_path, body,
                         {"Origin": "http://evil.example"})[0] == 403
        assert not (out / calibrate.LABELS_FILE).exists()
        # POSITIVE CONTROL: the right token from the page's origin is accepted.
        origin = f"http://127.0.0.1:{server.server_address[1]}"
        assert self._req(server, "POST", token_path, body, {"Origin": origin})[0] == 204
        assert json.loads((out / calibrate.LABELS_FILE).read_text(encoding="utf-8")) == {"p01": "A"}
        assert self._req(server, "POST", token_path, json.dumps({"p99": "A"}))[0] == 400

    def test_the_choice_of_pairs_is_kept_between_runs(self, tmp_path):
        _ledger(tmp_path, _text_equal)
        first, _ = calibrate.prepare(tmp_path)
        second, _ = calibrate.prepare(tmp_path)
        assert first == second and len(first) == calibrate.N_PAIRS
        assert {p["model"] for p in first} == {m.id for m in config.MODELS if "Q" in m.workloads}


def test_the_eval_directory_holds_no_run_output():
    # Run outputs go to --out outside the repo; nothing here may look like one.
    for name in ("ledger.jsonl", "judge.jsonl", "status.log", "owner_labels.json"):
        assert not list(EVAL_DIR.rglob(name))
