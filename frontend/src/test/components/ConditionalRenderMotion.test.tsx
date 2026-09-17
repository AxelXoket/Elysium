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
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { hiddenFromReaders } from "@/test/helpers/accessibility";
import { mockFetch } from "@/test/mocks/api";
import { useUiStore } from "@/lib/store/uiStore";
import { NotebookPanel } from "@/components/notebook/NotebookPanel";
import { BoundaryPanel } from "@/components/notebook/BoundaryPanel";
import { WorkerPanel } from "@/components/notebook/WorkerPanel";
import { ExtractionSettings } from "@/components/notebook/ExtractionSettings";

type Wrapper = "collapse" | "fade-in";

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
    expectHeldClosed(paragraphInDom(/could not be loaded/i), "the error line");
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
      expectShownInside(paragraph(/could not be loaded/i), "collapse", "the error line"));
    expectHeldClosed(paragraphInDom(/^loading/i), "the loading line");
    expectHeldClosed(paragraphInDom(/nothing yet/i), "the empty state");
  });

  it("on an empty notebook: the empty state opens and the no-match line stays closed", async () => {
    mockFetch({ "/notebook/7": { body: { entries: [], notebook_chars: 0 } } });
    renderWithQueryClient(<NotebookPanel />);
    await waitFor(() =>
      expectShownInside(paragraph(/nothing yet/i), "collapse", "the empty state"));
    expectHeldClosed(paragraphInDom(/no note matches that/i), "the no-match line");
    expectHeldClosed(paragraphInDom(/could not be loaded/i), "the error line");
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

  it("a user's own note: the strip stays closed, the delete trigger fades in, and the confirm pair replaces it", async () => {
    const user = userEvent.setup();
    mockFetch({ "/notebook/7": { body: { entries: [entry()], notebook_chars: 10 } } });
    renderWithQueryClient(<NotebookPanel />);
    const trigger = await screen.findByRole("button", { name: /delete note/i });
    expectShownInside(trigger, "fade-in", "the delete trigger");
    expectHeldClosed(screen.queryByTestId("just-saved"), "the just-saved strip");
    // An accepted note has no Keep-suggestion button; FadeIn means not rendered.
    expect(screen.queryByRole("button", { name: /keep suggestion/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /confirm delete/i })).toBeNull();

    await user.click(trigger);
    const confirm = await screen.findByRole("button", { name: /confirm delete/i });
    expectShownInside(confirm, "fade-in", "the confirm button");
    expectShownInside(
      screen.getByRole("button", { name: /keep note/i }), "fade-in", "the keep button");
    // The pair shares one fade, so the row keeps its layout.
    expect(confirm.closest('[data-slot="fade-in"]'))
      .toBe(screen.getByRole("button", { name: /keep note/i }).closest('[data-slot="fade-in"]'));
    expect(screen.queryByRole("button", { name: /delete note/i })).toBeNull();
  });

  it("a proposal: the Keep-suggestion button fades in", async () => {
    mockFetch({ "/notebook/7": { body: { entries: [modelNote({ status: "proposed" })], notebook_chars: 10 } } });
    renderWithQueryClient(<NotebookPanel />);
    expectShownInside(
      await screen.findByRole("button", { name: /keep suggestion/i }), "fade-in", "the accept button");
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
    const confirm = await screen.findByRole("button", { name: /confirm delete/i });
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
    expectHeldClosed(paragraphInDom(/could not be loaded/i), "the error line");

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
      expectShownInside(paragraph(/could not be loaded/i), "collapse", "the error line"));
    expectHeldClosed(paragraphInDom(/no limits set/i), "the empty line");
  });
});

