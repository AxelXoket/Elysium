/**
 * FAZ 3 - the limits panel.
 *
 * The thing this screen must never do is make a limit look like it is in force
 * when it is not. Two states carry that risk: a chat told to ignore the global
 * set, and the moment the global toggle fails to save. Both are tested here,
 * because both look like a working limit list otherwise.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { BoundaryPanel } from "@/components/notebook/BoundaryPanel";
import { mockFetch } from "../mocks/api";
import { useUiStore } from "@/lib/store/uiStore";

function boundary(over: Partial<Record<string, unknown>> = {}) {
  return {
    id: 1, scope: "global", chat_id: null, label: "no gore",
    phrasing: "Avoid graphic injury.", severity: "hard", polarity: "avoid",
    on_violation: "pause", source: "explicit", rating_ceiling: null,
    exempt_from_trim: 1, last_confirmed_at: null, active: 1,
    created_at: "2026-08-19", updated_at: "2026-08-19", ...over,
  };
}

describe("BoundaryPanel", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: 7 });
  });
  afterEach(() => vi.restoreAllMocks());

  it("says plainly that limits are never trimmed", async () => {
    // The one promise this panel makes that the notebook does not. If the
    // wording ever softens, the refusal behind it stops being explained.
    mockFetch({ "/notebook/7/boundaries": { body: { boundaries: [], use_global: true } } });
    renderWithQueryClient(<BoundaryPanel />);
    expect(await screen.findByText(/never trimmed/i)).toBeInTheDocument();
  });

  it("shows a limit with its strictness and where it applies", async () => {
    mockFetch({
      "/notebook/7/boundaries": { body: { boundaries: [boundary()], use_global: true } },
    });
    renderWithQueryClient(<BoundaryPanel />);
    expect(await screen.findByText("no gore")).toBeInTheDocument();
    expect(screen.getByText(/never - everywhere/i)).toBeInTheDocument();
  });

  it("distinguishes a chat-scoped limit from a global one", async () => {
    mockFetch({
      "/notebook/7/boundaries": {
        body: {
          boundaries: [boundary({ id: 2, scope: "chat", chat_id: 7,
                                  label: "just here", severity: "soft" })],
        },
      },
    });
    renderWithQueryClient(<BoundaryPanel />);
    expect(await screen.findByText(/prefer not - this chat/i))
      .toBeInTheDocument();
  });

  it("adds a limit as explicit, never inferred", async () => {
    // The route has no `source` field at all: a limit typed by a person is
    // explicit by construction, and the app must never invent a hard one.
    const user = userEvent.setup();
    mockFetch({
      "/notebook/7/boundaries": { body: { boundaries: [], use_global: true } },
      "/notebook/boundaries": { body: boundary() },
    });
    renderWithQueryClient(<BoundaryPanel />);

    await user.type(await screen.findByPlaceholderText(/keep out of the story/i),
                    "no gore");
    await user.click(screen.getByRole("button", { name: /add limit/i }));

    await waitFor(() => {
      const call = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls
        .find((c: unknown[]) =>
          String(c[0]).includes("/notebook/boundaries")
          && (c[1] as RequestInit | undefined)?.method === "POST");
      expect(call).toBeTruthy();
      const body = JSON.parse(String((call![1] as RequestInit).body));
      expect(body).not.toHaveProperty("source");
      expect(body.severity).toBe("hard");
    });
  });

  it("can set the global limits aside for one chat", async () => {
    const user = userEvent.setup();
    mockFetch({
      "/notebook/7/boundaries": { body: { boundaries: [boundary()], use_global: true } },
      "/notebook/7/use-global": { body: { ok: true, use_global: false } },
    });
    renderWithQueryClient(<BoundaryPanel />);

    await user.click(await screen.findByRole("switch", {
      name: /use my global limits/i,
    }));

    await waitFor(() => {
      expect(
        (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.some(
          (c: unknown[]) => String(c[0]).includes("/use-global"),
        ),
      ).toBe(true);
    });
  });

  it("renders the switch OFF when the server says it is off", async () => {
    // The bug this replaces: the panel guessed `true` on every mount and read
    // the stored flag nowhere. A chat with its global limits set aside showed
    // the switch ON after any remount - the exact "you believe it is in force
    // and it is not" failure this panel's own copy warns about.
    mockFetch({
      "/notebook/7/boundaries": {
        body: { boundaries: [boundary()], use_global: false },
      },
    });
    renderWithQueryClient(<BoundaryPanel />);
    const toggle = await screen.findByRole("switch", {
      name: /use my global limits/i,
    });
    // waitFor, not a bare assert: before the query resolves the switch shows
    // its fallback, and asserting there would pass on a panel that never reads
    // the server at all.
    await waitFor(() => expect(toggle).not.toBeChecked());
  });

  it("puts the switch back if the save fails", async () => {
    // Otherwise the screen says the global limits are off while the server
    // still has them on - the exact "looks in force, is not" failure.
    const user = userEvent.setup();
    mockFetch({
      "/notebook/7/boundaries": { body: { boundaries: [boundary()], use_global: true } },
      "/notebook/7/use-global": { status: 500, body: { detail: "nope" } },
    });
    renderWithQueryClient(<BoundaryPanel />);

    const toggle = await screen.findByRole("switch", {
      name: /use my global limits/i,
    });
    await user.click(toggle);

    await waitFor(() => {
      expect(toggle).toBeChecked();
    });
  });
});
