/**
 * errorMessages.ts - Centralized backend error code → safe user-facing message map.
 *
 * Every error code from docs/frontend_contract.md is mapped here.
 * Unknown codes fall back to a generic safe message.
 * Raw upstream/provider text is never exposed through this map.
 */

import { NAME_MAX_CODE_POINTS } from "@/lib/characters/nameLimits";

const ERROR_MESSAGES: Record<string, string> = {
  // Auth / API key
  api_key_missing:
    "No API key is configured. Please add your OpenRouter API key in Settings.",
  api_key_invalid:
    "API key is invalid. Please check it and try again.",
  // Not a failure to save that happened to be reported badly: settings.py
  // returns {ok: false} on this path and does NOT store the key. The old
  // sentence said only that validation failed, so somebody who read it as
  // "saved but unverified" closed the panel with no key set at all.
  validation_unavailable:
    "The API key could not be checked because OpenRouter could not be reached, so it was not saved. Check your connection, and your proxy if you use one, then enter the key again.",
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
  proxy_alias_too_long:
    "That proxy name is too long. Please use at most 50 characters.",
  proxy_url_required:
    "A proxy URL is required. Please enter one in Settings.",
  invalid_proxy_scheme:
    "The proxy URL scheme is not supported. Use http, https, socks5, or socks5h.",
  proxy_url_invalid:
    "The proxy URL is not valid. Please check it and try again.",

  // OpenRouter
  // Was byte-identical to `timeout` below. Both codes reached this map, so
  // the two were indistinguishable to a reader; this one is the only one of
  // the pair with a real producer (openrouter.py raises it in six places), so
  // it gets the sentence that names who went quiet.
  openrouter_timeout:
    "OpenRouter did not answer in time, so nothing came back. Try again - a busy model often answers on the second attempt.",
  openrouter_rate_limited:
    "Rate limited by the provider. Please wait a moment and try again.",
  // Named the problem and stopped. The account is the only place this can be
  // fixed, so the sentence says so rather than leaving the reader to guess
  // whether Elysium has a billing screen (it does not). The provider's address
  // is NOT printed here: S-01 in static-safety.test.ts bans that literal from
  // every source file, and a sentence is not an exception to it.
  openrouter_insufficient_credits:
    "Your OpenRouter account does not have enough credits, so nothing was sent. Top up the account this API key belongs to, then try again.",
  // Q-121. Two different answers the reader must be able to tell apart:
  // "never, for this model" (the three below the first) and "not right now"
  // (openrouter_temporarily_unavailable, rate limit, timeout). A sentence that
  // says "try again" for the first kind is what made people press Enter five
  // times at a request that could not succeed.
  openrouter_no_provider_meets_privacy:
    "No provider for this model that meets Elysium's privacy rules would take this request. Sending it again will not help; choose another model.",
  model_no_private_endpoint:
    "This model has no provider that meets Elysium's privacy rules (zero data retention), so nothing was sent. Choose another model.",
  openrouter_temporarily_unavailable:
    "The privacy-safe providers for this model are busy or down right now. This usually passes: wait a moment and send again.",
  reasoning_used_reply_budget:
    "The model spent the whole reply length on thinking and wrote no answer. Raise Max tokens or turn Thinking off, then send again.",
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
  // Both suggestions in the old sentence pointed the wrong way, and the
  // second one made things worse.
  //
  // The refusal compares `min_required` against `available`. `min_required`
  // is the system block, the persona, the post-history instruction, the
  // voice block, the notebook and limits, plus THIS message - it does not
  // include the history at all, so clearing messages cannot move it by a
  // single character. And `available` is derived FROM the context budget, so
  // reducing the budget shrinks the right-hand side and makes the same
  // refusal arrive sooner.
  //
  // What this names is exactly what `min_required` is made of, plus the one
  // knob that actually helps.
  //
  // ALL of it, which the first version of this sentence did not manage: the
  // comment above listed six things and the sentence listed four, dropping
  // the post-history instruction and the message being sent. Those are the
  // two a reader can do least about by guessing, and a long paste in the
  // composer is one of the commonest ways to arrive here at all - so the
  // remedy list has to name them or it sends the reader to shorten things
  // that were never the problem.
  context_too_large:
    "This model's context is too small for what has to be sent before the "
    + "conversation even starts: the character, the persona, the "
    + "post-history instruction, the voice block, your notes and limits, "
    + "and the message you are sending now - an attached image counts as "
    + "roughly a thousand words of it. Clearing older messages will not "
    + "help, because they are not counted here, and lowering Context "
    + "budget makes it worse. Raise Context budget, shorten what you "
    + "are sending, shorten the persona or the character or the "
    + "post-history instruction, remove some notes or limits, or pick a "
    + "model with a larger window.",
  // Three codes for a rejected field, three different producers, and the
  // sentences have to say which - because only one of them knows what the
  // reader was doing.
  //
  // `validation_failed` is the BACKEND's app-level RequestValidationError
  // handler in main.py. One handler answers every rejected body in the tree,
  // so it cannot know whether it is refusing a vault passphrase, an upload, a
  // notebook entry or a generation parameter. Its sentence therefore names no
  // panel and asks for nothing destructive: the same words have to be true for
  // all four. Sending them to Generation Settings and its Reset all button is
  // what the first version of that handler did, by borrowing the code below,
  // and it pointed a reader whose image upload arrived without its file at a
  // destructive control in a panel they had never opened.
  //
  // Three more things its wording had to survive, all of them reachable. It
  // answers DELETE routes with typed query parameters, so "nothing was saved"
  // would be answering a question the reader did not ask and "nothing was
  // changed" is the clause that covers saved, deleted and sent at once. It
  // answers values nobody typed - the auto-lock radio group, an importance
  // rank, a voice enum - so the next action has to include what was CHOSEN.
  // And it answers mismatches the reader cannot see at all, so there is a
  // last-resort action for the reader who changed nothing.
  //
  // That last action is "close Elysium and start it again", copied from
  // `invalid_response_shape` below, and NOT "reload Elysium". There is no
  // reload in the packaged window: run_app.py calls `webview.start()` without
  // `debug`, so the Edge Chromium backend leaves
  // `AreBrowserAcceleratorKeysEnabled` and `AreDefaultContextMenusEnabled`
  // off and F5, Ctrl+R and the context menu are all dead. Three sentences
  // further down this file used to say "reload" anyway; they now say this,
  // and ErrorMessageCopy.test.ts sweeps every code so the next one cannot.
  // Restarting does cost the reader something - draftStore.ts scopes drafts
  // to the renderer process - so the sentence says that rather than letting
  // them find out.
  validation_failed:
    "Something in that request was not a value Elysium could accept, so nothing was changed and nothing was sent. Check whatever you last typed, chose, or attached, then try again. If it keeps happening and you changed nothing, close Elysium and start it again - unsent drafts do not survive that.",
  // `invalid_generation_params` is synthesised in the FRONTEND, in five
  // branches: client.ts:88, stream.ts:254 and uploads.ts:125 when a 422 body
  // carries an ARRAY under `detail`, and parseApiError.ts:77 and :82 for an
  // array or an object. That is a shape the handler above cannot produce - it
  // always sends a code string - so against this backend all five are
  // defensive and none of them fires. That is the whole reason this one is
  // still allowed to name a panel: the day it does fire, the 422 came from
  // something that is not our handler, and two of those branches sit on the
  // upload and dialog paths where "press Reset all" would be the wrong
  // advice. Written down rather than fixed, because a sentence for a refusal
  // this application cannot currently make would be a sentence nobody can
  // check.
  invalid_generation_params:
    "A generation setting was rejected, and the reason did not arrive in a form Elysium could read. Open Generation Settings and press Reset all, then send again.",
  // `invalid_gen_params` is the BACKEND's own 422 from
  // validate_and_filter_gen_params, which knows the value was out of range.
  // It shares a next action with the code above, because Generation Settings
  // is where either one is fixed and its Reset all button is the control that
  // clears a bad value. The two were once byte-identical and were
  // differentiated rather than merged; this is the one of the pair that a
  // route can actually reach today.
  invalid_gen_params:
    "One of the generation settings is outside the range this model accepts, so nothing was sent. Open Generation Settings and press Reset all, then send again.",
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
  // Stream notices about the reply itself, added 2026-08-18. Both were
  // counted inside the provider layer and never left it, so a piece of a
  // reply could go missing, or a reply could simply stop, and the app showed
  // a normal complete message either way.
  // Q-121: the user's stop sequences were left out of the request because no
  // privacy-safe provider for this model accepts them. Never silent.
  stop_not_sent_privately:
    "Your stop sequences were not sent: no privacy-safe provider for this model supports them, so the reply may run past where they would have cut it.",
  provider_frame_dropped:
    "Part of this reply did not arrive in a form Elysium could read, so a piece of it is missing. Nothing else was affected.",
  stream_ended_without_done:
    "The connection ended before the model said it had finished, so this reply may be cut short.",
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

  // Chat rename / create. There is no `title_required`: an emptied title box
  // is answered with a default name, so the backend has no way to raise it and
  // a sentence here would be one nobody can reach.
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
  // C4: web search variants (`:online`), the auto router, presets and the
  // searching model families stay blocked until the app has its own
  // retrieval. Raised before anything is sent.
  model_web_search_blocked:
    "This model searches the web with your conversation, and that is turned off in this app. Nothing was sent. Choose a model without web search.",
  // The model id holds the stored API key. It would go to the provider as
  // the model name, where the scrub does not reach.
  model_id_carries_api_key:
    "Stopped. The model name contains your API key, so nothing was sent and nothing was saved. Choose the model from the list again.",

  // Response / network
  // Synthesised by the frontend in two places (client.ts when a JSON body is
  // not the shape the schema expects, stream.ts when a stream ends with no
  // terminal event). Neither knows WHAT was wrong with it, so the sentence
  // does not pretend to - but "unexpected response format" told the reader
  // nothing they could act on, and the escalation is real.
  invalid_response_shape:
    "Elysium could not read the server's reply, so nothing was applied. Try again - if it keeps happening, close Elysium and start it again.",
  invalid_openrouter_completion_response:
    "The provider returned an unexpected response. Please try again.",
  network_error:
    "Could not reach the server. Please check your connection.",
  // Byte-identical to `openrouter_timeout` until now, which made the two
  // indistinguishable to a reader - and they are not the same event at all.
  // This one is a PROXY failure: proxy_health.py raises HTTPException(503,
  // health["reason"]) and "timeout" is one of its six PROXY_REASONS, meaning
  // the proxy health probe went quiet. Its own comment block records that this
  // code "was believed for months to be a client-side code with no backend
  // producer" - so the generic sentence was not just vague, it was the wrong
  // subsystem, and it sent people retrying a model that was never asked.
  timeout:
    "The proxy did not answer in time, so nothing was sent. Check that your proxy is running and reachable, then try again.",

  // Character import
  character_json_too_large:
    "This character file is too large. Please use a smaller file.",
  // Also the answer to JSON that parses in principle but not here: nesting
  // too deep, a number too long, a broken (unpaired surrogate) character.
  // Those used to be a 500 (PK-06, PK-07).
  invalid_character_json:
    "This character file could not be read as a card. It is not valid JSON, or it holds something a card cannot (a broken character, a number too long, nesting too deep). Please check the file and try again.",
  // No `character_name_required` either: a card with no name is imported and
  // named, so nothing raises it.
  // Characters and personas. An import is shortened instead, so this only
  // answers a name typed or edited in a form.
  name_too_long:
    `That name is too long. A name can be at most ${NAME_MAX_CODE_POINTS} characters.`,
  // Raised when a chat is opened and the character's greeting, with its
  // {{char}} / {{user}} placeholders filled in, would outgrow the per-field
  // ceiling. Nothing was created.
  prompt_field_too_large:
    "This character's greeting grows too large once its placeholders are filled in, so the chat was not created. Shorten the greeting and try again.",
  // Prompt modules. Imports, toggles and the two prompt settings. The upload
  // is refused before it is read when its type or size is wrong, so these
  // name what to change in the FILE, not in the app.
  prompt_module_cannot_enable:
    "This entry cannot be switched on: it is a placeholder, it is empty, it is longer than a module may be, or it only runs in a kind of generation this app does not have.",
  prompt_module_empty:
    "This file is empty. A module needs some text.",
  prompt_module_file_too_large:
    "This file is too large to import. A .txt or .md module can be at most 96 KB, and a .json file at most 1 MB.",
  prompt_module_file_type_unsupported:
    "Only .txt, .md and .json files can be imported as prompt modules.",
  prompt_module_filename_missing:
    "The file arrived without a name, so its type could not be told. Please choose the file again.",
  prompt_module_filename_invalid:
    "The file name is too long or has characters a file name cannot have. Rename the file and choose it again.",
  prompt_module_import_not_found:
    "That import no longer exists. It may have been removed with its last module.",
  prompt_module_json_invalid:
    "This JSON file could not be read as prompt modules. It is not valid JSON, or a field is missing, empty, too long or of the wrong kind.",
  prompt_module_json_is_character_card:
    "This is a character card, not a prompt module file. Import it from the characters page instead.",
  prompt_module_json_no_modules:
    "This file holds nothing that can become a module: no entry with text in it.",
  prompt_module_json_unknown_keys:
    "This module file has fields this app does not know, so nothing was imported. Remove the extra fields and try again.",
  prompt_module_json_unrecognized:
    "This JSON file is not a format this app can import as prompt modules: the app's own module file, or a SillyTavern chat preset, prompt export, master export or system prompt.",
  prompt_module_json_version_newer:
    "This module file was written by a newer version of the app. Update the app to import it.",
  prompt_module_length_required:
    "The upload did not say how large it is, so it was not read. Please choose the file again.",
  prompt_module_name_invalid:
    "A module name must have at least one visible character and be at most 64 characters long.",
  prompt_module_not_found:
    "That module no longer exists. Refresh the list and try again.",
  prompt_module_not_utf8:
    "This file is not UTF-8 text, so it could not be read. Save it as UTF-8 and try again.",
  prompt_module_request_invalid:
    "The change could not be applied because the request was malformed. Please try again.",
  prompt_module_too_long:
    "This module is too long. A module can be at most 24,000 characters.",
  prompt_module_upload_raw_only:
    "The file was sent in a way this app does not accept. Please choose the file again.",
  prompt_modules_over_budget:
    "The prompt modules that are on do not fit this chat's Context budget: together they would take more than half of what is left for the conversation. Turn a module off, or raise Context budget in Generation settings.",
  prompt_settings_invalid:
    "That prompt setting could not be saved: the reply length can only be adaptive, 150 or 300 words.",

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
  // "Missing" alone was a lie half the time. The backend now also catches a
  // file that IS in the folder but is not the size the download recorded - a
  // half-finished download - and this sentence is all the user ever sees, so
  // it has to cover both. Telling somebody a file is missing while they are
  // looking straight at it sends them after the wrong thing.
  tts_model_incomplete:
    "This voice model folder is incomplete. A file it needs is missing or did not finish downloading.",
  // Nothing is known to be missing: the T3 backbone in the folder names a
  // Chatterbox build (Turbo, Multilingual v3) that the engine Elysium bundles
  // has no loader for yet, so there is no file list to check it against and
  // the sentence above would send the reader to fetch the same files again.
  // Neither is it tts_worker_failed's "press Set up again": the engine is
  // fine. The builds it does load are named so the reader knows what to
  // fetch instead of guessing.
  tts_model_variant_unsupported:
    "The voice engine Elysium bundles cannot load this build of the model yet. For Chatterbox, pick the English or Multilingual v2 checkpoint.",
  tts_engine_unknown:
    "This voice engine is not supported yet.",
  // Setting the runtime up is Elysium's job, not the user's - so this says what
  // to press, never "edit runtimes.json".
  // The button in VoiceSettingsPage reads "Set up" and, once a setup has been
  // attempted or the runtime is broken, "Set up again". These two sentences
  // said "Set up voice" and "Set up voice again", which is not a control that
  // exists - so the reader scanned the page for a button that was right there
  // under a different name.
  tts_runtime_missing:
    "The voice engine is not set up yet. Press Set up in Settings to install it.",
  tts_runtime_broken:
    "The voice engine was set up before but its files are gone now. Press Set up again in Settings to reinstall it.",
  // Not the same sentence as tts_runtime_broken, deliberately. "Its files are
  // gone" and "its files are not the ones we installed" ask for the same
  // button but mean very different things, and collapsing them would hide the
  // second one behind a routine reinstall prompt.
  tts_runtime_untrusted:
    "The voice engine on this machine is not the one Elysium installed, so it was not started. Press Set up again in Settings to reinstall it.",
  tts_runtime_installing:
    "Voice engine setup is already running. Wait for it to finish, or cancel it first.",
  tts_runtime_install_failed:
    "Voice engine setup did not finish. Nothing was left half-installed - you can try again.",
  tts_python_not_found:
    "Elysium could not find a way to build the voice engine environment on this computer.",
  // Deliberately not tts_runtime_install_failed's "you can try again": trying
  // again changes nothing until the proxy can be read, so the sentence has to
  // name the door that is shut rather than invite another press of the button.
  tts_proxy_unavailable:
    "Elysium is set to send everything through your proxy and could not get one, so the voice engine download was not started. Unlock Elysium, check the proxy in Settings, then press Set up again.",
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
  // The three below named a problem and stopped. All three are recoverable by
  // the same act, because VoiceHost starts the worker on demand (host.py
  // _start_worker, reached from the speak path): asking again IS the retry,
  // and there is no Load button in the UI to send anyone hunting for.
  // worker_client raises tts_load_timeout after the worker goes quiet past the
  // budget and then takes it down, so nothing is left half-loaded to wait on.
  tts_load_timeout:
    "The voice model stopped answering while it loaded, so it was shut down. Press speak again - the first load of a model is the slow one, so a second attempt usually works. If it stops again, a smaller model will load.",
  tts_worker_failed:
    "The voice engine could not start, so nothing was spoken. Press speak again to retry it - if it keeps failing, press Set up again in Settings to reinstall the engine.",
  tts_worker_crashed:
    "The voice engine stopped unexpectedly. Voice is off until it is loaded again.",
  tts_worker_unavailable:
    "The voice engine is not running, so nothing was spoken. It starts again on the next request, so press speak again.",
  // Not the same advice as tts_insufficient_vram: this one ran out mid-flight,
  // so the fix is a smaller setting, not closing another program.
  tts_out_of_memory:
    "The voice model ran out of GPU memory while working. Lower its memory settings, or use a smaller model.",
  tts_synthesis_failed:
    "The voice could not be generated for this message. Press speak again, or choose a different voice model in Settings. The message itself is unaffected.",
  // The spoken reply is the conversation read aloud, so it is kept where the
  // conversation is kept. If the audio folder points somewhere else on disk,
  // nothing is written rather than something being left there forever.
  // ELYSIUM_DATA_DIR was called a "setting", which sent people looking through
  // Settings for a row that has never existed. It is read once by config.py
  // from the environment; nothing in the UI writes it, and the frontend does
  // not mention it anywhere else. The sentence now says which kind of thing it
  // is, so the reader knows to stop looking in the app.
  tts_cache_outside_data_dir:
    "The voice folder points outside Elysium's own data folder, so nothing was written there. Move it back inside the data folder - the data folder itself moves only with the ELYSIUM_DATA_DIR environment variable, not from Settings.",
  tts_reference_too_short:
    "That voice clip is too short to clone from. Around ten seconds of clear speech works best.",
  // Not a problem with the clip. The folder this voice would be saved into
  // leads somewhere else on disk, so saving would write the recording into a
  // directory that is not this app's, and replacing a clip would delete
  // whatever audio is already there.
  tts_reference_folder_redirected:
    "This voice's folder points somewhere else on disk, so nothing was saved. Move or remove that link and try again.",
  // Nothing wrong with the clip that was just sent. The previous recording is
  // open somewhere - the voice engine mid-sentence, an antivirus mid-scan -
  // and it cannot be destroyed, so the upload was refused rather than left to
  // land beside it and lose. Waiting is the whole fix; do not send anyone off
  // to record again.
  tts_reference_clip_stuck:
    "The recording already saved for this voice is in use right now, so nothing was changed. Try again in a moment.",
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
  // The failure is on this computer, not in the reply, and the last sentence
  // is there because "no audio device" next to a message reads as though the
  // message itself failed.
  tts_audio_device_error:
    "No audio output device is available, so the voice could not be played. Connect or enable an output device, then press speak again. The reply itself is unaffected.",
  tts_nothing_streaming:
    "That reply has already finished. Use the speaker button on the message to hear it.",

  internal_error:
    "Something went wrong on the server. Please try again.",
  vault_locked:
    "Elysium locked. Enter your passphrase to continue.",

  // Both of these belong to the reset route, and both are refusals of a
  // destructive request, so neither may read as a fault the user should
  // work around. The route exists for somebody who has lost their
  // passphrase; it is reachable while locked BECAUSE of that, which is
  // exactly why it says no twice.
  vault_unlocked:
    "Elysium is already unlocked, so there is nothing to reset from here. " +
    "Starting over is only offered to someone who cannot get in at all.",

  reset_confirmation_mismatch:
    "That did not match, so nothing was deleted. The words have to be typed " +
    "exactly as they are shown.",

  // The door is not refused here, it is absent: this build does not carry it.
  // A development checkout has no reset route at all, so the sentence has to
  // explain a missing feature rather than a rejected request, and it must not
  // send somebody looking for a passphrase or a permission they could fix.
  vault_reset_unavailable:
    "Starting over is only offered by the installed Elysium app, on its lock " +
    "screen. This build does not have that option.",

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
  // K-52. Not a wrong passphrase and not a broken database - each of those
  // has its own sentence, and both of them would send someone off doing the
  // wrong thing. The passphrase is right; the two halves of the vault simply
  // come from different moments, which is what restoring one of them without
  // the other does. Only whoever restored it knows which half they meant to
  // keep, so the message names the files rather than proposing a repair.
  vault_identity_mismatch:
    "That passphrase is correct, but it does not open the database that is here - the key files and the database are from different vaults. This usually means one of them was restored from a backup without the other. Put back the matching app.db, or the matching salt.bin and verifier.bin, from the same backup.",
  change_passphrase_failed:
    "The passphrase was not changed - nothing was lost, and your current passphrase still works. Close anything else using Elysium and retry.",
  // FAZ 1 - the notebook. Its rows are sent with every message, so the two
  // refusals a person actually meets are "that is too long" and "that field is
  // not yours to change" - and both have to explain the cost rather than just
  // say no.
  notebook_entry_empty:
    "That note is empty. Write something for the character to remember.",
  // "One short sentence" undersold the real cap by half: notebook_store's
  // ENTRY_MAX_CHARS is 240, which NotebookPanel mirrors as the textarea's
  // maxLength. Two or three sentences fit, and a reader who trimmed to one was
  // giving up room the app was willing to give them.
  notebook_entry_too_long:
    "That note is too long. Keep it under 240 characters - the notebook is sent with every message, so every note costs room the conversation would otherwise use.",
  // There is no type control and no importance control in the add-note row -
  // the whole row is one textarea, so this sentence pointed at two things that
  // are not on the screen. What the backend actually refuses here is a kind,
  // durability, importance or status value it does not recognise, none of
  // which the panel lets anyone type. That makes it an app-side mismatch, so
  // the sentence has to say that the words are not what was refused - and the
  // last-resort action is the one `validation_failed` uses, because the
  // packaged window has no reload to offer.
  notebook_entry_invalid:
    "That note could not be saved: something in it was not a value Elysium recognises, so nothing was written. The wording is not what it refused, so it is fine to reuse - close Elysium and start it again, then write the note once more.",
  notebook_field_not_editable:
    "That part of a note cannot be changed after it is written. Who wrote a note - you or the model - is recorded once and stays.",
  // The list has to arrive whole. Pass two of the renumber writes positions by
  // list index, so a list missing one of the chat's notes hands a number a row
  // outside the list still holds - and a drag becomes a crash.
  //
  // Retrying the same drag sends the same stale list, so "try again" on its
  // own would loop. The list only comes back whole on a fresh fetch, and
  // useNotebookMutation invalidates on success only - a refused reorder
  // leaves the panel holding exactly the list that was refused.
  notebook_reorder_incomplete:
    "The notebook changed while you were rearranging it, so nothing moved. The list on screen is behind what is stored, and dragging again would send the same stale order - close Elysium and start it again to see the notes as they are now.",
  // The one refusal that is a feature. Limits are never trimmed to make room,
  // because a limit that silently stops being sent is worse than no limit: you
  // believe it is in force, the model never sees it, and nothing reports the
  // gap. Generating is refused instead.
  boundaries_do_not_fit:
    "Your limits do not fit in this model's context, so nothing was sent. Shorten them, or choose a model with more room.",
  // "One of the two available" named a count and not the two. The router
  // accepts "en" and "tr"; the select in ExtractionSettings offers exactly
  // English and Turkce, so the sentence can simply say which.
  notebook_language_unknown:
    "The notebook's instructions come in English or Turkce only. Pick one of those two.",
  // The only limit in this app that is enforced rather than requested. The
  // sentence says so, because a user who reaches for it deserves to know it
  // worked rather than hoping the model listened.
  // The last sentence is the half that used not to be true, and it is kept
  // short on purpose: this arrives as a toast, at the worst moment somebody
  // expects to have, and a sentence that outruns its own dismiss timer is a
  // sentence nobody finishes. The panel carries the whole story. Left out
  // altogether, though, this one promises a silence the app does not keep -
  // a copy of the word in older text rides on turns you asked for now.
  //
  // "That message" rather than "what you typed": regenerate re-sends a stored
  // message as the turn, and this is the sentence it shows.
  safeword_triggered:
    "Stopped. Your safeword was in that message, so nothing was sent - not the message, not your notes, not your limits. Nothing was saved either. The same word in older text is cut out instead, and that turn still goes.",
  // C9. Checked against the stored API key only; the vault password is not
  // checked (it is kept as a slow hash, not as text), and the sentence says so.
  api_key_in_message:
    "Stopped. That message contains your API key, so nothing was sent and nothing was saved. Remove the key and send again. (Only the API key is checked this way, not your vault password.)",
  safeword_blank:
    "A space is not a safeword - it would have turned the stop off while the box still looked filled in. Clear the box completely to turn it off, or type a word.",
  safeword_too_short:
    "Too short to be safe. One or two letters appear inside ordinary words, so every message would be stopped. Three characters at least.",
  safeword_too_long:
    "A safeword has to be short enough to type in a hurry. Sixty-four characters at most.",
  notebook_daily_cap_reached:
    "The notebook has used its calls for today and stopped. It will start again tomorrow, and nothing was lost - the messages it has not read yet stay unread, not skipped.",
  // The field accepts typing, so "instead of typing it" told somebody off for
  // using the control as built. The rule it broke is a shape (_MODEL_ID wants
  // author/model), so the sentence names the shape and leaves both routes open.
  notebook_model_id_invalid:
    "That is not an OpenRouter model id. They look like author/model - pick one from the list, or type it in that form.",
  // Its sibling `model_id_too_long` is plain about the same rule. "Too long to
  // be real" called the reader's input a lie for crossing a 128 character cap.
  notebook_model_id_too_long:
    "That model id is too long. Pick one from the list.",
  // The four below are relayed from OpenRouter through the notebook's own
  // routes. They used to arrive as raw reasons with no record and no
  // sentence, so an expired key read "Something went wrong. Please try
  // again." on this panel while the chat screen named it exactly.
  notebook_extract_failed:
    "The notebook could not reach the model. Nothing was written, and this chat is unchanged.",
  openrouter_unreachable:
    "OpenRouter could not be reached. Check your connection - if you use a proxy, check that it is running.",
  openrouter_auth_failed:
    "OpenRouter rejected your API key. Open Security and enter it again.",
  api_key_not_set:
    "There is no API key saved yet. Open Security and add one before the notebook can read anything.",
  notebook_entry_not_found:
    "That note is no longer there. It may have been removed in another window.",
  // Says WHY, not just no. A refusal the reader cannot explain to themselves
  // reads as a bug, and this one is a deliberate promise: the chat came from
  // somebody else's card, so its notes are reviewed whatever the general
  // switch says. The sentence names the reason and the way out.
  imported_chat_always_reviews:
    "This chat came from an imported character card, so notes the model "
    + "writes here are always reviewed before they are used. Start a chat "
    + "from a character you wrote yourself to change that.",
  // "A name AND the wording" described a two-field form. BoundaryPanel has one
  // text field and sends its text as both label and phrasing, so the reader was
  // told to fill in a second box that is not there. The backend refuses when
  // either is blank, which through this UI can only mean the one field was
  // empty or held nothing but spaces and newlines.
  boundary_empty:
    "A limit needs some wording. Type what to keep out of the story, then add it.",
  // Severity is a select that starts on "never" and cannot be cleared, so
  // "choose how strict it is" asked for something already chosen. The backend
  // checks four enums here, not just severity, and the panel never offers an
  // invalid value for any of them - so reaching this means the app sent
  // something its own store refused. Nothing the reader can reach caused it,
  // and the packaged window has no reload, so the way out is the restart
  // `validation_failed` names.
  boundary_invalid:
    "That limit could not be saved: one of its settings was not a value Elysium recognises, so nothing was added. Nothing on the screen can be set to a value it would refuse - close Elysium and start it again, then add the limit once more.",
  boundary_not_found:
    "That limit is no longer there. It may have been removed in another window.",
  // Both ceilings are notebook_store.BOUNDARY_MAX_CHARS and
  // BOUNDARY_SET_MAX_CHARS, and both sentences have to say WHY a limit is
  // held to something stricter than a note. A note that does not fit is
  // dropped and reported; a limit that does not fit refuses the whole send,
  // because a limit silently left out is worse than no limit. So the cure is
  // named here rather than left to the reader to guess.
  boundary_too_long:
    "That limit is too long. Keep it under 160 characters - limits are sent with every message and are never trimmed to make room, so a long enough one would stop every message in this chat from being sent at all.",
  boundary_set_too_long:
    "Your limits are already as long as they can be together, so this one was not added. They are never trimmed to make room, so the whole set has to fit beside the conversation. Shorten or remove one of the limits you have, then add this one again.",
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

  // The two refusals the draft cache can raise. Both are WARNINGS, not
  // errors: nothing was lost, the previous draft is still there, and the only
  // thing that did not happen is the write that would have gone over a
  // ceiling. Wording says which ceiling, because "too large" and "no room
  // left" have different answers - shorten this one, or send some others.
  draft_too_large:
    "This draft is too long to hold. Please shorten it, or send it in parts.",
  draft_budget_exhausted:
    "There are too many unsent drafts to hold this one. Please send or clear some of them.",
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
