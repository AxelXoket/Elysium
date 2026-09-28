/**
 * The notes range is picked FROM THE CHAT, never typed as an id.
 *
 * The real bubbles and the real strip, side by side, over one fake server.
 * The fake server's plan route counts the way the backend does (active rows
 * of the chat whose id lies between the two it was sent), so "the preview
 * shows the right count" is a claim about what the strip SENT, not about a
 * number the mock was told to say. The fixture's ids are not its positions
 * (103, 106, ... 136), so an id shown as a place, or a place sent as an id,
 * reads as a wrong number.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { hiddenFromReaders, readableText } from "@/test/helpers/accessibility";
import { mockFetch } from "../mocks/api";
import { MessageBubble } from "@/components/chat/MessageBubble";
import { BackfillStrip } from "@/components/notebook/BackfillStrip";
import {
  RANGE_WORDS,
  SCOPE_LABELS,
  noSuchMessage,
  planProse,
  rangeSummary,
} from "@/components/notebook/backfillProse";
import { EXCERPT_MAX, NOTE_RANGE_WORDS, excerpt } from "@/lib/chat/noteRange";
import { SETTING_RETRY } from "@/components/notebook/noteProse";
import { NotesTab } from "@/components/notebook/NotesTab";
import { useNotesViewStore } from "@/components/notebook/notesTabState";
import { useUiStore } from "@/lib/store/uiStore";
import type { Message } from "@/lib/schemas/chats";
import type { BackfillPlan } from "@/lib/schemas/notebook";

const CHAT = 7;
const OTHER = 8;

function rows(chatId: number, count: number, firstId: number): Message[] {
  return Array.from({ length: count }, (_, i) => ({
    id: firstId + i * 3,
    chat_id: chatId,
    role: i % 2 === 0 ? "user" : "assistant",
    content: `Line ${i + 1}: the keeper climbed the stairs again and counted every step out loud to the gulls.`,
    created_at: "2026-09-27 10:00:00",
    attachments: [], variant_group: null, active: true,
    variant_index: 0, variant_count: 1,
  }) as Message);
}

const SEVEN = rows(CHAT, 12, 103);
const EIGHT = rows(OTHER, 4, 900);

function plan(over: Partial<BackfillPlan>): BackfillPlan {
  return {
    ready: true, blocked_by: [], scope: "range",
    messages_total: 12, messages_unread: 12, chunks: 1, calls_planned: 1,
    daily_cap: 60, backfill_cap: 50, live_reserve: 10, calls_spent_today: 0,
    fits_today: true, prompt_price: 0.06, cost_estimate: 0.001,
    model_id: "vendor/slug", model_is_chat_bind: false, ...over,
  };
}

const WORKER = {
  stats: { done: 0, failed: 0, skipped: 0, skip_reasons: {} },
  spend: { calls: 0, tokens_in: 0, tokens_out: 0, cost: 0 },
  spend_lifetime: { calls: 0, tokens_in: 0, tokens_out: 0, cost: 0 },
  worker: {
    state: "closed", failures: 0, total_failures: 0, queued: 0,
    dropped_offers: 0, runs: 0, batch_size: 20,
    backlog: { chats: 0, messages: 0 }, backfill: null,
  },
  daily_cap: 60,
};

/** Every plan request the strip sent, as the two ids it carried. */
let planAsks: { from: number; to: number }[] = [];

function serve() {
  planAsks = [];
  const base = mockFetch({
    [`GET /chats/${CHAT}/messages`]: { body: SEVEN },
    [`GET /chats/${OTHER}/messages`]: { body: EIGHT },
    "/notebook/worker": { body: WORKER },
  });
  // The plan route, counting like the backend: active rows of the chat
  // with `id BETWEEN from AND to` (notebook_worker._plan_backfill).
  vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const m = url.match(/\/notebook\/extract\/plan\/(\d+)\?(.*)$/);
    if (m) {
      const q = new URLSearchParams(m[2]);
      const from = Number(q.get("from_message_id"));
      const to = Number(q.get("to_message_id"));
      if (q.get("scope") === "range") planAsks.push({ from, to });
      const chat = Number(m[1]) === CHAT ? SEVEN : EIGHT;
      const count = chat.filter((r) => r.id >= from && r.id <= to).length;
      return new Response(JSON.stringify(plan({ messages_unread: count })), {
        status: 200, headers: { "Content-Type": "application/json" } });
    }
    return base(input, init);
  });
}

