/**
 * Decision 58 a: a note the read writes after a reply is on screen within
 * two seconds, while the Notes tab is open.
 *
 * A finished reply starts a short poll of this chat's status
 * (`worker.reading`); the first answer that says the read is over refreshes
 * the note list once and ends the poll. It also ends at its ceiling, when
 * the tab closes, and when the vault locks. With the tab closed nothing is
 * asked at all.
 *
 * Every step and ceiling is imported from the module that uses it, never
 * retyped. The panels are the real ones; only fetch is fake, and its bodies
 * are the objects the mock serves, so changing them is the server changing
 * its answer between two polls.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act, cleanup, screen, waitFor } from "@testing-library/react";

import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { WorkerPanel } from "@/components/notebook/WorkerPanel";
import { NotebookPanel } from "@/components/notebook/NotebookPanel";
import {
  FAST_POLL_MAX,
  FAST_POLL_MS,
  WORKER_POLL_MS,
  useReplyFinishedStore,
} from "@/lib/query/notebook";
import { useDraftStore } from "@/lib/store/draftStore";
import { useUiStore } from "@/lib/store/uiStore";
import { mockFetch } from "../mocks/api";

const CHAT = 7;
const OTHER_CHAT = 8;
const FIRST = "Mira is her sister.";
const SECOND = "She keeps the shop key on a red string.";

function entry(id: number, text: string) {
  return {
    id, chat_id: CHAT, position: id - 1, kind: "fact", text,
    evidence: null, durability: "permanent", importance: 2, pinned: 0,
    retired_at: null, superseded_by: null, excluded_reason: null,
    status: "accepted", provenance: "model", source_message_id: null,
    created_at: "2026-09-27", updated_at: "2026-09-27",
  };
}

function statusBody(reading: boolean) {
  return {
    stats: { done: 3, failed: 0, skipped: 0, skip_reasons: {} },
    spend: { calls: 3, tokens_in: 900, tokens_out: 40, cost: 0.0004 },
    spend_lifetime: { calls: 3, tokens_in: 900, tokens_out: 40,
                      cost: 0.0004 },
    worker: {
      state: "closed", failures: 0, total_failures: 0,
      queued: 0, dropped_offers: 0, runs: 3, batch_size: 20,
      backfill: null, reading,
    },
    daily_cap: 60,
  };
}

/** The note list and the worker card, as the Notes tab mounts them. */
function server() {
  const notes: { entries: unknown[]; notebook_chars: number } = {
    entries: [entry(1, FIRST)],
    notebook_chars: 40,
  };
  const worker = statusBody(true);
  const workerRoute: { status?: number; body: unknown } = { body: worker };
  const fetchMock = mockFetch({
    "/notebook/worker": workerRoute,
    "/notebook/auto-accept": {
      body: { enabled: true, effective: true, overridden: false },
    },
    [`/notebook/${CHAT}`]: { body: notes },
  });
  function callsTo(path: string): number {
    return fetchMock.mock.calls.filter((c) => {
      const url = String(c[0] as RequestInfo | URL);
      const init = c[1] as RequestInit | undefined;
      return (init?.method ?? "GET") === "GET" && url.includes(path);
    }).length;
  }
  return { notes, worker, workerRoute, callsTo };
}

function openTheNotesTab() {
  return renderWithQueryClient(
    <>
      <NotebookPanel />
      <WorkerPanel />
    </>,
  );
}

async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

function replyFinished(chatId = CHAT) {
  act(() => {
    useReplyFinishedStore.getState().mark(chatId);
  });
}

