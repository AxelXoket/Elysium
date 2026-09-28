# Elysium Frontend-Backend Contract

> **Last updated:** 2026-09-05, checked route by route and code by code against
> the source and against `shared/error_catalogue.json`.
> **Status:** Living contract of record

---

## Overview

- Backend URL: `http://127.0.0.1:8787/api/v1` (dev). The packaged desktop app
  serves the same API same-origin at a relative `/api/v1`.
- CORS: only `http://127.0.0.1:5173` accepted (irrelevant same-origin in the
  packaged app)
- All endpoints return JSON
- Streaming: SSE on `/complete/stream`, `/regenerate/stream` and `/edit/stream`.
  Voice has its own SSE pair, `/tts/speak_stream` and `/tts/speak_live`.
- Vault gate: the database is passphrase-encrypted (SQLCipher). While locked,
  every data route answers `423 {"detail": "vault_locked"}`; only `/vault/*`
  and root-level `/healthz` respond. The frontend treats any data-route 423
  as "vault locked out from under us" and falls back to the lock screen.

---

## Privacy rules (mandatory)

1. The frontend never sends an `Authorization` header, to the backend or to anything else.
2. The frontend never sends `zdr`, `data_collection` or `allow_fallbacks` in any request.
3. The frontend never puts the API key, the proxy URL, messages, persona descriptions or drafts in localStorage, sessionStorage, IndexedDB or cookies.
4. The frontend never calls openrouter.ai directly, under any circumstances.
5. The backend hardcodes `zdr=true`, `data_collection="deny"`, `allow_fallbacks=true` on every OpenRouter completion request, and `require_parameters=true` unless a chat request body sets it. `zdr` limits OpenRouter to endpoints with a zero data retention policy and `data_collection` to providers that do not collect data. `allow_fallbacks` lets OpenRouter use a backup provider from that filtered list, for the same model, when the one it picked is unavailable; it relaxes neither filter. Model fallback is a separate `models` parameter and no request carries one, so failover does not swap the model.
6. `context_budget_tokens` is **never** forwarded to OpenRouter. It is an app-level history-trimming budget only.

---

## Endpoint List (all /api/v1)