/** One chat's bubbles and the strip, the way the app lays them out. */
function Screen({ strip = CHAT, chat = CHAT }: { strip?: number; chat?: number }) {
  const list = chat === CHAT ? SEVEN : EIGHT;
  return (
    <div>
      <div data-testid="chat">
        {list.map((m) => (
          <MessageBubble key={m.id} chatId={chat} message={m} messages={list} />
        ))}
      </div>
      <BackfillStrip chatId={strip} />
    </div>
  );
}

/** Chat 7's bubbles alone, the strip unmounted: the right panel on another
 *  tab. */
function ChatOnly() {
  return (
    <div>
      <div data-testid="chat">
        {SEVEN.map((m) => (
          <MessageBubble key={m.id} chatId={CHAT} message={m} messages={SEVEN} />
        ))}
      </div>
    </div>
  );
}

/** The bubble at a 1-based place in the chat. */
function bubble(place: number): HTMLElement {
  return within(screen.getByTestId("chat")).getAllByRole("article")[place - 1];
}

function rangeButton(place: number): HTMLElement {
  return within(bubble(place)).getByRole("button", {
    name: new RegExp(`^(${NOTE_RANGE_WORDS.pickFrom}|${NOTE_RANGE_WORDS.pickTo})$`),
  });
}

const first = () => screen.getByLabelText(RANGE_WORDS.firstLabel) as HTMLInputElement;
const last = () => screen.getByLabelText(RANGE_WORDS.lastLabel) as HTMLInputElement;
const said = () => screen.getByTestId("backfill-range-said");

