/**
 * CharacterEditDialog.test.tsx - FE-6B: character edit + delete UI.
 *
 * Covers:
 *  - Form prefill from the character
 *  - PATCH payload contains ONLY changed fields
 *  - No-change save closes without any PATCH request
 *  - Two-step delete confirm shows CHARACTER_DELETE_CASCADE_WARNING verbatim
 *  - Delete cascade: DELETE call, selection cleared, chats+characters invalidated
 *  - Errors render as safe mapped messages (never raw detail)
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { mockFetch } from "@/test/mocks/api";
import { characterFixture } from "@/test/mocks/fixtures";
import { CharacterEditDialog } from "@/components/characters/CharacterEditDialog";
import { CharacterCard } from "@/components/characters/CharacterCard";
import { Button } from "@/components/ui/button";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useUiStore } from "@/lib/store/uiStore";
import { CHARACTER_DELETE_CASCADE_WARNING } from "@/lib/characters";
import { keys } from "@/lib/query/keys";
import type { ReactNode } from "react";

function makeWrapper(qc: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={qc}>
        <TooltipProvider>{children}</TooltipProvider>
      </QueryClientProvider>
    );
  };
}

function newQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 } },
  });
}

async function openDialog(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Edit" }));
  await waitFor(() => {
    expect(screen.getByText("Edit Character")).toBeInTheDocument();
  });
}

describe("Character Edit Dialog Tests", () => {
  beforeEach(() => {
    useUiStore.setState({ selectedCharacterId: null, selectedChatId: null });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("prefills every field from the character", async () => {
    mockFetch({});
    const user = userEvent.setup();
    render(
      <CharacterEditDialog
        character={characterFixture}
        trigger={<Button>Edit</Button>}
      />,
      { wrapper: makeWrapper(newQueryClient()) },
    );

    await openDialog(user);

    expect(screen.getByLabelText("Character name")).toHaveValue(
      characterFixture.name,
    );
    expect(screen.getByLabelText("Character description")).toHaveValue(
      characterFixture.description,
    );
    expect(screen.getByLabelText("Character personality")).toHaveValue(
      characterFixture.personality,
    );
    expect(screen.getByLabelText("Character scenario")).toHaveValue(
      characterFixture.scenario,
    );
    expect(screen.getByLabelText("Character first message")).toHaveValue(
      characterFixture.first_mes,
    );
    expect(screen.getByLabelText("Character system prompt")).toHaveValue(
      characterFixture.system_prompt,
    );
    expect(screen.getByLabelText("Character tags")).toHaveValue(
      characterFixture.tags.join(", "),
    );
  });

  it("sends ONLY changed fields in the PATCH payload", async () => {
    const fetchMock = mockFetch({
      "/characters/1": {
        body: { ...characterFixture, name: "Renamed Character" },
      },
    });
    const user = userEvent.setup();
    render(
      <CharacterEditDialog
        character={characterFixture}
        trigger={<Button>Edit</Button>}
      />,
      { wrapper: makeWrapper(newQueryClient()) },
    );

    await openDialog(user);

    const nameInput = screen.getByLabelText("Character name");
    await user.clear(nameInput);
    await user.type(nameInput, "Renamed Character");

    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      const patchCalls = fetchMock.mock.calls.filter(
        (call) =>
          typeof call[0] === "string" &&
          call[0].includes("/characters/1") &&
          call[1]?.method === "PATCH",
      );
      expect(patchCalls.length).toBe(1);
      const body = JSON.parse(patchCalls[0][1]?.body as string);
      expect(body).toEqual({ name: "Renamed Character" });
    });
  });

  it("saving with no changes closes without a PATCH request", async () => {
    const fetchMock = mockFetch({});
    const user = userEvent.setup();
    render(
      <CharacterEditDialog
        character={characterFixture}
        trigger={<Button>Edit</Button>}
      />,
      { wrapper: makeWrapper(newQueryClient()) },
    );

    await openDialog(user);
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(screen.queryByText("Edit Character")).not.toBeInTheDocument();
    });
    const patchCalls = fetchMock.mock.calls.filter(
      (call) => call[1]?.method === "PATCH",
    );
    expect(patchCalls).toHaveLength(0);
  });

  it("delete confirm shows CHARACTER_DELETE_CASCADE_WARNING verbatim and focuses confirm", async () => {
    mockFetch({});
    const user = userEvent.setup();
    render(
      <CharacterEditDialog
        character={characterFixture}
        trigger={<Button>Edit</Button>}
      />,
      { wrapper: makeWrapper(newQueryClient()) },
    );

    await openDialog(user);
    await user.click(
      screen.getByRole("button", { name: /delete character/i }),
    );

    // Two-step confirm: the exact constant renders verbatim
    expect(
      screen.getByText(CHARACTER_DELETE_CASCADE_WARNING),
    ).toBeInTheDocument();
    // Destructive confirm a11y: confirm button is focused
    expect(
      screen.getByRole("button", { name: /delete permanently/i }),
    ).toHaveFocus();
  });

  it("cancel backs out of the delete confirm without deleting", async () => {
    const fetchMock = mockFetch({});
    const user = userEvent.setup();
    render(
      <CharacterEditDialog
        character={characterFixture}
        trigger={<Button>Edit</Button>}
      />,
      { wrapper: makeWrapper(newQueryClient()) },
    );

    await openDialog(user);
    await user.click(
      screen.getByRole("button", { name: /delete character/i }),
    );
    const confirmRegion = screen.getByRole("dialog", {
      name: "Confirm delete character",
    });
    await user.click(
      within(confirmRegion).getByRole("button", { name: "Cancel" }),
    );

    expect(
      screen.queryByText(CHARACTER_DELETE_CASCADE_WARNING),
    ).not.toBeInTheDocument();
    const deleteCalls = fetchMock.mock.calls.filter(
      (call) => call[1]?.method === "DELETE",
    );
    expect(deleteCalls).toHaveLength(0);
  });

  it("confirmed delete calls DELETE, clears selection, invalidates chats+characters", async () => {
    const fetchMock = mockFetch({
      "/characters/1": { body: { ok: true } },
    });
    const qc = newQueryClient();
    const invalidateSpy = vi.spyOn(qc, "invalidateQueries");
    useUiStore.setState({ selectedCharacterId: 1, selectedChatId: 5 });

    const user = userEvent.setup();
    render(
      <CharacterEditDialog
        character={characterFixture}
        trigger={<Button>Edit</Button>}
      />,
      { wrapper: makeWrapper(qc) },
    );

    await openDialog(user);
    await user.click(
      screen.getByRole("button", { name: /delete character/i }),
    );
    await user.click(
      screen.getByRole("button", { name: /delete permanently/i }),
    );

    await waitFor(() => {
      const deleteCalls = fetchMock.mock.calls.filter(
        (call) =>
          typeof call[0] === "string" &&
          call[0].includes("/characters/1") &&
          call[1]?.method === "DELETE",
      );
      expect(deleteCalls).toHaveLength(1);
    });

    // Cascade: the deleted character (and its chats) are deselected
    await waitFor(() => {
      expect(useUiStore.getState().selectedCharacterId).toBeNull();
      expect(useUiStore.getState().selectedChatId).toBeNull();
    });

    // Cascade invalidations from useDeleteCharacter
    const invalidatedKeys = invalidateSpy.mock.calls.map(
      (call) => JSON.stringify(call[0]?.queryKey),
    );
    expect(invalidatedKeys).toContain(JSON.stringify(keys.characters()));
    expect(invalidatedKeys).toContain(JSON.stringify(keys.chats()));

    // Dialog closed after delete
    await waitFor(() => {
      expect(screen.queryByText("Edit Character")).not.toBeInTheDocument();
    });
  });

  it("delete of a non-selected character keeps the current selection", async () => {
    mockFetch({
      "/characters/1": { body: { ok: true } },
    });
    useUiStore.setState({ selectedCharacterId: 2, selectedChatId: 9 });

    const user = userEvent.setup();
    render(
      <CharacterEditDialog
        character={characterFixture}
        trigger={<Button>Edit</Button>}
      />,
      { wrapper: makeWrapper(newQueryClient()) },
    );

    await openDialog(user);
    await user.click(
      screen.getByRole("button", { name: /delete character/i }),
    );
    await user.click(
      screen.getByRole("button", { name: /delete permanently/i }),
    );

    await waitFor(() => {
      expect(screen.queryByText("Edit Character")).not.toBeInTheDocument();
    });
    expect(useUiStore.getState().selectedCharacterId).toBe(2);
    expect(useUiStore.getState().selectedChatId).toBe(9);
  });

  it("patch failure renders a safe mapped message, never raw detail", async () => {
    const fetchMock = mockFetch({});
    const user = userEvent.setup();
    render(
      <CharacterEditDialog
        character={characterFixture}
        trigger={<Button>Edit</Button>}
      />,
      { wrapper: makeWrapper(newQueryClient()) },
    );

    await openDialog(user);

    const nameInput = screen.getByLabelText("Character name");
    await user.clear(nameInput);
    await user.type(nameInput, "New Name");

    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "RAW_UPSTREAM_DETAIL" }), {
        status: 500,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(
      await screen.findByText("Something went wrong. Please try again."),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("RAW_UPSTREAM_DETAIL"),
    ).not.toBeInTheDocument();
    // Dialog stays open so the user can retry
    expect(screen.getByText("Edit Character")).toBeInTheDocument();
  });

  it("CharacterCard edit trigger opens the editor WITHOUT changing selection", async () => {
    mockFetch({});
    useUiStore.setState({ selectedCharacterId: 2, selectedChatId: null });

    const user = userEvent.setup();
    render(<CharacterCard character={characterFixture} />, {
      wrapper: makeWrapper(newQueryClient()),
    });

    await user.click(
      screen.getByRole("button", { name: "Edit character" }),
    );
    await waitFor(() => {
      expect(screen.getByText("Edit Character")).toBeInTheDocument();
    });

    // Selection untouched by opening the editor
    expect(useUiStore.getState().selectedCharacterId).toBe(2);
  });

  it("CharacterCard select button still selects the character", async () => {
    mockFetch({});
    useUiStore.setState({ selectedCharacterId: null, selectedChatId: null });

    const user = userEvent.setup();
    render(<CharacterCard character={characterFixture} />, {
      wrapper: makeWrapper(newQueryClient()),
    });

    await user.click(
      screen.getByRole("button", {
        name: `Select character ${characterFixture.name}`,
      }),
    );

    expect(useUiStore.getState().selectedCharacterId).toBe(
      characterFixture.id,
    );
  });

  it("delete failure shows character_not_found mapped message", async () => {
    mockFetch({
      "/characters/1": {
        status: 404,
        body: { detail: "character_not_found" },
      },
    });
    const user = userEvent.setup();
    render(
      <CharacterEditDialog
        character={characterFixture}
        trigger={<Button>Edit</Button>}
      />,
      { wrapper: makeWrapper(newQueryClient()) },
    );

    await openDialog(user);
    await user.click(
      screen.getByRole("button", { name: /delete character/i }),
    );
    await user.click(
      screen.getByRole("button", { name: /delete permanently/i }),
    );

    expect(
      await screen.findByText(
        "This character no longer exists. Please refresh characters.",
      ),
    ).toBeInTheDocument();
  });
});
