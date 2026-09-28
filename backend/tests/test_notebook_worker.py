"""FAZ 5 - the part that runs when nobody is watching.

Everything here is about money and silence. The worker spends the user's own
credits on their behalf, unattended, and the two ways that goes wrong are a
loop that will not stop and a refusal nobody can see.

The circuit breaker, the daily block and the work key are each tested for the
state they are supposed to prevent, not for the happy path.
"""
from __future__ import annotations

import asyncio

import pytest

import config
import notebook_extract
import notebook_store as notebook
import notebook_worker
from database import get_db

from tests.conftest import make_character, make_chat


@pytest.fixture
def fresh_worker():
    """A worker per test. The module-level one is process-wide state, and a
    breaker left open by one test would refuse in the next."""
    w = notebook_worker.Worker()
    w.queue = asyncio.Queue(maxsize=notebook_worker.QUEUE_MAXSIZE)
    return w


def seed(client, count: int = 4) -> int:
    """A chat with `count` real messages in it."""
    chat_id = make_chat(client, make_character(client))
    with get_db() as con:
        for i in range(count):
            con.execute(
                "INSERT INTO messages (chat_id, role, content, active) "
                "VALUES (?,?,?,1)",
                (chat_id, "user" if i % 2 == 0 else "assistant",
                 f"line {i}: her brother owns the mill"))
    return chat_id


def fact(**over):
    base = {"text": "Her brother owns the mill.",
            "evidence": "her brother owns the mill",
            "kind": "fact", "durability": "permanent",
            "importance": 2, "supersedes": None}
    base.update(over)
    return base


class TestTheQueueIsBounded:
    def test_the_default_would_have_been_infinite(self) -> None:
        """`asyncio.Queue(maxsize=0)` is the default and it means UNBOUNDED -
        the setting that reads like "no queueing" and is its opposite. On a
        machine that cannot reach the provider, an unbounded queue grows for
        as long as the user keeps typing."""
        assert notebook_worker.QUEUE_MAXSIZE > 0

    def test_a_full_queue_drops_the_OLDEST(self, fresh_worker) -> None:
        """The newest turn is the one worth reading. A queue that dropped the
        newest would starve exactly the chat somebody is using."""
        for i in range(notebook_worker.QUEUE_MAXSIZE):
            fresh_worker.offer(i)
        fresh_worker.offer(999)
        assert fresh_worker.dropped_offers == 1
        assert fresh_worker.queue.get_nowait() == 1      # 0 was pushed out
        assert 999 in list(fresh_worker.queue._queue)

    def test_offering_before_the_worker_starts_does_not_raise(self) -> None:
        """The send path calls this. A background feature that can make a
        message fail to send is not a feature."""
        w = notebook_worker.Worker()
        w.offer(1)
        assert w.dropped_offers == 1

    def test_the_queue_is_not_built_at_import_time(self) -> None:
        """An asyncio.Queue binds to the loop it is first used on. Built on a
        module-level object it belongs to whichever loop existed at import,
        and every later loop - a restart, a second test client - gets "bound
        to a different event loop" from a queue that looks healthy."""
        assert notebook_worker.Worker().queue is None


