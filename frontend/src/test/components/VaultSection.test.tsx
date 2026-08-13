/**
 * VaultSection.test.tsx - the passphrase form.
 *
 * Written in KADEME 18b, because this surface had no test file of its own at
 * all. That is a strange gap for the one form in the app that re-encrypts the
 * whole database in place: the child notices had tests, AutoLockControl had
 * tests, the gate that locks had tests, and the box you type the passphrase
 * into had none.
 *
 * What is pinned here is deliberately narrow: the two refusals that must
 * happen BEFORE anything leaves the machine, the one that must be explained
 * when it comes back, and the state of the boxes afterwards. The re-encryption
 * itself is the server's, and backend/tests own it.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { VaultSection } from "@/components/settings/VaultSection";

/** Enough to clear the client-side length floor without naming it twice. */
const LONG_ENOUGH = "correct horse battery";

function fetchStub(
  onChangeCall: (body: Record<string, unknown>) => Response,
): { calls: () => number } {
  let calls = 0;
  const json = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    });

  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/vault/change-passphrase")) {
        calls += 1;
        return onChangeCall(JSON.parse(String(init?.body)));
      }
      if (url.includes("/vault/status")) {
        return json({
          locked: false, plaintext_backup: false, orphaned_copy: false,
          empty_stub: false, auto_lock_minutes: 0,
        });
      }
      return json({});
    }),
  );
  return { calls: () => calls };
}

async function fill(
  user: ReturnType<typeof userEvent.setup>,
  values: { current: string; next: string; repeat: string },
) {
  await user.type(screen.getByLabelText("Current passphrase"), values.current);
  await user.type(screen.getByLabelText("New passphrase"), values.next);
  await user.type(screen.getByLabelText("Repeat new passphrase"), values.repeat);
  await user.click(screen.getByRole("button", { name: "Change passphrase" }));
}

describe("changing the vault passphrase", () => {
  beforeEach(() => vi.unstubAllGlobals());
  afterEach(() => vi.unstubAllGlobals());

  it("masks all three boxes and names them for a password manager", () => {
    // Not decoration. `type` is what keeps the passphrase off the screen in a
    // room with other people and out of the browser's own value logging, and
    // the autoComplete values are what stop a manager offering the CURRENT
    // passphrase as the new one - the two fields are otherwise identical to it.
    fetchStub(() => new Response("{}", { status: 200 }));
    renderWithQueryClient(<VaultSection />);

    const current = screen.getByLabelText("Current passphrase");
    const next = screen.getByLabelText("New passphrase");
    const repeat = screen.getByLabelText("Repeat new passphrase");

    for (const box of [current, next, repeat]) {
      expect(box, "a passphrase box is readable over a shoulder")
        .toHaveAttribute("type", "password");
    }
    expect(current).toHaveAttribute("autocomplete", "current-password");
    expect(next).toHaveAttribute("autocomplete", "new-password");
    expect(repeat).toHaveAttribute("autocomplete", "new-password");
  });

  it("refuses a repeat that does not match, without sending anything", async () => {
    // The failure this exists for is unrecoverable: a mistyped repeat that
    // reached the server would re-encrypt the database under a passphrase
    // nobody knows, and there is no copy to fall back to.
    const user = userEvent.setup();
    const wire = fetchStub(() => new Response("{}", { status: 200 }));
    renderWithQueryClient(<VaultSection />);

    await fill(user, {
      current: "old passphrase here",
      next: LONG_ENOUGH,
      repeat: `${LONG_ENOUGH}!`,
    });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The new entries do not match.",
    );
    expect(wire.calls(), "a mismatched passphrase reached the vault").toBe(0);
  });

  it("refuses a new passphrase too short to be one, without sending it", async () => {
    const user = userEvent.setup();
    const wire = fetchStub(() => new Response("{}", { status: 200 }));
    renderWithQueryClient(<VaultSection />);

    await fill(user, { current: "old passphrase here", next: "short", repeat: "short" });

    expect(await screen.findByRole("alert")).toHaveTextContent("at least");
    expect(wire.calls()).toBe(0);
  });

  it("says the current passphrase was wrong, in its own words", async () => {
    // The raw detail is a wire code. Somebody who mistyped their passphrase
    // should not have to read one.
    const user = userEvent.setup();
    const wire = fetchStub(() =>
      new Response(JSON.stringify({ detail: "wrong_passphrase" }), { status: 403 }),
    );
    renderWithQueryClient(<VaultSection />);

    await fill(user, {
      current: "not the passphrase",
      next: LONG_ENOUGH,
      repeat: LONG_ENOUGH,
    });

    await waitFor(() => expect(wire.calls()).toBe(1));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Current passphrase is wrong.");
    expect(alert.textContent, "the wire code was shown to the reader")
      .not.toContain("wrong_passphrase");
  });

  it("empties every box once the change lands", async () => {
    // A passphrase left sitting in a form is a passphrase on screen for as
    // long as the panel stays open.
    const user = userEvent.setup();
    const sent: Record<string, unknown>[] = [];
    fetchStub((body) => {
      sent.push(body);
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    renderWithQueryClient(<VaultSection />);

    await fill(user, {
      current: "old passphrase here",
      next: LONG_ENOUGH,
      repeat: LONG_ENOUGH,
    });

    expect(await screen.findByText("Passphrase changed.")).toBeInTheDocument();
    for (const label of [
      "Current passphrase",
      "New passphrase",
      "Repeat new passphrase",
    ]) {
      expect(screen.getByLabelText(label), `${label} still holds a value`)
        .toHaveValue("");
    }
    expect(sent, "the form sent nothing").toHaveLength(1);
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
