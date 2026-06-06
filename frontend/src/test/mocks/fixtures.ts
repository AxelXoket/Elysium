import type { Settings, ProxyHealth } from "@/lib/schemas/settings";
import type { Character } from "@/lib/schemas/characters";
import type { Chat, Message } from "@/lib/schemas/chats";
import type { Model, ModelList } from "@/lib/schemas/models";
import type { CompletionResponse } from "@/lib/schemas/completions";

export const settingsFixture: Settings = {
  api_key_set: true,
  proxy_required: false,
  proxy_configured: false,
  proxy_alias: null,
};

export const proxyHealthFixture: ProxyHealth = {
  healthy: true,
  latency_ms: 42,
  reason: null,
  cached: false,
};

export const characterFixture: Character = {
  id: 1,
  name: "Test Character",
  description: "A test character for unit tests.",
  personality: "Helpful and friendly.",
  scenario: "Testing environment.",
  first_mes: "Hello! I'm a test character.",
  mes_example: "",
  system_prompt: "You are a test character.",
  post_history_instruction: "",
  tags: ["test"],
  created_at: "2026-01-01T00:00:00",
};

export const chatFixture: Chat = {
  id: 1,
  character_id: 1,
  character_name: "Test Character",
  title: "Test Chat",
  model_id: null,
  created_at: "2026-01-01T00:00:00",
  updated_at: "2026-01-01T00:00:00",
  message_count: 1,
};

export const messageFixture: Message = {
  id: 1,
  chat_id: 1,
  role: "assistant",
  content: "Hello! I'm a test character.",
  created_at: "2026-01-01T00:00:00",
};

export const modelFixture: Model = {
  id: "openai/gpt-4o",
  name: "GPT-4o",
  description: "OpenAI GPT-4o model",
  context_length: 128000,
  max_completion_tokens: 16384,
  supported_parameters: ["temperature", "top_p"],
  input_modalities: ["text", "image"],
  output_modalities: ["text"],
  pricing: { prompt: "0.005", completion: "0.015" },
  top_provider: { max_completion_tokens: 16384 },
  created: 1700000000,
  canonical_slug: "openai/gpt-4o",
};

export const modelListFixture: ModelList = {
  source: "user",
  cached: false,
  count: 1,
  models: [modelFixture],
};

export const modelListFallbackFixture: ModelList = {
  source: "public_fallback",
  cached: true,
  count: 1,
  models: [modelFixture],
  fallback_reason: "API key invalid or expired",
};

export const completionFixture: CompletionResponse = {
  chat_id: 1,
  model_id: "openai/gpt-4o",
  user_message: {
    id: 2,
    chat_id: 1,
    role: "user",
    content: "Hello there",
    created_at: "2026-01-01T00:01:00",
  },
  assistant_message: {
    id: 3,
    chat_id: 1,
    role: "assistant",
    content: "Hi! How can I help you?",
    created_at: "2026-01-01T00:01:01",
  },
};
