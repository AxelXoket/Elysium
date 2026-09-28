/**
 * Plain lines on the Notes tab, each held by behaviour with the words
 * imported:
 *   - the limits line sits with the limits, not under the safeword;
 *   - a model that cannot write notes: the line is action first, two
 *      sentences, the rest behind "Why this model cannot";
 *   - the summary's failure count is the census's own denominator;
 *   - the Note suggestions intro does not read as "off" while a model is on.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { hiddenFromReaders } from "@/test/helpers/accessibility";
import { mockFetch } from "../mocks/api";
import { BoundaryPanel } from "@/components/notebook/BoundaryPanel";
import { WorkerPanel, CHAT_REFUSAL_AFTER } from "@/components/notebook/WorkerPanel";
import { ExtractionSettings } from "@/components/notebook/ExtractionSettings";
import { DISCLOSURE_WORDS, LIMITS_LINE } from "@/components/notebook/limitsProse";
import { REASON, REASON_TAIL, REASON_WHY } from "@/components/notebook/reasonProse";
import { EXTRACTION_WORDS } from "@/components/notebook/noteProse";
import { actFailureProse, troubleProse } from "@/components/notebook/workerProse";
import { getErrorMessage } from "@/lib/errors/errorMessages";
import { useUiStore } from "@/lib/store/uiStore";

function worker(over: { stats?: object; worker?: object; chat_refusal?: string | null } = {}) {
  return {
    stats: { done: 3, failed: 0, skipped: 0, skip_reasons: {}, ...(over.stats ?? {}) },
    spend: { calls: 3, tokens_in: 900, tokens_out: 40, cost: 0.0004 },
    spend_lifetime: { calls: 3, tokens_in: 900, tokens_out: 40, cost: 0.0004 },
    worker: { state: "closed", failures: 0, total_failures: 0, queued: 0, dropped_offers: 0,
      runs: 3, batch_size: 20, backfill: null, ...(over.worker ?? {}) },
    daily_cap: 60,
    chat_refusal: over.chat_refusal ?? null,
  };
}
function mountWorker(body: ReturnType<typeof worker>) {
  mockFetch({
    "/notebook/worker": { body },
    "/notebook/auto-accept": { body: { enabled: true, effective: true, overridden: false } },
  });
  return renderWithQueryClient(<WorkerPanel />);
}
const sentences = (t: string) => t.split(/(?<=[.!?])\s+/).filter((s) => /\w/.test(s));

describe("plain lines on the Notes tab", () => {
  beforeEach(() => { vi.restoreAllMocks(); useUiStore.setState({ selectedChatId: 7 }); });
  afterEach(() => { cleanup(); vi.restoreAllMocks(); });

  it("the limits line says whose it is and sits with the limits, apart from the safeword", async () => {
    mockFetch({
      "/notebook/safeword": { body: { word: "" } },
      "/notebook/7/boundaries": { body: { boundaries: [], use_global: true } },
      "/notebook/boundaries": { body: { boundaries: [], use_global: true } },
    });
    renderWithQueryClient(<BoundaryPanel />);
    const line = await screen.findByTestId("limits-line");
    expect(line.textContent).toBe(LIMITS_LINE);
    expect(LIMITS_LINE).toMatch(/^Your limits/);
    const box = await screen.findByLabelText(/^safeword$/i);
    // Not in the safeword's card, and after the new-limit row.
    expect(box.closest(".persona-card")!.contains(line)).toBe(false);
    const newLimit = screen.getByRole("textbox", { name: "New limit" });
    expect(newLimit.compareDocumentPosition(line) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("the card's refusal is action first, two sentences and a tail, the rest behind a door", async () => {
    const user = userEvent.setup();
    mountWorker(worker({ chat_refusal: "model_not_extractable" }));
    const line = await screen.findByTestId("worker-chat-refusal");
    await waitFor(() => expect(line.textContent).toContain(REASON.modelNotExtractable));
    expect(line.textContent!.startsWith(REASON.modelNotExtractable)).toBe(true);
    expect(sentences(REASON.modelNotExtractable)).toHaveLength(2);
    expect(sentences(REASON.modelNotExtractable)[0]).toMatch(/^Pick a note model/);
    expect(line.textContent).toContain(REASON_TAIL.notSpent);
    const door = within(line.parentElement!).getByRole("button", { name: DISCLOSURE_WORDS.whyModel });
    const why = within(line.parentElement!).getByText(REASON_WHY.modelNotExtractable.join(" ") + " " + CHAT_REFUSAL_AFTER);
    expect(hiddenFromReaders(why)).toBe(true);
    await user.click(door);
    expect(hiddenFromReaders(why)).toBe(false);
    // "After ..." names what it follows, the pick, inside the door itself.
    expect(CHAT_REFUSAL_AFTER).toMatch(/^After you pick one/);
    // The door keeps the tab's standard gap under the line above it.
    expect(screen.getByTestId("worker-refusal-why")).toHaveClass("mt-1");
  });

  it("the summary counts the census's failures; an on-screen failure has no second total", async () => {
    mountWorker(worker({
      stats: { failed: 5, paid_and_lost: 5, error_types: { "OpenRouterError:api_key_not_set": 2, truncated: 3 } },
      worker: { unhandled: 1 },
    }));
    const summary = await screen.findByTestId("worker-trouble");
    expect(summary.textContent).toBe(troubleProse({ failed: 5, unexpected: 1, skipped: 0, dropped: 0, empty: 0 }));
    expect(summary.textContent).toMatch(/^5 runs failed\. 1 unexpected error\./);
    const act = screen.getByTestId("worker-failure-act");
    expect(act.textContent).toBe(`${actFailureProse(2)} ${getErrorMessage("api_key_not_set")}`);
    expect(act.textContent).not.toMatch(/of \d+ failures/);
  });

  it("every summary sentence is short, and a write failure is counted once", async () => {
    const all = troubleProse({ failed: 55, unexpected: 11, skipped: 100, dropped: 44, empty: 22 })!;
    for (const s of sentences(all)) expect(s.split(/\s+/).length, s).toBeLessThanOrEqual(12);
    mountWorker(worker({
      stats: { failed: 1, paid_and_lost: 1, error_types: { write_RuntimeError: 1 } },
      worker: { unhandled: 1 },
    }));
    const summary = await screen.findByTestId("worker-trouble");
    expect(summary.textContent).toBe(troubleProse({ failed: 1, unexpected: 0, skipped: 0, dropped: 0, empty: 0 }));
    expect(summary.textContent).not.toMatch(/unexpected/);
    cleanup();
    // POSITIVE CONTROL: an error that is not a write is still named.
    mountWorker(worker({ stats: { failed: 0 }, worker: { unhandled: 1 } }));
    expect((await screen.findByTestId("worker-trouble")).textContent).toMatch(/1 unexpected error/);
  });

  it("the intro does not say 'off' over a picked model", async () => {
    mockFetch({
      "/notebook/extract/models": { body: { models: [{ id: "cheap/one", provider: "P", prompt_price: 0.06, context_length: 1, endpoints: 3 }] } },
      "/notebook/extract/settings": { body: { model_id: "cheap/one", prompt_language: "en" } },
    });
    renderWithQueryClient(<ExtractionSettings />);
    const intro = await screen.findByText(EXTRACTION_WORDS.intro);
    expect(hiddenFromReaders(intro)).toBe(false);
    // Its "off" is conditional, never a flat claim.
    expect(sentences(EXTRACTION_WORDS.intro).some((s) => /^off\b/i.test(s))).toBe(false);
    expect(EXTRACTION_WORDS.intro).toMatch(/no model picked/i);
  });
});
