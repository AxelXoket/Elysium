import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { mockFetch } from "@/test/mocks/api";
import { modelListFixture, modelListFallbackFixture } from "@/test/mocks/fixtures";
import { ModelPanel } from "@/components/models/ModelPanel";
import { TooltipProvider } from "@/components/ui/tooltip";
import { GenerationSettingsProvider } from "@/components/generation/GenerationSettingsContext";

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 } },
  });
  return (
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <GenerationSettingsProvider>{children}</GenerationSettingsProvider>
      </TooltipProvider>
    </QueryClientProvider>
  );
}

describe("Model Panel Tests", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  // T-14: Models panel renders model list
  it("T-14: renders model list", async () => {
    mockFetch({
      "/models/openrouter": { body: modelListFixture },
    });

    render(<ModelPanel />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("GPT-4o")).toBeInTheDocument();
    });
  });

  // T-15: Models panel shows source badge
  it("T-15: shows source badge", async () => {
    mockFetch({
      "/models/openrouter": { body: modelListFixture },
    });

    render(<ModelPanel />, { wrapper });

    await waitFor(() => {
      expect(screen.getByTestId("model-source-badge")).toHaveTextContent("user");
    });
  });

  // T-16: Models panel shows fallback_reason
  it("T-16: shows fallback_reason", async () => {
    mockFetch({
      "/models/openrouter": { body: modelListFallbackFixture },
    });

    render(<ModelPanel />, { wrapper });

    await waitFor(() => {
      expect(screen.getByTestId("fallback-reason")).toHaveTextContent(
        "API key invalid or expired",
      );
    });
  });

  // T-17: Modality badges shown as informational, no upload UI
  it("T-17: modality badges informational, no upload UI", async () => {
    mockFetch({
      "/models/openrouter": { body: modelListFixture },
    });

    render(<ModelPanel />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("GPT-4o")).toBeInTheDocument();
    });

    // Image modality badge exists (informational)
    expect(screen.getByText("Image")).toBeInTheDocument();

    // No upload-related UI
    expect(screen.queryByText(/upload/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/upload/i)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /upload/i }),
    ).not.toBeInTheDocument();
  });

  // T-76: Model search filters the list
  it("T-76: model search filters the list", async () => {
    mockFetch({
      "/models/openrouter": { body: modelListFixture },
    });

    render(<ModelPanel />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("GPT-4o")).toBeInTheDocument();
    });

    const searchInput = screen.getByLabelText("Search models");
    await userEvent.type(searchInput, "GPT-4o");

    // GPT-4o still visible after filtering
    expect(screen.getByText("GPT-4o")).toBeInTheDocument();
  });

  // T-77: Model search empty state appears when no match
  it("T-77: model search empty state appears when no match", async () => {
    mockFetch({
      "/models/openrouter": { body: modelListFixture },
    });

    render(<ModelPanel />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("GPT-4o")).toBeInTheDocument();
    });

    const searchInput = screen.getByLabelText("Search models");
    await userEvent.type(searchInput, "zzz_no_match");

    await waitFor(() => {
      expect(screen.getByTestId("model-search-empty")).toBeInTheDocument();
    });
    expect(screen.queryByText("GPT-4o")).not.toBeInTheDocument();
  });

  // T-78: Model search clear button resets list
  it("T-78: clear button resets model search", async () => {
    mockFetch({
      "/models/openrouter": { body: modelListFixture },
    });

    render(<ModelPanel />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("GPT-4o")).toBeInTheDocument();
    });

    const searchInput = screen.getByLabelText("Search models");
    await userEvent.type(searchInput, "zzz_no_match");

    await waitFor(() => {
      expect(screen.getByTestId("model-search-empty")).toBeInTheDocument();
    });

    const clearBtn = screen.getByLabelText("Clear search");
    await userEvent.click(clearBtn);

    await waitFor(() => {
      expect(screen.getByText("GPT-4o")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("model-search-empty")).not.toBeInTheDocument();
  });
});
