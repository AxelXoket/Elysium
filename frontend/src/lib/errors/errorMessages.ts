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

  // Regenerate / variants
  not_last_assistant_message:
    "Only the latest assistant message can be regenerated.",
  no_preceding_user_message:
    "This response cannot be regenerated because there is no preceding user message.",
  regenerate_conflict:
    "The chat changed while regenerating. Please refresh and try again.",
  variant_group_not_last:
    "Only the latest reply can switch variants. Please refresh the chat.",
  not_a_variant_target:
    "This message cannot switch variants.",

  // Message editing (v1.1)
  edit_conflict:
    "The chat changed while editing. Your edit was not saved - please refresh and try again.",
  exchange_stale:
    "The chat was cleared or deleted while the reply was streaming. Please refresh.",
  not_editable:
    "Only your own messages can be edited.",

  // Chat rename / create
  title_required:
    "Chat title cannot be empty.",
  title_too_long:
    "Chat title is too long. Please use at most 200 characters.",
  model_id_too_long:
    "That model id is too long. Please choose a model from the list.",

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
  // ── Voice / TTS (V0) ───────────────────────────────────────────────────────
  // Voice runs entirely on this machine. Every failure below is local: a model
  // that is missing, unreadable, too big for the GPU, or an engine that died.
  // None of them block chatting - they only turn voice off, and they always say
  // WHY (silent failure is what makes a voice feature feel broken).
  tts_model_not_found:
    "No voice model found. Put one in the voice models folder, then rescan.",
  tts_model_unknown:
    "That voice model is no longer available. Rescan the models folder.",
  tts_model_unrecognized:
    "Those files were not recognised as a supported voice model.",
  tts_model_incomplete:
    "This voice model folder is missing files it needs to load.",
  tts_engine_unknown:
    "This voice engine is not supported yet.",
  // Setting the runtime up is Elysium's job, not the user's - so this says what
  // to press, never "edit runtimes.json".
  tts_runtime_missing:
    "The voice engine is not set up yet. Use Set up voice in Settings to enable it.",
  tts_runtime_broken:
    "The voice engine was set up before but its files are gone now. Set up voice again to restore it.",
  tts_runtime_installing:
    "Voice engine setup is already running. Wait for it to finish, or cancel it first.",
  tts_runtime_install_failed:
    "Voice engine setup did not finish. Nothing was left half-installed - you can try again.",
  tts_python_not_found:
    "Elysium could not find a way to build the voice engine environment on this computer.",
  tts_insufficient_disk:
    "There is not enough free disk space to install the voice engine.",
  tts_param_invalid:
    "One of the voice settings is outside the allowed range.",
  // Not "the engine could not start" - no engine is involved. The model's own
  // folder would not accept a small marker file (read-only drive, permissions).
  tts_sidecar_write_failed:
    "Elysium could not write next to that model's files. The folder may be read-only.",
  tts_values_too_large:
    "Those voice settings are too large to save.",
  tts_insufficient_vram:
    "Not enough GPU memory to load this voice model. Close other GPU apps and try again.",
  // Distinct from the line above on purpose: telling someone with no NVIDIA
  // card to "close other GPU apps" sends them chasing a fix that cannot work.
  tts_gpu_unavailable:
    "No NVIDIA GPU could be found on this computer, so voice models cannot run here. You can still browse and configure them.",
  tts_language_unsupported:
    "This voice model does not speak the selected language. Pick another language or another model.",
  tts_model_already_loading:
    "A voice model is already loading. Wait for it to finish.",
  tts_load_timeout:
    "The voice model took too long to load and was stopped.",
  tts_worker_failed:
    "The voice engine could not start.",
  tts_worker_crashed:
    "The voice engine stopped unexpectedly. Voice is off until it is loaded again.",
  tts_worker_unavailable:
    "The voice engine is not running right now.",
  // Not the same advice as tts_insufficient_vram: this one ran out mid-flight,
  // so the fix is a smaller setting, not closing another program.
  tts_out_of_memory:
    "The voice model ran out of GPU memory while working. Lower its memory settings, or use a smaller model.",
  tts_synthesis_failed:
    "The voice could not be generated for this message.",
  tts_reference_too_short:
    "That voice clip is too short to clone from. Around ten seconds of clear speech works best.",
  tts_transcript_required:
    "This engine needs to know what is said in the voice clip. Type the words in below.",
  // Distinct from tts_worker_failed on purpose. The engine is running; it
  // simply has no speech recognition, and calling that "could not start" sent
  // people looking for a broken install that was never broken.
  tts_transcribe_unsupported:
    "This voice engine cannot listen to a clip and write out its words. Type them in below.",
  // Both used to be tts_synthesis_failed ("the voice could not be generated"),
  // which is untrue of either: one had nothing to say, the other already said
  // it and the file has since been wiped.
  tts_nothing_to_speak:
    "There was nothing to read out in that message.",
  tts_invalid_narrative:
    "That narration setting is not one Elysium knows. Pick one of the offered options.",
  tts_audio_expired:
    "That audio is no longer available - spoken replies are cleared when Elysium locks. Press speak again to hear it.",
  tts_reference_invalid:
    "That reference recording could not be used. Try a clear 10-20 second clip.",
  tts_audio_device_error:
    "No audio output device is available to play the voice.",
  tts_nothing_streaming:
    "That reply has already finished. Use the speaker button on the message to hear it.",

  internal_error:
    "Something went wrong on the server. Please try again.",
  vault_locked:
    "Elysium locked. Enter your passphrase to continue.",

  // Vault. The gate used to map only wrong_passphrase and passphrase_too_short
  // and collapse everything else into "Is the backend running?" - which is
  // never the cause, and which hid the ONE state that has a one-file fix:
  // encrypted data present, identity file gone.
  wrong_passphrase:
    "Wrong passphrase.",
  passphrase_too_short:
    "That passphrase is too short.",
  // Missing from this map entirely, two lines below its own sibling: somebody
  // pasting a long generated passphrase got "Something went wrong. Please try
  // again." and pasted the exact same thing again, forever.
  passphrase_too_long:
    "That passphrase is too long. Use 1024 characters or fewer.",
  vault_already_initialized:
    "Elysium is already set up on this computer. Unlock it with your passphrase instead.",
  vault_not_initialized:
    "Elysium has not been set up on this computer yet. Choose a passphrase to create your vault.",
  // The browser asked from an origin the backend does not serve. Local-only by
  // design, so this is a misconfiguration rather than an attack in practice -
  // but a fallback sentence gives nobody anywhere to look.
  cross_origin_denied:
    "That request came from an unexpected address and was refused. Open Elysium from its own window.",
  encrypted_db_without_identity:
    "Your data is here, but the key file (salt.bin) is missing, so no passphrase can open it. Restore salt.bin from a backup of the Elysium data folder - that one file recovers everything.",
  vault_init_failed:
    "The vault could not be created. Check that the Elysium data folder is writable and has free space, then try again.",
  vault_unlock_failed:
    "The passphrase was accepted but the vault could not be opened. The database may be in use by another program, or the disk may be full.",
  change_passphrase_failed:
    "The passphrase was not changed - nothing was lost, and your current passphrase still works. Close anything else using Elysium and retry.",
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
