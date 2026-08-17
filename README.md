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
    <img src="https://img.shields.io/badge/frontend_tests-1308_passed-success?style=flat-square" alt="Frontend Tests">
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

## Since v1.1.0

Everything added and fixed since the last release is in
**[CHANGELOG.md](CHANGELOG.md)**. It becomes the v1.2.0 notes when the version
moves; keeping it out of here is what stops this file growing a new section per
release and never losing one.

## Features

- **Passphrase Vault** - The whole database (characters, chats, messages, personas, settings, attached image bytes, and your OpenRouter API key + proxy URL) is a SQLCipher-encrypted file. The app starts locked; a passphrase unlocks it (scrypt-derived raw key, held only in RAM). Change the passphrase from the Secrets tab; closing the desktop app locks the vault. The decorative chat wallpaper is the one exception - it lives in local browser storage, not the vault
- **Character System** - Create, import (Character Card V2 JSON), and manage characters with full field support (system prompt, description, personality, scenario, example dialogue, post-history instruction)
- **Persona System** - Create and switch AI personas that are injected as a system block into every completion request
- **Streaming Responses** - Token-by-token SSE streaming with a live cursor and a Stop control. However a reply ends early - you press Stop, the provider drops out mid-sentence, anything - the text you have already read is kept; only a send that produced nothing rolls back and restores your draft
- **Response Variants** - Regenerate keeps every take: swipe between variants in a carousel and pick which one the conversation continues from
- **OpenRouter Integration** - Browse and select from the full OpenRouter model catalogue; generation parameters (temperature, top\_p, top\_k, max\_tokens, seed, repetition\_penalty) are validated, model-filtered, and forwarded
- **Context Budget** - App-level `context_budget_tokens` controls history trimming; oldest messages are dropped to fit the budget - never forwarded to OpenRouter as a provider field
- **Message Lifecycle** - Send (streaming + optimistic UI), regenerate with variant history, delete (target + all following), clear chat, and rename chats inline from the sidebar
- **Copy Out** - Every message carries a copy button that puts the original text on the clipboard, asterisks and all, not the styled version on screen; text selection is on window-wide, so dragging across a reply and pressing Ctrl+C works too, with timestamps and variant counters kept out of the selection. What happens to a copy once it leaves is Windows' business - [SECURITY.md](SECURITY.md) covers Clipboard History and its sync
- **Stop Sequences** - Up to 4 stop sequences (with `\n` support) managed as chips in Generation Settings; always forwarded to the provider, mirroring the backend rule
- **Image Attachments** - Attach up to 4 images (PNG/JPEG/WebP) per message to vision-capable models; drag-in/paste/pick, thumbnail strip, full-size lightbox. The attach UI is gated by the model's image modality, images are downscaled and content-addressed server-side, and the backend builds the provider payload (the frontend never constructs image URLs)
- **Reading & Ambience Settings** - In-app settings for message font size and line height, `*narration*` styling, an optional chat wallpaper with framing, zoom, contrast/tint controls and adaptive text, message-bubble solidity, and a living WebGL mist backdrop (with a static fallback)
- **Sidebar Navigation** - Persona strip with a switcher, client-side character search, and New Chat / New Character docks
- **Active Context Preview + live context meter** - Local-only collapsible card in the Models tab showing what the next request will include (model, persona, character, message count, generation params, context budget) plus a live "≈ used / capacity tokens" gauge on the selected model; approximate estimates, never the exact provider payload
- **Error Toast System** - Centralized safe error notifications over the chat canvas; auto-dismiss after 4.5 s, max 5 visible, extras queued
- **Privacy by Design** - the provider policy is hardcoded backend-side and cannot be overridden from anywhere; see [Privacy Contract](#privacy-contract) for the exact fields and the full list of what is and is not sent
- **Sealed Secrets** - API key and proxy URL live inside the encrypted vault (unreadable while locked); a one-time migration moves them out of the OS keyring and deletes the old entries - never sent to the frontend
- **Strict CORS + Host allowlist** - Backend accepts browser requests from `http://127.0.0.1:5173` only and rejects foreign `Host` headers (DNS-rebinding shield)
- **Locks itself when idle** - after 5 minutes of doing nothing the vault closes: the key leaves memory, the voice model is unloaded and the GPU memory comes back. Change the delay or turn it off in Settings > Secrets. A reply that is still streaming counts as activity for as long as it runs
- **Takes its own folder back** - at launch Elysium checks whether other accounts on this PC can reach its data folder and removes that access, naming what it removed in the log. Your database is encrypted, but `salt.bin` and `verifier.bin` beside it are what an offline passphrase attack needs. This is the one change that persists after the app closes; [SECURITY.md](SECURITY.md) says how to undo it
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
- Raw upstream OpenRouter error bodies are never forwarded to the client - safe mapped codes only, on the streaming path as well as the plain one. Streaming otherwise uses the same hardcoded provider policy, relaying deltas untouched
- API key is sealed inside the encrypted vault (unreachable while locked); never returned by any endpoint, never logged
- Browser storage holds only UI preferences - never messages, personas, characters, API keys, or proxy URLs
- Frontend never emits an `Authorization` header - all provider auth happens backend-side
- Logs carry ids, counts, and status codes only - never message content, passphrases, or key material
- Every response carries a Content-Security-Policy plus `X-Content-Type-Options: nosniff` and `X-Frame-Options: DENY`, including the 423/403/400 refusals that never reach a route. The policy exists for containment rather than for XSS: the same local origin serves the whole API, so `default-src 'self'` means nothing running there has anywhere to send data. The packaged build additionally sends `Cross-Origin-Resource-Policy: same-origin`, which stops a remote page that guesses the port from probing your attachments through an `<img>` tag
- A stored image is served only if its recorded type is one the app itself produced (PNG/JPEG/WebP); anything else is refused rather than handed to the browser
- The key is derived with scrypt at OWASP's current floor (N=2^17, r=8, p=1). The parameters are recorded per vault, so an older vault keeps opening under the ones it was made with and is re-derived to the current ones the next time it is unlocked - the one moment the passphrase exists in memory and a re-key is possible at all
- A passphrase must be at least 12 characters and must not be a repeated fragment, a keyboard walk, or one of the phrases any guessing attempt starts with. There is no rate limit behind this vault - somebody with the folder guesses offline - so length and variety are what matter, and composition rules (one capital, one digit, one symbol) are deliberately NOT imposed
- Locking overwrites the key in memory rather than dropping the reference. Optionally the vault locks itself after a chosen idle period; a request still in flight counts as activity, so a streamed reply is never cut short
- The desktop window is given a secret at launch and every API request carries it. Loopback is not a permission boundary: without this, any program running as your user could read the whole conversation over HTTP while Elysium is open, which is exactly when the vault is unlocked. The secret travels in the URL fragment (never sent to a server, never logged), is read once and stripped from the address, and is kept in memory rather than in browser storage. It is also withheld from every subprocess Elysium starts, since the voice engine and the installer both run code this project did not write. Two routes a browser element must load directly - a stored picture and a spoken reply - accept a browser's own same-origin signal instead, which still refuses a program with `curl`
- Every outbound request passes one check that refuses any host but the configured provider, before a connection is opened. With a proxy configured it reads the destination rather than the first hop, so the proxy cannot be used to reach elsewhere
- The app window refuses to navigate off its own origin. It has no address bar, so a page loaded there would wear Elysium's frame with nothing visible to contradict it

**At rest:** the database file is genuine SQLCipher ciphertext - without the
passphrase it does not open as SQLite at all. Images are stored as encrypted
blobs INSIDE that database (v0.6) - both the ones you attach and the ones a
model generates, through the same validate-and-re-encode pipeline - and the API
key + proxy URL are sealed in it too; served images carry `Cache-Control: no-store` so the
browser keeps no plaintext copies. The scrypt salt and verifier (`salt.bin`,
`verifier.bin`) sit beside the DB by design (they are not secrets, but never
publish them). The one-time migration from an older plaintext
database leaves a plaintext backup on purpose - Settings > Secrets lists it
and deletes it (overwriting first) once you are satisfied the move worked.

> **Note on `proxy_required`:** If set to `false` (default), the app connects to OpenRouter directly. Your IP may be visible to OpenRouter. Set `proxy_required=true` and every request to the provider goes through your proxy or does not go at all - completions, `/models`, and the voice installer's multi-gigabyte downloads alike. The switch fails CLOSED: if the setting cannot be read, the download refuses rather than proceeding without the proxy, and the installer's child processes are handed an environment with the ambient `HTTP_PROXY`/`NO_PROXY`/index variables stripped, so a proxy exported in your shell can neither capture the traffic nor be used to skip yours.
>
> What it does NOT cover: the embedded browser window. WebView2 loads this app's own files from the local server and is not routed through the proxy. It makes no request to any other host - crash reporting is blocked at the filesystem level and every API response carries `no-store` - but "proxy-only" is a statement about the app's outbound requests, not about the browser control.

## Security

A separate page answers the questions this one does not: what exactly is
encrypted and what is not, which of Elysium's protections change a **persistent
Windows setting** and how to undo them, and what stays on your disk after you
delete the app.

**Read it: [SECURITY.md](SECURITY.md).**

The short version: everything you write lives in one encrypted file, the
passphrase is never stored, the app talks to one host on the internet, and it
writes nothing to the Windows registry. The page also lists what is *not*
protected, which is the half worth reading.

## Quick Start

**Fastest way (Windows):** the repository root ships a ready-to-run `Elysium.exe` - just double-click it. First run: create your vault passphrase (there is no recovery - it IS the key), add your OpenRouter API key in Settings, done. Data lives encrypted in `%LOCALAPPDATA%\Elysium`; WebView2 is preinstalled on Windows 10/11.

Everything below is for running from source.

### Prerequisites

- **Python 3.13** (3.12+ compatible)
- **Node.js 20+** with npm
- **OS keyring** - not needed for a new install. Secrets live in the encrypted
  vault; the keyring is read once, and only to migrate an older setup out of it

### Backend

```powershell
cd backend
py -3.13 -m venv .venv
.venv\Scripts\activate
pip install --require-hashes -r requirements.lock.txt
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
| Desktop | pywebview (WebView2) · PyInstaller (one-folder to develop, one-file to ship) |

## Repository Layout

```
backend/          FastAPI app: routers/, tts/, vault + hardening modules, tests/
frontend/         React SPA: src/components, src/lib, src/test
SECURITY.md       what is protected, what is not, and what persists
docs/             frontend_contract.md, design notes
Elysium.exe       packaged desktop build (see Desktop build below)
```

A per-file tree used to live here and was wrong within weeks: eight backend
modules and a whole frontend voice/ directory had appeared without it
noticing. The directories above are stable; `git ls-files` is accurate.

## API Endpoints

All endpoints are under `/api/v1` (except `GET /healthz`, which lives at the
root). While the vault is locked, every data route answers `423 Locked`; only
the `/vault/*` routes and `/healthz` pass.

The full list, with every error code each route can return, lives in
**[docs/frontend_contract.md](docs/frontend_contract.md)** and is kept honest
by a test: `test_every_route_the_app_serves_appears_in_the_contract` fails the
build if the app grows a route the contract does not mention.

A hand-maintained copy used to sit here and had drifted by eight routes,
which is the argument for one list rather than two.

## Verification

### Backend (pytest)

```powershell
cd backend
.venv\Scripts\python -m pytest tests -q   # TestClient regression suite (2087 collected, 2081 pass + 6 skip)
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

The legacy `verify_*.py` scripts were deleted on 17 August 2026. Twelve of them
had stopped being checks: two could not be imported at all, one had been
declared dead in writing months earlier, and seven asserted things about the
app that are no longer true. Everything they still covered is in the test suite,
and `docs/VERIFY_SCRIPTS_RETIRED.md` says where each one went. Three tools stay,
because each does something no test can: `verify_hygiene.py` (the source gate
the commit hook runs), `verify_image_output.py` (a live request with your own
key) and `verify_tts_latency.py` (measures real hardware).

### Frontend (1308 tests)

```powershell
cd frontend
npm test                          # full suite - 1308 tests, 102 files
npm test -- src/test/static-safety.test.ts   # static privacy checks
npm run typecheck                 # tsc strict - app + test configs
```

## Known Limitations (v1.1.0)

- **Plaintext migration backup** - upgrading an older unencrypted database keeps a plaintext `app.db.plain.bak-<timestamp>` copy next to the vault, deliberately: if the move had verified wrong it is the only copy left. Settings > Secrets shows it on every visit and removes it on request
- **A second copy after an interrupted move** - if the one-time migration is cut off midway it can leave a complete ENCRYPTED copy beside the vault. Settings > Secrets shows it, and offers to delete it only when it opens with your current passphrase; a copy it cannot open may belong to an older one, so the app refuses to remove it
- **The vault locks itself after 5 minutes idle** - Settings > Secrets changes the delay or turns it off. Idle means nothing in flight and nothing finished recently, so a reply that is still streaming holds it open however long it takes. Locking also unloads the voice model and gives the GPU memory back
- **UI preferences are not encrypted** - type size, bubble solidity, the wallpaper image and how it is framed, last-open ids, the sampling parameters and the model you last picked persist in the desktop app's local WebView profile (no chat content)
- **No local/offline models** - OpenRouter only
- **No PDF/file upload** - images are supported (vision models); documents are not
- **Pictures in replies are not verified against a live model yet** - the whole path is built and covered by tests, but Elysium's privacy routing is strict enough that it is not certain any provider will accept the request. `backend/verify/verify_image_output.py` answers that with your own key in one run: whether a provider will answer at all under the policy, and whether it returns the picture inline (usable) or as a link (refused). Until you run it, treat the feature as untested against the real thing
- **A model that returns a link cannot be used for pictures** - the reply is kept, the picture is not, and a note says so. Fetching it would mean a second place your data goes
- **Pictures in replies are shown, not remembered by the model** - the model does not see its own drawing again on the next turn. That is deliberate; making it possible is a separate feature with a real token cost
- **The vault does not shrink** - deleting an image-heavy chat frees the space inside the database file but does not give it back to the disk
- **Privacy routing cannot be relaxed** - strict ZDR is always enforced; there is no compatibility mode and no toggle in the UI
- **No multi-branch chat** - linear conversation with per-message variants; the latest reply can be regenerated and continued from any variant, while older variant groups are view-only (browsable but the conversation always continues from their active take); delete-forward or edit-a-message to rewind
- **Single instance** - running two copies of the desktop app against the same data folder is unsupported
- **Voice needs a one-time engine setup** - the speech engines are multi-GB and are not bundled in the exe; Settings › Voice installs one on request. An NVIDIA GPU is required to run them
- **One voice model at a time** - the card holds one; loading another unloads the first
- **Voice is production quality in English only** - the shipped engines speak Turkish with a heavy foreign accent rather than not at all, which is the worse failure of the two: nothing errors, the reply is simply read out wrong. Cloning from a native Turkish reference clip helps and does not fix it
- **Generated audio is session-only** - locking the vault or closing the app deletes the cached speech, the next launch clears whatever a crash or a kill left behind, and anything older than half an hour is cleared as the next reply is spoken
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
