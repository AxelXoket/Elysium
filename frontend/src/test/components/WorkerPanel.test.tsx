/**
 * FAZ 5 - the screen that answers "what did it do with my money".
 *
 * The feature spends the user's own credits unattended. Two things can go
 * wrong: a loop that will not stop, and a refusal nobody can see. These tests
 * are about the second - every counter here exists because without it a
 * refused run and a quiet week look identical.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { hiddenFromReaders, readableText } from "@/test/helpers/accessibility";
import {
  CHAT_STATE_PROSE,
  DROP_PROSE,
  EMPTY_PROSE,
  FAILURE_PROSE,
  SKIP_PROSE,
  STATE_PROSE,
  SWEEP_PROSE,
  WorkerPanel,
  echoable,
} from "@/components/notebook/WorkerPanel";
import { getErrorMessage } from "@/lib/errors/errorMessages";
import { mockFetch } from "../mocks/api";
import { useUiStore } from "@/lib/store/uiStore";
// The backend's skip vocabulary, as agreed rather than as retyped. Its other
// half is backend/tests/test_notebook_worker.py, which asserts this list
// equals notebook_store.SKIP_REASONS in both directions - so a reason the
// worker can write and this file has never heard of fails over there, and a
// reason listed here with no sentence fails down below. Same two-sided
// arrangement shared/error_catalogue.json has, for the same reason: the
// tokens are the backend's, the English is ours, and neither side can see
// the other's half at runtime.
import skipContract from "../../../../shared/notebook_skip_reasons.json";

const BACKEND_SKIP_REASONS: string[] = skipContract.reasons;

function statusBody(over: Record<string, unknown> = {}) {
  const worker = {
    state: "closed", failures: 0, total_failures: 0,
    queued: 0, dropped_offers: 0, runs: 3, batch_size: 20,
    ...((over.worker as object) ?? {}),
  };
  const stats = {
    done: 3, failed: 0, skipped: 0, skip_reasons: {},
    ...((over.stats as object) ?? {}),
  };
  return {
    stats,
    spend: { calls: 3, tokens_in: 900, tokens_out: 40, cost: 0.0004,
             ...((over.spend as object) ?? {}) },
    spend_lifetime: { calls: 3, tokens_in: 900, tokens_out: 40, cost: 0.0004,
                      ...((over.spend_lifetime as object) ?? {}) },
    worker,
    daily_cap: 60,
  };
}

function mount(
  over: Record<string, unknown> = {},
  enabled = true,
  extra: Record<string, { status?: number; body: unknown }> = {},
) {
  mockFetch({
    ...extra,
    "/notebook/worker/reset": { body: { ok: true } },
    "/notebook/worker": { body: statusBody(over) },
    "/notebook/auto-accept": {
      body: { enabled, effective: enabled, overridden: false },
    },
  });
  return renderWithQueryClient(<WorkerPanel />);
}

describe("WorkerPanel", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it("says what it has done and what it cost", async () => {
    mount();
    const box = await screen.findByTestId("worker-status");
    expect(box.textContent).toMatch(/3 runs/);
    expect(box.textContent).toMatch(/3 of 60 calls today/);
    expect(box.textContent).toMatch(/0\.00040 credits/);
  });

  it("a fresh vault shows a lifetime total of zero, not blank", async () => {
    mount({
      stats: { done: 0, failed: 0, skipped: 0, skip_reasons: {} },
      spend: { calls: 0, tokens_in: 0, tokens_out: 0, cost: 0 },
      spend_lifetime: { calls: 0, tokens_in: 0, tokens_out: 0, cost: 0 },
    });
    const box = await screen.findByTestId("worker-status");
    expect(box.textContent).toMatch(/0 of 60 calls today/);
    expect(box.textContent).toMatch(/0 calls, 0\.00000 credits lifetime/);
  });

  it("shows the lifetime total beside today's, and the two can differ",
     async () => {
    // The positive control the feature would be untested without: seeded so
    // today's count and the lifetime total are NOT the same number, and both
    // must be readable on screen at once.
    mount({
      spend: { calls: 3, tokens_in: 900, tokens_out: 40, cost: 0.0004 },
      spend_lifetime: { calls: 47, tokens_in: 9000, tokens_out: 400,
                        cost: 0.0091 },
    });
    const box = await screen.findByTestId("worker-status");
    expect(box.textContent).toMatch(/3 of 60 calls today/);
    expect(box.textContent).toMatch(/47 calls, 0\.00910 credits lifetime/);
    expect(box.textContent).not.toMatch(/47 of 60 calls today/);
  });

  it("shows the lifetime MONEY, not only the lifetime call count", async () => {
    // Defect 1: spend_lifetime.cost - the figure notebook_store.py's own
    // docstring argues at length must not be hidden from "the one screen
    // that is supposed to be honest about every credit spent" - was rendered
    // NOWHERE. Only .calls reached the panel. Seeded so today's cost and the
    // lifetime cost are different numbers, so this cannot pass by echoing
    // today's figure twice, and both must be visible together.
    mount({
      spend: { calls: 3, tokens_in: 900, tokens_out: 40, cost: 0.0004 },
      spend_lifetime: { calls: 47, tokens_in: 9000, tokens_out: 400,
                        cost: 0.0091 },
    });
    const box = await screen.findByTestId("worker-status");
    expect(box.textContent).toMatch(/0\.00040 credits/);          // today's
    expect(box.textContent).toMatch(/0\.00910 credits lifetime/); // lifetime's
  });

  it("says when part of today's credit figure is missing", async () => {
    // "We do not know" is not "it was free". `cost` is NOT NULL in the
    // database, so a call the provider declined to price landed in the sum
    // as zero and the line read as a call that cost nothing. The card has to
    // say how much of its own total is missing.
    mount({
      spend: { calls: 3, tokens_in: 900, tokens_out: 40, cost: 0.0004,
               cost_unknown: 2 },
    });
    const note = await screen.findByTestId("worker-cost-unknown");
    expect(note.textContent).toMatch(/2 of the calls made today came back with no price/);
  });

  it("says nothing about missing prices when none are missing", async () => {
    // GROUND CONTROL. A line that is always on would satisfy the test above
    // and put a warning on every healthy install.
    mount({
      spend: { calls: 3, tokens_in: 900, tokens_out: 40, cost: 0.0004,
               cost_unknown: 0 },
    });
    await screen.findByTestId("worker-status");
    expect(hiddenFromReaders(screen.queryByTestId("worker-cost-unknown"))).toBe(true);
  });

  it("says WHY runs were skipped, in words", async () => {
    // The whole reason this panel exists. "0 notes this week" and "sixty runs
    // refused because your daily limit was reached" are the same screen
    // without this line, and only one of them needs a person.
    mount({ stats: { done: 0, failed: 0, skipped: 12,
                     skip_reasons: { notebook_daily_cap_reached: 12 } } });
    const box = await screen.findByTestId("worker-status");
    expect(box.textContent).toMatch(/12 skipped: today's call limit/i);
    expect(box.textContent).not.toMatch(/notebook_daily_cap_reached/);
  });

  it("says a failed run lost nothing", async () => {
    // A failed range stays UNREAD rather than being marked processed, and
    // that distinction is the point of the whole design - so it is said out
    // loud rather than left for the user to worry about.
    mount({ stats: { done: 1, failed: 4, skipped: 0, skip_reasons: {} } });
    const box = await screen.findByTestId("worker-status");
    expect(box.textContent).toMatch(/4 runs failed/i);
    expect(box.textContent).toMatch(/stay unread, not skipped/i);
  });

  it("does not print the breaker's internal vocabulary", async () => {
    // "closed" means "working". A panel that prints it asks the reader to
    // learn the implementation before reading their own status.
    mount({ worker: { state: "closed" } });
    const box = await screen.findByTestId("worker-status");
    expect(box.textContent).toMatch(/running/i);
    expect(box.textContent).not.toMatch(/closed/i);
  });

  it("offers the reset only when something is actually stuck", async () => {
    mount({ worker: { state: "closed" } });
    await screen.findByTestId("worker-status");
    expect(screen.queryByRole("button", { name: /try again now/i }))
      .not.toBeInTheDocument();
  });

  it("offers the reset when it has stopped, and says so", async () => {
    // Without a hand on the breaker, recovering from a provider outage means
    // restarting the whole application after fixing it.
    mount({ worker: { state: "stopped", total_failures: 20 } });
    const box = await screen.findByTestId("worker-status");
    expect(box.textContent).toMatch(/will not try again until you say so/i);
    expect(await screen.findByRole("button", { name: /try again now/i }))
      .toBeEnabled();
  });

  it("the reset actually asks the backend", async () => {
    const user = userEvent.setup();
    mount({ worker: { state: "stopped" } });
    await user.click(await screen.findByRole("button", { name: /try again now/i }));
    await waitFor(() => {
      const call = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls
        .find((c: unknown[]) =>
          String(c[0]).includes("/worker/reset")
          && (c[1] as RequestInit | undefined)?.method === "POST");
      expect(call).toBeTruthy();
    });
  });

  it("reports turns that went unqueued and what happens to them", async () => {
    mount({ worker: { state: "closed", dropped_offers: 7 } });
    const box = await screen.findByTestId("worker-status");
    expect(box.textContent).toMatch(/7 turns went unqueued/i);
    expect(box.textContent).toMatch(/a later run picks them up/i);
  });

  it("shows the auto-accept switch in its stored position", async () => {
    mount({}, false);
    await waitFor(() =>
      expect(screen.getByRole("switch", { name: /keep suggestions without asking/i }))
        .not.toBeChecked());
  });

  it("saves the switch when it is flipped", async () => {
    const user = userEvent.setup();
    mount({}, true);
    const sw = await screen.findByRole("switch", { name: /keep suggestions without asking/i });
    await waitFor(() => expect(sw).toBeEnabled());
    await user.click(sw);

    await waitFor(() => {
      const call = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls
        .find((c: unknown[]) =>
          String(c[0]).includes("/auto-accept")
          && (c[1] as RequestInit | undefined)?.method === "POST");
      expect(call).toBeTruthy();
      expect(JSON.parse(String((call![1] as RequestInit).body)))
        .toEqual({ enabled: false });
    });
  });
});

describe("runs that were paid for and lost", () => {
  // The money case. Rows that cost money carry status 'failed' too, so the
  // panel used to report them all under one line saying "nothing was lost" -
  // while the backend counted them separately precisely because something
  // WAS lost: the call was billed and those messages are never read.
  //
  // The subtraction now uses `paid_and_lost`, not `abandoned`. `abandoned`
  // is only the calls the app was killed in the middle of; a `write_*`
  // failure happens after the reply has been sent, generated and billed and
  // was landing on the reassuring side of the sum.
  it("says the run was paid for rather than that nothing was lost", async () => {
    mount({ stats: { done: 3, failed: 2, skipped: 0, abandoned: 2,
                     paid_and_lost: 2, skip_reasons: {} } });
    const line = await screen.findByTestId("worker-abandoned");
    expect(line.textContent).toMatch(/paid for/i);
    // Ground: with every failure abandoned, the reassuring line must not
    // appear at all - that is the sentence that was untrue.
    // The failed-runs line is a Collapse and keeps its words in the DOM
    // while closed; the card's REACHABLE text is what this is about.
    expect(readableText(screen.getByTestId("worker-status")))
      .not.toMatch(/Nothing was lost/i);
  });

  it("still reassures about an ordinary failure", async () => {
    // Positive control, and the half that must survive: a genuine failure
    // really does leave its messages unread rather than skipped.
    //
    // RE-CUT. `abandoned: 0` alone no longer describes "nothing was spent" -
    // a write that failed after the reply arrived is also paid for and is
    // counted by `paid_and_lost`. Both have to be zero for this to be the
    // case it claims to be.
    mount({ stats: { done: 3, failed: 2, skipped: 0, abandoned: 0,
                     paid_and_lost: 0, skip_reasons: {} } });
    const box = await screen.findByTestId("worker-status");
    expect(box.textContent).toMatch(/2 runs failed/);
    expect(box.textContent).toMatch(/Nothing was lost/i);
    expect(hiddenFromReaders(screen.queryByTestId("worker-abandoned"))).toBe(true);
  });

  it("counts the two apart when both happened", async () => {
    mount({ stats: { done: 1, failed: 5, skipped: 0, abandoned: 2,
                     paid_and_lost: 2, skip_reasons: {} } });
    const box = await screen.findByTestId("worker-status");
    // Five failed, two of them paid for, so three ordinary ones. Added
    // together instead of nested it would read seven.
    expect(box.textContent).toMatch(/3 runs failed/);
    expect(box.textContent).toMatch(/2 runs were cut off/);
  });

  it("a write that failed after the reply arrived is not reassured about",
    async () => {
      // The case the old arithmetic got wrong, and the reason the field
      // changed. Nothing was abandoned - the app was not killed - but the
      // call was sent, generated and billed, and the notes it carried were
      // lost on the way to disk.
      mount({ stats: { done: 1, failed: 2, skipped: 0, abandoned: 0,
                       paid_and_lost: 2, skip_reasons: {} } });
      const box = await screen.findByTestId("worker-status");
      expect(readableText(box)).not.toMatch(/Nothing was lost/i);
    });

  it("the two counts can differ, and the sum uses the wider one", async () => {
    // POSITIVE CONTROL for the change itself: `paid_and_lost` is a superset,
    // so a fixture where they differ is the only one that can tell which
    // field the sentence is reading.
    mount({ stats: { done: 1, failed: 4, skipped: 0, abandoned: 1,
                     paid_and_lost: 3, skip_reasons: {} } });
    const box = await screen.findByTestId("worker-status");
    // 4 - 3, not 4 - 1.
    expect(box.textContent).toMatch(/1 runs failed/);
  });

  it("does not say Paused while a billed trial call is going out", async () => {
    // The cooldown is over, so the next turn sends exactly one real request.
    // The panel used to read "Paused after repeated failures. It will try
    // again by itself." over a call that was already going out.
    mount({ worker: { state: "half_open" } });
    const box = await screen.findByTestId("worker-status");
    expect(box.textContent).not.toMatch(/^Paused after repeated failures/);
    expect(box.textContent).toMatch(/one trial call/i);
    expect(box.textContent).toMatch(/billed/i);
  });

  it("still says Paused while it really is paused", async () => {
    // GROUND CONTROL: the ordinary open state keeps its sentence.
    mount({ worker: { state: "open" } });
    const box = await screen.findByTestId("worker-status");
    expect(box.textContent).toMatch(/Paused after repeated failures/);
  });

  it("says a withdrawn edit in words, not as a raw code", async () => {
    // This skip reason is written by a different path from the worker's own,
    // so the gate that keeps the prose table complete never covered it and
    // the panel printed the token itself at the reader.
    mount({ stats: { done: 0, failed: 0, skipped: 1, abandoned: 0,
                     skip_reasons: { plan_invalidated: 1 } } });
    const box = await screen.findByTestId("worker-status");
    expect(box.textContent).not.toMatch(/plan_invalidated/);
    expect(box.textContent).toMatch(/wording you had already replaced/i);
  });

  // -------------------------------------------------------------------
  // The two rollback reasons, and the split they actually make.
  //
  // Both descriptions named the wrong discriminator. The backend asks one
  // question when a late reply finds its running row gone: does the LAST
  // message of the range still exist? Probed against the real routes, an
  // edit that lands on the range end gives `plan_invalidated`; a deleted
  // message, a cleared chat, an aborted send's cleanup and an edit whose
  // swept tail was the range end all give `range_cleared`. NOT a regenerated
  // reply: that deletes nothing, so it reaches neither key. So "you edited OR
  // DELETED a message" was wrong about every delete, and "the chat was
  // cleared" was one of four ways into its sibling.
  // -------------------------------------------------------------------

  it("the edit reason says the message was rewritten, not deleted",
     async () => {
    mount({ stats: { done: 0, failed: 0, skipped: 1, abandoned: 0,
                     skip_reasons: { plan_invalidated: 1 } } });
    const box = await screen.findByTestId("worker-status");
    // Rewriting is the only action that reaches this reason: the message
    // survives, so the stretch is genuinely read again.
    expect(box.textContent).toMatch(/rewrote/i);
    expect(box.textContent).toMatch(/read again/i);
    // The claim that put this reason on a delete. A delete removes the
    // message, which is the other reason entirely.
    expect(box.textContent).not.toMatch(/deleted/i);
  });

  it("the removed-messages reason is not described as a cleared chat only",
     async () => {
    mount({ stats: { done: 0, failed: 0, skipped: 1, abandoned: 0,
                     skip_reasons: { range_cleared: 1 } } });
    const box = await screen.findByTestId("worker-status");
    expect(box.textContent).not.toMatch(/range_cleared/);
    // The ways in are named, not just the clear.
    expect(box.textContent).toMatch(/delete/i);
    expect(box.textContent).toMatch(/cleared/i);
    // And NOT a regenerated reply. This assertion used to require the word
    // "regenerated" and so held the panel to a claim that was not true:
    // regenerating deletes nothing, `forget_proposals_from_messages` is never
    // called on that path, and the extraction lands and is written like any
    // other. Telling the reader it was thrown away was the sixth instance in
    // this repository of a test nailing down a sentence the code does not
    // honour, so it is asserted in the negative now.
    expect(box.textContent).not.toMatch(/regenerat/i);
    // And the old claim that this range has nothing left in it at all. Only
    // the removed messages are gone; the rest of the stretch is re-read.
    expect(box.textContent).toMatch(/whatever survives/i);
  });

  it("tells the two rollback reasons apart on screen", async () => {
    // Two reasons that render the same sentence are one reason with two
    // names, and the reader cannot tell which happened.
    mount({ stats: { done: 0, failed: 0, skipped: 2, abandoned: 0,
                     skip_reasons: { plan_invalidated: 1, range_cleared: 1 } } });
    const box = await screen.findByTestId("worker-status");
    const lines = Array.from(box.querySelectorAll("p"))
      .map((p) => p.textContent ?? "")
      .filter((t) => t.includes("skipped:"));
    expect(lines).toHaveLength(2);
    expect(lines[0]).not.toEqual(lines[1]);
  });

  it("the agreed vocabulary is not empty", () => {
    // The guard that stops the walk below from passing by walking nothing.
    // A deleted contract, an emptied `reasons`, a rename that makes the
    // import resolve to undefined would all leave the loop with zero
    // iterations and every assertion in it green. The backend half carries
    // its own floor for the same reason.
    expect(BACKEND_SKIP_REASONS.length).toBeGreaterThanOrEqual(2);
  });

  it("every reason the BACKEND can write reaches the screen as words",
     async () => {
    // What this replaces, and why it is worth the extra file: a backend test
    // opened WorkerPanel.tsx and asserted `${reason}:` appeared somewhere in
    // it. Three ways that lied. It could not tell a sentence from an empty
    // string. It could not tell SKIP_PROSE from the three prose tables that
    // now sit beside it, so a reason whose name collided with a key in any
    // of them passed with no skip sentence at all. And it never rendered
    // anything, so a key present but unreachable read as covered.
    //
    // The union is deliberate: the contract catches a reason the backend can
    // write and this panel has no words for, and the table's own keys catch
    // a sentence that renders empty. Every one of them is checked by
    // mounting the panel and reading the line a person would read.
    const reasons = Array.from(
      new Set([...BACKEND_SKIP_REASONS, ...Object.keys(SKIP_PROSE)]));
    for (const reason of reasons) {
      mount({ stats: { done: 0, failed: 0, skipped: 1, abandoned: 0,
                       skip_reasons: { [reason]: 1 } } });
      const box = await screen.findByTestId("worker-status");
      const line = Array.from(box.querySelectorAll("p"))
        .map((p) => p.textContent ?? "")
        .find((t) => t.includes("skipped:")) ?? "";
      // The fallback sentence. It is the right answer for a reason from a
      // newer backend, and the wrong one for a reason we have committed to
      // having words for - which is every reason in the contract.
      expect(line, reason).not.toMatch(/no words for/i);
      // Asserted before the containment below, because `toContain("")` is
      // satisfied by anything at all: a key stripped of its words would
      // otherwise sail through an assertion written to catch exactly that.
      expect(SKIP_PROSE[reason], reason).toBeTruthy();
      expect(line, reason).toContain(SKIP_PROSE[reason]);
      expect(line, reason).not.toMatch(new RegExp(reason));
      cleanup();
    }
  });

  it("has no sentence for a reason no writer can produce", () => {
    // The other direction, and the one the walk above cannot see: a key left
    // behind by a rename renders perfectly and means nothing. Cheap to
    // check, and it is how the error catalogue's orphan rule reads too.
    expect(Object.keys(SKIP_PROSE)
      .filter((k) => !BACKEND_SKIP_REASONS.includes(k))).toEqual([]);
  });

  it("GROUND: a reason with no sentence still reads as English", async () => {
    // The backend can grow a reason ahead of this file. The fallback used to
    // print the token straight at the reader, which is the one thing this
    // table exists to prevent. The code rides along in brackets for a bug
    // report; the sentence is what a reader gets.
    const unknown = "some_future_reason";
    expect(SKIP_PROSE[unknown]).toBeUndefined();
    mount({ stats: { done: 0, failed: 0, skipped: 1, abandoned: 0,
                     skip_reasons: { [unknown]: 1 } } });
    const box = await screen.findByTestId("worker-status");
    const line = Array.from(box.querySelectorAll("p"))
      .map((p) => p.textContent ?? "")
      .find((t) => t.includes("skipped:")) ?? "";
    expect(line).toMatch(/no words for/i);
    // Not merely "the token appeared somewhere": the line must not BE the
    // token, which is what the old fallback rendered.
    expect(line.replace(/\s+/g, " ").trim())
      .not.toEqual(`1 skipped: ${unknown}.`);
  });
});

/**
 * The way back to a chat's own history.
 *
 * The worker's cursor is a maximum, and the first read of a chat that
 * already had a long history starts at the PRESENT on purpose - a notebook
 * describing a conversation four hundred messages ago is worse than an empty
 * one. That was a decision with no undo. This button is the undo, and the
 * two ways it can decline are ordinary answers rather than errors: there may
 * be nothing unread, or a sweep may already be running.
 */
