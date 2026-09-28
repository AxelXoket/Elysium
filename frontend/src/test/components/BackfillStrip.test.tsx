/**
 * The strip at the top of the Notebook panel: reading a chat's earlier
 * messages on request, with the price said before the button acts.
 *
 * Every claim the worker card's "Read the earlier messages here" button
 * used to carry lives here now, against the strip's own routes: the
 * backlog offer (KARAR-J), the preview that spends nothing, the run, the
 * ten refusals rendered as sentences, stop, and undo. Nothing here reads
 * source text; every sentence is checked against the numbers in the body
 * the mock served, and every constant is imported.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { hiddenFromReaders } from "@/test/helpers/accessibility";
import { delayRoute } from "@/test/helpers/delayRoute";
import { useHeldDiscards } from "@/lib/chat/heldDiscards";
import {
  expectOptionsOnTheOptionStep,
  expectPlacedCollisionPadding,
  openPopup,
  openSelect,
} from "@/test/helpers/select";
import {
  glassRightStops,
  loadGlassRightCss,
  over,
  parseRgbColour,
} from "@/test/helpers/glassSurfaceCss";
import { panelMistGrounds } from "@/test/helpers/fogGround";
import { AA_NON_TEXT, contrastRatio } from "@/lib/appearance/contrast";
import {
  lengthPx,
  parseSheet,
  remPx,
  resolveVars,
  rootTokens,
  timeMs,
  TW_DURATION,
  utilityOf,
  winningDeclaration,
} from "@/test/helpers/compiledSheet";
import { durationSeconds, typeScale } from "@/lib/theme/tokens";
import { BackfillStrip } from "@/components/notebook/BackfillStrip";
import { NotebookPanel } from "@/components/notebook/NotebookPanel";
import { Select, SelectTrigger, SelectValue } from "@/components/ui/select";
import { SKIP_PROSE, SWEEP_PROSE, sweepProse } from "@/components/notebook/WorkerPanel";
import { getErrorMessage } from "@/lib/errors/errorMessages";
import type { BackfillStatus } from "@/lib/schemas/notebook";
import {
  BLOCKED_PROSE,
  PREVIEW_FAILED,
  RANGE_WORDS,
  SCOPE_LABELS,
  SCOPE_ORDER,
  blockedProse,
  rangeSummary,
  capProse,
  outcomeProse,
  planProse,
  stopProse,
  undoProse,
  UNDO_NOTHING_LEFT,
  UNDO_RUN_ASK,
  UNDO_RUN_CONFIRM,
} from "@/components/notebook/backfillProse";
import type { BackfillPlan } from "@/lib/schemas/notebook";
import { keys } from "@/lib/query/keys";
import { mockFetch } from "../mocks/api";
import { useUiStore } from "@/lib/store/uiStore";

const CHAT = 7;
/** A second conversation, for the questions that are about switching. */
const OTHER = 8;

type Route = { status?: number; body: unknown };

/** The worker status body, with the block the strip reads. */
function statusBody(over: Record<string, unknown> = {}) {
  return {
    stats: { done: 3, failed: 0, skipped: 0, skip_reasons: {} },
    spend: { calls: 3, tokens_in: 900, tokens_out: 40, cost: 0.0004 },
    spend_lifetime: { calls: 3, tokens_in: 900, tokens_out: 40, cost: 0.0004 },
    worker: {
      state: "closed", failures: 0, total_failures: 0,
      queued: 0, dropped_offers: 0, runs: 3, batch_size: 20,
      backlog: { chats: 0, messages: 0 },
      backfill: null,
      ...over,
    },
    daily_cap: 60,
  };
}

/** A preview the backend would send, in the shape `BackfillPlan` names. */
function planBody(over: Partial<BackfillPlan> = {}): BackfillPlan {
  return {
    ready: true, blocked_by: [], scope: "all",
    messages_total: 412, messages_unread: 412, chunks: 21, calls_planned: 21,
    // TWO CEILINGS, TWO NAMES. `daily_cap` is the day's, the same number
    // GET /notebook/worker reports; `backfill_cap` is what a job may
    // claim. Everything this strip counts towards is the second.
    daily_cap: 60, backfill_cap: 50,
    live_reserve: 10, calls_spent_today: 3, fits_today: true,
    prompt_price: 0.06, cost_estimate: 0.014,
    model_id: "vendor/slug", model_is_chat_bind: false,
    ...over,
  };
}

/** A job block as the worker route serves one.
 *
 *  Typed as the schema's own inferred type rather than as a loose record:
 *  `state` and `scope` are enums now, so a fixture that invents a state
 *  fails to compile instead of sailing through a `z.string()`. */
function job(over: Partial<BackfillStatus> = {}): BackfillStatus {
  return {
    job_id: 17, chat_id: CHAT, state: "done", scope: "all",
    calls_planned: 21, calls_spent: 21, notes_written: 14, messages_left: 0,
    last_error: null, ...over,
  };
}

/** A chat's message list as `GET /chats/{id}/messages` serves it.
 *
 *  The ids are deliberately NOT the positions: they start high and step by
 *  three, the way ids interleave across chats in one table. A strip that
 *  sent a typed position as an id, or showed an id as a position, reads
 *  the wrong number against this fixture instead of passing by accident. */
function chatMessages(chatId: number, count: number, firstId = 103) {
  return Array.from({ length: count }, (_, i) => ({
    id: firstId + i * 3,
    chat_id: chatId,
    role: i % 2 === 0 ? "user" : "assistant",
    content: `Message number ${i + 1} says something about the lighthouse keeper and the storm that night.`,
    created_at: "2026-09-27 10:00:00",
  }));
}

/** Method-prefixed keys FIRST: the mock takes the first match in insertion
 *  order, and a bare `/notebook/extract` substring would swallow them. */
function mount(
  worker: Record<string, unknown> = {},
  extra: Record<string, Route> = {},
  plan: Route = { body: planBody() },
) {
  // Held rather than built inline, so `poll` below can change what the
  // route serves. `mockFetch` stringifies the body at request time, so the
  // next request reads whatever this object says then.
  const served: { worker: Record<string, unknown> } = statusBody(worker);
  const fetchMock = mockFetch({
    ...extra,
    [`GET /notebook/extract/plan/${CHAT}`]: plan,
    [`GET /chats/${CHAT}/messages`]: { body: chatMessages(CHAT, 12) },
    [`GET /chats/${OTHER}/messages`]: { body: chatMessages(OTHER, 4, 900) },
    "/notebook/worker": { body: served },
  });
  const view = renderWithQueryClient(<BackfillStrip chatId={CHAT} />);
  /** Every request whose method and URL substring match, in order. */
  function calls(method: string, path: string): RequestInit[] {
    return fetchMock.mock.calls
      .filter((c: unknown[]) => {
        const init = c[1] as RequestInit | undefined;
        return ((init?.method ?? "GET") === method)
          && String(c[0]).includes(path);
      })
      .map((c: unknown[]) => (c[1] as RequestInit | undefined) ?? {});
  }
  /** The worker query settled, whichever way. Absence questions are
   *  asked only after this, never inside a first waitFor.
   *
   *  The key is BUILT from `keys.extractionWorker()`, the same function the
   *  hook spells it with, and never retyped: a key that is typed out here
   *  goes on matching nothing the day it is renamed, `settled()` waits on a
   *  state that stays undefined, and every absence assertion in this file
   *  quietly stops being guarded while staying green. */
  async function settled() {
    await waitFor(() => expect(
      view.queryClient.getQueryState([...keys.extractionWorker(), CHAT])?.status,
    ).not.toBe("pending"));
  }
  /** THE TWENTY-SECOND POLL MOVING UNDER THE READER.
   *
   *  The strip is rendered without a `key` and nothing about a second read
   *  of the same chat remounts it, so a new run arrives here exactly like
   *  this: the same route, the same query, a different `job_id` in the
   *  block. Served over the network boundary and read back through the one
   *  query the component itself polls - never `setQueryData` - so what the
   *  component sees is a response it fetched, not a value pushed past it.
   */
  async function poll(next: BackfillStatus | null) {
    served.worker.backfill = next;
    await view.queryClient.invalidateQueries({
      queryKey: [...keys.extractionWorker(), CHAT],
    });
  }
  return { ...view, fetchMock, calls, settled, poll };
}

/** Choose a scope through the house Select.
 *
 *  Base UI renders a BUTTON and a portalled listbox, not a native
 *  `<select>`, so the option is clicked rather than selected by value -
 *  and asserting through the role is what makes this a test of what a
 *  reader can actually do. */
async function pickScope(
  user: ReturnType<typeof userEvent.setup>, option: RegExp | string,
) {
  await user.click(screen.getByRole("combobox", { name: /what to read/i }));
  await user.click(await screen.findByRole("option", { name: option }));
}

/** What the scope field READS AS while it is shut.
 *
 *  The trigger's OWN text, minus what is hidden from the reader. The node
 *  comes from a role query at the call site, and the only other thing
 *  inside it is the chevron: Base UI's Select.Icon renders a span marked
 *  `aria-hidden` carrying a glyph, so dropping the hidden subtrees leaves
 *  exactly what a person - or a screen reader - takes off this field.
 *  That is the reader's view rather than an internal `data-slot` handle a
 *  rename of the house component would silently change. The empty string
 *  is asserted against, so a trigger that stopped rendering its value is
 *  a red test rather than a comparison of two blanks. */
function fieldText(trigger: HTMLElement): string {
  const shown = trigger.cloneNode(true) as HTMLElement;
  shown.querySelectorAll("[aria-hidden='true']").forEach((n) => n.remove());
  const text = shown.textContent?.trim() ?? "";
  expect(text, "the trigger rendered no value").not.toBe("");
  return text;
}

/* `delay: null` on the three sessions that TYPE into the range fields, for
 * speed: user-event's default puts a timer round trip after every
 * keystroke, and these are the only places in the file that send any. The
 * sessions that only click keep the default.
 *
 * A press that lands while a swap is still leaving goes nowhere, whatever
 * the session's delay: the leaving pane is inert, hidden from readers and
 * refuses the pointer. The exit-tick row in the undo block holds that. */

/** Reduced motion for one case, so every swap and collapse in it takes no
 *  time and nothing it waits on is an animation frame.
 *
 *  A SPY and not `stubGlobal`, because of what undoes each. The blocks that
 *  call this restore every mock in their afterEach, and `restoreAllMocks`
 *  undoes a spy but not a stubbed global: measured, a `matchMedia` stubbed
 *  in one case still answered reduced motion in the next, where the spy did
 *  not. The stub itself could be undone - `test/setup.ts`'s `matchMedia` is
 *  configurable and writable, and `unstubAllGlobals` puts the same function
 *  back - but nothing in this file calls that. */
function reducedMotion() {
  vi.spyOn(window, "matchMedia").mockImplementation((query: string) => ({
    matches: query.includes("prefers-reduced-motion"),
    media: query, onchange: null,
    addListener: () => {}, removeListener: () => {},
    addEventListener: () => {}, removeEventListener: () => {},
    dispatchEvent: () => false,
  } as MediaQueryList));
}

/** Whether a control is one a reader could actually press.
 *
 *  Computed rather than driven, the same way `AppShell.test.tsx` computes
 *  the tab order: jsdom does not implement `inert`, so React writes the
 *  attribute and jsdom goes on delivering clicks and focus into the
 *  subtree. A `user.click()` here would therefore measure jsdom's gap
 *  instead of the product. A browser takes an `[inert]` subtree out of hit
 *  testing, out of the tab order and out of the accessibility tree, and an
 *  `aria-hidden` one out of the last; subtracting both is what the browser
 *  does and what this returns. */
function pressable(el: HTMLElement): boolean {
  if (el.hasAttribute("disabled")) return false;
  if (el.closest("[inert]")) return false;
  return !hiddenFromReaders(el);
}

async function openPreview(user: ReturnType<typeof userEvent.setup>) {
  await user.click(
    await screen.findByRole("button", { name: /read the earlier messages/i }));
  return screen.findByTestId("backfill-preview");
}

describe("the backlog offer (KARAR-J)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: CHAT });
  });
  afterEach(() => vi.restoreAllMocks());

  it("says how much is unread and what reads it without being asked", async () => {
    // 3.31, decisions 30 a and 31 a. The line used to say nothing is read
    // without you asking; the background reader reads every chat by itself
    // at its threshold, and the line now says so, with the worker's own
    // number off the poll. 17 rather than 20 so a figure typed into the
    // component cannot pass for the one the worker sent.
    mount({ backlog: { chats: 3, messages: 512 }, trigger_threshold: 17 });
    const note = await screen.findByTestId("worker-backlog");
    await waitFor(() => expect(hiddenFromReaders(note)).toBe(false));
    const said = (note.textContent ?? "").replace(/\s+/g, " ");
    expect(said).toMatch(/3 chats have 512 unread messages/);
    expect(said).toMatch(/reads a chat by itself every 17 new messages/);
    // The sentence that makes it an offer rather than a warning.
    expect(said).toMatch(/everything older waits for you to ask here/i);
    expect(said).toMatch(/costs calls/i);
  });

  it("says one chat and one message in the singular", async () => {
    // "1 chat have 40 unread messages" was what the Notes tab's own fixture
    // state read, and one message read "1 unread messages". Each count
    // takes its own number, so the three mixed cases are each asked.
    for (const [chats, messages, said] of [
      [1, 1, "1 chat has 1 unread message."],
      [1, 40, "1 chat has 40 unread messages."],
      [2, 1, "2 chats have 1 unread message."],
    ] as const) {
      mount({ backlog: { chats, messages } });
      const note = await screen.findByTestId("worker-backlog");
      await waitFor(() => expect(hiddenFromReaders(note)).toBe(false));
      expect((note.textContent ?? "").replace(/\s+/g, " "), `${chats} and ${messages}`)
        .toMatch(new RegExp(`^${said.replace(/\./g, "\\.")} The background reader`));
      cleanup();
    }
  });

  it("no longer promises a whole-chat read nobody asked for (30 a, 31 a)", async () => {
    // The strip used to name mode 1 as the one exception to "nothing is
    // read without you asking": a chat with no notes had its whole history
    // read once on its own. That read is gone (decisions 30 a and 31 a;
    // the backend half is test_notebook_backfill.py::TestModeOneIsGone),
    // and a line promising it would now be describing a bill that never
    // comes. 3.31.
    mount({ backlog: { chats: 3, messages: 512 }, trigger_threshold: 20 });
    const note = await screen.findByTestId("worker-backlog");
    await waitFor(() => expect(hiddenFromReaders(note)).toBe(false));
    const said = (note.textContent ?? "").replace(/\s+/g, " ");
    // GROUND: the line is there and carries its counts, so the absences
    // below are about its words and not about an empty render.
    expect(said).toMatch(/3 chats have 512 unread messages/);
    // POSITIVE CONTROL for the same query: the new clause is found.
    expect(said).toMatch(/a new chat from its start only while it is short/);
    expect(said).not.toMatch(/read once on its own/i);
    expect(said).not.toMatch(/except a chat that has no notes/i);
    expect(said).not.toMatch(/the first time it comes up/i);
  });

  it("says the threshold without a number when the poll does not carry one", async () => {
    // The schema defaults a missing `trigger_threshold` to 0, and "every 0
    // new messages" is a sentence that is false in a new way.
    mount({ backlog: { chats: 1, messages: 5 } });
    const note = await screen.findByTestId("worker-backlog");
    await waitFor(() => expect(hiddenFromReaders(note)).toBe(false));
    const said = (note.textContent ?? "").replace(/\s+/g, " ");
    expect(said).toMatch(/reads a chat by itself as new messages arrive/);
    expect(said).not.toMatch(/every 0/);
  });

  it("GROUND: with nothing unread the line is not there for a reader", async () => {
    // Asserted after the worker query settled: a line hidden because the
    // answer has not arrived yet proves nothing.
    const { settled } = mount({ backlog: { chats: 0, messages: 0 } });
    await settled();
    expect(hiddenFromReaders(screen.queryByTestId("worker-backlog"))).toBe(true);
  });

  it("survives the worker query failing, without a word to the console", async () => {
    // NotebookPanel's own tests route nothing, so this strip meets a 404
    // on every one of them. The sentinel in src/test/sentinels.ts fails
    // this file on any console output; the button must still be offered.
    mockFetch({});
    renderWithQueryClient(<BackfillStrip chatId={CHAT} />);
    expect(await screen.findByRole("button", { name: /read the earlier messages/i }))
      .toBeEnabled();
  });
});