| Method | Path | Description | Added in |
|--------|------|-------------|----------|
| GET | /vault/status | `initialized` and `unlocked`, plus every stray artefact the app tracks by name so each can carry its own notice and button: `orphaned_copy`/`orphaned_copy_readable`, `plaintext_backups`, `empty_stub`, `rotation_backups`, and `premigrate_backup`/`premigrate_backups`/`premigrate_backup_readable` - a stale pre-migration snapshot of the whole vault that an uploads migration can leave behind indefinitely, encrypted under the same key as the live vault, and whether it opens under the key currently held. Every `_readable` field is `null` while locked, because the question needs the key to answer | Part K (vault) |
| POST | /vault/init | Create the vault (first run; migrates plaintext DB) | Part K (vault) |
| POST | /vault/unlock | Unlock with the passphrase | Part K (vault) |
| POST | /vault/lock | Lock (drop the in-RAM key) | Part K (vault) |
| POST | /vault/change-passphrase | Re-key the database | Part K (vault) |
| POST | /vault/discard-plaintext-backup | Shred the pre-vault plaintext copies. `{removed: [string], left: [string], shared: [string]}`. `left` means try again; `shared` means the file cannot be removed from in here at all and deleting it by hand will not remove the data either. Told as one list the second reads as the first and sends people off closing programs. Needs no unlock: the file is readable without the passphrase | Part K (vault) |
| POST | /vault/discard-orphaned-copy | Shred an encrypted copy stranded mid-migration. `{removed: bool, reason: string}` | Part K (vault) |
| POST | /vault/discard-empty-stub | Remove the 0-byte stub crash recovery moved aside; the size is re-checked at removal time, so a non-empty file is refused. `{removed: bool, reason: string}` | Part K (vault) |
| POST | /vault/discard-premigrate-backup | Shred the stale pre-migration snapshot named by `premigrate_backup`. Requires unlocked (`423 vault_locked` otherwise): the file is encrypted, so only the key can tell a redundant copy from one written under a different passphrase. Response `{removed: bool, reason: string}`; `reason` is `not_present`, `different_key` or `in_use` when `removed` is false, empty on success. Does not touch the separate `.unreadable-<timestamp>` copy an unopenable snapshot is moved aside to - nothing smaller than a full `/vault/reset` removes that one | Part K (vault) |
| POST | /vault/reset | The "forgot your passphrase" answer: there is no recovery, so starting over is the only honest response to a lost one. Body `{confirm: string}`, checked against a phrase only the backend decides (`"DELETE EVERYTHING"`, trimmed, no case-folding or synonyms), so a frontend bug cannot fire this with an empty or near-miss string. Reachable only while LOCKED: `409 vault_unlocked` if the vault is open, checked before the phrase is even read, because a vault that can be unlocked does not need this door. A mismatched phrase answers `422 reset_confirmation_mismatch` and deletes nothing. Response `{ok: bool, left: [string]}` - `left` names any artefact still on disk afterward (something has it open); an empty list is a complete wipe. TTS engine runtimes and model downloads are deliberately NOT wiped - they are reprovisionable software, not user data. The route answers `404 vault_reset_unavailable` and does nothing unless this is the packaged app AND the launch-token gate is armed, so a development checkout has no reset door at all. | Part K (vault) |
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
| GET | /notebook/safeword | The phrase that stops the turn being sent. Empty means off | Since 1.1.5 (notebook) |
| POST | /notebook/safeword | Set or clear it. **The only limit in this app enforced in code**, and it has two halves. The message BEING SENT is matched before the provider is called, and when it matches nothing is sent and nothing is stored (`400 safeword_triggered` from every completion route). That includes the stored message a regenerate re-sends, so arming a word can refuse regenerates on chats that already contain it. The same word ANYWHERE ELSE in an outgoing body - older history, a note, a character card, a `stop` sequence - does NOT stop the turn: `safeword_filter.scrub` removes it in both payload builders in `openrouter.py`, a message the cut empties is dropped from `messages`, an emptied `stop` entry is dropped, and the rest of the body goes. Model ids, provider policy, response schemas, modality lists and `data:` URIs are scanned and left alone. Neither half rewrites the vault: the word stays in the stored row, in the note, and on screen | Since 1.1.5 (notebook) |
| GET | /notebook/worker | What the background extractor has done. Optional `?chat_id=` is purely additive: it fills `worker.chat` and the top-level `chat_refusal` and changes nothing else, so a panel with no chat selected asks the same question it always did. `stats` is install-wide. `chat_refusal` is `"model_not_extractable"` only while such a refusal of that chat stands: among the chat's refusal rows newer than its last read (`done`, `failed` or `running`), at most `STANDING_REFUSAL_ROWS` of them, newest first, any one names the model the chat resolves to now (the row's work key is recomputed with the current model). So a chat that went back to a model it was already refused on is still told. Otherwise, with no `chat_id`, or if that check itself fails (logged, the rest of the body answered as usual), it is null. The Background notes card says the unfit-model sentence from this field only and never from the install-wide `stats.skip_reasons` count. See "The extraction worker's status" below for the response shape | FAZ 5 (notebook) |
| POST | /notebook/worker/reset | Lift a tripped or stopped circuit breaker by hand. No body means the global button: the process-wide refusal is lifted AND every per-chat one is dropped. `{"chat_id": N}` unsticks exactly that conversation and leaves the rest refusing. Answers `{ok: true, worker}` with the same `worker` block `GET /notebook/worker` serves. Without this route, recovering from a provider outage means restarting the whole application | FAZ 5 (notebook) |
| GET | /notebook/extract/plan/{chat_id} | The preview of a reading job, and it spends nothing. Query `scope` (`next` / `all` / `range`, required, 422 without it) and, for `range`, `from_message_id` and `to_message_id` (400 `notebook_range_invalid` when they run backwards or fall outside the chat; 404 `chat_not_found` when the chat is not there). Answers `ready`, `blocked_by`, the counts, `calls_planned`, `daily_cap` (the day's ceiling, the same number `GET /notebook/worker` reports), `backfill_cap` (what a job may claim: the day's ceiling minus `ELYSIUM_NOTEBOOK_LIVE_RESERVE`), `live_reserve`, `calls_spent_today`, `fits_today`, `prompt_price`, `cost_estimate` (null when the price is unknown, never a fake zero), `model_id` and `model_is_chat_bind`. See "Reading a chat on request" below | IP-01 (notebook) |
| POST | /notebook/extract/run/{chat_id} | Start a reading job. Body `{scope, from_message_id?, to_message_id?}`. `{started: true, job_id, calls_planned}` and the job runs on as its own task; or `{started: false, reason}` - a refusal, HTTP 200, never an error, with one of the ten reasons below. `next` reads ONE unit and writes a job row so it can be undone (it is the one-press read the removed sweep route used to be, decision 69 a); `all` loops until nothing is unread up to the last message present when the job started (3.13), or a stop lands, re-asking the ceiling every turn - a message written during the job is left to the ordinary reader; `range` reads the chosen stretch again, read or not. 404 `chat_not_found`, 400 `notebook_range_invalid` | IP-01 (notebook) |
| POST | /notebook/extract/stop/{chat_id} | Stop the running job after the unit in flight. `{stopped: true, job_id, calls_spent}` or `{stopped: false, reason: "nothing_running"}`, which is the tenth refusal, not an error. 404 `chat_not_found` | IP-01 (notebook) |
| POST | /notebook/extract/undo/{job_id} | Remove exactly the notes one job wrote. `chat_id` is a REQUIRED query parameter and the cross-chat gate: a job that is not this chat's answers 404 `notebook_job_not_found`, the same answer as a job that does not exist. A job still running answers 409 `notebook_backfill_running`. Notes the reader has since made theirs are left alone and counted back, each once: `{job_id, removed, kept_pinned, kept_edited, kept_accepted, kept_older}` (pinned now; words changed, `edited_at`, or a suggestion touched before the vault recorded marks; Keep pressed, `hand_accepted_at`, on a suggestion or on the saved-notes card (`POST .../keep`); accepted before the vault recorded who accepts). A note the automatic switch accepted and nobody touched is removed | IP-01 (notebook) |
| POST | /notebook/{chat_id}/auto-accept | Decide whether THIS chat keeps model-written notes without asking, or hand the decision back to the global switch with `enabled: null`. The column behind it had exactly one writer for most of its life - the chat INSERT, which sets it to 0 for a chat opened from an imported card - so until now a chat that was wrongly trusted stayed trusted for its whole life. It matters most for the case the import signal cannot see: somebody who pastes a downloaded card's fields into the form by hand leaves no record that the text came from outside, and no signal in the application can know that. `GET /notebook/auto-accept?chat_id=` reports the same decision back as `effective`, with `overridden` saying whether the chat or the global switch decided it | FAZ 3 (notebook) |
| GET | /notebook/auto-accept | Whether proposals are accepted without review. **Unset is ON** - the default, not "off" | FAZ 5 (notebook) |
| POST | /notebook/auto-accept | Turn review on or off. A chat opened from an imported card overrides this to OFF for itself, whatever the global says | FAZ 5 (notebook) |
| POST | /notebook/entries/{id}/accept | Promote one proposal. Changes `status` and nothing else - `provenance` stays `model` forever | FAZ 5 (notebook) |
| POST | /notebook/entries/{id}/keep | Keep a note the automatic switch already accepted (the saved-notes card's "Keep"). `chat_id` REQUIRED, same scope as accept. Stamps `hand_accepted_at` once on an accepted, not retired note; a second press does not restamp. Returns the row. "Undo this run" then keeps the note, and the card does not ask again | Notes tab (notebook) |
| GET | /notebook/extract/models | Models a background extraction may use. Filtered to endpoints that keep no data AND honour a strict JSON schema - a model that cannot do the job has no business being pickable and then failing at request time | FAZ 4 (notebook) |
| GET | /notebook/extract/settings | The chosen extraction model and instruction language. `model_id: null` means extraction never runs | FAZ 4 (notebook) |
| POST | /notebook/extract/settings | Choose the model and the instruction language (`en` or `tr`) | FAZ 4 (notebook) |
| POST | /activity | The activity tracker's ping: a person did something. No body in, **204 with no body** out; the backend records the activity in its vault gate before the handler runs, so the handler itself does nothing and `vault_state.touch()` is not called. Sent at most once every `PING_MIN_GAP_S` (30) seconds and only after a real input event (the definition is under `POST /settings/auto-lock`, below). Not under `/vault`, so a locked vault answers `423 vault_locked` and its clock does not move; not in the backend's exempt set, since its whole job is to move the clock. There is no server-side rate limit on it this turn: the client's 30-second gap is the only limit, and a limit in the handler would limit nothing because the clock is stamped before the handler runs. | IP-07 (activity tracker) |
| GET | /settings | Current config state (no secrets). Includes `auto_lock_default_minutes`, the backend's own default for a vault nobody configured | Existing |
| POST | /settings/api-key | Store API key (validates first) | Modified Part B |
| DELETE | /settings/api-key | Remove API key | Existing |
| POST | /settings/model-selection | Remember which model the composer is set to, INSIDE the vault. `{selected_model_id: string | null}`, clamped to `SELECTED_MODEL_ID_MAX_CHARS`; `null` clears it. The body model rejects unknown fields, so a wrong field name is a 422 rather than a silent clear. It lives in the vault rather than in the browser because a model id such as `author/slug` is a NAME a person reads on screen. The frontend hydrates it once after unlock and pushes every later change; while the vault is locked there is nothing on disk to read. |
| POST | /settings/api-key/check | Ask OpenRouter whether the key ALREADY STORED is still accepted, without it being retyped. No body. `{key_status}` is one of `valid`, `invalid`, `validation_unavailable`, `not_set` - a rejection is a 200, not a 4xx, because the check succeeded; only the key failed. `invalid` and `validation_unavailable` are opposite facts and must never share a sentence in the UI. Passes `enforce_proxy_gate` like every outbound path; the key is never returned or logged. |
| POST | /settings/proxy | Store proxy config | Existing |
| POST | /settings/proxy/alias | `{proxy_alias}` - rename the configured proxy without rewriting its URL (the URL is write-only and never displayed). An empty, blank, null or absent name is NOT a delete: it stores the default name `Proxy 1`. Max 50 characters, else 400 `proxy_alias_too_long`. 400 `proxy_url_required` when none is configured. |
| POST | /settings/proxy/required | Arm/disarm the proxy kill-switch alone (400 `proxy_url_required` when no proxy is configured) | Existing |
| DELETE | /settings/proxy | Remove proxy config | Existing |
| POST | /settings/image-output | `{image_output_enabled}` - allow a model to answer with a generated picture. Off by default. Stored in the vault, not browser storage, because it changes the outgoing request. No capability check on write: whether the model selected right now can draw is decided per request from the cached catalogue. |
| POST | /settings/auto-lock | `{auto_lock_minutes}` - lock the vault after this many minutes with nothing happening; 0 disables it. Refuses anything outside 0-1440. Stored in the vault, not browser storage: a protection setting somebody can read and change without the passphrase is not one. **Activity, defined once here; every other mention refers to this.** Activity is any `/api/v1` request that is not one of the polled routes the backend exempts (`/vault/status`, `/notebook/worker`, `/tts/state`, `/tts/active`, `/tts/runtimes/{engine}/install`), stamped by the vault gate when the request arrives and again when it finishes, plus a request still in flight, so a streamed reply is never interrupted. Since the activity tracker, real input also counts through `POST /activity`: a click, a key, a wheel, a touch, focus arriving, and pointer movement or hover over the focused, visible window at least `MOVE_THRESHOLD_PX` from the last accepted point. Programmatic scrolling does not count, and neither does anything while the vault is locked. `GET /settings` also serves `auto_lock_default_minutes`, the backend's own default, so the screen can say what a vault nobody configured gets. |
| POST | /settings/screen-privacy | `{screen_privacy_enabled}` - hide this window from screen capture and screen sharing. Off by default; screenshots of this app get taken. Stored in the vault, not browser storage: a protection setting readable without the passphrase is not one. Applied on vault transitions only - the window exists before the vault is open, and a locked screen has nothing to hide. |
| POST | /settings/stop-sequences | `{stop_sequences}` - up to 4, 100 chars each. A list longer than that is clamped rather than rejected, so a stale UI sending a few entries too many cannot 422 a save. The clamp bounds what is KEPT, not what is READ, though, so there is a second ceiling above it: a body past `MAX_STOP_SEQUENCES_READ` (sixteen times the four that are kept) is refused with `validation_failed` before the route walks it. A stale dialog is still clamped; the walk is the part that stops being open-ended. Entries are counted, bytes are not. |
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
| 422 | api_key_invalid | OpenRouter rejected the key on `POST /settings/api-key`, so it was not stored. The same code is a 401 when the notebook's model list relays it | Show invalid key error |
| 422 | not_last_assistant_message | Regenerate target is not the latest assistant message | Disable regenerate or refresh |
| 422 | no_preceding_user_message | No user message before assistant message | Show error |
| 422 | invalid_gen_params | Generation params failed validation | Highlight invalid param |
| 429 | openrouter_rate_limited | Rate limited by OpenRouter | Show retry-after message |
| 502 | openrouter_completion_error | OpenRouter server error | Show generic error |
| 504 | openrouter_timeout | OpenRouter request timed out | Suggest retry |
| 400 | model_no_private_endpoint | Refused before sending: the model has no zero-data-retention endpoint, or none that takes `max_tokens` (Q-121). Nothing was stored | Say it will not work for this model; point at another model, not at Enter |
| 503 | openrouter_no_provider_meets_privacy | OpenRouter answered 404: no endpoint passed the privacy and parameter filters. Never retried | Same as above |
| 502 | openrouter_temporarily_unavailable | A passing failure (408, 502, 503, 504, a dropped connection) after the one automatic retry was spent or not allowed | Say it usually passes; a resend is fine |
| 502 | reasoning_used_reply_budget | The reply came back empty, cut off by `length`: the budget went on thinking. Never retried | Suggest a larger Max tokens or Thinking off |
| 409 | regenerate_conflict | Chat changed while regenerating (stream) | Refresh messages |
| 409 | edit_conflict | Chat changed while the edit streamed (row edited/deleted, tail grew, chat cleared) - nothing was written | Restore pre-edit view, refresh messages |
| 409 | exchange_stale | Chat cleared/deleted while the reply streamed - finalize refused, no orphan written | Refresh messages |
| 422 | not_editable | Edit target is not a user message | Hide edit affordance |
| 500 | internal_error | Unexpected server-side failure (stream) | Show generic error |
| 400 | context_too_large | Even with all history dropped the fixed blocks do not fit: character, persona, post-history instruction, voice-delivery block, both notebook blocks, the limits block, and the message being sent | Reduce budget/message or switch model |
| 502 | invalid_openrouter_completion_response | Malformed provider response | Show generic error |
| 400 | title_too_long | Rename title over 200 chars | Keep edit open, show hint |
| 400 | model_id_too_long | Create-chat model_id over 300 chars | Choose a model from the list |
| 400 / 411 | attachment_invalid | 400 when the upload is not a decodable PNG/JPEG/WebP (decompression bombs included) or declares an unreadable `Content-Length`; 411 when it declares none at all, which is the chunked POST that would otherwise slip past the size shield | Show attach error |
| 400 | attachment_too_large | Upload over 10 MiB | Show size hint |
| 404 | attachment_not_found | Unknown attachment id | Drop stale id, re-attach |
| 415 | unsupported_media_type | `GET /uploads/images/{id}` found a stored mime outside PNG/JPEG/WebP and refused to serve it. The row exists and the bytes are there, which is why it is not a 404 | Do not retry; the image cannot be rendered |
| 400 / 409 | attachment_unavailable | 400 on send (id already sent / duplicate id); 409 on `DELETE /uploads/images/{id}` when the id is already linked to a message | Drop stale id, re-attach |
| 400 | too_many_attachments | More than 4 attachments on one message | Enforce cap client-side |
| 400 | model_no_image_input | Attachments sent to a text-only model | Gate attach UI by modality |
| 422 | not_a_variant_target | Activate target is not an assistant message | Hide the variant control |
| 409 | variant_group_not_last | Activate/regenerate on an older group (older groups are view-only) | Refresh messages |
| 400 | boundaries_do_not_fit | The limits block will not fit in the context budget. Generation is refused rather than degraded - a standing rule that is silently trimmed is worse than one that never went | Say which limits are too long for this model's budget |
| 400 | proxy_url_required | A proxy-only setting was saved with no proxy configured (`/settings/proxy/alias`, `/settings/proxy/required`) | Point at the proxy field |
| 400 | proxy_alias_too_long | Proxy name over 50 characters | Keep the field open, show the cap |
| 403 | cross_origin_denied | Cross-origin mutating request rejected by the CSRF shield | (Never reaches the trusted frontend) |
| 403 | launch_token_invalid | The packaged app's launch-token gate rejected the request | (Never reaches the trusted frontend) |

**Voice / TTS (`/tts/*`).** Voice is fully local: the model runs on this machine and no
audio or text leaves it. Endpoints: `GET /tts/models`, `POST /tts/rescan`,
`GET /tts/models/{uid}/schema|settings`, `POST|DELETE /tts/models/{uid}/settings`,
`POST /tts/models/{uid}/engine`, `GET|POST /tts/active`, `GET /tts/models/{uid}/readiness`
(can it run right now, with every blocker at once), `POST /tts/preflight`
(will this model fit in free VRAM right now, with these values - it only READS the
GPU, never allocates), and `GET /tts/runtimes` (per-engine runtime state; the
registry is app-owned, the user never edits it by hand).

**`GET /tts/models` and `POST /tts/rescan` answer with the same payload:**
`{models, unrecognized, roots, truncated, truncated_roots, unreadable_roots}`.
`models` are the folders an engine claimed, each row carrying its `readiness`
(below); one real folder is one row, whichever spelling or junction reached
it, listed under the nearest configured root above the real folder whichever
root's walk got there first. `unrecognized` are folders that
hold files and matched no engine, each `{path, reason, code}` with `code`
always `tts_model_unrecognized`; a folder under a listed folder of the same
walk is not a second row, and HuggingFace cache bookkeeping (`blobs`, `refs`,
`trees`, `.no_exist`, `.locks`, `xet`, `.cache`) is never listed or entered. `roots` are the
folders the scan set out to walk, in order, after normalisation: `~` and
`%VAR%` in `extra_roots` are expanded, a relative root is anchored to the data
folder (not the working directory, which differs between development and the
exe), and the same folder spelled twice is one root. Two roots that nest are
both walked, each with its own depth and directory budget, and the wider walk
steps around the inner root, so a model is listed once, under the nearest
root above it, and carries that root's uid. With more than one root
configured, a folder reached through a junction that lands in another root,
or below one, is left to that root's walk as well, so for a folder under
some configured root the uid depends neither on the root order nor on a
junction in an unrelated root. A folder outside every configured root that
two roots reach through junctions is listed by whichever walk arrives first,
and its uid follows that walk's root. Nesting is not symmetric for the
uids: adding a root above an existing one changes nothing under the inner
root, while adding a root inside one moves the folders under the new root
onto its uid. When the new root is itself the model, the backend remembers
the uid it had under the wider root and rewrites a saved selection to the
new uid once, on the first read that resolves the saved selection (`GET
/tts/active`, or a speak or load sent without a uid in its body), carrying
the model's saved values with it, so
the client never sees the old uid resolve to nothing; a settings route
called with the old uid still answers for the model and saves under its
current uid. A folder reached through a junction that points outside every
root is identified by the path the scan reached it by, so two such folders
of one name are two rows with two uids; the uid such a folder had in 1.1.6
(by its bare folder name) is remembered the same way, so a selection saved
then is rewritten once rather than lost, and two folders of one name share
that remembered uid, which resolves to the first row, the one 1.1.6 loaded.
A junction that is itself a root inside a wider root remembers the uid it had
under that root by its configured spelling. A root that is itself a model
folder is listed as that model, and a root that holds files no engine
claims is listed under `unrecognized` only when nothing under it is a model
or a row of its own, by any walk: a hub with a `version.txt` at its top
lists the snapshot below it, not itself, also when the snapshot belongs to
an inner root or sits behind a junction into another root; the default drop
folder is the one exception, always walked as a container and never judged
itself.
`truncated` is true when at least one root's
walk hit the per-root directory cap, and `truncated_roots` names those roots
in the same spelling as `roots`: the list may be missing models under them
and must not be shown as complete. `unreadable_roots` names every configured
root the scan could not open (does not exist, is a file, sits on a drive that
is gone, or exists but cannot be listed), in the normalised spelling it
tried; the default drop folder not existing yet is the normal fresh state and
is not reported. The settings page renders each as one sentence that names
the folders.

**Voice lifecycle (V3).** `routers/tts.py` stays the pure host half - it never
spawns anything. `routers/tts_runtime.py` shares the `/tts` prefix and owns the
process:

| Method | Path | Notes |
|--------|------|-------|
| GET | /tts/state | `{state, uid, engine_id, vram_mb, error_code, error_detail, idle_seconds}`. `state` is `unloaded` / `loading` / `loaded` / `unloading` / `error`. Polling this is also what notices a worker that died on its own. |
| POST | /tts/load | Preflight and the runtime check happen BEFORE any process exists, so a refusal costs nothing. |
| POST | /tts/unload | Gives the VRAM back. |
| POST | /tts/speak | `{text?, message_id?, chat_id?, uid?}` -> `{audio_id, sample_rate, seconds, truncated}`. Loads first if needed. `message_id` speaks a stored message from its RAW content (delivery tags intact - the client only ever holds the stripped view), and it is only ever read inside `chat_id`: ids run in one sequence across the whole vault, so an id on its own would name a row in any conversation. `message_id` without `chat_id` is refused at the body with 422 `validation_failed`, before the vault is read at all; an id that is not in the named chat gets exactly the answer an id that exists nowhere gets, 404 `message_not_found`, so a refusal never says which of the two it was, and the engine is resolved before the row is read so the answer does not differ by chat when no model is selected either. Both ids are capped at what SQLite can hold; wider is `validation_failed`, not a driver error. The scope is argument consistency rather than an authorization wall (anything that can post here can already list the chats); what it buys is that a stale or wrong id speaks nothing instead of the wrong conversation. `text` names no chat - it reads no row. `truncated` is true when the text was cut at the 5000-char cap - never silently. |
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
| GET | /tts/runtimes/{engine}/install | `{engine_id, state, log, error_code, error_detail, started_at, finished_at, running}`. `log` is the last 40 lines. States: `idle` / `preparing` / `installing` / `verifying` / `done` / `failed` / `cancelled`. `running` is true for `preparing`, `installing`, `verifying` and `uninstalling` - do not derive it client-side, the last of those four is a state this route never reports. |
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
| 422 | tts_model_incomplete | Engine matched but the folder is incomplete: a required file is absent, or a file the download manifest recorded is not the size it recorded (a half-finished download) | Say both cases in one sentence, do not offer load. `detail` names the files but is diagnostic English, not user-facing text |
| 409 | tts_model_variant_unsupported | Engine matched, but the T3 backbone names a build the bundled engine has no loader for (Chatterbox Turbo, Multilingual v3, or a name it does not know). Its files are not checked: there is no list to check them against. Raised by readiness and by the worker at load | Say the engine cannot load this build yet and name the ones it can; do NOT offer redownload, nothing is known to be missing. `detail` names the build but is diagnostic English, not user-facing text |
| 400 | tts_engine_unknown | Engine id not registered | Show as unsupported |
| 409 | tts_runtime_missing | The engine runtime was never set up | Offer "Set up voice" (the app installs it; the user never edits runtimes.json) |
| 409 | tts_runtime_broken | Runtime was recorded once, its interpreter is gone now | Offer "Set up voice" again, worded as a repair |
| 409 | tts_runtime_untrusted | The recorded interpreter is outside the folder Elysium installs into, carries no fingerprint, or no longer matches the one taken at install. It was NOT run | Offer "Set up voice" again. Deliberately not the same sentence as tts_runtime_broken: "gone" and "changed" send the user to the same button and mean different things |
| 409 | tts_runtime_installing | A setup job is already running for that engine | Show the running job instead of starting a second |
| 500 | tts_runtime_install_failed | Setup did not finish; the partial environment was removed | Offer retry; say nothing was left half-installed |
| 409 | tts_python_not_found | No way to build an isolated environment on this machine | Explain plainly; do NOT tell the user to install things by hand |
| 409 | tts_proxy_unavailable | A mandatory proxy could not be read or is not configured, so nothing was downloaded at all | Say the proxy is the reason and point at unlocking or at Settings; do NOT offer a bare retry |
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
| 404 | notebook_entry_not_found | No such entry id, or one that has already been retired. One status on every handler: the notebook's refusal helper carries a fixed set of missing-thing codes to 404 and everything else to 400 | That note is no longer there. |
| 400 | notebook_reorder_incomplete | The id list did not cover exactly this chat's notes; refused rather than partially applied | Nothing was moved. |
| 400 | notebook_language_unknown | Instruction language outside `en`/`tr` | Not one of the two available. |
| 400 | notebook_model_id_invalid | Not shaped like an OpenRouter model id (`author/slug`) | Pick one from the list. |
| 400 | notebook_model_id_too_long | Over 128 characters | Pick one from the list. |
| 404 | notebook_job_not_found | `POST /notebook/extract/undo/{job_id}`: no such job, or a job that belongs to another chat than the `chat_id` given - answered the same way on purpose, so job ids cannot be counted from another chat | That run is not here to undo. |
| 409 | notebook_backfill_running | The job an undo names is still being driven by this process. Stop first, then undo | Say it is still reading; offer Stop. |
| 400 | notebook_range_invalid | A `range` request whose ids run backwards or fall outside the chat's active messages, on the preview or the run | Ask for a first and a last message that are both in the chat. |
| - | model_not_extractable | Not an error code: a `blocked_by` token on the preview, a refusal reason on the run route, and a skip reason on the turn-driven reader, written once per chat and model while that model keeps being refused rather than once per turn, and again after a turn that checked the chat's model and found it fit, whether or not that turn read anything (a turn stopped before that check, by a job holding the chat, an open breaker or no model, does not count), after a restart, or once the pair has aged out of the reader's bounded memory (3.8), so it reaches `stats.skip_reasons` (install-wide, not rendered for this reason) and, for the chat on screen, `chat_refusal` on `GET /notebook/worker`, which is what the Background notes card says. A bound model refused because the list could not be fetched at all is NOT this reason: that skip row, `blocked_by` token and run refusal is `model_list_unavailable`, with its own sentence that does not blame the model. A model the reader PICKED is never `model_list_unavailable`: with the list out of reach it is refused only when the catalogue says it lacks `structured_outputs`, and that is `model_not_extractable`. It is answered from the extraction model list, which the backend now FETCHES before it decides rather than waiting for the picker to have been opened - that list is public, costs no model tokens and is the same one `GET /notebook/extract/models` serves. For the chat's own model under the binding that list is the ONLY evidence taken: the general model catalogue is a separate cache answering a weaker question (`structured_outputs` alone, where the list is zero-retention AND `structured_outputs`), so a bound model the list does not carry is refused even when the catalogue has heard of it, and a bound model is refused while the list cannot be reached at all, because nothing vetted that one. A model the reader picked came off the filtered list when they picked it, so it is let through and the catalogue stays its fallback. A CLOSED PROXY GATE COUNTS AS UNREACHABLE: that fetch is an outbound request like any other and passes `enforce_proxy_gate` first, so with "require a proxy" armed and no healthy proxy it is not sent at all and a bound model stays unvetted. Nothing could have been sent in that state anyway, so what is given up is the reason code rather than a read. The panel can read this as checked | Say what the preview says. |
| - | notebook_daily_cap_reached | The daily ceiling on extraction calls is spent. Claimed BEFORE the request, so no call was made. **No HTTP status**: the claim happens inside the worker, never on a request the client is waiting on, so it arrives as a `stats.skip_reasons` key on `GET /notebook/worker` rather than as a response code | It starts again tomorrow; nothing was lost. |
| 400 | imported_chat_always_reviews | `POST /notebook/{chat_id}/auto-accept` tried to raise the review shield on a chat opened from an imported card. `enabled: null` is refused too - it hands the chat back to the global switch, whose default is ON, which lowers the shield just as surely | Do not offer the toggle on an imported chat; say why it is fixed. |
| 400 | safeword_blank | The safeword was whitespace only. An empty string still means OFF; a space does not, and a field that shows a space while the stop is disarmed is the one silence this control cannot have | Say a space is not a safeword. |
| 400 | safeword_too_short | Under 3 characters once whitespace is collapsed. A single letter matches inside almost every sentence and makes the app unsendable | Ask for a longer word. |
| 400 | safeword_too_long | Over 64 characters, measured after collapsing runs of whitespace | Shorten it. |
| 401/502/504 | *relayed provider reasons* | `GET /notebook/extract/models` is the one notebook route that reaches OpenRouter, and it relays the provider's own reason rather than a literal. **401**: `api_key_invalid`, `api_key_not_set`, `openrouter_auth_failed`, `api_key_required_by_openrouter`. **502**: `proxy_auth_failed`, `openrouter_unreachable`, and the fallback `notebook_extract_failed`. **504**: `openrouter_timeout`. Note that `api_key_invalid` is a 401 here and a 422 on `POST /settings/api-key`; the code is one, the route decides the status | Each has its own sentence; none is a bare 502 |
| 404 | chat_not_found | The chat is gone. One code, one status, on every notebook route as well as on the chat routes. `POST /notebook/{chat_id}` used to answer 400 for it; that split is closed | That chat is no longer there. |
| 400 | boundary_empty | label or phrasing blank | A limit needs both a name and the wording the model will see. |
| 400 | boundary_too_long | `label` or `phrasing` over 160 characters. Checked here and not only in the request schema, because a ceiling in the schema is a ceiling the next caller of `create_boundary` does not have | Shorten it; that one field is over the limit. |
| 400 | boundary_set_too_long | Adding this limit would push its WHOLE block (global limits, plus this chat's own if the limit is chat-scoped) over 1500 characters - the ceiling the limits system refuses to send rather than silently drop from. Measured against the heaviest chat's own set when the new limit is global, since a global limit lands in every chat's block | Say which limit was too big for the set; shorten or remove one before adding another. |
| 400 | boundary_invalid | severity, polarity, on_violation or rating_ceiling outside its allowed set. `source: inferred` with `severity: hard` is refused by the DATABASE, not by this check | That limit could not be saved. |
| 404 | boundary_not_found | No such boundary id | That limit is no longer there. |
| 409 | tts_cache_outside_data_dir | The generated-audio folder resolves outside the app's data directory | Nothing was written; say the folder has to move back, or the whole data dir with ELYSIUM_DATA_DIR |
| - | provider_frame_dropped | An SSE frame could not be parsed and its text was lost | Say a piece of the reply is missing; the rest is unaffected |
| - | stream_ended_without_done | The stream closed with no [DONE] and no finish_reason | Say the reply may be cut short |
| 400 | tts_transcript_required | This engine clones from the audio AND the words in it; the words are missing | Ask for the words. Do NOT offer to auto-transcribe: no shipped engine can (see below) |
| 409 | tts_transcribe_unsupported | The loaded engine has no speech recognition, so it cannot draft a clip's transcript | Do not draw the button at all - gate it on capabilities.transcribes_reference. Never say the engine failed to start |
| 400 | tts_nothing_to_speak | The message had no words to read out (only markup, tags or symbols) | Say the message had nothing to read; do not offer a retry |
| 404 | tts_audio_expired | The generated wav is gone - the cache is wiped when the vault locks and never outlives the session | Offer to speak it again. Never report this as a synthesis failure or a missing audio device |
| - | tts_audio_device_error | No usable audio output device. Raised in the BROWSER, by the player, when the audio context will not open - the backend has no producer for it | Tell the user; keep text working |
| 404 | tts_nothing_streaming | Speak-live pressed, but that reply is no longer streaming | Point at the per-message speaker button instead |

**Vault codes (`/vault/*`).** These are the only routes that answer while the
vault is locked, so they are the only ones with an error vocabulary of their
own. Every one of them is a literal raise site.

| Status | Code | Meaning | Frontend action |
|--------|------|---------|-----------------|
| 401 | wrong_passphrase | Unlock was tried with the wrong passphrase. Nothing is counted, nothing is locked out | Keep the field, say the passphrase did not open it |
| 422 | passphrase_too_short | Below the minimum length | Show the rule, keep what was typed |
| 422 | passphrase_too_long | Above the maximum length | Show the rule, keep what was typed |
| 422 | passphrase_too_common | On the refused-list of known passphrases | Ask for one nobody else has used |
| 422 | passphrase_too_simple | Passes the length rule and nothing else | Say what is missing |
| 422 | passphrase_invalid | Rejected for a reason none of the four above names | One sentence, no guessing at which rule |
| 409 | vault_already_initialized | `POST /vault/init` on a vault that exists | Go to the unlock screen |
| 409 | vault_not_initialized | Unlock or change-passphrase before there is a vault | Go to the first-run screen |
| 409 | vault_identity_mismatch | The database on disk is not the one this vault's identity file names | Do not offer a retry; this needs the right file back |
| 409 | encrypted_db_without_identity | An encrypted database is present with no identity file beside it | Same; a retry changes nothing |
| 500 | vault_init_failed | Creating the vault did not finish | Say nothing was created; offer retry |
| 500 | vault_unlock_failed | Unlock failed for a reason that is not a wrong passphrase | Distinct sentence from `wrong_passphrase`; never blame the passphrase |
| 500 | change_passphrase_failed | Re-keying did not finish. The old passphrase still opens the vault | Say the old passphrase still works |
| 409 | vault_unlocked | `POST /vault/reset` while the vault is open. Checked before the phrase is read | Lock first, or do not offer reset at all |
| 422 | reset_confirmation_mismatch | The confirmation phrase did not match. Nothing was deleted | Keep the dialog open |
| 404 | vault_reset_unavailable | This is not the packaged app, or the launch-token gate is not armed | Do not draw the reset door |
| 423 | vault_locked | Any data route while the vault is locked | Fall back to the lock screen |

**Proxy gate (503, only when `proxy_required` is on and the proxy is unhealthy):** the completion and model-list endpoints return `503` with one of these `detail` reasons: `proxy_missing`, `proxy_unreachable`, `proxy_auth_failed`, `timeout`, `unknown_error`, `proxy_unhealthy`. `proxy_auth_failed` is distinct from the OpenRouter `auth_failed` above - it means the *proxy* rejected the probe, not the API key.

**Model list / settings / import codes**: `api_key_required_by_openrouter`,
`invalid_openrouter_models_response`, `openrouter_models_error` (models);
`invalid_proxy_scheme`, `proxy_url_invalid` (proxy config);
`character_json_too_large`, `invalid_character_json` (import).

**Where a status comes from.** `shared/error_catalogue.json` records a
`statuses` array that is machine-derived from LITERAL raise sites only, so `[]`
there means "no literal site", not "no status". Most codes above reach the
client through a mapping instead: `_ERROR_MAP` in `completions.py` for provider
failures, `_refuse` in `notebook.py` (404 for a missing thing, 400 for
everything else), `_STATUS` plus `_provision_error` in `tts_runtime.py` (409 by
default, 500 for a code ending in `failed`). Those statuses are as fixed as a
literal; they are just not visible to the enumerator. The statuses in the
tables above are the ones the client actually receives.

**Codes the backend never sends.** These are synthesised in the browser and
carry the same sentences as everything else: `network_error` (transport
failure), `invalid_response_shape` and `invalid_generation_params` (a body that
is not the shape the schema expects), `attachment_gate_closed` (a drop refused
before any upload), `chat_background_unreadable`, `draft_too_large`,
`draft_budget_exhausted`, `tts_text_truncated`, `tts_lines_dropped`,
`tts_notice`, `tts_audio_device_error`, and `validation_unavailable` (a
`key_status` value, not a failure). `openrouter_no_provider_meets_privacy` is
mapped to 503 and is produced by the HTTP STATUS alone (Q-121): a 404 from the
completion endpoint, or an error chunk carrying code 404, is OpenRouter's
empty candidate pool, the same answer on every attempt. No message text is
read for it. A 503 stays a passing failure (below). OpenRouter's error table describes 503 as "There is no
available model provider that meets your routing requirements", and the typed
`error.metadata.error_type` it documents for provider errors gives 503 to
`provider_overloaded` as well, so the status alone does not separate the two.
Its router metadata guide shows a "No Providers Available" example as a 404
with the reason in `error.message`. Opt-in router metadata
(`X-OpenRouter-Metadata: enabled`) reports `openrouter_metadata.attempt: 0` when the request never
reached a provider, typically because every candidate was filtered out. This
app requests no metadata and reads neither the message nor any substring of
it.

**The one automatic retry (Q-121).** Before any delta or image has reached the
client, a failure the provider layer marks as passing (HTTP 408/429/502/503/504,
the same codes in an error chunk, a dropped connection, an empty reply that was
not cut off by `length`) is sent once more, identical body, after `Retry-After`
(at most 4 s; a longer one is not retried) or 0.75 s. A timeout, the
first-token one included, is never retried: the reader has already waited it
out once.
A 503 is retried only when the preflight counted at least one private endpoint.
Two attempts in total. No second user or assistant row is written; the only
trace is `retried` on the terminal frame.

---

## Streaming Completions (Part F)

All three streaming endpoints accept the exact same request body as their
non-streaming counterparts and return `text/event-stream` of data-only SSE
events. Every event is one line `data: {json}` followed by a blank line; the
JSON always carries a `type` field:

| type | Payload | Meaning |
|------|---------|---------|
| user_message | `message`: full message row | First event. For /complete/stream this is the just-persisted user row; for /regenerate/stream the existing preceding user row. |
| delta | `content`: string | One streamed content fragment. Repeated. |
| done | `chat_id`, `model_id`, `user_message`, `assistant_message`, plus three context numbers described under "The `done` frame's context report", and `retried`: int? | Terminal success event. Rows are persisted. `retried` is present only when the backend made its one automatic retry before any delta (Q-121); the non-streaming responses carry the same field. |
| notice | `code`: string, `count`: int? | Something the request has to disclose. Six codes: `stop_not_sent_privately` (the user's stop sequences were left out because no zero-data-retention provider for this model accepts them; sent BEFORE the first delta), `images_omitted` (the model never received one or more attached pictures and answered as if they were not there), `image_output_rejected` (a picture came back but failed validation and was dropped), `image_output_remote_url_refused` (the model answered with a LINK to a picture; fetching it would be a second egress host, so it is refused rather than followed) - those three are sent BEFORE the first delta - and `provider_frame_dropped` / `stream_ended_without_done`, which arrive when they happen. All six are warnings, not failures; the reply still arrives. |
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
`GET /chats/{id}`) and `updated_at` is bumped. An empty or whitespace-only
title is NOT refused: it is a rename to the next default name, `New Chat N`,
picked server-side from the titles that exist at that moment (smallest free
number). `title` is still a required KEY - a body without it is a malformed
rename and answers 422. Over 200 characters -> 400 `title_too_long`. Only
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

## Message `truncated` Field

Every message object (GET /chats/{id}/messages, and the `user_message`/
`assistant_message` objects returned or streamed by `/complete`,
`/regenerate`, `/edit` and their `/stream` variants, plus `/activate`)
carries a `truncated: bool`. It is only ever true on an assistant row, and
means the reply was cut off before the model reached a natural stop rather
than finishing the sentence it was on:

- the provider reported a token-ceiling finish reason (`length` or an
  equivalent upstream spelling),
- the connection closed without ever saying how it ended (no `[DONE]`, no
  finish reason at all), or
- the reader disconnected or the provider dropped mid-stream and the partial
  text already received was kept instead of discarded.

A user message, and the character's first_mes greeting, are always `false` -
neither one is model output. Render this as a visible "cut off" marker on the
bubble; do not treat it as an error, the text that is there is real and was
kept on purpose.

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
    "stop": "string or [string] | null",
    "thinking": "bool (optional, default false; strict: a string is 422)"
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
| stop | str or [str] | List capped at `MAX_STOP_SEQUENCES_READ` entries; a longer body is refused with `validation_failed` before the walk. An empty entry inside the list is `invalid_gen_params`. An empty string, or an empty list, is dropped in silence. The string form has no length cap here. | Always forwarded | Never filtered (backend keeps `stop` regardless of supported_parameters) |
| context_budget_tokens | int | 512-2000000 | **NO** | App-level budget only |

Note: `context` (bare) is not a generation param and is never accepted.

`generation_params.thinking` (bool, strict, default false) is the Thinking switch (D3), not a forwarded parameter, so it is not in the table above. For a model whose reasoning is optional, true sends no `reasoning` field (the provider default) instead of `{"enabled": false}`. A mandatory or unknown model gets its usual `reasoning` value whatever it says. It never reaches OpenRouter under its own name.

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
- Hardcoded provider policy: `{zdr: true, data_collection: "deny", allow_fallbacks: true, require_parameters: true}`. A chat request body may set `require_parameters`; nothing sets the other three. The notebook reader sends the same policy with no override.
- `model` string
- `stream: false` (`true` on the three chat SSE endpoints)

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
- A `models` list: OpenRouter is never asked to try a model other than the one selected
- `tools`, `tool_choice`
- Blocks the backend adds that the frontend never sends and cannot
  influence: the **limits** block (a `system` block of standing rules,
  counted into the budget and never trimmed - if it does not fit,
  generation is refused with `boundaries_do_not_fit` rather than degraded),
  the **notebook** block of what the user wrote, in the `system` message
  beside the persona, and a second, separately labelled notebook block of
  what the MODEL wrote, which rides in the last `user` message after the
  user's own text, under a header that calls it unverified notes with less
  authority than the user (C3 a, Q-126; it used to be a `system` block
  beside the post-history instruction). Both notebook blocks carry a random
  per-payload tag in their open and close markers, and the header tells the
  model the block ends only at the line carrying that same tag
- `response_format` **from the frontend** - it is sent on exactly one backend
  call site, the notebook's note extractor, and only ever as a fixed schema defined
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

**Response (200, valid):** `{ "ok": true, "key_status": "valid" }` - stored.
**Response (200, unavailable):** `{ "ok": false, "key_status": "validation_unavailable" }` - NOT stored; a network, proxy or unexpected failure.
**Response (422, invalid):** `{ "detail": "api_key_invalid" }` - NOT stored; OpenRouter's `/api/v1/key` answered 401 or 403.

`invalid` and `validation_unavailable` are opposite facts and must never share a
sentence. An unexpected validation error is logged with the exception type only:
the raw key and the response body are never returned and never logged.

### POST /personas

**Request:** `{ "display_name": "Sarcastic", "description": "Always respond sarcastically." }`
**Response (201):** `{ "id": 1, "display_name": "Sarcastic", "description": "...", "is_active": false, "created_at": "...", "updated_at": "..." }`

`display_name` is OPTIONAL. Empty, whitespace-only, null or absent all mean
the same thing and none of them is refused: the persona is stored as
`New Persona N`, where N is the smallest free number among the display names
that exist at that moment. The same rule applies on `PATCH /personas/{id}` -
a display name present and blank is a rename to the default, while one left
out of the body leaves the stored name alone. `POST /characters`,
`POST /characters/import` and `PATCH /characters/{id}` behave identically with
`New Character N`, and `POST /chats` with `New Chat N`.

Uniqueness is a courtesy at creation time, not an invariant: no name column in
the schema carries a UNIQUE constraint, so two rows may hold the same name and
nothing stops them. What the server declines to do is CREATE a collision it
can see.

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

## Prompt modules

User files imported into the vault; an enabled module goes at the very top of
the one system message. Every refusal is `{detail: <code>, ...}` with a named
code from `prompt_modules/errors.py`.

| Method | Path | What |
|---|---|---|
| GET | /prompt-modules | Every row in stored order (modules and SillyTavern marker rows), the imports, and `enabled_count` / `enabled_chars` / `enabled_tokens` for an ordinary send, counted on the prepared text. `enabled_any` also counts regenerate-only entries. No raw file text, no raw entry JSON |
| POST | /prompt-modules/import | Raw body (never multipart), `Content-Length` required, file name in `X-Prompt-Module-Filename` (percent-encoded UTF-8, at most 255 characters, no control characters, else `400 prompt_module_filename_invalid`). `.txt`/`.md` up to 96 KiB, `.json` up to 1 MiB, refused before the body is read. Answers `201 {import_id, modules, report}`; the report is returned here only. Every module starts off |
| PATCH | /prompt-modules/{id} | Any of `enabled`, `sort_order` (0-based target position), `name`. Switching on runs the H4 size check; send the window with it: `chat_id`, `context_budget_tokens`, `max_tokens`, `model_id` (all optional) |
| DELETE | /prompt-modules/{id} | Remove one row; the last module of an import takes the import with it |
| POST | /prompt-modules/enable-preset-defaults | `{import_id, ...window}`: switch on every entry SillyTavern had on, all or none. Answers `{enabled_ids, left_out}` |
| GET | /prompt-modules/settings | `{prompt_layer_enabled: bool, reply_length_words: 150 \| 300 \| null}` |
| PUT | /prompt-modules/settings | Both keys, replaced together; anything else is `400 prompt_settings_invalid` |

---

## Model Metadata Fields

12 fields from `_normalise_model()`: `id`, `name`, `description`, `context_length`, `max_completion_tokens`, `supported_parameters`, `input_modalities`, `output_modalities`, `pricing`, `top_provider`, `created`, `canonical_slug`.

What the frontend reads them for:
- `supported_parameters` - enable or blur the generation-param controls
- `context_length` - the displayed context limit, and the default budget when `context_budget_tokens` is unset
- `input_modalities` / `output_modalities` - the modality indicators, and the gate on the attach button

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
- `is_active` is described under `GET /personas` above and is not repeated here.

---


### The `done` frame's context report

Every completion's `done` event carries three extra numbers, and a client that
does not declare them **loses them silently** - zod's `z.object` strips unknown
keys rather than rejecting them, so a schema that forgets a field turns a live
counter into a permanent zero with nothing red anywhere.

```
{ "type": "done", "chat_id", "model_id",
  "user_message", "assistant_message",
  "deactivated_message_id"?,          // regenerate only
  "notebook_sent":    2,              // notes that fit this turn
  "notebook_total":   9,              // notes that were eligible
  "history_trimmed":  4 }             // older messages dropped to make room
```

`notebook_sent` is the server's count, not a client-side guess: it is correct
precisely in the case the number exists for, when the ceiling trimmed the
notebook. `history_trimmed` rides along because it is the older debt - history
has always been trimmed oldest-first with nothing recording it, and announcing
the notebook alone would teach a reader that dropped context gets announced.


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
                   "completion_price": 0.2,       // USD per 1M completion tokens
                   "context_length": 131072 | null,
                   "endpoints": 3 } ] }           // zero-retention structured-output endpoints
