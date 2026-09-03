/**
 * The 422 the backend actually sends, read back by all three HTTP transports.
 *
 * The producer is pinned in backend/tests: one app-level
 * RequestValidationError handler answers every rejected body in the tree with
 * `{"detail": "validation_failed", "errors": [...]}`. Nothing on this side was
 * pinned to it. The only 422 transport test in the frontend suite drove the
 * shape that handler REPLACED - a bare FastAPI array under `detail` - so the
 * body a real 422 carries today was read by no test at all, and the reader
 * would have found out about a regression before any assertion did.
 *
 * Both shapes are here on purpose, because both are still live:
 *
 *   * The STRING shape is what our own handler sends. It has to pass through
 *     to the message map unchanged, or a rejected field renders as the
 *     generic "Something went wrong".
 *   * The ARRAY shape is what a 422 that did NOT come from that handler looks
 *     like - FastAPI's default, reachable if the handler is ever unregistered
 *     or bypassed. The three array guards collapse it to a code that has a
 *     sentence. They are defence, not dead code, so they are proven here
 *     rather than assumed.
 *
 * All three transports are exercised against the same two bodies because they
 * are three hand-written copies of one decision. uploads.ts carried the
 * comment saying it was in sync with client.ts while missing the array branch
 * entirely, which is exactly the drift a single-transport test cannot see.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { z } from "zod/v4";

import catalogue from "../../../../shared/error_catalogue.json";
import { API_BASE } from "@/lib/api/base";
import { request, rawRequest, isApiError } from "@/lib/api/client";
import { streamCompletion } from "@/lib/api/stream";
import { uploadImage } from "@/lib/api/uploads";
import { parseApiError } from "@/lib/errors/parseApiError";
import { getErrorMessage } from "@/lib/errors/errorMessages";
import { jsonResponse } from "../helpers/streamMocks";

/**
 * Codes come from the catalogue both sides are measured against, not from a
 * string typed here. A code that leaves the catalogue takes this file red at
 * the lookup, naming the rename, instead of quietly testing a wire value the
 * backend stopped sending.
 */
function catalogued(code: string): string {
  const record = catalogue.codes.find(
    (r: { code: string }) => r.code === code,
  );
  if (!record) throw new Error(`no catalogue record for "${code}"`);
  return record.code;
}

/** What main.py's RequestValidationError handler puts in `detail`. */
const VALIDATION_FAILED = catalogued("validation_failed");
/** What the three array guards collapse a non-handler 422 to. */
const FROM_THE_ARRAY_GUARD = catalogued("invalid_generation_params");

/**
 * Sentences are imported, never retyped: this is the same lookup the toast
 * stack performs, so a copy-edit in the map moves the app and this file
 * together instead of turning one of them red.
 */
const VALIDATION_FAILED_MESSAGE = getErrorMessage(VALIDATION_FAILED);
const ARRAY_GUARD_MESSAGE = getErrorMessage(FROM_THE_ARRAY_GUARD);

/** The sentence an unmapped code renders as. Pinned so "did this reach the
 *  reader as a real sentence" is a question with an answer. */
const FALLBACK_MESSAGE = getErrorMessage("a-code-no-map-will-ever-have");

/** The body our handler sends: a code string, and the rejected fields in a
 *  field of their own. */
const HANDLER_BODY = {
  detail: VALIDATION_FAILED,
  errors: [
    { type: "greater_than", loc: ["body", "temperature"], msg: "too big" },
  ],
};

/** The body FastAPI sends when our handler is not the one answering. */
const BARE_FASTAPI_BODY = {
  detail: [
    { type: "greater_than", loc: ["body", "temperature"], msg: "too big" },
  ],
};

/** The token that must never reach a person: a field name lifted out of the
 *  rejected request. */
const LEAKY_TOKEN = "temperature";

/** Declared with fetch's own parameters even though the body ignores them:
 *  that is what lets the URL assertion below read `mock.mock.calls[0][0]`
 *  instead of indexing an empty tuple. */
type FetchStub = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;

function stubFetch(body: unknown) {
  const impl: FetchStub = async () => jsonResponse(body, 422);
  const mock = vi.fn(impl);
  vi.stubGlobal("fetch", mock);
  return mock;
}

/** Run `fn`, hand back whatever it threw. Never returns undefined silently:
 *  every caller asserts `isApiError` on the result, which fails loudly if
 *  nothing threw at all. */
async function thrownBy(fn: () => Promise<unknown>): Promise<unknown> {
  try {
    await fn();
  } catch (err) {
    return err;
  }
  return undefined;
}

const AnyObject = z.object({}).loose();

function pngFile(): File {
  return new File([new Uint8Array([137, 80, 78, 71])], "photo.png", {
    type: "image/png",
  });
}

/** The four doors, each one a real exported entry point rather than a shared
 *  private helper, so a fix applied to one of them cannot green the rest. */
const TRANSPORTS: {
  name: string;
  urlPart: string;
  send: () => Promise<unknown>;
}[] = [
  {
    name: "client.request",
    urlPart: "/chats",
    send: () => request("/chats", AnyObject, { method: "POST", body: "{}" }),
  },
  {
    name: "client.rawRequest",
    urlPart: "/characters/import",
    send: () =>
      rawRequest("/characters/import", AnyObject, {
        method: "POST",
        body: "{}",
      }),
  },
  {
    name: "stream.streamCompletion",
    urlPart: "/chats/1/complete/stream",
    send: () =>
      streamCompletion("/chats/1/complete/stream", {}, { onEvent: () => {} }),
  },
  {
    name: "uploads.uploadImage",
    urlPart: "/uploads/images",
    send: () => uploadImage(pngFile()),
  },
];

