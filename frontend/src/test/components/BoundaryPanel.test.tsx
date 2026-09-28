/**
 * FAZ 3 - the limits panel.
 *
 * The thing this screen must never do is make a limit look like it is in force
 * when it is not. Two states carry that risk: a chat told to ignore the global
 * set, and the moment the global toggle fails to save. Both are tested here,
 * because both look like a working limit list otherwise.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { hiddenFromReaders } from "@/test/helpers/accessibility";
import {
  chooseOption,
  expectClosed,
  expectKeyboardContract,
  openPopup,
  openPositioner,
  openSelect,
} from "@/test/helpers/select";
import { loadGlassRightCss } from "@/test/helpers/glassSurfaceCss";
import { parseSheet, resolvedSelectors } from "@/test/helpers/compiledSheet";
import {
  BoundaryPanel,
  BOUNDARY_MAX_CHARS,
} from "@/components/notebook/BoundaryPanel";
import {
  BOUNDARY_ON_VIOLATION_ITEMS,
  BOUNDARY_RATING_ITEMS,
  BOUNDARY_SCOPE_ITEMS,
  BOUNDARY_SELECT_LABELS,
  BOUNDARY_SEVERITY_ITEMS,
} from "@/lib/notebook/labels";
import { ExtractionSettings } from "@/components/notebook/ExtractionSettings";
import {
  BASE_UI_COLLISION_PADDING,
  POPUP_COLLISION_PADDING,
  POPUP_SIDE_OFFSET,
  popupCollisionProps,
} from "@/lib/theme/popupPositioning";
import { tokens, TYPE_SCALE_STEPS } from "@/lib/theme/tokens";
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
    await waitFor(() =>
      // The line is mounted closed before it opens (Collapse), so
      // "says so" is asked of the accessibility tree.
      expect(hiddenFromReaders(screen.getByText(/no limits set/i))).toBe(false));
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
    expect(hiddenFromReaders(screen.queryByText(/no limits set/i))).toBe(true);
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
    // The assertion above IS the claim. What stood here was a local assigned
    // "mor" on one line and asserted to be "mor" on the next, inside the test
    // labelled GROUND CONTROL - a line that cannot go red under any change to
    // any file in this repo - together with a `globalThis.__unused` that was
    // never cleaned up and outlived every later test in the worker.
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
    //
    // `delay: null` on the session, and it is not a style choice. userEvent
    // waits a frame between keystrokes by default, and 200 of those cross
    // vitest's 5000 ms budget on a loaded machine - this file is where the
    // suite's Q-50 timeouts land. What is being measured is the field's cap,
    // not the typing speed.
    const user = userEvent.setup({ delay: null });
    mountEmpty();
    const box = await screen.findByPlaceholderText(/keep out of the story/i);
    await user.type(box, "x".repeat(BOUNDARY_MAX_CHARS + 40));
    expect((box as HTMLInputElement).value).toHaveLength(BOUNDARY_MAX_CHARS);
  });

  it("still sends a limit written right up to the cap, whole", async () => {
    // The positive control, and the ground for the test above: a field that
    // truncated everything would pass that one too. A limit at the cap has to
    // arrive at the server intact, character for character.
    // `delay: null` for the same reason as the case above: 160 keystrokes.
    const user = userEvent.setup({ delay: null });
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

    await waitFor(() =>
      // The line is mounted closed before it opens (Collapse), so
      // "says so" is asked of the accessibility tree.
      expect(hiddenFromReaders(screen.getByText(/could not be loaded/i))).toBe(false));
    expect(hiddenFromReaders(screen.queryByText(/no limits set/i))).toBe(true);
  });

  it("still says No limits set when the list really is empty", async () => {
    // GROUND CONTROL, as above: the sentence that was already correct has to
    // survive the fix.
    mockFetch({
      "/notebook/7/boundaries": { body: { boundaries: [], use_global: true } },
    });
    renderWithQueryClient(<BoundaryPanel />);

    await waitFor(() =>
      // The line is mounted closed before it opens (Collapse), so
      // "says so" is asked of the accessibility tree.
      expect(hiddenFromReaders(screen.getByText(/no limits set/i))).toBe(false));
    expect(hiddenFromReaders(screen.queryByText(/could not be loaded/i))).toBe(true);
  });

  it("says neither when there are limits to show", async () => {
    mockFetch({
      "/notebook/7/boundaries": {
        body: { boundaries: [boundary()], use_global: true },
      },
    });
    renderWithQueryClient(<BoundaryPanel />);

    expect(await screen.findByText("no gore")).toBeInTheDocument();
    expect(hiddenFromReaders(screen.queryByText(/no limits set/i))).toBe(true);
    expect(hiddenFromReaders(screen.queryByText(/could not be loaded/i))).toBe(true);
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

      await waitFor(() =>
      // The line is mounted closed before it opens (Collapse), so
      // "says so" is asked of the accessibility tree.
      expect(hiddenFromReaders(screen.getByText(/could not be loaded/i))).toBe(false));
      expect(hiddenFromReaders(screen.queryByText(/no limits set/i))).toBe(true);
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
    // Found by the words that name them, not by a test id. A `data-testid`
    // prop on LimitQualifier would be test scaffolding in the public
    // interface of a production component, and the visible labels reach the
    // same two triggers - which is the whole point of the labels.
    await chooseOption(
      userEvent,
      screen.getByLabelText(BOUNDARY_SELECT_LABELS.onViolation),
      "stop the scene");
    await chooseOption(
      userEvent, screen.getByLabelText(BOUNDARY_SELECT_LABELS.rating), "PG-13");
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


/**
 * IP-03: the four qualifiers are ui/select.tsx Selects, not native selects.
 *
 * A native select is drawn by the OS: it cannot be styled, centred or
 * animated, and it clips its own longest option at the panel's 300px floor.
 * Every claim below is read off the rendered tree and, for the scrollbar
 * rule, off the compiled sheet; none of it reads source text.
 */
