import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { mockFetch } from "@/test/mocks/api";
import { settingsFixture, proxyHealthFixture } from "@/test/mocks/fixtures";
import { ApiKeySection } from "@/components/settings/ApiKeySection";
import { TooltipProvider } from "@/components/ui/tooltip";

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 } },
  });
  return (
    <QueryClientProvider client={qc}>
      <TooltipProvider>{children}</TooltipProvider>
    </QueryClientProvider>
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
  it("T-06: shows api_key_set status", async () => {
    render(<ApiKeySection />, { wrapper });
    await waitFor(() => {
      expect(screen.getByText("API key is set")).toBeInTheDocument();
    });
  });

  // T-07: API key save calls POST /settings/api-key
  it("T-07: API key save calls POST /settings/api-key", async () => {
    const user = userEvent.setup();
    render(<ApiKeySection />, { wrapper });

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
  it("T-08: input clears after successful API key save", async () => {
    const user = userEvent.setup();
    render(<ApiKeySection />, { wrapper });

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
  it("T-09: API key value not rendered after save", async () => {
    const user = userEvent.setup();
    render(<ApiKeySection />, { wrapper });

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
  it("FIX-2: validation_unavailable says key not saved and keeps input", async () => {
    const user = userEvent.setup();
    render(<ApiKeySection />, { wrapper });

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
        "Couldn't reach OpenRouter to validate the key, so it was not saved. Check your connection or proxy and try again.",
      ),
    ).toBeInTheDocument();
    // Input NOT cleared - user can retry without retyping
    expect(input.value).toBe("sk-test-key-123");
  });

  // FIX-2: settings/models are invalidated (refetched) even when ok=false
  it("FIX-2: invalidates settings even on validation_unavailable", async () => {
    const user = userEvent.setup();
    render(<ApiKeySection />, { wrapper });

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
});
