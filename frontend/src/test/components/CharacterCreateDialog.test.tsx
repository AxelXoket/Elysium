import { describe, it, expect, vi, afterEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { mockFetch } from "@/test/mocks/api";
import { characterFixture } from "@/test/mocks/fixtures";
import { CharacterCreateDialog } from "@/components/characters/CharacterCreateDialog";
import { useUiStore } from "@/lib/store/uiStore";
import { Button } from "@/components/ui/button";
import { TooltipProvider } from "@/components/ui/tooltip";

function wrapper({ children }: { children: React.ReactNode }) {
  return (
    
      <TooltipProvider>{children}</TooltipProvider>
    
  );
}

describe("Character Create Dialog Tests", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  // T-18: Character create dialog calls POST /characters with correct body
  it("creates the character with everything that was typed", async () => {
    const fetchMock = mockFetch({
      "/characters": { body: characterFixture },
    });

    const user = userEvent.setup();
    renderWithQueryClient(
      <CharacterCreateDialog trigger={<Button>Create</Button>} />,
      { wrapper },
    );

    // Open dialog
    await user.click(screen.getByRole("button", { name: /create/i }));

    await waitFor(() => {
      expect(screen.getByText("Create Character")).toBeInTheDocument();
    });

    // Every field, not just the name. Asserting only the name let a whole
    // field be dropped - or written over another one - without a single test
    // noticing: moving `personality` onto the `description` key kept all
    // 1315 tests in the frontend suite green.
    await user.type(screen.getByPlaceholderText("Character name"), "My Test Char");
    await user.type(screen.getByPlaceholderText("A brief description…"), "Brief");
    await user.type(
      screen.getByPlaceholderText("Character personality traits…"),
      "Wry",
    );
    await user.type(screen.getByPlaceholderText("Context / scenario…"), "A pier");
    await user.type(screen.getByPlaceholderText("Opening message…"), "Hello there");

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
      const body = JSON.parse(postCalls[0][1]?.body as string);
      expect(body.name).toBe("My Test Char");
      expect(body.description).toBe("Brief");
      expect(body.personality).toBe("Wry");
      expect(body.scenario).toBe("A pier");
      expect(body.first_mes).toBe("Hello there");
    });
  });

  // T-20: raw_json absent from character render
  it("never renders the imported raw card back to the screen", async () => {
    mockFetch({
      "/characters": { body: [characterFixture] },
    });

    const user = userEvent.setup();
    renderWithQueryClient(
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
  it("explains a refused create in its own words, not the upstream detail", async () => {
    const fetchMock = mockFetch({});

    const user = userEvent.setup();
    renderWithQueryClient(
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
  it("will not create a character with no name", async () => {
    // The only thing standing between an empty name and a POST is the
    // button's disabled state, and nothing was checking it: dropping
    // `!name.trim()` from the condition left every test in the repo green.
    // The handler's own `if (!jsonText.trim()) return`-style guard behind it
    // is unreachable through the UI, so this is the boundary that matters.
    mockFetch({ "/characters": { body: characterFixture } });
    const user = userEvent.setup();
    renderWithQueryClient(
      <CharacterCreateDialog trigger={<Button>Create</Button>} />,
      { wrapper },
    );

    await user.click(screen.getByRole("button", { name: /create/i }));
    await waitFor(() => {
      expect(screen.getByText("Create Character")).toBeInTheDocument();
    });

    const buttons = screen.getAllByRole("button", { name: /create/i });
    expect(
      buttons[buttons.length - 1],
      "an unnamed character can be created",
    ).toBeDisabled();

    // And it becomes usable the moment there is a name, so the assertion
    // above is about the empty case and not about the button never working.
    await user.type(screen.getByPlaceholderText("Character name"), "Named");
    expect(
      screen.getAllByRole("button", { name: /create/i }).slice(-1)[0],
    ).toBeEnabled();
  });

  it("selects the character it just created", async () => {
    // Creating something and landing on nothing is a dead end: the panel
    // closes and the reader is back where they started, wondering whether it
    // worked. Nothing asserted the selection, so `selectCharacter(created.id)`
    // could become `selectCharacter(null)` unnoticed.
    useUiStore.setState({ selectedCharacterId: null, selectedChatId: null });
    const fetchMock = mockFetch({ "/characters": { body: characterFixture } });
    const user = userEvent.setup();
    renderWithQueryClient(
      <CharacterCreateDialog trigger={<Button>Create</Button>} />,
      { wrapper },
    );

    await user.click(screen.getByRole("button", { name: /create/i }));
    await waitFor(() => {
      expect(screen.getByText("Create Character")).toBeInTheDocument();
    });
    await user.type(screen.getByPlaceholderText("Character name"), "Fresh");

    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ ...characterFixture, id: 4242 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const buttons = screen.getAllByRole("button", { name: /create/i });
    await user.click(buttons[buttons.length - 1]);

    await waitFor(() => {
      expect(useUiStore.getState().selectedCharacterId).toBe(4242);
    });
  });
});
