/**
 * What one bubble costs, and whether that cost knows how long the chat is.
 *
 * Three values in MessageBubble were derived by sweeping the whole message
 * array on every render: regenerate eligibility, "nobody answered this yet",
 * and the number of rows that a delete would take. N bubbles each sweeping N
 * rows is O(N^2) per render, and the renders that trigger it are not rare -
 * every bubble subscribes to the reader's appearance settings, so dragging
 * the opacity slider re-renders all of them while the message array itself
 * never moves.
 *
 * THE FROZEN BUDGET (written before the memo landed, not fitted to it):
 *
 *   B1  At N = 200, one full re-render in which the message array identity is
 *       UNCHANGED may cost at most N helper calls in total.
 *   B2  That total may not grow with N: the count at N = 200 must be <= the
 *       count at N = 50.
 *
 * B1 is the queue's number. B2 is the one that cannot be satisfied by luck of
 * fixture shape - it is the invariant itself, stated as an experiment.
 *
 * The counters wrap the REAL helpers, so every assertion below is about work
 * that actually happened, never about the text of a source file.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act, cleanup, screen, within } from "@testing-library/react";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { MessageList } from "@/components/chat/MessageList";
import { useUiStore, MSG_OPACITY_DEFAULT } from "@/lib/store/uiStore";
import { keys } from "@/lib/query/keys";
import { mockFetch } from "../mocks/api";
import type { Message } from "@/lib/schemas/chats";

/**
 * Hoisted because `vi.mock`'s factory is lifted above the imports; the
 * counters have to exist before the module graph is built.
 */
const counters = vi.hoisted(() => ({ calls: 0 }));

vi.mock("@/lib/chat", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/chat")>();
  return {
    ...actual,
    canRegenerateMessage: (
      ...args: Parameters<typeof actual.canRegenerateMessage>
    ) => {
      counters.calls += 1;
      return actual.canRegenerateMessage(...args);
    },
    isMessageActive: (...args: Parameters<typeof actual.isMessageActive>) => {
      counters.calls += 1;
      return actual.isMessageActive(...args);
    },
  };
});

/** B1's list length, and the larger half of B2's pair. */
const BUDGET_N = 200;
/** B2's smaller list. Any length well below BUDGET_N proves the same point. */
const SMALL_N = 50;

function conversation(rowCount: number): Message[] {
  return Array.from({ length: rowCount }, (_, i) => ({
    id: i + 1,
    chat_id: 1,
    role: i % 2 === 0 ? ("user" as const) : ("assistant" as const),
    content: `Row ${i + 1}`,
    created_at: `2026-01-01T00:00:00`,
  }));
}

/** The flex row that holds a bubble AND its two edge chevrons. */
function rowOf(text: string): HTMLElement {
  const shell = screen.getByText(text).closest(".message-bubble-shell");
  expect(shell).not.toBeNull();
  const row = (shell as HTMLElement).parentElement;
  expect(row).not.toBeNull();
  return row as HTMLElement;
}

/**
 * Mount `rowCount` bubbles, then apply ONE appearance change and report how
 * many helper calls that change cost. The message array is untouched across
 * the update, which is the whole point: nothing about these bubbles' derived
 * values can legitimately have changed.
 *
 * `opacity` is a parameter because the two halves of B2 run in one test: a
 * store write that lands on the value already there is not an update at all,
 * and would score a free zero.
 */
async function costOfAnAppearanceChange(
  rowCount: number,
  opacity: number,
): Promise<number> {
  const rows = conversation(rowCount);
  mockFetch({ "/chats/1/messages": { body: rows } });
  renderWithQueryClient(<MessageList chatId={1} />);
  await screen.findByText(`Row ${rowCount}`);

  counters.calls = 0;
  act(() => {
    useUiStore.setState({ msgOpacity: opacity });
  });
  return counters.calls;
}

describe("a bubble's per-render cost does not know how long the chat is", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    counters.calls = 0;
    useUiStore.setState({
      selectedChatId: 1,
      selectedModelId: "openai/gpt-4o",
      msgOpacity: MSG_OPACITY_DEFAULT,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ msgOpacity: MSG_OPACITY_DEFAULT });
  });

  it("POSITIVE CONTROL: the counter can see the sweeps when they do run", async () => {
    // Without this, a budget of "few calls" would also be met by a counter
    // that is wired to nothing. First paint has no memo to hide behind, so
    // the helpers must be visible here, once per bubble at least.
    const rows = conversation(SMALL_N);
    mockFetch({ "/chats/1/messages": { body: rows } });
    renderWithQueryClient(<MessageList chatId={1} />);
    await screen.findByText(`Row ${SMALL_N}`);

    expect(counters.calls).toBeGreaterThan(0);
  });

  it("B1: an array-stable re-render of 200 bubbles stays inside the budget", async () => {
    const cost = await costOfAnAppearanceChange(BUDGET_N, 0.5);
    expect(cost).toBeLessThanOrEqual(BUDGET_N);
  });

  it("B2: that cost does not grow when the chat gets four times longer", async () => {
    const small = await costOfAnAppearanceChange(SMALL_N, 0.5);
    // Torn down, so the second measurement counts one list and not two.
    cleanup();
    useUiStore.setState({ msgOpacity: MSG_OPACITY_DEFAULT });
    const large = await costOfAnAppearanceChange(BUDGET_N, 0.4);

    expect(large).toBeLessThanOrEqual(small);
  });
});

