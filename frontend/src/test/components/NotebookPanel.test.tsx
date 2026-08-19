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
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { NotebookPanel } from "@/components/notebook/NotebookPanel";
import { mockFetch } from "../mocks/api";
import { useUiStore } from "@/lib/store/uiStore";
import { useSeenNotesStore } from "@/lib/chat/seenNotes";
import { useContextNotesStore } from "@/lib/chat/contextNotes";

/** The text of every live region on screen, in document order. */
function announced(): string[] {
  return screen.getAllByRole("status").map((node) => node.textContent ?? "");
}

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
    // One, not two. Both rows are on screen and neither is being sent, but
    // the retired one is not in the notebook any more - the server drops
    // retired rows before it counts, so counting it here would report a
    // notebook one note larger than the one that exists.
    expect(screen.getByTestId("notebook-sent-count").textContent)
      .toMatch(/0 of 1 sent/);
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

  it("says when the quote came from the model's own reply", async () => {
    // The one thing no verifier can supply. Every groundedness check asks
    // whether the note matches its quote, and when the model quotes ITSELF
    // that check passes by construction - so the quote being verbatim says
    // nothing at all about whether the underlying claim was invented.
    mockFetch({
      "/notebook/7": {
        body: {
          entries: [entry({ provenance: "model", evidence_role: "assistant" })],
          notebook_chars: 0,
        },
      },
    });
    renderWithQueryClient(<NotebookPanel />);
    expect(await screen.findByText(/model's own reply/i)).toBeInTheDocument();
  });

  it("does not say it when the quote came from something you wrote", async () => {
    // Ground: the mark has to distinguish, or it is decoration. A note the
    // model wrote FROM the user's own sentence is a paraphrase of something
    // that was really said, and marking it too would train the user to
    // ignore the mark on the notes where it means something.
    mockFetch({
      "/notebook/7": {
        body: {
          entries: [entry({ provenance: "model", evidence_role: "user" })],
          notebook_chars: 0,
        },
      },
    });
    renderWithQueryClient(<NotebookPanel />);
    // Positive control: the panel did render this note, badge and all.
    expect(await screen.findByText(/written by the model/i)).toBeInTheDocument();
    expect(screen.queryByText(/model's own reply/i)).not.toBeInTheDocument();
  });

  it("keeps the mark off notes the user typed", async () => {
    mockFetch({
      "/notebook/7": {
        body: {
          entries: [entry({ provenance: "user", evidence_role: null })],
          notebook_chars: 0,
        },
      },
    });
    renderWithQueryClient(<NotebookPanel />);
    expect(await screen.findByText(/Mira is her sister/)).toBeInTheDocument();
    expect(screen.queryByText(/model's own reply/i)).not.toBeInTheDocument();
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

  it("says WHY the notes are in English - once, not on every row", async () => {
    // Per row it was a lecture: a Turkish-speaking owner being told about
    // their own language's model support every few lines.
    mockFetch({ "/notebook/7": { body: {
      entries: [modelNote(), modelNote({ id: 6 })],
      notebook_chars: 10 } } });
    renderWithQueryClient(<NotebookPanel />);
    await screen.findByTestId("note-5");
    expect(screen.getAllByText(/reads English far better/i)).toHaveLength(1);
    expect((await screen.findByTestId("note-5")).textContent)
      .not.toMatch(/reads English far better/i);
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

/**
 * The filter is a view of ONE chat, and this panel does not unmount when the
 * chat changes. The trap it left is not a stale string: the search box only
 * renders above five notes, so a filter carried from a long notebook into a
 * short one hides every row AND takes its own escape hatch off the screen.
 */
describe("carrying a filter into the next chat", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: 7 });
    useSeenNotesStore.setState({ byChat: {} });
    useContextNotesStore.setState({ byChat: {} });
  });
  afterEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: null });
  });

  const sixNotes = {
    entries: Array.from({ length: 6 }, (_, i) =>
      entry({ id: 100 + i, text: `filler note ${i}` })),
    notebook_chars: 10,
  };
  const oneNote = {
    entries: [entry({ id: 300, chat_id: 3, text: "only note here" })],
    notebook_chars: 10,
  };

  it("forgets the search when the chat changes", async () => {
    const user = userEvent.setup();
    mockFetch({
      "/notebook/7": { body: sixNotes },
      "/notebook/3": { body: oneNote },
    });
    renderWithQueryClient(<NotebookPanel />);
    await user.type(await screen.findByLabelText(/search notes/i), "zzzz");
    // Ground: the filter is really filtering. Without this the next
    // assertion would pass on a panel that never hid anything.
    expect(screen.queryByTestId("note-100")).not.toBeInTheDocument();

    act(() => useUiStore.setState({ selectedChatId: 3 }));

    expect(await screen.findByTestId("note-300")).toBeInTheDocument();
    // And this is why it is not cosmetic: the short chat has no search box,
    // so a filter that survived the switch would be unclearable.
    expect(screen.queryByLabelText(/search notes/i)).not.toBeInTheDocument();
  });

  it("does not answer the delete question for the next chat's note",
    async () => {
      // Ground for the reset being scoped to the filter alone. Entry ids are
      // unique across the whole table, so a confirm held open in one chat
      // matches no row in another and has nothing to attach to.
      const user = userEvent.setup();
      mockFetch({
        "/notebook/7": { body: sixNotes },
        "/notebook/3": { body: oneNote },
      });
      renderWithQueryClient(<NotebookPanel />);
      await user.click(
        (await screen.findAllByRole("button", { name: /delete note/i }))[0]);
      expect(screen.getByRole("button", { name: /confirm delete/i }))
        .toBeInTheDocument();

      act(() => useUiStore.setState({ selectedChatId: 3 }));

      await screen.findByTestId("note-300");
      expect(screen.queryByRole("button", { name: /confirm delete/i }))
        .not.toBeInTheDocument();
    });
});

describe("one empty state at a time", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: 7 });
    useSeenNotesStore.setState({ byChat: {} });
    useContextNotesStore.setState({ byChat: {} });
  });
  afterEach(() => vi.restoreAllMocks());

  const sixNotes = {
    entries: Array.from({ length: 6 }, (_, i) =>
      entry({ id: 100 + i, text: `filler note ${i}` })),
    notebook_chars: 10,
  };

  it("a filter that matched nothing does not also say the notebook is empty",
    async () => {
      // The two sentences contradict each other, and the wrong one tells the
      // reader their notes are gone.
      const user = userEvent.setup();
      mockFetch({ "/notebook/7": { body: sixNotes } });
      renderWithQueryClient(<NotebookPanel />);
      await user.type(await screen.findByLabelText(/search notes/i), "zzzz");

      expect(await screen.findByText(/no note matches that/i))
        .toBeInTheDocument();
      expect(screen.queryByText(/nothing yet/i)).not.toBeInTheDocument();
    });

  it("an empty notebook still says so", async () => {
    // Ground: the fix must not silence the real empty state, which is the
    // only thing on screen when a chat has never had a note.
    mockFetch({ "/notebook/7": { body: { entries: [], notebook_chars: 0 } } });
    renderWithQueryClient(<NotebookPanel />);
    expect(await screen.findByText(/nothing yet/i)).toBeInTheDocument();
    expect(screen.queryByText(/no note matches that/i)).not.toBeInTheDocument();
  });
});

