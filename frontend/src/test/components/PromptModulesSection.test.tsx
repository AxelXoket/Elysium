/**
 * PromptModulesSection.test.tsx - the "Prompt modules" page in Settings.
 *
 * What this pins, each with a ground control (the thing absent, the check
 * reads nothing) and a positive control (the thing present, the check sees it):
 *
 *   MT-10  the import button stays off until a file is chosen AND the consent
 *          switch is on, and the line beside it says which is missing; the
 *          three warnings on screen are my English text byte for
 *          byte, from shared/prompt_module_warnings.json.
 *   9A.4   the counter "Active modules: {n}, about {x} tokens per message." is
 *          always on screen and counts only modules that are on, as the
 *          request will carry them (macros rendered, one separator each).
 *   Ek O 7 the roleplay layer is never sent while a module is on, and the
 *          page says so; reply length (A9) is live only when the layer
 *          actually goes (mode b), greyed out with the reason otherwise.
 *   H4     a refused toggle names the modules that do not fit.
 *   H3     "Turn on entries enabled in SillyTavern" asks the server for the
 *          whole set, and a set refused whole says nothing was turned on.
 *   15.3   delete asks first, in place of the actions, with the focus on the
 *          safe answer; Escape answers the innermost open thing only.
 *   15.2   loading, error and loaded are three different screens: the counter
 *          never states a number it does not have.
 *
 * The server is mocked at fetch: every assertion about what was sent reads
 * the request the page actually made.
 */
import { createHash } from "node:crypto";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useEffect, type ReactNode } from "react";

import warningsJson from "../../../../shared/prompt_module_warnings.json";
import limitsJson from "../../../../shared/prompt_module_limits.json";
import sanitizerVectors from "../../../../shared/sanitizer_vectors.json";

import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { PromptModulesSection } from "@/components/settings/PromptModulesSection";
import { AppSettingsDialog } from "@/components/settings/AppSettingsDialog";
import {
  GenerationSettingsProvider,
  useGenerationSettings,
} from "@/components/generation/GenerationSettingsContext";
import {
  LAYER_SETTINGS_UNREACHABLE,
  LAYER_WAITS_FOR_LIST,
  MODULE_LIST_UNREACHABLE,
} from "@/components/settings/PromptModulesSection";
import { IMPORT_CONSENT_LABEL } from "@/components/settings/PromptModuleImport";
import {
  IMPORT_HINTS,
  MODULE_COUNTER_CHECKING,
  MODULE_COUNTER_FAILED,
  PROMPT_MODULE_LIMITS,
  CLEAR_VARIABLES_LABEL,
  HISTORY_CLEANUP_HINT,
  HISTORY_CLEANUP_LABEL,
  NOT_IN_ORDER_NOTE,
  PROMPT_MODULE_WARNINGS,
  REGEX_UNSUPPORTED,
  badgeLabel,
  formatModuleCounter,
  placeholderLine,
  placeholderName,
  presetFieldName,
  secretName,
  triggerList,
} from "@/components/settings/promptModuleText";
import {
  OVER_BUDGET_FIX,
  OVER_BUDGET_FIX_VARIABLES,
  overBudgetSentence,
  PROMPT_MODULE_FILENAME_HEADER,
  PROMPT_MODULE_LIST_KEY,
} from "@/lib/api/promptModules";
import { formatCount } from "@/lib/formatCount";
import { hiddenFromReaders } from "@/test/helpers/accessibility";
import { CHARS_PER_TOKEN } from "@/lib/context/estimateContextUsage";
import { useUiStore } from "@/lib/store/uiStore";
import { getErrorMessage } from "@/lib/errors";
import { mockFetch } from "../mocks/api";
import { settingsFixture } from "../mocks/fixtures";

// ── My wording, as SHA-256 of the UTF-8 bytes ──────────────────
//
// My English wording of 26 Sep 2026, hashed by script, not by hand.
// I wrote the counter with {n} and {x} in it, so its hash is of the
// template as the shared file carries it.
const WORDING_SHA256 = {
  import_consent: "2638e39eda15020c583da9b08544300c92f850f34bfb4dcb673d1e2402fe44c5",
  counter: "3eea151465a9672c3a329657eba717d199ba29d28ee74592eb2a3a4818a6bd05",
  st_preset: "7b118f294f702a7a58e930eff1a2c9fb59ec4c8da835c8570d0ef489ec838b6d",
} as const;

/** The Turkish wording these texts replaced; the check must refuse it. */
const REPLACED_TURKISH_SHA256 = {
  import_consent: "296d83ea9d1d91abcf2315f93d5ac132bafbec857c8a813795e51036ccd60290",
  st_preset: "560e4f4b69fcf7d0f7e95deb58608d3e47fe146b3fc86d2a3b9d3866cf1e2574",
} as const;

const sha256 = (text: string) =>
  createHash("sha256").update(Buffer.from(text, "utf-8")).digest("hex");

/** A near miss: the same text with its closing period gone. */
const dropLastChar = (text: string) => text.slice(0, -1);

// ── Fixtures ────────────────────────────────────────────────────────────

type Row = Record<string, unknown>;

let nextId = 100;
function mod(over: Partial<Row> = {}): Row {
  nextId += 1;
  return {
    id: nextId,
    import_id: null,
    kind: "module",
    name: `Module ${nextId}`,
    content: "neutral fixture text",
    note: "",
    enabled: false,
    sort_order: nextId,
    revision: 1,
    source_type: "txt",
    source_filename: `m${nextId}.txt`,
    st_enabled: null,
    st_role: null,
    st_injection_depth: null,
    st_injection_order: null,
    st_injection_trigger: null,
    triggers: ["send", "regenerate"],
    badges: [],
    can_enable: true,
    blocked_reason: null,
    ...over,
  };
}

interface Setup {
  modules?: Row[];
  layer?: boolean;
  words?: number | null;
  extra?: Record<string, { status?: number; body: unknown }>;
}

/** Routes, most specific first: mockFetch matches by substring in order, and
 *  "/prompt-modules" is a substring of every route below it. */
function routes({ modules = [], layer = false, words = null, extra = {} }: Setup) {
  // A route in `extra` replaces the default of the same key, and keeps its
  // own place at the front (first match wins).
  const defaults: Record<string, { status?: number; body: unknown }> = {
    "GET /prompt-modules/settings": {
      body: { prompt_layer_enabled: layer, reply_length_words: words },
    },
    "PUT /prompt-modules/settings": {
      body: { prompt_layer_enabled: layer, reply_length_words: words },
    },
    "PATCH /prompt-modules/": { body: {} },
    "DELETE /prompt-modules/": { body: { ok: true } },
    "GET /prompt-modules": {
      body: { modules, imports: [], enabled_count: 0 },
    },
    "/settings": { body: settingsFixture },
  };
  return {
    ...extra,
    ...Object.fromEntries(Object.entries(defaults).filter(([key]) => !(key in extra))),
  };
}

/** Sets the reply cap through the provider's own setter, the way the
 *  generation settings dialog does, rather than through storage names. */
function MaxTokens({ value }: { value: number | null }) {
  const { setSetting } = useGenerationSettings();
  useEffect(() => {
    if (value != null) setSetting("max_tokens", value);
  }, [setSetting, value]);
  return null;
}

function renderPage(setup: Setup = {}, maxTokens: number | null = null) {
  const fetchMock = mockFetch(routes(setup));
  const wrapper = ({ children }: { children: ReactNode }) => (
    <GenerationSettingsProvider>
      <MaxTokens value={maxTokens} />
      {children}
    </GenerationSettingsProvider>
  );
  const view = renderWithQueryClient(<PromptModulesSection />, { wrapper });
  return { fetchMock, ...view };
}

type Call = { url: string; method: string; init: RequestInit };
function calls(fetchMock: ReturnType<typeof mockFetch>): Call[] {
  return fetchMock.mock.calls.map(([input, init]) => ({
    url: String(input),
    method: ((init as RequestInit | undefined)?.method ?? "GET").toUpperCase(),
    init: (init ?? {}) as RequestInit,
  }));
}
function sent(fetchMock: ReturnType<typeof mockFetch>, method: string, part: string) {
  return calls(fetchMock).filter((c) => c.method === method && c.url.includes(part));
}
const bodyOf = (c: Call) => JSON.parse(String(c.init.body));

async function waitForList() {
  await waitFor(() => expect(screen.queryAllByText("Loading…")).toHaveLength(0));
}

/** The always-on counter, by its role: it is the page's one live status. */
const counter = () => screen.getByRole("status");

/** Move, rename and delete sit behind the row's one "More" button. */
async function openActions(user: ReturnType<typeof userEvent.setup>, name: string) {
  await user.click(screen.getByRole("button", { name: `More actions for ${name}` }));
}

/** The layer switch, named by its label; its state line is its description. */
const layerSwitch = () => screen.getByRole("switch", { name: "Roleplay layer" });

function fileOf(name: string, text: string): File {
  return new File([text], name, { type: "application/octet-stream" });
}

const importButton = () => screen.getByRole("button", { name: /^Import$/ });
const consentBox = () => screen.getByRole("switch", { name: IMPORT_CONSENT_LABEL });
const fileInput = () => screen.getByLabelText("Choose a prompt module file");

beforeEach(() => {
  vi.restoreAllMocks();
  useUiStore.setState({ selectedChatId: null, selectedModelId: null });
});
afterEach(() => vi.restoreAllMocks());

// ── The shared texts ────────────────────────────────────────────────────

