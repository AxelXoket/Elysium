/**
 * Settings > Voice - the three parameter tiers.
 *
 * The backend has always shipped the split and the page always threw it
 * away: ModelParams computed `params` and `advanced` from the ParamSpec
 * descriptors and then rendered `[...params, ...advanced]` into one flat
 * column, with the matrix's uneditable rows mixed in. On Fish S2 that put
 * `language` and `temperature` at exactly the same visual weight as
 * `top_p`, `top_k`, `max_new_tokens` and `kv_cache_len`, and beneath them a
 * run of rows for knobs the engine does not have at all.
 *
 * These tests pin the arrangement, not the pixels: what a person meets
 * without touching anything, and that NOTHING was dropped to get there.
 * Every hidden control has to still be reachable behind its own door.
 *
 * The queries are deliberately role-based. Collapse closes by setting
 * `inert` and `aria-hidden`, and getByRole honours aria-hidden while
 * getByLabelText does not - so a role query is the one that would actually
 * go red if a door stopped closing.
 */
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { VoiceSettingsPage } from "@/components/settings/VoiceSettingsPage";
import { useErrorStore } from "@/lib/errors/errorStore";
import { mockFetch } from "../mocks/api";

const VOICE_MODE = { enabled: false, active: false, prompt_chars: 3200 };

const RUNTIMES = {
  runtimes: [
    { engine_id: "fish_s2", state: "ready", python: "p", error_code: null },
  ],
  engines: [{ engine_id: "fish_s2", display_name: "Fish Audio S2 Pro" }],
};

const IDLE_JOB = {
  engine_id: "fish_s2",
  state: "idle",
  log: [],
  error_code: null,
  error_detail: "",
  running: false,
};

const SECOND_MODEL = {
  uid: "u2",
  engine_id: "fish_s2",
  name: "s2-lite",
  path: "C:/m/s2lite",
  variant: null,
  source: "signature",
  incomplete: false,
  missing: [],
  readiness: {
    uid: "u2",
    engine_id: "fish_s2",
    runnable: true,
    settings_available: true,
    runtime_state: "ready",
    issues: [],
    languages: ["en"],
    fit: null,
  },
};

const MODEL = {
  uid: "u1",
  engine_id: "fish_s2",
  name: "s2-pro",
  path: "C:/m/s2",
  variant: null,
  source: "signature",
  incomplete: false,
  missing: [],
  readiness: {
    uid: "u1",
    engine_id: "fish_s2",
    runnable: true,
    settings_available: true,
    runtime_state: "ready",
    issues: [],
    languages: ["en"],
    fit: null,
  },
};

/** One row of each tier, so every branch is exercised by one render. */
const PRIMARY = {
  name: "temperature",
  type: "float",
  default: 0.7,
  label: "Expressiveness",
  help: "",
  minimum: 0.05,
  maximum: 1.5,
  step: 0.05,
  choices: null,
  group: "quality",
  advanced: false,
};

const ADVANCED = {
  name: "top_p",
  type: "float",
  default: 0.8,
  label: "Nucleus sampling",
  help: "",
  minimum: 0.1,
  maximum: 1,
  step: 0.05,
  choices: null,
  group: "quality",
  advanced: false,
};

const UNAVAILABLE_REASON = "The selected voice model has no such setting.";

/** matrix.py's three uneditable statuses say three different things, and its
    own opening paragraph exists to say they must not be flattened. These two
    are the ones that must NOT end up behind a door labelled "not available on
    this engine", because for them that label is false. */
const APP_LEVEL_REASON =
  "Set under Delivery - it is applied by Elysium and works the same on every voice model.";
const DEAD_REASON =
  "This engine accepts the value and never applies it - its repetition control is fixed internally, so the dial would do nothing.";

