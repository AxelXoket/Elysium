# Elysium Frontend-Backend Contract

> **Created:** Part A (scaffold)
> **Last updated:** 2026-08-09, checked route by route against the source.
> **Status:** Living contract of record

---

## Overview

- Backend URL: `http://127.0.0.1:8787/api/v1` (dev). The packaged desktop app
  serves the same API same-origin at a relative `/api/v1`.
- CORS: only `http://127.0.0.1:5173` accepted (irrelevant same-origin in the
  packaged app)
- All endpoints return JSON
- Streaming: SSE on `/complete/stream`, `/regenerate/stream` and `/edit/stream`
- Vault gate: the database is passphrase-encrypted (SQLCipher). While locked,
  every data route answers `423 {"detail": "vault_locked"}`; only `/vault/*`
  and root-level `/healthz` respond. The frontend treats any data-route 423
  as "vault locked out from under us" and falls back to the lock screen.

---

## Privacy Rules (MANDATORY - Codex must enforce)

1. Frontend MUST NOT send `Authorization` header to backend or any external service.
2. Frontend MUST NOT send `zdr`, `data_collection`, or `allow_fallbacks` fields in any request.
3. Frontend MUST NOT store API key, proxy URL, messages, persona descriptions, or drafts in localStorage, sessionStorage, IndexedDB, or cookies.
4. Frontend MUST NOT call openrouter.ai directly under any circumstances.
5. Backend hardcodes: `zdr=true`, `data_collection="deny"`, `allow_fallbacks=false` in every OpenRouter request.
6. `context_budget_tokens` is **never** forwarded to OpenRouter. It is an app-level history-trimming budget only.

---

## Endpoint List (all /api/v1)

| Method | Path | Description | Added in |
|--------|------|-------------|----------|
| GET | /vault/status | Vault initialized/unlocked state | Part K (vault) |
| POST | /vault/init | Create the vault (first run; migrates plaintext DB) | Part K (vault) |
| POST | /vault/unlock | Unlock with the passphrase | Part K (vault) |
| POST | /vault/lock | Lock (drop the in-RAM key) | Part K (vault) |
| POST | /vault/change-passphrase | Re-key the database | Part K (vault) |
| POST | /vault/discard-plaintext-backup | Shred the pre-vault plaintext copies | Part K (vault) |
| POST | /vault/discard-orphaned-copy | Shred an encrypted copy stranded mid-migration | Part K (vault) |
| POST | /vault/discard-empty-stub | Remove the 0-byte stub crash recovery moved aside (refuses a non-empty file) | Part K (vault) |
| GET | /notebook/{chat_id} | Notes for one chat, retired rows included | FAZ 1 (notebook) |
| POST | /notebook/{chat_id} | Add a note | FAZ 1 (notebook) |
| PATCH | /notebook/entries/{id} | Edit a note's text or flags; provenance is not editable | FAZ 1 (notebook) |
| DELETE | /notebook/entries/{id} | Remove a note the user no longer wants | FAZ 1 (notebook) |
| POST | /notebook/{chat_id}/reorder | Set the order notes are sent in | FAZ 1 (notebook) |
| GET | /notebook/boundaries | The global limits | FAZ 1 (notebook) |
| POST | /notebook/boundaries | Add a limit, global or chat-scoped | FAZ 1 (notebook) |
| DELETE | /notebook/boundaries/{id} | Remove a limit | FAZ 1 (notebook) |
| GET | /notebook/{chat_id}/boundaries | Limits actually in force for this chat | FAZ 1 (notebook) |
| POST | /notebook/{chat_id}/use-global | Whether this chat follows the global limits | FAZ 1 (notebook) |
| GET | /notebook/extract/models | Models a background extraction may use. Filtered to endpoints that keep no data AND honour a strict JSON schema - a model that cannot do the job has no business being pickable and then failing at request time | FAZ 4 (notebook) |
| GET | /notebook/extract/settings | The chosen extraction model and instruction language. `model_id: null` means extraction never runs | FAZ 4 (notebook) |
| POST | /notebook/extract/settings | Choose the model and the instruction language (`en` or `tr`) | FAZ 4 (notebook) |
| POST | /notebook/{chat_id}/extract/dry-run | Run the extractor once against this chat and return what it produced, beside the text it read. **Writes nothing.** Exists so the one thing that could not be measured - whether a small model reads the user's Turkish well enough - can be looked at rather than argued about | FAZ 4 (notebook) |
| GET | /settings | Current config state (no secrets) | Existing |
| POST | /settings/api-key | Store API key (validates first) | Modified Part B |
| DELETE | /settings/api-key | Remove API key | Existing |
| POST | /settings/proxy | Store proxy config | Existing |
| POST | /settings/proxy/alias | `{proxy_alias}` - rename the configured proxy without rewriting its URL (the URL is write-only and never displayed). 400 `proxy_url_required` when none is configured. |
| POST | /settings/proxy/required | Arm/disarm the proxy kill-switch alone (400 `proxy_url_required` when no proxy is configured) | Existing |
| DELETE | /settings/proxy | Remove proxy config | Existing |
| POST | /settings/image-output | `{image_output_enabled}` - allow a model to answer with a generated picture. Off by default. Stored in the vault, not browser storage, because it changes the outgoing request. No capability check on write: whether the model selected right now can draw is decided per request from the cached catalogue. |
| POST | /settings/auto-lock | `{auto_lock_minutes}` - lock the vault after this many minutes with nothing happening; 0 disables it. Refuses anything outside 0-1440. Stored in the vault, not browser storage: a protection setting somebody can read and change without the passphrase is not one. A request in flight counts as activity, so a streamed reply is never interrupted. |
| POST | /settings/screen-privacy | `{screen_privacy_enabled}` - hide this window from screen capture and screen sharing. Off by default; the owner takes screenshots. Stored in the vault, not browser storage: a protection setting readable without the passphrase is not one. Applied on vault transitions only - the window exists before the vault is open, and a locked screen has nothing to hide. |
| POST | /settings/stop-sequences | `{stop_sequences}` - up to 4, 100 chars each, clamped rather than rejected so a stale UI cannot 422 a save. |
| GET | /settings/proxy/health | Proxy health status | Existing |
| GET | /characters | List all characters | Existing |
| POST | /characters | Create character | Existing |
| POST | /characters/import | Import JSON character card | Existing |
| GET | /characters/{id} | Get single character | Existing |
| PATCH | /characters/{id} | Edit character (partial update) | Part D |
| DELETE | /characters/{id} | Delete character + cascade | Part D |
| GET | /chats | List all chats | Existing |
| POST | /chats | Create chat session | Existing |
| GET | /chats/{id} | Get single chat | Existing |
| PATCH | /chats/{id} | Rename chat (title only) | Part G (rename) |
| GET | /chats/{id}/messages | List messages | Existing |
| POST | /chats/{id}/complete | Send message, get completion | Existing (modified B+C) |
| DELETE | /chats/{id} | Delete chat + messages | Part E |
| POST | /chats/{id}/clear | Clear messages, keep chat | Part E |
| DELETE | /chats/{id}/messages/{msg_id} | Delete target + following messages | Part E (hotfix) |
| POST | /chats/{id}/messages/{msg_id}/regenerate | Regenerate as a new variant | Part E (hotfix) |
| POST | /chats/{id}/complete/stream | Streaming variant of /complete (SSE) | Part F (streaming) |
| POST | /chats/{id}/messages/{msg_id}/regenerate/stream | Streaming variant of /regenerate (SSE) | Part F (streaming) |
| POST | /chats/{id}/messages/{msg_id}/edit | Edit a USER message; tail is swept, assistant rewrites | v1.1 (C3) |
| POST | /chats/{id}/messages/{msg_id}/edit/stream | Streaming variant of /edit (SSE) | v1.1 (C3) |
| POST | /chats/{id}/messages/{msg_id}/activate | Make a sibling variant the active reply | Part J (variants) |
| GET | /personas | List all personas (includes is_active) | Part C (hotfix) |
| POST | /personas | Create persona | Part C |
| PATCH | /personas/{id} | Edit persona | Part C |
| DELETE | /personas/{id} | Delete persona | Part C |
| POST | /personas/{id}/select | Select active persona | Part C |
| GET | /models/openrouter | List OpenRouter models | Existing |
| POST | /uploads/images | Stage an image attachment (multipart "file") | Part H (images) |
| GET | /uploads/images/{id} | Serve a stored image to the frontend | Part H (images) |
| DELETE | /uploads/images/{id} | Unstage a staged upload (message_id NULL only; 409 when linked) | v1.1 FB8 |
| GET | /healthz | Liveness probe | Existing |