describe("my warning texts", () => {
  it("the shared file carries my bytes, all three", () => {
    expect(sha256(warningsJson.import_consent)).toBe(WORDING_SHA256.import_consent);
    expect(sha256(warningsJson.counter)).toBe(WORDING_SHA256.counter);
    expect(sha256(warningsJson.st_preset)).toBe(WORDING_SHA256.st_preset);
  });

  it("GROUND: the same check rejects a one-character near miss", () => {
    // Without this, a hash comparison that always passed would read as proof.
    expect(sha256(dropLastChar(warningsJson.import_consent))).not.toBe(
      WORDING_SHA256.import_consent,
    );
    expect(sha256(dropLastChar(warningsJson.counter))).not.toBe(WORDING_SHA256.counter);
    expect(sha256(dropLastChar(warningsJson.st_preset))).not.toBe(WORDING_SHA256.st_preset);
  });

  it("the texts are the English wording, not the Turkish they replaced", () => {
    expect(sha256(warningsJson.import_consent)).not.toBe(
      REPLACED_TURKISH_SHA256.import_consent,
    );
    expect(sha256(warningsJson.st_preset)).not.toBe(REPLACED_TURKISH_SHA256.st_preset);
    for (const text of Object.values(PROMPT_MODULE_WARNINGS)) {
      // Printable ASCII only: no Turkish letter survived the swap.
      expect(text).toMatch(/^[\x20-\x7e]+$/);
    }
  });

  it("the page's mirror is the shared file, byte for byte", () => {
    const { _why, ...texts } = warningsJson;
    expect(_why.length).toBeGreaterThan(0);
    expect(PROMPT_MODULE_WARNINGS).toEqual(texts);
    expect(Object.keys(texts).sort()).toEqual(
      ["counter", "import_consent", "st_preset"],
    );
  });

  it("the page's limits and upload header are the shared limits", () => {
    for (const [key, value] of Object.entries(PROMPT_MODULE_LIMITS)) {
      expect(limitsJson[key as keyof typeof limitsJson], key).toEqual(value);
    }
    expect(PROMPT_MODULE_FILENAME_HEADER).toBe(limitsJson.filename_header);
  });
});

// ── MT-10: consent gates the import ─────────────────────────────────────

describe("import (MT-10)", () => {
  it("GROUND: with no file chosen the button is off, consent or not", async () => {
    const user = userEvent.setup();
    renderPage();
    expect(importButton()).toBeDisabled();
    // A disabled button is never silent: it says what is missing.
    expect(importButton()).toHaveAccessibleDescription(IMPORT_HINTS.noFile);
    await user.click(consentBox());
    expect(consentBox()).toBeChecked();
    expect(importButton()).toBeDisabled();
    expect(importButton()).toHaveAccessibleDescription(IMPORT_HINTS.noFile);
  });

  it("the consent is the app's own switch row, not an OS checkbox", async () => {
    renderPage();
    // Ground: no native checkbox anywhere on the page.
    expect(screen.queryByRole("checkbox")).toBeNull();
    // Positive control: the switch is there, off, and a real button.
    expect(consentBox()).toHaveAttribute("aria-checked", "false");
    expect(consentBox().tagName).toBe("BUTTON");
  });

  it("a chosen file stays unimportable until the consent switch is on", async () => {
    const user = userEvent.setup();
    const { fetchMock } = renderPage({
      extra: {
        "POST /prompt-modules/import": {
          status: 201,
          body: {
            import_id: 7,
            modules: [mod({ name: "Pacing" })],
            report: { source_type: "txt", filename: "pacing.txt", modules_created: 1 },
          },
        },
      },
    });
    await user.upload(fileInput(), fileOf("pacing.txt", "Keep scenes short."));
    expect(screen.getByText("pacing.txt")).toBeInTheDocument();
    expect(importButton()).toBeDisabled();
    expect(importButton()).toHaveAccessibleDescription(IMPORT_HINTS.noConsent);
    // Clicking the disabled button sends nothing.
    await user.click(importButton());
    expect(sent(fetchMock, "POST", "/import")).toHaveLength(0);

    await user.click(consentBox());
    expect(importButton()).toBeEnabled();
    expect(importButton()).toHaveAccessibleDescription(IMPORT_HINTS.ready);
    await user.click(importButton());

    await screen.findByRole("region", { name: "Import report" });
    const posts = sent(fetchMock, "POST", "/prompt-modules/import");
    expect(posts).toHaveLength(1);
    const headers = posts[0].init.headers as Record<string, string>;
    expect(headers[PROMPT_MODULE_FILENAME_HEADER]).toBe("pacing.txt");
    // The file itself is the body: raw bytes, no multipart form around it.
    expect(posts[0].init.body).toBeInstanceOf(File);
    expect(headers["Content-Type"]).toBe("application/octet-stream");
    // Consent is for one file; the next one asks again.
    expect(consentBox()).not.toBeChecked();
    expect(importButton()).toBeDisabled();
  });

  it("the consent text on screen is the shared text, byte for byte", async () => {
    renderPage();
    expect(screen.getByTestId("prompt-module-consent-text").textContent).toBe(
      warningsJson.import_consent,
    );
    // And it is what describes the switch a reader lands on.
    expect(consentBox()).toHaveAccessibleName(IMPORT_CONSENT_LABEL);
    expect(consentBox()).toHaveAccessibleDescription(warningsJson.import_consent);
  });

  it("a non-ASCII file name travels percent-encoded", async () => {
    const user = userEvent.setup();
    const { fetchMock } = renderPage({
      extra: {
        "POST /prompt-modules/import": {
          status: 201,
          body: { import_id: 1, modules: [], report: { source_type: "md" } },
        },
      },
    });
    await user.upload(fileInput(), fileOf("kısa sahne.md", "text"));
    await user.click(consentBox());
    await user.click(importButton());
    await screen.findByRole("region", { name: "Import report" });
    const headers = sent(fetchMock, "POST", "/import")[0].init.headers as Record<string, string>;
    expect(decodeURIComponent(headers[PROMPT_MODULE_FILENAME_HEADER])).toBe("kısa sahne.md");
    expect(headers[PROMPT_MODULE_FILENAME_HEADER]).toMatch(/^[\x20-\x7e]+$/);
  });

  it("refuses an oversized or unknown file before sending it", async () => {
    const user = userEvent.setup({ applyAccept: false });
    const { fetchMock } = renderPage();
    const big = fileOf("big.txt", "a".repeat(limitsJson.txt_md_max_bytes + 1));
    await user.upload(fileInput(), big);
    await user.click(consentBox());
    expect(importButton()).toBeDisabled();
    expect(importButton()).toHaveAccessibleDescription(IMPORT_HINTS.badFile);
    const tooBig = screen.getByText(/over the 96 KB limit/);
    expect(tooBig.closest('[data-slot="collapse"]')).toHaveAttribute("data-open", "true");

    // Positive control: exactly at the cap is fine, and the refusal closes.
    await user.upload(fileInput(), fileOf("cap.txt", "a".repeat(limitsJson.txt_md_max_bytes)));
    expect(importButton()).toBeEnabled();
    expect(hiddenFromReaders(tooBig)).toBe(true);

    await user.upload(fileInput(), fileOf("notes.docx", "x"));
    expect(importButton()).toBeDisabled();
    expect(sent(fetchMock, "POST", "/import")).toHaveLength(0);
  });
});

// ── Warning 3 ───────────────────────────────────────────────────────────

describe("SillyTavern preset warning", () => {
  it("shows for a SillyTavern .json as soon as it is chosen, byte for byte", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.upload(
      fileInput(),
      fileOf("preset.json", JSON.stringify({ prompts: [], prompt_order: [] })),
    );
    const warning = screen.getByTestId("prompt-module-st-warning");
    await waitFor(() => expect(hiddenFromReaders(warning)).toBe(false));
    expect(warning.textContent).toBe(warningsJson.st_preset);
    // It arrives through the house Collapse, not in one frame.
    expect(warning.closest('[data-slot="collapse"]')).toHaveAttribute("data-open", "true");
  });

  it("GROUND: not for the app's own .json, and not for a .txt", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.upload(
      fileInput(),
      fileOf(
        "mine.json",
        JSON.stringify({ format: limitsJson.plain_json_format, version: 1, modules: [] }),
      ),
    );
    await screen.findByText("mine.json");
    await user.upload(fileInput(), fileOf("a.txt", "{\"prompts\": []}"));
    await screen.findByText("a.txt");
    // Held closed in its Collapse: in the DOM, gone for every reader.
    expect(hiddenFromReaders(screen.getByTestId("prompt-module-st-warning"))).toBe(true);
  });
});

// ── The import report ───────────────────────────────────────────────────

const ST_REPORT = {
  source_type: "st_chat_preset",
  filename: "preset.json",
  modules_created: 3,
  structure_rows: 1,
  all_start_off: true,
  warnings: ["st_preset_behaviour"],
  secrets_removed: ["$.proxy_password", "reverse_proxy"],
  order_source: "100001",
  per_character_orders_unused: 2,
  markers: ["chatHistory"],
  badges: { after_history: 1, never_triggers: 1 },
  never_triggered: [{ identifier: "imp", triggers: ["impersonate"] }],
  order_ids_missing_from_prompts: ["ghost"],
  invalid_entries: [],
  duplicate_identifiers: [],
  unknown_entry_keys: [],
  preset_fields: [
    { key: "temperature", group: "sampler", applied: false, value: 1.1 },
    { key: "reverse_proxy", group: "connection", applied: false, value_hidden: true },
  ],
  unprocessed_macros: { "{{getvar::x}}": 2 },
};

function stModules() {
  return [
    mod({ name: "Main", import_id: 9, source_type: "st_chat_preset", st_enabled: true }),
    mod({ name: "Tail", import_id: 9, source_type: "st_chat_preset", st_enabled: true, badges: ["after_history"] }),
    mod({
      name: "Imp", import_id: 9, source_type: "st_chat_preset", st_enabled: false,
      badges: ["never_triggers"], can_enable: false, blocked_reason: "never_triggers",
      triggers: [], st_injection_trigger: ["impersonate"],
    }),
  ];
}

async function importPreset(extra: Setup["extra"] = {}) {
  const user = userEvent.setup();
  const view = renderPage({
    extra: {
      "POST /prompt-modules/import": {
        status: 201,
        body: { import_id: 9, modules: stModules(), report: ST_REPORT },
      },
      ...extra,
    },
  });
  await user.upload(fileInput(), fileOf("preset.json", "{\"prompts\": []}"));
  await user.click(consentBox());
  await user.click(importButton());
  const report = await screen.findByRole("region", { name: "Import report" });
  return { user, report, ...view };
}

