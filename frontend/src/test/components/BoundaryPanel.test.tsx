/**
 * FAZ 3 - the limits panel.
 *
 * The thing this screen must never do is make a limit look like it is in force
 * when it is not. Two states carry that risk: a chat told to ignore the global
 * set, and the moment the global toggle fails to save. Both are tested here,
 * because both look like a working limit list otherwise.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import {
  BoundaryPanel,
  BOUNDARY_MAX_CHARS,
} from "@/components/notebook/BoundaryPanel";
import { mockFetch } from "../mocks/api";
import { useUiStore } from "@/lib/store/uiStore";

function boundary(over: Partial<Record<string, unknown>> = {}) {
  return {
    id: 1, scope: "global", chat_id: null, label: "no gore",
    phrasing: "Avoid graphic injury.", severity: "hard", polarity: "avoid",
    on_violation: "pause", source: "explicit", rating_ceiling: null,
    exempt_from_trim: 1, last_confirmed_at: null, active: 1,
    created_at: "2026-08-19", updated_at: "2026-08-19", ...over,
  };
}

describe("BoundaryPanel", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: 7 });
  });
  afterEach(() => vi.restoreAllMocks());

  it("says plainly that limits are never trimmed", async () => {
    // The one promise this panel makes that the notebook does not. If the
    // wording ever softens, the refusal behind it stops being explained.
    mockFetch({ "/notebook/7/boundaries": { body: { boundaries: [], use_global: true } } });
    renderWithQueryClient(<BoundaryPanel />);
    expect(await screen.findByText(/never trimmed/i)).toBeInTheDocument();
  });

  it("shows a limit with its strictness and where it applies", async () => {
    mockFetch({
      "/notebook/7/boundaries": { body: { boundaries: [boundary()], use_global: true } },
    });
    renderWithQueryClient(<BoundaryPanel />);
    expect(await screen.findByText("no gore")).toBeInTheDocument();
    expect(screen.getByText(/never - everywhere/i)).toBeInTheDocument();
  });

  it("distinguishes a chat-scoped limit from a global one", async () => {
    mockFetch({
      "/notebook/7/boundaries": {
        body: {
          boundaries: [boundary({ id: 2, scope: "chat", chat_id: 7,
                                  label: "just here", severity: "soft" })],
        },
      },
    });
    renderWithQueryClient(<BoundaryPanel />);
    expect(await screen.findByText(/prefer not - this chat/i))
      .toBeInTheDocument();
  });

  it("adds a limit as explicit, never inferred", async () => {
    // The route has no `source` field at all: a limit typed by a person is
    // explicit by construction, and the app must never invent a hard one.
    const user = userEvent.setup();
    mockFetch({
      "/notebook/7/boundaries": { body: { boundaries: [], use_global: true } },
      "/notebook/boundaries": { body: boundary() },
    });
    renderWithQueryClient(<BoundaryPanel />);

    await user.type(await screen.findByPlaceholderText(/keep out of the story/i),
                    "no gore");
    await user.click(screen.getByRole("button", { name: /add limit/i }));

    await waitFor(() => {
      const call = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls
        .find((c: unknown[]) =>
          String(c[0]).includes("/notebook/boundaries")
          && (c[1] as RequestInit | undefined)?.method === "POST");
      expect(call).toBeTruthy();
      const body = JSON.parse(String((call![1] as RequestInit).body));
      expect(body).not.toHaveProperty("source");
      expect(body.severity).toBe("hard");
    });
  });

  it("can set the global limits aside for one chat", async () => {
    const user = userEvent.setup();
    mockFetch({
      "/notebook/7/boundaries": { body: { boundaries: [boundary()], use_global: true } },
      "/notebook/7/use-global": { body: { ok: true, use_global: false } },
    });
    renderWithQueryClient(<BoundaryPanel />);

    await user.click(await screen.findByRole("switch", {
      name: /use my global limits/i,
    }));

    await waitFor(() => {
      expect(
        (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.some(
          (c: unknown[]) => String(c[0]).includes("/use-global"),
        ),
      ).toBe(true);
    });
  });

  it("renders the switch OFF when the server says it is off", async () => {
    // The bug this replaces: the panel guessed `true` on every mount and read
    // the stored flag nowhere. A chat with its global limits set aside showed
    // the switch ON after any remount - the exact "you believe it is in force
    // and it is not" failure this panel's own copy warns about.
    mockFetch({
      "/notebook/7/boundaries": {
        body: { boundaries: [boundary()], use_global: false },
      },
    });
    renderWithQueryClient(<BoundaryPanel />);
    const toggle = await screen.findByRole("switch", {
      name: /use my global limits/i,
    });
    // waitFor, not a bare assert: before the query resolves the switch shows
    // its fallback, and asserting there would pass on a panel that never reads
    // the server at all.
    await waitFor(() => expect(toggle).not.toBeChecked());
  });

  it("puts the switch back if the save fails", async () => {
    // Otherwise the screen says the global limits are off while the server
    // still has them on - the exact "looks in force, is not" failure.
    const user = userEvent.setup();
    mockFetch({
      "/notebook/7/boundaries": { body: { boundaries: [boundary()], use_global: true } },
      "/notebook/7/use-global": { status: 500, body: { detail: "nope" } },
    });
    renderWithQueryClient(<BoundaryPanel />);

    const toggle = await screen.findByRole("switch", {
      name: /use my global limits/i,
    });
    await user.click(toggle);

    await waitFor(() => {
      expect(toggle).toBeChecked();
    });
  });
});

/**
 * Before this fix the panel only ever called useChatBoundaries, which is
 * disabled outright with no chat selected - so `rows` was always [] here and
 * "No limits set." printed over GLOBAL limits that were sitting in the
 * database the whole time. useGlobalBoundaries is the existing hook that
 * answers this question; the fix is wiring it in for exactly this state.
 */
