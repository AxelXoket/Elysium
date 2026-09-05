/**
 * CharacterDeleteCascadeDescription.test.tsx - H-105.
 *
 * The delete step puts focus straight on the destructive button. Someone
 * listening heard "Delete permanently" and nothing else: the sentence next to
 * it, the one naming every chat and message that goes with the character, was
 * on screen but was not tied to the control it describes. Deleting is not
 * reversible, so hearing what it takes with it has to arrive WITH the focus,
 * not from reading around it afterwards.
 *
 * The link is followed here the way an assistive technology follows it - from
 * the focused button, through `aria-describedby`, to whatever that id
 * actually resolves to - so a renamed id with a stale pointer reads as broken
 * rather than as passing.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { mockFetch } from "@/test/mocks/api";
import { characterFixture } from "@/test/mocks/fixtures";
import { CharacterEditDialog } from "@/components/characters/CharacterEditDialog";
import { Button } from "@/components/ui/button";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useUiStore } from "@/lib/store/uiStore";
import { CHARACTER_DELETE_CASCADE_WARNING } from "@/lib/characters";

function renderEditor() {
  return renderWithQueryClient(
    <CharacterEditDialog
      character={characterFixture}
      trigger={<Button>Edit</Button>}
    />,
    { wrapper: TooltipProvider },
  );
}

async function openEditor(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Edit" }));
  await waitFor(() => {
    expect(screen.getByText("Edit Character")).toBeInTheDocument();
  });
}

async function openDeleteConfirm(user: ReturnType<typeof userEvent.setup>) {
  await openEditor(user);
  await user.click(screen.getByRole("button", { name: /delete character/i }));
  await waitFor(() => {
    expect(
      screen.getByRole("dialog", { name: "Confirm delete character" }),
    ).toBeInTheDocument();
  });
}

/** What a reader is handed when the described control takes focus. */
function descriptionOfFocusedControl(): string | null {
  const focused = document.activeElement as HTMLElement | null;
  const id = focused?.getAttribute("aria-describedby");
  if (id == null) return null;
  return document.getElementById(id)?.textContent ?? null;
}

describe("Character delete confirm - the cascade warning reaches the button", () => {
  beforeEach(() => {
    useUiStore.setState({ selectedCharacterId: null, selectedChatId: null });
    mockFetch({});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("describes the focused confirm button with the cascade warning", async () => {
    const user = userEvent.setup();
    renderEditor();

    await openDeleteConfirm(user);

    // The step focuses its own destructive button; that is the control whose
    // description matters, so it is read off whatever actually has focus.
    expect(document.activeElement).toBe(
      screen.getByRole("button", { name: /delete permanently/i }),
    );
    expect(
      descriptionOfFocusedControl(),
      "confirm button has no aria-describedby, or it points at nothing",
    ).toBe(CHARACTER_DELETE_CASCADE_WARNING);
  });

  it("POSITIVE CONTROL: the confirm panel is the one this test thinks it is", async () => {
    const user = userEvent.setup();
    renderEditor();

    await openDeleteConfirm(user);

    // Named panel, warning inside it, both correct before this unit and
    // unchanged by it. If these go red the queries are finding the wrong
    // region, not a missing description.
    const panel = screen.getByRole("dialog", {
      name: "Confirm delete character",
    });
    expect(
      within(panel).getByText(CHARACTER_DELETE_CASCADE_WARNING),
    ).toBeInTheDocument();
    expect(
      within(panel).getByRole("button", { name: /delete permanently/i }),
    ).toBeInTheDocument();
  });

  it("GROUND CONTROL: before the confirm step there is no such button to describe", async () => {
    const user = userEvent.setup();
    renderEditor();

    await openEditor(user);

    // The editor is open and the destructive step is not - so the assertion
    // above is not passing on a render that never got there.
    expect(
      screen.queryByRole("button", { name: /delete permanently/i }),
    ).toBeNull();
    expect(screen.queryByText(CHARACTER_DELETE_CASCADE_WARNING)).toBeNull();
    expect(descriptionOfFocusedControl()).toBeNull();

    // POSITIVE CONTROL for that absence: the same three queries all find
    // something once the step is open.
    await user.click(screen.getByRole("button", { name: /delete character/i }));
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /delete permanently/i }),
      ).toBeInTheDocument();
    });
    expect(
      screen.getByText(CHARACTER_DELETE_CASCADE_WARNING),
    ).toBeInTheDocument();
    expect(descriptionOfFocusedControl()).toBe(
      CHARACTER_DELETE_CASCADE_WARNING,
    );
  });

  it("backing out and reopening keeps the description attached", async () => {
    const user = userEvent.setup();
    renderEditor();

    await openDeleteConfirm(user);
    const panel = screen.getByRole("dialog", {
      name: "Confirm delete character",
    });
    await user.click(within(panel).getByRole("button", { name: "Cancel" }));
    await waitFor(() => {
      expect(
        screen.queryByRole("button", { name: /delete permanently/i }),
      ).toBeNull();
    });

    // A generated id is stable per instance, so a second visit to the step
    // must not leave the button pointing at an id that went away with the
    // first one.
    await user.click(screen.getByRole("button", { name: /delete character/i }));
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /delete permanently/i }),
      ).toBeInTheDocument();
    });
    expect(descriptionOfFocusedControl()).toBe(
      CHARACTER_DELETE_CASCADE_WARNING,
    );
  });
});
