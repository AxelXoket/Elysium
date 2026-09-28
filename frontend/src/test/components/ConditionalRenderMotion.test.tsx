/**
 * ConditionalRenderMotion.test.tsx - every conditional block in the four
 * notebook panels arrives and leaves through the house motion (FIX 6, FIX 7).
 *
 * Twenty conditional renders in NotebookPanel and twenty-three across
 * BoundaryPanel, WorkerPanel and ExtractionSettings used to appear and
 * vanish in one frame. The wrapper each one got is decided by one criterion,
 * not by taste: if the block changes the document's height when it appears
 * it is a `Collapse` (the height animates, the content stays mounted and is
 * taken out of the accessibility tree while closed); if it lives inside an
 * existing row it is a `FadeIn` (opacity only, unmounted when false); the
 * two two-branch swaps take motion's `AnimatePresence` with `mode="wait"`
 * directly, because a replacement is the one shape `components/motion/` has
 * no primitive for; the tests ask for those through `data-slot="cross-fade"`.
 *
 * The absence criterion is ONE sentence for both wrappers, and it is the
 * reader's: when the condition is false the block is not in the
 * accessibility tree. For `FadeIn` that is the node not being there; for
 * `Collapse` it is `inert` plus `aria-hidden` on a wrapper whose content is
 * deliberately still in the DOM, and the second half is asserted too, so a
 * `Collapse` that lost either attribute goes red rather than passing as
 * "not rendered". Presence is asked through the wrapper's own `data-slot`.
 *
 * Out of scope by name, as the plan lists them: NotebookPanel's seven
 * inline note labels (`state === "retired"` and friends inside a row),
 * the Pin/PinOff icon swap, and the model-note paragraph that IP-02 wraps.
 */
import { CENSUS, WORKER_DEAD } from "@/components/notebook/workerProse";
import { DISCLOSURE_WORDS } from "@/components/notebook/limitsProse";
import { says } from "@/test/helpers/prose";
import { UNDO_RUN_CONFIRM } from "@/components/notebook/backfillProse";
import {
  CHOICE_UNREAD, LIMITS_UNREAD, NOTES_UNREAD, NOTE_ROW_WORDS,
} from "@/components/notebook/noteProse";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useEffect, useState } from "react";

import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { hiddenFromReaders } from "@/test/helpers/accessibility";
import { mockFetch } from "@/test/mocks/api";
import { useUiStore } from "@/lib/store/uiStore";
import { NotebookPanel } from "@/components/notebook/NotebookPanel";
import { BoundaryPanel } from "@/components/notebook/BoundaryPanel";
import { WorkerPanel } from "@/components/notebook/WorkerPanel";
import { BackfillStrip } from "@/components/notebook/BackfillStrip";
import { ExtractionSettings } from "@/components/notebook/ExtractionSettings";
import { CrossFade } from "@/components/motion/CrossFade";
import { durationSeconds } from "@/lib/theme/tokens";
import type { BackfillStatus } from "@/lib/schemas/notebook";

type Wrapper = "collapse" | "fade-in" | "cross-fade";

/** The block is on screen, reachable, and inside the wrapper the criterion picked. */
function expectShownInside(node: HTMLElement | null, wrapper: Wrapper, what: string) {
  expect(node, `${what} is not on screen`).not.toBeNull();
  expect(hiddenFromReaders(node), `${what} is on screen but hidden from readers`).toBe(false);
  const host = node!.closest(`[data-slot="${wrapper}"]`);
  expect(host, `${what} is not inside a ${wrapper}`).not.toBeNull();
  if (wrapper === "collapse") expect(host).toHaveAttribute("data-open", "true");
}

/** The one absence criterion: not in the accessibility tree. */
function expectGoneForReaders(node: HTMLElement | null, what: string) {
  expect(hiddenFromReaders(node), `${what} is still reachable`).toBe(true);
}

/** A closed Collapse: the content is still mounted, and the wrapper's own
 *  attributes are what take it away from readers and from the tab order. */
function expectHeldClosed(node: HTMLElement | null, what: string) {
  expect(node, `${what} was unmounted; Collapse keeps its content`).not.toBeNull();
  expectGoneForReaders(node, what);
  const host = node!.closest('[data-slot="collapse"]');
  expect(host, `${what} is not inside a collapse`).not.toBeNull();
  expect(host).toHaveAttribute("data-open", "false");
  expect(host).toHaveAttribute("aria-hidden", "true");
  expect(host!.hasAttribute("inert"), `${what}'s collapse is not inert`).toBe(true);
}

/** A line of text by its words, through the accessibility tree. The
 *  notebook lines are plain paragraphs, live regions or alerts, so all
 *  three roles are searched; a closed Collapse hides them from all three. */
/** A whole line, matched exactly, from the imported constant. */
function exactly(words: string): RegExp {
  return new RegExp(`^${words.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`);
}

function paragraph(re: RegExp): HTMLElement | null {
  return [
    ...screen.queryAllByRole("paragraph"),
    ...screen.queryAllByRole("status"),
    ...screen.queryAllByRole("alert"),
  ].find((p) => re.test(p.textContent ?? "")) ?? null;
}

/** The same paragraph whether or not readers can reach it. */
function paragraphInDom(re: RegExp): HTMLElement | null {
  return screen.queryByText(re);
}

