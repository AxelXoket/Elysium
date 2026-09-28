"""H-17: one breaker per process was answering the wrong question.

A chat whose own material breaks every call - a context the model will not
fit, a card its moderation refuses, a range whose reply never parses - walked
the single breaker to `stopped` and took every OTHER chat down with it. The
fault was in one conversation and the punishment was the feature.

The split is HYBRID, and that is not a hedge:

  * a failure this chat's own content caused stops only this chat;
  * a failure no chat caused and every chat would repeat - 429, 5xx, 401,
    402, a timeout, a refused proxy - stops everything, because "only the
    problematic chat" names nothing when the problem is not in a chat. Left
    per-chat, one rate limit would burn TRIP_AFTER billed calls per open
    conversation instead of TRIP_AFTER in total;
  * a failure nobody has classified moves both, because that is the only
    default that protects the money AND stops one chat being re-billed.

Every test here drives real code: the real `Breaker`, the real `_handle`, the
real daily claim, the real routes. Nothing reads source text and every
constant is imported.
"""
from __future__ import annotations

import asyncio
import json

import pytest

import config
import notebook_store as notebook
import notebook_worker
from database import get_db

from tests.test_notebook_worker import fact, seed


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def worker() -> notebook_worker.Worker:
    w = notebook_worker.Worker()
    w.queue = asyncio.Queue(maxsize=notebook_worker.QUEUE_MAXSIZE)
    return w


MODEL = "vendor/cheap"


def _model(monkeypatch=None) -> None:
    import database
    database.set_setting(config.SETTING_NOTEBOOK_MODEL, MODEL)


def counting(monkeypatch, *, raises=None, finish="stop", facts=None):
    """A provider that counts itself and fails the way it was told to.

    The COUNT is what the money tests read - `spend_today` answers for the
    claim, this answers for the request - and the two are deliberately
    checked apart.
    """
    import openrouter

    state = {"calls": 0}

    async def _reply(*a, **kw):
        state["calls"] += 1
        if raises is not None:
            raise raises()
        return {"id": "gen", "choices": [{
            "finish_reason": finish,
            "message": {"content": json.dumps({"facts": facts or []})}}],
            "usage": {"cost": 0.0001}}

    monkeypatch.setattr(openrouter, "complete", _reply)
    return state


def _rate_limited():
    import openrouter
    return openrouter.OpenRouterError("openrouter_rate_limited")


def _paid() -> int:
    with get_db() as con:
        return notebook.spend_today(con)["calls"]


# ── M1: a broken chat stops itself and nothing else ────────────────────────


class TestOneBadChatDoesNotStopTheOthers:
    @pytest.mark.anyio
    async def test_a_chat_that_breaks_every_call_does_not_stop_its_neighbour(
            self, client, monkeypatch, worker) -> None:
        """The whole point of the split. `finish_reason="length"` is a real
        `ExtractionFailed("truncated")`: this chat's material is too big for
        the reply budget, which is a fact about THIS conversation."""
        _model()
        a = seed(client, 30)
        b = seed(client, 30)
        # A truncated reply is billed and unusable - the shape that walked
        # the single breaker to "open" on somebody else's behalf.
        provider = counting(monkeypatch, finish="length")

        for _ in range(notebook_worker.TRIP_AFTER):
            await worker._handle(a)

        assert provider["calls"] == notebook_worker.TRIP_AFTER, (
            "ground: every one of those attempts really reached the provider")
        assert worker.breaker.failures == 0, (
            "a fault inside one chat moved the GLOBAL breaker")

        before = provider["calls"]
        await worker._handle(b)
        assert provider["calls"] == before + 1, (
            "the neighbouring chat was refused by a breaker that has nothing "
            "to do with it")

        await worker._handle(a)
        assert provider["calls"] == before + 1, (
            "POSITIVE CONTROL: the broken chat was NOT refused, so the "
            "breaker above is not stopping anything at all")
        assert worker.refused_by_chat_breaker == 1

    @pytest.mark.anyio
    async def test_nothing_is_refused_when_nothing_fails(
            self, client, monkeypatch, worker) -> None:
        """GROUND. Two healthy chats both run, so the test above is not
        satisfied by a worker that refuses on a schedule."""
        _model()
        a = seed(client, 30)
        b = seed(client, 30)
        provider = counting(monkeypatch, facts=[fact()])

        await worker._handle(a)
        await worker._handle(b)

        assert provider["calls"] == 2
        assert worker.chat_breakers == {}, (
            "a healthy install holds no per-chat records at all")
        assert worker.refused_by_chat_breaker == 0


