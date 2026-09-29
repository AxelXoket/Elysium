/**
 * The thinking effort row in Generation Settings.
 *
 * It offers only what the selected model lists, keeps the choice per model in
 * the vault (POST /settings/thinking-effort, read back through GET /settings),
 * and carries it to the server as `generation_params.thinking_effort` from all
 * three payload builders. Untouched, the request is the one sent before the
 * row existed.
 *
 * The six catalogue shapes, as the server reads them:
 *  A no `reasoning` param      -> row hidden
 *  B param, no reasoning object -> plain text, "Provider decides"
 *  C optional, levels listed    -> Off, Provider default, each level
 *  D optional, no levels        -> Off, Provider default
 *  E mandatory, levels listed   -> Provider default, each level
 *  F mandatory, no levels       -> plain text, "Always on"
 *
 * The fetch boundary is a small stateful fake of the two settings routes. The
 * vault commits a save when the request arrives, the way the real route does,
 * and any answer can be held back, so the races a real network produces
 * (a read landing late, answers out of order) are played out exactly.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { QueryClient } from "@tanstack/react-query";
import type { ReactNode } from "react";

import thinkingContract from "../../../../shared/thinking_effort_contract.json";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { TooltipProvider } from "@/components/ui/tooltip";
import {
  GENERATION_SETTINGS_DEFAULTS,
  GenerationSettingsProvider,
  useGenerationSettings,
} from "@/components/generation/GenerationSettingsContext";
import * as thinkingText from "@/components/generation/ThinkingSetting";
import {
  MAX_NEW_TOKENS_LABEL,
  THINKING_BUDGET_WARNING,
  THINKING_DEFAULT_IS_OFF_NOTE,
  THINKING_DEFAULT_NOTE,
  THINKING_DETAILS_CACHE,
  THINKING_DETAILS_LABEL,
  THINKING_DETAILS_SAVED,
  THINKING_DETAILS_UNTOUCHED,
  THINKING_EFFORT_LABEL,
  THINKING_LEVEL_NOTE,
  THINKING_MANDATORY_LABEL,
  THINKING_MANDATORY_NOTE,
  THINKING_OFF_NOTE,
  THINKING_UNKNOWN_LABEL,
  THINKING_UNKNOWN_NOTE,
} from "@/components/generation/ThinkingSetting";
import { ModelPanel } from "@/components/models/ModelPanel";
import {
  buildCompletionPayload,
  buildEditPayload,
  buildRegeneratePayload,
} from "@/lib/generation/generationParams";
import {
  REASONING_EFFORT_ORDER,
  THINKING_BUDGET_FLOOR,
  THINKING_CHOICES,
  THINKING_CHOICE_LABELS,
  THINKING_DEFAULT,
  THINKING_OFF,
  THINKING_REPLY_ROOM_MIN,
  UNTOUCHED_MANDATORY_ORDER,
  listedEfforts,
  providerDefaultIsOff,
  resolveThinkingChoice,
  thinkingBudget,
  thinkingBudgetTooSmall,
  thinkingOptions,
  untouchedThinkingChoice,
  type ThinkingChoice,
} from "@/lib/models/thinking";
import { getErrorMessage, isKnownErrorCode } from "@/lib/errors/errorMessages";
import { keys } from "@/lib/query/keys";
import { ModelSchema, type Model } from "@/lib/schemas/models";
import { useUiStore } from "@/lib/store/uiStore";
import { modelFixture, settingsFixture } from "@/test/mocks/fixtures";

const ALL_WORDS = ["none", ...REASONING_EFFORT_ORDER];
const R = ["temperature", "max_tokens", "reasoning"];

const plainA: Model = {
  ...modelFixture,
  id: "a/plain",
  supported_parameters: ["temperature", "max_tokens"],
  reasoning: null,
};
const unknownB: Model = { ...plainA, id: "b/unknown", supported_parameters: R };
const optionalC: Model = {
  ...plainA,
  id: "c/optional",
  supported_parameters: R,
  // Reversed, with every word the catalogue uses: order and "none" are
  // tested, not the listing.
  reasoning: {
    mandatory: false,
    supported_efforts: [...ALL_WORDS].reverse(),
    default_effort: "medium",
    default_enabled: false,
  },
};
const optionalOn: Model = {
  ...plainA,
  id: "c/on-by-default",
  supported_parameters: R,
  reasoning: { mandatory: false, supported_efforts: ["low", "high"] },
};
const budgetD: Model = {
  ...plainA,
  id: "d/budget",
  supported_parameters: R,
  reasoning: { mandatory: false, supports_max_tokens: true },
};
const mandatoryE: Model = {
  ...plainA,
  id: "e/mandatory",
  supported_parameters: R,
  reasoning: { mandatory: true, supported_efforts: ["max", "high", "low"] },
};
const mandatoryF: Model = {
  ...plainA,
  id: "f/mandatory",
  supported_parameters: R,
  reasoning: { mandatory: true },
};
const maxOnly: Model = {
  ...plainA,
  id: "g/max-only",
  supported_parameters: R,
  reasoning: { mandatory: true, supported_efforts: ["max"] },
};
// The catalogue shape of a real model: optional, off unless asked, budget
// based, with levels.
const offBudget: Model = {
  ...plainA,
  id: "h/off-budget",
  supported_parameters: R,
  reasoning: {
    mandatory: false,
    supported_efforts: ["max", "high", "medium", "low"],
    default_enabled: false,
    supports_max_tokens: true,
  },
};
const MODELS = [plainA, unknownB, optionalC, optionalOn, budgetD, mandatoryE, mandatoryF, maxOnly, offBudget];

// -- pure logic --

describe("the mirror of the shared words", () => {
  it("equals shared/thinking_effort_contract.json", () => {
    expect([...REASONING_EFFORT_ORDER]).toEqual(thinkingContract.effort_order);
    expect([...THINKING_CHOICES]).toEqual(thinkingContract.choices);
    expect([...UNTOUCHED_MANDATORY_ORDER]).toEqual(thinkingContract.untouched_mandatory_order);
    expect(THINKING_OFF).toBe(thinkingContract.off);
    expect(THINKING_DEFAULT).toBe(thinkingContract.default);
    // Every choice has its own label.
    for (const choice of THINKING_CHOICES) {
      expect(THINKING_CHOICE_LABELS[choice], choice).toBeTruthy();
    }
    expect(new Set(Object.values(THINKING_CHOICE_LABELS)).size).toBe(THINKING_CHOICES.length);
  });
});

describe("the row's words", () => {
  const sentences: [string, string][] = Object.entries(thinkingText).flatMap(
    ([name, value]): [string, string][] =>
      typeof value === "string" ? [[name, value]] : [],
  );

  it("GROUND: the walk found the sentences", () => {
    expect(sentences.length).toBeGreaterThanOrEqual(10);
    expect(sentences.map(([, text]) => text)).toContain(THINKING_BUDGET_WARNING);
  });

  it("every one is at most twelve words, ASCII, no billing jargon", () => {
    for (const [name, text] of sentences) {
      expect(text.split(/\s+/).filter(Boolean).length, name).toBeLessThanOrEqual(12);
      // eslint-disable-next-line no-control-regex
      expect(text, name).toMatch(/^[\x00-\x7F]*$/);
      expect(text.toLowerCase(), name).not.toContain("reply token");
    }
  });

  it("the warning names the max output row with its own label, first", () => {
    expect(THINKING_BUDGET_WARNING.startsWith(`Raise ${MAX_NEW_TOKENS_LABEL}`)).toBe(true);
  });
});

describe("what each model offers", () => {
  it("POSITIVE: all seven words give every level in order, none left out", () => {
    expect(listedEfforts(optionalC)).toEqual([...REASONING_EFFORT_ORDER]);
    expect(thinkingOptions(optionalC)).toEqual([
      THINKING_OFF,
      THINKING_DEFAULT,
      ...REASONING_EFFORT_ORDER,
    ]);
    expect(thinkingOptions(optionalC)).not.toContain("none");
  });

  it("GROUND: an empty list offers no level", () => {
    const empty = { ...optionalC, reasoning: { mandatory: false, supported_efforts: [] } };
    expect(listedEfforts(empty)).toEqual([]);
    expect(thinkingOptions(empty)).toEqual([THINKING_OFF, THINKING_DEFAULT]);
  });

  it("per case A to F", () => {
    expect(thinkingOptions(plainA)).toEqual([]);
    expect(thinkingOptions(unknownB)).toEqual([]);
    expect(thinkingOptions(budgetD)).toEqual([THINKING_OFF, THINKING_DEFAULT]);
    // Mandatory: no Off, Provider default first, then the listed levels.
    expect(thinkingOptions(mandatoryE)).toEqual([THINKING_DEFAULT, "low", "high", "max"]);
    expect(thinkingOptions(mandatoryF)).toEqual([]);
  });

  it("untouched is today's request: Off, the lowest level, or the provider's pick", () => {
    expect(untouchedThinkingChoice(optionalC)).toBe(THINKING_OFF);
    expect(untouchedThinkingChoice(budgetD)).toBe(THINKING_OFF);
    expect(untouchedThinkingChoice(mandatoryE)).toBe("low");
    // Only "max" listed: the old request carried no level, so the provider
    // picked, and that is what untouched still shows.
    expect(untouchedThinkingChoice(maxOnly)).toBe(THINKING_DEFAULT);
    for (const m of [plainA, unknownB, mandatoryF]) {
      expect(untouchedThinkingChoice(m), m.id).toBeNull();
    }
  });

  it("a stored word the model does not offer shows the untouched value", () => {
    expect(resolveThinkingChoice(mandatoryE, "high")).toBe("high");
    expect(resolveThinkingChoice(mandatoryE, "medium")).toBe("low");
    expect(resolveThinkingChoice(mandatoryE, THINKING_OFF)).toBe("low");
    expect(resolveThinkingChoice(budgetD, "high")).toBe(THINKING_OFF);
    expect(resolveThinkingChoice(optionalC, "turbo")).toBe(THINKING_OFF);
    expect(resolveThinkingChoice(optionalC, null)).toBe(THINKING_OFF);
  });

  it("Provider default equals Off only where the list says off by default", () => {
    expect(providerDefaultIsOff(optionalC)).toBe(true);
    expect(providerDefaultIsOff(optionalOn)).toBe(false);
    expect(providerDefaultIsOff({ ...mandatoryE, reasoning: { ...mandatoryE.reasoning, default_enabled: false } })).toBe(false);
  });

  it("a listing that is not a list does not fail the model's parse", () => {
    const parsed = ModelSchema.parse({ ...mandatoryE, reasoning: { mandatory: true, supported_efforts: "high,low" } });
    expect(parsed.reasoning?.supported_efforts).toBeNull();
    expect(thinkingOptions(parsed)).toEqual([]);
    // Ground: a real list still parses as given.
    expect(ModelSchema.parse(mandatoryE).reasoning?.supported_efforts).toEqual(["max", "high", "low"]);
  });
});

describe("the budget warning", () => {
  it("a budget model with too little room warns", () => {
    const small = THINKING_BUDGET_FLOOR + THINKING_REPLY_ROOM_MIN - 1;
    expect(thinkingBudgetTooSmall(budgetD, THINKING_DEFAULT, small)).toBe(true);
    const enough = THINKING_BUDGET_FLOOR + THINKING_REPLY_ROOM_MIN;
    expect(thinkingBudgetTooSmall(budgetD, THINKING_DEFAULT, enough)).toBe(false);
  });

  it("a higher level takes a larger share", () => {
    const budgetC = {
      ...optionalC,
      reasoning: { ...optionalC.reasoning, supports_max_tokens: true },
    };
    expect(thinkingBudget("high", 4096)).toBeGreaterThan(thinkingBudget("low", 4096));
    expect(thinkingBudgetTooSmall(budgetC, "low", 4096)).toBe(false);
    expect(thinkingBudgetTooSmall(budgetC, "high", 4096)).toBe(true);
  });

  it("Provider default that means no thinking has no budget to warn about", () => {
    expect(providerDefaultIsOff(offBudget)).toBe(true);
    expect(thinkingBudgetTooSmall(offBudget, THINKING_DEFAULT, 1024)).toBe(false);
    // Positive control: a level on the same model still warns.
    expect(thinkingBudgetTooSmall(offBudget, "high", 1024)).toBe(true);
  });

  it("GROUND: Off, and a model that is not budget based, never warn", () => {
    expect(thinkingBudgetTooSmall(budgetD, THINKING_OFF, 1)).toBe(false);
    expect(thinkingBudgetTooSmall(optionalC, "max", 1)).toBe(false);
    expect(thinkingBudgetTooSmall(mandatoryE, "max", 1)).toBe(false);
  });
});

describe("the payload builders", () => {
  it("send, regenerate and edit all carry the choice", () => {
    const params = { temperature: 0.7, thinking_effort: "high" };
    for (const payload of [
      buildCompletionPayload({ message: "m", modelId: optionalC.id, generationParams: params, model: optionalC }),
      buildRegeneratePayload({ modelId: optionalC.id, generationParams: params, model: optionalC }),
      buildEditPayload({ message: "m", modelId: optionalC.id, generationParams: params, model: optionalC }),
    ]) {
      expect(payload.generation_params).toEqual({ temperature: 0.7, thinking_effort: "high" });
    }
  });

  it("the old switch still travels as before", () => {
    const payload = buildRegeneratePayload({
      modelId: optionalC.id,
      generationParams: { thinking: true },
      model: optionalC,
    });
    expect(payload.generation_params).toEqual({ thinking: true });
  });

  it("GROUND: nothing set sends nothing extra", () => {
    const payload = buildCompletionPayload({
      message: "m",
      modelId: optionalC.id,
      generationParams: { temperature: 0.7, thinking: false },
      model: optionalC,
    });
    expect(payload.generation_params).toEqual({ temperature: 0.7 });
  });
});

describe("the error sentences about thinking", () => {
  const words = (text: string) => text.split(/\s+/).filter(Boolean).length;

  it("the private-endpoint notice is short, action first, without jargon", () => {
    expect(isKnownErrorCode("reasoning_not_sent_privately")).toBe(true);
    const text = getErrorMessage("reasoning_not_sent_privately");
    expect(text).not.toBe(getErrorMessage("stop_not_sent_privately"));
    expect(words(text)).toBeLessThanOrEqual(12);
    expect(text).toMatch(/^Choose /);
    expect(text.toLowerCase()).not.toContain("privacy-safe");
  });

  it("the reply-budget error names the two rows by their labels, and no Off", () => {
    const text = getErrorMessage("reasoning_used_reply_budget");
    expect(isKnownErrorCode("reasoning_used_reply_budget")).toBe(true);
    expect(text).toContain(MAX_NEW_TOKENS_LABEL);
    expect(text).toContain(THINKING_EFFORT_LABEL);
    expect(text).not.toMatch(/\boff\b/i);
    expect(text).not.toMatch(/Max tokens/);
    expect(words(text)).toBeLessThanOrEqual(12);
    expect(text).toMatch(/^Raise /);
  });
});

// -- the dialog --

interface Held {
  resolve: () => void;
}

interface Vault {
  efforts: Record<string, string>;
  posts: { model_id: string; effort: string | null }[];
  /** Hold answers back until released. The vault still commits a save when
   *  its request arrives, as the real route does. */
  holdPosts: boolean;
  holdGets: boolean;
  heldPosts: Held[];
  heldGets: Held[];
  /** Refuse saves with this status. */
  failPosts: number | null;
  /** Commit a held save when its answer is released, not when it arrives,
   *  so a test can make the server apply two saves in either order. */
  commitOnRelease: boolean;
}

