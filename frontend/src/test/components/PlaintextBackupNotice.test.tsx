/**
 * A full, unencrypted copy of the database - and no way to know it was there.
 *
 * Migration keeps the pre-vault app.db as app.db.plain.bak-<ts>, deliberately:
 * if the move into the vault had verified wrong, that file is the only copy of
 * everything the user ever wrote. But it was then reported exactly once, in a
 * banner on the launch that migrated, and after that nothing in the app
 * mentioned it and nothing could remove it.
 *
 * These tests are about the difference between a moment and a state.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";

import { PlaintextBackupNotice } from "@/components/settings/PlaintextBackupNotice";
import { mockFetch } from "@/test/mocks/api";

const BACKUP = "app.db.plain.bak-20260101120000";
const SECOND = "app.db.plain.bak-20260202130000";


function withStatus(backups: string[], discard?: unknown) {
  mockFetch({
    "/vault/status": {
      body: {
        initialized: true,
        unlocked: true,
        orphaned_copy: false,
        plaintext_backups: backups,
      },
    },
    "/vault/discard-plaintext-backup": {
      body: discard ?? { removed: backups.length, left: [] },
    },
  });
}

describe("the unencrypted copy is a state, not a banner", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it("says so whenever one is on disk", async () => {
    withStatus([BACKUP]);
    renderWithQueryClient(<PlaintextBackupNotice />);

    expect(await screen.findByTestId("plaintext-backup-notice")).toBeInTheDocument();
    expect(screen.getByText(BACKUP)).toBeInTheDocument();
  });

  it("says the one thing that matters: no passphrase needed to read it", async () => {
    withStatus([BACKUP]);
    renderWithQueryClient(<PlaintextBackupNotice />);

    expect(
      await screen.findByText(/without your passphrase/i),
    ).toBeInTheDocument();
  });

  it("stays out of the way when there is nothing to warn about", async () => {
    withStatus([]);
    renderWithQueryClient(<PlaintextBackupNotice />);

    await waitFor(() => {
      expect(screen.queryByTestId("plaintext-backup-notice")).toBeNull();
    });
  });

  it("names every copy, not just the first", async () => {
    withStatus([BACKUP, SECOND]);
    renderWithQueryClient(<PlaintextBackupNotice />);

    expect(await screen.findByText(BACKUP)).toBeInTheDocument();
    expect(screen.getByText(SECOND)).toBeInTheDocument();
  });

  it("survives a backend that does not know the field yet", async () => {
    // An older build answers /vault/status without it. Parsing must not throw
    // and take the whole settings tab down with it.
    mockFetch({
      "/vault/status": {
        body: { initialized: true, unlocked: true, orphaned_copy: false },
      },
    });
    renderWithQueryClient(<PlaintextBackupNotice />);

    await waitFor(() => {
      expect(screen.queryByTestId("plaintext-backup-notice")).toBeNull();
    });
  });
});

describe("removing it", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it("offers a way out", async () => {
    withStatus([BACKUP]);
    renderWithQueryClient(<PlaintextBackupNotice />);

    expect(
      await screen.findByRole("button", { name: /delete the unencrypted/i }),
    ).toBeInTheDocument();
  });

  it("asks once before doing anything irreversible", async () => {
    // The backend overwrites before unlinking. One stray click would destroy
    // the only pre-vault copy with nothing to recover it from.
    withStatus([BACKUP]);
    renderWithQueryClient(<PlaintextBackupNotice />);
    await userEvent.click(
      await screen.findByRole("button", { name: /delete the unencrypted/i }),
    );

    expect(screen.getByText(/permanently delete it\?/i)).toBeInTheDocument();
    const called = vi
      .mocked(globalThis.fetch)
      .mock.calls.some(([url]) =>
        String(url).includes("/vault/discard-plaintext-backup"),
      );
    expect(called).toBe(false);
  });

  it("backs out without touching anything", async () => {
    withStatus([BACKUP]);
    renderWithQueryClient(<PlaintextBackupNotice />);
    await userEvent.click(
      await screen.findByRole("button", { name: /delete the unencrypted/i }),
    );
    await userEvent.click(screen.getByRole("button", { name: /^keep$/i }));

    expect(
      screen.getByRole("button", { name: /delete the unencrypted/i }),
    ).toBeInTheDocument();
    const called = vi
      .mocked(globalThis.fetch)
      .mock.calls.some(([url]) =>
        String(url).includes("/vault/discard-plaintext-backup"),
      );
    expect(called).toBe(false);
  });

  it("asks the backend once confirmed", async () => {
    withStatus([BACKUP]);
    renderWithQueryClient(<PlaintextBackupNotice />);
    await userEvent.click(
      await screen.findByRole("button", { name: /delete the unencrypted/i }),
    );
    await userEvent.click(screen.getByRole("button", { name: /^delete$/i }));

    await waitFor(() => {
      const called = vi
        .mocked(globalThis.fetch)
        .mock.calls.some(([url]) =>
          String(url).includes("/vault/discard-plaintext-backup"),
        );
      expect(called).toBe(true);
    });
  });

  it("stays quiet when everything went fine", async () => {
    // The alert was rendered unconditionally in an earlier cut and every test
    // still passed, because none of them checked the SUCCESS path for its
    // absence. A permanent "still readable on disk" warning is the opposite
    // of the message this component exists to deliver.
    withStatus([BACKUP], { removed: 1, left: [] });
    renderWithQueryClient(<PlaintextBackupNotice />);
    await userEvent.click(
      await screen.findByRole("button", { name: /delete the unencrypted/i }),
    );
    await userEvent.click(screen.getByRole("button", { name: /^delete$/i }));

    await waitFor(() => {
      expect(screen.queryByRole("alert")).toBeNull();
    });
  });

  it("does NOT claim success for a file it could not delete", async () => {
    // The route answers with what it failed to remove. Swallowing that would
    // tell the user the unencrypted copy is gone while it is still readable.
    withStatus([BACKUP], { removed: 0, left: [BACKUP] });
    renderWithQueryClient(<PlaintextBackupNotice />);
    await userEvent.click(
      await screen.findByRole("button", { name: /delete the unencrypted/i }),
    );
    await userEvent.click(screen.getByRole("button", { name: /^delete$/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/still readable on disk/i);
    expect(alert).toHaveTextContent(BACKUP);
  });
});
