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
import { useErrorStore } from "@/lib/errors";
import { mockFetch } from "@/test/mocks/api";
import { delayRoute } from "@/test/helpers/delayRoute";
import { mockFetchUnreachable } from "@/test/helpers/fetchUnreachable";
import { expectNothingOnceAnswered } from "@/test/helpers/vaultStatusSettled";

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
    // Asserted only once the status query has ANSWERED: an absence checked
    // on the first render passes before the answer exists (IP-11b).
    await expectNothingOnceAnswered(renderWithQueryClient(<PlaintextBackupNotice />));
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
    // Asserted only once the status query has ANSWERED: an absence checked
    // on the first render passes before the answer exists (IP-11b).
    await expectNothingOnceAnswered(renderWithQueryClient(<PlaintextBackupNotice />));
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

describe("PlaintextBackupNotice - the file that will never come free", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it("does not tell you to close a program you cannot close", async () => {
    /**
     * K-49. `shred` refuses a file whose bytes answer to a second name,
     * because overwriting it would destroy whatever that name belongs to.
     * That refusal used to arrive in the same list as "something has the file
     * open", so the screen told people to close a program and retry - and
     * when retrying never worked, to delete the file themselves. Deleting it
     * removes ONE name and leaves the whole unencrypted database readable
     * under the other. The wrong sentence was worse than no sentence.
     */
    const user = userEvent.setup();
    mockFetch({
      "/vault/status": {
        body: {
          initialized: true,
          unlocked: true,
          plaintext_backups: ["app.db.plain.bak-1700000000"],
        },
      },
      "/vault/discard-plaintext-backup": {
        body: {
          removed: 0,
          left: [],
          shared: ["app.db.plain.bak-1700000000"],
        },
      },
    });
    renderWithQueryClient(<PlaintextBackupNotice />);

    await user.click(
      await screen.findByRole("button", { name: /delete the unencrypted/i }),
    );
    await user.click(await screen.findByRole("button", { name: /^delete$/i }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/shares its contents/i);
    // And the one instruction that would have made things worse is absent.
    expect(alert.textContent).not.toMatch(/has the file open/i);
  });
});

/**
 * The confirm row replaces its own trigger, which unmounts the element the
 * keyboard was standing on. Without help, focus drops to <body> and the
 * question - delete the only unencrypted fallback copy of the whole database
 * - is reachable only by tabbing back in from the top of the document, with
 * no Escape. NotebookPanel's NoteRow already solved this for an undoable
 * delete; this one is not undoable at all.
 */
describe("answering the delete question from the keyboard", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it("focuses the SAFE choice when the question opens", async () => {
    const user = userEvent.setup();
    withStatus([BACKUP]);
    renderWithQueryClient(<PlaintextBackupNotice />);
    await user.click(
      await screen.findByRole("button", { name: /delete the unencrypted/i }),
    );

    expect(screen.getByRole("button", { name: /^keep$/i })).toHaveFocus();
    // Ground, twice over: not <body> (the unfixed behaviour) and not the
    // destructive button, which would put an irreversible delete under a
    // reflexive Enter.
    expect(document.activeElement).not.toBe(document.body);
    expect(screen.getByRole("button", { name: /^delete$/i })).not.toHaveFocus();
  });

  it("Escape backs out and hands focus back to the trigger", async () => {
    const user = userEvent.setup();
    withStatus([BACKUP]);
    renderWithQueryClient(<PlaintextBackupNotice />);
    await user.click(
      await screen.findByRole("button", { name: /delete the unencrypted/i }),
    );
    await user.keyboard("{Escape}");

    const trigger = await screen.findByRole(
      "button", { name: /delete the unencrypted/i },
    );
    expect(trigger).toHaveFocus();
    // Ground: backing out never asked the backend to delete anything.
    const called = vi
      .mocked(globalThis.fetch)
      .mock.calls.some(([url]) =>
        String(url).includes("/vault/discard-plaintext-backup"),
      );
    expect(called).toBe(false);
  });

  it("keeping it hands focus back to the trigger too", async () => {
    // Ground for the Escape test: the same return happens on the button
    // path, so the trigger is genuinely refocusable and the key handler is
    // not the only thing holding this together.
    const user = userEvent.setup();
    withStatus([BACKUP]);
    renderWithQueryClient(<PlaintextBackupNotice />);
    await user.click(
      await screen.findByRole("button", { name: /delete the unencrypted/i }),
    );
    await user.click(screen.getByRole("button", { name: /^keep$/i }));

    expect(
      await screen.findByRole("button", { name: /delete the unencrypted/i }),
    ).toHaveFocus();
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
        "/vault/status": { body: { initialized: true, unlocked: true, orphaned_copy: false,
          plaintext_backups: [BACKUP] } },
        "/vault/discard-plaintext-backup": { status: 500, body: { detail: "boom" } },
      });
      renderWithQueryClient(<PlaintextBackupNotice />);
      await userEvent.click(await screen.findByRole(
        "button", { name: /delete the unencrypted/i }));
      await userEvent.click(
        screen.getByRole("button", { name: /^delete$/i }));
      await waitFor(() => expect(errorCount()).toBe(1));
    });

    it("tells the user when the vault is locked", async () => {
      // POSITIVE CONTROL. 423 is the likeliest of these in practice - the
      // vault locks on idle while the notice is still on screen - and it
      // travels a different path through the client than a 500.
      mockFetch({
        "/vault/status": { body: { initialized: true, unlocked: true, orphaned_copy: false,
          plaintext_backups: [BACKUP] } },
        "/vault/discard-plaintext-backup": { status: 423, body: { detail: "vault_locked" } },
      });
      renderWithQueryClient(<PlaintextBackupNotice />);
      await userEvent.click(await screen.findByRole(
        "button", { name: /delete the unencrypted/i }));
      await userEvent.click(
        screen.getByRole("button", { name: /^delete$/i }));
      await waitFor(() => expect(errorCount()).toBe(1));
    });

    it("says nothing when the deletion succeeds", async () => {
      // GROUND CONTROL. Without it a handler that reports on every outcome
      // passes both tests above and puts an error on screen for work that
      // went fine.
      mockFetch({
        "/vault/status": { body: { initialized: true, unlocked: true, orphaned_copy: false,
          plaintext_backups: [BACKUP] } },
        "/vault/discard-plaintext-backup": { body: { removed: 1, left: [] } },
      });
      renderWithQueryClient(<PlaintextBackupNotice />);
      await userEvent.click(await screen.findByRole(
        "button", { name: /delete the unencrypted/i }));
      await userEvent.click(
        screen.getByRole("button", { name: /^delete$/i }));
      await waitFor(() => expect(
        vi.mocked(globalThis.fetch).mock.calls.some(([url]) =>
          String(url).includes("/vault/discard-plaintext-backup"))).toBe(true));
      expect(errorCount()).toBe(0);
    });
  });
});

