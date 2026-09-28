"""The background extractor: one supervised worker, one bounded queue.

This is the part of the notebook that runs when nobody is watching, on the
user's own API key, and every decision in it is shaped by that.

WHAT IT DOES
    After a turn is persisted, `offer(chat_id)` drops a chat id in a queue and
    returns immediately - the send path never waits for it and never fails
    because of it. The worker wakes, counts how many messages have arrived
    since the last extraction, and if that reaches the threshold it reads the
    delta plus two messages of disambiguation window, asks the chosen model
    for at most six facts, filters them in code, and writes the survivors.

WHAT IT REFUSES TO DO
    *Retry.* `network_client.py:68-79` documents why the whole app refuses:
    httpx reads the body inside `send()`, so "the request never left" and "it
    was accepted, generated, BILLED and then cut off" raise the same
    exception. Re-sending the second pays for the answer twice. A failed
    extraction is skipped and, because a failure does not move the cursor,
    the very next reply in that chat tries again, on a batch the failure
    has halved and so under a NEW work key - which is a fresh question, not
    a retry of the old one (decision 33 a).

    *Spend without a ceiling.* The daily cap is claimed BEFORE the request and
    is a block, not an alert. The largest documented runaway in this space was
    not a loop; it was a context that grew every call while a budget alarm
    dutifully fired.

    *Treat a locked vault as a failure.* Auto-lock can fire mid-extraction -
    background work deliberately does not feed the idle timer. The worker
    accepts that silently: it is a cancellation, and counting it as a failure
    would walk the circuit breaker towards "stop" every time somebody left
    their desk.

    *Outlive its own crash.* The packaged exe never runs a shutdown path -
    uvicorn is a daemon thread and the process simply dies with the window.
    So cancellation must be survivable at any point, and an extraction in
    flight when the window closes is lost. That is accepted and written down
    rather than defended against.

THE CIRCUIT BREAKERS, AND WHY THERE ARE TWO
    Five consecutive failures open one for a cooldown; twenty stop it until
    somebody says otherwise. The batch halves on the way down. The half-open
    probe is a SINGLE call, and a too-fast Open->Half-Open cycle is what makes
    a breaker flap, so the cooldown grows with each trip. That much is the
    same for both of them, and `Breaker` is one class used twice.

    ONE breaker for the whole process is what this file had, and it answered
    the wrong question. A chat whose own material breaks every call - a
    context the model will not fit, a card its moderation refuses, a range
    whose reply never parses - walked the single breaker to `stopped` and
    took every OTHER chat down with it. The fault was in one conversation and
    the punishment was the feature.

    So the failure is asked where it belongs before it is counted, and
    `_fault_scope` is the only place that decides:

    PER CHAT, in `chat_breakers`. A failure this chat's own content caused: a
    truncated or unparseable reply, a moderation refusal, a write that cannot
    land for this chat's rows, a prompt that cannot be built from this chat's
    card. Only this chat stops. Every other chat keeps running, which is the
    whole point of the split.

    GLOBAL, in `breaker`. A failure no chat caused and every chat would
    repeat: 429, 5xx, 401, 402, a timeout, a refused proxy. "Only the
    problematic chat should stop" has no meaning here because the problem is
    not in any chat - and without this half, one rate limit would burn
    TRIP_AFTER billed calls per open conversation instead of TRIP_AFTER in
    total, which is the runaway this module's daily cap exists to bound. The
    ceiling is not a nicety; it is the reason the split is safe.

    BOTH, when we cannot tell. `openrouter` raises one class for TLS errors,
    protocol errors and undecodable bodies alike, and its generic
    `openrouter_error` covers a context that will not fit AND a host that
    cannot be reached, so a failure nobody has classified moves both
    counters. The conservative default is the one that protects the money AND
    stops the loop re-billing one chat.

    AND A NET UNDER THE CLASSIFICATION. If more than CHAT_TRIP_FANOUT
    unrelated chats are tripped at once, the global breaker takes a failure
    too. A fault that appears in that many conversations at the same time is
    not a property of any of them; it means this file put it in the wrong
    bucket, and the net catches that without waiting for somebody to notice.

    The per-chat breakers are created on a FAILURE, never on a look, so a
    healthy install holds none of them. They are dropped when the chat
    recovers, when the chat is deleted or cleared, after CHAT_BREAKER_TTL_S
    without a failure, and by an LRU ceiling of CHAT_BREAKERS_MAX. An
    unbounded dict keyed on a user-controlled id is a leak, and each of those
    four is written down rather than assumed.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from functools import partial
import functools
import logging
import secrets

import anyio.to_thread

import config
import notebook_extract
import voice_tags
import notebook_store as notebook
# MODULE LEVEL, and it has to be. Two `except vault_state.VaultLockedError:`
# clauses sit on abandon paths that promise never to raise, and an `except`
# clause is evaluated only once something has been caught - so a name bound
# nowhere but inside `run()` cost nothing until the moment those blocks had
# work to do, then raised `NameError` mid-unwind and left the `except
# Exception:` beneath them unreachable. Safe to hoist: `vault_state` imports
# threading and time and nothing of ours, so there is no cycle to open.
import vault_state

logger = logging.getLogger(__name__)

#: A bounded queue, and the bound is written down. `asyncio.Queue(maxsize=0)`
#: is INFINITE - the default that looks like "no queueing" and is the opposite.
#: When it fills, the OLDEST offer is dropped: the newest turn is the one whose
#: context is worth reading, and a queue that drops the newest would starve
#: exactly the chat somebody is using.
QUEUE_MAXSIZE = 32

#: Every reason a run can be refused. It is a USER-FACING vocabulary - the
#: status route hands these to the panel, which turns each into a sentence -
#: so it is checked against the panel's map, exactly like the error catalogue
#: is checked against errorMessages.ts. A reason added without a sentence
#: reaches a reader as a snake_case token.
#:
#: Declared in notebook_store.py, not here, and reused under the same name
#: rather than copied. `plan_invalidated` is written from THAT module -
#: `commit_extraction`'s `require_trace` branch, not this worker - and a
#: second frozenset here would leave that writer checking itself against a
#: vocabulary that never heard of its own reason, which is exactly how it
#: leaked before this alias replaced the copy.
SKIP_REASONS: frozenset[str] = notebook.SKIP_REASONS

#: Consecutive failures. Five is a cooldown, twenty is a stop.
TRIP_AFTER = 5
STOP_AFTER = 20

#: Seconds. Doubles per trip, because a breaker that returns to Half-Open too
#: quickly does not protect anything - it flaps, and each flap is another
#: billed call into a provider that is still broken.
COOLDOWN_BASE_S = 60.0
COOLDOWN_MAX_S = 3600.0

#: What consecutive failures do to how much one call may READ (`batch_size`).
#:
#: It used to shrink the trigger threshold too, and decision 28 a took that
#: back. With the threshold halving, one chat's provider errors dropped EVERY
#: chat's threshold to 10, 5 and then 2 messages, so the other conversations
#: were read more often exactly while the provider was failing. The
#: rule is twenty messages, and a fault somewhere else is no reason to break
#: it here. So the threshold stays at `config.NOTEBOOK_EXTRACT_EVERY_TURNS`
#: whatever the breakers say, and only the batch shrinks: a failing period
#: reads less per call and does not read more often.
PRESSURE_FLOOR = 2
PRESSURE_STEPS = 3

#: How many separate chats may hold a breaker at once, and for how long after
#: their last failure. An unbounded dict keyed on a chat id is a leak: the id
#: comes from the user, and nothing else in this file would ever remove the
#: entry. One `Breaker` is nine small fields, so the ceiling is about 64 KB
#: and it does not grow with the number of chats.
CHAT_BREAKERS_MAX = 256
#: How many (chat, model) pairs the reader remembers having told "this model
#: cannot write notes" (3.8). Bounded for the same reason as the breakers:
#: both halves of the key come from the user, and a chat that cycles through
#: models would otherwise grow the memory without end. The oldest pair is
#: forgotten first, and a forgotten pair is only said once more.
UNFIT_SAID_MAX = 256
#: How many of a chat's refusal rows `standing_refusal` looks at, newest
#: first, when it asks whether the card should still say the chat's model
#: cannot write notes. Only rows newer than the chat's last read count. A
#: row is written once per chat and model while that model keeps being
#: refused; inside that window a pair is written again only after a turn
#: that checked the chat's model, found it fit and read nothing, a restart,
#: or the pair aging out of `UNFIT_SAID_MAX`, so a chat reaches this bound
#: only by many model switches or restarts with no read between them. The
#: bound keeps the status poll one small query whatever the table holds.
#: Read at call time.
STANDING_REFUSAL_ROWS = 64
#: Twenty-four times the longest cooldown this module can impose. A chat that
#: has not failed in a day is not carrying information any more, it is
#: carrying stale data, and refusing on it costs a call nobody asked for.
CHAT_BREAKER_TTL_S = 86400.0
#: The net under `_fault_scope`. A fault that shows up in this many unrelated
#: conversations at once is not a property of any of them - it means this
#: file put it in the wrong bucket. Its price is named: the worst case is
#: CHAT_TRIP_FANOUT * TRIP_AFTER billed calls instead of TRIP_AFTER, and the
#: daily cap is still underneath.
CHAT_TRIP_FANOUT = 8

#: Every reason the job routes can refuse with (the start route through
#: `Worker.backfill`, the stop route through `Worker.stop_backfill`). The
#: name is older than the routes: it was the one-press sweep's list, and
#: that route went with decision 69 a. Exported for the same reason
#: SKIP_REASONS is: the panel turns each of these into a sentence, and a
#: reason added without one reaches a reader as a snake_case token - or
#: worse, falls into the panel's catch-all arm and tells a paused chat that
#: it is "already reading", which is the opposite of what is happening.
#:
#: Ten, in three groups, and every one answers HTTP 200 with
#: `{started: false, reason}` - a refusal is the ordinary answer to a
#: button, not an error, so none of these is in the error catalogue. The
#: five the sweep always had; the four a backfill job adds (`not_ready`
#: is the precondition gate - chat, chat model and note model all chosen;
#: `daily_cap_reached` is the job's own, lower ceiling already spent;
#: `model_not_extractable` is a chat model that cannot return a strict
#: schema, refused before a call is made; `model_list_unavailable` is a
#: model bound to the chat while the provider's list of models that can
#: write notes could not be fetched, which says nothing about the model);
#: and the stop route's `nothing_running`.
SWEEP_REFUSALS: frozenset[str] = frozenset({
    "already_running", "already_in_flight", "nothing_unread",
    "chat_paused", "chat_stopped",
    "not_ready", "daily_cap_reached", "model_not_extractable",
    "model_list_unavailable",
    "nothing_running",
})

#: What a backfill job may claim of the day: the ceiling minus the reserve
#: the live reader keeps. Read at call time, not import time, because both
#: numbers are monkeypatched by tests and overridden by the environment.


def _backfill_cap() -> int:
    """What a job may claim: the day's ceiling minus the live reader's
    reserve, and never less than one.

    FLOORED AT ONE, because the reserve is floored at zero and the
    difference was not. `ELYSIUM_NOTEBOOK_LIVE_RESERVE=60` against the
    default ceiling of 60 made this return 0, every job refuse
    `daily_cap_reached` before it began, and the panel say "today's calls
    for reading history are used up" on a machine that had made none. One
    typo, no error, and an answer indistinguishable from a spent day.
    A reserve that would take the whole ceiling keeps all of it but one.
    """
    return max(1, config.NOTEBOOK_DAILY_CALL_CAP - config.NOTEBOOK_LIVE_RESERVE)


class _Skipped:
    """`_prepare`'s answer when it wrote a skip row and stopped.

    `None` used to be the only way `_prepare` said "no call this turn", and
    it said it for two different things: nothing to plan (breaker, no
    model, no material, already answered) and a skip with a NAME - the
    daily ceiling, the proxy gate, a missing key. The loop never needed
    the difference; a backfill job does, because "the ceiling is spent" is
    a reason to stop the job and say so, while "nothing planned" is a
    reason to stop the job and say something else. The reason carried is
    the same token the skip row was written with.
    """

    __slots__ = ("reason",)

    def __init__(self, reason: str) -> None:
        self.reason = reason


def _pressured(size: int, failures: int) -> int:
    """`size`, shrunk by however many consecutive failures there have been.

    `min(size, ...)` rather than the bare `max(PRESSURE_FLOOR, size // 2)`
    this replaces, and it is a defect being closed rather than a flourish: on
    a configured value of 1, `max(2, 1 // 2)` is 2, so halving moved the
    number UPWARD, so a failing provider was sent more per call than a
    healthy one. Shrinking must never grow.
    """
    for _ in range(min(failures, PRESSURE_STEPS)):
        size = min(size, max(PRESSURE_FLOOR, size // 2))
    return size


#: Reasons that belong to the PROVIDER and not to any chat. Every one of them
#: would repeat identically on the next conversation, so per-chat counting
#: would just bill TRIP_AFTER calls per open chat before anything stopped.
#: The codes are `openrouter._status_to_reason`'s vocabulary, matched against
#: the tail of `_failure_type`'s output.
_PROVIDER_REASONS: frozenset[str] = frozenset({
    "openrouter_rate_limited",       # 429, an ACCOUNT-level answer
    "openrouter_server_error",       # 5xx
    "openrouter_auth_failed",        # 401
    "api_key_invalid",
    "api_key_required_by_openrouter",
    "api_key_not_set",
    "openrouter_insufficient_credits",   # 402
    "openrouter_timeout",
    "openrouter_unreachable",
    "proxy_auth_failed",
})

#: Reasons that belong to THIS chat's own material. `openrouter_error` is
#: deliberately NOT here: it is the catch-all for a 400 that will not fit
#: (a chat fault) and for a host that cannot be reached (a provider fault)
#: alike, so claiming it for either bucket would be a guess wearing a table's
#: clothes. It falls through to "unknown" and moves both.
_CHAT_REASONS: frozenset[str] = frozenset({
    "openrouter_moderation_blocked",     # 403 + a moderation envelope
})

#: Prefixes of `_failure_type`'s output that name a fault in one chat's own
#: material. `ExtractionFailed` is every unusable reply - truncated, refused,
#: unparseable, off-schema - and each of those is a fact about the text that
#: was sent. `write_` is a commit that could not land for these rows.
#: `prompt_` is a prompt that could not be built from this card.
_CHAT_PREFIXES: tuple[str, ...] = ("ExtractionFailed", "write_", "prompt_")


def _fault_scope(error_type: str) -> str:
    """Where a failure belongs: "chat", "provider" or "unknown".

    One pure function, one decision, one place to argue with. Its input is
    whatever `_failure_type` produced - `"OpenRouterError:openrouter_timeout"`,
    `"write_RuntimeError"`, `"ExtractionFailed:truncated"` - and its output
    is one word.

    "unknown" is not a failure of this function; it is the honest answer for
    a code nobody has classified, and it moves BOTH breakers. That is the
    only default that protects the money (a provider fault counted per chat
    bills TRIP_AFTER calls per open conversation) AND stops one chat being
    re-billed forever (a chat fault counted globally is invisible until it
    has taken every other chat down with it).
    """
    reason = error_type.partition(":")[2] or error_type
    if reason in _PROVIDER_REASONS:
        return "provider"
    if reason in _CHAT_REASONS:
        return "chat"
    if error_type.startswith(_CHAT_PREFIXES):
        return "chat"
    return "unknown"

#: How many times the reading threshold a chat that has never been read may
#: hold and still be read FROM ITS FIRST MESSAGE by the ordinary reader.
#:
#: Decisions 30 a and 31 a. There used to be a third reader here, mode 1:
#: the first time a chat with no notes reached the threshold, the worker
#: started a backfill job on its own and read the whole history, up to fifty
#: calls with nobody asked. It is gone. A new chat with at most this many
#: times twenty unread messages - forty - is read from its beginning by the
#: ordinary reader, twenty at a time, one call per threshold, exactly like
#: every later read; a longer one is read from the present (its newest
#: twenty) and its history waits for the reader to start a read from the
#: reading strip, after a preview that names the price. Nothing starts a job
#: except that button.
#:
#: The edge is a fact about the conversation, so it is computed from the
#: unpressured threshold (see `from_start_edge`) and never from a number the
#: breakers move.
FROM_START_EDGE_FACTOR = 2


def from_start_edge() -> int:
    """The unread count up to which a never-read chat is read from its
    first message: `FROM_START_EDGE_FACTOR` times the threshold, forty by
    default. Read at call time, because tests and the environment both
    change the threshold."""
    return FROM_START_EDGE_FACTOR * config.NOTEBOOK_EXTRACT_EVERY_TURNS


#: How long `quiesce()`/`stop()` will wait for an ALREADY-PAID extraction to
#: finish settling before locking anyway. What it is waiting for is one
#: SQLite transaction (see Worker._settle) - this is generous headroom for a
#: slow disk, not a promise that every write completes. See Defect 1: a bare
#: `asyncio.Task.cancel()` unwinds the awaiting coroutine at once even through
#: `anyio.to_thread.run_sync(abandon_on_cancel=False)` - measured, it does NOT
#: protect the await from an external cancel - so the only reliable way to
#: let a paid write finish is to run it as its OWN task, shielded, and have
#: the canceller wait for THAT task specifically.
SETTLE_GRACE_S = 5.0

#: Characters of `notebook_extract.TURNS_MAX_CHARS` that the pronoun window
#: gets FIRST, before the new material is measured against what is left.
#:
#: RECENT_TURNS is described downstream as "context, not material", and it was
#: budgeted like a remainder to match: whatever NEW_TURNS did not spend. The
#: planner fills NEW_TURNS up to the ceiling, so on any busy chat the
#: remainder is zero and the section the prompt asks for by name goes out as
#: "(none)" - measured, a 12000-character `new` empties it every time. The
#: window is two messages (`ORDER BY id DESC LIMIT 2`), so this is one sixth
#: of the ceiling held back for a section that is normally a fraction of it,
#: and only as much of it as the window will actually use is taken.
#:
#: Held here rather than beside the ceiling it comes out of, because the split
#: is the PLANNER's decision: this is the one place the ids and the text are
#: still side by side, and `build_user_message`'s residual arithmetic then
#: leaves this much over by construction rather than by a second rule.
RECENT_RESERVE_CHARS = 2000


class Breaker:
    """Closed -> Open -> Half-Open, with a hand on the switch (A49)."""

    def __init__(self) -> None:
        self.failures = 0           # consecutive, resets on any success
        self.since_reset = 0        # what the STOP threshold counts
        self.total_failures = 0     # lifetime, for the counter on screen
        self.opened_at: float | None = None
        self.cooldown = COOLDOWN_BASE_S
        self.stopped = False
        self.trips = 0
        # The last time anything asked this breaker a question. `state` is a
        # property with no clock of its own, and "the cooldown has elapsed"
        # is a question about now - so the answer comes from the same clock
        # `allows()` uses rather than from a second one invented here.
        self._now: float | None = None
        # When this breaker last recorded a FAILURE, on the caller's clock.
        # The per-chat dictionary's TTL reads exactly this field and nothing
        # else: a record whose last failure is a day old is stale data, not
        # information, and keeping it refuses a call nobody asked it to.
        self.last_failed_at: float | None = None

    @property
    def state(self) -> str:
        """closed | open | half_open | stopped.

        `half_open` was missing and the omission reached the screen. Once the
        cooldown has elapsed `allows()` lets exactly one call through, and it
        deliberately does NOT clear `opened_at` - a success resets, a failure
        re-opens with a longer wait, and that rule is load-bearing. But the
        state read from the same field, so the panel went on saying "Paused
        after repeated failures. It will try again by itself." while a real,
        billed request was going out.

        Reported, not changed: `allows()` is untouched.

        NO ARGUMENT, so it answers from `self._now` - the clock of whoever
        last called `allows()`. That is stale exactly when it matters: a
        tripped breaker on an idle machine gets no queue items (the vault
        locks, `offer()` returns early), so nothing calls `allows()`, so
        `_now` never advances and this reports "open" long after the
        cooldown has passed. Prefer `state_at(now)` from anywhere that has
        a clock; this remains for the callers that do not.
        """
        return self.state_at(self._now)

    def state_at(self, now: float | None) -> str:
        """The same question, asked at a stated time.

        The clock is the caller's, like `allows(now)` and `failed(now)`. The
        class holds no clock of its own on purpose: every decision it makes
        is comparable to every other only if they share one, and the loop's
        `loop.time()` is the one the failures were stamped with.

        `None` means "no clock available", and then the honest answer is the
        conservative one - `open` - because a breaker that guesses `closed`
        would advertise a call it might refuse.
        """
        if self.stopped:
            return "stopped"
        if self.opened_at is None:
            return "closed"
        if now is not None and now - self.opened_at >= self.cooldown:
            return "half_open"
        return "open"

    def allows(self, now: float) -> bool:
        self._now = now
        if self.stopped:
            return False
        if self.opened_at is None:
            return True
        if now - self.opened_at >= self.cooldown:
            # Half-open: exactly one call is allowed through. It is not reset
            # here - a success resets, a failure re-opens with a longer wait.
            return True
        return False

    def succeeded(self) -> None:
        self.failures = 0
        # And the run towards the permanent stop. STOP_AFTER means twenty
        # failures with NO success in between; counting them cumulatively let
        # twenty transient network failures spread over a week kill the
        # feature for good, with one log line.
        self.since_reset = 0
        self.opened_at = None
        self.cooldown = COOLDOWN_BASE_S

    def failed(self, now: float) -> None:
        self.failures += 1
        self.since_reset += 1
        self.total_failures += 1
        self.last_failed_at = now
        if self.since_reset >= STOP_AFTER:
            self.stopped = True
            logger.warning(
                "Notebook extraction stopped after %d failures.",
                self.since_reset)
            return
        if self.failures >= TRIP_AFTER or self.opened_at is not None:
            self.trips += 1
            self.opened_at = now
            self.cooldown = min(COOLDOWN_MAX_S, COOLDOWN_BASE_S * (2 ** min(self.trips, 6)))
            logger.info("Notebook extraction paused for %.0fs.", self.cooldown)

    def reset(self) -> None:
        """The hand on the switch. A breaker with no manual reset makes the
        user restart the whole application to retry after fixing whatever
        broke - which is the same as no breaker plus an insult.

        It lifts the REFUSAL and nothing else. `self.__init__()` also zeroed
        `total_failures`, and since the permanent stop counted that, one press
        of this button made the twenty-failure ceiling unreachable forever -
        a reset that quietly disabled the strongest guard in the class.
        """
        self.failures = 0
        self.since_reset = 0
        self.opened_at = None
        self.cooldown = COOLDOWN_BASE_S
        self.stopped = False
        self.trips = 0


class Worker:
    """Owns the queue, the breaker and the batch size."""

    def __init__(self) -> None:
        # Created in start(), NOT here. An asyncio.Queue binds to the loop it
        # is first used on, and this object is module-level: built at import
        # time it belongs to whichever loop happened to exist then, and every
        # later loop - a second run, a test client, a restart after a crash -
        # gets "bound to a different event loop" from a queue that looks
        # perfectly healthy.
        self.queue: asyncio.Queue[int] | None = None
        #: GLOBAL. Only faults no chat caused reach this one - see
        #: `_fault_scope` and the module docstring.
        self.breaker = Breaker()
        #: One per chat, and only for chats that have actually FAILED. An
        #: OrderedDict rather than a dict because the ceiling below is an
        #: LRU: `move_to_end` on every look is what makes "oldest accessed"
        #: mean anything.
        self.chat_breakers: "OrderedDict[int, Breaker]" = OrderedDict()
        # Two different events, counted apart. Both used to land in
        # `dropped_offers`, and only ONE of them means what the status panel
        # says it means: a queue that overflowed is a worker falling behind,
        # while an offer arriving with no queue at all is the ordinary state
        # before startup and during every vault lock. Auto-lock fires all day,
        # `quiesce()` nulls the queue each time, and every turn sent while
        # locked added to the number labelled "turns went unqueued while it was
        # behind" - so the one counter that was supposed to reveal a backlog
        # was dominated by the most routine event in the application.
        self.queue_overflows = 0
        self.offers_while_down = 0
        self.refused_by_breaker = 0
        #: Turns refused by the chat's OWN breaker, counted apart from the
        #: global one. Folded together, "the provider is down" and "this one
        #: conversation is broken" would read as the same number, and only
        #: one of them means the other chats are fine.
        self.refused_by_chat_breaker = 0
        #: Turns the claim gate refused because the other entry point was
        #: already inside that range. Counted rather than logged: this file
        #: is on log_leak_scan's must-stay-clean list, and the number is what
        #: says whether the two paths are colliding often enough to matter.
        self.refused_in_flight = 0
        self.died: str | None = None
        self.unhandled = 0
        self.last_error: str | None = None
        #: Preambles whose turn was cancelled before it could send anything.
        #: Counted because the undo is best-effort and a reader deserves to
        #: know it happened rather than to find a call missing from the day.
        self.abandoned_preambles = 0
        self.runs = 0
        # A call that was paid for, parsed, and landed NOTHING.
        #
        # `runs` and `breaker.succeeded()` used to fire whenever `_write`
        # simply failed to raise - and `commit_extraction` reports several
        # failures by RETURNING them: a duplicate work key, a settle whose
        # attempt has been reclaimed, a range whose messages are gone. Each
        # of those was counted as a success, and because `succeeded()` also
        # clears `since_reset`, each one ERASED the real failures that came
        # before it. A worker failing every single turn could show a healthy
        # breaker and a rising run count forever.
        self.settled_empty = 0
        # One reading job at a time. A reader pressing the button three
        # times has asked for one thing, and three concurrent claims against
        # the daily cap is not what they asked for. A backfill job holds
        # this flag for its whole run. The name is the one-press sweep's,
        # which shared the lock until decision 69 a removed it.
        self._sweeping = False
        #: The backfill job this process is driving, or None. The row is in
        #: the database; this is the loop's own view of it - the id, the
        #: chat, the scope and the range - plus the stop flag the stop route
        #: raises. Read by `status()` through `backfill_block`, cleared by
        #: the loop task's done-callback.
        self._backfill: dict | None = None
        self._backfill_task: asyncio.Task | None = None
        self._backfill_stop = False
        #: Work keys this process has CLAIMED and not yet settled.
        #
        # Two coroutines can be inside `_handle` at once - the loop's and a
        # reading job's unit - and neither could see the other. `already_done`
        # answers only for `status='done'`, so the same range was planned
        # twice, claimed twice and billed twice for one answer; and
        # `_plan_work`'s opening `settle_orphaned_running` marked the other
        # one's live, paid row `failed` on the way past.
        #
        # In-process and deliberately so. There is one backend process, and a
        # database flag would need its own crash recovery - the exact
        # `running`-row problem this set exists to stop being confused with.
        self._active: set[str] = set()
        #: The chats those keys belong to, for the button.
        #
        # `backfill()` is asked about a CHAT and cannot know the work key
        # until after it has planned - by which point the claim gate below
        # has already refused it, silently, with nobody to tell. This is the
        # same fact indexed the way the route needs to ask it.
        self._active_chats: set[int] = set()
        #: The ordinary reader's current unit, as its own task, or None.
        #
        # Decision 34 a. `run()` used to await `_handle` inline, so the lock
        # cancelling the loop cancelled a read whose request was already on
        # the wire, and the range was then counted read with no note from
        # it. The unit now runs as a task of its own, shielded from the
        # loop's cancellation, and `quiesce()` gives it the same up to five
        # seconds a reading job's unit gets. When they run out it is
        # cancelled by name (`_stand_down_reading`), so its request does not
        # stay open after the key is gone.
        self._reading: asyncio.Task | None = None
        #: The (chat, model) pairs already told why the model is refused,
        #: newest last, each with the reason it was told. See `_prepare`'s
        #: gate: the refusal is written as a named skip row ONCE per pair
        #: while this process runs, not once per turn, because it is a
        #: standing condition of the chat's model rather than something that
        #: happened this turn. Keyed on the PAIR, so two unfit models
        #: alternated in one chat are two rows and not one row per switch.
        #: Bounded by `UNFIT_SAID_MAX`: both halves of the key come from
        #: the user.
        self._unfit_said: "OrderedDict[tuple[int, str], str]" = OrderedDict()
        # What nobody has read, counted once at unlock. Never a trigger: see
        # notebook.unread_backlog for why this is an offer on a screen and
        # not a background job.
        self.backlog: dict[str, int] = {"chats": 0, "messages": 0}
        # EVERY in-flight task that touches the database, not just the last
        # one. `self.settling` is a single pointer and stays one - the tests
        # and `_clear_settling`'s `is t` guard both depend on that - but a
        # single pointer cannot answer "is anything still running": a second
        # extraction overwrites it while the first is still writing, and
        # `quiesce()` then waits for the wrong task and clears the vault key
        # out from under the right one. A set answers it and cannot be
        # overwritten.
        self._inflight: set[asyncio.Task] = set()
        # What `parse_reply` refused, by reason. It came back from every
        # single call and was assigned to `_dropped` and never read - so a
        # model whose every suggestion failed the grounding check produced
        # "3 runs, 0 notes" and no way at all to find out why.
        self.dropped: dict[str, int] = {}
        self.task: asyncio.Task | None = None
        # The tail of ONE extraction - parse a paid reply, write it or record
        # its failure - running as its OWN task, shielded from the loop
        # task's cancellation. Set the instant that task is created, cleared
        # by the task itself when it finishes; `quiesce()`/`stop()` read this
        # to wait for a paid call to actually settle before the vault key is
        # cleared out from under it. See _handle, _settle, Defect 1.
        self.settling: asyncio.Task | None = None

    @property
    def dropped_offers(self) -> int:
        """Every offer that never reached the queue, whatever the reason.

        Derived rather than stored, because the two causes above are now
        counted separately and this name is a WIRE field: `status()` publishes
        it, the client schema requires it, and the panel renders it. A counter
        that is split in the backend and silently dropped from the response is
        a status screen that goes blank, which is the one thing a status screen
        may not do. So the total stays, exact, and the two causes travel beside
        it for whoever wants to tell them apart.
        """
        return self.queue_overflows + self.offers_while_down

    @dropped_offers.setter
    def dropped_offers(self, value: int) -> None:
        # Only a reset is meaningful on a derived counter - the test suite
        # zeroes this between tests, since the worker is process-wide state.
        # Splitting an arbitrary total back into two causes would be a guess,
        # and a guess written into a counter is worse than no counter.
        if value:
            raise ValueError(
                "dropped_offers is derived; set queue_overflows or "
                "offers_while_down")
        self.queue_overflows = 0
        self.offers_while_down = 0

    # ── the send path's only entry point ────────────────────────────────
    def offer(self, chat_id: int) -> None:
        """Note that a chat has moved. NEVER blocks, NEVER raises.

        Called from the completion path right after the turn is persisted. A
        background feature that can make a message fail to send is not a
        feature, so every failure mode here ends in a log line.
        """
        queue = self.queue
        if queue is None:
            # Offered before the worker started, or after the loop it belonged
            # to went away. Counted rather than raised: the send path must not
            # care whether the notebook is running.
            #
            # Its OWN counter, because this is not a backlog. The vault locks
            # on an idle timer many times a day and `quiesce()` nulls the queue
            # every time, so counting these as dropped-while-behind buried the
            # rare, real overflow under the most ordinary event there is.
            self.offers_while_down += 1
            return
        try:
            queue.put_nowait(chat_id)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
                queue.task_done()
                queue.put_nowait(chat_id)
            except (asyncio.QueueEmpty, asyncio.QueueFull):   # pragma: no cover
                pass
            # This one really is "behind": the worker is up, the queue is at
            # its bound, and a turn was lost because the drain could not keep
            # pace. It is the number the panel's sentence describes.
            self.queue_overflows += 1
            logger.info("Notebook queue full; oldest offer dropped.")
        except Exception:                                     # pragma: no cover
            # A live queue that refused an offer for some other reason is
            # still a turn lost while the worker was running, so it belongs
            # with the overflows rather than with the locks.
            self.queue_overflows += 1
            logger.warning("Notebook offer could not be queued.")

    # ── the loop ────────────────────────────────────────────────────────
    async def run(self) -> None:
        """Drain the queue forever. Cancellation is the normal exit."""
        assert self.queue is not None
        while True:
            chat_id = await self.queue.get()
            try:
                # ONE AUTOMATIC READER, and it is this one. There is no
                # automatic whole-chat read any more (decisions 30 a and
                # 31 a): a chat with no notes is read by this same path,
                # from its first message when it holds at most
                # `from_start_edge()` unread messages and from the present
                # when it holds more.
                #
                # THE UNIT IS ITS OWN TASK, SHIELDED (decision 34 a). A lock
                # cancels this loop; it must not cancel a read whose request
                # is already out, or the range is counted read and paid for
                # with no note from it. `quiesce()` waits for `_reading` up
                # to what is left of the stand-down's up to five seconds
                # and then cancels it by name; `stop()` cancels it at once,
                # as before.
                unit = asyncio.create_task(self._handle(chat_id),
                                           name="notebook-read")
                self._reading = unit
                unit.add_done_callback(self._reading_finished)
                await asyncio.shield(unit)
            except asyncio.CancelledError:
                # The vault locked, or the process is going down. Neither is a
                # failure of the model or of this code, and counting it as one
                # would walk the breaker to "stopped" every time the user
                # stepped away from their desk. The unit is not cancelled
                # here: see `quiesce()` and `stop()`.
                raise
            except Exception as exc:
                if isinstance(exc, vault_state.VaultLockedError):
                    # The vault closed under us. Not a failure of the model,
                    # of this code, or of anything the user should read about:
                    # background work deliberately does not feed the idle
                    # timer, so an ordinary lock/unlock cycle used to leave up
                    # to a queueful of "unhandled errors" on the status panel.
                    continue
                # A supervised worker that dies on one bad chat is not
                # supervised. The type name and the chat id - never the
                # message, which can carry the text that caused it. The
                # version without them logged the same sentence on every turn
                # for a chat that failed deterministically, with nothing to
                # tell which chat or what went wrong.
                self.unhandled += 1
                self.last_error = type(exc).__name__
                logger.warning(
                    "Notebook extraction raised (%s) for chat_id=%d; "
                    "loop continues.", type(exc).__name__, chat_id)
            finally:
                self.queue.task_done()

    async def _prepare(self, chat_id: int, *, min_new: int | None = None,
                       after_id: int | None = None,
                       until_id: int | None = None, reread: bool = False,
                       cap: int | None = None, job_id: int | None = None):
        """Everything up to and including the running row, or None.

        Split out of `_handle` so it can be waited for. Returns the three
        things the call itself needs - the plan, the model and the language -
        or None when this turn is not going to make a call at all, or a
        `_Skipped` carrying the reason when it wrote a skip row.

        `until_id`, `reread`, `cap` and `job_id` are the backfill job's
        keywords, in the same spirit as `min_new` and `after_id` above: a
        job reads through this door and no other. `cap` is the job's own
        lower ceiling for the claim; `job_id` rides on the plan so every
        write this attempt makes carries it.
        """
        loop = asyncio.get_running_loop()

        # A CHAT WHOSE HISTORY IS BEING READ BY A JOB IS THE JOB'S (3.12).
        # The ordinary reader used to plan against the same chat between two
        # of the job's units: it could take the stretch the job was about to
        # read, the job's next unit then found nothing to plan and closed
        # `failed (refused)`, and the notes the ordinary reader wrote carried
        # no job id, so "undo this run" could not reach them. While a job
        # holds this chat the turn is left alone; the job reads up to the
        # message that was newest when it started (3.13), and everything
        # after that is this reader's again as soon as the job ends - a
        # skipped offer is not a lost message, because the next offer
        # recounts what is unread.
        if job_id is None and self._job_holds(chat_id):
            return None

        # Before ANY database work. Planning decrypts every pending message,
        # and a stopped breaker that still did that would pay a
        # multi-hundred-kilobyte decrypt on every turn, forever, plus one
        # skipped row per turn with no ceiling. "Stopped" has to mean stop.
        # The reason is not lost: it is in-memory and on the status screen,
        # which is where a user looks when nothing is happening.
        now = loop.time()
        if not self.breaker.allows(now):
            self.refused_by_breaker += 1
            return None
        # And then the chat's own, for the same reason and at the same point.
        # GLOBAL FIRST: it is the cheaper question and the wider one, and a
        # process that is refusing everything should not walk a dictionary to
        # find that out.
        cb = self._chat_breaker(chat_id, now)
        if cb is not None and not cb.allows(now):
            self.refused_by_chat_breaker += 1
            return None

        model_id = await anyio.to_thread.run_sync(
            partial(notebook_extract.extract_model, chat_id))
        if not model_id:
            # A46: no model, no extraction. Not an error and not worth a row
            # every turn - the panel already says suggestions are off.
            return None

        # THE GATE, AT THE POINT BOTH DOORS PASS THROUGH. R-5 says a model
        # that cannot answer in the strict form notes need is refused
        # "cagri yapmadan" - without a call - and until this line that was
        # true of one door only: `_plan_backfill` asked the question, so a
        # job was refused `model_not_extractable` before it spent anything,
        # and the turn-driven reader beside it asked nothing and billed.
        #
        # That was survivable while the extraction model was a model the
        # reader picked off a list of models that can all answer. This unit
        # made it not: with `CHAT_MODEL_BINDING` stored, `extract_model`
        # resolves to whatever the CHAT is using, which is chosen for
        # conversation and never for this, and the same resolution feeds
        # both doors. So the gate belongs here, one line under the
        # resolution, rather than duplicated at each entry point where the
        # next entry point would forget it.
        #
        # NO CALL, and one named row per chat and model rather than one per
        # turn: see the refusal below, under the plan. The preview also
        # names `model_not_extractable` in `blocked_by` and the run route
        # refuses by that name.
        #
        # AND IT FAILS CLOSED FOR THE MODEL THIS UNIT DID NOT VET. The
        # gate used to answer True for anything neither cache had heard of,
        # which on a cold process is EVERY model - the ordinary state after
        # a restart, because until `_learn_about` existed the filtered list
        # was fetched by the settings picker and by nobody else. So the
        # evidence is fetched first - no model tokens, the public policy
        # list, and openrouter remembers the answer either way for
        # `MODEL_LIST_TTL` so this asks once and not once a turn - and only
        # then is the question asked. With no evidence a model reached through
        # `CHAT_MODEL_BINDING` is refused rather than paid for; a model the
        # reader picked came off the filtered list and the pick is its
        # evidence. See `_model_is_extractable`.
        bound = await anyio.to_thread.run_sync(
            notebook_extract.extract_model_setting
        ) == notebook_extract.CHAT_MODEL_BINDING
        await _learn_about(model_id, bound=bound)
        unfit = await anyio.to_thread.run_sync(
            partial(_unfit_reason, model_id, bound=bound))
        # NAMED ONCE PER (CHAT, MODEL), NOT SILENT AND NOT PER TURN (3.8).
        #
        # This refusal used to write nothing at all, on the reasoning above:
        # a standing condition is not an event, and a row per turn would be
        # an unbounded log of one fact. The first half of that was the
        # defect. The only place the reason surfaced was the reading strip's
        # preview, which a reader who never opens it never sees, so the
        # notes simply stopped coming and the Background notes card said
        # nothing about why.
        #
        # So it is written, as a skip row with its own reason, exactly once
        # for each pair of this chat and this model: the first turn that
        # would have read something writes it, and every later turn with
        # the same model returns here, before planning decrypts anything.
        # A different model is a different fact and is said again. A turn
        # that checks the chat's model here and finds it fit forgets every
        # pair of that chat, whether or not that turn then reads anything
        # (the day's cap, or fewer than twenty new messages, can stop it
        # before any call), so the next refusal is news and is said. A turn
        # stopped before this check (a job holding the chat, an open
        # breaker, no model) forgets nothing. The memory is this
        # process's and holds `UNFIT_SAID_MAX` pairs; after a restart, or
        # once a pair has aged out of it, the pair is said once more. So it
        # is once for each chat and model while that model keeps being
        # refused, and again after a turn that checked the chat's model and
        # found it fit, read or not, a restart or the pair aging out. Going
        # back to a model already said writes nothing; `standing_refusal`
        # still finds that pair's older row.
        #
        # TWO REASONS, and only one of them blames the model. A
        # bound model the provider's list does not carry is
        # `model_not_extractable`. A list that could not be fetched at all
        # says nothing about the model, so that refusal is
        # `model_list_unavailable`, with its own sentence, and a later turn
        # that gets the list and finds the model missing is a different
        # reason for the same pair and is said.
        if unfit is not None and self._unfit_already_said(chat_id, model_id,
                                                          unfit):
            return None
        if unfit is None:
            self._forget_unfit(chat_id)

        language = await anyio.to_thread.run_sync(_language)
        plan = await anyio.to_thread.run_sync(
            partial(_plan_work, chat_id, model_id, language,
                    self.trigger_threshold_for(chat_id),
                    self.batch_size_for(chat_id),
                    min_new=min_new, after_id=after_id,
                    until_id=until_id, reread=reread,
                    # THE UNPRESSURED NUMBER, and the parameter exists only
                    # so it can be. The first-read branch asks "is this
                    # conversation meeting the feature for the first time",
                    # which is a fact about the conversation - and the batch
                    # above shrinks when the provider is unwell, so taking
                    # the edge off it would send a twenty-message chat down
                    # the long-history branch and leave eighteen of them
                    # permanently unread with the breaker still CLOSED. That
                    # is H-14, and this is the door it would have walked
                    # back in through. Forty by default: decisions 30 a and
                    # 31 a, see `from_start_edge`.
                    first_read_edge=from_start_edge(),
                    # A JOB WALKS FROM THE OLDEST END (3.11). See
                    # `_plan_work`: the ordinary reader keeps the newest
                    # lines that fit, a job keeps the oldest, so a job over
                    # long messages moves forward through the chat instead
                    # of stepping over its beginning.
                    from_oldest=job_id is not None,
                    # What the OTHER entry point is holding. `_plan_work`
                    # opens by closing out `running` rows as orphans, and
                    # without this a job unit's first act could be to fail
                    # the loop's live call and move the cursor past it.
                    keep=frozenset(self._active)))
        if plan is None:
            return None
        if unfit is not None:
            # Something would have been read, so this is the turn the pair
            # is said on. The row names the stretch that was not read, with
            # no claim and no call behind it; a skipped row does not move
            # the cursor, so that stretch is read the day a model that can
            # write notes is chosen. Its work key is computed from this
            # chat's model, which is how the status route later tells
            # whether the refusal still names the model the chat uses
            # (`standing_refusal`).
            await _record_skip(chat_id, unfit, plan)
            self._remember_unfit(chat_id, model_id, unfit)
            return _Skipped(unfit)
        # The identity of THIS attempt, not of the range - work_key names the
        # range and is deterministic, so a re-plan of the identical range
        # reuses the same key. Carried through every write this attempt makes
        # (_record_running, _record_failure, _write) so a stale settle from an
        # abandoned attempt can be told apart from the retry that reclaimed
        # the row. See commit_extraction's ownership check, Defect 2.
        plan["attempt_token"] = secrets.token_hex(16)
        # ON THE PLAN, because the plan is the only thing the done-callback
        # that undoes an abandoned preamble is given. It had no way to say
        # which chat it was giving back.
        plan["chat_id"] = chat_id
        # And the job, for the same reason: `_write` and `_record` are
        # handed the plan and nothing else, and the notes and counters
        # they write have to name the run they belong to.
        plan["job_id"] = job_id

        # Before the gate, before the claim, before the money. The key was
        # computed and then never consulted: `already_done` had no caller in
        # the repository at all, and the duplicate was only noticed after the
        # call had been sent and billed - at which point the answer was thrown
        # away and the range stayed unread.
        if await anyio.to_thread.run_sync(_already_done, plan["work_key"]):
            return None

        # AND the same question about right now, which `already_done` cannot
        # answer. It reads `status='done'`; a range the other entry point
        # claimed thirty seconds ago and is still generating against has a
        # `running` row, passes that gate, and gets claimed and billed a
        # second time for one answer.
        #
        # Checked and taken in the same breath - no `await` between them -
        # so the two coroutines cannot both pass.
        work_key = plan["work_key"]
        if work_key in self._active:
            # SILENT, and the silence is correct here.
            #
            # This tried to write a `skipped` row and could not: the row for
            # this work key already exists as `running`, written by whoever
            # got here first, and `commit_extraction` is keyed on that. A
            # `skipped` row would have overwritten the live trace of a call
            # that is still out - so the reason was declared, given a
            # sentence, and dropped on the floor every time.
            #
            # The person who needs to hear it is the one starting a read,
            # and `backfill()` refuses them by name (`already_in_flight`)
            # before it gets this far. On the
            # loop's side nobody is waiting for an answer, so this is a net
            # under a second claim and nothing else.
            self.refused_in_flight += 1
            return None
        self._active.add(work_key)
        self._active_chats.add(chat_id)

        # GIVEN BACK IN ONE PLACE, and the first version of this gave it
        # back in four.
        #
        # Four hand-written `discard`s cover the exits somebody thought of.
        # `_claim_one` below raises `VaultLockedError` and
        # `sqlite3.OperationalError`, and neither is a `NotebookError`, so
        # the single likeliest failure on this path escaped all four. The key
        # then stayed in the set for the life of the process - nothing clears
        # it - and a key in the set is a key in `keep`, so the `running` row
        # could never be closed out, the cursor never advanced, the identical
        # range was re-planned and refused every turn, and the panel went on
        # reporting a healthy worker. A permanent silent stall for that chat,
        # which is worse than the double bill this gate exists to prevent.
        #
        # `handed_over` rather than a bare `finally`: on the ONE path that
        # succeeds the key belongs to `_handle` from here, and `_handle`'s
        # own `finally` is what gives it back.
        handed_over = False
        try:
            try:
                from proxy_health import enforce_proxy_gate
                await enforce_proxy_gate()
            except Exception:
                await _record_skip(chat_id, "proxy_gate", plan)
                return _Skipped("proxy_gate")

            # BEFORE the claim, because the claim is the moment the day's budget
            # is spent and this is the question of whether anything can be sent
            # at all. `openrouter.complete`'s first statement reads this same
            # secret and raises - so with no key the old order burned a slot per
            # turn on a request that never left the machine, twenty of them, and
            # then stopped the worker with an unnamed failure.
            #
            # Not folded into the provider: a pre-flight inside `complete` would
            # still be reached AFTER the claim, and the claim is the thing that
            # needs to not happen.
            if not await anyio.to_thread.run_sync(_have_api_key):
                await _record_skip(chat_id, "api_key_not_set", plan)
                return _Skipped("api_key_not_set")

            try:
                # The day the claim is STAMPED WITH, carried on the plan.
                #
                # The refund used to re-derive it hours later, so a claim made at
                # 23:59 and abandoned at 00:00 decremented a row it had never
                # touched: yesterday's phantom claim stayed, today's counter went
                # down for a call today never made, and the ceiling passed one
                # extra billed call.
                # The job's own ceiling when there is a job, the whole
                # ceiling otherwise. Called with no argument on the
                # ordinary path so the function keeps the shape every
                # existing caller and test hands it.
                plan["claim_day"] = await anyio.to_thread.run_sync(
                    _claim_one if cap is None else partial(_claim_one, cap))
            except notebook.NotebookError as exc:
                await _record_skip(chat_id, exc.code, plan)
                return _Skipped(exc.code)

            # The claim commits in its OWN transaction, BEFORE the request, so from
            # this line on the day's budget is spent whatever happens next. That
            # left one hole nothing covered: a cancellation in flight - the vault
            # locking while the provider is generating, which this module expects
            # rather than defends against - unwinds through `raise` and recorded
            # NOTHING. `calls` was +1, the cost was never attributed to it, and
            # there was no extraction row of ANY status, so the identical work key
            # was re-planned and re-billed on the chat's next reply.
            #
            # The schema has carried `status = 'running'` and `started_at` for
            # exactly this since the table was written - "so a crash leaves a
            # trace" - and nothing in the repository ever wrote them. This is that
            # row. It is not a lock and it does not refuse anything: it is the
            # evidence that a paid call left, and the success and failure paths
            # below settle it into `done` or `failed` through the same
            # `commit_extraction`, which updates a prior non-`done` row in place.
            if not await _record_running(chat_id, plan):
                # No trace could be written, so a call made now would be exactly
                # the unaccountable spend the row exists to prevent - and since the
                # bookkeeping is what just failed, the OUTCOME could not be
                # recorded either. One wasted call is much cheaper than an
                # invisible billed one, so this stops here and says so on the
                # status screen rather than in a log line.
                #
                # The claim goes BACK. Nothing has been written to the socket at
                # this line - `openrouter.complete` is a hundred lines below - so
                # "the day's budget is spent whatever happens next" is not true
                # yet, and charging the day for a request that never left is a
                # second failure on top of the first. Sixty calls a day, and a
                # chat that hits this path repeatedly could spend the whole
                # allowance without ever reaching a provider.
                await anyio.to_thread.run_sync(
                    _release_one, plan["claim_day"])
                self.unhandled += 1
                self.last_error = "running_row_unwritable"
                logger.warning(
                    "Notebook extraction abandoned before the call for "
                    "chat_id=%d: its trace could not be written.", chat_id)
                return None
            handed_over = True
            return plan, model_id, language
        finally:
            if not handed_over:
                self._active.discard(work_key)
                self._active_chats.discard(chat_id)

    async def _handle(self, chat_id: int, *, min_new: int | None = None,
                      after_id: int | None = None,
                      until_id: int | None = None, reread: bool = False,
                      cap: int | None = None,
                      job_id: int | None = None) -> str | None:
        """One work unit, and a one-word account of it.

        Returns None when nothing was planned, the skip reason when a skip
        row was written, `"failed"` when the call could not be made or
        came back unusable, and `"settled"` when a paid reply reached the
        settle. The loop in `run` ignores the word; the backfill loop reads
        it to decide whether to go round again and what to write on the
        job when it does not.
        """
        # THE PREAMBLE IS A TRACKED, SHIELDED TASK.
        #
        # Everything before the provider call touches the database from a
        # worker thread, and two of those touches WRITE: the daily claim
        # commits in its own transaction, and the running row opens a
        # `BEGIN IMMEDIATE`. None of it was reachable from `quiesce()`.
        #
        # The chain that made that a real hazard: `quiesce()` cancels the
        # loop task and then waits for `self.settling` - which is set AFTER
        # the provider answers, so during the whole preamble there is nothing
        # registered to wait for. `_await_inflight` returned instantly,
        # `lock_vault_now` cleared the vault key on its very next line, and a
        # thread was still holding an open keyed connection and a write lock.
        # Cancelling the loop task does not stop that thread: a thread
        # already inside `run_sync` runs to completion whatever the awaiting
        # coroutine does.
        #
        # So the preamble runs as its own task, registered before it starts
        # and shielded from the caller's cancellation, exactly as the settle
        # tail already was. The grace period applies to it too.
        prep = asyncio.create_task(
            self._prepare(chat_id, min_new=min_new, after_id=after_id,
                          until_id=until_id, reread=reread, cap=cap,
                          job_id=job_id),
            name="notebook-prepare")
        self._track(prep)
        try:
            ready = await asyncio.shield(prep)
        except asyncio.CancelledError:
            # THE SHIELD WORKED, AND THAT IS THE PROBLEM TO HANDLE.
            #
            # `prep` keeps running and will finish its claim and its
            # `running` row, then hand a plan to this coroutine, which is
            # gone. Nothing sends the request. Left alone, the row becomes
            # `abandoned_in_flight` on the next cycle and the cursor counts
            # that range as read - so a vault lock landing here cost one of
            # sixty daily calls and one stretch of messages, permanently,
            # for a request that never left the machine.
            #
            # The undo is attached rather than awaited: this coroutine is
            # being torn down and may not await anything. `prep` is still in
            # `_inflight`, so `_await_settle` holds the grace period open
            # for it, and the callback runs on the same loop before that
            # window closes.
            prep.add_done_callback(self._undo_abandoned_preamble)
            raise
        if ready is None:
            return None
        if isinstance(ready, _Skipped):
            return ready.reason
        loop = asyncio.get_running_loop()
        plan, model_id, language = ready
        try:
            import openrouter
            try:
                # Everything from here on is INSIDE the claim. A failure between
                # the claim and the request used to land in the loop's generic
                # handler: a sixtieth of the day's budget gone, no row in any
                # counter, and `calls+1` against zero cost.
                messages = [
                    {"role": "system",
                     "content": notebook_extract.system_prompt(language)},
                    {"role": "user",
                     "content": notebook_extract.build_user_message(
                         card=plan["card"], existing=plan["existing"],
                         recent=plan["recent"], new=plan["new"])},
                ]
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # This chat's card and this chat's text are what failed to
                # become a prompt; no other conversation is implicated.
                error_type = "prompt_" + type(exc).__name__
                self._note_failure(chat_id, loop.time(), error_type)
                await _record_failure(chat_id, plan, error_type)
            # Same window as the one above, same reason: the prompt is built
                # BEFORE the request, so this failure means nothing was sent.
                # SUBSCRIPT, not `.get`. This line is only reachable after
                # the claim succeeded, so the key is always there - and the
                # fallback a `.get` would take is `day=None`, which is the
                # re-derive this whole change removed. A refactor that broke
                # the invariant would silently restore the bug.
                await anyio.to_thread.run_sync(
                    _release_one, plan["claim_day"])
                return "failed"

            try:
                reply = await openrouter.complete(
                    messages,
                    model_id,
                    {"max_tokens": notebook_extract.MAX_TOKENS, "temperature": 0},
                    dict(config.PROVIDER_POLICY),
                    response_format=notebook_extract.RESPONSE_FORMAT,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # loop.time() NOW, not the `now` captured before the request.
                # A provider that hangs burns COMPLETION_TIMEOUT (120s) per call,
                # so `opened_at` would be stamped two minutes in the past against
                # a 60s cooldown: the breaker opens and is already half-open, and
                # every following turn gets another full billed call through a
                # breaker reporting "open".
                #
                # WHERE it is counted is `_fault_scope`'s decision, not this
                # line's. A 429 or a 5xx is the account's answer and would
                # repeat identically on every other chat; a moderation
                # refusal is about the text this chat just sent. The same
                # `except` catches both, so the sorting happens on the
                # reason code rather than on the call site.
                error_type = _failure_type(exc)
                self._note_failure(chat_id, loop.time(), error_type)
                # The class name alone said "OpenRouterError" for every single
                # provider failure - a timeout, a refused proxy, a rate limit, a
                # missing key - and threw away the only part a reader could act
                # on. `openrouter` raises one class and puts the separated reason
                # in the message, which is why the settle path two hundred lines
                # down already records `str(exc)`.
                # RELEASED ONLY WHEN NOTHING WAS BILLED.
                #
                # This except catches both sides of the only line that
                # matters: a request that never reached a socket
                # (`api_key_not_set`, a refused proxy, a destination the
                # egress gate would not open, a connect timeout) and one that
                # was sent, generated, billed and then failed. Keeping the
                # slot for both was the conservative half, and it cost a
                # sixtieth of the day every time a proxy was misconfigured.
                #
                # NOT a `sent_at` column, which is what this was waiting for.
                # A stamp we write after the fact cannot tell a connect
                # timeout from a read timeout - both are one
                # `TimeoutException` by the time anyone could write it, and
                # one of them has been paid for. The exception knows, because
                # it is raised at the point where the answer is a fact. See
                # `OpenRouterError.reached_provider`.
                #
                # `is False`, not `not`: an exception from somewhere else
                # carries no such attribute, and `getattr(..., True)` keeps
                # the old behaviour for it. Unknown still means billed.
                if getattr(exc, "reached_provider", True) is False:
                    await anyio.to_thread.run_sync(
                        _release_one, plan["claim_day"])
                await _record_failure(chat_id, plan, error_type)
                return "failed"

            # Defect 1. From here the call has been sent, generated and BILLED -
            # `reply` is the proof. Everything left to do (parse it, write it or
            # record its failure) must not be allowed to vanish if the vault
            # locks in the next few lines, and a bare `asyncio.Task.cancel()`
            # reaches here even through `anyio.to_thread.run_sync
            # (abandon_on_cancel=False)`: measured, that flag does NOT protect
            # the awaiting coroutine from an external cancel - the coroutine
            # unwinds at once and the worker thread keeps running detached,
            # racing `vault_state.clear_key()` for a connection to open.
            #
            # So the rest of this turn runs as its OWN task, SHIELDED from our
            # own cancellation. `quiesce()`/`stop()` do not just cancel this loop
            # and walk away - they wait for `self.settling` specifically, so a
            # commit that has already been paid for is allowed to finish (or to
            # record its own failure, with the cost attached) before the caller's
            # next line clears the key out from under it.
            settle_task = asyncio.create_task(
                self._settle(chat_id, plan, reply), name="notebook-settle")
            self.settling = settle_task
            # In the set as well as in the pointer. The pointer is what the
            # done-callback below guards and what the tests read; the set is what
            # survives a second extraction starting while this one still runs -
            # which `quiesce()` makes possible by restarting the loop after its
            # own timeout.
            self._track(settle_task)

            def _clear_settling(t: asyncio.Task, w: "Worker" = self) -> None:
                if w.settling is t:
                    w.settling = None
            settle_task.add_done_callback(_clear_settling)

            await asyncio.shield(settle_task)
            return "settled"
        finally:
            # THE RANGE IS FREE AGAIN, whatever happened to it.
            #
            # One place, not eight: every path out of this turn - sent and
            # settled, refused by the provider, cancelled mid-generation -
            # ends here, and a key left behind would refuse the range
            # forever with `already_in_flight` and no way back. The
            # cancellation path is the exception that proves it: `_handle`
            # never reaches this line then, so `_undo_abandoned_preamble`
            # discards the key itself.
            self._active.discard(plan["work_key"])
            self._active_chats.discard(chat_id)

    async def _settle(self, chat_id: int, plan: dict, reply: dict) -> None:
        """Parse a PAID reply and land it. Runs as its own task - see the
        shield in `_handle` above, which is the whole reason this is a
        separate method rather than the tail of that one: everything in here
        must keep running to completion even if `_handle`'s own await gets
        cancelled out from under it.
        """
        loop = asyncio.get_running_loop()
        usage = notebook_extract.usage_of(reply)
        try:
            proposals, dropped = notebook_extract.parse_reply(
                reply, plan["chunk"], plan["existing"],
                spans=plan.get("spans"))
            # Counted, not discarded. These are the only evidence of WHY a
            # paid call produced no notes; without them "0 notes this week"
            # and "every suggestion failed the grounding check" are the same
            # screen. Kept in memory and on the status route rather than in
            # the schema: the reasons are a fixed vocabulary produced by our
            # own parser, so a counter answers the question a column would.
            # COUNTED, and only counted. Not logged: this file is in
            # log_leak_scan's "must stay clean" list, and both of these are
            # derived from `parse_reply(reply, ...)` - the scanner cannot
            # know that its keys are a fixed vocabulary of our own, and it is
            # right not to guess. The counter reaches the status route, which
            # is where a reader looks anyway; a log line would add nothing
            # the panel does not already say.
            for reason, count in (dropped or {}).items():
                if count:
                    self.dropped[reason] = self.dropped.get(reason, 0) + count
        except notebook_extract.ExtractionFailed as exc:
            # A reply that cannot be trusted is a FAILURE, and the range stays
            # unread. Recorded as done-with-nothing it would be skipped
            # forever, which is the shape this whole design is built against.
            # THIS CHAT'S, every one of them. `truncated` is a context too
            # big for the reply budget, `refused` is a moderation answer to
            # this text, `unparseable` and the schema failures are what the
            # model returned for this card and this range. None of them says
            # anything about the next conversation, and counting them
            # globally is what used to take every other chat down.
            self._note_failure(chat_id, loop.time(), _failure_type(exc))
            await _record_failure(chat_id, plan, str(exc), usage=usage)
            return

        try:
            outcome = await anyio.to_thread.run_sync(
                _write, chat_id, plan, proposals, usage)
        except Exception as exc:
            # COUNTED, not merely recorded. A row was written and nothing else
            # was: the breaker stayed closed, `unhandled` stayed at zero and
            # `runs` never moved, so a chat that fails to commit every time -
            # a vault that locked between the reply and the commit, a chat
            # deleted mid-call so the foreign key fires, a disk error - burned
            # one billed call per threshold, forever, while the panel said
            # "Running. 0 runs." Only the daily cap ever stopped it, and a cap
            # is a ceiling on the damage, not a report of it.
            #
            # A locked vault is deliberately NOT a failure elsewhere in this
            # file (see `run`), and this is the considered exception rather
            # than an oversight: that rule exists so cancellations which cost
            # NOTHING cannot walk the breaker towards "stopped" every time
            # somebody steps away. By this line the call has been sent,
            # generated and paid for, and the work is lost. Five of those in a
            # row is precisely what the breaker is for.
            self.unhandled += 1
            error_type = "write_" + type(exc).__name__
            # A commit that cannot land for THESE rows: a foreign key on a
            # chat deleted mid-call, a row this chat's own content broke.
            # `_fault_scope` sends it to the chat, and the fan-out net is
            # what catches a disk error that is really global.
            self._note_failure(chat_id, loop.time(), error_type)
            if not await _record_failure(chat_id, plan, error_type,
                                         usage=usage):
                # `_record` swallows its own failure to keep the loop alive,
                # which is right, and it did so SILENTLY, which is not: when
                # the vault is still locked the row is lost as well as the
                # write, and then the database also says nothing happened. The
                # in-memory counter is the last witness, so it names it.
                error_type += "_unrecorded"
            self.last_error = error_type
            return

        # NOT RAISING IS NOT SUCCEEDING.
        #
        # commit_extraction reports three of its failures by returning them:
        # `duplicate` (this work key was already answered), `stale_attempt`
        # (the row has since been reclaimed by a retry), and `written == 0`
        # with a skip reason (the range was cleared or rewritten while the
        # reply was out). Every one of those was read as a success, `runs`
        # went up, and `succeeded()` wiped the breaker's memory of the real
        # failures that came before - so the panel showed a healthy worker
        # writing nothing, indefinitely.
        #
        # `written == 0` with no proposals at all is a different thing and is
        # NOT counted here: a reply that honestly found nothing worth noting
        # is a completed run. The empty case is about a call whose result
        # could not be landed, not about a quiet chat.
        landed = bool(outcome) and (
            not outcome.get("duplicate")
            and not outcome.get("stale_attempt")
            and (outcome.get("written", 0) > 0 or not proposals))
        if not landed:
            self.settled_empty += 1
            # On the status screen, not in the log. Same reason as the
            # dropped counters above: this is derived from what `_write`
            # returned for this reply, and nothing in this file may put a
            # value derived from a reply into elysium.log.
            self.last_error = _empty_reason(outcome)
            return
        self.breaker.succeeded()
        # T1, and the rule that clears almost every record in practice: a
        # chat that works again holds nothing at all. Kept next to the global
        # `succeeded()` rather than in a tidier place, because the one thing
        # that must never happen is these two drifting apart.
        self._chat_recovered(chat_id)
        self.runs += 1

    @property
    def batch_size(self) -> int:
        """How many messages ONE extraction may read. Halves on failure.

        This is the amount of WORK per call, and it was very nearly the
        opposite: halving the trigger THRESHOLD instead. That version fired
        ten times per twenty messages instead of once, and since the fixed
        part of the prompt - the card, the existing notes, the instructions,
        about ten kilobytes - is paid in full on every call regardless of how
        much transcript rides along, it multiplied the bill by ten at exactly
        the moment the provider was failing. A safety feature that is an
        amplifier.

        Halving what is READ is the real thing for the token side: fewer
        tokens and a smaller window, and with the provider fully down it
        really does send fewer characters.

        IT IS NOT THE SAME NUMBER OF CALLS, and that half of this defence was
        false from ONE consecutive failure. `batch_size` halves;
        NOTEBOOK_EXTRACT_EVERY_TURNS does not, so the same backlog is bought
        in more pieces and the fixed part of the prompt is paid again in each
        of them. HOW MANY MORE is measured, not written here: SECURITY.md
        quotes the call ratio of halving ON against OFF for each failure
        pattern, measured through reading jobs over a fixed backlog, and
        `TestTheFiguresTheDocumentsQuote` in
        tests/test_notebook_planner_budget.py drives this code and holds
        every quoted figure (from backend/, `python -m pytest
        tests/test_notebook_planner_budget.py -k TheFiguresTheDocumentsQuote
        -s` prints them). No copy of the numbers lives in this docstring, so
        none can go stale here.

        And the worse half is not the price, it is the INVERSE
        PRODUCTIVITY: when most calls fail, the job's calls for the day run
        out on two-message batches, so the halved run reads a fraction of
        what the unhalved run reads (the same test holds how much). In the
        degraded regime this mechanism exists for, it pays more and does
        less.

        THE TRIGGER THRESHOLD DOES NOT HALVE WITH IT (decision 28 a). It
        used to, on the argument that two numbers on one rule cannot drift
        apart, and a measurement that the halved threshold changed nothing
        for the chat that was failing. It did change something for every
        OTHER chat: the counter it read was the global one, so one chat's
        timeouts dropped the whole install's threshold to 10, 5 and 2, and a
        healthy conversation with twelve unread messages was read at once,
        mid-outage, at a smaller batch. The rule is twenty messages
        and it holds in a failing period too; only what each call reads
        shrinks. `trigger_threshold` below is the constant, on purpose.

        The rest of the backlog is not lost either way: it stays unread and
        the next run picks it up.
        """
        return _pressured(config.NOTEBOOK_EXTRACT_EVERY_TURNS,
                          self.breaker.failures)

    @property
    def trigger_threshold(self) -> int:
        """How much new material is worth a call: twenty messages, always.

        Decision 28 a. Failures shrink the batch (`batch_size`) and never
        this: a provider fault in one chat must not make every other chat
        read more often. Read at call time, because tests and the
        environment both set the constant.
        """
        return config.NOTEBOOK_EXTRACT_EVERY_TURNS

    # ── the per-chat breakers ───────────────────────────────────────────
    def _pressure(self, chat_id: int | None) -> int:
        """Consecutive failures governing this chat's numbers.

        The WORSE of the two counters, because both are true at once: a chat
        that keeps failing should read less of itself, and a provider that
        keeps failing should be sent less of anything.
        """
        worst = self.breaker.failures
        if chat_id is not None:
            cb = self.chat_breakers.get(chat_id)
            if cb is not None:
                worst = max(worst, cb.failures)
        return worst

    def batch_size_for(self, chat_id: int) -> int:
        """`batch_size`, asked about one chat. See `batch_size`."""
        return _pressured(config.NOTEBOOK_EXTRACT_EVERY_TURNS,
                          self._pressure(chat_id))

    def trigger_threshold_for(self, chat_id: int) -> int:
        """`trigger_threshold`, asked about one chat. The same twenty
        whatever this chat's own breaker says: a chat that keeps failing
        reads less per call (`batch_size_for`) and is retried on its very
        next reply, because a failure does not move the cursor (decision
        33 a) - it is not read on fewer messages."""
        return config.NOTEBOOK_EXTRACT_EVERY_TURNS

    def _expire_chat_breakers(self, now: float | None) -> None:
        """Drop records whose last failure is older than the TTL.

        From the FRONT and stopping at the first survivor: the dictionary is
        kept in last-touched order, so this is amortised O(1) rather than a
        walk of every entry on every turn.

        WHERE THAT IS APPROXIMATE, said out loud: last-touched order is not
        last-failed order. A record that was merely looked at recently sits
        behind one that failed recently, so a stale entry can outlive its TTL
        until it reaches the front. That is a memory question and not a
        behaviour one - by then its cooldown has long elapsed (an hour is the
        ceiling) so it refuses nothing - and CHAT_BREAKERS_MAX bounds it
        regardless. An exact sweep would walk every entry on every turn to
        buy nothing.
        """
        if now is None:
            return
        while self.chat_breakers:
            chat_id, cb = next(iter(self.chat_breakers.items()))
            stamp = cb.last_failed_at
            if stamp is None or now - stamp < CHAT_BREAKER_TTL_S:
                return
            del self.chat_breakers[chat_id]

    def _chat_breaker(self, chat_id: int, now: float | None = None):
        """This chat's breaker, or None. A LOOK, never a creation.

        Creating on a look would give a healthy install one record per chat
        it has ever seen, which is the leak this whole cleanup policy exists
        against. Records are made in `_fail_chat` and nowhere else.
        """
        self._expire_chat_breakers(now)
        cb = self.chat_breakers.get(chat_id)
        if cb is not None:
            self.chat_breakers.move_to_end(chat_id)
        return cb

    def _fail_chat(self, chat_id: int, now: float) -> "Breaker":
        """Count one failure against this chat, making the record if needed.

        Also the only place the fan-out net fires: if this failure leaves
        more than CHAT_TRIP_FANOUT separate chats refusing at once, the
        global breaker takes one too. See the module docstring - that is not
        a second route for chat faults, it is the alarm on `_fault_scope`
        being wrong.
        """
        self._expire_chat_breakers(now)
        cb = self.chat_breakers.get(chat_id)
        if cb is None:
            cb = self.chat_breakers[chat_id] = Breaker()
        self.chat_breakers.move_to_end(chat_id)
        cb.failed(now)

        # The ceiling, enforced AFTER the insert so the newest failure is
        # never the one thrown away. What an eviction costs is named: that
        # chat forgets its refusal and earns one more call. It takes
        # CHAT_BREAKERS_MAX chats failing at once to reach, and the global
        # breaker and the daily cap are both still underneath.
        while len(self.chat_breakers) > CHAT_BREAKERS_MAX:
            self.chat_breakers.popitem(last=False)

        tripped = sum(1 for b in self.chat_breakers.values()
                      if b.state_at(now) != "closed")
        if tripped > CHAT_TRIP_FANOUT:
            logger.warning(
                "Notebook extraction: %d chats refusing at once; counting it "
                "against the global breaker.", tripped)
            self.breaker.failed(now)
        return cb

    def _chat_recovered(self, chat_id: int) -> None:
        """A chat that works again holds nothing.

        The cheapest of the four cleanup rules and the one that does most of
        the work: in practice almost every record is made by a transient
        failure and removed by the next success.
        """
        self.chat_breakers.pop(chat_id, None)

    def forget_chat(self, chat_id: int) -> None:
        """The chat is gone - deleted, or cleared down to nothing.

        The messages that produced the failure went with it, so a refusal
        that outlived them would be refusing a conversation that no longer
        exists. Public because the routes that delete chats call it, and it
        is deliberately silent about a chat it has never heard of.
        """
        self.chat_breakers.pop(chat_id, None)

    def _note_failure(self, chat_id: int, now: float, error_type: str) -> None:
        """Count one failure wherever `_fault_scope` says it belongs.

        The single door. Every `breaker.failed()` in this file used to be
        written out at the call site, which is how a rule that has to hold in
        four places holds in three.
        """
        scope = _fault_scope(error_type)
        if scope in ("provider", "unknown"):
            self.breaker.failed(now)
        if scope in ("chat", "unknown"):
            self._fail_chat(chat_id, now)

    def _undo_abandoned_preamble(self, task: "asyncio.Task") -> None:
        """Give back what a preamble claimed for a turn that never happened.

        Runs as a done-callback on `_prepare` when the coroutine that would
        have used its plan was cancelled. Two things go back: the daily call
        slot, and the `running` row - because a row left behind is read by
        `settle_orphaned_running` as a paid attempt and moves the cursor past
        a range nothing has read.

        Best effort and silent about the vault, deliberately: the usual
        reason for landing here IS the vault locking, and by now the key may
        be gone. What it must never do is raise out of a done-callback,
        where asyncio would log it as an unretrieved exception.

        `_undo` below carries the same dependency `_release_one` does: its
        vault clause names `vault_state`, and the name is only resolvable
        because the module imports it at the top. While it was imported
        inside `run()` instead, both handlers here were dead, and the harm
        was the exact thing the capitalised comment inside `_undo` forbids.
        On the dominant path - a locked vault, which is the reason this
        callback exists at all - the `NameError` fired inside `_release_one`,
        one line ABOVE the try, so the refund ate the deletion and the row
        removal never ran at all. Measured on that path: one database
        attempt inside `_undo` while the import sat in `run()`, two once it
        moved to module level.
        """
        if task.cancelled():
            return
        if task.exception() is not None:
            # It failed before claiming anything; `_prepare`'s own handlers
            # already released what they took.
            return
        ready = task.result()
        if ready is None or isinstance(ready, _Skipped):
            # Nothing claimed, or a skip already recorded and released.
            return
        plan, _model_id, _language = ready
        self.abandoned_preambles += 1
        # `_handle` was torn down before its `finally` could run, so the key
        # is given back HERE or not at all - and not at all means the range
        # is refused for the life of the process.
        self._active.discard(plan["work_key"])
        self._active_chats.discard(plan["chat_id"])

        def _undo() -> None:
            # TWO REPAIRS, AND NEITHER MAY EAT THE OTHER.
            #
            # These were one unguarded block. `get_db` raises
            # `VaultLockedError` - which is the usual reason this callback
            # exists at all - and that took the row deletion down with the
            # refund, leaving behind the very `running` row this method's
            # docstring says moves the cursor past a range nothing read. The
            # same change taught `_release_one` to expect that exception and
            # left its twin here with nothing.
            from database import get_db

            _release_one(plan["claim_day"])
            try:
                with get_db() as con:
                    con.execute(
                        "DELETE FROM notebook_extractions "
                        "WHERE work_key = ? AND status = 'running'",
                        (plan["work_key"],))
            except vault_state.VaultLockedError:
                logger.info(
                    "Notebook: the trace of an abandoned preamble could not "
                    "be removed because the vault locked first; the next "
                    "cycle closes it out instead.")
            except Exception:
                logger.warning(
                    "Notebook: the trace of an abandoned preamble could not "
                    "be removed.")

        try:
            # TRACKED, like every other write this module starts.
            #
            # A bare `create_task` here was not waited for by
            # `_await_settle` and was referenced only weakly by the loop, so
            # the undo's `BEGIN IMMEDIATE` could land AFTER the stand-down
            # had returned and told the vault route it was safe to rekey.
            # The fix for an abandoned preamble was reopening, in miniature,
            # the race the tracking exists to close.
            self._track(asyncio.get_running_loop().create_task(
                anyio.to_thread.run_sync(_undo)))
        except Exception:                                 # pragma: no cover
            pass

    def _reading_finished(self, task: "asyncio.Task") -> None:
        """The ordinary reader's unit is done: forget it, and read its
        outcome so an exception that nobody awaited (the loop was cancelled
        while the unit ran on) is not reported by asyncio as unretrieved.
        `run()` has already counted it when it was there to see it."""
        if self._reading is task:
            self._reading = None
        if not task.cancelled():
            task.exception()

    def _unfit_already_said(self, chat_id: int, model_id: str,
                            reason: str) -> bool:
        """Whether this chat was told this reason about this model and the
        note of it is still held (3.8). It answers False once the note is
        gone, even though the chat was told earlier: after a fit turn's
        `_forget_unfit` for this chat, after the pair aged out past
        `UNFIT_SAID_MAX`, and after a restart (the notes live in memory
        only). A hit is touched, so the pairs a reader keeps meeting are
        the last to age out."""
        key = (chat_id, model_id)
        if self._unfit_said.get(key) != reason:
            return False
        self._unfit_said.move_to_end(key)
        return True

    def _remember_unfit(self, chat_id: int, model_id: str,
                        reason: str) -> None:
        """Note that the pair was told, newest last, and forget the oldest
        pairs past `UNFIT_SAID_MAX`. Read at call time, so a test or the
        environment may set the bound."""
        key = (chat_id, model_id)
        self._unfit_said[key] = reason
        self._unfit_said.move_to_end(key)
        while len(self._unfit_said) > UNFIT_SAID_MAX:
            self._unfit_said.popitem(last=False)

    def _forget_unfit(self, chat_id: int) -> None:
        """The chat's model is fit now: forget every pair of this chat, so
        going back to an unfit model later is said again. One pass over at
        most `UNFIT_SAID_MAX` keys, on every turn that checks the chat's
        model and finds it fit, read or not; a turn stopped before that
        check does not call this."""
        for key in [k for k in self._unfit_said if k[0] == chat_id]:
            del self._unfit_said[key]

    def _job_holds(self, chat_id: int) -> bool:
        """Whether a backfill job is reading this chat right now (3.12)."""
        job = self._backfill
        return (job is not None and job.get("chat_id") == chat_id
                and self.active_job_id() is not None)

    def _track(self, task: "asyncio.Task") -> None:
        """Register a task `quiesce()` and `stop()` must wait for.

        Registered BEFORE it can do anything, which is the whole point: the
        window this closes is the one between "a thread has started touching
        the database" and "somebody has recorded that fact".
        """
        self._inflight.add(task)
        task.add_done_callback(self._inflight.discard)

    def count_backlog(self) -> dict:
        """Measure what has not been read. Spends nothing, starts nothing.

        Called from the unlock bootstrap, on the bootstrap's own thread. It
        touches no asyncio object - a plain attribute, not the queue - so it
        is safe there; `offer()` would not be, and offering here is the thing
        this design refuses anyway.

        Never raises. A count that fails must not take an unlock down with
        it, and a stale zero reads as "nothing to report", which is the
        harmless direction.
        """
        from database import get_db

        try:
            with get_db() as con:
                self.backlog = notebook.unread_backlog(con)
        except Exception as exc:                              # noqa: BLE001
            # The CLASS, not the traceback. This file is in the "must stay
            # clean" list: an exception's own message can carry a filesystem
            # path, and a path carries the Windows account name.
            logger.warning("Notebook: could not count the unread backlog (%s).",
                           type(exc).__name__)
        return dict(self.backlog)

    # -- backfill jobs -----------------------------------------------------
    #
    # A backfill job reads a chat's unread history, or a stretch of it, one
    # unit per turn until there is nothing left or somebody says stop. It
    # holds the `_sweeping` lock for its whole run, runs each unit as a
    # tracked and shielded task, and reaches the provider through the same
    # `_handle` door as the ordinary reader, so the same claim, running row
    # and settle. What is its own is a row in `notebook_backfill_jobs`
    # (progress, and the handle "undo this run" needs), a lower ceiling for
    # the claim (the live reader keeps NOTEBOOK_LIVE_RESERVE of the day), and
    # the three refusals a job adds. Only a person starts one, from the
    # reading strip after its preview: the one-press route that read without
    # a preview is gone (decision 69 a), and so is the job the worker used to
    # start on its own for a chat with no notes (decisions 30 a and 31 a).

    def active_job_id(self) -> int | None:
        """The id of the job this process is driving right now, or None.

        THE ROW, NOT THE TASK, and that order is the whole point. `backfill`
        commits the row on a worker thread, so it is visible to a status
        poll while the coroutine that asked for it is still suspended and
        the loop task does not exist yet. Reading the task first answered
        None for that window, and the poll's own
        `settle_orphaned_jobs(keep=None)` stamped the new row `interrupted`.
        Nothing moves a row back out of that state - `advance_job` wants
        `planned`, `stop_job` wants `planned` or `running` - so the job ran
        to the end billing against a row that said it had stopped, and the
        undo route, seeing a terminal state, skipped its 409 and deleted
        notes while the loop was still writing them. `_backfill` is claimed
        inside the creating transaction, so it is set before any reader can
        see the row at all.
        """
        if self._backfill is None:
            return None
        task = self._backfill_task
        if task is not None and task.done():
            return None
        return self._backfill["id"]

    async def wait_for_backfill(self) -> None:
        """Wait for the running job's loop to finish. Tests and nothing else:
        the routes answer at once and the panel polls."""
        task = self._backfill_task
        if task is not None and not task.done():
            await asyncio.shield(task)

    def _plan_backfill(self, chat_id: int, scope: str,
                       from_id: int | None, to_id: int | None) -> dict:
        """The preview: what a job would read, cost and be blocked by.

        Spends nothing and starts nothing. On a worker thread: everything
        it reads is in the vault. Raises `chat_not_found` for a chat that
        is not there and `notebook_range_invalid` for a range that is not
        inside it; every other refusal is a token in `blocked_by`, because
        the preview's whole purpose is to say those BEFORE the button is
        pressed.
        """
        from database import get_db, get_setting_con

        with get_db() as con:
            # Orphans first, so a job the last process died under does not
            # keep this chat reporting "running" and refusing a new job.
            stale = notebook.orphaned_job_ids(con)
            if stale:
                notebook.settle_orphaned_jobs(
                    con, stale, keep=self.active_job_id())
            chat = con.execute(
                "SELECT ch.id, ch.model_id, c.id AS character_id "
                "FROM chats ch LEFT JOIN characters c "
                "ON c.id = ch.character_id WHERE ch.id = ?",
                (chat_id,)).fetchone()
            if chat is None:
                raise notebook.NotebookError("chat_not_found")
            blocked: list[str] = []
            if chat["character_id"] is None:
                blocked.append("no_character")
            chat_model = (chat["model_id"] or "").strip()
            if not chat_model:
                blocked.append("no_chat_model")
            stored = get_setting_con(con, config.SETTING_NOTEBOOK_MODEL) or None
            bound = stored == notebook_extract.CHAT_MODEL_BINDING
            model_id = (chat_model or None) if bound else stored
            if not model_id:
                blocked.append("no_extract_model")
            else:
                # The same question the turn gate asks, with the same two
                # answers: a list that could not be fetched is not blamed on
                # a model nobody examined (`_unfit_reason`).
                unfit = _unfit_reason(model_id, bound=bound)
                if unfit is not None:
                    blocked.append(unfit)

            lo, hi = notebook.message_span(con, chat_id)
            total = con.execute(
                "SELECT COUNT(*) FROM messages WHERE chat_id = ? "
                "AND active = 1", (chat_id,)).fetchone()[0]
            if scope == "range":
                if (from_id is None or to_id is None or from_id > to_id
                        or lo is None or from_id < lo or to_id > hi):
                    raise notebook.NotebookError("notebook_range_invalid")
                unread, chars = con.execute(
                    "SELECT COUNT(*), COALESCE(SUM(LENGTH(content)), 0) "
                    "FROM messages WHERE chat_id = ? AND active = 1 "
                    "AND id BETWEEN ? AND ?",
                    (chat_id, from_id, to_id)).fetchone()
            else:
                unread = notebook.unread_count(con, chat_id)
                chars = con.execute(
                    "SELECT COALESCE(SUM(LENGTH(m.content)), 0) FROM messages m "
                    "WHERE m.chat_id = ? AND m.active = 1 AND NOT EXISTS ("
                    "  SELECT 1 FROM notebook_extractions e "
                    "  WHERE e.chat_id = m.chat_id "
                    "    AND (e.status = 'done' "
                    "         OR (e.status = 'failed' AND e.error_type = ?)) "
                    "    AND m.id BETWEEN e.from_message_id "
                    "        AND e.to_message_id)",
                    (chat_id, notebook.ABANDONED_IN_FLIGHT)).fetchone()[0]
                if unread == 0:
                    blocked.append("nothing_unread")
            card_len = con.execute(
                "SELECT COALESCE(LENGTH(c.description), 0) FROM chats ch "
                "JOIN characters c ON c.id = ch.character_id WHERE ch.id = ?",
                (chat_id,)).fetchone()
            existing_len = con.execute(
                "SELECT COALESCE(SUM(LENGTH(text) + 8), 0) FROM notebook_entries "
                "WHERE chat_id = ? AND retired_at IS NULL",
                (chat_id,)).fetchone()[0]
            spent = notebook.spend_today(con)["calls"]
            language = get_setting_con(
                con, config.SETTING_NOTEBOOK_PROMPT_LANG) or "en"

        batch = max(1, self.batch_size_for(chat_id))
        chunks = 0 if unread == 0 else -(-unread // batch)
        calls = min(1, chunks) if scope == "next" else chunks
        cap = _backfill_cap()
        # THE JOB'S OWN CEILING, not the day's. `fits_today` answers the
        # question the run route will ask, and the run route claims
        # against `cap`, so the two cannot disagree.
        fits = spent + calls <= cap
        price = _prompt_price(model_id) if model_id else None
        reply_price = _completion_price(model_id) if model_id else None
        # What the job costs: every call pays for what it SENDS and for what
        # it gets BACK (3.17). This used to price the first half only, and
        # only part of that, so the number the reader agreed to before
        # pressing Start was lower than the bill.
        #
        # Sent, per call: the material (bounded per call by the turns
        # ceiling), the card, the notes already kept, the instructions, and
        # the fixed frame every request carries whatever is in it - the
        # fence rule and the four fenced section labels, measured off the
        # real builder as `notebook_extract.SCAFFOLD_CHARS`. Tokens are
        # estimated the way the composer estimates them, from the same
        # constant.
        #
        # Received, per call: the reply allowance, `MAX_TOKENS`, at the
        # model's completion price. That is the most a reply can bill, so
        # the estimate is an upper bound on that half rather than a guess
        # at it, which is the safe direction for a number a person agrees
        # to.
        #
        # Never a fake zero: an unknown price on either half is no number
        # at all, because a total missing one of its halves is the same
        # understatement this change is about.
        fixed = (min(card_len[0] if card_len else 0, config.NOTEBOOK_CARD_MAX_CHARS)
                 + min(existing_len, config.NOTEBOOK_EXISTING_MAX_CHARS)
                 + len(notebook_extract.system_prompt(language))
                 + notebook_extract.SCAFFOLD_CHARS)
        material = min(chars, calls * config.NOTEBOOK_TURNS_MAX_CHARS)
        est_tokens = (material + calls * fixed) / config.CHARS_PER_TOKEN_ESTIMATE
        reply_tokens = calls * notebook_extract.MAX_TOKENS
        cost = (None if price is None or reply_price is None
                else round((price * est_tokens + reply_price * reply_tokens)
                           / 1_000_000, 6))
        return {
            "ready": not blocked,
            "blocked_by": blocked,
            "scope": scope,
            "messages_total": total,
            "messages_unread": unread,
            "chunks": chunks,
            "calls_planned": calls,
            # TWO NAMES BECAUSE THEY ARE TWO NUMBERS. `daily_cap` is the
            # day's ceiling and means the same thing here as it does on
            # `GET /notebook/worker` - one constant, one meaning, and the
            # panel renders both routes at once, so a name that carried 50
            # on one and 60 on the other put two different sentences about
            # the same day side by side. `backfill_cap` is what a JOB may
            # claim: the day's ceiling minus the reserve the live reader
            # keeps. Everything this route says about fitting is about
            # `backfill_cap`.
            "daily_cap": config.NOTEBOOK_DAILY_CALL_CAP,
            "backfill_cap": cap,
            "live_reserve": config.NOTEBOOK_LIVE_RESERVE,
            "calls_spent_today": spent,
            "fits_today": fits,
            "prompt_price": price,
            "cost_estimate": cost,
            "model_id": model_id,
            "model_is_chat_bind": bound,
        }

    async def plan_backfill(self, chat_id: int, scope: str,
                            from_id: int | None = None,
                            to_id: int | None = None) -> dict:
        if scope not in notebook.JOB_SCOPES:
            raise notebook.NotebookError("notebook_range_invalid")
        # THE PREVIEW LEARNS TOO, and `backfill` reaches this door before
        # it writes a job row, so the run route learns with it. Without it
        # the panel would print `model_not_extractable` on a cold process
        # and the only thing that could clear it is a background turn -
        # the reader would be looking at a blocked button with no way to
        # unblock it. The model is resolved here only to say what to ask
        # about; `_plan_backfill` resolves its own for the answer.
        model_id = await anyio.to_thread.run_sync(
            partial(notebook_extract.extract_model, chat_id))
        if model_id:
            bound = await anyio.to_thread.run_sync(
                notebook_extract.extract_model_setting
            ) == notebook_extract.CHAT_MODEL_BINDING
            await _learn_about(model_id, bound=bound)
        return await anyio.to_thread.run_sync(
            partial(self._plan_backfill, chat_id, scope, from_id, to_id))

    async def backfill(self, chat_id: int, scope: str,
                       from_id: int | None = None,
                       to_id: int | None = None) -> dict:
        """Start a backfill job, or say why not. Answers at once.

        `{started: True, job_id, calls_planned}` and the loop runs on as
        its own task, progress on `GET /notebook/worker`; or
        `{started: False, reason}` with one of SWEEP_REFUSALS. Raises
        `chat_not_found` and `notebook_range_invalid` like the preview.

        Refused BEFORE a job row is written, every time: a refused job has
        nothing to undo, and a `planned` row for it would be an undo target
        with nothing behind it.
        """
        if scope not in notebook.JOB_SCOPES:
            raise notebook.NotebookError("notebook_range_invalid")
        if self._sweeping:
            return {"started": False, "reason": "already_running"}
        if chat_id in self._active_chats:
            return {"started": False, "reason": "already_in_flight"}
        now = asyncio.get_running_loop().time()
        cb = self._chat_breaker(chat_id, now)
        if cb is not None and not cb.allows(now):
            self.refused_by_chat_breaker += 1
            return {"started": False,
                    "reason": "chat_stopped" if cb.stopped else "chat_paused"}

        # CLAIMED HERE, BEFORE THE FIRST AWAIT, and handed back below if
        # this turns into a refusal.
        #
        # `self._sweeping` used to be set after the preview and after the
        # row was written - two awaits below the check at the top of this
        # method - and asyncio hands another coroutine the loop at each of
        # them. Two presses landing together (a double click, or two
        # panels) therefore BOTH read False and both started a loop. The damage is
        # not just a double spend: the second loop's done callback clears
        # `_backfill`, `_backfill_task` and this flag while the first is
        # still billing, so the first becomes a job `stop_backfill` answers
        # `nothing_running` for, that `active_job_id` cannot name, and that
        # a third press can stack on top of. The window is those two
        # awaits; the claim belongs before them, which is what every other
        # reader of this flag already assumes has happened.
        self._sweeping = True
        # AND THE STOP FLAG WITH IT, before the first await.
        #
        # This line used to sit after `_create` came back. `_create` claims
        # `self._backfill` on its own thread, and from that instant
        # `stop_backfill` passes its guard and can write True - so a stop
        # landing anywhere in the handoff between the commit and this
        # coroutine being rescheduled was written back to False here. The
        # route had already answered `{"stopped": true}` and the panel had
        # already said "stopping after this one", while the loop went on to
        # bill every unit it had planned. Nothing raised, no counter moved,
        # nothing reached a log. Cleared here, where the flag cannot have
        # been set yet because `self._backfill` is not claimed until
        # `_create` runs, there is no window at all.
        self._backfill_stop = False
        started = False
        try:
            preview = await self.plan_backfill(chat_id, scope, from_id, to_id)
            if preview["blocked_by"]:
                return {"started": False,
                        "reason": _refusal_for(preview["blocked_by"])}
            if preview["calls_spent_today"] >= preview["backfill_cap"]:
                # The job's ceiling is already spent. Said with the same
                # word the loop uses when it gets there mid-run, and the
                # preview said `fits_today: false` before the press.
                return {"started": False, "reason": "daily_cap_reached"}

            def _create() -> dict:
                from database import get_db
                with get_db() as con:
                    con.execute("BEGIN IMMEDIATE")
                    if scope == "range":
                        # The reading record over the chosen stretch goes,
                        # so the planner will read it again. See
                        # notebook.forget_readings_in_range.
                        notebook.forget_readings_in_range(
                            con, chat_id, from_id, to_id)
                    row = notebook.create_job(
                        con, chat_id, scope, from_id=from_id, to_id=to_id,
                        planned_calls=preview["calls_planned"])
                    # WHERE THIS JOB STOPS, fixed now (3.13). A job used to
                    # read "until nothing is unread", and the chat does not
                    # stand still while it reads: every message written
                    # during the job was read by the job too, each unit
                    # paying a whole call for the one or two new messages
                    # it found. The job reads the chat as it was when it
                    # was started; what arrives after is the ordinary
                    # reader's, once the job is done (3.12). A range job
                    # keeps its own end.
                    if scope != "range":
                        row = dict(row)
                        row["read_until"] = notebook.message_span(
                            con, chat_id)[1]
                    # INSIDE THE TRANSACTION THAT WRITES IT. The commit is
                    # what makes this row visible to the status poll, and
                    # the poll asks `active_job_id()` who is driving it -
                    # so that answer has to exist before the commit does,
                    # not after this coroutine is rescheduled. Claimed
                    # here, the two are ordered by SQLite's write lock.
                    self._backfill = dict(row)
                    return row

            job = await anyio.to_thread.run_sync(_create)
            task = asyncio.create_task(self._run_backfill(dict(job)),
                                       name="notebook-backfill")
            self._backfill_task = task
            task.add_done_callback(self._backfill_finished)
            started = True
            logger.info("Notebook backfill job started: scope=%s chat_id=%d",
                        scope, chat_id)
            return {"started": True, "job_id": job["id"],
                    "calls_planned": preview["calls_planned"]}
        finally:
            # A refusal, or a raise from the preview (`chat_not_found`,
            # `notebook_range_invalid`), leaves nothing running, so the
            # claim goes back. Once the loop task exists the flag belongs
            # to `_backfill_finished` and this must not touch it.
            if not started:
                self._sweeping = False
                self._backfill = None

    def _backfill_finished(self, task: "asyncio.Task") -> None:
        if self._backfill_task is task:
            self._backfill_task = None
            self._backfill = None
            self._sweeping = False

    def _backfill_cursor(self, job: dict) -> int | None:
        """Where the next unit starts, or None when the job is complete.

        On a worker thread. For `all` and `next` it is the id before the
        first unread message, and None once nothing unread is left at or
        below `read_until` - the message that was newest when the job
        started (3.13). For `range` it
        is the top of what this job has read inside its stretch, or the
        message before the stretch when it has read nothing yet, and None
        once nothing is left in the stretch for a unit to plan.

        COMPLETE IS NOT `top >= to_message_id`. The end of a range is an
        id a reader typed, and an id is not a message: one that was edited
        away or deleted leaves its id behind, inside the span a range is
        checked against and carried by no row the planner will ever
        select. A job ending on such an id reads everything it can,
        records the last ACTIVE id below it, and never reaches the end it
        was given - so the loop saw a cursor that had not moved and closed
        the row `failed` / `refused` on a read that had in fact finished
        and billed exactly what it planned. `JOB_STATES` says which word
        that is: `done` when nothing unread is left inside what the job
        reads (up to `read_until` for `all` and `next`).

        So the question is the planner's own - is there an ACTIVE message
        above the cursor and inside the stretch? - asked with the planner's
        own predicate (`active = 1 AND id > ? AND id <= ?`, see
        `_plan_work`), so the two cannot disagree about what is left to
        read. The old condition is contained in this one: a `top` at or
        past the end leaves nothing above the cursor either way.
        """
        from database import get_db
        with get_db() as con:
            if job["scope"] != "range":
                cursor = notebook.first_unread_message(con, job["chat_id"])
                bound = job.get("read_until")
                if cursor is None or bound is None:
                    return cursor
                # The same question as the range arm below, with the
                # job's own end: is there an ACTIVE message above the
                # cursor and at or below the message that was newest when
                # the job started? The first unread message may sit above
                # that end - written during the job - and it is not the
                # job's to read (3.13).
                more = con.execute(
                    "SELECT 1 FROM messages WHERE chat_id = ? AND active = 1 "
                    "AND id > ? AND id <= ? LIMIT 1",
                    (job["chat_id"], cursor, bound)).fetchone()
                return cursor if more is not None else None
            top = con.execute(
                "SELECT MAX(to_message_id) FROM notebook_extractions "
                "WHERE chat_id = ? AND from_message_id >= ? "
                "AND to_message_id <= ? AND (status = 'done' "
                "     OR (status = 'failed' AND error_type = ?))",
                (job["chat_id"], job["from_message_id"], job["to_message_id"],
                 notebook.ABANDONED_IN_FLIGHT)).fetchone()[0]
            cursor = job["from_message_id"] - 1 if top is None else top
            more = con.execute(
                "SELECT 1 FROM messages WHERE chat_id = ? AND active = 1 "
                "AND id > ? AND id <= ? LIMIT 1",
                (job["chat_id"], cursor, job["to_message_id"])).fetchone()
            return cursor if more is not None else None

    async def _run_backfill(self, job: dict) -> None:
        """The loop. One unit per turn through `_handle`, until it reaches the
        last message that was there when the job started (3.13), or a stop,
        or a reason to stop."""
        chat_id, job_id, scope = job["chat_id"], job["id"], job["scope"]
        loop = asyncio.get_running_loop()
        state, why = "failed", None
        try:
            await anyio.to_thread.run_sync(
                partial(_job_write, job_id, running=True))
            while True:
                # THE STOP FLAG IS READ AT THE TOP OF EVERY TURN. The unit
                # in flight is never cancelled - it is paid for - but the
                # next one does not start.
                if self._backfill_stop:
                    state, why = "stopped", None
                    break
                # The chat's own breaker, re-asked every turn: a chat that
                # tripped mid-job stops the job, and says which way.
                now = loop.time()
                cb = self._chat_breaker(chat_id, now)
                if cb is not None and not cb.allows(now):
                    state = "stopped"
                    why = "chat_stopped" if cb.stopped else "chat_paused"
                    break
                # THE CEILING IS RE-ASKED EVERY TURN, before planning, which
                # decrypts the batch. The live reader may have spent
                # between two units; the question is about now.
                cap = _backfill_cap()
                spent = await anyio.to_thread.run_sync(_calls_today)
                if spent >= cap:
                    state, why = "stopped", "daily_cap_reached"
                    break
                cursor = await anyio.to_thread.run_sync(
                    partial(self._backfill_cursor, job))
                if cursor is None:
                    state = "done"
                    break
                # A TRACKED, SHIELDED TASK: `quiesce()` cancels THIS loop
                # and then waits for the unit, so a call already paid for
                # lands before the key is cleared. Removing either half
                # loses a billed reply to the next vault lock.
                unit = asyncio.create_task(self._handle(
                    chat_id, min_new=1, after_id=cursor,
                    until_id=(job["to_message_id"] if scope == "range"
                              else job.get("read_until")),
                    reread=(scope == "range"), cap=cap, job_id=job_id))
                self._track(unit)
                outcome = await asyncio.shield(unit)
                after = await anyio.to_thread.run_sync(
                    partial(self._backfill_cursor, job))
                if after is not None and after <= cursor:
                    # Nothing moved. Whatever `_handle` said is why, and
                    # going round again would ask the same question of the
                    # same range.
                    if outcome == "notebook_daily_cap_reached":
                        state, why = "stopped", "daily_cap_reached"
                    elif outcome in ("proxy_gate", "api_key_not_set"):
                        state, why = "stopped", outcome
                    elif outcome in ("model_not_extractable",
                                     "model_list_unavailable"):
                        # The gate's two skip reasons are not in the job's
                        # closed `JOB_LAST_ERRORS`, and the panel prints a
                        # token it has no words for in brackets. The run
                        # route refuses an unfit model up front by name, so
                        # this is a model that turned unfit mid-job.
                        state, why = "failed", "refused"
                    else:
                        state, why = "failed", outcome or "refused"
                    break
                if scope == "next":
                    state = "done"
                    break
        except asyncio.CancelledError:
            # The vault is locking (`quiesce`) or the app is stopping. The
            # unit keeps running shielded; the job's row records why it
            # stopped, through a tracked task so the stand-down waits for
            # the write the same way it waits for the unit.
            self._track(loop.create_task(anyio.to_thread.run_sync(
                partial(_job_write, job_id, state="stopped",
                        last_error="vault_locked"))))
            raise
        except Exception as exc:
            # ONE TOKEN, NOT THE CLASS NAME. `last_error` is read by the
            # panel, which prints what it does not recognise in brackets -
            # so `KeyError` reaching a person in brackets is the shape
            # section 5.0 was written to stop. The class name still goes to
            # the log, where it is useful and where no reader meets it.
            state, why = "failed", "failed"
            logger.warning("Notebook backfill job ended in %s.",
                           type(exc).__name__)
        await anyio.to_thread.run_sync(
            partial(_job_write, job_id, state=state, last_error=why))
        logger.info("Notebook backfill job ended: %s%s", state,
                    f" ({why})" if why else "")

    async def stop_backfill(self, chat_id: int) -> dict:
        """Ask the running job on this chat to stop after its current unit.

        `{stopped: True, job_id, calls_spent}` or `{stopped: False,
        reason: "nothing_running"}`, which is a refusal and not an error.
        """
        job = self._backfill
        if (job is None or job["chat_id"] != chat_id
                or self.active_job_id() is None):
            return {"stopped": False, "reason": "nothing_running"}
        self._backfill_stop = True

        def _spent() -> int:
            from database import get_db
            with get_db() as con:
                row = notebook.job_row(con, job["id"])
            return int(row["spent_calls"]) if row else 0

        return {"stopped": True, "job_id": job["id"],
                "calls_spent": await anyio.to_thread.run_sync(_spent)}

    def backfill_block(self, con, chat_id: int | None) -> dict | None:
        """The `backfill` block of the status answer, on the route's
        connection.

        This chat's newest job when a chat was named, the one in flight
        otherwise, None when there is none. Column names are mapped to the
        wire names here and nowhere else.
        """
        # THE SELECT BEFORE THE DRIVER, and both before `active` is used
        # for anything else: see `orphaned_job_ids`. Nothing is written at
        # all on the ordinary poll, which is the common case by far - this
        # runs on every progress tick.
        stale = notebook.orphaned_job_ids(con)
        active = self.active_job_id()
        if stale:
            notebook.settle_orphaned_jobs(con, stale, keep=active)
        if chat_id is not None:
            row = notebook.job_for_chat(con, chat_id)
        else:
            row = notebook.job_row(con, active) if active is not None else None
        if row is None:
            return None
        if row["scope"] == "range":
            left = (0 if row["state"] == "done" else notebook.unread_count(
                con, row["chat_id"], row["from_message_id"],
                row["to_message_id"]))
        else:
            left = notebook.unread_count(con, row["chat_id"])
        return {
            "job_id": row["id"],
            "chat_id": row["chat_id"],
            "state": row["state"],
            "scope": row["scope"],
            "calls_planned": row["planned_calls"],
            "calls_spent": row["spent_calls"],
            "notes_written": row["notes_written"],
            "messages_left": left,
            "last_error": row["last_error"],
        }

    def status(self, now: float | None = None, chat_id: int | None = None,
               backfill: dict | None = None) -> dict:
        """What the panel reads.

        `backfill` is the job block `backfill_block` built on the route's
        own connection, or None; it is passed in because this method runs
        on the loop with no database in reach, and the block needs the job
        row. It rides on the same answer rather than on a route of its own
        because `main._IDLE_EXEMPT` matches paths exactly, and a progress
        poll that reset the idle clock would keep the vault open for as
        long as a job ran.

        `now` so the breaker is asked about the present rather than about
        whenever the loop last dequeued something. Defaulted from the running
        loop when there is one - the status route is async, so there always
        is in production - and left as None only where no loop exists, which
        is a handful of synchronous tests.

        `chat_id` adds the `chat` block and changes NOTHING else. Every key
        outside it keeps meaning the global thing it has always meant: a
        reader looking at a broken conversation must not be told the whole
        feature has stopped, and a panel whose top-level sentence changed
        with the selected chat would say exactly that.
        """
        if now is None:
            try:
                now = asyncio.get_running_loop().time()
            except RuntimeError:
                now = None
        return {
            "state": self.breaker.state_at(now),
            "failures": self.breaker.failures,
            "total_failures": self.breaker.total_failures,
            "queued": self.queue.qsize() if self.queue is not None else 0,
            # The total, kept because the client requires this key. The two
            # keys after it are the same number taken apart: an overflow is
            # the worker falling behind, an offer while it was down is an
            # ordinary vault lock, and reporting only the sum let the second
            # answer for the first.
            "dropped_offers": self.dropped_offers,
            "queue_overflows": self.queue_overflows,
            "offers_while_down": self.offers_while_down,
            "refused_by_breaker": self.refused_by_breaker,
            # Counted apart from the line above. Folded together, "the
            # provider is down" and "one conversation is broken" read as the
            # same number, and only one of them means the rest are fine.
            "refused_by_chat_breaker": self.refused_by_chat_breaker,
            "tripped_chats": sum(1 for b in self.chat_breakers.values()
                                 if b.state_at(now) != "closed"),
            # 3: a dead loop used to report "closed" with a growing queue
            # forever. The screen and the truth diverging is the one failure
            # a status screen must not have.
            "alive": self.task is not None and not self.task.done(),
            "died": self.died,
            # Recoverable but INVISIBLE was the shape here: every one of these
            # left no row, no breaker failure and no counter, so a notebook
            # that threw on every single turn reported "Running. 0 runs".
            "unhandled": self.unhandled,
            "abandoned_preambles": self.abandoned_preambles,
            "last_error": self.last_error,
            "runs": self.runs,
            # Runs that produced NOTHING, kept apart from `runs` rather than
            # folded into it: "it ran" and "it ran and landed something" are
            # different claims and only one of them is what a reader means by
            # a working notebook.
            "settled_empty": self.settled_empty,
            # So the panel can leave the button disabled while it runs
            # rather than discovering the refusal by pressing it.
            "sweeping": self._sweeping,
            "refused_in_flight": self.refused_in_flight,
            # Reported, not acted on. A chat whose history was never read is
            # invisible otherwise: the worker only moves forward and says
            # nothing about what it stepped over.
            "backlog": dict(self.backlog),
            # Why suggestions were refused, by reason. Empty is the healthy
            # answer, and an empty dict says that rather than saying nothing.
            "dropped": dict(self.dropped),
            "batch_size": self.batch_size,
            # Reported beside the batch because they are one rule now, and
            # a panel showing one without the other is where the belief that
            # they were the same number came from.
            "trigger_threshold": self.trigger_threshold,
            # None unless somebody asked about a chat. Present either way:
            # a key that appears and disappears is a key every client has to
            # guess at, and zod strips what it was not told about.
            "chat": self._chat_status(chat_id, now),
            # The backfill job: this chat's newest when a chat was asked
            # about, the one in flight otherwise, null when there is none.
            # Always present, for the same reason as `chat`.
            "backfill": backfill,
        }

    def _chat_status(self, chat_id: int | None, now: float | None) -> dict | None:
        """The selected chat's own breaker, or None.

        None both when nothing is selected and when the selected chat has
        never failed - a healthy chat holds no record, and "no record" is
        exactly "nothing to report".
        """
        if chat_id is None:
            return None
        cb = self.chat_breakers.get(chat_id)
        if cb is None:
            return None
        return {
            "state": cb.state_at(now),
            "failures": cb.failures,
            "total_failures": cb.total_failures,
            "batch_size": self.batch_size_for(chat_id),
            "trigger_threshold": self.trigger_threshold_for(chat_id),
        }


#: The one instance. Module-level so the completion path can reach it without
#: threading a reference through six call sites.
worker = Worker()


def start() -> asyncio.Task:
    """Start the loop and KEEP the reference.

    The event loop holds only a WEAK reference to a task. A worker started and
    forgotten is collected at an arbitrary moment, which in practice means it
    works in development and stops in production - and the failure looks
    exactly like the feature was never wired up at all.
    """
    if worker.task is not None and not worker.task.done():
        # Started twice, the first task is referenced only weakly by the loop
        # and is collected mid-extraction; worse, its `finally` calls
        # task_done() on the NEW queue for an item taken from the old one.
        return worker.task
    # The queue is built HERE so it binds to the loop that will drain it.
    #
    # And anything left over from a PREVIOUS loop goes with it. The queue was
    # already rebuilt for this reason; `_inflight` and `settling` are the same
    # kind of state and were not, so a task pending when the last loop closed
    # stayed in the set and poisoned the next stand-down's `gather`.
    here = asyncio.get_event_loop()
    worker._inflight = {t for t in worker._inflight if t.get_loop() is here}
    if worker.settling is not None and worker.settling.get_loop() is not here:
        worker.settling = None
    # Same rule for the backfill loop: a job task from a closed loop is not
    # running anything, and leaving it in place would keep `_sweeping`
    # true for the life of the process.
    bf = worker._backfill_task
    if bf is not None and bf.get_loop() is not here:
        worker._backfill_task = None
        worker._backfill = None
        worker._sweeping = False
    # And the ordinary reader's unit, for the same reason.
    rd = worker._reading
    if rd is not None and rd.get_loop() is not here:
        worker._reading = None
    worker.queue = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
    worker.task = asyncio.create_task(worker.run(), name="notebook-worker")
    worker.task.add_done_callback(_note_death)
    return worker.task


def _note_death(task: asyncio.Task) -> None:
    """A loop that ends is the one thing the status screen must not hide.

    Without this, a BaseException that is not CancelledError - a task group's
    ExceptionGroup, SystemExit - ended the loop permanently while `status()`
    went on reporting a healthy worker with a growing queue. The screen and
    the truth diverging is the failure a status screen exists to prevent.
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        worker.died = type(exc).__name__
        logger.warning("Notebook worker stopped: %s", worker.died)


