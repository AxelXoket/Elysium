/**
 * boundedVoiceMemory.test.ts - KÖK 10, the two collections that only grew.
 *
 * Neither is wrong on a short run. Both are wrong on a long one, which is the
 * only kind of run a chat app actually has.
 */
import { describe, it, expect, vi } from "vitest";

import { ChunkScheduler } from "@/lib/voice/chunkScheduler";
import { createStreamVoice, stopAllStreamVoices } from "@/lib/voice/streamVoice";
import type { VoiceStreamPlayer } from "@/lib/voice/streamPlayer";

// ── the scheduler holds decoded PCM ────────────────────────────────────────

function buffer(duration: number) {
  return { duration, length: duration * 48000, sampleRate: 48000 } as AudioBuffer;
}

function fakeContext() {
  const made: { disconnected: number; ended: (() => void) | null }[] = [];
  const node = () => {
    const self = {
      connect: () => self,
      disconnect: () => {
        entry.disconnected += 1;
      },
      start: () => undefined,
      stop: () => undefined,
      buffer: null as AudioBuffer | null,
      onended: null as (() => void) | null,
      gain: {
        value: 1,
        setValueAtTime: () => undefined,
        linearRampToValueAtTime: () => undefined,
      },
    };
    const entry = { disconnected: 0, ended: null as (() => void) | null };
    made.push(entry);
    Object.defineProperty(self, "onended", {
      get: () => entry.ended,
      set: (fn: () => void) => {
        entry.ended = fn;
      },
    });
    return self;
  };
  return {
    made,
    ctx: {
      currentTime: 0,
      destination: {},
      state: "running",
      createBufferSource: node,
      createGain: node,
    },
  };
}

/** The scheduler's private node list, read for what it is: the leak. */
function heldNodes(scheduler: ChunkScheduler): number {
  return (scheduler as unknown as { nodes: unknown[] }).nodes.length;
}

describe("finished audio is released as it finishes", () => {
  it("does not hold the whole reply until something else speaks", () => {
    // A reply that ends normally never calls stop(), so every decoded chunk of
    // it - roughly 10 MB per spoken minute - stayed in memory until the next
    // utterance or a vault lock.
    const { ctx, made } = fakeContext();
    const scheduler = new ChunkScheduler(ctx as never, {});
    scheduler.enqueue(buffer(1));
    scheduler.enqueue(buffer(1));
    expect(heldNodes(scheduler)).toBe(2);

    // Both sources report they have finished playing.
    made.filter((m) => m.ended).forEach((m) => m.ended?.());
    expect(heldNodes(scheduler)).toBe(0);
  });

  it("disconnects what it releases", () => {
    const { ctx, made } = fakeContext();
    const scheduler = new ChunkScheduler(ctx as never, {});
    scheduler.enqueue(buffer(1));
    made.filter((m) => m.ended).forEach((m) => m.ended?.());
    expect(made.some((m) => m.disconnected > 0)).toBe(true);
  });

  it("releases the chunk that finished, not whichever one is first in the list", () => {
    // KADEME 19b. Every existing case ended all the chunks together, so the
    // count reached zero whether the release was targeted or not - and
    // `nodes.splice(at, 1)` could become `nodes.splice(0, 1)` unnoticed.
    // Chunks do NOT always finish in order (a stop, a short buffer, a
    // re-queue), and evicting the wrong entry leaves the one that really
    // finished held for good: exactly the megabytes-per-minute leak this
    // file exists for.
    const { ctx, made } = fakeContext();
    const scheduler = new ChunkScheduler(ctx as never, {});
    scheduler.enqueue(buffer(1));
    scheduler.enqueue(buffer(1));
    expect(heldNodes(scheduler)).toBe(2);

    const finishers = made.filter((m) => m.ended);
    expect(finishers.length, "no source was wired to finish").toBe(2);

    // The SECOND one finishes first, and only it is released.
    finishers[1].ended?.();
    expect(heldNodes(scheduler), "one release freed the wrong number").toBe(1);

    // Then the first. If the earlier release had taken the wrong entry, this
    // one is no longer in the list and nothing is ever freed again.
    finishers[0].ended?.();
    expect(heldNodes(scheduler), "a finished chunk stayed held").toBe(0);
  });

  it("still schedules the SECOND chunk after the first has finished", () => {
    // The regression the separate `started` flag exists for: the first-chunk
    // branch used `nodes.length === 0`, and an emptied list now means "all
    // played" as well as "nothing yet". Confusing the two restarts the cursor
    // mid-reply, and the rest of the reply plays over itself.
    const { ctx, made } = fakeContext();
    const scheduler = new ChunkScheduler(ctx as never, {});
    const first = scheduler.enqueue(buffer(1));
    const finished = made.filter((m) => m.ended);
    // Floor. `second > first` holds because the cursor advances on its own,
    // whether or not anything ever finished - so if production stopped
    // attaching `onended` this list would empty, the loop would do nothing,
    // and the test would still be green while testing the wrong thing.
    expect(finished.length, "nothing was ever wired to finish").toBeGreaterThan(0);
    finished.forEach((m) => m.ended?.());
    const second = scheduler.enqueue(buffer(1));
    expect(second).toBeGreaterThan(first);
  });

  it("ends the utterance once, after the last chunk", () => {
    const { ctx, made } = fakeContext();
    const onEnded = vi.fn();
    const scheduler = new ChunkScheduler(ctx as never, { onEnded });
    scheduler.enqueue(buffer(1));
    scheduler.finish();
    made.filter((m) => m.ended).forEach((m) => m.ended?.());
    expect(onEnded).toHaveBeenCalledTimes(1);
  });
});