/**
 * IP-11b. The gate on this notice used to be `status.data?.plaintext_backups`, so a
 * status query that FAILED rendered the same nothing as a clean disk, and no
 * console call exists in this app to leave a trace of it. These tests drive
 * /vault/status itself, at the fetch boundary, into every state the real
 * useVaultStatus hook can be in, and read what the DOM shows for each.
 */
describe("PlaintextBackupNotice - when the check itself cannot run", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => vi.restoreAllMocks());

  const NOTICE = "plaintext-backup-notice";
  const CHECK = "plaintext-backup-check";
  const PRESENT = {
    initialized: true,
    unlocked: true,
    orphaned_copy: false,
    plaintext_backups: [BACKUP],
  };
  const ABSENT = {
    initialized: true,
    unlocked: true,
    orphaned_copy: false,
    plaintext_backups: [],
  };
  /** An older backend that does not know the field at all. */
  const OLDER = { initialized: true, unlocked: true };
  /** The warning's own sentences. None of them may leak into the check line. */
  const WARNING_WORDING = /without your passphrase|anyone who can read|unencrypted copy deleted|delete the unencrypted/i;

  it("T-57: a failed status query is said out loud, and does not read as all clear", async () => {
    mockFetch({ "/vault/status": { status: 500, body: { detail: "boom" } } });
    const { container } = renderWithQueryClient(<PlaintextBackupNotice />);

    const line = await screen.findByText(/could not check for an unencrypted copy of the database/i);
    expect(line).toBe(screen.getByTestId(CHECK));
    expect(line).toHaveAttribute("data-state", "error");
    // Not silence: the whole defect was that this container stayed empty.
    expect(container).not.toBeEmptyDOMElement();
    // Not the warning either: none of its furniture and none of its wording.
    expect(screen.queryByTestId(NOTICE)).not.toBeInTheDocument();
    expect(screen.queryByRole("region")).not.toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(container.querySelector("svg")).toBeNull();
    expect(line.textContent).not.toMatch(WARNING_WORDING);
    // And not the one sentence the absent branch can say, which would
    // claim a deletion on an answer that never arrived.
    expect(screen.queryByText(/unencrypted copy deleted/i)).not.toBeInTheDocument();
  });

  it("T-57: the same line when the backend cannot be reached at all", async () => {
    // fetch itself rejects before any status code exists. request() turns
    // that into NETWORK_ERROR, a different path through the client than a
    // 500 takes and the likelier one in practice.
    mockFetchUnreachable();
    renderWithQueryClient(<PlaintextBackupNotice />);

    // findByText, not findByTestId: the pending line carries the same
    // testid and is on screen before the rejection lands.
    const line = await screen.findByText(/could not check for an unencrypted copy of the database/i);
    expect(line).toBe(screen.getByTestId(CHECK));
    expect(line).toHaveAttribute("data-state", "error");
    expect(line.textContent).not.toMatch(WARNING_WORDING);
    expect(screen.queryByTestId(NOTICE)).not.toBeInTheDocument();
  });

  it("holds a quiet placeholder until the answer lands, then shows the truth", async () => {
    const fetchMock = mockFetch({ "/vault/status": { body: PRESENT } });
    const release = delayRoute(fetchMock, "GET", "/vault/status", { body: PRESENT });
    const { container } = renderWithQueryClient(<PlaintextBackupNotice />);

    const line = screen.getByTestId(CHECK);
    expect(line).toHaveAttribute("data-state", "pending");
    expect(line.textContent).toMatch(/checking for an unencrypted copy of the database/i);
    // Quiet: no bordered section, no icon, no alert, and no verdict in
    // either direction while the answer is still on its way.
    expect(screen.queryByRole("region")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(container.querySelector("svg")).toBeNull();
    expect(line.textContent).not.toMatch(/could not/i);
    expect(line.textContent).not.toMatch(WARNING_WORDING);
    expect(screen.queryByTestId(NOTICE)).not.toBeInTheDocument();

    release();
    expect(await screen.findByTestId(NOTICE)).toBeInTheDocument();
    expect(screen.queryByTestId(CHECK)).not.toBeInTheDocument();
  });

  it("T-57z GROUND: a status query that answered with nothing to report renders nothing at all", async () => {
    // The settled signal comes from the real query cache, not from the DOM:
    // an empty container before the answer would pass this on a component
    // that never renders anything. See helpers/vaultStatusSettled.ts.
    mockFetch({ "/vault/status": { body: ABSENT } });
    await expectNothingOnceAnswered(renderWithQueryClient(<PlaintextBackupNotice />));
  });

  it("T-57z GROUND: an older backend that does not know the field renders nothing at all", async () => {
    mockFetch({ "/vault/status": { body: OLDER } });
    await expectNothingOnceAnswered(renderWithQueryClient(<PlaintextBackupNotice />));
  });

  it("T-57p POSITIVE: with the file there, the warning renders as before and no check line", async () => {
    mockFetch({ "/vault/status": { body: PRESENT } });
    renderWithQueryClient(<PlaintextBackupNotice />);

    expect(await screen.findByTestId(NOTICE)).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Unencrypted database backup" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Unencrypted copy on disk" })).toBeInTheDocument();
    expect(screen.getByText(BACKUP)).toBeInTheDocument();
    expect(screen.getByText(/without your passphrase/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /delete the unencrypted/i })).toBeInTheDocument();

    expect(screen.queryByTestId(CHECK)).not.toBeInTheDocument();
  });

  /** A 200 with this body, for the one call the fetch mock answers differently. */
  function jsonResponse(body: unknown): Response {
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }

  it("T-57: a deletion whose confirming re-check fails says so, not 'deleted' and not the stale warning", async () => {
    // A successful delete invalidates /vault/status. If THAT refetch fails,
    // TanStack keeps the old list in `data` (naming the file that was just
    // shredded) while the query sits in error. The old gate re-painted that
    // stale warning; the absent branch would have said "deleted" on an
    // answer it never received. The first status read succeeds and lists
    // the file; every later one fails.
    const fetchMock = mockFetch({
      "/vault/status": { status: 500, body: { detail: "boom" } },
      "/vault/discard-plaintext-backup": { body: { removed: 1, left: [] } },
    });
    fetchMock.mockImplementationOnce(async () => jsonResponse(PRESENT));
    renderWithQueryClient(<PlaintextBackupNotice />);
    await userEvent.click(
      await screen.findByRole("button", { name: /delete the unencrypted/i }),
    );
    await userEvent.click(screen.getByRole("button", { name: /^delete$/i }));

    const line = await screen.findByText(/could not check for an unencrypted copy of the database/i);
    expect(line).toBe(screen.getByTestId(CHECK));
    expect(line).toHaveAttribute("data-state", "error");
    expect(screen.queryByText(/unencrypted copy deleted/i)).not.toBeInTheDocument();
    expect(screen.queryByTestId(NOTICE)).not.toBeInTheDocument();
    expect(screen.queryByText(BACKUP)).not.toBeInTheDocument();
  });

  it("keeps its one confirmation: a deletion the re-check confirms says 'deleted' and nothing else", async () => {
    // POSITIVE CONTROL for the absent branch only this notice has, so the
    // four-way split is shown not to have swallowed it. Same first-call
    // trick: the file is there, the delete succeeds, the re-check finds
    // nothing.
    const fetchMock = mockFetch({
      "/vault/status": { body: ABSENT },
      "/vault/discard-plaintext-backup": { body: { removed: 1, left: [] } },
    });
    fetchMock.mockImplementationOnce(async () => jsonResponse(PRESENT));
    renderWithQueryClient(<PlaintextBackupNotice />);
    await userEvent.click(
      await screen.findByRole("button", { name: /delete the unencrypted/i }),
    );
    await userEvent.click(screen.getByRole("button", { name: /^delete$/i }));

    expect(await screen.findByText("Unencrypted copy deleted.")).toBeInTheDocument();
    expect(screen.queryByTestId(NOTICE)).not.toBeInTheDocument();
    expect(screen.queryByTestId(CHECK)).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
