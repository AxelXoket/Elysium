/**
 * K-44. A rotation killed between taking its backup and removing it leaves a
 * complete copy of the vault behind, and this one opens with the passphrase
 * the user believes they just revoked.
 *
 * The backend sweeps every such copy it CAN read at unlock, so anything that
 * reaches this component is the copy Elysium refuses to touch. That refusal
 * is only defensible if the file is named on a screen: a field nobody paints
 * is the same as a log line nobody opens, which is the mistake orphaned_copy
 * made for a whole release.
 */
import { afterAll, beforeAll, describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { RotationBackupNotice } from "@/components/settings/RotationBackupNotice";
import { mockFetch } from "../mocks/api";
import {
  loadGlassRightCss,
  injectCss,
  wrapInGlassRight,
  surfaceToken,
} from "@/test/helpers/glassSurfaceCss";

const BASE = { initialized: true, unlocked: true };

describe("RotationBackupNotice", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it("says nothing on the normal path", async () => {
    mockFetch({ "/vault/status": { body: { ...BASE, rotation_backups: [] } } });
    renderWithQueryClient(<RotationBackupNotice />);

    await waitFor(() => {
      expect(
        screen.queryByTestId("rotation-backup-notice"),
      ).not.toBeInTheDocument();
    });
  });

  it("names every file, because the user has to find them in the folder", async () => {
    mockFetch({
      "/vault/status": {
        body: {
          ...BASE,
          rotation_backups: ["app.db.rekey.bak-1700000000",
                             "app.db.rekey.bak-1700000900"],
        },
      },
    });
    renderWithQueryClient(<RotationBackupNotice />);

    expect(
      await screen.findByTestId("rotation-backup-notice"),
    ).toBeInTheDocument();
    expect(screen.getByText("app.db.rekey.bak-1700000000")).toBeInTheDocument();
    expect(screen.getByText("app.db.rekey.bak-1700000900")).toBeInTheDocument();
  });

  it("offers no delete button, and that is the design", async () => {
    // Its two neighbours both have one. This file cannot be read by this
    // vault, so a button here would offer to destroy something nobody could
    // check first - the one thing every discard path in the app refuses.
    mockFetch({
      "/vault/status": {
        body: { ...BASE, rotation_backups: ["app.db.rekey.bak-1700000000"] },
      },
    });
    renderWithQueryClient(<RotationBackupNotice />);

    await screen.findByTestId("rotation-backup-notice");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("says the old passphrase still opens it", async () => {
    // The sentence that makes this different from "a duplicate you can tidy
    // up": changing the passphrase again does not close this hole.
    mockFetch({
      "/vault/status": {
        body: { ...BASE, rotation_backups: ["app.db.rekey.bak-1700000000"] },
      },
    });
    renderWithQueryClient(<RotationBackupNotice />);

    const notice = await screen.findByTestId("rotation-backup-notice");
    expect(notice.textContent).toMatch(/previous/i);
    expect(notice.textContent).toMatch(/will not close that/i);
  });
});

/**
 * This notice renders inside `.glass-right`, the app's one light surface,
 * but `settings-hint` (both paragraphs, and the `<code>` naming each file)
 * is painted for the dark settings dialog. See helpers/glassSurfaceCss.ts for
 * why these tests read custom-property tokens rather than `.color`.
 */
describe("RotationBackupNotice - light surface contrast", () => {
  let styleEl: HTMLStyleElement;

  beforeAll(async () => {
    styleEl = injectCss(await loadGlassRightCss());
  }, 20000);

  afterAll(() => styleEl.remove());

  afterEach(() => {
    document.querySelectorAll(".glass-right").forEach((el) => el.remove());
  });

  it("both paragraphs and the filename read .glass-right's own muted token", async () => {
    const glassRight = wrapInGlassRight();
    mockFetch({
      "/vault/status": {
        body: { ...BASE, rotation_backups: ["app.db.rekey.bak-1700000000"] },
      },
    });
    renderWithQueryClient(<RotationBackupNotice />, {
      container: glassRight,
      baseElement: document.body,
    });

    const hint1 = await screen.findByTestId("rotation-hint-1");
    const hint2 = screen.getByTestId("rotation-hint-2");
    const name = screen.getByTestId("rotation-name-app.db.rekey.bak-1700000000");

    const mutedText = surfaceToken(glassRight, "--muted-foreground");
    expect(mutedText, "the surface never resolved a muted token").not.toBe("");

    for (const el of [hint1, hint2, name]) {
      expect(surfaceToken(el, "--muted-foreground")).toBe(mutedText);
      expect(el.className).not.toMatch(/settings-/);
    }

    // Negative control: settings-hint's colour is a hardcoded rgba, not a
    // variable, so jsdom resolves `.color` for it directly.
    const oldHint = document.createElement("code");
    oldHint.className = "settings-hint";
    glassRight.appendChild(oldHint);
    expect(
      getComputedStyle(oldHint).color,
      "settings-hint's colour drifted - re-measure before trusting this ground",
    ).toBe("rgba(202, 212, 224, 0.72)");
  });
});