```

`endpoints` counts the rows on OpenRouter's zero-retention endpoint list that
advertise structured outputs for the model; two endpoints of one provider
count as two. The reader's policy keeps `zdr` and `require_parameters`, and
its strict schema needs structured output support, so a backup is expected to
be another row on this list. `endpoints: 1` means no such row exists: when
that machine is down, extraction most likely stops.

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

`model_id` accepts one value that is not a model: `elysium-internal/chat-model`
(`notebook_extract.CHAT_MODEL_BINDING`, mirrored by the frontend's
`CHAT_MODEL_BINDING` and pinned by both suites to
`shared/notebook_chat_model_binding.json`). Stored verbatim and reported
verbatim by the GET; the worker resolves it on EVERY read to the chat's
current model (`chats.model_id`), so changing the chat model moves the
extractor with it. It is a binding, not a one-time pick, and it has to be
chosen beside a price warning: a chat model is usually many times a cheap
extractor's price.

There is no route that runs an extraction and hands the raw reply back. The
extraction happens in the worker; what the client sees of it is the counters
below.


### Reading a chat on request (IP-01)

Two readers, one door. The turn-driven reader reads every chat by itself
when it has twenty unread messages, a chat with no notes included: from its
first message while it holds at most forty unread messages
(`notebook_worker.from_start_edge()`), and from its newest twenty when it holds
more, so the first automatic read of a long history deliberately starts at the
present (3.27). Nothing reads a whole history by itself any more; the worker
used to start a job on a chat's first offer, and decisions 30 a and 31 a
removed that. The older history is read only by a job the reader starts from
the Notebook panel, after the preview. Both go through the same `_handle` -
one claim against the day, one running row, one settle - and a job adds its
accounting: a `notebook_backfill_jobs` row, a lower ceiling, and undo. A job
reads the chat as it stood when it started (3.13: it ends at the message that
was newest then) and, over long messages, keeps the OLDEST lines of each batch
that fit, so it walks forward rather than stepping over the start (3.11).
While it runs the turn-driven reader leaves that chat alone (3.12). The
one-press `POST /notebook/sweep/{chat_id}` is gone (decision 69 a); `scope:
"next"` is that single unit, with a preview and the reserve.

```
GET /notebook/extract/plan/{chat_id}?scope=all
-> { "ready": true, "blocked_by": [],
     "scope": "all", "messages_total": 412, "messages_unread": 412,
     "chunks": 21, "calls_planned": 21,
     "daily_cap": 60,            // the day's ceiling, as GET /worker reports it
     "backfill_cap": 50,         // what a JOB may claim: the day's minus the reserve
     "live_reserve": 10, "calls_spent_today": 3, "fits_today": true,
     "prompt_price": 0.06,       // USD per 1M prompt tokens, or null
     "cost_estimate": 0.014,     // what is sent AND the reply allowance, per
                                 // call (3.17); null when either price is
                                 // unknown, never a fake zero
     "model_id": "vendor/slug", "model_is_chat_bind": false }