def _grace_left(deadline: float | None) -> float:
    """How much of the stand-down's up to five seconds is left.

    ONE DEADLINE FOR THE WHOLE STAND-DOWN, not one per step. `quiesce()`
    does three bounded things in a row - stand the reading job down, wait
    out the worker loop's cancellation, wait for an already-paid reply to
    land - and each used to carry its own `SETTLE_GRACE_S`. Three graces in
    a row is fifteen seconds, and SECURITY.md and README both say the wait
    is five. The document is the promise; this is how it stays true however
    many steps the stand-down grows. `None` means no deadline was set
    (`stop()` at shutdown), and then each step keeps its own full grace.
    """
    if deadline is None:
        return SETTLE_GRACE_S
    return max(0.0, deadline - asyncio.get_running_loop().time())


async def _await_settle(deadline: float | None = None) -> None:
    """Wait for every in-flight database step to finish, bounded.

    It used to wait for ONE thing: the tail that lands an already-paid reply.
    That tail is registered only after the provider answers, so for the whole
    preamble - planning, the daily claim, the running row, two of which WRITE
    - there was nothing registered and this returned instantly. The caller's
    next line clears the vault key.

    Shared by `quiesce()` and `stop()`. `asyncio.shield` here for the same
    reason `_handle` shields the task in the first place: if the CALLER of
    this function is itself cancelled (a lock request torn down mid-flight,
    a shutdown that does not wait), that must not re-open the exact race this
    exists to close by cancelling the shielded settle task out from under us
    a second time.
    """
    # THIS LOOP'S tasks only, and the stale ones are dropped on the way past.
    #
    # `worker` is a module singleton; a task still pending when its loop
    # closed stays in the set forever, and one such member makes
    # `asyncio.gather` raise synchronously below. The broad handler then
    # logged one line and returned - so a real, already-paid settle in the
    # same snapshot was never waited for. The message said the settle failed;
    # nobody had waited.
    #
    # Filtering rather than clearing: a live task from this loop must not be
    # dropped just because a dead one is keeping it company.
    here = asyncio.get_running_loop()
    stale = {t for t in worker._inflight if t.get_loop() is not here}
    worker._inflight -= stale
    if stale:
        logger.debug("Notebook: dropped %d task(s) from a closed loop.",
                     len(stale))
    pending = {t for t in worker._inflight if not t.done()}
    settle = worker.settling
    if settle is not None and settle.get_loop() is not here:
        settle = None
    if settle is not None and not settle.done():
        # Belt and braces: the pointer is what every existing test reads, and
        # the set is what cannot be overwritten. Waiting for the union costs
        # nothing when they are the same task, which is the ordinary case.
        pending.add(settle)
    if not pending:
        # NOTHING is in flight, so this returns AT ONCE. Not a formality: a
        # version that always waited out the grace period would put five
        # seconds on every vault lock, including the idle timer's, and the
        # test that pins `elapsed < 1.0` exists because that is a tempting
        # and wrong way to write this.
        return
    grace = _grace_left(deadline)
    try:
        await asyncio.wait_for(
            asyncio.shield(asyncio.gather(*pending, return_exceptions=True)),
            timeout=grace)
    except asyncio.TimeoutError:
        logger.warning(
            "Notebook: %d in-flight extraction step(s) did not finish within "
            "%.0fs; proceeding without them.", len(pending), grace)
    except asyncio.CancelledError:
        # OURS, and it travels. The `shield` above already protects the
        # gather - those tasks keep running whatever happens here - so
        # honouring a cancellation aimed at this coroutine costs the settle
        # nothing and is what the caller asked for. Swallowing it made every
        # caller of this function unbounded.
        raise
    except Exception:
        logger.warning("Notebook: the in-flight extraction did not settle "
                       "cleanly.")


