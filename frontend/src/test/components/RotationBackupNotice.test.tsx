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
import { screen } from "@testing-library/react";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { RotationBackupNotice } from "@/components/settings/RotationBackupNotice";
import { mockFetch } from "../mocks/api";
import { delayRoute } from "@/test/helpers/delayRoute";
import { mockFetchUnreachable } from "@/test/helpers/fetchUnreachable";
import { expectNothingOnceAnswered } from "@/test/helpers/vaultStatusSettled";
import {
  loadGlassRightCss,
  injectCss,
  wrapInGlassRight,
  surfaceToken,
  resolveColour,
} from "@/test/helpers/glassSurfaceCss";

const BASE = { initialized: true, unlocked: true };

describe("RotationBackupNotice", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it("says nothing on the normal path", async () => {
    mockFetch({ "/vault/status": { body: { ...BASE, rotation_backups: [] } } });
    // Asserted only once the status query has ANSWERED: an absence checked
    // on the first render passes before the answer exists (IP-11b).
    await expectNothingOnceAnswered(renderWithQueryClient(<RotationBackupNotice />));
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
  let sheet = "";
  let styleEl: HTMLStyleElement;

  beforeAll(async () => {
    sheet = await loadGlassRightCss();
    styleEl = injectCss(sheet);
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
      resolveColour(sheet, getComputedStyle(oldHint).color),
      "settings-hint's colour drifted - re-measure before trusting this ground",
    ).toBe("rgba(202, 212, 224, 0.72)");
  });
});

/**
 * IP-11b. The gate on this notice used to be `status.data?.rotation_backups`, so a
 * status query that FAILED rendered the same nothing as a clean disk, and no
 * console call exists in this app to leave a trace of it. These tests drive
 * /vault/status itself, at the fetch boundary, into every state the real
 * useVaultStatus hook can be in, and read what the DOM shows for each.
 */
describe("RotationBackupNotice - when the check itself cannot run", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => vi.restoreAllMocks());

  const NOTICE = "rotation-backup-notice";
  const CHECK = "rotation-backup-check";
  const PRESENT = { ...BASE, rotation_backups: ["app.db.rekey.bak-1700000000"] };
  const ABSENT = { ...BASE, rotation_backups: [] };
  /** An older backend that does not know the field at all. */
  const OLDER = { ...BASE };
  /** The warning's own sentences. None of them may leak into the check line. */
  const WARNING_WORDING = /previous|will not close that|cannot read it/i;

  it("T-57: a failed status query is said out loud, and does not read as all clear", async () => {
    mockFetch({ "/vault/status": { status: 500, body: { detail: "boom" } } });
    const { container } = renderWithQueryClient(<RotationBackupNotice />);

    const line = await screen.findByText(/could not check for a copy from before a passphrase change/i);
    expect(line).toBe(screen.getByTestId(CHECK));
    expect(line).toHaveAttribute("data-state", "error");
    // Not silence: the whole defect was that this container stayed empty.
    expect(container).not.toBeEmptyDOMElement();
    // Not the warning either: none of its furniture and none of its wording.
    expect(screen.queryByTestId(NOTICE)).not.toBeInTheDocument();
    expect(screen.queryByRole("region")).not.toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(container.querySelector("svg")).toBeNull();
    expect(line.textContent).not.toMatch(WARNING_WORDING);
  });

  it("T-57: the same line when the backend cannot be reached at all", async () => {
    // fetch itself rejects before any status code exists. request() turns
    // that into NETWORK_ERROR, a different path through the client than a
    // 500 takes and the likelier one in practice.
    mockFetchUnreachable();
    renderWithQueryClient(<RotationBackupNotice />);

    // findByText, not findByTestId: the pending line carries the same
    // testid and is on screen before the rejection lands.
    const line = await screen.findByText(/could not check for a copy from before a passphrase change/i);
    expect(line).toBe(screen.getByTestId(CHECK));
    expect(line).toHaveAttribute("data-state", "error");
    expect(line.textContent).not.toMatch(WARNING_WORDING);
    expect(screen.queryByTestId(NOTICE)).not.toBeInTheDocument();
  });

  it("holds a quiet placeholder until the answer lands, then shows the truth", async () => {
    const fetchMock = mockFetch({ "/vault/status": { body: PRESENT } });
    const release = delayRoute(fetchMock, "GET", "/vault/status", { body: PRESENT });
    const { container } = renderWithQueryClient(<RotationBackupNotice />);

    const line = screen.getByTestId(CHECK);
    expect(line).toHaveAttribute("data-state", "pending");
    expect(line.textContent).toMatch(/checking for a copy from before a passphrase change/i);
    // Quiet: no bordered section, no icon, no alert, and no verdict in
    // either direction while the answer is still on its way.
    expect(screen.queryByRole("region")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(container.querySelector("svg")).toBeNull();
    expect(line.textContent).not.toMatch(/could not/i);
    expect(line.textContent).not.toMatch(WARNING_WORDING);
    expect(screen.queryByTestId(NOTICE)).not.toBeInTheDocument();

    release();
    expect(await screen.findByTestId(NOTICE)).toBeInTheDocument();
    expect(screen.queryByTestId(CHECK)).not.toBeInTheDocument();
  });

  it("T-57z GROUND: a status query that answered with nothing to report renders nothing at all", async () => {
    // The settled signal comes from the real query cache, not from the DOM:
    // an empty container before the answer would pass this on a component
    // that never renders anything. See helpers/vaultStatusSettled.ts.
    mockFetch({ "/vault/status": { body: ABSENT } });
    await expectNothingOnceAnswered(renderWithQueryClient(<RotationBackupNotice />));
  });

  it("T-57z GROUND: an older backend that does not know the field renders nothing at all", async () => {
    mockFetch({ "/vault/status": { body: OLDER } });
    await expectNothingOnceAnswered(renderWithQueryClient(<RotationBackupNotice />));
  });

  it("T-57p POSITIVE: with the file there, the warning renders as before and no check line", async () => {
    mockFetch({ "/vault/status": { body: PRESENT } });
    renderWithQueryClient(<RotationBackupNotice />);

    expect(await screen.findByTestId(NOTICE)).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Copy left by an interrupted passphrase change" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "A copy from before a passphrase change" })).toBeInTheDocument();
    expect(screen.getByText("app.db.rekey.bak-1700000000")).toBeInTheDocument();
    expect(screen.getByText(/will not close that/i)).toBeInTheDocument();
    // No delete button, and that is the design (see the test above).
    expect(screen.queryByRole("button")).not.toBeInTheDocument();

    expect(screen.queryByTestId(CHECK)).not.toBeInTheDocument();
  });
});
