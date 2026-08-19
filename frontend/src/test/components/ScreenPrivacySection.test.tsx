/**
 * A55's missing half.
 *
 * The backend shipped a while ago - the vault-stored setting, the
 * apply-on-unlock / remove-on-lock transition, its tests. Nothing in the UI
 * ever reached it, so the toggle the owner asked for existed only as an HTTP
 * route. These tests are about the switch being honest: off by default,
 * inert until the stored value is known, and saying what it does not do.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { ScreenPrivacySection } from "@/components/settings/ScreenPrivacySection";
import { mockFetch } from "../mocks/api";
import { settingsFixture } from "../mocks/fixtures";

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
