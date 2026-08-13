/**
 * V5-c - the per-message speak button and the one shared player.
 *
 * Pinned behaviours: no selected voice model means NO button (an affordance
 * that can only fail is a broken promise); speaking sends message_id (the
 * tags live in the raw row, not in the visible text); two messages can never
 * talk over each other; a refusal surfaces through the shared error store
 * with the backend's own code.
 */
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api/tts", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/tts")>()),
  // Only the streaming call is scripted; /tts/active and the rest keep going
  // through the real client so the button's readiness query is unchanged.
  streamMessageSpeech: vi.fn(
    async (_body: unknown, { onEvent }: { onEvent: (e: unknown) => void }) => {
      onEvent({ type: "voice_chunk", audio_id: "speak-9", index: 0 });
    },
  ),
}));

import { SpeakButton } from "@/components/chat/SpeakButton";
import { streamMessageSpeech } from "@/lib/api/tts";

//: Derived from the real signature rather than restated - a hand-written
//: shape here drifts from the endpoint it is standing in for.
type SpeechOpts = Parameters<typeof streamMessageSpeech>[1];
type SpeechEvent = Parameters<SpeechOpts["onEvent"]>[0];

/** The default script: one chunk, and the utterance stays open. */
function speaksOneChunk() {
  vi.mocked(streamMessageSpeech).mockImplementation(
    async (_body, { onEvent }) => {
      onEvent({ type: "voice_chunk", audio_id: "speak-9", index: 0 });
    },
  );
}
import { useErrorStore } from "@/lib/errors/errorStore";
import { useVoicePlayer } from "@/lib/voice/playerStore";
import { mockFetch } from "../mocks/api";
import { stubAudioContext } from "../helpers/fakeAudioContext";


const ACTIVE = {
  uid: "u1", state: "unloaded", engine_id: "fish_s2",
  vram_mb: null, error_code: null, readiness: null,
};

describe("SpeakButton", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    stubAudioContext();
    speaksOneChunk();
    useErrorStore.getState().clearAll();
    useVoicePlayer.setState({ messageId: null, phase: "idle", requestSeq: 0 });
  });

  afterEach(() => {
    useVoicePlayer.getState().stop();
    vi.unstubAllGlobals();
  });

  it("renders nothing when no voice model is selected", async () => {
    const fetchMock = mockFetch({
      "/tts/active": {
        body: { ...ACTIVE, uid: null, engine_id: null },
      },
    });
    renderWithQueryClient(<SpeakButton messageId={5} />);
    // Settled-query control (audit-2: a raw sleep could not distinguish
    // "correctly hidden" from "query broke"): wait until the active query
    // has actually been fetched, then assert the DELIBERATE absence.
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([url]) => String(url).includes("/tts/active")),
      ).toBe(true),
    );
    expect(screen.queryByLabelText("Speak message")).not.toBeInTheDocument();
  });

  it("renders nothing for a selected model whose verdict says it cannot run", async () => {
    const fetchMock = mockFetch({
      "/tts/active": {
        body: {
          ...ACTIVE,
          readiness: {
            uid: "u1", engine_id: "fish_s2", runnable: false,
            settings_available: true, runtime_state: "missing",
            issues: [], languages: [], fit: null,
          },
        },
      },
    });
    renderWithQueryClient(<SpeakButton messageId={5} />);
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([url]) => String(url).includes("/tts/active")),
      ).toBe(true),
    );
    // A speaker icon that can only produce an error toast is a broken promise.
    expect(screen.queryByLabelText("Speak message")).not.toBeInTheDocument();
  });

  it("speaks by message_id and flips to a stop control while playing", async () => {
    mockFetch({ "/tts/active": { body: ACTIVE } });
    renderWithQueryClient(<SpeakButton messageId={9} />);

    await userEvent.click(await screen.findByLabelText("Speak message"));

    // The id, not the text: the tags that make the voice worth hearing live in
    // the raw row, and the client only ever holds the stripped view.
    await waitFor(() =>
      expect(streamMessageSpeech).toHaveBeenCalledWith(
        { messageId: 9 },
        expect.anything(),
      ),
    );

    const stopControl = await screen.findByLabelText("Stop speaking");
    expect(stopControl).toBeInTheDocument();
    // The label is what a sighted user reads; aria-pressed is what everyone
    // else reads. Both come off the same `playing` flag but through separate
    // attributes, and only the label was ever checked.
    expect(stopControl).toHaveAttribute("aria-pressed", "true");

    // The AUDIO finishing returns the button to idle - not the request. Audio
    // is the one part of a reply that keeps going after the fetch resolves.
    useVoicePlayer.setState({ messageId: null, phase: "idle" });
    const idle = await screen.findByLabelText("Speak message");
    expect(idle).toBeInTheDocument();
    expect(idle).toHaveAttribute("aria-pressed", "false");
  });

  it("stops when the stop control is pressed, not only when the store is poked", async () => {
    // KADEME 19b. Every test that reached the stop state got there by writing
    // to the store directly, so the button's own `if (playing || busy) stop()`
    // branch - the wiring an actual press goes through - was never executed.
    // Deleting that branch left the whole suite green.
    mockFetch({ "/tts/active": { body: ACTIVE } });
    renderWithQueryClient(<SpeakButton messageId={9} />);

    await userEvent.click(await screen.findByLabelText("Speak message"));
    await userEvent.click(await screen.findByLabelText("Stop speaking"));

    await waitFor(() =>
      expect(useVoicePlayer.getState().phase, "the press did not stop it").toBe(
        "idle",
      ),
    );
    expect(await screen.findByLabelText("Speak message")).toBeInTheDocument();
  });

  it("starting a second message bumps the sequence, abandoning the first", async () => {
    mockFetch({ "/tts/active": { body: ACTIVE } });
    const { speak } = useVoicePlayer.getState();
    await speak(1);
    expect(useVoicePlayer.getState().messageId).toBe(1);
    expect(useVoicePlayer.getState().phase).toBe("playing");

    const seqBefore = useVoicePlayer.getState().requestSeq;
    await speak(2);
    // The sequence bump IS the stop: any continuation still holding the older
    // seq is abandoned, which is what keeps the first voice from resurfacing.
    expect(useVoicePlayer.getState().requestSeq).toBeGreaterThan(seqBefore);
    expect(useVoicePlayer.getState().messageId).toBe(2);
  });

  it("a backend refusal lands in the error store with the real code", async () => {
    mockFetch({ "/tts/active": { body: ACTIVE } });
    // The refusal happens before a single byte of audio: the endpoint validates
    // and rejects, so nothing is ever streamed.
    vi.mocked(streamMessageSpeech).mockRejectedValueOnce(
      Object.assign(new Error("tts_insufficient_vram"), {
        status: 409,
        detail: "tts_insufficient_vram",
      }),
    );
    renderWithQueryClient(<SpeakButton messageId={3} />);
    await userEvent.click(await screen.findByLabelText("Speak message"));

    await waitFor(() => {
      const errors = useErrorStore.getState().errors;
      expect(errors.some((e) => e.code === "tts_insufficient_vram")).toBe(true);
    });
    // And the button is usable again - not stuck in a phantom playing state.
    expect(screen.getByLabelText("Speak message")).toBeInTheDocument();
  });
});

