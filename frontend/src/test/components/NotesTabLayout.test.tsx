/**
 * NotesTabLayout.test.tsx - the Notes tab says what matters first.
 *
 * Five claims, each read off the real RightPanel over a fake server:
 *   1.  the first words of the Notes view say whether THIS chat is read, and
 *       a "Not read" line leads to its reason (Settings), which opens on it;
 *   2.  waiting suggestions are counted and come first, with Keep all (hand
 *       keeps, one per row) and Discard all (held, undoable, sent only when
 *       the tab or the chat is left);
 *   4.  three views in the WAI-ARIA tabs pattern, and a view that holds
 *       something to act on says so in its tab's name;
 *   5.  the Notes tab button counts what waits, from any tab, and says
 *       nothing when nothing waits.
 * 3. (the first note on the first screen) is a layout measure, taken in a
 * browser; here only the order it rests on.
 *
 * Every string is imported from its prose module. Every claim has a ground
 * control (the reader can see the opposite) and, where it asserts an
 * absence, a positive control.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act, cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";

import { RightPanel } from "@/components/layout/RightPanel";
import { GenerationSettingsProvider } from "@/components/generation/GenerationSettingsContext";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { settingsFixture } from "@/test/mocks/fixtures";
import { hiddenFromReaders } from "@/test/helpers/accessibility";
import { useUiStore } from "@/lib/store/uiStore";
import { useSeenNotesStore } from "@/lib/chat/seenNotes";
import { useHeldDiscards } from "@/lib/chat/heldDiscards";
import { useDraftStore } from "@/lib/store/draftStore";
import { WORKER_POLL_MS } from "@/lib/query/notebook";
import { NOTE_ROW_WORDS } from "@/components/notebook/noteProse";
import {
  CHAT_READ_WORDS,
  NOTES_TARGET,
  NOTES_VIEW_WORDS,
  WAITING_WORDS,
  notesTabName,
  viewName,
} from "@/components/notebook/notesTabProse";
import {
  chatReadState,
  useNotesViewStore,
  type ChatReadInput,
} from "@/components/notebook/notesTabState";
import type { WorkerStatus } from "@/lib/schemas/notebook";

const CHAT = 7;
const T = "2026-09-28 10:00:00";

type Note = Record<string, unknown> & { id: number; status: string };

function note(id: number, over: Record<string, unknown> = {}): Note {
  return {
    id, chat_id: CHAT, position: id, kind: "fact", text: `note number ${id}`,
    evidence: null, durability: "story", importance: 2, pinned: 0, retired_at: null,
    superseded_by: null, excluded_reason: null, status: "accepted",
    provenance: "user", source_message_id: null, evidence_role: null,
    edited_at: null, hand_accepted_at: null, created_at: T, updated_at: T, ...over,
  };
}

function workerBody(over: {
  state?: string; chat?: string | null; alive?: boolean; refusal?: string | null;
  calls?: number; cap?: number; job?: "running" | "done" | null; jobChat?: number; done?: number;
  unread?: number | null;
} = {}) {
  return {
    stats: { done: over.done ?? 5, failed: 0, skipped: 0, skip_reasons: {} },
    spend: { calls: over.calls ?? 3, tokens_in: 1, tokens_out: 1, cost: 0.0001 },
    spend_lifetime: { calls: 3, tokens_in: 1, tokens_out: 1, cost: 0.0001 },
    worker: {
      state: over.state ?? "closed", failures: 0, total_failures: 0, queued: 0,
      dropped_offers: 0, runs: 5, batch_size: 20, alive: over.alive ?? true,
      backlog: { chats: 1, messages: 12 }, trigger_threshold: 20,
      chat: over.chat ? { state: over.chat, failures: 3 } : null,
      backfill: over.job ? {
        job_id: 3, chat_id: over.jobChat ?? CHAT, state: over.job, scope: "all",
        calls_planned: 4, calls_spent: 1, notes_written: 0, messages_left: 9, last_error: null,
      } : null,
    },
    daily_cap: over.cap ?? 60,
    chat_refusal: over.refusal ?? null,
    chat_unread: over.unread === undefined ? 4 : over.unread,
  };
}

/** A small server: the note list and the worker can change between polls,
 *  and every write is recorded. */