function entry(over: Partial<Record<string, unknown>> = {}) {
  return {
    id: 1, chat_id: 7, position: 0, kind: "fact", text: "Mira is her sister.",
    evidence: null, durability: "permanent", importance: 2, pinned: 0,
    retired_at: null, superseded_by: null, excluded_reason: null,
    status: "accepted", provenance: "user", source_message_id: null,
    created_at: "2026-08-19", updated_at: "2026-08-19", ...over,
  };
}

const modelNote = (over: Partial<Record<string, unknown>> = {}) =>
  entry({ id: 5, text: "Her brother owns the mill.", provenance: "model",
          evidence: "kardesi degirmenin sahibi", ...over });

describe("NotebookPanel", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: 7 });
  });
  afterEach(() => vi.restoreAllMocks());

  it("while loading: the loading line is open, every other section is held closed", async () => {
    // A fetch that never answers holds the panel in its loading state.
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
    renderWithQueryClient(<NotebookPanel />);

    expectShownInside(paragraph(/^loading/i), "collapse", "the loading line");
    expectHeldClosed(paragraphInDom(exactly(NOTES_UNREAD)), "the error line");
    expectHeldClosed(paragraphInDom(/nothing yet/i), "the empty state");
    expectHeldClosed(paragraphInDom(/no note matches that/i), "the no-match line");
    // The strip and the search box are Collapses too: mounted, unreachable.
    // ByTestId: the strip's role (status) is shared with the sent count.
    expectHeldClosed(screen.queryByTestId("just-saved"), "the just-saved strip");
    expect(screen.queryByRole("textbox", { name: /search notes/i })).toBeNull();
    expectHeldClosed(screen.queryByLabelText(/search notes/i), "the search box");
  });

  it("on a failed load: the error line opens and the empty state stays closed", async () => {
    mockFetch({ "/notebook/7": { status: 500, body: { detail: "boom" } } });
    renderWithQueryClient(<NotebookPanel />);
    await waitFor(() =>
      expectShownInside(paragraph(exactly(NOTES_UNREAD)), "collapse", "the error line"));
    expectHeldClosed(paragraphInDom(/^loading/i), "the loading line");
    expectHeldClosed(paragraphInDom(/nothing yet/i), "the empty state");
  });

  it("on an empty notebook: the empty state opens and the no-match line stays closed", async () => {
    mockFetch({ "/notebook/7": { body: { entries: [], notebook_chars: 0 } } });
    renderWithQueryClient(<NotebookPanel />);
    await waitFor(() =>
      expectShownInside(paragraph(/nothing yet/i), "collapse", "the empty state"));
    expectHeldClosed(paragraphInDom(/no note matches that/i), "the no-match line");
    expectHeldClosed(paragraphInDom(exactly(NOTES_UNREAD)), "the error line");
  });

  it("over a long list: the search box opens, and a query opens the clear button and the no-match line", async () => {
    const user = userEvent.setup();
    mockFetch({
      "/notebook/7": {
        body: {
          entries: Array.from({ length: 6 }, (_, i) => entry({ id: 100 + i, text: `filler note ${i}` })),
          notebook_chars: 10,
        },
      },
    });
    renderWithQueryClient(<NotebookPanel />);
    const box = await screen.findByRole("textbox", { name: /search notes/i });
    expectShownInside(box, "collapse", "the search box");
    // Nothing typed yet: the clear button is a FadeIn, so it is simply absent.
    expectGoneForReaders(screen.queryByRole("button", { name: /clear search/i }), "the clear button");
    expect(screen.queryByRole("button", { name: /clear search/i })).toBeNull();

    await user.type(box, "zzzz");
    expectShownInside(
      await screen.findByRole("button", { name: /clear search/i }), "fade-in", "the clear button");
    expectShownInside(paragraph(/no note matches that/i), "collapse", "the no-match line");
    expectHeldClosed(paragraphInDom(/nothing yet/i), "the empty state");
  });

  it("a user's own note: the strip stays closed, the delete trigger fades in, and the confirm pair opens in its place", async () => {
    const user = userEvent.setup();
    mockFetch({ "/notebook/7": { body: { entries: [entry()], notebook_chars: 10 } } });
    renderWithQueryClient(<NotebookPanel />);
    const trigger = await screen.findByRole("button", { name: NOTE_ROW_WORDS.delete });
    expectShownInside(trigger, "fade-in", "the delete trigger");
    expectHeldClosed(screen.queryByTestId("just-saved"), "the just-saved strip");
    // An accepted note has no Keep-suggestion button; FadeIn means not rendered.
    expect(screen.queryByRole("button", { name: NOTE_ROW_WORDS.keep })).toBeNull();
    expect(screen.queryByRole("button", { name: NOTE_ROW_WORDS.confirmDelete })).toBeNull();

    await user.click(trigger);
    const confirm = await screen.findByRole("button", { name: NOTE_ROW_WORDS.confirmDelete });
    expectShownInside(confirm, "fade-in", "the confirm button");
    const cancel = screen.getByRole("button", { name: NOTE_ROW_WORDS.cancelDelete });
    expectShownInside(cancel, "fade-in", "the cancel button");
    // The pair shares one fade, under the words, in a Collapse.
    expect(confirm.closest('[data-slot="fade-in"]')).toBe(cancel.closest('[data-slot="fade-in"]'));
    expectShownInside(confirm, "collapse", "the delete question");
    // The trigger keeps its place but is out of reach while the question is open.
    expect(screen.queryByRole("button", { name: NOTE_ROW_WORDS.delete })).toBeNull();
  });

  it("a proposal: the Keep-suggestion button fades in", async () => {
    mockFetch({ "/notebook/7": { body: { entries: [modelNote({ status: "proposed" })], notebook_chars: 10 } } });
    renderWithQueryClient(<NotebookPanel />);
    expectShownInside(
      await screen.findByRole("button", { name: NOTE_ROW_WORDS.keep }), "fade-in", "the accept button");
  });

  it("a note the model saved: the strip opens, and Undo cross-fades into its confirmation", async () => {
    const user = userEvent.setup();
    mockFetch({ "/notebook/7": { body: { entries: [modelNote()], notebook_chars: 10 } } });
    renderWithQueryClient(<NotebookPanel />);
    const undo = await screen.findByRole("button", { name: /^undo$/i });
    expectShownInside(undo, "collapse", "the just-saved strip");
    const undoPane = undo.closest('[data-slot="cross-fade"]');
    expect(undoPane, "Undo is not inside the cross-fade").not.toBeNull();
    expect(undoPane).toHaveAttribute("data-pane", "undo");

    await user.click(undo);
    // mode="wait": the confirm pane mounts once the undo pane has left.
    const confirm = await screen.findByRole("button", { name: /^remove it$/i });
    expect(confirm.closest('[data-slot="cross-fade"]')).toHaveAttribute("data-pane", "confirm");
    expect(screen.queryByRole("button", { name: /^undo$/i })).toBeNull();
  });

  it("adding a note: the plus cross-fades into the spinner while the save is in flight", async () => {
    const user = userEvent.setup();
    const answered = mockFetch({ "/notebook/7": { body: { entries: [entry()], notebook_chars: 10 } } });
    // The POST never answers, so the pending state can be looked at.
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) =>
      (init?.method ?? "GET") === "POST"
        ? new Promise<Response>(() => {})
        : answered(input, init)));
    renderWithQueryClient(<NotebookPanel />);
    const add = await screen.findByRole("button", { name: /add note/i });
    const pane = () => add.querySelector('[data-slot="cross-fade"]');
    expect(pane(), "the add button's icon is not inside the cross-fade").not.toBeNull();
    expect(pane()).toHaveAttribute("data-pane", "idle");

    await user.type(screen.getByRole("textbox", { name: /new note/i }), "Something new");
    await user.click(add);
    await waitFor(() => expect(pane()).toHaveAttribute("data-pane", "busy"));
    // Class query, justified: the spinner is a decorative svg with no role
    // or name, and `.animate-spin` is the class the spinner slow-down keys on.
    expect(pane()!.querySelector(".animate-spin"), "no spinner in the busy pane").not.toBeNull();
  });
});

