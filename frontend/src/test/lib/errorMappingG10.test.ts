/**
 * errorMappingG10.test.ts - KÖK 14, the sentences that sent people the wrong way.
 *
 * Every case here reached the user as a real sentence; they were just about a
 * different failure than the one that happened. The worst of them, "No audio
 * output device is available to play the voice", was produced for a backend
 * restart - so the fix people went looking for was a sound-card driver.
 */
import { describe, it, expect, afterEach, vi } from "vitest";

import { getErrorMessage, isKnownErrorCode } from "@/lib/errors/errorMessages";
import { VoiceChunkError, voiceErrorCode } from "@/lib/voice/streamPlayer";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("the audio player reports what actually failed", () => {
  it("repeats a code the backend gave it", () => {
    expect(voiceErrorCode(new VoiceChunkError("tts_audio_expired", 404))).toBe(
      "tts_audio_expired",
    );
  });

  it("keeps tts_audio_device_error for a genuine device failure", () => {
    // Anything that is not a chunk fetch IS the context or the decoder: those
    // are the only other things that can throw in here.
    expect(voiceErrorCode(new Error("AudioContext could not start"))).toBe(
      "tts_audio_device_error",
    );
  });

  it("does not blame the sound card for a backend restart", () => {
    // This used to open with `const { VoiceStreamPlayer } = await import(...)`
    // followed by `void VoiceStreamPlayer`, which imported the player class and
    // then threw it away. Nothing of the player ran; the name only made the
    // test LOOK like it covered the real fetch path. What it actually proves,
    // and still proves, is the headline of KOK 14: the sentence the reader gets
    // for an expired chunk must not send them after a sound card.
    const err = new VoiceChunkError("tts_audio_expired", 404);
    expect(voiceErrorCode(err)).not.toBe("tts_audio_device_error");
    expect(getErrorMessage(voiceErrorCode(err))).not.toMatch(/output device/i);
  });
});

describe("the codes that fell through to the fallback", () => {
  const FALLBACK = "Something went wrong. Please try again.";

  // Four codes used to be listed here by hand and checked for "known, and not
  // the fallback": passphrase_too_long, vault_already_initialized,
  // vault_not_initialized, cross_origin_denied. Deleted in KADEME 16a/16b.
  // errorCatalogue.test.ts now runs exactly those two checks over all 105
  // catalogued codes, so this list could only ever fail in the company of that
  // one, and a hand-kept list of four is the shape that goes stale. Their
  // history lives there now.

  it("tts_audio_expired offers the thing that actually helps", () => {
    expect(getErrorMessage("tts_audio_expired")).toMatch(/again/i);
    expect(getErrorMessage("tts_audio_expired")).not.toMatch(/could not be generated/i);
  });

  it("tts_nothing_to_speak does not claim generation failed", () => {
    expect(getErrorMessage("tts_nothing_to_speak")).not.toMatch(
      /could not be generated/i,
    );
  });

  it("a genuinely unknown code still falls back", () => {
    expect(getErrorMessage("not_a_real_code")).toBe(FALLBACK);
  });
});
