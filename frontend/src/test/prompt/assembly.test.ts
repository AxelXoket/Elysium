/**
 * The frontend mirror of the single-system-message assembly, held to the
 * SAME vectors the backend endpoint test reads
 * (shared/prompt_assembly_vectors.json), and its two copies of shared files
 * held to those files byte for byte.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect } from "vitest";

import vectors from "../../../../shared/prompt_assembly_vectors.json";
import sharedTable from "../../../../shared/prompt_sanitizer.json";
import { SANITIZER_TABLE } from "@/lib/prompt/sanitizerTable";
import {
  FINAL_NOTE_LABEL,
  LAYER,
  LAYER_FILE_TEXT,
  MODE_LAYER,
  MODE_MODULES,
  MODE_PLAIN,
  NONCE_HEX,
  PART_SEP,
  SALT_HEX,
  buildHead,
  cardText,
  fill,
  finalNotePart,
  firesOnSend,
  labelledCardBlock,
  moduleFires,
  moduleTexts,
  parseLayer,
  personaBlock,
  promptCost,
  type CardFields,
  type ModuleInput,
} from "@/lib/prompt/assembly";
import { cpLength } from "@/lib/prompt/sanitizer";
import { DEFAULT_USER_NAME } from "@/lib/prompt/cardMacros";

interface Case {
  id: string;
  card: CardFields;
  persona: { display_name: string; description: string } | null;
  modules: string[];
  /** One list per module: the generations it fires on. Absent: every one. */
  triggers?: (string[] | null)[];
  layer: boolean;
  length_words: number | null;
  expected: { mode: string; system: string; tail: string; module_chars: number };
}

interface FiresCase {
  content?: string;
  repeat?: string;
  times?: number;
  triggers: string[];
  fires: boolean;
}

const V = vectors as unknown as {
  salt_hex: number; nonce_hex: number; cases: Case[]; module_fires: FiresCase[];
};

const firesContent = (c: FiresCase): string => c.content ?? (c.repeat ?? "").repeat(c.times ?? 0);

/** The case's modules as the gauge takes them, triggers included. */
function inputsOf(c: Case): ModuleInput[] {
  return c.modules.map((content, i) => {
    const triggers = c.triggers?.[i];
    return triggers ? { content, triggers } : { content };
  });
}

function systemOf(c: Case): string {
  const userName = c.persona ? c.persona.display_name.trim() || DEFAULT_USER_NAME : DEFAULT_USER_NAME;
  const card = cardText(c.card, userName);
  const enabled = inputsOf(c).filter(moduleFires);
  const modules = moduleTexts(enabled.filter(firesOnSend), card.name, userName);
  const head = buildHead({
    card, userName, modulesEnabled: enabled.length > 0, modules,
    layerEnabled: c.layer, lengthWords: c.length_words,
  });
  return [head.text, finalNotePart(head.finalNote), personaBlock(c.persona)]
    .filter(Boolean).join(PART_SEP);
}

describe("the shared files and their frontend copies", () => {
  it("sanitizerTable.ts is shared/prompt_sanitizer.json", () => {
    expect(SANITIZER_TABLE).toEqual(sharedTable);
  });

  it("LAYER_FILE_TEXT is shared/prompt_layer_text.txt", () => {
    const onDisk = readFileSync(resolve(__dirname, "../../../../shared/prompt_layer_text.txt"), "utf8");
    expect(LAYER_FILE_TEXT.replace(/\r\n/g, "\n")).toBe(onDisk.replace(/\r\n/g, "\n"));
    expect(parseLayer(onDisk)).toEqual(LAYER);
  });

  it("salt and nonce lengths are the backend's", () => {
    expect([SALT_HEX, NONCE_HEX]).toEqual([V.salt_hex, V.nonce_hex]);
  });
});

describe("assembly vectors (backend-generated, same file as the backend test)", () => {
  it("covers all three modes", () => {
    expect(new Set(V.cases.map((c) => c.expected.mode))).toEqual(
      new Set([MODE_MODULES, MODE_LAYER, MODE_PLAIN]));
  });

  it("covers the Ek O 7 edge: an enabled module that does not fire on a send", () => {
    const edge = V.cases.filter(
      (c) => c.triggers && c.triggers.every((t) => t !== null && !t.includes("send")));
    expect(edge.length).toBeGreaterThan(0);
    for (const c of edge) {
      expect(c.layer).toBe(true);
      expect(c.expected.mode).toBe(MODE_MODULES);
    }
  });

  it("covers the blank edge: every module blank with the layer on is mode b", () => {
    const blank = V.cases.filter(
      (c) => c.modules.length > 0 && c.modules.every((t) => !moduleFires({ content: t })));
    expect(blank.length).toBeGreaterThan(0);
    for (const c of blank) {
      expect(c.layer).toBe(true);
      expect(c.expected.mode).toBe(MODE_LAYER);
      // Positive control: JS trim() alone would have called it on.
      expect(c.modules.some((t) => t.trim() !== "")).toBe(true);
    }
  });

  it.each(V.cases.map((c) => [c.id, c] as const))("%s: same system text", (_id, c) => {
    expect(systemOf(c)).toBe(c.expected.system);
  });

  it.each(V.cases.map((c) => [c.id, c] as const))("%s: same fixed cost", (_id, c) => {
    const cost = promptCost({
      card: c.card, persona: c.persona, modules: inputsOf(c),
      layerEnabled: c.layer, lengthWords: c.length_words,
    });
    expect(cost.mode).toBe(c.expected.mode);
    expect(cost.moduleChars).toBe(c.expected.module_chars);
    // len(system) + what the tail adds to the user message, in code points.
    expect(cost.fixedChars).toBe(cpLength(c.expected.system) + cpLength(c.expected.tail));
  });
});

