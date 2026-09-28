import { request } from "./client";
import {
  BackfillPlanSchema,
  BackfillRunSchema,
  BackfillStopSchema,
  BackfillUndoSchema,
  NotebookEntrySchema,
  WorkerStatusSchema,
  AutoAcceptSchema,
  SafewordSchema,
  NotebookListSchema,
  BoundarySchema,
  BoundaryListSchema,
  NotebookOkSchema,
  UseGlobalSchema,
  ExtractionModelListSchema,
  ExtractSettingsSchema,
} from "../schemas/notebook";
import type {
  NotebookEntry,
  Boundary,
} from "../schemas/notebook";
import type { z } from "zod/v4";

export type NotebookList = z.infer<typeof NotebookListSchema>;

export function listNotebook(chatId: number): Promise<NotebookList> {
  return request(`/notebook/${chatId}`, NotebookListSchema);
}

export function createNote(
  chatId: number,
  payload: {
    text: string;
    kind?: string;
    durability?: string;
    importance?: number;
    pinned?: boolean;
  },
): Promise<NotebookEntry> {
  return request(`/notebook/${chatId}`, NotebookEntrySchema, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

/** Every note route names the chat it is acting from.
 *
 *  The backend used to accept the primary key as the whole identity, so a
 *  call made from one chat could edit, delete or read back a note in another
 *  one - and patch and accept both hand the row's text back, so it was a
 *  read as much as a write. `chat_id` is required there now; it is required
 *  here for the same reason, rather than optional and forgotten.
 */
export function patchNote(
  id: number,
  chatId: number,
  payload: {
    text?: string;
    kind?: string;
    durability?: string;
    importance?: number;
    pinned?: boolean;
  },
): Promise<NotebookEntry> {
  // `provenance` is absent from the payload type on purpose. The backend
  // refuses it loudly; not offering it here keeps the idea out of the UI too.
  return request(
    `/notebook/entries/${id}?chat_id=${chatId}`, NotebookEntrySchema, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

/** Promote one proposal. Deliberately NOT `patchNote(id, {status})`: the
 *  patch body has no `status` field and must not grow one, because the same
 *  door would then be open to `provenance` - and a proposal that can rewrite
 *  who wrote it is the classic promotion bypass. */
export function acceptNote(id: number, chatId: number) {
  return request(
    `/notebook/entries/${id}/accept?chat_id=${chatId}`, NotebookEntrySchema, {
    method: "POST",
  });
}

export function deleteNote(id: number, chatId: number) {
  return request(
    `/notebook/entries/${id}?chat_id=${chatId}`, NotebookOkSchema, {
    method: "DELETE",
  });
}

export function reorderNotes(chatId: number, orderedIds: number[]) {
  return request(`/notebook/${chatId}/reorder`, NotebookOkSchema, {
    method: "POST",
    body: JSON.stringify({ ordered_ids: orderedIds }),
  });
}

export function listGlobalBoundaries() {
  return request("/notebook/boundaries", BoundaryListSchema);
}

export function listChatBoundaries(chatId: number) {
  return request(`/notebook/${chatId}/boundaries`, BoundaryListSchema);
}

export function createBoundary(payload: {
  label: string;
  phrasing: string;
  severity: string;
  chat_id?: number | null;
  polarity?: string;
  on_violation?: string;
  rating_ceiling?: string | null;
}): Promise<Boundary> {
  return request("/notebook/boundaries", BoundarySchema, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

/** `chatId` is optional here and required on the note routes, and the
 *  difference is the data: a GLOBAL limit belongs to no chat, so demanding
 *  one would mean inventing a scope for a row that has none. */
export function deleteBoundary(id: number, chatId?: number | null) {
  const scope = chatId == null ? "" : `?chat_id=${chatId}`;
  return request(`/notebook/boundaries/${id}${scope}`, NotebookOkSchema, {
    method: "DELETE",
  });
}

export function setUseGlobalBoundaries(chatId: number, useGlobal: boolean) {
  return request(`/notebook/${chatId}/use-global`, UseGlobalSchema, {
    method: "POST",
    body: JSON.stringify({ use_global: useGlobal }),
  });
}

export function listExtractionModels() {
  return request("/notebook/extract/models", ExtractionModelListSchema);
}

export function getExtractSettings() {
  return request("/notebook/extract/settings", ExtractSettingsSchema);
}

export function saveExtractSettings(payload: {
  model_id?: string | null;
  prompt_language?: string;
}) {
  return request("/notebook/extract/settings", NotebookOkSchema, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

// ── FAZ 5: the background extractor ────────────────────────────────────────

/** `chatId` is ADDITIVE: it fills the `worker.chat` block for the chat on
 *  screen and changes nothing else about the answer, so a panel with no chat
 *  selected asks exactly the question it always did. */
export function getWorkerStatus(chatId?: number | null) {
  const scope = chatId == null ? "" : `?chat_id=${chatId}`;
  return request(`/notebook/worker${scope}`, WorkerStatusSchema);
}

/** With no `chatId` this is the panel's global button - it lifts the
 *  process-wide refusal AND every per-chat one, because "try everything
 *  again" is what pressing it means. With one, it unsticks exactly that
 *  conversation and leaves the others refusing. */
export function resetWorker(chatId?: number | null) {
  return request("/notebook/worker/reset", NotebookOkSchema, {
    method: "POST",
    body: JSON.stringify(chatId == null ? {} : { chat_id: chatId }),
  });
}

// ── Backfill jobs: reading a chat whole, or a chosen stretch, on request ──
//
// Four routes, one job at a time. `plan` spends nothing and is what the
// strip shows BEFORE the button acts; `run` starts the job and answers at
// once (progress rides on `getWorkerStatus`, whose `worker.backfill` block
// is this job); `stop` lets the unit in flight finish and starts no more;
// `undo` removes exactly what one job wrote. A refusal (`started: false`,
// `stopped: false`) is a 200 with a reason, the same shape the old sweep
// button answered with, and never an error.

export type BackfillScope = "next" | "all" | "range";

export type BackfillRange = { from_message_id: number; to_message_id: number };

function rangeQuery(scope: BackfillScope, range?: BackfillRange | null) {
  const q = new URLSearchParams({ scope });
  if (scope === "range" && range) {
    q.set("from_message_id", String(range.from_message_id));
    q.set("to_message_id", String(range.to_message_id));
  }
  return q.toString();
}

export function getBackfillPlan(
  chatId: number, scope: BackfillScope, range?: BackfillRange | null,
) {
  return request(
    `/notebook/extract/plan/${chatId}?${rangeQuery(scope, range)}`,
    BackfillPlanSchema,
  );
}

export function runBackfill(
  chatId: number, scope: BackfillScope, range?: BackfillRange | null,
) {
  const body: Record<string, unknown> = { scope };
  if (scope === "range" && range) {
    body.from_message_id = range.from_message_id;
    body.to_message_id = range.to_message_id;
  }
  return request(`/notebook/extract/run/${chatId}`, BackfillRunSchema, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function stopBackfill(chatId: number) {
  return request(`/notebook/extract/stop/${chatId}`, BackfillStopSchema, {
    method: "POST",
  });
}

/** `chatId` is required on the wire: the backend answers 404 for a job that
 *  is not this chat's, the same 404 as for one that does not exist. */
export function undoBackfill(jobId: number, chatId: number) {
  return request(
    `/notebook/extract/undo/${jobId}?chat_id=${chatId}`, BackfillUndoSchema, {
    method: "POST",
  });
}

export function getAutoAccept(chatId?: number | null) {
  const scope = chatId == null ? "" : `?chat_id=${chatId}`;
  return request(`/notebook/auto-accept${scope}`, AutoAcceptSchema);
}

/** Decide for ONE chat, or hand the decision back to the global switch with
 *  `null`. The column behind this had no writer but the chat INSERT, so a
 *  chat that was wrongly trusted stayed trusted for its whole life. */
export function setChatAutoAccept(chatId: number, enabled: boolean | null) {
  return request(`/notebook/${chatId}/auto-accept`, NotebookOkSchema, {
    method: "POST",
    body: JSON.stringify({ enabled }),
  });
}

export function setAutoAccept(enabled: boolean) {
  return request("/notebook/auto-accept", NotebookOkSchema, {
    method: "POST",
    body: JSON.stringify({ enabled }),
  });
}


// ── The safeword ───────────────────────────────────────────────────────────

export function getSafeword() {
  return request("/notebook/safeword", SafewordSchema);
}

export function setSafeword(word: string) {
  return request("/notebook/safeword", NotebookOkSchema, {
    method: "POST",
    body: JSON.stringify({ word }),
  });
}