function stubFetch(vault: Vault) {
  const json = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  const later = (queue: Held[], answer: () => Response) =>
    new Promise<Response>((resolve) => queue.push({ resolve: () => resolve(answer()) }));
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = (init?.method ?? "GET").toUpperCase();
    if (url.includes("/vault/status")) return json({ initialized: true, unlocked: true });
    if (method === "POST" && url.includes("/settings/thinking-effort")) {
      const body = JSON.parse(String(init?.body));
      vault.posts.push(body);
      if (vault.failPosts) {
        const status = vault.failPosts;
        return json({ detail: "database_busy" }, status);
      }
      const commit = () => {
        const next = { ...vault.efforts };
        if (body.effort === null) delete next[body.model_id];
        else next[body.model_id] = body.effort;
        vault.efforts = next;
        return next;
      };
      if (vault.holdPosts && vault.commitOnRelease) {
        return later(vault.heldPosts, () => json({ ok: true, thinking_efforts: commit() }));
      }
      const next = commit();
      const answer = () => json({ ok: true, thinking_efforts: next });
      return vault.holdPosts ? later(vault.heldPosts, answer) : answer();
    }
    if (method === "GET" && url.endsWith("/settings")) {
      // The read happens now; only its answer can be late.
      const snapshot = vault.efforts;
      const answer = () => json({ ...settingsFixture, thinking_efforts: snapshot });
      return vault.holdGets ? later(vault.heldGets, answer) : answer();
    }
    if (url.includes("/models/openrouter")) {
      return json({ source: "user", cached: true, count: MODELS.length, models: MODELS });
    }
    return json({ detail: `No mock for ${method} ${url}` }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function Extra({ children }: { children: ReactNode }) {
  return (
    <TooltipProvider>
      <GenerationSettingsProvider>{children}</GenerationSettingsProvider>
    </TooltipProvider>
  );
}

async function renderDialog(model: Model, efforts: Record<string, string> = {}) {
  const vault: Vault = {
    efforts, posts: [], holdPosts: false, holdGets: false,
    heldPosts: [], heldGets: [], failPosts: null, commitOnRelease: false,
  };
  const api: { current: ReturnType<typeof useGenerationSettings> | null } = { current: null };
  function Probe({ take }: { take: (a: ReturnType<typeof useGenerationSettings>) => void }) {
    take(useGenerationSettings());
    return null;
  }
  useUiStore.setState({ selectedModelId: model.id });
  stubFetch(vault);
  const { queryClient } = renderWithQueryClient(
    <>
      <ModelPanel />
      <Probe take={(next) => (api.current = next)} />
    </>,
    { wrapper: Extra },
  );
  const user = userEvent.setup();
  await user.click(await screen.findByTestId("generation-settings-trigger"));
  expect(await screen.findByText("Sampling")).toBeInTheDocument();
  await waitFor(() => expect(screen.getAllByText(model.id).length).toBeGreaterThan(0));
  return { api, user, vault, queryClient };
}

function effortSelect(): HTMLSelectElement {
  return screen.getByRole("combobox", { name: THINKING_EFFORT_LABEL }) as HTMLSelectElement;
}

function optionLabels(): string[] {
  return within(effortSelect())
    .getAllByRole("option")
    .map((o) => o.textContent ?? "");
}

const labelsOf = (choices: ThinkingChoice[]) => choices.map((c) => THINKING_CHOICE_LABELS[c]);
const sent = (api: { current: ReturnType<typeof useGenerationSettings> | null }) =>
  api.current!.getRequestSettings().generationParams.thinking_effort;
const release = async (held: Held[]) => {
  await act(async () => {
    held.shift()!.resolve();
  });
};
/** Past react-query's batched notify (a zero timeout), so the screen shows
 *  whatever the cache now says. */
const settle = () => act(() => new Promise<void>((resolve) => setTimeout(resolve, 20)));
const describedBy = (el: Element) =>
  (el.getAttribute("aria-describedby") ?? "")
    .split(/\s+/)
    .filter(Boolean)
    .map((id) => document.getElementById(id)?.textContent ?? `MISSING ${id}`);

describe("the thinking effort row", () => {
  beforeEach(() => {
    useUiStore.setState({ selectedModelId: null, selectedChatId: null, selectedCharacterId: null });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("A: a model with no reasoning shows no row", async () => {
    await renderDialog(plainA);
    // Positive control: the dialog rendered its other rows.
    expect(screen.getByRole("switch", { name: "Let replies include pictures" })).toBeInTheDocument();
    expect(screen.queryByTestId("thinking-setting")).toBeNull();
    expect(screen.queryByRole("combobox", { name: THINKING_EFFORT_LABEL })).toBeNull();
  });

  it("B: unknown rules show plain text, at full ink and reachable, with the reason", async () => {
    const { user } = await renderDialog(unknownB);
    expect(screen.queryByRole("combobox", { name: THINKING_EFFORT_LABEL })).toBeNull();
    const state = screen.getByRole("status", { name: THINKING_EFFORT_LABEL });
    expect(state).toHaveTextContent(THINKING_UNKNOWN_LABEL);
    expect(describedBy(state)).toEqual([THINKING_UNKNOWN_NOTE]);
    expect(screen.getByTestId("thinking-setting")).not.toHaveClass("is-disabled");
    state.blur();
    // Reachable: the keyboard lands on it.
    for (let i = 0; i < 40 && document.activeElement !== state; i += 1) await user.tab();
    expect(document.activeElement).toBe(state);
  });

  it("C: optional offers Off, Provider default and every listed level; Off by default", async () => {
    const { api } = await renderDialog(optionalC);
    expect(effortSelect()).toBeEnabled();
    expect(optionLabels()).toEqual(labelsOf([THINKING_OFF, THINKING_DEFAULT, ...REASONING_EFFORT_ORDER]));
    expect(effortSelect().value).toBe(THINKING_OFF);
    expect(describedBy(effortSelect())).toEqual([THINKING_OFF_NOTE]);
    // Untouched: the request is the one sent before the row existed.
    expect(JSON.stringify(api.current!.getRequestSettings().generationParams)).toBe(
      JSON.stringify({ max_tokens: GENERATION_SETTINGS_DEFAULTS.max_tokens }),
    );
  });

  it("C: picking a level saves it for this model and sends it", async () => {
    const { api, user, vault } = await renderDialog(optionalOn);
    await user.selectOptions(effortSelect(), THINKING_CHOICE_LABELS.high);
    expect(vault.posts).toEqual([{ model_id: optionalOn.id, effort: "high" }]);
    await waitFor(() => expect(effortSelect().value).toBe("high"));
    expect(describedBy(effortSelect())).toEqual([THINKING_LEVEL_NOTE]);
    expect(sent(api)).toBe("high");

    await user.selectOptions(effortSelect(), THINKING_CHOICE_LABELS.default);
    await waitFor(() => expect(effortSelect().value).toBe(THINKING_DEFAULT));
    expect(describedBy(effortSelect())).toEqual([THINKING_DEFAULT_NOTE]);
    expect(sent(api)).toBe(THINKING_DEFAULT);
  });

  it("C: Provider default says it means no thinking where the list says so", async () => {
    const { user } = await renderDialog(optionalC);
    await user.selectOptions(effortSelect(), THINKING_CHOICE_LABELS.default);
    await waitFor(() => expect(effortSelect().value).toBe(THINKING_DEFAULT));
    expect(describedBy(effortSelect())).toEqual([THINKING_DEFAULT_IS_OFF_NOTE]);
    expect(screen.queryByText(THINKING_DEFAULT_NOTE)).toBeNull();
  });

  it("D: budget model warns, announced and linked, when max output is small", async () => {
    const { user } = await renderDialog(budgetD);
    expect(optionLabels()).toEqual(labelsOf([THINKING_OFF, THINKING_DEFAULT]));
    const region = within(screen.getByTestId("thinking-setting")).getByRole("status");
    // Ground: Off never warns, and the live region is already there, empty.
    expect(region).toBeEmptyDOMElement();
    await user.selectOptions(effortSelect(), THINKING_CHOICE_LABELS.default);
    expect(GENERATION_SETTINGS_DEFAULTS.max_tokens).toBeLessThan(
      THINKING_BUDGET_FLOOR + THINKING_REPLY_ROOM_MIN,
    );
    await waitFor(() => expect(region).toHaveTextContent(THINKING_BUDGET_WARNING));
    const warning = screen.getByTestId("thinking-budget-warning");
    // Normal helper weight, not the dim support note.
    expect(warning).toHaveClass("generation-helper");
    expect(warning).not.toHaveClass("generation-support-note");
    expect(describedBy(effortSelect())).toEqual([THINKING_DEFAULT_NOTE, THINKING_BUDGET_WARNING]);
    // The row it names exists under that exact label, and raising it clears it.
    const box = screen.getByRole("spinbutton", { name: `${MAX_NEW_TOKENS_LABEL} value` });
    await user.clear(box);
    await user.type(box, String(THINKING_BUDGET_FLOOR + THINKING_REPLY_ROOM_MIN) + "{Enter}");
    await waitFor(() => expect(region).toBeEmptyDOMElement());
    expect(describedBy(effortSelect())).toEqual([THINKING_DEFAULT_NOTE]);
  });

  it("E: mandatory offers Provider default and its levels, no Off; lowest by default", async () => {
    const { api, user, vault } = await renderDialog(mandatoryE);
    expect(optionLabels()).toEqual(labelsOf([THINKING_DEFAULT, "low", "high", "max"]));
    expect(optionLabels()).not.toContain(THINKING_CHOICE_LABELS.off);
    expect(effortSelect().value).toBe("low");
    expect(sent(api)).toBeUndefined();
    await user.selectOptions(effortSelect(), THINKING_CHOICE_LABELS.default);
    expect(vault.posts.at(-1)).toEqual({ model_id: mandatoryE.id, effort: THINKING_DEFAULT });
    expect(screen.queryByText(THINKING_BUDGET_WARNING)).toBeNull();
  });

  it("E: a model listing only max shows Provider default untouched and sends nothing", async () => {
    const { api } = await renderDialog(maxOnly);
    expect(optionLabels()).toEqual(labelsOf([THINKING_DEFAULT, "max"]));
    expect(effortSelect().value).toBe(THINKING_DEFAULT);
    expect(sent(api)).toBeUndefined();
  });

  it("F: mandatory with no levels shows plain text saying always on", async () => {
    await renderDialog(mandatoryF);
    expect(screen.queryByRole("combobox", { name: THINKING_EFFORT_LABEL })).toBeNull();
    const state = screen.getByRole("status", { name: THINKING_EFFORT_LABEL });
    expect(state).toHaveTextContent(THINKING_MANDATORY_LABEL);
    expect(state).toHaveAttribute("tabindex", "0");
    expect(describedBy(state)).toEqual([THINKING_MANDATORY_NOTE]);
    expect(screen.getByTestId("thinking-setting")).not.toHaveClass("is-disabled");
  });

  async function openDetails(user: ReturnType<typeof userEvent.setup>) {
    const door = within(screen.getByTestId("thinking-details")).getByRole("button", { name: THINKING_DETAILS_LABEL });
    expect(door).toHaveAttribute("aria-expanded", "false");
    await user.click(door);
    expect(door).toHaveAttribute("aria-expanded", "true");
    return document.getElementById(door.getAttribute("aria-controls")!)!;
  }

  it("the details door says the choice is saved per model and about the cache", async () => {
    const { user } = await renderDialog(optionalC);
    const body = await openDetails(user);
    expect(body).toHaveTextContent(THINKING_DETAILS_SAVED);
    expect(body).toHaveTextContent(THINKING_DETAILS_CACHE);
    // Not a model that always thinks: the untouched line would not apply.
    expect(body).not.toHaveTextContent(THINKING_DETAILS_UNTOUCHED);
  });

  it("the untouched line shows only where untouched is the lowest level", async () => {
    const { user } = await renderDialog(mandatoryE);
    const body = await openDetails(user);
    expect(body).toHaveTextContent(THINKING_DETAILS_UNTOUCHED);
    expect(body).toHaveTextContent(THINKING_DETAILS_SAVED);
  });

  it("GROUND: a max-only model, untouched on Provider default, has no untouched line", async () => {
    const { user } = await renderDialog(maxOnly);
    const body = await openDetails(user);
    expect(body).toHaveTextContent(THINKING_DETAILS_SAVED);
    expect(body).not.toHaveTextContent(THINKING_DETAILS_UNTOUCHED);
  });

  it("Provider default meaning no thinking shows one line, not a warning too", async () => {
    const { user } = await renderDialog(offBudget);
    await user.selectOptions(effortSelect(), THINKING_CHOICE_LABELS.default);
    await waitFor(() => expect(effortSelect().value).toBe(THINKING_DEFAULT));
    expect(describedBy(effortSelect())).toEqual([THINKING_DEFAULT_IS_OFF_NOTE]);
    expect(screen.queryByText(THINKING_BUDGET_WARNING)).toBeNull();
    // Positive control: a level on the same model does warn.
    await user.selectOptions(effortSelect(), THINKING_CHOICE_LABELS.high);
    await waitFor(() => expect(describedBy(effortSelect())).toEqual([THINKING_LEVEL_NOTE, THINKING_BUDGET_WARNING]));
  });

  it("each model shows its own last choice; a model with none shows today's value", async () => {
    const { api } = await renderDialog(optionalC, { [mandatoryE.id]: "high" });
    expect(effortSelect().value).toBe(THINKING_OFF);
    act(() => useUiStore.setState({ selectedModelId: mandatoryE.id }));
    await waitFor(() => expect(effortSelect().value).toBe("high"));
    expect(sent(api)).toBe("high");
    act(() => useUiStore.setState({ selectedModelId: optionalC.id }));
    await waitFor(() => expect(effortSelect().value).toBe(THINKING_OFF));
    expect(sent(api)).toBeUndefined();
  });

  it("a stored word the model does not list shows today's value", async () => {
    await renderDialog(mandatoryE, { [mandatoryE.id]: "medium" });
    await waitFor(() => expect(effortSelect().value).toBe("low"));
  });

  it("Reset all forgets this model's choice", async () => {
    const { api, user, vault } = await renderDialog(optionalC, { [optionalC.id]: "high" });
    await waitFor(() => expect(effortSelect().value).toBe("high"));
    await user.click(screen.getByRole("button", { name: "Reset all" }));
    expect(vault.posts.at(-1)).toEqual({ model_id: optionalC.id, effort: null });
    await waitFor(() => expect(effortSelect().value).toBe(THINKING_OFF));
    expect(sent(api)).toBeUndefined();
  });

  it("GROUND: Reset all with nothing stored writes nothing", async () => {
    const { user, vault } = await renderDialog(optionalC);
    await user.click(screen.getByRole("button", { name: "Reset all" }));
    expect(vault.posts).toEqual([]);
  });
});

describe("saves and reads that cross", () => {
  beforeEach(() => {
    useUiStore.setState({ selectedModelId: null, selectedChatId: null, selectedCharacterId: null });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  const refetch = (qc: QueryClient) =>
    act(async () => {
      void qc.invalidateQueries({ queryKey: keys.settings() });
    });

  it("a read that lands while the save is pending does not undo the choice", async () => {
    const { api, user, vault, queryClient } = await renderDialog(optionalC);
    vault.holdPosts = true;
    await user.selectOptions(effortSelect(), THINKING_CHOICE_LABELS.max);
    await waitFor(() => expect(vault.heldPosts.length).toBe(1));
    // The server has not committed yet when this read runs, so it answers
    // with the old, empty map.
    vault.efforts = {};
    await refetch(queryClient);
    await settle();
    expect(queryClient.getQueryData<{ thinking_efforts: object }>(keys.settings())?.thinking_efforts).toEqual({});
    expect(effortSelect().value).toBe("max");
    expect(sent(api)).toBe("max");
    vault.efforts = { [optionalC.id]: "max" };
    vault.holdPosts = false;
    await release(vault.heldPosts);
    await waitFor(() => expect(effortSelect().value).toBe("max"));
    expect(sent(api)).toBe("max");
  });

  it("a read that started before the save and lands after it is ignored", async () => {
    const { api, user, vault, queryClient } = await renderDialog(optionalC);
    vault.holdGets = true;
    await refetch(queryClient);
    await waitFor(() => expect(vault.heldGets.length).toBe(1));
    vault.holdGets = false;
    vault.holdPosts = true;
    await user.selectOptions(effortSelect(), THINKING_CHOICE_LABELS.low);
    await waitFor(() => expect(vault.heldPosts.length).toBe(1));
    expect(vault.efforts).toEqual({ [optionalC.id]: "low" });
    // The stale read, taken before the save, lands while the save is out.
    await release(vault.heldGets);
    await settle();
    // It was cancelled, so the cached settings still hold the choice, not
    // the older read (anything reading the cache directly sees "low").
    expect(
      queryClient.getQueryData<{ thinking_efforts: Record<string, string> }>(keys.settings())
        ?.thinking_efforts,
    ).toEqual({ [optionalC.id]: "low" });
    expect(effortSelect().value).toBe("low");
    expect(sent(api)).toBe("low");
    vault.holdPosts = false;
    await release(vault.heldPosts);
    await waitFor(() => expect(sent(api)).toBe("low"));
    expect(effortSelect().value).toBe("low");
  });

  it("saves go one at a time: the second waits for the first answer", async () => {
    const { api, user, vault } = await renderDialog(optionalC);
    vault.holdPosts = true;
    await user.selectOptions(effortSelect(), THINKING_CHOICE_LABELS.high);
    await user.selectOptions(effortSelect(), THINKING_CHOICE_LABELS.low);
    await waitFor(() => expect(vault.heldPosts.length).toBe(1));
    await settle();
    // Only the first save has gone out; the second is waiting its turn.
    expect(vault.posts.map((p) => p.effort)).toEqual(["high"]);
    expect(effortSelect().value).toBe("low");
    expect(sent(api)).toBe("low");
    vault.holdPosts = false;
    await release(vault.heldPosts);
    await waitFor(() => expect(vault.posts.map((p) => p.effort)).toEqual(["high", "low"]));
    await waitFor(() => expect(effortSelect().value).toBe("low"));
    expect(vault.efforts).toEqual({ [optionalC.id]: "low" });
    expect(sent(api)).toBe("low");
  });

  it("two quick saves end on the last pick whatever order the server applies them", async () => {
    const { api, user, vault } = await renderDialog(optionalC);
    vault.holdPosts = true;
    vault.commitOnRelease = true;
    await user.selectOptions(effortSelect(), THINKING_CHOICE_LABELS.high);
    await user.selectOptions(effortSelect(), THINKING_CHOICE_LABELS.low);
    // Ground: the screen already shows the last pick.
    await waitFor(() => expect(effortSelect().value).toBe("low"));
    // The server applies whatever is waiting newest first. With saves one
    // at a time there is never more than one waiting, so it cannot reorder.
    for (let round = 0; round < 4; round += 1) {
      await waitFor(() => expect(vault.heldPosts.length).toBeGreaterThan(0));
      await act(async () => {
        while (vault.heldPosts.length > 0) vault.heldPosts.pop()!.resolve();
      });
      await settle();
      if (vault.posts.length >= 2 && vault.heldPosts.length === 0) break;
    }
    expect(vault.posts.map((p) => p.effort)).toEqual(["high", "low"]);
    expect(vault.efforts).toEqual({ [optionalC.id]: "low" });
    await waitFor(() => expect(effortSelect().value).toBe("low"));
    expect(sent(api)).toBe("low");
  });

  it("a refused save puts the earlier choice back and says so", async () => {
    const { api, user, vault } = await renderDialog(optionalC, { [optionalC.id]: "high" });
    await waitFor(() => expect(effortSelect().value).toBe("high"));
    vault.failPosts = 503;
    // Reads are held, so only the rollback itself can put "high" back.
    vault.holdGets = true;
    await user.selectOptions(effortSelect(), THINKING_CHOICE_LABELS.max);
    await waitFor(() => expect(effortSelect().value).toBe("high"));
    expect(sent(api)).toBe("high");
    // Ground: the save was really attempted.
    expect(vault.posts.at(-1)).toEqual({ model_id: optionalC.id, effort: "max" });
  });
});