async def quiesce() -> None:
    """Stand down for a vault lock: cancel what is in flight, drop the queue.

    Called from `lock_vault_now`, which is the single funnel both the button
    and the idle watchdog come through, IMMEDIATELY before the vault key is
    cleared. The loop is restarted before returning, so this is a pause
    rather than a shutdown - what it must NOT leave behind is an in-flight
    request carrying decrypted text, a backlog of offers that will each wake
    into a locked vault and be counted as an error, or (Defect 1) a reply
    that was already paid for and received, discarded mid-write because the
    key was cleared out from under the thread writing it.
    """
    # ONE DEADLINE, OPENED HERE, SPENT BY ALL THREE STEPS BELOW.
    #
    # `lock_vault_now` clears the vault key on the line after this function
    # returns, so everything between here and that line is time the key
    # spends in memory with decrypted material beside it. SECURITY.md and
    # README both say that wait is up to five seconds. Three steps each
    # carrying their own five made the real bound fifteen, and one of them - the
    # worker loop's cancellation - carried no bound at all and could hold
    # the lock open forever. The deadline is what makes the sentence true.
    deadline = asyncio.get_running_loop().time() + SETTLE_GRACE_S
    try:
        await _stand_down(deadline)
    except asyncio.CancelledError:
        # THE LOCK REQUEST ITSELF WAS TORN DOWN MID-GRACE. The cancel is
        # ours and it travels - a caller bounding this function must get its
        # answer - but not before the notebook is put back on its feet. The
        # loop task was already taken and cancelled, and nothing else would
        # ever start it again: the queue stayed live with nobody draining it
        # and the panel said "stopped" until the app was restarted. The open
        # read is cancelled as the end of the grace would have cancelled it,
        # so its request does not stay open either.
        rd = worker._reading
        took_the_loop = worker.task is None or worker.task.done()
        if took_the_loop and rd is not None and not rd.done():
            rd.cancel()
        _back_on_its_feet()
        raise
    _back_on_its_feet()


