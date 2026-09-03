/**
 * RightPanelDrafts.test.tsx - the right panel used to eat what you typed in it.
 *
 * The four tabs swap by unmounting the open one, so every box in the panel
 * held its contents in component state that a single click on a neighbouring
 * tab destroyed. No warning, no undo, and nothing on screen suggesting the
 * click was destructive. The panels still unmount - that is deliberate, and
 * the model list's cascade depends on it - so the fix moved the text out of
 * the components and into the same module-scope store that already holds
 * unsent composer and edit text.
 *
 * Every assertion here is driven through the real panel: a real tab click, a
 * real keystroke, a real DOM read. Nothing reads the source of the component
 * it is testing, and the keys the boxes are stored under are IMPORTED rather
 * than retyped, so a rename cannot leave a test passing against a key that no
 * longer exists.
 *
 * The controls this file carries, because an assertion about text coming back
 * is worthless without them:
 *  - GROUND CONTROL: text typed with no tab switch at all is in the box.
 *    Proves the harness can actually type into these controls.
 *  - POSITIVE CONTROL for the survival claims: the vault-wipe path empties
 *    every one of these boxes. An assertion that can only ever be green is
 *    not measuring anything.
 *  - POSITIVE CONTROL for the credential exclusion: the proxy label DOES
 *    reach the store, which is what makes "the API key does not" a fact about
 *    the API key rather than about a scan that sees nothing anywhere.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";

import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { RightPanel } from "@/components/layout/RightPanel";
import { GenerationSettingsProvider } from "@/components/generation/GenerationSettingsContext";
import { mockFetch } from "../mocks/api";
import { settingsFixture, modelListFixture } from "../mocks/fixtures";
import { useUiStore } from "@/lib/store/uiStore";
import { useErrorStore } from "@/lib/errors";
import { useSeenNotesStore } from "@/lib/chat/seenNotes";
import { useContextNotesStore } from "@/lib/chat/contextNotes";
import {
  MAX_DRAFT_BYTES,
  PANEL_DRAFT_KEYS,
  chatScopedPanelDraftKey,
  draftByteSize,
  personaEditDraftKey,
  useDraftStore,
} from "@/lib/store/draftStore";

const CHAT_ID = 7;
/** The conversation the chat-scope cases walk to, and the one `9` below. */
const OTHER_CHAT_ID = 9;

/** The note box's key for the chat these cases open. */
const NOTE_KEY = chatScopedPanelDraftKey(PANEL_DRAFT_KEYS.newNote, CHAT_ID);

/**
 * Two personas, not one.
 *
 * "The form comes back on the row it belongs to" has exactly one possible
 * answer when there is one row, so a single-row fixture cannot fail that
 * claim however wrong the code is. Two rows also make the two-pencils path
 * reachable: Edit A, type, Edit B without cancelling.
 */
const PERSONA = {
  id: 4,
  display_name: "Kestrel",
  description: "Speaks in short sentences.",
  is_active: false,
  created_at: "2026-01-01T00:00:00",
  updated_at: "2026-01-01T00:00:00",
};

const OTHER_PERSONA = {
  id: 11,
  display_name: "Marlow",
  description: "Asks a lot of questions.",
  is_active: false,
  created_at: "2026-01-02T00:00:00",
  updated_at: "2026-01-02T00:00:00",
};

/** What the server hands back when the create form is submitted. */
const CREATED_PERSONA = {
  id: 21,
  display_name: "Wren",
  description: "Prefers being asked first.",
  is_active: false,
  created_at: "2026-01-03T00:00:00",
  updated_at: "2026-01-03T00:00:00",
};

/** The worker card's status, shaped as its schema requires. */
const workerStatusFixture = {
  stats: { done: 0, failed: 0, skipped: 0, skip_reasons: {} },
  spend: { calls: 0, tokens_in: 0, tokens_out: 0, cost: 0 },
  spend_lifetime: { calls: 0, tokens_in: 0, tokens_out: 0, cost: 0 },
  worker: {
    state: "closed",
    failures: 0,
    total_failures: 0,
    queued: 0,
    dropped_offers: 0,
    runs: 0,
    batch_size: 20,
  },
  daily_cap: 60,
};

/** One accepted note, shaped as the entry schema requires. */
const noteFixture = {
  id: 1,
  chat_id: CHAT_ID,
  position: 0,
  kind: "fact",
  text: "Her mother's name is never spoken.",
  evidence: null,
  durability: "permanent",
  importance: 2,
  pinned: 0,
  retired_at: null,
  superseded_by: null,
  excluded_reason: null,
  status: "accepted",
  provenance: "user",
  source_message_id: null,
  created_at: "2026-08-19",
  updated_at: "2026-08-19",
};

function wrapper({ children }: { children: ReactNode }) {
  // Mirrors production: AppShell provides this, and ModelPanel reads it.
  return <GenerationSettingsProvider>{children}</GenerationSettingsProvider>;
}

/**
 * Every route the four tabs reach for, answered with the emptiest valid
 * payload each schema accepts.
 *
 * Order matters: this mock matches by URL SUBSTRING in insertion order, so
 * `/notebook/7/boundaries` has to be listed before `/notebook/7` or the
 * boundary list would be served a notebook.
 *
 * Split from `mockPanelApi` so a case that needs ONE route to answer
 * differently can override that route and keep every other one answered.
 * Spreading a short hand-written set instead leaves the rest on 404, and a
 * 404 anywhere in this panel pushes an error the same shape as the one such a
 * case is usually waiting for.
 */
