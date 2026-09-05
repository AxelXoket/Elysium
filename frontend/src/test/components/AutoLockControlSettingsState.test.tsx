/**
 * Three different answers the auto-lock panel used to give as the same number.
 *
 * `settings.data?.auto_lock_minutes ?? 0` returned 0 for a vault that really
 * has auto-lock off, for a settings response that has not landed yet, and for
 * one that failed. Only the first of those means off, yet all three drew
 * "Never" as the chosen chip and printed the reassurance underneath it - a
 * sentence about what the vault does, written from a value nobody had read.
 *
 * These render against the REAL query hook rather than a mocked one, because
 * the question is whether the production fetch passes through that window at
 * all, and hold the window open with delayRoute so the assertions land inside
 * it. The ProxySection indicator tests measure their own version of this the
 * same way.
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";

import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { mockFetch } from "@/test/mocks/api";
import { delayRoute } from "@/test/helpers/delayRoute";
import { settingsFixture } from "@/test/mocks/fixtures";
import { keys } from "@/lib/query/keys";
import { AutoLockControl } from "@/components/settings/AutoLockControl";

/** The labels of the chips currently drawn as chosen, swept off every chip the
 *  panel actually renders rather than off a list of them retyped here. */
function chosenLabels(): (string | null)[] {
  return screen
    .getAllByRole("radio")
    .filter((chip) => chip.getAttribute("aria-checked") === "true")
    .map((chip) => chip.textContent);
}

/** Every chip, chosen or not. An empty group would make every "nothing is
 *  chosen" assertion below true for the wrong reason. */
function allChips(): HTMLElement[] {
  return screen.getAllByRole("radio");
}

function offLine(): HTMLElement | null {
  return screen.queryByTestId("auto-lock-off-hint");
}

function group(): HTMLElement {
  return screen.getByRole("radiogroup", {
    name: /lock the vault after this long idle/i,
  });
}

describe("AutoLockControl - a setting that has not been read", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("ground: a vault that really is off shows Never chosen and says so", async () => {
    mockFetch({
      "/settings": { body: { ...settingsFixture, auto_lock_minutes: 0 } },
    });
    renderWithQueryClient(<AutoLockControl />);

    await waitFor(() => expect(chosenLabels()).toEqual(["Never"]));
    expect(offLine()).toBeInTheDocument();
  });

  it("positive control: a vault set to 15 shows 15 chosen and prints no off line", async () => {
    // Without this, "no chip is chosen" and "no off line" would both pass
    // against a panel that rendered nothing at all.
    mockFetch({
      "/settings": { body: { ...settingsFixture, auto_lock_minutes: 15 } },
    });
    renderWithQueryClient(<AutoLockControl />);

    await waitFor(() => expect(chosenLabels()).toEqual(["15 min"]));
    expect(offLine()).toBeNull();
  });

  it("shows nothing as chosen, and no off line, while the settings are in flight", async () => {
    const fetchMock = mockFetch({
      "/settings": { body: { ...settingsFixture, auto_lock_minutes: 0 } },
    });
    const release = delayRoute(fetchMock, "GET", "/settings", {
      body: { ...settingsFixture, auto_lock_minutes: 0 },
    });
    renderWithQueryClient(<AutoLockControl />);

    // Ground for the absence below: the offers are on screen and pickable,
    // so "nothing is chosen" is a statement about the panel's answer and not
    // about an empty group.
    expect(allChips().length).toBeGreaterThan(0);
    expect(chosenLabels()).toEqual([]);
    expect(offLine()).toBeNull();
    expect(group()).toHaveAttribute("aria-busy", "true");

    // Releasing proves the assertions above were taken inside the window and
    // not against a panel that is simply broken: the same render then settles
    // into the ground state.
    release();
    await waitFor(() => expect(chosenLabels()).toEqual(["Never"]));
    expect(offLine()).toBeInTheDocument();
    expect(group()).toHaveAttribute("aria-busy", "false");
  });

  it("shows nothing as chosen, and no off line, when the settings cannot be read", async () => {
    mockFetch({
      "/settings": { status: 500, body: { detail: "internal_error" } },
    });
    const { queryClient } = renderWithQueryClient(<AutoLockControl />);

    // Waiting on the query's own state rather than on anything this change
    // adds: what is being measured is the settled failure, not a fetch still
    // in flight, and the wait has to mean that before and after the fix.
    await waitFor(() =>
      expect(queryClient.getQueryState(keys.settings())?.status).toBe("error"),
    );

    expect(allChips().length).toBeGreaterThan(0);
    expect(chosenLabels()).toEqual([]);
    expect(offLine()).toBeNull();
    expect(screen.getByTestId("auto-lock-state")).toHaveAttribute(
      "data-state",
      "error",
    );
  });

  it("tells the reader which of the two it is instead of showing a value", async () => {
    const fetchMock = mockFetch({
      "/settings": { body: { ...settingsFixture, auto_lock_minutes: 0 } },
    });
    const release = delayRoute(fetchMock, "GET", "/settings", {
      body: { ...settingsFixture, auto_lock_minutes: 0 },
    });
    renderWithQueryClient(<AutoLockControl />);

    const pendingLine = screen.getByTestId("auto-lock-state");
    expect(pendingLine).toHaveAttribute("data-state", "pending");
    // An empty element would satisfy the state attribute and tell the reader
    // nothing, so the line has to actually say something.
    expect(pendingLine.textContent?.trim()).not.toBe("");

    release();
    // And it goes away once there is a real value to show, rather than
    // sitting under a setting it no longer describes.
    await waitFor(() =>
      expect(screen.queryByTestId("auto-lock-state")).toBeNull(),
    );
  });
});
