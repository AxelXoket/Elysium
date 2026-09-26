/**
 * PromptModulesSection.test.tsx - the "Prompt modules" page in Settings.
 *
 * What this pins, each with a ground control (the thing absent, the check
 * reads nothing) and a positive control (the thing present, the check sees it):
 *
 *   MT-10  the import button stays off until a file is chosen AND the consent
 *          box is ticked; the three warnings on screen are the owner's English
 *          text byte for byte, from shared/prompt_module_warnings.json.
 *   9A.4   the counter "Active modules: {n}, about {x} tokens per message." is
 *          always on screen and counts only modules that are on, as the
 *          request will carry them (macros rendered, one separator each).
 *   Ek O 7 the roleplay layer is never sent while a module is on, and the
 *          page says so; reply length (A9) is live only when the layer
 *          actually goes (mode b), greyed out with the reason otherwise.
 *   H4     a refused toggle names the modules that do not fit.
 *   H3     "Turn on entries enabled in SillyTavern" asks the server for the
 *          whole set, and a set refused whole says nothing was turned on.
 *   15.3   delete asks first, in the page, never through window.confirm.
 *
 * The server is mocked at fetch: every assertion about what was sent reads
 * the request the page actually made.
 */
import { createHash } from "node:crypto";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
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
  PROMPT_MODULE_LIMITS,
  PROMPT_MODULE_WARNINGS,
  formatModuleCounter,
} from "@/components/settings/promptModuleText";
import { PROMPT_MODULE_FILENAME_HEADER } from "@/lib/api/promptModules";
import { CHARS_PER_TOKEN } from "@/lib/context/estimateContextUsage";
import { useUiStore } from "@/lib/store/uiStore";
import { getErrorMessage } from "@/lib/errors";
import { mockFetch } from "../mocks/api";
import { settingsFixture } from "../mocks/fixtures";

