import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ChatCanvas } from "@/components/chat/ChatCanvas";
import { useUiStore } from "@/lib/store/uiStore";
import { mockFetch } from "../mocks/api";
import { messageFixture } from "../mocks/fixtures";
import type { ReactNode } from "react";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("Messages", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({
      selectedChatId: null,
      selectedCharacterId: null,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // T-27: Empty state when no chat selected
  it("T-27: shows empty state when no chat selected", () => {
    render(<ChatCanvas />, { wrapper });
    expect(screen.getByText("Welcome to Elysium")).toBeInTheDocument();
  });

  // T-28: Messages rendered in read-only mode
  it("T-28: renders messages read-only for selected chat", async () => {
    mockFetch({
      "/chats/1/messages": { body: [messageFixture] },
    });
    useUiStore.setState({ selectedChatId: 1 });

    render(<ChatCanvas />, { wrapper });

    expect(
      await screen.findByText("Hello! I'm a test character."),
    ).toBeInTheDocument();
  });

  // T-29: Composer send is disabled
  it("T-29: composer send button is disabled", () => {
    render(<ChatCanvas />, { wrapper });
    const sendBtn = screen.getByRole("button", { name: /send message/i });
    expect(sendBtn).toBeDisabled();
  });

  // T-30: Composer input is disabled when no chat selected
  it("T-30: composer input is disabled", () => {
    render(<ChatCanvas />, { wrapper });
    const input = screen.getByLabelText("Message");
    expect(input).toBeDisabled();
  });
});
