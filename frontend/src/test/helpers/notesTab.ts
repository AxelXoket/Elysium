/**
 * notesTab.ts - the right panel's Notes tab, opened the way RightPanel
 * composes it, with every block showing what it can show.
 *
 * Three suites read the whole tab: the chrome-step sweep in fontScale.test.ts
 * (every button's words), the 300 px floor rows in css-contract.test.ts
 * (every Collapse, and the button that did not fit) and the ink rows in
 * right-panel-readability.test.ts (every line's colour). They share this one
 * fixture, and the way it is settled and armed, so a state added for one of
 * them is read by all three.
 *
 * What the routes put on screen:
 * - the worker card with a breaker that is open (its reset), a chat that
 *   stopped on its own (that chat's reset) and a known auto-accept answer
 *   (the per-chat button, in whichever wording the answer asks for);
 * - the strip with its backlog offer, a finished run with notes to undo, and
 *   a preview the plan route answers once the strip is opened;
 * - the saved-notes block (two notes the model saved), the reader's own
 *   notes, and one proposal (its Keep-suggestion button): six notes, so the
 *   search box is open too;
 * - one limit, and both extraction pickers.
 */
import { UNDO_RUN_CONFIRM } from "@/components/notebook/backfillProse";
import { NOTE_ROW_WORDS } from "@/components/notebook/noteProse";
import { createElement } from "react";
import { screen, within } from "@testing-library/react";
import { NOTES_VIEW_WORDS, type NotesView } from "@/components/notebook/notesTabProse";
import { useNotesViewStore } from "@/components/notebook/notesTabState";
import { RightPanel } from "@/components/layout/RightPanel";
import { GenerationSettingsProvider } from "@/components/generation/GenerationSettingsContext";
import { useUiStore } from "@/lib/store/uiStore";
import { mockFetch } from "@/test/mocks/api";
import { settingsFixture } from "@/test/mocks/fixtures";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";

/** The chat the tab is opened on. */
export const NOTES_TAB_CHAT = 7;

/** How many notes the finished run in the fixture wrote. Its undo confirm
 *  names the number. */
export const NOTES_TAB_RUN_WROTE = 16;

/** How many notes the model saved without asking in the fixture. The saved
 *  notes' undo confirm names the number. */
export const NOTES_TAB_MODEL_SAVED = 2;

/** What the per-chat button says, per auto-accept answer. */
export const AUTO_ACCEPT = {
  /** The switch decided, and it keeps suggestions: "Hold this chat's ...". */
  kept: { enabled: true, effective: true, overridden: false },
  /** This chat's suggestions are held: "Keep this chat's ... without asking". */
  held: { enabled: true, effective: false, overridden: false },
  /** This chat decides: "Follow the setting above". */
  overridden: { enabled: true, effective: false, overridden: true },
} as const;

/** One note row as `GET /notebook/{chat}` sends it. */
export function notesTabNote(id: number, provenance: string, over: Record<string, unknown> = {}) {
  return {
    id, chat_id: NOTES_TAB_CHAT, position: id, kind: "fact", text: `note ${id}`,
    evidence: null, durability: "permanent", importance: 2, pinned: 0,
    retired_at: null, superseded_by: null, excluded_reason: null,
    status: "accepted", provenance, source_message_id: null,
    created_at: "2026-08-19", updated_at: "2026-08-19", ...over,
  };
}

export interface NotesTabOptions {
  /** The auto-accept answer; the per-chat button's wording follows it. */
  autoAccept?: (typeof AUTO_ACCEPT)[keyof typeof AUTO_ACCEPT];
  /** The plan route fails, so the strip's preview, once opened, says that
   *  it could not be worked out. */
  previewFails?: boolean;
  /** The tab's own error lines are on screen: the note model's list and the
   *  saved choice cannot be read, and the background reader has stopped. */
  errors?: boolean;
}

/** Why the background reader stopped, in the fixture with `errors`. */
export const NOTES_TAB_WORKER_DIED = "RuntimeError";