describe("picking the notes range from the chat", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({
      selectedChatId: CHAT, activeRightPanelTab: "models", rightPanelCollapsed: true,
    });
    useNotesViewStore.setState({ view: "notes", focusTarget: null });
    serve();
  });
  afterEach(() => vi.restoreAllMocks());

  it("GROUND: before any pick the strip is shut and no message is marked", async () => {
    renderWithQueryClient(<Screen />);
    await screen.findByRole("button", { name: /read the earlier messages/i });
    expect(hiddenFromReaders(screen.getByTestId("backfill-preview"))).toBe(true);
    expect(screen.queryAllByTestId("note-range-mark")).toHaveLength(0);
    // Every bubble offers the start, and none offers the end yet.
    expect(within(screen.getByTestId("chat"))
      .getAllByRole("button", { name: NOTE_RANGE_WORDS.pickFrom })).toHaveLength(12);
    expect(within(screen.getByTestId("chat"))
      .queryAllByRole("button", { name: NOTE_RANGE_WORDS.pickTo })).toHaveLength(0);
  });

  it("two presses in the chat set the range, and the preview counts exactly it", async () => {
    const user = userEvent.setup();
    renderWithQueryClient(<Screen />);

    await user.click(rangeButton(3));
    // One press already names a range: from here to the end.
    await waitFor(() => expect(first().value).toBe("3"));
    expect(last().value).toBe("12");
    expect(said().textContent).toBe(rangeSummary(3, 12, 12, false));
    expect(hiddenFromReaders(screen.getByTestId("backfill-preview"))).toBe(false);
    await waitFor(() => expect(screen.getByTestId("backfill-plan").textContent)
      .toBe(planProse(plan({ messages_unread: 10 }))));

    // The press made the notes tab come forward, panel open.
    expect(useUiStore.getState().activeRightPanelTab).toBe("notebook");
    expect(useUiStore.getState().rightPanelCollapsed).toBe(false);

    // The same button now offers the other end, on every message.
    expect(within(screen.getByTestId("chat"))
      .getAllByRole("button", { name: NOTE_RANGE_WORDS.pickTo })).toHaveLength(12);
    await user.click(rangeButton(7));
    await waitFor(() => expect(last().value).toBe("7"));
    expect(said().textContent).toBe(rangeSummary(3, 7, 12, false));
    await waitFor(() => expect(screen.getByTestId("backfill-plan").textContent)
      .toBe(planProse(plan({ messages_unread: 5 }))));
    expect(planAsks.at(-1)).toEqual({ from: 109, to: 121 });

    // In the chat: the two messages say they are the ends, in words.
    expect(within(bubble(3)).getByTestId("note-range-mark").textContent)
      .toBe(NOTE_RANGE_WORDS.markFrom);
    expect(within(bubble(7)).getByTestId("note-range-mark").textContent)
      .toBe(NOTE_RANGE_WORDS.markTo);
    expect(screen.getAllByTestId("note-range-mark")).toHaveLength(2);
  });

  it("a pick made while the tab shows another view brings the Notes view forward", async () => {
    // The strip lives in the tab's Notes view. A pick made while the
    // reader was looking at Limits must not land in a hidden view.
    useNotesViewStore.setState({ view: "limits", focusTarget: null });
    const user = userEvent.setup();
    // The tab's own three views, with the chat beside them.
    renderWithQueryClient(
      <div>
        <div data-testid="chat">
          {SEVEN.map((m) => (
            <MessageBubble key={m.id} chatId={CHAT} message={m} messages={SEVEN} />
          ))}
        </div>
        <NotesTab />
      </div>,
    );
    // GROUND: the strip is out of reach while Limits is shown.
    expect(hiddenFromReaders(await screen.findByTestId("backfill-strip"))).toBe(true);
    await user.click(rangeButton(3));
    await waitFor(() => expect(useNotesViewStore.getState().view).toBe("notes"));
    await waitFor(() => expect(hiddenFromReaders(screen.getByTestId("backfill-preview"))).toBe(false));
  });

  it("shows each end as its place and first words, and never an id", async () => {
    const user = userEvent.setup();
    renderWithQueryClient(<Screen />);
    await user.click(rangeButton(3));
    await user.click(rangeButton(7));
    await waitFor(() => expect(last().value).toBe("7"));

    const words = screen.getByTestId("backfill-range-from-words").textContent ?? "";
    expect(words).toBe(`"${excerpt(SEVEN[2].content)}"`);
    expect(words.length - 2).toBeLessThanOrEqual(EXCERPT_MAX);
    // POSITIVE CONTROL for the cut: this message is longer than the limit.
    expect(SEVEN[2].content.length).toBeGreaterThan(EXCERPT_MAX);
    expect(screen.getByTestId("backfill-range").textContent).toContain("of 12");

    // No id anywhere a reader can see: not in the strip, not in the chat.
    const seen = readableText(document.body);
    const ids = SEVEN.map((r) => String(r.id));
    expect(ids.filter((id) => new RegExp(`\\b${id}\\b`).test(seen))).toEqual([]);
    // GROUND for that search: it does find a number that IS on screen.
    expect(/\b12\b/.test(seen)).toBe(true);
  });

  it("works across the whole chat, places 1 to 12", async () => {
    const user = userEvent.setup();
    renderWithQueryClient(<Screen />);
    await user.click(rangeButton(1));
    await user.click(rangeButton(12));
    await waitFor(() => expect(said().textContent).toBe(rangeSummary(1, 12, 12, false)));
    await waitFor(() => expect(planAsks.at(-1)).toEqual({ from: 103, to: 136 }));
    await waitFor(() => expect(screen.getByTestId("backfill-plan").textContent)
      .toBe(planProse(plan({ messages_unread: 12 }))));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /start reading/i })).toBeEnabled());
  });

  it("an end picked ABOVE the start is swapped and said, never a backwards range", async () => {
    const user = userEvent.setup();
    renderWithQueryClient(<Screen />);
    await user.click(rangeButton(8));
    await user.click(rangeButton(5));
    await waitFor(() => expect(first().value).toBe("5"));
    expect(last().value).toBe("8");
    expect(said().textContent).toBe(rangeSummary(5, 8, 12, true));
    // POSITIVE CONTROL: the words are not the plain summary.
    expect(said().textContent).not.toBe(rangeSummary(5, 8, 12, false));
    await waitFor(() => expect(planAsks.at(-1)).toEqual({ from: 115, to: 124 }));
    expect(planAsks.every((a) => a.from <= a.to)).toBe(true);
    expect(within(bubble(5)).getByTestId("note-range-mark").textContent)
      .toBe(NOTE_RANGE_WORDS.markFrom);
    expect(within(bubble(8)).getByTestId("note-range-mark").textContent)
      .toBe(NOTE_RANGE_WORDS.markTo);
  });

  it("leaves the keyboard on the message button it pressed", async () => {
    const user = userEvent.setup();
    renderWithQueryClient(<Screen />);
    const button = rangeButton(4);
    button.focus();
    await user.keyboard("{Enter}");
    await waitFor(() => expect(first().value).toBe("4"));
    expect(document.activeElement).toBe(button);
    // Same element, its name swapped to the next step.
    expect(button).toHaveAccessibleName(NOTE_RANGE_WORDS.pickTo);
  });

  it("a pick in another chat never reaches this chat's strip", async () => {
    const user = userEvent.setup();
    // The strip is chat 7's; the bubbles on screen are chat 8's.
    renderWithQueryClient(<Screen strip={CHAT} chat={OTHER} />);
    await screen.findByRole("button", { name: /read the earlier messages/i });
    await user.click(rangeButton(2));
    // Chat 8's own bubble is marked, so the press did land somewhere...
    expect(within(bubble(2)).getByTestId("note-range-mark")).toBeTruthy();
    // ...and chat 7's strip did not open or take it.
    expect(hiddenFromReaders(screen.getByTestId("backfill-preview"))).toBe(true);
    expect(planAsks).toHaveLength(0);
  });

  it("keeps a half-picked range with its own chat, and never shows it in another", async () => {
    const user = userEvent.setup();
    const { rerender } = renderWithQueryClient(<Screen />);
    await user.click(rangeButton(3));
    await waitFor(() => expect(first().value).toBe("3"));
    rerender(<Screen strip={OTHER} chat={OTHER} />);
    // In chat 8: nothing of chat 7's, marks or fields.
    await waitFor(() => expect(screen.queryAllByTestId("note-range-mark")).toHaveLength(0));
    expect(within(screen.getByTestId("chat"))
      .getAllByRole("button", { name: NOTE_RANGE_WORDS.pickFrom })).toHaveLength(4);
    rerender(<Screen />);
    // Back in chat 7: the pick is where it was, and the next press ends it.
    await waitFor(() => expect(screen.getAllByTestId("note-range-mark")).toHaveLength(2));
    expect(within(bubble(3)).getByTestId("note-range-mark").textContent)
      .toBe(NOTE_RANGE_WORDS.markFrom);
    expect(within(screen.getByTestId("chat"))
      .getAllByRole("button", { name: NOTE_RANGE_WORDS.pickTo })).toHaveLength(12);
  });

  it("does not open the preview again for a pick it already opened for", async () => {
    const user = userEvent.setup();
    const { rerender } = renderWithQueryClient(<Screen />);
    await user.click(rangeButton(3));
    await waitFor(() => expect(first().value).toBe("3"));
    // The reader closes the preview and leaves the tab (the strip unmounts)...
    await user.click(screen.getByRole("button", { name: /^cancel$/i }));
    await user.click(rangeButton(5));
    await waitFor(() => expect(first().value).toBe("5"));
    rerender(<ChatOnly />);
    rerender(<Screen />);
    // ...and on coming back the strip is as they left it: shut. GROUND for
    // "shut": the same strip is open right after a fresh pick (above).
    await screen.findByRole("button", { name: /read the earlier messages/i });
    expect(hiddenFromReaders(screen.getByTestId("backfill-preview"))).toBe(true);
  });

  it("Cancel lets go of the range and its marks", async () => {
    const user = userEvent.setup();
    renderWithQueryClient(<Screen />);
    await user.click(rangeButton(3));
    await waitFor(() => expect(screen.getAllByTestId("note-range-mark")).toHaveLength(2));
    await user.click(screen.getByRole("button", { name: /^cancel$/i }));
    await waitFor(() => expect(screen.queryAllByTestId("note-range-mark")).toHaveLength(0));
  });
});