POST /notebook/extract/run/{chat_id}
<- { "scope": "next" | "all" | "range", "from_message_id"?: 2, "to_message_id"?: 6 }
-> { "started": true, "job_id": 17, "calls_planned": 21 }
-> { "started": false, "reason": "<one of the ten>" }

POST /notebook/extract/stop/{chat_id}
-> { "stopped": true, "job_id": 17, "calls_spent": 8 }
-> { "stopped": false, "reason": "nothing_running" }

POST /notebook/extract/undo/{job_id}?chat_id=4
-> { "job_id": 17, "removed": 12, "kept_pinned": 3, "kept_edited": 1,
     "kept_accepted": 2, "kept_older": 0 }
```

`blocked_by` carries `no_character`, `no_chat_model`, `no_extract_model`
(the three the run refuses as `not_ready`), `model_not_extractable` (the
model that would write the notes is not on the filtered list, or, for a
model the reader picked, the catalogue says it cannot return a strict
schema; refused BEFORE a call), `model_list_unavailable` (the model is the
chat's own, under the binding, and the filtered list could not be reached
at all, so nothing vetted it; the model is not blamed, and the run refuses
with the same word) and, for `next` and `all` only, `nothing_unread` -
`range` is the re-read mode and does not ask. The six are the whole
vocabulary and the order above is the order the run answers in: the first
match becomes the refusal's `reason`.

**`backfill_cap` and `live_reserve` are on this body and are not in the
plan's section 5.** They are there because `daily_cap` has to keep meaning
one thing on the two routes the panel renders side by side: `daily_cap` is
the day's ceiling, the same number `GET /notebook/worker` reports, and
`backfill_cap` is the lower one a reading job may claim against
(`daily_cap` minus `live_reserve`, never less than one). Without both
numbers the strip cannot reconcile "50 of 60 used" on one card with a
refusal on the other, so `live_reserve` is what its sentence names.
`fits_today` is computed against `backfill_cap` as served, which is the
ceiling the loop claims against - and the job claims against it at the
moment of the claim, not only at the top of the turn, so a live call
landing in between cannot take the reserve. The job stops with
`daily_cap_reached` when it gets there, and the preview says so first.

`scope: "range"` rolls the reading record back over ranges lying entirely
inside the chosen stretch and reads it again; the notes it made before are
renewed by the model naming them in `supersedes`, never deleted by hand.
Pinned notes are never retired in any mode. A read that ends at message N is
shown only the notes distilled from messages up to N, so reading the past
cannot retire the future; the reader's own notes (no source message) stay on
the list in every mode and cannot be retired by the model in any.

A refusal is HTTP 200 with a reason token from `notebook_worker.SWEEP_REFUSALS`
and is NOT in the error catalogue; WorkerPanel's `SWEEP_PROSE` has a sentence
for each of the ten. The three real errors of these routes are
`notebook_job_not_found` (404), `notebook_backfill_running` (409) and
`notebook_range_invalid` (400), in the catalogue like every other code.


### The extraction worker's status

```
GET /notebook/worker            optional ?chat_id=
-> { "stats":          { "done", "failed", "skipped", "abandoned",
                         "skip_reasons": { "<reason>": n } },
     "spend":          today's calls and cost,
     "spend_lifetime": the same shape summed over every day,
     "daily_cap":      NOTEBOOK_DAILY_CALL_CAP,
     "chat_refusal":   null | "model_not_extractable",
     "worker":         { ... } }
