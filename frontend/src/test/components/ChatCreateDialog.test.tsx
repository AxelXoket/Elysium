import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ChatCreateDialog } from "@/components/chats/ChatCreateDialog";
import { Button } from "@/components/ui/button";
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

describe("ChatCreateDialog", () => {
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

  // T-31: Chat create calls POST /chats
  it("T-31: calls POST /chats with correct body", async () => {
    const user = userEvent.setup();
    const mock = mockFetch({
      "/chats": { body: chatFixture },
    });

    render(
      <ChatCreateDialog
        trigger={<Button>Open</Button>}
      />,
      { wrapper },
    );

    // Open dialog
    await user.click(screen.getByText("Open"));
    await waitFor(() =>
      expect(screen.getByText("New Chat")).toBeInTheDocument(),
    );

    // Click Create without filling title (title is optional)
    await user.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      const postCalls = mock.mock.calls.filter(
        (call: unknown[]) =>
          typeof call[0] === "string" &&
          call[0].includes("/chats") &&
          (call[1] as RequestInit)?.method === "POST",
      );
      expect(postCalls.length).toBeGreaterThanOrEqual(1);
      const body = JSON.parse((postCalls[0][1] as RequestInit).body as string);
      expect(body.character_id).toBe(1);
    });
  });

  // T-32: Chat create does NOT call /complete
  it("T-32: chat create does not call /complete", async () => {
    const user = userEvent.setup();
    const mock = mockFetch({
      "/chats": { body: chatFixture },
    });

    render(
      <ChatCreateDialog
        trigger={<Button>Open</Button>}
      />,
      { wrapper },
    );

    await user.click(screen.getByText("Open"));
    await waitFor(() =>
      expect(screen.getByText("New Chat")).toBeInTheDocument(),
    );

    await user.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      const completeCalls = mock.mock.calls.filter(
        (call: unknown[]) =>
          typeof call[0] === "string" && call[0].includes("/complete"),
      );
      expect(completeCalls).toHaveLength(0);
    });
  });

  // T-33: No character selected shows placeholder message
  it("T-33: shows placeholder when no character selected", async () => {
    const user = userEvent.setup();
    useUiStore.setState({ selectedCharacterId: null });

    render(
      <ChatCreateDialog
        trigger={<Button>Open</Button>}
      />,
      { wrapper },
    );

    await user.click(screen.getByText("Open"));
    await waitFor(() =>
      expect(
        screen.getByText("Select a character first to start a chat."),
      ).toBeInTheDocument(),
    );
  });
});