/** Every route the tab asks for, with a body that puts buttons on screen. */
export function notesTabRoutes(
  options: NotesTabOptions = {},
): Record<string, { status?: number; body: unknown }> {
  const chat = NOTES_TAB_CHAT;
  const limits = { useGlobal: true };
  const accept = { enabled: (options.autoAccept ?? AUTO_ACCEPT.kept).enabled as boolean };
  const limit = {
    id: 1, scope: "global", chat_id: null, label: "no gore",
    phrasing: "Avoid graphic injury.", severity: "hard", polarity: "avoid",
    on_violation: "pause", source: "explicit", rating_ceiling: null,
    exempt_from_trim: 1, last_confirmed_at: null, active: 1,
    created_at: "2026-08-19", updated_at: "2026-08-19",
  };
  return {
    [`GET /notebook/extract/plan/${chat}`]: options.previewFails
      ? { status: 500, body: { detail: "internal_error" } }
      : { body: {
        ready: true, blocked_by: [], scope: "all",
        messages_total: 412, messages_unread: 412, chunks: 21, calls_planned: 21,
        daily_cap: 60, backfill_cap: 50, live_reserve: 10, calls_spent_today: 3,
        fits_today: true, prompt_price: 0.06, cost_estimate: 0.014,
        model_id: "vendor/slug", model_is_chat_bind: false,
      } },
    "/notebook/extract/models": options.errors
      ? { status: 500, body: { detail: "internal_error" } }
      : { body: { models: [{
        id: "cheap/one", provider: "P", prompt_price: 0.06,
        context_length: 131000, endpoints: 3 }] } },
    "/notebook/extract/settings": options.errors
      ? { status: 500, body: { detail: "internal_error" } }
      : { body: { model_id: "cheap/one", prompt_language: "en" } },
    "/notebook/worker": { body: {
      stats: { done: 3, failed: 0, skipped: 0, skip_reasons: {} },
      spend: { calls: 3, tokens_in: 900, tokens_out: 40, cost: 0.0004 },
      spend_lifetime: { calls: 3, tokens_in: 900, tokens_out: 40, cost: 0.0004 },
      worker: {
        state: "open", failures: 5, total_failures: 5, queued: 0,
        dropped_offers: 0, runs: 3, batch_size: 20,
        ...(options.errors ? { alive: false, died: NOTES_TAB_WORKER_DIED } : {}),
        backlog: { chats: 1, messages: 40 },
        chat: { state: "open", failures: 5 },
        backfill: {
          job_id: 17, chat_id: chat, state: "done", scope: "all",
          calls_planned: 21, calls_spent: 21, notes_written: NOTES_TAB_RUN_WROTE,
          messages_left: 0, last_error: null,
        },
      },
      daily_cap: 60,
    } },
    "/notebook/safeword": { body: { word: "" } },
    // The global switch keeps what it was last told, as the server does.
    "POST /notebook/auto-accept": { get body() {
      accept.enabled = !accept.enabled;
      return { ok: true };
    } },
    "/notebook/auto-accept": { get body() {
      const base = options.autoAccept ?? AUTO_ACCEPT.kept;
      return accept.enabled ? base : { ...base, enabled: false, effective: false };
    } },
    // Longer than `/notebook/7`, so listed first: the mock matches by
    // substring in insertion order. The switch that follows the global
    // limits keeps what it was last told, as the server does: the mock reads
    // a route's body when it answers, so a press's answer and the next read
    // of the limits both see it.
    [`POST /notebook/${chat}/use-global`]: { get body() {
      limits.useGlobal = !limits.useGlobal;
      return { ok: true, use_global: limits.useGlobal };
    } },
    [`/notebook/${chat}/boundaries`]: { get body() {
      return { boundaries: [limit], use_global: limits.useGlobal };
    } },
    "/notebook/boundaries": { body: { boundaries: [], use_global: true } },
    [`/notebook/${chat}`]: { body: {
      entries: [
        // The model's saved notes are 5 and up, one per note it saved.
        ...Array.from({ length: NOTES_TAB_MODEL_SAVED }, (_, i) => notesTabNote(5 + i, "model")),
        notesTabNote(1, "user"), notesTabNote(2, "user"), notesTabNote(3, "user"),
        notesTabNote(8, "model", { status: "proposed" }),
      ],
      notebook_chars: 30 } },
    [`/chats/${chat}/messages`]: { body: [] },
    // Asked for by the generation-settings provider the shell wraps the
    // panel in. Last, because as a substring it would also answer
    // `/notebook/extract/settings` if it came first.
    "/settings": { body: settingsFixture },
  };
}

/** The tab's three views, in the order their tabs show them. */
export const NOTES_TAB_VIEWS: readonly NotesView[] = ["notes", "limits", "settings"];

/** Opens the tab. Returns the fetch mock with the render result. It opens
 *  on the Notes view, as a new session does. */
export function mountNotesTab(options: NotesTabOptions = {}) {
  useUiStore.setState({
    selectedChatId: NOTES_TAB_CHAT, activeRightPanelTab: "notebook", rightPanelCollapsed: false,
  });
  useNotesViewStore.setState({ view: "notes", focusTarget: null });
  const fetchMock = mockFetch(notesTabRoutes(options));
  const rendered = renderWithQueryClient(createElement(RightPanel), {
    wrapper: GenerationSettingsProvider,
  });
  return { fetchMock, ...rendered };
}