describe("import report", () => {
  it("folds each report group under a heading with its count", async () => {
    const { user, report } = await importPreset();
    const r = within(report);
    const toggle = r.getByRole("button", { name: /^Not made into modules, \d+ entries$/ });
    // Ground: folded by default, the lines are there but out of reach.
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(r.queryByRole("list", { name: "Entries not made into modules" })).toBeNull();
    // Positive control: one click opens that group, and only that one.
    await user.click(toggle);
    expect(r.getByRole("list", { name: "Entries not made into modules" })).toBeInTheDocument();
    expect(r.queryByRole("list", { name: "Preset fields" })).toBeNull();
    // The summary line is never folded.
    expect(r.getByTestId("prompt-module-report-created")).toHaveTextContent("3 modules created");
  });

  it("lists everything that did not come across as it was", async () => {
    const { user, report } = await importPreset();
    const r = within(report);
    for (const toggle of r.getAllByRole("button", { expanded: false })) {
      await user.click(toggle);
    }
    expect(r.getByTestId("prompt-module-report-st-warning").textContent).toBe(
      warningsJson.st_preset,
    );
    expect(r.getByTestId("prompt-module-report-created")).toHaveTextContent(
      "3 modules created, all off",
    );
    const notMade = r.getByRole("list", { name: "Entries not made into modules" });
    // Placeholders by what they held, not by SillyTavern's identifier.
    expect(notMade).toHaveTextContent(`${placeholderName("chatHistory")}: a SillyTavern placeholder`);
    expect(placeholderName("chatHistory")).not.toBe("chatHistory");
    // A sentence, with the trigger said in words.
    expect(notMade).toHaveTextContent(
      `imp: kept, but it only fires on ${triggerList(["impersonate"])}, which this app does not have`,
    );
    expect(notMade).toHaveTextContent("ghost: named in the order list");
    expect(r.getByRole("list", { name: "Badge counts" })).toHaveTextContent(
      `${badgeLabel("after_history")}: 1`,
    );
    expect(r.getByRole("list", { name: "Unprocessed macros" })).toHaveTextContent(
      "{{getvar::x}}: 2",
    );
    expect(r.getByTestId("prompt-module-order-source")).toHaveTextContent("(100001)");
    expect(r.getByTestId("prompt-module-order-source")).toHaveTextContent(
      "2 per-character order lists were not used",
    );
    const fields = r.getByRole("list", { name: "Preset fields" });
    // A field the page knows is named in words; an unknown one keeps its key.
    expect(fields).toHaveTextContent(`${presetFieldName("temperature")} 1.1: stored, not applied`);
    expect(presetFieldName("temperature")).not.toBe("temperature");
    expect(fields).toHaveTextContent("reverse_proxy: stored, value not shown");
    const secrets = r.getByRole("list", { name: "Removed secrets" });
    // A key the page knows is named in words; one caught by its shape keeps
    // its own name. Ground: the gloss really differs from the raw key.
    expect(secrets).toHaveTextContent(secretName("reverse_proxy"));
    expect(secretName("reverse_proxy")).not.toBe("reverse_proxy");
    expect(within(secrets).queryByText("reverse_proxy")).toBeNull();
    expect(secrets).toHaveTextContent("$.proxy_password");
  });

  it("the report arrives and leaves through the house Collapse", async () => {
    const { user, report } = await importPreset();
    const host = report.closest('[data-slot="collapse"]');
    expect(host).toHaveAttribute("data-open", "true");
    await user.click(within(report).getByRole("button", { name: "Close report" }));
    // The focus goes to the start of the next import, not to the page.
    expect(screen.getByRole("button", { name: "Choose file" })).toHaveFocus();
    // Held while it closes, and gone for readers at once.
    expect(host).toHaveAttribute("data-open", "false");
    expect(hiddenFromReaders(report)).toBe(true);
  });

  it("GROUND: a plain text import shows no SillyTavern warning and no empty sections", async () => {
    const user = userEvent.setup();
    renderPage({
      extra: {
        "POST /prompt-modules/import": {
          status: 201,
          body: {
            import_id: 3,
            modules: [mod({ name: "Plain" })],
            report: { source_type: "txt", filename: "plain.txt", modules_created: 1 },
          },
        },
      },
    });
    await user.upload(fileInput(), fileOf("plain.txt", "x"));
    await user.click(consentBox());
    await user.click(importButton());
    const report = await screen.findByRole("region", { name: "Import report" });
    expect(within(report).queryByTestId("prompt-module-report-st-warning")).toBeNull();
    expect(within(report).getByTestId("prompt-module-report-created")).toHaveTextContent(
      "1 module created, off",
    );
    expect(within(report).queryByRole("list", { name: "Badge counts" })).toBeNull();
    expect(
      within(report).queryByRole("button", { name: /Turn on entries enabled in SillyTavern/ }),
    ).toBeNull();
  });

  it("offers the SillyTavern button once, on the preset's group, never in the report", async () => {
    const { report } = await importPreset({
      "GET /prompt-modules/settings": {
        body: { prompt_layer_enabled: false, reply_length_words: null },
      },
      "GET /prompt-modules": { body: { modules: stModules(), imports: [], enabled_count: 0 } },
    });
    // Ground: the report has no copy of it.
    expect(
      within(report).queryByRole("button", { name: /Turn on entries enabled in SillyTavern/ }),
    ).toBeNull();
    // Positive control: the list's group has it, and it is the only one.
    await waitFor(() =>
      expect(
        screen.getAllByRole("button", { name: /Turn on entries enabled in SillyTavern/ }),
      ).toHaveLength(1),
    );
    const group = screen.getByRole("group", { name: /^From / });
    expect(
      within(group).getByRole("button", { name: /Turn on entries enabled in SillyTavern/ }),
    ).toBeInTheDocument();
  });

  it("turns on what SillyTavern had on, as one request with the window", async () => {
    useUiStore.setState({ selectedChatId: 4, selectedModelId: "vendor/model-a" });
    const user = userEvent.setup();
    const { fetchMock } = renderPage({
      modules: stModules(),
      extra: {
        "POST /prompt-modules/enable-preset-defaults": {
          body: { enabled_ids: [1, 2], left_out: [] },
        },
      },
    });
    await waitForList();
    // Two of the three were on in SillyTavern and can fire here.
    await user.click(
      screen.getByRole("button", { name: "Turn on entries enabled in SillyTavern (2)" }),
    );
    await waitFor(() =>
      expect(sent(fetchMock, "POST", "/enable-preset-defaults")).toHaveLength(1),
    );
    const body = bodyOf(sent(fetchMock, "POST", "/enable-preset-defaults")[0]);
    expect(body.import_id).toBe(9);
    expect(body.chat_id).toBe(4);
    expect(body.model_id).toBe("vendor/model-a");
    expect(body.max_tokens).toBeGreaterThan(0);
    expect(body.context_budget_tokens).toBeGreaterThan(0);
  });

  it("lists the entries SillyTavern had on that stay off here, by name", async () => {
    const user = userEvent.setup();
    const rows = stModules();
    const imp = rows[2];
    renderPage({
      modules: rows.map((r) => (r === imp ? { ...r, st_enabled: true } : r)),
      extra: {
        "POST /prompt-modules/enable-preset-defaults": {
          body: {
            enabled_ids: [rows[0].id, rows[1].id],
            left_out: [{ id: imp.id, reason: "never_triggers" }],
          },
        },
      },
    });
    await waitForList();
    await user.click(
      screen.getByRole("button", { name: /Turn on entries enabled in SillyTavern/ }),
    );
    const left = await screen.findByRole("list", { name: "Left off" });
    expect(left).toHaveTextContent(
      "Imp: it only fired on a kind of reply this app does not have",
    );
  });

  it("GROUND: nothing left off, no list", async () => {
    const user = userEvent.setup();
    const { fetchMock } = renderPage({
      modules: stModules(),
      extra: {
        "POST /prompt-modules/enable-preset-defaults": {
          body: { enabled_ids: [1], left_out: [] },
        },
      },
    });
    await waitForList();
    await user.click(
      screen.getByRole("button", { name: /Turn on entries enabled in SillyTavern/ }),
    );
    await waitFor(() =>
      expect(sent(fetchMock, "POST", "/enable-preset-defaults")).toHaveLength(1),
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Turn on entries/ })).toBeEnabled(),
    );
    expect(screen.queryByRole("list", { name: "Left off" })).toBeNull();
  });

  it("a set refused whole says nothing was turned on and names the modules", async () => {
    const user = userEvent.setup();
    renderPage({
      modules: stModules(),
      extra: {
        "POST /prompt-modules/enable-preset-defaults": {
          status: 409,
          body: {
            detail: "prompt_modules_over_budget",
            modules: [
              { id: 1, name: "Main", tokens: 3000 },
              { id: 2, name: "Tail", tokens: 2100 },
            ],
            total_tokens: 5101,
            room_tokens: 4549,
          },
        },
      },
    });
    await waitForList();
    await user.click(
      screen.getByRole("button", { name: /Turn on entries enabled in SillyTavern/ }),
    );
    const notice = await screen.findByRole("alert");
    // The list keeps its list role: the alert sits around it, not on it.
    const list = within(notice).getByRole("list", { name: "Modules over Context budget" });
    expect(within(list).getAllByRole("listitem").length).toBeGreaterThan(2);
    expect(notice).toHaveTextContent("Nothing was turned on.");
    // Counts in the one grouped form ("3,000"), and the fix named by its label.
    expect(notice).toHaveTextContent(`Main, about ${formatCount(3000)} tokens`);
    expect(formatCount(3000)).toBe("3,000");
    expect(notice).toHaveTextContent(`Tail, about ${formatCount(2100)} tokens`);
    expect(notice).toHaveTextContent(`About ${formatCount(5101)} tokens in all.`);
    expect(notice).toHaveTextContent(OVER_BUDGET_FIX);
  });
});

// ── The counter ─────────────────────────────────────────────────────────

/** The request's cost of a set of plain modules (no macros, nothing for the
 *  sanitizer to touch): text plus one shared separator each, at the app's
 *  characters-per-token. Constants imported, not retyped. */
function expectedTokens(contents: string[]): number {
  const chars = contents.reduce(
    (sum, c) => sum + c.length + limitsJson.module_separator.length,
    0,
  );
  return Math.ceil(chars / CHARS_PER_TOKEN);
}