function boundary(over: Partial<Record<string, unknown>> = {}) {
  return {
    id: 1, scope: "global", chat_id: null, label: "no gore",
    phrasing: "Avoid graphic injury.", severity: "hard", polarity: "avoid",
    on_violation: "pause", source: "explicit", rating_ceiling: null,
    exempt_from_trim: 1, last_confirmed_at: null, active: 1,
    created_at: "2026-08-19", updated_at: "2026-08-19", ...over,
  };
}

describe("BoundaryPanel", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: 7 });
  });
  afterEach(() => vi.restoreAllMocks());

  it("with a chat open and a limit: the global switch is open, the empty and error lines are held closed, the delete trigger fades", async () => {
    const user = userEvent.setup();
    mockFetch({ "/notebook/7/boundaries": { body: { boundaries: [boundary()], use_global: true } } });
    renderWithQueryClient(<BoundaryPanel />);
    expectShownInside(
      await screen.findByRole("switch", { name: /use my global limits/i }), "collapse", "the global switch");
    await screen.findByText("no gore");
    expectHeldClosed(paragraphInDom(/no limits set/i), "the empty line");
    expectHeldClosed(paragraphInDom(exactly(LIMITS_UNREAD)), "the error line");

    const trigger = screen.getByRole("button", { name: /^delete limit$/i });
    expectShownInside(trigger, "fade-in", "the delete trigger");
    expect(screen.queryByRole("button", { name: /confirm delete limit/i })).toBeNull();
    await user.click(trigger);
    expectShownInside(
      await screen.findByRole("button", { name: /confirm delete limit/i }), "fade-in", "the confirm button");
    expect(screen.queryByRole("button", { name: /^delete limit$/i })).toBeNull();
  });

  it("with no limits: the empty line opens", async () => {
    mockFetch({ "/notebook/7/boundaries": { body: { boundaries: [], use_global: true } } });
    renderWithQueryClient(<BoundaryPanel />);
    await waitFor(() => expectShownInside(paragraph(/no limits set/i), "collapse", "the empty line"));
  });

  it("with no chat open: the global switch is held closed and unreachable", async () => {
    useUiStore.setState({ selectedChatId: null });
    mockFetch({ "/notebook/boundaries": { body: { boundaries: [boundary()] } } });
    renderWithQueryClient(<BoundaryPanel />);
    await screen.findByText("no gore");
    expect(screen.queryByRole("switch", { name: /use my global limits/i })).toBeNull();
    // ByLabelText reaches the mounted control regardless of the tree.
    expectHeldClosed(
      screen.queryByLabelText(/use my global limits/i, { selector: '[role="switch"]' }),
      "the global switch");
  });

  it("when the list cannot be loaded: the error line opens and the empty line stays closed", async () => {
    mockFetch({ "/notebook/7/boundaries": { status: 500, body: { detail: "boom" } } });
    renderWithQueryClient(<BoundaryPanel />);
    await waitFor(() =>
      expectShownInside(paragraph(exactly(LIMITS_UNREAD)), "collapse", "the error line"));
    expectHeldClosed(paragraphInDom(/no limits set/i), "the empty line");
  });
});

