/**
 * errorMessages.ts - Centralized backend error code → safe user-facing message map.
 *
 * Every error code from docs/frontend_contract.md is mapped here.
 * Unknown codes fall back to a generic safe message.
 * Raw upstream/provider text is never exposed through this map.
 */

const ERROR_MESSAGES: Record<string, string> = {
  // Auth / API key
  api_key_missing:
    "No API key is configured. Please add your OpenRouter API key in Settings.",
  api_key_invalid:
    "API key is invalid. Please check it and try again.",
  validation_unavailable:
    "Could not validate the API key because the network or proxy is unavailable.",
  auth_failed:
    "Authentication failed. Please check your API key.",

  // Proxy
  proxy_missing:
    "Proxy is required but not configured. Please set a proxy URL in Settings.",
  proxy_unreachable:
    "Proxy is unreachable. Please check your proxy configuration.",
  proxy_auth_failed:
    "Proxy authentication failed. Please check your proxy credentials.",
  proxy_unhealthy:
    "The configured proxy is not responding. Please check your proxy configuration.",
  proxy_url_required:
    "A proxy URL is required. Please enter one in Settings.",
  invalid_proxy_scheme:
    "The proxy URL scheme is not supported. Use http, https, socks5, or socks5h.",
  proxy_url_invalid:
    "The proxy URL is not valid. Please check it and try again.",

  // OpenRouter
  openrouter_timeout:
    "The request timed out. Please try again.",
  openrouter_rate_limited:
    "Rate limited by the provider. Please wait a moment and try again.",
  openrouter_insufficient_credits:
    "Insufficient credits on your OpenRouter account.",
  openrouter_no_provider_meets_privacy:
    "This model may not be available with Elysium's strict privacy routing. Try another model.",
  openrouter_completion_error:
    "The provider returned an error. Please try again.",
  api_key_required_by_openrouter:
    "The provider requires an API key. Please add your OpenRouter API key in Settings.",
  invalid_openrouter_models_response:
    "Received an unexpected response while loading models. Please try again.",
  openrouter_models_error:
    "Could not load models. Please try again.",

  // Generation params
  context_too_large:
    "The context is too large for this model. Try reducing the context budget or clearing some messages.",
  invalid_generation_params:
    "One or more generation parameters are invalid.",
  invalid_gen_params:
    "One or more generation parameters are invalid.",
  unsupported_generation_params:
    "Some generation parameters are not supported by the selected model.",

  // Not found
  chat_not_found:
    "This chat no longer exists.",
  character_not_found:
    "This character no longer exists. Please refresh characters.",
  persona_not_found:
    "This persona no longer exists. Please refresh personas.",
  message_not_found:
    "This message no longer exists. Please refresh the chat.",

  // Regenerate
  not_last_assistant_message:
    "Only the latest assistant message can be regenerated.",
  no_preceding_user_message:
    "This response cannot be regenerated because there is no preceding user message.",
  regenerate_conflict:
    "The chat changed while regenerating. Please refresh and try again.",

  // Chat rename
  title_required:
    "Chat title cannot be empty.",
  title_too_long:
    "Chat title is too long. Please use at most 200 characters.",

  // Image attachments
  attachment_invalid:
    "This image cannot be used. Please choose a PNG, JPEG, or WebP file.",
  attachment_too_large:
    "This image is too large. Please use an image under 10 MB.",
  attachment_not_found:
    "An attached image no longer exists. Please remove it and attach it again.",
  attachment_unavailable:
    "An attached image was already used by another message. Please attach it again.",
  too_many_attachments:
    "Too many images attached. Please use at most 4 images per message.",
  model_no_image_input:
    "The selected model does not support image input. Remove the images or choose another model.",

  // Response / network
  invalid_response_shape:
    "Unexpected response format from server.",
  invalid_openrouter_completion_response:
    "The provider returned an unexpected response. Please try again.",
  network_error:
    "Could not reach the server. Please check your connection.",
  timeout:
    "The request timed out. Please try again.",

  // Character import
  character_json_too_large:
    "This character file is too large. Please use a smaller file.",
  invalid_character_json:
    "This character file is not valid JSON. Please check the file and try again.",
  character_name_required:
    "This character needs a name. Please add one and try again.",

  // Catch-all
  internal_error:
    "Something went wrong on the server. Please try again.",
  vault_locked:
    "Elysium locked. Enter your passphrase to continue.",
  unknown_error:
    "Something went wrong. Please try again.",
};

const FALLBACK_MESSAGE = "Something went wrong. Please try again.";

/**
 * Map a backend error code (detail string) to a safe user-facing message.
 * Never returns raw upstream text.
 */
export function getErrorMessage(code: string | undefined | null): string {
  if (!code) return FALLBACK_MESSAGE;
  return ERROR_MESSAGES[code] ?? FALLBACK_MESSAGE;
}

/**
 * Check if an error code is a known backend contract code.
 */
export function isKnownErrorCode(code: string): boolean {
  return code in ERROR_MESSAGES;
}
