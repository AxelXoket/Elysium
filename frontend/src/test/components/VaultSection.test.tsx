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
import { afterAll, beforeAll, describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { VaultSection } from "@/components/settings/VaultSection";
import { mockFetch } from "../mocks/api";
import {
  loadGlassRightCss,
  injectCss,
  wrapInGlassRight,
  surfaceToken,
} from "@/test/helpers/glassSurfaceCss";

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

/**
 * The one thing a rotation is supposed to revoke. change-passphrase can
 * answer `ok: true` and still name a sidecar copy it could not re-key - a
 * complete vault still openable with the passphrase the user just tried to
 * replace. That name arrives on THIS response only; it is nowhere in
 * /vault/status, so nothing else in the app ever gets a second chance to
 * show it.
 */
describe("VaultSection - what a rotation did not revoke", () => {
  beforeEach(() => vi.unstubAllGlobals());
  afterEach(() => vi.unstubAllGlobals());

  it("GROUND: a clean rotation says only 'Passphrase changed.'", async () => {
    const user = userEvent.setup();
    fetchStub(() => new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    renderWithQueryClient(<VaultSection />);

    await fill(user, {
      current: "old passphrase here",
      next: LONG_ENOUGH,
      repeat: LONG_ENOUGH,
    });

    expect(await screen.findByText("Passphrase changed.")).toBeInTheDocument();
    expect(screen.queryByTestId("vault-unrevoked-notice")).toBeNull();
  });

  it("POSITIVE CONTROL: names the file still readable under the old passphrase", async () => {
    const user = userEvent.setup();
    fetchStub(() => new Response(
      JSON.stringify({ ok: true, unrevoked: ["app.db.premigrate.bak"] }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ));
    renderWithQueryClient(<VaultSection />);

    await fill(user, {
      current: "old passphrase here",
      next: LONG_ENOUGH,
      repeat: LONG_ENOUGH,
    });

    expect(await screen.findByText("Passphrase changed.")).toBeInTheDocument();
    const notice = await screen.findByTestId("vault-unrevoked-notice");
    expect(notice).toBeInTheDocument();
    expect(
      screen.getByTestId("vault-unrevoked-name-app.db.premigrate.bak"),
    ).toHaveTextContent("app.db.premigrate.bak");
  });

  it("clears the notice the moment a new attempt is submitted, success or not", async () => {
    const user = userEvent.setup();
    let call = 0;
    fetchStub(() => {
      call += 1;
      // First submit leaves a copy unrevoked; second is refused outright.
      const body = call === 1
        ? { ok: true, unrevoked: ["app.db.premigrate.bak"] }
        : { detail: "wrong_passphrase" };
      return new Response(JSON.stringify(body), {
        status: call === 1 ? 200 : 403,
        headers: { "Content-Type": "application/json" },
      });
    });
    renderWithQueryClient(<VaultSection />);

    await fill(user, {
      current: "old passphrase here",
      next: LONG_ENOUGH,
      repeat: LONG_ENOUGH,
    });
    expect(await screen.findByTestId("vault-unrevoked-notice")).toBeInTheDocument();

    await user.type(screen.getByLabelText("Current passphrase"), "wrong one");
    await user.type(screen.getByLabelText("New passphrase"), LONG_ENOUGH);
    await user.type(screen.getByLabelText("Repeat new passphrase"), LONG_ENOUGH);
    await user.click(screen.getByRole("button", { name: "Change passphrase" }));

    await screen.findByRole("alert");
    expect(
      screen.queryByTestId("vault-unrevoked-notice"),
      "a stale warning from the PREVIOUS change survived a new attempt",
    ).toBeNull();
  });
});

describe("VaultSection - the leftovers it has to show", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it("paints every remnant the vault reports", async () => {
    /**
     * Each of these notices has its own test file, and every one of those
     * renders the component directly. That proves the component works and
     * says nothing about whether anything renders it - which is exactly the
     * failure orphaned_copy already made once, sitting on the wire for a whole
     * release with no screen reading it.
     *
     * So this is the mounting, asserted where the mounting happens. It covers
     * all four together on purpose: the next one added should have to change
     * this line.
     */
    mockFetch({
      "/vault/status": {
        body: {
          initialized: true,
          unlocked: true,
          plaintext_backups: ["app.db.plain.bak-1700000000"],
          orphaned_copy: true,
          orphaned_copy_readable: true,
          empty_stub: true,
          rotation_backups: ["app.db.rekey.bak-1700000000"],
        },
      },
      "/settings": { body: { auto_lock_minutes: 0 } },
    });

    renderWithQueryClient(<VaultSection />);

    expect(
      await screen.findByTestId("plaintext-backup-notice"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("orphaned-copy-notice")).toBeInTheDocument();
    expect(screen.getByTestId("rotation-backup-notice")).toBeInTheDocument();
    expect(screen.getByTestId("empty-stub-notice")).toBeInTheDocument();
  });
});

describe("VaultSection - what the encryption promise leaves out", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => vi.restoreAllMocks());

  /** The panel's encryption promise, as the reader sees it. */
  async function promise(): Promise<string> {
    mockFetch({
      "/vault/status": { body: { initialized: true, unlocked: true } },
      "/settings": { body: { auto_lock_minutes: 0 } },
    });
    renderWithQueryClient(<VaultSection />);
    const hint = await screen.findByText(
      /Everything on disk is encrypted with this passphrase/i,
    );
    return hint.textContent ?? "";
  }

  it("names the spoken audio, not the wallpaper alone", async () => {
    // This panel is the complete list (the setup card carries the short true
    // version). The one that must never be dropped again is the audio: every
    // reply is written as a plain wav under the data folder, and it is the
    // conversation itself, unencrypted, for as long as it is there. FF15
    // caught this same sentence once, added the wallpaper, and stopped.
    const text = await promise();

    expect(text, "the complete list is missing the spoken audio")
      .toMatch(/spoken replies are written as plain audio files/i);
    expect(text, "the reader is not told the audio ever goes away")
      .toMatch(/wiped at every lock/i);
    expect(text, "the wallpaper dropped out of the complete list")
      .toMatch(/wallpaper/i);
    // Ground: a phrase of the same shape that this panel does not carry.
    // Without it, a matcher that matched anything would pass the three above.
    expect(text).not.toMatch(/spoken replies are encrypted/i);
  });

  it("keeps the cloning clip conditional for users whose model cannot clone", async () => {
    // Reference clips exist only for cloning engines. The clause has to warn
    // the user who has one without inventing a file for the user who does not.
    const text = await promise();

    expect(text, "the clip is claimed to exist unconditionally")
      .toMatch(/any voice clip you add for cloning/i);
    expect(text, "the reader is not told this one survives a lock")
      .toMatch(/is not wiped/i);
    expect(text, "the copy asserts the reader already has a clip on disk")
      .not.toMatch(/your voice clip (is|lives|sits) /i);
  });
});

/**
 * This form renders inside `.glass-right`, the app's one light surface, but
 * `settings-label` (the three passphrase captions), `settings-hint` (the
 * disclosure paragraph), `settings-error` and `settings-value` are painted
 * for the dark settings dialog. `settings-label` is the sharpest case: it has
 * no colour rule of its own, so it just inherits body's - measured at
 * 1.05-1.14:1 here. See helpers/glassSurfaceCss.ts for why these tests read
 * custom-property tokens rather than `.color`.
 */
describe("VaultSection - light surface contrast", () => {
  let styleEl: HTMLStyleElement;

  beforeAll(async () => {
    styleEl = injectCss(await loadGlassRightCss());
  }, 20000);

  afterAll(() => styleEl.remove());

  afterEach(() => {
    document.querySelectorAll(".glass-right").forEach((el) => el.remove());
  });

  it("the three passphrase labels and the disclosure hint read .glass-right's own tokens", async () => {
    const glassRight = wrapInGlassRight();
    mockFetch({
      "/vault/status": {
        body: { initialized: true, unlocked: true, auto_lock_minutes: 0 },
      },
      "/settings": { body: { auto_lock_minutes: 0 } },
    });
    renderWithQueryClient(<VaultSection />, {
      container: glassRight,
      baseElement: document.body,
    });

    const disclosure = await screen.findByTestId("vault-disclosure-hint");
    const current = screen.getByTestId("vault-label-current");
    const next = screen.getByTestId("vault-label-new");
    const repeat = screen.getByTestId("vault-label-repeat");

    const lightText = surfaceToken(glassRight, "--color-es-text-light");
    const mutedText = surfaceToken(glassRight, "--muted-foreground");
    expect(lightText, "the surface never resolved a text-light token").not.toBe("");
    expect(mutedText, "the surface never resolved a muted token").not.toBe("");

    expect(surfaceToken(disclosure, "--muted-foreground")).toBe(mutedText);
    for (const label of [current, next, repeat]) {
      expect(surfaceToken(label, "--color-es-text-light")).toBe(lightText);
      expect(label.className).not.toMatch(/settings-/);
    }
    expect(disclosure.className).not.toMatch(/settings-/);

    // Negative control: settings-label declares no colour of its own, so it
    // just inherits - the fixed label's explicit inline
    // `color: var(--color-es-text-light)` is what settings-label never had.
    // The custom-property TOKEN is still reachable at this DOM position
    // either way (inheritance of a custom property is unaffected by which
    // class is on the element); what differs, provably, is whether `color`
    // itself is ever set to consume it.
    const oldLabel = document.createElement("span");
    oldLabel.className = "settings-label";
    glassRight.appendChild(oldLabel);
    expect(
      getComputedStyle(oldLabel).color,
      "settings-label must not coincidentally already declare the fix's colour",
    ).not.toBe(getComputedStyle(current).color);
  });

  it("a refused change uses persona-local-error, and a saved one drops settings-value", async () => {
    const user = userEvent.setup();
    const glassRight = wrapInGlassRight();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        const json = (body: unknown, status = 200) =>
          new Response(JSON.stringify(body), {
            status,
            headers: { "Content-Type": "application/json" },
          });
        if (url.includes("/vault/change-passphrase")) {
          return json({ detail: "wrong_passphrase" }, 403);
        }
        if (url.includes("/vault/status")) {
          return json({ initialized: true, unlocked: true, auto_lock_minutes: 0 });
        }
        return json({});
      }),
    );
    renderWithQueryClient(<VaultSection />, {
      container: glassRight,
      baseElement: document.body,
    });

    await user.type(await screen.findByLabelText("Current passphrase"), "wrong one");
    await user.type(screen.getByLabelText("New passphrase"), "correct horse battery");
    await user.type(screen.getByLabelText("Repeat new passphrase"), "correct horse battery");
    await user.click(screen.getByRole("button", { name: "Change passphrase" }));

    const alert = await screen.findByRole("alert");
    expect(alert.className).not.toMatch(/settings-error/);
    expect(alert.className).toMatch(/persona-local-error/);
    // settings-error and persona-local-error both read the same :root-only
    // --color-es-danger token, which jsdom cannot resolve either way - see
    // AutoLockControl's contrast tests for the same note. Font-size is the
    // real, measured difference between the two hand-written rules.
    expect(getComputedStyle(alert).fontSize).toBe("12px");

    vi.unstubAllGlobals();
  });
});