# ── M3: the ceiling that makes the split safe ──────────────────────────────


class TestAProviderFaultStillStopsEverything:
    @pytest.mark.anyio
    async def test_a_rate_limit_is_bounded_across_every_open_chat(
            self, client, monkeypatch, worker) -> None:
        """The money. A 429 is an account-level answer: per-chat alone, ten
        open conversations would each burn TRIP_AFTER billed calls before
        anything stopped them - fifty, against a daily cap of sixty."""
        _model()
        chats = [seed(client, 30) for _ in range(10)]
        provider = counting(monkeypatch, raises=_rate_limited)

        for chat_id in chats:
            await worker._handle(chat_id)

        assert provider["calls"] <= notebook_worker.TRIP_AFTER, (
            f"a provider-wide rate limit was billed {provider['calls']} times "
            f"across {len(chats)} chats")
        assert _paid() <= notebook_worker.TRIP_AFTER
        assert worker.breaker.state != "closed", (
            "the global breaker never moved for a global fault")

    @pytest.mark.anyio
    async def test_a_healthy_provider_serves_every_one_of_them(
            self, client, monkeypatch, worker) -> None:
        """GROUND for the ceiling above: without a fault the same loop pays
        for all ten, so the bound is measuring the breaker rather than a
        harness that never got past the first chat."""
        _model()
        chats = [seed(client, 30) for _ in range(10)]
        provider = counting(monkeypatch, facts=[fact()])

        for chat_id in chats:
            await worker._handle(chat_id)

        assert provider["calls"] == len(chats)


# ── M4: what an unclassified failure does ──────────────────────────────────


class TestAnUnknownFailureMovesBoth:
    @pytest.mark.anyio
    async def test_a_failure_nobody_classified_moves_both_counters(
            self, client, monkeypatch, worker) -> None:
        """`openrouter` raises ONE class for TLS errors, protocol errors and
        undecodable bodies alike, so a code this file has never seen is not a
        rarity - it is the default arrival. The conservative answer protects
        the money and stops the loop re-billing one chat."""
        _model()
        chat_id = seed(client, 30)

        class Weird(Exception):
            pass

        counting(monkeypatch, raises=Weird)
        await worker._handle(chat_id)

        assert worker.breaker.failures == 1, "the global counter did not move"
        assert chat_id in worker.chat_breakers, "no per-chat record was made"
        assert worker.chat_breakers[chat_id].failures == 1, (
            "the per-chat counter did not move")

    @pytest.mark.anyio
    async def test_a_known_provider_code_moves_only_the_global_one(
            self, client, monkeypatch, worker) -> None:
        """POSITIVE CONTROL. Without it the test above passes on a router
        that sends everything to both."""
        _model()
        chat_id = seed(client, 30)
        counting(monkeypatch, raises=_rate_limited)
        await worker._handle(chat_id)

        assert worker.breaker.failures == 1
        assert chat_id not in worker.chat_breakers

    @pytest.mark.anyio
    async def test_a_known_chat_fault_moves_only_the_chat_one(
            self, client, monkeypatch, worker) -> None:
        """The other positive control, the other way round."""
        _model()
        chat_id = seed(client, 30)
        counting(monkeypatch, finish="length")
        await worker._handle(chat_id)

        assert worker.breaker.failures == 0
        assert worker.chat_breakers[chat_id].failures == 1


# ── M5: the net under the classification ───────────────────────────────────