describe("the preview comes before the button acts", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: CHAT });
  });
  afterEach(() => vi.restoreAllMocks());

  it("keeps the keyboard's place: opening moves into the preview, Cancel hands it back", async () => {
    // The same fault as ledger Q-114. The open button unmounts when
    // the preview opens and Cancel makes the preview inert, so focus fell
    // to the body both times. Real key presses, and focus read AFTER each
    // one: a reading taken before the key cannot see where it went.
    const user = userEvent.setup();
    const { settled } = mount();
    await settled();
    const read = await screen.findByRole(
      "button", { name: /read the earlier messages/i });
    read.focus();
    // GROUND: the reader is on the open button before anything is pressed,
    // so "not on the body" below is the product moving focus, not a page
    // that never had it.
    expect(document.activeElement).toBe(read);

    await user.keyboard("{Enter}");
    const preview = await screen.findByTestId("backfill-preview");
    await waitFor(() => expect(hiddenFromReaders(preview)).toBe(false));
    const scope = within(preview).getByRole("combobox");
    await waitFor(() => expect(document.activeElement).toBe(scope));
    expect(document.activeElement).not.toBe(document.body);

    // To Cancel by Tab, the way a keyboard reader gets there.
    const cancel = within(preview).getByRole("button", { name: /^cancel$/i });
    for (let i = 0; i < 12 && document.activeElement !== cancel; i++) {
      await user.tab();
    }
    expect(document.activeElement).toBe(cancel);
    await user.keyboard("{Enter}");

    const back = await screen.findByRole(
      "button", { name: /read the earlier messages/i });
    await waitFor(() => expect(document.activeElement).toBe(back));
    expect(document.activeElement).not.toBe(document.body);
  });

  /** Open the preview and reach "Start reading" the keyboard's way. */
  async function tabToStart(user: ReturnType<typeof userEvent.setup>) {
    const read = await screen.findByRole(
      "button", { name: /read the earlier messages/i });
    read.focus();
    await user.keyboard("{Enter}");
    const preview = await screen.findByTestId("backfill-preview");
    const start = within(preview).getByRole("button", { name: /start reading/i });
    await waitFor(() => expect(start).toBeEnabled());
    for (let i = 0; i < 12 && document.activeElement !== start; i++) {
      await user.tab();
    }
    // GROUND: the reader is on the button before it is pressed.
    expect(document.activeElement).toBe(start);
    return { preview, start };
  }

  it.each([
    ["a run that starts", { started: true, job_id: 5 }, "Reading this chat now."],
    ["a run that is refused", { started: false, reason: "model_list_unavailable" },
      sweepProse("model_list_unavailable")],
  ])("keeps the keyboard's place when Start is pressed: %s", async (_label, answer, words) => {
    // Q-114 again. The button was `disabled` while the request was
    // out, and a focused button that turns disabled drops focus to the
    // body; a started run then closed the preview under it. Focus is read
    // after the key, while the request is out, and after the answer.
    const user = userEvent.setup();
    const { settled, fetchMock, calls } = mount();
    await settled();
    const release = delayRoute(fetchMock, "POST", `/notebook/extract/run/${CHAT}`,
      { body: answer });
    const { start } = await tabToStart(user);

    await user.keyboard("{Enter}");
    await waitFor(() =>
      expect(calls("POST", `/notebook/extract/run/${CHAT}`)).toHaveLength(1));
    await waitFor(() => expect(start).toHaveAttribute("aria-disabled", "true"));
    // While it is out: still focusable, still focused, and a second press
    // sends nothing.
    expect(start).not.toBeDisabled();
    expect(document.activeElement).toBe(start);
    await user.keyboard("{Enter}");
    expect(calls("POST", `/notebook/extract/run/${CHAT}`)).toHaveLength(1);

    release();
    const said = await screen.findByTestId("backfill-said");
    await waitFor(() => expect(said.textContent).toBe(words));
    await waitFor(() => expect(document.activeElement).toBe(said));
    expect(document.activeElement).not.toBe(document.body);
    expect(hiddenFromReaders(said)).toBe(false);
  });

  it("leaves focus where the reader took it while the run request was out", async () => {
    // POSITIVE CONTROL for the move above: it happens only when focus was
    // still on the button, so a reader who tabbed on is not pulled back.
    const user = userEvent.setup();
    const { settled, fetchMock, calls } = mount();
    await settled();
    const release = delayRoute(fetchMock, "POST", `/notebook/extract/run/${CHAT}`,
      { body: { started: false, reason: "model_list_unavailable" } });
    const { preview } = await tabToStart(user);
    await user.keyboard("{Enter}");
    await waitFor(() =>
      expect(calls("POST", `/notebook/extract/run/${CHAT}`)).toHaveLength(1));
    const cancel = within(preview).getByRole("button", { name: /^cancel$/i });
    await user.tab();
    expect(document.activeElement).toBe(cancel);

    release();
    const said = await screen.findByTestId("backfill-said");
    await waitFor(() =>
      expect(said.textContent).toBe(sweepProse("model_list_unavailable")));
    expect(document.activeElement).toBe(cancel);
  });

  it("gives focus back only on Cancel, not when another chat closes the preview", async () => {
    // The preview also closes when the reader opens another chat,
    // and there focus is somewhere else on purpose (the chat list): taking
    // it back to "Read the earlier messages" would steal it.
    const user = userEvent.setup();
    const { settled, rerender } = mount();
    await settled();
    const read = await screen.findByRole(
      "button", { name: /read the earlier messages/i });
    read.focus();
    await user.keyboard("{Enter}");
    const preview = await screen.findByTestId("backfill-preview");
    await waitFor(() => expect(hiddenFromReaders(preview)).toBe(false));

    // Where a reader switching chats has focus: outside the strip.
    const elsewhere = document.createElement("button");
    elsewhere.textContent = "a chat in the list";
    document.body.appendChild(elsewhere);
    try {
      elsewhere.focus();
      // GROUND: focus is outside before the chat changes.
      expect(document.activeElement).toBe(elsewhere);

      rerender(<BackfillStrip chatId={OTHER} />);
      // The preview closed and the open button is back, so a focus move
      // had somewhere to go; it did not go there.
      const back = await screen.findByRole(
        "button", { name: /read the earlier messages/i });
      await waitFor(() => expect(hiddenFromReaders(preview)).toBe(true));
      expect(back).toBeInTheDocument();
      expect(document.activeElement).toBe(elsewhere);
    } finally {
      elsewhere.remove();
    }
  });

  it.each([
    ["Cancel", "cancel"],
    ["the scope field", "scope"],
  ] as const)("a run that starts moves focus to the answer from anywhere in the preview: %s",
    async (_label, where) => {
      // A started run closes the preview and makes it inert,
      // so focus anywhere inside it fell to the body, not only focus on
      // Start. The reader moves with real keys while the request is out.
      const user = userEvent.setup();
      const { settled, fetchMock, calls } = mount();
      await settled();
      const release = delayRoute(fetchMock, "POST", `/notebook/extract/run/${CHAT}`,
        { body: { started: true, job_id: 5 } });
      const { preview } = await tabToStart(user);
      await user.keyboard("{Enter}");
      await waitFor(() =>
        expect(calls("POST", `/notebook/extract/run/${CHAT}`)).toHaveLength(1));
      const target = where === "cancel"
        ? within(preview).getByRole("button", { name: /^cancel$/i })
        : within(preview).getByRole("combobox", { name: /what to read/i });
      for (let i = 0; i < 12 && document.activeElement !== target; i++) {
        if (where === "cancel") await user.tab();
        else await user.tab({ shift: true });
      }
      // GROUND: focus is inside the preview, and not on Start, before the
      // answer lands.
      expect(document.activeElement).toBe(target);
      expect(preview.contains(document.activeElement)).toBe(true);

      release();
      const said = await screen.findByTestId("backfill-said");
      await waitFor(() => expect(said.textContent).toBe("Reading this chat now."));
      await waitFor(() => expect(hiddenFromReaders(preview)).toBe(true));
      await waitFor(() => expect(document.activeElement).toBe(said));
      expect(document.activeElement).not.toBe(document.body);
    });

  it("a run that starts leaves focus outside the panel where it is", async () => {
    // POSITIVE CONTROL for the case above: the move is for focus INSIDE the preview.
    // A reader who went outside while the request was out stays there.
    const user = userEvent.setup();
    const { settled, fetchMock, calls } = mount();
    await settled();
    const release = delayRoute(fetchMock, "POST", `/notebook/extract/run/${CHAT}`,
      { body: { started: true, job_id: 5 } });
    const { preview } = await tabToStart(user);
    await user.keyboard("{Enter}");
    await waitFor(() =>
      expect(calls("POST", `/notebook/extract/run/${CHAT}`)).toHaveLength(1));
    const elsewhere = document.createElement("button");
    elsewhere.textContent = "the composer";
    document.body.appendChild(elsewhere);
    try {
      elsewhere.focus();
      expect(document.activeElement).toBe(elsewhere);
      release();
      const said = await screen.findByTestId("backfill-said");
      await waitFor(() => expect(said.textContent).toBe("Reading this chat now."));
      await waitFor(() => expect(hiddenFromReaders(preview)).toBe(true));
      expect(document.activeElement).toBe(elsewhere);
    } finally {
      elsewhere.remove();
    }
  });

  it("another chat opening under a reader in the preview hands focus to the open button", async () => {
    // The preview closes on a chat switch and goes inert;
    // with focus in it, focus fell to the body. It goes to the button that
    // opens the preview, which is back in the same render. The chat-switch
    // case above (focus outside) is the positive control: that one is not moved.
    const user = userEvent.setup();
    const { settled, rerender } = mount();
    await settled();
    const read = await screen.findByRole(
      "button", { name: /read the earlier messages/i });
    read.focus();
    await user.keyboard("{Enter}");
    const preview = await screen.findByTestId("backfill-preview");
    await waitFor(() => expect(hiddenFromReaders(preview)).toBe(false));
    const scope = within(preview).getByRole("combobox", { name: /what to read/i });
    await waitFor(() => expect(document.activeElement).toBe(scope));
    // GROUND: the reader is inside the preview when the chat changes.
    expect(preview.contains(document.activeElement)).toBe(true);

    rerender(<BackfillStrip chatId={OTHER} />);
    await waitFor(() => expect(hiddenFromReaders(preview)).toBe(true));
    const back = await screen.findByRole(
      "button", { name: /read the earlier messages/i });
    await waitFor(() => expect(document.activeElement).toBe(back));
    expect(document.activeElement).not.toBe(document.body);
  });

  it("the answer line is named by its own sentence", async () => {
    // `role="status"` takes no name from its content, so the
    // focused answer line read as an unnamed status.
    const user = userEvent.setup();
    const { settled, fetchMock } = mount();
    await settled();
    const release = delayRoute(fetchMock, "POST", `/notebook/extract/run/${CHAT}`,
      { body: { started: false, reason: "model_list_unavailable" } });
    await tabToStart(user);
    await user.keyboard("{Enter}");
    release();
    const words = sweepProse("model_list_unavailable");
    const said = await screen.findByTestId("backfill-said");
    await waitFor(() => expect(said.textContent).toBe(words));
    // GROUND: the sentence is on screen; the query below asks for it as a
    // NAME, which a status only has when it is given one.
    expect(screen.getByRole("status", { name: words })).toBe(said);
  });

  it("opening the preview focuses the scope field without the browser's jump", async () => {
    // Under reduced motion the preview is full size at once,
    // and a plain focus() centred the field: a 145 px jump of the panel,
    // measured in Edge. The field opens where the pressed button was, so
    // focus asks for no scroll at all. The spy calls through.
    reducedMotion();
    const focusSpy = vi.spyOn(HTMLElement.prototype, "focus");
    const user = userEvent.setup();
    const { settled } = mount();
    await settled();
    const read = await screen.findByRole(
      "button", { name: /read the earlier messages/i });
    read.focus();
    await user.keyboard("{Enter}");
    const preview = await screen.findByTestId("backfill-preview");
    const scope = within(preview).getByRole("combobox", { name: /what to read/i });
    await waitFor(() => expect(document.activeElement).toBe(scope));
    // GROUND: the field was focused by a call, and those calls are the
    // ones read below.
    const onField = focusSpy.mock.contexts
      .map((ctx, i) => ({ ctx, args: focusSpy.mock.calls[i] }))
      .filter(({ ctx }) => ctx === scope);
    expect(onField.length).toBeGreaterThan(0);
    for (const { args } of onField) {
      expect(args[0]).toEqual(expect.objectContaining({ preventScroll: true }));
    }
  });

  it("the busy Start button's focus border keeps 3:1: the busy state dims its content, not it", async () => {
    // SC 1.4.11. `aria-disabled:opacity-50` on the button
    // faded its focus border with everything else, to 1.52-1.97:1, and
    // focus sits on this button for the whole request. Read off the
    // compiled sheet against the DOM that rendered: which opacity rules
    // match the button and its ancestors, and which match its children.
    const css = await loadGlassRightCss();
    const root = parseSheet(css);
    const table = rootTokens(root);
    const opacityOf = (el: Element): number => {
      const won = winningDeclaration(root, el, /^opacity$/);
      if (won === null) return 1;
      const v = resolveVars(won.value, table).trim();
      return v.endsWith("%") ? Number(v.slice(0, -1)) / 100 : Number(v);
    };
    const chain = (el: Element, stop: Element): number => {
      let product = 1;
      for (let e: Element | null = el; e && e !== stop.parentElement; e = e.parentElement) {
        product *= opacityOf(e);
      }
      return product;
    };

    const user = userEvent.setup();
    const { settled, fetchMock, calls } = mount();
    await settled();
    const release = delayRoute(fetchMock, "POST", `/notebook/extract/run/${CHAT}`,
      { body: { started: false, reason: "model_list_unavailable" } });
    const { start } = await tabToStart(user);
    const strip = screen.getByTestId("backfill-strip");
    const content = Array.from(start.children);
    // GROUND: the icon and the words are there to be dimmed, and at rest
    // nothing about the button is dimmed.
    expect(content.length).toBeGreaterThanOrEqual(2);
    expect(content.map((c) => opacityOf(c))).toEqual(content.map(() => 1));
    expect(chain(start, strip)).toBe(1);

    await user.keyboard("{Enter}");
    await waitFor(() =>
      expect(calls("POST", `/notebook/extract/run/${CHAT}`)).toHaveLength(1));
    await waitFor(() => expect(start).toHaveAttribute("aria-disabled", "true"));
    expect(document.activeElement).toBe(start);

    // POSITIVE CONTROL: busy still reads as busy, the content is dimmed.
    const dim = Math.max(...content.map((c) => opacityOf(c)));
    expect(dim).toBeLessThan(1);
    // The claim: the button and every box around it paint at full strength.
    const alpha = chain(start, strip);
    expect(alpha).toBe(1);

    // And what that buys, measured: the focus border the sheet gives this
    // button, at that strength, against every ground of the panel.
    const border = winningDeclaration(root, start, /^border-color$/);
    expect(border, "no focus border reaches the busy Start button").not.toBeNull();
    const ring = parseRgbColour(resolveVars(border!.value, table).trim());
    const grounds = [...glassRightStops(css), ...panelMistGrounds(root, "right")];
    for (const g of grounds) {
      expect(contrastRatio(over(ring, g, alpha), g), `on rgb(${g.r}, ${g.g}, ${g.b})`)
        .toBeGreaterThanOrEqual(AA_NON_TEXT);
    }
    // GROUND for the measure: the same border at the content's dim fails
    // the floor on at least one ground, so the reading above would have
    // caught the whole button being dimmed.
    expect(Math.min(...grounds.map((g) => contrastRatio(over(ring, g, dim), g))))
      .toBeLessThan(AA_NON_TEXT);
    release();
    await screen.findByTestId("backfill-said");
  }, 60_000);

  it("asks for nothing until the strip is opened, then shows calls and cost", async () => {
    const user = userEvent.setup();
    const { calls, settled } = mount();
    await settled();
    expect(calls("GET", "/notebook/extract/plan/")).toHaveLength(0);

    await openPreview(user);
    await waitFor(() =>
      expect(calls("GET", `/notebook/extract/plan/${CHAT}?scope=all`)).toHaveLength(1));
    const line = await screen.findByTestId("backfill-plan");
    await waitFor(() => expect(hiddenFromReaders(line)).toBe(false));
    // The numbers the mock served, through the same function the strip
    // renders with, so a sentence that dropped one goes red here.
    expect(line.textContent).toBe(planProse(planBody()));
    expect(line.textContent).toMatch(/412 unread messages/);
    expect(line.textContent).toMatch(/21 calls/);
    expect(line.textContent).toMatch(/\$0\.01400/);
    expect(line.textContent).toMatch(/3 of 50 calls/);
    expect(screen.getByRole("button", { name: /start reading/i })).toBeEnabled();
    // Nothing was spent by looking.
    expect(calls("POST", "/notebook/extract/run/")).toHaveLength(0);
  });

  it("an unknown price is said as unknown, never as a number", async () => {
    const user = userEvent.setup();
    mount({}, {}, { body: planBody({ prompt_price: null, cost_estimate: null }) });
    await openPreview(user);
    const line = await screen.findByTestId("backfill-plan");
    expect(line.textContent).toMatch(/price this app does not know/i);
    expect(line.textContent).not.toMatch(/\$0\.0+/);
    // POSITIVE CONTROL for the absence: the numeric case renders a dollar
    // figure through the same function.
    expect(planProse(planBody({ cost_estimate: 0.014 }))).toMatch(/\$0\.01400/);
  });

  it("says a known price as a ceiling, because its reply half is one", async () => {
    // The reply half of `cost_estimate` is every call's whole reply
    // allowance at the completion price: the most a reply can bill. So the
    // number is "up to", and "about" read it as a middle guess.
    const user = userEvent.setup();
    mount({}, {}, { body: planBody({ cost_estimate: 0.014 }) });
    await openPreview(user);
    const line = await screen.findByTestId("backfill-plan");
    // GROUND: the figure itself is on screen.
    expect(line.textContent).toMatch(/\$0\.01400/);
    expect(line.textContent).toMatch(/up to \$0\.01400/);
    expect(line.textContent).not.toMatch(/about \$/);
  });

  it("says the cap will be hit BEFORE any click, and still lets a partial read start", async () => {
    // E-0403: the preview says it, not the refusal after the press. Part
    // of the run fits today, so it may start and will stop at the limit.
    const user = userEvent.setup();
    const plan = planBody({ fits_today: false, calls_spent_today: 45, calls_planned: 21 });
    const { calls } = mount({}, {}, { body: plan });
    await openPreview(user);
    const cap = await screen.findByTestId("backfill-cap");
    await waitFor(() => expect(hiddenFromReaders(cap)).toBe(false));
    expect(cap.textContent).toBe(capProse(plan));
    expect(cap.textContent).toMatch(/only 5 of those 21 calls fit today/i);
    expect(calls("POST", "/notebook/extract/run/")).toHaveLength(0);
    expect(screen.getByRole("button", { name: /start reading/i })).toBeEnabled();
  });

  it("with today's calls already used, nothing can start", async () => {
    const user = userEvent.setup();
    const plan = planBody({ fits_today: false, calls_spent_today: 50, calls_planned: 21 });
    const { calls } = mount({}, {}, { body: plan });
    await openPreview(user);
    const cap = await screen.findByTestId("backfill-cap");
    expect(cap.textContent).toMatch(/cannot start until tomorrow/i);
    // WHY it is used up while the day's own counter still has room. The
    // reader meets "50 of 60 used" in the Notes tab and a refusal here,
    // and those two only stop contradicting each other once the reserve
    // is named. Both numbers come from the body the mock served.
    expect(cap.textContent).toBe(capProse(plan));
    expect(cap.textContent).toMatch(
      new RegExp(`last ${plan.live_reserve} of the day's ${plan.daily_cap}`, "i"));
    expect(cap.textContent).toMatch(/kept for your own conversations/i);
    const start = screen.getByRole("button", { name: /start reading/i });
    await waitFor(() => expect(start).toBeDisabled());
    // The button names the sentence that explains it, so a reader who
    // meets the control before the paragraph is still told why.
    expect(start.getAttribute("aria-describedby")).toBe(cap.id);
    await user.click(start);
    expect(calls("POST", "/notebook/extract/run/")).toHaveLength(0);
  });

  it("a job running anywhere else holds Start down, and the panel says so", async () => {
    // `worker.sweeping` is PROCESS-WIDE: the automatic reader, or a job
    // someone started on another chat, turns it on while this chat's own
    // preview is full and affordable. Without the sentence below the
    // reader gets a dead button and no cause anywhere on the panel.
    const user = userEvent.setup();
    const { calls } = mount({ sweeping: true });
    await openPreview(user);
    const line = await screen.findByTestId("backfill-plan");
    await waitFor(() => expect(hiddenFromReaders(line)).toBe(false));
    // The preview itself says yes: nothing here is the reason.
    expect(line.textContent).toBe(planProse(planBody()));
    const why = await screen.findByTestId("backfill-busy");
    await waitFor(() => expect(hiddenFromReaders(why)).toBe(false));
    expect(why.textContent).toBe(SWEEP_PROSE.already_running);
    const start = screen.getByRole("button", { name: /start reading/i });
    await waitFor(() => expect(start).toBeDisabled());
    expect(start.getAttribute("aria-describedby")).toBe(why.id);
    await user.click(start);
    expect(calls("POST", "/notebook/extract/run/")).toHaveLength(0);
  });

  it("GROUND: with nothing else reading, the same preview starts", async () => {
    // The positive control for the row above. Same body, same preview, one
    // flag flipped - so a gate that had simply been deleted would fail
    // there rather than pass both.
    const user = userEvent.setup();
    const { calls } = mount({ sweeping: false });
    await openPreview(user);
    await screen.findByTestId("backfill-plan");
    const start = screen.getByRole("button", { name: /start reading/i });
    await waitFor(() => expect(start).toBeEnabled());
    expect(hiddenFromReaders(screen.queryByTestId("backfill-busy"))).toBe(true);
    expect(start.getAttribute("aria-describedby")).toBeNull();
    expect(calls("POST", "/notebook/extract/run/")).toHaveLength(0);
  });

  it("GROUND for the cap line: a run that fits says nothing about the cap", async () => {
    const user = userEvent.setup();
    mount({}, {}, { body: planBody({ fits_today: true }) });
    await openPreview(user);
    await screen.findByTestId("backfill-plan");
    expect(hiddenFromReaders(screen.queryByTestId("backfill-cap"))).toBe(true);
    expect(capProse(planBody({ fits_today: true }))).toBe("");
  });

  it("GROUND for the reserve clause: with no reserve set, nothing is claimed about one", () => {
    // `ELYSIUM_NOTEBOOK_LIVE_RESERVE=0` is a supported setting, and the
    // sentence above must not then invent a reserve of zero calls. A
    // positive control sits beside it so this is about the zero arm and
    // not about the clause having quietly disappeared for everyone.
    const none = planBody({ fits_today: false, calls_spent_today: 50, live_reserve: 0 });
    expect(capProse(none)).toMatch(/cannot start until tomorrow/i);
    expect(capProse(none)).not.toMatch(/kept for your own conversations/i);
    expect(capProse({ ...none, live_reserve: 10 }))
      .toMatch(/kept for your own conversations/i);
  });

  it("a blocked preview disables the run and says what is missing", async () => {
    const user = userEvent.setup();
    const { calls } = mount({}, {}, {
      body: planBody({ ready: false, blocked_by: ["no_extract_model"] }),
    });
    await openPreview(user);
    const why = await screen.findByTestId("backfill-blocked");
    await waitFor(() => expect(hiddenFromReaders(why)).toBe(false));
    // The sentence through the same function the strip renders with, not a
    // regex copy of it: a retyped one goes on passing after the wording
    // changes on one side only.
    expect(why.textContent).toBe(blockedProse(["no_extract_model"]));
    expect(why.textContent).not.toMatch(/no_extract_model/);
    const start = screen.getByRole("button", { name: /start reading/i });
    await waitFor(() => expect(start).toBeDisabled());
    // The disabled button carries the sentence that explains it.
    expect(start.getAttribute("aria-describedby")).toBe(why.id);
    await user.click(start);
    expect(calls("POST", "/notebook/extract/run/")).toHaveLength(0);
  });

  it.each(Object.keys(BLOCKED_PROSE))(
    "the blocking reason %s reaches the screen as words, never as its token",
    async (token) => {
      // SIX tokens, one per member of the backend's `_BLOCKER_REFUSALS`,
      // walked the way the ten refusals are. One covered token proved
      // the mapping existed for that token and nothing about the others:
      // deleting any of their sentences left the suite green while the
      // strip printed "it is blocked (no_character)" at a reader.
      const user = userEvent.setup();
      mount({}, {}, { body: planBody({ ready: false, blocked_by: [token] }) });
      await openPreview(user);
      const why = await screen.findByTestId("backfill-blocked");
      await waitFor(() => expect(hiddenFromReaders(why)).toBe(false));
      expect(why.textContent).toBe(blockedProse([token]));
      // The token itself never reaches the screen, and neither does the
      // fallback's shape - which is what a deleted sentence renders as.
      expect(why.textContent).not.toMatch(new RegExp(token));
      expect(why.textContent).not.toMatch(/it is blocked/i);
      cleanup();
    });

  it("the blocking vocabulary is the backend's six, not a table that drifted", () => {
    // An equality rather than a floor, the same shape the ten refusals
    // are pinned with: a seventh token invented on either side is a red
    // test rather than a sentence nobody wrote.
    expect(Object.keys(BLOCKED_PROSE).sort()).toEqual([
      "model_list_unavailable", "model_not_extractable", "no_character",
      "no_chat_model", "no_extract_model", "nothing_unread",
    ]);
  });

  it("the preview's words and a refusal's words are different sentences on purpose", () => {
    // Section 5.0 forbids one state wearing two names. These two tables
    // share three tokens and still differ, and the reason is the moment
    // each is read at: `BLOCKED_PROSE` holds FRAGMENTS of a state, joined
    // into "It cannot start: a; b." before anything is pressed;
    // `SWEEP_PROSE` holds whole past-tense sentences about a press that
    // was refused. Swapping them produces "It cannot start: Nothing here
    // is unread - it has all been looked at.".
    for (const shared of ["nothing_unread", "model_not_extractable",
                          "model_list_unavailable"]) {
      expect(BLOCKED_PROSE[shared]).not.toBe(SWEEP_PROSE[shared]);
      // A fragment, so: no capital at the front and no full stop at the
      // end, which is what makes it joinable.
      expect(BLOCKED_PROSE[shared]).toMatch(/^[a-z]/);
      expect(BLOCKED_PROSE[shared]).not.toMatch(/\.$/);
      expect(SWEEP_PROSE[shared]).toMatch(/^[A-Z]/);
    }
    // And the preview's version of the hardest one keeps BOTH halves the
    // refusal gives: why it cannot, and what to do instead.
    expect(BLOCKED_PROSE.model_not_extractable).toMatch(/keep no copy/i);
    expect(BLOCKED_PROSE.model_not_extractable).toMatch(/pick a note model/i);
  });

  it("a list that could not be fetched is never said as the model's fault", () => {
    // With the provider's list out of reach, a bound model is refused
    // because nothing vetted it, not because it was found unfit. GROUND:
    // the model's own refusal does say the model cannot answer, so the
    // absence below is about these two sentences and not a pattern that
    // matches nothing.
    const blames = /cannot answer|cannot write/i;
    expect(BLOCKED_PROSE.model_not_extractable).toMatch(blames);
    expect(SWEEP_PROSE.model_not_extractable).toMatch(blames);
    for (const said of [BLOCKED_PROSE.model_list_unavailable,
                        SWEEP_PROSE.model_list_unavailable]) {
      expect(said).toMatch(/could not be fetched/i);
      expect(said).not.toMatch(blames);
    }
  });

  it("a list that could not be fetched points at no list and promises no read", () => {
    // The picker is fed by the same provider list, so "pick a note
    // model from the list" was advice nobody could follow at that moment,
    // and "those stretches are read once the list can be fetched" promised
    // a read the list may refuse (a bound model it does not carry is then
    // `model_not_extractable`). GROUND: the model's own refusal does point
    // at the picker, so the absence below is these sentences and not a
    // pattern that matches nothing.
    const pick = /pick a note model/i;
    expect(BLOCKED_PROSE.model_not_extractable).toMatch(pick);
    expect(SWEEP_PROSE.model_not_extractable).toMatch(pick);
    const promise = /\b(are|is|will be) read\b/i;
    // POSITIVE CONTROL for the second pattern: the retired wording.
    expect("those stretches are read once the list can be fetched")
      .toMatch(promise);
    for (const said of [BLOCKED_PROSE.model_list_unavailable,
                        SWEEP_PROSE.model_list_unavailable,
                        SKIP_PROSE.model_list_unavailable]) {
      expect(said).toMatch(/could not be fetched/i);
      expect(said).toMatch(/checked/i);
      expect(said).not.toMatch(pick);
      expect(said).not.toMatch(promise);
    }
  });

  it("a blocking reason nobody wrote words for is never printed raw", async () => {
    // The other half of the guard `outcomeProse` puts on `last_error`, and
    // it was missing here: `blocked_by` is built by the backend from a list
    // the client cannot check ahead of time, so the day one of these
    // carries a path or a quoted sentence it went straight to the screen.
    const user = userEvent.setup();
    const LEAKY = "could not read D:/archive/rowan-chats.db (locked)";
    mount({}, {}, { body: planBody({ ready: false, blocked_by: [LEAKY] }) });
    await openPreview(user);
    const why = await screen.findByTestId("backfill-blocked");
    await waitFor(() => expect(hiddenFromReaders(why)).toBe(false));
    expect(why.textContent).toBe(blockedProse([LEAKY]));
    expect(why.textContent).not.toContain(LEAKY);
    expect(why.textContent).not.toMatch(/chats\.db/);
    // POSITIVE CONTROL: the guard is a filter, not a blanket. A token that
    // looks like a token still rides along for a bug report, so the
    // absence above is about the guard rather than about the fallback
    // having no token in it at all.
    expect(blockedProse(["some_future_reason"])).toContain("some_future_reason");
  });

  it("a preview that cannot be loaded is not a free run", async () => {
    const user = userEvent.setup();
    const { calls } = mount({}, {}, { status: 502, body: { detail: "internal_error" } });
    await openPreview(user);
    const alert = await screen.findByRole("alert");
    // The server's own sentence, then what the strip knows whatever
    // failed. Both halves, because a reader needs the second one even
    // when the first says nothing they can act on.
    expect(alert.textContent).toContain(getErrorMessage("internal_error"));
    expect(alert.textContent).toMatch(/nothing was started/i);
    const start = screen.getByRole("button", { name: /start reading/i });
    expect(start).toBeDisabled();
    await user.click(start);
    expect(calls("POST", "/notebook/extract/run/")).toHaveLength(0);
  });

  it("a failure the catalogue has no sentence for does not read like the notes failing", async () => {
    // The strip is mounted INSIDE the Notebook panel, whose own failed load
    // says "Notes could not be loaded". While this paragraph said "The
    // preview could not be loaded" the panel could show two sentences that
    // ended in the same five words about two different things - and since
    // Collapse keeps its content mounted, the second one is in the tree
    // even while it is shut.
    const user = userEvent.setup();
    mount({}, {}, { status: 500, body: { detail: "some_code_nobody_wrote" } });
    await openPreview(user);
    const alert = await screen.findByRole("alert");
    await waitFor(() => expect(alert.textContent).toContain(PREVIEW_FAILED));
    expect(alert.textContent).not.toMatch(/could not be loaded/i);
    expect(alert.textContent).toMatch(/nothing was started/i);
    // POSITIVE CONTROL for the absence: the catalogue path is still taken
    // when there IS a code, so this line is the generic one rather than
    // the paragraph having stopped rendering a first half at all.
    expect(getErrorMessage("notebook_range_invalid")).toBeTruthy();
    expect(alert.textContent)
      .not.toContain(getErrorMessage("notebook_range_invalid"));
  });

  it("a range that is not in this chat says which range, not which server",
     async () => {
    // `notebook_range_invalid` is the one preview failure a reader can fix,
    // and it enters the catalogue in the same change as this strip. If the
    // strip swallowed the code, that sentence could never reach a screen.
    const user = userEvent.setup({ delay: null });
    const { calls } = mount({}, {},
      { status: 400, body: { detail: "notebook_range_invalid" } });
    await openPreview(user);
    await pickScope(user, /a range of messages/i);
    // Two places that ARE in this chat, in order, so the refusal below is
    // the server's (a message deleted from under the strip, say) and not
    // the strip's own check, which never asks the server at all.
    await user.type(await screen.findByLabelText(/first message/i), "2");
    await user.clear(screen.getByLabelText(/last message/i));
    await user.type(screen.getByLabelText(/last message/i), "6");
    const alert = await screen.findByRole("alert");
    await waitFor(() => expect(alert.textContent)
      .toContain(getErrorMessage("notebook_range_invalid")));
    // GROUND: the catalogue sentence is a real, distinct sentence and not
    // the fallback every unknown code would also produce.
    expect(getErrorMessage("notebook_range_invalid"))
      .not.toBe(getErrorMessage("no_such_code_exists"));
    expect(alert.textContent).not.toContain(PREVIEW_FAILED);
    expect(screen.getByRole("button", { name: /start reading/i }))
      .toBeDisabled();
    expect(calls("POST", "/notebook/extract/run/")).toHaveLength(0);
  });
});

