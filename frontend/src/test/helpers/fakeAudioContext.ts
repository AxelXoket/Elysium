/**
 * A headless AudioContext, good enough for the scheduler and no more.
 *
 * jsdom has no Web Audio, so any test that drives the REAL player needs one of
 * these. It lived inside streamPlayer.test.ts until the Speak button started
 * streaming too and a second test file needed the same thing - at which point
 * copying it would have been two fakes to keep in step with one scheduler.
 */
import { vi } from "vitest";

export class FakeAudioContext {
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

  decodeAudioData() {
    return Promise.resolve({ duration: 1 } as AudioBuffer);
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

/** Install it as the global, the way a browser would provide one. */
export function stubAudioContext(): void {
  vi.stubGlobal("AudioContext", FakeAudioContext as unknown as typeof AudioContext);
}