// ── The owner's wording, as SHA-256 of the UTF-8 bytes ──────────────────
//
// The owner's English wording of 26 Sep 2026, hashed by script, not by hand.
// The owner wrote the counter with {n} and {x} in it, so its hash is of the
// template as the shared file carries it.
const OWNER_SHA256 = {
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
  return {
    ...extra,
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
  await waitFor(() =>
    expect(screen.queryByText("Loading…")).not.toBeInTheDocument(),
  );
}

function fileOf(name: string, text: string): File {
  return new File([text], name, { type: "application/octet-stream" });
}

const importButton = () => screen.getByRole("button", { name: /^Import$/ });
const consentBox = () => screen.getByRole("checkbox");
const fileInput = () => screen.getByLabelText("Choose a prompt module file");

beforeEach(() => {
  vi.restoreAllMocks();
  useUiStore.setState({ selectedChatId: null, selectedModelId: null });
});
afterEach(() => vi.restoreAllMocks());

// ── The shared texts ────────────────────────────────────────────────────

describe("the owner's warning texts", () => {
  it("the shared file carries the owner's bytes, all three", () => {
    expect(sha256(warningsJson.import_consent)).toBe(OWNER_SHA256.import_consent);
    expect(sha256(warningsJson.counter)).toBe(OWNER_SHA256.counter);
    expect(sha256(warningsJson.st_preset)).toBe(OWNER_SHA256.st_preset);
  });

  it("GROUND: the same check rejects a one-character near miss", () => {
    // Without this, a hash comparison that always passed would read as proof.
    expect(sha256(dropLastChar(warningsJson.import_consent))).not.toBe(
      OWNER_SHA256.import_consent,
    );
    expect(sha256(dropLastChar(warningsJson.counter))).not.toBe(OWNER_SHA256.counter);
    expect(sha256(dropLastChar(warningsJson.st_preset))).not.toBe(OWNER_SHA256.st_preset);
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
  it("GROUND: with no file chosen the button is off, ticked or not", async () => {
    const user = userEvent.setup();
    renderPage();
    expect(importButton()).toBeDisabled();
    await user.click(consentBox());
    expect(consentBox()).toBeChecked();
    expect(importButton()).toBeDisabled();
  });

  it("a chosen file stays unimportable until the consent box is ticked", async () => {
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
    // Clicking the disabled button sends nothing.
    await user.click(importButton());
    expect(sent(fetchMock, "POST", "/import")).toHaveLength(0);

    await user.click(consentBox());
    expect(importButton()).toBeEnabled();
    await user.click(importButton());

    await screen.findByTestId("prompt-module-report");
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
    // And it is what names the box.
    expect(consentBox()).toHaveAccessibleName(warningsJson.import_consent);
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
    await screen.findByTestId("prompt-module-report");
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
    expect(screen.getByText(/over the 96 KB limit/)).toBeInTheDocument();

    // Positive control: exactly at the cap is fine.
    await user.upload(fileInput(), fileOf("cap.txt", "a".repeat(limitsJson.txt_md_max_bytes)));
    expect(importButton()).toBeEnabled();

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
    const warning = await screen.findByTestId("prompt-module-st-warning");
    expect(warning.textContent).toBe(warningsJson.st_preset);
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
    expect(screen.queryByTestId("prompt-module-st-warning")).not.toBeInTheDocument();
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
  secrets_removed: ["$.proxy_password"],
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
  const report = await screen.findByTestId("prompt-module-report");
  return { user, report, ...view };
}

describe("import report", () => {
  it("lists everything that did not come across as it was", async () => {
    const { report } = await importPreset();
    const r = within(report);
    expect(r.getByTestId("prompt-module-report-st-warning").textContent).toBe(
      warningsJson.st_preset,
    );
    expect(r.getByTestId("prompt-module-report-created")).toHaveTextContent(
      "3 modules created, all off",
    );
    const notMade = r.getByRole("list", { name: "Entries not made into modules" });
    expect(notMade).toHaveTextContent("chatHistory");
    expect(notMade).toHaveTextContent("imp - kept, but it only fires on impersonate");
    expect(notMade).toHaveTextContent("ghost - named in the order list");
    expect(r.getByRole("list", { name: "Badge counts" })).toHaveTextContent(
      "Went after the chat history in SillyTavern: 1",
    );
    expect(r.getByRole("list", { name: "Unprocessed macros" })).toHaveTextContent(
      "{{getvar::x}}: 2",
    );
    expect(r.getByTestId("prompt-module-order-source")).toHaveTextContent("(100001)");
    expect(r.getByTestId("prompt-module-order-source")).toHaveTextContent(
      "2 per-character order lists were not used",
    );
    const fields = r.getByRole("list", { name: "Preset fields" });
    expect(fields).toHaveTextContent("temperature = 1.1 - stored, not applied");
    expect(fields).toHaveTextContent("reverse_proxy - stored, value not shown");
    expect(r.getByRole("list", { name: "Removed secrets" })).toHaveTextContent(
      "$.proxy_password",
    );
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
    const report = await screen.findByTestId("prompt-module-report");
    expect(within(report).queryByTestId("prompt-module-report-st-warning")).toBeNull();
    expect(within(report).getByTestId("prompt-module-report-created")).toHaveTextContent(
      "1 module created, off",
    );
    expect(within(report).queryByRole("list", { name: "Badge counts" })).toBeNull();
    expect(
      within(report).queryByRole("button", { name: /Turn on entries enabled in SillyTavern/ }),
    ).toBeNull();
  });

  it("turns on what SillyTavern had on, as one request with the window", async () => {
    useUiStore.setState({ selectedChatId: 4, selectedModelId: "vendor/model-a" });
    const { user, report, fetchMock } = await importPreset({
      "POST /prompt-modules/enable-preset-defaults": {
        body: { enabled_ids: [1, 2], left_out: [] },
      },
    });
    // Two of the three were on in SillyTavern and can fire here.
    await user.click(
      within(report).getByRole("button", {
        name: "Turn on entries enabled in SillyTavern (2)",
      }),
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
      "Imp - it only fired on a kind of reply this app does not have",
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
    const { user, report } = await importPreset({
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
    });
    await user.click(
      within(report).getByRole("button", { name: /Turn on entries enabled in SillyTavern/ }),
    );
    const notice = await within(report).findByTestId("prompt-modules-over-budget");
    expect(notice).toHaveTextContent("Nothing was turned on.");
    expect(notice).toHaveTextContent("Main, about 3000 tokens");
    expect(notice).toHaveTextContent("Tail, about 2100 tokens");
    expect(notice).toHaveTextContent("About 5101 tokens in all.");
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
    expect(screen.getByTestId("prompt-module-counter").textContent).toBe(
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
    const counter = screen.getByTestId("prompt-module-counter");
    expect(counter.textContent).toBe(formatModuleCounter(2, expectedTokens([on1, on2])));
    // The template is the owner's with only the two numbers changed.
    expect(counter.textContent).toBe(
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
    expect(screen.getByTestId("prompt-module-counter").textContent).toBe(
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
    expect(screen.getByTestId("prompt-module-counter").textContent).toBe(
      formatModuleCounter(1, expectedTokens([send])),
    );
    // Still on, so the layer still does not go: mode a counts every module on.
    expect(screen.getByTestId("prompt-layer-suppressed")).toHaveTextContent(
      "2 modules are on",
    );
  });

  it("GROUND: a regenerate-only module that is off changes nothing", async () => {
    renderPage({
      layer: true,
      modules: [mod({ content: "r".repeat(900), enabled: false, triggers: ["regenerate"] })],
    });
    await waitForList();
    expect(screen.getByTestId("prompt-module-counter").textContent).toBe(
      formatModuleCounter(0, 0),
    );
    expect(screen.queryByTestId("prompt-layer-suppressed")).toBeNull();
  });

  it("a macro comment costs nothing: it never reaches the model", async () => {
    const text = "x".repeat(30);
    renderPage({
      modules: [
        mod({ content: `${text}{{// ${"long author note ".repeat(40)}}}`, enabled: true }),
      ],
    });
    await waitForList();
    expect(screen.getByTestId("prompt-module-counter").textContent).toBe(
      formatModuleCounter(1, expectedTokens([text])),
    );
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
    await user.click(screen.getByRole("button", { name: "Rename Alpha" }));
    const input = screen.getByRole("textbox", { name: "New name for Alpha" });
    await user.clear(input);
    await user.type(input, "Beta{Enter}");
    await waitFor(() => expect(sent(fetchMock, "PATCH", "/prompt-modules/")).toHaveLength(1));
    expect(bodyOf(sent(fetchMock, "PATCH", "/prompt-modules/")[0])).toEqual({ name: "Beta" });
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
    expect(screen.queryByTestId("prompt-modules-over-budget")).toBeNull();
    await user.click(screen.getByRole("switch", { name: "Alpha" }));
    const notice = await screen.findByTestId("prompt-modules-over-budget");
    expect(notice).toHaveTextContent("Alpha, about 4800 tokens");
    expect(notice).toHaveTextContent("Turn a module off, or make the context window bigger.");
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
    expect(screen.queryByTestId("prompt-modules-over-budget")).toBeNull();
  });

  it("an entry that never fires here cannot be switched on, says why, and offers no copy", async () => {
    const [, , imp] = stModules();
    const { fetchMock } = renderPage({ modules: [imp] });
    await waitForList();
    expect(screen.getByRole("switch", { name: "Imp" })).toBeDisabled();
    expect(screen.getByText(/only fired on a kind of reply this app does not have/)).toBeInTheDocument();
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
    expect(screen.queryByText(/only fired on a kind of reply this app does not have/)).toBeNull();
  });

  it("moves a module by position, and the ends cannot move past themselves", async () => {
    const user = userEvent.setup();
    const first = mod({ name: "First", sort_order: 0 });
    const second = mod({ name: "Second", sort_order: 1 });
    const { fetchMock } = renderPage({ modules: [second, first] });
    await waitForList();
    expect(screen.getByRole("button", { name: "Move First up" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Move Second down" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Move First down" }));
    await waitFor(() => expect(sent(fetchMock, "PATCH", `/${first.id}`)).toHaveLength(1));
    expect(bodyOf(sent(fetchMock, "PATCH", `/${first.id}`)[0])).toEqual({ sort_order: 1 });
  });

  it("delete asks in the page first, and never through window.confirm", async () => {
    const confirmSpy = vi.spyOn(window, "confirm");
    const user = userEvent.setup();
    const gone = mod({ name: "Gone" });
    const { fetchMock } = renderPage({ modules: [gone] });
    await waitForList();

    await user.click(screen.getByRole("button", { name: "Delete Gone" }));
    const ask = screen.getByRole("group", { name: "Confirm delete" });
    expect(sent(fetchMock, "DELETE", "/prompt-modules/")).toHaveLength(0);

    await user.click(within(ask).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("group", { name: "Confirm delete" })).toBeNull();
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
    expect(screen.getByText("From preset.json")).toBeInTheDocument();
    expect(screen.getByTestId("prompt-module-marker")).toHaveTextContent("Placeholder: chatHistory");
    expect(screen.getByText(/On in SillyTavern/)).toBeInTheDocument();
    expect(screen.getByText(/Off in SillyTavern/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Turn on entries enabled in SillyTavern (1)" }),
    ).toBeInTheDocument();
  });

  it("GROUND: with no modules it says so and shows no switch", async () => {
    renderPage();
    await waitForList();
    expect(screen.getByTestId("prompt-module-empty")).toBeInTheDocument();
    expect(screen.queryAllByRole("switch").map((s) => s.getAttribute("aria-label")))
      .toEqual(["Roleplay layer"]);
  });
});

// ── Layer and reply length (Ek O 7-8, A9) ───────────────────────────────

const lengthRadios = () =>
  within(screen.getByRole("radiogroup", { name: "Reply length" })).getAllByRole("radio");

describe("roleplay layer and reply length", () => {
  it("mode c (no module, layer off): length greyed out with its reason", async () => {
    renderPage({ layer: false });
    await waitForList();
    await waitFor(() => expect(screen.getByRole("switch", { name: "Roleplay layer" })).toBeEnabled());
    expect(screen.getByRole("switch", { name: "Roleplay layer" })).toHaveAttribute("aria-checked", "false");
    for (const radio of lengthRadios()) expect(radio).toBeDisabled();
    expect(screen.getByTestId("reply-length-reason")).toHaveTextContent(
      "part of the roleplay layer, which is off",
    );
    expect(screen.getByTestId("reply-length")).toHaveClass("settings-section-disabled");
    expect(screen.queryByTestId("prompt-layer-suppressed")).toBeNull();
  });

  it("mode b (no module, layer on): length is live and saves both settings together", async () => {
    const user = userEvent.setup();
    const { fetchMock } = renderPage({ layer: true, words: null });
    await waitForList();
    await waitFor(() => expect(lengthRadios()[1]).toBeEnabled());
    expect(screen.queryByTestId("reply-length-reason")).toBeNull();
    expect(screen.getByTestId("reply-length")).not.toHaveClass("settings-section-disabled");
    expect(lengthRadios()[0]).toHaveAttribute("aria-checked", "true");
    const allowed = limitsJson.reply_length_words_allowed;
    expect(lengthRadios().map((r) => r.textContent)).toEqual([
      "Adaptive",
      ...allowed.map((n) => `About ${n} words`),
    ]);
    await user.click(lengthRadios()[1]);
    await waitFor(() => expect(sent(fetchMock, "PUT", "/prompt-modules/settings")).toHaveLength(1));
    expect(bodyOf(sent(fetchMock, "PUT", "/prompt-modules/settings")[0])).toEqual({
      prompt_layer_enabled: true,
      reply_length_words: allowed[0],
    });
  });

  it("mode a (a module on, layer on): the layer is shown as not sent, length greyed out", async () => {
    renderPage({ layer: true, modules: [mod({ enabled: true })] });
    await waitForList();
    expect(await screen.findByTestId("prompt-layer-suppressed")).toHaveTextContent(
      "Not being sent right now: 1 module is on.",
    );
    for (const radio of lengthRadios()) expect(radio).toBeDisabled();
    expect(screen.getByTestId("reply-length-reason")).toHaveTextContent(
      "Prompt modules are on (1), so the roleplay layer and its length line are not sent.",
    );
  });

  it("a blank module that is on does not count: the layer is still sent (mode b)", async () => {
    // The server's list_enabled skips it (store.block_reason), so the page
    // must not tell the user the layer is off. U+0085 is blank to the server
    // and not to JS trim().
    renderPage({ layer: true, modules: [mod({ enabled: true, content: " 	" })] });
    await waitForList();
    await waitFor(() => expect(lengthRadios()[1]).toBeEnabled());
    expect(screen.queryByTestId("prompt-layer-suppressed")).toBeNull();
    expect(screen.queryByTestId("reply-length-reason")).toBeNull();
    expect(screen.getByTestId("prompt-module-counter").textContent).toBe(
      formatModuleCounter(0, 0),
    );
  });

  it("mode a with the layer switch off: no suppressed note, same reason", async () => {
    renderPage({ layer: false, modules: [mod({ enabled: true }), mod({ enabled: true })] });
    await waitForList();
    await screen.findByText(/Prompt modules are on \(2\)/);
    expect(screen.queryByTestId("prompt-layer-suppressed")).toBeNull();
  });

  it("the not-sent-with-modules note is always on screen", async () => {
    renderPage();
    expect(screen.getByTestId("prompt-layer-note")).toHaveTextContent(
      "Never sent while any prompt module is on.",
    );
  });

  it("flipping the layer keeps the stored length", async () => {
    const user = userEvent.setup();
    const { fetchMock } = renderPage({ layer: false, words: 300 });
    await waitForList();
    const layer = screen.getByRole("switch", { name: "Roleplay layer" });
    await waitFor(() => expect(layer).toBeEnabled());
    await user.click(layer);
    await waitFor(() => expect(sent(fetchMock, "PUT", "/settings")).toHaveLength(1));
    expect(bodyOf(sent(fetchMock, "PUT", "/settings")[0])).toEqual({
      prompt_layer_enabled: true,
      reply_length_words: 300,
    });
  });

  it("warns when the chosen length cannot fit the reply cap (A9 condition 3)", async () => {
    renderPage({ layer: true, words: 300 }, 512);
    await waitForList();
    expect(await screen.findByTestId("reply-length-max-tokens")).toHaveTextContent(
      "replies stop at 512",
    );
  });

  it("GROUND: no cap warning when it fits, or when length is not live", async () => {
    renderPage({ layer: true, words: 150 }, 4096);
    await waitForList();
    await waitFor(() => expect(lengthRadios()[1]).toBeEnabled());
    expect(screen.queryByTestId("reply-length-max-tokens")).toBeNull();
  });
});

// ── The hook in the Settings dialog ─────────────────────────────────────

describe("Settings dialog", () => {
  it("has its own Prompt modules heading that opens this page", async () => {
    const user = userEvent.setup();
    mockFetch(routes({}));
    useUiStore.setState({ settingsInitialPage: null });
    renderWithQueryClient(<AppSettingsDialog open onOpenChange={() => undefined} />, {
      wrapper: ({ children }) => (
        <GenerationSettingsProvider>{children}</GenerationSettingsProvider>
      ),
    });
    expect(screen.queryByTestId("prompt-module-counter")).toBeNull();
    await user.click(await screen.findByText("Prompt modules"));
    expect(await screen.findByTestId("prompt-module-counter")).toBeInTheDocument();
    // The page's own title in the dialog, and the section heading on it.
    expect(screen.getByRole("heading", { level: 2, name: /Prompt modules/ })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 3, name: "Prompt modules" })).toBeInTheDocument();
  });
});
