/**
 * A note is edited in place, from the keyboard alone if need be.
 *
 * The pencil swaps the text for a field in the SAME row (no remount), Enter
 * saves through the existing PATCH, Escape cancels and puts the words back,
 * and focus returns to the pencil either way. An empty note cannot be saved
 * and says why; a failed save says so and keeps every word typed. Escape is
 * stopped at the row, because Composer binds Escape at the window to "stop
 * generating".
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { hiddenFromReaders } from "@/test/helpers/accessibility";
import { mockFetch } from "../mocks/api";
import { NotebookPanel } from "@/components/notebook/NotebookPanel";
import {
  EDIT_EMPTY, EDIT_FAILED, EDIT_FAILED_WHY, MODEL_NOTE_WORDS, NOTE_ROW_WORDS,
} from "@/components/notebook/noteProse";
import { useUiStore } from "@/lib/store/uiStore";

const CHAT = 7;

function entry(over: Record<string, unknown> = {}) {
  return {
    id: 1, chat_id: CHAT, position: 0, kind: "fact", text: "Mira is her sister.",
    evidence: null, durability: "permanent", importance: 2, pinned: 0,
    retired_at: null, superseded_by: null, excluded_reason: null,
    status: "accepted", provenance: "user", source_message_id: null,
    created_at: "2026-08-19", updated_at: "2026-08-19", ...over,
  };
}

/** The panel over one note, with a PATCH that answers `patch`. The list
 *  route serves `served`, which the test changes to what the server would
 *  hold after the save, so the refetch shows the saved words. */
function mount(note = entry(), patch: { status?: number; body?: unknown } = {}) {
  const served = { entries: [note], notebook_chars: 20 };
  const fetchMock = mockFetch({
    [`PATCH /notebook/entries/${note.id}`]: {
      status: patch.status ?? 200, body: patch.body ?? note },
    [`/notebook/${CHAT}`]: { body: served },
    "/notebook/worker": { body: {
      stats: { done: 0, failed: 0, skipped: 0, skip_reasons: {} },
      spend: { calls: 0, tokens_in: 0, tokens_out: 0, cost: 0 },
      spend_lifetime: { calls: 0, tokens_in: 0, tokens_out: 0, cost: 0 },
      worker: { state: "closed", failures: 0, total_failures: 0, queued: 0,
        dropped_offers: 0, runs: 0, batch_size: 20,
        backlog: { chats: 0, messages: 0 }, backfill: null },
      daily_cap: 60 } },
  });
  renderWithQueryClient(<NotebookPanel />);
  const patches = () => fetchMock.mock.calls
    .filter((c: unknown[]) => (c[1] as RequestInit | undefined)?.method === "PATCH")
    .map((c: unknown[]) => JSON.parse(String((c[1] as RequestInit).body)));
  return { served, patches };
}

const row = () => screen.getByTestId("note-1");
const pencil = () => within(row()).getByRole("button", { name: NOTE_ROW_WORDS.edit });
const field = () => within(row()).getByRole("textbox", { name: NOTE_ROW_WORDS.field });