describe("the range typed as places, and Start's reasons", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: CHAT });
    serve();
  });
  afterEach(() => vi.restoreAllMocks());

  async function openRange(user: ReturnType<typeof userEvent.setup>) {
    await user.click(await screen.findByRole("button", { name: /read the earlier messages/i }));
    await user.click(screen.getByRole("combobox", { name: /what to read/i }));
    await user.click(await screen.findByRole("option", { name: /a range of messages/i }));
  }

  it("an empty range says why Start is off, and Start points at that line", async () => {
    const user = userEvent.setup();
    renderWithQueryClient(<Screen />);
    await openRange(user);
    const start = screen.getByRole("button", { name: /start reading/i });
    await waitFor(() => expect(said().textContent).toBe(RANGE_WORDS.pick));
    expect(start).toBeDisabled();
    expect(start).toHaveAccessibleDescription(RANGE_WORDS.pick);
    expect(hiddenFromReaders(said())).toBe(false);
  });

  it("a place past the end is named, with the chat's size", async () => {
    const user = userEvent.setup({ delay: null });
    renderWithQueryClient(<Screen />);
    await openRange(user);
    await user.type(first(), "13");
    await waitFor(() => expect(said().textContent).toBe(noSuchMessage(12)));
    expect(screen.getByRole("button", { name: /start reading/i }))
      .toHaveAccessibleDescription(noSuchMessage(12));
    // The "1" on the way to "13" was a real place and was asked about; the
    // preview it got does not stay up under a number that is not one.
    expect(hiddenFromReaders(screen.queryByTestId("backfill-plan"))).toBe(true);
    // POSITIVE CONTROL: 12 is in the chat, and it is taken.
    await user.clear(first());
    await user.type(first(), "12");
    await waitFor(() => expect(said().textContent).toBe(rangeSummary(12, 12, 12, false)));
  });

  it("typed places in the wrong order say so and send nothing", async () => {
    const user = userEvent.setup({ delay: null });
    renderWithQueryClient(<Screen />);
    await openRange(user);
    await user.type(first(), "9");
    await user.clear(last());
    await user.type(last(), "4");
    await waitFor(() => expect(said().textContent).toBe(RANGE_WORDS.order));
    expect(screen.getByRole("button", { name: /start reading/i })).toBeDisabled();
    expect(planAsks.filter((a) => a.from > a.to)).toEqual([]);
  });
});

