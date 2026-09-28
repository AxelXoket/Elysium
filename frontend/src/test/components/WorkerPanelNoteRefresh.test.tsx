/**
 * Notes written in the BACKGROUND have to reach the screen on their own.
 *
 * The extractor spends the reader's own credits while they are reading
 * something else, and the note list had no clock: the only thing that ever
 * invalidated it was a mutation the reader made themselves. So a run that
 * landed while the Notes tab was open wrote a note nobody saw until they
 * clicked to another tab and back. The reader paid for it and the panel did
 * not show it.
 *
 * The signal already exists - the worker status poll - and these tests are
 * about hanging the note list off THAT tick rather than off a second timer.
 * Every one of them drives the real `refetchInterval` with the real
 * `WORKER_POLL_MS`, imported rather than retyped, so nothing here can pass
 * against a number the app has stopped using.
 *
 * The property that matters, and the one a lazy fix gets wrong: the trigger
 * is `stats.done` CHANGING, not growing. It is a COUNT over every chat's
 * extraction rows and those rows are hard-deleted when a chat is deleted or
 * cleared and when a message is deleted or edited, so it falls as readily as
 * it rises. "Higher than last time" would go quiet after any delete.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act, cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode, type ComponentType, type ReactNode } from "react";

import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { RightPanel } from "@/components/layout/RightPanel";
import { GenerationSettingsProvider } from "@/components/generation/GenerationSettingsContext";
import { settingsFixture } from "../mocks/fixtures";
import { WORKER_POLL_MS } from "@/lib/query/notebook";
import { useDraftStore } from "@/lib/store/draftStore";
import { mockFetch } from "../mocks/api";
import { useUiStore } from "@/lib/store/uiStore";

const CHAT = 7;

/** The note the reader already had on screen before any of this. */
const FIRST = "Mira is her sister.";
/** What the background extractor writes while they are reading. */
const SECOND = "She keeps the shop key on a red string.";

function entry(id: number, text: string) {
  return {
    id, chat_id: CHAT, position: id - 1, kind: "fact", text,
    evidence: null, durability: "permanent", importance: 2, pinned: 0,
    retired_at: null, superseded_by: null, excluded_reason: null,
    status: "accepted", provenance: "model", source_message_id: null,
    created_at: "2026-08-30", updated_at: "2026-08-30",
  };
}

function statusBody(done: number) {
  return {
    stats: { done, failed: 0, skipped: 0, skip_reasons: {} },
    spend: { calls: done, tokens_in: 900, tokens_out: 40, cost: 0.0004 },
    spend_lifetime: { calls: done, tokens_in: 900, tokens_out: 40,
                      cost: 0.0004 },
    worker: {
      state: "closed", failures: 0, total_failures: 0,
      queued: 0, dropped_offers: 0, runs: done, batch_size: 20,
      backfill: null,
    },
    daily_cap: 60,
  };
}

const MODELS = {
  models: [
    { id: "cheap/one", provider: "P", prompt_price: 0.06,
      context_length: 131000, endpoints: 3 },
  ],
};

/**
 * The whole Notes tab, wired to bodies the test can rewrite in place.
 *
 * The route bodies are the SAME objects the mock serialises on every
 * request, so mutating `notes` or `worker` here is the server changing its
 * mind between two polls - which is the thing being tested. Mounted through
 * RightPanel itself, because since the three views the refresh belongs to the panel's
 * frame (useNotesFollowRuns): the worker card sits in the tab's Settings
 * view, and the note list must follow a run whichever view is open. One
 * query client, the note list on screen, the worker card mounted beside it.
 */