---

## Error Code Reference

| HTTP Status | Detail String | Meaning | Recommended UI Action |
|-------------|---------------|---------|----------------------|
| 401 | auth_failed | OpenRouter auth failure | Prompt user to check API key |
| 401 | api_key_missing | No API key configured | Show settings panel |
| 403 | openrouter_moderation_blocked | The model's moderation refused the input. Distinct from `auth_failed` on purpose: the key is valid, the message was rejected | Suggest rewording or another model; do NOT point at the API key |
| 402 | openrouter_insufficient_credits | OpenRouter account has no credits | Show credit warning |
| 404 | chat_not_found | Chat ID does not exist | Navigate away from chat |
| 404 | character_not_found | Character ID does not exist | Refresh character list |
| 404 | persona_not_found | Explicit body persona_id does not exist (a stale settings selection is tolerated, not 404) | Refresh persona list |
| 404 | message_not_found | Message ID does not exist | Refresh messages |
| 422 | api_key_invalid | API key failed validation | Show invalid key error |
| 422 | not_last_assistant_message | Regenerate target is not the latest assistant message | Disable regenerate or refresh |
| 422 | no_preceding_user_message | No user message before assistant message | Show error |
| 422 | invalid_gen_params | Generation params failed validation | Highlight invalid param |
| 429 | openrouter_rate_limited | Rate limited by OpenRouter | Show retry-after message |
| 502 | openrouter_completion_error | OpenRouter server error | Show generic error |
| 504 | openrouter_timeout | OpenRouter request timed out | Suggest retry |
| 409 | regenerate_conflict | Chat changed while regenerating (stream) | Refresh messages |
| 409 | edit_conflict | Chat changed while the edit streamed (row edited/deleted, tail grew, chat cleared) - nothing was written | Restore pre-edit view, refresh messages |
| 409 | exchange_stale | Chat cleared/deleted while the reply streamed - finalize refused, no orphan written | Refresh messages |
| 422 | not_editable | Edit target is not a user message | Hide edit affordance |
| 500 | internal_error | Unexpected server-side failure (stream) | Show generic error |
| 400 | context_too_large | System + persona + message + PHI exceed the budget | Reduce budget/message or switch model |
| 502 | invalid_openrouter_completion_response | Malformed provider response | Show generic error |
| 400 | title_required | Rename with empty/whitespace title | Keep edit open, show hint |
| 400 | title_too_long | Rename title over 200 chars | Keep edit open, show hint |
| 400 | model_id_too_long | Create-chat model_id over 300 chars | Choose a model from the list |
| 400 | attachment_invalid | Upload is not a decodable PNG/JPEG/WebP (incl. decompression bomb) | Show attach error |
| 400 | attachment_too_large | Upload over 10 MiB | Show size hint |
| 404 | attachment_not_found | Unknown attachment id | Drop stale id, re-attach |
| 400 / 409 | attachment_unavailable | 400 on send (id already sent / duplicate id); 409 on `DELETE /uploads/images/{id}` when the id is already linked to a message | Drop stale id, re-attach |
| 400 | too_many_attachments | More than 4 attachments on one message | Enforce cap client-side |
| 400 | model_no_image_input | Attachments sent to a text-only model | Gate attach UI by modality |
| 422 | not_a_variant_target | Activate target is not an assistant message | Hide the variant control |
| 409 | variant_group_not_last | Activate/regenerate on an older group (older groups are view-only) | Refresh messages |
| 403 | cross_origin_denied | Cross-origin mutating request rejected by the CSRF shield | (Never reaches the trusted frontend) |

