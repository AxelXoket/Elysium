/**
 * tts.ts - voice subsystem API (/tts/*).
 *
 * Thin functions in the house pattern: one per endpoint, response validated by
 * the Zod schema, errors normalized to ApiError whose `detail` IS the contract
 * code - every `tts_*` code already has a human sentence in errorMessages.ts,
 * so nothing here invents user-facing text.
 *
 * Privacy: everything talks to the local backend only. Voice runs on this
 * machine; the audio URL below is a localhost fetch of a session-only file.
 */

import { z } from "zod/v4";

import { streamCompletion, type StreamEvent } from "./stream";

import {
  TtsActiveSchema,
  TtsInstallJobSchema,
  TtsInstallPlanSchema,
  TtsRuntimesSchema,
  TtsScanSchema,
  TtsSchemaSchema,
  TtsSpeakSchema,
  TtsStateSchema,
  TtsValuesSchema,
  TtsVoiceListSchema,
  TtsVoiceModeSchema,
  TtsVoiceSchema,
  type TtsActive,
  type TtsInstallJob,
  type TtsInstallPlan,
  type TtsModelSchemaInfo,
  type TtsParamValue,
  type TtsRuntimes,
  type TtsScan,
  type TtsSpeak,
  type TtsState,
  type TtsValues,
  type TtsVoice,
  type TtsVoiceMode,
} from "../schemas/tts";
import { request, rawRequest } from "./client";

/** Forward-compatible on purpose, like the other tts schemas. */
const SpeakLiveSchema = z.object({ speaking: z.boolean() });
import { API_BASE } from "./base";

// ── discovery + per-model settings ───────────────────────────────────────────

export function listTtsModels(): Promise<TtsScan> {
  return request("/tts/models", TtsScanSchema);
}

export function rescanTtsModels(): Promise<TtsScan> {
  return request("/tts/rescan", TtsScanSchema, { method: "POST" });
}

export function getTtsSchema(uid: string): Promise<TtsModelSchemaInfo> {
  return request(`/tts/models/${encodeURIComponent(uid)}/schema`, TtsSchemaSchema);
}

export function getTtsSettings(uid: string): Promise<TtsValues> {
  return request(`/tts/models/${encodeURIComponent(uid)}/settings`, TtsValuesSchema);
}

export function saveTtsSettings(
  uid: string,
  values: Record<string, TtsParamValue>,
): Promise<TtsValues> {
  return request(`/tts/models/${encodeURIComponent(uid)}/settings`, TtsValuesSchema, {
    method: "POST",
    body: JSON.stringify({ values }),
  });
}

export function resetTtsSettings(uid: string): Promise<TtsValues> {
  return request(`/tts/models/${encodeURIComponent(uid)}/settings`, TtsValuesSchema, {
    method: "DELETE",
  });
}

// ── selection + live state ───────────────────────────────────────────────────

export function getTtsActive(): Promise<TtsActive> {
  return request("/tts/active", TtsActiveSchema);
}

export function setTtsActive(uid: string): Promise<TtsActive> {
  return request("/tts/active", TtsActiveSchema, {
    method: "POST",
    body: JSON.stringify({ uid }),
  });
}

export function getTtsState(): Promise<TtsState> {
  return request("/tts/state", TtsStateSchema);
}

export function loadVoice(uid?: string): Promise<TtsState> {
  return request("/tts/load", TtsStateSchema, {
    method: "POST",
    body: JSON.stringify(uid ? { uid } : {}),
  });
}

export function unloadVoice(): Promise<TtsState> {
  return request("/tts/unload", TtsStateSchema, { method: "POST" });
}

/**
 * Speak a stored message, receiving each piece AS IT IS MADE.
 *
 * The Speak button used to wait for the whole reply: `/tts/speak` joins every
 * sentence into one file and answers at the end, so a four-paragraph message
 * was twenty seconds of silence before anything was heard. The shape of the
 * request was the latency - the engine was never the problem.
 *
 * The events are the SAME `voice_chunk` / `voice_error` / `voice_done` the
 * live reply already emits, so both paths feed one player. Two ways to hear a
 * sentence would be two things to keep working.
 */
