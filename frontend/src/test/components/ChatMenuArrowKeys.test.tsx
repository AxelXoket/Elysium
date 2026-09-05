/**
 * ChatMenuArrowKeys.test.tsx - H-112: the chat action menu under a keyboard.
 *
 * The menu could already be opened with a key and closed with Escape, and the
 * items already carried their menu roles. What it had no answer for was the
 * step in between: focus stayed on the ⋯ trigger when the menu appeared, so
 * there was nobody standing in the list for an arrow key to move, and Tab
 * walked the items one at a time as if they were three unrelated buttons.
 *
 * So this is measured in two steps, in the order they depend on each other:
 * first that focus ENTERS the menu, then that the arrows move it. An arrow
 * assertion written without the first step passes or fails on where focus
 * happened to be, which is not a statement about the arrows at all.
 *
 * Every assertion below reads `document.activeElement` - where a keyboard
 * user actually is - rather than any attribute that claims to describe it.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { ChatList } from "@/components/sidebar/ChatList";
import { useUiStore } from "@/lib/store/uiStore";
import { useStreamRegistry } from "@/lib/chat/streamRegistry";
import { useErrorStore } from "@/lib/errors";
import { mockFetch } from "../mocks/api";
import { chatFixture } from "../mocks/fixtures";

const TRIGGER = /open chat actions for test chat/i;

function trigger(): HTMLElement {
  return screen.getByRole("button", { name: TRIGGER });
}

function itemNamed(name: RegExp): HTMLElement {
  return screen.getByRole("menuitem", { name });
}

/** Open the menu the way a keyboard reaches it: focus the trigger, press Enter. */
async function openWithKeyboard(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByText("Test Chat");
  trigger().focus();
  expect(document.activeElement).toBe(trigger());
  await user.keyboard("{Enter}");
  await waitFor(() => {
    expect(screen.getByRole("menu")).toBeInTheDocument();
  });
}

describe("ChatList action menu - arrow-key navigation", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedCharacterId: 1, selectedChatId: null });
    useStreamRegistry.setState({ controllers: new Map() });
    useErrorStore.getState().clearAll();
    mockFetch({ "/chats": { body: [chatFixture] } });
  });

  afterEach(() => {
    useStreamRegistry.setState({ controllers: new Map() });
    vi.restoreAllMocks();
  });

  it("STEP 1: opening the menu moves focus into it", async () => {
    const user = userEvent.setup();
    renderWithQueryClient(<ChatList />);

    await openWithKeyboard(user);

    // Focus has left the trigger and landed on the first item. Everything
    // below is a statement about moving from here; without this it would be a
    // statement about the trigger.
    expect(document.activeElement).toBe(itemNamed(/rename/i));
  });

  it("STEP 2: ArrowDown walks the items and wraps at the bottom", async () => {
    const user = userEvent.setup();
    renderWithQueryClient(<ChatList />);

    await openWithKeyboard(user);

    await user.keyboard("{ArrowDown}");
    expect(document.activeElement).toBe(itemNamed(/clear chat/i));

    await user.keyboard("{ArrowDown}");
    expect(document.activeElement).toBe(itemNamed(/delete chat/i));

    await user.keyboard("{ArrowDown}");
    expect(document.activeElement).toBe(itemNamed(/rename/i));
  });

  it("ArrowUp from the first item wraps to the last", async () => {
    const user = userEvent.setup();
    renderWithQueryClient(<ChatList />);

    await openWithKeyboard(user);

    await user.keyboard("{ArrowUp}");
    expect(document.activeElement).toBe(itemNamed(/delete chat/i));

    await user.keyboard("{ArrowUp}");
    expect(document.activeElement).toBe(itemNamed(/clear chat/i));
  });

  it("Home and End jump to the ends of the menu", async () => {
    const user = userEvent.setup();
    renderWithQueryClient(<ChatList />);

    await openWithKeyboard(user);

    await user.keyboard("{End}");
    expect(document.activeElement).toBe(itemNamed(/delete chat/i));

    await user.keyboard("{Home}");
    expect(document.activeElement).toBe(itemNamed(/rename/i));
  });

  it("the open menu is ONE tab stop, so Tab leaves it instead of walking it", async () => {
    const user = userEvent.setup();
    renderWithQueryClient(<ChatList />);

    await openWithKeyboard(user);
    await user.keyboard("{ArrowDown}");
    expect(document.activeElement).toBe(itemNamed(/clear chat/i));

    await user.tab();

    // Roving tabindex: the arrows own movement inside the menu, Tab owns
    // leaving it. Landing on the third item would mean the menu is still
    // three separate stops in the page order.
    expect(screen.getByRole("menu").contains(document.activeElement)).toBe(
      false,
    );
  });

  it("a streaming chat greys out two items, and the arrows do not step onto them", async () => {
    const user = userEvent.setup();
    useStreamRegistry.setState({
      controllers: new Map([[1, new AbortController()]]),
    });
    renderWithQueryClient(<ChatList />);

    await openWithKeyboard(user);

    // Focus cannot rest on a disabled control, so the only reachable item is
    // the one that stays enabled. Stepping onto a greyed row would strand the
    // reader on something that never takes focus.
    expect(document.activeElement).toBe(itemNamed(/rename/i));
    await user.keyboard("{ArrowDown}");
    expect(document.activeElement).toBe(itemNamed(/rename/i));
    await user.keyboard("{ArrowUp}");
    expect(document.activeElement).toBe(itemNamed(/rename/i));
  });

  it("GROUND CONTROL: the menu opens with a key and Escape hands focus back", async () => {
    const user = userEvent.setup();
    renderWithQueryClient(<ChatList />);

    // Both halves of this were already true before the arrows existed. It is
    // here to prove the harness can drive this menu at all, so a red arrow
    // assertion means the arrows and not the setup.
    await openWithKeyboard(user);

    await user.keyboard("{Escape}");

    await waitFor(() => {
      expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    });
    expect(document.activeElement).toBe(trigger());
  });

  it("POSITIVE CONTROL: aria-expanded tracks the menu the arrows move in", async () => {
    const user = userEvent.setup();
    renderWithQueryClient(<ChatList />);

    await screen.findByText("Test Chat");
    expect(trigger()).toHaveAttribute("aria-expanded", "false");

    await openWithKeyboard(user);
    expect(trigger()).toHaveAttribute("aria-expanded", "true");

    await user.keyboard("{Escape}");
    await waitFor(() => {
      expect(trigger()).toHaveAttribute("aria-expanded", "false");
    });
  });
});
