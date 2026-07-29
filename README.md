<p align="center">
  <h1 align="center">Elysium</h1>
  <p align="center">
    <strong>Privacy-first, localhost-only AI character chat client powered by OpenRouter</strong>
  </p>
  <p align="center">
    <img src="https://img.shields.io/badge/python-3.13-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python">
    <img src="https://img.shields.io/badge/license-MIT-blue?style=flat-square" alt="License">
    <img src="https://img.shields.io/badge/version-1.1.0-brightgreen?style=flat-square" alt="Version">
    <img src="https://img.shields.io/badge/privacy-ZDR_enforced-brightgreen?style=flat-square" alt="Privacy">
    <img src="https://img.shields.io/badge/at--rest-SQLCipher_vault-brightgreen?style=flat-square" alt="Encryption">
    <img src="https://img.shields.io/badge/streaming-SSE-brightgreen?style=flat-square" alt="Streaming">
    <img src="https://img.shields.io/badge/frontend_tests-1163_passed-success?style=flat-square" alt="Frontend Tests">
    <img src="https://img.shields.io/badge/frontend-React_19-61DAFB?style=flat-square&logo=react&logoColor=white" alt="React">
  </p>
  <p align="center">
    <img src="assets/elysium_banner.png" alt="Elysium" width="820">
  </p>
</p>

---

Elysium is a privacy-first AI character chat client that routes **all model traffic through a local FastAPI backend**. The frontend never contacts OpenRouter directly. Your entire chat database - messages, characters, personas, attached images, and your API key - is **passphrase-encrypted at rest (SQLCipher)**, strict ZDR privacy routing is enforced on every request, and raw upstream error bodies are never exposed to the client. It runs as a dev server pair or as a **packaged Windows desktop app** (`Elysium.exe`).

## What's new in v1.1.0

- **Local voice** - Elysium can speak replies out loud with a text-to-speech model that runs on **your own GPU**. Nothing is sent anywhere: no cloud voice API, no audio upload. Drop a model folder in, and its own settings appear in Settings › Voice automatically
- **The app installs the voice engine** - one button. Elysium builds the engine's isolated Python environment itself, shows the real download size before you commit, and can be cancelled or removed at any time. You never open a terminal or edit a config file
- **Says plainly when it cannot speak** - a voice model is always inspectable, and every reason it will not run right now (engine not set up, no NVIDIA GPU, card busy, files missing) is listed at once, in the same words the rest of the app uses
- **Delivery direction** - with voice on, replies carry invisible performance cues (`[whisper]`, `[laughing]`) that shape HOW a line is spoken. They never appear in the chat, and they are stripped from what you read
- **Voice clones from your own clips** - add a short reference recording. Engines that need the words spoken in it ask you to type them; no shipped engine has speech recognition, so Elysium does not offer to listen and guess
- **Reading rules** - teach Elysium how to say a name it gets wrong (`Aoife` → `EE-fa`). Applies to spoken replies only; the chat text never changes
- **Delivery dials** - reading speed, tag density, a standing tone, how narration is voiced, and the pause between sentences. Each one means the same thing whether a reply is arriving live or being replayed by the speak button
- **Hear any reply** - a speak button on assistant messages. Two messages can never talk over each other, and locking the vault stops playback and deletes the generated audio

- **Message editing** - Edit any of your messages inline; the reply after it is regenerated and the following turns are rewound (attachments preserved, edits are conflict-guarded)
- **Image drag-and-drop** - Drag PNG/JPEG/WebP files onto the chat for a full-panel drop overlay; rejected files and the 4-image cap now surface a toast instead of vanishing silently
- **Persona name in the prompt** - The persona's name now reaches the model as a `[User Persona: {name}]` block (previously only the description was sent)
- **Smooth streaming** - A pacing layer types replies at a natural, model-tracking speed (grapheme-safe, respects reduced-motion) instead of dumping bursts
- **Jump-to-latest** - Scroll up while a reply streams and you are never yanked back; a pulsing down-arrow returns you to the bottom on click
- **Message contrast presets** - Soft / Default / High readability presets (all AA-verified), independent of the chat wallpaper, persisted across restarts
- **Composer typography** - The composer font follows the message-size setting without overflowing, and generation settings survive a vault re-lock
- **Image metadata stripped** - Every attached image is re-encoded on upload so EXIF/GPS and other embedded metadata are dropped before it is stored or sent - your camera and location never ride along with the picture
- **Hardened by a full-system audit** - An 8-dimension adversarial code audit swept the whole codebase before release (no critical/high/medium issues); the low-severity findings it surfaced are all fixed and regression-tested

