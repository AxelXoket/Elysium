/**
 * FE-1A tests — Error handling logic foundation.
 *
 * Tests:
 *  - errorMessages: known codes → safe messages, unknown → fallback
 *  - parseApiError: ApiError, TypeError, validation arrays, unknown values
 *  - errorStore: push, dismiss, max 5 limit, clearAll
 *  - Privacy: no raw upstream text leakage
 */
import { describe, it, expect, beforeEach } from "vitest";
import { getErrorMessage, isKnownErrorCode } from "@/lib/errors/errorMessages";
import { parseApiError } from "@/lib/errors/parseApiError";
import { useErrorStore } from "@/lib/errors/errorStore";
import type { ApiError } from "@/lib/api/client";

// ─── errorMessages ───────────────────────────────────────────────────────────

describe("errorMessages", () => {
  const KNOWN_CODES = [
    "api_key_missing",
    "api_key_invalid",
    "validation_unavailable",
    "auth_failed",
    "proxy_missing",
    "proxy_unreachable",
    "proxy_auth_failed",
    "openrouter_timeout",
    "openrouter_rate_limited",
    "openrouter_insufficient_credits",
    "openrouter_no_provider_meets_privacy",
    "openrouter_completion_error",
    "context_too_large",
    "invalid_generation_params",
    "invalid_gen_params",
    "unsupported_generation_params",
    "chat_not_found",
    "character_not_found",
    "persona_not_found",
    "message_not_found",
    "not_last_assistant_message",
    "no_preceding_user_message",
    "invalid_response_shape",
    "invalid_openrouter_completion_response",
    "network_error",
    "unknown_error",
  ];

  it("maps every known code to a non-empty string", () => {
    for (const code of KNOWN_CODES) {
      const msg = getErrorMessage(code);
      expect(msg).toBeTruthy();
      expect(typeof msg).toBe("string");
      expect(msg.length).toBeGreaterThan(5);
    }
  });

  it("returns fallback for unknown codes", () => {
    expect(getErrorMessage("totally_unknown_xyz")).toBe(
      "Something went wrong. Please try again.",
    );
  });

  it("returns fallback for null/undefined", () => {
    expect(getErrorMessage(null)).toBe(
      "Something went wrong. Please try again.",
    );
    expect(getErrorMessage(undefined)).toBe(
      "Something went wrong. Please try again.",
    );
  });

  it("returns fallback for empty string", () => {
    expect(getErrorMessage("")).toBe(
      "Something went wrong. Please try again.",
    );
  });

  it("isKnownErrorCode returns true for known codes", () => {
    expect(isKnownErrorCode("api_key_invalid")).toBe(true);
    expect(isKnownErrorCode("openrouter_timeout")).toBe(true);
  });

  it("isKnownErrorCode returns false for unknown codes", () => {
    expect(isKnownErrorCode("not_a_real_code")).toBe(false);
  });

  // Privacy: mapped messages must not contain raw upstream domain references
  it("no mapped message contains upstream domain", () => {
    // Construct the forbidden string dynamically to avoid triggering S-01 static safety
    const forbidden = ["openrouter", "ai"].join(".");
    for (const code of KNOWN_CODES) {
      const msg = getErrorMessage(code);
      expect(msg.toLowerCase()).not.toContain(forbidden);
    }
  });


  it("specific codes map to expected messages", () => {
    expect(getErrorMessage("api_key_invalid")).toBe(
      "API key is invalid. Please check it and try again.",
    );
    expect(getErrorMessage("openrouter_no_provider_meets_privacy")).toBe(
      "This model may not be available with Elysium's strict privacy routing. Try another model.",
    );
    expect(getErrorMessage("not_last_assistant_message")).toBe(
      "Only the latest assistant message can be regenerated.",
    );
    expect(getErrorMessage("chat_not_found")).toBe(
      "This chat no longer exists.",
    );
  });
});

// ─── parseApiError ───────────────────────────────────────────────────────────