/** The buttons only one block of the tab renders each: once all of them
 *  are on screen, every block has drawn what the fixture gives it. */
export const NOTES_TAB_SETTLED_BY: readonly RegExp[] = [
  /^try again now$/i, /^try this chat again$/i, /^undo this run$/i, /^undo$/i,
  /^delete limit$/i, /^keep suggestion$/i,
];

/** Waits until the whole tab has drawn: the buttons above, and the worker
 *  card's per-chat button, which arrives with the last answer it waits on.
 *  The three views are all mounted (only one is shown), so a button in a
 *  view that is not open is waited for as well. */
export async function settleNotesTab(): Promise<void> {
  for (const name of NOTES_TAB_SETTLED_BY) {
    await screen.findByRole("button", { name, hidden: true });
  }
  await screen.findByTestId("chat-auto-accept-toggle");
}

/** The Notes tab's own panel (the outer one): every view is inside it. */
export function notesTabPanel(): HTMLElement {
  const panel = document.querySelector<HTMLElement>(
    '#es-right-panel [role="tabpanel"][data-slot="tabs-content"]');
  if (!panel) throw new Error("the Notes tab is not open");
  return panel;
}

/** Opens one of the tab's views by pressing its tab, and waits for it. */
export async function openNotesView(
  user: { click: (element: Element) => Promise<void> },
  view: NotesView,
): Promise<void> {
  // Inside the views' own list: the outer strip has a "Notes" tab too.
  const list = screen.getByRole("tablist", { name: NOTES_VIEW_WORDS.list });
  // Its word, then nothing, a count ("Notes 3 to review") or a reason.
  const name = new RegExp(`^${NOTES_VIEW_WORDS[view]}(,| |$)`, "i");
  await user.click(within(list).getByRole("tab", { name }));
  await within(list).findByRole("tab", { name, selected: true });
}

/** The four confirms the tab shows once each first press is made, by the
 *  names they carry: the run's undo, the saved notes' undo, a limit's
 *  delete and a note's delete (its pair has words: "Delete" and "Cancel"). */
export const NOTES_TAB_CONFIRMS = {
  run: new RegExp(`^${UNDO_RUN_CONFIRM}$`, "i"),
  saved: new RegExp(`^remove all ${NOTES_TAB_MODEL_SAVED}$`, "i"),
  limit: /^confirm delete limit$/i,
  note: new RegExp(`^${NOTE_ROW_WORDS.confirmDelete}$`, "i"),
} as const;

/** Makes every first press in the tab and waits for each confirm to
 *  arrive, so the buttons that only exist behind one are on screen. */
export async function armNotesTabConfirms(
  user: { click: (element: Element) => Promise<void> },
): Promise<void> {
  // The two confirms with words of their own are waited for by the words
  // they show, so a name that stops matching them is a finding of the row
  // that asks, not a confirm that never arrived.
  await user.click(screen.getByRole("button", { name: /^undo this run$/i }));
  await screen.findByText(NOTES_TAB_CONFIRMS.run, { selector: "button" });
  await user.click(screen.getByRole("button", { name: /^undo$/i }));
  await screen.findByText(NOTES_TAB_CONFIRMS.saved, { selector: "button" });
  // The limit is in the Limits view. Its question stays armed while the
  // view is hidden again (the views stay mounted), so every confirm is in
  // the tab at once afterwards.
  await openNotesView(user, "limits");
  await user.click(screen.getByRole("button", { name: /^delete limit$/i }));
  await screen.findByRole("button", { name: NOTES_TAB_CONFIRMS.limit });
  await openNotesView(user, "notes");
  await user.click(screen.getAllByRole("button", { name: /^delete note$/i })[0]);
  await screen.findByRole("button", { name: NOTES_TAB_CONFIRMS.note });
}

/** The store back to the tab the shell opens on, for an `afterEach`. */
export function closeNotesTab() {
  useUiStore.setState({
    selectedChatId: null, activeRightPanelTab: "models", rightPanelCollapsed: false,
  });
  useNotesViewStore.setState({ view: "notes", focusTarget: null });
}

/** Which block of the tab a node sits in, by the landmark or test id each
 *  block carries. */
export function notesTabBlockOf(node: Element): string {
  return node.closest('[data-testid="backfill-strip"]') ? "the strip"
    : node.closest('[data-testid="just-saved"]') ? "the saved notes"
    : node.closest('[role="tablist"]') ? "the view tabs"
      : node.closest("section[aria-label]")?.getAttribute("aria-label")
        ?? "outside every block";
}

/** The blocks of the tab, as `notesTabBlockOf` names them. */
export const NOTES_TAB_BLOCKS = [
  "the strip", "the saved notes", "the view tabs", "Notebook", "Limits",
  "Note suggestions", "Background notes",
] as const;
