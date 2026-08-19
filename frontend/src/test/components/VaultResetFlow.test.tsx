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
 * here - it is an ASSUMED value (backend/routers/vault.py has no /vault/reset
 * route yet; see the comment above the constant), so this test file must
 * track whatever VaultGate.tsx actually sends, not a copy that could drift.
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
}

/** Same stateful backend stand-in shape as VaultGate.test.tsx, extended with
 *  /vault/reset: it wipes the sim back to first-run on success, exactly what
 *  the real route is expected to do (a boot-state transition, like init). */
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
        sim.initialized = false;
        sim.unlocked = false;
        sim.passphrase = null;
        return json({ ok: true });
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
    await screen.findByText("Protect your world");
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
