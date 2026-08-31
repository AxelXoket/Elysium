/**
 * A stale, encrypted snapshot from a migration that never finished cleanly -
 * app.db.premigrate.bak, reported by /vault/status and removable by a route.
 *
 * Two defects fixed here:
 *
 * 1. The discard route answers one of three reasons (not_present,
 *    different_key, in_use - the same vocabulary discard_orphaned_enc_tmp
 *    uses), and the component used to render one blanket sentence - the
 *    in_use one - for all three. different_key means the snapshot may be the
 *    only copy of something an older passphrase reached, which Elysium is
 *    protecting, not failing to delete - and the old sentence told the user
 *    to go close a program instead.
 *
 * 2. /vault/status also carries premigrate_backup_readable, mirroring
 *    orphaned_copy_readable, and the component ignored it - so the delete
 *    button was offered unconditionally and the "may belong to an older
 *    passphrase" fact was only ever discovered AFTER a refused click. These
 *    tests pin the three-way branch (readable / not readable / unknown while
 *    locked) the same way OrphanedCopyNotice.test.tsx pins its own.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";

import { PremigrateBackupNotice } from "@/components/settings/PremigrateBackupNotice";
import { useErrorStore } from "@/lib/errors";
import { mockFetch } from "@/test/mocks/api";

function withStatus(
  present: boolean,
  readable: boolean | null = true,
  discard?: unknown,
) {
  mockFetch({
    "/vault/status": {
      body: {
        initialized: true,
        unlocked: true,
        premigrate_backup: present,
        premigrate_backup_readable: readable,
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
    withStatus(false, null);
    renderWithQueryClient(<PremigrateBackupNotice />);
    await waitFor(() => {
      expect(screen.queryByTestId("premigrate-backup-notice")).toBeNull();
    });
  });

  it("survives a backend that does not know either field yet", async () => {
    // An older build answers /vault/status without premigrate_backup at all.
    // Parsing must not throw and take the whole settings tab down with it -
    // the optional()/nullish() on the schema fields is what this pins.
    mockFetch({
      "/vault/status": { body: { initialized: true, unlocked: true } },
    });
    renderWithQueryClient(<PremigrateBackupNotice />);
    await waitFor(() => {
      expect(screen.queryByTestId("premigrate-backup-notice")).toBeNull();
    });
  });
});

describe("a copy this vault can read", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it("says it is not a leak", async () => {
    withStatus(true, true);
    renderWithQueryClient(<PremigrateBackupNotice />);
    expect(
      await screen.findByText(/opens with your current passphrase/i),
    ).toBeInTheDocument();
  });

  it("says why it matters anyway: it is a stale copy", async () => {
    withStatus(true, true);
    renderWithQueryClient(<PremigrateBackupNotice />);
    expect(
      await screen.findByText(/keeps living inside this copy/i),
    ).toBeInTheDocument();
  });

  it("offers a way out", async () => {
    withStatus(true, true);
    renderWithQueryClient(<PremigrateBackupNotice />);
    expect(await screen.findByRole("button", DELETE)).toBeInTheDocument();
  });
});

describe("a copy this vault CANNOT read", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it("offers no delete button at all", async () => {
    // Not a disabled button with a tooltip - the backend refuses this case
    // outright (different_key), so a button implying a decision the user
    // cannot safely make has no honest disabled state either.
    withStatus(true, false);
    renderWithQueryClient(<PremigrateBackupNotice />);

    expect(
      await screen.findByTestId("premigrate-backup-notice"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", DELETE)).toBeNull();
  });

  it("says ahead of time that it may belong to an older passphrase", async () => {
    // The defect: this used to be discoverable only after a refused click.
    withStatus(true, false);
    renderWithQueryClient(<PremigrateBackupNotice />);
    expect(
      await screen.findByText(/only copy of chats this vault cannot show you/i),
    ).toBeInTheDocument();
  });

  it("does not also show the readable copy's text", async () => {
    // Ground for the branch: the two paragraphs are mutually exclusive.
    withStatus(true, false);
    renderWithQueryClient(<PremigrateBackupNotice />);
    await screen.findByTestId("premigrate-backup-notice");
    expect(screen.queryByText(/keeps living inside this copy/i)).toBeNull();
  });
});

describe("a locked vault (readable unknown)", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it("admits it does not know yet, and offers nothing", async () => {
    withStatus(true, null);
    renderWithQueryClient(<PremigrateBackupNotice />);

    expect(
      await screen.findByText(/unlock the vault to find out/i),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", DELETE)).toBeNull();
  });

  it("survives a backend that knows premigrate_backup but not the readable field", async () => {
    mockFetch({
      "/vault/status": {
        body: { initialized: true, unlocked: true, premigrate_backup: true },
      },
    });
    renderWithQueryClient(<PremigrateBackupNotice />);

    expect(
      await screen.findByTestId("premigrate-backup-notice"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", DELETE)).toBeNull();
  });
});

describe("removing it", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it("asks once before doing anything irreversible", async () => {
    withStatus(true, true);
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
    withStatus(true, true);
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
    withStatus(true, true);
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
    withStatus(true, true, { removed: true, reason: "" });
    renderWithQueryClient(<PremigrateBackupNotice />);
    await userEvent.click(await screen.findByRole("button", DELETE));
    await userEvent.click(screen.getByRole("button", { name: /^delete$/i }));

    await waitFor(() => {
      expect(screen.queryByRole("alert")).toBeNull();
    });
  });
});

/**
 * House rule: a distinct sentence per refusal reason needs a test per reason
 * plus one proving they differ. `in_use` and `different_key` are both
 * reachable in practice even though the button is gated on readable === true
 * (the backend re-checks the key at delete time, not just at status time);
 * `not_present` covers a race where the file is already gone.
 */