function schemaWith(matrix: unknown[]) {
  return {
    uid: "u1",
    engine_id: "fish_s2",
    display_name: "Fish Audio S2 Pro",
    variant: null,
    capabilities: {
      voice_cloning: true,
      needs_reference_transcript: true,
      inline_prosody_tags: true,
      streaming: false,
      languages: ["en"],
      native_sample_rate: 44100,
    },
    params: [PRIMARY],
    matrix,
  };
}

/** primary (editable, plain) + advanced (editable, advanced) + inert row. */
const FULL_MATRIX = [
  { ...PRIMARY, editable: true, status: "supported", reason: "" },
  { ...ADVANCED, advanced: true, editable: true, status: "supported", reason: "" },
  {
    name: "min_p",
    type: "float",
    default: 0.05,
    label: "Minimum probability",
    help: "",
    minimum: 0,
    maximum: 1,
    step: 0.01,
    choices: null,
    group: "quality",
    advanced: false,
    editable: false,
    status: "unsupported",
    reason: UNAVAILABLE_REASON,
  },
];

/** The full union as the backend really emits it: one supported row, one
    advanced row, one unsupported row, plus the app_level and dead rows. */
const STATUS_MATRIX = [
  ...FULL_MATRIX,
  {
    name: "speed",
    type: "float",
    default: 1,
    label: "Reading speed",
    help: "",
    minimum: 0.8,
    maximum: 1.25,
    step: 0.05,
    choices: null,
    group: "voice",
    advanced: false,
    editable: false,
    status: "app_level",
    reason: APP_LEVEL_REASON,
  },
  {
    name: "repetition_penalty",
    type: "float",
    default: 2,
    label: "Repetition penalty",
    help: "",
    minimum: 1,
    maximum: 15,
    step: 0.5,
    choices: null,
    group: "quality",
    advanced: false,
    editable: false,
    status: "dead",
    reason: DEAD_REASON,
  },
];

function routes(matrix: unknown[]) {
  return {
    "/tts/voice-mode": { body: VOICE_MODE },
    "/tts/runtimes/fish_s2/install": { body: IDLE_JOB },
    "/tts/runtimes": { body: RUNTIMES },
    "/tts/models/u1/schema": { body: schemaWith(matrix) },
    "/tts/models/u1/settings": {
      body: { uid: "u1", values: { temperature: 0.7 }, source_map: {} },
    },
    "/tts/models": { body: { models: [MODEL], unrecognized: [], roots: [] } },
    "/tts/active": {
      body: {
        uid: "u1",
        state: "loaded",
        engine_id: "fish_s2",
        vram_mb: null,
        error_code: null,
        readiness: MODEL.readiness,
      },
    },
    "/tts/voices": { body: { voices: [] } },
  };
}

/** Opens the model's settings disclosure and waits for the panel. */
async function openParams(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByLabelText("s2-pro settings"));
  return screen.findByTestId("voice-params-u1");
}

