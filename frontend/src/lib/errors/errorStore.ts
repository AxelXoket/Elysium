/**
 * errorStore.ts - Lightweight Zustand store for error events.
 *
 * Provides a centralized, non-persistent error event queue for the app.
 * ErrorToastStack consumes the visible queue and promotes pending events.
 *
 * Rules:
 *  - Max 5 errors visible; extra events wait in-memory until a slot opens
 *  - Queue is capped at 20 events; oldest queued events are dropped first
 *  - Duplicate suppression: an event whose code+message matches a currently
 *    visible toast is skipped entirely (prevents identical toast spam)
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
}

interface ErrorState {
  errors: ErrorEvent[];
  queuedErrors: ErrorEvent[];
  /** Push an error from any thrown value. Parses and maps automatically. */
  pushError: (err: unknown, severity?: ErrorSeverity) => void;
  /** Push an error with a pre-parsed code and message. */
  pushErrorDirect: (code: string, message: string, severity?: ErrorSeverity) => void;
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

  pushError: (err, severity = "error") => {
    const parsed = parseApiError(err);
    const event: ErrorEvent = {
      id: nextId(),
      message: parsed.message,
      code: parsed.detail,
      createdAt: new Date().toISOString(),
      severity,
    };
    set((state) => enqueueError(state, event));
  },

  pushErrorDirect: (code, message, severity = "error") => {
    const event: ErrorEvent = {
      id: nextId(),
      message,
      code,
      createdAt: new Date().toISOString(),
      severity,
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

function enqueueError(state: ErrorState, event: ErrorEvent): Pick<ErrorState, "errors" | "queuedErrors"> {
  // Dedupe: skip if an identical code+message toast is already visible.
  const isDuplicate = state.errors.some(
    (e) => e.code === event.code && e.message === event.message,
  );
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