def _back_on_its_feet() -> None:
    """Drop the queue and start the loop again, unless a loop is still
    running (a stand-down cancelled before it took the loop down).

    The queue is nulled and drained only AFTER the cancellation has fully
    unwound - not before. Nulling it first left `run()`'s own
    `finally: self.queue.task_done()` reading `self.queue` as None while
    the CancelledError it was supposed to let through was still in flight,
    replacing that CancelledError with an AttributeError and reporting the
    loop as died rather than cleanly cancelled. See `stop()`'s docstring,
    which already carries this fix.
    """
    if worker.task is not None and not worker.task.done():
        return
    queue, worker.queue = worker.queue, None
    if queue is not None:
        while True:
            try:
                queue.get_nowait()
                queue.task_done()
            except asyncio.QueueEmpty:
                break
    # Back on its feet for the next unlock. A worker that stayed down after
    # the first auto-lock would look exactly like a feature that stopped
    # working for no reason.
    start()


async def _stand_down(deadline: float) -> None:
    """The three bounded steps of `quiesce()`, on its one deadline."""
    # THE BACKFILL LOOP FIRST. It is a reader-started task that would go
    # on planning the next unit against a vault about to lose its key. The
    # unit it is inside is tracked and shielded, so
    # cancelling the loop does not cancel a paid call: `_await_settle`
    # below waits for that unit, and for the loop's own stop-row write.
    await _cancel_backfill(deadline)
    task, worker.task = worker.task, None
    if task is not None and not task.done():
        task.cancel()
        # `asyncio.wait`, not `await task`.
        #
        # `await task` raises the task's own `CancelledError`, which has to
        # be ignored - it is the cancellation just requested. A bare
        # `except BaseException` around it also ignores a cancellation aimed
        # at THIS coroutine, and that made this function uncancellable: it
        # carried on to `start()` below and built a fresh worker task on a
        # loop that might be closing. Anything bounding a vault lock with
        # `asyncio.wait_for` would then wait forever, because `wait_for`
        # cancels the inner coroutine and waits for it to finish.
        #
        # `wait` returns when the task settles, however it settled, and
        # raises only when the waiter itself is cancelled. That is exactly
        # the distinction the `except` could not make.
        #
        # OUR LOOP ONLY, and this line is why the previous spelling survived.
        # `await task` raises RuntimeError at once when the task belongs to
        # another loop, and the `except BaseException` around it swallowed
        # that - so the lock carried on, by accident. `asyncio.wait` has no
        # such guard: it attaches a callback that resolves a future owned by
        # THIS loop from the OTHER loop's thread, and this loop is never
        # woken. Measured: the worker task reaches CANCELLED and the wait has
        # still not returned seventy-five seconds later. A vault lock that
        # never returns is worse than either thing the change was about.
        #
        # A foreign task has already been cancelled by the line above, and
        # its own loop will run that cancellation. There is nothing this loop
        # can usefully wait for, and `_await_settle` below filters foreign
        # tasks out of the grace period for the same reason.
        #
        # BOUNDED, like everything else between here and the cleared key.
        # This wait had no timeout: a loop task that swallows its own
        # cancellation - wedged on a thread, or on a foreign lock - held
        # the vault open for as long as it liked, one statement after the
        # bound that was added for exactly that hazard on the line above.
        # Measured before this line changed: no return in fifteen seconds.
        if task.get_loop() is asyncio.get_running_loop():
            left = _grace_left(deadline)
            done, still = await asyncio.wait({task}, timeout=left)
            if still:
                logger.warning(
                    "Notebook: the reader loop did not stand down within "
                    "%.0fs; locking without it.", left)

    # THE ORDINARY READER'S UNIT, which the cancellation above did not
    # reach: it runs shielded from the loop (decision 34 a). It gets what is
    # left of the same up to five seconds, and then it is cancelled by name.
    await _stand_down_reading(deadline)

    # Defect 1. The loop task above is cancelled and gone, but `_handle` may
    # have handed the tail of a PAID call to `self.settling` as its own
    # shielded task before it went - see `_handle`. That task is still
    # running, or about to be, entirely independent of the cancellation just
    # awaited. Waiting for it HERE, before the queue is torn down and before
    # this function returns to `lock_vault_now` (which clears the key on its
    # very next line), is what lets that write actually land instead of
    # racing a cleared key in a detached thread.
    await _await_settle(deadline)


