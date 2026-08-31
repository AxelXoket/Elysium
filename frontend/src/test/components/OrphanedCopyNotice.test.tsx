/**
 * A second, complete copy of the vault - reported since it was added, and
 * rendered nowhere.
 *
 * `/vault/status` has carried `orphaned_copy` for a while. A grep across the
 * frontend found it in the schema, in tests, and in no component at all. So
 * an interrupted migration left a full duplicate on disk and the only trace
 * was a log line.
 *
 * The design question is not "show it" but "may the user delete it". This
 * copy is ENCRYPTED, so it is not a leak - it is either a duplicate of the
 * live database, or a vault under a passphrase we do not have. These tests
 * are mostly about that fork.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";

import { OrphanedCopyNotice } from "@/components/settings/OrphanedCopyNotice";
import { useErrorStore } from "@/lib/errors";
import { mockFetch } from "@/test/mocks/api";


function withStatus(
  orphan: boolean,
  readable: boolean | null,
  discard?: unknown,
) {
  mockFetch({
    "/vault/status": {
      body: {
        initialized: true,
        unlocked: true,
        orphaned_copy: orphan,
        orphaned_copy_readable: readable,
        plaintext_backups: [],
      },
    },
    "/vault/discard-orphaned-copy": {
      body: discard ?? { removed: true, reason: "" },
    },
  });
}

const DELETE = { name: /delete the duplicate/i } as const;

describe("the duplicate is visible at all", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it("says so when one is on disk", async () => {
    withStatus(true, true);
    renderWithQueryClient(<OrphanedCopyNotice />);
    expect(await screen.findByTestId("orphaned-copy-notice")).toBeInTheDocument();
  });

  it("stays out of the way when there is none", async () => {
    withStatus(false, null);
    renderWithQueryClient(<OrphanedCopyNotice />);
    await waitFor(() => {
      expect(screen.queryByTestId("orphaned-copy-notice")).toBeNull();
    });
  });

  it("does not frighten anyone: the copy is encrypted, and it says so", async () => {
    // Unlike the plaintext backup this is NOT a leak. Copy that reads like a
    // breach would push people into deleting something they may need.
    withStatus(true, true);
    renderWithQueryClient(<OrphanedCopyNotice />);
    expect(
      await screen.findByText(/not readable by anyone else/i),
    ).toBeInTheDocument();
  });
});

describe("a copy this vault can read", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it("offers to remove it", async () => {
    withStatus(true, true);
    renderWithQueryClient(<OrphanedCopyNotice />);
    expect(await screen.findByRole("button", DELETE)).toBeInTheDocument();
  });

  it("asks once before doing anything irreversible", async () => {
    withStatus(true, true);
    renderWithQueryClient(<OrphanedCopyNotice />);
    await userEvent.click(await screen.findByRole("button", DELETE));

    expect(screen.getByText(/permanently delete it\?/i)).toBeInTheDocument();
    expect(
      vi.mocked(globalThis.fetch).mock.calls.some(([url]) =>
        String(url).includes("/vault/discard-orphaned-copy"),
      ),
    ).toBe(false);
  });

  it("asks the backend once confirmed", async () => {
    withStatus(true, true);
    renderWithQueryClient(<OrphanedCopyNotice />);
    await userEvent.click(await screen.findByRole("button", DELETE));
    await userEvent.click(screen.getByRole("button", { name: /^delete$/i }));

    await waitFor(() => {
      expect(
        vi.mocked(globalThis.fetch).mock.calls.some(([url]) =>
          String(url).includes("/vault/discard-orphaned-copy"),
        ),
      ).toBe(true);
    });
  });
});

describe("a copy this vault CANNOT read", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it("offers no delete button at all", async () => {
    // Not a disabled button with a tooltip. There is no safe version of this
    // action: the file may be the only copy of chats under an older
    // passphrase, and a button implies a decision the user cannot yet make.
    withStatus(true, false);
    renderWithQueryClient(<OrphanedCopyNotice />);

    expect(await screen.findByTestId("orphaned-copy-notice")).toBeInTheDocument();
    expect(screen.queryByRole("button", DELETE)).toBeNull();
  });

  it("says what it might be instead of what to click", async () => {
    withStatus(true, false);
    renderWithQueryClient(<OrphanedCopyNotice />);
    expect(
      await screen.findByText(/only copy of chats this vault cannot show you/i),
    ).toBeInTheDocument();
  });
});

describe("a locked vault", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it("admits it does not know yet, and offers nothing", async () => {
    // null means "we did not look", which must not be shown as either answer.
    withStatus(true, null);
    renderWithQueryClient(<OrphanedCopyNotice />);

    expect(await screen.findByText(/unlock the vault to find out/i))
      .toBeInTheDocument();
    expect(screen.queryByRole("button", DELETE)).toBeNull();
  });

  it("survives a backend that does not know the field yet", async () => {
    mockFetch({
      "/vault/status": {
        body: { initialized: true, unlocked: true, orphaned_copy: true },
      },
    });
    renderWithQueryClient(<OrphanedCopyNotice />);

    expect(await screen.findByTestId("orphaned-copy-notice")).toBeInTheDocument();
    expect(screen.queryByRole("button", DELETE)).toBeNull();
  });
});

describe("a deletion that did not happen", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it("does not claim success when the file is held open", async () => {
    withStatus(true, true, { removed: false, reason: "in_use" });
    renderWithQueryClient(<OrphanedCopyNotice />);
    await userEvent.click(await screen.findByRole("button", DELETE));
    await userEvent.click(screen.getByRole("button", { name: /^delete$/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/still on disk/i);
  });

  it("stays quiet when it worked", async () => {
    withStatus(true, true, { removed: true, reason: "" });
    renderWithQueryClient(<OrphanedCopyNotice />);
    await userEvent.click(await screen.findByRole("button", DELETE));
    await userEvent.click(screen.getByRole("button", { name: /^delete$/i }));

    await waitFor(() => {
      expect(screen.queryByRole("alert")).toBeNull();
    });
  });
});

/**
 * Same defect as PlaintextBackupNotice, same fix: the confirm row replaces
 * its own trigger, so an unhelped keyboard user loses focus to <body> asking
 * to delete a second, irreversible copy of the vault.
 */