describe("counter (9A.4 warning 2)", () => {
  it("GROUND: with no modules it is on screen and reads zero", async () => {
    renderPage();
    await waitForList();
    expect(counter().textContent).toBe(
      "Active modules: 0, about 0 tokens per message.",
    );
  });

  it("counts only the modules that are on, as the request carries them", async () => {
    const on1 = "a".repeat(300);
    const on2 = "b".repeat(151);
    renderPage({
      modules: [
        mod({ content: on1, enabled: true }),
        mod({ content: "c".repeat(5000), enabled: false }),
        mod({ content: on2, enabled: true }),
        mod({ kind: "marker", name: "chatHistory", content: "", can_enable: false }),
      ],
    });
    await waitForList();
    expect(counter().textContent).toBe(formatModuleCounter(2, expectedTokens([on1, on2])));
    // The template is mine with only the two numbers changed.
    expect(counter().textContent).toBe(
      warningsJson.counter.replace("{n}", "2").replace("{x}", String(expectedTokens([on1, on2]))),
    );
  });

  it("counts the sanitized text, the text the request carries", async () => {
    // The first shared attack vector whose output differs in length from its
    // input: the counter must price the output. Taken from the vectors file
    // both sanitizers are held to, not from the function under test.
    const vector = sanitizerVectors.attack.find((v) => v.out.length !== v.in.length);
    expect(vector, "no length-changing vector to test with").toBeDefined();
    renderPage({ modules: [mod({ content: vector!.in, enabled: true })] });
    await waitForList();
    expect(counter().textContent).toBe(
      formatModuleCounter(1, expectedTokens([vector!.out])),
    );
    // Ground: the raw text would have priced differently, or this proved nothing.
    expect(expectedTokens([vector!.in])).not.toBe(expectedTokens([vector!.out]));
  });

  it("counts only modules that go with an ordinary message (A4)", async () => {
    const send = "s".repeat(90);
    renderPage({
      layer: true,
      modules: [
        mod({ content: send, enabled: true, triggers: ["send", "regenerate"] }),
        mod({
          content: "r".repeat(900),
          enabled: true,
          triggers: ["regenerate"],
          badges: ["trigger_regenerate_only"],
        }),
      ],
    });
    await waitForList();
    expect(counter().textContent).toBe(
      formatModuleCounter(1, expectedTokens([send])),
    );
    // Still on, so the layer still does not go: mode a counts every module on.
    expect(screen.getByTestId("prompt-layer-state")).toHaveTextContent(
      "Not sent now: 2 modules are on.",
    );
  });

  it("GROUND: a regenerate-only module that is off changes nothing", async () => {
    renderPage({
      layer: true,
      modules: [mod({ content: "r".repeat(900), enabled: false, triggers: ["regenerate"] })],
    });
    await waitForList();
    expect(counter().textContent).toBe(
      formatModuleCounter(0, 0),
    );
    expect(screen.getByTestId("prompt-layer-state")).not.toHaveTextContent(/not sent/i);
  });

  it("a macro comment costs nothing: it never reaches the model", async () => {
    const text = "x".repeat(30);
    renderPage({
      modules: [
        mod({ content: `${text}{{// ${"long author note ".repeat(40)}}}`, enabled: true }),
      ],
    });
    await waitForList();
    expect(counter().textContent).toBe(
      formatModuleCounter(1, expectedTokens([text])),
    );
  });
});

describe("counter while loading and after a failed load (15.2)", () => {
  it("says it is checking while the list is on its way, and no number", async () => {
    // A list request that never answers; everything else as usual.
    const base = mockFetch(routes({}));
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) =>
        String(input).endsWith("/prompt-modules")
          ? new Promise<Response>(() => undefined)
          : base(input, init),
      ),
    );
    renderWithQueryClient(<PromptModulesSection />, {
      wrapper: ({ children }) => (
        <GenerationSettingsProvider>{children}</GenerationSettingsProvider>
      ),
    });
    expect(counter().textContent).toBe(MODULE_COUNTER_CHECKING);
    expect(counter().textContent).not.toMatch(/\d/);
    // The layer controls wait for the list too: no switch showing a guess.
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByRole("switch", { name: "Roleplay layer" })).toBeNull();
  });

  it("says it could not load when the list fails, never 0 modules", async () => {
    const on = mod({ enabled: true });
    renderPage({
      modules: [on],
      extra: { "GET /prompt-modules/settings": { body: { prompt_layer_enabled: true, reply_length_words: null } }, "GET /prompt-modules": { status: 500, body: { detail: "internal_error" } } },
    });
    await waitFor(() => expect(counter().textContent).toBe(MODULE_COUNTER_FAILED));
    expect(counter().textContent).not.toBe(formatModuleCounter(0, 0));
    // Voice's warning language, and the layer is not offered on a guess.
    const alerts = screen.getAllByRole("alert");
    expect(alerts.map((a) => a.textContent)).toContain(MODULE_LIST_UNREACHABLE);
    // Said once: the layer section does not repeat the list's failure.
    expect(alerts.filter((a) => a.textContent === MODULE_LIST_UNREACHABLE)).toHaveLength(1);
    expect(alerts.map((a) => a.textContent)).toContain(LAYER_WAITS_FOR_LIST);
    expect(screen.queryByRole("switch", { name: "Roleplay layer" })).toBeNull();
  });

  it("says the layer settings could not load, and shows no guessed switch", async () => {
    renderPage({
      extra: { "GET /prompt-modules/settings": { status: 500, body: { detail: "internal_error" } } },
    });
    await waitFor(() =>
      expect(screen.getAllByRole("alert").map((a) => a.textContent)).toContain(
        LAYER_SETTINGS_UNREACHABLE,
      ),
    );
    expect(screen.queryByRole("switch", { name: "Roleplay layer" })).toBeNull();
    expect(screen.queryByRole("radiogroup", { name: "Reply length" })).toBeNull();
    // Positive control: the list itself still loaded and counts.
    expect(counter().textContent).toBe(formatModuleCounter(0, 0));
  });

  it("GROUND: once both load, the numbers and the layer are there", async () => {
    renderPage({ layer: true });
    await waitForList();
    await waitFor(() => expect(layerSwitch()).toBeEnabled());
    expect(counter().textContent).toBe(formatModuleCounter(0, 0));
    expect(screen.queryAllByRole("alert")).toHaveLength(0);
  });
});

// ── The list ────────────────────────────────────────────────────────────