function statusBody(over: Record<string, unknown> = {}) {
  const worker = {
    state: "closed", failures: 0, total_failures: 0, alive: true,
    queued: 0, dropped_offers: 0, runs: 3, batch_size: 20,
    unhandled: 0, settled_empty: 0, refused_by_breaker: 0,
    refused_by_chat_breaker: 0, backlog: { chats: 0, messages: 0 },
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
      ["the failed-runs line", paragraphInDom(/runs failed/i)],
      ["the abandoned line", screen.queryByTestId("worker-abandoned")],
      ["the reader-stopped alert", paragraphInDom(/background reader has stopped/i)],
      ["the unhandled line", screen.queryByTestId("worker-unhandled")],
      ["the settled-empty line", screen.queryByTestId("worker-settled-empty")],
      ["the breaker line", paragraphInDom(/while it was cooling down/i)],
      ["the chat-breaker line", paragraphInDom(/keep failing on their own/i)],
      ["the dropped-offers line", paragraphInDom(/went unqueued/i)],
      ["the way-back block", screen.queryByTestId("worker-sweep")],
      ["the backlog line", screen.queryByTestId("worker-backlog")],
      ["the sweep answer", screen.queryByTestId("worker-sweep-said")],
    ] as const) {
      expectHeldClosed(node, what);
    }
    for (const name of [/read the earlier messages/i, /try again now/i, /try this chat again/i]) {
      expect(screen.queryByRole("button", { name })).toBeNull();
    }
  });

  it("with everything to report and a chat open: every conditional line opens inside its collapse, every button fades in", async () => {
    const user = userEvent.setup();
    useUiStore.setState({ selectedChatId: 7 });
    mountWorker({
      worker: {
        state: "stopped", alive: false, unhandled: 2, settled_empty: 1,
        refused_by_breaker: 3, refused_by_chat_breaker: 1, dropped_offers: 4,
        chat: { state: "open", failures: 5 }, backlog: { chats: 1, messages: 5 },
      },
      stats: { done: 3, failed: 2, abandoned: 1, paid_and_lost: 0, skipped: 0, skip_reasons: {} },
      spend: { calls: 3, tokens_in: 900, tokens_out: 40, cost: 0.0004, cost_unknown: 1 },
    }, { "POST /notebook/sweep/7": { body: { started: true, after_id: 0 } } });
    await screen.findByTestId("worker-status");

    for (const [what, locate] of [
      ["the per-chat auto-accept block", () => screen.queryByTestId("chat-auto-accept")],
      ["the chat-state line", () => screen.queryByTestId("worker-chat-state")],
      ["the missing-prices line", () => screen.queryByTestId("worker-cost-unknown")],
      ["the failed-runs line", () => paragraph(/runs failed/i)],
      ["the abandoned line", () => screen.queryByTestId("worker-abandoned")],
      ["the reader-stopped alert", () => screen.queryByRole("alert")],
      ["the unhandled line", () => screen.queryByTestId("worker-unhandled")],
      ["the settled-empty line", () => screen.queryByTestId("worker-settled-empty")],
      ["the breaker line", () => paragraph(/while it was cooling down/i)],
      ["the chat-breaker line", () => paragraph(/keep failing on their own/i)],
      ["the dropped-offers line", () => paragraph(/went unqueued/i)],
      ["the backlog line", () => screen.queryByTestId("worker-backlog")],
    ] as const) {
      await waitFor(() => expectShownInside(locate(), "collapse", what));
    }
    const sweep = screen.getByRole("button", { name: /read the earlier messages/i });
    expectShownInside(sweep, "collapse", "the way-back block");
    expectShownInside(
      screen.getByRole("button", { name: /try again now/i }), "fade-in", "the reset button");
    expectShownInside(
      screen.getByRole("button", { name: /try this chat again/i }), "fade-in", "the per-chat reset");

    // The sweep answer opens once there is an answer.
    expectHeldClosed(screen.queryByTestId("worker-sweep-said"), "the sweep answer");
    await user.click(sweep);
    await waitFor(() =>
      expectShownInside(paragraph(/reading the earlier part/i), "collapse", "the sweep answer"));
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
        screen.queryAllByRole("alert").find((a) => /saved choice could not be read/i.test(a.textContent ?? "")) ?? null,
        "collapse", "the settings alert"));
  });

  it("when it can: the alert is held closed", async () => {
    mockFetch({
      "/notebook/extract/models": { body: MODELS },
      "/notebook/extract/settings": { body: { model_id: null, prompt_language: "en" } },
    });
    renderWithQueryClient(<ExtractionSettings />);
    await screen.findByText(/suggestions are off/i);
    expectHeldClosed(paragraphInDom(/saved choice could not be read/i), "the settings alert");
    expect(within(document.body).queryByRole("alert")).toBeNull();
  });
});