describe("reading a chat's earlier messages", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: 7 });
  });
  afterEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: null });
  });

  it("is not offered with no chat open", async () => {
    // GROUND CONTROL: there is nothing to read, so there is nothing to
    // press. A button that is always there invites pressing it at random.
    useUiStore.setState({ selectedChatId: null });
    mount();
    await screen.findByTestId("worker-status");
    expect(hiddenFromReaders(screen.queryByTestId("worker-sweep"))).toBe(true);
  });

  it("says it started when there was something unread", async () => {
    mount({}, true, {
      "POST /notebook/sweep/7": { body: { started: true, after_id: 0 } },
    });
    await userEvent.click(await screen.findByTestId("worker-sweep"));
    // The answer line is a Collapse: mounted before it has anything to
    // say, so the words are awaited rather than read on first sight.
    await waitFor(() =>
      expect(screen.getByTestId("worker-sweep-said").textContent)
        .toMatch(/reading the earlier part/i));
  });

  it("says nothing is unread, and does not read as a failure", async () => {
    mount({}, true, {
      "POST /notebook/sweep/7": {
        body: { started: false, reason: "nothing_unread" },
      },
    });
    await userEvent.click(await screen.findByTestId("worker-sweep"));
    await waitFor(() =>
      expect(screen.getByTestId("worker-sweep-said").textContent)
        .toMatch(/nothing here is unread/i));
  });

  it("offers the backlog rather than reading it", async () => {
    mount({ worker: { backlog: { chats: 3, messages: 512 } } });
    const note = await screen.findByTestId("worker-backlog");
    expect(note.textContent).toMatch(/3 chats have 512 unread messages/);
    // The sentence that makes it an offer rather than a warning.
    expect(note.textContent).toMatch(/nothing is read without you asking/i);
  });

  it("says nothing when there is no backlog", async () => {
    // GROUND CONTROL: a line that is always there is not an offer.
    mount({ worker: { backlog: { chats: 0, messages: 0 } } });
    await screen.findByTestId("worker-status");
    expect(hiddenFromReaders(screen.queryByTestId("worker-backlog"))).toBe(true);
  });

  it("is disabled while a sweep is already running", async () => {
    mount({ worker: { sweeping: true } });
    // The button renders before the status query resolves, so waiting for
    // the button is not waiting for the answer that disables it.
    await screen.findByTestId("worker-status");
    await waitFor(() => {
      expect(screen.getByTestId("worker-sweep")).toBeDisabled();
    });
  });

  it("is pressable when one is not", async () => {
    // POSITIVE CONTROL for the line above: a button disabled unconditionally
    // would satisfy it and remove the feature.
    mount({ worker: { sweeping: false } });
    expect(await screen.findByTestId("worker-sweep")).not.toBeDisabled();
  });
});

