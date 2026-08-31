/**
 * U-13 - the four lint rules, proved from the REQUEST instead of the source.
 *
 * S-01, S-08, S-20 and S-21 all constrain whether a string appears in the
 * source text. None of them can see:
 *
 *   * a host assembled at runtime, or read from a variable;
 *   * a completion request sent from a module that is not one of the two
 *     approved ones (the scan's glob does not even cover `src/lib/generation`);
 *   * a provider privacy field written as `{ ["z" + "dr"]: true }`, renamed,
 *     or nested inside an object built from constants;
 *   * an image turned into a string by `canvas.toDataURL()`,
 *     `FileReader.readAsDataURL(file)` or `btoa(...)`, none of which spells
 *     `image_url`, `data:image` or `;base64` at the call site.
 *
 * These tests read what the transport was actually handed. The four lint
 * rules stay exactly where they are - a source scan sees code that has never
 * run, and a captured request sees only code that has - and neither is
 * sufficient on its own.
 *
 * The contract key set is IMPORTED from the schema. Retyping it here would
 * make this test agree with itself rather than with the thing it guards.
 */
import { describe, it, expect, afterEach, vi } from "vitest";

import { sentinelLog } from "@/test/sentinels";
import { API_BASE } from "@/lib/api/base";
import { completeChat, regenerateMessage } from "@/lib/api/completions";
import {
  CompletionRequestSchema,
  RegenerateRequestSchema,
} from "@/lib/schemas/completions";
import { ALLOWED_GEN_PARAM_KEYS } from "@/lib/generation/generationParams";

/** Every JSON body the transport was handed, parsed. */
function sentBodies(): Array<Record<string, unknown>> {
  return sentinelLog().egress
    .filter((r) => typeof r.body === "string" && r.body.startsWith("{"))
    .map((r) => JSON.parse(r.body as string) as Record<string, unknown>);
}

/** Every key at every depth of a value. */
function keysAtEveryDepth(value: unknown, found: string[] = []): string[] {
  if (Array.isArray(value)) {
    for (const item of value) keysAtEveryDepth(item, found);
  } else if (value && typeof value === "object") {
    for (const [key, inner] of Object.entries(value)) {
      found.push(key);
      keysAtEveryDepth(inner, found);
    }
  }
  return found;
}

/** A generation-settings fixture with every documented knob filled in. */
const EVERY_KNOB = {
  temperature: 0.8,
  top_p: 0.9,
  top_k: 40,
  repetition_penalty: 1.1,
  max_tokens: 512,
  seed: 7,
  stop: ["END"],
};

/**
 * Drive a real send and let it fail.
 *
 * Deliberately NOT `vi.spyOn(globalThis, "fetch")`: the sentinel IS the
 * global fetch, so replacing it would remove the very thing these tests
 * read. The request is made for real, the sentinel records it before calling
 * through, and jsdom has nothing listening - so the promise rejects and that
 * rejection is the expected outcome, not a failure.
 */
async function send(run: () => Promise<unknown>): Promise<void> {
  try {
    await run();
  } catch {
    /* nothing is listening; the record was made before the attempt */
  }
}

afterEach(() => vi.restoreAllMocks());

describe("S-01 - where a request may go", () => {
  it("the API base is this machine, and nothing else", () => {
    // The lint rule looks for the provider's host name as a literal in
    // source - which is why this comment does not spell it. That rule
    // and this test ask the same question from opposite ends. This
    // asks the thing the lint rule is about: where do requests go.
    const host = API_BASE.startsWith("http")
      ? new URL(API_BASE).hostname
      : "";
    expect(["localhost", "127.0.0.1", "[::1]", "::1", ""]).toContain(host);
  });

  it("a completion really is sent to that base", async () => {
    await send(() => completeChat(1, { message: "hi", model_id: "vendor/model" }));

    const sent = sentinelLog().egress.filter((r) => r.kind === "fetch");
    expect(sent.length).toBeGreaterThan(0);   // ground: something was sent
    for (const record of sent) {
      const host = record.url.startsWith("http")
        ? new URL(record.url).hostname
        : "";
      expect(["localhost", "127.0.0.1", "[::1]", "::1", ""]).toContain(host);
    }
  });
});