describe("the run", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: CHAT });
  });
  afterEach(() => vi.restoreAllMocks());

  it("sends exactly one POST with the scope, then refreshes the worker", async () => {
    const user = userEvent.setup();
    const { calls } = mount({}, {
      [`POST /notebook/extract/run/${CHAT}`]:
        { body: { started: true, job_id: 17, calls_planned: 21 } },
    });
    await openPreview(user);
    const start = screen.getByRole("button", { name: /start reading/i });
    await waitFor(() => expect(start).toBeEnabled());
    const workerFetches = calls("GET", "/notebook/worker").length;
    await user.click(start);

    await waitFor(() =>
      expect(calls("POST", `/notebook/extract/run/${CHAT}`)).toHaveLength(1));
    const [init] = calls("POST", `/notebook/extract/run/${CHAT}`);
    expect(JSON.parse(String(init.body))).toEqual({ scope: "all" });
    expect((await screen.findByTestId("backfill-said")).textContent)
      .toMatch(/reading this chat now/i);
    // The worker query is invalidated by the mutation, so the job's
    // progress arrives without waiting for the poll.
    await waitFor(() =>
      expect(calls("GET", "/notebook/worker").length).toBeGreaterThan(workerFetches));
  });

  it("the closed field reads the sentence for the scope, never the wire token", async () => {
    // What the ONE control this strip ships says about itself when it is
    // shut, which is how a reader meets it for all but a second at a time.
    // Base UI resolves `Select.Value` through the ROOT's `items` and not
    // through the mounted options, so with none it serialised the value
    // and the field read `all`, then `next`: the API enum, on the face of
    // the control and in the combobox's announced value, in every state
    // including its default, while the three written sentences existed
    // one click away and were never shown shut.
    //
    // Asserted as EQUALITY against the same record the options are built
    // from, and per scope: a substring check would pass on a field that
    // printed the sentence and the token both, and checking only the
    // default would miss the state a reader actually chose.
    const user = userEvent.setup();
    mount();
    await openPreview(user);
    const trigger = await screen.findByRole("combobox", { name: /what to read/i });

    // GROUND: before anything is chosen the field is already a sentence,
    // so the rows below measure the CHOICE and not the first render.
    expect(fieldText(trigger)).toBe(SCOPE_LABELS.all);

    for (const scope of SCOPE_ORDER) {
      await pickScope(user, new RegExp(SCOPE_LABELS[scope], "i"));
      await waitFor(() => expect(screen.queryByRole("listbox")).toBeNull());

      expect(
        fieldText(trigger),
        `the closed field does not read the sentence for "${scope}"`,
      ).toBe(SCOPE_LABELS[scope]);
      // POSITIVE CONTROL for the defect itself: the wire token is what
      // the field used to carry, and it is never what it carries now.
      expect(fieldText(trigger)).not.toBe(scope);
    }
  });

  it("the open list and the closed field say the same string", async () => {
    // GROUND for the row above, and the one thing a second table would
    // break: the option's own text is read off the DOM rather than off the
    // record, so a field mapped from a table that had drifted from the
    // options would fail here while still reading as a sentence.
    const user = userEvent.setup();
    mount();
    await openPreview(user);
    const trigger = await screen.findByRole("combobox", { name: /what to read/i });

    await user.click(trigger);
    const optionText = new Map<string, string>();
    for (const scope of SCOPE_ORDER) {
      const option = await screen.findByRole(
        "option", { name: new RegExp(SCOPE_LABELS[scope], "i") });
      optionText.set(scope, option.textContent?.trim() ?? "");
    }
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("listbox")).toBeNull());

    for (const scope of SCOPE_ORDER) {
      await pickScope(user, new RegExp(SCOPE_LABELS[scope], "i"));
      await waitFor(() => expect(screen.queryByRole("listbox")).toBeNull());
      expect(fieldText(trigger)).toBe(optionText.get(scope));
    }
  });

  it("the scope picker opens, chooses and closes from the keyboard alone", async () => {
    // This strip is the repo's FIRST production call site of the house
    // `Select`, and every other row here opens it with a click. Base UI
    // supplies the keyboard behaviour, which is exactly why nothing would
    // notice the day a className or a prop took it away: a picker that
    // only a mouse can reach is a control half the readers of this panel
    // cannot use at all.
    const user = userEvent.setup();
    const { calls } = mount({}, {
      [`POST /notebook/extract/run/${CHAT}`]: { body: { started: true, job_id: 5 } },
    });
    await openPreview(user);
    const trigger = await screen.findByRole("combobox", { name: /what to read/i });

    trigger.focus();
    expect(trigger).toHaveFocus();
    expect(screen.queryByRole("listbox")).toBeNull();

    await user.keyboard("{Enter}");
    const list = await screen.findByRole("listbox");
    expect(list).toBeInTheDocument();
    // The trigger says it is open, which is the state a reader is told.
    await waitFor(() => expect(trigger).toHaveAttribute("aria-expanded", "true"));

    // Down to the second option and choose it. The value is read off the
    // request the run then sends, so this asserts the CHOICE landed rather
    // than that a key was pressed.
    await user.keyboard("{ArrowDown}{Enter}");
    await waitFor(() => expect(screen.queryByRole("listbox")).toBeNull());
    await waitFor(() => expect(trigger).toHaveAttribute("aria-expanded", "false"));
    expect(trigger).toHaveFocus();

    await user.click(await screen.findByRole("button", { name: /start reading/i }));
    await waitFor(() => expect(calls("POST", "/notebook/extract/run/")).toHaveLength(1));
    expect(JSON.parse(String(calls("POST", "/notebook/extract/run/")[0].body)).scope)
      .toBe("next");
  });

  it("Escape closes the picker without choosing", async () => {
    // GROUND for the row above: the keys are doing the choosing, not the
    // popup opening and something else committing a value.
    const user = userEvent.setup();
    const { calls } = mount({}, {
      [`POST /notebook/extract/run/${CHAT}`]: { body: { started: true, job_id: 5 } },
    });
    await openPreview(user);
    const trigger = await screen.findByRole("combobox", { name: /what to read/i });
    trigger.focus();
    await user.keyboard("{Enter}");
    await screen.findByRole("listbox");

    await user.keyboard("{ArrowDown}{Escape}");

    await waitFor(() => expect(screen.queryByRole("listbox")).toBeNull());
    await user.click(await screen.findByRole("button", { name: /start reading/i }));
    await waitFor(() => expect(calls("POST", "/notebook/extract/run/")).toHaveLength(1));
    expect(JSON.parse(String(calls("POST", "/notebook/extract/run/")[0].body)).scope)
      .toBe("all");
  });

  it("a range typed as places in the chat is sent as those messages' ids", async () => {
    // Places 2 and 6 of this chat are ids 106 and 118 in the fixture. The
    // reader types what the chat shows; the server gets what it keys on.
    const user = userEvent.setup({ delay: null });
    const { calls } = mount({}, {
      [`POST /notebook/extract/run/${CHAT}`]:
        { body: { started: true, job_id: 18, calls_planned: 1 } },
    }, { body: planBody({ scope: "range", messages_unread: 5, calls_planned: 1 }) });
    await openPreview(user);
    await pickScope(user, /a range of messages/i);
    await user.type(await screen.findByLabelText(/first message/i), "2");
    await user.clear(screen.getByLabelText(/last message/i));
    await user.type(screen.getByLabelText(/last message/i), "6");
    await waitFor(() => expect(
      calls("GET", `/notebook/extract/plan/${CHAT}?scope=range&from_message_id=106&to_message_id=118`),
    ).toHaveLength(1));
    // GROUND for the mapping: the positions themselves never went out.
    expect(calls("GET", "from_message_id=2&")).toHaveLength(0);
    expect(screen.getByTestId("backfill-range-said").textContent)
      .toBe(rangeSummary(2, 6, 12, false));
    const start = screen.getByRole("button", { name: /start reading/i });
    await waitFor(() => expect(start).toBeEnabled());
    await user.click(start);
    await waitFor(() =>
      expect(calls("POST", `/notebook/extract/run/${CHAT}`)).toHaveLength(1));
    expect(JSON.parse(String(calls("POST", `/notebook/extract/run/${CHAT}`)[0].body)))
      .toEqual({ scope: "range", from_message_id: 106, to_message_id: 118 });
  });

  it("every refusal the backend can answer reaches the screen as words", async () => {
    // The moved walk. Ten keys after the backfill routes; each one mounts
    // the strip, presses Start, and reads what a person would read.
    // EXACTLY ten, not at least ten: `>=` lets a sentence with no
    // backend reason through, which is the direction that puts words on a
    // screen for a thing that can never happen.
    expect(Object.keys(SWEEP_PROSE)).toHaveLength(10);
    for (const reason of Object.keys(SWEEP_PROSE)) {
      const user = userEvent.setup();
      mount({}, {
        [`POST /notebook/extract/run/${CHAT}`]: { body: { started: false, reason } },
      });
      await openPreview(user);
      const start = screen.getByRole("button", { name: /start reading/i });
      await waitFor(() => expect(start).toBeEnabled());
      await user.click(start);
      const said = (await screen.findByTestId("backfill-said")).textContent ?? "";
      expect(SWEEP_PROSE[reason], reason).toBeTruthy();
      expect(said, reason).toContain(SWEEP_PROSE[reason]);
      expect(said, reason).not.toMatch(new RegExp(reason));
      cleanup();
      vi.restoreAllMocks();
    }
  });

  it("a refusal nobody wrote words for is named, not printed raw", async () => {
    const user = userEvent.setup();
    mount({}, {
      [`POST /notebook/extract/run/${CHAT}`]:
        { body: { started: false, reason: "some_future_reason" } },
    });
    await openPreview(user);
    const start = screen.getByRole("button", { name: /start reading/i });
    await waitFor(() => expect(start).toBeEnabled());
    await user.click(start);
    const said = (await screen.findByTestId("backfill-said")).textContent;
    expect(said).toMatch(/did not start/i);
    expect(said).not.toMatch(/already reading/i);
  });
});

