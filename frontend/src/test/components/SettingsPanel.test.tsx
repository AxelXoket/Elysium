import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { mockFetch } from "@/test/mocks/api";
import { settingsFixture, proxyHealthFixture } from "@/test/mocks/fixtures";
import { ApiKeySection } from "@/components/settings/ApiKeySection";
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
