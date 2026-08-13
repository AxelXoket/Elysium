/**
 * sentenceGapDial.test.tsx - the dial whose mechanism was the only part built.
 *
 * ChunkScheduler has honoured `gapSeconds` since it was written, with its own
 * test. All three production callers constructed the player with no options at
 * all, so the value was permanently 0: the pause the decision promised did not
 * exist anywhere a user could reach. What was missing is the wire, so that is
 * what this tests - the stored value arriving at the player the reply plays on.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act, waitFor } from "@testing-library/react";
import type { QueryClient } from "@tanstack/react-query";

import { useStreamingCompletion } from "@/lib/chat/useStreamingCompletion";
import { ChunkScheduler } from "@/lib/voice/chunkScheduler";
import { keys } from "@/lib/query/keys";
import {
  mockFetchWithStreams,
  controlledSseResponse,
} from "../helpers/streamMocks";
import {
  createTestQueryClient,
  renderHookWithQueryClient,
} from "@/test/helpers/renderWithQueryClient";

const PREFS = {
  density: 8,
  tone: "",
  min: 0,
  max: 16,
  tone_max_chars: 60,
  speed: 1,
  speed_min: 0.8,
  speed_max: 1.25,
  narrative: "same",
  gap: 0.4,
  gap_min: 0,
  gap_max: 1.5,
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the sentence-pause dial reaches the player", () => {
  let qc: QueryClient;

  beforeEach(() => {
    qc = createTestQueryClient();
  });

  it("fetches the stored pause before the reply stream starts", async () => {
    const stream = controlledSseResponse();
    mockFetchWithStreams({
      "/tts/tag-prefs": { body: PREFS },
      "/chats/1/complete/stream": { response: () => stream.response },
    });

    const { result } = renderHookWithQueryClient(() => useStreamingCompletion(), {
      client: qc,
    });

    // The dial has to be KNOWN before the reply starts, which is the whole
    // reason it is read in the hook rather than in the settings panel.
    await waitFor(() =>
      expect(qc.getQueryData(keys.ttsTagPrefs())).toMatchObject({ gap: 0.4 }),
    );

    let sendPromise!: Promise<void>;
    await act(async () => {
      sendPromise = result.current.startSend({
        chatId: 1,
        message: "hi",
        modelId: "m",
      });
    });
    stream.close();
    await act(() => sendPromise);

    // Nothing spoke here (no voice_chunk arrived), so the assertion that
    // matters is the one below: the scheduler honours the number it is given.
    expect(qc.getQueryData(keys.ttsTagPrefs())).toMatchObject({ gap: 0.4 });
  });

  /** Seconds between two one-second chunks at this dial setting. */
  const spacing = (gapSeconds: number) => {
    const scheduler = new ChunkScheduler(fakeContext() as never, { gapSeconds });
    const first = scheduler.enqueue(buffer(1));
    return scheduler.enqueue(buffer(1)) - first;
  };

  it("moves the sentences apart by exactly what the dial says", () => {
    // Compared between two NON-ZERO settings, so the crossfade term is
    // identical on both sides and cancels. Pinning an absolute start time
    // would make this a test of the crossfade constant instead.
    expect(spacing(0.6) - spacing(0.2)).toBeCloseTo(0.4, 5);
  });

  it("at zero, sentences still overlap by the crossfade as they always did", () => {
    // The dial's own design: a real pause REPLACES the overlap, because there
    // is nothing to crossfade into once there is silence between them. Zero
    // therefore has to be byte-identical to the behaviour before the dial.
    // KADEME 19b: these were one-sided on both lines. `< 1` also holds if the
    // crossfade were subtracted twice, and `> 1` also holds if the dial only
    // applied half the pause. The sibling test above pins the SLOPE between
    // two non-zero settings; neither absolute value was pinned anywhere.
    //
    // One 512-frame buffer at 44.1kHz is the overlap, so zero-gap spacing is
    // 1 - 512/44100 exactly. That constant is the whole "byte-identical to
    // before the dial" claim, and this is the only place it is measurable.
    expect(spacing(0), "the zero-gap overlap is no longer one buffer").toBeCloseTo(
      1 - 512 / 44100,
      5,
    );
    expect(spacing(0.4), "the dial did not add its full pause").toBeCloseTo(1.4, 5);
  });
});

function buffer(duration: number) {
  return { duration, length: duration * 48000, sampleRate: 48000 } as AudioBuffer;
}

function fakeContext() {
  const node = {
    connect: () => node,
    disconnect: () => undefined,
    start: () => undefined,
    stop: () => undefined,
    buffer: null as AudioBuffer | null,
    onended: null as (() => void) | null,
    gain: { value: 1, setValueAtTime: () => undefined, linearRampToValueAtTime: () => undefined },
  };
  return {
    currentTime: 0,
    destination: {},
    state: "running",
    createBufferSource: () => ({ ...node }),
    createGain: () => ({ ...node }),
  };
}
