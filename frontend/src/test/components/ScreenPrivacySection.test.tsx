/**
 * A55's missing half.
 *
 * The backend shipped a while ago - the vault-stored setting, the
 * apply-on-unlock / remove-on-lock transition, its tests. Nothing in the UI
 * ever reached it, so the toggle the owner asked for existed only as an HTTP
 * route. These tests are about the switch being honest: off by default,
 * inert until the stored value is known, and saying what it does not do.
 */
import { afterAll, beforeAll, describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { ScreenPrivacySection } from "@/components/settings/ScreenPrivacySection";
import { mockFetch } from "../mocks/api";
import { settingsFixture } from "../mocks/fixtures";
import {
  loadGlassRightCss,
  injectCss,
  wrapInGlassRight,
  surfaceToken,
} from "@/test/helpers/glassSurfaceCss";

function mount(enabled = false) {
  mockFetch({
    "POST /settings/screen-privacy": { body: { ok: true } },
    "/settings": { body: { ...settingsFixture,
                           screen_privacy_enabled: enabled } },
  });
  return renderWithQueryClient(<ScreenPrivacySection />);
}

const SWITCH = /hide this window from screen capture/i;

describe("ScreenPrivacySection", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it("is off by default", async () => {
    // The owner said they take screenshots of this app. A protection that
    // silently blanks them is a bug report, not a feature.
    mount(false);
    await waitFor(() =>
      expect(screen.getByRole("switch", { name: SWITCH })).not.toBeChecked());
  });

  it("shows the stored position, not a guess", async () => {
    mount(true);
    await waitFor(() =>
      expect(screen.getByRole("switch", { name: SWITCH })).toBeChecked());
  });

  it("is inert until the stored value is known", async () => {
    // A protection switch that shows a guess and accepts a click is the worst
    // kind of all: the user comes away believing it is on.
    let release: () => void = () => {};
    const held = new Promise<void>((r) => { release = r; });
    const json = (body: unknown) =>
      new Response(JSON.stringify(body),
                   { status: 200,
                     headers: { "Content-Type": "application/json" } });

    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/vault/status"))
        return json({ initialized: true, unlocked: true });
      if (url.includes("/settings")) {
        await held;
        return json(settingsFixture);
      }
      return json({});
    }));

    const user = userEvent.setup();
    renderWithQueryClient(<ScreenPrivacySection />);

    // Behaviour, not the attribute: clicking it must not save anything.
    await user.click(screen.getByRole("switch", { name: SWITCH }));
    expect((globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls
      .some((c: unknown[]) => String(c[0]).includes("screen-privacy")))
      .toBe(false);

    release();
    // And once it knows, the same click does save.
    await waitFor(() =>
      expect(screen.getByRole("switch", { name: SWITCH }))
        .not.toHaveAttribute("aria-disabled", "true"));
    await user.click(screen.getByRole("switch", { name: SWITCH }));
    await waitFor(() =>
      expect((globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls
        .some((c: unknown[]) => String(c[0]).includes("screen-privacy")))
        .toBe(true));
  });

  it("saves to the vault when flipped", async () => {
    const user = userEvent.setup();
    mount(false);
    const sw = screen.getByRole("switch", { name: SWITCH });
    await waitFor(() => expect(sw).toBeEnabled());
    await user.click(sw);

    await waitFor(() => {
      const call = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls
        .find((c: unknown[]) =>
          String(c[0]).includes("/settings/screen-privacy")
          && (c[1] as RequestInit | undefined)?.method === "POST");
      expect(call).toBeTruthy();
      expect(JSON.parse(String((call![1] as RequestInit).body)))
        .toEqual({ screen_privacy_enabled: true });
    });
  });

  it("says it does not apply while the vault is locked", async () => {
    // A user who locks, screenshots the lock screen and sees it work would
    // otherwise conclude the switch is broken.
    mount(true);
    expect(screen.getByText(/not applied while the vault is locked/i))
      .toBeInTheDocument();
  });

  it("says it is a layer and not a guarantee", async () => {
    mount(true);
    expect(screen.getByText(/not every possible way a screen can be read/i))
      .toBeInTheDocument();
  });
});

/**
 * This is the worst case the whole audit found: the label of a SECURITY
 * control, rendered inside `.glass-right` (the app's one light surface) with
 * `settings-section-title` and `settings-hint` - classes painted for the dark
 * settings dialog. See helpers/glassSurfaceCss.ts for why these tests read
 * custom-property tokens rather than `.color`: jsdom cannot run Tailwind v4's
 * generated utility rules, so `.color` on a `text-muted-foreground` element
 * comes back as the browser default rather than the real declared value.
 */
describe("ScreenPrivacySection - light surface contrast", () => {
  let styleEl: HTMLStyleElement;

  beforeAll(async () => {
    styleEl = injectCss(await loadGlassRightCss());
  }, 20000);

  afterAll(() => styleEl.remove());

  afterEach(() => {
    document.querySelectorAll(".glass-right").forEach((el) => el.remove());
  });

  it("the heading and every hint read .glass-right's own text tokens", async () => {
    const glassRight = wrapInGlassRight();
    mockFetch({
      "/settings": { body: { ...settingsFixture, screen_privacy_enabled: false } },
    });
    renderWithQueryClient(<ScreenPrivacySection />, {
      container: glassRight,
      baseElement: document.body,
    });

    const heading = await screen.findByTestId("screen-privacy-heading");
    const switchLabel = screen.getByTestId("screen-privacy-switch-label");
    const hint1 = screen.getByTestId("screen-privacy-hint-1");
    const hint2 = screen.getByTestId("screen-privacy-hint-2");

    const lightText = surfaceToken(glassRight, "--color-es-text-light");
    const mutedText = surfaceToken(glassRight, "--muted-foreground");
    expect(lightText, "the surface never resolved a text-light token").not.toBe("");
    expect(mutedText, "the surface never resolved a muted token").not.toBe("");

    expect(surfaceToken(heading, "--color-es-text-light")).toBe(lightText);
    for (const el of [switchLabel, hint1, hint2]) {
      expect(surfaceToken(el, "--muted-foreground")).toBe(mutedText);
      expect(el.className).not.toMatch(/settings-/);
    }
    expect(heading.className).not.toMatch(/settings-/);

    // Negative control: `settings-section-title`'s colour is also a
    // hardcoded rgba, not a variable - jsdom resolves it directly, and it is
    // the class this heading used to carry.
    const oldHeading = document.createElement("h4");
    oldHeading.className = "settings-section-title";
    glassRight.appendChild(oldHeading);
    expect(
      getComputedStyle(oldHeading).color,
      "settings-section-title's colour drifted - re-measure before trusting this ground",
    ).toBe("rgba(202, 212, 224, 0.62)");
  });
});
