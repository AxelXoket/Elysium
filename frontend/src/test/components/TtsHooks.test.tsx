/**
 * V5-a - the voice API layer and its hooks, over a mocked wire.
 *
 * What matters here is not that fetch gets called - it is that the CONTRACT
 * holds at the seams the UI stands on: readiness travels with every model row,
 * error details surface as the exact backend code (so errorMessages can speak),
 * a voice-mode write lands in the cache immediately (the context gauge reads
 * it), install polling stops when the job ends, and the multipart upload never
 * gets a JSON Content-Type forced onto it.
 */
import { waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { isApiError } from "@/lib/api/client";
import { streamMessageSpeech, uploadVoice } from "@/lib/api/tts";
import { keys } from "@/lib/query/keys";
import {
  useSetVoiceMode,
  useTtsInstallStatus,
  useTtsModels,
  useVoiceMode,
} from "@/lib/query/tts";
import { mockFetch } from "../mocks/api";
import { renderHookWithQueryClient } from "@/test/helpers/renderWithQueryClient";

const READINESS = {
  uid: "u1",
  engine_id: "fish_s2",
  runnable: false,
  settings_available: true,
  runtime_state: "missing",
  issues: [
    {
      code: "tts_runtime_missing",
      severity: "blocker",
      detail: "the voice engine has not been set up yet",
      transient: false,
      action: "setup_runtime",
    },
  ],
  languages: ["en", "tr"],
  fit: null,
};

const MODEL = {
  uid: "u1",
  engine_id: "fish_s2",
  name: "s2-pro",
  path: "C:/models/s2-pro",
  variant: null,
  source: "signature",
  incomplete: false,
  missing: [],
  readiness: READINESS,
};

describe("voice hooks", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("models arrive with their readiness verdict attached", async () => {
    mockFetch({
      "/tts/models": {
        body: { models: [MODEL], unrecognized: [], roots: ["C:/voice/models"] },
      },
    });
    const { result } = renderHookWithQueryClient(() => useTtsModels());
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const model = result.current.data!.models[0];
    expect(model.readiness.runnable).toBe(false);
    expect(model.readiness.issues[0].code).toBe("tts_runtime_missing");
    expect(model.readiness.issues[0].action).toBe("setup_runtime");
  });

  it("a backend refusal surfaces as an ApiError whose detail IS the code", async () => {
    // A refusal happens BEFORE any audio: the endpoint validates and rejects,
    // so the caller gets a normal ApiError rather than an empty stream.
    mockFetch({
      "POST /tts/speak_stream": {
        status: 409,
        body: { detail: "tts_insufficient_vram" },
      },
    });
    try {
      await streamMessageSpeech({ messageId: 42 }, { onEvent: () => {} });
      expect.unreachable("speak should have thrown");
    } catch (err) {
      expect(isApiError(err)).toBe(true);
      if (isApiError(err)) {
        expect(err.detail).toBe("tts_insufficient_vram");
        expect(err.status).toBe(409);
      }
    }
  });

  it("speaking a message sends message_id, never the stripped text", async () => {
    // The delivery tags that make the voice worth hearing live in the RAW row;
    // the client only ever holds the stripped view, so it must send the id.
    const fetchMock = mockFetch({
      "POST /tts/speak_stream": { body: "" },
    });
    await streamMessageSpeech({ messageId: 7 }, { onEvent: () => {} });
    const call = fetchMock.mock.calls.find(([url]) =>
      String(url).includes("/tts/speak_stream"),
    )!;
    expect(JSON.parse(call[1]!.body as string)).toEqual({ message_id: 7 });
  });

  it("voice upload posts FormData without a forced JSON content type", async () => {
    const fetchMock = mockFetch({
      "POST /tts/voices/ayse": {
        body: {
          voice_id: "ayse",
          label: "Ayse",
          audio_name: "ref.wav",
          transcript: "hello",
          transcript_source: "user",
          seconds: 8.2,
          needs_conversion: false,
          has_transcript: true,
        },
      },
    });
    const file = new File([new Uint8Array([1, 2, 3])], "ref.wav", {
      type: "audio/wav",
    });
    const voice = await uploadVoice("ayse", file, { transcript: "hello" });
    expect(voice.has_transcript).toBe(true);

    const call = fetchMock.mock.calls.find(([url]) =>
      String(url).includes("/tts/voices/ayse"),
    )!;
    const init = call[1]!;
    expect(init.body).toBeInstanceOf(FormData);
    // fetch must derive the multipart boundary itself - a manual JSON header
    // here would make the backend reject every upload.
    const headers = new Headers(init.headers);
    expect(headers.get("Content-Type")).toBeNull();
  });

  it("setting voice mode writes the cache through immediately for the gauge", async () => {
    mockFetch({
      "POST /tts/voice-mode": {
        body: { enabled: true, active: true, prompt_chars: 3200 },
      },
      "/tts/voice-mode": {
        body: { enabled: false, active: false, prompt_chars: 3200 },
      },
    });
    const { result, queryClient } = renderHookWithQueryClient(() => ({
      mode: useVoiceMode(),
      set: useSetVoiceMode(),
    }));
    await waitFor(() => expect(result.current.mode.isSuccess).toBe(true));
    expect(result.current.mode.data!.active).toBe(false);

    result.current.set.mutate(true);
    await waitFor(() =>
      expect(queryClient.getQueryData(keys.ttsVoiceMode())).toMatchObject({
        active: true,
        prompt_chars: 3200,
      }),
    );
  });

  it("install polling stops on its own when the job reaches a terminal state", async () => {
    const done = {
      engine_id: "fish_s2",
      state: "done",
      log: ["voice engine ready"],
      error_code: null,
      error_detail: "",
      running: false,
    };
    const fetchMock = mockFetch({ "/tts/runtimes/fish_s2/install": { body: done } });
    const { result } = renderHookWithQueryClient(() =>
      useTtsInstallStatus("fish_s2"),
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data!.state).toBe("done");

    const callsAfterSettle = fetchMock.mock.calls.length;
    await new Promise((r) => setTimeout(r, 1600));
    // running:false => refetchInterval false => not a single extra poll.
    expect(fetchMock.mock.calls.length).toBe(callsAfterSettle);
  });
});