class TestTheBreaker:
    def test_it_closes_over_a_healthy_run(self, fresh_worker) -> None:
        assert fresh_worker.breaker.allows(0.0)

    def test_five_failures_open_it(self, fresh_worker) -> None:
        b = fresh_worker.breaker
        for _ in range(notebook_worker.TRIP_AFTER):
            b.failed(0.0)
        assert b.state == "open"
        assert not b.allows(0.0)

    def test_four_failures_do_NOT(self, fresh_worker) -> None:
        """The control. A breaker that trips on the first hiccup is a breaker
        that turns one bad minute into a dead feature."""
        b = fresh_worker.breaker
        for _ in range(notebook_worker.TRIP_AFTER - 1):
            b.failed(0.0)
        assert b.state == "closed"

    def test_a_success_closes_it_again(self, fresh_worker) -> None:
        b = fresh_worker.breaker
        for _ in range(notebook_worker.TRIP_AFTER):
            b.failed(0.0)
        b.succeeded()
        assert b.state == "closed" and b.allows(0.0)

    def test_the_cooldown_expires_into_half_open(self, fresh_worker) -> None:
        b = fresh_worker.breaker
        for _ in range(notebook_worker.TRIP_AFTER):
            b.failed(0.0)
        assert not b.allows(b.cooldown - 1)
        assert b.allows(b.cooldown + 1)

    def test_the_cooldown_GROWS_so_it_cannot_flap(self, fresh_worker) -> None:
        """Open -> Half-Open -> Open at a fixed interval is a breaker that
        does not protect anything: each cycle is another billed call into a
        provider that is still broken."""
        b = fresh_worker.breaker
        for _ in range(notebook_worker.TRIP_AFTER):
            b.failed(0.0)
        first = b.cooldown
        b.failed(b.cooldown + 1)          # the half-open probe also failed
        assert b.cooldown > first

    def test_twenty_failures_stop_it_entirely(self, fresh_worker) -> None:
        b = fresh_worker.breaker
        for i in range(notebook_worker.STOP_AFTER):
            b.failed(float(i))
        assert b.state == "stopped"
        assert not b.allows(1e9), "a stopped breaker must not time out into life"

    def test_a_stopped_breaker_can_be_reset_by_hand(self, fresh_worker) -> None:
        """Otherwise recovering from a provider outage means restarting the
        whole application after fixing it - a breaker plus an insult."""
        b = fresh_worker.breaker
        for i in range(notebook_worker.STOP_AFTER):
            b.failed(float(i))
        b.reset()
        assert b.state == "closed" and b.allows(0.0)

    def test_the_batch_halves_while_it_is_unhappy(self, fresh_worker) -> None:
        """Sending the same large batch into a failing provider spends the
        most money at the moment it is least likely to get anything back."""
        full = fresh_worker.batch_size
        fresh_worker.breaker.failed(0.0)
        assert fresh_worker.batch_size < full
        fresh_worker.breaker.succeeded()
        assert fresh_worker.batch_size == full

    def test_the_batch_never_reaches_zero(self, fresh_worker) -> None:
        for _ in range(4):
            fresh_worker.breaker.failed(0.0)
        assert fresh_worker.batch_size >= 2


class TestOneExtractionIsOneTransaction:
    """The key, the proposals, the retirement and the cost land together or
    not at all. Every partial state is a bug somebody has shipped."""

    def test_a_run_writes_the_key_and_the_notes(self, client) -> None:
        chat_id = seed(client)
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            out = notebook.commit_extraction(
                con, work_key="k1", chat_id=chat_id, from_id=1, to_id=4,
                proposals=[fact()], usage={"cost": 0.0001})
        assert out["written"] == 1
        entries = notebook.list_entries(chat_id)
        assert len(entries) == 1
        assert entries[0]["provenance"] == "model"

    def test_the_same_key_twice_writes_NOTHING_the_second_time(self, client):
        """The idempotent-consumer answer. Without it, a range answered once
        is paid for and duplicated on every later attempt."""
        chat_id = seed(client)
        for _ in range(2):
            with get_db() as con:
                con.execute("BEGIN IMMEDIATE")
                out = notebook.commit_extraction(
                    con, work_key="same", chat_id=chat_id, from_id=1, to_id=4,
                    proposals=[fact()])
        assert out["duplicate"] is True
        assert len(notebook.list_entries(chat_id)) == 1

    def test_a_supersede_retires_its_target_in_the_SAME_transaction(
            self, client) -> None:
        """G-15. Written separately, the old note outlives the thing that
        replaced it and both go into the next payload."""
        chat_id = seed(client)
        # provenance=model, on purpose. A model's suggestion may only retire
        # a note the model itself wrote; the fixture used to build a USER
        # note and assert that a model suggestion retired it, which is the
        # defect this file now guards against rather than the behaviour it
        # measures. What is being tested here is that retirement happens in
        # the SAME transaction, so the note has to be a retirable one.
        old = notebook.create_entry(
            chat_id, "The mill belonged to her uncle.",
            provenance=notebook.PROV_MODEL)
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.commit_extraction(
                con, work_key="k2", chat_id=chat_id, from_id=1, to_id=4,
                proposals=[fact(supersedes=0)], existing_ids=[old["id"]])
        rows = {e["id"]: e for e in notebook.list_entries(chat_id)}
        assert rows[old["id"]]["retired_at"] is not None
        assert rows[old["id"]]["superseded_by"] is not None

    def test_a_retired_note_is_not_in_the_next_payload(self, client) -> None:
        chat_id = seed(client)
        # provenance=model, on purpose. A model's suggestion may only retire
        # a note the model itself wrote; the fixture used to build a USER
        # note and assert that a model suggestion retired it, which is the
        # defect this file now guards against rather than the behaviour it
        # measures. What is being tested here is that retirement happens in
        # the SAME transaction, so the note has to be a retirable one.
        old = notebook.create_entry(
            chat_id, "The mill belonged to her uncle.",
            provenance=notebook.PROV_MODEL)
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.commit_extraction(
                con, work_key="k3", chat_id=chat_id, from_id=1, to_id=4,
                proposals=[fact(supersedes=0)], existing_ids=[old["id"]])
        block = notebook.build_notebook_blocks(chat_id, 9000)
        assert "uncle" not in (block["user_block"] + block["model_block"])

    def test_a_supersedes_index_pointing_nowhere_retires_nothing(self, client):
        chat_id = seed(client)
        keep = notebook.create_entry(chat_id, "Still true.")
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.commit_extraction(
                con, work_key="k4", chat_id=chat_id, from_id=1, to_id=4,
                proposals=[fact(supersedes=7)], existing_ids=[keep["id"]])
        rows = {e["id"]: e for e in notebook.list_entries(chat_id)}
        assert rows[keep["id"]]["retired_at"] is None

    def test_the_cost_lands_with_the_notes(self, client) -> None:
        chat_id = seed(client)
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.commit_extraction(
                con, work_key="k5", chat_id=chat_id, from_id=1, to_id=4,
                proposals=[fact()],
                usage={"tokens_in": 900, "tokens_out": 40, "cost": 0.00007})
        with get_db() as con:
            assert notebook.spend_today(con)["cost"] == pytest.approx(0.00007)