describe("the message button itself", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: CHAT, selectedModelId: "vendor/slug" });
    serve();
  });
  afterEach(() => vi.restoreAllMocks());

  it("sits in the message's own action row, as one of its buttons, named by its tooltip", () => {
    renderWithQueryClient(<Screen />);
    const button = rangeButton(2);
    const row = within(bubble(2)).getByLabelText("Message actions");
    expect(row.contains(button)).toBe(true);
    // The family the row's other buttons are drawn from (Copy is one).
    const copy = within(row).getByRole("button", { name: /copy/i });
    expect(button.classList.contains(copy.className)).toBe(true);
    expect(button.getAttribute("title")).toBe(button.getAttribute("aria-label"));
    const icon = button.querySelector("svg")!;
    expect(icon.getAttribute("width")).toBe("13");
    expect(icon.getAttribute("aria-hidden")).toBe("true");
    // One button per message, not two.
    expect(within(row).getAllByRole("button", {
      name: new RegExp(`${NOTE_RANGE_WORDS.pickFrom}|${NOTE_RANGE_WORDS.pickTo}`),
    })).toHaveLength(1);
  });

  it("is not charged to the bubble's size: data-actions counts the first line only", () => {
    renderWithQueryClient(<Screen />);
    // `data-actions` sizes the reserve on the right of the words and
    // the short-bubble minimum width. The book sits on a second line of the
    // row, inside that reserve, and is NOT counted, so no bubble changes
    // size group for it (widths measured in Edge). A user bubble
    // with no edit handler draws Copy and Delete on the first line.
    for (const place of [1, 2]) {
      const shell = bubble(place);
      const row = within(shell).getByLabelText("Message actions");
      const firstLine = [...row.querySelectorAll("button")]
        .filter((b) => !b.classList.contains("is-second-row"));
      const book = row.querySelectorAll("button.is-second-row");
      expect(book, "the row did not draw the range button").toHaveLength(1);
      expect(Number(shell.dataset.actions)).toBeGreaterThanOrEqual(firstLine.length);
      expect(Number(shell.dataset.actions), "the book was counted")
        .toBe(firstLine.length + (place % 2 === 0 ? 1 : 0));
    }
  });

  it("steps aside while the message is being rewritten", async () => {
    const user = userEvent.setup();
    const list = SEVEN;
    renderWithQueryClient(
      <MessageBubble chatId={CHAT} message={list[0]} messages={list} onEditMessage={vi.fn()} />);
    // GROUND: it is there before the edit starts.
    expect(screen.getByRole("button", { name: NOTE_RANGE_WORDS.pickFrom })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: /^edit message$/i }));
    expect(screen.queryByRole("button", { name: NOTE_RANGE_WORDS.pickFrom })).toBeNull();
  });
});