```

`stats` counts every chat. `chat_refusal` is about the `?chat_id=` chat only
(null without one): the unfit-model refusal while it still stands there, see
the `GET /notebook/worker` row above.

`spend_lifetime` never gates anything; `spend` alone governs the daily cap.
`abandoned` counts calls whose row was still `error_type: abandoned_in_flight`
- the process died or the vault locked with the call already on the wire, so
the money is gone and the answer never arrived. The row is closed out as
`failed` and counted here rather than left to re-send and re-bill the same
range forever.

The `worker` block carries the breaker state (`state`, `failures`,
`total_failures`, `tripped_chats`, `refused_by_breaker`,
`refused_by_chat_breaker`), the loop's health (`alive`, `died`, `runs`,
`settled_empty`, `unhandled`, `last_error`, `abandoned_preambles`), the queue
(`queued`, `dropped_offers`, `queue_overflows`, `offers_while_down`), the
job lock (`sweeping`, the name it had when the one-press sweep shared it;
and `refused_in_flight`), what is unread (`backlog`), why suggestions were
refused (`dropped`), and the two numbers the breaker is about: `batch_size`,
which shrinks under consecutive failures, and `trigger_threshold`, which does
not (decision 28 a: it is the configured twenty whatever the breakers say).

`chat` is always present and is `null` unless `?chat_id=` was passed AND that
chat has a breaker record of its own. When it is there it holds `state`,
`failures`, `total_failures`, `batch_size` and `trigger_threshold` for that
one conversation. A key that appears and disappears is a key every client has
to guess at, and zod strips what it was not told about.

`backfill` is always present too, and it is REQUIRED on the client's schema
(nullable, never optional): with `?chat_id=` it is that chat's newest reading
job in any state, without it the one in flight, and `null` when there is
none. It rides on this route rather than one of its own because
`main._IDLE_EXEMPT` matches paths exactly and a progress poll on a route of
its own would keep the vault open for as long as a job ran.

```
"backfill": null | {
    "job_id":        17,        // notebook_backfill_jobs.id
    "chat_id":       4,
    "state":         "planned" | "running" | "stopped" | "done" | "failed",
    "scope":         "next" | "all" | "range",
    "calls_planned": 21,        // planned_calls, written once by the run route
    "calls_spent":   8,         // spent_calls, moved by the claim's running row
    "notes_written": 14,        // notes_written, moved as notes land
    "messages_left": 240,       // derived: the chat's unread count, or the range's
    "last_error":    null }     // why it stopped, or null. The whole vocabulary
                                // is notebook_store.JOB_LAST_ERRORS, ten tokens:
                                // daily_cap_reached, chat_paused, chat_stopped,
                                // proxy_gate, api_key_not_set, refused, failed,
                                // settled, vault_locked, interrupted. A panel
                                // that has no sentence for one prints none of
                                // it (WorkerPanel.echoable), so a new token is
                                // safe on the wire and silent on screen
