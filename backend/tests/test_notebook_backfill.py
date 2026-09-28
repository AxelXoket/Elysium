"""IP-01: reading a chat whole, or a chosen stretch, on request.

The turn-driven reader only ever moves forward, and the first read of a chat
that already had a long history starts at the PRESENT on purpose. The sweep
button was the way back, one work unit per press, so a four-hundred-message
chat took twenty presses. A backfill job is that sweep looped until nothing
is unread (the sweep's own route is gone, decision 69 a), through the SAME
`_handle` door - the same claim, the same
running row, the same settle - with a row of its own for progress and undo,
and a lower ceiling so the live reader keeps a reserve of the day's calls.

Everything here drives the real loop: `Worker.backfill` on the module
singleton, or the four routes through the TestClient. The provider is
replaced at `openrouter.complete` and nowhere else. Every claim about money
is read from `notebook_spend` and `notebook_spend_calls`; every claim about
coverage from `first_unread_message` and `notebook_extractions`.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time

import anyio
import httpx
import pytest

import config
import database
import notebook_extract
import notebook_store as notebook
import notebook_worker
import openrouter
import vault_state
from database import get_db, set_setting

from tests.conftest import TEST_VAULT_KEY, make_character, make_chat
from tests.test_notebook_planner_budget import add_messages
from tests.test_notebook_worker import fact, seed

#: The production batch, read rather than retyped. It is overridable with
#: ELYSIUM_NOTEBOOK_EVERY_TURNS, so every `20`, `19` and `45` spelled out in
#: this file was a machine-dependent assertion waiting to happen: set the
#: variable and the suite goes red for a reason with nothing to do with
#: backfill. Every count below is derived from it, and the fixture chat is
#: sized so that THREE units cover it whatever it is.
BATCH = config.NOTEBOOK_EXTRACT_EVERY_TURNS
UNITS = 3

API = "/api/v1/notebook"
EXTRACT = "vendor/cheap"
CHAT_MODEL = "vendor/chat"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def _job_reset():
    """The job task is process-wide state on the module singleton, like the
    breaker the conftest resets. A job left running by one test would refuse
    the next with `already_running`.

    CANCELLED AND DRAINED, NOT JUST NULLED. Dropping the reference leaves
    the loop running against the `tmp_path` database the `db` fixture is
    about to tear down - the shape that produces a "database is locked" or
    an unraisable exception on a slower machine rather than here.

    THE CANCEL HAS TO BE RUN BY THE OWNING LOOP, which is why this is not
    four assignments. `Task.cancel()` is a request; a task whose loop has
    already closed never processes it, and a task living on the TestClient's
    portal loop in another thread cannot be cancelled from this one at all.
    So: ask the owning loop to do it if that loop is still running, and
    drain the task before the reference goes. Four tests in this file
    deliberately start a job and do not drain it themselves. The lifespan's
    own `notebook_worker.stop()` drains the client-driven ones on the way
    out of `TestClient.__exit__`; this is the net under the rest.
    """
    w = notebook_worker.worker
    yield
    task = w._backfill_task
    if task is not None and not task.done():
        loop = task.get_loop()
        if loop.is_closed():
            # Nothing can run its cancellation any more. Say so rather than
            # calling a method that cannot work: a cleanup that silently
            # does nothing is worse than none, because it reads as done.
            task.cancel()
        elif loop is _running_loop():
            task.cancel()
        else:
            # Another thread's loop - the TestClient portal. Hand it over
            # and wait, briefly, for it to actually unwind.
            loop.call_soon_threadsafe(task.cancel)
            deadline = time.monotonic() + 5.0
            while not task.done() and time.monotonic() < deadline:
                time.sleep(0.01)
    w._backfill_task = None
    w._backfill = None
    w._sweeping = False
    w._backfill_stop = False


def _running_loop():
    """The loop running on THIS thread, or None. `get_running_loop` raises
    outside a coroutine and this fixture is a plain function."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


@pytest.fixture
def chat(client) -> int:
    """A chat that needs THREE units at the production batch of twenty:
    forty-five seeded rows plus the character's greeting is forty-six."""
    chat_id = seed(client, count=BATCH * (UNITS - 1) + 5)
    choose(chat_id)
    return chat_id


def choose(chat_id: int, extract: str | None = EXTRACT,
           chat_model: str | None = CHAT_MODEL) -> None:
    """The four preconditions, minus the ones a test leaves out."""
    if extract is None:
        database.delete_setting(config.SETTING_NOTEBOOK_MODEL)
    else:
        set_setting(config.SETTING_NOTEBOOK_MODEL, extract)
    with get_db() as con:
        con.execute("UPDATE chats SET model_id = ? WHERE id = ?",
                    (chat_model, chat_id))


def provider(monkeypatch, *, facts=None, hold: asyncio.Event | None = None,
             entered: asyncio.Event | None = None) -> list[dict]:
    """A counting provider stub. `facts(n)` decides what call n proposes."""
    calls: list[dict] = []

    async def stub(*a, **kw):
        calls.append({"model": a[1], "prompt": a[0][1]["content"]})
        if entered is not None:
            entered.set()
        if hold is not None:
            await hold.wait()
        n = len(calls)
        proposed = facts(n) if facts else [fact(text=f"Fact number {n}.")]
        return {"id": f"gen-{n}", "choices": [{
            "finish_reason": "stop",
            "message": {"content": json.dumps({"facts": proposed})}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                      "cost": 0.0001}}

    monkeypatch.setattr(openrouter, "complete", stub)
    return calls


def spend() -> int:
    with get_db() as con:
        return notebook.spend_today(con)["calls"]


def ledger_rows() -> int:
    with get_db() as con:
        return con.execute(
            "SELECT COUNT(*) FROM notebook_spend_calls").fetchone()[0]


def unread(chat_id: int) -> int | None:
    with get_db() as con:
        return notebook.first_unread_message(con, chat_id)


def job_of(chat_id: int) -> dict | None:
    with get_db() as con:
        return notebook.job_for_chat(con, chat_id)


def job_count(chat_id: int) -> int:
    with get_db() as con:
        return con.execute(
            "SELECT COUNT(*) FROM notebook_backfill_jobs WHERE chat_id = ?",
            (chat_id,)).fetchone()[0]


def running_rows(chat_id: int) -> int:
    with get_db() as con:
        return con.execute(
            "SELECT COUNT(*) FROM notebook_extractions WHERE chat_id = ? "
            "AND status = 'running'", (chat_id,)).fetchone()[0]


def extraction_rows(chat_id: int) -> list[tuple]:
    with get_db() as con:
        return con.execute(
            "SELECT work_key, status FROM notebook_extractions "
            "WHERE chat_id = ? ORDER BY from_message_id",
            (chat_id,)).fetchall()


async def run_and_wait(chat_id: int, scope: str = "all",
                       from_id: int | None = None,
                       to_id: int | None = None) -> dict:
    w = notebook_worker.worker
    out = await w.backfill(chat_id, scope, from_id, to_id)
    await w.wait_for_backfill()
    return out


# ── T-01, T-02: one press covers the chat, and pays once ─────────────────

class TestOnePressReadsTheWholeChat:
    @pytest.mark.anyio
    async def test_scope_all_covers_everything_in_one_press(
            self, client, chat, monkeypatch) -> None:
        """T-01. The whole point: what took a press per unit is one press."""
        calls = provider(monkeypatch)
        assert unread(chat) == 0, "ground: the first message is unread"
        planned = client.get(f"{API}/extract/plan/{chat}?scope=all").json()
        assert planned["calls_planned"] > 1, (
            "ground: this chat needs more than one unit, or the loop's exit "
            "is never exercised")

        out = await run_and_wait(chat)

        assert out["started"] is True
        assert out["calls_planned"] == planned["calls_planned"]
        assert unread(chat) is None, "something was left unread"
        row = job_of(chat)
        assert row["state"] == "done"
        assert row["spent_calls"] == len(calls) == spend() == planned["calls_planned"]

    @pytest.mark.anyio
    async def test_the_same_range_is_never_billed_twice(
            self, client, chat, monkeypatch) -> None:
        """T-02. Read from the money tables with `==`, not from the stub."""
        calls = provider(monkeypatch)
        before = spend()

        out = await run_and_wait(chat)

        assert spend() == before + out["calls_planned"] == len(calls)
        assert ledger_rows() == out["calls_planned"]
        rows = extraction_rows(chat)
        assert [status for _, status in rows] == ["done"] * out["calls_planned"]
        assert len({key for key, _ in rows}) == len(rows), "a work key repeated"

    def test_the_refusal_vocabulary_is_ten(self) -> None:
        """The floor for the panel's sentence table until T-55 (IP-10) ties
        the two sides through a shared file: the five the sweep always had
        plus the five this unit adds (`model_list_unavailable` is the run
        route's word for a provider list it could not fetch)."""
        # THE WHOLE SET, not a subset and not a count. Both of those stay
        # green when a token is renamed, which is five of the ten this
        # asserts nothing about. The two-way tie to the sentence table is
        # T-55 and belongs to IP-10; this is the half that lives here.
        assert set(notebook_worker.SWEEP_REFUSALS) == {
            "nothing_unread", "already_running", "already_in_flight",
            "chat_paused", "chat_stopped",
            "not_ready", "daily_cap_reached", "model_not_extractable",
            "model_list_unavailable", "nothing_running"}

    def test_the_blocking_vocabulary_is_the_six_the_route_promises(
            self) -> None:
        """Section 5.2 writes `blocked_by` out in full - `no_character`,
        `no_chat_model`, `no_extract_model`, `model_not_extractable`,
        `nothing_unread` - and the panel's own table is walked against
        them on the other side; `model_list_unavailable` is the sixth, the
        provider's list out of reach for a bound model. Nothing held THIS
        side: a token added here without a sentence reaches a reader as "it
        is blocked",
        which is the shape section 5.0 forbids, and the front-side walk
        cannot see it because it only knows its own keys.

        AN ORDERED EQUALITY, because the order is load-bearing too:
        `_refusal_for` answers with the FIRST match, so moving
        `nothing_unread` above `no_character` would change what a chat with
        neither a character nor anything unread is told.
        """
        assert [token for token, _ in notebook_worker._BLOCKER_REFUSALS] == [
            "no_character", "no_chat_model", "no_extract_model",
            "model_not_extractable", "model_list_unavailable",
            "nothing_unread"]
        # And every refusal they collapse into is a word the panel has a
        # sentence for, which is what makes the press's answer renderable.
        assert {reason for _, reason in notebook_worker._BLOCKER_REFUSALS} \
            <= set(notebook_worker.SWEEP_REFUSALS)


# ── T-03: stop lands between units ────────────────────────────────────────

class TestStop:
    @pytest.mark.anyio
    async def test_stop_prevents_the_next_unit_and_leaves_no_running_row(
            self, client, chat, monkeypatch) -> None:
        """T-03. The unit in flight is paid for and finishes; the next one
        never starts."""
        hold, entered = asyncio.Event(), asyncio.Event()
        calls = provider(monkeypatch, hold=hold, entered=entered)
        w = notebook_worker.worker

        out = await w.backfill(chat, "all")
        assert out["started"] is True
        await asyncio.wait_for(entered.wait(), 5)

        stopped = client.post(f"{API}/extract/stop/{chat}").json()
        assert stopped == {"stopped": True, "job_id": out["job_id"],
                           "calls_spent": 1}
        hold.set()
        await w.wait_for_backfill()

        assert len(calls) == 1, "a second unit started after the stop"
        row = job_of(chat)
        assert row["state"] == "stopped" and row["last_error"] is None
        assert row["spent_calls"] == spend() == 1
        assert running_rows(chat) == 0, "a half-finished running row was left"
        assert unread(chat) is not None, "ground: there was more to read"

    @pytest.mark.anyio
    async def test_stop_with_nothing_running_is_a_refusal(self, client, chat):
        r = client.post(f"{API}/extract/stop/{chat}")
        assert r.status_code == 200
        assert r.json() == {"stopped": False, "reason": "nothing_running"}


# ── T-04: the job's own ceiling ───────────────────────────────────────────

class TestTheJobsOwnCeiling:
    @pytest.mark.anyio
    async def test_the_job_stops_at_its_own_cap_and_the_preview_said_so(
            self, client, monkeypatch) -> None:
        """T-04. Cap = reserve + 2, so the job may make two calls and the
        live reader keeps the reserve. Enough units that a job passed the
        whole ceiling would use all of it (the second mutation's ground)."""
        reserve = config.NOTEBOOK_LIVE_RESERVE
        monkeypatch.setattr(config, "NOTEBOOK_DAILY_CALL_CAP", reserve + 2)
        chat_id = seed(client, count=BATCH * (reserve + 3))
        choose(chat_id)
        calls = provider(monkeypatch)

        preview = client.get(f"{API}/extract/plan/{chat_id}?scope=all").json()
        assert preview["backfill_cap"] == config.NOTEBOOK_DAILY_CALL_CAP - reserve
        # ONE NAME, ONE NUMBER, on both routes the panel renders together.
        # `daily_cap` used to mean the day's ceiling here and the job's
        # ceiling there, so the panel said "3 of 60" beside "3 of 50".
        assert preview["daily_cap"] == config.NOTEBOOK_DAILY_CALL_CAP
        assert client.get(f"{API}/worker?chat_id={chat_id}").json()[
            "daily_cap"] == preview["daily_cap"]
        assert preview["calls_spent_today"] == spend() == 0
        assert preview["fits_today"] is False, (
            "the preview must say the cap will be hit BEFORE the press")

        await run_and_wait(chat_id)

        row = job_of(chat_id)
        assert row["state"] == "stopped"
        assert row["last_error"] == "daily_cap_reached"
        assert spend() == len(calls) == preview["backfill_cap"]
        # Asked before planning, every turn: no unit was planned, claimed and
        # refused at the ceiling, so no skip row was written for it.
        with get_db() as con:
            stats = notebook.extraction_stats(con, chat_id)
        assert stats["skipped"] == 0

        # GROUND: the reserve is real. The live reader, on another chat,
        # still gets a call.
        other = seed(client, count=BATCH + 10)
        choose(other)
        await notebook_worker.worker._handle(other)
        assert spend() == preview["backfill_cap"] + 1, (
            "the live reader had no call left: the job spent the reserve")

    @pytest.mark.anyio
    async def test_the_live_reader_spending_between_plan_and_claim_is_caught(
            self, client, chat, monkeypatch) -> None:
        """KARAR-K's ceiling at the moment it is CLAIMED, not a turn earlier.

        The loop asks `spent >= cap` before it plans, and planning decrypts
        a batch - so the live reader has a real window in which to spend
        the last of the job's ceiling while the job is still working out
        what to send. Only the claim itself closes that window, and the
        claim closes it only because the job's own lower ceiling is passed
        down to it. Handing `_claim_one` no ceiling leaves the job claiming
        against the WHOLE day, which is the reserve gone, one call at a
        time, with every pre-turn check green.

        Driven by spending between the plan and the claim, which is the
        only place the difference shows.
        """
        reserve = config.NOTEBOOK_LIVE_RESERVE
        monkeypatch.setattr(config, "NOTEBOOK_DAILY_CALL_CAP", reserve + 2)
        cap = notebook_worker._backfill_cap()
        assert cap == 2, "ground: the job may make two calls and no more"
        calls = provider(monkeypatch)

        real_plan = notebook_worker._plan_work
        raced: list[int] = []

        def plan_then_race(*a, **kw):
            out = real_plan(*a, **kw)
            if out is not None and not raced:
                raced.append(1)
                # THE LIVE READER, between the plan and the claim: it takes
                # the job's whole ceiling for itself, against the day's.
                with get_db() as con:
                    for _ in range(cap):
                        notebook.claim_call(con, config.NOTEBOOK_DAILY_CALL_CAP)
            return out

        monkeypatch.setattr(notebook_worker, "_plan_work", plan_then_race)

        await run_and_wait(chat, "all")

        assert raced, "the race never happened, so this proves nothing"
        row = job_of(chat)
        assert row["state"] == "stopped"
        assert row["last_error"] == "daily_cap_reached"
        assert calls == [], "the job claimed past its own ceiling and sent"
        assert spend() == cap, "and it billed on top of the live reader"
        assert row["spent_calls"] == 0

    @pytest.mark.anyio
    async def test_GROUND_the_same_job_without_the_race_reads(
            self, client, chat, monkeypatch) -> None:
        """Without the interleaved spend the identical fixture reads, so
        the refusal above is the claim and not a job that cannot start."""
        reserve = config.NOTEBOOK_LIVE_RESERVE
        monkeypatch.setattr(config, "NOTEBOOK_DAILY_CALL_CAP", reserve + 2)
        calls = provider(monkeypatch)

        await run_and_wait(chat, "all")

        assert len(calls) == notebook_worker._backfill_cap() == 2
        assert spend() == 2

    @pytest.mark.anyio
    async def test_a_cap_already_spent_refuses_without_a_job_row(
            self, client, chat, monkeypatch) -> None:
        reserve = config.NOTEBOOK_LIVE_RESERVE
        monkeypatch.setattr(config, "NOTEBOOK_DAILY_CALL_CAP", reserve + 1)
        provider(monkeypatch)
        with get_db() as con:
            notebook.claim_call(con, reserve + 1)

        r = client.post(f"{API}/extract/run/{chat}", json={"scope": "all"})

        assert r.status_code == 200
        assert r.json() == {"started": False, "reason": "daily_cap_reached"}
        assert job_count(chat) == 0


class TestThePreviewSaysTheNumberBeforeAnythingIsSent:
    """SECURITY.md promises the panel says how many calls a read will take
    before you start it, and that promise is what makes the ceiling
    meaningful: a number after the fact is a receipt, not a choice.

    Registered as that sentence's proof, so it has to measure the number and
    the silence together. It used to be registered against the refusal test
    next door, which never asks the preview anything and stays green if the
    preview stopped reporting the number at all.
    """

    @pytest.mark.anyio
    async def test_the_plan_route_reports_it_and_spends_nothing(
            self, db, client, chat, monkeypatch) -> None:
        calls = provider(monkeypatch)
        before = spend()

        plan = client.get(f"{API}/extract/plan/{chat}?scope=all").json()

        assert plan["calls_planned"] >= 1, plan
        assert plan["ready"] is True, plan
        # The silence half: asking what it costs costs nothing.
        assert calls == [], "the preview reached the provider"
        assert spend() == before, "the preview spent a call"
        assert job_count(chat) == 0, "the preview wrote a job row"

        # GROUND CONTROL: the number is the job's, not a decoration. Running
        # it bills exactly what the preview said it would.
        out = await run_and_wait(chat, "all")

        assert out["calls_planned"] == plan["calls_planned"], (out, plan)
        assert len(calls) == plan["calls_planned"], (
            f"the preview said {plan['calls_planned']} calls and "
            f"{len(calls)} were made")
        assert spend() == before + plan["calls_planned"]


# ── T-05: the precondition gate ───────────────────────────────────────────

class TestThePreconditionGate:
    def test_all_four_present_starts(self, client, chat, monkeypatch) -> None:
        """POSITIVE CONTROL first: a gate that refuses everything satisfies
        the three tests below."""
        provider(monkeypatch)
        r = client.post(f"{API}/extract/run/{chat}", json={"scope": "next"})
        assert r.status_code == 200
        assert r.json()["started"] is True

    @pytest.mark.parametrize("missing,token", [
        ("chat_model", "no_chat_model"),
        ("extract_model", "no_extract_model"),
        ("character", "no_character"),
    ])
    def test_a_missing_precondition_is_not_ready_and_costs_nothing(
            self, client, chat, monkeypatch, missing, token) -> None:
        """T-05. HTTP 200, the exact body, no call, no money, no job row,
        and the preview names the same blocker."""
        calls = provider(monkeypatch)
        if missing == "chat_model":
            choose(chat, chat_model=None)
        elif missing == "extract_model":
            choose(chat, extract=None)
        else:
            with get_db() as con:
                con.execute("PRAGMA foreign_keys = OFF")
                con.execute("DELETE FROM characters WHERE id = "
                            "(SELECT character_id FROM chats WHERE id = ?)",
                            (chat,))

        preview = client.get(f"{API}/extract/plan/{chat}?scope=all").json()
        assert preview["ready"] is False
        assert token in preview["blocked_by"]

        r = client.post(f"{API}/extract/run/{chat}", json={"scope": "all"})

        assert r.status_code == 200, r.text
        assert r.json() == {"started": False, "reason": "not_ready"}
        assert calls == [] and spend() == 0
        assert job_count(chat) == 0

    def test_the_fourth_precondition_is_the_chat_itself(
            self, client, chat, monkeypatch) -> None:
        """T-05's fourth leg. The other three are settings that can be
        missing; this one is the chat. It is not `not_ready` - a chat that
        is not there cannot be described to a reader as a setting they
        forgot - so it is the same 404 the preview gives, and it spends
        nothing and writes no row either way."""
        calls = provider(monkeypatch)
        missing = 999999
        assert client.get(f"{API}/extract/plan/{missing}?scope=all"
                          ).status_code == 404, "ground: the preview agrees"

        r = client.post(f"{API}/extract/run/{missing}", json={"scope": "all"})

        assert r.status_code == 404, r.text
        assert r.json()["detail"] == "chat_not_found"
        assert calls == [] and spend() == 0
        with get_db() as con:
            assert con.execute(
                "SELECT COUNT(*) FROM notebook_backfill_jobs").fetchone()[0] == 0
        # And the refusal handed the claim back: the next press on a real
        # chat is not told another job is running.
        assert notebook_worker.worker._sweeping is False
        assert client.post(f"{API}/extract/run/{chat}",
                           json={"scope": "next"}).json()["started"] is True


# ── T-06: the persistent binding ──────────────────────────────────────────

class TestTheChatModelBinding:
    @pytest.mark.anyio
    async def test_it_follows_the_chats_current_model(
            self, client, chat, monkeypatch) -> None:
        """T-06. Resolved on every read, not once at save time."""
        calls = provider(monkeypatch)
        r = client.post(f"{API}/extract/settings",
                        json={"model_id": notebook_extract.CHAT_MODEL_BINDING})
        assert r.status_code == 200, r.text
        assert client.get(f"{API}/extract/settings").json()["model_id"] == \
            notebook_extract.CHAT_MODEL_BINDING, "stored verbatim, not resolved"
        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/a")
        # BOTH MODELS ARE ON THE FILTERED LIST, because this row is about
        # WHICH model the binding resolves to and not about whether that
        # model can answer. A bound model the list does not carry is
        # refused without a call now (see the class below), so leaving the
        # list empty would make this pass or fail for the other reason
        # entirely.
        _list_carries("vendor/a", "vendor/b")

        await run_and_wait(chat, "next")
        assert [c["model"] for c in calls] == ["vendor/a"]

        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/b")
        await run_and_wait(chat, "next")
        assert [c["model"] for c in calls] == ["vendor/a", "vendor/b"], (
            "the binding was resolved once and frozen")

    def test_none_keeps_its_meaning(self, client, chat) -> None:
        """GROUND: a bound setting on a chat with no model is None - nothing
        to send with - and no chat at all is None too."""
        set_setting(config.SETTING_NOTEBOOK_MODEL,
                    notebook_extract.CHAT_MODEL_BINDING)
        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING, chat_model=None)
        assert notebook_extract.extract_model(chat) is None
        assert notebook_extract.extract_model() is None
        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/a")
        assert notebook_extract.extract_model(chat) == "vendor/a"

    def test_the_marker_is_the_shared_one(self) -> None:
        from pathlib import Path
        contract = json.loads((Path(__file__).resolve().parents[2] / "shared"
                               / "notebook_chat_model_binding.json")
                              .read_text(encoding="utf-8"))
        assert notebook_extract.CHAT_MODEL_BINDING == contract["model_id"]


# ── T-07: a chat model without strict schema support ──────────────────────

def _cache_model(model_id: str, params: list[str]) -> None:
    """Teach this process what the catalogue says about one model.

    ADDITIVE, because the gate now fails closed on a model nothing here
    has heard of and a bound chat can move between two models inside one
    test. Replacing the entry made the second model unknown, which reads
    as "this test is about a model that cannot answer" when it is not.
    The conftest's `_model_cache_reset` still empties it between tests, so
    nothing leaks from one to the next.
    """
    entry = openrouter._model_cache.setdefault(
        "user", {"fetched_at": time.monotonic(), "data": {"models": []}})
    models = [m for m in entry["data"]["models"] if m.get("id") != model_id]
    models.append({"id": model_id, "supported_parameters": params})
    entry["data"]["models"] = models


