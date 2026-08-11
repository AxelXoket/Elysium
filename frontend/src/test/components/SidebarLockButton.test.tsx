/**
 * Sidebar lock button: fires the lock API and requests the closing
 * animation; the two run in parallel and neither depends on the other.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { SidebarHeader } from "@/components/sidebar/SidebarHeader";
import { setVaultLockAnimationHandler } from "@/lib/vaultLockUi";

describe("SidebarHeader lock button", () => {
  beforeEach(() => vi.unstubAllGlobals());
  afterEach(() => {
    vi.unstubAllGlobals();
    setVaultLockAnimationHandler(null);
  });

  it("hands the lock call to the overlay as commit; API fires when the overlay says so", async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        calls.push(`${init?.method ?? "GET"} ${String(input)}`);
        return new Response(JSON.stringify({ ok: true }), { status: 200 });
      }),
    );
    let received: (() => void) | null = null;
    setVaultLockAnimationHandler((commit) => {
      received = commit;
    });

    renderWithQueryClient(<SidebarHeader />);
    await userEvent.click(screen.getByRole("button", { name: "Lock vault" }));

    // The button did NOT fire the API itself - the overlay owns the timing.
    expect(received).not.toBeNull();
    expect(calls.some((c) => c.includes("/vault/lock"))).toBe(false);

    // The overlay's click moment: commit fires the real call.
    received!();
    await waitFor(() => {
      expect(calls.some((c) => c.startsWith("POST") && c.includes("/vault/lock"))).toBe(true);
    });
  });

  it("a lock the server refuses says so instead of failing quietly", async () => {
    // The failure path had no test, on the one button whose whole job is to
    // close the vault. Worth being exact about what the promise here is,
    // because it is easy to read this as worse than it is: the overlay's
    // reveal timer is fixed and does NOT wait on the API, so a refused lock
    // ends with the veil lifting and the app still on screen. That is the
    // documented design ("on failure the overlay fades back to the app and a
    // toast explains"), not a UI claiming to be locked while it is not: the
    // lock SCREEN only ever appears when the server's status flips.
    //
    // What actually has to hold is the explaining half. A refused lock that
    // said nothing would leave somebody believing they had locked up and
    // walked away.
    const { useErrorStore } = await import("@/lib/errors");
    useErrorStore.getState().clearAll();

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) =>
        String(input).includes("/vault/lock")
          ? new Response(JSON.stringify({ detail: "vault_locked" }), {
              status: 500,
            })
          : new Response(JSON.stringify({ ok: true }), { status: 200 }),
      ),
    );

    renderWithQueryClient(<SidebarHeader />);
    await userEvent.click(screen.getByRole("button", { name: "Lock vault" }));

    await waitFor(() => {
      expect(
        useErrorStore.getState().errors,
        "the vault refused to lock and nothing said so",
      ).toHaveLength(1);
    });
    useErrorStore.getState().clearAll();
  });

  it("locking still works with NO animation handler registered", async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        calls.push(`${init?.method ?? "GET"} ${String(input)}`);
        return new Response(JSON.stringify({ ok: true }), { status: 200 });
      }),
    );

    renderWithQueryClient(<SidebarHeader />);
    await userEvent.click(screen.getByRole("button", { name: "Lock vault" }));
    await waitFor(() => {
      expect(calls.some((c) => c.includes("/vault/lock"))).toBe(true);
    });
  });
});
