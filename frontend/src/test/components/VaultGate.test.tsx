/**
 * VaultGate.test.tsx - boot gate for full-DB encryption.
 *
 * A stateful fetch stub plays the backend: status flips to unlocked after a
 * successful init/unlock, so the invalidation → refetch → children flow is
 * exercised end to end (the same path the real app takes).
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { VaultGate } from "@/components/vault/VaultGate";
import type { ReactNode } from "react";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

interface VaultSim {
  initialized: boolean;
  unlocked: boolean;
  passphrase: string | null;
}

/** Stateful backend stand-in for /vault/*. */
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
      if (url.endsWith("/vault/init")) {
        sim.initialized = true;
        sim.unlocked = true;
        sim.passphrase = body.passphrase;
        return json({ ok: true, migrated: false });
      }
      if (url.endsWith("/vault/unlock")) {
        if (body.passphrase !== sim.passphrase) {
          return json({ detail: "wrong_passphrase" }, 401);
        }
        sim.unlocked = true;
        return json({ ok: true });
      }
      return json({}, 404);
    }),
  );
}

const APP_MARKER = <div data-testid="app-root">app</div>;

describe("VaultGate", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the app directly when the vault is unlocked", async () => {
    stubVaultFetch({ initialized: true, unlocked: true, passphrase: "x" });
    render(<VaultGate>{APP_MARKER}</VaultGate>, { wrapper });
    expect(await screen.findByTestId("app-root")).toBeInTheDocument();
  });

  it("walks first-run setup: create passphrase → app", async () => {
    const user = userEvent.setup();
    stubVaultFetch({ initialized: false, unlocked: false, passphrase: null });
    render(<VaultGate>{APP_MARKER}</VaultGate>, { wrapper });

    await screen.findByText("Protect your world");
    // Exact labels, not /passphrase/i - the reveal buttons carry
    // "Show passphrase" and would otherwise match the regex too.
    await user.type(screen.getByLabelText("Passphrase"), "seaside-orchid-9");
    await user.type(
      screen.getByLabelText("Repeat passphrase"),
      "seaside-orchid-9",
    );
    await user.click(screen.getByRole("button", { name: "Create vault" }));

    expect(await screen.findByTestId("app-root")).toBeInTheDocument();
  });

  it("rejects mismatched entries locally without calling the API", async () => {
    const user = userEvent.setup();
    stubVaultFetch({ initialized: false, unlocked: false, passphrase: null });
    render(<VaultGate>{APP_MARKER}</VaultGate>, { wrapper });

    await screen.findByText("Protect your world");
    await user.type(screen.getByLabelText("Passphrase"), "seaside-orchid-9");
    await user.type(
      screen.getByLabelText("Repeat passphrase"),
      "different-thing-1",
    );
    await user.click(screen.getByRole("button", { name: "Create vault" }));

    expect(
      await screen.findByText("The two entries do not match."),
    ).toBeInTheDocument();
    const calls = (fetch as ReturnType<typeof vi.fn>).mock.calls.map((c) =>
      String(c[0]),
    );
    expect(calls.some((u) => u.endsWith("/vault/init"))).toBe(false);
  });

  it("locks: wrong passphrase shows the error and clears the field, right one opens", async () => {
    const user = userEvent.setup();
    stubVaultFetch({
      initialized: true,
      unlocked: false,
      passphrase: "right-horse-42",
    });
    render(<VaultGate>{APP_MARKER}</VaultGate>, { wrapper });

    await screen.findByText("Elysium is locked");
    await user.type(screen.getByLabelText("Passphrase"), "wrong-guess-11");
    await user.click(screen.getByRole("button", { name: "Unlock" }));

    expect(await screen.findByText("Wrong passphrase.")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByLabelText("Passphrase")).toHaveValue("");
    });

    await user.type(screen.getByLabelText("Passphrase"), "right-horse-42");
    await user.click(screen.getByRole("button", { name: "Unlock" }));
    expect(await screen.findByTestId("app-root")).toBeInTheDocument();
  });
});

describe("VaultGate lock hygiene", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it("purges every non-vault query from the cache when the vault locks", async () => {
    const sim: VaultSim = { initialized: true, unlocked: true, passphrase: "x" };
    stubVaultFetch(sim);
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={qc}>
        <VaultGate>{APP_MARKER}</VaultGate>
      </QueryClientProvider>,
    );
    await screen.findByTestId("app-root");

    // Seed user-data caches the way a running app would hold them.
    qc.setQueryData(["chats"], [{ id: 1 }]);
    qc.setQueryData(["messages", 1], [{ id: 10, content: "private text" }]);
    qc.setQueryData(["characters"], [{ id: 2 }]);
    qc.setQueryData(["settings"], { api_key_set: true });

    // Backend locks out from under the app; the gate refetches status.
    sim.unlocked = false;
    await qc.invalidateQueries({ queryKey: ["vault"] });

    await screen.findByText("Elysium is locked");
    // Watch-point 3: EVERY key that is not the gate's own must be gone -
    // a prefix/exact-key mistake here would leave chat text in RAM.
    await waitFor(() => {
      const roots = qc
        .getQueryCache()
        .getAll()
        .map((q) => q.queryKey[0]);
      expect(roots.length).toBeGreaterThan(0);
      expect(roots.every((k) => k === "vault")).toBe(true);
    });
    expect(qc.getQueryData(["messages", 1])).toBeUndefined();
    expect(qc.getQueryData(["chats"])).toBeUndefined();
    expect(qc.getQueryData(["characters"])).toBeUndefined();
    expect(qc.getQueryData(["settings"])).toBeUndefined();
  });
});