describe("a job in flight", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: CHAT });
  });
  afterEach(() => vi.restoreAllMocks());

  it("shows the progress numbers and offers Stop, not Start", async () => {
    const user = userEvent.setup();
    const { calls } = mount({
      backfill: job({ state: "running", calls_spent: 8, notes_written: 14, messages_left: 240 }),
    }, {
      [`POST /notebook/extract/stop/${CHAT}`]:
        { body: { stopped: true, job_id: 17, calls_spent: 8 } },
    });
    const progress = await screen.findByTestId("backfill-progress");
    await waitFor(() => expect(hiddenFromReaders(progress)).toBe(false));
    expect(progress.textContent).toMatch(/8 of 21 calls/);
    expect(progress.textContent).toMatch(/14 notes written/);
    expect(progress.textContent).toMatch(/240 messages left/);
    expect(screen.queryByRole("button", { name: /read the earlier messages/i })).toBeNull();

    await user.click(screen.getByRole("button", { name: /stop reading/i }));
    await waitFor(() =>
      expect(calls("POST", `/notebook/extract/stop/${CHAT}`)).toHaveLength(1));
    expect((await screen.findByTestId("backfill-said")).textContent)
      .toMatch(/stopping after the call/i);
  });

  it("stop with nothing running is a sentence, not an error", async () => {
    const user = userEvent.setup();
    mount({ backfill: job({ state: "running" }) }, {
      [`POST /notebook/extract/stop/${CHAT}`]:
        { body: { stopped: false, reason: "nothing_running" } },
    });
    await user.click(await screen.findByRole("button", { name: /stop reading/i }));
    const said = (await screen.findByTestId("backfill-said")).textContent;
    expect(said).toContain(SWEEP_PROSE.nothing_running);
    expect(said).not.toMatch(/nothing_running/);
  });

  it("a stop nobody wrote words for does not answer with \"it did not start\"", async () => {
    // The stop route used to borrow the RUN route's fallback, so pressing
    // "Stop reading" and getting a reason this client has never heard of
    // answered "It did not start (token)" - the opposite of what the press
    // asked for, with the token on screen as well.
    const user = userEvent.setup();
    mount({ backfill: job({ state: "running" }) }, {
      [`POST /notebook/extract/stop/${CHAT}`]:
        { body: { stopped: false, reason: "some_future_reason" } },
    });
    await user.click(await screen.findByRole("button", { name: /stop reading/i }));
    const said = (await screen.findByTestId("backfill-said")).textContent;
    expect(said).toBe(stopProse("some_future_reason"));
    expect(said).not.toMatch(/did not start/i);
    expect(said).not.toMatch(/some_future_reason/);
    // POSITIVE CONTROL: the run route's fallback still says "did not
    // start", so the absence above is this route having its own sentence
    // rather than that sentence having been deleted everywhere.
    expect(sweepProse("some_future_reason")).toMatch(/did not start/i);
  });

  it("the answer to a press is a live region, and the ticking counter is not", async () => {
    // The paragraph this strip replaced on the worker card carried
    // `role="status"`; without it every refusal the strip answers with is
    // announced to nobody. The progress node is the other half: its whole
    // sentence is rewritten on every poll, so a live region there reads
    // the counter out three times a minute for the length of the job.
    const user = userEvent.setup();
    mount({ backfill: job({ state: "running" }) }, {
      [`POST /notebook/extract/stop/${CHAT}`]:
        { body: { stopped: false, reason: "nothing_running" } },
    });
    const progress = await screen.findByTestId("backfill-progress");
    expect(progress).not.toHaveAttribute("aria-live");
    await user.click(await screen.findByRole("button", { name: /stop reading/i }));
    // By ROLE, not by testid. This node declares `role="status"` and the
    // role query is the reader's own path to it; a testid where a role
    // exists asks a question no reader can ask. The testid stays on the
    // node for the rows that need the SAME paragraph whether or not it is
    // reachable (a closed Collapse is out of every role query).
    const said = await screen.findByRole("status");
    expect(said).toHaveAttribute("data-testid", "backfill-said");
    // GROUND: one live region for the panel, not two.
    expect(screen.getAllByRole("status")).toHaveLength(1);
  });

  it("a job on another chat is not this chat's job", async () => {
    const { settled } = mount({ backfill: job({ chat_id: 9, state: "running" }) });
    await settled();
    expect(hiddenFromReaders(screen.queryByTestId("backfill-progress"))).toBe(true);
    expect(await screen.findByRole("button", { name: /read the earlier messages/i }))
      .toBeEnabled();
  });
});

