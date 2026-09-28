/**
 * A picked range and a note's edit at their edges, each held by behaviour:
 *
 *  - a picked range that waits behind a closed strip is said;
 *  - Cancel lets go of the range AND of the range scope;
 *  - the book button's name and what its press sets agree after both range
 *    fields are cleared;
 *  - a note's edit survives the lock screen's remount.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { hiddenFromReaders } from "@/test/helpers/accessibility";
import { mockFetch } from "../mocks/api";
import { BackfillStrip } from "@/components/notebook/BackfillStrip";
import { NotebookPanel } from "@/components/notebook/NotebookPanel";
import { SCOPE_LABELS, pickWaitingProse } from "@/components/notebook/backfillProse";
import { EDIT_FAILED_WHY, NOTE_ROW_WORDS } from "@/components/notebook/noteProse";
import { EMPTY_RANGE, NOTE_RANGE_WORDS, useNoteRangeStore } from "@/lib/chat/noteRange";
import { useDraftStore } from "@/lib/store/draftStore";
import { useUiStore } from "@/lib/store/uiStore";
import type { Message } from "@/lib/schemas/chats";

const CHAT = 7;
const messages: Message[] = Array.from({ length: 12 }, (_, i) => ({
  id: 103 + i * 3, chat_id: CHAT, role: i % 2 ? "assistant" : "user",
  content: `Message ${i + 1} about the harbour.`, created_at: "2026-09-27 10:00:00",
}) as Message);
const at = (place: number) => messages[place - 1].id;

function stripRoutes() {
  mockFetch({
    [`GET /notebook/extract/plan/${CHAT}`]: { body: {
      ready: true, blocked_by: [], scope: "range", messages_total: 12, messages_unread: 5,
      chunks: 1, calls_planned: 1, daily_cap: 60, backfill_cap: 50, live_reserve: 10,
      calls_spent_today: 3, fits_today: true, prompt_price: 0.06, cost_estimate: 0.001,
      model_id: "vendor/slug", model_is_chat_bind: false } },
    [`GET /chats/${CHAT}/messages`]: { body: messages },
    "/notebook/worker": { body: {
      stats: { done: 0, failed: 0, skipped: 0, skip_reasons: {} },
      spend: { calls: 0, tokens_in: 0, tokens_out: 0, cost: 0 },
      spend_lifetime: { calls: 0, tokens_in: 0, tokens_out: 0, cost: 0 },
      worker: { state: "closed", failures: 0, total_failures: 0, queued: 0, dropped_offers: 0,
        runs: 0, batch_size: 20, backlog: { chats: 0, messages: 0 }, backfill: null },
      daily_cap: 60 } },
  });
}

describe("a picked range and the closed strip", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: CHAT });
    useNoteRangeStore.setState({ byChat: {}, picks: 0 });
  });
  afterEach(() => { cleanup(); vi.restoreAllMocks(); });

  it("says a whole pick is waiting when the strip comes back closed", async () => {
    // Already answered (acked), so the strip does not open by itself: the
    // reader who comes back to this chat meets it closed.
    useNoteRangeStore.setState({ picks: 4, byChat: { [CHAT]: {
      ...EMPTY_RANGE, from: at(3), to: at(7), seq: 4, acked: 4 } } });
    stripRoutes();
    renderWithQueryClient(<BackfillStrip chatId={CHAT} />);
    const line = await screen.findByTestId("backfill-pick-waiting");
    await waitFor(() => expect(line.textContent).toBe(pickWaitingProse(3, 7)));
    expect(hiddenFromReaders(line)).toBe(false);
    expect(line.textContent!.split(/(?<=\.)\s/).every((s) => s.split(/\s+/).length <= 12)).toBe(true);
  });

  it("GROUND: with no pick nothing is said", async () => {
    stripRoutes();
    renderWithQueryClient(<BackfillStrip chatId={CHAT} />);
    await screen.findByRole("button", { name: /read the earlier messages/i });
    expect(hiddenFromReaders(screen.getByTestId("backfill-pick-waiting"))).toBe(true);
  });

  it("Cancel lets go of the pick, and the strip reopens on its default scope", async () => {
    const user = userEvent.setup();
    useNoteRangeStore.setState({ picks: 4, byChat: { [CHAT]: {
      ...EMPTY_RANGE, from: at(3), to: at(7), seq: 4, acked: 4 } } });
    stripRoutes();
    renderWithQueryClient(<BackfillStrip chatId={CHAT} />);
    await user.click(await screen.findByRole("button", { name: /read the earlier messages/i }));
    const preview = await screen.findByTestId("backfill-preview");
    // GROUND: it opened on the picked range.
    expect(within(preview).getByRole("combobox", { name: /what to read/i }).textContent)
      .toContain(SCOPE_LABELS.range);
    await user.click(within(preview).getByRole("button", { name: /^cancel$/i }));
    expect(useNoteRangeStore.getState().byChat[CHAT]).toBeUndefined();
    await user.click(await screen.findByRole("button", { name: /read the earlier messages/i }));
    const again = await screen.findByTestId("backfill-preview");
    expect(within(again).getByRole("combobox", { name: /what to read/i }).textContent)
      .toContain(SCOPE_LABELS.all);
    // No range block with two empty fields.
    expect(hiddenFromReaders(screen.getByTestId("backfill-range"))).toBe(true);
  });
});

describe("the book button after both range fields are cleared", () => {
  beforeEach(() => useNoteRangeStore.setState({ byChat: {}, picks: 0 }));

  it("offers 'from here' and a press sets the start", () => {
    const store = useNoteRangeStore.getState();
    store.pick(CHAT, messages, at(3));
    store.pick(CHAT, messages, at(7));
    // Clear the first field, then the last, as a reader typing would.
    useNoteRangeStore.getState().setEnd(CHAT, "from", null);
    // GROUND: one end cleared, the name follows the cleared end.
    expect(useNoteRangeStore.getState().byChat[CHAT].next).toBe("from");
    useNoteRangeStore.getState().setEnd(CHAT, "to", null);
    const cleared = useNoteRangeStore.getState().byChat[CHAT];
    expect(cleared.next).toBe("from");
    const promised = cleared.next === "from" ? NOTE_RANGE_WORDS.pickFrom : NOTE_RANGE_WORDS.pickTo;
    useNoteRangeStore.getState().pick(CHAT, messages, at(5));
    const after = useNoteRangeStore.getState().byChat[CHAT];
    // What the name promised is what the press did.
    expect(promised).toBe(NOTE_RANGE_WORDS.pickFrom);
    expect(after.from).toBe(at(5));
  });

  it("POSITIVE CONTROL: clearing only the last field offers 'to here'", () => {
    const store = useNoteRangeStore.getState();
    store.pick(CHAT, messages, at(3));
    store.pick(CHAT, messages, at(7));
    useNoteRangeStore.getState().setEnd(CHAT, "to", null);
    expect(useNoteRangeStore.getState().byChat[CHAT].next).toBe("to");
    useNoteRangeStore.getState().pick(CHAT, messages, at(9));
    expect(useNoteRangeStore.getState().byChat[CHAT].to).toBe(at(9));
    expect(useNoteRangeStore.getState().byChat[CHAT].from).toBe(at(3));
  });
});

describe("a note's edit across the lock screen", () => {
  const note = {
    id: 5, chat_id: CHAT, position: 1, kind: "fact", text: "The keeper whistled twice.",
    evidence: null, durability: "story", importance: 3, pinned: 0, retired_at: null,
    superseded_by: null, excluded_reason: null, status: "accepted", provenance: "user",
    source_message_id: null, evidence_role: null, created_at: "2026-09-27 10:00:00",
    updated_at: "2026-09-27 10:00:00", edited_at: null, hand_accepted_at: null,
  };
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: CHAT });
    useDraftStore.getState().clearAll();
    mockFetch({ [`/notebook/${CHAT}`]: { body: { entries: [note], notebook_chars: 20 } } });
  });
  afterEach(() => { cleanup(); useDraftStore.getState().clearAll(); vi.restoreAllMocks(); });

  it("comes back open with the words typed, after a remount", async () => {
    const user = userEvent.setup();
    const first = renderWithQueryClient(<NotebookPanel />);
    await user.click(await screen.findByRole("button", { name: NOTE_ROW_WORDS.edit }));
    const box = screen.getByRole("textbox", { name: NOTE_ROW_WORDS.field });
    await user.type(box, " Then he left.");
    // The lock screen unmounts the whole panel; unlocking mounts it again.
    first.unmount();
    renderWithQueryClient(<NotebookPanel />);
    const back = await screen.findByRole("textbox", { name: NOTE_ROW_WORDS.field });
    expect(back).toHaveValue("The keeper whistled twice. Then he left.");
    // Coming back does not grab the keyboard by itself.
    expect(back).not.toHaveFocus();
    // And the lock's own sentence says what really happens.
    expect(EDIT_FAILED_WHY.vault_locked).toMatch(/kept/i);
  });

  it("GROUND: a cancelled edit does not come back", async () => {
    const user = userEvent.setup();
    const first = renderWithQueryClient(<NotebookPanel />);
    await user.click(await screen.findByRole("button", { name: NOTE_ROW_WORDS.edit }));
    await user.click(screen.getByRole("button", { name: NOTE_ROW_WORDS.cancel }));
    first.unmount();
    renderWithQueryClient(<NotebookPanel />);
    await screen.findByText(note.text);
    expect(screen.queryByRole("textbox", { name: NOTE_ROW_WORDS.field })).toBeNull();
  });
});
