/**
 * SettingsToggleNames.test.tsx - one control, one name.
 *
 * A settings row shows a label and hands the accessibility tree a name, and
 * the two are supposed to be the same words. They drifted once: the row read
 * "Performed replies" on screen while a screen reader was told "Voice
 * replies", so the two users of one control were given different names for
 * it. Renaming visible copy is easy to do and easy to half-do.
 *
 * This used to be checked in settings-copy.test.ts by matching source text
 * with two double-quote regexes, and it had three ways to miss. An
 * `aria-label={expression}` never matched the quote regex; a second class name
 * beside `settings-label` never matched the exact-match regex; and a row whose
 * body ran past a four-hundred character window was never looked at. Every
 * miss was a `continue`, so the rule passed by reading nothing. Two of the
 * five toggle rows in the settings directory fall into those shapes, and they
 * are the two REUSABLE ones: they draw four of the seven rows on screen
 * today, and every row anybody adds next.
 *
 * So the names are read the way the browser reads them. The panel is
 * rendered, every row on screen is found, and each one is looked up by the
 * name it prints through the role query - which runs the same accessible-name
 * computation a screen reader does, the thing the regexes were imitating.
 *
 * Nothing may be skipped, and that is the rule this file carries over from
 * the old floor rather than dropping: a row with no control role, or no
 * printed label, is a finding here and not a `continue`.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { AppSettingsDialog } from "@/components/settings/AppSettingsDialog";
import { useUiStore } from "@/lib/store/uiStore";
import { mockFetch } from "../mocks/api";

/**
 * What today's panel puts on screen, page by page, and the number is the
 * floor. Root: the mist switch. Text: the bubble finish group. Narration: two
 * switches and the narration voice group. Voice: the performed-replies switch
 * and one boolean model parameter.
 *
 * The old test compared three rows and required three. Requiring the total
 * here does the same job, and the equality below - every row on screen was
 * compared - is what the old floor could not do: it fails on ONE row slipping
 * out of view, not only on all of them going at once.
 */
const ROWS_TODAY = 1 + 1 + 3 + 2;

/** The roles a toggle row can expose. A row with neither announces nothing. */
const ROW_ROLES = ["switch", "radiogroup"] as const;