describe("one end set: the book fills the other end", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: CHAT });
    serve();
  });
  afterEach(() => vi.restoreAllMocks());

  it("last cleared: the hint asks for the last, the book sets the last", async () => {
    const user = userEvent.setup();
    renderWithQueryClient(<Screen />);
    await user.click(rangeButton(5));
    await user.click(rangeButton(7));
    await waitFor(() => expect(last().value).toBe("7"));
    await user.clear(last());
    await waitFor(() => expect(said().textContent).toBe(RANGE_WORDS.pickLast));
    // The message button offers what the hint asks for.
    expect(rangeButton(9)).toHaveAccessibleName(NOTE_RANGE_WORDS.pickTo);
    await user.click(rangeButton(9));
    await waitFor(() => expect(said().textContent).toBe(rangeSummary(5, 9, 12, false)));
  });

  it("first cleared with the last typed: the book sets the first and keeps the last", async () => {
    const user = userEvent.setup();
    renderWithQueryClient(<Screen />);
    await user.click(rangeButton(2));
    await waitFor(() => expect(first().value).toBe("2"));
    await user.clear(last());
    await user.type(last(), "8");
    await user.clear(first());
    await waitFor(() => expect(said().textContent).toBe(RANGE_WORDS.pickFirst));
    expect(rangeButton(3)).toHaveAccessibleName(NOTE_RANGE_WORDS.pickFrom);
    await user.click(rangeButton(3));
    await waitFor(() => expect(said().textContent).toBe(rangeSummary(3, 8, 12, false)));
  });
});