describe("S-08 - which module a completion may leave from", () => {
  it("a send names the approved module in its own stack", async () => {
    await send(() => completeChat(1, { message: "hi", model_id: "vendor/model" }));

    const record = sentinelLog().egress.find((r) =>
      r.url.includes("/complete"));
    expect(record).toBeTruthy();
    // WEAKER THAN THE FRAME IMMEDIATELY ABOVE, deliberately. Vitest's
    // transform rewrites frames, and a test that pinned a frame POSITION
    // would be asserting about the bundler. What it can honestly say is that
    // the approved module is on the stack of the request at all - which a
    // send from anywhere else would not produce.
    expect(record!.stack).toMatch(/lib[\\/]api[\\/](completions|stream|client)/);
  });

  it("a regenerate does the same", async () => {
    await send(() => regenerateMessage(1, 2, { model_id: "vendor/model" }));

    const record = sentinelLog().egress.find((r) =>
      r.url.includes("/regenerate"));
    expect(record).toBeTruthy();
    expect(record!.stack).toMatch(/lib[\\/]api[\\/](completions|stream|client)/);
  });
});

describe("S-20 - what a completion body may carry", () => {
  it("the key set is exactly the documented contract", async () => {
    await send(() => completeChat(1, {
      message: "hi",
      model_id: "vendor/model",
      generation_params: EVERY_KNOB,
      persona_id: 3,
      context_budget_tokens: 8192,
    }));

    const [body] = sentBodies();
    expect(body).toBeTruthy();
    // IMPORTED, not retyped: the schema is the contract.
    const allowed = new Set(Object.keys(CompletionRequestSchema.shape));
    for (const key of Object.keys(body)) expect(allowed).toContain(key);
    // And the knobs inside it are the documented ones.
    const params = body.generation_params as Record<string, unknown>;
    for (const key of Object.keys(params)) {
      expect(ALLOWED_GEN_PARAM_KEYS.has(key)).toBe(true);
    }
  });

  it("no provider privacy field appears at ANY depth", async () => {
    await send(() => completeChat(1, {
      message: "hi",
      model_id: "vendor/model",
      generation_params: EVERY_KNOB,
    }));

    const forbidden = ["provider", "zdr", "data_collection", "allow_fallbacks"];
    for (const body of sentBodies()) {
      const keys = keysAtEveryDepth(body);
      expect(keys.length).toBeGreaterThan(3);   // ground: the walk works
      for (const bad of forbidden) expect(keys).not.toContain(bad);
    }
  });

  it("regenerate carries the same contract, minus the message", async () => {
    await send(() => regenerateMessage(1, 2, {
      model_id: "vendor/model",
      generation_params: EVERY_KNOB,
    }));

    const [body] = sentBodies();
    const allowed = new Set(Object.keys(RegenerateRequestSchema.shape));
    for (const key of Object.keys(body)) expect(allowed).toContain(key);
    expect(allowed.has("message")).toBe(false);
  });
});

describe("S-21 - an image never becomes a string", () => {
  it("turning a canvas into a data URI throws", () => {
    const canvas = document.createElement("canvas");
    expect(() => canvas.toDataURL()).toThrow(/IMAGE SENTINEL/);
  });

  it("reading a file as a data URI throws", () => {
    const reader = new FileReader();
    expect(() => reader.readAsDataURL(new Blob(["x"]))).toThrow(
      /IMAGE SENTINEL/);
  });

  it("base64-encoding anything throws", () => {
    expect(() => btoa("x")).toThrow(/IMAGE SENTINEL/);
  });

  it("toBlob with a real mime type still works", () => {
    // GROUND CONTROL, and it is a real caller: chatBackground.ts encodes a
    // JPEG this way. A tripwire that broke it would be worse than the gap.
    const canvas = document.createElement("canvas");
    expect(() => canvas.toBlob(() => {}, "image/jpeg", 0.8)).not.toThrow();
  });

  it("no completion body carries an image, at any depth", async () => {
    await send(() => completeChat(1, {
      message: "look at this",
      model_id: "vendor/model",
      attachments: [11, 12],
    }));

    for (const body of sentBodies()) {
      expect(keysAtEveryDepth(body)).not.toContain("image_url");
      const text = JSON.stringify(body);
      expect(text).not.toMatch(/data:image/);
      expect(text).not.toMatch(/base64/);
    }
    // Ground: the attachment ids DID travel, so this is not passing on an
    // empty body.
    expect(sentBodies()[0].attachments).toEqual([11, 12]);
  });
});
