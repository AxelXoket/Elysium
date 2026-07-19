/**
 * MistCanvas.test.tsx - gating ladder for the living mist backdrop.
 *
 * jsdom has no WebGL, so the deepest reachable state here is "WebGL
 * unavailable → permanent fallback"; the ladder above it (toggle off,
 * reduced motion, small viewport) must all render nothing. Also covers the
 * Settings root toggle wiring.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  MistCanvas,
  CanvasMist,
  PanelMist,
} from "@/components/backdrop/MistCanvas";
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

/** matchMedia stub where only the given queries report matches: true. */
function stubMatchMedia(matching: string[]) {
  vi.stubGlobal(
    "matchMedia",
    (query: string) => ({
      matches: matching.some((m) => query.includes(m)),
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  );
}

describe("MistCanvas gating ladder", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    useUiStore.setState({ ambientFogOn: true });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    useUiStore.setState({ ambientFogOn: true });
  });

  it("renders nothing when the toggle is off", () => {
    stubMatchMedia(["min-width: 901px"]);
    useUiStore.setState({ ambientFogOn: false });
    const { container } = render(<MistCanvas />);
    expect(container.querySelector("canvas")).toBeNull();
  });

  it("renders nothing under prefers-reduced-motion", () => {
    stubMatchMedia(["min-width: 901px", "prefers-reduced-motion"]);
    const { container } = render(<MistCanvas />);
    expect(container.querySelector("canvas")).toBeNull();
  });

  it("renders nothing on small viewports (fog fully covered by the frame)", () => {
    stubMatchMedia([]); // desktop query does not match
    const { container } = render(<MistCanvas />);
    expect(container.querySelector("canvas")).toBeNull();
  });

  it("falls back permanently when WebGL is unavailable (jsdom)", async () => {
    stubMatchMedia(["min-width: 901px"]);
    const { container } = render(<MistCanvas />);
    // The canvas mounts, getContext("webgl") returns null in jsdom, and the
    // component settles into the static-gradient fallback (no canvas).
    await waitFor(() => {
      expect(container.querySelector("canvas")).toBeNull();
    });
  });
});

describe("CanvasMist (in-canvas frosted-glass fog)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    useUiStore.setState({ ambientFogOn: true });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    useUiStore.setState({ ambientFogOn: true });
  });

  it("follows the same toggle as the backdrop fog", () => {
    stubMatchMedia(["min-width: 901px"]);
    useUiStore.setState({ ambientFogOn: false });
    const { container } = render(<CanvasMist />);
    expect(container.querySelector(".canvas-mist")).toBeNull();
  });

  it("renders nothing under prefers-reduced-motion", () => {
    stubMatchMedia(["min-width: 901px", "prefers-reduced-motion"]);
    const { container } = render(<CanvasMist />);
    expect(container.querySelector(".canvas-mist")).toBeNull();
  });

  it("mounts the fog canvas under the milk veil, then falls back cleanly without WebGL (jsdom)", async () => {
    stubMatchMedia(["min-width: 901px"]);
    const { container } = render(<CanvasMist />);
    // Wrapper + milk render synchronously; jsdom then reports no WebGL and
    // the whole layer unmounts to the static fallback.
    await waitFor(() => {
      expect(container.querySelector(".canvas-mist")).toBeNull();
      expect(container.querySelector("canvas")).toBeNull();
    });
  });
});

describe("PanelMist (frosted side panels)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    useUiStore.setState({ ambientFogOn: true });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    useUiStore.setState({ ambientFogOn: true });
  });

  it("follows the shared toggle", () => {
    stubMatchMedia(["min-width: 901px"]);
    useUiStore.setState({ ambientFogOn: false });
    const { container } = render(<PanelMist side="left" />);
    expect(container.querySelector(".panel-mist")).toBeNull();
  });

  it("falls back cleanly without WebGL for both sides (jsdom)", async () => {
    stubMatchMedia(["min-width: 901px"]);
    const left = render(<PanelMist side="left" />);
    const right = render(<PanelMist side="right" />);
    await waitFor(() => {
      expect(left.container.querySelector(".panel-mist")).toBeNull();
      expect(right.container.querySelector(".panel-mist")).toBeNull();
    });
  });
});

describe("Ambient mist settings toggle", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ ambientFogOn: true });
  });

  it("flips the persisted flag from the Settings root page", async () => {
    const user = userEvent.setup();
    mockFetch({});
    render(<SidebarFooter />, { wrapper });

    await user.click(screen.getByRole("button", { name: "Open settings" }));
    const toggle = await screen.findByRole("switch", {
      name: "Ambient mist",
    });
    expect(toggle).toHaveAttribute("aria-checked", "true");
    await user.click(toggle);
    expect(useUiStore.getState().ambientFogOn).toBe(false);
    expect(toggle).toHaveAttribute("aria-checked", "false");
  });
});
