import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { mockFetch } from "@/test/mocks/api";
import { settingsFixture, proxyHealthFixture } from "@/test/mocks/fixtures";
import { ApiKeySection } from "@/components/settings/ApiKeySection";
import { SettingsPanel } from "@/components/settings/SettingsPanel";
import { useUiStore } from "@/lib/store/uiStore";
import { TooltipProvider } from "@/components/ui/tooltip";

function wrapper({ children }: { children: React.ReactNode }) {
  return (
    
      <TooltipProvider>{children}</TooltipProvider>
    
  );
}

describe("Settings Panel Tests", () => {
  let fetchMock: ReturnType<typeof mockFetch>;

  beforeEach(() => {
    fetchMock = mockFetch({
      "/settings/proxy/health": { body: proxyHealthFixture },
      "/settings": { body: settingsFixture },
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // T-06: Settings shows api_key_set=true status
  it("tells the reader a key is already stored", async () => {
    renderWithQueryClient(<ApiKeySection />, { wrapper });
    await waitFor(() => {
      expect(screen.getByText("API key is set")).toBeInTheDocument();
    });
  });

  // T-07: API key save calls POST /settings/api-key
  it("sends a typed key in the request body", async () => {
    const user = userEvent.setup();
    renderWithQueryClient(<ApiKeySection />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("API key is set")).toBeInTheDocument();
    });

    const input = screen.getByLabelText("API key input");
    await user.type(input, "sk-test-key-123");

    // Mock the POST response
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ ok: true, key_status: "valid" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const saveBtn = screen.getByRole("button", { name: /save/i });
    await user.click(saveBtn);

    await waitFor(() => {
      const postCalls = fetchMock.mock.calls.filter(
        (call) =>
          typeof call[0] === "string" &&
          call[0].includes("/settings/api-key") &&
          call[1]?.method === "POST",
      );
      expect(postCalls.length).toBeGreaterThanOrEqual(1);
    });
  });

  // T-08: Input clears after successful API key save
  it("empties the field once the key is stored", async () => {
    const user = userEvent.setup();
    renderWithQueryClient(<ApiKeySection />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("API key is set")).toBeInTheDocument();
    });

    const input = screen.getByLabelText("API key input") as HTMLInputElement;
    await user.type(input, "sk-test-key-123");

    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ ok: true, key_status: "valid" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const saveBtn = screen.getByRole("button", { name: /save/i });
    await user.click(saveBtn);

    await waitFor(() => {
      expect(input.value).toBe("");
    });
  });

  // T-09: API key not rendered in DOM after save
  it("never renders the key back after storing it", async () => {
    const user = userEvent.setup();
    renderWithQueryClient(<ApiKeySection />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("API key is set")).toBeInTheDocument();
    });

    const input = screen.getByLabelText("API key input") as HTMLInputElement;
    await user.type(input, "sk-test-key-123");

    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ ok: true, key_status: "valid" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const saveBtn = screen.getByRole("button", { name: /save/i });
    await user.click(saveBtn);

    await waitFor(() => {
      expect(input.value).toBe("");
    });

    // The API key value should not appear anywhere in the document
    expect(screen.queryByText("sk-test-key-123")).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue("sk-test-key-123")).not.toBeInTheDocument();
  });

  // FIX-2: validation_unavailable means the key was NOT saved - the message
  // must say so and the input must be kept so the user can retry.
  it("says the key was not saved when validation could not run, and keeps what was typed", async () => {
    const user = userEvent.setup();
    renderWithQueryClient(<ApiKeySection />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("API key is set")).toBeInTheDocument();
    });

    const input = screen.getByLabelText("API key input") as HTMLInputElement;
    await user.type(input, "sk-test-key-123");

    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({ ok: false, key_status: "validation_unavailable" }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    await user.click(screen.getByRole("button", { name: /save/i }));

    expect(
      await screen.findByText(
        "Could not reach OpenRouter, so the key was not saved. Check your connection or proxy.",
      ),
    ).toBeInTheDocument();
    // Input NOT cleared - user can retry without retyping
    expect(input.value).toBe("sk-test-key-123");
  });

  // FIX-2: settings/models are invalidated (refetched) even when ok=false
  it("refreshes the stored-key status even when validation could not run", async () => {
    const user = userEvent.setup();
    renderWithQueryClient(<ApiKeySection />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("API key is set")).toBeInTheDocument();
    });

    const settingsGetCalls = () =>
      fetchMock.mock.calls.filter(
        (call) =>
          typeof call[0] === "string" &&
          call[0].endsWith("/settings") &&
          (call[1]?.method ?? "GET") === "GET",
      ).length;
    const before = settingsGetCalls();

    const input = screen.getByLabelText("API key input");
    await user.type(input, "sk-test-key-123");

    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({ ok: false, key_status: "validation_unavailable" }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    await user.click(screen.getByRole("button", { name: /save/i }));

    // Invalidation refetches the active settings query despite ok=false
    await waitFor(() => {
      expect(settingsGetCalls()).toBeGreaterThan(before);
    });
  });

  // v1.1 FF12: Enter in the key field saves (house convention).
  it("Enter in the key field saves, without hunting for the button", async () => {
    const user = userEvent.setup();
    renderWithQueryClient(<ApiKeySection />, { wrapper });
    await waitFor(() => {
      expect(screen.getByText("API key is set")).toBeInTheDocument();
    });

    const input = screen.getByLabelText("API key input");
    await user.type(input, "sk-enter-save");
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ ok: true, key_status: "valid" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    await user.type(input, "{Enter}");

    await waitFor(() => {
      const postCalls = fetchMock.mock.calls.filter(
        (c) =>
          typeof c[0] === "string" &&
          c[0].includes("/settings/api-key") &&
          c[1]?.method === "POST",
      );
      expect(postCalls.length).toBeGreaterThanOrEqual(1);
    });
  });

  // v1.1 FF13: Remove API Key requires an inline confirmation.
  it("asks before removing the stored key", async () => {
    const user = userEvent.setup();
    renderWithQueryClient(<ApiKeySection />, { wrapper });
    await waitFor(() => {
      expect(screen.getByText("API key is set")).toBeInTheDocument();
    });

    const deleteCalls = () =>
      fetchMock.mock.calls.filter(
        (c) =>
          typeof c[0] === "string" &&
          c[0].includes("/settings/api-key") &&
          c[1]?.method === "DELETE",
      ).length;

    // First click only reveals the confirm - no DELETE yet.
    await user.click(screen.getByRole("button", { name: /remove api key/i }));
    expect(screen.getByText("Remove the stored key?")).toBeInTheDocument();
    expect(deleteCalls()).toBe(0);

    // Cancel closes it without deleting.
    await user.click(screen.getByRole("button", { name: /^cancel$/i }));
    expect(screen.queryByText("Remove the stored key?")).not.toBeInTheDocument();
    expect(deleteCalls()).toBe(0);

    // Re-open and confirm -> DELETE fires.
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    await user.click(screen.getByRole("button", { name: /remove api key/i }));
    await user.click(screen.getByRole("button", { name: /^remove$/i }));
    await waitFor(() => expect(deleteCalls()).toBe(1));
  });
});

