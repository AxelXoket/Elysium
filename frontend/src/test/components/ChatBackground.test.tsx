/**
 * ChatBackground.test.tsx - chat wallpaper math, store actions, and the
 * settings page gating.
 *
 * The image pipeline itself (createImageBitmap/canvas) is environment-bound
 * and not exercised in jsdom; the Wisteria-parity FORMULAS and the state
 * machine around them are what this file pins.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  hexLum,
  resolveTint,
  computeEff,
  buildBgLayers,
  CHAT_BG_PAPER,
  CHAT_BG_INK,
} from "@/lib/appearance/chatBackground";
import { SidebarFooter } from "@/components/sidebar/SidebarFooter";
import { GenerationSettingsProvider } from "@/components/generation/GenerationSettingsContext";
import { useUiStore } from "@/lib/store/uiStore";
import { mockFetch } from "../mocks/api";
import type { ReactNode } from "react";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return (
    <QueryClientProvider client={qc}>
      <GenerationSettingsProvider>{children}</GenerationSettingsProvider>
    </QueryClientProvider>
  );
}

describe("chat background math (Wisteria parity)", () => {
  it("hexLum uses non-linear Rec.709 weights", () => {
    expect(hexLum("#ffffff")).toBeCloseTo(1, 5);
    expect(hexLum("#000000")).toBeCloseTo(0, 5);
    expect(hexLum(CHAT_BG_PAPER)).toBeGreaterThan(0.85);
    expect(hexLum(CHAT_BG_INK)).toBeLessThan(0.15);
    expect(hexLum("not-a-color")).toBe(0.5); // safe fallback
  });

  it("auto tint reinforces the image's brightness class at 0.55", () => {
    expect(resolveTint("auto", 0.56)).toBe(CHAT_BG_PAPER);
    expect(resolveTint("auto", 0.54)).toBe(CHAT_BG_INK);
    expect(resolveTint("#123456", 0.9)).toBe("#123456");
  });

  it("computeEff blends image and tint luminance by contrast", () => {
    // eff = lum*(1-c) + hexLum(tint)*c
    expect(computeEff(0.2, 0, "#ffffff")).toBeCloseTo(0.2, 5);
    expect(computeEff(0.2, 0.5, "#ffffff")).toBeCloseTo(0.6, 5);
    // dark image + auto ink scrim stays dark → light text mode
    const tint = resolveTint("auto", 0.2);
    expect(computeEff(0.2, 0.35, tint)).toBeLessThan(0.5);
  });

  it("buildBgLayers puts the scrim first and matches per-layer lists", () => {
    const layers = buildBgLayers("blob:abc-123", "#161a1d", 0.35);
    expect(layers).not.toBeNull();
    expect(layers!.backgroundImage).toBe(
      'linear-gradient(rgba(22, 26, 29, 0.35), rgba(22, 26, 29, 0.35)), url("blob:abc-123")',
    );
    expect(layers!.backgroundSize).toBe("100% 100%, cover");
    expect(layers!.backgroundPosition).toBe("0 0, center");
    expect(layers!.backgroundRepeat).toBe("no-repeat");
  });

  it("buildBgLayers rejects url-injection and bad tints", () => {
    expect(buildBgLayers('blob:x" evil', "#161a1d", 0.35)).toBeNull();
    expect(buildBgLayers("blob:ok", "auto", 0.35)).toBeNull(); // must be resolved first
  });
});

describe("chat background store actions", () => {
  beforeEach(() => {
    useUiStore.setState({
      chatBgOn: false,
      chatBgLum: 0.5,
      chatBgContrast: 0.35,
      chatBgTint: "auto",
      chatBgRev: 0,
    });
  });

  it("setChatBgMeta turns on, clamps lum, bumps rev, keeps contrast/tint", () => {
    useUiStore.setState({ chatBgContrast: 0.6, chatBgTint: "#2A3648" });
    useUiStore.getState().setChatBgMeta({ lum: 1.7 });
    const s = useUiStore.getState();
    expect(s.chatBgOn).toBe(true);
    expect(s.chatBgLum).toBe(1);
    expect(s.chatBgRev).toBe(1);
    expect(s.chatBgContrast).toBe(0.6); // untouched (Wisteria parity)
    expect(s.chatBgTint).toBe("#2A3648");
  });

  it("clearChatBg only flips the flag (prefs kept for the next image)", () => {
    useUiStore.getState().setChatBgMeta({ lum: 0.3 });
    useUiStore.setState({ chatBgTint: "#6f8a66" });
    useUiStore.getState().clearChatBg();
    const s = useUiStore.getState();
    expect(s.chatBgOn).toBe(false);
    expect(s.chatBgTint).toBe("#6f8a66");
  });

  it("contrast clamps to 0..0.85 and tint validates or falls back to auto", () => {
    const st = useUiStore.getState();
    st.setChatBgContrast(2);
    expect(useUiStore.getState().chatBgContrast).toBe(0.85);
    st.setChatBgContrast(-1);
    expect(useUiStore.getState().chatBgContrast).toBe(0);
    st.setChatBgTint("#ABCdef");
    expect(useUiStore.getState().chatBgTint).toBe("#ABCdef");
    st.setChatBgTint('url("evil")');
    expect(useUiStore.getState().chatBgTint).toBe("auto");
  });
});

describe("Background settings page", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({
      activeRightPanelTab: "models",
      chatBgOn: false,
      chatBgContrast: 0.35,
      chatBgTint: "auto",
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  async function openBackgroundPage(user: ReturnType<typeof userEvent.setup>) {
    await user.click(screen.getByRole("button", { name: "Open settings" }));
    await user.click(await screen.findByText("Chat background"));
    await screen.findByRole("button", { name: /choose image/i });
  }

  it("without an image: controls disabled, no Remove, interlock note shown", async () => {
    const user = userEvent.setup();
    mockFetch({});
    render(<SidebarFooter />, { wrapper });
    await openBackgroundPage(user);

    expect(
      screen.queryByRole("button", { name: "Remove" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText("Contrast and tint have no effect without an image."),
    ).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Auto tint" })).toBeDisabled();
  });

  it("with an image: contrast writes the store and tint chips select", async () => {
    const user = userEvent.setup();
    mockFetch({});
    useUiStore.setState({ chatBgOn: true, chatBgLum: 0.3 });
    render(<SidebarFooter />, { wrapper });
    await user.click(screen.getByRole("button", { name: "Open settings" }));
    await user.click(await screen.findByText("Chat background"));

    expect(
      await screen.findByRole("button", { name: "Remove" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Change image" }),
    ).toBeInTheDocument();

    fireEvent.change(
      screen.getByRole("slider", { name: "Contrast slider" }),
      { target: { value: "0.6" } },
    );
    expect(useUiStore.getState().chatBgContrast).toBe(0.6);

    const moss = screen.getByRole("radio", { name: "Slate tint" });
    expect(moss).not.toBeDisabled();
    await user.click(moss);
    expect(useUiStore.getState().chatBgTint).toBe("#2A3648");
    expect(moss).toHaveAttribute("aria-checked", "true");

    // Remove flips the flag off (blob deletion is a no-op in jsdom).
    await user.click(screen.getByRole("button", { name: "Remove" }));
    expect(useUiStore.getState().chatBgOn).toBe(false);
  });
});
