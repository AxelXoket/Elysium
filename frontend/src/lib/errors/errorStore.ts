/**
 * errorStore.ts - Lightweight Zustand store for error events.
 *
 * Provides a centralized, non-persistent error event queue for the app.
 * ErrorToastStack consumes the visible queue and promotes pending events.
 *
 * Rules:
 *  - Max 5 errors visible; extra events wait in-memory until a slot opens
 *  - Queue is capped at 20 events; oldest queued events are dropped first
 *  - Duplicate suppression: an event whose identity matches one already
 *    visible or queued is skipped entirely (prevents identical toast spam).
 *    What counts as the same event is argued at identityOf, not here, because
 *    that line has been wrong twice and a summary of it goes stale silently
 *  - No localStorage/sessionStorage/IndexedDB/cookies
 *  - No sensitive data in error events (messages use safe mapped text)
 *  - Events have stable shape for future UI: id, message, code, createdAt, severity
 */

import { create } from "zustand";
import { parseApiError } from "./parseApiError";

// ── Types ────────────────────────────────────────────────────────────────────

export type ErrorSeverity = "error" | "warning";

export interface ErrorEvent {
  /** Unique event id for dismiss targeting */
  id: string;
  /** Safe user-facing message (never raw upstream text) */
  message: string;
  /** Backend error code if available */
  code: string;
  /** ISO timestamp */
  createdAt: string;
  /** Severity level */
  severity: ErrorSeverity;
  /**
   * Which conversation this belongs to, when it belongs to one. K-21.
   *
   * Undefined for the ~two thirds of push sites that have no chat at all -
   * settings, the voice engine, the player. Undefined is NOT a wildcard: an
   * event with no chat never matches one with a chat, because aggregating
   * across that boundary is how a second conversation's failure disappeared
   * into the first one's toast.
   */
  chatId?: number;
  /**
   * Which button, when two of them can fail the same way at once.
   *
   * The four vault discards push the same codes with no chat id, so a 423
   * on the second notice was byte-identical to the 423 on the first and was
   * dropped - in a panel whose in-line alerts only ever report the fields of
   * a SUCCESSFUL response, so the second button reported nothing at all.
   * Undefined everywhere else, and undefined never equals a real source.
   */
  source?: string;
  /**
   * Whether the half of the reply that had arrived was kept. K-26.
   *
   * The backend has always sent this and the frontend has always parsed it,
   * and then it died at makeApiError, which returns {status, detail,
   * message}. So the app knew whether the user's words survived and had no
   * way to say so.
   */
  partialSaved?: boolean;
}

/** The context a caller can attach. All optional; most sites have none.
 *
 *  `source` is the caller saying "this is a different event from that one",
 *  and it exists because the dedupe below could not tell four buttons apart.
 *  The four vault discards push the same codes with no chat id, so a 423 on
 *  the second notice was identical to the 423 on the first and vanished -
 *  in a panel whose in-line alerts only ever report success. Anything that
 *  is one deliberate user action per push should pass it; anything that can
 *  genuinely fire twice for one event must NOT, or the dedupe stops working
 *  where it is needed. */
export type ErrorContext = Pick<ErrorEvent, "chatId" | "partialSaved"> & {
  source?: string;
};

interface ErrorState {
  errors: ErrorEvent[];
  queuedErrors: ErrorEvent[];
  /** Push an error from any thrown value. Parses and maps automatically. */
  pushError: (err: unknown, severity?: ErrorSeverity, context?: ErrorContext) => void;
  /** Push an error with a pre-parsed code and message. */
  pushErrorDirect: (code: string, message: string, severity?: ErrorSeverity, context?: ErrorContext) => void;
  /** Dismiss a specific error by id. */
  dismiss: (id: string) => void;
  /** Clear all errors. */
  clearAll: () => void;
}

const MAX_ERRORS = 5;
const MAX_QUEUED_ERRORS = 20;

let _counter = 0;
function nextId(): string {
  _counter += 1;
  return `err_${_counter}_${Date.now()}`;
}

// ── Store ────────────────────────────────────────────────────────────────────

