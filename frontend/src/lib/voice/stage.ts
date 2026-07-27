/**
 * stage.ts - the single speaking stage: one voice source at a time.
 *
 * Elysium speaks from three independent places: the per-message Speak button
 * (playerStore, an HTMLAudioElement), the live reply stream (streamVoice, a
 * ChunkScheduler on the shared AudioContext) and the Delivery settings preview.
 * Each owned its own player and none knew the others existed, so pressing Speak
 * while a reply was being read aloud played both at once - the same character's
 * voice speaking two different messages, with a Stop face on only one of them.
 *
 * playerStore's docstring already called that "instantly, obviously broken in a
 * way no error message could excuse". It was true only WITHIN one source; every
 * pair of sources could overlap freely. This module is the missing arbiter, and
 * the rule is one line: taking the stage silences whoever holds it.
 */

export interface VoiceSource {
  /** Stop this source's playback NOW. Must be idempotent. */
  silence: () => void;
}

let occupant: VoiceSource | null = null;

/** Claim the stage for `source`, silencing the previous occupant. */
export function takeStage(source: VoiceSource): void {
  const previous = occupant;
  // Install FIRST: silence() runs the previous occupant's own teardown, which
  // calls leaveStage - identity-guarded, so it cannot evict the newcomer that
  // just displaced it.
  occupant = source;
  if (previous != null && previous !== source) previous.silence();
}

/** Give up the stage. No-op when someone else already took it. */
export function leaveStage(source: VoiceSource): void {
  if (occupant === source) occupant = null;
}

/** Silence whatever holds the stage. Vault lock, teardown, app exit. */
export function clearStage(): void {
  const previous = occupant;
  occupant = null;
  previous?.silence();
}

/** Introspection for tests: is anything holding the stage? */
export function stageOccupied(): boolean {
  return occupant !== null;
}