describe("the marks and the strip agree", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: CHAT });
    serve();
  });
  afterEach(() => vi.restoreAllMocks());

  it("after a tab switch the strip opens on the marked range, and asks for it", async () => {
    const user = userEvent.setup();
    const { rerender } = renderWithQueryClient(<Screen />);
    await user.click(rangeButton(3));
    await user.click(rangeButton(7));
    await waitFor(() => expect(last().value).toBe("7"));
    rerender(<ChatOnly />);
    rerender(<Screen />);
    await user.click(await screen.findByRole("button", { name: /read the earlier messages/i }));
    await waitFor(() => expect(first().value).toBe("3"));
    expect(last().value).toBe("7");
    expect(screen.getByRole("combobox", { name: /what to read/i }).textContent)
      .toContain(SCOPE_LABELS.range);
    await waitFor(() => expect(planAsks.at(-1)).toEqual({ from: 109, to: 121 }));
    expect(screen.getAllByTestId("note-range-mark")).toHaveLength(2);
  });

  it("after a trip to another chat the strip opens on the marked range", async () => {
    const user = userEvent.setup();
    const { rerender } = renderWithQueryClient(<Screen />);
    await user.click(rangeButton(3));
    await user.click(rangeButton(7));
    await waitFor(() => expect(last().value).toBe("7"));
    rerender(<Screen strip={OTHER} chat={OTHER} />);
    await waitFor(() => expect(screen.queryAllByTestId("note-range-mark")).toHaveLength(0));
    rerender(<Screen />);
    await waitFor(() => expect(screen.getAllByTestId("note-range-mark")).toHaveLength(2));
    await user.click(await screen.findByRole("button", { name: /read the earlier messages/i }));
    await waitFor(() => expect(screen.getByRole("combobox", { name: /what to read/i }).textContent)
      .toContain(SCOPE_LABELS.range));
    expect(hiddenFromReaders(screen.getByTestId("backfill-range"))).toBe(false);
    expect(first().value).toBe("3");
    expect(last().value).toBe("7");
    await waitFor(() => expect(planAsks.at(-1)).toEqual({ from: 109, to: 121 }));
  });

  it("choosing another scope lets go of the marks too", async () => {
    const user = userEvent.setup();
    renderWithQueryClient(<Screen />);
    await user.click(rangeButton(3));
    await user.click(rangeButton(7));
    await waitFor(() => expect(screen.getAllByTestId("note-range-mark")).toHaveLength(2));
    await user.click(screen.getByRole("combobox", { name: /what to read/i }));
    await user.click(await screen.findByRole("option", { name: SCOPE_LABELS.all }));
    await waitFor(() => expect(screen.queryAllByTestId("note-range-mark")).toHaveLength(0));
  });
});

describe("a chat whose messages change between the pick and the run", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: CHAT });
  });
  afterEach(() => vi.restoreAllMocks());

  /** Chat 7 served from a list the test can change; the plan route counts
   *  like the backend and REFUSES a start below the lowest active id. */
  function serveList(list: { rows: Message[] }) {
    planAsks = [];
    const base = mockFetch({
      [`GET /chats/${CHAT}/messages`]: { get body() { return list.rows; } },
      "/notebook/worker": { body: WORKER },
    });
    vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const m = url.match(/\/notebook\/extract\/plan\/(\d+)\?(.*)$/);
      if (m) {
        const q = new URLSearchParams(m[2]);
        const from = Number(q.get("from_message_id"));
        const to = Number(q.get("to_message_id"));
        planAsks.push({ from, to });
        const active = list.rows.filter((r) => r.active !== false);
        const lo = Math.min(...active.map((r) => r.id));
        if (from < lo) {
          return new Response(JSON.stringify({ detail: "notebook_range_invalid" }), {
            status: 400, headers: { "Content-Type": "application/json" } });
        }
        const count = active.filter((r) => r.id >= from && r.id <= to).length;
        return new Response(JSON.stringify(plan({ messages_unread: count })), {
          status: 200, headers: { "Content-Type": "application/json" } });
      }
      return base(input, init);
    });
  }

  function Live({ list }: { list: { rows: Message[] } }) {
    return (
      <div>
        <div data-testid="chat">
          {list.rows.filter((m) => m.active !== false).map((m) => (
            <MessageBubble key={m.id} chatId={CHAT} message={m} messages={list.rows} />
          ))}
        </div>
        <BackfillStrip chatId={CHAT} />
      </div>
    );
  }

  it("message 1 of a chat with a re-rolled greeting is accepted", async () => {
    const user = userEvent.setup();
    const greeting = { ...SEVEN[1], id: 101, variant_group: 101, active: false } as Message;
    const reroll = { ...SEVEN[1], id: 102, variant_group: 101, active: true,
      variant_index: 1 } as Message;
    const list = { rows: [greeting, reroll, ...SEVEN.slice(2, 6)] };
    serveList(list);
    renderWithQueryClient(<Live list={list} />);
    await user.click(rangeButton(1));
    await waitFor(() => expect(first().value).toBe("1"));
    await waitFor(() => expect(planAsks.length).toBeGreaterThan(0));
    // The start sent is the ACTIVE row, which the server accepts.
    expect(planAsks.at(-1)!.from).toBe(102);
    await waitFor(() => expect(screen.getByTestId("backfill-plan").textContent)
      .toBe(planProse(plan({ messages_unread: 5 }))));
  });

  it("a picked message deleted before the run leaves no stale id to send", async () => {
    const user = userEvent.setup();
    const list = { rows: SEVEN.slice(0, 6) };
    serveList(list);
    const qc = renderWithQueryClient(<Live list={list} />);
    await user.click(rangeButton(2));
    await user.click(rangeButton(4));
    await waitFor(() => expect(last().value).toBe("4"));
    const asked = planAsks.length;
    // Message 4 goes away before Start (a delete elsewhere), and the list
    // is read again.
    list.rows = list.rows.filter((r) => r.id !== SEVEN[3].id);
    await act(async () => {
      await qc.queryClient.invalidateQueries({
        predicate: (q) => JSON.stringify(q.queryKey).includes("messages") });
    });
    await waitFor(() => expect(said().textContent).toBe(RANGE_WORDS.pickLast));
    expect(screen.getByRole("button", { name: /start reading/i })).toBeDisabled();
    // Nothing was asked with the deleted id.
    expect(planAsks.slice(asked).some((a) => a.to === SEVEN[3].id)).toBe(false);
  });
});