describe("a deletion that did not happen - one sentence per reason", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it("in_use: says something else has the file open", async () => {
    withStatus(true, true, { removed: false, reason: "in_use" });
    renderWithQueryClient(<PremigrateBackupNotice />);
    await userEvent.click(await screen.findByRole("button", DELETE));
    await userEvent.click(screen.getByRole("button", { name: /^delete$/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/still on disk/i);
  });

  it("different_key: does NOT say something has the file open", async () => {
    // The bug this defect describes: different_key used to render the
    // in_use sentence, sending someone to close a program that was never the
    // problem while Elysium was actually protecting a possibly-unique copy.
    withStatus(true, true, { removed: false, reason: "different_key" });
    renderWithQueryClient(<PremigrateBackupNotice />);
    await userEvent.click(await screen.findByRole("button", DELETE));
    await userEvent.click(screen.getByRole("button", { name: /^delete$/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/older passphrase/i);
    expect(alert).not.toHaveTextContent(/something else has the file open/i);
  });

  it("not_present: says it was already gone", async () => {
    withStatus(true, true, { removed: false, reason: "not_present" });
    renderWithQueryClient(<PremigrateBackupNotice />);
    await userEvent.click(await screen.findByRole("button", DELETE));
    await userEvent.click(screen.getByRole("button", { name: /^delete$/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/already gone/i);
  });

  it("the three reasons render three DIFFERENT sentences", async () => {
    const reasons = ["in_use", "different_key", "not_present"] as const;
    const texts: string[] = [];

    for (const reason of reasons) {
      vi.restoreAllMocks();
      withStatus(true, true, { removed: false, reason });
      const { unmount } = renderWithQueryClient(<PremigrateBackupNotice />);
      await userEvent.click(await screen.findByRole("button", DELETE));
      await userEvent.click(screen.getByRole("button", { name: /^delete$/i }));
      const alert = await screen.findByRole("alert");
      texts.push(alert.textContent ?? "");
      unmount();
    }

    expect(new Set(texts).size, "every reason must read differently").toBe(
      texts.length,
    );
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
    withStatus(true, true);
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
    withStatus(true, true);
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
    withStatus(true, true);
    renderWithQueryClient(<PremigrateBackupNotice />);
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
        "/vault/status": { body: { initialized: true, unlocked: true, premigrate_backup: true,
          premigrate_backup_readable: true } },
        "/vault/discard-premigrate-backup": { status: 500, body: { detail: "boom" } },
      });
      renderWithQueryClient(<PremigrateBackupNotice />);
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
        "/vault/status": { body: { initialized: true, unlocked: true, premigrate_backup: true,
          premigrate_backup_readable: true } },
        "/vault/discard-premigrate-backup": { status: 423, body: { detail: "vault_locked" } },
      });
      renderWithQueryClient(<PremigrateBackupNotice />);
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
        "/vault/status": { body: { initialized: true, unlocked: true, premigrate_backup: true,
          premigrate_backup_readable: true } },
        "/vault/discard-premigrate-backup": { body: { removed: true, reason: "", left: [] } },
      });
      renderWithQueryClient(<PremigrateBackupNotice />);
      await userEvent.click(await screen.findByRole("button", DELETE));
      await userEvent.click(
        screen.getByRole("button", { name: /^delete$/i }));
      await waitFor(() => expect(
        vi.mocked(globalThis.fetch).mock.calls.some(([url]) =>
          String(url).includes("/vault/discard-premigrate-backup"))).toBe(true));
      expect(errorCount()).toBe(0);
    });
  });
});
