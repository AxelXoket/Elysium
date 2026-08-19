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

import { useContextNotesStore } from "./contextNotes";
import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { keys } from "../query/keys";
import { nextOptimisticId } from "../query/completions";
import {
  streamChatCompletion,
  streamRegenerateMessage,
  streamEditMessage,
  isAbortError,
} from "../api/stream";
import { deleteMessageAndFollowing } from "../api/chats";
import {
  noteFirstDelta,
  registerStream,
  unregisterStream,
} from "./streamRegistry";
import type { StreamEvent } from "../api/stream";
import { createStreamVoice } from "../voice/streamVoice";
import { useTagPrefs } from "../query/tts";
import type { NarrationMode, TtsTagPrefs } from "../api/tts";
import { migrateDeviceNarration } from "../voice/narrationMigration";
import { useUiStore } from "../store/uiStore";
import {
  buildCompletionPayload,
  buildRegeneratePayload,
  buildEditPayload,
} from "../generation";
import { getCountMessage, getErrorMessage, useErrorStore } from "../errors";
import type { ApiError } from "../api/client";
import type { GenerationParams } from "../schemas/completions";
import type { Model } from "../schemas/models";
import type { Message } from "../schemas/chats";

/** The terminal `error` event, kept until the stream body closes. */
interface StreamErrorEvent {
  status: number;
  code: string;
  /**
   * The server kept the text that had already arrived (KÖK 16). It still
   * failed - the toast is unchanged - but the rows are committed, so rolling
   * the optimistic ones back would delete a reply the user is looking at.
   */
  partialSaved: boolean;
}

/**
 * Something the request had to disclose, arriving before the first delta.
 *
 * A warning, not an error: the reply is on its way. It is the answer that is
 * different from the one the user asked for - a model that never received the
 * picture will write as though there were no picture, and only this says so.
 */
function reportStreamNotice(event: { code: string; count?: number }): void {
  // K-36: the singular/plural pair used to be typed here, which left the
  // catalogue holding a sentence for `images_omitted` that no reader ever saw.
  // Both forms now live beside every other sentence, and getCountMessage falls
  // back to the plain one for a notice that carries no number - so the sentence
  // reaches pushErrorDirect straight from the helper rather than through a local
  // that the S-24 scan cannot follow.
  useErrorStore
    .getState()
    .pushErrorDirect(
      event.code,
      getCountMessage(event.code, event.count),
      "warning",
    );
}


/** What the turn actually carried, taken from the frame rather than guessed.
 *
 *  Called from every `done` handler - append, regenerate and edit - because
 *  a number that is correct on two paths out of three is worse than none:
 *  the panel would go stale exactly on the path the user just took. */
function recordContextNotes(
  chatId: number,
  event: { notebook_sent?: number; notebook_total?: number;
           history_trimmed?: number },
) {
  useContextNotesStore.getState().record(chatId, event);
}