describe("a finished job and its undo", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: CHAT });
  });
  afterEach(() => vi.restoreAllMocks());

  it("says how it ended, from its own numbers", async () => {
    const done = job({ state: "done", calls_spent: 21, notes_written: 14 });
    mount({ backfill: done });
    const out = await screen.findByTestId("backfill-outcome");
    await waitFor(() => expect(hiddenFromReaders(out)).toBe(false));
    expect(within(out).getByText(outcomeProse(done))).toBeInTheDocument();
    expect(out.textContent).toMatch(/finished reading: 21 calls, 14 notes written/i);
  });

  it("a job stopped at the cap says so and what waits", async () => {
    const stopped = job({ state: "stopped", last_error: "daily_cap_reached",
                          calls_spent: 2, notes_written: 3, messages_left: 300 });
    mount({ backfill: stopped });
    const out = await screen.findByTestId("backfill-outcome");
    expect(within(out).getByText(outcomeProse(stopped))).toBeInTheDocument();
    expect(out.textContent).toMatch(/stopped at today's limit/i);
    expect(out.textContent).toMatch(/300 messages are still unread/);
  });

  it("a failure whose reason is a sentence keeps that sentence off the screen", async () => {
    // `state === "failed"` is the ONE arm that echoes `last_error`, and it
    // had no row here at all. `last_error` is written by the worker from an
    // exception, so the day one of those carries an interpolated message
    // the whole thing lands on screen: a path, a quoted line, a name.
    const LEAKY = "Timeout reading Rowan's letter at D:/archive/notes.txt";
    const failed = job({
      state: "failed", last_error: LEAKY,
      calls_spent: 3, notes_written: 1, messages_left: 40,
    });
    mount({ backfill: failed });
    const out = await screen.findByTestId("backfill-outcome");
    await waitFor(() => expect(hiddenFromReaders(out)).toBe(false));
    expect(within(out).getByText(outcomeProse(failed))).toBeInTheDocument();
    expect(out.textContent).toMatch(/stopped after a failure/i);
    expect(out.textContent).not.toContain(LEAKY);
    expect(out.textContent).not.toMatch(/Rowan|notes\.txt/);
    // The numbers still reach the reader: dropping the echo is not
    // dropping the answer.
    expect(out.textContent).toMatch(/3 calls, 1 note written/);
    expect(out.textContent).toMatch(/40 messages are still unread/);
  });

  it("GROUND: a failure reason that IS a token is echoed", async () => {
    // The positive control for the row above. Without it a screen that had
    // simply stopped rendering `last_error` altogether would pass there.
    const failed = job({
      state: "failed", last_error: "ReadTimeout",
      calls_spent: 3, notes_written: 1, messages_left: 40,
    });
    mount({ backfill: failed });
    const out = await screen.findByTestId("backfill-outcome");
    await waitFor(() => expect(hiddenFromReaders(out)).toBe(false));
    expect(out.textContent).toContain("ReadTimeout");
  });

  it("undo asks first, then sends one POST with the chat, and says what stayed", async () => {
    // Under reduced motion, because this row is about the request and the
    // words, not the fade: the swaps it passes through take no time, so no
    // wait below is a wait on animation frames. The animated path has rows
    // of its own - ConditionalRenderMotion's, and the one below that lands
    // the answer on the confirm's last exit frame.
    reducedMotion();
    const user = userEvent.setup();
    const answer = { job_id: 17, removed: 12, kept_pinned: 3, kept_edited: 1, kept_accepted: 2, kept_older: 1 };
    const { calls } = mount({ backfill: job({ notes_written: 16 }) }, {
      [`POST /notebook/extract/undo/17`]: { body: answer },
    });
    await user.click(await screen.findByRole("button", { name: /undo this run/i }));
    // Nothing sent on the first press: the house confirm pattern.
    expect(calls("POST", "/notebook/extract/undo/")).toHaveLength(0);
    // `findByRole`, not `getByRole`: the trigger and its confirmation are a
    // cross-fade with `mode="wait"`, so the confirm pane mounts once the
    // trigger's pane has left rather than inside the click's own frame.
    await user.click(
      await screen.findByRole("button", { name: UNDO_RUN_CONFIRM }));
    await waitFor(() =>
      expect(calls("POST", `/notebook/extract/undo/17?chat_id=${CHAT}`)).toHaveLength(1));
    const said = await screen.findByTestId("backfill-undone");
    expect(said.textContent).toBe(undoProse(answer));
    // Each class under its own name, in the order the sentence promises, each count once.
    expect(said.textContent).toBe(
      "Removed 12 notes. Kept 7: 3 pinned, 1 edited, 2 kept, 1 earlier.");
  });

  it("the undo question says what will go and what stays, before anything goes", async () => {
    reducedMotion();
    const user = userEvent.setup();
    const { calls } = mount({ backfill: job({ notes_written: 16 }) }, {
      [`POST /notebook/extract/undo/17`]: { body: {
        job_id: 17, removed: 12, kept_pinned: 3, kept_edited: 1, kept_accepted: 0, kept_older: 0 } },
    });
    // GROUND: no question before the first press.
    expect(screen.queryByTestId("backfill-undo-ask")).toBeNull();
    await user.click(await screen.findByRole("button", { name: /^undo this run$/i }));
    const ask = await screen.findByTestId("backfill-undo-ask");
    expect(ask.textContent).toBe(UNDO_RUN_ASK);
    const confirm = screen.getByRole("button", { name: UNDO_RUN_CONFIRM });
    // Label in name: the words on the button ARE its name, and it points at
    // the question.
    expect((confirm.textContent ?? "").replace(/\s+/g, " ").trim()).toBe(UNDO_RUN_CONFIRM);
    expect(confirm).toHaveAccessibleDescription(UNDO_RUN_ASK);
    // It no longer promises "all": the count of what the run wrote is not
    // on the button, because what goes can be fewer.
    expect(confirm.textContent).not.toMatch(/all|16/i);
    expect(calls("POST", "/notebook/extract/undo/")).toHaveLength(0);
  });

  it("suggestions the reader discarded are deleted before the undo, so it does not count them", async () => {
    const user = userEvent.setup();
    const answer = { job_id: 17, removed: 2, kept_pinned: 0, kept_edited: 0, kept_accepted: 0, kept_older: 0 };
    const { fetchMock, queryClient } = mount({ backfill: job({ notes_written: 4 }) }, {
      "DELETE /notebook/entries/111": { body: { ok: true } },
      [`POST /notebook/extract/undo/17`]: { body: answer },
    });
    // The Notes list on hand says 111 is still a suggestion.
    queryClient.setQueryData(keys.notebookEntries(CHAT), {
      entries: [{ id: 111, status: "proposed" }], notebook_chars: 0 });
    useHeldDiscards.getState().hold(CHAT, 111);
    await user.click(await screen.findByRole("button", { name: /undo this run/i }));
    await user.click(await screen.findByRole("button", { name: UNDO_RUN_CONFIRM }));
    await screen.findByTestId("backfill-undone");
    const order = fetchMock.mock.calls
      .filter((c: unknown[]) => ["DELETE", "POST"].includes(String((c[1] as RequestInit | undefined)?.method)))
      .map((c: unknown[]) => `${(c[1] as RequestInit).method} ${String(c[0]).replace(/^.*\/api\/v1/, "")}`);
    // The held delete goes out, and BEFORE the undo.
    expect(order[0]).toMatch(/^DELETE \/notebook\/entries\/111\?chat_id=/);
    expect(order[1]).toMatch(/^POST \/notebook\/extract\/undo\/17/);
    expect(useHeldDiscards.getState().byChat[CHAT] ?? []).toEqual([]);
  });

  it("the undo keeps the keyboard at every step, and its answer is heard", async () => {
    // No step of the run's undo drops focus to the page's body. The
    // question opens on Cancel; Cancel and Escape give focus back to "Undo
    // this run"; Remove keeps it while the request is out (aria-disabled,
    // not disabled) and the answer line, a live region, takes it after.
    const user = userEvent.setup();
    const answer = { job_id: 17, removed: 12, kept_pinned: 3, kept_edited: 1, kept_accepted: 0, kept_older: 0 };
    const { calls, fetchMock } = mount({ backfill: job({ notes_written: 16 }) }, {
      [`POST /notebook/extract/undo/17`]: { body: answer },
    });
    const trigger = await screen.findByRole("button", { name: /undo this run/i });
    trigger.focus();
    await user.keyboard("{Enter}");
    await waitFor(() => expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus());
    await user.keyboard("{Enter}");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /undo this run/i })).toHaveFocus());
    await user.keyboard("{Enter}");
    await waitFor(() => expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus());
    await user.keyboard("{Escape}");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /undo this run/i })).toHaveFocus());
    expect(calls("POST", "/notebook/extract/undo/")).toHaveLength(0);

    await user.keyboard("{Enter}");
    const confirm = await screen.findByRole("button", { name: UNDO_RUN_CONFIRM });
    await waitFor(() => expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus());
    confirm.focus();
    const release = delayRoute(fetchMock, "POST", "/notebook/extract/undo/17", { body: answer });
    try {
      await user.keyboard("{Enter}");
      await waitFor(() => expect(confirm).toHaveAttribute("aria-disabled", "true"));
      // While it is out: still the same button, still focused, not disabled.
      expect(confirm).toHaveFocus();
      expect(confirm).not.toBeDisabled();
    } finally {
      release();
    }
    const said = await screen.findByTestId("backfill-undone");
    expect(said.textContent).toBe(undoProse(answer));
    expect(said).toHaveAttribute("role", "status");
    await waitFor(() => expect(said).toHaveFocus());
    expect(calls("POST", `/notebook/extract/undo/17?chat_id=${CHAT}`)).toHaveLength(1);
  });

  it("'Reading this chat now.' leaves once the run it started has finished", async () => {
    const user = userEvent.setup();
    const { poll } = mount({}, {
      [`POST /notebook/extract/run/${CHAT}`]: { body: { started: true, job_id: 5, calls_planned: 1 } },
    });
    await user.click(await screen.findByRole("button", { name: /read the earlier messages/i }));
    const start = await screen.findByRole("button", { name: /start reading/i });
    await waitFor(() => expect(start).toBeEnabled());
    await user.click(start);
    const said = await screen.findByTestId("backfill-said");
    await waitFor(() => expect(said.textContent).toBe("Reading this chat now."));
    await poll(job({ job_id: 5, state: "running", calls_spent: 0, notes_written: 0 }));
    // GROUND: while it runs, the sentence stays.
    await new Promise((r) => setTimeout(r, 30));
    expect(hiddenFromReaders(said)).toBe(false);
    await poll(job({ job_id: 5, state: "done", notes_written: 2 }));
    await waitFor(() => expect(hiddenFromReaders(said)).toBe(true));
    // The outcome says how it ended instead.
    expect(screen.getByTestId("backfill-outcome").textContent).toMatch(/^Finished reading/);
  });

  it("GROUND: a job that wrote nothing has nothing to undo", async () => {
    const { settled } = mount({ backfill: job({ notes_written: 0 }) });
    await settled();
    await screen.findByTestId("backfill-outcome");
    expect(screen.queryByRole("button", { name: /undo this run/i })).toBeNull();
  });

  it("a new job under an armed confirm disarms it, and the next press arms rather than deletes", async () => {
    // THE ONE PATH ON THIS STRIP THAT DELETES, and the only state on it
    // that outlives a render. `takeBack()` reads `job.job_id` fresh at the
    // moment of the press, and the twenty-second poll can replace the
    // block under an armed confirm: a second read of this chat, or a job
    // started from another window, arrives as a new `job_id` with nothing
    // remounting. A confirm that survives that change turns the reader's
    // next press into `POST /extract/undo/<the run they never looked at>`
    // and removes a different run's notes.
    const user = userEvent.setup();
    const first = job({ job_id: 17, notes_written: 16 });
    const second = job({ job_id: 18, notes_written: 9, calls_spent: 5 });
    const answer = { job_id: 18, removed: 9, kept_pinned: 0, kept_edited: 0, kept_accepted: 0, kept_older: 0 };
    const { calls, poll } = mount({ backfill: first }, {
      [`POST /notebook/extract/undo/18`]: { body: answer },
    });

    await user.click(
      await screen.findByRole("button", { name: /undo this run/i }));
    // GROUND: the confirm really was armed against job 17, so what happens
    // below is it being disarmed rather than it never having been there.
    await screen.findByRole("button", { name: UNDO_RUN_CONFIRM });

    await poll(second);
    // The poll landed and the block on screen is job 18's. The absence
    // questions are asked only after this, never inside the first wait.
    await waitFor(() => expect(
      screen.getByTestId("backfill-outcome").textContent,
    ).toContain(outcomeProse(second)));

    // The pane is a `CrossFade` with `mode="wait"`, so the one that is
    // leaving stays mounted while it animates out: the query settling is
    // not the same moment as the swap finishing, and this second wait is
    // about the animation, not about the network.
    await waitFor(() => expect(
      screen.queryByRole("button", { name: /^remove (it|all \d+)$/i }),
    ).toBeNull());
    // POSITIVE CONTROL for that absence: the undo affordance is still on
    // screen under the new job, so the confirm went and the block did not.
    const again = await screen.findByRole("button", { name: /undo this run/i });
    expect(calls("POST", "/notebook/extract/undo/")).toHaveLength(0);

    // AND THE PRESS AFTER A JOB CHANGE ARMS. Under a confirm that survived
    // the change this very press is the delete, against a run the reader
    // never looked at.
    await user.click(again);
    expect(calls("POST", "/notebook/extract/undo/")).toHaveLength(0);

    // POSITIVE CONTROL for the row as a whole: re-armed, the confirm still
    // deletes, and it deletes THIS job and no other.
    await user.click(
      await screen.findByRole("button", { name: UNDO_RUN_CONFIRM }));
    await waitFor(() => expect(
      calls("POST", `/notebook/extract/undo/18?chat_id=${CHAT}`),
    ).toHaveLength(1));
    expect(calls("POST", "/notebook/extract/undo/17")).toHaveLength(0);
  });

  it("the pane on its way out is dead: a press in the exit tick reaches nothing",
     async () => {
    // `CrossFade` runs `mode="wait"`, so for the length of
    // the exit the OLD pane is still in the document - and on this strip
    // the old pane is an ARMED confirm whose handler closed over the run
    // that just ended. A press landing in that tick sent
    // `POST /extract/undo/<the finished run>` with the reader looking at
    // the new one. The remedy is the language a closed `Collapse`
    // carries: the leaving pane is inert, hidden from readers and
    // refuses the pointer, so exactly one pane is live at a time.
    const user = userEvent.setup();
    const first = job({ job_id: 17, notes_written: 16 });
    const second = job({ job_id: 18, notes_written: 9, calls_spent: 5 });
    const { calls, poll } = mount({ backfill: first }, {
      "POST /notebook/extract/undo/17": { body: {
        job_id: 17, removed: 16, kept_pinned: 0, kept_edited: 0, kept_accepted: 0, kept_older: 0 } },
      "POST /notebook/extract/undo/18": { body: {
        job_id: 18, removed: 9, kept_pinned: 0, kept_edited: 0, kept_accepted: 0, kept_older: 0 } },
    });

    await user.click(
      await screen.findByRole("button", { name: /undo this run/i }));
    const armed = await screen.findByRole("button",
      { name: UNDO_RUN_CONFIRM });
    const pane = armed.closest('[data-slot="cross-fade"]');
    expect(pane, "the confirm is not inside a swap at all").not.toBeNull();
    // GROUND, and it is the positive control for every absence below: while
    // this pane is the live one it is reachable, it is not inert, and it
    // takes the pointer. Without this the row would pass on a pane that was
    // dead from the start.
    expect(hiddenFromReaders(armed)).toBe(false);
    expect(pane!.hasAttribute("inert"), "the live pane is already inert")
      .toBe(false);
    expect(pane!.className).not.toContain("pointer-events-none");

    // GROUND for the press half: right now this confirm is one a reader can
    // aim at.
    expect(pressable(armed), "the armed confirm is not pressable to begin with")
      .toBe(true);

    await poll(second);

    // THE EXIT TICK: caught while the old pane is still in the document and
    // already leaving.
    await waitFor(() => expect(
      pane!.hasAttribute("inert"), "the leaving pane never went inert",
    ).toBe(true));
    expect(pane, "the leaving pane is still in the accessibility tree")
      .toHaveAttribute("aria-hidden", "true");
    expect(pane!.className,
      "the leaving pane still takes the pointer")
      .toContain("pointer-events-none");
    expect(hiddenFromReaders(armed)).toBe(true);
    // By role, which is the reader's own question: the old confirm is not
    // there to be found any more.
    expect(screen.queryByRole("button", { name: /^remove (it|all \d+)$/i }))
      .toBeNull();

    // AND THE PRESS. The node is still mounted for the length of the fade,
    // so the press has to be answered rather than waved away - and it is
    // answered by COMPUTING it, for the reason AppShell's focus-ring case
    // computes its tab order: jsdom ignores `inert` outright, so driving a
    // click here would measure jsdom's gap and not the product. What the
    // product ships is a subtree a browser removes from hit testing and
    // from the tab order, and that is what `pressable` subtracts.
    expect(pressable(armed),
      "the confirm in the leaving pane can still be pressed").toBe(false);
    expect(calls("POST", "/notebook/extract/undo/"),
      "an undo went out during the exit tick").toHaveLength(0);
    // And nothing on screen says a run was undone.
    expect(screen.queryByTestId("backfill-undone")).toBeNull();

    // POSITIVE CONTROL for the row as a whole: once the swap has finished,
    // the pane that ARRIVED is live, and the strip still deletes when it is
    // asked to - against the new job and never the old one.
    await user.click(
      await screen.findByRole("button", { name: /undo this run/i }));
    await user.click(
      await screen.findByRole("button", { name: UNDO_RUN_CONFIRM }));
    await waitFor(() => expect(
      calls("POST", `/notebook/extract/undo/18?chat_id=${CHAT}`)).toHaveLength(1));
    expect(calls("POST", "/notebook/extract/undo/17")).toHaveLength(0);
  }, 30_000);

  it("under reduced motion there is no exit tick to land in", async () => {
    // The exit takes no time there, so the leaving pane is never on screen
    // dead: the swap is the plain instant replacement the ternary always
    // was, and nothing about the row above changes what that reader sees.
    reducedMotion();
    const user = userEvent.setup();
    const first = job({ job_id: 17, notes_written: 16 });
    const second = job({ job_id: 18, notes_written: 9, calls_spent: 5 });
    const { calls, poll } = mount({ backfill: first }, {
      "POST /notebook/extract/undo/17": { body: {
        job_id: 17, removed: 16, kept_pinned: 0, kept_edited: 0, kept_accepted: 0, kept_older: 0 } },
    });
    await user.click(
      await screen.findByRole("button", { name: /undo this run/i }));
    // GROUND: armed, and armed against the run that is about to be replaced.
    await screen.findByRole("button", { name: UNDO_RUN_CONFIRM });

    await poll(second);
    // The new pane is live and the old confirm is gone, with no dead pane
    // left behind to press.
    const again = await screen.findByRole("button", { name: /undo this run/i });
    expect(hiddenFromReaders(again)).toBe(false);
    expect(again.closest('[data-slot="cross-fade"]')!.hasAttribute("inert"))
      .toBe(false);
    expect(document.querySelectorAll('[data-slot="cross-fade"][inert]'))
      .toHaveLength(0);
    expect(calls("POST", "/notebook/extract/undo/")).toHaveLength(0);
  }, 30_000);

  it("a new job does not inherit the previous run's 'notes removed' line", async () => {
    // The other half of the same fact, and the cheaper half to get wrong:
    // the answer to an undo is about ONE run. Left unscoped it sits under
    // the next job's outcome and tells the reader that 12 notes were
    // removed from a run nobody undid.
    //
    // Under reduced motion, for the reason the undo row above gives: the
    // subject is which run the line belongs to, and the swaps on the way
    // there take no time.
    reducedMotion();
    const user = userEvent.setup();
    const first = job({ job_id: 17, notes_written: 16 });
    const second = job({ job_id: 18, notes_written: 9, calls_spent: 5 });
    const answer = { job_id: 17, removed: 12, kept_pinned: 3, kept_edited: 1, kept_accepted: 0, kept_older: 0 };
    const { poll, calls } = mount({ backfill: first }, {
      [`POST /notebook/extract/undo/17`]: { body: answer },
    });

    await user.click(
      await screen.findByRole("button", { name: /undo this run/i }));
    await user.click(
      await screen.findByRole("button", { name: UNDO_RUN_CONFIRM }));
    // The press first, then the line, as two waits: a press that sent
    // nothing and a line that never mounted are different failures, and
    // asked in order each one says which.
    await waitFor(() => expect(
      calls("POST", `/notebook/extract/undo/17?chat_id=${CHAT}`),
      "the confirm press sent no undo at all",
    ).toHaveLength(1));
    const said = await screen.findByTestId("backfill-undone");
    // GROUND: the line really was there, and it really was this run's.
    await waitFor(() => expect(said.textContent).toBe(undoProse(answer)));

    await poll(second);
    await waitFor(() => expect(
      screen.getByTestId("backfill-outcome").textContent,
    ).toContain(outcomeProse(second)));

    // Same `CrossFade` with `mode="wait"`: the leaving pane stays mounted
    // until its exit has finished - no time under reduced motion, but still
    // a render after the one the query caused - so this is its own wait
    // and not the query's, which the line above already settled.
    await waitFor(() => expect(
      screen.queryByTestId("backfill-undone"),
    ).toBeNull());
    // POSITIVE CONTROL: the block is still rendered and the new run can be
    // undone in its turn, so the line went rather than the whole outcome.
    await screen.findByRole("button", { name: /undo this run/i });
  });
});

