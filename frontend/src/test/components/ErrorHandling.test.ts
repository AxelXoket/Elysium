/**
 * FE-1A tests - Error handling logic foundation.
 *
 * Tests:
 *  - errorMessages: known codes → safe messages, unknown → fallback
 *  - parseApiError: ApiError, TypeError, validation arrays, unknown values
 *  - errorStore: push, dismiss, max 5 limit, clearAll
 *  - Privacy: no raw upstream text leakage
 */
import { describe, it, expect, beforeEach } from "vitest";
import { getErrorMessage, isKnownErrorCode, knownErrorCodes } from "@/lib/errors/errorMessages";
import { parseApiError } from "@/lib/errors/parseApiError";
import { useErrorStore } from "@/lib/errors/errorStore";
import type { ApiError } from "@/lib/api/client";

// ─── errorMessages ───────────────────────────────────────────────────────────

describe("errorMessages", () => {
  // KNOWN_CODES and TTS_ERROR_CODES used to live here: two hand-typed arrays,
  // 56 and 31 entries, asserting that every code in them had a sentence. They
  // did. What they could not do is notice a code that was never added to them,
  // which is how four backend-reachable codes shipped rendering the generic
  // fallback and why errorMappingG10.test.ts had to be written.
  //
  // Both loops moved to src/test/lib/errorCatalogue.test.ts on 2026-08-10,
  // where the list comes from shared/error_catalogue.json and the backend is
  // measured against the same file. Nothing about the assertions changed; the
  // source of the list did, from memory to machine.
  //
  // Everything below this point is behaviour and stays.

  // ── Voice / TTS (V0): the contract is that EVERY tts_* code the backend can
  // emit has a real message. The generic loop above only checks length, which
  // the fallback also passes - so assert explicitly that none fall through.

  it("V0: TTS messages name the actual problem", () => {
    expect(getErrorMessage("tts_insufficient_vram")).toMatch(/memory|VRAM/i);
    expect(getErrorMessage("tts_worker_crashed")).toMatch(/voice|engine/i);
    expect(getErrorMessage("tts_model_not_found")).toMatch(/model/i);
    expect(getErrorMessage("tts_runtime_missing")).toMatch(/set up/i);
  });

  it("V3: a machine with no GPU is not told to close other GPU apps", () => {
    // The two causes look alike in code and feel nothing alike to the person
    // reading them: one is fixable in seconds, the other never is.
    const noGpu = getErrorMessage("tts_gpu_unavailable");
    expect(noGpu).toMatch(/no NVIDIA GPU/i);
    expect(noGpu).not.toMatch(/close other/i);
    expect(getErrorMessage("tts_insufficient_vram")).toMatch(/close other/i);
  });

  it("V3: never-installed and installed-then-gone read differently", () => {
    const missing = getErrorMessage("tts_runtime_missing");
    const broken = getErrorMessage("tts_runtime_broken");
    expect(missing).not.toBe(broken);
    expect(broken).toMatch(/again/i);
  });

  it("V3: refused-before-starting and ran-out-midway give different advice", () => {
    // Same shortage, different moment, different fix. Telling someone whose
    // model died mid-sentence to "close other GPU apps" is useless when the
    // real answer is a smaller cache.
    expect(getErrorMessage("tts_insufficient_vram")).toMatch(/close other/i);
    expect(getErrorMessage("tts_out_of_memory")).toMatch(/lower|smaller/i);
  });

  it("V3: a failed setup says nothing was left half-installed", () => {
    // A multi-GB install that dies halfway is frightening. The message has to
    // answer the question the user actually has: is my disk now a mess?
    expect(getErrorMessage("tts_runtime_install_failed")).toMatch(
      /half-installed|try again/i,
    );
  });

  it("V3: setup errors never tell the user to edit a file or open a terminal", () => {
    // Provisioning is the app's job. If any of these leak an instruction to go
    // edit runtimes.json or run pip, the acceptance criterion is broken.
    for (const code of [
      "tts_runtime_missing",
      "tts_runtime_broken",
      "tts_runtime_installing",
      "tts_runtime_install_failed",
      "tts_python_not_found",
    ]) {
      expect(getErrorMessage(code)).not.toMatch(
        /runtimes\.json|terminal|command line|pip install|venv/i,
      );
    }
  });

  it("V3: an unrunnable model still tells the user they can configure it", () => {
    // The promise this whole verdict exists to keep - the settings page stays
    // open, and the message says so instead of reading like a dead end.
    expect(getErrorMessage("tts_gpu_unavailable")).toMatch(
      /browse and configure/i,
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
    // Every sentence in the map now, not a hand-picked subset: the list
    // this used to walk could not name a message it had never heard of.
    for (const code of knownErrorCodes()) {
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
    expect(getErrorMessage("regenerate_conflict")).toBe(
      "The chat changed while regenerating. Please refresh and try again.",
    );
    expect(getErrorMessage("title_required")).toBe(
      "Chat title cannot be empty.",
    );
    expect(getErrorMessage("title_too_long")).toBe(
      "Chat title is too long. Please use at most 200 characters.",
    );
    expect(getErrorMessage("internal_error")).toBe(
      "Something went wrong on the server. Please try again.",
    );
    // v1.1 audit L4: these must NOT fall through to the generic message.
    expect(getErrorMessage("edit_conflict")).not.toBe(
      "Something went wrong. Please try again.",
    );
    expect(getErrorMessage("edit_conflict")).toContain("editing");
    expect(getErrorMessage("not_editable")).toBe(
      "Only your own messages can be edited.",
    );
    expect(getErrorMessage("variant_group_not_last")).toContain("variants");
    expect(getErrorMessage("attachment_too_large")).toBe(
      "This image is too large. Please use an image under 10 MB.",
    );
    expect(getErrorMessage("attachment_unavailable")).toBe(
      "An attached image was already used by another message. Please attach it again.",
    );
    expect(getErrorMessage("model_no_image_input")).toBe(
      "The selected model does not support image input. Remove the images or choose another model.",
    );
    expect(getErrorMessage("too_many_attachments")).toBe(
      "Too many images attached. Please use at most 4 images per message.",
    );
  });

  // Contract audit: proxy-gate + generic-fallback codes now have friendly copy
  // (previously hit the generic fallback). Cross-checked against backend source:
  // proxy_health.py, routers/settings.py, routers/characters.py, openrouter.py,
  // routers/models_router.py, routers/completions.py.
  it("maps newly added backend codes to their specific messages", () => {
    // A2 - proxy gate reasons (503) + probe reasons
    expect(getErrorMessage("proxy_unhealthy")).toBe(
      "The configured proxy is not responding. Please check your proxy configuration.",
    );
    // Reworded 2026-08-19. It used to be byte-identical to openrouter_timeout,
    // which hid what this code actually is: proxy_health.py raises it as a 503
    // detail when the PROXY probe times out, so the sentence names the proxy.
    expect(getErrorMessage("timeout")).toBe(
      "The proxy did not answer in time, so nothing was sent. Check that your proxy is running and reachable, then try again.",
    );
    expect(getErrorMessage("timeout")).not.toBe(
      getErrorMessage("openrouter_timeout"),
    );
    // A3 - proxy URL validation (settings.py, 400)
    expect(getErrorMessage("proxy_url_required")).toBe(
      "A proxy URL is required. Please enter one in Settings.",
    );
    expect(getErrorMessage("invalid_proxy_scheme")).toBe(
      "The proxy URL scheme is not supported. Use http, https, socks5, or socks5h.",
    );
    expect(getErrorMessage("proxy_url_invalid")).toBe(
      "The proxy URL is not valid. Please check it and try again.",
    );
    // A3 - OpenRouter models listing (openrouter.py, models_router.py)
    expect(getErrorMessage("api_key_required_by_openrouter")).toBe(
      "The provider requires an API key. Please add your OpenRouter API key in Settings.",
    );
    expect(getErrorMessage("invalid_openrouter_models_response")).toBe(
      "Received an unexpected response while loading models. Please try again.",
    );
    expect(getErrorMessage("openrouter_models_error")).toBe(
      "Could not load models. Please try again.",
    );
    // A3 - character import (characters.py, 400)
    expect(getErrorMessage("character_json_too_large")).toBe(
      "This character file is too large. Please use a smaller file.",
    );
    expect(getErrorMessage("invalid_character_json")).toBe(
      "This character file is not valid JSON. Please check the file and try again.",
    );
    expect(getErrorMessage("character_name_required")).toBe(
      "This character needs a name. Please add one and try again.",
    );
  });

  // A hand-listed "recognizes newly added codes as known (not generic
  // fallback)" test lived here and checked eleven codes for exactly two
  // things: known, and not the fallback. Deleted in KADEME 16b.
  // errorCatalogue.test.ts runs both checks over all 105 catalogued codes, and
  // all eleven are in the catalogue, so the list could only fail alongside it
  // while quietly needing hand-maintenance forever. The eleven names are
  // recorded in that file.
  //
  // The test above it, "maps newly added backend codes to their specific
  // messages", is NOT the same thing and stays: it pins exact wording, and a
  // wrong-but-specific sentence passes the catalogue check and fails that one.
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
    // Pinned to the map rather than to a retyped copy of its sentence: what
    // this test is actually guarding is that the raw Pydantic detail above
    // never reaches the reader, and re-typing the wording made an unrelated
    // rewording fail here instead of where it belonged.
    expect(result.message).toBe(getErrorMessage("invalid_generation_params"));
    expect(result.message).not.toContain("temperature");
    expect(result.message).not.toContain("must be >=");
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
    // Distinct code+message pairs - identical events are deduped (see below)
    for (let i = 0; i < 8; i++) {
      useErrorStore.getState().pushErrorDirect(`code_${i}`, `Message ${i}`);
    }
    const errors = useErrorStore.getState().errors;
    const queuedErrors = useErrorStore.getState().queuedErrors;
    expect(errors).toHaveLength(5);
    expect(queuedErrors).toHaveLength(3);
    expect(errors[4].code).toBe("code_4");
    expect(queuedErrors[2].code).toBe("code_7");
  });

  it("skips a push when an identical code+message toast is visible", () => {
    useErrorStore.getState().pushError(new TypeError("fail A"));
    useErrorStore.getState().pushError(new TypeError("fail B")); // same code+message after mapping
    const state = useErrorStore.getState();
    expect(state.errors).toHaveLength(1);
    expect(state.errors[0].code).toBe("network_error");
    expect(state.queuedErrors).toHaveLength(0);
  });

  it("dedupes on the code, not on the sentence it renders", () => {
    // REWRITTEN. This used to assert the opposite, and the opposite is what
    // K-23 is about: the key was code+message, and every count-bearing code
    // builds a different sentence every time - `images_omitted` says "one
    // image" then "three images" - so the one rule that was supposed to stop
    // a repeat never applied to the codes that repeat most.
    //
    // The sentence is a rendering of the event, not its identity.
    useErrorStore.getState().pushErrorDirect("same_code", "Message one");
    useErrorStore.getState().pushErrorDirect("same_code", "Message two");
    expect(useErrorStore.getState().errors).toHaveLength(1);
  });

  it("keeps two conversations apart even when they fail identically", () => {
    // K-21. Every sentence in the catalogue is static text, so two chats
    // failing the same way produced byte-identical events and the second was
    // dropped. The one the user was not looking at is the one they never
    // heard about.
    useErrorStore
      .getState()
      .pushErrorDirect("network_error", "Could not reach the server.",
                       "error", { chatId: 4 });
    useErrorStore
      .getState()
      .pushErrorDirect("network_error", "Could not reach the server.",
                       "error", { chatId: 9 });

    const errors = useErrorStore.getState().errors;
    expect(errors).toHaveLength(2);
    expect(errors.map((e) => e.chatId)).toEqual([4, 9]);
  });

  it("still collapses the same failure in the same conversation", () => {
    // The discriminating half. A key that never matches is not a dedupe rule,
    // it is a stack of duplicates.
    for (let attempt = 0; attempt < 3; attempt += 1) {
      useErrorStore
        .getState()
        .pushErrorDirect("network_error", "Could not reach the server.",
                         "error", { chatId: 4 });
    }
    expect(useErrorStore.getState().errors).toHaveLength(1);
  });

  it("does not let a chatless error match one that has a chat", () => {
    // Undefined is a value, not a wildcard. Settings, the voice engine and
    // the player have no chat at all, and folding them into whichever
    // conversation failed most recently would be a worse attribution than
    // none.
    useErrorStore
      .getState()
      .pushErrorDirect("network_error", "Could not reach the server.");
    useErrorStore
      .getState()
      .pushErrorDirect("network_error", "Could not reach the server.",
                       "error", { chatId: 4 });
    expect(useErrorStore.getState().errors).toHaveLength(2);
  });

  it("tells apart a failure that kept your half reply from one that did not", () => {
    // K-26. The backend has always sent partial_saved and this app has always
    // parsed it, and then it died at makeApiError. Two different things
    // happened to the user's words; they are not one event.
    useErrorStore
      .getState()
      .pushErrorDirect("openrouter_timeout", "The request timed out.",
                       "error", { chatId: 4, partialSaved: true });
    useErrorStore
      .getState()
      .pushErrorDirect("openrouter_timeout", "The request timed out.",
                       "error", { chatId: 4, partialSaved: false });
    expect(useErrorStore.getState().errors).toHaveLength(2);
  });

  it("keeps two different voice notices raised by the same reply", () => {
    // The other half of K-23, and where its rule was exactly backwards.
    // `tts_notice` is the one code whose sentence is not a rendering of the
    // code: the engine can report warming up AND a reply that did not fit in
    // the same turn, and an identity built from the code alone called those
    // one event and dropped the second without a trace.
    useErrorStore
      .getState()
      .pushErrorDirect("tts_notice", "The voice engine is preparing itself.",
                       "warning");
    useErrorStore
      .getState()
      .pushErrorDirect("tts_notice", "The last part was not spoken.",
                       "warning");

    const errors = useErrorStore.getState().errors;
    expect(errors).toHaveLength(2);
    expect(errors.map((e) => e.message)).toEqual([
      "The voice engine is preparing itself.",
      "The last part was not spoken.",
    ]);
  });

  it("GROUND: the same voice notice twice is still one toast", () => {
    // A rule that never collapses anything is not a dedupe. Two diagnostics
    // that reduce to the same sentence are one thing to be told.
    useErrorStore
      .getState()
      .pushErrorDirect("tts_notice", "Speech will be slower.", "warning");
    useErrorStore
      .getState()
      .pushErrorDirect("tts_notice", "Speech will be slower.", "warning");

    expect(useErrorStore.getState().errors).toHaveLength(1);
  });

  it("GROUND: a counted code is still deduped by its code alone", () => {
    // The incident K-23 records, re-run. `images_omitted` builds its sentence
    // around a live count, so a key that reads the message stops deduping the
    // codes that repeat most. Adding tts_notice must not buy that back.
    useErrorStore
      .getState()
      .pushErrorDirect("images_omitted", "One image could not be sent.",
                       "warning", { chatId: 4 });
    useErrorStore
      .getState()
      .pushErrorDirect("images_omitted", "3 images could not be sent.",
                       "warning", { chatId: 4 });

    expect(useErrorStore.getState().errors).toHaveLength(1);
  });

  it("sees the queue when it dedupes, so a waiting error does not double", () => {
    // REWRITTEN, was characterisation for K-23. The check read the five
    // VISIBLE toasts and never the queue, so the guarantee "the same thing is
    // not shown twice" quietly stopped applying the moment the stack filled -
    // and the duplicate was then promoted into view like any other event.
    for (let i = 0; i < 5; i++) {
      useErrorStore.getState().pushErrorDirect(`code_${i}`, `Message ${i}`);
    }
    useErrorStore.getState().pushErrorDirect("waiting", "Same sentence");
    useErrorStore.getState().pushErrorDirect("waiting", "Same sentence");

    const queued = useErrorStore.getState().queuedErrors;
    expect(queued).toHaveLength(1);
    expect(queued.map((e) => e.code)).toEqual(["waiting"]);
  });

  it("and the queue drains without showing it twice", () => {
    for (let i = 0; i < 5; i++) {
      useErrorStore.getState().pushErrorDirect(`code_${i}`, `Message ${i}`);
    }
    useErrorStore.getState().pushErrorDirect("waiting", "Same sentence");
    useErrorStore.getState().pushErrorDirect("waiting", "Same sentence");

    const state = useErrorStore.getState();
    state.dismiss(state.errors[0].id);
    useErrorStore.getState().dismiss(useErrorStore.getState().errors[0].id);

    const visible = useErrorStore.getState().errors;
    expect(visible.filter((e) => e.code === "waiting")).toHaveLength(1);
  });

  it("allows the same error again after the visible copy is dismissed", () => {
    useErrorStore.getState().pushError(new TypeError("fail"));
    const id = useErrorStore.getState().errors[0].id;
    useErrorStore.getState().dismiss(id);
    useErrorStore.getState().pushError(new TypeError("fail"));
    expect(useErrorStore.getState().errors).toHaveLength(1);
  });

  it("caps the queue at 20 and drops the oldest queued events", () => {
    // Fill 5 visible + 25 queued candidates (all distinct)
    for (let i = 0; i < 30; i++) {
      useErrorStore.getState().pushErrorDirect(`code_${i}`, `Message ${i}`);
    }
    const state = useErrorStore.getState();
    expect(state.errors).toHaveLength(5);
    expect(state.queuedErrors).toHaveLength(20);
    // codes 5..9 were dropped (oldest queued); the queue starts at code_10
    expect(state.queuedErrors[0].code).toBe("code_10");
    expect(state.queuedErrors[19].code).toBe("code_29");
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

  it("promotes queued errors oldest first", () => {
    // With exactly ONE item waiting, first-in and last-in look identical, and
    // one waiting item is all the existing promotion tests ever set up. With
    // two, an order bug means the failure reported FIRST can be starved while
    // newer ones keep jumping the line, which is the opposite of what a queue
    // is for.
    for (let i = 0; i < 7; i++) {
      useErrorStore.getState().pushErrorDirect(`code_${i}`, `Message ${i}`);
    }
    expect(useErrorStore.getState().queuedErrors).toHaveLength(2);

    useErrorStore.getState().dismiss(useErrorStore.getState().errors[0].id);
    expect(useErrorStore.getState().errors[4].message).toBe("Message 5");

    useErrorStore.getState().dismiss(useErrorStore.getState().errors[0].id);
    expect(useErrorStore.getState().errors[4].message).toBe("Message 6");
  });

  it("clearAll empties the store", () => {
    useErrorStore.getState().pushErrorDirect("code_a", "Message A");
    useErrorStore.getState().pushErrorDirect("code_b", "Message B");
    expect(useErrorStore.getState().errors).toHaveLength(2);
    useErrorStore.getState().clearAll();
    expect(useErrorStore.getState().errors).toHaveLength(0);
    expect(useErrorStore.getState().queuedErrors).toHaveLength(0);
  });

  it("each error has a unique id", () => {
    useErrorStore.getState().pushErrorDirect("code_a", "Message A");
    useErrorStore.getState().pushErrorDirect("code_b", "Message B");
    const [e1, e2] = useErrorStore.getState().errors;
    expect(e1.id).not.toBe(e2.id);
  });
});

describe("two buttons that fail the same way", () => {
  /**
   * The four vault discards push the same codes with no chat id, so their
   * dedupe identity was the CODE alone. The vault auto-locks with Settings
   * open, two notices are on screen, and pressing Remove on both inside one
   * toast's window produced ONE toast: the second button spun, stopped, and
   * reported nothing. The `role="alert"` paragraphs in those notices read
   * fields of a SUCCESSFUL response, so they say nothing on a failure
   * either. Silence, in the panel the `onError` hooks were added to stop
   * being silent.
   */
  beforeEach(() => {
    useErrorStore.setState({ errors: [], queuedErrors: [] });
  });

  const failure = (status: number, detail: string): ApiError => ({
    status,
    detail,
    message: `Request failed: ${status}`,
  });

  it("answers both of them", () => {
    const push = useErrorStore.getState().pushError;
    push(failure(423, "vault_locked"), "error", {
      source: "vault:discard-orphaned#1",
    });
    push(failure(423, "vault_locked"), "error", {
      source: "vault:discard-premigrate#1",
    });
    expect(useErrorStore.getState().errors).toHaveLength(2);
  });

  it("still answers a retry of the SAME button", () => {
    // Every one of these is one deliberate press. A retry after a "please
    // try again" toast is a new event, and dropping it makes the retry
    // indistinguishable from a success.
    const push = useErrorStore.getState().pushError;
    push(failure(500, "unknown_error"), "error", {
      source: "vault:discard-plaintext#1",
    });
    push(failure(500, "unknown_error"), "error", {
      source: "vault:discard-plaintext#2",
    });
    expect(useErrorStore.getState().errors).toHaveLength(2);
  });

  it("still collapses one failure reported twice", () => {
    // GROUND CONTROL, and it is the whole reason the dedupe exists. If
    // `source` simply defeated it, K-23's incident would be back: a
    // duplicate raised while five toasts are up going into the queue and
    // being shown again as the queue drains.
    const push = useErrorStore.getState().pushError;
    push(failure(500, "unknown_error"), "error", {
      source: "vault:discard-plaintext#1",
    });
    push(failure(500, "unknown_error"), "error", {
      source: "vault:discard-plaintext#1",
    });
    expect(useErrorStore.getState().errors).toHaveLength(1);
  });

  it("still collapses two pushes with no source at all", () => {
    // POSITIVE CONTROL for every existing caller. `source` is absent at
    // ~every push site in the app, and undefined must keep behaving exactly
    // as it did - otherwise this change quietly turns the dedupe off
    // everywhere it was already working.
    const push = useErrorStore.getState().pushError;
    push(failure(500, "unknown_error"));
    push(failure(500, "unknown_error"));
    expect(useErrorStore.getState().errors).toHaveLength(1);
  });
});