**Voice / TTS (`/tts/*`).** Voice is fully local: the model runs on this machine and no
audio or text leaves it. Endpoints: `GET /tts/models`, `POST /tts/rescan`,
`GET /tts/models/{uid}/schema|settings`, `POST|DELETE /tts/models/{uid}/settings`,
`POST /tts/models/{uid}/engine`, `GET|POST /tts/active`, `GET /tts/models/{uid}/readiness`
(can it run right now, with every blocker at once), `POST /tts/preflight`
(will this model fit in free VRAM right now, with these values - it only READS the
GPU, never allocates), and `GET /tts/runtimes` (per-engine runtime state; the
registry is app-owned, the user never edits it by hand).

**Voice lifecycle (V3).** `routers/tts.py` stays the pure host half - it never
spawns anything. `routers/tts_runtime.py` shares the `/tts` prefix and owns the
process:

| Method | Path | Notes |
|--------|------|-------|
| GET | /tts/state | `{state, uid, engine_id, vram_mb, error_code, error_detail, idle_seconds}`. `state` is `unloaded` / `loading` / `loaded` / `unloading` / `error`. Polling this is also what notices a worker that died on its own. |
| POST | /tts/load | Preflight and the runtime check happen BEFORE any process exists, so a refusal costs nothing. |
| POST | /tts/unload | Gives the VRAM back. |
| POST | /tts/speak | `{text?, message_id?, uid?}` -> `{audio_id, sample_rate, seconds, truncated}`. Loads first if needed. `message_id` speaks a stored message from its RAW content (delivery tags intact - the client only ever holds the stripped view). `truncated` is true when the text was cut at the 5000-char cap - never silently. |
| GET | /tts/voice-mode | `{enabled, active, prompt_chars}`. `active` = the delivery prompt would actually inject right now (toggle on AND a tag-capable engine selected); `prompt_chars` lets the context gauge charge the injected block to fixed cost exactly like the backend budget does. |
| POST | /tts/voice-mode | `{enabled}` - the global voice toggle. |
| POST | /tts/speak_stream | Same body as `/tts/speak`, answered as SSE: `voice_chunk` / `voice_notice` / `voice_error` / `voice_done`. This is what the Speak button calls - `/tts/speak` cannot make a sound until the last sentence is finished. |
| POST | /tts/speak_live | `{chat_id}` - start speaking the reply STREAMING right now. During a stream there is no message id yet, so nothing else can address it. 404 `tts_nothing_streaming` when that reply is over. |
| GET | /tts/tag-prefs | `{density, tone, min, max, tone_max_chars, speed, speed_min, speed_max, narrative, narrative_modes, gap, gap_min, gap_max}` - every delivery dial. |
| POST | /tts/tag-prefs | Any subset of `{density, tone, speed, narrative, gap}`. 422 `tts_invalid_narrative` for a mode nobody implements. |
| GET | /tts/pronunciations | `{pronunciations, max_entries, max_chars}` - the user's reading rules. |
| POST | /tts/pronunciations | `{pronunciations}` - the WHOLE table (a merge-only write could not express a deletion). |
| GET | /tts/audio/{audio_id} | The wav. Ids are confined to the cache directory. |
| GET | /tts/runtimes/{engine}/plan | What setup will do and how large the download is - shown BEFORE the user commits. |
| POST | /tts/runtimes/{engine}/install | Returns immediately; watch with the GET below. |
| GET | /tts/runtimes/{engine}/install | `{state, log, error_code, error_detail, running}`. States: `idle` / `preparing` / `installing` / `verifying` / `done` / `failed` / `cancelled`. |
| POST | /tts/runtimes/{engine}/install/cancel | Kills the job and deletes the partial environment. |
| DELETE | /tts/runtimes/{engine} | Unloads the worker first, then removes the environment. Removing the last engine also reclaims the wheel cache. |
| GET | /tts/voices | Reference voices (clone sources): `{voices: [{voice_id, label, transcript, transcript_source, seconds, has_transcript, needs_conversion}]}` |
| POST | /tts/voices/{voice_id} | Multipart upload (`file`, optional `label`/`transcript` form fields). Replaces the previous clip only after the new one validates. |
| POST | /tts/voices/{voice_id}/transcript | `{text}` - always editable; an auto transcript is a first draft (Whisper mishears). |
| POST | /tts/voices/{voice_id}/transcribe | The LOADED engine drafts the words, on this machine. Engines that do not transcribe answer with a code instead of pretending. |
| DELETE | /tts/voices/{voice_id} | Remove a voice. |

**GET /tts/active reports the LIVE state**: when the selected model is the
loaded one (or the host is in error), `state`/`vram_mb`/`error_code` come from
the running host, not from a static answer - a crashed worker is visible here.

**Two rules the frontend can rely on.** An environment is reported `ready` only
after its imports have been proven to work in that new interpreter - a green
install command is not enough. And a worker's death is never silent: the exit
code becomes a specific error (`2` -> `tts_out_of_memory`, `3` ->
`tts_runtime_broken`, anything else -> `tts_worker_crashed`), stored on the
state snapshot until the next success.

**Voice delivery tags (V4).** When voice mode is on AND the selected engine
understands inline prosody tags (Fish S2), the backend injects a system block
teaching the model `[whisper]`-style delivery directions - at CALL level, never
stored on the character, never visible in any API response. Replies are stored
RAW (tags intact, so re-speak and regenerate keep the performance) and stripped
at every door to the client: SSE deltas are stripped chunk-safely (a tag split
across two deltas never flashes on screen; markdown links are never eaten;
an unclosed bracket is released verbatim), and `GET /chats/{id}/messages`
serves the same stripped view, so streamed and refreshed text always agree.
Old rows contain no tags; stripping is the identity on them. The injected
block's length is reserved in the context budget exactly like the
post-history instruction.

