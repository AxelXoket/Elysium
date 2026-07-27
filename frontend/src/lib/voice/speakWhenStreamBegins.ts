/**
 * speakWhenStreamBegins.ts - the S16 half of the continuous-voice rule.
 *
 * S15 and S16 are a matched pair, and only S15 had landed:
 *
 *   S15  Turning "speak replies aloud" on MID-reply must not go back and
 *        read what the user has already read.
 *   S16  But turning it on after sending and BEFORE the reply starts
 *        arriving should speak that reply, as soon as it starts.
 *
 * The request carries `speak` and is built at send time, so by the time the
 * toggle is flipped the server has already been told "no". What it also has,
 * on every stream, is a dormant speaker waiting to be woken - the same one
 * the per-message Speak button uses. Waking it is the whole of S16.
 *
 * The retry exists because the two sides register at different moments: the
 * client registers its stream when it calls fetch, the server when its
 * generator starts (after the DB reads and the proxy gate). Flipping the
 * toggle in that gap is exactly the case this function is for, so answering
 * "nothing is streaming" once is not an answer - it is a race.
 */

import { speakLive } from "../api/tts";
import { isAwaitingFirstDelta } from "../chat/streamRegistry";

/** How long to keep trying while the server catches up. */
const ATTEMPTS = 6;
const RETRY_MS = 250;

function isNotStreamingYet(err: unknown): boolean {
  return (
    typeof err === "object" &&
    err !== null &&
    (err as { detail?: unknown }).detail === "tts_nothing_streaming"
  );
}

const sleep = (ms: number) =>
  new Promise<void>((resolve) => setTimeout(resolve, ms));

/**
 * Wake the dormant speaker of the reply this chat is waiting for.
 *
 * Returns true if it started speaking. Deliberately SILENT on failure: the
 * toggle's own promise is "from your next message onwards", so this is a
 * bonus the user was never told about, and a toast explaining that a bonus
 * did not happen would be worse than the bonus not happening.
 */
export async function speakWhenStreamBegins(chatId: number): Promise<boolean> {
  for (let attempt = 0; attempt < ATTEMPTS; attempt += 1) {
    // Re-checked EVERY pass, not just once: if the first delta lands while we
    // are waiting for the server, the window has closed and speaking now
    // would read text the user has already seen. That is S15, and S15 wins.
    if (!isAwaitingFirstDelta(chatId)) return false;
    try {
      await speakLive(chatId);
      return true;
    } catch (err) {
      if (!isNotStreamingYet(err)) return false;
      await sleep(RETRY_MS);
    }
  }
  return false;
}