function statusBody(over: Record<string, unknown> = {}) {
  const worker = {
    state: "closed", failures: 0, total_failures: 0, alive: true,
    queued: 0, dropped_offers: 0, runs: 3, batch_size: 20,
    unhandled: 0, settled_empty: 0, refused_by_breaker: 0,
    refused_by_chat_breaker: 0, backlog: { chats: 0, messages: 0 },
    backfill: null,
    ...((over.worker as object) ?? {}),
  };
  const stats = {
    done: 3, failed: 0, skipped: 0, skip_reasons: {}, abandoned: 0, paid_and_lost: 0,
    ...((over.stats as object) ?? {}),
  };
  return {
    stats,
    spend: { calls: 3, tokens_in: 900, tokens_out: 40, cost: 0.0004, cost_unknown: 0,
             ...((over.spend as object) ?? {}) },
    spend_lifetime: { calls: 3, tokens_in: 900, tokens_out: 40, cost: 0.0004 },
    worker,
    daily_cap: 60,
    chat_refusal: (over.chat_refusal as string | null | undefined) ?? null,
  };
}

function mountWorker(over: Record<string, unknown> = {}, extra: Record<string, { status?: number; body: unknown }> = {}) {
  mockFetch({
    ...extra,
    "/notebook/worker/reset": { body: { ok: true } },
    "/notebook/worker": { body: statusBody(over) },
    "/notebook/auto-accept": { body: { enabled: true, effective: true, overridden: false } },
  });
  return renderWithQueryClient(<WorkerPanel />);
}

describe("WorkerPanel", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: null });
  });

  it("with nothing to report and no chat open: every conditional line is held closed, every conditional button is absent", async () => {
    useUiStore.setState({ selectedChatId: null });
    mountWorker();
    const card = await screen.findByTestId("worker-status");
    expectShownInside(card, "collapse", "the status card");

    for (const [what, node] of [
      ["the per-chat auto-accept block", screen.queryByTestId("chat-auto-accept")],
      ["the chat-state line", screen.queryByTestId("worker-chat-state")],
      ["the missing-prices line", screen.queryByTestId("worker-cost-unknown")],
      ["the failed-runs line", paragraphInDom(says(CENSUS.failedFree(0)))],
      ["the abandoned line", screen.queryByTestId("worker-abandoned")],
      ["the reader-stopped alert", paragraphInDom(says(WORKER_DEAD))],
      ["the unhandled line", screen.queryByTestId("worker-unhandled")],
      ["the chat-refusal line", screen.queryByTestId("worker-chat-refusal")],
      ["the settled-empty line", screen.queryByTestId("worker-settled-empty")],
      ["the breaker line", paragraphInDom(says(CENSUS.pausedTurns(0)))],
      ["the chat-breaker line", paragraphInDom(says(CENSUS.failingChatTurns(0)))],
      ["the dropped-offers line", paragraphInDom(says(CENSUS.busyTurns(0)))],
    ] as const) {
      expectHeldClosed(node, what);
    }
    // The way-back block, the backlog line and the sweep answer are not on
    // this card any more; the strip at the top of the Notebook panel
    // carries them (BackfillStrip.test.tsx).
    for (const name of [/try again now/i, /try this chat again/i]) {
      expect(screen.queryByRole("button", { name })).toBeNull();
    }
  });

  it("with everything to report and a chat open: every conditional line opens inside its collapse, every button fades in", async () => {
    useUiStore.setState({ selectedChatId: 7 });
    mountWorker({
      worker: {
        state: "stopped", alive: false, unhandled: 2, settled_empty: 1,
        refused_by_breaker: 3, refused_by_chat_breaker: 1, dropped_offers: 4,
        chat: { state: "open", failures: 5 }, backlog: { chats: 1, messages: 5 },
      },
      stats: { done: 3, failed: 2, abandoned: 1, paid_and_lost: 0, skipped: 0, skip_reasons: {} },
      spend: { calls: 3, tokens_in: 900, tokens_out: 40, cost: 0.0004, cost_unknown: 1 },
      chat_refusal: "model_not_extractable",
    });
    const card = await screen.findByTestId("worker-status");
    // The census sits behind "Show details": opened as a reader
    // opens it, so every line below is asked inside an OPEN collapse.
    fireEvent.click(within(card).getByRole("button", { name: DISCLOSURE_WORDS.details }));

    for (const [what, locate] of [
      ["the per-chat auto-accept block", () => screen.queryByTestId("chat-auto-accept")],
      ["the chat-state line", () => screen.queryByTestId("worker-chat-state")],
      ["the missing-prices line", () => screen.queryByTestId("worker-cost-unknown")],
      ["the failed-runs line", () => paragraph(says(CENSUS.failedFree(2)))],
      ["the abandoned line", () => screen.queryByTestId("worker-abandoned")],
      ["the reader-stopped alert", () => screen.queryByRole("alert")],
      ["the unhandled line", () => screen.queryByTestId("worker-unhandled")],
      ["the chat-refusal line", () => screen.queryByTestId("worker-chat-refusal")],
      ["the settled-empty line", () => screen.queryByTestId("worker-settled-empty")],
      ["the breaker line", () => paragraph(says(CENSUS.pausedTurns(3)))],
      ["the chat-breaker line", () => paragraph(says(CENSUS.failingChatTurns(1)))],
      ["the dropped-offers line", () => paragraph(says(CENSUS.busyTurns(4)))],
    ] as const) {
      await waitFor(() => expectShownInside(locate(), "collapse", what));
    }
    expectShownInside(
      screen.getByRole("button", { name: /try again now/i }), "fade-in", "the reset button");
    expectShownInside(
      screen.getByRole("button", { name: /try this chat again/i }), "fade-in", "the per-chat reset");
  });
});

