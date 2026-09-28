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
  CHAT_SCOPED_SKIPS,
  STATE_PROSE,
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
    // Required and nullable on the wire: the block is what says a job is
    // running, so a backend that stopped sending it must fail the parse
    // rather than read as "no job".
    backfill: null,
    ...((over.worker as object) ?? {}),
  };
  const stats = {
    done: 3, failed: 0, skipped: 0, skip_reasons: {},
    ...((over.stats as object) ?? {}),
  };
  return {
    ...(over.chat_refusal !== undefined
      ? { chat_refusal: over.chat_refusal } : {}),
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

  it("the card carries no live region any more, the strip is the answer",
     async () => {
    // The one node here that declared `role="status"` was the paragraph
    // answering a sweep press (`worker-sweep-said`). The press moved to
    // the strip and the answer moved with it, role included, because a
    // press whose refusal is announced to nobody is worse than a tidy
    // count.
    //
    // So the Notes tab's live-region count is UNCHANGED by this unit: one
    // left here, one arrived there. The recorded measurement expects a
    // drop - "Notes sekmesinin tamaminda 1; bugun alti (IP-01 sonrasi
    // bes)" - and that drop does not happen. IP-02 inherits exactly the
    // consolidation it was always going to do, and it inherits it as a
    // measured number rather than as a prediction. The other half of the
    // move is pinned in BackfillStrip.test.tsx, "the answer to a press is
    // a live region, and the ticking counter is not".
    mount();
    const box = await screen.findByTestId("worker-status");
    // GROUND: the card rendered its own sentences, so this counts over
    // something rather than over an empty tree.
    expect(readableText(box)).not.toBe("");
    // POSITIVE CONTROL for the absence: role queries do reach this render
    // - the same screen finds the card's own heading by role.
    expect(screen.getAllByRole("heading").length).toBeGreaterThan(0);
    expect(screen.queryAllByRole("status")).toHaveLength(0);
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
    // 4 - 3, not 4 - 1, and one run is one run.
    expect(box.textContent).toMatch(/(^|\D)1 run failed\./);
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
      // A chat-scoped reason reaches the screen through `chat_refusal`, the
      // backend's answer about the chat on screen, and never through the
      // install-wide count; the next test holds that split.
      const scoped = CHAT_SCOPED_SKIPS.has(reason);
      mount({ stats: { done: 0, failed: 0, skipped: 1, abandoned: 0,
                       skip_reasons: { [reason]: 1 } },
              ...(scoped ? { chat_refusal: reason } : {}) });
      const box = await screen.findByTestId("worker-status");
      const line = scoped
        ? (await screen.findByTestId("worker-chat-refusal")).textContent ?? ""
        : Array.from(box.querySelectorAll("p"))
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

  it("says the unfit-model sentence about this chat only, never from the install-wide count",
     async () => {
    // 3.8. `stats.skip_reasons` counts every chat, and the
    // sentence says "the note model this chat uses". Shown from the count,
    // a chat on a working model was told its model was broken because
    // another chat's was. The backend now says it per chat, in
    // `chat_refusal`, and the count is not rendered for it at all.
    const reason = "model_not_extractable";
    expect(CHAT_SCOPED_SKIPS.has(reason)).toBe(true);

    // Another chat was refused; this one was not.
    mount({ stats: { done: 0, failed: 0, skipped: 2, abandoned: 0,
                     skip_reasons: { [reason]: 1, proxy_gate: 1 } },
            chat_refusal: null });
    const box = await screen.findByTestId("worker-status");
    // GROUND: the install-wide list is rendered, so the absence below is
    // about this reason and not an empty card.
    expect(box.textContent).toContain(SKIP_PROSE.proxy_gate);
    expect(box.textContent).not.toContain(SKIP_PROSE[reason]);
    // The line is held closed in its Collapse (KURAL 5.5), so absent means
    // out of the accessibility tree and saying nothing.
    const closed = screen.queryByTestId("worker-chat-refusal");
    expect(hiddenFromReaders(closed)).toBe(true);
    expect(closed?.textContent ?? "").toBe("");
    cleanup();

    // POSITIVE CONTROL: this chat's own standing refusal is said, once,
    // with no count in front of it.
    mount({ stats: { done: 0, failed: 0, skipped: 1, abandoned: 0,
                     skip_reasons: { [reason]: 7 } },
            chat_refusal: reason });
    const said = await screen.findByTestId("worker-chat-refusal");
    await waitFor(() => expect(hiddenFromReaders(said)).toBe(false));
    expect(said.textContent).toContain(SKIP_PROSE[reason]);
    expect(said.textContent).not.toMatch(/\d+ skipped/);
    const all = (await screen.findByTestId("worker-status")).textContent ?? "";
    expect(all.split(SKIP_PROSE[reason]).length - 1).toBe(1);
  });

  it("keeps the refusal's words while its line closes", async () => {
    // The line closes through a Collapse, which animates
    // its content's height down, so the content has to still be there
    // while it closes; `useHeldValue` keeps the last reason after the poll
    // answers null. Without it the words vanish first and the box snaps
    // shut. Held only by a headless browser run until this test.
    const reason = "model_not_extractable";
    const { queryClient } = mount({ chat_refusal: reason });
    const line = await screen.findByTestId("worker-chat-refusal");
    // GROUND: the line is open and says the sentence.
    await waitFor(() => expect(hiddenFromReaders(line)).toBe(false));
    expect(line.textContent).toContain(SKIP_PROSE[reason]);

    // The poll now answers that nothing stands.
    mockFetch({
      "/notebook/worker/reset": { body: { ok: true } },
      "/notebook/worker": { body: statusBody({ chat_refusal: null }) },
      "/notebook/auto-accept": {
        body: { enabled: true, effective: true, overridden: false },
      },
    });
    await queryClient.invalidateQueries();

    // Closing: out of the accessibility tree at once (the Collapse), and
    // the words are still inside it for the animation to carry down.
    const closing = screen.getByTestId("worker-chat-refusal");
    await waitFor(() => expect(hiddenFromReaders(closing)).toBe(true));
    expect(closing.textContent).toContain(SKIP_PROSE[reason]);
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

// The way back to a chat's own history and the sweep's refusals used to be
// tested here, on the worker card's "Read the earlier messages here" button.
// That button is gone: one job at a time means one door, and the door is
// the strip at the top of the Notebook panel. Every claim those tests made
// lives on in BackfillStrip.test.tsx against the strip's own routes.

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
      .not.toEqual(`1 of 1 failure: ${unknown}`);
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
    expect(line.textContent).toMatch(/1 of 1 failure:/);
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
    expect(box.textContent).toMatch(/2 chats are failing right now/i);
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

describe("every count the worker card shows, at 0, 1 and 2", () => {
  // A number on this card is followed by a noun or a verb that has to agree
  // with it: "1 runs failed" is the reading a reader trips over, and the
  // card read it that way for one run, one call, one turn. Each row renders
  // the card with the count at 0, 1 and 2 and reads the words that are ON
  // SCREEN: text in a shut Collapse is mounted and is on no screen. Where a
  // line only exists for a count above 0, the row at 0 says it is not shown
  // and the row at 1 is what proves the same reader finds it.
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  /** The card's words a reader can see, whitespace collapsed. */
  const visibleText = (card: Element): string => {
    let out = "";
    const walker = document.createTreeWalker(card, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      if (node.parentElement?.closest('[data-slot="collapse"][data-open="false"]')) continue;
      out += node.textContent ?? "";
    }
    return out.replace(/\s+/g, " ").trim();
  };

  /** The card for one body, the whole status body given, so the cap too. */
  async function cardFor(body: ReturnType<typeof statusBody>): Promise<string> {
    cleanup();
    mockFetch({
      "/notebook/worker": { body },
      "/notebook/auto-accept": { body: { enabled: true, effective: true, overridden: false } },
    });
    renderWithQueryClient(<WorkerPanel />);
    return visibleText(await screen.findByTestId("worker-status"));
  }

  const failureKey = Object.keys(FAILURE_PROSE)[0];
  const skipKey = Object.keys(SKIP_PROSE)[0];
  const dropKey = Object.keys(DROP_PROSE)[0];

  /** One wording: the body at a count, and what the card says at 0, 1 and
   *  2, or null where the line is not on screen at that count. `marker`
   *  finds the line whatever its count says, for the rows that expect it
   *  gone. */
  const WORDINGS: {
    name: string;
    body: (n: number) => ReturnType<typeof statusBody>;
    says: [string | null, string, string];
    marker: RegExp;
  }[] = [
    { name: "the runs done", body: (n) => statusBody({ stats: { done: n } }),
      says: ["0 runs \u00b7", "1 run \u00b7", "2 runs \u00b7"], marker: / \u00b7 \d+ of \d+ calls? today/ },
    { name: "the calls the day allows", body: (n) => ({ ...statusBody({ spend: { calls: 0 } }), daily_cap: n }),
      says: ["0 of 0 calls today", "0 of 1 call today", "0 of 2 calls today"], marker: /calls? today/ },
    { name: "the calls made in all", body: (n) => statusBody({ spend_lifetime: { calls: n } }),
      says: ["(0 calls,", "(1 call,", "(2 calls,"], marker: /credits lifetime\)/ },
    { name: "the calls that came back with no price",
      body: (n) => statusBody({ spend: { cost_unknown: n } }),
      says: [null, "1 of the calls made today came back with no price - the credit figure above "
        + "leaves it out rather than counting it as free.",
      "2 of the calls made today came back with no price - the credit figure above leaves "
        + "them out rather than counting them as free."], marker: /came back with no price/ },
    { name: "the runs that failed with nothing lost",
      body: (n) => statusBody({ stats: { failed: n, paid_and_lost: 0 } }),
      says: [null, "1 run failed. Nothing was lost - the messages it could not read stay unread, "
        + "not skipped.", "2 runs failed. Nothing was lost - the messages they could not read "
        + "stay unread, not skipped."], marker: /failed\. Nothing was lost/ },
    { name: "the runs cut off after they were paid for",
      body: (n) => statusBody({ stats: { failed: n, paid_and_lost: n, abandoned: n } }),
      says: [null, "1 run was cut off after the request had already been sent, so it was paid "
        + "for. Elysium does not send that stretch again, which is why the notes for it are "
        + "missing rather than duplicated.", "2 runs were cut off after the request had already "
        + "been sent, so they were paid for. Elysium does not send those stretches again, which "
        + "is why the notes for them are missing rather than duplicated."],
      marker: /cut off after the request/ },
    { name: "the failures a reason accounts for",
      body: (n) => statusBody({ stats: { failed: n, paid_and_lost: n,
        error_types: n > 0 ? { [failureKey]: n } : {} } }),
      says: [null, "1 of 1 failure:", "2 of 2 failures:"], marker: /of \d+ failures?:/ },
    { name: "the turns skipped for a reason",
      body: (n) => statusBody({ stats: { skipped: n, skip_reasons: n > 0 ? { [skipKey]: n } : {} } }),
      says: [null, "1 skipped:", "2 skipped:"], marker: /\d+ skipped:/ },
    { name: "the runs that ended in an unexpected error",
      body: (n) => statusBody({ worker: { unhandled: n } }),
      says: [null, "1 run ended in an unexpected error. Those messages stay unread.",
        "2 runs ended in an unexpected error. Those messages stay unread."],
      marker: /ended in an unexpected error/ },
    { name: "the replies paid for that produced no notes",
      body: (n) => statusBody({ worker: { settled_empty: n } }),
      says: [null, "1 reply was paid for and produced no notes",
        "2 replies were paid for and produced no notes"], marker: /paid for and produced no notes/ },
    { name: "the suggestions thrown away",
      body: (n) => statusBody({ worker: { dropped: n > 0 ? { [dropKey]: n } : {} } }),
      says: [null, "1 suggestion was thrown away:", "2 suggestions were thrown away:"],
      marker: /thrown away:/ },
    { name: "the turns passed over while it cooled down",
      body: (n) => statusBody({ worker: { refused_by_breaker: n } }),
      says: [null, "1 turn was passed over while it was cooling down. Its messages are still "
        + "unread.", "2 turns were passed over while it was cooling down. Their messages are "
        + "still unread."], marker: /while it was cooling down/ },
    { name: "the turns passed over in chats that keep failing",
      body: (n) => statusBody({ worker: { refused_by_chat_breaker: n } }),
      says: [null, "1 turn was passed over in a chat that keeps failing on its own.",
        "2 turns were passed over in chats that keep failing on their own."],
      marker: /that keeps? failing on/ },
    { name: "the chats failing right now",
      body: (n) => statusBody({ worker: { refused_by_chat_breaker: 1, tripped_chats: n } }),
      says: ["keeps failing on its own. Every other chat kept running.",
        "keeps failing on its own (1 chat is failing right now). Every other chat kept running.",
        "keeps failing on its own (2 chats are failing right now). Every other chat kept running."],
      marker: /Every other chat kept running/ },
    { name: "the turns that went unqueued",
      body: (n) => statusBody({ worker: { dropped_offers: n } }),
      says: [null, "1 turn went unqueued while it was behind. Its messages are still unread, so a "
        + "later run picks them up.", "2 turns went unqueued while it was behind. Their "
        + "messages are still unread, so a later run picks them up."], marker: /went unqueued/ },
  ];

  it.each(WORDINGS.map((w) => [w.name, w] as const))("%s", async (_name, wording) => {
    for (const n of [0, 1, 2] as const) {
      const text = await cardFor(wording.body(n));
      const says = wording.says[n];
      if (says === null) {
        expect(text, `at ${n} the line is on screen`).not.toMatch(wording.marker);
      } else {
        expect(text, `at ${n}`).toContain(says);
        // GROUND for the rows that expect a line gone: the marker finds the
        // line wherever it is shown.
        expect(text, `the marker does not find the line at ${n}`).toMatch(wording.marker);
      }
    }
  }, 30_000);
});
