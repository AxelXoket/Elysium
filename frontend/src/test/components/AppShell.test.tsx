import { describe, it, expect, afterEach, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { Providers } from "@/app/providers";
import { AppShell } from "@/components/layout/AppShell";
import { queryClient } from "@/lib/query/queryClient";
import { useUiStore } from "@/lib/store/uiStore";
import { mockFetch } from "@/test/mocks/api";
import {
  settingsFixture,
  proxyHealthFixture,
  characterFixture,
  chatFixture,
  modelListFixture,
} from "@/test/mocks/fixtures";

describe("T-01: App shell renders without crash", () => {
  // Mock fetch so these synchronous render assertions neither hit a live dev
  // backend nor leak unhandled rejections into the shared singleton client.
  beforeEach(() => {
    mockFetch({
      "/settings/proxy/health": { body: proxyHealthFixture },
      "/settings": { body: settingsFixture },
      "/characters": { body: [characterFixture] },
      "/models/openrouter": { body: modelListFixture },
      "/personas": { body: [] },
      "/chats": { body: [chatFixture] },
    });
  });
  afterEach(() => {
    vi.restoreAllMocks();
    queryClient.clear();
  });

  it("renders the Elysium heading", () => {
    render(
      <Providers>
        <AppShell />
      </Providers>,
    );
    // "Elysium" now renders as the brand wordmark in more than one place
    // (sidebar header + empty-state welcome), so assert presence, not unique.
    expect(screen.getAllByText("Elysium").length).toBeGreaterThan(0);
  });

  it("renders the sidebar with Characters section", () => {
    render(
      <Providers>
        <AppShell />
      </Providers>,
    );
    expect(screen.getByText("Characters")).toBeInTheDocument();
  });

  // Updated from "Settings" â†’ "Secrets" (Phase 6E-A tab rename)
  it("renders the right panel with Secrets tab", () => {
    render(
      <Providers>
        <AppShell />
      </Providers>,
    );
    expect(screen.getByRole("tab", { name: /secrets/i })).toBeInTheDocument();
  });

  it("renders the composer", () => {
    render(
      <Providers>
        <AppShell />
      </Providers>,
    );
    const textarea = screen.getByLabelText("Message");
    expect(textarea).toBeDisabled();
  });

  // T-72: Right panel renders Models tab
  it("T-72: renders Models tab", () => {
    render(
      <Providers>
        <AppShell />
      </Providers>,
    );
    expect(screen.getByRole("tab", { name: /models/i })).toBeInTheDocument();
  });

  // T-73: Right panel renders Secrets tab
  it("T-73: renders Secrets tab", () => {
    render(
      <Providers>
        <AppShell />
      </Providers>,
    );
    expect(screen.getByRole("tab", { name: /secrets/i })).toBeInTheDocument();
  });

  // T-74: Right panel renders Persona tab
  it("T-74: renders Persona tab", () => {
    render(
      <Providers>
        <AppShell />
      </Providers>,
    );
    expect(screen.getByRole("tab", { name: /persona/i })).toBeInTheDocument();
  });

  // T-75: Sidebar footer shows the app version (injected from package.json)
  it("T-75: sidebar footer shows the app version", () => {
    render(
      <Providers>
        <AppShell />
      </Providers>,
    );
    expect(screen.getByText(/^v\d+\.\d+/i)).toBeInTheDocument();
  });
});

describe("AppShell stale selection reconciliation", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    queryClient.clear();
    useUiStore.setState({
      selectedChatId: null,
      selectedCharacterId: null,
      selectedModelId: null,
    });
  });

  it("clears a stale persisted chat selection once server data loads", async () => {
    queryClient.clear();
    mockFetch({
      "/settings/proxy/health": { body: proxyHealthFixture },
      "/settings": { body: settingsFixture },
      "/characters": { body: [characterFixture] },
      "/models/openrouter": { body: modelListFixture },
      "/personas": { body: [] },
      "/chats": { body: [chatFixture] },
    });
    useUiStore.setState({
      selectedChatId: 999, // persisted id that no longer exists server-side
      selectedCharacterId: characterFixture.id,
      selectedModelId: "openai/gpt-4o",
    });

    render(
      <Providers>
        <AppShell />
      </Providers>,
    );

    await waitFor(() => {
      expect(useUiStore.getState().selectedChatId).toBeNull();
    });
    // Valid selections survive
    expect(useUiStore.getState().selectedCharacterId).toBe(characterFixture.id);
    expect(useUiStore.getState().selectedModelId).toBe("openai/gpt-4o");
  });
});
