/**
 * A note's row, the parts beyond the edit itself
 * (NoteEditInPlace.test.tsx has that):
 *
 *   - Keep and Discard differ in shape AND name, Discard is one press,
 *         and the delete question is Trash + words, never a Check;
 *   - while a note is edited its Pin is hidden but keeps its place and
 *         is no Tab stop, and Save and Cancel carry tooltips;
 *         the "you edited it" line follows the stored mark (`edited_at`),
 *         so it survives a restart and a pin or a drag never sets it;
 *   - a note the reader kept by hand (`hand_accepted_at`) or edited
 *         (`edited_at`) is not announced again by the just-saved strip.
 *
 * Every request is read off the network boundary; every absence has a
 * ground beside it that shows the same thing present.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { hiddenFromReaders } from "@/test/helpers/accessibility";
import { NotebookPanel } from "@/components/notebook/NotebookPanel";
import { MODEL_NOTE_WORDS, NOTE_ROW_WORDS } from "@/components/notebook/noteProse";
import { useUiStore } from "@/lib/store/uiStore";
import { useSeenNotesStore } from "@/lib/chat/seenNotes";
import { useContextNotesStore } from "@/lib/chat/contextNotes";
import { mockFetch } from "../mocks/api";
import { sentinelLog } from "@/test/sentinels";

const CHAT = 7;

function entry(over: Record<string, unknown> = {}) {
  return {
    id: 1, chat_id: CHAT, position: 0, kind: "fact", text: "Mira is her sister.",
    evidence: null, durability: "permanent", importance: 2, pinned: 0,
    retired_at: null, superseded_by: null, excluded_reason: null,
    status: "accepted", provenance: "user", source_message_id: null,
    created_at: "2026-09-27 10:00:00", updated_at: "2026-09-27 10:00:00",
    edited_at: null as string | null, hand_accepted_at: null as string | null,
    ...over,
  };
}

type Route = { status?: number; body: unknown };

function mount(rows: ReturnType<typeof entry>[], extra: Record<string, Route> = {}) {
  const served = { entries: rows, notebook_chars: 10 };
  const fetchMock = mockFetch({
    ...extra,
    [`/notebook/${CHAT}`]: { get body() { return served; } },
    "/notebook/worker": { body: {
      stats: { done: 0, failed: 0, skipped: 0, skip_reasons: {} },
      spend: { calls: 0, tokens_in: 0, tokens_out: 0, cost: 0 },
      spend_lifetime: { calls: 0, tokens_in: 0, tokens_out: 0, cost: 0 },
      worker: { state: "closed", failures: 0, total_failures: 0, queued: 0,
        dropped_offers: 0, runs: 0, batch_size: 20,
        backlog: { chats: 0, messages: 0 }, backfill: null },
      daily_cap: 60,
    } },
    [`/chats/${CHAT}/messages`]: { body: [] },
  });
  const view = renderWithQueryClient(<NotebookPanel />);
  const sent = (method: string) => fetchMock.mock.calls
    .filter((c: unknown[]) => (c[1] as RequestInit | undefined)?.method === method)
    .map((c: unknown[]) => String(c[0]));
  return { ...view, served, sent };
}

/** The lucide glyph a button draws, by its class ("lucide-check" ...). */
function glyph(button: HTMLElement): string {
  const cls = button.querySelector("svg")?.getAttribute("class") ?? "";
  return cls.split(/\s+/).find((c) => c.startsWith("lucide-")) ?? "";
}

function reset() {
  vi.restoreAllMocks();
  useUiStore.setState({ selectedChatId: CHAT });
  useSeenNotesStore.setState({ byChat: {} });
  useContextNotesStore.setState({ byChat: {} });
}

const row = (id = 1) => screen.getByTestId(`note-${id}`);