def _list_carries(*model_ids: str) -> None:
    """Teach this process the FILTERED list, the way a fetch leaves it.

    `_cache_model` above fills the general catalogue, which is a different
    cache answering a weaker question: `structured_outputs` alone, where
    this list is zero-retention AND `structured_outputs`. For a model the
    chat is BOUND to, this list is the only evidence the gate takes, so a
    row that wants such a model read has to put it here.

    Additive for the same reason `_cache_model` is: a bound chat can move
    between two models inside one row. The conftest's `_model_cache_reset`
    calls `invalidate_model_cache`, which drops `_zdr_cache` too, so
    nothing here leaks into the next test.
    """
    rows = list(openrouter.cached_extraction_models() or [])
    rows = [r for r in rows if r.get("id") not in model_ids]
    rows += [{"id": mid, "prompt_price": 0.5, "provider": "p",
              "context_length": 8000, "endpoints": 1} for mid in model_ids]
    openrouter._zdr_cache = (time.monotonic(), rows)


def _unreachable(monkeypatch) -> list[int]:
    """The provider cannot be asked. Counted, not just stubbed: the rows
    that use it assert the reader TRIED, which is the half that keeps a
    refusal from being a process that never looked - and the rows about
    how OFTEN it tries read the length of this list."""
    tried: list[int] = []

    async def fail():
        tried.append(1)
        raise openrouter.OpenRouterError("openrouter_unreachable")

    monkeypatch.setattr(openrouter, "fetch_extraction_models", fail)
    return tried


def _wire(monkeypatch, *, serves: tuple[str, ...] = ()) -> list[tuple]:
    """Record what would go on the wire, at the transport itself.

    `openrouter.get_client` is the seam every request in that module dials
    through, so the real `fetch_extraction_models` runs end to end above
    it: a row that stubbed the fetch would prove only that we did not call
    ourselves, which is not what "nothing left the machine" means.

    HEADER NAMES, never values. The point of the rows below is that
    `Authorization` would have ridden along; a test printing a key, even a
    fixture's fake one, is a habit with no upside.

    With `serves` the recorder answers as the provider would, so the same
    seam carries the positive control: the door reaches the transport, the
    list comes back, and the read proceeds.
    """
    seen: list[tuple] = []

    class _Recorder:
        async def get(self, url, headers=None, timeout=None):
            seen.append((url, sorted(headers or {})))
            if not serves:
                raise httpx.ConnectError("no host is dialled in this suite")
            return httpx.Response(200, json={"data": [
                {"model_id": mid,
                 "supported_parameters": ["structured_outputs"],
                 "pricing": {"prompt": "0.0000001"},
                 "provider_name": "p", "context_length": 8000}
                for mid in serves]})

    monkeypatch.setattr(openrouter, "get_client", lambda: _Recorder())
    return seen


@pytest.fixture
def gate_armed():
    """`proxy_required` armed with no proxy URL stored: `proxy_missing`,
    the state every outbound path in this app refuses outright.

    The verdict is cached in a PROCESS global rather than in the per-test
    database, so it is the one piece of state here that would outlive the
    test that set it. Cleared on the way in and on the way out, in a
    teardown rather than a trailing line, because teardown runs on the way
    out of a failure too.
    """
    import proxy_health

    set_setting("proxy_required", "1")
    proxy_health.invalidate_health_cache()
    yield
    set_setting("proxy_required", "0")
    proxy_health.invalidate_health_cache()


class TestAModelThatCannotAnswerInSchema:
    @pytest.mark.parametrize("params", [
        ["temperature"],
        ["response_format"],      # not enough: json_object is not a schema
        # THE CATALOGUE SAYING YES IS STILL NO, and this is the case that
        # was read and billed while the locked document said it was
        # refused. The filtered list is zero-retention AND
        # `structured_outputs`; this arm is `structured_outputs` by
        # itself, and for a bound model it does not decide anything.
        ["structured_outputs", "temperature"],
    ])
    def test_it_is_refused_without_a_call(
            self, client, chat, monkeypatch, params) -> None:
        """T-07."""
        calls = provider(monkeypatch)
        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/nojson")
        _cache_model("vendor/nojson", params)
        # The list IS known here and does not carry this model, so the
        # refusal below is the list answering and not a cold process that
        # never asked - the class after this one is that other state.
        _list_carries("vendor/json")

        # THE PREVIEW SAYS IT FIRST, which is the half that was missing.
        # `model_not_extractable` was asserted as a refusal REASON three
        # times and never as a `blocked_by` token, so dropping it from the
        # preview left the strip offering Start on a chat it cannot read -
        # the press is still refused and the money is safe, but the panel
        # has told the reader the opposite of what it knows.
        preview = client.get(f"{API}/extract/plan/{chat}?scope=all").json()
        assert preview["ready"] is False
        assert "model_not_extractable" in preview["blocked_by"]

        r = client.post(f"{API}/extract/run/{chat}", json={"scope": "all"})

        assert r.json() == {"started": False, "reason": "model_not_extractable"}
        assert calls == [] and spend() == 0
        assert job_count(chat) == 0

    @pytest.mark.anyio
    async def test_a_model_that_can_is_read(self, client, chat, monkeypatch):
        """POSITIVE CONTROL: the same shape with the model on the list."""
        calls = provider(monkeypatch)
        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/json")
        _list_carries("vendor/json")

        # GROUND for the row above: the same preview on a model that CAN
        # answer names no blocker at all, so the token there is the model
        # and not the preview refusing everything.
        ok = client.get(f"{API}/extract/plan/{chat}?scope=all").json()
        assert ok["ready"] is True and ok["blocked_by"] == []

        out = await run_and_wait(chat, "next")

        assert out["started"] is True
        assert len(calls) == 1 and spend() == 1

    @pytest.mark.anyio
    async def test_the_turn_driven_reader_is_gated_by_the_same_check(
            self, client, chat, monkeypatch) -> None:
        """T-07 / R-5 on the OTHER door, which is where the money was.

        R-5 says such a model is refused "cagri yapmadan". The check sat at
        one entry point: the job route asked it and refused for free, and
        the turn-driven reader beside it asked nothing. That was survivable
        while the extraction model was picked off a list of models that can
        all answer - and this unit ended that, because `CHAT_MODEL_BINDING`
        resolves to whatever the CHAT is using, a model chosen for
        conversation and never for this, and the SAME resolution feeds both
        doors.

        Measured: the job route refuses for nothing, and then the ordinary
        reader is driven over the same chat and bills nothing either.
        """
        calls = provider(monkeypatch)
        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/nojson")
        _cache_model("vendor/nojson", ["temperature"])
        _list_carries("vendor/json")
        w = notebook_worker.Worker()

        refused = await w.backfill(chat, "all")

        assert refused == {"started": False,
                           "reason": "model_not_extractable"}
        assert calls == [] and spend() == 0, "ground: the job door is free"

        # THE OTHER DOOR, the one a turn walks through on its own.
        await w._handle(chat, min_new=1, after_id=0)

        assert calls == [], (
            "the turn-driven reader called a model the job route refuses, "
            "under a binding that resolves to the same model for both")
        assert spend() == 0, "and it billed for it"
        assert notebook.list_entries(chat) == []

    @pytest.mark.anyio
    async def test_the_refusal_is_named_once_per_chat_and_model(
            self, client, chat, monkeypatch) -> None:
        """3.8. The turn-driven reader refused this model without a call and
        without a word: no row, no counter, and the Background notes card
        said nothing, so the notes simply stopped. It now writes ONE named
        skip row, `model_not_extractable`, for each pair of this chat and
        this model - not one per turn, because it is a standing condition
        and not an event.

        The chat is long and has never been read, so the ordinary reader
        plans its NEWEST twenty, and every turn below moves that window: a
        row keyed on the stretch would be a new row every turn. Ten turns
        with one model are one row; a second unfit model is a second.
        """
        calls = provider(monkeypatch)
        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/nojson")
        _cache_model("vendor/nojson", ["temperature"])
        _cache_model("vendor/nojson2", ["temperature"])
        _list_carries("vendor/json")
        w = notebook_worker.Worker()

        def named() -> int:
            with get_db() as con:
                return con.execute(
                    "SELECT COUNT(*) FROM notebook_extractions "
                    "WHERE chat_id = ? AND status = 'skipped' "
                    "AND skip_reason = 'model_not_extractable'",
                    (chat,)).fetchone()[0]

        outcomes = []
        for turn in range(10):
            add_messages(chat, [f"turn {turn} question", f"turn {turn} reply"])
            outcomes.append(await w._handle(chat))

        assert outcomes[0] == "model_not_extractable", (
            "ground: the first turn that would have read something is "
            "refused by name")
        assert named() == 1, (
            f"{named()} rows for one chat and one model over ten turns")
        assert calls == [] and spend() == 0, "and nothing was sent or spent"
        # What the Background notes card reads for THIS chat: the sentence,
        # scoped to it, and no count (3.8; the chat-scoped class below
        # holds the other chats and the model switch).
        body = client.get(f"{API}/worker?chat_id={chat}").json()
        assert body["chat_refusal"] == "model_not_extractable"

        # A DIFFERENT MODEL IS A DIFFERENT FACT, and is said again.
        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/nojson2")
        add_messages(chat, ["another question", "another reply"])
        await w._handle(chat)
        assert named() == 2

        # POSITIVE CONTROL: a model that can write notes is read, and adds
        # no refusal row, so the count above is about the unfit models and
        # not about this path writing a row for everything.
        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/json")
        add_messages(chat, ["a third question", "a third reply"])
        assert await w._handle(chat) == "settled"
        assert len(calls) == 1 and named() == 2

    @pytest.mark.anyio
    async def test_the_pair_is_said_again_after_the_model_was_fit(
            self, client, chat, monkeypatch) -> None:
        """The memory is of the pair as it stands. A chat that went back to
        a fit model and then to the unfit one again is told again: that is
        news, not the same fact repeated."""
        provider(monkeypatch)
        _cache_model("vendor/nojson", ["temperature"])
        _list_carries("vendor/json")
        w = notebook_worker.Worker()

        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/nojson")
        assert await w._handle(chat) == "model_not_extractable"
        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/json")
        assert await w._handle(chat) == "settled"
        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/nojson")
        add_messages(chat, [f"later {i}" for i in range(BATCH)])
        assert await w._handle(chat) == "model_not_extractable"
        # And said means WRITTEN: the second refusal of the pair is a second
        # row, not only a return value.
        with get_db() as con:
            rows = con.execute(
                "SELECT COUNT(*) FROM notebook_extractions WHERE chat_id = ? "
                "AND skip_reason = 'model_not_extractable'",
                (chat,)).fetchone()[0]
        assert rows == 2

    @pytest.mark.anyio
    async def test_a_turn_on_a_fit_model_that_reads_nothing_still_forgets(
            self, client, chat, monkeypatch) -> None:
        """The text said the pair is said again "after a fit read in
        between". The code forgets on a TURN whose model passes the gate,
        before planning or the cap, so a fit turn that reads nothing (the
        day's cap spent here: no call, no read row) forgets too, and going
        back to the unfit model is a second row. The row above only covered
        a fit turn that read, so the "read" condition was never tested."""
        calls = provider(monkeypatch)
        _cache_model("vendor/nojson", ["temperature"])
        _list_carries("vendor/json")
        w = notebook_worker.Worker()

        def rows(where: str) -> int:
            with get_db() as con:
                return con.execute(
                    "SELECT COUNT(*) FROM notebook_extractions "
                    f"WHERE chat_id = ? AND {where}", (chat,)).fetchone()[0]

        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/nojson")
        assert await w._handle(chat) == "model_not_extractable"
        assert rows("skip_reason = 'model_not_extractable'") == 1

        with get_db() as con:
            while (notebook.spend_today(con)["calls"]
                   < config.NOTEBOOK_DAILY_CALL_CAP):
                notebook.claim_call(con, config.NOTEBOOK_DAILY_CALL_CAP)
        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/json")
        # GROUND: this fit turn read nothing. No call went out and no
        # done, failed or running row was written, so what follows cannot
        # be explained by a read.
        assert await w._handle(chat) == "notebook_daily_cap_reached"
        assert calls == []
        assert rows("status IN ('done', 'failed', 'running')") == 0

        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/nojson")
        add_messages(chat, [f"later {i}" for i in range(BATCH)])
        assert await w._handle(chat) == "model_not_extractable", (
            "a fit turn that read nothing did not forget the pair")
        assert rows("skip_reason = 'model_not_extractable'") == 2
        assert calls == []

    @pytest.mark.anyio
    async def test_a_fit_turn_with_too_few_new_messages_still_forgets(
            self, client, chat, monkeypatch) -> None:
        """The other arm of "read or not": a fit turn whose plan is
        empty because fewer new messages arrived than the turn asks for. It
        plans nothing and calls nothing, and still forgets the pair, so
        going back to the unfit model is said again."""
        calls = provider(monkeypatch)
        _cache_model("vendor/nojson", ["temperature"])
        _list_carries("vendor/json")
        w = notebook_worker.Worker()

        def rows(where: str) -> int:
            with get_db() as con:
                return con.execute(
                    "SELECT COUNT(*) FROM notebook_extractions "
                    f"WHERE chat_id = ? AND {where}", (chat,)).fetchone()[0]

        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/nojson")
        assert await w._handle(chat) == "model_not_extractable"
        assert rows("skip_reason = 'model_not_extractable'") == 1

        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/json")
        # GROUND: this fit turn planned nothing. More new messages are
        # asked for than the chat has, so no plan, no call and no row of
        # any kind; what follows cannot be a read or a cap row.
        before = rows("1 = 1")
        assert await w._handle(chat, min_new=10 ** 6) is None
        assert calls == [] and rows("1 = 1") == before

        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/nojson")
        add_messages(chat, [f"later {i}" for i in range(BATCH)])
        assert await w._handle(chat) == "model_not_extractable", (
            "a fit turn that planned nothing did not forget the pair")
        assert rows("skip_reason = 'model_not_extractable'") == 2
        assert calls == []

    @pytest.mark.anyio
    @pytest.mark.parametrize("stop", ["breaker", "no_model"])
    async def test_a_turn_stopped_before_the_check_forgets_nothing(
            self, client, chat, monkeypatch, stop) -> None:
        """Only a turn that reaches the check and finds the model fit
        forgets. A turn stopped before it (here a stopped breaker, or no
        model to check) has checked nothing, so going back to the unfit
        model is not news and writes no second row. POSITIVE CONTROL at
        the end: the same return after a fit turn that did reach the check
        is said again, so the silence is the stop and not a pair that is
        never said twice."""
        calls = provider(monkeypatch)
        _cache_model("vendor/nojson", ["temperature"])
        _list_carries("vendor/json")
        w = notebook_worker.Worker()

        def named() -> int:
            with get_db() as con:
                return con.execute(
                    "SELECT COUNT(*) FROM notebook_extractions "
                    "WHERE chat_id = ? "
                    "AND skip_reason = 'model_not_extractable'",
                    (chat,)).fetchone()[0]

        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/nojson")
        assert await w._handle(chat) == "model_not_extractable"
        assert named() == 1

        if stop == "breaker":
            choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
                   chat_model="vendor/json")
            w.breaker.stopped = True
            assert await w._handle(chat) is None
            # GROUND: the turn was stopped by the breaker, not by the gate.
            assert w.refused_by_breaker == 1
            w.breaker.reset()
        else:
            choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
                   chat_model=None)
            assert notebook_extract.extract_model(chat) is None, (
                "ground: there is no model for this turn to check")
            assert await w._handle(chat) is None
        assert calls == []

        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/nojson")
        add_messages(chat, [f"later {i}" for i in range(BATCH)])
        assert await w._handle(chat) is None, (
            "a turn stopped before the check forgot the pair")
        assert named() == 1

        # POSITIVE CONTROL: a fit turn that reaches the check forgets.
        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/json")
        assert await w._handle(chat) == "settled"
        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/nojson")
        add_messages(chat, [f"after {i}" for i in range(BATCH)])
        assert await w._handle(chat) == "model_not_extractable"
        assert named() == 2

    @pytest.mark.anyio
    async def test_two_unfit_models_alternated_are_two_rows_not_six(
            self, client, chat, monkeypatch) -> None:
        """The memory is keyed on the (chat, model) PAIR. It held one model
        per chat, so two unfit models taken in turns were a new row at every
        switch: six turns, six rows, for two facts."""
        calls = provider(monkeypatch)
        _cache_model("vendor/nojson", ["temperature"])
        _cache_model("vendor/nojson2", ["temperature"])
        _list_carries("vendor/json")
        w = notebook_worker.Worker()

        said = []
        for turn in range(6):
            model = "vendor/nojson" if turn % 2 == 0 else "vendor/nojson2"
            choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
                   chat_model=model)
            add_messages(chat, [f"q{turn}", f"r{turn}"])
            said.append(await w._handle(chat))

        # GROUND: each model was refused by name the first time it came up.
        assert said[:2] == ["model_not_extractable"] * 2
        assert said[2:] == [None] * 4, said
        with get_db() as con:
            rows = con.execute(
                "SELECT COUNT(*) FROM notebook_extractions WHERE chat_id = ? "
                "AND skip_reason = 'model_not_extractable'",
                (chat,)).fetchone()[0]
        assert rows == 2, f"{rows} rows for two chat-and-model pairs"
        assert calls == [] and spend() == 0

    def test_the_memory_of_what_was_said_is_bounded(self) -> None:
        """`UNFIT_SAID_MAX` pairs and no more: both halves of the key come
        from the user. The oldest pair goes first and is said again; the
        newest are still remembered."""
        w = notebook_worker.Worker()
        cap = notebook_worker.UNFIT_SAID_MAX
        for i in range(cap + 1):
            w._remember_unfit(i, "vendor/nojson", "model_not_extractable")

        assert len(w._unfit_said) == cap
        # POSITIVE: the newest pair is remembered, so a miss below is the
        # bound and not a memory that holds nothing.
        assert w._unfit_already_said(cap, "vendor/nojson",
                                     "model_not_extractable")
        assert not w._unfit_already_said(0, "vendor/nojson",
                                         "model_not_extractable"), (
            "the oldest pair was kept past the bound")

    def test_a_forgotten_or_restarted_note_answers_not_said(self) -> None:
        """What `_unfit_already_said` answers once its note is gone: the
        chat WAS told in this run, and after a fit turn's
        `_forget_unfit` the answer is False. A new worker, which is what a
        restart builds, holds no note either."""
        w = notebook_worker.Worker()
        reason = "model_not_extractable"
        w._remember_unfit(7, "vendor/nojson", reason)
        w._remember_unfit(8, "vendor/nojson", reason)
        # GROUND: told, and the note is held.
        assert w._unfit_already_said(7, "vendor/nojson", reason)

        w._forget_unfit(7)

        assert not w._unfit_already_said(7, "vendor/nojson", reason), (
            "a chat whose model was found fit still reads as told")
        # POSITIVE: another chat's note survives, so the miss above is the
        # forget and not an empty memory.
        assert w._unfit_already_said(8, "vendor/nojson", reason)
        assert not notebook_worker.Worker()._unfit_already_said(
            8, "vendor/nojson", reason), "a restart kept the note"

    def test_a_pair_met_again_is_the_last_to_be_forgotten(self) -> None:
        """LRU, not first-in-first-out. A pair the reader
        keeps meeting is touched on every hit, so a full memory lets go of
        the pair nobody has met for longest, not of the one that is still
        being refused every turn and would otherwise be written again."""
        w = notebook_worker.Worker()
        cap = notebook_worker.UNFIT_SAID_MAX
        reason = "model_not_extractable"
        for i in range(cap):
            w._remember_unfit(i, "vendor/nojson", reason)
        # Chat 0 is the oldest entry, and it is met again.
        assert w._unfit_already_said(0, "vendor/nojson", reason), "ground"

        w._remember_unfit(cap, "vendor/nojson", reason)

        assert len(w._unfit_said) == cap
        assert w._unfit_already_said(0, "vendor/nojson", reason), (
            "the pair met a moment ago was the one forgotten")
        # POSITIVE: something WAS forgotten, and it is the next oldest.
        assert not w._unfit_already_said(1, "vendor/nojson", reason)

    @pytest.mark.anyio
    async def test_a_list_that_cannot_be_fetched_is_not_blamed_on_the_model(
            self, client, chat, monkeypatch) -> None:
        """No evidence is not bad evidence. With the provider's list out of
        reach a bound model is refused (nothing is paid for a question nobody
        answered), and that refusal has its own reason: the model was never
        examined, so nothing may say it cannot write notes."""
        calls = provider(monkeypatch)
        tried = _unreachable(monkeypatch)
        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/never-heard-of")
        w = notebook_worker.Worker()

        assert await w._handle(chat) == "model_list_unavailable"
        assert tried, "ground: the reader asked for the list first"
        assert calls == [] and spend() == 0
        with get_db() as con:
            reasons = [r[0] for r in con.execute(
                "SELECT skip_reason FROM notebook_extractions "
                "WHERE chat_id = ? AND status = 'skipped'", (chat,))]
        assert reasons == ["model_list_unavailable"]
        body = client.get(f"{API}/worker?chat_id={chat}").json()
        assert body["chat_refusal"] is None, (
            "the card blamed the model for a list it never saw")
        assert body["stats"]["skip_reasons"] == {"model_list_unavailable": 1}

        # POSITIVE CONTROL: once the list is in hand and does not carry the
        # model, the same pair IS the model's fault, is said, and the card
        # says it about this chat.
        _list_carries("vendor/json")
        add_messages(chat, ["one more", "and a reply"])
        assert await w._handle(chat) == "model_not_extractable"
        body = client.get(f"{API}/worker?chat_id={chat}").json()
        assert body["chat_refusal"] == "model_not_extractable"
        assert calls == []


class TestAListOutageBlamesOnlyWhatNobodyVetted:
    """`model_list_unavailable` means
    the provider's list could not be fetched AND the model is bound to the
    chat, so nothing vetted it. A model the reader PICKED is refused with
    the list out of reach only when the catalogue says it cannot answer in
    schema: the model was examined and found unfit, which is
    `model_not_extractable`. One function answers for the turn gate, the
    preview's `blocked_by` and the run route, so all three agree.

    Bound or picked, list up or down: the reason each door gives."""

    @pytest.mark.anyio
    @pytest.mark.parametrize("bound, list_up, expected", [
        # The list is out of reach and nothing vetted the chat's own model.
        (True, False, "model_list_unavailable"),
        # The list is in hand and does not carry the chat's own model.
        (True, True, "model_not_extractable"),
        # A picked model the catalogue refuses, list out of reach.
        (False, False, "model_not_extractable"),
        # A picked model the list, in hand, does not carry.
        (False, True, "model_not_extractable"),
    ])
    async def test_every_door_gives_the_same_reason(
            self, client, chat, monkeypatch, bound, list_up,
            expected) -> None:
        calls = provider(monkeypatch)
        model = "vendor/examined"
        if bound:
            # The catalogue says it CAN, which is exactly the evidence a
            # bound model is not judged on.
            _cache_model(model, ["structured_outputs", "temperature"])
            choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
                   chat_model=model)
        else:
            _cache_model(model, ["response_format", "temperature"])
            choose(chat, extract=model)
        tried = [] if list_up else _unreachable(monkeypatch)
        if list_up:
            _list_carries("vendor/json")
        other = ({"model_list_unavailable", "model_not_extractable"}
                 - {expected}).pop()

        preview = client.get(f"{API}/extract/plan/{chat}?scope=all").json()
        assert preview["ready"] is False
        assert expected in preview["blocked_by"], preview["blocked_by"]
        assert other not in preview["blocked_by"]

        r = client.post(f"{API}/extract/run/{chat}", json={"scope": "all"})
        assert r.json() == {"started": False, "reason": expected}

        w = notebook_worker.Worker()
        assert await w._handle(chat) == expected
        with get_db() as con:
            reasons = [row[0] for row in con.execute(
                "SELECT skip_reason FROM notebook_extractions "
                "WHERE chat_id = ? AND status = 'skipped'", (chat,))]
        assert reasons == [expected]
        # GROUND for the outage rows: the list really is not in hand, and
        # for the bound model the reader did try to fetch it, so
        # "unreachable" is the network and not a process that never asked.
        # (A pick the catalogue knows is its own evidence; nothing is
        # fetched for it, see `_learn_about`.)
        if not list_up:
            assert openrouter.cached_extraction_models() is None
            assert bool(tried) is bound
        assert calls == [] and spend() == 0 and job_count(chat) == 0

    @pytest.mark.anyio
    async def test_GROUND_a_picked_model_nothing_refuses_is_read_with_the_list_down(
            self, client, chat, monkeypatch) -> None:
        """POSITIVE CONTROL for the rows above: the picked arm with the list
        out of reach is not a blanket refusal. A pick the catalogue has no
        word against is read."""
        calls = provider(monkeypatch)
        _unreachable(monkeypatch)
        choose(chat, extract="vendor/picked-and-quiet")
        preview = client.get(f"{API}/extract/plan/{chat}?scope=all").json()
        assert preview["blocked_by"] == []
        w = notebook_worker.Worker()
        await w._handle(chat, min_new=1, after_id=0)
        assert len(calls) == 1

    @pytest.mark.parametrize("bound", [True, False])
    def test_a_list_filling_between_reads_cannot_blame_a_listed_model(
            self, monkeypatch, bound) -> None:
        """`_unfit_reason` read the list twice: inside
        `_model_is_extractable`, then again for the reason. Another task's
        `_learn_about` filling the cache between the two reads made the
        first see no list (refused) and the second see a list carrying the
        model (blamed as `model_not_extractable`). The list here fills
        after the first read, which is that interleaving on demand."""
        carried = [{"id": "vendor/json", "prompt_price": 0.5,
                    "provider": "p", "context_length": 8000,
                    "endpoints": 1}]
        reads: list[int] = []

        def fills_after_first_read():
            reads.append(1)
            return None if len(reads) == 1 else carried

        # GROUND: a steady list carrying the model lets it through, and a
        # steady list without it is the model's refusal, so the absence of
        # that reason below is the snapshot and not a gate that never
        # says it.
        monkeypatch.setattr(openrouter, "cached_extraction_models",
                            lambda: carried)
        assert notebook_worker._unfit_reason("vendor/json", bound=bound) is None
        monkeypatch.setattr(openrouter, "cached_extraction_models",
                            lambda: [])
        assert notebook_worker._unfit_reason(
            "vendor/json", bound=bound) == "model_not_extractable"

        monkeypatch.setattr(openrouter, "cached_extraction_models",
                            fills_after_first_read)
        monkeypatch.setattr(openrouter, "get_cached_model_metadata",
                            lambda _mid: None)
        said = notebook_worker._unfit_reason("vendor/json", bound=bound)

        assert said != "model_not_extractable", (
            "a model the list carries was blamed because the list filled "
            "between two reads of it")
        # One snapshot: no list at that moment is the network's refusal for
        # a bound model and a pass for a picked one the catalogue has not
        # examined.
        assert said == ("model_list_unavailable" if bound else None)
        assert len(reads) == 1