describe("the memoised values still tell the truth when the list changes", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    counters.calls = 0;
    useUiStore.setState({
      selectedChatId: 1,
      selectedModelId: "openai/gpt-4o",
      msgOpacity: MSG_OPACITY_DEFAULT,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("STALENESS: an older reply loses the regenerate chevron once a newer one arrives", async () => {
    // The failure a wrong dependency list produces, stated as behaviour: the
    // affordance stays on a message the backend would refuse to regenerate.
    const first: Message[] = [
      { id: 1, chat_id: 1, role: "user", content: "Q1", created_at: "2026-01-01T00:00:00" },
      { id: 2, chat_id: 1, role: "assistant", content: "A1", created_at: "2026-01-01T00:00:01" },
    ];
    mockFetch({ "/chats/1/messages": { body: first } });
    const { queryClient } = renderWithQueryClient(<MessageList chatId={1} />);
    await screen.findByText("A1");

    // GROUND CONTROL: the chevron IS there while A1 is the last reply, so the
    // absence asserted below is a real change and not an empty sweep.
    expect(
      within(rowOf("A1")).getByRole("button", {
        name: "Generate a new reply",
      }),
    ).toBeInTheDocument();

    act(() => {
      queryClient.setQueryData<Message[]>(keys.messages(1), [
        ...first,
        { id: 3, chat_id: 1, role: "user", content: "Q2", created_at: "2026-01-01T00:00:02" },
        { id: 4, chat_id: 1, role: "assistant", content: "A2", created_at: "2026-01-01T00:00:03" },
      ]);
    });
    await screen.findByText("A2");

    expect(
      within(rowOf("A1")).queryByRole("button", {
        name: "Generate a new reply",
      }),
    ).toBeNull();
    // POSITIVE CONTROL for the same sweep: the chevron moved, it did not
    // vanish from the page.
    expect(
      within(rowOf("A2")).getByRole("button", {
        name: "Generate a new reply",
      }),
    ).toBeInTheDocument();
  });

  it("STALENESS: the unanswered question stops asking once the reply lands", async () => {
    // The third memoised value. It moves on the arrival of a row that is not
    // this bubble's own, which is exactly what a dependency list keyed on
    // `message.id` cannot see.
    const first: Message[] = [
      { id: 1, chat_id: 1, role: "assistant", content: "Greeting", created_at: "2026-01-01T00:00:00" },
      { id: 2, chat_id: 1, role: "user", content: "Q1", created_at: "2026-01-01T00:00:01" },
    ];
    mockFetch({ "/chats/1/messages": { body: first } });
    const { queryClient } = renderWithQueryClient(
      <MessageList chatId={1} onEditMessage={() => {}} />,
    );
    await screen.findByText("Q1");

    // GROUND CONTROL: nobody has answered yet, so the affordance is there.
    expect(
      within(rowOf("Q1")).getByRole("button", { name: /get a reply/i }),
    ).toBeInTheDocument();

    act(() => {
      queryClient.setQueryData<Message[]>(keys.messages(1), [
        ...first,
        { id: 3, chat_id: 1, role: "assistant", content: "A1", created_at: "2026-01-01T00:00:02" },
      ]);
    });
    await screen.findByText("A1");

    expect(
      within(rowOf("Q1")).queryByRole("button", { name: /get a reply/i }),
    ).toBeNull();
  });

  it("STALENESS: the edit tooltip counts the rows that are there now", async () => {
    // The second derived value, and it moves in the opposite direction: a
    // count that only grows. A memo pinned to the wrong dependency reports
    // the number the chat had when the bubble first rendered.
    const first: Message[] = [
      { id: 1, chat_id: 1, role: "user", content: "Q1", created_at: "2026-01-01T00:00:00" },
      { id: 2, chat_id: 1, role: "assistant", content: "A1", created_at: "2026-01-01T00:00:01" },
      { id: 3, chat_id: 1, role: "assistant", content: "A1b", created_at: "2026-01-01T00:00:02" },
    ];
    mockFetch({ "/chats/1/messages": { body: first } });
    const { queryClient } = renderWithQueryClient(
      <MessageList chatId={1} onEditMessage={() => {}} />,
    );
    await screen.findByText("Q1");

    expect(
      within(rowOf("Q1")).getByRole("button", {
        name: "Edit message",
      }),
    ).toHaveAttribute(
      "title",
      "Edit message (the 2 messages after it are deleted)",
    );

    act(() => {
      queryClient.setQueryData<Message[]>(keys.messages(1), [
        ...first,
        { id: 4, chat_id: 1, role: "user", content: "Q2", created_at: "2026-01-01T00:00:03" },
        { id: 5, chat_id: 1, role: "assistant", content: "A2", created_at: "2026-01-01T00:00:04" },
      ]);
    });
    await screen.findByText("A2");

    expect(
      within(rowOf("Q1")).getByRole("button", {
        name: "Edit message",
      }),
    ).toHaveAttribute(
      "title",
      "Edit message (the 4 messages after it are deleted)",
    );
  });
});
