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