describe("with no chat open", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: null });
  });
  afterEach(() => vi.restoreAllMocks());

  it("still says No limits set when there really are none", async () => {
    // GROUND: the fix must not invent limits that are not there. An empty
    // global list with no chat open is still an empty list, not an error
    // state and not a reason to hide the sentence that was already correct.
    mockFetch({ "/notebook/boundaries": { body: { boundaries: [] } } });
    renderWithQueryClient(<BoundaryPanel />);
    expect(await screen.findByText(/no limits set/i)).toBeInTheDocument();
  });

  it("shows the global limits instead of claiming none exist", async () => {
    // POSITIVE CONTROL: the failure this replaces. A user with a global
    // limit and no chat open used to see "No limits set." here - the exact
    // "believes a limit is in force and it is not" case the panel's own
    // copy warns about, except read backwards.
    mockFetch({
      "/notebook/boundaries": { body: { boundaries: [boundary()] } },
    });
    renderWithQueryClient(<BoundaryPanel />);
    expect(await screen.findByText("no gore")).toBeInTheDocument();
    expect(screen.queryByText(/no limits set/i)).not.toBeInTheDocument();
  });
});

/**
 * The delete confirm here replaces its own trigger too, the same as the
 * notebook's NoteRow, and it inherits the same fix: the SAFE choice takes
 * focus when the question opens, Escape cancels without deleting, and the
 * trigger gets focus back either way it closes.
 */
describe("answering the delete question from the keyboard", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: 7 });
  });
  afterEach(() => vi.restoreAllMocks());

  function mountOneLimit() {
    mockFetch({
      "/notebook/7/boundaries": {
        body: { boundaries: [boundary()], use_global: true },
      },
    });
    return renderWithQueryClient(<BoundaryPanel />);
  }

  it("focuses the SAFE choice when the question opens", async () => {
    const user = userEvent.setup();
    mountOneLimit();
    await user.click(
      await screen.findByRole("button", { name: /delete limit/i }));

    expect(screen.getByRole("button", { name: /keep limit/i })).toHaveFocus();
    // Ground, twice over: not <body> (the unfixed behaviour) and not the
    // destructive button, which would put an irreversible delete under a
    // reflexive Enter.
    expect(document.activeElement).not.toBe(document.body);
    expect(screen.getByRole("button", { name: /confirm delete limit/i }))
      .not.toHaveFocus();
  });

  it("Escape backs out and hands focus back to the trigger", async () => {
    const user = userEvent.setup();
    mountOneLimit();
    await user.click(
      await screen.findByRole("button", { name: /delete limit/i }));
    await user.keyboard("{Escape}");

    const trigger = await screen.findByRole("button", { name: /delete limit/i });
    expect(trigger).toHaveFocus();
    // Ground: backing out leaves the limit alone rather than deleting it.
    expect(screen.getByTestId("boundary-1")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /confirm delete limit/i }))
      .not.toBeInTheDocument();
  });

  it("keeping the limit hands focus back to the trigger too", async () => {
    // Ground for the Escape test: the same return happens on the button
    // path, so the trigger is genuinely refocusable and the key handler is
    // not the only thing holding this together.
    const user = userEvent.setup();
    mountOneLimit();
    await user.click(
      await screen.findByRole("button", { name: /delete limit/i }));
    await user.click(screen.getByRole("button", { name: /keep limit/i }));

    expect(await screen.findByRole("button", { name: /delete limit/i }))
      .toHaveFocus();
  });
});

