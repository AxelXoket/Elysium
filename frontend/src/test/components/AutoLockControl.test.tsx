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
// The one number the hint prints, pinned on the backend side to
// auto_lock.DEFAULT_MINUTES (backend/tests/test_auto_lock.py), so the
// expectation below is derived from the server's constant rather than typed.
import autoLockContract from "../../../../shared/auto_lock_contract.json";

type SettingsData = { auto_lock_minutes: number; auto_lock_default_minutes?: number };

const setAutoLock = vi.fn();
const settingsData = { current: { auto_lock_minutes: 0 } as SettingsData | undefined };
const mutationState = { isPending: false, isError: false };
// The read has states of its own, and the panel now reads them: a response
// that has not landed is not the same fact as a response that says off. Every
// test in this file is about a settled read, so the default says so out loud
// rather than leaving it to an undefined field. The states themselves are
// measured against the real hook in AutoLockControlSettingsState.test.tsx.
const settingsQuery = { isPending: false, isError: false };

vi.mock("@/lib/query/settings", () => ({
  useSettings: () => ({
    data: settingsData.current,
    isPending: settingsQuery.isPending,
    isError: settingsQuery.isError,
  }),
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
    settingsData.current = {
      auto_lock_minutes: 0,
      auto_lock_default_minutes: autoLockContract.default_minutes,
    };
    mutationState.isPending = false;
    mutationState.isError = false;
    settingsQuery.isPending = false;
    settingsQuery.isError = false;
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

  it("says the default out loud, with the server's number, and what counts as being here", () => {
    // The stale sentence said "off by default" for a lock that has been on
    // at five minutes since August 2026, and "activity" used to mean an HTTP
    // request. The number is not typed here: it comes from the shared
    // contract the backend pins to its own constant.
    renderIt();
    const hint = screen.getByTestId("auto-lock-hint");
    const n = autoLockContract.default_minutes;
    expect(n).toBeGreaterThan(0);
    expect(hint).toHaveTextContent(`It is on by default, at ${n} minutes.`);
    for (const action of [
      /clicking/i,
      /typing/i,
      /scrolling/i,
      /hovering/i,
      /moving the mouse/i,
      /touching the screen/i,
    ]) {
      expect(hint).toHaveTextContent(action);
    }
    // Not claimed: a bare window focus is not one of the seven events
    // (focusin needs an element to take focus), so the sentence must not
    // promise "coming back to the window".
    expect(hint).not.toHaveTextContent(/coming back/i);
    // The reason people turn this kind of thing off. Saying it is the
    // difference between a feature that gets used and one that gets disabled.
    expect(hint).toHaveTextContent(/still being written/i);
    expect(hint).toHaveTextContent(/will not be interrupted/i);
  });

  it("prints the default the server serves, not a number of its own", () => {
    // The binding, measured: shift what the server reports and the sentence
    // has to follow. A component that printed a literal would still pass
    // the test above and fails this one.
    const shifted = autoLockContract.default_minutes + 10;
    settingsData.current = { auto_lock_minutes: 0, auto_lock_default_minutes: shifted };
    renderIt();
    const hint = screen.getByTestId("auto-lock-hint");
    expect(hint).toHaveTextContent(`at ${shifted} minutes`);
    expect(hint).not.toHaveTextContent(`at ${autoLockContract.default_minutes} minutes`);
  });

  it("says nothing about the default when the server does not report one", () => {
    // An older server, or a read that has not landed: the sentence must not
    // guess a number and must not print a hole where one would go.
    settingsData.current = { auto_lock_minutes: 0 };
    renderIt();
    const hint = screen.getByTestId("auto-lock-hint");
    expect(hint).not.toHaveTextContent(/by default/i);
    expect(hint).not.toHaveTextContent(/undefined|null|NaN/);
    // Positive control: the same sentence appears the moment it is known.
    settingsData.current = {
      auto_lock_minutes: 0,
      auto_lock_default_minutes: autoLockContract.default_minutes,
    };
    renderIt();
    expect(screen.getAllByTestId("auto-lock-hint").at(-1)).toHaveTextContent(/on by default/i);
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
    // This is a response that ARRIVED and lacks the field, which is a value
    // we have; it is not the same case as a read still in flight or a read
    // that failed, and those two are covered in their own file.
    settingsData.current = {} as SettingsData;
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

  it("the line shown while the setting is unknown reads the same muted token", () => {
    // The off line and this one swap places, so a token that resolved for one
    // and not the other would make the panel legible only when it happened to
    // know the answer.
    settingsQuery.isPending = true;
    const glassRight = wrapInGlassRight();
    renderWithQueryClient(<AutoLockControl />, {
      container: glassRight,
      baseElement: document.body,
    });

    const stateLine = screen.getByTestId("auto-lock-state");
    const mutedText = surfaceToken(glassRight, "--muted-foreground");
    expect(mutedText, "the surface never resolved a muted token").not.toBe("");
    expect(surfaceToken(stateLine, "--muted-foreground")).toBe(mutedText);
    expect(stateLine.className).not.toMatch(/settings-/);

    // Negative control, same as above: an element still wearing the old class
    // resolves the hardcoded rgba that measured 1.24-1.31:1 here, which is
    // what the assertions above are distinguishing themselves from.
    const oldHint = document.createElement("p");
    oldHint.className = "settings-hint";
    glassRight.appendChild(oldHint);
    expect(getComputedStyle(oldHint).color).toBe("rgba(202, 212, 224, 0.72)");

    settingsQuery.isPending = false;
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