function panelRoutes(): Record<string, { status?: number; body: unknown }> {
  return {
    // Method-prefixed writes first, then the LONGEST url patterns, because
    // this mock matches by substring in insertion order: a bare "/models"
    // above "/notebook/extract/models" answers the extraction query with the
    // model catalogue, and the schema rejection that follows is invisible -
    // the panel just renders an error state and no assertion notices.
    "POST /settings/proxy/alias": { body: { ok: true } },
    [`POST /notebook/${CHAT_ID}`]: { body: noteFixture },
    // The row a successful create comes back as. Method-prefixed so it cannot
    // shadow the GET below, which answers with the LIST - a create served an
    // array fails schema validation and the save looks like a server error.
    "POST /personas": { body: CREATED_PERSONA },
    "/notebook/extract/models": { body: { models: [] } },
    "/notebook/extract/settings": { body: { model_id: null, prompt_language: "en" } },
    "/notebook/worker": { body: workerStatusFixture },
    "/notebook/safeword": { body: { word: "" } },
    "/notebook/auto-accept": {
      body: { enabled: true, effective: true, overridden: false },
    },
    [`/notebook/${CHAT_ID}/boundaries`]: {
      body: { boundaries: [], use_global: true },
    },
    // The second conversation the chat-scope cases move to. Listed here, and
    // ahead of the shorter patterns below it, because a 404 leaves the
    // notebook and limit panels rendering an error instead of their boxes -
    // and a case that cannot find its box reads as a fixture problem rather
    // than as the scope claim it was written to make.
    [`/notebook/${OTHER_CHAT_ID}/boundaries`]: {
      body: { boundaries: [], use_global: true },
    },
    "/notebook/boundaries": { body: { boundaries: [], use_global: true } },
    [`/notebook/${CHAT_ID}`]: { body: { entries: [], notebook_chars: 0 } },
    [`/notebook/${OTHER_CHAT_ID}`]: { body: { entries: [], notebook_chars: 0 } },
    "/notebook/": { body: { entries: [], notebook_chars: 0 } },
    "/models": { body: modelListFixture },
    "/settings": { body: { ...settingsFixture, proxy_configured: true } },
    "/proxy/health": {
      body: { healthy: true, latency_ms: 12, reason: null, cached: false },
    },
    "/personas": { body: [PERSONA, OTHER_PERSONA] },
    // The Models tab's context gauge asks for these four. They answered 404
    // until `assertNoUnansweredRoutes` below said so out loud.
    "/tts/voice-mode": { body: { enabled: false, active: false, prompt_chars: 0 } },
    [`/chats/${CHAT_ID}/messages`]: { body: [] },
    [`/chats/${OTHER_CHAT_ID}/messages`]: { body: [] },
    "/chats": { body: [] },
    "/characters": { body: [] },
  };
}

/** Every panel route installed as the global fetch. */
function mockPanelApi(
  overrides: Record<string, { status?: number; body: unknown }> = {},
) {
  const base = panelRoutes();
  // Overrides go FIRST, because the mock matches in insertion order, and the
  // key they replace is deleted from the base rather than shadowed: object
  // spread keeps the first POSITION but the last VALUE, so leaving it in
  // would quietly hand the default back while the override looked applied.
  for (const key of Object.keys(overrides)) delete base[key];
  return mockFetch({ ...overrides, ...base });
}

/**
 * Every request the panels made was actually answered by the mock.
 *
 * Unmatched routes come back 404, the panel that asked renders an error
 * state, and every survival assertion in this file goes on passing while
 * half the Notes tab is broken. This is the check that would have caught a
 * shorter url pattern shadowing a longer one above it.
 */
async function assertNoUnansweredRoutes(
  fetchMock: ReturnType<typeof mockPanelApi>,
): Promise<void> {
  const responses = await Promise.all(
    fetchMock.mock.results.map((result) => result.value as Promise<Response>),
  );
  expect(
    responses.length,
    "the panels made no requests at all, so this check is vacuous",
  ).toBeGreaterThan(0);
  const missed = responses
    .map((response, index) => ({
      status: response.status,
      url: String(fetchMock.mock.calls[index][0]),
    }))
    .filter((call) => call.status === 404);
  expect(missed, "a panel route was not answered by the mock").toEqual([]);
}

/** The Edit button of one row, since every row has one. */
function editButtonFor(personaId: number): HTMLElement {
  return within(screen.getByTestId(`persona-card-${personaId}`)).getByRole(
    "button",
    { name: "Edit" },
  );
}

/**
 * A notebook with more than five notes in two different chats.
 *
 * The search box only renders above five notes, so the filter cases need a
 * chat that actually has them - and a second chat that also has them, or
 * "the filter did not follow me" would be satisfied by a box that is not on
 * screen at all.
 */
function manyNotesRoutes(): Record<string, { body: unknown }> {
  const entries = (chatId: number) =>
    Array.from({ length: 6 }, (_, index) => ({
      ...noteFixture,
      id: chatId * 100 + index,
      chat_id: chatId,
      position: index,
      text: index === 0 ? "her mother is never spoken of" : `note ${index}`,
    }));
  return {
    "/notebook/extract/models": { body: { models: [] } },
    "/notebook/extract/settings": { body: { model_id: null, prompt_language: "en" } },
    "/notebook/worker": { body: workerStatusFixture },
    "/notebook/safeword": { body: { word: "" } },
    "/notebook/auto-accept": {
      body: { enabled: true, effective: true, overridden: false },
    },
    [`/notebook/${CHAT_ID}/boundaries`]: { body: { boundaries: [], use_global: true } },
    "/notebook/9/boundaries": { body: { boundaries: [], use_global: true } },
    "/notebook/boundaries": { body: { boundaries: [], use_global: true } },
    [`/notebook/${CHAT_ID}`]: { body: { entries: entries(CHAT_ID), notebook_chars: 0 } },
    "/notebook/9": { body: { entries: entries(9), notebook_chars: 0 } },
    [`/chats/${CHAT_ID}/messages`]: { body: [] },
    "/chats/9/messages": { body: [] },
  };
}

/** Click a tab by the label a person reads on it. */
async function openTab(
  user: ReturnType<typeof userEvent.setup>,
  name: string,
): Promise<void> {
  await user.click(screen.getByRole("tab", { name }));
}

/**
 * Leave the tab and come back.
 *
 * Models is the neighbour used everywhere except when Models is the tab under
 * test, because it is the one panel whose remount is a deliberate feature.
 */
async function roundTrip(
  user: ReturnType<typeof userEvent.setup>,
  tab: string,
  via: string,
): Promise<void> {
  await openTab(user, via);
  await openTab(user, tab);
}