/**
 * `{sent} of {total} sent` has two suppliers - the server's numbers from the
 * last turn, and a client count before any turn has run - and they were
 * measuring different sets. The fallback is only honest if it counts what the
 * server counts: accepted rows that have not been retired.
 */
describe("the sent counter counts one set", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: 7 });
    useSeenNotesStore.setState({ byChat: {} });
    useContextNotesStore.setState({ byChat: {} });
  });
  afterEach(() => vi.restoreAllMocks());

  const mixed = {
    entries: [
      entry({ id: 1, text: "in force" }),
      entry({ id: 2, text: "waiting on the user", status: "proposed",
              provenance: "model" }),
      entry({ id: 3, text: "replaced", retired_at: "2026-08-19" }),
    ],
    notebook_chars: 10,
  };

  it("before the first turn, unreviewed proposals are not in the total",
    async () => {
      mockFetch({ "/notebook/7": { body: mixed } });
      renderWithQueryClient(<NotebookPanel />);
      await screen.findByTestId("note-1");
      // Ground: all three rows ARE on screen, so a total of 1 cannot be the
      // number of rows the panel happens to be rendering.
      expect(screen.getByTestId("note-2")).toBeInTheDocument();
      expect(screen.getByTestId("note-3")).toBeInTheDocument();

      expect(screen.getByTestId("notebook-sent-count").textContent)
        .toMatch(/1 of 1 sent/);
    });

  it("after a turn the server's numbers win", async () => {
    // Ground for the one above: the fallback and the server disagree here on
    // purpose, so this passes only if the server's pair is the one shown.
    useContextNotesStore.setState({
      byChat: { 7: { notebook_sent: 2, notebook_total: 4, history_trimmed: 0 } },
    });
    mockFetch({ "/notebook/7": { body: mixed } });
    renderWithQueryClient(<NotebookPanel />);
    await screen.findByTestId("note-1");
    expect(screen.getByTestId("notebook-sent-count").textContent)
      .toMatch(/2 of 4 sent/);
  });
});

/**
 * The delete confirm replaces its own trigger, so the element the keyboard
 * was standing on is unmounted under it. Without help, focus lands on <body>
 * and the question is answerable only by tabbing back in from the top of the
 * document - with no Escape.
 */