## Fixed since v1.1.0

- **Spoken replies no longer stop short** - a reply could go quiet a few words before the end, with no error and nothing to explain it. The last sentence was being dropped while it was still being made
- **Narration no longer flattens the speech next to it** - with narration voice set to *Narrator*, dialogue that shared a sentence with a `*narrated action*` was performed in the narrator's flat tone too. Narration is told; the speech after it now returns to the character's voice, including your standing tone if you have set one
- **Delivery holds up over a long reply** - the tag density dial is a budget for the model's own cues, and narration was quietly spending it. On a long roleplay reply the delivery used to go plainer the further it ran
- **Faster first word** - the voice engine was shuffling its model in and out of the graphics card before nearly every sentence, to make room it already had, and then parking the audio decoder before every one as well. Both were reserving space for the longest reply the settings allow rather than for the sentence actually being spoken. Measured end to end, the fixed cost per sentence went from 2.3 seconds to under 0.6, and speech now starts inside three seconds on the very first reply of a session instead of the third
- **Speech starts sooner on a long reply** - a reply is spoken sentence by sentence, and the first piece is cut short on purpose so it can start early. Nothing was keeping the NEXT piece small, so a short opener could be followed by one three times its length - and playback then sat waiting for it rather than starting, which threw away the head start it had just been given. The chunks are now sized against what the reply has already banked
- **The first sentence no longer waits for the second** - a reply is synthesised a piece ahead of what you are hearing, so playback never runs dry. That read-ahead was being paid for before anything was handed over: the opening piece finished, and then sat in memory until the piece behind it was finished too. It is the one delay you experience in full, because nothing is playing yet to cover it. Measured end to end on a real reply, from pressing speak to the first sound: 10.5 seconds, now 5.6
- **The narration voice setting now means one thing** - it was stored twice, once on this device and once in your vault, and the two were only written together when you touched the dropdown. A choice made before that, or a save that failed once, left replies performed one way as they arrived and another way when the speak button repeated them. Your current choice is moved into the vault on first launch; nothing to do

## Features