describe("keep and discard a suggestion", () => {
  beforeEach(reset);
  afterEach(() => { cleanup(); vi.restoreAllMocks(); });

  it("the two differ in shape, name and tooltip", async () => {
    mount([entry({ provenance: "model", status: "proposed" })]);
    const keep = await screen.findByRole("button", { name: NOTE_ROW_WORDS.keep });
    const discard = within(row()).getByRole("button", { name: NOTE_ROW_WORDS.discard });
    expect(glyph(keep)).toBe("lucide-check");
    expect(glyph(discard)).toBe("lucide-x");
    // Label in name: the tooltip is the same words as the name.
    expect(keep).toHaveAttribute("title", NOTE_ROW_WORDS.keep);
    expect(discard).toHaveAttribute("title", NOTE_ROW_WORDS.discard);
    // A suggestion keeps its Pin (restored); it has no delete question -
    // Discard is its way out.
    expect(within(row()).getByRole("button", { name: NOTE_ROW_WORDS.pin })).toBeTruthy();
    expect(within(row()).queryByRole("button", { name: NOTE_ROW_WORDS.delete })).toBeNull();
  });

  it("Cancel edit and Discard never share a glyph", async () => {
    const user = userEvent.setup();
    mount([entry({ provenance: "model", status: "proposed" })]);
    const discard = await screen.findByRole("button", { name: NOTE_ROW_WORDS.discard });
    await user.click(within(row()).getByRole("button", { name: NOTE_ROW_WORDS.edit }));
    const cancel = within(row()).getByRole("button", { name: NOTE_ROW_WORDS.cancel });
    expect(glyph(cancel)).not.toBe(glyph(discard));
    expect(glyph(cancel)).toBe("lucide-undo2");
  });

  it("Discard from the keyboard says so in the row, and Undo puts it back", async () => {
    // Not silent. The row becomes "Suggestion discarded." with an
    // Undo that takes the keyboard; Undo brings the suggestion back and
    // hands the keyboard to its Discard. Nothing is deleted meanwhile.
    const user = userEvent.setup();
    const { sent } = mount([entry({ provenance: "model", status: "proposed" })],
      { "DELETE /notebook/entries/1": { body: { ok: true } } });
    const discard = await screen.findByRole("button", { name: NOTE_ROW_WORDS.discard });
    discard.focus();
    await user.keyboard("{Enter}");       // a keyboard press: click detail 0
    const undo = await screen.findByRole("button", { name: NOTE_ROW_WORDS.undoDiscard });
    expect(screen.getByTestId("note-1-discarded").textContent)
      .toContain(NOTE_ROW_WORDS.discarded);
    await waitFor(() => expect(undo).toHaveFocus());
    expect(sent("DELETE")).toEqual([]);
    await user.keyboard("{Enter}");
    const back = await screen.findByRole("button", { name: NOTE_ROW_WORDS.discard });
    await waitFor(() => expect(back).toHaveFocus());
    expect(screen.queryByTestId("note-1-discarded")).toBeNull();
    expect(sent("DELETE")).toEqual([]);
  });

  it("a held discard survives a page close: pagehide sends nothing", async () => {
    // No request may outlive its page (the egress sentinel refuses
    // keepalive), so a close leaves the suggestion where it is.
    const user = userEvent.setup();
    const { sent } = mount([entry({ provenance: "model", status: "proposed" })],
      { "DELETE /notebook/entries/1": { body: { ok: true } } });
    await user.click(await screen.findByRole("button", { name: NOTE_ROW_WORDS.discard }));
    await screen.findByTestId("note-1-discarded");
    act(() => { window.dispatchEvent(new Event("pagehide")); });
    await new Promise((r) => setTimeout(r, 30));
    expect(sent("DELETE")).toEqual([]);
    // Still held: the row and its Undo are still there.
    expect(screen.getByTestId("note-1-discarded")).toBeTruthy();
  });

  it("under the real egress sentinel, pagehide makes no request at all (no keepalive)", async () => {
    const user = userEvent.setup();
    mount([entry({ provenance: "model", status: "proposed" })],
      { "DELETE /notebook/entries/1": { body: { ok: true } } });
    await user.click(await screen.findByRole("button", { name: NOTE_ROW_WORDS.discard }));
    await screen.findByTestId("note-1-discarded");
    // Back to the suite's own fetch: the sentinel, which records every
    // request and refuses a keepalive one.
    vi.unstubAllGlobals();
    const before = sentinelLog().egress.length;
    act(() => { window.dispatchEvent(new Event("pagehide")); });
    await new Promise((r) => setTimeout(r, 30));
    expect(sentinelLog().egress.slice(before)).toEqual([]);
    // POSITIVE CONTROL: the sentinel is the live fetch and does record one.
    await fetch("http://127.0.0.1:9/probe").catch(() => undefined);
    expect(sentinelLog().egress.length).toBe(before + 1);
  });

  it("a held discard of a note that became accepted meanwhile is not sent", async () => {
    const user = userEvent.setup();
    const proposal = entry({ provenance: "model", status: "proposed" });
    const { sent, served, queryClient } = mount([proposal],
      { "DELETE /notebook/entries/1": { body: { ok: true } } });
    await user.click(await screen.findByRole("button", { name: NOTE_ROW_WORDS.discard }));
    await screen.findByTestId("note-1-discarded");
    // Accepted elsewhere (another window, the automatic reader) before the
    // held delete goes.
    served.entries = [{ ...proposal, status: "accepted" }];
    await act(async () => { await queryClient.invalidateQueries(); });
    act(() => useUiStore.setState({ selectedChatId: 8 }));
    await new Promise((r) => setTimeout(r, 50));
    expect(sent("DELETE")).toEqual([]);
  });

  it("a held discard is sent when the panel leaves the chat", async () => {
    const user = userEvent.setup();
    const { sent } = mount([entry({ provenance: "model", status: "proposed" })],
      { "DELETE /notebook/entries/1": { body: { ok: true } } });
    await user.click(await screen.findByRole("button", { name: NOTE_ROW_WORDS.discard }));
    await screen.findByTestId("note-1-discarded");
    expect(sent("DELETE")).toEqual([]);                  // ground: held
    act(() => useUiStore.setState({ selectedChatId: 8 }));
    await waitFor(() => expect(sent("DELETE")).toHaveLength(1));
    expect(sent("DELETE")[0]).toContain(`/notebook/entries/1?chat_id=${CHAT}`);
    // Sent, so no Undo is offered for it any more on coming back.
    act(() => useUiStore.setState({ selectedChatId: CHAT }));
    await screen.findByTestId("note-1");
    expect(screen.queryByTestId("note-1-discarded")).toBeNull();
    expect(screen.queryByRole("button", { name: NOTE_ROW_WORDS.undoDiscard })).toBeNull();
  });

  it("the second half of a double click does not discard; a single click does", async () => {
    mount([entry({ provenance: "model", status: "proposed" })]);
    const discard = await screen.findByRole("button", { name: NOTE_ROW_WORDS.discard });
    fireEvent.click(discard, { detail: 2 });
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByTestId("note-1-discarded")).toBeNull();
    // POSITIVE CONTROL: a single click goes (the keyboard's detail 0 is the
    // test above).
    fireEvent.click(discard, { detail: 1 });
    await screen.findByTestId("note-1-discarded");
  });

  it("Keep hands the keyboard to the row's Pin", async () => {
    const user = userEvent.setup();
    const proposal = entry({ provenance: "model", status: "proposed" });
    const { served } = mount([proposal], {
      "/notebook/entries/1/accept": { get body() {
        served.entries = [{ ...proposal, status: "accepted",
          hand_accepted_at: "2026-09-27 10:02:00" }];
        return served.entries[0];
      } },
    });
    const keep = await screen.findByRole("button", { name: NOTE_ROW_WORDS.keep });
    keep.focus();
    await user.keyboard("{Enter}");
    await waitFor(() => expect(row()).toHaveAttribute("data-state", "live"));
    await waitFor(() =>
      expect(within(row()).getByRole("button", { name: NOTE_ROW_WORDS.pin })).toHaveFocus());
  });

  it("Pin keeps the keyboard while it saves", async () => {
    const user = userEvent.setup();
    const { served } = mount([entry()], {
      "PATCH /notebook/entries/1": { get body() {
        served.entries = [entry({ pinned: 1 })];
        return served.entries[0];
      } },
    });
    const pin = await screen.findByRole("button", { name: NOTE_ROW_WORDS.pin });
    pin.focus();
    await user.keyboard("{Enter}");
    const unpin = await within(row()).findByRole("button", { name: NOTE_ROW_WORDS.unpin });
    expect(unpin).toBe(pin);                             // the same element
    expect(unpin).toHaveFocus();
  });

  it("an accepted note's delete question is Trash and words, never a Check", async () => {
    const user = userEvent.setup();
    mount([entry()]);
    const trigger = await screen.findByRole("button", { name: NOTE_ROW_WORDS.delete });
    expect(glyph(trigger)).toBe("lucide-trash2");
    await user.click(trigger);
    const yes = await within(row()).findByRole("button", { name: NOTE_ROW_WORDS.confirmDelete });
    expect(glyph(yes)).toBe("lucide-trash2");
    expect(within(row()).getByRole("button", { name: NOTE_ROW_WORDS.cancelDelete })).toHaveFocus();
    // No Check anywhere in the row while the question is open.
    expect(row().querySelector("svg.lucide-check")).toBeNull();
  });
});

