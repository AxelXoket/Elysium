/**
 * FAZ 3 - the notebook panel.
 *
 * The panel's one job the storage layer cannot do is make three states legible
 * that look identical in a plain list: a note that IS being sent, one a newer
 * note replaced, and one the ceiling left out this turn. The owner's rule is
 * that a note never disappears, so none of them is hidden - which means the
 * mark is the only thing separating "working" from "silently not sent".
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { NotebookPanel } from "@/components/notebook/NotebookPanel";
import { mockFetch } from "../mocks/api";
import { useUiStore } from "@/lib/store/uiStore";
import { useSeenNotesStore } from "@/lib/chat/seenNotes";

function entry(over: Partial<Record<string, unknown>> = {}) {
  return {
    id: 1, chat_id: 7, position: 0, kind: "fact", text: "Mira is her sister.",
    evidence: null, durability: "permanent", importance: 2, pinned: 0,
    retired_at: null, superseded_by: null, excluded_reason: null,
    status: "accepted", provenance: "user", source_message_id: null,
    created_at: "2026-08-19", updated_at: "2026-08-19", ...over,
  };
}

describe("NotebookPanel", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: 7 });
  });
  afterEach(() => vi.restoreAllMocks());

  it("asks for a chat before it shows anything", async () => {
    useUiStore.setState({ selectedChatId: null });
    mockFetch({});
    renderWithQueryClient(<NotebookPanel />);
    expect(
      await screen.findByText(/open a chat/i),
    ).toBeInTheDocument();
  });

  it("shows a note and how many are being sent", async () => {
    mockFetch({
      "/notebook/7": { body: { entries: [entry()], notebook_chars: 40 } },
    });
    renderWithQueryClient(<NotebookPanel />);
    expect(await screen.findByText("Mira is her sister.")).toBeInTheDocument();
    expect(screen.getByTestId("notebook-sent-count").textContent)
      .toMatch(/1 of 1 sent/);
  });

  it("says WHY a note is not being sent, and the two reasons differ", async () => {
    // Both look like an ordinary row otherwise, and they call for different
    // actions: one is history, the other is fixable by pinning it.
    mockFetch({
      "/notebook/7": {
        body: {
          entries: [
            entry({ id: 1, text: "old wound", retired_at: "2026-08-19" }),
            entry({ id: 2, text: "crowded out",
                    excluded_reason: "over_ceiling" }),
          ],
          notebook_chars: 0,
        },
      },
    });
    renderWithQueryClient(<NotebookPanel />);

    await screen.findByText("old wound");
    expect(screen.getByText(/replaced by a newer note/i)).toBeInTheDocument();
    expect(screen.getByText(/did not fit this turn/i)).toBeInTheDocument();
    expect(screen.getByTestId("notebook-sent-count").textContent)
      .toMatch(/0 of 2 sent/);
  });

  it("marks a note the model wrote", async () => {
    // The badge is a fact, not a hint: provenance is set once at insert and
    // no route can change it.
    mockFetch({
      "/notebook/7": {
        body: { entries: [entry({ provenance: "model" })], notebook_chars: 0 },
      },
    });
    renderWithQueryClient(<NotebookPanel />);
    expect(await screen.findByText(/written by the model/i))
      .toBeInTheDocument();
  });

  it("adds a note", async () => {
    const user = userEvent.setup();
    // GET and POST answer differently, and the difference matters: with one
    // shared stub the POST reply fails the entry schema, `handleAdd` lands in
    // its catch, and a test that only checks "a request went" passes over a
    // broken success path.
    mockFetch({
      // Method-specific first: the mock takes the first matching key in
      // insertion order, and a bare "/notebook/7" would swallow the POST.
      "POST /notebook/7": { body: entry({ text: "she keeps her word" }) },
      "/notebook/7": { body: { entries: [], notebook_chars: 0 } },
    });
    renderWithQueryClient(<NotebookPanel />);

    const box = await screen.findByPlaceholderText(/established/i);
    await user.type(box, "she keeps her word");
    await user.click(screen.getByRole("button", { name: /add note/i }));

    // The box clears only on the success path, so this is the assertion that
    // separates "it sent something" from "it worked".
    await waitFor(() => expect(box).toHaveValue(""));
  });

  it("asks before deleting, in the row, without a second control appearing",
    async () => {
      const user = userEvent.setup();
      mockFetch({
        "/notebook/7": { body: { entries: [entry()], notebook_chars: 0 } },
      });
      renderWithQueryClient(<NotebookPanel />);

      await user.click(await screen.findByRole("button", { name: /delete note/i }));
      // The delete button is REPLACED by confirm/cancel - the row does not
      // remount and no dialog opens over the list.
      expect(screen.queryByRole("button", { name: /delete note/i }))
        .not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: /confirm delete/i }))
        .toBeInTheDocument();
      expect(screen.getByRole("button", { name: /keep note/i }))
        .toBeInTheDocument();
    });

  it("pins with one button whose icon swaps", async () => {
    mockFetch({
      "/notebook/7": {
        body: { entries: [entry({ pinned: 1 })], notebook_chars: 0 },
      },
    });
    renderWithQueryClient(<NotebookPanel />);
    // One control, two meanings - not two controls appearing and disappearing.
    expect(await screen.findByRole("button", { name: /unpin note/i }))
      .toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^pin note$/i }))
      .not.toBeInTheDocument();
  });
});

describe("what the model wrote, and taking it back", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: 7 });
    useSeenNotesStore.setState({ byChat: {} });
  });
  afterEach(() => vi.restoreAllMocks());

  const modelNote = (over = {}) => entry({
    id: 5, text: "Her brother owns the mill.", provenance: "model",
    evidence: "kardesi degirmenin sahibi", ...over,
  });

  it("announces a note the model saved without asking", async () => {
    // A35. With auto-accept on there is NO review step, so this is the only
    // moment the user is told a note was written. Every shipped version of
    // this feature that surprised its users surprised them exactly here.
    mockFetch({ "/notebook/7": { body: { entries: [modelNote()],
                                         notebook_chars: 10 } } });
    renderWithQueryClient(<NotebookPanel />);
    const strip = await screen.findByTestId("just-saved");
    expect(strip.textContent).toMatch(/saved a note the model wrote/i);
    expect(strip.textContent).toContain("Her brother owns the mill.");
  });

  it("does not announce the user's own notes", async () => {
    // Ground: the user knows what they typed.
    mockFetch({ "/notebook/7": { body: { entries: [entry()],
                                         notebook_chars: 10 } } });
    renderWithQueryClient(<NotebookPanel />);
    await screen.findByTestId("note-1");
    expect(screen.queryByTestId("just-saved")).not.toBeInTheDocument();
  });

  it("does not announce a proposal - it is already visibly waiting", async () => {
    mockFetch({ "/notebook/7": {
      body: { entries: [modelNote({ status: "proposed" })],
              notebook_chars: 10 } } });
    renderWithQueryClient(<NotebookPanel />);
    await screen.findByTestId("note-5");
    expect(screen.queryByTestId("just-saved")).not.toBeInTheDocument();
  });

  it("Undo actually deletes it", async () => {
    const user = userEvent.setup();
    mockFetch({
      "DELETE /notebook/entries/5": { body: { ok: true } },
      "/notebook/7": { body: { entries: [modelNote()], notebook_chars: 10 } },
    });
    renderWithQueryClient(<NotebookPanel />);
    await user.click(await screen.findByRole("button", { name: /^undo$/i }));

    await waitFor(() => {
      const call = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls
        .find((c: unknown[]) =>
          String(c[0]).includes("/notebook/entries/5")
          && (c[1] as RequestInit | undefined)?.method === "DELETE");
      expect(call).toBeTruthy();
    });
  });

  it("stops announcing once it has been acknowledged", async () => {
    const user = userEvent.setup();
    mockFetch({ "/notebook/7": { body: { entries: [modelNote()],
                                         notebook_chars: 10 } } });
    renderWithQueryClient(<NotebookPanel />);
    await user.click(await screen.findByRole("button", { name: /keep it/i }));
    await waitFor(() =>
      expect(screen.queryByTestId("just-saved")).not.toBeInTheDocument());
  });

  it("shows the Turkish original under an English note", async () => {
    // The whole reason notes are English is that a small model reads and
    // writes it far better. The cost is that a Turkish sentence comes back
    // as somebody else's paraphrase - unless the verbatim quote is there to
    // check it against.
    mockFetch({ "/notebook/7": { body: {
      entries: [modelNote({ evidence: "kardeşi değirmenin sahibi" })],
      notebook_chars: 10 } } });
    renderWithQueryClient(<NotebookPanel />);
    const row = await screen.findByTestId("note-5");
    expect(row.textContent).toMatch(/Türkçe aslı/);
    expect(row.textContent).toContain("kardeşi değirmenin sahibi");
  });

  it("says WHY the note is in English", async () => {
    mockFetch({ "/notebook/7": { body: { entries: [modelNote()],
                                         notebook_chars: 10 } } });
    renderWithQueryClient(<NotebookPanel />);
    expect((await screen.findByTestId("note-5")).textContent)
      .toMatch(/reads and writes English far better/i);
  });

  it("does not repeat the quote when it is already the note", async () => {
    // Ground: a second identical line is noise, not evidence.
    mockFetch({ "/notebook/7": { body: {
      entries: [modelNote({ text: "she said her brother owns the mill",
                            evidence: "her brother owns the mill" })],
      notebook_chars: 10 } } });
    renderWithQueryClient(<NotebookPanel />);
    const row = await screen.findByTestId("note-5");
    expect(row.textContent).not.toMatch(/From:/);
  });
});

describe("finding a note again", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: 7 });
    useSeenNotesStore.setState({ byChat: {} });
  });
  afterEach(() => vi.restoreAllMocks());

  const many = (extra: object[] = []) => ({
    entries: [
      ...Array.from({ length: 6 }, (_, i) =>
        entry({ id: 100 + i, text: `filler note ${i}` })),
      ...extra,
    ],
    notebook_chars: 10,
  });

  it("does not offer a search box over a short list", async () => {
    // Furniture over four rows.
    mockFetch({ "/notebook/7": { body: { entries: [entry()],
                                         notebook_chars: 10 } } });
    renderWithQueryClient(<NotebookPanel />);
    await screen.findByTestId("note-1");
    expect(screen.queryByLabelText(/search notes/i)).not.toBeInTheDocument();
  });

  it("filters by what the note says", async () => {
    const user = userEvent.setup();
    mockFetch({ "/notebook/7": { body: many([
      entry({ id: 200, text: "Mira keeps the ledger." })]) } });
    renderWithQueryClient(<NotebookPanel />);
    await user.type(await screen.findByLabelText(/search notes/i), "ledger");
    expect(await screen.findByTestId("note-200")).toBeInTheDocument();
    expect(screen.queryByTestId("note-100")).not.toBeInTheDocument();
  });

  it("finds a note by the TURKISH it was taken from", async () => {
    // The point of searching the quote. Notes are written in English, so the
    // words the user actually typed are otherwise the one thing they cannot
    // search for.
    const user = userEvent.setup();
    mockFetch({ "/notebook/7": { body: many([
      entry({ id: 201, text: "Her brother owns the mill.",
              provenance: "model",
              evidence: "kardeşi değirmenin sahibi" })]) } });
    renderWithQueryClient(<NotebookPanel />);
    await user.type(await screen.findByLabelText(/search notes/i), "değirmen");
    expect(await screen.findByTestId("note-201")).toBeInTheDocument();
  });

  it("lowercases the Turkish way", async () => {
    // `İstanbul` folds to `i̇stanbul` under the invariant rules and to
    // `istanbul` under Turkish ones. With the default, typing `istanbul`
    // finds nothing.
    const user = userEvent.setup();
    mockFetch({ "/notebook/7": { body: many([
      entry({ id: 202, text: "İstanbul is where they met." })]) } });
    renderWithQueryClient(<NotebookPanel />);
    await user.type(await screen.findByLabelText(/search notes/i), "istanbul");
    expect(await screen.findByTestId("note-202")).toBeInTheDocument();
  });

  it("says nothing matched, and how many are still there", async () => {
    // An empty filtered list looks exactly like an empty notebook, and one of
    // those means the notes are gone.
    const user = userEvent.setup();
    mockFetch({ "/notebook/7": { body: many() } });
    renderWithQueryClient(<NotebookPanel />);
    await user.type(await screen.findByLabelText(/search notes/i), "zzzz");
    expect(await screen.findByText(/no note matches that/i)).toBeInTheDocument();
    expect(screen.getByText(/6 are still here/i)).toBeInTheDocument();
  });

  it("the sent counter still describes the whole notebook", async () => {
    // Filtering is a view. A counter that followed the filter would report
    // that fewer notes are in force than actually are.
    const user = userEvent.setup();
    mockFetch({ "/notebook/7": { body: many() } });
    renderWithQueryClient(<NotebookPanel />);
    await user.type(await screen.findByLabelText(/search notes/i), "filler note 3");
    expect((await screen.findByTestId("notebook-sent-count")).textContent)
      .toMatch(/6 of 6 sent/);
  });

  it("clears back to the whole list", async () => {
    const user = userEvent.setup();
    mockFetch({ "/notebook/7": { body: many() } });
    renderWithQueryClient(<NotebookPanel />);
    const box = await screen.findByLabelText(/search notes/i);
    await user.type(box, "note 2");
    await user.click(screen.getByRole("button", { name: /clear search/i }));
    expect(await screen.findByTestId("note-100")).toBeInTheDocument();
  });
});
