/** Canonical query keys — consistent across all hooks. */
export const keys = {
  settings: () => ["settings"] as const,
  proxyHealth: () => ["proxyHealth"] as const,
  characters: () => ["characters"] as const,
  character: (id: number) => ["character", id] as const,
  chats: () => ["chats"] as const,
  chat: (id: number) => ["chat", id] as const,
  messages: (chatId: number) => ["messages", chatId] as const,
  models: () => ["models", "openrouter"] as const,
  personas: () => ["personas"] as const,
  persona: (id: number) => ["persona", id] as const,
};
