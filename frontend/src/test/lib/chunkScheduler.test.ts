/**
 * V8-3 (frontend half) - gapless playback of synthesised sentences.
 *
 * An <audio> element per sentence clicks at every join: the element starts
 * when the browser feels like it, and a waveform cut at a non-zero sample is a
 * step edge, which is exactly what a click is. Web Audio can schedule to the
 * sample and ramp a gain, so the seam is placed deliberately instead of being
 * left to chance.
 *
 * A fake AudioContext records what was scheduled and when. That is the whole
 * point of testing here rather than by ear: "does it click" is not a question
 * jsdom can answer, but "did chunk two start exactly one crossfade before
 * chunk one ended" is, and that is the property that makes it not click.
 */
import { describe, it, expect, vi } from "vitest";

import { ChunkScheduler } from "@/lib/voice/chunkScheduler";

interface Ramp {
  kind: "setValueAtTime" | "linearRampToValueAtTime";
  value: number;
  time: number;
}

class FakeParam {
  ramps: Ramp[] = [];
  setValueAtTime(value: number, time: number) {
    this.ramps.push({ kind: "setValueAtTime", value, time });
    return this;
  }
  linearRampToValueAtTime(value: number, time: number) {
    this.ramps.push({ kind: "linearRampToValueAtTime", value, time });
    return this;
  }
  cancelScheduledValues() {
    return this;
  }
}

class FakeGain {
  gain = new FakeParam();
  connect = vi.fn();
  disconnect = vi.fn();
}

class FakeSource {
  buffer: { duration: number } | null = null;
  onended: (() => void) | null = null;
  started: number | null = null;
  stopped = false;
  connect = vi.fn();
  disconnect = vi.fn();
  start(when: number) {
    this.started = when;
  }
  stop() {
    this.stopped = true;
  }
  /** Pretend the audio finished, the way the real node would. */
  end() {
    this.onended?.();
  }
}

class FakeContext {
  currentTime = 0;
  destination = {};
  sources: FakeSource[] = [];
  gains: FakeGain[] = [];
  createBufferSource() {
    const s = new FakeSource();
    this.sources.push(s);
    return s as unknown as AudioBufferSourceNode;
  }
  createGain() {
    const g = new FakeGain();
    this.gains.push(g);
    return g as unknown as GainNode;
  }
}

function buf(duration: number) {
  return { duration } as AudioBuffer;
}

function make(opts = {}) {
  const ctx = new FakeContext();
  const scheduler = new ChunkScheduler(
    ctx as unknown as AudioContext,
    { crossfadeSeconds: 0.0116, leadSeconds: 0.02, ...opts },
  );
  return { ctx, scheduler };
}

// ── scheduling ───────────────────────────────────────────────────────────────