```

`skip_reasons` is keyed by reason string. `plan_invalidated` and
`range_cleared` are the two keys for a reply that arrives after the work it
answered was invalidated, and they are NOT edit versus clear.
`commit_extraction` asks one question when its `running` row has gone: does
`to_message_id` still name a row in `messages`? It still does after an edit
that landed on the range end - the message survives, only its wording changed
- and that case alone is `plan_invalidated`. It does not after a deleted
message (a delete takes every message after it too), a cleared chat, an
aborted send being cleaned up, or an edit whose swept tail was where the range
ended: all four are `range_cleared`. Regenerating reaches neither key - it
deletes nothing, so the extraction is written normally and a note can be taken
from a reply somebody discarded. Deleting the CHAT reaches neither either: the
chat row is gone, the foreign key on the insert fires, and it lands in
`worker.unhandled` as `write_IntegrityError`. Both named skips discard the
proposals and both still record the cost, so a skip under either name is a
paid call.

`worker.dropped` counts refused PROPOSALS by reason, which is a different
question from a refused call. One integer cannot tell "a quote was invented" -
the defence working - from "a Turkish quote failed a byte comparison" - the
defence eating a true fact. `ungrounded` is the one key that does not keep
that promise: it counts a quote that appears nowhere, a quote assembled from
the end of one message and the start of the next, and a quote sitting in a
stretch of transcript that names no speaker. The first is the defence working;
the other two say the span is not a quotation of any one thing anybody said,
and a pile of them reads exactly like fabrication on this screen.

The reading job routes answer `{started: false, reason}` for ten refusals,
none of which is an error, and the ten are `notebook_worker.SWEEP_REFUSALS`
(the name is the old sweep route's, whose five reasons the job kept):
`nothing_unread` (everything here has been covered), `already_running` (a job
is already reading; pressing three times is one request), `already_in_flight`
(the background loop is mid-call on this same chat, and letting a job plan
the identical range would bill it twice), `chat_paused` (this chat's breaker
is open and will retry by itself), `chat_stopped` (it will not retry until the
breaker is reset by hand), `not_ready` (a chat, its model or a note model is
not chosen yet - the preview's `blocked_by` says which), `daily_cap_reached`
(the job's own ceiling is spent), `model_not_extractable` (the model that
would write the notes cannot return a strict schema, refused before a call),
`model_list_unavailable` (the chat's own model under the binding, with the
provider's list out of reach, so nothing vetted it; not the model's fault)
and, from the stop route, `nothing_running`. `chat_paused` and `chat_stopped`
are separate because only one of them asks the reader to do something. Every
one of the ten has a sentence in WorkerPanel's `SWEEP_PROSE`.

Every note carries **`evidence_role`** on `GET /notebook/{chat_id}`: `"user"`,
`"assistant"`, or `null` for a note somebody typed themselves. It records
whose sentence the note's quote was lifted from, read off the `role:` prefix
of the message the quote was found in. A note the extractor writes always
carries one of the two roles: a quote whose speaker cannot be read is refused
rather than stored with a blank. `null` therefore means a note somebody typed
themselves - or one written before that refusal landed.

It is the one signal no verifier can supply. The grounding check asks whether
a note is supported by its quote; nothing can ask whether the QUOTE was
invented, and when the chat model quotes its own reply the check passes by
construction. The panel therefore MARKS an `"assistant"` note and does not
withhold it - at the measured rate of fabrication a queue of these would be
almost entirely correct notes, and a queue nobody reads is worse than a label
somebody can see.

Every note also carries two exact marks on `GET /notebook/{chat_id}` (and on
the rows `PATCH /notebook/entries/{id}` and `POST .../accept` return), both
`null` or a UTC `YYYY-MM-DD HH:MM:SS` stamp. **`edited_at`** is set when a
PATCH changes the note's `text`: any change counts, letter case and
punctuation included, except whitespace alone (both sides are trimmed and
runs of whitespace collapsed before comparing; the new text is still stored).
Invisible characters (zero-width space, joiners, byte-order mark) do not
count either. The duplicate check keeps its own looser comparison. A pin, a
kind, an importance, a reorder or an accept never sets it, and sending the
same words back does not either. **`hand_accepted_at`** is set when the
accept route moves a proposal to `accepted`, or when `POST .../keep` is
pressed on a note the app accepted on its own; pressing either again does
not restamp, and editing a proposal's text does not set it. Notes from before these
fields existed carry `null` in both: `updated_at` moving past `created_at`
was the old guess, and it is not copied in.

`GET /notebook/extract/models` passes `enforce_proxy_gate()` first, like every
outbound path. Extraction calls are claimed against `NOTEBOOK_DAILY_CALL_CAP`
*before* the request rather than counted after it; see
`notebook_daily_cap_reached` in the error table for what a spent cap looks like
to the client.


## Character Rules

- Only the character matching `chat.character_id` is fetched and injected.
- `raw_json` is never returned by any endpoint and never included in payload.
- `avatar_path`, when a character has one, is a relative path the frontend builds the avatar URL from.
- Avatar image data is **never** sent to OpenRouter.
- No `image_url` content from CHARACTER data in any message - user-attached
  images are the sole sanctioned source of image parts (see Part H).

---

## Empty Chat Heuristic

A chat with `message_count=1` whose single message has `role='assistant'` is
unstarted: that message is the `first_mes` greeting, not something anybody
typed. The backend adds no flag for it; the frontend derives it from those two
fields.

---

## Selected Persona ID

`GET /settings` carries `selected_persona_id: int | null`.
`POST /personas/{id}/select` sets it, and deleting the selected persona clears
it back to null.

---

## Frontend implementation notes

- TanStack Query for every API call, with query keys like `["settings"]`,
  `["chats"]`, `["personas"]`. Invalidate the relevant key after a mutation, for
  example `["chats", chatId, "messages"]` after complete/regenerate/delete.
- Every error code in the tables above gets its own sentence. The sentences live
  in `frontend/src/lib/errors/errorMessages.ts`; the machine record of which
  codes exist lives in `shared/error_catalogue.json`.
- Nothing secret goes in browser storage.
- The `context_budget_tokens` slider maps to the model's `context_length`
  (512 to `context_length`).
- The regenerate control belongs on the latest message only, and only when it is
  `role=assistant`.
- Message delete takes the target and everything after it; clear takes the whole
  chat. Refresh the message list after either. `deleted_count` is the response
  field for both.
- The character PATCH form sends changed fields only.