class TestAutoAccept:
    def test_the_default_is_ON(self, client) -> None:
        """ON is the settled default, and an unset key IS the default.
        Reading unset as "off" would make a fresh install do nothing and look
        like a broken worker rather than a setting."""
        chat_id = seed(client)
        with get_db() as con:
            assert notebook.auto_accept_for(con, chat_id) is True

    def test_turning_it_off_makes_proposals_wait(self, client) -> None:
        import database
        chat_id = seed(client)
        database.set_setting(config.SETTING_NOTEBOOK_AUTO_ACCEPT, "0")
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.commit_extraction(
                con, work_key="k6", chat_id=chat_id, from_id=1, to_id=4,
                proposals=[fact()])
        assert notebook.list_entries(chat_id)[0]["status"] == "proposed"

    def test_a_proposed_note_never_reaches_the_payload(self, client) -> None:
        """G-2. The carrying defence when review is on."""
        import database
        chat_id = seed(client)
        database.set_setting(config.SETTING_NOTEBOOK_AUTO_ACCEPT, "0")
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.commit_extraction(
                con, work_key="k7", chat_id=chat_id, from_id=1, to_id=4,
                proposals=[fact()])
        block = notebook.build_notebook_blocks(chat_id, 9000)
        assert "mill" not in (block["user_block"] + block["model_block"])

    def test_a_chat_override_beats_the_global_switch(self, client) -> None:
        """An imported card or lorebook forces review no matter what the
        general setting says: it is somebody else's text arriving in bulk, and
        item-by-item review is the effort a salami attack defeats."""
        chat_id = seed(client)
        with get_db() as con:
            con.execute(
                "UPDATE chats SET notebook_auto_accept_override = 0 WHERE id = ?",
                (chat_id,))
            assert notebook.auto_accept_for(con, chat_id) is False

    def test_accepting_a_proposal_does_not_change_its_provenance(self, client):
        """G-3. Promotion is the classic bypass: if accepting could rewrite
        provenance, `provenance='model'` would have no live rows and its guard
        would pass by describing an empty set."""
        import database
        chat_id = seed(client)
        database.set_setting(config.SETTING_NOTEBOOK_AUTO_ACCEPT, "0")
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.commit_extraction(
                con, work_key="k8", chat_id=chat_id, from_id=1, to_id=4,
                proposals=[fact()])
        entry = notebook.list_entries(chat_id)[0]

        resp = client.post(f"/api/v1/notebook/entries/{entry['id']}/accept"
                           f"?chat_id={chat_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "accepted"
        assert resp.json()["provenance"] == "model"


class TestNothingRunsWithoutAModel:
    @pytest.mark.anyio
    async def test_no_model_means_no_request(self, client, monkeypatch) -> None:
        """A46. And the cheapest possible refusal: no plan, no DB read, no
        call."""
        import openrouter

        sent = []

        async def spy(*a, **kw):
            sent.append(1)
            return {}

        monkeypatch.setattr(openrouter, "complete", spy)
        w = notebook_worker.Worker()
        w.queue = asyncio.Queue(maxsize=4)
        await w._handle(seed(client))
        assert sent == []


@pytest.fixture
def anyio_backend():
    return "asyncio"


class TestWhatTheWorkerRecords:
    """A47: a skipped extraction is not silent. Without a row, "the notebook
    has proposed nothing this week" and "the notebook refused sixty times for
    a reason nobody can see" are the same screen."""

    @pytest.mark.anyio
    async def test_a_daily_cap_refusal_is_recorded_with_its_reason(
            self, client, monkeypatch) -> None:
        import database
        import openrouter

        chat_id = seed(client, 30)
        database.set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")
        monkeypatch.setattr(config, "NOTEBOOK_DAILY_CALL_CAP", 0)

        sent = []

        async def spy(*a, **kw):
            sent.append(1)
            return {}

        monkeypatch.setattr(openrouter, "complete", spy)

        w = notebook_worker.Worker()
        w.queue = asyncio.Queue(maxsize=4)
        await w._handle(chat_id)

        assert sent == [], "a capped run must not reach the provider"
        with get_db() as con:
            stats = notebook.extraction_stats(con, chat_id)
        assert stats["skipped"] == 1
        assert stats["skip_reasons"] == {"notebook_daily_cap_reached": 1}

    @pytest.mark.anyio
    async def test_a_provider_failure_is_recorded_and_counted(
            self, client, monkeypatch) -> None:
        import database
        import openrouter

        chat_id = seed(client, 30)
        database.set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")

        async def boom(*a, **kw):
            raise openrouter.OpenRouterError("openrouter_error")

        monkeypatch.setattr(openrouter, "complete", boom)

        w = notebook_worker.Worker()
        w.queue = asyncio.Queue(maxsize=4)
        await w._handle(chat_id)

        assert w.breaker.failures == 1
        with get_db() as con:
            assert notebook.extraction_stats(con, chat_id)["failed"] == 1

    @pytest.mark.anyio
    async def test_a_TRUNCATED_reply_is_a_failure_and_the_range_stays_unread(
            self, client, monkeypatch) -> None:
        """The most expensive wound this design inherits. Recorded as
        done-with-nothing, the range is skipped forever and the facts in it
        are gone with no error anywhere."""
        import database
        import openrouter

        chat_id = seed(client, 30)
        database.set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")

        async def cut_off(*a, **kw):
            return {"id": "gen-1",
                    "choices": [{"finish_reason": "length",
                                 "message": {"content": '{"facts": [{"te'}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2048}}

        monkeypatch.setattr(openrouter, "complete", cut_off)

        w = notebook_worker.Worker()
        w.queue = asyncio.Queue(maxsize=4)
        await w._handle(chat_id)

        with get_db() as con:
            stats = notebook.extraction_stats(con, chat_id)
            done_max = con.execute(
                "SELECT COALESCE(MAX(to_message_id), 0) FROM "
                "notebook_extractions WHERE chat_id = ? AND status = 'done'",
                (chat_id,)).fetchone()[0]
        assert stats["failed"] == 1 and stats["done"] == 0
        assert done_max == 0, "a failed range must not read as processed"

    @pytest.mark.anyio
    async def test_a_good_reply_writes_notes_and_advances_the_range(
            self, client, monkeypatch) -> None:
        """The positive control for every refusal above. Without it they are
        all satisfied by a worker that never does anything."""
        import database
        import openrouter

        chat_id = seed(client, 30)
        database.set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")

        async def good(*a, **kw):
            import json
            return {"id": "gen-2",
                    "choices": [{"finish_reason": "stop",
                                 "message": {"content":
                                             json.dumps({"facts": [fact()]})}}],
                    "usage": {"prompt_tokens": 900, "completion_tokens": 40,
                              "cost": 0.00007}}

        monkeypatch.setattr(openrouter, "complete", good)

        w = notebook_worker.Worker()
        w.queue = asyncio.Queue(maxsize=4)
        await w._handle(chat_id)

        entries = notebook.list_entries(chat_id)
        assert len(entries) == 1
        assert entries[0]["provenance"] == "model"
        with get_db() as con:
            assert notebook.extraction_stats(con, chat_id)["done"] == 1
            assert notebook.spend_today(con)["calls"] == 1

    @pytest.mark.anyio
    async def test_the_same_range_is_not_paid_for_twice(
            self, client, monkeypatch) -> None:
        """A28 end to end: a second offer over an unchanged range finds the
        threshold unmet, and even if it did not, the work key is taken."""
        import database
        import openrouter

        chat_id = seed(client, 30)
        database.set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")
        calls = []

        async def good(*a, **kw):
            import json
            calls.append(1)
            return {"id": "gen-3",
                    "choices": [{"finish_reason": "stop",
                                 "message": {"content":
                                             json.dumps({"facts": []})}}],
                    "usage": {}}

        monkeypatch.setattr(openrouter, "complete", good)

        w = notebook_worker.Worker()
        w.queue = asyncio.Queue(maxsize=4)
        await w._handle(chat_id)
        await w._handle(chat_id)
        assert len(calls) == 1

    @pytest.mark.anyio
    async def test_below_the_threshold_nothing_happens_at_all(
            self, client, monkeypatch) -> None:
        import database
        import openrouter

        chat_id = seed(client, 2)
        database.set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")
        sent = []

        async def spy(*a, **kw):
            sent.append(1)
            return {}

        monkeypatch.setattr(openrouter, "complete", spy)
        w = notebook_worker.Worker()
        w.queue = asyncio.Queue(maxsize=4)
        await w._handle(chat_id)
        assert sent == []


class TestALockedVaultIsNotAFailure:
    @pytest.mark.anyio
    async def test_cancellation_propagates_and_does_not_open_the_breaker(
            self, client, monkeypatch) -> None:
        """Auto-lock fires mid-extraction because background work
        deliberately does not feed the idle timer. Counting that as a failure
        would walk the breaker towards "stopped" every time the user stepped
        away from their desk."""
        import database
        import openrouter

        chat_id = seed(client, 30)
        database.set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")

        async def cancelled(*a, **kw):
            raise asyncio.CancelledError()

        monkeypatch.setattr(openrouter, "complete", cancelled)

        w = notebook_worker.Worker()
        w.queue = asyncio.Queue(maxsize=4)
        with pytest.raises(asyncio.CancelledError):
            await w._handle(chat_id)
        assert w.breaker.failures == 0
        assert w.breaker.state == "closed"


class TestTheRoutes:
    def test_the_counters_are_readable(self, client) -> None:
        resp = client.get("/api/v1/notebook/worker")
        assert resp.status_code == 200
        body = resp.json()
        assert body["worker"]["state"] == "closed"
        assert body["daily_cap"] == config.NOTEBOOK_DAILY_CALL_CAP
        assert "spend" in body and "stats" in body

    def test_the_breaker_can_be_reset_over_http(self, client) -> None:
        for i in range(notebook_worker.STOP_AFTER):
            notebook_worker.worker.breaker.failed(float(i))
        assert notebook_worker.worker.breaker.state == "stopped"
        resp = client.post("/api/v1/notebook/worker/reset")
        assert resp.status_code == 200
        assert resp.json()["worker"]["state"] == "closed"

    def test_auto_accept_round_trips(self, client) -> None:
        assert client.get("/api/v1/notebook/auto-accept").json()["enabled"] is True
        client.post("/api/v1/notebook/auto-accept", json={"enabled": False})
        assert client.get("/api/v1/notebook/auto-accept").json()["enabled"] is False
        client.post("/api/v1/notebook/auto-accept", json={"enabled": True})
        assert client.get("/api/v1/notebook/auto-accept").json()["enabled"] is True


class TestTheWorkKeyIsWhatTheWorkerActuallyUses:
    def test_the_planner_builds_it_from_the_range_it_will_send(
            self, client) -> None:
        """A key that names a different range than the one extracted makes the
        whole idempotency argument decorative."""
        chat_id = seed(client, 30)
        plan = notebook_worker._plan_work(chat_id, "vendor/cheap", "en", 4, 20)
        assert plan is not None
        assert plan["work_key"] == notebook_extract.work_key(
            chat_id, plan["from_id"], plan["to_id"], "vendor/cheap", "en")

    def test_the_window_is_context_and_the_delta_is_the_material(
            self, client) -> None:
        """A25. The two-message window is shown for reference resolution and
        is explicitly not extracted from - Graphiti's default and its rule."""
        chat_id = seed(client, 30)
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.commit_extraction(
                con, work_key="cut", chat_id=chat_id, from_id=1, to_id=4,
                proposals=[])
            first_ids = [r[0] for r in con.execute(
                "SELECT id FROM messages WHERE chat_id = ? ORDER BY id LIMIT 4",
                (chat_id,)).fetchall()]
            con.execute(
                "UPDATE notebook_extractions SET to_message_id = ?, "
                "status = 'done' WHERE work_key = 'cut'", (first_ids[-1],))

        plan = notebook_worker._plan_work(chat_id, "vendor/cheap", "en", 4, 20)
        assert plan is not None
        assert plan["from_id"] > first_ids[-1], "the delta must start after"
        assert len(plan["recent"]) == 2, "two messages of window, no more"
        # The chunk the grounding check runs against is the DELTA only. A
        # window message inside it would let a quote from already-processed
        # text ground a fact the model was told not to extract.
        assert plan["chunk"].count("\n") == len(plan["new"]) - 1


class TestTheSkipVocabularyHasSentences:
    """A51 says an error code lives in three places. Skip reasons were a
    FOURTH, ungated vocabulary: the worker writes them, the status route
    returns them verbatim, and the panel translates them from a private map.
    A reason added without a sentence reaches a reader as snake_case.
    """

    @pytest.mark.anyio
    async def test_an_undeclared_reason_cannot_be_written(self) -> None:
        """Behaviour, not a grep: the writer itself refuses a reason that has
        no sentence behind it.

        `ValueError`, not `AssertionError`. The refusal used to be an
        `assert`, and `python -O` deletes those - a gate with a command-line
        switch on it. Nothing ships with assertions stripped today, so this
        was a latent hole; it is closed by construction now instead of by
        the absence of a flag.
        """
        with pytest.raises(ValueError):
            await notebook_worker._record_skip(
                1, "invented_reason", {"work_key": "k", "from_id": 1,
                                       "to_id": 2})

    @pytest.mark.anyio
    async def test_a_declared_reason_goes_through(self, client) -> None:
        """The ground for the refusal above."""
        chat_id = seed(client, 2)
        await notebook_worker._record_skip(
            chat_id, "proxy_gate",
            {"work_key": "kk", "from_id": 1, "to_id": 2})
        with get_db() as con:
            assert notebook.extraction_stats(con, chat_id)["skipped"] == 1

    def test_the_vocabulary_is_exactly_what_the_screen_was_promised(
            self) -> None:
        """Half of the gate that makes the declaration mean something, and
        the only half this side of the wall can answer honestly.

        What it replaces, and why: this used to open WorkerPanel.tsx and
        assert `f"{r}:" in text` for every reason. Three things were wrong
        with that. It is a substring search standing in for a rendering, so
        it passes on a key whose sentence is the empty string and on a key
        sitting in dead code. The panel grew three MORE prose tables next to
        SKIP_PROSE, so a reason whose name collides with a key in any of them
        satisfies the search while having no skip sentence at all. And the
        suite's own behavioural-test gate could not see the read, because its
        scanner only recognised a quoted path ending in `.py` - which is the
        defect that brought this test here.

        The split follows shared/error_catalogue.json exactly: the token
        vocabulary is a contract file both suites read, this side asserts the
        backend declaration equals it in BOTH directions, and the claim about
        what a reader actually SEES is made where the sentences are, by
        mounting the panel once per reason
        (frontend/src/test/components/WorkerPanel.test.tsx).

        This is a completeness census over a vocabulary, not a behaviour
        substitute: it asserts nothing about what any code path does. A
        behavioural form would only ever see the reasons some test happened
        to fire.
        """
        import json
        from pathlib import Path

        contract = (Path(__file__).resolve().parents[2] / "shared"
                    / "notebook_skip_reasons.json")
        declared = set(json.loads(
            contract.read_text(encoding="utf-8"))["reasons"])

        missing = sorted(set(notebook_worker.SKIP_REASONS) - declared)
        orphan = sorted(declared - set(notebook_worker.SKIP_REASONS))
        assert not missing, (
            f"a writer can store these and the contract has no record: "
            f"{missing}. Add each to shared/notebook_skip_reasons.json, then "
            f"give it a sentence in WorkerPanel.tsx's SKIP_PROSE - until "
            f"both exist the panel prints the raw token at the reader.")
        assert not orphan, (
            f"the contract declares these and no writer can produce them: "
            f"{orphan}. Usually a reason somebody renamed. Delete the record "
            f"and its sentence.")

    def test_the_declaration_is_not_empty(self) -> None:
        """Ground: an empty set satisfies every assertion above.

        Both `pytest.raises` tests pass against a vocabulary of nothing, and
        so does the contract check - two empty sets are equal in both
        directions. This is the floor that stops the whole class from being
        green while proving nothing, and because the contract is asserted
        EQUAL rather than a subset, this one line puts a floor under the
        shared file and the frontend walk that reads it as well.
        """
        assert len(notebook_worker.SKIP_REASONS) >= 2

    def test_commit_extraction_refuses_an_undeclared_reason_too(
            self, client) -> None:
        """`plan_invalidated` is written by commit_extraction's require_trace
        branch, not by `_record_skip` above - a DIFFERENT writer. Behaviour,
        not a grep: that writer refuses an undeclared reason on its own,
        exactly like `_record_skip` does, rather than trusting the caller.

        `ValueError` for the same reason as its sibling above: the refusal
        must not be removable by a command-line flag."""
        chat_id = seed(client, 2)
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            with pytest.raises(ValueError):
                notebook.commit_extraction(
                    con, work_key="undeclared-writer", chat_id=chat_id,
                    from_id=1, to_id=2, status="skipped",
                    skip_reason="invented_reason")

    def test_a_declared_reason_reaches_commit_extraction_too(
            self, client) -> None:
        """The ground for the refusal above, on the same writer."""
        chat_id = seed(client, 2)
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.commit_extraction(
                con, work_key="declared-writer", chat_id=chat_id,
                from_id=1, to_id=2, status="skipped",
                skip_reason="proxy_gate")
        with get_db() as con:
            stats = notebook.extraction_stats(con, chat_id)
        assert stats["skip_reasons"] == {"proxy_gate": 1}

    def test_the_two_writers_share_one_declaration(self) -> None:
        """The gate itself: notebook_worker declares no frozenset of its own
        any more, it reuses notebook_store's. Two copies is how
        `plan_invalidated` leaked - one writer checked itself against a
        vocabulary that had never heard of the other writer's reason."""
        assert notebook_worker.SKIP_REASONS is notebook.SKIP_REASONS
        assert "plan_invalidated" in notebook.SKIP_REASONS


class TestAFailedReadIsTriedOnTheNextReply:
    """Decision 33 a. A reply that comes back unusable does not move the
    cursor, because the cursor counts `done` (and `abandoned_in_flight`)
    and nothing else - so the very next reply in that chat plans the same
    stretch again, on a batch the failure has halved. The locked document
    said "on the next threshold", twenty messages later, and was wrong; the
    code was right and is kept."""

    @pytest.fixture
    def anyio_backend(self):
        return "asyncio"

    @staticmethod
    def _provider(monkeypatch, outcomes: list[bool]) -> list[int]:
        """True answers with a note, False with a body that is not JSON."""
        import json

        import openrouter

        calls: list[int] = []

        async def reply(*a, **kw):
            ok = outcomes[min(len(calls), len(outcomes) - 1)]
            calls.append(1)
            content = (json.dumps({"facts": [fact()]}) if ok
                       else "this is not the schema")
            return {"id": f"gen-{len(calls)}", "choices": [{
                "finish_reason": "stop", "message": {"content": content}}],
                "usage": {"tokens_in": 10, "tokens_out": 5, "cost": 0.0001}}

        monkeypatch.setattr(openrouter, "complete", reply)
        return calls

    @staticmethod
    def _ranges(chat_id: int) -> list[tuple]:
        with get_db() as con:
            return [tuple(r) for r in con.execute(
                "SELECT status, from_message_id FROM notebook_extractions "
                "WHERE chat_id = ? ORDER BY rowid", (chat_id,)).fetchall()]

    @staticmethod
    def _one_more(chat_id: int) -> None:
        with get_db() as con:
            con.execute("INSERT INTO messages (chat_id, role, content, active) "
                        "VALUES (?, 'assistant', 'one more reply', 1)",
                        (chat_id,))

    @pytest.mark.anyio
    async def test_a_failed_read_is_tried_again_on_the_very_next_reply(
            self, client, monkeypatch) -> None:
        import database

        database.set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")
        calls = self._provider(monkeypatch, [False, True])
        chat_id = seed(client, config.NOTEBOOK_EXTRACT_EVERY_TURNS - 1)
        w = notebook_worker.Worker()

        assert await w._handle(chat_id) == "settled"
        first = self._ranges(chat_id)
        assert [r[0] for r in first] == ["failed"], "ground: the reply failed"

        self._one_more(chat_id)
        await w._handle(chat_id)

        assert len(calls) == 2, (
            "one new message after a failed read did not bring the retry")
        ranges = self._ranges(chat_id)
        assert ranges[-1][0] == "done"
        assert ranges[-1][1] == first[0][1], (
            "the retry did not start where the failed read did")

    @pytest.mark.anyio
    async def test_the_retry_reads_a_piece_the_failure_has_halved(
            self, client, monkeypatch) -> None:
        """The other half of the sentence: the retry is not the same read
        again, it is a smaller one. The failed read covered a whole batch;
        the retry at the next reply covers the batch one failure halves."""
        import database

        database.set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")
        self._provider(monkeypatch, [False, True])
        batch = config.NOTEBOOK_EXTRACT_EVERY_TURNS
        chat_id = seed(client, batch - 1)
        w = notebook_worker.Worker()

        def sizes() -> list[int]:
            with get_db() as con:
                return [con.execute(
                    "SELECT COUNT(*) FROM messages WHERE chat_id = ? "
                    "AND id BETWEEN ? AND ?", (chat_id, lo, hi)).fetchone()[0]
                    for lo, hi in con.execute(
                        "SELECT from_message_id, to_message_id FROM "
                        "notebook_extractions WHERE chat_id = ? "
                        "ORDER BY rowid", (chat_id,)).fetchall()]

        await w._handle(chat_id)
        assert sizes() == [batch], "ground: the failed read took a batch"
        self._one_more(chat_id)
        await w._handle(chat_id)

        halved = notebook_worker._pressured(batch, 1)
        assert halved < batch, "ground: one failure does halve the batch"
        assert sizes() == [batch, halved], (
            f"the retry read {sizes()[-1:]} messages, not the {halved} "
            "one failure leaves")

    @pytest.mark.anyio
    async def test_GROUND_after_a_success_one_new_message_is_not_a_read(
            self, client, monkeypatch) -> None:
        import database

        database.set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")
        calls = self._provider(monkeypatch, [True])
        chat_id = seed(client, config.NOTEBOOK_EXTRACT_EVERY_TURNS - 1)
        w = notebook_worker.Worker()

        await w._handle(chat_id)
        assert len(calls) == 1 and self._ranges(chat_id)[0][0] == "done"
        self._one_more(chat_id)
        await w._handle(chat_id)
        assert len(calls) == 1, "one message after a success was read"

    @pytest.mark.anyio
    async def test_five_failures_in_a_row_pause_the_retries(
            self, client, monkeypatch) -> None:
        """POSITIVE CONTROL for "every reply": the chat's breaker is what
        bounds it. Five failures, and the sixth reply sends nothing."""
        import database

        database.set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")
        calls = self._provider(monkeypatch, [False])
        chat_id = seed(client, config.NOTEBOOK_EXTRACT_EVERY_TURNS - 1)
        w = notebook_worker.Worker()

        for _ in range(notebook_worker.TRIP_AFTER):
            await w._handle(chat_id)
            self._one_more(chat_id)
        assert len(calls) == notebook_worker.TRIP_AFTER, (
            "ground: every reply retried until the breaker tripped")
        await w._handle(chat_id)
        assert len(calls) == notebook_worker.TRIP_AFTER