describe("editing a note in place", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: CHAT });
  });
  afterEach(() => vi.restoreAllMocks());

  it("keyboard only: pencil, type, Enter saves through PATCH, focus back on the pencil", async () => {
    const user = userEvent.setup();
    const { served, patches } = mount(entry(), {
      body: entry({ text: "Mira is her older sister." }) });
    await screen.findByText("Mira is her sister.");
    const theRow = row();
    const button = pencil();

    button.focus();
    await user.keyboard("{Enter}");
    // The field is in the same row, focused, holding the note's words.
    expect(document.activeElement).toBe(field());
    expect(field()).toHaveValue("Mira is her sister.");
    expect(theRow.contains(field())).toBe(true);
    // The pencil is the same element, now Save.
    expect(button).toHaveAccessibleName(NOTE_ROW_WORDS.save);

    served.entries = [entry({ text: "Mira is her older sister." })];
    await user.keyboard("{Backspace}{Backspace}{Backspace}{Backspace}{Backspace}{Backspace}{Backspace}");
    await user.keyboard("older sister.{Enter}");
    await waitFor(() => expect(patches()).toEqual([{ text: "Mira is her older sister." }]));
    await waitFor(() => expect(within(row()).queryByRole("textbox")).toBeNull());
    await screen.findByText("Mira is her older sister.");
    // No remount, and the keyboard is where it started.
    expect(row()).toBe(theRow);
    expect(document.activeElement).toBe(button);
    expect(button).toHaveAccessibleName(NOTE_ROW_WORDS.edit);
  });

  it("Escape cancels, restores the words, sends nothing and returns focus", async () => {
    const user = userEvent.setup();
    const { patches } = mount();
    await screen.findByText("Mira is her sister.");
    await user.click(pencil());
    await user.type(field(), " Not anymore.");
    await user.keyboard("{Escape}");
    expect(within(row()).queryByRole("textbox")).toBeNull();
    expect(within(row()).getByText("Mira is her sister.")).toBeTruthy();
    expect(document.activeElement).toBe(pencil());
    expect(patches()).toEqual([]);
  });

  it("Escape in the field does not reach the window, where Composer stops a reply", async () => {
    const user = userEvent.setup();
    mount();
    await screen.findByText("Mira is her sister.");
    const atWindow = vi.fn();
    window.addEventListener("keydown", atWindow);
    try {
      // GROUND: an Escape pressed while NOT editing does reach the window,
      // so the listener is live and the absence below means something.
      pencil().focus();
      await user.keyboard("{Escape}");
      expect(atWindow.mock.calls.filter((c) => c[0].key === "Escape")).toHaveLength(1);
      await user.click(pencil());
      await user.keyboard("{Escape}");
      expect(atWindow.mock.calls.filter((c) => c[0].key === "Escape")).toHaveLength(1);
    } finally {
      window.removeEventListener("keydown", atWindow);
    }
  });

  it("an empty note cannot be saved, and the row says why", async () => {
    const user = userEvent.setup();
    const { patches } = mount();
    await screen.findByText("Mira is her sister.");
    await user.click(pencil());
    await user.clear(field());
    const save = within(row()).getByRole("button", { name: NOTE_ROW_WORDS.save });
    expect(save).toBeDisabled();
    expect(save).toHaveAccessibleDescription(EDIT_EMPTY);
    expect(hiddenFromReaders(screen.getByText(EDIT_EMPTY))).toBe(false);
    await user.keyboard("{Enter}");
    expect(patches()).toEqual([]);
    expect(field()).toBeInTheDocument();
    // POSITIVE CONTROL: a word makes it savable and the line goes.
    await user.type(field(), "x");
    expect(save).toBeEnabled();
    expect(hiddenFromReaders(screen.queryByText(EDIT_EMPTY))).toBe(true);
  });

  it("a failed save says so and keeps the draft open", async () => {
    const user = userEvent.setup();
    const { patches } = mount(entry(), { status: 500, body: { detail: "boom" } });
    await screen.findByText("Mira is her sister.");
    await user.click(pencil());
    await user.clear(field());
    await user.type(field(), "Mira moved away.{Enter}");
    await waitFor(() => expect(patches()).toHaveLength(1));
    const alert = await within(row()).findByRole("alert");
    expect(alert.textContent).toBe(EDIT_FAILED);
    expect(field()).toHaveValue("Mira moved away.");
    expect(field()).toHaveAttribute("aria-invalid", "true");
  });

  it("a failed save says why, from the server's reason, in at most 12 words", async () => {
    const user = userEvent.setup();
    mount(entry(), { status: 404, body: { detail: "notebook_entry_not_found" } });
    await screen.findByText("Mira is her sister.");
    await user.click(pencil());
    await user.type(field(), " Still.");
    // Pressed on Save, from the keyboard: Save keeps focus through it.
    await user.tab();
    const save = within(row()).getByRole("button", { name: NOTE_ROW_WORDS.save });
    expect(save).toHaveFocus();
    await user.keyboard("{Enter}");
    const alert = await within(row()).findByRole("alert");
    expect(alert.textContent).toBe(EDIT_FAILED_WHY.notebook_entry_not_found);
    expect(alert.textContent!.split(/\s+/).length).toBeLessThanOrEqual(12);
    expect(field()).toHaveValue("Mira is her sister. Still.");
    expect(save).toHaveFocus();
  });

  it("an unchanged Enter closes without a request", async () => {
    const user = userEvent.setup();
    const { patches } = mount();
    await screen.findByText("Mira is her sister.");
    await user.click(pencil());
    await user.keyboard("{Enter}");
    expect(within(row()).queryByRole("textbox")).toBeNull();
    expect(patches()).toEqual([]);
  });

  it("leaving the field neither saves nor throws the words away", async () => {
    const user = userEvent.setup();
    const { patches } = mount();
    await screen.findByText("Mira is her sister.");
    await user.click(pencil());
    await user.type(field(), " Still here.");
    await user.click(document.body);
    expect(field()).toHaveValue("Mira is her sister. Still here.");
    expect(patches()).toEqual([]);
  });

  it("a model note says it was edited once the store says so, and only then", async () => {
    const user = userEvent.setup();
    const model = entry({ provenance: "model" });
    const saved = { ...model, text: "Mira is older.", edited_at: "2026-09-27 10:00:00" };
    const { served } = mount(model, { body: saved });
    await screen.findByText("Mira is her sister.");
    expect(within(row()).getByText(MODEL_NOTE_WORDS.wrote)).toBeTruthy();
    expect(within(row()).queryByText(MODEL_NOTE_WORDS.edited)).toBeNull();
    // What the server holds after the save: new words AND the mark it
    // writes. The line follows the mark, not the fact that a save happened.
    served.entries = [saved];
    await user.click(pencil());
    await user.clear(field());
    await user.type(field(), "Mira is older.{Enter}");
    await within(row()).findByText(MODEL_NOTE_WORDS.edited);
    expect(within(row()).queryByText(MODEL_NOTE_WORDS.wrote)).toBeNull();
  });

  it("a note the reader wrote gets no model line", async () => {
    mount();
    await screen.findByText("Mira is her sister.");
    expect(within(row()).queryByText(MODEL_NOTE_WORDS.wrote)).toBeNull();
    expect(within(row()).queryByText(MODEL_NOTE_WORDS.edited)).toBeNull();
  });

  it("a note the reader wrote and then edited still gets no model line", async () => {
    // The store sets edited_at on a reader's own note too; only a MODEL
    // note says "Model wrote it, you edited it."
    mount(entry({ edited_at: "2026-09-27 10:05:00" }));
    await screen.findByText("Mira is her sister.");
    expect(within(row()).queryByText(MODEL_NOTE_WORDS.edited)).toBeNull();
    // POSITIVE CONTROL: the same mark on a model note shows the line.
    cleanup();
    mount(entry({ provenance: "model", edited_at: "2026-09-27 10:05:00" }));
    await screen.findByText("Mira is her sister.");
    expect(within(row()).getByText(MODEL_NOTE_WORDS.edited)).toBeTruthy();
  });

  it("the box is one line: Shift+Enter saves like Enter, and a pasted break is a space", async () => {
    const user = userEvent.setup();
    const { patches } = mount(entry(), { body: entry({ text: "Mira is her sister. Tom too." }) });
    await screen.findByText("Mira is her sister.");
    await user.click(pencil());
    await user.paste("\nTom too.");
    expect(field()).toHaveValue("Mira is her sister. Tom too.");
    expect(patches()).toEqual([]);                  // ground: nothing sent yet
    await user.keyboard("{Shift>}{Enter}{/Shift}");
    await waitFor(() => expect(patches()).toEqual([{ text: "Mira is her sister. Tom too." }]));
  });

  it("a suggestion's Keep is out of reach while it is being edited, and back after", async () => {
    const user = userEvent.setup();
    mount(entry({ provenance: "model", status: "proposed" }));
    await screen.findByText("Mira is her sister.");
    // GROUND: Keep is there to begin with.
    expect(within(row()).getByRole("button", { name: /keep suggestion/i })).toBeTruthy();
    await user.click(pencil());
    expect(within(row()).queryByRole("button", { name: /keep suggestion/i })).toBeNull();
    await user.keyboard("{Escape}");
    expect(within(row()).getByRole("button", { name: /keep suggestion/i })).toBeTruthy();
  });
});
