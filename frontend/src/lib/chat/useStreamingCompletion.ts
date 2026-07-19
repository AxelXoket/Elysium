/**
 * useStreamingCompletion - per-chat SSE send/regenerate state machine.
 *
 * Owns: Map<chatId, StreamingEntry> (accumulating text for the streaming
 * bubble) and one AbortController per chat. Payloads are built with the SAME
 * builders as the non-streaming mutations (buildCompletionPayload /
 * buildRegeneratePayload) so param filtering/clamping stays identical.
 *
 * Backend persistence semantics this hook mirrors:
 *  - send: user row persisted BEFORE streaming. Provider error → backend
 *    deletes it (we remove it from cache too). Abort with partial → backend
 *    persists the partial assistant message (we refetch). Abort with no
 *    partial → backend deletes the user row (silent cleanup, draft restored
 *    by the caller).
 *  - regenerate: old assistant row untouched until the atomic swap at done.
 *    Error or abort → old row intact, partial discarded (no cache change).
 *
 * Deltas accumulate in LOCAL state only - never in the query cache - to
 * avoid cache churn per token. State flushes are additionally batched per
 * animation frame: one render per frame, not per delta (terminal events
 * flush synchronously, so no text is ever lost).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { keys } from "../query/keys";
import { nextOptimisticId } from "../query/completions";
import {
  streamChatCompletion,
  streamRegenerateMessage,
  isAbortError,
} from "../api/stream";
import type { StreamEvent } from "../api/stream";
import { buildCompletionPayload, buildRegeneratePayload } from "../generation";
import { getErrorMessage, useErrorStore } from "../errors";
import type { ApiError } from "../api/client";
import type { GenerationParams } from "../schemas/completions";
import type { Model } from "../schemas/models";
import type { Message } from "../schemas/chats";

export interface StreamingEntry {
  kind: "send" | "regenerate";
  /** Set for kind="regenerate": the assistant row regenerate was pressed on. */
  targetMessageId?: number;
  /** Set for kind="regenerate": the variant-group anchor of that row. The
   * UI routes streaming text by ANCHOR (bubbles are group-keyed) - matching
   * by row id would break at the done swap when the active row id changes. */
  targetAnchor?: number;
  /** Accumulated streamed text (empty until the first delta). */
  text: string;
}

type ModelInfo = Pick<
  Model,
  "supported_parameters" | "max_completion_tokens" | "context_length"
> | null;

export interface StreamSendVars {
  chatId: number;
  message: string;
  modelId: string;
  generationParams?: GenerationParams;
  personaId?: number | null;
  contextBudgetTokens?: number | null;
  model?: ModelInfo;
  /** Ready upload ids (POST /uploads/images), max 4. Omitted → no images. */
  attachments?: readonly number[];
}

export interface StreamRegenerateVars {
  chatId: number;
  messageId: number;
  /** Variant-group anchor of the target row (messageAnchor(target)). */
  anchor: number;
  modelId: string;
  generationParams?: GenerationParams;
  personaId?: number | null;
  contextBudgetTokens?: number | null;
  model?: ModelInfo;
}

export interface StreamSendCallbacks {
  /** Send failed (HTTP, network, or in-stream error event). */
  onError?: (err: unknown) => void;
  /** User aborted before any text streamed - message was not sent. */
  onAbortedEmpty?: () => void;
  /** The user row (with its attachments) is persisted and visible in the
   * chat - fired at the user_message event, i.e. the START of streaming.
   * Callers clear staged attachment thumbnails here: from this moment the
   * image lives in the sent bubble, and keeping the staged copy through a
   * long stream reads as "my image is still waiting to be sent". On a later
   * error/abort-empty the backend unlinks the images back to staged and the
   * caller restores placeholder entries (same ids, no preview bitmap) so a
   * retry still carries them. */
  onUserMessagePersisted?: () => void;
  /** The exchange fully persisted server-side: the stream finished (done)
   * or was stopped after text streamed. Any attached upload ids are consumed
   * by that row - callers clear their staged copies here (idempotent with
   * onUserMessagePersisted; kept as a safety net). */
  onPersisted?: () => void;
}

function makeApiError(status: number, detail: string): ApiError {
  return { status, detail, message: getErrorMessage(detail) };
}

/**
 * Batches per-delta state flushes into one animation frame.
 *
 * Fast providers can deliver many deltas per frame; rendering each one is
 * wasted work. Deltas accumulate synchronously in the caller's local text
 * variable - only the setState flush is deferred, so terminal logic always
 * sees the full text regardless of frame timing.
 */
