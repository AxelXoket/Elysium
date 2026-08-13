/**
 * speakWhenStreamBegins.test.ts - the S16 half of the continuous-voice pair.
 *
 * S15 (do not read back what is already on screen) and S16 (do read the reply
 * that has not started yet) are a matched decision, and the code could not
 * tell the two states apart - so S15 was implemented and S16 was unreachable
 * while the decision log counted both as delivered.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";

import {
  registerStream,
  unregisterStream,
  noteFirstDelta,
  isAwaitingFirstDelta,
  useStreamRegistry,
} from "@/lib/chat/streamRegistry";

const speakLive = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api/tts", () => ({ speakLive }));

const { speakWhenStreamBegins } = await import(
  "@/lib/voice/speakWhenStreamBegins"
);

function notStreaming() {
  return { status: 404, detail: "tts_nothing_streaming", message: "" };
}

beforeEach(() => {
  vi.useFakeTimers();
  speakLive.mockReset();
  useStreamRegistry.setState({
    controllers: new Map(),
    awaitingFirstDelta: new Set(),
  });
});

afterEach(() => {
  vi.useRealTimers();
});

/** Drive a promise that awaits fake timers to completion. */
async function settle<T>(p: Promise<T>): Promise<T> {
  await vi.runAllTimersAsync();
  return p;
}

describe("the S16 window", () => {
  it("opens when a stream is registered and nothing has arrived", () => {
    registerStream(1, new AbortController());
    expect(isAwaitingFirstDelta(1)).toBe(true);
  });

  it("closes on the first delta - that is S15", () => {
    registerStream(1, new AbortController());
    noteFirstDelta(1);
    expect(isAwaitingFirstDelta(1)).toBe(false);
  });

  it("closes when the stream ends", () => {
    const controller = new AbortController();
    registerStream(1, controller);
    unregisterStream(1, controller);
    expect(isAwaitingFirstDelta(1)).toBe(false);
  });

  it("is per chat", () => {
    registerStream(1, new AbortController());
    expect(isAwaitingFirstDelta(2)).toBe(false);
  });
});

describe("speakWhenStreamBegins", () => {
  it("wakes the dormant speaker of a reply that has not started", async () => {
    registerStream(1, new AbortController());
    speakLive.mockResolvedValue({ speaking: true });

    await expect(settle(speakWhenStreamBegins(1))).resolves.toBe(true);
    expect(speakLive).toHaveBeenCalledWith(1);
  });

  it("does nothing once text is on screen - S15 wins", async () => {
    registerStream(1, new AbortController());
    noteFirstDelta(1);

    await expect(settle(speakWhenStreamBegins(1))).resolves.toBe(false);
    expect(speakLive).not.toHaveBeenCalled();
  });

  it("does nothing when no stream is in flight", async () => {
    await expect(settle(speakWhenStreamBegins(1))).resolves.toBe(false);
    expect(speakLive).not.toHaveBeenCalled();
  });

  it("retries while the server is still catching up", async () => {
    // The real gap this exists for: the client registers when it calls fetch,
    // the server when its generator starts - after the DB reads and the proxy
    // gate. One "nothing is streaming" in that window is a race, not an answer.
    registerStream(1, new AbortController());
    speakLive
      .mockRejectedValueOnce(notStreaming())
      .mockRejectedValueOnce(notStreaming())
      .mockResolvedValueOnce({ speaking: true });

    await expect(settle(speakWhenStreamBegins(1))).resolves.toBe(true);
    expect(speakLive).toHaveBeenCalledTimes(3);
  });

  it("gives up if the reply starts arriving while it waits", async () => {
    registerStream(1, new AbortController());
    speakLive.mockImplementation(() => {
      // The first delta lands between attempts: the window has closed and
      // speaking now would read text the user has already seen.
      noteFirstDelta(1);
      return Promise.reject(notStreaming());
    });

    await expect(settle(speakWhenStreamBegins(1))).resolves.toBe(false);
    expect(speakLive).toHaveBeenCalledTimes(1);
  });

  it("stops on a real failure instead of hammering the endpoint", async () => {
    registerStream(1, new AbortController());
    speakLive.mockRejectedValue({
      status: 409,
      detail: "tts_runtime_missing",
      message: "",
    });

    await expect(settle(speakWhenStreamBegins(1))).resolves.toBe(false);
    expect(speakLive).toHaveBeenCalledTimes(1);
  });

  it("gives up quietly rather than forever", async () => {
    registerStream(1, new AbortController());
    speakLive.mockRejectedValue(notStreaming());

    await expect(settle(speakWhenStreamBegins(1))).resolves.toBe(false);
    // Both sides. The ceiling alone ("gives up") was satisfied by giving up
    // after ONE try, which is not patience, it is a different bug: the reply
    // stream can take a moment to appear and a single attempt would lose the
    // voice on a slow machine.
    expect(speakLive.mock.calls.length, "it stopped trying too early or too late")
      .toBe(6);
  });
});