/**
 * What THIS chat will do, which is not always what the switch says.
 *
 * The switch is the global setting. A chat can carry its own answer and one
 * opened from an imported card does - and the panel rendered the global
 * value alone, so that chat showed "on" while the extractor was correctly
 * holding every suggestion for review. The indicator and the decision
 * disagreeing about the one case the override exists for.
 */
describe("what this chat does with suggestions", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: 7 });
  });
  afterEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: null });
  });

  function mountWithAuto(auto: Record<string, unknown>) {
    mockFetch({
      "/notebook/worker/reset": { body: { ok: true } },
      "/notebook/worker": { body: statusBody() },
      "/notebook/auto-accept": { body: auto },
      "/notebook/7/auto-accept": { body: { ok: true } },
    });
    return renderWithQueryClient(<WorkerPanel />);
  }

  it("says the chat holds them when the chat is what decided", async () => {
    mountWithAuto({ enabled: true, effective: false, overridden: true });
    const box = await screen.findByTestId("chat-auto-accept");
    expect(box.textContent).toMatch(/held for review/i);
    expect(box.textContent).toMatch(/this chat decides/i);
  });

  it("says the switch decided when it did", async () => {
    // GROUND CONTROL: an ordinary chat must not be reported as overridden,
    // or the sentence stops meaning anything.
    mountWithAuto({ enabled: true, effective: true, overridden: false });
    const box = await screen.findByTestId("chat-auto-accept");
    expect(box.textContent).toMatch(/kept without asking/i);
    expect(box.textContent).not.toMatch(/this chat decides/i);
  });

  it("offers the escape hatch and sends what it says", async () => {
    const user = userEvent.setup();
    mountWithAuto({ enabled: true, effective: true, overridden: false });
    await user.click(await screen.findByTestId("chat-auto-accept-toggle"));

    await waitFor(() => {
      const call = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls
        .find((c: unknown[]) => String(c[0]).includes("/notebook/7/auto-accept")
          && (c[1] as RequestInit | undefined)?.method === "POST");
      expect(call).toBeTruthy();
      expect(JSON.parse(String((call![1] as RequestInit).body)))
        .toEqual({ enabled: false });
    });
  });

  it("hands the chat back to the switch when it is already overridden",
    async () => {
      const user = userEvent.setup();
      mountWithAuto({ enabled: true, effective: false, overridden: true });
      await user.click(await screen.findByTestId("chat-auto-accept-toggle"));

      await waitFor(() => {
        const call = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls
          .find((c: unknown[]) =>
            String(c[0]).includes("/notebook/7/auto-accept")
            && (c[1] as RequestInit | undefined)?.method === "POST");
        expect(call).toBeTruthy();
        expect(JSON.parse(String((call![1] as RequestInit).body)))
          .toEqual({ enabled: null });
      });
    });

  it("is not offered with no chat open", async () => {
    useUiStore.setState({ selectedChatId: null });
    mountWithAuto({ enabled: true, effective: true, overridden: false });
    await screen.findByTestId("worker-status");
    expect(hiddenFromReaders(screen.queryByTestId("chat-auto-accept"))).toBe(true);
  });
});


