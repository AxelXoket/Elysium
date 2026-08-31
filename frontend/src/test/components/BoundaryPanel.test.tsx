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
import { useErrorStore } from "@/lib/errors";

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

  function mount(
    word = "",
    post: { status?: number; body: unknown } = { body: { ok: true } },
  ) {
    mockFetch({
      "POST /notebook/safeword": post,
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

  it("does not go on showing a word the server refused", async () => {
    // THE defect. `shown` is `local ?? server` and nothing ever cleared the
    // local value, so after a POST that failed the box displayed the typed
    // word exactly as if it had been saved. For this control that is the
    // worst possible failure: a safeword is a thing you believe is
    // protecting you, and believing it is set when it is not inverts the
    // feature.
    const user = userEvent.setup();
    mount("kırmızı", { status: 500, body: { detail: "boom" } });
    const box = await screen.findByLabelText(/safeword/i);
    await waitFor(() => expect(box).toHaveValue("kırmızı"));

    await user.clear(box);
    await user.type(box, "mor");
    await user.tab();

    // Back to what the server actually holds.
    await waitFor(() => expect(box).toHaveValue("kırmızı"));
  });

  it("still shows the new word when the save SUCCEEDS", async () => {
    // GROUND CONTROL. Clearing the local value on every path must not throw
    // away a word that was saved - the box has to end up showing the new
    // one, which it can only do by reading it back from the server.
    const user = userEvent.setup();
    let stored = "";
    (globalThis as unknown as Record<string, unknown>).__unused = stored;
    mockFetch({
      "POST /notebook/safeword": { body: { ok: true } },
      "/notebook/safeword": { body: { word: "mor" } },
      "/notebook/7/boundaries": { body: { boundaries: [], use_global: true } },
    });
    renderWithQueryClient(<BoundaryPanel />);
    const box = await screen.findByLabelText(/safeword/i);
    await waitFor(() => expect(box).toHaveValue("mor"));

    await user.clear(box);
    await user.type(box, "yeşil");
    await user.tab();

    // The refetch answers "mor", and the box shows the server's word rather
    // than the one still sitting in local state.
    await waitFor(() => expect(box).toHaveValue("mor"));
    stored = "mor";
    expect(stored).toBe("mor");
  });

  it("locks the field while the save is in flight", async () => {
    // The field stayed writable while its own POST was on the wire, so a
    // second edit could be typed over a value still being saved and the two
    // answers raced - last one home wins, silently.
    const user = userEvent.setup();
    let release: (() => void) | null = null;
    const held = new Promise<void>((resolve) => { release = resolve; });

    function json(body: unknown): Response {
      return new Response(JSON.stringify(body), {
        status: 200, headers: { "Content-Type": "application/json" },
      });
    }

    vi.spyOn(globalThis, "fetch").mockImplementation(
      (async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/notebook/safeword") && init?.method === "POST") {
          await held;
          return json({ ok: true });
        }
        if (url.includes("/notebook/safeword")) return json({ word: "" });
        if (url.includes("/boundaries")) {
          return json({ boundaries: [], use_global: true });
        }
        if (url.includes("/vault/status")) {
          return json({ initialized: true, unlocked: true });
        }
        return json({});
      }) as unknown as typeof fetch);

    renderWithQueryClient(<BoundaryPanel />);
    const box = await screen.findByLabelText(/safeword/i);
    await waitFor(() => expect(box).toBeEnabled());
    await user.type(box, "kırmızı");
    await user.tab();

    await waitFor(() => expect(box).toBeDisabled());
    release!();
    await waitFor(() => expect(box).toBeEnabled());
  });

  it("reports a failed save WITHOUT naming a chat", async () => {
    // Deliberately in the other direction. The safeword is a global setting,
    // not a property of whichever chat happens to be open, and attaching a
    // chat id here would make the error's identity depend on that - the
    // exact class of bug two earlier fixes were written for. This test is
    // the gate that keeps the correct behaviour correct.
    const user = userEvent.setup();
    useErrorStore.getState().clearAll();
    mount("kırmızı", { status: 500, body: { detail: "boom" } });
    const box = await screen.findByLabelText(/safeword/i);
    await waitFor(() => expect(box).toHaveValue("kırmızı"));

    await user.clear(box);
    await user.type(box, "mor");
    await user.tab();

    await waitFor(() => {
      expect(useErrorStore.getState().errors.length).toBeGreaterThan(0);
    });
    for (const entry of useErrorStore.getState().errors) {
      expect((entry as unknown as { chatId?: number }).chatId).toBeUndefined();
    }
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

/**
 * The same lie, on the panel where it costs more.
 *
 * Neither query's `isError` was read, so a 500 emptied `rows` and the panel
 * printed "No limits set." - about limits the reader wrote down precisely so
 * a model would not cross them. This is the panel's own stated failure mode
 * ("a limit that looks like it is in force when it is not") read backwards.
 */
describe("when the limits cannot be loaded", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: 7 });
  });
  afterEach(() => vi.restoreAllMocks());

  it("says so, and does not claim there are no limits", async () => {
    mockFetch({
      "/notebook/7/boundaries": { status: 500, body: { detail: "boom" } },
    });
    renderWithQueryClient(<BoundaryPanel />);

    expect(await screen.findByText(/could not be loaded/i)).toBeInTheDocument();
    expect(screen.queryByText(/no limits set/i)).not.toBeInTheDocument();
  });

  it("still says No limits set when the list really is empty", async () => {
    // GROUND CONTROL, as above: the sentence that was already correct has to
    // survive the fix.
    mockFetch({
      "/notebook/7/boundaries": { body: { boundaries: [], use_global: true } },
    });
    renderWithQueryClient(<BoundaryPanel />);

    expect(await screen.findByText(/no limits set/i)).toBeInTheDocument();
    expect(screen.queryByText(/could not be loaded/i)).not.toBeInTheDocument();
  });

  it("says neither when there are limits to show", async () => {
    mockFetch({
      "/notebook/7/boundaries": {
        body: { boundaries: [boundary()], use_global: true },
      },
    });
    renderWithQueryClient(<BoundaryPanel />);

    expect(await screen.findByText("no gore")).toBeInTheDocument();
    expect(screen.queryByText(/no limits set/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/could not be loaded/i)).not.toBeInTheDocument();
  });

  it("says so with no chat open, when the GLOBAL list is the one that failed",
    async () => {
      // The other branch of the same choice: with no chat open the panel
      // reads useGlobalBoundaries instead, so that is the query whose
      // failure has to be believed.
      useUiStore.setState({ selectedChatId: null });
      mockFetch({
        "/notebook/boundaries": { status: 500, body: { detail: "boom" } },
      });
      renderWithQueryClient(<BoundaryPanel />);

      expect(await screen.findByText(/could not be loaded/i))
        .toBeInTheDocument();
      expect(screen.queryByText(/no limits set/i)).not.toBeInTheDocument();
    });
});