describe("right panel text across a tab switch", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useDraftStore.getState().clearAll();
    useErrorStore.getState().clearAll();
    // The Notes tab reads both of these, and they are module scope like the
    // draft store: a suite that does not establish its own preconditions is
    // one fixture change away from silent bleed between tests.
    useSeenNotesStore.setState({ byChat: {} });
    useContextNotesStore.setState({ byChat: {} });
    localStorage.clear();
    sessionStorage.clear();
    useUiStore.setState({
      selectedChatId: CHAT_ID,
      selectedCharacterId: null,
      selectedModelId: null,
      activeRightPanelTab: "models",
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  // ── GROUND CONTROL ───────────────────────────────────────────────────────
  it("GROUND CONTROL: text typed with no tab switch is in the box", async () => {
    // Green before the fix as well as after. Its whole job is to prove that
    // when the tests below find an empty box, the emptiness came from the
    // tab switch and not from a keystroke that never landed.
    const user = userEvent.setup();
    mockPanelApi();
    renderWithQueryClient(<RightPanel />, { wrapper });

    const search = await screen.findByRole("textbox", {
      name: "Search models",
    });
    await user.type(search, "sonnet");

    expect(
      screen.getByRole("textbox", { name: "Search models" }),
      "the harness cannot type into this panel at all",
    ).toHaveValue("sonnet");
  });

  // ── One tab per test: the scope proof ────────────────────────────────────
  //
  // Four separate cases rather than one loop, deliberately. A fix applied to
  // one tab and not the others has to show up as three red tests naming three
  // tabs, not as one red test naming a panel.

  it("MODELS: the search box still holds its text", async () => {
    const user = userEvent.setup();
    mockPanelApi();
    renderWithQueryClient(<RightPanel />, { wrapper });

    const search = await screen.findByRole("textbox", {
      name: "Search models",
    });
    await user.type(search, "opus");
    await roundTrip(user, "Models", "Persona");

    expect(
      await screen.findByRole("textbox", { name: "Search models" }),
      "the models search was destroyed by a tab switch",
    ).toHaveValue("opus");
  });

  it("SECURITY: the proxy label still holds its text", async () => {
    const user = userEvent.setup();
    mockPanelApi();
    renderWithQueryClient(<RightPanel />, { wrapper });

    await openTab(user, "Security");
    const alias = await screen.findByLabelText("Proxy alias input");
    await user.type(alias, "work laptop");
    await roundTrip(user, "Security", "Models");

    expect(
      await screen.findByLabelText("Proxy alias input"),
      "the proxy label was destroyed by a tab switch",
    ).toHaveValue("work laptop");
  });

  it("PERSONA: the create form comes back open, with its words", async () => {
    const user = userEvent.setup();
    mockPanelApi();
    renderWithQueryClient(<RightPanel />, { wrapper });

    await openTab(user, "Persona");
    await user.click(await screen.findByRole("button", { name: /New Persona/ }));
    await user.type(
      screen.getByRole("textbox", { name: "Display name" }),
      "Wren",
    );
    await user.type(
      screen.getByRole("textbox", { name: "Description" }),
      "Prefers being asked first.",
    );
    await roundTrip(user, "Persona", "Models");

    // The form itself has to be back, not only its text. Keeping the words in
    // a store while the form returns closed would restore nothing a person
    // can see, and the button that opens it wipes the boxes on the way.
    expect(
      await screen.findByText("Create Persona"),
      "the create form came back closed, so its text is unreachable",
    ).toBeInTheDocument();
    expect(
      screen.getByRole("textbox", { name: "Display name" }),
      "the persona name was destroyed by a tab switch",
    ).toHaveValue("Wren");
    expect(
      screen.getByRole("textbox", { name: "Description" }),
      "the persona description was destroyed by a tab switch",
    ).toHaveValue("Prefers being asked first.");
  });

  it("NOTES: the new-note box still holds its text", async () => {
    const user = userEvent.setup();
    renderWithQueryClient(<RightPanel />, { wrapper });

    const fetchMock = mockPanelApi();
    await openTab(user, "Notes");
    await user.type(
      await screen.findByRole("textbox", { name: "New note" }),
      "Her mother's name is never spoken.",
    );
    await roundTrip(user, "Notes", "Models");

    expect(
      await screen.findByRole("textbox", { name: "New note" }),
      "the unsent note was destroyed by a tab switch",
    ).toHaveValue("Her mother's name is never spoken.");
    // The Notes tab stacks four panels and asks for eight endpoints. If any
    // of them 404s the panel renders an error instead of its controls, and
    // the assertion above would pass over a broken screen.
    await assertNoUnansweredRoutes(fetchMock);
  });

  it("NOTES: the new-limit box still holds its text", async () => {
    // The Notes tab renders four panels stacked, and only the first of them
    // is the notebook. A fix that reached one component in the tab and not
    // its neighbour would pass the case above and fail here.
    const user = userEvent.setup();
    mockPanelApi();
    renderWithQueryClient(<RightPanel />, { wrapper });

    await openTab(user, "Notes");
    const limit = await screen.findByPlaceholderText(
      "Something to keep out of the story...",
    );
    await user.type(limit, "no hospitals");
    await roundTrip(user, "Notes", "Models");

    expect(
      await screen.findByPlaceholderText(
        "Something to keep out of the story...",
      ),
      "the unsent limit was destroyed by a tab switch",
    ).toHaveValue("no hospitals");
  });

  it("NOTES: an unsent note does not follow the reader into another chat", async () => {
    // The hazard that comes WITH keeping the text: a note is filed into
    // whichever chat is open when Add is pressed, so a buffer shared across
    // chats would offer a sentence written about one story for filing into
    // the next one. Keyed on the chat, like the composer buffer.
    const user = userEvent.setup();
    mockPanelApi();
    renderWithQueryClient(<RightPanel />, { wrapper });

    await openTab(user, "Notes");
    await user.type(
      await screen.findByRole("textbox", { name: "New note" }),
      "Belongs to chat seven.",
    );

    // A different conversation, reached the way the app reaches one.
    act(() => {
      useUiStore.setState({ selectedChatId: 9 });
    });
    await openTab(user, "Models");
    await openTab(user, "Notes");

    expect(
      await screen.findByRole("textbox", { name: "New note" }),
      "one chat's unsent note was offered to another chat",
    ).toHaveValue("");

    // GROUND CONTROL for this case: the note is not gone, it is filed under
    // the chat it was written in, and going back shows it again.
    act(() => {
      useUiStore.setState({ selectedChatId: CHAT_ID });
    });
    expect(
      await screen.findByRole("textbox", { name: "New note" }),
      "the note was destroyed rather than kept with its own chat",
    ).toHaveValue("Belongs to chat seven.");
  });

  it("NOTES: an unsent limit does not follow the reader into another chat", async () => {
    // The limit box has the same documented property as the note box above
    // and had nothing measuring it. The hazard is worse here, because the
    // scope select beside it can file the limit into whichever chat is open:
    // a buffer shared across chats would offer a limit written about one
    // story as the one to store against the next, with the user's own words
    // on screen saying it belonged there.
    const user = userEvent.setup();
    mockPanelApi();
    renderWithQueryClient(<RightPanel />, { wrapper });

    await openTab(user, "Notes");
    await user.type(
      await screen.findByRole("textbox", { name: "New limit" }),
      "no hospitals",
    );

    act(() => {
      useUiStore.setState({ selectedChatId: OTHER_CHAT_ID });
    });
    await openTab(user, "Models");
    await openTab(user, "Notes");

    expect(
      await screen.findByRole("textbox", { name: "New limit" }),
      "one chat's unsent limit was offered to another chat",
    ).toHaveValue("");

    // GROUND CONTROL: the limit is not gone, it is filed under the chat it
    // was written in. Without this, a box that had simply stopped restoring
    // anything at all would read exactly the same.
    act(() => {
      useUiStore.setState({ selectedChatId: CHAT_ID });
    });
    expect(
      await screen.findByRole("textbox", { name: "New limit" }),
      "the limit was destroyed rather than kept with its own chat",
    ).toHaveValue("no hospitals");
  });

  it("NOTES: a restored limit keeps the settings that say what it means", async () => {
    // Half a restore is a lie about the other half. The label coming back
    // beside four qualifiers that had snapped to their defaults would store
    // a HARD GLOBAL limit where the user had written a soft one for this
    // chat, with their own words on screen saying it was theirs. This panel
    // is the one place in the app where that costs the most.
    const user = userEvent.setup();
    const fetchMock = mockPanelApi();
    renderWithQueryClient(<RightPanel />, { wrapper });

    await openTab(user, "Notes");
    await user.type(
      await screen.findByRole("textbox", { name: "New limit" }),
      "no hospitals",
    );
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Where this applies" }),
      "chat",
    );
    await user.selectOptions(
      screen.getByRole("combobox", { name: "How strict" }),
      "soft",
    );
    await roundTrip(user, "Notes", "Models");

    expect(
      await screen.findByRole("combobox", { name: "Where this applies" }),
      "the limit's scope snapped back while its words were restored",
    ).toHaveValue("chat");
    expect(
      screen.getByRole("combobox", { name: "How strict" }),
      "the limit's strictness snapped back while its words were restored",
    ).toHaveValue("soft");

    // And what is stored is what was on screen, which is the only version of
    // this claim the user can be harmed by.
    await user.click(screen.getByRole("button", { name: "Add limit" }));
    await waitFor(() => {
      const write = fetchMock.mock.calls.find(
        (call) =>
          String(call[0]).includes("/notebook/boundaries") &&
          (call[1] as RequestInit | undefined)?.method === "POST",
      );
      expect(write, "the limit was never sent").toBeDefined();
      const body = JSON.parse(String((write as unknown[])[1] &&
        (write![1] as RequestInit).body));
      expect(body.severity, "a soft limit was stored as hard").toBe("soft");
      expect(body.chat_id, "a chat limit was stored globally").toBe(CHAT_ID);
    });
  });

  it("NOTES: a restored limit keeps what happens next and how far it may go", async () => {
    // The other two qualifiers. The case above covers scope and strictness;
    // these two had no test at all, and they are the pair that decides what
    // the app DOES when the line is crossed. A label restored beside them
    // after they had snapped back would store "pause there, no rating limit"
    // under words that said "stop the scene, nothing past PG-13" - the same
    // lie as the case above, told about the half nobody was measuring.
    const user = userEvent.setup();
    const fetchMock = mockPanelApi();
    renderWithQueryClient(<RightPanel />, { wrapper });

    await openTab(user, "Notes");
    await user.type(
      await screen.findByRole("textbox", { name: "New limit" }),
      "no hospitals",
    );
    await user.selectOptions(
      screen.getByRole("combobox", { name: "What to do if it happens" }),
      "hard_stop",
    );
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Rating ceiling" }),
      "PG-13",
    );
    await roundTrip(user, "Notes", "Models");

    expect(
      await screen.findByRole("combobox", {
        name: "What to do if it happens",
      }),
      "what to do about the limit snapped back while its words were restored",
    ).toHaveValue("hard_stop");
    expect(
      screen.getByRole("combobox", { name: "Rating ceiling" }),
      "the rating ceiling snapped back while its words were restored",
    ).toHaveValue("PG-13");

    // And what is stored is what was on screen. The rating is the one field
    // the panel OMITS when it is empty, so a ceiling that never reached the
    // payload would leave no trace anywhere else.
    await user.click(screen.getByRole("button", { name: "Add limit" }));
    await waitFor(() => {
      const write = fetchMock.mock.calls.find(
        (call) =>
          String(call[0]).includes("/notebook/boundaries") &&
          (call[1] as RequestInit | undefined)?.method === "POST",
      );
      expect(write, "the limit was never sent").toBeDefined();
      const body = JSON.parse(String((write![1] as RequestInit).body));
      expect(
        body.on_violation,
        "a limit that should stop the scene was stored as a pause",
      ).toBe("hard_stop");
      expect(
        body.rating_ceiling,
        "the rating ceiling never reached the payload",
      ).toBe("PG-13");
    });
  });

  it("NOTES: the search filter belongs to the chat it was typed in", async () => {
    // The filter used to be cleared by hand on every chat change, because a
    // word carried into the next chat hides notes behind a box that only
    // renders above five of them. Keyed on the chat, that rule holds without
    // anyone remembering it - and the filter now also survives a tab switch,
    // which it did not before.
    const user = userEvent.setup();
    mockFetch({
      ...manyNotesRoutes(),
      "/models": { body: modelListFixture },
      "/settings": { body: { ...settingsFixture, proxy_configured: true } },
      "/personas": { body: [PERSONA, OTHER_PERSONA] },
      "/tts/voice-mode": { body: { enabled: false, active: false, prompt_chars: 0 } },
      "/chats": { body: [] },
      "/characters": { body: [] },
    });
    renderWithQueryClient(<RightPanel />, { wrapper });

    await openTab(user, "Notes");
    await user.type(
      await screen.findByRole("textbox", { name: "Search notes" }),
      "mother",
    );
    await roundTrip(user, "Notes", "Models");

    expect(
      await screen.findByRole("textbox", { name: "Search notes" }),
      "the filter was destroyed by a tab switch",
    ).toHaveValue("mother");

    act(() => {
      useUiStore.setState({ selectedChatId: 9 });
    });
    expect(
      await screen.findByRole("textbox", { name: "Search notes" }),
      "one chat's filter followed the reader into another chat",
    ).toHaveValue("");
  });

  it("PERSONA: an open edit form comes back on the row it belongs to", async () => {
    // The edit form is opened one row at a time, so restoring it means
    // restoring WHICH row as well as what was typed into it. A buffer keyed
    // on the field alone would reopen the wrong person's form.
    const user = userEvent.setup();
    mockPanelApi();
    renderWithQueryClient(<RightPanel />, { wrapper });

    await openTab(user, "Persona");
    await screen.findByTestId(`persona-card-${PERSONA.id}`);
    await user.click(editButtonFor(PERSONA.id));
    const name = screen.getByRole("textbox", { name: "Display name" });
    await user.clear(name);
    await user.type(name, "Kestrel the elder");
    await roundTrip(user, "Persona", "Models");

    const card = await screen.findByTestId(`persona-card-${PERSONA.id}`);
    expect(
      within(card).getByRole("textbox", { name: "Display name" }),
      "the persona edit form lost its row or its text",
    ).toHaveValue("Kestrel the elder");
    // And it is stored under this row's key, not a shared one.
    expect(
      useDraftStore.getState().panel[personaEditDraftKey(PERSONA.id, "name")]
        ?.text,
    ).toBe("Kestrel the elder");
  });

  it("typing in one box does not copy the other box out of the vault", async () => {
    // A persona description is vault content. This store sits OUTSIDE the
    // vault and outlives a lock, so a field the user has not touched must
    // not be copied into it just because its neighbour was typed in. That is
    // also what keeps "a buffer exists" meaning "the user typed here", which
    // is the fact the open/closed flags are derived from.
    const user = userEvent.setup();
    mockPanelApi();
    renderWithQueryClient(<RightPanel />, { wrapper });

    await openTab(user, "Persona");
    await screen.findByTestId(`persona-card-${PERSONA.id}`);
    await user.click(editButtonFor(PERSONA.id));
    await user.type(
      screen.getByRole("textbox", { name: "Display name" }),
      "x",
    );

    const panel = useDraftStore.getState().panel;
    expect(
      panel[personaEditDraftKey(PERSONA.id, "name")]?.text,
      "the box that WAS typed in kept nothing, so the check below is empty",
    ).toBe(`${PERSONA.display_name}x`);
    expect(
      panel[personaEditDraftKey(PERSONA.id, "description")],
      "an untouched description was copied out of the vault into the store",
    ).toBeUndefined();
    // Said again over the whole map, so a future field cannot slip through
    // under a key this case does not name.
    expect(
      Object.values(panel).some((entry) =>
        entry.text.includes(PERSONA.description),
      ),
      "vault text the user never typed reached a store that outlives a lock",
    ).toBe(false);
  });

  it("moving straight to another row's pencil hands the form over", async () => {
    // Two clicks, no Cancel between them. Only one form is ever on screen, so
    // the row walked away from must not keep a live buffer: it would outrank
    // nothing today, but it would come back as a second armed row and hide
    // the words just typed into the row the user is actually in.
    const user = userEvent.setup();
    mockPanelApi();
    renderWithQueryClient(<RightPanel />, { wrapper });

    await openTab(user, "Persona");
    await screen.findByTestId(`persona-card-${PERSONA.id}`);
    await user.click(editButtonFor(PERSONA.id));
    await user.type(
      screen.getByRole("textbox", { name: "Display name" }),
      " abandoned",
    );
    await user.click(editButtonFor(OTHER_PERSONA.id));
    await user.type(
      screen.getByRole("textbox", { name: "Display name" }),
      " kept",
    );
    await roundTrip(user, "Persona", "Models");

    const other = await screen.findByTestId(
      `persona-card-${OTHER_PERSONA.id}`,
    );
    expect(
      within(other).getByRole("textbox", { name: "Display name" }),
      "the form came back on the row the user had walked away from",
    ).toHaveValue(`${OTHER_PERSONA.display_name} kept`);
    expect(
      within(screen.getByTestId(`persona-card-${PERSONA.id}`)).queryByRole(
        "textbox",
        { name: "Display name" },
      ),
      "two rows were armed at once",
    ).not.toBeInTheDocument();
    expect(
      useDraftStore.getState().panel[personaEditDraftKey(PERSONA.id, "name")],
      "the abandoned row kept a buffer nothing can reach",
    ).toBeUndefined();
  });

  it("PERSONA: two live edit buffers reopen the NEWEST row, not the first met", async () => {
    // Only one edit form is ever on screen, so a second live buffer means an
    // older one was stranded - a row deleted from another window, a mount
    // that raced a close. The panel picks which row to reopen at mount, and
    // picking the first key it meets would let the stranded row outrank the
    // one the user was actually typing in: their words would be in the store
    // and nowhere on screen, under someone else's name.
    //
    // BOTH insertion orders are run, and that is the control. With one order,
    // "the newest key" and "the second row in the list" are the same answer,
    // so a rule that was really doing something else would look correct.
    const orders: [number, number][] = [
      [PERSONA.id, OTHER_PERSONA.id],
      [OTHER_PERSONA.id, PERSONA.id],
    ];
    for (const [stranded, live] of orders) {
      useDraftStore.getState().clearAll();
      useUiStore.setState({ activeRightPanelTab: "models" });
      act(() => {
        const store = useDraftStore.getState();
        // Written in this order, so the LIVE row is the newest key.
        store.setPanelDraft(
          personaEditDraftKey(stranded, "name"),
          "walked away from",
        );
        store.setPanelDraft(
          personaEditDraftKey(live, "name"),
          "still being typed",
        );
      });

      const user = userEvent.setup();
      mockPanelApi();
      const { unmount } = renderWithQueryClient(<RightPanel />, { wrapper });
      await openTab(user, "Persona");

      const liveCard = await screen.findByTestId(`persona-card-${live}`);
      // Queried rather than asserted straight through, so a form that opened
      // on the wrong row reports WHICH row it opened on instead of a bare
      // "element not found" from the row it should have been on.
      const liveBox = within(liveCard).queryByRole("textbox", {
        name: "Display name",
      });
      expect(
        liveBox,
        `the form reopened on stranded row ${stranded} instead of ${live}`,
      ).not.toBeNull();
      expect(liveBox, `row ${live} came back without its words`).toHaveValue(
        "still being typed",
      );
      // GROUND CONTROL for the line above: exactly one row is armed, so
      // "the live row has a form" is not merely "every row has one".
      expect(
        within(screen.getByTestId(`persona-card-${stranded}`)).queryByRole(
          "textbox",
          { name: "Display name" },
        ),
        `rows ${stranded} and ${live} were armed at once`,
      ).not.toBeInTheDocument();

      unmount();
    }
  });

  it("an untouched edit form follows the stored persona instead of a buffer", async () => {
    // The other half of the rule above: nothing is copied into the store when
    // the pencil is pressed, so a form the user has not disagreed with still
    // shows what the server holds and leaves no buffer behind.
    const user = userEvent.setup();
    mockPanelApi();
    renderWithQueryClient(<RightPanel />, { wrapper });

    await openTab(user, "Persona");
    await screen.findByTestId(`persona-card-${PERSONA.id}`);
    await user.click(editButtonFor(PERSONA.id));

    expect(
      screen.getByRole("textbox", { name: "Display name" }),
    ).toHaveValue(PERSONA.display_name);
    expect(
      useDraftStore.getState().panel[personaEditDraftKey(PERSONA.id, "name")],
      "opening the form wrote a buffer nobody typed",
    ).toBeUndefined();
  });

  it("Cancel discards the edit form instead of reopening it later", async () => {
    // The counterpart to the restore. If Cancel left the buffer behind, the
    // form would come back on the next mount holding words the user has
    // explicitly abandoned - which is the one thing a Cancel promises.
    const user = userEvent.setup();
    mockPanelApi();
    renderWithQueryClient(<RightPanel />, { wrapper });

    await openTab(user, "Persona");
    await screen.findByTestId(`persona-card-${PERSONA.id}`);
    await user.click(editButtonFor(PERSONA.id));
    await user.type(
      screen.getByRole("textbox", { name: "Display name" }),
      " the elder",
    );
    expect(
      useDraftStore.getState().panel[personaEditDraftKey(PERSONA.id, "name")],
      "typing left no buffer, so the Cancel below would prove nothing",
    ).toBeDefined();

    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await roundTrip(user, "Persona", "Models");

    expect(
      screen.queryByText("Edit Persona"),
      "a cancelled edit form came back after a tab switch",
    ).not.toBeInTheDocument();
    expect(
      useDraftStore.getState().panel[personaEditDraftKey(PERSONA.id, "name")],
    ).toBeUndefined();
  });

  it("Close discards the create form instead of reopening it later", async () => {
    const user = userEvent.setup();
    mockPanelApi();
    renderWithQueryClient(<RightPanel />, { wrapper });

    await openTab(user, "Persona");
    await user.click(await screen.findByRole("button", { name: /New Persona/ }));
    await user.type(
      screen.getByRole("textbox", { name: "Display name" }),
      "Wren",
    );
    expect(
      useDraftStore.getState().panel[PANEL_DRAFT_KEYS.personaCreateName],
      "typing left no buffer, so the Close below would prove nothing",
    ).toBeDefined();

    await user.click(screen.getByRole("button", { name: /Close/ }));
    await roundTrip(user, "Persona", "Models");

    expect(
      screen.queryByText("Create Persona"),
      "a closed create form came back after a tab switch",
    ).not.toBeInTheDocument();
  });

  it("PERSONA: a saved persona stops being a buffer", async () => {
    // The buffer is the record of an unfinished thought, and a saved persona
    // is finished. Leaving it behind would reopen the create form on the next
    // mount, pre-filled with a person who already exists - and it would leave
    // the name and the description sitting in a store that outlives a vault
    // lock, long after the row itself went into the vault.
    const user = userEvent.setup();
    mockPanelApi();
    renderWithQueryClient(<RightPanel />, { wrapper });

    await openTab(user, "Persona");
    await user.click(await screen.findByRole("button", { name: /New Persona/ }));
    await user.type(
      screen.getByRole("textbox", { name: "Display name" }),
      CREATED_PERSONA.display_name,
    );
    await user.type(
      screen.getByRole("textbox", { name: "Description" }),
      CREATED_PERSONA.description,
    );
    // PRECONDITION: both boxes really are buffers before the save. Without
    // it, "gone afterwards" would be true of a form that never held anything.
    expect(
      useDraftStore.getState().panel[PANEL_DRAFT_KEYS.personaCreateName],
      "typing left no buffer, so the save below would prove nothing",
    ).toBeDefined();
    expect(
      useDraftStore.getState().panel[
        PANEL_DRAFT_KEYS.personaCreateDescription
      ],
    ).toBeDefined();

    await user.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(
        useDraftStore.getState().panel[PANEL_DRAFT_KEYS.personaCreateName],
        "a saved persona's name stayed behind as an unfinished draft",
      ).toBeUndefined();
    });
    expect(
      useDraftStore.getState().panel[
        PANEL_DRAFT_KEYS.personaCreateDescription
      ],
      "a saved persona's description stayed behind as an unfinished draft",
    ).toBeUndefined();
    // The form closes with it, so nothing on screen is still offering to
    // create the row that was just created.
    expect(
      screen.queryByText("Create Persona"),
      "the create form stayed open over a persona that already exists",
    ).not.toBeInTheDocument();
  });

  it("PERSONA: a create that FAILED keeps its words", async () => {
    // The control that makes the case above a fact about success rather than
    // about the click. If the buffer were dropped on the press, a save that
    // the server refused would take the user's words with it and leave them
    // nothing to retry from - which is the loss this whole store exists to
    // prevent, arriving through the one door that looks like a save.
    const user = userEvent.setup();
    // The whole panel mock with ONE route turned refusing, rather than a
    // short hand-written set: every unanswered route 404s, and a 404 raises
    // an error of the same shape this case is waiting for.
    const fetchMock = mockPanelApi({
      "POST /personas": { status: 500, body: { detail: "no" } },
    });
    renderWithQueryClient(<RightPanel />, { wrapper });

    await openTab(user, "Persona");
    await user.click(await screen.findByRole("button", { name: /New Persona/ }));
    await user.type(
      screen.getByRole("textbox", { name: "Display name" }),
      "Wren",
    );

    await user.click(screen.getByRole("button", { name: "Create" }));

    // Waited on the SAVE settling, not on an error appearing. The button is
    // disabled while the request is in flight, so this is the panel itself
    // saying the attempt is over - an error count can be moved by anything on
    // screen and would let the assertions below read a half-finished save.
    await waitFor(() => {
      const posts = fetchMock.mock.calls.filter(
        (call) =>
          String(call[0]).includes("/personas") &&
          (call[1] as RequestInit | undefined)?.method === "POST",
      );
      expect(posts, "the save was never attempted").toHaveLength(1);
      expect(screen.getByRole("button", { name: "Create" })).toBeEnabled();
    });
    expect(
      useErrorStore.getState().errors.length,
      "a refused save told the user nothing",
    ).toBeGreaterThan(0);
    expect(
      useDraftStore.getState().panel[PANEL_DRAFT_KEYS.personaCreateName]?.text,
      "a refused save threw away the words it refused to store",
    ).toBe("Wren");
    expect(
      screen.getByRole("textbox", { name: "Display name" }),
      "the form kept the buffer but stopped showing it",
    ).toHaveValue("Wren");
  });

  it("a restored proxy label is still counted as edited when it is saved", async () => {
    // The trap this panel set for the fix. Whether the label may be written
    // was tracked in a second flag beside the box, and that flag came back
    // false after a tab switch - so a restored label would have been SHOWN to
    // the user and then dropped by the save, which is worse than losing it,
    // because the box looks like it worked.
    const user = userEvent.setup();
    const fetchMock = mockPanelApi();
    renderWithQueryClient(<RightPanel />, { wrapper });

    await openTab(user, "Security");
    await user.type(
      await screen.findByLabelText("Proxy alias input"),
      "work laptop",
    );
    await roundTrip(user, "Security", "Models");
    await user.click(await screen.findByRole("button", { name: "Save proxy" }));

    await waitFor(() => {
      const aliasWrites = fetchMock.mock.calls.filter(
        (call) =>
          String(call[0]).includes("/proxy/alias") &&
          (call[1] as RequestInit | undefined)?.method === "POST",
      );
      expect(
        aliasWrites.length,
        "the restored label was shown and then not saved",
      ).toBe(1);
      expect(String((aliasWrites[0][1] as RequestInit).body)).toContain(
        "work laptop",
      );
    });
  });

  // ── POSITIVE CONTROL: the survival claims can go the other way ────────────
  it("POSITIVE CONTROL: a vault wipe empties every one of these boxes", async () => {
    // Without this, "the text comes back" would be satisfied by a panel that
    // simply never lets go of anything, and the wipe path is the one case
    // where letting go is mandatory: a new vault restarts its ids at 1 and
    // must not open holding the destroyed vault's words.
    const user = userEvent.setup();
    mockPanelApi();
    renderWithQueryClient(<RightPanel />, { wrapper });

    await user.type(
      await screen.findByRole("textbox", { name: "Search models" }),
      "opus",
    );
    await openTab(user, "Notes");
    await user.type(
      await screen.findByRole("textbox", { name: "New note" }),
      "a note",
    );

    // Exactly what VaultGate's wipe path calls, invoked the same way.
    act(() => {
      useDraftStore.getState().clearAll();
    });

    expect(
      screen.getByRole("textbox", { name: "New note" }),
      "the note outlived a vault wipe",
    ).toHaveValue("");
    await openTab(user, "Models");
    expect(
      await screen.findByRole("textbox", { name: "Search models" }),
      "the search outlived a vault wipe",
    ).toHaveValue("");
    expect(useDraftStore.getState().totalBytes).toBe(0);
  });

  it("the text survives the whole panel being unmounted and mounted again", async () => {
    // This is what a vault LOCK does to this text, and the reason it is
    // written as an unmount rather than as a lock: the lock path belongs to
    // VaultGate, which deliberately does NOT clear this store, and which
    // another unit owns. So the semantic that ships is stated here rather
    // than left to be discovered - right-panel text outlives a lock exactly
    // as composer and edit text do, on the same reasoning: the subtree is
    // unmounted rather than hidden, so after a lock the words are a heap
    // value with no node, no accessible name and no pixel. The one case
    // where they must NOT survive is a vault WIPE, covered below.
    const user = userEvent.setup();
    mockPanelApi();
    const { unmount } = renderWithQueryClient(<RightPanel />, { wrapper });

    await user.type(
      await screen.findByRole("textbox", { name: "Search models" }),
      "held across the gate",
    );

    unmount();
    expect(
      screen.queryByRole("textbox", { name: "Search models" }),
      "the panel did not actually go away, so nothing was tested",
    ).not.toBeInTheDocument();

    mockPanelApi();
    renderWithQueryClient(<RightPanel />, { wrapper });
    expect(
      await screen.findByRole("textbox", { name: "Search models" }),
      "the text did not survive an unmount, so it would not survive a lock",
    ).toHaveValue("held across the gate");
  });

  it("a filed note stops being a buffer", async () => {
    const user = userEvent.setup();
    mockPanelApi();
    renderWithQueryClient(<RightPanel />, { wrapper });

    await openTab(user, "Notes");
    await user.type(
      await screen.findByRole("textbox", { name: "New note" }),
      "Her mother's name is never spoken.",
    );
    expect(
      useDraftStore.getState().panel[NOTE_KEY],
      "typing left no buffer, so the case below would prove nothing",
    ).toBeDefined();

    await user.click(screen.getByRole("button", { name: "Add note" }));

    await waitFor(() => {
      expect(
        useDraftStore.getState().panel[NOTE_KEY],
        "a filed note left its buffer behind forever",
      ).toBeUndefined();
    });
  });

  // ── The credential exclusion, stated as a fact about credentials ──────────
  it("the API key never reaches the draft store, while the proxy label does", async () => {
    // Buffers in this store outlive a vault lock on purpose. A credential
    // must not, so the key box keeps its own state and keeps losing it here.
    // The second half of this case is the positive control: without it, a
    // scan that had gone blind would report exactly the same thing.
    const user = userEvent.setup();
    mockPanelApi();
    renderWithQueryClient(<RightPanel />, { wrapper });

    await openTab(user, "Security");
    await user.type(
      await screen.findByLabelText("API key input"),
      "sk-or-v1-secret",
    );
    await user.type(
      screen.getByLabelText("Proxy alias input"),
      "work laptop",
    );

    const stored = Object.values(useDraftStore.getState().panel).map(
      (entry) => entry.text,
    );
    expect(
      stored.some((text) => text.includes("sk-or-v1-secret")),
      "a credential was written into a store that outlives a vault lock",
    ).toBe(false);
    expect(
      stored,
      "nothing at all reached the store, so the line above measured nothing",
    ).toContain("work laptop");
  });

  it("none of this text reaches device storage", async () => {
    const user = userEvent.setup();
    mockPanelApi();
    renderWithQueryClient(<RightPanel />, { wrapper });

    await user.type(
      await screen.findByRole("textbox", { name: "Search models" }),
      "zzsentinelzz",
    );
    await openTab(user, "Notes");
    await user.type(
      await screen.findByRole("textbox", { name: "New note" }),
      "zzsentinelzz note",
    );

    // Precondition: the sentinel really is in the store. Without it the
    // sweep below would be hunting for something nothing ever wrote.
    expect(
      Object.values(useDraftStore.getState().panel).some((entry) =>
        entry.text.includes("zzsentinelzz"),
      ),
    ).toBe(true);

    const sweep = (store: Storage) =>
      Object.keys(store)
        .map((key) => `${key}=${store.getItem(key) ?? ""}`)
        .join("\n");

    // POSITIVE CONTROL on the SWEEP ITSELF, not on setItem. Planting a value
    // and reading it straight back proves the storage works; it does not
    // prove that this particular Object.keys walk can SEE a planted value,
    // and a walk that returned nothing would look identical to a clean
    // result. Both storages are planted, because they are separate objects
    // and only one of them was ever really exercised.
    for (const store of [localStorage, sessionStorage]) {
      store.setItem("probe", "zzsentinelzz planted");
      expect(
        sweep(store),
        "the sweep cannot see a value that is definitely there",
      ).toContain("zzsentinelzz");
      store.removeItem("probe");
      expect(
        sweep(store),
        "panel text was written to device storage",
      ).not.toContain("zzsentinelzz");
    }
  });
});

