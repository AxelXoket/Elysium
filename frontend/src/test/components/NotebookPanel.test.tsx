/**
 * FAZ 3 - the notebook panel.
 *
 * The panel's one job the storage layer cannot do is make three states legible
 * that look identical in a plain list: a note that IS being sent, one a newer
 * note replaced, and one the ceiling left out this turn. The rule is
 * that a note never disappears, so none of them is hidden - which means the
 * mark is the only thing separating "working" from "silently not sent".
 */
import { UNDO_RUN_CONFIRM } from "@/components/notebook/backfillProse";
import { NOTES_UNREAD, NOTE_ROW_WORDS } from "@/components/notebook/noteProse";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act, cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { hiddenFromReaders } from "@/test/helpers/accessibility";
import { NotebookPanel } from "@/components/notebook/NotebookPanel";
import { mockFetch } from "../mocks/api";
import { useUiStore } from "@/lib/store/uiStore";
import { useSeenNotesStore } from "@/lib/chat/seenNotes";
import { useContextNotesStore } from "@/lib/chat/contextNotes";
import {
  PANEL_DRAFT_KEYS,
  chatScopedPanelDraftKey,
  useDraftStore,
} from "@/lib/store/draftStore";
import { typeScale } from "@/lib/theme/tokens";
import { loadGlassRightCss } from "@/test/helpers/glassSurfaceCss";
import { nearestDeclaration, parseSheet } from "@/test/helpers/compiledSheet";
import {
  NOTES_TAB_CONFIRMS,
  armNotesTabConfirms,
  closeNotesTab,
  mountNotesTab,
  settleNotesTab,
} from "@/test/helpers/notesTab";

/** The text of every live region on screen, in document order. */
function announced(): string[] {
  return screen.getAllByRole("status").map((node) => node.textContent ?? "");
}

