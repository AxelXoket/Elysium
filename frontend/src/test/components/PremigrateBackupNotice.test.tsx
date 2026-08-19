/**
 * A stale, encrypted snapshot from a migration that never finished cleanly -
 * app.db.premigrate.bak, reported by /vault/status and removable by a route,
 * neither of which existed before this file's component did.
 *
 * The wording question these tests check is narrower than PlaintextBackupNotice's:
 * this copy is NOT a leak (it opens with the current passphrase), so the tests
 * make sure the copy says that plainly, while also making sure it explains why
 * the file is still worth a banner - it is frozen at the moment it was taken,
 * so something deleted from the live vault afterward still lives inside it.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";

import { PremigrateBackupNotice } from "@/components/settings/PremigrateBackupNotice";
import { mockFetch } from "@/test/mocks/api";

function withStatus(present: boolean, discard?: unknown) {
  mockFetch({
    "/vault/status": {
      body: {
        initialized: true,
        unlocked: true,
        premigrate_backup: present,
      },
    },
    "/vault/discard-premigrate-backup": {
      body: discard ?? { removed: true, reason: "" },
    },
  });
}

const DELETE = { name: /delete the snapshot/i } as const;

describe("the snapshot is visible at all", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it("says so when one is on disk", async () => {
    withStatus(true);
    renderWithQueryClient(<PremigrateBackupNotice />);
    expect(
      await screen.findByTestId("premigrate-backup-notice"),
    ).toBeInTheDocument();
  });

  it("stays out of the way when there is none - the ground for every test above it", async () => {
    withStatus(false);
    renderWithQueryClient(<PremigrateBackupNotice />);
    await waitFor(() => {
      expect(screen.queryByTestId("premigrate-backup-notice")).toBeNull();
    });
  });

  it("survives a backend that does not know the field yet", async () => {
    // An older build answers /vault/status without it. Parsing must not throw
    // and take the whole settings tab down with it - the optional() on the
    // schema field is what this test is really pinning down.
    mockFetch({
      "/vault/status": { body: { initialized: true, unlocked: true } },
    });
    renderWithQueryClient(<PremigrateBackupNotice />);
    await waitFor(() => {
      expect(screen.queryByTestId("premigrate-backup-notice")).toBeNull();
    });
  });

  it("says it is not a leak", async () => {
    withStatus(true);
    renderWithQueryClient(<PremigrateBackupNotice />);
    expect(
      await screen.findByText(/opens with your current passphrase/i),
    ).toBeInTheDocument();
  });

  it("says why it matters anyway: it is a stale copy", async () => {
    withStatus(true);
    renderWithQueryClient(<PremigrateBackupNotice />);
    expect(
      await screen.findByText(/keeps living inside this copy/i),
    ).toBeInTheDocument();
  });
});

describe("removing it", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it("offers a way out", async () => {
    withStatus(true);
    renderWithQueryClient(<PremigrateBackupNotice />);
    expect(await screen.findByRole("button", DELETE)).toBeInTheDocument();
  });

  it("asks once before doing anything irreversible", async () => {
    withStatus(true);
    renderWithQueryClient(<PremigrateBackupNotice />);
    await userEvent.click(await screen.findByRole("button", DELETE));

    expect(screen.getByText(/permanently delete it\?/i)).toBeInTheDocument();
    const called = vi
      .mocked(globalThis.fetch)
      .mock.calls.some(([url]) =>
        String(url).includes("/vault/discard-premigrate-backup"),
      );
    expect(called).toBe(false);
  });

  it("backs out without touching anything", async () => {
    withStatus(true);
    renderWithQueryClient(<PremigrateBackupNotice />);
    await userEvent.click(await screen.findByRole("button", DELETE));
    await userEvent.click(screen.getByRole("button", { name: /^keep$/i }));

    expect(await screen.findByRole("button", DELETE)).toBeInTheDocument();
    const called = vi
      .mocked(globalThis.fetch)
      .mock.calls.some(([url]) =>
        String(url).includes("/vault/discard-premigrate-backup"),
      );
    expect(called).toBe(false);
  });

  it("asks the backend once confirmed", async () => {
    withStatus(true);
    renderWithQueryClient(<PremigrateBackupNotice />);
    await userEvent.click(await screen.findByRole("button", DELETE));
    await userEvent.click(screen.getByRole("button", { name: /^delete$/i }));

    await waitFor(() => {
      const called = vi
        .mocked(globalThis.fetch)
        .mock.calls.some(([url]) =>
          String(url).includes("/vault/discard-premigrate-backup"),
        );
      expect(called).toBe(true);
    });
  });

  it("stays quiet when it worked", async () => {
    withStatus(true, { removed: true, reason: "" });
    renderWithQueryClient(<PremigrateBackupNotice />);
    await userEvent.click(await screen.findByRole("button", DELETE));
    await userEvent.click(screen.getByRole("button", { name: /^delete$/i }));

    await waitFor(() => {
      expect(screen.queryByRole("alert")).toBeNull();
    });
  });

  it("does NOT claim success for a file it could not delete", async () => {
    withStatus(true, { removed: false, reason: "in_use" });
    renderWithQueryClient(<PremigrateBackupNotice />);
    await userEvent.click(await screen.findByRole("button", DELETE));
    await userEvent.click(screen.getByRole("button", { name: /^delete$/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/still on disk/i);
  });
});

/**
 * Same defect PlaintextBackupNotice and OrphanedCopyNotice were both fixed
 * for: the confirm row replaces the trigger it grew out of, so an unhelped
 * keyboard user asking to delete a full copy of the vault loses focus to
 * <body> with no Escape.
 */
describe("answering the delete question from the keyboard", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it("focuses the SAFE choice when the question opens", async () => {
    const user = userEvent.setup();
    withStatus(true);
    renderWithQueryClient(<PremigrateBackupNotice />);
    await user.click(await screen.findByRole("button", DELETE));

    expect(screen.getByRole("button", { name: /^keep$/i })).toHaveFocus();
    // Ground, twice over: not <body> (the unfixed behaviour) and not the
    // destructive button, which would put an irreversible delete under a
    // reflexive Enter.
    expect(document.activeElement).not.toBe(document.body);
    expect(screen.getByRole("button", { name: /^delete$/i })).not.toHaveFocus();
  });

  it("Escape backs out and hands focus back to the trigger", async () => {
    const user = userEvent.setup();
    withStatus(true);
    renderWithQueryClient(<PremigrateBackupNotice />);
    await user.click(await screen.findByRole("button", DELETE));
    await user.keyboard("{Escape}");

    const trigger = await screen.findByRole("button", DELETE);
    expect(trigger).toHaveFocus();
    // Ground: backing out never asked the backend to delete anything.
    const called = vi
      .mocked(globalThis.fetch)
      .mock.calls.some(([url]) =>
        String(url).includes("/vault/discard-premigrate-backup"),
      );
    expect(called).toBe(false);
  });

  it("keeping it hands focus back to the trigger too", async () => {
    // Ground for the Escape test: the same return happens on the button
    // path, so the trigger is genuinely refocusable and the key handler is
    // not the only thing holding this together.
    const user = userEvent.setup();
    withStatus(true);
    renderWithQueryClient(<PremigrateBackupNotice />);
    await user.click(await screen.findByRole("button", DELETE));
    await user.click(screen.getByRole("button", { name: /^keep$/i }));

    expect(await screen.findByRole("button", DELETE)).toHaveFocus();
  });
});