**Generated audio is session-only.** It is the user's conversation in audible
form, so locking the vault unloads the model AND deletes every cached wav.

**Voice readiness.** A voice model is ALWAYS inspectable: `/schema` and
`/settings` answer on a machine with no GPU, with nothing installed, with a
half-finished download. Whether it can actually RUN is a separate verdict that
travels with it - every row of `GET /tts/models` carries `readiness`, and so
does `GET /tts/active`. A verdict has `runnable`, `settings_available` (false
only when no adapter exists, since settings come from the adapter), the
`runtime_state`, the `fit` figures, the model's real `languages`, and `issues`:
every reason at once, each with `severity` (`blocker` | `warning`), `transient`
(will it clear on its own) and `action` (`setup_runtime` | `free_vram` |
`redownload` | `change_language`). Reporting one blocker at a time - fix it,
discover the next - is the shape this deliberately avoids. Every code below turns voice off and says why - none of them
block chatting, and none are ever silent (a voice feature that fails quietly reads as
broken). All `/tts/*` routes sit behind the vault gate (`423 vault_locked`) and the CSRF
shield like every other data route.

| Status | Code | Meaning | Frontend action |
|--------|------|---------|-----------------|
| 404 | tts_model_not_found | No model in the models folder | Prompt to add one, offer rescan |
| 400 | tts_model_unknown | Referenced model uid is gone (folder changed) | Rescan, clear selection |
| 409 | tts_model_unrecognized | Folder present but no engine signature matched | Show as unrecognized, offer manual engine override |
| 422 | tts_model_incomplete | Engine matched but required files are missing | Show which files, do not offer load |
| 400 | tts_engine_unknown | Engine id not registered | Show as unsupported |
| 409 | tts_runtime_missing | The engine runtime was never set up | Offer "Set up voice" (the app installs it; the user never edits runtimes.json) |
| 409 | tts_runtime_broken | Runtime was recorded once, its interpreter is gone now | Offer "Set up voice" again, worded as a repair |
| 409 | tts_runtime_installing | A setup job is already running for that engine | Show the running job instead of starting a second |
| 500 | tts_runtime_install_failed | Setup did not finish; the partial environment was removed | Offer retry; say nothing was left half-installed |
| 409 | tts_python_not_found | No way to build an isolated environment on this machine | Explain plainly; do NOT tell the user to install things by hand |
| 409 | tts_insufficient_disk | Not enough free space for a multi-GB engine | Show how much is needed vs free |
| 500 | tts_sidecar_write_failed | The model's folder would not accept the engine marker (read-only drive/permissions) | Say the folder is not writable; do NOT suggest a rescan |
| 400 | tts_param_invalid | A setting is out of range | Highlight the field, restore last good value |
| 400 | tts_values_too_large | Settings payload exceeds the stored size cap | Reject the save |
| 409 | tts_insufficient_vram | Pre-load check says it will not fit right now | Show estimate vs free, refuse to load, invite a retry (transient) |
| 409 | tts_gpu_unavailable | No readable NVIDIA GPU on this machine | Say voice cannot run here; do NOT suggest closing GPU apps. Settings stay open |
| - | tts_language_unsupported | Warning, not a failure: the model cannot speak the requested language | Offer another language or another model; never block the model |
| 409 | tts_model_already_loading | Another load is in flight (one slot) | Disable load, show progress |
| 504 | tts_load_timeout | Load exceeded the timeout | Offer retry; first Fish load compiles and is slow |
| 500 | tts_worker_failed | Worker could not start | Surface the reason, voice stays off |
| 500 | tts_worker_crashed | Worker died unexpectedly | Toast + set voice state to error |
| 500 | tts_worker_unavailable | No worker running for this request | Offer load |
| 500 | tts_out_of_memory | The model ran out of GPU memory MID-generation (worker exit 2) | Advise a smaller cache/model - NOT closing other apps |
| 500 | tts_synthesis_failed | Generation failed for this message | Per-message error, chat unaffected |
| 400 | tts_reference_invalid | Reference clip unusable (wrong format/unreadable/too large) | Ask for a clear 10-20s clip |
| 400 | tts_reference_too_short | Clip is under the minimum length for cloning | Say how short it was; ~10 s of clear speech works |
| 400 | tts_reference_folder_redirected | The folder for this voice is a junction or a symlink, so saving would write into a directory that is not the app's | Nothing was saved; say the link has to be moved or removed |
| 409 | tts_reference_clip_stuck | The clip already saved for this voice could not be destroyed - something has the file open | Nothing was saved and nothing was lost; say to try again shortly, NOT to re-record |
| 400 | notebook_entry_empty | The note was blank once line breaks were collapsed | That note is empty. |
| 400 | notebook_entry_too_long | Over the per-entry character limit; refused rather than truncated | That note is too long. |
| 400 | notebook_entry_invalid | A field outside its allowed set (kind, durability, importance) | That note could not be saved. |
| 400 | notebook_field_not_editable | An edit tried to change provenance, chat or source; refused loudly rather than dropped silently | That part of a note cannot be changed after it is written. |
| 404 | notebook_entry_not_found | No such entry id | That note is no longer there. |
| 400 | boundary_empty | label or phrasing blank | A limit needs both a name and the wording the model will see. |
| 400 | boundary_invalid | severity outside hard/veiled/soft | That limit could not be saved. |
| 404 | boundary_not_found | No such boundary id | That limit is no longer there. |
| 500 | tts_cache_outside_data_dir | The generated-audio folder resolves outside the app's data directory | Nothing was written; say the folder has to move back, or the whole data dir with ELYSIUM_DATA_DIR |
| - | provider_frame_dropped | An SSE frame could not be parsed and its text was lost | Say a piece of the reply is missing; the rest is unaffected |
| - | stream_ended_without_done | The stream closed with no [DONE] and no finish_reason | Say the reply may be cut short |
| 400 | tts_transcript_required | This engine clones from the audio AND the words in it; the words are missing | Ask for the words. Do NOT offer to auto-transcribe: no shipped engine can (see below) |
| 409 | tts_transcribe_unsupported | The loaded engine has no speech recognition, so it cannot draft a clip’s transcript | Do not draw the button at all - gate it on capabilities.transcribes_reference. Never say the engine failed to start |
| 400 | tts_nothing_to_speak | The message had no words to read out (only markup, tags or symbols) | Say the message had nothing to read; do not offer a retry |
| 404 | tts_audio_expired | The generated wav is gone - the cache is wiped when the vault locks and never outlives the session | Offer to speak it again. Never report this as a synthesis failure or a missing audio device |
| 500 | tts_audio_device_error | No usable audio output device | Tell the user; keep text working |
| 404 | tts_nothing_streaming | Speak-live pressed, but that reply is no longer streaming | Point at the per-message speaker button instead |