/** Every DELETE the component has sent, in order. */
function deleteCalls(): unknown[][] {
  return (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls
    .filter((c: unknown[]) =>
      (c[1] as RequestInit | undefined)?.method === "DELETE");
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

  it("says WHY a note is not being sent, and the reasons differ", async () => {
    // Three rows that look identical otherwise, calling for three different
    // actions: one is history, one is fixable by pinning it, and one is
    // ALREADY pinned - where "pin it" is the single action that cannot help.
    //
    // The server has always written the two ceiling reasons separately
    // (`over_ceiling` and `pinned_over_ceiling`, notebook_store.py); the
    // panel folded them into one state and printed one sentence.
    mockFetch({
      "/notebook/7": {
        body: {
          entries: [
            entry({ id: 1, text: "old wound", retired_at: "2026-08-19" }),
            entry({ id: 2, text: "crowded out",
                    excluded_reason: "over_ceiling" }),
            entry({ id: 3, text: "pinned and still cut", pinned: 1,
                    excluded_reason: "pinned_over_ceiling" }),
          ],
          notebook_chars: 0,
        },
      },
    });
    renderWithQueryClient(<NotebookPanel />);

    await screen.findByText("old wound");
    expect(screen.getByText(/replaced by a newer note/i)).toBeInTheDocument();

    // SCOPED PER ROW. A bare getByText cannot tell the two ceiling rows
    // apart, and the whole defect is that they read the same.
    const crowded = within(screen.getByTestId("note-2"));
    const pinned = within(screen.getByTestId("note-3"));

    // GROUND: the ordinary ceiling row still says what it always said.
    // Without this the test is green for a change that deletes both
    // sentences.
    expect(crowded.getByText(/pin it to protect it/i)).toBeInTheDocument();

    // The defect: this row is pinned, and was being told to pin it.
    expect(pinned.queryByText(/pin it to protect it/i)).toBeNull();
    expect(pinned.getByText(/unpin one to make room/i)).toBeInTheDocument();

    // POSITIVE CONTROL for the absence above: the matcher does find that
    // sentence somewhere on this screen, so `toBeNull` is a real refusal
    // and not a typo in the regex.
    expect(screen.getByText(/pin it to protect it/i)).toBeInTheDocument();

    // And the two rows are marked differently for anything reading state.
    expect(screen.getByTestId("note-2")).toHaveAttribute("data-state", "over");
    expect(screen.getByTestId("note-3"))
      .toHaveAttribute("data-state", "pinned_over");
    // Two, not three. All three rows are on screen and none is being sent,
    // but the retired one is not in the notebook any more - the server drops
    // retired rows before it counts, so counting it here would report a
    // notebook one note larger than the one that exists.
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
      // In words now: "Delete" beside "Cancel", the way the
      // strip's confirms read, and never a Check, which keeps.
      expect(screen.getByRole("button", { name: NOTE_ROW_WORDS.confirmDelete }))
        .toBeInTheDocument();
      expect(screen.getByRole("button", { name: NOTE_ROW_WORDS.cancelDelete }))
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
    // The strip is a Collapse: mounted closed until the note arrives, so
    // its words are awaited rather than read on first sight.
    const strip = await screen.findByTestId("just-saved");
    await waitFor(() =>
      expect(strip.textContent).toMatch(/saved a note the model wrote/i));
    expect(strip.textContent).toContain("Her brother owns the mill.");
  });

  it("breaks a quoted note inside a word when the word will not fit the card", async () => {
    // A note that is one long word was quoted whole on the saved line, which
    // held it to one run: measured headless at the panel's floor it went past
    // the card and was cut by the card's clip. The note's own row below
    // breaks the same word, so the line quoting it has to as well.
    // `overflow-wrap` inherits, so it is read on the line or the nearest
    // box above it that sets it, through the compiled sheet.
    const root = parseSheet(await loadGlassRightCss());
    const text = "She calls it Muvaffakiyetsizlestiricilestiriveremeyebileceklerimizdenmissinizcesine";
    mockFetch({ "/notebook/7": { body: { entries: [modelNote({ text })], notebook_chars: 10 } } });
    renderWithQueryClient(<NotebookPanel />);
    const strip = await screen.findByTestId("just-saved");
    await waitFor(() => expect(strip.textContent).toContain(text));
    const breaking = (el: Element): string | null =>
      nearestDeclaration(root, el, /^overflow-wrap$/)?.declaration.value ?? null;
    // GROUND: the line read is the one that quotes the note.
    const line = [...strip.querySelectorAll("p")].find((p) => (p.textContent ?? "").includes(text));
    expect(line, "no line on the strip quotes the note").toBeTruthy();
    expect(["break-word", "anywhere"], "the saved line holds a long word to one run")
      .toContain(breaking(line!));
    // POSITIVE CONTROL: the reader finds the break where the note's row
    // declares it, and finds none on a line that declares nothing.
    expect(["break-word", "anywhere"]).toContain(
      breaking(within(screen.getByTestId("note-5")).getByText(text)));
    const bare = document.createElement("p");
    document.body.appendChild(bare);
    try {
      expect(breaking(bare), "the reader found a break where nothing sets one").toBeNull();
    } finally {
      bare.remove();
    }
  }, 60_000);

  it("does not announce the user's own notes", async () => {
    // Ground: the user knows what they typed.
    mockFetch({ "/notebook/7": { body: { entries: [entry()],
                                         notebook_chars: 10 } } });
    renderWithQueryClient(<NotebookPanel />);
    await screen.findByTestId("note-1");
    expect(hiddenFromReaders(screen.queryByTestId("just-saved"))).toBe(true);
  });

  it("does not announce a proposal - it is already visibly waiting", async () => {
    mockFetch({ "/notebook/7": {
      body: { entries: [modelNote({ status: "proposed" })],
              notebook_chars: 10 } } });
    renderWithQueryClient(<NotebookPanel />);
    await screen.findByTestId("note-5");
    expect(hiddenFromReaders(screen.queryByTestId("just-saved"))).toBe(true);
  });

  it("Undo actually deletes it, once it has been confirmed", async () => {
    // The confirmation step is new. Undo deletes every note on the strip at
    // once and permanently, and it used to be one press of a ghost button -
    // while the same screen already asked before deleting a SINGLE note.
    const user = userEvent.setup();
    mockFetch({
      "DELETE /notebook/entries/5": { body: { ok: true } },
      "/notebook/7": { body: { entries: [modelNote()], notebook_chars: 10 } },
    });
    renderWithQueryClient(<NotebookPanel />);
    await user.click(await screen.findByRole("button", { name: /^undo$/i }));

    // GROUND: still nothing deleted at this point.
    expect(deleteCalls()).toHaveLength(0);

    // The confirm pane mounts once the Undo pane has faded out
    // (AnimatePresence, mode="wait"), so it is awaited.
    await user.click(await screen.findByRole("button", { name: /^remove it$/i }));

    await waitFor(() => {
      expect(deleteCalls().some((c) =>
        String(c[0]).includes("/notebook/entries/5"))).toBe(true);
    });
  });

  it("asks before deleting several, and deletes nothing until told", async () => {
    const user = userEvent.setup();
    mockFetch({
      "DELETE /notebook/entries/5": { body: { ok: true } },
      "DELETE /notebook/entries/6": { body: { ok: true } },
      "/notebook/7": {
        body: {
          entries: [modelNote(), modelNote({ id: 6, text: "A second one." })],
          notebook_chars: 20,
        },
      },
    });
    renderWithQueryClient(<NotebookPanel />);

    await user.click(await screen.findByRole("button", { name: /^undo$/i }));
    expect(deleteCalls()).toHaveLength(0);

    await user.click(await screen.findByRole("button", { name: /^remove all 2$/i }));

    await waitFor(() => expect(deleteCalls()).toHaveLength(2));
  });

  it("the saved-notes controls are on the chrome step, both before and after "
     + "the confirm swaps them", async () => {
    // The four buttons on this block are the same buttons the backfill strip
    // renders a few hundred pixels above it in the same tab - a danger
    // action and a ghost action, side by side, inside a `CrossFade`. They
    // took the label step while the strip's took chrome, so one panel drew
    // the identical control at 12 and 13 at once. The role rule settles it:
    // text inside a control takes chrome.
    //
    // BOTH PANES, because the swap is where a step goes missing: the confirm
    // pair is a different pair of buttons from the trigger pair, and only
    // one of them is on screen at a time.
    const user = userEvent.setup();
    mockFetch({
      "/notebook/7": {
        body: {
          entries: [modelNote(), modelNote({ id: 6, text: "A second one." })],
          notebook_chars: 20,
        },
      },
    });
    renderWithQueryClient(<NotebookPanel />);

    // Both names are taken BY NAME off the imported scale and typed as its
    // keys, so a renamed step is a compile error rather than a class that
    // matches nothing.
    const uiStep: keyof typeof typeScale = "ui";
    const labelStep: keyof typeof typeScale = "label";
    const UI = `text-${uiStep}`;
    const LABEL = `text-${labelStep}`;
    // GROUND for the names: they are the 13 and the 12 the rule means.
    expect(typeScale[uiStep].px).toBe(13);
    expect(typeScale[labelStep].px).toBe(12);
    const STEPS = Object.keys(typeScale).map((k) => `text-${k}`);
    const stepOf = (el: Element): string | null =>
      [...el.classList].filter((c) => STEPS.includes(c))[0] ?? null;

    const strip = await screen.findByTestId("just-saved");
    // The block is a Collapse: mounted shut and hidden from readers until
    // the notes arrive, so the first pane is AWAITED rather than read on
    // sight. Without this the sweep runs over a pane nobody can see yet.
    await screen.findByRole("button", { name: /^undo$/i });
    for (const pane of ["undo", "confirm"]) {
      if (pane === "confirm") {
        await user.click(screen.getByRole("button", { name: /^undo$/i }));
        await screen.findByRole("button", { name: /^remove all 2$/i });
      }
      // GROUND for this pane: the buttons it is named for are really here,
      // so the sweep below cannot pass on an empty swap.
      const buttons = [...strip.querySelectorAll("button")]
        .filter((b) => !hiddenFromReaders(b));
      const names = buttons.map((b) =>
        `${(b.textContent ?? "").trim()} ${b.getAttribute("aria-label") ?? ""}`);
      expect(names.some((n) => (pane === "confirm" ? /^Remove all 2/ : /^Undo/).test(n)),
        `${pane}: the pane's own button is not on screen`).toBe(true);
      expect(buttons.length, `${pane}: the pair is not a pair`).toBe(2);
      expect(buttons.filter((b) => stepOf(b) !== UI)
        .map((b) => `${pane}: ${(b.textContent ?? "").trim()} -> ${stepOf(b) ?? "no step"}`),
        "a control on the saved-notes block sizes its words off the chrome step")
        .toEqual([]);
    }

    // THE OTHER DIRECTION, so "put everything on 13" cannot pass: the line
    // that says what was saved is prose, and stays on the label step.
    const prose = strip.querySelector("p");
    expect(prose, "the saved-notes line rendered no paragraph").not.toBeNull();
    expect(stepOf(prose!), "the saved-notes line left the label step").toBe(LABEL);
  });

  it("cancelling deletes nothing and keeps announcing", async () => {
    // POSITIVE CONTROL for the confirmation: backing out must leave the
    // notes AND the strip exactly as they were.
    const user = userEvent.setup();
    mockFetch({
      "DELETE /notebook/entries/5": { body: { ok: true } },
      "/notebook/7": { body: { entries: [modelNote()], notebook_chars: 10 } },
    });
    renderWithQueryClient(<NotebookPanel />);

    await user.click(await screen.findByRole("button", { name: /^undo$/i }));
    await user.click(await screen.findByRole("button", { name: /^cancel$/i }));

    expect(deleteCalls()).toHaveLength(0);
    expect(await screen.findByRole("button", { name: /^undo$/i }))
      .toBeInTheDocument();
  });

  it("a refused delete does not silence the notes that are still there",
    async () => {
      // THE ordering defect. `acknowledge()` ran BEFORE the loop and marked
      // every id as seen; the first failed DELETE then broke out with the
      // rest already silenced, so notes that are still in the notebook
      // stopped being announced by the one strip that announces them.
      const user = userEvent.setup();
      mockFetch({
        "DELETE /notebook/entries/5": { status: 500, body: { detail: "no" } },
        "DELETE /notebook/entries/6": { body: { ok: true } },
        "/notebook/7": {
          body: {
            entries: [modelNote(), modelNote({ id: 6, text: "A second one." })],
            notebook_chars: 20,
          },
        },
      });
      renderWithQueryClient(<NotebookPanel />);

      await user.click(await screen.findByRole("button", { name: /^undo$/i }));
      await user.click(await screen.findByRole("button", { name: /^remove all 2$/i }));

      // The strip is still there, because nothing was actually removed.
      expect(await screen.findByRole("button", { name: /^undo$/i }))
        .toBeInTheDocument();
      expect(useSeenNotesStore.getState().byChat[7] ?? []).toEqual([]);
    });

  it("Keep it is a hand keep: it marks the note on the server and stops announcing it", async () => {
    // "Keep it" writes `hand_accepted_at` (POST .../keep), so "Undo
    // this run" keeps the note and the card does not ask again after a
    // restart, when the in-memory seen list is empty.
    const user = userEvent.setup();
    const served = { entries: [modelNote()], notebook_chars: 10 };
    const fetchMock = mockFetch({
      "POST /notebook/entries/5/keep": { get body() {
        served.entries = [modelNote({ hand_accepted_at: "2026-09-27 12:00:00" })];
        return served.entries[0];
      } },
      "/notebook/7": { get body() { return served; } },
    });
    renderWithQueryClient(<NotebookPanel />);
    await user.click(await screen.findByRole("button", { name: /keep it/i }));
    await waitFor(() =>
      expect(hiddenFromReaders(screen.queryByTestId("just-saved"))).toBe(true));
    const kept = fetchMock.mock.calls.filter((c: unknown[]) =>
      String(c[0]).includes("/notebook/entries/5/keep?chat_id=7")
      && (c[1] as RequestInit | undefined)?.method === "POST");
    expect(kept).toHaveLength(1);
    // After a "restart": the seen list is empty, and the mark alone keeps
    // the card shut.
    useSeenNotesStore.setState({ byChat: {} });
    cleanup();
    renderWithQueryClient(<NotebookPanel />);
    await screen.findByTestId("note-5");
    expect(hiddenFromReaders(screen.queryByTestId("just-saved"))).toBe(true);
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
    // Per row it was a lecture: a Turkish-speaking reader being told about
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
    expect(screen.queryByRole("textbox", { name: /search notes/i })).toBeNull();
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

  // WAS AN ANTI-TEST (`it.fails`) until the fold direction was decided on
  // 31 August 2026. It is an ordinary test now, and the line below is the
  // signal that the fix landed.
  //
  // The fold is `toLocaleLowerCase("tr")`, one locale, one direction. Turkish
  // rules win `İ` -> `i` and LOSE `I` -> `ı`: an English capital I folds to a
  // dotless one, so `fold("India")` is `"ındia"`, the typed `"india"` is not a
  // substring of it, and the note cannot be found. The panel's own text says
  // suggested notes are written in English, so English capital I is not an
  // edge case here - it is the common case.
  //
  // Switching to the invariant locale would not have fixed anything; it
  // turns the same defect around and breaks the İstanbul test above, which
  // is pinned and correct. The fold normalises the I family to one letter
  // before lowercasing instead, so the locale question does not arise.
  //
  // THE ACCEPTANCE CRITERION: this test and "lowercases the Turkish way"
  // have to be green AT THE SAME TIME. Either one alone is satisfied by
  // simply choosing the other locale.
  it("finds a note with an English capital I", async () => {
    const user = userEvent.setup();
    mockFetch({ "/notebook/7": { body: many([
      entry({ id: 203, text: "India is where the tea came from." })]) } });
    renderWithQueryClient(<NotebookPanel />);
    await user.type(await screen.findByLabelText(/search notes/i), "india");
    expect(await screen.findByTestId("note-203")).toBeInTheDocument();
  });

  it("finds a note whose text arrived DECOMPOSED", async () => {
    // The regression the I-family fold introduced, and the wider hole it
    // then had a chance to close.
    //
    // A note pasted from macOS, from a web page, or typed on many IMEs
    // arrives in NFD: `İ` is `I` + U+0307 rather than one codepoint. The
    // character class matches the base `I` only, so the replacement left the
    // combining dot stranded and produced `i̇stanbul` - which is exactly
    // the string the fold's own comment names as the failure it exists to
    // avoid. The `toLocaleLowerCase("tr")` it replaced got this right, so it
    // was a regression, not an inherited hole.
    //
    // Written with explicit escapes rather than pasted characters: an
    // editor, a linter or a git filter that normalises this file would
    // silently turn the test into a duplicate of the one above it.
    const user = userEvent.setup();
    const decomposed = "İstanbul is where they met.";
    expect(decomposed.normalize("NFC")).not.toBe(decomposed);   // ground
    mockFetch({ "/notebook/7": { body: many([
      entry({ id: 205, text: decomposed })]) } });
    renderWithQueryClient(<NotebookPanel />);
    await user.type(await screen.findByLabelText(/search notes/i), "istanbul");
    expect(await screen.findByTestId("note-205")).toBeInTheDocument();
  });

  it("finds a decomposed note with no I in it at all", async () => {
    // POSITIVE CONTROL for the test above, and a hole that predates the
    // I-family change: every Turkish letter with a diacritic differs NFC vs
    // NFD, so a decomposed `baş` never matched a typed `baş`. A fix
    // aimed only at the I family would leave this red.
    const user = userEvent.setup();
    const decomposed = "The başlangıç was quiet.";
    expect(decomposed.normalize("NFC")).not.toBe(decomposed);
    mockFetch({ "/notebook/7": { body: many([
      entry({ id: 206, text: decomposed })]) } });
    renderWithQueryClient(<NotebookPanel />);
    await user.type(await screen.findByLabelText(/search notes/i),
                    "başlangıç");
    expect(await screen.findByTestId("note-206")).toBeInTheDocument();
  });

  it("finds a note with no capital I either way", async () => {
    // GROUND CONTROL for the pair above. A query with nothing in the I family
    // must work under any fold anyone chooses, so a fix that breaks this is
    // not a fix.
    const user = userEvent.setup();
    mockFetch({ "/notebook/7": { body: many([
      entry({ id: 204, text: "The degirmen stood by the water." })]) } });
    renderWithQueryClient(<NotebookPanel />);
    await user.type(await screen.findByLabelText(/search notes/i), "degirmen");
    expect(await screen.findByTestId("note-204")).toBeInTheDocument();
  });

  it("says nothing matched, and how many are still there", async () => {
    // An empty filtered list looks exactly like an empty notebook, and one of
    // those means the notes are gone.
    const user = userEvent.setup();
    mockFetch({ "/notebook/7": { body: many() } });
    renderWithQueryClient(<NotebookPanel />);
    await user.type(await screen.findByLabelText(/search notes/i), "zzzz");
    await waitFor(() =>
      // The line is mounted closed before it opens (Collapse), so
      // "says so" is asked of the accessibility tree.
      expect(hiddenFromReaders(screen.getByText(/no note matches that/i))).toBe(false));
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
    expect(screen.queryByRole("textbox", { name: /search notes/i })).toBeNull();
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
      expect(screen.getByRole("button", { name: NOTE_ROW_WORDS.confirmDelete }))
        .toBeInTheDocument();

      act(() => useUiStore.setState({ selectedChatId: 3 }));

      await screen.findByTestId("note-300");
      expect(screen.queryByRole("button", { name: NOTE_ROW_WORDS.confirmDelete }))
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

      await waitFor(() =>
      // The line is mounted closed before it opens (Collapse), so
      // "says so" is asked of the accessibility tree.
      expect(hiddenFromReaders(screen.getByText(/no note matches that/i))).toBe(false));
      expect(hiddenFromReaders(screen.queryByText(/nothing yet/i))).toBe(true);
    });

  it("an empty notebook still says so", async () => {
    // Ground: the fix must not silence the real empty state, which is the
    // only thing on screen when a chat has never had a note.
    mockFetch({ "/notebook/7": { body: { entries: [], notebook_chars: 0 } } });
    renderWithQueryClient(<NotebookPanel />);
    await waitFor(() =>
      // The line is mounted closed before it opens (Collapse), so
      // "says so" is asked of the accessibility tree.
      expect(hiddenFromReaders(screen.getByText(/nothing yet/i))).toBe(false));
    expect(hiddenFromReaders(screen.queryByText(/no note matches that/i))).toBe(true);
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

    expect(screen.getByRole("button", { name: NOTE_ROW_WORDS.cancelDelete })).toHaveFocus();
    // Ground, twice over: not <body> (the unfixed behaviour) and not the
    // destructive button, which would put an irreversible delete under a
    // reflexive Enter.
    expect(document.activeElement).not.toBe(document.body);
    expect(screen.getByRole("button", { name: NOTE_ROW_WORDS.confirmDelete }))
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
    expect(screen.queryByRole("button", { name: NOTE_ROW_WORDS.confirmDelete }))
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
    await user.click(screen.getByRole("button", { name: NOTE_ROW_WORDS.cancelDelete }));

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
    await screen.findByText(/saved a note the model wrote/i);

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

/**
 * A failed load is not an empty notebook.
 *
 * `isError` was never taken off the query, so a 500 produced exactly the
 * shape of a notebook with nothing in it - `isLoading` false, `data`
 * undefined - and the panel answered "Nothing yet." about notes that were in
 * the database the whole time. For this feature that is the worst available
 * lie: the notebook exists so the character stops forgetting, and the panel
 * said the forgetting had already happened.
 */
describe("when the notes cannot be loaded", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: 7 });
  });
  afterEach(() => vi.restoreAllMocks());

  it("says so, and does not claim the notebook is empty", async () => {
    mockFetch({ "/notebook/7": { status: 500, body: { detail: "boom" } } });
    renderWithQueryClient(<NotebookPanel />);

    await waitFor(() =>
      // The line is mounted closed before it opens (Collapse), so
      // "says so" is asked of the accessibility tree.
      expect(hiddenFromReaders(screen.getByText(NOTES_UNREAD))).toBe(false));
    expect(hiddenFromReaders(screen.queryByText(/nothing yet/i))).toBe(true);
  });

  it("still says Nothing yet when the notebook really is empty", async () => {
    // GROUND CONTROL. Without this, deleting the empty-state branch outright
    // would satisfy the assertion above and lose a sentence the panel needs.
    mockFetch({ "/notebook/7": { body: { entries: [] } } });
    renderWithQueryClient(<NotebookPanel />);

    await waitFor(() =>
      // The line is mounted closed before it opens (Collapse), so
      // "says so" is asked of the accessibility tree.
      expect(hiddenFromReaders(screen.getByText(/nothing yet/i))).toBe(false));
    expect(hiddenFromReaders(screen.queryByText(NOTES_UNREAD))).toBe(true);
  });

  it("says neither when there are notes to show", async () => {
    mockFetch({ "/notebook/7": { body: { entries: [entry()] } } });
    renderWithQueryClient(<NotebookPanel />);

    expect(await screen.findByText(/mira is her sister/i)).toBeInTheDocument();
    expect(hiddenFromReaders(screen.queryByText(/nothing yet/i))).toBe(true);
    expect(hiddenFromReaders(screen.queryByText(NOTES_UNREAD))).toBe(true);
  });
});

describe("the confirm question does not follow the reader", () => {
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

  it("a chat switch disarms a confirm armed in the chat before it", async () => {
    // The panel does NOT unmount when the chat changes, so a bare boolean
    // carries across. Armed in chat 7 and left armed, chat 8 opens with its
    // Undo strip already asking to confirm a bulk delete of notes the reader
    // has never seen - the exact unconfirmed deletion the pair prevents.
    const user = userEvent.setup();
    mockFetch({
      "/notebook/7": { body: { entries: [modelNote(),
                                        modelNote({ id: 6, text: "A second." })],
                               notebook_chars: 20 } },
      "/notebook/8": { body: { entries: [modelNote({ id: 9 }),
                                        modelNote({ id: 10, text: "Other." })],
                               notebook_chars: 20 } },
    });
    renderWithQueryClient(<NotebookPanel />);

    await user.click(await screen.findByRole("button", { name: /^undo$/i }));
    // GROUND: it really is armed in this chat.
    expect(await screen.findByRole("button", { name: /^remove (it|all \d+)$/i })).toBeInTheDocument();

    act(() => useUiStore.setState({ selectedChatId: 8 }));

    expect(await screen.findByRole("button", { name: /^undo$/i }))
      .toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^remove (it|all \d+)$/i })).toBeNull();
  });
});


