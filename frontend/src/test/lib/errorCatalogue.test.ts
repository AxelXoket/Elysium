/**
 * Direction (b) of the three-way error gate: every catalogued code has a
 * sentence, and every sentence has a catalogued code.
 *
 * The other two directions live in backend/tests/test_error_catalogue.py,
 * because that is where the emitting code is. This half is here because this
 * is where the English is, and the English deliberately stays here rather than
 * moving into the JSON.
 *
 * That was the one real disagreement in the design and it is worth recording.
 * Generating `errorMessages.ts` from the catalogue would make "every record has
 * a sentence" true by construction, which is a green tautology rather than a
 * check. It would also destroy the 56 lines of comment in that file explaining
 * why one code is not its neighbour - why `openrouter_moderation_blocked` is a
 * 403 and not a 401, why `tts_out_of_memory` is not `tts_insufficient_vram` -
 * and JSON cannot hold prose.
 *
 * What this file replaces: `ErrorHandling.test.ts` asserted completeness
 * against two hand-typed arrays of 56 and 31 codes. A code added to the
 * backend and not to those arrays passed, silently, rendering the generic
 * fallback to the user. `errorMappingG10.test.ts` exists only because that
 * happened to four codes at once. The arrays are gone; the source of truth is
 * now the same file the backend's own gate is measured against.
 */
import { describe, it, expect } from "vitest";

import catalogue from "../../../../shared/error_catalogue.json";
import {
  getErrorMessage,
  isKnownErrorCode,
  knownErrorCodes,
} from "@/lib/errors/errorMessages";

const CODES: string[] = catalogue.codes.map((r: { code: string }) => r.code);

/**
 * The one code whose sentence IS the fallback, on purpose.
 *
 * Pinned as a literal rather than dropping the assertion, because the
 * assertion is the whole "did this ship generic" detector and one visible
 * exception in a diff is cheaper than losing it.
 */
const FALLBACK = "Something went wrong. Please try again.";
const MAPS_TO_THE_FALLBACK_ON_PURPOSE = new Set(["unknown_error"]);

describe("the error catalogue", () => {
  it("is present and not empty", () => {
    // The guard that stops every assertion below from passing vacuously if the
    // file is deleted, emptied, or fails to resolve. The backend half carries
    // the same floor for the same reason.
    expect(CODES.length).toBeGreaterThanOrEqual(100);
  });

  it("has no duplicate records", () => {
    expect(new Set(CODES).size).toBe(CODES.length);
  });

  it("gives every catalogued code a sentence", () => {
    // KADEME 16b folded two hand-kept lists into this pair of checks, and
    // their history belongs here now. errorMappingG10.test.ts named four codes
    // reachable from routers/vault.py that were absent from the map:
    // passphrase_too_long, vault_already_initialized, vault_not_initialized,
    // each sitting two lines below a sibling that WAS mapped correctly, plus
    // cross_origin_denied, which was listed in the contract table and missing
    // from the map, so the invariant was wrong in both directions at once.
    // ErrorHandling.test.ts named eleven more (proxy_unhealthy, timeout,
    // proxy_url_required, invalid_proxy_scheme, proxy_url_invalid,
    // api_key_required_by_openrouter, invalid_openrouter_models_response,
    // openrouter_models_error, character_json_too_large,
    // invalid_character_json, character_name_required). All fifteen are in the
    // catalogue, so all fifteen are covered by iterating it. A hand-kept list
    // is exactly the thing that goes stale while looking like coverage.
    const missing = CODES.filter((c) => !isKnownErrorCode(c));
    expect(missing, `no sentence in errorMessages.ts for: ${missing.join(", ")}`)
      .toEqual([]);
  });

  it("gives every catalogued code a REAL sentence, not the fallback", () => {
    // The check that would have caught the three notice codes years earlier.
    // image_output_remote_url_refused reports this app refusing to make a
    // second network request - the promise the whole design rests on - and it
    // reached the reader as "Something went wrong. Please try again."
    const generic = CODES.filter(
      (c) => !MAPS_TO_THE_FALLBACK_ON_PURPOSE.has(c)
        && getErrorMessage(c) === FALLBACK,
    );
    expect(generic, `these render the generic fallback: ${generic.join(", ")}`)
      .toEqual([]);
  });

  it("has no sentence for a code nobody produces", () => {
    // This direction existed nowhere in the repo before, and it is the one
    // that catches a rename: the old key keeps its sentence, the new key has
    // none, and every other check stays green.
    const catalogued = new Set(CODES);
    const orphans = knownErrorCodes().filter((k) => !catalogued.has(k));
    expect(orphans, `in errorMessages.ts, not in the catalogue: ${orphans.join(", ")}`)
      .toEqual([]);
  });

  it("has no sentence that could tell a reader their half reply was kept", () => {
    // CHARACTERISATION, not approval. See KUSUR-DEFTERI K-26.
    //
    // When a provider drops mid-reply the backend sometimes keeps the partial
    // and sometimes throws it away, and the frontend knows which: partial_saved
    // picks failSendKeepingPartial over failSend. But the error handed to the
    // reader is built by makeApiError(status, code), which carries only those
    // two fields, so both branches end at the same code and therefore the same
    // sentence. The reader is left looking at half a reply with no way to know
    // whether it is saved or a ghost that vanishes on reload, and those two
    // situations call for opposite actions: resending after a KEPT partial
    // appends a duplicate exchange.
    //
    // The house style already answers "was it saved?" in three other places:
    // image_output_rejected says "Nothing was saved", edit_conflict says "Your
    // edit was not saved", tts_runtime_install_failed says "Nothing was left
    // half-installed". The one place the answer decides what the reader should
    // do next is the one that stays quiet.
    //
    // This pins the absence over the provider failures, which are the codes
    // that can arrive after text has already started arriving. The day one of
    // them can say it, this goes red and the ledger entry closes.
    const providerFailures = CODES.filter((c) => c.startsWith("openrouter_"));
    expect(
      providerFailures.length,
      "no provider failure codes found, so this test is measuring nothing",
    ).toBeGreaterThan(3);

    const speaksToIt = providerFailures.filter((c) =>
      /\b(partial|half|kept|saved|discarded)\b/i.test(getErrorMessage(c)));
    expect(
      speaksToIt,
      `these now mention it, so the distinction may be sayable: ${speaksToIt.join(", ")}`,
    ).toEqual([]);
  });

  it("keeps an escape hatch honest by making it say why", () => {
    // Three records today, all three codes the frontend synthesises itself.
    // A boolean flag would be one keystroke; a sentence is a line a reviewer
    // has to read and agree with.
    const hatched = catalogue.codes.filter(
      (r: { no_backend_producer?: string }) => r.no_backend_producer !== undefined,
    );
    expect(hatched.length).toBeLessThanOrEqual(10);
    for (const rec of hatched) {
      expect(rec.no_backend_producer!.length).toBeGreaterThan(40);
    }
  });

  it("marks a code with no backend producer as reaching no backend channel", () => {
    // Consistency between the two fields: something the backend never sends
    // cannot arrive on one of the backend's exits. Cheap, and it catches a
    // half-finished record that copied its neighbour.
    for (const rec of catalogue.codes as Array<{
      code: string; channels: string[]; no_backend_producer?: string;
    }>) {
      if (rec.no_backend_producer !== undefined) {
        expect(rec.channels, `${rec.code} claims a backend channel`).toEqual([]);
      } else {
        expect(rec.channels.length, `${rec.code} reaches no channel`)
          .toBeGreaterThan(0);
      }
    }
  });
});