describe("Settings > Voice: parameter tiers", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useErrorStore.getState().clearAll();
  });

  it("GROUND: an ordinary parameter needs no door at all", async () => {
    // Without this, every "is hidden" assertion below could be passing
    // because the panel rendered nothing.
    mockFetch(routes(FULL_MATRIX));
    const user = userEvent.setup();
    renderWithQueryClient(<VoiceSettingsPage />);
    await openParams(user);

    expect(
      await screen.findByRole("slider", { name: "Expressiveness slider" }),
    ).toBeInTheDocument();
  });

  it("keeps an advanced parameter behind a door, and gives it back", async () => {
    mockFetch(routes(FULL_MATRIX));
    const user = userEvent.setup();
    renderWithQueryClient(<VoiceSettingsPage />);
    await openParams(user);
    await screen.findByRole("slider", { name: "Expressiveness slider" });

    const door = await screen.findByTestId("voice-advanced-u1");
    expect(door).toHaveAttribute("aria-expanded", "false");
    expect(
      screen.queryByRole("slider", { name: "Nucleus sampling slider" }),
    ).not.toBeInTheDocument();

    await user.click(door);

    expect(door).toHaveAttribute("aria-expanded", "true");
    expect(
      await screen.findByRole("slider", { name: "Nucleus sampling slider" }),
    ).toBeInTheDocument();
  });

  it("keeps the rows this engine does not have behind their own door", async () => {
    // The reason text is the whole value of an inert row, so it is what the
    // assertion follows rather than the label.
    mockFetch(routes(FULL_MATRIX));
    const user = userEvent.setup();
    renderWithQueryClient(<VoiceSettingsPage />);
    const panel = await openParams(user);
    await screen.findByRole("slider", { name: "Expressiveness slider" });

    const door = await screen.findByTestId("voice-unavailable-u1");
    expect(door).toHaveAttribute("aria-expanded", "false");

    // The inert row is a plain div with no role, so aria-hidden is the only
    // handle a query has on it. queryAllByText's default `ignore` does not
    // filter it, but `hidden: false` on an accessibility query does, and the
    // wrapper carries aria-hidden while closed.
    const closedBox = door.nextElementSibling as HTMLElement;
    await waitFor(() => expect(closedBox.textContent).toContain(UNAVAILABLE_REASON));
    expect(closedBox).toHaveAttribute("aria-hidden", "true");

    await user.click(door);

    expect(door).toHaveAttribute("aria-expanded", "true");
    expect(closedBox).not.toHaveAttribute("aria-hidden");
    await waitFor(() =>
      expect(panel.textContent).toContain(UNAVAILABLE_REASON),
    );
  });

  it("opens no door it has nothing to put behind", async () => {
    // A model whose every row is ordinary and editable should look exactly
    // as it did before this change: controls, and no chrome.
    mockFetch(
      routes([{ ...PRIMARY, editable: true, status: "supported", reason: "" }]),
    );
    const user = userEvent.setup();
    renderWithQueryClient(<VoiceSettingsPage />);
    await openParams(user);
    await screen.findByRole("slider", { name: "Expressiveness slider" });

    expect(screen.queryByTestId("voice-advanced-u1")).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("voice-unavailable-u1"),
    ).not.toBeInTheDocument();
  });

  it("files only 'unsupported' behind the door that says so", async () => {
    // The crux, and the reason the filter reads `status`, not `editable`.
    // All three of these rows are uneditable. Only ONE of them is absent
    // because this engine has no such setting. `speed` is implemented by
    // Elysium for every engine, and on XTTS the engine implements it
    // natively; `repetition_penalty` on Fish S2 is accepted and silently
    // ignored. Filing either under "Not available on this engine" states
    // something false about the model in front of the person.
    //
    // Reverting the filter to `!p.editable` turns this test red and leaves
    // every other test in the file green, which is exactly why it exists.
    mockFetch(routes(STATUS_MATRIX));
    const user = userEvent.setup();
    renderWithQueryClient(<VoiceSettingsPage />);
    const panel = await openParams(user);
    await screen.findByRole("slider", { name: "Expressiveness slider" });

    const door = await screen.findByTestId("voice-unavailable-u1");
    expect(door).toHaveAttribute("aria-expanded", "false");

    // Scoped to the door's own subtree, not to panel.textContent. Collapse
    // keeps its child MOUNTED and merely marks it inert and aria-hidden, so
    // textContent sees closed content exactly as it sees open content: a
    // whole-panel assertion here would pass whatever the filter did, which
    // is the shape of a test that proves nothing.
    const behindTheDoor = door.parentElement as HTMLElement;
    await waitFor(() =>
      expect(behindTheDoor.textContent).toContain(UNAVAILABLE_REASON),
    );
    expect(behindTheDoor.textContent).not.toContain(APP_LEVEL_REASON);
    expect(behindTheDoor.textContent).not.toContain(DEAD_REASON);

    // ...and they really are in the column, not simply absent everywhere.
    expect(panel.textContent).toContain(APP_LEVEL_REASON);
    expect(panel.textContent).toContain(DEAD_REASON);
  });

  it("loses nothing: every row the wire sent comes out the other side", async () => {
    // The losslessness gate, counted rather than spot-checked. Naming three
    // rows passes while a fourth is silently dropped by a future filter, and
    // "nothing lost" is precisely the claim that needs the strong form.
    //
    // A row leaves as one of three shapes: a slider (float and int), a
    // switch (bool), or a .settings-param-disabled div (uneditable). One of
    // those per row in the matrix, no more and no fewer.
    mockFetch(routes(FULL_MATRIX));
    const user = userEvent.setup();
    renderWithQueryClient(<VoiceSettingsPage />);
    const panel = await openParams(user);
    await screen.findByRole("slider", { name: "Expressiveness slider" });

    await user.click(await screen.findByTestId("voice-advanced-u1"));
    await user.click(await screen.findByTestId("voice-unavailable-u1"));
    await screen.findByRole("slider", { name: "Nucleus sampling slider" });

    const rendered = () =>
      panel.querySelectorAll(
        'input[type="range"], [role="switch"], .settings-param-disabled',
      ).length;
    await waitFor(() => expect(rendered()).toBe(FULL_MATRIX.length));

    // ...and each of the three is the one it should be, so the count cannot
    // be met by rendering the same row three times.
    expect(
      screen.getByRole("slider", { name: "Expressiveness slider" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("slider", { name: "Nucleus sampling slider" }),
    ).toBeInTheDocument();
    const unavailable = (await screen.findByTestId("voice-unavailable-u1"))
      .nextElementSibling as HTMLElement;
    expect(unavailable.textContent).toContain(UNAVAILABLE_REASON);
  });

  it("keeps two open models' controls apart, ids and all", async () => {
    // The whole reason ParamControl takes a uid. Collapse keeps its child
    // mounted, so opening a second model leaves the first one mounted too,
    // and the ids used to be minted from the param name alone. Two mounted
    // panels then minted `voice-temperature` twice: label.control resolves
    // to the FIRST match in tree order, which is inside a closed inert
    // Collapse, so clicking the visible label did nothing and the on-screen
    // control was left with no accessible name at all.
    //
    // Every other test in this file mounts exactly one model, which is
    // exactly why this defect survived them.
    // u2's routes go FIRST: mockFetch matches by substring in insertion
    // order, so the bare "/tts/models" list route would otherwise swallow
    // "/tts/models/u2/schema" and hand a model list to a schema query.
    mockFetch({
      "/tts/models/u2/schema": { body: schemaWith(FULL_MATRIX) },
      "/tts/models/u2/settings": {
        body: { uid: "u2", values: { temperature: 0.7 }, source_map: {} },
      },
      ...routes(FULL_MATRIX),
      "/tts/models": {
        body: { models: [MODEL, SECOND_MODEL], unrecognized: [], roots: [] },
      },
    });
    const user = userEvent.setup();
    renderWithQueryClient(<VoiceSettingsPage />);

    await user.click(await screen.findByLabelText("s2-pro settings"));
    await screen.findByTestId("voice-params-u1");
    await user.click(await screen.findByLabelText("s2-lite settings"));
    const second = await screen.findByTestId("voice-params-u2");

    // Both panels are mounted; only one is reachable.
    expect(screen.getByTestId("voice-params-u1")).toBeInTheDocument();
    expect(
      screen.getAllByRole("slider", { name: "Expressiveness slider" }),
    ).toHaveLength(1);
    expect(second).toContainElement(
      screen.getByRole("slider", { name: "Expressiveness slider" }),
    );

    // And no id is minted twice anywhere in the document.
    const ids = Array.from(document.querySelectorAll("[id]")).map((n) => n.id);
    expect(ids.length).toBeGreaterThan(0);
    expect(new Set(ids).size, `duplicate id in: ${ids.join(", ")}`).toBe(
      ids.length,
    );
  });
});