/**
 * The accounting behind the panel buffers.
 *
 * Driven through the store's own API rather than the DOM, because what is
 * being measured is the running byte total, and no pixel shows it. It still
 * measures behaviour: every assertion is about what the store DOES, and the
 * ceiling it is checked against is imported from the store rather than
 * copied here, so raising the ceiling cannot leave a stale number behind.
 */
describe("panel buffer accounting", () => {
  beforeEach(() => {
    useDraftStore.getState().clearAll();
    useErrorStore.getState().clearAll();
  });

  const KEY = PANEL_DRAFT_KEYS.modelSearch;
  const OTHER = PANEL_DRAFT_KEYS.proxyAlias;

  it("counts, replaces and frees without the total drifting", () => {
    const store = useDraftStore.getState();
    expect(store.totalBytes, "the store did not start empty").toBe(0);

    const sample = "ölçüm";
    store.setPanelDraft(KEY, sample);
    // Derived, never retyped. The expected size comes from an INDEPENDENT
    // encoder rather than from the function under test or from arithmetic
    // done by hand, so this checks the store's accounting against something
    // that does not share its bugs.
    const counted = new TextEncoder().encode(sample).length;
    expect(
      counted,
      "the sample is pure ASCII, so it would not exercise the counter",
    ).toBeGreaterThan(sample.length);
    expect(draftByteSize(sample)).toBe(counted);
    expect(useDraftStore.getState().totalBytes).toBe(counted);

    // Replacing a longer buffer with a shorter one must SHRINK the total.
    // Adding the new size without subtracting the old is the classic bug
    // here, and it leaks the budget one keystroke at a time.
    store.setPanelDraft(KEY, "a");
    expect(useDraftStore.getState().totalBytes).toBe(1);

    store.setPanelDraft(OTHER, "bb");
    expect(useDraftStore.getState().totalBytes).toBe(3);

    store.clearPanelDraft(KEY);
    expect(useDraftStore.getState().totalBytes).toBe(2);
    expect(useDraftStore.getState().panel[KEY]).toBeUndefined();
    expect(useDraftStore.getState().panel[OTHER]).toBeDefined();

    // The total is the sum of what is actually held, not a counter that has
    // been kept roughly in step with it.
    const sum = Object.values(useDraftStore.getState().panel).reduce(
      (acc, entry) => acc + entry.bytes,
      0,
    );
    expect(useDraftStore.getState().totalBytes).toBe(sum);
  });

  it("an emptied box keeps its entry, and a cleared one does not", () => {
    const store = useDraftStore.getState();
    store.setPanelDraft(KEY, "typed");
    store.setPanelDraft(KEY, "");
    expect(
      useDraftStore.getState().panel[KEY],
      "deleting the text also deleted the record that the box was touched",
    ).toEqual({ text: "", bytes: 0 });

    store.clearPanelDraft(KEY);
    expect(useDraftStore.getState().panel[KEY]).toBeUndefined();
  });

  it("a prefix clear takes its own buffers and leaves the rest", () => {
    const store = useDraftStore.getState();
    store.setPanelDraft(personaEditDraftKey(1, "name"), "one");
    store.setPanelDraft(personaEditDraftKey(2, "name"), "two");
    store.setPanelDraft(KEY, "kept");

    store.clearPanelDraftsWithPrefix("persona.edit.1.");

    const panel = useDraftStore.getState().panel;
    expect(panel[personaEditDraftKey(1, "name")]).toBeUndefined();
    expect(
      panel[personaEditDraftKey(2, "name")]?.text,
      "a prefix clear took a neighbouring row's buffer with it",
    ).toBe("two");
    expect(panel[KEY]?.text).toBe("kept");
    expect(useDraftStore.getState().totalBytes).toBe(3 + 4);
  });

  it("refuses an oversized write, keeps the old text, and says so", () => {
    const store = useDraftStore.getState();
    store.setPanelDraft(KEY, "safe");

    const accepted = store.setPanelDraft(KEY, "x".repeat(MAX_DRAFT_BYTES + 1));

    expect(accepted, "an oversized write was accepted").toBe(false);
    expect(
      useDraftStore.getState().panel[KEY]?.text,
      "a refused write destroyed the text it refused to replace",
    ).toBe("safe");
    // A refusal the user cannot see is indistinguishable from the app losing
    // the text, which is the failure this whole store exists to prevent.
    const events = useErrorStore.getState().errors;
    expect(events.map((event) => event.code)).toContain("draft_too_large");
    // Told apart by WHICH box refused, so a second box refusing is not
    // silently folded into the first one's toast.
    expect(events[0].source).toBe(KEY);
  });

  it("a write that fits is accepted, so the refusal above means something", () => {
    // Positive control for the case before it: the same code path with a
    // payload one byte under the ceiling has to succeed, or "refused" would
    // just be what this store always does.
    const store = useDraftStore.getState();
    const accepted = store.setPanelDraft(KEY, "y".repeat(MAX_DRAFT_BYTES));
    expect(accepted).toBe(true);
    expect(useDraftStore.getState().panel[KEY]?.bytes).toBe(MAX_DRAFT_BYTES);
    expect(useErrorStore.getState().errors).toHaveLength(0);
  });
});
