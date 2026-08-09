/**
 * streamPlayer.ts - turning `voice_chunk` events into continuous speech.
 *
 * The backend sends one event per sentence as it finishes synthesising. Each
 * carries an id, not audio, so every chunk still has to be fetched and decoded
 * - and those finish OUT OF ORDER. Sentence three is short and lands before
 * sentence two. Playing them as they arrive would scramble the reply.
 *
 * So the fetches run as they arrive (network in parallel, which is the whole
 * point of starting them early) while the SCHEDULING is serialised behind a
 * promise chain. Order is a property of the chain, not of the network.
 *
 * The scheduler below it owns the seam between chunks; this class owns getting
 * bytes to it and the lifetime of the audio context.
 */

import { ttsAudioUrl } from "../api/tts";
import { ChunkScheduler, DEFAULT_CROSSFADE_SECONDS } from "./chunkScheduler";
import { launchTokenHeader } from "../api/launchToken";

export interface StreamPlayerOptions {
  crossfadeSeconds?: number;
  /** Silence between sentences - the pacing dial. */
  gapSeconds?: number;
  /** Everything queued has been spoken and no more is coming. */
  onEnded?: () => void;
  /** A chunk could not be fetched or decoded. */
  onError?: (err: unknown) => void;
  /** Injected for tests; defaults to the real fetch + decodeAudioData. */
  fetchChunk?: (audioId: string, ctx: AudioContext) => Promise<AudioBuffer>;
  /** Injected for tests; defaults to `new AudioContext()`. */
  createContext?: () => AudioContext;
}

async function defaultFetchChunk(
  audioId: string,
  ctx: AudioContext,
): Promise<AudioBuffer> {
  // Static import: the dynamic one bought nothing. `api/tts` is already
  // statically imported by the speak buttons and the query layer, so the
  // bundler cannot split it out - all the lazy form did was add an await.
  const res = await fetch(ttsAudioUrl(audioId), {
    credentials: "same-origin",
    headers: { ...launchTokenHeader() },
  });
  if (!res.ok) {
    // Carry the backend's own code (KÖK 14). This threw a bare Error, and both
    // consumers answered every failure with `tts_audio_device_error` - "No
    // audio output device is available to play the voice." A backend restart
    // or an expired wav therefore sent people looking for a sound-card driver,
    // while a REAL device fault never reaches here at all: the context throws
    // synchronously in push() and a refused resume() is swallowed.
    const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
    const detail =
      typeof body.detail === "string" && body.detail
        ? body.detail
        : res.status === 404
          ? "tts_audio_expired"
          : "tts_synthesis_failed";
    throw new VoiceChunkError(detail, res.status);
  }
  return ctx.decodeAudioData(await res.arrayBuffer());
}

/** A chunk fetch that failed with a code the shared error map already knows. */
export class VoiceChunkError extends Error {
  // Declared and assigned rather than written as constructor parameter
  // properties: the build runs under `erasableSyntaxOnly`, which rejects any
  // TypeScript that emits runtime code.
  readonly detail: string;
  readonly status: number;

  constructor(detail: string, status: number) {
    super(`voice chunk: ${detail} (HTTP ${status})`);
    this.name = "VoiceChunkError";
    this.detail = detail;
    this.status = status;
  }
}

/**
 * The code to report for a failure that came out of the player.
 *
 * `tts_audio_device_error` is reserved for what it says: the context could not
 * be created or resumed, or the bytes could not be decoded. Anything the
 * backend named, we repeat.
 */
export function voiceErrorCode(err: unknown): string {
  if (err instanceof VoiceChunkError) return err.detail;
  return "tts_audio_device_error";
}

/**
 * ONE context for the whole app, created on first use and never closed.
 *
 * A context per reply leaks: a reply that ends normally has no reason to call
 * stop(), so nothing closes it, and Chromium caps how many a page may hold
 * (around six). The seventh spoken reply would throw from `new AudioContext()`
 * and the voice would simply stop working, with nothing on screen to say why.
 *
 * Sharing is also better behaviour: one context means one resume-on-gesture,
 * and the schedulers built on it are per-reply anyway, so nothing is shared
 * that carries state between replies.
 */
let sharedContext: AudioContext | null = null;

function getSharedContext(create: () => AudioContext): AudioContext {
  if (!sharedContext || sharedContext.state === "closed") {
    sharedContext = create();
  }
  return sharedContext;
}

/** Tests only: drop the shared context so each case starts clean. */
export function __resetSharedAudioContext(): void {
  sharedContext = null;
}

export class VoiceStreamPlayer {
  private readonly options: StreamPlayerOptions;
  private ctx: AudioContext | null = null;
  private scheduler: ChunkScheduler | null = null;
  /** Serialises SCHEDULING while the fetches themselves run in parallel. */
  private chain: Promise<void> = Promise.resolve();
  private pending = 0;
  private finishRequested = false;
  private stopped = false;

  constructor(options: StreamPlayerOptions = {}) {
    this.options = options;
  }

  get active(): boolean {
    return !this.stopped && (this.pending > 0 || this.scheduler !== null);
  }

  /** Queue one chunk. Returns immediately; ordering is preserved internally. */
  push(audioId: string | null): void {
    if (this.stopped || !audioId) return;
    const ctx = this.ensureContext();
    const fetchChunk = this.options.fetchChunk ?? defaultFetchChunk;

    this.pending += 1;
    // Start the network NOW, before joining the chain: waiting our turn to
    // fetch would serialise the one part that has no reason to be serial.
    const decoded = fetchChunk(audioId, ctx);

    this.chain = this.chain
      .then(async () => {
        const buffer = await decoded;
        if (this.stopped) return;
        this.scheduler?.enqueue(buffer);
      })
      .catch((err) => {
        // One bad chunk must not wedge the chain: the rest of the reply is
        // still worth hearing, and the error is surfaced once.
        if (!this.stopped) this.options.onError?.(err);
      })
      .finally(() => {
        this.pending -= 1;
        this.maybeFinish();
      });
  }

  /** No more chunks are coming. */
  finish(): void {
    this.finishRequested = true;
    this.maybeFinish();
  }

  /** Stop immediately: user pressed stop, switched chat, or locked the vault. */
  stop(): void {
    if (this.stopped) return;
    this.stopped = true;
    // The SCHEDULER is what belongs to this reply, so it is what gets torn
    // down. The context is shared and deliberately outlives us - closing it
    // here would silence the next reply as well.
    this.scheduler?.stop();
    this.scheduler = null;
    this.ctx = null;
  }

  private maybeFinish(): void {
    if (!this.finishRequested || this.pending > 0 || this.stopped) return;
    // Everything queued is scheduled; the scheduler decides when the last
    // sample has actually played.
    this.scheduler?.finish();
  }

  private ensureContext(): AudioContext {
    if (!this.ctx) {
      const create =
        this.options.createContext ?? (() => new AudioContext());
      this.ctx = getSharedContext(create);
      // Autoplay policy: the context can start suspended. Sending a message is
      // a user gesture, so resuming here is allowed - and if it is refused the
      // audio simply never starts, which the error path reports.
      void this.ctx.resume?.().catch(() => {});
      this.scheduler = new ChunkScheduler(this.ctx, {
        crossfadeSeconds:
          this.options.crossfadeSeconds ?? DEFAULT_CROSSFADE_SECONDS,
        gapSeconds: this.options.gapSeconds,
        onEnded: () => this.options.onEnded?.(),
      });
    }
    return this.ctx;
  }
}
