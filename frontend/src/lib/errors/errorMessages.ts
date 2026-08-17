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
  // This used to arrive as auth_failed, so a refused message read as "check
  // your API key" and sent people rotating a key that worked. Moderation is
  // per model and per provider, so "try another model" is real advice here and
  // not a shrug. Nothing about WHAT was flagged is shown: the backend never
  // sends it, deliberately.
  openrouter_moderation_blocked:
    "The provider refused this message before generating anything. Rewording it, or choosing another model, usually works.",
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
  // `unsupported_generation_params` lived here with a sentence and no sender.
  // Removed 2026-08-10: the catalogue's producer check found zero raise sites
  // in Python and zero throw sites in TypeScript. It existed only here and in
  // one hand-written test array that asserted it had a sentence, which it did,
  // for an event that cannot happen.

  // Stream notices. Not failures: the reply is on its way, and the user is
  // being told what was dropped from it on the way there. Written as full
  // sentences rather than the fallback because all three reached the reader as
  // "Something went wrong. Please try again." until 2026-08-10 - including the
  // one below that reports this app REFUSING to make a second network request,
  // which is the promise the whole design is built on and read as a shrug.
  images_omitted:
    "Some images were left out because the selected model cannot read them. The rest of your message was sent.",
  image_output_rejected:
    "The model sent back a picture this app would not open. Nothing was saved, and the reply itself is unaffected.",
  image_output_remote_url_refused:
    "The model answered with a link to a picture on another server instead of the picture itself. Elysium talks to one address only, so the link was not followed.",

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
  unsupported_media_type:
    "This stored image has an unexpected format and was not shown, for safety.",
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
  // Not a problem with the clip. The folder this voice would be saved into
  // leads somewhere else on disk, so saving would write the recording into a
  // directory that is not this app's, and replacing a clip would delete
  // whatever audio is already there.
  tts_reference_folder_redirected:
    "This voice's folder points somewhere else on disk, so nothing was saved. Move or remove that link and try again.",
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
    "That passphrase is too short. Use at least 12 characters - three ordinary words are easier to remember than one mangled one.",
  // Missing from this map entirely, two lines below its own sibling: somebody
  // pasting a long generated passphrase got "Something went wrong. Please try
  // again." and pasted the exact same thing again, forever.
  passphrase_too_long:
    "That passphrase is too long. Use 512 characters or fewer.",
  // There is no login box in front of this vault. Somebody who copies the
  // folder guesses offline with no limit, so a long passphrase made of one
  // repeated idea buys nothing that its shortest piece did not.
  passphrase_too_common:
    "That is one of the first passphrases anyone guessing would try. Pick something that is yours.",
  // The fallback branch in _check_passphrase: a rule was added on the server
  // with no sentence written for it here. Reaching this means the list is
  // stale, so the wording says what to do rather than what happened.
  // Loopback is not a permission boundary, so the desktop window is given a
  // secret at launch and every request carries it. Seeing this means the page
  // was opened some other way - a stale tab, a bookmark to the port - and the
  // fix is to open Elysium again rather than anything about the vault.
  launch_token_invalid:
    "This page was not opened by Elysium. Close it and start Elysium again.",
  passphrase_invalid:
    "That passphrase cannot be used. Try a longer one made of a few unrelated words.",
  passphrase_too_simple:
    "That passphrase is long but repetitive, so it is no harder to guess than its shortest part. Try a few unrelated words instead.",
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

  // K-36. These five reached the reader as sentences typed at the call site,
  // so neither catalogue gate could see them and nothing could tell whether
  // the wording had ever been reviewed. Moved here WORD FOR WORD - the point
  // was where they lived, not what they said, and changing both at once would
  // have made the move impossible to verify.
  attachment_gate_closed:
    "Images cannot be attached right now, so that file was not added.",
  chat_background_unreadable:
    "The chat background could not be loaded, so it has been turned off.",
  tts_text_truncated:
    "The reply was too long to read in full, so the end was not spoken.",
  // The two below carry a number, so their real wording lives in
  // COUNTED_MESSAGES. These entries are what getErrorMessage returns if one is
  // ever asked for without a count - deliberately true either way rather than
  // a placeholder, because a catalogued code with no honest sentence is how the
  // gate gets weakened later to accommodate one.
  tts_lines_dropped:
    "Part of the reply could not be spoken.",
  // Not our sentence at all: the voice_notice event carries the backend's own
  // free text and this entry is only the floor under it. See the catalogue
  // record - the second sentence source is a defect in its own right.
  tts_notice:
    "The voice engine reported something about this reply.",
};

/**
 * Sentences that need a number, and the singular the number changes.
 *
 * Separated from ERROR_MESSAGES rather than being written at the call site,
 * which is where both of these used to live. `images_omitted` is the reason
 * this exists: it HAD a sentence in the map above, and a second one typed into
 * useStreamingCompletion.ts that is what readers actually saw - so the
 * catalogue gate was checking a sentence nobody could reach, and reported the
 * code as covered.
 */
const COUNTED_MESSAGES: Record<string, (count: number) => string> = {
  images_omitted: (n) =>
    n === 1
      ? "One image could not be sent with this message; the model answered without seeing it."
      : `${n} images could not be sent with this message; the model answered without seeing them.`,
  tts_lines_dropped: (n) =>
    n === 1
      ? "One line of the reply could not be spoken."
      : `${n} lines of the reply could not be spoken.`,
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
 * The sentence for a code that reports a COUNT.
 *
 * Falls back to the plain sentence when the code has no counted form, so a
 * caller that gains a number does not have to know whether one was written yet.
 */
export function getCountMessage(
  code: string | undefined | null,
  count?: number,
): string {
  if (!code) return FALLBACK_MESSAGE;
  const counted = COUNTED_MESSAGES[code];
  // No number, or no counted form for this code: the plain sentence. Both
  // fallbacks matter at one call site - reportStreamNotice handles every
  // notice code the stream can carry, and only some of them count anything.
  if (count == null || !counted) return getErrorMessage(code);
  return counted(count);
}

/** Every code whose sentence changes with a number. */
export function countedErrorCodes(): string[] {
  return Object.keys(COUNTED_MESSAGES);
}

/**
 * Check if an error code is a known backend contract code.
 */
export function isKnownErrorCode(code: string): boolean {
  return code in ERROR_MESSAGES;
}

/**
 * Every code this file has a sentence for.
 *
 * Exists so the catalogue gate can check the direction nothing checked before:
 * a sentence here for a code the backend cannot send. That is what a rename
 * leaves behind - the old key keeps its sentence, the new key has none, and
 * every other assertion stays green.
 *
 * A function rather than exporting the map, so the map stays private and
 * nobody can reach in and mutate a user-facing string at runtime.
 */
export function knownErrorCodes(): string[] {
  return Object.keys(ERROR_MESSAGES);
}