#: How long the stand-down waits for a cancelled read to unwind. Taken out
#: of the up to five seconds, not added to them: the read is cancelled this
#: long before the deadline, so the lock still returns inside the one grace the
#: documents state. Cancelling a coroutine that awaits a request takes one
#: turn of the event loop, so this is headroom, not an estimate.
READ_CANCEL_UNWIND_S = 0.25


async def _stand_down_reading(deadline: float | None, *,
                              grace: bool = True) -> None:
    """Give the ordinary reader's open unit what is left of the up to five
    seconds, then cancel it.

    Decision 34 a. A read
    that finishes inside the grace is written, exactly like a reading job's
    unit. One that does not is CANCELLED, by name and on purpose: before
    this, `_await_settle` just stopped waiting and the request stayed open
    with the key already gone. Cancelling it closes the request, and its
    `running` row is closed out as abandoned on the next plan, so that
    stretch is not bought a second time - which is what decision 34 a
    keeps from before for the read that does not make it.

    A WAIT, NOT `wait_for`. `asyncio.wait` watches the unit without owning
    it, so nothing here can cancel it early, and nothing else is touched:
    the backfill loop, a job's unit and a paid reply's settle are all
    separate tasks that this neither waits on nor cancels.

    `grace=False` is `stop()`: the window closing cuts the read at once, as
    it always did.
    """
    unit = worker._reading
    if unit is None or unit.done():
        return
    if unit.get_loop() is not asyncio.get_running_loop():
        # Another loop's task. It can be cancelled from here but not
        # waited for; see `quiesce()` on waiting across loops.
        unit.cancel()
        return
    if grace:
        left = max(0.0, _grace_left(deadline) - READ_CANCEL_UNWIND_S)
        done, _ = await asyncio.wait({unit}, timeout=left)
        if done:
            return
        logger.info("Notebook: a read was still open when the lock's grace "
                    "ran out; it is cancelled and that stretch is not read.")
    unit.cancel()
    await asyncio.wait({unit}, timeout=READ_CANCEL_UNWIND_S)