describe("the safeword", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: 7 });
  });
  afterEach(() => vi.restoreAllMocks());

  function mount(word = "") {
    mockFetch({
      "POST /notebook/safeword": { body: { ok: true } },
      "/notebook/safeword": { body: { word } },
      "/notebook/7/boundaries": { body: { boundaries: [], use_global: true } },
    });
    return renderWithQueryClient(<BoundaryPanel />);
  }

  it("says it is checked here, not asked of the model", async () => {
    // The whole reason it exists. Every other line on this panel is a request
    // to a model; this one is not, and a user reaching for it in a bad moment
    // needs to know which kind it is.
    mount();
    expect(await screen.findByText(/not a request to the model; it is checked here/i))
      .toBeInTheDocument();
  });

  it("shows the stored word", async () => {
    mount("kırmızı");
    await waitFor(() =>
      expect(screen.getByLabelText(/safeword/i)).toHaveValue("kırmızı"));
  });

  it("saves when the field loses focus", async () => {
    // A50: the vault can lock mid-thought, and a buffer nobody committed is a
    // buffer the lock eats.
    const user = userEvent.setup();
    mount("");
    const box = await screen.findByLabelText(/safeword/i);
    await waitFor(() => expect(box).toBeEnabled());
    await user.type(box, "kırmızı");
    await user.tab();

    await waitFor(() => {
      const call = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls
        .find((c: unknown[]) =>
          String(c[0]).includes("/notebook/safeword")
          && (c[1] as RequestInit | undefined)?.method === "POST");
      expect(call).toBeTruthy();
      expect(JSON.parse(String((call![1] as RequestInit).body)))
        .toEqual({ word: "kırmızı" });
    });
  });

  it("does not save when nothing changed", async () => {
    // Ground: blur fires on every tab through the panel.
    const user = userEvent.setup();
    mount("kırmızı");
    const box = await screen.findByLabelText(/safeword/i);
    await waitFor(() => expect(box).toHaveValue("kırmızı"));
    await user.click(box);
    await user.tab();
    expect((globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls
      .some((c: unknown[]) =>
        String(c[0]).includes("/notebook/safeword")
        && (c[1] as RequestInit | undefined)?.method === "POST")).toBe(false);
  });
});

describe("the length a limit is allowed to be", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: 7 });
  });
  afterEach(() => vi.restoreAllMocks());

  function mountEmpty() {
    mockFetch({
      "/notebook/7/boundaries": { body: { boundaries: [], use_global: true } },
      "/notebook/boundaries": { body: boundary() },
    });
    return renderWithQueryClient(<BoundaryPanel />);
  }

  function posted() {
    const call = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls
      .find((c: unknown[]) =>
        String(c[0]).includes("/notebook/boundaries")
        && (c[1] as RequestInit | undefined)?.method === "POST");
    return call
      ? JSON.parse(String((call[1] as RequestInit).body))
      : null;
  }

  it("holds the field to the length the backend accepts", async () => {
    // The number is imported, not copied. The backend's cap is arithmetic -
    // the limits block is never trimmed, so it has to fit the smallest model
    // this app serves - and a second hand-written copy of that number here is
    // how the field and the contract drift apart with nothing saying so.
    const user = userEvent.setup();
    mountEmpty();
    const box = await screen.findByPlaceholderText(/keep out of the story/i);
    await user.type(box, "x".repeat(BOUNDARY_MAX_CHARS + 40));
    expect((box as HTMLInputElement).value).toHaveLength(BOUNDARY_MAX_CHARS);
  });

  it("still sends a limit written right up to the cap, whole", async () => {
    // The positive control, and the ground for the test above: a field that
    // truncated everything would pass that one too. A limit at the cap has to
    // arrive at the server intact, character for character.
    const user = userEvent.setup();
    mountEmpty();
    const box = await screen.findByPlaceholderText(/keep out of the story/i);
    const atTheCap = "x".repeat(BOUNDARY_MAX_CHARS);
    await user.type(box, atTheCap);
    await user.click(screen.getByRole("button", { name: /add limit/i }));

    await waitFor(() => {
      const body = posted();
      expect(body).toBeTruthy();
      expect(body.phrasing).toBe(atTheCap);
      expect(body.label).toBe(atTheCap);
    });
  });

  it("does not undercut the backend by capping shorter than it does", async () => {
    // The cap being LOWER than the backend's would be a silent product
    // decision made in a maxLength attribute: limits the contract accepts that
    // the app cannot type. Matching is the whole point - the field is a
    // convenience in front of create_boundary, not a second rule.
    mountEmpty();
    const box = await screen.findByPlaceholderText(/keep out of the story/i);
    expect(box).toHaveAttribute("maxLength", String(BOUNDARY_MAX_CHARS));
  });
});