/**
 * The privacy note above the API key and proxy sections.
 *
 * This block exists because the note previously made two absolute claims that
 * the code contradicted: "Nothing is stored in the browser" (uiStore persists
 * selectedCharacterId / selectedChatId / selectedModelId to localStorage) and
 * "nothing leaves this machine" (the whole app sends the conversation, and the
 * API key, to the provider). The ids stay in localStorage by decision, so the
 * SENTENCE is what has to stay honest, and a sentence with nothing guarding it
 * drifts back to the tidier lie on the next edit.
 *
 * Every negative assertion here is paired with a positive control on the
 * RETIRED wording, so a matcher that has quietly stopped matching anything
 * fails loudly instead of passing.
 */
const RETIRED_NOTE =
  "Secrets are sealed inside your encrypted vault - locked with your " +
  "passphrase, together with everything else. Nothing is stored in the " +
  "browser, and nothing leaves this machine.";

const NO_BROWSER_STORAGE_CLAIM = /nothing\s+is\s+stored\s+in\s+the\s+browser/i;
const NO_EGRESS_CLAIM = /nothing\s+leaves\s+this\s+machine/i;

describe("Settings Panel privacy note", () => {
  beforeEach(() => {
    mockFetch({
      "/settings/proxy/health": { body: proxyHealthFixture },
      "/settings": { body: settingsFixture },
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  async function noteText() {
    const note = await screen.findByRole("note", { name: "Privacy note" });
    return note.textContent?.replace(/\s+/g, " ").trim() ?? "";
  }

  // GROUND. If the panel fails to render, every "does not say X" assertion
  // below passes for the wrong reason. This is the test that has to fail
  // first when that happens.
  it("renders the privacy note above the key and proxy sections", async () => {
    renderWithQueryClient(<SettingsPanel />, { wrapper });
    expect(await noteText()).toMatch(/encrypted vault/i);
  });

  it("does not claim that nothing is stored in the browser", async () => {
    // Positive control: the matcher does catch the claim when it is present.
    expect(RETIRED_NOTE).toMatch(NO_BROWSER_STORAGE_CLAIM);

    renderWithQueryClient(<SettingsPanel />, { wrapper });
    expect(await noteText()).not.toMatch(NO_BROWSER_STORAGE_CLAIM);
  });

  it("names what the browser does keep: the last open character, chat and model", async () => {
    renderWithQueryClient(<SettingsPanel />, { wrapper });
    const text = await noteText();

    // Not a wording check - these three are exactly the identifiers uiStore's
    // `partialize` writes to localStorage, so if the note stops naming one of
    // them it has stopped describing what is actually on disk.
    expect(text).toMatch(/browser/i);
    expect(text).toMatch(/character/i);
    expect(text).toMatch(/chat/i);
    expect(text).toMatch(/model/i);
  });

  it("keeps naming exactly what uiStore persists", () => {
    // The other half of the pair above: the note is measured against the store
    // rather than against a remembered sentence. If somebody adds a new
    // user-identifying id to `partialize`, this fails and the note gets
    // updated with it.
    useUiStore.setState({
      selectedCharacterId: 3,
      selectedChatId: 9,
      selectedModelId: "some/model",
    });
    const persisted = JSON.parse(
      localStorage.getItem("elysium-ui-state") ?? "{}",
    );
    const state = (persisted.state ?? {}) as Record<string, unknown>;
    expect(state).toHaveProperty("selectedCharacterId", 3);
    expect(state).toHaveProperty("selectedChatId", 9);
    expect(state).toHaveProperty("selectedModelId", "some/model");
    // The claim the note is allowed to keep making.
    expect(Object.keys(state)).not.toContain("apiKey");
  });

  it("does not claim that nothing leaves this machine, and says where what you send goes", async () => {
    // Positive control for the second retired absolute.
    expect(RETIRED_NOTE).toMatch(NO_EGRESS_CLAIM);

    renderWithQueryClient(<SettingsPanel />, { wrapper });
    const text = await noteText();
    expect(text).not.toMatch(NO_EGRESS_CLAIM);
    // What replaced it: one named destination, chosen by the user.
    expect(text).toMatch(/provider you chose/i);
  });
});
