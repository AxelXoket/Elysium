import { request } from "./client";
import {
  SweepSchema,
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

export function getWorkerStatus() {
  return request("/notebook/worker", WorkerStatusSchema);
}

export function resetWorker() {
  return request("/notebook/worker/reset", NotebookOkSchema, {
    method: "POST",
  });
}

/** Ask the worker to read this chat's unread history.
 *
 *  One work unit per press, claimed against the same daily cap as an
 *  ordinary turn. The chat's older history is otherwise unreachable: the
 *  worker's cursor is a maximum and a first read of a long chat starts at
 *  the present on purpose. */
export function sweepChat(chatId: number) {
  return request(`/notebook/sweep/${chatId}`, SweepSchema, { method: "POST" });
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
