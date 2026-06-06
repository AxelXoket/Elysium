import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ChatList } from "@/components/sidebar/ChatList";
import { useUiStore } from "@/lib/store/uiStore";
import { mockFetch } from "../mocks/api";
import { chatFixture } from "../mocks/fixtures";
import type { ReactNode } from "react";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("ChatList", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({
      selectedCharacterId: 1,
      selectedChatId: null,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // T-24: Chat list renders for selected character
  it("T-24: renders chats for selected character", async () => {
    mockFetch({
      "/chats": { body: [chatFixture] },
    });

    render(<ChatList />, { wrapper });

    expect(await screen.findByText("Chats")).toBeInTheDocument();
    expect(await screen.findByText("Test Chat")).toBeInTheDocument();
    expect(await screen.findByText(/1 msg/)).toBeInTheDocument();
  });

  // T-25: No character selected shows placeholder
  it("T-25: shows placeholder when no character selected", async () => {
    useUiStore.setState({ selectedCharacterId: null });

    render(<ChatList />, { wrapper });

    expect(screen.getByText("Select a character first")).toBeInTheDocument();
  });

  // T-26: Empty chat list for selected character shows empty state
  it("T-26: shows empty state when no chats exist", async () => {
    mockFetch({
      "/chats": { body: [] },
    });

    render(<ChatList />, { wrapper });

    expect(await screen.findByText("No chats yet")).toBeInTheDocument();
  });
});