function createFrameFlusher(apply: () => void) {
  let handle: number | null = null;
  return {
    /** Queue a flush on the next frame (no-op when one is already queued). */
    schedule() {
      if (handle == null) {
        handle = requestAnimationFrame(() => {
          handle = null;
          apply();
        });
      }
    },
    /** Terminal events: cancel the pending frame and apply synchronously. */
    flushNow() {
      if (handle != null) {
        cancelAnimationFrame(handle);
        handle = null;
        apply();
      }
    },
    /** Cancel without applying - nothing may fire after clearEntry. */
    cancel() {
      if (handle != null) {
        cancelAnimationFrame(handle);
        handle = null;
      }
    },
  };
}

export function useStreamingCompletion() {
  const qc = useQueryClient();
  const pushError = useErrorStore((s) => s.pushError);

  const [streamingByChat, setStreamingByChat] = useState<
    ReadonlyMap<number, StreamingEntry>
  >(() => new Map());
  const controllersRef = useRef<Map<number, AbortController>>(new Map());
  // Active frame flushers, tracked so unmount can cancel any queued rAF before
  // it fires setState on a torn-down component.
  const flushersRef = useRef<Set<ReturnType<typeof createFrameFlusher>>>(
    new Set(),
  );

  // Unmount: abort every in-flight stream and cancel any pending frame. Without
  // this an SSE request keeps running (and its rAF keeps firing) after the hook
  // host is gone.
  useEffect(() => {
    const controllers = controllersRef.current;
    const flushers = flushersRef.current;
    return () => {
      for (const controller of controllers.values()) controller.abort();
      controllers.clear();
      for (const flusher of flushers) flusher.cancel();
      flushers.clear();
    };
  }, []);

  const setEntry = useCallback((chatId: number, entry: StreamingEntry) => {
    setStreamingByChat((prev) => {
      const next = new Map(prev);
      next.set(chatId, entry);
      return next;
    });
  }, []);

  const clearEntry = useCallback((chatId: number) => {
    setStreamingByChat((prev) => {
      if (!prev.has(chatId)) return prev;
      const next = new Map(prev);
      next.delete(chatId);
      return next;
    });
  }, []);

  /** Abort the active stream for a chat (no-op when nothing is streaming). */
  const stop = useCallback((chatId: number) => {
    controllersRef.current.get(chatId)?.abort();
  }, []);

  const startSend = useCallback(
    async (vars: StreamSendVars, callbacks?: StreamSendCallbacks) => {
      const { chatId } = vars;
      // One active stream per chat - the UI enforces this; guard defensively.
      if (controllersRef.current.has(chatId)) return;

      const controller = new AbortController();
      controllersRef.current.set(chatId, controller);
      setEntry(chatId, { kind: "send", text: "" });

      // History-at-risk marker: if this chat's messages were NEVER loaded
      // (first GET still in flight - most likely right after app boot with a
      // persisted selection), the cancel below kills that GET and the cache
      // gets seeded with only this exchange. The done handler resyncs then.
      const historyWasUnloaded =
        qc.getQueryData<Message[]>(keys.messages(chatId)) === undefined;

      // Cancel in-flight refetches so they don't clobber our cache writes
      await qc.cancelQueries({ queryKey: keys.messages(chatId) });

      // Optimistic user message (same negative-id space as the mutations).
      // No attachments on the optimistic row - the user_message event swaps
      // in the persisted row carrying the attachment metadata.
      const optimisticId = nextOptimisticId();
      const optimisticMessage: Message = {
        id: optimisticId,
        chat_id: chatId,
        role: "user",
        content: vars.message,
        created_at: new Date().toISOString(),
      };
      qc.setQueryData<Message[]>(keys.messages(chatId), (prev) => [
        ...(prev ?? []),
        optimisticMessage,
      ]);

      let realUserMessageId: number | null = null;
      let streamedText = "";
      let sawDone = false;
      let errorEvent: { status: number; code: string } | null = null;
      // rAF batching: deltas accumulate in streamedText; one frame per batch
      // flushes them into the streaming entry (see createFrameFlusher).
      const flusher = createFrameFlusher(() => {
        setEntry(chatId, { kind: "send", text: streamedText });
      });
      flushersRef.current.add(flusher);

      const handleEvent = (event: StreamEvent) => {
        switch (event.type) {
          case "user_message":
            // Backend persisted the user row - swap the optimistic one out
            realUserMessageId = event.message.id;
            qc.setQueryData<Message[]>(keys.messages(chatId), (prev) => {
              const existing = prev ?? [];
              const without = existing.filter(
                (m) => m.id !== optimisticId && m.id !== event.message.id,
              );
              return [...without, event.message];
            });
            callbacks?.onUserMessagePersisted?.();
            break;
          case "delta":
            streamedText += event.content;
            flusher.schedule();
            break;
          case "done":
            sawDone = true;
            flusher.flushNow();
            // A refetch dispatched mid-stream would resolve with PRE-append
            // server state and clobber the rows we are about to write - kill
            // it before touching the cache.
            void qc.cancelQueries({ queryKey: keys.messages(chatId) });
            qc.setQueryData<Message[]>(keys.messages(chatId), (prev) => {
              const existing = prev ?? [];
              const without = existing.filter((m) => m.id !== optimisticId);
              const ids = new Set(without.map((m) => m.id));
              const toAdd = [event.user_message, event.assistant_message].filter(
                (m) => !ids.has(m.id),
              );
              return [...without, ...toAdd];
            });
            qc.invalidateQueries({ queryKey: keys.chats() });
            // Resync net: if this send raced the chat's FIRST messages GET
            // (cancelled at send start), the cache now holds only this
            // exchange and the prior history would stay invisible. Refetch -
            // the backend has persisted the rows by `done`, so the response
            // includes them plus the missing history. Skipped on the normal
            // path (history present) to avoid a pointless GET per send.
            if (historyWasUnloaded) {
              void qc.invalidateQueries({ queryKey: keys.messages(chatId) });
            }
            // Same-batch teardown: the transient streaming bubble must vanish
            // in the SAME commit the cached rows land, or React paints an
            // intermediate frame showing the reply twice. The finally-block
            // clear stays as the idempotent safety net.
            clearEntry(chatId);
            break;
          case "error":
            errorEvent = { status: event.status, code: event.code };
            flusher.flushNow();
            break;
        }
      };

      /** Remove the optimistic row and (if seen) the persisted user row. */
      const removeUserRows = () => {
        qc.setQueryData<Message[]>(keys.messages(chatId), (prev) =>
          prev
            ? prev.filter(
                (m) =>
                  m.id !== optimisticId &&
                  (realUserMessageId == null || m.id !== realUserMessageId),
              )
            : prev,
        );
      };

      const failSend = (err: unknown) => {
        // Backend deleted the just-persisted user row on failure - mirror it,
        // then resync from the server.
        removeUserRows();
        qc.invalidateQueries({ queryKey: keys.messages(chatId) });
        callbacks?.onError?.(err);
      };

      // Attachments ride alongside the payload builder's output - the builder
      // lives in lib/generation and stays attachment-agnostic. The key is
      // omitted entirely when the message has no images.
      const payload = buildCompletionPayload({
        message: vars.message,
        modelId: vars.modelId,
        generationParams: vars.generationParams,
        personaId: vars.personaId,
        contextBudgetTokens: vars.contextBudgetTokens,
        model: vars.model,
      });
      if (vars.attachments != null && vars.attachments.length > 0) {
        payload.attachments = [...vars.attachments];
      }

      try {
        await streamChatCompletion(chatId, payload, {
          signal: controller.signal,
          onEvent: handleEvent,
        });

        // Stream finished (with or without a terminal event) - apply any
        // still-queued deltas before the terminal branches below.
        flusher.flushNow();

        if (errorEvent != null) {
          const evt = errorEvent as { status: number; code: string };
          failSend(makeApiError(evt.status, evt.code));
        } else if (!sawDone) {
          // Stream ended without a terminal event - malformed response
          failSend(makeApiError(0, "invalid_response_shape"));
        } else {
          callbacks?.onPersisted?.();
        }
      } catch (err) {
        // Abort/failure mid-batch: flush first so the terminal logic (and any
        // UI between here and clearEntry) sees the full accumulated text.
        flusher.flushNow();
        if (isAbortError(err) || controller.signal.aborted) {
          if (streamedText.length > 0) {
            // Backend persisted the partial as the assistant message - the
            // refetch swaps it in (user row stays, attachments consumed).
            qc.invalidateQueries({ queryKey: keys.messages(chatId) });
            callbacks?.onPersisted?.();
          } else {
            // Nothing streamed: backend deleted the user row. Same cleanup
            // as error, but user-initiated - no error surface.
            removeUserRows();
            qc.invalidateQueries({ queryKey: keys.messages(chatId) });
            callbacks?.onAbortedEmpty?.();
          }
        } else {
          failSend(err);
        }
      } finally {
        // Guard: no queued frame may fire after clearEntry, or it would
        // resurrect a ghost streaming entry.
        flusher.cancel();
        flushersRef.current.delete(flusher);
        controllersRef.current.delete(chatId);
        clearEntry(chatId);
      }
    },
    [qc, setEntry, clearEntry],
  );

  const startRegenerate = useCallback(
    async (vars: StreamRegenerateVars) => {
      const { chatId, messageId } = vars;
      if (controllersRef.current.has(chatId)) return;

      const controller = new AbortController();
      controllersRef.current.set(chatId, controller);
      // NO optimistic cache change - the old assistant variant stays visible
      // and the target bubble renders the accumulating text.
      setEntry(chatId, {
        kind: "regenerate",
        targetMessageId: messageId,
        targetAnchor: vars.anchor,
        text: "",
      });

      await qc.cancelQueries({ queryKey: keys.messages(chatId) });

      let streamedText = "";
      let sawDone = false;
      let errorEvent: { status: number; code: string } | null = null;
      // rAF batching - same scheme as startSend.
      const flusher = createFrameFlusher(() => {
        setEntry(chatId, {
          kind: "regenerate",
          targetMessageId: messageId,
          targetAnchor: vars.anchor,
          text: streamedText,
        });
      });
      flushersRef.current.add(flusher);

      const handleEvent = (event: StreamEvent) => {
        switch (event.type) {
          case "user_message":
            // Existing preceding user row - already in the cache; ignore.
            break;
          case "delta":
            streamedText += event.content;
            flusher.schedule();
            break;
          case "done": {
            sawDone = true;
            flusher.flushNow();
            // Kill any mid-stream refetch: it would resolve with PRE-append
            // server state and erase the variant we are about to write.
            void qc.cancelQueries({ queryKey: keys.messages(chatId) });
            // Variant append: deactivate the previous sibling IN PLACE and
            // dedupe-append the new active row - nothing is removed, old
            // variants stay navigable.
            const deactivatedId = event.deactivated_message_id ?? null;
            const anchor =
              event.assistant_message.variant_group ??
              event.assistant_message.id;
            qc.setQueryData<Message[]>(keys.messages(chatId), (prev) => {
              const existing = prev ?? [];
              const next = existing.map((m) =>
                deactivatedId != null && m.id === deactivatedId
                  ? { ...m, active: false, variant_group: m.variant_group ?? anchor }
                  : m,
              );
              const ids = new Set(next.map((m) => m.id));
              if (!ids.has(event.user_message.id)) next.push(event.user_message);
              if (!ids.has(event.assistant_message.id)) {
                next.push(event.assistant_message);
              }
              return next.sort((a, b) => a.id - b.id);
            });
            qc.invalidateQueries({ queryKey: keys.chats() });
            // Same-batch teardown: cache append and streaming-entry clear
            // must land in ONE commit. Split across commits, the bubble
            // renders an intermediate frame where the group already grew
            // while isStreamingTarget is still true - the pane re-slides and
            // the counter flashes (n+2)/(n+2). finally stays as safety net.
            clearEntry(chatId);
            break;
          }
          case "error":
            errorEvent = { status: event.status, code: event.code };
            flusher.flushNow();
            break;
        }
      };

      try {
        await streamRegenerateMessage(
          chatId,
          messageId,
          buildRegeneratePayload({
            modelId: vars.modelId,
            generationParams: vars.generationParams,
            personaId: vars.personaId,
            contextBudgetTokens: vars.contextBudgetTokens,
            model: vars.model,
          }),
          { signal: controller.signal, onEvent: handleEvent },
        );

        // Stream finished - apply any still-queued deltas before the terminal
        // branches below.
        flusher.flushNow();

        // Regenerate errors surface as a toast (single surface for regenerate).
        // Old assistant row is intact server-side - no cache change needed.
        if (errorEvent != null) {
          const evt = errorEvent as { status: number; code: string };
          pushError(makeApiError(evt.status, evt.code));
        } else if (!sawDone) {
          pushError(makeApiError(0, "invalid_response_shape"));
        }
      } catch (err) {
        flusher.flushNow();
        if (isAbortError(err) || controller.signal.aborted) {
          // User-initiated stop: old message intact, partial discarded - silent.
        } else {
          pushError(err);
        }
      } finally {
        // Guard: no queued frame may fire after clearEntry (ghost entry).
        flusher.cancel();
        flushersRef.current.delete(flusher);
        controllersRef.current.delete(chatId);
        clearEntry(chatId);
      }
    },
    [qc, pushError, setEntry, clearEntry],
  );

  return { streamingByChat, startSend, startRegenerate, stop };
}