- **Passphrase Vault** - The whole database (characters, chats, messages, personas, settings, attached image bytes, and your OpenRouter API key + proxy URL) is a SQLCipher-encrypted file. The app starts locked; a passphrase unlocks it (scrypt-derived raw key, held only in RAM). Change the passphrase from the Secrets tab; closing the desktop app locks the vault. The decorative chat wallpaper is the one exception - it lives in local browser storage, not the vault
- **Character System** - Create, import (Character Card V2 JSON), and manage characters with full field support (system prompt, description, personality, scenario, example dialogue, post-history instruction)
- **Persona System** - Create and switch AI personas that are injected as a system block into every completion request
- **Streaming Responses** - Token-by-token SSE streaming with a live cursor and a Stop control. However a reply ends early - you press Stop, the provider drops out mid-sentence, anything - the text you have already read is kept; only a send that produced nothing rolls back and restores your draft
- **Response Variants** - Regenerate keeps every take: swipe between variants in a carousel and pick which one the conversation continues from
- **OpenRouter Integration** - Browse and select from the full OpenRouter model catalogue; generation parameters (temperature, top\_p, top\_k, max\_tokens, seed, repetition\_penalty) are validated, model-filtered, and forwarded
- **Context Budget** - App-level `context_budget_tokens` controls history trimming; oldest messages are dropped to fit the budget - never forwarded to OpenRouter as a provider field
- **Message Lifecycle** - Send (streaming + optimistic UI), regenerate with variant history, delete (target + all following), clear chat, and rename chats inline from the sidebar
- **Stop Sequences** - Up to 4 stop sequences (with `\n` support) managed as chips in Generation Settings; always forwarded to the provider, mirroring the backend rule
- **Image Attachments** - Attach up to 4 images (PNG/JPEG/WebP) per message to vision-capable models; drag-in/paste/pick, thumbnail strip, full-size lightbox. The attach UI is gated by the model's image modality, images are downscaled and content-addressed server-side, and the backend builds the provider payload (the frontend never constructs image URLs)
- **Reading & Ambience Settings** - In-app settings for message font size and line height, `*narration*` styling, an optional chat wallpaper with contrast/tint controls and adaptive text, and a living WebGL mist backdrop (with a static fallback)
- **Sidebar Navigation** - Persona strip with a switcher, client-side character search, and New Chat / New Character docks
- **Active Context Preview + live context meter** - Local-only collapsible card in the Models tab showing what the next request will include (model, persona, character, message count, generation params, context budget) plus a live "≈ used / capacity tokens" gauge on the selected model; approximate estimates, never the exact provider payload
- **Error Toast System** - Centralized safe error notifications over the chat canvas; auto-dismiss after 4.5 s, max 5 visible, extras queued
- **Privacy by Design** - ZDR, data\_collection=deny, allow\_fallbacks=false are hardcoded in the backend and cannot be overridden
- **Sealed Secrets** - API key and proxy URL live inside the encrypted vault (unreadable while locked); a one-time migration moves them out of the OS keyring and deletes the old entries - never sent to the frontend
- **Strict CORS + Host allowlist** - Backend accepts browser requests from `http://127.0.0.1:5173` only and rejects foreign `Host` headers (DNS-rebinding shield)
- **Desktop App** - PyInstaller build (one-folder for development, a single ~33 MB exe for release) with a native window (pywebview + WebView2); the exe serves the built frontend same-origin on a random loopback port and locks the vault when the window closes

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Frontend (Browser)                    │
│          React 19 · Vite · TypeScript · TanStack         │
│                  http://127.0.0.1:5173                   │
│                                                          │
│   Settings ──┐                                           │
│   Characters │                                           │
│   Personas   ├──────── REST API (/api/v1/*) ────────────►│
│   Models     │                                           │
│   Chat     ──┘                                           │
└─────────────────────────────┬───────────────────────────┘
                              │ http only, 127.0.0.1
                              ▼
┌─────────────────────────────────────────────────────────┐
│              Backend (FastAPI + Uvicorn)                  │
│                  http://127.0.0.1:8787                   │
│                                                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────┐ │
│  │ settings │  │characters│  │  chats/  │  │personas │ │
│  │  vault   │  │ uploads  │  │ complete │  │ models  │ │
│  └──────────┘  └──────────┘  └──────────┘  └─────────┘ │
│                                                          │
│  SQLCipher vault (DB + images + secrets, WAL) · httpx     │
│  (trust_env=False) · 423 vault gate while locked         │
│                                                          │
│  PROVIDER_POLICY (hardcoded, immutable):                 │
│    zdr=true · data_collection=deny · allow_fallbacks=false│
└──────────────────────────────┬──────────────────────────┘
                               │ HTTPS · Authorization only
                               ▼
                    ┌─────────────────────┐
                    │  OpenRouter API      │
                    │  openrouter.ai       │
                    └─────────────────────┘
```

In the packaged desktop app the same process serves both the API and the built
frontend on one random loopback port (same-origin, no CORS in play), shown in
a native WebView2 window.

## Privacy Contract

Elysium enforces strict privacy routing on every OpenRouter request:

| Field | Value | Overridable? |
|-------|-------|--------------|
| `provider.zdr` | `true` | ❌ Never |
| `provider.data_collection` | `"deny"` | ❌ Never |
| `provider.allow_fallbacks` | `false` | ❌ Never |

Additional guarantees:

- `context_budget_tokens` is **never** forwarded to OpenRouter - app-level history trimming only
- `raw_json`, `avatar_path`, `tools`, `tool_choice`, `response_format` - **never** sent. `image_url` parts are built server-side **only** for images the user explicitly attached (vision models); the frontend never constructs them
- Streaming uses the same hardcoded provider policy; deltas are relayed to the client but raw upstream error frames are mapped to safe codes, never forwarded
- API key is sealed inside the encrypted vault (unreachable while locked); never returned by any endpoint, never logged
- Raw upstream OpenRouter error bodies are never forwarded to the client - safe mapped messages only
- Browser storage holds only UI preferences - never messages, personas, characters, API keys, or proxy URLs
- Frontend never emits an `Authorization` header - all provider auth happens backend-side
- Logs carry ids, counts, and status codes only - never message content, passphrases, or key material

**At rest:** the database file is genuine SQLCipher ciphertext - without the
passphrase it does not open as SQLite at all. Attached images are stored as
encrypted blobs INSIDE that database (v0.6), and the API key + proxy URL are
sealed in it too; served images carry `Cache-Control: no-store` so the
browser keeps no plaintext copies. The scrypt salt and verifier (`salt.bin`,
`verifier.bin`) sit beside the DB by design (they are not secrets, but never
publish them). One remaining note lives under Known Limitations: the
one-time migration from an older plaintext database leaves a plaintext
backup you should delete once satisfied.

> **Note on `proxy_required`:** If set to `false` (default), the app connects to OpenRouter directly. Your IP may be visible to OpenRouter. Set `proxy_required=true` to enforce proxy-only traffic.

## Quick Start

**Fastest way (Windows):** the repository root ships a ready-to-run `Elysium.exe` - just double-click it. First run: create your vault passphrase (there is no recovery - it IS the key), add your OpenRouter API key in Settings, done. Data lives encrypted in `%LOCALAPPDATA%\Elysium`; WebView2 is preinstalled on Windows 10/11.

Everything below is for running from source.

### Prerequisites

- **Python 3.13** (3.12+ compatible)
- **Node.js 20+** with npm
- **OS keyring** - Windows Credential Manager, macOS Keychain, or libsecret

### Backend

```powershell
cd backend
py -3.13 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8787
```

Or use the included quick-start script:
```powershell
start_backend.bat
```

### Frontend

```powershell
cd frontend
npm install
npm run dev          # starts at http://127.0.0.1:5173
```

> **Never use `0.0.0.0`** - both services must bind to `127.0.0.1` only.

### Getting Started

1. **Start the backend** at `127.0.0.1:8787`
2. **Start the frontend** at `127.0.0.1:5173`
3. **Open the app** at exactly `http://127.0.0.1:5173`
   - Do **not** use `http://localhost:5173` - intentionally rejected by CORS
4. **Create your vault passphrase** on first run (or unlock with it later).
   There is no recovery: the passphrase IS the key to your data
5. **Set your OpenRouter API key** in Settings - sealed in your encrypted vault, never sent to frontend
6. **Import or create a character**, select a model, and start chatting

### Desktop build (Windows)

```powershell
cd frontend
npm run build                      # builds the SPA into frontend/dist

cd ..\backend
.venv\Scripts\activate
pyinstaller elysium.spec           # one FOLDER: backend + SPA + SQLCipher
dist\Elysium\Elysium.exe           # run it
```

For the single-file build, which is what the `Elysium.exe` in this repo
actually is:

```powershell
pyinstaller elysium_onefile.spec   # one FILE, ~33 MB
dist\Elysium.exe
```

Both specs bundle the same things (the voice worker scripts and the engine
requirements files among them); `backend/tests/test_release_sync.py` keeps
them from drifting apart.

- Needs the **WebView2 runtime** (preinstalled on Windows 10/11; the app
  shows an install link if it is missing)
- The packaged app stores its data in `%LOCALAPPDATA%\Elysium` (override
  with the `ELYSIUM_DATA_DIR` environment variable); the dev servers keep
  using `backend/`
- Closing the window ends the process and locks the vault
- `ELYSIUM_SELFTEST=1 Elysium.exe` runs a headless boot check (exit 0 = OK)
- Startup problems are logged to `%LOCALAPPDATA%\Elysium\elysium.log`

## Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.13 · FastAPI · Uvicorn |
| Database | SQLCipher (encrypted SQLite, WAL) via `sqlcipher3-wheels` · scrypt KDF |
| HTTP client | httpx with SOCKS proxy support (`trust_env=False`) |
| Secrets | Sealed in the encrypted vault DB (one-time migration out of the OS keyring) |
| Frontend | React 19 · Vite · TypeScript (strict) · TanStack Query v5 · Zustand · Zod v4 |
| Frontend UI | Base UI primitives · Lucide icons · Tailwind CSS v4 · motion |
| Desktop | pywebview (WebView2) · PyInstaller one-folder build |

## Repository Layout

```
elysium/
├── backend/
│   ├── routers/              API route handlers
│   │   ├── settings.py       API key, proxy, health
│   │   ├── characters.py     Character CRUD + import
│   │   ├── chats.py          Chat + message management + variants
│   │   ├── completions.py    POST /complete + /regenerate (+ /stream)
│   │   ├── personas.py       Persona CRUD + select
│   │   ├── uploads.py        Image attachment staging + serving
│   │   ├── models_router.py  OpenRouter model catalogue
│   │   └── vault.py          Vault status/init/unlock/lock/change-passphrase
│   ├── tts/                  Voice subsystem (host half - never imports torch)
│   │   ├── adapters/         Per-engine identification, settings, VRAM estimates
│   │   ├── worker/           The engine-side scripts, run in their own interpreter
│   │   ├── requirements/     Per-engine dependency pins for the installer
│   │   ├── host.py           Worker lifecycle, load/unload, the audio cache
│   │   ├── registry.py       Model discovery and identity
│   │   ├── readiness.py      Every reason a model will not run, at once
│   │   ├── speech_queue.py   Sentence-at-a-time synthesis with pacing
│   │   └── stream_hook.py    The speaker a streaming reply talks to
│   ├── speech_prep.py        Turning a written reply into something speakable
│   ├── voice_tags.py         Delivery tags: the prompt, the stripper, the dials
│   ├── messages_common.py    The message row -> API shape door
│   ├── secrets_service.py    Vault-sealed secrets (API key, proxy URL)
│   ├── legacy_migration.py   One-time unlock-time migrations (keyring, uploads)
│   ├── attachments_service.py  Image validation, downscale, storage, refcounts
│   ├── config.py             App constants + PROVIDER_POLICY + data dir
│   ├── crypto.py             scrypt KDF, verifier, rekey (vault identity)
│   ├── database.py           Keyed SQLCipher connections + migration
│   ├── vault_state.py        In-RAM vault key holder
│   ├── keyring_service.py    OS keyring abstraction
│   ├── network_client.py     Shared httpx client
│   ├── openrouter.py         OpenRouter API client + model cache + SSE stream
│   ├── proxy_health.py       Proxy health check with TTL cache
│   ├── main.py               FastAPI app + vault gate + CORS + router wiring
│   ├── run_app.py            Desktop launcher (native window + uvicorn)
│   ├── elysium.spec          PyInstaller build spec (one folder)
│   ├── elysium_onefile.spec  PyInstaller build spec (single exe - ships)
│   ├── version_info.txt      Windows version resource for the exe
│   ├── elysium.ico           App icon
│   ├── tests/                pytest suite (TestClient + mock provider)
│   └── verify/               Legacy regression scripts (reference only)
├── frontend/
│   └── src/
│       ├── app/              App entry, providers, stale-selection reconciliation
│       ├── components/
│       │   ├── backdrop/     WebGL mist canvases
│       │   ├── brand/        ElysiumMark + Wordmark
│       │   ├── chat/         ChatCanvas, Composer, MessageList, MessageBubble
│       │   ├── characters/   Character list, create/import/edit dialogs
│       │   ├── chats/        Chat create dialog
│       │   ├── errors/       ErrorToastStack + ErrorBoundary
│       │   ├── models/       Model panel
│       │   ├── persona/      Persona panel area
│       │   ├── settings/     ApiKeySection, ProxySection, app settings dialog
│       │   ├── sidebar/      Sidebar layout, search, persona strip
│       │   └── vault/        Lock/create/unlock screens (VaultGate)
│       ├── lib/
│       │   ├── api/          REST + SSE + upload client functions
│       │   ├── appearance/   Chat wallpaper pipeline
│       │   ├── characters/   Character helpers
│       │   ├── chat/         Chat action helpers + message parser
│       │   ├── errors/       Error parser, mapper, store
│       │   ├── generation/   Generation params helpers + payload builders
│       │   ├── models/       Model metadata + modality helpers
│       │   ├── personas/     Persona active/id helpers
│       │   ├── preview/      Active context preview builder
│       │   ├── query/        TanStack Query hooks (all resources)
│       │   ├── schemas/      Zod schemas + inferred types
│       │   └── store/        Zustand UI store + wallpaper IndexedDB
│       └── test/
│           ├── components/   Focused test suites
│           ├── helpers/      SSE-aware fetch stub for streaming tests
│           ├── static-safety.test.ts   Static privacy checks
│           └── fe0-contract.test.ts    API contract shape tests
├── docs/                     Internal planning docs (not committed)
├── start_backend.bat         Windows quick-start script
├── .gitignore
└── README.md
```

## API Endpoints

All endpoints are under `/api/v1` (except `GET /healthz`, which lives at the
root). While the vault is locked, every data route answers `423 Locked`; only
`/vault/*` and `/healthz` respond.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/healthz` | Liveness probe (root level) |
| `GET` | `/vault/status` | Vault initialized/unlocked state |
| `POST` | `/vault/init` | Create the vault (first run; migrates a plaintext DB) |
| `POST` | `/vault/unlock` | Unlock with the passphrase |
| `POST` | `/vault/lock` | Lock (drop the in-RAM key) |
| `POST` | `/vault/change-passphrase` | Re-key the database |
| `GET` | `/settings` | Current config state (no secrets) |
| `POST` | `/settings/api-key` | Store + validate API key |
| `DELETE` | `/settings/api-key` | Remove API key |
| `POST` | `/settings/proxy` | Store proxy config |
| `POST` | `/settings/proxy/required` | Arm/disarm the kill-switch without rewriting the URL |
| `POST` | `/settings/proxy/alias` | Rename the configured proxy (the URL is write-only) |
| `POST` | `/settings/stop-sequences` | Up to 4 stop sequences |
| `DELETE` | `/settings/proxy` | Remove proxy config |
| `GET` | `/settings/proxy/health` | Proxy health status |
| `GET` | `/characters` | List all characters |
| `POST` | `/characters` | Create character |
| `POST` | `/characters/import` | Import JSON character card |
| `GET` | `/characters/{id}` | Get single character |
| `PATCH` | `/characters/{id}` | Edit character (partial update) |
| `DELETE` | `/characters/{id}` | Delete character + cascade chats/messages |
| `GET` | `/chats` | List all chats |
| `POST` | `/chats` | Create chat session |
| `GET` | `/chats/{id}` | Get single chat |
| `PATCH` | `/chats/{id}` | Rename chat (title only) |
| `GET` | `/chats/{id}/messages` | List messages |
| `POST` | `/chats/{id}/complete` | Send message, get completion (non-streaming) |
| `POST` | `/chats/{id}/complete/stream` | Send message, stream completion (SSE) |
| `DELETE` | `/chats/{id}` | Delete chat + messages |
| `POST` | `/chats/{id}/clear` | Clear messages, keep chat |
| `DELETE` | `/chats/{id}/messages/{msg_id}` | Delete target + all following messages |
| `POST` | `/chats/{id}/messages/{msg_id}/regenerate` | Regenerate as a new variant |
| `POST` | `/chats/{id}/messages/{msg_id}/regenerate/stream` | Regenerate as a new variant (SSE) |
| `POST` | `/chats/{id}/messages/{msg_id}/edit` | Edit a user message and rewrite the tail |
| `POST` | `/chats/{id}/messages/{msg_id}/edit/stream` | Same, streamed (SSE) |
| `POST` | `/chats/{id}/messages/{msg_id}/activate` | Make a variant the active reply |
| `GET` | `/personas` | List personas (includes `is_active`) |
| `POST` | `/personas` | Create persona |
| `PATCH` | `/personas/{id}` | Edit persona |
| `DELETE` | `/personas/{id}` | Delete persona |
| `POST` | `/personas/{id}/select` | Set active persona |
| `GET` | `/models/openrouter` | List OpenRouter models (cached) |
| `POST` | `/uploads/images` | Stage an image attachment (multipart) |
| `GET` | `/uploads/images/{id}` | Serve a stored image to the frontend |
| `DELETE` | `/uploads/images/{id}` | Drop a staged attachment |

Voice (all local; nothing here contacts anything but this machine, except
the one-time engine install, which downloads packages and sends nothing):

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/tts/models` | Discovered voice models, each with its readiness verdict |
| `POST` | `/tts/rescan` | Re-walk the model folders |
| `GET\|POST` | `/tts/active` | The selected model, and its live state |
| `GET\|POST\|DELETE` | `/tts/models/{uid}/settings` | Per-model settings (stored beside the model, not in the vault) |
| `GET` | `/tts/models/{uid}/schema` | What knobs this engine has, and what it can do |
| `GET` | `/tts/models/{uid}/readiness` | Every reason it will not run right now, at once |
| `POST` | `/tts/load` \| `/tts/unload` | Take the card, give it back |
| `GET` | `/tts/state` | What is loaded, and what went wrong. Polling this is what notices a dead worker |
| `POST` | `/tts/speak` | Synthesise a whole message into one wav |
| `POST` | `/tts/speak_stream` | The same, sentence by sentence over SSE - what the speak button uses |
| `POST` | `/tts/speak_live` | Start speaking the reply that is streaming right now |
| `GET` | `/tts/audio/{audio_id}` | Fetch a generated wav (no-store; cleared on lock) |
| `GET\|POST` | `/tts/voice-mode` | The global voice toggle |
| `GET\|POST` | `/tts/tag-prefs` | The delivery dials: density, tone, speed, narration, pause |
| `GET\|POST` | `/tts/pronunciations` | Reading rules (the whole table per write) |
| `GET\|POST\|DELETE` | `/tts/voices/{voice_id}` | Reference clips to clone from |
| `POST` | `/tts/voices/{voice_id}/transcript` | Type the words spoken in a clip |
| `GET\|POST\|DELETE` | `/tts/runtimes/...` | Engine install: plan, start, watch, cancel, remove |

## Frontend Logic Foundation

The frontend is built on a layer of pure logic helpers - no browser storage of secrets, no direct OpenRouter calls. The UI is now implemented on top of these slices.

| Slice | Module | Description |
|-------|--------|-------------|
| FE-0 | `lib/api/`, `lib/schemas/`, `lib/query/` | API clients, Zod schemas, TanStack Query hooks |
| FE-1A | `lib/errors/` | Safe error parser, code→message mapper, Zustand error store |
| FE-1B | `components/errors/ErrorToastStack` | Global toast UI: glass pill, auto-dismiss, queue, accessibility |
| FE-2 | `lib/query/completions` | Optimistic send, thinking bubble, error rollback, draft restore |
| FE-3A | `lib/personas/` | Active persona helpers, safe persona ID extraction |
| FE-4A | `lib/generation/` | Generation param filtering, model compatibility, payload builders |
| FE-5A | `lib/chat/` | Chat action helpers: delete/clear/regenerate eligibility, cache transforms |
| FE-6A | `lib/characters/` | Character lookup, safe start-chat builder, cascade warning |
| FE-7A | `lib/models/` | Model metadata, modality detection, context budget bounds |
| FE-8A | `lib/preview/` | Active context preview builder (local-only, approximate, privacy-safe) |

## Verification

### Backend (pytest)

```powershell
cd backend
.venv\Scripts\python -m pytest tests -q   # TestClient regression suite (1221 tests)
```

The `tests/` suite covers the completion/regenerate flows (including the
provider-failure and abort paths), the vault lifecycle (migration, rekey,
recovery, the 423 gate), and attachments against an in-memory keyring and a
faked provider. `tests/mock_provider.py` is a stdlib-only OpenRouter stand-in
(with real word-by-word SSE framing) for end-to-end smoke testing with zero
network egress:

```powershell
.venv\Scripts\python tests\mock_provider.py            # terminal 1 (port 9797)
set OPENROUTER_BASE_URL=http://127.0.0.1:9797/api/v1   # terminal 2
uvicorn main:app --host 127.0.0.1 --port 8787
```

The legacy `verify_*.py` scripts remain for reference.

### Frontend (1163 tests)

```powershell
cd frontend
npm test                          # full suite - 1163 tests, 84 files
npm test -- src/test/static-safety.test.ts   # static privacy checks
npm run typecheck                 # tsc strict - app + test configs
```

## Known Limitations (v1.1.0)

- **Plaintext migration backup** - upgrading an older unencrypted database keeps a plaintext `app.db.plain.bak-<timestamp>` copy next to the vault; delete it once you have verified the migration
- **No idle auto-lock** - lock manually with the sidebar button, or by closing the app
- **UI preferences are not encrypted** - font size, wallpaper image, and last-open ids persist in the desktop app's local WebView profile (no chat content)
- **No local/offline models** - OpenRouter only
- **No PDF/file upload** - images are supported (vision models); documents are not
- **No Compatibility Mode** - strict ZDR privacy routing always enforced
- **No ZDR toggle** - privacy settings cannot be relaxed in the UI
- **No multi-branch chat** - linear conversation with per-message variants; the latest reply can be regenerated and continued from any variant, while older variant groups are view-only (browsable but the conversation always continues from their active take); delete-forward or edit-a-message to rewind
- **Single instance** - running two copies of the desktop app against the same data folder is unsupported
- **Voice needs a one-time engine setup** - the speech engines are multi-GB and are not bundled in the exe; Settings › Voice installs one on request. An NVIDIA GPU is required to run them
- **One voice model at a time** - the card holds one; loading another unloads the first
- **Generated audio is session-only** - locking the vault or closing the app deletes the cached speech, and anything older than half an hour is cleared as the next reply is spoken
- **No speech recognition** - no shipped engine can listen to a reference clip and write out its words; type them in yourself. The control only appears for an engine that declares the ability

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Cannot reach the server | Ensure backend is running at `127.0.0.1:8787` |
| CORS error / blank page | Open at `http://127.0.0.1:5173`, not `http://localhost:5173` |
| Wrong passphrase | There is no recovery or reset - the passphrase is the key. Try again |
| Forgot passphrase | Data in the vault is unrecoverable by design; delete the data folder to start fresh |
| Desktop app shows nothing | Install the WebView2 runtime (the app links it), then check `%LOCALAPPDATA%\Elysium\elysium.log` |
| API key not set | Configure OpenRouter API key in Settings |
| Authentication failed | API key is invalid or expired - update in Settings |
| Proxy required but not configured | Set proxy URL in Settings, or disable `proxy_required` |
| Model unavailable / ZDR error | Model doesn't support zero-data-retention - try a different model |
| Frontend tests fail | Run `npm install` then `npm test` from the `frontend/` directory |