describe("every count the notebook says, at 0, 1 and 2", () => {
  // The same rule as the worker card's and the strip's rows: a number is
  // followed by words that agree with it. "Keep note" was the name of the
  // button that keeps two notes, and "1 are still here" the count of one.
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: 7 });
    useSeenNotesStore.setState({ byChat: {} });
    useContextNotesStore.setState({ byChat: {} });
    useDraftStore.getState().clearAll();
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    useDraftStore.getState().clearAll();
  });

  const flat = (text: string | null | undefined): string => (text ?? "").replace(/\s+/g, " ").trim();
  const saved = (n: number) => Array.from({ length: n }, (_, i) =>
    entry({ id: 40 + i, text: `saved note ${i}`, provenance: "model" }));

  it("the notes the model saved, and the undo that takes them back", async () => {
    const user = userEvent.setup();
    for (const n of [0, 1, 2] as const) {
      cleanup();
      useSeenNotesStore.setState({ byChat: {} });
      mockFetch({
        "/notebook/7": { body: { entries: [entry({ id: 1 }), ...saved(n)], notebook_chars: 10 } },
      });
      renderWithQueryClient(<NotebookPanel />);
      await screen.findByText("Mira is her sister.");
      const strip = screen.getByTestId("just-saved");
      if (n === 0) {
        // Nothing saved, nothing announced; the row at 1 is where the same
        // reader finds it.
        expect(hiddenFromReaders(strip), "an announcement with nothing saved").toBe(true);
        continue;
      }
      await waitFor(() => expect(hiddenFromReaders(strip)).toBe(false));
      const line = strip.querySelector("p");
      const [said, keep, remove] = n === 1
        ? ['Saved a note the model wrote: "saved note 0"', "Keep it", "Remove it"]
        : ["Saved 2 notes the model wrote.", "Keep them", "Remove all 2"];
      expect(flat(line?.textContent), `at ${n}`).toBe(said);
      expect(within(strip).getByRole("button", { name: keep }), `at ${n}`).toBeInTheDocument();
      await user.click(within(strip).getByRole("button", { name: /^undo$/i }));
      // Each confirm button is named by the words it shows (WCAG 2.5.3).
      const confirm = await within(strip).findByRole("button", { name: remove });
      expect(flat(confirm.textContent), `at ${n}`).toBe(remove);
      expect(within(strip).getByRole("button", { name: "Cancel" }), `at ${n}`).toBeInTheDocument();
    }
  }, 30_000);

  it("the notes still there when nothing matches the search", async () => {
    // The search box opens at six notes, but the word in it outlives that:
    // it is the chat's own draft, so an undo that takes the notebook below
    // six leaves the filter in place with fewer notes behind it.
    const said: string[] = [];
    for (const n of [0, 1, 2]) {
      cleanup();
      useDraftStore.getState().setPanelDraft(
        chatScopedPanelDraftKey(PANEL_DRAFT_KEYS.newNote, 7, "filter"), "nothing matches this");
      mockFetch({ "/notebook/7": { body: {
        entries: Array.from({ length: n }, (_, i) => entry({ id: 60 + i, text: `kept note ${i}` })),
        notebook_chars: 10 } } });
      renderWithQueryClient(<NotebookPanel />);
      const line = await screen.findByText(/no note matches that/i);
      // Settled: the line opens once the notes have arrived.
      await waitFor(() => expect(hiddenFromReaders(line)).toBe(false));
      said.push(flat(line.textContent));
    }
    expect(said).toEqual([
      expect.stringMatching(/^No note matches that\. 0 are still here,/),
      expect.stringMatching(/^No note matches that\. 1 is still here,/),
      expect.stringMatching(/^No note matches that\. 2 are still here,/),
    ]);
  }, 30_000);

  it("the notes sent against the notes there are", async () => {
    // A count with no noun of its own: "N of M sent" reads at every count.
    for (const n of [0, 1, 2]) {
      cleanup();
      mockFetch({ "/notebook/7": { body: {
        entries: Array.from({ length: n }, (_, i) => entry({ id: 80 + i, text: `sent note ${i}` })),
        notebook_chars: 10 } } });
      renderWithQueryClient(<NotebookPanel />);
      await waitFor(() =>
        expect(flat(screen.getByTestId("notebook-sent-count").textContent)).toBe(`${n} of ${n} sent`));
    }
  }, 30_000);
});

