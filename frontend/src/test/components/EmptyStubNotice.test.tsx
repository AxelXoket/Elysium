/**
 * The leftover recovery file has to reach a screen, or it is not reported.
 *
 * /vault/status has carried orphaned_copy since it was added, and for a while
 * nothing rendered it - a field nobody paints is the same as a log line nobody
 * opens. empty_stub is the newest of the three and this is the test that keeps
 * it from repeating that.
 */
import { afterAll, beforeAll, describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { EmptyStubNotice } from "@/components/settings/EmptyStubNotice";
import { mockFetch } from "../mocks/api";
import {
  loadGlassRightCss,
  injectCss,
  wrapInGlassRight,
  surfaceToken,
} from "@/test/helpers/glassSurfaceCss";


const BASE = { initialized: true, unlocked: true };

describe("EmptyStubNotice", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it("says nothing when there is no leftover file", async () => {
    mockFetch({ "/vault/status": { body: { ...BASE, empty_stub: false } } });
    renderWithQueryClient(<EmptyStubNotice />);

    await waitFor(() => {
      expect(screen.queryByTestId("empty-stub-notice")).not.toBeInTheDocument();
    });
  });

  it("names the file when one is there", async () => {
    mockFetch({ "/vault/status": { body: { ...BASE, empty_stub: true } } });
    renderWithQueryClient(<EmptyStubNotice />);

    expect(await screen.findByTestId("empty-stub-notice")).toBeInTheDocument();
    // Naming it is the point: the user has to be able to match what the app
    // says against what they see in the folder.
    expect(screen.getByText("app.db.empty-stub-bak")).toBeInTheDocument();
  });

  it("removes it without asking twice", async () => {
    // No confirmation step, unlike the other two notices in this folder. They
    // guard copies of the user's data; this file is provably empty.
    const user = userEvent.setup();
    mockFetch({
      "/vault/status": { body: { ...BASE, empty_stub: true } },
      "/vault/discard-empty-stub": { body: { removed: true, reason: "" } },
    });
    renderWithQueryClient(<EmptyStubNotice />);

    await user.click(await screen.findByRole("button", { name: /remove it/i }));

    await waitFor(() => {
      expect(
        (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.some(
          (c: unknown[]) => String(c[0]).includes("/vault/discard-empty-stub"),
        ),
      ).toBe(true);
    });
  });

  it("reports a refusal instead of pretending it worked", async () => {
    // The backend re-measures the file and refuses one that is not empty. A
    // screen that swallowed that would leave the user believing a file is gone
    // while it is still there, which is the failure mode every notice in this
    // folder exists to prevent.
    const user = userEvent.setup();
    mockFetch({
      "/vault/status": { body: { ...BASE, empty_stub: true } },
      "/vault/discard-empty-stub": {
        body: { removed: false, reason: "not_empty" },
      },
    });
    renderWithQueryClient(<EmptyStubNotice />);

    await user.click(await screen.findByRole("button", { name: /remove it/i }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/not empty/i);
  });
});

/**
 * This notice renders inside `.glass-right`, the app's one light surface,
 * but `settings-hint` and `settings-error` are painted for the dark settings
 * dialog. See helpers/glassSurfaceCss.ts for why these tests read
 * custom-property tokens rather than `.color`.
 */
describe("EmptyStubNotice - light surface contrast", () => {
  let styleEl: HTMLStyleElement;

  beforeAll(async () => {
    styleEl = injectCss(await loadGlassRightCss());
  }, 20000);

  afterAll(() => styleEl.remove());

  afterEach(() => {
    document.querySelectorAll(".glass-right").forEach((el) => el.remove());
  });

  it("the housekeeping hint reads .glass-right's own muted token", async () => {
    const glassRight = wrapInGlassRight();
    mockFetch({ "/vault/status": { body: { ...BASE, empty_stub: true } } });
    renderWithQueryClient(<EmptyStubNotice />, {
      container: glassRight,
      baseElement: document.body,
    });

    const hint = await screen.findByTestId("empty-stub-hint");
    const mutedText = surfaceToken(glassRight, "--muted-foreground");
    expect(mutedText, "the surface never resolved a muted token").not.toBe("");
    expect(surfaceToken(hint, "--muted-foreground")).toBe(mutedText);
    expect(hint.className).not.toMatch(/settings-/);

    // Negative control: settings-hint's colour is a hardcoded rgba, not a
    // variable, so jsdom resolves `.color` for it directly.
    const oldHint = document.createElement("p");
    oldHint.className = "settings-hint";
    glassRight.appendChild(oldHint);
    expect(
      getComputedStyle(oldHint).color,
      "settings-hint's colour drifted - re-measure before trusting this ground",
    ).toBe("rgba(202, 212, 224, 0.72)");
  });

  it("a refusal alert uses persona-local-error, not settings-error", async () => {
    const user = userEvent.setup();
    const glassRight = wrapInGlassRight();
    mockFetch({
      "/vault/status": { body: { ...BASE, empty_stub: true } },
      "/vault/discard-empty-stub": { body: { removed: false, reason: "not_empty" } },
    });
    renderWithQueryClient(<EmptyStubNotice />, {
      container: glassRight,
      baseElement: document.body,
    });

    await user.click(await screen.findByRole("button", { name: /remove it/i }));
    const alert = await screen.findByRole("alert");
    expect(alert.className).not.toMatch(/settings-error/);
    expect(alert.className).toMatch(/persona-local-error/);
    // settings-error and persona-local-error both read the same :root-only
    // --color-es-danger token, which jsdom cannot resolve either way - see
    // AutoLockControl's contrast tests for the same note. Font-size is the
    // real, measured difference between the two hand-written rules.
    expect(getComputedStyle(alert).fontSize).toBe("12px");
  });
});
