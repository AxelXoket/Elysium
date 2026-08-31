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
import { SKIP_PROSE, WorkerPanel } from "@/components/notebook/WorkerPanel";
import { mockFetch } from "../mocks/api";
import { useUiStore } from "@/lib/store/uiStore";

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
    expect(screen.queryByTestId("worker-cost-unknown")).toBeNull();
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
    expect(screen.getByTestId("worker-status").textContent)
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
    expect(screen.queryByTestId("worker-abandoned")).not.toBeInTheDocument();
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
      expect(box.textContent).not.toMatch(/Nothing was lost/i);
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

  it("every declared reason reaches the screen as words, not its token",
     async () => {
    // Walks the vocabulary itself rather than a list retyped here: a reason
    // added to the table without a sentence is exactly the failure the
    // table exists to stop, and a hand-kept copy would not notice.
    for (const reason of Object.keys(SKIP_PROSE)) {
      mount({ stats: { done: 0, failed: 0, skipped: 1, abandoned: 0,
                       skip_reasons: { [reason]: 1 } } });
      const box = await screen.findByTestId("worker-status");
      expect(box.textContent).not.toMatch(new RegExp(reason));
      cleanup();
    }
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
    expect(screen.queryByTestId("worker-sweep")).toBeNull();
  });

  it("says it started when there was something unread", async () => {
    mount({}, true, {
      "POST /notebook/sweep/7": { body: { started: true, after_id: 0 } },
    });
    await userEvent.click(await screen.findByTestId("worker-sweep"));
    expect((await screen.findByTestId("worker-sweep-said")).textContent)
      .toMatch(/reading the earlier part/i);
  });

  it("says nothing is unread, and does not read as a failure", async () => {
    mount({}, true, {
      "POST /notebook/sweep/7": {
        body: { started: false, reason: "nothing_unread" },
      },
    });
    await userEvent.click(await screen.findByTestId("worker-sweep"));
    expect((await screen.findByTestId("worker-sweep-said")).textContent)
      .toMatch(/nothing here is unread/i);
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
    expect(screen.queryByTestId("worker-backlog")).toBeNull();
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
    expect(screen.queryByTestId("chat-auto-accept")).toBeNull();
  });
});