/**
 * WHY it failed, which is the question the card could not answer.
 *
 * `error_type` was written on every failed extraction from the day the
 * column existed, and read by two queries that each ask it one fixed yes/no:
 * "was this abandoned" and "did this cost money". Both answers are a single
 * number, so "it failed three times" and "your key was refused three times"
 * were the same sentence on screen - and only one of those is something a
 * person can act on.
 */
describe("why the failures failed", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => {
    vi.restoreAllMocks();
    cleanup();
  });

  it("names the class of the failure on screen", async () => {
    // THE CLAIM. Two timeouts are not "two failures"; they are the provider
    // going quiet, which is a different thing from a refused key.
    mount({ stats: { done: 0, failed: 2, skipped: 0, abandoned: 0,
                     paid_and_lost: 0, skip_reasons: {},
                     error_types: { "OpenRouterError:openrouter_timeout": 2 } } });
    // Scoped to the LINE, not to the whole card: a sentence that escaped
    // its own paragraph would satisfy an assertion against the card.
    const line = await screen.findByTestId("worker-failure-reason");
    // The denominator is asserted with it: without a total on the card the
    // census lines invite being added against "3 runs failed" above, which
    // is a SUBSET of `failed` and does not reconcile.
    expect(line.textContent).toMatch(/2 of 2 failures/);
    // Imported, not retyped. The sentence belongs to the code catalogue,
    // and a copy here would go on passing after the real one changed.
    expect(line.textContent).toContain(getErrorMessage("openrouter_timeout"));
  });

  it("GROUND: says nothing about reasons when there are none", async () => {
    // A line that is always on would satisfy the test above and put a
    // failure report on every healthy install. An empty render proves
    // nothing, so this is the half that makes the one above mean something.
    mount({ stats: { done: 5, failed: 0, skipped: 0, abandoned: 0,
                     paid_and_lost: 0, skip_reasons: {}, error_types: {} } });
    await screen.findByTestId("worker-status");
    expect(screen.queryByTestId("worker-failure-reason")).toBeNull();
  });

  it("borrows the sentence a reason code already has, rather than writing "
     + "a second one", async () => {
    // The rule this panel already paid for once: a code's English lives in
    // lib/errors/errorMessages.ts and nowhere else, because two tables are
    // how two screens come to disagree about one code. Imported, not
    // retyped - a copy of the sentence here would pass while the real one
    // changed underneath it.
    mount({ stats: { done: 0, failed: 1, skipped: 0, abandoned: 0,
                     paid_and_lost: 0, skip_reasons: {},
                     error_types: { "OpenRouterError:api_key_not_set": 1 } } });
    const line = await screen.findByTestId("worker-failure-reason");
    expect(line.textContent).toContain(getErrorMessage("api_key_not_set"));
    expect(line.textContent).not.toMatch(/OpenRouterError/);
  });

  it("says a failed write cost money and a failed prompt did not",
     async () => {
    // The two structural shapes, and they are opposites: `write_*` happens
    // after the reply has been sent, generated and billed; `prompt_*`
    // happens before anything leaves the machine. One sentence for both
    // would tell a reader they had paid for a call that was never made.
    mount({ stats: { done: 0, failed: 2, skipped: 0, abandoned: 0,
                     paid_and_lost: 1, skip_reasons: {},
                     error_types: { write_RuntimeError: 1,
                                    prompt_KeyError: 1 } } });
    const lines = (await screen.findAllByTestId("worker-failure-reason"))
      .map((p) => p.textContent ?? "");
    const write = lines.find((t) => t.includes("write_RuntimeError")) ?? "";
    const prompt = lines.find((t) => t.includes("prompt_KeyError")) ?? "";
    expect(write).toMatch(/was paid for/i);
    expect(prompt).toMatch(/nothing was sent and nothing was spent/i);
  });

  it("every reason it has words for reaches the screen as words", async () => {
    // Walks the table by its own keys rather than a list retyped here: a
    // token added without a sentence is the exact failure the table exists
    // to stop, and a hand-kept copy would not notice.
    for (const token of Object.keys(FAILURE_PROSE)) {
      mount({ stats: { done: 0, failed: 1, skipped: 0, abandoned: 0,
                       paid_and_lost: 0, skip_reasons: {},
                       error_types: { [token]: 1 } } });
      const line = await screen.findByTestId("worker-failure-reason");
      // BOTH halves, and the emptiness check before them. Asserting only
      // that the token is absent is satisfied by a table whose sentences
      // are all "", and `toContain("")` is satisfied by anything at all -
      // so a key stripped of its words would pass two assertions that look
      // like they were written to catch exactly that.
      expect(FAILURE_PROSE[token], token).not.toEqual("");
      expect(line.textContent, token).toContain(FAILURE_PROSE[token]);
      expect(line.textContent, token).not.toMatch(new RegExp(token));
      cleanup();
    }
  });

  it("GROUND: a class it has never heard of still reads as English",
     async () => {
    // The vocabulary is open-ended - the backend writes an exception class
    // name with no allow-list in front of it - so the unnamed case is the
    // ordinary one here, not the exception. The token rides in brackets for
    // a bug report; it must not BE the line.
    const unknown = "SomeFutureError";
    expect(FAILURE_PROSE[unknown]).toBeUndefined();
    mount({ stats: { done: 0, failed: 1, skipped: 0, abandoned: 0,
                     paid_and_lost: 0, skip_reasons: {},
                     error_types: { [unknown]: 1 } } });
    const line = await screen.findByTestId("worker-failure-reason");
    expect(line.textContent).toMatch(/no words for/i);
    expect((line.textContent ?? "").replace(/\s+/g, " ").trim())
      .not.toEqual(`1 of 1 failures: ${unknown}`);
  });

  it("refuses to echo a value that is not a plain token", async () => {
    // `error_type` is a database column with NO vocabulary gate, and one of
    // its writers stores the TEXT of an exception rather than a class name.
    // Every one of those texts is a bare token today. Rendering the column
    // is what makes that a promise about the future, so the echo is refused
    // for anything that does not look like one - the count and its sentence
    // still reach the reader, the sentence-shaped value does not.
    const leaky = "bad shape: Dr Marta said 'no' at C:/vault/x.db";
    expect(echoable(leaky)).toBe(false);
    mount({ stats: { done: 0, failed: 1, skipped: 0, abandoned: 0,
                     paid_and_lost: 0, skip_reasons: {},
                     error_types: { [leaky]: 1 } } });
    const line = await screen.findByTestId("worker-failure-reason");
    expect(line.textContent).toMatch(/1 of 1 failures/);
    expect(line.textContent).toMatch(/no words for/i);
    expect(line.textContent).not.toContain("Marta");
    expect(line.textContent).not.toContain("vault");
  });

  it("POSITIVE CONTROL: it still echoes a value that IS a plain token",
     async () => {
    // Without this the test above is satisfied by a fallback that echoes
    // nothing at all, which would take the one thing a bug report needs.
    const token = "SomeFutureError:some_future_reason";
    expect(echoable(token)).toBe(true);
    mount({ stats: { done: 0, failed: 1, skipped: 0, abandoned: 0,
                     paid_and_lost: 0, skip_reasons: {},
                     error_types: { [token]: 1 } } });
    const line = await screen.findByTestId("worker-failure-reason");
    expect(line.textContent).toContain("(" + token + ")");
  });

  it("keeps the abandoned line as its own count beside the breakdown",
     async () => {
    // `abandoned` is counted INSIDE `failed`, so it is the one number here
    // that may never be added to its neighbours. The census reports the
    // same rows under their own name; it does not replace the counter, and
    // a change that swallowed the counter would leave the reader with a
    // dict whose shape invites exactly the sum that must not be made.
    mount({ stats: { done: 0, failed: 2, skipped: 0, abandoned: 2,
                     paid_and_lost: 2, skip_reasons: {},
                     error_types: { abandoned_in_flight: 2 } } });
    const line = await screen.findByTestId("worker-abandoned");
    expect(line.textContent).toMatch(/2 runs were cut off/);
    const census = await screen.findByTestId("worker-failure-reason");
    expect(census.textContent).toContain(FAILURE_PROSE.abandoned_in_flight);
    expect(census.textContent).toMatch(/2 of 2 failures/);
    expect(census.textContent).not.toMatch(/abandoned_in_flight/);
  });
});