describe("while a note is being edited (Pin, tooltips)", () => {
  beforeEach(reset);
  afterEach(() => { cleanup(); vi.restoreAllMocks(); });

  it("Pin is hidden but keeps its place, and Tab goes field, Save, Cancel", async () => {
    const user = userEvent.setup();
    mount([entry()]);
    const pin = await screen.findByRole("button", { name: NOTE_ROW_WORDS.pin });
    const slot = screen.getByTestId("note-1-pin-slot");
    // GROUND: at rest Pin is reachable and visible.
    expect(slot).not.toHaveClass("invisible");
    expect(slot).not.toHaveAttribute("inert");

    await user.click(within(row()).getByRole("button", { name: NOTE_ROW_WORDS.edit }));
    const field = within(row()).getByRole("textbox", { name: NOTE_ROW_WORDS.field });
    expect(field).toHaveFocus();
    // Still in the row (its width is still taken), not shown, not reachable.
    expect(row().contains(pin)).toBe(true);
    expect(slot).toHaveClass("invisible");
    expect(slot).toHaveAttribute("inert");
    expect(within(row()).queryByRole("button", { name: NOTE_ROW_WORDS.pin })).toBeNull();

    await user.tab();
    expect(within(row()).getByRole("button", { name: NOTE_ROW_WORDS.save })).toHaveFocus();
    await user.tab();
    expect(within(row()).getByRole("button", { name: NOTE_ROW_WORDS.cancel })).toHaveFocus();

    // And back when the edit ends.
    await user.keyboard("{Escape}");
    expect(slot).not.toHaveClass("invisible");
    expect(within(row()).getByRole("button", { name: NOTE_ROW_WORDS.pin })).toBe(pin);
  });

  it("Save and Cancel carry tooltips in the same words as their names", async () => {
    const user = userEvent.setup();
    mount([entry()]);
    const pencil = await screen.findByRole("button", { name: NOTE_ROW_WORDS.edit });
    expect(pencil).toHaveAttribute("title", NOTE_ROW_WORDS.edit);
    await user.click(pencil);
    expect(pencil).toHaveAttribute("title", NOTE_ROW_WORDS.save);
    expect(within(row()).getByRole("button", { name: NOTE_ROW_WORDS.cancel }))
      .toHaveAttribute("title", NOTE_ROW_WORDS.cancel);
  });
});

