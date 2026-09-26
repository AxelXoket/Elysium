/**
 * The send-time H4 refusal reaches the chat with the module names.
 *
 * When a message is refused because the enabled prompt modules no longer fit,
 * the completion route answers 400 `prompt_modules_over_budget` with a JSON
 * body listing each module's id, name and token cost (the same body the
 * toggle refusal has). The chat must say WHICH modules and what they cost
 * (dF 4), so the stream client reads the body, names each module from the
 * module list, and the error path renders the sentence.
 *
 * Ground controls: a body with only the code, and a malformed module list,
 * both fall back to the catalogued sentence. Positive controls: the names
 * come from the list; a module the list does not know keeps the body's name,
 * and with no name at all is named by number rather than dropped.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";

import { streamCompletion } from "@/lib/api/stream";
import { OVER_BUDGET_FIX, isOverBudgetError } from "@/lib/api/promptModules";
import { formatCount } from "@/lib/formatCount";
import { getErrorMessage, parseApiError, useErrorStore } from "@/lib/errors";
import { queryClient } from "@/lib/query/queryClient";

const CODE = "prompt_modules_over_budget";

function stubFetch(body: unknown, list: unknown, extraHeaders: Record<string, string> = {}) {
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    if (method === "GET" && url.endsWith("/prompt-modules")) {
      return new Response(JSON.stringify(list), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    return new Response(JSON.stringify(body), {
      status: 400,
      headers: { "Content-Type": "application/json", ...extraHeaders },
    });
  });
  vi.stubGlobal("fetch", mock);
  return mock;
}

const LIST = {
  modules: [
    { id: 1, name: "Pacing", content: "x", enabled: true, sort_order: 0 },
    { id: 2, name: "Sensory detail", content: "y", enabled: true, sort_order: 1 },
  ],
};

async function sendAndCatch(): Promise<unknown> {
  try {
    await streamCompletion("/chats/1/completions", {}, { onEvent: () => undefined });
  } catch (err) {
    return err;
  }
  throw new Error("the send was not refused");
}

beforeEach(() => {
  queryClient.clear();
  useErrorStore.getState().clearAll();
});
afterEach(() => {
  vi.unstubAllGlobals();
  queryClient.clear();
});

describe("send-time over-budget refusal", () => {
  it("the catalogued sentence ends with the same fix the module notice gives", () => {
    // The body-less fallback and the named notice must point at the same
    // control, by its label.
    const sentence = getErrorMessage(CODE);
    expect(sentence.endsWith(` ${OVER_BUDGET_FIX}`)).toBe(true);
    // GROUND CONTROL: the fix is not simply in every sentence.
    expect(getErrorMessage("context_too_large").endsWith(OVER_BUDGET_FIX)).toBe(false);
  });

  it("names the budget setting by its label, Context budget, never another name", () => {
    for (const code of [CODE, "context_too_large"]) {
      const sentence = getErrorMessage(code);
      // POSITIVE CONTROL: the sentence does talk about the setting.
      expect(sentence).toContain("Context budget");
      expect(sentence).not.toMatch(/context window|the context budget/);
    }
  });

  it("names each module and its cost in the chat's error", async () => {
    // The shape main.py's PromptModuleError handler answers with.
    stubFetch(
      {
        detail: CODE,
        modules: [
          { id: 1, name: "Pacing", tokens: 4200 },
          { id: 2, name: "Sensory detail", tokens: 640 },
        ],
        total_tokens: 4845,
        room_tokens: 4549,
      },
      LIST,
    );
    const err = await sendAndCatch();
    expect(isOverBudgetError(err)).toBe(true);
    const message = parseApiError(err).message;
    // Counts in the one grouped form the module pages use ("4,200").
    expect(message).toContain(`Pacing (about ${formatCount(4200)} tokens)`);
    expect(formatCount(4200)).toBe("4,200");
    expect(message).toContain("Sensory detail (about 640 tokens)");
    // The fix names the control by its label, the same sentence as the page.
    expect(message).toContain(OVER_BUDGET_FIX);
    expect(OVER_BUDGET_FIX).toContain("Context budget");

    // And it is what the error store shows, under the catalogued code.
    useErrorStore.getState().pushError(err, "error", { chatId: 1 });
    const shown = useErrorStore.getState().errors.at(-1)!;
    expect(shown.code).toBe(CODE);
    expect(shown.message).toBe(message);
  });

  it("the name shown is the list's name for that id", async () => {
    // The list was renamed since the server built its body: the page's name wins.
    stubFetch(
      { detail: CODE, modules: [{ id: 2, name: "Old name", tokens: 7 }] },
      LIST,
    );
    const message = parseApiError(await sendAndCatch()).message;
    expect(message).toContain("Sensory detail (about 7 tokens)");
    expect(message).not.toContain("Old name");
  });

  it("a module the list does not know keeps the body name, else its number", async () => {
    stubFetch(
      {
        detail: CODE,
        modules: [
          { id: 1, name: "Pacing", tokens: 10 },
          { id: 77, name: "Gone but named", tokens: 15 },
          { id: 99, tokens: 20 },
        ],
      },
      LIST,
    );
    const message = parseApiError(await sendAndCatch()).message;
    expect(message).toContain("Pacing (about 10 tokens)");
    expect(message).toContain("Gone but named (about 15 tokens)");
    expect(message).toContain("Module 99 (about 20 tokens)");
  });

  it("the list failing to load still names modules from the body", async () => {
    const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const method = (init?.method ?? "GET").toUpperCase();
      if (method === "GET" && String(input).endsWith("/prompt-modules")) {
        return new Response("{}", { status: 500 });
      }
      return new Response(
        JSON.stringify({ detail: CODE, modules: [{ id: 1, name: "Pacing", tokens: 3 }] }),
        { status: 400, headers: { "Content-Type": "application/json" } },
      );
    });
    vi.stubGlobal("fetch", mock);
    expect(parseApiError(await sendAndCatch()).message).toContain("Pacing (about 3 tokens)");
  });

  it("GROUND: a body with only the code shows the catalogued sentence", async () => {
    stubFetch({ detail: CODE }, LIST);
    const err = await sendAndCatch();
    expect(isOverBudgetError(err)).toBe(false);
    expect(parseApiError(err).message).toBe(getErrorMessage(CODE));
  });

  it("GROUND: a malformed module list falls back the same way", async () => {
    stubFetch({ detail: CODE, modules: "not a list" }, LIST);
    const err = await sendAndCatch();
    expect(isOverBudgetError(err)).toBe(false);
    expect(parseApiError(err).detail).toBe(CODE);
    expect(parseApiError(err).message).toBe(getErrorMessage(CODE));
  });

  it("GROUND: the retired response header is not read any more", async () => {
    stubFetch({ detail: CODE }, LIST, {
      "X-Prompt-Modules-Over-Budget": JSON.stringify({ modules: [{ id: 1, tokens: 5 }] }),
    });
    const err = await sendAndCatch();
    expect(isOverBudgetError(err)).toBe(false);
    expect(parseApiError(err).message).toBe(getErrorMessage(CODE));
  });

  it("GROUND: another refusal with a modules field is not read as over budget", async () => {
    stubFetch(
      { detail: "context_too_large", modules: [{ id: 1, name: "Pacing", tokens: 5 }] },
      LIST,
    );
    const err = await sendAndCatch();
    expect(isOverBudgetError(err)).toBe(false);
    expect(parseApiError(err).message).toBe(getErrorMessage("context_too_large"));
  });
});
