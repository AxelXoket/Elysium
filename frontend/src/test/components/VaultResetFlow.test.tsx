/**
 * VaultResetFlow.test.tsx - the lock screen's "forgotten passphrase" door.
 *
 * There is no recovery, so the only honest option is to wipe the vault and
 * start over. That makes this the single most destructive control in the
 * app, and the point of every test below is safety: the wipe must be
 * reachable only through an exact typed phrase, must never fire by opening
 * the panel or by cancelling it, and the ordinary unlock path directly next
 * to it must keep working untouched.
 *
 * RESET_CONFIRM_PHRASE is imported from the component rather than retyped
 * here, so this test file tracks whatever VaultGate.tsx actually sends
 * rather than a copy that could drift. It is no longer an assumed value:
 * backend/routers/vault.py's RESET_CONFIRMATION_PHRASE is the true one and
 * the two agree today, but the frontend copy is what this screen sends and
 * therefore what these tests must exercise.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { VaultGate, RESET_CONFIRM_PHRASE } from "@/components/vault/VaultGate";

interface VaultSim {
  initialized: boolean;
  unlocked: boolean;
  passphrase: string | null;
  resetOk: boolean;
  /** What the sweep could not remove. The real route answers 200 with this
   *  non-empty when the DELETION was partial, which is a different thing
   *  from the request failing - and the difference is the whole point. */
  resetLeft?: string[];
}

/** The database file's own name, which is what /vault/reset reports in `left`
 *  when the sweep could not remove it: backend/config.py's DB_PATH ends in
 *  app.db, and backend/tests/test_vault_reset_hardening.py asserts
 *  `db_path.name in body["left"]` for exactly that case. The fake below
 *  branches on it because the real route does. */
const DB_FILE_NAME = "app.db";

/** Same stateful backend stand-in shape as VaultGate.test.tsx, extended with
 *  /vault/reset: on a COMPLETE wipe it drops the sim back to first-run, which
 *  is what the real route leaves behind (a boot-state transition, like init).
 *  A partial wipe is not that, and the rule for it is spelled out inline. */
function stubVaultFetch(sim: VaultSim) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const body = init?.body ? JSON.parse(String(init.body)) : {};
      const json = (data: unknown, status = 200) =>
        new Response(JSON.stringify(data), { status });

      if (url.endsWith("/vault/status")) {
        return json({ initialized: sim.initialized, unlocked: sim.unlocked });
      }
      if (url.endsWith("/vault/unlock")) {
        if (body.passphrase !== sim.passphrase) {
          return json({ detail: "wrong_passphrase" }, 401);
        }
        sim.unlocked = true;
        return json({ ok: true });
      }
      if (url.endsWith("/vault/reset")) {
        if (!sim.resetOk) {
          return json({ detail: "vault_reset_failed" }, 500);
        }
        const left = sim.resetLeft ?? [];
        // What the vault's boot state actually is after this call, which is
        // NOT "always first-run". _reset_vault_sync in backend/routers/
        // vault.py sweeps the database, then checks whether app.db is still
        // on disk, and when it IS the identity files are held back
        // ENTIRELY - the whole reason that branch exists is that destroying
        // salt.bin/verifier.bin over a database that survived would brick a
        // vault that could otherwise still be opened. /vault/status derives
        // `initialized` from those two files existing (crypto.py's
        // is_initialized), so a reset that could not remove the database
        // leaves the vault initialised AND unlockable with the old
        // passphrase. Sweeping first does not imply succeeding; the
        // database surviving is the condition, not the ordering.
        const dbSurvives = left.includes(DB_FILE_NAME);
        // When the database did go, the identity sweep ran, and only the
        // names reported back in `left` are still there. is_initialized
        // wants BOTH files, and the encrypted-database recovery branch
        // cannot apply with no database left to classify.
        const identitySurvives =
          dbSurvives ||
          (left.includes("salt.bin") && left.includes("verifier.bin"));
        sim.initialized = identitySurvives;
        sim.unlocked = false;
        // Unlocking needs the database and the identity that opens it. Only
        // the held-back case keeps both, and keeping the passphrase here is
        // what lets a test prove the vault was not bricked.
        sim.passphrase = dbSurvives ? sim.passphrase : null;
        return json({ ok: left.length === 0, left });
      }
      return json({}, 404);
    }),
  );
}

