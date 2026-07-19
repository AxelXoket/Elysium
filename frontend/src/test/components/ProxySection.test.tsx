import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { mockFetch } from "@/test/mocks/api";
import {
  settingsFixture,
  proxyHealthFixture,
} from "@/test/mocks/fixtures";
import { ProxySection } from "@/components/settings/ProxySection";
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

describe("Proxy Section Tests", () => {
  let fetchMock: ReturnType<typeof mockFetch>;

  beforeEach(() => {
    fetchMock = mockFetch({
      "/settings/proxy/health": { body: proxyHealthFixture },
      "/settings": { body: { ...settingsFixture, proxy_configured: true } },
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // T-10: Proxy save calls POST /settings/proxy with exact backend field names
  it("T-10: proxy save calls POST /settings/proxy", async () => {
    const user = userEvent.setup();
    render(<ProxySection />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("Proxy configured")).toBeInTheDocument();
    });

    const urlInput = screen.getByLabelText("Proxy URL input");
    await user.type(urlInput, "https://proxy.test.com");

    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const saveBtn = screen.getByRole("button", { name: /save proxy/i });
    await user.click(saveBtn);

    await waitFor(() => {
      const postCalls = fetchMock.mock.calls.filter(
        (call) =>
          typeof call[0] === "string" &&
          call[0].includes("/settings/proxy") &&
          !call[0].includes("/health") &&
          call[1]?.method === "POST",
      );
      expect(postCalls.length).toBeGreaterThanOrEqual(1);

      // Verify body field names match backend exactly
      if (postCalls.length > 0) {
        const body = JSON.parse(postCalls[0][1]?.body as string);
        expect(body).toHaveProperty("proxy_url");
        expect(body).toHaveProperty("proxy_required");
        expect(body).toHaveProperty("proxy_alias");
      }
    });
  });

  // T-11: Proxy delete calls DELETE
  it("T-11: proxy delete calls DELETE /settings/proxy", async () => {
    const user = userEvent.setup();
    render(<ProxySection />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("Proxy configured")).toBeInTheDocument();
    });

    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const deleteBtn = screen.getByRole("button", { name: /remove proxy/i });
    await user.click(deleteBtn);

    await waitFor(() => {
      const deleteCalls = fetchMock.mock.calls.filter(
        (call) =>
          typeof call[0] === "string" &&
          call[0].includes("/settings/proxy") &&
          !call[0].includes("/health") &&
          call[1]?.method === "DELETE",
      );
      expect(deleteCalls.length).toBeGreaterThanOrEqual(1);
    });
  });

  // T-12: Proxy URL not displayed after save
  it("T-12: proxy URL not displayed after save", async () => {
    const user = userEvent.setup();
    render(<ProxySection />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("Proxy configured")).toBeInTheDocument();
    });

    const urlInput = screen.getByLabelText("Proxy URL input") as HTMLInputElement;
    await user.type(urlInput, "https://proxy.test.com");

    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const saveBtn = screen.getByRole("button", { name: /save proxy/i });
    await user.click(saveBtn);

    await waitFor(() => {
      expect(urlInput.value).toBe("");
    });
    expect(
      screen.queryByText("https://proxy.test.com"),
    ).not.toBeInTheDocument();
  });

  // T-13: Proxy health status renders
  it("T-13: proxy health status renders", async () => {
    render(<ProxySection />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText(/Healthy/)).toBeInTheDocument();
      expect(screen.getByText(/42ms/)).toBeInTheDocument();
    });
  });

  // FIX-1: "Require proxy" toggle reflects server state on mount
  it("FIX-1: toggle renders on when settings.proxy_required=true", async () => {
    mockFetch({
      "/settings/proxy/health": { body: proxyHealthFixture },
      "/settings": {
        body: {
          ...settingsFixture,
          proxy_configured: true,
          proxy_required: true,
        },
      },
    });

    render(<ProxySection />, { wrapper });

    const toggle = screen.getByLabelText("Proxy required toggle");
    await waitFor(() => {
      expect(toggle).toBeChecked();
    });
  });

  // FIX-1: saving with only the URL changed must NOT silently disable
  // proxy_required - the untouched toggle mirrors the server value (true).
  it("FIX-1: URL-only save keeps proxyRequired true from server state", async () => {
    const user = userEvent.setup();
    const mock = mockFetch({
      "/settings/proxy/health": { body: proxyHealthFixture },
      "/settings": {
        body: {
          ...settingsFixture,
          proxy_configured: true,
          proxy_required: true,
        },
      },
    });

    render(<ProxySection />, { wrapper });

    await waitFor(() => {
      expect(screen.getByLabelText("Proxy required toggle")).toBeChecked();
    });

    // Change ONLY the URL; do not touch the toggle
    await user.type(
      screen.getByLabelText("Proxy URL input"),
      "https://proxy.test.com",
    );

    mock.mockResolvedValueOnce(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await user.click(screen.getByRole("button", { name: /save proxy/i }));

    await waitFor(() => {
      const postCalls = mock.mock.calls.filter(
        (call) =>
          typeof call[0] === "string" &&
          call[0].includes("/settings/proxy") &&
          !call[0].includes("/health") &&
          call[1]?.method === "POST",
      );
      expect(postCalls.length).toBeGreaterThanOrEqual(1);
      const body = JSON.parse(postCalls[0][1]?.body as string);
      expect(body.proxy_required).toBe(true);
    });
  });

  // FIX-1: a user-toggled value is preserved (dirty flag blocks server re-sync)
  it("FIX-1: user-toggled value is sent even before settings refetch", async () => {
    const user = userEvent.setup();
    // Server says proxy_required=false; user switches it on before saving.
    const mock = fetchMock;

    render(<ProxySection />, { wrapper });

    const toggle = screen.getByLabelText("Proxy required toggle");
    await waitFor(() => {
      expect(toggle).not.toBeChecked();
    });

    await user.click(toggle);
    expect(toggle).toBeChecked();

    await user.type(
      screen.getByLabelText("Proxy URL input"),
      "https://proxy.test.com",
    );

    mock.mockResolvedValueOnce(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await user.click(screen.getByRole("button", { name: /save proxy/i }));

    await waitFor(() => {
      const postCalls = mock.mock.calls.filter(
        (call) =>
          typeof call[0] === "string" &&
          call[0].includes("/settings/proxy") &&
          !call[0].includes("/health") &&
          call[1]?.method === "POST",
      );
      expect(postCalls.length).toBeGreaterThanOrEqual(1);
      const body = JSON.parse(postCalls[0][1]?.body as string);
      expect(body.proxy_required).toBe(true);
    });
  });

  // FIX-3: proxy save failure shows a safe mapped message, never raw detail
  it("FIX-3: save error shows mapped message instead of raw detail", async () => {
    const user = userEvent.setup();

    render(<ProxySection />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("Proxy configured")).toBeInTheDocument();
    });

    await user.type(
      screen.getByLabelText("Proxy URL input"),
      "https://proxy.test.com",
    );

    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "RAW_UPSTREAM_DETAIL" }), {
        status: 500,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await user.click(screen.getByRole("button", { name: /save proxy/i }));

    expect(
      await screen.findByText("Something went wrong. Please try again."),
    ).toBeInTheDocument();
    expect(screen.queryByText("RAW_UPSTREAM_DETAIL")).not.toBeInTheDocument();
  });
});