/** The block `GET /notebook/worker` carries for a reading job. */
function backfillJob(over: Partial<BackfillStatus> = {}): BackfillStatus {
  return {
    job_id: 17, chat_id: 7, state: "done", scope: "all",
    calls_planned: 21, calls_spent: 21, notes_written: 14, messages_left: 0,
    last_error: null, ...over,
  };
}

/** A preview the plan route would answer with. */
const PLAN = {
  ready: true, blocked_by: [], scope: "all",
  messages_total: 412, messages_unread: 412, chunks: 21, calls_planned: 21,
  daily_cap: 60, backfill_cap: 50, live_reserve: 10,
  calls_spent_today: 3, fits_today: true,
  prompt_price: 0.06, cost_estimate: 0.014,
  model_id: "vendor/slug", model_is_chat_bind: false,
};

function mountStrip(
  worker: Record<string, unknown> = {},
  extra: Record<string, { status?: number; body: unknown }> = {},
) {
  mockFetch({
    ...extra,
    "GET /notebook/extract/plan/7": { body: PLAN },
    "/notebook/worker": { body: statusBody({ worker }) },
  });
  return renderWithQueryClient(<BackfillStrip chatId={7} />);
}

/** Which pane of a swap the node is in, or null when it is in no swap at
 *  all - which is the shape a bare ternary has and the thing this block
 *  exists to refuse. */
function paneOf(node: HTMLElement | null): string | null {
  return node?.closest('[data-slot="cross-fade"]')?.getAttribute("data-pane") ?? null;
}

