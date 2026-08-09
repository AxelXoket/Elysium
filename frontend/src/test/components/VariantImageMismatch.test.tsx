/**
 * A variant's picture belongs to that variant's words.
 *
 * MessageBubble reads text, timestamp and the Speak target from `shownMessage`
 * (the row the reader paged to) but read attachments from `message` (the group's
 * ACTIVE row). Paging a variant group therefore showed one take's picture above
 * another take's words, and the lightbox opened the wrong image.
 *
 * Latent for as long as only user rows had attachments - user rows never form
 * variant groups - and reachable the moment a reply can carry a generated
 * picture. Found by audit, not by use.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { ChatCanvas } from "@/components/chat/ChatCanvas";
import { GenerationSettingsProvider } from "@/components/generation/GenerationSettingsContext";
import { useUiStore } from "@/lib/store/uiStore";
import { jsonResponse, mockFetchWithStreams } from "../helpers/streamMocks";
import { settingsFixture } from "../mocks/fixtures";
import type { Message } from "@/lib/schemas/chats";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={qc}>
      <GenerationSettingsProvider>{children}</GenerationSettingsProvider>
    </QueryClientProvider>
  );
}

const PICTURE = [{ id: 77, mime: "image/png", width: 256, height: 256 }];

/**
 * A view-only variant group: two assistant takes on an OLDER turn, so the
 * arrows page a local index and never call /activate. Take A owns a picture and
 * take B does not.
 */
function transcript(): Message[] {
  const base = {
    chat_id: 1,
    created_at: "2026-01-01T00:00:00Z",
    variant_group: 2,
    variant_count: 2,
  };
  return [
    {
      id: 1, role: "user", content: "draw me something",
      created_at: base.created_at, chat_id: 1, attachments: [],
      variant_group: null, active: true, variant_index: 0, variant_count: 1,
    } as Message,
    {
      ...base, id: 2, role: "assistant", content: "TAKE-A-WITH-PICTURE",
      attachments: PICTURE, active: true, variant_index: 0,
    } as Message,
    {
      ...base, id: 3, role: "assistant", content: "TAKE-B-NO-PICTURE",
      attachments: [], active: false, variant_index: 1,
    } as Message,
    {
      id: 4, role: "user", content: "a later turn, so the group is not last",
      created_at: base.created_at, chat_id: 1, attachments: [],
      variant_group: null, active: true, variant_index: 0, variant_count: 1,
    } as Message,
  ];
}

describe("paging a variant group that owns a picture", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({
      selectedChatId: 1,
      selectedCharacterId: 1,
      selectedModelId: "openai/gpt-4o",
    });
    mockFetchWithStreams({
      "/settings": { body: settingsFixture },
      "/chats/1/messages": { response: () => jsonResponse(transcript()) },
      "/chats": { response: () => jsonResponse([]) },
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the picture with the take that owns it", async () => {
    render(<ChatCanvas />, { wrapper });
    await screen.findByText("TAKE-A-WITH-PICTURE");
    expect(
      screen.getByRole("button", { name: /view attached image/i }),
    ).toBeInTheDocument();
  });

  it("takes the picture away when the reader pages to a take without one", async () => {
    render(<ChatCanvas />, { wrapper });
    await screen.findByText("TAKE-A-WITH-PICTURE");

    const next = screen.getAllByRole("button", { name: /next reply/i })[0];
    fireEvent.click(next);

    await screen.findByText("TAKE-B-NO-PICTURE");
    expect(screen.queryByText("TAKE-A-WITH-PICTURE")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /view attached image/i }),
    ).not.toBeInTheDocument();
  });

  it("brings it back on the way home", async () => {
    render(<ChatCanvas />, { wrapper });
    await screen.findByText("TAKE-A-WITH-PICTURE");
    fireEvent.click(screen.getAllByRole("button", { name: /next reply/i })[0]);
    await screen.findByText("TAKE-B-NO-PICTURE");

    fireEvent.click(
      screen.getAllByRole("button", { name: /previous reply/i })[0],
    );
    await screen.findByText("TAKE-A-WITH-PICTURE");
    expect(
      screen.getByRole("button", { name: /view attached image/i }),
    ).toBeInTheDocument();
  });
});