describe("a 422 from the app-level validation handler", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("the catalogue really carries this code at this status", () => {
    // Ground control for every case below. Without it a renamed or deleted
    // record would take `catalogued` down at import time, but a record that
    // quietly lost 422 would leave the whole file green while testing a
    // status the backend never pairs with the code.
    const record = catalogue.codes.find(
      (r: { code: string; statuses: number[] }) => r.code === VALIDATION_FAILED,
    );
    expect(record?.statuses).toContain(422);
  });

  for (const transport of TRANSPORTS) {
    it(`${transport.name} passes the code through to its sentence`, async () => {
      const mock = stubFetch(HANDLER_BODY);

      const thrown = await thrownBy(transport.send);

      // Ground control: the request went out, at the expected door. Without
      // this the assertions below would also pass for a transport that threw
      // before it ever reached fetch.
      expect(mock).toHaveBeenCalledTimes(1);
      expect(String(mock.mock.calls[0][0])).toContain(
        `${API_BASE}${transport.urlPart}`,
      );

      expect(isApiError(thrown)).toBe(true);
      expect(thrown).toMatchObject({
        status: 422,
        detail: VALIDATION_FAILED,
      });
      // What the reader is actually shown. client.ts stops at a status line
      // and the map lookup happens later, so the sentence is asked for the
      // way the app asks for it: through parseApiError.
      expect(parseApiError(thrown).message).toBe(VALIDATION_FAILED_MESSAGE);
      // The whole point of the code surviving the trip: a generic shrug here
      // is the failure this file exists to catch.
      expect(parseApiError(thrown).message).not.toBe(FALLBACK_MESSAGE);
    });

    it(`${transport.name} keeps the rejected field names out of the error`, async () => {
      const mock = stubFetch(HANDLER_BODY);

      const thrown = await thrownBy(transport.send);

      expect(mock).toHaveBeenCalledTimes(1);
      expect(isApiError(thrown)).toBe(true);
      // `errors` is for a caller that wants it, not for the reader. Nothing
      // from that array may ride out inside the normalized error.
      const carried = JSON.stringify(parseApiError(thrown));
      expect(carried).not.toContain(LEAKY_TOKEN);
      // Positive control: the same sweep over the body the server sent DOES
      // find the token, so a green absence above means it was dropped rather
      // than that this search went blind.
      expect(JSON.stringify(HANDLER_BODY)).toContain(LEAKY_TOKEN);
    });

    it(`${transport.name} still collapses a bare FastAPI array`, async () => {
      const mock = stubFetch(BARE_FASTAPI_BODY);

      const thrown = await thrownBy(transport.send);

      expect(mock).toHaveBeenCalledTimes(1);
      expect(isApiError(thrown)).toBe(true);
      expect(thrown).toMatchObject({
        status: 422,
        detail: FROM_THE_ARRAY_GUARD,
      });
      expect(parseApiError(thrown).message).toBe(ARRAY_GUARD_MESSAGE);
      expect(parseApiError(thrown).message).not.toBe(FALLBACK_MESSAGE);
      // The array is raw upstream shape; none of it may survive the guard.
      expect(JSON.stringify(parseApiError(thrown))).not.toContain(LEAKY_TOKEN);
      expect(JSON.stringify(BARE_FASTAPI_BODY)).toContain(LEAKY_TOKEN);
    });
  }

  it("the two shapes do not answer with the same code", async () => {
    // Discriminator. Every case above would stay green if some transport
    // answered every 422 with one code, which is what uploads.ts did before
    // it grew the array branch: the string passed through and the array fell
    // to the fallback, so the two shapes differed but neither was right.
    expect(VALIDATION_FAILED).not.toBe(FROM_THE_ARRAY_GUARD);
    expect(VALIDATION_FAILED_MESSAGE).not.toBe(ARRAY_GUARD_MESSAGE);

    const seen = new Map<string, string[]>();
    for (const transport of TRANSPORTS) {
      const codes: string[] = [];
      for (const body of [HANDLER_BODY, BARE_FASTAPI_BODY]) {
        stubFetch(body);
        const thrown = await thrownBy(transport.send);
        codes.push(parseApiError(thrown).detail);
        vi.unstubAllGlobals();
      }
      seen.set(transport.name, codes);
    }

    // Read as a table so a failure names the transport that drifted.
    expect(Object.fromEntries(seen)).toEqual({
      "client.request": [VALIDATION_FAILED, FROM_THE_ARRAY_GUARD],
      "client.rawRequest": [VALIDATION_FAILED, FROM_THE_ARRAY_GUARD],
      "stream.streamCompletion": [VALIDATION_FAILED, FROM_THE_ARRAY_GUARD],
      "uploads.uploadImage": [VALIDATION_FAILED, FROM_THE_ARRAY_GUARD],
    });
  });

  it("a 422 with no usable detail is not silently called a validation failure", async () => {
    // The other half of the discriminator: the transports must not reach for
    // a 422 code just because the status is 422. An empty body is the shape
    // an upstream proxy or a crash produces, and it has no field to report.
    for (const transport of TRANSPORTS) {
      stubFetch({});
      const thrown = await thrownBy(transport.send);
      expect(isApiError(thrown)).toBe(true);
      expect(parseApiError(thrown).detail).toBe("unknown_error");
      vi.unstubAllGlobals();
    }
  });
});