class TestTheFanOutNet:
    """A fault that appears in CHAT_TRIP_FANOUT+1 unrelated conversations at
    once is not a property of any of them; it means this file put it in the
    wrong bucket. The net catches the classifier's own mistake without
    waiting for somebody to notice."""

    def test_enough_tripped_chats_trip_the_global_one(self, worker) -> None:
        for chat_id in range(notebook_worker.CHAT_TRIP_FANOUT + 1):
            for _ in range(notebook_worker.TRIP_AFTER):
                worker._fail_chat(chat_id, 0.0)

        assert worker.breaker.failures > 0, (
            f"{notebook_worker.CHAT_TRIP_FANOUT + 1} chats were tripped at "
            "once and the global breaker never noticed")

    def test_below_the_ceiling_the_global_one_does_not_move(
            self, worker) -> None:
        """GROUND. The net is a ceiling, not a second route: tripping chats
        up to the limit must leave the global breaker exactly where it was."""
        for chat_id in range(notebook_worker.CHAT_TRIP_FANOUT):
            for _ in range(notebook_worker.TRIP_AFTER):
                worker._fail_chat(chat_id, 0.0)

        assert len(worker.chat_breakers) == notebook_worker.CHAT_TRIP_FANOUT, (
            "ground: every one of them really is tripped")
        assert worker.breaker.failures == 0


# ── M2: the four cleanup rules, each one measured ──────────────────────────


class TestTheDictIsBounded:
    """An unbounded dict keyed on a user-controlled id is a leak. Four rules,
    four tests, and each is written down rather than assumed."""

    @pytest.mark.anyio
    async def test_recovery_drops_the_record_entirely(
            self, client, monkeypatch, worker) -> None:
        """T1, and in practice it is the rule that clears almost everything:
        a chat that starts working again holds nothing."""
        _model()
        chat_id = seed(client, 30)
        counting(monkeypatch, finish="length")
        await worker._handle(chat_id)
        assert chat_id in worker.chat_breakers, "ground: a record was made"

        counting(monkeypatch, facts=[fact()])
        await worker._handle(chat_id)

        assert chat_id not in worker.chat_breakers, (
            "a recovered chat kept its record forever")

    def test_a_record_older_than_the_ttl_is_dropped(self, worker) -> None:
        """T3. A day with no new failure makes a refusal stale data rather
        than information - the longest cooldown is an hour, so the TTL is
        twenty-four of them."""
        worker._fail_chat(11, 0.0)
        assert worker._chat_breaker(
            11, notebook_worker.CHAT_BREAKER_TTL_S - 1.0) is not None, (
            "ground: inside the TTL the record is kept, so the assertion "
            "below is not reading an empty dict")

        assert worker._chat_breaker(
            11, notebook_worker.CHAT_BREAKER_TTL_S + 1.0) is None
        assert 11 not in worker.chat_breakers

    def test_the_dict_stops_growing_at_its_ceiling(self, worker) -> None:
        """T4. The oldest ACCESSED record is dropped, and the price is named:
        that chat earns one more call. It costs nothing until 256 chats are
        failing at once, and the global breaker and the daily cap are still
        underneath."""
        for chat_id in range(notebook_worker.CHAT_BREAKERS_MAX):
            worker._fail_chat(chat_id, 0.0)
        assert len(worker.chat_breakers) == notebook_worker.CHAT_BREAKERS_MAX
        assert 0 in worker.chat_breakers, "ground: nothing was dropped yet"

        worker._fail_chat(notebook_worker.CHAT_BREAKERS_MAX, 0.0)

        assert len(worker.chat_breakers) == notebook_worker.CHAT_BREAKERS_MAX
        assert 0 not in worker.chat_breakers, "the ceiling did not evict"
        assert notebook_worker.CHAT_BREAKERS_MAX in worker.chat_breakers

    def test_deleting_the_chat_forgets_its_breaker(self, client) -> None:
        """T2. The messages that produced the failure are gone; a refusal
        that outlives them is refusing a conversation that no longer exists,
        and the id can be handed to a new chat."""
        w = notebook_worker.worker
        chat_id = seed(client, 4)
        w._fail_chat(chat_id, 0.0)
        assert chat_id in w.chat_breakers, "ground"

        assert client.delete(f"/api/v1/chats/{chat_id}").status_code == 200
        assert chat_id not in w.chat_breakers

    def test_clearing_the_chat_forgets_its_breaker(self, client) -> None:
        """T2, the other half. Clearing removes every message, so the range
        that could not be read is gone too."""
        w = notebook_worker.worker
        chat_id = seed(client, 4)
        w._fail_chat(chat_id, 0.0)
        assert chat_id in w.chat_breakers, "ground"

        assert client.post(
            f"/api/v1/chats/{chat_id}/clear").status_code == 200
        assert chat_id not in w.chat_breakers