describe("module list", () => {
  it("turning a module on sends the open chat's window with it", async () => {
    useUiStore.setState({ selectedChatId: 12, selectedModelId: "vendor/model-b" });
    const user = userEvent.setup();
    const alpha = mod({ name: "Alpha" });
    const { fetchMock } = renderPage({ modules: [alpha] });
    await waitForList();
    const toggle = screen.getByRole("switch", { name: "Alpha" });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    await user.click(toggle);
    await waitFor(() => expect(sent(fetchMock, "PATCH", `/prompt-modules/${alpha.id}`)).toHaveLength(1));
    const body = bodyOf(sent(fetchMock, "PATCH", `/prompt-modules/${alpha.id}`)[0]);
    expect(body).toMatchObject({ enabled: true, chat_id: 12, model_id: "vendor/model-b" });
    expect(body.context_budget_tokens).toBeGreaterThan(0);
  });

  it("GROUND: a rename carries no window", async () => {
    const user = userEvent.setup();
    const alpha = mod({ name: "Alpha" });
    const { fetchMock } = renderPage({ modules: [alpha] });
    await waitForList();
    await openActions(user, "Alpha");
    await user.click(screen.getByRole("button", { name: "Rename Alpha" }));
    const input = screen.getByRole("textbox", { name: "New name for Alpha" });
    // The name swaps to the box through the house fade, not in one frame.
    expect(input.closest('[data-slot="fade-in"]')).not.toBeNull();
    await user.clear(input);
    await user.type(input, "Beta{Enter}");
    await waitFor(() => expect(sent(fetchMock, "PATCH", "/prompt-modules/")).toHaveLength(1));
    expect(bodyOf(sent(fetchMock, "PATCH", "/prompt-modules/")[0])).toEqual({ name: "Beta" });
  });

  it("Enter in the rename box saves once and does not reopen the box", async () => {
    const user = userEvent.setup();
    const alpha = mod({ name: "Alpha" });
    const { fetchMock } = renderPage({ modules: [alpha] });
    await waitForList();
    await openActions(user, "Alpha");
    await user.click(screen.getByRole("button", { name: "Rename Alpha" }));
    const input = screen.getByRole("textbox", { name: "New name for Alpha" });
    await user.clear(input);
    await user.type(input, "Beta");
    // A real key: keydown, keypress and keyup, as the browser sends them.
    await user.keyboard("{Enter}");
    await waitFor(() => expect(sent(fetchMock, "PATCH", "/prompt-modules/")).toHaveLength(1));
    // The box is gone and stays gone; the focus is back on Rename.
    expect(screen.queryByRole("textbox", { name: /^New name for/ })).toBeNull();
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByRole("textbox", { name: /^New name for/ })).toBeNull();
    expect(screen.getByRole("button", { name: "Rename Alpha" })).toHaveFocus();
    // Positive control: Rename itself still opens the box.
    await user.keyboard("{Enter}");
    expect(screen.getByRole("textbox", { name: "New name for Alpha" })).toBeInTheDocument();
  });

  it("while renaming, the first Tab from the box reaches Save name", async () => {
    const user = userEvent.setup();
    renderPage({ modules: [mod({ name: "Alpha" })] });
    await waitForList();
    await openActions(user, "Alpha");
    await user.click(screen.getByRole("button", { name: "Rename Alpha" }));
    expect(screen.getByRole("textbox", { name: "New name for Alpha" })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("button", { name: "Save name" })).toHaveFocus();
    // Ground: outside a rename the switch and the disclosure are live.
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("switch", { name: "Alpha" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "More actions for Alpha" })).toBeEnabled();
  });

  it("the switch ends the row, after the name, before the disclosure", async () => {
    renderPage({ modules: [mod({ name: "Alpha" })] });
    await waitForList();
    const name = screen.getByText("Alpha");
    const sw = screen.getByRole("switch", { name: "Alpha" });
    const more = screen.getByRole("button", { name: "More actions for Alpha" });
    const follows = (a: Node, b: Node) =>
      Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
    expect(follows(name, sw)).toBe(true);
    expect(follows(sw, more)).toBe(true);
  });

  it("after a delete the focus goes to the next module, never to the page", async () => {
    const user = userEvent.setup();
    const one = mod({ name: "One", sort_order: 0 });
    const two = mod({ name: "Two", sort_order: 1 });
    renderPage({ modules: [one, two] });
    await waitForList();
    await openActions(user, "One");
    await user.click(screen.getByRole("button", { name: "Delete One" }));
    await user.click(
      within(screen.getByRole("group", { name: "Confirm delete" })).getByRole("button", {
        name: "Delete",
      }),
    );
    await waitFor(() => expect(screen.getByRole("switch", { name: "Two" })).toHaveFocus());
    expect(document.activeElement).not.toBe(document.body);
  });

  it("after deleting the last module the focus goes to Choose file", async () => {
    const user = userEvent.setup();
    renderPage({ modules: [mod({ name: "Solo" })] });
    await waitForList();
    await openActions(user, "Solo");
    await user.click(screen.getByRole("button", { name: "Delete Solo" }));
    await user.click(
      within(screen.getByRole("group", { name: "Confirm delete" })).getByRole("button", {
        name: "Delete",
      }),
    );
    await waitFor(() => expect(screen.getByRole("button", { name: "Choose file" })).toHaveFocus());
  });

  it("a refused toggle names the modules that do not fit", async () => {
    const user = userEvent.setup();
    const alpha = mod({ name: "Alpha" });
    renderPage({
      modules: [alpha],
      extra: {
        [`PATCH /prompt-modules/${alpha.id}`]: {
          status: 409,
          body: {
            detail: "prompt_modules_over_budget",
            modules: [{ id: alpha.id, name: "Alpha", tokens: 4800 }],
            total_tokens: 4801,
            room_tokens: 4549,
          },
        },
      },
    });
    await waitForList();
    expect(screen.queryByRole("alert")).toBeNull();
    await user.click(screen.getByRole("switch", { name: "Alpha" }));
    const notice = await screen.findByRole("alert");
    expect(notice).toHaveTextContent(`Alpha, about ${formatCount(4800)} tokens`);
    expect(notice).toHaveTextContent(OVER_BUDGET_FIX);
    expect(notice.closest('[data-slot="collapse"]')).toHaveAttribute("data-open", "true");
  });

  it("GROUND: any other refusal is its own sentence, not the budget notice", async () => {
    const user = userEvent.setup();
    const alpha = mod({ name: "Alpha" });
    renderPage({
      modules: [alpha],
      extra: {
        [`PATCH /prompt-modules/${alpha.id}`]: {
          status: 409,
          body: { detail: "prompt_module_cannot_enable", reason: "never_triggers" },
        },
      },
    });
    await waitForList();
    await user.click(screen.getByRole("switch", { name: "Alpha" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      getErrorMessage("prompt_module_cannot_enable"),
    );
    expect(screen.queryByRole("list", { name: "Modules over Context budget" })).toBeNull();
  });

  it("an entry that never fires here cannot be switched on, says why, and offers no copy", async () => {
    const [, , imp] = stModules();
    const { fetchMock } = renderPage({ modules: [imp] });
    await waitForList();
    expect(screen.getByRole("switch", { name: "Imp" })).toBeDisabled();
    // The reason is the switch's own description, not a caption nearby.
    expect(screen.getByRole("switch", { name: "Imp" })).toHaveAccessibleDescription(
      /only fired on a kind of reply this app does not have/,
    );
    expect(screen.getByText("Never fires in this app: impersonate")).toBeInTheDocument();
    // The server has no copy route (test_modules_privacy pins its absence),
    // so the page must not offer one: no such button, and no request to it.
    expect(screen.queryByRole("button", { name: /copy/i })).toBeNull();
    expect(sent(fetchMock, "POST", `/${imp.id}/copy`)).toHaveLength(0);
  });

  it("GROUND: an ordinary module has no blocked reason and its switch works", async () => {
    renderPage({ modules: [mod({ name: "Plain" })] });
    await waitForList();
    expect(screen.getByRole("switch", { name: "Plain" })).toBeEnabled();
    expect(screen.getByRole("switch", { name: "Plain" })).toHaveAccessibleDescription("");
    expect(screen.queryByText(/only fired on a kind of reply this app does not have/)).toBeNull();
  });

  it("moves a module by position, and the ends cannot move past themselves", async () => {
    const user = userEvent.setup();
    const first = mod({ name: "First", sort_order: 0 });
    const second = mod({ name: "Second", sort_order: 1 });
    const { fetchMock } = renderPage({ modules: [second, first] });
    await waitForList();
    await openActions(user, "First");
    await openActions(user, "Second");
    expect(screen.getByRole("button", { name: "Move First up" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Move Second down" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Move First down" }));
    await waitFor(() => expect(sent(fetchMock, "PATCH", `/${first.id}`)).toHaveLength(1));
    expect(bodyOf(sent(fetchMock, "PATCH", `/${first.id}`)[0])).toEqual({ sort_order: 1 });
  });

  it("each row shows one action besides its switch; the rest open under it", async () => {
    const user = userEvent.setup();
    renderPage({ modules: [mod({ name: "Solo" })] });
    await waitForList();
    const row = screen.getByTestId(/^prompt-module-\d+$/);
    const more = screen.getByRole("button", { name: "More actions for Solo" });
    // Ground: closed, the row offers the switch and "More", nothing else.
    expect(more).toHaveAttribute("aria-expanded", "false");
    expect(within(row).getAllByRole("switch")).toHaveLength(1);
    expect(within(row).getAllByRole("button").map((b) => b.getAttribute("aria-label")))
      .toEqual(["More actions for Solo"]);
    // Positive control: open, the rare actions are there, inside a Collapse.
    await user.click(more);
    expect(more).toHaveAttribute("aria-expanded", "true");
    const actions = screen.getByRole("group", { name: "Actions for Solo" });
    expect(actions.closest('[data-slot="collapse"]')).toHaveAttribute("data-open", "true");
    for (const name of ["Move Solo up", "Move Solo down", "Rename Solo", "Delete Solo"]) {
      expect(within(actions).getByRole("button", { name })).toBeInTheDocument();
    }
  });

  it("delete asks in the page first, and never through window.confirm", async () => {
    const confirmSpy = vi.spyOn(window, "confirm");
    const user = userEvent.setup();
    const gone = mod({ name: "Gone" });
    const { fetchMock } = renderPage({ modules: [gone] });
    await waitForList();
    await openActions(user, "Gone");

    await user.click(screen.getByRole("button", { name: "Delete Gone" }));
    const ask = screen.getByRole("group", { name: "Confirm delete" });
    expect(ask.closest('[data-slot="fade-in"]')).not.toBeNull();
    // The question replaces the trigger, and the focus lands on the safe answer.
    expect(screen.queryByRole("button", { name: "Delete Gone" })).toBeNull();
    expect(within(ask).getByRole("button", { name: "Keep" })).toHaveFocus();
    expect(sent(fetchMock, "DELETE", "/prompt-modules/")).toHaveLength(0);

    await user.click(within(ask).getByRole("button", { name: "Keep" }));
    expect(screen.queryByRole("group", { name: "Confirm delete" })).toBeNull();
    expect(sent(fetchMock, "DELETE", "/prompt-modules/")).toHaveLength(0);
    // Focus goes back to the trigger it came from, not to the page.
    await waitFor(() => expect(screen.getByRole("button", { name: "Delete Gone" })).toHaveFocus());

    // Escape answers the question the same way.
    await user.click(screen.getByRole("button", { name: "Delete Gone" }));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("group", { name: "Confirm delete" })).toBeNull();
    await waitFor(() => expect(screen.getByRole("button", { name: "Delete Gone" })).toHaveFocus());
    expect(sent(fetchMock, "DELETE", "/prompt-modules/")).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: "Delete Gone" }));
    await user.click(
      within(screen.getByRole("group", { name: "Confirm delete" })).getByRole("button", {
        name: "Delete",
      }),
    );
    await waitFor(() => expect(sent(fetchMock, "DELETE", `/prompt-modules/${gone.id}`)).toHaveLength(1));
    expect(confirmSpy).not.toHaveBeenCalled();
  });

  it("keeps a preset's rows together with its placeholders and SillyTavern state", async () => {
    const rows = [
      mod({ name: "Main", import_id: 9, source_type: "st_chat_preset", st_enabled: true, source_filename: "preset.json" }),
      mod({ kind: "marker", name: "chatHistory", import_id: 9, source_type: "st_chat_preset", can_enable: false, content: "" }),
      mod({ name: "Tail", import_id: 9, source_type: "st_chat_preset", st_enabled: false, badges: ["after_history"] }),
    ];
    renderPage({ modules: rows });
    await waitForList();
    // One group, named by its file, holding the button and the placeholder.
    const group = screen.getByRole("group", { name: "From preset.json" });
    expect(within(group).getByText("From preset.json")).toBeInTheDocument();
    // The placeholder reads as a plain sentence, not SillyTavern's identifier.
    expect(within(group).getByTestId("prompt-module-marker").textContent).toBe(
      placeholderLine("chatHistory"),
    );
    expect(within(group).queryByText(/chatHistory/)).toBeNull();
    expect(within(group).getByText("SillyTavern: on")).toBeInTheDocument();
    expect(within(group).getByText("SillyTavern: off")).toBeInTheDocument();
    expect(
      within(group).getByRole("button", { name: "Turn on entries enabled in SillyTavern (1)" }),
    ).toBeInTheDocument();
  });

  it("GROUND: with no modules it says so and shows no switch", async () => {
    renderPage();
    await waitForList();
    expect(screen.getByTestId("prompt-module-empty")).toBeInTheDocument();
    await waitFor(() => expect(layerSwitch()).toBeInTheDocument());
    // Only the page's own two switches: consent and the layer. No module row.
    expect(screen.getAllByRole("switch")).toEqual([consentBox(), layerSwitch()]);
  });
});

// ── Layer and reply length (Ek O 7-8, A9) ───────────────────────────────

const lengthRadios = () =>
  within(screen.getByRole("radiogroup", { name: "Reply length" })).getAllByRole("radio");
const lengthGroup = () => screen.getByRole("radiogroup", { name: "Reply length" });

/** Every visible sentence on the page that says the layer is not sent. */
const notSentLines = () =>
  screen.queryAllByText(/not (being )?sent/i).filter((n) => !hiddenFromReaders(n));

