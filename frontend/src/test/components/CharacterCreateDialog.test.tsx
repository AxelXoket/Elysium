import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { mockFetch } from "@/test/mocks/api";
import { characterFixture } from "@/test/mocks/fixtures";
import { CharacterCreateDialog } from "@/components/characters/CharacterCreateDialog";
import { Button } from "@/components/ui/button";
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

describe("Character Create Dialog Tests", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  // T-18: Character create dialog calls POST /characters with correct body
  it("T-18: calls POST /characters", async () => {
    const fetchMock = mockFetch({
      "/characters": { body: characterFixture },
    });

    const user = userEvent.setup();
    render(
      <CharacterCreateDialog trigger={<Button>Create</Button>} />,
      { wrapper },
    );

    // Open dialog
    await user.click(screen.getByRole("button", { name: /create/i }));

    await waitFor(() => {
      expect(screen.getByText("Create Character")).toBeInTheDocument();
    });

    // Fill name
    const nameInput = screen.getByPlaceholderText("Character name");
    await user.type(nameInput, "My Test Char");

    // Submit
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify(characterFixture), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const createBtn = screen.getAllByRole("button", { name: /create/i });
    const submitBtn = createBtn[createBtn.length - 1]; // Last "Create" button is inside dialog
    await user.click(submitBtn);

    await waitFor(() => {
      const postCalls = fetchMock.mock.calls.filter(
        (call) =>
          typeof call[0] === "string" &&
          call[0].includes("/characters") &&
          !call[0].includes("/import") &&
          call[1]?.method === "POST",
      );
      expect(postCalls.length).toBeGreaterThanOrEqual(1);

      if (postCalls.length > 0) {
        const body = JSON.parse(postCalls[0][1]?.body as string);
        expect(body.name).toBe("My Test Char");
      }
    });
  });

  // T-20: raw_json absent from character render
  it("T-20: raw_json absent from character render", async () => {
    mockFetch({
      "/characters": { body: [characterFixture] },
    });

    const user = userEvent.setup();
    render(
      <CharacterCreateDialog trigger={<Button>Create</Button>} />,
      { wrapper },
    );

    await user.click(screen.getByRole("button", { name: /create/i }));

    await waitFor(() => {
      expect(screen.getByText("Create Character")).toBeInTheDocument();
    });

    // raw_json should never appear
    expect(screen.queryByText("raw_json")).not.toBeInTheDocument();
  });

  // FIX-3: create failure renders a safe mapped message, never raw detail
  it("FIX-3: create error shows mapped message instead of raw detail", async () => {
    const fetchMock = mockFetch({});

    const user = userEvent.setup();
    render(
      <CharacterCreateDialog trigger={<Button>Create</Button>} />,
      { wrapper },
    );

    await user.click(screen.getByRole("button", { name: /create/i }));
    await waitFor(() => {
      expect(screen.getByText("Create Character")).toBeInTheDocument();
    });

    await user.type(
      screen.getByPlaceholderText("Character name"),
      "Broken Char",
    );

    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "RAW_UPSTREAM_DETAIL" }), {
        status: 500,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const createBtns = screen.getAllByRole("button", { name: /create/i });
    await user.click(createBtns[createBtns.length - 1]);

    expect(
      await screen.findByText("Something went wrong. Please try again."),
    ).toBeInTheDocument();
    expect(screen.queryByText("RAW_UPSTREAM_DETAIL")).not.toBeInTheDocument();
  });
});
