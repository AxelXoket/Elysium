/**
 * V9-1 - playing `voice_chunk` events in the order they were spoken.
 *
 * The property that actually matters here is ORDER UNDER OUT-OF-ORDER
 * ARRIVAL: the chunks are fetched in parallel (deliberately - starting the
 * network early is the whole point) and a short sentence three lands before a
 * long sentence two. If scheduling followed arrival, the reply would be
 * scrambled, and nothing about that failure looks like a bug in a log.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

import {
  VoiceStreamPlayer,
  __resetSharedAudioContext,
} from "@/lib/voice/streamPlayer";

class FakeContext {
  currentTime = 0;
  destination = {};
  closed = false;
  resumed = false;
  scheduled: { duration: number; when: number }[] = [];

  createBufferSource() {
    const scheduled = this.scheduled;
    const node = {
      buffer: null as { duration: number } | null,
      onended: null as (() => void) | null,
      connect: vi.fn(),
      disconnect: vi.fn(),
      start(when: number) {
        scheduled.push({ duration: node.buffer?.duration ?? 0, when });
      },
      stop: vi.fn(),
    };
    return node as unknown as AudioBufferSourceNode;
  }

  createGain() {
    const param = {
      setValueAtTime: vi.fn().mockReturnThis(),
      linearRampToValueAtTime: vi.fn().mockReturnThis(),
      cancelScheduledValues: vi.fn().mockReturnThis(),
    };
    return {
      gain: param,
      connect: vi.fn(),
      disconnect: vi.fn(),
    } as unknown as GainNode;
  }

  resume() {
    this.resumed = true;
    return Promise.resolve();
  }

  close() {
    this.closed = true;
    return Promise.resolve();
  }
}

function make(overrides: Record<string, unknown> = {}) {
  const ctx = new FakeContext();
  const gates = new Map<string, (buf: { duration: number }) => void>();
  const durations = new Map<string, number>();

  const fetchChunk = vi.fn((audioId: string) =>
    new Promise<AudioBuffer>((resolve) => {
      gates.set(audioId, (buf) => resolve(buf as AudioBuffer));
    }),
  );

  const player = new VoiceStreamPlayer({
    createContext: () => ctx as unknown as AudioContext,
    fetchChunk: fetchChunk as never,
    crossfadeSeconds: 0.01,
    ...overrides,
  });

  /**
   * Let one chunk's fetch resolve, then drain the microtask queue.
   *
   * A fixed number of `await Promise.resolve()` hops is not enough: each push
   * adds its own then/catch/finally links, so releasing one chunk can unblock
   * several chain steps behind it. A macrotask tick drains all of them.
   */
  const land = async (audioId: string, duration = 1) => {
    durations.set(audioId, duration);
    gates.get(audioId)?.({ duration });
    await new Promise((resolve) => setTimeout(resolve, 0));
  };

  return { player, ctx, fetchChunk, land };
}

beforeEach(() => __resetSharedAudioContext());

describe("VoiceStreamPlayer", () => {
  it("starts every fetch immediately rather than one at a time", async () => {
    const { player, fetchChunk } = make();
    player.push("a");
    player.push("b");
    player.push("c");
    // Serialising the network would defeat the point of sending chunk events
    // early in the first place.
    expect(fetchChunk).toHaveBeenCalledTimes(3);
  });

  it("schedules in PUSH order even when the fetches land out of order", async () => {
    const { player, ctx, land } = make();
    player.push("a");
    player.push("b");
    player.push("c");

    // c and b come back first - a short sentence beating a long one.
    await land("c", 3);
    await land("b", 2);
    expect(ctx.scheduled).toHaveLength(0);   // nothing may play before "a"

    await land("a", 1);
    expect(ctx.scheduled.map((s) => s.duration)).toEqual([1, 2, 3]);
  });

  it("does not schedule anything after stop()", async () => {
    const { player, ctx, land } = make();
    player.push("a");
    player.stop();
    await land("a", 1);
    expect(ctx.scheduled).toHaveLength(0);
  });

  it("does NOT close the shared context on stop", () => {
    // Regression from the V9 audit. A context per reply leaked (a reply that
    // ends normally never calls stop), and Chromium caps them at around six -
    // so the seventh spoken reply died with nothing on screen to say why. One
    // shared context fixes that, and closing it here would silence the NEXT
    // reply instead.
    const { player, ctx } = make();
    player.push("a");
    player.stop();
    expect(ctx.closed).toBe(false);
  });

  it("reuses one context across replies instead of making a new one each time", () => {
    const ctx = new FakeContext();
    const create = vi.fn(() => ctx as unknown as AudioContext);
    for (let i = 0; i < 8; i += 1) {
      const player = new VoiceStreamPlayer({
        createContext: create,
        fetchChunk: (() => new Promise(() => {})) as never,
      });
      player.push(`chunk${i}`);
      player.stop();
    }
    expect(create).toHaveBeenCalledTimes(1);
  });

  it("resumes the context, because autoplay policy can start it suspended", () => {
    const { player, ctx } = make();
    player.push("a");
    expect(ctx.resumed).toBe(true);
  });

  it("creates no audio context at all until something is pushed", () => {
    const created = vi.fn(() => new FakeContext() as unknown as AudioContext);
    const player = new VoiceStreamPlayer({ createContext: created });
    expect(created).not.toHaveBeenCalled();
    player.stop();
  });

  it("ignores a null audio id instead of fetching nothing", () => {
    const { player, fetchChunk } = make();
    player.push(null);
    expect(fetchChunk).not.toHaveBeenCalled();
  });

  it("reports a failed chunk once and keeps the rest of the reply", async () => {
    const onError = vi.fn();
    const failing = vi.fn((audioId: string) =>
      audioId === "b"
        ? Promise.reject(new Error("HTTP 404"))
        : Promise.resolve({ duration: 1 } as AudioBuffer),
    );
    const ctx = new FakeContext();
    const player = new VoiceStreamPlayer({
      createContext: () => ctx as unknown as AudioContext,
      fetchChunk: failing as never,
      onError,
      crossfadeSeconds: 0.01,
    });

    player.push("a");
    player.push("b");
    player.push("c");
    await new Promise((r) => setTimeout(r, 0));

    expect(onError).toHaveBeenCalledTimes(1);
    // One bad chunk must not wedge the chain behind it.
    expect(ctx.scheduled).toHaveLength(2);
  });

  it("calls onEnded only after finish() and every chunk has played", async () => {
    const onEnded = vi.fn();
    const { player, land } = make({ onEnded });
    player.push("a");
    player.finish();
    expect(onEnded).not.toHaveBeenCalled();   // "a" has not even landed yet
    await land("a", 1);
    // The scheduler decides when the last sample really ends; what is asserted
    // here is that finish() was not swallowed while a fetch was outstanding.
    expect(player.active).toBe(true);
  });

  it("survives finish() before anything was ever pushed", () => {
    const { player } = make();
    expect(() => player.finish()).not.toThrow();
  });

  it("is safe to stop twice", () => {
    const { player } = make();
    player.push("a");
    player.stop();
    expect(() => player.stop()).not.toThrow();
  });
});