describe("BackfillStrip", () => {
  // The strip took the way-back block, the backlog line and the sweep
  // answer off the worker card, and with them the three cases this file
  // used to hold under WorkerPanel. It also added a gesture the card never
  // had: an Undo trigger REPLACED in place by its confirmation, and the
  // pair replaced again by the line that says what the undo removed. A
  // replacement is neither a Collapse nor a FadeIn - KURAL 5.5 names it
  // and names the fix - so it goes through `CrossFade`, the same primitive
  // the just-saved strip in NotebookPanel uses for the identical gesture.
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: 7 });
  });
  afterEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: null });
  });

  it("idle: the offer fades in, and every block that has nothing to say is held closed", async () => {
    mountStrip({ backlog: { chats: 2, messages: 340 } });
    expectShownInside(
      await screen.findByRole("button", { name: /read the earlier messages/i }),
      "fade-in", "the Read button");
    await waitFor(() => expectShownInside(
      screen.queryByTestId("worker-backlog"), "collapse", "the backlog line"));
    for (const [what, node] of [
      ["the preview block", screen.queryByTestId("backfill-preview")],
      ["the answer line", screen.queryByTestId("backfill-said")],
    ] as const) {
      expectHeldClosed(node, what);
    }
    // The two job blocks are a NULL NARROWING inside their collapse rather
    // than a closed one: with no job there is nothing to say and nothing
    // to keep mounted, and the component says so at both sites. The
    // collapses themselves are there and shut, which is what the rows
    // below measure once a job exists.
    expect(screen.queryByTestId("backfill-progress")).toBeNull();
    expect(screen.queryByTestId("backfill-outcome")).toBeNull();
  });

  it("GROUND: with nothing unread the backlog line is held closed too", async () => {
    mountStrip({ backlog: { chats: 0, messages: 0 } });
    await screen.findByRole("button", { name: /read the earlier messages/i });
    await waitFor(() =>
      expectHeldClosed(screen.queryByTestId("worker-backlog"), "the backlog line"));
  });

  it("the preview: the pending line opens inside a collapse and closes when the plan lands", async () => {
    // The line that says "working out what this would read and cost" used
    // to be a bare `&&`: it appeared in one frame and, worse, was removed
    // with no exit at all in the frame the numbers arrived.
    const user = userEvent.setup();
    // The plan request is held open, then answered by hand, so both edges
    // of the line can be looked at rather than only the settled state.
    const answered = mockFetch({
      "GET /notebook/extract/plan/7": { body: PLAN },
      "/notebook/worker": { body: statusBody() },
    });
    let release: (() => void) | null = null;
    const held = new Promise<void>((resolve) => { release = resolve; });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).includes("/notebook/extract/plan/")) await held;
      return answered(input, init);
    }));
    renderWithQueryClient(<BackfillStrip chatId={7} />);
    await user.click(
      await screen.findByRole("button", { name: /read the earlier messages/i }));
    await waitFor(() => expectShownInside(
      screen.queryByTestId("backfill-pending"), "collapse", "the pending line"));

    release!();
    await waitFor(() => expectShownInside(
      screen.queryByTestId("backfill-plan"), "collapse", "the plan line"));
    // And it LEAVES through the same collapse rather than being unmounted.
    expectHeldClosed(screen.queryByTestId("backfill-pending"), "the pending line");
  });

  it("the range fields open inside a collapse only for the range scope", async () => {
    const user = userEvent.setup();
    mountStrip();
    await user.click(
      await screen.findByRole("button", { name: /read the earlier messages/i }));
    await screen.findByTestId("backfill-plan");
    expectHeldClosed(screen.queryByLabelText(/first message/i), "the first-message field");

    await user.click(screen.getByRole("combobox", { name: /what to read/i }));
    await user.click(await screen.findByRole("option", { name: /a range of messages/i }));
    await waitFor(() => expectShownInside(
      screen.queryByLabelText(/first message/i), "collapse", "the first-message field"));
  });

  it("a finished run: Undo cross-fades into its confirmation, and the confirm pair is one pane", async () => {
    const user = userEvent.setup();
    mountStrip({ backfill: backfillJob({ notes_written: 14 }) });
    const undo = await screen.findByRole("button", { name: /undo this run/i });
    expectShownInside(undo, "cross-fade", "the undo trigger");
    expect(paneOf(undo)).toBe("undo");

    await user.click(undo);
    // mode="wait": the confirm pane mounts once the undo pane has left.
    const confirm = await screen.findByRole("button", { name: UNDO_RUN_CONFIRM });
    expectShownInside(confirm, "cross-fade", "the confirm button");
    expect(paneOf(confirm)).toBe("confirm");
    const keep = screen.getByRole("button", { name: /^cancel$/i });
    // ONE pane for the pair, so the row keeps its layout across the swap.
    expect(confirm.closest('[data-slot="cross-fade"]'))
      .toBe(keep.closest('[data-slot="cross-fade"]'));
    expect(screen.queryByRole("button", { name: /undo this run/i })).toBeNull();

    // And back: "Cancel" returns the trigger through the same swap.
    await user.click(keep);
    const again = await screen.findByRole("button", { name: /undo this run/i });
    expect(paneOf(again)).toBe("undo");
  });

  it("the undo answer replaces the pair through the same swap", async () => {
    const user = userEvent.setup();
    mountStrip({ backfill: backfillJob({ notes_written: 14 }) }, {
      "POST /notebook/extract/undo/17": { body: {
        job_id: 17, removed: 12, kept_pinned: 2, kept_edited: 0, kept_accepted: 0, kept_older: 0 } },
    });
    await user.click(await screen.findByRole("button", { name: /undo this run/i }));
    await user.click(
      await screen.findByRole("button", { name: UNDO_RUN_CONFIRM }));
    const undone = await screen.findByTestId("backfill-undone");
    expectShownInside(undone, "cross-fade", "the undo answer");
    expect(paneOf(undone)).toBe("undone");
  });

  it("GROUND: a run that wrote nothing offers no undo, and the swap is held closed", async () => {
    mountStrip({ backfill: backfillJob({ notes_written: 0 }) });
    await screen.findByTestId("backfill-outcome");
    // The outcome sentence is reachable; the swap under it is not.
    expect(screen.queryByRole("button", { name: /undo this run/i })).toBeNull();
    await waitFor(() => expectHeldClosed(
      screen.queryByRole("button", { name: /undo this run/i, hidden: true }),
      "the undo trigger"));
  });

  it("a job in flight: the progress block opens and the offer is gone", async () => {
    mountStrip({ backfill: backfillJob({ state: "running", messages_left: 200 }) });
    await waitFor(() => expectShownInside(
      screen.queryByTestId("backfill-progress"), "collapse", "the progress block"));
    expectShownInside(
      screen.getByRole("button", { name: /stop reading/i }), "collapse", "the Stop button");
    // The Read offer is a FadeIn, so it is simply absent.
    expect(screen.queryByRole("button", { name: /read the earlier messages/i })).toBeNull();
    expectHeldClosed(screen.queryByTestId("backfill-outcome"), "the outcome block");
  });

  it("a finished job: the progress block is held closed rather than unmounted", async () => {
    // The other direction of the row above, and the reason the children
    // are narrowed on `job` alone and never on `running` as well: a child
    // that disappears in the render that closes the box animates an empty
    // box shut.
    mountStrip({ backfill: backfillJob({ notes_written: 14 }) });
    await screen.findByTestId("backfill-outcome");
    expectHeldClosed(screen.queryByTestId("backfill-progress"), "the progress block");
  });

  it("the answer to a press opens inside its collapse", async () => {
    const user = userEvent.setup();
    mountStrip({}, {
      "POST /notebook/extract/run/7": { body: { started: false, reason: "nothing_unread" } },
    });
    await user.click(
      await screen.findByRole("button", { name: /read the earlier messages/i }));
    await screen.findByTestId("backfill-plan");
    await user.click(screen.getByRole("button", { name: /start reading/i }));
    await waitFor(() => expectShownInside(
      screen.queryByTestId("backfill-said"), "collapse", "the answer line"));
  });
});

describe("ExtractionSettings", () => {
  const MODELS = { models: [{ id: "cheap/one", provider: "P", prompt_price: 0.06, context_length: 131000, endpoints: 3 }] };

  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: 7 });
  });
  afterEach(() => vi.restoreAllMocks());

  it("when the saved choice cannot be read: the alert opens inside its collapse", async () => {
    mockFetch({
      "/notebook/extract/models": { body: MODELS },
      "/notebook/extract/settings": { status: 500, body: { detail: "boom" } },
    });
    renderWithQueryClient(<ExtractionSettings />);
    await waitFor(() =>
      expectShownInside(
        screen.queryAllByRole("status").find((a) => a.textContent === CHOICE_UNREAD) ?? null,
        "collapse", "the settings line"));
  });

  it("when it can: the alert is held closed", async () => {
    mockFetch({
      "/notebook/extract/models": { body: MODELS },
      "/notebook/extract/settings": { body: { model_id: null, prompt_language: "en" } },
    });
    renderWithQueryClient(<ExtractionSettings />);
    await screen.findByText(/suggestions are off/i);
    expectHeldClosed(paragraphInDom(exactly(CHOICE_UNREAD)), "the settings line");
    expect(within(document.body).queryByRole("alert")).toBeNull();
  });
});