**Proxy gate (503, only when `proxy_required` is on and the proxy is unhealthy):** the completion and model-list endpoints return `503` with one of these `detail` reasons: `proxy_missing`, `proxy_unreachable`, `proxy_auth_failed`, `timeout`, `unknown_error`, `proxy_unhealthy`. `proxy_auth_failed` is distinct from the OpenRouter `auth_failed` above - it means the *proxy* rejected the probe, not the API key.

**Model list / settings / import codes** (surface via the generic mapper today; friendly messages recommended): `api_key_required_by_openrouter`, `invalid_openrouter_models_response`, `openrouter_models_error` (models); `proxy_url_required`, `invalid_proxy_scheme`, `proxy_url_invalid` (proxy config); `character_json_too_large`, `invalid_character_json`, `character_name_required` (import).

> Note: `network_error` is synthesized frontend-side for transport failures (it is not returned by the backend). `openrouter_no_provider_meets_privacy` is reserved but not currently emitted - a real ZDR rejection from OpenRouter surfaces as a generic completion error.

---

## Streaming Completions (Part F)

Both streaming endpoints accept the exact same request body as their
non-streaming counterparts and return `text/event-stream` of data-only SSE
events. Every event is one line `data: {json}` followed by a blank line; the
JSON always carries a `type` field:

| type | Payload | Meaning |
|------|---------|---------|
| user_message | `message`: full message row | First event. For /complete/stream this is the just-persisted user row; for /regenerate/stream the existing preceding user row. |
| delta | `content`: string | One streamed content fragment. Repeated. |
| done | `chat_id`, `model_id`, `user_message`, `assistant_message` | Terminal success event. Rows are persisted. |
| notice | `code`: string, `count`: int? | Something the request has to disclose, sent BEFORE the first delta. Today: `images_omitted` (the model never received one or more attached pictures and answered as if they were not there), `image_output_rejected` (a picture came back but failed validation and was dropped), `image_output_remote_url_refused` (the model answered with a LINK to a picture; fetching it would be a second egress host, so it is refused rather than followed). All three are warnings, not failures; the reply still arrives. |
| error | `status`: int, `code`: string, `partial_saved`: bool? | Terminal failure event. Codes match the table above. `partial_saved` means the provider failed AFTER text had arrived and the backend KEPT it - the rows are committed, so the client must not roll its optimistic rows back. |
| voice_chunk | `audio_id`, `seconds`?, `index` | Spoken audio for one sentence. Arrives AFTER `done` - reading never waits on speaking. Fetch from `GET /tts/audio/{audio_id}`. |
| voice_notice | `note`: string | The worker telling the person something actionable (a compile that fell back to eager decoding, a cold cache). The speech is fine, just slower or different. |
| voice_error | `code`: string | The utterance stopped. A `tts_*` code the error map already has a sentence for - never prose. |
| voice_done | `count`?, `truncated`?, `dropped`?, `dropped_samples`? | Speech finished. `truncated`: the text was cut at the 5000-char cap. `dropped`: sentences that had words and produced no audio - the reply was spoken with a line missing. |

Validation failures (404s, `context_too_large`, `api_key_missing`, proxy gate)
are returned as ordinary HTTP JSON errors **before** any stream starts.

Persistence semantics:

- **/complete/stream** persists the user message before streaming. Every
  way it can end early - client abort, provider failure mid-stream,
  internal error - follows ONE rule: if visible text arrived it is kept as
  the assistant message, otherwise the user message is deleted again (a
  failed exchange leaves no half-turn and the frontend restores the draft).
  Keeping the partial for a Stop press and discarding it for a dropped
  connection used to make the reply's survival depend on which side let go
  first. When a provider failure keeps the partial the error event carries
  `partial_saved: true`, and the client keeps its rows and resyncs instead
  of rolling them back.

  "Visible text" is judged on the DISPLAY view: a reply that is nothing but
  delivery tags would render as a permanently empty bubble, so it is not
  stored - the same gate the success path applies.
- **/regenerate/stream** never touches the old assistant message until the
  full new text has streamed; the swap is atomic at `done`. Provider failure
  or client abort leaves the old message intact (partials are discarded). If
  the chat changed while streaming, a `regenerate_conflict` error event is
  emitted and nothing is modified.