# ── what the reader can see, and what the button does ──────────────────────


class TestTheStatusRouteSaysWhichChatIsStopped:
    def test_the_route_answers_for_one_chat_when_asked(self, client) -> None:
        w = notebook_worker.worker
        chat_id = seed(client, 4)
        for _ in range(notebook_worker.TRIP_AFTER):
            w._fail_chat(chat_id, 0.0)

        body = client.get(
            f"/api/v1/notebook/worker?chat_id={chat_id}").json()["worker"]

        assert body["state"] == "closed", (
            "the GLOBAL sentence must not change for a fault in one chat")
        assert body["chat"] is not None
        assert body["chat"]["state"] in {"open", "half_open"}
        assert body["chat"]["failures"] == notebook_worker.TRIP_AFTER

    def test_without_a_chat_id_there_is_no_chat_block(self, client) -> None:
        """GROUND. The panel asks for a chat only when one is selected, and
        the key has to be there and null rather than absent."""
        body = client.get("/api/v1/notebook/worker").json()["worker"]
        assert body["chat"] is None

    def test_the_reset_button_clears_the_per_chat_refusals_too(
            self, client) -> None:
        """The button on screen is a global one, and "try everything again"
        is what a reader pressing it means."""
        w = notebook_worker.worker
        chat_id = seed(client, 4)
        w._fail_chat(chat_id, 0.0)

        assert client.post(
            "/api/v1/notebook/worker/reset").status_code == 200
        assert w.chat_breakers == {}

    def test_one_chat_can_be_unstuck_on_its_own(self, client) -> None:
        w = notebook_worker.worker
        a = seed(client, 4)
        b = seed(client, 4)
        w._fail_chat(a, 0.0)
        w._fail_chat(b, 0.0)

        assert client.post("/api/v1/notebook/worker/reset",
                           json={"chat_id": a}).status_code == 200
        assert a not in w.chat_breakers
        assert b in w.chat_breakers, "it reset more than it was asked to"


class TestTheStatusRouteCountsThisChatsUnread:
    """The Notes tab says "Up to date." only on a live count of
    THIS chat's unread messages. `worker.backlog` is counted once at unlock
    over every chat, so it cannot say that. Expected numbers are read from
    the messages table itself (a new chat can carry a greeting), never
    retyped."""

    @staticmethod
    def _ids(chat_id: int) -> list[int]:
        with get_db() as con:
            return [r[0] for r in con.execute(
                "SELECT id FROM messages WHERE chat_id = ? AND active = 1 "
                "ORDER BY id", (chat_id,)).fetchall()]

    @staticmethod
    def _unread(client, chat_id: int):
        return client.get(
            f"/api/v1/notebook/worker?chat_id={chat_id}").json()["chat_unread"]

    def test_counts_this_chats_unread_messages(self, client) -> None:
        chat_id = seed(client, 4)
        other = seed(client, 7)
        assert self._unread(client, chat_id) == len(self._ids(chat_id))
        # POSITIVE CONTROL: the other chat's own count, not a sum, and the
        # two differ, so a sum or a swap would be caught.
        assert self._unread(client, other) == len(self._ids(other))
        assert len(self._ids(other)) != len(self._ids(chat_id)), "ground"

    def test_a_read_range_is_not_counted(self, client) -> None:
        chat_id = seed(client, 4)
        ids = self._ids(chat_id)
        assert self._unread(client, chat_id) == len(ids), "ground"
        with get_db() as con:
            con.execute(
                "INSERT INTO notebook_extractions (work_key, chat_id, "
                "from_message_id, to_message_id, status) "
                "VALUES (?,?,?,?,'done')",
                (f"range-{chat_id}", chat_id, ids[0], ids[2]))
        assert self._unread(client, chat_id) == len(ids) - 3

    def test_the_count_follows_a_new_message(self, client) -> None:
        """The failure the field exists for: the backlog stays as it was at
        unlock, this count does not."""
        chat_id = seed(client, 2)
        before = self._unread(client, chat_id)
        assert before == len(self._ids(chat_id)), "ground"
        with get_db() as con:
            con.execute(
                "INSERT INTO messages (chat_id, role, content, active) "
                "VALUES (?,?,?,1)", (chat_id, "user", "one more line"))
        assert self._unread(client, chat_id) == before + 1

    def test_without_a_chat_id_the_field_is_null(self, client) -> None:
        """GROUND: present and null, never absent, like `chat_refusal`."""
        body = client.get("/api/v1/notebook/worker").json()
        assert "chat_unread" in body and body["chat_unread"] is None