async def _cancel_backfill(deadline: float | None = None) -> None:
    """Stop the backfill loop, if one is running on THIS loop.

    Shared by `quiesce()` and `stop()`. The loop's own cancellation handler
    writes the job's stop row through a tracked task, and the unit it was
    awaiting keeps running shielded; both are then waited for by
    `_await_settle`.

    BOUNDED BY THE SAME `SETTLE_GRACE_S` THE REST OF THE STAND-DOWN USES,
    and that is not a formality. This is the FIRST thing `quiesce()` does,
    so an open-ended wait here is an open-ended vault lock - the key sits
    in memory with decrypted material beside it for as long as this task
    takes, and `_await_settle`'s grace, which is the bound the whole design
    is written around, is never even reached. SECURITY.md says the wait is
    up to five seconds; this is where that has to be true. A loop that does
    not unwind inside the grace is left running and the lock proceeds without
    it: its job row is settled as an orphan on the next poll, which is
    exactly what that path is for.
    """
    bf = worker._backfill_task
    if bf is None or bf.done():
        return
    bf.cancel()
    if bf.get_loop() is asyncio.get_running_loop():
        grace = _grace_left(deadline)
        done, pending = await asyncio.wait({bf}, timeout=grace)
        if pending:
            logger.warning(
                "Notebook: the reading job did not stand down within %.0fs; "
                "locking without it.", grace)


async def stop() -> None:
    """Cancel the loop. Best effort by design: in the packaged exe this never
    runs at all - uvicorn is a daemon thread and the process dies with the
    window - so nothing may DEPEND on it having run."""
    await _cancel_backfill()
    task = worker.task
    if task is not None and not task.done():
        task.cancel()
        # Same reasoning as `quiesce()`, including the loop check: waiting
        # on a task another loop owns deadlocks this one outright. Fixed
        # here in the same change rather than left for the day a test
        # happens to call `stop()` across loops - which is the only reason
        # this site was not red too.
        if task.get_loop() is asyncio.get_running_loop():
            await asyncio.wait({task})
    # The ordinary reader's unit is cancelled at once here, with no grace:
    # closing the app cuts an open read exactly as it did before decision
    # 34 a, which gave the up to five seconds to the LOCK only.
    await _stand_down_reading(None, grace=False)
    # Defect 1, same fix as `quiesce()`: give an already-paid call the chance
    # to actually land before this function returns. Best-effort like the
    # rest of this path - the packaged exe never reaches here at all - but a
    # graceful dev-mode shutdown should not needlessly throw away a call that
    # was already billed and had already come back.
    await _await_settle()
    # Cleared AFTER the await, not before. Nulled first, the CancelledError
    # unwound into run()'s `finally: self.queue.task_done()` and raised
    # AttributeError from inside the finally - which REPLACES the
    # CancelledError, so the task ended as failed rather than cancelled and
    # the swallow below hid that too.
    worker.task = None
    worker.queue = None


# ── database-side helpers, all off the event loop ──────────────────────────

def _language() -> str:
    from database import get_setting
    return get_setting(config.SETTING_NOTEBOOK_PROMPT_LANG) or "en"


def _already_done(work_key: str) -> bool:
    from database import get_db
    with get_db() as con:
        return notebook.already_done(con, work_key)


def _calls_today() -> int:
    from database import get_db
    with get_db() as con:
        return notebook.spend_today(con)["calls"]


def _job_write(job_id: int, *, running: bool = False,
               state: str | None = None,
               last_error: str | None = None) -> None:
    """One write to the job row, on a worker thread. Never raises: the
    usual reason a stop row cannot be written is that the vault locked
    first, and the next process closes the job out as an orphan."""
    from database import get_db
    try:
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            if running:
                notebook.advance_job(con, job_id, running=True)
            if state is not None:
                notebook.stop_job(con, job_id, state, last_error)
    except vault_state.VaultLockedError:
        logger.info("Notebook: a backfill job's state could not be written "
                    "because the vault locked first.")
    except Exception:
        logger.warning("Notebook: a backfill job's state could not be written.")


#: What each `blocked_by` token of the preview refuses the run with. Three
#: of the six collapse into `not_ready` - the precondition gate - because
#: the panel says which one from the preview, and the refusal only has to
#: be one word a sentence exists for. `model_list_unavailable` keeps its own
#: word: it is the provider's list out of reach, not the model's fault.
_BLOCKER_REFUSALS = (
    ("no_character", "not_ready"),
    ("no_chat_model", "not_ready"),
    ("no_extract_model", "not_ready"),
    ("model_not_extractable", "model_not_extractable"),
    ("model_list_unavailable", "model_list_unavailable"),
    ("nothing_unread", "nothing_unread"),
)


def _refusal_for(blocked_by: list[str]) -> str:
    for token, reason in _BLOCKER_REFUSALS:
        if token in blocked_by:
            return reason
    return "not_ready"


#: "The caller did not read the list; read it here." A sentinel and not
#: None, because None is a real answer: the list could not be fetched.
_LIST_NOT_READ = object()


def _model_is_extractable(model_id: str, *, bound: bool,
                          listed=_LIST_NOT_READ) -> bool:
    """Whether a model can return the strict schema the reader sends.

    Answered from what this process knows, without a call: the extraction
    model list, when it has been fetched, is exactly the set of models that
    can; failing that, the catalogue's own metadata for the model says
    whether it advertises `structured_outputs` - `response_format` alone is
    not it.

    THE UNKNOWN ARM, and `bound` is the whole of it. R-5 names the risk
    about one model in particular: "Sohbet modeli kati JSON sema
    tasimayabilir. `GET /extract/models` listesi bu yuzden suzuluyor." A
    model the reader PICKED came off that filtered list, so the pick itself
    is the evidence and an unknown answer here is a cold cache rather than
    an unexamined model. A model reached through `CHAT_MODEL_BINDING` was
    chosen for conversation and vetted by nothing, and that is the one this
    unit created - so with no evidence either way it is refused, and money
    is not spent on a question nobody has answered.

    THE CATALOGUE DOES NOT ANSWER FOR A BOUND MODEL, and that arm is what
    made the locked sentence false. `get_cached_model_metadata` reads
    `_model_cache`, which the chat model picker fills; the filtered list
    lives in `_zdr_cache`, and openrouter's own comment calls them "a
    second, independent cache". So after any ordinary restart in which
    somebody chatted, the catalogue knew the model, the filtered list did
    not, and this answered off `supported_parameters` alone. That is the
    WEAKER question: the list is zero-retention AND `structured_outputs`,
    the catalogue arm is `structured_outputs` by itself, so a bound model
    the list does not carry was read while SECURITY.md said it was refused.
    For a bound model the filtered list is the only evidence now, and until
    it is known the answer is no. Nothing about a picked model changed: its
    pick is its evidence and the catalogue stays its fallback.

    `bound` is a keyword and has no default deliberately: a caller that
    forgets it is a TypeError at import-time coverage rather than a silent
    return to the arm this closed.

    The evidence is not waited for passively - `_learn_about` goes and
    gets the list before either door asks - so "unknown" here means the
    provider could not be reached at all, and nothing can be sent under
    that condition either.

    `listed` is the list as the caller already read it. `_unfit_reason`
    reads it once and hands it down, so the yes-or-no and the reason are
    answered from the same snapshot (see there).
    """
    import openrouter

    if listed is _LIST_NOT_READ:
        listed = openrouter.cached_extraction_models()
    if listed is not None:
        return any(m.get("id") == model_id for m in listed)
    if bound:
        return False
    meta = openrouter.get_cached_model_metadata(model_id)
    if meta is None:
        return True
    return "structured_outputs" in (meta.get("supported_parameters") or [])


