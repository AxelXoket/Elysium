/**
 * POST /activity - "a person did something", sent by the activity tracker.
 *
 * The backend records the activity in its vault gate before the handler even
 * runs, so the route carries nothing and answers nothing: 204, no body. While
 * the vault is locked the same gate answers 423 before recording anything,
 * which is why the tracker is switched off on the lock screen and why a 423
 * that still arrives is left to client.ts's ordinary lock signal.
 */
import { z } from "zod/v4";

import { request } from "./client";

/** The route answers 204 with no body. client.ts parses a 204 as `undefined`
 *  through the caller's schema, so this declares that expectation: a body
 *  appearing here one day is a shape error rather than something silently
 *  ignored. */
export const ActivityPingSchema = z.undefined();

export async function postActivity(): Promise<void> {
  await request("/activity", ActivityPingSchema, { method: "POST" });
}