function openTheNotesTab(wrapper?: ComponentType<{ children: ReactNode }>) {
  const notes: { entries: unknown[]; notebook_chars: number } = {
    entries: [entry(1, FIRST)],
    notebook_chars: 40,
  };
  const worker = statusBody(3);
  const fetchMock = mockFetch({
    "/notebook/worker": { body: worker },
    "/notebook/auto-accept": {
      body: { enabled: true, effective: true, overridden: false },
    },
    "/notebook/extract/models": { body: MODELS },
    "/notebook/extract/settings": {
      body: { model_id: "cheap/one", prompt_language: "en" },
    },
    // Before the bare key below, because the first match in insertion order
    // wins and a POST to the same path must come back as ONE entry.
    [`POST /notebook/${CHAT}`]: { body: entry(2, SECOND) },
    [`/notebook/${CHAT}/boundaries`]: { body: { boundaries: [], use_global: true } },
    "/notebook/boundaries": { body: { boundaries: [], use_global: true } },
    "/notebook/safeword": { body: { word: "" } },
    [`/notebook/${CHAT}`]: { body: notes },
    [`/chats/${CHAT}/messages`]: { body: [] },
    // Asked for by the generation-settings provider the shell wraps the
    // panel in. Last: as a substring it would answer the extract settings.
    "/settings": { body: settingsFixture },
  });
  const Strict = wrapper;
  const Frame = ({ children }: { children: ReactNode }) => (
    Strict
      ? <Strict><GenerationSettingsProvider>{children}</GenerationSettingsProvider></Strict>
      : <GenerationSettingsProvider>{children}</GenerationSettingsProvider>
  );
  const view = renderWithQueryClient(<RightPanel />, { wrapper: Frame });
  /** Every GET this component tree has sent whose URL's PATH ends in
   *  `path`. Ends in, not contains: the tab now also asks for
   *  `/notebook/7/boundaries` (the Limits view is mounted), and a substring
   *  match counted that as a second read of the note list. */
  function callsTo(path: string): number {
    return fetchMock.mock.calls.filter((c) => {
      const url = String(c[0] as RequestInfo | URL);
      const init = c[1] as RequestInit | undefined;
      return (init?.method ?? "GET") === "GET"
        && new URL(url, "http://127.0.0.1").pathname.endsWith(path);
    }).length;
  }
  return { notes, worker, view, callsTo };
}

/** One turn of the app's own background clock. */
async function onePoll() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(WORKER_POLL_MS);
  });
}

