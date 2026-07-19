/**
 * SidebarNav.test.tsx - the restructured sidebar top matter.
 *
 * Covers:
 *  - CharacterList filters by the SEARCH query in place (and the two distinct
 *    empty states: no matches vs no characters at all)
 *  - the pinned New Character / import actions render (moved out of the header)
 *  - PersonaStrip shows the active persona, opens the switcher, selecting a
 *    sibling POSTs /personas/{id}/select, and "Manage personas" routes to the
 *    Persona tab
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { CharacterList } from "@/components/sidebar/CharacterList";
import { PersonaStrip } from "@/components/sidebar/PersonaStrip";
import { useUiStore } from "@/lib/store/uiStore";
import { useErrorStore } from "@/lib/errors";
import { mockFetch } from "../mocks/api";
import { characterFixture, personaFixture } from "../mocks/fixtures";
import type { Character } from "@/lib/schemas/characters";
import type { Persona } from "@/lib/schemas/personas";
import type { ReactNode } from "react";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

function character(id: number, name: string): Character {
  return { ...characterFixture, id, name };
}

function persona(id: number, name: string, active: boolean): Persona {
  return { ...personaFixture, id, display_name: name, is_active: active };
}

describe("CharacterList - search filter", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("shows all characters when the query is empty", async () => {
    mockFetch({
      "/characters": { body: [character(1, "Madeline"), character(2, "Selim")] },
    });
    render(<CharacterList query="" />, { wrapper });

    expect(await screen.findByText("Madeline")).toBeInTheDocument();
    expect(screen.getByText("Selim")).toBeInTheDocument();
  });

  it("filters by name (case-insensitive, substring)", async () => {
    mockFetch({
      "/characters": { body: [character(1, "Madeline"), character(2, "Selim")] },
    });
    render(<CharacterList query="mad" />, { wrapper });

    expect(await screen.findByText("Madeline")).toBeInTheDocument();
    expect(screen.queryByText("Selim")).not.toBeInTheDocument();
  });

  it("shows a no-matches state distinct from no-characters", async () => {
    mockFetch({
      "/characters": { body: [character(1, "Madeline")] },
    });
    render(<CharacterList query="zzz" />, { wrapper });

    expect(
      await screen.findByText('No characters match "zzz"'),
    ).toBeInTheDocument();
    expect(screen.queryByText("Madeline")).not.toBeInTheDocument();
  });

  it("pins the New Character action and import affordance at the foot", async () => {
    mockFetch({ "/characters": { body: [] } });
    render(<CharacterList query="" />, { wrapper });

    expect(await screen.findByText("No characters yet")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /new character/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Import character from JSON" }),
    ).toBeInTheDocument();
  });
});

describe("PersonaStrip", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ activeRightPanelTab: "models" });
    useErrorStore.getState().clearAll();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the active persona name", async () => {
    mockFetch({
      "/personas": {
        body: [persona(1, "Selim", true), persona(2, "Ayla", false)],
      },
    });
    render(<PersonaStrip />, { wrapper });

    expect(await screen.findByText("Selim")).toBeInTheDocument();
    // The eyebrow label frames it as "Persona", never "Playing as".
    expect(screen.getByText("Persona")).toBeInTheDocument();
    expect(screen.queryByText(/playing as/i)).not.toBeInTheDocument();
  });

  it("falls back to 'No persona' when none is active", async () => {
    mockFetch({ "/personas": { body: [persona(1, "Selim", false)] } });
    render(<PersonaStrip />, { wrapper });

    expect(await screen.findByText("No persona")).toBeInTheDocument();
  });

  it("opens the switcher and selecting a sibling POSTs /select", async () => {
    const user = userEvent.setup();
    const mock = mockFetch({
      "/personas/2/select": { body: { ok: true, selected_persona_id: 2 } },
      "/personas": {
        body: [persona(1, "Selim", true), persona(2, "Ayla", false)],
      },
    });
    render(<PersonaStrip />, { wrapper });

    await screen.findByText("Selim");
    await user.click(screen.getByRole("button", { name: "Change persona" }));

    const menu = await screen.findByRole("menu", { name: "Select persona" });
    // Active sibling is marked; picking the inactive one calls select.
    expect(
      within(menu).getByRole("menuitemradio", { name: /Selim/ }),
    ).toHaveAttribute("aria-checked", "true");
    await user.click(
      within(menu).getByRole("menuitemradio", { name: /Ayla/ }),
    );

    await waitFor(() => {
      expect(
        mock.mock.calls.some(
          ([url, init]) =>
            String(url).includes("/personas/2/select") &&
            (init as RequestInit | undefined)?.method === "POST",
        ),
      ).toBe(true);
    });
  });

  it("'Manage personas' routes to the Persona tab", async () => {
    const user = userEvent.setup();
    mockFetch({ "/personas": { body: [persona(1, "Selim", true)] } });
    render(<PersonaStrip />, { wrapper });

    await screen.findByText("Selim");
    await user.click(screen.getByRole("button", { name: "Change persona" }));
    await user.click(await screen.findByText("Manage personas"));

    expect(useUiStore.getState().activeRightPanelTab).toBe("persona");
  });
});