def _unfit_reason(model_id: str, *, bound: bool) -> str | None:
    """Why the gate refuses this model, or None when it may be read.

    `_model_is_extractable` answers yes or no, and a no has two causes that
    must not be told the same way. The provider's list was fetched and does
    not carry the model: `model_not_extractable`, which is a fact about the
    model. The list could not be fetched at all and the model is bound to
    the chat: `model_list_unavailable`, which is a fact about the network
    and says nothing about the model. Telling the second as the first put a
    sentence on the Notes tab blaming a model for a connection that dropped.

    ONLY A BOUND MODEL can be refused for want of the list. A model the
    reader picked is refused with the list out of reach only when the
    catalogue says it lacks `structured_outputs`, which is the model
    examined and found unfit: that stays `model_not_extractable`. The turn
    gate, the preview's `blocked_by` and the run route all ask this one
    function, so none of them tells the two apart differently.

    ONE READ OF THE LIST. It used to be read twice, once inside
    `_model_is_extractable` and once for the reason. Another task's
    `_learn_about` can fill the cache between the two, and then a bound
    model the list carries was refused (the first read found no list) and
    blamed as `model_not_extractable` (the second found the list). Both
    answers now come from the same snapshot.
    """
    import openrouter

    listed = openrouter.cached_extraction_models()
    if _model_is_extractable(model_id, bound=bound, listed=listed):
        return None
    if bound and listed is None:
        return "model_list_unavailable"
    return "model_not_extractable"


def standing_refusal(con, chat_id: int) -> str | None:
    """`model_not_extractable` when that refusal is still true of this chat
    right now, else None. What the Background notes card says about the
    chat on screen, and nothing else (3.8).

    WHAT THE QUERY CHECKS. It takes this chat's `model_not_extractable`
    rows (`WHERE chat_id = ?`) that are newer than this chat's last read,
    the newest `done`, `failed` or `running` row of the same chat; a row
    older than a read is history, because a model was called here since.
    The refusal stands when ANY of those rows names the model this chat
    resolves to NOW. The table has no model column, and a schema change is
    not in this package, but a row's work key is
    `work_key(chat, from, to, model, language)`, so it is recomputed with
    the chat's current model and compared. A chat that switched to a
    working model matches none of them and says nothing.

    ANY, NOT THE NEWEST. The reader writes a refusal once for each chat and
    model it keeps meeting, so a chat that went nojson -> nojson2 -> nojson
    has two rows and the newest names nojson2. Reading only that one left
    the card silent while the chat's model still could not write notes.

    At most `STANDING_REFUSAL_ROWS` rows are looked at, newest first, so a
    chat that piled up refusals over many restarts costs one bounded
    query. Every language the reader can be set to is tried, so switching
    the instructions' language does not hide a refusal that still stands.
    Reads only; runs on the status route's connection plus one settings
    read for the model.
    """
    rows = con.execute(
        "SELECT work_key, from_message_id, to_message_id "
        "FROM notebook_extractions WHERE chat_id = ? AND status = 'skipped' "
        "AND skip_reason = 'model_not_extractable' AND rowid > COALESCE(("
        "SELECT MAX(rowid) FROM notebook_extractions WHERE chat_id = ? "
        "AND status IN ('done', 'failed', 'running')), 0) "
        "ORDER BY rowid DESC LIMIT ?",
        (chat_id, chat_id, STANDING_REFUSAL_ROWS)).fetchall()
    if not rows:
        return None
    model_id = notebook_extract.extract_model(chat_id)
    if not model_id:
        return None
    languages = tuple(dict.fromkeys(
        (_language(), *notebook_extract.PROMPT_LANGUAGES)))
    for key, lo, hi in rows:
        for language in languages:
            if notebook_extract.work_key(chat_id, lo, hi, model_id,
                                         language) == key:
                return "model_not_extractable"
    return None


_list_fetch_in_flight = False


async def _learn_about(model_id: str, *, bound: bool) -> None:
    """Go and get the evidence the gate needs, once, before it decides.

    `GET /extract/models` is the filtered list - zero-retention endpoints
    that advertise `structured_outputs` - and it is the only thing that can
    turn "nothing here has heard of this model" into an answer BEFORE a
    call is paid for. It costs no model tokens: the policy list is public,
    the picker already reads it, and openrouter caches it for
    `MODEL_LIST_TTL`.

    Until this existed, the list was fetched by exactly one caller, the
    settings picker, so a process that had never had that panel opened
    knew nothing about any model and the gate had nothing to decide with.
    That is the cold process, and it is the ordinary state after every
    restart rather than an edge case.

    BEST EFFORT, and quiet. A provider this cannot reach is a provider
    nothing can be sent to either, so a failure here is not a second
    refusal to explain to anybody; the gate then answers from `bound`.

    THE CATALOGUE IS NOT A REASON TO SKIP THE LIST for a bound model. The
    general model cache and the filtered list are two independent caches,
    so "the catalogue has heard of this model" said nothing about the
    question `_model_is_extractable` asks of a bound model, and skipping
    the fetch on it meant the ordinary post-restart path never asked. For
    a model the reader picked the catalogue is still evidence, and its own
    pick is more, so that skip stays where it was.

    ONCE PER PROCESS WHILE THE ANSWER IS UNKNOWN, then not again. A
    success is cached by openrouter for `MODEL_LIST_TTL` and the first
    line here reads it, so a known answer costs nothing. A FAILURE is now
    remembered for the same window (`note_extraction_list_unreachable`),
    because without that a provider that is down turned every door and
    every turn into another outbound GET: measured at two attempts per
    refused turn. The plan's rule for a refused turn is money, not
    traffic - "Sohbet modeli kati JSON sema desteklemiyorsa is **cagri
    yapmadan** reddedilir" - and no billed call is made either way; this
    keeps the free request honest as well. `invalidate_model_cache` drops
    that memory, so a key or proxy change is asked again at once.

    ONE AT A TIME. Two doors opened together would otherwise each start
    their own fetch. The second does not wait for the first: it returns
    and lets the gate decide with what is known, which for a bound model
    is a refusal that costs nothing and that the next turn clears.

    AND IT PASSES THE PROXY GATE, like every other outbound path. This is
    a request the reader never asked for, made from five doors that had no
    gate of their own: the preview, the run route and the turn-driven
    reader (the one-press sweep and the automatic first offer were two
    more, and both are gone: decisions 69 a, 30 a and 31 a). With
    `proxy_required` armed and no proxy URL stored - `proxy_missing`, the
    state every other path refuses outright - it went to the provider on a
    direct client carrying the stored key, so the key and the reader's
    real IP left the machine from a door they only pressed to see a
    preview. `GET /extract/models` fetches this same list and has called
    the gate since it was written; its comment says why. A refusal here is
    the provider being unreachable and is treated as exactly that: nothing
    is sent, the failure is remembered like any other, and a bound model
    stays unknown, which the gate turns into a refusal that costs nothing.
    Under an armed gate no call could have been made anyway, so the only
    thing given up is a reason code, and `invalidate_model_cache` runs on
    every proxy change, so fixing the proxy clears this at once rather
    than after `MODEL_LIST_TTL`.
    """
    import openrouter

    global _list_fetch_in_flight

    if openrouter.cached_extraction_models() is not None:
        return
    if not bound and openrouter.get_cached_model_metadata(model_id) is not None:
        return
    if openrouter.extraction_list_recently_unreachable():
        return
    if _list_fetch_in_flight:
        return
    _list_fetch_in_flight = True
    try:
        from proxy_health import enforce_proxy_gate
        try:
            # BEFORE anything reaches the network, and inside the in-flight
            # guard so the gate's own health probe is bounded with the
            # fetch it protects.
            await enforce_proxy_gate()
        except Exception:
            openrouter.note_extraction_list_unreachable()
            logger.info("Notebook: the proxy gate is closed, so the model "
                        "list was not asked for and a model nothing here "
                        "knows stays unknown.")
            return
        await openrouter.fetch_extraction_models()
    except Exception as exc:
        openrouter.note_extraction_list_unreachable()
        logger.info("Notebook: the model list could not be read (%s), so a "
                    "model nothing here knows stays unknown.",
                    type(exc).__name__)
    finally:
        _list_fetch_in_flight = False


def _prompt_price(model_id: str) -> float | None:
    """USD per million prompt tokens, from the extraction list, or None.

    FALSY, NOT `is None`, and that is the whole point of this function.
    `openrouter.fetch_extraction_models` normalises a price it could not
    read - absent `pricing`, a string that will not parse - to `0.0` rather
    than to None, at the one place the wire unit is known. Passing that
    through made the preview say "about $0.00000" for a model whose price
    is simply unknown, which is the fake zero this route is required never
    to show; the caller renders None as "the price is not known", which is
    the true sentence. A genuinely free model reads as unknown too, and
    that is the safe direction: `:free` variants are dropped from the list
    upstream, so the case is theoretical and the cost of being wrong about
    it is a sentence rather than a bill.
    """
    import openrouter

    for m in openrouter.cached_extraction_models() or []:
        if m.get("id") == model_id:
            return float(m.get("prompt_price") or 0) or None
    return None


def _completion_price(model_id: str) -> float | None:
    """USD per million completion tokens, from the extraction list, or
    None. Falsy is unknown, for the reason `_prompt_price` gives."""
    import openrouter

    for m in openrouter.cached_extraction_models() or []:
        if m.get("id") == model_id:
            return float(m.get("completion_price") or 0) or None
    return None


def _claim_one(cap: int | None = None) -> str:
    """Reserve one call and RETURN THE DAY it was reserved against.

    The day travels with the plan so the refund can name the same row. See
    `notebook_store.release_call`: deriving it twice, hours apart, is how a
    claim at 23:59 was refunded out of the first of January.

    `cap` is the ceiling to claim against: the whole daily ceiling by
    default, the lower one a backfill job passes. One counter either way -
    the same row, the same statement - so a job cannot spend what the live
    reader has already spent, and the live reader cannot spend what a job
    has.
    """
    from database import get_db
    with get_db() as con:
        day = notebook.spend_day(con)
        notebook.claim_call(
            con, config.NOTEBOOK_DAILY_CALL_CAP if cap is None else cap,
            day=day)
        return day


def _release_one(day: str | None = None) -> None:
    """Hand the claimed slot back. No `Exception` reaches the caller.

    NOT `BaseException`, and the gap is deliberate rather than an oversight
    to be tidied up. `CancelledError`, `KeyboardInterrupt` and `SystemExit`
    travel straight through this function, and they have to: both callers
    are unwinding a turn that a cancellation started, and a refund that
    swallowed the cancellation would hold the stand-down open past the
    moment the vault route clears the key. Widening the handlers below to
    `except BaseException` would trade one lost slot for a vault that
    cannot finish locking, which is the more expensive of the two.

    Both callers are already on an abandon path, and the whole point of the
    slot is that the day's budget stays honest - failing to give it back is
    the cheaper of the two mistakes, and it is the one that used to happen
    every time.

    `day` is the day the claim was stamped with, carried on the plan. A
    refund that re-derives it lands on the wrong row after midnight.

    AND IT SAYS SO WHEN IT FAILS. The bare `except: pass` was swallowing the
    single most likely outcome on this path: the usual reason a refund is
    needed at all is that the vault locked, and at that moment `get_db`
    raises `VaultLockedError` before a statement runs. So the commonest
    failure of the repair was the one nothing recorded - no counter, no log,
    the slot simply gone out of sixty. Still lets no `Exception` out,
    because neither caller can act on one; but a reader looking for where
    their day went now has a line to find.

    AND THAT PROMISE RESTS ON ONE IMPORT. `vault_state` has to be bound at
    module level for the clause below to be evaluable, because an `except`
    clause is only evaluated once something is in flight. Bound only inside
    another function, the lookup raised `NameError` at exactly the moment
    this block was doing its job, the `except Exception:` under it never
    ran, and the promise in the first line was false for every failure this
    path saw. If that import moves back inside a function, this docstring
    is a lie again.
    """
    from database import get_db

    try:
        with get_db() as con:
            notebook.release_call(con, day=day)
    except vault_state.VaultLockedError:
        # EXPECTED, and named rather than counted as a fault: the lock is
        # what abandoned the turn in the first place. No values, no day
        # string - this module is on log_leak_scan's must-stay-clean list.
        logger.info(
            "Notebook: a claimed call could not be given back because the "
            "vault locked first. Today's allowance is one call smaller than "
            "it should be.")
    except Exception:
        logger.warning(
            "Notebook: a claimed call could not be given back. Today's "
            "allowance is one call smaller than it should be.")


def _empty_reason(outcome: dict | None) -> str:
    """Why a paid reply landed nothing, in one word for the status screen."""
    if not outcome:
        return "settled_nothing"
    if outcome.get("stale_attempt"):
        return "settled_stale_attempt"
    # BEFORE `duplicate`, and that ordering is the whole of it. The dead-chat
    # gate in commit_extraction returns `duplicate` alongside `chat_gone` on
    # purpose - the flag is what stops a reply nobody can be shown from
    # counting as a healthy run and resetting the failure breaker - so a chat
    # deleted while its reply was in flight arrived on this screen wearing the
    # word for an already-answered work key. Nothing read `chat_gone`, so the
    # one outcome that is not a retry, not a race and not the provider's fault
    # was reported as the commonest of the three.
    if outcome.get("chat_gone"):
        return "settled_chat_gone"
    if outcome.get("duplicate"):
        return "settled_duplicate"
    return "settled_empty"


def _failure_type(exc: BaseException) -> str:
    """What to write in `error_type` for a failed call.

    The class name, plus this app's own short reason code when the exception
    carries one - as an attribute, or as the whole of its message.

    `openrouter` raises ONE class for everything and separates the cause into
    a short code: `openrouter_timeout`, `proxy_auth_failed`, `api_key_not_set`
    and the rest of the `_status_to_reason` vocabulary. Recording only the
    class meant every provider failure in the table read `OpenRouterError`,
    and "it broke" and "you have not set an API key" became the same row.

    Both sources are checked against `isidentifier()`, and that is the guard
    rather than a formality: a code from our own vocabulary is one word with
    no spaces, while an arbitrary exception MESSAGE can carry a path, a URL,
    or something a person typed. Anything that does not look like one of our
    codes is refused and the class name stands.
    """
    name = type(exc).__name__
    for candidate in (getattr(exc, "reason", None), str(exc)):
        if isinstance(candidate, str) and candidate.isidentifier():
            return f"{name}:{candidate}"
    return name


def _have_api_key() -> bool:
    """Whether a request could leave this machine at all.

    Imported inside the function for the same reason `openrouter` is: this
    module is imported by the send path, and the secrets layer pulls in the
    vault. Failure is treated as "no key" rather than raised - a background
    feature that cannot read a secret must not take the send path down with
    it, and answering "no" costs a skip row, which is exactly the visible,
    bounded outcome this branch exists to produce.
    """
    try:
        # The same two names openrouter.complete itself reads, from the same
        # module. Reading the secret by any other route would be a second
        # answer to a question that already has one, and the two could
        # disagree.
        from openrouter import SECRET_API_KEY, get_secret
        return bool(get_secret(SECRET_API_KEY))
    except Exception as exc:                              # noqa: BLE001
        # As above: the class name only.
        logger.warning("Notebook: could not read the API key (%s).",
                       type(exc).__name__)
        return False


def _existing_line(entry: dict) -> str:
    """One note as the model sees it, with what KIND of note it is.

    Three states that look identical in a plain list and mean different
    things: something the reader wrote, something the model suggested and the
    reader kept, and something the model suggested that nobody has looked at
    yet. The last of those only became visible to the model at all when the
    list widened to stop it re-proposing its own observations - and a
    suggestion it cannot tell from an accepted fact is a suggestion it will
    treat as settled.
    """
    if entry["status"] == notebook.STATUS_PROPOSED:
        return f"(unreviewed suggestion) {entry['text']}"
    if entry["provenance"] == notebook.PROV_USER:
        return f"(written by the reader) {entry['text']}"
    return entry["text"]