class TestTheReadButtonSaysWhyItRefused:
    """The refusals the old sweep button gave, asked of the door that is left.

    `sweep()` refused with three reasons and the panel translated them with
    an inline ternary whose last arm is a catch-all: "It is already reading."
    A paused chat answered by that arm tells the reader the opposite of what
    is happening. The sweep route and method are gone (decision 69 a); the
    reading strip's `next` job is the one-press read now, and it refuses a
    paused or stopped chat by the same two names, before a job row exists."""

    @staticmethod
    def _ready(chat_id: int) -> None:
        """A chat model and a note model, so the only thing that can refuse
        a healthy chat is the breaker these tests are about."""
        from database import get_db, set_setting

        set_setting(config.SETTING_NOTEBOOK_MODEL, "vendor/cheap")
        with get_db() as con:
            con.execute("UPDATE chats SET model_id = ? WHERE id = ?",
                        ("vendor/cheap", chat_id))

    @pytest.mark.anyio
    async def test_a_paused_chat_says_so(self, client, worker) -> None:
        now = asyncio.get_running_loop().time()
        chat_id = seed(client, 30)
        self._ready(chat_id)
        for _ in range(notebook_worker.TRIP_AFTER):
            worker._fail_chat(chat_id, now)

        out = await worker.backfill(chat_id, "next")
        assert out == {"started": False, "reason": "chat_paused"}
        assert out["reason"] in notebook_worker.SWEEP_REFUSALS

    @pytest.mark.anyio
    async def test_a_stopped_chat_says_so(self, client, worker) -> None:
        now = asyncio.get_running_loop().time()
        chat_id = seed(client, 30)
        self._ready(chat_id)
        for _ in range(notebook_worker.STOP_AFTER):
            worker._fail_chat(chat_id, now)

        out = await worker.backfill(chat_id, "next")
        assert out == {"started": False, "reason": "chat_stopped"}

    @pytest.mark.anyio
    async def test_a_healthy_chat_is_not_refused(self, client, worker) -> None:
        """GROUND. Without it the two above are satisfied by a door that
        refuses everything."""
        chat_id = seed(client, 30)
        self._ready(chat_id)
        out = await worker.backfill(chat_id, "next")
        try:
            assert out["started"] is True, out
        finally:
            await worker.wait_for_backfill()

    def test_every_refusal_the_sweep_can_return_is_declared(self) -> None:
        """The vocabulary gate, the same shape as SKIP_REASONS: a reason with
        no sentence reaches a reader as a snake_case token, and the panel's
        table is checked against this set on the client side."""
        assert notebook_worker.SWEEP_REFUSALS >= {
            "already_running", "already_in_flight", "nothing_unread",
            "chat_paused", "chat_stopped"}