class TestTheCardSaysItAboutTheChatOnScreen:
    """3.8. The Background notes card said "N skipped: the
    note model this chat uses cannot write notes" with N counted over every
    chat, so a chat on a working model was told its model was broken because
    another chat's was, and a chat that had followed the advice went on
    being told it. The sentence now comes from `chat_refusal`: this chat's
    own refusals newer than its last read, and only while one of them names
    the model the chat uses now."""

    @staticmethod
    def _refused_and_other(client, chat) -> int:
        _cache_model("vendor/nojson", ["temperature"])
        _list_carries("vendor/json")
        other = seed(client, count=BATCH * 2 + 5)
        choose(other, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/json")
        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/nojson")
        return other

    @staticmethod
    def _said(client, chat_id: int | None):
        q = "" if chat_id is None else f"?chat_id={chat_id}"
        return client.get(f"{API}/worker{q}").json()["chat_refusal"]

    @pytest.mark.anyio
    async def test_another_chats_refusal_says_nothing_here(
            self, client, chat, monkeypatch) -> None:
        provider(monkeypatch)
        other = self._refused_and_other(client, chat)
        w = notebook_worker.Worker()
        assert await w._handle(chat) == "model_not_extractable"

        # GROUND: the refused chat is told.
        assert self._said(client, chat) == "model_not_extractable"
        # The chat on a working model is not, and neither is no chat.
        assert self._said(client, other) is None
        assert self._said(client, None) is None

    @pytest.mark.anyio
    async def test_a_chat_that_switched_to_a_working_model_says_nothing(
            self, client, chat, monkeypatch) -> None:
        calls = provider(monkeypatch)
        self._refused_and_other(client, chat)
        w = notebook_worker.Worker()
        assert await w._handle(chat) == "model_not_extractable"
        assert self._said(client, chat) == "model_not_extractable", "ground"

        # The switch alone, before any turn: the row names the old model.
        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/json")
        assert self._said(client, chat) is None

        # And after the working model has read the chat.
        add_messages(chat, ["a question", "a reply"])
        assert await w._handle(chat) == "settled"
        assert len(calls) == 1
        assert self._said(client, chat) is None

    @pytest.mark.anyio
    async def test_it_holds_when_the_instructions_change_language(
            self, client, chat, monkeypatch) -> None:
        """The work key carries the language, so a refusal still standing
        must not vanish because the reader's instructions were switched."""
        provider(monkeypatch)
        self._refused_and_other(client, chat)
        w = notebook_worker.Worker()
        assert await w._handle(chat) == "model_not_extractable"
        other = next(lang for lang in notebook_extract.PROMPT_LANGUAGES
                     if lang != notebook_worker._language())
        set_setting(config.SETTING_NOTEBOOK_PROMPT_LANG, other)

        assert self._said(client, chat) == "model_not_extractable"

    @pytest.mark.anyio
    async def test_going_back_to_a_model_already_refused_still_says_it(
            self, client, chat, monkeypatch) -> None:
        """nojson -> nojson2 -> nojson: the pair memory
        writes no row for the third turn (that pair was said), so the
        newest refusal row names nojson2. Reading only the newest row left
        the card silent while the chat's model still could not write notes.
        Any refusal newer than the chat's last read that names the current
        model keeps the sentence up."""
        calls = provider(monkeypatch)
        _cache_model("vendor/nojson", ["temperature"])
        _cache_model("vendor/nojson2", ["temperature"])
        _list_carries("vendor/json")
        w = notebook_worker.Worker()

        said = []
        for turn, model in enumerate(["vendor/nojson", "vendor/nojson2",
                                      "vendor/nojson", "vendor/nojson",
                                      "vendor/nojson"]):
            choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
                   chat_model=model)
            add_messages(chat, [f"q{turn}", f"r{turn}"])
            outcome = await w._handle(chat)
            said.append((model, outcome, self._said(client, chat)))

        # The pair memory is what makes this the case: the turns back on
        # nojson wrote nothing.
        assert [o for _, o, _ in said] == [
            "model_not_extractable", "model_not_extractable",
            None, None, None], said
        assert [r for _, _, r in said] == ["model_not_extractable"] * 5, (
            f"the card went silent on a model it had refused: {said}")
        assert calls == [] and spend() == 0

        # GROUND: a read after the refusals clears it. The same card, the
        # same chat, now on a model that can write notes and read with it.
        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/json")
        add_messages(chat, ["fit q", "fit r"])
        assert await w._handle(chat) == "settled"
        assert len(calls) == 1
        assert self._said(client, chat) is None
        # Going back to nojson after that read is quiet until the chat is
        # refused again: the old rows are older than the read.
        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/nojson")
        assert self._said(client, chat) is None
        add_messages(chat, [f"back {i}" for i in range(BATCH)])
        assert await w._handle(chat) == "model_not_extractable"
        assert self._said(client, chat) == "model_not_extractable"

    @pytest.mark.anyio
    async def test_a_read_with_the_same_model_once_it_is_fit_clears_it(
            self, client, chat, monkeypatch) -> None:
        """The "nothing read after the refusal" half. The model the
        refusal names becomes fit (the provider's list now carries it) and
        reads the chat. The work key still matches the current model, so
        without the last-read check the card would go on telling the reader
        their model cannot write notes right after it wrote them.

        And the chat was READ ONCE BEFORE the refusal too, which
        is the ordinary shape (a chat read for weeks, then moved to a model
        that cannot write notes). With no earlier read the first read and
        the last read are the same row, so a check against the FIRST read
        passed every test here while it would blame a model right after it
        wrote notes in any chat that had been read before."""
        calls = provider(monkeypatch)
        _cache_model("vendor/nojson", ["temperature"])
        _list_carries("vendor/json")
        w = notebook_worker.Worker()
        # The earlier read, on a model that can write notes.
        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/json")
        assert await w._handle(chat) == "settled"
        assert len(calls) == 1

        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/nojson")
        add_messages(chat, ["x0", "x1"])
        assert await w._handle(chat, min_new=1) == "model_not_extractable"
        # GROUND: a refusal newer than the earlier read is said.
        assert self._said(client, chat) == "model_not_extractable", "ground"

        _list_carries("vendor/json", "vendor/nojson")
        add_messages(chat, ["now it can", "and it does"])
        assert await w._handle(chat, min_new=1) == "settled"
        assert len(calls) == 2, "the same model read the chat"
        with get_db() as con:
            kept = con.execute(
                "SELECT COUNT(*) FROM notebook_extractions WHERE chat_id = ? "
                "AND skip_reason = 'model_not_extractable'",
                (chat,)).fetchone()[0]
        # POSITIVE: the refusal row is still there, so the None below is the
        # read retiring it and not the row having been overwritten.
        assert kept == 1
        assert self._said(client, chat) is None

    @pytest.mark.anyio
    async def test_a_read_in_flight_counts_as_a_read(
            self, client, chat, monkeypatch) -> None:
        """A `running` row is a read: the model it names
        has been called for this chat. While the fit model's first read is
        in flight the card must not go on saying the refusal the read
        retires."""
        hold, entered = asyncio.Event(), asyncio.Event()
        calls = provider(monkeypatch, hold=hold, entered=entered)
        _cache_model("vendor/nojson", ["temperature"])
        _list_carries("vendor/json")
        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/nojson")
        w = notebook_worker.Worker()
        assert await w._handle(chat) == "model_not_extractable"
        assert self._said(client, chat) == "model_not_extractable", "ground"

        _list_carries("vendor/json", "vendor/nojson")
        add_messages(chat, ["now it can", "and it does"])
        turn = asyncio.create_task(w._handle(chat))
        try:
            await asyncio.wait_for(entered.wait(), 10)
            with get_db() as con:
                states = [r[0] for r in con.execute(
                    "SELECT status FROM notebook_extractions WHERE chat_id = ? "
                    "AND status IN ('done', 'failed', 'running')",
                    (chat,)).fetchall()]
            # GROUND: the only read so far is the one in flight.
            assert states == ["running"] and len(calls) == 1
            assert self._said(client, chat) is None, (
                "the card blamed the model while it was being read")
        finally:
            hold.set()
            assert await asyncio.wait_for(turn, 10) == "settled"

    @pytest.mark.anyio
    async def test_the_rows_looked_at_are_the_newest(
            self, client, chat, monkeypatch) -> None:
        """`STANDING_REFUSAL_ROWS` bounds the rows looked
        at, and they are the NEWEST. Set to one here: two refusals (nojson,
        then nojson2) and the one row looked at must be nojson2's."""
        calls = provider(monkeypatch)
        _cache_model("vendor/nojson", ["temperature"])
        _cache_model("vendor/nojson2", ["temperature"])
        _list_carries("vendor/json")
        w = notebook_worker.Worker()
        for turn, model in enumerate(["vendor/nojson", "vendor/nojson2"]):
            choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
                   chat_model=model)
            add_messages(chat, [f"q{turn}", f"r{turn}"])
            assert await w._handle(chat) == "model_not_extractable"
        assert calls == []

        # GROUND, bound wide enough for both rows: both models are said.
        monkeypatch.setattr(notebook_worker, "STANDING_REFUSAL_ROWS", 2)
        assert self._said(client, chat) == "model_not_extractable"
        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/nojson")
        assert self._said(client, chat) == "model_not_extractable"

        monkeypatch.setattr(notebook_worker, "STANDING_REFUSAL_ROWS", 1)
        # The newest row is looked at.
        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/nojson2")
        assert self._said(client, chat) == "model_not_extractable", (
            "the one row looked at was not the newest")
        # And only it: the older pair is past the bound (the ledgered
        # silence past `STANDING_REFUSAL_ROWS`).
        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/nojson")
        assert self._said(client, chat) is None, "the bound was not applied"

    @pytest.mark.anyio
    async def test_two_refused_chats_each_say_their_own(
            self, client, chat, monkeypatch) -> None:
        """The `WHERE chat_id = ?` half. Two chats refused
        on the same model, the other one later. Each says its own. Then the
        other chat is read by a working model: this chat's refusal still
        stands, because another chat's read is not this chat's.

        The bound on rows looked at is set to one here, which is where the
        chat filter decides everything: without it the one row looked at is
        whichever chat was refused last."""
        calls = provider(monkeypatch)
        monkeypatch.setattr(notebook_worker, "STANDING_REFUSAL_ROWS", 1)
        _cache_model("vendor/nojson", ["temperature"])
        _list_carries("vendor/json")
        other = seed(client, count=BATCH * 2 + 5)
        for c in (chat, other):
            choose(c, extract=notebook_extract.CHAT_MODEL_BINDING,
                   chat_model="vendor/nojson")
        w = notebook_worker.Worker()
        assert await w._handle(chat) == "model_not_extractable"
        assert await w._handle(other) == "model_not_extractable"

        # GROUND: the chat refused last says it.
        assert self._said(client, other) == "model_not_extractable"
        assert self._said(client, chat) == "model_not_extractable", (
            "the older refusal was lost behind another chat's newer one")

        choose(other, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/json")
        add_messages(other, ["other q", "other r"])
        assert await w._handle(other) == "settled"
        assert len(calls) == 1
        assert self._said(client, other) is None
        assert self._said(client, chat) == "model_not_extractable", (
            "another chat's read retired this chat's refusal")

    @pytest.mark.anyio
    async def test_a_failing_check_costs_the_sentence_and_not_the_card(
            self, client, chat, monkeypatch, caplog) -> None:
        """`standing_refusal` reads the chat's model through
        `notebook_extract.extract_model`, which lets read errors travel.
        One optional field must not turn the whole status poll into a 500:
        the field is null, the failure is logged, the rest is answered.

        On a chat with a multi-digit id, so a log line carrying a
        wrong or truncated id cannot pass as a substring of the right one.
        """
        provider(monkeypatch)
        with get_db() as con:
            con.execute("UPDATE sqlite_sequence SET seq = 4720 "
                        "WHERE name = 'chats'")
        chat = seed(client, count=BATCH * (UNITS - 1) + 5)
        assert chat >= 1000, chat
        self._refused_and_other(client, chat)
        w = notebook_worker.Worker()
        assert await w._handle(chat) == "model_not_extractable"
        # POSITIVE CONTROL: with the read working the sentence is there.
        assert self._said(client, chat) == "model_not_extractable"

        # What the exception says is not the log's to carry: in this app an
        # exception's text can quote a row, and a row is vault content.
        secret = "quoted row text 7f3a"

        def broken(chat_id):
            raise notebook.NotebookError(f"settings_unreadable: {secret}")

        monkeypatch.setattr(notebook_extract, "extract_model", broken)
        caplog.set_level("WARNING", logger="routers.notebook")
        r = client.get(f"{API}/worker?chat_id={chat}")

        assert r.status_code == 200
        body = r.json()
        assert body["chat_refusal"] is None
        # GROUND: the rest of the card is still answered.
        assert body["stats"]["skip_reasons"] == {"model_not_extractable": 1}
        assert body["daily_cap"] == config.NOTEBOOK_DAILY_CALL_CAP
        logged = [rec.getMessage() for rec in caplog.records
                  if "chat_refusal unavailable" in rec.getMessage()]
        # GROUND: the line is there, with the type and the chat id.
        assert logged, "the failure was not logged"
        assert logged == [
            f"chat_refusal unavailable for chat {chat}: NotebookError"
        ], logged
        # And nothing the exception said.
        assert not any(secret in m for m in logged), logged
        assert not any(secret in rec.getMessage() for rec in caplog.records)

    @pytest.mark.anyio
    async def test_the_same_door_reads_a_model_that_can_answer(
            self, client, chat, monkeypatch) -> None:
        """POSITIVE CONTROL for the gate above: one line of production code
        that returned None unconditionally would pass that row and silence
        the whole reader. Same door, same driver, a model that qualifies."""
        calls = provider(monkeypatch)
        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/json")
        _list_carries("vendor/json")
        w = notebook_worker.Worker()

        await w._handle(chat, min_new=1, after_id=0)

        assert len(calls) == 1 and spend() == 1
        assert notebook.list_entries(chat), "and it wrote what it read"


class TestAColdProcessKnowsNothingAndSpendsNothing:
    """The gate on a process that has never fetched the model list.

    R-5: "Sohbet modeli kati JSON sema tasimayabilir. `GET /extract/models`
    listesi bu yuzden suzuluyor ... `model_not_extractable` ile cagri
    yapmadan reddedilir." Until this class the gate answered True for every
    model neither cache had heard of, which after a restart is EVERY model
    - so the one door this unit opened, a model the chat chose and nothing
    vetted, was paid for under exactly the condition the risk names.

    Two halves, and both are needed: the reader goes and gets the list
    before it decides, and when it cannot get it a BOUND model is refused
    rather than billed.
    """

    @pytest.mark.anyio
    async def test_a_bound_model_nothing_knows_is_refused_without_a_call(
            self, client, chat, monkeypatch) -> None:
        calls = provider(monkeypatch)
        tried = _unreachable(monkeypatch)
        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/never-heard-of")

        preview = client.get(f"{API}/extract/plan/{chat}?scope=all").json()
        r = client.post(f"{API}/extract/run/{chat}", json={"scope": "all"})

        # Refused, and refused as what it is: the list could not be
        # fetched, so nothing vetted this model, and no door blames it.
        assert preview["ready"] is False
        assert preview["blocked_by"] == ["model_list_unavailable"]
        assert r.json() == {"started": False,
                            "reason": "model_list_unavailable"}
        assert tried, "the reader refused without ever asking for the list"
        # THE WHOLE CLAIM: nothing was sent and nothing was billed.
        assert calls == [] and spend() == 0
        assert job_count(chat) == 0

        # And the other door, the one a turn walks through by itself.
        await notebook_worker.Worker()._handle(chat, min_new=1, after_id=0)

        assert calls == [] and spend() == 0
        assert notebook.list_entries(chat) == []

    @pytest.mark.anyio
    async def test_GROUND_the_same_cold_process_reads_a_model_that_was_picked(
            self, client, chat, monkeypatch) -> None:
        """The pick is the evidence, so the same cold cache is not a
        refusal for everybody. Without this row the one above would also
        pass on a gate that had stopped reading anything at all."""
        calls = provider(monkeypatch)
        _unreachable(monkeypatch)
        choose(chat)   # the reader's own choice, off the filtered list

        preview = client.get(f"{API}/extract/plan/{chat}?scope=all").json()
        out = await run_and_wait(chat, "next")

        assert preview["blocked_by"] == [] and preview["ready"] is True
        assert out["started"] is True
        assert len(calls) == 1 and spend() == 1

    @pytest.mark.anyio
    async def test_the_list_is_fetched_before_the_gate_decides(
            self, client, chat, monkeypatch) -> None:
        """The learning half. The list answers the question for free, and
        the answer is taken: a bound model ON it is read."""
        calls = provider(monkeypatch)
        fetched: list[int] = []

        async def serve():
            fetched.append(1)
            openrouter._zdr_cache = (time.monotonic(), [
                {"id": "vendor/listed", "prompt_price": 0.5, "provider": "p",
                 "context_length": 8000, "endpoints": 1}])
            return openrouter._zdr_cache[1]

        monkeypatch.setattr(openrouter, "fetch_extraction_models", serve)
        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/listed")

        out = await run_and_wait(chat, "next")

        assert fetched, "the list was never asked for"
        assert out["started"] is True
        assert [c["model"] for c in calls] == ["vendor/listed"]
        assert spend() == 1

    @pytest.mark.anyio
    async def test_a_bound_model_the_list_does_not_carry_is_refused(
            self, client, chat, monkeypatch) -> None:
        """POSITIVE CONTROL for the row above, and the `cached_extraction_
        models` arm of the gate, which no other row reaches: the list IS
        fetched, it answers, and the answer is no."""
        calls = provider(monkeypatch)

        async def serve():
            openrouter._zdr_cache = (time.monotonic(), [
                {"id": "vendor/listed", "prompt_price": 0.5, "provider": "p",
                 "context_length": 8000, "endpoints": 1}])
            return openrouter._zdr_cache[1]

        monkeypatch.setattr(openrouter, "fetch_extraction_models", serve)
        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/absent")

        r = client.post(f"{API}/extract/run/{chat}", json={"scope": "all"})

        assert r.json() == {"started": False, "reason": "model_not_extractable"}
        assert calls == [] and spend() == 0
        # The metadata cache says the opposite, and the LIST still wins:
        # a fetched list is the filtered set and metadata is the fallback.
        _cache_model("vendor/absent", ["structured_outputs"])
        assert notebook_worker._model_is_extractable(
            "vendor/absent", bound=True) is False


class TestTheCatalogueDoesNotAnswerForABoundModel:
    """The ordinary state after a restart in which anybody chatted.

    Two independent caches: the chat model picker fills the general
    catalogue (`openrouter._model_cache`), the extraction picker fills the
    filtered list (`_zdr_cache`), and openrouter's own comment calls them
    "a second, independent cache". So this is not an edge state - somebody
    chatted, the catalogue knows the chat model, the extraction panel was
    never opened, and the filtered list is unknown.

    SECURITY.md says of that moment that the app "asks the provider which
    models can answer in the strict form notes need ... and if the model
    you are chatting with is not among them, the read is refused with that
    reason instead of paid for". The catalogue answers a WEAKER question -
    `structured_outputs` by itself, where the list is zero-retention AND
    `structured_outputs` - so while the gate fell back to it, a bound model
    the list does not carry was read and billed under a document that says
    it is refused.
    """

    @pytest.mark.anyio
    async def test_every_door_refuses_a_bound_model_the_catalogue_knows(
            self, client, chat, monkeypatch) -> None:
        calls = provider(monkeypatch)
        tried = _unreachable(monkeypatch)
        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/chatty")
        # The catalogue says yes, in the strongest form it has.
        _cache_model("vendor/chatty", ["structured_outputs", "temperature"])
        assert openrouter.get_cached_model_metadata("vendor/chatty"), (
            "ground: the catalogue is warm, which is the whole fixture")
        assert openrouter.cached_extraction_models() is None, (
            "ground: and the filtered list is the unknown thing")

        preview = client.get(f"{API}/extract/plan/{chat}?scope=all").json()
        # HERE, not only at the end. The preview door resolves `bound` for
        # itself before it learns, and a door that resolved it wrong would
        # skip the fetch on the warm catalogue and still refuse - so a
        # check of `tried` taken after all four doors is answered by
        # whichever door got it right. This one is about this door.
        assert tried == [1], (
            "the preview door refused without asking for the list itself")
        run = client.post(f"{API}/extract/run/{chat}", json={"scope": "all"})
        # The sweep route was a fourth door here until decision 69 a
        # removed it; the three that are left are all asked.
        await notebook_worker.Worker()._handle(chat, min_new=1, after_id=0)

        # Refused at every door, as the list it could not fetch: the
        # warm catalogue's yes is not evidence for a bound model, and its
        # absence of a list is not the model's fault either.
        assert preview["ready"] is False
        assert preview["blocked_by"] == ["model_list_unavailable"]
        assert run.json() == {"started": False,
                              "reason": "model_list_unavailable"}
        # THE WHOLE CLAIM: every door, nothing sent, nothing billed.
        assert calls == [] and spend() == 0
        assert job_count(chat) == 0
        assert notebook.list_entries(chat) == []
        assert unread(chat) == 0, "nothing was read, and it did not pretend"
        assert tried, "refused without ever asking for the list"

    @pytest.mark.anyio
    async def test_GROUND_the_same_catalogue_reads_a_model_the_list_carries(
            self, client, chat, monkeypatch) -> None:
        """The same warm catalogue and the same bound model, with the list
        fetched and carrying it. Without this row the one above would pass
        on a gate that had stopped reading anything at all."""
        calls = provider(monkeypatch)
        _unreachable(monkeypatch)
        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/chatty")
        _cache_model("vendor/chatty", ["structured_outputs", "temperature"])
        _list_carries("vendor/chatty")

        preview = client.get(f"{API}/extract/plan/{chat}?scope=all").json()
        out = await run_and_wait(chat, "next")

        assert preview["ready"] is True and preview["blocked_by"] == []
        assert out["started"] is True
        assert [c["model"] for c in calls] == ["vendor/chatty"]
        assert spend() == 1

    @pytest.mark.anyio
    async def test_a_picked_model_still_reads_on_the_catalogue_alone(
            self, client, chat, monkeypatch) -> None:
        """POSITIVE CONTROL for the SCOPE of the change: `bound` is the
        whole of it. A model the reader picked came off the filtered list,
        so the pick is its evidence, the catalogue stays its fallback, and
        nothing about that path moved - including that it does not go and
        fetch the list when the catalogue can answer."""
        calls = provider(monkeypatch)
        tried = _unreachable(monkeypatch)
        choose(chat)   # the reader's own pick, EXTRACT
        _cache_model(EXTRACT, ["structured_outputs", "temperature"])

        out = await run_and_wait(chat, "next")

        assert out["started"] is True
        assert [c["model"] for c in calls] == [EXTRACT]
        assert tried == [], "a picked model the catalogue knows sent a request"


class TestTheListIsAskedForOnceAndNotOnceATurn:
    """`_learn_about`'s own frequency claim, measured rather than asserted.

    Nothing here is money: `GET /extract/models` is the public policy list,
    it carries no conversation and costs no model tokens, and the plan's
    rule for this refusal is about the paid call - "Sohbet modeli kati JSON
    sema desteklemiyorsa is **cagri yapmadan** reddedilir". But a refused
    turn that asks again is one outbound request per door per turn while a
    provider is down, and the docstring claimed the opposite, so the number
    is written down where it can go red.
    """

    @pytest.mark.anyio
    async def test_a_provider_that_cannot_be_reached_is_asked_once(
            self, client, chat, monkeypatch) -> None:
        calls = provider(monkeypatch)
        tried = _unreachable(monkeypatch)
        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/never-heard-of")

        client.get(f"{API}/extract/plan/{chat}?scope=all")
        run = client.post(f"{API}/extract/run/{chat}", json={"scope": "all"})
        # Two turns of the reader, where the sweep route used to be the
        # fourth door (decision 69 a removed it).
        second = notebook_worker.Worker()
        await second._handle(chat, min_new=1, after_id=0)
        await second._handle(chat, min_new=1, after_id=0)

        assert run.json()["reason"] == "model_list_unavailable", (
            "ground: the doors ran and refused, so there was something to "
            "ask about at each of them")
        assert len(tried) == 1, (
            "four doors over two turns made one request each while the "
            "provider was unreachable")
        assert calls == [] and spend() == 0

    @pytest.mark.anyio
    async def test_a_key_change_makes_it_ask_again(
            self, client, chat, monkeypatch) -> None:
        """POSITIVE CONTROL for the row above: the memory of the failure is
        the cached provider view like every other cache, so the moment that
        view is thrown away the next door asks again. Without this the row
        above would also pass on a reader that asked once and then never
        again for the life of the process."""
        tried = _unreachable(monkeypatch)
        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/never-heard-of")

        client.get(f"{API}/extract/plan/{chat}?scope=all")
        assert len(tried) == 1, "ground: the first door asked"
        client.get(f"{API}/extract/plan/{chat}?scope=all")
        assert len(tried) == 1, "ground: the second did not"

        openrouter.invalidate_model_cache()
        client.get(f"{API}/extract/plan/{chat}?scope=all")

        assert len(tried) == 2

    @pytest.mark.anyio
    async def test_once_the_answer_is_known_nothing_asks_again(
            self, client, chat, monkeypatch) -> None:
        """The other half of "asks once": a success is the end of it."""
        calls = provider(monkeypatch)
        fetched: list[int] = []

        async def serve():
            fetched.append(1)
            _list_carries("vendor/listed")
            return openrouter.cached_extraction_models()

        monkeypatch.setattr(openrouter, "fetch_extraction_models", serve)
        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/listed")

        await run_and_wait(chat, "next")
        assert len(fetched) == 1, "ground: the first read went and asked"
        assert len(calls) == 1, "ground: and it read"

        client.get(f"{API}/extract/plan/{chat}?scope=all")
        await notebook_worker.Worker()._handle(chat, min_new=1, after_id=0)

        assert len(fetched) == 1, "the answer was already here"

    @pytest.mark.anyio
    async def test_a_door_that_opens_mid_request_does_not_start_another(
            self, client, chat, monkeypatch) -> None:
        """The single in-flight guard. Two doors opened together is one
        event loop re-entering this function while the first request is
        still out, which is exactly what the inner call below is: the
        preview is away fetching, and a turn reaches the gate meanwhile."""
        fetched: list[int] = []

        async def serve():
            fetched.append(1)
            if len(fetched) == 1:
                # The second door, while the first request is in flight.
                await notebook_worker._learn_about("vendor/listed",
                                                   bound=True)
            _list_carries("vendor/listed")
            return openrouter.cached_extraction_models()

        monkeypatch.setattr(openrouter, "fetch_extraction_models", serve)
        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/listed")

        preview = await notebook_worker.worker.plan_backfill(chat, "all")

        assert fetched == [1], "the second door started its own request"
        assert preview["ready"] is True, (
            "ground: the door still answered, off the list the first "
            "request brought back")

    @pytest.mark.anyio
    async def test_the_memory_of_a_failure_ends_with_its_window(
            self, client, chat, monkeypatch) -> None:
        """The RECOVERY half of the memory, which nothing measured.

        `extraction_list_recently_unreachable` promises the window a
        success gets and no more - "after it the next caller tries again,
        so a provider that comes back is noticed without a restart". Until
        this row no test in the tree referenced `MODEL_LIST_TTL` at all, so
        the memory could have been turned into a permanent flag and the
        suite would have stayed green while a provider that came back was
        ignored for the life of the process.

        THE STAMP MOVES, NOT THE CLOCK. `openrouter.time` is the shared
        time module, so patching `monotonic` lands on asyncio's scheduler
        as well; `test_stream_deadlines._ScriptedClock` says what that
        costs. Ageing the one timestamp asks the same question of the same
        comparison without touching anything else in the process. The
        window itself is imported, never retyped.
        """
        tried = _unreachable(monkeypatch)
        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/never-heard-of")

        client.get(f"{API}/extract/plan/{chat}?scope=all")
        assert len(tried) == 1, "ground: the first door asked"

        # One second inside the window: still remembered, still silent.
        openrouter._zdr_unreachable_at = (
            time.monotonic() - (config.MODEL_LIST_TTL - 1))
        client.get(f"{API}/extract/plan/{chat}?scope=all")
        assert len(tried) == 1, (
            "ground: a failure younger than the window is still remembered, "
            "so the row below is measuring the window and not the door")

        # One second past it.
        openrouter._zdr_unreachable_at = (
            time.monotonic() - (config.MODEL_LIST_TTL + 1))
        blocked = client.get(f"{API}/extract/plan/{chat}?scope=all").json()

        assert len(tried) == 2, (
            "the window ended and the next door still did not ask, so a "
            "provider that came back would never be noticed")
        assert "model_list_unavailable" in blocked["blocked_by"], (
            "ground: the door ran and refused, and the fresh attempt failed "
            "too, so the refusal is still the honest answer")


class TestTheFreeListFetchPassesTheProxyGate:
    """The list fetch is an outbound request, so it passes THE gate.

    `enforce_proxy_gate` is the one function every outbound path goes
    through, and its docstring records what a path that forgets it looks
    like: `POST /settings/api-key` validated the key against the provider
    with no gate, so in the `proxy_required` + no-URL state - the state
    every other path refuses outright - the key and the reader's real IP
    went out unproxied from the very screen where the key is typed.

    The doors below are the ones that learn: the preview, the run route,
    the turn-driven reader, and a queued offer, which reaches that same
    reader through the loop. (The sweep route was a fifth until decision
    69 a removed it, and the offer used to start mode 1's automatic job,
    which decisions 30 a and 31 a removed.) None of them has a gate of
    its own, and none of them is a request the
    reader asked to send. `GET /extract/models` fetches the same list and
    has called the gate since it was written; it is the ground here.

    Measured at the transport rather than at our own wrapper: every
    request in `openrouter` goes through `get_client`, so a row that
    stubbed `fetch_extraction_models` would prove only that we did not
    call ourselves.
    """

    @pytest.mark.anyio
    async def test_the_preview_door_sends_nothing(
            self, client, chat, monkeypatch, gate_armed) -> None:
        wire = _wire(monkeypatch)
        calls = provider(monkeypatch)
        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/chatty")
        # GROUND for the memory below: nothing is remembered yet, so what
        # is asserted after the door is the door's own doing. The autouse
        # cache reset clears it, and this says so out loud rather than
        # relying on it.
        assert openrouter.extraction_list_recently_unreachable() is False

        preview = client.get(f"{API}/extract/plan/{chat}?scope=all").json()

        assert preview["ready"] is False, "ground: the door ran and answered"
        # A closed gate is an unreachable list, and says so.
        assert "model_list_unavailable" in preview["blocked_by"]
        assert wire == [], "a request went out from behind an armed gate"
        assert calls == [] and spend() == 0
        # AND THE REFUSAL IS REMEMBERED, which is the second half of
        # `_learn_about`'s promise that a closed gate is treated exactly
        # like an unreachable provider. Without it every door and every
        # turn consults the gate again - the gate's own health probe, to
        # the reader's OWN proxy, not to the provider - for as long as the
        # switch is armed, and `TestTheListIsAskedForOnceAndNotOnceATurn`
        # cannot cover a state it never enters. Nothing else in this suite
        # counts a closed gate, so this line is the only thing that can go
        # red when the memory is dropped.
        assert openrouter.extraction_list_recently_unreachable(), (
            "a closed gate was not remembered, so the next door asks again")

    @pytest.mark.anyio
    async def test_the_run_door_sends_nothing(
            self, client, chat, monkeypatch, gate_armed) -> None:
        wire = _wire(monkeypatch)
        calls = provider(monkeypatch)
        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/chatty")

        run = client.post(f"{API}/extract/run/{chat}", json={"scope": "all"})

        assert run.json() == {"started": False,
                              "reason": "model_list_unavailable"}
        assert wire == [], "a request went out from behind an armed gate"
        assert calls == [] and spend() == 0 and job_count(chat) == 0

    @pytest.mark.anyio
    async def test_the_turn_driven_reader_sends_nothing(
            self, client, chat, monkeypatch, gate_armed) -> None:
        wire = _wire(monkeypatch)
        calls = provider(monkeypatch)
        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/chatty")

        await notebook_worker.Worker()._handle(chat, min_new=1, after_id=0)

        assert unread(chat) == 0, "ground: nothing was read, and it did not "\
                                  "pretend it had been"
        assert wire == [], "a request went out from behind an armed gate"
        assert calls == [] and spend() == 0
        assert notebook.list_entries(chat) == []

    @pytest.mark.anyio
    async def test_the_automatic_first_offer_sends_nothing(
            self, client, chat, monkeypatch, gate_armed) -> None:
        """The door nobody presses: the queue offer a finished reply
        leaves, on a chat with no notes. It used to start mode 1's
        whole-chat job; since decisions 30 a and 31 a it reaches the
        ordinary reader, and the gate holds there too."""
        wire = _wire(monkeypatch)
        calls = provider(monkeypatch)
        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/chatty")
        w = notebook_worker.Worker()
        w.queue = asyncio.Queue(maxsize=8)
        assert unread(chat) == 0, "ground: the chat has something to offer"

        await offer_and_drain(w, chat)

        assert wire == [], "a request went out from behind an armed gate"
        assert calls == [] and spend() == 0 and job_count(chat) == 0

    @pytest.mark.anyio
    async def test_GROUND_the_gated_route_answers_the_same_way(
            self, client, monkeypatch, gate_armed) -> None:
        """The door that has always called the gate, in the same state.

        It is the yardstick for the five above: this is what the app does
        with this list when the gate is closed, and now they do it too.
        """
        import proxy_health

        wire = _wire(monkeypatch)

        models = client.get(f"{API}/extract/models")

        assert models.status_code == 503
        detail = models.json()["detail"]
        assert detail in proxy_health.PROXY_REASONS
        assert detail == "proxy_missing"
        assert wire == [], "a request went out from behind an armed gate"

    @pytest.mark.anyio
    async def test_POSITIVE_the_same_door_does_send_when_the_gate_is_open(
            self, client, chat, monkeypatch) -> None:
        """POSITIVE CONTROL for all six absences above, and the only reason
        they mean anything: no `gate_armed`, and the same preview reaches
        the transport, sends the Authorization header the armed rows are
        about, and reads.

        Without it, a `_learn_about` that had simply stopped fetching - or
        a recorder wired to nothing - would pass every row above.
        """
        wire = _wire(monkeypatch, serves=("vendor/chatty",))
        calls = provider(monkeypatch)
        choose(chat, extract=notebook_extract.CHAT_MODEL_BINDING,
               chat_model="vendor/chatty")

        preview = client.get(f"{API}/extract/plan/{chat}?scope=all").json()
        out = await run_and_wait(chat, "next")

        assert len(wire) == 1, "the door did not reach the transport at all"
        url, header_names = wire[0]
        assert url.endswith(openrouter._ZDR_ENDPOINTS_PATH)
        assert "Authorization" in header_names, (
            "the key rides on this request, which is what the armed rows "
            "are about")
        assert preview["ready"] is True and preview["blocked_by"] == []
        assert out["started"] is True
        assert [c["model"] for c in calls] == ["vendor/chatty"]
        assert spend() == 1


class TestFitsTodayIsAboutTheJobsCeiling:
    """T-04 names it: "`fits_today` bu tavana bakar" - THAT ceiling, the
    job's, not the day's.

    The existing preview rows plan more calls than either ceiling allows, so
    `fits_today is False` is true under both readings and cannot tell them
    apart: measuring it against `NOTEBOOK_DAILY_CALL_CAP` instead left the
    whole file green. The fixture here is the one shape that separates them
    - a run that fits the DAY and not the JOB - and the strip reads
    `fits_today` to decide whether Start may be pressed at all.
    """

    def _planned(self, client, chat) -> int:
        """How many calls this fixture's chat needs, asked rather than
        assumed: the number follows the batch size and the fixture's
        length, and a retyped one would make the arithmetic below a
        coincidence."""
        return client.get(
            f"{API}/extract/plan/{chat}?scope=all").json()["calls_planned"]

    def test_a_run_that_fits_the_day_and_not_the_job_does_not_fit(
            self, client, chat, monkeypatch) -> None:
        planned = self._planned(client, chat)
        assert planned >= 2, "ground: there is a run to talk about"
        # Room for the whole run in the DAY, room for one call in the JOB.
        monkeypatch.setattr(config, "NOTEBOOK_DAILY_CALL_CAP", planned + 10)
        monkeypatch.setattr(config, "NOTEBOOK_LIVE_RESERVE", planned + 9)

        plan = client.get(f"{API}/extract/plan/{chat}?scope=all").json()

        assert plan["daily_cap"] == planned + 10
        assert plan["backfill_cap"] == 1
        assert plan["calls_spent_today"] == 0
        assert plan["calls_planned"] == planned
        # It fits the day by ten calls and misses the job's ceiling by
        # everything but one. Only one reading of the sentence answers
        # False here, which is what the existing rows could not tell apart:
        # theirs plan more than BOTH ceilings allow.
        assert plan["calls_spent_today"] + plan["calls_planned"] \
            <= plan["daily_cap"], "ground: it fits the DAY"
        assert plan["fits_today"] is False, (
            "`fits_today` was measured against the day's ceiling, so the "
            "strip offers a run the job's own ceiling will cut short")

    def test_GROUND_the_same_run_under_a_smaller_reserve_fits(
            self, client, chat, monkeypatch) -> None:
        """The positive control: move the reserve alone and the same run
        fits, so the row above is about the reserve and not about
        `fits_today` being wired to False."""
        planned = self._planned(client, chat)
        monkeypatch.setattr(config, "NOTEBOOK_DAILY_CALL_CAP", planned + 10)
        monkeypatch.setattr(config, "NOTEBOOK_LIVE_RESERVE", 1)

        plan = client.get(f"{API}/extract/plan/{chat}?scope=all").json()

        assert plan["backfill_cap"] == planned + 9
        assert plan["fits_today"] is True, plan


# ── T-08: the vault locks mid-job ─────────────────────────────────────────

class TestTheVaultLocksMidJob:
    @pytest.mark.anyio
    async def test_quiesce_waits_and_the_paid_reply_lands(
            self, db, monkeypatch) -> None:
        """T-08. Same shape as Defect 1's test: a paid call is in flight,
        the vault locks, the reply still lands and no second unit starts."""
        chat_id = make_chat_on_db(45)
        choose(chat_id)
        reply_sent = asyncio.Event()
        calls: list[int] = []

        async def slow_reply(*a, **kw):
            calls.append(1)
            await asyncio.sleep(0.02)
            reply_sent.set()
            return {"id": "gen-lock", "choices": [{
                "finish_reason": "stop",
                "message": {"content": json.dumps({"facts": [fact()]})}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                          "cost": 0.0009}}

        monkeypatch.setattr(openrouter, "complete", slow_reply)
        real_write = notebook_worker._write

        def slow_write(*a, **kw):
            time.sleep(0.1)
            return real_write(*a, **kw)

        monkeypatch.setattr(notebook_worker, "_write", slow_write)
        assert notebook_worker.worker.task is None, "ground: nothing running"
        notebook_worker.start()
        try:
            out = await notebook_worker.worker.backfill(chat_id, "all")
            assert out["started"] is True
            await asyncio.wait_for(reply_sent.wait(), 5)
            await asyncio.sleep(0.01)

            await notebook_worker.quiesce()
            vault_state.clear_key()
            vault_state.set_key(TEST_VAULT_KEY)
            await asyncio.sleep(0.05)

            assert calls == [1], "a second unit started across the lock"
            with get_db() as con:
                today = notebook.spend_today(con)
            assert today["calls"] == 1
            assert today["cost"] == pytest.approx(0.0009), "the paid reply's cost was lost"
            assert notebook.list_entries(chat_id), "the paid notes were lost"
            assert running_rows(chat_id) == 0
            row = job_of(chat_id)
            assert row["state"] != "running"
            assert row["last_error"] == "vault_locked"
        finally:
            await notebook_worker.stop()

    @pytest.mark.anyio
    async def test_a_lock_while_the_call_is_open_does_not_discard_it(
            self, db, monkeypatch) -> None:
        """T-08, the half the row above cannot see.

        There the lock lands after the provider answered, so the write is
        already in a thread and a thread finishes whatever the coroutine
        awaiting it does. Here the request is STILL OPEN when the lock
        arrives, which is the only window the loop's tracked, shielded unit
        exists for: cancelling the loop must not tear down a call that is
        already being billed. Awaiting `_handle` directly instead of
        shielding a tracked task loses the reply and everything it paid for.
        """
        chat_id = make_chat_on_db(45)
        choose(chat_id)
        entered, hold = asyncio.Event(), asyncio.Event()
        calls = provider(monkeypatch, hold=hold, entered=entered)
        assert notebook_worker.worker.task is None, "ground: nothing running"
        notebook_worker.start()
        try:
            out = await notebook_worker.worker.backfill(chat_id, "all")
            assert out["started"] is True
            loop_task = notebook_worker.worker._backfill_task
            assert loop_task is not None, "ground: the job loop is running"
            await asyncio.wait_for(entered.wait(), 5)
            # GROUND, and the positive control for the two absences is the
            # same two reads after `hold` is released: nothing has been
            # written yet because the reply has not arrived, and the tail of
            # this test is what proves those reads can see a note and a cost
            # when there is one.
            assert notebook.list_entries(chat_id) == [], "ground: no notes yet"
            with get_db() as con:
                assert notebook.spend_today(con)["cost"] == 0, "ground: unpaid"

            # The lock, with the request open. `quiesce` cancels the loop and
            # then holds the grace period for the unit, so this does not
            # return until the shielded unit settles.
            standing_down = asyncio.create_task(notebook_worker.quiesce())
            for _ in range(500):
                if loop_task.done():
                    break
                await asyncio.sleep(0.01)
            assert loop_task.done(), "the job loop was never cancelled"
            # Only now does the provider answer. Under production code the
            # unit survived the cancellation and is still waiting here.
            # THE OTHER HALF OF THE SAME MECHANISM. The shield keeps the
            # unit alive; the TRACKING is what makes `quiesce` wait for it.
            # Drop only `self._track(unit)` and the unit still finishes -
            # but `_await_settle` has nothing registered to wait for, so
            # this returns here, with the request open, and its caller
            # clears the vault key on its very next line.
            assert not standing_down.done(), (
                "quiesce returned while the paid call was still open")
            hold.set()
            await asyncio.wait_for(standing_down, 20)
            vault_state.clear_key()
            vault_state.set_key(TEST_VAULT_KEY)
            await asyncio.sleep(0.01)

            assert len(calls) == 1, "a second unit started across the lock"
            with get_db() as con:
                today = notebook.spend_today(con)
            assert today["cost"] == pytest.approx(0.0001), (
                "the open call was cancelled with the loop and its cost lost")
            assert notebook.list_entries(chat_id), (
                "the reply arrived after the lock and was thrown away")
            assert running_rows(chat_id) == 0, "a running row was left behind"
        finally:
            hold.set()
            await notebook_worker.stop()


def make_chat_on_db(count: int) -> int:
    """`seed` needs the client; this is its `db`-only twin."""
    with get_db() as con:
        con.execute("INSERT INTO characters (name, description) VALUES ('C', 'd')")
        cid = con.execute("SELECT MAX(id) FROM characters").fetchone()[0]
        con.execute("INSERT INTO chats (character_id, title) VALUES (?, 't')", (cid,))
        chat_id = con.execute("SELECT MAX(id) FROM chats").fetchone()[0]
        for i in range(count):
            con.execute(
                "INSERT INTO messages (chat_id, role, content, active) "
                "VALUES (?,?,?,1)",
                (chat_id, "user" if i % 2 == 0 else "assistant",
                 f"line {i}: her brother owns the mill"))
    return chat_id


# ── T-09: the same fact from two units enters once ────────────────────────

class TestDedup:
    @pytest.mark.parametrize("auto", ["0", "1"])
    @pytest.mark.anyio
    async def test_the_second_unit_sees_the_first_proposal_and_writes_no_twin(
            self, client, chat, monkeypatch, auto) -> None:
        """T-09. With review ON the first note is `proposed`, which is
        exactly the branch of `live` this guards; with review OFF it is the
        ordinary case."""
        set_setting(config.SETTING_NOTEBOOK_AUTO_ACCEPT, auto)
        calls = provider(monkeypatch, facts=lambda n: [fact()])

        await run_and_wait(chat)

        assert len(calls) >= 2, "ground: two units ran"
        texts = [e["text"] for e in notebook.list_entries(chat)]
        assert texts == ["Her brother owns the mill."]
        second = calls[1]["prompt"]
        assert "Her brother owns the mill." in second, (
            "the second unit was not shown the first unit's proposal")
        if auto == "0":
            assert "(unreviewed suggestion) Her brother owns the mill." in second


# ── T-10, T-37: undo ──────────────────────────────────────────────────────

class TestUndo:
    @pytest.mark.parametrize("scope", ["next", "all", "range"])
    @pytest.mark.anyio
    async def test_it_removes_exactly_that_jobs_notes(
            self, client, chat, monkeypatch, scope) -> None:
        """T-10, in every mode. Pinned and edited notes stay and are
        counted; a note from another source, and a note from ANOTHER JOB,
        are untouched.

        Parametrized because T-10 and KARAR-D both say the mode does not
        matter ("pinli ve duzenlenmis notlar hicbir kipte etkilenmez"), and
        the row used to run `all` alone: undo is reached over `next` and
        `range` in the sibling classes, but the assertion that a pinned note
        SURVIVES was made in one mode only.

        The other job's note is what makes `WHERE job_id = ?` load-bearing
        here. With one job and one reader note in the fixture, widening the
        filter to `WHERE job_id IS NOT NULL` changed nothing and this row
        stayed green while the undo reached every other run's work.
        """
        provider(monkeypatch)
        mine = notebook.create_entry(chat, "Written by the reader.")
        # ANOTHER JOB, on another chat's worth of work, before this one.
        # Its note carries its own job id, so the filter under test is the
        # only thing keeping it out of the doomed set.
        earlier = await run_and_wait(chat, "next")
        earlier_job = earlier["job_id"]
        earlier_notes = {e["id"] for e in notebook.list_entries(chat)
                         if e["provenance"] == notebook.PROV_MODEL}
        assert earlier_notes, "ground: the earlier job wrote something"
        monkeypatch.setattr(openrouter, "complete", None)
        # THREE facts a call, so every scope reaches the three notes this
        # row needs - one pinned, one edited, one removed. `next` reads a
        # single unit by definition, so a one-fact stub would make the
        # parametrisation test nothing in that mode.
        provider(monkeypatch, facts=lambda n: [
            fact(text=f"Fact {n}.{i} from the second job.") for i in range(3)])
        if scope == "range":
            with get_db() as con:
                lo, hi = notebook.message_span(con, chat)
            out = await run_and_wait(chat, "range", lo, hi)
        else:
            out = await run_and_wait(chat, scope)
        job_id = out["job_id"]
        assert job_id != earlier_job, "ground: two different jobs"

        wrote = [e for e in notebook.list_entries(chat)
                 if e["provenance"] == notebook.PROV_MODEL
                 and e["id"] not in earlier_notes]
        assert len(wrote) >= 3, f"ground: the job wrote notes, {wrote}"
        with get_db() as con:
            tagged = con.execute(
                "SELECT COUNT(*) FROM notebook_entries WHERE job_id = ?",
                (job_id,)).fetchone()[0]
        assert tagged == len(wrote), "ground: the rows carry the job id"
        notebook.update_entry(wrote[0]["id"], chat, pinned=True)
        notebook.update_entry(wrote[1]["id"], chat, text="Edited by the reader.")

        r = client.post(f"{API}/extract/undo/{job_id}?chat_id={chat}")

        assert r.status_code == 200, r.text
        assert r.json() == {"job_id": job_id, "removed": len(wrote) - 2,
                            "kept_pinned": 1, "kept_edited": 1}
        left = {e["id"] for e in notebook.list_entries(chat)}
        for gone in wrote[2:]:
            assert gone["id"] not in left
        assert {mine["id"], wrote[0]["id"], wrote[1]["id"]} <= left
        # THE OTHER RUN'S WORK, which is the half "exactly that job's notes"
        # is really about. `mine` only proves the reader's own notes are out
        # of reach; these prove an earlier JOB's are.
        assert earlier_notes <= left, (
            "the undo reached a note another job wrote")
        # And the earlier job can still be undone on its own afterwards.
        again = client.post(f"{API}/extract/undo/{earlier_job}?chat_id={chat}")
        assert again.status_code == 200, again.text
        assert again.json()["removed"] == len(earlier_notes)

    @pytest.mark.anyio
    async def test_another_chats_job_is_not_found_and_untouched(
            self, client, chat, monkeypatch) -> None:
        """T-37. Same 404 as a missing job, on purpose."""
        provider(monkeypatch)
        out = await run_and_wait(chat, "next")
        other = make_chat(client, make_character(client))
        before = notebook.list_entries(chat)
        assert before, "ground: there is something to remove"

        r = client.post(f"{API}/extract/undo/{out['job_id']}?chat_id={other}")

        assert r.status_code == 404, r.text
        assert r.json()["detail"] == "notebook_job_not_found"
        assert notebook.list_entries(chat) == before
        assert client.post(f"{API}/extract/undo/{out['job_id']}").status_code == 422
        # GROUND: the same call from its own chat succeeds.
        r = client.post(f"{API}/extract/undo/{out['job_id']}?chat_id={chat}")
        assert r.status_code == 200 and r.json()["removed"] == len(before)

    @pytest.mark.anyio
    async def test_a_running_job_cannot_be_undone(
            self, client, chat, monkeypatch) -> None:
        hold, entered = asyncio.Event(), asyncio.Event()
        provider(monkeypatch, hold=hold, entered=entered)
        w = notebook_worker.worker
        out = await w.backfill(chat, "all")
        await asyncio.wait_for(entered.wait(), 5)
        try:
            r = client.post(f"{API}/extract/undo/{out['job_id']}?chat_id={chat}")
            assert r.status_code == 409, r.text
            assert r.json()["detail"] == "notebook_backfill_running"
        finally:
            w._backfill_stop = True
            hold.set()
            await w.wait_for_backfill()


# ── T-38, T-40: scope next, and a chat with nothing unread ────────────────

class TestScopes:
    @pytest.mark.anyio
    async def test_next_reads_one_unit_like_the_sweep(
            self, client, chat, monkeypatch) -> None:
        """T-38."""
        calls = provider(monkeypatch)
        out = await run_and_wait(chat, "next")
        assert out["calls_planned"] == 1
        assert len(calls) == 1
        assert unread(chat) is not None
        assert job_of(chat)["planned_calls"] == 1

    def test_a_fully_read_chat_writes_no_job(self, client, chat, monkeypatch):
        """T-40. No job row for a job with nothing to undo."""
        calls = provider(monkeypatch)
        with get_db() as con:
            lo, hi = notebook.message_span(con, chat)
            con.execute("BEGIN IMMEDIATE")
            notebook.commit_extraction(con, work_key="all-read", chat_id=chat,
                                       from_id=lo, to_id=hi, proposals=[])
        before = spend()

        # AND THE PREVIEW SAYS SO, before the press. Same gap as
        # `model_not_extractable`: the token was asserted as a refusal
        # reason and never as a `blocked_by`, so a preview that dropped it
        # offered Start on a chat with nothing left to read.
        preview = client.get(f"{API}/extract/plan/{chat}?scope=all").json()
        assert preview["ready"] is False
        assert "nothing_unread" in preview["blocked_by"]
        assert preview["messages_unread"] == 0

        r = client.post(f"{API}/extract/run/{chat}", json={"scope": "all"})

        assert r.status_code == 200
        assert r.json() == {"started": False, "reason": "nothing_unread"}
        assert job_count(chat) == 0
        assert spend() == before and calls == []

    @pytest.mark.anyio
    async def test_a_range_reads_again_what_was_read(
            self, client, chat, monkeypatch) -> None:
        """Mode 3: the chosen stretch is read even when it is covered, and
        the notes it made before are renewed through `supersedes`."""
        calls = provider(monkeypatch)
        await run_and_wait(chat)
        assert unread(chat) is None, "ground: everything is read"
        with get_db() as con:
            lo, _ = notebook.message_span(con, chat)
        old = notebook.list_entries(chat)[0]
        # The model names the old note and replaces it.
        monkeypatch.setattr(openrouter, "complete", None)
        calls2 = provider(monkeypatch, facts=lambda n: [
            fact(text="Her brother sold the mill.", supersedes=0)])

        # The first unit's notes carry its LAST message as their source, so
        # the range has to reach that message for the read to be shown them
        # (IP-01a: a read is shown only the notes from messages up to where
        # it ends). Twenty messages is the batch, so lo + BATCH - 1 is that message.
        out = await run_and_wait(chat, "range", lo, lo + BATCH - 1)

        assert out["started"] is True and len(calls2) == 1
        assert job_of(chat)["state"] == "done"
        rows = {e["id"]: e for e in notebook.list_entries(chat)}
        assert rows[old["id"]]["retired_at"] is not None
        assert "Her brother sold the mill." in [e["text"] for e in rows.values()]
        assert len(calls) == 3, "the first run's calls are untouched"

    def test_a_range_outside_the_chat_is_400(self, client, chat) -> None:
        r = client.get(f"{API}/extract/plan/{chat}?scope=range"
                       "&from_message_id=5&to_message_id=2")
        assert r.status_code == 400 and r.json()["detail"] == "notebook_range_invalid"
        r = client.post(f"{API}/extract/run/{chat}",
                        json={"scope": "range", "from_message_id": 1,
                              "to_message_id": 99999})
        assert r.status_code == 400 and r.json()["detail"] == "notebook_range_invalid"
        assert client.get(f"{API}/extract/plan/{chat}").status_code == 422
        assert client.get(f"{API}/extract/plan/999999?scope=all").status_code == 404

    def test_a_backwards_range_on_the_RUN_route_is_400_too(
            self, client, chat) -> None:
        """The run route's OWN guard, which the row above never reaches.

        `_check_range` has two callers and only one of them was measured:
        the 400 asserted on POST above sends 1..99999, which `_plan_backfill`
        rejects for being outside the chat long before the route's own check
        matters. A backwards range is the one shape only `_check_range` can
        refuse, and it was tested on the PLAN route alone. So the route's
        two-namespace contract - an error code answers 4xx with `detail`, a
        refusal answers 200 with `{started: false, reason}` - could be broken
        on this route by answering `notebook_range_invalid` as a 200 refusal,
        and nothing went red. It costs the strip a token it has no sentence
        for.
        """
        before = spend()

        r = client.post(f"{API}/extract/run/{chat}",
                        json={"scope": "range", "from_message_id": 5,
                              "to_message_id": 2})

        assert r.status_code == 400, r.text
        assert r.json()["detail"] == "notebook_range_invalid"
        # An ERROR, not a refusal: the two namespaces do not overlap.
        assert "started" not in r.json()
        assert r.json()["detail"] not in notebook_worker.SWEEP_REFUSALS
        assert job_count(chat) == 0 and spend() == before
        # GROUND: the same route with a range that IS in the chat starts,
        # so the 400 above is the range and not this route refusing
        # everything.
        with get_db() as con:
            lo, hi = notebook.message_span(con, chat)
        ok = client.post(f"{API}/extract/run/{chat}",
                         json={"scope": "range", "from_message_id": lo,
                               "to_message_id": hi})
        assert ok.status_code == 200, ok.text


class TestARangeEndingOnAMessageThatIsGone:
    """A range's end is an id a READER typed, and an id is not a message.

    A message that was edited away or deleted leaves its id behind: still
    inside the span `_check_range` checks against, so the job starts, and
    carried by no row the planner will ever select, so the last unit
    records the last ACTIVE id below it and the loop's cursor stops short
    of the end it was given. The job then read everything it could and
    billed exactly what it planned, and the row said `failed` /`refused`
    and the panel said "Stopped after a failure".

    Nothing is lost and nothing is paid twice by that, which is why it is
    the sentence rather than the money that is wrong - and the sentence is
    what this whole strip is for. `JOB_STATES` names the word: `done` when
    nothing unread is left.
    """

    @pytest.mark.anyio
    async def test_a_range_ending_on_a_message_that_is_gone_closes_done(
            self, client, chat, monkeypatch) -> None:
        calls = provider(monkeypatch)
        with get_db() as con:
            ids = [r[0] for r in con.execute(
                "SELECT id FROM messages WHERE chat_id = ? AND active = 1 "
                "ORDER BY id", (chat,)).fetchall()]
        lo, gone = ids[0], ids[len(ids) // 2]
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute("UPDATE messages SET active = 0 WHERE id = ?", (gone,))
        with get_db() as con:
            span_lo, span_hi = notebook.message_span(con, chat)
        # GROUND for the setup: the id is still inside the span a range is
        # checked against, so this is a job that really starts rather than
        # a 400 the route would have answered.
        assert span_lo == lo and lo < gone < span_hi, (span_lo, gone, span_hi)

        out = await run_and_wait(chat, "range", lo, gone)

        row = job_of(chat)
        assert out["started"] is True
        # GROUND: it read, and it read everything it set out to read.
        assert row["planned_calls"] >= 1 and len(calls) == row["spent_calls"]
        assert row["spent_calls"] == row["planned_calls"]
        with get_db() as con:
            assert notebook.unread_count(con, chat, lo, gone) == 0, (
                "the read finished: nothing active is left in the stretch")
        assert row["state"] == "done", (
            "a read that finished and billed what it planned was closed as "
            f"{row['state']} ({row['last_error']})")
        assert row["last_error"] is None

    @pytest.mark.anyio
    async def test_POSITIVE_a_range_whose_call_cannot_land_still_says_so(
            self, client, chat, monkeypatch) -> None:
        """The control for the row above, and the reason its `last_error is
        None` means anything: `done` is not the new answer to everything.
        A stretch that is entirely live and whose call cannot land closes
        on a terminal state with a reason out of `JOB_LAST_ERRORS`, so the
        completion test still discriminates."""
        async def boom(*a, **kw):
            raise openrouter.OpenRouterError("openrouter_unreachable")

        monkeypatch.setattr(openrouter, "complete", boom)
        with get_db() as con:
            lo, hi = notebook.message_span(con, chat)

        out = await run_and_wait(chat, "range", lo, hi)

        row = job_of(chat)
        assert out["started"] is True, "ground: the job was allowed to start"
        assert row["state"] != "done", (
            "a range nobody could read was closed as finished")
        assert row["state"] in notebook.JOB_STATES
        assert row["last_error"] in notebook.JOB_LAST_ERRORS
        with get_db() as con:
            assert notebook.unread_count(con, chat, lo, hi) > 0, (
                "ground: the stretch really is still unread")


class TestTheEndOfARangeIsInsideIt:
    """W1F-04. The cursor's own boundary, and it is `<=` and not `<`.

    `_backfill_cursor` asks whether an ACTIVE message is left above the
    cursor and INSIDE the stretch, with the planner's predicate:
    `id > cursor AND id <= to_message_id`. The end of a range is a message
    a reader chose, so it is part of what they asked for - turn that `<=`
    into `<` and a stretch whose last unread message IS the end id answers
    "nothing left" before anything is read. The job then closes `done`
    having spent nothing and read nothing, which is the worst shape a
    wrong answer can take here: the panel says it finished.

    123 rows in this file stayed green under that change, because every
    other range in them has something after its end or something before
    it. This is the one that does not.
    """

    @pytest.mark.anyio
    async def test_a_range_that_is_only_its_own_end_is_read(
            self, client, chat, monkeypatch) -> None:
        calls = provider(monkeypatch)
        with get_db() as con:
            ids = [r[0] for r in con.execute(
                "SELECT id FROM messages WHERE chat_id = ? AND active = 1 "
                "ORDER BY id", (chat,)).fetchall()]
        end = ids[-1]
        # GROUND for the setup: the stretch is exactly one message, that
        # message is the END of the range, and nobody has read it.
        with get_db() as con:
            assert notebook.unread_count(con, chat, end, end) == 1, (
                "ground: the end message is unread before the job runs")

        out = await run_and_wait(chat, "range", end, end)

        row = job_of(chat)
        assert out["started"] is True
        # It really read: a job that answered "nothing left" would close
        # with no call at all, and `done` on its own cannot tell the two
        # apart.
        assert row["planned_calls"] >= 1, (
            "the job planned nothing, so the end of the range was never "
            "inside it")
        assert row["spent_calls"] == row["planned_calls"] == len(calls)
        with get_db() as con:
            assert notebook.unread_count(con, chat, end, end) == 0, (
                "the range closed with its own end message still unread")
        assert row["state"] == "done"
        assert row["last_error"] is None

    @pytest.mark.anyio
    async def test_POSITIVE_a_stretch_above_the_end_is_left_alone(
            self, client, chat, monkeypatch) -> None:
        """The control for the row above: `<=` includes the end and stops
        there. A range that ends one short of the last message must leave
        that last message unread, or "the end is inside the range" would
        just be "the cursor reads past its instructions"."""
        provider(monkeypatch)
        with get_db() as con:
            ids = [r[0] for r in con.execute(
                "SELECT id FROM messages WHERE chat_id = ? AND active = 1 "
                "ORDER BY id", (chat,)).fetchall()]
        lo, end, beyond = ids[0], ids[-2], ids[-1]

        out = await run_and_wait(chat, "range", lo, end)

        row = job_of(chat)
        assert out["started"] is True and row["state"] == "done"
        with get_db() as con:
            assert notebook.unread_count(con, chat, lo, end) == 0, (
                "ground: the stretch that WAS asked for is read")
            assert notebook.unread_count(con, chat, beyond, beyond) == 1, (
                "the job read a message above the end it was given")


# -- Decisions 30 a and 31 a: there is no automatic whole-chat read ---------

async def offer_and_drain(w: notebook_worker.Worker, chat_id: int) -> None:
    task = asyncio.create_task(w.run())
    w.queue.put_nowait(chat_id)
    await asyncio.wait_for(w.queue.join(), timeout=5)
    await w.wait_for_backfill()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def short_chat(total: int) -> int:
    """A ready chat holding exactly `total` active messages, none read.
    `make_chat_on_db` writes no greeting, so `total` is the unread count."""
    chat_id = make_chat_on_db(total)
    choose(chat_id)
    return chat_id


class TestModeOneIsGone:
    """Mode 1 read a chat with no notes WHOLE, as a job the worker started
    on its own, the first time that chat reached the threshold: up to fifty
    calls nobody asked for, and on a busy afternoon (another chat's job, the
    job ceiling spent) not at all, sometimes for good. Decisions 30 a and
    31 a removed it. A chat with no notes is read by the ordinary reader:
    from its first message while it holds at most `from_start_edge()` unread
    messages (forty), from its newest twenty when it holds more. Its older
    history is read only when the reader starts a job from the strip, after
    the preview names the price."""

    @pytest.mark.anyio
    async def test_a_new_noteless_chat_is_read_by_the_ordinary_reader_at_twenty(
            self, client, monkeypatch) -> None:
        """3.9, 3.19, 3.20. Nineteen unread: no call. Twenty: one call, from
        the chat's first message, and no job row either time."""
        calls = provider(monkeypatch)
        threshold = config.NOTEBOOK_EXTRACT_EVERY_TURNS
        chat_id = short_chat(threshold - 1)
        w = notebook_worker.Worker()
        w.queue = asyncio.Queue(maxsize=8)

        await offer_and_drain(w, chat_id)
        # GROUND: below the threshold nothing is sent and nothing is planned.
        assert calls == [] and extraction_rows(chat_id) == []
        assert job_count(chat_id) == 0

        with get_db() as con:
            con.execute("INSERT INTO messages (chat_id, role, content, active) "
                        "VALUES (?, 'assistant', 'the twentieth', 1)",
                        (chat_id,))
        await offer_and_drain(w, chat_id)

        assert len(calls) == 1, "the twentieth message was not read"
        assert job_count(chat_id) == 0, (
            "a job was started with nobody asking for one")
        first = message_ids_of(chat_id)[0]
        with get_db() as con:
            lo = con.execute(
                "SELECT MIN(from_message_id) FROM notebook_extractions "
                "WHERE chat_id = ? AND status = 'done'", (chat_id,)).fetchone()[0]
        assert lo == first, "the new chat was not read from its beginning"

    @pytest.mark.anyio
    async def test_a_short_noteless_chat_is_read_while_another_chat_runs_a_job(
            self, client, monkeypatch) -> None:
        """3.9. The first of mode 1's three blockers: another chat's job held
        the one lock, so this chat's turn was not read at all. The ordinary
        reader does not take that lock and reads it."""
        hold, entered = asyncio.Event(), asyncio.Event()
        calls: list[int] = []

        async def the_job_call_waits(*a, **kw):
            calls.append(1)
            if len(calls) == 1:
                entered.set()
                await hold.wait()
            return {"id": f"gen-{len(calls)}", "choices": [{
                "finish_reason": "stop",
                "message": {"content": json.dumps({"facts": [fact()]})}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                          "cost": 0.0001}}

        monkeypatch.setattr(openrouter, "complete", the_job_call_waits)
        other = make_chat_on_db(45)
        choose(other)
        chat_id = short_chat(config.NOTEBOOK_EXTRACT_EVERY_TURNS)
        w = notebook_worker.worker
        w.queue = asyncio.Queue(maxsize=8)
        try:
            assert (await w.backfill(other, "all"))["started"] is True
            await asyncio.wait_for(entered.wait(), 5)
            assert w._sweeping is True, "ground: the job holds the lock"

            task = asyncio.create_task(w.run())
            w.queue.put_nowait(chat_id)
            await asyncio.wait_for(w.queue.join(), timeout=5)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

            assert len(calls) == 2, "the chat was not read while a job ran"
            assert notebook.list_entries(chat_id), "and wrote nothing"
            assert job_count(chat_id) == 0
        finally:
            hold.set()
            await notebook_worker.stop()

    @pytest.mark.parametrize("extra, from_start", [(0, True), (1, False)])
    def test_forty_unread_reads_from_the_start_and_forty_one_reads_the_newest_twenty(
            self, client, extra, from_start) -> None:
        """30 a and 31 a, at the edge. The edge is the named constant, and
        both sides of it are asked."""
        edge = notebook_worker.from_start_edge()
        assert edge == (notebook_worker.FROM_START_EDGE_FACTOR
                        * config.NOTEBOOK_EXTRACT_EVERY_TURNS)
        chat_id = short_chat(edge + extra)
        ids = message_ids_of(chat_id)
        w = notebook_worker.Worker()

        plan = notebook_worker._plan_work(
            chat_id, EXTRACT, "en", w.trigger_threshold_for(chat_id),
            w.batch_size_for(chat_id), first_read_edge=edge)

        assert plan is not None
        if from_start:
            assert plan["from_id"] == ids[0], (
                f"{edge} unread were not read from the first message")
        else:
            assert plan["to_id"] == ids[-1]
            assert plan["from_id"] == ids[-config.NOTEBOOK_EXTRACT_EVERY_TURNS], (
                f"{edge + 1} unread were not read from the newest twenty")

    @pytest.mark.anyio
    async def test_a_long_noteless_chat_starts_no_job_by_itself(
            self, client, chat, monkeypatch) -> None:
        """31 a. The fixture chat holds forty-six unread messages and no
        note: the offer reads its newest twenty, in one call, and starts no
        job. POSITIVE CONTROL: the reader's own `scope=all` press on the same
        chat still makes the job, so the absence above is about the offer."""
        calls = provider(monkeypatch)
        ids = message_ids_of(chat)
        assert len(ids) > notebook_worker.from_start_edge(), (
            "ground: this chat is past the edge")
        w = notebook_worker.Worker()
        w.queue = asyncio.Queue(maxsize=8)

        await offer_and_drain(w, chat)

        assert job_count(chat) == 0, "the offer started a whole-chat job"
        assert len(calls) == 1
        rows = extraction_rows(chat)
        assert len(rows) == 1
        with get_db() as con:
            lo, hi = con.execute(
                "SELECT from_message_id, to_message_id FROM "
                "notebook_extractions WHERE chat_id = ?", (chat,)).fetchone()
        assert (lo, hi) == (ids[-config.NOTEBOOK_EXTRACT_EVERY_TURNS], ids[-1])

        out = await run_and_wait(chat, "all")
        assert out["started"] is True
        assert job_of(chat)["scope"] == "all"

    @pytest.mark.anyio
    async def test_a_chat_with_one_note_takes_the_ordinary_path(
            self, client, chat, monkeypatch) -> None:
        """One note, no job; the incremental path ran. Unchanged by the
        decisions, kept as the other side of the rows above."""
        calls = provider(monkeypatch)
        notebook.create_entry(chat, "One note already.")
        w = notebook_worker.Worker()
        w.queue = asyncio.Queue(maxsize=8)

        await offer_and_drain(w, chat)

        assert job_count(chat) == 0
        assert len(calls) == 1, "the ordinary path made its one call"
        assert extraction_rows(chat), "and left its extraction row"

    @pytest.mark.anyio
    async def test_the_gate_still_applies(
            self, client, chat, monkeypatch) -> None:
        calls = provider(monkeypatch)
        choose(chat, extract=None)
        w = notebook_worker.Worker()
        w.queue = asyncio.Queue(maxsize=8)

        await offer_and_drain(w, chat)

        assert job_count(chat) == 0 and calls == [] and spend() == 0

    @pytest.mark.anyio
    async def test_the_reserve_is_there_for_a_new_chat(
            self, client, monkeypatch) -> None:
        """3.23. The ten calls kept back from jobs are for the day's
        conversations, and a new chat is one of them now: with the jobs'
        ceiling spent, its twentieth message is still read. GROUND: at the
        day's own ceiling it is not, so the read above is the reserve and
        not a door with no ceiling at all."""
        calls = provider(monkeypatch)
        cap = notebook_worker._backfill_cap()
        with get_db() as con:
            for _ in range(cap):
                notebook.claim_call(con, config.NOTEBOOK_DAILY_CALL_CAP)
        assert spend() == cap, "ground: the jobs' ceiling is used up"
        chat_id = short_chat(config.NOTEBOOK_EXTRACT_EVERY_TURNS)
        w = notebook_worker.Worker()
        w.queue = asyncio.Queue(maxsize=8)

        await offer_and_drain(w, chat_id)
        assert len(calls) == 1, "a new chat was not read from the reserve"

        with get_db() as con:
            while notebook.spend_today(con)["calls"] < config.NOTEBOOK_DAILY_CALL_CAP:
                notebook.claim_call(con, config.NOTEBOOK_DAILY_CALL_CAP)
        second = short_chat(config.NOTEBOOK_EXTRACT_EVERY_TURNS)
        await offer_and_drain(w, second)
        assert len(calls) == 1, "the day's own ceiling did not hold"


def message_ids_of(chat_id: int) -> list[int]:
    with get_db() as con:
        return [r[0] for r in con.execute(
            "SELECT id FROM messages WHERE chat_id = ? AND active = 1 "
            "ORDER BY id", (chat_id,)).fetchall()]


# ── A price nobody could read is not a price of zero ──────────────────────

class TestThePreviewNeverShowsAFakeZero:
    def test_an_unreadable_price_is_unknown_rather_than_about_nothing(
            self, client, chat, monkeypatch) -> None:
        """`fetch_extraction_models` normalises a price it could not read -
        absent `pricing`, a string that will not parse - to 0.0 at the one
        place the wire unit is known. That is a number, and the preview
        multiplied it into "about $0.00000" for a model whose price is
        simply not known."""
        monkeypatch.setattr(openrouter, "_zdr_cache", (time.monotonic(), [
            {"id": EXTRACT, "prompt_price": 0.0, "completion_price": 2.0,
             "provider": "p", "context_length": 8000, "endpoints": 1}]))

        body = client.get(f"{API}/extract/plan/{chat}?scope=all").json()

        assert body["prompt_price"] is None
        assert body["cost_estimate"] is None
        # POSITIVE CONTROL: a readable price comes through as a number and
        # as a cost, so the two Nones above are about this row's price and
        # not about this route answering None for everything. Since 3.17
        # the cost prices the reply as well, so the row carries both
        # halves; the half that is missing is the next test's subject.
        monkeypatch.setattr(openrouter, "_zdr_cache", (time.monotonic(), [
            {"id": EXTRACT, "prompt_price": 0.5, "completion_price": 2.0,
             "provider": "p", "context_length": 8000, "endpoints": 1}]))

        body = client.get(f"{API}/extract/plan/{chat}?scope=all").json()

        assert body["prompt_price"] == 0.5
        assert body["cost_estimate"] is not None and body["cost_estimate"] > 0


class TestAJobWalksFromTheOldestEnd:
    """3.11. A job over long messages used to keep the NEWEST lines of each
    batch that fit the character ceiling, as the ordinary reader does. For
    a job that is the wrong end: its purpose is the history, and the
    stretch in front of what it kept was left for a later unit that - on
    the job's own coverage walk - could stop the job before ever reading
    it. A job now keeps the OLDEST lines that fit and walks forward."""

    PAD = "x" * 980
    FIRST = "line 0: her brother owns the mill"

    def _long_chat(self) -> int:
        chat_id = make_chat_on_db(0)
        with get_db() as con:
            for i in range(BATCH):
                con.execute(
                    "INSERT INTO messages (chat_id, role, content, active) "
                    "VALUES (?,?,?,1)",
                    (chat_id, "user" if i % 2 == 0 else "assistant",
                     f"line {i}: her brother owns the mill {self.PAD}"))
        choose(chat_id)
        return chat_id

    def _provider(self, monkeypatch) -> list[str]:
        prompts: list[str] = []

        async def stub(*a, **kw):
            prompt = a[0][1]["content"]
            prompts.append(prompt)
            facts = ([fact(text="The mill at line 0.", evidence=self.FIRST)]
                     if self.FIRST in prompt else [])
            return {"id": f"gen-{len(prompts)}", "choices": [{
                "finish_reason": "stop",
                "message": {"content": json.dumps({"facts": facts})}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                          "cost": 0.0001}}

        monkeypatch.setattr(openrouter, "complete", stub)
        return prompts

    @pytest.mark.anyio
    async def test_a_job_over_twenty_long_messages_reads_the_first_message(
            self, client, monkeypatch) -> None:
        prompts = self._provider(monkeypatch)
        chat_id = self._long_chat()
        first = message_ids_of(chat_id)[0]

        out = await run_and_wait(chat_id, "all")

        assert out["started"] is True
        with get_db() as con:
            assert notebook.unread_count(con, chat_id, first, first) == 0, (
                "the job ended with the chat's first message never read")
            assert notebook.unread_count(con, chat_id) == 0
        assert job_of(chat_id)["state"] == "done"
        # GROUND: the messages really are too long for one call, so the
        # rows above are about walking a chat in pieces.
        assert len(prompts) >= 2, (
            "ground: twenty long messages do not fit one call")
        assert "The mill at line 0." in [
            e["text"] for e in notebook.list_entries(chat_id)], (
            "and no note came from it")

    @pytest.mark.anyio
    async def test_GROUND_the_ordinary_reader_keeps_the_newest_end(
            self, client, monkeypatch) -> None:
        """The same chat, the ordinary reader, one call: it keeps the newest
        lines (decision 68 a, which would change that, is a later heading),
        so the first message is not in its read. The row above is therefore
        about the job's end, not about a chat where everything fits."""
        prompts = self._provider(monkeypatch)
        chat_id = self._long_chat()
        first = message_ids_of(chat_id)[0]

        await notebook_worker.Worker()._handle(chat_id)

        assert len(prompts) == 1 and self.FIRST not in prompts[0]
        with get_db() as con:
            assert notebook.unread_count(con, chat_id, first, first) == 1


class TestAJobHasTheChatToItself:
    """3.12 and 3.13, which are one rule seen from two sides: a job reads
    the chat as it stood when the job started, and the ordinary reader
    leaves that chat alone until the job ends."""

    @pytest.mark.anyio
    async def test_the_ordinary_reader_leaves_a_chat_alone_while_its_job_runs(
            self, client, chat, monkeypatch) -> None:
        """3.12. The fixture chat is long and never read, so the ordinary
        reader would plan its NEWEST twenty - a different stretch from the
        job's first unit, so no work key collides and nothing else stops
        it. It used to read that stretch between the job's units; the job's
        next unit then found it covered, and those notes carried no job id,
        out of reach of "undo this run"."""
        hold, entered = asyncio.Event(), asyncio.Event()
        calls: list[int] = []

        async def only_the_job_call_waits(*a, **kw):
            # The job's first call is held open; anything after it answers
            # at once, so a reader that does go ahead shows up as a second
            # call rather than as a hang.
            calls.append(1)
            if len(calls) == 1:
                entered.set()
                await hold.wait()
            return {"id": f"gen-{len(calls)}", "choices": [{
                "finish_reason": "stop",
                "message": {"content": json.dumps({"facts": []})}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                          "cost": 0.0001}}

        monkeypatch.setattr(openrouter, "complete", only_the_job_call_waits)
        w = notebook_worker.worker
        try:
            assert (await w.backfill(chat, "all"))["started"] is True
            await asyncio.wait_for(entered.wait(), 5)
            assert len(calls) == 1, "ground: the job's first unit is out"

            # The SAME worker, because it is one process-wide reader: the
            # offer queue and the job are both driven by the singleton.
            answered = await asyncio.wait_for(w._handle(chat), 5)

            assert answered is None and len(calls) == 1, (
                "the ordinary reader read a chat whose job was running")
        finally:
            hold.set()
            await w.wait_for_backfill()

        # GROUND: with the job over, the same reader on the same chat is
        # not refused by this rule - one more reply's worth of messages and
        # it reads.
        for i in range(BATCH):
            with get_db() as con:
                con.execute("INSERT INTO messages (chat_id, role, content, "
                            "active) VALUES (?, 'user', ?, 1)",
                            (chat, f"after the job {i}"))
        before = len(calls)
        await w._handle(chat)
        assert len(calls) == before + 1, (
            "the reader stayed away after the job ended")

    @pytest.mark.anyio
    async def test_a_job_reads_up_to_the_message_that_was_newest_when_it_started(
            self, client, chat, monkeypatch) -> None:
        """3.13. A message written while the job reads is not the job's:
        the job used to go on to it, paying a whole unit for one or two new
        messages. It stops at the end it started with, and says `done`."""
        hold, entered = asyncio.Event(), asyncio.Event()
        calls = provider(monkeypatch, hold=hold, entered=entered)
        w = notebook_worker.worker
        ids = message_ids_of(chat)
        try:
            assert (await w.backfill(chat, "all"))["started"] is True
            await asyncio.wait_for(entered.wait(), 5)
            with get_db() as con:
                con.execute("INSERT INTO messages (chat_id, role, content, "
                            "active) VALUES (?, 'user', 'written during the "
                            "job', 1)", (chat,))
                late = con.execute("SELECT MAX(id) FROM messages WHERE "
                                   "chat_id = ?", (chat,)).fetchone()[0]
        finally:
            hold.set()
            await w.wait_for_backfill()

        row = job_of(chat)
        assert row["state"] == "done", row
        with get_db() as con:
            # GROUND: everything the chat held when the job started is read.
            assert notebook.unread_count(con, chat, ids[0], ids[-1]) == 0
            assert notebook.unread_count(con, chat, late, late) == 1, (
                "the job read a message written after it started")
            top = con.execute(
                "SELECT MAX(to_message_id) FROM notebook_extractions "
                "WHERE chat_id = ? AND status = 'done'", (chat,)).fetchone()[0]
        assert top == ids[-1]
        assert len(calls) == UNITS, "a unit was paid for the late message"


class TestThePriceCountsTheWholeCall:
    """3.17. The preview priced what each call SENDS of the conversation,
    the card, the notes and the instructions, and nothing else: not the
    fixed frame every request carries (the fence rule and the four fenced
    section labels) and not the reply at all. The number a reader agrees to
    before pressing Start was lower than the bill."""

    @staticmethod
    def _chat(bodies: list[str]) -> int:
        chat_id = make_chat_on_db(0)
        with get_db() as con:
            for i, body in enumerate(bodies):
                con.execute(
                    "INSERT INTO messages (chat_id, role, content, active) "
                    "VALUES (?,?,?,1)",
                    (chat_id, "user" if i % 2 == 0 else "assistant", body))
        choose(chat_id)
        return chat_id

    def test_the_price_is_every_call_s_frame_input_and_reply(
            self, client, monkeypatch) -> None:
        prompt, reply = 0.5, 2.0
        monkeypatch.setattr(openrouter, "_zdr_cache", (time.monotonic(), [
            {"id": EXTRACT, "prompt_price": prompt, "completion_price": reply,
             "provider": "p", "context_length": 8000, "endpoints": 1}]))
        bodies = [f"message {i} about the mill and the river" for i in range(45)]
        chat_id = self._chat(bodies)

        body = client.get(f"{API}/extract/plan/{chat_id}?scope=all").json()

        calls = body["calls_planned"]
        assert calls == -(-len(bodies) // config.NOTEBOOK_EXTRACT_EVERY_TURNS), (
            "ground: three calls for forty-five messages")
        card = len("d")                  # make_chat_on_db's description
        fixed = (min(card, config.NOTEBOOK_CARD_MAX_CHARS)
                 + 0                     # no notes yet
                 + len(notebook_extract.system_prompt("en"))
                 + notebook_extract.SCAFFOLD_CHARS)
        material = min(sum(len(b) for b in bodies),
                       calls * config.NOTEBOOK_TURNS_MAX_CHARS)
        sent = (material + calls * fixed) / config.CHARS_PER_TOKEN_ESTIMATE
        back = calls * notebook_extract.MAX_TOKENS
        expected = round((prompt * sent + reply * back) / 1_000_000, 6)

        assert body["cost_estimate"] == expected
        # GROUND: the frame and the reply are real parts of it, so the
        # equality above is not satisfied by a formula without them.
        without = round(prompt * (material + calls * (
            fixed - notebook_extract.SCAFFOLD_CHARS))
            / config.CHARS_PER_TOKEN_ESTIMATE / 1_000_000, 6)
        assert notebook_extract.SCAFFOLD_CHARS > 0
        assert body["cost_estimate"] > without

    def test_the_notes_already_kept_are_priced_on_every_call(
            self, client, monkeypatch) -> None:
        """Every call carries the chat's notes, so a chat that has notes
        costs more to read than the same chat without them, by at least the
        notes' own text once per call. Measured as a difference between two
        previews of one chat, so nothing here copies the price formula: the
        bound is the notes' length, which the test wrote itself."""
        prompt, reply = 0.5, 2.0
        monkeypatch.setattr(openrouter, "_zdr_cache", (time.monotonic(), [
            {"id": EXTRACT, "prompt_price": prompt, "completion_price": reply,
             "provider": "p", "context_length": 8000, "endpoints": 1}]))
        chat_id = self._chat(
            [f"message {i} about the mill and the river" for i in range(45)])

        before = client.get(f"{API}/extract/plan/{chat_id}?scope=all").json()
        texts = [f"Note {i}: her brother owns the mill by the river." * 3
                 for i in range(6)]
        for text in texts:
            notebook.create_entry(chat_id, text)
        after = client.get(f"{API}/extract/plan/{chat_id}?scope=all").json()

        calls = after["calls_planned"]
        assert calls == before["calls_planned"] > 1, (
            "ground: the notes change the price, not the number of calls")
        floor = (prompt * calls * sum(len(t) for t in texts)
                 / config.CHARS_PER_TOKEN_ESTIMATE / 1_000_000)
        grew = after["cost_estimate"] - before["cost_estimate"]
        assert grew >= floor - 2e-6, (
            f"six notes added {grew:.6f} to the price; carried on each of "
            f"{calls} calls they are at least {floor:.6f}")

    def test_the_notes_are_priced_no_higher_than_the_cap_a_call_carries(
            self, client, monkeypatch) -> None:
        """The notes ride on every call only up to
        `NOTEBOOK_EXISTING_MAX_CHARS`, so the price carries no more than
        that per call. A price that counted every note a chat has would
        overstate a long-lived chat's read by however many notes it kept.
        The cap is set small here and the notes written far past it."""
        prompt, reply = 50.0, 2.0
        cap = 40
        monkeypatch.setattr(config, "NOTEBOOK_EXISTING_MAX_CHARS", cap)
        monkeypatch.setattr(openrouter, "_zdr_cache", (time.monotonic(), [
            {"id": EXTRACT, "prompt_price": prompt, "completion_price": reply,
             "provider": "p", "context_length": 8000, "endpoints": 1}]))
        chat_id = self._chat(
            [f"message {i} about the mill and the river" for i in range(45)])

        before = client.get(f"{API}/extract/plan/{chat_id}?scope=all").json()
        texts = [f"Note {i}: her brother owns the mill by the river." * 3
                 for i in range(6)]
        for text in texts:
            notebook.create_entry(chat_id, text)
        after = client.get(f"{API}/extract/plan/{chat_id}?scope=all").json()

        calls = after["calls_planned"]
        assert calls == before["calls_planned"] > 1, "ground: same calls"
        assert sum(len(t) for t in texts) > 10 * cap, (
            "ground: the notes are far past the cap")
        grew = after["cost_estimate"] - before["cost_estimate"]
        ceiling = (prompt * calls * cap
                   / config.CHARS_PER_TOKEN_ESTIMATE / 1_000_000)
        # POSITIVE: the notes are priced at all, so the ceiling below is
        # not met by a price that ignores them.
        assert grew > 0
        assert grew <= ceiling + 2e-6, (
            f"the notes added {grew:.6f}; capped at {cap} characters a call "
            f"over {calls} calls they are at most {ceiling:.6f}")

    def test_an_unknown_reply_price_is_no_number_at_all(
            self, client, monkeypatch) -> None:
        """Half a price is the understatement this is about, so a missing
        completion price is an unknown cost, never the prompt half alone."""
        monkeypatch.setattr(openrouter, "_zdr_cache", (time.monotonic(), [
            {"id": EXTRACT, "prompt_price": 0.5, "provider": "p",
             "context_length": 8000, "endpoints": 1}]))
        chat_id = self._chat([f"line {i}" for i in range(25)])

        body = client.get(f"{API}/extract/plan/{chat_id}?scope=all").json()

        assert body["prompt_price"] == 0.5, "ground: the prompt half is known"
        assert body["cost_estimate"] is None


# ── A read you asked for does not hold the vault open ─────────────────────

class TestAReadingJobIsNotActivity:
    """Three documents say a reading job does not count as activity.

    It is the sentence that makes the whole feature safe to leave running:
    a job that reset the idle clock would hold the vault open for as long as
    it read, which is exactly what the background reader is forbidden from
    doing and for exactly the same reason. The routes are new, so the claim
    was true of nothing until this measured it.
    """

    def _age(self) -> float:
        vault_state._last_activity -= 100_000
        return vault_state.idle_seconds()

    def test_a_running_job_and_its_poll_leave_the_clock_where_it_was(
            self, client, chat, monkeypatch) -> None:
        """The claim, exactly as the three documents make it.

        The BUTTON is a user action and counts, like every other thing a
        person presses - that is not what any of the copies say. What they
        say is that the READING does not: a job that kept resetting the
        clock would hold the vault open for as long as it read, which is
        the one thing the background reader is forbidden to do and for the
        same reason. So the clock is aged AFTER the press, the job is left
        to work, and its progress poll - the request the panel repeats for
        as long as the job lasts - is made repeatedly on top.
        """
        hold = threading.Event()
        timer = threading.Timer(0.4, hold.set)
        calls: list[int] = []

        async def waits_for_the_timer(*a, **kw):
            calls.append(1)
            while not hold.is_set():
                await asyncio.sleep(0.01)
            return {"id": f"gen-{len(calls)}", "choices": [{
                "finish_reason": "stop",
                "message": {"content": json.dumps({"facts": [fact()]})}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                          "cost": 0.0001}}

        monkeypatch.setattr(openrouter, "complete", waits_for_the_timer)
        assert client.post(f"{API}/extract/run/{chat}",
                           json={"scope": "all"}).json()["started"] is True
        timer.start()
        try:
            # AFTER the press, which is the only part of this a person did.
            before = self._age()
            assert before > 1.0, (
                "ground: the clock was not old, so 'it did not move' proves "
                "nothing")
            deadline = time.monotonic() + 10
            while not calls and time.monotonic() < deadline:
                assert client.get(f"{API}/worker?chat_id={chat}"
                                  ).status_code == 200
            assert calls, "ground: the job really was reading"
            for _ in range(8):
                assert client.get(f"{API}/worker?chat_id={chat}"
                                  ).status_code == 200

            assert vault_state.idle_seconds() >= before - 1.0, (
                "the reading job or its progress poll counted as activity "
                "and held the vault open")
        finally:
            timer.cancel()
            hold.set()

    def test_the_activity_ping_beside_them_does_move_it(self, client) -> None:
        """POSITIVE CONTROL for the row above: the same measurement, on the
        one route that is meant to count. Without this, a clock that had
        stopped working would pass every row in this class."""
        before = self._age()
        assert before > 1.0

        assert client.post("/api/v1/activity").status_code == 204

        assert vault_state.idle_seconds() < 1.0

    def test_a_notebook_route_that_is_not_exempt_moves_the_clock(
            self, client, chat) -> None:
        """The gate's membership TEST, which nothing measured.

        `routers/notebook.py` states the claim these four routes ship with:
        "None of the four is idle-exempt: a button press is a person."
        `test_every_exempt_route_actually_exists` pins the exempt SET by
        equality, so nothing can join it unseen - but the middleware's test
        is `path in _IDLE_EXEMPT`, and widening THAT (to
        `or path.startswith("/api/v1/notebook")`, say) touches no set and
        was caught by nothing: the row above measures only that the clock
        does NOT move, and it ages the clock after the press, so the press
        itself falls outside what it looks at.

        Both directions, through the real middleware: a press moves it, the
        progress poll beside it does not.
        """
        before = self._age()
        assert before > 1.0, "ground: the clock was old before the press"

        # A PRESS. The plan route spends nothing, so this is the cheapest
        # of the four, and it is not in the exempt set.
        assert client.get(f"{API}/extract/plan/{chat}?scope=all"
                          ).status_code == 200

        assert vault_state.idle_seconds() < 1.0, (
            "a button press on a notebook route did not count as activity, "
            "so the exempt set has been widened past the poll")

        # And the other direction, on the ONE notebook path that is exempt.
        after = self._age()
        assert after > 1.0
        assert client.get(f"{API}/worker?chat_id={chat}").status_code == 200
        assert vault_state.idle_seconds() >= after - 1.0, (
            "the progress poll counted as activity, so a running job holds "
            "the vault open")


class TestTheGuardsTheStoreDeclares:
    """Five claims the code states in its own docstrings, each of which a
    mutation could take out without a single test noticing.

    They are grouped because they are one kind of defect rather than five:
    a sentence written in capitals beside a SQL predicate, with no row that
    depends on the predicate being there.
    """

    def test_the_jobs_ceiling_never_falls_to_zero(self, monkeypatch) -> None:
        """`_backfill_cap`: "`ELYSIUM_NOTEBOOK_LIVE_RESERVE=60`
        against the default ceiling of 60 made this return 0, every job
        refuse `daily_cap_reached` ... on a machine that had made none".

        A named, measured failure with the floor as its fix and nothing
        measuring the floor. Both numbers are imported.
        """
        monkeypatch.setattr(config, "NOTEBOOK_DAILY_CALL_CAP", 60)
        monkeypatch.setattr(config, "NOTEBOOK_LIVE_RESERVE", 60)
        assert notebook_worker._backfill_cap() == 1, (
            "the reserve swallowed the whole ceiling and every job refuses "
            "on a machine that has spent nothing")
        # GROUND: the ordinary arithmetic is untouched, so the floor is a
        # floor and not a constant.
        monkeypatch.setattr(config, "NOTEBOOK_LIVE_RESERVE", 10)
        assert notebook_worker._backfill_cap() == 50
        # And past the ceiling, which is the same defect one step worse.
        monkeypatch.setattr(config, "NOTEBOOK_LIVE_RESERVE", 99)
        assert notebook_worker._backfill_cap() == 1

    @pytest.mark.anyio
    async def test_a_late_advance_cannot_resurrect_a_stopped_job(
            self, client, chat, monkeypatch) -> None:
        """`advance_job`: "a job already past `running` is left where
        it is, so a late advance from a unit that settled after the stop
        cannot resurrect it"."""
        provider(monkeypatch)
        out = await run_and_wait(chat, "next")
        job_id = out["job_id"]
        # Written by SQL rather than through `stop_job`, because `stop_job`
        # has the twin of the guard under test and would refuse a job that
        # has already ended - which is the row below this one.
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute("UPDATE notebook_backfill_jobs SET state = 'stopped', "
                        "last_error = 'chat_stopped' WHERE id = ?", (job_id,))
        assert job_of(chat)["state"] == "stopped", "ground: it is terminal"

        # The unit that was still settling reports its progress, which is
        # the loop's first act on every turn and the one that moves the
        # state.
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.advance_job(con, job_id, calls=1, notes=1, running=True)

        row = job_of(chat)
        assert row["state"] == "stopped", (
            "a late advance put a stopped job back to running, so the panel "
            "offers Stop on a job nothing is driving")
        # GROUND: the counters still moved, so the guard is on the STATE
        # and the call is not simply ignored.
        assert row["spent_calls"] == 2 and row["notes_written"] >= 1, dict(row)
        # POSITIVE CONTROL: the same call on a `planned` job does move it.
        second = await run_and_wait(chat, "next")
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute("UPDATE notebook_backfill_jobs SET state = 'planned', "
                        "started_at = NULL WHERE id = ?", (second["job_id"],))
            notebook.advance_job(con, second["job_id"], running=True)
        with get_db() as con:
            moved = con.execute(
                "SELECT state FROM notebook_backfill_jobs WHERE id = ?",
                (second["job_id"],)).fetchone()[0]
        assert moved == "running", "ground: advance_job moves a planned job"

    @pytest.mark.anyio
    async def test_a_finished_job_is_not_stopped_over_the_top(
            self, client, chat, monkeypatch) -> None:
        """`stop_job`: the same guard on the other writer. A job that
        ended `done` must not be rewritten as `stopped` by a stop that
        arrived after it finished - the panel would then offer "stopped at
        today's limit" for a run that completed."""
        provider(monkeypatch)
        out = await run_and_wait(chat, "next")
        assert job_of(chat)["state"] == "done", "ground: it finished"

        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.stop_job(con, out["job_id"], "stopped",
                              "daily_cap_reached")

        row = job_of(chat)
        assert row["state"] == "done", "a finished job was rewritten"
        assert row["last_error"] is None, (
            f"and given a reason it never had: {row['last_error']}")

    @pytest.mark.anyio
    async def test_a_range_read_leaves_a_running_row_inside_it_alone(
            self, client, chat, monkeypatch) -> None:
        """`forget_readings_in_range`: "a range that merely overlaps
        is left" - and a `running` row inside it is left too, whatever it
        overlaps. That row is the live trace of a call that is out, and the
        one `settle_orphaned_running` converts to `abandoned_in_flight`;
        deleting it loses the record that the call was paid for."""
        provider(monkeypatch)
        with get_db() as con:
            lo, hi = notebook.message_span(con, chat)
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                "INSERT INTO notebook_extractions "
                "(chat_id, from_message_id, to_message_id, work_key, status) "
                "VALUES (?, ?, ?, ?, 'running')",
                (chat, lo, hi, "a-call-that-is-out"))
        with get_db() as con:
            before = con.execute(
                "SELECT COUNT(*) FROM notebook_extractions "
                "WHERE chat_id = ? AND status = 'running'", (chat,)
            ).fetchone()[0]
        assert before == 1, "ground: the row is there"

        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.forget_readings_in_range(con, chat, lo, hi)

        with get_db() as con:
            rows = con.execute(
                "SELECT status FROM notebook_extractions WHERE chat_id = ?",
                (chat,)).fetchall()
        assert [r[0] for r in rows] == ["running"], (
            "the range read deleted a reading that was still out, so the "
            "call it paid for has no record at all")

    @pytest.mark.anyio
    async def test_the_block_names_the_newest_job_not_the_first(
            self, client, chat, monkeypatch) -> None:
        """`job_for_chat`: "Newest rather than 'the running one': the
        panel needs to offer undo on a job that has just finished". Ordered
        the other way the strip shows a chat's FIRST run for ever, with its
        Undo pointed at it."""
        provider(monkeypatch)
        first = await run_and_wait(chat, "next")
        second = await run_and_wait(chat, "next")
        assert second["job_id"] > first["job_id"], "ground: two runs"

        block = client.get(f"{API}/worker?chat_id={chat}"
                           ).json()["worker"]["backfill"]

        assert block["job_id"] == second["job_id"], (
            f"the panel is offering the first run, {block}")

    @pytest.mark.anyio
    async def test_an_abandoned_reading_counts_as_unread_again(
            self, client, chat, monkeypatch) -> None:
        """`unread_count`: "a message is read when a `done` range
        covers it, OR a range whose call was made and abandoned in flight
        does".

        That second arm is the app not charging the reader twice for the
        same stretch: the call went out and was paid for, so the messages
        are closed even though no note came back. Drop the arm and every
        abandoned range is offered for reading again, at the reader's
        expense, for ever. Nothing measured it.
        """
        provider(monkeypatch)
        await run_and_wait(chat, "next")
        with get_db() as con:
            covered = notebook.unread_count(con, chat)
            total = con.execute(
                "SELECT COUNT(*) FROM messages WHERE chat_id = ? "
                "AND active = 1", (chat,)).fetchone()[0]
        assert covered < total, "ground: the run really covered something"

        # The same range, now recorded the way a vault lock or a crash
        # leaves it: the call went out, the answer never landed.
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                "UPDATE notebook_extractions SET status = 'failed', "
                "error_type = ? WHERE chat_id = ?",
                (notebook.ABANDONED_IN_FLIGHT, chat))
        with get_db() as con:
            after = notebook.unread_count(con, chat)

        assert after == covered, (
            f"a range whose call was paid for and lost is being offered for "
            f"reading again: {covered} -> {after}")
        # And the planner agrees, which is the whole point of one predicate.
        plan = client.get(f"{API}/extract/plan/{chat}?scope=all").json()
        assert plan["messages_unread"] == after

        # GROUND: an ordinary failure is NOT the same thing. Nothing was
        # paid, so those messages come back - which is what makes the
        # assertion above about the abandoned arm rather than about
        # `failed` rows being ignored wholesale.
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute("UPDATE notebook_extractions SET error_type = "
                        "'timeout' WHERE chat_id = ?", (chat,))
        with get_db() as con:
            plain = notebook.unread_count(con, chat)
        assert plain > covered, (
            "an ordinary failed reading still counts as read")


# ── The lock is bounded, whatever the job does ────────────────────────────

class TestTheLockDoesNotWaitForAJobThatWillNotGo:
    """SECURITY.md:181 says the wait is five seconds. This is where it has
    to be true.

    `quiesce()` stands the reading job down FIRST, before the grace-bounded
    settle, so an open-ended wait there is an open-ended vault lock: the key
    stays in memory, with decrypted material beside it, for as long as the
    loop takes to unwind. A loop that does not unwind at all - one that
    swallows its cancellation - is the case that tells a bound from a hope.
    """

    @pytest.mark.anyio
    async def test_a_job_that_swallows_its_cancel_does_not_hold_the_lock(
            self, db, monkeypatch) -> None:
        monkeypatch.setattr(notebook_worker, "SETTLE_GRACE_S", 0.2)
        alive, release = asyncio.Event(), asyncio.Event()

        async def never_stands_down() -> None:
            # Cancellation asked for, refused, and this goes on running -
            # which is what a loop wedged on a thread or a foreign lock
            # looks like from the outside. `release` is the test's own way
            # back out; nothing in production has one, which is the point.
            alive.set()
            while not release.is_set():
                try:
                    await asyncio.sleep(0.01)
                except asyncio.CancelledError:
                    pass

        task = asyncio.get_running_loop().create_task(never_stands_down())
        notebook_worker.worker._backfill_task = task
        notebook_worker.worker._backfill = {"id": 1, "chat_id": 1}
        notebook_worker.worker._sweeping = True
        try:
            await asyncio.wait_for(alive.wait(), 5)
            assert not task.done(), "ground: the job really is running"

            started = time.monotonic()
            # `wait_for` so the mutation is a RED TEST rather than a hung
            # suite: without the timeout inside `_cancel_backfill` this
            # never returns, and a suite that never returns reports
            # nothing at all.
            await asyncio.wait_for(notebook_worker._cancel_backfill(), 5)
            elapsed = time.monotonic() - started

            assert elapsed < 2.0, (
                f"the stand-down waited {elapsed:.1f}s on a job that will "
                "not go; the lock is not bounded")
            assert not task.done(), (
                "ground: it is still running, so the bound is what returned "
                "and not the job finishing")
        finally:
            release.set()
            notebook_worker.worker._backfill_task = None
            notebook_worker.worker._backfill = None
            notebook_worker.worker._sweeping = False
            done, still = await asyncio.wait({task}, timeout=5)
            assert not still, "the swallowing task outlived the test"

    def test_the_lock_route_answers_while_a_job_is_still_reading(
            self, client, chat, monkeypatch) -> None:
        """The same claim through the real door: `POST /vault/lock`, the
        middleware, `lock_vault_now`, `quiesce`.

        In order rather than on a second thread: the TestClient drives one
        portal, so a concurrent request from another thread deadlocks the
        client rather than the code under test. The provider is released on
        a timer, so the job is genuinely mid-call when the lock arrives and
        nothing here waits on a person.
        """
        hold = threading.Event()
        timer = threading.Timer(0.3, hold.set)

        async def waits_for_the_timer(*a, **kw):
            while not hold.is_set():
                await asyncio.sleep(0.01)
            return {"id": "gen", "choices": [{
                "finish_reason": "stop",
                "message": {"content": json.dumps({"facts": [fact()]})}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                          "cost": 0.0001}}

        monkeypatch.setattr(openrouter, "complete", waits_for_the_timer)
        started = client.post(f"{API}/extract/run/{chat}", json={"scope": "all"})
        assert started.json()["started"] is True, "ground: a job is reading"
        timer.start()
        try:
            began = time.monotonic()
            answer = client.post("/api/v1/vault/lock")
            elapsed = time.monotonic() - began
        finally:
            timer.cancel()
            hold.set()

        assert answer.status_code == 200, answer.text
        # The lock is allowed to wait out its grace twice over - once for
        # the loop, once for the settle - and no longer.
        assert elapsed < 4 * notebook_worker.SETTLE_GRACE_S, (
            f"the lock route took {elapsed:.1f}s with a job reading")


# ── One job at a time, and who is driving it ──────────────────────────────

class TestOnlyOneJobAtATime:
    """Two presses landing together used to start two loops.

    The guard is one flag on the module singleton. It was read at the top
    of `backfill` and written thirty-five lines later, with the preview and
    the row write - two awaits - in between, and asyncio hands the loop to
    another coroutine at each of them. The second loop's exit then cleared
    the pointers for the first, which left the first billing with nothing
    able to stop it, name it, or keep a third off the same chat.
    """

    @pytest.mark.anyio
    async def test_two_presses_at_once_start_exactly_one(
            self, db, monkeypatch) -> None:
        chat_id = make_chat_on_db(45)
        choose(chat_id)
        hold = asyncio.Event()
        entered = asyncio.Event()
        calls = provider(monkeypatch, hold=hold, entered=entered)
        w = notebook_worker.worker
        assert job_count(chat_id) == 0, "ground: no job yet"
        try:
            first, second = await asyncio.gather(
                w.backfill(chat_id, "all"), w.backfill(chat_id, "all"))

            started = [o for o in (first, second) if o["started"]]
            refused = [o for o in (first, second) if not o["started"]]
            assert len(started) == 1, (first, second)
            assert refused[0]["reason"] == "already_running"
            assert refused[0]["reason"] in notebook_worker.SWEEP_REFUSALS
            assert job_count(chat_id) == 1, "a second job row was written"
            # The one that did start is the one the process can name, stop
            # and undo. That is what the second loop used to take away.
            assert w.active_job_id() == started[0]["job_id"]
            await asyncio.wait_for(entered.wait(), 5)
            assert len(calls) == 1, "two loops were billing the same chat"
            out = await w.stop_backfill(chat_id)
            assert out["stopped"] is True and out["job_id"] == w.active_job_id()
        finally:
            hold.set()
            await notebook_worker.stop()

    def test_a_one_unit_press_is_refused_while_a_job_reads_and_leaves_the_lock_alone(
            self, client, chat, monkeypatch) -> None:
        """One lock, and the half that costs notes.

        This was the sweep route's test; decision 69 a removed that route,
        and the one-unit press that is left is the strip's `next` job. Two
        things have to hold. The press must be refused while a job reads,
        or two units bill the same chat at once. And a refusal must not
        touch a lock it never took: a bare clear unlocks the running job,
        and the SECOND job then overwrites `_backfill` and `_backfill_task`,
        which is how the first becomes unstoppable and how "undo this run"
        deletes notes out from under a job still writing them.
        """
        hold = threading.Event()
        timer = threading.Timer(1.0, hold.set)

        async def waits_for_the_timer(*a, **kw):
            while not hold.is_set():
                await asyncio.sleep(0.01)
            return {"id": "gen", "choices": [{
                "finish_reason": "stop",
                "message": {"content": json.dumps({"facts": [fact()]})}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                          "cost": 0.0001}}

        monkeypatch.setattr(openrouter, "complete", waits_for_the_timer)
        w = notebook_worker.worker
        started = client.post(f"{API}/extract/run/{chat}", json={"scope": "all"})
        assert started.json()["started"] is True, "ground: a job is reading"
        job_id = started.json()["job_id"]
        assert w.active_job_id() == job_id, "ground: and this process drives it"
        timer.start()
        try:
            refused = client.post(f"{API}/extract/run/{chat}",
                                  json={"scope": "next"})

            assert refused.status_code == 200, refused.text
            assert refused.json() == {"started": False,
                                      "reason": "already_running"}
            assert refused.json()["reason"] in notebook_worker.SWEEP_REFUSALS
            # THE HALF THAT COSTS NOTES: the refused press must not have
            # handed back a lock it never took.
            assert w._sweeping is True, (
                "the refused press cleared the running job's lock")
            assert w.active_job_id() == job_id, (
                "the job stopped being the one this process is driving")
            assert client.post(f"{API}/extract/run/{chat}",
                               json={"scope": "all"}).json() == {
                "started": False, "reason": "already_running"}
        finally:
            timer.cancel()
            hold.set()

    @pytest.mark.anyio
    async def test_a_refused_press_hands_the_claim_back(
            self, db, monkeypatch) -> None:
        """GROUND for the flag: taking the claim early must not leave it
        taken when the answer is a refusal. A chat with nothing unread is
        refused after the preview, which is exactly where the claim is
        already held."""
        chat_id = make_chat_on_db(4)
        choose(chat_id)
        provider(monkeypatch)
        w = notebook_worker.worker
        await run_and_wait(chat_id, "all")
        assert unread(chat_id) is None, "ground: the chat is fully read"

        again = await w.backfill(chat_id, "all")

        assert again == {"started": False, "reason": "nothing_unread"}
        assert w._sweeping is False, "the refusal kept the claim"
        assert w.active_job_id() is None


class TestTheRowSaysWhoIsDrivingIt:
    @pytest.mark.anyio
    async def test_the_poll_racing_the_start_does_not_orphan_the_job(
            self, db, monkeypatch) -> None:
        """The status poll's own pair, run flat out while a job starts.

        `backfill_block` asks `active_job_id()` and hands the answer to
        `settle_orphaned_jobs` as `keep`. The row is committed on a worker
        thread, so the poll gets the loop while `backfill` is still
        suspended - and while `active_job_id` read the loop TASK, that
        window answered None, so the poll stamped the new row `interrupted`
        with `keep=None`. Nothing moves a row back out of that state, so
        the job then ran to the end spending money against a row that said
        it had stopped, and the undo route's 409 was disarmed with it.
        """
        chat_id = make_chat_on_db(45)
        choose(chat_id)
        hold, entered = asyncio.Event(), asyncio.Event()
        provider(monkeypatch, hold=hold, entered=entered)
        w = notebook_worker.worker
        seen: list[tuple] = []
        errors: list[str] = []
        stop_polling = threading.Event()

        def poll_once() -> None:
            with get_db() as con:
                # THE PRODUCTION FUNCTION, not a copy of it: `backfill_block`
                # is what every status poll runs, and what it does with the
                # driver id is the thing under test.
                w.backfill_block(con, chat_id)
                row = con.execute(
                    "SELECT state, last_error FROM notebook_backfill_jobs "
                    "ORDER BY id DESC LIMIT 1").fetchone()
            if row is not None:
                seen.append((row["state"], row["last_error"]))

        def poll_until_told_to_stop() -> None:
            # A REAL THREAD, because a route is one. Polling through
            # `anyio.to_thread` cannot reach this window at all: the
            # coroutine that wrote the row is already queued to resume by
            # the time a loop-scheduled poll could run, so it always wins,
            # and the test would pass on the broken code. A route thread is
            # under no such obligation and reads whenever it likes.
            while not stop_polling.is_set():
                try:
                    poll_once()
                except Exception as exc:
                    # CARRY ON. A locked database while the creating
                    # transaction is open is the ordinary answer here, and
                    # a poller that gave up on the first one would stop
                    # exactly when the window it is hunting opens - which
                    # is how this test passed on the broken code once.
                    errors.append(type(exc).__name__)

        spy = threading.Thread(target=poll_until_told_to_stop, daemon=True)
        spy.start()
        try:
            out = await w.backfill(chat_id, "all")
            assert out["started"] is True
            await asyncio.wait_for(entered.wait(), 5)
            # WAIT FOR THE POLL, do not guess at it. A fixed sleep makes the
            # ground control below a measure of how loaded the machine is:
            # under a full suite the thread is starved and reads twenty
            # times fewer rows than it does alone.
            deadline = time.monotonic() + 10
            while len(seen) <= 20 and time.monotonic() < deadline:
                await asyncio.sleep(0.01)

            # GROUND, and it has to be this strong: the poll must have READ
            # the row many times, or "it was never stamped" is only "it was
            # never looked at". A poller that gave up on the first locked
            # database satisfied a bare `assert seen` with two rows from
            # before the job existed.
            assert len(seen) > 20, (
                f"ground: the poll barely ran ({len(seen)} reads, "
                f"{len(errors)} errors)")
            orphaned = [s for s in seen if s[1] == notebook.JOB_INTERRUPTED]
            assert orphaned == [], (
                f"the poll stamped a job it was driving as interrupted "
                f"({len(orphaned)} of {len(seen)} reads)")
            assert job_of(chat_id)["state"] in ("planned", "running")
        finally:
            stop_polling.set()
            hold.set()
            # JOINED HERE, not beside the assertions. Any assertion above
            # raising used to skip the join entirely, and the thread body is
            # a hot spin over `get_db()` - so a failing test left it opening
            # connections against the `tmp_path` database the `db` fixture
            # was tearing down. The join is asserted for the same reason:
            # a cleanup nobody checks is a cleanup that stops working
            # silently.
            spy.join(timeout=5)
            assert not spy.is_alive(), "the poller outlived the test"
            await notebook_worker.stop()


# ── The chat breaker, before the job and inside it ────────────────────────

class TestEveryReasonAJobStopsForHasAName:
    """`last_error` is read by the panel, which prints what it does not
    recognise in brackets - so an unbounded value here reaches a person as
    a token they cannot act on. `JOB_LAST_ERRORS` is the table; this walks
    the terminal states a job can actually reach and asks whether what was
    written is in it."""

    @pytest.mark.anyio
    async def test_the_reasons_a_job_actually_stops_for_are_all_in_the_table(
            self, client, chat, monkeypatch) -> None:
        seen: set[str] = set()

        # 1. it finished.
        provider(monkeypatch)
        await run_and_wait(chat, "all")
        seen.add(str(job_of(chat)["last_error"]))

        # 2. the reader stopped it.
        hold, entered = asyncio.Event(), asyncio.Event()
        provider(monkeypatch, hold=hold, entered=entered)
        other = make_chat_on_db(45)
        choose(other)
        w = notebook_worker.worker
        assert (await w.backfill(other, "all"))["started"] is True
        await asyncio.wait_for(entered.wait(), 5)
        await w.stop_backfill(other)
        hold.set()
        await w.wait_for_backfill()
        seen.add(str(job_of(other)["last_error"]))

        # 3. the chat stopped for good under it.
        third = make_chat_on_db(45)
        choose(third)
        now = asyncio.get_running_loop().time()
        for _ in range(notebook_worker.STOP_AFTER):
            w._fail_chat(third, now)
        assert (await w.backfill(third, "all")) == {
            "started": False, "reason": "chat_stopped"}

        assert seen, "ground: jobs really did end and write a reason"
        for reason in seen:
            assert reason == "None" or reason in notebook.JOB_LAST_ERRORS, (
                f"a job wrote {reason!r}, which no sentence covers")
        # POSITIVE CONTROL for the membership question: the table is a
        # table and not everything, so a token nobody declared is caught.
        assert "KeyError" not in notebook.JOB_LAST_ERRORS
        assert notebook.JOB_INTERRUPTED in notebook.JOB_LAST_ERRORS


class TestTheChatBreakerStopsTheJob:
    """A chat that tripped is refused, and one that trips mid-job stops
    between units rather than going round forever. Both are written
    acceptance criteria for this unit and neither had a test."""

    @pytest.mark.anyio
    async def test_a_tripped_chat_is_refused_before_anything_is_written(
            self, db, monkeypatch) -> None:
        chat_id = make_chat_on_db(45)
        choose(chat_id)
        calls = provider(monkeypatch)
        w = notebook_worker.worker
        # THE LOOP'S OWN CLOCK, not zero. `allows` asks whether the cooldown
        # has elapsed since `opened_at`, so failures stamped at t=0 against
        # a monotonic clock already in the thousands read as long cooled
        # down, and the breaker answers half-open.
        now = asyncio.get_running_loop().time()
        for _ in range(notebook_worker.TRIP_AFTER):
            w._fail_chat(chat_id, now)
        assert w._chat_breaker(chat_id, now) is not None, "ground: tripped"

        out = await w.backfill(chat_id, "all")

        assert out == {"started": False, "reason": "chat_paused"}
        assert out["reason"] in notebook_worker.SWEEP_REFUSALS
        assert calls == [] and spend() == 0 and job_count(chat_id) == 0
        assert w._sweeping is False, "the refusal kept the claim"

    @pytest.mark.anyio
    async def test_a_chat_that_stops_for_good_says_so(
            self, db, monkeypatch) -> None:
        chat_id = make_chat_on_db(45)
        choose(chat_id)
        provider(monkeypatch)
        w = notebook_worker.worker
        now = asyncio.get_running_loop().time()
        for _ in range(notebook_worker.STOP_AFTER):
            w._fail_chat(chat_id, now)

        out = await w.backfill(chat_id, "all")

        assert out == {"started": False, "reason": "chat_stopped"}
        assert job_count(chat_id) == 0

    @pytest.mark.anyio
    async def test_the_live_reader_stopping_the_chat_ends_the_job(
            self, db, monkeypatch) -> None:
        """The loop re-asks the chat's breaker at the top of every turn.

        The thing that moves it mid-job is the LIVE reader, not the job:
        the job's own failure ends it at the "nothing moved" branch long
        before five of them. And a success does not just reset this chat's
        record, it POPS it - `_chat_recovered` - stopped and all. So the
        window the in-loop check exists for is between a unit's success and
        the next turn's question, and the tripper here keeps the chat
        stopped from the moment the first call is out, which is exactly a
        live reader failing this chat while the job runs.
        """
        chat_id = make_chat_on_db(45)
        choose(chat_id)
        w = notebook_worker.worker
        entered = asyncio.Event()
        calls = provider(monkeypatch, entered=entered)

        async def the_live_reader_keeps_failing_this_chat() -> None:
            while True:
                now = asyncio.get_running_loop().time()
                for _ in range(notebook_worker.STOP_AFTER):
                    w._fail_chat(chat_id, now)
                await asyncio.sleep(0)

        out = await w.backfill(chat_id, "all")
        assert out["started"] is True
        # The first unit is already in flight, so its own turn asked the
        # breaker before any of this - which is what makes "one call" a
        # loop that stopped rather than a loop that never started.
        await asyncio.wait_for(entered.wait(), 5)
        tripper = asyncio.create_task(the_live_reader_keeps_failing_this_chat())
        try:
            await w.wait_for_backfill()
        finally:
            tripper.cancel()

        assert len(calls) == 1, "the loop kept going on a chat that stopped"
        assert spend() == 1 and notebook.list_entries(chat_id), (
            "ground: the first unit really did run and really did pay")
        row = job_of(chat_id)
        assert row["state"] == "stopped"
        assert row["last_error"] == "chat_stopped"
        assert row["last_error"] in notebook_worker.SWEEP_REFUSALS
        assert unread(chat_id) is not None, "ground: it really did stop early"


# ── T-73: the review-queue gate (KARAR-D) ─────────────────────────────────

def _five_notes(chat_id: int) -> list[dict]:
    """In list order: model, USER, model PINNED, model, model.

    EVERY MODEL NOTE CARRIES THE MESSAGE IT CAME FROM, because in
    production every one of them does - `commit_extraction` stamps the
    range's last message, and NULL is reserved for the reader's own notes
    and for the one case where that message was deleted mid-call. The
    source is the FIRST message of the chat, so a read of any range at all
    can see where these notes came from and the review gate is asked the
    question this row is about rather than a question about ranges.
    """
    with get_db() as con:
        first, _last = notebook.message_span(con, chat_id)
    model = dict(provenance=notebook.PROV_MODEL, source_message_id=first)
    return [
        notebook.create_entry(chat_id, "Note zero, the model's.", **model),
        notebook.create_entry(chat_id, "Note one, the reader's own."),
        notebook.create_entry(chat_id, "Note two, pinned.", pinned=True,
                              **model),
        notebook.create_entry(chat_id, "Note three, the model's.", **model),
        notebook.create_entry(chat_id, "Note four, the model's.", **model),
    ]


# ANCHORED ON PURPOSE. `_is_anchored` wants the proposal's text and its
# evidence to share a real token; the old fixtures paired "Replaces zero."
# with these lines and shared nothing, and survived only because the
# one-character stem "0" falls under `_ANCHOR_MIN_CHARS` and leaves the
# anchor list empty, which short-circuits to True. Raise that constant by
# one and every row below would have gone red for a reason with nothing to
# do with the review gate. "mill" is the token, so the facts reach the gate
# because they are anchored rather than because nothing was checked.
USER_QUOTE = "line 0: her brother owns the mill"        # a user message
ASSISTANT_QUOTE = "line 1: her brother owns the mill"   # an assistant one

#: The `_five_notes` positions a proposal may retire: 1 is the reader's own
#: and 2 is pinned, so those two are categories of their own.
MODEL_NOTES = [0, 3, 4]
#: The two bulk cases are SIZED BY THE PRODUCTION CONSTANT rather than
#: spelled out. `notebook_store.BULK_CONFLICT_MIN` is the number of notes a
#: single proposal may retire before the gate holds it for review; one case
#: sits ON it and its ZEMIN sits one under it, so raising the constant moves
#: both cases with it instead of leaving two fact lists describing an old
#: threshold.
AT_BULK = MODEL_NOTES[:notebook.BULK_CONFLICT_MIN]
UNDER_BULK = MODEL_NOTES[:notebook.BULK_CONFLICT_MIN - 1]


def _replacing(*positions: int) -> list[dict]:
    """One anchored proposal per position, each retiring that note."""
    return [fact(text=f"The mill, replacing note {p}.",
                 evidence=USER_QUOTE, supersedes=p) for p in positions]

CASES = {
    # name: (facts, expected statuses, retired indexes)
    "names the reader's note": (
        [fact(text="Replaces one.", evidence=USER_QUOTE, supersedes=1)],
        ["proposed"], []),
    "names a pinned note": (
        [fact(text="Replaces two.", evidence=USER_QUOTE, supersedes=2)],
        ["proposed"], []),
    "notes at the bulk threshold": (
        _replacing(*AT_BULK),
        ["proposed"] * len(AT_BULK), []),
    "quotes the model itself while retiring": (
        [fact(text="The mill, replacing note zero.",
              evidence=ASSISTANT_QUOTE, supersedes=0)],
        ["proposed"], []),
    "ZEMIN: quotes the reader while retiring": (
        [fact(text="The mill, replacing note zero.",
              evidence=USER_QUOTE, supersedes=0)],
        ["accepted"], [0]),
    "ZEMIN: one under the bulk threshold": (
        _replacing(*UNDER_BULK),
        ["accepted"] * len(UNDER_BULK), list(UNDER_BULK)),
    "ZEMIN: replaces nothing": (
        [fact(text="The mill is a plain new fact.", evidence=USER_QUOTE)],
        ["accepted"], []),
}


async def _drive_incremental(chat_id: int) -> None:
    await notebook_worker.worker._handle(chat_id)


async def _drive_all(chat_id: int) -> None:
    await run_and_wait(chat_id, "all")


async def _drive_range(chat_id: int) -> None:
    with get_db() as con:
        lo, _ = notebook.message_span(con, chat_id)
    await run_and_wait(chat_id, "range", lo, lo + BATCH - 1)


class TestTheReviewQueueGate:
    @pytest.mark.parametrize("case", list(CASES))
    @pytest.mark.parametrize("drive", [_drive_incremental, _drive_all, _drive_range],
                             ids=["incremental", "all", "range"])
    @pytest.mark.anyio
    async def test_four_categories_wait_and_the_rest_are_kept(
            self, client, monkeypatch, case, drive) -> None:
        """T-73. Auto-accept at its default (ON); the same answer in all
        three modes because the gate lives in `commit_extraction`."""
        facts, expected, retired = CASES[case]
        assert len(MODEL_NOTES) >= notebook.BULK_CONFLICT_MIN, (
            "ground: the fixture carries fewer model notes than the bulk "
            "threshold needs, so the two bulk cases stopped being about it")
        # Thirty seeded rows plus the greeting: over the threshold, under
        # the first-read edge, so every driver's first unit is messages 1-20
        # and both quotes are inside it.
        chat_id = seed(client, count=BATCH + 10)
        choose(chat_id)
        notes = _five_notes(chat_id)
        provider(monkeypatch, facts=lambda n: facts if n == 1 else [])

        await drive(chat_id)

        rows = notebook.list_entries(chat_id)
        written = [e for e in rows if e["id"] > notes[-1]["id"]]
        assert [e["status"] for e in written] == expected, case
        by_id = {e["id"]: e for e in rows}
        for i, note in enumerate(notes):
            is_retired = by_id[note["id"]]["retired_at"] is not None
            assert is_retired == (i in retired), (case, i)


class TestUndoCountsAReorderAsTheReadersHand:
    """`updated_at` has exactly one reader - "did the reader make this note
    theirs" - and four writers, and one of them was stamping a bare
    `datetime('now')`.

    Both columns default to the same second-resolution clock, so a note
    DRAGGED inside the second it was written in kept `updated_at` equal to
    `created_at` and the undo removed it, while the same note EDITED in
    that second was kept. Two answers to one question, decided by which
    writer happened to run.
    """

    @pytest.mark.anyio
    async def test_a_note_dragged_in_its_own_second_is_still_theirs(
            self, client, chat, monkeypatch) -> None:
        provider(monkeypatch)
        out = await run_and_wait(chat, "next")
        wrote = [e for e in notebook.list_entries(chat)
                 if e["provenance"] == notebook.PROV_MODEL]
        assert wrote, f"ground: the run wrote notes, {wrote}"
        fresh = {e["id"]: e for e in notebook.list_entries(chat)}
        assert all(fresh[e["id"]]["updated_at"] == fresh[e["id"]]["created_at"]
                   for e in wrote), "ground: nothing has touched them yet"

        # The drag, in the same clock second the notes were written in -
        # which is every drag in this suite and most of them in life. The
        # list has to be COMPLETE, so it is the chat's own order reversed.
        notebook.reorder(chat, [e["id"] for e in
                                reversed(notebook.list_entries(chat))])

        moved = {e["id"]: e for e in notebook.list_entries(chat)}
        assert all(moved[e["id"]]["updated_at"] > moved[e["id"]]["created_at"]
                   for e in wrote), (
            "a reorder left the column equal, so the undo cannot tell a note "
            "the reader arranged from one nobody has touched")

        r = client.post(f"{API}/extract/undo/{out['job_id']}?chat_id={chat}")

        assert r.status_code == 200, r.text
        assert r.json()["removed"] == 0, r.json()
        assert r.json()["kept_edited"] == len(wrote), r.json()
        left = {e["id"] for e in notebook.list_entries(chat)}
        assert {e["id"] for e in wrote} <= left

    @pytest.mark.anyio
    async def test_GROUND_an_untouched_note_is_still_removed(
            self, client, chat, monkeypatch) -> None:
        """Without this the row above passes on an undo that removes
        nothing at all."""
        provider(monkeypatch)
        out = await run_and_wait(chat, "next")
        wrote = [e for e in notebook.list_entries(chat)
                 if e["provenance"] == notebook.PROV_MODEL]

        r = client.post(f"{API}/extract/undo/{out['job_id']}?chat_id={chat}")

        assert r.json()["removed"] == len(wrote), r.json()
        assert r.json()["kept_edited"] == 0, r.json()


class TestTheReviewQueueGateOnTheAutomaticDoor:
    """T-73 / KARAR-D: "Kip fark etmez: kip 1, kip 2 ve kip 3 ayni kurala
    tabidir."

    The matrix above drives three doors. This is the fourth: the door
    nobody presses, a queue offer on a chat that started with no notes at
    all, which `_five_notes` cannot set up. It used to be mode 1's
    whole-chat job; since decisions 30 a and 31 a it is the ordinary reader,
    offered twice, and the note the gate is asked about is one that reader
    itself wrote on the first offer.
    """

    #: A quote has to be INSIDE the read that makes it - every proposal is
    #: checked against the messages that read covered - so the second
    #: read's evidence names a line from its own range. Read one is the
    #: greeting plus lines 0..18; read two is lines 19..38, where line 20
    #: is the reader's and line 21 the model's (both writers alternate by
    #: parity). The line number rides in the note's own text because it is
    #: an anchor: `_is_anchored` wants the note to keep one untranslatable
    #: word of its quote, and the number is one.
    USER_QUOTE_2 = "line 20: her brother owns the mill"
    ASSISTANT_QUOTE_2 = "line 21: her brother owns the mill"
    FIRST_NOTE = "Her brother owns the mill."

    async def _drive_mode_one(self, client, monkeypatch, quote: str,
                              replacement: str) -> int:
        """The automatic door over a chat with no notes: the first offer
        writes a note, the second proposes one that names it."""
        chat_id = seed(client, count=BATCH - 1)
        choose(chat_id)
        assert notebook.list_entries(chat_id) == [], "ground: no notes yet"

        def facts(n):
            if n == 1:
                return [fact(text=self.FIRST_NOTE, evidence=USER_QUOTE)]
            return [fact(text=replacement, evidence=quote, supersedes=0)]

        calls = provider(monkeypatch, facts=facts)
        w = notebook_worker.Worker()
        w.queue = asyncio.Queue(maxsize=8)

        await offer_and_drain(w, chat_id)
        with get_db() as con:
            for i in range(BATCH - 1, 2 * BATCH - 1):
                con.execute(
                    "INSERT INTO messages (chat_id, role, content, active) "
                    "VALUES (?,?,?,1)",
                    (chat_id, "user" if i % 2 == 0 else "assistant",
                     f"line {i}: her brother owns the mill"))
        await offer_and_drain(w, chat_id)

        assert job_count(chat_id) == 0, (
            "ground: nobody pressed anything and no job was made")
        assert len(calls) == 2, "ground: two offers, two reads"
        return chat_id

    @pytest.mark.anyio
    async def test_a_proposal_quoting_the_model_waits_even_here(
            self, client, monkeypatch) -> None:
        replacement = "The mill at line 21, replacing the first note."
        chat_id = await self._drive_mode_one(
            client, monkeypatch, self.ASSISTANT_QUOTE_2, replacement)

        rows = {e["text"]: e for e in notebook.list_entries(chat_id)}
        assert replacement in rows, rows
        assert rows[replacement]["status"] == "proposed", (
            "a proposal quoting the model itself while retiring was "
            "auto-accepted on the automatic door")
        assert rows[replacement]["evidence_role"] == "assistant"
        assert rows[self.FIRST_NOTE]["retired_at"] is None, (
            "and it retired the note it named")

    @pytest.mark.anyio
    async def test_ZEMIN_the_same_shape_quoting_the_reader_is_accepted(
            self, client, monkeypatch) -> None:
        """The ground control, same door, one field different: change whose
        words are quoted and the gate lets it through. Without this the row
        above passes on a mode 1 that holds everything, or on one that
        writes nothing at all."""
        replacement = "The mill at line 20, replacing the first note."
        chat_id = await self._drive_mode_one(
            client, monkeypatch, self.USER_QUOTE_2, replacement)

        rows = {e["text"]: e for e in notebook.list_entries(chat_id)}
        assert replacement in rows, rows
        assert rows[replacement]["status"] == "accepted", rows[replacement]
        assert rows[replacement]["evidence_role"] == "user"
        assert rows[self.FIRST_NOTE]["retired_at"] is not None, (
            "the retirement it asked for did not happen")


# ── The stand-down spends one grace in total ──────────────────────────────


class TestTheStandDownSpendsOneGraceInTotal:
    """`quiesce()` is the last thing that runs before `lock_vault_now`
    clears the vault key, so every second it spends is a second the key sits
    in memory with decrypted material beside it. SECURITY.md and README both
    say that wait is five seconds.

    It does three bounded things in a row: stand the reading job down, wait
    out the worker loop's cancellation, wait for an already-paid reply to
    land. Two of the three used to carry their own full grace and the middle
    one carried no bound at all, so the true worst case was "forever" and
    the best bad case was three graces for a document that promises one.
    """

    def test_the_grace_is_the_five_seconds_the_documents_state(self) -> None:
        """The only guard on the number itself.

        Every other test here monkeypatches this constant so it does not
        spend five real seconds, which means all of them stay green if
        somebody sets it to thirty - while SECURITY.md and README go quietly
        false. This is the one row that reads it as written.
        """
        assert notebook_worker.SETTLE_GRACE_S == 5.0

    @pytest.mark.anyio
    async def test_a_reader_loop_that_swallows_its_cancel_is_left_behind(
            self, db, monkeypatch) -> None:
        # 0.4, not 0.2. This row's budget is a multiple of the grace, so a
        # short grace makes the whole allowance a fraction of a second on a
        # machine that is running the rest of the suite beside it - the
        # margin then measures the scheduler rather than the bound. Doubling
        # the constant doubles the allowance without widening it in graces,
        # which is what the assertion is actually about.
        monkeypatch.setattr(notebook_worker, "SETTLE_GRACE_S", 0.4)
        w = notebook_worker.worker
        alive, release = asyncio.Event(), asyncio.Event()

        async def never_stands_down() -> None:
            # Cancellation asked for, refused, still running: a loop wedged
            # on a thread or a foreign lock looks exactly like this from
            # the outside. `release` is the test's way back out; production
            # has none, which is the point.
            alive.set()
            while not release.is_set():
                try:
                    await asyncio.sleep(0.01)
                except asyncio.CancelledError:
                    pass

        loop = asyncio.get_running_loop()
        task = loop.create_task(never_stands_down())
        old_task, old_queue = w.task, w.queue
        w.task, w.queue = task, asyncio.Queue()
        try:
            await asyncio.wait_for(alive.wait(), 5)
            assert not task.done(), "ground: the loop really is running"

            began = time.monotonic()
            # `wait_for` so a missing bound is a RED TEST and not a hung
            # suite: with no timeout on the loop's own wait this never
            # returns, and a suite that never returns reports nothing.
            await asyncio.wait_for(notebook_worker.quiesce(), 10)
            elapsed = time.monotonic() - began

            assert elapsed < 2 * notebook_worker.SETTLE_GRACE_S, (
                f"the lock waited {elapsed:.2f}s on a reader loop that will "
                "not go; the stand-down is not bounded")
            assert not task.done(), (
                "ground: it is still running, so what returned was the "
                "bound and not the loop finishing")
        finally:
            release.set()
            await asyncio.wait({task}, timeout=5)
            fresh = w.task
            if fresh is not None and fresh is not task and not fresh.done():
                fresh.cancel()
                await asyncio.wait({fresh}, timeout=5)
            w.task, w.queue = old_task, old_queue

    @pytest.mark.anyio
    async def test_two_wedged_steps_still_share_the_one_deadline(
            self, db, monkeypatch) -> None:
        """Both halves refusing at once is the case that tells one deadline
        from two: two graces in a row is twice the number the document
        states, and nothing on screen would say so."""
        monkeypatch.setattr(notebook_worker, "SETTLE_GRACE_S", 0.4)
        w = notebook_worker.worker
        release = asyncio.Event()
        ready = asyncio.Event()

        async def never_stands_down() -> None:
            while not release.is_set():
                try:
                    await asyncio.sleep(0.01)
                except asyncio.CancelledError:
                    pass

        loop = asyncio.get_running_loop()
        job_task = loop.create_task(never_stands_down())
        loop_task = loop.create_task(never_stands_down())
        loop.call_soon(ready.set)
        old_task, old_queue = w.task, w.queue
        w._backfill_task = job_task
        w._backfill = {"id": 1, "chat_id": 1}
        w.task, w.queue = loop_task, asyncio.Queue()
        try:
            await ready.wait()
            began = time.monotonic()
            await asyncio.wait_for(notebook_worker.quiesce(), 10)
            elapsed = time.monotonic() - began
            # Under two deadlines this is at least 2 x 0.4; under one it is
            # a shade over 0.4. The ground control for the whole assertion
            # is the pair still being alive below: nothing finished early.
            #
            # 1.5 graces, not 2. At the boundary the two-deadline mutant
            # failed by eleven milliseconds on eight hundred - the
            # scheduler's own overhead rather than a gap anybody designed,
            # so a slow machine would have made it pass. Halfway between
            # one grace and two is a bound the correct code clears by 50%
            # of a grace and the mutant misses by 50% of a grace, which is
            # a measurement rather than a coin toss.
            assert elapsed < 1.5 * notebook_worker.SETTLE_GRACE_S, (
                f"the stand-down spent {elapsed:.2f}s, which is more than "
                "the one grace the documents promise")
            # And it really did wait: a stand-down that returned at once
            # would satisfy the bound above and mean the opposite thing.
            assert elapsed >= notebook_worker.SETTLE_GRACE_S * 0.5, (
                f"the stand-down returned in {elapsed:.2f}s, so it did not "
                "wait for the wedged steps at all")
            assert not job_task.done() and not loop_task.done(), (
                "ground: both are still wedged, so the bound is what "
                "returned")
        finally:
            release.set()
            await asyncio.wait({job_task, loop_task}, timeout=5)
            fresh = w.task
            if fresh is not None and fresh is not loop_task and not fresh.done():
                fresh.cancel()
                await asyncio.wait({fresh}, timeout=5)
            w.task, w.queue = old_task, old_queue


# ── A stop that arrives while its own job is being written ────────────────


class TestAStopIsNotErasedByTheJobStarting:
    """The panel said "stopping after this one" and the job billed every
    call it had planned.

    `_create` claims `self._backfill` on its worker thread, so from that
    instant `stop_backfill` passes its guard and can set the stop flag. The
    line that cleared that flag used to run when this coroutine was next
    scheduled - after the thread had finished - so a stop landing in the
    handoff was written straight back to False. The route had already
    answered `{"stopped": true}`. Nothing raised, no counter moved, nothing
    reached a log.
    """

    @pytest.mark.anyio
    async def test_a_stop_during_job_creation_is_honoured(
            self, db, client, chat, monkeypatch) -> None:
        choose(chat)
        calls = provider(monkeypatch)
        w = notebook_worker.worker

        async def stop_as_soon_as_the_row_exists() -> dict:
            while w._backfill is None:
                await asyncio.sleep(0)
            return await w.stop_backfill(chat)

        racer = asyncio.create_task(stop_as_soon_as_the_row_exists())
        out = await w.backfill(chat, "all")
        answer = await racer

        assert out["started"] is True, out
        # GROUND: the stop really did land, and it was told it had.
        assert answer["stopped"] is True, answer
        assert w._backfill_stop is True, (
            "the stop was erased by the press that started the job")

        await w.wait_for_backfill()
        assert out["calls_planned"] > 1, (
            "ground: it had more than one call planned, so stopping early "
            "is a difference this test can see")
        assert len(calls) <= 1, (
            f"the job billed {len(calls)} of {out['calls_planned']} planned "
            "calls after a stop it had already accepted")
        assert spend() == len(calls), "ground: the money agrees with the calls"
        row = job_of(chat)
        assert row["state"] == "stopped", row["state"]


# ── Undo on a job nobody is driving ───────────────────────────────────────


class TestUndoOfAJobNobodyIsDriving:
    """The app killed with its window leaves a row that says `running`.

    The undo route closes that row out itself so the reader is not told to
    stop a job nothing can stop. It opens its own write transaction first,
    and the settle it then calls opened a second one on the same connection:
    "cannot start a transaction within a transaction", a 500, and a job that
    could never be undone - on exactly the case the path exists for.
    """

    @pytest.mark.anyio
    async def test_it_answers_and_removes_the_notes(
            self, db, client, chat, monkeypatch) -> None:
        choose(chat)
        provider(monkeypatch)
        await run_and_wait(chat, "next")
        job_id = job_of(chat)["id"]
        # The row is put back the way a killed process leaves it: still
        # saying `running`, with nothing in this process driving it.
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                "UPDATE notebook_backfill_jobs SET state = 'running', "
                "finished_at = NULL, last_error = NULL WHERE id = ?",
                (job_id,))
        notebook_worker.worker._backfill = None
        notebook_worker.worker._backfill_task = None
        # GROUND: the job wrote something, so "removed" has somewhere to go.
        assert notebook.list_entries(chat), "ground: the run wrote notes"

        answer = client.post(f"{API}/extract/undo/{job_id}?chat_id={chat}")

        assert answer.status_code == 200, answer.text
        assert answer.json()["removed"] >= 1, answer.json()
        assert not [e for e in notebook.list_entries(chat)
                    if e.get("job_id") == job_id]
        assert job_of(chat)["state"] == "stopped", (
            "the row nobody was driving was left saying it was running")


# ── Undo removes what the job itself retired ──────────────────────────────


class TestUndoRemovesWhatTheJobItselfRetired:
    """`updated_at` is the only signal anything has for "the reader made
    this note theirs", and the app was stamping it too.

    A job writes a note, a later unit of the same job replaces it, and
    `retire_superseded` used to stamp `updated_at` on the way past. The undo
    then read that stamp as a reader edit: it kept the note, the repair
    cleared its `retired_at`, and the reader was told they had edited
    something they had never touched. Both notes are the job's work and both
    go.
    """

    @pytest.mark.anyio
    async def test_a_note_the_job_replaced_is_removed_and_not_called_mine(
            self, db, client, chat, monkeypatch) -> None:
        choose(chat)
        provider(monkeypatch)
        await run_and_wait(chat, "next")
        wrote = notebook.list_entries(chat)[0]
        job_id = job_of(chat)["id"]
        # AGED ON PURPOSE, and without it this test cannot fail. Both
        # columns default to `datetime('now')` and SQLite freezes `now` per
        # statement, so in a suite this fast the write and the retirement
        # land in the same clock SECOND: a retirement that stamps
        # `updated_at` still leaves it equal to `created_at`, and the note
        # is classified correctly by accident. Moved back an hour, the two
        # are equal only while nothing stamps the column, which is the
        # thing under test.
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                "UPDATE notebook_entries SET created_at = "
                "datetime('now', '-1 hour'), updated_at = "
                "datetime('now', '-1 hour') WHERE id = ?", (wrote["id"],))
        fresh = {e["id"]: e for e in notebook.list_entries(chat)}[wrote["id"]]
        assert fresh["updated_at"] == fresh["created_at"], (
            "ground: nothing has touched it yet")

        # A later read replaces it. Nothing the reader did; the app did it.
        with get_db() as con:
            lo, _ = notebook.message_span(con, chat)
        monkeypatch.setattr(openrouter, "complete", None)
        provider(monkeypatch, facts=lambda n: [
            fact(text="Her brother sold the mill.", supersedes=0)])
        await run_and_wait(chat, "range", lo, lo + BATCH - 1)
        rows = {e["id"]: e for e in notebook.list_entries(chat)}
        # GROUND: the note really is retired now, so the arm this is about
        # is the one the classification will reach.
        assert rows[wrote["id"]]["retired_at"] is not None, (
            f"ground: nothing was retired, rows={rows}")

        answer = client.post(f"{API}/extract/undo/{job_id}?chat_id={chat}")

        body = answer.json()
        assert answer.status_code == 200, answer.text
        assert body["kept_edited"] == 0, (
            f"the reader was told they had edited a note the app retired: "
            f"{body}")
        assert body["removed"] == 1, body
        left = {e["id"] for e in notebook.list_entries(chat)}
        assert wrote["id"] not in left, (
            "a note the job wrote and the app then retired survived the "
            "undo, and came back into the prompt un-retired")

    @pytest.mark.anyio
    async def test_a_note_the_reader_edited_is_kept_even_once_retired(
            self, db, client, chat, monkeypatch) -> None:
        """POSITIVE CONTROL for the arm order above: the reader's own edit
        outranks a retirement the app did afterwards, which is the promise
        the docstring on `delete_job_notes` makes. Without it, moving the
        retired test back above the edited test would cost nothing.

        One job writes the note, the reader edits it, a second job retires
        it, and the FIRST job's undo has to leave it alone."""
        choose(chat)
        provider(monkeypatch)
        await run_and_wait(chat, "next")
        mine = notebook.list_entries(chat)[0]
        first_job = job_of(chat)["id"]
        notebook.update_entry(mine["id"], text="Her brother owns the mill!")

        with get_db() as con:
            lo, _ = notebook.message_span(con, chat)
        monkeypatch.setattr(openrouter, "complete", None)
        provider(monkeypatch, facts=lambda n: [
            fact(text="Her brother sold the mill.", supersedes=0)])
        await run_and_wait(chat, "range", lo, lo + BATCH - 1)
        rows = {e["id"]: e for e in notebook.list_entries(chat)}
        assert rows[mine["id"]]["retired_at"] is not None, (
            "ground: the second job really did retire the edited note")

        answer = client.post(
            f"{API}/extract/undo/{first_job}?chat_id={chat}")

        body = answer.json()
        assert answer.status_code == 200, answer.text
        assert body["kept_edited"] == 1, body
        left = {e["id"] for e in notebook.list_entries(chat)}
        assert mine["id"] in left, (
            f"the note the reader edited was deleted by the undo: {body}")

    @pytest.mark.anyio
    async def test_undoing_the_job_that_retired_a_note_brings_it_back(
            self, db, client, chat, monkeypatch) -> None:
        """The arm `delete_job_notes`' repair exists for, and the one no
        row reached.

        The docstring promises it in so many words: "anything that pointed
        at a doomed row stops pointing AND stops being retired, so a note
        this job superseded comes back into the prompt when the note that
        replaced it is gone". Both existing rows here undo the job that did
        the WRITING - in the first the superseded note is deleted with it,
        in the second the pointer is held by a job that is not being undone
        - so `retired_at = NULL` could be dropped from that UPDATE and
        nothing went red. The consequence is silent and permanent: a note
        the reader never touched leaves the prompt for good, the undo
        reports success, and the note is still on screen.

        So: job one writes it, the reader edits it (which is what keeps it
        alive through job two's undo), job two retires it, and job TWO is
        the one undone.
        """
        choose(chat)
        provider(monkeypatch)
        await run_and_wait(chat, "next")
        mine = notebook.list_entries(chat)[0]
        notebook.update_entry(mine["id"], text="Her brother owns the mill!")

        with get_db() as con:
            lo, _ = notebook.message_span(con, chat)
        monkeypatch.setattr(openrouter, "complete", None)
        provider(monkeypatch, facts=lambda n: [
            fact(text="Her brother sold the mill.", supersedes=0)])
        await run_and_wait(chat, "range", lo, lo + BATCH - 1)
        second_job = job_of(chat)["id"]
        rows = {e["id"]: e for e in notebook.list_entries(chat)}
        assert rows[mine["id"]]["retired_at"] is not None, (
            "ground: the second job really did retire it")
        assert rows[mine["id"]]["superseded_by"] is not None, (
            "ground: and something really is pointing at it")

        answer = client.post(f"{API}/extract/undo/{second_job}?chat_id={chat}")

        assert answer.status_code == 200, answer.text
        after = {e["id"]: e for e in notebook.list_entries(chat)}
        assert mine["id"] in after, "the note the undo was not about was deleted"
        assert after[mine["id"]]["retired_at"] is None, (
            "the note stayed retired on the strength of a row that no longer "
            "exists, so it is out of the prompt for good")
        assert after[mine["id"]]["superseded_by"] is None, (
            "it still points at the deleted row")
        # POSITIVE CONTROL: the repair is a repair, not a blanket un-retire.
        # A note retired by hand, pointing at nothing this undo touched, is
        # left exactly as it was.
        other = notebook.create_entry(chat, "Retired by hand.")
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute("UPDATE notebook_entries SET retired_at = "
                        "datetime('now') WHERE id = ?", (other["id"],))
        third = await run_and_wait(chat, "next")
        client.post(f"{API}/extract/undo/{third['job_id']}?chat_id={chat}")
        still = {e["id"]: e for e in notebook.list_entries(chat)}
        assert still[other["id"]]["retired_at"] is not None, (
            "the repair un-retired a note it never retired")