// ── the live-voice registry ────────────────────────────────────────────────

const STOPPED = { count: 0 };

function fakePlayer(): VoiceStreamPlayer {
  return {
    push: () => undefined,
    finish: () => undefined,
    stop: () => {
      STOPPED.count += 1;
    },
  } as unknown as VoiceStreamPlayer;
}

/**
 * How many voices the registry is holding.
 *
 * Measured through the only thing that reads it: stopAllStreamVoices visits
 * every entry and stops its player. Counting those is the honest way to size
 * a private Set - and it also destroys the set, which is why each test builds
 * its own.
 */
function silenceAllAndCount(): number {
  STOPPED.count = 0;
  stopAllStreamVoices();
  return STOPPED.count;
}

describe("the live-voice registry only holds replies that can speak", () => {
  it("a reply that never spoke leaves nothing behind", () => {
    // Registration happens on the FIRST CHUNK now, not at construction:
    // every message used to add a permanent entry - voice off included -
    // and the only removal was in stop(), which the send handler calls only
    // on abort.
    createStreamVoice({ createPlayer: () => fakePlayer() });
    createStreamVoice({ createPlayer: () => fakePlayer() });
    createStreamVoice({ createPlayer: () => fakePlayer() });
    expect(silenceAllAndCount()).toBe(0);
  });

  it("a reply that spoke can still be silenced", () => {
    const voice = createStreamVoice({ createPlayer: () => fakePlayer() });
    voice.handle({ type: "voice_chunk", audio_id: "a", index: 0 });
    expect(silenceAllAndCount()).toBe(1);
  });

  it("lets go once the reply is silenced", () => {
    const voice = createStreamVoice({ createPlayer: () => fakePlayer() });
    voice.handle({ type: "voice_chunk", audio_id: "a", index: 0 });
    voice.stop();
    expect(silenceAllAndCount()).toBe(0);
  });

  // NOT covered here: the other removal, wired into the onEnded the PRODUCTION
  // player factory receives. Every test in this file replaces that factory, so
  // reaching it would mean driving a real VoiceStreamPlayer and a real
  // AudioContext - and the leak it closes (one entry per SPOKEN reply) is the
  // smaller half, since a spoken reply is evicted from the stage by the next
  // one anyway. The unconditional half above is what actually grew forever.
});