describe("nothing carries over when the chat does", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: CHAT });
  });
  afterEach(() => vi.restoreAllMocks());

  it("clears the sentence and the range fields when another chat opens", async () => {
    // The strip is rendered without a `key`, so nothing remounts here. The
    // sentence is the visible half; the range fields are the expensive one,
    // because two message ids from chat A submitted against chat B are
    // either a different stretch or not there at all.
    const user = userEvent.setup({ delay: null });
    const { rerender } = mount({}, {
      [`POST /notebook/extract/run/${CHAT}`]:
        { body: { started: false, reason: "nothing_unread" } },
    }, { body: planBody({ scope: "range", messages_unread: 5, calls_planned: 1 }) });
    await openPreview(user);
    await pickScope(user, /a range of messages/i);
    await user.type(await screen.findByLabelText(/first message/i), "2");
    await user.clear(screen.getByLabelText(/last message/i));
    await user.type(screen.getByLabelText(/last message/i), "6");
    const start = screen.getByRole("button", { name: /start reading/i });
    await waitFor(() => expect(start).toBeEnabled());
    await user.click(start);
    const said = await screen.findByTestId("backfill-said");
    await waitFor(() => expect(said.textContent).toBe(SWEEP_PROSE.nothing_unread));

    // GROUND: a re-render of the SAME chat changes nothing, so the clear
    // below is the chat changing rather than any re-render clearing it.
    rerender(<BackfillStrip chatId={CHAT} />);
    expect(hiddenFromReaders(screen.getByTestId("backfill-said"))).toBe(false);
    expect(screen.getByLabelText(/first message/i)).toHaveValue(2);

    rerender(<BackfillStrip chatId={OTHER} />);
    expect(hiddenFromReaders(screen.queryByTestId("backfill-said"))).toBe(true);
    // The preview closed with it, so the fields are reached the way a
    // reader would reach them: by opening the new chat's strip.
    await openPreview(user);
    await pickScope(user, /a range of messages/i);
    expect(screen.getByLabelText(/first message/i)).toHaveValue(null);
    expect(screen.getByLabelText(/last message/i)).toHaveValue(null);
  });
});

describe("its place in the Notebook panel", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: CHAT });
  });
  afterEach(() => vi.restoreAllMocks());

  it("sits under the title row and above the just-saved strip", async () => {
    mockFetch({
      [`/notebook/${CHAT}`]: { body: { entries: [{
        id: 5, chat_id: CHAT, position: 0, kind: "fact",
        text: "Her brother owns the mill.", evidence: null,
        durability: "permanent", importance: 2, pinned: 0, retired_at: null,
        superseded_by: null, excluded_reason: null, status: "accepted",
        provenance: "model", source_message_id: null,
        created_at: "2026-09-18", updated_at: "2026-09-18",
      }], notebook_chars: 40 } },
      "/notebook/worker": { body: statusBody() },
    });
    renderWithQueryClient(<NotebookPanel />);
    const strip = await screen.findByTestId("backfill-strip");
    const title = screen.getByRole("heading", { name: /notebook/i });
    const saved = await screen.findByTestId("just-saved");
    // DOCUMENT_POSITION_FOLLOWING (4): the argument comes after the node.
    expect(title.compareDocumentPosition(strip) & Node.DOCUMENT_POSITION_FOLLOWING)
      .toBeTruthy();
    expect(strip.compareDocumentPosition(saved) & Node.DOCUMENT_POSITION_FOLLOWING)
      .toBeTruthy();
  });
});

