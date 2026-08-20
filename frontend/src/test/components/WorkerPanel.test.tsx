/**
 * FAZ 5 - the screen that answers "what did it do with my money".
 *
 * The feature spends the user's own credits unattended. Two things can go
 * wrong: a loop that will not stop, and a refusal nobody can see. These tests
 * are about the second - every counter here exists because without it a
 * refused run and a quiet week look identical.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { WorkerPanel } from "@/components/notebook/WorkerPanel";
import { mockFetch } from "../mocks/api";

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

function mount(over: Record<string, unknown> = {}, enabled = true) {
  mockFetch({
    "/notebook/worker/reset": { body: { ok: true } },
    "/notebook/worker": { body: statusBody(over) },
    "/notebook/auto-accept": { body: { enabled } },
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
  // The money case. `abandoned` rows carry status 'failed' too, so the panel
  // used to report them all under one line saying "nothing was lost" - while
  // the backend counted them separately precisely because something WAS
  // lost: the call was billed and those messages are never read.
  it("says the run was paid for rather than that nothing was lost", async () => {
    mount({ stats: { done: 3, failed: 2, skipped: 0, abandoned: 2,
                     skip_reasons: {} } });
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
    mount({ stats: { done: 3, failed: 2, skipped: 0, abandoned: 0,
                     skip_reasons: {} } });
    const box = await screen.findByTestId("worker-status");
    expect(box.textContent).toMatch(/2 runs failed/);
    expect(box.textContent).toMatch(/Nothing was lost/i);
    expect(screen.queryByTestId("worker-abandoned")).not.toBeInTheDocument();
  });

  it("counts the two apart when both happened", async () => {
    mount({ stats: { done: 1, failed: 5, skipped: 0, abandoned: 2,
                     skip_reasons: {} } });
    const box = await screen.findByTestId("worker-status");
    // Five failed, two of them abandoned, so three ordinary ones. Added
    // together instead of nested it would read seven.
    expect(box.textContent).toMatch(/3 runs failed/);
    expect(box.textContent).toMatch(/2 runs were cut off/);
  });

  it("says a withdrawn edit in words, not as a raw code", async () => {
    // This skip reason is written by a different path from the worker's own,
    // so the gate that keeps the prose table complete never covered it and
    // the panel printed the token itself at the reader.
    mount({ stats: { done: 0, failed: 0, skipped: 1, abandoned: 0,
                     skip_reasons: { plan_invalidated: 1 } } });
    const box = await screen.findByTestId("worker-status");
    expect(box.textContent).not.toMatch(/plan_invalidated/);
    expect(box.textContent).toMatch(/wording you took back/i);
  });
});