/**
 * WHAT IT OFFERED AND WHAT WAS THROWN AWAY.
 *
 * Two counters the backend has computed and declared on the wire for as long
 * as they have existed, and that no screen rendered: the parser's own
 * refusals, and the replies that were paid for and landed nothing. Between
 * them they are the difference between "the model had nothing to say" and "a
 * gate is discarding your notes", which arrived as the same empty list.
 */
describe("what the reader paid for and did not get", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => {
    vi.restoreAllMocks();
    cleanup();
  });

  it("says when a check threw away notes, and admits that check is not free",
     async () => {
    // The anchor gate catches nine invented notes in ten and drops four
    // true notes in twenty. That second number reaches a person here or
    // nowhere, and without it a missing note looks like a model that found
    // nothing rather than a gate that was too strict.
    mount({ worker: { dropped: { unanchored: 4 } } });
    const line = await screen.findByTestId("worker-dropped-note");
    expect(line.textContent).toMatch(/4 suggestions were thrown away/i);
    expect(line.textContent).toMatch(/no name and no number/i);
    // The count is plural, so the reason may not be a sentence about one of
    // them: "4 suggestions were thrown away: nothing in IT" was the reading.
    expect(line.textContent).not.toMatch(/away: nothing in it/i);
    expect(line.textContent).toMatch(/one true note in five/i);
    expect(line.textContent).not.toMatch(/unanchored/);
  });

  it("GROUND: says nothing when the parser threw nothing away", async () => {
    mount({ worker: { dropped: {} } });
    await screen.findByTestId("worker-status");
    expect(screen.queryByTestId("worker-dropped-note")).toBeNull();
  });

  it("every drop reason it declares reaches the screen as words", async () => {
    for (const reason of Object.keys(DROP_PROSE)) {
      mount({ worker: { dropped: { [reason]: 1 } } });
      const line = await screen.findByTestId("worker-dropped-note");
      expect(DROP_PROSE[reason], reason).not.toEqual("");
      expect(line.textContent, reason).toContain(DROP_PROSE[reason]);
      expect(line.textContent, reason).not.toMatch(new RegExp(reason));
      cleanup();
    }
  });

  it("GROUND: a drop reason with no sentence still reads as English",
     async () => {
    const unknown = "some_future_gate";
    expect(DROP_PROSE[unknown]).toBeUndefined();
    mount({ worker: { dropped: { [unknown]: 1 } } });
    const line = await screen.findByTestId("worker-dropped-note");
    expect(line.textContent).toMatch(/no words for/i);
    // POSITIVE CONTROL for the negative below: the token IS carried, in
    // brackets, so a bug report can still quote it. The negative says the
    // line must not BE the token, which is what the old fallback rendered.
    expect(line.textContent).toContain("(" + unknown + ")");
    expect((line.textContent ?? "").replace(/\s+/g, " ").trim())
      .not.toEqual(`1 suggestion was thrown away: ${unknown}.`);
  });

  it("says when a paid reply landed nothing at all", async () => {
    // Not a failure and not a refusal: the call went out, came back and was
    // billed, and nothing could be landed from it. Reported by a counter
    // and a code that reached no screen, so on the panel it looked exactly
    // like a quiet week.
    mount({ worker: { settled_empty: 9 } });
    const line = await screen.findByTestId("worker-settled-empty");
    expect(line.textContent).toMatch(/9 replies were paid for/i);
    expect(line.textContent).toMatch(/produced no notes/i);
  });

  it("GROUND: says nothing when every reply landed something", async () => {
    mount({ worker: { settled_empty: 0 } });
    await screen.findByTestId("worker-status");
    expect(hiddenFromReaders(screen.queryByTestId("worker-settled-empty"))).toBe(true);
  });
});