function server(init: {
  notes: Note[]; worker?: ReturnType<typeof workerBody>; failSettings?: boolean;
  noModel?: boolean; app?: Partial<typeof settingsFixture>; proxyHealthy?: boolean; failApp?: boolean;
  /** Holds the note-model answer until this promise settles. */
  holdExtract?: Promise<void>;
}) {
  const st = { notes: init.notes, worker: init.worker ?? workerBody(), writes: [] as string[], proxyAsks: 0 };
  const json = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  const fetchMock = vi.fn(async (input: RequestInfo | URL, reqInit?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = (reqInit?.method ?? "GET").toUpperCase();
    const path = new URL(url, "http://127.0.0.1").pathname;
    if (method !== "GET") st.writes.push(`${method} ${path}`);
    const accept = /\/notebook\/entries\/(\d+)\/accept$/.exec(path);
    if (accept && method === "POST") {
      const n = st.notes.find((x) => x.id === Number(accept[1]))!;
      Object.assign(n, { status: "accepted", hand_accepted_at: T });
      return json(n);
    }
    const entry = /\/notebook\/entries\/(\d+)$/.exec(path);
    if (entry && method === "DELETE") {
      st.notes = st.notes.filter((x) => x.id !== Number(entry[1]));
      return json({ ok: true, kept: false });
    }
    if (path.endsWith("/vault/status")) return json({ initialized: true, unlocked: true });
    if (path.endsWith("/notebook/worker")) return json(st.worker);
    if (path.endsWith("/notebook/extract/settings")) {
      if (init.holdExtract) await init.holdExtract;
      return init.failSettings ? json({ detail: "internal_error" }, 500)
        : json({ model_id: init.noModel ? null : "cheap/one", prompt_language: "en" });
    }
    if (path.endsWith("/notebook/extract/models")) {
      return json({ models: [{ id: "cheap/one", provider: "P", prompt_price: 0.06, context_length: 1000, endpoints: 2 }] });
    }
    if (path.includes("/notebook/extract/plan/")) {
      return json({ ready: true, blocked_by: [], scope: "all", messages_total: 12, messages_unread: 5,
        chunks: 1, calls_planned: 1, daily_cap: 60, backfill_cap: 50, live_reserve: 10,
        calls_spent_today: 3, fits_today: true, prompt_price: 0.06, cost_estimate: 0.001,
        model_id: "cheap/one", model_is_chat_bind: false });
    }
    if (path.endsWith("/auto-accept")) return json({ enabled: true, effective: true, overridden: false });
    if (path.endsWith("/notebook/safeword")) return json({ word: "" });
    if (path.endsWith("/boundaries")) return json({ boundaries: [], use_global: true });
    if (path.endsWith(`/notebook/${CHAT}`)) return json({ entries: st.notes, notebook_chars: 10 });
    if (path.endsWith(`/chats/${CHAT}/messages`)) return json([]);
    if (path.endsWith("/proxy/health")) {
      st.proxyAsks += 1;
      return json({ healthy: init.proxyHealthy ?? true, latency_ms: 40, reason: null, cached: false });
    }
    if (path.endsWith("/settings")) {
      return init.failApp ? json({ detail: "internal_error" }, 500) : json({ ...settingsFixture, ...init.app });
    }
    return json({ detail: `no stub ${method} ${path}` }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return st;
}

function Frame({ children }: { children: ReactNode }) {
  return <GenerationSettingsProvider>{children}</GenerationSettingsProvider>;
}

function mount(tab: "notebook" | "models" = "notebook") {
  useUiStore.setState({ selectedChatId: CHAT, activeRightPanelTab: tab, rightPanelCollapsed: false });
  return renderWithQueryClient(<RightPanel />, { wrapper: Frame });
}

const viewList = () => screen.getByRole("tablist", { name: NOTES_VIEW_WORDS.list });
const viewTab = (view: "notes" | "limits" | "settings") =>
  within(viewList()).getAllByRole("tab").find((t) =>
    (t.textContent ?? "").replace(/\d+$/, "").trim() === NOTES_VIEW_WORDS[view])!;
const outerNotesTab = () => screen.getAllByRole("tablist")[0].querySelector<HTMLElement>('[data-testid="notes-tab-trigger"]')!;
const flat = (s: string | null | undefined) => (s ?? "").replace(/\s+/g, " ").trim();
const follows = (a: Node, b: Node) => (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0;

beforeEach(() => {
  vi.restoreAllMocks();
  useSeenNotesStore.setState({ byChat: {} });
  useHeldDiscards.setState({ byChat: {} });
  useDraftStore.getState().clearAll();
  useNotesViewStore.setState({ view: "notes", focusTarget: null });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  useUiStore.setState({ selectedChatId: null, activeRightPanelTab: "models" });
});

// ------------------------------------------------------------------ 1
describe("the line about this chat, first", () => {
  const status = (over: Parameters<typeof workerBody>[0] = {}) =>
    workerBody(over) as unknown as WorkerStatus;
  const not = (why: keyof typeof CHAT_READ_WORDS.why) => `${CHAT_READ_WORDS.notRead} ${CHAT_READ_WORDS.why[why]}`;

  it("says each state in its own words, and never guesses", () => {
    const read = (st: WorkerStatus | undefined, over: Partial<ChatReadInput> = {}) =>
      chatReadState({ status: st, modelId: "m", chatId: CHAT, apiKeySet: true, proxyDown: false, ...over });
    const text = (r: ReturnType<typeof chatReadState>) => (r as { text: string }).text;
    const fix = (r: ReturnType<typeof chatReadState>) => (r as { fix?: unknown }).fix;
    // Healthy: "Up to date." ONLY on this chat's live zero; a count above
    // zero, or no count at all (an older server), says it is being read.
    expect(read(status({ unread: 0 }))).toEqual({ kind: "ok", text: CHAT_READ_WORDS.upToDate });
    expect(read(status({ unread: 4 }))).toEqual({ kind: "ok", text: CHAT_READ_WORDS.reading });
    expect(read(status({ unread: null }))).toEqual({ kind: "ok", text: CHAT_READ_WORDS.reading });
    // No key and a required proxy down stop every read, first, and are fixed
    // on the Security tab.
    expect(text(read(status({ unread: 0 }), { apiKeySet: false }))).toBe(not("noKey"));
    expect(fix(read(status(), { apiKeySet: false }))).toEqual({ target: NOTES_TARGET.apiKey, where: "security" });
    expect(text(read(status({ unread: 0 }), { proxyDown: true }))).toBe(not("proxy"));
    expect(fix(read(status(), { proxyDown: true }))).toEqual({ target: NOTES_TARGET.proxy, where: "security" });
    // Unknown key or proxy state is not a reason (ground).
    expect(read(status({ unread: 0 }), { apiKeySet: undefined, proxyDown: undefined }).kind).toBe("ok");
    // No model picked beats everything else, even an unknown worker.
    expect(text(read(undefined, { modelId: null }))).toBe(not("noModel"));
    expect(fix(read(undefined, { modelId: null }))).toEqual({ target: NOTES_TARGET.noteModel, where: "settings" });
    // Unknown and failed are said, not guessed as healthy.
    expect(text(read(undefined))).toBe(CHAT_READ_WORDS.checking);
    expect(text(read(status({ unread: 0 }), { failed: true }))).toBe(CHAT_READ_WORDS.couldNotCheck);
    // A required proxy's health still on its way claims nothing.
    expect(text(read(status({ unread: 0 }), { pending: true, proxyDown: undefined }))).toBe(CHAT_READ_WORDS.checking);
    expect(text(read(status({ chat: "half_open" }), { pending: true }))).toBe(CHAT_READ_WORDS.checking);
    // ...but an alarm the answers already prove is still said.
    expect(text(read(status({ alive: false }), { pending: true }))).toBe(not("readerStopped"));
    expect(text(read(status({ alive: false })))).toBe(not("readerStopped"));
    expect(fix(read(status({ alive: false })))).toEqual({ target: NOTES_TARGET.workerCard, where: "settings" });
    expect(text(read(status({ refusal: "model_not_extractable" })))).toBe(not("modelCannot"));
    expect(text(read(status({ chat: "stopped" })))).toBe(not("chatStopped"));
    expect(fix(read(status({ chat: "stopped" })))).toEqual({ target: NOTES_TARGET.tryChat, where: "settings" });
    expect(text(read(status({ chat: "open" })))).toBe(not("chatPaused"));
    expect(text(read(status({ state: "stopped" })))).toBe(not("allStopped"));
    expect(fix(read(status({ state: "stopped" })))).toEqual({ target: NOTES_TARGET.tryAll, where: "settings" });
    expect(text(read(status({ state: "open" })))).toBe(not("allPaused"));
    // half_open is a billed test call, not "not read".
    expect(read(status({ chat: "half_open" }))).toMatchObject({ kind: "test", text: CHAT_READ_WORDS.testHere });
    expect(read(status({ state: "half_open" }))).toMatchObject({ kind: "test", text: CHAT_READ_WORDS.testAll });
    // The day's cap: at the cap not read; one under it, read (ground).
    expect(text(read(status({ calls: 60, cap: 60 })))).toBe(not("capUsed"));
    expect(read(status({ calls: 59, cap: 60 })).kind).toBe("ok");
    // The cap beats a test-call promise it would refuse.
    expect(text(read(status({ calls: 60, cap: 60, state: "half_open" })))).toBe(not("capUsed"));
    // A reading job: this chat's, and not another chat's (ground).
    expect(read(status({ job: "running" }))).toEqual({ kind: "reading", text: CHAT_READ_WORDS.readingNow });
    expect(read(status({ job: "running", jobChat: 99 })).kind).toBe("ok");
    expect(read(status({ job: "done" })).kind).toBe("ok");
  });

  it("is not \"Up to date.\" while part of a very long message is left (68 a)", () => {
    const read = (st: WorkerStatus) =>
      chatReadState({ status: st, modelId: "m", chatId: CHAT, apiKeySet: true, proxyDown: false });
    const withParts = (long_message: WorkerStatus["long_message"]) =>
      ({ ...status({ unread: 0 }), long_message }) as WorkerStatus;
    // GROUND: no parts, or all zero, is up to date.
    expect(read(withParts(null)).text).toBe(CHAT_READ_WORDS.upToDate);
    expect(read(withParts({ pending: 0, failed: 0, unread: 0 })).text).toBe(CHAT_READ_WORDS.upToDate);
    for (const left of [{ pending: 2, failed: 0, unread: 0 }, { pending: 0, failed: 1, unread: 0 },
      { pending: 0, failed: 0, unread: 1 }]) {
      expect(read(withParts(left))).toEqual({ kind: "ok", text: CHAT_READ_WORDS.reading });
    }
  });

  it("opens the Notes view on it, before the count and before any note", async () => {
    server({ notes: [note(1), note(2)] });
    mount();
    const line = await screen.findByTestId("chat-read-line");
    await waitFor(() => expect(flat(line.textContent)).toBe(CHAT_READ_WORDS.reading));
    const count = screen.getByTestId("notebook-sent-count");
    const first = await screen.findByTestId("note-1");
    expect(follows(line, count), "the count is before the line").toBe(true);
    expect(follows(line, first), "a note is before the line").toBe(true);
    // Healthy: no way-to-a-reason button is offered (it sits in a shut Collapse).
    expect(screen.queryByRole("button", { name: CHAT_READ_WORDS.openSettings })).toBeNull();
    // GROUND: the reader of that absence can see the button when it exists.
    cleanup();
    server({ notes: [note(1)], worker: workerBody({ refusal: "model_not_extractable" }) });
    mount();
    expect(await screen.findByRole("button", { name: CHAT_READ_WORDS.openSettings })).toBeInTheDocument();
  });

  it("says Not read in the danger ink, and Open Settings lands on the control that fixes it", async () => {
    const user = userEvent.setup();
    server({ notes: [note(1)], worker: workerBody({ refusal: "model_not_extractable" }) });
    mount();
    const want = `${CHAT_READ_WORDS.notRead} ${CHAT_READ_WORDS.why.modelCannot}`;
    const line = await screen.findByTestId("chat-read-line");
    await waitFor(() => expect(flat(line.textContent)).toBe(want));
    expect(line.getAttribute("style") ?? "").toContain("--es-island-danger-ink");
    await user.click(screen.getByRole("button", { name: CHAT_READ_WORDS.openSettings }));
    expect(viewTab("settings")).toHaveAttribute("aria-selected", "true");
    // On the note model picker, the thing to change; not on a tab.
    await waitFor(() => expect(document.activeElement?.getAttribute("data-notes-target"))
      .toBe(NOTES_TARGET.noteModel));
    // The Settings view opens on the same reason, above the card's "Running.".
    const top = screen.getByTestId("settings-chat-read-line");
    expect(flat(top.textContent)).toBe(want);
    expect(follows(top, screen.getByTestId("worker-status"))).toBe(true);
    // GROUND: healthy, the Settings view carries no such line.
    cleanup();
    server({ notes: [note(1)] });
    mount();
    await screen.findByTestId("worker-status");
    expect(screen.queryByTestId("settings-chat-read-line")).toBeNull();
  });

  it("a stopped chat lands on its own Try again", async () => {
    const user = userEvent.setup();
    server({ notes: [note(1)], worker: workerBody({ chat: "stopped" }) });
    mount();
    await user.click(await screen.findByRole("button", { name: CHAT_READ_WORDS.openSettings }));
    await waitFor(() => expect(document.activeElement?.getAttribute("data-notes-target"))
      .toBe(NOTES_TARGET.tryChat));
    expect(document.activeElement?.tagName).toBe("BUTTON");
  });

  it("skips a target that cannot take focus (inert) and lands on the one that can", async () => {
    const user = userEvent.setup();
    server({ notes: [note(1)], worker: workerBody({ calls: 60, cap: 60 }) });
    mount();
    const card = await screen.findByTestId("worker-status");
    // A decoy with the same target, first in the document, inside a shut
    // (inert) box: the way a Collapse holds a control it has closed.
    const shut = document.createElement("div");
    shut.setAttribute("inert", "");
    const decoy = document.createElement("button");
    decoy.setAttribute("data-notes-target", NOTES_TARGET.workerCard);
    shut.appendChild(decoy);
    screen.getAllByRole("tabpanel")[0].prepend(shut);
    await user.click(await screen.findByRole("button", { name: CHAT_READ_WORDS.openSettings }));
    await waitFor(() => expect(document.activeElement).toBe(card));
    expect(document.activeElement).not.toBe(decoy);
  });

  it("no API key: says so, and Open Security puts the keyboard in the key field", async () => {
    const user = userEvent.setup();
    server({ notes: [note(1)], worker: workerBody({ unread: 0 }), app: { api_key_set: false } });
    mount();
    const line = await screen.findByTestId("chat-read-line");
    await waitFor(() => expect(flat(line.textContent)).toBe(`${CHAT_READ_WORDS.notRead} ${CHAT_READ_WORDS.why.noKey}`));
    // Its fix is on another tab, so the Settings view is not marked for it.
    expect(viewTab("settings")).not.toHaveAttribute("aria-label");
    await user.click(screen.getByRole("button", { name: CHAT_READ_WORDS.openSecurity }));
    expect(useUiStore.getState().activeRightPanelTab).toBe("secrets");
    await waitFor(() => expect(document.activeElement?.getAttribute("data-notes-target")).toBe(NOTES_TARGET.apiKey));
    expect(document.activeElement?.tagName).toBe("INPUT");
  });

  it("never says Up to date while the note model is not answered yet", async () => {
    let release = () => {};
    const hold = new Promise<void>((r) => { release = r; });
    server({ notes: [note(1)], worker: workerBody({ unread: 0 }), noModel: true, holdExtract: hold });
    mount();
    const line = await screen.findByTestId("chat-read-line");
    // Every text the line shows while the answer is held.
    const seen = new Set<string>();
    const watch = new MutationObserver(() => seen.add(flat(line.textContent)));
    watch.observe(line, { childList: true, characterData: true, subtree: true });
    seen.add(flat(line.textContent));
    // The worker answer (unread 0) has arrived; the note model has not.
    await waitFor(() => expect((globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls
      .some((c) => String(c[0]).includes("/notebook/worker"))).toBe(true));
    await new Promise((r) => setTimeout(r, 60));
    expect(flat(line.textContent)).toBe(CHAT_READ_WORDS.checking);
    act(() => release());
    await waitFor(() => expect(flat(line.textContent)).toBe(`${CHAT_READ_WORDS.notRead} ${CHAT_READ_WORDS.why.noModel}`));
    watch.disconnect();
    expect([...seen]).not.toContain(CHAT_READ_WORDS.upToDate);
    expect([...seen]).not.toContain(CHAT_READ_WORDS.reading);
  });

  it("settings that could not be read are not read as fine", async () => {
    server({ notes: [note(1)], worker: workerBody({ unread: 0 }), failApp: true });
    mount();
    const line = await screen.findByTestId("chat-read-line");
    await waitFor(() => expect(flat(line.textContent)).toBe(CHAT_READ_WORDS.couldNotCheck));
    // GROUND: the same worker answer with the settings read is up to date.
    cleanup();
    server({ notes: [note(1)], worker: workerBody({ unread: 0 }) });
    mount();
    await waitFor(() => expect(flat(screen.getByTestId("chat-read-line").textContent)).toBe(CHAT_READ_WORDS.upToDate));
  });

  it("a required proxy that is down says so, and asks its health only while a proxy is required", async () => {
    const st = server({
      notes: [note(1)], worker: workerBody({ unread: 0 }),
      app: { proxy_required: true, proxy_configured: true }, proxyHealthy: false,
    });
    mount();
    const line = await screen.findByTestId("chat-read-line");
    await waitFor(() => expect(flat(line.textContent)).toBe(`${CHAT_READ_WORDS.notRead} ${CHAT_READ_WORDS.why.proxy}`));
    expect(screen.getByRole("button", { name: CHAT_READ_WORDS.openSecurity })).toBeInTheDocument();
    expect(st.proxyAsks).toBeGreaterThan(0);
    // GROUND: no proxy required, no health asked, and the line is healthy.
    cleanup();
    const quiet = server({ notes: [note(1)], worker: workerBody({ unread: 0 }) });
    mount();
    await waitFor(() => expect(flat(screen.getByTestId("chat-read-line").textContent)).toBe(CHAT_READ_WORDS.upToDate));
    expect(quiet.proxyAsks).toBe(0);
  });
});

describe("waiting suggestions, counted and first", () => {
  const notes = () => [
    note(1, { pinned: 1 }), note(2),
    note(10, { status: "proposed", provenance: "model" }),
    note(11, { status: "proposed", provenance: "model" }),
    note(12, { status: "proposed", provenance: "model" }),
  ];

  it("counts them at the top and draws them before the kept notes", async () => {
    server({ notes: notes() });
    mount();
    const head = await screen.findByTestId("notes-waiting-count");
    await waitFor(() => expect(flat(head.textContent)).toBe(WAITING_WORDS.count(3)));
    expect(hiddenFromReaders(head)).toBe(false);
    const firstKept = screen.getByTestId("note-1");
    for (const id of [10, 11, 12]) {
      expect(follows(screen.getByTestId(`note-${id}`), firstKept), `suggestion ${id} is after a kept note`).toBe(true);
    }
    expect(follows(head, screen.getByTestId("note-10"))).toBe(true);
    // GROUND: with none waiting there is no count at all.
    cleanup();
    server({ notes: [note(1), note(2)] });
    mount();
    await screen.findByTestId("note-1");
    // The group's box stays mounted for its closing motion, shut: out of
    // reach and unheard, which is what "not there" means to a reader.
    expect(hiddenFromReaders(screen.getByTestId("notes-waiting-count"))).toBe(true);
    expect(screen.queryByRole("button", { name: WAITING_WORDS.keepAll })).toBeNull();
  });

  it("Keep all keeps every waiting row by hand and nothing else, then hands the keyboard on", async () => {
    const user = userEvent.setup();
    const st = server({ notes: notes() });
    mount();
    await user.click(await screen.findByRole("button", { name: WAITING_WORDS.keepAll }));
    await waitFor(() => expect(hiddenFromReaders(screen.getByTestId("notes-waiting-count"))).toBe(true));
    const accepts = st.writes.filter((w) => w.startsWith("POST") && w.endsWith("/accept"));
    expect(accepts).toHaveLength(3);
    for (const id of [10, 11, 12]) expect(accepts.some((a) => a.endsWith(`/entries/${id}/accept`))).toBe(true);
    // Nothing kept was touched, nothing deleted.
    expect(st.writes.some((w) => /entries\/(1|2)\//.test(w))).toBe(false);
    expect(st.writes.some((w) => w.startsWith("DELETE"))).toBe(false);
    // Hand keeps: the server marks each (the stub writes hand_accepted_at on accept).
    expect(st.notes.filter((n) => n.hand_accepted_at).map((n) => n.id).sort()).toEqual([10, 11, 12]);
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("textbox", { name: "New note" })));
  });

  it("Discard all stands as one line with one Undo, sends nothing across a view switch, and Undo puts them back", async () => {
    const user = userEvent.setup();
    const st = server({ notes: notes() });
    mount();
    await user.click(await screen.findByRole("button", { name: WAITING_WORDS.discardAll }));
    const batch = await screen.findByTestId("notes-discarded-batch");
    await waitFor(() => expect(hiddenFromReaders(batch)).toBe(false));
    expect(flat(within(batch).getByRole("status").textContent)).toBe(WAITING_WORDS.discarded(3));
    // One line, one Undo: no row per suggestion beside it.
    expect(screen.queryAllByText(NOTE_ROW_WORDS.discarded)).toHaveLength(0);
    expect(screen.queryAllByRole("button", { name: NOTE_ROW_WORDS.undoDiscard })).toHaveLength(0);
    const undoAll = screen.getByRole("button", { name: WAITING_WORDS.undoAll });
    expect(document.activeElement).toBe(undoAll);
    // A look at the limits and back: still held, nothing sent.
    await user.click(viewTab("limits"));
    await user.click(viewTab("notes"));
    expect(st.writes.filter((w) => w.startsWith("DELETE"))).toEqual([]);
    await user.click(screen.getByRole("button", { name: WAITING_WORDS.undoAll }));
    await waitFor(() => expect(hiddenFromReaders(screen.getByTestId("notes-discarded-batch"))).toBe(true));
    for (const id of [10, 11, 12]) expect(screen.getByTestId(`note-${id}`)).toBeInTheDocument();
    expect(flat(screen.getByTestId("notes-waiting-count").textContent)).toBe(WAITING_WORDS.count(3));
    expect(document.activeElement).toBe(screen.getByRole("button", { name: WAITING_WORDS.discardAll }));
    // GROUND: a single Discard still gets its own row and Undo.
    await user.click(within(screen.getByTestId("note-10")).getByRole("button", { name: NOTE_ROW_WORDS.discard }));
    expect(await screen.findByText(NOTE_ROW_WORDS.discarded)).toBeInTheDocument();
    // POSITIVE CONTROL: leaving the chat does send what is held.
    await user.click(screen.getByRole("button", { name: WAITING_WORDS.discardAll }));
    act(() => { useUiStore.setState({ selectedChatId: 8 }); });
    await waitFor(() => expect(st.writes.filter((w) => w.startsWith("DELETE"))).toHaveLength(3));
  });

  it("a search does not hide a waiting suggestion", async () => {
    const user = userEvent.setup();
    server({ notes: [...notes(), note(3), note(4), note(5)] });
    mount();
    await user.type(await screen.findByRole("textbox", { name: "Search notes" }), "number 1");
    // "number 1" matches notes 1, 10, 11, 12: note 2 goes, every suggestion stays.
    await waitFor(() => expect(screen.queryByTestId("note-2")).toBeNull());
    for (const id of [10, 11, 12]) expect(screen.getByTestId(`note-${id}`)).toBeInTheDocument();
    await user.clear(screen.getByRole("textbox", { name: "Search notes" }));
    await user.type(screen.getByRole("textbox", { name: "Search notes" }), "zzz");
    await waitFor(() => expect(screen.queryByTestId("note-1")).toBeNull());
    for (const id of [10, 11, 12]) expect(screen.getByTestId(`note-${id}`)).toBeInTheDocument();
    expect(flat(screen.getByTestId("notes-waiting-count").textContent)).toBe(WAITING_WORDS.count(3));
  });

  it("a single Keep moves the row into the list with the keyboard on its Pin", async () => {
    const user = userEvent.setup();
    server({ notes: notes() });
    mount();
    const row = await screen.findByTestId("note-10");
    await user.click(within(row).getByRole("button", { name: NOTE_ROW_WORDS.keep }));
    await waitFor(() => expect(screen.getByTestId("note-10").dataset.state).toBe("live"));
    const moved = screen.getByTestId("note-10");
    expect(follows(screen.getByTestId("note-2"), moved) || follows(screen.getByTestId("note-1"), moved)).toBe(true);
    await waitFor(() => expect(document.activeElement).toBe(
      within(moved).getByRole("button", { name: NOTE_ROW_WORDS.pin })));
  });
});

// ------------------------------------------------------------------ 4
describe("three views, the WAI-ARIA tabs pattern", () => {
  it("names the list, moves between tabs with the arrow keys and shows one view at a time", async () => {
    const user = userEvent.setup();
    server({ notes: [note(1)] });
    mount();
    await screen.findByTestId("note-1");
    const tabs = within(viewList()).getAllByRole("tab");
    expect(tabs.map((t) => flat(t.textContent))).toEqual(
      [NOTES_VIEW_WORDS.notes, NOTES_VIEW_WORDS.limits, NOTES_VIEW_WORDS.settings]);
    // The view is not named like the panel tab it sits in.
    expect(NOTES_VIEW_WORDS.notes).not.toBe(flat(outerNotesTab().textContent));
    expect(viewTab("notes")).toHaveAttribute("aria-selected", "true");
    // Each tab controls a panel that is labelled by it.
    for (const t of tabs) {
      const panel = document.getElementById(t.getAttribute("aria-controls")!);
      expect(panel?.getAttribute("role")).toBe("tabpanel");
      expect(panel?.getAttribute("aria-labelledby")).toBe(t.id);
      // Not a tab stop of its own: each view opens on controls, so Tab goes
      // from the tab to the first of them (a stop here drew a 1.96:1 ring).
      expect(panel?.tabIndex).toBe(-1);
    }
    // Keyboard: focus the selected tab, ArrowRight moves to the next one.
    act(() => viewTab("notes").focus());
    await user.keyboard("{ArrowRight}");
    expect(document.activeElement).toBe(viewTab("limits"));
    await user.keyboard("{Enter}");
    expect(viewTab("limits")).toHaveAttribute("aria-selected", "true");
    // One view shown: the limits form is in reach, the note list is not.
    expect(screen.getByRole("textbox", { name: "New limit" })).toBeVisible();
    expect(screen.queryByRole("textbox", { name: "New note" })).toBeNull();
    // Only the tabs are tab stops in the list (roving tabindex).
    expect(tabs.filter((t) => t.tabIndex === 0)).toHaveLength(1);
  });

  it("marks a view that holds something to act on, in its name and with a dot, and only then", async () => {
    server({ notes: [note(1)], failSettings: true });
    mount();
    await waitFor(() => expect(viewTab("settings")).toHaveAttribute("aria-label", viewName("settings", true)));
    expect(screen.getByTestId("notes-view-settings-dot")).toBeInTheDocument();
    expect(viewTab("limits")).not.toHaveAttribute("aria-label");
    // GROUND: all read fine, no mark anywhere.
    cleanup();
    server({ notes: [note(1)] });
    mount();
    await screen.findByTestId("worker-status");
    await waitFor(() => expect(screen.getByTestId("chat-read-line").dataset.read).toBe("ok"));
    expect(viewTab("settings")).not.toHaveAttribute("aria-label");
    expect(screen.queryByTestId("notes-view-settings-dot")).toBeNull();
  });
});

// ------------------------------------------------------------------ 5
describe("the Notes tab counts what waits, from any tab", () => {
  it("counts unseen model notes and waiting suggestions, named for a screen reader", async () => {
    server({ notes: [
      note(1), note(2, { provenance: "model" }), note(3, { provenance: "model", hand_accepted_at: T }),
      note(10, { status: "proposed", provenance: "model" }),
    ] });
    mount("models");
    // 1 unseen model note (2; 3 was kept by hand) + 1 waiting (10) = 2.
    await waitFor(() => expect(screen.getByTestId("notes-tab-count").textContent).toBe("2"));
    expect(outerNotesTab()).toHaveAttribute("aria-label", notesTabName(2));
    // Once, on the panel's tab: the Notebook view's tab carries no count.
    act(() => { useUiStore.setState({ activeRightPanelTab: "notebook" }); });
    await screen.findByRole("tablist", { name: NOTES_VIEW_WORDS.list });
    expect(flat(viewTab("notes").textContent)).toBe(NOTES_VIEW_WORDS.notes);
    expect(viewTab("notes")).not.toHaveAttribute("aria-label");
    expect(screen.getAllByTestId("notes-tab-count")).toHaveLength(1);
    // Label in name: the visible "Notes 2" is inside the name.
    expect(notesTabName(2).toLowerCase()).toContain(flat(outerNotesTab().textContent).replace(/(\D)(\d)/, "$1 $2").toLowerCase());
  });

  it("says nothing when nothing waits", async () => {
    server({ notes: [note(1), note(2, { provenance: "model", hand_accepted_at: T })] });
    mount("models");
    // Settled: the list arrived (the Notes tab reads it for the count).
    await waitFor(() => expect(
      (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.some((c) => String(c[0]).endsWith(`/notebook/${CHAT}`))).toBe(true));
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByTestId("notes-tab-count")).toBeNull();
    expect(outerNotesTab()).not.toHaveAttribute("aria-label");
    expect(flat(outerNotesTab().textContent)).toBe(notesTabName(0));
  });

  it("notices a note written in the background while another tab is open", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const st = server({ notes: [note(1)] });
    mount("models");
    await waitFor(() => expect(
      (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.some((c) => String(c[0]).includes("/notebook/worker"))).toBe(true));
    await act(async () => { await vi.advanceTimersByTimeAsync(50); });
    expect(screen.queryByTestId("notes-tab-count")).toBeNull();
    st.notes = [...st.notes, note(20, { status: "proposed", provenance: "model" })];
    st.worker = workerBody({ done: 6 });
    await act(async () => { await vi.advanceTimersByTimeAsync(WORKER_POLL_MS); });
    await waitFor(() => expect(screen.getByTestId("notes-tab-count").textContent).toBe("1"));
  });
});