describe("answering the delete question from the keyboard", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it("focuses the SAFE choice when the question opens", async () => {
    const user = userEvent.setup();
    withStatus(true, true);
    renderWithQueryClient(<OrphanedCopyNotice />);
    await user.click(await screen.findByRole("button", DELETE));

    expect(screen.getByRole("button", { name: /^keep$/i })).toHaveFocus();
    expect(document.activeElement).not.toBe(document.body);
    expect(screen.getByRole("button", { name: /^delete$/i })).not.toHaveFocus();
  });

  it("Escape backs out and hands focus back to the trigger", async () => {
    const user = userEvent.setup();
    withStatus(true, true);
    renderWithQueryClient(<OrphanedCopyNotice />);
    await user.click(await screen.findByRole("button", DELETE));
    await user.keyboard("{Escape}");

    const trigger = await screen.findByRole("button", DELETE);
    expect(trigger).toHaveFocus();
    const called = vi
      .mocked(globalThis.fetch)
      .mock.calls.some(([url]) =>
        String(url).includes("/vault/discard-orphaned-copy"),
      );
    expect(called).toBe(false);
  });

  it("keeping it hands focus back to the trigger too", async () => {
    // Ground for the Escape test: the same return happens on the button
    // path, so the key handler is not the only thing holding this together.
    const user = userEvent.setup();
    withStatus(true, true);
    renderWithQueryClient(<OrphanedCopyNotice />);
    await user.click(await screen.findByRole("button", DELETE));
    await user.click(screen.getByRole("button", { name: /^keep$/i }));

    expect(await screen.findByRole("button", DELETE)).toHaveFocus();
  });


  describe("when the deletion fails", () => {
    // Four irreversible deletions, and none of them had an `onError`. No
    // MutationCache either, and the `role="alert"` paragraphs in these
    // notices report fields of a SUCCESSFUL response - so a 500 or a 423
    // said nothing at all. "The file is gone" and "nothing was even
    // attempted" looked identical to the one person who cannot check.
    //
    // COUNTED across both lists, and after a reset. The store dedups by
    // identity, so a leftover event from an earlier test would satisfy
    // "there is an error" without this change doing anything.
    function errorCount(): number {
      const s = useErrorStore.getState();
      return s.errors.length + s.queuedErrors.length;
    }

    beforeEach(() => useErrorStore.getState().clearAll());

    it("tells the user when the request is refused", async () => {
      mockFetch({
        "/vault/status": { body: { initialized: true, unlocked: true, orphaned_copy: true,
          orphaned_copy_readable: true, plaintext_backups: [] } },
        "/vault/discard-orphaned-copy": { status: 500, body: { detail: "boom" } },
      });
      renderWithQueryClient(<OrphanedCopyNotice />);
      await userEvent.click(await screen.findByRole("button", DELETE));
      await userEvent.click(
        screen.getByRole("button", { name: /^delete$/i }));
      await waitFor(() => expect(errorCount()).toBe(1));
    });

    it("tells the user when the vault is locked", async () => {
      // POSITIVE CONTROL. 423 is the likeliest of these in practice - the
      // vault locks on idle while the notice is still on screen - and it
      // travels a different path through the client than a 500.
      mockFetch({
        "/vault/status": { body: { initialized: true, unlocked: true, orphaned_copy: true,
          orphaned_copy_readable: true, plaintext_backups: [] } },
        "/vault/discard-orphaned-copy": { status: 423, body: { detail: "vault_locked" } },
      });
      renderWithQueryClient(<OrphanedCopyNotice />);
      await userEvent.click(await screen.findByRole("button", DELETE));
      await userEvent.click(
        screen.getByRole("button", { name: /^delete$/i }));
      await waitFor(() => expect(errorCount()).toBe(1));
    });

    it("says nothing when the deletion succeeds", async () => {
      // GROUND CONTROL. Without it a handler that reports on every outcome
      // passes both tests above and puts an error on screen for work that
      // went fine.
      mockFetch({
        "/vault/status": { body: { initialized: true, unlocked: true, orphaned_copy: true,
          orphaned_copy_readable: true, plaintext_backups: [] } },
        "/vault/discard-orphaned-copy": { body: { removed: true, reason: "", left: [] } },
      });
      renderWithQueryClient(<OrphanedCopyNotice />);
      await userEvent.click(await screen.findByRole("button", DELETE));
      await userEvent.click(
        screen.getByRole("button", { name: /^delete$/i }));
      await waitFor(() => expect(
        vi.mocked(globalThis.fetch).mock.calls.some(([url]) =>
          String(url).includes("/vault/discard-orphaned-copy"))).toBe(true));
      expect(errorCount()).toBe(0);
    });
  });
});