describe("the Notes tab after a reply (58 a)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    useUiStore.setState({ selectedChatId: CHAT, rightPanelCollapsed: false });
    useReplyFinishedStore.setState({ finished: {} });
    useDraftStore.getState().forgetChat(CHAT);
  });
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("the note written after a reply is on screen within two seconds",
     async () => {
    const { notes, worker, callsTo } = server();
    openTheNotesTab();
    await advance(0);
    expect(await screen.findByText(FIRST)).toBeInTheDocument();
    const listFetches = callsTo(`/notebook/${CHAT}`);

    replyFinished();
    await advance(FAST_POLL_MS);
    // GROUND: the read is still going, so the list is left as it was.
    expect(callsTo("/notebook/worker")).toBe(2);
    expect(callsTo(`/notebook/${CHAT}`)).toBe(listFetches);
    expect(screen.queryByText(SECOND)).not.toBeInTheDocument();

    // The read writes its note and ends.
    notes.entries.push(entry(2, SECOND));
    worker.worker.reading = false;
    await advance(2_000);

    expect(callsTo(`/notebook/${CHAT}`)).toBe(listFetches + 1);
    expect(screen.getByText(SECOND)).toBeInTheDocument();
    expect(screen.getByText(FIRST)).toBeInTheDocument();
  });

  it("the fast poll stops when the read ends", async () => {
    const { worker, callsTo } = server();
    worker.worker.reading = false;
    openTheNotesTab();
    await advance(0);
    expect(await screen.findByText(FIRST)).toBeInTheDocument();
    const listFetches = callsTo(`/notebook/${CHAT}`);

    replyFinished();
    await advance(FAST_POLL_MS);
    expect(callsTo("/notebook/worker")).toBe(2);
    await advance(FAST_POLL_MS * 5);
    expect(callsTo("/notebook/worker")).toBe(2);
    expect(callsTo(`/notebook/${CHAT}`)).toBe(listFetches + 1);
  });

  it("the fast poll stops after its cap", async () => {
    const { callsTo } = server();
    openTheNotesTab();
    await advance(0);
    expect(await screen.findByText(FIRST)).toBeInTheDocument();

    replyFinished();
    await advance(FAST_POLL_MS * FAST_POLL_MAX);
    // POSITIVE: it really was asking, at the fast step, up to the cap.
    const regular = Math.floor((FAST_POLL_MS * FAST_POLL_MAX) / WORKER_POLL_MS);
    expect(callsTo("/notebook/worker")).toBe(1 + regular + FAST_POLL_MAX);

    // After the cap only the ordinary tick is left: two in two ticks.
    const atCap = callsTo("/notebook/worker");
    await advance(WORKER_POLL_MS * 2);
    expect(callsTo("/notebook/worker")).toBe(atCap + 2);
  });

  it("the fast poll stops when the tab closes", async () => {
    const { callsTo } = server();
    const view = openTheNotesTab();
    await advance(0);
    expect(await screen.findByText(FIRST)).toBeInTheDocument();

    replyFinished();
    await advance(FAST_POLL_MS);
    expect(callsTo("/notebook/worker")).toBe(2);
    view.unmount();
    await advance(FAST_POLL_MS * 10);
    expect(callsTo("/notebook/worker")).toBe(2);
  });

  it("the fast poll stops when the vault locks", async () => {
    const { workerRoute, callsTo } = server();
    openTheNotesTab();
    await advance(0);
    expect(await screen.findByText(FIRST)).toBeInTheDocument();

    replyFinished();
    await advance(FAST_POLL_MS);
    expect(callsTo("/notebook/worker")).toBe(2);
    workerRoute.status = 423;
    workerRoute.body = { detail: { code: "vault_locked" } };
    await advance(FAST_POLL_MS);
    expect(callsTo("/notebook/worker")).toBe(3);
    await advance(FAST_POLL_MS * 5);
    expect(callsTo("/notebook/worker")).toBe(3);
  });

  it("asks nothing with the tab closed, nor when it opens later",
     async () => {
    const { callsTo } = server();
    replyFinished();
    await advance(FAST_POLL_MS * 3);
    expect(callsTo("/notebook/worker")).toBe(0);

    openTheNotesTab();
    await advance(0);
    expect(await screen.findByText(FIRST)).toBeInTheDocument();
    await advance(FAST_POLL_MS * 3);
    // GROUND: the tab's own first ask happened, and nothing after it.
    expect(callsTo("/notebook/worker")).toBe(1);
  });

  it("a reply in another chat does not start it", async () => {
    const { callsTo } = server();
    openTheNotesTab();
    await advance(0);
    expect(await screen.findByText(FIRST)).toBeInTheDocument();

    replyFinished(OTHER_CHAT);
    await advance(FAST_POLL_MS * 3);
    expect(callsTo("/notebook/worker")).toBe(1);

    // POSITIVE: the same panel does start it for its own chat.
    replyFinished();
    await advance(FAST_POLL_MS);
    await waitFor(() => expect(callsTo("/notebook/worker")).toBe(2));
  });

  it("asks nothing while the right panel is collapsed", async () => {
    // The panel collapses to zero width and stays mounted, so the Notes tab
    // can be the selected tab on a panel nobody can see.
    const { callsTo } = server();
    useUiStore.setState({ rightPanelCollapsed: true });
    openTheNotesTab();
    await advance(0);
    const first = callsTo("/notebook/worker");

    replyFinished();
    await advance(FAST_POLL_MS * 3);
    expect(callsTo("/notebook/worker")).toBe(first);

    // POSITIVE: open the panel and the next reply is asked about.
    act(() => useUiStore.setState({ rightPanelCollapsed: false }));
    replyFinished();
    await advance(FAST_POLL_MS);
    await waitFor(() =>
      expect(callsTo("/notebook/worker")).toBe(first + 1));
  });

  // An ask already out when the tab closes or the panel collapses.
  // Its answer must do nothing: not ask again ("still reading") and not
  // refresh the list ("read over") for a panel nobody is looking at.
  async function askHeldOpen(
    close: (view: { unmount: () => void }) => void, readingAfter: boolean) {
    const { worker, callsTo } = server();
    const view = openTheNotesTab();
    await advance(0);
    await screen.findByText(FIRST);
    const base = globalThis.fetch;
    let release!: () => void;
    const held = new Promise<void>((r) => { release = r; });
    let holding = false;
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (holding && String(input).includes("/notebook/worker")) await held;
      return base(input, init);
    }) as typeof fetch;
    const asks = callsTo("/notebook/worker");
    const lists = callsTo(`/notebook/${CHAT}`);
    holding = true;
    replyFinished();
    await advance(FAST_POLL_MS);            // the ask leaves and is held
    close(view);
    worker.worker.reading = readingAfter;
    release();
    await advance(FAST_POLL_MS * 5);
    return { asks: callsTo("/notebook/worker") - asks,
             lists: callsTo(`/notebook/${CHAT}`) - lists };
  }

  it("an ask in flight when the tab closes asks no more", async () => {
    const out = await askHeldOpen((view) => view.unmount(), true);
    expect(out.asks).toBe(1);
  });

  it("an ask in flight when the panel collapses does not refresh the list", async () => {
    const out = await askHeldOpen(() =>
      act(() => useUiStore.setState({ rightPanelCollapsed: true })), false);
    expect(out.asks).toBe(1);
    expect(out.lists).toBe(0);
  });

  it("POSITIVE: the same held ask, nothing closed, refreshes the list", async () => {
    const out = await askHeldOpen(() => {}, false);
    expect(out.lists).toBe(1);
  });
});