/** A model with one of every control shape the schema page can draw. */
const SCHEMA = {
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
  params: [
    {
      name: "denoise",
      type: "bool",
      default: true,
      label: "Clean up the recording",
      help: "",
      minimum: null,
      maximum: null,
      step: null,
      choices: null,
      group: "quality",
      advanced: false,
    },
  ],
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

function routes() {
  return {
    "/tts/voice-mode": {
      body: { enabled: false, active: false, prompt_chars: 3200 },
    },
    "/tts/runtimes": {
      body: {
        runtimes: [
          { engine_id: "fish_s2", state: "ready", python: null, error_code: null },
        ],
        engines: [{ engine_id: "fish_s2", display_name: "Fish Audio S2 Pro" }],
      },
    },
    "/tts/models/u1/schema": { body: SCHEMA },
    "/tts/models/u1/settings": {
      body: { uid: "u1", values: { denoise: true }, source_map: {} },
    },
    "/tts/models": { body: { models: [MODEL], unrecognized: [], roots: [] } },
    // A loaded model is what puts the narration voice group on screen; the row
    // hides itself when nothing can speak.
    "/tts/active": {
      body: {
        uid: "u1",
        state: "loaded",
        engine_id: "fish_s2",
        vram_mb: 1024,
        error_code: null,
        readiness: null,
      },
    },
    "/tts/tag-prefs": {
      body: {
        narrative: "same", density: 8, tone: "", min: 0, max: 16,
        tone_max_chars: 60, speed: 1, speed_min: 0.8, speed_max: 1.25,
        gap: 0, gap_min: 0, gap_max: 1.5,
      },
    },
    "/tts/voices": { body: { voices: [] } },
  };
}

/** A toggle row as its two audiences meet it. */
interface Row {
  control: HTMLElement;
  role: string;
  visible: string;
}

function rowsIn(root: HTMLElement): Row[] {
  return Array.from(
    root.querySelectorAll<HTMLElement>(".settings-toggle-row"),
  ).map((control) => ({
    control,
    role: control.getAttribute("role") ?? "",
    visible: (control.querySelector(".settings-label")?.textContent ?? "")
      .replace(/\s+/g, " ")
      .trim(),
  }));
}

/**
 * Every row on screen whose accessible name is not the name it prints.
 *
 * A row that cannot be compared is one of these too. That is the whole
 * difference from the regex pass this replaces, where an unreadable row was
 * silently dropped and the sweep still reported a clean run.
 */
function disagreements(root: HTMLElement): string[] {
  const found: string[] = [];
  for (const { control, role, visible } of rowsIn(root)) {
    if (!(ROW_ROLES as readonly string[]).includes(role)) {
      found.push(`a toggle row exposes no control role (role="${role}")`);
      continue;
    }
    if (visible === "") {
      found.push(`a ${role} row prints no label to compare`);
      continue;
    }
    const byPrintedName = within(root).queryAllByRole(role, { name: visible });
    if (!byPrintedName.includes(control)) {
      found.push(`"${visible}" is not the name this ${role} announces`);
    }
  }
  return found;
}

describe("a settings control says the same thing to everybody", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({
      ambientFogOn: true,
      narrationEnabled: true,
      quoteTintEnabled: true,
      settingsInitialPage: null,
    });
  });

  afterEach(() => vi.restoreAllMocks());

  it("every toggle row in the panel announces the name it displays", async () => {
    const user = userEvent.setup();
    mockFetch(routes());
    renderWithQueryClient(
      <AppSettingsDialog open onOpenChange={() => undefined} />,
    );
    const body = document.body;
    let compared = 0;

    /** Sweep whatever page is up, and refuse to call an empty page a pass. */
    const sweep = (page: string) => {
      const rows = rowsIn(body);
      expect(rows.length, `the ${page} page rendered no toggle row`)
        .toBeGreaterThan(0);
      expect(disagreements(body), `${page}: a control has two names`)
        .toEqual([]);
      compared += rows.length;
    };

    // Root. The mist switch sits under the category list.
    await screen.findByText("Text & readability");
    sweep("root");

    await user.click(screen.getByText("Text & readability"));
    await screen.findByRole("slider", { name: "Font size slider" });
    sweep("text");

    await user.click(screen.getByRole("button", { name: "Back to settings" }));
    await user.click(await screen.findByText("Narration style"));
    // By its printed text for the same reason as the voice page below.
    await screen.findByText("Narration voice");
    sweep("narration");

    await user.click(screen.getByRole("button", { name: "Back to settings" }));
    await user.click(await screen.findByText("Voice"));
    // The model's own controls are behind its settings button; the boolean
    // parameter row is drawn from the schema, which is the second of the two
    // reusable row shapes the old regex could not read.
    await user.click(await screen.findByLabelText("s2-pro settings"));
    // Waited for by its printed text, never by its accessible name: a
    // waypoint that queries the name under test turns a divergence into a
    // missing fixture and reports the wrong thing.
    await screen.findByText("Clean up the recording");
    sweep("voice");

    // Nothing was skipped on the way, and the walk really did visit the panel.
    expect(compared, "fewer rows were compared than the panel has")
      .toBeGreaterThanOrEqual(ROWS_TODAY);
  });

  it("GROUND: the sweep reports a row whose two names differ", async () => {
    // Without this, every assertion above passes on a sweep that can no
    // longer tell agreement from silence - which is exactly how the test it
    // replaces went green for three years' worth of rows it never read.
    const stage = document.createElement("div");
    stage.innerHTML = `
      <button type="button" class="settings-toggle-row" role="switch"
              aria-checked="false" aria-label="Voice replies">
        <span class="settings-label">Performed replies</span>
      </button>`;
    document.body.appendChild(stage);

    try {
      expect(rowsIn(stage)).toHaveLength(1);
      expect(disagreements(stage)).toEqual([
        '"Performed replies" is not the name this switch announces',
      ]);
    } finally {
      stage.remove();
    }
  });

  it("GROUND: a row nobody can operate is a finding, not a skip", async () => {
    // The shape the old rule dropped in silence: a row that carries no
    // control role at all announces nothing to anybody, and a sweep that
    // steps over it reports a clean panel.
    const stage = document.createElement("div");
    stage.innerHTML = `
      <div class="settings-toggle-row">
        <span class="settings-label">Ambient mist</span>
      </div>`;
    document.body.appendChild(stage);

    try {
      expect(disagreements(stage)).toEqual([
        'a toggle row exposes no control role (role="")',
      ]);
    } finally {
      stage.remove();
    }
  });
});