describe("the range while the chat's messages cannot be read", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: CHAT });
  });
  afterEach(() => vi.restoreAllMocks());

  it("says so in words, never 'from 1 to 0', and Try again hands the keyboard to the field", async () => {
    const user = userEvent.setup();
    const state = { failing: true };
    mockFetch({
      [`GET /chats/${CHAT}/messages`]: {
        get status() { return state.failing ? 500 : 200; },
        get body() { return state.failing ? { detail: "internal_error" } : SEVEN; },
      },
      "/notebook/worker": { body: WORKER },
    });
    renderWithQueryClient(<BackfillStrip chatId={CHAT} />);
    await user.click(await screen.findByRole("button", { name: /read the earlier messages/i }));
    await user.click(screen.getByRole("combobox", { name: /what to read/i }));
    await user.click(await screen.findByRole("option", { name: /a range of messages/i }));
    await user.type(first(), "3");
    await waitFor(() => expect(said().textContent).toBe(RANGE_WORDS.unread));
    expect(said().textContent).not.toMatch(/from 1 to 0/);
    const retry = screen.getByRole("button", { name: SETTING_RETRY });
    state.failing = false;
    retry.focus();
    await user.keyboard("{Enter}");
    await waitFor(() => expect(first()).toHaveFocus());
    expect(screen.queryByRole("button", { name: SETTING_RETRY })).toBeNull();
  });
});

describe("the icon, the excerpt ink and the one-message mark", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({
      selectedChatId: CHAT, activeRightPanelTab: "models", rightPanelCollapsed: true,
    });
    serve();
  });
  afterEach(() => vi.restoreAllMocks());

  it("the message button is the book the strip's Read button carries", async () => {
    renderWithQueryClient(<Screen />);
    const read = await screen.findByRole("button", { name: /read the earlier messages/i });
    const book = read.querySelector("svg")?.getAttribute("class") ?? "";
    // GROUND: the strip's own button is the book.
    expect(book).toContain("lucide-book-open");
    for (const place of [1, 2, 12]) {
      expect(rangeButton(place).querySelector("svg")?.getAttribute("class"))
        .toContain("lucide-book-open");
    }
    // The hint names that icon in words, so the two can be matched.
    expect(RANGE_WORDS.pick).toContain("book icon");
  });

  it("the excerpt is in the panel's ink, not the muted one", async () => {
    const user = userEvent.setup();
    renderWithQueryClient(<Screen />);
    await user.click(rangeButton(3));
    const words = await screen.findByTestId("backfill-range-from-words");
    expect(words.style.color).toBe("var(--color-es-text-light)");
    expect(words).not.toHaveClass("text-muted-foreground");
  });

  it("a range of one message marks it as that message only", async () => {
    const user = userEvent.setup();
    renderWithQueryClient(<Screen />);
    await user.click(rangeButton(5));
    await user.click(rangeButton(5));
    await waitFor(() => expect(within(bubble(5)).getByTestId("note-range-mark").textContent)
      .toBe(NOTE_RANGE_WORDS.markOnly));
  });
});