describe("answering the delete question from the keyboard", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: 7 });
    useSeenNotesStore.setState({ byChat: {} });
    useContextNotesStore.setState({ byChat: {} });
  });
  afterEach(() => vi.restoreAllMocks());

  const one = { entries: [entry()], notebook_chars: 10 };

  it("focuses the SAFE choice when the question opens", async () => {
    const user = userEvent.setup();
    mockFetch({ "/notebook/7": { body: one } });
    renderWithQueryClient(<NotebookPanel />);
    await user.click(
      await screen.findByRole("button", { name: /delete note/i }));

    expect(screen.getByRole("button", { name: /keep note/i })).toHaveFocus();
    // Ground, twice over: not <body> (the unfixed behaviour) and not the
    // destructive button, which would put an irreversible delete under a
    // reflexive Enter.
    expect(document.activeElement).not.toBe(document.body);
    expect(screen.getByRole("button", { name: /confirm delete/i }))
      .not.toHaveFocus();
  });

  it("Escape backs out and hands focus back to the trigger", async () => {
    const user = userEvent.setup();
    mockFetch({ "/notebook/7": { body: one } });
    renderWithQueryClient(<NotebookPanel />);
    await user.click(
      await screen.findByRole("button", { name: /delete note/i }));
    await user.keyboard("{Escape}");

    const trigger = await screen.findByRole("button", { name: /delete note/i });
    expect(trigger).toHaveFocus();
    // Ground: backing out leaves the note alone rather than deleting it.
    expect(screen.getByTestId("note-1")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /confirm delete/i }))
      .not.toBeInTheDocument();
  });

  it("keeping the note hands focus back to the trigger too", async () => {
    // Ground for the Escape test: the same return happens on the button
    // path, so the trigger is genuinely refocusable and the key handler is
    // not the only thing holding this together.
    const user = userEvent.setup();
    mockFetch({ "/notebook/7": { body: one } });
    renderWithQueryClient(<NotebookPanel />);
    await user.click(
      await screen.findByRole("button", { name: /delete note/i }));
    await user.click(screen.getByRole("button", { name: /keep note/i }));

    expect(await screen.findByRole("button", { name: /delete note/i }))
      .toHaveFocus();
  });
});

describe("what a screen reader is told", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: 7 });
    useSeenNotesStore.setState({ byChat: {} });
    useContextNotesStore.setState({ byChat: {} });
  });
  afterEach(() => vi.restoreAllMocks());

  const sixNotes = {
    entries: Array.from({ length: 6 }, (_, i) =>
      entry({ id: 100 + i, text: `filler note ${i}` })),
    notebook_chars: 10,
  };

  it("the model's note and the sent count are live regions", async () => {
    // The strip appears after NO user action, and the count is rewritten by
    // a turn the user spent in the composer. Both are things that happen to
    // the reader rather than because of them.
    mockFetch({ "/notebook/7": { body: {
      entries: [entry({ id: 5, text: "Her brother owns the mill.",
                        provenance: "model" })],
      notebook_chars: 10 } } });
    renderWithQueryClient(<NotebookPanel />);
    await screen.findByTestId("just-saved");

    const live = announced();
    expect(live.some((t) => /saved a note the model wrote/i.test(t))).toBe(true);
    expect(live.some((t) => /of \d+ sent/.test(t))).toBe(true);
    // Ground: sitting on this panel is not enough. The row's own "Written by
    // the model." line is ordinary text and must not interrupt anybody.
    expect(live.some((t) => /written by the model/i.test(t))).toBe(false);
  });

  it("the search result count is a live region", async () => {
    const user = userEvent.setup();
    mockFetch({ "/notebook/7": { body: sixNotes } });
    renderWithQueryClient(<NotebookPanel />);
    await screen.findByTestId("note-100");
    // Ground: nothing says it before the search empties the list, so what
    // follows is about the message and not about the panel's chrome.
    expect(announced().some((t) => /no note matches/i.test(t))).toBe(false);

    await user.type(screen.getByLabelText(/search notes/i), "zzzz");
    await screen.findByText(/no note matches that/i);
    expect(announced().some((t) => /no note matches/i.test(t))).toBe(true);
  });

  it("the note box is still named once there is text in it", async () => {
    // A placeholder is a hint, not a name: it stops being read out as soon
    // as the field has a value, which is exactly when the user is in it.
    const user = userEvent.setup();
    mockFetch({ "/notebook/7": { body: { entries: [], notebook_chars: 0 } } });
    renderWithQueryClient(<NotebookPanel />);
    const box = await screen.findByPlaceholderText(/established/i);
    await user.type(box, "she keeps her word");

    expect(box).toHaveAccessibleName("New note");
    // Ground: the hint text is not what names it.
    expect(box).not
      .toHaveAccessibleName("Something this story has established...");
  });

  it("the search box was already named, and stays named", async () => {
    // The audit reported two placeholder-only inputs; this one was not one
    // of them. Asserted anyway so a later edit cannot quietly drop the label.
    const user = userEvent.setup();
    mockFetch({ "/notebook/7": { body: sixNotes } });
    renderWithQueryClient(<NotebookPanel />);
    const box = await screen.findByLabelText(/search notes/i);
    await user.type(box, "filler");
    expect(box).toHaveAccessibleName("Search notes");
  });
});