def _plan_work(chat_id: int, model_id: str, language: str,
               threshold: int, limit: int, *,
               min_new: int | None = None,
               after_id: int | None = None,
               until_id: int | None = None,
               reread: bool = False,
               first_read_edge: int | None = None,
               from_oldest: bool = False,
               keep=()) -> dict | None:
    """Decide whether there is enough new material, and gather it.

    A25: the delta is what gets extracted, plus a two-message disambiguation
    window that is shown as CONTEXT and is explicitly not extracted from.
    Graphiti's default is the same two, with the same rule.

    `threshold` is how much new material is worth a call; `limit` is how much
    one call may read. They are different numbers and conflating them was a
    ten-fold spending bug - see Worker.batch_size.

    TWO CALLERS, ONE DOOR. The ordinary turn-driven path passes neither
    keyword and behaves exactly as before. A reading job's unit - a reader
    asking for a chat's unread history to be read - passes both, and they
    are what make it a different question rather than a different function:

      * `min_new` says "one message is enough", because a person asking for
        this has already decided it is worth a call. It also CLOSES the
        upgrading-user branch below: that branch exists to spare somebody the
        cost of reading four hundred old messages they did not ask for, and
        it must not fire for somebody who did ask.
      * `after_id` names where to read from, so a job can fill a hole the
        ordinary cursor has already passed. The cursor is a MAX and can only
        move forward; a range that was skipped is below it forever.

    `first_read_edge` is the backlog above which a never-extracted chat is
    read from the PRESENT instead of from the beginning, and it defaults to
    `threshold * 2` so every caller that does not care behaves exactly as
    before. It is a parameter because an edge derived from a number the
    breaker moves (the batch halves; the threshold did too until decision
    28 a) is H-14 - a twenty-message chat read as a
    four-hundred-message backlog, eighteen of them abandoned under a cursor
    that only moves forward, with the breaker still CLOSED and nothing else
    refusing work. Whether a conversation is meeting this feature for the
    first time is a fact about the conversation; how healthy the provider is
    has no business in it.

    Opening a second entry point into the worker is exactly the thing this
    module refuses elsewhere, so this is a keyword pair on the existing
    planner rather than a second planner - one cursor, one claim, one
    accounting.

    `until_id` and `reread` are the range job's two words, on the same
    principle: `until_id` bounds the batch to the chosen stretch so the
    last unit does not run past it, and `reread` skips the coverage walk,
    because re-reading a stretch that has been read is the whole of what a
    range job is for (its reading record was rolled back when the job was
    made, so the work key is free again).

    `from_oldest` is the job's third word (3.11): when the batch is longer
    than the character ceiling, keep the OLDEST lines that fit rather than
    the newest. See the budget below for why a job needs the other end.
    """
    from database import get_db

    with get_db() as con:
        # Before anything is planned, close out the trace of a call that was
        # made and never settled - the app killed with the window, or the
        # vault locked mid-request. See notebook.settle_orphaned_running: the
        # money for that range is already spent, and a cursor that will not
        # move past it re-sends and re-bills the identical range on every
        # later cycle.
        abandoned = notebook.settle_orphaned_running(con, chat_id, keep)
        if abandoned:
            # Read, at last. This was assigned and never used, so the one
            # event it names - a paid call that was never settled, now closed
            # out - left no trace anywhere a person could see it.
            logger.info("Notebook: closed %d abandoned extraction(s) for a "
                        "chat before planning.", abandoned)
        last = con.execute(
            "SELECT COALESCE(MAX(to_message_id), 0) FROM notebook_extractions "
            "WHERE chat_id = ? AND (status = 'done' "
            "     OR (status = 'failed' AND error_type = ?))",
            (chat_id, notebook.ABANDONED_IN_FLIGHT)).fetchone()[0]
        if after_id is not None:
            # A job reads from where it was TOLD to, not from the high
            # water mark. `last` is a MAX and only moves forward, so a range
            # the ordinary path stepped over - a budget that dropped the
            # oldest lines of a batch, an upgrading chat that started at the
            # present - sits below it and is unreachable for good. This is
            # the only way back to it.
            last = after_id
        # Whether this chat has EVER been read, which `last == 0` alone does
        # not answer. `forget_proposals_from_messages` deletes extraction rows
        # above an edited message ON PURPOSE, to roll the cursor back - and
        # that can empty the table for a chat that has in fact been read many
        # times over. Without this flag, Defect 3: a 61-message chat with
        # three completed ranges, an edit to message 5, and `last` collapses
        # to 0 exactly like a chat that has never been looked at - so the
        # branch below reads it as the upgrading-user case and jumps to the
        # PRESENT, abandoning messages 1..41 (the edited one included) for
        # good. The flag survives the delete that emptied `last`.
        ever_extracted = con.execute(
            "SELECT notebook_extracted_ever FROM chats WHERE id = ?",
            (chat_id,)).fetchone()
        ever_extracted = bool(ever_extracted[0]) if ever_extracted else False
        # COUNT before SELECT. Nineteen offers in twenty return here, and the
        # version that fetched the rows first decrypted every pending message
        # body to do it - a hundred and ninety message bodies per cycle, to
        # answer a question a counter answers.
        pending = con.execute(
            "SELECT COUNT(*) FROM messages "
            "WHERE chat_id = ? AND active = 1 AND id > ?",
            (chat_id, last)).fetchone()[0]
        # A person who asked for this has already decided it is worth a
        # call, so the turn-count threshold is theirs to override - but only
        # downward, and only for their own request.
        if pending < (min_new if min_new is not None else threshold):
            return None

        # LIMIT, and the range is what was actually READ. Without it a long
        # backlog was selected whole, `to_id` named the last of five hundred
        # messages, the prompt builder kept the newest twelve thousand
        # characters of it, and the transaction marked the entire range done -
        # so the other four hundred and eighty were silently never extracted.
        # The one silent-loss shape this whole module exists to prevent,
        # rebuilt at the other end of the same function.
        #
        # AND THE EDGE IS NEITHER `limit` NOR, ANY LONGER, `threshold`.
        # `limit` is `Worker.batch_size`, which HALVES on the breaker's
        # consecutive failures - three of them and it is 2, so the edge was 4
        # and a chat with five messages in it read as a four-hundred-message
        # backlog. The branch then jumped to the present and left three of
        # those five under a cursor that only moves forward. Three failures
        # is not an open breaker (TRIP_AFTER is 5), so nothing else was
        # refusing work at the time and the loss was silent.
        #
        # `threshold` was the fix, and it stopped being safe while
        # `Worker.trigger_threshold` halved alongside the batch (it no longer
        # does, decision 28 a): the identical bug, through the other
        # argument. So the edge is its own parameter, the worker passes
        # `from_start_edge()` - forty, the line decisions 30 a and 31 a draw
        # between a chat read from its start and one read from the present -
        # and every other caller gets `threshold * 2`.
        edge = first_read_edge if first_read_edge is not None else threshold * 2
        if (min_new is None and last == 0 and pending > edge
                and not ever_extracted):
            # NOTHING has EVER been read in this chat (not merely "since the
            # cursor last moved") and there is a backlog. That is the
            # upgrading user: an existing conversation meeting this feature
            # for the first time.
            #
            # Reading from the OLDEST end would have the notebook describing
            # the opening of a story that has since moved four hundred
            # messages on, and injecting those notes into the live prompt for
            # the twenty-odd turns it takes to catch up - each one a paid
            # call. A notebook that lags a session behind is worse than an
            # empty one, because the model trusts it.
            #
            # So the first read of an existing chat starts at the PRESENT.
            # The older history is not extracted at all, and that is the
            # honest trade: it was never read, it is not pretended to be.
            #
            # And it is now a trade the reader can UNDO. This branch used to
            # be the end of the story: the cursor sat at the newest message
            # and the `AND id > ?` branch below could never look under it
            # again, so the whole history was unreachable by any route. A
            # job passes `min_new`, which is what keeps it out of here.
            rows = con.execute(
                "SELECT id, role, content FROM messages "
                "WHERE chat_id = ? AND active = 1 "
                "ORDER BY id DESC LIMIT ?",
                (chat_id, max(1, limit))).fetchall()
            rows = list(reversed(rows))
        else:
            bound = "" if until_id is None else " AND id <= ?"
            args = ([chat_id, last] + ([] if until_id is None else [until_id])
                    + [max(1, limit)])
            rows = con.execute(
                "SELECT id, role, content FROM messages "
                f"WHERE chat_id = ? AND active = 1 AND id > ?{bound} "
                "ORDER BY id LIMIT ?", args).fetchall()

        if after_id is not None and rows and not reread:
            # WHERE THE HOLE ENDS.
            #
            # A job unit is told where to START and was never told where to
            # stop: the walk above is a plain LIMIT, and `after_id` names the
            # bottom of a hole, not its top. So a four-message hole sitting
            # under a stretch of forty that had already been read was planned
            # as forty-four - the four nobody had read, and forty that were
            # paid for once. `work_key` hashes the RANGE, so a range nobody
            # had asked before is a key nobody had answered before, and
            # `already_done` waves it through. The same answer is bought
            # twice, and the repository's own sweep test shows the shape.
            #
            # `work_key` is not the place to fix that and is deliberately
            # left alone: it carries the model and the language because a
            # different model is a different QUESTION, and the live worker
            # depends on that. Coverage is a different question again - it
            # asks whether anybody has READ these messages, whatever they
            # were asked - so it is answered from the range table, with the
            # same predicate the cursor and the unread scan already use.
            #
            # A plan is one contiguous range by construction (`from_id` and
            # `to_id` are a span), so the walk stops at the first covered
            # message rather than skipping over it. The next hole is the next
            # press. The ordinary path is not asked: its cursor is a MAX, so
            # nothing above it can be covered, and this is the hot path
            # nineteen offers in twenty return from.
            spans = [(lo, hi) for lo, hi in con.execute(
                "SELECT from_message_id, to_message_id "
                "FROM notebook_extractions WHERE chat_id = ? "
                "AND (status = 'done' "
                "     OR (status = 'failed' AND error_type = ?))",
                (chat_id, notebook.ABANDONED_IN_FLIGHT)).fetchall()
                if lo is not None and hi is not None]
            uncovered = []
            for row in rows:
                if any(lo <= row["id"] <= hi for lo, hi in spans):
                    break
                uncovered.append(row)
            if not uncovered:
                # `after_id` is sitting under a message somebody has already
                # read. There is nothing here to buy, and buying it anyway is
                # the whole defect.
                return None
            rows = uncovered

        window = con.execute(
            "SELECT role, content FROM messages "
            "WHERE chat_id = ? AND active = 1 AND id <= ? "
            "ORDER BY id DESC LIMIT 2", (chat_id, last)).fetchall()
        card_row = con.execute(
            "SELECT c.description FROM chats ch "
            "JOIN characters c ON c.id = ch.character_id WHERE ch.id = ?",
            (chat_id,)).fetchone()
        # On THIS connection. `list_entries` opens its own, and doing that
        # from inside an open `get_db()` block works today only because the
        # database is in WAL mode - the moment the outer block becomes a
        # `BEGIN IMMEDIATE`, the inner connection waits out the busy timeout
        # and fails. Reading it here costs one query and removes the trap.
        entries = [dict(r) for r in con.execute(
            "SELECT id, text, status, provenance, source_message_id "
            "FROM notebook_entries "
            "WHERE chat_id = ? AND retired_at IS NULL ORDER BY position, id",
            (chat_id,)).fetchall()]
        # The newest ACTIVE message, read on this connection for the same
        # reason `entries` is. It is what tells a read that reaches the
        # present from one that stops short, and that is the only question
        # the source filter below actually asks.
        newest_row = con.execute(
            "SELECT MAX(id) FROM messages WHERE chat_id = ? AND active = 1",
            (chat_id,)).fetchone()
        newest_id = newest_row[0] if newest_row else None

    # WHAT ENTERS THE PROMPT IS WHAT GETS MARKED READ.
    #
    # `build_user_message` budgets these lines and drops whole ones from the
    # OLD end when they do not fit. The planner then recorded `from_id` as
    # the first row of the SQL range - including the lines the budget threw
    # away - so a batch larger than the character ceiling marked messages
    # read that no model ever saw. The cursor is a MAX, so they were never
    # coming back.
    #
    # Budgeting HERE, where the ids and the text are still side by side, is
    # the same rule the existing-notes list a few lines down already follows,
    # and for the same reason. `notebook_extract._budget` stays exactly as it
    # is (its `keep="tail"` is load-bearing and locked): given a list that
    # already fits, it returns it unchanged.
    #
    # The tags come off first, because a stripped line is shorter and the
    # budget must be spent on what the model will actually read.
    # The role and the body, kept APART as well as joined.
    #
    # The joined form is what the model reads; the split form is what the
    # grounding gate reads. Rebuilding the second from the first is where a
    # message body that contains a line starting `user: ` became a message
    # of its own - the model's words attributed to the person, and the
    # panel's "from the model's own reply" mark silenced on the one note
    # class it exists for.
    parts = {r["id"]: (r["role"],
                       voice_tags.strip_for_display(r["content"], r["role"]))
             for r in rows}
    lines = [(r["id"], f"{r['role']}: "
              f"{voice_tags.strip_for_display(r['content'], r['role'])}")
             for r in rows]
    # The same stripping for the context window. Not cosmetic: the grounding
    # check in parse_reply compares a quote against one MESSAGE of `chunk`,
    # and `chunk` is built from these same lines - so stripping one side and
    # not the other would make every quote ungrounded and silently empty the
    # feature.
    #
    # Built BEFORE the material is budgeted, because what it costs is what
    # comes off the material's ceiling.
    recent = [f"{r['role']}: "
              f"{voice_tags.strip_for_display(r['content'], r['role'])}"
              for r in reversed(window)]
    # THE PRONOUN WINDOW IS A SECTION, NOT A REMAINDER.
    #
    # Downstream it is handed `TURNS_MAX_CHARS` minus whatever `new` spent,
    # and this line used to spend all of it - so RECENT_TURNS was empty on
    # exactly the chats long enough to need it, while the prompt goes on
    # instructing the model to resolve pronouns against it. Taken out first,
    # and only as much as the two messages will really use, so a short window
    # costs the material nothing and a long one cannot be starved by it.
    reserve = min(RECENT_RESERVE_CHARS,
                  sum(len(line) + 1 for line in recent))
    # WHICH END IS KEPT depends on who is reading.
    #
    # The ordinary reader keeps the NEWEST lines that fit
    # (`budget_pairs`): it reads the present, and on a long-message chat the
    # stretch it steps over is the part the next reader would have to walk
    # back for. Decision 68 a moves that to a later commit.
    #
    # A job keeps the OLDEST (3.11). Its whole purpose is the history, and
    # keeping the newest did the opposite of that. Measured on twenty
    # messages of about a thousand characters: the first unit read the
    # newest of them, the job's cursor then still pointed at the chat's
    # first message, the loop saw no progress and closed the job `failed`
    # after that one call - with the first message never read.
    # Keeping the oldest, each unit reads forward from where the last one
    # stopped, and whatever did not fit stays above the cursor for the next
    # unit, which is the same job. Nothing is stepped over.
    ceiling = notebook_extract.TURNS_MAX_CHARS - reserve
    kept = (_budget_from_oldest(lines, ceiling) if from_oldest
            else notebook_extract.budget_pairs(lines, ceiling))
    if not kept:
        # Even one line does not fit. Reading nothing is right; marking the
        # range read would be the silent loss this whole block is about.
        return None
    rows = [r for r in rows if r["id"] in {i for i, _ in kept}]

    new = [text for _, text in kept]
    # ACCEPTED notes and the model's OWN pending suggestions.
    #
    # The model could not see what it had already proposed, so on the next
    # window it proposed the same fact again, and again - a review queue that
    # grew a fresh copy of every observation per turn, which is the fastest
    # way to make a reader stop reading it. It sees them now.
    #
    # A pending note the USER wrote is a state that does not exist (the panel
    # writes accepted rows), so `provenance='model'` costs nothing here and
    # keeps the widening from ever reaching the reader's own writing.
    #
    # This is the half that goes in SECOND. Being visible means being
    # nameable in `supersedes`, and a model naming its own unreviewed
    # suggestion would have retired a note nobody had looked at yet -
    # retire_superseded refuses that, and it refuses it because of the
    # `status = accepted` condition that landed with this same change.
    live = [e for e in entries
            if e["status"] == notebook.STATUS_ACCEPTED
            or (e["status"] == notebook.STATUS_PROPOSED
                and e["provenance"] == notebook.PROV_MODEL)]
    # WHAT THIS READ MAY NAME. A read that ends at message N is shown only
    # the notes that came from messages up to N. Shown a note distilled
    # from message 13 while reading 2 to 4, the model saw a "fact" the
    # transcript in front of it did not contain, called it stale, and
    # named it in `supersedes` - so reading the past deleted the future.
    # Measured five times out of five before this line existed. A note
    # with no source message is handled by the second clause below.
    #
    # THE READER'S OWN NOTES stay on the list in every mode: they carry no
    # source, the model is shown them so it does not re-propose them, and
    # `retire_superseded` refuses anything whose provenance is not the
    # model's, so being visible costs nothing.
    #
    # A MODEL NOTE WITH NO SOURCE is not the same thing, and reading NULL as
    # "the reader wrote it" is what left Q-44 half open. `commit_extraction`
    # writes NULL for a model note whose message was deleted or edited away
    # during the provider call - a stale foreign key would abort the whole
    # transaction - and the provenance guard does not cover such a row. On a
    # read that stops short of the present it is exactly the hazard this
    # filter exists for: a note the transcript in front of the model does
    # not account for, nameable in `supersedes`, and this time with nothing
    # to compare it against at all. So it is dropped from a read that stops
    # short and kept by a read that reaches the present, which is the same
    # rule the first clause applies, asked the only way it can be asked of a
    # note with no source of its own.
    to_id = rows[-1]["id"]
    reaches_the_present = newest_id is not None and to_id >= newest_id
    live = [e for e in live
            if (e["source_message_id"] is None
                and (e["provenance"] != notebook.PROV_MODEL
                     or reaches_the_present))
            or (e["source_message_id"] is not None
                and e["source_message_id"] <= to_id)]
    # C-7, DISCHARGED IN WRITING. This unit changes what this planner puts
    # in `existing`, which is one of the three sections `_budget` trims, so
    # C-7 asks for `notebook_extract._budget`'s queue selection to be read
    # in the same change and its constancy recorded. Read: it still takes
    # `keep="tail"` for the turns and the head for the rest, and neither
    # the parameter nor the branch that uses it moved. Nothing here asks it
    # for a different end of the queue, and the prune above happens before
    # `budget_pairs` is applied rather than inside it.
    # Bounded HERE, where the ids and the text are still side by side, so the
    # numbered list the model sees and the list its answers are resolved
    # against are the same list. Budgeting one and not the other shifted every
    # index and retired the wrong note.
    budget = 0
    trimmed: list[dict] = []
    for entry in reversed(live):          # newest first, keep what fits
        cost = len(entry["text"]) + 8     # the "12. " prefix and a newline
        if budget + cost > notebook_extract.EXISTING_MAX_CHARS:
            break
        trimmed.append(entry)
        budget += cost
    live = list(reversed(trimmed))
    return {
        "from_id": rows[0]["id"],
        "to_id": rows[-1]["id"],
        "new": new,
        "recent": recent,
        "chunk": "\n".join(new),
        # The same messages, still in pieces. `parse_reply` grounds against
        # THESE rather than re-parsing the string above, so nothing a message
        # body contains can invent a boundary.
        "spans": [parts[i] for i, _ in kept],
        "card": (card_row["description"] if card_row else "") or "",
        # MARKED, not bare. The model was shown a numbered list with no way
        # to tell its own unreviewed suggestions from notes the reader wrote
        # and kept - so it re-proposed its own pending observations turn
        # after turn, and a `supersedes` naming one of the reader's notes
        # looked exactly like naming one of its own.
        #
        # THE ONE CONDITION: both lists are built from the SAME `live`, in
        # the SAME order, and nothing touches one without the other.
        # `parse_reply` bounds-checks against `len(existing)` and
        # `commit_extraction` resolves `existing_ids[idx]`, so an index
        # into one must be the same note in the other. Pruning is allowed -
        # the source filter and the character budget above both prune -
        # because they act on `live` before either list is derived; a
        # prefix on a line changes neither length nor order. What is not
        # allowed is dropping, sorting or trimming ONE of the two lists,
        # which retires the note at the wrong index.
        "existing": [_existing_line(e) for e in live],
        "existing_ids": [e["id"] for e in live],
        "work_key": notebook_extract.work_key(
            chat_id, rows[0]["id"], rows[-1]["id"], model_id, language),
    }


def _budget_from_oldest(pairs, ceiling):
    """`notebook_extract.budget_pairs` from the other end: the longest
    run of whole lines, from the FIRST, that fits the ceiling. Same cost per
    line (its length and a newline), so the two agree about what fits and
    differ only in which end is kept. See `_plan_work` (3.11)."""
    out = []
    spent = 0
    for pair in pairs:
        cost = len(pair[1]) + 1
        if spent + cost > ceiling:
            break
        out.append(pair)
        spent += cost
    return out


def _write(chat_id: int, plan: dict, proposals: list[dict],
           usage: dict) -> dict:
    from database import get_db
    with get_db() as con:
        con.execute("BEGIN IMMEDIATE")
        return notebook.commit_extraction(
            con, work_key=plan["work_key"], chat_id=chat_id,
            from_id=plan["from_id"], to_id=plan["to_id"],
            proposals=proposals, existing_ids=plan["existing_ids"],
            # This path ALWAYS wrote a `running` row first. If it is gone,
            # an edit or a delete rolled the cursor back under us on purpose,
            # and these notes describe wording the user has since taken back.
            usage=usage, status="done", require_trace=True,
            # Whose attempt this is. If the row has since been reclaimed by a
            # NEWER attempt at the identical range (same work_key), this call
            # is stale and must not overwrite it - see commit_extraction's
            # ownership check, Defect 2.
            attempt_token=plan.get("attempt_token"),
            claim_day=plan.get("claim_day"),
            # The run these notes belong to, or None. What makes "undo
            # this run" reach exactly these rows and no others.
            job_id=plan.get("job_id"))


async def _record_running(chat_id: int, plan: dict) -> bool:
    """The trace a paid call leaves before it is made. True if it landed.

    Written between the claim and the request, so that a cancellation in
    flight - or the process dying with the window, which this module accepts
    as unavoidable - leaves something behind instead of a billed call with no
    row of any status. `commit_extraction` settles this same row into `done`
    or `failed` afterwards; it is a row updated in place, never a second one.
    """
    return await anyio.to_thread.run_sync(
        functools.partial(_record, chat_id, plan, "running"))


async def _record_skip(chat_id: int, reason: str,
                       plan: dict | None = None) -> bool:
    """A47: a skipped extraction is not silent.

    Without a row, "the notebook has not proposed anything for a week" and
    "the notebook has refused sixty times for a reason nobody can see" are the
    same screen.
    """
    if plan is None:
        return False
    # Declared or it does not ship. These reach a reader as sentences, so an
    # undeclared one is a snake_case token in prose - the same failure the
    # error catalogue exists to prevent, in a vocabulary the catalogue does
    # not cover.
    if reason not in SKIP_REASONS:
        # NOT an `assert`. A gate written as an assertion is a gate with a
        # command-line switch on it: `python -O` removes it and the
        # undeclared reason is written silently, which is the one outcome
        # this check exists to prevent.
        #
        # The two spec files carry no `optimize=` today and `PYTHONOPTIMIZE`
        # appears nowhere in the repo, so nothing SHIPS with them stripped -
        # this closes a latent hole rather than a live one. Closing it here
        # rather than pinning `optimize=0` in the build is the stronger of
        # the two: it holds however the process is started, including one
        # somebody starts by hand.
        raise ValueError(f"undeclared skip reason: {reason}")
    return await anyio.to_thread.run_sync(
        functools.partial(_record, chat_id, plan, "skipped",
                          skip_reason=reason))


async def _record_failure(chat_id: int, plan: dict, error_type: str,
                          usage: dict | None = None) -> bool:
    return await anyio.to_thread.run_sync(
        functools.partial(_record, chat_id, plan, "failed",
                          error_type=error_type, usage=usage))


def _record(chat_id: int, plan: dict, status: str, *,
            skip_reason: str | None = None, error_type: str | None = None,
            usage: dict | None = None) -> bool:
    """Write one outcome row. Returns whether it was actually written.

    The return value is the point. This function must swallow its own failure
    or a locked vault takes the loop down with it, and swallowing it silently
    meant the caller believed a row existed when none did - so the failure
    that lost the work ALSO lost the only record of the work, and every screen
    agreed that nothing had happened.
    """
    from database import get_db
    try:
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            notebook.commit_extraction(
                con, work_key=plan["work_key"], chat_id=chat_id,
                from_id=plan["from_id"], to_id=plan["to_id"],
                usage=usage, status=status, skip_reason=skip_reason,
                error_type=error_type,
                attempt_token=plan.get("attempt_token"),
                claim_day=plan.get("claim_day"),
                job_id=plan.get("job_id"))
            if status == "running":
                # In the same transaction as the row it stamps.
                # `commit_extraction` does not set `started_at`, and it is the
                # only field that says WHEN the call left - which is what
                # separates a row abandoned by a crash hours ago from one that
                # is genuinely still in flight. `created_at` cannot answer
                # that, because the settled statuses share it.
                con.execute(
                    "UPDATE notebook_extractions SET started_at = "
                    "datetime('now') WHERE work_key = ?", (plan["work_key"],))
        return True
    except Exception:
        # The vault may have locked between the attempt and the bookkeeping.
        # Losing the row is regrettable; losing the loop is not acceptable.
        logger.info("Notebook outcome could not be recorded.")
        return False