describe("the player under adversity (audit-2)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    stubAudioContext();
    speaksOneChunk();
    useErrorStore.getState().clearAll();
    useVoicePlayer.setState({ messageId: null, phase: "idle", requestSeq: 0 });
  });

  it("a stop during synthesis leaves the player idle when the late chunk lands", async () => {
    // The audit's exact bug: stop() did not advance the sequence, so the late
    // response resurrected as audio no button anywhere could stop.
    let release: () => void;
    let late: (e: SpeechEvent) => void;
    vi.mocked(streamMessageSpeech).mockImplementationOnce(
      (_body, { onEvent }) => {
        late = onEvent;
        return new Promise<void>((res) => {
          release = res;
        });
      },
    );

    const speakPromise = useVoicePlayer.getState().speak(9);
    expect(useVoicePlayer.getState().phase).toBe("requesting");

    useVoicePlayer.getState().stop();
    expect(useVoicePlayer.getState().phase).toBe("idle");

    // The chunk lands AFTER the stop - the audit's exact shape, one layer
    // down: it used to become audio that no button anywhere could stop.
    late!({ type: "voice_chunk", audio_id: "late", index: 0 });
    release!();
    await speakPromise;

    expect(useVoicePlayer.getState().phase).toBe("idle");
    expect(useVoicePlayer.getState().messageId).toBeNull();
    // And no scolding toast for a user-initiated stop.
    expect(useErrorStore.getState().errors).toHaveLength(0);
  });

  it("locking the vault stops the spoken conversation", async () => {
    mockFetch({ "/tts/active": { body: ACTIVE } });
    await useVoicePlayer.getState().speak(4);
    expect(useVoicePlayer.getState().phase).toBe("playing");

    const { stopVoicePlayback } = await import("@/lib/voice/playerStore");
    stopVoicePlayback();
    expect(useVoicePlayer.getState().phase).toBe("idle");
    expect(useVoicePlayer.getState().messageId).toBeNull();
  });
});
