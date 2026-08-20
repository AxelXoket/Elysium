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
import { waitFor, act } from "@testing-library/react";
import { useStaleSelectionReconciliation } from "@/app/useStaleSelectionReconciliation";
import { useUiStore } from "@/lib/store/uiStore";
import { mockFetch } from "../mocks/api";
import { keys } from "@/lib/query/keys";
import {
  chatFixture,
  characterFixture,
  modelListFixture,
  settingsFixture,
} from "../mocks/fixtures";
import {
  createTestQueryClient,
  renderHookWithQueryClient,
} from "@/test/helpers/renderWithQueryClient";

/** Valid server data: chat 1, character 1, model "openai/gpt-4o". */
function mockAllListsValid() {
  return mockFetch({
    "/characters": { body: [characterFixture] },
    "/models/openrouter": { body: modelListFixture },
    "/chats": { body: [chatFixture] },
  });
}

/**
 * The same three lists, plus GET /settings (for the model's vault hydration)
 * and POST /settings/model-selection (for the push side). `selectedModelId`
 * is the ONE selection that no longer lives in localStorage (v1.2 privacy
 * fix) - see uiStore.ts's version-3 migrate - so its round trip through the
 * vault is the thing these tests exist to prove.
 */
function mockAllListsAndSettings(
  overrides: Partial<typeof settingsFixture> = {},
) {
  return mockFetch({
    "/characters": { body: [characterFixture] },
    "/models/openrouter": { body: modelListFixture },
    "/chats": { body: [chatFixture] },
    "GET /settings": { body: { ...settingsFixture, ...overrides } },
    "POST /settings/model-selection": { body: { ok: true, selected_model_id: null } },
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

    renderHookWithQueryClient(() => useStaleSelectionReconciliation());

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

    renderHookWithQueryClient(() => useStaleSelectionReconciliation());

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

    renderHookWithQueryClient(() => useStaleSelectionReconciliation());

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

    renderHookWithQueryClient(() => useStaleSelectionReconciliation());

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

    renderHookWithQueryClient(() => useStaleSelectionReconciliation());

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
    const qc = createTestQueryClient();
    qc.setQueryData(keys.chats(), [chatFixture]); // stale: only id 1
    await qc.invalidateQueries({ queryKey: keys.chats() });

    useUiStore.setState({
      selectedChatId: 163, // just selected after create
      selectedCharacterId: characterFixture.id,
      selectedModelId: "openai/gpt-4o",
    });

    renderHookWithQueryClient(() => useStaleSelectionReconciliation(), {
      client: qc,
    });

    // Let the refetch settle and effects run.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 40));
    });

    // The selection must survive: reconciliation must not fire against the
    // stale in-flight list.
    expect(useUiStore.getState().selectedChatId).toBe(163);
  });

  it("does NOT clear a freshly-created character while the list is refetching", async () => {
    // The same race, one list over, and it costs more here: clearing the
    // character clears its chat selection too, so a create that lost this
    // window would drop the reader out of the chat they just started.
    //
    // The `isFetching` guard for characters has been in the hook as long as
    // the one for chats. Only the test was missing - KADEME 17a's adversary
    // called the guard itself absent, which measuring disproved; what was
    // absent was any way to notice it going away.
    const newCharacter = { ...characterFixture, id: 471 };
    mockFetch({
      "/characters": { body: [newCharacter] },
      "/models/openrouter": { body: modelListFixture },
      "/chats": { body: [chatFixture] },
    });

    const qc = createTestQueryClient();
    qc.setQueryData(keys.characters(), [characterFixture]); // stale: no 471
    await qc.invalidateQueries({ queryKey: keys.characters() });

    useUiStore.setState({
      selectedChatId: null,
      selectedCharacterId: 471, // just selected after create
      selectedModelId: "openai/gpt-4o",
    });

    renderHookWithQueryClient(() => useStaleSelectionReconciliation(), {
      client: qc,
    });

    await act(async () => {
      await new Promise((r) => setTimeout(r, 40));
    });

    expect(useUiStore.getState().selectedCharacterId).toBe(471);
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

    renderHookWithQueryClient(() => useStaleSelectionReconciliation());

    await act(async () => {
      await new Promise((r) => setTimeout(r, 25));
    });

    expect(useUiStore.getState().selectedChatId).toBe(999);
    expect(useUiStore.getState().selectedCharacterId).toBe(999);
    expect(useUiStore.getState().selectedModelId).toBe("vendor/removed-model");
  });
});

/**
 * The model selection's vault round trip (v1.2 privacy fix).
 *
 * selectedChatId/selectedCharacterId still persist in localStorage (bare row
 * ids, which the owner's rule permits). selectedModelId does not: it is a
 * model NAME, and now lives in the encrypted settings table instead - these
 * are the tests that prove the hydrate-once-then-push bridge in
 * useStaleSelectionReconciliation actually does that job.
 */