export const useErrorStore = create<ErrorState>()((set) => ({
  errors: [],
  queuedErrors: [],

  pushError: (err, severity = "error", context = {}) => {
    const parsed = parseApiError(err);
    const event: ErrorEvent = {
      id: nextId(),
      message: parsed.message,
      code: parsed.detail,
      createdAt: new Date().toISOString(),
      severity,
      ...context,
    };
    set((state) => enqueueError(state, event));
  },

  pushErrorDirect: (code, message, severity = "error", context = {}) => {
    const event: ErrorEvent = {
      id: nextId(),
      message,
      code,
      createdAt: new Date().toISOString(),
      severity,
      ...context,
    };
    set((state) => enqueueError(state, event));
  },

  dismiss: (id) => {
    set((state) => dismissError(state, id));
  },

  clearAll: () => {
    set({ errors: [], queuedErrors: [] });
  },
}));

/**
 * What makes two events "the same one twice". K-21 and K-23 together.
 *
 * THE CHAT IS PART OF IT (K-21). Every sentence in the catalogue is static
 * text, so two different conversations failing the same way produced byte
 * identical events - and the second was dropped. The user was told one thing
 * had gone wrong when two had, and the one they were not looking at is the
 * one they never heard about. Undefined is a value here, not a wildcard: it
 * never equals a real chat id.
 *
 * THE MESSAGE IS NOT (K-23). It used to be, and that made the rule useless
 * for exactly the codes that need it most: `images_omitted` builds its
 * sentence around a live count, so the text differed every time and it never
 * deduped at all. The code plus its context is the identity; the sentence is
 * a rendering of it.
 *
 * partialSaved is in the key because it changes what the user is being told -
 * "we kept what arrived" and "nothing was saved" are two different events
 * about the same code, and collapsing them would report the wrong one.
 *
 * AND SO IS THE MESSAGE, FOR THE CODES BELOW, for the same reason partialSaved
 * is. K-23's argument holds wherever the sentence is a rendering of the code;
 * it is exactly backwards where one code carries several different things a
 * person could be told. `tts_notice` is the only such code today: one reply
 * can raise "the engine is warming up" and "the reply was cut short" in the
 * same turn, and a key that ignores what the event SAYS called those two the
 * same event and dropped the second in silence. Nothing weakens for the codes
 * K-23 was written for - `images_omitted` still collapses whether it counts
 * one image or three, because its code is not in this set.
 */
const MESSAGE_IS_THE_EVENT: ReadonlySet<string> = new Set(["tts_notice"]);

function identityOf(event: ErrorEvent): string {
  return [
    event.code,
    event.chatId === undefined ? "-" : String(event.chatId),
    event.partialSaved === undefined ? "-" : String(event.partialSaved),
    MESSAGE_IS_THE_EVENT.has(event.code) ? event.message : "",
    // The axis that was missing. Without it, `vault_locked` from the
    // orphaned-copy button and `vault_locked` from the premigrate button
    // one row below it were the same event, and only the first was ever
    // shown. Absent for every existing caller, so nothing else changes.
    event.source ?? "",
  ].join("\u0000");
}

function enqueueError(state: ErrorState, event: ErrorEvent): Pick<ErrorState, "errors" | "queuedErrors"> {
  // Both lists, and that is K-23's other half. Scanning only the visible ones
  // meant a duplicate raised while five toasts were up went into the queue
  // and was shown again as the queue drained - the exact double the check
  // exists to prevent, arriving a few seconds late.
  const identity = identityOf(event);
  const isDuplicate =
    state.errors.some((e) => identityOf(e) === identity) ||
    state.queuedErrors.some((e) => identityOf(e) === identity);
  if (isDuplicate) {
    return {
      errors: state.errors,
      queuedErrors: state.queuedErrors,
    };
  }

  if (state.errors.length < MAX_ERRORS) {
    return {
      errors: [...state.errors, event],
      queuedErrors: state.queuedErrors,
    };
  }

  // Cap the queue at MAX_QUEUED_ERRORS - drop the oldest queued events first.
  return {
    errors: state.errors,
    queuedErrors: [...state.queuedErrors, event].slice(-MAX_QUEUED_ERRORS),
  };
}

function dismissError(state: ErrorState, id: string): Pick<ErrorState, "errors" | "queuedErrors"> {
  const visibleErrors = state.errors.filter((e) => e.id !== id);
  const removedVisible = visibleErrors.length !== state.errors.length;
  const queuedErrors = state.queuedErrors.filter((e) => e.id !== id);

  if (!removedVisible) {
    return {
      errors: state.errors,
      queuedErrors,
    };
  }

  const [nextQueued, ...remainingQueued] = queuedErrors;
  return {
    errors: nextQueued ? [...visibleErrors, nextQueued] : visibleErrors,
    queuedErrors: remainingQueued,
  };
}