export function streamMessageSpeech(
  body: { messageId?: number; text?: string },
  options: { signal?: AbortSignal; onEvent: (event: StreamEvent) => void },
): Promise<void> {
  return streamCompletion(
    "/tts/speak_stream",
    body.messageId !== undefined
      ? { message_id: body.messageId }
      : { text: body.text ?? "" },
    options,
  );
}

/**
 * Start speaking the reply that is streaming in this chat RIGHT NOW.
 *
 * `streamMessageSpeech` cannot do this: during a stream the assistant row does
 * not exist yet, so there is no id to send - and the client cannot send the
 * text either, because it only ever holds the stripped view while the delivery
 * tags that make the voice worth hearing live server-side.
 *
 * Nothing useful comes back. The audio arrives as `voice_chunk` events on the
 * SSE stream this client is already reading.
 */
export function speakLive(chatId: number): Promise<{ speaking: boolean }> {
  return request("/tts/speak_live", SpeakLiveSchema, {
    method: "POST",
    body: JSON.stringify({ chat_id: chatId }),
  });
}

export function speakText(text: string): Promise<TtsSpeak> {
  return request("/tts/speak", TtsSpeakSchema, {
    method: "POST",
    body: JSON.stringify({ text }),
  });
}

/** URL of a synthesized wav - usable directly as an <audio src>. Served with
 * no-store so the embedded browser's persistent profile never keeps a copy. */
export function ttsAudioUrl(audioId: string): string {
  return `${API_BASE}/tts/audio/${encodeURIComponent(audioId)}`;
}

// ── engine setup (the app installs it; the user never opens a terminal) ──────

export function listTtsRuntimes(): Promise<TtsRuntimes> {
  return request("/tts/runtimes", TtsRuntimesSchema);
}

export function getInstallPlan(engineId: string): Promise<TtsInstallPlan> {
  return request(
    `/tts/runtimes/${encodeURIComponent(engineId)}/plan`,
    TtsInstallPlanSchema,
  );
}

export function startInstall(engineId: string): Promise<TtsInstallJob> {
  return request(
    `/tts/runtimes/${encodeURIComponent(engineId)}/install`,
    TtsInstallJobSchema,
    { method: "POST" },
  );
}

export function getInstallStatus(engineId: string): Promise<TtsInstallJob> {
  return request(
    `/tts/runtimes/${encodeURIComponent(engineId)}/install`,
    TtsInstallJobSchema,
  );
}

export function cancelInstall(engineId: string): Promise<TtsInstallJob> {
  return request(
    `/tts/runtimes/${encodeURIComponent(engineId)}/install/cancel`,
    TtsInstallJobSchema,
    { method: "POST" },
  );
}

export function uninstallRuntime(
  engineId: string,
): Promise<{ engine_id: string; removed: boolean }> {
  return request(
    `/tts/runtimes/${encodeURIComponent(engineId)}`,
    // A one-off ack, not a domain object anything else reads.
    z.object({ engine_id: z.string(), removed: z.boolean() }),
    { method: "DELETE" },
  );
}

// ── reference voices ─────────────────────────────────────────────────────────

export function listVoices(): Promise<{ voices: TtsVoice[] }> {
  return request("/tts/voices", TtsVoiceListSchema);
}

/** Multipart, so it bypasses the JSON client helper the same way image
 * uploads do: fetch must derive the boundary from the FormData itself. */
export function uploadVoice(
  voiceId: string,
  file: File,
  opts?: { label?: string; transcript?: string },
): Promise<TtsVoice> {
  const form = new FormData();
  form.append("file", file);
  if (opts?.label) form.append("label", opts.label);
  if (opts?.transcript) form.append("transcript", opts.transcript);
  return rawRequest(`/tts/voices/${encodeURIComponent(voiceId)}`, TtsVoiceSchema, {
    method: "POST",
    body: form,
  });
}

export function setVoiceTranscript(
  voiceId: string,
  text: string,
): Promise<TtsVoice> {
  return request(
    `/tts/voices/${encodeURIComponent(voiceId)}/transcript`,
    TtsVoiceSchema,
    { method: "POST", body: JSON.stringify({ text }) },
  );
}

