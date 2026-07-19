/**
 * Sidebar lock button: fires the lock API and requests the closing
 * animation; the two run in parallel and neither depends on the other.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SidebarHeader } from "@/components/sidebar/SidebarHeader";
import { setVaultLockAnimationHandler } from "@/lib/vaultLockUi";

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

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

    render(<SidebarHeader />, { wrapper });
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

  it("locking still works with NO animation handler registered", async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        calls.push(`${init?.method ?? "GET"} ${String(input)}`);
        return new Response(JSON.stringify({ ok: true }), { status: 200 });
      }),
    );

    render(<SidebarHeader />, { wrapper });
    await userEvent.click(screen.getByRole("button", { name: "Lock vault" }));
    await waitFor(() => {
      expect(calls.some((c) => c.includes("/vault/lock"))).toBe(true);
    });
  });
});