describe("the four qualifiers are one Select (T-18, T-19, T-75, T-76, T-86)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: 7 });
  });
  afterEach(() => vi.restoreAllMocks());

  const LABELS = Object.values(BOUNDARY_SELECT_LABELS);

  function mount() {
    mockFetch({
      "/notebook/7/boundaries": { body: { boundaries: [], use_global: true } },
    });
    renderWithQueryClient(<BoundaryPanel />);
  }

  /** `var(--duration-fast)` as the Tailwind utility spells it, from the token. */
  function durationUtility(): string {
    const m = /^var\((--[\w-]+)\)$/.exec(tokens.duration.fast);
    if (!m) throw new Error(`not a var() field: ${tokens.duration.fast}`);
    return `duration-(${m[1]})`;
  }

  it("the four names are the four names the plan writes down", () => {
    // Every lookup in this file finds its control BY the imported constant,
    // which is what stops the words on screen and the words a test types
    // drifting apart - and it is exactly why none of them can see a rename:
    // both sides of the comparison move together, so `Rating ceiling` could
    // become `Rating cap` with ten test files green. The four strings are the
    // claim, so they are written down here ONCE, the way KURAL 4.3's four
    // dialog widths are written down in css-contract.test.ts, and every
    // lookup goes on importing.
    //
    // IP-03 FIX 6 names them: "dort yer ve dizeler bugunku `aria-label`
    // metinleridir: ... `Where this applies`, `How strict`, `What to do if it
    // happens`, `Rating ceiling`". They are the accessible names the four
    // native selects already carried, so a rename here renames a control
    // somebody has already learned. T-76's own sentence asks that the four be
    // imported rather than typed; that binds the LOOKUPS, which is where
    // drift does the damage, and this single assertion is the anchor they are
    // measured against.
    expect(BOUNDARY_SELECT_LABELS).toEqual({
      scope: "Where this applies",
      severity: "How strict",
      onViolation: "What to do if it happens",
      rating: "Rating ceiling",
    });
  });

  it("T-18: every qualifier keeps the whole keyboard contract", async () => {
    // All five clauses, on all four call sites: opened from the trigger's own
    // key path, walked, closed on Escape with focus handed back, and COMMITTED
    // with Enter. The shared gesture is in test/helpers/select.ts so the eight
    // dropdowns across three files are held to one contract rather than to
    // four different readings of it.
    const user = userEvent.setup();
    mount();
    let typeaheadAsked = 0;
    for (const label of LABELS) {
      const trigger = await screen.findByLabelText(label);
      expect(trigger).toHaveAttribute("role", "combobox");
      if (await expectKeyboardContract(user, trigger, label)) typeaheadAsked += 1;
    }
    // The type-ahead clause skips a dropdown whose options share their first
    // letters, so it has to be shown reaching at least one of these four -
    // otherwise a clause that silently opted out everywhere would read as a
    // clause that passed everywhere.
    expect(typeaheadAsked, "no qualifier was asked for type-ahead at all")
      .toBeGreaterThan(0);
  }, 60_000);

  it("T-18: Enter selects the option the keyboard is on", async () => {
    const user = userEvent.setup();
    mount();
    const strict = await screen.findByLabelText(BOUNDARY_SELECT_LABELS.severity);
    expect(strict).toHaveTextContent("never");
    const listbox = await openSelect(user, strict);
    // One key at a time, each waited for: the list takes focus a tick after
    // it opens, and a key sent before that lands nowhere.
    const highlighted = (name: string) =>
      waitFor(() =>
        expect(document.activeElement).toBe(
          within(listbox).getByRole("option", { name }),
        ),
      );
    await highlighted("never");
    await user.keyboard("{ArrowDown}");
    await highlighted("off the page");
    await user.keyboard("{ArrowDown}");
    await highlighted("prefer not");
    await user.keyboard("{Enter}");
    await expectClosed(listbox);
    expect(strict).toHaveTextContent("prefer not");
    expect(document.activeElement).toBe(strict);
  });

  it("T-19: the popup is sized by its longest option, and no option is clipped", async () => {
    // A class contract, and named as one: jsdom lays nothing out, so the
    // width itself is Playwright's to measure (KARAR-S). What the tree CAN
    // show is which contract the popup renders under.
    const user = userEvent.setup();
    mount();
    await openSelect(user, await screen.findByLabelText(BOUNDARY_SELECT_LABELS.onViolation));
    const popup = openPopup();
    expect(popup, "no popup rendered").not.toBeNull();
    const classes = Array.from(popup!.classList);
    expect(classes, "the class list is empty; nothing to hold to").not.toHaveLength(0);
    expect(classes).toContain("min-w-(--anchor-width)");
    expect(classes).toContain("w-auto");
    // ASSEMBLED. `classes` is read off the rendered node, so a rule in the
    // sheet cannot make this assertion lie - the reason is the other way
    // round: spelling the utility here puts it in the compiled sheet that
    // ships, which is a test adding a class to the product.
    expect(classes).not.toContain("w-" + "(--anchor-width)");
    expect(classes.some((c) => c.startsWith("min-w-") && /^\d/.test(c.slice(6))))
      .toBe(false);
    // The other half of KURAL 7.5, and nothing held it: the ceiling that
    // keeps a popup wider than its trigger on screen could be deleted with
    // the whole suite green. Written out rather than assembled because this
    // is a PRESENCE, so the utility is emitted by the component either way.
    expect(classes, "the popup lost its width ceiling")
      .toContain("max-w-[min(20rem,calc(100vw-2rem))]");
    // And the popup can still scroll. A long option list with no overflow is
    // a list whose last option cannot be reached; the SCROLLBAR's appearance
    // is the scrollbar rule's; the overflow itself is the popup's own.
    expect(classes, "a long option list can no longer scroll")
      .toContain("overflow-y-auto");
    // ASSEMBLED, and here the reason is real. These are read off classList,
    // so a rule in the sheet cannot make the assertion lie - but Tailwind
    // scans this file as raw text, so the clipping utility named on the
    // next line was emitted into the SHIPPED stylesheet purely because a
    // line here spelled it whole: a test writing a utility into the product
    // it forbids the product to use. The name is broken in the prose too,
    // because the scanner reads a comment exactly as it reads code.
    const clipping = ["truncate", "text-" + "ellipsis", "overflow-" + "hidden"];
    for (const option of screen.getAllByRole("option")) {
      const own = Array.from(option.classList);
      for (const banned of clipping) {
        expect(own, `${option.textContent} carries ${banned}`).not.toContain(banned);
      }
    }
    // The longest option is present in full.
    expect(screen.getByRole("option", { name: "go back and take it another way" }))
      .toBeInTheDocument();
    // And every option is on the primitive's own step. Deleting it from
    // SelectItem left the options rendering at whatever they inherited, with
    // nothing saying so. The step is derived from the scale, never typed.
    const optionStep = `text-${TYPE_SCALE_STEPS[3]}`;
    for (const option of screen.getAllByRole("option")) {
      expect([...option.classList], `${option.textContent} is off the option step`)
        .toContain(optionStep);
    }
  });

  it("T-19: the sheet reaches the popup slot at all, which is what the scrollbar rule will need", async () => {
    // The eight popups portal to the body, outside .elysium-page, so no rule
    // scoped to the page can reach them by descent: a rule that wants them
    // has to name the slot. What is asked here is that the slot EXISTS as
    // something the sheet addresses and that the popup which renders is that
    // same node - which is the precondition every rule aimed at these
    // popups depends on, the scrollbar rule included. The scrollbar's own
    // declarations are asserted in css-contract.test.ts, beside the rule
    // that writes them.
    const root = parseSheet(await loadGlassRightCss());
    const props = new Set<string>();
    root.walkRules((rule) => {
      if (!resolvedSelectors(rule).includes('[data-slot="select-content"]')) return;
      rule.walkDecls((d) => {
        props.add(d.prop);
      });
    });
    expect(props.size, "no rule in the sheet names the popup slot").toBeGreaterThan(0);
    // GROUND: the slot is addressed by the sheet AND by the popup that
    // renders, so the two really are the same node and not two spellings.
    const user = userEvent.setup();
    mount();
    await openSelect(user, await screen.findByLabelText(BOUNDARY_SELECT_LABELS.scope));
    expect(openPopup(), "nothing renders the slot the sheet names").not.toBeNull();
  }, 60_000);

  it("T-75: the shared padding is the one the reason asks for", () => {
    // The number is imported everywhere, which is right and also means no
    // test pins it: changing 8 to 5 would leave every assertion green while
    // undoing the fix. So it is held to its own written derivation instead -
    // twice the popup's step away from its trigger, and more than the
    // library default that was not enough inside a 300px panel.
    expect(POPUP_COLLISION_PADDING).toBe(POPUP_SIDE_OFFSET * 2);
    expect(POPUP_COLLISION_PADDING).toBeGreaterThan(BASE_UI_COLLISION_PADDING);
    // And the derivation is anchored at one end. Held only to a relation,
    // the two numbers move together: 8 and 4 become 10 and 5 with every
    // assertion above still green, and REF KURAL 8.4 writes the padding as
    // binding. The side offset is the design constant, so it is the one that
    // is pinned, and the padding follows it.
    expect(POPUP_SIDE_OFFSET, "the popup's step away from its trigger moved")
      .toBe(4);
    expect(POPUP_COLLISION_PADDING, "REF 8.4 writes this value as binding")
      .toBe(8);
  });

  it("T-75: the padding and the attribute that proves it travel together", () => {
    // The attribute a test reads and the prop the positioner acts on come
    // from one call, so the prop cannot be deleted while the attribute goes
    // on reporting it - which is what made the assertion below a tautology
    // when they were two JSX attributes written from the same local.
    const props = popupCollisionProps(POPUP_COLLISION_PADDING);
    expect(props.collisionPadding).toBe(POPUP_COLLISION_PADDING);
    expect(props["data-collision-padding"]).toBe(POPUP_COLLISION_PADDING);
    expect(Object.keys(props).sort())
      .toEqual(["collisionPadding", "data-collision-padding"]);
  });

  it("T-75: every popup renders with the shared collision padding", async () => {
    const user = userEvent.setup();
    mount();
    for (const label of LABELS) {
      const trigger = await screen.findByLabelText(label);
      const listbox = await openSelect(user, trigger);
      expect(openPositioner()!.getAttribute("data-collision-padding"), label)
        .toBe(String(POPUP_COLLISION_PADDING));
      await user.keyboard("{Escape}");
      await expectClosed(listbox);
    }
  });

  it("each qualifier offers exactly the options it is given, and shows the one it holds", async () => {
    // The gap four separate mutations walked through. Deleting `R` from the
    // rating ceiling, or the disabled condition from the scope qualifier,
    // left every assertion in this file green: the tests drove whatever the
    // page happened to render, so a lost option read as a shorter list and a
    // lost guard as nothing at all. One row per call site now, with the
    // option set IMPORTED from the module the component reads.
    // The STORED values, written out. The maps are imported for the render
    // half below, and an imported map cannot catch an option deleted from the
    // map itself: both sides of the comparison move together. These four keys
    // are the wire contract with create_boundary, so they are the claim, and
    // a claim is written down rather than read back off the thing it judges.
    expect(Object.keys(BOUNDARY_SCOPE_ITEMS)).toEqual(["global", "chat"]);
    expect(Object.keys(BOUNDARY_SEVERITY_ITEMS))
      .toEqual(["hard", "veiled", "soft"]);
    expect(Object.keys(BOUNDARY_ON_VIOLATION_ITEMS))
      .toEqual(["pause", "rewind", "fast_forward", "hard_stop"]);
    expect(Object.keys(BOUNDARY_RATING_ITEMS))
      .toEqual(["", "G", "PG", "PG-13", "R"]);

    const user = userEvent.setup();
    mount();
    const contracts = [
      [BOUNDARY_SELECT_LABELS.scope, BOUNDARY_SCOPE_ITEMS, "global"],
      [BOUNDARY_SELECT_LABELS.severity, BOUNDARY_SEVERITY_ITEMS, "hard"],
      [BOUNDARY_SELECT_LABELS.onViolation, BOUNDARY_ON_VIOLATION_ITEMS, "pause"],
      [BOUNDARY_SELECT_LABELS.rating, BOUNDARY_RATING_ITEMS, ""],
    ] as const;
    for (const [label, items, initial] of contracts) {
      const trigger = await screen.findByLabelText(label);
      // The value it holds on arrival, shown as words rather than as a code.
      expect(trigger, `${label} does not show the value it holds`)
        .toHaveTextContent(items[initial as keyof typeof items]);
      const listbox = await openSelect(user, trigger);
      const shown = within(listbox).getAllByRole("option")
        .map((o) => (o.textContent ?? "").trim());
      expect(shown, `${label} does not offer the options it was given`)
        .toEqual(Object.values(items));
      await user.keyboard("{Escape}");
      await expectClosed(listbox);
    }
  }, 60_000);

  it("refuses to scope a limit to a chat when no chat is open", async () => {
    // The scope qualifier is the one with a condition of its own, and the
    // condition is load-bearing: "this chat" with no chat open would write a
    // row nothing could ever match. Dropping `chatId == null` from its
    // `disabled` left the suite green.
    useUiStore.setState({ selectedChatId: null });
    mockFetch({ "/notebook/boundaries": { body: { boundaries: [] } } });
    renderWithQueryClient(<BoundaryPanel />);
    const scope = await screen.findByLabelText(BOUNDARY_SELECT_LABELS.scope);
    expect(scope, "a limit can be scoped to a chat that is not open")
      .toBeDisabled();
    // GROUND: the other three are NOT disabled in the same render, so this is
    // the scope qualifier's own condition and not a panel that is inert.
    for (const label of [BOUNDARY_SELECT_LABELS.severity,
                         BOUNDARY_SELECT_LABELS.onViolation,
                         BOUNDARY_SELECT_LABELS.rating]) {
      expect(await screen.findByLabelText(label), `${label} is disabled too`)
        .toBeEnabled();
    }
  }, 30_000);

  it("T-76: each qualifier is named by a visible label and carries no aria-label", async () => {
    mount();
    for (const label of LABELS) {
      const trigger = await screen.findByLabelText(label);
      expect(trigger).toHaveAttribute("role", "combobox");
      expect(trigger, `${label} still carries an aria-label`).not.toHaveAttribute("aria-label");
      // The visible words are on screen, not only in an attribute.
      expect(screen.getByText(label)).toBeInTheDocument();
      // And both are on the chrome step: the label names a control and the
      // trigger IS one, so the role rule puts them at 13 rather than at the
      // 12 the nearest-step rule would have given. Derived from the scale,
      // not typed, so renaming a step moves the assertion with it.
      const chrome = `text-${TYPE_SCALE_STEPS[2]}`;
      expect([...trigger.classList], `${label}'s trigger is off the chrome step`)
        .toContain(chrome);
      expect([...screen.getByText(label).classList], `${label} is off the chrome step`)
        .toContain(chrome);
    }
  });

  it("T-76 GROUND: the same query finds a fifth control through its visible label", async () => {
    // A fifth control, in another component, found by the same query: if
    // getByLabelText could not reach a Select through its words at all, the
    // four assertions above would be proving nothing. The label text is
    // imported rather than typed, and it is the accessible name AND the
    // words on screen - which is the point of the whole fix, so the ground
    // control asserts both of those about it too.
    mockFetch({
      "/notebook/extract/models": { body: { models: [] } },
      "/notebook/extract/settings": { body: { model_id: null, prompt_language: "en" } },
    });
    renderWithQueryClient(<ExtractionSettings />);
    const trigger = await screen.findByLabelText("Model");
    expect(trigger).toHaveAttribute("role", "combobox");
    expect(trigger).not.toHaveAttribute("aria-label");
    expect(screen.getByText("Model")).toBeInTheDocument();
  });

  it("T-76 POSITIVE CONTROL: the matcher really can see an aria-label", async () => {
    // `not.toHaveAttribute("aria-label")` proves nothing unless the same
    // matcher can be made to report one. A node that carries the attribute
    // is put in front of it, so the four absences above are readings.
    mount();
    const probe = document.createElement("button");
    probe.setAttribute("aria-label", "a name nothing on screen says");
    document.body.appendChild(probe);
    expect(probe).toHaveAttribute("aria-label");
    probe.remove();
    // And the query used above would find THIS one by that hidden name,
    // which is exactly the drift the visible-label rule exists to stop.
    const trigger = await screen.findByLabelText(BOUNDARY_SELECT_LABELS.scope);
    expect(trigger.getAttribute("aria-labelledby"), "named by something other than the words")
      .not.toBeNull();
  });

  it("T-86: the opened popup animates, and its duration is the token", async () => {
    const user = userEvent.setup();
    mount();
    await openSelect(user, await screen.findByLabelText(BOUNDARY_SELECT_LABELS.scope));
    const popup = openPopup()!;
    const classes = Array.from(popup.classList);
    // GROUND: the reader sees the class list and the attribute at all.
    expect(classes).toContain("data-open:animate-in");
    expect(popup.hasAttribute("data-align-trigger")).toBe(true);
    // Alignment mode is off by default, so the entry animation is not
    // switched off by the data-[align-trigger=true] rule.
    expect(popup.getAttribute("data-align-trigger")).not.toBe("true");
    expect(classes).toContain(durationUtility());
    expect(classes.filter((c) => /^duration-\d/.test(c)), "a numeric duration class is back")
      .toEqual([]);
  });
});