/**
 * THE EMPTY SETTLE, WHICH HAD NO WAY ONTO THE SCREEN AT ALL.
 *
 * A reply that is paid for and lands nothing does not increment `unhandled`,
 * does not fail, and is not refused. Its reason was written to `last_error`,
 * and `last_error` was rendered in exactly one place: inside the `unhandled`
 * block. So the whole class was invisible however good the reason string
 * was - and the reason string was being improved at the same time, into a
 * screen that could not show it.
 *
 * One value, two signals. `last_error` carries the most recent event of any
 * kind, so which line may claim it is itself a thing to prove.
 */
describe("a reply that was paid for and landed nothing", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => {
    vi.restoreAllMocks();
    cleanup();
  });

  it("names the reason the most recent one landed nothing", async () => {
    mount({ worker: { settled_empty: 3, last_error: "settled_chat_gone" } });
    const line = await screen.findByTestId("worker-settled-empty");
    expect(line.textContent).toMatch(/3 replies were paid for/i);
    expect(line.textContent).toMatch(/most recent/i);
    expect(line.textContent).toContain(EMPTY_PROSE.settled_chat_gone);
    expect(line.textContent).not.toMatch(/settled_chat_gone/);
  });

  it("GROUND: with no reason recorded it still says what the three ways are",
     async () => {
    // The half that proves the assertion above is reading the reason rather
    // than a sentence that is always there.
    mount({ worker: { settled_empty: 3, last_error: null } });
    const line = await screen.findByTestId("worker-settled-empty");
    expect(line.textContent).not.toMatch(/most recent/i);
    expect(line.textContent).toMatch(/a retry arrived first/i);
  });

  it("refuses a reason that belongs to the other signal", async () => {
    // `last_error` holds whatever happened LAST, and a failed write is not
    // an empty settle. Claimed here it would put a failure's token in a
    // sentence about a reply that landed nothing.
    mount({ worker: { settled_empty: 2, unhandled: 1,
                      last_error: "write_RuntimeError" } });
    const line = await screen.findByTestId("worker-settled-empty");
    expect(line.textContent).not.toMatch(/most recent/i);
    expect(line.textContent).not.toMatch(/write_RuntimeError/);
  });

  it("every empty reason it declares reaches the screen as words", async () => {
    for (const reason of Object.keys(EMPTY_PROSE)) {
      mount({ worker: { settled_empty: 1, last_error: reason } });
      const line = await screen.findByTestId("worker-settled-empty");
      expect(EMPTY_PROSE[reason], reason).not.toEqual("");
      expect(line.textContent, reason).toContain(EMPTY_PROSE[reason]);
      expect(line.textContent, reason).not.toMatch(new RegExp(reason));
      expect(line.textContent, reason).toMatch(/most recent/i);
      cleanup();
    }
  });

  it("GROUND: an empty reason with no sentence still reads as English",
     async () => {
    const unknown = "settled_some_future_race";
    expect(EMPTY_PROSE[unknown]).toBeUndefined();
    mount({ worker: { settled_empty: 1, last_error: unknown } });
    const line = await screen.findByTestId("worker-settled-empty");
    expect(line.textContent).toMatch(/no words for/i);
    expect(line.textContent).toContain("(" + unknown + ")");
    expect(line.textContent).toMatch(/1 reply was paid for/i);
  });

  it("does not lend the empty reason to the unexpected-error line",
     async () => {
    // Both lines read the one field. Printed unconditionally next door it
    // read "an unexpected error (settled_chat_gone)", which names the wrong
    // event with the other one's word.
    mount({ worker: { unhandled: 2, settled_empty: 1,
                      last_error: "settled_chat_gone" } });
    const line = await screen.findByTestId("worker-unhandled");
    expect(line.textContent).toMatch(/2 runs ended in an unexpected error/i);
    expect(line.textContent).not.toMatch(/settled_chat_gone/);
  });

  it("POSITIVE CONTROL: it still names a real unexpected error", async () => {
    // The assertion above says something is ABSENT, so this proves the sweep
    // can see it when it is there. A line that had simply stopped printing
    // the field would satisfy the negative and lose the signal.
    mount({ worker: { unhandled: 2, settled_empty: 0,
                      last_error: "RuntimeError" } });
    const line = await screen.findByTestId("worker-unhandled");
    expect(line.textContent).toMatch(/\(RuntimeError\)/);
  });
});