describe("roleplay layer and reply length", () => {
  it("mode c (no module, layer off): length greyed out with its reason", async () => {
    renderPage({ layer: false });
    await waitForList();
    await waitFor(() => expect(layerSwitch()).toBeEnabled());
    expect(layerSwitch()).toHaveAttribute("aria-checked", "false");
    for (const radio of lengthRadios()) expect(radio).toBeDisabled();
    // The reason is the group's own description line.
    expect(lengthGroup()).toHaveAccessibleDescription("Part of the roleplay layer, which is off.");
    expect(notSentLines()).toHaveLength(0);
  });

  it("mode b (no module, layer on): length is live and saves both settings together", async () => {
    const user = userEvent.setup();
    const { fetchMock } = renderPage({ layer: true, words: null });
    await waitForList();
    await waitFor(() => expect(lengthRadios()[1]).toBeEnabled());
    expect(lengthGroup()).toHaveAccessibleDescription(
      "Adaptive sends no length line; the card decides.",
    );
    expect(lengthRadios()[0]).toHaveAttribute("aria-checked", "true");
    const allowed = limitsJson.reply_length_words_allowed;
    // Short labels, so the choices fit beside the label at the dialog's width.
    expect(lengthRadios().map((r) => r.textContent)).toEqual([
      "Adaptive",
      ...allowed.map((n) => `${formatCount(n)} words`),
    ]);
    await user.click(lengthRadios()[1]);
    await waitFor(() => expect(sent(fetchMock, "PUT", "/prompt-modules/settings")).toHaveLength(1));
    expect(bodyOf(sent(fetchMock, "PUT", "/prompt-modules/settings")[0])).toEqual({
      prompt_layer_enabled: true,
      reply_length_words: allowed[0],
    });
  });

  it("mode a (a module on, layer on): the layer row says it is not sent, once", async () => {
    renderPage({ layer: true, modules: [mod({ enabled: true })] });
    await waitForList();
    await waitFor(() => expect(layerSwitch()).toBeEnabled());
    // Said by the row itself, in its description line...
    expect(layerSwitch()).toHaveAccessibleDescription(
      /^Not sent now: 1 module is on\. It comes back when the last one is turned off\.$/,
    );
    // ...and nowhere else on the page (it used to be said three times).
    expect(notSentLines()).toHaveLength(1);
    for (const radio of lengthRadios()) expect(radio).toBeDisabled();
    expect(lengthGroup()).toHaveAccessibleDescription("Part of the roleplay layer.");
  });

  it("a blank module that is on does not count: the layer is still sent (mode b)", async () => {
    // The server's list_enabled skips it (store.block_reason), so the page
    // must not tell the user the layer is off. U+0085 is blank to the server
    // and not to JS trim().
    renderPage({ layer: true, modules: [mod({ enabled: true, content: " " })] });
    await waitForList();
    await waitFor(() => expect(lengthRadios()[1]).toBeEnabled());
    expect(notSentLines()).toHaveLength(0);
    expect(counter().textContent).toBe(formatModuleCounter(0, 0));
  });

  it("mode a with the layer switch off: no not-sent line, length still greyed out", async () => {
    renderPage({ layer: false, modules: [mod({ enabled: true }), mod({ enabled: true })] });
    await waitForList();
    await waitFor(() => expect(layerSwitch()).toBeEnabled());
    expect(notSentLines()).toHaveLength(0);
    expect(lengthGroup()).toHaveAccessibleDescription("Part of the roleplay layer.");
    for (const radio of lengthRadios()) expect(radio).toBeDisabled();
  });

  it("the section title says when the layer applies, even before anything loads", async () => {
    renderPage();
    expect(screen.getByRole("heading", { level: 3, name: "When no module is on" })).toBeInTheDocument();
  });

  it("flipping the layer keeps the stored length", async () => {
    const user = userEvent.setup();
    const { fetchMock } = renderPage({ layer: false, words: 300 });
    await waitForList();
    await waitFor(() => expect(layerSwitch()).toBeEnabled());
    await user.click(layerSwitch());
    await waitFor(() => expect(sent(fetchMock, "PUT", "/settings")).toHaveLength(1));
    expect(bodyOf(sent(fetchMock, "PUT", "/settings")[0])).toEqual({
      prompt_layer_enabled: true,
      reply_length_words: 300,
    });
  });

  it("warns when the chosen length cannot fit the reply cap (A9 condition 3)", async () => {
    renderPage({ layer: true, words: 300 }, 512);
    await waitForList();
    const warning = await screen.findByTestId("reply-length-max-tokens");
    expect(warning).toHaveTextContent("replies stop at 512");
    // It names the control by its label in the Generation settings dialog.
    expect(warning).toHaveTextContent("Max new tokens");
    expect(warning.closest('[data-slot="collapse"]')).toHaveAttribute("data-open", "true");
  });

  it("GROUND: no cap warning when it fits, or when length is not live", async () => {
    renderPage({ layer: true, words: 150 }, 4096);
    await waitForList();
    await waitFor(() => expect(lengthRadios()[1]).toBeEnabled());
    expect(screen.queryByTestId("reply-length-max-tokens")).toBeNull();
  });
});

// ── The hook in the Settings dialog ─────────────────────────────────────

function renderDialog(setup: Setup = {}) {
  const onOpenChange = vi.fn();
  mockFetch(routes(setup));
  useUiStore.setState({ settingsInitialPage: "prompt" });
  renderWithQueryClient(<AppSettingsDialog open onOpenChange={onOpenChange} />, {
    wrapper: ({ children }) => (
      <GenerationSettingsProvider>{children}</GenerationSettingsProvider>
    ),
  });
  return { onOpenChange };
}