describe("useStaleSelectionReconciliation - the model's vault round trip", () => {
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

  it("hydrates the model selection from the vault when nothing is chosen locally", async () => {
    mockAllListsAndSettings({ selected_model_id: "openai/gpt-4o" });

    renderHookWithQueryClient(() => useStaleSelectionReconciliation());

    await waitFor(() => {
      expect(useUiStore.getState().selectedModelId).toBe("openai/gpt-4o");
    });
  });

  it("does NOT overwrite a selection already made in memory (GROUND: local wins)", async () => {
    // The device-vs-vault tie-break narrationMigration.ts also uses: what is
    // already showing is what the user believes they chose.
    mockAllListsAndSettings({ selected_model_id: "vendor/other-model" });
    useUiStore.setState({ selectedModelId: "openai/gpt-4o" });

    renderHookWithQueryClient(() => useStaleSelectionReconciliation());

    await act(async () => {
      await new Promise((r) => setTimeout(r, 25));
    });
    expect(useUiStore.getState().selectedModelId).toBe("openai/gpt-4o");
  });

  it("leaves the selection null when the vault has nothing stored either (GROUND)", async () => {
    mockAllListsAndSettings({ selected_model_id: null });

    renderHookWithQueryClient(() => useStaleSelectionReconciliation());

    await waitFor(() => {
      expect(useUiStore.getState().selectedModelId).toBeNull();
    });
  });

  it("does not hydrate at all while the settings read has not succeeded", async () => {
    // Same lists as normal, but no /settings mock at all - the fetch mock's
    // own default (404) means the query never reaches isSuccess. A stale
    // local selection must survive: hydration must never run on an error.
    mockFetch({
      "/characters": { body: [characterFixture] },
      "/models/openrouter": { body: modelListFixture },
      "/chats": { body: [chatFixture] },
    });
    useUiStore.setState({ selectedModelId: "openai/gpt-4o" });

    renderHookWithQueryClient(() => useStaleSelectionReconciliation());

    await act(async () => {
      await new Promise((r) => setTimeout(r, 25));
    });
    expect(useUiStore.getState().selectedModelId).toBe("openai/gpt-4o");
  });

  it("pushes a later change to the vault", async () => {
    const fetchMock = mockAllListsAndSettings({ selected_model_id: null });

    renderHookWithQueryClient(() => useStaleSelectionReconciliation());

    // Let hydration settle first (vault has nothing, local has nothing - no
    // push yet) before making the change this test is actually about.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 25));
    });

    act(() => {
      useUiStore.getState().selectModel("openai/gpt-4o");
    });

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([url, init]) =>
        String(url).includes("/settings/model-selection") &&
        (init as RequestInit)?.method === "POST",
      );
      expect(call, "no POST /settings/model-selection was ever sent").toBeDefined();
      expect(JSON.parse((call![1] as RequestInit).body as string)).toEqual({
        selected_model_id: "openai/gpt-4o",
      });
    });
  });

  it("hydrating from a non-null vault value does not itself trigger a push", async () => {
    // MUTATION-SENSITIVE: this is the test that would fail red if the guard
    // ordering in the hook regressed and the hydration write echoed straight
    // back out as a POST before the render carrying it had even landed.
    const fetchMock = mockAllListsAndSettings({ selected_model_id: "openai/gpt-4o" });

    renderHookWithQueryClient(() => useStaleSelectionReconciliation());

    await waitFor(() => {
      expect(useUiStore.getState().selectedModelId).toBe("openai/gpt-4o");
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 25));
    });

    const pushed = fetchMock.mock.calls.some(([url, init]) =>
      String(url).includes("/settings/model-selection") &&
      (init as RequestInit)?.method === "POST",
    );
    expect(pushed, "hydration echoed itself back as a spurious save").toBe(false);
  });

  it("pushes null when reconciliation clears a stale model (vault stays in sync)", async () => {
    const fetchMock = mockFetch({
      "/characters": { body: [characterFixture] },
      "/models/openrouter": { body: modelListFixture }, // does not contain the vendor id below
      "/chats": { body: [chatFixture] },
      "GET /settings": {
        body: { ...settingsFixture, selected_model_id: "vendor/removed-model" },
      },
      "POST /settings/model-selection": { body: { ok: true, selected_model_id: null } },
    });

    renderHookWithQueryClient(() => useStaleSelectionReconciliation());

    // Hydrates to the stale id, then the models-list reconciliation clears it.
    await waitFor(() => {
      expect(useUiStore.getState().selectedModelId).toBeNull();
    });

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([url, init]) =>
        String(url).includes("/settings/model-selection") &&
        (init as RequestInit)?.method === "POST",
      );
      expect(call, "the cleared model was never pushed to the vault").toBeDefined();
      expect(JSON.parse((call![1] as RequestInit).body as string)).toEqual({
        selected_model_id: null,
      });
    });
  });
});