describe("ChunkScheduler", () => {
  it("schedules the first chunk slightly ahead of now, never in the past", () => {
    const { ctx, scheduler } = make();
    ctx.currentTime = 5;
    scheduler.enqueue(buf(2));
    expect(ctx.sources[0].started).toBeCloseTo(5.02, 5);
  });

  it("overlaps each chunk with the previous one by exactly one crossfade", () => {
    const { ctx, scheduler } = make({ crossfadeSeconds: 0.01 });
    scheduler.enqueue(buf(2));
    scheduler.enqueue(buf(3));
    const [a, b] = ctx.sources;
    // a runs 0.02 -> 2.02; b starts one crossfade before that edge.
    expect(a.started).toBeCloseTo(0.02, 5);
    expect(b.started).toBeCloseTo(2.01, 5);
  });

  it("fades every chunk in and out so no join is a step edge", () => {
    const { ctx, scheduler } = make({ crossfadeSeconds: 0.01 });
    scheduler.enqueue(buf(2));
    const ramps = ctx.gains[0].gain.ramps;
    expect(ramps[0]).toMatchObject({ value: 0, time: 0.02 });
    expect(ramps[1]).toMatchObject({ value: 1, time: 0.03 });
    // ...and back down, landing exactly on the end of the buffer.
    expect(ramps[ramps.length - 1]).toMatchObject({ value: 0, time: 2.02 });
  });

  it("inserts a real silence when a gap is configured, with no overlap", () => {
    // The pause dial is free of quality risk precisely because it does not
    // touch the audio - it only moves the next start time.
    const { ctx, scheduler } = make({ crossfadeSeconds: 0.01, gapSeconds: 0.3 });
    scheduler.enqueue(buf(2));
    scheduler.enqueue(buf(1));
    expect(ctx.sources[1].started).toBeCloseTo(2.02 + 0.3, 5);
  });

  it("keeps scheduling correctly when chunks arrive late", () => {
    // Synthesis is 1.6x realtime, but a slow first sentence can still land
    // after the cursor has passed. The next chunk must then start from NOW,
    // not from a cursor in the past (which the browser would clamp anyway,
    // silently losing the crossfade).
    const { ctx, scheduler } = make({ crossfadeSeconds: 0.01 });
    scheduler.enqueue(buf(1));
    ctx.currentTime = 9;
    scheduler.enqueue(buf(1));
    expect(ctx.sources[1].started).toBeCloseTo(9.02, 5);
  });

  it("reports how far ahead it is scheduled", () => {
    const { scheduler } = make({ crossfadeSeconds: 0.01 });
    scheduler.enqueue(buf(2));
    expect(scheduler.scheduledUntil).toBeCloseTo(2.02, 5);
  });
});

// ── lifecycle ────────────────────────────────────────────────────────────────

describe("ChunkScheduler lifecycle", () => {
  it("calls onEnded only after finish() and the last chunk really ended", () => {
    const onEnded = vi.fn();
    const { ctx, scheduler } = make({ onEnded });
    scheduler.enqueue(buf(1));
    ctx.sources[0].end();
    expect(onEnded).not.toHaveBeenCalled();   // more may still be coming

    scheduler.enqueue(buf(1));
    scheduler.finish();
    expect(onEnded).not.toHaveBeenCalled();   // the last one is still playing
    ctx.sources[1].end();
    expect(onEnded).toHaveBeenCalledTimes(1);
  });

  it("fires onEnded on finish() when everything already ended", () => {
    const onEnded = vi.fn();
    const { ctx, scheduler } = make({ onEnded });
    scheduler.enqueue(buf(1));
    ctx.sources[0].end();
    scheduler.finish();
    expect(onEnded).toHaveBeenCalledTimes(1);
  });

  it("stop() silences every scheduled chunk, including future ones", () => {
    const { ctx, scheduler } = make();
    scheduler.enqueue(buf(2));
    scheduler.enqueue(buf(2));
    scheduler.stop();
    expect(ctx.sources.every((s) => s.stopped)).toBe(true);
  });

  it("stop() does not fire onEnded - a stop is not a finish", () => {
    // The distinction matters upstream: onEnded advances continuous mode to
    // the next message, and a user who pressed stop did not ask for that.
    const onEnded = vi.fn();
    const { scheduler } = make({ onEnded });
    scheduler.enqueue(buf(1));
    scheduler.finish();
    scheduler.stop();
    expect(onEnded).not.toHaveBeenCalled();
  });

  it("ignores chunks enqueued after stop", () => {
    const { ctx, scheduler } = make();
    scheduler.enqueue(buf(1));
    scheduler.stop();
    scheduler.enqueue(buf(1));
    expect(ctx.sources).toHaveLength(1);
  });

  it("is safe to stop twice", () => {
    const { scheduler } = make();
    scheduler.enqueue(buf(1));
    scheduler.stop();
    expect(() => scheduler.stop()).not.toThrow();
  });

  it("a zero-length chunk does not stall the cursor", () => {
    const { ctx, scheduler } = make({ crossfadeSeconds: 0.01 });
    scheduler.enqueue(buf(0));
    scheduler.enqueue(buf(1));
    expect(ctx.sources[1].started).toBeGreaterThanOrEqual(0.02);
  });
});
