/**
 * Q-121: a model no zero-data-retention provider serves is shown DISABLED with
 * the reason, never hidden and never selectable.
 *
 * The backend counts, per model, the private endpoints that take a chat
 * request (`zdr_endpoints`). 0 is a model the send point refuses with
 * `model_no_private_endpoint`, so letting the reader pick it would only move
 * the refusal to the moment they press Enter. null/absent means the list was
 * unknown and nothing is claimed: such a row must behave exactly as before.
 *
 * Also here: the "never for this model" and "not right now" sentences are
 * different, and only the second one invites a resend.
 */
import { beforeEach, describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import {
  ModelCard,
  NO_PRIVATE_PROVIDER_LABEL,
  NO_PRIVATE_PROVIDER_REASON,
} from "@/components/models/ModelCard";
import { useUiStore } from "@/lib/store/uiStore";
import { getErrorMessage, isKnownErrorCode } from "@/lib/errors/errorMessages";
import { ModelListSchema } from "@/lib/schemas/models";
import { StreamEventSchema } from "@/lib/api/stream";
import { modelFixture } from "@/test/mocks/fixtures";
import type { Model } from "@/lib/schemas/models";

function renderCard(overrides: Partial<Model>) {
  const model = { ...modelFixture, ...overrides } as Model;
  render(<ModelCard model={model} />);
  return screen.getByRole("button", { name: /select model/i });
}

describe("a model with no private provider", () => {
  beforeEach(() => {
    useUiStore.setState({ selectedModelId: null });
  });

  it("is shown, marked, and cannot be picked", () => {
    const row = renderCard({ id: "v/none", name: "None", zdr_endpoints: 0 });
    expect(row.getAttribute("aria-disabled")).toBe("true");
    expect(screen.getByText(NO_PRIVATE_PROVIDER_LABEL)).toBeTruthy();
    // The reason is attached to the row for assistive tech, not just drawn.
    const describedBy = row.getAttribute("aria-describedby");
    expect(describedBy).toBeTruthy();
    expect(document.getElementById(describedBy!)?.textContent).toContain(
      NO_PRIVATE_PROVIDER_REASON,
    );
    fireEvent.click(row);
    expect(useUiStore.getState().selectedModelId).toBeNull();
  });

  it("a model with private providers is picked as before", () => {
    // Positive control: the click path works, so the test above is not
    // green because clicking does nothing in this harness.
    const row = renderCard({ id: "v/ok", name: "Ok", zdr_endpoints: 3 });
    expect(row.getAttribute("aria-disabled")).toBeNull();
    expect(screen.queryByText(NO_PRIVATE_PROVIDER_LABEL)).toBeNull();
    fireEvent.click(row);
    expect(useUiStore.getState().selectedModelId).toBe("v/ok");
  });

  it("an unknown count claims nothing", () => {
    // Ground control: null is "the list was unknown", not zero.
    const row = renderCard({ id: "v/unknown", name: "U", zdr_endpoints: null });
    expect(row.getAttribute("aria-disabled")).toBeNull();
    expect(screen.queryByText(NO_PRIVATE_PROVIDER_LABEL)).toBeNull();
    fireEvent.click(row);
    expect(useUiStore.getState().selectedModelId).toBe("v/unknown");
  });

  it("the count survives the list schema instead of being stripped", () => {
    const parsed = ModelListSchema.parse({
      source: "public",
      cached: false,
      count: 1,
      zdr_known: true,
      models: [{ ...modelFixture, zdr_endpoints: 0 }],
    });
    expect(parsed.models[0].zdr_endpoints).toBe(0);
    expect(parsed.zdr_known).toBe(true);
  });
});

describe("the retry count", () => {
  it("survives the done frame schema", () => {
    const done = StreamEventSchema.parse({
      type: "done",
      chat_id: 1,
      model_id: "v/m",
      user_message: {
        id: 1, chat_id: 1, role: "user", content: "hi",
        created_at: "2026-09-26T00:00:00Z",
      },
      assistant_message: {
        id: 2, chat_id: 1, role: "assistant", content: "ok",
        created_at: "2026-09-26T00:00:00Z",
      },
      retried: 1,
    });
    expect(done.type === "done" && done.retried).toBe(1);
  });
});

describe("never versus not now", () => {
  const NEVER = ["model_no_private_endpoint", "openrouter_no_provider_meets_privacy"];
  const NOT_NOW = ["openrouter_temporarily_unavailable", "openrouter_rate_limited"];

  it("every code has its own sentence", () => {
    for (const code of [...NEVER, ...NOT_NOW, "reasoning_used_reply_budget",
      "stop_not_sent_privately"]) {
      expect(isKnownErrorCode(code), code).toBe(true);
    }
    const sentences = new Set([...NEVER, ...NOT_NOW].map(getErrorMessage));
    expect(sentences.size).toBe(NEVER.length + NOT_NOW.length);
  });

  it("the never kind points at another model, not at Enter", () => {
    for (const code of NEVER) {
      const text = getErrorMessage(code).toLowerCase();
      expect(text, code).toContain("another model");
      expect(text, code).not.toMatch(/please try again|send again\.?$/);
    }
  });

  it("the not-now kind says it passes", () => {
    // Positive control for the test above: these DO invite a resend.
    for (const code of NOT_NOW) {
      expect(getErrorMessage(code).toLowerCase(), code).toMatch(
        /wait a moment/,
      );
    }
  });
});