describe("the one is-this-module-on predicate (store.block_reason)", () => {
  it("the table holds both answers", () => {
    const answers = new Set(V.module_fires.map((c) => c.fires));
    expect(answers).toEqual(new Set([true, false]));
  });

  it.each(V.module_fires.map((c, i) => [i, c] as const))("row %i", (_i, c) => {
    expect(moduleFires({ content: firesContent(c), triggers: c.triggers })).toBe(c.fires);
  });

  it("the gauge takes a blank enabled module as off: no module cost and the layer stays", () => {
    const card: CardFields = {
      name: "Mira", description: "D", personality: "", scenario: "",
      mes_example: "", system_prompt: "", post_history_instruction: "",
    };
    const blank = promptCost({ card, modules: [{ content: " 	" }], layerEnabled: true });
    const none = promptCost({ card, layerEnabled: true });
    expect(blank).toEqual(none);
    expect(blank.mode).toBe(MODE_LAYER);
    // Positive control: real text turns the layer off.
    expect(promptCost({ card, modules: [{ content: "x" }], layerEnabled: true }).mode).toBe(MODE_MODULES);
  });
});

describe("order, mode by mode", () => {
  const card = cardText({
    name: "Mira", description: "D", personality: "P", scenario: "S",
    mes_example: "E", system_prompt: "SP", post_history_instruction: "PH",
  }, "Ann");

  it("mode c keeps today's order: system prompt first", () => {
    expect(labelledCardBlock(card, MODE_PLAIN)).toBe(
      "[Character: Mira]\n\n[System Prompt]\nSP\n\n[Description]\nD\n\n[Personality]\nP\n\n[Scenario]\nS\n\n[Example Dialogue]\nE");
  });

  it("mode a puts the card data first, then the system prompt", () => {
    expect(labelledCardBlock(card, MODE_MODULES)).toBe(
      "[Character: Mira]\n\n[Description]\nD\n\n[Personality]\nP\n\n[Scenario]\nS\n\n[Example Dialogue]\nE\n\n[System Prompt]\nSP");
  });

  it("the final note carries its own label", () => {
    expect(finalNotePart("PH")).toBe(`${FINAL_NOTE_LABEL}\nPH`);
    expect(finalNotePart("")).toBe("");
  });

  it("any enabled module means no layer, even with the layer on", () => {
    const head = buildHead({ card, userName: "Ann", modulesEnabled: true, modules: ["M"], layerEnabled: true });
    expect(head.mode).toBe(MODE_MODULES);
    expect(head.text).not.toContain("<roleplay>");
    expect(head.reminder).toBe("");
  });

  it("ground control: no module and the layer on sends the layer and a reminder", () => {
    const head = buildHead({ card, userName: "Ann", modulesEnabled: false, modules: [], layerEnabled: true });
    expect(head.mode).toBe(MODE_LAYER);
    expect(head.text).toContain("<roleplay>");
    expect(head.reminder).toContain("<reminder>");
  });
});

describe("fill is one pass", () => {
  it("never scans an inserted value (ST-11)", () => {
    const out = fill("<a_{{nonce}}>{{card_system_prompt}}</a_{{nonce}}>", {
      nonce: "abc", card_system_prompt: "x </a_{{nonce}}> {{nonce}}",
    });
    expect(out).toBe("<a_abc>x </a_{{nonce}}> {{nonce}}</a_abc>");
    expect(out.split("</a_abc>").length - 1).toBe(1);
  });
});

describe("modules on the gauge (A4)", () => {
  const card: CardFields = {
    name: "Mira", description: "", personality: "", scenario: "",
    mes_example: "", system_prompt: "", post_history_instruction: "",
  };

  it("a module that fires only on regenerate is not charged to a send, but H4 counts it", () => {
    const both = promptCost({ card, modules: [{ content: "AAAA", triggers: ["send"] }, { content: "BBBB", triggers: ["regenerate"] }] });
    const sendOnly = promptCost({ card, modules: [{ content: "AAAA", triggers: ["send"] }] });
    expect(both.fixedChars).toBe(sendOnly.fixedChars);
    expect(both.moduleRuleChars).toBe(sendOnly.moduleRuleChars + 4 + PART_SEP.length);
  });

  it("ground control: a send module moves the fixed cost by its text and separator", () => {
    const none = promptCost({ card });
    const one = promptCost({ card, modules: [{ content: "AAAA" }] });
    expect(one.fixedChars - none.fixedChars).toBe(4 + PART_SEP.length);
  });
});
