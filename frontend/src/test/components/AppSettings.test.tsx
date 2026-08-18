/**
 * AppSettings.test.tsx - the bottom-left Settings panel.
 *
 * Covers:
 *  - Settings button opens the nested-page dialog at the root list
 *  - navigating into a category shows its page + back arrow returns to root
 *  - Text & readability sliders write the persisted store values
 *  - reset restores defaults
 *  - the Secrets row bridges to the right panel's Secrets tab and closes
 *  - closing the dialog resets navigation to the root page
 *  - ChatCanvas applies the reader variables to the message scroll area
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { SidebarFooter } from "@/components/sidebar/SidebarFooter";
import { ChatCanvas } from "@/components/chat/ChatCanvas";
import { GenerationSettingsProvider } from "@/components/generation/GenerationSettingsContext";
import {
  useUiStore,
  MSG_FONT_DEFAULT,
  MSG_LINE_DEFAULT,
} from "@/lib/store/uiStore";
import { mockFetch } from "../mocks/api";
import { settingsFixture } from "../mocks/fixtures";
import type { ReactNode } from "react";

function wrapper({ children }: { children: ReactNode }) {
  return (
    
      <GenerationSettingsProvider>{children}</GenerationSettingsProvider>
    
  );
}

async function openSettings(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Open settings" }));
  await screen.findByText("Appearance and reading preferences. Stored on this device only.");
}

describe("AppSettingsDialog", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({
      activeRightPanelTab: "models",
      msgFontPx: MSG_FONT_DEFAULT,
      msgLineHeight: MSG_LINE_DEFAULT,
      msgContrast: "default",
      narrationEnabled: true,
      quoteTintEnabled: true,
      // The dialog's open state moved out of SidebarFooter's useState and into
      // the store (the composer's voice hint has to be able to open it ON the
      // Voice page), so it is module-global and leaks between cases: a dialog
      // left open by the previous test makes the background inert and its
      // "Open settings" button unreachable.
      settingsOpen: false,
      settingsInitialPage: null,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("opens at the root category list", async () => {
    const user = userEvent.setup();
    mockFetch({});
    renderWithQueryClient(<SidebarFooter />, { wrapper });

    await openSettings(user);
    expect(screen.getByText("Text & readability")).toBeInTheDocument();
    expect(screen.getByText("Narration style")).toBeInTheDocument();
    expect(screen.getByText("Chat background")).toBeInTheDocument();
    expect(screen.getByText("Secrets & API")).toBeInTheDocument();
  });

  it("navigates into Text and the back arrow returns to the root", async () => {
    const user = userEvent.setup();
    mockFetch({});
    renderWithQueryClient(<SidebarFooter />, { wrapper });
    await openSettings(user);

    await user.click(screen.getByText("Text & readability"));
    expect(
      await screen.findByRole("slider", { name: "Font size slider" }),
    ).toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: "Back to settings" }),
    );
    expect(await screen.findByText("Narration style")).toBeInTheDocument();
    expect(
      screen.queryByRole("slider", { name: "Font size slider" }),
    ).not.toBeInTheDocument();
  });

  it("sliders write the persisted store and reset restores defaults", async () => {
    const user = userEvent.setup();
    mockFetch({});
    renderWithQueryClient(<SidebarFooter />, { wrapper });
    await openSettings(user);
    await user.click(screen.getByText("Text & readability"));

    const fontSlider = await screen.findByRole("slider", {
      name: "Font size slider",
    });
    // Range inputs: change events via fireEvent-style value set
    await user.pointer({ target: fontSlider });
    // jsdom sliders don't drag; set value directly through change
    const { fireEvent } = await import("@testing-library/react");
    fireEvent.change(fontSlider, { target: { value: "17" } });
    expect(useUiStore.getState().msgFontPx).toBe(17);

    fireEvent.change(
      screen.getByRole("slider", { name: "Line spacing slider" }),
      { target: { value: "1.8" } },
    );
    expect(useUiStore.getState().msgLineHeight).toBe(1.8);

    await user.click(
      screen.getByRole("button", { name: "Reset to defaults" }),
    );
    expect(useUiStore.getState().msgFontPx).toBe(MSG_FONT_DEFAULT);
    expect(useUiStore.getState().msgLineHeight).toBe(MSG_LINE_DEFAULT);
  });

  // v1.1 E2: message contrast radiogroup.
  it("contrast radiogroup writes the store and drives the preview class", async () => {
    const user = userEvent.setup();
    mockFetch({});
    renderWithQueryClient(<SidebarFooter />, { wrapper });
    await openSettings(user);
    await user.click(screen.getByText("Text & readability"));

    const group = await screen.findByRole("radiogroup", {
      name: "Message contrast",
    });
    const high = screen.getByRole("radio", { name: "High contrast" });
    await user.click(high);

    expect(useUiStore.getState().msgContrast).toBe("high");
    expect(high).toHaveAttribute("aria-checked", "true");
    // The dialog preview carries the preset class.
    const preview = document.querySelector(".settings-preview") as HTMLElement;
    expect(preview.className).toContain("msg-contrast-high");

    // Back to Default removes the class again.
    await user.click(
      within(group).getByRole("radio", { name: "Default contrast" }),
    );
    expect(useUiStore.getState().msgContrast).toBe("default");
    expect(
      (document.querySelector(".settings-preview") as HTMLElement).className,
    ).not.toContain("msg-contrast");
  });

  it("Reset covers font, line spacing AND contrast", async () => {
    const user = userEvent.setup();
    mockFetch({});
    useUiStore.setState({ msgFontPx: 17, msgLineHeight: 1.8, msgContrast: "high" });
    renderWithQueryClient(<SidebarFooter />, { wrapper });
    await openSettings(user);
    await user.click(screen.getByText("Text & readability"));

    await user.click(
      await screen.findByRole("button", { name: "Reset to defaults" }),
    );
    expect(useUiStore.getState().msgFontPx).toBe(MSG_FONT_DEFAULT);
    expect(useUiStore.getState().msgLineHeight).toBe(MSG_LINE_DEFAULT);
    expect(useUiStore.getState().msgContrast).toBe("default");

    // With everything at defaults, Reset is disabled (isDefault includes contrast).
    expect(
      screen.getByRole("button", { name: "Reset to defaults" }),
    ).toBeDisabled();
  });

  it("narration page toggles both store flags and previews the parser", async () => {
    const user = userEvent.setup();
    mockFetch({});
    renderWithQueryClient(<SidebarFooter />, { wrapper });
    await openSettings(user);

    await user.click(screen.getByText("Narration style"));
    const narration = await screen.findByRole("switch", {
      name: "Style narration",
    });
    expect(narration).toHaveAttribute("aria-checked", "true");
    // The preview runs the REAL parser: asterisks hidden, spans styled.
    expect(screen.getByText("She smiles softly and waves.")).toHaveClass(
      "narration-span",
    );
    expect(
      screen.getByText('"It is good to see you again."'),
    ).toHaveClass("quote-span");

    await user.click(narration);
    expect(useUiStore.getState().narrationEnabled).toBe(false);
    expect(narration).toHaveAttribute("aria-checked", "false");

    const quoteTint = screen.getByRole("switch", {
      name: "Tint quoted speech",
    });
    await user.click(quoteTint);
    expect(useUiStore.getState().quoteTintEnabled).toBe(false);
    // Both off → the raw sample shows, asterisks visible.
    expect(
      screen.getByText(
        '*She smiles softly and waves.* "It is good to see you again."',
      ),
    ).toBeInTheDocument();
  });

  it("Secrets row bridges to the secrets tab and closes the dialog", async () => {
    const user = userEvent.setup();
    mockFetch({});
    renderWithQueryClient(<SidebarFooter />, { wrapper });
    await openSettings(user);

    await user.click(screen.getByText("Secrets & API"));
    expect(useUiStore.getState().activeRightPanelTab).toBe("secrets");
    await waitFor(() => {
      expect(
        screen.queryByText("Text & readability"),
      ).not.toBeInTheDocument();
    });
  });

  it("reopening after close starts back at the root page", async () => {
    const user = userEvent.setup();
    mockFetch({});
    renderWithQueryClient(<SidebarFooter />, { wrapper });
    await openSettings(user);
    await user.click(screen.getByText("Text & readability"));
    await screen.findByRole("slider", { name: "Font size slider" });

    await user.keyboard("{Escape}");
    await waitFor(() => {
      expect(
        screen.queryByRole("slider", { name: "Font size slider" }),
      ).not.toBeInTheDocument();
    });

    await openSettings(user);
    expect(await screen.findByText("Text & readability")).toBeInTheDocument();
    expect(
      screen.queryByRole("slider", { name: "Font size slider" }),
    ).not.toBeInTheDocument();
  });

  it("takes back a narration voice the vault refused, and says so", async () => {
    // CHARACTERISATION, not approval. KUSUR-DEFTERI K-22.
    //
    // The swallowed `.catch(() => undefined)` in NarrationVoiceRow is not an
    // oversight, and the ledger entry that called it one needed correcting:
    // the comment directly above it documents the choice, "invalidated after
    // so a failed write corrects itself rather than leaving the UI claiming
    // something the vault never accepted". The correction does happen, and
    // this test proves it.
    //
    // What it also proves is the cost. Silent self-correction is still
    // silence: the row reads Narrator, then reads Same voice again a beat
    // later, and nobody is told which of the two the vault holds. Compare
    // AutoLockControl, which renders its own inline alert, and the stop
    // sequences a page over, which revert AND raise a toast.
    //
    // REWRITTEN for K-22. The last assertion used to require SILENCE, and the
    // silence was the defect: the row read Narrator, read Same voice again a
    // beat later, and nobody was told which of the two the vault holds.
    //
    // The revert is still correct and is still asserted. What is new is that
    // the refusal reaches the error store, which maps the backend's code to
    // its catalogued sentence - so this adds no user-facing text of its own.
    const user = userEvent.setup();
    const { useErrorStore } = await import("@/lib/errors");
    useErrorStore.getState().clearAll();

    const json = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json" },
      });
    // The refusal is held open on purpose. An awaited click flushes the
    // failure, the invalidate and the refetch in one go, so the optimistic
    // value never exists long enough to look at - which would leave this
    // test unable to tell "reverted" apart from "never applied".
    let releaseRefusal: (() => void) | null = null;
    let refused = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/tts/tag-prefs")) {
          if ((init?.method ?? "GET") !== "GET") {
            refused += 1;
            await new Promise<void>((r) => {
              releaseRefusal = r;
            });
            return json({ detail: "vault_locked" }, 423);
          }
          return json({
            narrative: "same", density: 8, tone: "", min: 0, max: 16,
            tone_max_chars: 60, speed: 1, speed_min: 0.8, speed_max: 1.25,
            gap: 0, gap_min: 0, gap_max: 1.5,
          });
        }
        if (url.includes("/tts/active")) return json({ uid: "u1", state: "loaded" });
        return json(settingsFixture);
      }),
    );

    renderWithQueryClient(<SidebarFooter />, { wrapper });
    await openSettings(user);
    await user.click(screen.getByText("Narration style"));

    const group = await screen.findByRole("radiogroup", { name: "Narration voice" });
    const narrator = within(group).getByRole("radio", { name: "Narrator" });
    const same = within(group).getByRole("radio", { name: "Same voice" });
    expect(same, "the row never reached its loaded state").toHaveAttribute(
      "aria-checked",
      "true",
    );

    await user.click(narrator);
    // The optimistic write lands first. Asserting it is what stops the rest
    // of this test from passing against a row that rendered nothing.
    expect(narrator).toHaveAttribute("aria-checked", "true");
    expect(refused, "the choice was never sent anywhere").toBe(1);

    await act(async () => {
      releaseRefusal?.();
      await Promise.resolve();
    });
    await waitFor(() =>
      expect(same, "the refused choice stayed on screen").toHaveAttribute(
        "aria-checked",
        "true",
      ),
    );
    await waitFor(() =>
      expect(
        useErrorStore.getState().errors,
        "the vault refused and the user was told nothing",
      ).toHaveLength(1),
    );
    expect(useErrorStore.getState().errors[0].code).toBe("vault_locked");

    vi.unstubAllGlobals();
  });
});

describe("Reader variables on the chat canvas", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({
      selectedChatId: null,
      selectedModelId: null,
      selectedCharacterId: null,
      msgFontPx: 17,
      msgLineHeight: 1.8,
      msgContrast: "default",
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({
      msgFontPx: MSG_FONT_DEFAULT,
      msgLineHeight: MSG_LINE_DEFAULT,
      msgContrast: "default",
    });
  });

  // v1.1 E3 hoist: the reader vars now live on <main> so the composer inherits
  // them; the scroller no longer carries them.
  it("applies --msg-fs/--msg-lh to <main>, not the scroll container", async () => {
    mockFetch({ "/settings": { body: settingsFixture } });
    const { container } = renderWithQueryClient(<ChatCanvas />, { wrapper });

    const main = container.querySelector("main") as HTMLElement;
    const scroller = container.querySelector(
      ".flex-1.overflow-y-auto",
    ) as HTMLElement;
    expect(main).not.toBeNull();
    expect(scroller).not.toBeNull();
    await waitFor(() => {
      expect(main.style.getPropertyValue("--msg-fs")).toBe("17px");
      expect(main.style.getPropertyValue("--msg-lh")).toBe("1.8");
    });
    // The scroller no longer carries the reader vars (moved up to <main>).
    expect(scroller.style.getPropertyValue("--msg-fs")).toBe("");
    expect(scroller.style.getPropertyValue("--msg-lh")).toBe("");
  });

  // v1.1 E2: the contrast preset class rides on the scroller.
  it("applies the msg-contrast preset class to the scroller", async () => {
    mockFetch({ "/settings": { body: settingsFixture } });
    useUiStore.setState({ msgContrast: "high" });
    const { container } = renderWithQueryClient(<ChatCanvas />, { wrapper });

    const scroller = container.querySelector(
      ".flex-1.overflow-y-auto",
    ) as HTMLElement;
    await waitFor(() => {
      expect(scroller.className).toContain("msg-contrast-high");
    });

    // Default sets NO msg-contrast class (zero-change baseline).
    useUiStore.setState({ msgContrast: "default" });
    await waitFor(() => {
      const s = container.querySelector(
        ".flex-1.overflow-y-auto",
      ) as HTMLElement;
      expect(s.className).not.toContain("msg-contrast");
    });
  });
});