export interface StreamingEntry {
  kind: "send" | "regenerate" | "edit";
  /** Set for kind="regenerate": the assistant row regenerate was pressed on.
   * Set for kind="edit": the user row being edited. */
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

export interface StreamEditVars {
  chatId: number;
  /** The USER row being edited. */
  messageId: number;
  /** Replacement text. */
  message: string;
  modelId: string;
  generationParams?: GenerationParams;
  personaId?: number | null;
  contextBudgetTokens?: number | null;
  model?: ModelInfo;
}

export interface StreamSendCallbacks {
  /** Send failed (HTTP, network, or in-stream error event). */
  onError?: (err: unknown) => void;
  /** User aborted before any text streamed - message was not sent.
   *
   * `attachmentsSurvived` says whether the staged images are still usable.
   * The two abort-empty cleanups do DIFFERENT things: the server's own
   * disconnect handler UNLINKS attachments back to staged (so a retry can
   * carry them), while the authoritative client-side
   * DELETE /chats/{id}/messages/{id} deletes the attachment rows outright.
   * Restoring the strip on that second path put entries with dead ids back in
   * the composer - blank 56px tiles whose retry got 404 attachment_not_found. */
  onAbortedEmpty?: (info: { attachmentsSurvived: boolean }) => void;
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
 * Last-resort resync delay for the blind abort window (stop before the
 * user_message event): the server HAS persisted a user row but the client
 * never learned its id, so it cannot delete it authoritatively. The server's
 * own disconnect cleanup deletes the row shortly after - this one-shot
 * delayed refetch settles the cache once that cleanup has landed. (v1.1
 * D1/I8: the authoritative client-side delete is the primary mechanism; this
 * timer only covers the id-less window.)
 */
const ABORT_RESYNC_DELAY_MS = 750;

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


/**
 * What to tell the server about speaking this reply.
 *
 * Deliberately a function, not a hook value: it must be read at REQUEST-BUILD
 * time rather than at render time. A render-time snapshot would speak - or
 * fail to - according to whatever the toggle was when the component last drew,
 * which is precisely the surprise the toggle rule exists to prevent.
 */
function speakOptions(narrationVoice: NarrationMode): {
  speak?: boolean;
  speakNarrative?: "same" | "narrator" | "skip";
} {
  const { continuousVoice } = useUiStore.getState();
  // The narration mode travels EVEN WHEN continuous is off.
  //
  // The server arms a dormant speaker on every stream so the per-message Speak
  // button can wake it mid-reply - and it configures that speaker from THIS
  // request. Sending the mode only when continuous was on left speak-live
  // permanently on the default, so the narration setting silently did nothing
  // for exactly the people who had not turned continuous on. Regression:
  // test_speak_live_honours_the_narration_setting.
  return {
    ...(continuousVoice ? { speak: true } : {}),
    speakNarrative: narrationVoice,
  };
}

export function useStreamingCompletion() {
  const qc = useQueryClient();
  const pushError = useErrorStore((s) => s.pushError);
  // The sentence-pause dial and the narration mode, read at REQUEST-BUILD time
  // straight out of the query cache.
  //
  // Both used to be mirrored into refs written during render. Two things were
  // wrong with that. The refs rule objects for a real reason - a render React
  // throws away still mutates the ref - and, more to the point, a render-time
  // snapshot was never what this code wanted: the paragraph above
  // `speakOptions` says the value must be read when the request is built, and
  // fifteen lines further up that function already does exactly this for
  // `continuousVoice`, reading the store inside the callback. This is that
  // pattern, one dial along.
  //
  // It also keeps the property the refs were there to protect - a dial moved
  // mid-reply must not rebuild the send callbacks - and keeps it more simply,
  // because there is no longer a changing value for them to close over. The
  // reason those dials are read here at all still holds: a value only the open
  // Delivery page knew is exactly why this dial did nothing for its three
  // production callers.
  const deliveryPrefs = useCallback(
    () => qc.getQueryData<TtsTagPrefs>(keys.ttsTagPrefs()),
    [qc],
  );
  // Still subscribed, because the one-time migration below needs to react to
  // the value arriving. Nothing on the send path reads this binding.
  const tagPrefs = useTagPrefs().data;
  useEffect(() => {
    if (tagPrefs) void migrateDeviceNarration(tagPrefs.narrative);
  }, [tagPrefs]);

  const [streamingByChat, setStreamingByChat] = useState<
    ReadonlyMap<number, StreamingEntry>
  >(() => new Map());
  const controllersRef = useRef<Map<number, AbortController>>(new Map());
  // Active frame flushers, tracked so unmount can cancel any queued rAF before
  // it fires setState on a torn-down component.
  const flushersRef = useRef<Set<ReturnType<typeof createFrameFlusher>>>(
    new Set(),
  );
  // Pending abort-resync timers (see ABORT_RESYNC_DELAY_MS), keyed by chat.
  const resyncTimersRef = useRef<Map<number, ReturnType<typeof setTimeout>>>(
    new Map(),
  );
  // Chats whose stream has seen `done` but whose SSE body is still open while
  // the backend drains voice events (see claimChat).
  const drainingRef = useRef<Set<number>>(new Set());

  // Unmount: abort every in-flight stream and cancel any pending frame. Without
  // this an SSE request keeps running (and its rAF keeps firing) after the hook
  // host is gone.
  useEffect(() => {
    const controllers = controllersRef.current;
    const flushers = flushersRef.current;
    const resyncTimers = resyncTimersRef.current;
    return () => {
      for (const controller of controllers.values()) controller.abort();
      controllers.clear();
      for (const flusher of flushers) flusher.cancel();
      flushers.clear();
      for (const timer of resyncTimers.values()) clearTimeout(timer);
      resyncTimers.clear();
    };
  }, []);

  /** One-shot delayed invalidate for the blind abort window (I8: cancellable,
   * re-armed per chat, cleared on unmount and when a new stream starts). */
  const scheduleAbortResync = useCallback(
    (chatId: number) => {
      const timers = resyncTimersRef.current;
      const existing = timers.get(chatId);
      if (existing != null) clearTimeout(existing);
      timers.set(
        chatId,
        setTimeout(() => {
          timers.delete(chatId);
          void qc.invalidateQueries({ queryKey: keys.messages(chatId) });
        }, ABORT_RESYNC_DELAY_MS),
      );
    },
    [qc],
  );

  const cancelAbortResync = useCallback((chatId: number) => {
    const timers = resyncTimersRef.current;
    const existing = timers.get(chatId);
    if (existing != null) {
      clearTimeout(existing);
      timers.delete(chatId);
    }
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

  /**
   * Claim the chat's single stream slot. False = a LIVE stream owns it.
   *
   * The controller map is NOT a faithful mirror of "the UI is busy". After
   * `done` the backend deliberately holds the SSE body open to drain voice
   * events (up to DRAIN_TIMEOUT_S = 120s), while `done` has already cleared the
   * streaming entry - so the composer is enabled, the Stop button is gone, and
   * the chat reads as idle for the user. A bare `controllersRef.has(chatId)`
   * guard turned every send/regenerate/edit in that window into a silent
   * no-op: no request, no callback, no error, and the typed text destroyed.
   *
   * A new request in the drain window supersedes the tail audio instead: the
   * drain is aborted (which stops playback, see the finally blocks) and the
   * caller proceeds. The superseded stream's teardown is identity-guarded so
   * it cannot evict the newcomer it just handed the chat to.
   */
  const claimChat = useCallback((chatId: number) => {
    const existing = controllersRef.current.get(chatId);
    if (existing == null) return true;
    if (!drainingRef.current.has(chatId)) return false;
    drainingRef.current.delete(chatId);
    existing.abort();
    return true;
  }, []);

  const startSend = useCallback(
    async (vars: StreamSendVars, callbacks?: StreamSendCallbacks) => {
      const { chatId } = vars;
      // One LIVE stream per chat - the UI enforces this; guard defensively.
      // A post-`done` voice drain is not live: claimChat supersedes it.
      if (!claimChat(chatId)) return;

      const controller = new AbortController();
      controllersRef.current.set(chatId, controller);
      registerStream(chatId, controller);
      // A fresh stream supersedes any pending abort-resync for this chat -
      // its refetch would land mid-stream with pre-append state (I8).
      cancelAbortResync(chatId);
      setEntry(chatId, { kind: "send", text: "" });

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
      // Voice rides along; nothing is built unless audio actually arrives.
      const voice = createStreamVoice({ gapSeconds: deliveryPrefs()?.gap ?? 0 });
      let sawDone = false;
      let errorEvent: StreamErrorEvent | null = null;
      // rAF batching: deltas accumulate in streamedText; one frame per batch
      // flushes them into the streaming entry (see createFrameFlusher).
      const flusher = createFrameFlusher(() => {
        setEntry(chatId, { kind: "send", text: streamedText });
      });
      flushersRef.current.add(flusher);

      const handleEvent = (event: StreamEvent) => {
        voice.handle(event);
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
            // The reply is on screen now: switching the toggle on from here
            // must not go back and read it (S15).
            noteFirstDelta(chatId);
            streamedText += event.content;
            flusher.schedule();
            break;
          case "done":
            sawDone = true;
            recordContextNotes(chatId, event);
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
            // Resync net - UNCONDITIONAL (v1.1 D3/H20): the synchronous
            // setQueryData above keeps the UI instant; this background GET
            // settles the cache on server truth every send. It closes both
            // the raced-first-GET seeding case AND any ghost row a previous
            // aborted exchange left behind - one cheap GET per send.
            void qc.invalidateQueries({ queryKey: keys.messages(chatId) });
            // Same-batch teardown: the transient streaming bubble must vanish
            // in the SAME commit the cached rows land, or React paints an
            // intermediate frame showing the reply twice. The finally-block
            // clear stays as the idempotent safety net.
            clearEntry(chatId);
            // From here the chat READS as idle but the SSE body is still open
            // for the voice drain - mark it so a new request supersedes the
            // drain instead of being swallowed (claimChat).
            drainingRef.current.add(chatId);
            break;
          case "error":
            errorEvent = {
              status: event.status,
              code: event.code,
              partialSaved: event.partial_saved === true,
            };
            flusher.flushNow();
            break;
          case "notice":
            reportStreamNotice(event);
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

      /**
       * The provider failed, but AFTER text had arrived, and the server kept
       * it (KÖK 16). Mirroring failSend here would delete the exchange the
       * server just committed - so this keeps the rows and resyncs instead,
       * exactly like the abort-with-partial branch. The error still surfaces:
       * the user keeps what they read AND learns why it stopped.
       *
       * No scheduleAbortResync: unlike the abort path, this write happens
       * BEFORE the error event is sent, so the immediate refetch already
       * answers with the saved rows.
       */
      const failSendKeepingPartial = (err: unknown) => {
        qc.invalidateQueries({ queryKey: keys.messages(chatId) });
        qc.invalidateQueries({ queryKey: keys.chats() });
        callbacks?.onError?.(err);
        callbacks?.onPersisted?.();
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
        // Read HERE, when the request is BUILT - which is what makes
        // the toggle's rule fall out for free: flipping it mid-stream
        // cannot retro-fit the reply already arriving, and the next
        // send picks it up with no extra state to keep in sync.
        ...speakOptions(deliveryPrefs()?.narrative ?? "same"),
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

        // K-28: `!sawDone` guards this branch, and on the send path that guard
        // is worth more than a toast. `done` leaves the body open for the voice
        // drain, so a late `error` frame can arrive after the exchange is saved
        // and on screen - and this branch answers it with `failSend`, which
        // REMOVES the user's row from the cache and hands the text back to
        // ChatCanvas, which puts it in the composer as a failed draft. The
        // reader sees a reply they were just given disappear, with their own
        // sentence back in the box inviting them to send - and pay for - it a
        // second time. The catch branch below already had this guard and says
        // why; the terminal branch did not.
        if (errorEvent != null && !sawDone) {
          // Cast because the assignment happens inside the event callback,
          // which control-flow analysis does not follow: without it TS narrows
          // the checked variable to .
          const evt = errorEvent as StreamErrorEvent;
          const fail = evt.partialSaved ? failSendKeepingPartial : failSend;
          fail(makeApiError(evt.status, evt.code));
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
        if (sawDone) {
          // The body was torn down INSIDE the post-`done` voice drain (Stop,
          // unmount, or a newer request claiming the chat). The exchange is
          // already persisted server-side and already written to the cache -
          // the abort branches below would delete a completed exchange.
          callbacks?.onPersisted?.();
        } else if (isAbortError(err) || controller.signal.aborted) {
          if (streamedText.length > 0) {
            // Backend persisted the partial as the assistant message - the
            // refetch swaps it in (user row stays, attachments consumed).
            //
            // Same LATE-propagation premise as the abort-empty branch below:
            // the server writes the partial inside its `except GeneratorExit`
            // handler, which runs AFTER our disconnect, so this immediate
            // refetch usually answers with the pre-insert list - and the
            // finally-block clearEntry then removes the transient bubble. The
            // partial the user watched arrive was invisible until they
            // switched chats or sent again. The delayed second refetch is what
            // actually lands it (I8), and chats() carries the new preview.
            qc.invalidateQueries({ queryKey: keys.messages(chatId) });
            scheduleAbortResync(chatId);
            qc.invalidateQueries({ queryKey: keys.chats() });
            callbacks?.onPersisted?.();
          } else {
            // Nothing streamed. The server's own disconnect cleanup deletes
            // the user row, but its GeneratorExit propagates LATE - our
            // refetch below usually answers BEFORE that delete and writes
            // the row back into the cache as an undeletable ghost (v1.1 D1).
            // The server's cleanup UNLINKS attachments back to staged; our
            // own delete removes the rows. Which one ran decides whether the
            // caller may restore the staged strip.
            let attachmentsSurvived = true;
            if (realUserMessageId != null) {
              // The client saw the persisted id - become the authority and
              // delete it BEFORE reconciling. Raw API call, no toast (H11):
              // a 404 just means the server's cleanup won the race - same
              // outcome; any other failure is settled by the invalidate.
              try {
                await deleteMessageAndFollowing(chatId, realUserMessageId);
                attachmentsSurvived = false;
              } catch {
                /* intentionally swallowed - see above. A failure here means
                   the server's own cleanup won, which unlinks rather than
                   deletes, so the staged ids are still good. */
              }
            } else {
              // Blind window: a row may exist server-side under an id we
              // never learned. Arm the delayed second refetch (I8).
              scheduleAbortResync(chatId);
            }
            removeUserRows();
            qc.invalidateQueries({ queryKey: keys.messages(chatId) });
            callbacks?.onAbortedEmpty?.({ attachmentsSurvived });
          }
        } else {
          failSend(err);
        }
      } finally {
        // Aborted, failed or finished: playback must not outlive
        // the stream it belongs to. `voice_done` is what ends it
        // normally; this is the net under every other path.
        // `sawDone` alone was the wrong test. The backend holds the SSE body
        // open through the whole post-`done` voice-drain window, so pressing
        // stop in that window aborts a stream that HAS seen `done` - and the
        // audio played on for a reply the user had already dismissed. Abort is
        // what decides; a stream that finished cleanly keeps playing on purpose.
        if (!sawDone || controller.signal.aborted) voice.stop();
        // Guard: no queued frame may fire after clearEntry, or it would
        // resurrect a ghost streaming entry.
        flusher.cancel();
        flushersRef.current.delete(flusher);
        unregisterStream(chatId, controller);
        // Identity-guarded: a request issued during our post-`done` voice
        // drain has already claimed this chat (claimChat). Deleting the slot
        // blindly would strip the NEWCOMER's controller - making its Stop
        // button a no-op - and clear the streaming entry it just set.
        if (controllersRef.current.get(chatId) === controller) {
          controllersRef.current.delete(chatId);
          drainingRef.current.delete(chatId);
          clearEntry(chatId);
        }
      }
    },
    // deliveryPrefs is useCallback([qc]) and qc never changes, so naming it
    // here does not put the send callback back on the rebuild treadmill the
    // refs it replaced were avoiding.
    [qc, setEntry, clearEntry, scheduleAbortResync, cancelAbortResync, claimChat,
     deliveryPrefs],
  );

  const startRegenerate = useCallback(
    async (vars: StreamRegenerateVars) => {
      const { chatId, messageId } = vars;
      if (!claimChat(chatId)) return;

      const controller = new AbortController();
      controllersRef.current.set(chatId, controller);
      registerStream(chatId, controller);
      // A fresh stream supersedes any pending abort-resync for this chat -
      // its refetch would land mid-stream with pre-append state (I8). Same
      // rule as startSend/startEdit; regenerate is reachable straight after
      // an aborted send, which is exactly when one is armed.
      cancelAbortResync(chatId);
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
      // Voice rides along; nothing is built unless audio actually arrives.
      const voice = createStreamVoice({ gapSeconds: deliveryPrefs()?.gap ?? 0 });
      let sawDone = false;
      let errorEvent: StreamErrorEvent | null = null;
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
        voice.handle(event);
        switch (event.type) {
          case "user_message":
            // Existing preceding user row - already in the cache; ignore.
            break;
          case "delta":
            // The reply is on screen now: switching the toggle on from here
            // must not go back and read it (S15).
            noteFirstDelta(chatId);
            streamedText += event.content;
            flusher.schedule();
            break;
          case "done": {
            sawDone = true;
            recordContextNotes(chatId, event);
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
            // Idle-looking chat, still-open body: see startSend's `done`.
            drainingRef.current.add(chatId);
            break;
          }
          case "error":
            errorEvent = {
              status: event.status,
              code: event.code,
              partialSaved: event.partial_saved === true,
            };
            flusher.flushNow();
            break;
          case "notice":
            reportStreamNotice(event);
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
            // Read HERE, when the request is BUILT - which is what makes
            // the toggle's rule fall out for free: flipping it mid-stream
            // cannot retro-fit the reply already arriving, and the next
            // send picks it up with no extra state to keep in sync.
            ...speakOptions(deliveryPrefs()?.narrative ?? "same"),
          }),
          { signal: controller.signal, onEvent: handleEvent },
        );

        // Stream finished - apply any still-queued deltas before the terminal
        // branches below.
        flusher.flushNow();

        // Regenerate errors surface as a toast (single surface for regenerate).
        // Old assistant row is intact server-side - no cache change needed.
        //
        // K-28: `!sawDone`, for the same reason the catch branch below carries
        // it. After `done` the variant has already been swapped in; a failure
        // notice about a connection that was closing describes nothing the
        // reader can act on, and contradicts the reply in front of them.
        if (errorEvent != null && !sawDone) {
          // Cast because the assignment happens inside the event callback,
          // which control-flow analysis does not follow: without it TS narrows
          // the checked variable to .
          const evt = errorEvent as StreamErrorEvent;
          // K-21 and K-26. The chat, so two conversations failing the same
          // way are two events rather than one; and whether the half that
          // arrived was kept, which the backend has always sent, this file
          // has always parsed, and makeApiError has always dropped.
          pushError(makeApiError(evt.status, evt.code), "error", {
            chatId,
            partialSaved: evt.partialSaved,
          });
        } else if (!sawDone) {
          pushError(makeApiError(0, "invalid_response_shape"), "error", {
            chatId,
          });
        }
      } catch (err) {
        flusher.flushNow();
        if (sawDone || isAbortError(err) || controller.signal.aborted) {
          // User-initiated stop: old message intact, partial discarded - silent.
          // `sawDone`: the body broke during the voice drain, AFTER the variant
          // was persisted and swapped in - a toast there would blame a reply
          // that succeeded.
        } else {
          pushError(err);
        }
      } finally {
        // Aborted, failed or finished: playback must not outlive
        // the stream it belongs to. `voice_done` is what ends it
        // normally; this is the net under every other path.
        // `sawDone` alone was the wrong test. The backend holds the SSE body
        // open through the whole post-`done` voice-drain window, so pressing
        // stop in that window aborts a stream that HAS seen `done` - and the
        // audio played on for a reply the user had already dismissed. Abort is
        // what decides; a stream that finished cleanly keeps playing on purpose.
        if (!sawDone || controller.signal.aborted) voice.stop();
        // Guard: no queued frame may fire after clearEntry (ghost entry).
        flusher.cancel();
        flushersRef.current.delete(flusher);
        unregisterStream(chatId, controller);
        // Identity-guarded: a request issued during our post-`done` voice
        // drain has already claimed this chat (claimChat). Deleting the slot
        // blindly would strip the NEWCOMER's controller - making its Stop
        // button a no-op - and clear the streaming entry it just set.
        if (controllersRef.current.get(chatId) === controller) {
          controllersRef.current.delete(chatId);
          drainingRef.current.delete(chatId);
          clearEntry(chatId);
        }
      }
    },
    [qc, pushError, setEntry, clearEntry, cancelAbortResync, claimChat,
     deliveryPrefs],
  );

  const startEdit = useCallback(
    async (vars: StreamEditVars) => {
      const { chatId, messageId } = vars;
      if (!claimChat(chatId)) return;

      const controller = new AbortController();
      controllersRef.current.set(chatId, controller);
      registerStream(chatId, controller);
      cancelAbortResync(chatId);
      setEntry(chatId, { kind: "edit", targetMessageId: messageId, text: "" });

      await qc.cancelQueries({ queryKey: keys.messages(chatId) });

      // Snapshot for rollback: the server writes NOTHING until the atomic
      // swap at done, so on abort/error the pre-edit list is the truth.
      const snapshot = qc.getQueryData<Message[]>(keys.messages(chatId));

      // Optimistic: replace the edited row's text and hide the tail - the
      // "everything after rewrites" outcome is visible immediately.
      qc.setQueryData<Message[]>(keys.messages(chatId), (prev) =>
        prev
          ?.filter((m) => m.id <= messageId)
          .map((m) =>
            m.id === messageId ? { ...m, content: vars.message } : m,
          ),
      );
      // I14: restore only if OUR write is still the one in the cache - a
      // mid-stream foreign write (refetch, another mutation) must not be
      // clobbered by this stale snapshot; invalidate settles those instead.
      //
      // K-28(b): this compared `dataUpdatedAt`, which is a MILLISECOND stamp.
      // Two writes inside the same millisecond carry the same one, so a foreign
      // write that landed in the same tick read as "still ours" and got
      // overwritten. Whether the guard held came down to how the clock fell;
      // the test for it had to sleep 3ms to make the race come out the same way
      // twice. The array identity has no such resolution: setQueryData produces
      // a new array on every write, ours or anybody's, so this is exact.
      let ownData = qc.getQueryData<Message[]>(keys.messages(chatId));

      const restoreSnapshot = () => {
        const current = qc.getQueryData<Message[]>(keys.messages(chatId));
        if (current === ownData && snapshot != null) {
          qc.setQueryData(keys.messages(chatId), snapshot);
        }
        qc.invalidateQueries({ queryKey: keys.messages(chatId) });
      };

      let streamedText = "";
      // Voice rides along; nothing is built unless audio actually arrives.
      const voice = createStreamVoice({ gapSeconds: deliveryPrefs()?.gap ?? 0 });
      let sawDone = false;
      let errorEvent: StreamErrorEvent | null = null;
      const flusher = createFrameFlusher(() => {
        setEntry(chatId, {
          kind: "edit",
          targetMessageId: messageId,
          text: streamedText,
        });
      });
      flushersRef.current.add(flusher);

      const handleEvent = (event: StreamEvent) => {
        voice.handle(event);
        switch (event.type) {
          case "user_message":
            // Server preview of the edited row (same id, new content) -
            // replace in place; the optimistic filter already hid the tail.
            qc.setQueryData<Message[]>(keys.messages(chatId), (prev) =>
              prev?.map((m) => (m.id === event.message.id ? event.message : m)),
            );
            ownData = qc.getQueryData<Message[]>(keys.messages(chatId));
            break;
          case "delta":
            // The reply is on screen now: switching the toggle on from here
            // must not go back and read it (S15).
            noteFirstDelta(chatId);
            streamedText += event.content;
            flusher.schedule();
            break;
          case "done":
            sawDone = true;
            recordContextNotes(chatId, event);
            flusher.flushNow();
            void qc.cancelQueries({ queryKey: keys.messages(chatId) });
            qc.setQueryData<Message[]>(keys.messages(chatId), (prev) => {
              const kept = (prev ?? []).filter(
                (m) => m.id < messageId,
              );
              return [...kept, event.user_message, event.assistant_message];
            });
            qc.invalidateQueries({ queryKey: keys.chats() });
            // D3 discipline: settle on server truth in the background.
            void qc.invalidateQueries({ queryKey: keys.messages(chatId) });
            clearEntry(chatId);
            // Idle-looking chat, still-open body: see startSend's `done`.
            drainingRef.current.add(chatId);
            break;
          case "error":
            errorEvent = {
              status: event.status,
              code: event.code,
              partialSaved: event.partial_saved === true,
            };
            flusher.flushNow();
            break;
          case "notice":
            reportStreamNotice(event);
            break;
        }
      };

      try {
        await streamEditMessage(
          chatId,
          messageId,
          buildEditPayload({
            message: vars.message,
            modelId: vars.modelId,
            generationParams: vars.generationParams,
            personaId: vars.personaId,
            contextBudgetTokens: vars.contextBudgetTokens,
            model: vars.model,
            // Read HERE, when the request is BUILT - which is what makes
            // the toggle's rule fall out for free: flipping it mid-stream
            // cannot retro-fit the reply already arriving, and the next
            // send picks it up with no extra state to keep in sync.
            ...speakOptions(deliveryPrefs()?.narrative ?? "same"),
          }),
          { signal: controller.signal, onEvent: handleEvent },
        );

        flusher.flushNow();

        // Edit errors surface as a toast (like regenerate - the composer is
        // not involved). Server wrote nothing - restore the pre-edit list.
        //
        // K-28: `!sawDone`. "Server wrote nothing" is only true before `done`.
        // After it the atomic swap has landed, so this branch would both report
        // a failure that did not happen AND try to roll back a committed edit -
        // the catch branch three lines down refuses to do exactly that.
        if (errorEvent != null && !sawDone) {
          // Cast because the assignment happens inside the event callback,
          // which control-flow analysis does not follow: without it TS narrows
          // the checked variable to .
          const evt = errorEvent as StreamErrorEvent;
          restoreSnapshot();
          // K-21 and K-26, same as the regenerate path above.
          pushError(makeApiError(evt.status, evt.code), "error", {
            chatId,
            partialSaved: evt.partialSaved,
          });
        } else if (!sawDone) {
          restoreSnapshot();
          pushError(makeApiError(0, "invalid_response_shape"), "error", {
            chatId,
          });
        }
      } catch (err) {
        flusher.flushNow();
        // A tear-down inside the post-`done` voice drain must NOT roll back:
        // the atomic swap already landed server-side and in the cache.
        if (!sawDone) restoreSnapshot();
        if (sawDone || isAbortError(err) || controller.signal.aborted) {
          // User-initiated stop: old content + tail intact - silent.
        } else {
          pushError(err);
        }
      } finally {
        // Aborted, failed or finished: playback must not outlive
        // the stream it belongs to. `voice_done` is what ends it
        // normally; this is the net under every other path.
        // `sawDone` alone was the wrong test. The backend holds the SSE body
        // open through the whole post-`done` voice-drain window, so pressing
        // stop in that window aborts a stream that HAS seen `done` - and the
        // audio played on for a reply the user had already dismissed. Abort is
        // what decides; a stream that finished cleanly keeps playing on purpose.
        if (!sawDone || controller.signal.aborted) voice.stop();
        flusher.cancel();
        flushersRef.current.delete(flusher);
        unregisterStream(chatId, controller);
        // Identity-guarded: a request issued during our post-`done` voice
        // drain has already claimed this chat (claimChat). Deleting the slot
        // blindly would strip the NEWCOMER's controller - making its Stop
        // button a no-op - and clear the streaming entry it just set.
        if (controllersRef.current.get(chatId) === controller) {
          controllersRef.current.delete(chatId);
          drainingRef.current.delete(chatId);
          clearEntry(chatId);
        }
      }
    },
    [qc, pushError, setEntry, clearEntry, cancelAbortResync, claimChat,
     deliveryPrefs],
  );

  return { streamingByChat, startSend, startRegenerate, startEdit, stop };
}