describe("every button in the Notes tab is named by the words it shows", () => {
  // WCAG 2.5.3, Label in Name: a control that shows words carries them in
  // its accessible name, so a reader who says what they see reaches it. The
  // saved notes' confirm showed "Cancel" and was named "Keep notes", and
  // its partner showed "Delete it" and was named "Confirm delete"; the
  // strip's showed "Remove all 16" and was named "Confirm removing 16
  // notes". A button that shows no words (an icon) is named by its label
  // alone, and is not held to this.
  afterEach(() => {
    cleanup();
    closeNotesTab();
    vi.restoreAllMocks();
  });

  const flat = (text: string | null | undefined): string =>
    (text ?? "").replace(/\s+/g, " ").trim().toLowerCase();

  /** The accessible name a button gets from its own markup: the elements
   *  it is labelled by, else its label, else its words. */
  const nameOf = (el: Element): string => {
    const by = el.getAttribute("aria-labelledby");
    if (by) {
      return flat(by.split(/\s+/).map((id) => document.getElementById(id)?.textContent).join(" "));
    }
    return flat(el.getAttribute("aria-label") ?? el.textContent);
  };

  /** Every button on screen whose name does not carry the words it shows.
   *  A select's trigger is a `<button>` with the role of a combobox, and the
   *  words it shows are its value, not its label, so it is not asked. */
  const isButton = (b: Element): boolean => (b.getAttribute("role") ?? "button") === "button";
  const mismatches = (within: Element): string[] =>
    [...within.querySelectorAll<HTMLElement>('button, [role="button"]')]
      .filter((b) => isButton(b) && !b.closest("[inert]") && flat(b.textContent) !== "")
      .filter((b) => !nameOf(b).includes(flat(b.textContent)))
      .map((b) => `"${flat(b.textContent)}" is named "${nameOf(b)}"`);

  it("at rest and with every confirm armed", async () => {
    const user = userEvent.setup();
    mountNotesTab();
    await settleNotesTab();
    const tab = screen.getByRole("tabpanel");
    const worded = () => [...tab.querySelectorAll("button")]
      .filter((b) => isButton(b) && flat(b.textContent) !== "");
    // GROUND: the tab shows buttons with words, so the check reads some.
    expect(worded().length).toBeGreaterThan(5);
    expect(mismatches(tab), "at rest").toEqual([]);
    await armNotesTabConfirms(user);
    // GROUND: the two confirms with words of their own are on screen, found
    // by the words they show and not by their names, which are the question.
    // The run's confirm now reads "Remove untouched notes" (what it does);
    // the saved notes' reads "Remove all N". Both begin "Remove".
    for (const words of [
      new RegExp(`^remove (all \\d+|${UNDO_RUN_CONFIRM.toLowerCase().replace(/^remove /, "")})$`),
      /^cancel$/]) {
      expect(worded().filter((b) => words.test(flat(b.textContent))).length, `${words}`)
        .toBeGreaterThan(1);
    }
    expect(mismatches(tab), "with every confirm armed").toEqual([]);

    // POSITIVE CONTROL: a button that shows one thing and is named another
    // is reported; one whose name carries its words, and one that shows
    // only an icon, are not.
    const probe = (label: string | null, words: string): HTMLButtonElement => {
      const b = document.createElement("button");
      if (label !== null) b.setAttribute("aria-label", label);
      b.textContent = words;
      tab.appendChild(b);
      return b;
    };
    const wrong = probe("Keep notes", "Cancel");
    const right = probe("Remove all 2 notes", "Remove all 2");
    const icon = probe("Confirm delete", "");
    try {
      expect(mismatches(tab)).toEqual(['"cancel" is named "keep notes"']);
    } finally {
      wrong.remove();
      right.remove();
      icon.remove();
    }
  }, 60_000);

  it("the two confirms with words of their own speak one pattern: \"Remove\" and a count, "
     + "beside \"Cancel\"", async () => {
    // The strip's undo said "Remove all 16" beside "Keep them" while the
    // saved notes' said "Delete all 2" beside "Cancel", 150 px apart, for
    // the same act: taking back notes the model wrote.
    const user = userEvent.setup();
    mountNotesTab();
    await settleNotesTab();
    await armNotesTabConfirms(user);
    /** The words on each button of the pair a confirm sits in. */
    const pairOf = (confirm: Element): string[] => {
      const pane = confirm.closest('[data-slot="cross-fade"]');
      expect(pane, `${flat(confirm.textContent)} is not in a cross-fade`).not.toBeNull();
      return [...pane!.querySelectorAll("button")].filter((b) => !b.closest("[inert]"))
        .map((b) => flat(b.textContent));
    };
    const pairs = [NOTES_TAB_CONFIRMS.run, NOTES_TAB_CONFIRMS.saved]
      .map((name) => pairOf(screen.getByRole("button", { name })));
    // GROUND: both confirms are on screen, each as a pair.
    expect(pairs.map((p) => p.length)).toEqual([2, 2]);
    // "Remove untouched notes" is the run's: the run keeps what the reader
    // kept, pinned or edited, so "all N" would promise more than it does.
    const PATTERN = [
      new RegExp(`^remove (it|all \\d+|${UNDO_RUN_CONFIRM.toLowerCase().replace(/^remove /, "")})$`),
      /^cancel$/];
    const offPattern = (pair: string[]): boolean =>
      !(pair.length === PATTERN.length && pair.every((words, i) => PATTERN[i].test(words)));
    expect(pairs.filter(offPattern), "a confirm pair in the Notes tab speaks another pattern")
      .toEqual([]);
    // POSITIVE CONTROL: the saved notes' old pair is off the pattern.
    expect(offPattern(["delete all 2", "cancel"])).toBe(true);
    expect(offPattern(["remove all 16", "keep them"])).toBe(true);
  }, 60_000);
});
