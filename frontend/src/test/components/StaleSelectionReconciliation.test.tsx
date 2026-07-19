/**
 * StaleSelectionReconciliation.test.tsx - persisted selections are validated
 * against server data once list queries succeed.
 *
 * Rules under test:
 *  - stale selectedChatId / selectedCharacterId / selectedModelId → cleared
 *  - valid selections → untouched
 *  - loading or error states NEVER clear (e.g. models 401 before API key set)
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { renderHook, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useStaleSelectionReconciliation } from "@/app/useStaleSelectionReconciliation";
import { useUiStore } from "@/lib/store/uiStore";
import { mockFetch } from "../mocks/api";
import { keys } from "@/lib/query/keys";
import {
  chatFixture,
  characterFixture,
  modelListFixture,
} from "../mocks/fixtures";
import type { ReactNode } from "react";

function createWrapper(qc?: QueryClient) {
  const client =
    qc ?? new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

/** Valid server data: chat 1, character 1, model "openai/gpt-4o". */
function mockAllListsValid() {
  return mockFetch({
    "/characters": { body: [characterFixture] },
    "/models/openrouter": { body: modelListFixture },
    "/chats": { body: [chatFixture] },
  });
}

describe("useStaleSelectionReconciliation", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({
      selectedChatId: null,
      selectedCharacterId: null,
      selectedModelId: null,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("clears a stale chat selection and keeps valid character/model", async () => {
    mockAllListsValid();
    useUiStore.setState({
      selectedChatId: 999,
      selectedCharacterId: characterFixture.id,
      selectedModelId: "openai/gpt-4o",
    });

    renderHook(() => useStaleSelectionReconciliation(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(useUiStore.getState().selectedChatId).toBeNull();
    });
    expect(useUiStore.getState().selectedCharacterId).toBe(characterFixture.id);
    expect(useUiStore.getState().selectedModelId).toBe("openai/gpt-4o");
  });

  it("clears a stale character selection", async () => {
    mockAllListsValid();
    useUiStore.setState({
      selectedChatId: chatFixture.id,
      selectedCharacterId: 999,
      selectedModelId: "openai/gpt-4o",
    });

    renderHook(() => useStaleSelectionReconciliation(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(useUiStore.getState().selectedCharacterId).toBeNull();
    });
    // Store semantics: clearing the character clears its chat selection too
    expect(useUiStore.getState().selectedChatId).toBeNull();
    expect(useUiStore.getState().selectedModelId).toBe("openai/gpt-4o");
  });

  it("clears a stale model selection", async () => {
    mockAllListsValid();
    useUiStore.setState({
      selectedChatId: chatFixture.id,
      selectedCharacterId: characterFixture.id,
      selectedModelId: "vendor/removed-model",
    });

    renderHook(() => useStaleSelectionReconciliation(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(useUiStore.getState().selectedModelId).toBeNull();
    });
    expect(useUiStore.getState().selectedChatId).toBe(chatFixture.id);
    expect(useUiStore.getState().selectedCharacterId).toBe(characterFixture.id);
  });

  it("keeps all selections when they exist in server data", async () => {
    const fetchMock = mockAllListsValid();
    useUiStore.setState({
      selectedChatId: chatFixture.id,
      selectedCharacterId: characterFixture.id,
      selectedModelId: "openai/gpt-4o",
    });

    renderHook(() => useStaleSelectionReconciliation(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      const urls = fetchMock.mock.calls.map(([url]) => String(url));
      expect(urls.some((u) => u.includes("/chats"))).toBe(true);
      expect(urls.some((u) => u.includes("/characters"))).toBe(true);
      expect(urls.some((u) => u.includes("/models/openrouter"))).toBe(true);
    });
    // Let queries settle and effects run
    await act(async () => {
      await new Promise((r) => setTimeout(r, 25));
    });

    expect(useUiStore.getState().selectedChatId).toBe(chatFixture.id);
    expect(useUiStore.getState().selectedCharacterId).toBe(characterFixture.id);
    expect(useUiStore.getState().selectedModelId).toBe("openai/gpt-4o");
  });

  it("does NOT clear the model selection when the models query errors (401)", async () => {
    mockFetch({
      "/characters": { body: [characterFixture] },
      "/models/openrouter": { status: 401, body: { detail: "api_key_missing" } },
      "/chats": { body: [chatFixture] },
    });
    useUiStore.setState({
      selectedChatId: 999, // stale - proves reconciliation ran
      selectedCharacterId: characterFixture.id,
      selectedModelId: "openai/gpt-4o",
    });

    renderHook(() => useStaleSelectionReconciliation(), {
      wrapper: createWrapper(),
    });

    // Chats reconciliation ran (stale chat cleared)…
    await waitFor(() => {
      expect(useUiStore.getState().selectedChatId).toBeNull();
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 25));
    });
    // …but the errored models query must not wipe the model selection
    expect(useUiStore.getState().selectedModelId).toBe("openai/gpt-4o");
  });

  it("does NOT clear a freshly-created chat while the list is refetching (race regression)", async () => {
    // Server now knows about the new chat (id 163)…
    const newChat = { ...chatFixture, id: 163 };
    mockFetch({
      "/characters": { body: [characterFixture] },
      "/models/openrouter": { body: modelListFixture },
      "/chats": { body: [newChat] },
    });

    // …but the cache still holds the STALE list (without 163) and is marked
    // invalidated, so the first render serves stale data with isFetching=true
    // - exactly the create→invalidate→refetch window.
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    qc.setQueryData(keys.chats(), [chatFixture]); // stale: only id 1
    await qc.invalidateQueries({ queryKey: keys.chats() });

    useUiStore.setState({
      selectedChatId: 163, // just selected after create
      selectedCharacterId: characterFixture.id,
      selectedModelId: "openai/gpt-4o",
    });

    renderHook(() => useStaleSelectionReconciliation(), {
      wrapper: createWrapper(qc),
    });

    // Let the refetch settle and effects run.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 40));
    });

    // The selection must survive: reconciliation must not fire against the
    // stale in-flight list.
    expect(useUiStore.getState().selectedChatId).toBe(163);
  });

  it("does NOT clear selections while queries are still loading", async () => {
    // Fetch never resolves - all queries stay in loading state
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise<Response>(() => {})),
    );
    useUiStore.setState({
      selectedChatId: 999,
      selectedCharacterId: 999,
      selectedModelId: "vendor/removed-model",
    });

    renderHook(() => useStaleSelectionReconciliation(), {
      wrapper: createWrapper(),
    });

    await act(async () => {
      await new Promise((r) => setTimeout(r, 25));
    });

    expect(useUiStore.getState().selectedChatId).toBe(999);
    expect(useUiStore.getState().selectedCharacterId).toBe(999);
    expect(useUiStore.getState().selectedModelId).toBe("vendor/removed-model");
  });
});