describe("CrossFade", () => {
  // The swap primitive under both notebook swaps, asked on its own: three
  // moves of `pane` in a row, the way the strip's undo makes them - to a
  // second pane, back to the first, then on to a third.

  /** One swap whose pane the test moves, inside a box the test can read. */
  function Swap({ expose }: { expose: (move: (pane: string) => void) => void }) {
    const [pane, setPane] = useState("x");
    useEffect(() => expose(setPane), [expose]);
    return (
      <div data-testid="swap">
        <CrossFade pane={pane}>
          <span>{`pane ${pane}`}</span>
        </CrossFade>
      </div>
    );
  }

  function mountSwap() {
    const moves: { to: (pane: string) => void } = { to: () => {} };
    renderWithQueryClient(<Swap expose={(move) => { moves.to = move; }} />);
    return moves;
  }

  /** How many panes the box holds right now. */
  const panesIn = (box: HTMLElement) =>
    box.querySelectorAll('[data-slot="cross-fade"]').length;

  /**
   * THE SHAPE: the second pane is leaving, the first is waiting to come
   * back, and the third arrives in the very render that finishes the
   * second's exit. The first is then mounted already leaving. Left to itself
   * that pane never exits - it was never present - and its key has left once
   * before, so the presence would wait on it for good: the swap stops on an
   * inert pane and the third never mounts. The third is moved in from the
   * second's last exit frame, so this is the shape on every run and not when
   * timing happens to line up.
   *
   * What the swap did after that move is recorded on every observer callback:
   * whether the superseded pane was ever added, how many callbacks found the
   * box holding no pane at all, and the opacity the third pane had when it
   * was first seen.
   */
  async function supersedeOnTheLastExitFrame() {
    const moves = mountSwap();
    await screen.findByText("pane x");
    act(() => moves.to("y"));
    const y = await screen.findByText("pane y");
    // Fully arrived before it is sent away, so its exit is a whole fade and
    // its last frame is a frame of its own.
    await waitFor(() => expect(
      y.closest<HTMLElement>('[data-slot="cross-fade"]')?.style.opacity).toBe("1"));
    const box = screen.getByTestId("swap");
    const seen = {
      fired: false,
      supersededShown: false,
      callbacks: 0,
      empty: 0,
      zFirstOpacity: null as string | null,
    };
    const watch = new MutationObserver((records) => {
      if (seen.fired) {
        seen.callbacks += 1;
        for (const r of records) {
          r.addedNodes.forEach((n) => {
            if (n instanceof HTMLElement && n.dataset.pane === "x") seen.supersededShown = true;
          });
        }
        if (panesIn(box) === 0) seen.empty += 1;
        const z = box.querySelector<HTMLElement>('[data-slot="cross-fade"][data-pane="z"]');
        if (z && seen.zFirstOpacity === null) seen.zFirstOpacity = z.style.opacity;
      }
      const leaving = document.querySelector<HTMLElement>(
        '[data-slot="cross-fade"][data-pane="y"][inert]');
      if (!seen.fired && leaving?.style.opacity === "0") {
        seen.fired = true;
        moves.to("z");
      }
    });
    watch.observe(document.body, {
      subtree: true, childList: true, attributes: true,
      attributeFilter: ["style", "inert"],
    });
    try {
      act(() => moves.to("x"));
      // GROUND: the third move really landed on the second pane's last exit
      // frame, so the render that finishes the exit is the one it is in.
      await waitFor(() => expect(seen.fired, "the second pane never finished leaving")
        .toBe(true));
      const z = await screen.findByText("pane z");
      // The pane the swap ends on has finished arriving before the record is
      // handed back, so every frame of that arrival was observed.
      await waitFor(() => expect(
        z.closest<HTMLElement>('[data-slot="cross-fade"]')?.style.opacity,
        "the pane the swap ends on never finished arriving").toBe("1"));
      return { z, seen };
    } finally {
      watch.disconnect();
    }
  }

  it("a pane that arrives already superseded does not stop the swap", async () => {
    const { z, seen } = await supersedeOnTheLastExitFrame();
    expectShownInside(z, "cross-fade", "the pane the swap ends on");
    // Nothing left behind that nobody can press. The ground found the
    // leaving pane by this same selector, so it sees an inert pane when
    // there is one.
    await waitFor(() => expect(
      document.querySelectorAll('[data-slot="cross-fade"][inert]'),
      "an inert pane is still in the swap",
    ).toHaveLength(0));
    // And the pane that was superseded before it was ever present was
    // never put on screen at all: there was nothing to show.
    expect(seen.supersededShown, "a pane that was never present was put on screen")
      .toBe(false);
  });

  it("the pane a restarted swap ends on fades in, as every arriving pane does",
     async () => {
    // KURAL 5.5: whatever opens is animated, and a swap's panes cross-fade.
    // The restart could mount the newest pane at full opacity, the way the
    // FIRST mount of a swap is still - and the reader, who has just watched
    // the confirm fade out, would see the next line pop in. Read off the
    // first frame the observer sees the pane in, and off its last.
    const { seen } = await supersedeOnTheLastExitFrame();
    // GROUND: the pane was seen, so the opacity below is a reading.
    expect(seen.zFirstOpacity, "the pane the swap ends on was never seen arriving")
      .not.toBeNull();
    expect(seen.zFirstOpacity, "the pane a restart mounts popped in instead of fading")
      .toBe("0");
  });

  it("a restarted swap never leaves the box empty in between", async () => {
    // The superseded pane renders nothing and the swap restarts under a new
    // key. Were the restart to wait for a passive effect, the render in
    // which the box holds no pane would be its own commit, painted before
    // the restart: one frame with nothing in the swap and the block
    // collapsing. The restart runs in a layout effect, so no observer
    // callback - each of which runs after a commit - finds the box empty.
    const { seen } = await supersedeOnTheLastExitFrame();
    // GROUND: callbacks really ran after the move, so zero is a count.
    expect(seen.callbacks, "no callback ran after the move").toBeGreaterThan(0);
    expect(seen.empty, "a callback found the swap holding no pane at all").toBe(0);
    // POSITIVE CONTROL for the count: the same reader, over a box that
    // really is emptied, counts it.
    const probe = document.createElement("div");
    const pane = document.createElement("div");
    pane.dataset.slot = "cross-fade";
    probe.appendChild(pane);
    document.body.appendChild(probe);
    let emptied = 0;
    const control = new MutationObserver(() => {
      if (panesIn(probe) === 0) emptied += 1;
    });
    control.observe(probe, { childList: true });
    try {
      pane.remove();
      await waitFor(() => expect(emptied, "the reader never saw an empty box")
        .toBeGreaterThan(0));
    } finally {
      control.disconnect();
      probe.remove();
    }
  });

  it("a swap's first pane is there whole from its first frame, with no fade", async () => {
    // The first presence mounts still: the pane is already there when the
    // block opens, so it has nothing to arrive from, and a fade here would
    // double the Collapse that usually wraps a swap. Its opacity is read on
    // every observer callback from the render that puts it in the document
    // until twice a fade's length has passed, so a fade that started late
    // would be seen as well.
    /** Every opacity the pane `name` is seen with, in order. */
    const recorder = (name: string) => {
      const readings: string[] = [];
      const watch = new MutationObserver(() => {
        const pane = document.querySelector<HTMLElement>(
          `[data-slot="cross-fade"][data-pane="${name}"]`);
        if (pane) readings.push(pane.style.opacity);
      });
      watch.observe(document.body, {
        subtree: true, childList: true, attributes: true, attributeFilter: ["style"],
      });
      return { readings, stop: () => watch.disconnect() };
    };
    const first = recorder("x");
    const moves = mountSwap();
    try {
      await screen.findByText("pane x");
      await new Promise((resolve) => setTimeout(resolve, durationSeconds.exit * 1000 * 2));
    } finally {
      first.stop();
    }
    // GROUND: the pane was seen, from the render that put it in.
    expect(first.readings.length, "the first pane was never seen").toBeGreaterThan(0);
    expect(first.readings[0], "the first pane faded in instead of being there")
      .toBe("1");
    expect(first.readings.filter((opacity) => opacity !== "1"),
      "the first pane was drawn below whole").toEqual([]);

    // POSITIVE CONTROL: the same recorder, on the pane a move brings in,
    // sees the fade every later pane arrives with, so "1" above is a reading.
    const second = recorder("y");
    try {
      act(() => moves.to("y"));
      const y = await screen.findByText("pane y");
      await waitFor(() => expect(
        y.closest<HTMLElement>('[data-slot="cross-fade"]')?.style.opacity).toBe("1"));
    } finally {
      second.stop();
    }
    expect(second.readings[0], "the recorder did not see a pane arrive from nothing")
      .toBe("0");
  });

  it("POSITIVE CONTROL: the same three moves, spaced out, end on the last pane too", async () => {
    // The ordinary path, so the rows above are about the shape and not about
    // three moves in themselves: here the first pane is back and live before
    // the third move, and it is SHOWN, which is what the first row says the
    // superseded one never is. The added-node reader that row uses is run
    // here too, and it sees the first pane put back.
    const moves = mountSwap();
    await screen.findByText("pane x");
    act(() => moves.to("y"));
    await screen.findByText("pane y");
    let xAdded = false;
    const watch = new MutationObserver((records) => {
      for (const r of records) {
        r.addedNodes.forEach((n) => {
          if (n instanceof HTMLElement && n.dataset.pane === "x") xAdded = true;
        });
      }
    });
    watch.observe(document.body, { subtree: true, childList: true });
    try {
      act(() => moves.to("x"));
      const x = await screen.findByText("pane x");
      await waitFor(() => expectShownInside(x, "cross-fade", "the first pane, back"));
      expect(xAdded, "the added-node reader did not see the first pane put back")
        .toBe(true);
    } finally {
      watch.disconnect();
    }
    act(() => moves.to("z"));
    const z = await screen.findByText("pane z");
    await waitFor(() => expectShownInside(z, "cross-fade", "the pane the swap ends on"));
    await waitFor(() => expect(
      document.querySelectorAll('[data-slot="cross-fade"][inert]')).toHaveLength(0));
  });
});