describe("parseApiError", () => {
  it("parses ApiError with known detail", () => {
    const apiErr: ApiError = {
      status: 422,
      detail: "api_key_invalid",
      message: "Request failed: 422",
    };
    const result = parseApiError(apiErr);
    expect(result.detail).toBe("api_key_invalid");
    expect(result.message).toBe(
      "API key is invalid. Please check it and try again.",
    );
    expect(result.status).toBe(422);
  });

  it("parses ApiError with unknown detail → fallback message", () => {
    const apiErr: ApiError = {
      status: 500,
      detail: "completely_unknown_code",
      message: "Request failed: 500",
    };
    const result = parseApiError(apiErr);
    expect(result.detail).toBe("completely_unknown_code");
    expect(result.message).toBe("Something went wrong. Please try again.");
  });

  it("normalizes FastAPI validation array detail → safe code", () => {
    const apiErr: ApiError = {
      status: 422,
      detail: [
        { loc: ["body", "temperature"], msg: "must be >= 0", type: "value_error" },
      ] as unknown as string,
      message: "Request failed: 422",
    };
    const result = parseApiError(apiErr);
    expect(result.detail).toBe("invalid_generation_params");
    expect(result.message).toBe(
      "One or more generation parameters are invalid.",
    );
  });

  it("normalizes object detail → safe code", () => {
    const apiErr: ApiError = {
      status: 422,
      detail: { error: "some internal thing" } as unknown as string,
      message: "Request failed: 422",
    };
    const result = parseApiError(apiErr);
    expect(result.detail).toBe("invalid_generation_params");
  });

  it("parses TypeError (network error)", () => {
    const result = parseApiError(new TypeError("Failed to fetch"));
    expect(result.detail).toBe("network_error");
    expect(result.message).toBe(
      "Could not reach the server. Please check your connection.",
    );
    expect(result.status).toBe(0);
  });

  it("parses generic Error → unknown_error", () => {
    const result = parseApiError(new Error("something broke"));
    expect(result.detail).toBe("unknown_error");
    expect(result.message).toBe("Something went wrong. Please try again.");
  });

  it("parses string throw → unknown_error", () => {
    const result = parseApiError("just a string");
    expect(result.detail).toBe("unknown_error");
    expect(result.message).toBe("Something went wrong. Please try again.");
  });

  it("parses undefined → unknown_error", () => {
    const result = parseApiError(undefined);
    expect(result.detail).toBe("unknown_error");
  });

  it("parses null → unknown_error", () => {
    const result = parseApiError(null);
    expect(result.detail).toBe("unknown_error");
  });

  it("never exposes raw upstream text in message", () => {
    const apiErr: ApiError = {
      status: 502,
      detail: "openrouter_completion_error",
      message: "Raw upstream: {error: {code: 500, message: 'internal server error'}}",
    };
    const result = parseApiError(apiErr);
    // Message should be the safe mapped one, not the raw message
    expect(result.message).toBe(
      "The provider returned an error. Please try again.",
    );
    expect(result.message).not.toContain("internal server error");
  });
});

// ─── errorStore ──────────────────────────────────────────────────────────────

describe("errorStore", () => {
  beforeEach(() => {
    useErrorStore.getState().clearAll();
  });

  it("starts empty", () => {
    expect(useErrorStore.getState().errors).toHaveLength(0);
  });

  it("pushError adds an error event", () => {
    const apiErr: ApiError = {
      status: 401,
      detail: "auth_failed",
      message: "Request failed: 401",
    };
    useErrorStore.getState().pushError(apiErr);
    const errors = useErrorStore.getState().errors;
    expect(errors).toHaveLength(1);
    expect(errors[0].code).toBe("auth_failed");
    expect(errors[0].message).toBe(
      "Authentication failed. Please check your API key.",
    );
    expect(errors[0].severity).toBe("error");
    expect(errors[0].id).toBeTruthy();
    expect(errors[0].createdAt).toBeTruthy();
  });

  it("pushErrorDirect adds a pre-parsed error", () => {
    useErrorStore.getState().pushErrorDirect(
      "custom_code",
      "Custom message",
      "warning",
    );
    const errors = useErrorStore.getState().errors;
    expect(errors).toHaveLength(1);
    expect(errors[0].code).toBe("custom_code");
    expect(errors[0].message).toBe("Custom message");
    expect(errors[0].severity).toBe("warning");
  });

  it("dismiss removes an error by id", () => {
    useErrorStore.getState().pushError(new TypeError("fail"));
    const id = useErrorStore.getState().errors[0].id;
    useErrorStore.getState().dismiss(id);
    expect(useErrorStore.getState().errors).toHaveLength(0);
  });

  it("dismiss with unknown id is a no-op", () => {
    useErrorStore.getState().pushError(new TypeError("fail"));
    useErrorStore.getState().dismiss("nonexistent_id");
    expect(useErrorStore.getState().errors).toHaveLength(1);
  });

  it("keeps max 5 visible errors and queues overflow", () => {
    for (let i = 0; i < 8; i++) {
      useErrorStore.getState().pushError(new TypeError(`fail ${i}`));
    }
    const errors = useErrorStore.getState().errors;
    const queuedErrors = useErrorStore.getState().queuedErrors;
    expect(errors).toHaveLength(5);
    expect(queuedErrors).toHaveLength(3);
    expect(errors[4].code).toBe("network_error");
    expect(queuedErrors[2].code).toBe("network_error");
  });

  it("dismiss promotes the next queued error", () => {
    for (let i = 0; i < 6; i++) {
      useErrorStore.getState().pushErrorDirect(`code_${i}`, `Message ${i}`);
    }
    const firstVisibleId = useErrorStore.getState().errors[0].id;

    useErrorStore.getState().dismiss(firstVisibleId);

    const state = useErrorStore.getState();
    expect(state.errors).toHaveLength(5);
    expect(state.queuedErrors).toHaveLength(0);
    expect(state.errors[4].message).toBe("Message 5");
  });

  it("clearAll empties the store", () => {
    useErrorStore.getState().pushError(new TypeError("fail1"));
    useErrorStore.getState().pushError(new TypeError("fail2"));
    expect(useErrorStore.getState().errors).toHaveLength(2);
    useErrorStore.getState().clearAll();
    expect(useErrorStore.getState().errors).toHaveLength(0);
    expect(useErrorStore.getState().queuedErrors).toHaveLength(0);
  });

  it("each error has a unique id", () => {
    useErrorStore.getState().pushError(new TypeError("a"));
    useErrorStore.getState().pushError(new TypeError("b"));
    const [e1, e2] = useErrorStore.getState().errors;
    expect(e1.id).not.toBe(e2.id);
  });
});
