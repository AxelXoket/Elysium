/**
 * The only control in the app that shortens the window in which the data is
 * decrypted. Everything else protects a file at rest; this protects an open
 * window on a desk.
 *
 * The failure worth testing is not "the button does not work" - it is a
 * control that reports a setting the vault does not actually have, which is
 * how somebody ends up believing their vault locks itself when it does not.
 */
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { AutoLockControl } from "@/components/settings/AutoLockControl";
import {
  loadGlassRightCss,
  injectCss,
  wrapInGlassRight,
  surfaceToken,
} from "@/test/helpers/glassSurfaceCss";

const setAutoLock = vi.fn();
const settingsData = { current: { auto_lock_minutes: 0 } as { auto_lock_minutes: number } | undefined };
const mutationState = { isPending: false, isError: false };

vi.mock("@/lib/query/settings", () => ({
  useSettings: () => ({ data: settingsData.current }),
  useSetAutoLock: () => ({
    mutate: setAutoLock,
    isPending: mutationState.isPending,
    isError: mutationState.isError,
  }),
}));

function renderIt() {
  return renderWithQueryClient(<AutoLockControl />);
}

describe("AutoLockControl", () => {
  beforeEach(() => {
    setAutoLock.mockClear();
    settingsData.current = { auto_lock_minutes: 0 };
    mutationState.isPending = false;
    mutationState.isError = false;
  });

  it("shows which timeout is actually in force", () => {
    settingsData.current = { auto_lock_minutes: 15 };
    renderIt();
    expect(screen.getByRole("radio", { name: "15 min" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(screen.getByRole("radio", { name: "Never" })).toHaveAttribute(
      "aria-checked",
      "false",
    );
  });

  it("reads the server's value, not a local default", async () => {
    // A control that always drew "Never" while the vault held 30 would be
    // worse than no control: it would invite turning on something already on,
    // and hide something already off.
    settingsData.current = { auto_lock_minutes: 60 };
    renderIt();
    await waitFor(() =>
      expect(screen.getByRole("radio", { name: "1 hour" })).toHaveAttribute(
        "aria-checked",
        "true",
      ),
    );
  });

  it("sends the chosen number of minutes", async () => {
    renderIt();
    await userEvent.click(screen.getByRole("radio", { name: "30 min" }));
    expect(setAutoLock).toHaveBeenCalledWith(30);
  });

  it("sends zero for Never", async () => {
    settingsData.current = { auto_lock_minutes: 30 };
    renderIt();
    await userEvent.click(screen.getByRole("radio", { name: "Never" }));
    expect(setAutoLock).toHaveBeenCalledWith(0);
  });

  it("says plainly that off means off", () => {
    renderIt();
    expect(
      screen.getByText(/stays open until you lock it/i),
    ).toBeInTheDocument();
  });

  it("does not claim off when a timeout is set", () => {
    settingsData.current = { auto_lock_minutes: 5 };
    renderIt();
    expect(screen.queryByText(/stays open until you lock it/i)).toBeNull();
  });

  it("promises not to interrupt a reply being written", () => {
    // The reason people turn this kind of thing off. Saying it is the
    // difference between a feature that gets used and one that gets disabled.
    renderIt();
    expect(
      screen.getByText(/still being written counts as something happening/i),
    ).toBeInTheDocument();
  });

  it("says the setting did not save rather than showing it as saved", async () => {
    mutationState.isError = true;
    renderIt();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /still using the previous setting/i,
    );
  });

  it("cannot be clicked twice while saving", () => {
    mutationState.isPending = true;
    renderIt();
    expect(screen.getByRole("radio", { name: "5 min" })).toBeDisabled();
  });

  it("is reachable as one group by a screen reader", () => {
    renderIt();
    expect(
      screen.getByRole("radiogroup", {
        name: /lock the vault after this long idle/i,
      }),
    ).toBeInTheDocument();
  });

  it("survives a settings response that predates this field", () => {
    // An older server does not send auto_lock_minutes at all. The schema
    // defaults it, and the control must read that as off rather than crash.
    settingsData.current = {} as { auto_lock_minutes: number };
    renderIt();
    expect(screen.getByRole("radio", { name: "Never" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });
});

/**
 * This panel renders inside `.glass-right`, the app's one LIGHT surface, but
 * `settings-hint` and `settings-error` are painted for the DARK settings
 * dialog - `settings-hint`'s colour is a hardcoded rgba, never a variable, so
 * it measured 1.24-1.31:1 here. jsdom cannot run Tailwind v4's generated
 * utility rules (confirmed by hand: `.text-muted-foreground`'s own `color`
 * silently fails to apply, unlike the hand-written `settings-hint` rule,
 * which is a literal and DOES apply) - see helpers/glassSurfaceCss.ts for the
 * full explanation. So these tests measure the one thing that does survive
 * jsdom's cascade reliably: which custom-property TOKEN each element
 * actually resolves at its real position in the tree, against index.css run
 * through the project's own build pipeline, not a hand-copied excerpt.
 */
describe("AutoLockControl - light surface contrast", () => {
  let styleEl: HTMLStyleElement;

  beforeAll(async () => {
    styleEl = injectCss(await loadGlassRightCss());
  }, 20000);

  afterAll(() => styleEl.remove());

  afterEach(() => {
    document.querySelectorAll(".glass-right").forEach((el) => el.remove());
  });

  it("the heading and both hints read .glass-right's own text tokens", () => {
    settingsData.current = { auto_lock_minutes: 0 };
    const glassRight = wrapInGlassRight();
    renderWithQueryClient(<AutoLockControl />, {
      container: glassRight,
      baseElement: document.body,
    });

    const heading = screen.getByTestId("auto-lock-heading");
    const hint = screen.getByTestId("auto-lock-hint");
    const offHint = screen.getByTestId("auto-lock-off-hint");

    // Ground: what the surface itself declares for these tokens - read live,
    // not a hex copied out of index.css that the file could drift away from.
    const lightText = surfaceToken(glassRight, "--color-es-text-light");
    const mutedText = surfaceToken(glassRight, "--muted-foreground");
    expect(lightText, "the surface never resolved a text-light token").not.toBe("");
    expect(mutedText, "the surface never resolved a muted token").not.toBe("");

    expect(surfaceToken(heading, "--color-es-text-light")).toBe(lightText);
    expect(surfaceToken(hint, "--muted-foreground")).toBe(mutedText);
    expect(surfaceToken(offHint, "--muted-foreground")).toBe(mutedText);

    // None of the fixed elements carry a dark-dialog settings-* class.
    expect(heading.className).not.toMatch(/settings-/);
    expect(hint.className).not.toMatch(/settings-/);
    expect(offHint.className).not.toMatch(/settings-/);

    // Negative control, rendered inline: an element still wearing the OLD
    // class. Its colour is a hardcoded rgba rather than a variable, so - and
    // only for this one class - jsdom's own `.color` resolves it directly,
    // reproducing the exact measured defect (1.24-1.31:1 against this
    // surface's near-white background).
    const oldHint = document.createElement("p");
    oldHint.className = "settings-hint";
    glassRight.appendChild(oldHint);
    expect(
      getComputedStyle(oldHint).color,
      "settings-hint's colour drifted - re-measure the defect before trusting this ground",
    ).toBe("rgba(202, 212, 224, 0.72)");
  });

  it("the save-failed alert uses the sibling panels' local-error idiom, not settings-error", async () => {
    mutationState.isError = true;
    const glassRight = wrapInGlassRight();
    renderWithQueryClient(<AutoLockControl />, {
      container: glassRight,
      baseElement: document.body,
    });

    const alert = await screen.findByRole("alert");
    expect(alert.className).not.toMatch(/settings-error/);
    expect(alert.className).toMatch(/persona-local-error/);
    // settings-error and persona-local-error both read the SAME
    // --color-es-danger token, declared only at :root - which jsdom does not
    // resolve through getPropertyValue (confirmed by hand), so colour cannot
    // distinguish the two classes here. Font-size can, and is real: 11px is
    // settings-error's own declared size, 12px is persona-local-error's.
    expect(getComputedStyle(alert).fontSize).toBe("12px");

    mutationState.isError = false;
  });
});