describe("the strip's Select sits on the house primitive (KURAL 7.4, T-19)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: CHAT });
  });
  afterEach(() => { cleanup(); vi.restoreAllMocks(); });

  /** The strip opened, its preview out, and the scope list showing. */
  async function openScopeList() {
    const user = userEvent.setup();
    mount();
    await openPreview(user);
    const trigger = screen.getByRole("combobox", { name: /what to read/i });
    const listbox = await openSelect(user, trigger);
    const popup = openPopup();
    expect(popup, "no popup rendered").not.toBeNull();
    return { user, trigger, listbox, popup: popup! };
  }

  /** A length the sheet writes, in pixels, off the winning rule. */
  function sheetPx(
    root: ReturnType<typeof parseSheet>,
    table: Map<string, string>,
    rem: number,
    el: Element,
    prop: RegExp,
    what: string,
  ): number {
    const won = winningDeclaration(root, el, prop);
    expect(won, `${what} is not declared by any rule in the sheet`).not.toBeNull();
    return lengthPx(resolveVars(won!.value, table), rem);
  }

  it("KURAL 7.4 closes on this call site: the token duration, no trigger "
     + "alignment, and a field that was told its words", async () => {
    // The three traps KURAL 7.4 puts in the BASE STRING rather than at a
    // call site. They are asked HERE because the strip is the primitive's
    // first production call site and the whole point of the rule is that
    // this call site needs no patch of its own. All three read off what
    // rendered, never off the source.
    const { trigger, popup } = await openScopeList();
    const classes = Array.from(popup.classList);
    expect(classes, "the popup rendered with no classes at all")
      .not.toHaveLength(0);

    // 1. No hard-coded duration on the popup. The name is ASSEMBLED:
    //    Tailwind scans this file as raw text, so spelling the utility here
    //    would emit into the shipped sheet the very rule this line says is
    //    absent.
    const literal = "duration-" + "100";
    expect(classes, "the popup carries a duration literal").not.toContain(literal);
    // POSITIVE CONTROL for the same query: it does carry a duration, it is a
    // token, and the token is the house's fast one. Without this half, a
    // popup with no duration at all would pass the line above.
    const tokenClass = classes.find((c) => c.startsWith("duration-("));
    expect(tokenClass, "the popup declares no duration at all").toBeTruthy();
    const root = parseSheet(await loadGlassRightCss());
    const table = rootTokens(root);
    const name = /^duration-\((--[a-z-]+)\)$/.exec(tokenClass!);
    expect(name, "the duration class names no token").not.toBeNull();
    expect(timeMs(resolveVars(`var(${name![1]})`, table)))
      .toBe(durationSeconds.fast * 1000);

    // And none on the TRIGGER either. A duration written at this call site
    // would override the base string's own for this one field, which is the
    // patch the rule says this call site must not need, and the popup half
    // above never looks at the trigger. Three spellings write one: a
    // `duration-` class whose value is not a token, an arbitrary property
    // that sets a transition's or an animation's time without one, and an
    // arbitrary property that sets Tailwind's own duration variable, which
    // every transition utility on the element reads, to anything but a
    // token. Behind a variant or not (`utilityOf` cuts the variant off, and
    // keeps the colon an arbitrary property carries inside its brackets).
    const variableSetter = new RegExp(`^\\[${TW_DURATION}:`);
    const literals = (el: Element) => [...el.classList].map(utilityOf).filter((c) =>
      (c.startsWith("duration-") && !c.startsWith("duration-("))
      || (/^\[(transition|animation)(-duration)?:/.test(c) && !/var\(--duration-/.test(c))
      || (variableSetter.test(c) && !/^\[[-\w]+:var\(--duration-[a-z-]+\)\]$/.test(c)));
    // GROUND: the trigger is wearing the base string, so the list read below
    // is the one the call site and the primitive produced together.
    expect([...trigger.classList], "the trigger is not wearing the house base string")
      .toContain("justify-between");
    expect(literals(trigger), "the trigger carries a duration literal").toEqual([]);
    // POSITIVE CONTROL: the predicate finds a literal in both spellings, bare
    // and behind a variant, and passes a token in both. Names assembled, for
    // the reason above.
    const arbitrary = "[transition-" + "duration:";
    const variable = "[" + TW_DURATION + ":";
    const probe = document.createElement("span");
    probe.className = [literal, "hover:" + "duration-" + "75", name![0],
      arbitrary + "200ms]", "data-[state=open]:" + arbitrary + "120ms]",
      arbitrary + "var(--duration-fast)]", variable + "200ms]",
      "hover:" + variable + "150ms]", variable + "var(--duration-fast)]"].join(" ");
    expect(literals(probe), "the literal-duration reader went blind").toHaveLength(6);
    // The fourth spelling is an inline style, which no class reader sees.
    const inlineMotion = (el: Element) => (el.getAttribute("style") ?? "").split(";")
      .map((part) => part.split(":")[0].trim().toLowerCase())
      .filter((prop) => /^(transition|animation)(-duration)?$/.test(prop) || prop === TW_DURATION);
    expect(inlineMotion(trigger), "the trigger carries an inline duration").toEqual([]);
    // POSITIVE CONTROL: the inline reader finds one among other declarations,
    // the duration variable included.
    const inlineProbe = document.createElement("span");
    inlineProbe.setAttribute("style",
      `color: red; transition-duration: 200ms; ${TW_DURATION}: 200ms`);
    expect(inlineMotion(inlineProbe), "the inline-duration reader went blind")
      .toEqual(["transition-duration", TW_DURATION]);

    // 2. `alignItemWithTrigger` is false, so the branch that binds
    //    `data-[align-trigger=true]:animate-none` - the popup opening with
    //    no animation at all - is not taken.
    const aligned = popup.getAttribute("data-align-trigger");
    expect(aligned, "the popup carries no alignment marker to read").not.toBeNull();
    expect(aligned, "the popup is item-aligned, so it opens with no animation")
      .toBe("false");

    // 3. `items` really reached the root, so the shut field reads the
    //    sentence and never the wire token. The run block asks this of every
    //    scope in turn; it is repeated here because this is the closure row
    //    and a reader of it should not have to go looking for the third leg.
    await userEvent.setup().keyboard("{Escape}");
    await waitFor(() => expect(fieldText(trigger)).toBe(SCOPE_LABELS.all));
    expect(fieldText(trigger), "the field is showing the wire token")
      .not.toBe("all");
  }, 30_000);

  it("the strip's chrome is on the chrome step and its prose on the label "
     + "step (IP-05's role rule)", async () => {
    // The strip's own type, and the reason it needs a case of its own: the
    // class-closure sweep in fontScale.test.ts walks what `AppShell` paints
    // on first render, and this strip sits inside the right panel's Notes
    // tab, which that sweep never opens. Nothing else looks at it.
    //
    // The rule is IP-05's, one sentence: text INSIDE a control or NAMING a
    // control takes the chrome step; everything else takes the label step.
    // Both class names are taken BY NAME off the imported scale and typed as
    // its keys, so a step that is renamed is a compile error here rather
    // than two string literals that match nothing.
    const uiStep: keyof typeof typeScale = "ui";
    const labelStep: keyof typeof typeScale = "label";
    const UI = `text-${uiStep}`;
    const LABEL = `text-${labelStep}`;
    // GROUND for the two names: they are the 13 and the 12 the rule means.
    expect(typeScale[uiStep].px).toBe(13);
    expect(typeScale[labelStep].px).toBe(12);

    // EVERY STATE THAT RENDERS A CONTROL, because the ones that do not get
    // looked at are exactly the ones that drift. A single mount answers for
    // one branch of the strip and leaves the rest unguarded: `Stop reading`
    // lives inside `<Collapse open={running}>`, a closed Collapse is
    // `aria-hidden`, and a role query skips what is hidden from readers - so
    // a finished-job mount cannot see that button at all, and the sheet-side
    // sweep in fontScale.test.ts never opens this tab either: a retired size
    // class on that button is invisible to both of them.
    //
    // Controls are therefore taken off the STRIP's own subtree by tag, not
    // by role, so an inert pane is still read. `text-<step>` is matched
    // against the scale's own keys, so a class off the scale (a retired
    // name, an arbitrary size) leaves the control with none and fails.
    const sheetRoot = parseSheet(await loadGlassRightCss());
    const sheetTable = rootTokens(sheetRoot);
    const sheetRem = remPx(sheetRoot);
    const STEPS = Object.keys(typeScale).map((k) => `text-${k}`);
    /** The size step a node carries, or null. Never more than one. */
    const stepOf = (el: Element): string | null => {
      const found = [...el.classList].filter((c) => STEPS.includes(c));
      expect(found.length, `${el.tagName} carries two size steps at once`)
        .toBeLessThan(2);
      return found[0] ?? null;
    };

    /** Every distinct control the strip put on the chrome step, over all the
     *  states below. The inventory row in tokens.ts names a COUNT, and a
     *  count in prose is a claim nothing checks - so the strip's share of it
     *  is counted here, where the controls actually render. */
    const onChromeStep = new Set<string>();

    // The states the strip renders a control in: nothing read yet with a
    // backlog on offer, the preview open, a job running, a job the reader
    // STOPPED and a job that finished - both of which offer the undo - and
    // the undo armed, which swaps the pair in place. Stopped is its own
    // state and not a finished one with other words: it is how a run the
    // reader halted ends, and it is the one a halted job actually shows.
    const running = job({
      state: "running", calls_spent: 8, notes_written: 14, messages_left: 240,
    });
    const stopped = job({
      state: "stopped", calls_spent: 8, notes_written: 14, messages_left: 240,
    });
    const done = job({ notes_written: 14 });
    const served: Record<string, BackfillStatus | null> = {
      idle: null, preview: null, running, stopped, done, armed: done,
    };
    // GROUND for each state: the control that makes it worth walking, and
    // for the two ended states the sentence that tells them apart. Without
    // the first the sweep below is green on a strip that rendered nothing;
    // without the second "stopped" could quietly be the done strip again.
    const wanted: Record<string, RegExp> = {
      idle: /read the earlier messages/i,
      preview: /start reading/i,
      running: /stop reading/i,
      stopped: /undo this run/i,
      done: /undo this run/i,
      armed: /^cancel/i,
    };
    const ended: Record<string, string> = {
      stopped: outcomeProse(stopped),
      done: outcomeProse(done),
    };
    // The two ended states differ by their STATE and not only by their
    // numbers: the same job told it finished must read differently from the
    // job told it was stopped, or "stopped" below is the done strip again
    // with other counts in it.
    expect(ended.stopped, "a stopped job reads the same as a finished one")
      .not.toBe(outcomeProse({ ...stopped, state: "done" }));

    for (const state of Object.keys(served)) {
      const user = userEvent.setup();
      const view = mount({
        backlog: { chats: 3, messages: 512 },
        backfill: served[state],
      });
      const strip = await screen.findByTestId("backfill-strip");
      await view.settled();
      if (state === "preview") await openPreview(user);
      if (state === "armed") {
        await user.click(
          await screen.findByRole("button", { name: /undo this run/i }));
        await screen.findByRole("button", { name: UNDO_RUN_CONFIRM });
      }

      const buttons = [...strip.querySelectorAll("button")];
      const named = buttons
        .map((b) => `${(b.textContent ?? "").trim()} ${b.getAttribute("aria-label") ?? ""}`);
      expect(named.some((n) => wanted[state].test(n)),
        `${state}: ${wanted[state]} is not on the strip, so this state was not reached`)
        .toBe(true);
      if (ended[state]) {
        expect(within(strip).getByTestId("backfill-outcome").textContent,
          `${state}: the outcome block is not this state's`)
          .toContain(ended[state]);
      }

      // CONTROLS: every button, and the scope field, take the chrome step.
      // The combobox is a button too, so the tag sweep already holds it; it
      // is named separately in the message so a failure says which.
      const offScale = buttons
        .filter((el) => stepOf(el) !== UI)
        .map((el) => `${state}: ${(el.textContent ?? "").trim()
          || el.getAttribute("aria-label")} -> ${stepOf(el) ?? "no step at all"}`);
      expect(offScale,
        "a control on this strip sizes its own words off the chrome step")
        .toEqual([]);
      for (const b of buttons) {
        // The scope trigger reads as its own value, which changes; it is
        // identified by the label it is named by instead.
        onChromeStep.add((b.getAttribute("aria-labelledby")
          ?? b.getAttribute("aria-label")
          ?? (b.textContent ?? "").trim()).replace(/\d+/g, "N"));
      }

      // PROSE: the other direction, and what stops "put everything on 13"
      // from passing. Every paragraph the strip renders takes the label
      // step, in every state, including the ones inside a shut Collapse.
      //
      // Two ways a line can be on that step and both are asked. Most carry
      // the utility. The local-error line carries `persona-local-error`
      // instead, which the SHEET sizes - so its step is resolved through the
      // compiled stylesheet rather than waved through for having no utility,
      // which is the hole a class-list sweep leaves open.
      const paragraphs = [...strip.querySelectorAll("p")];
      expect(paragraphs.length, `${state}: the strip rendered no prose at all`)
        .toBeGreaterThan(0);
      const utilityProse = paragraphs.filter((p) => stepOf(p) !== null);
      expect(utilityProse.length, `${state}: no line carries a step of its own`)
        .toBeGreaterThan(0);
      expect(utilityProse.filter((p) => stepOf(p) !== LABEL)
        .map((p) => `${state}: ${(p.textContent ?? "").trim().slice(0, 40)}`),
        "a line of prose on this strip is not on the label step")
        .toEqual([]);
      for (const p of paragraphs.filter((el) => stepOf(el) === null)) {
        expect(
          sheetPx(sheetRoot, sheetTable, sheetRem, p, /^font-size$/,
            `${(p.textContent ?? "").trim().slice(0, 40)}'s size`),
          `${state}: a line the sheet sizes is not on the label step`,
        ).toBe(typeScale.label.px);
      }

      if (state === "preview") {
        // The span that NAMES the scope field, which the rule puts on the
        // step of the control it names rather than on a label's 12.
        const label = document.getElementById("backfill-scope-label");
        expect(label, "the scope field's label span is not on the page")
          .not.toBeNull();
        expect(stepOf(label!), "the span naming a control took the label step")
          .toBe(UI);
        // And the two range FIELDS, which are the documented exception: a
        // field labels content, so it takes the table's own 12. Asserted
        // rather than skipped, because "not 13" is the whole claim.
        // By type, because Base UI's Select renders a hidden `<input>` of
        // its own for form submission and that one is not a field a reader
        // types in.
        // Named by a visible `<label>` now (KURAL 11.3), so the name is
        // read through the field's own `labels`, not an attribute.
        const fields = [...strip.querySelectorAll<HTMLInputElement>(
          'input[type="number"]')];
        const names = fields.map((f) => f.labels?.[0]?.textContent ?? null);
        expect(names, "the range fields are not mounted")
          .toEqual([RANGE_WORDS.firstLabel, RANGE_WORDS.lastLabel]);
        for (const field of fields) {
          expect(stepOf(field),
            `${field.labels?.[0]?.textContent} left the label step`)
            .toBe(LABEL);
          // The name beside a field is chrome, like the scope field's.
          expect(stepOf(field.labels![0]),
            "the words naming a range field took the label step")
            .toBe(UI);
        }
      }
      cleanup();
    }

    // THE STRIP'S SHARE of the count the inventory row names, measured:
    // seven buttons and the scope trigger here, and the ninth is the span
    // that names the trigger, asserted above. The row's other seven are the
    // saved-notes block's four buttons, held where that block renders - two
    // panes of two, in NotebookPanel.test.tsx - and the worker card's three,
    // held by the Notes tab sweep in fontScale.test.ts. A control added to
    // this strip without a step, or one that stopped rendering, moves this
    // number.
    expect([...onChromeStep].sort(),
      "the strip's set of chrome-step controls changed; the inventory row in "
      + "tokens.ts names a count and it is now wrong")
      .toEqual([
        "Cancel",
        "Read the earlier messages",
        "Remove untouched notes",
        "Start reading",
        "Stop reading",
        "Undo this run",
        "backfill-scope-label",
      ]);
  }, 60_000);

  it("T-19: all three scopes render whole, and the check mark has a column "
     + "of its own", async () => {
    // A popup that follows a content-sized trigger clips its own longest
    // option. `ui/select.tsx` sizes the popup by that option instead, with
    // the trigger's width only as a floor, so the question here is whether
    // that rule covers the strip with no width written on the popup at this
    // call site.
    //
    // A CLASS CONTRACT, and named as one the way BoundaryPanel's T-19 row
    // is: jsdom lays nothing out, so `scrollWidth` and `clientWidth` are
    // both 0 on every node here and comparing them would be green on a
    // popup one pixel wide. What the render tree can show is which contract
    // the popup renders under; what the compiled sheet can show is the
    // arithmetic underneath the one claim that is arithmetic.
    const { trigger, popup, listbox } = await openScopeList();
    // THE TRIGGER FILLS ITS ROW, the same way the boundary panel's pickers
    // and the two extraction pickers do. `SelectTrigger`'s base string is
    // `w-fit`, and over two full-width range inputs it rendered a scope
    // field narrower than the fields under it (205.7 px in a 274 px row,
    // measured at a 350 px panel). `min-w-0` is the other half of the pair
    // those pickers carry, and here it moves no pixel: the trigger's parent
    // is a block box, whose minimum is already 0 (measured: a 456 px name
    // leaves the row at the card's width with the class and without it).
    // What does keep a long name inside the panel is asked in the next row.
    const triggerClasses = Array.from(trigger.classList);
    // GROUND: this really is the merged base-plus-call-site list and not an
    // empty node. `justify-between` is the base string's and is in no width
    // group, so it survives whatever the call site says about width.
    expect(triggerClasses, "the trigger is not wearing the house base string")
      .toContain("justify-between");
    expect(triggerClasses, "the scope field no longer fills its row")
      .toContain("w-full");
    expect(triggerClasses, "the trigger lost the min-w-0 the other pickers pair with "
      + "w-full (inert under this block parent, measured)")
      .toContain("min-w-0");
    // And the base's content width LOST, which is what "the call site wins"
    // means here: `cn` merges through tailwind-merge, so `w-full` and
    // `w-fit` cannot both survive. Both directions are asserted because a
    // trigger carrying the pair would be a merge that stopped working, and
    // which of the two the browser then took would depend on rule order.
    expect(triggerClasses, "both widths survived the merge, so the winner is order")
      .not.toContain("w-fit");
    // POSITIVE CONTROL for that absence: the base string really does carry
    // `w-fit`, so its absence above is the call site winning the merge and
    // not a base that never had it. Read off a bare trigger rendered from
    // the same `ui/select.tsx`, with no class of its own.
    renderWithQueryClient(
      <Select items={{ a: "Alpha" }}>
        <SelectTrigger aria-label="bare trigger"><SelectValue /></SelectTrigger>
      </Select>,
    );
    const bare = Array.from(screen.getByLabelText("bare trigger").classList);
    expect(bare, "the bare trigger is not wearing the house base string")
      .toContain("justify-between");
    expect(bare, "the base string has no content width for the call site to beat")
      .toContain("w-fit");
    expect(bare, "the base string already fills its row, so the call site proves nothing")
      .not.toContain("w-full");
    const classes = Array.from(popup.classList);
    // Sized by its own content with the trigger's width as a FLOOR. That
    // pair is what stops a content-sized trigger clipping the options.
    expect(classes, "the popup lost its anchor-width floor")
      .toContain("min-w-(--anchor-width)");
    expect(classes, "the popup is not sized by its own content")
      .toContain("w-auto");
    // ASSEMBLED, same reason as the duration literal above.
    expect(classes, "the popup is pinned to the trigger's width")
      .not.toContain("w-" + "(--anchor-width)");
    expect(classes, "the popup lost its width ceiling")
      .toContain("max-w-[min(20rem,calc(100vw-2rem))]");
    // And nothing scrolls sideways, so a clipped option would be
    // unreachable rather than merely awkward.
    expect(classes, "the popup scrolls sideways instead of fitting")
      .toContain("overflow-x-hidden");

    // All three labels, whole and in the order the strip offers them, and
    // none of them wearing a clipping utility.
    const options = within(listbox).getAllByRole("option");
    expect(options.map((o) => (o.textContent ?? "").trim()))
      .toEqual(SCOPE_ORDER.map((scope) => SCOPE_LABELS[scope]));
    const clipping = ["truncate", "text-" + "ellipsis", "overflow-" + "hidden"];
    for (const option of options) {
      for (const banned of clipping) {
        expect([...option.classList], `${option.textContent} carries ${banned}`)
          .not.toContain(banned);
      }
    }
    // The words in them are on the option step, by class and by what the
    // compiled sheet makes of it, the step pinned to KURAL 3.5's 14 in
    // BoundaryPanel.test.tsx. And the popup was really placed with the
    // shared padding T-75 names, read off what Base UI did with the prop
    // rather than off the attribute that reports it.
    const root = parseSheet(await loadGlassRightCss());
    expectOptionsOnTheOptionStep(listbox, "the strip's scope popup", root);
    await expectPlacedCollisionPadding(listbox, "the strip's scope popup");

    // THE CHECK MARK'S COLUMN, measured rather than eyeballed. The indicator
    // is absolutely positioned, so nothing about the words pushes it aside:
    // it is the item's right PADDING that keeps the two apart, and the three
    // numbers come out of the compiled sheet through the same reader every
    // other geometry case in this suite uses.
    const table = rootTokens(root);
    const rem = remPx(root);
    const chosen = options[0];
    const mark = chosen.querySelector("span.absolute");
    expect(mark, "the option renders no indicator to measure").not.toBeNull();
    const reserved = sheetPx(root, table, rem, chosen,
      /^padding-(inline-end|right)$/, "the item's right padding");
    const offset = sheetPx(root, table, rem, mark!, /^right$/,
      "the indicator's right offset");
    const markWidth = sheetPx(root, table, rem, mark!, /^width$/,
      "the indicator's width");
    expect(reserved, "the check mark sits on top of the option's words")
      .toBeGreaterThanOrEqual(offset + markWidth);
    // GROUND: these are real numbers and not three zeroes agreeing with
    // each other, which is what an unresolved token would give.
    expect(markWidth, "the indicator measured zero wide").toBeGreaterThan(0);
    expect(reserved, "the item reserves no right padding at all")
      .toBeGreaterThan(0);
  }, 30_000);

  it("a scope name too long for its field ends in an ellipsis, and the row keeps "
     + "to the panel", async () => {
    // KURAL 4.4 designs a right-panel row at 300 px, and there, measured
    // headless, the range scope's name pushed its row 2.23 px past the card
    // and could not end in an ellipsis. Both come from the chain of boxes
    // the words sit in, and jsdom lays nothing out, so the chain is asked of
    // the compiled sheet one box at a time: the arithmetic is the engine's.
    const root = parseSheet(await loadGlassRightCss());
    const tokenTable = rootTokens(root);
    const rem = remPx(root);
    /** The value the cascade gives `prop` on `el`, var() resolved. */
    const won = (el: Element, prop: RegExp): string | null => {
      const w = winningDeclaration(root, el, prop);
      return w === null ? null : resolveVars(w.value, tokenTable).trim();
    };
    const user = userEvent.setup();
    mount();
    await openPreview(user);
    /** The title on the element that holds the field's words. */
    const titleOnTheWords = () => screen.getByRole("combobox", { name: /what to read/i })
      .querySelector('[data-slot="select-value"] > *')?.getAttribute("title") ?? null;
    // Before the choice, the title is the default scope's, so the one read
    // in step 5 follows the value rather than being written once.
    expect(titleOnTheWords(), "the default scope's words carry no title of their own")
      .toBe(SCOPE_LABELS.all);
    // The longest of the three names, the one that did not fit.
    await pickScope(user, SCOPE_LABELS.range);
    const trigger = screen.getByRole("combobox", { name: /what to read/i });
    await waitFor(() => expect(fieldText(trigger)).toBe(SCOPE_LABELS.range));

    // 1. THE ROW'S TRACK. The preview is a Collapse, and its inner is a grid
    //    item; a grid item's automatic minimum is its content's own width,
    //    and here that is the nowrap trigger's whole name. A minimum of 0 is
    //    what lets the track stay the width of the card.
    const inner = screen.getByTestId("backfill-preview").parentElement!;
    expect(inner.classList.contains("es-collapse-inner"),
      "the preview is not the content of a collapse").toBe(true);
    expect(won(inner.parentElement!, /^display$/),
      "the preview's collapse is not a grid, so its inner is not a grid item")
      .toBe("grid");
    const minimum = won(inner, /^min-width$/);
    expect(minimum, "the preview's grid item keeps its words' width as its minimum")
      .not.toBeNull();
    expect(lengthPx(minimum!, rem), "the preview's grid item has a minimum above 0")
      .toBe(0);

    // 2. THE FIELD FILLS THE ROW, and nothing wider.
    expect(won(trigger, /^width$/), "the scope field does not fill its row").toBe("100%");

    // 3. THE VALUE CAN SHRINK. It is a flex item of the trigger, and a flex
    //    item that clips has an automatic minimum of 0, so it gives up width
    //    before the trigger overflows.
    expect(won(trigger, /^display$/)).toBe("flex");
    const value = trigger.querySelector<HTMLElement>('[data-slot="select-value"]');
    expect(value, "the trigger shows no value node").not.toBeNull();
    expect(won(value!, /^overflow$/), "the value keeps its whole name as its minimum")
      .toBe("hidden");

    // 4. THE WORDS END IN AN ELLIPSIS. The base string makes the value itself
    //    a flex box, and text inside a flex box cannot be elided, so the
    //    words sit in a child that truncates.
    expect(won(value!, /^display$/)).toBe("flex");
    const words = value!.firstElementChild;
    expect(words?.textContent, "the value's words are not in a box of their own")
      .toBe(SCOPE_LABELS.range);
    // POSITIVE CONTROL for the absence below: the reader finds the ellipsis
    // where it is declared, on the words' own box.
    expect(won(words!, /^text-overflow$/), "the name cannot end in an ellipsis")
      .toBe("ellipsis");
    expect(won(words!, /^white-space$/)).toBe("nowrap");
    expect(won(words!, /^overflow$/)).toBe("hidden");
    // And none on the value node itself, which is exactly why one there
    // would never show.
    expect(won(value!, /^text-overflow$/)).toBeNull();

    // 5. THE WHOLE NAME CAN STILL BE READ. KURAL 4.4's third leg: text a row
    //    cuts short keeps its full form within reach, as a title. It is the
    //    chosen scope's own label, so it follows the value.
    expect(titleOnTheWords(), "the name cut short carries no title")
      .toBe(SCOPE_LABELS.range);
  }, 30_000);
});