export function transcribeVoice(voiceId: string): Promise<TtsVoice> {
  return request(
    `/tts/voices/${encodeURIComponent(voiceId)}/transcribe`,
    TtsVoiceSchema,
    { method: "POST" },
  );
}

export function deleteVoice(
  voiceId: string,
): Promise<{ voice_id: string; removed: boolean }> {
  return request(
    `/tts/voices/${encodeURIComponent(voiceId)}`,
    z.object({ voice_id: z.string(), removed: z.boolean() }),
    { method: "DELETE" },
  );
}

// ── voice mode (global toggle; feeds the context gauge) ──────────────────────

export function getVoiceMode(): Promise<TtsVoiceMode> {
  return request("/tts/voice-mode", TtsVoiceModeSchema);
}

export function setVoiceMode(enabled: boolean): Promise<TtsVoiceMode> {
  return request("/tts/voice-mode", TtsVoiceModeSchema, {
    method: "POST",
    body: JSON.stringify({ enabled }),
  });
}

/** The delivery dials: tag density, a standing tone, and reading speed. */
const TagPrefsSchema = z.object({
  density: z.number(),
  tone: z.string(),
  min: z.number(),
  max: z.number(),
  tone_max_chars: z.number(),
  // Reading speed is app-level (tts/matrix.py APP_LEVEL): the settings panel
  // greys the engine's own rate knob and says the dial "lives under Delivery",
  // so this is where it lives.
  speed: z.number(),
  speed_min: z.number(),
  speed_max: z.number(),
  /**
   * How narration is voiced on the REPLAY path.
   *
   * The live stream carries this in its request body; a replay has no request
   * to carry it and reads the stored value instead. Nothing ever WROTE that
   * value, so picking "Skip" worked while a reply arrived and was ignored the
   * moment the Speak button repeated the same message. Defaulted so an older
   * backend renders the row instead of failing the whole panel.
   */
  narrative: z.enum(["same", "narrator", "skip"]).default("same"),
  /**
   * Silence inserted BETWEEN sentences, in seconds.
   *
   * The mechanism has existed and been tested all along (ChunkScheduler's
   * `gapSeconds`); all three production callers built the player without
   * options, so the value was always 0 and the dial the decision promised
   * existed nowhere. Defaulted so an older backend renders the panel.
   */
  gap: z.number().default(0),
  gap_min: z.number().default(0),
  gap_max: z.number().default(1.5),
});
export type TtsTagPrefs = z.infer<typeof TagPrefsSchema>;
export type NarrationMode = TtsTagPrefs["narrative"];

/**
 * The user's own reading rules: {written form -> how to say it}.
 *
 * speech_prep has applied these since it was written and nothing supplied
 * them, so a character called "Aoife" was mispronounced in every single reply
 * with nowhere in the app to correct it.
 */
const PronunciationsSchema = z.object({
  pronunciations: z.record(z.string(), z.string()),
  max_entries: z.number().default(200),
  max_chars: z.number().default(80),
});
export type TtsPronunciations = z.infer<typeof PronunciationsSchema>;

export function getPronunciations(): Promise<TtsPronunciations> {
  return request("/tts/pronunciations", PronunciationsSchema);
}

/** The WHOLE table. Editing reading rules is a list operation - people remove
 *  entries as often as they add them - and a merge-only endpoint cannot
 *  express a deletion at all. */
export function savePronunciations(
  pronunciations: Record<string, string>,
): Promise<TtsPronunciations> {
  return request("/tts/pronunciations", PronunciationsSchema, {
    method: "POST",
    body: JSON.stringify({ pronunciations }),
  });
}

export function getTagPrefs(): Promise<TtsTagPrefs> {
  return request("/tts/tag-prefs", TagPrefsSchema);
}

export function saveTagPrefs(
  patch: {
    density?: number;
    tone?: string;
    speed?: number;
    narrative?: NarrationMode;
    gap?: number;
  },
): Promise<TtsTagPrefs> {
  return request("/tts/tag-prefs", TagPrefsSchema, {
    method: "POST",
    body: JSON.stringify(patch),
  });
}