describe("Settings dialog", () => {
  afterEach(() => useUiStore.setState({ settingsInitialPage: null }));

  it("has its own Prompt modules heading that opens this page", async () => {
    const user = userEvent.setup();
    mockFetch(routes({}));
    useUiStore.setState({ settingsInitialPage: null });
    renderWithQueryClient(<AppSettingsDialog open onOpenChange={() => undefined} />, {
      wrapper: ({ children }) => (
        <GenerationSettingsProvider>{children}</GenerationSettingsProvider>
      ),
    });
    expect(screen.queryByRole("status")).toBeNull();
    await user.click(await screen.findByText("Prompt modules"));
    expect(await screen.findByRole("status")).toBeInTheDocument();
    // The dialog's title names the page once; the page does not repeat it
    // as a section heading (the Voice page's headings name parts, not the page).
    expect(screen.getByRole("heading", { level: 2, name: /Prompt modules/ })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 3, name: "Prompt modules" })).toBeNull();
    // Positive control: the page's one section heading is there.
    expect(screen.getByRole("heading", { level: 3, name: "When no module is on" })).toBeInTheDocument();
  });

  it("Escape in the rename box cancels the rename and leaves the dialog open", async () => {
    const user = userEvent.setup();
    const { onOpenChange } = renderDialog({ modules: [mod({ name: "Alpha" })] });
    await user.click(await screen.findByRole("button", { name: "More actions for Alpha" }));
    await user.click(screen.getByRole("button", { name: "Rename Alpha" }));
    const input = screen.getByRole("textbox", { name: "New name for Alpha" });
    await user.type(input, "zzz");
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("textbox", { name: "New name for Alpha" })).toBeNull();
    expect(onOpenChange).not.toHaveBeenCalledWith(false, expect.anything());
    expect(onOpenChange).not.toHaveBeenCalled();
    // The focus goes back to the button that started it.
    await waitFor(() => expect(screen.getByRole("button", { name: "Rename Alpha" })).toHaveFocus());
  });

  it("Escape on the delete question answers the question only", async () => {
    const user = userEvent.setup();
    const { onOpenChange } = renderDialog({ modules: [mod({ name: "Alpha" })] });
    await user.click(await screen.findByRole("button", { name: "More actions for Alpha" }));
    await user.click(screen.getByRole("button", { name: "Delete Alpha" }));
    expect(screen.getByRole("button", { name: "Keep" })).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("group", { name: "Confirm delete" })).toBeNull();
    expect(onOpenChange).not.toHaveBeenCalled();
  });

  it("Escape on the open actions closes them, then the next Escape closes the dialog", async () => {
    const user = userEvent.setup();
    const { onOpenChange } = renderDialog({ modules: [mod({ name: "Alpha" })] });
    const more = await screen.findByRole("button", { name: "More actions for Alpha" });
    await user.click(more);
    await user.click(screen.getByRole("button", { name: "Rename Alpha" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await user.keyboard("{Escape}");
    expect(more).toHaveAttribute("aria-expanded", "false");
    await waitFor(() => expect(more).toHaveFocus());
    expect(onOpenChange).not.toHaveBeenCalled();
    // GROUND: with nothing of ours open, Escape reaches the dialog as usual.
    fireEvent.keyDown(document.activeElement ?? document.body, { key: "Escape" });
    await waitFor(() => expect(onOpenChange).toHaveBeenCalled());
    expect(onOpenChange.mock.calls[0][0]).toBe(false);
  });
});

// -- The switch against a server that remembers --
//
// The fixed routes above answer the same list every time. These tests need a
// server that keeps what each PATCH set, and a list read that can be held, so
// the moment between "the write is answered" and "the list is fresh" can be
// looked at directly.

interface StatefulServer {
  /** What the server holds, by module id. */
  state: Map<number, boolean>;
  /** The `enabled` of every PATCH that carried one, in the order they arrived. */
  patches: boolean[];
  /** The `sort_order` of every PATCH that carried one. */
  moves: number[];
  /** How many preset calls arrived. */
  presetCalls: () => number;
  /** Makes the next list reads wait until `release` is called. */
  holdList: () => void;
  release: () => Promise<void>;
  /** Makes list reads fail with a 500 while true. */
  failList: (on: boolean) => void;
  fetchMock: ReturnType<typeof vi.fn>;
}

function statefulServer(rows: Row[]): StatefulServer {
  const state = new Map(rows.map((r) => [r.id as number, r.enabled as boolean]));
  const patches: boolean[] = [];
  const moves: number[] = [];
  let presets = 0;
  const order = rows.map((r) => r.id as number);
  let held: Array<() => void> | null = null;
  let failing = false;
  const json = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  const snapshot = (): Row[] =>
    rows.map((r) => ({
      ...r,
      enabled: state.get(r.id as number),
      sort_order: order.indexOf(r.id as number),
    }));
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    if (url.includes("/vault/status")) return json({ initialized: true, unlocked: true });
    if (url.includes("/prompt-modules/settings")) {
      return json({ prompt_layer_enabled: false, reply_length_words: null });
    }
    if (method === "POST" && url.includes("/prompt-modules/enable-preset-defaults")) {
      presets += 1;
      const ids = rows.filter((r) => r.st_enabled === true).map((r) => r.id as number);
      for (const id of ids) state.set(id, true);
      return json({ enabled_ids: ids, left_out: [] });
    }
    const patch = method === "PATCH" ? url.match(/\/prompt-modules\/(\d+)/) : null;
    if (patch) {
      const id = Number(patch[1]);
      const body = JSON.parse(String(init?.body));
      if ("enabled" in body) {
        patches.push(body.enabled);
        state.set(id, body.enabled);
      }
      if ("sort_order" in body) {
        moves.push(body.sort_order);
        order.splice(order.indexOf(id), 1);
        order.splice(body.sort_order, 0, id);
      }
      return json(snapshot().find((r) => r.id === id));
    }
    if (method === "GET" && url.includes("/prompt-modules")) {
      // The list is read when the request arrives, as a database would.
      const list = snapshot();
      if (held) {
        const queue = held;
        await new Promise<void>((resolve) => queue.push(resolve));
      }
      if (failing) return json({ detail: "boom" }, 500);
      return json({ modules: list, imports: [], enabled_count: 0 });
    }
    if (url.includes("/settings")) return json(settingsFixture);
    return json({ detail: `No mock for ${method} ${url}` }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return {
    state,
    patches,
    moves,
    presetCalls: () => presets,
    fetchMock,
    holdList: () => {
      held = [];
    },
    release: async () => {
      const waiting = held ?? [];
      held = null;
      await act(async () => {
        for (const go of waiting) go();
      });
    },
    failList: (on) => {
      failing = on;
    },
  };
}

function renderStateful(rows: Row[]) {
  const server = statefulServer(rows);
  const wrapper = ({ children }: { children: ReactNode }) => (
    <GenerationSettingsProvider>{children}</GenerationSettingsProvider>
  );
  const { queryClient } = renderWithQueryClient(<PromptModulesSection />, { wrapper });
  return { ...server, client: queryClient };
}

const listReads = (s: Pick<StatefulServer, "fetchMock">) =>
  s.fetchMock.mock.calls.filter(([input, init]) => {
    const url = String(input);
    const method = ((init as RequestInit | undefined)?.method ?? "GET").toUpperCase();
    return method === "GET" && /\/prompt-modules(\?|$)/.test(url);
  }).length;

describe("the switch never acts on a stale list", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("on then off from the keyboard ends off, and the focus stays on the switch", async () => {
    const user = userEvent.setup();
    const alpha = mod({ name: "Alpha" });
    const s = renderStateful([alpha]);
    const toggle = await screen.findByRole("switch", { name: "Alpha" });

    // GROUND: nothing sent yet, the switch shows what the server holds.
    expect(s.patches).toEqual([]);
    expect(toggle).toHaveAttribute("aria-checked", "false");

    toggle.focus();
    const readsBefore = listReads(s);
    s.holdList();
    await user.keyboard(" ");
    await waitFor(() => expect(s.patches).toEqual([true]));
    await waitFor(() => expect(listReads(s)).toBeGreaterThan(readsBefore));

    // The PATCH is answered, the list read after it is still out. The switch
    // already shows the server's answer, refuses a second press, and is not
    // `disabled`, which in a browser would drop the focus to the body.
    await waitFor(() => expect(toggle).toHaveAttribute("aria-checked", "true"));
    expect(toggle).toHaveAttribute("aria-disabled", "true");
    expect(toggle).not.toBeDisabled();
    expect(toggle).toHaveFocus();
    await user.keyboard(" ");
    expect(s.patches).toEqual([true]);

    await s.release();
    await waitFor(() => expect(toggle).not.toHaveAttribute("aria-disabled"));
    expect(toggle).toHaveFocus();

    // POSITIVE: the next press is the second flip, and it sends off.
    await user.keyboard(" ");
    await waitFor(() => expect(s.patches).toEqual([true, false]));
    await waitFor(() => expect(toggle).toHaveAttribute("aria-checked", "false"));
    await waitFor(() => expect(toggle).not.toHaveAttribute("aria-disabled"));
    expect(s.state.get(alpha.id as number)).toBe(false);
    expect(toggle).toHaveFocus();
  });

  it("a fast double click sends one write, and the screen matches the server", async () => {
    const alpha = mod({ name: "Alpha" });
    const s = renderStateful([alpha]);
    const toggle = await screen.findByRole("switch", { name: "Alpha" });
    // Two clicks in one task, before the page renders again.
    act(() => {
      toggle.click();
      toggle.click();
    });
    await waitFor(() => expect(toggle).toHaveAttribute("aria-checked", "true"));
    await waitFor(() => expect(toggle).not.toHaveAttribute("aria-disabled"));
    expect(s.patches).toEqual([true]);
    expect(s.state.get(alpha.id as number)).toBe(true);

    // POSITIVE: once settled, the same double click again is one flip back.
    act(() => {
      toggle.click();
      toggle.click();
    });
    await waitFor(() => expect(toggle).toHaveAttribute("aria-checked", "false"));
    await waitFor(() => expect(toggle).not.toHaveAttribute("aria-disabled"));
    expect(s.patches).toEqual([true, false]);
    expect(s.state.get(alpha.id as number)).toBe(false);
  });

  it("a click in the same moment a fresh list lands acts on the fresh list", async () => {
    const alpha = mod({ name: "Alpha" });
    const s = renderStateful([alpha]);
    const toggle = await screen.findByRole("switch", { name: "Alpha" });
    await waitFor(() => expect(toggle).not.toHaveAttribute("aria-disabled"));
    // GROUND: the page and the server agree the module is off.
    expect(toggle).toHaveAttribute("aria-checked", "false");

    // Another window turned it on; a list read brings that in. The cache has
    // it at once, the page draws it a moment later, and the click falls in
    // between. What the click sends must follow the list, not the old frame.
    s.state.set(alpha.id as number, true);
    act(() => {
      s.client.setQueryData(PROMPT_MODULE_LIST_KEY, {
        modules: [{ ...alpha, enabled: true }],
      });
      toggle.click();
    });
    await waitFor(() => expect(s.patches).toEqual([false]));
    await waitFor(() => expect(toggle).not.toHaveAttribute("aria-disabled"));
    expect(toggle).toHaveAttribute("aria-checked", "false");
    expect(s.state.get(alpha.id as number)).toBe(false);
  });

  it("a list read that fails after a write leaves no old switch to click", async () => {
    const user = userEvent.setup();
    const alpha = mod({ name: "Alpha" });
    const s = renderStateful([alpha]);
    const toggle = await screen.findByRole("switch", { name: "Alpha" });
    expect(toggle).toHaveAttribute("aria-checked", "false");

    s.failList(true);
    await user.click(toggle);
    await waitFor(() => expect(s.patches).toEqual([true]));
    // The read after the write failed. The page says so and draws no switch,
    // so nothing on screen can show the list from before the write.
    await waitFor(() =>
      expect(screen.getAllByRole("alert").map((a) => a.textContent)).toContain(
        MODULE_LIST_UNREACHABLE,
      ),
    );
    expect(screen.queryByRole("switch", { name: "Alpha" })).toBeNull();

    // POSITIVE: the list is back, and it is the server's state, on.
    s.failList(false);
    await act(async () => {
      await s.client.invalidateQueries({ queryKey: PROMPT_MODULE_LIST_KEY });
    });
    const back = await screen.findByRole("switch", { name: "Alpha" });
    expect(back).toHaveAttribute("aria-checked", "true");
    await user.click(back);
    await waitFor(() => expect(s.patches).toEqual([true, false]));
    await waitFor(() => expect(back).toHaveAttribute("aria-checked", "false"));
    expect(s.state.get(alpha.id as number)).toBe(false);
  });

  it("while a preset's set is settling, no module switch sends anything", async () => {
    const user = userEvent.setup();
    const common = { import_id: 7, source_type: "st_preset", source_filename: "p.json" };
    const on = mod({ ...common, name: "StOn", st_enabled: true });
    const off = mod({ ...common, name: "StOff", st_enabled: false });
    const s = renderStateful([on, off]);
    const stOff = await screen.findByRole("switch", { name: "StOff" });
    const stOn = screen.getByRole("switch", { name: "StOn" });
    // GROUND: before the preset button, both switches take clicks.
    expect(stOn).not.toHaveAttribute("aria-disabled");

    s.holdList();
    await user.click(screen.getByRole("button", { name: /Turn on entries enabled in SillyTavern/ }));
    await waitFor(() => expect(s.state.get(on.id as number)).toBe(true));
    await waitFor(() => expect(stOff).toHaveAttribute("aria-disabled", "true"));
    expect(stOn).toHaveAttribute("aria-disabled", "true");
    // The list on screen still says StOn is off; a click here would send on.
    expect(stOn).toHaveAttribute("aria-checked", "false");
    await user.click(stOn);
    await user.click(stOff);
    expect(s.patches).toEqual([]);

    await s.release();
    await waitFor(() => expect(stOn).toHaveAttribute("aria-checked", "true"));
    await waitFor(() => expect(stOn).not.toHaveAttribute("aria-disabled"));

    // POSITIVE: settled, a click on StOn turns it off.
    await user.click(stOn);
    await waitFor(() => expect(s.patches).toEqual([false]));
    expect(s.state.get(on.id as number)).toBe(false);
  });
  it("a click on a row the list no longer has sends nothing", async () => {
    const alpha = mod({ name: "Alpha" });
    const beta = mod({ name: "Beta" });
    const s = renderStateful([alpha, beta]);
    const toggle = await screen.findByRole("switch", { name: "Alpha" });
    await waitFor(() => expect(toggle).not.toHaveAttribute("aria-disabled"));

    // Alpha was deleted elsewhere; the list without it is in the cache, and
    // the click lands before the page draws that.
    act(() => {
      s.client.setQueryData(PROMPT_MODULE_LIST_KEY, { modules: [{ ...beta }] });
      toggle.click();
    });
    await waitFor(() => expect(screen.queryByRole("switch", { name: "Alpha" })).toBeNull());
    expect(s.patches).toEqual([]);

    // POSITIVE: the same kind of click on a row the list still has is sent.
    const betaSwitch = screen.getByRole("switch", { name: "Beta" });
    act(() => {
      s.client.setQueryData(PROMPT_MODULE_LIST_KEY, { modules: [{ ...beta }] });
      betaSwitch.click();
    });
    await waitFor(() => expect(s.patches).toEqual([true]));
  });

  it("a move from the keyboard keeps the focus on the button while it settles", async () => {
    const user = userEvent.setup();
    const first = mod({ name: "First" });
    const second = mod({ name: "Second" });
    const s = renderStateful([first, second]);
    await screen.findByRole("switch", { name: "Second" });
    await openActions(user, "Second");
    const up = screen.getByRole("button", { name: "Move Second up" });
    // GROUND: nothing moved yet, and the button takes presses.
    expect(s.moves).toEqual([]);
    expect(up).not.toHaveAttribute("aria-disabled");

    up.focus();
    s.holdList();
    await user.keyboard("{Enter}");
    await waitFor(() => expect(s.moves).toEqual([0]));
    await waitFor(() => expect(up).toHaveAttribute("aria-disabled", "true"));
    expect(up).not.toBeDisabled();
    expect(up).toHaveFocus();
    // A second press while it settles sends nothing.
    await user.keyboard("{Enter}");
    expect(s.moves).toEqual([0]);

    await s.release();
    // Second is first now: Up is at its end and Down takes presses.
    await waitFor(() => expect(up).toBeDisabled());
    const down = screen.getByRole("button", { name: "Move Second down" });
    await waitFor(() => expect(down).not.toHaveAttribute("aria-disabled"));
    // POSITIVE: settled, Down is sent.
    await user.click(down);
    await waitFor(() => expect(s.moves).toEqual([0, 1]));
  });

  it("the preset button keeps the focus while its set settles", async () => {
    const user = userEvent.setup();
    const common = { import_id: 7, source_type: "st_preset", source_filename: "p.json" };
    const on = mod({ ...common, name: "StOn", st_enabled: true });
    const s = renderStateful([on, mod({ ...common, name: "StOff", st_enabled: false })]);
    await screen.findByRole("switch", { name: "StOn" });
    const preset = screen.getByRole("button", { name: /Turn on entries enabled in SillyTavern/ });
    // GROUND: no call yet, the button takes presses.
    expect(s.presetCalls()).toBe(0);
    expect(preset).not.toHaveAttribute("aria-disabled");

    preset.focus();
    s.holdList();
    await user.keyboard("{Enter}");
    await waitFor(() => expect(s.presetCalls()).toBe(1));
    await waitFor(() => expect(preset).toHaveAttribute("aria-disabled", "true"));
    expect(preset).not.toBeDisabled();
    expect(preset).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(s.presetCalls()).toBe(1);

    await s.release();
    await waitFor(() => expect(preset).not.toHaveAttribute("aria-disabled"));
    expect(preset).toHaveFocus();
    // POSITIVE: settled, the next press is sent.
    await user.keyboard("{Enter}");
    await waitFor(() => expect(s.presetCalls()).toBe(2));
  });
});


describe("SillyTavern placement on the page", () => {
  const presetRows = () => [
    mod({ name: "Main", import_id: 9, source_type: "st_chat_preset", st_enabled: true,
          source_filename: "preset.json" }),
    mod({ name: "Loose", import_id: 9, source_type: "st_chat_preset", st_enabled: null,
          source_filename: "preset.json", badges: ["not_in_order"] }),
    mod({ name: "No depth", import_id: 9, source_type: "st_chat_preset", st_enabled: true,
          source_filename: "preset.json", badges: ["in_chat_not_sent"], can_enable: false,
          blocked_reason: "never_triggers", triggers: [] }),
  ];
  const importRow = (over: Row = {}) => ({
    id: 9, source_type: "st_chat_preset", source_filename: "preset.json",
    history_cleanup: false, cleanup_scripts: 2, ...over,
  });
  const listRoute = (imports: Row[]) => ({
    "GET /prompt-modules": { body: { modules: presetRows(), imports, enabled_count: 0 } },
  });

  it("offers history cleanup off, and sends the switch to the import", async () => {
    const user = userEvent.setup();
    const { fetchMock } = renderPage({ extra: listRoute([importRow()]) });
    const group = await screen.findByRole("group", { name: "From preset.json" });
    const toggle = within(group).getByRole("switch", { name: HISTORY_CLEANUP_LABEL });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    expect(toggle).toHaveAccessibleDescription(HISTORY_CLEANUP_HINT);
    await user.click(toggle);
    await waitFor(() => expect(sent(fetchMock, "PATCH", "/prompt-modules/imports/9")).toHaveLength(1));
    expect(bodyOf(sent(fetchMock, "PATCH", "/prompt-modules/imports/9")[0])).toEqual({
      history_cleanup: true,
    });
  });

  it("shows no cleanup switch for a preset without cleanup scripts", async () => {
    renderPage({ extra: listRoute([importRow({ cleanup_scripts: 0 })]) });
    const group = await screen.findByRole("group", { name: "From preset.json" });
    expect(within(group).queryByRole("switch", { name: HISTORY_CLEANUP_LABEL })).toBeNull();
    // Positive control: the module switches in the same group are there.
    expect(within(group).getByRole("switch", { name: "Main" })).toBeInTheDocument();
  });

  it("says a part outside the order was not sent there, and can be turned on", async () => {
    renderPage({ extra: listRoute([importRow()]) });
    const loose = await screen.findByRole("switch", { name: "Loose" });
    expect(loose).toBeEnabled();
    const group = screen.getByRole("group", { name: "From preset.json" });
    expect(within(group).getAllByText(NOT_IN_ORDER_NOTE).length).toBeGreaterThan(0);
  });

  it("names why an in-chat part with no depth cannot be turned on", async () => {
    renderPage({ extra: listRoute([importRow()]) });
    const sw = await screen.findByRole("switch", { name: "No depth" });
    expect(sw).toBeDisabled();
    expect(sw).toHaveAccessibleDescription("Cannot be turned on: SillyTavern never sent it.");
    expect(screen.getByText(badgeLabel("in_chat_not_sent"))).toBeInTheDocument();
  });

  it("badges say where a part is sent now", () => {
    expect(badgeLabel("role_user")).toBe("Sent as a user message");
    expect(badgeLabel("in_chat_depth", { st_injection_depth: 0, st_injection_order: 100 }))
      .toBe("Sent inside the chat at depth 0, order 100");
    expect(badgeLabel("not_in_order")).toBe(NOT_IN_ORDER_NOTE);
  });

  it("lists what the preset does differently here in the report", async () => {
    const report = {
      ...ST_REPORT,
      global_variables_per_chat: true,
      trailing_assistant: ["tail_a"],
      in_chat_not_sent: [{ identifier: "nodepth", reason: "no_depth" }],
      regex_scripts: {
        prompt_only: ["Strip old state"], display_only: ["Colors"],
        edits_stored_message: ["Fix typos"], disabled: [],
        unsupported: [{ name: "Odd one", reason: "may_be_slow" }],
      },
    };
    const { user, report: region } = await importPreset({
      "POST /prompt-modules/import": {
        status: 201, body: { import_id: 9, modules: stModules(), report },
      },
    });
    const r = within(region);
    await user.click(r.getByRole("button", { name: /^Handled differently here, 6 entries$/ }));
    const list = r.getByRole("list", { name: "Handled differently" });
    expect(list).toHaveTextContent("Global variables: kept per chat here");
    expect(list).toHaveTextContent("tail_a: sent as a user message");
    expect(list).toHaveTextContent("Fix typos: would change saved messages, not applied");
    expect(list).toHaveTextContent(`Odd one: ${REGEX_UNSUPPORTED.may_be_slow}, skipped`);
    await user.click(r.getByRole("button", { name: /^Not made into modules/ }));
    expect(r.getByRole("list", { name: "Entries not made into modules" }))
      .toHaveTextContent("nodepth: no depth, so SillyTavern never sent it");
  });

  it("leaves the group out for a plain report", async () => {
    const { report: region } = await importPreset();
    expect(within(region).queryByRole("button", { name: /^Handled differently here/ })).toBeNull();
  });
});


describe("a preset's macro variables", () => {
  const presetRow = () =>
    mod({ name: "Main", import_id: 9, source_type: "st_chat_preset", st_enabled: true });

  afterEach(() => useUiStore.setState({ selectedChatId: null }));

  it("clears the open chat's variables", async () => {
    useUiStore.setState({ selectedChatId: 42 });
    const user = userEvent.setup();
    const { fetchMock } = renderPage({
      modules: [presetRow()],
      extra: { "DELETE /prompt-modules/variables/": { body: { ok: true, cleared: 3 } } },
    });
    const clear = await screen.findByRole("button", { name: CLEAR_VARIABLES_LABEL });
    await user.click(clear);
    await waitFor(() =>
      expect(sent(fetchMock, "DELETE", "/prompt-modules/variables/42")).toHaveLength(1),
    );
    expect(await screen.findByText("Cleared.")).toBeInTheDocument();
  });

  it("offers no clear without an open chat", async () => {
    renderPage({ modules: [presetRow()] });
    await screen.findByRole("switch", { name: "Main" });
    expect(screen.queryByRole("button", { name: CLEAR_VARIABLES_LABEL })).toBeNull();
  });

  it("offers no clear without a preset", async () => {
    useUiStore.setState({ selectedChatId: 42 });
    renderPage({ modules: [mod({ name: "Plain" })] });
    await screen.findByRole("switch", { name: "Plain" });
    expect(screen.queryByRole("button", { name: CLEAR_VARIABLES_LABEL })).toBeNull();
  });

  it("the refusal names clearing when the chat's variables may be what grew", () => {
    const base = { detail: "prompt_modules_over_budget" as const,
                   modules: [{ id: 1, name: "Main", tokens: 900 }] };
    expect(overBudgetSentence(base)).toContain(OVER_BUDGET_FIX);
    expect(overBudgetSentence({ ...base, chat_variables: true }))
      .toContain(OVER_BUDGET_FIX_VARIABLES);
    expect(OVER_BUDGET_FIX_VARIABLES.split(" ").length).toBeLessThanOrEqual(12);
  });

  it("the report says what the preset leaves to this app", async () => {
    const report = {
      ...ST_REPORT,
      no_chat_history_marker: true,
      role_sent_as_system: ["odd"],
      in_chat_not_sent: [{ identifier: "deep", reason: "too_deep" }],
    };
    const { user, report: region } = await importPreset({
      "POST /prompt-modules/import": {
        status: 201, body: { import_id: 9, modules: stModules(), report },
      },
    });
    const r = within(region);
    await user.click(r.getByRole("button", { name: /^Handled differently here/ }));
    const list = r.getByRole("list", { name: "Handled differently" });
    expect(list).toHaveTextContent("No chat history placeholder: the chat is still sent here");
    expect(list).toHaveTextContent("odd: unknown role, sent as a system message");
    await user.click(r.getByRole("button", { name: /^Not made into modules/ }));
    expect(r.getByRole("list", { name: "Entries not made into modules" }))
      .toHaveTextContent("deep: deeper than 10000, so SillyTavern never sent it");
  });
});