- **/edit/stream** (v1.1) follows regenerate's law: nothing is written until
  the atomic swap at `done` (update the user row's content, sweep every row
  after it, insert the new assistant reply - one transaction). The first
  `user_message` event is a PREVIEW of the edited row (same id, new content);
  the DB still holds the old text until `done`. Provider failure or client
  abort leaves the chat byte-identical. If the chat changed while streaming
  (concurrent edit/send/delete/clear - checked via the row's `updated_at` +
  content + the chat's tail id captured at validate time), an `edit_conflict`
  error event is emitted and nothing is modified. The edited row keeps its
  linked images; edits cannot add or remove attachments in v1.1.

Frontend rules: stream deltas accumulate in component state (not the query
cache); the cache is only written from `user_message`/`done` events or by
refetching after abort. The Send control becomes a Stop control while a
stream is active for the selected chat.

---

## Chat Rename (Part G)

`PATCH /chats/{id}` with body `{"title": "New title"}`. The title is trimmed
server-side; the response is the full chat row (same shape as
`GET /chats/{id}`) and `updated_at` is bumped. Empty/whitespace titles →
400 `title_required`; over 200 characters → 400 `title_too_long`. Only
`title` is patchable - model/character bindings never change through this
endpoint.

---

## Image Attachments (Part H)

User messages can carry up to 4 image attachments (PNG/JPEG/WebP, 10 MiB cap,
longest side downscaled to 2048px server-side).

Storage (v0.6, E6): image BYTES live as content-addressed blobs INSIDE the
SQLCipher-encrypted database - no plaintext image file exists on disk.
`GET /uploads/images/{id}` decrypts on demand and answers with
`Cache-Control: no-store`, so the browser keeps no plaintext copies either.
A one-time unlock migration sweeps any legacy `uploads/` files into blobs.

Lifecycle:
1. `POST /uploads/images` (multipart field `file`) stages the image and
   returns `{id, mime, width, height, byte_size}`. Staged uploads older than
   24h are purged at unlock, and (v1.1 FB8) an opportunistic best-effort
   purge also runs after each successful upload. The composer's remove button
   calls `DELETE /uploads/images/{id}` to unstage immediately: staged rows
   only (404 `attachment_not_found` when gone, 409 `attachment_unavailable`
   when already linked). The client treats 404/409 as expected terminal
   states and never surfaces an error for them.
2. `POST /chats/{id}/complete[/stream]` accepts `"attachments": [id, ...]`.
   The ids are validated (existence, staging, count, model image support) and
   linked to the persisted user message. Message rows everywhere now include
   `"attachments": [{id, mime, width, height}]` (empty for text-only).
3. A failed or empty-aborted streaming send UNLINKS the attachments (ids stay
   valid); the client keeps them staged alongside the restored draft and a
   retry re-sends the same ids.
4. Deleting messages/chats/characters deletes the attachment rows AND, in
   the same transaction, any blob no remaining row references.

Provider payload: on vision-capable models (input_modalities includes
"image"), messages with attachments become OpenRouter content-part arrays -
`[{type:"text",...}, {type:"image_url","image_url":{"url":"data:<mime>;base64,..."}}]`.
On text-only models images are silently omitted from the payload (history
with images never breaks a text model). Budgeting: each image costs a flat
IMAGE_TOKEN_ESTIMATE = 1100 tokens in the history-trim math (keep the
frontend estimator in sync).

Frontend rules: gate the attach UI by the selected model's input modalities;
render via `GET /uploads/images/{id}`; never build `image_url` parts client-
side; never persist attachments in browser storage.

---

## Model List `fallback_reason` Values

When `/models/openrouter` falls back to the public list, `fallback_reason`
may be `timeout`, `http_<status>` (e.g. `http_500`), or a Python exception
class name such as `ConnectError`. Treat it as an opaque diagnostic string:
map the known prefixes and fall back to a generic "primary source
unavailable" label for anything else. Never render it verbatim as user-facing
copy.

---

## Completion Request Schema

```json
{
  "message": "string (required, non-empty after strip)",
  "model_id": "string (required, non-empty)",
  "generation_params": {
    "temperature": "float 0.0-2.0 | null",
    "top_p": "float 0.0-1.0 | null",
    "top_k": "int 0-131072 | null",
    "repetition_penalty": "float 0.001-2.0 | null",
    "max_tokens": "int 1-131072 | null",
    "seed": "int | null",
    "stop": "string or [string] | null"
  },
  "persona_id": "int | null (optional; overrides selected_persona_id from settings)",
  "context_budget_tokens": "int | null (optional; range 512-2000000; NOT forwarded to OpenRouter)"
}
```

---

## Generation Parameters (backend allowlist)

| Param | Type | Range | Forwarded to OpenRouter? | Notes |
|-------|------|-------|--------------------------|-------|
| temperature | float | 0.0-2.0 | Yes, if model supports it | Filtered by supported_parameters |
| top_p | float | 0.0-1.0 | Yes, if supported | Filtered |
| top_k | int | 0-131072 | Yes, if supported | Filtered |
| min_p | float | 0.0-1.0 | Yes, if supported | Filtered |
| top_a | float | 0.0-1.0 | Yes, if supported | Filtered |
| frequency_penalty | float | -2.0-2.0 | Yes, if supported | Filtered |
| presence_penalty | float | -2.0-2.0 | Yes, if supported | Filtered |
| repetition_penalty | float | 0.001-2.0 | Yes, if supported | Filtered |
| max_tokens | int | 1-131072 | Yes, if supported | Also used for output reservation |
| seed | int | -(2^31)-(2^31-1) | Yes, if supported | Filtered |
| stop | str or [str] | non-empty | Always forwarded | Never filtered (backend keeps `stop` regardless of supported_parameters) |
| context_budget_tokens | int | 512-2000000 | **NO** | App-level budget only |

Note: `context` (bare) is not a generation param and is never accepted.

---

## What Goes to OpenRouter (exhaustive)

- Current user message text
- Selected chat history trimmed to effective context budget
- Selected character system block (text fields only)
- Selected persona system block (if any)
- Voice delivery-tag system block, ONLY while voice mode is on and the
  selected engine reads inline directions. One extra `system` message per
  request, charged to the context budget like the others. It asks the model
  to mark *how* lines are performed; the marks are stripped before display.
- Validated and model-filtered generation params
- Hardcoded provider policy: `{zdr: true, data_collection: "deny", allow_fallbacks: false}`
- `model` string
- `stream: false` (`true` on the four SSE endpoints)

## What Does NOT Go to OpenRouter (exhaustive)

- `context_budget_tokens` (app-level only)
- Inactive persona text
- Inactive character text
- Character avatar/image data
- `avatar_path`
- `raw_json`
- API key (except in Authorization header - not payload)
- Proxy URL or alias
- UI preferences (tab state, sidebar state, model search text)
- Unsent drafts
- Frontend-sent `zdr`, `data_collection`, `allow_fallbacks`
- `tools`, `tool_choice`
- `response_format` **from the frontend** - it is sent on exactly one backend
  path, the notebook's note extractor, and only ever as a fixed schema defined
  in this repository. Nothing the frontend sends can add it to any request,
  and no request carrying a conversation to a chat model carries it. Until
  v1.2 nothing sent it at all and this list said so without the qualifier
- Any stored-but-not-selected data
- `image_url` - EXCEPT images the user explicitly attached (Part H): the
  backend builds `image_url` content parts (base64 data URLs) only for
  user-attached images on vision-capable models. The frontend still never
  constructs or sends `image_url` itself.

---

## Request/Response Examples

### POST /chats/{id}/complete

**Request:**
```json
{
  "message": "Hello, how are you?",
  "model_id": "openai/gpt-4",
  "generation_params": { "temperature": 0.8, "max_tokens": 1024 },
  "persona_id": null,
  "context_budget_tokens": null
}
```

**Response (200):**
```json
{
  "chat_id": 1,
  "model_id": "openai/gpt-4",
  "user_message": { "id": 10, "chat_id": 1, "role": "user", "content": "Hello, how are you?", "created_at": "..." },
  "assistant_message": { "id": 11, "chat_id": 1, "role": "assistant", "content": "...", "created_at": "..." }
}
```

### POST /settings/api-key

**Request:** `{ "api_key": "sk-or-v1-..." }`

**Response (200, valid):** `{ "ok": true, "key_status": "valid" }`
**Response (200, unavailable):** `{ "ok": false, "key_status": "validation_unavailable" }`
**Response (422, invalid):** `{ "detail": "api_key_invalid" }`

Note: Unexpected validation errors also return `validation_unavailable` (not stored). Raw key is never returned or logged.

### POST /personas

**Request:** `{ "display_name": "Sarcastic", "description": "Always respond sarcastically." }`
**Response (201):** `{ "id": 1, "display_name": "Sarcastic", "description": "...", "is_active": false, "created_at": "...", "updated_at": "..." }`

### GET /personas

**Response (200):**
```json
[
  { "id": 1, "display_name": "Sarcastic", "description": "...", "is_active": true, "created_at": "...", "updated_at": "..." },
  { "id": 2, "display_name": "Formal", "description": "...", "is_active": false, "created_at": "...", "updated_at": "..." }
]
```

`is_active` is derived from `settings.selected_persona_id` at response time. It is NOT a DB column. Only one persona can be active at a time. Both `GET /personas` and `PATCH /personas/{id}` return the correct derived `is_active`.

### PATCH /characters/{id}

**Request:** `{ "description": "Updated description" }`
**Response (200):** Full character object with updated field

### DELETE /characters/{id}

**Response (200):** `{ "ok": true }`
Cascades to delete all chats and messages for that character.

### POST /chats/{id}/messages/{msg_id}/regenerate

**Request:** `{ "model_id": "openai/gpt-4" }`

**Response (200):**
```json
{
  "chat_id": 1,
  "model_id": "openai/gpt-4",
  "user_message": { "id": 10, "chat_id": 1, "role": "user", "content": "existing user message", "created_at": "..." },
  "assistant_message": { "id": 12, "chat_id": 1, "role": "assistant", "content": "new assistant reply", "created_at": "..." }
}
```

Key contract points:
- `user_message.id` is the **existing** row ID (unchanged, not re-inserted)
- `assistant_message.id` is a **new** row ID. The previous reply is NOT deleted:
  since Part J it is deactivated and stays navigable as a sibling variant
- Total message count GROWS by one per regenerate. The response also carries
  `deactivated_message_id` (the take that just stepped aside) and `notices`
- No duplicate user message is ever created
- Only the **latest** message in the chat can be regenerated, and it must be `role=assistant`

**Errors:**
- `422 not_last_assistant_message` - target is not the latest message or is not assistant role
- `422 no_preceding_user_message` - no user message exists before the target assistant message

### DELETE /chats/{id}/messages/{msg_id}

**Response (200):**
```json
{ "ok": true, "deleted_count": 3 }
```

Deletes the target message **and all following messages** in the same chat. The cut starts at the target's VARIANT GROUP, not at the target's own id (`start_id = variant_group or id`), so deleting one take of a reply removes its siblings too - including ones with a lower id. Deleting half a group would leave the survivors pointing at an anchor that is gone. Does not affect other chats.

**Errors:**
- `404 chat_not_found`
- `404 message_not_found`

### POST /chats/{id}/clear

**Response (200):**
```json
{ "ok": true, "deleted_count": 5 }
```

Deletes all messages in the chat. The chat shell is preserved with `updated_at` refreshed.

**Errors:**
- `404 chat_not_found`

---

## Model Metadata Fields

12 fields from `_normalise_model()`: `id`, `name`, `description`, `context_length`, `max_completion_tokens`, `supported_parameters`, `input_modalities`, `output_modalities`, `pricing`, `top_provider`, `created`, `canonical_slug`.

Codex uses:
- `supported_parameters` → disable/blur unsupported param controls
- `context_length` → display context limit; use as default budget if `context_budget_tokens` not set
- `input_modalities` → show/hide modality indicators
- `output_modalities` → show/hide modality indicators

---

## Persona Rules

- Only the selected persona (identified by `selected_persona_id` in settings) is injected into generation.
- Inactive personas are **never** sent to OpenRouter.
- `persona_id` in request body overrides the settings-level selection for that request only.
- Persona description is NOT logged by backend.
- Persona system block format (v1.1): a `[User Persona: {display_name}]` header,
  with the trimmed description on the following line when non-empty; a name-only
  persona still injects the header. Appended as the second system message after
  the character block, before history.
- A stale `selected_persona_id` (persona deleted out from under the setting) is
  tolerated: the completion proceeds WITHOUT a persona and the backend logs a
  warning (id only, no PII). `404 persona_not_found` is raised ONLY when the
  request body carries an explicit `persona_id` that does not exist.
- Missing or null persona is silently omitted - not an error.
- `GET /personas` includes `is_active: bool` derived from `settings.selected_persona_id`. Not a DB column.

---


### The notebook's extraction routes (FAZ 4)

`prompt_price` is **USD per MILLION prompt tokens**, converted at the backend
boundary. OpenRouter quotes it per token; passed through raw, every cheap model
renders as `$0.000` and the price column stops distinguishing anything, which
is the whole reason it is on screen. Do not confuse it with the `/models`
catalogue's `pricing` object, which IS OpenRouter's native per-token value.

```
GET /notebook/extract/models
-> { "models": [ { "id": "vendor/slug",
                   "provider": "Name" | null,
                   "prompt_price": 0.06,          // USD per 1M prompt tokens
                   "context_length": 131072 | null,
                   "endpoints": 3 } ] }           // how many providers serve it
```

`endpoints: 1` means the model is pinned to one machine: provider fallback is
off for this call, so when that machine is down extraction simply stops.

```
GET  /notebook/extract/settings
-> { "model_id": "vendor/slug" | null, "prompt_language": "en" | "tr" }

POST /notebook/extract/settings
<- { "model_id"?: "vendor/slug", "prompt_language"?: "en" | "tr" }
-> { "ok": true }
```

`model_id: null` means extraction never runs. There is deliberately no
default: a background job spending somebody's credits on a model they never
chose is not a convenience. A `model_id` is shape-checked (`author/slug`, at
most 128 characters) - `notebook_model_id_invalid` / `_too_long`.

```
POST /notebook/{chat_id}/extract/dry-run       // writes NOTHING
-> { "model_id": ..., "prompt_language": ...,
     "source": "the exact text it read",
     "raw": "the model's reply, verbatim" | null,
     "proposals": [ { "text", "evidence", "kind", "durability",
                      "importance", "supersedes" } ],
     "dropped": 3,
     "dropped_by_reason": { "ungrounded": 2, "too_long": 1 },
     "failure": "truncated" | null,
     "usage": { "tokens_in", "tokens_out", "cost",
                "request_id", "finish_reason" } }
```

`failure` and an empty `proposals` list are **different answers**. A truncated
or unusable reply is a failure; `[]` with `failure: null` is the model
legitimately finding nothing, which for a quiet scene is correct. Collapsing
the two is the single most expensive wound this design inherits.

`dropped_by_reason` exists because one integer cannot tell "a quote was
invented" - the defence working - from "a Turkish quote failed a byte
comparison" - the defence eating a true fact.

Both routes that reach OpenRouter pass `enforce_proxy_gate()` first and are
counted against `NOTEBOOK_DAILY_CALL_CAP`, which refuses with **429
`notebook_daily_cap_reached`** *before* the request rather than after it.


## Character Rules

- Only the character matching `chat.character_id` is fetched and injected.
- `raw_json` is never returned by any endpoint and never included in payload.
- `avatar_path` (if present on character) is a relative path for Codex to construct avatar URL.
- Avatar image data is **never** sent to OpenRouter.
- No `image_url` content from CHARACTER data in any message - user-attached
  images are the sole sanctioned source of image parts (see Part H).

---

## API Key Validation Status

- `POST /settings/api-key` response:
  - `{ok: true, key_status: "valid"}` - key stored
  - `{ok: false, key_status: "validation_unavailable"}` - NOT stored; network/proxy/unexpected failure
  - HTTP 422 + `{detail: "api_key_invalid"}` - NOT stored; 401/403 from OpenRouter `/api/v1/key` endpoint
- Unexpected validation errors are logged with exception type only (never raw key or response body)

---

## Empty Chat Heuristic

- A chat with `message_count=1` where that single message has `role='assistant'` is considered empty/unstarted.
- This is the `first_mes` greeting, not user-initiated.
- Backend does not add a separate flag; Codex derives it from `message_count` and first message role.

---

## Selected Persona ID

- `GET /settings` includes `selected_persona_id: int | null`
- `POST /personas/{id}/select` updates it
- Deleting selected persona clears it (returns null in settings)

---

## Active Context Preview

Deferred. The `/complete` payload rules above define exactly what would be sent.

---

## Codex Implementation Notes

- Use TanStack Query for all API calls (query keys like `["settings"]`, `["chats"]`, `["personas"]`, etc.)
- Invalidate relevant query keys after mutations (e.g., invalidate `["chats", chatId, "messages"]` after complete/regenerate/delete)
- Handle all error codes in the table above with appropriate UI feedback
- Never store secrets in browser storage
- The `context_budget_tokens` slider should map to model's `context_length` range (512-context_length)
- Persona selector should show all personas from `GET /personas` and use `is_active` field directly to highlight the selected one
- `PATCH /personas/{id}` response also includes correct derived `is_active` - safe to use directly
- Character PATCH form should only send changed fields (partial update)
- DELETE operations should confirm with user before executing
- Model selector should use `supported_parameters` to enable/disable generation param controls
- Regenerate button should only be shown on the **latest** message if it is `role=assistant`
- Message delete removes target + all following messages - Codex should refresh the message list after delete
- Clear chat removes all messages - Codex should refresh messages after clear
- `deleted_count` is the stable response field for both message delete and chat clear