describe("who wrote it, from the stored mark (provenance)", () => {
  beforeEach(reset);
  afterEach(() => { cleanup(); vi.restoreAllMocks(); });

  it("a model note already edited says so on first load, as after a restart", async () => {
    mount([entry({ provenance: "model", edited_at: "2026-09-27 11:00:00",
      updated_at: "2026-09-27 11:00:00" })]);
    expect(await screen.findByTestId("note-1-author")).toHaveTextContent(MODEL_NOTE_WORDS.edited);
  });

  it("a moved updated_at alone is not an edit", async () => {
    // A pin, a keep or a drag moves updated_at; only the mark says edited.
    mount([entry({ provenance: "model", updated_at: "2026-09-27 12:00:00" })]);
    expect(await screen.findByTestId("note-1-author")).toHaveTextContent(MODEL_NOTE_WORDS.wrote);
  });
});

describe("no second ask after the reader answered", () => {
  beforeEach(reset);
  afterEach(() => { cleanup(); vi.restoreAllMocks(); });

  const strip = () => screen.getByTestId("just-saved");
  const strippedOpen = () => !hiddenFromReaders(strip());

  it("GROUND: an auto-accepted model note nobody touched is announced", async () => {
    mount([entry({ provenance: "model" })]);
    await screen.findByTestId("note-1");
    await waitFor(() => expect(strippedOpen()).toBe(true));
    expect(strip()).toHaveTextContent("Mira is her sister.");
  });

  // The absences below are read in the SAME settled state as a presence:
  // an untouched model note beside them opens the strip (the ground), and
  // only then is the strip asked what it names.
  const UNTOUCHED = { id: 9, text: "Tom is ten.", provenance: "model" };

  it("a note kept by hand is not announced, even with nothing seen in memory", async () => {
    mount([entry({ provenance: "model", hand_accepted_at: "2026-09-27 10:01:00" }),
      entry(UNTOUCHED)]);
    await waitFor(() => expect(strippedOpen()).toBe(true));
    expect(strip()).toHaveTextContent("Tom is ten.");
    expect(strip()).not.toHaveTextContent("Mira is her sister.");
  });

  it("a note the reader edited is not announced either", async () => {
    mount([entry({ provenance: "model", edited_at: "2026-09-27 10:01:00" }),
      entry(UNTOUCHED)]);
    await waitFor(() => expect(strippedOpen()).toBe(true));
    expect(strip()).toHaveTextContent("Tom is ten.");
    expect(strip()).not.toHaveTextContent("Mira is her sister.");
  });

  it("a pinned model note is never on the card, so its Undo cannot delete it", async () => {
    mount([entry({ provenance: "model", pinned: 1 }), entry(UNTOUCHED)]);
    await waitFor(() => expect(strippedOpen()).toBe(true));
    expect(strip()).toHaveTextContent("Tom is ten.");
    expect(strip()).not.toHaveTextContent("Mira is her sister.");
  });

  it("pressing Keep never opens the strip about the note just kept", async () => {
    const user = userEvent.setup();
    const proposal = entry({ provenance: "model", status: "proposed" });
    const { served } = mount([proposal], {
      "/notebook/entries/1/accept": { get body() {
        served.entries = [{ ...proposal, status: "accepted",
          hand_accepted_at: "2026-09-27 10:02:00", updated_at: "2026-09-27 10:02:00" }];
        return served.entries[0];
      } },
    });
    await user.click(await screen.findByRole("button", { name: NOTE_ROW_WORDS.keep }));
    await waitFor(() => expect(row()).toHaveAttribute("data-state", "live"));
    expect(strippedOpen()).toBe(false);
  });
});
