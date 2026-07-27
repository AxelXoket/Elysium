/**
 * chunkScheduler.ts - playing a reply that is still being synthesised.
 *
 * The backend hands over one sentence at a time (see `tts/speech_queue.py`),
 * which is what lets the voice start talking a couple of seconds in instead of
 * after the whole reply. Playing those sentences through one <audio> element
 * each does not work: the element starts when the browser gets round to it, so
 * the gap between sentences is whatever the event loop felt like that frame,
 * and a waveform cut at a non-zero sample is a step edge - a click.
 *
 * Web Audio fixes both. `start(when)` is sample-accurate against the audio
 * clock, and a gain ramp turns the seam into a short blend instead of an edge.
 * The crossfade is about 11 ms (512 samples at 44.1 kHz): long enough to hide
 * the discontinuity, far too short to be heard as an overlap.
 *
 * The scheduler owns TIMING only. It never fetches or decodes - the caller
 * hands it decoded buffers - because that keeps the whole thing testable
 * against a fake context, and "did chunk two start exactly one crossfade
 * before chunk one ended" is a question a test can answer while "does it
 * click" is not.
 */

/** ~512 samples at 44.1 kHz - the seam length the roadmap settled on. */
export const DEFAULT_CROSSFADE_SECONDS = 512 / 44100;

/**
 * How far ahead of `currentTime` the first chunk is placed. Scheduling at
 * exactly now is a race the browser resolves by starting late, which loses the
 * very accuracy this class exists for.
 */
const DEFAULT_LEAD_SECONDS = 0.02;

export interface ChunkSchedulerOptions {
  crossfadeSeconds?: number;
  /** Silence inserted between sentences. The pacing dial: it changes nothing
   * about the audio itself, so it carries no quality risk at all. */
  gapSeconds?: number;
  leadSeconds?: number;
  /** Everything queued has finished playing AND `finish()` was called. */
  onEnded?: () => void;
}

export class ChunkScheduler {
  private readonly ctx: AudioContext;
  private readonly crossfade: number;
  private readonly gap: number;
  private readonly lead: number;
  private readonly onEnded?: () => void;

  private cursor = 0;
  private live = 0;
  private done = false;
  private stopped = false;
  private ended = false;
  private nodes: { source: AudioBufferSourceNode; gain: GainNode }[] = [];
  /**
   * Has anything been scheduled yet?
   *
   * A separate flag because `nodes` no longer answers it: finished chunks
   * remove themselves (KÖK 10), so an emptied list means "all played" as
   * well as "nothing yet", and the first-chunk branch below must not
   * confuse the two - it would restart the cursor mid-reply.
   */
  private started = false;

  constructor(ctx: AudioContext, options: ChunkSchedulerOptions = {}) {
    this.ctx = ctx;
    this.crossfade = Math.max(0, options.crossfadeSeconds ?? DEFAULT_CROSSFADE_SECONDS);
    this.gap = Math.max(0, options.gapSeconds ?? 0);
    this.lead = Math.max(0, options.leadSeconds ?? DEFAULT_LEAD_SECONDS);
    this.onEnded = options.onEnded;
  }

  /** Latest audio-clock time this scheduler has committed to. */
  get scheduledUntil(): number {
    return this.cursor;
  }

  get isStopped(): boolean {
    return this.stopped;
  }

  /**
   * Queue one decoded sentence. Returns the audio-clock time it will start.
   *
   * Chunks that arrive after the cursor has already passed (a slow first
   * sentence, a stalled worker) restart from now rather than from a time in
   * the past - the browser would clamp a past `start()` to immediate anyway,
   * silently dropping the crossfade instead of reporting anything.
   */
  enqueue(buffer: AudioBuffer): number {
    if (this.stopped) return this.cursor;

    const earliest = this.ctx.currentTime + this.lead;
    const startAt = !this.started
      ? earliest
      : Math.max(this.cursor - (this.gap > 0 ? 0 : this.crossfade) + this.gap,
                 earliest);

    const source = this.ctx.createBufferSource();
    const gain = this.ctx.createGain();
    source.buffer = buffer;
    source.connect(gain);
    gain.connect(this.ctx.destination);

    // A fade at BOTH ends of every chunk, not only between them: the first
    // chunk's opening edge and the last one's closing edge are step edges too.
    const fade = Math.min(this.crossfade, buffer.duration / 2);
    const endAt = startAt + buffer.duration;
    gain.gain.setValueAtTime(0, startAt);
    gain.gain.linearRampToValueAtTime(1, startAt + fade);
    gain.gain.setValueAtTime(1, Math.max(startAt + fade, endAt - fade));
    gain.gain.linearRampToValueAtTime(0, endAt);

    const entry = { source, gain };
    source.onended = () => {
      this.live -= 1;
      // Released as it finishes, not at stop() (KÖK 10). A reply that ends
      // normally never calls stop(), so the decoded PCM of the WHOLE reply
      // - about 10 MB per spoken minute at 44.1 kHz mono float32 - stayed
      // in memory until the next utterance or a vault lock.
      try {
        source.disconnect();
        gain.disconnect();
      } catch {
        // Already torn down by stop(); nothing to release twice.
      }
      const at = this.nodes.indexOf(entry);
      if (at !== -1) this.nodes.splice(at, 1);
      this.maybeEnd();
    };
    this.live += 1;
    this.started = true;
    source.start(startAt);
    this.nodes.push(entry);

    this.cursor = endAt;
    return startAt;
  }

  /** No more chunks are coming. `onEnded` fires once the queued audio runs out. */
  finish(): void {
    this.done = true;
    this.maybeEnd();
  }

  /**
   * Stop everything immediately and refuse further work.
   *
   * Deliberately does NOT fire `onEnded`: upstream, ending a reply advances
   * continuous mode to the next message, and someone who pressed stop did not
   * ask for the next message.
   */
  stop(): void {
    if (this.stopped) return;
    this.stopped = true;
    for (const { source, gain } of this.nodes) {
      source.onended = null;
      try {
        source.stop();
      } catch {
        // Already finished on its own - stopping a stopped node throws in some
        // engines and means nothing here.
      }
      source.disconnect();
      gain.disconnect();
    }
    this.nodes = [];
    this.live = 0;
  }

  private maybeEnd(): void {
    if (this.ended || this.stopped) return;
    if (!this.done || this.live > 0) return;
    this.ended = true;
    this.onEnded?.();
  }
}