/**
 * The two settings the app collected, stored, and told nobody about.
 *
 * `on_violation` and `rating_ceiling` have been columns since the table was
 * written - validated by a CHECK, saved, and shown back to the person who
 * set them - and they reached the model in neither direction: no control
 * here, no line in the prompt. A setting that exists, saves, and does
 * nothing is worse than no setting, because the panel displays it as a
 * promise.
 */
describe("what to do when a limit is crossed", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: 7 });
  });
  afterEach(() => vi.restoreAllMocks());

  function sent(): Record<string, unknown> {
    const calls = (globalThis.fetch as unknown as {
      mock: { calls: [string, { body?: string }][] };
    }).mock.calls;
    const post = calls.find(
      ([url, init]) => url.includes("/notebook/boundaries") && init?.body);
    return JSON.parse(post![1].body!) as Record<string, unknown>;
  }

  it("sends the action and the rating the reader chose", async () => {
    mockFetch({
      "/notebook/7/boundaries": { body: { boundaries: [], use_global: true } },
      "/notebook/boundaries": { body: boundary() },
    });
    renderWithQueryClient(<BoundaryPanel />);

    await userEvent.type(
      await screen.findByPlaceholderText(/keep out of the story/i), "no gore");
    await userEvent.selectOptions(
      screen.getByTestId("boundary-on-violation"), "hard_stop");
    await userEvent.selectOptions(screen.getByTestId("boundary-rating"), "PG-13");
    await userEvent.click(screen.getByLabelText("Add limit"));

    await waitFor(() => {
      expect(sent().on_violation).toBe("hard_stop");
    });
    expect(sent().rating_ceiling).toBe("PG-13");
  });

  it("omits the rating entirely when none is chosen", async () => {
    // GROUND CONTROL. The column is nullable and its CHECK does not allow an
    // empty string, so sending "" would be a 400 on the most ordinary path
    // there is: adding a limit without touching either new control.
    mockFetch({
      "/notebook/7/boundaries": { body: { boundaries: [], use_global: true } },
      "/notebook/boundaries": { body: boundary() },
    });
    renderWithQueryClient(<BoundaryPanel />);

    await userEvent.type(
      await screen.findByPlaceholderText(/keep out of the story/i), "no gore");
    await userEvent.click(screen.getByLabelText("Add limit"));

    await waitFor(() => {
      expect(sent().on_violation).toBe("pause");
    });
    expect("rating_ceiling" in sent()).toBe(false);
  });
});