/**
 * H-17 - the breaker was split, and the reader has to be able to see which
 * half stopped.
 *
 * A chat whose own material breaks every call used to walk the ONE
 * process-wide breaker to "stopped" and take every other conversation with
 * it. Now only that chat stops - which is the right behaviour and the worse
 * screen, because the panel's global sentence goes on saying "Running." over
 * a conversation nothing will ever read. Every test here mounts the panel
 * and reads the line a person would read.
 */
describe("a chat that stopped on its own", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: 7 });
  });
  afterEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: null });
  });

  it("says so, without saying the whole feature stopped", async () => {
    mount({ worker: { state: "closed",
                      chat: { state: "stopped", failures: 20 } } });
    const box = await screen.findByTestId("worker-status");

    expect(box.textContent).toContain(STATE_PROSE.closed);
    const line = await screen.findByTestId("worker-chat-state");
    expect(line.textContent).toContain(CHAT_STATE_PROSE.stopped);
    expect(line.textContent).toMatch(/other chats keep running/i);
  });

  it("GROUND: a healthy chat gets no second line at all", async () => {
    // Without this the assertion above is satisfied by a panel that prints
    // the chat line unconditionally, which would tell every reader their
    // conversation was broken.
    mount({ worker: { state: "closed", chat: null } });
    await screen.findByTestId("worker-status");
    expect(hiddenFromReaders(screen.queryByTestId("worker-chat-state"))).toBe(true);
    expect(screen.queryByRole("button", { name: /try this chat again/i }))
      .not.toBeInTheDocument();
  });

  it("every per-chat state the backend can report reads as words",
     async () => {
    expect(Object.keys(CHAT_STATE_PROSE).length).toBeGreaterThanOrEqual(3);
    for (const state of Object.keys(CHAT_STATE_PROSE)) {
      mount({ worker: { state: "closed", chat: { state, failures: 5 } } });
      const line = await screen.findByTestId("worker-chat-state");
      expect(CHAT_STATE_PROSE[state], state).toBeTruthy();
      expect(line.textContent, state).toContain(CHAT_STATE_PROSE[state]);
      // Every one of them has to say the other chats are fine, or a reader
      // reads it as the feature being down and goes hunting for a fault
      // that is not there.
      expect(line.textContent, state).toMatch(/other chats/i);
      // And none of them may reach the screen as the raw token.
      expect(line.textContent, state).not.toEqual(state);
      cleanup();
    }
  });

  it("offers a button that unsticks only this chat", async () => {
    const user = userEvent.setup();
    mount({ worker: { state: "closed", chat: { state: "open", failures: 5 } } });
    await user.click(
      await screen.findByRole("button", { name: /try this chat again/i }));

    await waitFor(() => {
      const call = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls
        .find((c: unknown[]) =>
          String(c[0]).includes("/worker/reset")
          && (c[1] as RequestInit | undefined)?.method === "POST");
      expect(call).toBeTruthy();
      expect(String((call?.[1] as RequestInit).body)).toContain("chat_id");
      expect(String((call?.[1] as RequestInit).body)).toContain("7");
    });
  });

  it("counts the per-chat refusals apart from the global ones", async () => {
    mount({ worker: { refused_by_breaker: 0, refused_by_chat_breaker: 4,
                      tripped_chats: 2 } });
    const box = await screen.findByTestId("worker-status");
    expect(box.textContent).toMatch(/4 turns were passed over/i);
    expect(box.textContent).toMatch(/2 of them right now/i);
    expect(box.textContent).toMatch(/every other chat kept running/i);
    // GROUND for the line above: the global sentence is a different one and
    // is not printed when its own counter is zero.
    expect(readableText(box)).not.toMatch(/while it was cooling down/i);
  });

  it("asks the backend about the chat that is on screen", async () => {
    mount();
    await screen.findByTestId("worker-status");
    await waitFor(() => {
      const call = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls
        .find((c: unknown[]) => String(c[0]).includes("/notebook/worker?"));
      expect(String(call?.[0])).toContain("chat_id=7");
    });
  });
});

