/** Canonical query keys - consistent across all hooks. */
export const keys = {
  settings: () => ["settings"] as const,
  proxyHealth: () => ["proxyHealth"] as const,
  // Voice / TTS. One namespace so a single invalidate can sweep it, with
  // narrower keys for the pieces that poll or key off a uid.
  tts: () => ["tts"] as const,
  ttsModels: () => ["tts", "models"] as const,
  ttsSchema: (uid: string) => ["tts", "schema", uid] as const,
  ttsSettings: (uid: string) => ["tts", "settings", uid] as const,
  ttsActive: () => ["tts", "active"] as const,
  ttsState: () => ["tts", "state"] as const,
  ttsRuntimes: () => ["tts", "runtimes"] as const,
  ttsInstall: (engineId: string) => ["tts", "install", engineId] as const,
  ttsVoices: () => ["tts", "voices"] as const,
  ttsVoiceMode: () => ["tts", "voiceMode"] as const,
  ttsTagPrefs: () => ["tts", "tagPrefs"] as const,
  characters: () => ["characters"] as const,
  character: (id: number) => ["character", id] as const,
  chats: () => ["chats"] as const,
  chat: (id: number) => ["chat", id] as const,
  messages: (chatId: number) => ["messages", chatId] as const,
  models: () => ["models", "openrouter"] as const,
  personas: () => ["personas"] as const,
  persona: (id: number) => ["persona", id] as const,
  vault: () => ["vault"] as const,
};
