import { request } from "./client";
import {
  NotebookEntrySchema,
  NotebookListSchema,
  BoundarySchema,
  BoundaryListSchema,
  NotebookOkSchema,
  UseGlobalSchema,
  ExtractionModelListSchema,
  ExtractSettingsSchema,
  DryRunSchema,
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

export function patchNote(
  id: number,
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
  return request(`/notebook/entries/${id}`, NotebookEntrySchema, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function deleteNote(id: number) {
  return request(`/notebook/entries/${id}`, NotebookOkSchema, {
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

export function deleteBoundary(id: number) {
  return request(`/notebook/boundaries/${id}`, NotebookOkSchema, {
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

export function dryRun(chatId: number) {
  return request(`/notebook/${chatId}/extract/dry-run`, DryRunSchema, {
    method: "POST",
  });
}