function fetchUrls(): string[] {
  return (fetch as ReturnType<typeof vi.fn>).mock.calls.map((c) => String(c[0]));
}

function resetCallCount(): number {
  return fetchUrls().filter((u) => u.endsWith("/vault/reset")).length;
}

async function openLockScreen(sim: VaultSim) {
  stubVaultFetch(sim);
  renderWithQueryClient(<VaultGate>{APP_MARKER}</VaultGate>);
  await screen.findByText("Elysium is locked");
}

const APP_MARKER = <div data-testid="app-root">app</div>;

/** The first-run stage's own heading. VaultGate has no fourth stage for a
 *  reset: a complete wipe simply makes /vault/status answer
 *  initialized: false and the gate lands here. Naming it once keeps the
 *  "did we go to setup" assertions below reading the same thing the
 *  complete-wipe test waits for. */
const SETUP_HEADING = "Protect your world";

describe("VaultGate lock screen - forgot passphrase / reset", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows a forgot-passphrase control distinct from Unlock", async () => {
    await openLockScreen({
      initialized: true,
      unlocked: false,
      passphrase: "right-horse-42",
      resetOk: true,
    });

    expect(
      screen.getByRole("button", { name: "Unlock" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Forgot your passphrase?" }),
    ).toBeInTheDocument();
  });

  it("opening the reset panel sends no destructive request", async () => {
    const user = userEvent.setup();
    await openLockScreen({
      initialized: true,
      unlocked: false,
      passphrase: "right-horse-42",
      resetOk: true,
    });

    await user.click(
      screen.getByRole("button", { name: "Forgot your passphrase?" }),
    );

    await screen.findByRole("button", { name: "Delete everything" });
    expect(resetCallCount()).toBe(0);
  });

  it("autofocuses the safe Cancel control on open, not the destructive one", async () => {
    const user = userEvent.setup();
    await openLockScreen({
      initialized: true,
      unlocked: false,
      passphrase: "right-horse-42",
      resetOk: true,
    });

    await user.click(
      screen.getByRole("button", { name: "Forgot your passphrase?" }),
    );

    const cancelButton = await screen.findByRole("button", { name: "Cancel" });
    await waitFor(() => expect(document.activeElement).toBe(cancelButton));
  });

  it("keeps the destructive control disabled on an empty confirmation", async () => {
    const user = userEvent.setup();
    await openLockScreen({
      initialized: true,
      unlocked: false,
      passphrase: "right-horse-42",
      resetOk: true,
    });

    await user.click(
      screen.getByRole("button", { name: "Forgot your passphrase?" }),
    );
    const destructive = await screen.findByRole("button", {
      name: "Delete everything",
    });
    expect(destructive).toBeDisabled();
  });

  it("rejects a near-miss confirmation phrase and sends nothing", async () => {
    const user = userEvent.setup();
    await openLockScreen({
      initialized: true,
      unlocked: false,
      passphrase: "right-horse-42",
      resetOk: true,
    });

    await user.click(
      screen.getByRole("button", { name: "Forgot your passphrase?" }),
    );
    const input = await screen.findByLabelText(
      `Type "${RESET_CONFIRM_PHRASE}" to continue`,
    );
    // Lowercased near miss - not the ground: a completely wrong string would
    // prove nothing about case sensitivity, which is the actual boundary.
    await user.type(input, RESET_CONFIRM_PHRASE.toLowerCase());

    const destructive = screen.getByRole("button", {
      name: "Delete everything",
    });
    expect(destructive).toBeDisabled();

    // Enter in the field still reaches the form's submit handler even with
    // the button disabled - the real guard has to be in that handler, and
    // this is what proves it is there rather than only on the button.
    await user.type(input, "{Enter}");
    expect(resetCallCount()).toBe(0);
  });

  it("cancelling sends nothing and returns to the ordinary lock screen with focus restored", async () => {
    const user = userEvent.setup();
    await openLockScreen({
      initialized: true,
      unlocked: false,
      passphrase: "right-horse-42",
      resetOk: true,
    });

    const forgotButton = screen.getByRole("button", {
      name: "Forgot your passphrase?",
    });
    await user.click(forgotButton);
    await screen.findByRole("button", { name: "Delete everything" });

    await user.click(screen.getByRole("button", { name: "Cancel" }));

    // Back on the ordinary lock screen.
    expect(
      await screen.findByRole("button", { name: "Unlock" }),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(document.activeElement).toBe(
        screen.getByRole("button", { name: "Forgot your passphrase?" }),
      ),
    );

    // The one assertion that actually proves "sent nothing": the mock, not
    // just the UI having gone back to normal.
    expect(resetCallCount()).toBe(0);
  });

  it("Escape cancels the panel and returns focus to its trigger", async () => {
    const user = userEvent.setup();
    await openLockScreen({
      initialized: true,
      unlocked: false,
      passphrase: "right-horse-42",
      resetOk: true,
    });

    await user.click(
      screen.getByRole("button", { name: "Forgot your passphrase?" }),
    );
    await screen.findByRole("button", { name: "Delete everything" });

    await user.keyboard("{Escape}");

    // The lock screen's own form is torn down and rebuilt across this swap
    // (same as CreatePassphrase's migration-notice swap does), so the
    // trigger button is a NEW element afterwards - re-queried rather than
    // compared against the pre-Escape handle, which would fail on identity
    // alone even when focus landed correctly.
    expect(
      await screen.findByRole("button", { name: "Unlock" }),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(document.activeElement).toBe(
        screen.getByRole("button", { name: "Forgot your passphrase?" }),
      ),
    );
    expect(resetCallCount()).toBe(0);
  });

  it("sends the destructive request exactly once on an exact match, and lands on first-run setup", async () => {
    const user = userEvent.setup();
    const sim: VaultSim = {
      initialized: true,
      unlocked: false,
      passphrase: "right-horse-42",
      resetOk: true,
    };
    await openLockScreen(sim);

    await user.click(
      screen.getByRole("button", { name: "Forgot your passphrase?" }),
    );
    const input = await screen.findByLabelText(
      `Type "${RESET_CONFIRM_PHRASE}" to continue`,
    );
    await user.type(input, RESET_CONFIRM_PHRASE);
    await user.click(screen.getByRole("button", { name: "Delete everything" }));

    // Reuses VaultGate's OWN existing setup stage - no new fourth state.
    // This is also the positive control for the two "not the setup screen"
    // assertions below: the heading they query for does appear when the
    // wipe really was complete.
    await screen.findByText(SETUP_HEADING);
    expect(resetCallCount()).toBe(1);
  });

  it("keeps the panel open and reports the error when the reset request fails", async () => {
    const user = userEvent.setup();
    await openLockScreen({
      initialized: true,
      unlocked: false,
      passphrase: "right-horse-42",
      resetOk: false,
    });

    await user.click(
      screen.getByRole("button", { name: "Forgot your passphrase?" }),
    );
    const input = await screen.findByLabelText(
      `Type "${RESET_CONFIRM_PHRASE}" to continue`,
    );
    await user.type(input, RESET_CONFIRM_PHRASE);
    await user.click(screen.getByRole("button", { name: "Delete everything" }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    // Still on the reset panel, not silently dropped back to the lock form.
    expect(
      screen.getByRole("button", { name: "Cancel" }),
    ).toBeInTheDocument();
    expect(resetCallCount()).toBe(1);
  });

  it("positive control: the ordinary unlock path still works with the reset control present", async () => {
    const user = userEvent.setup();
    await openLockScreen({
      initialized: true,
      unlocked: false,
      passphrase: "right-horse-42",
      resetOk: true,
    });

    await user.type(screen.getByLabelText("Passphrase"), "right-horse-42");
    await user.click(screen.getByRole("button", { name: "Unlock" }));

    expect(await screen.findByTestId("app-root")).toBeInTheDocument();
    expect(resetCallCount()).toBe(0);
  });
});

describe("a reset that only partly worked", () => {
  // The route answers 200 even when the sweep left files behind, because the
  // REQUEST succeeded and the DELETION did not. The screen used to strip
  // that list in its own zod schema and move straight on, so somebody was
  // told every trace of their vault was gone while their own files were
  // still readable on disk. On this route that is the worst possible lie.
  async function wipe(user: ReturnType<typeof userEvent.setup>) {
    await user.click(await screen.findByRole("button",
      { name: /forgot your passphrase/i }));
    await user.type(screen.getByLabelText(/type/i), RESET_CONFIRM_PHRASE);
    await user.click(screen.getByRole("button",
      { name: /delete everything/i }));
  }

  function partial(left: string[]): VaultSim {
    return { initialized: true, unlocked: false, passphrase: "right-horse-42",
             resetOk: true, resetLeft: left };
  }

  it("names what survived instead of claiming it all went", async () => {
    const user = userEvent.setup();
    await openLockScreen(partial(["voice cache", "uploads"]));
    await wipe(user);

    const left = await screen.findByTestId("reset-left");
    expect(left.textContent).toMatch(/voice cache/);
    expect(left.textContent).toMatch(/uploads/);
  });

  it("does not send the user on to setup while files remain", async () => {
    const user = userEvent.setup();
    await openLockScreen(partial(["uploads"]));
    await wipe(user);

    await screen.findByTestId("reset-left");
    // The assertion that names the failure: the first-run screen is what a
    // reset normally swings to, and it must not swing there while survivors
    // are still on the panel. Live query, not a vacuous one - the complete
    // wipe test above finds this same heading after an ok:true reset.
    expect(screen.queryByText(SETUP_HEADING)).not.toBeInTheDocument();
    // And the app behind the gate is still not showing either.
    expect(screen.queryByTestId("app-root")).not.toBeInTheDocument();
  });

  it("a reset that could not remove the DATABASE leaves the vault openable and says so", async () => {
    // The case the "do not brick the vault" branch exists for, and the one
    // no test covered. app.db is held open (a hardlink, an indexer, a second
    // instance), so the route holds the identity files back and the user is
    // exactly where they started: locked, intact, able to unlock. They asked
    // for everything to be destroyed and effectively nothing was, so the
    // screen owes them the survivors and must not hand them a setup screen
    // for a vault that is still full.
    const user = userEvent.setup();
    const sim = partial([DB_FILE_NAME, `${DB_FILE_NAME}-wal`]);
    await openLockScreen(sim);
    await wipe(user);

    // 1. It does not imply success: the survivors are named, database first.
    const left = await screen.findByTestId("reset-left");
    expect(left.textContent).toMatch(/app\.db/);

    // 2. It does not send them to first-run setup. That screen offers to
    //    mint a new passphrase, and over a surviving encrypted database the
    //    backend refuses to (encrypted_db_without_identity) - a dead end
    //    presented as a fresh start.
    expect(screen.queryByText(SETUP_HEADING)).not.toBeInTheDocument();

    // 3. The vault is not bricked, proved by using it rather than by
    //    inspecting state: back out of the panel and the ORIGINAL passphrase
    //    still opens everything.
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await user.type(
      await screen.findByLabelText("Passphrase"),
      "right-horse-42",
    );
    await user.click(screen.getByRole("button", { name: "Unlock" }));
    expect(await screen.findByTestId("app-root")).toBeInTheDocument();
  });

  it("says nothing extra when the wipe really was complete", async () => {
    // Positive control. A warning that shows on a clean reset would train
    // somebody to ignore it on the one that matters.
    const user = userEvent.setup();
    await openLockScreen({ initialized: true, unlocked: false,
                           passphrase: "right-horse-42", resetOk: true });
    await wipe(user);

    await waitFor(() =>
      expect(screen.queryByTestId("reset-left")).not.toBeInTheDocument());
  });
});
