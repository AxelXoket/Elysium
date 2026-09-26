/**
 * The settings page counts a module the way the server's H4 check counts it.
 *
 * Held to shared/prompt_module_cost_vectors.json, the same file the backend
 * test test_prompt_module_cost_parity.py holds prompt_layer to: code points
 * of the prepared text (macros with the stand-in names, sanitizer, trim) plus
 * one separator, and nothing for a blank module.
 */
import { describe, it, expect } from "vitest";

import vectors from "../../../../shared/prompt_module_cost_vectors.json";
import {
  charsToTokens,
  moduleChars,
  moduleCounter,
} from "@/components/settings/promptModuleCost";
import { PART_SEP } from "@/lib/prompt/assembly";

interface Case {
  id: string;
  content: string;
  expected_chars: number;
}
const CASES = (vectors as { cases: Case[] }).cases;
const byId = (id: string) => CASES.find((c) => c.id === id)!;

describe("per-module cost parity with the backend", () => {
  it.each(CASES.map((c) => [c.id, c] as const))("%s", (_id, c) => {
    expect(moduleChars(c.content)).toBe(c.expected_chars);
  });

  it("GROUND: counting UTF-16 units would not match the astral vector", () => {
    const c = byId("astral");
    // The same text counted in UTF-16 units is longer than the server's count,
    // so the parity case above really distinguishes the two units.
    expect(c.content.length + PART_SEP.length).toBeGreaterThan(c.expected_chars);
  });

  it("POSITIVE: a blank module adds nothing, a plain one adds its separator", () => {
    expect(moduleChars(byId("blank").content)).toBe(0);
    const plain = byId("ascii");
    expect(moduleChars(plain.content)).toBe(plain.content.length + PART_SEP.length);
  });

  it("the counter sums the modules that go with a send", () => {
    const a = byId("astral");
    const b = byId("names");
    const r = byId("comment_dropped");
    const counter = moduleCounter([
      { enabled: true, content: a.content },
      { enabled: true, content: b.content, triggers: ["send", "regenerate"] },
      { enabled: true, content: r.content, triggers: ["regenerate"] },
      { enabled: false, content: r.content },
    ]);
    expect(counter.enabled).toBe(2);
    expect(counter.chars).toBe(a.expected_chars + b.expected_chars);
    expect(counter.tokens).toBe(charsToTokens(a.expected_chars + b.expected_chars));
  });

  it("a blank module that is on is not counted (the server's predicate)", () => {
    const plain = byId("ascii");
    const blank = moduleCounter([
      { enabled: true, content: plain.content },
      { enabled: true, content: " 	" },
    ]);
    // Ground: the same list without the blank row gives the same answer.
    expect(blank).toEqual(moduleCounter([{ enabled: true, content: plain.content }]));
    expect(blank.enabled).toBe(1);
  });
});