describe("every count the strip says, at 0, 1 and 2", () => {
  // A number the strip shows is followed by a noun or a verb that has to
  // agree with it, and "1 unread messages", "Confirm removing 1 notes" and
  // "Keep them" beside "Remove it" were all on it. One row per wording:
  // the sentence functions are read from a body, the rest from the strip as
  // it renders, each at 0, 1 and 2.
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: CHAT });
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  const flat = (text: string | null | undefined): string => (text ?? "").replace(/\s+/g, " ").trim();

  /** One sentence function's wording at 0, 1 and 2. */
  const SENTENCES: { name: string; say: (n: number) => string; says: [string, string, string] }[] = [
    { name: "the preview's unread messages",
      say: (n) => planProse(planBody({ messages_unread: n })),
      says: ["0 unread messages:", "1 unread message:", "2 unread messages:"] },
    { name: "the preview's messages in a range",
      say: (n) => planProse(planBody({ scope: "range", messages_unread: n })),
      says: ["0 messages in that range:", "1 message in that range:", "2 messages in that range:"] },
    { name: "the preview's calls",
      say: (n) => planProse(planBody({ calls_planned: n })),
      says: [": 0 calls,", ": 1 call,", ": 2 calls,"] },
    { name: "the calls reading history may use today",
      say: (n) => planProse(planBody({ calls_spent_today: 0, backfill_cap: n })),
      says: ["0 of 0 calls for reading history used today.",
        "0 of 1 call for reading history used today.",
        "0 of 2 calls for reading history used today."] },
    { name: "the calls kept for conversations",
      say: (n) => capProse(planBody({ fits_today: false, calls_spent_today: 50, backfill_cap: 50,
        live_reserve: n, daily_cap: 60 })),
      says: ["cannot start until tomorrow.",
        "The last one of the day's 60 calls is kept for your own conversations.",
        "The last 2 of the day's 60 calls are kept for your own conversations."] },
    { name: "the calls that fit today",
      say: (n) => capProse(planBody({ fits_today: false, calls_planned: 21, calls_spent_today: 48 - n,
        backfill_cap: 48 })),
      says: ["already used, so this cannot start until tomorrow.",
        "Only 1 of those 21 calls fits today:", "Only 2 of those 21 calls fit today:"] },
    { name: "the calls a finished run spent",
      say: (n) => outcomeProse(job({ calls_spent: n })),
      says: ["Finished reading: 0 calls,", "Finished reading: 1 call,", "Finished reading: 2 calls,"] },
    { name: "the notes a finished run wrote",
      say: (n) => outcomeProse(job({ notes_written: n })),
      says: ["0 notes written.", "1 note written.", "2 notes written."] },
    { name: "the messages a stopped run left",
      say: (n) => outcomeProse(job({ state: "stopped", messages_left: n })),
      says: ["0 messages are still unread.", "1 message is still unread.",
        "2 messages are still unread."] },
    { name: "the notes an undo removed",
      say: (n) => undoProse({ job_id: 17, removed: n, kept_pinned: 0, kept_edited: 0, kept_accepted: 0, kept_older: 0 }),
      says: [UNDO_NOTHING_LEFT, "Removed 1 note.", "Removed 2 notes."] },
    { name: "the notes an undo kept because they were pinned",
      say: (n) => undoProse({ job_id: 17, removed: 3, kept_pinned: n, kept_edited: 0, kept_accepted: 0, kept_older: 0 }),
      says: ["Removed 3 notes.", "Removed 3 notes. Kept 1 pinned note.",
        "Removed 3 notes. Kept 2 pinned notes."] },
    { name: "the notes an undo kept because they were accepted by hand",
      say: (n) => undoProse({ job_id: 17, removed: 0, kept_pinned: 0, kept_edited: 0, kept_accepted: n, kept_older: 0 }),
      says: [UNDO_NOTHING_LEFT, "Removed nothing. Kept 1 note you chose to keep.",
        "Removed nothing. Kept 2 notes you chose to keep."] },
    { name: "the notes an undo kept because they were edited",
      say: (n) => undoProse({ job_id: 17, removed: 1, kept_pinned: 0, kept_edited: n, kept_accepted: 0, kept_older: 0 }),
      says: ["Removed 1 note.", "Removed 1 note. Kept 1 edited note.",
        "Removed 1 note. Kept 2 edited notes."] },
    { name: "the notes an undo kept from before the marks",
      say: (n) => undoProse({ job_id: 17, removed: 1, kept_pinned: 1, kept_edited: 0, kept_accepted: 0, kept_older: n }),
      says: ["Removed 1 note. Kept 1 pinned note.",
        "Removed 1 note. Kept 2: 1 pinned, 1 earlier.",
        "Removed 1 note. Kept 3: 1 pinned, 2 earlier."] },
  ];

  it.each(SENTENCES.map((s) => [s.name, s] as const))("%s", (_name, sentence) => {
    for (const n of [0, 1, 2] as const) {
      expect(flat(sentence.say(n)), `at ${n}`).toContain(sentence.says[n]);
    }
  });

  /** The backlog line's words as the strip shows them, or null when its
   *  Collapse is shut. */
  async function backlogAt(chats: number, messages: number): Promise<string | null> {
    cleanup();
    const { settled } = mount({ backlog: { chats, messages } });
    await settled();
    const note = screen.getByTestId("worker-backlog");
    if (hiddenFromReaders(note)) return null;
    return flat(note.textContent);
  }

  it("the chats with unread messages", async () => {
    const said = [];
    for (const n of [0, 1, 2]) said.push(await backlogAt(n, 5));
    expect(said).toEqual([
      expect.stringMatching(/^0 chats have 5 unread messages\./),
      expect.stringMatching(/^1 chat has 5 unread messages\./),
      expect.stringMatching(/^2 chats have 5 unread messages\./),
    ]);
  }, 30_000);

  it("the unread messages", async () => {
    const said = [];
    for (const n of [0, 1, 2]) said.push(await backlogAt(1, n));
    // At 0 the line is not shown; at 1 the same reader finds it.
    expect(said).toEqual([
      null,
      expect.stringMatching(/^1 chat has 1 unread message\./),
      expect.stringMatching(/^1 chat has 2 unread messages\./),
    ]);
  }, 30_000);

  /** The progress line of a running job. */
  async function progressAt(over: Partial<BackfillStatus>): Promise<string> {
    cleanup();
    mount({ backfill: job({ state: "running", calls_spent: 0, ...over }) });
    const progress = await screen.findByTestId("backfill-progress");
    await waitFor(() => expect(hiddenFromReaders(progress)).toBe(false));
    return flat(progress.textContent);
  }

  it.each([
    ["the calls a running job plans", (n: number) => ({ calls_planned: n }),
      ["Reading this chat: 0 of 0 calls,", "Reading this chat: 0 of 1 call,",
        "Reading this chat: 0 of 2 calls,"]],
    ["the notes a running job has written", (n: number) => ({ notes_written: n }),
      [" 0 notes written,", " 1 note written,", " 2 notes written,"]],
    ["the messages a running job has left", (n: number) => ({ messages_left: n }),
      [" 0 messages left.", " 1 message left.", " 2 messages left."]],
  ] as const)("%s", async (_name, over, says) => {
    for (const n of [0, 1, 2] as const) {
      expect(await progressAt(over(n)), `at ${n}`).toContain(says[n]);
    }
  }, 30_000);

  it("the notes an undo would remove, on its confirm", async () => {
    // At 0 there is nothing to undo, so no offer; at 1 and 2 the confirm
    // names the count on both of its buttons, and each button is named by
    // the words it shows (WCAG 2.5.3), so a reader who says them reaches it.
    const user = userEvent.setup();
    for (const n of [0, 1, 2] as const) {
      cleanup();
      const { settled } = mount({ backfill: job({ state: "done", notes_written: n }) });
      await settled();
      await screen.findByTestId("backfill-outcome");
      const offer = screen.queryByRole("button", { name: /^undo this run$/i });
      if (n === 0) {
        expect(offer, "an undo is offered for a run that wrote nothing").toBeNull();
        continue;
      }
      expect(offer, `no undo is offered at ${n}`).not.toBeNull();
      await user.click(offer!);
      const [remove, keep] = [UNDO_RUN_CONFIRM, "Cancel"];
      const confirm = await screen.findByRole("button", { name: remove });
      expect(flat(confirm.textContent), `at ${n}`).toBe(remove);
      expect(screen.getByRole("button", { name: keep }), `at ${n}`).toBeInTheDocument();
    }
  }, 30_000);
});