describe("WorkerPanel keeps the note list honest about background work", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    useUiStore.setState({ selectedChatId: CHAT, activeRightPanelTab: "notebook", rightPanelCollapsed: false });
    // The new-note buffer outlives a component, so it outlives a test too.
    useDraftStore.getState().forgetChat(CHAT);
  });
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("shows a note the extractor wrote while the reader sat still",
     async () => {
    // THE DEFECT. Nobody touches the keyboard between these two lines: a run
    // settles in the background, and the note it wrote has to arrive by
    // itself. Before the fix the second note never appeared, because the
    // only thing that invalidated this list was the reader's own mutation.
    const { notes, worker } = openTheNotesTab();
    expect(await screen.findByText(FIRST)).toBeInTheDocument();
    expect(screen.queryByText(SECOND)).not.toBeInTheDocument();

    notes.entries.push(entry(2, SECOND));
    worker.stats.done = 4;

    await onePoll();
    expect(await screen.findByText(SECOND)).toBeInTheDocument();
    // And the note that was already there is still there: a refresh that
    // replaced the list would be a different bug wearing this one's clothes.
    expect(screen.getByText(FIRST)).toBeInTheDocument();
  });

  it("still shows it when the counter went DOWN, not up", async () => {
    // THE TRAP. `stats.done` counts extraction rows across every chat, and
    // deleting a chat deletes its rows. So a run that lands in the open chat
    // (+1) in the same window as a delete elsewhere (-2) leaves the number
    // LOWER than it was, with a real new note behind it. A trigger written
    // as "increased" is silent here; "changed" is not.
    const { notes, worker } = openTheNotesTab();
    expect(await screen.findByText(FIRST)).toBeInTheDocument();

    notes.entries.push(entry(2, SECOND));
    worker.stats.done = 2;

    await onePoll();
    expect(await screen.findByText(SECOND)).toBeInTheDocument();
  });

  it("leaves the list alone when the counter did not move", async () => {
    // GROUND CONTROL. Without this, "invalidate on every poll" passes the two
    // tests above and quietly puts the note list on a twenty-second timer -
    // the thing the record explicitly refuses.
    //
    // The server is seeded WITH a second note that the panel must not see:
    // the sweep can only report a false pass if nothing changed at all, and
    // here something did. The positive control that this sweep can see the
    // note when it is fetched is the first test in this file, on the same
    // fixture and the same tick.
    const { notes, callsTo } = openTheNotesTab();
    expect(await screen.findByText(FIRST)).toBeInTheDocument();

    const notesFetches = callsTo(`/notebook/${CHAT}`);
    const workerFetches = callsTo("/notebook/worker");
    notes.entries.push(entry(2, SECOND));
    // `worker.stats.done` deliberately untouched: the poll returns, nothing
    // has been extracted.

    await onePoll();

    // The tick really landed. Without this the test would pass on a dead
    // clock and prove nothing at all.
    await waitFor(() =>
      expect(callsTo("/notebook/worker")).toBeGreaterThan(workerFetches));
    expect(callsTo(`/notebook/${CHAT}`)).toBe(notesFetches);
    expect(screen.queryByText(SECOND)).not.toBeInTheDocument();
  });

  it("prints the new counter on the card, so the poll is visibly alive",
     async () => {
    // POSITIVE CONTROL for the mechanism the fix rides on. The claim behind
    // this record is an ABSENCE - "nobody refreshes the note list" - and it
    // is only worth anything if the status poll itself demonstrably reaches
    // the screen. It does: the card's run count follows it.
    const { worker } = openTheNotesTab();
    const card = await screen.findByTestId("worker-status");
    expect(card.textContent).toMatch(/3 runs/);

    worker.stats.done = 9;
    worker.worker.runs = 9;

    await onePoll();
    await waitFor(() => expect(card.textContent).toMatch(/9 runs/));
  });

  it("does not send the model catalogue round trip with it", async () => {
    // The refresh invalidates the `notebook` prefix, and the extraction model
    // list is keyed OUTSIDE it on purpose - invalidateQueries refetches an
    // active query whatever its staleTime, so a key inside that prefix would
    // put a call out to OpenRouter every time a background run settled.
    const { notes, worker, callsTo } = openTheNotesTab();
    expect(await screen.findByText(FIRST)).toBeInTheDocument();
    await waitFor(() =>
      expect(callsTo("/notebook/extract/models")).toBeGreaterThan(0));
    const modelFetches = callsTo("/notebook/extract/models");

    notes.entries.push(entry(2, SECOND));
    worker.stats.done = 4;

    await onePoll();
    // The refresh happened...
    expect(await screen.findByText(SECOND)).toBeInTheDocument();
    // ...and the catalogue was not dragged along with it.
    expect(callsTo("/notebook/extract/models")).toBe(modelFetches);
  });

  it("does not eat a half-written note while it refreshes", async () => {
    // A refresh that costs the reader unsent text is a worse defect than the
    // one being fixed. The list is invalidated, not remounted, and the note
    // box reads from the draft store - so the half-written sentence has to
    // still be there, unsent, with the new note beside it.
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const { notes, worker } = openTheNotesTab();
    expect(await screen.findByText(FIRST)).toBeInTheDocument();

    const box = screen.getByLabelText("New note");
    await user.type(box, "She never says the brother's");
    expect(box).toHaveValue("She never says the brother's");

    notes.entries.push(entry(2, SECOND));
    worker.stats.done = 4;
    await onePoll();

    expect(await screen.findByText(SECOND)).toBeInTheDocument();
    expect(screen.getByLabelText("New note"))
      .toHaveValue("She never says the brother's");
    // And nothing was filed on the reader's behalf.
    const posts = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls
      .filter((c) => (c[1] as RequestInit | undefined)?.method === "POST");
    expect(posts).toHaveLength(0);
  });

  it("does not ask for the note list twice just for opening the tab",
     async () => {
    // The first status response is not news. The list was fetched on the
    // same mount and is already as fresh as that number, so treating the
    // first sighting of `done` as a change would buy a duplicate round trip
    // every time the reader opens this tab. The positive control that this
    // counter does move when it should is the first test in this file.
    const { callsTo } = openTheNotesTab();
    expect(await screen.findByText(FIRST)).toBeInTheDocument();
    await screen.findByTestId("worker-status");
    await onePoll();
    expect(callsTo(`/notebook/${CHAT}`)).toBe(1);
  });

  it("asks once even when StrictMode runs the effect twice", async () => {
    // The app mounts under StrictMode (main.tsx), which invokes every effect
    // twice on mount in a development build. The second invocation sees a
    // counter it has already recorded, so the guard that matters here is
    // "this is the same number", not "I have seen a number before" - without
    // it the second pass buys a round trip nobody asked for, on every mount,
    // for every reader running a dev build.
    const { callsTo } = openTheNotesTab(StrictMode);
    expect(await screen.findByText(FIRST)).toBeInTheDocument();
    await screen.findByTestId("worker-status");
    await onePoll();
    expect(callsTo(`/notebook/${CHAT}`)).toBe(1);
  });

  it("still refreshes on the reader's own note, as it always did", async () => {
    // GROUND CONTROL for the chain that already worked. The fix adds a second
    // way into the same invalidation and must not stand on the first one's
    // toes: a note the reader files still refetches the list.
    const { notes, callsTo } = openTheNotesTab();
    expect(await screen.findByText(FIRST)).toBeInTheDocument();

    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const before = callsTo(`/notebook/${CHAT}`);
    notes.entries.push(entry(2, SECOND));

    await user.type(screen.getByLabelText("New note"), SECOND);
    await user.click(screen.getByLabelText("Add note"));

    await waitFor(() =>
      expect(callsTo(`/notebook/${CHAT}`)).toBeGreaterThan(before));
    expect(await screen.findByText(SECOND)).toBeInTheDocument();
  });
});