/**
 * The sweep's refusals were translated by an inline ternary whose last arm
 * was a catch-all reading "It is already reading." The worker now refuses a
 * paused chat, and that arm would have told the reader the exact opposite of
 * what was happening.
 */
describe("why the sweep refused", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: 7 });
  });
  afterEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: null });
  });

  it("a paused chat is not told it is already reading", async () => {
    mount({}, true, {
      "POST /notebook/sweep/7": {
        body: { started: false, reason: "chat_paused" },
      },
    });
    await userEvent.click(await screen.findByTestId("worker-sweep"));
    const said = (await screen.findByTestId("worker-sweep-said")).textContent;
    expect(said).toContain(SWEEP_PROSE.chat_paused);
    expect(said).not.toMatch(/already reading/i);
    expect(said).not.toMatch(/chat_paused/);
  });

  it("a stopped chat is told what to press", async () => {
    mount({}, true, {
      "POST /notebook/sweep/7": {
        body: { started: false, reason: "chat_stopped" },
      },
    });
    await userEvent.click(await screen.findByTestId("worker-sweep"));
    const said = (await screen.findByTestId("worker-sweep-said")).textContent;
    expect(said).toContain(SWEEP_PROSE.chat_stopped);
    expect(said).not.toMatch(/already reading/i);
  });

  it("every refusal in the table reaches the screen as words", async () => {
    expect(Object.keys(SWEEP_PROSE).length).toBeGreaterThanOrEqual(5);
    for (const reason of Object.keys(SWEEP_PROSE)) {
      mount({}, true, {
        "POST /notebook/sweep/7": { body: { started: false, reason } },
      });
      await userEvent.click(await screen.findByTestId("worker-sweep"));
      const said =
        (await screen.findByTestId("worker-sweep-said")).textContent ?? "";
      expect(SWEEP_PROSE[reason], reason).toBeTruthy();
      expect(said, reason).toContain(SWEEP_PROSE[reason]);
      expect(said, reason).not.toMatch(new RegExp(reason));
      cleanup();
    }
  });

  it("a refusal nobody wrote words for is named, not printed raw",
     async () => {
    // The fallback. It is the right answer for a reason from a newer
    // backend; what it may not be is the catch-all sentence about a chat
    // that is already reading.
    mount({}, true, {
      "POST /notebook/sweep/7": {
        body: { started: false, reason: "some_future_reason" },
      },
    });
    await userEvent.click(await screen.findByTestId("worker-sweep"));
    const said = (await screen.findByTestId("worker-sweep-said")).textContent;
    expect(said).toMatch(/did not start/i);
    expect(said).not.toMatch(/already reading/i);
  });
});

/**
 * The button appeared for "open" and "stopped" and not for "half_open" - the
 * one state whose sentence tells the reader a BILLED trial call is about to
 * go out. The state that asks for a decision was the state with no way to
 * make one.
 */
describe("the reset button covers every state that is stuck", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it("is offered while the cooldown has elapsed and a billed call is next",
     async () => {
    mount({ worker: { state: "half_open" } });
    const box = await screen.findByTestId("worker-status");
    expect(box.textContent).toMatch(/one trial call/i);
    expect(await screen.findByRole("button", { name: /try again now/i }))
      .toBeEnabled();
  });

  it("GROUND: it is still absent while nothing is stuck", async () => {
    mount({ worker: { state: "closed" } });
    await screen.findByTestId("worker-status");
    expect(screen.queryByRole("button", { name: /try again now/i }))
      .not.toBeInTheDocument();
  });
});
