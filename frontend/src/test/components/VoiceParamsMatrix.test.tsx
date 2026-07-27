/**
 * V9-5 - the settings panel shows the union, not just what this engine has.
 *
 * Asserted here rather than only on the backend because the failure mode is
 * visual: a control that silently disappears on a model swap teaches that the
 * app is inconsistent, when the difference actually belongs to the engine. The
 * disabled row with its reason is the thing that says so.
 */
import { describe, it, expect } from "vitest";

import { TtsSchemaSchema } from "@/lib/schemas/tts";

const BASE = {
  uid: "m1",
  engine_id: "fish_s2",
  display_name: "Fish",
  variant: null,
  capabilities: {},
  params: [
    { name: "temperature", type: "float", default: 0.7, label: "Expressiveness" },
  ],
};

function parse(extra: Record<string, unknown> = {}) {
  return TtsSchemaSchema.parse({ ...BASE, ...extra });
}

describe("voice settings schema", () => {
  it("accepts a payload with no matrix, so an older backend still renders", () => {
    // Forward compatibility in the other direction: the panel falls back to
    // this engine's own params rather than showing an error.
    expect(parse().matrix).toEqual([]);
  });

  it("carries editability and the reason for each union row", () => {
    const schema = parse({
      matrix: [
        {
          name: "exaggeration",
          type: "float",
          default: 0.5,
          label: "Emotion intensity",
          editable: false,
          status: "unsupported",
          reason: "The selected voice model has no such setting.",
        },
      ],
    });
    expect(schema.matrix[0].editable).toBe(false);
    expect(schema.matrix[0].reason).toContain("no such setting");
  });

  it("keeps `dead` distinct from `unsupported`", () => {
    // They look identical from outside and mean very different things: one
    // engine cannot do it, the other accepts the value and ignores it.
    const schema = parse({
      matrix: [
        {
          name: "repetition_penalty",
          type: "float",
          default: 2,
          label: "Repetition penalty",
          editable: false,
          status: "dead",
          reason: "accepts the value and never applies it",
        },
      ],
    });
    expect(schema.matrix[0].status).toBe("dead");
  });

  it("marks the app-level dial as editable and says who implements it", () => {
    const schema = parse({
      matrix: [
        {
          name: "speed",
          type: "float",
          default: 1,
          label: "Speed",
          editable: true,
          status: "app_level",
          reason: "Applied by Elysium",
          implemented_by: "elysium",
        },
      ],
    });
    expect(schema.matrix[0].editable).toBe(true);
    expect(schema.matrix[0].implemented_by).toBe("elysium");
  });

  it("defaults an unknown row to editable rather than hiding it", () => {
    // Failing towards "usable" - a row the client cannot classify must not
    // silently become a dead control.
    const schema = parse({
      matrix: [
        { name: "future_knob", type: "float", default: 1, label: "Future" },
      ],
    });
    expect(schema.matrix[0].editable).toBe(true);
    expect(schema.matrix[0].status).toBe("supported");
  });
});
