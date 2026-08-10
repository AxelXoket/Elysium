/**
 * The leftover recovery file has to reach a screen, or it is not reported.
 *
 * /vault/status has carried orphaned_copy since it was added, and for a while
 * nothing rendered it - a field nobody paints is the same as a log line nobody
 * opens. empty_stub is the newest of the three and this is the test that keeps
 * it from repeating that.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { EmptyStubNotice } from "@/components/settings/EmptyStubNotice";
import { mockFetch } from "../mocks/api";


const BASE = { initialized: true, unlocked: true };

describe("EmptyStubNotice", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it("says nothing when there is no leftover file", async () => {
    mockFetch({ "/vault/status": { body: { ...BASE, empty_stub: false } } });
    renderWithQueryClient(<EmptyStubNotice />);

    await waitFor(() => {
      expect(screen.queryByTestId("empty-stub-notice")).not.toBeInTheDocument();
    });
  });

  it("names the file when one is there", async () => {
    mockFetch({ "/vault/status": { body: { ...BASE, empty_stub: true } } });
    renderWithQueryClient(<EmptyStubNotice />);

    expect(await screen.findByTestId("empty-stub-notice")).toBeInTheDocument();
    // Naming it is the point: the user has to be able to match what the app
    // says against what they see in the folder.
    expect(screen.getByText("app.db.empty-stub-bak")).toBeInTheDocument();
  });

  it("removes it without asking twice", async () => {
    // No confirmation step, unlike the other two notices in this folder. They
    // guard copies of the user's data; this file is provably empty.
    const user = userEvent.setup();
    mockFetch({
      "/vault/status": { body: { ...BASE, empty_stub: true } },
      "/vault/discard-empty-stub": { body: { removed: true, reason: "" } },
    });
    renderWithQueryClient(<EmptyStubNotice />);

    await user.click(await screen.findByRole("button", { name: /remove it/i }));

    await waitFor(() => {
      expect(
        (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.some(
          (c: unknown[]) => String(c[0]).includes("/vault/discard-empty-stub"),
        ),
      ).toBe(true);
    });
  });

  it("reports a refusal instead of pretending it worked", async () => {
    // The backend re-measures the file and refuses one that is not empty. A
    // screen that swallowed that would leave the user believing a file is gone
    // while it is still there, which is the failure mode every notice in this
    // folder exists to prevent.
    const user = userEvent.setup();
    mockFetch({
      "/vault/status": { body: { ...BASE, empty_stub: true } },
      "/vault/discard-empty-stub": {
        body: { removed: false, reason: "not_empty" },
      },
    });
    renderWithQueryClient(<EmptyStubNotice />);

    await user.click(await screen.findByRole("button", { name: /remove it/i }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/not empty/i);
  });
});
